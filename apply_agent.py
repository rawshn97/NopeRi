# ----------------------------------------------------------------------------------
# apply_agent.py
#
# Entry point for the automated Naukri job application agent.
#
# What this script does end to end:
#   1. Logs in to Naukri using credentials from the environment.
#   2. Searches for jobs across a curated set of keyword/location queries (stream apply per chunk).
#   3. Deduplicates results and passes each search chunk through an AI scoring pipeline.
#   4. Applies to Easy Apply jobs as they pass the filter (no full-sweep wait).
#   5. Handles questionnaires automatically using a static answer engine.
#   6. Skips external company-site jobs (search flags + cache before AI; apply API at submit).
#   7. Persists applied job IDs to a CSV so they are never applied to twice.
#   8. Prints a structured terminal summary at the end of each run.
#
# Dependencies:
#   - NaukriLoginClient   : handles login and session management
#   - NaukriJobClient     : wraps Naukri's internal job/apply APIs
#   - JobFilterPipeline2  : AI-based job relevance scorer
#   - colorama            : terminal color output
#
# Configuration:
#   Set USERNAME, PASSWORD, and OPEN_API_KEY in a .env file.
#   Adjust BQUERIES, EXPERIENCE_LEVELS, PAGES, and JOB_AGE inside
#   fetch_all_jobs() to tune what gets fetched each run.
# ----------------------------------------------------------------------------------

from src.client.naukri_client import NaukriLoginClient
from src.client.job_client import NaukriJobClient
from src.client.jop_classifier import JobFilterPipeline2
from config.ba_salary import (
    BA_MIN_LPA,
    ba_salary_allows_apply,
    is_business_analyst_title,
    salary_text_from_job_details,
)
from src.exceptions.exceptions import NaukriAuthError, NaukriParseError, NaukriRecaptchaError
from src.external_jobs import load_external_job_ids, log_external_skip
from src.questionnaire_review import append_questionnaire_review, build_review_record
from src.search_variation_state import (
    estimate_variation_space,
    finish_run,
    is_tried,
    load_state,
    record_variation,
    reset_state,
    start_run,
    state_path,
    summarize,
    variation_key,
)
from dotenv import load_dotenv
from colorama import Fore, Back, Style, init
import os
import time
import csv
import logging
from datetime import datetime, timedelta, timezone

load_dotenv()

NAUKRI_DAILY_QUOTA = int(os.getenv("NAUKRI_DAILY_QUOTA", "50"))
_QUOTA_MARKERS = (
    "daily quota",
    "quota of jobs exceeded",
    "quota exceeded",
    "crossed the limit of your daily quota",
)
init(autoreset=True)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------
# Persistence — applied jobs CSV
#
# A flat CSV file is used as a lightweight store for applied job IDs. This
# prevents the agent from applying to the same job on subsequent runs.
# The file is appended to, never rewritten, so historical records are preserved.
# ----------------------------------------------------------------------------------

CSV_FILE = "applied_jobs.csv"


def load_applied_jobs() -> set:
    # Returns the set of job_ids already applied to in previous runs.
    # Returns an empty set if the file does not exist yet.
    if not os.path.exists(CSV_FILE):
        return set()
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return set(row["job_id"] for row in reader)


def count_applied_jobs() -> int:
    return len(load_applied_jobs())


def _parse_applied_at(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def count_applies_last_24h() -> int:
    """Rolling 24h apply count from applied_jobs.csv (matches Naukri quota window)."""
    if not os.path.exists(CSV_FILE):
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    count = 0
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dt = _parse_applied_at(row.get("applied_at", ""))
            if dt and dt >= cutoff:
                count += 1
    return count


def is_naukri_daily_quota_error(exc_or_msg) -> bool:
    msg = str(exc_or_msg).lower()
    return any(marker in msg for marker in _QUOTA_MARKERS)


def print_naukri_quota_stop(*, applies_24h: int, limit: int, session_applied: int) -> None:
    print(f"\n{LINE}")
    print(
        f"  {Fore.YELLOW}{Style.BRIGHT}Naukri daily apply quota reached "
        f"({applies_24h}/{limit} in last 24h; {session_applied} this session).{Style.RESET_ALL}"
    )
    print(
        f"  {Fore.WHITE}Stopping apply loop. Retry after the rolling 24h window resets.{Style.RESET_ALL}"
    )
    print(LINE)


def _env_int(name: str) -> int | None:
    val = (os.getenv(name) or "").strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        print(f"  {Fore.YELLOW}Warning: invalid {name}={val!r}, ignoring{Style.RESET_ALL}")
        return None


def parse_apply_targets() -> tuple[int | None, int | None, int]:
    """Return (cumulative APPLY_TARGET, session MIN_APPLY_COUNT, starting CSV count)."""
    starting = count_applied_jobs()
    return _env_int("APPLY_TARGET"), _env_int("MIN_APPLY_COUNT"), starting


def resolve_session_goal(
    apply_target: int | None,
    min_apply: int | None,
    starting_count: int,
) -> int | None:
    """How many successful Easy Applies to attempt this session (None = no cap)."""
    goals: list[int] = []
    if min_apply is not None and min_apply > 0:
        goals.append(min_apply)
    if apply_target is not None:
        goals.append(max(0, apply_target - starting_count))
    if not goals:
        return None
    return max(goals)


def get_search_round_config() -> tuple[int, int]:
    if os.getenv("USE_RAWSHN_CONFIG") == "1":
        from config.rawshn_search import MAX_SEARCH_ROUNDS, PAGES
        pages = int(os.getenv("RAWSHN_PAGES", str(PAGES)))
        rounds = int(os.getenv("RAWSHN_SEARCH_ROUNDS", str(MAX_SEARCH_ROUNDS)))
        return pages, rounds
    return 1, 1


def build_pipeline(ai_key: str):
    daily_limit = int(os.getenv("DAILY_APPLY_LIMIT", "50"))
    ai_score_limit = int(os.getenv("AI_SCORE_LIMIT", "300"))
    min_apply_score = int(os.getenv("MIN_APPLY_SCORE", "70"))
    apply_target, min_apply, starting = parse_apply_targets()
    if os.getenv("EXHAUST_JOBS", "").strip().lower() in ("1", "true", "yes"):
        daily_limit = int(os.getenv("DAILY_APPLY_LIMIT", "999"))
        ai_score_limit = int(os.getenv("AI_SCORE_LIMIT", "500"))
    elif apply_target is not None:
        daily_limit = max(daily_limit, max(0, apply_target - starting))
    if min_apply is not None:
        daily_limit = max(daily_limit, min_apply)
    if os.getenv("USE_RAWSHN_CONFIG") == "1":
        from config.rawshn_classifier import JobFilterPipelinePM
        return JobFilterPipelinePM(
            openai_api_key=ai_key,
            daily_apply_limit=daily_limit,
            ai_score_limit=ai_score_limit,
            min_apply_score=min_apply_score,
        )
    return JobFilterPipeline2(
        openai_api_key=ai_key,
        daily_apply_limit=daily_limit,
        ai_score_limit=ai_score_limit,
        min_apply_score=min_apply_score,
    )


def log_questionnaire_review(job, status: str, qa_log: list, *, applied_at=None, skipped_at=None) -> None:
    record = build_review_record(
        job_id=job.job_id,
        title=job.title,
        company=job.company,
        status=status,
        qa_log=qa_log,
        applied_at=applied_at,
        skipped_at=skipped_at,
    )
    append_questionnaire_review(record)


def save_applied_job(job) -> None:
    # Appends a single job record to the CSV after a successful apply.
    # Creates the file with a header row on first write.
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["job_id", "title", "company", "applied_at"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "job_id":     job.job_id,
            "title":      job.title,
            "company":    job.company,
            "applied_at": datetime.utcnow().isoformat(),
        })


