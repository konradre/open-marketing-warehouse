"""Where a job gets its data from: the platforms (``LiveMode``) or a fixture directory (``FixtureMode``).

Fixture mode is the demo's: the real pipelines, their merges, the marts SQL and the checks all run, and
only the socket is replaced by files from ``omw demo generate``. It lets a deployment be rehearsed end to
end (the scheduler container, the checks, the failure hook, the backup) before any credential exists, and
it is how the scheduler is tested.
"""

from __future__ import annotations

import importlib
import logging
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import dlt

from omw.checks.expiry import read_expiries
from omw.demo import warehouse
from omw.demo.fixtures import Manifest
from omw.demo.load import LOADERS, Run
from omw.demo.replay import Replay
from omw.eval import connect
from omw.pipelines.client import pipeline_client
from omw.scheduler.jobs import Job

log = logging.getLogger(__name__)


class Mode(Protocol):
    name: str

    def client(self) -> Any:
        """The warehouse as the ``pipeline`` user."""

    def sync(self, job: Job) -> dict[str, int]:
        """Run the job's pipeline. Returns rows normalised per table."""

    def today(self) -> date | None:
        """The day the over-count and row-count checks take as today; ``None`` for the real one."""

    def budgets(self) -> dict[str, float] | None:
        """Freshness budgets in hours per source."""

    def expiries(self, source: str) -> dict[str, Any]:
        """The credential end dates the expiry check should look at after a sync of ``source``."""

    def child_args(self) -> list[str]:
        """Arguments that put an ``omw sync`` child process in the same mode."""


def configured_budgets() -> dict[str, float] | None:
    return dlt.config.get("checks.freshness_hours", dict) or None


def last_row_counts(pipeline_name: str) -> dict[str, int]:
    """Rows per table in the last normalize step of a pipeline that just ran in this process."""
    try:
        trace = dlt.attach(pipeline_name).last_trace
    except Exception as exc:  # noqa: BLE001 - the counts are a courtesy; never fail a finished sync on them
        log.debug("no trace for %s: %s", pipeline_name, exc)
        return {}
    if trace is None or trace.last_normalize_info is None:
        return {}
    return dict(trace.last_normalize_info.row_counts)


class SyncFailedError(RuntimeError):
    """A pipeline entry point reported failure without raising."""


class LiveMode:
    """The platforms, with the credentials in ``.dlt/secrets.toml``: each job is its pipeline module's own
    ``main``, exactly what ``uv run python -m omw.pipelines.<source> <cadence>`` runs."""

    name = "live"

    def client(self) -> Any:
        return pipeline_client()

    def sync(self, job: Job) -> dict[str, int]:
        module = importlib.import_module(job.module)
        code = int(module.main(list(job.argv)))
        if code:
            raise SyncFailedError(f"{job.module} {' '.join(job.argv)} ended with exit code {code}")
        return last_row_counts(module.PIPELINE_NAME)

    def today(self) -> date | None:
        return None

    def budgets(self) -> dict[str, float] | None:
        return configured_budgets()

    def expiries(self, source: str) -> dict[str, Any]:
        return read_expiries([source])

    def child_args(self) -> list[str]:
        return []


class FixtureMode:
    """A directory written by ``omw demo generate``: every job is an ordinary daily sync against its
    ``restated`` snapshot. Refuses a warehouse that holds anything the demo did not load."""

    name = "fixtures"

    def __init__(self, directory: Path, expiries: dict[str, Any] | None = None) -> None:
        self.directory = Path(directory)
        self.manifest = Manifest.read(self.directory)
        self._expiries = expiries or {}

    def client(self) -> Any:
        return connect.client("pipeline")  # the same OMW_* settings the demo's destination is built from

    def sync(self, job: Job) -> dict[str, int]:
        client = self.client()
        if warehouse.source_tables(client) and not warehouse.owned(client):
            raise warehouse.DemoRefusedError(
                "fixture mode would load synthetic data over source data the demo did not load; "
                "point OMW_CLICKHOUSE_* at another stack, or run `omw demo drop --force` if it may go"
            )
        replay = Replay(self.directory / "restated", self.manifest.data["accounts"])
        with tempfile.TemporaryDirectory(prefix="omw-fixtures-") as pipelines_dir:
            run = Run(
                self.manifest,
                "restated",
                replay,
                warehouse.destination(),
                pipelines_dir,
                first=False,
                meta_mode="weekly" if job.name == "meta_ads_weekly" else None,
            )
            counts = LOADERS[job.source](run)
        warehouse.mark(client, self.manifest)
        return counts

    def today(self) -> date | None:
        return self.manifest.today

    def budgets(self) -> dict[str, float] | None:
        return configured_budgets()

    def expiries(self, source: str) -> dict[str, Any]:
        """Fixture mode has no credentials, so nothing expires unless a rehearsal says so."""
        return {source: self._expiries[source]} if source in self._expiries else {}

    def child_args(self) -> list[str]:
        return ["--fixtures", str(self.directory)]
