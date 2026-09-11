"""Load Rawshn application profile from YAML for NopeRi questionnaire autofill."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROFILE_PATH = Path(
    os.getenv(
        "APPLICATION_PROFILE_PATH",
        "/Users/rawshn/Projects/interview-prep/profile/application-profile.yaml",
    )
)

DEFAULT_QUESTIONNAIRE_OVERRIDES_PATH = Path(
    os.getenv(
        "QUESTIONNAIRE_OVERRIDES_PATH",
        "/Users/rawshn/Projects/interview-prep/profile/questionnaire_answers.yaml",
    )
)

_cached_raw: dict[str, Any] | None = None
_cached_profile: dict[str, Any] | None = None


def _flatten_skills(raw: dict[str, Any]) -> list[str]:
    skills_block = raw.get("skills") or {}
    flat: list[str] = []
    for group in ("core_pm", "technical", "tools", "industries"):
        for item in skills_block.get(group) or []:
            token = str(item).strip().lower()
            if token and token not in flat:
                flat.append(token)
    return flat


def _build_user_information_all(raw: dict[str, Any]) -> str:
    identity = raw.get("identity") or {}
    contact = raw.get("contact") or {}
    location = raw.get("location") or {}
    targeting = raw.get("targeting") or {}
    compensation = raw.get("compensation") or {}
    availability = raw.get("availability") or {}
    narrative = raw.get("career_narrative") or {}
    employment = raw.get("employment") or []
    education = (raw.get("education") or [{}])[0]

    recent = employment[0] if employment else {}
    prior_lines = []
    for row in employment[1:4]:
        prior_lines.append(
            f"- {row.get('title', 'N/A')} at {row.get('company', 'N/A')}"
        )

    skill_sample = ", ".join(_flatten_skills(raw)[:20])

    return f"""Name: {identity.get('legal_name', '')}
Location: {location.get('full', '')} (open to remote India)
Email: {contact.get('email_primary', '')}
Phone: {contact.get('phone_display', '')}
LinkedIn: {contact.get('linkedin', '')}
Portfolio: {contact.get('portfolio', '')}
Years of experience: {targeting.get('years_experience_total', '')} total ({targeting.get('years_experience_product', '')}+ in product management)
Notice period: {availability.get('notice_period_text', '')}
Target compensation: {compensation.get('target_annual_lpa', '')} LPA INR ({compensation.get('target_annual_text', '')})
Work authorization: Indian citizen, authorized to work in India

Recent role: {recent.get('title', '')} at {recent.get('company', '')} ({recent.get('start_date', '')} to {recent.get('end_date', '')})
{chr(10).join(prior_lines)}

Career gap (if asked): {narrative.get('gap', {}).get('reason_screening', '')}
Tell me about yourself: {narrative.get('tell_me_about_yourself', '')}

Skills: {skill_sample}
Education: {education.get('degree', '')} {education.get('field', '')}, {education.get('institution_short', education.get('institution', ''))} ({education.get('end_year', '')})
Summary: {raw.get('summary', '')}
"""


def load_raw_profile(path: Path | str | None = None) -> dict[str, Any]:
    global _cached_raw
    profile_path = Path(path) if path else DEFAULT_PROFILE_PATH
    if _cached_raw is not None and profile_path == DEFAULT_PROFILE_PATH:
        return _cached_raw

    with open(profile_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if profile_path == DEFAULT_PROFILE_PATH:
        _cached_raw = data
    return data


def load_application_profile(path: Path | str | None = None) -> dict[str, Any]:
    """Return a flat dict used by questionnaire static rules and AI context."""
    global _cached_profile
    profile_path = Path(path) if path else DEFAULT_PROFILE_PATH
    if _cached_profile is not None and profile_path == DEFAULT_PROFILE_PATH:
        return _cached_profile

    raw = load_raw_profile(profile_path)
    targeting = raw.get("targeting") or {}
    compensation = raw.get("compensation") or {}
    availability = raw.get("availability") or {}
    narrative = raw.get("career_narrative") or {}
    location = raw.get("location") or {}
    domain = targeting.get("domain_experience") or {}

    target_lpa = int(compensation.get("target_annual_lpa", 24))
    target_inr = compensation.get("target_annual_inr")
    if target_inr is None:
        target_inr = target_lpa * 100000

    profile = {
        "legal_name": (raw.get("identity") or {}).get("legal_name", ""),
        "email": (raw.get("contact") or {}).get("email_primary", ""),
        "phone": (raw.get("contact") or {}).get("phone_display", ""),
        "location": location.get("full", ""),
        "city": (location.get("current") or {}).get("city") or location.get("city", "Hyderabad"),
        "willing_to_relocate": bool(location.get("willing_to_relocate", True)),
        "current_ctc": "0",
        "current_ctc_lpa": 0,
        "expected_ctc": str(target_lpa),
        "expected_ctc_lpa": target_lpa,
        "expected_ctc_inr": int(target_inr),
        "exp_total": str(targeting.get("years_experience_total", "6")),
        "exp_product": str(targeting.get("years_experience_product", "3")),
        "exp_infosec": str(targeting.get("years_experience_infosec", "3")),
        "exp_b2c": str(domain.get("b2c_years", 3)),
        "exp_ecommerce": str(domain.get("ecommerce_years", 3)),
        "exp_npd": str(domain.get("npd_years", 3)),
        "exp_pricing_strategy": str(domain.get("pricing_strategy_years", 0)),
        "exp_marketing_strategy": str(domain.get("marketing_strategy_years", 0)),
        "exp_marketing": str(domain.get("marketing_years", 0)),
        "exp_packaging": str(domain.get("packaging_development_years", 0)),
        "notice_days": int(availability.get("notice_period_days", 0)),
        "notice_text": availability.get("notice_period_text", "Immediate joiner"),
        "skills": _flatten_skills(raw),
        "tell_me_about_yourself": narrative.get("tell_me_about_yourself", ""),
        "gap_reason_short": (narrative.get("gap") or {}).get("reason_short", ""),
        "gap_reason_screening": (narrative.get("gap") or {}).get("reason_screening", ""),
        "headline": raw.get("headline", ""),
        "summary": raw.get("summary", ""),
        "user_information_all": _build_user_information_all(raw),
        "raw": raw,
    }

    if profile_path == DEFAULT_PROFILE_PATH:
        _cached_profile = profile
    return profile


_cached_overrides: dict[str, Any] | None = None


def load_questionnaire_overrides(path: Path | str | None = None) -> dict[str, Any]:
    """User-provided prescreening answer overrides (optional yaml)."""
    global _cached_overrides
    overrides_path = Path(path) if path else DEFAULT_QUESTIONNAIRE_OVERRIDES_PATH
    if _cached_overrides is not None and overrides_path == DEFAULT_QUESTIONNAIRE_OVERRIDES_PATH:
        return _cached_overrides

    if not overrides_path.exists():
        empty = {"by_question_id": {}, "by_question_contains": {}}
        if overrides_path == DEFAULT_QUESTIONNAIRE_OVERRIDES_PATH:
            _cached_overrides = empty
        return empty

    with open(overrides_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    overrides = {
        "by_question_id": data.get("by_question_id") or {},
        "by_question_contains": data.get("by_question_contains") or {},
    }
    if overrides_path == DEFAULT_QUESTIONNAIRE_OVERRIDES_PATH:
        _cached_overrides = overrides
    return overrides
