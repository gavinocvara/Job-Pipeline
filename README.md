# Application Desk

A human-in-the-loop, multi-agent job application system for Gavino Vara.
Targets **Austin, TX**. Runs as a website you can use from your phone.

Nine agents find postings, tailor your resume, tear it apart, and hand you a
finished package with the apply link. **You press submit.** Nothing auto-applies.

## The crew

| Agent | Job |
|---|---|
| **Job Scout** | Searches live Austin postings, scores each 0–100 for fit |
| **Resume Tailor** | Rewrites/reorders your resume for the specific JD |
| **ATS Reviewer** | Parseability, formatting, tense, length |
| **Truth Reviewer** | Flags anything not traceable to your real experience |
| **Recruiter Reviewer** | The 6-second skim test |
| **Keyword Team** | Extracts what the ATS scans for, scores coverage |
| **Senior Recruiter Audit** | Score /100, missing keywords, red flags, strong vs weak sections, how you compare to a strong candidate |
| **Notifier** | Emails you the resume + best way to apply |
| **Master Coach** | Teaches the other agents after every run |
| **Watchdog** | Scans run history for recurring problems |
| **Improver** | Drafts code patches for the recurring ones |
| **Patch Reviewer** | Independently vets each patch before you see it |
| **Deployer** | Commits approved fixes, pushes to GitHub, deploys, rolls back failures |

The three reviewers run **in parallel** — they're independent and don't see each
other's verdicts. The orchestrator merges them and loops the Tailor until they
all pass (max 3 rounds).

## How the team actually improves

Two loops, and it's worth knowing which is which.

**Loop 1 — cross-agent teaching (every run, automatic).** The Senior Recruiter
Audit runs *last*, on the final resume the review team already approved. Anything
the audit catches that the reviewers missed is, by definition, a reviewer failure.
The Coach routes it: audit found a red flag the Recruiter Reviewer passed → the
Recruiter Reviewer gets a lesson. Audit found missing keywords the Keyword Team
scored as fine → Keyword Team gets a lesson. Those lessons persist in
`storage/lessons.json` and get injected into that agent's system prompt on every
future run. That's the agents teaching each other.

**Loop 2 — real outcomes (manual, slower, more valuable).** Record what actually
happened (`applied`, `no_response`, `interview`, `rejected`, `offer`) on the
Applications tab. Hit "Learn from recorded outcomes" and the Coach looks for
patterns across your history.

Straight talk on Loop 2: with fewer than ~10 resolved outcomes it will tell you
the data is too thin rather than invent a pattern. That's deliberate. A confident-
sounding "finding" from 3 data points would get injected into every future
application and quietly make things worse. Loop 1 makes the team more internally
consistent; only Loop 2 tells you whether any of it works on real recruiters.

## The self-improvement loop

Three layers, separated by how much damage a bad change could do.

**Prompt refinements — automatic.** The Watchdog spots a recurring problem
("summary is generic across 3 runs"), writes an instruction fix, and it applies
immediately to that agent. Stored as an overlay, never touching code, one tap to
turn off. This is where your "little minor tweaks" live.

**Code patches — you approve.** For real bugs, the Improver writes a minimal
patch, the Patch Reviewer independently tries to poke holes in it, and it lands
on your Improve tab with the diff, the risk level, and an honest "what could
break." You tap Apply. It backs up the file, creates a git branch, applies, and
compiles-checks — auto-reverting if it fails. One tap to revert afterward.

**Protected files.** `config.py`, `.env`, `server.py`, `claude_client.py`, the
deploy configs, and `improver.py` itself cannot be patched. The improvement system
can't rewrite its own safety rails or touch anything holding secrets.

### Why it doesn't just apply its own patches

You asked for it to improve forever on its own. The loop *does* run forever — it
scans after every single run and never stops proposing. The gate is only on code.

An agent that edits its own source and redeploys unattended has no error floor.
A patch that passes a syntax check can still quietly degrade every resume you
send, and the next cycle improves on top of the broken state. You'd find out
weeks later from a silence you couldn't explain. Reviewing a diff takes fifteen
seconds; that's the cheapest insurance in the whole system. Prompt tweaks run
free because they're instantly reversible and can't break anything.

## The Brain

`storage/brain.json` is the one file everything reads from and writes to,
forever. It renders to **BRAIN.md** after every write, so you can read what the
system has learned straight from GitHub without running anything.

Five sections:

- **Profile** — durable facts about you, including your known gaps. Injected into
  the Tailor and Audit prompts so they stop rediscovering the same things.
- **Playbook** — tactics that demonstrably work. Entries carry a confidence and
  a hit count; re-observing one promotes it (3 hits → medium, 5 → high).
- **Companies** — per-employer history: how many times you've applied, average
  audit score there, which flags keep recurring.
- **Agent notes** — what each agent keeps getting wrong.
- **Decision log** — append-only record of every change the system made to
  itself. Never decays. If something breaks in three months, this is how you
  find which change did it.

The brain **decays**, deliberately. Entries that stop being corroborated get
demoted over time. A memory that only accumulates fills up with stale beliefs
stated at full confidence, which is worse than an empty one.

