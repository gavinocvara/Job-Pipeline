# One-time GitHub setup. Run from inside the job_pipeline folder:
#     bash setup_github.sh
#
# Creates the repo, verifies no secrets are staged, and pushes.
set -euo pipefail

BOLD=$(tput bold 2>/dev/null || echo ""); RESET=$(tput sgr0 2>/dev/null || echo "")
say() { echo "${BOLD}==>${RESET} $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

[ -f "server.py" ] || die "Run this from inside the job_pipeline folder."

# ---------------------------------------------------------------- 1. safety
say "Checking that no secrets are about to be committed"
[ -f ".gitignore" ] || die ".gitignore is missing. Do not push without it."
grep -qx "\.env" .gitignore || die ".gitignore does not block .env. Stopping."

if [ -f ".env" ]; then
  say ".env found locally — it is gitignored and will NOT be pushed."
fi

# ---------------------------------------------------------------- 2. git init
if [ ! -d ".git" ]; then
  say "Initializing git repository"
  git init -q
  git branch -M main
else
  say "Git repository already initialized"
fi

git config user.name  >/dev/null 2>&1 || git config user.name "Gavino Vara"
git config user.email >/dev/null 2>&1 || {
  read -rp "Your GitHub email: " GH_EMAIL
  git config user.email "$GH_EMAIL"
}

# ---------------------------------------------------------------- 3. stage
say "Staging files"
git add -A

if git diff --cached --name-only | grep -qx ".env"; then
  git reset >/dev/null
  die ".env got staged. Fix .gitignore before continuing."
fi

say "Scanning staged content for anything key-shaped"
if git diff --cached | grep -Eq 'sk-ant-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}'; then
  git reset >/dev/null
  die "Found something that looks like an API key in the staged changes. Remove it first."
fi
say "Clean. Nothing secret is staged."

# ---------------------------------------------------------------- 4. commit
if git rev-parse HEAD >/dev/null 2>&1; then
  git commit -q -m "Update application desk" || say "Nothing new to commit"
else
  git commit -q -m "Application Desk: multi-agent job application system"
fi

# ---------------------------------------------------------------- 5. remote
if git remote get-url origin >/dev/null 2>&1; then
  say "Remote 'origin' already set: $(git remote get-url origin)"
else
  echo
  echo "Create an EMPTY repo at https://github.com/new"
  echo "  - No README, no .gitignore, no license (this folder has them)"
  echo "  - Private is fine and recommended"
  echo
  read -rp "Repo in owner/name form (e.g. gavinovara/application-desk): " REPO
  [ -n "$REPO" ] || die "Repo name required."
  git remote add origin "https://github.com/${REPO}.git"
  say "Remote set. Add this to your .env so the agents can push:"
  echo "    GITHUB_REPO=${REPO}"
fi

# ---------------------------------------------------------------- 6. push
echo
say "Pushing to GitHub"
echo "When prompted for a password, paste a Personal Access Token, not your"
echo "GitHub password. Create one at:"
echo "    https://github.com/settings/tokens?type=beta"
echo "Give it: Repository access -> only this repo, Permissions -> Contents: Read and write"
echo
git push -u origin main

echo
say "Done."
echo
echo "Next:"
echo "  1. Add GITHUB_TOKEN and GITHUB_REPO to your .env (so agents can push their own fixes)"
echo "  2. Deploy on Render: render.com -> New -> Web Service -> pick this repo"
echo "  3. Add every key from .env into Render's Environment tab"
echo "  4. Set HEALTH_CHECK_URL in Render to your Render URL (enables auto-rollback)"
echo "  5. Open the URL on your phone -> Share -> Add to Home Screen"
