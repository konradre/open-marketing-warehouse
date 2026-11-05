#!/bin/bash
# Writes an env file for the optional profiles with throwaway random secrets (CI, or a first local boot):
#   deploy/profiles-env.sh .env
# Every other key keeps its .env.example value. Never use these secrets for a deployment you keep.
set -euo pipefail
target="${1:-.env}"
here="$(cd "$(dirname "$0")/.." && pwd)"
cp "$here/.env.example" "$target"
secret() { openssl rand -hex "$1"; }
set_key() {  # set_key <name> <value>
  if grep -qE "^$1=" "$target"; then
    sed -i.bak -E "s|^$1=.*$|$1=$2|" "$target" && rm -f "$target.bak"
  else
    echo "$1=$2" >>"$target"
  fi
}
for name in LIBRECHAT_JWT_SECRET LIBRECHAT_JWT_REFRESH_SECRET LIBRECHAT_CREDS_KEY LANGFUSE_NEXTAUTH_SECRET \
            LANGFUSE_SALT LANGFUSE_ENCRYPTION_KEY; do
  set_key "$name" "$(secret 32)"
done
set_key LIBRECHAT_CREDS_IV "$(secret 16)"
for name in LANGFUSE_POSTGRES_PASSWORD LANGFUSE_REDIS_PASSWORD LANGFUSE_MINIO_PASSWORD LANGFUSE_CLICKHOUSE_PASSWORD; do
  set_key "$name" "$(secret 12)"
done
echo "profiles-env: wrote $target with throwaway secrets"
