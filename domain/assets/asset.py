"""Identidade de uma ação brasileira ou FII, independente de banco e HTTP.

Preço da operação pertence à transação; preço atual é uma entrada da avaliação.
O cadastro de ativo não armazena nenhum desses preços ou uma posição do usuário.
"""

import re
from dataclasses import dataclass

from ..enums import AssetType
from ..exceptions import InvalidValueError

TICKER_PATTERN = r"[A-Z0-9]{1,20}"
MAX_RAW_TICKER = 64
MAX_RAW_NAME = 256
MAX_ASSET_NAME = 120


def normalize_ticker(ticker: str) -> str:
    """Normaliza a identidade local sem certificar sua existência na bolsa."""
    if not isinstance(ticker, str) or len(ticker) > MAX_RAW_TICKER:
        raise InvalidValueError("Invalid ticker input")
    value = ticker.strip().upper()
    if not re.fullmatch(TICKER_PATTERN, value):
        raise InvalidValueError("Ticker must contain 1 to 20 ASCII letters or digits")
    return value


def normalize_asset_name(name: str) -> str:
    """Limita entrada bruta antes de normalizar e identidade após strip."""
    if not isinstance(name, str) or len(name) > MAX_RAW_NAME:
        raise InvalidValueError("Invalid asset name input")
    value = name.strip()
    if not 1 <= len(value) <= MAX_ASSET_NAME:
        raise InvalidValueError("Asset name must contain 1 to 120 characters")
    return value


@dataclass(frozen=True)
class Asset:
    """Ativo imutável com nome, ticker normalizado e tipo stock/fii.

    Remove espaços das pontas; ticker tem 1–20 letras ASCII/dígitos em
    maiúsculas e nome tem 1–120 caracteres. Entrada bruta também é limitada.
    Não consulta a B3 nem certifica existência real em provedor de mercado.
    """

    name: str
    ticker: str
    asset_type: AssetType

    def __post_init__(self) -> None:
        """Valida os campos e normaliza a identidade antes de torná-la disponível."""
        if not isinstance(self.asset_type, AssetType):
            raise InvalidValueError("Tipo de ativo deve ser stock ou fii")
        object.__setattr__(self, "name", normalize_asset_name(self.name))
        object.__setattr__(self, "ticker", normalize_ticker(self.ticker))
