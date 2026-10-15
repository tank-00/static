#!/usr/bin/env bash
# Start the single-process app on a fresh, seeded temporary workspace and run Playwright against it.
set -euo pipefail
PORT="${1:-8765}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$(mktemp -d)"
export TARIFF_DATA_DIR="$DATA_DIR"
export TARIFF_SERVE_FRONTEND_DIST="$ROOT/frontend/dist"
export UV_SYSTEM_CERTS=1
unset UV_NATIVE_TLS || true

uv --directory "$ROOT/backend" run python -m tariff_app.seed --reset
uv --directory "$ROOT/backend" run uvicorn tariff_app.main:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true; rm -rf "$DATA_DIR"' EXIT

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/api/v1/meta" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

E2E_BASE_URL="http://127.0.0.1:$PORT" npm --prefix "$ROOT/frontend" run e2e
