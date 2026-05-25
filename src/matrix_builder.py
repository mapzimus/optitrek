"""Phase 2 — build the N×N driving-time matrix via OSRM /table.

Pulls all NPS POIs from PostGIS, queries the local OSRM server in batches,
and caches the result to a parquet file under data/matrix/.

Run from D:\\optitrek (after data_pull.py and spatial_join.py, and after OSRM
is up — see scripts/build_osrm.sh for OSRM bring-up):
    python -m src.matrix_builder

Configurable via env vars:
    OSRM_URL          — base URL for OSRM (default http://localhost:5000)
    OSRM_BATCH_SIZE   — sources per /table request (default 100; OSRM max is ~100)
    OSRM_TIMEOUT      — request timeout in seconds (default 60)

Output (under data/matrix/):
    pois.parquet      — one row per POI: id, name, state, category, lat, lon
    duration.parquet  — N×N drive-time matrix in seconds (float32)
    distance.parquet  — N×N driving-distance matrix in meters (float32)

The two matrices are written as parquet tables with no schema (an unnamed
N-column float32 table). Use load_matrix() to read them back.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from dotenv import load_dotenv

from src.db import get_conn

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)  # project .env wins over shell env

MATRIX_DIR = REPO_ROOT / "data" / "matrix"
DEFAULT_OSRM_URL = "http://localhost:5000"
DEFAULT_BATCH = 100
DEFAULT_TIMEOUT = 60

# Exclude AK, HI, and all US territories from the Tier 1 solver candidate
# set per DECISIONS.md (non-contiguous; US-only OSRM extract can't route to
# them anyway). Territories are listed defensively — only PR currently has
# an NPS unit in the catalog, but VI/GU/MP/AS could appear in future pulls
# and would silently waste OSRM /table calls + sit as phantom nodes in the
# solver search space (they're not in REQUIRED_STATES in src/run_tier1.py).
EXCLUDED_STATES = {"AK", "HI", "PR", "VI", "GU", "MP", "AS"}


def _osrm_url() -> str:
    return os.environ.get("OSRM_URL", DEFAULT_OSRM_URL).rstrip("/")


def _batch_size() -> int:
    return int(os.environ.get("OSRM_BATCH_SIZE", DEFAULT_BATCH))


def _timeout() -> int:
    return int(os.environ.get("OSRM_TIMEOUT", DEFAULT_TIMEOUT))


def fetch_pois() -> list[dict]:
    """Pull NPS POIs from PostGIS, ordered by (state, id) so the index in the
    matrix is stable across re-runs (assuming the DB hasn't changed)."""
    with get_conn() as conn, conn.cursor() as cur:
        # psycopg v3 does NOT expand a tuple into "IN (...)" the way psycopg2
        # did — it sends the tuple as a single parameter and Postgres errors
        # with 'syntax error at or near "$1"'. Use array <> ALL() instead.
        cur.execute("""
            SELECT id,
                   name,
                   state,
                   category,
                   ST_Y(geom) AS lat,
                   ST_X(geom) AS lon
              FROM pois
             WHERE source = 'nps'
               AND state IS NOT NULL
               AND state <> ALL(%(excluded)s)
             ORDER BY state, id
        """, {"excluded": list(EXCLUDED_STATES) if EXCLUDED_STATES else ["__none__"]})
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _coord_string(pois: list[dict]) -> str:
    """OSRM wants lon,lat;lon,lat;… (yes, lon first — different from most APIs)."""
    return ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in pois)


