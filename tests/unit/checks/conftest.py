"""A fake warehouse: canned answers per SQL fragment, and a record of every write."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


class Result:
    def __init__(self, rows: list[list[Any]]) -> None:
        self.result_rows = rows


class FakeWarehouse:
    def __init__(self, objects: set[str] | None = None) -> None:
        self.objects = objects or set()
        self.answers: list[tuple[Callable[[str], bool], list[list[Any]]]] = []
        self.queries: list[str] = []
        self.commands: list[str] = []
        self.inserted: list[tuple[str, list[list[Any]], list[str]]] = []

    def when(self, fragment: str, rows: list[list[Any]]) -> None:
        self.answers.append((lambda sql: fragment in sql, rows))

    def query(self, sql: str) -> Result:
        self.queries.append(sql)
        if "FROM system.tables" in sql:
            return Result([list(name.split(".", 1)) for name in sorted(self.objects)])
        for matches, rows in self.answers:
            if matches(sql):
                return Result(rows)
        raise AssertionError(f"no canned answer for: {sql[:120]}")

    def command(self, sql: str) -> None:
        self.commands.append(sql)

    def insert(self, table: str, data: list[list[Any]], column_names: list[str]) -> None:
        self.inserted.append((table, data, column_names))


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()
