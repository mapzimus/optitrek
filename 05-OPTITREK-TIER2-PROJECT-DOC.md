# Optitrek — Tier 2 Project Doc

## Overview

Expand Optitrek from the Tier 1 NPS-only proof of concept into a configurable road trip optimizer. Users select POI categories, states, driving constraints, and must-see stops via a web form. The solver filters from a ~100k POI database, computes optimal routing, and returns an interactive map split into daily driving legs.

**Tier 1 result:** Fixed NPS-only loop across all 48 states, single output.
**Prerequisite:** Database expansion complete (see OPTITREK-DATABASE-EXPANSION-SPEC.md). Full ~100k POI database with OSM, Amtrak, and overnight cities already loaded and validated.
**Tier 2 target:** User-configurable categories, states, daily limits, and must-include stops. Web UI with shareable map output.

---

## Architecture

```
[User Browser]
    │
    ▼
[Railway: FastAPI app]
    │
    ├──► [Neon: PostGIS] ──── POI queries
    │
    └──► [BRONTOSAURUS: OSRM] ──── distance matrices + route geometries
              (via Cloudflare Tunnel)
```

- **FastAPI app** hosted on Railway handles user input, database queries, solver orchestration, and map serving.
- **Neon PostGIS** stores the full POI catalog (~100k points). Same database and schema as Tier 1, expanded with OSM data.
- **OSRM** runs in Docker on BRONTOSAURUS, exposed via Cloudflare Tunnel. Handles distance matrix computation and route geometry requests.

---

## What's New vs. Tier 1

| Dimension | Tier 1 | Tier 2 |
|---|---|---|
| POI pool | ~400 NPS units | ~100k (NPS + OSM) |
| Categories | NPS only | Museums, zoos, stadiums, historic sites, nature, etc. |
| Configuration | Hardcoded | User-configurable via web form |
| State coverage | All 48 required | User selects subset or all |
| Daily limits | None | Max hours/day with leg splitting |
| Must-include stops | None | User pins specific POIs |
| Output | Standalone HTML file | Hosted web app with embedded map |
| Interface | Script | FastAPI + HTML form |
| Hosting | Local only | Railway + BRONTOSAURUS OSRM |

---

## Stack

| Component | Tool |
|---|---|
| Language | Python 3.11+ |
| Database | Neon (PostGIS) — same instance as Tier 1 |
| Routing engine | OSRM (Docker on BRONTOSAURUS via Cloudflare Tunnel) |
| Solver | Google OR-Tools |
| Visualization | Folium |
| Backend | FastAPI |
| Frontend | Single-page HTML form (vanilla JS) |
| App hosting | Railway |
| Data sources | NPS API + OSM Overpass API + Census TIGER boundaries |

---

## Data Expansion

The full data expansion (OSM tourist attractions, Amtrak GTFS, overnight cities, category taxonomy finalization) is executed as a standalone task before Tier 2 begins. See **OPTITREK-DATABASE-EXPANSION-SPEC.md** for the complete spec covering Overpass extraction, deduplication, GTFS parsing, filtering rules, and validation criteria.

By the time Tier 2 starts, the `pois` table contains ~100,000+ rows across all sources (NPS, OSM, Amtrak, overnight cities), fully deduplicated and validated. Tier 2 work begins with solver generalization, not data engineering.

### Schema

No schema changes from Tier 1. Same `pois` table, more rows. The only new table is `amtrak_legs` (created during the database expansion) for station-to-station travel times.

---

## Solver Configuration

The Tier 1 solver is refactored to accept a config object:

```python
@dataclass
class TripConfig:
    categories: list[str]           # POI categories to include
    category_priority: dict[str, int]  # priority ranking per category (e.g., {"national_park": 5, "museum": 3})
    states: list[str]               # states to cover (default: all 48 contiguous)
    start_state: str | None         # optional fixed start state
    max_hours_per_day: float        # daily driving cap (e.g., 6.0)
    total_trip_days: int | None     # total trip length in days (time budget)
    max_radius_miles: float | None  # optional max distance from start point
    must_include: list[int]         # specific POI IDs that must appear in route
    max_stops: int | None           # upper bound on total stops
    loop: bool                      # True = return to start, False = point-to-point
```

