"""Cria usuários, catálogo e fatos de compra/venda sem indicadores derivados.

Inclui unicidade de email/ticker, tipos permitidos, valores positivos e finitos,
chaves estrangeiras e sequência estável. Regras de saldo e data futura ficam
no domínio. A reversão remove as três tabelas e seus dados; testes de reversão
devem usar exclusivamente banco/schema descartável.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Cria as três tabelas e o índice de leitura do histórico por usuário."""
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint(
            "email = lower(btrim(email)) AND email <> ''", name="ck_users_email"
        ),
        sa.CheckConstraint("btrim(password_hash) <> ''", name="ck_users_password_hash"),
    )
    op.create_table(
        "assets",
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("ticker"),
        sa.CheckConstraint(
            "ticker = upper(btrim(ticker)) AND ticker <> ''", name="ck_assets_ticker"
        ),
        sa.CheckConstraint("name = btrim(name) AND name <> ''", name="ck_assets_name"),
        sa.CheckConstraint("asset_type IN ('stock', 'fii')", name="ck_assets_type"),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("asset_ticker", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("price", sa.Numeric(18, 8), nullable=False),
        sa.Column("transaction_type", sa.Text(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence", name="uq_transactions_sequence"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_transactions_user"),
        sa.ForeignKeyConstraint(
            ["asset_ticker"], ["assets.ticker"], name="fk_transactions_asset"
        ),
        sa.CheckConstraint("sequence > 0", name="ck_transactions_sequence"),
        sa.CheckConstraint(
            "transaction_type IN ('buy', 'sell')", name="ck_transactions_type"
        ),
        sa.CheckConstraint(
            "quantity > 0 AND quantity <= 9999999999.99999999",
            name="ck_transactions_quantity",
        ),
        sa.CheckConstraint(
            "price > 0 AND price <= 9999999999.99999999", name="ck_transactions_price"
        ),
    )
    op.create_index(
        "ix_transactions_user_history",
        "transactions",
        ["user_id", "transaction_date", "sequence"],
    )


def downgrade() -> None:
    """Remove primeiro o histórico, depois ativos e usuários, respeitando FKs."""
    op.drop_index("ix_transactions_user_history", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("assets")
    op.drop_table("users")
