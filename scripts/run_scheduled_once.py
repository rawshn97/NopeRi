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


def parse_time_str(time_str: str) -> tuple[int, int, int]:
    """Parse HH:MM or HH:MM:SS string."""
    parts = time_str.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1]), 0
    elif len(parts) == 3:
        return int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError(f"Invalid time format: '{time_str}'. Expected HH:MM or HH:MM:SS (24-hour).")


def get_target_ist(time_str: str | None = None, target_ist_str: str | None = None) -> datetime:
    """Calculate target datetime in IST.

    If target_ist_str is provided, parses exact timestamp.
    Otherwise, uses time_str (defaulting to 00:10 IST). If the target time today in IST
    has already passed, targets tomorrow. If still upcoming today, targets today.
    """
    now_ist = datetime.now(timezone.utc).astimezone(IST)

    if target_ist_str:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(target_ist_str.strip(), fmt)
                return dt.replace(tzinfo=IST)
            except ValueError:
                continue
        raise ValueError(f"Invalid target-ist format: '{target_ist_str}'. Expected 'YYYY-MM-DD HH:MM:SS'.")

    if not time_str:
        time_str = os.environ.get("SCHEDULED_TIME_IST") or os.environ.get("TARGET_TIME_IST") or "00:10"

    hour, minute, second = parse_time_str(time_str)
    candidate_today = datetime(now_ist.year, now_ist.month, now_ist.day, hour, minute, second, tzinfo=IST)

    if (candidate_today - now_ist).total_seconds() > 5:
        return candidate_today
    else:
        return candidate_today + timedelta(days=1)


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
        "--time",
        "-t",
        type=str,
        default=None,
        help="Target time of day in IST (24-hour format: HH:MM or HH:MM:SS, e.g. 00:10, 01:30, 09:00). Default: 00:10 IST",
    )
    parser.add_argument(
        "--target-ist",
        type=str,
        default=None,
        help="Exact target datetime in IST (format: YYYY-MM-DD HH:MM:SS)",
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

    def handle_signal(sig, frame):
        finished_ist = datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{finished_ist} IST] Received termination signal ({sig}). Stopping scheduler daemon.")
        update_state({
            "status": "stopped",
            "stopped_at": finished_ist,
        })
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    target_dt = get_target_ist(time_str=args.time, target_ist_str=args.target_ist)

    now_ist = datetime.now(timezone.utc).astimezone(IST)
    diff_secs = (target_dt - now_ist).total_seconds()
    hrs, rem = divmod(int(diff_secs), 3600)
    mins, secs = divmod(rem, 60)

    print(f"[{now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST] Scheduled runner initialized.")
    print(f"  PID               : {current_pid}")
    print(f"  Target start time : {target_dt.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"  Time remaining    : {hrs}h {mins}m {secs}s")
    print(f"  Min apply count   : {args.min_apply_count}")
    print("  Hardware check    : Keep this laptop/PC powered on and awake until the run executes.")
    sys.stdout.flush()

    update_state({
        "pid": current_pid,
        "target_ist": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "target_time_arg": args.time or "00:10",
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
    env["USE_ADVANCED_CONFIG"] = "1"

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