### Solver Objective: Two Modes

**Mode 1 — Cover all states (Tier 1 behavior):**
When `total_trip_days` is not set and `states` includes all 48, the solver minimizes total drive time to cover every state. All stops are weighted equally — the solver picks whichever NPS unit per state makes the loop shortest. This is the Olson replication mode.

**Mode 2 — Time-budgeted trip (new in Tier 2):**
When `total_trip_days` is set, the solver has a finite time budget (`total_trip_days × max_hours_per_day`). The objective flips: instead of minimizing time to cover all states, it **maximizes total priority score within the time budget**.

The priority score for a route is `sum(category_priority[stop.category] for stop in selected_stops)`. The user ranks their selected categories by importance (e.g., national parks = 5, museums = 3, stadiums = 1), and the solver favors higher-priority stops when it can't visit everything.

**Tiebreaker:** When two candidate routes have equal priority scores, the solver selects the shorter route (less total drive time). This means geographic efficiency is always the secondary objective — the solver never picks a longer route when a shorter one scores the same.

If `max_radius_miles` is also set, the candidate pool is pre-filtered to POIs within that radius of the start point before the solver runs. This is an optional additional constraint for users who want to stay regional.

### Prioritization UX

The web form presents category priority as a simple drag-and-drop ranking or numbered dropdown alongside each category checkbox. When the user selects categories, they also rank them. Default ranking assigns equal weight to all selected categories, which reduces to "maximize stop count" (Tier 2 fallback behavior).

### Solver Flow

1. Query PostGIS for POIs matching `categories` within `states`
2. If `max_radius_miles` is set, further filter to POIs within radius of start point (`ST_DWithin`)
3. If `must_include` POIs are specified, ensure they're in the candidate set
4. Assign each candidate its `category_priority` weight from the config
5. Compute distance matrix via OSRM for filtered candidate set (typically 200–2,000 points)
6. Run OR-Tools constrained TSP/VRP:
   - **If time-budgeted:** maximize total priority score within driving hour budget; tiebreaker is shortest total drive time. At least one stop per selected state where feasible.
   - **If cover-all-states:** minimize total drive time, at least one stop per selected state (mandatory). All stops weighted equally (priority scoring not used).
   - Must-include stops are mandatory in both modes
   - Respect `max_stops` if set
7. Partition solved route into daily legs based on `max_hours_per_day`
8. Generate Folium map with day-colored route segments

### Daily Leg Splitting

Simple approach: walk the ordered stop list, accumulate drive time per leg, cut to a new day when the cap is reached. Each day starts where the previous day ended.

No overnight location optimization — just label the splits. Tier 3 adds smart overnight suggestions.

---

## Build Phases

**Note:** The OSM data pull, Amtrak GTFS ingestion, overnight cities, and taxonomy finalization are completed in the standalone database expansion (see OPTITREK-DATABASE-EXPANSION-SPEC.md) before Tier 2 begins. Tier 2 starts with the full ~100k POI database already loaded and validated.

### Phase 1 — Generalize Solver (Days 1–2)

1. Refactor Tier 1 solver to accept `TripConfig` dataclass
2. Add category and state filtering to POI query
3. Add `max_radius_miles` spatial pre-filter (`ST_DWithin` from start point)
4. Implement two solver modes: cover-all-states (minimize time) vs. time-budgeted (maximize weighted priority score within budget, tiebreaker = shortest distance)
5. Add category priority weighting to solver objective
6. Add must-include constraint to OR-Tools model
7. Add max_stops constraint
8. Implement daily leg splitting as post-processing step
9. Add loop vs. point-to-point option
10. **Test with Tier 1 config** — results should match or improve on Tier 1 output

### Phase 2 — Matrix Computation Strategy (Day 2)

1. New flow: config → filter POIs → compute matrix for filtered subset → solve
2. Matrix size depends on filter result (200–2,000 points typical)
3. OSRM batching logic from Tier 1, parameterized for variable-size inputs
4. Add timing/progress logging (matrix computation is the bottleneck)
5. Consider caching matrices by config hash for repeat queries

### Phase 3 — FastAPI Backend (Days 3–4)

