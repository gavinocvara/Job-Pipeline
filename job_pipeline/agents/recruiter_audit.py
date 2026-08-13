"""
Senior Recruiter Audit Agent.

This is Gavino's own prompt, wired in as a first-class agent. It runs on the
FINAL tailored resume (after the review team and keyword team have done their
passes), so the audit reflects what would actually be submitted — not the
first draft. Its findings do two things:
  1. Surface to him in the UI and the notification email
  2. Feed the Master Coach, which turns repeated findings into lessons for
     the other agents

It is deliberately told not to soften. The whole point of the prompt is that
a harsh read now beats being ghosted later.
"""
from agents.claude_client import ask_json
from agents.event_bus import bus
from agents import coach

SYSTEM = """Act as a senior recruiter for this exact company. Analyze the candidate's resume \
against this job description.

Give:
1. A match score out of 100
2. The top 5 missing keywords that the ATS will be scanning for
3. The 3 red flags a hiring manager would spot in under 10 seconds
4. Which sections are strong and why
5. Which sections are weak and why
6. How this resume compares to what a strong candidate for this role would look like

Be brutally honest. The candidate would rather fix problems now than get ghosted later.

Context you must weigh: the candidate is a May 2026 B.S. Computer Engineering graduate targeting \
Austin, TX. Judge him against the realistic bar for THIS role at THIS company — not against a \
senior engineer, but also do not grade on a curve because he is early-career. If the honest answer \
is that he is underqualified for this posting, say so plainly and say what would close the gap.

Do not pad. Do not open with encouragement. Lead with the score.

Return JSON exactly in this shape:
{
  "match_score": 0-100,
  "verdict": "one blunt sentence on whether this is worth applying to as-is",
  "missing_keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"],
  "red_flags": [
    {"flag": "what they'd see", "why": "why it kills interest in under 10 seconds", "fix": "the specific fix"}
  ],
  "strong_sections": [{"section": "name", "why": "specific reason"}],
  "weak_sections": [{"section": "name", "why": "specific reason", "fix": "specific fix"}],
  "vs_strong_candidate": "honest paragraph comparing this resume to what a genuinely strong applicant for this exact role looks like, including what they'd have that he doesn't",
  "highest_leverage_fix": "the single change that would move the needle most"
}"""


def audit(tailored_resume: dict, job_description: str, company: str = "",
          job_title: str = "", run_id: str = "") -> dict:
    bus.emit("audit", "working", f"Auditing as a senior recruiter at {company or 'this company'}...",
             run_id=run_id)
    user = f"""COMPANY: {company or "infer from the job description"}
ROLE: {job_title or "infer from the job description"}

JOB DESCRIPTION:
{job_description}

CANDIDATE RESUME (this is the exact version that would be submitted):
{tailored_resume}

Run the analysis now."""
    try:
        result = ask_json(SYSTEM + coach.lessons_for("audit"), user, max_tokens=4000)
        bus.emit("audit", "done",
                 f"Score {result.get('match_score')}/100 — {result.get('verdict', '')[:90]}",
                 data={"match_score": result.get("match_score")}, run_id=run_id)
        return result
    except Exception as e:
        bus.emit("audit", "error", str(e), run_id=run_id)
        raise


def audit_arbitrary(resume_text: str, job_description: str, company: str = "",
                    job_title: str = "", run_id: str = "") -> dict:
    """Same audit, but against pasted resume text instead of the pipeline's JSON —
    lets Gavino spot-check any resume version, not just ones this tool generated."""
    bus.emit("audit", "working", "Auditing pasted resume...", run_id=run_id)
    user = f"""COMPANY: {company or "infer from the job description"}
ROLE: {job_title or "infer from the job description"}

JOB DESCRIPTION:
{job_description}

CANDIDATE RESUME:
{resume_text}

Run the analysis now."""
    result = ask_json(SYSTEM + coach.lessons_for("audit"), user, max_tokens=4000)
    bus.emit("audit", "done", f"Score {result.get('match_score')}/100", run_id=run_id)
    return result
