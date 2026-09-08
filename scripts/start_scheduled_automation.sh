#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p runs
DAEMON_LOG="runs/scheduled_daemon.log"

if [[ -f "runs/scheduled_daemon.pid" ]]; then
  EXISTING_PID="$(cat runs/scheduled_daemon.pid || true)"
  if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "Scheduled automation is already running with PID $EXISTING_PID."
    echo "Check status with: cat runs/scheduled_run_state.json"
    echo "View logs with  : tail -f $DAEMON_LOG"
    exit 0
  else
    rm -f runs/scheduled_daemon.pid
  fi
fi

export PYTHONUNBUFFERED=1

echo "Starting NopeRi one-time scheduled automation under caffeinate..."
nohup caffeinate -ims .venv/bin/python -u scripts/run_scheduled_once.py "$@" > "$DAEMON_LOG" 2>&1 &
RUNNER_PID=$!
echo "Process launched with PID $RUNNER_PID"
sleep 2

if kill -0 "$RUNNER_PID" 2>/dev/null; then
  echo "Automation daemon is active."
  cat "$DAEMON_LOG"
  echo ""
  cat runs/scheduled_run_state.json 2>/dev/null || true
  echo ""
  echo "Log file: $DAEMON_LOG"
else
  echo "Failed to start daemon. Recent output:"
  cat "$DAEMON_LOG"
  exit 1
fi
