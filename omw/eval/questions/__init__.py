"""The question set: about a hundred marketing questions in nine categories, each with a ground truth
computed from the seeded world and a plan per arm for the scripted planners."""

from __future__ import annotations

from omw.eval.questions import business, compare, cross, rates, spend, traps, web
from omw.eval.questions.base import Question

MODULES = (spend, rates, web, business, cross, compare, traps)


def all_questions() -> list[Question]:
    found = [question for module in MODULES for question in module.QUESTIONS]
    ids = [question.id for question in found]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate question ids")
    return found
