#!/bin/sh
set -eu

uv sync --locked
digest=$(sha256sum package-lock.json package.json)
stamp=node_modules/.texsys-dependencies
if [ ! -x node_modules/.bin/vite ] || [ "$digest" != "$(cat "$stamp" 2>/dev/null || true)" ]; then
  npm ci --ignore-scripts --no-audit --no-fund
  printf '%s\n' "$digest" > "$stamp"
fi

exec uv run --locked --no-sync texsys studio -p examples/hvac-dynamic-station --hmr --host 0.0.0.0
