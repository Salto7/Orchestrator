#!/bin/bash
# Sandbox entrypoint: record image base commands once at container start.
set -euo pipefail
BASE_FILE=/etc/orchestrator/base-commands
mkdir -p /etc/orchestrator
if [[ ! -s "$BASE_FILE" ]]; then
  {
    command -v compgen >/dev/null 2>&1 && compgen -c || true
    ls /bin /usr/bin /usr/local/bin 2>/dev/null | xargs -n1 basename || true
  } | sort -u >"$BASE_FILE"
fi
exec "$@"
