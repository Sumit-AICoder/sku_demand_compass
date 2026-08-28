---
name: deploy
description: Deploy, redeploy, or update the live Sonalika Demand Compass (Railway backend + Vercel frontend). Use whenever the user asks to deploy, redeploy, push changes live, update the live site/dashboard, switch between the full and lightweight data scope, or check on/troubleshoot the current deployment.
---

# Deploying the Sonalika Demand Compass

Two platforms: **Vercel** serves the React frontend as static files; **Railway** runs
the FastAPI backend in a container with a persistent volume for the generated parquet
data (too large — ~1.1GB at full scope — to bake into a Docker image or fit a
serverless function). Vercel proxies `/api/*` to Railway server-side, so the browser
only ever talks to the Vercel domain.

The full narrative walkthrough, written the first time this was done from scratch,
lives in **`DEPLOY.md`** at the repo root — read it if this is a truly fresh setup (no
Railway/Vercel project exists yet). This skill is the fast path for everything short of
that: updating what's already deployed, and the exact gotcha fixes learned the hard way.

## Current live deployment

Check `railway status` and `vercel ls` for the authoritative state — projects get
recreated occasionally (e.g. after a full teardown), so treat the names below as *how
things are usually set up*, not a permanent address:

- Railway project **and** service: `sonalika-demand-compass`
- Railway volume: `sonalika-demand-compass-volume`, mounted at `/app/data`
- Vercel project: `sonalika-demand-compass`

```bash
railway status              # backend: online/offline, domain, volume usage
railway volume list --json  # volume size (cap!) and current usage
vercel ls sonalika-demand-compass
```

---

## The four things that go wrong, and their fixes

Read this section before running anything — every one of these was discovered by
hitting it, not anticipated in advance.

### 1. Volume uploads nest silently on any retry

`railway volume files upload <local> <remote>` appends the source directory *inside*
the destination whenever the destination already exists — including a destination left
half-populated by a **previous failed/timed-out attempt**. The result: files land at
`<remote>/<basename>/*.parquet` instead of `<remote>/*.parquet`, and the API's DuckDB
view builder silently skips whatever it doesn't find at the expected path (it checks
`if path.exists()` and just omits the view — no crash, just a 500 later on whichever
endpoint needed that table).

**Always use `scripts/safe_upload.sh`, never the raw `volume files upload` command
directly** — it deletes the destination first, verifies it's gone, uploads, then
verifies the file count matches and nothing nested:

```bash
.claude/skills/deploy/scripts/safe_upload.sh data/curated /app/data/curated sonalika-demand-compass-volume
.claude/skills/deploy/scripts/safe_upload.sh data/marts   /app/data/marts   sonalika-demand-compass-volume
```

If it exits non-zero, don't retry blindly — check `railway ssh -- find <remote_path>`
and clean up by hand first.

### 2. The default volume is 500MB, and there is no CLI or API way to resize it

Confirmed by searching Railway's own GraphQL schema (`railway api search size`,
`railway api describe VolumeInstanceUpdateInput`) — `VolumeInstance.sizeMB` is
read-only from the API. Resizing is **dashboard-only**: project → service → Volumes tab
→ click the volume → change size → save.

Full 3-state data is ~1.14GB, which needs the volume resized to ~2GB+ first. **Punjab
alone is ~138MB**, comfortably under the 500MB default with no resize needed — see
"Switching data scope" below. Default to the lightweight scope unless the user
specifically asks for full coverage, since it avoids a manual dashboard step entirely.

### 3. Only one volume per service

`railway volume add` refuses a second volume ("A volume is already mounted"). Mount the
single volume at `/app/data` (covers `curated/`, `marts/`, and would cover `raw/` too —
which is exactly the problem: a volume mount **shadows** whatever the Docker image had
baked in at that path). The lightweight Dockerfile doesn't bake in `data/raw` at all
(not needed at serving time — only the pipeline needs it, and the pipeline runs
locally, not in the deployed container), which sidesteps this entirely.

### 4. Secrets in Bash commands leak into logs and tool-call history

Never run `railway variable set KEY=VALUE` with the real value typed inline. Use
`scripts/set_env_from_dotenv.py`, which pipes each value through stdin so it never
appears as a visible argument:

```bash
python3 .claude/skills/deploy/scripts/set_env_from_dotenv.py AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_KEY MODEL_NAME DEPLOYMENT API_VERSION
# or push everything found in .env:
python3 .claude/skills/deploy/scripts/set_env_from_dotenv.py --all
```

---

## Recipe: update backend code only (data unchanged)

```bash
mv .gitignore .gitignore.bak   # see note below
railway up --detach --message "describe the change"
mv .gitignore.bak .gitignore
railway redeploy --yes         # only needed if `up` doesn't auto-deploy
```

