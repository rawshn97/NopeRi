import logging
import os
import time
from datetime import datetime

from src.models.models import Job
from src.client.apply_mode import (
    classify_search_apply_mode,
    external_url_from_apply_result,
    external_url_from_job_details,
    is_external_job_details,
)
from src.client.naukri_client import NaukriLoginClient
from src.exceptions.exceptions import NaukriAuthError, NaukriParseError, NaukriRecaptchaError
from src.utils.request_helper import with_exponential_retry
from src.utils.nkparam_generator import generate_nkparam
from src.config.constants import RECOMMENDED_JOBS_URL, JOB_SEARCH_URL, APPLY_JOB_URL
import json

from config.profile_loader import load_application_profile
from src.client.questionnaire import build_smart_answers


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
_handler = logging.StreamHandler()
_handler.setFormatter(
    logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
)
logger.addHandler(_handler)


APPLY_SRC_MAP = {
    "recommended": ("drecomm_apply", "--drecomm_apply-1-F-0-1--{sid}-"),
    "search":      ("srp",           "--srp-1-F-0-1--{sid}-"),
}


# ----------------------------------------------------------------------------------
# NaukriJobClient
#
# A thin client over Naukri's internal APIs using an authenticated session from
# NaukriLoginClient. Handles job search, recommendations, and apply workflows.
#
# Responsibilities:
#   - Build correct headers (authenticated and non-authenticated)
#   - Generate SEO-style keys for the search endpoint
#   - Attach the required nkparam header
#   - Parse raw API responses into the Job model
#   - Normalize inconsistent fields (placeholders, tags, etc.)
#   - Handle common failure cases (403, 406, malformed JSON)
# ----------------------------------------------------------------------------------


# ----------------------------------------------------------------------------------
# nkparam header
#
# nkparam is a signed request header required by the Naukri search API. It is
# generated inside their obfuscated frontend JS and validated server-side.
# A missing or invalid token results in a 403 response.
#
# It is not tied to the login session directly, but to how the frontend signs
# outgoing requests.
# ----------------------------------------------------------------------------------


# ----------------------------------------------------------------------------------
# Supported nkparam modes
#
# 1. Generator mode (default)
#    Uses generate_nkparam("srp") to produce a fresh token per request.
#    Preferred when the generator logic is working correctly.
#
# 2. Pool mode (optional)
#    Uses a list of pre-captured tokens (self.pool), rotated via pool_idx.
#    Useful as a fallback if the generator breaks.
#
# Toggle via:
#    NaukriJobClient(login_client, use_pool=True)
# ----------------------------------------------------------------------------------


# ----------------------------------------------------------------------------------
# Token pool notes
#
# Naukri's search endpoint (/jobapi/v3/search) requires a signed nkparam header.
# This token is generated inside Naukri's obfuscated JS bundle and changes each
# browser session. Without a valid token the API returns 403 Forbidden.
#
# Harvesting tokens:
#   1. Run: python get_Nkparam.py
#      Opens Chrome, captures nkparam from network logs, appends to nkPool.txt.
#   2. Collect roughly 100 tokens for light usage, ~1000 for heavy usage.
#
# Using the pool:
#   self.pool = open("nkPool.txt").read().splitlines()
#
# Token expiry:
#   Tokens typically last a few hours. On 403, rotate to the next token.
#   If all tokens fail, regenerate the pool.
#
# Note: do not commit nkPool.txt. Add it to .gitignore.
# ----------------------------------------------------------------------------------


# ----------------------------------------------------------------------------------
# Design notes
#
# - All helpers live inside the class, no module-level globals.
# - _get_nkparam() abstracts which token source is used.
# - The search path only requires a valid nkparam.
# - The rest of the client is a standard request / parse layer.
# ----------------------------------------------------------------------------------


