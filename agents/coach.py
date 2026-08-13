"""
Master Coach Agent — the "constantly improve and teach each other" layer.

Being straight about what this is and isn't: it does not retrain any model
weights. What it does is maintain a persistent, per-agent lesson file that gets
injected into each agent's system prompt on every future run. Two feedback
loops fill it:

  LOOP 1 — after every run (fast, always available):
    The coach reads what each agent produced and what the downstream agents
    caught. If the Truth Reviewer flagged a fabrication, that becomes a lesson
    for the Tailor. If the Recruiter Audit found a red flag the review team
    missed, that becomes a lesson for whichever reviewer should have caught it.
    That is the agents teaching each other — the signal is one agent's output
    critiqued by another's, routed by the coach.

  LOOP 2 — from real outcomes (slow, higher value):
    Once outcomes are recorded (interview / rejected / no response), the coach
    looks for patterns across applications and writes strategy-level lessons.

Honest caveat that the UI also shows: Loop 1 is self-consistency improvement —
the team gets better at agreeing with itself and at not repeating caught
mistakes. Only Loop 2 tells you whether any of it is working in the real job
market, and it needs a real sample size (10+ resolved outcomes) before its
"patterns" are worth more than a guess. The coach is instructed to say so
rather than invent confident-sounding advice from three data points.
"""
import json
import os
from datetime import datetime, timezone

import config
from agents.claude_client import ask_json
from agents.event_bus import bus

LESSONS_PATH = os.path.join(config.STORAGE_DIR, "lessons.json")

TEACHABLE_AGENTS = ["tailor", "ats", "truth", "recruiter", "keywords", "scout", "audit"]


def _load() -> dict:
    if os.path.exists(LESSONS_PATH):
        with open(LESSONS_PATH) as f:
            return json.load(f)
    return {a: [] for a in TEACHABLE_AGENTS}


