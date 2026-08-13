"""
Master Orchestrator.

Owns the data flow between agents. The agents themselves never import each
other — they take inputs, emit status to the event bus, and return outputs.
This file decides who runs when, and hands the run to the Master Coach at the
end so the team learns from it.

Pipeline for one job:
  scout (optional, if job came from search)
    -> tailor
    -> [ats | truth | recruiter] in parallel, looping up to MAX_REVIEW_ROUNDS
    -> keywords (with a targeted revision pass if coverage is weak)
    -> senior recruiter audit (Gavino's brutal-honesty prompt, on the FINAL resume)
    -> docx render
    -> notifier
    -> coach critique (cross-agent teaching)

Nothing auto-submits. Ever. The output is always "here's the resume + the apply
link, you press the button."
"""
import json
import os
import uuid
from datetime import datetime, timezone

import config
from agents import (jd_intake, resume_tailor, review_team, keyword_match,
                    resume_writer, notifier, recruiter_audit, coach, job_scout,
                    improver, tracker, brain, git_ops)
from agents.event_bus import bus

LEARN_EVERY_N_RUNS = 5


def _load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_master_resume():
    return _load_json(config.MASTER_RESUME_PATH, {})


def load_applications():
    return _load_json(config.APPLICATIONS_LOG, [])


def save_applications(apps):
    _save_json(config.APPLICATIONS_LOG, apps)


def search_jobs(location: str = None, min_fit: int = 55, run_id: str = "") -> list:
    """Standalone: just find jobs, don't build resumes yet."""
    return job_scout.search(location=location or config.TARGET_LOCATION,
                            min_fit=min_fit, run_id=run_id)


