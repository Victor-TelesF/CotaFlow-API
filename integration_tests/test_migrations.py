"""Real migrations in a disposable database, including coexisting APIs."""

import os
import runpy
import subprocess
import sys
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from infrastructure.config import Settings
from infrastructure.database.models import AssetModel, Base, TransactionModel, UserModel
from infrastructure.database.session import Database
from integration_tests.support import PROJECT_ROOT, migrate

TABLES = {"users", "assets", "transactions", "alembic_version"}


def tables(engine, schema="cotaflow"):
    return set(inspect(engine).get_table_names(schema=schema))


def seed_legacy(connection):
    """Run the unchanged historical revision exactly as before schema isolation."""
    migration = runpy.run_path(str(PROJECT_ROOT / "alembic/versions/0001_initial.py"))
    with Operations.context(MigrationContext.configure(connection)):
        migration["upgrade"]()
    connection.exec_driver_sql(
        "CREATE TABLE public.alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
    )
    connection.exec_driver_sql("INSERT INTO public.alembic_version VALUES ('0001')")
    user_id, transaction_id = uuid4(), uuid4()
    connection.execute(
        text(
            "INSERT INTO public.users VALUES (:id, 'legacy@example.com', 'synthetic-hash')"
        ),
        {"id": user_id},
    )
    connection.exec_driver_sql(
        "INSERT INTO public.assets VALUES ('TEST3', 'Synthetic asset', 'stock')"
    )
    connection.execute(
        text(
            "INSERT INTO public.transactions "
            "(id, user_id, asset_ticker, quantity, price, transaction_type, transaction_date) "
            "VALUES (:id, :user_id, 'TEST3', 10, 30, 'buy', '2026-01-01')"
        ),
        {"id": transaction_id, "user_id": user_id},
    )
    return user_id, transaction_id


def test_initial_migration_up_down_up_and_metadata_match(postgres_engine, tmp_path):
    # Another project's same-named tables and revision must survive both directions.
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("CREATE SCHEMA other_api")
        for schema in ("public", "other_api"):
            for name in TABLES:
                connection.exec_driver_sql(
                    f"CREATE TABLE {schema}.{name} (marker TEXT)"
                )
                connection.exec_driver_sql(
                    f"INSERT INTO {schema}.{name} VALUES ('belongs-to-another-api')"
                )
    migrate(postgres_engine)
    assert tables(postgres_engine) == TABLES
    checked = cli(postgres_engine, tmp_path, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    with postgres_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, parents: (
                    name == "cotaflow" if kind == "schema" else True
                ),
                "version_table_schema": "cotaflow",
            },
        )
        assert compare_metadata(context, Base.metadata) == []
    migrate(postgres_engine, "base")
    assert tables(postgres_engine) == {"alembic_version"}
    migrate(postgres_engine)
    assert tables(postgres_engine) == TABLES
    with postgres_engine.connect() as connection:
        for schema in ("public", "other_api"):
            assert tables(connection, schema) == TABLES
            for name in TABLES:
                assert (
                    connection.exec_driver_sql(
                        f"SELECT marker FROM {schema}.{name}"
                    ).scalar_one()
                    == "belongs-to-another-api"
                )


