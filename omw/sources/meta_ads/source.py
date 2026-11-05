"""The dlt source: merge entities, per-level insights replacing their slice, and a ``sync_windows`` log."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import dlt
from dlt.common.schema.typing import TMergeDispositionDict
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource

from omw.pacing import MetaPacer, PacerStop
from omw.sources.meta_ads.api import connect
from omw.sources.meta_ads.cadences import SyncMode, cadence_window
from omw.sources.meta_ads.helpers import DEFAULT_POLLING, JobPolling, export, page_entities, wait_for_job
from omw.sources.meta_ads.settings import (
    ACTION_ATTRIBUTION_WINDOWS,
    BREAKDOWNS,
    ENTITY_RESOURCES,
    ENTITY_TEXT_DEFAULTS,
    INSIGHT_FIELDS,
    INSIGHTS_MERGE_KEY,
    LEVEL_KEYS,
    LEVEL_REPORT,
    InsightsLevel,
    entity_columns,
    insights_columns,
    insights_sort_key,
)
from omw.sources.search_console.grains import column
from omw.sources.windows import Window

log = logging.getLogger(__name__)

SOURCE_NAME = "meta_ads"
MERGE_DISPOSITION: TMergeDispositionDict = {"disposition": "merge", "strategy": "delete-insert"}
AD_LEVEL_ADSET_STATUSES = ("ACTIVE",)
"""Ad-level insights are pulled for live ad sets only (DESIGN.md §5)."""


def _paced(pacer: MetaPacer, name: str, items: Iterator[Any]) -> Iterator[Any]:
    """Yield from ``items`` until the pacer stops; then end the resource instead of failing the run."""
    if pacer.stopped is not None:
        log.warning("meta_ads %s skipped: pacer already stopped (%s)", name, pacer.stopped.detail)
        return
    try:
        yield from items
    except PacerStop as stop:
        log.warning("meta_ads %s ended early: %s", name, stop.detail)


def _entity_resource(
    name: str, accounts: Mapping[str, Any], pacer: MetaPacer, page_size: int, states: Sequence[str] | None
) -> DltResource:
    method_name, fields = ENTITY_RESOURCES[name]

    def rows() -> Iterator[list[dict[str, Any]]]:
        for account_id, account in accounts.items():
            for page in page_entities(getattr(account, method_name), fields, states, page_size):
                for row in page:
                    row.setdefault("account_id", account_id)
                    for field in ENTITY_TEXT_DEFAULTS[name]:
                        row[field] = str(row.get(field) or "")
                yield page

    resource = dlt.resource(
        lambda: _paced(pacer, name, rows()),
        name=name,
        write_disposition="merge",
        primary_key="id",
        columns=entity_columns(name),
    )
    # ClickHouse needs the primary key (id, from the merge) to lead the sorting key.
    return clickhouse_adapter(resource, sort=["id"])


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(0)


def _int(value: Any) -> int:
    return int(value) if value not in (None, "") else 0


def _float(value: Any) -> float:
    return float(value) if value not in (None, "") else 0.0


def shape_insight(
    level: InsightsLevel,
    account_id: str,
    currency: str,
    item: Mapping[str, Any],
    breakdown: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source": SOURCE_NAME,
        "report": breakdown or LEVEL_REPORT[level],
        "level": level,
        "account_id": account_id,
        "currency": currency,
        "date": date.fromisoformat(str(item["date_start"])),
        "campaign_id": str(item.get("campaign_id", "")),
        "campaign_name": str(item.get("campaign_name", "")),
    }
    if level == "ad":
        for name in ("adset_id", "adset_name", "ad_id", "ad_name"):
            row[name] = str(item.get(name, ""))
    for name in BREAKDOWNS[breakdown] if breakdown else ():
        row[name] = str(item.get(name, ""))
    row.update(
        {
            "impressions": _int(item.get("impressions")),
            "clicks": _int(item.get("clicks")),
            "reach": _int(item.get("reach")),
            "spend": _decimal(item.get("spend")),
            "frequency": _float(item.get("frequency")),
            "ctr": _float(item.get("ctr")),
            "cpc": _float(item.get("cpc")),
            "cpm": _float(item.get("cpm")),
            "actions": json.dumps(item.get("actions", []), separators=(",", ":")),
            "action_values": json.dumps(item.get("action_values", []), separators=(",", ":")),
        }
    )
    return row


def insights_params(
    level: InsightsLevel, day: date, page_size: int, breakdown: str | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "level": level,
        "fields": list(INSIGHT_FIELDS),
        "time_range": {"since": day.isoformat(), "until": day.isoformat()},
        "time_increment": 1,
        "action_attribution_windows": list(ACTION_ATTRIBUTION_WINDOWS),
        "limit": page_size,
    }
    if breakdown:
        params["breakdowns"] = list(BREAKDOWNS[breakdown])
    if level == "ad":
        params["filtering"] = [
            {"field": "adset.effective_status", "operator": "IN", "value": list(AD_LEVEL_ADSET_STATUSES)}
        ]
    return params


def _insights_resource(
    level: InsightsLevel,
    accounts: Mapping[str, Any],
    currencies: Mapping[str, str],
    pacer: MetaPacer,
    window: Window,
    page_size: int,
    polling: JobPolling,
    sleep: Callable[[float], None],
    breakdown: str | None = None,
) -> DltResource:
    name = f"insights_{breakdown or LEVEL_REPORT[level]}"

    def rows() -> Iterator[list[dict[str, Any]]]:
        for account_id, account in accounts.items():
            for day in window:
                # One async job per day; a day is yielded only once fully fetched, so a pacer stop in the
                # middle of a day leaves that day as it was in the warehouse.
                params = insights_params(level, day, page_size, breakdown)
                job = account.get_insights(params=params, is_async=True)
                job = wait_for_job(job, polling, sleep)
                day_rows = [
                    shape_insight(level, account_id, currencies[account_id], export(item), breakdown)
                    for item in job.get_result(params={"limit": page_size})
                ]
                yield day_rows

    resource = dlt.resource(
        lambda: _paced(pacer, name, rows()),
        name=name,
        write_disposition=MERGE_DISPOSITION,
        merge_key=list(INSIGHTS_MERGE_KEY),
        columns=insights_columns(level, breakdown),
    )
    return clickhouse_adapter(resource, sort=insights_sort_key(level, breakdown))


def _sync_windows_resource(
    account_ids: Sequence[str], window: Window, mode: SyncMode, levels: Sequence[str]
) -> DltResource:
    def rows() -> Iterator[dict[str, Any]]:
        loaded_at = datetime.now(tz=UTC)
        for account_id in account_ids:
            yield {
                "account_id": account_id,
                "mode": mode,
                "levels": ",".join(levels),
                "window_start": window.start,
                "window_end": window.end,
                "loaded_at": loaded_at,
            }

    resource = dlt.resource(
        rows,
        name="sync_windows",
        write_disposition="append",
        columns={
            "account_id": column("text"),
            "mode": column("text"),
            "levels": column("text"),
            "window_start": column("date"),
            "window_end": column("date"),
            "loaded_at": column("timestamp"),
        },
    )
    return clickhouse_adapter(resource, sort=["account_id", "window_start"])


def account_currency(account: Any) -> str:
    """The account's currency, from the object if it carries it, else one small API call."""
    data = (
        export(account)
        if hasattr(account, "export_all_data")
        else dict(account)
        if isinstance(account, dict)
        else {}
    )
    if data.get("currency"):
        return str(data["currency"])
    fetched = account.api_get(fields=["currency"])
    return str(export(fetched).get("currency", ""))


