"""Diagnóstico observável sem copiar dados privados de falhas ou requisições."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from application.exceptions import QuotesUnavailable
from infrastructure.diagnostics import FailureDiagnostics
from main import create_app
from presentation.http.dependencies import get_current_user

LOGGER = "cotaflow.diagnostics"
PRIVATE = "synthetic-private-marker"


def diagnostic_records(caplog):
    return [record for record in caplog.records if record.name == LOGGER]


def failing_client(error):
    app = create_app()

    def fail():
        raise error

    app.dependency_overrides[get_current_user] = fail
    return TestClient(app)


def test_database_failure_logs_safe_metadata_and_controlled_correlation(caplog):
    class DriverError(Exception):
        sqlstate = "08006"

    error = OperationalError(
        f"SELECT '{PRIVATE}'", {"password": PRIVATE}, DriverError(PRIVATE)
    )
    client = failing_client(error)
    response = client.get(
        f"/api/v1/portfolio/positions?token={PRIVATE}",
        headers={"Authorization": f"Bearer {PRIVATE}", "X-Request-ID": PRIVATE},
    )
    assert response.status_code == 503
    assert response.json() == {
        "code": "persistence_unavailable",
        "message": "Persistence unavailable",
    }
    records = diagnostic_records(caplog)
    assert len(records) == 1
    data = json.loads(records[0].getMessage())
    assert data == {
        "event": "request_failure",
        "category": "database",
        "method": "GET",
        "route": "/api/v1/portfolio/positions",
        "request_id": response.headers["X-Request-ID"],
        "exception_type": "OperationalError",
        "sqlstate": "08006",
        "suppressed": 0,
    }
    assert re.fullmatch(r"[0-9a-f]{32}", data["request_id"])
    assert response.headers["Cache-Control"] == "no-store"
    assert PRIVATE not in response.text + caplog.text
    assert records[0].exc_info is records[0].stack_info is None


@pytest.mark.parametrize("sqlstate", [None, PRIVATE, "08006\n", "08é06", "x" * 10000])
def test_database_failure_omits_invalid_sqlstate_without_stringifying_error(
    caplog, sqlstate
):
    class DriverError(Exception):
        prevent_string = False

        def __str__(self):
            if self.prevent_string:
                pytest.fail("Diagnostic tried to stringify the driver error")
            return super().__str__()

    driver = DriverError(PRIVATE)
    driver.sqlstate = sqlstate
    # SQLAlchemy lê o texto ao construir seu erro; a proibição vale ao reportar.
    error = OperationalError(PRIVATE, {"email": PRIVATE}, driver)
    driver.prevent_string = True
    client = failing_client(error)
    response = client.get("/api/v1/portfolio/positions")
    assert response.status_code == 503
    data = json.loads(diagnostic_records(caplog)[0].getMessage())
    assert data["category"] == "database"
    assert "sqlstate" not in data
    assert PRIVATE not in caplog.text + response.text


def test_route_template_is_logged_without_path_values_or_query(caplog):
    app = create_app()

    @app.get("/probe/{value}")
    def fail(value: str):
        raise OperationalError(PRIVATE, None, RuntimeError(PRIVATE))

    response = TestClient(app).get(f"/probe/{PRIVATE}?password={PRIVATE}")
    assert response.status_code == 503
    data = json.loads(diagnostic_records(caplog)[0].getMessage())
    assert data["route"] == "/probe/{value}"
    assert PRIVATE not in caplog.text


def test_failure_burst_is_bounded_concurrently_and_recovers(caplog):
    seconds = [1000.0]
    client = failing_client(QuotesUnavailable({"TEST3"}, "timeout"))
    client.app.state.failure_diagnostics = FailureDiagnostics(clock=lambda: seconds[0])
    barrier = Barrier(20)

    def fail(_):
        barrier.wait(timeout=5)
        return client.get("/api/v1/portfolio/valuation")

    with ThreadPoolExecutor(max_workers=20) as executor:
        responses = list(executor.map(fail, range(20)))
    assert all(response.status_code == 503 for response in responses)
    assert len({response.headers["X-Request-ID"] for response in responses}) == 20
    assert len(diagnostic_records(caplog)) == 10
    assert all(
        json.loads(record.getMessage())["suppressed"] == 0
        for record in diagnostic_records(caplog)
    )

    # Ambos os tipos dividem o teto: falhas de banco não abrem outro orçamento.
    def database_failure():
        raise OperationalError(PRIVATE, None, RuntimeError(PRIVATE))

    client.app.dependency_overrides[get_current_user] = database_failure
    seconds[0] = 1059.999
    assert client.get("/api/v1/portfolio/valuation").status_code == 503
    assert len(diagnostic_records(caplog)) == 10
    seconds[0] = 1060.0
    recovered = client.get("/api/v1/portfolio/valuation")
    assert recovered.status_code == 503
    records = diagnostic_records(caplog)
    assert len(records) == 11
    data = json.loads(records[-1].getMessage())
    assert data["category"] == "database"
    assert data["suppressed"] == 11
    assert data["request_id"] == recovered.headers["X-Request-ID"]
    assert PRIVATE not in caplog.text


def test_log_window_is_sliding_and_instances_are_independent(caplog):
    seconds = [0.0]
    reporter = FailureDiagnostics(clock=lambda: seconds[0])

    def record(target=reporter):
        return target.record(
            category="quotes",
            method="GET",
            route=None,
            error=QuotesUnavailable(set()),
            reason="timeout",
        )

    for _ in range(9):
        record()
    seconds[0] = 30
    record()
    seconds[0] = 60
    for _ in range(10):
        record()
    assert len(diagnostic_records(caplog)) == 19
    seconds[0] = 90
    record()
    records = diagnostic_records(caplog)
    assert len(records) == 20
    assert json.loads(records[-1].getMessage())["suppressed"] == 1
    assert json.loads(records[-1].getMessage())["route"] == "unknown"
    record(FailureDiagnostics(clock=lambda: seconds[0]))
    assert len(diagnostic_records(caplog)) == 21


def test_success_and_expected_client_errors_do_not_consume_diagnostic_budget(caplog):
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/portfolio/positions").status_code == 401
    assert client.get("/api/v1/assets?offset=10001").status_code == 422
    assert diagnostic_records(caplog) == []


@pytest.mark.parametrize(
    "reason,expected", [("timeout", "timeout"), (PRIVATE, "unknown")]
)
def test_quote_failure_logs_bounded_reason_and_count_without_tickers(
    caplog, reason, expected
):
    client = failing_client(QuotesUnavailable({PRIVATE, "TEST3"}, reason, 60))
    response = client.get("/api/v1/portfolio/valuation")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    assert response.headers["Cache-Control"] == "no-store"
    assert "total_market_value" not in response.json()
    records = diagnostic_records(caplog)
    assert len(records) == 1
    assert json.loads(records[0].getMessage()) == {
        "event": "request_failure",
        "category": "quotes",
        "method": "GET",
        "route": "/api/v1/portfolio/valuation",
        "request_id": response.headers["X-Request-ID"],
        "exception_type": "QuotesUnavailable",
        "reason": expected,
        "ticker_count": 2,
        "suppressed": 0,
    }
    assert PRIVATE not in caplog.text
    assert records[0].exc_info is records[0].stack_info is None
