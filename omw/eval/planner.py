"""Planners: the part of the agent that decides what to ask its tools and what to say back.

The interface is small on purpose. A planner is shown a ``Task`` (the question's words) and a toolbox, and
returns an ``AgentAnswer``. The scripted planners here run offline and are deterministic; a model planner
(``omw.eval.model_planner``) plugs into the same seat.

``reference`` follows each question's reference plan: a careful analyst who knows the traps. It is the
harness's known-good run. ``literal`` takes every question at face value: the hurried analyst who sums the
rate column or joins two fact tables row by row. Its plans are written by hand, so its scores describe what
each *tool surface* does with a face-value request, not what a model would do. ``refuser`` declines
everything: the null baseline, which never gives a wrong number and never gives a right one either.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from omw.eval.questions.base import BLANK, MetricStep, Question, Task
from omw.eval.tools import InfraError, Toolbox, ToolError
from omw.eval.truth import change, label_of

AnswerKind = Literal["answer", "refuse", "clarify", "error"]


@dataclass
class AgentAnswer:
    kind: AnswerKind
    value: float | None = None
    label: str | None = None
    rows: list[tuple[str, float | None]] | None = None
    caveats: list[str] = field(default_factory=list)
    reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    """Token counts and the serving model, for model planners."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.value is not None:
            out["value"] = self.value
        if self.label is not None:
            out["label"] = self.label
        if self.rows is not None:
            out["rows"] = [list(row) for row in self.rows]
        if self.caveats:
            out["caveats"] = self.caveats
        if self.reason:
            out["reason"] = self.reason
        if self.usage:
            out["usage"] = self.usage
        return out


class Planner(Protocol):
    name: str

    def solve(self, task: Task, toolbox: Toolbox) -> AgentAnswer: ...


def number(cell: Any) -> float | None:
    """A result cell as a float: ClickHouse decimals arrive as text, NULL and NaN as no value."""
    if cell is None or isinstance(cell, bool):
        return None
    try:
        value = float(cell)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _shape_answer(
    question: Question, rows: list[tuple[str, float | None]], caveats: list[str]
) -> AgentAnswer:
    """Label and value rows as the answer the question's shape calls for."""
    if question.shape == "scalar":
        return AgentAnswer("answer", value=rows[0][1] if rows else None, caveats=caveats)
    if question.shape == "label":
        best = max(rows, key=lambda row: -math.inf if row[1] is None else row[1], default=None)
        return AgentAnswer("answer", label=best[0] if best else None, caveats=caveats)
    if question.top:
        rows = sorted(rows, key=lambda row: (-(row[1] or 0.0), row[0]))[: question.top]
    return AgentAnswer("answer", rows=rows, caveats=caveats)


def answer_sql(question: Question, toolbox: Toolbox, query: str, caveat: str = "") -> AgentAnswer:
    """Run one query whose result is the answer: a cell, or label and value rows in order."""
    try:
        result = toolbox.call("run_query", {"query": query})
    except InfraError:
        raise
    except ToolError as exc:
        return AgentAnswer("refuse", reason=f"the warehouse would not run it: {exc}")
    rows = result["rows"]
    caveats = [caveat] if caveat else []
    if question.shape == "label":
        return AgentAnswer("answer", label=label_of(rows[0][0]) if rows else None, caveats=caveats)
    if question.shape == "scalar":
        return AgentAnswer("answer", value=number(rows[0][0]) if rows else None, caveats=caveats)
    return AgentAnswer("answer", rows=[(label_of(row[0]), number(row[1])) for row in rows], caveats=caveats)


def _read(step: MetricStep, columns: list[str], row: list[Any]) -> float | None:
    cells = dict(zip(columns, row, strict=True))
    if step.add:
        parts = [number(cells[name]) for name in step.add]
        value: float | None = sum(part or 0.0 for part in parts)
    else:
        value = number(cells[step.value or step.metrics[0]])
    if value is not None and step.divide_by:
        bottom = number(cells[step.divide_by])
        value = value / bottom if bottom else None
    return None if value is None else value * step.scale


def answer_metrics(question: Question, toolbox: Toolbox, steps: tuple[MetricStep, ...]) -> AgentAnswer:
    """Run the ``query_metrics`` calls of a plan and read the rows the way the plan says."""
    rows: list[tuple[str, float | None]] = []
    caveats: list[str] = []
    for step in steps:
        try:
            answer = toolbox.call("query_metrics", step.request())
        except InfraError:
            raise
        except ToolError as exc:
            return AgentAnswer("refuse", reason=str(exc))
        if answer["mood"] in ("refuse", "clarify"):
            return AgentAnswer(answer["mood"], reason=answer["reason"])
        caveats += [c for c in answer["caveats"] if c not in caveats]
        columns = answer["columns"]
        found = [
            (label_of(row[columns.index(step.label)]) if step.label else "", _read(step, columns, row))
            for row in answer["rows"]
        ]
        if step.only is not None:
            found = [row for row in found if row[0] == step.only]
        if step.alias:
            found = [(step.alias, found[0][1] if found else None)]
        rows += found
    if question.combine == "pct_change":
        values = dict(rows)
        current, previous = values.get("current"), values.get("previous")
        if current is None or not previous:
            return AgentAnswer("answer", value=None, caveats=caveats)
        return AgentAnswer("answer", value=change(current, previous), caveats=caveats)
    return _shape_answer(question, rows, caveats)


@dataclass
class ScriptedPlanner:
    """Follows the plans written into the questions. ``mode`` picks which ones."""

    questions: dict[str, Question]
    mode: Literal["reference", "literal"] = "reference"
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = self.mode

    def solve(self, task: Task, toolbox: Toolbox) -> AgentAnswer:
        question = self.questions[task.id]
        literal = self.mode == "literal"
        if question.is_trap and not literal:
            return AgentAnswer(question.expect, reason=question.why)
        if toolbox.arm == "sql":
            query = (question.naive_sql if literal else None) or (None if question.is_trap else question.sql)
            if query is None:
                return AgentAnswer("refuse", reason="nothing in the schema answers this")
            caveat = "" if literal else question.sql_caveat
            return answer_sql(question, toolbox, query, caveat)
        steps = question.naive_metrics if literal and question.naive_metrics is not None else None
        if steps is None:
            steps = () if question.is_trap else question.metrics
        if not steps:
            return AgentAnswer("refuse", reason="no metric in the catalog expresses this")
        return answer_metrics(question, toolbox, steps)


@dataclass
class RefuserPlanner:
    """Declines every question. A null baseline: no wrong numbers, no answers, and only the traps that
    call for a refusal (not a question back) handled."""

    name: str = "refuser"

    def solve(self, task: Task, toolbox: Toolbox) -> AgentAnswer:
        return AgentAnswer("refuse", reason="declined")


def normalise_label(text: str) -> str:
    return (text.strip() or BLANK).casefold()
