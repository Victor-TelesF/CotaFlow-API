import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from infrastructure.config import MarketSettings, SecuritySettings, Settings
from main import create_app


@pytest.mark.parametrize("exit_path", ["normal", "exception"])
def test_application_shutdown_closes_real_pool_connections(postgres_engine, exit_path):
    url = postgres_engine.url
    settings = Settings(
        _env_file=None,
        DB_HOST=url.host,
        DB_PORT=url.port,
        DB_NAME=url.database,
        DB_USER=url.username,
        DB_PASSWORD=url.password,
    )
    app = create_app(
        settings,
        SecuritySettings(
            _env_file=None,
            JWT_SECRET_KEY="synthetic-lifespan-key-with-at-least-32-characters",
        ),
        MarketSettings(_env_file=None),
    )
    backend_pid = None
    market_client = None

    def run():
        nonlocal backend_pid, market_client
        with TestClient(app) as client:
            market_client = app.state.quotes._provider._client
            assert not market_client.is_closed
            assert client.get("/health").json() == {"status": "ok"}
            assert set(client.get("/openapi.json").json()["paths"]) == {
                "/health",
                "/api/v1/auth/register",
                "/api/v1/auth/login",
                "/api/v1/assets",
                "/api/v1/portfolio/transactions",
                "/api/v1/portfolio/positions",
                "/api/v1/portfolio/valuation",
            }
            with app.state.uow_factory() as uow:
                backend_pid = uow._session.scalar(text("SELECT pg_backend_pid()"))
                pool = uow._session.bind.pool
            assert pool.checkedout() == 0
            with postgres_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid"),
                        {"pid": backend_pid},
                    )
                    == 1
                )
            if exit_path == "exception":
                raise RuntimeError("synthetic lifespan failure")

    if exit_path == "exception":
        with pytest.raises(RuntimeError, match="synthetic lifespan failure"):
            run()
    else:
        run()
    assert backend_pid is not None
    assert market_client.is_closed
    assert not hasattr(app.state, "quotes")
    assert not hasattr(app.state, "uow_factory")
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": backend_pid},
            )
            == 0
        )
