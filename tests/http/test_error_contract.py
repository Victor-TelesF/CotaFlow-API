"""OpenAPI deve descrever o envelope seguro realmente retornado pela API."""

from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx2
from fastapi.testclient import TestClient

from application.types import User
from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.portfolio import Transaction
from infrastructure.config import MarketSettings, Settings
from infrastructure.market.brapi import BrapiQuotes
from infrastructure.market.cache import CachedQuotes
from infrastructure.market.limits import ValuationLimits
from main import create_app
from presentation.http.dependencies import get_current_user


def test_validation_error_matches_documented_schema(security_settings):
    app = create_app(
        Settings(_env_file=None, DB_PASSWORD="synthetic", DB_PORT=1),
        security_settings,
        MarketSettings(_env_file=None),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/register", json={})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "invalid_request"
        assert set(body) == {"code", "message", "details"}
        schema = client.get("/openapi.json").json()
    documented = schema["paths"]["/api/v1/auth/register"]["post"]["responses"]["422"]
    assert documented["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    fields = schema["components"]["schemas"]["ErrorResponse"]["properties"]
    assert set(body) <= fields.keys()
    assert fields["code"]["type"] == fields["message"]["type"] == "string"


def test_protected_routes_document_auth_and_persistence_errors():
    schema = create_app().openapi()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if path.startswith("/api/v1/"):
                assert "503" in operation["responses"], (path, method)
                assert operation["responses"]["503"]["headers"]["X-Request-ID"][
                    "schema"
                ] == {"type": "string", "pattern": "^[0-9a-f]{32}$"}
            if path.startswith(("/api/v1/assets", "/api/v1/portfolio")):
                assert operation["security"] == [{"HTTPBearer": []}]
                assert "401" in operation["responses"], (path, method)
    failure = schema["paths"]["/api/v1/portfolio/valuation"]["get"]["responses"]["503"]
    assert "Retry-After" in failure["headers"]
    assert failure["headers"]["Retry-After"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 3600,
    }
    quote = schema["components"]["schemas"]["QuoteFailureResponse"]
    assert {"tickers", "reason", "code", "message"} <= set(quote["required"])


def test_valuation_rate_limit_header_is_bounded_without_partial_totals():
    seconds = [1000.0]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(429, headers={"Retry-After": "999999999"})

    transaction = Transaction(
        Asset("Synthetic", "TEST3", AssetType.STOCK),
        Decimal(1),
        Decimal(30),
        TransactionType.BUY,
        date(2026, 1, 1),
    )

    @contextmanager
    def uow_factory():
        yield SimpleNamespace(
            transactions=SimpleNamespace(list_for_user=lambda user_id: [transaction])
        )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: User(
        "synthetic@example.test", "synthetic-hash"
    )
    app.state.uow_factory = uow_factory
    app.state.valuation_limits = ValuationLimits()
    app.state.max_valuation_tickers = 20
    with httpx2.Client(transport=httpx2.MockTransport(handle)) as transport:
        app.state.quotes = CachedQuotes(
            BrapiQuotes(
                transport,
                clock=lambda: seconds[0],
                now=lambda: datetime(2026, 11, 8, tzinfo=UTC),
            ),
            clock=lambda: seconds[0],
        )
        client = TestClient(app)
        for elapsed, expected in [(0, "3600"), (3599.5, "1")]:
            seconds[0] = 1000 + elapsed
            response = client.get("/api/v1/portfolio/valuation")
            assert response.status_code == 503
            assert response.headers["Retry-After"] == expected
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json() == {
                "code": "quotes_unavailable",
                "message": "Unable to complete portfolio valuation",
                "tickers": ["TEST3"],
                "reason": "rate_limited",
            }
        assert len(calls) == 1
