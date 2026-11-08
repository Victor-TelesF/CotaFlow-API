"""Precisão financeira compartilhada pelas operações e cálculos do domínio.

Entradas de quantidade/preço devem caber exatamente em NUMERIC(18, 8).
Cálculos usam contexto local de 50 dígitos; não há conversão intermediária
para float. Arredondamento de apresentação pertence à fronteira HTTP futura.
"""

from decimal import Context, Decimal, localcontext

from .exceptions import InvalidValueError

# Operations use NUMERIC(18, 8); calculations retain 50 significant digits.
# Presentation rounding belongs to the HTTP boundary.
CALCULATION_CONTEXT = Context(prec=50)
TRANSACTION_QUANTUM = Decimal("0.00000001")
MAX_TRANSACTION_VALUE = Decimal("9999999999.99999999")


def validate_positive_decimal(value: Decimal, error: type[InvalidValueError]) -> None:
    """Exige Decimal finito e maior que zero, levantando o erro informado."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise error("O valor deve ser Decimal, finito e positivo")


def validate_transaction_decimal(
    value: Decimal, error: type[InvalidValueError]
) -> None:
    """Rejeita valores fora de NUMERIC(18, 8), sem arredondá-los para caber.

    Zeros excedentes após a oitava casa são aceitos quando não alteram o valor.
    Essa validação precede a persistência e evita arredondamento silencioso.
    """
    validate_positive_decimal(value, error)
    with localcontext(CALCULATION_CONTEXT):
        if value > MAX_TRANSACTION_VALUE or value != value.quantize(
            TRANSACTION_QUANTUM
        ):
            raise error("O valor deve caber em NUMERIC(18, 8), sem arredondamento")
