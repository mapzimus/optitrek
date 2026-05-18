# Optitrek — Database Expansion Spec

## Overview

Standalone data engineering task executed after Tier 1 is complete and before Tier 2 solver work begins. Expands the `pois` table from ~400 NPS-only rows to ~100,000+ rows covering OSM tourist attractions, Amtrak stations, and overnight cities. The schema is already in place from Tier 1 — this task adds rows, not tables (except `amtrak_legs`).

**Prerequisite:** Tier 1 complete. NPS data in PostGIS, solver validated, blog post shipped.

**Deliverable:** Fully populated, validated, deduplicated POI database ready for Tier 2 solver work.

---

## Data Sources

| Source | Estimated Rows | Method | Notes |
|---|---|---|---|
| NPS API | ~400 | Already loaded (Tier 1) | No action needed |
| OSM Overpass API | ~70,000–100,000 | Bulk extract by category, batched by state | Heaviest lift |
| Amtrak GTFS | ~500 stations | Static feed download + parse | Plus `amtrak_legs` table for station-to-station travel times |
| Overnight cities | ~5,000–10,000 | OSM `place=city/town` with population filter | Proxy for "has lodging" |

---

## Phase 1 — OSM Tourist Attractions (Days 1–3)

### 1.1 Overpass Query Templates

Write parameterized Overpass QL queries for each target category:

| Category | OSM Tags | Normalized Category |
|---|---|---|
| Museums | `tourism=museum` | `museum` |
| Zoos | `tourism=zoo` | `zoo` |
| Aquariums | `tourism=aquarium` | `aquarium` |
| Theme parks | `tourism=theme_park` | `theme_park` |
| Stadiums | `leisure=stadium` | `stadium` |
| Monuments | `historic=monument` | `historic_marker` |
| Memorials | `historic=memorial` | `historic_marker` |
| Castles/Ruins | `historic=castle`, `historic=ruins` | `historic_marker` |
| Archaeological sites | `historic=archaeological_site` | `historic_marker` |
| Viewpoints | `tourism=viewpoint` | `viewpoint` |
| General attractions | `tourism=attraction` | `landmark` |
| Nature reserves | `leisure=nature_reserve` | `nature_reserve` |
| Galleries | `tourism=gallery` | `gallery` |

### 1.2 Batch Extraction

- Batch by state (50 states × 13 tag queries = ~650 queries)
- Throttle to 1 request/second to respect Overpass rate limits
- Retry on 429/timeout with exponential backoff
- Estimated total extraction time: 1–2 hours
- Save raw responses to `data/osm_raw/` for reproducibility

### 1.3 Parsing and Filtering

For each Overpass result, extract:
- `name` (discard if missing)
- `lat`, `lon` (discard if missing or outside contiguous US bounding box: 24°N–50°N, 125°W–66°W)
- All OSM tags → store in `tags` JSONB
- Wikidata ID if present (`wikidata` tag) → store in `tags` for Tier 3 collection generation

**Discard rules:**
- No `name` tag
- Coordinates outside contiguous US
- Tagged `disused=yes`, `abandoned=yes`, or `access=private`
- Duplicate OSM node IDs (can occur across batches)

### 1.4 Deduplication Against NPS

- For each OSM POI, check spatial proximity to existing NPS entries: `ST_DWithin(osm.geom, nps.geom, 500)` (500 meters)
- If within 500m AND fuzzy name match (Levenshtein distance < 5 or trigram similarity > 0.4), flag as duplicate
- NPS record wins — do not insert the OSM duplicate
- Log all dedup decisions for manual review

### 1.5 Spatial Join and Insert

- Assign `state` via `ST_Contains` against Census TIGER state boundaries (same as Tier 1)
- Insert into `pois` table with `source = 'osm'` and normalized `category`
- Use OSM node ID as a stable external identifier (store in `tags` JSONB as `osm_id`)

### 1.6 Validation

