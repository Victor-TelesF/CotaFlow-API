"""Eventos JSON de falha com campos permitidos e volume limitado por instância."""

import json
import logging
import re
from collections import deque
from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import uuid4

logger = logging.getLogger("cotaflow.diagnostics")
QUOTE_REASONS = frozenset(
    {
        "unavailable",
        "invalid_symbol",
        "timeout",
        "rate_limited",
        "authentication",
        "not_found",
        "invalid_response",
        "busy",
        "external_budget",
    }
)


class FailureDiagnostics:
    """Registra apenas metadados seguros de banco/cotações, sem traceback.

    Cada aplicação possui uma instância. No máximo dez eventos são emitidos
    em qualquer janela móvel de 60 s, compartilhada entre as categorias.
    O próximo evento admitido informa quantos foram suprimidos (contador
    saturado em 2^31-1). Não há fila, timer ou armazenamento por usuário/rota.
    Reinícios perdem contadores; outras instâncias têm orçamentos próprios.
    O logger usa os handlers do ambiente, sem configurar o logging global.
    """

    def __init__(self, *, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._events: deque[float] = deque()
        self._suppressed = 0
        self._lock = Lock()

    def record(
        self,
        *,
        category: Literal["database", "quotes"],
        method: str,
        route: str | None,
        error: Exception,
        reason: str | None = None,
        ticker_count: int = 0,
    ) -> str:
        """Gera correlação própria mesmo quando o evento excede o orçamento.

        Recebe o template de rota do framework, nunca URL/path/query crus.
        Não converte a exceção, sua causa ou parâmetros para texto. SQLSTATE
        só é lido de ``orig.sqlstate`` e aceito no formato ASCII de cinco
        caracteres; motivos de cotação vêm de uma lista fechada.
        """
        request_id = uuid4().hex
        with self._lock:
            now = self._clock()
            while self._events and self._events[0] <= now - 60:
                self._events.popleft()
            if len(self._events) >= 10:
                self._suppressed = min(self._suppressed + 1, 2**31 - 1)
                return request_id
            self._events.append(now)
            suppressed, self._suppressed = self._suppressed, 0

        exception_type = type(error).__name__
        data = {
            "event": "request_failure",
            "category": category,
            "method": method
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
            else "OTHER",
            "route": route
            if isinstance(route, str)
            and re.fullmatch(r"/[A-Za-z0-9_/{}/.\-]{0,159}", route)
            else "unknown",
            "request_id": request_id,
            "exception_type": exception_type
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", exception_type)
            else "Exception",
            "suppressed": suppressed,
        }
        if category == "database":
            sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
            if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
                data["sqlstate"] = sqlstate
        else:
            data["reason"] = reason if reason in QUOTE_REASONS else "unknown"
            data["ticker_count"] = ticker_count
        logger.warning(json.dumps(data, separators=(",", ":")))
        return request_id
