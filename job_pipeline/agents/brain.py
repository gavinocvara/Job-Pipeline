"""
The Brain.

One durable file the whole system reads from and writes to, forever. Everything
else in this app is stateless per-run; this is the part that accumulates.

Design choice worth understanding: the brain is APPEND-BIASED and DECAY-AWARE.
Agents can add observations freely, but every entry carries a confidence and a
hit count, and entries that stop being corroborated get demoted rather than
living forever as fact. A memory system that only accumulates becomes a memory
system full of stale wrong beliefs — the 2026 lesson that no longer applies in
2027 is worse than no lesson, because it's stated with the same authority.

Five sections, each written by a different part of the system:

  profile      — durable facts about Gavino. Slow-changing. Human-editable.
  playbook     — what demonstrably works: phrasings, orderings, keyword tactics.
                 Written by the Coach, weighted by real outcomes.
  companies    — per-employer intelligence gathered across applications.
  agent_notes  — how each agent is performing and what it keeps getting wrong.
  decisions    — an append-only log of every code/prompt change and why.
                 This one never decays. It's the audit trail.

brain.json is the machine-readable store. BRAIN.md is rendered from it after
every write so you can read the whole thing on GitHub without running anything.
"""
import json
import os
from datetime import datetime, timezone

import config

BRAIN_PATH = os.path.join(config.STORAGE_DIR, "brain.json")
BRAIN_MD = os.path.join(os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))),
                        "BRAIN.md")

