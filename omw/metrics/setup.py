"""Create the metric layer's warehouse objects (the campaign key mapping table) as the pipeline user.

uv run python -m omw.metrics.setup
"""

from __future__ import annotations

import logging
import sys

import clickhouse_connect
import dlt

from omw.pipelines.marts import SQL_DIR, statements

log = logging.getLogger(__name__)
METRIC_LAYER_SQL = SQL_DIR / "60_metric_layer.sql"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    credentials = dlt.secrets["destination.clickhouse.credentials"]
    client = clickhouse_connect.get_client(
        host=credentials["host"],
        port=int(credentials.get("http_port", 8123)),
        username=credentials["username"],
        password=credentials["password"],
        secure=bool(credentials.get("secure", 0)),
    )
    for statement in statements(METRIC_LAYER_SQL.read_text()):
        client.command(statement)
    log.info("applied %s", METRIC_LAYER_SQL.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
