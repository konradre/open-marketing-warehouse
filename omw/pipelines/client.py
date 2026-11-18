"""The warehouse as the ``pipeline`` user, for everything that runs beside the dlt pipelines."""

from __future__ import annotations

from typing import Any

import clickhouse_connect
import dlt

from omw.eval.connect import DEV_PASSWORDS, setting

_SECTION = "destination.clickhouse.credentials"


def _secret(name: str) -> Any:
    return dlt.secrets.get(f"{_SECTION}.{name}")


def pipeline_credentials() -> dict[str, Any]:
    """Host, HTTP port, user and password of the pipeline's warehouse connection.

    From the dlt destination credentials, field by field, so an environment variable
    (``DESTINATION__CLICKHOUSE__CREDENTIALS__HOST``) overrides one value of ``.dlt/secrets.toml``: the
    scheduler container reaches the same warehouse under another host name. A stack with no dlt
    credentials at all (the demo, a fixture-mode scheduler) falls back to the ``OMW_*`` settings compose uses.
    """
    host = _secret("host")
    if host is None:
        return {
            "host": setting("OMW_CLICKHOUSE_HOST", "127.0.0.1"),
            "port": int(setting("OMW_CLICKHOUSE_HTTP_PORT", "8123")),
            "username": "pipeline",
            "password": setting("OMW_PIPELINE_PASSWORD", DEV_PASSWORDS["pipeline"]),
            "secure": False,
        }
    return {
        "host": str(host),
        "port": int(_secret("http_port") or 8123),
        "username": str(_secret("username") or "pipeline"),
        "password": str(_secret("password") or ""),
        "secure": bool(int(_secret("secure") or 0)),
    }


def pipeline_client() -> Any:
    """clickhouse-connect as the pipeline user."""
    return clickhouse_connect.get_client(
        **pipeline_credentials(), connect_timeout=10, send_receive_timeout=600
    )
