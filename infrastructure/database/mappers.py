"""Converte linhas ORM em objetos independentes da sessão e do SQLAlchemy.

O histórico é reconstruído com os construtores do domínio, preservando UUID,
sequência, Decimal, preço e data. A conversão não recalcula indicadores e não
consulta o banco; a linha do ativo deve vir carregada pelo repositório.
"""

from application.types import User
from domain.assets import Asset
from domain.enums import AssetType, TransactionType
from domain.portfolio import Transaction
from infrastructure.database.models import AssetModel, TransactionModel, UserModel


def to_user(row: UserModel) -> User:
    """Restaura a identidade e o hash armazenados, sem autenticar o usuário."""
    return User(id=row.id, email=row.email, password_hash=row.password_hash)


def to_asset(row: AssetModel) -> Asset:
    """Converte o tipo textual para o enum e aplica as invariantes de Asset."""
    return Asset(name=row.name, ticker=row.ticker, asset_type=AssetType(row.asset_type))


def to_transaction(row: TransactionModel, asset: AssetModel) -> Transaction:
    """Restaura uma operação com identidade e ordem originais, sem gerar outro ID.

    Quantidade, preço e data passam novamente pela validação do domínio;
    dados persistidos incompatíveis não viram uma operação silenciosamente.
    """
    return Transaction(
        asset=to_asset(asset),
        quantity=row.quantity,
        price=row.price,
        transaction_type=TransactionType(row.transaction_type),
        transaction_date=row.transaction_date,
        transaction_sequence=row.sequence,
        id_transaction=row.id,
    )