EMPTY = {
    "version": 1,
    "created_at": None,
    "updated_at": None,
    "run_count": 0,
    "profile": {},
    "playbook": [],
    "companies": {},
    "agent_notes": {},
    "decisions": [],
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def load() -> dict:
    if os.path.exists(BRAIN_PATH):
        with open(BRAIN_PATH) as f:
            b = json.load(f)
        for k, v in EMPTY.items():
            b.setdefault(k, v if not isinstance(v, (dict, list)) else type(v)())
        return b
    b = dict(EMPTY)
    b["created_at"] = _now()
    b["profile"] = {
        "name": "Gavino Vara",
        "degree": "B.S. Computer Engineering, UT San Antonio",
        "graduation": "May 2026",
        "based": "San Antonio, TX",
        "targeting": "Austin, TX",
        "tracks": ["software engineering", "embedded/firmware", "full-stack",
                   "technical sales / sales engineering"],
        "real_depth": ["embedded C++ / ESP32", "real-time DSP", "firmware validation",
                       "Python ML pipelines", "TypeScript/Next.js", "consultative sales"],
        "exposure_only": ["React", "AWS", "GCP", "Firebase", "AutoCAD"],
        "known_gaps": ["no prior software internship at a software company",
                       "no public repo with shared-codebase commit history"],
    }
    return b


def save(b: dict):
    b["updated_at"] = _now()
    os.makedirs(os.path.dirname(BRAIN_PATH), exist_ok=True)
    with open(BRAIN_PATH, "w") as f:
        json.dump(b, f, indent=2)
    render_markdown(b)


# ------------------------------------------------------------------ writes
def note_run(job_title: str, company: str, audit_score, keyword_score,
             red_flags: list, weak_sections: list, track: str = ""):
    """Called after every pipeline run. Builds up per-company intelligence and
    keeps a rolling sense of what keeps going wrong."""
    b = load()
    b["run_count"] = b.get("run_count", 0) + 1

    if company and company.lower() not in ("unknown company", "unknown"):
        c = b["companies"].setdefault(company, {
            "applications": 0, "titles": [], "audit_scores": [],
            "recurring_flags": {}, "notes": [], "outcomes": [],
        })
        c["applications"] += 1
        if job_title and job_title not in c["titles"]:
            c["titles"].append(job_title)
        if isinstance(audit_score, (int, float)):
            c["audit_scores"].append(audit_score)
            c["audit_scores"] = c["audit_scores"][-20:]
        for f in red_flags or []:
            c["recurring_flags"][f] = c["recurring_flags"].get(f, 0) + 1

    # Global weak-spot tally lives under agent_notes so the coach can see it.
    tally = b["agent_notes"].setdefault("_global", {"weak_sections": {}, "red_flags": {}})
    for s in weak_sections or []:
        tally["weak_sections"][s] = tally["weak_sections"].get(s, 0) + 1
    for f in red_flags or []:
        tally["red_flags"][f] = tally["red_flags"].get(f, 0) + 1

    save(b)


def add_playbook_entry(text: str, source: str, confidence: str = "low",
                       evidence: str = "") -> bool:
    """A tactic that appears to work. Deduped; re-adding an existing entry
    increments its hit count and promotes confidence instead of duplicating."""
    b = load()
    for e in b["playbook"]:
        if e["text"].strip().lower() == text.strip().lower():
            e["hits"] = e.get("hits", 1) + 1
            e["last_seen"] = _now()
            if e["hits"] >= 5:
                e["confidence"] = "high"
            elif e["hits"] >= 3:
                e["confidence"] = "medium"
            save(b)
            return False
    b["playbook"].append({
        "text": text, "source": source, "confidence": confidence,
        "evidence": evidence, "hits": 1,
        "added_at": _now(), "last_seen": _now(),
    })
    b["playbook"] = b["playbook"][-60:]
    save(b)
    return True


def note_outcome(company: str, job_title: str, outcome: str):
    b = load()
    if company in b["companies"]:
        b["companies"][company]["outcomes"].append(
            {"title": job_title, "outcome": outcome, "at": _now()})
    save(b)


def note_agent(agent_id: str, observation: str, kind: str = "weakness"):
    b = load()
    a = b["agent_notes"].setdefault(agent_id, {"observations": []})
    a.setdefault("observations", [])
    if any(o["text"].strip().lower() == observation.strip().lower()
           for o in a["observations"]):
        return False
    a["observations"].append({"text": observation, "kind": kind, "at": _now()})
    a["observations"] = a["observations"][-20:]
    save(b)
    return True


def log_decision(what: str, why: str, kind: str, actor: str,
                 detail: str = "", reversible: bool = True):
    """Append-only. Never decays, never pruned. If something breaks three months
    from now, this is how you find out which change did it."""
    b = load()
    b["decisions"].append({
        "at": _now(), "kind": kind, "actor": actor, "what": what,
        "why": why, "detail": detail, "reversible": reversible,
    })
    save(b)


def decay_pass():
    """Demote playbook entries that haven't been corroborated in a long time.
    Run occasionally. Keeps the brain from calcifying around old assumptions."""
    b = load()
    now = datetime.now(timezone.utc)
    changed = 0
    for e in b["playbook"]:
        try:
            last = datetime.fromisoformat(e["last_seen"].replace("Z", "+00:00"))
        except Exception:
            continue
        age_days = (now - last).days
        if age_days > 120 and e.get("confidence") == "high":
            e["confidence"] = "medium"; changed += 1
        elif age_days > 180 and e.get("confidence") == "medium":
            e["confidence"] = "low"; changed += 1
        elif age_days > 365 and e.get("confidence") == "low":
            e["stale"] = True; changed += 1
    if changed:
        save(b)
    return changed


# ------------------------------------------------------------------ reads
def context_for_agent(agent_id: str, max_items: int = 8) -> str:
    """Injected into agent prompts. Only high-signal entries — the brain gets
    big and stuffing all of it into every prompt would drown the instructions."""
    b = load()
    parts = []

    p = b.get("profile", {})
    if p and agent_id in ("tailor", "audit", "scout"):
        parts.append(
            f"CANDIDATE CONTEXT: {p.get('degree')}, graduating {p.get('graduation')}, "
            f"targeting {p.get('targeting')}. Real depth: {', '.join(p.get('real_depth', [])[:6])}. "
            f"Exposure-only (never imply expertise): {', '.join(p.get('exposure_only', []))}. "
            f"Known gaps a recruiter will notice: {'; '.join(p.get('known_gaps', []))}.")

    strong = [e for e in b.get("playbook", [])
              if e.get("confidence") in ("high", "medium") and not e.get("stale")]
    if strong:
        parts.append("PROVEN TACTICS (from real application history):\n"
                     + "\n".join(f"- {e['text']} [{e['confidence']}, seen {e.get('hits',1)}x]"
                                 for e in strong[-max_items:]))

    notes = b.get("agent_notes", {}).get(agent_id, {}).get("observations", [])
    if notes:
        parts.append("YOUR RECURRING WEAK SPOTS:\n"
                     + "\n".join(f"- {o['text']}" for o in notes[-5:]))

    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def company_brief(company: str) -> str:
    b = load()
    c = b.get("companies", {}).get(company)
    if not c:
        return ""
    flags = sorted(c.get("recurring_flags", {}).items(), key=lambda x: -x[1])[:3]
    scores = c.get("audit_scores", [])
    avg = round(sum(scores) / len(scores)) if scores else None
    bits = [f"You have applied to {company} {c['applications']} time(s)."]
    if avg:
        bits.append(f"Average audit score there: {avg}/100.")
    if flags:
        bits.append("Flags that keep recurring: "
                    + "; ".join(f"{f} (x{n})" for f, n in flags) + ".")
    outs = [o["outcome"] for o in c.get("outcomes", []) if o["outcome"] != "unknown"]
    if outs:
        bits.append(f"Past outcomes: {', '.join(outs)}.")
    return " ".join(bits)


def summary() -> dict:
    b = load()
    return {
        "run_count": b.get("run_count", 0),
        "playbook_entries": len(b.get("playbook", [])),
        "high_confidence": len([e for e in b.get("playbook", [])
                                if e.get("confidence") == "high"]),
        "companies_tracked": len(b.get("companies", {})),
        "decisions_logged": len(b.get("decisions", [])),
        "updated_at": b.get("updated_at"),
    }


# ------------------------------------------------------------------ render
def render_markdown(b: dict = None) -> str:
    """Renders BRAIN.md — the human-readable version that lives in the repo,
    so you can read what the system has learned straight from GitHub."""
    b = b or load()
    L = []
    L.append("# Brain\n")
    L.append("Everything this system has learned, accumulated across every run.\n")
    L.append(f"*Auto-generated — edit `storage/brain.json` or the Brain tab, not this file. "
             f"Last updated {b.get('updated_at', 'never')}.*\n")

    L.append(f"\n**{b.get('run_count', 0)} runs · "
             f"{len(b.get('playbook', []))} playbook entries · "
             f"{len(b.get('companies', {}))} companies · "
             f"{len(b.get('decisions', []))} decisions logged**\n")

    p = b.get("profile", {})
    if p:
        L.append("\n## Profile\n")
        for k, v in p.items():
            val = ", ".join(v) if isinstance(v, list) else v
            L.append(f"- **{k.replace('_', ' ').title()}:** {val}")

    pb = [e for e in b.get("playbook", []) if not e.get("stale")]
    L.append("\n## Playbook\n")
    if pb:
        for conf in ("high", "medium", "low"):
            entries = [e for e in pb if e.get("confidence") == conf]
            if not entries:
                continue
            L.append(f"\n### {conf.title()} confidence\n")
            for e in entries:
                L.append(f"- {e['text']}  \n  <sub>seen {e.get('hits',1)}x · "
                         f"source: {e.get('source','')}</sub>")
    else:
        L.append("*Nothing proven yet. Entries appear as outcomes get recorded.*")

    comps = b.get("companies", {})
    if comps:
        L.append("\n## Companies\n")
        L.append("| Company | Apps | Avg audit | Recurring flags |")
        L.append("|---|---|---|---|")
        for name, c in sorted(comps.items(), key=lambda x: -x[1]["applications"]):
            s = c.get("audit_scores", [])
            avg = f"{round(sum(s)/len(s))}/100" if s else "–"
            flags = "; ".join(f"{f} (x{n})" for f, n in
                              sorted(c.get("recurring_flags", {}).items(),
                                     key=lambda x: -x[1])[:2]) or "–"
            L.append(f"| {name} | {c['applications']} | {avg} | {flags} |")

    glob = b.get("agent_notes", {}).get("_global", {})
    if glob.get("red_flags") or glob.get("weak_sections"):
        L.append("\n## Recurring problems across all applications\n")
        for label, key in (("Red flags", "red_flags"), ("Weak sections", "weak_sections")):
            items = sorted(glob.get(key, {}).items(), key=lambda x: -x[1])[:6]
            if items:
                L.append(f"\n**{label}:**")
                for name, n in items:
                    L.append(f"- {name} — {n}x")

    notes = {k: v for k, v in b.get("agent_notes", {}).items() if k != "_global"}
    if notes:
        L.append("\n## Agent notes\n")
        for ag, data in notes.items():
            obs = data.get("observations", [])
            if obs:
                L.append(f"\n**{ag}**")
                for o in obs[-6:]:
                    L.append(f"- {o['text']}")

    decs = b.get("decisions", [])
    if decs:
        L.append("\n## Decision log\n")
        L.append("*Append-only. Every change this system made to itself.*\n")
        L.append("| When | Kind | What | Why |")
        L.append("|---|---|---|---|")
        for d in decs[-40:][::-1]:
            L.append(f"| {d['at'][:16].replace('T',' ')} | {d['kind']} | "
                     f"{d['what'][:70]} | {d['why'][:80]} |")

    md = "\n".join(L) + "\n"
    try:
        with open(BRAIN_MD, "w") as f:
            f.write(md)
    except Exception:
        pass
    return md
