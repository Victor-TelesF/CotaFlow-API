from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal, localcontext

import pytest

from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.exceptions import (
    AssetNotFoundError,
    InsufficientBalanceError,
    InvalidPriceError,
    InvalidQuantityError,
    MissingPriceError,
)
from domain.portfolio import Portfolio, Transaction


def buy(portfolio, asset, quantity="10", price="30", day=5, sequence=None):
    return portfolio.buy(
        asset, Decimal(quantity), Decimal(price), date(2026, 1, day), sequence
    )


def sell(portfolio, asset, quantity="5", price="50", day=10, sequence=None):
    return portfolio.sell(
        asset, Decimal(quantity), Decimal(price), date(2026, 1, day), sequence
    )


@pytest.mark.parametrize("asset_type", [AssetType.STOCK, AssetType.FII])
def test_weighted_average_partial_sale_and_market_snapshot(asset_type):
    asset = Asset("Example", "TEST3", asset_type)
    portfolio = Portfolio()
    buy(portfolio, asset)
    buy(portfolio, asset, price="40", day=6)
    sale = sell(portfolio, asset)
    history = portfolio.transaction_list

    position = portfolio.positions["TEST3"]
    assert position.quantity == Decimal(15)
    assert position.average_price == Decimal(35)
    assert position.cost == Decimal(525)
    assert portfolio.get_total_cost() == Decimal(525)
    assert portfolio.get_total_equity({"TEST3": Decimal(42)}) == Decimal(630)
    assert portfolio.get_total_pnl({"TEST3": Decimal(42)}) == Decimal(105)
    assert portfolio.get_unrealized_return({"TEST3": Decimal(42)}) == Decimal("0.20")
    assert portfolio.get_pnl("TEST3", {"TEST3": Decimal(28)}) == Decimal(-105)
    assert portfolio.get_unrealized_return({"TEST3": Decimal(28)}) == Decimal("-0.20")
    assert portfolio.transaction_list == history
    assert sale.price == Decimal(50)
    assert sale.transaction_date == date(2026, 1, 10)


def test_liquidation_keeps_history_and_repurchase_starts_new_cost(petr4):
    portfolio = Portfolio()
    first = buy(portfolio, petr4)
    sale = sell(portfolio, petr4, quantity="10")
    assert portfolio.positions == {}
    assert portfolio.transaction_list == [first, sale]
    assert portfolio.get_total_equity({}) == Decimal(0)
    assert portfolio.get_total_cost() == Decimal(0)

    repurchase = buy(portfolio, petr4, quantity="2", price="80", day=20)
    assert portfolio.positions["PETR4"].average_price == Decimal(80)
    assert portfolio.get_total_cost() == Decimal(160)
    assert portfolio.transaction_list == [first, sale, repurchase]


@pytest.mark.parametrize("day, quantity", [(4, "1"), (10, "11")])
def test_rejected_sale_preserves_history_and_position(petr4, day, quantity):
    portfolio = Portfolio()
    purchase = buy(portfolio, petr4)
    with pytest.raises(InsufficientBalanceError):
        sell(portfolio, petr4, quantity=quantity, day=day)
    assert portfolio.transaction_list == [purchase]
    assert portfolio.positions["PETR4"].quantity == Decimal(10)
    assert portfolio.get_total_cost() == Decimal(300)


def test_retroactive_sale_checks_closed_cycle_even_with_current_balance(petr4):
    portfolio = Portfolio()
    buy(portfolio, petr4)
    sell(portfolio, petr4, quantity="10")
    buy(portfolio, petr4, quantity="100", price="40", day=20)
    before = portfolio.transaction_list
    with pytest.raises(InsufficientBalanceError):
        sell(portfolio, petr4, quantity="1", day=7)
    assert portfolio.transaction_list == before
    assert portfolio.positions["PETR4"].quantity == Decimal(100)
    assert portfolio.get_total_cost() == Decimal(4000)


def test_sale_without_history_is_insufficient_and_does_not_record(petr4):
    portfolio = Portfolio()
    with pytest.raises(InsufficientBalanceError):
        sell(portfolio, petr4)
    assert portfolio.positions == {}
    assert portfolio.transaction_list == []


