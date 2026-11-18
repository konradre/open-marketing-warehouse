"""Fakes for the scheduler: a run log in memory, a notifier that keeps what it was asked to send."""

from __future__ import annotations

from datetime import datetime

import pytest

from omw.scheduler.notify import Notifier
from omw.scheduler.runs import Outcome, SlotState
from tests.unit.checks.conftest import FakeWarehouse


class MemoryStore:
    """What ``RunStore`` is to the loop, without a warehouse."""

    def __init__(self) -> None:
        self.outcomes: list[Outcome] = []
        self.down = False

    def record(self, outcome: Outcome) -> None:
        if self.down:
            raise ConnectionError("warehouse is down")
        self.outcomes.append(outcome)

    def slot_states(self) -> dict[str, SlotState]:
        if self.down:
            raise ConnectionError("warehouse is down")
        latest: dict[str, datetime] = {}
        for outcome in self.outcomes:
            latest[outcome.job] = max(outcome.slot, latest.get(outcome.job, outcome.slot))
        found: dict[str, SlotState] = {}
        for job, slot in latest.items():
            tries = [o for o in self.outcomes if o.job == job and o.slot == slot]
            found[job] = SlotState(slot, len(tries), str(tries[-1].status), tries[-1].finished_at)
        return found

    def statuses(self) -> list[tuple[str, str]]:
        return [(outcome.job, str(outcome.status)) for outcome in self.outcomes]


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def notifier() -> Notifier:
    return Notifier()


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


@pytest.fixture(autouse=True)
def no_alert_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads a developer's real ``[alerts]`` section."""
    for name in ("WEBHOOK_URL", "EMAIL_TO", "EMAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME"):
        monkeypatch.delenv(f"ALERTS__{name}", raising=False)


def kinds(notifier: Notifier) -> list[tuple[str, str]]:
    return [(alert.kind, alert.job) for alert in notifier.sent]
