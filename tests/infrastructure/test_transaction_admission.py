"""Janela móvel, isolamento e concorrência da quota de escrita HTTP."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.exceptions import TransactionRateLimited
from infrastructure.config import PortfolioSettings
from infrastructure.transaction_limits import TransactionLimits


def test_staggered_window_releases_only_expired_attempts_and_bounds_memory():
    seconds = [1000.0]
    limits = TransactionLimits(2, capacity=2, clock=lambda: seconds[0])
    alice, bob, carol = uuid4(), uuid4(), uuid4()
    limits.check(alice)
    seconds[0] += 30
    limits.check(alice)
    limits.check(bob)
    with pytest.raises(TransactionRateLimited) as error:
        limits.check(alice)
    assert error.value.retry_after == 30
    with pytest.raises(TransactionRateLimited):
        limits.check(carol)
    assert len(limits._users) == 2
    seconds[0] += 30
    limits.check(alice)
    with pytest.raises(TransactionRateLimited) as error:
        limits.check(alice)
    assert error.value.retry_after == 30
    seconds[0] += 30
    limits.check(carol)
    assert len(limits._users) == 2


def test_atomic_admission_does_not_allow_concurrent_quota_bypass():
    limits = TransactionLimits(3)
    barrier = Barrier(8)
    user = uuid4()

    def admit():
        barrier.wait(timeout=5)
        try:
            limits.check(user)
            return True
        except TransactionRateLimited:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(admit) for _ in range(8)]
        assert sum(f.result(timeout=5) for f in futures) == 3


@pytest.mark.parametrize("value", [0, 1001])
def test_settings_reject_invalid_transaction_quotas(value):
    with pytest.raises(ValidationError) as error:
        PortfolioSettings(_env_file=None, PORTFOLIO_TRANSACTIONS_PER_MINUTE=value)
    assert error.value.errors(include_input=False)[0]["loc"] == (
        "PORTFOLIO_TRANSACTIONS_PER_MINUTE",
    )
