#!/usr/bin/env bash
# run_random_walk.sh — start OSRM, walk every town in random order, stop OSRM.
# Same container lifecycle as run_oracle.sh. All args pass through to
# `python -m src.random_walk` (e.g. --seed 42 --limit 200).
#
#   cd /mnt/e/dev/optitrek && ./scripts/run_random_walk.sh --seed 42 --limit 200   # smoke
#   cd /mnt/e/dev/optitrek && ./scripts/run_random_walk.sh --seed 42               # full (~31k legs)
#
# Resumable: re-run the same command and it continues from legs.jsonl.
# Keep the whole thing in one WSL session (WSL idles out after 60s and takes
# the docker daemon with it).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV_PY="${OPTITREK_VENV_PY:-/root/venvs/optitrek-wsl/bin/python}"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"
OSRM_DIR="${REPO_ROOT}/data/osrm-major"
CONTAINER_NAME="optitrek-osrm-major"
OSRM_URL="http://127.0.0.1:5000"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
cleanup() { docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

log "Starting ${CONTAINER_NAME}"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run -d --name "${CONTAINER_NAME}" --rm \
    -p 127.0.0.1:5000:5000 \
    -v "${OSRM_DIR}:/data:ro" \
    "${OSRM_IMAGE}" \
    osrm-routed --algorithm mld --max-table-size 8000 /data/us-major.osrm >/dev/null

log "Waiting for OSRM (max 3 min)..."
for i in $(seq 1 36); do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
        "${OSRM_URL}/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo 000)
    if [ "${code}" = "200" ]; then
        log "OSRM ready at t+$((i*5))s"
        break
    fi
    if [ "${i}" = "36" ]; then log "TIMEOUT"; exit 1; fi
    sleep 5
done

log "Random walk: $*"
cd "${REPO_ROOT}"
mkdir -p logs
OSRM_URL="${OSRM_URL}" "${VENV_PY}" -m src.random_walk "$@" 2>&1 | tee -a "logs/random_walk_$(date +%Y%m%d_%H%M%S).log"

log "Done."
