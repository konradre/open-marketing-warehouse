"""Recorded Meta header sequences."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "meta_ads"


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())
