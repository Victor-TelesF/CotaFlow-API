from datetime import date

import pytest

from domain.assets import Asset
from domain.enums import AssetType


@pytest.fixture
def frozen_today():
    return date(2026, 6, 1)


@pytest.fixture(autouse=True)
def freeze_reference_date(monkeypatch, frozen_today):
    monkeypatch.setattr(
        "domain.portfolio.transaction.reference_date", lambda: frozen_today
    )


@pytest.fixture
def past_date():
    return date(2026, 1, 5)


@pytest.fixture
def future_date():
    return date(2026, 6, 2)


@pytest.fixture
def petr4():
    return Asset("Petrobras", "PETR4", AssetType.STOCK)


@pytest.fixture
def vale3():
    return Asset("Vale", "VALE3", AssetType.STOCK)
