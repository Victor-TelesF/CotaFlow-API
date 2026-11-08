"""Recursos de banco pertencentes ao ciclo de vida de uma instância da API.

O engine gerencia o pool; a fábrica fornece sessões independentes às unidades
de trabalho. Importar este módulo não lê configurações nem abre conexões.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from infrastructure.config import Settings


class Database:
    """Agrupa engine e fábrica de sessões; não executa migrations nem commits.

    Construir o engine ainda não conecta ao PostgreSQL. ``pool_pre_ping``
    verifica conexões retiradas do pool. ``hide_parameters`` oculta parâmetros
    SQL nas mensagens do SQLAlchemy, mas não redige o DETAIL do PostgreSQL;
    erros brutos do banco não devem ser expostos pela fronteira HTTP.
    """

    def __init__(self, settings: Settings):
        """Cria o pool preguiçoso e sessões que não podem reabrir após close."""
        self.engine = create_engine(
            settings.database_url, pool_pre_ping=True, hide_parameters=True
        )
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, close_resets_only=False
        )

    def dispose(self) -> None:
        """Fecha conexões devolvidas ao pool durante o shutdown da aplicação.

        Unidades de trabalho devem fechar suas sessões antes desta chamada:
        ``dispose`` não força o fechamento de conexões ainda emprestadas.
        """
        self.engine.dispose()
