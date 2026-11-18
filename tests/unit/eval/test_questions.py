"""The question set: its size and coverage, a ground truth for everything answerable, plans the metric
compiler accepts, and the published copy in ``eval/questions.json`` still matching the seed."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from omw.eval.questions.base import Question, task_of
from omw.eval.report import QUESTIONS_FILE, questions_payload
from omw.eval.truth import expected, largest
from omw.eval.world import TODAY, World
from omw.metrics import Mood, load_catalog
from omw.metrics.guard import assert_single_select
from omw.metrics.request import MetricRequest
from omw.metrics.runner import plan_request

CATEGORIES = {"spend", "rates", "search", "ga4", "revenue", "pipeline", "cross_source", "compare", "trap"}
CANNOT_ANSWER = {"not_in_catalog", "grain_refused"}
"""Tags of answerable questions the metric arm is known not to answer (reported, not hidden)."""


def test_about_a_hundred_questions_in_every_category(questions: list[Question]) -> None:
    counts = Counter(question.category for question in questions)
    assert set(counts) == CATEGORIES and 100 <= len(questions) <= 140
    assert all(count >= 8 for count in counts.values()), counts
    assert len({question.id for question in questions}) == len(questions)
    traps = [question for question in questions if question.is_trap]
    assert {question.expect for question in traps} == {"refuse", "clarify"} and len(traps) >= 20


def test_every_answerable_question_has_a_truth_and_a_reference_query(
    questions: list[Question], world: World
) -> None:
    for question in questions:
        if question.is_trap:
            assert question.why and question.sql is None and not question.metrics, question.id
            assert question.truth is None, question.id
            continue
        want = expected(question, world)
        assert want is not None and question.sql, question.id
        assert_single_select(question.sql)
        if want.rows is not None:
            assert want.rows and all(label for label, _ in want.rows), question.id
        elif want.label is None:
            assert want.value is not None and want.value == want.value, question.id


def test_the_hint_gives_the_shape_and_never_the_verdict(questions: list[Question]) -> None:
    for question in questions:
        task = task_of(question)
        assert task.id == question.id and task.prompt.startswith(question.text)
        assert "Answer with" in task.prompt
        assert question.why == "" or question.why not in task.prompt
        assert "refuse" not in question.hint() and "clarify" not in question.hint()


def test_top_n_answers_have_no_tie_at_the_cut(questions: list[Question], world: World) -> None:
    ranked = [question for question in questions if question.top]
    assert len(ranked) >= 4
    for question in ranked:
        assert question.truth is not None and question.top is not None
        values = [value for _, value in largest(question.truth(world), question.top + 1)]
        assert len(set(values)) == len(values), question.id


def test_name_answers_have_a_clear_winner(questions: list[Question], world: World) -> None:
    for question in questions:
        if question.shape != "label" or question.truth is None:
            continue
        want = expected(question, world)
        assert want is not None and want.label


def test_reference_metric_plans_compile_unless_the_gap_is_declared(questions: list[Question]) -> None:
    catalog = load_catalog()
    for question in questions:
        if question.is_trap:
            continue
        if not question.metrics:
            assert "not_in_catalog" in question.tags, question.id
            continue
        for step in question.metrics:
            planned = plan_request(catalog, MetricRequest(**step.request()), TODAY)
            refused = getattr(planned, "mood", None) in (Mood.REFUSE, Mood.CLARIFY)
            assert refused == ("grain_refused" in question.tags), (
                question.id,
                getattr(planned, "reason", ""),
            )


def test_face_value_metric_requests_are_valid_requests(questions: list[Question]) -> None:
    for question in questions:
        for step in question.naive_metrics or ():
            MetricRequest(**step.request())  # the layer, not pydantic, decides what happens to them


def test_caveat_questions_and_declared_gaps_exist(questions: list[Question]) -> None:
    assert sum(bool(question.caveat_terms) for question in questions) >= 5
    gaps = [question for question in questions if CANNOT_ANSWER & set(question.tags)]
    assert 5 <= len(gaps) <= 15  # enough to be honest about coverage, not enough to drown the comparison
    assert sum("pitfall" in question.tags for question in questions) >= 15


def test_the_published_questions_and_ground_truth_match_the_seed(
    questions: list[Question], world: World
) -> None:
    published = json.loads(QUESTIONS_FILE.read_text())
    fresh = json.loads(json.dumps(questions_payload(questions, world)))
    assert published["count"] == len(questions)
    assert published == fresh, "run `uv run omw eval questions` (or `omw eval report`) and commit the result"


@pytest.mark.parametrize("question_id", ["meta_spend_sep", "roas_usd_sep", "top_queries_sep"])
def test_ground_truth_is_pinned_for_a_few_questions(
    questions: list[Question], world: World, question_id: str
) -> None:
    """A change to the generator that moves the numbers must be a deliberate one."""
    published = {q["id"]: q["ground_truth"] for q in json.loads(QUESTIONS_FILE.read_text())["questions"]}
    question = next(q for q in questions if q.id == question_id)
    want = expected(question, world)
    assert want is not None and want.to_dict() == published[question_id]
