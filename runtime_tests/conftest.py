"""Reuse the guarded disposable PostgreSQL database fixture."""

from integration_tests.conftest import postgres_engine

__all__ = ["postgres_engine"]
