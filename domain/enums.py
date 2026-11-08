"""Types supported by the initial portfolio scope."""

from enum import Enum


class AssetType(Enum):
    STOCK = "stock"
    FII = "fii"


class TransactionType(Enum):
    BUY = "buy"
    SELL = "sell"
