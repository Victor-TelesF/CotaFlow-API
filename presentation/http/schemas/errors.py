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


DIAGNOSTIC_HEADERS = {
    "X-Request-ID": {
        "description": "API-generated correlation identifier; a bounded subset of failures is logged",
        "schema": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
    }
}


COMMON_ERRORS = {
    413: {"model": ErrorResponse, "description": "Request body exceeds 16384 bytes"},
    422: {"model": ErrorResponse, "description": "Invalid request or domain value"},
    503: {
        "model": ErrorResponse,
        "description": "Persistence unavailable",
        "headers": DIAGNOSTIC_HEADERS,
    },
}
AUTHENTICATION_ERROR = {
    "model": ErrorResponse,
    "description": "Authentication failed",
    "headers": {"WWW-Authenticate": {"schema": {"type": "string"}}},
}
PROTECTED_ERRORS = {**COMMON_ERRORS, 401: AUTHENTICATION_ERROR}
AUTH_ERRORS = {
    **COMMON_ERRORS,
    429: {
        "model": ErrorResponse,
        "description": "Authentication budget or concurrency exhausted",
        "headers": {
            "Retry-After": {"schema": {"type": "integer", "minimum": 1, "maximum": 600}}
        },
    },
}
