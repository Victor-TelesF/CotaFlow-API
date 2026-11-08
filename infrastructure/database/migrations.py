"""Transferência explícita da instalação antiga; nunca adota tabelas por nome."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import BLANK_SCHEMA

from infrastructure.database.models import DATABASE_SCHEMA, Base


def move_from_public(connection: Connection) -> None:
    """Move somente a revisão 0001 compatível, dentro da transação do Alembic.

    Exige API parada durante a transferência. ALTER TABLE preserva dados,
    constraints, índices e a sequência identity vinculada à tabela. Não usa
    DROP/CASCADE nem transfere objetos desconhecidos de outros projetos.
    """
    inspector = inspect(connection)
    names = {table.name for table in Base.metadata.tables.values()}
    error = "Legacy schema transfer refused: expected an intact public revision 0001 and an empty cotaflow schema"
    if DATABASE_SCHEMA in inspector.get_schema_names() and (
        inspector.get_table_names(schema=DATABASE_SCHEMA)
        or inspector.get_view_names(schema=DATABASE_SCHEMA)
        or inspector.get_sequence_names(schema=DATABASE_SCHEMA)
    ):
        raise RuntimeError(error)
    if not (names | {"alembic_version"}).issubset(
        inspector.get_table_names(schema="public")
    ):
        raise RuntimeError(error)
    if connection.scalars(
        text("SELECT version_num FROM public.alembic_version")
    ).all() != ["0001"]:
        raise RuntimeError(error)

    legacy_metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(
            legacy_metadata,
            schema=None,
            referred_schema_fn=lambda table, schema, constraint, referred: BLANK_SCHEMA,
        )
    migration_context = MigrationContext.configure(
        connection,
        opts={
            "include_schemas": True,
            "include_name": lambda name, kind, parents: (
                name in {None, "public"}
                if kind == "schema"
                else name in names
                if kind == "table"
                else True
            ),
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    if compare_metadata(migration_context, legacy_metadata):
        raise RuntimeError(error)

    # Identifiers are constants from application metadata, never CLI input.
    for name in ("users", "assets", "transactions", "alembic_version"):
        connection.exec_driver_sql(
            f"ALTER TABLE public.{name} SET SCHEMA {DATABASE_SCHEMA}"
        )
