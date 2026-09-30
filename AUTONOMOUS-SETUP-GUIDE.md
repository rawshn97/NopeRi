# Production Blueprint: Autonomous Zero-Cost Naukri Auto-Applier (NopeRi)

> **Lineage:** Forked and enhanced from [Traverser25/NopeRi](https://github.com/Traverser25/NopeRi)  
> **Infrastructure:** Optional Apify residential proxy or local broadband + Agentic IDE (Cursor / Windsurf / VS Code) + OpenRouter model hub + 00:10 IST quota scheduler  
> **Operating Cost:** Under $0.50 / month (or $0.00 using free-tier models and local network)  
> **Automation:** Hands-off daily application pipeline with intelligent prescreening Q&A  

---

## Table of Contents
1. [Upstream Architecture vs. Production Fork](#1-upstream-architecture-vs-production-fork)
2. [Full System Architecture & Zero-Cost Economics](#2-full-system-architecture--zero-cost-economics)
3. [Network Egress & Proxy Options](#3-network-egress--proxy-options)
4. [OpenRouter Free / Low-Cost Model Configuration](#4-openrouter-free--low-cost-model-configuration)
5. [Search Expansion & State Machine](#5-search-expansion--state-machine)
6. [Pre-Filter Pipeline: External vs. Easy Apply](#6-pre-filter-pipeline-external-vs-easy-apply)
7. [Two-Tier Questionnaire Engine & Confidence Guardrails](#7-two-tier-questionnaire-engine--confidence-guardrails)
8. [Autonomous 00:10 IST Quota Scheduler](#8-autonomous-0010-ist-quota-scheduler)
9. [End-to-End Setup & Runbook](#9-end-to-end-setup--runbook)
10. [Troubleshooting & Failure Modes](#10-troubleshooting--failure-modes)

---

## 1. Upstream Architecture vs. Production Fork

Out-of-the-box, the base repository provides the low-level Selenium-free REST client (authentication, bearer token extraction, search, apply). This production fork implements the automated pipeline, state machines, and prescreening guardrails needed for reliable daily execution:

| Challenge in Base Client | Production Fork Solution |
|---|---|
| **Cloud IP / Bot Detection:** Datacenter IPs get `403 Forbidden` / Recaptcha. | **Residential Egress Support:** Runs natively on local PC/Mac broadband or via Apify residential proxies. |
| **Paging Past End (400 Bad Request):** Blindly requesting deep pages causes API errors. | **Adaptive Pagination:** Paginates only when a full page (20 jobs) returns; stops on short or all-duplicate pages (`STOP_ON_ZERO_NEW=1`). |
| **Wasting AI Tokens on External Links:** Scrapes and scores jobs that redirect to third-party ATS portals. | **Pre-AI Flag Filter:** Drops external links using `jobTypeFlags` and `external_jobs.jsonl` *before* AI scoring, saving 70%+ of inference calls. |
| **Hallucinated Prescreening Answers:** Generic LLMs invent unverified experience or claims. | **Two-Tier Engine:** Deterministic YAML profile handles standard fields; AI handles novel questions with a strict `<0.85` confidence skip gate. |
| **Redundant Query Loops:** Restarting runs repeats previous searches from page 1. | **Search Variation State Machine (`search_variation_state.json`):** Tracks `(title, city, exp, age, page)` permutations and resumes automatically across sessions. |
| **Rolling 24h Quota:** Naukri caps Easy Applies at 50 per 24 hours. | **Autonomous Quota Management:** Tracks applications over rolling 24 hours and stops cleanly at 50. |

---

## 2. Full System Architecture & Zero-Cost Economics

```
[ Scheduled Trigger: 00:10 IST ] (launchd / cron / run_scheduled_once.py)
              │
              ▼
    [ Network Egress ] ◄── Local Broadband / Optional Apify Residential Proxy
              │
              ▼
      [ Naukri API Client ] ── (Token generation, Session Bearer, nkparam)
              │
              ├─► Freshness Sweep (Age: 3 -> 4 -> 5 -> 6 -> 7 days)
              ├─► State Resume (search_variation_state.json)
              └─► Adaptive Paging (stop on short or duplicate page)
              │
              ▼
    [ Pre-AI Flag Filter ] ────► External ATS redirect? ──► Log to external_jobs.jsonl
              │ (Easy Apply Only)
              ▼
      [ Model Hub ] ◄── OpenRouter (google/gemini-2.5-flash-lite or similar)
    (Score 0-100; Threshold >= 70)
              │
              ▼
   [ Questionnaire Engine ]
        ├── Tier 1: Static YAML (application-profile.yaml) ──► CTC, Notice, Relocation, YOE
        └── Tier 2: AI Fallback ──► Confidence < 0.85? ──► SKIP & log to questionnaire_review.jsonl
              │
              ▼
      [ Naukri Apply API ] (Submit Profile Resume)
              │
              ▼
   [ applied_jobs.csv ] ──► (Optional) sync to Notion / Spreadsheets
```

### Cost Breakdown
- **NopeRi Core:** Open Source (MIT): $0.00
- **Network Egress:** Local broadband ($0.00) or Apify Free Credit ($0.00)
- **Model Inference:** OpenRouter free or low-cost models: < $0.50 / month
- **Harness:** Local runtime (Cursor / VS Code / Terminal): $0.00

---

## 3. Network Egress & Proxy Options

Naukri protects its endpoints against datacenter IP ranges.
- **Recommended Default:** Run directly on your home broadband or local machine connection.
- **Cloud / VM Alternative:** If running on a cloud server or VPN, route egress through an Apify proxy by setting:
  ```env
  HTTP_PROXY=http://auto:YOUR_APIFY_TOKEN@proxy.apify.com:8000
  HTTPS_PROXY=http://auto:YOUR_APIFY_TOKEN@proxy.apify.com:8000
  ```

---

## 4. OpenRouter Free / Low-Cost Model Configuration

Configure OpenRouter in `.env` to access fast, inexpensive models:

```env
USERNAME=your_naukri_email@example.com
PASSWORD=your_naukri_password

# OpenRouter Configuration
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENAI_API_BASE=https://openrouter.ai/api/v1/chat/completions
OPENAI_MODEL=google/gemini-2.5-flash-lite
MIN_APPLY_SCORE=70

# Production Controls
USE_ADVANCED_CONFIG=1
EXHAUST_JOBS=1
NAUKRI_DAILY_QUOTA=50
```

---

## 5. Search Expansion & State Machine

The multi-dimensional sweep systematically discovers relevant openings without repetitive queries:

$$\text{freshness (age: 3} \to 4 \to 5 \to 6 \to 7) \times \text{titles} \times \text{cities} \times \text{experience levels} \times \text{adaptive pages}$$

### State Persistence (`search_variation_state.json`)
Every query permutation is recorded:
```json
{
  "tried": {
    "Product Manager|Hyderabad|exp=4|age=3|p1": {
      "fetched": 20,
      "new": 14,
      "keep_going": true,
      "status": "ok"
    }
  }
}
```
If a session halts after hitting the 50-job limit, subsequent runs skip completed variations and resume fresh slices.

---

## 6. Pre-Filter Pipeline: External vs. Easy Apply

Search results contain two distinct types:
1. **Easy Apply:** Handled natively via the Naukri API.
2. **External Apply:** Redirects to Workday, Taleo, Greenhouse, or company portals.

`jobTypeFlags` is checked during search. External jobs are dropped before AI scoring, saving 70%+ of inference tokens and logging them into `external_jobs.jsonl` for offline review.

---

## 7. Two-Tier Questionnaire Engine & Confidence Guardrails

Prescreening forms are evaluated using a strict two-tier safety architecture:

### Tier 1: Static YAML Engine (`application-profile.yaml`)
Standard questions are answered deterministically:
- Current and expected CTC (in LPA or INR based on phrasing)
- Notice period and availability
- Total and domain-specific years of experience
- Work authorization and relocation willingness

### Tier 2: AI Fallback with Confidence Gating
- Novel questions consult the LLM with candidate profile context and job details.
- If confidence is `< 0.85`, the submission is aborted, skipped, and logged to `questionnaire_review.jsonl`.
- Users can review skipped questions, update `questionnaire_answers.yaml`, and re-apply.

---

## 8. Autonomous Quota Scheduler (Configurable Time)

Naukri enforces a rolling 24-hour limit of 50 Easy Applies. Running a single sweep right after the midnight quota reset ensures you get the most out of each day.

### Prerequisites: Laptop Must Stay On

> **Your machine must remain powered on and awake until the scheduled run fires.**
> - `caffeinate -ims` prevents the system from sleeping on AC power.
> - If the lid is closed without an external display, sleep prevention may fail.
> - If the machine is shut down, the daemon exits and the run will not execute.

### Quick Start

Run at the default time (00:10 IST, recommended):
```bash
bash scripts/start_scheduled_automation.sh
```

Run at a custom time of your choosing (24-hour IST):
```bash
bash scripts/start_scheduled_automation.sh --time 01:30
bash scripts/start_scheduled_automation.sh --time 09:00 --min-apply-count 50
```

Set via environment variable instead of a flag:
```bash
SCHEDULED_TIME_IST=02:00 bash scripts/start_scheduled_automation.sh
```

### How the Time Logic Works

- Pass `--time HH:MM` (24-hour IST). The script calculates the next occurrence: today if the time is still upcoming, tomorrow if it has already passed.
- Pass `--target-ist "YYYY-MM-DD HH:MM:SS"` for an exact one-off timestamp.
- Omit all flags to default to 00:10 IST.

### Monitoring the Daemon

```bash
# View live countdown
tail -f runs/scheduled_daemon.log

# Check current state (waiting / running / completed / failed)
cat runs/scheduled_run_state.json

# Check whether the daemon process is alive
cat runs/scheduled_daemon.pid | xargs kill -0 && echo "running" || echo "not running"
```

### Prompt to Set Up the Scheduler via Your AI Agent

Paste this into your Cursor / AI agent chat to have it configure and start the scheduler for you:

```
Set up the NopeRi autonomous quota scheduler for me. Ask me what time I want it to run (in 24-hour IST format, e.g. 00:10 or 01:30). Then run: bash scripts/start_scheduled_automation.sh --time <my-chosen-time> --min-apply-count 50. Remind me that my laptop must remain powered on and awake until the run fires. Show me the startup log and the state file so I can confirm it is waiting correctly.
```

---

## 9. End-to-End Setup & Runbook

### Step 1: Clone Repository and Set Up Environment
```bash
git clone https://github.com/rawshn97/NopeRi.git
cd NopeRi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Configure Environment
Copy `.env.example` to `.env` and provide:
- `USERNAME` & `PASSWORD`
- `OPENROUTER_API_KEY`
- `USE_ADVANCED_CONFIG=1`

### Step 3: Configure Candidate Profile
Copy `application-profile.example.yaml` to `application-profile.yaml` and enter your CTC, notice period, target roles, and experience.

### Step 4: Verify Naukri Profile Resume
Ensure your master PDF resume is uploaded on [Naukri Profile](https://www.naukri.com/mnjuser/profile). Easy Apply attaches this profile resume automatically.

### Step 5: Execute
```bash
chmod +x run.sh
./run.sh
```

---

## 10. Troubleshooting & Failure Modes

| Issue | Cause | Solution |
|---|---|---|
| **403 Forbidden** | Datacenter IP detected | Run on local home broadband or configure `HTTP_PROXY` with residential proxy. |
| **406 / Rate Limit Wall** | Requests firing too rapidly | The built-in random jitter and batch pacing handle cooldowns automatically. |
| **50/50 Quota Reached** | Daily API cap reached | Normal behavior. Wait for the daily reset window. |
| **Skipped Questionnaire** | Confidence below 0.85 | Check `questionnaire_review.jsonl`, add custom answer to `questionnaire_answers.yaml`, and re-run. |
