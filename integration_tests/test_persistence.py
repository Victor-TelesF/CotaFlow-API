from datetime import date
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.exc import DataError, IntegrityError, InvalidRequestError

from application.exceptions import AssetAlreadyRegistered, EmailAlreadyRegistered
from application.types import User
from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.portfolio import Portfolio, Transaction
from infrastructure.database.models import AssetModel, TransactionModel, UserModel
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


def seed(factory):
    user = User("synthetic@example.test", "synthetic-hash")
    asset = Asset("Synthetic stock", " test3 ", AssetType.STOCK)
    with SqlAlchemyUnitOfWork(factory) as uow:
        uow.users.add(user)
        uow.assets.add(asset)
        uow.commit()
    return user, asset


def test_round_trip_preserves_identity_history_and_independent_financial_result(
    session_factory,
):
    user, asset = seed(session_factory)
    other = User("other@example.test", "synthetic-other-hash")
    fii = Asset("Synthetic FII", "FUND11", AssetType.FII)
    # Insert out of date order; ties must retain the DB-assigned order.
    transactions = [
        Transaction(
            asset, Decimal(5), Decimal(45), TransactionType.BUY, date(2026, 1, 2)
        ),
        Transaction(
            asset, Decimal(10), Decimal(30), TransactionType.BUY, date(2026, 1, 1)
        ),
        Transaction(
            asset, Decimal(3), Decimal(50), TransactionType.SELL, date(2026, 1, 2)
        ),
    ]
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.users.add(other)
        uow.assets.add(fii)
        stored = [uow.transactions.add(user.id, tx) for tx in transactions]
        uow.transactions.add(
            other.id,
            Transaction(
                fii,
                Decimal("0.00000001"),
                Decimal("9999999999.99999999"),
                TransactionType.BUY,
                date(2026, 1, 1),
            ),
        )
        uow.commit()

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.users.get(user.id) == user
        assert uow.users.get_by_email(user.email) == user
        assert uow.assets.get("TEST3") == asset
        assert uow.assets.get("FUND11") == fii
        loaded = uow.transactions.list_for_user(user.id)
        assert [tx.id_transaction for tx in loaded] == [
            transactions[1].id_transaction,
            transactions[0].id_transaction,
            transactions[2].id_transaction,
        ]
        assert [tx.transaction_sequence for tx in loaded] == [
            stored[1].transaction_sequence,
            stored[0].transaction_sequence,
            stored[2].transaction_sequence,
        ]
        for original, restored in zip(
            [transactions[1], transactions[0], transactions[2]], loaded, strict=True
        ):
            assert (
                restored.asset,
                restored.quantity,
                restored.price,
                restored.transaction_type,
                restored.transaction_date,
            ) == (
                original.asset,
                original.quantity,
                original.price,
                original.transaction_type,
                original.transaction_date,
            )
        other_history = uow.transactions.list_for_user(other.id)
        assert len(other_history) == 1
        assert other_history[0].quantity == Decimal("0.00000001")
        assert other_history[0].price == Decimal("9999999999.99999999")
        assert uow.transactions.list_for_user(uuid4()) == []
        assert uow.users.get(uuid4()) is None
        assert uow.users.get_by_email("missing@example.test") is None
        assert uow.assets.get("MISSING3") is None

    # Independent expectation: (10*30 + 5*45)/15 = 35; sale leaves 12*35.
    portfolio = Portfolio(loaded)
    assert portfolio.positions["TEST3"].quantity == Decimal(12)
    assert portfolio.positions["TEST3"].average_price == Decimal(35)
    assert portfolio.get_total_cost() == Decimal(420)


@pytest.mark.parametrize(
    "exit_path", ["normal_without_commit", "exception", "constraint"]
)
def test_rollback_leaves_no_partial_rows_and_returns_connection(
    session_factory, exit_path
):
    user = User("rollback@example.test", "synthetic-hash")
    asset = Asset("Rollback", "ROLL3", AssetType.STOCK)
    saved_repository = None

    def write():
        nonlocal saved_repository
        with SqlAlchemyUnitOfWork(session_factory) as uow:
            saved_repository = uow.users
            uow.users.add(user)
            uow.assets.add(asset)
            uow.transactions.add(
                user.id,
                Transaction(
                    asset,
                    Decimal(10),
                    Decimal(30),
                    TransactionType.BUY,
                    date(2026, 1, 1),
                ),
            )
            if exit_path == "exception":
                raise RuntimeError("synthetic failure after all inserts")
            if exit_path == "constraint":
                uow.users.add(User(user.email, "duplicate-hash"))

    if exit_path == "exception":
        with pytest.raises(RuntimeError, match="synthetic failure"):
            write()
    elif exit_path == "constraint":
        with pytest.raises(EmailAlreadyRegistered):
            write()
    else:
        write()

    assert session_factory.kw["bind"].pool.checkedout() == 0
    with session_factory() as session:
        for model in (UserModel, AssetModel, TransactionModel):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        saved_repository.get(user.id)


def test_sessions_are_isolated_until_explicit_commit(session_factory):
    user = User("isolated@example.test", "synthetic-hash")
    with SqlAlchemyUnitOfWork(session_factory) as writer:
        writer.users.add(user)
        with SqlAlchemyUnitOfWork(session_factory) as reader:
            assert reader.users.get(user.id) is None
            writer.commit()
            assert reader.users.get(user.id) == user
    with SqlAlchemyUnitOfWork(session_factory) as fresh:
        assert fresh.users.get(user.id) == user
    assert session_factory.kw["bind"].pool.checkedout() == 0


