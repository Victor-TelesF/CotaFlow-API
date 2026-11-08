"""Tradução segura e uniforme de erros; nunca serializa entradas recebidas."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from application.exceptions import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidToken,
)


def install_error_handlers(app: FastAPI) -> None:
    """Instala somente as traduções necessárias às rotas desta etapa."""

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
        return JSONResponse(
            status_code=503,
            content={
                "code": "persistence_unavailable",
                "message": "Persistence unavailable",
            },
        )
