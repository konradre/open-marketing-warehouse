---
name: dlt-custom-source
description: Use when building or changing a dlt source in omw/sources (Search Console, LinkedIn Ads, Twenty, Google Ads reports) or wrapping a verified source. Covers resource design, incremental loading, restatement windows, backfill, secrets and fixture tests.
---

# Building a dlt source in this repo

Read the dlt docs for the current API (dlthub.com/docs) before writing code; check the installed version with
`uv run python -c "import dlt; print(dlt.__version__)"`.

## Shape

- One module per source in `omw/sources/<source>.py`, exposing a `@dlt.source` function that takes config and
  credentials as arguments (resolved from `dlt.config` / `dlt.secrets`), and returns one `@dlt.resource` per
  report grain or entity.
- Prefer dlt's `rest_api` source (declarative client, paginator, auth) for plain REST APIs. Write a
  hand-coded resource only where the API needs it (streaming GAQL, async report jobs).
- Every row of a report resource carries `source`, `report`, `date` and the account id. Name resources after
  their grain (`search_console_queries`, not `data`).

## Loading

- Entities: `write_disposition="merge"` with a `primary_key`.
- Reports with restatement (Meta up to 28 days, Google Ads conversion lag, Search Console 2–3 day lag):
  re-fetch a rolling window each run and replace that `(source, report, window)` slice. Never append a
  second copy of a day.
- Immutable event-like objects: `write_disposition="append"` with `dlt.sources.incremental` on a cursor.

## Backfill

- Oldest first, in 90-day chunks. Persist progress in dlt state. Stop at the first failed chunk and keep
  the completed ones; the next run resumes.

## Rate limits and errors

- Respect the platform's limits proactively where it exposes usage (see `omw/pacing/`); otherwise use
  bounded retries with backoff on 429/5xx only. Never retry an auth error.
- A run that loads zero rows for a window that should have data is a failure, not a success: record it in
  `ops`.

## Secrets

- Credentials live in `.dlt/secrets.toml` (never committed). Add every new key to
  `.dlt/secrets.toml.example` with a placeholder and a comment on where to obtain it.

## Tests

- Unit tests run the source against recorded JSON fixtures in `tests/fixtures/<source>/` (synthetic IDs and
  amounts), by patching the HTTP client or using `requests-mock`/`responses`. Cover pagination, an empty page,
  a rate-limit response, and a restated day being replaced rather than duplicated.
- An integration test loads into ClickHouse in Docker and checks the `marts` view.
