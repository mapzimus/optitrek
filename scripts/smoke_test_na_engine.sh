#!/usr/bin/env bash
# smoke_test_na_engine.sh — verify the NA OSRM artifact set actually routes
# through Canada for the Detroit→Buffalo case. Starts BOTH engines side-by-
# side (US on :5000, NA on :5001), probes the same leg against each, and
# reports the delta.
#
# Usage (from WSL Ubuntu):
#   cd /mnt/e/dev/optitrek
#   ./scripts/smoke_test_na_engine.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
cleanup() {
    docker stop optitrek-osrm-major >/dev/null 2>&1 || true
    docker stop optitrek-osrm-na >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "Starting US OSRM on :5000"
docker rm -f optitrek-osrm-major >/dev/null 2>&1 || true
docker run -d --name optitrek-osrm-major --rm \
    -p 127.0.0.1:5000:5000 \
    -v "${REPO_ROOT}/data/osrm-major:/data:ro" \
    "${OSRM_IMAGE}" \
    osrm-routed --algorithm mld --max-table-size 8000 /data/us-major.osrm >/dev/null

log "Starting NA OSRM on :5001"
docker rm -f optitrek-osrm-na >/dev/null 2>&1 || true
docker run -d --name optitrek-osrm-na --rm \
    -p 127.0.0.1:5001:5000 \
    -v "${REPO_ROOT}/data/osrm-major-na:/data:ro" \
    "${OSRM_IMAGE}" \
    osrm-routed --algorithm mld --max-table-size 8000 /data/north-america-major.osrm >/dev/null

log "Waiting for both engines to be ready (max 4 min)..."
for i in $(seq 1 48); do
    us_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
        "http://127.0.0.1:5000/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo 000)
    na_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
        "http://127.0.0.1:5001/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo 000)
    if [ "${us_code}" = "200" ] && [ "${na_code}" = "200" ]; then
        log "Both ready at t+$((i*5))s"
        break
    fi
    if [ "${i}" = "48" ]; then log "TIMEOUT (us=${us_code} na=${na_code})"; exit 1; fi
    sleep 5
done

probe() {
    local label="$1"
    local coords="$2"
    local us_resp na_resp us_mi us_h na_mi na_h delta_mi delta_h
    us_resp=$(curl -s "http://127.0.0.1:5000/route/v1/driving/${coords}?overview=false")
    na_resp=$(curl -s "http://127.0.0.1:5001/route/v1/driving/${coords}?overview=false")
    local us_stats na_stats
    us_stats=$(python3 <<PYEOF
import json
r = json.loads('''${us_resp}''')
print(f"{r['routes'][0]['distance']/1609.344:.0f} {r['routes'][0]['duration']/3600:.2f}")
PYEOF
)
    na_stats=$(python3 <<PYEOF
import json
r = json.loads('''${na_resp}''')
print(f"{r['routes'][0]['distance']/1609.344:.0f} {r['routes'][0]['duration']/3600:.2f}")
PYEOF
)
    us_mi=$(echo "$us_stats" | awk '{print $1}')
    us_h=$(echo "$us_stats" | awk '{print $2}')
    na_mi=$(echo "$na_stats" | awk '{print $1}')
    na_h=$(echo "$na_stats" | awk '{print $2}')
    printf '\n  %-32s\n' "${label}"
    printf '    US-only :  %5s mi / %5s h\n' "$us_mi" "$us_h"
    printf '    US+CA   :  %5s mi / %5s h\n' "$na_mi" "$na_h"
    printf '    delta   :  %5s mi / %5s h saved by cross-border\n' \
        "$(python3 -c "print(f'{${us_mi}-${na_mi}:+.0f}')")" \
        "$(python3 -c "print(f'{${us_h}-${na_h}:+.2f}')")"
}

echo
log "=== Cross-border comparison ==="
probe "Detroit -> Buffalo"           "-83.0458,42.3314;-78.8784,42.8864"
probe "Niagara Falls -> Sault Ste M" "-79.0377,43.0962;-84.3475,46.4953"
probe "Acadia NP -> Campobello Is."  "-68.2733,44.3500;-66.9700,44.8761"
probe "Seattle WA -> Glacier NP MT"  "-122.3321,47.6062;-113.7186,48.7596"

log "Done."
