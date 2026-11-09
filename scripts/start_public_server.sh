#!/usr/bin/env bash
set -euo pipefail
PUBLIC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PUBLIC_ROOT"
export SUPPLY_CHAIN_PLATFORM_MODE="${SUPPLY_CHAIN_PLATFORM_MODE:-visitor}"
case "$SUPPLY_CHAIN_PLATFORM_MODE" in
 visitor) ;;
 operator) PUBLIC_OPERATOR_TOKEN="${SUPPLY_CHAIN_OPERATOR_TOKEN:-}"; if [[ ${#PUBLIC_OPERATOR_TOKEN} -lt 32 ]]; then echo 'Configure an operator token of at least 32 characters.' >&2; exit 1; fi ;;
 *) echo 'Mode must be visitor or operator.' >&2; exit 1 ;;
esac
export SUPPLY_CHAIN_RUNTIME_ROOT="${SUPPLY_CHAIN_RUNTIME_ROOT:-$PUBLIC_ROOT/agent/runtime}"
mkdir -p "$SUPPLY_CHAIN_RUNTIME_ROOT"
export SIMULATION_OPERATIONS_SQLITE_PATH="${SIMULATION_OPERATIONS_SQLITE_PATH:-$SUPPLY_CHAIN_RUNTIME_ROOT/operations.sqlite}"
export SIMULATION_SQL_MIRROR_PATH="$SIMULATION_OPERATIONS_SQLITE_PATH"
export SUPPLY_CHAIN_FRONTEND_DIST="${SUPPLY_CHAIN_FRONTEND_DIST:-$PUBLIC_ROOT/visualization/dist}"
if [[ ! -f "$SUPPLY_CHAIN_FRONTEND_DIST/index.html" ]]; then
  echo 'Build the viewer first: npm --prefix visualization ci && npm --prefix visualization run build' >&2
  exit 1
fi
PUBLIC_PYTHON="${PYTHON_BIN:-$PUBLIC_ROOT/.venv/bin/python}"
PUBLIC_PORT="${BACKEND_PORT:-8000}"
export SIMULATION_API_BASE_URL="${SIMULATION_API_BASE_URL:-http://127.0.0.1:$PUBLIC_PORT}"
exec "$PUBLIC_PYTHON" -m uvicorn simulate.simulation_server:app --host "${BACKEND_HOST:-127.0.0.1}" --port "$PUBLIC_PORT"
