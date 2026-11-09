"""Posições a custo sobre PostgreSQL real, sem mock de cálculo/persistência."""

from datetime import date
from decimal import Decimal
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import domain.portfolio.transaction as transaction_module
from application.portfolio import load_portfolio
from application.types import User
from domain.assets import Asset
from domain.enums import AssetType
from infrastructure.config import (
    MarketSettings,
    PortfolioSettings,
    SecuritySettings,
    Settings,
)
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from main import create_app

POSITIONS = "/api/v1/portfolio/positions"
HISTORY = "/api/v1/portfolio/transactions"


@pytest.fixture
def positions_setup(session_factory, monkeypatch):
    """Identidades/catálogo reais e JWT assinado; nenhum banco da aplicação."""
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
    app = create_app(
        Settings(
            _env_file=None, DB_HOST="127.0.0.1", DB_PORT=1, DB_PASSWORD="synthetic"
        ),
        SecuritySettings(
            _env_file=None,
            JWT_SECRET_KEY="synthetic-positions-key-at-least-32-characters",
        ),
        MarketSettings(_env_file=None),
        portfolio_settings=PortfolioSettings(_env_file=None),
    )
    with TestClient(app) as client:
        app.state.uow_factory = factory
        headers = [
            {"Authorization": f"Bearer {app.state.tokens.issue(user.id)}"}
            for user in users
        ]
        yield client, users, headers, factory
        assert session_factory.kw["bind"].pool.checkedout() == 0


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


def read_positions(client, headers, **kwargs):
    response = client.get(POSITIONS, headers=headers, **kwargs)
    assert response.status_code == 200, response.json()
    body = response.json()
    assert set(body) == {"positions", "total_cost"}
    assert isinstance(body["total_cost"], str)
    for position in body["positions"]:
        assert set(position) == {
            "ticker",
            "name",
            "type",
            "quantity",
            "average_price",
            "cost",
        }
        for field in ("quantity", "average_price", "cost"):
            assert isinstance(position[field], str)
    return body


def test_empty_portfolio_ignores_catalog_and_returns_zero(positions_setup):
    client, _, headers, _ = positions_setup
    assert read_positions(client, headers[0]) == {"positions": [], "total_cost": "0"}


@pytest.mark.parametrize(
    "operations,quantity,average,cost,total",
    [
        (
            [{}, {"price": "40"}, {"type": "sell", "quantity": "5", "price": "42"}],
            "15",
            "35",
            "525",
            "825.75",
        ),
        (
            [
                {},
                {"type": "sell", "quantity": "5", "price": "42"},
                {"quantity": "5", "price": "50"},
                {"type": "sell", "quantity": "2", "price": "1"},
            ],
            "8",
            "40",
            "320",
            "620.75",
        ),
    ],
)
def test_stock_fii_costs_and_constant_query_count(
    positions_setup, session_factory, operations, quantity, average, cost, total
):
    client, _, headers, _ = positions_setup
    for changes in operations:
        write(client, headers[0], **changes)
    write(client, headers[0], ticker="FUND11", quantity="3", price="100.25")
    history = client.get(HISTORY, headers=headers[0]).json()
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        body = read_positions(client, headers[0])
    finally:
        event.remove(engine, "before_cursor_execute", record)
    # One SELECT for authenticated identity, one joined SELECT for all facts.
    assert len(statements) == 2
    assert "JOIN cotaflow.assets" in statements[1]
    assert "LIMIT" not in statements[1]
    assert "FOR UPDATE" not in statements[1]
    assert [item["ticker"] for item in body["positions"]] == ["FUND11", "TEST3"]
    fii, stock = body["positions"]
    assert (fii["name"], fii["type"]) == ("Synthetic FII", "fii")
    assert (stock["name"], stock["type"]) == ("Synthetic stock", "stock")
    assert [Decimal(fii[key]) for key in ("quantity", "average_price", "cost")] == [
        Decimal(3),
        Decimal("100.25"),
        Decimal("300.75"),
    ]
    assert [Decimal(stock[key]) for key in ("quantity", "average_price", "cost")] == [
        Decimal(quantity),
        Decimal(average),
        Decimal(cost),
    ]
    assert Decimal(body["total_cost"]) == Decimal(total)
    assert client.get(HISTORY, headers=headers[0]).json() == history


def test_zero_position_disappears_and_repurchase_starts_new_cost(positions_setup):
    client, _, headers, _ = positions_setup
    first = write(client, headers[0])
    sale = write(client, headers[0], type="sell", price="42")
    assert read_positions(client, headers[0]) == {"positions": [], "total_cost": "0"}
    assert client.get(HISTORY, headers=headers[0]).json()["items"] == [first, sale]
    rebuy = write(client, headers[0], quantity="2", price="99")
    partial_sale = write(client, headers[0], quantity="1", price="999", type="sell")
    body = read_positions(client, headers[0])
    assert len(body["positions"]) == 1
    assert [
        Decimal(body["positions"][0][key])
        for key in ("quantity", "average_price", "cost")
    ] == [Decimal(1), Decimal(99), Decimal(99)]
    assert Decimal(body["total_cost"]) == Decimal(99)
    assert client.get(HISTORY, headers=headers[0]).json()["items"] == [
        first,
        sale,
        rebuy,
        partial_sale,
    ]