def _request_table_block(
    pois: list[dict],
    sources: list[int],
    destinations: list[int] | None = None,
    osrm_url: str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Query OSRM /table for one block of (sources × destinations).
    Returns (durations_s, distances_m) as float32 2D arrays.

    `osrm_url` overrides the OSRM_URL env var when set — used by run_trip()
    to select between the US-only (port 5000) and US+Canada (port 5001)
    routing engines based on TripConfig.routing_network.
    """
    base = osrm_url or _osrm_url()
    url = (
        f"{base}/table/v1/driving/{_coord_string(pois)}"
        f"?annotations=duration,distance"
        f"&sources={';'.join(map(str, sources))}"
    )
    if destinations is not None:
        url += f"&destinations={';'.join(map(str, destinations))}"
    resp = requests.get(url, timeout=_timeout())
    resp.raise_for_status()
    blob = resp.json()
    if blob.get("code") != "Ok":
        raise RuntimeError(f"OSRM /table returned {blob.get('code')}: {blob}")
    durs = np.asarray(blob["durations"], dtype=np.float32)
    dists = np.asarray(blob["distances"], dtype=np.float32)
    return durs, dists


def build_matrix(
    pois: list[dict],
    osrm_url: str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the full N×N (duration, distance) matrices by batching /table calls.

    OSRM's /table endpoint accepts up to ~10,000 total coordinates per request
    but is most reliable with ~100 sources at a time against the full set of
    destinations. We submit one batch per chunk of sources, with all POIs as
    destinations every time. Result: ceil(N/batch) requests total.

    `osrm_url` overrides the OSRM_URL env var when set (used by run_trip() to
    pick between US-only and US+Canada routing engines). When None, falls
    back to the env var via `_osrm_url()`.
    """
    n = len(pois)
    if n == 0:
        raise RuntimeError("no POIs available for matrix construction")
    batch = _batch_size()
    effective_url = osrm_url or _osrm_url()
    print(f">> Building {n}x{n} matrix in batches of {batch} sources")
    print(f"   OSRM: {effective_url}  ({(n + batch - 1) // batch} requests expected)")

    duration = np.full((n, n), np.nan, dtype=np.float32)
    distance = np.full((n, n), np.nan, dtype=np.float32)
    t0 = time.monotonic()
    for chunk_start in range(0, n, batch):
        chunk_end = min(chunk_start + batch, n)
        sources = list(range(chunk_start, chunk_end))
        durs, dists = _request_table_block(pois, sources=sources, osrm_url=effective_url)
        duration[chunk_start:chunk_end, :] = durs
        distance[chunk_start:chunk_end, :] = dists
        elapsed = time.monotonic() - t0
        pct = chunk_end / n * 100
        print(f"   [{chunk_end:>4}/{n}] {pct:5.1f}%  ({elapsed:5.1f}s elapsed)")

    return duration, distance


def save_matrices(
    pois: list[dict],
    duration: np.ndarray,
    distance: np.ndarray,
) -> None:
    """Write matrices + the POI index table to parquet under data/matrix/."""
    MATRIX_DIR.mkdir(parents=True, exist_ok=True)

    poi_table = pa.table({
        "id":       pa.array([p["id"]       for p in pois], type=pa.int64()),
        "name":     pa.array([p["name"]     for p in pois], type=pa.string()),
        "state":    pa.array([p["state"]    for p in pois], type=pa.string()),
        "category": pa.array([p["category"] for p in pois], type=pa.string()),
        "lat":      pa.array([p["lat"]      for p in pois], type=pa.float64()),
        "lon":      pa.array([p["lon"]      for p in pois], type=pa.float64()),
    })
    pq.write_table(poi_table, MATRIX_DIR / "pois.parquet")
    pq.write_table(_matrix_to_table(duration), MATRIX_DIR / "duration.parquet")
    pq.write_table(_matrix_to_table(distance), MATRIX_DIR / "distance.parquet")

    print(f">> Wrote {MATRIX_DIR.relative_to(REPO_ROOT)}/pois.parquet "
          f"+ duration.parquet + distance.parquet")


def _matrix_to_table(m: np.ndarray) -> pa.Table:
    """Encode an N×N float32 matrix as a parquet table with columns c0..cN-1."""
    return pa.table({f"c{i}": m[:, i] for i in range(m.shape[1])})


def load_matrix(name: str) -> np.ndarray:
    """Read a saved matrix back into a numpy float32 array.
    `name` is one of 'duration' or 'distance'."""
    path = MATRIX_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run build_matrix first")
    table = pq.read_table(path)
    return np.stack([col.to_numpy() for col in table.columns], axis=1)


def validate_matrix(duration: np.ndarray, unreachable_threshold_hours: float = 48.0) -> dict:
    """Per Gap 10: scan for NaN/null and absurdly long legs. Returns a summary
    dict. Caller decides whether to drop unreachable POIs or fail."""
    n = duration.shape[0]
    threshold = unreachable_threshold_hours * 3600
    bad = np.isnan(duration) | (duration > threshold)
    np.fill_diagonal(bad, False)  # self-pairs are always 0, not "bad"
    per_row_bad = bad.sum(axis=1)
    summary = {
        "n_pois": n,
        "total_bad_pairs": int(bad.sum()),
        "rows_with_any_bad": int((per_row_bad > 0).sum()),
        "rows_above_10pct_bad": int((per_row_bad / (n - 1) > 0.10).sum()),
        "worst_row_bad_pct": float(per_row_bad.max() / (n - 1) * 100),
    }
    return summary


def main() -> int:
    print(">> Fetching POIs from PostGIS")
    pois = fetch_pois()
    print(f"   {len(pois)} POIs (NPS, contiguous + DC)")

    duration, distance = build_matrix(pois)

    print(">> Validating matrix")
    summary = validate_matrix(duration)
    for k, v in summary.items():
        print(f"   {k}: {v}")
    if summary["rows_above_10pct_bad"] > 0:
        print("!! Some POIs are unreachable from >10% of others — review before solving")

    save_matrices(pois, duration, distance)
    print(">> Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
