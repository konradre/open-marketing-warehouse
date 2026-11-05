"""The meta_ads source: resources, hints, slice windows, a clean pacer stop, and the pipeline on dummy."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import dlt
import pytest

from omw.pacing import MetaPacer, StopReason
from omw.pipelines.meta_ads import run
from omw.sources.meta_ads import CADENCES, cadence_window, check_token, meta_ads
from omw.sources.meta_ads.source import insights_params, shape_insight
from omw.sources.meta_ads.token import TokenExpiredError
from omw.sources.windows import Window
from tests.unit.meta_ads.conftest import ACCOUNT, FakeAccount, fixture_rows

TODAY = date(2026, 10, 4)  # daily window 2026-09-27..10-03 covers the fixture days 10-01..03
WINDOW = Window(date(2026, 10, 1), date(2026, 10, 3))


def _source(account: FakeAccount, pacer: MetaPacer, **kwargs: Any) -> Any:
    return meta_ads(account_ids=[ACCOUNT], accounts={ACCOUNT: account}, pacer=pacer, window=WINDOW, **kwargs)


def test_cadences_end_yesterday() -> None:
    assert CADENCES == {"daily": 7, "weekly": 28}
    assert cadence_window("daily", TODAY) == Window(date(2026, 9, 27), date(2026, 10, 3))
    assert cadence_window("weekly", TODAY) == Window(date(2026, 9, 6), date(2026, 10, 3))


def test_resources_and_hints(account: FakeAccount, pacer: MetaPacer) -> None:
    source = _source(account, pacer, levels=["campaign", "ad"])
    assert set(source.resources) == {
        "campaigns",
        "ad_sets",
        "ads",
        "insights_campaigns",
        "insights_ads",
        "sync_windows",
    }
    campaigns: dict[str, Any] = dict(source.resources["campaigns"].compute_table_schema())
    assert campaigns["write_disposition"] == "merge"
    assert [c for c, h in campaigns["columns"].items() if h.get("primary_key")] == ["id"]
    insights: dict[str, Any] = dict(source.resources["insights_ads"].compute_table_schema())
    assert insights["x-merge-strategy"] == "delete-insert"
    assert [c for c, h in insights["columns"].items() if h.get("merge_key")] == ["account_id", "date"]
    assert insights["x-clickhouse-sort"] == ["account_id", "date", "campaign_id", "adset_id", "ad_id"]
    assert all(h["nullable"] is False for h in insights["columns"].values())
    assert account.currency_lookups == 1


def test_insights_params_per_level() -> None:
    campaign = insights_params("campaign", date(2026, 10, 1), 100)
    assert campaign["time_range"] == {"since": "2026-10-01", "until": "2026-10-01"}
    assert campaign["time_increment"] == 1 and campaign["level"] == "campaign"
    assert "filtering" not in campaign
    ad = insights_params("ad", date(2026, 10, 1), 100)
    assert ad["filtering"] == [{"field": "adset.effective_status", "operator": "IN", "value": ["ACTIVE"]}]


def test_shape_insight_types_and_report() -> None:
    row = shape_insight("campaign", ACCOUNT, "USD", fixture_rows("insights_campaigns_2026-10-01")[0])
    assert row["source"] == "meta_ads" and row["report"] == "campaigns" and row["level"] == "campaign"
    assert row["date"] == date(2026, 10, 1) and row["currency"] == "USD"
    assert row["spend"] == Decimal("48.50") and isinstance(row["impressions"], int)
    assert '"purchase"' in row["actions"]
    assert "adset_id" not in row
    ad_row = shape_insight("ad", ACCOUNT, "USD", fixture_rows("insights_ads_2026-10-01")[0])
    assert ad_row["report"] == "ads" and ad_row["ad_id"] == "23870000000000001"


def test_entities_and_insights_cover_the_window(account: FakeAccount, pacer: MetaPacer) -> None:
    source = _source(account, pacer)
    campaigns = list(source.resources["campaigns"])
    assert [c["account_id"] for c in campaigns] == [ACCOUNT, ACCOUNT]
    days = list(source.resources["insights_campaigns"])
    assert len(days) == 6
    assert [p["time_range"]["since"] for p in account.insight_params] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
    ]


def test_a_pacer_stop_ends_the_window_with_the_complete_days(pacer: MetaPacer) -> None:
    account = FakeAccount(pacer=pacer, stop_on=date(2026, 10, 3))
    source = _source(account, pacer, entities=[])
    days = list(source.resources["insights_campaigns"])
    assert {row["date"] for row in days} == {date(2026, 10, 1), date(2026, 10, 2)}
    assert pacer.stopped is not None and pacer.stopped.reason is StopReason.UTILISATION
    assert list(source.resources["sync_windows"])  # still logs the run


def test_resources_after_a_stop_do_nothing(pacer: MetaPacer) -> None:
    account = FakeAccount(pacer=pacer, stop_on=date(2026, 10, 1))
    source = _source(account, pacer)
    assert list(source.resources["insights_campaigns"]) == []
    assert list(source.resources["ads"]) == []
    assert account.entity_calls == []


def test_validation(account: FakeAccount, pacer: MetaPacer) -> None:
    with pytest.raises(ValueError, match="levels"):
        _source(account, pacer, levels=["adset"])
    with pytest.raises(ValueError, match="entities"):
        _source(account, pacer, entities=["leads"])
    with pytest.raises(ValueError, match="access_token"):
        meta_ads(account_ids=[ACCOUNT], access_token=None, pacer=pacer)


def test_token_check() -> None:
    assert check_token(None, TODAY).event is None
    assert check_token("2026-10-30", TODAY).event is None
    expiring = check_token("2026-10-08", TODAY)
    assert expiring.expiring and expiring.event == "token_expiring" and expiring.days_left == 4
    expired = check_token(date(2026, 10, 3), TODAY)
    assert expired.expired and expired.event == "token_expired"


def _pipeline(tmp_path: Path) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="meta_ads_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="meta_ads",
        pipelines_dir=str(tmp_path),
    )


def test_run_loads_the_cadence_window(tmp_path: Path, pacer: MetaPacer) -> None:
    account = FakeAccount(pacer=pacer)
    result = run(
        _pipeline(tmp_path),
        "daily",
        [ACCOUNT],
        today=TODAY,
        pacer=pacer,
        apply_sql=False,
        record_ops=False,
        accounts={ACCOUNT: account},
    )
    assert result.counts["insights_campaigns"] == 6 and result.counts["campaigns"] == 2
    assert result.counts["sync_windows"] == 1
    assert result.stopped is None and result.events == []
    assert [p["time_range"]["since"] for p in account.insight_params][0] == "2026-09-27"


def test_run_records_a_stop_and_still_loads(tmp_path: Path, pacer: MetaPacer) -> None:
    account = FakeAccount(pacer=pacer, stop_on=date(2026, 10, 2))
    result = run(
        _pipeline(tmp_path),
        "weekly",
        [ACCOUNT],
        today=TODAY,
        pacer=pacer,
        apply_sql=False,
        record_ops=False,
        accounts={ACCOUNT: account},
    )
    assert result.stopped is not None
    assert result.counts.get("insights_campaigns", 0) == 2  # 2026-10-01 only
    assert [e.event for e in result.events] == ["stopped"]
    assert result.events[0].reason == "utilisation" and result.events[0].mode == "weekly"


def test_run_refuses_an_expired_token(tmp_path: Path, pacer: MetaPacer) -> None:
    with pytest.raises(TokenExpiredError):
        run(
            _pipeline(tmp_path),
            "daily",
            [ACCOUNT],
            today=TODAY,
            token_expires_at="2026-10-01",
            pacer=pacer,
            apply_sql=False,
            record_ops=False,
            accounts={ACCOUNT: FakeAccount(pacer=pacer)},
        )