- Count POIs per state — flag any state with < 10 (suspiciously low)
- Count POIs per category — verify distribution is reasonable
- Spot-check 5 random POIs per category on a map — verify coordinates are correct
- Verify no null geometries, no null names, no duplicate OSM IDs
- Total row count should be in the 70,000–100,000 range

---

## Phase 2 — Amtrak Stations and Legs (Day 3–4)

### 2.1 GTFS Download

- Download Amtrak GTFS static feed from transit.land or Amtrak's published feed
- Relevant files: `stops.txt`, `stop_times.txt`, `trips.txt`, `routes.txt`

### 2.2 Station Ingestion

- Parse `stops.txt` for station name, lat, lon
- Filter to stations within contiguous US bounding box
- Insert into `pois` table with `source = 'amtrak'`, `category = 'train_station'`
- Store GTFS stop_id in `tags` JSONB for linking to schedules

### 2.3 Station-to-Station Travel Times

- Parse `stop_times.txt` + `trips.txt` to compute travel time between consecutive station pairs on each route
- For station pairs served by multiple trips, compute median travel time
- Determine frequency: daily, 3x/week, etc. based on trip count per week

### 2.4 Create and Populate `amtrak_legs` Table

```sql
CREATE TABLE amtrak_legs (
    id SERIAL PRIMARY KEY,
    origin_station_id INTEGER REFERENCES pois(id),
    destination_station_id INTEGER REFERENCES pois(id),
    travel_time_minutes INTEGER NOT NULL,
    route_name TEXT,
    frequency TEXT
);

CREATE INDEX idx_amtrak_origin ON amtrak_legs (origin_station_id);
CREATE INDEX idx_amtrak_dest ON amtrak_legs (destination_station_id);
```

- Insert one row per station pair per route
- Only include direct legs (no transfers) — transfer routing is out of scope

### 2.5 Validation

- Count stations — should be ~500
- Count legs — should be ~1,000–2,000
- Spot-check a known route (e.g., Northeast Corridor: Boston → New York → Philadelphia → Washington) — verify stations, travel times, and frequency are reasonable
- Verify all station POIs have valid geometries and state assignments

---

## Phase 3 — Overnight Cities (Day 4–5)

### 3.1 Overpass Query

Extract OSM `place=city` and `place=town` nodes within contiguous US:

```
[out:json][timeout:300];
(
  node["place"="city"](24.0,-125.0,50.0,-66.0);
  node["place"="town"](24.0,-125.0,50.0,-66.0);
);
out body;
```

### 3.2 Population Filtering

- OSM `population` tag is available on many (not all) place nodes
- Keep only places with `population` tag present AND value > 5,000
- For places without a `population` tag: include if `place=city` (cities are generally large enough), exclude if `place=town` (too uncertain)

### 3.3 Insert

- Insert into `pois` table with `source = 'osm'`, `category = 'overnight_city'`
- Store population in `tags` JSONB

### 3.4 Validation

- Count overnight cities — should be ~5,000–10,000
- Verify every state has at least 5 overnight cities (if not, the population threshold may be too aggressive for rural states — lower to 2,500 for those states)
- Spot-check: verify Springfield, IL (pop ~114k) is included; verify tiny hamlets are excluded

---

## Phase 4 — Category Taxonomy Finalization (Day 5)

### 4.1 Document the Final Taxonomy

After all data is loaded, document the complete category taxonomy with counts:

| Normalized Category | Source(s) | Expected Count Range |
|---|---|---|
| `nps_park` | NPS | 60–80 |
| `nps_monument` | NPS | 80–120 |
| `nps_historic` | NPS | 70–100 |
| `nps_other` | NPS | 100–150 |
| `museum` | OSM | 10,000–20,000 |
| `zoo` | OSM | 200–500 |
| `aquarium` | OSM | 50–100 |
| `theme_park` | OSM | 200–500 |
| `stadium` | OSM | 1,000–3,000 |
| `historic_marker` | OSM | 15,000–25,000 |
| `viewpoint` | OSM | 5,000–10,000 |
| `landmark` | OSM | 10,000–20,000 |
| `nature_reserve` | OSM | 5,000–10,000 |
| `gallery` | OSM | 2,000–5,000 |
| `train_station` | Amtrak | ~500 |
| `overnight_city` | OSM | 5,000–10,000 |

