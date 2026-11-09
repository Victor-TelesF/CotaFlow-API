"""Orçamentos móveis por processo; sem rede, espera ou chaves ilimitadas."""

from collections import deque
from collections.abc import Callable
from math import ceil
from threading import Lock
from time import monotonic
from uuid import UUID

from application.exceptions import ValuationRateLimited
from infrastructure.user_limits import UserMinuteBudget


class WindowBudget:
    """Janela móvel de 60 s; burst máximo igual à capacidade configurada."""

    def __init__(self, capacity: int, *, clock: Callable[[], float] = monotonic):
        if capacity < 1:
            raise ValueError("Budget must be positive")
        self._capacity = capacity
        self._clock = clock
        self._attempts: deque[float] = deque()
        self._lock = Lock()

    def admit(self) -> int | None:
        with self._lock:
            now = self._clock()
            while self._attempts and self._attempts[0] <= now - 60:
                self._attempts.popleft()
            if len(self._attempts) >= self._capacity:
                return max(1, ceil(60 - (now - self._attempts[0])))
            self._attempts.append(now)
            return None


class ValuationLimits(UserMinuteBudget):
    """Traduz rejeição da quota para o contrato específico de avaliação."""

    def __init__(self, per_minute: int = 10, **kwargs):
        super().__init__(per_minute, **kwargs)

    def check(self, user_id: UUID) -> None:
        retry_after = self.admit(user_id)
        if retry_after is not None:
            raise ValuationRateLimited(retry_after)
