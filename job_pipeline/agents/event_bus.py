"""
Event bus — the thing that makes the agent bubbles light up.

Every agent publishes status transitions here (idle -> working -> done/error)
plus any notes worth showing. The FastAPI layer subscribes and streams them
to the browser over SSE, so the phone UI sees each agent working in real time
rather than staring at a spinner until the whole run finishes.

Agents stay independent — none of them import each other. They just emit
events and return values; the orchestrator wires the data flow.
"""
import asyncio
import json
import time
from typing import Optional

AGENTS = [
    {"id": "scout", "name": "Job Scout", "role": "Finds live Austin postings"},
    {"id": "tailor", "name": "Resume Tailor", "role": "Rewrites resume for the JD"},
    {"id": "ats", "name": "ATS Reviewer", "role": "Parseability + formatting"},
    {"id": "truth", "name": "Truth Reviewer", "role": "Nothing fabricated"},
    {"id": "recruiter", "name": "Recruiter Reviewer", "role": "6-second skim test"},
    {"id": "keywords", "name": "Keyword Team", "role": "ATS keyword coverage"},
    {"id": "audit", "name": "Senior Recruiter Audit", "role": "Brutally honest scorecard"},
    {"id": "notifier", "name": "Notifier", "role": "Emails you the ready packet"},
    {"id": "coach", "name": "Master Coach", "role": "Teaches the team, every run"},
    {"id": "watchdog", "name": "Watchdog", "role": "Spots recurring problems"},
    {"id": "improver", "name": "Improver", "role": "Drafts code patches for your approval"},
    {"id": "patch_reviewer", "name": "Patch Reviewer", "role": "Vets patches before you see them"},
    {"id": "deployer", "name": "Deployer", "role": "Ships approved fixes, rolls back bad ones"},
]


class EventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._history: list[dict] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop):
        """Called from the async side so sync agent code can push events safely."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _dispatch(self, event: dict):
        self._history.append(event)
        self._history = self._history[-500:]
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def emit(self, agent_id: str, status: str, message: str = "", data: dict | None = None,
             run_id: str = ""):
        """Thread-safe emit — agents run in a worker thread, the bus lives on the loop."""
        event = {
            "agent": agent_id,
            "status": status,          # working | done | error | idle | info
            "message": message,
            "data": data or {},
            "run_id": run_id,
            "ts": time.time(),
        }
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._dispatch, event)
        else:
            self._dispatch(event)

    def history(self, run_id: str = "") -> list[dict]:
        if run_id:
            return [e for e in self._history if e.get("run_id") == run_id]
        return list(self._history)


bus = EventBus()


def sse_format(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
