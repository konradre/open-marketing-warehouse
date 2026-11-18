"""A stand-in for ``adAnalytics`` as LinkedIn documents it: no pagination, and a response cut off at a fixed
number of elements with nothing in it to say so. It answers from a synthetic account, honouring the
``pivot``, ``dateRange`` and ``campaigns`` facets of the request path."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote

_DATE = r"\(year:(\d+),month:(\d+),day:(\d+)\)"
_RANGE = re.compile(rf"dateRange=\(start:{_DATE},end:{_DATE}\)")
_CAMPAIGNS = re.compile(r"campaigns=List\(([^)]*)\)")


@dataclass
class CappedAnalytics:
    """``creatives`` maps a campaign id to how many creatives it runs; every one is active on every day."""

    creatives: dict[str, int]
    start: date
    days: int
    cap: int
    requests: list[str] = field(default_factory=list)
    served: list[int] = field(default_factory=list)

    def rows(self, pivot: str) -> list[dict[str, Any]]:
        """Every row the account has at ``pivot``: the answer a complete load must reproduce."""
        out: list[dict[str, Any]] = []
        for offset in range(self.days):
            day = self.start + timedelta(days=offset)
            stamp = {"year": day.year, "month": day.month, "day": day.day}
            for campaign, count in sorted(self.creatives.items()):
                ids = [f"{campaign}{n:04d}" for n in range(count)] if pivot == "CREATIVE" else [campaign]
                kind = "sponsoredCreative" if pivot == "CREATIVE" else "sponsoredCampaign"
                for pivot_id in ids:
                    out.append(
                        {
                            "dateRange": {"start": stamp, "end": stamp},
                            "pivotValues": [f"urn:li:{kind}:{pivot_id}"],
                            "impressions": 100 if pivot == "CREATIVE" else 100 * count,
                            "clicks": 2 if pivot == "CREATIVE" else 2 * count,
                            "costInLocalCurrency": "1.5" if pivot == "CREATIVE" else str(1.5 * count),
                            "_campaign": campaign,
                        }
                    )
        return out

    def __call__(self, path: str) -> list[dict[str, Any]]:
        self.requests.append(path)
        pivot = "CREATIVE" if "pivot=CREATIVE" in path else "CAMPAIGN"
        found = _RANGE.search(path)
        assert found, path
        y1, m1, d1, y2, m2, d2 = (int(part) for part in found.groups())
        first, last = date(y1, m1, d1), date(y2, m2, d2)
        facet = _CAMPAIGNS.search(path)
        wanted = {urn.rsplit(":", 1)[-1] for urn in unquote(facet[1]).split(",")} if facet else None
        rows = []
        for row in self.rows(pivot):
            start = row["dateRange"]["start"]
            day = date(start["year"], start["month"], start["day"])
            if first <= day <= last and (wanted is None or row["_campaign"] in wanted):
                rows.append({k: v for k, v in row.items() if k != "_campaign"})
        answer = rows[: self.cap]  # LinkedIn's limit: the rest is simply not there
        self.served.append(len(answer))
        return answer