1. `POST /solve` — accepts config JSON, returns job ID
2. `GET /status/{job_id}` — poll for completion
3. `GET /result/{job_id}` — returns map HTML
4. Async job execution (matrix + solver take minutes)
5. Health check endpoint verifying OSRM and Neon connectivity
6. CORS config for frontend

### Phase 4 — Web UI (Days 4–5)

Single-page HTML form with:

- **Category checkboxes** — select which POI types to include
- **Category priority ranking** — drag-and-drop or numbered dropdown to rank selected categories by importance (only active when time-budgeted mode is selected; default: equal weight)
- **State selection** — multi-select dropdown or clickable map, default "all 48"
- **Start state dropdown** — optional, default "optimize from anywhere"
- **Total trip days** — slider, 1–90 days, or "no limit" (cover-all-states mode)
- **Max driving hours/day** — slider, 4–12 hours, default 6
- **Max radius from start** — optional, miles slider, for staying regional
- **Must-include stops** — search/autocomplete against POI names in database
- **Max stops** — slider, 10–200, default "no limit"
- **Loop toggle** — loop vs. point-to-point

Submit → loading spinner with progress updates → embedded Folium map result.

### Phase 5 — Deploy (Days 5–6)

1. FastAPI app → Railway
2. OSRM → Docker on BRONTOSAURUS behind Cloudflare Tunnel
3. Neon PostGIS — no changes needed
4. End-to-end test: form submit → POI filter → matrix build → solve → map render
5. Verify Cloudflare Tunnel stability under load

---

## Repo Structure Additions

```
optitrek/
├── src/
│   ├── data_pull.py         # NPS API fetch (Tier 1)
│   ├── osm_pull.py          # Overpass bulk extract + dedup (NEW)
│   ├── spatial_join.py      # state assignment (Tier 1)
│   ├── matrix_builder.py    # OSRM matrix generation (generalized)
│   ├── solver.py            # OR-Tools solver (generalized with TripConfig)
│   ├── config.py            # TripConfig dataclass (NEW)
│   ├── visualize.py         # Folium map builder (updated for daily legs)
│   ├── app.py               # FastAPI endpoints (NEW)
│   └── templates/
│       └── index.html       # web form UI (NEW)
├── trips/                   # example configs (NEW)
│   ├── all_nps_loop.yaml
│   ├── northeast_museums.yaml
│   └── west_coast_nature.yaml
├── data/
│   ├── nps_raw/
│   ├── osm_raw/             # raw Overpass responses (NEW)
│   ├── matrix/
│   └── boundaries/
├── output/
├── docker-compose.yml
├── requirements.txt
├── OPTITREK-TIER1-PROJECT-DOC.md
├── OPTITREK-TIER2-PROJECT-DOC.md
└── README.md
```

---

## Known Risks

- **Overpass API rate limits:** Bulk extraction of ~500 queries needs throttling (1 request/second, retry on 429). Budget 1–2 hours for full pull.
- **OSM data quality:** Some POIs will have missing names, wrong coordinates, or miscategorized tags. Build in validation and allow manual corrections.
- **Matrix computation time:** 2,000 × 2,000 = 4M pairs. Even with local OSRM, this may take 10–30 minutes. Users need clear progress feedback.
- **Solver scaling:** OR-Tools handles thousands of nodes but solution quality depends on time limits and metaheuristic strategy. May need to tune per config size.
- **Cloudflare Tunnel reliability:** BRONTOSAURUS must be on and tunnel must be active for OSRM access. If tunnel drops, app returns clear error.
- **Deduplication imperfection:** Some NPS/OSM overlaps will slip through fuzzy matching. Acceptable for Tier 2 — manual curation is Tier 3.

---

## Out of Scope (Tier 2)

- Amtrak / multi-modal routing (Tier 3)
- User accounts, saved trips (Tier 3)
- Overnight stop suggestions with lodging data (Tier 3)
- Cost estimates (gas, lodging) (Tier 3)
- Complex daily planning beyond hour caps (Tier 3)
- "What if" comparisons (Tier 3)
- Seasonal filters / road closures (Tier 3)

---

## Next Step

Complete Tier 1 build first. Then execute the database expansion (OPTITREK-DATABASE-EXPANSION-SPEC.md). Then execute this spec starting with Phase 1 (generalize solver).
