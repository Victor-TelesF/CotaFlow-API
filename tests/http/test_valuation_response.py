"""Precisão pública e contrato OpenAPI da avaliação, sem infraestrutura."""

from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

from application.types import PortfolioValuation, Quote
from domain.assets import Asset
from domain.enums import AssetType
from domain.portfolio import Portfolio
from main import create_app
from presentation.http.schemas.portfolio import PortfolioValuationResponse


def test_fractional_quote_preserves_decimal_precision_with_low_global_context():
    portfolio = Portfolio()
    portfolio.buy(
        Asset("Synthetic", "TEST3", AssetType.STOCK),
        Decimal(3),
        Decimal(1),
        date(2026, 1, 1),
    )
    quote = Quote(
        "TEST3",
        Decimal("0.123456789123456789"),
        "BRL",
        "brapi",
        None,
        datetime(2026, 11, 8, tzinfo=UTC),
    )
    with localcontext() as context:
        context.prec = 6
        result = PortfolioValuationResponse.from_valuation(
            PortfolioValuation(portfolio, {"TEST3": quote})
        ).model_dump(mode="json")
    # 3 x 0.123456789123456789; resultado sobre custo = preço - 1.
    assert result["total_market_value"] == "0.370370367370370367"
    assert result["total_unrealized_pnl"] == "-2.629629632629629633"
    assert result["unrealized_return"] == "-0.876543210876543211"
    position = result["positions"][0]
    assert position["market_value"] == "0.370370367370370367"
    assert position["unrealized_pnl"] == "-2.629629632629629633"
    assert position["quote"]["price"] == "0.123456789123456789"
    assert position["quote"]["quoted_at"] is None


def test_valuation_openapi_requires_bearer_and_financial_strings():
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v1/portfolio/valuation"]["get"]
    assert operation["security"] == [{"HTTPBearer": []}]
    models = schema["components"]["schemas"]
    for model, fields in {
        "QuoteResponse": ["price"],
        "ValuedPositionResponse": [
            "quantity",
            "average_price",
            "cost",
            "market_value",
            "unrealized_pnl",
        ],
        "PortfolioValuationResponse": [
            "total_cost",
            "total_market_value",
            "total_unrealized_pnl",
        ],
    }.items():
        for field in fields:
            assert models[model]["properties"][field]["type"] == "string"
    assert models["PortfolioValuationResponse"]["properties"]["unrealized_return"][
        "anyOf"
    ] == [{"type": "string"}, {"type": "null"}]
