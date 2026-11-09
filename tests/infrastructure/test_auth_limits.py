"""Oráculos de janela móvel, estado limitado e admissão concorrente."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from pydantic import ValidationError

from application.exceptions import AuthenticationLimited
from infrastructure.auth_limits import AuthenticationLimits, client_identity
from infrastructure.config import AuthSettings


def policy(clock, **settings):
    return AuthenticationLimits(
        AuthSettings(_env_file=None, **settings), clock=lambda: clock[0]
    )


@pytest.mark.parametrize(
    "operation,limit,period",
    [("register", 5, 600), ("login", 20, 60)],
)
def test_ip_quota_has_exact_boundary_and_does_not_extend_on_rejection(
    operation, limit, period
):
    clock = [0.0]
    limits = policy(clock)
    for _ in range(limit):
        limits.check_ip(operation, "192.0.2.1")
    limits.check_ip(operation, "192.0.2.2")
    with pytest.raises(AuthenticationLimited) as caught:
        limits.check_ip(operation, "192.0.2.1")
    assert caught.value.retry_after == period
    clock[0] = period - 0.1
    with pytest.raises(AuthenticationLimited) as caught:
        limits.check_ip(operation, "192.0.2.1")
    assert caught.value.retry_after == 1
    clock[0] = period
    limits.check_ip(operation, "192.0.2.1")


def test_pair_is_separate_for_ip_and_email_and_expires_without_account_lock():
    clock = [0.0]
    limits = policy(clock)
    for _ in range(5):
        with limits.admit("login", "192.0.2.1", "private@example.com"):
            pass
    with (
        pytest.raises(AuthenticationLimited) as caught,
        limits.admit("login", "192.0.2.1", "private@example.com"),
    ):
        pytest.fail("Pair quota must reject")
    assert caught.value.retry_after == 300
    for ip, email in [
        ("192.0.2.2", "private@example.com"),
        ("192.0.2.1", "other@example.com"),
    ]:
        with limits.admit("login", ip, email):
            pass
    # Neither the raw email nor an unkeyed email hash is stored as a key.
    from hashlib import sha256

    state = repr(limits._attempts)
    assert "private@example.com" not in state
    assert sha256(b"private@example.com").hexdigest() not in state
    other = policy(clock)
    with other.admit("login", "192.0.2.1", "private@example.com"):
        pass
    assert set(other._attempts).isdisjoint(limits._attempts)
    clock[0] = 300
    with limits.admit("login", "192.0.2.1", "private@example.com"):
        pass


def test_full_map_rejects_new_keys_without_evicting_active_quotas():
    clock = [0.0]
    limits = AuthenticationLimits(
        AuthSettings(_env_file=None), capacity=2, clock=lambda: clock[0]
    )
    for _ in range(5):
        limits.check_ip("register", "192.0.2.1")
    limits.check_ip("login", "192.0.2.2")
    with pytest.raises(AuthenticationLimited):
        limits.check_ip("login", "192.0.2.3")
    assert len(limits._attempts) == 2
    clock[0] = 60
    limits.check_ip("login", "192.0.2.3")
    assert len(limits._attempts) == 2
    with pytest.raises(AuthenticationLimited) as caught:
        limits.check_ip("register", "192.0.2.1")
    assert caught.value.retry_after == 540
    clock[0] = 600
    limits.check_ip("register", "192.0.2.4")
    assert len(limits._attempts) == 1


def test_concurrent_ip_attempts_cannot_exceed_quota():
    limits = policy([0.0], AUTH_LOGIN_PER_MINUTE=3)
    barrier = Barrier(12)

    def attempt():
        barrier.wait(timeout=5)
        try:
            limits.check_ip("login", "192.0.2.1")
        except AuthenticationLimited:
            return False
        return True

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda _: attempt(), range(12)))
    assert results.count(True) == 3
    assert results.count(False) == 9


def test_concurrent_slots_reject_without_waiting_and_recover_after_error():
    limits = policy([0.0], AUTH_MAX_CONCURRENT=2)
    entered = Barrier(3)
    release = Event()

    def held(email):
        with limits.admit("login", "192.0.2.1", email):
            entered.wait(timeout=5)
            assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(held, email) for email in ("a@example.com", "b@example.com")
        ]
        try:
            entered.wait(timeout=5)
            with (
                pytest.raises(AuthenticationLimited) as caught,
                limits.admit("register", "192.0.2.2", "c@example.com"),
            ):
                pytest.fail("Third operation must be rejected immediately")
            assert caught.value.retry_after == 1
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    with (
        pytest.raises(RuntimeError, match="synthetic"),
        limits.admit("register", None, "c@example.com"),
    ):
        raise RuntimeError("synthetic")
    # Both slots remain usable after normal and exceptional exits.
    with (
        limits.admit("register", None, "c@example.com"),
        limits.admit("register", None, "d@example.com"),
    ):
        pass


def test_ipv6_spellings_and_mapped_ipv4_share_identity():
    assert client_identity("2001:db8:0:0:0:0:0:1") == client_identity("2001:db8::1")
    assert client_identity("::ffff:192.0.2.1") == client_identity("192.0.2.1")
    assert client_identity(None) == client_identity("invalid") == "unknown"
    limits = policy([0.0], AUTH_LOGIN_PER_MINUTE=1)
    limits.check_ip("login", "::ffff:192.0.2.1")
    with pytest.raises(AuthenticationLimited):
        limits.check_ip("login", "192.0.2.1")


@pytest.mark.parametrize(
    "name,value",
    [
        ("AUTH_REGISTER_PER_10_MINUTES", 0),
        ("AUTH_LOGIN_PER_MINUTE", 1001),
        ("AUTH_LOGIN_PAIR_PER_5_MINUTES", 0),
        ("AUTH_MAX_CONCURRENT", 9),
    ],
)
def test_auth_configuration_rejects_unbounded_or_zero_limits(name, value):
    with pytest.raises(ValidationError) as caught:
        AuthSettings(_env_file=None, **{name: value})
    assert caught.value.errors(include_input=False)[0]["loc"] == (name,)


def test_window_releases_only_expired_attempts_instead_of_resetting_all():
    clock = [0.0]
    limits = policy(clock, AUTH_LOGIN_PER_MINUTE=2)
    limits.check_ip("login", "192.0.2.1")
    clock[0] = 30
    limits.check_ip("login", "192.0.2.1")
    clock[0] = 60
    limits.check_ip("login", "192.0.2.1")
    with pytest.raises(AuthenticationLimited) as caught:
        limits.check_ip("login", "192.0.2.1")
    assert caught.value.retry_after == 30
