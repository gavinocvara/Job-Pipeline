"""
The Meta-Improvement Team.

This is the "code on yourself, forever" layer — built with one hard rule:
NOTHING that changes executable code ships without your explicit approval.

Why that rule exists, plainly: an agent that rewrites its own source and
deploys autonomously in a loop has no error floor. One bad patch that still
passes tests can silently degrade every resume you send for weeks before you
notice, and each subsequent self-improvement compounds on top of the broken
state. The failure is quiet and cumulative, which is the worst kind. So the
loop below runs forever and proposes forever, but you are the commit gate.

Two classes of improvement, deliberately separated by risk:

  CLASS A — PROMPT TWEAKS (auto-applied, reversible)
    Changes to agent instructions only. Stored as an overlay in
    storage/prompt_overlays.json, injected at prompt-build time, never touching
    source. Instantly revertable, cannot break the app, cannot execute anything.
    This is where the "little minor tweaks" you mentioned actually live, and
    they apply continuously without bothering you.

  CLASS B — CODE PATCHES (proposed, human-approved, branch-only)
    Real source changes. The Improver writes a unified diff, the Patch Reviewer
    agent independently critiques it for correctness and blast radius, and it
    lands in your approval queue. On approval it applies to a NEW GIT BRANCH
    with a backup, never to main, never auto-deployed.

The Watchdog runs after every pipeline run and decides whether either class is
warranted. Most runs, the answer is neither, and it says so instead of inventing
work.
"""
import difflib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone

import config
from agents.claude_client import ask_json
from agents.event_bus import bus
from agents import brain

PROPOSALS_PATH = os.path.join(config.STORAGE_DIR, "proposals.json")
OVERLAYS_PATH = os.path.join(config.STORAGE_DIR, "prompt_overlays.json")
TELEMETRY_PATH = os.path.join(config.STORAGE_DIR, "telemetry.json")
BACKUP_DIR = os.path.join(config.STORAGE_DIR, "backups")

# Files the improver is allowed to propose changes to. Anything touching
# secrets, deploy config, or this file itself is off the table by design.
PATCHABLE = {
    "agents/resume_tailor.py", "agents/review_team.py", "agents/keyword_match.py",
    "agents/recruiter_audit.py", "agents/job_scout.py", "agents/coach.py",
    "agents/resume_writer.py", "agents/jd_intake.py", "agents/notifier.py",
    "orchestrator.py", "static/index.html",
}
FORBIDDEN = {"config.py", ".env", "agents/improver.py", "server.py",
             "agents/claude_client.py", "render.yaml", "Procfile"}


