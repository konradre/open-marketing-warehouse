"""The failure hook: what the webhook receives, what the e-mail says, and that a dead hook never raises."""

from __future__ import annotations

import json
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import pytest
import responses

from omw.scheduler import notify
from omw.scheduler.notify import Alert, AlertConfig, Notifier

ALERT = Alert(
    "sync_failed",
    "error",
    "stripe",
    "PermissionError: the key was revoked",
    "stripe",
    ("FAIL stripe/freshness/sync: last sync 31.0h ago, budget 30h",),
    host="warehouse-1",
    at=datetime(2026, 11, 18, 4, 30, tzinfo=UTC),
)
HOOK = "https://hooks.example.com/services/T000/B000/synthetic"


@responses.activate
def test_the_webhook_gets_a_readable_text_and_the_structured_fields() -> None:
    responses.post(HOOK, json={"ok": True})
    assert Notifier(AlertConfig(webhook_url=HOOK)).send(ALERT) == []
    body = json.loads(responses.calls[0].request.body or "{}")
    assert body["text"] == (
        "[omw] ERROR stripe on warehouse-1: PermissionError: the key was revoked\n"
        "- FAIL stripe/freshness/sync: last sync 31.0h ago, budget 30h"
    )
    assert (body["kind"], body["severity"], body["job"], body["source"]) == (
        "sync_failed",
        "error",
        "stripe",
        "stripe",
    )
    assert body["details"] == list(ALERT.details) and body["at"] == "2026-11-18T04:30:00+00:00"


@responses.activate
def test_a_hook_that_fails_is_reported_and_never_raises() -> None:
    responses.post(HOOK, status=500)
    notifier = Notifier(
        AlertConfig(webhook_url=HOOK, email_to=("ops@example.com",), smtp_host="127.0.0.1", smtp_port=1)
    )
    failures = notifier.send(ALERT)
    assert len(failures) == 2 and failures[0].startswith("webhook: HTTPError")
    assert failures[1].startswith("email: ")
    assert notifier.sent == [ALERT]


def test_no_channel_is_not_an_error(caplog: pytest.LogCaptureFixture) -> None:
    notifier = Notifier()
    assert notifier.send(ALERT) == [] and notifier.send(ALERT) == []
    warnings = [r for r in caplog.records if "no alert channel is configured" in r.getMessage()]
    assert len(warnings) == 1  # said once, not per alert
    assert any("PermissionError: the key was revoked" in r.getMessage() for r in caplog.records)


class FakeSMTP:
    sessions: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, **_: Any) -> None:
        self.host, self.port = host, port
        self.steps: list[str] = []
        self.message: EmailMessage | None = None
        FakeSMTP.sessions.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_: Any) -> None:
        self.steps.append("quit")

    def ehlo(self) -> None:
        self.steps.append("ehlo")

    def has_extn(self, name: str) -> bool:
        return name == "starttls"

    def starttls(self, **_: Any) -> None:
        self.steps.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.steps.append(f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        self.message = message
        self.steps.append("send")


def test_the_email_goes_out_over_starttls_with_the_summary_as_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSMTP.sessions.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    config = AlertConfig(
        email_to=("ops@example.com", "data@example.com"),
        email_from="omw@example.com",
        smtp_host="smtp.example.com",
        smtp_username="omw@example.com",
        smtp_password="synthetic",
    )
    assert config.channels == ["email"]
    assert Notifier(config).send(ALERT) == []
    session = FakeSMTP.sessions[0]
    assert (session.host, session.port) == ("smtp.example.com", 587)
    assert session.steps == ["ehlo", "starttls", "ehlo", "login omw@example.com", "send", "quit"]
    assert session.message is not None
    assert session.message["Subject"] == "[omw] ERROR stripe: PermissionError: the key was revoked"
    assert session.message["To"] == "ops@example.com, data@example.com"
    assert "last sync 31.0h ago" in session.message.get_content()
    # Port 465 is TLS from the first byte: no STARTTLS on top, and no login without a user.
    notify.send_email(
        AlertConfig(email_to=("ops@example.com",), smtp_host="smtp.example.com", smtp_port=465), ALERT
    )
    assert FakeSMTP.sessions[1].steps == ["ehlo", "send", "quit"]


def test_the_configuration_is_read_from_the_alerts_section(monkeypatch: pytest.MonkeyPatch) -> None:
    assert AlertConfig.read().channels == []
    monkeypatch.setenv("ALERTS__WEBHOOK_URL", HOOK)
    monkeypatch.setenv("ALERTS__EMAIL_TO", "ops@example.com, data@example.com")
    monkeypatch.setenv("ALERTS__SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERTS__SMTP_PORT", "2525")
    monkeypatch.setenv("ALERTS__SMTP_USERNAME", "omw@example.com")
    config = AlertConfig.read()
    assert config.channels == ["webhook", "email"]
    assert config.email_to == ("ops@example.com", "data@example.com") and config.smtp_port == 2525
    assert config.email_from == "omw@example.com"  # the login, when no sender is named
    monkeypatch.delenv("ALERTS__SMTP_HOST")
    assert AlertConfig.read().channels == ["webhook"]  # recipients without a server are not a channel
