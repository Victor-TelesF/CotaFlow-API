import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from infrastructure.config import MarketSettings, Settings
from main import create_app


def test_imports_do_not_require_credentials_or_create_resources(tmp_path):
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("DB_")
    }
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import infrastructure.config as config; "
                "import infrastructure.database.session as session; "
                "import main; "
                "assert not hasattr(config, 'settings'); "
                "assert not hasattr(session, 'engine'); "
                "assert not hasattr(main.app.state, 'uow_factory')"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_healthcheck_and_openapi_without_database_access(security_settings):
    settings = Settings(
        _env_file=None,
        DB_DRIVER="postgresql+psycopg",
        DB_HOST="127.0.0.1",
        DB_PORT=1,
        DB_NAME="synthetic_health",
        DB_USER="synthetic_user",
        DB_PASSWORD="synthetic-test-password",
    )
    app = create_app(settings, security_settings, MarketSettings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert set(client.get("/openapi.json").json()["paths"]) == {
            "/health",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/assets",
            "/api/v1/portfolio/transactions",
            "/api/v1/portfolio/positions",
            "/api/v1/portfolio/valuation",
        }
    assert not hasattr(app.state, "uow_factory")


def test_copied_env_example_blocks_api_startup(tmp_path, monkeypatch):
    example = Path(__file__).resolve().parents[2] / ".env.example"
    (tmp_path / ".env").write_bytes(example.read_bytes())
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_ACCESS_TOKEN_MINUTES", raising=False)
    app = create_app()
    with pytest.raises(ValidationError) as caught, TestClient(app):
        pytest.fail("API accepted the copied example signing key")
    assert caught.value.errors(include_input=False)[0]["loc"] == ("JWT_SECRET_KEY",)
    assert not hasattr(app.state, "uow_factory")


def test_legacy_placeholder_stops_uvicorn_before_opening_http(tmp_path):
    secret = " \treplace-with-a-random-secret-of-at-least-32-characters\n"
    environment = os.environ | {
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "JWT_SECRET_KEY": secret,
        "JWT_ACCESS_TOKEN_MINUTES": "30",
        "DB_PASSWORD": "synthetic-startup-password",
        "DB_PORT": "1",
        "BRAPI_TOKEN": "",
    }
    socket_path = tmp_path / "api.sock"
    with subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--uds", str(socket_path)],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            pytest.fail("Uvicorn stayed alive with the published signing key")
    output = stdout + stderr
    assert process.returncode != 0
    assert "JWT_SECRET_KEY" in output
    assert "Application startup failed" in output
    assert "Application startup complete" not in output
    assert "replace-with-a-random-secret-of-at-least-32-characters" not in output
    assert not socket_path.exists()
