from datetime import date
from decimal import Decimal

import pytest

from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.exceptions import InvalidPriceError, InvalidQuantityError
from domain.portfolio import Portfolio, Transaction


def test_retroactive_buy_after_liquidation_keeps_closed_cycle_cost():
    asset = Asset("Example", "TEST3", AssetType.STOCK)
    portfolio = Portfolio()
    portfolio.buy(asset, Decimal(10), Decimal(30), date(2026, 1, 5))
    portfolio.sell(asset, Decimal(10), Decimal(50), date(2026, 1, 10))
    portfolio.buy(asset, Decimal(10), Decimal(40), date(2026, 1, 1))

    # 20 units costing 700 before the sale; 10 remain at average cost 35.
    assert portfolio.positions["TEST3"].average_price == Decimal(35)
    assert portfolio.get_total_cost() == Decimal(350)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
@pytest.mark.parametrize(
    "field, error", [("quantity", InvalidQuantityError), ("price", InvalidPriceError)]
)
def test_nonfinite_transaction_value_has_specific_domain_error(value, field, error):
    values = {"quantity": Decimal(10), "price": Decimal(30)}
    values[field] = value
    with pytest.raises(error):
        Transaction(
            Asset("Example", "TEST3", AssetType.STOCK),
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 1, 5),
            **values,
        )
