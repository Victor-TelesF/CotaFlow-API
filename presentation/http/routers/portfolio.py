"""Operações, histórico e posições protegidos pela identidade assinada."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from application.portfolio import (
    evaluate_portfolio,
    list_transactions,
    load_portfolio,
    register_transaction,
)
from presentation.http.dependencies import CurrentUser
from presentation.http.pagination import PageOffset, validate_page_offset
from presentation.http.schemas.errors import (
    DIAGNOSTIC_HEADERS,
    ErrorResponse,
    QuoteFailureResponse,
)
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
        429: {
            "model": ErrorResponse,
            "description": "User transaction budget exhausted",
            "headers": {
                "Retry-After": {
                    "schema": {"type": "integer", "minimum": 1, "maximum": 60}
                }
            },
        },
    },
)
def create_transaction(body: TransactionRequest, request: Request, user: CurrentUser):
    """Valida todo o histórico sob bloqueio; preço informado é preservado."""
    request.app.state.transaction_limits.check(user.id)
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


@router.get(
    "/transactions",
    response_model=TransactionPage,
    dependencies=[Depends(validate_page_offset)],
)
def get_transactions(
    request: Request,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: PageOffset = 0,
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
        422: {
            "model": ErrorResponse,
            "description": "Invalid input or too many open positions to value",
        },
        429: {
            "model": ErrorResponse,
            "description": "User valuation budget exhausted",
            "headers": {
                "Retry-After": {
                    "schema": {"type": "integer", "minimum": 1, "maximum": 60}
                }
            },
        },
        503: {
            "model": ErrorResponse | QuoteFailureResponse,
            "description": "Persistence or complete quotation unavailable; no totals",
            "headers": {
                **DIAGNOSTIC_HEADERS,
                "Retry-After": {
                    "description": "Seconds before a cold retry after provider cooldown or external budget exhaustion (up to one hour)",
                    "schema": {"type": "integer", "minimum": 1, "maximum": 3600},
                },
            },
        },
    },
)
def get_valuation(request: Request, user: CurrentUser):
    """Avaliação completa usando uma fotografia de carteira e cotações."""
    request.app.state.valuation_limits.check(user.id)
    valuation = evaluate_portfolio(
        user.id,
        request.app.state.uow_factory,
        request.app.state.quotes,
        request.app.state.max_valuation_tickers,
    )
    return PortfolioValuationResponse.from_valuation(valuation)
