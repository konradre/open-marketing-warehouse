"""``omw eval``: seed the warehouse, run a planner through both arms, write the report.

omw eval seed                       load the synthetic world (refuses to replace data it did not seed)
omw eval report                     run the scripted planners offline, rewrite eval/README.md results
omw eval run --planner model        optional: a real model run, recorded under eval/results/
omw eval drop                       remove everything the seed created
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Any

from omw.eval import report as reporting
from omw.eval import seed as seeding
from omw.eval.connect import client, mcp_endpoint
from omw.eval.generate import build_world
from omw.eval.planner import Planner, RefuserPlanner, ScriptedPlanner
from omw.eval.questions import all_questions
from omw.eval.questions.base import Question
from omw.eval.runner import Run, run_eval
from omw.eval.tools import ARMS, SqlToolbox, Toolbox, cached, direct_runner, mcp_runner, metrics_toolbox
from omw.eval.world import SEED

log = logging.getLogger("omw.eval")
SCRIPTED = ("reference", "literal", "refuser")


def toolboxes(arms: list[str], transport: str) -> list[Toolbox]:
    """The arms' tools as the ``agent`` user. One set per process, so repeated queries are run once."""
    agent = client("agent")
    found: list[Toolbox] = []
    for arm in arms:
        if arm == "sql":
            runner = mcp_runner(*mcp_endpoint()) if transport == "mcp" else direct_runner(agent)
            found.append(SqlToolbox(cached(runner)))
        else:
            found.append(metrics_toolbox(agent))
    return found


def planner_for(name: str, questions: list[Question], args: argparse.Namespace) -> Planner:
    if name == "refuser":
        return RefuserPlanner()
    if name == "model":
        from omw.eval.model_planner import AnthropicPlanner

        return AnthropicPlanner(model=args.model, effort=args.effort, max_steps=args.max_steps)
    return ScriptedPlanner({q.id: q for q in questions}, "literal" if name == "literal" else "reference")


def _print(run: Run) -> None:
    for arm in run.arms:
        s = run.summary(arm)
        log.info(
            "%s / %s: accuracy %d/%d, traps %d/%d, wrong numbers %d/%d, over-refusals %d, errors %d",
            run.planner, arm, s.correct_answers, s.answerable, s.correct_traps, s.traps,
            s.wrong_numbers, s.scored, s.over_refusals, s.errors,
        )  # fmt: skip


def cmd_seed(args: argparse.Namespace) -> int:
    try:
        result = seeding.seed(client("pipeline"), build_world(args.seed), force=args.force)
    except seeding.SeedRefusedError as exc:
        log.error("%s (omw eval seed --force)", exc)
        return 1
    log.info(
        "seeded %d rows into %d raw tables; checks: %s", result.rows, result.tables, result.checks.summary()
    )
    unexpected = seeding.unexpected_verdicts(result.checks)
    for line in unexpected:
        log.error("unexpected check verdict: %s", line)
    return 1 if unexpected else 0


def cmd_drop(args: argparse.Namespace) -> int:
    warehouse = client("pipeline")
    if not seeding.seeded(warehouse) and not args.force:
        log.error("this warehouse was not seeded by omw eval; nothing dropped (omw eval drop --force)")
        return 1
    log.info("dropped %d tables and views", seeding.drop(warehouse))
    return 0


