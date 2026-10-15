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
uv --directory backend sync --all-extras --quiet

# Frontend: npm install (not ci) so the container cache can reuse node_modules.
npm --prefix frontend install --no-audit --no-fund --loglevel=error

# Persist uv TLS setting for the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export UV_SYSTEM_CERTS=1' >> "$CLAUDE_ENV_FILE"
fi
