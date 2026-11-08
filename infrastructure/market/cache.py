"""Cache limitado por processo; um lock serializa buscas entre threads.

Revalidar sob o lock deduplica cache frio concorrente. Nenhum banco é mantido
por este recurso. Falhas não entram no cache; 429 estabelece cooldown global
para respeitar Retry-After, sem dormir ou repetir chamadas na requisição.
"""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from math import floor
from threading import Lock
from time import monotonic

from application.exceptions import QuotesUnavailable
from application.types import Quote
from infrastructure.market.brapi import BrapiQuotes


class CachedQuotes:
    """TTL desde o fim da busca; horário de mercado pode ser anterior ao TTL."""

    def __init__(
        self,
        provider: BrapiQuotes,
        *,
        ttl: float = 1800,
        capacity: int = 512,
        budget: float = 10,
        clock: Callable[[], float] = monotonic,
    ):
        if ttl <= 0 or capacity < 1 or budget <= 0:
            raise ValueError("Cache limits must be positive")
        self._provider = provider
        self._ttl = ttl
        self._capacity = capacity
        self._budget = budget
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Quote]] = OrderedDict()
        self._lock = Lock()
        self._cooldown = (0.0, 0)

    def get_quotes(self, tickers: set[str]) -> dict[str, Quote]:
        """Entrega fotografia completa ou indica todos os tickers não resolvidos."""
        if not tickers:
            return {}
        deadline = self._clock() + self._budget
        if not self._lock.acquire(timeout=self._budget):
            raise QuotesUnavailable(tickers, "timeout")
        try:
            if self._clock() >= deadline:
                raise QuotesUnavailable(tickers, "timeout")
            snapshot = {}
            # Primeiro recolhe TODOS os hits: uma falha posterior só lista faltantes.
            for ticker in sorted(tickers):
                entry = self._entries.get(ticker)
                if entry is not None:
                    expires_at, quote = entry
                    if self._clock() < expires_at:
                        snapshot[ticker] = replace(quote, cached=True)
                        self._entries.move_to_end(ticker)
                    else:
                        del self._entries[ticker]
            for ticker in sorted(tickers - snapshot.keys()):
                missing = tickers - snapshot.keys()
                now = self._clock()
                if now >= deadline:
                    raise QuotesUnavailable(missing, "timeout")
                started_at, delay = self._cooldown
                retry_after = delay - floor(now - started_at)
                if retry_after > 0:
                    raise QuotesUnavailable(missing, "rate_limited", retry_after)
                try:
                    quote = self._provider.get_quote(ticker, deadline)
                except QuotesUnavailable as error:
                    if error.reason == "rate_limited":
                        self._cooldown = (self._clock(), error.retry_after or 60)
                    raise QuotesUnavailable(
                        missing, error.reason, error.retry_after
                    ) from None
                if self._clock() >= deadline:
                    raise QuotesUnavailable(missing, "timeout")
                self._entries[ticker] = (self._clock() + self._ttl, quote)
                self._entries.move_to_end(ticker)
                while len(self._entries) > self._capacity:
                    self._entries.popitem(last=False)
                snapshot[ticker] = quote
            return snapshot
        finally:
            self._lock.release()
