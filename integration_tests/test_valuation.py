"""HTTP/domínio/PostgreSQL reais; apenas transporte da brapi é controlado."""

import json
from datetime import date
from decimal import Decimal
from functools import partial

import httpx2
import pytest
from fastapi.testclient import TestClient

import domain.portfolio.transaction as transaction_module
import main
from application.types import User
from domain.assets import Asset
from domain.enums import AssetType
from infrastructure.config import MarketSettings, SecuritySettings, Settings
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

VALUATION = "/api/v1/portfolio/valuation"
HISTORY = "/api/v1/portfolio/transactions"


@pytest.fixture
def valuation_setup(session_factory, monkeypatch):
    monkeypatch.setattr(transaction_module, "reference_date", lambda: date(2026, 11, 8))
    factory = partial(SqlAlchemyUnitOfWork, session_factory)
    users = [
        User(f"{name}@example.test", "synthetic-hash") for name in ("alice", "bob")
    ]
    with factory() as uow:
        for user in users:
            uow.users.add(user)
        uow.assets.add(Asset("Synthetic stock", "TEST3", AssetType.STOCK))
        uow.assets.add(Asset("Synthetic FII", "FUND11", AssetType.FII))
        uow.commit()
    engine = session_factory.kw["bind"]
    calls = []
    failures = {}
    prices = {"TEST3": 42, "FUND11": 110}
    hook = [None]

    def handle(request):
        # A dependência de identidade E a leitura financeira já devolveram conexão.
        assert engine.pool.checkedout() == 0
        assert request.headers["Authorization"] == "Bearer synthetic-brapi-token"
        assert not request.url.query
        ticker = request.url.path.rsplit("/", 1)[-1]
        calls.append(ticker)
        if hook[0] is not None:
            hook[0]()
        if ticker in failures:
            status, content, headers = failures[ticker]
            if status == "timeout":
                raise httpx2.ReadTimeout(
                    "synthetic-private-provider-body", request=request
                )
            return httpx2.Response(status, content=content, headers=headers)
        return httpx2.Response(
            200,
            content=json.dumps(
                {
                    "results": [
                        {
                            "symbol": ticker,
                            "currency": "BRL",
                            "regularMarketPrice": prices[ticker],
                            "regularMarketTime": "2026-11-07T21:32:40.000Z",
                        }
                    ]
                }
            ).encode(),
        )

    real_client = httpx2.Client
    clients = []

    def create_market_client(**kwargs):
        client = real_client(transport=httpx2.MockTransport(handle), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(main.httpx2, "Client", create_market_client)
    app = main.create_app(
        Settings(
            _env_file=None, DB_HOST="127.0.0.1", DB_PORT=1, DB_PASSWORD="synthetic"
        ),
        SecuritySettings(
            _env_file=None,
            JWT_SECRET_KEY="synthetic-valuation-key-at-least-32-characters",
        ),
        MarketSettings(_env_file=None, BRAPI_TOKEN="synthetic-brapi-token"),
    )
    with TestClient(app) as client:
        app.state.uow_factory = factory
        headers = [
            {"Authorization": f"Bearer {app.state.tokens.issue(user.id)}"}
            for user in users
        ]
        yield client, headers, calls, failures, hook, users
        assert engine.pool.checkedout() == 0
        assert len(clients) == 1
        assert not clients[0].is_closed
    assert clients[0].is_closed
    assert not hasattr(app.state, "quotes")


def write(client, headers, **changes):
    response = client.post(
        HISTORY,
        headers=headers,
        json={
            "ticker": "TEST3",
            "quantity": "10",
            "price": "30",
            "type": "buy",
            "date": "2026-01-01",
        }
        | changes,
    )
    assert response.status_code == 201, response.json()
    return response.json()


def test_empty_and_zeroed_portfolio_never_call_market(valuation_setup):
    client, headers, calls, _, _, _ = valuation_setup
    empty = {
        "currency": "BRL",
        "positions": [],
        "total_cost": "0",
        "total_market_value": "0",
        "total_unrealized_pnl": "0",
        "unrealized_return": None,
    }
    assert client.get(VALUATION, headers=headers[0]).json() == empty
    write(client, headers[0])
    write(client, headers[0], type="sell")
    assert client.get(VALUATION, headers=headers[0]).json() == empty
    assert calls == []


def test_stock_acceptance_values_complete_snapshot_and_warm_cache(valuation_setup):
    client, headers, calls, _, _, _ = valuation_setup
    write(client, headers[0])
    write(client, headers[0], price="40")
    write(client, headers[0], quantity="5", price="999", type="sell")
    history = client.get(HISTORY, headers=headers[0]).json()
    response = client.get(VALUATION, headers=headers[0])
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "BRL"
    assert [
        Decimal(body[key])
        for key in (
            "total_cost",
            "total_market_value",
            "total_unrealized_pnl",
            "unrealized_return",
        )
    ] == [Decimal(525), Decimal(630), Decimal(105), Decimal("0.2")]
    stock = body["positions"][0]
    assert [
        Decimal(stock[key])
        for key in (
            "quantity",
            "average_price",
            "cost",
            "market_value",
            "unrealized_pnl",
        )
    ] == [Decimal(15), Decimal(35), Decimal(525), Decimal(630), Decimal(105)]
    assert stock["quote"]["price"] == "42"
    assert stock["quote"]["currency"] == "BRL"
    assert stock["quote"]["source"] == "brapi"
    assert stock["quote"]["quoted_at"] == "2026-11-07T21:32:40Z"
    assert stock["quote"]["fetched_at"].endswith("Z")
    assert stock["quote"]["cached"] is False
    warm = client.get(VALUATION, headers=headers[0]).json()
    assert warm["positions"][0]["quote"] == stock["quote"] | {"cached": True}
    assert calls == ["TEST3"]
    assert client.get(HISTORY, headers=headers[0]).json() == history


def test_stock_fii_and_new_write_during_http_use_original_portfolio_snapshot(
    valuation_setup,
):
    client, headers, calls, _, hook, _ = valuation_setup
    write(client, headers[0])
    write(client, headers[0], ticker="FUND11", quantity="3", price="100")

    def write_during_fetch():
        hook[0] = None
        write(client, headers[0], quantity="2", price="60")

    hook[0] = write_during_fetch
    response = client.get(VALUATION, headers=headers[0])
    assert response.status_code == 200
    body = response.json()
    assert [position["ticker"] for position in body["positions"]] == ["FUND11", "TEST3"]
    assert [
        Decimal(body[key])
        for key in (
            "total_cost",
            "total_market_value",
            "total_unrealized_pnl",
            "unrealized_return",
        )
    ] == [Decimal(600), Decimal(750), Decimal(150), Decimal("0.25")]
    second = client.get(VALUATION, headers=headers[0]).json()
    assert [
        Decimal(second[key])
        for key in ("total_cost", "total_market_value", "total_unrealized_pnl")
    ] == [Decimal(720), Decimal(834), Decimal(114)]
    assert calls == ["FUND11", "TEST3"]


@pytest.mark.parametrize(
    "status,content,response_headers,reason",
    [
        (401, b"synthetic-private-provider-body", {}, "authentication"),
        (
            429,
            b"synthetic-private-provider-body",
            {"Retry-After": "90"},
            "rate_limited",
        ),
        (503, b"synthetic-private-provider-body", {}, "unavailable"),
        ("timeout", b"", {}, "timeout"),
        (200, b'{"results": []}', {}, "invalid_response"),
    ],
)
def test_provider_failure_has_no_partial_total_and_preserves_history_and_cost(
    valuation_setup, status, content, response_headers, reason
):
    client, headers, calls, failures, _, _ = valuation_setup
    write(client, headers[0])
    write(client, headers[0], ticker="FUND11", quantity="3", price="100")
    history = client.get(HISTORY, headers=headers[0]).json()
    failures["TEST3"] = (status, content, response_headers)
    response = client.get(VALUATION, headers=headers[0])
    assert response.status_code == 503
    assert response.json() == {
        "code": "quotes_unavailable",
        "message": "Unable to complete portfolio valuation",
        "tickers": ["TEST3"],
        "reason": reason,
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert "total_market_value" not in response.json()
    assert "synthetic-private" not in response.text
    assert "synthetic-brapi-token" not in response.text
    if status == 429:
        assert response.headers["Retry-After"] == "90"
    assert calls == ["FUND11", "TEST3"]
    assert client.get(HISTORY, headers=headers[0]).json() == history
    assert Decimal(
        client.get("/api/v1/portfolio/positions", headers=headers[0]).json()[
            "total_cost"
        ]
    ) == Decimal(600)
    # Falha externa não bloqueia nova operação nem a sua leitura.
    write(client, headers[0], quantity="1")
    assert Decimal(
        client.get("/api/v1/portfolio/positions", headers=headers[0]).json()[
            "total_cost"
        ]
    ) == Decimal(630)


def test_signed_user_cannot_value_another_users_positions(valuation_setup):
    client, headers, calls, _, _, users = valuation_setup
    write(client, headers[0])
    empty = client.get(
        VALUATION,
        headers=headers[1] | {"X-User-Id": str(users[0].id)},
        params={"user_id": str(users[0].id)},
    )
    assert empty.status_code == 200
    assert empty.json()["positions"] == []
    assert calls == []
    write(client, headers[1], ticker="FUND11", quantity="2", price="100")
    body = client.get(VALUATION, headers=headers[1]).json()
    assert [item["ticker"] for item in body["positions"]] == ["FUND11"]
    assert Decimal(body["total_market_value"]) == Decimal(220)
    assert calls == ["FUND11"]


def test_incomplete_provider_contract_cannot_escape_as_complete_valuation(
    valuation_setup,
):
    client, headers, calls, _, _, _ = valuation_setup
    write(client, headers[0])

    class IncompleteProvider:
        def get_quotes(self, tickers):
            assert tickers == {"TEST3"}
            return {}

    client.app.state.quotes = IncompleteProvider()
    response = client.get(VALUATION, headers=headers[0])
    assert response.status_code == 503
    assert response.json()["tickers"] == ["TEST3"]
    assert response.json()["code"] == "quotes_unavailable"
    assert "total_market_value" not in response.json()
    assert calls == []