# ----------------------------------------------------------------------------------
# Terminal display helpers
#
# All output is routed through these functions so the visual style stays
# consistent across the run. Nothing here affects business logic.
# ----------------------------------------------------------------------------------

LINE = f"{Fore.WHITE}{'─' * 68}{Style.RESET_ALL}"
THIN = f"{Fore.WHITE}{'·' * 68}{Style.RESET_ALL}"


def print_section_title(text: str) -> None:
    # Prints a bold titled section divider. Used to mark each major phase
    # of the run (login, fetch, filter, apply, summary).
    print(f"\n{LINE}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}{text.upper()}{Style.RESET_ALL}")
    print(LINE)


def print_job_header(index: int, total: int, job, score=None, ai_detail=None) -> None:
    # Prints the full metadata block for a single job. Includes title, company,
    # job ID, URL, AI score with a visual bar, and skill tags if present.
    now = datetime.utcnow().strftime("%Y-%m-%d  %H:%M UTC")
    score_str = ""

    if score is not None:
        score_color = Fore.GREEN if score >= 70 else (Fore.YELLOW if score >= 50 else Fore.RED)
        score_bar   = _score_bar(score)
        score_str   = f"  {score_color}{score}/100{Style.RESET_ALL}  {score_bar}"

    print(f"\n{LINE}")
    print(
        f"  {Fore.CYAN}{Style.BRIGHT}JOB {index}/{total}{Style.RESET_ALL}"
        f"  {Fore.WHITE}{now}{Style.RESET_ALL}"
    )
    print(THIN)
    print(f"  {Fore.WHITE}Title   :{Style.RESET_ALL}  {Style.BRIGHT}{job.title}{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Company :{Style.RESET_ALL}  {Fore.YELLOW}{job.company}{Style.RESET_ALL}")
    salary = getattr(job, "salary", None) or "Not disclosed"
    print(f"  {Fore.WHITE}Salary  :{Style.RESET_ALL}  {salary}")
    print(f"  {Fore.WHITE}Job ID  :{Style.RESET_ALL}  {Fore.BLUE}{job.job_id}{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}URL     :{Style.RESET_ALL}  {Fore.BLUE}https://www.naukri.com/job-listings-{job.job_id}{Style.RESET_ALL}")

    if score is not None:
        detail_text = f"  {Fore.WHITE}({ai_detail}){Style.RESET_ALL}" if ai_detail else ""
        print(f"  {Fore.WHITE}Score   :{Style.RESET_ALL}{score_str}{detail_text}")

    if job.tags:
        tag_str = "  ".join(f"{Fore.CYAN}[{t}]{Style.RESET_ALL}" for t in job.tags)
        print(f"  {Fore.WHITE}Tags    :{Style.RESET_ALL}  {tag_str}")


def _score_bar(score: int, width: int = 10) -> str:
    # Returns a small ASCII progress bar representing the AI score (0-100).
    # Color shifts from red to yellow to green as score increases.
    filled = int((score / 100) * width)
    bar    = "█" * filled + "░" * (width - filled)
    color  = Fore.GREEN if score >= 70 else (Fore.YELLOW if score >= 50 else Fore.RED)
    return f"{color}{bar}{Style.RESET_ALL}"


def print_status_applied(applied_at=None) -> None:
    ts = f"  {Fore.WHITE}at {applied_at}{Style.RESET_ALL}" if applied_at else ""
    print(f"  {Fore.GREEN}Status  :  Applied successfully{Style.RESET_ALL}{ts}")


def print_status_skipped_low_confidence(reason: str) -> None:
    print(f"  {Fore.YELLOW}Status  :  Skipped - {reason}{Style.RESET_ALL}")


def print_status_skipped_external() -> None:
    # External apply jobs cannot be submitted via the API. The URL is printed
    # in the job header so the user can open it manually if needed.
    print(f"  {Fore.YELLOW}Status  :  Skipped - external apply (open URL manually){Style.RESET_ALL}")


def print_status_skipped_already_applied() -> None:
    print(f"  {Fore.YELLOW}Status  :  Skipped - already in applied_jobs.csv{Style.RESET_ALL}")


def print_status_failed(error) -> None:
    print(f"  {Fore.RED}Status  :  Failed — {error}{Style.RESET_ALL}")


