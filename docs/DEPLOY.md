# Deploying on one host

Everything runs from `deploy/docker-compose.yml` on a single machine: ClickHouse, `mcp-clickhouse` (the
agent's SQL path) and, in the `scheduler` profile, one process that pulls every platform on its cadence
(`DESIGN.md` §1). The host needs Docker with Compose v2 and `make`. Nothing else: every `omw` command below
runs inside the scheduler image.

| Service | Profile | What it does |
|---|---|---|
| `clickhouse` | always | The warehouse. Ports on `127.0.0.1` only. |
| `mcp-clickhouse` | always | The agent's SQL path, connected as the `agent` user. |
| `scheduler` | `scheduler` | `omw schedule run`: each source on its cadence, the post-sync checks after every sync, the failure hook, the nightly backup and its weekly restore drill. |

## Local

A rehearsal with synthetic data and no credentials. It runs the same container, pipelines, checks, hook
and backup as production; only the platform answers come from fixture files.

```bash
cp .env.example .env
make up                                  # ClickHouse + mcp-clickhouse
make scheduler-build                     # the scheduler image, from this checkout
docker compose --env-file .env -f deploy/docker-compose.yml --profile scheduler \
  run --rm scheduler demo generate --out /var/lib/omw/demo
```

Set `OMW_SCHEDULER_COMMAND=schedule run --fixtures /var/lib/omw/demo` in `.env`, then:

```bash
make scheduler                           # starts the loop; on a first start every job is due at once
make scheduler-logs                      # watch it: sync, checks, sync, checks, ..., backup, restore drill
make schedule                            # every job, its next slot, how its last run ended
make backup-list
```

`make scheduler-smoke` does all of this in one go on an empty stack and checks the result (every job ok,
a backup that passed its restore drill, the service healthy, a clean stop); CI runs it on every push.

Fixture mode refuses a warehouse that holds anything the demo did not load, so it cannot overwrite real
data. Before going live, remove the synthetic data (the same `docker compose ... run --rm scheduler`
with `demo drop`) and set `OMW_SCHEDULER_COMMAND=schedule run` again.

## VPS

1. **Host.** A Linux machine with Docker Engine and Compose v2. ClickHouse is the memory user; its
   configuration keeps it within 80% of what the host gives the container. Clone the repository, for
   example to `/srv/omw` (the systemd units below assume that path).
2. **Stack secrets.** `cp .env.example .env`, then replace every `*-dev-password` and the MCP token with
   your own (`openssl rand -hex 24`), and `chmod 600 .env`.
3. **Platform credentials.** `cp .dlt/secrets.toml.example .dlt/secrets.toml` and fill in the sources you
   use (see [Secrets](#secrets)). Turn the others off in `[schedule]` (see [The schedule](#the-schedule)).
4. **Start the warehouse.** `make up`. On first start ClickHouse creates the databases, the `pipeline` and
   `agent` users, the agent's profile, quota and grants from `sql/00_*.sql`.
5. **Test the hook.** `docker compose --env-file .env -f deploy/docker-compose.yml --profile scheduler run
   --rm scheduler notify test` sends a test alert through every channel in `[alerts]`.
6. **First loads.** The scheduler only re-fetches each source's rolling window. Load history once by hand,
   in the same image (the commands are in the README, per source):
   ```bash
   RUN="docker compose --env-file .env -f deploy/docker-compose.yml --profile scheduler run --rm --entrypoint python scheduler"
   $RUN -m omw.pipelines.search_console backfill      # the 16 months Google keeps, resumable
   $RUN -m omw.pipelines.ga4 daily --days 400
   ```
7. **Start the scheduler.** `make scheduler`, then `make schedule`. On a first start every job is due, so
   it runs them all once, in order, and from then on at their slots.

Every published port is bound to `127.0.0.1`. To reach `mcp-clickhouse` from another machine use an SSH
tunnel (`ssh -L 8000:127.0.0.1:8000 <host>`) or put your own TLS reverse proxy in front of it; the bearer
token (`OMW_MCP_AUTH_TOKEN`) is the only authentication it has.

## Secrets

| What | Where | Read by |
|---|---|---|
| ClickHouse passwords, MCP token, ports, backup volume | `.env` | Compose; the containers' environment |
| Platform credentials, alert channels | `.dlt/secrets.toml` | The pipelines and the scheduler (mounted read-only at `/app/.dlt`) |
| Service-account JSON keys (GA4, Search Console) | `.dlt/keys/*.json` | Named by `credentials_path` in `secrets.toml` |
| Schedule, freshness budgets, other non-secret settings | `.dlt/config.toml` | The same processes; committed, edit it in place |

None of the first three is ever committed (`.gitignore`), and none is copied into the scheduler image: the
build context admits only the code (`.dockerignore`), and `.dlt/` is mounted at run time.

- Write `credentials_path` relative to the repository root (`.dlt/keys/ga4.json`): the same path then works
  on the host and in the container.
- The scheduler runs as **uid 101** (the ClickHouse container's user, so it can prune the backup files that
  server writes). Give that uid read access to the secrets and nothing more:
  ```bash
  sudo chgrp -R 101 .dlt && chmod 750 .dlt && chmod 640 .dlt/secrets.toml
  mkdir -p .dlt/keys && sudo chgrp 101 .dlt/keys && chmod 750 .dlt/keys && chmod 640 .dlt/keys/*.json
  ```
  The scheduler refuses to start with a clear message when it cannot read `secrets.toml`.
- `[destination.clickhouse.credentials]` in `secrets.toml` is for commands run on the host (`uv run omw
  check`). The scheduler container takes the host name, ports and the `pipeline` password from `.env`
  instead, so the two never drift apart.
- **Rotating a ClickHouse password:** change it in `.env`, `make up` (recreates the containers with the new
  value), `make bootstrap` (applies the `pipeline` and `agent` passwords to the server), `make scheduler`.
- The scheduler never holds the ClickHouse `admin` password. Backups and restores run as `pipeline`.

## The schedule

One job per source and cadence, then the backup. Times are UTC whatever the host's time zone.

| Job | Default | What it runs |
|---|---|---|
| `search_console` | `03:00` | Re-fetches the rolling 10 days and replaces them. |
| `meta_ads_weekly` | `sun 03:15` | The 28-day reconcile (`DESIGN.md` §5). |
| `meta_ads_daily` | `03:15`, not on the weekly's day | The 7-day re-pull. |
| `google_ads` | `04:00` | Re-fetches the last 30 days. |
| `ga4` | `04:15` | Re-fetches the last 7 days. |
| `stripe` | `04:30` | Merges customers, subscriptions and recent invoices; appends balance transactions. |
| `linkedin_ads` | `04:45` | Re-fetches the last 7 days. |
| `twenty` | `05:00` | Everything changed since the last run. |
| `backup` | `06:00` | Backs up `raw`, `marts` and `ops`; keeps the newest 7. |
| `backup_verify` | `sun 06:30` | Restore drill of the newest backup. |
| `checks` | `*:50` (every hour) | Every check of every scheduled source. |

Change it in `[schedule]` of `.dlt/config.toml`, then `make scheduler-restart` (the loop reads it at start):

```toml
[schedule]
meta_ads_weekly = "sat 02:00"     # "HH:MM" daily, "sun HH:MM", "mon,thu HH:MM", "*:MM" every hour
linkedin_ads = "off"              # not synced and not checked: one line says "we do not use this source"
```

How the loop behaves:

- **One job at a time**, in the order of the table. A job that runs long delays the next one; it never
  overlaps it.
- **After every sync, `omw check` for that source**: freshness, over-count, row-count and expiry, written to
  `ops.check_results`. The checks also run after a sync that failed, so the freshness verdict keeps moving.
- **Once an hour, every check of every scheduled source** (the `checks` job). A sync checks its source
  right after it ran; a source whose last sync was 24 hours ago passes then and is past its 30-hour budget
  six hours later. The hourly run is what turns it stale in `ops.freshness`, and with it in the agent's
  answers. It alerts when a check starts failing, not every hour it keeps failing.
- **A slot runs once.** Attempts are recorded in `ops.sync_runs`; a restart neither repeats a finished slot
  nor forgets one that passed while the host was down. Several missed days are caught up with one run.
- **A job that failed is tried again**, for the same slot: `[scheduler] retries` (2) more times,
  `retry_minutes` (30) apart, because most failures of a pull are an API having a bad minute. The hook
  stays quiet until the last attempt has failed too, and every attempt is in `ops.sync_runs`. A failed
  post-sync check is not retried: the data loaded, and loading it again gives the same verdict. After the
  last attempt the slot is over; `make job JOB=stripe` runs the job by hand once the cause is fixed.
- **Each job is its own process** (`omw sync <job>`). A job still running after `[scheduler]
  timeout_minutes` (360) is stopped and reported.
- On the weekly reconcile's day the daily Meta re-pull is skipped: the 28-day window contains it.

`omw sync <job>` is the unit everything else is built from. Its exit code is `0`, `1` when the job itself
failed, or `3` when it loaded but a post-sync check failed; in both failure cases it has already fired the
hook and written the run. `omw schedule run --once` runs whatever is due and exits non-zero when any of it
failed, for a host that prefers one cron line. It fires the hook on every failure (nobody promises it a
later attempt), and the next invocation retries a failed slot if the pause has passed. systemd timers do
not retry: a failed unit waits for its next slot.

```sql
-- as the pipeline or admin user: the last runs
SELECT started_at, job, status, rows, checks_failed, detail
FROM ops.sync_runs ORDER BY started_at DESC LIMIT 20;
```

## systemd timers instead

The loop and the timers are two triggers for the same `omw sync <job>`; use one, never both.
`deploy/systemd/` holds the default schedule as units: a template service and one timer per job, with
`Persistent=true` (a slot missed while the host was down runs at boot) and a shared lock (one job at a
time). Each run happens in the scheduler image, so the image must exist (`make scheduler-build`) while the
`scheduler` service itself stays stopped.

```bash
make up && make scheduler-build
sudo cp deploy/systemd/omw-sync@.service deploy/systemd/omw-sync-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
for timer in deploy/systemd/omw-sync-*.timer; do sudo systemctl enable --now "$(basename "$timer")"; done
systemctl list-timers 'omw-sync-*'
journalctl -u 'omw-sync@*' --since today
```

The units are rendered from `[schedule]`. After changing it, or for a checkout that is not at `/srv/omw`,
render them again and copy them over:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml --profile scheduler \
  run --rm scheduler schedule systemd --workdir /opt/omw     # prints the units
uv run omw schedule systemd --output deploy/systemd          # or, with uv on the host: make systemd-units
```

A unit that fails shows in `systemctl --failed`; `omw sync` has already sent the alert by then. For a host
install without the image, render with `--command "/usr/local/bin/uv run omw"`; the service user then needs
the backup directory (`OMW_BACKUP_DIR`) to prune it.

## Alerts

`[alerts]` in `.dlt/secrets.toml` (or `ALERTS__WEBHOOK_URL` and friends in the environment) names the
channels. Both can be set; each alert goes to every one.

```toml
[alerts]
webhook_url = "https://hooks.example.com/services/..."
email_to = "ops@example.com"
smtp_host = "smtp.example.com"
smtp_port = 587                # 465: TLS from the start; any other port: STARTTLS when offered
smtp_username = "omw@example.com"
smtp_password = "..."
```

The webhook receives a JSON `POST`. `text` is one readable message, which Slack-compatible incoming
webhooks display as it is; the other fields are for anything that wants to route on them:

```json
{"kind": "check_failed", "severity": "error", "job": "ga4", "source": "ga4",
 "summary": "1 post-sync check(s) failed after the sync",
 "details": ["FAIL ga4/overcount/channels: 3 of 28 groups exceed core by more than 1% on sessions"],
 "host": "warehouse-1", "at": "2026-11-18T04:16:02+00:00",
 "text": "[omw] ERROR ga4 on warehouse-1: 1 post-sync check(s) failed after the sync\n- FAIL ga4/overcount/..."}
```

| `kind` | When |
|---|---|
| `sync_failed` | A sync raised: a dead token, an API error, the warehouse unreachable. Exit 1. Under the loop: after the last retry. |
| `check_failed` | The sync loaded, a post-sync check failed (stale, over-count, a collapse to zero rows, an expired credential). Exit 3. Also from the hourly `checks` job, once, when a check starts failing between syncs. |
| `expiry_warning` | A credential or LinkedIn's API version is inside its warning window. The job still succeeds. |
| `backup_failed` | The backup or the restore drill failed. |
| `job_crashed` | A job's process died or ran out of time before it could report itself, on its last attempt. |
| `scheduler_error` | The loop cannot read its run log (the warehouse is down). Sent once per outage. |

A hook that cannot be reached is logged and never changes a job's exit code. Nothing alerts when the
scheduler itself is gone: the container restarts by itself (`restart: unless-stopped`) and has a health
check (`docker compose ps`), and `ops.freshness.checked_at` stops moving, which is what the agent sees.
Watch one of the two from outside the host if that matters to you.

## Credential expiry

Most platforms do not say when a credential ends, so the date is configuration: `token_expires_at` (an ISO
date) in the source's section of `.dlt/secrets.toml`, written down when the credential is created. The
`expiry` check warns `[checks] expiry_warn_days` (14) days ahead, on every sync of that source, and fails
from the day after. A source without a date has no expiry row.

| Source | Credential | How long it lasts |
|---|---|---|
| `meta_ads` | System-user token | No end unless it was generated with one (60 days). Its sync also refuses to run once it has lapsed. |
| `google_ads` | OAuth refresh token | No end for a published OAuth app; 7 days while the app's consent screen is in testing. |
| `ga4`, `search_console` | Service-account key | No end unless your organisation enforces key rotation. |
| `stripe` | Restricted API key | No end unless one was set, or the key is rolled. |
| `linkedin_ads` | OAuth refresh token | 365 days from the consent. Set the date. |
| `twenty` | API key | Whatever was chosen when the key was created. |

LinkedIn's pinned `LinkedIn-Version` needs no date: the same check warns three months before the version
leaves support and fails after it.

## Backup and restore

Backups use ClickHouse's own `BACKUP ... TO Disk`: one zip per backup holding `raw`, `marts` and `ops`.
That is all the state there is. dlt's incremental cursors and backfill progress live in `raw`; users,
grants, the profile and the quota are re-created from `sql/00_*.sql` and `.env` by the bootstrap. **Not in
the backup:** `.env`, `.dlt/secrets.toml` and `.dlt/keys/`. Keep those in a password manager.

- The `backup` job writes `omw-<UTC time>.zip` every night and then deletes all but the newest
  `[backup] keep` (7). A backup with any other name (`backup create --name before-upgrade.zip`) is never
  pruned.
- Each backup carries a manifest of its own row counts (`ops.backup_manifest`), taken just before it starts.
- `OMW_BACKUP_VOLUME` in `.env` is where the files live: a Docker volume by default, or an absolute host
  path. **A backup on the same disk as the data is not a backup yet.** Copy the files off the host, with
  whatever you already use:
  ```bash
  docker compose --env-file .env -f deploy/docker-compose.yml cp clickhouse:/backups/omw-20261118T060000Z.zip .
  ```
  With a host path (`OMW_BACKUP_VOLUME=/srv/omw-backups`, owned by uid 101), point `rsync`, `restic` or
  `rclone` at the directory instead.

```bash
make backup                 # one now
make backup-list            # every backup: rows, size, last drill, whether the file is still there
make backup-prune
```

### Restore drill

`make backup-verify` (the weekly `backup_verify` job) restores the newest backup into three scratch
databases (`omw_drill_raw`, `omw_drill_marts`, `omw_drill_ops`), compares every table's row count with the
manifest inside the backup, and drops the scratch databases. The live data is never touched, and a failure
fires the hook. It proves the file restores and holds what it should. It checks views for presence only
(ClickHouse 25.8 drops table aliases from a view's query when it restores it under another database name;
a real restore keeps the names), and it says nothing about your off-host copy.

For that, restore the copy on a second stack once in a while. This is the whole disaster-recovery path, on
any machine with the repository and Docker:

```bash
cat > /tmp/drill.env <<'EOF'
COMPOSE_PROJECT_NAME=omw-drill
OMW_CLICKHOUSE_HTTP_PORT=28123
OMW_CLICKHOUSE_NATIVE_PORT=29000
OMW_MCP_PORT=28000
EOF
DRILL="docker compose --env-file /tmp/drill.env -f deploy/docker-compose.yml --profile scheduler"
$DRILL build scheduler
$DRILL up -d --wait clickhouse                                   # an empty warehouse, bootstrapped
$DRILL cp ./omw-20261118T060000Z.zip clickhouse:/backups/
$DRILL exec clickhouse chown clickhouse:clickhouse /backups/omw-20261118T060000Z.zip
$DRILL run --rm scheduler backup restore omw-20261118T060000Z.zip
$DRILL run --rm scheduler check                                  # freshness says how old the data is
$DRILL run --rm scheduler schedule list                          # the run log came back with it
$DRILL down -v                                                   # removes the drill stack and its volumes
```

### Restore

```bash
docker compose --env-file .env -f deploy/docker-compose.yml --profile scheduler stop scheduler
make backup-list
make restore BACKUP=omw-20261118T060000Z.zip FORCE=1
make scheduler
```

`restore` runs the drill first and leaves the live data alone when it fails. Without `FORCE=1` it refuses a
warehouse that holds anything. With it, every table and view in `raw`, `marts` and `ops` is dropped and
replaced by the backup. It then removes the scheduler's local dlt pipeline state, so the next sync resumes
from the restored cursors rather than from a position the warehouse no longer holds. On a new host: steps
1 to 4 of [VPS](#vps), `make scheduler-build`, copy the file into the backup directory as in the drill, then
`make restore` without `FORCE`.

After a restore the next syncs re-fetch their rolling windows (7 to 30 days), and Stripe and Twenty resume
from the restored cursor, so the days since the backup fill in by themselves. Anything older than a
source's window that was loaded after the backup needs that source's first-load command again.

## Logs

- **Containers:** Docker's `json-file` driver with rotation, 5 files of 10 MB per service
  (`x-logging` in the compose file). `make scheduler-logs` follows the scheduler.
- **ClickHouse server log files:** 5 files of 50 MB each (`deploy/clickhouse/config.d/omw.xml`).
- **ClickHouse system log tables:** `query_log` and `part_log` keep 30 days; the high-volume diagnostics
  (`trace_log`, `metric_log`, `asynchronous_metric_log`, `text_log` and the like) are off.
- **`ops` tables:** `ops.check_results`, `ops.sync_runs`, `ops.backup_log` and `ops.backup_manifest` keep
  400 days.
- **systemd timers:** the journal. Cap it with `SystemMaxUse=` in `/etc/systemd/journald.conf`.

## Upgrade

```bash
make backup                              # first
git pull
make scheduler-build                     # the image is built from the checkout
make up                                  # recreates containers whose image or configuration changed
make bootstrap                           # re-applies sql/00_*.sql: users, profile, quota, grants
make scheduler && make schedule
```

- `marts` views are re-created by each pipeline after its next successful sync, so a changed view
  definition arrives with the next run of that source (`make job JOB=<job>` to have it now).
- The ClickHouse version is pinned in the compose file and moves with the repository. Back up before a pull
  that changes it: an upgrade in place is supported, a downgrade is not.
- **`ops.check_results` created before its 400-day TTL existed** keeps everything until you add it, once,
  as the `pipeline` or `admin` user: `ALTER TABLE ops.check_results MODIFY TTL checked_at + INTERVAL 400 DAY`.
- **Coming from a stack started before this guide existed:** `make bootstrap` is required once (the backup
  grants), and the server's old diagnostic tables stay on disk until you drop them, as the `admin` user:
  `DROP TABLE IF EXISTS system.trace_log`, and the same for `metric_log`, `asynchronous_metric_log`,
  `text_log` and `query_log_0`.
