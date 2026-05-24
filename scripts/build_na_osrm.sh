#!/usr/bin/env bash
# build_na_osrm.sh — pull Canada, merge with US-major, build a combined
# OSRM artifact set at data/osrm-major-na/ for cross-border routing.
#
# Total wall clock: ~45-60 min on a typical broadband connection.
# Disk usage:
#   data/canada-latest.osm.pbf          ~5 GB    (raw Geofabrik download)
#   data/osrm-major-na/canada-major.osm.pbf  ~400-500 MB (filtered to major roads)
#   data/osrm-major-na/us-major.osm.pbf      (copied from data/osrm-major/)
#   data/osrm-major-na/north-america-major.osm.pbf  ~1 GB (merged PBF)
#   data/osrm-major-na/north-america-major.osrm.*   ~9 GB (OSRM artifacts)
#
# Idempotent — each stage checks for its sentinel file before running.
#
# Usage (from WSL Ubuntu):
#   cd /mnt/e/dev/optitrek
#   ./scripts/build_na_osrm.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
DATA_DIR="${REPO_ROOT}/data"
NA_DIR="${DATA_DIR}/osrm-major-na"
US_PBF="${DATA_DIR}/osrm-major/us-major.osm.pbf"
CA_PBF_RAW="${DATA_DIR}/canada-latest.osm.pbf"
CA_PBF_MAJOR="${NA_DIR}/canada-major.osm.pbf"
NA_PBF_MERGED="${NA_DIR}/north-america-major.osm.pbf"
NA_OSRM="${NA_DIR}/north-america-major.osrm"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"
OSMIUM_IMAGE="iboates/osmium:latest"
PBF_URL="https://download.geofabrik.de/north-america/canada-latest.osm.pbf"

# Threads for osrm-extract/partition/customize — same default as build_osrm.sh
THREADS="${OSRM_THREADS:-6}"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

mkdir -p "${NA_DIR}"

# ---- 1. Verify US-major source PBF exists ----
if [[ ! -f "${US_PBF}" ]]; then
    log "ERROR: ${US_PBF} not found. Run filter_pbf.sh first to produce the US major-roads PBF."
    exit 1
fi
log "US-major source ready: $(du -h "${US_PBF}" | cut -f1)"

# ---- 2. Download Canada PBF ----
# F10 fix: validate that the on-disk PBF is plausibly the right thing.
# Without this, a corrupt or HTML-error-page download would silently pass
# the file-existence check and then fail 5+ minutes later inside
# osrm-extract with a cryptic error. Canada PBF is ~3-5 GB; anything
# under 500 MB is almost certainly a download failure.
CA_PBF_MIN_BYTES=$((500 * 1024 * 1024))  # 500 MB
_validate_pbf_size() {
    local path="$1"
    local actual_bytes
    actual_bytes=$(stat -c %s "$path" 2>/dev/null || stat -f %z "$path")
    if [[ "$actual_bytes" -lt "${CA_PBF_MIN_BYTES}" ]]; then
        log "ERROR: ${path} is only $(du -h "$path" | cut -f1) — expected >= 500 MB."
        log "  Likely a partial or failed download. Removing and re-trying:"
        log "    rm '${path}' && rerun this script"
        log "  Or check the URL is still valid: ${PBF_URL}"
        exit 1
    fi
}

if [[ -f "${CA_PBF_RAW}" ]]; then
    log "Canada raw PBF already present: $(du -h "${CA_PBF_RAW}" | cut -f1)"
    _validate_pbf_size "${CA_PBF_RAW}"
else
    log "Downloading ${PBF_URL} (~5 GB; takes ~5-15 min depending on connection)"
    curl -L -C - -o "${CA_PBF_RAW}" "${PBF_URL}"
    _validate_pbf_size "${CA_PBF_RAW}"
fi

# ---- 3. Filter Canada to major roads ----
if [[ -f "${CA_PBF_MAJOR}" ]]; then
    log "Canada major-roads PBF already present: $(du -h "${CA_PBF_MAJOR}" | cut -f1)"
else
    log "Filtering Canada PBF to major roads (~3-5 min)"
    docker run --rm \
        -v "${DATA_DIR}:/in:ro" \
        -v "${NA_DIR}:/out" \
        "${OSMIUM_IMAGE}" \
        tags-filter \
        --overwrite \
        -o "/out/canada-major.osm.pbf" \
        "/in/canada-latest.osm.pbf" \
        w/highway=motorway,trunk,primary,secondary,tertiary,motorway_link,trunk_link,primary_link,secondary_link,tertiary_link
    log "Canada major-roads PBF written: $(du -h "${CA_PBF_MAJOR}" | cut -f1)"
fi

# ---- 4. Merge with US-major PBF ----
if [[ -f "${NA_PBF_MERGED}" ]]; then
    log "Merged NA PBF already present: $(du -h "${NA_PBF_MERGED}" | cut -f1)"
else
    log "Merging US + Canada major-roads PBFs"
    # Copy US-major into the NA dir so osmium can see both files in /work
    cp "${US_PBF}" "${NA_DIR}/us-major.osm.pbf"
    docker run --rm \
        -v "${NA_DIR}:/work" \
        "${OSMIUM_IMAGE}" \
        merge \
        --overwrite \
        -o "/work/north-america-major.osm.pbf" \
        "/work/us-major.osm.pbf" \
        "/work/canada-major.osm.pbf"
    log "Merged NA PBF written: $(du -h "${NA_PBF_MERGED}" | cut -f1)"
fi

# ---- 5. osrm-extract ----
if [[ -f "${NA_OSRM}" ]]; then
    log "osrm-extract artifact already present"
else
    log "Running osrm-extract on merged PBF (~5-10 min)"
    docker run --rm -t \
        -v "${NA_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-extract -p /opt/car.lua --threads "${THREADS}" /data/north-america-major.osm.pbf
fi

# ---- 6. osrm-partition ----
if [[ -f "${NA_OSRM}.partition" ]]; then
    log "osrm-partition artifact already present"
else
    log "Running osrm-partition (~5-10 min)"
    docker run --rm -t \
        -v "${NA_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-partition --threads "${THREADS}" /data/north-america-major.osrm
fi

# ---- 7. osrm-customize ----
if [[ -f "${NA_OSRM}.cell_metrics" ]]; then
    log "osrm-customize artifact already present"
else
    log "Running osrm-customize (~1-2 min)"
    docker run --rm -t \
        -v "${NA_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-customize --threads "${THREADS}" /data/north-america-major.osrm
fi

log "NA OSRM artifact set ready at ${NA_DIR}"
log "Total size: $(du -sh "${NA_DIR}" | cut -f1)"
log ""
log "Start the NA server with:"
log "  docker run -d --name optitrek-osrm-na --rm \\"
log "    -p 127.0.0.1:5001:5000 \\"
log "    -v ${NA_DIR}:/data:ro \\"
log "    ${OSRM_IMAGE} \\"
log "    osrm-routed --algorithm mld --max-table-size 8000 /data/north-america-major.osrm"
log ""
log "(Port 5001 instead of 5000 so it can coexist with the US-only engine.)"
