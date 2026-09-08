# NopeRi Naukri auto-applier (Rawshn)

Official repo: [Traverser25/NopeRi](https://github.com/Traverser25/NopeRi)

**Path:** `/Users/rawshn/Projects/NopeRi`

## Stack role

| Tool | Role |
|------|------|
| **NopeRi** | Naukri Easy Apply (API client + AI agent) |
| Naukri profile | Resume already uploaded on Naukri (used for Easy Apply) |

## Why this repo

README in [Applying for Jobs](../Applying%20for%20Jobs/README.md) references **NopeRi** by name. [Traverser25/NopeRi](https://github.com/Traverser25/NopeRi) is a Selenium-free Python API client with an `apply_agent.py` AI workflow (last tested Jun 2026 per upstream).

## Config (Rawshn overrides)

| File | What |
|------|------|
| `config/rawshn_search.py` | PM + Senior BA / BA titles, Hyderabad + Bangalore + Pune, exp levels 4→3→2→5→6 (no 1 YOE), adaptive pages |
| `config/rawshn_classifier.py` | PM + BA scoring; BA not hard-vetoed; specific analyst vetoes only |
| `config/ba_salary.py` | BA titles apply only if posted salary is **> 20 LPA** (undisclosed skipped at apply time) |
| `config/profile_loader.py` | Loads `application-profile.yaml` for Q&A autofill |
| `.env` | Naukri login + OpenRouter (gitignored) |
| `run.sh` | venv, `USE_RAWSHN_CONFIG=1`, runs agent (no Drive resume fetch by default) |

Upstream defaults target backend developers. `run.sh` enables Rawshn PM config automatically.

## Questionnaire autofill (JD-aware)

When an employer prescreening form appears after Easy Apply:

**Easy Apply vs external (API savings):** Search results include `jobTypeFlags` (look for `easy_apply`) and sometimes `responseManager`. Jobs flagged external, or already in `external_jobs.jsonl`, are dropped **before AI scoring**. At submit time NopeRi calls the apply API once and checks the response for redirect URLs (no double `get_job_details` pre-check). Probe flag coverage: `python3 scripts/probe_search_apply_flags.py`. Optional `STRICT_EASY_APPLY=1` skips jobs with no search flag (aggressive).

1. **Profile source:** `~/Projects/Applying for Jobs/profile/application-profile.yaml` (override with `APPLICATION_PROFILE_PATH`).
2. **Static rules first:** CTC (24 LPA / 2400000 INR expected, 0 current; auto-detects lacs vs INR from question text), notice (0 / immediate), YOE (6 total, 3 product; domain overrides for B2C/e-commerce/NPD=3, marketing/pricing/packaging=0), relocation Yes, city Hyderabad, email, phone, gap reason, tell-me-about-yourself.
3. **JD context:** Fetches full job details via `get_job_details()` (title, tags, description).
4. **AI fallback:** OpenRouter (`OPENROUTER_API_KEY` or `OPEN_API_KEY`) answers unknown text/select questions using GodsScion-style prompt + profile context.
5. **Select/radio:** AI returns an option label; matcher maps it to Naukri `answerOption` key.
6. **Safety:** Low-confidence answers skip submit and log Q&A to `questionnaire_review.jsonl` for your review.

Update the yaml profile when compensation, notice, or narrative changes. Add prescreening overrides in `questionnaire_answers.yaml` (see Applying for Jobs profile). No code edits needed for routine updates.

**Classifier veto:** Marketing Manager titles (including Associate/Digital variants) are hard-vetoed in `rawshn_classifier.py` (no marketing management experience).

**BA salary gate:** Business Analyst titles (including Senior/Lead/Associate BA) apply only when posted salary is **strictly greater than 20 LPA**. Ranges apply if the ceiling is above 20 (e.g. 18-25 applies; 10-20 skips). Undisclosed BA salary is skipped after a job-details lookup. Product Manager titles are not salary-gated. Override floor with `BA_MIN_SALARY_LPA` (default `20`).

## Questionnaire review log

Every prescreening form (submitted or skipped for low confidence) is appended to:

`~/Projects/NopeRi/questionnaire_review.jsonl`

One JSON object per job/questionnaire event. Fields: `job_id`, `title`, `company`, `status` (`submitted` | `skipped_low_confidence`), `qa_log`, `naukri_job_url`, optional `notion_url` after sync.

**Review workflow:** open the JSONL file, note wrong answers, then either:

1. Add overrides to `~/Projects/Applying for Jobs/profile/questionnaire_answers.yaml`, or
2. Tell the agent which questions need different answers (paste job_id + corrections).

Backfill past terminal captures:

```bash
python3 ~/Projects/NopeRi/scripts/backfill_questionnaire_review.py
```

## Before first run

1. Copy env template and fill credentials:
   ```bash
   cp .env.example .env
   ```
2. Set in `.env`:
   - `USERNAME` / `PASSWORD` (Naukri login)
   - `OPENROUTER_API_KEY` (or `OPEN_API_KEY` for OpenAI direct)
3. Ensure your resume is already uploaded on [Naukri](https://www.naukri.com/mnjuser/profile). Easy Apply uses that profile resume; NopeRi does not upload a local PDF during `./run.sh`.

To refresh the resume on Naukri manually (optional, not part of normal runs):

```bash
cd ~/Projects/NopeRi
source .venv/bin/activate
python -c "
from pathlib import Path
from src.client.naukri_client import NaukriLoginClient
import os
from dotenv import load_dotenv
load_dotenv()
pdf = Path('~/Projects/Applying for Jobs/Resume Workflow/workspace/Roshan Raj Mishra - MP.pdf').expanduser()
c = NaukriLoginClient(os.environ['USERNAME'], os.environ['PASSWORD'])
c.login()
c.update_resume(str(pdf))
"
```

Or set `FETCH_RESUME_FROM_DRIVE=1` before `./run.sh` only when you want to pull MP-CL from Drive first (then upload via `main.py` / `update_resume()` yourself).

## Run

```bash
cd ~/Projects/NopeRi
chmod +x run.sh   # once
./run.sh
```

### Target 100 cumulative Easy Applies

You have ~32 rows in `applied_jobs.csv` already. To reach **100 total** Naukri Easy Apply submissions logged in CSV:

```bash
cd ~/Projects/NopeRi
APPLY_TARGET=100 ./run.sh
```

That stops when `applied_jobs.csv` has 100 rows. To require at least **68 new** applies in one session (100 minus current CSV count):

```bash
MIN_APPLY_COUNT=68 ./run.sh
```

Both together (session goal uses whichever is larger):

```bash
APPLY_TARGET=100 MIN_APPLY_COUNT=68 ./run.sh
```

**What counts toward 100:** only successful **Naukri Easy Apply** submissions written to `applied_jobs.csv`. **External apply** jobs (company site redirect) are skipped by the agent, logged to `external_jobs.jsonl`, and synced to Notion with **Job Status: Opening** (see `~/Projects/Applying for Jobs/scripts/sync_external_notion.py`). Dedup skips jobs already in CSV.

**Search expansion:** Rawshn sweeps Naukri in nested order: **titles → location → experience → freshness → pages** (title changes last). Easy Apply jobs are classified and applied **per search chunk** as found (no full-sweep wait). Default: 10 PM titles × 3 cities (Hyderabad, Pune, Bangalore) × 5 experience levels (`4,3,2,5,6`; exp=1 omitted) × 5 freshness days (`3,4,5,6,7`). **Adaptive pages:** keep requesting the next page while Naukri returns a full page (`20` results); stop on a short/empty page, when a page is all duplicates (`20 fetched 0 new`), or past-end `pageNo` (no more noisy 400 FAILs). Hard cap `RAWSHN_MAX_PAGES` (default `6`). Zero-new stop is on by default (`RAWSHN_STOP_ON_ZERO_NEW=1`). Up to **4 search rounds** until `MIN_APPLY_COUNT` / `APPLY_TARGET` is met, listings dry up, or the round cap is hit. Override with `RAWSHN_MAX_PAGES=10`, `RAWSHN_STOP_ON_ZERO_NEW=0`, `RAWSHN_SEARCH_ROUNDS=4`, `RAWSHN_EXPERIENCE_LEVELS=4,3`, `RAWSHN_JOB_AGE_LEVELS=3,5,7`, or `RAWSHN_JOB_AGE=5` (single age, disables freshness cycling).

**EXHAUST_JOBS** (`EXHAUST_JOBS=1`, default on in `.env`): keep expanding through all search rounds instead of stopping after one pass; also raises `DAILY_APPLY_LIMIT` to 999 and `AI_SCORE_LIMIT` to 500 so more listings get scored and applied per round. Pair with `MIN_APPLY_COUNT` or `APPLY_TARGET` to stop once the session goal is met.

**Search variation resume:** Each title×city×exp×age×page API call is logged to `~/Projects/NopeRi/search_variation_state.json`. The next run **skips already-tried variations** and continues the sweep. Use the full config defaults (all freshness days via `JOB_AGE_LEVELS`; do **not** pass `RAWSHN_JOB_AGE=5` unless you want a single age). **Do not** lower `MIN_APPLY_SCORE` (default `50`) to force applies; exhaust the variation space instead.

Backfill state from prior tee logs:

```bash
python3 scripts/backfill_search_variations.py runs/*-noperi-*.log
```

Reset the variation log to re-scan from scratch: `RESET_SEARCH_VARIATIONS=1 ./run.sh`

**Realistic expectations:** One session may not hit 100. Recent runs applied 26, then 5, then 1 (most listings were filtered, deduped, external, or already applied). Plan **multiple runs over several days** as Naukri posts new PM listings. Classifier still vetoes marketing manager titles and other hard filters.

| Env var | Purpose |
|---------|---------|
| `APPLY_TARGET` | Cumulative CSV total to reach (e.g. `100`) |
| `MIN_APPLY_COUNT` | Minimum new applies this session (e.g. `68`) |
| `RAWSHN_PAGES` | Legacy seed (kept for env compat; adaptive paging uses `RAWSHN_MAX_PAGES`) |
| `RAWSHN_MAX_PAGES` | Hard cap of pages per title×city×exp×age combo (default `6`); stop earlier on short or all-duplicate page |
| `RAWSHN_STOP_ON_ZERO_NEW` | Stop paging a combo when a page has 0 new jobs (default `1`; set `0` to disable) |
| `RAWSHN_SEARCH_ROUNDS` | Re-fetch rounds (default `4`) |
| `RAWSHN_EXPERIENCE_LEVELS` | Comma-separated exp levels in sweep (default `4,3,2,5,6`) |
| `RAWSHN_JOB_AGE_LEVELS` | Comma-separated freshness days in sweep (default `3,4,5,6,7`) |
| `RAWSHN_JOB_AGE` | Force single max job age in days (e.g. `5`); disables freshness cycling |
| `EXHAUST_JOBS` | Set to `1` to run all search rounds with raised apply/AI caps (default on in `.env`) |
| `STRICT_EASY_APPLY` | Set to `1` to skip search results with no `easy_apply` flag (default `0`) |
| `DAILY_APPLY_LIMIT` | Max jobs passed to apply loop after AI filter (auto-raised with targets) |
| `NAUKRI_DAILY_QUOTA` | Naukri rolling 24h apply cap (default `50`); stop apply loop when reached |
| `MIN_APPLY_SCORE` | AI apply threshold (default `50`; do not lower to force applies) |
| `SEARCH_VARIATION_STATE_PATH` | Override path for variation resume log (default `search_variation_state.json`) |
| `RESET_SEARCH_VARIATIONS` | Set to `1` to clear variation log and re-scan from scratch |
| `FETCH_RESUME_FROM_DRIVE` | Set to `1` to pull MP-CL from Drive before run (default off; Easy Apply still uses Naukri profile resume) |

Manual (without Rawshn PM config):

```bash
source .venv/bin/activate
python apply_agent.py
```

## Env vars (user must fill)

| Variable | Required | Purpose |
|----------|----------|---------|
| `USERNAME` | Yes | Naukri email |
| `PASSWORD` | Yes | Naukri password |
| `OPENROUTER_API_KEY` | Yes* | AI job scoring via OpenRouter |
| `OPEN_API_KEY` | Alt* | Direct OpenAI key instead of OpenRouter |
| `OPENAI_API_BASE` | No | Default `https://openrouter.ai/api/v1/chat/completions` in `run.sh` |
| `OPENAI_MODEL` | No | Default `google/gemini-2.5-flash-lite` |
| `APPLICATION_PROFILE_PATH` | No | Default `~/Projects/Applying for Jobs/profile/application-profile.yaml` |
| `APPLY_TARGET` | No | Cumulative Easy Apply rows in CSV to reach (e.g. `100`) |
| `MIN_APPLY_COUNT` | No | Minimum new applies this session |
| `RAWSHN_PAGES` | No | Legacy seed (adaptive paging uses `RAWSHN_MAX_PAGES`) |
| `RAWSHN_MAX_PAGES` | No | Hard cap pages per search combo (default `6`); stop earlier when page is short or all duplicates |
| `RAWSHN_STOP_ON_ZERO_NEW` | No | Stop paging when a page has 0 new jobs (default `1`; set `0` to keep paging) |
| `RAWSHN_SEARCH_ROUNDS` | No | Search rounds with expanding pages (default `4`) |
| `RAWSHN_EXPERIENCE_LEVELS` | No | Comma-separated exp levels in sweep (default `4,3,2,5,6`) |
| `RAWSHN_JOB_AGE_LEVELS` | No | Comma-separated freshness days in sweep (default `3,4,5,6,7`) |
| `RAWSHN_JOB_AGE` | No | Single max job age in days; disables freshness cycling |
| `EXHAUST_JOBS` | No | `1` = all search rounds + raised apply/AI caps (default on in `.env`) |
| `DAILY_APPLY_LIMIT` | No | Classifier apply cap per run (default `50`, auto-raised with targets) |
| `NAUKRI_DAILY_QUOTA` | No | Naukri rolling 24h apply cap (default `50`); agent stops cleanly when hit |

\* One of `OPENROUTER_API_KEY` or `OPEN_API_KEY` is required for `apply_agent.py`.

## Applied jobs log

Local CSV: `applied_jobs.csv` (dedup across runs).

After each run, `run.sh` syncs new rows to the **Notion Job Application Tracker** (same DB as GodsScion / `job-board-apply`). Requires `NOTION_TOKEN` or `NOTION_API_KEY` in your shell.

Manual sync anytime:

```bash
python3 ~/Projects/Applying\ for\ Jobs/scripts/sync_noperi_notion.py
```

Dry run: add `--dry-run`. State file: `~/Projects/Applying for Jobs/runs/noperi-notion-synced.json`.

## Safety / hosting notes (from upstream)

- Run from **home broadband** or residential IP. Naukri fingerprints datacenter IPs (Azure, GitHub Actions often blocked).
- Sessions are IP-bound; changing IP invalidates login.
- OTP/MFA login is supported upstream.
- Review Naukri ToS before high-volume auto-apply.

## Troubleshooting

- **403 on job search:** `nkparam` token issue; see upstream README for `nkparam_generator.py` / `get_Nkparam.py`.
- **Wrong resume on applications:** update the resume on your Naukri profile (or use `update_resume()` manually); `./run.sh` does not replace it.
- **AI errors:** confirm model on OpenRouter; refresh key in `.env`.
- **PM jobs vetoed:** confirm you use `./run.sh` (sets `USE_RAWSHN_CONFIG=1`).

## Related

- [Applying for Jobs README](../Applying%20for%20Jobs/README.md)
- [GodsScion LinkedIn setup](../Auto_job_applier_linkedIn/SETUP-RAWSHN.md)
- [application-profile.yaml](../Applying%20for%20Jobs/profile/application-profile.yaml)
