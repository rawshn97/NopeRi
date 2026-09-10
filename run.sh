#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  PY311="/Users/rawshn/.local/bin/python3.11"
  if [[ -x "$PY311" ]]; then
    "$PY311" -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
source .venv/bin/activate
pip install -q -r requirements.txt

# Resume: Easy Apply uses the resume already on your Naukri profile (no local upload).
# Opt-in Drive fetch only when refreshing the profile PDF manually:
#   FETCH_RESUME_FROM_DRIVE=1 ./run.sh
if [[ "${FETCH_RESUME_FROM_DRIVE:-0}" == "1" ]]; then
  FETCH_SCRIPT="/Users/rawshn/Projects/Applying for Jobs/Resume Workflow/scripts/fetch_resume_master.py"
  if [[ -f "$FETCH_SCRIPT" ]]; then
    python3 "$FETCH_SCRIPT" --variant MP-CL || echo "Resume fetch skipped (check composio / Drive)" >&2
  fi
  RESUME_PDF="/Users/rawshn/Projects/Applying for Jobs/Resume Workflow/workspace/Roshan Raj Mishra - MP.pdf"
  if [[ ! -f "$RESUME_PDF" ]]; then
    echo "Missing $RESUME_PDF - export PDF from MP-CL docx before uploading to Naukri." >&2
  fi
fi

LOCAL_ENV="$ROOT/.env"
if [[ -f "$LOCAL_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$LOCAL_ENV"
  set +a
  if [[ -n "${OPENROUTER_API_KEY:-}" && -z "${OPEN_API_KEY:-}" ]]; then
    export OPEN_API_KEY="$OPENROUTER_API_KEY"
  fi
fi

export USE_RAWSHN_CONFIG=1
export OPENAI_API_BASE="${OPENAI_API_BASE:-https://openrouter.ai/api/v1/chat/completions}"
export OPENAI_MODEL="${OPENAI_MODEL:-google/gemini-2.5-flash-lite}"
export MIN_APPLY_SCORE="${MIN_APPLY_SCORE:-70}"

# Apply volume (optional; set before ./run.sh):
#   APPLY_TARGET=100 ./run.sh          # stop when applied_jobs.csv has 100 Easy Apply rows
#   MIN_APPLY_COUNT=68 ./run.sh        # apply at least 68 new jobs this session (100 minus ~32 existing)
#   APPLY_TARGET=100 MIN_APPLY_COUNT=68 ./run.sh   # both (uses the larger session goal)
# Search depth (optional; default uses full title×city×exp×freshness sweep):
#   RAWSHN_PAGES=5 RAWSHN_SEARCH_ROUNDS=4 ./run.sh
#   EXHAUST_JOBS=1 ./run.sh   # all rounds + raised AI/apply caps (default on via .env)
# Do NOT pass RAWSHN_JOB_AGE=5 for quota runs (limits freshness to one day).
# Variation resume: search_variation_state.json skips prior API combos automatically.
# Classifier cap (optional, auto-raised when APPLY_TARGET/MIN_APPLY_COUNT set):
#   DAILY_APPLY_LIMIT=100 ./run.sh

python apply_agent.py "$@"
AGENT_EXIT=$?

SYNC_SCRIPT="/Users/rawshn/Projects/Applying for Jobs/scripts/sync_noperi_notion.py"
EXTERNAL_SYNC="/Users/rawshn/Projects/Applying for Jobs/scripts/sync_external_notion.py"
if [[ -f "$SYNC_SCRIPT" ]]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null)}"
  python3 "$SYNC_SCRIPT" || echo "Notion sync skipped (check NOTION_TOKEN)" >&2
fi
if [[ -f "$EXTERNAL_SYNC" ]]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null)}"
  python3 "$EXTERNAL_SYNC" || echo "External Notion sync skipped (check NOTION_TOKEN)" >&2
fi
exit "$AGENT_EXIT"
