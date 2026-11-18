"""The failure hook: a webhook and/or an e-mail when a job fails, a check fails or a credential is about to
expire.

Configured in ``.dlt/secrets.toml`` (or ``ALERTS__WEBHOOK_URL`` and friends in the environment)::

    [alerts]
    webhook_url = "https://hooks.example.com/services/..."   # receives a JSON POST
    email_to = "ops@example.com"                             # comma separated for several
    email_from = "omw@example.com"
    smtp_host = "smtp.example.com"
    smtp_port = 587                                          # 465: TLS from the start; otherwise STARTTLS
    smtp_username = "omw@example.com"
    smtp_password = "..."

The webhook body carries ``text`` (one readable message, which Slack-compatible receivers show as is) beside
the structured fields. Sending never raises: a hook that cannot be reached is logged and must not change
what the job itself reports.
"""

from __future__ import annotations

import logging
import smtplib
import socket
import ssl
from dataclasses import asdict, dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any

import dlt
import requests

from omw.pipelines.instants import instant

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
IMPLICIT_TLS_PORT = 465


@dataclass(frozen=True)
class Alert:
    kind: str
    """sync_failed, check_failed, backup_failed, job_crashed, scheduler_error, expiry_warning or test."""
    severity: str
    """error or warning."""
    job: str
    summary: str
    """One line: what happened."""
    source: str = ""
    details: tuple[str, ...] = ()
    host: str = field(default_factory=socket.gethostname)
    at: datetime = field(default_factory=instant)

    @property
    def subject(self) -> str:
        return f"[omw] {self.severity.upper()} {self.job}: {self.summary}"[:200]

    @property
    def text(self) -> str:
        lines = [f"[omw] {self.severity.upper()} {self.job} on {self.host}: {self.summary}"]
        lines += [f"- {detail}" for detail in self.details]
        return "\n".join(lines)

    def payload(self) -> dict[str, Any]:
        body = asdict(self)
        body["at"] = self.at.isoformat()
        body["details"] = list(self.details)
        body["text"] = self.text
        return body


@dataclass(frozen=True)
class AlertConfig:
    webhook_url: str = ""
    email_to: tuple[str, ...] = ()
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""

    @property
    def email(self) -> bool:
        return bool(self.email_to and self.smtp_host)

    @property
    def channels(self) -> list[str]:
        return [name for name, on in (("webhook", bool(self.webhook_url)), ("email", self.email)) if on]

    @classmethod
    def read(cls) -> AlertConfig:
        def value(name: str) -> str:
            found: Any = dlt.secrets.get(f"alerts.{name}")
            return str(found if found is not None else "").strip()

        recipients = tuple(part.strip() for part in value("email_to").split(",") if part.strip())
        return cls(
            webhook_url=value("webhook_url"),
            email_to=recipients,
            email_from=value("email_from") or value("smtp_username"),
            smtp_host=value("smtp_host"),
            smtp_port=int(value("smtp_port") or 587),
            smtp_username=value("smtp_username"),
            smtp_password=value("smtp_password"),
        )


def send_webhook(config: AlertConfig, alert: Alert, session: requests.Session | None = None) -> None:
    response = (session or requests).post(config.webhook_url, json=alert.payload(), timeout=TIMEOUT_SECONDS)
    response.raise_for_status()


def send_email(config: AlertConfig, alert: Alert) -> None:
    message = EmailMessage()
    message["Subject"] = alert.subject
    message["From"] = config.email_from or f"omw@{alert.host}"
    message["To"] = ", ".join(config.email_to)
    message.set_content(alert.text + f"\n\n{alert.kind} at {alert.at.isoformat()}\n")
    smtp: smtplib.SMTP
    if config.smtp_port == IMPLICIT_TLS_PORT:
        smtp = smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
    else:
        smtp = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=TIMEOUT_SECONDS)
    with smtp:
        smtp.ehlo()
        if config.smtp_port != IMPLICIT_TLS_PORT and smtp.has_extn("starttls"):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if config.smtp_username:
            smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


class Notifier:
    """Sends an alert to every configured channel and reports which ones failed."""

    def __init__(self, config: AlertConfig | None = None) -> None:
        self.config = config if config is not None else AlertConfig()
        self.sent: list[Alert] = []
        self._warned = False

    @classmethod
    def from_config(cls) -> Notifier:
        return cls(AlertConfig.read())

    def send(self, alert: Alert) -> list[str]:
        """Deliver ``alert``. Returns one message per channel that failed; never raises."""
        self.sent.append(alert)
        log.log(logging.ERROR if alert.severity == "error" else logging.WARNING, "%s", alert.text)
        if not self.config.channels and not self._warned:
            self._warned = True
            log.warning(
                "no alert channel is configured ([alerts] in .dlt/secrets.toml): "
                "failures reach the log and the exit code only"
            )
        failures: list[str] = []
        for channel, deliver in (("webhook", send_webhook), ("email", send_email)):
            if channel not in self.config.channels:
                continue
            try:
                deliver(self.config, alert)
            except Exception as exc:  # noqa: BLE001 - a broken hook must not change the job's outcome
                failures.append(f"{channel}: {exc.__class__.__name__}: {exc}")
                log.error("could not deliver the alert by %s: %s", channel, exc)
        return failures
