"""Detect Naukri Easy Apply vs external (company-site) apply."""

from __future__ import annotations

from typing import Any


def _normalize_flags(raw: dict) -> set[str]:
    flags = raw.get("jobTypeFlags") or raw.get("jobFlags") or raw.get("flags") or []
    if isinstance(flags, str):
        flags = [flags]
    if not isinstance(flags, list):
        return set()
    return {str(f).lower().replace("-", "_") for f in flags}


def classify_search_apply_mode(raw: dict) -> bool | None:
    """
    Classify apply mode from a Naukri search/recommended job payload.

    Returns:
        True  - Naukri Easy Apply (API apply)
        False - external / company-site apply
        None  - unknown (no reliable signal in search payload)
    """
    flags = _normalize_flags(raw)
    if "easy_apply" in flags or "easyapply" in flags:
        return True

    rm = raw.get("responseManager")
    if rm == "companyUrl":
        return False
    if rm and rm not in ("companyUrl", ""):
        return True

    mode = (raw.get("mode") or raw.get("applyMode") or "").lower()
    if mode in {"external", "companyurl", "company", "redirect"}:
        return False
    if mode in {"easy", "easyapply", "naukri", "quick"}:
        return True

    if raw.get("showMultipleApply") is True and not flags.intersection({"easy_apply", "easyapply"}):
        return False

    if flags and "walk_in" in flags and not flags.intersection({"easy_apply", "easyapply"}):
        return False

    return None


def is_external_job_details(details: dict | None) -> bool:
    if not isinstance(details, dict):
        return False
    job = details.get("job")
    return isinstance(job, dict) and job.get("responseManager") == "companyUrl"


def external_url_from_job_details(details: dict, job_id: str) -> str | None:
    job = details.get("job") or {}
    for key in (
        "companyUrl",
        "companyApplyUrl",
        "externalApplyUrl",
        "applyRedirectUrl",
        "redirectUrl",
        "webUrl",
    ):
        value = (job.get(key) or "").strip()
        if value.startswith("http"):
            return value
    jd_url = (job.get("jdURL") or job.get("jdUrl") or "").strip()
    if jd_url.startswith("http"):
        return jd_url
    if jd_url.startswith("/"):
        return f"https://www.naukri.com{jd_url}"
    return f"https://www.naukri.com/job-listings-{job_id}"


def external_url_from_apply_result(job_result: dict) -> str | None:
    if not job_result:
        return None
    for key in (
        "applyRedirectUrl",
        "redirectUrl",
        "externalApplyURL",
        "externalApplyUrl",
        "companyUrl",
    ):
        value = (job_result.get(key) or "").strip()
        if value.startswith("http"):
            return value
    if job_result.get("responseManager") == "companyUrl":
        return None
    return None


def apply_result_is_external(job_result: dict) -> bool:
    if not job_result:
        return False
    if job_result.get("responseManager") == "companyUrl":
        return True
    if external_url_from_apply_result(job_result):
        return True
    status = (job_result.get("status") or job_result.get("applyStatus") or "").lower()
    if "external" in status or "redirect" in status:
        return True
    return False
