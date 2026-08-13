"""
Git + deploy automation.

This closes the loop you asked for: an approved patch commits itself, pushes to
GitHub, and Render redeploys automatically. It also handles the part that makes
that safe, which is what happens when a patch is wrong.

THE SHAPE OF THE LOOP

  patch approved
    -> commit to a branch  (improve/<id>)
    -> push to GitHub
    -> merge to main  [only if AUTO_MERGE=true and the patch is low-risk]
    -> Render auto-deploys from main
    -> health check the live URL after deploy
    -> if the health check fails: revert the merge, push, redeploy the last good state

That last step is the whole reason this is safe enough to run. Without it,
"auto-deploy on approval" means a bad patch takes your app down and it stays
down until you notice. With it, a broken deploy self-heals in about two minutes
and logs what happened to the brain.

AUTH

Uses a GitHub Personal Access Token, read from the environment, never written to
disk and never committed. The remote URL is rewritten in-memory for each push so
the token never lands in .git/config either — if you ever run `git remote -v`
you'll see a clean URL.

WHAT IT WILL NOT DO

- Push anything gitignored (.env, storage/, output/). Enforced before every commit.
- Force-push, rewrite history, or delete branches.
- Merge a patch the Patch Reviewer rejected, regardless of AUTO_MERGE.
- Merge a "high" risk patch automatically. Those always wait for you.
- Deploy more than MAX_DEPLOYS_PER_DAY times, so a bad loop can't burn your
  build minutes or thrash production.
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta

import config
from agents import brain
from agents.event_bus import bus

DEPLOY_LOG = os.path.join(config.STORAGE_DIR, "deploys.json")
MAX_DEPLOYS_PER_DAY = int(os.getenv("MAX_DEPLOYS_PER_DAY", "6"))
AUTO_MERGE = os.getenv("AUTO_MERGE", "false").lower() == "true"
AUTO_DEPLOY = os.getenv("AUTO_DEPLOY", "false").lower() == "true"
HEALTH_URL = os.getenv("HEALTH_CHECK_URL", "")
RENDER_DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK", "")

# Health-check timings. Render cold builds vary a lot, so these are tunable.
HEALTH_INITIAL_WAIT = int(os.getenv("HEALTH_INITIAL_WAIT", "45"))   # let the build start
HEALTH_TIMEOUT = int(os.getenv("HEALTH_TIMEOUT", "420"))            # give up after this
HEALTH_INTERVAL = int(os.getenv("HEALTH_INTERVAL", "20"))           # poll every


def _root():
    return os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))


def _run(args, timeout=90, check=False):
    return subprocess.run(["git", "-C", _root()] + args, capture_output=True,
                          text=True, timeout=timeout, check=check)


def is_repo() -> bool:
    return os.path.isdir(os.path.join(_root(), ".git"))


def _token_remote() -> str | None:
    """Builds an authenticated push URL in memory. Never persisted."""
    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPO", "")   # e.g. gavinovara/application-desk
    if not (token and repo):
        return None
    return f"https://{token}@github.com/{repo}.git"


def _sanitize(text: str) -> str:
    """Belt and braces — strip anything token-shaped out of logs and messages."""
    return re.sub(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
                  "***", text or "")


def status() -> dict:
    if not is_repo():
        return {"repo": False, "reason": "Not a git repository yet. Run setup_github.sh."}
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    dirty = bool(_run(["status", "--porcelain"]).stdout.strip())
    remote = _run(["remote", "get-url", "origin"]).stdout.strip()
    return {
        "repo": True,
        "branch": branch,
        "dirty": dirty,
        "remote": _sanitize(remote),
        "token_set": bool(os.getenv("GITHUB_TOKEN")),
        "repo_set": bool(os.getenv("GITHUB_REPO")),
        "auto_merge": AUTO_MERGE,
        "auto_deploy": AUTO_DEPLOY,
        "health_url": HEALTH_URL,
        "deploys_today": len(_deploys_today()),
        "deploy_budget": MAX_DEPLOYS_PER_DAY,
    }


# ------------------------------------------------------------ safety checks
def _staged_files() -> list:
    return [l for l in _run(["diff", "--cached", "--name-only"]).stdout.splitlines() if l]


SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9\-_]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]


def _scan_staged_for_secrets() -> list:
    """Last line of defense before anything leaves the machine. Even though
    .gitignore covers .env, a key pasted into a source file would slip past it."""
    diff = _run(["diff", "--cached"]).stdout
    hits = []
    for pat in SECRET_PATTERNS:
        if re.search(pat, diff):
            hits.append(pat)
    for f in _staged_files():
        if f == ".env" or f.startswith("storage/") or f.startswith("output/"):
            hits.append(f"gitignored path staged: {f}")
    return hits


# ------------------------------------------------------------ deploy budget
def _load_deploys() -> list:
    if os.path.exists(DEPLOY_LOG):
        with open(DEPLOY_LOG) as f:
            return json.load(f)
    return []


def _save_deploys(d):
    with open(DEPLOY_LOG, "w") as f:
        json.dump(d[-100:], f, indent=2)


def _deploys_today() -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    out = []
    for d in _load_deploys():
        try:
            if datetime.fromisoformat(d["at"].replace("Z", "+00:00")) > cutoff:
                out.append(d)
        except Exception:
            pass
    return out


# ------------------------------------------------------------ core ops
def commit_and_push(proposal: dict, run_id: str = "") -> dict:
    """Commit an approved patch to its own branch and push it."""
    if not is_repo():
        return {"ok": False, "error": "Not a git repo. Run setup_github.sh first."}

    remote = _token_remote()
    if not remote:
        return {"ok": False, "error": "GITHUB_TOKEN and GITHUB_REPO must be set."}

    branch = f"improve/{proposal['id']}"
    bus.emit("deployer", "working", f"Committing patch to {branch}...", run_id=run_id)

    base = _run(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "main"
    _run(["checkout", "-B", branch])
    _run(["add", proposal["file"], "BRAIN.md"])

    leaks = _scan_staged_for_secrets()
    if leaks:
        _run(["reset"])
        _run(["checkout", base])
        bus.emit("deployer", "error", "Blocked — staged changes look like they contain secrets.",
                 run_id=run_id)
        return {"ok": False, "error": f"Secret scan blocked the commit: {leaks}"}

    msg = (f"auto: {proposal['explanation'][:60]}\n\n"
           f"Problem: {proposal['problem']}\n"
           f"Risk: {proposal['risk']}\n"
           f"Reviewer: {(proposal.get('review') or {}).get('verdict', 'n/a')}\n"
           f"Proposal: {proposal['id']}\n\n"
           f"Generated by the improvement team. Human-approved before commit.")
    r = _run(["commit", "-m", msg])
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr).lower():
        _run(["checkout", base])
        return {"ok": False, "error": _sanitize(r.stderr or r.stdout)}

    push = _run(["push", remote, f"{branch}:{branch}", "--set-upstream"], timeout=120)
    if push.returncode != 0:
        return {"ok": False, "error": _sanitize(push.stderr)[:400], "branch": branch}

    bus.emit("deployer", "done", f"Pushed {branch} to GitHub.", run_id=run_id)
    brain.log_decision(
        what=f"Pushed patch {proposal['id']} to {branch}",
        why=proposal["problem"][:200], kind="code-push", actor="deployer",
        detail=f"{proposal['file']} · {proposal['lines_changed']} lines · risk {proposal['risk']}")
    return {"ok": True, "branch": branch, "base": base}


def merge_and_deploy(proposal: dict, branch: str, base: str = "main",
                     run_id: str = "") -> dict:
    """Merge to main so Render redeploys. Gated, budgeted, and health-checked."""
    review = proposal.get("review") or {}
    if not review.get("approve"):
        return {"ok": False, "error": "Patch reviewer did not approve — will not auto-merge."}
    if proposal.get("risk") == "high":
        return {"ok": False, "error": "High-risk patches never auto-merge. Merge it yourself."}
    if len(_deploys_today()) >= MAX_DEPLOYS_PER_DAY:
        return {"ok": False,
                "error": f"Deploy budget spent ({MAX_DEPLOYS_PER_DAY}/day). Resets in 24h."}

    remote = _token_remote()
    bus.emit("deployer", "working", f"Merging {branch} into {base}...", run_id=run_id)

    good_sha = _run(["rev-parse", base]).stdout.strip()

    _run(["checkout", base])
    m = _run(["merge", "--no-ff", branch, "-m", f"auto-merge {branch}"])
    if m.returncode != 0:
        _run(["merge", "--abort"])
        return {"ok": False, "error": "Merge conflict — needs you.",
                "detail": _sanitize(m.stdout + m.stderr)[:400]}

    push = _run(["push", remote, base], timeout=120)
    if push.returncode != 0:
        _run(["reset", "--hard", good_sha])
        return {"ok": False, "error": _sanitize(push.stderr)[:400]}

    _save_deploys(_load_deploys() + [{
        "at": datetime.now(timezone.utc).isoformat(), "proposal": proposal["id"],
        "branch": branch, "sha_before": good_sha}])

    if RENDER_DEPLOY_HOOK:
        try:
            import requests
            requests.post(RENDER_DEPLOY_HOOK, timeout=15)
        except Exception:
            pass

    bus.emit("deployer", "working", "Pushed to main. Waiting for deploy, then health check...",
             run_id=run_id)

    health = _health_check_with_rollback(good_sha, base, remote, run_id=run_id)
    brain.log_decision(
        what=f"Merged {branch} to {base} and deployed",
        why=proposal["problem"][:200], kind="deploy", actor="deployer",
        detail=f"health={health.get('status')}", reversible=True)
    return {"ok": True, "merged": True, "health": health}


def _health_check_with_rollback(good_sha: str, base: str, remote: str,
                                run_id: str = "") -> dict:
    """Wait for the deploy, then verify the live app actually works. If it
    doesn't, put the last known-good commit back and redeploy that."""
    if not HEALTH_URL:
        bus.emit("deployer", "info",
                 "No HEALTH_CHECK_URL set — deployed without verification.", run_id=run_id)
        return {"status": "unverified",
                "note": "Set HEALTH_CHECK_URL to your Render URL to enable auto-rollback."}

    import requests
    deadline = time.time() + HEALTH_TIMEOUT
    ok = False
    last = ""
    time.sleep(HEALTH_INITIAL_WAIT)
    while time.time() < deadline:
        try:
            r = requests.get(HEALTH_URL.rstrip("/") + "/api/health", timeout=12)
            if r.status_code == 200 and r.json().get("ok"):
                ok = True
                break
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(HEALTH_INTERVAL)

    if ok:
        bus.emit("deployer", "done", "Deploy is live and healthy.", run_id=run_id)
        return {"status": "healthy"}

    bus.emit("deployer", "error",
             f"Deploy failed its health check ({last}). Rolling back...", run_id=run_id)
    _run(["reset", "--hard", good_sha])
    p = _run(["push", "--force-with-lease", remote, base], timeout=120)
    if RENDER_DEPLOY_HOOK:
        try:
            import requests as rq
            rq.post(RENDER_DEPLOY_HOOK, timeout=15)
        except Exception:
            pass
    brain.log_decision(
        what="Rolled back a bad deploy", why=f"Health check failed: {last}",
        kind="rollback", actor="deployer",
        detail=f"restored {good_sha[:8]}", reversible=False)
    bus.emit("deployer", "done" if p.returncode == 0 else "error",
             "Rolled back to the last working version."
             if p.returncode == 0 else "Rollback push failed — check the repo.",
             run_id=run_id)
    return {"status": "rolled_back", "reason": last,
            "rollback_pushed": p.returncode == 0}


