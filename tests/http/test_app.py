import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from infrastructure.config import Settings
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
    app = create_app(settings, security_settings)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert set(client.get("/openapi.json").json()["paths"]) == {
            "/health",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
        }
    assert not hasattr(app.state, "uow_factory")
