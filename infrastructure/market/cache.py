"""Cache por processo com estado curto, deduplicação por ticker e filas limitadas."""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from math import floor
from threading import BoundedSemaphore, Event, Lock
from time import monotonic

from application.exceptions import QuotesUnavailable
from application.types import Quote
from infrastructure.market.brapi import BrapiQuotes, bounded_retry_delay
from infrastructure.market.limits import WindowBudget


@dataclass(frozen=True)
class _Failure:
    reason: str
    retry_after: int | None = None


@dataclass
class _Flight:
    done: Event = field(default_factory=Event)
    quote: Quote | None = None
    failure: _Failure | None = None


class CachedQuotes:
    """Preços e falhas dividem a capacidade; HTTP/esperas nunca seguram o lock.

    Cada avaliação compartilha um deadline. Timeouts síncronos de I/O não
    garantem SLA rígido de parede. No máximo ``pending_capacity`` símbolos
    aguardam ou executam HTTP; interessados no mesmo símbolo compartilham voo.
    """

    def __init__(
        self,
        provider: BrapiQuotes,
        *,
        ttl: float = 1800,
        capacity: int = 512,
        budget: float = 10,
        concurrency: int = 2,
        pending_capacity: int = 64,
        external_per_minute: int = 30,
        clock: Callable[[], float] = monotonic,
    ):
        if min(ttl, capacity, budget, concurrency, pending_capacity) <= 0:
            raise ValueError("Cache limits must be positive")
        self._provider = provider
        self._ttl = ttl
        self._capacity = capacity
        self._budget = budget
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Quote | _Failure]] = OrderedDict()
        self._lock = Lock()
        self._cooldown = (0.0, 0)
        self._authentication_until = 0.0
        self._flights: dict[str, _Flight] = {}
        self._pending_capacity = pending_capacity
        self._slots = BoundedSemaphore(concurrency)
        self._external_budget = WindowBudget(external_per_minute, clock=clock)

    def _prune(self) -> None:
        now = self._clock()
        for ticker, (expiry, _) in list(self._entries.items()):
            if now >= expiry:
                del self._entries[ticker]

    def _store(self, ticker: str, value: Quote | _Failure, ttl: float) -> None:
        self._entries[ticker] = (self._clock() + ttl, value)
        self._entries.move_to_end(ticker)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def _global_failure(self) -> _Failure | None:
        now = self._clock()
        started_at, delay = self._cooldown
        retry_after = delay - floor(now - started_at)
        if retry_after > 0:
            return _Failure("rate_limited", retry_after)
        if now < self._authentication_until:
            return _Failure("authentication")
        return None

    def get_quotes(self, tickers: set[str]) -> dict[str, Quote]:
        """Entrega fotografia completa ou todos os símbolos ainda não resolvidos."""
        deadline = self._clock() + self._budget
        snapshot = {}
        # Recolhe todos os hits antes de qualquer falha, inclusive no cooldown.
        with self._lock:
            self._prune()
            for ticker in sorted(tickers):
                entry = self._entries.get(ticker)
                if entry is not None and isinstance(entry[1], Quote):
                    snapshot[ticker] = replace(entry[1], cached=True)
                    self._entries.move_to_end(ticker)
        for ticker in sorted(tickers - snapshot.keys()):
            try:
                snapshot[ticker] = self._get_quote(ticker, deadline)
            except QuotesUnavailable as error:
                raise QuotesUnavailable(
                    tickers - snapshot.keys(), error.reason, error.retry_after
                ) from None
        return snapshot

    def _get_quote(self, ticker: str, deadline: float) -> Quote:
        with self._lock:
            self._prune()
            entry = self._entries.get(ticker)
            if entry is not None:
                self._entries.move_to_end(ticker)
                value = entry[1]
                if isinstance(value, Quote):
                    return replace(value, cached=True)
                raise QuotesUnavailable({ticker}, value.reason, value.retry_after)
            failure = self._global_failure()
            if failure is not None:
                raise QuotesUnavailable({ticker}, failure.reason, failure.retry_after)
            flight = self._flights.get(ticker)
            owner = flight is None
            if owner:
                if len(self._flights) >= self._pending_capacity:
                    raise QuotesUnavailable({ticker}, "busy")
                flight = _Flight()
                self._flights[ticker] = flight
        if not owner:
            if not flight.done.wait(max(0, deadline - self._clock())):
                raise QuotesUnavailable({ticker}, "timeout")
            if self._clock() >= deadline:
                raise QuotesUnavailable({ticker}, "timeout")
            if flight.failure is not None:
                raise QuotesUnavailable(
                    {ticker}, flight.failure.reason, flight.failure.retry_after
                )
            return replace(flight.quote, cached=True)
        try:
            remaining = deadline - self._clock()
            if remaining <= 0 or not self._slots.acquire(timeout=remaining):
                raise QuotesUnavailable({ticker}, "timeout")
            try:
                with self._lock:
                    failure = self._global_failure()
                    if failure is not None:
                        raise QuotesUnavailable(
                            {ticker}, failure.reason, failure.retry_after
                        )
                if self._clock() >= deadline:
                    raise QuotesUnavailable({ticker}, "timeout")
                retry_after = self._external_budget.admit()
                if retry_after is not None:
                    raise QuotesUnavailable({ticker}, "external_budget", retry_after)
                # Somente tentativas externas gastam orçamento, inclusive erros.
                try:
                    quote = self._provider.get_quote(ticker, deadline)
                    if self._clock() >= deadline:
                        raise QuotesUnavailable({ticker}, "timeout")
                except QuotesUnavailable as error:
                    with self._lock:
                        if error.reason == "rate_limited":
                            delay = bounded_retry_delay(error.retry_after)
                            previous_start, previous_delay = self._cooldown
                            delay = max(
                                delay,
                                previous_delay - floor(self._clock() - previous_start),
                            )
                            self._cooldown = (self._clock(), delay)
                            error = QuotesUnavailable({ticker}, error.reason, delay)
                        elif error.reason == "authentication":
                            self._authentication_until = self._clock() + 5
                        else:
                            self._store(
                                ticker,
                                _Failure(error.reason),
                                30 if error.reason == "not_found" else 5,
                            )
                    raise error from None
                with self._lock:
                    self._store(ticker, quote, self._ttl)
                flight.quote = quote
                return quote
            finally:
                self._slots.release()
        except BaseException as error:
            # Libera interessados também em falha inesperada, sem guardar traceback.
            flight.failure = _Failure(
                error.reason if isinstance(error, QuotesUnavailable) else "unavailable",
                error.retry_after if isinstance(error, QuotesUnavailable) else None,
            )
            raise
        finally:
            with self._lock:
                del self._flights[ticker]
                flight.done.set()
