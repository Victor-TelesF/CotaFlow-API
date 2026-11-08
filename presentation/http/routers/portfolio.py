"""Operações, histórico e posições protegidos pela identidade assinada."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from application.portfolio import (
    evaluate_portfolio,
    list_transactions,
    load_portfolio,
    register_transaction,
)
from presentation.http.dependencies import CurrentUser
from presentation.http.schemas.errors import ErrorResponse, QuoteFailureResponse
from presentation.http.schemas.portfolio import (
    PortfolioPositionsResponse,
    PortfolioValuationResponse,
    TransactionPage,
    TransactionRequest,
    TransactionResponse,
)

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])


@router.post(
    "/transactions",
    status_code=201,
    response_model=TransactionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Catalog asset not found"},
        409: {"model": ErrorResponse, "description": "Incompatible historical balance"},
    },
)
def create_transaction(body: TransactionRequest, request: Request, user: CurrentUser):
    """Valida todo o histórico sob bloqueio; preço informado é preservado."""
    transaction = register_transaction(
        user.id,
        body.ticker,
        body.quantity,
        body.price,
        body.type,
        body.date,
        request.app.state.uow_factory,
    )
    return TransactionResponse.from_transaction(transaction)


@router.get("/transactions", response_model=TransactionPage)
def get_transactions(
    request: Request,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Histórico somente do dono, sem truncar a validação de novas operações."""
    transactions = list_transactions(
        user.id, limit, offset, request.app.state.uow_factory
    )
    return TransactionPage(
        items=[TransactionResponse.from_transaction(item) for item in transactions],
        limit=limit,
        offset=offset,
    )


@router.get("/positions", response_model=PortfolioPositionsResponse)
def get_positions(request: Request, user: CurrentUser):
    """Posições abertas e custo total calculados do histórico integral do dono."""
    portfolio = load_portfolio(user.id, request.app.state.uow_factory)
    return PortfolioPositionsResponse.from_portfolio(portfolio)


@router.get(
    "/valuation",
    response_model=PortfolioValuationResponse,
    responses={
        503: {
            "model": ErrorResponse | QuoteFailureResponse,
            "description": "Persistence or complete quotation unavailable; no totals",
            "headers": {
                "Retry-After": {
                    "description": "Seconds before a cold retry after provider rate limiting",
                    "schema": {"type": "integer", "minimum": 1},
                }
            },
        }
    },
)
def get_valuation(request: Request, user: CurrentUser):
    """Avaliação completa usando uma fotografia de carteira e cotações."""
    valuation = evaluate_portfolio(
        user.id, request.app.state.uow_factory, request.app.state.quotes
    )
    return PortfolioValuationResponse.from_valuation(valuation)