def cmd_questions(args: argparse.Namespace) -> int:
    payload = reporting.questions_payload(all_questions(), build_world())
    reporting.write_json(reporting.QUESTIONS_FILE, payload)
    log.info("wrote %d questions to %s", payload["count"], reporting.QUESTIONS_FILE)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    questions = [q for q in all_questions() if args.only in q.id or args.only == q.category]
    if not questions:
        log.error("no question matches %r", args.only)
        return 1
    planner = planner_for(args.planner, questions, args)
    model = args.model if args.planner == "model" else ""
    run = run_eval(planner, toolboxes(args.arms, args.transport), questions, build_world(), args.reps, model)
    _print(run)
    name = f"model-{re.sub(r'[^a-z0-9.]+', '-', args.model.lower())}" if model else args.planner
    out = Path(args.out) if args.out else reporting.RESULTS_DIR / f"{name}.json"
    reporting.write_runs(out, [run.to_dict() if args.full else reporting.slim(run)])
    log.info("wrote %s", out)
    errors = sum(run.summary(arm).errors for arm in run.arms)
    if errors:
        log.error("%d rows are errors (no scorable answer); the rates leave them out", errors)
    return 2 if errors else 0


def scripted_runs(transport: str = "mcp") -> list[Run]:
    """The three offline planners over both arms, sharing one set of tools."""
    questions = all_questions()
    world = build_world()
    boxes = toolboxes(list(ARMS), transport)
    plans = {q.id: q for q in questions}
    planners: list[Planner] = [ScriptedPlanner(plans, "reference"), ScriptedPlanner(plans, "literal")]
    planners.append(RefuserPlanner())
    return [run_eval(planner, boxes, questions, world, stamp=False) for planner in planners]


def cmd_report(args: argparse.Namespace) -> int:
    runs = scripted_runs(args.transport)
    for run in runs:
        _print(run)
    errors = sum(run.summary(arm).errors for run in runs for arm in run.arms)
    if errors:
        log.error("%d rows are errors; fix the stack before publishing numbers", errors)
        return 2
    payloads: list[dict[str, Any]] = [reporting.slim(run) for run in runs]
    reporting.write_runs(reporting.SCRIPTED_FILE, payloads)
    reporting.write_json(
        reporting.QUESTIONS_FILE, reporting.questions_payload(all_questions(), build_world())
    )
    reporting.update_readme(reporting.results_markdown(payloads + reporting.recorded_model_runs()))
    log.info(
        "wrote %s, %s and the results in %s",
        reporting.SCRIPTED_FILE,
        reporting.QUESTIONS_FILE,
        reporting.README,
    )
    return 0


def add_parser(commands: Any) -> None:
    parser = commands.add_parser("eval", help="evaluate the agent read path (eval/README.md)")
    actions = parser.add_subparsers(dest="action", required=True)

    seed = actions.add_parser("seed", help="load the synthetic world into raw and rebuild marts")
    seed.add_argument("--seed", type=int, default=SEED, help="only the default has published ground truth")
    seed.add_argument("--force", action="store_true", help="replace source data this seed did not create")
    seed.set_defaults(handler=cmd_seed)

    drop = actions.add_parser("drop", help="remove every table and view the seed created")
    drop.add_argument("--force", action="store_true", help="drop even without the seed marker")
    drop.set_defaults(handler=cmd_drop)

    questions = actions.add_parser("questions", help="write eval/questions.json with the ground truth")
    questions.set_defaults(handler=cmd_questions)

    run = actions.add_parser("run", help="run one planner through the arms and record the run")
    run.add_argument("--planner", choices=[*SCRIPTED, "model"], default="reference")
    run.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    run.add_argument(
        "--transport", choices=["mcp", "direct"], default="mcp", help="how the SQL arm runs queries"
    )
    run.add_argument("--only", default="", help="a category, or part of a question id")
    run.add_argument("--reps", type=int, default=1, help="repeats per question, for model planners")
    run.add_argument("--model", default="claude-opus-5-5")
    run.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    run.add_argument("--max-steps", type=int, default=12)
    run.add_argument("--out", help="where to write the run (default eval/results/<planner>.json)")
    run.add_argument("--full", action="store_true", help="keep every tool result in the recorded run")
    run.set_defaults(handler=cmd_run)

    report = actions.add_parser("report", help="run the scripted planners and rewrite eval/README.md results")
    report.add_argument("--transport", choices=["mcp", "direct"], default="mcp")
    report.set_defaults(handler=cmd_report)
