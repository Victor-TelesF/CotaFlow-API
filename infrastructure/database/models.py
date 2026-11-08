"""Mapeamento das três tabelas, sem persistir posições ou cálculos derivados.

Usuário identifica o dono do histórico; ticker identifica o ativo do catálogo.
Transações preservam UUID, data e preço da operação e recebem uma sequência
do PostgreSQL para desempatar datas iguais. Saldo histórico e data futura são
validados pelo domínio, não por estes modelos.

Quantidade e preço usam NUMERIC(18, 8). O domínio precisa rejeitar previamente
uma escala incompatível, pois o PostgreSQL pode arredondar casas excedentes
ao converter um valor para esse tipo. A migration define o schema instalado;
declarar modelos não cria tabelas durante a inicialização da API.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_SCHEMA = "cotaflow"


class Base(DeclarativeBase):
    """Registro de metadados compartilhado pelos modelos e pelo Alembic."""

    metadata = MetaData(schema=DATABASE_SCHEMA)


class UserModel(Base):
    """Identidade com email canônico único e hash fornecido pela autenticação.

    A constraint exige email não vazio, sem espaços nas pontas e em minúsculas.
    Validação de formato e geração/verificação do hash ficam no cadastro/login;
    a constraint de hash apenas rejeita uma string vazia ou só com espaços.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            "email = lower(btrim(email)) AND email <> ''", name="ck_users_email"
        ),
        CheckConstraint("btrim(password_hash) <> ''", name="ck_users_password_hash"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)


class AssetModel(Base):
    """Catálogo local de ações/FIIs, identificado pelo ticker normalizado.

    Não contém quantidade, custo ou preço atual. O domínio normaliza nome e
    ticker antes da escrita; as constraints protegem a forma armazenada.
    """

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "ticker = upper(btrim(ticker)) AND ticker <> ''", name="ck_assets_ticker"
        ),
        CheckConstraint("name = btrim(name) AND name <> ''", name="ck_assets_name"),
        CheckConstraint("asset_type IN ('stock', 'fii')", name="ck_assets_type"),
    )

    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    asset_type: Mapped[str] = mapped_column(Text)


class TransactionModel(Base):
    """Fato de compra/venda vinculado ao usuário e ao ativo existentes.

    UUID identifica a operação; a sequência global, positiva e única é gerada
    pelo banco. Lacunas após rollback são normais: ela ordena o histórico e
    não representa quantidade de operações. Não há edição/exclusão exposta
    pelos repositórios, nem validação de saldo nesta tabela.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("sequence", name="uq_transactions_sequence"),
        CheckConstraint("sequence > 0", name="ck_transactions_sequence"),
        CheckConstraint(
            "transaction_type IN ('buy', 'sell')", name="ck_transactions_type"
        ),
        CheckConstraint(
            "quantity > 0 AND quantity <= 9999999999.99999999",
            name="ck_transactions_quantity",
        ),
        CheckConstraint(
            "price > 0 AND price <= 9999999999.99999999", name="ck_transactions_price"
        ),
        Index(
            "ix_transactions_user_history", "user_id", "transaction_date", "sequence"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", name="fk_transactions_user")
    )
    asset_ticker: Mapped[str] = mapped_column(
        ForeignKey("assets.ticker", name="fk_transactions_asset")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    transaction_type: Mapped[str] = mapped_column(Text)
    transaction_date: Mapped[date] = mapped_column(Date)
