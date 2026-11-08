"""Reconstrói quantidade e preço médio de um ativo a partir do histórico.

O histórico é a fonte de verdade: posições e custos não são persistidos.
Compras ponderam a média, vendas parciais a preservam e zeragem encerra o
ciclo, permitindo que uma recompra inicie outro custo.
"""

from decimal import Decimal, localcontext

from ..assets import Asset
from ..decimal_values import CALCULATION_CONTEXT, validate_positive_decimal
from ..enums import TransactionType
from ..exceptions import InsufficientBalanceError, InvalidPriceError, InvalidValueError
from .transaction import Transaction, ordered_transactions


class Position:
    """Posição de um único ativo, validada em toda a linha do tempo.

    Mesmo uma operação retroativa precisa deixar saldo não negativo em cada
    ponto do histórico, inclusive em ciclos já encerrados. Os cálculos usam
    o contexto Decimal do domínio, sem depender da precisão global do chamador.
    """

    def __init__(self, asset: Asset, transaction_list: list[Transaction] | None = None):
        """Copia, ordena e valida o histórico recebido, sem acessar banco ou rede."""
        if not isinstance(asset, Asset):
            raise InvalidValueError("Asset deve ser uma instância de Asset")
        self._asset = asset
        transactions = ordered_transactions(list(transaction_list or []))
        self._calculate(transactions)
        self._transaction_list = transactions

    @property
    def asset(self) -> Asset:
        """Retorna a identidade imutável do ativo desta posição."""
        return self._asset

    @property
    def transaction_list(self) -> list[Transaction]:
        """Retorna uma cópia da lista; suas operações permanecem imutáveis."""
        return self._transaction_list.copy()

    def _calculate(self, transactions: list[Transaction]) -> tuple[Decimal, Decimal]:
        """Percorre o histórico ordenado e retorna quantidade e média finais.

        Rejeita ativo divergente ou saldo negativo no ponto em que aparecem;
        não altera o histórico da posição quando o candidato é inválido.
        """
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
        """Aceita uma operação somente se o histórico completo continuar válido."""
        candidates = ordered_transactions([*self._transaction_list, transaction])
        self._calculate(candidates)
        self._transaction_list = candidates

    @property
    def quantity(self) -> Decimal:
        """Reconstrói a quantidade aberta após todas as compras e vendas."""
        return self._calculate(self._transaction_list)[0]

    @property
    def average_price(self) -> Decimal:
        """Reconstrói a média do ciclo aberto; posição zerada tem média zero."""
        return self._calculate(self._transaction_list)[1]

    @property
    def cost(self) -> Decimal:
        """Calcula o custo da posição aberta: quantidade vezes preço médio."""
        with localcontext(CALCULATION_CONTEXT):
            quantity, average = self._calculate(self._transaction_list)
            return quantity * average

    def market_value(self, price: Decimal) -> Decimal:
        """Avalia a quantidade aberta com preço Decimal positivo e finito fornecido.

        Não busca cotação e não altera preço médio ou histórico da operação.
        """
        validate_positive_decimal(price, InvalidPriceError)
        with localcontext(CALCULATION_CONTEXT):
            return self.quantity * price
