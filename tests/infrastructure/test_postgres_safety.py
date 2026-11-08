import pytest

from integration_tests.support import disposable_database_url


@pytest.mark.parametrize(
    "url, opt_in",
    [
        (None, "1"),
        ("postgresql+psycopg://localhost/cotaflow_test_synthetic", None),
        ("postgresql+psycopg://localhost/app", "1"),
        ("postgresql+psycopg://remote.invalid/cotaflow_test_synthetic", "1"),
        ("sqlite:///cotaflow_test_synthetic", "1"),
        ("invalid-url-synthetic-secret", "1"),
    ],
)
def test_unsafe_or_missing_test_configuration_is_rejected(monkeypatch, url, opt_in):
    for name, value in {
        "TEST_DATABASE_URL": url,
        "TEST_DATABASE_DISPOSABLE": opt_in,
    }.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    # Even a configured application database must never become a fallback.
    monkeypatch.setenv("DB_NAME", "application_database")
    with pytest.raises(pytest.UsageError) as caught:
        disposable_database_url()
    assert "invalid-url-synthetic-secret" not in str(caught.value)


def test_explicit_local_disposable_database_is_accepted(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_DISPOSABLE", "1")
    monkeypatch.setenv(
        "TEST_DATABASE_URL", "postgresql+psycopg://localhost/cotaflow_test_synthetic"
    )
    assert disposable_database_url().database == "cotaflow_test_synthetic"
