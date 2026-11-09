"""Admissão atômica e expiração, com relógio controlado e identidades sintéticas."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from application.exceptions import ValuationRateLimited
from infrastructure.market.limits import ValuationLimits, WindowBudget


def test_user_budget_exact_boundary_expiry_and_isolation():
    seconds = [1000.0]
    limits = ValuationLimits(clock=lambda: seconds[0])
    alice, bob = uuid4(), uuid4()
    for _ in range(10):
        limits.check(alice)
    with pytest.raises(ValuationRateLimited) as error:
        limits.check(alice)
    assert error.value.retry_after == 60
    limits.check(bob)
    seconds[0] += 59.5
    with pytest.raises(ValuationRateLimited) as error:
        limits.check(alice)
    assert error.value.retry_after == 1
    seconds[0] += 0.5
    limits.check(alice)


def test_user_map_cannot_evict_active_quota_to_bypass_limit():
    seconds = [1000.0]
    limits = ValuationLimits(1, capacity=2, clock=lambda: seconds[0])
    alice, bob, carol = uuid4(), uuid4(), uuid4()
    limits.check(alice)
    limits.check(bob)
    for user in (carol, alice):
        with pytest.raises(ValuationRateLimited):
            limits.check(user)
    assert len(limits._users) == 2
    seconds[0] += 60
    limits.check(carol)
    assert len(limits._users) == 1


@pytest.mark.parametrize("kind", ["user", "external"])
def test_concurrent_admission_cannot_exceed_budget(kind):
    barrier = Barrier(8)
    user = uuid4()
    limits = ValuationLimits(3) if kind == "user" else WindowBudget(3)

    def admit():
        barrier.wait(timeout=5)
        if kind == "external":
            return limits.admit() is None
        try:
            limits.check(user)
            return True
        except ValuationRateLimited:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(admit) for _ in range(8)]
        assert sum(future.result(timeout=5) for future in futures) == 3


def test_market_settings_bound_public_configuration():
    from pydantic import ValidationError

    from infrastructure.config import MarketSettings

    for field in (
        "MARKET_MAX_TICKERS",
        "MARKET_VALUATIONS_PER_MINUTE",
        "MARKET_EXTERNAL_CALLS_PER_MINUTE",
    ):
        with pytest.raises(ValidationError) as error:
            MarketSettings(_env_file=None, **{field: 0})
        assert error.value.errors(include_input=False)[0]["loc"] == (field,)