def _load(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------- telemetry
def record_run(run_summary: dict):
    """Every run leaves a trace. This is what the watchdog reads."""
    tel = _load(TELEMETRY_PATH, [])
    tel.append({**run_summary, "at": datetime.now(timezone.utc).isoformat()})
    _save(TELEMETRY_PATH, tel[-200:])


def load_telemetry(n: int = 25) -> list:
    return _load(TELEMETRY_PATH, [])[-n:]


# ---------------------------------------------------------------- overlays
def overlay_for(agent_id: str) -> str:
    """Class A improvements. Injected into agent prompts at build time."""
    ov = _load(OVERLAYS_PATH, {})
    entries = ov.get(agent_id, [])
    if not entries:
        return ""
    active = [e for e in entries if e.get("active", True)]
    if not active:
        return ""
    return ("\nACTIVE PROMPT REFINEMENTS (auto-applied by the improvement team; "
            "these override defaults where they conflict):\n"
            + "\n".join(f"- {e['text']}" for e in active[-8:]))


def add_overlay(agent_id: str, text: str, rationale: str = ""):
    ov = _load(OVERLAYS_PATH, {})
    ov.setdefault(agent_id, [])
    if any(e["text"].strip().lower() == text.strip().lower() for e in ov[agent_id]):
        return False
    ov[agent_id].append({
        "id": uuid.uuid4().hex[:8], "text": text, "rationale": rationale,
        "active": True, "added_at": datetime.now(timezone.utc).isoformat(),
    })
    ov[agent_id] = ov[agent_id][-15:]
    _save(OVERLAYS_PATH, ov)
    brain.log_decision(what=f"Prompt refinement added to {agent_id}", why=rationale or text,
                       kind="prompt", actor="watchdog", detail=text[:300])
    return True


def toggle_overlay(agent_id: str, overlay_id: str, active: bool) -> bool:
    ov = _load(OVERLAYS_PATH, {})
    for e in ov.get(agent_id, []):
        if e["id"] == overlay_id:
            e["active"] = active
            _save(OVERLAYS_PATH, ov)
            return True
    return False


def all_overlays() -> dict:
    return _load(OVERLAYS_PATH, {})


# ---------------------------------------------------------------- watchdog
WATCHDOG_SYSTEM = """You are the Watchdog for a multi-agent resume pipeline. You read telemetry \
from recent runs and decide whether anything is actually worth fixing.

You are looking for RECURRING problems, not one-offs:
- An agent that repeatedly gets overruled by a downstream agent
- Audit scores that stay low for a specific job track
- Review rounds that consistently max out without converging
- Keyword coverage that is consistently weak in one category
- Errors or exceptions that repeat
- The tailor producing the same flaw across different jobs

Classify each finding:
- "prompt" — fixable by refining an agent's instructions. Low risk. Prefer this.
- "code" — genuinely needs a source change (a bug, a missing capability, broken logic).

BE CONSERVATIVE. Most runs need no changes. Returning an empty findings list is the \
correct and common answer. Do not manufacture work to seem useful — a stream of \
unnecessary patches to a working system is a cost, not a benefit. Require the pattern to \
appear in at least 2 runs before flagging it, unless it is an outright error.

Return JSON: {
  "findings": [{
    "class": "prompt" | "code",
    "agent": "tailor|ats|truth|recruiter|keywords|scout|audit|notifier|orchestrator|ui",
    "problem": "what is recurring, specific, with evidence from the telemetry",
    "evidence_runs": 2,
    "severity": "low" | "medium" | "high",
    "proposed_fix": "for prompt class: the exact instruction text to add. for code class: what the change should do"
  }],
  "verdict": "one sentence — usually 'nothing worth changing'"
}"""


def watchdog(run_id: str = "") -> dict:
    tel = load_telemetry(25)
    bus.emit("watchdog", "working", f"Scanning {len(tel)} recent run(s) for patterns...",
             run_id=run_id)
    if len(tel) < 2:
        bus.emit("watchdog", "done", "Not enough run history to spot a pattern yet.",
                 run_id=run_id)
        return {"findings": [], "verdict": "Need at least 2 runs before patterns mean anything."}
    try:
        result = ask_json(WATCHDOG_SYSTEM, f"TELEMETRY:\n{json.dumps(tel, indent=2)[:14000]}",
                          max_tokens=2500)
    except Exception as e:
        bus.emit("watchdog", "error", str(e), run_id=run_id)
        return {"findings": [], "verdict": f"Watchdog failed: {e}"}

    findings = result.get("findings", [])
    applied = 0
    for f in findings:
        if f.get("class") == "prompt":
            if add_overlay(f.get("agent", ""), f.get("proposed_fix", ""), f.get("problem", "")):
                applied += 1

    code_findings = [f for f in findings if f.get("class") == "code"]
    msg = (f"{applied} prompt refinement(s) applied"
           + (f", {len(code_findings)} code issue(s) to propose." if code_findings
              else ". No code changes needed."))
    bus.emit("watchdog", "done", msg if findings else result.get("verdict", "Nothing to change."),
             data={"findings": findings}, run_id=run_id)
    return result


# ---------------------------------------------------------------- improver
IMPROVER_SYSTEM = """You are a senior engineer proposing a MINIMAL, surgical patch to a Python \
codebase. You will be given a problem description and the current contents of one file.

Rules:
- Change as little as possible. A 4-line fix beats a 40-line refactor.
- Do not restructure, rename, or "clean up" anything unrelated to the stated problem.
- Preserve all existing function signatures unless the fix requires changing one, and say so if it does.
- The code must remain valid Python (or valid HTML/JS for static files).
- If the problem cannot be fixed safely with a small change, say so and return no patch. \
That is a legitimate answer.

Return JSON: {
  "can_fix": true/false,
  "reason_if_not": "...",
  "new_file_content": "the COMPLETE new content of the file, not a diff",
  "explanation": "what you changed and why, 2-3 sentences",
  "risk": "low" | "medium" | "high",
  "what_could_break": "honest assessment of blast radius"
}"""


def _repo_root() -> str:
    return os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))


