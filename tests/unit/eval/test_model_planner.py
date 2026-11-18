"""The optional model planner, driven through the real Anthropic SDK against a canned HTTP transport: the
request it sends, the tool loop, the structured answer, and the outcomes that must be errors, not answers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from omw.eval.questions.base import Task
from omw.eval.tools import InfraError, ToolError
from tests.unit.eval.conftest import FakeToolbox

anthropic = pytest.importorskip("anthropic", reason="the optional eval group: uv sync --group eval")
httpx2 = pytest.importorskip("httpx2")

from omw.eval.model_planner import DEFAULT_MODEL, AnthropicPlanner, parse_submission  # noqa: E402

TASK = Task(
    "meta_spend_sep",
    "How much did we spend on Meta Ads in September 2026?\nAnswer with a single number (USD).",
)
USAGE = {
    "input_tokens": 1200,
    "output_tokens": 40,
    "cache_read_input_tokens": 900,
    "cache_creation_input_tokens": 0,
}


def message(
    content: list[dict[str, Any]], stop: str = "tool_use", model: str = DEFAULT_MODEL
) -> dict[str, Any]:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": stop, "stop_sequence": None, "usage": USAGE,
    }  # fmt: skip


def tool_use(name: str, payload: dict[str, Any], call_id: str = "toolu_1") -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": payload}


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-1"}


def planner(replies: list[dict[str, Any]], **options: Any) -> tuple[AnthropicPlanner, list[dict[str, Any]]]:
    """A planner on a real SDK client whose HTTP layer answers from ``replies``; also the request bodies."""
    sent: list[dict[str, Any]] = []
    queue = list(replies)

    def handler(request: Any) -> Any:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=queue.pop(0))

    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    client = anthropic.Anthropic(api_key="test-key", http_client=http, max_retries=0)
    return AnthropicPlanner(client=client, **options), sent


def test_it_runs_the_tool_loop_and_returns_the_structured_answer() -> None:
    submit = {"kind": "answer", "value": 28946.52, "caveats": ["inside Meta's 28-day restatement window"]}
    model, sent = planner(
        [
            message(
                [
                    THINKING,
                    tool_use("run_query", {"query": "SELECT sum(spend) FROM marts.meta_ads_campaigns"}),
                ]
            ),
            message([tool_use("submit_answer", submit, "toolu_2")]),
        ]
    )
    box = FakeToolbox("sql", [{"columns": ["s"], "rows": [["28946.520000000"]]}])
    answer = model.solve(TASK, box)
    assert answer.kind == "answer" and answer.value == 28946.52 and "restatement" in answer.caveats[0]
    assert answer.usage == {
        "model": DEFAULT_MODEL, "turns": 2, "input_tokens": 2400, "output_tokens": 80,
        "cache_read_input_tokens": 1800,
    }  # fmt: skip
    assert [call.name for call in box.calls] == ["run_query"] and model.name == f"model:{DEFAULT_MODEL}"

    first, second = sent
    assert first["model"] == DEFAULT_MODEL and first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "medium"} and "tool_choice" not in first
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert (
        "context of the sql arm" in first["system"][0]["text"] and "2026-10-01" in first["system"][0]["text"]
    )
    assert [tool["name"] for tool in first["tools"]] == ["run_query", "submit_answer"]
    assert first["messages"] == [{"role": "user", "content": TASK.prompt}]
    # The model's turn goes back whole (thinking block included), followed by the tool result.
    assert second["messages"][1]["role"] == "assistant"
    assert [block["type"] for block in second["messages"][1]["content"]] == ["thinking", "tool_use"]
    assert second["messages"][1]["content"][0]["signature"] == "sig-1"
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"]) == {"columns": ["s"], "rows": [["28946.520000000"]]}


def test_the_model_never_sees_a_plan_or_the_truth() -> None:
    model, sent = planner(
        [message([tool_use("submit_answer", {"kind": "clarify", "reason": "which platform?"})])]
    )
    answer = model.solve(TASK, FakeToolbox("metrics"))
    assert answer.kind == "clarify" and answer.reason == "which platform?"
    body = json.dumps(sent[0])
    assert "naive" not in body and "ground_truth" not in body and "SELECT sum" not in body
    assert "context of the metrics arm" in body and "query_metrics" in body


def test_a_failed_tool_call_is_shown_to_the_model_and_a_dead_warehouse_stops_the_question() -> None:
    model, sent = planner(
        [
            message([tool_use("run_query", {"query": "SELECT nope"})]),
            message([tool_use("submit_answer", {"kind": "refuse", "reason": "no such column"}, "toolu_2")]),
        ]
    )
    answer = model.solve(TASK, FakeToolbox("sql", [ToolError("UNKNOWN_IDENTIFIER nope")]))
    assert answer.kind == "refuse"
    result = sent[1]["messages"][2]["content"][0]
    assert result["is_error"] is True and "UNKNOWN_IDENTIFIER" in result["content"]

    model, _ = planner([message([tool_use("run_query", {"query": "SELECT 1"})])])
    with pytest.raises(InfraError):
        model.solve(TASK, FakeToolbox("sql", [InfraError("QUOTA_EXCEEDED")]))


def test_long_tool_results_are_cut_with_a_notice() -> None:
    model, sent = planner(
        [
            message([tool_use("run_query", {"query": "SELECT 1"})]),
            message([tool_use("submit_answer", {"kind": "answer", "value": 1}, "toolu_2")]),
        ]
    )
    model.solve(TASK, FakeToolbox("sql", [{"columns": ["t"], "rows": [["x" * 50_000]]}]))
    content = sent[1]["messages"][2]["content"][0]["content"]
    assert len(content) < 31_000 and "narrow the query" in content


@pytest.mark.parametrize(
    ("replies", "reason"),
    [
        (
            [message([tool_use("submit_answer", {"kind": "answer", "value": 1})], model="claude-opus-4-8")],
            "served by",
        ),
        ([message([{"type": "text", "text": ""}], stop="refusal")], "refusal"),
        ([message([{"type": "text", "text": "The answer is"}], stop="max_tokens")], "max_tokens"),
        (
            [message([{"type": "text", "text": "About 29k."}], stop="end_turn")] * 2,
            "without calling submit_answer",
        ),
        ([message([tool_use("submit_answer", {"kind": "maybe"})])], "kind 'maybe'"),
    ],
)
def test_what_is_not_an_answer_from_the_asked_model_is_an_error(
    replies: list[dict[str, Any]], reason: str
) -> None:
    model, sent = planner(replies)
    answer = model.solve(TASK, FakeToolbox("sql"))
    assert answer.kind == "error" and reason in answer.reason
    if "without" in reason:  # one reminder, then give up
        assert sent[1]["messages"][-1] == {
            "role": "user",
            "content": "Call submit_answer with your final answer.",
        }


def test_a_dated_snapshot_of_the_asked_model_is_the_asked_model() -> None:
    reply = message(
        [tool_use("submit_answer", {"kind": "answer", "value": 2})], model=f"{DEFAULT_MODEL}-20260901"
    )
    model, _ = planner([reply])
    assert model.solve(TASK, FakeToolbox("sql")).value == 2.0


def test_it_gives_up_after_the_step_budget() -> None:
    turn = message([tool_use("run_query", {"query": "SELECT 1"})])
    model, sent = planner([turn, turn], max_steps=2, effort="low")
    box = FakeToolbox("sql", [{"columns": [], "rows": []}] * 2)
    answer = model.solve(TASK, box)
    assert answer.kind == "error" and "2 turns" in answer.reason and len(sent) == 2
    assert sent[0]["output_config"] == {"effort": "low"}


def test_submissions_parse_into_the_three_shapes() -> None:
    rows = parse_submission(
        {"kind": "answer", "rows": [{"label": "USD", "value": "12.5"}, {"label": "EUR", "value": None}]}
    )
    assert rows.rows == [("USD", 12.5), ("EUR", None)] and rows.value is None
    name = parse_submission({"kind": "answer", "label": "Gadgets Launch", "caveats": None})
    assert name.label == "Gadgets Launch" and name.caveats == []
    assert parse_submission({"kind": "answer", "rows": [{"name": "USD"}]}).kind == "error"
    assert parse_submission({"kind": "refuse", "reason": "not additive"}).reason == "not additive"
