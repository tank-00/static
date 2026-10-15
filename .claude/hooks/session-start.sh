#!/bin/bash
# SessionStart hook: install backend (uv) and frontend (npm) dependencies for Claude Code cloud sessions
# so `make check` works immediately. Idempotent and non-interactive.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

export UV_SYSTEM_CERTS=1
unset UV_NATIVE_TLS || true

# Backend: sync the locked environment (incl. optional AI extra and dev group).
uv --directory backend sync --all-extras --locked --quiet

# Frontend: install from the lockfile only when node_modules is missing or older than package-lock.json
# (npm ci never rewrites the lockfile, so no lockfile churn leaks into commits).
if [ ! -d frontend/node_modules ] || [ frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]; then
  npm --prefix frontend ci --no-audit --no-fund --loglevel=error
fi

# Persist uv TLS setting for the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export UV_SYSTEM_CERTS=1' >> "$CLAUDE_ENV_FILE"
fi