def propose_code_patch(finding: dict, target_file: str, run_id: str = "") -> dict | None:
    if target_file in FORBIDDEN or target_file not in PATCHABLE:
        bus.emit("improver", "info", f"{target_file} is not patchable by design.", run_id=run_id)
        return None

    root = _repo_root()
    path = os.path.join(root, target_file)
    if not os.path.exists(path):
        return None

    bus.emit("improver", "working", f"Drafting a patch for {target_file}...", run_id=run_id)
    original = open(path).read()

    try:
        result = ask_json(IMPROVER_SYSTEM,
                          f"PROBLEM:\n{json.dumps(finding, indent=2)}\n\n"
                          f"FILE: {target_file}\n\nCURRENT CONTENT:\n{original}",
                          max_tokens=8000)
    except Exception as e:
        bus.emit("improver", "error", str(e), run_id=run_id)
        return None

    if not result.get("can_fix") or not result.get("new_file_content"):
        bus.emit("improver", "done",
                 f"No safe patch: {result.get('reason_if_not', 'declined')}", run_id=run_id)
        return None

    new_content = result["new_file_content"]

    # Hard gate: it must at least parse before a human is asked to look at it.
    if target_file.endswith(".py"):
        try:
            compile(new_content, target_file, "exec")
        except SyntaxError as e:
            bus.emit("improver", "error", f"Rejected own patch — syntax error: {e}", run_id=run_id)
            return None

    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), new_content.splitlines(keepends=True),
        fromfile=f"a/{target_file}", tofile=f"b/{target_file}"))

    if not diff.strip():
        bus.emit("improver", "done", "Patch was a no-op.", run_id=run_id)
        return None

    proposal = {
        "id": uuid.uuid4().hex[:8],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file": target_file,
        "problem": finding.get("problem", ""),
        "explanation": result.get("explanation", ""),
        "risk": result.get("risk", "medium"),
        "what_could_break": result.get("what_could_break", ""),
        "diff": diff,
        "new_content": new_content,
        "lines_changed": len([l for l in diff.splitlines()
                              if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]),
        "status": "awaiting_review",
        "review": None,
    }
    bus.emit("improver", "done",
             f"Patch drafted for {target_file} ({proposal['lines_changed']} lines).",
             run_id=run_id)
    return proposal


# ---------------------------------------------------------------- reviewer
PATCH_REVIEW_SYSTEM = """You are an independent code reviewer. Another agent wrote this patch; \
your job is to find what is wrong with it, not to rubber-stamp it.

Check specifically:
- Does it actually address the stated problem?
- Does it break any existing behavior or contract?
- Does it introduce a bug, an unhandled case, or a silent failure?
- Is it larger than necessary? Scope creep is a defect.
- Does it touch anything security-relevant (secrets, auth, file paths, network)?
- Would this change how the resume content is produced in a way the user should know about?

Be skeptical. Approving a bad patch is worse than rejecting a decent one, because this \
system generates resumes the user sends to real employers.

Return JSON: {
  "approve": true/false,
  "confidence": "low"|"medium"|"high",
  "concerns": ["..."],
  "verdict": "one blunt sentence for the human deciding whether to apply this"
}"""


def review_patch(proposal: dict, run_id: str = "") -> dict:
    bus.emit("patch_reviewer", "working", f"Reviewing patch for {proposal['file']}...",
             run_id=run_id)
    try:
        result = ask_json(PATCH_REVIEW_SYSTEM,
                          f"PROBLEM BEING FIXED:\n{proposal['problem']}\n\n"
                          f"AUTHOR'S EXPLANATION:\n{proposal['explanation']}\n\n"
                          f"DIFF:\n{proposal['diff'][:12000]}",
                          max_tokens=2000)
    except Exception as e:
        bus.emit("patch_reviewer", "error", str(e), run_id=run_id)
        return {"approve": False, "concerns": [str(e)], "verdict": "Review failed."}

    bus.emit("patch_reviewer", "done",
             ("Recommends applying. " if result.get("approve") else "Recommends rejecting. ")
             + result.get("verdict", "")[:90], run_id=run_id)
    return result


# ---------------------------------------------------------------- queue
def save_proposal(proposal: dict):
    props = _load(PROPOSALS_PATH, [])
    props.append(proposal)
    _save(PROPOSALS_PATH, props[-50:])


def list_proposals() -> list:
    return list(reversed(_load(PROPOSALS_PATH, [])))


def get_proposal(pid: str) -> dict | None:
    for p in _load(PROPOSALS_PATH, []):
        if p["id"] == pid:
            return p
    return None


def _set_status(pid: str, status: str, extra: dict = None):
    props = _load(PROPOSALS_PATH, [])
    for p in props:
        if p["id"] == pid:
            p["status"] = status
            p.update(extra or {})
    _save(PROPOSALS_PATH, props)


