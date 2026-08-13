# Putting this live on your phone

## Why not Vercel

You asked about Vercel. It's the wrong tool here, and it would fail in a way
that's genuinely annoying to debug, so it's worth being specific:

| What this app needs | What Vercel gives you |
|---|---|
| 2–5 min pipeline runs | Serverless functions time out (10s hobby / 60s pro) |
| A held-open SSE connection for live agent bubbles | Connections close when the function returns |
| Writable disk for your tracker and lessons | Filesystem is read-only and ephemeral |
| A long-running background thread per run | Execution ends when the response is sent |

Your run would die partway through, the bubbles would freeze, and your
application history would vanish on every deploy. Vercel is excellent for
frontends and short API calls — this is neither.

**Use Render or Railway.** Same result: a URL you open on your phone, live agent
view, everything working.

## Render (recommended)

1. Push to GitHub: `bash setup_github.sh` (it runs the safety checks for you).
2. render.com → New → Web Service → connect your repo.
3. It reads `render.yaml` automatically.
4. Environment tab → add each key from your `.env`:
   `ANTHROPIC_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
   `SMTP_USER`, `SMTP_PASS`, `NOTIFY_EMAIL`
5. Also add `GITHUB_TOKEN`, `GITHUB_REPO`, and `HEALTH_CHECK_URL` (your Render
   URL) so the agents can ship their own fixes and roll back bad ones.
6. Deploy. Open the URL on your phone → Share → **Add to Home Screen**.

**Add a disk** (Render dashboard → Disks, 1GB is plenty, mount at
`/opt/render/project/src/storage`). Without it, the free tier wipes
`storage/` on every deploy and you lose your application history and lessons.

Free tier sleeps after ~15 min idle, so the first request after a gap takes
about 30 seconds to wake. The $7/mo tier removes that and includes the disk.

## Railway

Same flow, reads `railway.json`. Variables tab for keys. Add a volume mounted at
`/app/storage`. No sleep on the paid plan.

## Before you push — the 30-second safety check

```bash
git status --porcelain | grep -i "\.env$"   # must print NOTHING
git ls-files | grep -i "\.env$"             # must print NOTHING
cat .gitignore | grep -x "\.env"            # must print .env
```

If `.env` shows up in either of the first two, do not push. See `SECRETS.md`.

## After deploying

Keys live in the host's environment panel, never in the repo. Your local `.env`
and the deployed environment are separate — updating one doesn't update the
other.

If you approve a code patch on the Improve tab while running on Render, it
applies to the *server's* filesystem, which gets wiped on the next deploy. For
patches you want to keep, apply them locally, review the branch, and push. The
Improve tab is a proposal queue you can read from anywhere; treat applying as
something you do on your own machine.
