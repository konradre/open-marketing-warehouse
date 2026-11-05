"""Answer moods (DESIGN.md §6): serve, disclose, clarify, refuse. Every answer carries one."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Mood(StrEnum):
    SERVE = "serve"
    DISCLOSE = "disclose"
    CLARIFY = "clarify"
    REFUSE = "refuse"


class Answer(BaseModel):
    mood: Mood
    reason: str = ""
    """Why the answer was refused or needs clarifying; empty when served."""
    caveats: list[str] = Field(default_factory=list)
    """Conditions that travel with the numbers (disclose)."""
    candidates: list[dict[str, str]] = Field(default_factory=list)
    """For clarify: the metrics a name may mean, with descriptions."""
    sql: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: bool = False

    @classmethod
    def refuse(cls, reason: str) -> Answer:
        return cls(mood=Mood.REFUSE, reason=reason)

    @classmethod
    def clarify(cls, reason: str, candidates: list[dict[str, str]]) -> Answer:
        return cls(mood=Mood.CLARIFY, reason=reason, candidates=candidates)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RefuseError(Exception):
    """Raised while planning when there is no valid path; becomes a refuse answer."""


class ClarifyError(Exception):
    def __init__(self, reason: str, candidates: list[dict[str, str]]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.candidates = candidates
