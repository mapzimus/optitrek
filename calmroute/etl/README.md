# Calm Route ETL

## Crash data (`load_crashes.py`)

Target table: `crashes` in the `calmroute` Neon PostGIS database
(GIST-indexed point geometry, upsert on `crash_id` — safe to re-run).

### Source A — IMPACT portal extracts (recent years; primary)

1. Go to the MassDOT IMPACT Crash Data Portal:
   <https://apps.impact.dot.state.ma.us/cdp/home>
2. Open **Data Extracts** and request a statewide **Crash Details** extract
   for each of the most recent 5 full years. The export is CSV with the
   standard MassDOT crash schema (`CRASH_NUMB`, `CRASH_DATE`,
   `CRASH_SEVERITY_DESCR`, `LAT`, `LON`, `CITY_TOWN_NAME`, ...).
3. Load each year:

   ```bash
   export DATABASE_URL=postgresql://...neon.../calmroute
   python etl/load_crashes.py --csv data/crashes_2025.csv --year 2025
   ```

Rows without usable coordinates (LAT/LON missing or 0) are skipped —
typically a small percent of records that MassDOT could not geocode.

### Source B — MassDOT ArcGIS FeatureServer (closed years 2002–2019)

MassDOT also publishes finalized ("closed") crash years as public
FeatureServers, which need no portal account:

```bash
python etl/load_crashes.py --from-arcgis --year 2019
```

Useful for backfills and for verifying the pipeline without portal access.

### Verification

```sql
SELECT year, severity, count(*) FROM crashes GROUP BY 1, 2 ORDER BY 1, 2;

-- Crashes within 100 m of a known bad intersection
-- (Route 1 / Route 60 interchange, Revere):
SELECT count(*) FROM crashes
WHERE ST_DWithin(geom::geography,
                 ST_SetSRID(ST_MakePoint(-71.0095, 42.4198), 4326)::geography,
                 100);
```

## Bridge segments (`load_bridges.py`)

The winter layer applies a freeze-first multiplier to routes that spend
distance on bridges. This loader extracts `bridge=yes` car-road ways from
the same Massachusetts PBF the OSRM engine uses:

```bash
python etl/load_bridges.py --pbf data/massachusetts-latest.osm.pbf
```

Target table: `bridges` (LineString, GIST-indexed, upsert on OSM way id).
Re-run after refreshing the PBF.
