"""Tradução segura e uniforme de erros; nunca serializa entradas recebidas."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from application.exceptions import (
    AssetAlreadyRegistered,
    AuthenticationLimited,
    CatalogAssetNotFound,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidToken,
    QuotesUnavailable,
    TransactionRateLimited,
    ValuationRateLimited,
    ValuationTooLarge,
)
from domain.exceptions import InsufficientBalanceError, InvalidValueError
from presentation.http.protection import authentication_limited_response


def install_error_handlers(app: FastAPI) -> None:
    """Instala somente as traduções necessárias às rotas desta etapa."""

    @app.exception_handler(TransactionRateLimited)
    async def transaction_limited(request: Request, error: TransactionRateLimited):
        return JSONResponse(
            status_code=429,
            content={
                "code": "transaction_rate_limited",
                "message": "Transaction budget exhausted",
            },
            headers={
                "Retry-After": str(error.retry_after),
                "Cache-Control": "no-store",
            },
        )

    @app.exception_handler(AuthenticationLimited)
    async def authentication_limited(request: Request, error: AuthenticationLimited):
        return authentication_limited_response(error)

    @app.exception_handler(ValuationRateLimited)
    async def valuation_limited(request: Request, error: ValuationRateLimited):
        return JSONResponse(
            status_code=429,
            content={
                "code": "valuation_rate_limited",
                "message": "Valuation budget exhausted",
            },
            headers={
                "Retry-After": str(error.retry_after),
                "Cache-Control": "no-store",
            },
        )

    @app.exception_handler(ValuationTooLarge)
    async def valuation_too_large(request: Request, error: ValuationTooLarge):
        return JSONResponse(
            status_code=422,
            content={
                "code": "valuation_too_large",
                "message": "Too many open positions to value",
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(QuotesUnavailable)
    async def quotes_failure(request: Request, error: QuotesUnavailable):
        request_id = request.app.state.failure_diagnostics.record(
            category="quotes",
            method=request.method,
            route=getattr(request.scope.get("route"), "path", None),
            error=error,
            reason=error.reason,
            ticker_count=len(error.tickers),
        )
        headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
        if error.retry_after is not None:
            headers["Retry-After"] = str(error.retry_after)
        return JSONResponse(
            status_code=503,
            content={
                "code": "quotes_unavailable",
                "message": "Unable to complete portfolio valuation",
                "tickers": list(error.tickers),
                "reason": error.reason,
            },
            headers=headers,
        )

    @app.exception_handler(AssetAlreadyRegistered)
    async def duplicate_asset(request: Request, error: AssetAlreadyRegistered):
        return JSONResponse(
            status_code=409,
            content={
                "code": "asset_already_registered",
                "message": "Asset already registered",
            },
        )

    @app.exception_handler(CatalogAssetNotFound)
    async def missing_asset(request: Request, error: CatalogAssetNotFound):
        return JSONResponse(
            status_code=404,
            content={"code": "asset_not_found", "message": "Catalog asset not found"},
        )

    @app.exception_handler(InsufficientBalanceError)
    async def insufficient_balance(request: Request, error: InsufficientBalanceError):
        return JSONResponse(
            status_code=409,
            content={
                "code": "insufficient_balance",
                "message": "Incompatible historical balance",
            },
        )

    @app.exception_handler(InvalidValueError)
    async def invalid_value(request: Request, error: InvalidValueError):
        return JSONResponse(
            status_code=422,
            content={"code": "invalid_value", "message": "Invalid domain value"},
        )

    @app.exception_handler(EmailAlreadyRegistered)
    async def duplicate_email(request: Request, error: EmailAlreadyRegistered):
        return JSONResponse(
            status_code=409,
            content={
                "code": "email_already_registered",
                "message": "Email already registered",
            },
        )

    @app.exception_handler(InvalidCredentials)
    @app.exception_handler(InvalidToken)
    async def unauthorized(request: Request, error: InvalidCredentials | InvalidToken):
        code = (
            "invalid_credentials"
            if isinstance(error, InvalidCredentials)
            else "invalid_token"
        )
        return JSONResponse(
            status_code=401,
            content={"code": code, "message": "Authentication failed"},
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # Pydantic pode incluir senha/email em input, ctx e msg. Expor só o caminho/tipo.
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "Invalid request data",
                "details": [
                    {"location": list(item["loc"]), "type": item["type"]}
                    for item in error.errors()
                ],
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def persistence_failure(request: Request, error: SQLAlchemyError):
        # hide_parameters não redige o DETAIL do servidor; nunca retornar str(error).
        request_id = request.app.state.failure_diagnostics.record(
            category="database",
            method=request.method,
            route=getattr(request.scope.get("route"), "path", None),
            error=error,
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": "persistence_unavailable",
                "message": "Persistence unavailable",
            },
            headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
        )
