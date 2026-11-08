"""Dados de identidade e avaliação que atravessam as fronteiras da aplicação.

Não são entidades ORM nem schemas HTTP. Validação/normalização de email e
geração de hash pertencem ao cadastro/login da etapa de autenticação.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from domain.portfolio import Portfolio


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


@dataclass(frozen=True)
class Quote:
    """Fotografia normalizada; cache não muda o horário original da busca."""

    ticker: str
    price: Decimal
    currency: str
    source: str
    quoted_at: datetime | None
    fetched_at: datetime
    cached: bool = False


@dataclass(frozen=True)
class PortfolioValuation:
    """Carteira e cotações completas usadas em todos os indicadores da resposta."""

    portfolio: Portfolio
    quotes: dict[str, Quote]
