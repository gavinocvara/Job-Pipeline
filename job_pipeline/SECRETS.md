# Keeping your API keys safe

Short version: **keys live in environment variables, never in code, never in
git.** `.env` is already in `.gitignore`, so the file holding your real keys
cannot be committed even by accident.

## The split

There are three separate keys, and they have very different blast radii if
leaked. Treat them accordingly.

| Key | If leaked | Rotate at |
|---|---|---|
| `ANTHROPIC_API_KEY` | Someone bills your account. Highest risk. | console.anthropic.com → API Keys |
| `ADZUNA_APP_KEY` | Read-only job search. Low risk, still rotate. | developer.adzuna.com |
| `SMTP_PASS` | Someone sends email as you. High risk. | Google App Passwords |

**Use a Gmail App Password, never your actual Google password.** Generate one at
myaccount.google.com/apppasswords. It's scoped to one app and revocable on its own,
so revoking it doesn't lock you out of your account.

## Local setup

```bash
cp .env.example .env      # .env is gitignored
# open .env, paste your real keys
```

Never paste a real key into `.env.example`, into README, into a commit message,
or into a screenshot. `.env.example` is the template that gets committed —
it holds placeholder text only.

## On the server (Render / Railway)

You do **not** upload `.env`. You paste each key into the host's Environment
Variables panel, where they're encrypted at rest and injected at runtime.

Render: Dashboard → your service → Environment → Add Environment Variable.
Railway: project → Variables tab.

`render.yaml` lists every key with `sync: false`, which means "prompt me for this,
don't store it in the repo." The repo describes *which* keys exist; it never
contains their values.

## Verify before you push

```bash
git status --porcelain | grep -i env    # should show nothing but .env.example
git ls-files | grep -i "\.env$"         # should print nothing at all
```

If either prints something unexpected, stop and fix it before pushing.

## If you leak a key anyway

It happens. Do this in order:

1. **Rotate the key immediately** at the provider. Do this first — a key in a
   public repo is scraped by bots within minutes, so revoking beats cleaning.
2. Remove the file from tracking: `git rm --cached .env && git commit`
3. Deleting the commit is not enough. It stays in git history and in anyone's
   fork. `git filter-repo` or GitHub's support can purge it, but assume the old
   key is burned regardless.

The order matters. Rotating a key takes 30 seconds and makes the leak harmless;
scrubbing history takes an hour and doesn't.

## One thing this app does right

No key ever reaches the browser. Every API call happens server-side in Python —
the frontend only ever talks to your own `/api/*` endpoints. If you later add a
client-side call to a third-party API, that key *would* be visible in devtools.
Don't.
