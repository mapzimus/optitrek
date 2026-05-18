"""Phase 1B — assign state to every NPS POI via spatial join against Census TIGER.

Run from D:\\optitrek (after data_pull.py):
    python -m src.spatial_join

Idempotent. Re-runs overwrite the state field. Fails loudly if any of the 48
contiguous states + DC is missing an NPS unit.
"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.db import get_dsn

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2024/STATE/tl_2024_us_state.zip"
BOUNDARY_DIR = REPO_ROOT / "data" / "boundaries"
SHAPEFILE_PATH = BOUNDARY_DIR / "tl_2024_us_state.shp"
STAGING_TABLE = "tl_2024_us_state"

# 48 contiguous states + DC. Required to have ≥1 NPS unit per DECISIONS.md D2/D4.
REQUIRED_ZONES = {
    "AL","AR","AZ","CA","CO","CT","DC","DE","FL","GA",
    "IA","ID","IL","IN","KS","KY","LA","MA","MD","ME",
    "MI","MN","MO","MS","MT","NC","ND","NE","NH","NJ",
    "NM","NV","NY","OH","OK","OR","PA","RI","SC","SD",
    "TN","TX","UT","VA","VT","WA","WI","WV","WY",
}
assert len(REQUIRED_ZONES) == 49


def _download_tiger() -> None:
    """Download and unzip TIGER state shapefile if not present."""
    if SHAPEFILE_PATH.exists():
        print(f">> TIGER shapefile already present: {SHAPEFILE_PATH.relative_to(REPO_ROOT)}")
        return
    BOUNDARY_DIR.mkdir(parents=True, exist_ok=True)
    print(f">> Downloading TIGER 2024 state boundaries from Census ({TIGER_URL})")
    with urllib.request.urlopen(TIGER_URL, timeout=120) as resp:
        zip_bytes = resp.read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(BOUNDARY_DIR)
    print(f">> Extracted to {BOUNDARY_DIR.relative_to(REPO_ROOT)}")


def _load_to_postgis(engine) -> None:
    """Read shapefile, reproject to 4326, push to PostGIS staging table."""
    print(f">> Loading shapefile into staging table `{STAGING_TABLE}`")
    gdf = gpd.read_file(SHAPEFILE_PATH)
    if gdf.crs is None:
        gdf = gdf.set_crs(4269)  # TIGER native CRS, just in case
    gdf = gdf.to_crs(4326)
    # Keep only the columns we need; rename to lowercase for SQL ergonomics.
    keep = gdf[["STUSPS", "NAME", "geometry"]].rename(
        columns={"STUSPS": "stusps", "NAME": "name"}
    )
    keep.to_postgis(STAGING_TABLE, engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{STAGING_TABLE}_geom ON {STAGING_TABLE} USING GIST (geometry)"))
    print(f"   loaded {len(keep)} state polygons")


def _spatial_join(engine) -> None:
    print(">> Running spatial join (ST_Contains)")
    with engine.begin() as conn:
        # Reset state on NPS rows so re-runs are clean.
        conn.execute(text("UPDATE pois SET state = NULL WHERE source = 'nps'"))
        result = conn.execute(text(f"""
            UPDATE pois p
               SET state = s.stusps
              FROM {STAGING_TABLE} s
             WHERE p.source = 'nps'
               AND ST_Contains(s.geometry, p.geom)
        """))
        print(f"   {result.rowcount} rows assigned a state")


def _coverage_report(engine) -> bool:
    """Print per-state counts and return True iff every required zone is covered."""
    print(">> Coverage report (NPS units per state)")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT COALESCE(state, '(none)') AS state, COUNT(*) AS n
              FROM pois
             WHERE source = 'nps'
             GROUP BY 1
             ORDER BY 1
        """)).all()
    counts = {state: n for state, n in rows}
    total = sum(counts.values())
    unassigned = counts.pop("(none)", 0)

    # Tabular print
    print(f"   total NPS rows: {total}   unassigned: {unassigned}")
    print(f"   {'state':<8}{'count':>6}")
    for state in sorted(counts):
        marker = "" if state in REQUIRED_ZONES or state in {"AK","HI"} else "  (non-required)"
        print(f"   {state:<8}{counts[state]:>6}{marker}")

    missing = sorted(REQUIRED_ZONES - counts.keys())
    if missing:
        print()
        print(f"!! COVERAGE FAILURE — {len(missing)} required zone(s) missing an NPS unit:")
        for z in missing:
            print(f"     {z}")
        return False

    # Bonus info: AK/HI status (ingested but excluded from solver per DECISIONS.md).
    for z in ("AK", "HI"):
        if z in counts:
            print(f"   note: {z} has {counts[z]} units; excluded from Tier 1 solver per DECISIONS.md")

    print()
    print(">> Coverage OK — all 48 contiguous states + DC have ≥1 NPS unit.")
    return True


def main() -> int:
    _download_tiger()
    engine = create_engine(get_dsn())
    _load_to_postgis(engine)
    _spatial_join(engine)
    ok = _coverage_report(engine)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
