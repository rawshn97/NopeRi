---
name: noperi
description: Autonomous Naukri Easy Apply job application workflow with AI evaluation and screening auto-retry.
---

# NopeRi Execution Skill

Use this skill when the user asks to "Run NopeRi", "/noperi", or apply to jobs on Naukri.

## Workflow Instructions

1. **Verify Prerequisites:**
   - Ensure `.env` exists with `USERNAME`, `PASSWORD`, and `OPENROUTER_API_KEY`.
   - Ensure `application-profile.yaml` exists. If missing, copy from `application-profile.example.yaml` and prompt the user to review.
   - Verify that the master PDF resume is uploaded on the user's Naukri profile.

2. **Execute Application Pipeline:**
   - Run the bash runner script:
     ```bash
     ./run.sh
     ```
   - Monitor the run output for application progress, rate-limiting jitter, and daily quota counts.

3. **Handle Prescreening Questionnaires:**
   - After the run completes, inspect `questionnaire_review.jsonl` for entries with `status: "skipped_low_confidence"`.
   - If any questions were skipped due to low confidence:
     1. Present the questions to the user in the chat with selectable options or freeform answer prompts.
     2. Save user answers into `questionnaire_answers.yaml` under `by_question_contains`.
     3. Re-run `./run.sh` to apply to the previously skipped listings.

4. **Report Summary:**
   - Report the number of jobs applied in this session.
   - Report the cumulative 24-hour total against the official 50 daily apply limit.
