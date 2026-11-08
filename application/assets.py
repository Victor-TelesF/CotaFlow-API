"""Catálogo compartilhado; cadastro não consulta nem certifica dados de mercado."""

from collections.abc import Callable

from application.ports import UnitOfWork
from domain.assets import Asset
from domain.enums import AssetType


def register_asset(
    ticker: str,
    name: str,
    asset_type: AssetType,
    uow_factory: Callable[[], UnitOfWork],
) -> Asset:
    """Normaliza no domínio e confia na unicidade do banco inclusive em corrida."""
    asset = Asset(name=name, ticker=ticker, asset_type=asset_type)
    with uow_factory() as uow:
        uow.assets.add(asset)
        uow.commit()
    return asset


def list_assets(
    limit: int,
    offset: int,
    uow_factory: Callable[[], UnitOfWork],
) -> list[Asset]:
    """Consulta uma página do catálogo local sem provedor externo."""
    with uow_factory() as uow:
        return uow.assets.list_page(limit, offset)
