from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal

import pytest

from domain.enums import TransactionType
from domain.exceptions import (
    InvalidPriceError,
    InvalidQuantityError,
    InvalidTransactionDateError,
    InvalidValueError,
)
from domain.portfolio import Transaction


@pytest.mark.parametrize(
    "value",
    [
        Decimal(-1),
        Decimal(0),
        Decimal("-Infinity"),
        Decimal("sNaN"),
        Decimal(10000000000),
        Decimal("0.000000001"),
        1.5,
    ],
)
@pytest.mark.parametrize(
    "field, error", [("quantity", InvalidQuantityError), ("price", InvalidPriceError)]
)
def test_values_reject_invalid_sign_type_precision_and_range(
    petr4, past_date, field, value, error
):
    values = {"quantity": Decimal(1), "price": Decimal(1)}
    values[field] = value
    with pytest.raises(error):
        Transaction(
            petr4,
            transaction_type=TransactionType.BUY,
            transaction_date=past_date,
            **values,
        )


def test_minimum_fraction_and_redundant_trailing_zeroes_are_exact(petr4, past_date):
    transaction = Transaction(
        petr4,
        Decimal("0.00000001"),
        Decimal("1.000000000"),
        TransactionType.BUY,
        past_date,
    )
    assert transaction.quantity == Decimal("0.00000001")
    assert transaction.price == Decimal(1)


@pytest.mark.parametrize("sequence", [0, -1, True, "1", 1.5])
def test_sequence_requires_a_positive_integer(petr4, past_date, sequence):
    with pytest.raises(InvalidValueError, match="Sequência"):
        Transaction(
            petr4, Decimal(1), Decimal(1), TransactionType.BUY, past_date, sequence
        )


def test_datetime_is_rejected_as_date_with_domain_error(petr4):
    with pytest.raises(InvalidTransactionDateError):
        Transaction(
            petr4, Decimal(1), Decimal(1), TransactionType.BUY, datetime(2026, 1, 5)
        )


def test_historical_price_and_sequence_are_immutable(petr4, past_date):
    transaction = Transaction(
        petr4, Decimal(1), Decimal(30), TransactionType.BUY, past_date, 100
    )
    with pytest.raises(FrozenInstanceError):
        transaction.price = Decimal(42)
    with pytest.raises(FrozenInstanceError):
        transaction.transaction_sequence = 101
    assert transaction.price == Decimal(30)
    assert transaction.transaction_sequence == 100
