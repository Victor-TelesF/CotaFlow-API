"""Browser access is opt-in and never bypasses bearer authentication."""

import json

import pytest
from fastapi.testclient import TestClient

from infrastructure.config import Settings
from main import create_app

ORIGIN = "https://synthetic-cotaflow.vercel.app"


@pytest.fixture
def cors_client(monkeypatch, security_settings):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", json.dumps([ORIGIN]))
    settings = Settings(_env_file=None, DB_PORT=1, DB_PASSWORD="synthetic-password")
    with TestClient(create_app(settings, security_settings)) as client:
        yield client


def test_vercel_preflight_accepts_bearer_and_json_without_database(cors_client):
    response = cors_client.options(
        "/api/v1/portfolio/transactions",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "Origin" in response.headers["vary"]
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "https://unrelated.vercel.app",
        "https://synthetic-cotaflow.vercel.app.evil.test",
        "null",
    ],
)
def test_unlisted_origins_are_not_granted_browser_access(cors_client, origin):
    response = cors_client.options(
        "/api/v1/portfolio/positions",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_still_requires_token_and_can_read_error_headers(cors_client):
    response = cors_client.get(
        "/api/v1/portfolio/positions", headers={"Origin": ORIGIN}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert {"retry-after", "x-request-id"} <= {
        value.strip().lower()
        for value in response.headers["access-control-expose-headers"].split(",")
    }


def test_cors_disabled_until_an_origin_is_set(monkeypatch, security_settings):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "[]")
    settings = Settings(_env_file=None, DB_PORT=1, DB_PASSWORD="synthetic-password")
    with TestClient(create_app(settings, security_settings)) as client:
        response = client.get("/health", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_body_rejection_is_readable_by_allowed_frontend(cors_client):
    response = cors_client.post(
        "/api/v1/auth/login", content=b"x" * 17000, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == ORIGIN
