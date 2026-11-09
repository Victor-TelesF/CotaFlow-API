"""Ferramenta de catálogo contra PostgreSQL isolado; efeitos persistidos reais."""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text

from application.types import User
from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.portfolio import Transaction
from infrastructure.catalog import audit_catalog, main
from infrastructure.config import Settings
from infrastructure.database.models import AssetModel
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


def test_local_tool_inserts_once_rejects_invalid_and_preserves_duplicate(
    session_factory, capsys
):
    engine = session_factory.kw["bind"]
    url = engine.url
    settings = Settings(
        _env_file=None,
        DB_HOST=url.host,
        DB_PORT=url.port,
        DB_USER=url.username,
        DB_PASSWORD=url.password,
        DB_NAME=url.database,
    )
    args = ["add", "--ticker", " test3 ", "--name", " Synthetic ", "--type", "stock"]
    assert main(args, settings=settings) == 0
    assert (
        main(
            ["add", "--ticker", "TEST3", "--name", "Changed", "--type", "fii"],
            settings=settings,
        )
        == 1
    )
    assert (
        main(
            ["add", "--ticker", "BAD/3", "--name", "Invalid", "--type", "stock"],
            settings=settings,
        )
        == 2
    )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssetModel)) == 1
        asset = session.get(AssetModel, "TEST3")
        assert (asset.name, asset.asset_type) == ("Synthetic", "stock")
    assert main(["audit", "--max-tickers", "20"], settings=settings) == 0
    output = capsys.readouterr().out
    assert '"invalid_tickers": 0' in output
    assert "synthetic-test-password" not in output
    assert engine.pool.checkedout() == 0


def test_read_only_audit_detects_legacy_incompatibility_without_hydration_or_changes(
    session_factory,
):
    engine = session_factory.kw["bind"]
    user = User("synthetic@example.test", "synthetic")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.users.add(user)
        for index in range(21):
            asset = Asset("Synthetic", f"SYN{index}", AssetType.STOCK)
            uow.assets.add(asset)
            uow.transactions.add(
                user.id,
                Transaction(
                    asset,
                    Decimal(1),
                    Decimal(30),
                    TransactionType.BUY,
                    date(2026, 1, 1),
                ),
            )
        uow.commit()
    with engine.begin() as connection:
        # Permitido pela revisão antiga, rejeitado pelo novo domínio.
        connection.execute(
            text(
                "INSERT INTO cotaflow.assets (ticker, name, asset_type) VALUES ('BAD/3', :name, 'stock')"
            ),
            {"name": "x" * 121},
        )
    assert audit_catalog(engine, 20) == {
        "invalid_tickers": 1,
        "invalid_names": 1,
        "oversized_portfolios": 1,
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM cotaflow.assets")) == 22
        assert (
            connection.scalar(text("SELECT count(*) FROM cotaflow.transactions")) == 21
        )
        assert (
            connection.scalar(
                text(
                    "SELECT char_length(name) FROM cotaflow.assets WHERE ticker='BAD/3'"
                )
            )
            == 121
        )
