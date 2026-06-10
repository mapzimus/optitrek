"""Load MassDOT crash data into PostGIS.

Two sources, one table:

1. IMPACT portal CSV extracts (recent years, the primary source):
     python etl/load_crashes.py --csv data/crashes_2025.csv --year 2025
   Download instructions in etl/README.md. Column names below match the
   IMPACT "Crash Details" extract headers (CRASH_NUMB, CRASH_DATE,
   CRASH_SEVERITY_DESCR, LAT, LON, ...).

2. MassDOT ArcGIS FeatureServer (closed years 2002-2019, no download step):
     python etl/load_crashes.py --from-arcgis --year 2019
   Service: gis.massdot.state.ma.us/arcgis/rest/services/CrashClosedYear/

Re-runnable by design: rows upsert on crash_id.
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg

ARCGIS_BASE = (
    "https://gis.massdot.state.ma.us/arcgis/rest/services/"
    "CrashClosedYear/CrashClosedYear{year}/FeatureServer/0/query"
)
ARCGIS_PAGE_SIZE = 2000
MAX_RETRIES = 3

DDL = """
CREATE TABLE IF NOT EXISTS crashes (
    crash_id     text PRIMARY KEY,
    year         integer NOT NULL,
    crash_date   date,
    severity     text NOT NULL,        -- 'fatal' | 'injury' | 'pdo' | 'unknown'
    severity_raw text,
    city_town    text,
    geom         geometry(Point, 4326) NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crashes_geom_gist ON crashes USING gist (geom);
CREATE INDEX IF NOT EXISTS crashes_year_idx ON crashes (year);
"""

UPSERT = """
INSERT INTO crashes (crash_id, year, crash_date, severity, severity_raw, city_town, geom)
VALUES (%(crash_id)s, %(year)s, %(crash_date)s, %(severity)s, %(severity_raw)s,
        %(city_town)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326))
ON CONFLICT (crash_id) DO UPDATE SET
    year = EXCLUDED.year,
    crash_date = EXCLUDED.crash_date,
    severity = EXCLUDED.severity,
    severity_raw = EXCLUDED.severity_raw,
    city_town = EXCLUDED.city_town,
    geom = EXCLUDED.geom,
    loaded_at = now()
"""


def classify_severity(severity_descr: str | None) -> str:
    s = (severity_descr or "").lower()
    if "fatal" in s and "non-fatal" not in s:
        return "fatal"
    if "injury" in s:
        return "injury"
    if "property damage" in s:
        return "pdo"
    return "unknown"


def _row_from_attrs(attrs: dict, year: int) -> dict | None:
    """Normalize one record (CSV row or ArcGIS attributes) to an upsert dict."""
    crash_id = str(attrs.get("CRASH_NUMB") or "").strip()
    lat_raw, lon_raw = attrs.get("LAT"), attrs.get("LON")
    try:
        lat, lon = float(lat_raw), float(lon_raw)
    except (TypeError, ValueError):
        return None
    if not crash_id or lat == 0 or lon == 0:
        return None

    crash_date = None
    raw_date = attrs.get("CRASH_DATE") or attrs.get("CRASH_DATE_TEXT")
    if isinstance(raw_date, (int, float)):           # ArcGIS epoch millis
        crash_date = datetime.fromtimestamp(raw_date / 1000, tz=timezone.utc).date()
    elif raw_date:
        for fmt in ("%d-%b-%Y", "%m/%d/%Y", "%Y-%m-%d"):
            try:
                crash_date = datetime.strptime(str(raw_date).strip(), fmt).date()
                break
            except ValueError:
                continue

    severity_raw = attrs.get("CRASH_SEVERITY_DESCR")
    return {
        "crash_id": crash_id,
        "year": year,
        "crash_date": crash_date,
        "severity": classify_severity(severity_raw),
        "severity_raw": severity_raw,
        "city_town": attrs.get("CITY_TOWN_NAME"),
        "lat": lat,
        "lon": lon,
    }


def iter_csv(path: str, year: int):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            row = _row_from_attrs(raw, year)
            if row:
                yield row


WANTED_FIELDS = ["CRASH_NUMB", "CRASH_DATE", "CRASH_DATE_TEXT",
                 "CRASH_SEVERITY_DESCR", "CITY_TOWN_NAME", "LAT", "LON"]


def iter_arcgis(year: int):
    url = ARCGIS_BASE.format(year=year)
    offset = 0
    with httpx.Client(timeout=60) as client:
        # Field names drift across years (e.g. 2017 has CRASH_DATE_TEXT but
        # not CRASH_DATE); requesting a missing field is a hard 400.
        layer = client.get(url.rsplit("/query", 1)[0], params={"f": "json"}).json()
        available = {f["name"] for f in layer.get("fields", [])}
        out_fields = ",".join(f for f in WANTED_FIELDS if f in available)
        while True:
            params = {
                "where": "1=1",
                "outFields": out_fields,
                "returnGeometry": "false",
                "orderByFields": "OBJECTID",   # stable pagination
                "resultOffset": offset,
                "resultRecordCount": ARCGIS_PAGE_SIZE,
                "f": "json",
            }
            for attempt in range(MAX_RETRIES):
                try:
                    payload = client.get(url, params=params).json()
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    if attempt == MAX_RETRIES - 1:
                        raise SystemExit(f"ArcGIS fetch failed after {MAX_RETRIES} tries: {exc}")
                    time.sleep(2 * (attempt + 1))
            if "error" in payload:
                raise SystemExit(f"ArcGIS error: {payload['error']}")
            features = payload.get("features", [])
            if not features:
                return
            for feature in features:
                row = _row_from_attrs(feature["attributes"], year)
                if row:
                    yield row
            offset += len(features)
            print(f"  fetched {offset} records...", file=sys.stderr)


def load(rows, dsn: str) -> int:
    count = 0
    with psycopg.connect(dsn) as conn:
        conn.execute(DDL)
        with conn.cursor() as cur:
            batch = []
            for row in rows:
                batch.append(row)
                if len(batch) >= 1000:
                    cur.executemany(UPSERT, batch)
                    count += len(batch)
                    batch = []
            if batch:
                cur.executemany(UPSERT, batch)
                count += len(batch)
        conn.commit()
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="IMPACT portal CSV extract path")
    source.add_argument("--from-arcgis", action="store_true",
                        help="fetch from MassDOT ArcGIS (closed years 2002-2019)")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""),
                        help="Postgres DSN (default: $DATABASE_URL)")
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("Set DATABASE_URL or pass --dsn")

    rows = iter_csv(args.csv, args.year) if args.csv else iter_arcgis(args.year)
    n = load(rows, args.dsn)
    print(f"Upserted {n} crashes for {args.year}.")


if __name__ == "__main__":
    main()
