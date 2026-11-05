"""The pacer, driven by recorded header sequences (rising usage, error 4, recovery)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests
import responses

from omw.pacing import MetaPacer, PacerConfig, PacerStop, StopReason
from omw.pacing.pacer import rate_limit_error
from tests.unit.pacing.conftest import recorded

URL = "https://graph.facebook.com/v26.0/act_100000000000001/insights"


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _pacer(**config: Any) -> tuple[MetaPacer, Recorder]:
    recorder = Recorder()
    return MetaPacer(PacerConfig(**config), sleep=recorder), recorder


def test_sleep_is_zero_below_slow_at_and_linear_up_to_stop_at() -> None:
    pacer, _ = _pacer(slow_at=75, stop_at=95, max_sleep_seconds=300)
    assert pacer.sleep_for(0) == 0 and pacer.sleep_for(75) == 0
    assert pacer.sleep_for(80) == 75.0
    assert pacer.sleep_for(85) == 150.0
    assert pacer.sleep_for(94) == 285.0
    assert pacer.sleep_for(200) == 300.0


def test_rising_usage_sleeps_in_proportion_then_stops_near_the_limit() -> None:
    pacer, recorder = _pacer(slow_at=75, stop_at=95, max_sleep_seconds=300)
    stopped_at = None
    for index, response in enumerate(recorded("headers_rising")):
        try:
            pacer.apply(pacer.check(response["status"], response["headers"]))
        except PacerStop as stop:
            stopped_at = index
            assert stop.reason is StopReason.UTILISATION
            assert stop.utilisation == 97.0
            break
    assert stopped_at == 5  # 10, 40, 76, 85, 93 pass; 97 stops
    assert recorder.sleeps == [15.0, 150.0, 270.0]  # 76 -> 85 -> 93
    summary = pacer.summary
    assert summary.requests == 6 and summary.sleeps == 3 and summary.total_sleep_seconds == 435.0
    assert summary.peak_utilisation == 97.0 and summary.stop is StopReason.UTILISATION
    assert summary.tier == "development_access"
    assert pacer.stopped is not None


@pytest.mark.parametrize(("fixture", "code"), [("headers_error4", 4), ("headers_error80004", 80004)])
def test_rate_limit_error_is_a_stop_not_a_retry(fixture: str, code: int) -> None:
    pacer, recorder = _pacer()
    response = recorded(fixture)
    body = json.dumps(response["body"])
    assert rate_limit_error(response["status"], body) == (code, response["body"]["error"]["message"])
    with pytest.raises(PacerStop) as info:
        pacer.apply(pacer.check(response["status"], response["headers"], body))
    assert info.value.reason is StopReason.RATE_LIMITED
    assert f"error {code}" in info.value.detail
    assert recorder.sleeps == []
    assert pacer.stopped is info.value and pacer.summary.stop is StopReason.RATE_LIMITED


def test_successful_bodies_are_never_parsed_for_errors() -> None:
    assert rate_limit_error(200, json.dumps({"error": {"code": 4}})) is None
    assert rate_limit_error(400, "not json") is None
    assert rate_limit_error(400, json.dumps({"error": {"code": 190, "message": "bad token"}})) is None


def test_recovery_shrinks_the_sleeps_to_zero() -> None:
    pacer, recorder = _pacer(slow_at=75, stop_at=95, max_sleep_seconds=100)
    for response in recorded("headers_recovery"):
        pacer.apply(pacer.check(response["status"], response["headers"]))
    assert recorder.sleeps == [75.0, 25.0]  # 90, 80 sleep; 60 and 20 do not
    assert pacer.stopped is None and pacer.summary.stop is None


def test_blocked_use_case_stops_even_at_low_utilisation() -> None:
    pacer, _ = _pacer()
    response = recorded("headers_blocked")
    with pytest.raises(PacerStop) as info:
        pacer.apply(pacer.check(response["status"], response["headers"]))
    assert info.value.reason is StopReason.ACCESS_BLOCKED
    assert "15 min" in info.value.detail


def test_config_is_validated() -> None:
    with pytest.raises(ValueError):
        PacerConfig(slow_at=95, stop_at=75)
    with pytest.raises(ValueError):
        PacerConfig(max_sleep_seconds=-1)


@responses.activate
def test_installed_on_a_session_it_paces_every_send_and_fails_fast_after_a_stop() -> None:
    pacer, recorder = _pacer(slow_at=75, stop_at=95, max_sleep_seconds=100)
    rising = recorded("headers_rising")
    for response in rising[2:4]:  # 76% then 85%
        responses.get(URL, json={"data": []}, headers=response["headers"])
    error = recorded("headers_error4")
    responses.get(URL, json=error["body"], status=error["status"], headers=error["headers"])
    session = pacer.install(requests.Session())

    assert session.get(URL).ok
    assert session.get(URL).ok
    assert recorder.sleeps == [5.0, 50.0]
    with pytest.raises(PacerStop) as info:
        session.get(URL)
    assert info.value.reason is StopReason.RATE_LIMITED
    with pytest.raises(PacerStop):
        session.get(URL)
    assert len(responses.calls) == 3  # the fourth call never reached the network