def _save(data: dict):
    with open(LESSONS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def lessons_for(agent_id: str, limit: int = 12) -> str:
    """Returns coach lessons PLUS any auto-applied prompt refinements from the
    improvement team, so both learning loops reach the agent in one string."""
    """Called by each agent at prompt-build time. Returns the most recent lessons
    as plain text to append to that agent's system prompt."""
    from agents import improver, brain  # local imports avoid circular dependencies
    data = _load()
    entries = data.get(agent_id, [])[-limit:]
    block = ""
    if entries:
        lines = [f"- {e['lesson']}" for e in entries]
        block = ("\nLESSONS FROM YOUR PAST RUNS (apply these; they came from mistakes you or a "
                 "teammate caught):\n" + "\n".join(lines))
    return block + improver.overlay_for(agent_id) + brain.context_for_agent(agent_id)


def add_lesson(agent_id: str, lesson: str, source: str = "coach"):
    data = _load()
    data.setdefault(agent_id, [])
    # Avoid piling up near-duplicates
    if any(lesson.strip().lower() == e["lesson"].strip().lower() for e in data[agent_id]):
        return
    data[agent_id].append({
        "lesson": lesson,
        "source": source,
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    # Keep each agent's list bounded so prompts don't balloon
    data[agent_id] = data[agent_id][-25:]
    _save(data)


CRITIQUE_SYSTEM = """You are the Master Coach for a multi-agent resume pipeline. Your job is to \
turn what happened during one run into specific, actionable lessons for individual agents.

The team:
- "scout": finds and fit-scores live job postings
- "tailor": rewrites the resume for the specific JD (must never fabricate)
- "ats": reviews formatting/parseability
- "truth": verifies nothing in the tailored resume is unsupported by the master resume
- "keywords": scores ATS keyword coverage
- "recruiter": 6-second skim-test reviewer
- "audit": the final senior-recruiter audit itself, run last on the finished resume

You will see: the run's tailored resume, what each reviewer flagged, the keyword score, and the \
final brutally-honest Senior Recruiter Audit.

Key signal to look for: things the FINAL AUDIT caught that the review team MISSED. If the audit \
found a red flag that the "recruiter" reviewer passed, that reviewer needs a lesson. If the audit \
found missing keywords that the "keywords" agent scored as fine, that agent needs a lesson. If the \
tailor produced something a reviewer had to correct, the tailor needs a lesson so it stops \
producing it in the first place. The audit itself rarely earns a lesson from this loop alone — it's \
the ground truth other agents are checked against — but if its own scoring or flags look internally \
inconsistent (e.g. contradicts a fact the truth reviewer already confirmed), give it one.

Write lessons that are SPECIFIC and TRANSFERABLE to future different jobs. Bad lesson: "do better \
on keywords." Good lesson: "When a JD lists a cloud platform in requirements, check whether the \
resume surfaces it in the skills line specifically — burying it in a project bullet does not \
reliably register with ATS parsers."

Do not invent lessons to fill quota. If an agent performed fine this run, give it no lesson. \
Zero lessons is a valid output.

Return JSON: {"lessons": [{"agent": "tailor|ats|truth|keywords|recruiter|scout|audit", "lesson": "..."}], \
"run_note": "one sentence on how this run went overall"}"""


def critique_run(run_payload: dict, run_id: str = "") -> dict:
    """LOOP 1 — post-run cross-agent teaching."""
    bus.emit("coach", "working", "Reviewing how the team performed on this run...", run_id=run_id)
    try:
        result = ask_json(CRITIQUE_SYSTEM, f"RUN DATA:\n{json.dumps(run_payload, indent=2)[:14000]}",
                          max_tokens=2500)
        for item in result.get("lessons", []):
            if item.get("agent") in TEACHABLE_AGENTS:
                add_lesson(item["agent"], item["lesson"], source="post-run critique")
        n = len(result.get("lessons", []))
        bus.emit("coach", "done",
                 f"{n} new lesson(s) taught. {result.get('run_note', '')}"[:140],
                 data={"lessons": result.get("lessons", [])}, run_id=run_id)
        return result
    except Exception as e:
        bus.emit("coach", "error", str(e), run_id=run_id)
        return {"lessons": [], "run_note": f"Coach failed: {e}"}


OUTCOME_SYSTEM = """You analyze a job applicant's application history to find patterns between \
resume/keyword strategy and real outcomes (interview, rejected, no_response, offer).

BE HONEST ABOUT SAMPLE SIZE. With fewer than 10 resolved outcomes, you must say the data is too \
thin for confident conclusions and return at most 1-2 tentative hypotheses clearly labeled as \
hypotheses. Never present a pattern from 3 data points as a finding. Fabricated confidence here \
is worse than no advice, because it will be injected into the agents' prompts and shape every \
future application.

Return JSON: {
  "sample_size": int,
  "confidence": "too thin | low | moderate | reasonable",
  "findings": ["..."],
  "lessons": [{"agent": "tailor|keywords|scout|recruiter|audit", "lesson": "..."}]
}"""


def learn_from_outcomes(applications: list, run_id: str = "") -> dict:
    """LOOP 2 — real-world outcome learning."""
    resolved = [a for a in applications if a.get("outcome") not in (None, "unknown")]
    bus.emit("coach", "working",
             f"Analyzing {len(resolved)} resolved outcome(s) of {len(applications)} applications...",
             run_id=run_id)
    if not resolved:
        bus.emit("coach", "done", "No resolved outcomes yet — nothing real to learn from.",
                 run_id=run_id)
        return {"sample_size": 0, "confidence": "too thin", "findings": [], "lessons": []}

    slim = [{k: a.get(k) for k in ("job_title", "company", "match_score", "audit_score",
                                    "missing_required_keywords", "outcome", "track")}
            for a in resolved]
    result = ask_json(OUTCOME_SYSTEM, f"APPLICATION HISTORY:\n{json.dumps(slim, indent=2)}",
                      max_tokens=2500)
    for item in result.get("lessons", []):
        if item.get("agent") in TEACHABLE_AGENTS:
            add_lesson(item["agent"], item["lesson"], source="outcome analysis")
    bus.emit("coach", "done",
             f"Confidence: {result.get('confidence')}. {len(result.get('lessons', []))} lesson(s) added.",
             data=result, run_id=run_id)
    return result


def all_lessons() -> dict:
    return _load()


def reset_lessons():
    _save({a: [] for a in TEACHABLE_AGENTS})
