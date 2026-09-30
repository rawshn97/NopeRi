#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p runs
DAEMON_LOG="runs/scheduled_daemon.log"

# -------------------------------------------------------------------------
# Usage hint
# -------------------------------------------------------------------------
# Run at default 00:10 IST:
#   bash scripts/start_scheduled_automation.sh
#
# Run at a custom time (HH:MM, 24-hour IST):
#   bash scripts/start_scheduled_automation.sh --time 01:30
#   bash scripts/start_scheduled_automation.sh --time 09:00 --min-apply-count 50
#
# Environment variable alternative:
#   SCHEDULED_TIME_IST=02:00 bash scripts/start_scheduled_automation.sh
# -------------------------------------------------------------------------

echo ""
echo "============================================================"
echo "  NopeRi Autonomous Quota Scheduler"
echo "============================================================"
echo "  IMPORTANT: Your laptop/PC MUST remain powered on and awake"
echo "  until the scheduled run executes. caffeinate will prevent"
echo "  sleep while on AC power, but the machine must not be shut"
echo "  down or the lid closed without an external display."
echo "============================================================"
echo ""

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

echo "Launching scheduler daemon under caffeinate -ims..."
nohup caffeinate -ims .venv/bin/python -u scripts/run_scheduled_once.py "$@" > "$DAEMON_LOG" 2>&1 &
RUNNER_PID=$!
echo "Process launched with PID $RUNNER_PID"
sleep 2

if kill -0 "$RUNNER_PID" 2>/dev/null; then
  echo ""
  echo "-- Daemon startup log ----------------------------------------"
  cat "$DAEMON_LOG"
  echo "--------------------------------------------------------------"
  echo ""
  echo "State file:"
  cat runs/scheduled_run_state.json 2>/dev/null || echo "  (not yet written)"
  echo ""
  echo "Live log: tail -f $DAEMON_LOG"
  echo ""
  echo "caffeinate is active: system will not sleep (AC power required)."
  echo "Keep the lid open or connect to an external display."
else
  echo "ERROR: Daemon failed to start. Recent output:"
  cat "$DAEMON_LOG"
  exit 1
fi
