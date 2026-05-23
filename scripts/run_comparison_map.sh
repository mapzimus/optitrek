#!/usr/bin/env bash
# run_comparison_map.sh — orchestrate the dual-engine comparison render.
#
# Starts both OSRM engines (US :5000, NA :5001), waits for both ready,
# activates the venv, then runs render_comparison_map.py against the
# given trip YAML (default tier1_replica). Leaves the engines running on
# exit so subsequent runs are fast — explicitly stop them with:
#   docker stop optitrek-osrm-major optitrek-osrm-na
#
# Usage (from WSL Ubuntu):
#   cd /mnt/e/dev/optitrek
#   ./scripts/run_comparison_map.sh                          # tier1_replica
#   ./scripts/run_comparison_map.sh trips/all_national_parks.yaml

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
TRIP_YAML="${1:-trips/tier1_replica.yaml}"
# Capture any remaining args (e.g. --time-limit-override 1200) and pass them
# through to render_comparison_map.py. Useful when the NA matrix needs more
# solver budget than the Tier 1 oracle's 300s to converge to ≤ US-only cost.
shift || true
EXTRA_ARGS=("$@")
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

ensure_engine() {
    local name="$1" port="$2" data_dir="$3" osrm_file="$4"
    if docker ps --format '{{.Names}}' | grep -qx "$name"; then
        log "$name already running on :$port"
        return 0
    fi
    log "Starting $name on :$port (data: $data_dir, osrm: $osrm_file)"
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --rm \
        -p "127.0.0.1:${port}:5000" \
        -v "${REPO_ROOT}/${data_dir}:/data:ro" \
        "${OSRM_IMAGE}" \
        osrm-routed --algorithm mld --max-table-size 8000 "/data/${osrm_file}" >/dev/null
}

wait_for_engines() {
    log "Waiting for both engines to be ready (max 4 min)..."
    for i in $(seq 1 48); do
        local us_code na_code
        us_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
            "http://127.0.0.1:5000/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo 000)
        na_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
            "http://127.0.0.1:5001/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo 000)
        if [ "$us_code" = "200" ] && [ "$na_code" = "200" ]; then
            log "Both ready at t+$((i*5))s"
            return 0
        fi
        if [ "$i" = "48" ]; then log "TIMEOUT (us=$us_code na=$na_code)"; exit 1; fi
        sleep 5
    done
}

ensure_engine "optitrek-osrm-major" 5000 "data/osrm-major"    "us-major.osrm"
ensure_engine "optitrek-osrm-na"    5001 "data/osrm-major-na" "north-america-major.osrm"
wait_for_engines

log "Activating venv and running comparison renderer for: $TRIP_YAML"
cd "$REPO_ROOT"
# WSL venv lives outside the repo to avoid Windows/WSL filesystem conflicts
# (the .venv path under /mnt/e/ would otherwise get tangled with the
# Windows-side venv used by pytest on the host). See HANDOVER.md task 7b.
VENV_PATH="${OPTITREK_VENV:-/root/venvs/optitrek-wsl}"
# shellcheck disable=SC1091
source "${VENV_PATH}/bin/activate"

# Point matrix_builder/visualize at the right defaults via env (the script
# uses _osrm_url_for_network which already respects these vars).
export OSRM_URL="http://127.0.0.1:5000"
export OSRM_URL_NA="http://127.0.0.1:5001"

# Force unbuffered stdout so progress prints stream live to the log file
# (without -u, Python buffers until the process exits, hiding what's happening).
python -u scripts/render_comparison_map.py "$TRIP_YAML" "${EXTRA_ARGS[@]}"

log "Done. Engines left running for fast re-runs."
log "Stop them with: docker stop optitrek-osrm-major optitrek-osrm-na"
