"""Shared pieces for the evaluation harness tests: the seeded world and a toolbox that needs no warehouse."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from omw.eval.generate import build_world
from omw.eval.questions import all_questions
from omw.eval.questions.base import Question
from omw.eval.tools import ToolCall, ToolError, ToolSpec
from omw.eval.world import World


@pytest.fixture(scope="session")
def world() -> World:
    return build_world()


@pytest.fixture(scope="session")
def questions() -> list[Question]:
    return all_questions()


@dataclass
class FakeToolbox:
    """Answers each call from a queue: a result to return, or an exception to raise."""

    arm: str
    replies: list[Any] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)

    def context(self) -> str:
        return f"context of the {self.arm} arm"

    def specs(self) -> list[ToolSpec]:
        name = "run_query" if self.arm == "sql" else "query_metrics"
        return [ToolSpec(name, "a tool", {"type": "object", "properties": {}})]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        record = ToolCall(name, arguments)
        self.calls.append(record)
        reply = self.replies.pop(0)
        if isinstance(reply, ToolError):
            record.error = str(reply)
            raise reply
        record.result = reply
        return reply


def metric_answer(
    columns: list[str], rows: list[list[Any]], mood: str = "serve", **more: Any
) -> dict[str, Any]:
    """A ``query_metrics`` answer as the server returns it."""
    return {
        "mood": mood,
        "reason": "",
        "caveats": [],
        "candidates": [],
        "columns": columns,
        "rows": rows,
    } | more
