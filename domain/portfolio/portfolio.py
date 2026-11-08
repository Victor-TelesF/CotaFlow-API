"""Carteira implícita reconstruída com o histórico completo de um usuário.

Não conhece usuário autenticado, repositório ou provedor. A aplicação carrega
os fatos do dono correto e, para avaliação, fornece um mapa completo de preços.
A mesma implementação de reconstrução atende consultas de custo e de avaliação.
"""

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
    """Coordena posições por ticker e indicadores das posições ainda abertas.

    Preserva operações de ciclos zerados no histórico, mas omite essas posições
    dos indicadores atuais. Não calcula resultado realizado de vendas, tributos,
    dividendos ou saldo em dinheiro.
    """

    def __init__(self, transaction_list: list[Transaction] | None = None):
        """Copia e ordena o histórico, rejeitando qualquer saldo histórico inválido."""
        transactions = ordered_transactions(list(transaction_list or []))
        self._rebuild_positions(transactions)
        self._transaction_list = transactions

    @staticmethod
    def _rebuild_positions(transactions: list[Transaction]) -> dict[str, Position]:
        """Agrupa por ticker e delega invariantes/cálculos de cada ativo a Position."""
        grouped: dict[str, list[Transaction]] = {}
        for transaction in transactions:
            grouped.setdefault(transaction.asset.ticker, []).append(transaction)
        return {
            ticker: Position(history[0].asset, history)
            for ticker, history in grouped.items()
        }

    @property
    def transaction_list(self) -> list[Transaction]:
        """Retorna cópia do histórico completo, inclusive operações de ciclos zerados."""
        return self._transaction_list.copy()

    @property
    def positions(self) -> dict[str, Position]:
        """Reconstrói somente posições abertas em objetos independentes.

        Alterar uma posição retornada não modifica a carteira. O resultado
        é recalculado a partir do histórico; não é um cache de saldos.
        """
        return {
            ticker: position
            for ticker, position in self._rebuild_positions(
                self._transaction_list
            ).items()
            if position.quantity > 0
        }

    def add_transaction(self, transaction: Transaction) -> None:
        """Valida o histórico candidato inteiro antes de substituir o anterior.

        Em erro, mantém a carteira intacta. Essa validação inclui os efeitos
        posteriores de uma compra/venda retroativa, não apenas o saldo final.
        """
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
        """Cria e incorpora uma compra válida, retornando a operação imutável."""
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
        """Cria uma venda e rejeita saldo negativo em qualquer ponto do histórico."""
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
        """Soma os custos das posições abertas; carteira vazia resulta em zero."""
        with localcontext(CALCULATION_CONTEXT):
            return sum(
                (position.cost for position in self.positions.values()), Decimal(0)
            )

    def get_total_equity(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Soma valores de mercado usando o mapa de preços fornecido pela aplicação.

        Exige preço válido para cada posição aberta; ausência/valor inválido
        levanta erro em vez de devolver um total parcial aparentemente completo.
        Carteira vazia resulta em zero sem consultar qualquer serviço externo.
        """
        with localcontext(CALCULATION_CONTEXT):
            total = Decimal(0)
            for ticker, position in self.positions.items():
                if ticker not in prices:
                    raise MissingPriceError(f"Preço ausente para {ticker}")
                total += position.market_value(prices[ticker])
            return total

    def get_total_pnl(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Calcula valor de mercado menos custo, apenas nas posições abertas.

        É resultado não realizado; receitas de vendas não entram neste indicador.
        """
        with localcontext(CALCULATION_CONTEXT):
            return self.get_total_equity(prices) - self.get_total_cost()

    def get_unrealized_return(self, prices: Mapping[str, Decimal]) -> Decimal | None:
        """Retorna resultado não realizado/custo; 0.20 representa 20%.

        Sem custo, retorna None por não existir denominador para essa razão.
        Não representa retorno histórico ou rentabilidade realizada.
        """
        with localcontext(CALCULATION_CONTEXT):
            cost = self.get_total_cost()
            if cost == 0:
                return None
            return self.get_total_pnl(prices) / cost

    def get_pnl(self, ticker: str, prices: Mapping[str, Decimal]) -> Decimal:
        """Calcula o resultado não realizado de um ticker canônico com posição aberta.

        Ausência de posição ou de cotação gera erro de domínio específico.
        """
        position = self.positions.get(ticker)
        if position is None:
            raise AssetNotFoundError("O ticker não possui posição aberta")
        if ticker not in prices:
            raise MissingPriceError(f"Preço ausente para {ticker}")
        with localcontext(CALCULATION_CONTEXT):
            return position.market_value(prices[ticker]) - position.cost
