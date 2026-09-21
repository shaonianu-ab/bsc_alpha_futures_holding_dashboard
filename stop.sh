#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIRECTORY="$PROJECT_ROOT/.run"
PID_FILE="$RUNTIME_DIRECTORY/dashboard.pid"

is_dashboard_process() {
  local pid="$1"
  local command

  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command" == *"$PROJECT_ROOT/dashboard_server.py"* ]]
}

if [[ ! -f "$PID_FILE" ]]; then
  printf 'Dashboard is not running through start.sh.\n'
  exit 0
fi

read -r dashboard_pid < "$PID_FILE" || true
if ! is_dashboard_process "${dashboard_pid:-}"; then
  rm -f "$PID_FILE"
  printf 'Removed a stale dashboard PID record.\n'
  exit 0
fi

kill "$dashboard_pid"
for _ in {1..10}; do
  if ! kill -0 "$dashboard_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    printf 'Dashboard stopped.\n'
    exit 0
  fi
  sleep 1
done

printf 'Dashboard did not stop after 10 seconds; it was not force-terminated.\n' >&2
exit 1
