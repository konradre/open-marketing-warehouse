"""Scoring: what counts as right, what counts as a wrong number, and what never counts at all."""

from __future__ import annotations

import pytest

from omw.eval.planner import AgentAnswer
from omw.eval.questions.base import Question
from omw.eval.scoring import close, score, summarise, wilson
from omw.eval.truth import Expected

SCALAR = Question(id="q", category="spend", text="How much?", shape="scalar", unit="USD")
TABLE = Question(
    id="t", category="spend", text="By campaign?", shape="table", unit="USD", label_name="campaign"
)
NAME = Question(id="n", category="spend", text="Which?", shape="label", unit="")
TRAP = Question(id="x", category="trap", text="Reach?", shape="scalar", unit="", expect="refuse", why="no")
ASK = Question(
    id="a", category="trap", text="Clicks?", shape="scalar", unit="", expect="clarify", why="which"
)
EITHER = Question(
    id="e",
    category="trap",
    text="Total?",
    shape="scalar",
    unit="",
    expect="refuse",
    accept=("refuse", "clarify"),
)
CAVEAT = Question(
    id="c",
    category="spend",
    text="Last week?",
    shape="scalar",
    unit="USD",
    caveat_terms=("restat", "may change"),
)


def answer(**fields: object) -> AgentAnswer:
    return AgentAnswer("answer", **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("got", "want", "same"),
    [(100.0, 100.4, True), (100.0, 101.0, False), (0.01332, 0.01333, True), (0.0133, 0.0140, False),
     (None, None, True), (None, 1.0, False), (0.0, 0.0, True)],
)  # fmt: skip
def test_numbers_match_within_half_a_percent(got: float | None, want: float | None, same: bool) -> None:
    assert close(got, want) is same


def test_a_right_number_is_correct_and_a_wrong_one_is_a_wrong_number() -> None:
    assert score(SCALAR, Expected(value=250.0), answer(value=250.01)).outcome == "correct"
    wrong = score(SCALAR, Expected(value=250.0), answer(value=300.0))
    assert wrong.outcome == "wrong_number" and "300.0" in wrong.detail


def test_names_ignore_case_and_surrounding_space() -> None:
    assert (
        score(NAME, Expected(label="Gadgets Launch"), answer(label=" gadgets launch ")).outcome == "correct"
    )
    assert score(NAME, Expected(label="Gadgets Launch"), answer(label="Brand")).outcome == "wrong_number"
    assert score(NAME, Expected(label="Gadgets Launch"), answer(value=3.0)).outcome == "wrong_number"


def test_rows_match_as_a_mapping_with_zero_and_empty_rows_free() -> None:
    want = Expected(rows=(("a", 10.0), ("b", 0.0), ("(none)", 5.0)))
    assert score(TABLE, want, answer(rows=[("A", 10.0), ("", 5.0)])).outcome == "correct"
    assert (
        score(TABLE, want, answer(rows=[("a", 10.0), ("(none)", 5.0), ("z", None), ("y", 0.0)])).outcome
        == "correct"
    )
    assert "missing row" in score(TABLE, want, answer(rows=[("a", 10.0)])).detail
    assert (
        "unexpected rows"
        in score(TABLE, want, answer(rows=[("a", 10.0), ("(none)", 5.0), ("z", 3.0)])).detail
    )
    assert "twice" in score(TABLE, want, answer(rows=[("a", 10.0), ("a", 10.0)])).detail
    assert score(TABLE, want, answer(rows=[("a", 11.0), ("(none)", 5.0)])).outcome == "wrong_number"
    assert score(TABLE, want, answer(value=15.0)).outcome == "wrong_number"


def test_an_undefined_value_matches_only_no_value() -> None:
    want = Expected(rows=(("a", 2.0), ("b", None)))
    assert score(TABLE, want, answer(rows=[("a", 2.0), ("b", None)])).outcome == "correct"
    assert score(TABLE, want, answer(rows=[("a", 2.0)])).outcome == "correct"
    assert score(TABLE, want, answer(rows=[("a", 2.0), ("b", 7.0)])).outcome == "wrong_number"


