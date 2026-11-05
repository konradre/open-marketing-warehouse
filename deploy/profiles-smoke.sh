#!/bin/bash
# Boots the optional compose profiles (chat: LibreChat; observability: Langfuse) with the given env file and
# proves they answer over HTTP, then leaves them running. CI calls it with throwaway secrets; locally:
#   deploy/profiles-smoke.sh .env
# Exit 1 with the failing service's logs when a service never becomes healthy or an endpoint never answers.
set -euo pipefail
env_file="${1:-.env}"
compose=(docker compose --env-file "$env_file" -f "$(dirname "$0")/docker-compose.yml" --profile chat --profile observability)
chat_port="$(grep -E '^OMW_CHAT_PORT=' "$env_file" | tail -n 1 | cut -d= -f2)"
langfuse_port="$(grep -E '^OMW_LANGFUSE_PORT=' "$env_file" | tail -n 1 | cut -d= -f2)"
chat_port="${chat_port:-3080}"
langfuse_port="${langfuse_port:-3000}"

wait_http() {  # wait_http <name> <url> <attempts>
  local name="$1" url="$2" attempts="$3" i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS -m 5 "$url" >/dev/null 2>&1; then
      echo "profiles-smoke: $name answers at $url"
      return 0
    fi
    sleep 5
  done
  echo "profiles-smoke: $name never answered at $url" >&2
  return 1
}

"${compose[@]}" up -d --wait --wait-timeout 600
# LibreChat and Langfuse have no compose healthcheck: wait for their HTTP health endpoints instead.
wait_http "LibreChat" "http://127.0.0.1:${chat_port}/health" 60
wait_http "Langfuse" "http://127.0.0.1:${langfuse_port}/api/public/health" 120
# LibreChat must have loaded librechat.yaml and listed the warehouse MCP server's tools (DESIGN.md §6).
if ! "${compose[@]}" logs --no-color librechat | grep -qE "\[MCP\]\[warehouse\] Tools: [a-z_]+"; then
  echo "profiles-smoke: LibreChat did not list the warehouse MCP server's tools" >&2
  "${compose[@]}" logs --no-color --tail 100 librechat >&2
  exit 1
fi
echo "profiles-smoke: chat and observability profiles are up"
