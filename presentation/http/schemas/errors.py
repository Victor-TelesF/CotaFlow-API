"""Schemas dos envelopes de erro seguros para documentação OpenAPI."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    location: list[str | int]
    type: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class QuoteFailureResponse(ErrorResponse):
    tickers: list[str]
    reason: str


COMMON_ERRORS = {
    422: {"model": ErrorResponse, "description": "Invalid request or domain value"},
    503: {"model": ErrorResponse, "description": "Persistence unavailable"},
}
AUTHENTICATION_ERROR = {
    "model": ErrorResponse,
    "description": "Authentication failed",
    "headers": {"WWW-Authenticate": {"schema": {"type": "string"}}},
}
PROTECTED_ERRORS = {**COMMON_ERRORS, 401: AUTHENTICATION_ERROR}