def test_retroactive_operations_and_same_day_sequence(positions_setup):
    client, _, headers, _ = positions_setup
    write(client, headers[0], date="2026-01-03", price="40")
    write(client, headers[0], date="2026-01-01", price="30")
    write(client, headers[0], date="2026-01-03", type="sell", quantity="5", price="42")
    body = read_positions(client, headers[0])
    stock = body["positions"][0]
    assert [Decimal(stock[key]) for key in ("quantity", "average_price", "cost")] == [
        Decimal(15),
        Decimal(35),
        Decimal(525),
    ]
    assert Decimal(body["total_cost"]) == Decimal(525)


def test_history_pagination_cannot_truncate_positions(positions_setup, session_factory):
    client, _, headers, _ = positions_setup
    seconds = [1000.0]
    client.app.state.transaction_limits._clock = lambda: seconds[0]
    for index in range(50):
        # Build the long history across valid quota windows, without sleeping.
        seconds[0] = 1000.0 + 60 * (index // 30)
        write(client, headers[0], quantity="1", price="2")
    write(client, headers[0], type="sell", quantity="49", price="100")
    assert len(client.get(HISTORY, headers=headers[0]).json()["items"]) == 50
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        body = read_positions(client, headers[0], params={"limit": 1, "offset": 50})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 2
    assert Decimal(body["positions"][0]["quantity"]) == Decimal(1)
    assert Decimal(body["total_cost"]) == Decimal(2)


def test_positions_belong_to_signed_identity_only(positions_setup):
    client, users, headers, _ = positions_setup
    write(client, headers[0])
    assert read_positions(client, headers[1]) == {"positions": [], "total_cost": "0"}
    write(client, headers[1], ticker="FUND11", quantity="2", price="99")
    for index, total, ticker in [(0, "300", "TEST3"), (1, "198", "FUND11")]:
        body = read_positions(
            client,
            headers[index] | {"X-User-Id": str(users[1 - index].id)},
            params={"user_id": str(users[1 - index].id)},
        )
        assert [item["ticker"] for item in body["positions"]] == [ticker]
        assert Decimal(body["total_cost"]) == Decimal(total)


def test_loaded_snapshot_releases_connection_and_survives_later_writes(
    positions_setup, session_factory
):
    client, users, headers, factory = positions_setup
    write(client, headers[0])
    portfolio = load_portfolio(users[0].id, factory)
    assert session_factory.kw["bind"].pool.checkedout() == 0
    write(client, headers[0], quantity="2", price="60")
    # Domain facts remain usable after the reading session closes.
    assert portfolio.positions["TEST3"].quantity == Decimal(10)
    assert portfolio.get_total_cost() == Decimal(300)
    assert len(portfolio.transaction_list) == 1
    assert Decimal(read_positions(client, headers[0])["total_cost"]) == Decimal(420)


@pytest.mark.parametrize(
    "quantity,price,cost",
    [
        ("0.00000001", "0.00000001", "0.0000000000000001"),
        (
            "9999999999.99999999",
            "9999999999.99999999",
            "99999999999999999800.0000000000000001",
        ),
    ],
)
def test_derived_cost_is_not_limited_to_transaction_scale(
    positions_setup, quantity, price, cost
):
    client, _, headers, _ = positions_setup
    write(client, headers[0], quantity=quantity, price=price)
    body = read_positions(client, headers[0])
    stock = body["positions"][0]
    assert Decimal(stock["quantity"]) == Decimal(quantity)
    assert Decimal(stock["average_price"]) == Decimal(price)
    assert Decimal(stock["cost"]) == Decimal(cost)
    assert Decimal(body["total_cost"]) == Decimal(cost)
    assert "e" not in body["total_cost"].lower()


def test_read_failure_returns_safe_503_without_partial_total(
    positions_setup, session_factory
):
    client, _, headers, _ = positions_setup
    write(client, headers[0])
    engine = session_factory.kw["bind"]
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE cotaflow.transactions RENAME TO unavailable_transactions")
        )
    response = client.get(POSITIONS, headers=headers[0])
    assert response.status_code == 503
    assert response.json()["code"] == "persistence_unavailable"
    assert "total_cost" not in response.json()
    assert "SELECT" not in response.text
    assert "unavailable_transactions" not in response.text
    assert engine.pool.checkedout() == 0