def test_reconstruction_sorts_persisted_sequence_before_new_same_day_operations(petr4):
    day = date(2026, 1, 5)
    first = Transaction(petr4, Decimal(10), Decimal(30), TransactionType.BUY, day, 100)
    second = Transaction(petr4, Decimal(5), Decimal(50), TransactionType.SELL, day, 101)
    portfolio = Portfolio([second, first])
    new = buy(portfolio, petr4, quantity="5", price="40", day=5)
    assert portfolio.transaction_list == [first, second, new]
    assert portfolio.positions["PETR4"].average_price == Decimal(35)
    assert portfolio.get_total_cost() == Decimal(350)


def test_same_day_new_sale_follows_prior_new_buy(petr4):
    portfolio = Portfolio()
    first = buy(portfolio, petr4)
    second = sell(portfolio, petr4, day=5)
    assert portfolio.transaction_list == [first, second]
    assert portfolio.positions["PETR4"].quantity == Decimal(5)


def test_incomplete_snapshot_cannot_produce_a_total(petr4, vale3):
    portfolio = Portfolio()
    buy(portfolio, petr4)
    buy(portfolio, vale3)
    before = portfolio.transaction_list
    with pytest.raises(MissingPriceError, match="VALE3"):
        portfolio.get_total_equity({"PETR4": Decimal(40)})
    assert portfolio.transaction_list == before
    assert portfolio.get_total_cost() == Decimal(600)
    assert portfolio.get_total_equity(
        {"PETR4": Decimal(40), "VALE3": Decimal(70)}
    ) == Decimal(1100)


@pytest.mark.parametrize(
    "price", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity"), 35.5]
)
def test_invalid_market_price_rejects_evaluation_without_changing_history(petr4, price):
    portfolio = Portfolio()
    purchase = buy(portfolio, petr4)
    with pytest.raises(InvalidPriceError):
        portfolio.get_total_equity({"PETR4": price})
    assert portfolio.transaction_list == [purchase]
    assert portfolio.get_total_cost() == Decimal(300)


def test_empty_portfolio_and_unknown_position():
    portfolio = Portfolio()
    assert portfolio.positions == {}
    assert portfolio.get_total_cost() == Decimal(0)
    assert portfolio.get_total_equity({}) == Decimal(0)
    assert portfolio.get_total_pnl({}) == Decimal(0)
    assert portfolio.get_unrealized_return({}) is None
    with pytest.raises(AssetNotFoundError):
        portfolio.get_pnl("TEST3", {})


def test_returned_collections_and_positions_cannot_rewrite_portfolio(petr4):
    portfolio = Portfolio()
    purchase = buy(portfolio, petr4)
    portfolio.transaction_list.clear()
    positions = portfolio.positions
    positions["PETR4"].add_transaction(
        Transaction(
            petr4, Decimal(1), Decimal(30), TransactionType.SELL, date(2026, 1, 10)
        )
    )
    positions.clear()
    with pytest.raises(FrozenInstanceError):
        purchase.quantity = Decimal(100)
    assert portfolio.transaction_list == [purchase]
    assert portfolio.positions["PETR4"].quantity == Decimal(10)


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("quantity", Decimal("Infinity"), InvalidQuantityError),
        ("price", Decimal("NaN"), InvalidPriceError),
    ],
)
def test_invalid_new_operation_does_not_change_existing_history(
    petr4, field, value, error
):
    portfolio = Portfolio()
    purchase = buy(portfolio, petr4)
    values = {
        "quantity": Decimal(1),
        "price": Decimal(30),
        "buy_date": date(2026, 1, 6),
    }
    values[field] = value
    with pytest.raises(error):
        portfolio.buy(petr4, **values)
    assert portfolio.transaction_list == [purchase]
    assert portfolio.get_total_cost() == Decimal(300)


def test_maximum_transaction_keeps_product_precision_under_small_caller_context(petr4):
    portfolio = Portfolio()
    with localcontext() as context:
        context.prec = 6
        buy(
            portfolio,
            petr4,
            quantity="9999999999.99999999",
            price="9999999999.99999999",
        )
        assert portfolio.get_total_cost() == Decimal(
            "99999999999999999800.0000000000000001"
        )
        assert portfolio.get_total_equity(
            {"PETR4": Decimal("9999999999.99999999")}
        ) == Decimal("99999999999999999800.0000000000000001")
        assert portfolio.get_total_pnl(
            {"PETR4": Decimal("9999999999.99999999")}
        ) == Decimal(0)
