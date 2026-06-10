"""Expansion Phase 1.4-1.6 (spec 04) — load parsed OSM POIs into PostGIS.

Run from the repo root (after src.osm_pull, and after src.spatial_join has
loaded the TIGER state polygons):
    python -m src.osm_load
    python -m src.osm_load --dry-run     # parse + stats only, no DB writes

Pipeline:
  1. Read data/osm_parsed/osm_pois.jsonl
  2. COPY into a fresh `osm_staging` table
  3. Dedup against NPS rows: within 500 m AND fuzzy name match → flag,
     NPS wins (spec §1.4); decisions logged to data/osm_parsed/dedup_log.csv
  4. Upsert survivors into `pois` with source='osm' (idempotent on osm_id)
  5. Spatial join: assign state via ST_Contains against the TIGER staging
     table created by src.spatial_join (spec §1.5)
  6. Validation report (spec §1.6 + §3.4) → data/osm_parsed/validation_report.md
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from src.db import apply_schema, get_conn

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSED_PATH = REPO_ROOT / "data" / "osm_parsed" / "osm_pois.jsonl"
DEDUP_LOG = REPO_ROOT / "data" / "osm_parsed" / "dedup_log.csv"
REPORT_PATH = REPO_ROOT / "data" / "osm_parsed" / "validation_report.md"

TIGER_TABLE = "tl_2024_us_state"  # created by src.spatial_join

# Spec §1.4 dedup thresholds.
DEDUP_RADIUS_METERS = 500
DEDUP_LEVENSHTEIN_MAX = 4      # "distance < 5"
DEDUP_TRIGRAM_MIN = 0.4

# Spec §1.6 / §3.4 validation thresholds.
MIN_POIS_PER_STATE = 10
MIN_CITIES_PER_STATE = 5

REQUIRED_KEYS = ("osm_id", "name", "category", "lat", "lon", "tags")

STAGING_DDL = """
DROP TABLE IF EXISTS osm_staging;
CREATE TABLE osm_staging (
    osm_id  TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    category TEXT NOT NULL,
    lat     FLOAT8 NOT NULL,
    lon     FLOAT8 NOT NULL,
    tags    JSONB NOT NULL DEFAULT '{}'::jsonb,
    geom    GEOMETRY(Point, 4326),
    is_nps_duplicate BOOLEAN NOT NULL DEFAULT FALSE
);
"""

# Overnight cities are skipped: a city node near a similarly-named NPS unit
# is not a duplicate of it (different category, different purpose).
DEDUP_PAIRS_SQL = """
SELECT s.osm_id, s.name AS osm_name, p.id AS nps_id, p.name AS nps_name,
       ROUND(ST_Distance(s.geom::geography, p.geom::geography)::numeric, 1) AS meters
  FROM osm_staging s
  JOIN pois p
    ON p.source = 'nps'
   AND ST_DWithin(s.geom::geography, p.geom::geography, %(radius)s)
 WHERE s.category <> 'overnight_city'
   AND (
        levenshtein_less_equal(lower(left(s.name, 255)), lower(left(p.name, 255)),
                               %(lev_max)s) <= %(lev_max)s
     OR similarity(s.name, p.name) > %(trgm_min)s
   )
 ORDER BY s.osm_id
"""

UPSERT_SQL = """
INSERT INTO pois (name, source, category, geom, tags)
SELECT s.name, 'osm', s.category, s.geom,
       s.tags || jsonb_build_object('osm_id', s.osm_id)
  FROM osm_staging s
 WHERE NOT s.is_nps_duplicate
ON CONFLICT ((tags->>'osm_id')) WHERE source = 'osm'
DO UPDATE SET
    name     = EXCLUDED.name,
    category = EXCLUDED.category,
    geom     = EXCLUDED.geom,
    tags     = pois.tags || EXCLUDED.tags
"""


def read_parsed_rows(path: Path) -> list[dict[str, Any]]:
    """Read the JSONL written by src.osm_pull, validating each line carries
    the full row contract. Fails loudly on a malformed line — a partial or
    hand-edited file should never reach the DB silently."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{lineno}: invalid JSON ({exc})") from exc
            missing = [k for k in REQUIRED_KEYS if k not in row]
            if missing:
                raise ValueError(f"{path.name}:{lineno}: missing keys {missing}")
            rows.append(row)
    return rows