def print_questionnaire_notice() -> None:
    print(f"  {Fore.CYAN}           Questionnaire detected, handling automatically{Style.RESET_ALL}")


def print_pipeline_results(final_jobs: list) -> None:
    # Prints a compact ranked table of every job that passed the AI filter,
    # sorted by score descending. Gives a quick overview before the apply loop.
    print_section_title(f"AI filter — {len(final_jobs)} jobs passed")
    col_w  = [4, 35, 28, 6]
    header = (
        f"  {Fore.WHITE}{'#':<{col_w[0]}}  "
        f"{'Title':<{col_w[1]}}  "
        f"{'Company':<{col_w[2]}}  "
        f"{'Score':>{col_w[3]}}{Style.RESET_ALL}"
    )
    print(header)
    print(f"  {Fore.WHITE}{'─' * sum(col_w)}{Style.RESET_ALL}")

    for i, job in enumerate(final_jobs, 1):
        score = job.get("score")
        score_color = (
            Fore.GREEN  if score and score >= 70 else
            Fore.YELLOW if score and score >= 50 else
            Fore.RED
        )
        score_display = f"{score_color}{score:>3}{Style.RESET_ALL}" if score is not None else "  ?"
        title   = (job.get("title")   or "")[:col_w[1]]
        company = (job.get("company") or "")[:col_w[2]]
        print(
            f"  {Fore.CYAN}{i:<{col_w[0]}}{Style.RESET_ALL}  "
            f"{title:<{col_w[1]}}  "
            f"{Fore.YELLOW}{company:<{col_w[2]}}{Style.RESET_ALL}  "
            f"{score_display}"
        )


def print_fetch_progress(
    keyword: str,
    location: str,
    exp: int,
    page: int,
    fetched: int,
    new: int,
    job_age: int | None = None,
) -> None:
    # Prints a single progress line per search query showing how many jobs
    # were returned and how many were new (not seen in earlier queries).
    loc = location or "All India"
    kw_display = keyword[:28].ljust(28)
    loc_display = loc[:12].ljust(12)
    new_color = Fore.GREEN if new > 0 else Fore.WHITE
    age_display = job_age if job_age is not None else "-"
    print(
        f"  {Fore.WHITE}[{kw_display} | {loc_display} | exp={exp} | age={age_display} | p{page}]{Style.RESET_ALL}"
        f"  {Fore.WHITE}{fetched:>3} fetched  "
        f"{new_color}{new:>3} new{Style.RESET_ALL}"
    )


def print_summary(
    total_found: int,
    total_allowed: int,
    applied: int,
    skipped_ext: int,
    skipped_already: int,
    failed: int,
    *,
    csv_total: int | None = None,
    apply_target: int | None = None,
    session_goal: int | None = None,
    quota_exhausted: bool = False,
    applies_24h: int | None = None,
) -> None:
    # Prints the final run summary table. Called once at the end of the script.
    print_section_title("run summary")
    rows = [
        ("Jobs fetched (last round unique)", str(total_found), Fore.WHITE),
        ("Jobs passed AI filter (last round)", str(total_allowed), Fore.CYAN),
        ("Applied successfully (this session)", str(applied), Fore.GREEN),
        ("Skipped (external apply)", str(skipped_ext), Fore.YELLOW),
        ("Skipped (already applied)", str(skipped_already), Fore.YELLOW),
        ("Failed", str(failed), Fore.RED),
    ]
    if applies_24h is not None:
        rows.append((f"Applies in last 24h (limit {NAUKRI_DAILY_QUOTA})", str(applies_24h), Fore.CYAN))
    if quota_exhausted:
        rows.append(("Stopped reason", "Naukri daily quota", Fore.YELLOW))
    if csv_total is not None:
        rows.append(("Total in applied_jobs.csv", str(csv_total), Fore.GREEN))
    if apply_target is not None:
        remaining = max(0, apply_target - (csv_total or 0))
        rows.append((f"Remaining to APPLY_TARGET={apply_target}", str(remaining), Fore.CYAN))
    if session_goal is not None:
        rows.append(("Session goal (new applies)", str(session_goal), Fore.CYAN))
    for label, value, color in rows:
        print(f"  {Fore.WHITE}{label:<36}{Style.RESET_ALL}  {color}{Style.BRIGHT}{value}{Style.RESET_ALL}")
    print(LINE + "\n")


# ----------------------------------------------------------------------------------
# Job fetching
#
# Runs a fixed set of search queries against the Naukri search API and
# collects results into a deduplicated list.
#
# Design decisions:
#   - Queries are hand-curated for the target stack (Node.js, Python, backend).
#   - Only Bangalore and Pune are targeted — highest product/startup density.
#   - Experience is fixed at 2 years. exp=3 pulled in too many senior roles.
#   - job_age=2 keeps results fresh, which improves apply response rates.
#   - 1 page per query. Quality drops sharply beyond page 2 on Naukri.
#   - 1.2s sleep between requests to avoid rate limiting.
#   - Deduplication is done by job_id across all queries before returning.
# ----------------------------------------------------------------------------------

def filter_easy_apply_candidates(jobs: list) -> tuple[list, dict[str, int]]:
    """
    Drop jobs we can identify as non-Easy-Apply before AI scoring.

    Uses search payload flags (jobTypeFlags / responseManager) and the
    external_jobs.jsonl cache from prior runs.
    """
    known_external = load_external_job_ids()
    strict = os.getenv("STRICT_EASY_APPLY", "0") == "1"
    kept: list = []
    stats = {
        "known_external": 0,
        "search_external": 0,
        "unknown_skipped": 0,
        "easy_apply_flag": 0,
    }

    for job in jobs:
        if job.job_id in known_external:
            stats["known_external"] += 1
            continue
        if job.easy_apply is False:
            stats["search_external"] += 1
            continue
        if job.easy_apply is None and strict:
            stats["unknown_skipped"] += 1
            continue
        if job.easy_apply is True:
            stats["easy_apply_flag"] += 1
        kept.append(job)

    return kept, stats


