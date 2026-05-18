#!/usr/bin/env bash
# build_osrm.sh — one-shot bring-up of OSRM for Optitrek Tier 1.
#
# Downloads the US-only OSM extract from Geofabrik (per DECISIONS.md D3 —
# prevents the solver from routing through Canada) and runs the three-stage
# osrm-backend pipeline against it. Result: a ready-to-serve us-latest.osrm
# under data/osrm/, plus a docker-compose service that exposes OSRM on :5000.
#
# Requirements: docker, ~25GB free disk, ~30-60min on first run (extract +
# partition + customize is CPU-bound; partition is the slow step).
#
# Usage:
#   cd "$(git rev-parse --show-toplevel)"
#   ./scripts/build_osrm.sh
#   docker compose up -d osrm
#   curl http://localhost:5000/route/v1/driving/-77.0365,38.8977;-71.0589,42.3601
#
# Idempotent — checks for each artifact before recomputing.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
OSRM_DIR="${REPO_ROOT}/data/osrm"
PBF_URL="https://download.geofabrik.de/north-america/us-latest.osm.pbf"
PBF_FILE="${OSRM_DIR}/us-latest.osm.pbf"
OSRM_FILE="${OSRM_DIR}/us-latest.osrm"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"

mkdir -p "${OSRM_DIR}"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

# --- 1. Download the US OSM extract ---
if [[ -f "${PBF_FILE}" ]]; then
    log "PBF already present: ${PBF_FILE} ($(du -h "${PBF_FILE}" | cut -f1))"
else
    log "Downloading ${PBF_URL} (this is ~10 GB — go get coffee)"
    # -C - resumes a partial download if the script was interrupted.
    curl -L -C - -o "${PBF_FILE}" "${PBF_URL}"
fi

# --- 2. osrm-extract ---
if [[ -f "${OSRM_FILE}" ]]; then
    log "Extract artifact already present: ${OSRM_FILE}"
else
    log "Running osrm-extract (uses car.lua profile)"
    docker run --rm -t \
        -v "${OSRM_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-extract -p /opt/car.lua /data/us-latest.osm.pbf
fi

# --- 3. osrm-partition ---
# Partition produces several files; check for .osrm.partition as the sentinel.
if [[ -f "${OSRM_FILE}.partition" ]]; then
    log "Partition artifact already present"
else
    log "Running osrm-partition (this is the slow step — ~30 min)"
    docker run --rm -t \
        -v "${OSRM_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-partition /data/us-latest.osrm
fi

# --- 4. osrm-customize ---
if [[ -f "${OSRM_FILE}.cells" ]]; then
    log "Customize artifact already present"
else
    log "Running osrm-customize"
    docker run --rm -t \
        -v "${OSRM_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-customize /data/us-latest.osrm
fi

log "OSRM artifacts ready in ${OSRM_DIR}"
log "Start the server with:  docker compose up -d osrm"
log "Smoke test:             curl 'http://localhost:5000/route/v1/driving/-77.0365,38.8977;-71.0589,42.3601'"
