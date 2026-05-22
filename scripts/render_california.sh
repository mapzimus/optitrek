#!/usr/bin/env bash
# render_california.sh — start OSRM, re-render the California-double map with
# road geometry, stop OSRM. Used to refresh the gallery copy when the original
# render was done with use_road_geometry=False (because OSRM wasn't running).

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

log "Running california_control.py (with OSRM road geometry, 300s budget)"
cd "${REPO_ROOT}"
OSRM_URL="${OSRM_URL}" OPTITREK_TIME_LIMIT=300 "${VENV_PY}" -m scripts.california_control

log "Done."
