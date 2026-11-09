"""Regressões HTTP: rejeitar trabalho antes de parsing/Argon2/banco."""

import pytest
from fastapi.testclient import TestClient

from infrastructure.config import MarketSettings, Settings
from main import create_app


@pytest.fixture
def protected_app(security_settings):
    return create_app(
        Settings(_env_file=None, DB_PASSWORD="synthetic", DB_PORT=1),
        security_settings,
        MarketSettings(_env_file=None),
    )


def test_login_ip_quota_counts_even_invalid_requests(protected_app):
    with TestClient(protected_app) as client:
        for _ in range(20):
            assert client.post("/api/v1/auth/login", json={}).status_code == 422
        response = client.post("/api/v1/auth/login", json={})
    assert response.status_code == 429
    assert response.json() == {
        "code": "authentication_rate_limited",
        "message": "Authentication budget exhausted",
    }
    assert 1 <= int(response.headers["Retry-After"]) <= 600


def test_body_over_16_kib_is_rejected_before_json_validation(protected_app):
    with TestClient(protected_app) as client:
        response = client.post(
            "/api/v1/auth/register",
            content=b"{}" + b" " * (16385 - 2),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json() == {
        "code": "request_too_large",
        "message": "Request body exceeds 16384 bytes",
    }


def test_pair_quota_normalizes_email_and_rejects_before_verify_or_database(
    protected_app, monkeypatch
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from infrastructure.auth_limits import AuthenticationLimits
    from infrastructure.config import AuthSettings

    clock = [0.0]
    reads = []
    verifications = []

    @contextmanager
    def uow_factory():
        reads.append(True)
        yield SimpleNamespace(users=SimpleNamespace(get_by_email=lambda _: None))

    with TestClient(protected_app) as client:
        protected_app.state.auth_limits = AuthenticationLimits(
            AuthSettings(_env_file=None), clock=lambda: clock[0]
        )
        protected_app.state.uow_factory = uow_factory
        real_verify = protected_app.state.passwords.verify

        def verify(password, password_hash):
            verifications.append(password_hash)
            return real_verify(password, password_hash)

        monkeypatch.setattr(protected_app.state.passwords, "verify", verify)
        for _ in range(5):
            response = client.post(
                "/api/v1/auth/login",
                json={"email": "Alice@example.com", "password": "wrong-password"},
            )
            assert response.status_code == 401
            assert response.json() == {
                "code": "invalid_credentials",
                "message": "Authentication failed",
            }
        response = client.post(
            "/api/v1/auth/login",
            json={"email": " ALICE@EXAMPLE.COM ", "password": "wrong-password"},
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "300"
        assert response.headers["Cache-Control"] == "no-store"
        assert len(reads) == 5
        assert (
            verifications == [None] * 5
        )  # Real dummy Argon2 verification for admitted requests.
        assert "alice" not in response.text.lower()
        assert "wrong-password" not in response.text
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"email": "other@example.com", "password": "wrong-password"},
            ).status_code
            == 401
        )
        clock[0] = 300
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"email": "alice@example.com", "password": "wrong-password"},
            ).status_code
            == 401
        )
        assert len(reads) == len(verifications) == 7


def test_openapi_documents_auth_limits_and_body_failures(protected_app):
    schema = protected_app.openapi()
    for path in ("/api/v1/auth/register", "/api/v1/auth/login"):
        responses = schema["paths"][path]["post"]["responses"]
        assert responses["413"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
        assert responses["429"]["headers"]["Retry-After"]["schema"] == {
            "type": "integer",
            "minimum": 1,
            "maximum": 600,
        }


def invoke_asgi(app, chunks, headers=(), path="/test-body"):
    import asyncio

    messages = []
    received = []
    pending = iter(chunks)

    async def receive():
        message = next(pending)
        received.append(message)
        return message

    async def send(message):
        messages.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": list(headers),
                "client": ("192.0.2.1", 1234),
                "server": ("test", 80),
            },
            receive,
            send,
        )
    )
    return messages, received


@pytest.mark.parametrize(
    "headers", [[], [(b"content-length", b"1")], [(b"transfer-encoding", b"chunked")]]
)
def test_chunked_body_counts_actual_bytes_before_endpoint(protected_app, headers):
    from fastapi import Request

    calls = []

    @protected_app.post("/test-body")
    async def consume(request: Request):
        calls.append(True)
        return {"length": len(await request.body())}

    messages, received = invoke_asgi(
        protected_app,
        [
            {"type": "http.request", "body": b"x" * 8192, "more_body": True},
            {"type": "http.request", "body": b"x" * 8192, "more_body": True},
            {"type": "http.request", "body": b"X", "more_body": True},
            {"type": "http.request", "body": b"private-marker", "more_body": False},
        ],
        headers,
    )
    assert messages[0]["status"] == 413
    assert b"private-marker" not in messages[1]["body"]
    assert len(received) == 3  # No drain of an unbounded attacker stream.
    assert calls == []


def test_body_exactly_at_limit_replays_bytes_to_real_endpoint(protected_app):
    from fastapi import Request

    @protected_app.post("/test-body")
    async def consume(request: Request):
        body = await request.body()
        return {"length": len(body), "ending": body[-2:].decode()}

    messages, received = invoke_asgi(
        protected_app,
        [
            {"type": "http.request", "body": "á".encode() * 4096, "more_body": True},
            {"type": "http.request", "body": b"x" * 8190 + b"OK", "more_body": False},
        ],
    )
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b'{"length":16384,"ending":"OK"}'
    assert len(received) == 2


@pytest.mark.parametrize("length", [b"16385", b"9" * 5000, b"0" * 5000 + b"16385"])
def test_declared_oversize_rejects_without_reading_body(protected_app, length):
    messages, received = invoke_asgi(protected_app, [], [(b"content-length", length)])
    assert messages[0]["status"] == 413
    assert received == []


def test_disconnect_does_not_run_endpoint_or_send_response(protected_app):
    from fastapi import Request

    calls = []

    @protected_app.post("/test-body")
    async def consume(request: Request):
        calls.append(True)
        return {}

    messages, received = invoke_asgi(
        protected_app,
        [
            {"type": "http.request", "body": b"{", "more_body": True},
            {"type": "http.disconnect"},
        ],
    )
    assert messages == []
    assert len(received) == 2
    assert calls == []


def test_oversize_financial_request_rejects_before_identity_database(protected_app):
    with TestClient(protected_app) as client:

        def forbidden_database():
            pytest.fail("Body must be rejected before identity lookup")

        protected_app.state.uow_factory = forbidden_database
        token = protected_app.state.tokens.issue(__import__("uuid").uuid4())
        response = client.post(
            "/api/v1/portfolio/transactions",
            content=b"private-marker" * 2000,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 413
    assert "private-marker" not in response.text
    assert token not in response.text
