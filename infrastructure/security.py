"""Adaptadores Argon2 e JWT; nenhum segredo é carregado durante o import."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from application.exceptions import InvalidToken
from infrastructure.config import SecuritySettings


class Argon2PasswordHasher:
    """Usa Argon2 recomendado pelo pwdlib, com sal novo a cada hash.

    Email ausente também verifica um hash fictício. Isso reduz uma diferença
    grosseira de custo, mas não promete tempo constante para todo o login.
    """

    def __init__(self):
        self._hasher = PasswordHash.recommended()
        self._dummy_hash = self._hasher.hash("synthetic-absent-user-password")

    def hash(self, password: str) -> str:
        """Produz somente a representação Argon2 persistível."""
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str | None) -> bool:
        """Hash desconhecido/corrompido não autentica nem expõe seu conteúdo."""
        try:
            stored_hash = self._dummy_hash if password_hash is None else password_hash
            matches = self._hasher.verify(password, stored_hash)
        except (UnknownHashError, ValueError):
            return False
        return password_hash is not None and matches


class JwtTokenService:
    """JWT HS256 com sub UUID e exp inteiro obrigatório, validado pelo PyJWT.

    Não inclui email, senha ou hash no payload. O relógio UTC é consultado na
    emissão; PyJWT confere expiração e claims temporais na verificação.
    """

    def __init__(self, settings: SecuritySettings):
        self._key = settings.JWT_SECRET_KEY.get_secret_value()
        self._lifetime = timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)

    def issue(self, user_id: UUID) -> str:
        """Emite token com expiração absoluta; não implementa refresh token."""
        now = datetime.now(UTC)
        return jwt.encode(
            {"sub": str(user_id), "iat": now, "exp": now + self._lifetime},
            self._key,
            algorithm="HS256",
        )

    def verify(self, token: str) -> UUID:
        """Rejeita assinatura, algoritmo, claims e assunto inválidos como InvalidToken."""
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                options={"require": ["sub", "exp"]},
            )
            # PyJWT aceita timestamps conversíveis em int; o contrato exige inteiro.
            if type(payload["exp"]) is not int:
                raise InvalidToken
            return UUID(payload["sub"])
        except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
            raise InvalidToken from None
