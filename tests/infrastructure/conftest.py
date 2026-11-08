import importlib

import pytest


@pytest.fixture
def config_module(monkeypatch, tmp_path):
    """Isola ambiente e diretório para não ler dotenv ou credenciais pessoais."""
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "DB_DRIVER": "postgresql+psycopg",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "55432",
        "DB_NAME": "synthetic_review",
        "DB_USER": "synthetic_user",
        "DB_PASSWORD": "synthetic-review-password",
    }.items():
        monkeypatch.setenv(name, value)
    return importlib.import_module("infrastructure.config")
