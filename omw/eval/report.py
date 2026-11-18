"""Turn runs into the results section of ``eval/README.md`` and into the JSON files beside it.

Everything here works on a run's JSON form (``Run.to_dict``), so a run made a minute ago and a model run
recorded last month render the same way, and every number in the README is recomputed from per-question
rows rather than read from a stored total.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from omw.eval.questions.base import Question
from omw.eval.runner import Run
from omw.eval.scoring import Summary, summarise, wilson
from omw.eval.truth import expected
from omw.eval.world import SEED, TODAY, World

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
README = EVAL_DIR / "README.md"
QUESTIONS_FILE = EVAL_DIR / "questions.json"
RESULTS_DIR = EVAL_DIR / "results"
SCRIPTED_FILE = RESULTS_DIR / "scripted.json"
BEGIN = "<!-- results:begin (written by `omw eval report`; do not edit by hand) -->"
END = "<!-- results:end -->"
ARM_NAMES = {"sql": "schema + raw SQL", "metrics": "SEMANTICS.md + query_metrics"}
PLANNER_NOTES = {
    "reference": "known-good plans: the harness check",
    "literal": "face-value plans",
    "refuser": "declines everything: the null baseline",
}
Payload = dict[str, Any]
_SERVER = re.compile(r" \(version [^()]*(\([^()]*\))?\)| \(for url [^)]*\)")
"""Where a warehouse error came from: true of one stack only, so left out of a recorded run."""


def questions_payload(questions: list[Question], world: World) -> Payload:
    """The question set with its computed ground truth, for readers and for other harnesses."""
    items = []
    for question in questions:
        want = expected(question, world)
        items.append(
            {
                "id": question.id,
                "category": question.category,
                "question": question.text,
                "answer_format": question.hint(),
                "expect": question.expect,
                "accept": list(question.accepted),
                "tags": list(question.tags),
                "needs_caveat": bool(question.caveat_terms),
                "why": question.why,
                "ground_truth": want.to_dict() if want else None,
            }
        )
    return {"seed": SEED, "today": TODAY.isoformat(), "count": len(items), "questions": items}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n")


def write_runs(path: Path, runs: list[Payload]) -> None:
    """Recorded runs as JSON with one question row per line: small, and a diff shows which answers moved."""
    blocks = []
    for run in runs:
        head = {key: value for key, value in run.items() if key != "rows"}
        rows = ",\n".join("  " + json.dumps(row, separators=(",", ":")) for row in run["rows"])
        blocks.append(json.dumps(head, indent=1)[:-2] + ',\n "rows": [\n' + rows + "\n ]\n}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[\n" + ",\n".join(blocks) + "\n]\n")


def _rounded(value: Any) -> Any:
    """Floats to ten significant digits, so a recorded run does not change with summation order."""
    if isinstance(value, float):
        return float(f"{value:.10g}")
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    return value


def slim(run: Run) -> Payload:
    """A run small enough to keep in the repository: no tool results (each call's name and arguments stay,
    so any answer can be re-run) and no copy of the ground truth, which ``eval/questions.json`` holds."""
    payload = run.to_dict()
    for row in payload["rows"]:
        del row["expected"]
        row["detail"] = _SERVER.sub("", row["detail"])
        row["answer"] = _rounded(row["answer"])
        if "reason" in row["answer"]:
            row["answer"]["reason"] = _SERVER.sub("", row["answer"]["reason"])
        row["calls"] = [
            {
                "name": call["name"],
                "arguments": call["arguments"],
                "error": _SERVER.sub("", call["error"])[:300],
            }
            for call in row["calls"]
        ]
    return payload


def _summary(rows: Iterable[Payload]) -> Summary:
    return summarise((row["expect"] != "answer", row["outcome"], row["disclosed"]) for row in rows)


def _arms(run: Payload) -> list[str]:
    return list(dict.fromkeys(row["arm"] for row in run["rows"]))


def _of(run: Payload, arm: str) -> list[Payload]:
    return [row for row in run["rows"] if row["arm"] == arm]


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "n/a"
    low, high = wilson(part, whole)
    return f"{100 * part / whole:.1f}% ({part}/{whole}; 95% CI {100 * low:.0f}-{100 * high:.0f}%)"


def _short(part: int, whole: int) -> str:
    return f"{part}/{whole}" if whole else "-"


def headline(runs: list[Payload]) -> str:
    lines = [
        "| Planner | Arm | Accuracy on answerable questions | Refuse/clarify correct on traps | "
        "Wrong-number rate (all questions) | Over-refusals | Caveats carried | Errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        note = PLANNER_NOTES.get(run["planner"], f"recorded {run.get('started', '')[:10]}")
        for arm in _arms(run):
            s = _summary(_of(run, arm))
            lines.append(
                f"| `{run['planner']}` ({note}) | {ARM_NAMES.get(arm, arm)} | "
                f"{_pct(s.correct_answers, s.answerable)} | {_pct(s.correct_traps, s.traps)} | "
                f"{_pct(s.wrong_numbers, s.scored)} | {_short(s.over_refusals, s.answerable)} | "
                f"{_short(s.disclosed, s.need_caveat)} | {s.errors} |"
            )
    return "\n".join(lines)


def _grouped(rows: list[Payload], keys_of: Any) -> dict[str, Summary]:
    groups: dict[str, list[Payload]] = {}
    for row in rows:
        for key in keys_of(row):
            groups.setdefault(key, []).append(row)
    return {key: _summary(found) for key, found in groups.items()}


def by_category(run: Payload) -> str:
    arms = _arms(run)
    names = [ARM_NAMES.get(arm, arm) for arm in arms]
    lines = [
        "| Category | Questions | "
        + " | ".join(f"Correct: {name}" for name in names)
        + " | "
        + " | ".join(f"Wrong numbers: {name}" for name in names)
        + " |",
        "|---|---|" + "---|" * (2 * len(arms)),
    ]
    per_arm = {arm: _grouped(_of(run, arm), lambda row: [row["category"]]) for arm in arms}
    for category in per_arm[arms[0]]:
        cells = [per_arm[arm][category] for arm in arms]
        correct = [f"{c.correct_answers + c.correct_traps}/{c.scored}" for c in cells]
        wrong = [str(c.wrong_numbers) for c in cells]
        lines.append(
            f"| {category.replace('_', ' ')} | {cells[0].questions} | " + " | ".join(correct + wrong) + " |"
        )
    return "\n".join(lines)


def traps_by_kind(run: Payload) -> str:
    arms = _arms(run)
    lines = [
        "| Kind of trap | Traps | " + " | ".join(f"Handled: {ARM_NAMES.get(a, a)}" for a in arms) + " |",
        "|---|---|" + "---|" * len(arms),
    ]
    per_arm = {
        arm: _grouped(_of(run, arm), lambda row: row["tags"] if row["expect"] != "answer" else [])
        for arm in arms
    }
    for kind in per_arm[arms[0]]:
        cells = [per_arm[arm][kind] for arm in arms]
        handled = [f"{c.correct_traps}/{c.traps}" for c in cells]
        lines.append(f"| {kind.replace('_', ' ')} | {cells[0].traps} | " + " | ".join(handled) + " |")
    return "\n".join(lines)


def _missed(run: Payload, arm: str) -> set[str]:
    return {row["id"] for row in _of(run, arm) if row["outcome"] != "correct"}


def misses(run: Payload, arm: str, skip: frozenset[str] = frozenset()) -> str:
    """Every row of ``arm`` that is not correct, with what went wrong; ``skip`` leaves out known ones."""
    lines = ["| Question | Outcome | What happened |", "|---|---|---|"]
    for row in _of(run, arm):
        if row["outcome"] == "correct" or row["id"] in skip:
            continue
        detail = re.sub(r"\s+", " ", row["detail"]).replace("|", "/")
        detail = detail if len(detail) <= 170 else detail[:167] + "..."
        lines.append(f"| `{row['id']}` | {row['outcome'].replace('_', ' ')} | {detail} |")
    return "\n".join(lines) if len(lines) > 2 else "None."


def results_markdown(runs: list[Payload]) -> str:
    by_name = {run["planner"]: run for run in runs}
    parts = [headline(runs)]
    literal, reference = by_name.get("literal"), by_name.get("reference")
    gaps = frozenset(_missed(reference, "metrics")) if reference else frozenset()
    if literal:
        parts += [
            "\n### Face-value plans, by category\n",
            by_category(literal),
            "\n### Face-value plans on traps, by kind of trap\n",
            traps_by_kind(literal),
            "\n### Face-value requests the metric layer did not turn into a right answer\n",
            "Beyond the coverage gaps in the next table.\n",
            misses(literal, "metrics", gaps),
        ]
    if reference:
        parts += [
            "\n### Questions the metric layer could not answer with the best plan\n",
            misses(reference, "metrics"),
        ]
    return "\n".join(parts)


def update_readme(markdown: str, path: Path = README) -> None:
    text = path.read_text()
    if BEGIN not in text or END not in text:
        raise ValueError(f"{path} has no results markers")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    path.write_text(f"{head}{BEGIN}\n\n{markdown}\n\n{END}{tail}")


def recorded_model_runs(directory: Path = RESULTS_DIR) -> list[Payload]:
    """Model runs kept under ``eval/results/model-*.json``, oldest first."""
    found = [run for path in sorted(directory.glob("model-*.json")) for run in json.loads(path.read_text())]
    return sorted(found, key=lambda run: run.get("started", ""))
