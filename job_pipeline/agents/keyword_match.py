"""
Agent Team 3 — Keyword extraction + match scoring.
One agent pulls the required/preferred keywords out of the JD, a second
scores the tailored resume against them. Split into two calls so the
extraction isn't biased by having already seen the resume.
"""
from agents.claude_client import ask_json
from agents.event_bus import bus
from agents import coach

EXTRACT_SYSTEM = """Extract ATS-relevant keywords from this job description: required skills, \
preferred skills, tools/technologies, certifications, and role-specific terms (e.g. 'quota', \
'pipeline', 'SDLC', 'CI/CD'). Separate required vs preferred.
Return JSON: {"required": ["keyword", ...], "preferred": ["keyword", ...]}"""

SCORE_SYSTEM = """Given a list of required/preferred keywords and a tailored resume JSON, \
determine which keywords are genuinely present (including close synonyms/equivalents, e.g. \
'Next.js' counts for 'React framework experience') versus missing. Do not count a keyword as \
present just because it would sound good — it must actually appear or be clearly implied by \
resume content. Pay attention to WHERE a keyword appears: a hard requirement buried only in a \
project bullet is weaker than one in the skills line.
Return JSON: {
  "matched_required": ["..."], "missing_required": ["..."],
  "matched_preferred": ["..."], "missing_preferred": ["..."],
  "match_score": 0.0
}
match_score = weighted coverage where required keywords count double. Return a float 0-1."""


def extract_keywords(job_description: str) -> dict:
    return ask_json(EXTRACT_SYSTEM, f"JOB DESCRIPTION:\n{job_description}")


def score_match(keywords: dict, tailored_resume: dict) -> dict:
    return ask_json(SCORE_SYSTEM + coach.lessons_for("keywords"),
                    f"KEYWORDS:\n{keywords}\n\nTAILORED RESUME:\n{tailored_resume}")


def run(job_description: str, tailored_resume: dict, run_id: str = "") -> dict:
    bus.emit("keywords", "working", "Extracting what the ATS will scan for...", run_id=run_id)
    keywords = extract_keywords(job_description)
    bus.emit("keywords", "working",
             f"{len(keywords.get('required', []))} required / "
             f"{len(keywords.get('preferred', []))} preferred keywords found. Scoring...",
             run_id=run_id)
    result = score_match(keywords, tailored_resume)
    result["keywords"] = keywords
    score = result.get("match_score", 0)
    bus.emit("keywords", "done", f"Coverage: {score*100:.0f}%",
             data={"match_score": score}, run_id=run_id)
    return result
