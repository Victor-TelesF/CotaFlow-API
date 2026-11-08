"""Explicit opt-in PostgreSQL tests; never fall back to application settings."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from integration_tests.support import disposable_database_url, migrate


@pytest.fixture
def postgres_engine():
    """Cria um schema aleatório por teste e o remove ao terminar, mesmo em erro.

    Só acessa a conexão explicitamente autorizada pela configuração de teste.
    Criação/remoção usam um engine sem pool; o engine do teste fixa search_path
    no schema gerado e limita tempos de conexão, consulta e espera por lock.
    """
    url = disposable_database_url()
    # Generated identifier, never caller-provided; only this schema is removed.
    schema = f"ct_test_{uuid4().hex}"
    admin = create_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            url,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=3000",
            },
        )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            admin.dispose()


@pytest.fixture
def session_factory(postgres_engine):
    """Aplica a migration real e fornece sessões independentes nesse schema."""
    migrate(postgres_engine)
    return sessionmaker(bind=postgres_engine, autoflush=False, close_resets_only=False)
