"""Score one answer against a question's ground truth, and add the scores up.

Six outcomes, kept apart so that plumbing never reads as a wrong answer and a refusal never reads as a
number:

- ``correct``: the right number (or name, or rows), or the right refusal or clarification on a trap.
- ``wrong_number``: a number was given and it is wrong, or a trap was answered with a number at all.
- ``over_refusal``: an answerable question was refused or sent back for clarification.
- ``wrong_mood``: a trap was declined, but not in an accepted way (refused where it should have asked).
- ``no_answer``: the agent claimed an answer and gave no value.
- ``error``: no scorable output (a planner or API failure). Counted, never averaged in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from omw.eval.planner import AgentAnswer, normalise_label
from omw.eval.questions.base import Question
from omw.eval.truth import Expected

Outcome = Literal["correct", "wrong_number", "over_refusal", "wrong_mood", "no_answer", "error"]
REL_TOLERANCE = 5e-3
"""Half a percent: wide enough for cents and float noise, far narrower than any trap's error."""


@dataclass(frozen=True)
class Score:
    outcome: Outcome
    detail: str = ""
    disclosed: bool | None = None
    """For a question that needs a caveat: whether the answer carried one. ``None`` otherwise."""


def close(got: float | None, want: float | None) -> bool:
    if got is None or want is None:
        return got is None and want is None
    return math.isclose(got, want, rel_tol=REL_TOLERANCE, abs_tol=1e-9)


def _rows_match(answer: list[tuple[str, float | None]], want: Expected) -> str:
    """Empty when the rows match, else what differs. A row the answer adds with no value or zero, or leaves
    out when the truth is zero or undefined, is not a difference."""
    assert want.rows is not None
    got: dict[str, float | None] = {}
    for label, value in answer:
        key = normalise_label(label)
        if key in got:
            return f"label {label!r} appears twice"
        got[key] = value
    wanted = {normalise_label(label): value for label, value in want.rows}
    for key, value in wanted.items():
        if key not in got:
            if value:
                return f"missing row {key!r}"
            continue
        if not close(got[key], value) and not (not value and not got[key]):
            return f"{key!r}: got {got[key]}, want {value}"
    extra = [key for key, value in got.items() if key not in wanted and value]
    if extra:
        return f"unexpected rows {sorted(extra)[:3]}"
    if want.ordered:
        order = [normalise_label(label) for label, _ in answer if normalise_label(label) in wanted]
        if order != [key for key in wanted if key in got]:
            return "rows are in the wrong order"
    return ""


def _mismatch(answer: AgentAnswer, want: Expected) -> str:
    if want.rows is not None:
        if answer.rows is None:
            return "expected rows"
        return _rows_match(answer.rows, want)
    if want.label is not None:
        if answer.label is None:
            return "expected a name"
        same = normalise_label(answer.label) == normalise_label(want.label)
        return "" if same else f"got {answer.label!r}, want {want.label!r}"
    return "" if close(answer.value, want.value) else f"got {answer.value}, want {want.value}"


def _empty(answer: AgentAnswer) -> bool:
    return answer.value is None and answer.label is None and not answer.rows


def score(question: Question, want: Expected | None, answer: AgentAnswer) -> Score:
    if answer.kind == "error":
        return Score("error", answer.reason)
    if answer.kind != "answer":
        if answer.kind in question.accepted:
            return Score("correct")
        outcome: Outcome = "wrong_mood" if question.is_trap else "over_refusal"
        return Score(outcome, f"{answer.kind}: {answer.reason}"[:300])
    if "answer" not in question.accepted or want is None:
        return Score("wrong_number", "answered a question that has no single right number")
    if _empty(answer) and not (want.value is None and want.rows is None and want.label is None):
        return Score("no_answer", "an answer with no value")
    difference = _mismatch(answer, want)
    if difference:
        return Score("wrong_number", difference)
    disclosed = None
    if question.caveat_terms:
        text = " ".join(answer.caveats).casefold()
        disclosed = any(term.casefold() in text for term in question.caveat_terms)
    return Score("correct", disclosed=disclosed)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson interval of a proportion: how much a rate over ``n`` questions can be trusted."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Summary:
    """The headline numbers of one planner in one arm. Rates leave ``error`` rows out."""

    questions: int
    answerable: int
    traps: int
    errors: int
    correct_answers: int
    correct_traps: int
    wrong_numbers: int
    over_refusals: int
    need_caveat: int
    disclosed: int

    @property
    def scored(self) -> int:
        return self.answerable + self.traps

    @property
    def accuracy(self) -> float:
        return self.correct_answers / self.answerable if self.answerable else 0.0

    @property
    def trap_correctness(self) -> float:
        return self.correct_traps / self.traps if self.traps else 0.0

    @property
    def wrong_number_rate(self) -> float:
        return self.wrong_numbers / self.scored if self.scored else 0.0

    @property
    def over_refusal_rate(self) -> float:
        return self.over_refusals / self.answerable if self.answerable else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "questions": self.questions,
            "answerable": self.answerable,
            "traps": self.traps,
            "errors": self.errors,
            "correct_answers": self.correct_answers,
            "correct_traps": self.correct_traps,
            "wrong_numbers": self.wrong_numbers,
            "over_refusals": self.over_refusals,
            "need_caveat": self.need_caveat,
            "disclosed": self.disclosed,
            "accuracy": round(self.accuracy, 4),
            "trap_correctness": round(self.trap_correctness, 4),
            "wrong_number_rate": round(self.wrong_number_rate, 4),
            "over_refusal_rate": round(self.over_refusal_rate, 4),
        }


def summarise(rows: Iterable[tuple[bool, str, bool | None]]) -> Summary:
    """Add up (is a trap, outcome, caveat carried) rows. ``error`` rows are counted and left out of every
    rate."""
    everything = list(rows)
    kept = [row for row in everything if row[1] != "error"]
    answerable = [row for row in kept if not row[0]]
    traps = [row for row in kept if row[0]]
    caveats = [row[2] for row in kept if row[2] is not None]
    return Summary(
        questions=len(everything),
        answerable=len(answerable),
        traps=len(traps),
        errors=len(everything) - len(kept),
        correct_answers=sum(row[1] == "correct" for row in answerable),
        correct_traps=sum(row[1] == "correct" for row in traps),
        wrong_numbers=sum(row[1] == "wrong_number" for row in kept),
        over_refusals=sum(row[1] == "over_refusal" for row in answerable),
        need_caveat=len(caveats),
        disclosed=sum(caveats),
    )
