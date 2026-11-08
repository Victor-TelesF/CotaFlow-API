"""Explicit opt-in PostgreSQL tests; never fall back to application settings."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from integration_tests.support import disposable_database_url, migrate


@pytest.fixture
def postgres_engine():
    """Cria um banco aleatório por teste e o remove ao terminar, mesmo em erro.

    Só acessa a conexão explicitamente autorizada pela configuração de teste.
    O schema fixo cotaflow exige isolamento por banco, sem traduzir o schema
    real dos modelos. A conexão administrativa também é explicitamente descartável.
    """
    url = disposable_database_url()
    # Generated identifier, never caller-provided; only this database is removed.
    database = f"cotaflow_test_{uuid4().hex}"
    admin = create_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
        isolation_level="AUTOCOMMIT",
    )
    engine = None
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        engine = create_engine(
            url.set(database=database),
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-cstatement_timeout=10000 -clock_timeout=3000",
            },
        )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_database()")) == database
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                with admin.begin() as connection:
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        finally:
            admin.dispose()


@pytest.fixture
def session_factory(postgres_engine):
    """Aplica a migration real e fornece sessões independentes nesse schema."""
    migrate(postgres_engine)
    return sessionmaker(bind=postgres_engine, autoflush=False, close_resets_only=False)
