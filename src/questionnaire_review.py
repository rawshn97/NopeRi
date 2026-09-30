"""Persist Naukri prescreening Q&A for user review and agent corrections."""

from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_REVIEW_PATH = Path(
    os.getenv(
        "QUESTIONNAIRE_REVIEW_PATH",
        "questionnaire_review.jsonl",
    )
)

JOB_HEADER_RE = re.compile(
    r"Title\s+:\s+(?P<title>.+?)\s*\n\s*Company\s+:\s+(?P<company>.+?)\s*\n\s*Job ID\s+:\s+(?P<job_id>\d+)",
    re.MULTILINE,
)
QA_LOG_RE = re.compile(r"Q&A log:\s*(\[.+\])\s*$", re.MULTILINE)
APPLIED_RE = re.compile(
    r"Status\s+:\s+Applied successfully\s+at\s+(?P<time>\d{2}:\d{2}:\d{2}\s+UTC)"
)
SKIPPED_RE = re.compile(
    r"Status\s+:\s+Skipped\s+-\s+(?P<reason>low_confidence_questionnaire)"
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def naukri_job_url(job_id: str) -> str:
    return f"https://www.naukri.com/job-listings-{job_id}"


def build_review_record(
    *,
    job_id: str,
    title: str,
    company: str,
    status: str,
    qa_log: list[dict[str, Any]],
    applied_at: str | None = None,
    skipped_at: str | None = None,
    notion_url: str | None = None,
    naukri_url: str | None = None,
    source_run: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "status": status,
        "applied_at": applied_at,
        "skipped_at": skipped_at,
        "qa_log": qa_log,
        "notion_url": notion_url,
        "naukri_job_url": naukri_url or naukri_job_url(job_id),
        "logged_at": utcnow_iso(),
        "source_run": source_run,
    }


def append_questionnaire_review(
    record: dict[str, Any],
    path: Path | str | None = None,
) -> Path:
    """Append one JSON object per line to the review log."""
    review_path = Path(path) if path else DEFAULT_REVIEW_PATH
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with review_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return review_path


def load_reviews(path: Path | str | None = None) -> list[dict[str, Any]]:
    review_path = Path(path) if path else DEFAULT_REVIEW_PATH
    if not review_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with review_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def latest_review_by_job_id(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Return the most recent review record per job_id."""
    by_job: dict[str, dict[str, Any]] = {}
    for record in load_reviews(path):
        job_id = str(record.get("job_id") or "").strip()
        if job_id:
            by_job[job_id] = record
    return by_job


def existing_job_ids(path: Path | str | None = None) -> set[str]:
    return set(latest_review_by_job_id(path).keys())


def format_qa_for_comments(qa_log: list[dict[str, Any]], *, max_len: int = 1200) -> str:
    if not qa_log:
        return ""
    lines = ["Prescreening Q&A:"]
    for row in qa_log:
        question = (row.get("question") or "").strip()
        answer = row.get("answer")
        if isinstance(answer, list):
            answer_text = ", ".join(str(a) for a in answer)
        else:
            answer_text = str(answer)
        source = row.get("source", "?")
        confidence = row.get("confidence", "?")
        lines.append(f"- Q: {question}")
        lines.append(f"  A: {answer_text} ({source}/{confidence})")
    text = "\n".join(lines)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def update_notion_url_for_job(job_id: str, notion_url: str, path: Path | str | None = None) -> bool:
    """Patch the latest record for job_id with notion_url (rewrite file)."""
    review_path = Path(path) if path else DEFAULT_REVIEW_PATH
    records = load_reviews(review_path)
    if not records:
        return False

    updated = False
    for idx in range(len(records) - 1, -1, -1):
        if str(records[idx].get("job_id")) == str(job_id):
            records[idx]["notion_url"] = notion_url
            updated = True
            break

    if updated:
        with review_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return updated


def _parse_qa_log(raw: str) -> list[dict[str, Any]]:
    try:
        data = ast.literal_eval(raw)
        if isinstance(data, list):
            return data
    except (SyntaxError, ValueError):
        pass
    return []


def _job_context_before(text: str, qa_pos: int) -> tuple[str, str, str]:
    chunk = text[:qa_pos]
    matches = list(JOB_HEADER_RE.finditer(chunk))
    if not matches:
        return "", "", ""
    last = matches[-1]
    return (
        last.group("job_id").strip(),
        last.group("title").strip(),
        last.group("company").strip(),
    )


def _status_after(text: str, qa_pos: int) -> tuple[str, str | None, str | None]:
    tail = text[qa_pos: qa_pos + 800]
    if SKIPPED_RE.search(tail):
        return "skipped_low_confidence", None, utcnow_iso()
    applied = APPLIED_RE.search(tail)
    if applied:
        return "submitted", applied.group("time"), None
    return "submitted", None, None


def parse_agent_tools_capture(
    capture_path: Path | str,
    *,
    source_run: str | None = None,
) -> list[dict[str, Any]]:
    """Parse NopeRi terminal capture files into review records."""
    text = Path(capture_path).read_text(encoding="utf-8", errors="replace")
    records: list[dict[str, Any]] = []

    for match in QA_LOG_RE.finditer(text):
        qa_log = _parse_qa_log(match.group(1))
        if not qa_log:
            continue
        job_id, title, company = _job_context_before(text, match.start())
        if not job_id:
            continue
        status, applied_at, skipped_at = _status_after(text, match.end())
        records.append(
            build_review_record(
                job_id=job_id,
                title=title,
                company=company,
                status=status,
                qa_log=qa_log,
                applied_at=applied_at,
                skipped_at=skipped_at,
                source_run=source_run or Path(capture_path).name,
            )
        )
    return records


def backfill_from_captures(
    capture_paths: list[Path | str],
    *,
    review_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Append records from capture files, skipping job_ids already logged."""
    path = Path(review_path) if review_path else DEFAULT_REVIEW_PATH
    seen = existing_job_ids(path)
    appended = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    for capture in capture_paths:
        for record in parse_agent_tools_capture(capture):
            job_id = record["job_id"]
            if job_id in seen:
                skipped += 1
                results.append({"job_id": job_id, "action": "skipped_duplicate"})
                continue
            if not dry_run:
                append_questionnaire_review(record, path)
            seen.add(job_id)
            appended += 1
            results.append({"job_id": job_id, "action": "appended", "status": record["status"]})

    return {
        "review_path": str(path),
        "appended": appended,
        "skipped_duplicates": skipped,
        "dry_run": dry_run,
        "results": results,
    }
