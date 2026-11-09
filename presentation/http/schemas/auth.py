"""Contratos HTTP de autenticação; respostas nunca incluem senha ou hash."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class LoginRequest(BaseModel):
    """Email canônico e senha preservada exatamente, inclusive espaços."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def valid_password_encoding(cls, value: SecretStr) -> SecretStr:
        """Rejeita surrogates isolados antes de banco/Argon2, sem mudar a senha."""
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Password must be valid UTF-8 text") from None
        return value

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        """O produto trata o endereço inteiro sem distinção entre maiúsculas."""
        return value.strip().lower()


class RegisterRequest(LoginRequest):
    """Cadastro exige 8–128 caracteres; não corta nem transforma a senha."""

    password: SecretStr = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    """Identidade pública, sem os campos internos de segurança."""

    id: UUID
    email: str


class TokenResponse(BaseModel):
    """Token Bearer de acesso; resposta não deve ser armazenada em cache HTTP."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
