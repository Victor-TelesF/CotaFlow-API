"""Composição da API, autenticação e ciclo de vida dos recursos.

``create_app`` conecta as rotas e prepara a fábrica de unidades de trabalho,
segurança, cliente HTTP e cache de cotações no startup. Este
módulo não aplica migrations: o schema deve ser preparado pelo Alembic.
"""

from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from functools import partial

import httpx2
from fastapi import FastAPI

from infrastructure.auth_limits import AuthenticationLimits
from infrastructure.config import (
    AuthSettings,
    CorsSettings,
    MarketSettings,
    PortfolioSettings,
    SecuritySettings,
    Settings,
)
from infrastructure.cors import ConfiguredCORSMiddleware
from infrastructure.database.session import Database
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from infrastructure.diagnostics import FailureDiagnostics
from infrastructure.market.brapi import BrapiQuotes
from infrastructure.market.cache import CachedQuotes
from infrastructure.market.limits import ValuationLimits
from infrastructure.security import Argon2PasswordHasher, JwtTokenService
from infrastructure.transaction_limits import TransactionLimits
from presentation.http.errors import install_error_handlers
from presentation.http.protection import RequestProtectionMiddleware
from presentation.http.routers.assets import router as assets_router
from presentation.http.routers.auth import router as auth_router
from presentation.http.routers.health import router as health_router
from presentation.http.routers.portfolio import router as portfolio_router
from presentation.http.schemas.errors import AUTH_ERRORS, PROTECTED_ERRORS


def create_app(
    settings: Settings | None = None,
    security_settings: SecuritySettings | None = None,
    market_settings: MarketSettings | None = None,
    auth_settings: AuthSettings | None = None,
    portfolio_settings: PortfolioSettings | None = None,
    cors_settings: CorsSettings | None = None,
) -> FastAPI:
    """Constrói uma aplicação sem abrir conexões ou carregar o arquivo .env.

    Uma configuração explícita permite testes isolados. Quando omitida, será
    carregada no lifespan. Cada inicialização possui seu próprio engine/pool;
    ``app.state.uow_factory()`` fornece uma unidade de trabalho nova por uso.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Disponibiliza recursos no startup e fecha HTTP/pool no shutdown.

        As sessões são encerradas pelos contextos das unidades de trabalho;
        o pool encerra suas conexões devolvidas ao terminar a aplicação.
        """
        security = (
            security_settings if security_settings is not None else SecuritySettings()
        )
        tokens = JwtTokenService(security)
        passwords = Argon2PasswordHasher()
        market = market_settings if market_settings is not None else MarketSettings()
        auth = auth_settings if auth_settings is not None else AuthSettings()
        portfolio = (
            portfolio_settings
            if portfolio_settings is not None
            else PortfolioSettings()
        )
        with ExitStack() as resources:
            database = Database(settings if settings is not None else Settings())
            resources.callback(database.dispose)
            token = market.BRAPI_TOKEN.get_secret_value().strip()
            client = resources.enter_context(
                httpx2.Client(
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                    timeout=httpx2.Timeout(2.0),
                    follow_redirects=False,
                    trust_env=False,
                )
            )
            app.state.uow_factory = partial(
                SqlAlchemyUnitOfWork, database.session_factory
            )
            app.state.tokens = tokens
            app.state.passwords = passwords
            app.state.auth_limits = AuthenticationLimits(auth)
            app.state.transaction_limits = TransactionLimits(
                portfolio.PORTFOLIO_TRANSACTIONS_PER_MINUTE
            )
            app.state.quotes = CachedQuotes(
                BrapiQuotes(client),
                external_per_minute=market.MARKET_EXTERNAL_CALLS_PER_MINUTE,
            )
            app.state.valuation_limits = ValuationLimits(
                market.MARKET_VALUATIONS_PER_MINUTE
            )
            app.state.max_valuation_tickers = market.MARKET_MAX_TICKERS
            try:
                yield
            finally:
                del app.state.uow_factory
                del app.state.tokens
                del app.state.passwords
                del app.state.auth_limits
                del app.state.transaction_limits
                del app.state.quotes
                del app.state.valuation_limits
                del app.state.max_valuation_tickers

    app = FastAPI(title="CotaFlow API", lifespan=lifespan)
    app.state.failure_diagnostics = FailureDiagnostics()
    app.add_middleware(RequestProtectionMiddleware)
    app.add_middleware(ConfiguredCORSMiddleware, settings=cors_settings)
    app.include_router(health_router)
    app.include_router(auth_router, responses=AUTH_ERRORS)
    app.include_router(assets_router, responses=PROTECTED_ERRORS)
    app.include_router(portfolio_router, responses=PROTECTED_ERRORS)
    install_error_handlers(app)
    return app


app = create_app()
