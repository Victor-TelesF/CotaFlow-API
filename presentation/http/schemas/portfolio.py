"""Entradas exatas e respostas de catálogo, histórico, posições e avaliação.

Quantidade/preço entram como strings JSON: um número JSON já pode ter perdido
precisão no parser. O domínio decide positividade, finitude e NUMERIC(18,8).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_serializer

from application.types import PortfolioValuation
from domain.assets.asset import (
    MAX_RAW_TICKER,
    TICKER_PATTERN,
    normalize_ticker,
)
from domain.enums import AssetType, TransactionType
from domain.exceptions import InvalidValueError
from domain.portfolio import Portfolio, Position, Transaction


def decimal_string(value: object) -> Decimal:
    """Exige texto antes do parsing Decimal, sem aceitar float ou bool."""
    if not isinstance(value, str):
        # Pydantic traduz ValueError em 422; TypeError escaparia como erro 500.
        raise ValueError("Expected a decimal string")  # noqa: TRY004
    try:
        return Decimal(value)
    except ArithmeticError:
        raise ValueError("Invalid decimal string") from None


DecimalInput = Annotated[
    Decimal, BeforeValidator(decimal_string, json_schema_input_type=str)
]


def ticker_input(value: object) -> str:
    try:
        return normalize_ticker(value)
    except InvalidValueError:
        raise ValueError("Invalid ticker") from None


TickerInput = Annotated[
    str,
    Field(
        min_length=1,
        max_length=20,
        pattern=f"^{TICKER_PATTERN}$",
        description=f"Normalized ASCII ticker; raw input at most {MAX_RAW_TICKER} characters",
    ),
    BeforeValidator(ticker_input),
]


class AssetResponse(BaseModel):
    """Identidade pública do ativo normalizado."""

    ticker: str
    name: str
    type: AssetType


class AssetPage(BaseModel):
    """Página ordenada por ticker; não promete fotografia entre requisições."""

    items: list[AssetResponse]
    limit: int
    offset: int


class TransactionRequest(BaseModel):
    """Dono vem exclusivamente do Bearer; não há user_id na entrada."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    ticker: TickerInput
    quantity: DecimalInput
    price: DecimalInput
    type: TransactionType
    date: date


class TransactionResponse(BaseModel):
    """Fatos com UUID público; o desempate interno não integra a resposta."""

    id: UUID
    ticker: str
    quantity: Decimal
    price: Decimal
    type: TransactionType
    date: date

    @field_serializer("quantity", "price")
    def serialize_decimal(self, value: Decimal) -> str:
        """Estabiliza POST/GET: os valores já cabem exatamente em NUMERIC(18,8)."""
        return format(value, ".8f")

    @classmethod
    def from_transaction(cls, transaction: Transaction) -> "TransactionResponse":
        """Conversão única da operação para seu contrato público."""
        return cls(
            id=transaction.id_transaction,
            ticker=transaction.asset.ticker,
            quantity=transaction.quantity,
            price=transaction.price,
            type=transaction.transaction_type,
            date=transaction.transaction_date,
        )


class TransactionPage(BaseModel):
    """Histórico por data e ordem estável de registro, inclusive ciclos zerados."""

    items: list[TransactionResponse]
    limit: int
    offset: int


class PositionResponse(BaseModel):
    """Posição aberta; derivados preservam a precisão de cálculo do domínio."""

    ticker: str
    name: str
    type: AssetType
    quantity: Decimal
    average_price: Decimal
    cost: Decimal

    @field_serializer("quantity", "average_price", "cost")
    def serialize_decimal(self, value: Decimal) -> str:
        """Texto decimal sem float, notação científica ou arredondamento extra."""
        return format(value, "f")

    @classmethod
    def from_position(cls, position: Position) -> "PositionResponse":
        """Expõe identidade e indicadores já calculados pelo domínio."""
        return cls(
            ticker=position.asset.ticker,
            name=position.asset.name,
            type=position.asset.asset_type,
            quantity=position.quantity,
            average_price=position.average_price,
            cost=position.cost,
        )


class PortfolioPositionsResponse(BaseModel):
    """Todas as posições abertas por ticker; carteira vazia tem custo zero."""

    positions: list[PositionResponse]
    total_cost: Decimal

    @field_serializer("total_cost")
    def serialize_decimal(self, value: Decimal) -> str:
        """Preserva o total calculado antes de qualquer conversão de apresentação."""
        return format(value, "f")

    @classmethod
    def from_portfolio(cls, portfolio: Portfolio) -> "PortfolioPositionsResponse":
        """Serializa uma única carteira; não soma nem duplica fórmulas aqui."""
        positions = portfolio.positions
        return cls(
            positions=[
                PositionResponse.from_position(positions[ticker])
                for ticker in sorted(positions)
            ],
            total_cost=portfolio.get_total_cost(),
        )


class QuoteResponse(BaseModel):
    """Preço externo e origem/horários; TTL não promete cotação em tempo real."""

    model_config = ConfigDict(from_attributes=True)
    ticker: str
    price: Decimal
    currency: str
    source: str
    quoted_at: datetime | None
    fetched_at: datetime
    cached: bool

    @field_serializer("price")
    def serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class ValuedPositionResponse(PositionResponse):
    """Mesmos indicadores a custo, acrescidos da avaliação do domínio."""

    quote: QuoteResponse
    market_value: Decimal
    unrealized_pnl: Decimal

    @field_serializer("market_value", "unrealized_pnl")
    def serialize_market_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class PortfolioValuationResponse(BaseModel):
    """Valores em BRL; razão 0.20 significa 20%, sem denominador retorna null."""

    currency: str = "BRL"
    positions: list[ValuedPositionResponse]
    total_cost: Decimal
    total_market_value: Decimal
    total_unrealized_pnl: Decimal
    unrealized_return: Decimal | None

    @field_serializer("total_cost", "total_market_value", "total_unrealized_pnl")
    def serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")

    @field_serializer("unrealized_return")
    def serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        return format(value, "f") if value is not None else None

    @classmethod
    def from_valuation(
        cls, valuation: PortfolioValuation
    ) -> "PortfolioValuationResponse":
        """Converte somente; todas as fórmulas permanecem no domínio existente."""
        portfolio, quotes = valuation.portfolio, valuation.quotes
        prices = {ticker: quote.price for ticker, quote in quotes.items()}
        positions = portfolio.positions
        return cls(
            positions=[
                ValuedPositionResponse(
                    **PositionResponse.from_position(positions[ticker]).model_dump(),
                    quote=QuoteResponse.model_validate(quotes[ticker]),
                    market_value=positions[ticker].market_value(prices[ticker]),
                    unrealized_pnl=portfolio.get_pnl(ticker, prices),
                )
                for ticker in sorted(positions)
            ],
            total_cost=portfolio.get_total_cost(),
            total_market_value=portfolio.get_total_equity(prices),
            total_unrealized_pnl=portfolio.get_total_pnl(prices),
            unrealized_return=portfolio.get_unrealized_return(prices),
        )
