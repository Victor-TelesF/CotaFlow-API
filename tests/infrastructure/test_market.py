"""Transporte HTTP controlado; parser/cache/sincronização executam de verdade."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

import httpx2
import pytest

from application.exceptions import QuotesUnavailable
from infrastructure.market.brapi import BrapiQuotes, retry_delay
from infrastructure.market.cache import CachedQuotes

NOW = datetime(2026, 11, 8, tzinfo=UTC)


def body(ticker="TEST3", **changes):
    return json.dumps(
        {
            "results": [
                {
                    "symbol": ticker,
                    "currency": "BRL",
                    "regularMarketPrice": 42,
                    "regularMarketTime": "2026-11-07T21:32:40.000Z",
                }
                | changes
            ]
        }
    ).encode()


@pytest.fixture
def market():
    """Cada cenário controla relógio monotônico, relógio UTC e transporte."""
    seconds = [1000.0]
    calls = []
    reply = [
        lambda request: httpx2.Response(
            200, content=body(request.url.path.rsplit("/", 1)[-1])
        )
    ]

    def handle(request):
        calls.append(request)
        return reply[0](request)

    client = httpx2.Client(
        transport=httpx2.MockTransport(handle),
        headers={"Authorization": "Bearer synthetic-secret"},
    )
    clock = lambda: seconds[0]
    provider = BrapiQuotes(
        client, clock=clock, now=lambda: NOW + timedelta(seconds=seconds[0] - 1000)
    )
    cache = CachedQuotes(provider, clock=clock)
    yield seconds, calls, reply, provider, cache
    client.close()


def test_exact_decimal_and_metadata_without_token_in_url(market):
    _, calls, reply, provider, _ = market
    reply[0] = lambda request: httpx2.Response(
        200, content=body().replace(b"42,", b"42.123456789123456789,")
    )
    quote = provider.get_quote("TEST3", 1010)
    assert quote.price == Decimal("42.123456789123456789")
    assert quote.fetched_at == NOW
    assert quote.quoted_at == datetime(2026, 11, 7, 21, 32, 40, tzinfo=UTC)
    assert (quote.currency, quote.source, quote.cached) == ("BRL", "brapi", False)
    assert str(calls[0].url) == "https://brapi.dev/api/quote/TEST3"
    assert calls[0].headers["Authorization"] == "Bearer synthetic-secret"
    assert all(0 < value <= 2 for value in calls[0].extensions["timeout"].values())


@pytest.mark.parametrize(
    "changes",
    [
        {"regularMarketPrice": 0},
        {"regularMarketPrice": -1},
        {"regularMarketPrice": None},
        {"regularMarketPrice": True},
        {"regularMarketPrice": "42"},
        {"regularMarketPrice": float("nan")},
        {"regularMarketPrice": float("inf")},
        {"symbol": "OTHER3"},
        {"currency": "USD"},
        {"regularMarketTime": "invalid"},
        {"regularMarketTime": "2026-11-07T21:32:40"},
        {"regularMarketTime": 1794087160},
    ],
)
def test_invalid_quote_never_becomes_price_and_recovery_after_negative_ttl(
    market, changes
):
    seconds, calls, reply, _, cache = market
    reply[0] = lambda request: httpx2.Response(200, content=body(**changes))
    with pytest.raises(QuotesUnavailable) as error:
        cache.get_quotes({"TEST3"})
    assert error.value.reason == "invalid_response"
    assert error.value.tickers == ("TEST3",)
    seconds[0] += 5
    reply[0] = lambda request: httpx2.Response(200, content=body())
    assert cache.get_quotes({"TEST3"})["TEST3"].price == Decimal(42)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b"[]",
        b"null",
        b"{}",
        b'{"results": []}',
        b'{"results": [null]}',
        b'{"results": [{}, {}]}',
        b'{"results": [{}]}',
        b'{"results": {}}',
        b"\xff",
        body().replace(b"42,", b"1e100000,"),
        body().replace(b"42,", b"1e-100000,"),
    ],
)
def test_incomplete_and_pathological_responses_are_specific_failures(market, content):
    _, _, reply, provider, _ = market
    reply[0] = lambda request: httpx2.Response(200, content=content)
    with pytest.raises(QuotesUnavailable) as error:
        provider.get_quote("TEST3", 1010)
    assert error.value.reason == "invalid_response"


def test_absent_quote_time_is_explicitly_null(market):
    _, _, reply, provider, _ = market
    reply[0] = lambda request: httpx2.Response(
        200, content=body(regularMarketTime=None)
    )
    assert provider.get_quote("TEST3", 1010).quoted_at is None


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "authentication"),
        (403, "authentication"),
        (404, "not_found"),
        (500, "unavailable"),
        (503, "unavailable"),
        (302, "unavailable"),
    ],
)
def test_http_failures_are_safe_without_retry_or_redirect(market, status, reason):
    _, calls, reply, _, cache = market
    reply[0] = lambda request: httpx2.Response(
        status,
        content=b"synthetic-secret private-provider-body",
        headers={"Location": "https://other.invalid"},
    )
    with pytest.raises(QuotesUnavailable) as error:
        cache.get_quotes({"TEST3"})
    assert error.value.reason == reason
    assert "synthetic-secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "exception,reason",
    [
        (httpx2.ReadTimeout, "timeout"),
        (httpx2.ConnectTimeout, "timeout"),
        (httpx2.ConnectError, "unavailable"),
    ],
)
def test_transport_failure_is_reused_without_price_or_repeated_http(
    market, exception, reason
):
    _, calls, reply, _, cache = market

    def fail(request):
        raise exception("synthetic-secret", request=request)

    reply[0] = fail
    for _ in range(2):
        with pytest.raises(QuotesUnavailable) as error:
            cache.get_quotes({"TEST3"})
        assert error.value.reason == reason
        assert "synthetic-secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "ticker", ["../TEST3", "TEST3?token=private", "TEST3,OTHER3", "TEST3/OTHER3"]
)
def test_local_ticker_cannot_inject_provider_path_or_multiple_symbols(market, ticker):
    _, calls, _, provider, _ = market
    with pytest.raises(QuotesUnavailable) as error:
        provider.get_quote(ticker, 1010)
    assert error.value.reason == "invalid_symbol"
    assert calls == []


def test_cold_warm_and_exact_expiry_preserve_fetched_time(market):
    seconds, calls, _, _, cache = market
    first = cache.get_quotes({"TEST3", "FUND11"})
    assert [request.url.path for request in calls] == [
        "/api/quote/FUND11",
        "/api/quote/TEST3",
    ]
    assert all(not quote.cached for quote in first.values())
    seconds[0] += 1799
    warm = cache.get_quotes({"TEST3", "FUND11"})
    assert len(calls) == 2
    assert all(quote.cached and quote.fetched_at == NOW for quote in warm.values())
    assert all(not quote.cached for quote in first.values())
    seconds[0] += 1
    refreshed = cache.get_quotes({"TEST3", "FUND11"})
    assert len(calls) == 4
    assert all(
        not quote.cached and quote.fetched_at == NOW + timedelta(seconds=1800)
        for quote in refreshed.values()
    )


def test_empty_and_lru_capacity(market):
    _, calls, _, provider, _ = market
    cache = CachedQuotes(provider, capacity=2, clock=lambda: 1000)
    assert cache.get_quotes(set()) == {}
    assert calls == []
    cache.get_quotes({"TEST3", "FUND11"})
    cache.get_quotes({"FUND11"})
    cache.get_quotes({"OTHER3"})
    assert cache.get_quotes({"FUND11"})["FUND11"].cached
    assert not cache.get_quotes({"TEST3"})["TEST3"].cached
    assert len(calls) == 4


def test_stale_cache_does_not_hide_provider_failure(market):
    seconds, calls, reply, _, cache = market
    cache.get_quotes({"TEST3"})
    seconds[0] += 1800
    reply[0] = lambda request: httpx2.Response(503)
    with pytest.raises(QuotesUnavailable):
        cache.get_quotes({"TEST3"})
    assert len(calls) == 2


@pytest.mark.parametrize("delay,expected", [(120, 120), (172800, 3600)])
def test_rate_limit_honors_cooldown_and_keeps_valid_hits_available(
    market, delay, expected
):
    seconds, calls, reply, _, cache = market
    cache.get_quotes({"FUND11"})
    reply[0] = lambda request: httpx2.Response(429, headers={"Retry-After": str(delay)})
    with pytest.raises(QuotesUnavailable) as error:
        cache.get_quotes({"TEST3", "FUND11"})
    assert error.value.tickers == ("TEST3",)
    assert error.value.retry_after == expected
    assert cache.get_quotes({"FUND11"})["FUND11"].cached
    seconds[0] += 20
    with pytest.raises(QuotesUnavailable) as cooldown:
        cache.get_quotes({"OTHER3"})
    assert cooldown.value.retry_after == expected - 20
    assert len(calls) == 2
    seconds[0] += expected - 20
    reply[0] = lambda request: httpx2.Response(200, content=body())
    assert not cache.get_quotes({"TEST3"})["TEST3"].cached
    assert len(calls) == 3


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 60),
        ("bad", 60),
        ("-120", 60),
        ("0", 1),
        ("1", 1),
        ("120", 120),
        ("3600", 3600),
        ("3601", 3600),
        ("Sun, 08 Nov 2026 00:02:00 GMT", 120),
        ("Sat, 07 Nov 2026 23:59:00 GMT", 1),
        ("Sun, 08 Nov 2026 01:00:00 GMT", 3600),
        ("Sun, 08 Nov 2026 01:00:01 GMT", 3600),
        ("Fri, 31 Dec 9999 23:59:59 GMT", 3600),
        ("999999999", 3600),
        ("9" * 5000, 3600),
        ("0" * 5000 + "120", 120),
    ],
)
def test_retry_after_seconds_http_date_and_invalid_text(value, expected):
    assert retry_delay(value, NOW) == expected


@pytest.mark.parametrize(
    "delay,expected", [(None, 60), (0, 1), (-1, 1), (999999999, 3600)]
)
def test_cache_bounds_provider_cooldown_without_relying_on_http_parser(delay, expected):
    seconds = [1000.0]
    attempts = []

    class Provider:
        def get_quote(self, ticker, deadline):
            attempts.append(ticker)
            raise QuotesUnavailable({ticker}, "rate_limited", delay)

    cache = CachedQuotes(Provider(), clock=lambda: seconds[0])
    with pytest.raises(QuotesUnavailable) as first:
        cache.get_quotes({"TEST3"})
    assert first.value.reason == "rate_limited"
    assert first.value.retry_after == expected
    seconds[0] += expected - 0.5
    with pytest.raises(QuotesUnavailable) as blocked:
        cache.get_quotes({"OTHER3"})
    assert blocked.value.retry_after == 1
    assert attempts == ["TEST3"]
    seconds[0] += 0.5
    with pytest.raises(QuotesUnavailable) as recovered:
        cache.get_quotes({"OTHER3"})
    assert recovered.value.retry_after == expected
    assert attempts == ["TEST3", "OTHER3"]


def test_one_deadline_for_entire_batch_and_no_late_quote_cached(market):
    seconds, calls, reply, _, cache = market

    def delayed(request):
        seconds[0] += 6
        return httpx2.Response(200, content=body(request.url.path.rsplit("/", 1)[-1]))

    reply[0] = delayed
    with pytest.raises(QuotesUnavailable) as error:
        cache.get_quotes({"AAA3", "BBB3", "CCC3"})
    assert error.value.reason == "timeout"
    assert error.value.tickers == ("BBB3", "CCC3")
    assert len(calls) == 2
    assert calls[1].extensions["timeout"]["read"] <= 1
    seconds[0] += 5
    reply[0] = lambda request: httpx2.Response(200, content=body("BBB3"))
    assert not cache.get_quotes({"BBB3"})["BBB3"].cached


def test_stream_body_limit_and_deadline_close_response(market):
    seconds, _, reply, provider, _ = market
    closed = []

    class Stream(httpx2.SyncByteStream):
        def __iter__(self):
            yield b"{"
            seconds[0] += 11
            yield b'"results":[]}'

        def close(self):
            closed.append(True)

    reply[0] = lambda request: httpx2.Response(200, stream=Stream())
    with pytest.raises(QuotesUnavailable) as error:
        provider.get_quote("TEST3", 1010)
    assert error.value.reason == "timeout"
    assert closed == [True]
    seconds[0] = 1000
    reply[0] = lambda request: httpx2.Response(200, content=b" " * 65537)
    with pytest.raises(QuotesUnavailable) as large:
        provider.get_quote("TEST3", 1010)
    assert large.value.reason == "invalid_response"


def test_concurrent_cold_reads_share_one_http_request(market):
    _, calls, reply, _, cache = market
    entered, release, waiting = Event(), Event(), Event()

    class ObservedEvent:
        def __init__(self, real):
            self.real = real

        def wait(self, timeout):
            waiting.set()
            return self.real.wait(timeout)

        def set(self):
            self.real.set()

    def blocked(request):
        entered.set()
        assert release.wait(5)
        return httpx2.Response(200, content=body())

    reply[0] = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_quotes, {"TEST3"})
        assert entered.wait(5)
        with cache._lock:
            flight = cache._flights["TEST3"]
            flight.done = ObservedEvent(flight.done)
        second = pool.submit(cache.get_quotes, {"TEST3"})
        try:
            assert waiting.wait(5)
            assert len(calls) == 1
            assert not first.done() and not second.done()
        finally:
            release.set()
        cold, warm = first.result(timeout=5), second.result(timeout=5)
    assert len(calls) == 1
    assert cold["TEST3"].price == warm["TEST3"].price == Decimal(42)
    assert not cold["TEST3"].cached and warm["TEST3"].cached
    assert cache._flights == {}


def test_waiting_for_http_slot_has_bounded_timeout_without_http(market):
    seconds, calls, _, provider, _ = market
    cache = CachedQuotes(provider, budget=0.01, concurrency=1, clock=lambda: seconds[0])
    cache._slots.acquire()
    try:
        with pytest.raises(QuotesUnavailable) as error:
            cache.get_quotes({"TEST3"})
    finally:
        cache._slots.release()
    assert error.value.reason == "timeout"
    assert calls == []
    assert cache._flights == {}
    assert cache.get_quotes({"TEST3"})["TEST3"].price == Decimal(42)


def test_deep_json_is_invalid_negatively_cached_and_recovers_at_expiry(market):
    seconds, calls, reply, _, cache = market
    nested = b'{"results":' + b"[" * 15000 + b"0" + b"]" * 15000 + b"}"
    assert len(nested) < 65536
    reply[0] = lambda request: httpx2.Response(200, content=nested)
    for instant in (1000.0, 1004.999):
        seconds[0] = instant
        with pytest.raises(QuotesUnavailable) as caught:
            cache.get_quotes({"TEST3"})
        assert caught.value.reason == "invalid_response"
        assert caught.value.tickers == ("TEST3",)
        assert cache._flights == {}
    assert len(calls) == 1
    seconds[0] = 1005.0
    reply[0] = lambda request: httpx2.Response(200, content=body())
    assert cache.get_quotes({"TEST3"})["TEST3"].price == Decimal(42)
    assert len(calls) == 2
