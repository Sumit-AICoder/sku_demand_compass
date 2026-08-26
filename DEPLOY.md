# Deploying the Sonalika Demand Compass

## Architecture

Two pieces, on two platforms, because they need different things:

| | Runs where | Why |
|---|---|---|
| **Frontend** (`web/`) | Vercel | Static React build — exactly what Vercel is built for. Free tier is plenty. |
| **Backend** (`api/`, `pipeline/`) | Railway | FastAPI + DuckDB + ~1.1GB of generated parquet data + a Python analytics stack (pandas, statsmodels, scikit-learn). **Does not fit in a Vercel serverless function** — those cap at 250MB unzipped. |

Vercel proxies `/api/*` to the Railway backend server-side (via `vercel.json` rewrites), so the browser only ever talks to your Vercel domain — no CORS to think about.

---

## Prerequisites

```bash
npm i -g vercel                    # or use npx vercel
npm i -g @railway/cli              # or use npx @railway/cli
vercel login
railway login                      # opens a browser; use `railway login --browserless` over SSH
```

You'll also want your Azure OpenAI (or Anthropic) credentials from `.env` handy — you'll paste them into Railway's dashboard, not into any file that gets deployed.

---

## Part 1 — Backend on Railway

### 1.1 Create the Dockerfile

The image bakes in **code** and the **small boundary geojson** (~37MB, a build input). It deliberately does **not** bake in `data/curated`/`data/marts` (~1.1GB) — those exceed Railway's upload limit as a build artifact and live on a volume instead, populated in a separate step below.

**Use `requirements-serve.txt`, not `requirements.txt`.** `api/*.py` never imports
`statsmodels`, `scikit-learn`, `kmodes` or `scipy` at runtime — those exist purely to
*generate* `data/curated`/`data/marts` (see `pipeline/ucm`, `pipeline/cluster`,
`pipeline/score`), not to serve it. Verified end to end against a real request —
health, KPIs, the map, competition, and chat (with a live LLM call) all pass on the
trimmed set. This alone drops the installed footprint from ~660MB to ~355MB (46%
smaller), with no functional difference in what the deployed app can do.

```dockerfile
FROM python:3.12-slim
WORKDIR /app

COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

COPY pipeline/ pipeline/
COPY api/ api/
# Seeded OUTSIDE the volume mount path (see 1.3) so the volume mount doesn't shadow it.
COPY data/raw/ /app/data_seed/raw/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

No `gcc`/`g++` needed either — those were only there for `statsmodels`/`scikit-learn`
wheels that sometimes compile from source; everything in `requirements-serve.txt` ships
prebuilt wheels for `python:3.12-slim`.

**If you ever need to regenerate the data** (a new pipeline run), do that on your own
machine with the full `requirements.txt` — the deploy image was never meant to run the
pipeline, only serve its output.

### 1.2 `.railwayignore` (repo root)

Keeps the CLI upload small — without this, `.venv` (~660MB) and `node_modules` (~90MB) get swept in and you'll hit Railway's 413 payload limit immediately.

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.git/
.env
.env.local
web/node_modules/
web/dist/
web/public/sku/
data/chat_sessions.json
data/curated/
data/marts/
*.log
```

**If you also have a root `.gitignore`** that excludes `data/curated`/`data/marts` (correct for git, since they're huge generated artifacts) — check whether your Railway CLI version honours `.gitignore` in addition to `.railwayignore` for the upload archive. If it does, temporarily rename `.gitignore` out of the way before `railway up` (it doesn't affect the build either way, since `data/raw/` is what actually needs to be present) and rename it back immediately after.

### 1.3 Deploy the code

```bash
railway init --name sonalika-demand-compass
railway up --detach
```

This should upload in seconds (~40MB). If it hangs or errors with a `413`, something huge is leaking into the upload — check `.railwayignore` first.

### 1.4 Add a volume — **set the size explicitly in the dashboard**

The CLI's `volume add` creates a **500MB volume by default with no size flag to override it**, and Railway allows only **one volume per service**. `data/curated` (335MB) + `data/raw` (37MB) + `data/marts` (803MB) ≈ 1.2GB total, so 500MB isn't enough.

**Use the dashboard for this step**, not the CLI: open your project → the service → **Volumes** → add a volume, set:
- **Mount path:** `/app/data`
- **Size:** at least **2GB** (headroom for growth)

Mounting at `/app/data` covers `curated/`, `marts/` and `raw/` all at once, which is why the Dockerfile seeds the raw geojson to `/app/data_seed/raw/` instead — a volume mount replaces whatever the image had at its mount path, so anything baked in directly at `/app/data/raw` would vanish the moment the volume attaches.

### 1.5 Environment variables

Dashboard → your service → **Variables** → add:

```
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
AZURE_OPENAI_DEPLOYMENT       (or DEPLOYMENT / MODEL_NAME — the app accepts any of these names)
AZURE_OPENAI_API_VERSION      (or API_VERSION)
```

