"""dlt column hints shared by the sources: explicit types, no NULLs (the ClickHouse convention)."""

from __future__ import annotations

from dlt.common.data_types import TDataType
from dlt.common.schema.typing import TColumnSchema


def column(data_type: TDataType) -> TColumnSchema:
    return {"data_type": data_type, "nullable": False}