def cli(engine, tmp_path, *arguments, search_path="public"):
    url = engine.url
    environment = os.environ | {
        "DB_DRIVER": "postgresql+psycopg",
        "DB_HOST": url.host,
        "DB_PORT": str(url.port),
        "DB_NAME": url.database,
        "DB_USER": url.username,
        "DB_PASSWORD": url.password,
        # Even an explicitly different default schema must not redirect migration.
        "PGOPTIONS": f"-csearch_path={search_path}",
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(PROJECT_ROOT / "alembic.ini"),
            *arguments,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_alembic_cli_uses_environment_for_online_and_offline_migrations(
    postgres_engine, tmp_path
):
    offline = cli(postgres_engine, tmp_path, "upgrade", "head", "--sql")
    assert offline.returncode == 0, offline.stderr
    assert "CREATE SCHEMA IF NOT EXISTS cotaflow" in offline.stdout
    assert "SET LOCAL search_path TO cotaflow" in offline.stdout
    assert "CREATE TABLE cotaflow.alembic_version" in offline.stdout
    assert "NUMERIC(18, 8)" in offline.stdout
    assert "cotaflow" not in inspect(postgres_engine).get_schema_names()
    online = cli(postgres_engine, tmp_path, "upgrade", "head")
    assert online.returncode == 0, online.stderr
    assert tables(postgres_engine) == TABLES
    assert tables(postgres_engine, "public") == set()
    again = cli(postgres_engine, tmp_path, "upgrade", "head")
    assert again.returncode == 0, again.stderr
    checked = cli(postgres_engine, tmp_path, "check", search_path="cotaflow")
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_explicit_legacy_move_preserves_rows_foreign_keys_and_identity(
    postgres_engine, tmp_path
):
    with postgres_engine.begin() as connection:
        user_id, transaction_id = seed_legacy(connection)
        connection.exec_driver_sql("CREATE TABLE public.unrelated (marker INT)")
        connection.exec_driver_sql("INSERT INTO public.unrelated VALUES (42)")
    result = cli(
        postgres_engine, tmp_path, "-x", "move_from_public=1", "upgrade", "head"
    )
    assert result.returncode == 0, result.stderr
    assert tables(postgres_engine) == TABLES
    assert tables(postgres_engine, "public") == {"unrelated"}
    # Real application engine: models must work without a configured search_path.
    url = postgres_engine.url
    database = Database(
        Settings(
            _env_file=None,
            DB_HOST=url.host,
            DB_PORT=url.port,
            DB_NAME=url.database,
            DB_USER=url.username,
            DB_PASSWORD=url.password,
        )
    )
    try:
        with database.session_factory() as session:
            assert session.get(UserModel, user_id).email == "legacy@example.com"
            assert session.get(AssetModel, "TEST3").name == "Synthetic asset"
            row = session.get(TransactionModel, transaction_id)
            assert (row.quantity, row.price, row.sequence) == (
                Decimal(10),
                Decimal(30),
                1,
            )
            session.add(
                TransactionModel(
                    id=uuid4(),
                    user_id=user_id,
                    asset_ticker="TEST3",
                    quantity=Decimal(5),
                    price=Decimal(40),
                    transaction_type="buy",
                    transaction_date=date(2026, 1, 2),
                )
            )
            session.commit()
        with Session(postgres_engine) as session:
            sequences = session.scalars(
                text("SELECT sequence FROM cotaflow.transactions ORDER BY sequence")
            ).all()
            assert sequences == [1, 2]
            assert session.scalar(text("SELECT marker FROM public.unrelated")) == 42
        fks = inspect(postgres_engine).get_foreign_keys(
            "transactions", schema="cotaflow"
        )
        assert {fk["referred_schema"] for fk in fks} == {"cotaflow"}
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT pg_get_serial_sequence('cotaflow.transactions', 'sequence')"
                    )
                )
                == "cotaflow.transactions_sequence_seq"
            )
    finally:
        database.dispose()


@pytest.mark.parametrize(
    "problem", ["wrong_revision", "missing_table", "changed_column", "target_exists"]
)
def test_legacy_move_rejects_ambiguous_state_without_partial_changes(
    postgres_engine, tmp_path, problem
):
    with postgres_engine.begin() as connection:
        user_id, _ = seed_legacy(connection)
        if problem == "wrong_revision":
            connection.exec_driver_sql(
                "UPDATE public.alembic_version SET version_num = 'another_api'"
            )
        elif problem == "missing_table":
            connection.exec_driver_sql("DROP TABLE public.transactions")
        elif problem == "changed_column":
            connection.exec_driver_sql(
                "ALTER TABLE public.assets ADD COLUMN unexpected INT"
            )
        else:
            connection.exec_driver_sql("CREATE SCHEMA cotaflow")
            connection.exec_driver_sql("CREATE TABLE cotaflow.unrelated (marker INT)")
    before = tables(postgres_engine, "public")
    result = cli(
        postgres_engine, tmp_path, "-x", "move_from_public=1", "upgrade", "head"
    )
    assert result.returncode != 0
    assert "Legacy schema transfer refused" in result.stderr
    assert tables(postgres_engine, "public") == before
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT id FROM public.users")) == user_id
    if problem == "target_exists":
        assert tables(postgres_engine) == {"unrelated"}
    else:
        assert "cotaflow" not in inspect(postgres_engine).get_schema_names()
