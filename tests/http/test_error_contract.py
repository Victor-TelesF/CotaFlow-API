"""OpenAPI deve descrever o envelope seguro realmente retornado pela API."""

from fastapi.testclient import TestClient

from infrastructure.config import MarketSettings, Settings
from main import create_app


def test_validation_error_matches_documented_schema(security_settings):
    app = create_app(
        Settings(_env_file=None, DB_PASSWORD="synthetic", DB_PORT=1),
        security_settings,
        MarketSettings(_env_file=None),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/register", json={})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "invalid_request"
        assert set(body) == {"code", "message", "details"}
        schema = client.get("/openapi.json").json()
    documented = schema["paths"]["/api/v1/auth/register"]["post"]["responses"]["422"]
    assert documented["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    fields = schema["components"]["schemas"]["ErrorResponse"]["properties"]
    assert set(body) <= fields.keys()
    assert fields["code"]["type"] == fields["message"]["type"] == "string"


def test_protected_routes_document_auth_and_persistence_errors():
    schema = create_app().openapi()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if path.startswith("/api/v1/"):
                assert "503" in operation["responses"], (path, method)
            if path.startswith(("/api/v1/assets", "/api/v1/portfolio")):
                assert operation["security"] == [{"HTTPBearer": []}]
                assert "401" in operation["responses"], (path, method)
    failure = schema["paths"]["/api/v1/portfolio/valuation"]["get"]["responses"]["503"]
    assert "Retry-After" in failure["headers"]
    quote = schema["components"]["schemas"]["QuoteFailureResponse"]
    assert {"tickers", "reason", "code", "message"} <= set(quote["required"])
