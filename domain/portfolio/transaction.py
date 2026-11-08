"""Immutable operations and their stable historical order."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from ..assets import Asset
from ..decimal_values import validate_transaction_decimal
from ..enums import TransactionType
from ..exceptions import (
    InvalidPriceError,
    InvalidQuantityError,
    InvalidTransactionDateError,
    InvalidTransactionTypeError,
    InvalidValueError,
)
from ..reference_date import reference_date


@dataclass(frozen=True, eq=False)
class Transaction:
    asset: Asset
    quantity: Decimal
    price: Decimal
    transaction_type: TransactionType
    transaction_date: date
    transaction_sequence: int | None = None
    id_transaction: UUID = field(default_factory=uuid4, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise InvalidValueError("Asset deve ser uma instância de Asset")
        validate_transaction_decimal(self.quantity, InvalidQuantityError)
        validate_transaction_decimal(self.price, InvalidPriceError)
        if not isinstance(self.transaction_type, TransactionType):
            raise InvalidTransactionTypeError("Tipo de transação inválido")
        if (
            type(self.transaction_date) is not date
            or self.transaction_date > reference_date()
        ):
            raise InvalidTransactionDateError(
                "A data deve ser date e não pode ser futura"
            )
        sequence = self.transaction_sequence
        if sequence is not None and (type(sequence) is not int or sequence <= 0):
            raise InvalidValueError(
                "Sequência da transação deve ser um inteiro positivo"
            )


def ordered_transactions(transactions: list[Transaction]) -> list[Transaction]:
    """Sort by date/sequence; new same-day operations follow persisted ones."""
    if any(not isinstance(transaction, Transaction) for transaction in transactions):
        raise InvalidValueError("O histórico deve conter apenas transações")
    return [
        transaction
        for _, transaction in sorted(
            enumerate(transactions),
            key=lambda item: (
                item[1].transaction_date,
                item[1].transaction_sequence is None,
                item[1].transaction_sequence
                if item[1].transaction_sequence is not None
                else item[0],
            ),
        )
    ]
