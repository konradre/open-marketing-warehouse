"""A real model in the planner's seat, through the Anthropic SDK (optional: ``uv sync --group eval``).

The model is shown the question, the arm's document (the schema with its column comments, or
``SEMANTICS.md``) and the arm's tools, plus one more tool, ``submit_answer``, that ends the conversation
with a structured answer. It never sees a reference plan or the ground truth: the planner interface only
hands it a ``Task``.

Runs are optional and cost money; nothing here is imported unless a model run is asked for. A model run is
a measurement of that model, so it must be exactly that model: no fallback model is configured, and a
response served by another model, a refusal by the safety classifiers or a truncated reply is recorded as
an ``error`` row, never as an answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from omw.eval.planner import AgentAnswer, number
from omw.eval.questions.base import BLANK, Task
from omw.eval.tools import InfraError, Toolbox, ToolError
from omw.eval.world import TODAY

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
RESULT_LIMIT = 30_000
"""Characters of a tool result passed back to the model; a longer one is cut with a notice."""

INSTRUCTIONS = f"""You answer marketing questions for an analyst from the company's data warehouse.
Today is {TODAY.isoformat()}; "last month", "the last 7 days" and the like are relative to it.

Get every number from the tools. When you are done, call submit_answer exactly once:
- kind "answer": you have the number. Give value for a single number, label for a single name, or rows
  (label and value, in the order asked) for a breakdown. Write an empty label as {BLANK}. Put anything the
  reader must know to trust the number in caveats.
- kind "clarify": the question has more than one valid meaning and picking one would be a guess. Say in
  reason what you need to know.
- kind "refuse": there is no valid way to compute what is asked from this warehouse. Say why in reason.

A wrong number is worse than no number. Do not answer with a number you would not stand behind."""

SUBMIT_ANSWER: dict[str, Any] = {
    "name": "submit_answer",
    "description": "Give the final answer to the question. Call it exactly once, as your last action.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["answer", "clarify", "refuse"]},
            "value": {"type": ["number", "null"], "description": "A single number"},
            "label": {"type": ["string", "null"], "description": "A single name"},
            "rows": {
                "type": "array",
                "description": "A breakdown: one object per row, in the order asked",
                "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "value": {"type": ["number", "null"]}},
                    "required": ["label", "value"],
                },
            },
            "caveats": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["kind"],
    },
}


def parse_submission(payload: dict[str, Any]) -> AgentAnswer:
    """The ``submit_answer`` input as an answer; anything malformed is an error, not a guess."""
    kind = payload.get("kind")
    if kind not in ("answer", "clarify", "refuse"):
        return AgentAnswer("error", reason=f"submit_answer with kind {kind!r}")
    rows = None
    if isinstance(payload.get("rows"), list) and payload["rows"]:
        try:
            rows = [(str(row["label"]), number(row["value"])) for row in payload["rows"]]
        except (KeyError, TypeError):
            return AgentAnswer("error", reason="submit_answer rows are not label and value objects")
    label = payload.get("label")
    return AgentAnswer(
        kind,
        value=number(payload.get("value")),
        label=str(label) if isinstance(label, str) and label else None,
        rows=rows,
        caveats=[str(c) for c in payload.get("caveats") or []],
        reason=str(payload.get("reason") or ""),
    )


def _served(asked: str, got: str) -> bool:
    """An alias resolves to a dated snapshot of the same model; anything else is another model."""
    return got == asked or got.startswith(f"{asked}-")


@dataclass
class AnthropicPlanner:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_steps: int = 12
    client: Any = None
    """An ``anthropic.Anthropic`` client; created from the environment's credentials when unset."""
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = f"model:{self.model}"
        if self.client is None:
            import anthropic  # the optional dependency: only a model run needs it

            self.client = anthropic.Anthropic()

    def solve(self, task: Task, toolbox: Toolbox) -> AgentAnswer:
        tools: list[dict[str, Any]] = [
            {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
            for spec in toolbox.specs()
        ]
        tools.append(SUBMIT_ANSWER)
        # The instructions and the arm's document are the same for every question: cache them.
        system = [
            {
                "type": "text",
                "text": f"{INSTRUCTIONS}\n\n{toolbox.context()}",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": task.prompt}]
        usage: dict[str, Any] = {"model": "", "turns": 0}
        usage |= {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        reminded = False
        for _ in range(self.max_steps):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
            )
            usage["model"] = response.model
            usage["turns"] += 1
            for name in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
                usage[name] += getattr(response.usage, name, 0) or 0
            if not _served(self.model, response.model):
                return AgentAnswer(
                    "error", reason=f"served by {response.model}, not {self.model}", usage=usage
                )
            if response.stop_reason in ("refusal", "max_tokens"):
                return AgentAnswer("error", reason=f"stop_reason {response.stop_reason}", usage=usage)
            # Thinking blocks go back unchanged with the rest of the turn.
            messages.append({"role": "assistant", "content": response.content})
            calls = [block for block in response.content if block.type == "tool_use"]
            results: list[dict[str, Any]] = []
            for call in calls:
                if call.name == "submit_answer":
                    answer = parse_submission(dict(call.input))
                    answer.usage = usage
                    return answer
                results.append(self._run(toolbox, call))
            if results:
                messages.append({"role": "user", "content": results})
            elif reminded:
                return AgentAnswer("error", reason="ended without calling submit_answer", usage=usage)
            else:
                reminded = True
                messages.append({"role": "user", "content": "Call submit_answer with your final answer."})
        return AgentAnswer("error", reason=f"no answer within {self.max_steps} turns", usage=usage)

    @staticmethod
    def _run(toolbox: Toolbox, call: Any) -> dict[str, Any]:
        try:
            text = json.dumps(toolbox.call(call.name, dict(call.input)), default=str)
        except InfraError:
            raise
        except ToolError as exc:
            return {"type": "tool_result", "tool_use_id": call.id, "content": str(exc), "is_error": True}
        if len(text) > RESULT_LIMIT:
            text = text[:RESULT_LIMIT] + f"\n[cut: the result is {len(text)} characters; narrow the query]"
        return {"type": "tool_result", "tool_use_id": call.id, "content": text}
