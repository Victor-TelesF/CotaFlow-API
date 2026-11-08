"""Contratos de forma/auth das rotas sem acesso ao banco nem rede."""

import pytest
from fastapi.testclient import TestClient

from application.types import User
from main import create_app
from presentation.http.dependencies import get_current_user


@pytest.fixture
def validation_client():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: User(
        "synthetic@example.test", "synthetic-hash"
    )

    def no_database():
        pytest.fail("Invalid HTTP shape must not enter persistence")

    app.state.uow_factory = no_database
    return TestClient(app)


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/assets"),
        ("post", "/api/v1/assets"),
        ("get", "/api/v1/portfolio/transactions"),
        ("post", "/api/v1/portfolio/transactions"),
        ("get", "/api/v1/portfolio/positions"),
        ("get", "/api/v1/portfolio/valuation"),
    ],
)
def test_financial_routes_require_bearer_before_database(method, path):
    client = TestClient(create_app())
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", 0.123456789),
        ("price", 30),
        ("quantity", True),
        ("price", "not-decimal"),
        ("price", "NaN"),
        ("quantity", "Infinity"),
        ("type", "dividend"),
        ("date", "not-a-date"),
        ("user_id", "forged-owner"),
    ],
)
def test_invalid_transaction_shape_is_safe_422(validation_client, field, value):
    body = {
        "ticker": "TEST3",
        "quantity": "1",
        "price": "30",
        "type": "buy",
        "date": "2026-01-01",
    }
    body[field] = value
    response = validation_client.post("/api/v1/portfolio/transactions", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert all(set(item) == {"location", "type"} for item in response.json()["details"])
    assert "forged-owner" not in response.text


@pytest.mark.parametrize("path", ["/api/v1/assets", "/api/v1/portfolio/transactions"])
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_pagination_is_bounded(validation_client, path, query):
    response = validation_client.get(f"{path}?{query}")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_decimal_input_openapi_documents_strings():
    schema = create_app().openapi()["components"]["schemas"]["TransactionRequest"]
    assert schema["properties"]["quantity"]["type"] == "string"
    assert schema["properties"]["price"]["type"] == "string"
