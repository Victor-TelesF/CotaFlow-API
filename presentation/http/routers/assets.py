"""Catálogo compartilhado, disponível somente com identidade autenticada."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from application.assets import list_assets
from domain.assets import Asset
from presentation.http.dependencies import CurrentUser
from presentation.http.pagination import PageOffset, validate_page_offset
from presentation.http.schemas.portfolio import AssetPage, AssetResponse

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


def asset_response(asset: Asset) -> AssetResponse:
    """Converte somente os campos públicos do catálogo."""
    return AssetResponse(ticker=asset.ticker, name=asset.name, type=asset.asset_type)


@router.get("", response_model=AssetPage, dependencies=[Depends(validate_page_offset)])
def get_assets(
    request: Request,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: PageOffset = 0,
):
    """Página em ordem de ticker com limite de cem itens."""
    assets = list_assets(limit, offset, request.app.state.uow_factory)
    return AssetPage(
        items=[asset_response(asset) for asset in assets], limit=limit, offset=offset
    )
