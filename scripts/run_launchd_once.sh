#!/usr/bin/env bash
set -euo pipefail
ROOT="/Users/rawshn/Projects/NopeRi"
cd "$ROOT"

mkdir -p runs

# If another scheduled runner is already active, do nothing
if [[ -f "runs/scheduled_daemon.pid" ]]; then
  RUNNER_PID="$(cat runs/scheduled_daemon.pid 2>/dev/null || true)"
  if [[ -n "$RUNNER_PID" ]] && kill -0 "$RUNNER_PID" 2>/dev/null; then
    echo "NopeRi runner is already active with PID $RUNNER_PID. Launchd job exiting."
    launchctl unload "$HOME/Library/LaunchAgents/com.rawshn.noperi-once.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.rawshn.noperi-once.plist"
    exit 0
  fi
fi

# Clean up LaunchAgent so it runs only once
launchctl unload "$HOME/Library/LaunchAgents/com.rawshn.noperi-once.plist" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.rawshn.noperi-once.plist"

export MIN_APPLY_COUNT=50
export USE_RAWSHN_CONFIG=1

LOG="runs/$(date +%Y%m%d-%H%M%S)-noperi-scheduled.log"
echo "$LOG" > runs/noperi-latest-logpath.txt
echo "Starting NopeRi via launchd at $(date)..."
caffeinate -ims ./run.sh 2>&1 | tee "$LOG"
