#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
RUNTIME_DIRECTORY="$PROJECT_ROOT/.run"
PID_FILE="$RUNTIME_DIRECTORY/dashboard.pid"
LOG_FILE="$RUNTIME_DIRECTORY/dashboard.log"
HOST="${DASHBOARD_HOST:-127.0.0.1}"
PORT="${DASHBOARD_PORT:-8765}"

is_dashboard_process() {
  local pid="$1"
  local command

  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command" == *"$PROJECT_ROOT/dashboard_server.py"* ]]
}

if [[ ! -x "$PYTHON" ]]; then
  command -v python3 >/dev/null || {
    printf 'Python 3 is required to create the project virtual environment.\n' >&2
    exit 1
  }
  python3 -m venv "$PROJECT_ROOT/.venv"
fi

mkdir -p "$RUNTIME_DIRECTORY"

if [[ -f "$PID_FILE" ]]; then
  read -r existing_pid < "$PID_FILE" || true
  if is_dashboard_process "${existing_pid:-}"; then
    printf 'Dashboard is already running with PID %s.\n' "$existing_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$PROJECT_ROOT"
nohup "$PYTHON" "$PROJECT_ROOT/dashboard_server.py" --host "$HOST" --port "$PORT" \
  >"$LOG_FILE" 2>&1 < /dev/null &
dashboard_pid=$!
printf '%s\n' "$dashboard_pid" > "$PID_FILE"

sleep 1
if ! is_dashboard_process "$dashboard_pid"; then
  rm -f "$PID_FILE"
  printf 'Dashboard failed to start. Review %s for details.\n' "$LOG_FILE" >&2
  exit 1
fi

printf 'Dashboard started with PID %s.\n' "$dashboard_pid"
printf 'Listening on %s:%s.\n' "$HOST" "$PORT"
