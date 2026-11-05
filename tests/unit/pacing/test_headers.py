from __future__ import annotations

import json

from omw.pacing import read_usage
from omw.pacing.headers import AD_ACCOUNT_USAGE, APP_USAGE, BUSINESS_USE_CASE_USAGE, INSIGHTS_THROTTLE
from tests.unit.pacing.conftest import recorded


def test_takes_the_highest_percentage_across_all_four_headers() -> None:
    usage = read_usage(recorded("headers_rising")[2]["headers"])
    assert usage.pct == 76.0
    assert usage.source == f"{INSIGHTS_THROTTLE}:app_id_util_pct"
    assert usage.tier == "development_access"
    assert usage.regain_access_minutes == 0
    assert {r.header for r in usage.readings} == {
        INSIGHTS_THROTTLE,
        AD_ACCOUNT_USAGE,
        APP_USAGE,
        BUSINESS_USE_CASE_USAGE,
    }


def test_each_header_is_read_on_its_own() -> None:
    assert (
        read_usage({APP_USAGE: json.dumps({"call_count": 12, "total_cputime": 40, "total_time": 7})}).pct
        == 40.0
    )
    assert read_usage({AD_ACCOUNT_USAGE: json.dumps({"acc_id_util_pct": 33.3})}).pct == 33.3
    assert (
        read_usage({INSIGHTS_THROTTLE: json.dumps({"app_id_util_pct": 1, "acc_id_util_pct": 9.5})}).pct == 9.5
    )
    business = read_usage(recorded("headers_blocked")["headers"])
    assert business.pct == 100.0
    assert business.source == f"{BUSINESS_USE_CASE_USAGE}:100200300400500.ads_insights.call_count"
    assert business.regain_access_minutes == 15


def test_header_names_are_case_insensitive() -> None:
    assert read_usage({"X-App-Usage": json.dumps({"call_count": 50})}).pct == 50.0


def test_malformed_headers_are_ignored_not_fatal() -> None:
    usage = read_usage(recorded("headers_malformed")["headers"])
    assert usage.readings == ()
    assert usage.pct == 0.0
    assert usage.source == "none"


def test_no_headers_means_zero() -> None:
    assert read_usage({}).pct == 0.0