def print_easy_apply_filter_stats(before: int, after: int, stats: dict[str, int]) -> None:
    print(
        f"\n  {Fore.CYAN}Easy Apply prefilter:{Style.RESET_ALL}  "
        f"{before} -> {after} jobs for AI"
    )
    if stats["easy_apply_flag"]:
        print(f"    {Fore.GREEN}search easy_apply flag :{Style.RESET_ALL}  {stats['easy_apply_flag']}")
    if stats["known_external"]:
        print(f"    {Fore.YELLOW}known external cache  :{Style.RESET_ALL}  {stats['known_external']}")
    if stats["search_external"]:
        print(f"    {Fore.YELLOW}search external signal:{Style.RESET_ALL}  {stats['search_external']}")
    if stats["unknown_skipped"]:
        print(f"    {Fore.YELLOW}unknown (strict skip) :{Style.RESET_ALL}  {stats['unknown_skipped']}")


def _search_goal_reached(
    *,
    session_goal: int | None,
    session_applied: int,
    apply_target: int | None,
    quota_exhausted: bool,
) -> bool:
    if quota_exhausted:
        return True
    if session_goal is not None and session_applied >= session_goal:
        return True
    if apply_target is not None and count_applied_jobs() >= apply_target:
        return True
    return False


def _normalize_final_job_rows(final_jobs: list) -> None:
    for row in final_jobs:
        if "score" not in row and row.get("ai_score") is not None:
            row["score"] = row["ai_score"]
        if "ai_detail" not in row and row.get("ai_reason"):
            row["ai_detail"] = row["ai_reason"]


