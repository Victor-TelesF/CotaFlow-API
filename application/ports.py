"""Contratos de persistência e segurança sem dependência dos adaptadores.

Os repositórios representam as três coleções de fatos; a unidade de trabalho
coordena sua transação. Implementações SQLAlchemy ficam na infraestrutura.
Estes protocolos descrevem fronteiras; não implementam cálculos, JWT ou HTTP.
"""

from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from application.types import Quote, User
from domain.assets import Asset
from domain.portfolio import Transaction


class UserRepository(Protocol):
    """Armazena e consulta identidades com email canônico e hash já preparados."""

    def add(self, user: User) -> None:
        """Inclui a identidade na transação atual, sem confirmar a escrita."""
        ...

    def get(self, user_id: UUID) -> User | None:
        """Retorna a identidade pelo UUID ou None quando não existir."""
        ...

    def get_by_email(self, email: str) -> User | None:
        """Retorna a identidade pelo email normalizado ou None."""
        ...

    def lock(self, user_id: UUID) -> bool:
        """Bloqueia a linha até o fim da UoW; False se a identidade não existir."""
        ...


class AssetRepository(Protocol):
    """Armazena o catálogo local, sem posição financeira ou preços de mercado."""

    def add(self, asset: Asset) -> None:
        """Inclui um ativo validado pelo domínio, sem commit próprio."""
        ...

    def get(self, ticker: str) -> Asset | None:
        """Consulta o ticker canônico exato; ausência resulta em None."""
        ...

    def list_page(self, limit: int, offset: int) -> list[Asset]:
        """Página ordenada pelo ticker canônico."""
        ...


class TransactionRepository(Protocol):
    """Persiste operações e fornece o histórico completo para reconstrução."""

    def add(self, user_id: UUID, transaction: Transaction) -> Transaction:
        """Preserva os fatos da operação e retorna sua sequência persistida."""
        ...

    def list_for_user(self, user_id: UUID) -> list[Transaction]:
        """Retorna somente o histórico do usuário, ordenado por data/sequência."""
        ...

    def list_page(self, user_id: UUID, limit: int, offset: int) -> list[Transaction]:
        """Página do dono, por data e sequência crescentes, sem truncar cálculos."""
        ...


class UnitOfWork(Protocol):
    """Fronteira atômica: contexto sem commit explícito descarta pendências.

    O caso de uso acessa os repositórios dentro do contexto e confirma apenas
    após concluir suas validações/escritas. Uma instância corresponde a uma
    operação; seus recursos não devem ser reutilizados depois da saída.
    """

    users: UserRepository
    assets: AssetRepository
    transactions: TransactionRepository

    def __enter__(self) -> Self:
        """Disponibiliza os repositórios da transação atual."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Desfaz pendências e libera recursos, propagando a exceção recebida."""
        ...

    def commit(self) -> None:
        """Confirma a transação ativa; repositórios não confirmam isoladamente."""
        ...


class PasswordHasher(Protocol):
    """Fronteira de hash; nunca recebe nem retorna entidades de persistência."""

    def hash(self, password: str) -> str:
        """Gera um hash salgado para persistência; não preserva a senha original."""
        ...

    def verify(self, password: str, password_hash: str | None) -> bool:
        """Verifica a senha; None executa trabalho fictício para email ausente."""
        ...


class TokenService(Protocol):
    """Emite tokens e resolve somente assuntos cuja autenticação foi validada."""

    def issue(self, user_id: UUID) -> str:
        """Emite token assinado com validade limitada para a identidade."""
        ...

    def verify(self, token: str) -> UUID:
        """Retorna UUID validado ou levanta InvalidToken sem revelar o token."""
        ...


class QuoteProvider(Protocol):
    """Obtém uma fotografia completa ou levanta QuotesUnavailable."""

    def get_quotes(self, tickers: set[str]) -> dict[str, Quote]:
        """Sem preços inventados, vencidos ou totais parciais."""
        ...
