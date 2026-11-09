"""Cache real, transporte controlado e coordenação explícita entre threads."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

import httpx2
import pytest

from application.exceptions import QuotesUnavailable
from infrastructure.market.brapi import BrapiQuotes
from infrastructure.market.cache import CachedQuotes
from tests.infrastructure.test_market import body


def test_slow_cold_lookup_does_not_block_unrelated_warm_hit():
    entered, release = Event(), Event()
    calls = []

    def handle(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        calls.append(ticker)
        if ticker == "SLOW3":
            entered.set()
            assert release.wait(5)
        return httpx2.Response(200, content=body(ticker))

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        cache = CachedQuotes(BrapiQuotes(client))
        cache.get_quotes({"WARM3"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(cache.get_quotes, {"SLOW3"})
            assert entered.wait(5)
            warm = pool.submit(cache.get_quotes, {"WARM3"})
            try:
                assert warm.result(timeout=1)["WARM3"].price == Decimal(42)
                assert not slow.done()
                assert calls == ["WARM3", "SLOW3"]
            finally:
                release.set()
            assert slow.result(timeout=5)["SLOW3"].price == Decimal(42)


@pytest.mark.parametrize(
    "status,reason,ttl", [(404, "not_found", 30), (503, "unavailable", 5)]
)
def test_negative_cache_expires_exactly_and_is_isolated_from_other_symbols(
    status, reason, ttl
):
    seconds = [1000.0]
    calls = []
    failing = [True]

    def handle(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        calls.append(ticker)
        if ticker == "FAIL3" and failing[0]:
            return httpx2.Response(status)
        return httpx2.Response(200, content=body(ticker))

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        clock = lambda: seconds[0]
        cache = CachedQuotes(BrapiQuotes(client, clock=clock), clock=clock)
        for instant in (1000, 1000 + ttl - 0.1):
            seconds[0] = instant
            with pytest.raises(QuotesUnavailable) as error:
                cache.get_quotes({"FAIL3"})
            assert error.value.reason == reason
            assert error.value.tickers == ("FAIL3",)
        assert calls == ["FAIL3"]
        assert cache.get_quotes({"OTHER3"})["OTHER3"].price == Decimal(42)
        seconds[0] = 1000 + ttl
        failing[0] = False
        assert cache.get_quotes({"FAIL3"})["FAIL3"].price == Decimal(42)
        assert calls == ["FAIL3", "OTHER3", "FAIL3"]


def test_concurrency_and_pending_symbols_have_independent_hard_bounds():
    from threading import Lock

    entered, release = Event(), Event()
    guard = Lock()
    active = [0]
    maximum = [0]
    calls = []

    def handle(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        with guard:
            calls.append(ticker)
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            if active[0] == 2:
                entered.set()
        try:
            assert release.wait(5)
            return httpx2.Response(200, content=body(ticker))
        finally:
            with guard:
                active[0] -= 1

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        cache = CachedQuotes(BrapiQuotes(client), pending_capacity=2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(cache.get_quotes, {ticker}) for ticker in ("AAA3", "BBB3")
            ]
            try:
                assert entered.wait(5)
                assert len(cache._flights) == 2
                with pytest.raises(QuotesUnavailable) as rejected:
                    cache.get_quotes({"CCC3"})
                assert rejected.value.reason == "busy"
                assert sorted(calls) == ["AAA3", "BBB3"]
            finally:
                release.set()
            assert all(future.result(timeout=5) for future in futures)
        assert maximum == [2]
        assert cache._flights == {}
        assert cache.get_quotes({"CCC3"})["CCC3"].price == Decimal(42)


def test_waiting_third_symbol_cannot_start_http_before_a_slot_is_released():
    from threading import Lock

    two_entered, third_waiting, release = Event(), Event(), Event()
    guard = Lock()
    calls = []
    attempts = [0]

    def handle(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        with guard:
            calls.append(ticker)
            if len(calls) == 2:
                two_entered.set()
        assert release.wait(5)
        return httpx2.Response(200, content=body(ticker))

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        cache = CachedQuotes(BrapiQuotes(client))
        real_slots = cache._slots

        class ObservedSlots:
            def acquire(self, timeout):
                with guard:
                    attempts[0] += 1
                    if attempts[0] == 3:
                        third_waiting.set()
                return real_slots.acquire(timeout=timeout)

            def release(self):
                real_slots.release()

        cache._slots = ObservedSlots()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(cache.get_quotes, {ticker}) for ticker in ("AAA3", "BBB3")
            ]
            try:
                assert two_entered.wait(5)
                futures.append(pool.submit(cache.get_quotes, {"CCC3"}))
                assert third_waiting.wait(5)
                assert sorted(calls) == ["AAA3", "BBB3"]
            finally:
                release.set()
            assert all(future.result(timeout=5) for future in futures)
        assert sorted(calls) == ["AAA3", "BBB3", "CCC3"]


@pytest.mark.parametrize(
    "status,reason", [(503, "unavailable"), (401, "authentication")]
)
def test_followers_share_failure_and_recover_without_leaked_flights(status, reason):
    entered, waiting, release = Event(), Event(), Event()
    seconds = [1000.0]
    calls = []

    def handle(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return httpx2.Response(status)

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        clock = lambda: seconds[0]
        cache = CachedQuotes(BrapiQuotes(client, clock=clock), clock=clock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(cache.get_quotes, {"TEST3"})
            assert entered.wait(5)
            flight = cache._flights["TEST3"]
            real_event = flight.done

            class ObservedEvent:
                def wait(self, timeout):
                    waiting.set()
                    return real_event.wait(timeout)

                def set(self):
                    real_event.set()

            flight.done = ObservedEvent()
            second = pool.submit(cache.get_quotes, {"TEST3"})
            try:
                assert waiting.wait(5)
            finally:
                release.set()
            for future in (first, second):
                with pytest.raises(QuotesUnavailable) as error:
                    future.result(timeout=5)
                assert error.value.reason == reason
                assert error.value.tickers == ("TEST3",)
        assert len(calls) == 1
        assert cache._flights == {}
        seconds[0] += 5
        with pytest.raises(QuotesUnavailable):
            cache.get_quotes({"TEST3"})
        assert len(calls) == 2


def test_negative_entries_share_lru_capacity_with_prices_and_expire():
    clock = [1000.0]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(404)

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        now = lambda: clock[0]
        cache = CachedQuotes(BrapiQuotes(client, clock=now), capacity=2, clock=now)
        for ticker in ("AAA3", "BBB3", "CCC3", "AAA3"):
            with pytest.raises(QuotesUnavailable):
                cache.get_quotes({ticker})
            assert len(cache._entries) <= 2
        assert len(calls) == 4
        clock[0] += 30
        assert cache.get_quotes(set()) == {}
        assert cache._entries == {}


def test_external_budget_counts_failures_but_not_negative_or_positive_hits():
    seconds = [1000.0]
    calls = []

    def handle(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        calls.append(ticker)
        return (
            httpx2.Response(503)
            if ticker == "FAIL3"
            else httpx2.Response(200, content=body(ticker))
        )

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        clock = lambda: seconds[0]
        cache = CachedQuotes(
            BrapiQuotes(client, clock=clock), external_per_minute=2, clock=clock
        )
        assert cache.get_quotes({"WARM3"})["WARM3"].price == Decimal(42)
        for _ in range(2):
            with pytest.raises(QuotesUnavailable) as failed:
                cache.get_quotes({"FAIL3"})
            assert failed.value.reason == "unavailable"
        assert cache.get_quotes({"WARM3"})["WARM3"].cached
        with pytest.raises(QuotesUnavailable) as exhausted:
            cache.get_quotes({"OTHER3"})
        assert (exhausted.value.reason, exhausted.value.retry_after) == (
            "external_budget",
            60,
        )
        assert calls == ["WARM3", "FAIL3"]
        seconds[0] += 60
        assert cache.get_quotes({"OTHER3"})["OTHER3"].price == Decimal(42)
        assert calls == ["WARM3", "FAIL3", "OTHER3"]


def test_authentication_failure_is_global_but_not_symbol_absence():
    seconds = [1000.0]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(403)

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        clock = lambda: seconds[0]
        cache = CachedQuotes(BrapiQuotes(client, clock=clock), clock=clock)
        for ticker in ("AAA3", "BBB3"):
            with pytest.raises(QuotesUnavailable) as error:
                cache.get_quotes({ticker})
            assert error.value.reason == "authentication"
        assert len(calls) == 1
        seconds[0] += 5
        with pytest.raises(QuotesUnavailable):
            cache.get_quotes({"BBB3"})
        assert len(calls) == 2


def test_follower_timeout_does_not_cancel_owner_or_leak_flight():
    entered, release = Event(), Event()
    calls = []

    def handle(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return httpx2.Response(200, content=body())

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        cache = CachedQuotes(BrapiQuotes(client), budget=0.02)
        # Relógio fixo deixa o orçamento do owner válido; Event.wait limita o follower.
        cache._clock = lambda: 1000.0
        cache._provider._clock = cache._clock
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(cache.get_quotes, {"TEST3"})
            assert entered.wait(5)
            try:
                with pytest.raises(QuotesUnavailable) as error:
                    cache.get_quotes({"TEST3"})
                assert error.value.reason == "timeout"
                assert len(cache._flights) == 1
            finally:
                release.set()
            assert first.result(timeout=5)["TEST3"].price == Decimal(42)
        assert cache._flights == {}
        assert cache.get_quotes({"TEST3"})["TEST3"].cached
        assert len(calls) == 1


def test_unexpected_transport_exception_releases_slot_and_allows_recovery():
    attempts = []

    def handle(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise RuntimeError("synthetic transport defect")
        return httpx2.Response(200, content=body())

    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
        cache = CachedQuotes(BrapiQuotes(client), concurrency=1)
        with pytest.raises(RuntimeError, match="synthetic transport defect"):
            cache.get_quotes({"TEST3"})
        assert cache._flights == {}
        assert cache.get_quotes({"TEST3"})["TEST3"].price == Decimal(42)
        assert len(attempts) == 2
