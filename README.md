<p align="center">
  <img src="assests/logo2.svg" alt="noperi-logo" width="680"/>
</p>

# NopeRi: Autonomous Naukri Job Application Pipeline

> **Fork Notice:** This repository is an enhanced production fork of [Traverser25/NopeRi](https://github.com/Traverser25/NopeRi), transforming the raw Selenium-free API client into a fully automated, quota-aware job application pipeline with multi-dimensional search sweeps, AI evaluation, and two-tier prescreening form autofill.

---

## What This Fork Adds

The upstream repository provides foundational low-level REST client capabilities (login, token generation, search, apply). This fork introduces complete production infrastructure for hands-off daily execution:

1. **Multi-Dimensional Freshness Sweep:**
   - Sweeps queries in priority order: `Freshness (Age 3 -> 4 -> 5 -> 6 -> 7 days) x Target Titles x Cities x Experience Levels x Adaptive Pages`.
   - Prioritizes the freshest job listings first, expanding to older listings only when session targets require it.

2. **Search Variation State Machine (`search_variation_state.json`):**
   - Persists state for every `(title, city, experience, posting_age, page)` permutation across runs.
   - Automatically skips already-queried slices on subsequent runs, eliminating duplicate requests and saving network quota.

3. **Adaptive Pagination & Anti-Bot Protection:**
   - Dynamically requests next pages only when a full 20-job page returns; stops early on short pages, all-duplicate pages (`STOP_ON_ZERO_NEW=1`), or pagination boundaries.
   - Includes randomized request jitter and micro-batch cooling pauses to prevent rate limits and IP blocks.

4. **Pre-AI External ATS Filtering:**
   - Filters out third-party redirect links (Workday, Greenhouse, Taleo) before AI scoring using `jobTypeFlags` and `external_jobs.jsonl`.
   - Preserves 70%+ of inference tokens and logs external jobs for manual review.

5. **Two-Tier Questionnaire Engine:**
   - **Tier 1 (Deterministic Static Profile):** `application-profile.yaml` handles standard questions (current CTC, expected CTC, notice period, relocation, YOE) deterministically with zero hallucinations.
   - **Tier 2 (AI Fallback with Confidence Gating):** Novel questions are evaluated by LLM. If confidence is below 0.85, the submission aborts safely and logs the question for review.

6. **Prescreening Review Log (`questionnaire_review.jsonl`):**
   - Captures skipped questions with confidence scores and context.
   - Enables human-in-the-loop review: add answers to `questionnaire_answers.yaml` and re-run.

7. **Role-Agnostic Salary Floor Gating (`config/ba_salary.py`):**
   - Enforces a configurable minimum salary threshold (`MIN_SALARY_LPA`, default 20 LPA) across all roles, preventing applications to low-comp listings.
   - Supports `ALLOW_UNDISCLOSED_SALARY=1` to permit listings where compensation is confidential or unstated.

8. **24-Hour Rolling Quota Enforcement:**
   - Automatically tracks applications against Naukri's official rolling 24-hour limit of 50 Easy Applies, stopping gracefully when reached.

9. **Cursor Agent Skill (`.cursor/skills/noperi/SKILL.md`):**
   - Built-in Cursor slash command (`/noperi` or "Run NopeRi") that triggers the application workflow, reports skipped questions in chat, and handles interactive auto-retries.

10. **Autonomous Quota Scheduler (Configurable Time):**
    - Scheduler daemon (`scripts/run_scheduled_once.py`) that waits until your chosen time then fires under `caffeinate` (default: 00:10 IST).
    - Pass `--time HH:MM` (24-hour IST) or set `SCHEDULED_TIME_IST` env var to pick any run time.
    - Laptop must remain powered on and awake; `caffeinate -ims` prevents sleep on AC power.

---

## System Architecture

```
[ Trigger: ./run.sh or Cursor /noperi ]
             │
             ▼
   [ Session & Auth Engine ] ── Bearer Token & Dynamic nkparam
             │
             ├─► Freshness Sweep (Age: 3 -> 4 -> 5 -> 6 -> 7 days)
             ├─► State Resume (search_variation_state.json)
             └─► Adaptive Pagination (stop on short or duplicate page)
             │
             ▼
   [ Pre-AI Flag Filter ] ──► External Redirect? ──► Log to external_jobs.jsonl
             │ (Easy Apply Only)
             ▼
     [ Model Hub ] ◄── OpenRouter (google/gemini-2.5-flash-lite or similar)
   (Score 0-100; Threshold >= 70)
             │
             ▼
  [ Questionnaire Engine ]
       ├── Tier 1: Static YAML (application-profile.yaml)
       └── Tier 2: AI Fallback (Confidence < 0.85 skips & logs to questionnaire_review.jsonl)
             │
             ▼
   [ Naukri Apply API ] (Submit Profile Resume)
             │
             ▼
  [ applied_jobs.csv ] ── Logged and deduped
```

---

## Quickstart

### 1. Clone the Fork
```bash
git clone https://github.com/rawshn97/NopeRi.git
cd NopeRi
```

### 2. Set Up Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Credentials (`.env`)
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your details:
```env
USERNAME=your_naukri_email@example.com
PASSWORD=your_naukri_password

# OpenRouter (Low cost / Free tier)
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENAI_API_BASE=https://openrouter.ai/api/v1/chat/completions
OPENAI_MODEL=google/gemini-2.5-flash-lite

# Pipeline Controls
USE_ADVANCED_CONFIG=1
MIN_APPLY_SCORE=70
NAUKRI_DAILY_QUOTA=50
```

### 4. Configure Your Profile (`application-profile.yaml`)
Copy the example profile template:
```bash
cp application-profile.example.yaml application-profile.yaml
```
Customize with your details:
- Contact information and location preferences
- Current and expected compensation (LPA)
- Notice period
- Total and product years of experience
- Target role titles and domain tags

### 5. Verify Naukri Profile Resume
Make sure your master PDF resume is uploaded to your [Naukri Profile](https://www.naukri.com/mnjuser/profile). Easy Apply automatically submits your uploaded profile resume.

### 6. Run
```bash
chmod +x run.sh
./run.sh
```

Or if you are using Cursor, simply type in chat:
```markdown
/noperi
```

---

## Configuration Reference

All settings can be customized via `.env` or passed inline before `./run.sh`:

| Environment Variable | Default | Description |
|---|---|---|
| `USE_ADVANCED_CONFIG` | `1` | Enables the multi-dimensional search sweep and AI pipeline |
| `MIN_APPLY_SCORE` | `70` | Minimum AI fit score (0-100) required to submit an application |
| `NAUKRI_DAILY_QUOTA` | `50` | Maximum Easy Applies allowed in rolling 24-hour window |
| `APPLY_TARGET` | None | Cumulative CSV total to reach (e.g. `APPLY_TARGET=100`) |
| `MIN_APPLY_COUNT` | None | Minimum new applications required in this session |
| `EXPAND_KEYWORDS` | `0` | Set to `1` to expand search across 12+ specialized role titles |
| `TARGET_KEYWORDS` | Config | Comma-separated list of target job titles |
| `CITY_ORDER` | Config | Comma-separated list of cities to sweep |
| `EXPERIENCE_LEVELS` | `4,2` | Comma-separated experience levels to query |
| `JOB_AGE_LEVELS` | `7` | Comma-separated freshness levels in days (e.g. `3,4,5,6,7`) |
| `MAX_PAGES` | `2` | Hard cap on pages per search variation |
| `STOP_ON_ZERO_NEW` | `1` | Stop paging when a page returns zero new jobs |
| `RESET_SEARCH_VARIATIONS` | `0` | Set to `1` to clear variation history and re-scan from scratch |
| `MIN_SALARY_LPA` | `20` | Minimum salary threshold in LPA across all roles |
| `ALLOW_UNDISCLOSED_SALARY` | `0` | Set to `1` to permit listings with undisclosed or confidential salary |

---

## Screening Form Workflow & Auto-Retry

When an employer form includes questions that cannot be answered with high confidence (confidence `< 0.85`):
1. The listing is safely skipped to avoid false claims.
2. The question and options are logged to `questionnaire_review.jsonl`.
3. Add the question keyword and your preferred answer to `questionnaire_answers.yaml`:
   ```yaml
   by_question_contains:
     "visa sponsorship": "No"
     "willing to relocate": "Yes"
   ```
4. Re-run `./run.sh` to automatically apply to previously skipped jobs.

---

## Production Safety & Best Practices

- **Home Broadband Recommended:** Run on home broadband or local connections. Naukri aggressively fingerprints datacenter ASNs (AWS, GCP, Azure, GitHub Actions).
- **Residential Proxy Support:** For cloud deployment, set `HTTP_PROXY` and `HTTPS_PROXY` pointing to a residential proxy provider.
- **24-Hour Rolling Quota:** Respect the 50 daily apply limit. Naukri caps applications at 50 per day across their platform.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
