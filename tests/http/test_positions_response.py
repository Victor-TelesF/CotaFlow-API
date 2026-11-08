"""Precisão do contrato de posições, usando cálculo real e oráculo racional."""

from datetime import date
from decimal import Decimal, localcontext
from fractions import Fraction

import domain.portfolio.transaction as transaction_module
from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.portfolio import Portfolio, Transaction
from main import create_app
from presentation.http.schemas.portfolio import PortfolioPositionsResponse


def test_repeating_average_preserves_calculation_precision_under_small_context(
    monkeypatch,
):
    monkeypatch.setattr(transaction_module, "reference_date", lambda: date(2026, 11, 8))
    asset = Asset("Synthetic stock", "TEST3", AssetType.STOCK)
    portfolio = Portfolio(
        [
            Transaction(
                asset, Decimal(1), Decimal(1), TransactionType.BUY, date(2026, 1, 1)
            ),
            Transaction(
                asset, Decimal(2), Decimal(2), TransactionType.BUY, date(2026, 1, 1)
            ),
        ]
    )
    # The expected weighted price is independently represented as rational 5/3.
    # A presentation quantize to 8 places or a float conversion loses this precision.
    with localcontext() as context:
        context.prec = 6
        body = PortfolioPositionsResponse.from_portfolio(portfolio).model_dump(
            mode="json"
        )
    position = body["positions"][0]
    assert isinstance(position["average_price"], str)
    assert Fraction(position["quantity"]) == Fraction(3)
    assert abs(Fraction(position["average_price"]) - Fraction(5, 3)) < Fraction(
        1, 10**49
    )
    assert abs(Fraction(position["cost"]) - Fraction(5)) < Fraction(1, 10**48)
    assert abs(Fraction(body["total_cost"]) - Fraction(5)) < Fraction(1, 10**48)


def test_positions_openapi_defines_decimal_strings_and_authentication():
    schema = create_app().openapi()
    route = schema["paths"]["/api/v1/portfolio/positions"]["get"]
    assert route["security"] == [{"HTTPBearer": []}]
    assert "parameters" not in route
    position = schema["components"]["schemas"]["PositionResponse"]["properties"]
    for name in ("quantity", "average_price", "cost"):
        assert position[name]["type"] == "string"
    portfolio = schema["components"]["schemas"]["PortfolioPositionsResponse"][
        "properties"
    ]
    assert portfolio["total_cost"]["type"] == "string"
