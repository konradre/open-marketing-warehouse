# The scheduler image: the omw package with its pipelines, run by `omw schedule run` (the compose service
# `scheduler`) or one job at a time by a systemd timer (`docker compose run --rm scheduler sync <job>`).
# Build context: the repo root (see deploy/docker-compose.yml). Nothing secret is copied in: .dlt/ is
# mounted at run time.
FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM python:3.12.16-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY omw ./omw
COPY sql ./sql
COPY metrics ./metrics
COPY .dlt/config.toml ./.dlt/config.toml
RUN uv sync --frozen --no-dev

# uid 101 is the ClickHouse image's user: the scheduler prunes the backup files that server writes.
RUN groupadd --gid 101 omw \
    && useradd --uid 101 --gid 101 --home-dir /var/lib/omw --create-home omw \
    && mkdir -p /var/lib/omw/.dlt /backups \
    && chown -R 101:101 /var/lib/omw /backups
USER 101:101
ENV HOME=/var/lib/omw \
    PATH=/app/.venv/bin:$PATH \
    OMW_BACKUP_DIR=/backups \
    OMW_HEARTBEAT=/tmp/omw-scheduler.heartbeat
# The heartbeat is the health check's evidence that this container's loop is alive, so it lives in the
# container, not on the state volume, where one left by an earlier container would still look fresh.

ENTRYPOINT ["omw"]
CMD ["schedule", "run"]
