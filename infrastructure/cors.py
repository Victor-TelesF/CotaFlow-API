"""CORS explícito na pilha ASGI, validado antes do lifespan, nunca no import."""

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from infrastructure.config import CorsSettings


class ConfiguredCORSMiddleware(CORSMiddleware):
    """Permite o frontend configurado ler respostas e enviar JWT, sem cookies.

    A pilha é construída pelo Starlette ao iniciar o ASGI. Assim a configuração
    inválida impede startup sem carregar .env durante o import de main.
    Deve envolver a proteção de requisições para tornar 413/429 legíveis.
    """

    def __init__(self, app: ASGIApp, settings: CorsSettings | None = None):
        configuration = settings if settings is not None else CorsSettings()
        super().__init__(
            app,
            allow_origins=configuration.CORS_ALLOW_ORIGINS,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            expose_headers=["Retry-After", "X-Request-ID"],
            max_age=600,
        )