@dlt.source(name=SOURCE_NAME, max_table_nesting=0)
def meta_ads(
    account_ids: Sequence[str] = dlt.config.value,
    access_token: str | None = dlt.secrets.value,
    mode: SyncMode = "daily",
    window: Window | None = None,
    levels: Sequence[InsightsLevel] = ("campaign",),
    breakdowns: Sequence[str] = (),
    entities: Sequence[str] = tuple(ENTITY_RESOURCES),
    entity_states: Sequence[str] | None = None,
    pacer: MetaPacer | None = None,
    api_version: str | None = None,
    page_size: int = 100,
    polling: JobPolling = DEFAULT_POLLING,
    sleep: Callable[[float], None] | None = None,
    accounts: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> Iterator[DltResource]:
    """Meta Ads entities and insights for ``window`` (default: the cadence's trailing window).

    ``account_ids`` without the ``act_`` prefix. ``levels``: ``campaign`` (default) and/or ``ad`` (live ad
    sets only). ``breakdowns``: campaign-level presets (``age_gender``, ``countries``, ``placements``), each
    its own table. ``accounts`` injects SDK-like account objects for tests; otherwise each account is
    connected through a session wrapped by ``pacer``.
    """
    import time

    pacer = pacer or MetaPacer()
    sleep = sleep or time.sleep
    if not account_ids:
        raise ValueError("meta_ads needs at least one account id (sources.meta_ads.account_ids)")
    unknown_levels = set(levels) - set(LEVEL_KEYS)
    if unknown_levels:
        raise ValueError(
            f"unknown insights levels {sorted(unknown_levels)}; choose from {sorted(LEVEL_KEYS)}"
        )
    unknown_breakdowns = set(breakdowns) - set(BREAKDOWNS)
    if unknown_breakdowns:
        raise ValueError(f"unknown breakdowns {sorted(unknown_breakdowns)}; choose from {sorted(BREAKDOWNS)}")
    unknown_entities = set(entities) - set(ENTITY_RESOURCES)
    if unknown_entities:
        raise ValueError(
            f"unknown entities {sorted(unknown_entities)}; choose from {sorted(ENTITY_RESOURCES)}"
        )
    if accounts is None:
        if not access_token:
            raise ValueError("meta_ads needs access_token (sources.meta_ads.access_token)")
        accounts = {
            account_id.removeprefix("act_"): connect(account_id, access_token, pacer, api_version)
            for account_id in account_ids
        }
    window = window or cadence_window(mode, today or date.today())
    currencies = {account_id: account_currency(account) for account_id, account in accounts.items()}

    for name in entities:
        yield _entity_resource(name, accounts, pacer, page_size, entity_states)
    for level in levels:
        yield _insights_resource(level, accounts, currencies, pacer, window, page_size, polling, sleep)
    for breakdown in breakdowns:
        yield _insights_resource(
            "campaign", accounts, currencies, pacer, window, page_size, polling, sleep, breakdown
        )
    yield _sync_windows_resource(list(accounts), window, mode, [*levels, *breakdowns])
