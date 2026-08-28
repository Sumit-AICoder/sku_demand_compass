#!/usr/bin/env bash
# Start the Sonalika Demand Compass locally.
#
#   ./startup.sh            API on :8000, dashboard on :5273
#
# First run creates .venv, installs Python + npm deps and offers to build the data.
# Later runs skip straight to starting both servers. Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")"

# 8000 is not arbitrary: web/vite.config.ts proxies /api to 127.0.0.1:8000 and the
# Dockerfile serves on 8000. (The README's 8848 is stale -- it makes every call 502.)
API_PORT=8000
WEB_PORT=5273

if [ ! -d .venv ]; then
  echo "==> creating .venv"
  python3 -m venv .venv
fi

# Reinstall only when requirements.txt is newer than the last successful install.
if [ ! -f .venv/.deps-ok ] || [ requirements.txt -nt .venv/.deps-ok ]; then
  echo "==> installing Python dependencies (a minute or two the first time)"
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  touch .venv/.deps-ok
fi

if [ ! -d web/node_modules ]; then
  echo "==> npm install"
  (cd web && npm install)
fi

# The API is a thin DuckDB layer over parquet in data/marts. No marts, no numbers.
if [ ! -f data/marts/village_totals.parquet ]; then
  echo
  echo "!!  data/marts is empty. The dashboard will load but every panel will be blank."
  read -r -p "    Build it now with the full pipeline (~60s)? [y/N] " reply
  if [[ "$reply" =~ ^[Yy]$ ]]; then
    .venv/bin/python -m pipeline.run
  else
    echo "    Skipped. Run '.venv/bin/python -m pipeline.run' when you want the data."
  fi
fi

# ponytail: kill the whole process group rather than tracking PIDs. The script is its
# own group leader when launched from an interactive shell, so this takes both servers
# down and nothing else.
trap 'kill 0' EXIT

.venv/bin/python -m uvicorn api.main:app --port "$API_PORT" --reload &
(cd web && npm run dev -- --port "$WEB_PORT") &

cat <<BANNER

    dashboard   http://localhost:$WEB_PORT
    API docs    http://localhost:$API_PORT/docs
    Ctrl-C stops both.

BANNER

wait
