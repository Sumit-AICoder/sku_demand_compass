---
name: git-push
description: Commit and push code changes to the Sonalika Demand Compass GitHub repo (Sumit-AICoder/sku_demand_compass). Use whenever the user asks to push, commit, sync to GitHub, "put this on git/GitHub", or update the repo.
---

# Pushing to GitHub

Repo: **https://github.com/Sumit-AICoder/sku_demand_compass** (branch `main`).

This is a data-heavy project (1.1GB of generated parquet at full scope) sitting next to
real API credentials in `.env`. The two things that actually went wrong the first time
this was set up were **the wrong GitHub account being logged in** (a 403, not a secrets
leak) and **not verifying what was staged before committing**. Both are checked below,
in order, every time — not just on the first push.

## Before touching git: verify the account

```bash
gh auth status
```

Compare the logged-in account against who should own commits on this repo. The first
attempt here was authenticated as a *different* GitHub account (`Pragatik19`) with no
write access to `Sumit-AICoder/sku_demand_compass` — the push failed with a plain `403`,
which is easy to misread as a repo problem when it's actually an identity problem.

**If it's the wrong account** (or logged out):

```bash
gh auth logout --hostname github.com   # only if a wrong account is active
gh auth login --hostname github.com --git-protocol https --web
```

This prints a one-time code and a URL (`https://github.com/login/device`). Hand the
code to the user — this is an interactive device-flow login only they can approve, the
same shape as the Railway login in the `deploy` skill. Run the wait as a bounded
background command, not a blocking sleep loop:

```bash
until gh auth status 2>&1 | grep -q "Logged in"; do sleep 5; done
```

Once it reports the *correct* account, configure git to use it and proceed:

```bash
gh auth setup-git
```

## Before every commit: verify what's staged

Never `git add -A` and commit blind on this repo. Stage, then look:

```bash
git add -A
git status --short
```

Specifically check for these, because they are exactly the things that must never be
committed here:

```bash
# any secret-bearing file about to be staged
git status --short | grep -iE "\.env$|\.env\.local|chat_sessions|\.pytest_cache"

# a broad pattern scan across the actual file contents, not just filenames
grep -rlE "AZURE_OPENAI_API_KEY\s*[:=]\s*['\"]?[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}" \
  --include="*.py" --include="*.ts" --include="*.tsx" --include="*.md" \
  --include="*.yaml" --include="*.yml" --include="*.json" . 2>/dev/null \
  | grep -v -E "\.venv/|node_modules/|\.git/"

# total size of what's about to be committed -- should be a few MB, not gigabytes
git ls-files | xargs du -ch 2>/dev/null | tail -1
```

All three commands should come back **empty / small**. If the size check comes back
large, something in `data/curated` or `data/marts` slipped past `.gitignore` — stop and
find out why before committing, don't just `git rm --cached` and move on without
understanding how it got staged.

## What belongs in git, and what doesn't

Already encoded in `.gitignore` — this list exists so a change to that file gets a
sanity check against it, not so it's re-typed by hand:

| In git | Not in git | Why not |
|---|---|---|
| `pipeline/`, `api/`, `web/src/` | `data/curated/`, `data/marts/` | ~1.1GB generated output; `python -m pipeline.run` regenerates it (~65s) |
| `requirements.txt`, `requirements-serve.txt` | `.venv/`, `web/node_modules/` | reinstall from the manifest, don't commit the environment |
| `.env.example` (placeholder values) | `.env`, `.env.local` | real Azure OpenAI / Anthropic credentials |
| `web/vercel.json` (a public backend URL, not a secret) | `web/.vercel/`, `web/.env.local` | Vercel project link + OIDC token — already caught by `web/.gitignore` |
| `pipeline/tests/`, `DEPLOY.md`, this skill | `data/chat_sessions.json`, `.pytest_cache/` | runtime/test state, not source |

If a new top-level data or cache directory shows up in `git status` that isn't in this
table, don't commit it reflexively — work out which column it belongs in first.

## Commit and push

```bash
git commit -m "$(cat <<'EOF'
<one-line summary of what changed and why>

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
git push -u origin main   # -u only needed the first time a branch is pushed
```

## After pushing: verify, don't just trust the exit code

```bash
gh repo view Sumit-AICoder/sku_demand_compass --json pushedAt,defaultBranchRef
git ls-tree -r origin/main --name-only | wc -l     # sanity-check the file count
git log --all --source --remotes -- .env            # must be EMPTY, always
```

That last check matters even if `.env` was gitignored from the start: it confirms the
file was never committed at any point in history, not merely absent from the current
tree (a `.gitignore` entry added after a file was already tracked doesn't remove it from
history — this check is what actually proves the secret never left the machine).

## Commit author identity

If `git commit` warns about an auto-detected name/email (e.g. a hostname-based address
like `user@Users-MacBook-Pro.local`), that's cosmetic and the commit still succeeds —
but flag it to the user rather than silently leaving a wrong-looking author on a
public repo. Only change it if they ask:

```bash
git config user.name "..."
git config user.email "..."
git commit --amend --reset-author   # only for the most recent, not-yet-pushed commit
```

Never run `git config --global` without being asked explicitly — it changes the user's
identity for every other repo on the machine, not just this one.
