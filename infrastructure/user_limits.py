"""Janela móvel compartilhada por quotas de usuários; memória limitada."""

from collections import deque
from collections.abc import Callable
from math import ceil
from threading import Lock
from time import monotonic
from uuid import UUID


class UserMinuteBudget:
    """Admissão por usuário sem evicção de quotas ativas para contornar o teto."""

    def __init__(
        self,
        per_minute: int,
        *,
        capacity: int = 4096,
        clock: Callable[[], float] = monotonic,
    ):
        if min(per_minute, capacity) < 1:
            raise ValueError("Limits must be positive")
        self._per_minute = per_minute
        self._capacity = capacity
        self._clock = clock
        self._users: dict[UUID, deque[float]] = {}
        self._lock = Lock()

    def admit(self, user_id: UUID) -> int | None:
        with self._lock:
            now = self._clock()
            for key, attempts in list(self._users.items()):
                while attempts and attempts[0] <= now - 60:
                    attempts.popleft()
                if not attempts:
                    del self._users[key]
            attempts = self._users.get(user_id)
            if attempts is None:
                if len(self._users) >= self._capacity:
                    # Não remove usuário ativo nem permite crescimento ilimitado.
                    return 60
                attempts = self._users[user_id] = deque()
            if len(attempts) >= self._per_minute:
                return max(1, ceil(60 - (now - attempts[0])))
            attempts.append(now)
            return None
