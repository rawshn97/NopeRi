#!/usr/bin/env python3
"""
Automated job applier that runs NopeRi continuously until Naukri daily quota (50 applies) is exhausted.
If no jobs are found or quota is not met at the current MIN_APPLY_SCORE, it automatically reduces
the score threshold (reliability score) and re-scans listings.
"""

import os
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from apply_agent import count_applies_last_24h

SCORE_LEVELS = [70, 60, 50, 45, 40, 35, 30]
DAILY_QUOTA = 50

def main():
    print("====================================================================")
    print("  NOPERI AUTOMATED APPLIER: QUOTA EXHAUSTION LOOP")
    print("====================================================================")
    
    initial_applies = count_applies_last_24h()
    print(f"Current applies in last 24h: {initial_applies} / {DAILY_QUOTA}")
    
    if initial_applies >= DAILY_QUOTA:
        print(f"Quota already exhausted ({initial_applies}/{DAILY_QUOTA}). Exiting.")
        return

    for score in SCORE_LEVELS:
        current_24h = count_applies_last_24h()
        if current_24h >= DAILY_QUOTA:
            print(f"\n[SUCCESS] Daily quota exhausted ({current_24h}/{DAILY_QUOTA}).")
            break
            
        print(f"\n--------------------------------------------------------------------")
        print(f"  Running sweep with MIN_APPLY_SCORE = {score}")
        print(f"  Applies in last 24h so far: {current_24h} / {DAILY_QUOTA}")
        print(f"--------------------------------------------------------------------\n")
        
        env = os.environ.copy()
        env["MIN_APPLY_SCORE"] = str(score)
        env["RESET_SEARCH_VARIATIONS"] = "1"
        env["EXHAUST_JOBS"] = "1"
        env["USE_RAWSHN_CONFIG"] = "1"
        
        # Execute run.sh which handles apply_agent.py + Notion sync
        run_script = str(PROJECT_ROOT / "run.sh")
        proc = subprocess.run([run_script], env=env, cwd=str(PROJECT_ROOT))
        
        after_24h = count_applies_last_24h()
        print(f"\n[SWEEP RESULT] Finished sweep at MIN_APPLY_SCORE={score}.")
        print(f"Applies in last 24h: {after_24h} / {DAILY_QUOTA}")
        
        if after_24h >= DAILY_QUOTA:
            print(f"\n[SUCCESS] Daily quota exhausted ({after_24h}/{DAILY_QUOTA})!")
            break
        else:
            print(f"\n[REDUCING RELIABILITY SCORE] Quota not yet hit. Reducing MIN_APPLY_SCORE for next pass...\n")

if __name__ == "__main__":
    main()
