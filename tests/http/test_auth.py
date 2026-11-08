from contextlib import contextmanager
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from application.types import User
from infrastructure.config import MarketSettings, Settings
from main import create_app
from presentation.http.dependencies import CurrentUser
from presentation.http.schemas.auth import RegisterRequest


@pytest.fixture
def auth_app(security_settings):
    app = create_app(
        Settings(
            _env_file=None,
            DB_HOST="127.0.0.1",
            DB_PORT=1,
            DB_PASSWORD="synthetic-password",
        ),
        security_settings,
        MarketSettings(_env_file=None),
    )

    @app.get("/test-identity")
    def identity(user: CurrentUser):
        return {"id": str(user.id), "email": user.email}

    return app


@pytest.mark.parametrize(
    "header", [None, "Basic synthetic", "Bearer", "Bearer not-a-jwt"]
)
def test_identity_dependency_rejects_missing_or_invalid_auth_before_database(
    auth_app, header
):
    with TestClient(auth_app) as client:

        def forbidden_database():
            pytest.fail(
                "Invalid authentication must be rejected before database access"
            )

        auth_app.state.uow_factory = forbidden_database
        response = client.get(
            "/test-identity", headers={"Authorization": header} if header else {}
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "code": "invalid_token",
        "message": "Authentication failed",
    }


def test_identity_comes_from_verified_token_and_requires_existing_user(auth_app):
    alice = User("alice@example.com", "synthetic-not-exposed")
    bob = User("bob@example.com", "synthetic-not-exposed")
    users = {alice.id: alice, bob.id: bob}

    class Users:
        def get(self, user_id):
            return users.get(user_id)

    class Uow:
        users = Users()

    @contextmanager
    def uow_factory():
        yield Uow()

    with TestClient(auth_app) as client:
        auth_app.state.uow_factory = uow_factory
        for user, other in [(alice, bob), (bob, alice)]:
            token = auth_app.state.tokens.issue(user.id)
            response = client.get(
                f"/test-identity?user_id={other.id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-User-Id": str(other.id),
                },
            )
            assert response.status_code == 200
            assert response.json() == {"id": str(user.id), "email": user.email}
        token = auth_app.state.tokens.issue(uuid4())
        response = client.get(
            "/test-identity", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize(
    "body, location",
    [
        ({"email": "invalid-private-email", "password": "synthetic-password"}, "email"),
        ({"email": "alice@example.com", "password": "short"}, "password"),
        ({"email": "alice@example.com", "password": "a" * 129}, "password"),
        (
            {"email": "alice@example.com", "password": ["private-invalid-secret"]},
            "password",
        ),
        (
            {
                "email": "alice@example.com",
                "password": "synthetic-password",
                "user_id": "spoofed",
            },
            "user_id",
        ),
    ],
)
def test_register_validation_has_safe_errors_and_does_not_reach_database(
    auth_app, body, location
):
    with TestClient(auth_app) as client:

        def forbidden_database():
            pytest.fail("Invalid request must not reach persistence")

        auth_app.state.uow_factory = forbidden_database
        response = client.post("/api/v1/auth/register", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["details"][0]["location"] == ["body", location]
    assert "input" not in response.text
    assert "private-invalid-secret" not in response.text
    assert "invalid-private-email" not in response.text
    assert "synthetic-password" not in response.text


def test_http_credentials_normalize_email_and_preserve_password_exactly():
    for password in ("x" * 8, "x" * 128, " password-with-spaces "):
        request = RegisterRequest(email=" Alice@Example.COM ", password=password)
        assert request.email == "alice@example.com"
        assert request.password.get_secret_value() == password
        assert password not in repr(request)


@pytest.mark.parametrize(
    "defect",
    [
        "expired",
        "missing_sub",
        "missing_exp",
        "bad_subject",
        "bad_signature",
        "wrong_algorithm",
    ],
)
def test_signed_invalid_tokens_return_401_before_identity_lookup(
    auth_app, security_settings, auth_clock, defect
):
    key = security_settings.JWT_SECRET_KEY.get_secret_value()
    payload = {"sub": str(uuid4()), "exp": 1893457800}
    if defect == "expired":
        payload["exp"] = 1893455999
    elif defect == "missing_sub":
        del payload["sub"]
    elif defect == "missing_exp":
        del payload["exp"]
    elif defect == "bad_subject":
        payload["sub"] = "invalid-uuid"
    elif defect == "bad_signature":
        key = "other-synthetic-key-with-at-least-32-characters"
    token = jwt.encode(
        payload, key, algorithm="HS384" if defect == "wrong_algorithm" else "HS256"
    )
    with TestClient(auth_app) as client:

        def forbidden_database():
            pytest.fail("Invalid JWT must not reach identity lookup")

        auth_app.state.uow_factory = forbidden_database
        response = client.get(
            "/test-identity", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert token not in response.text
