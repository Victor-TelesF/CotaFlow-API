"""Migrations confinadas ao schema cotaflow, inclusive a tabela de versões.

A revisão inicial é preservada. Seu DDL não qualificado executa somente com
search_path transacional fixo; a API usa metadados explicitamente qualificados.
Não adotar automaticamente uma tabela alembic_version de outro projeto.
"""

from contextlib import nullcontext
from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from sqlalchemy.schema import CreateSchema

from alembic import context
from infrastructure.config import Settings
from infrastructure.database.migrations import move_from_public
from infrastructure.database.models import DATABASE_SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(name, kind, parents):
    """Autogenerate não deve propor alterações em schemas de outras APIs."""
    return name == DATABASE_SCHEMA if kind == "schema" else True


def migration_options():
    return {
        "target_metadata": target_metadata,
        "version_table_schema": DATABASE_SCHEMA,
        "include_schemas": True,
        "include_name": include_name,
    }


def run_migrations_offline() -> None:
    """SQL para instalação nova; transferência antiga exige inspeção online."""
    if context.get_x_argument(as_dictionary=True).get("move_from_public"):
        raise RuntimeError("Legacy schema transfer requires an online connection")
    context.configure(
        url=Settings().database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **migration_options(),
    )
    with context.begin_transaction():
        context.execute(CreateSchema(DATABASE_SCHEMA, if_not_exists=True))
        context.execute(f"SET LOCAL search_path TO {DATABASE_SCHEMA}")
        context.run_migrations()


def migrate_connection(connection) -> None:
    """Criação do schema, transferência opcional e revisões são uma transação."""
    transaction = nullcontext() if connection.in_transaction() else connection.begin()
    with transaction:
        context.configure(connection=connection, **migration_options())
        connection.execute(CreateSchema(DATABASE_SCHEMA, if_not_exists=True))
        transfer = context.get_x_argument(as_dictionary=True).get("move_from_public")
        if transfer is not None:
            if transfer != "1":
                raise RuntimeError(
                    "Use -x move_from_public=1 to transfer legacy tables"
                )
            move_from_public(connection)
        # SET LOCAL is reverted on transaction end, including pooled connections.
        connection.exec_driver_sql(f"SET LOCAL search_path TO {DATABASE_SCHEMA}")
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_online() -> None:
    """Testes podem injetar conexão; a CLI abre/fecha seu próprio engine."""
    connection = config.attributes.get("connection")
    if connection is not None:
        migrate_connection(connection)
        return
    engine = create_engine(
        Settings().database_url,
        poolclass=pool.NullPool,
        hide_parameters=True,
        # Keep reflection's default schema stable even when PGOPTIONS differs.
        # DDL runs with SET LOCAL cotaflow inside the transaction below.
        connect_args={"options": "-csearch_path=public"},
    )
    try:
        with engine.connect() as connection:
            migrate_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
