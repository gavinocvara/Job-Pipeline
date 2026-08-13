"""
Job Scout Agent — the "actively search for jobs" piece.

Uses the Adzuna public jobs API (free tier, legitimate, ToS-permitted) rather
than scraping LinkedIn/Indeed search pages, which would get the account banned
and violates their terms. Adzuna aggregates from a very wide set of boards, so
coverage for Austin tech roles is solid.

It runs a set of role queries tuned to Gavino's profile (SWE, tech sales, sales
engineering, embedded, new grad), dedupes, filters out anything obviously
senior-level, then asks the model to rank what's actually worth applying to.
"""
import os
import requests
from datetime import datetime, timezone

from agents.claude_client import ask_json
from agents.event_bus import bus
from agents import coach

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/us/search/1"

DEFAULT_QUERIES = [
    "software engineer",
    "junior software engineer",
    "new grad software engineer",
    "embedded software engineer",
    "firmware engineer",
    "full stack developer",
    "sales engineer",
    "solutions engineer",
    "technical account manager",
    "software sales representative",
    "business development representative technology",
    "associate solutions consultant",
]

SENIORITY_BLOCKLIST = [
    "senior", "sr.", "staff", "principal", "lead ", "director", "vp ",
    "head of", "manager,", "architect", "10+ years", "8+ years", "7+ years",
]


def _adzuna_search(query: str, location: str, max_days_old: int, per_page: int = 20) -> list:
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")
    if not (app_id and app_key):
        raise RuntimeError(
            "ADZUNA_APP_ID / ADZUNA_APP_KEY not set. Register free at "
            "https://developer.adzuna.com to enable live job search."
        )
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "where": location,
        "results_per_page": per_page,
        "max_days_old": max_days_old,
        "sort_by": "date",
        "content-type": "application/json",
    }
    resp = requests.get(ADZUNA_BASE, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json().get("results", [])


def _looks_too_senior(title: str) -> bool:
    t = title.lower()
    return any(flag in t for flag in SENIORITY_BLOCKLIST)


def _normalize(raw: dict) -> dict:
    return {
        "source_id": str(raw.get("id", "")),
        "title": raw.get("title", "").replace("<strong>", "").replace("</strong>", ""),
        "company": (raw.get("company") or {}).get("display_name", "Unknown"),
        "location": (raw.get("location") or {}).get("display_name", ""),
        "url": raw.get("redirect_url", ""),
        "description": (raw.get("description") or "").replace("<strong>", "").replace("</strong>", ""),
        "created": raw.get("created", ""),
        "salary_min": raw.get("salary_min"),
        "salary_max": raw.get("salary_max"),
    }


RANK_SYSTEM = """You are a job-fit screener for Gavino Vara, a May 2026 B.S. Computer Engineering \
graduate (UT San Antonio) targeting Austin, TX. His profile: embedded C++/ESP32 firmware, real-time \
DSP, full-stack TypeScript/Next.js, Python ML research, plus real sales experience at Apple \
(business account openings, exceeded targets) and high-touch client service.

He is open to: software engineering, embedded/firmware, full-stack, AND technical sales / sales \
engineering / solutions engineering / software sales roles.

Given a list of job postings, score each 0-100 for how worth-applying it is for him RIGHT NOW, \
considering: seniority fit (he is entry-level — anything requiring 5+ years is a bad fit), \
skill overlap, whether it's genuinely in/near Austin, and whether his background gives him a real \
angle. Be discriminating — do not give everything a 70.

Return JSON: {"ranked": [{"source_id": "...", "fit_score": 0-100, "why": "one sentence, specific", \
"track": "swe" | "embedded" | "fullstack" | "tech_sales" | "other"}]}
Rank highest first. Include every posting given to you."""


def search(location: str = "Austin, TX", queries: list[str] | None = None,
           max_days_old: int = 14, min_fit: int = 55, limit: int = 25,
           run_id: str = "") -> list[dict]:
    """Actively pull live postings and return them ranked by fit."""
    bus.emit("scout", "working", f"Searching live postings in {location}...", run_id=run_id)
    queries = queries or DEFAULT_QUERIES

    seen: dict[str, dict] = {}
    errors = []
    for q in queries:
        try:
            for raw in _adzuna_search(q, location, max_days_old):
                job = _normalize(raw)
                if not job["source_id"] or job["source_id"] in seen:
                    continue
                if _looks_too_senior(job["title"]):
                    continue
                job["matched_query"] = q
                seen[job["source_id"]] = job
        except Exception as e:
            errors.append(f"{q}: {e}")

    jobs = list(seen.values())
    bus.emit("scout", "info", f"Found {len(jobs)} candidate postings, ranking fit...",
             run_id=run_id)

    if not jobs:
        bus.emit("scout", "error", f"No postings found. {'; '.join(errors[:2])}", run_id=run_id)
        return []

    # Trim descriptions before ranking to keep the prompt sane
    slim = [{"source_id": j["source_id"], "title": j["title"], "company": j["company"],
             "location": j["location"], "description": j["description"][:600]} for j in jobs]

    try:
        ranking = ask_json(RANK_SYSTEM + coach.lessons_for("scout"), f"POSTINGS:\n{slim}", max_tokens=4000)
        score_map = {r["source_id"]: r for r in ranking.get("ranked", [])}
    except Exception as e:
        bus.emit("scout", "info", f"Ranking failed ({e}) — returning unranked results.",
                 run_id=run_id)
        score_map = {}

    for j in jobs:
        r = score_map.get(j["source_id"], {})
        j["fit_score"] = r.get("fit_score", 50)
        j["why"] = r.get("why", "Not scored.")
        j["track"] = r.get("track", "other")

    jobs = [j for j in jobs if j["fit_score"] >= min_fit]
    jobs.sort(key=lambda j: j["fit_score"], reverse=True)
    jobs = jobs[:limit]

    bus.emit("scout", "done", f"{len(jobs)} strong matches in {location}.",
             data={"count": len(jobs)}, run_id=run_id)
    return jobs