def run_pipeline(jd_source: str, is_url: bool, job_title_hint: str = "",
                 company_hint: str = "", run_id: str = "", prefetched_jd: dict = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    master_resume = load_master_resume()

    # --- Intake -----------------------------------------------------------
    if prefetched_jd:
        jd_meta = prefetched_jd
    elif is_url:
        jd_meta = jd_intake.fetch_jd_from_url(jd_source)
    else:
        jd_meta = jd_intake.from_pasted_text(jd_source)

    if jd_meta.get("extraction_looks_thin"):
        bus.emit("scout", "info",
                 "Job text looks thin — if this posting is JavaScript-rendered, paste the "
                 "description manually for a better result.", run_id=run_id)
    job_description = jd_meta["text"]

    # --- Agent 1: Tailor --------------------------------------------------
    tailored = resume_tailor.tailor(master_resume, job_description, job_title_hint,
                                    company_hint, run_id=run_id)

    # --- Agent Team 2: Review loop (parallel reviewers) -------------------
    review_result = {"pass": False, "issues": []}
    for round_num in range(1, config.MAX_REVIEW_ROUNDS + 1):
        review_result = review_team.run_all(master_resume, tailored, job_description, run_id=run_id)
        if review_result["pass"]:
            break
        if round_num < config.MAX_REVIEW_ROUNDS:
            tailored = resume_tailor.revise(master_resume, tailored,
                                            review_result["issues"], run_id=run_id)

    # --- Agent Team 3: Keywords ------------------------------------------
    match_result = keyword_match.run(job_description, tailored, run_id=run_id)

    if (match_result.get("match_score", 0) < config.MIN_KEYWORD_MATCH
            and match_result.get("missing_required")):
        feedback = [f"Keyword coverage is below threshold. Work in these missing required "
                    f"keywords ONLY where truthful and applicable: {match_result['missing_required']}. "
                    f"Do not fabricate experience to hit a keyword."]
        tailored = resume_tailor.revise(master_resume, tailored, feedback, run_id=run_id)
        review_result = review_team.run_all(master_resume, tailored, job_description, run_id=run_id)
        match_result = keyword_match.run(job_description, tailored, run_id=run_id)

    job_title = tailored.get("target_role") or job_title_hint or "Unknown role"
    company = tailored.get("target_company") or company_hint or "Unknown company"

    # --- Senior Recruiter Audit (runs on the FINAL resume) ----------------
    audit_result = recruiter_audit.audit(tailored, job_description, company, job_title,
                                         run_id=run_id)

    # --- Render -----------------------------------------------------------
    app_id = uuid.uuid4().hex[:8]
    safe = lambda s: "".join(c for c in s if c.isalnum() or c in " -_").strip().replace(" ", "_")[:40]
    resume_filename = f"{app_id}_{safe(company)}_{safe(job_title)}.docx"
    resume_path = os.path.join(config.OUTPUT_DIR, resume_filename)
    resume_writer.build_docx(tailored, master_resume["contact"], resume_path)

    # --- Agent 4: Notifier ------------------------------------------------
    bus.emit("notifier", "working", "Packaging resume + apply link...", run_id=run_id)
    notify_result = notifier.notify(app_id, job_title, company, resume_path,
                                    match_result, jd_meta, review_result, audit_result)
    bus.emit("notifier", "done",
             f"Resume ready for {job_title} @ {company}."
             + (" Emailed you." if notify_result.get("emailed") else " (email not configured)"),
             data={"resume_path": resume_path}, run_id=run_id)

    # --- Log --------------------------------------------------------------
    record = {
        "id": app_id,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_title": job_title,
        "company": company,
        "target_location": config.TARGET_LOCATION,
        "apply_url": jd_meta.get("url", ""),
        "portal": jd_meta.get("portal", ""),
        "resume_path": resume_path,
        "resume_filename": resume_filename,
        "tailored_resume": tailored,
        "match_score": match_result.get("match_score"),
        "missing_required_keywords": match_result.get("missing_required", []),
        "review_passed": review_result.get("pass"),
        "outstanding_review_notes": review_result.get("issues", []),
        "audit": audit_result,
        "audit_score": audit_result.get("match_score"),
        "status": "built",
        "outcome": "unknown",
        "timeline": [{
            "stage": "built",
            "note": f"Resume tailored and reviewed. Audit score {audit_result.get('match_score')}/100.",
            "at": datetime.now(timezone.utc).isoformat(),
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }],
    }
    apps = load_applications()
    apps.append(record)
    save_applications(apps)

    # --- Master Coach: cross-agent teaching -------------------------------
    coach.critique_run({
        "job_title": job_title,
        "company": company,
        "tailored_resume": tailored,
        "review_result": review_result,
        "keyword_result": {k: v for k, v in match_result.items() if k != "keywords"},
        "final_audit": audit_result,
    }, run_id=run_id)

    # --- Brain: accumulate what we learned from this run -------------------
    brain.note_run(
        job_title=job_title, company=company,
        audit_score=audit_result.get("match_score"),
        keyword_score=match_result.get("match_score"),
        red_flags=[f.get("flag") for f in audit_result.get("red_flags", [])],
        weak_sections=[s.get("section") for s in audit_result.get("weak_sections", [])],
    )

    # --- Periodic outcome learning + decay ---------------------------------
    # learn() only does real work once outcomes are recorded; calling it here
    # too is cheap, and means decay/outcome-learning happen even if you never
    # remember to click "learn" yourself.
    if brain.summary().get("run_count", 0) % LEARN_EVERY_N_RUNS == 0:
        try:
            learn(run_id=run_id)
        except Exception as e:
            bus.emit("coach", "error", f"Periodic learn skipped: {e}", run_id=run_id)

    # --- Telemetry + the forever improvement loop -------------------------
    improver.record_run({
        "run_id": run_id,
        "job_title": job_title,
        "company": company,
        "audit_score": audit_result.get("match_score"),
        "keyword_score": match_result.get("match_score"),
        "review_passed": review_result.get("pass"),
        "review_issues": review_result.get("issues", []),
        "audit_red_flags": [f.get("flag") for f in audit_result.get("red_flags", [])],
        "audit_missing_keywords": audit_result.get("missing_keywords", []),
        "audit_weak_sections": [s.get("section") for s in audit_result.get("weak_sections", [])],
    })
    if config.AUTO_IMPROVE:
        try:
            improver.improvement_cycle(run_id=run_id)
        except Exception as e:
            bus.emit("watchdog", "error", f"Improvement cycle skipped: {e}", run_id=run_id)

    return record


def add_timeline_event(app_id: str, stage: str, note: str = "", event_at: str = "") -> bool:
    """All status changes go through the tracker so history is preserved."""
    return tracker.add_event(app_id, stage, note, event_at)


def learn(run_id: str = "") -> dict:
    """Outcome learning, and anything confidently learned gets promoted into the
    brain's playbook so it survives beyond the lessons list."""
    result = coach.learn_from_outcomes(load_applications(), run_id=run_id)
    if result.get("confidence") in ("moderate", "reasonable"):
        for f in result.get("findings", []):
            brain.add_playbook_entry(f, source="outcome analysis",
                                     confidence="medium",
                                     evidence=f"n={result.get('sample_size')}")
    brain.decay_pass()
    for a in load_applications():
        if a.get("outcome") not in (None, "unknown"):
            brain.note_outcome(a.get("company", ""), a.get("job_title", ""), a["outcome"])
    try:
        if git_ops.is_repo() and os.getenv("GITHUB_TOKEN"):
            git_ops.sync_brain(run_id=run_id)
    except Exception:
        pass
    return result
