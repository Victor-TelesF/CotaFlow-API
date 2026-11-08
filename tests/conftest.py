"""Configuração sintética de segurança, sem dotenv nem chaves pessoais."""

from datetime import UTC, datetime

import jwt.api_jwt
import pytest

import infrastructure.security
from infrastructure.config import SecuritySettings


@pytest.fixture
def security_settings():
    return SecuritySettings(
        _env_file=None,
        JWT_SECRET_KEY="synthetic-test-key-with-at-least-64-characters-for-algorithm-tests-only",
        JWT_ACCESS_TOKEN_MINUTES=30,
    )


@pytest.fixture
def auth_clock(monkeypatch):
    """Controla emissão e verificação nativas, sem esperas ou rede."""
    clock = [datetime(2030, 1, 1, tzinfo=UTC)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock[0].timestamp(), tz=tz)

    monkeypatch.setattr(infrastructure.security, "datetime", FrozenDatetime)
    monkeypatch.setattr(jwt.api_jwt, "datetime", FrozenDatetime)
    return clock
