"""Phase 1A — pull the full NPS catalog into PostGIS.

Run from D:\\optitrek:
    python -m src.data_pull

Idempotent. Re-runs upsert on (source='nps', tags->>'park_code') so existing rows
are updated rather than duplicated.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import requests
from dotenv import load_dotenv

from src.db import apply_schema, get_conn

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

NPS_API_URL = "https://developer.nps.gov/api/v1/parks"
PAGE_SIZE = 500            # API max
REQUEST_TIMEOUT = 30
RAW_DIR = REPO_ROOT / "data" / "nps_raw"
DISCARD_LOG = RAW_DIR / "discarded.csv"

# Contiguous-US bounding box used as a sanity filter.
# AK and HI parks (and any territories) fall outside this box and get discarded
# from the *coverage candidate set*, but we still keep them in the DB for the
# future expansion. So instead of discarding, we record state='AK'/'HI' via the
# spatial join and let the solver filter them out. Discard only when coordinates
# are missing, zero, non-numeric, or clearly bogus (outside the wider US+AK+HI box).
WIDE_US_BOX = {"min_lat": 17.0, "max_lat": 72.0, "min_lon": -180.0, "max_lon": -65.0}

# Map NPS designation strings to normalized category labels.
# Anything not in this map falls through to "nps_other".
DESIGNATION_MAP: dict[str, str] = {
    "National Park": "national_park",
    "National Monument": "national_monument",
    "National Historic Site": "national_historic_site",
    "National Historical Park": "national_historical_park",
    "National Memorial": "national_memorial",
    "National Battlefield": "national_battlefield",
    "National Battlefield Park": "national_battlefield",
    "National Military Park": "national_battlefield",
    "National Preserve": "national_preserve",
    "National Reserve": "national_preserve",
    "National Seashore": "national_seashore",
    "National Lakeshore": "national_lakeshore",
    "National Recreation Area": "national_recreation_area",
    "National River": "national_river",
    "National Wild and Scenic River": "national_river",
    "National Wild and Scenic River & Recreation Area": "national_river",
    "National Scenic River": "national_river",
    "National Scenic Trail": "national_trail",
    "National Historic Trail": "national_trail",
    "National Parkway": "national_parkway",
    "Park": "national_park",
}


def _api_key() -> str:
    key = os.environ.get("NPS_API_KEY")
    if not key:
        print("ERROR: NPS_API_KEY not set in .env", file=sys.stderr)
        print("       Get a free key at https://www.nps.gov/subjects/developer/get-started.htm")
        sys.exit(1)
    return key


def fetch_all_parks(api_key: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Page through /parks until exhausted. Yields (page_index, json_blob)."""
    start = 0
    page_index = 0
    while True:
        resp = requests.get(
            NPS_API_URL,
            headers={"X-Api-Key": api_key},
            params={"limit": PAGE_SIZE, "start": start},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        blob = resp.json()
        yield page_index, blob

        data = blob.get("data", [])
        total = int(blob.get("total", 0))
        start += len(data)
        page_index += 1
        if not data or start >= total:
            return
        time.sleep(0.25)  # courtesy delay


def _save_raw(page_index: int, blob: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"parks_{page_index:03d}.json"
    out.write_text(json.dumps(blob, indent=2), encoding="utf-8")


def _parse_park(park: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Return (row_dict, None) on success or (None, discard_reason) on failure."""
    park_code = park.get("parkCode") or ""
    name = park.get("fullName") or park.get("name") or ""
    lat_raw = park.get("latitude")
    lon_raw = park.get("longitude")
    designation = park.get("designation") or ""
    api_states = park.get("states") or ""

    if not park_code or not name:
        return None, "missing_park_code_or_name"

    try:
        lat = float(lat_raw) if lat_raw not in (None, "") else None
        lon = float(lon_raw) if lon_raw not in (None, "") else None
    except (TypeError, ValueError):
        return None, "non_numeric_coords"

    if lat is None or lon is None:
        return None, "missing_coords"
    if lat == 0.0 and lon == 0.0:
        return None, "zero_coords"
    if not (WIDE_US_BOX["min_lat"] <= lat <= WIDE_US_BOX["max_lat"]):
        return None, f"lat_out_of_bounds:{lat}"
    if not (WIDE_US_BOX["min_lon"] <= lon <= WIDE_US_BOX["max_lon"]):
        return None, f"lon_out_of_bounds:{lon}"

    category = DESIGNATION_MAP.get(designation, "nps_other")
    return (
        {
            "park_code": park_code,
            "name": name,
            "category": category,
            "designation": designation,
            "lat": lat,
            "lon": lon,
            "api_states": api_states,
        },
        None,
    )


UPSERT_SQL = """
INSERT INTO pois (name, source, category, geom, tags)
VALUES (
    %(name)s::text,
    'nps',
    %(category)s::text,
    ST_SetSRID(ST_MakePoint(%(lon)s::float8, %(lat)s::float8), 4326),
    jsonb_build_object(
        'park_code',  %(park_code)s::text,
        'designation', %(designation)s::text,
        'api_states', %(api_states)s::text
    )
)
ON CONFLICT ((tags->>'park_code')) WHERE source = 'nps'
DO UPDATE SET
    name     = EXCLUDED.name,
    category = EXCLUDED.category,
    geom     = EXCLUDED.geom,
    tags     = pois.tags || EXCLUDED.tags;
"""


def main() -> int:
    api_key = _api_key()
    print(">> Applying schema")
    apply_schema()

    print(">> Fetching NPS catalog")
    fetched = 0
    parsed: list[dict[str, Any]] = []
    discards: list[tuple[str, str, str]] = []  # (park_code, name, reason)

    for page_index, blob in fetch_all_parks(api_key):
        _save_raw(page_index, blob)
        items = blob.get("data", [])
        fetched += len(items)
        print(f"   page {page_index}: {len(items)} parks (total fetched: {fetched})")
        for park in items:
            row, reason = _parse_park(park)
            if row is None:
                discards.append(
                    (
                        park.get("parkCode") or "",
                        park.get("fullName") or park.get("name") or "",
                        reason or "unknown",
                    )
                )
                continue
            parsed.append(row)

    print(f">> Parsed {len(parsed)} parks, discarded {len(discards)}")

    if discards:
        DISCARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DISCARD_LOG.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["park_code", "name", "reason"])
            w.writerows(discards)
        print(f"   discard reasons written to {DISCARD_LOG.relative_to(REPO_ROOT)}")
        # Tally reasons
        from collections import Counter
        for reason, n in Counter(d[2] for d in discards).most_common():
            print(f"     {n:>4}  {reason}")

    print(">> Upserting into pois")
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, parsed)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM pois WHERE source = 'nps'")
        (n_rows,) = cur.fetchone()
    print(f">> Done. pois rows where source='nps': {n_rows}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