def _stage_rows(cur, rows: list[dict[str, Any]]) -> None:
    cur.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    cur.execute(STAGING_DDL)
    with cur.copy("COPY osm_staging (osm_id, name, category, lat, lon, tags) FROM STDIN") as copy:
        for r in rows:
            copy.write_row((r["osm_id"], r["name"], r["category"], r["lat"], r["lon"], json.dumps(r["tags"])))
    cur.execute("UPDATE osm_staging SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)")
    cur.execute("ALTER TABLE osm_staging ALTER COLUMN geom SET NOT NULL")
    cur.execute("CREATE INDEX idx_osm_staging_geom ON osm_staging USING GIST (geom)")
    cur.execute("ANALYZE osm_staging")


def _dedup_against_nps(cur) -> int:
    """Flag staging rows that duplicate an NPS unit (spec §1.4). Writes every
    decision to DEDUP_LOG for manual review. Returns the flag count."""
    cur.execute(
        DEDUP_PAIRS_SQL,
        {
            "radius": DEDUP_RADIUS_METERS,
            "lev_max": DEDUP_LEVENSHTEIN_MAX,
            "trgm_min": DEDUP_TRIGRAM_MIN,
        },
    )
    pairs = cur.fetchall()

    DEDUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DEDUP_LOG.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["osm_id", "osm_name", "nps_id", "nps_name", "meters"])
        w.writerows(pairs)

    dup_ids = sorted({p[0] for p in pairs})
    if dup_ids:
        cur.execute(
            "UPDATE osm_staging SET is_nps_duplicate = TRUE WHERE osm_id = ANY(%(ids)s)",
            {"ids": dup_ids},
        )
    return len(dup_ids)


def _spatial_join(cur) -> tuple[int, int]:
    """Assign state to every OSM row via ST_Contains against the TIGER
    polygons (spec §1.5). Returns (assigned, unassigned)."""
    cur.execute("SELECT to_regclass(%s)", (TIGER_TABLE,))
    if cur.fetchone()[0] is None:
        raise SystemExit(
            f"ERROR: staging table `{TIGER_TABLE}` not found. "
            "Run `python -m src.spatial_join` once to load the TIGER state polygons."
        )
    # Reset so re-runs pick up geometry changes from the upsert.
    cur.execute("UPDATE pois SET state = NULL WHERE source = 'osm'")
    cur.execute(
        f"""
        UPDATE pois p
           SET state = s.stusps
          FROM {TIGER_TABLE} s
         WHERE p.source = 'osm'
           AND ST_Contains(s.geometry, p.geom)
        """
    )
    assigned = cur.rowcount
    cur.execute("SELECT COUNT(*) FROM pois WHERE source = 'osm' AND state IS NULL")
    (unassigned,) = cur.fetchone()
    return assigned, unassigned


