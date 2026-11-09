"""Composição/configuração reais e fronteira de persistência controlada."""

import pytest
from fastapi.testclient import TestClient

from application.types import User
from infrastructure.config import PortfolioSettings, Settings
from main import create_app
from presentation.http.dependencies import get_current_user


def test_configured_quota_counts_rejections_before_financial_uow(security_settings):
    seconds = [1000.0]
    reads = []
    user = User("synthetic@example.test", "synthetic")
    app = create_app(
        Settings(_env_file=None, DB_PASSWORD="synthetic", DB_PORT=1),
        security_settings,
        portfolio_settings=PortfolioSettings(
            _env_file=None, PORTFOLIO_TRANSACTIONS_PER_MINUTE=2
        ),
    )
    app.dependency_overrides[get_current_user] = lambda: user

    def unavailable():
        from sqlalchemy.exc import OperationalError

        reads.append(True)
        raise OperationalError("synthetic", None, RuntimeError("synthetic"))

    with TestClient(app) as client:
        app.state.uow_factory = unavailable
        app.state.transaction_limits._clock = lambda: seconds[0]
        body = {
            "ticker": "TEST3",
            "quantity": "1",
            "price": "30",
            "type": "buy",
            "date": "2026-01-01",
        }
        path = "/api/v1/portfolio/transactions"
        for _ in range(2):
            assert client.post(path, json=body).status_code == 503
        response = client.post(path, json=body)
        assert response.status_code == 429
        assert response.json()["code"] == "transaction_rate_limited"
        assert response.headers["Retry-After"] == "60"
        assert response.headers["Cache-Control"] == "no-store"
        assert len(reads) == 2
        # Invalid shapes keep their specific validation error.
        assert client.post(path, json=body | {"price": 30}).status_code == 422
        seconds[0] += 60
        assert client.post(path, json=body).status_code == 503
        assert len(reads) == 3
        response_schema = app.openapi()["paths"][path]["post"]["responses"]
        assert (
            response_schema["429"]["headers"]["Retry-After"]["schema"]["maximum"] == 60
        )
    with pytest.raises(AttributeError):
        _ = app.state.transaction_limits
