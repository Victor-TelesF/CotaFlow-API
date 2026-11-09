"""Operações, reconstrução e avaliação da carteira do dono autenticado."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID

from application.exceptions import (
    CatalogAssetNotFound,
    InvalidToken,
    QuotesUnavailable,
    ValuationTooLarge,
)
from application.ports import QuoteProvider, UnitOfWork
from application.types import PortfolioValuation
from domain.assets.asset import normalize_ticker
from domain.enums import TransactionType
from domain.portfolio import Portfolio, Transaction


def register_transaction(
    user_id: UUID,
    ticker: str,
    quantity: Decimal,
    price: Decimal,
    transaction_type: TransactionType,
    transaction_date: date,
    uow_factory: Callable[[], UnitOfWork],
) -> Transaction:
    """Serializa escritas do usuário antes de ler e validar o histórico inteiro.

    A operação nova sem sequência entra depois das persistidas na mesma data.
    Validação e INSERT ocorrem sob o mesmo bloqueio até commit/rollback; não
    basta bloquear o INSERT depois de calcular o saldo. Nenhuma cotação é usada.
    """
    ticker = normalize_ticker(ticker)
    with uow_factory() as uow:
        if not uow.users.lock(user_id):
            raise InvalidToken
        asset = uow.assets.get(ticker)
        if asset is None:
            raise CatalogAssetNotFound
        transaction = Transaction(
            asset, quantity, price, transaction_type, transaction_date
        )
        # Uma única reconstrução valida o candidato e toda a linha do tempo.
        Portfolio([*uow.transactions.list_for_user(user_id), transaction])
        persisted = uow.transactions.add(user_id, transaction)
        uow.commit()
    return persisted


def list_transactions(
    user_id: UUID,
    limit: int,
    offset: int,
    uow_factory: Callable[[], UnitOfWork],
) -> list[Transaction]:
    """Paginação exclusiva da leitura HTTP, nunca da validação financeira."""
    with uow_factory() as uow:
        return uow.transactions.list_page(user_id, limit, offset)


def load_portfolio(
    user_id: UUID,
    uow_factory: Callable[[], UnitOfWork],
) -> Portfolio:
    """Reconstrói uma fotografia completa, compartilhada com a avaliação.

    O repositório carrega operações e ativos juntos, sem paginação. Os fatos
    já estão separados do ORM quando a UoW fecha; nenhum recurso de banco
    acompanha a carteira retornada. Fórmulas permanecem somente no domínio.
    """
    with uow_factory() as uow:
        transactions = uow.transactions.list_for_user(user_id)
    return Portfolio(transactions)


def evaluate_portfolio(
    user_id: UUID,
    uow_factory: Callable[[], UnitOfWork],
    quotes: QuoteProvider,
    max_tickers: int,
) -> PortfolioValuation:
    """Libera o banco antes do HTTP e exige preços para todas as posições."""
    portfolio = load_portfolio(user_id, uow_factory)
    tickers = set(portfolio.positions)
    if len(tickers) > max_tickers:
        raise ValuationTooLarge
    snapshot = quotes.get_quotes(tickers) if tickers else {}
    missing = tickers - snapshot.keys()
    if missing:
        raise QuotesUnavailable(missing)
    return PortfolioValuation(portfolio, snapshot)
