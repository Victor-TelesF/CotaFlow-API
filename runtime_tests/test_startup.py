"""Exercise the actual image command against disposable PostgreSQL, without mocks."""

import json
import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text


def docker(*arguments, environment=None):
    result = subprocess.run(
        ["docker", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if arguments[0] == "logs":
        return result.stdout + result.stderr
    return result.stdout.strip()


@pytest.fixture
def start_container(postgres_engine):
    image = os.environ.get("TEST_RUNTIME_IMAGE")
    if not image:
        raise pytest.UsageError("Runtime checks require a built TEST_RUNTIME_IMAGE")
    database_container = os.environ.get("TEST_RUNTIME_DATABASE_CONTAINER")
    if not database_container:
        raise pytest.UsageError(
            "Runtime checks require TEST_RUNTIME_DATABASE_CONTAINER"
        )
    assert state(database_container)["Running"]
    docker("image", "inspect", image)
    containers = []

    def start(*, network=None, port="8000", database_port=5432):
        url = postgres_engine.url
        environment = {
            "DB_DRIVER": "postgresql+psycopg",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": str(database_port),
            "DB_NAME": url.database,
            "DB_USER": url.username,
            "DB_PASSWORD": url.password,
            "PGSSLMODE": "disable",  # Only the explicitly disposable local server.
            "PGCONNECT_TIMEOUT": "5",
            "JWT_SECRET_KEY": "synthetic-runtime-key-with-at-least-32-characters",
            "PORT": str(port),
        }
        name = f"cotaflow-runtime-test-{uuid4().hex}"
        containers.append(name)
        # Values stay in the subprocess environment, never in Docker command args.
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            network or f"container:{database_container}",
            *[argument for key in environment for argument in ("--env", key)],
            image,
            environment=os.environ | environment,
        )
        return name

    yield start
    for name in reversed(containers):
        docker("rm", "--force", name)


def state(name):
    return json.loads(docker("inspect", "--format", "{{json .State}}", name))


def http_request(name, port, path="/health", payload=None):
    """Use real HTTP inside the test network, including on Docker Desktop."""
    script = """
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

payload = json.loads(sys.argv[3])
request = Request(
    'http://127.0.0.1:' + sys.argv[1] + sys.argv[2],
    data=json.dumps(payload).encode() if payload is not None else None,
    headers={'Content-Type': 'application/json'},
)
try:
    result = urlopen(request, timeout=3)
except HTTPError as error:
    result = error
with result as response:
    print(json.dumps({'status': response.status, 'body': json.load(response)}))
"""
    result = subprocess.run(
        [
            "docker",
            "exec",
            name,
            "python",
            "-c",
            script,
            str(port),
            path,
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return json.loads(result.stdout) if result.returncode == 0 else None


def wait_until_serving(name, port):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        assert state(name)["Running"], docker("logs", name)
        response = http_request(name, port)
        if response is not None:
            assert response == {"status": 200, "body": {"status": "ok"}}
            return
        sleep(0.1)  # Poll readiness; never assume a fixed startup duration.
    pytest.fail(f"Runtime did not become ready: {docker('logs', name)}")


def test_startup_migrates_configured_database_and_restart_preserves_data(
    postgres_engine, start_container
):
    port = 18080  # Deployment PORT, distinct from the image's default.
    name = start_container(port=port)
    wait_until_serving(name, port)
    assert set(inspect(postgres_engine).get_table_names(schema="cotaflow")) == {
        "users",
        "assets",
        "transactions",
        "alembic_version",
    }
    assert inspect(postgres_engine).get_table_names(schema="public") == []
    user_id = uuid4()
    with postgres_engine.begin() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM cotaflow.alembic_version"))
            == "0001"
        )
        connection.execute(
            text(
                "INSERT INTO cotaflow.users VALUES (:id, 'preserved@example.com', 'synthetic-hash')"
            ),
            {"id": user_id},
        )

    response = http_request(
        name,
        port,
        "/api/v1/auth/login",
        {"email": "absent@example.com", "password": "synthetic"},
    )
    assert response is not None
    assert response["status"] == 401
    assert response["body"]["code"] == "invalid_credentials"

    # exec must forward SIGTERM to Uvicorn instead of forcing a SIGKILL timeout.
    docker("stop", "--time", "10", name)
    assert state(name)["ExitCode"] == 0
    docker("start", name)
    wait_until_serving(name, port)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT id FROM cotaflow.users")) == user_id
        assert (
            connection.scalar(text("SELECT count(*) FROM cotaflow.alembic_version"))
            == 1
        )
        assert (
            connection.scalar(text("SELECT version_num FROM cotaflow.alembic_version"))
            == "0001"
        )


@pytest.mark.parametrize("failure", ["connection", "revision"])
def test_migration_failure_exits_before_starting_http(
    postgres_engine, start_container, failure
):
    if failure == "revision":
        with postgres_engine.begin() as connection:
            connection.exec_driver_sql("CREATE SCHEMA cotaflow")
            connection.exec_driver_sql(
                "CREATE TABLE cotaflow.alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "INSERT INTO cotaflow.alembic_version VALUES ('unknown_revision')"
            )
        name = start_container()
        expected_error = "Can't locate revision identified by 'unknown_revision'"
    else:
        name = start_container(network="none", database_port=1)
        expected_error = "Connection refused"

    deadline = monotonic() + 10
    while state(name)["Running"] and monotonic() < deadline:
        sleep(0.1)
    final_state = state(name)
    logs = docker("logs", name)
    assert not final_state["Running"], (
        "HTTP process stayed alive despite migration failure"
    )
    assert final_state["ExitCode"] != 0
    assert expected_error in logs
    assert "Uvicorn running" not in logs
    assert "Application startup complete" not in logs
