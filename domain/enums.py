"""Types supported by the initial portfolio scope."""

from enum import Enum


class AssetType(Enum):
    """Tipos do catálogo inicial: ação brasileira ou fundo imobiliário."""

    STOCK = "stock"
    FII = "fii"


class TransactionType(Enum):
    """Tipos de operação que alteram a quantidade: compra ou venda."""

    BUY = "buy"
    SELL = "sell"
