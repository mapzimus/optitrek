#!/bin/bash
# Bundle: start OSRM, wait, fetch the 56 diff polylines, stop OSRM.
# Kept in one wsl invocation so vmmem stays alive throughout (see CLAUDE.md).
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

echo ">> Waiting for OSRM (max 120s)..."
ready=0
for i in $(seq 1 40); do
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

echo ">> Fetching 56 diff polylines..."
/root/venvs/optitrek-wsl/bin/python -m scripts.fetch_diff_polylines

echo ">> Stopping OSRM..."
docker stop optitrek-osrm-major >/dev/null
echo ">> Done."
