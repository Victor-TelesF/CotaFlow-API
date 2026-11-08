"""Identidade de uma ação brasileira ou FII, independente de banco e HTTP.

Preço da operação pertence à transação; preço atual é uma entrada da avaliação.
O cadastro de ativo não armazena nenhum desses preços ou uma posição do usuário.
"""

from dataclasses import dataclass

from ..enums import AssetType
from ..exceptions import InvalidValueError


def normalize_ticker(ticker: str) -> str:
    """Normaliza a identidade local sem certificar sua existência na bolsa."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise InvalidValueError("Ticker deve ser uma string não vazia")
    return ticker.strip().upper()


@dataclass(frozen=True)
class Asset:
    """Ativo imutável com nome, ticker normalizado e tipo stock/fii.

    Remove espaços das pontas e converte ticker para maiúsculas. Exige strings
    não vazias e um AssetType, mas não consulta a B3 ou valida a existência
    real do símbolo em um provedor de mercado.
    """

    name: str
    ticker: str
    asset_type: AssetType

    def __post_init__(self) -> None:
        """Valida os campos e normaliza a identidade antes de torná-la disponível."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidValueError("Nome deve ser uma string não vazia")
        if not isinstance(self.asset_type, AssetType):
            raise InvalidValueError("Tipo de ativo deve ser stock ou fii")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "ticker", normalize_ticker(self.ticker))