**The `.gitignore` dance**: the repo's root `.gitignore` excludes `data/curated/` and
`data/marts/` (correct for git — they're large generated artifacts). If it's ever
determined that the Railway CLI's upload also honours `.gitignore` in addition to
`.railwayignore`, that would strip the volume-mount seed data from the upload too.
Renaming it out of the way for the few seconds `railway up` takes, then restoring it
immediately after, is cheap insurance — confirm whether this is still necessary with
whatever Railway CLI version is current before assuming it's load-bearing.

Then verify with the checklist at the bottom of this file.

## Recipe: update data (pipeline changed, or switching scope)

1. Regenerate data locally — **do not run the full pipeline on Railway itself**, the
   default instance (~1GB RAM) OOM-kills the heaviest stage (`sku`, a 3.9M-row table at
   full scope). Generating locally takes ~65s at full scope, ~22s for Punjab alone, and
   costs nothing extra:
   ```bash
   python -m pipeline.run
   ```
2. Upload with the safe script (§1 above), for both `data/curated` and `data/marts`.
3. Restart so the API's cached DuckDB connection picks up the fresh files — it's
   memoized for the life of the process, so a request that lands *before* the restart
   would otherwise cache an incomplete view set forever:
   ```bash
   railway redeploy --yes
   ```
4. Verify (checklist below).

## Recipe: switching data scope (full ↔ lightweight)

Do this in an **isolated copy**, never in the repo's own `data/` directory — the local
copy is what the full analytical test suite (`pipeline/tests/`) validates against, and
those tests hard-code the full 3-state counts (114 districts, 105,246 villages).
Regenerating in place would make `pytest` fail for the right reason (scope changed) but
for a confusing reason (looks like a real regression) the next time anyone runs it.

```bash
rm -rf /tmp/lightweight_build && mkdir -p /tmp/lightweight_build
rsync -a --exclude='.venv' --exclude='data' --exclude='web/node_modules' \
  --exclude='web/dist' --exclude='.git' --exclude='__pycache__' --exclude='.pytest_cache' \
  pipeline api requirements.txt requirements-serve.txt /tmp/lightweight_build/
mkdir -p /tmp/lightweight_build/data
cp -r data/raw /tmp/lightweight_build/data/raw
```

Trim `pipeline/config/districts.csv` (keep only the wanted state's rows) and the
`pilot_states:` list in `pipeline/config/sources.yaml` — both in the **temp copy**, not
the real repo. `pipeline/transform/geo_spine.py`'s `STATE_ANCHORS`/`STATE_BBOX` dicts
don't need editing; they're only ever looked up by state names actually present in
`districts.csv`, so unused entries for dropped states are harmless.

```bash
cd /tmp/lightweight_build
/path/to/repo/.venv/bin/python -m pipeline.run
du -sh data/curated data/marts   # sanity-check it actually shrank
cd -
```

Then upload from `/tmp/lightweight_build/data/...` with the safe script, and clean up
the temp directory once confirmed live.

**Switching the Dockerfile between full and lightweight**: the lightweight image uses
`requirements-serve.txt` (no `statsmodels`/`scikit-learn`/`kmodes`/`scipy` — `api/*.py`
never imports them; those exist only to *generate* the data, verified by grepping every
`api/*.py` import and running the real server against the trimmed set before trusting
it). The full image would need `requirements.txt` instead only if the deployed
container is expected to *run* the pipeline itself — which §2's advice says not to do
anyway, so `requirements-serve.txt` is very likely correct regardless of data scope.

## Recipe: fresh deploy from scratch

No existing Railway/Vercel project — follow `DEPLOY.md` in full. It has the Dockerfile,
`.railwayignore`, the SSH key setup for `railway ssh` (needed for the safe-upload
script's cleanup step — `ssh-keygen`, `railway ssh keys add`, and a
`StrictHostKeyChecking accept-new` entry in `~/.ssh/config` so the first connection
doesn't hang waiting for an interactive yes/no this session can't answer), and a full
gotcha table.

---

## Verify after any change

```bash
BACKEND=$(railway domain 2>&1 | grep -oE 'https://[^ ]+' | head -1)
FRONTEND=https://sonalika-demand-compass.vercel.app

for u in health meta kpis villages/summary shapes/india compete/summary chat/suggestions; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' -m 20 "$BACKEND/api/$u")
  [ "$code" = 200 ] || echo "FAIL $code $u"
done

curl -sS -o /dev/null -w "frontend %{http_code}\n" -m 20 "$FRONTEND/"
curl -sS "$FRONTEND/api/kpis" | python3 -c "import json,sys; print(json.load(sys.stdin)['demand'])"
```

`shapes/india` and `compete/summary` are the two that broke both times this was done —
they're the ones that actually exercise `geo_villages`, so they're the tell if an
upload nested silently. Don't consider a deploy verified without checking them
specifically, not just `/health`.