## Shipping its own fixes

When you approve a patch on the Improve tab, the Deployer:

1. Commits it to a branch (`improve/<id>`)
2. Scans the staged diff for anything key-shaped — blocks the commit if found
3. Pushes to GitHub
4. If `AUTO_MERGE` and `AUTO_DEPLOY` are on: merges to main, which triggers Render
5. Health-checks your live URL for up to 7 minutes
6. **If the health check fails, restores the last good commit and force-pushes it**,
   so Render redeploys the working version

Step 6 is what makes step 4 safe to turn on. Without a `HEALTH_CHECK_URL`, a bad
deploy stays broken until you notice; with it, the app self-heals in about two
minutes and logs what happened to the brain.

Hard limits regardless of settings: patches the reviewer rejected never merge,
high-risk patches never merge, and `MAX_DEPLOYS_PER_DAY` (default 6) caps how
often it can touch production so a bad loop can't thrash it.

Start with `AUTO_MERGE=false`. Watch a few patches land as branches first, see
whether you agree with them, then turn it on.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in your keys
uvicorn server:app --reload --port 8000
```

Open `http://localhost:8000`.

### Keys you need

| Key | Required? | Where |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | console.anthropic.com |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | For job search | developer.adzuna.com (free) |
| `SMTP_USER` / `SMTP_PASS` / `NOTIFY_EMAIL` | For email alerts | Gmail [App Password](https://myaccount.google.com/apppasswords) |

Without Adzuna keys everything still works — you just paste job descriptions
manually instead of searching.

## Deploy it (so it's on your phone)

**Render** (free tier, easiest):
1. Push this folder to a GitHub repo
2. render.com → New → Web Service → connect the repo
3. It reads `render.yaml` automatically
4. Add your keys under Environment
5. Open the URL on your phone → Share → Add to Home Screen

Railway and Fly.io work the same way via the `Procfile`.

Note on the free tier: it sleeps after inactivity, so the first request after a
gap takes ~30s to wake. Also, `storage/` is ephemeral on Render's free plan —
your application log resets on redeploy. If you want history to persist, attach
a Render disk or move `storage/` to a small Postgres/S3 bucket.

## Using it

**Find jobs** → searches Austin postings across SWE, embedded, full-stack, and
technical sales tracks, scores each, and shows why. Tap "Build resume for this"
on any of them.

**Build resume** → paste a JD (or a link) and the crew runs. Bubbles light up
live as each agent works. You get the resume download, the audit scorecard, and
the apply link.

**Audit only** → runs just the senior-recruiter audit against any resume you
paste. Useful for checking a version you already have.

**Tracker** → every application with a full dated timeline. Tap "Just applied",
"Heard back", "Interview on…", "Rejected", "No response", or "Offer" and it
timestamps the event and keeps the history. Interviews you schedule show as
upcoming; anything applied 10+ days ago with no reply gets flagged for follow-up.
Nothing overwrites — you keep the whole story of each application.

**Improve** → the patch approval queue and the list of prompt refinements that
applied on their own.

**Team** → the lessons each agent has accumulated.

## Deploying and keeping keys safe

See **DEPLOY.md** (short version: Render or Railway, not Vercel — Vercel's
serverless timeouts kill multi-minute runs and SSE) and **SECRETS.md** (short
version: `.env` is gitignored, keys go in the host's environment panel, and no
key ever reaches the browser).

## Your master resume

`data/master_resume.json` is the single source of truth, merged from your two
uploaded resumes. The Truth Reviewer treats it as ground truth — if it's not in
there, no agent is allowed to claim it.

Skills are split into `core` (real hands-on depth) and `extended` (exposure-level:
React, AWS, Firebase, GCP, AutoCAD). Extended skills only surface when a JD calls
for them, and never phrased as expertise. Edit this file as you gain experience.

## Why it doesn't auto-submit

LinkedIn, Indeed, and Workday all prohibit automated submission in their terms,
and they run bot detection on application forms. An auto-apply bot risks your
accounts getting banned — and the volume play doesn't work anyway when the same
generic resume goes out 200 times. This does the expensive part (genuinely
tailoring and stress-testing each application) and leaves you the 30 seconds of
clicking.

Job search uses the Adzuna API rather than scraping search pages, for the same
reason: it's a real API with real terms, so nothing here puts your accounts at risk.

## Files

```
server.py             FastAPI + SSE live agent stream
orchestrator.py       wires the agents together, owns the run
main.py               CLI (same pipeline, terminal version)
config.py             env settings
data/master_resume.json    your ground truth
agents/
  event_bus.py        live status -> the bubbles
  job_scout.py        Adzuna search + fit scoring
  resume_tailor.py    Agent 1
  review_team.py      the 3 parallel reviewers
  keyword_match.py    keyword extraction + scoring
  recruiter_audit.py  the brutal scorecard
  notifier.py         email + packet
  coach.py            lessons + cross-agent teaching
  resume_writer.py    docx renderer (deterministic, not an LLM)
  jd_intake.py        URL fetch / paste, ATS portal detection
static/index.html     the mobile UI
storage/              applications.json, lessons.json
output/               generated resumes
```
