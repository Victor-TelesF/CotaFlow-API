"""Configuração validada na inicialização, sem recursos globais no import.

Valores explícitos têm precedência sobre variáveis de ambiente e o ``.env``.
A API cria ``Settings`` no lifespan; o Alembic cria sua própria instância.
Testes passam ``_env_file=None`` e dados sintéticos para isolar a configuração.
"""

from typing import Literal

from pydantic import Field, SecretStr
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
