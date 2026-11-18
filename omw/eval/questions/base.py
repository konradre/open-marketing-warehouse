"""What a question is: the words, the shape of a right answer, where the ground truth comes from, and the
plans the scripted planners follow in each arm.

A question never carries a stored answer. ``truth`` computes it from the seeded ``World`` in plain Python,
independently of ClickHouse, of the ``marts`` views and of both tool surfaces under test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from omw.eval.world import Period, World

Shape = Literal["scalar", "table", "label"]
"""``scalar``: one number. ``table``: label -> number rows. ``label``: one name (a campaign, a page)."""
Kind = Literal["answer", "refuse", "clarify"]
Truth = Callable[[World], Any]

BLANK = "(none)"
"""How an empty label (revenue with no campaign key, say) is written in answers and in the truth."""


@dataclass(frozen=True)
class MetricStep:
    """One ``query_metrics`` call and how to read its rows."""

    metrics: tuple[str, ...]
    time: Period | str | None
    """A period, a named range, or ``None`` for a snapshot measure (a campaign list has no dates)."""
    dimensions: tuple[str, ...] = ()
    filters: Mapping[str, Any] = field(default_factory=dict)
    compare: str | None = None
    label: str | None = None
    """The column that names a row, for ``table`` and ``label`` answers (``period`` with ``compare``)."""
    value: str | None = None
    """The column holding the number; the first metric when unset."""
    add: tuple[str, ...] = ()
    """Sum these columns instead (three platforms' clicks)."""
    divide_by: str | None = None
    """Divide the value by this column: a ratio of two additive totals the catalog has no measure for."""
    scale: float = 1.0
    only: str | None = None
    """Keep the one row with this label and answer with its number."""
    alias: str | None = None
    """Name the single number this step yields, when several steps build one table."""
    limit: int = 1000

    def request(self) -> dict[str, Any]:
        request: dict[str, Any] = {"metrics": list(self.metrics)}
        if self.time is not None:
            request["time_range"] = self.time.iso() if isinstance(self.time, Period) else self.time
        if self.dimensions:
            request["dimensions"] = list(self.dimensions)
        if self.filters:
            request["filters"] = dict(self.filters)
        if self.compare:
            request["compare"] = self.compare
        if self.limit != 1000:
            request["limit"] = self.limit
        return request


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    text: str
    shape: Shape
    unit: str
    """What the number is, for the answer format hint: ``USD``, ``count``, ``percent``, ..."""
    expect: Kind = "answer"
    accept: tuple[Kind, ...] = ()
    """Every acceptable behaviour; just ``expect`` when empty. A trap that may be answered by splitting
    the ambiguity (per currency) lists ``answer`` too and carries a ``truth``."""
    truth: Truth | None = None
    label_name: str = ""
    """What a row label is (``campaign name``), for the format hint."""
    top: int | None = None
    """The answer is the N largest rows, in order."""
    caveat_terms: tuple[str, ...] = ()
    """When set, a right answer must also carry a caveat mentioning one of these (case-insensitive)."""
    sql: str | None = None
    """Reference SQL for the raw SQL arm. It returns the answer itself: one cell, or label and value rows."""
    sql_caveat: str = ""
    """The caveat a careful analyst attaches to the reference SQL answer."""
    metrics: tuple[MetricStep, ...] = ()
    """Reference ``query_metrics`` calls. Empty: the catalog cannot express the question."""
    combine: Literal["pct_change"] | None = None
    """Turn the ``current`` and ``previous`` steps into one percentage change."""
    naive_sql: str | None = None
    """The tempting query that takes the question at face value (literal planner, SQL arm)."""
    naive_metrics: tuple[MetricStep, ...] | None = None
    """The face-value ``query_metrics`` request (literal planner, metric arm)."""
    tags: tuple[str, ...] = ()
    why: str = ""
    """For a trap: why answering with a number is wrong."""

    @property
    def accepted(self) -> tuple[Kind, ...]:
        return self.accept or (self.expect,)

    @property
    def is_trap(self) -> bool:
        return self.expect != "answer"

    def hint(self) -> str:
        """The answer format, appended to the question. It describes the shape only, for traps too."""
        unit = f" ({self.unit})" if self.unit else ""
        if self.shape == "scalar":
            return f"Answer with a single number{unit}."
        if self.shape == "label":
            return f"Answer with a single {self.label_name or 'name'}."
        order = f" Give the top {self.top}, largest first." if self.top else ""
        return (
            f"Answer with one row per {self.label_name or 'label'}: the label and its number{unit}. "
            f"Write an empty label as {BLANK}.{order}"
        )

    def prompt(self) -> str:
        return f"{self.text}\n{self.hint()}"


@dataclass(frozen=True)
class Task:
    """What a planner is shown of a question: its id and its words, nothing else."""

    id: str
    prompt: str


def task_of(question: Question) -> Task:
    return Task(question.id, question.prompt())