def test_ranked_rows_must_come_in_order() -> None:
    want = Expected(rows=(("a", 9.0), ("b", 5.0)), ordered=True)
    assert score(TABLE, want, answer(rows=[("a", 9.0), ("b", 5.0)])).outcome == "correct"
    assert "order" in score(TABLE, want, answer(rows=[("b", 5.0), ("a", 9.0)])).detail


def test_a_trap_answered_with_a_number_is_a_wrong_number_whatever_the_number() -> None:
    assert score(TRAP, None, answer(value=1.0)).outcome == "wrong_number"
    assert score(TRAP, None, AgentAnswer("refuse", reason="not additive")).outcome == "correct"
    assert score(ASK, None, AgentAnswer("clarify", reason="which platform")).outcome == "correct"


def test_declining_the_wrong_way_is_not_correct_and_not_a_wrong_number() -> None:
    assert score(ASK, None, AgentAnswer("refuse")).outcome == "wrong_mood"
    assert score(TRAP, None, AgentAnswer("clarify")).outcome == "wrong_mood"
    assert score(EITHER, None, AgentAnswer("clarify")).outcome == "correct"


def test_declining_an_answerable_question_is_an_over_refusal() -> None:
    result = score(SCALAR, Expected(value=1.0), AgentAnswer("refuse", reason="no metric"))
    assert result.outcome == "over_refusal" and "no metric" in result.detail
    assert score(SCALAR, Expected(value=1.0), AgentAnswer("clarify")).outcome == "over_refusal"


def test_no_value_is_not_a_wrong_number_and_a_failure_is_not_an_answer() -> None:
    assert score(SCALAR, Expected(value=1.0), answer()).outcome == "no_answer"
    assert score(SCALAR, Expected(value=1.0), AgentAnswer("error", reason="timeout")).outcome == "error"
    assert score(TRAP, None, AgentAnswer("error")).outcome == "error"


def test_a_caveat_is_tracked_beside_the_number_not_instead_of_it() -> None:
    want = Expected(value=10.0)
    carried = score(CAVEAT, want, answer(value=10.0, caveats=["Meta RESTATES for 28 days"]))
    bare = score(CAVEAT, want, answer(value=10.0, caveats=["mixes currencies"]))
    assert carried.outcome == bare.outcome == "correct"
    assert carried.disclosed is True and bare.disclosed is False
    assert score(SCALAR, want, answer(value=10.0)).disclosed is None
    assert score(CAVEAT, want, answer(value=11.0)).disclosed is None


def test_the_summary_keeps_errors_out_of_every_rate() -> None:
    rows = [
        (False, "correct", None), (False, "correct", True), (False, "wrong_number", None),
        (False, "over_refusal", None), (False, "error", None), (False, "correct", False),
        (True, "correct", None), (True, "wrong_number", None), (True, "wrong_mood", None),
        (True, "error", None),
    ]  # fmt: skip
    summary = summarise(rows)
    assert (summary.questions, summary.answerable, summary.traps, summary.errors) == (10, 5, 3, 2)
    assert summary.accuracy == 3 / 5 and summary.trap_correctness == 1 / 3
    assert summary.wrong_number_rate == 2 / 8 and summary.over_refusal_rate == 1 / 5
    assert (summary.need_caveat, summary.disclosed) == (2, 1)
    assert summary.to_dict()["wrong_numbers"] == 2
    assert summarise([]).accuracy == 0.0


def test_the_interval_narrows_with_more_questions() -> None:
    low, high = wilson(9, 10)
    wide = high - low
    low, high = wilson(90, 100)
    assert high - low < wide and low < 0.9 < high
    assert wilson(0, 0) == (0.0, 0.0) and wilson(10, 10)[1] == 1.0
