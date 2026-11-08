"""Catálogo compartilhado, disponível somente com identidade autenticada."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from application.assets import list_assets, register_asset
from domain.assets import Asset
from presentation.http.dependencies import CurrentUser
from presentation.http.schemas.errors import ErrorResponse
from presentation.http.schemas.portfolio import AssetPage, AssetRequest, AssetResponse

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


def asset_response(asset: Asset) -> AssetResponse:
    """Converte somente os campos públicos do catálogo."""
    return AssetResponse(ticker=asset.ticker, name=asset.name, type=asset.asset_type)


@router.post(
    "",
    status_code=201,
    response_model=AssetResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Asset already registered"}
    },
)
def create_asset(body: AssetRequest, request: Request, user: CurrentUser):
    """Cadastra sem consultar mercado; duplicata canônica retorna 409."""
    return asset_response(
        register_asset(body.ticker, body.name, body.type, request.app.state.uow_factory)
    )


@router.get("", response_model=AssetPage)
def get_assets(
    request: Request,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Página em ordem de ticker com limite de cem itens."""
    assets = list_assets(limit, offset, request.app.state.uow_factory)
    return AssetPage(
        items=[asset_response(asset) for asset in assets], limit=limit, offset=offset
    )
