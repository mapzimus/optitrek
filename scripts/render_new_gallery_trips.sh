#!/usr/bin/env bash
# render_new_gallery_trips.sh — batch-render one or more gallery trips in a
# single OSRM session. Mirrors run_oracle.sh's lifecycle pattern (single
# docker start, trap-on-exit stop) but iterates a list of YAMLs.
#
# Usage (from WSL Ubuntu, cd /mnt/e/dev/optitrek):
#   # Run default batch (the four maps 10-13):
#   ./scripts/render_new_gallery_trips.sh
#
#   # Run a custom batch by passing YAML paths:
#   ./scripts/render_new_gallery_trips.sh trips/all_national_seashores.yaml trips/boston_500mi_radius.yaml
#
# Trips are run sequentially; failure on one does not stop the rest.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV_PY="${OPTITREK_VENV_PY:-/root/venvs/optitrek-wsl/bin/python}"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"
OSRM_DIR="${REPO_ROOT}/data/osrm-major"
CONTAINER_NAME="optitrek-osrm-major"
OSRM_URL="http://127.0.0.1:5000"

# Default batch (the original four maps 10-13). Override by passing YAML
# paths as command-line args.
DEFAULT_TRIPS=(
    "trips/all_national_parks.yaml"
    "trips/civil_war_battlefields.yaml"
    "trips/pacific_northwest_parks.yaml"
    "trips/east_to_west_open_path.yaml"
)
if [ $# -gt 0 ]; then
    TRIPS=("$@")
else
    TRIPS=("${DEFAULT_TRIPS[@]}")
fi

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

cd "${REPO_ROOT}"
for yaml in "${TRIPS[@]}"; do
    log "=== Running ${yaml} ==="
    OSRM_URL="${OSRM_URL}" "${VENV_PY}" -m scripts.run_trip "${yaml}" || {
        log "FAILED on ${yaml} — continuing to next trip"
        continue
    }
done

log "All trips processed."
