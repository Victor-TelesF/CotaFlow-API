"""Etapa 4 sobre PostgreSQL real: histórico, rollback, isolamento e corridas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from functools import partial
from threading import Barrier, Event, Lock
from time import monotonic
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

import domain.portfolio.transaction as transaction_module
from application.exceptions import InvalidToken
from application.portfolio import register_transaction
from application.types import User
from domain.enums import TransactionType
from domain.portfolio import Portfolio, Transaction
from infrastructure.config import MarketSettings, SecuritySettings, Settings
from infrastructure.database.models import AssetModel
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from main import create_app

ASSETS = "/api/v1/assets"
HISTORY = "/api/v1/portfolio/transactions"


@pytest.fixture(autouse=True)
def controlled_date(monkeypatch):
    monkeypatch.setattr(transaction_module, "reference_date", lambda: date(2026, 11, 8))


@pytest.fixture
def portfolio_client(session_factory):
    app = create_app(
        Settings(
            _env_file=None,
            DB_HOST="127.0.0.1",
            DB_PORT=1,
            DB_PASSWORD="synthetic-password",
        ),
        SecuritySettings(
            _env_file=None,
            JWT_SECRET_KEY="synthetic-portfolio-key-at-least-32-characters",
        ),
        MarketSettings(_env_file=None),
    )
    with TestClient(app) as client:
        app.state.uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
        yield client
        assert session_factory.kw["bind"].pool.checkedout() == 0


@pytest.fixture
def owners(portfolio_client):
    """Usa cadastro, login, JWT e resolução de identidade reais, sem mock."""
    result = []
    for name in ("alice", "bob"):
        body = {"email": f"{name}@example.com", "password": "synthetic-password"}
        response = portfolio_client.post("/api/v1/auth/register", json=body)
        assert response.status_code == 201
        login = portfolio_client.post("/api/v1/auth/login", json=body)
        assert login.status_code == 200
        result.append(
            (
                UUID(response.json()["id"]),
                {"Authorization": f"Bearer {login.json()['access_token']}"},
            )
        )
    return result


@pytest.fixture
def catalog(portfolio_client, owners):
    response = portfolio_client.post(
        ASSETS,
        headers=owners[0][1],
        json={"ticker": " test3 ", "name": " Synthetic stock ", "type": "stock"},
    )
    assert response.status_code == 201
    return response.json()


def operation(client, headers, **changes):
    body = {
        "ticker": " test3 ",
        "quantity": "10",
        "price": "30",
        "type": "buy",
        "date": "2026-01-02",
    }
    return client.post(HISTORY, headers=headers, json=body | changes)


def stored_history(factory, owner):
    with SqlAlchemyUnitOfWork(factory) as uow:
        return uow.transactions.list_for_user(owner)


def test_catalog_normalization_pagination_and_shared_visibility(
    portfolio_client, owners, catalog, session_factory
):
    assert catalog == {"ticker": "TEST3", "name": "Synthetic stock", "type": "stock"}
    response = portfolio_client.post(
        ASSETS,
        headers=owners[1][1],
        json={"ticker": "FUND11", "name": "Synthetic FII", "type": "fii"},
    )
    assert response.status_code == 201
    assert response.json()["type"] == "fii"
    for _, headers in owners:
        first = portfolio_client.get(ASSETS, headers=headers, params={"limit": 1})
        assert first.status_code == 200
        assert first.json() == {"items": [response.json()], "limit": 1, "offset": 0}
        second = portfolio_client.get(
            ASSETS, headers=headers, params={"limit": 1, "offset": 1}
        )
        assert second.json()["items"] == [catalog]
        assert (
            portfolio_client.get(ASSETS, headers=headers, params={"offset": 2}).json()[
                "items"
            ]
            == []
        )
    duplicate = portfolio_client.post(
        ASSETS,
        headers=owners[0][1],
        json={"ticker": "TeSt3", "name": "Changed", "type": "fii"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "asset_already_registered"
    assert "assets_pkey" not in duplicate.text
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssetModel)) == 2
        assert session.get(AssetModel, "TEST3").name == "Synthetic stock"
        assert session.get(AssetModel, "TEST3").asset_type == "stock"


@pytest.mark.parametrize(
    "changes",
    [{"ticker": " "}, {"name": " "}, {"type": "crypto"}, {"user_id": "forged"}],
)
def test_bad_catalog_data_is_rejected_without_rows(
    portfolio_client, owners, session_factory, changes
):
    response = portfolio_client.post(
        ASSETS,
        headers=owners[0][1],
        json={"ticker": "TEST3", "name": "Valid", "type": "stock"} | changes,
    )
    assert response.status_code == 422
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssetModel)) == 0


def test_buys_sales_preserve_facts_and_paginate_by_date_sequence(
    portfolio_client, owners, catalog, session_factory
):
    owner, headers = owners[0]
    # Insert a later date first; same-day new rows must follow persisted rows.
    first = operation(
        portfolio_client, headers, quantity="10", price="40", date="2026-01-03"
    )
    earlier = operation(
        portfolio_client, headers, quantity="10", price="30", date="2026-01-01"
    )
    sale = operation(
        portfolio_client,
        headers,
        type="sell",
        quantity="5",
        price="42",
        date="2026-01-03",
    )
    for response in (first, earlier, sale):
        assert response.status_code == 201
        assert response.json()["ticker"] == "TEST3"
        assert isinstance(response.json()["quantity"], str)
        assert isinstance(response.json()["price"], str)
        assert "user_id" not in response.json()
    expected = [earlier.json(), first.json(), sale.json()]
    for offset, item in enumerate(expected):
        response = portfolio_client.get(
            HISTORY, headers=headers, params={"limit": 1, "offset": offset}
        )
        assert response.status_code == 200
        assert response.json() == {"items": [item], "limit": 1, "offset": offset}
    history = stored_history(session_factory, owner)
    assert [str(tx.id_transaction) for tx in history] == [
        item["id"] for item in expected
    ]
    assert [tx.price for tx in history] == [Decimal(30), Decimal(40), Decimal(42)]
    assert [tx.transaction_date for tx in history] == [
        date(2026, 1, 1),
        date(2026, 1, 3),
        date(2026, 1, 3),
    ]
    position = Portfolio(history).positions["TEST3"]
    assert position.quantity == Decimal(15)
    assert position.average_price == Decimal(35)
    assert position.cost == Decimal(525)


def test_insufficient_sale_keeps_history_and_position_unchanged(
    portfolio_client, owners, catalog, session_factory
):
    owner, headers = owners[0]
    buy = operation(portfolio_client, headers)
    assert buy.status_code == 201
    response = operation(portfolio_client, headers, type="sell", quantity="11")
    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_balance"
    history = stored_history(session_factory, owner)
    assert [str(tx.id_transaction) for tx in history] == [buy.json()["id"]]
    position = Portfolio(history).positions["TEST3"]
    assert position.quantity == Decimal(10)
    assert position.cost == Decimal(300)


def test_retroactive_sale_checks_closed_cycle_even_with_large_current_balance(
    portfolio_client, owners, catalog, session_factory
):
    owner, headers = owners[0]
    for changes in [
        {"date": "2026-01-01"},
        {"date": "2026-01-03", "type": "sell"},
        {"date": "2026-01-04", "quantity": "100", "price": "50"},
    ]:
        assert operation(portfolio_client, headers, **changes).status_code == 201
    before = portfolio_client.get(HISTORY, headers=headers).json()
    response = operation(
        portfolio_client, headers, date="2026-01-02", type="sell", quantity="1"
    )
    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_balance"
    assert portfolio_client.get(HISTORY, headers=headers).json() == before
    position = Portfolio(stored_history(session_factory, owner)).positions["TEST3"]
    assert position.quantity == Decimal(100)
    assert position.cost == Decimal(5000)


def test_same_day_zero_rebuy_and_sale_before_first_purchase(
    portfolio_client, owners, catalog, session_factory
):
    owner, headers = owners[0]
    early = operation(
        portfolio_client, headers, type="sell", date="2026-01-01", quantity="1"
    )
    assert early.status_code == 409
    assert early.json()["code"] == "insufficient_balance"
    assert stored_history(session_factory, owner) == []
    assert operation(portfolio_client, headers).status_code == 201
    assert operation(portfolio_client, headers, type="sell").status_code == 201
    history = stored_history(session_factory, owner)
    assert len(history) == 2
    assert Portfolio(history).positions == {}
    assert (
        operation(portfolio_client, headers, quantity="2", price="80").status_code
        == 201
    )
    history = stored_history(session_factory, owner)
    assert len(history) == 3
    assert [tx.transaction_sequence for tx in history] == sorted(
        tx.transaction_sequence for tx in history
    )
    assert Portfolio(history).positions["TEST3"].cost == Decimal(160)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("quantity", "0", "invalid_value"),
        ("price", "-1", "invalid_value"),
        ("quantity", "0.000000001", "invalid_value"),
        ("price", "10000000000", "invalid_value"),
        ("quantity", "NaN", "invalid_request"),
        ("price", "Infinity", "invalid_request"),
        ("quantity", 0.123456789, "invalid_request"),
        ("price", True, "invalid_request"),
        ("date", "2026-11-09", "invalid_value"),
    ],
)
def test_invalid_operation_is_specific_rejection_with_no_write(
    portfolio_client, owners, catalog, session_factory, field, value, code
):
    owner, headers = owners[0]
    response = operation(portfolio_client, headers, **{field: value})
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert stored_history(session_factory, owner) == []


def test_exact_numeric_boundaries_today_and_fii_operations(
    portfolio_client, owners, session_factory
):
    owner, headers = owners[0]
    assert (
        portfolio_client.post(
            ASSETS,
            headers=headers,
            json={"ticker": "FUND11", "name": "Fund", "type": "fii"},
        ).status_code
        == 201
    )
    for quantity, price in [
        ("0.00000001", "9999999999.99999999"),
        ("9999999999.99999999", "0.00000001"),
    ]:
        response = operation(
            portfolio_client,
            headers,
            ticker="fund11",
            quantity=quantity,
            price=price,
            date="2026-11-08",
        )
        assert response.status_code == 201
        assert Decimal(response.json()["quantity"]) == Decimal(quantity)
        assert Decimal(response.json()["price"]) == Decimal(price)
    history = stored_history(session_factory, owner)
    assert history[0].quantity == Decimal("0.00000001")
    assert history[0].price == Decimal("9999999999.99999999")
    assert history[1].quantity == Decimal("9999999999.99999999")
    assert history[1].price == Decimal("0.00000001")


def test_unknown_asset_and_missing_identity_leave_no_transactions(
    portfolio_client, owners, session_factory
):
    owner, headers = owners[0]
    response = operation(portfolio_client, headers)
    assert response.status_code == 404
    assert response.json()["code"] == "asset_not_found"
    assert stored_history(session_factory, owner) == []
    missing = User("missing@example.test", "unused")
    with pytest.raises(InvalidToken):
        register_transaction(
            missing.id,
            "TEST3",
            Decimal(1),
            Decimal(30),
            TransactionType.BUY,
            date(2026, 1, 1),
            partial(SqlAlchemyUnitOfWork, session_factory),
        )


def test_user_cannot_read_or_consume_other_users_balance(
    portfolio_client, owners, catalog, session_factory
):
    (alice, alice_headers), (bob, bob_headers) = owners
    assert operation(portfolio_client, alice_headers).status_code == 201
    forged = bob_headers | {"X-User-Id": str(alice)}
    response = portfolio_client.get(
        HISTORY, headers=forged, params={"user_id": str(alice)}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    sale = operation(portfolio_client, forged, type="sell", quantity="1")
    assert sale.status_code == 409
    assert sale.json()["code"] == "insufficient_balance"
    forged_body = operation(portfolio_client, forged, user_id=str(alice))
    assert forged_body.status_code == 422
    assert forged_body.json()["code"] == "invalid_request"
    assert (
        operation(portfolio_client, forged, quantity="2", price="50").status_code == 201
    )
    assert Portfolio(stored_history(session_factory, alice)).positions[
        "TEST3"
    ].cost == Decimal(300)
    assert Portfolio(stored_history(session_factory, bob)).positions[
        "TEST3"
    ].cost == Decimal(100)
    # Version one exposes no edit/delete path, even to the owner.
    tx_id = stored_history(session_factory, alice)[0].id_transaction
    assert (
        portfolio_client.delete(f"{HISTORY}/{tx_id}", headers=forged).status_code == 404
    )
    assert (
        portfolio_client.patch(
            f"{HISTORY}/{tx_id}", headers=forged, json={"quantity": "99"}
        ).status_code
        == 404
    )
    assert len(stored_history(session_factory, alice)) == 1


def test_financial_validation_uses_history_beyond_http_page(
    portfolio_client, owners, catalog, session_factory
):
    owner, headers = owners[0]
    # First fifty buys supply 50, then one sale consumes all; a truncated read
    # of fifty would incorrectly accept another sale.
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        asset = uow.assets.get("TEST3")

        for _ in range(50):
            uow.transactions.add(
                owner,
                Transaction(
                    asset,
                    Decimal(1),
                    Decimal(30),
                    TransactionType.BUY,
                    date(2026, 1, 1),
                ),
            )
        uow.transactions.add(
            owner,
            Transaction(
                asset, Decimal(50), Decimal(40), TransactionType.SELL, date(2026, 1, 2)
            ),
        )
        uow.commit()
    assert len(portfolio_client.get(HISTORY, headers=headers).json()["items"]) == 50
    response = operation(
        portfolio_client, headers, type="sell", quantity="1", date="2026-01-03"
    )
    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_balance"
    assert len(stored_history(session_factory, owner)) == 51
    assert Portfolio(stored_history(session_factory, owner)).positions == {}


def test_failure_after_insert_rolls_back_real_transaction(
    portfolio_client, owners, catalog, session_factory
):
    engine = session_factory.kw["bind"]

    def fail_after_insert(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.startswith("INSERT INTO transactions"):
            raise IntegrityError(
                "synthetic post-insert error", None, RuntimeError("synthetic")
            )

    event.listen(engine, "after_cursor_execute", fail_after_insert)
    try:
        response = operation(portfolio_client, owners[0][1])
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_insert)
    assert response.status_code == 503
    assert response.json()["code"] == "persistence_unavailable"
    assert "INSERT" not in response.text
    assert stored_history(session_factory, owners[0][0]) == []
    assert operation(portfolio_client, owners[0][1]).status_code == 201


def test_concurrent_catalog_registration_has_one_winner(
    portfolio_client, owners, session_factory
):
    engine = session_factory.kw["bind"]
    barrier = Barrier(2)

    def coordinate(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO assets"):
            barrier.wait(timeout=5)

    def create(ticker):
        return portfolio_client.post(
            ASSETS,
            headers=owners[0][1],
            json={"ticker": ticker, "name": "Race", "type": "fii"},
        )

    event.listen(engine, "before_cursor_execute", coordinate)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(create, ticker) for ticker in ("race11", " RACE11 ")
            ]
            responses = [future.result(timeout=15) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", coordinate)
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()[
            "code"
        ]
        == "asset_already_registered"
    )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssetModel)) == 1
        assert session.get(AssetModel, "RACE11").asset_type == "fii"


def test_concurrent_sales_wait_for_user_lock_and_cannot_oversell(
    portfolio_client, owners, catalog, session_factory
):
    owner, headers = owners[0]
    assert operation(portfolio_client, headers).status_code == 201
    engine = session_factory.kw["bind"]
    barrier = Barrier(2)
    acquired = Event()
    release = Event()
    guard = Lock()
    first_lock = [True]

    def coordinate(connection, cursor, statement, parameters, context, executemany):
        if statement.rstrip().endswith("FOR UPDATE"):
            barrier.wait(timeout=5)

    def hold_first_lock(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.rstrip().endswith("FOR UPDATE"):
            with guard:
                first = first_lock[0]
                first_lock[0] = False
            if first:
                acquired.set()
                assert release.wait(timeout=10), "Test must release held user lock"

    event.listen(engine, "before_cursor_execute", coordinate)
    event.listen(engine, "after_cursor_execute", hold_first_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    operation, portfolio_client, headers, type="sell", quantity="7"
                )
                for _ in range(2)
            ]
            try:
                assert acquired.wait(timeout=5), "No user lock acquired"
                # Observe a real blocked PostgreSQL backend before releasing the
                # first writer. No arbitrary sleep or sequential mock is used.
                blocked = False
                deadline = monotonic() + 5
                with engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as connection:
                    while monotonic() < deadline and not blocked:
                        blocked = connection.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND cardinality(pg_blocking_pids(pid)) > 0 "
                                "AND query LIKE '%FOR UPDATE%')"
                            )
                        )
                assert blocked, "Second real sale did not wait for the user lock"
            finally:
                release.set()
            responses = [future.result(timeout=15) for future in futures]
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", coordinate)
        event.remove(engine, "after_cursor_execute", hold_first_lock)
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()[
            "code"
        ]
        == "insufficient_balance"
    )
    history = stored_history(session_factory, owner)
    assert len(history) == 2
    assert sum(
        tx.quantity for tx in history if tx.transaction_type == TransactionType.SELL
    ) == Decimal(7)
    position = Portfolio(history).positions["TEST3"]
    assert position.quantity == Decimal(3)
    assert position.cost == Decimal(90)
    assert engine.pool.checkedout() == 0
