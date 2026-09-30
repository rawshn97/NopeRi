#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  if command -v python3.11 >/dev/null 2>&1; then
    python3.11 -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
source .venv/bin/activate
pip install -q -r requirements.txt

# Resume: Easy Apply uses the master resume already uploaded to your Naukri profile.
# (Ensure your resume is uploaded at https://www.naukri.com/mnjuser/profile before running).

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

export USE_ADVANCED_CONFIG=1
export OPENAI_API_BASE="${OPENAI_API_BASE:-https://openrouter.ai/api/v1/chat/completions}"
export OPENAI_MODEL="${OPENAI_MODEL:-google/gemini-2.5-flash-lite}"
export MIN_APPLY_SCORE="${MIN_APPLY_SCORE:-70}"

# Optional apply volume controls (e.g., APPLY_TARGET=100 ./run.sh or MIN_APPLY_COUNT=50 ./run.sh)
# Optional search depth controls (e.g., PAGES=5 SEARCH_ROUNDS=4 ./run.sh)
# Optional search variation reset (e.g., RESET_SEARCH_VARIATIONS=1 ./run.sh)

python apply_agent.py "$@"
AGENT_EXIT=$?

SYNC_SCRIPT="${NOTION_SYNC_SCRIPT:-scripts/sync_noperi_notion.py}"
EXTERNAL_SYNC="${EXTERNAL_SYNC_SCRIPT:-scripts/sync_external_notion.py}"
if [[ -f "$SYNC_SCRIPT" ]]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null)}"
  python3 "$SYNC_SCRIPT" || echo "Notion sync skipped (check NOTION_TOKEN)" >&2
fi
if [[ -f "$EXTERNAL_SYNC" ]]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null)}"
  python3 "$EXTERNAL_SYNC" || echo "External Notion sync skipped (check NOTION_TOKEN)" >&2
fi
exit "$AGENT_EXIT"
