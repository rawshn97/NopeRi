"""Job filter classification pipeline with AI scoring and domain guardrails."""

import json
import re

import requests

from config.ba_salary import MIN_SALARY_LPA, parse_salary_lpa
from config.profile_loader import load_application_profile
from src.client.jop_classifier import JobFilterPipeline2


class JobFilterPipelinePM(JobFilterPipeline2):
    MY_STACK = [
        "product management", "product manager", "product strategy", "product roadmap",
        "prd", "user research", "gtm", "stakeholder", "prioritization",
        "business analyst", "business analysis", "requirements", "brd", "frd",
        "process", "gap analysis", "user stories", "acceptance criteria",
        "b2b saas", "enterprise", "marketplace", "onboarding", "implementation",
        "ai", "ml", "llm", "generative ai", "genai", "prompt engineering",
        "figma", "sql", "jira", "analytics", "mixpanel", "amplitude",
        "a/b testing", "ux", "ui", "agile", "scrum", "api",
    ]

    # Veto non-relevant roles to protect daily apply quota
    VETO_TITLES = [
        "walk-in", "walkin", "walk in",
        "android developer", "ios developer", "flutter developer",
        "frontend developer", "front-end developer",
        "principal engineer", "staff engineer",
        "vp of engineering", "head of engineering", "head of technology",
        "founder", "tutor", "trainer",
        "data scientist", "ml engineer", "data engineer", "intern", "internship",
        "engineering manager", "etl engineer", "prompt engineer",
        "sales manager", "account manager", "project manager",
        "marketing manager", "brand marketing manager", "digital marketing manager",
        "success manager", "customer success", "client success", "csm",
        "sales effectiveness", "sales enablement", "sales operations", "sales ops",
        "sales executive", "account executive", "business development", "bd manager",
        "relationship manager", "client relationship",
        "operations manager", "service delivery manager", "general manager",
        "data analyst", "financial analyst", "equity analyst", "research analyst",
        "credit analyst", "risk analyst", "soc analyst", "security analyst",
        "associate is engineer", "infra engineer",
        "observability engineer",
    ]

    SOFTWARE_KEYWORDS = {
        "product", "manager", "pm", "owner", "program",
        "business analyst", "analyst", "ba ",
        "software", "engineer", "developer", "technology",
        "saas", "platform", "growth", "strategy",
    }

    FRONTEND_VETO_KEYWORDS = {
        "android", "ios", "flutter", "mobile", "kotlin", "swift",
        "embedded", "firmware", "intern", "internship",
    }

    def experience_filter(self, jobs):
        # Keep roles overlapping 2-8 years
        return [
            j for j in jobs
            if j.get("experience_min", 0) <= 8
            and j.get("experience_max", 10) >= 2
        ]

    def salary_filter(self, jobs):
        # Drop listings whose posted ceiling is below MIN_SALARY_LPA across all roles.
        # Undisclosed salary listings stay in pipeline; apply_agent re-checks job details.
        clean = []
        for j in jobs:
            min_lpa, max_lpa = parse_salary_lpa(j.get("salary"))
            ceiling = max_lpa if max_lpa is not None else min_lpa
            if ceiling is not None and ceiling <= MIN_SALARY_LPA:
                print(
                    f"  [SALARY SKIP] {j.get('title')} | {j.get('salary')} "
                    f"| need >{MIN_SALARY_LPA:g} LPA"
                )
                continue
            clean.append(j)
        return clean

    def _call_ai(self, jobs):
        profile = load_application_profile()
        exp_total = profile.get("exp_total") or "5"
        exp_product = profile.get("exp_product") or "3"
        skills = ", ".join(profile.get("skills", [])) or "product management, product strategy, PRDs, roadmaps, agile, user research"
        location = profile.get("location") or "India"
        expected_ctc = profile.get("expected_ctc") or "flexible"

        job_block = ""
        for i, j in enumerate(jobs):
            mandatory = ", ".join(j.get("mandatory_tags", [])) or "none"
            optional = ", ".join(j.get("optional_tags", [])) or "none"
            exp = f"{j.get('experience_min', 0)}-{j.get('experience_max', 10)} yrs"
            job_block += (
                f"Job {i}:\n"
                f"  Title:     {j.get('title')}\n"
                f"  Company:   {j.get('company')}\n"
                f"  Mandatory: {mandatory}\n"
                f"  Optional:  {optional}\n"
                f"  Exp:       {exp}\n"
                f"  Days old:  {j.get('days_old', 7)}\n"
                f"---\n"
            )

        prompt = f"""
You are a strict job filter for a Product Manager / Senior Business Analyst candidate. Score each job 0-100.
Use the full range; avoid clustering every job at 85 or 60.

CANDIDATE PROFILE:
- Experience: {exp_total} years total ({exp_product}+ in product management / business analysis)
- Core Skills: {skills}
- Target roles: Product Manager, Senior PM, Technical PM, AI PM, Growth PM, Senior Business Analyst, Business Analyst (tech/product)
- Location preference: {location}
- Expected compensation: {expected_ctc} LPA INR
- Exclusions: Skip pure marketing manager roles. Skip Customer Success, Support, Sales Operations, and non-product operations (score 0-20).

SCORING RUBRIC:

90-100: Strong PM fit, apply immediately
  PM or Senior PM title with product strategy, roadmap, PRD, analytics, or tech domain tags.
  Experience requirement matches candidate profile range.

75-89: Good PM or Senior BA fit, apply
  PM-adjacent title (Product Owner, Growth PM, Technical PM) or Senior Business Analyst in product/tech.
  Solid stack and requirements overlap.

55-74: Marginal fit, lower priority
  Product adjacent role with partial overlap, or Business Analyst with domain alignment.

30-54: Weak fit, skip
  Project manager, pure finance/risk/data analyst without business or product context.

0-29: Do not apply
  Customer Success, Success Manager, Sales Operations, Account Management.
  Pure engineering (Android, iOS, backend-only), data science, sales, internship, walk-in.

Return ONLY valid JSON:
{{
  "0": {{"score": 92, "reason": "Senior PM, tech/product tags, matches experience"}},
  "1": {{"score": 10, "reason": "Pure sales role, zero PM overlap"}}
}}

Jobs:
{job_block}
"""

        try:
            res = requests.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=90,
            )

            if res.status_code != 200:
                print("AI HTTP ERROR:", res.status_code, res.text[:200])
                return {}

            content = res.json()["choices"][0]["message"]["content"]
            content = re.sub(r"```json|```", "", content).strip()
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                print("AI PARSE ERROR - raw:", content[:300])
                return {}

            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}

        except Exception as e:
            print("AI call error:", e)
            return {}
