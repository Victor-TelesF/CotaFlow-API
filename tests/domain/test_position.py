"""Adapted from FinTrack fcfd48b; original preserved in the local reference."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from domain.enums import TransactionType
from domain.exceptions import InsufficientBalanceError, InvalidValueError
from domain.portfolio.position import Position
from domain.portfolio.transaction import Transaction


def make_tx(asset, quantity, price, tx_type, day):
    return Transaction(
        asset, Decimal(quantity), Decimal(price), tx_type, date(2026, 1, day)
    )


class TestPositionQuantity:
    def test_empty_position_has_zero_quantity(self, petr4):
        position = Position(petr4)
        assert position.quantity == Decimal(0)

    def test_quantity_sums_buys_and_subtracts_sells(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))
        position.add_transaction(make_tx(petr4, "3", "35", TransactionType.SELL, 10))
        assert position.quantity == Decimal(7)


class TestPositionAveragePrice:
    def test_new_buy_follows_persisted_same_day_sale(self, petr4):
        buy = make_tx(petr4, "10", "30", TransactionType.BUY, 5)
        buy = replace(buy, transaction_sequence=100)
        sell = make_tx(petr4, "5", "50", TransactionType.SELL, 5)
        sell = replace(sell, transaction_sequence=101)
        position = Position(petr4, [buy, sell])

        position.add_transaction(make_tx(petr4, "5", "40", TransactionType.BUY, 5))

        assert position.quantity == Decimal(10)
        assert position.average_price == Decimal(35)

    def test_single_buy_sets_average_price_to_purchase_price(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))
        assert position.average_price == Decimal(30)

    def test_two_buys_weighted_average(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))
        position.add_transaction(make_tx(petr4, "5", "40", TransactionType.BUY, 10))
        # (10*30 + 5*40) / 15 = 500/15 ≈ 33.333...
        expected = Decimal("33.333333333333333333333333333333333333333333333333")
        assert position.average_price == expected

    def test_sell_does_not_change_average_price(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))
        position.add_transaction(make_tx(petr4, "4", "50", TransactionType.SELL, 10))
        assert position.average_price == Decimal(30)

    def test_sell_between_two_buys_does_not_corrupt_average(self, petr4):
        """Regressão do bug de raciocínio identificado pela revisão externa:
        processar em ordem cronológica é obrigatório."""
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 1))
        position.add_transaction(
            make_tx(petr4, "5", "100", TransactionType.SELL, 5)
        )  # não afeta average_price
        position.add_transaction(make_tx(petr4, "5", "40", TransactionType.BUY, 10))
        # Após a 1ª compra: qty=10, avg=30
        # Após a venda: qty=5, avg=30 (inalterado)
        # Após a 2ª compra: (5*30 + 5*40) / 10 = 350/10 = 35
        assert position.average_price == Decimal(35)

    def test_transactions_out_of_insertion_order_still_process_chronologically(
        self, petr4
    ):
        """add_transaction é chamado fora de ordem cronológica —
        average_price deve mesmo assim respeitar transaction_date, não a ordem de inserção."""
        position = Position(petr4)
        tx_later = make_tx(petr4, "5", "40", TransactionType.BUY, 10)
        tx_earlier = make_tx(petr4, "10", "30", TransactionType.BUY, 1)
        position.add_transaction(tx_later)
        position.add_transaction(tx_earlier)
        expected = Decimal("33.333333333333333333333333333333333333333333333333")
        assert position.average_price == expected


class TestPositionAddTransaction:
    @pytest.mark.parametrize("sell_quantity", ["4", "10"])
    def test_new_sale_follows_persisted_same_day_buy(self, petr4, sell_quantity):
        buy = make_tx(petr4, "10", "30", TransactionType.BUY, 5)
        buy = replace(buy, transaction_sequence=100)
        position = Position(petr4, [buy])

        position.add_transaction(
            make_tx(petr4, sell_quantity, "50", TransactionType.SELL, 5)
        )

        assert position.quantity == Decimal(10) - Decimal(sell_quantity)
        assert position.average_price == (
            Decimal(30) if sell_quantity == "4" else Decimal(0)
        )

    @pytest.mark.parametrize("sell_quantity, sell_day", [("11", 5), ("10", 4)])
    def test_invalid_sale_after_persisted_buy_preserves_position(
        self, petr4, sell_quantity, sell_day
    ):
        buy = make_tx(petr4, "10", "30", TransactionType.BUY, 5)
        buy = replace(buy, transaction_sequence=100)
        position = Position(petr4, [buy])

        with pytest.raises(InsufficientBalanceError):
            position.add_transaction(
                make_tx(petr4, sell_quantity, "40", TransactionType.SELL, sell_day)
            )

        assert position.quantity == Decimal(10)
        assert position.transaction_list == [buy]

    def test_rejects_non_transaction(self, petr4):
        position = Position(petr4)
        with pytest.raises(InvalidValueError):
            position.add_transaction("not a transaction")

    def test_rejects_transaction_of_different_asset(self, petr4, vale3):
        position = Position(petr4)
        with pytest.raises(InvalidValueError):
            position.add_transaction(make_tx(vale3, "10", "68", TransactionType.BUY, 5))

    def test_sell_more_than_owned_raises(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))
        with pytest.raises(InsufficientBalanceError):
            position.add_transaction(
                make_tx(petr4, "15", "35", TransactionType.SELL, 10)
            )

    def test_sell_before_existing_buy_raises(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))
        with pytest.raises(InsufficientBalanceError):
            position.add_transaction(
                make_tx(petr4, "10", "35", TransactionType.SELL, 1)
            )

    def test_sell_exact_owned_quantity_is_allowed(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))
        position.add_transaction(make_tx(petr4, "10", "35", TransactionType.SELL, 10))
        assert position.quantity == Decimal(0)


class TestPositionTransactionList:
    def test_returns_defensive_copy(self, petr4):
        position = Position(petr4)
        position.add_transaction(make_tx(petr4, "10", "30", TransactionType.BUY, 5))

        stolen_list = position.transaction_list
        stolen_list.clear()

        assert len(position.transaction_list) == 1

    def test_init_with_initial_list_validates_each_transaction(self, petr4, vale3):
        valid_tx = make_tx(petr4, "10", "30", TransactionType.BUY, 5)
        invalid_tx = make_tx(vale3, "10", "68", TransactionType.BUY, 5)  # ativo errado

        with pytest.raises(InvalidValueError):
            Position(petr4, transaction_list=[valid_tx, invalid_tx])

    def test_init_without_transaction_list_starts_empty(self, petr4):
        position = Position(petr4)
        assert position.transaction_list == []
