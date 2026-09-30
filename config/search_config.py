"""Search configuration defaults for NopeRi apply_agent."""

import os

# City priority: configurable via CITY_ORDER or SEARCH_CITY_ORDER (comma-separated city names)
_city_override = (
    os.getenv("CITY_ORDER", "")
    or os.getenv("SEARCH_CITY_ORDER", "")
).strip()

if _city_override:
    CITY_ORDER = [c.strip() for c in _city_override.split(",") if c.strip()]
else:
    CITY_ORDER = ["Hyderabad", "Pune", "Bangalore"]

_kw_override = (
    os.getenv("TARGET_KEYWORDS", "")
    or os.getenv("PM_KEYWORDS", "")
).strip()

_expand_kw_raw = os.getenv("EXPAND_KEYWORDS", "").strip().lower()
_expand_kw = _expand_kw_raw in ("1", "true", "yes")

ROOT_PM_KEYWORDS = [
    "Product Manager",
    "Product Owner",
    "Business Analyst",
]

EXPANDED_PM_KEYWORDS = [
    "Product Manager",
    "Product Owner",
    "Senior Product Manager",
    "Technical Product Manager",
    "Associate Product Manager",
    "AI Product Manager",
    "Growth Product Manager",
    "Implementation Product Manager",
    "Platform Product Manager",
    "Product Management Specialist",
    "Senior Business Analyst",
    "Business Analyst",
]

if _kw_override:
    PM_KEYWORDS = [k.strip() for k in _kw_override.split(",") if k.strip()]
elif _expand_kw:
    PM_KEYWORDS = EXPANDED_PM_KEYWORDS
else:
    # Default to root titles: Naukri full-text search matches specialized titles under roots
    PM_KEYWORDS = ROOT_PM_KEYWORDS

# Sweep order in apply_agent: job age (fresh first) -> titles -> location -> experience -> pages.
_exp_override = os.getenv("EXPERIENCE_LEVELS", "").strip()

if _exp_override:
    EXPERIENCE_LEVELS = [int(x.strip()) for x in _exp_override.split(",") if x.strip()]
else:
    EXPERIENCE_LEVELS = [4, 2]

# Adaptive pagination: keep requesting pages while Naukri returns a full page
# (RESULTS_PER_PAGE), stop on a short/empty page, all-duplicate page, or missing pageNo (400).
RESULTS_PER_PAGE = int(os.getenv("RESULTS_PER_PAGE", "20"))
PAGES = int(os.getenv("PAGES", "2"))
MAX_PAGES_PER_QUERY = int(os.getenv("MAX_PAGES", "2"))
MIN_NEW_TO_CONTINUE = int(os.getenv("MIN_NEW_TO_CONTINUE", "2"))

_stop_on_zero = os.getenv("STOP_ON_ZERO_NEW", "1").strip().lower()
STOP_ON_ZERO_NEW = _stop_on_zero not in ("0", "false", "no")

# Search rounds: each round re-fetches with +1 page depth until target or cap
MAX_SEARCH_ROUNDS = int(os.getenv("SEARCH_ROUNDS", "4"))

# Days since posted (freshness). Default: single age band [7] to avoid redundant multi-pass queries.
_job_age_override = os.getenv("JOB_AGE", "").strip()
_job_age_levels_override = os.getenv("JOB_AGE_LEVELS", "").strip()

if _job_age_override:
    JOB_AGE = int(_job_age_override)
    JOB_AGE_LEVELS = [JOB_AGE]
elif _job_age_levels_override:
    JOB_AGE_LEVELS = sorted(
        int(x.strip()) for x in _job_age_levels_override.split(",") if x.strip()
    )
    JOB_AGE = JOB_AGE_LEVELS[0]
else:
    JOB_AGE_LEVELS = [7]
    JOB_AGE = JOB_AGE_LEVELS[0]
