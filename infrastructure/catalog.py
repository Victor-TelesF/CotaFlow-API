"""Ferramenta local: python -m infrastructure.catalog audit | add --help.

Usa DB_* do ambiente e a mesma UoW da API. Não aplica migrations, não consulta
mercado, não altera ativos existentes nem imprime dados/erros do driver.
"""

import argparse
import json
from functools import partial

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from application.assets import register_asset
from application.exceptions import AssetAlreadyRegistered
from domain.assets.asset import normalize_asset_name, normalize_ticker
from domain.enums import AssetType
from domain.exceptions import InvalidValueError
from infrastructure.config import Settings
from infrastructure.database.session import Database
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


def audit_catalog(engine, max_tickers: int) -> dict[str, int]:
    """Consulta agregados em transação read-only; não hidrata domínio incompatível."""
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        connection.execute(text("SET LOCAL lock_timeout = '1s'"))
        counts = {"invalid_tickers": 0, "invalid_names": 0}
        # Streaming limita memória e usa a mesma semântica Unicode/strip do domínio.
        rows = connection.execution_options(yield_per=256).execute(
            text("SELECT ticker, name FROM cotaflow.assets")
        )
        for ticker, name in rows:
            try:
                if normalize_ticker(ticker) != ticker:
                    counts["invalid_tickers"] += 1
            except InvalidValueError:
                counts["invalid_tickers"] += 1
            try:
                normalize_asset_name(name)
            except InvalidValueError:
                counts["invalid_names"] += 1
        oversized = connection.scalar(
            text(
                "SELECT count(*) FROM (SELECT user_id FROM ("
                "SELECT user_id, asset_ticker FROM cotaflow.transactions "
                "GROUP BY user_id, asset_ticker HAVING sum("
                "CASE WHEN transaction_type='buy' THEN quantity ELSE -quantity END)>0"
                ") positions GROUP BY user_id HAVING count(*)>:maximum) portfolios"
            ),
            {"maximum": max_tickers},
        )
    return counts | {"oversized_portfolios": oversized}


def main(argv=None, *, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain the curated local catalog")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser(
        "audit", help="Read-only compatibility counts before rollout"
    )
    audit.add_argument("--max-tickers", type=int, default=20)
    add = commands.add_parser(
        "add", help="Include one reviewed asset without market calls"
    )
    add.add_argument("--ticker", required=True)
    add.add_argument("--name", required=True)
    add.add_argument(
        "--type", required=True, choices=[kind.value for kind in AssetType]
    )
    args = parser.parse_args(argv)
    if args.command == "audit" and args.max_tickers < 1:
        parser.error("max-tickers must be positive")
    database = None
    try:
        database = Database(settings if settings is not None else Settings())
        if args.command == "audit":
            counts = audit_catalog(database.engine, args.max_tickers)
            print(json.dumps(counts))
            return int(any(counts.values()))
        register_asset(
            args.ticker,
            args.name,
            AssetType(args.type),
            partial(SqlAlchemyUnitOfWork, database.session_factory),
        )
        print("Asset added")
        return 0
    except InvalidValueError:
        print("Invalid asset data")
        return 2
    except AssetAlreadyRegistered:
        print("Asset already registered; existing data preserved")
        return 1
    except SQLAlchemyError:
        print("Catalog database unavailable; details withheld")
        return 1
    finally:
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
