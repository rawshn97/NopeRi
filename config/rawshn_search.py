"""Rawshn PM search defaults for NopeRi apply_agent (USE_RAWSHN_CONFIG=1)."""

import os

# City priority: Hyderabad first, then Pune, then Bangalore (Naukri location labels)
_city_override = os.getenv("RAWSHN_CITY_ORDER", "").strip()
if _city_override:
    CITY_ORDER = [c.strip() for c in _city_override.split(",") if c.strip()]
else:
    CITY_ORDER = ["Hyderabad", "Pune", "Bangalore"]

_kw_override = os.getenv("RAWSHN_PM_KEYWORDS", "").strip()
_expand_kw = os.getenv("RAWSHN_EXPAND_KEYWORDS", "").strip().lower() in ("1", "true", "yes")

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

# Legacy flat list (title x location); apply_agent uses nested sweep instead.
BQUERIES = [
    {"keyword": keyword, "location": city}
    for keyword in PM_KEYWORDS
    for city in CITY_ORDER
]

# Sweep order in apply_agent: job age (fresh first) -> titles -> location -> experience -> pages.
# Anchor experience: exp=4 covers 3-6 YOE (mid/sr PM); exp=2 covers 1-3 YOE (APM/early PM).
# Override: RAWSHN_EXPERIENCE_LEVELS=4,3,2,5,6 (comma-separated integers)
_exp_override = os.getenv("RAWSHN_EXPERIENCE_LEVELS", "").strip()
if _exp_override:
    EXPERIENCE_LEVELS = [int(x.strip()) for x in _exp_override.split(",") if x.strip()]
else:
    EXPERIENCE_LEVELS = [4, 2]

# Adaptive pagination: keep requesting pages while Naukri returns a full page
# (RESULTS_PER_PAGE), stop on a short/empty page, all-duplicate page, or missing pageNo (400).
# RAWSHN_PAGES = floor/ceiling seed for round 1 (also used when raising depth per round).
# RAWSHN_MAX_PAGES = hard cap per title x city x exp x age combo (default 2 to prevent deep paging waste).
# RAWSHN_STOP_ON_ZERO_NEW = stop paging when a full page has 0 new jobs (default on).
# RAWSHN_MIN_NEW_TO_CONTINUE = minimum new jobs on current page to warrant next page (default 2).
RESULTS_PER_PAGE = int(os.getenv("RAWSHN_RESULTS_PER_PAGE", "20"))
PAGES = int(os.getenv("RAWSHN_PAGES", "2"))
MAX_PAGES_PER_QUERY = int(os.getenv("RAWSHN_MAX_PAGES", "2"))
MIN_NEW_TO_CONTINUE = int(os.getenv("RAWSHN_MIN_NEW_TO_CONTINUE", "2"))
STOP_ON_ZERO_NEW = os.getenv("RAWSHN_STOP_ON_ZERO_NEW", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

# Search rounds: each round re-fetches with +1 page depth until target or cap
MAX_SEARCH_ROUNDS = int(os.getenv("RAWSHN_SEARCH_ROUNDS", "4"))

# Days since posted (freshness). Default: single age band [7] to avoid redundant multi-pass queries.
# Override: RAWSHN_JOB_AGE=5 forces single age.
# Override list: RAWSHN_JOB_AGE_LEVELS=3,4,5,6,7
_job_age_override = os.getenv("RAWSHN_JOB_AGE", "").strip()
_job_age_levels_override = os.getenv("RAWSHN_JOB_AGE_LEVELS", "").strip()
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
