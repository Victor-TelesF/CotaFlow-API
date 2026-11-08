import os
import subprocess
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from infrastructure.database.models import Base
from integration_tests.support import PROJECT_ROOT, migrate


def test_initial_migration_up_down_up_and_metadata_match(postgres_engine):
    assert inspect(postgres_engine).get_table_names() == []
    migrate(postgres_engine)
    assert set(inspect(postgres_engine).get_table_names()) == {
        "users",
        "assets",
        "transactions",
        "alembic_version",
    }
    with postgres_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
    migrate(postgres_engine, "base")
    assert inspect(postgres_engine).get_table_names() == ["alembic_version"]
    migrate(postgres_engine)
    assert set(inspect(postgres_engine).get_table_names()) == {
        "users",
        "assets",
        "transactions",
        "alembic_version",
    }


def test_alembic_cli_uses_environment_for_online_and_offline_migrations(
    postgres_engine, tmp_path
):
    url = postgres_engine.url
    with postgres_engine.connect() as connection:
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    environment = os.environ | {
        "DB_DRIVER": "postgresql+psycopg",
        "DB_HOST": url.host,
        "DB_PORT": str(url.port),
        "DB_NAME": url.database,
        "DB_USER": url.username,
        "DB_PASSWORD": url.password,
        "PGOPTIONS": f"-csearch_path={schema}",
    }
    command = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(PROJECT_ROOT / "alembic.ini"),
        "upgrade",
        "head",
    ]
    offline = subprocess.run(
        [*command, "--sql"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert offline.returncode == 0, offline.stderr
    assert "CREATE TABLE transactions" in offline.stdout
    assert "NUMERIC(18, 8)" in offline.stdout
    assert inspect(postgres_engine).get_table_names() == []
    online = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert online.returncode == 0, online.stderr
    assert set(inspect(postgres_engine).get_table_names()) == {
        "users",
        "assets",
        "transactions",
        "alembic_version",
    }
