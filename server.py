"""
Web server — this is what makes it usable from your phone.

FastAPI + server-sent events. The browser opens one SSE connection and every
agent status transition streams to it live, so the bubbles light up as each
agent works. Pipeline runs happen in a background thread so the HTTP request
returns immediately and the UI stays responsive on mobile.

Deploy target: Render / Railway / Fly (see render.yaml + Procfile).
"""
import asyncio
import json
import os
import threading
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import orchestrator
from agents.event_bus import bus, sse_format, AGENTS
from agents import coach, recruiter_audit, jd_intake, improver, tracker, brain, git_ops

app = FastAPI(title="Job Pipeline")


@app.on_event("startup")
async def _startup():
    bus.bind_loop(asyncio.get_running_loop())


# ---------------------------------------------------------------- models
class SearchRequest(BaseModel):
    location: str = ""
    min_fit: int = 55


class RunRequest(BaseModel):
    jd_text: str = ""
    url: str = ""
    title: str = ""
    company: str = ""


class AuditRequest(BaseModel):
    resume_text: str
    jd_text: str
    company: str = ""
    title: str = ""


class TimelineRequest(BaseModel):
    app_id: str
    stage: str
    note: str = ""
    event_at: str = ""


class ProposalAction(BaseModel):
    proposal_id: str


# ---------------------------------------------------------------- events
@app.get("/api/agents")
async def get_agents():
    return {"agents": AGENTS}


@app.get("/api/stream")
async def stream(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield sse_format({"agent": "system", "status": "info", "message": "connected"})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield sse_format(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- actions
def _bg(fn, *args, **kwargs):
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


@app.post("/api/search")
async def search_jobs(req: SearchRequest):
    run_id = uuid.uuid4().hex[:8]
    results_holder = {}

    def work():
        try:
            jobs = orchestrator.search_jobs(req.location or config.TARGET_LOCATION,
                                            req.min_fit, run_id=run_id)
            path = os.path.join(config.STORAGE_DIR, f"search_{run_id}.json")
            with open(path, "w") as f:
                json.dump(jobs, f, indent=2)
            bus.emit("scout", "results", f"{len(jobs)} jobs ready.",
                     data={"jobs": jobs}, run_id=run_id)
        except Exception as e:
            bus.emit("scout", "error", str(e), run_id=run_id)

    _bg(work)
    return {"run_id": run_id, "status": "searching"}


@app.post("/api/run")
async def run_pipeline(req: RunRequest):
    run_id = uuid.uuid4().hex[:8]

    def work():
        try:
            if req.url and not req.jd_text:
                record = orchestrator.run_pipeline(req.url, is_url=True,
                                                   job_title_hint=req.title,
                                                   company_hint=req.company, run_id=run_id)
            else:
                jd_meta = jd_intake.from_pasted_text(req.jd_text, req.url)
                record = orchestrator.run_pipeline(req.jd_text, is_url=False,
                                                   job_title_hint=req.title,
                                                   company_hint=req.company, run_id=run_id,
                                                   prefetched_jd=jd_meta)
            safe = {k: v for k, v in record.items() if k != "tailored_resume"}
            bus.emit("system", "complete", "Run complete.", data={"record": safe}, run_id=run_id)
        except Exception as e:
            bus.emit("system", "error", str(e), run_id=run_id)

    _bg(work)
    return {"run_id": run_id, "status": "running"}


@app.post("/api/audit")
async def audit_only(req: AuditRequest):
    """Standalone senior-recruiter audit on any pasted resume — no pipeline run."""
    run_id = uuid.uuid4().hex[:8]

    def work():
        try:
            result = recruiter_audit.audit_arbitrary(req.resume_text, req.jd_text,
                                                     req.company, req.title, run_id=run_id)
            bus.emit("system", "audit_result", "Audit complete.",
                     data={"audit": result}, run_id=run_id)
        except Exception as e:
            bus.emit("audit", "error", str(e), run_id=run_id)

    _bg(work)
    return {"run_id": run_id, "status": "auditing"}


@app.post("/api/learn")
async def learn():
    run_id = uuid.uuid4().hex[:8]
    _bg(lambda: orchestrator.learn(run_id=run_id))
    return {"run_id": run_id, "status": "learning"}


@app.post("/api/timeline")
async def add_timeline(req: TimelineRequest):
    ok = orchestrator.add_timeline_event(req.app_id, req.stage, req.note, req.event_at)
    return {"ok": ok}


@app.get("/api/tracker")
async def tracker_view():
    return {
        "stats": tracker.stats(),
        "upcoming": tracker.upcoming(),
        "needs_nudge": tracker.needs_nudge(),
        "stages": tracker.STAGES,
    }


# ------------------------------------------------- self-improvement
@app.get("/api/proposals")
async def proposals():
    return {"proposals": improver.list_proposals(), "overlays": improver.all_overlays()}


@app.post("/api/improve")
async def improve_now():
    """Kick the improvement cycle manually instead of waiting for the next run."""
    run_id = uuid.uuid4().hex[:8]
    _bg(lambda: improver.improvement_cycle(run_id=run_id))
    return {"run_id": run_id, "status": "scanning"}


@app.post("/api/proposal/apply")
async def apply_proposal(req: ProposalAction):
    """Deliberate, human-triggered. This is the only path that changes code."""
    return improver.apply_proposal(req.proposal_id)


@app.post("/api/proposal/reject")
async def reject_proposal(req: ProposalAction):
    return {"ok": improver.reject_proposal(req.proposal_id)}


@app.post("/api/proposal/revert")
async def revert_proposal(req: ProposalAction):
    return improver.revert_proposal(req.proposal_id)


# ---------------------------------------------------------------- reads
@app.get("/api/applications")
async def applications():
    apps = orchestrator.load_applications()
    return {"applications": [{k: v for k, v in a.items() if k != "tailored_resume"}
                             for a in reversed(apps)]}


@app.get("/api/application/{app_id}")
async def application(app_id: str):
    for a in orchestrator.load_applications():
        if a["id"] == app_id:
            return a
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/lessons")
async def lessons():
    return coach.all_lessons()


@app.get("/api/resume/{app_id}")
async def download_resume(app_id: str):
    for a in orchestrator.load_applications():
        if a["id"] == app_id and os.path.exists(a["resume_path"]):
            return FileResponse(
                a["resume_path"],
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename=a["resume_filename"])
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/brain")
async def get_brain():
    b = brain.load()
    return {"summary": brain.summary(), "profile": b.get("profile", {}),
            "playbook": b.get("playbook", []), "companies": b.get("companies", {}),
            "agent_notes": b.get("agent_notes", {}),
            "decisions": b.get("decisions", [])[-40:][::-1]}


@app.get("/api/brain/markdown")
async def brain_markdown():
    return {"markdown": brain.render_markdown()}


@app.get("/api/git")
async def git_status():
    return git_ops.status()


@app.post("/api/git/sync-brain")
async def git_sync_brain():
    return git_ops.sync_brain()


@app.get("/api/health")
async def health():
    return {"ok": True, "target_location": config.TARGET_LOCATION,
            "api_key_set": bool(config.ANTHROPIC_API_KEY),
            "job_search_set": bool(os.getenv("ADZUNA_APP_ID")),
            "email_set": bool(config.SMTP_USER and config.SMTP_PASS),
            "auto_improve": config.AUTO_IMPROVE}


static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