Or the Anthropic equivalent (`ANTHROPIC_API_KEY`) if you're using Claude instead. Paste these directly in the dashboard — never commit them, and don't put them in a file the Dockerfile copies.

### 1.6 Get a public domain

Dashboard → service → **Settings** → **Networking** → **Generate Domain**, or:

```bash
railway domain
```

Note the URL — you'll need it for the frontend's proxy config in Part 3.

---

## Part 2 — Populate the data

Two ways to get `data/curated` and `data/marts` onto the volume. **Use Option A.**

### Option A (recommended): generate locally, upload the result

The full pipeline needs more than the ~1GB of RAM Railway's default instance gives you — the heaviest stage builds a 3.9M-row table and will get OOM-killed if run remotely on a small instance. Generating it on your own machine sidesteps that entirely and only takes about a minute:

```bash
python -m pipeline.run          # ~65s locally
```

Then upload the two output directories to the volume:

```bash
# CRITICAL: the destination must NOT already exist, or the upload nests itself
# inside the existing directory (e.g. /app/data/curated/curated/*.parquet) instead
# of landing at /app/data/curated/*.parquet. Always clean first.
railway ssh -- rm -rf /app/data/curated /app/data/marts

railway volume files --volume <your-volume-name> upload data/curated /app/data/curated --overwrite
railway volume files --volume <your-volume-name> upload data/marts   /app/data/marts   --overwrite
```

(`railway volume list --json` will give you the exact volume name if you don't have it handy.) The `marts` upload is ~800MB — expect it to take a few minutes.

**Verify it landed flat, not nested:**

```bash
railway ssh -- find /app/data/curated -maxdepth 1
railway ssh -- find /app/data/marts -maxdepth 1
```

You should see the `.parquet` files directly under each path — no repeated `curated/curated/` or `marts/marts/`.

### Option B: run the pipeline on Railway itself

Only do this if you've bumped the service to a plan with **at least 2GB RAM**. Then:

```bash
railway ssh -- sh -c "mkdir -p /app/data/raw && cp -r /app/data_seed/raw/* /app/data/raw/"
railway ssh -- python -m pipeline.run
```

Run this as a single foregrounded command you wait on directly — if you background it and the terminal session that launched it closes, the remote process gets killed mid-run.

### 2.1 Restart the service after populating data

The API caches its DuckDB connection on first use. If the service started before the data existed, it may have already cached an empty view set. Restart it from the dashboard (**Deployments** → **⋮** → **Restart**) so it picks up the now-populated volume fresh.

### 2.2 Verify

```bash
curl https://<your-railway-domain>/api/health
curl https://<your-railway-domain>/api/kpis
```

`/api/kpis` should return real numbers, not an error.

---

## Part 3 — Frontend on Vercel

### 3.1 `web/vercel.json`

```json
{
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://<your-railway-domain>/api/:path*"
    }
  ]
}
```

Replace `<your-railway-domain>` with the actual domain from step 1.6.

### 3.2 Deploy

```bash
cd web
vercel --prod
```

Vercel auto-detects the Vite project (build command `vite build`, output `dist`) — no other config needed. Product images under `web/public/sku/` are bundled automatically as static assets.

### 3.3 Verify

Open the Vercel URL, check the Summary tab loads real numbers, and try the chat — it should hit `/api/chat`, which Vercel proxies through to Railway.

---

## Known gotchas (all hit and fixed during the first attempt)

| Symptom | Cause | Fix |
|---|---|---|
| `413 Payload Too Large` on `railway up` | `.venv`/`node_modules`/generated data swept into the upload | Add `.railwayignore` (§1.2); never bake `data/curated`/`data/marts` into the image |
| `railway volume add` refuses: "A volume is already mounted" | Only one volume per service | Use one volume mounted at `/app/data`, not separate volumes per subfolder |
| Uploaded files end up at `/app/data/curated/curated/*` | The upload nests inside an already-existing destination directory | `railway ssh -- rm -rf` the destination immediately before every upload |
| Remote `python -m pipeline.run` dies silently, no traceback | Default Railway instance (~1GB RAM) OOM-kills the heaviest stage | Generate locally and upload the result (§2, Option A), or upgrade the plan |
| A backgrounded remote command stops partway through with no error | The SSH session was killed when its parent shell/terminal exited | Run it in the foreground and wait, don't background-and-disconnect |
| `railway login --browserless` never completes | The device code expired (~10–15 min window) | Start a fresh `railway login --browserless` and approve the new code promptly |
| A deleted volume still shows `isPendingDeletion: true` and blocks a new one | Railway soft-deletes with a ~2-day grace period; the CLI can't cancel it | `railway volume update --mount-path ...` retargets the *same* volume instead of waiting it out |

---

## Cost

Both platforms have free tiers, but a persistent backend with ~1.2GB of data and non-trivial CPU (DuckDB queries, statsmodels fits) will likely need Railway's paid tier once you're past initial testing — check current pricing before leaving this running long-term.