def _validate(cur, n_unassigned: int) -> list[str]:
    """Spec §1.6 + §3.4 checks. Returns report lines; writes REPORT_PATH."""
    lines: list[str] = [f"# OSM expansion validation report — {date.today().isoformat()}", ""]
    warnings: list[str] = []

    cur.execute("SELECT source, COUNT(*) FROM pois GROUP BY source ORDER BY source")
    lines += ["## Rows per source", ""]
    for source, n in cur.fetchall():
        lines.append(f"- `{source}`: {n:,}")

    cur.execute(
        "SELECT category, COUNT(*) FROM pois WHERE source = 'osm' GROUP BY category ORDER BY category"
    )
    cat_counts = cur.fetchall()
    lines += ["", "## OSM rows per category", "", "| category | count |", "|---|---|"]
    for category, n in cat_counts:
        lines.append(f"| {category} | {n:,} |")
    for category, n in cat_counts:
        if n == 0:
            warnings.append(f"category `{category}` is empty")

    cur.execute(
        """
        SELECT COALESCE(state, '(none)'), COUNT(*)
          FROM pois
         WHERE source = 'osm' AND category <> 'overnight_city'
         GROUP BY 1 ORDER BY 1
        """
    )
    state_counts = dict(cur.fetchall())
    lines += ["", "## OSM attractions per state", "", "| state | count |", "|---|---|"]
    for state, n in sorted(state_counts.items()):
        flag = " ⚠️" if state != "(none)" and n < MIN_POIS_PER_STATE else ""
        lines.append(f"| {state} | {n:,}{flag} |")
    for state, n in sorted(state_counts.items()):
        if state != "(none)" and n < MIN_POIS_PER_STATE:
            warnings.append(f"state {state} has only {n} OSM attractions (< {MIN_POIS_PER_STATE})")

    cur.execute(
        """
        SELECT state, COUNT(*)
          FROM pois
         WHERE source = 'osm' AND category = 'overnight_city' AND state IS NOT NULL
         GROUP BY state
        """
    )
    city_counts = dict(cur.fetchall())
    lines += ["", "## Overnight cities per state", ""]
    low_city_states = sorted(
        s for s, n in city_counts.items() if n < MIN_CITIES_PER_STATE
    )
    if city_counts:
        lines.append(f"- {sum(city_counts.values()):,} cities across {len(city_counts)} states")
        for state in low_city_states:
            warnings.append(
                f"state {state} has only {city_counts[state]} overnight cities "
                f"(< {MIN_CITIES_PER_STATE}); consider lowering the population "
                "threshold to 2,500 for it (spec §3.4)"
            )
    else:
        lines.append("- none loaded (run osm_pull without --skip-places)")

    cur.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT tags->>'osm_id'
              FROM pois
             WHERE source = 'osm'
             GROUP BY 1 HAVING COUNT(*) > 1
        ) d
        """
    )
    (n_dup_ids,) = cur.fetchone()
    if n_dup_ids:
        warnings.append(f"{n_dup_ids} duplicate osm_id values in pois (should be impossible)")
    if n_unassigned:
        warnings.append(
            f"{n_unassigned} OSM rows have no state assignment (outside TIGER "
            "polygons; invisible to Tier 2 queries, which require state IS NOT NULL)"
        )

    lines += ["", "## Warnings", ""]
    lines += [f"- ⚠️ {w}" for w in warnings] if warnings else ["- none"]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=PARSED_PATH)
    parser.add_argument("--dry-run", action="store_true", help="parse + stats only, no DB writes")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: {args.input} not found. Run `python -m src.osm_pull` first.", file=sys.stderr)
        return 2

    print(f">> Reading {args.input}")
    rows = read_parsed_rows(args.input)
    print(f"   {len(rows):,} rows")
    for category, n in sorted(Counter(r["category"] for r in rows).items()):
        print(f"   {category:<20}{n:>8,}")
    if args.dry_run:
        print(">> --dry-run: stopping before DB writes")
        return 0

    print(">> Applying schema")
    apply_schema()

    with get_conn() as conn, conn.cursor() as cur:
        print(">> Staging rows (COPY)")
        _stage_rows(cur, rows)

        print(">> Deduplicating against NPS (500 m + fuzzy name)")
        n_dups = _dedup_against_nps(cur)
        print(f"   {n_dups} flagged as NPS duplicates → {DEDUP_LOG.relative_to(REPO_ROOT)}")

        print(">> Upserting into pois")
        cur.execute(UPSERT_SQL)
        print(f"   {cur.rowcount:,} rows upserted")

        print(">> Spatial join (ST_Contains against TIGER)")
        assigned, unassigned = _spatial_join(cur)
        print(f"   {assigned:,} assigned, {unassigned:,} without a state")

        print(">> Cleaning up staging table")
        cur.execute("DROP TABLE osm_staging")
        conn.commit()

        print(">> Validating")
        warnings = _validate(cur, unassigned)

    print(f">> Report: {REPORT_PATH.relative_to(REPO_ROOT)}")
    if warnings:
        print(f"!! {len(warnings)} validation warning(s):")
        for w in warnings:
            print(f"   - {w}")
    else:
        print(">> Validation clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
