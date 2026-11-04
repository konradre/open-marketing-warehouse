#!/bin/bash
# Fails when any of the named variables is empty in the given env file. Compose cannot require a variable
# only for one profile, so the Makefile runs this before starting the `chat` and `observability` profiles.
#   deploy/require-env.sh .env LANGFUSE_SALT LANGFUSE_NEXTAUTH_SECRET ...
set -eu
file="$1"; shift
if [ ! -f "$file" ]; then
  echo "require-env: $file not found; copy .env.example to .env and fill in the secrets" >&2
  exit 1
fi
missing=()
for name in "$@"; do
  value="$(grep -E "^${name}=" "$file" | tail -n 1 | cut -d= -f2- | tr -d '"'"'" || true)"
  if [ -z "$value" ]; then
    missing+=("$name")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "require-env: set these in $file first: ${missing[*]}" >&2
  exit 1
fi
