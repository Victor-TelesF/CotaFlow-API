"""Composição da API, autenticação e ciclo de vida dos recursos.

``create_app`` conecta o healthcheck e prepara uma fábrica de unidades de
trabalho e adaptadores de segurança no startup. Ainda não há rotas de carteira. Este
módulo não aplica migrations: o schema deve ser preparado pelo Alembic.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from infrastructure.config import SecuritySettings, Settings
from infrastructure.database.session import Database
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from infrastructure.security import Argon2PasswordHasher, JwtTokenService
from presentation.http.errors import install_error_handlers
from presentation.http.routers.auth import router as auth_router
from presentation.http.routers.health import router as health_router


def create_app(
    settings: Settings | None = None,
    security_settings: SecuritySettings | None = None,
) -> FastAPI:
    """Constrói uma aplicação sem abrir conexões ou carregar o arquivo .env.

    Uma configuração explícita permite testes isolados. Quando omitida, será
    carregada no lifespan. Cada inicialização possui seu próprio engine/pool;
    ``app.state.uow_factory()`` fornece uma unidade de trabalho nova por uso.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Disponibiliza a fábrica no startup e descarta o pool no shutdown.

        As sessões são encerradas pelos contextos das unidades de trabalho;
        o pool encerra suas conexões devolvidas ao terminar a aplicação.
        """
        security = (
            security_settings if security_settings is not None else SecuritySettings()
        )
        tokens = JwtTokenService(security)
        passwords = Argon2PasswordHasher()
        database = Database(settings if settings is not None else Settings())
        try:
            app.state.uow_factory = partial(
                SqlAlchemyUnitOfWork, database.session_factory
            )
            app.state.tokens = tokens
            app.state.passwords = passwords
            yield
        finally:
            del app.state.uow_factory
            del app.state.tokens
            del app.state.passwords
            database.dispose()

    app = FastAPI(title="CotaFlow API", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(auth_router)
    install_error_handlers(app)
    return app


app = create_app()
