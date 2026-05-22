#!/usr/bin/env bash
# render_overlays.sh — start OSRM (filtered network) and render overlay comparison maps.
#
# Bundles the steps so WSL doesn't idle-shutdown the container between calls.
#
# Usage (from WSL Ubuntu):
#   cd /mnt/e/dev/optitrek
#   ./scripts/render_overlays.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV_PY="${OPTITREK_VENV_PY:-/root/venvs/optitrek-wsl/bin/python}"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"
OSRM_DIR="${REPO_ROOT}/data/osrm-major"
CONTAINER_NAME="optitrek-osrm-major"
OSRM_URL="http://127.0.0.1:5000"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

cleanup() {
    log "Stopping ${CONTAINER_NAME} (cleanup)"
    docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "Starting ${CONTAINER_NAME}"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run -d --name "${CONTAINER_NAME}" --rm \
    -p 127.0.0.1:5000:5000 \
    -v "${OSRM_DIR}:/data:ro" \
    "${OSRM_IMAGE}" \
    osrm-routed --algorithm mld --max-table-size 8000 /data/us-major.osrm >/dev/null

log "Waiting for OSRM (max 4 min)..."
for i in $(seq 1 48); do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
        "${OSRM_URL}/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo 000)
    if [ "${code}" = "200" ]; then
        log "OSRM ready at t+$((i*5))s"
        break
    fi
    if [ "${i}" = "48" ]; then
        log "TIMEOUT. Logs:"
        docker logs "${CONTAINER_NAME}" 2>&1 | tail -20
        exit 1
    fi
    sleep 5
done

log "Running compare_overlays.py (with OSRM road geometry)"
cd "${REPO_ROOT}"
OSRM_URL="${OSRM_URL}" "${VENV_PY}" -m scripts.compare_overlays

log "Done."