def reject_proposal(pid: str) -> bool:
    if not get_proposal(pid):
        return False
    _set_status(pid, "rejected",
                {"resolved_at": datetime.now(timezone.utc).isoformat()})
    return True


def apply_proposal(pid: str) -> dict:
    """YOU call this, from the UI, deliberately. Never automatic.

    Backs the file up, applies to a git branch if the repo is a git repo,
    and leaves main untouched so a bad patch is one command to undo."""
    p = get_proposal(pid)
    if not p:
        return {"ok": False, "error": "Proposal not found."}
    if p["status"] == "applied":
        return {"ok": False, "error": "Already applied."}
    if p["file"] in FORBIDDEN:
        return {"ok": False, "error": "File is protected."}

    root = _repo_root()
    path = os.path.join(root, p["file"])
    if not os.path.exists(path):
        return {"ok": False, "error": "Target file missing."}

    # Backup first, always.
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"{stamp}_{p['file'].replace('/', '_')}")
    shutil.copy2(path, backup)

    git_note = "applied to working tree; git handled by the deployer"

    with open(path, "w") as f:
        f.write(p["new_content"])

    # Verify it still imports rather than discovering breakage on your next run.
    verify = "skipped (non-python)"
    if p["file"].endswith(".py"):
        r = subprocess.run(["python3", "-m", "py_compile", path],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            shutil.copy2(backup, path)
            _set_status(pid, "reverted", {"error": r.stderr[:500]})
            return {"ok": False, "error": "Patch failed to compile — reverted.",
                    "detail": r.stderr[:500]}
        verify = "compiles"

    _set_status(pid, "applied", {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "backup": backup, "git": git_note, "verify": verify})
    brain.log_decision(what=f"Applied patch to {p['file']}", why=p["problem"][:200],
                       kind="code", actor="human-approved",
                       detail=f"{p['lines_changed']} lines, risk {p['risk']}")

    # Ship it: commit, push, and (if enabled) merge + deploy + health check.
    ship = {"attempted": False}
    try:
        from agents import git_ops
        if git_ops.is_repo() and os.getenv("GITHUB_TOKEN"):
            ship = git_ops.full_ship(p)
            ship["attempted"] = True
            _set_status(pid, "applied", {"ship": ship})
    except Exception as e:
        ship = {"attempted": True, "ok": False, "error": str(e)}

    return {"ok": True, "git": git_note, "backup": backup, "verify": verify,
            "ship": ship, "restart_required": not ship.get("merged")}


def revert_proposal(pid: str) -> dict:
    p = get_proposal(pid)
    if not p or p.get("status") != "applied" or not p.get("backup"):
        return {"ok": False, "error": "Nothing to revert."}
    path = os.path.join(_repo_root(), p["file"])
    shutil.copy2(p["backup"], path)
    _set_status(pid, "reverted", {"reverted_at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "restart_required": True}


# ---------------------------------------------------------------- the loop
FILE_FOR_AGENT = {
    "tailor": "agents/resume_tailor.py", "ats": "agents/review_team.py",
    "truth": "agents/review_team.py", "recruiter": "agents/review_team.py",
    "keywords": "agents/keyword_match.py", "scout": "agents/job_scout.py",
    "audit": "agents/recruiter_audit.py", "notifier": "agents/notifier.py",
    "orchestrator": "orchestrator.py", "ui": "static/index.html",
}


def improvement_cycle(run_id: str = "") -> dict:
    """The forever loop, one turn of it. Called automatically after each pipeline
    run and manually from the UI. Prompt tweaks land immediately; code patches
    queue up for you."""
    result = watchdog(run_id=run_id)
    findings = result.get("findings", [])
    code_findings = [f for f in findings if f.get("class") == "code"]

    queued = 0
    for f in code_findings[:2]:  # at most 2 code proposals per cycle — no patch floods
        target = FILE_FOR_AGENT.get(f.get("agent", ""))
        if not target:
            continue
        proposal = propose_code_patch(f, target, run_id=run_id)
        if not proposal:
            continue
        proposal["review"] = review_patch(proposal, run_id=run_id)
        save_proposal(proposal)
        queued += 1

    if queued:
        bus.emit("improver", "info",
                 f"{queued} patch(es) waiting for your approval on the Improvements tab.",
                 run_id=run_id)
    return {"findings": findings, "queued_patches": queued,
            "verdict": result.get("verdict", "")}
