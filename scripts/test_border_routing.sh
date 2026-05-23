#!/usr/bin/env bash
# test_border_routing.sh — probe three classic "border shortcut" cases against
# the US-only OSRM artifacts to show what Decision D3 actually costs.
#
# Detroit -> Buffalo: real-world drivers cut through Ontario; we go around Lake Erie.
# Acadia -> Campobello: real-world dips into New Brunswick; we hit the border bridge.
# El Paso -> San Diego: usually no real Mexico benefit, but tests southern border.
#
# Usage (from WSL Ubuntu):
#   cd /mnt/e/dev/optitrek
#   ./scripts/test_border_routing.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
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

log "Waiting for OSRM"
for i in $(seq 1 36); do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
        "${OSRM_URL}/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo 000)
    if [ "${code}" = "200" ]; then log "Ready at t+$((i*5))s"; break; fi
    if [ "${i}" = "36" ]; then log "TIMEOUT"; exit 1; fi
    sleep 5
done

probe() {
    local label="$1"
    local coords="$2"
    local expected="$3"
    local resp
    resp=$(curl -s "${OSRM_URL}/route/v1/driving/${coords}?overview=false")
    # Use a heredoc to feed JSON to Python so the inner quotes don't need escaping.
    local stats
    stats=$(python3 <<PYEOF
import json
r = json.loads('''${resp}''')
miles = r['routes'][0]['distance'] / 1609.344
hours = r['routes'][0]['duration'] / 3600
print(f'{miles:.0f} {hours:.1f}')
PYEOF
)
    local mi h
    mi=$(echo "$stats" | awk '{print $1}')
    h=$(echo "$stats" | awk '{print $2}')
    printf '  %-45s %5s mi / %5s h   (real-world: %s)\n' "$label" "$mi" "$h" "$expected"
}

echo
log "Border-shortcut probe (US-only OSRM)"
echo
probe "Detroit -> Buffalo"          "-83.0458,42.3314;-78.8784,42.8864"  "~280 mi via Ontario"
probe "Acadia NP -> Campobello Is." "-68.2733,44.3500;-66.9700,44.8761"  "~150 mi via NB shortcut"
probe "El Paso -> San Diego"        "-106.4850,31.7619;-117.1611,32.7157" "~720 mi via I-10/I-8 (no Mexico benefit)"
probe "Seattle WA -> Glacier NP MT" "-122.3321,47.6062;-113.7186,48.7596" "~550 mi via I-90 (no Canada benefit)"

log "Done."
