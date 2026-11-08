"""Fluxo público completo; PostgreSQL, hashes/JWT e cálculos reais."""

from datetime import date
from decimal import Decimal
from functools import partial

import httpx2
import pytest
from fastapi.testclient import TestClient

import domain.portfolio.transaction as transaction_module
import main
from infrastructure.config import MarketSettings, SecuritySettings, Settings
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

PREFIX = "/api/v1"
TRANSACTIONS = f"{PREFIX}/portfolio/transactions"
POSITIONS = f"{PREFIX}/portfolio/positions"
VALUATION = f"{PREFIX}/portfolio/valuation"


@pytest.mark.parametrize("provider_status", [200, 503])
def test_public_delivery_flow(session_factory, monkeypatch, provider_status):
    """Cadastro → login → catálogo → compra → avaliação → venda → histórico.

    A falha externa não pode apagar operações, bloquear vendas ou produzir
    um total parcial. O outro usuário não pode consumir a carteira do dono.
    Somente o transporte HTTP externo é substituído.
    """
    monkeypatch.setattr(transaction_module, "reference_date", lambda: date(2026, 11, 8))
    engine = session_factory.kw["bind"]
    calls = []

    def handle(request):
        assert engine.pool.checkedout() == 0
        assert request.headers["Authorization"] == "Bearer synthetic-delivery-token"
        assert str(request.url) == "https://brapi.dev/api/quote/TEST3"
        calls.append(request.url.path)
        if provider_status == 503:
            return httpx2.Response(503, content=b"synthetic-private-provider-body")
        return httpx2.Response(
            200,
            content=(
                b'{"results":[{"symbol":"TEST3","currency":"BRL",'
                b'"regularMarketPrice":42,"regularMarketTime":"2026-11-07T21:32:40Z"}]}'
            ),
        )

    real_client = httpx2.Client
    monkeypatch.setattr(
        main.httpx2,
        "Client",
        lambda **kwargs: real_client(transport=httpx2.MockTransport(handle), **kwargs),
    )
    app = main.create_app(
        Settings(_env_file=None, DB_PASSWORD="synthetic", DB_PORT=1),
        SecuritySettings(
            _env_file=None,
            JWT_SECRET_KEY="synthetic-delivery-key-at-least-32-characters",
        ),
        MarketSettings(_env_file=None, BRAPI_TOKEN="synthetic-delivery-token"),
    )
    with TestClient(app) as client:
        app.state.uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
        headers = []
        users = []
        for name in ("alice", "bob"):
            credentials = {
                "email": f"{name}@example.com",
                "password": "synthetic-delivery-password",
            }
            registered = client.post(f"{PREFIX}/auth/register", json=credentials)
            assert registered.status_code == 201
            user = registered.json()
            assert set(user) == {"id", "email"}
            users.append(user)
            logged_in = client.post(f"{PREFIX}/auth/login", json=credentials)
            assert logged_in.status_code == 200
            assert logged_in.headers["Cache-Control"] == "no-store"
            headers.append(
                {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
            )

        owner, outsider = headers
        assert users[0]["id"] != users[1]["id"]
        assert client.get(POSITIONS).status_code == 401
        asset = client.post(
            f"{PREFIX}/assets",
            headers=owner,
            json={"ticker": " test3 ", "name": "Synthetic stock", "type": "stock"},
        )
        assert asset.status_code == 201
        assert asset.json()["ticker"] == "TEST3"
        # Catálogo é compartilhado, histórico financeiro não.
        assert client.get(f"{PREFIX}/assets", headers=outsider).json()["items"] == [
            asset.json()
        ]

        def write(quantity, price, kind="buy"):
            return client.post(
                TRANSACTIONS,
                headers=owner,
                json={
                    "ticker": "TEST3",
                    "quantity": quantity,
                    "price": price,
                    "type": kind,
                    "date": "2026-01-02",
                },
            )

        buys = [write("10", "30"), write("10", "40")]
        assert [response.status_code for response in buys] == [201, 201]
        before = client.get(TRANSACTIONS, headers=owner).json()
        position = client.get(POSITIONS, headers=owner).json()
        assert Decimal(position["total_cost"]) == Decimal(700)
        assert Decimal(position["positions"][0]["quantity"]) == Decimal(20)
        assert Decimal(position["positions"][0]["average_price"]) == Decimal(35)
        assert calls == []

        spoofed = outsider | {"X-User-Id": users[0]["id"]}
        assert (
            client.get(
                TRANSACTIONS, headers=spoofed, params={"user_id": users[0]["id"]}
            ).json()["items"]
            == []
        )
        assert client.get(POSITIONS, headers=spoofed).json() == {
            "positions": [],
            "total_cost": "0",
        }
        assert client.get(VALUATION, headers=spoofed).json()["positions"] == []
        assert calls == []

        valued = client.get(VALUATION, headers=owner)
        if provider_status == 200:
            assert valued.status_code == 200
            body = valued.json()
            assert [
                Decimal(body[key])
                for key in (
                    "total_market_value",
                    "total_unrealized_pnl",
                    "unrealized_return",
                )
            ] == [Decimal(840), Decimal(140), Decimal("0.2")]
        else:
            assert valued.status_code == 503
            assert valued.json() == {
                "code": "quotes_unavailable",
                "message": "Unable to complete portfolio valuation",
                "tickers": ["TEST3"],
                "reason": "unavailable",
            }
            assert "total_market_value" not in valued.json()
            assert "synthetic-private" not in valued.text
        assert len(calls) == 1
        assert client.get(TRANSACTIONS, headers=owner).json() == before

        rejected = write("21", "999", "sell")
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "insufficient_balance"
        assert client.get(TRANSACTIONS, headers=owner).json() == before
        assert client.get(POSITIONS, headers=owner).json() == position

        sale = write("5", "999", "sell")
        assert sale.status_code == 201
        after = client.get(POSITIONS, headers=owner).json()
        assert Decimal(after["total_cost"]) == Decimal(525)
        assert Decimal(after["positions"][0]["quantity"]) == Decimal(15)
        assert Decimal(after["positions"][0]["average_price"]) == Decimal(35)
        history = client.get(TRANSACTIONS, headers=owner).json()["items"]
        assert [item["id"] for item in history] == [
            *(response.json()["id"] for response in buys),
            sale.json()["id"],
        ]
        assert [Decimal(item["price"]) for item in history] == [
            Decimal(30),
            Decimal(40),
            Decimal(999),
        ]
        if provider_status == 200:
            warm = client.get(VALUATION, headers=owner).json()
            assert [
                Decimal(warm[key])
                for key in (
                    "total_market_value",
                    "total_unrealized_pnl",
                    "unrealized_return",
                )
            ] == [Decimal(630), Decimal(105), Decimal("0.2")]
            assert warm["positions"][0]["quote"]["cached"] is True
        assert len(calls) == 1
        assert engine.pool.checkedout() == 0
