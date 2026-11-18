#!/bin/bash
# Builds the scheduler image and proves it does its job, against a fresh ClickHouse and with no credentials:
# one tick of every job in fixture mode (sync, checks, backup, restore drill), then the same loop as the
# long-running service until its health check passes, then a clean stop. CI runs this on every push.
#   deploy/scheduler-smoke.sh .env
set -euo pipefail
env_file="${1:-.env}"
fixtures=/var/lib/omw/demo
compose=(docker compose --env-file "$env_file" -f deploy/docker-compose.yml --profile scheduler)
run=("${compose[@]}" run --rm -T scheduler)

"${compose[@]}" build scheduler
"${compose[@]}" up -d --wait clickhouse

echo "scheduler-smoke: one tick of every job, in fixture mode"
"${run[@]}" demo generate --out "$fixtures" --days 60
"${run[@]}" schedule run --once --fixtures "$fixtures"

listed="$("${run[@]}" schedule list --fixtures "$fixtures")"
echo "$listed"
for job in search_console meta_ads_weekly google_ads ga4 stripe linkedin_ads twenty backup backup_verify; do
  if ! grep -Eq "^${job} .* ok " <<<"$listed"; then
    echo "scheduler-smoke: ${job} did not end ok" >&2
    exit 1
  fi
done
grep -Eq "^meta_ads_daily .* skipped .*covered by meta_ads_weekly" <<<"$listed" || {
  echo "scheduler-smoke: the weekly Meta reconcile did not cover the daily re-pull" >&2
  exit 1
}

backups="$("${run[@]}" backup list)"
echo "$backups"
grep -Eq "^omw-[0-9]{8}T[0-9]{6}Z\.zip .* verified +present$" <<<"$backups" || {
  echo "scheduler-smoke: no backup that is both verified by the restore drill and present on the disk" >&2
  exit 1
}

echo "scheduler-smoke: a second tick has nothing left to do"
again="$("${run[@]}" schedule run --once --fixtures "$fixtures" 2>&1)"
if grep -q "starting for slot" <<<"$again"; then
  echo "$again"
  echo "scheduler-smoke: a slot ran twice" >&2
  exit 1
fi

echo "scheduler-smoke: the loop as a service, until it is healthy"
OMW_SCHEDULER_COMMAND="schedule run --fixtures $fixtures" "${compose[@]}" up -d --wait scheduler
"${compose[@]}" stop scheduler
logs="$("${compose[@]}" logs --no-color scheduler)"
grep -q "scheduler started in fixtures mode with 10 jobs" <<<"$logs" || { echo "$logs"; exit 1; }
grep -q "scheduler stopped (signal 15)" <<<"$logs" || {
  echo "$logs"
  echo "scheduler-smoke: the loop did not stop cleanly on SIGTERM" >&2
  exit 1
}
echo "scheduler-smoke: ok"
