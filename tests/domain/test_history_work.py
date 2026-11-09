"""Count real reconstruction work rather than relying on CI timing."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from application.types import PortfolioValuation, Quote
from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.exceptions import InsufficientBalanceError
from domain.portfolio import Portfolio, Position, Transaction
from presentation.http.schemas.portfolio import (
    PortfolioPositionsResponse,
    PortfolioValuationResponse,
)


@pytest.mark.parametrize("size", [10, 1000])
def test_one_reconstruction_per_snapshot_with_independent_financial_oracle(
    monkeypatch, size
):
    # Each closed cycle: buy 10 @30, buy 10 @40, sell 20.
    # The final cycle has buy 10 @30, buy 10 @40, sell 5 -> 15 @35.
    asset = Asset("Synthetic", "TEST3", AssetType.STOCK)
    history = []
    for _ in range(size):
        for kind, quantity, price in (
            (TransactionType.BUY, 10, 30),
            (TransactionType.BUY, 10, 40),
            (TransactionType.SELL, 20, 90),
        ):
            history.append(
                Transaction(
                    asset,
                    Decimal(quantity),
                    Decimal(price),
                    kind,
                    date(2026, 1, 1),
                    len(history) + 1,
                )
            )
    history[-1] = Transaction(
        asset,
        Decimal(5),
        Decimal(90),
        TransactionType.SELL,
        date(2026, 1, 1),
        len(history),
    )
    visited = []
    calculate = Position._calculate

    def counted(self, transactions):
        visited.append(len(transactions))
        return calculate(self, transactions)

    monkeypatch.setattr(Position, "_calculate", counted)
    portfolio = Portfolio(history)
    positions = PortfolioPositionsResponse.from_portfolio(portfolio)
    quote = Quote(
        "TEST3",
        Decimal(42),
        "BRL",
        "synthetic",
        None,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    valuation = PortfolioValuationResponse.from_valuation(
        PortfolioValuation(portfolio, {"TEST3": quote})
    )
    assert positions.total_cost == valuation.total_cost == Decimal(525)
    assert positions.positions[0].quantity == Decimal(15)
    assert positions.positions[0].average_price == Decimal(35)
    assert valuation.total_market_value == Decimal(630)
    assert valuation.total_unrealized_pnl == Decimal(105)
    assert valuation.unrealized_return == Decimal("0.2")
    assert visited == [3 * size]


def test_returned_snapshot_is_independent_of_later_retroactive_changes(petr4):
    portfolio = Portfolio()
    portfolio.buy(petr4, Decimal(10), Decimal(30), date(2026, 1, 5))
    portfolio.sell(petr4, Decimal(10), Decimal(50), date(2026, 1, 10))
    portfolio.buy(petr4, Decimal(10), Decimal(80), date(2026, 1, 20))
    prior = portfolio.positions["PETR4"]
    # An earlier purchase changes the closed-cycle average to 35 and leaves
    # 10 @35 before the 10 @80 repurchase: 20 @57.5, cost 1150.
    portfolio.buy(petr4, Decimal(10), Decimal(40), date(2026, 1, 1))
    assert prior.quantity == Decimal(10) and prior.cost == Decimal(800)
    assert portfolio.get_total_cost() == Decimal(1150)
    prior.add_transaction(
        Transaction(
            petr4, Decimal(10), Decimal(90), TransactionType.SELL, date(2026, 1, 21)
        )
    )
    assert prior.quantity == 0 and prior.cost == 0
    assert portfolio.positions["PETR4"].quantity == Decimal(20)
    before = portfolio.transaction_list
    # Final balance would be positive, but the earlier sale breaks the closed
    # cycle before the repurchase; cached state must survive this rejection.
    with pytest.raises(InsufficientBalanceError):
        portfolio.sell(petr4, Decimal(11), Decimal(90), date(2026, 1, 7))
    assert portfolio.transaction_list == before
    assert portfolio.get_total_cost() == Decimal(1150)
