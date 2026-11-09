"""Senhas são preservadas exatamente, mas precisam ser codificáveis em UTF-8."""

import json

import pytest
from fastapi.testclient import TestClient

from infrastructure.config import Settings
from main import create_app


@pytest.mark.parametrize("operation", ["register", "login"])
def test_unpaired_surrogate_password_rejected_before_database_and_password_work(
    security_settings, monkeypatch, operation
):
    app = create_app(
        Settings(_env_file=None, DB_PORT=1, DB_PASSWORD="synthetic-password"),
        security_settings,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        calls = []

        def forbidden(*args, **kwargs):
            calls.append(True)
            raise RuntimeError("Invalid password reached persistence or Argon2")

        app.state.uow_factory = forbidden
        monkeypatch.setattr(app.state.passwords, "hash", forbidden)
        monkeypatch.setattr(app.state.passwords, "verify", forbidden)
        response = client.post(
            f"/api/v1/auth/{operation}",
            content=json.dumps(
                {"email": "synthetic@example.com", "password": "abcdefgh\ud800"}
            ),
            headers={"Content-Type": "application/json"},
        )
    assert calls == []
    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "message": "Invalid request data",
        "details": [{"location": ["body", "password"], "type": "value_error"}],
    }
    assert "abcdefgh" not in response.text