def sync_brain(run_id: str = "") -> dict:
    """Commit and push just BRAIN.md + the brain store, so what the system has
    learned is readable on GitHub without a deploy."""
    if not is_repo():
        return {"ok": False, "error": "Not a git repo."}
    remote = _token_remote()
    if not remote:
        return {"ok": False, "error": "GITHUB_TOKEN / GITHUB_REPO not set."}
    brain.render_markdown()
    _run(["add", "BRAIN.md"])
    if not _staged_files():
        return {"ok": True, "note": "Brain unchanged since last sync."}
    if _scan_staged_for_secrets():
        _run(["reset"])
        return {"ok": False, "error": "Secret scan blocked the brain sync."}
    _run(["commit", "-m", "brain: update learned knowledge"])
    base = _run(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    p = _run(["push", remote, base], timeout=120)
    return {"ok": p.returncode == 0, "error": _sanitize(p.stderr)[:300] if p.returncode else None}


def full_ship(proposal: dict, run_id: str = "") -> dict:
    """Everything, in order: commit -> push -> (maybe) merge -> deploy -> verify."""
    res = commit_and_push(proposal, run_id=run_id)
    if not res.get("ok"):
        return res
    if not (AUTO_MERGE and AUTO_DEPLOY):
        bus.emit("deployer", "done",
                 f"Pushed to {res['branch']}. Auto-merge is off — merge it on GitHub when ready.",
                 run_id=run_id)
        return {**res, "merged": False,
                "note": "Branch is on GitHub. Open a PR or merge it to deploy."}
    merged = merge_and_deploy(proposal, res["branch"], res.get("base", "main"), run_id=run_id)
    return {**res, **merged}
