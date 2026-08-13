"""
Application Tracker.

Every application carries a timeline: an append-only list of dated events, so
you can see exactly when you applied, when you heard back, and what's scheduled.
Nothing is overwritten — changing status adds an event rather than replacing the
last one, so the history survives.

Stages are deliberately close to how you actually talk about this:
  built -> applied -> heard_back -> interview -> offer / rejected / ghosted
"""
import json
import os
from datetime import datetime, timezone, timedelta

import config

STAGES = {
    "built":      {"label": "Resume ready",   "color": "dim"},
    "applied":    {"label": "Applied",        "color": "cool"},
    "heard_back": {"label": "Heard back",     "color": "live"},
    "interview":  {"label": "Interview",      "color": "good"},
    "offer":      {"label": "Offer",          "color": "good"},
    "rejected":   {"label": "Rejected",       "color": "bad"},
    "ghosted":    {"label": "No response",    "color": "faint"},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load():
    if os.path.exists(config.APPLICATIONS_LOG):
        with open(config.APPLICATIONS_LOG) as f:
            return json.load(f)
    return []


def _save(apps):
    with open(config.APPLICATIONS_LOG, "w") as f:
        json.dump(apps, f, indent=2)


def add_event(app_id: str, stage: str, note: str = "", event_at: str = "") -> bool:
    """Append a timeline event. `event_at` lets you back-date (you applied last
    Tuesday but are only logging it now) or forward-date an interview."""
    if stage not in STAGES:
        return False
    apps = _load()
    for a in apps:
        if a["id"] == app_id:
            a.setdefault("timeline", [])
            a["timeline"].append({
                "stage": stage,
                "note": note,
                "at": event_at or _now(),        # when the thing happened
                "logged_at": _now(),             # when you recorded it
            })
            a["timeline"].sort(key=lambda e: e["at"])
            # current stage = latest event that isn't in the future
            now = _now()
            past = [e for e in a["timeline"] if e["at"] <= now]
            a["status"] = (past[-1]["stage"] if past else a["timeline"][0]["stage"])
            a["outcome"] = _outcome_from(a["timeline"])
            _save(apps)
            return True
    return False


def _outcome_from(timeline: list) -> str:
    """Maps the timeline onto the coarse outcome the coach learns from."""
    stages = {e["stage"] for e in timeline}
    if "offer" in stages: return "offer"
    if "interview" in stages: return "interview"
    if "rejected" in stages: return "rejected"
    if "ghosted" in stages: return "no_response"
    if "heard_back" in stages: return "heard_back"
    if "applied" in stages: return "applied"
    return "unknown"


def delete_event(app_id: str, index: int) -> bool:
    apps = _load()
    for a in apps:
        if a["id"] == app_id and 0 <= index < len(a.get("timeline", [])):
            a["timeline"].pop(index)
            a["outcome"] = _outcome_from(a["timeline"])
            now = _now()
            past = [e for e in a["timeline"] if e["at"] <= now]
            a["status"] = past[-1]["stage"] if past else "built"
            _save(apps)
            return True
    return False


def upcoming(days: int = 30) -> list:
    """Anything scheduled ahead — interviews, mostly. Drives the reminder strip."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    out = []
    for a in _load():
        for e in a.get("timeline", []):
            try:
                when = datetime.fromisoformat(e["at"].replace("Z", "+00:00"))
            except Exception:
                continue
            if now < when <= horizon:
                out.append({"app_id": a["id"], "company": a.get("company"),
                            "job_title": a.get("job_title"), "stage": e["stage"],
                            "note": e.get("note", ""), "at": e["at"]})
    out.sort(key=lambda x: x["at"])
    return out


def needs_nudge(days: int = 10) -> list:
    """Applied a while ago, never heard anything. Worth a follow-up."""
    now = datetime.now(timezone.utc)
    out = []
    for a in _load():
        tl = a.get("timeline", [])
        stages = {e["stage"] for e in tl}
        if "applied" not in stages:
            continue
        if stages & {"heard_back", "interview", "offer", "rejected", "ghosted"}:
            continue
        applied = [e for e in tl if e["stage"] == "applied"]
        if not applied:
            continue
        try:
            when = datetime.fromisoformat(applied[-1]["at"].replace("Z", "+00:00"))
        except Exception:
            continue
        age = (now - when).days
        if age >= days:
            out.append({"app_id": a["id"], "company": a.get("company"),
                        "job_title": a.get("job_title"), "days_since_applied": age})
    out.sort(key=lambda x: -x["days_since_applied"])
    return out


def stats() -> dict:
    apps = _load()
    counts = {k: 0 for k in STAGES}
    for a in apps:
        s = a.get("status", "built")
        if s in counts:
            counts[s] += 1
    applied = sum(1 for a in apps if any(e["stage"] == "applied" for e in a.get("timeline", [])))
    interviews = sum(1 for a in apps if any(e["stage"] == "interview" for e in a.get("timeline", [])))
    return {
        "total": len(apps),
        "applied": applied,
        "interviews": interviews,
        "response_rate": round(interviews / applied, 3) if applied else None,
        "by_stage": counts,
    }
