"""Business Analyst salary gate for NopeRi (Rawshn).

Apply to BA titles only when posted salary is strictly greater than 20 LPA.
Product Manager and other non-BA titles are not gated.
"""

from __future__ import annotations

import os
import re
from typing import Any

BA_MIN_LPA = float(os.getenv("BA_MIN_SALARY_LPA", "20"))

_BA_TITLE_RE = re.compile(
    r"\bbusiness\s+analyst\b"
    r"|\bsenior\s+ba\b"
    r"|\bsr\.?\s*ba\b"
    r"|\blead\s+ba\b"
    r"|\bassociate\s+ba\b"
    r"|\bba\s*[-/]"
    r"|[-/]\s*ba\b",
    re.I,
)

_UNDISCLOSED_RE = re.compile(
    r"not\s+disclosed|undisclosed|unpaid|n/?a|confidential",
    re.I,
)

_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def is_business_analyst_title(title: str | None) -> bool:
    text = (title or "").strip()
    if not text:
        return False
    return bool(_BA_TITLE_RE.search(text))


def _coerce_number(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _to_lpa(value: float, *, as_inr: bool) -> float:
    if as_inr or value >= 100_000:
        return value / 100_000.0
    return value


def parse_salary_lpa(text: Any) -> tuple[float | None, float | None]:
    """Return (min_lpa, max_lpa). Both None when salary is unknown."""
    if text is None:
        return None, None

    if isinstance(text, dict):
        mn = _coerce_number(text.get("minimumSalary") or text.get("min"))
        mx = _coerce_number(text.get("maximumSalary") or text.get("max"))
        if mn is not None or mx is not None:
            as_inr = any(
                v is not None and v >= 100_000 for v in (mn, mx)
            )
            min_lpa = _to_lpa(mn, as_inr=as_inr) if mn is not None else None
            max_lpa = _to_lpa(mx, as_inr=as_inr) if mx is not None else None
            return min_lpa, max_lpa or min_lpa
        text = text.get("label") or text.get("salary")

    s = str(text or "").strip()
    if not s or _UNDISCLOSED_RE.search(s):
        return None, None

    low = s.lower()
    as_inr = bool(re.search(r"\b(inr|rs\.?|₹)\b", low)) or "000" in s.replace(",", "")
    if re.search(r"\b(lacs?|lakhs?|lpa)\b", low):
        as_inr = False

    nums = []
    for match in _NUM_RE.findall(s.replace(",", "")):
        n = _coerce_number(match)
        if n is not None:
            nums.append(_to_lpa(n, as_inr=as_inr))
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums[0], nums[1]), max(nums[0], nums[1])


def ba_salary_allows_apply(title: str | None, salary: Any) -> tuple[bool, str]:
    """Whether this listing may be applied to under the BA salary rule."""
    if not is_business_analyst_title(title):
        return True, "not-ba"
    min_lpa, max_lpa = parse_salary_lpa(salary)
    ceiling = max_lpa if max_lpa is not None else min_lpa
    label = str(salary).strip() if salary not in (None, "") else "Not disclosed"
    if isinstance(salary, dict):
        label = str(salary.get("label") or salary)
    if ceiling is None:
        return False, f"{label} (undisclosed; need >{BA_MIN_LPA:g} LPA)"
    if ceiling > BA_MIN_LPA:
        return True, f"{label} (>{BA_MIN_LPA:g} LPA)"
    return False, f"{label} (need >{BA_MIN_LPA:g} LPA)"


def salary_label_from_raw(raw: dict | None) -> str:
    """Best salary string from a Naukri search or details payload."""
    if not isinstance(raw, dict):
        return "Not disclosed"

    job = raw.get("jobDetails") or raw.get("job") or raw
    if not isinstance(job, dict):
        job = raw

    for item in job.get("placeholders") or []:
        if item.get("type") == "salary" and item.get("label"):
            return str(item["label"])

    detail = job.get("salaryDetail") or job.get("salary") or raw.get("salaryDetail")
    if isinstance(detail, dict):
        label = detail.get("label") or detail.get("salary")
        if label:
            return str(label)
        mn, mx = detail.get("minimumSalary"), detail.get("maximumSalary")
        if mn or mx:
            return f"{mn}-{mx}"
    if isinstance(detail, str) and detail.strip():
        return detail.strip()

    return "Not disclosed"


def salary_text_from_job_details(data: dict | None) -> str:
    return salary_label_from_raw(data)
