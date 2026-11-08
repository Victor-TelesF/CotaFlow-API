"""Fatos imutáveis de compra/venda e convenção única de ordenação histórica.

Preço e data informados na operação nunca são substituídos por cotação atual.
Este módulo valida uma operação isolada; o saldo ao longo de todo o histórico
é verificado por Position/Portfolio.
"""

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
    """Operação com valores Decimal exatos e data não futura.

    Quantidade/preço precisam ser positivos, finitos e representáveis em
    NUMERIC(18, 8), sem arredondamento. Operações novas geram UUID e podem
    estar sem sequência; a persistência atribui uma sequência positiva.
    ``id_transaction`` pode ser informado por nome ao restaurar uma linha,
    preservando sua identidade sem contornar as validações do domínio.
    """

    asset: Asset
    quantity: Decimal
    price: Decimal
    transaction_type: TransactionType
    transaction_date: date
    transaction_sequence: int | None = None
    id_transaction: UUID = field(default_factory=uuid4, kw_only=True)

    def __post_init__(self) -> None:
        """Valida identidade, ativo, valores, tipo, data e sequência opcional."""
        if not isinstance(self.id_transaction, UUID):
            raise InvalidValueError("ID da transação deve ser UUID")
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
    """Retorna outra lista ordenada por data e sequência persistida.

    Na mesma data, operações novas sem sequência seguem as já persistidas e
    mantêm sua ordem de entrada entre si. A lista recebida não é alterada.
    """
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
