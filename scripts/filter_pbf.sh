#!/usr/bin/env bash
# filter_pbf.sh — tag-filter a US OSM PBF to long-distance routable roads.
#
# Drops residential, service, track, etc., keeping only the road classes a
# car would actually use to drive between parks. Output is ~25-30% the size
# of the input and produces OSRM artifacts that fit in BRONTOSAURUS's 24 GB
# WSL2 cap (full-US artifacts do not — see brontosaurus-osrm-memory-ceiling).
#
# Usage:
#   ./scripts/filter_pbf.sh data/us-latest.osm.pbf data/osrm-major/us-major.osm.pbf
#
# Requirements: docker. No osmium-tool install needed — uses iboates/osmium image.

set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <input.osm.pbf> <output.osm.pbf>" >&2
    exit 1
fi

INPUT="$1"
OUTPUT="$2"

if [[ ! -f "${INPUT}" ]]; then
    echo "ERROR: input PBF not found: ${INPUT}" >&2
    exit 1
fi

INPUT_ABS="$(realpath "${INPUT}")"
OUTPUT_DIR_ABS="$(realpath "$(dirname "${OUTPUT}")")"
OUTPUT_NAME="$(basename "${OUTPUT}")"
INPUT_NAME="$(basename "${INPUT_ABS}")"
INPUT_DIR_ABS="$(dirname "${INPUT_ABS}")"

mkdir -p "${OUTPUT_DIR_ABS}"

echo "[filter_pbf] input:  ${INPUT_ABS} ($(du -h "${INPUT_ABS}" | cut -f1))"
echo "[filter_pbf] output: ${OUTPUT_DIR_ABS}/${OUTPUT_NAME}"
echo "[filter_pbf] filter: highway in {motorway,trunk,primary,secondary,tertiary} + _link variants"

# Two mounts: input dir read-only, output dir read-write. Keeps the
# osmium container from being able to clobber the input file.
docker run --rm \
    -v "${INPUT_DIR_ABS}:/in:ro" \
    -v "${OUTPUT_DIR_ABS}:/out" \
    iboates/osmium:latest \
    tags-filter \
    --overwrite \
    -o "/out/${OUTPUT_NAME}" \
    "/in/${INPUT_NAME}" \
    w/highway=motorway,trunk,primary,secondary,tertiary,motorway_link,trunk_link,primary_link,secondary_link,tertiary_link

echo "[filter_pbf] done: $(du -h "${OUTPUT_DIR_ABS}/${OUTPUT_NAME}" | cut -f1)"
