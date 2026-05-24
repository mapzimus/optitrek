#!/bin/bash
# One-shot: start OSRM major-roads engine, wait for ready, fetch tour
# polylines, stop OSRM. Designed to be run inside WSL Ubuntu from PowerShell:
#
#     MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash /mnt/e/dev/optitrek/scripts/fetch_polylines_wsl.sh
#
# Bundling start→poll→fetch→stop into ONE wsl invocation keeps the WSL2
# vmmem alive throughout (per CLAUDE.md "Known environment quirks" §3).
set -e

cd /mnt/e/dev/optitrek

echo ">> Removing any old container..."
docker rm -f optitrek-osrm-major >/dev/null 2>&1 || true

echo ">> Starting OSRM major-roads engine..."
docker run -d --name optitrek-osrm-major --rm \
  -p 127.0.0.1:5000:5000 \
  -v /mnt/e/dev/optitrek/data/osrm-major:/data:ro \
  ghcr.io/project-osrm/osrm-backend:latest \
  osrm-routed --algorithm mld --max-table-size 8000 /data/us-major.osrm >/dev/null

echo ">> Waiting for OSRM (max 90s)..."
ready=0
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 \
    "http://127.0.0.1:5000/route/v1/driving/-77.036,38.897;-71.058,42.360" 2>/dev/null || echo "000")
  if [ "$code" = "200" ]; then
    echo "   READY after $((i*3))s"
    ready=1
    break
  fi
  sleep 3
done
if [ $ready -eq 0 ]; then
  echo "!! OSRM never became ready"
  docker logs --tail 30 optitrek-osrm-major 2>&1 || true
  docker stop optitrek-osrm-major >/dev/null 2>&1 || true
  exit 1
fi

echo ">> Fetching tour polylines..."
/root/venvs/optitrek-wsl/bin/python -m scripts.fetch_tour_polylines

echo ">> Stopping OSRM..."
docker stop optitrek-osrm-major >/dev/null

echo ">> Done."
