"""One generated fixture directory for the whole session: the generator is deterministic, so every test
reads the same files."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import dlt
import pytest

from omw.demo.fixtures import Manifest, generate
from omw.demo.replay import Replay

TODAY = date(2026, 11, 18)
DAYS = 90


@pytest.fixture(scope="session")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("demo")
    generate(out, TODAY, DAYS)
    return out


@pytest.fixture(scope="session")
def manifest(demo_dir: Path) -> Manifest:
    return Manifest.read(demo_dir)


def payload(directory: Path, snapshot: str, source: str, name: str) -> Any:
    return json.loads((directory / snapshot / source / f"{name}.json").read_text())


@pytest.fixture
def replay(demo_dir: Path, manifest: Manifest) -> Replay:
    return Replay(demo_dir / "restated", manifest.data["accounts"])


@pytest.fixture(autouse=True)
def fresh_pipeline(tmp_path: Path) -> dlt.Pipeline:
    """A pipeline of this test's own. Reading a dlt resource outside a run uses the state of the pipeline
    last activated in the process, so without this a source would start from another test's cursor."""
    return dlt.pipeline(
        pipeline_name="demo_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="demo_unit",
        pipelines_dir=str(tmp_path / "dlt"),
    )
