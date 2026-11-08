"""Conecta o Alembic aos metadados sem usar recursos globais da API.

Na CLI, carrega a configuração por ambiente e usa um engine sem pool mantido
entre execuções. Testes podem fornecer uma conexão restrita a schema exclusivo.
O modo offline produz SQL e não acessa o PostgreSQL.
"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from infrastructure.config import Settings
from infrastructure.database.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Gera SQL PostgreSQL com base nas revisões, sem abrir uma conexão."""
    context.configure(
        url=Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Executa revisões em transação e libera o engine criado pela CLI.

    Se houver conexão injetada, seu ciclo de vida pertence ao chamador.
    Não criar nem migrar tabelas automaticamente no startup do FastAPI.
    """
    # Tests inject a connection confined to an explicitly disposable schema.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(
        Settings().database_url, poolclass=pool.NullPool, hide_parameters=True
    )
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
