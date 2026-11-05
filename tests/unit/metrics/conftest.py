from __future__ import annotations

from datetime import date

import pytest

from omw.metrics import Catalog, TimeRange, load_catalog

TODAY = date(2026, 10, 5)
OCTOBER = TimeRange(start=date(2026, 10, 1), end=date(2026, 10, 3))
OLD = TimeRange(start=date(2026, 1, 1), end=date(2026, 1, 31))


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return load_catalog()
