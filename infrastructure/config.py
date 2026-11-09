"""Configuração validada na inicialização, sem recursos globais no import.

Valores explícitos têm precedência sobre variáveis de ambiente e o ``.env``.
A API cria ``Settings`` no lifespan; o Alembic cria sua própria instância.
Testes passam ``_env_file=None`` e dados sintéticos para isolar a configuração.
"""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Dados de acesso ao PostgreSQL síncrono com Psycopg.

    ``DB_PASSWORD`` é obrigatório e mascarado por ``SecretStr`` nas
    representações usuais; isso não é criptografia. Inputs são omitidos das
    mensagens textuais de validação, mas quem usar ``ValidationError.errors``
    deve pedir ``include_input=False`` para não expor entradas recebidas.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    DB_DRIVER: Literal["postgresql+psycopg"] = "postgresql+psycopg"
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = Field(default=55432, ge=1, le=65535)
    DB_NAME: str = "app"
    DB_USER: str = "app"
    DB_PASSWORD: SecretStr = Field(min_length=1)

    @property
    def database_url(self) -> URL:
        """Monta a URL do driver sem interpolar ou escapar a senha manualmente.

        O objeto preserva caracteres especiais e entrega a senha original ao
        driver. Não registrar versões da URL com a senha desmascarada.
        """
        return URL.create(
            drivername=self.DB_DRIVER,
            username=self.DB_USER,
            password=self.DB_PASSWORD.get_secret_value(),
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )


class SecuritySettings(BaseSettings):
    """Segredo obrigatório da API; migrations não precisam dessa configuração.

    HS256 é fixo no adaptador, sem aceitar o algoritmo recebido no token.
    Use uma chave aleatória de pelo menos 32 caracteres fora do repositório.
    Não há chave padrão nem geração silenciosa que invalide tokens no restart.
    """

    model_config = Settings.model_config

    JWT_SECRET_KEY: SecretStr = Field(min_length=32)
    JWT_ACCESS_TOKEN_MINUTES: int = Field(default=30, ge=1, le=1440)

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_signing_key(cls, secret: SecretStr) -> SecretStr:
        candidate = secret.get_secret_value().strip()
        if (
            len(candidate) < 32
            or candidate == "replace-with-a-random-secret-of-at-least-32-characters"
        ):
            raise ValueError(
                "Set a private random JWT signing key of at least 32 characters"
            )
        # Whitespace is only inspected for validation, never removed from the key.
        return secret


class CorsSettings(BaseSettings):
    """Origens exatas do frontend, em lista JSON; vazia desabilita acesso CORS.

    Bearer é enviado explicitamente em Authorization, sem cookies cross-site.
    Não aceitar wildcard, regex, credenciais, paths ou origens opacas.
    """

    model_config = Settings.model_config

    CORS_ALLOW_ORIGINS: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("CORS_ALLOW_ORIGINS")
    @classmethod
    def validate_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                len(origin) > 2048
                or any(character.isspace() for character in origin)
                or "*" in origin
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.port == 0
                or origin != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError("Set exact HTTP(S) origins without paths or wildcards")
        return list(dict.fromkeys(origins))


class MigrationSettings(BaseSettings):
    """Prazo de espera pelo lock de migrations, sem exigir configuração JWT."""

    model_config = Settings.model_config

    MIGRATION_LOCK_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=300)


class AuthSettings(BaseSettings):
    """Quotas em memória por processo; capacidades não são SLA de hospedagem."""

    model_config = Settings.model_config

    AUTH_REGISTER_PER_10_MINUTES: int = Field(default=5, ge=1, le=1000)
    AUTH_LOGIN_PER_MINUTE: int = Field(default=20, ge=1, le=1000)
    AUTH_LOGIN_PAIR_PER_5_MINUTES: int = Field(default=5, ge=1, le=1000)
    AUTH_MAX_CONCURRENT: int = Field(default=2, ge=1, le=8)


class PortfolioSettings(BaseSettings):
    """Admissão por usuário/processo; sem limite de tamanho do histórico."""

    model_config = Settings.model_config

    PORTFOLIO_TRANSACTIONS_PER_MINUTE: int = Field(default=30, ge=1, le=1000)


class MarketSettings(BaseSettings):
    """Token opcional: símbolos públicos funcionam sem chave; falhas viram 503.

    Cache: TTL 1.800 s, 512 entradas, busca 10 s e I/O até 2 s. Limites
    configuráveis são por processo e reiniciam no startup, sem teto mensal.
    """

    model_config = Settings.model_config
    BRAPI_TOKEN: SecretStr = SecretStr("")
    MARKET_MAX_TICKERS: int = Field(default=20, ge=1, le=512)
    MARKET_VALUATIONS_PER_MINUTE: int = Field(default=10, ge=1, le=1000)
    MARKET_EXTERNAL_CALLS_PER_MINUTE: int = Field(default=30, ge=1, le=10000)
