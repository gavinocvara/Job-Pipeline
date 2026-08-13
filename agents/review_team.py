"""
Agent Team 2 — Three independent reviewers, each with a different lens.

They run concurrently (threads) because they don't depend on each other —
that's the "work independently but together" part. Each returns
{"pass": bool, "issues": [...]}, and the orchestrator merges their verdicts.
Each pulls its own accumulated lessons from the Master Coach.
"""
from concurrent.futures import ThreadPoolExecutor

from agents.claude_client import ask_json
from agents.event_bus import bus
from agents import coach

ATS_SYSTEM = """You are an ATS/formatting reviewer. Check the tailored resume JSON for:
- Bullet length (each bullet should be one line to ~2 lines, not paragraphs)
- No special characters that break ATS parsing
- Consistent tense (past roles = past tense, current role = present tense)
- No missing required fields in the schema
- Skills list isn't bloated (8-14 skills is the sane range)
- Total content fits on one page
Return JSON: {"pass": true/false, "issues": ["specific issue", ...]}"""

TRUTH_SYSTEM = """You are a truthfulness/integrity reviewer. Your only job: compare the tailored \
resume JSON against the master resume JSON (ground truth) and flag ANY claim, metric, tool, \
technology, title, date, or outcome in the tailored version that is not directly traceable to \
the master resume. Rephrasing is fine. Invention is not. Also flag anything that implies deep \
expertise in a skill the master resume marks as exposure-level. Be strict — err on the side of \
flagging. This is the agent that keeps him from getting caught out in an interview.
Return JSON: {"pass": true/false, "issues": ["specific fabrication or unsupported claim", ...]}"""

RECRUITER_SYSTEM = """You are a skeptical technical recruiter with 6 seconds to scan this resume \
before deciding to keep reading. Evaluate the tailored resume JSON against the job description for:
- Does the summary immediately signal fit for this specific role?
- Are the strongest, most relevant bullets in the first 1-2 lines of each section?
- Is impact clear (not just duties)?
- Would a hiring manager spot an obvious red flag in the first glance?
- Would you keep reading, or is it generic/hard to skim?
Be demanding. A downstream senior-recruiter audit will catch what you miss, and you will be \
given a lesson about it.
Return JSON: {"pass": true/false, "issues": ["specific improvement", ...]}"""


def ats_review(tailored_resume: dict, run_id: str = "") -> dict:
    bus.emit("ats", "working", "Checking ATS parseability and formatting...", run_id=run_id)
    r = ask_json(ATS_SYSTEM + coach.lessons_for("ats"), f"TAILORED RESUME:\n{tailored_resume}")
    bus.emit("ats", "done" if r.get("pass") else "info",
             "Clean." if r.get("pass") else f"{len(r.get('issues', []))} formatting issue(s).",
             run_id=run_id)
    return r


def truthfulness_review(master_resume: dict, tailored_resume: dict, run_id: str = "") -> dict:
    bus.emit("truth", "working", "Verifying every claim against your real experience...",
             run_id=run_id)
    user = f"MASTER RESUME:\n{master_resume}\n\nTAILORED RESUME:\n{tailored_resume}"
    r = ask_json(TRUTH_SYSTEM + coach.lessons_for("truth"), user)
    bus.emit("truth", "done" if r.get("pass") else "info",
             "Nothing fabricated." if r.get("pass") else f"{len(r.get('issues', []))} unsupported claim(s).",
             run_id=run_id)
    return r


def recruiter_review(tailored_resume: dict, job_description: str, run_id: str = "") -> dict:
    bus.emit("recruiter", "working", "Running the 6-second skim test...", run_id=run_id)
    user = f"JOB DESCRIPTION:\n{job_description}\n\nTAILORED RESUME:\n{tailored_resume}"
    r = ask_json(RECRUITER_SYSTEM + coach.lessons_for("recruiter"), user)
    bus.emit("recruiter", "done" if r.get("pass") else "info",
             "Would keep reading." if r.get("pass") else f"{len(r.get('issues', []))} skim issue(s).",
             run_id=run_id)
    return r


def run_all(master_resume: dict, tailored_resume: dict, job_description: str,
            run_id: str = "") -> dict:
    """Runs all three reviewers in parallel and consolidates results."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_ats = pool.submit(ats_review, tailored_resume, run_id)
        f_truth = pool.submit(truthfulness_review, master_resume, tailored_resume, run_id)
        f_rec = pool.submit(recruiter_review, tailored_resume, job_description, run_id)
        ats, truth, recruiter = f_ats.result(), f_truth.result(), f_rec.result()

    all_pass = ats.get("pass") and truth.get("pass") and recruiter.get("pass")
    all_issues = (
        [f"[ATS] {i}" for i in ats.get("issues", [])]
        + [f"[Truthfulness] {i}" for i in truth.get("issues", [])]
        + [f"[Recruiter] {i}" for i in recruiter.get("issues", [])]
    )
    return {"pass": bool(all_pass), "issues": all_issues,
            "detail": {"ats": ats, "truthfulness": truth, "recruiter": recruiter}}
