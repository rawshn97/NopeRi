#!/usr/bin/env python3
"""
One-time scheduled runner for NopeRi.
Waits until the quota reset window (default: 00:10:00 IST),
keeps the system awake, runs NopeRi once, and exits.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
STATE_FILE = RUNS_DIR / "scheduled_run_state.json"
PID_FILE = RUNS_DIR / "scheduled_daemon.pid"


def get_default_target_ist() -> datetime:
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    if now_ist.hour >= 1:
        target_date = (now_ist + timedelta(days=1)).date()
    else:
        target_date = now_ist.date()
    return datetime(target_date.year, target_date.month, target_date.day, 0, 10, 0, tzinfo=IST)


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def update_state(payload: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            current = {}
    current.update(payload)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="One-time scheduled runner for NopeRi")
    parser.add_argument(
        "--target-ist",
        type=str,
        default=None,
        help="Target datetime in IST (format: YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--min-apply-count",
        type=int,
        default=50,
        help="MIN_APPLY_COUNT for this session (default: 50)",
    )
    args = parser.parse_args()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # Check existing PID
    current_pid = os.getpid()
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if old_pid != current_pid and is_process_running(old_pid):
                print(f"Error: Scheduled runner already running with PID {old_pid}", file=sys.stderr)
                sys.exit(1)
        except ValueError:
            pass

    PID_FILE.write_text(str(current_pid))

    if args.target_ist:
        target_dt = datetime.strptime(args.target_ist, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    else:
        target_dt = get_default_target_ist()

    now_ist = datetime.now(timezone.utc).astimezone(IST)
    print(f"[{now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST] Scheduled runner initialized.")
    print(f"  PID               : {current_pid}")
    print(f"  Target start time : {target_dt.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"  Min apply count   : {args.min_apply_count}")
    sys.stdout.flush()

    update_state({
        "pid": current_pid,
        "target_ist": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "min_apply_count": args.min_apply_count,
        "status": "waiting",
        "initialized_at": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
    })

    # Countdown loop
    last_logged_minute = -1
    first_tick = True
    while True:
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(IST)
        diff_secs = (target_dt - now_ist).total_seconds()

        if diff_secs <= 0:
            print(f"[{now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST] Target time reached. Starting run.", flush=True)
            break

        current_minute = int(diff_secs // 60)
        # Log immediately on first tick, every 5 minutes, or every 30s when less than 2 minutes left
        if first_tick or diff_secs <= 120 or (current_minute != last_logged_minute and current_minute % 5 == 0):
            mins, secs = divmod(int(diff_secs), 60)
            print(
                f"[{now_ist.strftime('%H:%M:%S')} IST] Waiting for reset at {target_dt.strftime('%H:%M:%S')} IST "
                f"({mins}m {secs}s remaining)...",
                flush=True
            )
            first_tick = False
            last_logged_minute = current_minute

        sleep_interval = min(10.0 if diff_secs <= 60 else 30.0, max(1.0, diff_secs))
        time.sleep(sleep_interval)

    # Launch NopeRi
    start_time_str = datetime.now(timezone.utc).astimezone(IST).strftime("%Y%m%d-%H%M%S")
    run_log_name = f"{start_time_str}-noperi-scheduled.log"
    run_log_path = RUNS_DIR / run_log_name
    latest_log_pointer = RUNS_DIR / "noperi-latest-logpath.txt"

    relative_log_path = f"runs/{run_log_name}"
    latest_log_pointer.write_text(relative_log_path + "\n")

    update_state({
        "status": "running",
        "started_at": datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "log_path": relative_log_path,
    })

    print(f"Logging execution to {relative_log_path}")
    sys.stdout.flush()

    env = os.environ.copy()
    env["MIN_APPLY_COUNT"] = str(args.min_apply_count)
    env["USE_RAWSHN_CONFIG"] = "1"

    with open(run_log_path, "w", encoding="utf-8") as run_log:
        proc = subprocess.Popen(
            ["./run.sh"],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in proc.stdout:
            run_log.write(line)
            run_log.flush()
            # Also mirror critical lines to daemon stdout
            if any(k in line for k in ("LOGGING IN", "APPLY TARGETS", "Applied successfully", "Naukri daily apply quota", "RUN SUMMARY", "Notion sync")):
                print(f"  [NopeRi] {line.strip()}")
                sys.stdout.flush()

        proc.wait()
        exit_code = proc.returncode

    finished_ist = datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    final_status = "completed" if exit_code == 0 else f"failed (code {exit_code})"
    print(f"[{finished_ist} IST] NopeRi execution finished with status: {final_status}")
    sys.stdout.flush()

    update_state({
        "status": final_status,
        "finished_at": finished_ist,
        "exit_code": exit_code,
    })

    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except OSError:
            pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
