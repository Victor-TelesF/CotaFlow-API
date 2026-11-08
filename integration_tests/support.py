"""Shared test setup; application credentials are never a fallback."""

import os
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def disposable_database_url():
    """Exige adesão explícita, PostgreSQL local e nome cotaflow_test_*.

    Não lê Settings, .env ou URL de produção como alternativa. Erros de
    configuração omitem a URL recebida para não revelar sua senha.
    """
    value = os.environ.get("TEST_DATABASE_URL")
    if not value or os.environ.get("TEST_DATABASE_DISPOSABLE") != "1":
        raise pytest.UsageError(
            "PostgreSQL tests require TEST_DATABASE_URL and TEST_DATABASE_DISPOSABLE=1"
        )
    try:
        url = make_url(value)
    except (ArgumentError, ValueError):
        raise pytest.UsageError("Invalid TEST_DATABASE_URL (value withheld)") from None
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (url.database or "").startswith("cotaflow_test_")
    ):
        raise pytest.UsageError(
            "Tests require a local PostgreSQL database named cotaflow_test_*"
        )
    return url


def migrate(engine, revision="head"):
    """Executa revisões no schema da conexão fornecida pelo teste.

    O engine da fixture já restringe o search_path a um schema exclusivo.
    ``base`` reverte a migration; ``head`` aplica a revisão mais recente.
    """
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if revision == "base":
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)
