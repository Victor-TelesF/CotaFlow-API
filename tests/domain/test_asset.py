from dataclasses import FrozenInstanceError

import pytest

from domain.assets import Asset
from domain.enums import AssetType
from domain.exceptions import InvalidValueError


@pytest.mark.parametrize("asset_type", [AssetType.STOCK, AssetType.FII])
def test_asset_normalizes_catalog_identity(asset_type):
    asset = Asset("  Example  ", " test3 ", asset_type)
    assert (asset.name, asset.ticker, asset.asset_type) == (
        "Example",
        "TEST3",
        asset_type,
    )


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", ""),
        ("name", "  "),
        ("name", 123),
        ("ticker", ""),
        ("ticker", "  "),
        ("ticker", None),
        ("asset_type", "stock"),
        ("asset_type", "crypto"),
    ],
)
def test_asset_rejects_invalid_catalog_data(field, value):
    values = {"name": "Example", "ticker": "TEST3", "asset_type": AssetType.STOCK}
    values[field] = value
    with pytest.raises(InvalidValueError):
        Asset(**values)


def test_catalog_identity_cannot_change_after_an_operation(petr4):
    with pytest.raises(FrozenInstanceError):
        petr4.ticker = "OTHER3"
    assert petr4.ticker == "PETR4"


@pytest.mark.parametrize(
    "ticker",
    ["TEST3/OTHER3", "A" * 21, "A" * 65, "TEST3?x", "á3", "A B", "\t" * 65 + "A"],
)
def test_ticker_rejects_unsafe_or_unbounded_input(ticker):
    with pytest.raises(InvalidValueError):
        Asset("Synthetic", ticker, AssetType.STOCK)


@pytest.mark.parametrize("name", ["a" * 121, " " * 256 + "A", "\t\n"])
def test_asset_name_limits_raw_and_normalized_input(name):
    with pytest.raises(InvalidValueError):
        Asset(name, "TEST3", AssetType.STOCK)


def test_identity_accepts_exact_normalized_and_raw_limits():
    asset = Asset(" " * 136 + "a" * 120, " " * 44 + "a" * 20, AssetType.STOCK)
    assert asset.name == "a" * 120
    assert asset.ticker == "A" * 20
