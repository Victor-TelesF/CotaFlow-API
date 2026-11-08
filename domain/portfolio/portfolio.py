"""An implicit portfolio reconstructed from a user's complete history."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, localcontext

from ..assets import Asset
from ..decimal_values import CALCULATION_CONTEXT
from ..enums import TransactionType
from ..exceptions import AssetNotFoundError, MissingPriceError
from .position import Position
from .transaction import Transaction, ordered_transactions


class Portfolio:
    def __init__(self, transaction_list: list[Transaction] | None = None):
        transactions = ordered_transactions(list(transaction_list or []))
        self._rebuild_positions(transactions)
        self._transaction_list = transactions

    @staticmethod
    def _rebuild_positions(transactions: list[Transaction]) -> dict[str, Position]:
        grouped: dict[str, list[Transaction]] = {}
        for transaction in transactions:
            grouped.setdefault(transaction.asset.ticker, []).append(transaction)
        return {
            ticker: Position(history[0].asset, history)
            for ticker, history in grouped.items()
        }

    @property
    def transaction_list(self) -> list[Transaction]:
        return self._transaction_list.copy()

    @property
    def positions(self) -> dict[str, Position]:
        return {
            ticker: position
            for ticker, position in self._rebuild_positions(
                self._transaction_list
            ).items()
            if position.quantity > 0
        }

    def add_transaction(self, transaction: Transaction) -> None:
        candidates = ordered_transactions([*self._transaction_list, transaction])
        self._rebuild_positions(candidates)
        self._transaction_list = candidates

    def buy(
        self,
        asset: Asset,
        quantity: Decimal,
        price: Decimal,
        buy_date: date,
        transaction_sequence: int | None = None,
    ) -> Transaction:
        transaction = Transaction(
            asset, quantity, price, TransactionType.BUY, buy_date, transaction_sequence
        )
        self.add_transaction(transaction)
        return transaction

    def sell(
        self,
        asset: Asset,
        quantity: Decimal,
        price: Decimal,
        sell_date: date,
        transaction_sequence: int | None = None,
    ) -> Transaction:
        transaction = Transaction(
            asset,
            quantity,
            price,
            TransactionType.SELL,
            sell_date,
            transaction_sequence,
        )
        self.add_transaction(transaction)
        return transaction

    def get_total_cost(self) -> Decimal:
        with localcontext(CALCULATION_CONTEXT):
            return sum(
                (position.cost for position in self.positions.values()), Decimal(0)
            )

    def get_total_equity(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Use an already loaded price snapshot; never fetch prices here."""
        with localcontext(CALCULATION_CONTEXT):
            total = Decimal(0)
            for ticker, position in self.positions.items():
                if ticker not in prices:
                    raise MissingPriceError(f"Preço ausente para {ticker}")
                total += position.market_value(prices[ticker])
            return total

    def get_total_pnl(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Unrealized result on open positions, excluding proceeds from sales."""
        with localcontext(CALCULATION_CONTEXT):
            return self.get_total_equity(prices) - self.get_total_cost()

    def get_unrealized_return(self, prices: Mapping[str, Decimal]) -> Decimal | None:
        """Return the unrealized result/cost ratio; 0.20 represents 20%."""
        with localcontext(CALCULATION_CONTEXT):
            cost = self.get_total_cost()
            if cost == 0:
                return None
            return self.get_total_pnl(prices) / cost

    def get_pnl(self, ticker: str, prices: Mapping[str, Decimal]) -> Decimal:
        position = self.positions.get(ticker)
        if position is None:
            raise AssetNotFoundError("O ticker não possui posição aberta")
        if ticker not in prices:
            raise MissingPriceError(f"Preço ausente para {ticker}")
        with localcontext(CALCULATION_CONTEXT):
            return position.market_value(prices[ticker]) - position.cost
