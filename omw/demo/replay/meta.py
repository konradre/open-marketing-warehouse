"""Replay handler for Meta's Graph API, as the ``facebook_business`` SDK calls it.

The Meta source reaches the Marketing API through the SDK, and the SDK through a ``requests`` session the
pacer wraps. Fixture mode answers at that session, so the SDK's cursors, the async report runs and the
pacer all run as in production:

- ``GET /act_<id>``: the account (the source reads its currency).
- ``GET /act_<id>/campaigns|adsets|ads``: entity lists, with ``fields``, ``effective_status`` and cursors.
- ``POST /act_<id>/insights``: starts an async report run for the day, level and breakdowns asked for.
  ``GET /<run>`` reports it running once, then complete; ``GET /<run>/insights`` pages its rows.
- ``GET /<ad>/leads``: the leads of an ad created after the ``time_created`` filter.

Every answer carries Meta's usage headers, rising slowly with the number of requests but staying under the
pacer's slow-down threshold, so a demo run is paced without ever sleeping.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

from omw.demo.replay.transport import Answer, Call, Fixtures, Handler, not_found
from omw.sources.meta_ads.settings import BREAKDOWNS

GRAPH_HOST = "graph.facebook.com"
SOURCE = "meta_ads"
EDGES = {"campaigns": "campaigns", "adsets": "ad_sets", "ads": "ads"}
LEVEL_FILES = {"campaign": "insights_campaigns", "ad": "insights_ads"}
TIER = "standard_access"
_PATH = re.compile(r"^/v\d+\.\d+/(?P<node>[^/]+)(?:/(?P<edge>\w+))?$")


def usage_headers(account_id: str, requests_seen: int) -> dict[str, str]:
    """The four usage headers the pacer reads, at a utilisation that grows with the run and tops out
    well below the level where the pacer starts to slow down."""
    app = round(min(60.0, 4.0 + requests_seen / 25), 1)
    account = round(app / 2, 1)
    use_case = {"type": "ads_insights", "call_count": int(app), "total_cputime": int(account)}
    use_case |= {"total_time": int(account), "estimated_time_to_regain_access": 0}
    return {
        "x-fb-ads-insights-throttle": json.dumps(
            {"app_id_util_pct": app, "acc_id_util_pct": account, "ads_api_access_tier": TIER}
        ),
        "x-ad-account-usage": json.dumps(
            {"acc_id_util_pct": account, "reset_time_duration": 100, "ads_api_access_tier": TIER}
        ),
        "x-app-usage": json.dumps(
            {"call_count": int(app), "total_cputime": int(account), "total_time": int(account)}
        ),
        "x-business-use-case-usage": json.dumps({account_id: [use_case]}),
    }


def _parameters(call: Call) -> dict[str, Any]:
    """Query and form parameters as the SDK sent them: JSON-encoded values decoded, the rest as text."""
    raw = dict(call.query)
    if isinstance(call.body, str):
        raw |= {key: values[0] for key, values in parse_qs(call.body).items()}
    elif isinstance(call.body, dict):
        raw |= call.body
    found: dict[str, Any] = {}
    for key, value in raw.items():
        try:
            found[key] = json.loads(value) if isinstance(value, str) and value[:1] in "[{" else value
        except ValueError:
            found[key] = value
    return found


def _page(rows: list[dict[str, Any]], params: dict[str, Any], url: str) -> dict[str, Any]:
    """One page of an edge, with the cursors the SDK follows (``paging.next`` only when more remains)."""
    start = int(params.get("after") or 0)
    limit = int(params.get("limit") or 25)
    page = rows[start : start + limit]
    answer: dict[str, Any] = {"data": page}
    if page:
        paging: dict[str, Any] = {"cursors": {"before": str(start), "after": str(start + len(page))}}
        if start + limit < len(rows):
            paging["next"] = f"{url}?after={start + len(page)}"
        answer["paging"] = paging
    return answer


def _project(row: dict[str, Any], fields: Any) -> dict[str, Any]:
    """The requested fields of an object (the id always comes back), as the Graph API returns them."""
    names = fields.split(",") if isinstance(fields, str) else list(fields or [])
    return {name: row[name] for name in dict.fromkeys(("id", *names)) if name in row} if names else dict(row)


def graph(fixtures: Fixtures, account_ids: list[str]) -> Handler:
    runs: dict[str, tuple[str, str, str]] = {}
    """report run id -> (account id, fixture file, day)."""
    polled: set[str] = set()
    seen = 0

    def rows(name: str, account_id: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = fixtures.load(SOURCE, name)["data"]
        return [row for row in found if row.get("account_id", account_id) == account_id]

    def start_run(account_id: str, params: dict[str, Any]) -> dict[str, Any]:
        breakdowns = params.get("breakdowns")
        if breakdowns:
            report = next(name for name, fields in BREAKDOWNS.items() if list(fields) == list(breakdowns))
            name = f"insights_{report}"
        else:
            name = LEVEL_FILES[str(params.get("level", "campaign"))]
        run_id = f"99{len(runs) + 1:014d}"
        runs[run_id] = (account_id, name, str(params["time_range"]["since"]))
        return {"report_run_id": run_id}

    def run_status(run_id: str) -> dict[str, Any]:
        done = run_id in polled  # running on the first poll, complete from the second
        polled.add(run_id)
        return {
            "id": run_id,
            "account_id": runs[run_id][0],
            "async_status": "Job Completed" if done else "Job Running",
            "async_percent_completion": 100 if done else 40,
        }

    def leads(ad_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        since = max(
            (int(f["value"]) for f in params.get("filtering", []) if f["field"] == "time_created"), default=0
        )
        return [
            row
            for row in fixtures.load(SOURCE, "leads")["data"]
            if row["ad_id"] == ad_id
            and datetime.strptime(row["created_time"], "%Y-%m-%dT%H:%M:%S%z").timestamp() > since
        ]

    def answer(call: Call) -> tuple[int, Any] | None:
        found = _PATH.match(call.path.rstrip("/"))  # the SDK asks for a node as `/act_<id>/`
        if not found:
            return None
        node, edge = found["node"], found["edge"]
        params = _parameters(call)
        url = f"https://{GRAPH_HOST}{call.path}"
        account_id = node.removeprefix("act_")
        if node.startswith("act_") and account_id in account_ids:
            if call.method == "POST" and edge == "insights":
                return 200, start_run(account_id, params)
            if call.method == "GET" and edge is None:
                return 200, _project(fixtures.load(SOURCE, "account"), params.get("fields"))
            if call.method == "GET" and edge in EDGES:
                states = params.get("effective_status")
                found_rows = [
                    r for r in rows(EDGES[edge], account_id) if not states or r["effective_status"] in states
                ]
                return 200, _page([_project(r, params.get("fields")) for r in found_rows], params, url)
            return None
        if call.method != "GET":
            return None
        if node in runs and edge is None:
            return 200, run_status(node)
        if node in runs and edge == "insights":
            run_account, name, day = runs[node]
            return 200, _page([r for r in rows(name, run_account) if r["date_start"] == day], params, url)
        if edge == "leads":
            return 200, _page(leads(node, params), params, url)
        return None

    def handle(call: Call) -> Answer:
        nonlocal seen
        seen += 1
        found = answer(call)
        if found is None:
            status, payload = not_found(call)[:2]
            return status, {"error": {**payload["error"], "type": "GraphMethodException", "code": 100}}
        return found[0], found[1], usage_headers(account_ids[0], seen)

    return handle
