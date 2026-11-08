"""Reconstruct a position across partial sales and closed cycles."""

from decimal import Decimal, localcontext

from ..assets import Asset
from ..decimal_values import CALCULATION_CONTEXT, validate_positive_decimal
from ..enums import TransactionType
from ..exceptions import InsufficientBalanceError, InvalidPriceError, InvalidValueError
from .transaction import Transaction, ordered_transactions


class Position:
    def __init__(self, asset: Asset, transaction_list: list[Transaction] | None = None):
        if not isinstance(asset, Asset):
            raise InvalidValueError("Asset deve ser uma instância de Asset")
        self._asset = asset
        transactions = ordered_transactions(list(transaction_list or []))
        self._calculate(transactions)
        self._transaction_list = transactions

    @property
    def asset(self) -> Asset:
        return self._asset

    @property
    def transaction_list(self) -> list[Transaction]:
        return self._transaction_list.copy()

    def _calculate(self, transactions: list[Transaction]) -> tuple[Decimal, Decimal]:
        quantity = Decimal(0)
        average = Decimal(0)
        with localcontext(CALCULATION_CONTEXT):
            for transaction in transactions:
                if transaction.asset != self.asset:
                    raise InvalidValueError(
                        "O ativo da transação difere do ativo da posição"
                    )
                if transaction.transaction_type == TransactionType.BUY:
                    new_quantity = quantity + transaction.quantity
                    average = (
                        quantity * average + transaction.quantity * transaction.price
                    ) / new_quantity
                    quantity = new_quantity
                else:
                    quantity -= transaction.quantity
                    if quantity < 0:
                        raise InsufficientBalanceError(
                            "Não é possível vender mais do que possui"
                        )
                    if quantity == 0:
                        average = Decimal(0)
        return quantity, average

    def add_transaction(self, transaction: Transaction) -> None:
        candidates = ordered_transactions([*self._transaction_list, transaction])
        self._calculate(candidates)
        self._transaction_list = candidates

    @property
    def quantity(self) -> Decimal:
        return self._calculate(self._transaction_list)[0]

    @property
    def average_price(self) -> Decimal:
        return self._calculate(self._transaction_list)[1]

    @property
    def cost(self) -> Decimal:
        with localcontext(CALCULATION_CONTEXT):
            quantity, average = self._calculate(self._transaction_list)
            return quantity * average

    def market_value(self, price: Decimal) -> Decimal:
        validate_positive_decimal(price, InvalidPriceError)
        with localcontext(CALCULATION_CONTEXT):
            return self.quantity * price