def iter_search_jobs(
    jc: NaukriJobClient,
    pages: int | None = None,
    *,
    seen_ids: set | None = None,
    should_stop=None,
    variation_state: dict | None = None,
    search_round: int = 1,
    variation_stats: dict | None = None,
):
    """
    Yield lists of new (deduped) jobs after each search API call.

    Rawshn nesting (outer to inner): freshness -> title -> location -> experience -> pages.
    Fresher jobAge bands run first; older bands run only if the session goal is not met.

    Pagination is adaptive: keep requesting the next page while Naukri returns a full
    page (RESULTS_PER_PAGE, default 20). Stop early on a short/empty page, when an
    entire page is duplicate (0 new jobs), or when pageNo is past the last page.
    Hard cap: MAX_PAGES_PER_QUERY (default 6).
    """
    use_rawshn = os.getenv("USE_RAWSHN_CONFIG") == "1"
    if use_rawshn:
        from config.rawshn_search import (
            CITY_ORDER,
            EXPERIENCE_LEVELS,
            JOB_AGE_LEVELS,
            MAX_PAGES_PER_QUERY,
            PM_KEYWORDS,
            RESULTS_PER_PAGE,
            STOP_ON_ZERO_NEW,
        )
    else:
        BQUERIES = [
            {"keyword": "Node.js backend developer", "location": "Bangalore"},
            {"keyword": "Python Developer",          "location": ""},
            {"keyword": "Node.js Developer",         "location": ""},
            {"keyword": "python backend developer",  "location": "Pune"},
        ]

        EXPERIENCE_LEVELS = [2]
        JOB_AGE_LEVELS = [2]
        MAX_PAGES_PER_QUERY = 5
        RESULTS_PER_PAGE = 20
        STOP_ON_ZERO_NEW = os.getenv("RAWSHN_STOP_ON_ZERO_NEW", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )

    # Adaptive stop + MAX_PAGES_PER_QUERY control depth (`pages` arg kept for call-site compat).
    max_pages = max(1, MAX_PAGES_PER_QUERY)
    search_delay = float(os.getenv("NAUKRI_SEARCH_DELAY", "3"))

    # Recaptcha wall handling: 406-after-retries means Naukri is rate-limiting
    # this IP/session. Cool down instead of burning every combo, and give up on
    # the sweep (not the run) if walls persist after several cooldowns.
    recaptcha_cooldown = float(os.getenv("RECAPTCHA_COOLDOWN", "900"))
    recaptcha_max_walls = int(os.getenv("RECAPTCHA_MAX_WALLS", "3"))
    recaptcha_state = {"consecutive": 0, "walled": False}

    def _sweep_walled() -> bool:
        return recaptcha_state["walled"]

    if seen_ids is None:
        seen_ids = set()
    if variation_stats is None:
        variation_stats = {"tried": 0, "skipped": 0}
    variation_verbose = os.getenv("SEARCH_VARIATION_VERBOSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if use_rawshn:
        combo_base = (
            len(PM_KEYWORDS)
            * len(CITY_ORDER)
            * len(EXPERIENCE_LEVELS)
            * len(JOB_AGE_LEVELS)
        )
        print_section_title(
            f"fetching jobs  (freshness-first: {len(JOB_AGE_LEVELS)} age bands x "
            f"{len(PM_KEYWORDS)} titles x {len(CITY_ORDER)} cities "
            f"x {len(EXPERIENCE_LEVELS)} exp, "
            f"adaptive pages up to {max_pages} while full={RESULTS_PER_PAGE}; "
            f"~{combo_base}+ searches)"
        )
    else:
        print_section_title(
            f"fetching jobs  ({len(BQUERIES)} queries, adaptive pages up to {max_pages})"
        )

    def _page_past_end(err: Exception) -> bool:
        text = str(err)
        return "pageNo" in text and (
            "doesn't exists" in text
            or "does not exist" in text
            or "Requested page number" in text
        )

    def _collect_search(keyword: str, location: str, exp: int, job_age: int, page: int):
        """Return (new_jobs, continue_paging, fetched_count, skipped_resume)."""
        key = variation_key(keyword, location, exp, job_age, page)
        if variation_state is not None and is_tried(variation_state, key):
            variation_stats["skipped"] += 1
            entry = variation_state.get("tried", {}).get(key, {})
            keep_going = entry.get("keep_going", False)
            if (
                STOP_ON_ZERO_NEW
                and entry.get("fetched", 0) > 0
                and entry.get("new", 0) == 0
            ):
                keep_going = False
            if variation_verbose:
                print(
                    f"  {Fore.WHITE}[SKIP]{Style.RESET_ALL}   "
                    f"{keyword} | {location or 'All India'} | exp={exp} | age={job_age} | p{page}"
                    f"  (logged in prior run)"
                )
            return [], keep_going, 0, True

        try:
            jobs = jc.search_jobs(
                keyword=keyword,
                location=location,
                experience=exp,
                job_age=job_age,
                page=page,
                results_per_page=RESULTS_PER_PAGE,
            )

            new_jobs = []
            for job in jobs:
                job_id = getattr(job, "id", None) or getattr(job, "job_id", None)
                if job_id and job_id not in seen_ids:
                    seen_ids.add(job_id)
                    new_jobs.append(job)

            print_fetch_progress(
                keyword, location, exp, page,
                fetched=len(jobs),
                new=len(new_jobs),
                job_age=job_age,
            )

            fetched = len(jobs)
            # Keep paging only while Naukri returns a full page.
            keep_going = fetched >= RESULTS_PER_PAGE
            if STOP_ON_ZERO_NEW and fetched > 0 and len(new_jobs) == 0:
                keep_going = False
                print(
                    f"  {Fore.WHITE}[DEDUP]{Style.RESET_ALL}   "
                    f"{keyword} | {location or 'All India'} | exp={exp} | age={job_age} | p{page}"
                    f"  (all duplicates; stop paging combo)"
                )
            if variation_state is not None:
                record_variation(
                    variation_state,
                    key,
                    keyword=keyword,
                    city=location,
                    exp=exp,
                    job_age=job_age,
                    page=page,
                    fetched=fetched,
                    new=len(new_jobs),
                    keep_going=keep_going,
                    status="ok",
                    search_round=search_round,
                )
                variation_stats["tried"] += 1
            recaptcha_state["consecutive"] = 0
            time.sleep(search_delay)
            return new_jobs, keep_going, fetched, False

        except NaukriRecaptchaError:
            # Do not record the variation: it was never actually searched, so it
            # must stay eligible for a future run.
            recaptcha_state["consecutive"] += 1
            walls = recaptcha_state["consecutive"]
            if walls >= recaptcha_max_walls:
                recaptcha_state["walled"] = True
                print(
                    f"  {Fore.YELLOW}{Style.BRIGHT}[RECAPTCHA]{Style.RESET_ALL}  "
                    f"{walls} consecutive walls; ending search sweep "
                    f"(unsearched combos stay pending for the next run)"
                )
                return [], False, 0, False
            print(
                f"  {Fore.YELLOW}[RECAPTCHA]{Style.RESET_ALL}  "
                f"wall {walls}/{recaptcha_max_walls} on "
                f"{keyword} | {location or 'All India'} | exp={exp} | age={job_age} | p{page}"
                f"  ->  cooling down {int(recaptcha_cooldown)}s"
            )
            time.sleep(recaptcha_cooldown)
            return [], False, 0, False

        except Exception as e:
            if _page_past_end(e):
                print(
                    f"  {Fore.WHITE}[END]{Style.RESET_ALL}   "
                    f"{keyword} | {location or 'All India'} | exp={exp} | age={job_age} | p{page}"
                    f"  (no more pages)"
                )
                if variation_state is not None:
                    record_variation(
                        variation_state,
                        key,
                        keyword=keyword,
                        city=location,
                        exp=exp,
                        job_age=job_age,
                        page=page,
                        fetched=0,
                        new=0,
                        keep_going=False,
                        status="end_of_pages",
                        search_round=search_round,
                    )
                    variation_stats["tried"] += 1
                time.sleep(search_delay)
                return [], False, 0, False
            print(
                f"  {Fore.RED}[FAIL]{Style.RESET_ALL}  "
                f"{keyword} | {location or 'All India'} | exp={exp} | age={job_age} | p{page}  ->  {e}"
            )
            if variation_state is not None:
                record_variation(
                    variation_state,
                    key,
                    keyword=keyword,
                    city=location,
                    exp=exp,
                    job_age=job_age,
                    page=page,
                    fetched=0,
                    new=0,
                    keep_going=False,
                    status="error",
                    search_round=search_round,
                )
                variation_stats["tried"] += 1
            time.sleep(search_delay)
            # Transient search errors: stop this combo (don't hammer empty deeper pages).
            return [], False, 0, False

    def _yield_chunk(new_jobs: list):
        if new_jobs:
            yield new_jobs

    def _pager(keyword: str, location: str, exp: int, job_age: int):
        for page in range(1, max_pages + 1):
            if (should_stop and should_stop()) or _sweep_walled():
                return
            new_jobs, keep_going, _fetched, skipped_resume = _collect_search(
                keyword, location, exp, job_age, page
            )
            if not skipped_resume:
                yield from _yield_chunk(new_jobs)
            if not keep_going:
                break

    if use_rawshn:
        for age_idx, job_age in enumerate(JOB_AGE_LEVELS):
            if (should_stop and should_stop()) or _sweep_walled():
                return
            if age_idx > 0:
                print(
                    f"\n  {Fore.CYAN}Freshness band age={job_age} days "
                    f"(session goal not yet met; expanding beyond age="
                    f"{JOB_AGE_LEVELS[age_idx - 1]}).{Style.RESET_ALL}"
                )
            for keyword in PM_KEYWORDS:
                if (should_stop and should_stop()) or _sweep_walled():
                    return
                for city in CITY_ORDER:
                    if (should_stop and should_stop()) or _sweep_walled():
                        return
                    for exp in EXPERIENCE_LEVELS:
                        if (should_stop and should_stop()) or _sweep_walled():
                            return
                        yield from _pager(keyword, city, exp, job_age)
    else:
        for q in BQUERIES:
            if (should_stop and should_stop()) or _sweep_walled():
                return
            yield from _pager(
                q["keyword"], q["location"], EXPERIENCE_LEVELS[0], JOB_AGE_LEVELS[0]
            )


def run_search_round_streaming(
    jc: NaukriJobClient,
    pipeline,
    applied_jobs_set: set,
    *,
    pages: int,
    seen_ids: set,
    session_goal: int | None,
    apply_target: int | None,
    session_applied: int,
    quota_exhausted: bool,
    search_round: int = 1,
    variation_state: dict | None = None,
    variation_stats: dict | None = None,
) -> tuple[int, int, int, int, int, int, bool, bool]:
    """
    One search round: stream each API chunk through easy-apply filter, AI, and apply.

    Returns (found, allowed, applied, skipped_ext, skipped_already, failed, quota_hit, any_jobs).
    """
    round_found = 0
    round_allowed = 0
    round_applied = 0
    skipped_ext = 0
    skipped_already = 0
    failed = 0
    quota_hit = quota_exhausted
    any_jobs = False

    def should_stop() -> bool:
        return _search_goal_reached(
            session_goal=session_goal,
            session_applied=session_applied + round_applied,
            apply_target=apply_target,
            quota_exhausted=quota_hit,
        )

    for new_jobs in iter_search_jobs(
        jc,
        pages=pages,
        seen_ids=seen_ids,
        should_stop=should_stop,
        variation_state=variation_state,
        search_round=search_round,
        variation_stats=variation_stats,
    ):
        if should_stop():
            break

        round_found += len(new_jobs)
        if not new_jobs:
            continue

        any_jobs = True
        prefilter_before = len(new_jobs)
        jobs, easy_stats = filter_easy_apply_candidates(new_jobs)
        if prefilter_before != len(jobs):
            print_easy_apply_filter_stats(prefilter_before, len(jobs), easy_stats)

        if not jobs:
            continue

        final_jobs = pipeline.run(jobs)
        _normalize_final_job_rows(final_jobs)
        round_allowed += len(final_jobs)

        if not final_jobs:
            continue

        chunk_applied, chunk_ext, chunk_already, chunk_failed, chunk_quota = apply_to_filtered_jobs(
            jc,
            jobs,
            final_jobs,
            applied_jobs_set,
            session_goal=session_goal,
            apply_target=apply_target,
            session_applied_so_far=session_applied + round_applied,
        )

        round_applied += chunk_applied
        skipped_ext += chunk_ext
        skipped_already += chunk_already
        failed += chunk_failed
        if chunk_quota:
            quota_hit = True
            break

        if should_stop():
            break

    return (
        round_found,
        round_allowed,
        round_applied,
        skipped_ext,
        skipped_already,
        failed,
        quota_hit,
        any_jobs,
    )


# ----------------------------------------------------------------------------------
# Main — orchestrates the full agent run
# ----------------------------------------------------------------------------------

def _job_score(meta: dict) -> int | None:
    if meta.get("ai_score") is not None:
        return meta.get("ai_score")
    return meta.get("score")


def _job_ai_detail(meta: dict) -> str | None:
    return meta.get("ai_reason") or meta.get("ai_detail")


def apply_to_filtered_jobs(
    jc: NaukriJobClient,
    jobs: list,
    final_jobs: list,
    applied_jobs_set: set,
    *,
    session_goal: int | None,
    apply_target: int | None,
    session_applied_so_far: int = 0,
) -> tuple[int, int, int, int, bool]:
    """Apply loop. Returns (applied, skipped_ext, skipped_already, failed, quota_hit)."""
    score_map = {j["job_id"]: j for j in final_jobs}
    allow = set(score_map.keys())
    allowed_jobs = [j for j in jobs if j.job_id in allow]

    applied_count = 0
    skipped_ext = 0
    skipped_already = 0
    failed_count = 0
    quota_hit = False

    remaining_session = None
    if session_goal is not None:
        remaining_session = max(0, session_goal - session_applied_so_far)

    applies_24h = count_applies_last_24h()
    if applies_24h >= NAUKRI_DAILY_QUOTA:
        print_section_title(f"applying to {len(allowed_jobs)} filtered jobs")
        print_naukri_quota_stop(
            applies_24h=applies_24h,
            limit=NAUKRI_DAILY_QUOTA,
            session_applied=0,
        )
        return 0, 0, 0, 0, True

    print_section_title(f"applying to {len(allowed_jobs)} filtered jobs")

    for index, job in enumerate(allowed_jobs, start=1):
        if count_applies_last_24h() >= NAUKRI_DAILY_QUOTA:
            print_naukri_quota_stop(
                applies_24h=count_applies_last_24h(),
                limit=NAUKRI_DAILY_QUOTA,
                session_applied=applied_count,
            )
            quota_hit = True
            break

        if remaining_session is not None and applied_count >= remaining_session:
            print(f"\n  {Fore.GREEN}Session apply goal reached ({session_goal}).{Style.RESET_ALL}")
            break
        if apply_target is not None and count_applied_jobs() >= apply_target:
            print(f"\n  {Fore.GREEN}Cumulative APPLY_TARGET reached ({apply_target}).{Style.RESET_ALL}")
            break

        meta = score_map.get(job.job_id, {})
        print_job_header(
            index=index,
            total=len(allowed_jobs),
            job=job,
            score=_job_score(meta),
            ai_detail=_job_ai_detail(meta),
        )

        if is_business_analyst_title(job.title):
            salary_text = job.salary
            ok, reason = ba_salary_allows_apply(job.title, salary_text)
            if not ok:
                try:
                    details = jc.get_job_details_cached(job.job_id)
                    fetched = salary_text_from_job_details(details)
                    if fetched and fetched != "Not disclosed":
                        salary_text = fetched
                        job.salary = fetched
                        ok, reason = ba_salary_allows_apply(job.title, salary_text)
                except Exception as salary_exc:
                    reason = f"{reason}; details fetch failed ({salary_exc})"
            if not ok:
                print(
                    f"  {Fore.YELLOW}Status  :  Skipped BA salary "
                    f"({reason}; need >{BA_MIN_LPA:g} LPA){Style.RESET_ALL}"
                )
                continue

        if job.job_id in applied_jobs_set:
            print_status_skipped_already_applied()
            skipped_already += 1
            continue

        mandatory = job.tags[:2] if job.tags else []
        optional = job.tags[2:] if len(job.tags) > 2 else []

        auth_retried = False
        while True:
            try:
                result = jc.apply_job(
                    job,
                    mandatory_skills=mandatory,
                    optional_skills=optional,
                    source="search",
                )

                job_result = (result.get("jobs") or [{}])[0]

                external_url = jc.external_url_from_apply_response(result, job.job_id)
                if external_url:
                    log_external_skip(
                        job_id=job.job_id,
                        title=job.title,
                        company=job.company,
                        external_apply_url=external_url,
                        ai_score=_job_score(meta),
                        job_description=job.description,
                    )
                    print_status_skipped_external()
                    skipped_ext += 1
                    break

                if job_result.get("questionnaire"):
                    print_questionnaire_notice()
                    sid = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "0000000"
                    result = jc.handle_static_questionnaire_and_apply(
                        job,
                        questionnaire=job_result["questionnaire"],
                        sid=sid,
                        mandatory_skills=mandatory,
                        optional_skills=optional,
                        source="search",
                    )
                    if result.get("error") and is_naukri_daily_quota_error(result["error"]):
                        print_status_failed(result["error"])
                        print_naukri_quota_stop(
                            applies_24h=count_applies_last_24h(),
                            limit=NAUKRI_DAILY_QUOTA,
                            session_applied=applied_count,
                        )
                        quota_hit = True
                        break
                    if result.get("skipped"):
                        reason = result.get("reason", "low confidence questionnaire")
                        qa_log = result.get("qa_log") or []
                        for row in qa_log:
                            print(
                                f"  {Fore.WHITE}Q:{Style.RESET_ALL} {row.get('question', '')[:60]}"
                                f"  {Fore.CYAN}A:{Style.RESET_ALL} {row.get('answer')}"
                                f"  ({row.get('source')}/{row.get('confidence')})"
                            )
                        log_questionnaire_review(
                            job,
                            "skipped_low_confidence",
                            qa_log,
                            skipped_at=datetime.utcnow().isoformat(),
                        )
                        print_status_skipped_low_confidence(reason)
                        failed_count += 1
                        break
                    qa_log = result.get("qa_log") or []
                    for row in qa_log:
                        print(
                            f"  {Fore.WHITE}Q:{Style.RESET_ALL} {row.get('question', '')[:60]}"
                            f"  {Fore.CYAN}A:{Style.RESET_ALL} {row.get('answer')}"
                            f"  ({row.get('source')}/{row.get('confidence')})"
                        )
                    log_questionnaire_review(
                        job,
                        "submitted",
                        qa_log,
                        applied_at=datetime.utcnow().isoformat(),
                    )

                applied_at = datetime.utcnow().strftime("%H:%M:%S UTC")
                print_status_applied(applied_at)
                save_applied_job(job)
                applied_jobs_set.add(job.job_id)
                applied_count += 1
                break

            except NaukriAuthError as e:
                if is_naukri_daily_quota_error(e):
                    print_status_failed(e)
                    print_naukri_quota_stop(
                        applies_24h=count_applies_last_24h(),
                        limit=NAUKRI_DAILY_QUOTA,
                        session_applied=applied_count,
                    )
                    quota_hit = True
                    break
                if auth_retried:
                    print_status_failed(e)
                    failed_count += 1
                    break
                print(
                    f"  {Fore.YELLOW}Auth expired on apply; re-logging in and retrying once..."
                    f"{Style.RESET_ALL}"
                )
                try:
                    jc._client.login()
                except Exception as login_exc:
                    print_status_failed(login_exc)
                    failed_count += 1
                    break
                auth_retried = True
                continue
            except Exception as e:
                if is_naukri_daily_quota_error(e):
                    print_status_failed(e)
                    print_naukri_quota_stop(
                        applies_24h=count_applies_last_24h(),
                        limit=NAUKRI_DAILY_QUOTA,
                        session_applied=applied_count,
                    )
                    quota_hit = True
                    break
                print_status_failed(e)
                failed_count += 1
                break

        if quota_hit:
            break
        time.sleep(3)

    return applied_count, skipped_ext, skipped_already, failed_count, quota_hit


if __name__ == "__main__":

    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    ai_key   = os.getenv("OPEN_API_KEY") or os.getenv("OPENROUTER_API_KEY")

    apply_target, min_apply, starting_count = parse_apply_targets()
    session_goal = resolve_session_goal(apply_target, min_apply, starting_count)
    initial_pages, max_search_rounds = get_search_round_config()

    # Step 1: authenticate and establish session.
    print_section_title("logging in to naukri")
    client = NaukriLoginClient(username, password)
    client.login()
    print(f"  {Fore.GREEN}Logged in as {Fore.YELLOW}{username}{Style.RESET_ALL}")
    print(
        f"  {Fore.CYAN}Resume  :{Style.RESET_ALL}  "
        f"Using resume already on Naukri profile (no local PDF upload)"
    )

    if session_goal is not None or apply_target is not None or os.getenv("EXHAUST_JOBS", "").strip().lower() in ("1", "true", "yes"):
        print_section_title("apply targets")
        print(f"  {Fore.WHITE}CSV total now     :{Style.RESET_ALL}  {starting_count}")
        if os.getenv("EXHAUST_JOBS", "").strip().lower() in ("1", "true", "yes"):
            print(f"  {Fore.WHITE}EXHAUST_JOBS      :{Style.RESET_ALL}  run all search rounds until listings dry up")
        if apply_target is not None:
            print(f"  {Fore.WHITE}APPLY_TARGET      :{Style.RESET_ALL}  {apply_target} cumulative Easy Apply rows")
        if min_apply is not None:
            print(f"  {Fore.WHITE}MIN_APPLY_COUNT   :{Style.RESET_ALL}  {min_apply} new this session")
        if session_goal is not None:
            print(f"  {Fore.WHITE}Session goal      :{Style.RESET_ALL}  {session_goal} successful applies")
        print(
            f"  {Fore.YELLOW}Note: external-apply jobs do not count toward targets "
            f"(not logged to CSV).{Style.RESET_ALL}"
        )

    jc = NaukriJobClient(client)
    applied_jobs_set = load_applied_jobs()

    if os.getenv("RESET_SEARCH_VARIATIONS", "").strip().lower() in ("1", "true", "yes"):
        reset_state()
        print(f"  {Fore.YELLOW}Cleared search variation log ({state_path()}).{Style.RESET_ALL}")

    variation_state = load_state()
    variation_stats = {"tried": 0, "skipped": 0}
    variation_run = start_run(variation_state)

    use_rawshn = os.getenv("USE_RAWSHN_CONFIG") == "1"
    space_estimate = None
    if use_rawshn:
        from config.rawshn_search import (
            CITY_ORDER,
            EXPERIENCE_LEVELS,
            JOB_AGE_LEVELS,
            MAX_PAGES_PER_QUERY,
            PM_KEYWORDS,
        )

        space_estimate = estimate_variation_space(
            titles=len(PM_KEYWORDS),
            cities=len(CITY_ORDER),
            exp_levels=len(EXPERIENCE_LEVELS),
            age_levels=len(JOB_AGE_LEVELS),
            max_pages=MAX_PAGES_PER_QUERY,
        )
        var_summary = summarize(variation_state, space_estimate=space_estimate)
        print_section_title("search variation resume")
        print(f"  {Fore.WHITE}State file          :{Style.RESET_ALL}  {state_path()}")
        print(f"  {Fore.WHITE}Variations tried    :{Style.RESET_ALL}  {var_summary['tried']}")
        print(
            f"  {Fore.WHITE}Space estimate      :{Style.RESET_ALL}  "
            f"{var_summary['space_estimate']} title×city×exp×age×page combos"
        )
        print(
            f"  {Fore.WHITE}Remaining estimate  :{Style.RESET_ALL}  "
            f"{var_summary['remaining_estimate']}"
        )
        print(
            f"  {Fore.CYAN}Sweep is freshness-first: age bands "
            f"{JOB_AGE_LEVELS[0]}..{JOB_AGE_LEVELS[-1]} days, then titles, cities, exp "
            f"(no MIN_APPLY_SCORE override).{Style.RESET_ALL}"
        )

    session_applied = 0
    skipped_ext_total = 0
    skipped_already_total = 0
    failed_total = 0
    quota_exhausted = False
    last_found = 0
    last_allowed = 0

    pages = initial_pages
    seen_ids: set = set()
    pipeline = build_pipeline(ai_key)
    stopped_reason = "completed"

    for search_round in range(1, max_search_rounds + 1):
        if _search_goal_reached(
            session_goal=session_goal,
            session_applied=session_applied,
            apply_target=apply_target,
            quota_exhausted=quota_exhausted,
        ):
            break

        if max_search_rounds > 1:
            print_section_title(f"search round {search_round}/{max_search_rounds} ({pages} pages per query)")

        (
            round_found,
            round_allowed,
            round_applied,
            skipped_ext,
            skipped_already,
            failed,
            quota_hit,
            any_jobs,
        ) = run_search_round_streaming(
            jc,
            pipeline,
            applied_jobs_set,
            pages=pages,
            seen_ids=seen_ids,
            session_goal=session_goal,
            apply_target=apply_target,
            session_applied=session_applied,
            quota_exhausted=quota_exhausted,
            search_round=search_round,
            variation_state=variation_state,
            variation_stats=variation_stats,
        )

        last_found += round_found
        last_allowed += round_allowed
        session_applied += round_applied
        skipped_ext_total += skipped_ext
        skipped_already_total += skipped_already
        failed_total += failed
        if quota_hit:
            quota_exhausted = True
            stopped_reason = "naukri_daily_quota"
            break

        if not any_jobs:
            print(f"\n{Fore.YELLOW}  No jobs found this round.{Style.RESET_ALL}")
            if search_round < max_search_rounds:
                pages += 1
                continue
            stopped_reason = "no_jobs_found"
            break

        print(
            f"\n  {Fore.CYAN}Round {search_round} unique new jobs: {Style.BRIGHT}{round_found}{Style.RESET_ALL}  "
            f"AI passed: {round_allowed}  applied: {round_applied}"
        )

        exhaust_mode = os.getenv("EXHAUST_JOBS", "").strip().lower() in ("1", "true", "yes")
        if session_goal is None and apply_target is None and not exhaust_mode:
            break

        if _search_goal_reached(
            session_goal=session_goal,
            session_applied=session_applied,
            apply_target=apply_target,
            quota_exhausted=quota_exhausted,
        ):
            stopped_reason = "session_goal_reached"
            break

        if search_round >= max_search_rounds:
            stopped_reason = "search_rounds_complete"
            break

        pages += 1
        print(
            f"\n  {Fore.CYAN}Expanding search to {pages} pages per query "
            f"({session_applied}/{session_goal or '?'} session applies so far).{Style.RESET_ALL}"
        )
        time.sleep(2)

    if quota_exhausted:
        stopped_reason = "naukri_daily_quota"

    finish_run(
        variation_state,
        variation_run,
        stopped_reason=stopped_reason,
        tried_this_run=variation_stats["tried"],
        skipped_resume=variation_stats["skipped"],
    )
    if variation_stats["tried"] or variation_stats["skipped"]:
        print(
            f"\n  {Fore.CYAN}Search variation log:{Style.RESET_ALL}  "
            f"{variation_stats['tried']} new API calls logged, "
            f"{variation_stats['skipped']} skipped (resume)  ->  {state_path()}"
        )

    print_summary(
        total_found=last_found,
        total_allowed=last_allowed,
        applied=session_applied,
        skipped_ext=skipped_ext_total,
        skipped_already=skipped_already_total,
        failed=failed_total,
        csv_total=count_applied_jobs(),
        apply_target=apply_target,
        session_goal=session_goal,
        quota_exhausted=quota_exhausted,
        applies_24h=count_applies_last_24h(),
    )