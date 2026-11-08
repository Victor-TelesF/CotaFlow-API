"""Catalog identity; operation and market prices are separate inputs."""

from dataclasses import dataclass

from ..enums import AssetType
from ..exceptions import InvalidValueError


@dataclass(frozen=True)
class Asset:
    name: str
    ticker: str
    asset_type: AssetType

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidValueError("Nome deve ser uma string não vazia")
        if not isinstance(self.ticker, str) or not self.ticker.strip():
            raise InvalidValueError("Ticker deve ser uma string não vazia")
        if not isinstance(self.asset_type, AssetType):
            raise InvalidValueError("Tipo de ativo deve ser stock ou fii")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "ticker", self.ticker.strip().upper())
