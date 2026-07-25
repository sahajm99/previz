#!/usr/bin/env bash
# Start Magic Hour on localhost. Run from anywhere in the repo.
#
#   ./scripts/dev.sh            http://127.0.0.1:8080
#   PORT=8090 ./scripts/dev.sh
#
# Creates the venv and installs requirements on first run, then reloads on save.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
PORT="${PORT:-8080}"
PROJECT="nyu-ai-builder26nyc-9338"

cd "$BACKEND"

if [ ! -d .venv ]; then
  echo "· creating .venv"
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
fi

# Windows Git Bash puts the interpreter in Scripts/, everything else in bin/.
if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi

echo "· installing requirements"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt

# The single most expensive gotcha on this project. Without a quota project, ADC
# bills against a starvation-tier bucket and every Vertex call returns 429
# RESOURCE_EXHAUSTED, which looks exactly like a broken app.
if command -v gcloud >/dev/null 2>&1; then
  CURRENT="$(gcloud config get-value project 2>/dev/null || true)"
  if [ "$CURRENT" != "$PROJECT" ]; then
    echo "· pointing gcloud at $PROJECT"
    gcloud config set project "$PROJECT" >/dev/null 2>&1 || true
  fi
  if [ ! -f "${APPDATA:-$HOME/.config}/gcloud/application_default_credentials.json" ] \
     && [ ! -f "$HOME/.config/gcloud/application_default_credentials.json" ]; then
    echo
    echo "  No application default credentials found. Model calls will fail."
    echo "  Run these once, then restart:"
    echo "      gcloud auth application-default login"
    echo "      gcloud auth application-default set-quota-project $PROJECT"
    echo
  fi
fi

echo
echo "  Magic Hour  ·  http://127.0.0.1:$PORT"
echo "  API docs    ·  http://127.0.0.1:$PORT/docs"
echo "  Health      ·  http://127.0.0.1:$PORT/healthz"
echo
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload
