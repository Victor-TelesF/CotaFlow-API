"""Adapta somente a cotação básica brapi, um símbolo por chamada.

O JSON é lido diretamente em Decimal. Não seguem redirects, não há retry
implícito nem token na URL. O orçamento monotônico é compartilhado pelo lote;
I/O síncrono tem timeout próprio e o corpo é limitado e lido em streaming.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from math import ceil
from time import monotonic

import httpx2

from application.exceptions import QuotesUnavailable
from application.types import Quote
from domain.assets.asset import TICKER_PATTERN

MAX_BODY_BYTES = 65_536
DEFAULT_RETRY_DELAY = 60
MAX_RETRY_DELAY = 3600


def bounded_retry_delay(seconds: int | None) -> int:
    """Política compartilhada pelo parser e pela fronteira de cache/provider."""
    return (
        DEFAULT_RETRY_DELAY
        if seconds is None
        else max(1, min(MAX_RETRY_DELAY, seconds))
    )


def retry_delay(value: str | None, now: datetime) -> int:
    """Limita segundos/HTTP-date a uma hora, sem ecoar texto em headers."""
    if value is not None:
        try:
            if value.isascii() and value.isdigit():
                # Bound before int(): untrusted headers may exceed Python's digit limit.
                digits = value.lstrip("0")
                if len(digits) > len(str(MAX_RETRY_DELAY)):
                    return MAX_RETRY_DELAY
                seconds = int(digits or "0")
            else:
                seconds = ceil((parsedate_to_datetime(value) - now).total_seconds())
            return bounded_retry_delay(seconds)
        except (ValueError, TypeError, OverflowError):
            pass
    return DEFAULT_RETRY_DELAY


class BrapiQuotes:
    """O cliente pertence ao lifespan; esta classe não cria recursos globais."""

    def __init__(
        self,
        client: httpx2.Client,
        *,
        clock: Callable[[], float] = monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._client = client
        self._clock = clock
        self._now = now

    def get_quote(self, ticker: str, deadline: float) -> Quote:
        """Rejeita símbolos não seguros para URL, respostas incompletas e tardias.

        Timeouts HTTPX2 são por operação de I/O, não um SLA rígido de parede.
        O deadline impede iniciar outro ticker e rejeita respostas após o prazo.
        Cada chunk é conferido para impedir corpo lento ilimitado.
        """
        if not re.fullmatch(TICKER_PATTERN, ticker):
            raise QuotesUnavailable({ticker}, "invalid_symbol")
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise QuotesUnavailable({ticker}, "timeout")
        try:
            with self._client.stream(
                "GET",
                f"https://brapi.dev/api/quote/{ticker}",
                timeout=httpx2.Timeout(min(2.0, remaining / 4)),
                follow_redirects=False,
            ) as response:
                if response.status_code == 429:
                    raise QuotesUnavailable(
                        {ticker},
                        "rate_limited",
                        retry_delay(response.headers.get("Retry-After"), self._now()),
                    )
                if response.status_code in {401, 403}:
                    raise QuotesUnavailable({ticker}, "authentication")
                if response.status_code == 404:
                    raise QuotesUnavailable({ticker}, "not_found")
                if response.status_code != 200:
                    raise QuotesUnavailable({ticker})
                body = bytearray()
                for chunk in response.iter_bytes():
                    if self._clock() >= deadline:
                        raise QuotesUnavailable({ticker}, "timeout")
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        raise QuotesUnavailable({ticker}, "invalid_response")
                    body.extend(chunk)
            if self._clock() >= deadline:
                raise QuotesUnavailable({ticker}, "timeout")
            quote = self._parse(bytes(body), ticker)
            if self._clock() >= deadline:
                raise QuotesUnavailable({ticker}, "timeout")
            return quote
        except httpx2.TimeoutException:
            raise QuotesUnavailable({ticker}, "timeout") from None
        except httpx2.HTTPError:
            raise QuotesUnavailable({ticker}) from None

    def _parse(self, body: bytes, ticker: str) -> Quote:
        """Exige identidade/moeda exatas, preço finito e timestamp com timezone.

        Limites de magnitude/dígitos evitam números patológicos do transporte;
        não quantizam a cotação à escala de armazenamento das operações.
        """
        try:
            data = json.loads(body, parse_float=Decimal, parse_int=Decimal)
            results = data["results"]
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError
            item = results[0]
            price = item["regularMarketPrice"]
            if (
                item["symbol"] != ticker
                or item["currency"] != "BRL"
                or not isinstance(price, Decimal)
                or not price.is_finite()
                or price <= 0
                or len(price.as_tuple().digits) > 50
                or abs(price.adjusted()) > 18
            ):
                raise ValueError
            timestamp = item.get("regularMarketTime")
            quoted_at = None
            if timestamp is not None:
                if not isinstance(timestamp, str):
                    raise ValueError
                quoted_at = datetime.fromisoformat(timestamp)
                if quoted_at.tzinfo is None or quoted_at.utcoffset() is None:
                    raise ValueError
                quoted_at = quoted_at.astimezone(UTC)
            return Quote(ticker, price, "BRL", "brapi", quoted_at, self._now())
        except (
            ValueError,
            TypeError,
            KeyError,
            ArithmeticError,
            UnicodeError,
            RecursionError,
        ):
            raise QuotesUnavailable({ticker}, "invalid_response") from None
