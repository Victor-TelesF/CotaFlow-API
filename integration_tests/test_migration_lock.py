"""Coordinate separate Alembic processes and inspect actual PostgreSQL locks."""

import os
import subprocess
import sys
from time import monotonic, sleep

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from integration_tests.support import PROJECT_ROOT

# Fixed application namespace and schema-version key, scoped by PostgreSQL database.
LOCK = "SELECT pg_advisory_xact_lock(1129272385, 1)"


def launch(engine, tmp_path, *, timeout="5"):
    url = engine.url
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(PROJECT_ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=tmp_path,
        env=os.environ
        | {
            "DB_HOST": url.host,
            "DB_PORT": str(url.port),
            "DB_NAME": url.database,
            "DB_USER": url.username,
            "DB_PASSWORD": url.password,
            "PGAPPNAME": "cotaflow-migration-lock-test",
            "MIGRATION_LOCK_TIMEOUT_SECONDS": timeout,
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for_advisory_waiters(engine, processes):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            pytest.fail("Migration escaped the advisory lock before release")
        with engine.connect() as connection:
            waiting = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                    "AND application_name='cotaflow-migration-lock-test' "
                    "AND wait_event='advisory'"
                )
            )
        if waiting == len(processes):
            return
        sleep(0.02)  # Poll an observable lock condition, not a timing assumption.
    pytest.fail("Migration processes did not wait for the shared advisory lock")


def finish(process):
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stdout + stderr


def cleanup(processes):
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_two_boot_migrations_wait_then_create_exactly_one_schema(
    postgres_engine, tmp_path
):
    processes = []
    try:
        with postgres_engine.begin() as blocker:
            blocker.exec_driver_sql(LOCK)
            processes = [launch(postgres_engine, tmp_path) for _ in range(2)]
            wait_for_advisory_waiters(postgres_engine, processes)
            assert "cotaflow" not in inspect(postgres_engine).get_schema_names()
        for process in processes:
            finish(process)
        assert set(inspect(postgres_engine).get_table_names(schema="cotaflow")) == {
            "users",
            "assets",
            "transactions",
            "alembic_version",
        }
        with postgres_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM cotaflow.alembic_version"
            ).all() == [("0001",)]
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM cotaflow.transactions"
                ).scalar_one()
                == 0
            )
    finally:
        cleanup(processes)


def test_lock_timeout_leaves_no_schema_and_retry_works(postgres_engine, tmp_path):
    processes = []
    try:
        with postgres_engine.begin() as blocker:
            blocker.exec_driver_sql(LOCK)
            process = launch(postgres_engine, tmp_path, timeout="1")
            processes.append(process)
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode != 0
            assert "LockNotAvailable" in stderr
            assert "lock timeout" in stderr
            assert "synthetic-test-password" not in stdout + stderr
            assert "cotaflow" not in inspect(postgres_engine).get_schema_names()
        process = launch(postgres_engine, tmp_path)
        processes.append(process)
        finish(process)
        assert "transactions" in inspect(postgres_engine).get_table_names(
            schema="cotaflow"
        )
    finally:
        cleanup(processes)


def test_injected_transaction_holds_lock_until_its_own_commit(postgres_engine):
    with postgres_engine.begin() as outer:
        outer.exec_driver_sql("SET LOCAL lock_timeout='700ms'")
        configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
        configuration.attributes["connection"] = outer
        command.upgrade(configuration, "head")
        assert outer.exec_driver_sql("SHOW lock_timeout").scalar_one() == "700ms"
        with postgres_engine.begin() as observer:
            assert not observer.exec_driver_sql(
                "SELECT pg_try_advisory_xact_lock(1129272385, 1)"
            ).scalar_one()
    with postgres_engine.begin() as observer:
        assert observer.exec_driver_sql(
            "SELECT pg_try_advisory_xact_lock(1129272385, 1)"
        ).scalar_one()
