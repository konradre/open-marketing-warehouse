#!/bin/bash
# Runs once, on first start of the clickhouse container (the image executes /docker-entrypoint-initdb.d/*
# while the server listens on localhost only). Applies sql/00_*.sql in order, substituting the user
# passwords from the environment. Re-run on a live container with `make bootstrap`.
set -euo pipefail

: "${OMW_PIPELINE_PASSWORD:?OMW_PIPELINE_PASSWORD must be set}"
: "${OMW_AGENT_PASSWORD:?OMW_AGENT_PASSWORD must be set}"
SQL_DIR="${OMW_SQL_DIR:-/omw/sql}"
HOST="${OMW_BOOTSTRAP_HOST:-127.0.0.1}"

client=(clickhouse-client --multiquery --host "$HOST" --user "${CLICKHOUSE_USER:-default}" --password "${CLICKHOUSE_PASSWORD:-}")

shopt -s nullglob
files=("$SQL_DIR"/00_*.sql)
if [ "${#files[@]}" -eq 0 ]; then
  echo "omw bootstrap: no $SQL_DIR/00_*.sql files found" >&2
  exit 1
fi

for f in "${files[@]}"; do
  echo "omw bootstrap: applying $f"
  sed -e "s|__OMW_PIPELINE_PASSWORD__|${OMW_PIPELINE_PASSWORD}|g" \
      -e "s|__OMW_AGENT_PASSWORD__|${OMW_AGENT_PASSWORD}|g" "$f" | "${client[@]}"
done
echo "omw bootstrap: done"
