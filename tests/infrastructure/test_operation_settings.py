import json

import pytest
from pydantic import ValidationError

from infrastructure.config import CorsSettings, MigrationSettings


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.vercel.app",
        "null",
        "https://example.test/",
        "https://example.test/path",
        "https://example.test?private=1",
        "https://example.test#fragment",
        "https://user:synthetic@example.test",
        "ftp://example.test",
        "https://example.test:65536",
        "https://example.test:0",
        "https://example.test\n",
        "https://example.test?",
        "https://example.test#",
    ],
)
def test_invalid_cors_origins_rejected_without_input_disclosure(origin):
    with pytest.raises(ValidationError) as caught:
        CorsSettings(_env_file=None, CORS_ALLOW_ORIGINS=[origin])
    assert caught.value.errors(include_input=False)[0]["loc"] == ("CORS_ALLOW_ORIGINS",)
    assert "input_value" not in str(caught.value)


def test_cors_json_environment_and_exact_origin_list(monkeypatch):
    origins = [
        "https://synthetic.vercel.app",
        "http://localhost:5173",
        "https://synthetic.vercel.app",
    ]
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", json.dumps(origins))
    assert CorsSettings(_env_file=None).CORS_ALLOW_ORIGINS == origins[:2]


@pytest.mark.parametrize("seconds", ["0", "301", "invalid"])
def test_migration_lock_timeout_is_bounded(monkeypatch, seconds):
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT_SECONDS", seconds)
    with pytest.raises(ValidationError) as caught:
        MigrationSettings(_env_file=None)
    assert caught.value.errors(include_input=False)[0]["loc"] == (
        "MIGRATION_LOCK_TIMEOUT_SECONDS",
    )
