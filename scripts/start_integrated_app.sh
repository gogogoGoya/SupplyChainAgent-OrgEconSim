#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/.env"

load_env_file() {
  local line key value
  [[ -f "$ENV_FILE" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    [[ -z "$key" || -z "$value" ]] && continue
    [[ "$key" == "SUPPLY_CHAIN_PLATFORM_MODE" || "$key" == "SUPPLY_CHAIN_OPERATOR_TOKEN" ]] && continue
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < "$ENV_FILE"
}

detect_python_bin() {
  local candidate fallback_candidate=""

  python_has_backend_dependencies() {
    "$1" -c 'import fastapi, pydantic, uvicorn' >/dev/null 2>&1
  }

  if [[ -n "${PYTHON_BIN:-}" ]]; then
    candidate="$PYTHON_BIN"
    if [[ "$candidate" != /* ]]; then
      candidate="$ROOT_DIR/$candidate"
    fi
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    echo "Ignoring stale PYTHON_BIN (not executable): $PYTHON_BIN" >&2
  fi

  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    candidate="$VIRTUAL_ENV/bin/python"
    if [[ -x "$candidate" ]]; then
      fallback_candidate="$candidate"
      if python_has_backend_dependencies "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  fi

  for candidate in \
    "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/venv/bin/python" \
    "$ROOT_DIR/env/bin/python" \
    "$ROOT_DIR/upgrade/bin/python"; do
    if [[ -x "$candidate" ]]; then
      [[ -n "$fallback_candidate" ]] || fallback_candidate="$candidate"
      if python_has_backend_dependencies "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done

  while IFS= read -r candidate; do
    candidate="${candidate%/pyvenv.cfg}/bin/python"
    if [[ -x "$candidate" ]]; then
      [[ -n "$fallback_candidate" ]] || fallback_candidate="$candidate"
      if python_has_backend_dependencies "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done < <(
    find "$ROOT_DIR" -mindepth 1 -maxdepth 4 \
      \( -path '*/.git' -o -path '*/node_modules' -o -path '*/workspace_jobs' \
         -o -path '*/workspace_multi' -o -path '*/simulation_runs' \) -prune \
      -o -name pyvenv.cfg -type f -print 2>/dev/null | sort
  )

  if command -v python3 >/dev/null 2>&1; then
    candidate="$(command -v python3)"
    [[ -n "$fallback_candidate" ]] || fallback_candidate="$candidate"
    if python_has_backend_dependencies "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi
  if command -v python >/dev/null 2>&1; then
    candidate="$(command -v python)"
    [[ -n "$fallback_candidate" ]] || fallback_candidate="$candidate"
    if python_has_backend_dependencies "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  if [[ -n "$fallback_candidate" ]]; then
    printf '%s\n' "$fallback_candidate"
    return 0
  fi

  echo "No Python interpreter found. Set PYTHON_BIN=/path/to/venv/bin/python and retry." >&2
  return 1
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local max_attempts="${3:-40}"
  local attempt status

  for ((attempt = 1; attempt <= max_attempts; attempt += 1)); do
    if [[ -n "${BACKEND_PID:-}" ]] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then return 1; fi
    if [[ -n "${FRONTEND_PID:-}" ]] && ! kill -0 "$FRONTEND_PID" 2>/dev/null; then return 1; fi
    status="$(
      "$PYTHON_BIN" - "$url" <<'PY' 2>/dev/null || true
import sys
import os
import json
from urllib.request import urlopen

try:
    with urlopen(sys.argv[1], timeout=1.5) as response:
        if sys.argv[1].endswith("/health"):
            health=json.load(response)
            print(response.status if health.get("instance_id")==os.environ.get("SUPPLY_CHAIN_INSTANCE_ID") else "000")
        else:
            print(response.status)
except Exception:
    print("000")
PY
    )"
    if [[ "$status" =~ ^[23] ]]; then
      return 0
    fi
    sleep 0.5
  done

  echo "Timed out waiting for ${label}: ${url}" >&2
  return 1
}

export SUPPLY_CHAIN_PLATFORM_MODE="${SUPPLY_CHAIN_PLATFORM_MODE:-visitor}"
case "$SUPPLY_CHAIN_PLATFORM_MODE" in
  visitor)
    echo "Visitor mode: existing results only; scripts/.env will not be loaded."
    ;;
  operator)
    operator_access_token="${SUPPLY_CHAIN_OPERATOR_TOKEN:-}"
    if [[ ${#operator_access_token} -lt 32 ]]; then
      echo "Set SUPPLY_CHAIN_OPERATOR_TOKEN to a private random token of at least 32 characters." >&2
      exit 1
    fi
    load_env_file
    ;;
  *)
    echo "SUPPLY_CHAIN_PLATFORM_MODE must be visitor or operator." >&2
    exit 1
    ;;
esac
PYTHON_BIN="$(detect_python_bin)"
export SUPPLY_CHAIN_INSTANCE_ID="$("$PYTHON_BIN" -c 'import uuid; print(uuid.uuid4().hex)')"
if [[ -z "${SUPPLY_CHAIN_PROJECT_ROOT:-}" ]]; then
  export SUPPLY_CHAIN_PROJECT_ROOT="$ROOT_DIR"
elif [[ "$SUPPLY_CHAIN_PROJECT_ROOT" != /* ]]; then
  export SUPPLY_CHAIN_PROJECT_ROOT="$ROOT_DIR/$SUPPLY_CHAIN_PROJECT_ROOT"
fi
if [[ ! -d "$SUPPLY_CHAIN_PROJECT_ROOT" ]]; then
  export SUPPLY_CHAIN_PROJECT_ROOT="$ROOT_DIR"
fi
if [[ -z "${SUPPLY_CHAIN_AGENT_ROOT:-}" ]]; then
  export SUPPLY_CHAIN_AGENT_ROOT="$SUPPLY_CHAIN_PROJECT_ROOT/agent"
elif [[ "$SUPPLY_CHAIN_AGENT_ROOT" != /* ]]; then
  export SUPPLY_CHAIN_AGENT_ROOT="$SUPPLY_CHAIN_PROJECT_ROOT/$SUPPLY_CHAIN_AGENT_ROOT"
fi
if [[ ! -d "$SUPPLY_CHAIN_AGENT_ROOT" ]]; then
  export SUPPLY_CHAIN_AGENT_ROOT="$SUPPLY_CHAIN_PROJECT_ROOT/agent"
fi
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
RUNTIME_DIR="${SUPPLY_CHAIN_RUNTIME_ROOT:-$SUPPLY_CHAIN_AGENT_ROOT/runtime}"
export SUPPLY_CHAIN_RUNTIME_ROOT="$RUNTIME_DIR"
export SUPPLY_CHAIN_RUNTIME_LOG_DIR="${SUPPLY_CHAIN_RUNTIME_LOG_DIR:-$RUNTIME_DIR/logs}"
DEFAULT_INTEGRATED_DB="$RUNTIME_DIR/operations.sqlite"
LEGACY_DB="$SUPPLY_CHAIN_AGENT_ROOT/workspace_jobs/_operations/supplychain_integrated.sqlite"
if [[ -f "$LEGACY_DB" ]]; then DEFAULT_INTEGRATED_DB="$LEGACY_DB"; fi
INTEGRATED_DB="${SIMULATION_SQL_MIRROR_PATH:-${SIMULATION_OPERATIONS_SQLITE_PATH:-$DEFAULT_INTEGRATED_DB}}"
if [[ "$INTEGRATED_DB" != /* ]]; then
  INTEGRATED_DB="$ROOT_DIR/$INTEGRATED_DB"
elif [[ ! -e "$INTEGRATED_DB" && "$INTEGRATED_DB" == */agent/* ]]; then
  INTEGRATED_DB="$SUPPLY_CHAIN_AGENT_ROOT/${INTEGRATED_DB#*/agent/}"
fi
mkdir -p "$(dirname "$INTEGRATED_DB")"

export SIMULATION_SQL_MIRROR_PATH="$INTEGRATED_DB"
export SIMULATION_OPERATIONS_SQLITE_PATH="$INTEGRATED_DB"
export SIMULATION_API_BASE_URL="${SIMULATION_API_BASE_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}"
export OPERATIONS_API_PROXY_TARGET="http://${BACKEND_HOST}:${BACKEND_PORT}"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

missing = [
    module
    for module in ("uvicorn", "fastapi", "pydantic")
    if importlib.util.find_spec(module) is None
]
if missing:
    print(
        "Missing Python backend dependencies: " + ", ".join(missing),
        file=sys.stderr,
    )
    print(
        "Set PYTHON_BIN to the project virtualenv Python or install backend dependencies.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

if ! command -v npm >/dev/null 2>&1; then
  echo "Missing frontend dependency: npm. Install Node.js/npm and retry." >&2
  exit 1
fi
if [[ ! -x "$ROOT_DIR/visualization/node_modules/.bin/vite" ]]; then
  echo "Missing visualization dependencies (vite not installed)." >&2
  echo "Run: cd '$ROOT_DIR/visualization' && npm install" >&2
  exit 1
fi

BACKEND_PID=""
FRONTEND_PID=""

VIEWER_SERVE_MODE="${SUPPLY_CHAIN_VIEWER_MODE:-}"
if [[ -z "$VIEWER_SERVE_MODE" ]]; then
  if [[ "$SUPPLY_CHAIN_PLATFORM_MODE" == "visitor" ]]; then VIEWER_SERVE_MODE="production"; else VIEWER_SERVE_MODE="dev"; fi
fi
case "$VIEWER_SERVE_MODE" in
  production)
    if ! node "$ROOT_DIR/scripts/viewer_build_stamp.mjs" --check; then
      echo "Preparing the visitor viewer build (once per source version)..."
      (cd "$ROOT_DIR/visualization" && npm run build)
    fi
    FRONTEND_COMMAND="preview"
    ;;
  dev) FRONTEND_COMMAND="dev" ;;
  *) echo "SUPPLY_CHAIN_VIEWER_MODE must be production or dev." >&2; exit 1 ;;
esac

cleanup() {
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

echo "Integrated SQLite mirror: $SIMULATION_SQL_MIRROR_PATH"
echo "Python runtime: $PYTHON_BIN"
echo "Starting simulation server: http://${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "$ROOT_DIR"
  "$PYTHON_BIN" -m uvicorn simulate.simulation_server:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
BACKEND_PID=$!

if ! wait_for_http "http://${BACKEND_HOST}:${BACKEND_PORT}/health" "simulation server"; then
  cleanup
  exit 1
fi
if ! wait_for_http "http://${BACKEND_HOST}:${BACKEND_PORT}/operations/artifacts/runs?limit=1" "operations artifact index"; then
  cleanup
  exit 1
fi

echo "Starting visualization: http://127.0.0.1:${FRONTEND_PORT}"
echo "Viewer serving mode: $VIEWER_SERVE_MODE"
(
  cd "$ROOT_DIR/visualization"
  npm run "$FRONTEND_COMMAND" -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

wait_for_http "http://127.0.0.1:${FRONTEND_PORT}/workspace_jobs/" "visualization data directory" 40 || {
  cleanup
  exit 1
}

wait -n "$BACKEND_PID" "$FRONTEND_PID"
EXIT_STATUS=$?
cleanup
wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
exit "$EXIT_STATUS"
