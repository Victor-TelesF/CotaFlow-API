"""Decimal validation and calculation precision, independent of infrastructure."""

from decimal import Context, Decimal, localcontext

from .exceptions import InvalidValueError

# Operations use NUMERIC(18, 8); calculations retain 50 significant digits.
# Presentation rounding belongs to the HTTP boundary.
CALCULATION_CONTEXT = Context(prec=50)
TRANSACTION_QUANTUM = Decimal("0.00000001")
MAX_TRANSACTION_VALUE = Decimal("9999999999.99999999")


def validate_positive_decimal(value: Decimal, error: type[InvalidValueError]) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise error("O valor deve ser Decimal, finito e positivo")


def validate_transaction_decimal(
    value: Decimal, error: type[InvalidValueError]
) -> None:
    validate_positive_decimal(value, error)
    with localcontext(CALCULATION_CONTEXT):
        if value > MAX_TRANSACTION_VALUE or value != value.quantize(
            TRANSACTION_QUANTUM
        ):
            raise error("O valor deve caber em NUMERIC(18, 8), sem arredondamento")
