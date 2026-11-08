"""Adapted from FinTrack fcfd48b; original preserved in the local reference."""

from decimal import Decimal
from uuid import UUID

import pytest

from domain.enums import TransactionType
from domain.exceptions import (
    InvalidPriceError,
    InvalidQuantityError,
    InvalidTransactionDateError,
    InvalidTransactionTypeError,
    InvalidValueError,
)
from domain.portfolio.transaction import Transaction


class TestTransactionId:
    def test_id_is_generated_automatically(self, petr4, past_date):
        tx = Transaction(
            petr4, Decimal(10), Decimal(35), TransactionType.BUY, past_date
        )
        assert isinstance(tx.id_transaction, UUID)

    def test_two_transactions_have_different_ids(self, petr4, past_date):
        tx1 = Transaction(
            petr4, Decimal(10), Decimal(35), TransactionType.BUY, past_date
        )
        tx2 = Transaction(
            petr4, Decimal(10), Decimal(35), TransactionType.BUY, past_date
        )
        assert tx1.id_transaction != tx2.id_transaction


class TestTransactionAssetValidation:
    def test_non_asset_raises(self, past_date):
        with pytest.raises(InvalidValueError):
            Transaction(
                "PETR4", Decimal(10), Decimal(35), TransactionType.BUY, past_date
            )


class TestTransactionQuantityValidation:
    def test_non_decimal_quantity_raises(self, petr4, past_date):
        with pytest.raises(InvalidQuantityError):
            Transaction(
                petr4, 10, Decimal(35), TransactionType.BUY, past_date
            )  # int, não Decimal

    def test_zero_quantity_raises(self, petr4, past_date):
        with pytest.raises(InvalidQuantityError):
            Transaction(petr4, Decimal(0), Decimal(35), TransactionType.BUY, past_date)

    def test_negative_quantity_raises(self, petr4, past_date):
        with pytest.raises(InvalidQuantityError):
            Transaction(petr4, Decimal(-5), Decimal(35), TransactionType.BUY, past_date)

    def test_fractional_quantity_is_valid(self, petr4, past_date):
        """A quantidade decimal positiva faz parte do contrato inicial."""
        tx = Transaction(
            petr4, Decimal("0.005"), Decimal(35), TransactionType.BUY, past_date
        )
        assert tx.quantity == Decimal("0.005")


class TestTransactionPriceValidation:
    def test_non_decimal_price_raises(self, petr4, past_date):
        with pytest.raises(InvalidPriceError):
            Transaction(petr4, Decimal(10), 35.5, TransactionType.BUY, past_date)

    def test_zero_price_raises(self, petr4, past_date):
        with pytest.raises(InvalidPriceError):
            Transaction(petr4, Decimal(10), Decimal(0), TransactionType.BUY, past_date)


class TestTransactionTypeValidation:
    def test_non_enum_type_raises(self, petr4, past_date):
        with pytest.raises(InvalidTransactionTypeError):
            Transaction(petr4, Decimal(10), Decimal(35), "buy", past_date)


class TestTransactionDateValidation:
    def test_non_date_raises(self, petr4):
        with pytest.raises(InvalidTransactionDateError):
            Transaction(
                petr4, Decimal(10), Decimal(35), TransactionType.BUY, "2026-01-05"
            )

    def test_future_date_raises(self, petr4, future_date):
        with pytest.raises(InvalidTransactionDateError):
            Transaction(
                petr4, Decimal(10), Decimal(35), TransactionType.BUY, future_date
            )

    def test_today_is_valid(self, petr4, frozen_today):
        tx = Transaction(
            petr4, Decimal(10), Decimal(35), TransactionType.BUY, frozen_today
        )
        assert tx.transaction_date == frozen_today
