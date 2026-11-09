"""Admissão de autenticação por processo, antes de Argon2 e persistência."""

from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from hmac import digest
from ipaddress import ip_address
from math import ceil
from secrets import token_bytes
from threading import BoundedSemaphore, Lock
from time import monotonic

from application.exceptions import AuthenticationLimited
from infrastructure.config import AuthSettings


def client_identity(host: str | None) -> str:
    """Somente o peer ASGI já tratado pelo servidor, nunca headers HTTP."""
    try:
        address = ip_address(host or "")
    except ValueError:
        return "unknown"
    mapped = getattr(address, "ipv4_mapped", None)
    return str(mapped or address)


class AuthenticationLimits:
    """Janelas móveis, HMAC efêmero de email e slots sem fila.

    Até 4096 chaves compartilhadas entre IPs e pares IP/email. Quotas ativas
    não são expulsas para admitir outra chave. Reinício perde o estado;
    réplicas não compartilham quotas. Nenhum bloqueio permanente por conta.
    """

    def __init__(
        self,
        settings: AuthSettings,
        *,
        capacity: int = 4096,
        clock: Callable[[], float] = monotonic,
    ):
        if capacity < 1:
            raise ValueError("Capacity must be positive")
        self._settings = settings
        self._capacity = capacity
        self._clock = clock
        self._key = token_bytes(32)
        self._attempts: dict[tuple, tuple[int, deque[float]]] = {}
        self._lock = Lock()
        self._slots = BoundedSemaphore(settings.AUTH_MAX_CONCURRENT)

    def _consume(self, key: tuple, limit: int, window: int) -> None:
        with self._lock:
            now = self._clock()
            for identity, (period, attempts) in list(self._attempts.items()):
                while attempts and attempts[0] <= now - period:
                    attempts.popleft()
                if not attempts:
                    del self._attempts[identity]
            entry = self._attempts.get(key)
            if entry is None:
                if len(self._attempts) >= self._capacity:
                    raise AuthenticationLimited(600)
                entry = self._attempts[key] = (window, deque())
            _, attempts = entry
            if len(attempts) >= limit:
                raise AuthenticationLimited(ceil(window - (now - attempts[0])))
            attempts.append(now)

    def check_ip(self, operation: str, host: str | None) -> None:
        """Inclui entradas inválidas e rejeições posteriores por par/slot."""
        ip = client_identity(host)
        if operation == "register":
            self._consume(
                (operation, ip), self._settings.AUTH_REGISTER_PER_10_MINUTES, 600
            )
        else:
            self._consume((operation, ip), self._settings.AUTH_LOGIN_PER_MINUTE, 60)

    @contextmanager
    def admit(self, operation: str, host: str | None, email: str) -> Iterator[None]:
        """Email vem do schema canônico; nenhuma leitura de usuário para limitar."""
        if operation == "login":
            pseudonym = digest(self._key, email.encode("utf-8"), sha256)
            self._consume(
                ("pair", client_identity(host), pseudonym),
                self._settings.AUTH_LOGIN_PAIR_PER_5_MINUTES,
                300,
            )
        if not self._slots.acquire(blocking=False):
            raise AuthenticationLimited(1)
        try:
            yield
        finally:
            self._slots.release()
