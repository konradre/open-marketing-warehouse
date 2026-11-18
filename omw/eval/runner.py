"""Run every question through a planner in each arm and keep everything: the answer, the score, the
ground truth and every tool call behind it."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from omw.eval.planner import AgentAnswer, Planner
from omw.eval.questions.base import Question, task_of
from omw.eval.scoring import Score, Summary, score, summarise
from omw.eval.tools import Toolbox
from omw.eval.truth import Expected, expected
from omw.eval.world import SEED, TODAY, World

log = logging.getLogger(__name__)


@dataclass
class Row:
    question: Question
    arm: str
    rep: int
    answer: AgentAnswer
    want: Expected | None
    score: Score
    calls: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.question.id,
            "arm": self.arm,
            "rep": self.rep,
            "category": self.question.category,
            "tags": list(self.question.tags),
            "expect": self.question.expect,
            "outcome": self.score.outcome,
            "detail": self.score.detail,
            "disclosed": self.score.disclosed,
            "answer": self.answer.to_dict(),
            "expected": self.want.to_dict() if self.want else None,
            "calls": self.calls,
        }


@dataclass
class Run:
    planner: str
    rows: list[Row] = field(default_factory=list)
    model: str = ""
    started: str = ""

    def of_arm(self, arm: str) -> list[Row]:
        return [row for row in self.rows if row.arm == arm]

    def summary(self, arm: str, category: str | None = None) -> Summary:
        rows = [r for r in self.of_arm(arm) if category is None or r.question.category == category]
        return summarise((row.question.is_trap, row.score.outcome, row.score.disclosed) for row in rows)

    @property
    def arms(self) -> list[str]:
        return list(dict.fromkeys(row.arm for row in self.rows))

    @property
    def categories(self) -> list[str]:
        return list(dict.fromkeys(row.question.category for row in self.rows))

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner": self.planner,
            "model": self.model,
            "seed": SEED,
            "today": TODAY.isoformat(),
            "started": self.started,
            "summary": {arm: self.summary(arm).to_dict() for arm in self.arms},
            "rows": [row.to_dict() for row in self.rows],
        }


def run_eval(
    planner: Planner,
    toolboxes: Sequence[Toolbox],
    questions: Sequence[Question],
    world: World,
    reps: int = 1,
    model: str = "",
    stamp: bool = True,
) -> Run:
    """``reps`` repeats each question, for planners that are not deterministic."""
    started = datetime.now(tz=UTC).isoformat(timespec="seconds") if stamp else ""
    run = Run(planner=planner.name, model=model, started=started)
    truths = {question.id: expected(question, world) for question in questions}
    for toolbox in toolboxes:
        for question in questions:
            for rep in range(reps):
                toolbox.calls.clear()
                try:
                    answer = planner.solve(task_of(question), toolbox)
                except Exception as exc:  # noqa: BLE001 - a planner failure is an error row, not a crash
                    log.warning("%s/%s: planner failed: %r", toolbox.arm, question.id, exc)
                    answer = AgentAnswer("error", reason=f"{type(exc).__name__}: {exc}"[:300])
                result = score(question, truths[question.id], answer)
                calls = [call.to_dict() for call in toolbox.calls]
                run.rows.append(Row(question, toolbox.arm, rep, answer, truths[question.id], result, calls))
    return run
