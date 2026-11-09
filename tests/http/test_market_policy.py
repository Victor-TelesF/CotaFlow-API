"""Políticas de avaliação na rota real, sem rede nem persistência simulada como real."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient

from application.types import User
from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.portfolio import Transaction
from infrastructure.config import MarketSettings, Settings
from infrastructure.market.brapi import BrapiQuotes
from infrastructure.market.cache import CachedQuotes
from main import create_app
from presentation.http.dependencies import get_current_user
from tests.infrastructure.test_market import body

PATH = "/api/v1/portfolio/valuation"


def test_warm_evaluations_consume_user_budget_before_loading_portfolio(
    security_settings,
):
    seconds = [1000.0]
    calls, reads, open_units = [], [], []
    alice, bob = (
        User("alice@example.test", "synthetic"),
        User("bob@example.test", "synthetic"),
    )
    current = [alice]
    transaction = Transaction(
        Asset("Synthetic", "TEST3", AssetType.STOCK),
        Decimal(2),
        Decimal(30),
        TransactionType.BUY,
        date(2026, 1, 1),
    )

    @contextmanager
    def uow_factory():
        reads.append(current[0].id)
        open_units.append(True)
        try:
            yield SimpleNamespace(
                transactions=SimpleNamespace(list_for_user=lambda user: [transaction])
            )
        finally:
            open_units.pop()

    def handle(request):
        assert open_units == []
        calls.append(request)
        return httpx2.Response(200, content=body())

    app = create_app(
        Settings(_env_file=None, DB_PORT=1, DB_PASSWORD="synthetic"),
        security_settings,
        MarketSettings(_env_file=None, MARKET_VALUATIONS_PER_MINUTE=2),
    )
    app.dependency_overrides[get_current_user] = lambda: current[0]
    with (
        httpx2.Client(transport=httpx2.MockTransport(handle)) as transport,
        TestClient(app) as client,
    ):
        clock = lambda: seconds[0]
        app.state.uow_factory = uow_factory
        app.state.quotes = CachedQuotes(
            BrapiQuotes(transport, clock=clock), clock=clock
        )
        app.state.valuation_limits._clock = clock
        for _ in range(2):
            response = client.get(PATH)
            assert response.status_code == 200
            assert response.json()["total_market_value"] == "84"
        rejected = client.get(PATH)
        assert rejected.status_code == 429
        assert rejected.json() == {
            "code": "valuation_rate_limited",
            "message": "Valuation budget exhausted",
        }
        assert rejected.headers["Retry-After"] == "60"
        assert rejected.headers["Cache-Control"] == "no-store"
        assert len(reads) == 2 and len(calls) == 1
        current[0] = bob
        assert client.get(PATH).status_code == 200
        current[0] = alice
        seconds[0] += 60
        assert client.get(PATH).status_code == 200
        assert len(calls) == 1
        assert len(reads) == 4
    schema = app.openapi()
    assert "post" not in schema["paths"]["/api/v1/assets"]
    assert "429" in schema["paths"][PATH]["get"]["responses"]
    ticker = schema["components"]["schemas"]["TransactionRequest"]["properties"][
        "ticker"
    ]
    assert ticker["maxLength"] == 20
    assert ticker["pattern"] == "^[A-Z0-9]{1,20}$"


@pytest.mark.parametrize("ticker", ["TEST3/OTHER3", "a" * 21, " " * 65 + "A", "é3"])
def test_invalid_ticker_is_rejected_before_portfolio_database(ticker):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: User(
        "user@example.test", "synthetic"
    )

    def forbidden_database():
        pytest.fail("Invalid ticker reached persistence")

    app.state.uow_factory = forbidden_database
    response = TestClient(app).post(
        "/api/v1/portfolio/transactions",
        json={
            "ticker": ticker,
            "quantity": "1",
            "price": "30",
            "type": "buy",
            "date": "2026-01-01",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["details"][0]["location"] == ["body", "ticker"]


def test_public_catalog_has_no_write_route_even_with_authenticated_user():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: User(
        "user@example.test", "synthetic"
    )
    response = TestClient(app).post(
        "/api/v1/assets", json={"ticker": "TEST3", "name": "Synthetic", "type": "stock"}
    )
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert "post" not in app.openapi()["paths"]["/api/v1/assets"]
