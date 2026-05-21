#!/usr/bin/env bash
# run_tier1_local.sh — orchestrate the Tier 1 pipeline locally on BRONTOSAURUS.
#
# Starts osrm-routed in Docker, waits for ready, runs spot-check, matrix build,
# and solver+render in one session. Designed to be invoked from WSL Ubuntu so
# OSRM is reachable on localhost without Docker Desktop's broken proxy.
#
# Usage (from WSL Ubuntu):
#   /root/venvs/optitrek-wsl/bin/python is the expected venv
#   cd /mnt/e/dev/optitrek
#   ./scripts/run_tier1_local.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV_PY="${OPTITREK_VENV_PY:-/root/venvs/optitrek-wsl/bin/python}"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"
OSRM_DIR="${REPO_ROOT}/data/osrm-major"
OSRM_BASE="us-major"
CONTAINER_NAME="optitrek-osrm-major"
OSRM_URL="http://127.0.0.1:5000"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

# Cleanup: stop the container even if we exit via error.
cleanup() {
    log "Stopping ${CONTAINER_NAME} (cleanup)"
    docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- 1. Start OSRM ---
log "[1/4] Starting ${CONTAINER_NAME}"
# Remove stale container if any
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run -d --name "${CONTAINER_NAME}" --rm \
    -p 127.0.0.1:5000:5000 \
    -v "${OSRM_DIR}:/data:ro" \
    "${OSRM_IMAGE}" \
    osrm-routed --algorithm mld --max-table-size 8000 "/data/${OSRM_BASE}.osrm"

log "Waiting for OSRM (max 2 min)..."
for i in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
        "${OSRM_URL}/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo "000")
    if [ "${code}" = "200" ]; then
        log "OSRM ready at t+$((i*5))s"
        break
    fi
    if [ "${i}" = "24" ]; then
        log "OSRM never came up. Logs:"
        docker logs "${CONTAINER_NAME}" 2>&1 | tail -20
        exit 1
    fi
    sleep 5
done

# --- 2. Spot-check ---
log "[2/4] Three-route spot-check vs full-network ground truth"
"${VENV_PY}" - <<PYEOF
import requests
routes = [
    ("Yellowstone -> Yosemite",   "-110.588,44.428;-119.538,37.865", 907.7, 17.62),
    ("Grand Teton -> Arches",     "-110.682,43.790;-109.593,38.733", 513.5, 11.04),
    ("Zion -> Grand Canyon",      "-113.026,37.298;-112.140,36.054", 272.0,  6.15),
]
problems = []
for name, coords, exp_mi, exp_h in routes:
    r = requests.get(f"${OSRM_URL}/route/v1/driving/{coords}?overview=false", timeout=15)
    j = r.json()
    mi = j["routes"][0]["distance"] / 1609.34
    h  = j["routes"][0]["duration"] / 3600
    mi_pct = (mi / exp_mi - 1) * 100
    h_pct  = (h  / exp_h  - 1) * 100
    ok = abs(mi_pct) <= 5 and abs(h_pct) <= 10
    if not ok:
        problems.append(name)
    status = "OK" if ok else "FAIL"
    print(f"  {name:<26} {mi:7.1f} mi {h:5.2f} h  (dist {mi_pct:+5.1f}%, dur {h_pct:+5.1f}%)  [{status}]")
print(f"  Problems: {len(problems)}/3")
if problems:
    raise SystemExit(2)
PYEOF

# --- 3. Matrix builder ---
log "[3/4] Building distance matrix"
cd "${REPO_ROOT}"
OSRM_URL="${OSRM_URL}" "${VENV_PY}" -m src.matrix_builder

# --- 4. Solver + render ---
log "[4/4] Running Tier 1 solver + render"
OSRM_URL="${OSRM_URL}" "${VENV_PY}" -m src.run_tier1

log "All stages complete."
