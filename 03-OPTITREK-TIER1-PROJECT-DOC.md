# Optitrek — Tier 1 Project Doc

## Overview

Replicate and improve Randal Olson's 2015 "optimal US road trip" using modern GIS and optimization tools. Given the full catalog of National Park Service units, find the shortest driving loop that visits all 48 contiguous states, stopping only at NPS sites.

**Olson's result:** ~50 hand-picked stops, 13,699 miles, genetic algorithm solver, Google Maps API.
**Our target:** ~400 candidate stops, joint selection + ordering, provably better solver, real road-network routing, interactive map output.

---

## Constraints

- Candidate pool: all NPS units with valid coordinates in the contiguous US
- Must visit at least one NPS unit per contiguous state (48 states)
- Minimize total driving time
- Loop route (no fixed start/end — start anywhere on the loop)
- Road-network distances only (no straight-line / haversine)

---

## Stack

| Component | Tool |
|---|---|
| Language | Python 3.11+ |
| Database | Neon (PostGIS) |
| Routing engine | OSRM (Docker on BRONTOSAURUS, North America extract) |
| Solver | Google OR-Tools |
| Visualization | Folium |
| Data source | NPS API + Census TIGER state boundaries |

---

## Schema

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE pois (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'nps',       -- nps | osm | amtrak (future)
    category TEXT,                             -- national_park, monument, historic_site, etc.
    state CHAR(2),                            -- derived via spatial join
    geom GEOMETRY(Point, 4326) NOT NULL,
    tags JSONB DEFAULT '{}'::jsonb,           -- source-specific metadata
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pois_geom ON pois USING GIST (geom);
CREATE INDEX idx_pois_source ON pois (source);
CREATE INDEX idx_pois_state ON pois (state);
CREATE INDEX idx_pois_category ON pois (category);
```

This schema is designed to grow. Tier 2 adds OSM and Amtrak rows to the same table.

---

## Build Phases

### Phase 1 — Data (Day 1)

1. Pull all NPS units from NPS API (`/api/v1/parks`)
2. Parse lat/lon, name, designation, state codes
3. Insert into `pois` table with `source = 'nps'`
4. Download Census TIGER state boundaries shapefile
5. Spatial join: assign `state` field via `ST_Contains` against state polygons
6. **Validation gate:** confirm every contiguous state has ≥1 NPS unit. If any state is missing, flag it before proceeding.

### Phase 2 — Routing Engine (Day 2)

1. Pull North America OSM extract (Geofabrik `.osm.pbf`)
2. Process with `osrm-backend` Docker container (`osrm-extract`, `osrm-partition`, `osrm-customize`)
3. Stand up OSRM HTTP API on localhost
4. Write Python script to generate N×N driving time + distance matrix using OSRM `/table` endpoint
5. Batch requests (OSRM table service handles ~100 sources per request; batch 400 points into chunks)
6. Cache matrix to disk (pickle or parquet) — this is the expensive computation

### Phase 3 — Solver (Days 2–3)

1. Formulate as constrained TSP using OR-Tools routing library
2. Decision variables: which NPS units to visit + visit order
3. Constraint: at least one selected unit per contiguous state
4. Objective: minimize total driving time across the loop
5. This is a **set cover + TSP hybrid** — OR-Tools handles this via the vehicle routing problem (VRP) model with disjunctions
6. Output: ordered list of selected stops with per-leg drive time and distance

**Prioritization in Tier 1:** Since every state must be covered and there is no time budget, the solver's only decision when multiple NPS units exist in a state is which one minimizes total drive time. All stops are weighted equally — pure geographic efficiency. This is intentional: Tier 1 is a direct methodological comparison to Olson, where stop quality was a human editorial decision, not an algorithmic one. Category-weighted prioritization is introduced in Tier 2.

### Phase 4 — Visualization (Days 3–4)

1. For each consecutive pair of stops, fetch OSRM `/route` geometry (actual road polyline)
2. Build Folium map:
   - Route polylines (real roads, not straight lines)
   - Numbered markers at each stop with popups (name, NPS designation, state)
   - Summary stats panel: total miles, total drive hours, number of stops, states covered
3. Export as standalone `.html` file
4. Optional: static PNG export for blog/social via `selenium` screenshot of the Folium map

### Phase 5 — Writeup (Days 4–5)

1. Methodology section: data source, routing approach, solver formulation
2. Results comparison vs. Olson (miles, stops, coverage logic)
3. Interactive map embed
4. Link to GitHub repo
5. Format as blog post (Markdown → publishable)

---

## Comparison Framework (Optitrek vs. Olson)

| Dimension | Olson (2015) | Optitrek Tier 1 |
|---|---|---|
| Candidate pool | ~50 hand-picked | ~400 (full NPS catalog) |
| Stop selection | Manual | Algorithmic (set cover) |
| Stop ordering | Genetic algorithm | OR-Tools (near-optimal) |
| Routing data | Google Maps API | OSRM (open, self-hosted) |
| Distance type | Drive time only | Drive time + distance |
| State coverage | All 48 (manual) | All 48 (constrained) |
| Route type | Loop | Loop |
| Output | Static map image | Interactive Folium map |

---

## Known Risks

- **OSRM setup time:** North America extract is ~10GB processed. Download + processing may take several hours on first run.
- **Matrix computation:** 400×400 = 160,000 pairs. Batching required. Estimate ~10–20 min total with local OSRM.
- **Solver complexity:** Joint selection + ordering is harder than pure TSP. OR-Tools should handle it but may need tuning (time limits, metaheuristic strategy).
- **NPS coverage gap:** If any contiguous state lacks an NPS unit, we either relax the constraint for that state or manually add one waypoint. Unlikely but check in Phase 1.

---

## Out of Scope (Tier 1)

- OSM POI data (Tier 2)
- Amtrak integration (Tier 2)
- Daily driving limits / overnight stops (Tier 2)
- User-configurable constraints (Tier 2)
- Web app / frontend (Tier 3)
- FastAPI backend (Tier 3)
- User accounts, saved trips (Tier 3)

---

## Repo Structure

```
optitrek/
├── README.md
├── OPTITREK-TIER1-PROJECT-DOC.md
├── data/
│   ├── nps_raw/           # raw API responses
│   ├── matrix/            # cached distance/time matrices
│   └── boundaries/        # Census TIGER shapefiles
├── src/
│   ├── data_pull.py       # NPS API fetch + PostGIS load
│   ├── spatial_join.py    # state assignment
│   ├── matrix_builder.py  # OSRM N×N matrix generation
│   ├── solver.py          # OR-Tools constrained TSP
│   └── visualize.py       # Folium map builder
├── output/
│   └── optitrek_map.html  # final interactive map
├── docker-compose.yml     # OSRM container config
└── requirements.txt
```

---

## Next Step

Build this in Claude Code on BRONTOSAURUS. Start with Phase 1.

After Tier 1 is complete and the blog post is shipped, execute the database expansion (see OPTITREK-DATABASE-EXPANSION-SPEC.md) before starting Tier 2 solver work.
