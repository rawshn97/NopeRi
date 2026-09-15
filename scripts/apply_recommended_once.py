#!/usr/bin/env python3
"""Apply Easy Apply jobs from Naukri recommended feed (bypasses search reCAPTCHA)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from colorama import Fore, Style, init

load_dotenv(ROOT / ".env")
init(autoreset=True)

# Match run.sh defaults
os.environ.setdefault("USE_RAWSHN_CONFIG", "1")
if os.getenv("OPENROUTER_API_KEY") and not os.getenv("OPEN_API_KEY"):
    os.environ["OPEN_API_KEY"] = os.environ["OPENROUTER_API_KEY"]

from src.client.naukri_client import NaukriLoginClient
from src.client.job_client import NaukriJobClient
import apply_agent as aa


def main() -> int:
    goal = int(os.getenv("MIN_APPLY_COUNT", "36"))
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    ai_key = os.getenv("OPEN_API_KEY") or os.getenv("OPENROUTER_API_KEY")

    aa.print_section_title("logging in to naukri (recommended feed)")
    client = NaukriLoginClient(username, password)
    client.login()
    print(f"  {Fore.GREEN}Logged in as {Fore.YELLOW}{username}{Style.RESET_ALL}")

    recent = aa.count_applies_last_24h()
    remaining_quota = max(0, aa.NAUKRI_DAILY_QUOTA - recent)
    session_goal = min(goal, remaining_quota)
    print(f"  {Fore.WHITE}Applies last 24h :{Style.RESET_ALL}  {recent}/{aa.NAUKRI_DAILY_QUOTA}")
    print(f"  {Fore.WHITE}Session goal     :{Style.RESET_ALL}  {session_goal}")

    if session_goal <= 0:
        print(f"  {Fore.YELLOW}Daily quota already full.{Style.RESET_ALL}")
        return 0

    jc = NaukriJobClient(client)
    aa.print_section_title("fetching recommended jobs")
    jobs = jc.get_recommended_jobs()
    print(f"  {Fore.CYAN}Recommended raw  :{Style.RESET_ALL}  {len(jobs)}")

    jobs, easy_stats = aa.filter_easy_apply_candidates(jobs)
    aa.print_easy_apply_filter_stats(len(jobs) + sum(easy_stats.values()), len(jobs), easy_stats)

    if not jobs:
        print(f"  {Fore.YELLOW}No Easy Apply jobs in recommended feed.{Style.RESET_ALL}")
        return 1

    pipeline = aa.build_pipeline(ai_key)
    final_jobs = pipeline.run(jobs)
    aa._normalize_final_job_rows(final_jobs)
    print(f"  {Fore.CYAN}AI-allowed        :{Style.RESET_ALL}  {len(final_jobs)}")

    if not final_jobs:
        print(f"  {Fore.YELLOW}No jobs passed classifier.{Style.RESET_ALL}")
        return 1

    applied_jobs_set = aa.load_applied_jobs()
    applied, skipped_ext, skipped_already, failed, quota_hit = aa.apply_to_filtered_jobs(
        jc,
        jobs,
        final_jobs,
        applied_jobs_set,
        session_goal=session_goal,
        apply_target=None,
        session_applied_so_far=0,
    )

    aa.print_section_title("recommended apply summary")
    print(f"  Applied          :  {applied}")
    print(f"  Skipped external :  {skipped_ext}")
    print(f"  Already applied  :  {skipped_already}")
    print(f"  Failed           :  {failed}")
    print(f"  Quota hit        :  {quota_hit}")
    print(f"  24h total now    :  {aa.count_applies_last_24h()}")
    return 0 if applied > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
