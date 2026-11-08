"""Delimita uma transação que pode envolver os três repositórios.

Cada contexto cria uma sessão própria, compartilhada apenas pelos repositórios
daquela unidade de trabalho. Os repositórios podem enviar SQL com ``flush``,
mas somente ``commit`` confirma os dados. Sair sem confirmar, inclusive por
exceção, desfaz as alterações pendentes e fecha a sessão.

Regras financeiras e autorização pertencem aos casos de uso e ao domínio.
Este módulo controla apenas a fronteira transacional de infraestrutura.
"""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from infrastructure.database.repositories import (
    SqlAlchemyAssetRepository,
    SqlAlchemyTransactionRepository,
    SqlAlchemyUserRepository,
)


class SqlAlchemyUnitOfWork:
    """Coordena uma operação atômica com confirmação explícita e uso único.

    Exemplo de uso pelo caso de uso::

        with uow_factory() as uow:
            uow.users.add(user)
            uow.assets.add(asset)
            uow.commit()

    Sem a última linha, as duas inclusões são revertidas. Repositórios não
    devem escapar do contexto, pois sua sessão será fechada. Um commit já
    concluído não pode ser desfeito por uma exceção posterior; confirme
    somente depois de terminar as validações e escritas da operação.
    """

    def __init__(self, session_factory: sessionmaker[Session]):
        """Recebe a fábrica da aplicação; ainda não abre sessão ou conexão."""
        self._session_factory = session_factory
        self._session: Session | None = None
        self._used = False

    def __enter__(self) -> Self:
        """Cria a sessão e os repositórios; rejeita reentrada ou reutilização.

        A conexão PostgreSQL é obtida pela sessão quando o primeiro SQL
        precisar ser executado, não pela simples entrada no contexto.
        """
        if self._used:
            raise RuntimeError("A unit of work cannot be reused")
        self._used = True
        self._session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self._session)
        self.assets = SqlAlchemyAssetRepository(self._session)
        self.transactions = SqlAlchemyTransactionRepository(self._session)
        return self

    def commit(self) -> None:
        """Confirma as escritas da sessão ativa, propagando falhas do banco.

        Fora do contexto, levanta ``RuntimeError``. Se a confirmação falhar,
        a saída do contexto ainda executará rollback e fechamento.
        """
        if self._session is None:
            raise RuntimeError("Commit requires an active unit of work")
        self._session.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Reverte pendências e fecha a sessão, sem suprimir a exceção recebida.

        O rollback também é chamado na saída normal; depois de um commit
        bem-sucedido, não desfaz os dados já confirmados. O fechamento é
        tentado mesmo se o rollback levantar uma exceção.
        """
        if self._session is not None:
            try:
                self._session.rollback()
            finally:
                self._session.close()
                self._session = None