class NaukriJobClient:

    def __init__(self, login_client: NaukriLoginClient, use_pool: bool = False):
        if not login_client.session:
            raise NaukriAuthError("Login required")

        self._session = login_client.session
        self._client = login_client

        self.pool_idx = 0
        self.use_pool = use_pool

        # Reuse one generated nkparam for several minutes (reduces bot signals).
        self._nkparam_cache: str | None = None
        self._nkparam_cached_at: float = 0.0
        self._nkparam_ttl_s = int(os.getenv("NAUKRI_NKPARAM_TTL", "300"))

        # Seed pool with one pre-captured token as a baseline fallback.
        self.pool = [
            "sa9chfJkrXEpn3Zt7rAPaAOb6gAWNSFzzmPQEc6tLSMzytUGPxrGDqiKJyjvBAHGIYPhbDRBDHMad071ZRZlZA=="
        ]
        self._job_details_cache: dict[str, dict] = {}

    # ----------------------------------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------------------------------

    def _parse_job(self, raw: dict) -> Job:
        # Extract location from the placeholders list if present.
        location = next(
            (p["label"] for p in raw.get("placeholders", []) if p.get("type") == "location"),
            "N/A",
        )
        salary = "Not disclosed"
        for p in raw.get("placeholders") or []:
            if p.get("type") == "salary" and p.get("label"):
                salary = str(p["label"])
                break
        if salary == "Not disclosed":
            detail = raw.get("salaryDetail") or raw.get("salary")
            if isinstance(detail, dict):
                salary = str(
                    detail.get("label")
                    or detail.get("salary")
                    or (
                        f"{detail.get('minimumSalary')}-{detail.get('maximumSalary')}"
                        if detail.get("minimumSalary") or detail.get("maximumSalary")
                        else "Not disclosed"
                    )
                )
            elif isinstance(detail, str) and detail.strip():
                salary = detail.strip()

        return Job(
            job_id=str(raw.get("jobId") or raw.get("id") or ""),
            title=raw.get("title") or raw.get("jobTitle") or "N/A",
            company=raw.get("companyName") or raw.get("company") or "N/A",
            location=location,
            experience=raw.get("experienceText") or raw.get("experience") or "N/A",
            salary=salary,
            posted_date=raw.get("footerPlaceholderLabel") or raw.get("postedDate") or "N/A",
            apply_link=raw.get("jdURL") or f"https://www.naukri.com/job-listings-{raw.get('jobId', '')}",
            description=raw.get("jobDescription") or "",
            tags=(
                [t.strip() for t in raw.get("tagsAndSkills", "").split(",")]
                if raw.get("tagsAndSkills")
                else []
            ),
            easy_apply=classify_search_apply_mode(raw),
        )

    def _cluster_dates(self) -> dict:
        # Returns a dict of current UTC timestamps used by the recommended jobs payload.
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return {
            "apply":        now,
            "preference":   now,
            "profile":      now,
            "similar_jobs": now,
        }

    def _headers(self):
        return self._client._build_headers(auth=True)

    def _build_seo_key(self, keyword: str, location: str, page: int) -> str:
        # Produces the seoKey param expected by the search endpoint.
        # Example: "python-developer-jobs-in-bangalore-2"
        kw_slug = (
            keyword.strip().lower()
            .replace(".", "-dot-")
            .replace(" ", "-")
            .replace("+", "-")
            .strip("-")
        )

        if location.strip():
            loc_slug = location.strip().lower().replace(" ", "-")
            return f"{kw_slug}-jobs-in-{loc_slug}-{page}"

        return f"{kw_slug}-jobs-{page}"

    def format_jobs(self, raw_jobs: list) -> list[dict]:
        # Converts raw job dicts from the API into a flat, readable structure.
        formatted = []

        for job in raw_jobs:
            exp = sal = loc = ""

            for item in job.get("placeholders", []):
                t = item.get("type")
                if t == "experience":
                    exp = item.get("label")
                elif t == "salary":
                    sal = item.get("label")
                elif t == "location":
                    loc = item.get("label")

            formatted.append({
                "title":    job.get("title"),
                "company":  job.get("companyName"),
                "experience": exp,
                "location": loc,
                "salary":   sal,
                "skills":   job.get("tagsAndSkills", "").split(","),
                "job_url":  "https://www.naukri.com" + job.get("jdURL", ""),
                "posted":   job.get("footerPlaceholderLabel"),
            })

        return formatted

    def _get_nkparam(self) -> str:
        # Returns a token from the pool (pool mode) or generates/caches one.
        if self.use_pool:
            token = self.pool[self.pool_idx % len(self.pool)]
            self.pool_idx += 1
            return token
        now = time.time()
        if self._nkparam_cache and (now - self._nkparam_cached_at) < self._nkparam_ttl_s:
            return self._nkparam_cache
        self._nkparam_cache = generate_nkparam("srp")
        self._nkparam_cached_at = now
        return self._nkparam_cache

    def _search_headers(self) -> dict:
        # Builds headers for the search endpoint. Uses non-auth base headers
        # and adds the appid, gid, and nkparam fields required by the search API.
        headers = self._client._build_headers(auth=False)
        headers.update({
            "authority":       "www.naukri.com",
            "accept":          "application/json",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "appid":           "109",
            "gid":             "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE",
            "nkparam":         self._get_nkparam(),
        })
        return headers

    # ----------------------------------------------------------------------------------
    # Job details
    # ----------------------------------------------------------------------------------

    def get_job_details(self, job_id: str, sid: str = "") -> dict:
        if not job_id:
            raise ValueError("job_id is required")

        if not sid:
            sid = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "0000000"

        url = f"https://www.naukri.com/jobapi/v1/job/{job_id}"

        params = {
            "microsite": "y",
            "src":       "jobsearchDesk",
            "sid":       sid,
            "xp":        "1",
            "px":        "1",
        }

        headers = self._client._build_headers(auth=True)
        headers["nkparam"] = self._get_nkparam()
        headers.update({
            "appid":          "121",
            "systemid":       "Naukri",
            "clientid":       "d3skt0p",
            "accept":         "application/json",
            "referer":        "https://www.naukri.com/",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        })

        logger.debug("Fetching job details for job_id=%s sid=%s", job_id, sid)

        res = self._session.get(url, headers=headers, params=params)

        if res.status_code in (401, 403):
            try:
                msg = res.json().get("message", "Auth failed")
            except Exception:
                msg = res.text
            raise NaukriAuthError(msg)

        if not res.ok:
            raise NaukriParseError(f"Job details fetch failed: {res.status_code} — {res.text}")

        try:
            return res.json()
        except Exception:
            raise NaukriParseError(f"Invalid JSON response: {res.text}")

    def get_job_details_cached(self, job_id: str, sid: str = "") -> dict:
        if job_id not in self._job_details_cache:
            self._job_details_cache[job_id] = self.get_job_details(job_id, sid)
        return self._job_details_cache[job_id]

    def is_external_apply(self, job_id: str, sid: str = "") -> bool:
        # Returns True if the job redirects to an external company URL for apply.
        data = self.get_job_details_cached(job_id, sid)
        return is_external_job_details(data)

    def get_external_apply_url(self, job_id: str, sid: str = "") -> str | None:
        """Best-effort company apply URL from job details (falls back to Naukri listing)."""
        data = self.get_job_details_cached(job_id, sid)
        return external_url_from_job_details(data, job_id)

    def external_url_from_apply_response(self, apply_result: dict, job_id: str) -> str | None:
        """Detect external apply from apply-workflow response (no extra details call)."""
        job_result = (apply_result.get("jobs") or [{}])[0]
        url = external_url_from_apply_result(job_result)
        if url:
            return url
        if job_result.get("responseManager") == "companyUrl":
            try:
                return self.get_external_apply_url(job_id)
            except Exception:
                return f"https://www.naukri.com/job-listings-{job_id}"
        return None

    # ----------------------------------------------------------------------------------
    # Apply job
    # ----------------------------------------------------------------------------------

    def apply_job(
        self,
        job: Job,
        mandatory_skills: list[str] = None,
        optional_skills:  list[str] = None,
        sid:    str = "",
        source: str = "recommended",
    ) -> dict:
        url = APPLY_JOB_URL

        if not job.job_id:
            raise ValueError("Invalid job_id")

        if not sid:
            sid = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "0000000"

        apply_src, logstr_template = APPLY_SRC_MAP.get(source, APPLY_SRC_MAP["recommended"])
        logstr = logstr_template.format(sid=sid)

        payload = {
            "strJobsarr":       [job.job_id],
            "logstr":           logstr,
            "flowtype":         "show",
            "crossdomain":      True,
            "jquery":           1,
            "rdxMsgId":         "",
            "chatBotSDK":       True,
            "mandatory_skills": mandatory_skills or [],
            "optional_skills":  optional_skills or [],
            "applyTypeId":      "107",
            "closebtn":         "y",
            "applySrc":         apply_src,
            "sid":              sid,
            "mid":              "",
        }

        headers = self._client._build_headers(auth=True)
        headers.update({
            "appid":     "121",
            "systemid":  "jobseeker",
            "clientid":  "d3skt0p",
            "accept":    "application/json",
        })

        logger.debug("Applying to job_id=%s sid=%s", job.job_id, sid)

        res = self._session.post(url, headers=headers, json=payload)

        if res.status_code in (401, 403):
            try:
                body = res.json()
                msg = (
                    body.get("message")
                    or body.get("error")
                    or body.get("msg")
                    or ""
                ).strip()
            except Exception:
                msg = (res.text or "").strip()[:200]
            if not msg:
                msg = f"Apply rejected (HTTP {res.status_code}; session likely expired)"
            # Naukri often returns 401/403 for daily apply quota, not only auth.
            if any(
                marker in msg.lower()
                for marker in (
                    "daily quota",
                    "quota of jobs exceeded",
                    "quota exceeded",
                    "crossed the limit of your daily quota",
                )
            ):
                raise NaukriParseError(msg)
            raise NaukriAuthError(msg, status_code=res.status_code)

        if not res.ok:
            raise NaukriParseError(f"Apply failed: {res.status_code} — {res.text}")

        try:
            return res.json()
        except Exception:
            raise NaukriParseError(f"Invalid JSON response: {res.text}")

    # ----------------------------------------------------------------------------------
    # Apply job with questionnaire answers
    # ----------------------------------------------------------------------------------

    def handle_static_questionnaire_and_apply(
        self,
        job,
        questionnaire,
        sid,
        mandatory_skills=None,
        optional_skills=None,
        source="recommended",
    ) -> dict:

        profile = load_application_profile()
        job_details = None
        try:
            job_details = self.get_job_details(job.job_id, sid)
        except Exception as exc:
            logger.warning("Could not fetch JD for questionnaire: %s", exc)

        answers, qa_log, low_confidence = build_smart_answers(
            questionnaire,
            profile=profile,
            job_details=job_details,
        )
        logger.debug("Generated answers: %s", answers)
        logger.debug("Q&A log: %s", qa_log)

        if low_confidence:
            logger.warning(
                "Low-confidence questionnaire answers for job_id=%s; caller may skip apply",
                job.job_id,
            )
            return {
                "success": False,
                "skipped": True,
                "reason": "low_confidence_questionnaire",
                "qa_log": qa_log,
                "answers": answers,
            }

        apply_src, logstr_template = APPLY_SRC_MAP.get(source, APPLY_SRC_MAP["recommended"])
        logstr = logstr_template.format(sid=sid)

        payload = {
            "strJobsarr":       [job.job_id],
            "logstr":           logstr,
            "flowtype":         "show",
            "crossdomain":      True,
            "jquery":           1,
            "rdxMsgId":         "",
            "chatBotSDK":       True,
            "mandatory_skills": mandatory_skills or [],
            "optional_skills":  optional_skills or [],
            "applyTypeId":      "107",
            "closebtn":         "y",
            "applySrc":         apply_src,
            "sid":              sid,
            "mid":              "",
            "applyData": {
                job.job_id: {
                    "answers": answers,
                }
            },
        }

        headers = self._client._build_headers(auth=True)
        res = self._session.post(APPLY_JOB_URL, headers=headers, json=payload)

        if not res.ok:
            logger.debug("Apply failed: %s", res.text)
            return {"success": False, "error": res.text, "qa_log": qa_log}

        try:
            payload = res.json()
            payload["qa_log"] = qa_log
            return payload
        except Exception:
            return {"success": False, "error": "Invalid JSON response", "qa_log": qa_log}

    # ----------------------------------------------------------------------------------
    # Recommended jobs
    # ----------------------------------------------------------------------------------

    def get_recommended_jobs(self) -> list[Job]:
        url = RECOMMENDED_JOBS_URL
        res = self._session.post(
            url,
            headers=self._headers(),
            json={
                "clusterId":       None,
                "src":             "recommClusterApi",
                "clusterSplitDate": self._cluster_dates(),
            },
        )

        if not res.ok:
            raise NaukriParseError(f"Recommended jobs fetch failed: {res.status_code}")

        data = res.json()
        raw_jobs = data.get("jobDetails") or []
        print(raw_jobs[:5])
        return [self._parse_job(j) for j in raw_jobs]

    # ----------------------------------------------------------------------------------
    # Search jobs
    # ----------------------------------------------------------------------------------

    def search_jobs(
        self,
        keyword:          str,
        location:         str = "",
        page:             int = 2,
        job_age:          int = 3,
        experience:       int = 2,
        results_per_page: int = 20,
        lat_long:         str = "",
    ) -> list[Job]:

        url     = JOB_SEARCH_URL
        seo_key = self._build_seo_key(keyword, location, page)

        params = {
            "noOfResults":    results_per_page,
            "urlType":        "search_by_keyword",
            "searchType":     "adv",
            "keyword":        keyword,
            "k":              keyword,
            "pageNo":         page,
            "experience":     experience,
            "jobAge":         job_age,
            "nignbevent_src": "jobsearchDeskGNB",
            "seoKey":         seo_key,
            "src":            "jobsearchDesk",
            "latLong":        lat_long,
        }

        recaptcha_backoff = (30, 60, 120)
        res = None
        for attempt in range(len(recaptcha_backoff) + 1):
            res = self._session.get(url, headers=self._search_headers(), params=params)

            if res.status_code == 403:
                raise NaukriAuthError("403 Forbidden — nkparam token likely expired")

            if res.status_code == 406:
                if attempt < len(recaptcha_backoff):
                    wait_s = recaptcha_backoff[attempt]
                    logger.warning(
                        "406 recaptcha on search; waiting %ss then retry (%d/%d)",
                        wait_s,
                        attempt + 1,
                        len(recaptcha_backoff),
                    )
                    time.sleep(wait_s)
                    self._nkparam_cache = None
                    continue
                logger.debug("406 Validation error after retries: %s", res.text)
                # Do NOT return [] here: callers would mistake the recaptcha wall
                # for an empty page and burn the variation as "tried".
                raise NaukriRecaptchaError()

            break

        if res is None:
            return []

        if not res.ok:
            # Naukri returns 400 when pageNo is past the last page for that query.
            # Treat as empty (callers stop paging) instead of a hard PARSE ERROR.
            if res.status_code == 400 and (
                "pageNo" in res.text or "doesn't exists" in res.text or "does not exist" in res.text
            ):
                logger.debug(
                    "Search pageNo past end for keyword=%r page=%d (treating as empty)",
                    keyword,
                    page,
                )
                return []
            raise NaukriParseError(f"Search failed: {res.status_code} — {res.text}")

        data     = res.json()
        raw_jobs = data.get("jobDetails") or data.get("jobs") or []

        # format_jobs is called here for side-effect logging/debugging purposes.
        self.format_jobs(raw_jobs)

        if not raw_jobs:
            logger.debug("No jobs returned for keyword=%r page=%d", keyword, page)
            return []

        return [self._parse_job(j) for j in raw_jobs]