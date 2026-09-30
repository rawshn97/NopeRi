#!/usr/bin/env python3
"""Backfill questionnaire_review.jsonl from NopeRi agent-tools terminal captures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.questionnaire_review import backfill_from_captures, DEFAULT_REVIEW_PATH

DEFAULT_CAPTURES: list[Path] = []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture",
        type=Path,
        action="append",
        dest="captures",
        help="Agent-tools capture file (repeatable). Defaults to two Jun 2026 runs.",
    )
    parser.add_argument(
        "--review-path",
        type=Path,
        default=DEFAULT_REVIEW_PATH,
        help="Output JSONL path",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    captures = args.captures or DEFAULT_CAPTURES
    missing = [p for p in captures if not p.exists()]
    if missing:
        raise SystemExit(f"Capture file(s) not found: {', '.join(str(p) for p in missing)}")

    summary = backfill_from_captures(
        captures,
        review_path=args.review_path,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