Counts are estimates — actual values validated after ingestion.

### 4.2 NPS Category Refinement

Tier 1 loaded all NPS units with a generic category. Now refine using NPS API designation field:

- National Park → `nps_park`
- National Monument → `nps_monument`
- National Historic Site, National Historical Park → `nps_historic`
- Everything else (National Battlefield, National Seashore, National Memorial, etc.) → `nps_other`

This is a single UPDATE query against existing NPS rows.

---

## Phase 5 — Final Validation and Stats (Day 5)

### 5.1 Global Checks

- Total POI count across all sources
- POIs per state — heatmap or table, flag outliers
- POIs per category — verify no category is empty or suspiciously small
- No null geometries anywhere
- No duplicate entries (same name + within 100m of each other + same source)
- Spatial index is built and functional (`idx_pois_geom`)

### 5.2 Cross-Source Consistency

- NPS units should not also appear as OSM entries (dedup check from Phase 1)
- Amtrak stations should not overlap with tourist attraction POIs (different category, no conflict expected)
- Overnight cities should not overlap with tourist attractions (different category, same table, no conflict)

### 5.3 Deliverable

A validation report (markdown or notebook) documenting:
- Row counts per source and category
- State coverage summary
- Any anomalies flagged and resolved
- Confirmation that the database is ready for Tier 2 solver work

---

## Schema Summary (Post-Expansion)

```sql
-- pois table (unchanged from Tier 1, just more rows)
-- Approximate row counts after expansion:
--   source = 'nps':     ~400
--   source = 'osm':     ~70,000–100,000
--   source = 'amtrak':  ~500
--   category = 'overnight_city': ~5,000–10,000
--   TOTAL:              ~80,000–110,000

-- New table (Tier 3 Amtrak routing, created now for data completeness)
-- amtrak_legs:          ~1,000–2,000
```

---

## Timeline

| Phase | Task | Days |
|---|---|---|
| 1 | OSM tourist attractions (extract, parse, dedup, load, validate) | 3 |
| 2 | Amtrak stations and legs (GTFS parse, load, validate) | 1–2 |
| 3 | Overnight cities (extract, filter, load, validate) | 1 |
| 4 | Category taxonomy finalization + NPS category refinement | 0.5 |
| 5 | Final validation and stats report | 0.5 |
| **Total** | | **5–6 days** |

---

## Known Risks

- **Overpass API rate limits:** 650 queries at 1/second = ~11 minutes of queries, but timeouts and retries could extend this. Budget 1–2 hours.
- **OSM data quality:** Some POIs will have wrong coordinates, missing names, or miscategorized tags. The filtering rules catch most of these but manual spot-checking is essential.
- **Deduplication imperfection:** The 500m + fuzzy name match heuristic will miss some duplicates and incorrectly flag some non-duplicates. Acceptable for now — Tier 3's user error reporting handles residual issues.
- **Amtrak GTFS availability:** Amtrak's GTFS feed has historically been available but not always well-maintained. If the feed is unavailable or malformed, defer Amtrak ingestion to Tier 3 and proceed with OSM + overnight cities only.
- **Population data gaps:** Many OSM town nodes lack population tags. The filtering rules handle this conservatively (include cities, exclude untagged towns) but some qualifying towns will be missed. Acceptable for overnight suggestion purposes.

---

## Next Step

Complete Tier 1 first. Then execute this spec as a standalone data engineering task before starting Tier 2 solver generalization.
