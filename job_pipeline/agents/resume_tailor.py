"""
Agent 1 — Resume Tailor.

Takes the master resume (ground truth of everything Gavino has actually
done) and a job description, and selects/reorders/rewrites bullets to
match the role. Hard rule: it can rephrase and reprioritize, but it may
never invent a claim, tool, metric, or outcome that isn't traceable back
to master_resume.json. That's what the Truthfulness reviewer checks.

Pulls its accumulated lessons from the Master Coach at prompt-build time.
"""
from agents.claude_client import ask_json
from agents.event_bus import bus
from agents import coach

BASE_SYSTEM = """You are a resume-tailoring agent for Gavino Vara, a May 2026 Computer \
Engineering graduate applying to Software Engineering, Software Sales, and Technical/Sales \
Engineering roles, targeting Austin, TX.

You will be given:
1. His master resume as structured JSON (the ONLY source of truth about his experience)
2. A job description

Your job: produce a tailored resume as JSON in the exact same schema, where you:
- Select the most relevant bullets per role/project for THIS job (you may drop irrelevant ones)
- Reorder bullets within each entry so the most relevant lead
- Lightly rewrite bullet phrasing to mirror the job description's terminology, WITHOUT changing \
the underlying facts, numbers, tools, or scope
- Choose which skills from 'core' and 'extended' to surface, and in what order
- Write a 2-3 sentence professional summary tailored to this specific role
- If the role is sales/tech-sales, foreground the Apple and JW Marriott sales/communication \
bullets and technical projects as credibility signals, not the other way around
- If the role is SWE/engineering, foreground MyoPack, the ML research, and Data Optics

STRICT RULES:
- Never invent metrics, tools, technologies, or claims not present in the master resume
- Never change job titles, dates, companies, or degree information
- 'extended' skills are exposure-level only — surface them only if the JD calls for them, and \
never in a way that implies deep expertise
- Do not exceed one page worth of content (roughly 5-6 bullets per major entry max, fewer for \
minor entries)
- Return valid JSON only, matching this schema:
{
  "summary": "string",
  "target_role": "string",
  "target_company": "string",
  "skills_to_show": ["ordered list of skill strings"],
  "experience": [{"org":..., "title":..., "location":..., "dates":..., "bullets": ["string", ...]}],
  "projects": [{"name":..., "role":..., "location":..., "dates":..., "bullets": ["string", ...]}],
  "education": {"school":..., "degree":..., "graduation":..., "location":...}
}
"""


def _system():
    return BASE_SYSTEM + coach.lessons_for("tailor")


def tailor(master_resume: dict, job_description: str, job_title_hint: str = "",
           company_hint: str = "", run_id: str = "") -> dict:
    bus.emit("tailor", "working", "Drafting a tailored resume for this posting...", run_id=run_id)
    user = f"""MASTER RESUME (source of truth):
{master_resume}

JOB DESCRIPTION:
{job_description}

Job title hint: {job_title_hint or "unknown - infer from JD"}
Company hint: {company_hint or "unknown - infer from JD"}

Produce the tailored resume JSON now."""
    try:
        result = ask_json(_system(), user, max_tokens=3000)
        bus.emit("tailor", "done",
                 f"Draft ready for {result.get('target_role', 'role')}.", run_id=run_id)
        return result
    except Exception as e:
        bus.emit("tailor", "error", str(e), run_id=run_id)
        raise


def revise(master_resume: dict, tailored_resume: dict, feedback: list, run_id: str = "") -> dict:
    """Second pass — apply reviewer feedback to an existing tailored draft."""
    bus.emit("tailor", "working", f"Revising to address {len(feedback)} note(s) from the team...",
             run_id=run_id)
    user = f"""MASTER RESUME (source of truth, do not exceed this):
{master_resume}

CURRENT TAILORED DRAFT:
{tailored_resume}

REVIEWER FEEDBACK TO ADDRESS:
{feedback}

Revise the draft to address every piece of feedback. Return the full corrected JSON in the \
same schema."""
    try:
        result = ask_json(_system(), user, max_tokens=3000)
        bus.emit("tailor", "done", "Revision complete.", run_id=run_id)
        return result
    except Exception as e:
        bus.emit("tailor", "error", str(e), run_id=run_id)
        raise