@pytest.mark.parametrize(
    "model, changes, constraint",
    [
        (UserModel, {"email": " Case@example.test "}, "ck_users_email"),
        (UserModel, {"email": ""}, "ck_users_email"),
        (UserModel, {"password_hash": " "}, "ck_users_password_hash"),
        (AssetModel, {"ticker": "bad3"}, "ck_assets_ticker"),
        (AssetModel, {"ticker": ""}, "ck_assets_ticker"),
        (AssetModel, {"asset_type": "crypto"}, "ck_assets_type"),
        (AssetModel, {"name": " "}, "ck_assets_name"),
        (TransactionModel, {"transaction_type": "dividend"}, "ck_transactions_type"),
        (TransactionModel, {"quantity": Decimal(0)}, "ck_transactions_quantity"),
        (TransactionModel, {"quantity": Decimal(-1)}, "ck_transactions_quantity"),
        (TransactionModel, {"quantity": Decimal("NaN")}, "ck_transactions_quantity"),
        (TransactionModel, {"price": Decimal(0)}, "ck_transactions_price"),
        (TransactionModel, {"price": Decimal(-1)}, "ck_transactions_price"),
        (TransactionModel, {"price": Decimal("NaN")}, "ck_transactions_price"),
        (TransactionModel, {"user_id": uuid4()}, "fk_transactions_user"),
        (TransactionModel, {"asset_ticker": "MISSING3"}, "fk_transactions_asset"),
    ],
)
def test_database_constraints_reject_invalid_facts(
    session_factory, model, changes, constraint
):
    user, asset = seed(session_factory)
    defaults = {
        UserModel: {
            "id": uuid4(),
            "email": "valid@example.test",
            "password_hash": "synthetic-hash",
        },
        AssetModel: {"ticker": "VALID3", "name": "Valid", "asset_type": "stock"},
        TransactionModel: {
            "id": uuid4(),
            "user_id": user.id,
            "asset_ticker": asset.ticker,
            "quantity": Decimal(1),
            "price": Decimal(30),
            "transaction_type": "buy",
            "transaction_date": date(2026, 1, 1),
        },
    }
    with pytest.raises(IntegrityError) as caught, session_factory.begin() as session:
        session.execute(insert(model).values(**(defaults[model] | changes)))
    assert caught.value.orig.diag.constraint_name == constraint
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TransactionModel)) == 0
        assert session.scalar(select(func.count()).select_from(UserModel)) == 1
        assert session.scalar(select(func.count()).select_from(AssetModel)) == 1


@pytest.mark.parametrize("field", ["quantity", "price"])
@pytest.mark.parametrize(
    "value", [Decimal(10000000000), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_database_numeric_overflow_is_rejected(session_factory, field, value):
    user, asset = seed(session_factory)
    values = {
        "id": uuid4(),
        "user_id": user.id,
        "asset_ticker": asset.ticker,
        "quantity": Decimal(1),
        "price": Decimal(30),
        "transaction_type": "buy",
        "transaction_date": date(2026, 1, 1),
    }
    values[field] = value
    with pytest.raises(DataError) as caught, session_factory.begin() as session:
        session.execute(insert(TransactionModel).values(**values))
    assert caught.value.orig.sqlstate == "22003"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TransactionModel)) == 0


@pytest.mark.parametrize("resource", ["email", "ticker"])
def test_uniqueness_rejects_duplicates_without_deleting_original(
    session_factory, resource
):
    user, asset = seed(session_factory)
    expected_error = (
        EmailAlreadyRegistered if resource == "email" else AssetAlreadyRegistered
    )
    with (
        pytest.raises(expected_error) as caught,
        SqlAlchemyUnitOfWork(session_factory) as uow,
    ):
        if resource == "email":
            uow.users.add(User(user.email, "synthetic-other-hash"))
        else:
            uow.assets.add(Asset("Other", "test3", AssetType.FII))
        uow.commit()
    # The exact known uniqueness constraints now have safe application errors.
    assert "synthetic-other-hash" not in str(caught.value)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.users.get(user.id) == user
        assert uow.assets.get(asset.ticker) == asset


def test_history_is_loaded_in_one_query_and_retains_decimal_precision(session_factory):
    user, asset = seed(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        for _ in range(3):
            uow.transactions.add(
                user.id,
                Transaction(
                    asset,
                    Decimal("0.00000001"),
                    Decimal("9999999999.99999999"),
                    TransactionType.BUY,
                    date(2026, 1, 1),
                ),
            )
        uow.commit()
    engine = session_factory.kw["bind"]
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with localcontext() as context:
            context.prec = 6
            with SqlAlchemyUnitOfWork(session_factory) as uow:
                history = uow.transactions.list_for_user(user.id)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 1
    assert len(history) == 3
    assert all(tx.quantity == Decimal("0.00000001") for tx in history)
    assert all(tx.price == Decimal("9999999999.99999999") for tx in history)


def test_raw_ticker_uniqueness_constraint_preserves_original_asset(session_factory):
    """Mantém evidência da constraint exata além da tradução feita pelo repositório."""
    _, asset = seed(session_factory)
    with pytest.raises(IntegrityError) as caught, session_factory.begin() as session:
        session.execute(
            insert(AssetModel).values(
                ticker=asset.ticker, name="Changed", asset_type="fii"
            )
        )
    assert caught.value.orig.sqlstate == "23505"
    assert caught.value.orig.diag.constraint_name == "assets_pkey"
    assert caught.value.hide_parameters
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.assets.get(asset.ticker) == asset
