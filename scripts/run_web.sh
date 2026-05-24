#!/usr/bin/env bash
# run_web.sh — launch the FastAPI web frontend locally.
#
# Prereqs (verified by run_trip's engine validator, but worth knowing):
#   - Postgres reachable via DATABASE_URL in .env (Neon is the default)
#   - The US-only OSRM container running on :5000 if you'll submit any
#     trip (the app itself starts without it; only Solve needs it)
#   - For routing_network='us_canada' trips: also the NA engine on :5001
#
# Usage (from WSL Ubuntu):
#   cd /mnt/e/dev/optitrek
#   ./scripts/run_web.sh
#
# Then open http://localhost:8000/ in a browser.
#
# --reload restarts the app on every src/ change. Drop it for prod-like
# runs or when you don't want uvicorn watching the filesystem.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV_PATH="${OPTITREK_VENV:-/root/venvs/optitrek-wsl}"
HOST="${OPTITREK_WEB_HOST:-0.0.0.0}"
PORT="${OPTITREK_WEB_PORT:-8000}"

cd "${REPO_ROOT}"

# Make sure the venv has the web deps. Cheap idempotent check.
if ! "${VENV_PATH}/bin/python" -c "import fastapi, uvicorn, jinja2" 2>/dev/null; then
    echo "Installing web dependencies into ${VENV_PATH}..."
    "${VENV_PATH}/bin/pip" install -q \
        'fastapi>=0.115' 'uvicorn[standard]>=0.30' \
        'jinja2>=3.1' 'python-multipart>=0.0.9'
fi

echo "Starting Optitrek web app on http://${HOST}:${PORT}"
echo "(Press Ctrl-C to stop.)"
exec "${VENV_PATH}/bin/uvicorn" src.web.main:app \
    --host "${HOST}" \
    --port "${PORT}" \
    --reload \
    --reload-dir src
