"""Leitura e escrita de fatos na sessão recebida da unidade de trabalho.

``add`` usa flush para enviar SQL e detectar constraints dentro da transação;
isso não confirma dados. Somente a unidade de trabalho pode fazer commit.
Consultas devolvem objetos de aplicação/domínio, sem expor entidades ORM.
Unicidade de email é traduzida para a falha esperada da aplicação. Demais
falhas SQL são propagadas para rollback e tradução segura na fronteira HTTP.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.exceptions import EmailAlreadyRegistered
from application.types import User
from domain.assets import Asset
from domain.portfolio import Transaction
from infrastructure.database.mappers import to_asset, to_transaction, to_user
from infrastructure.database.models import AssetModel, TransactionModel, UserModel


class SqlAlchemyUserRepository:
    """Persiste identidades; não normaliza email, gera hash ou verifica senha."""

    def __init__(self, session: Session):
        """Compartilha a sessão da unidade de trabalho, sem assumir seu fechamento."""
        self._session = session

    def add(self, user: User) -> None:
        """Insere a identidade com email canônico e hash previamente preparados."""
        self._session.add(
            UserModel(id=user.id, email=user.email, password_hash=user.password_hash)
        )
        try:
            self._session.flush()
        except IntegrityError as error:
            original = error.orig
            if (
                getattr(original, "sqlstate", None) == "23505"
                and getattr(getattr(original, "diag", None), "constraint_name", None)
                == "uq_users_email"
            ):
                raise EmailAlreadyRegistered from None
            raise

    def get(self, user_id: UUID) -> User | None:
        """Busca a identidade pelo UUID; ausência resulta em None."""
        row = self._session.get(UserModel, user_id)
        return to_user(row) if row is not None else None

    def get_by_email(self, email: str) -> User | None:
        """Busca por correspondência exata do email já normalizado pelo chamador."""
        row = self._session.scalar(select(UserModel).where(UserModel.email == email))
        return to_user(row) if row is not None else None


class SqlAlchemyAssetRepository:
    """Persiste o catálogo validado pelo domínio, sem consultar cotações."""

    def __init__(self, session: Session):
        """Recebe a sessão que participa da mesma transação dos demais repositórios."""
        self._session = session

    def add(self, asset: Asset) -> None:
        """Insere nome, ticker e tipo; duplicatas são rejeitadas pelo banco."""
        self._session.add(
            AssetModel(
                ticker=asset.ticker, name=asset.name, asset_type=asset.asset_type.value
            )
        )
        self._session.flush()

    def get(self, ticker: str) -> Asset | None:
        """Busca o ticker canônico exato; não faz pesquisa em provedor externo."""
        row = self._session.get(AssetModel, ticker)
        return to_asset(row) if row is not None else None


class SqlAlchemyTransactionRepository:
    """Armazena operações e carrega o histórico completo de um usuário.

    O chamador determina o usuário autenticado e valida a carteira antes da
    escrita. Este adaptador não autoriza acessos nem garante saldo suficiente.
    """

    def __init__(self, session: Session):
        """Recebe a sessão transacional; não cria uma sessão por operação."""
        self._session = session

    def add(self, user_id: UUID, transaction: Transaction) -> Transaction:
        """Grava uma operação nova e devolve outra instância com sequência persistida.

        O UUID, o preço e a data são preservados. A sequência é atribuída pelo
        banco, independentemente da sequência de entrada; o objeto imutável
        recebido permanece intacto. Usuário e ativo precisam existir.
        """
        row = TransactionModel(
            id=transaction.id_transaction,
            user_id=user_id,
            asset_ticker=transaction.asset.ticker,
            quantity=transaction.quantity,
            price=transaction.price,
            transaction_type=transaction.transaction_type.value,
            transaction_date=transaction.transaction_date,
        )
        self._session.add(row)
        self._session.flush()
        asset = self._session.get(AssetModel, row.asset_ticker)
        if asset is None:
            raise RuntimeError("Persisted transaction has no catalog asset")
        return to_transaction(row, asset)

    def list_for_user(self, user_id: UUID) -> list[Transaction]:
        """Carrega operações e ativos em uma consulta, por data e sequência.

        Retorna todo o histórico, inclusive ciclos zerados, para reconstruir a
        carteira sem truncamento por paginação. Não calcula posição ou saldo.
        """
        rows = self._session.execute(
            select(TransactionModel, AssetModel)
            .join(AssetModel, TransactionModel.asset_ticker == AssetModel.ticker)
            .where(TransactionModel.user_id == user_id)
            .order_by(TransactionModel.transaction_date, TransactionModel.sequence)
        )
        return [to_transaction(transaction, asset) for transaction, asset in rows]
