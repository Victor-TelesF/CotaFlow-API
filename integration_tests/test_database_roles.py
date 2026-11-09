"""Exercise a future external-migration/DML-only role design, not runtime config."""

from datetime import date
from decimal import Decimal
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker

from application.portfolio import register_transaction
from application.types import User
from domain.assets.asset import Asset
from domain.enums import AssetType, TransactionType
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from integration_tests.support import migrate


def test_separate_roles_support_identity_writes_but_deny_ddl(postgres_engine):
    suffix = uuid4().hex
    owner, api = f"cf_owner_{suffix}", f"cf_api_{suffix}"
    engines = []
    with postgres_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as admin:
        try:
            for role in (owner, api):
                admin.exec_driver_sql(
                    f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'synthetic-role-password'"
                )
            database = postgres_engine.url.database
            admin.exec_driver_sql(
                f'GRANT CONNECT, CREATE ON DATABASE "{database}" TO {owner}'
            )
            owner_engine = create_engine(
                postgres_engine.url.set(
                    username=owner, password="synthetic-role-password"
                ),
                hide_parameters=True,
            )
            engines.append(owner_engine)
            migrate(owner_engine)
            admin.exec_driver_sql(f"GRANT USAGE ON SCHEMA cotaflow TO {api}")
            admin.exec_driver_sql(
                f"GRANT SELECT, INSERT ON cotaflow.users, cotaflow.assets, cotaflow.transactions TO {api}"
            )
            # SELECT FOR UPDATE used to serialize writes requires UPDATE privilege.
            admin.exec_driver_sql(f"GRANT UPDATE ON cotaflow.users TO {api}")
            admin.exec_driver_sql(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA cotaflow TO {api}"
            )
            api_engine = create_engine(
                postgres_engine.url.set(
                    username=api, password="synthetic-role-password"
                ),
                hide_parameters=True,
            )
            engines.append(api_engine)
            factory = sessionmaker(api_engine, autoflush=False, close_resets_only=False)
            user = User(email="synthetic@example.test", password_hash="synthetic-hash")
            asset = Asset("Synthetic asset", "TEST3", AssetType.STOCK)
            with SqlAlchemyUnitOfWork(factory) as uow:
                uow.users.add(user)
                uow.assets.add(asset)
                uow.commit()
            register_transaction(
                user.id,
                "TEST3",
                Decimal(10),
                Decimal(30),
                TransactionType.BUY,
                date(2026, 1, 1),
                partial(SqlAlchemyUnitOfWork, factory),
            )
            with api_engine.connect() as connection:
                assert connection.execute(
                    text("SELECT quantity, price, sequence FROM cotaflow.transactions")
                ).all() == [(Decimal(10), Decimal(30), 1)]
                assert not connection.scalar(
                    text(
                        "SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user"
                    )
                )
            for statement in (
                "CREATE TABLE cotaflow.forbidden (id int)",
                "ALTER TABLE cotaflow.transactions ADD COLUMN forbidden int",
                "DROP TABLE cotaflow.transactions",
                "SELECT * FROM cotaflow.alembic_version",
            ):
                with (
                    api_engine.begin() as connection,
                    pytest.raises(ProgrammingError) as caught,
                ):
                    connection.exec_driver_sql(statement)
                assert caught.value.orig.sqlstate == "42501"
            assert "forbidden" not in inspect(postgres_engine).get_table_names(
                schema="cotaflow"
            )
            with postgres_engine.connect() as connection:
                assert (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM cotaflow.transactions"
                    ).scalar_one()
                    == 1
                )
        finally:
            for engine in engines:
                engine.dispose()
            # The fixture created this unique database; only test-owned rows/roles.
            admin.exec_driver_sql("DROP SCHEMA IF EXISTS cotaflow CASCADE")
            for role in (api, owner):
                admin.exec_driver_sql(f"DROP OWNED BY {role}")
                admin.exec_driver_sql(f"DROP ROLE {role}")
