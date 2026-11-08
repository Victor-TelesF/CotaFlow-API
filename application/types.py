"""Dados de identidade que atravessam a fronteira de persistência.

Não são entidades ORM nem schemas HTTP. Validação/normalização de email e
geração de hash pertencem ao cadastro/login da etapa de autenticação.
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(frozen=True)
class User:
    """Identidade imutável com UUID, email canônico e hash previamente gerado.

    Esta estrutura não verifica o formato do email nem se a string recebida
    é um hash válido. O hash não aparece no repr, mas continua presente no
    objeto e não deve integrar respostas HTTP ou logs.
    """

    email: str
    password_hash: str = field(repr=False)
    id: UUID = field(default_factory=uuid4)
