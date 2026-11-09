"""Admissão de operações antes de carregar/bloquear o histórico financeiro."""

from uuid import UUID

from application.exceptions import TransactionRateLimited
from infrastructure.user_limits import UserMinuteBudget


class TransactionLimits(UserMinuteBudget):
    """Sucessos e erros de domínio consomem quota; reinício perde o estado."""

    def check(self, user_id: UUID) -> None:
        retry_after = self.admit(user_id)
        if retry_after is not None:
            raise TransactionRateLimited(retry_after)
