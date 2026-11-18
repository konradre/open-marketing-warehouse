from __future__ import annotations

import pytest

from tests.unit.checks.conftest import FakeWarehouse


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()
