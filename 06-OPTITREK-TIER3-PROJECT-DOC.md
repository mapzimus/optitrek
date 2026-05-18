# Optitrek — Tier 3 Project Doc

## Overview

Optitrek Tier 3 is the full product: a web application where users build, customize, share, and discover optimized road trips across the United States. It adds multi-modal routing (Amtrak), user accounts with saved trips and preferences, a community gallery, auto-generated preset collections, overnight stop suggestions, "what if" re-solving, and seasonal awareness.

**Tier 2 result:** Configurable solver with web form UI, ~100k POIs, daily leg splitting.
**Tier 3 target:** Full-featured trip planner with accounts, sharing, community, presets, Amtrak, and overnight suggestions.

---

## Architecture

```
[User Browser]
    │
    ▼
[Railway: FastAPI app + Clerk auth]
    │
    ├──► [Neon: PostGIS] ──── POIs, users, trips, collections, seasonal notes
    │
    └──► [BRONTOSAURUS: OSRM] ──── distance matrices + route geometries
              (via Cloudflare Tunnel)
```

Same infrastructure as Tier 2. New additions are database tables (users, trips, collections) and Clerk integration for auth.

---

## What's New vs. Tier 2

| Dimension | Tier 2 | Tier 3 |
|---|---|---|
| Amtrak | Not available | Optional train legs (user-selected or solver-suggested) |
| User accounts | None | Clerk auth, saved trips, preferences |
| Sharing | None | Shareable trip URLs |
| Community | None | Public gallery of user-created trips |
| Presets | None | Auto-generated collections from Wikidata (MLB stadiums, etc.) |
| Overnight stops | Day labels only | City suggestions near each day's endpoint |
| What-if | Re-submit form | Modify a stop and re-solve inline |
| Seasonal notes | None | Static metadata on POIs (winter closures, best seasons) |

---

## Stack

| Component | Tool |
|---|---|
| Language | Python 3.11+ |
| Database | Neon (PostGIS) — expanded schema |
| Routing engine | OSRM (Docker on BRONTOSAURUS via Cloudflare Tunnel) |
| Solver | Google OR-Tools |
| Visualization | Folium |
| Backend | FastAPI |
| Frontend | HTML/JS (vanilla or lightweight framework) |
| Auth | Clerk |
| App hosting | Railway |
| Amtrak data | GTFS static feed |
| Collection tagging | Wikidata SPARQL + OSM Wikidata IDs |

---

## Feature Specs

### 1. Amtrak Integration

**Data:**
- Load Amtrak GTFS static feed into PostGIS
- ~500 stations as POI entries with `source = 'amtrak'`, `category = 'train_station'`
- Parse GTFS `stop_times.txt` and `trips.txt` for station-to-station travel times and schedules

**Three user modes:**

| Mode | Behavior |
|---|---|
| No trains | Default. Road-only routing, same as Tier 2. |
| Trains only between A and B | User specifies two cities. Solver treats that segment as a fixed train leg with GTFS travel time. Driving legs connect to/from the nearest stations. |
| Open to trains | Solver evaluates whether any leg would be faster by train than by car. If a train leg saves time and the stations are near existing stops, it gets suggested. User can accept or reject. |

**Solver changes:**
- Train legs are modeled as fixed-cost edges in the routing graph
- For "open to trains" mode: pre-filter Amtrak station pairs where train time < drive time, inject as optional edges, let OR-Tools decide
- Route output distinguishes drive segments (blue) from train segments (a different color) on the map

### 2. User Accounts (Clerk)

**Auth:**
- Clerk handles signup/login (email, Google, Apple)
- Clerk user ID maps to internal `users` table in Neon

**User data:**

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    clerk_id TEXT UNIQUE NOT NULL,
    display_name TEXT,
    home_state CHAR(2),
    default_categories TEXT[],        -- preferred POI categories
    default_max_hours_per_day FLOAT,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**Preferences:**
- Home state (default start point)
- Favorite categories (pre-checked on form)
- Default daily driving limit
- Preferences auto-populate the trip config form but can be overridden per trip

### 3. Saved Trips

```sql
CREATE TABLE trips (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    config JSONB NOT NULL,            -- full TripConfig as JSON
    result JSONB NOT NULL,            -- ordered stops, legs, daily splits
    map_html TEXT,                    -- cached Folium output
    is_public BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_trips_user ON trips (user_id);
CREATE INDEX idx_trips_public ON trips (is_public) WHERE is_public = true;
```

- Users save any solved trip to their account
- Toggle public/private per trip
- Public trips appear in the community gallery

### 4. Sharing

- Each saved trip gets a unique URL: `optitrek.com/trip/{trip_id}`
- Public trips are viewable by anyone (no auth required to view)
- Shared trip page shows: interactive map, stop list, daily breakdown, config summary
- "Clone this trip" button: copies config into the solver form for modification

### 5. Community Gallery

- Browse public trips by: most recent, most cloned, category tags, region
- Each trip card shows: title, stop count, total miles, daily count, creator name, mini map thumbnail
- Search/filter by states covered, categories included, trip length
- Featured trips section (manually curated or algorithmically surfaced)

### 6. Preset Collections (Wikidata Auto-Tagging)

**Data pipeline:**

1. For OSM POIs that have a `wikidata` tag in their JSONB metadata, query Wikidata SPARQL for structured properties
2. Key Wikidata properties:
   - `P31` (instance of): identifies venue type (baseball stadium, art museum, etc.)
   - `P118` (league): identifies sports league (MLB, NFL, NHL, etc.)
   - `P127` (owned by) / `P466` (occupant): identifies tenant teams
   - `P17` (country), `P131` (located in): geographic filtering
3. Auto-generate collections from query results

**Example auto-generated collections:**
- All MLB Stadiums
- All NFL Stadiums
- All NHL Arenas
- All NBA Arenas
- All MLS Stadiums
- Ivy League Campuses
- Smithsonian Museums
- National Memorials
- Civil War Battlefields
- Route 66 Landmarks

```sql
CREATE TABLE collections (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    source TEXT NOT NULL DEFAULT 'wikidata',  -- wikidata | manual | user
    is_curated BOOLEAN DEFAULT false,
    created_by INTEGER REFERENCES users(id),  -- NULL for auto-generated
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE collection_pois (
    collection_id INTEGER REFERENCES collections(id),
    poi_id INTEGER REFERENCES pois(id),
    PRIMARY KEY (collection_id, poi_id)
);

CREATE INDEX idx_collection_pois_col ON collection_pois (collection_id);
CREATE INDEX idx_collection_pois_poi ON collection_pois (poi_id);
```

**User interaction with presets:**
- Browse collections, select one as trip basis
- Modify before solving: exclude specific stops, add categories, change constraints
- Example: "All MLB Stadiums" → exclude Fenway → add theme parks → max 8 hours/day → solve

**Error reporting:**
- Users can flag incorrect collection memberships ("this isn't an MLB stadium")
- Flagged items queued for manual review

### 7. Overnight Stop Suggestions

**Data source:**
- OSM `place=city` and `place=town` nodes with population data
- Filter to places with population > 5,000 (proxy for "has lodging")
- Store in `pois` table with `source = 'osm'`, `category = 'overnight_city'`

**Logic:**
- After daily leg splitting, find the nearest qualifying city/town to each day's final stop
- Present as suggestion: "End of Day 3: near Springfield, IL (pop. 114,000)"
- Not a routing constraint — just informational
- Map shows overnight city markers in a distinct style

### 8. What-If Re-Solving

**UX flow:**
1. User views a solved trip
2. Clicks "Add a stop" → search/autocomplete → select POI
3. Or clicks "Remove this stop" on an existing stop
4. App re-submits modified config to solver
5. New result replaces old one (or shown side-by-side if we get ambitious)
6. Summary shows delta: "+45 miles, +1 stop, same number of days"

**Implementation:**
- Frontend sends modified config to same `POST /solve` endpoint
- No new solver logic needed — just re-solve with updated must-include/exclude list
- Delta computation is simple math on the two result objects

### 9. Seasonal Notes

**Data model:**
- Add `seasonal_notes` field to `pois` table:

```sql
ALTER TABLE pois ADD COLUMN seasonal_notes JSONB DEFAULT NULL;
```

- Example value:
```json
{
    "closure": "Nov-Apr",
    "best_season": "Jun-Sep",
    "note": "Going-to-the-Sun Road typically closed November through April"
}
```

**Data sources:**
- NPS API includes operating hours and seasonal info for many units
- Manual additions for well-known closures (mountain passes, northern parks)
- No live data — static metadata only

**User-facing:**
- Trip config includes optional "travel month" selector
- If set, solver warns (but doesn't exclude) about stops with seasonal closures during that month
- Map popups show seasonal notes where available
- Trip summary lists any seasonal warnings

### 10. Stop Prioritization (Tier 3 Enhancements)

Tier 3 extends the category-weighted priority scoring introduced in Tier 2 with additional signals:

**Priority stack (highest to lowest):**

1. **Must-include stops** — infinite priority, always in the route regardless of time budget
2. **Preset collection stops** — if the user selected a preset collection (e.g., "All MLB Stadiums"), those stops receive the highest category weight automatically
3. **User-starred stops** — during the what-if flow, users can boost individual POIs, elevating them above their category's default weight
4. **Category priority ranking** — inherited from Tier 2, user ranks their selected categories by importance
5. **Geographic efficiency** — tiebreaker when two candidate routes have equal priority scores; shorter route wins

**Community popularity signal (optional, low weight):**
Stops that appear frequently in shared public trips could receive a small popularity bonus. This is deliberately low-weight to avoid herding everyone toward the same stops. Implementation: count appearances in public trips, normalize to a 0–1 scale, multiply by a small coefficient (e.g., 0.1). This feature is optional and can be toggled off if it produces uninteresting results.

**Solver objective in Tier 3 time-budgeted mode:**
`maximize sum(effective_priority[stop] for stop in selected_stops)` subject to total drive time ≤ budget, where `effective_priority` combines category weight + collection membership + user star + popularity signal. Tiebreaker remains shortest total drive time.

---

## Build Phases

### Phase 1 — Amtrak Data + Routing (Days 1–3)

1. Download Amtrak GTFS static feed
2. Parse stations → insert into `pois` table with `source = 'amtrak'`
3. Parse `stop_times.txt` + `trips.txt` → build station-to-station travel time lookup
4. Store as `amtrak_legs` table:

```sql
CREATE TABLE amtrak_legs (
    id SERIAL PRIMARY KEY,
    origin_station_id INTEGER REFERENCES pois(id),
    destination_station_id INTEGER REFERENCES pois(id),
    travel_time_minutes INTEGER NOT NULL,
    route_name TEXT,
    frequency TEXT                    -- e.g., "daily", "3x/week"
);

CREATE INDEX idx_amtrak_origin ON amtrak_legs (origin_station_id);
CREATE INDEX idx_amtrak_dest ON amtrak_legs (destination_station_id);
```

5. Modify solver to accept train leg edges
6. Implement three Amtrak modes (none / fixed A-B / open to suggestion)
7. Update Folium visualization to color-code train vs. drive segments

### Phase 2 — User Accounts + Saved Trips (Days 3–5)

1. Integrate Clerk SDK with FastAPI
2. Create `users` table, map Clerk IDs
3. Build preferences form (home state, default categories, daily limit)
4. Create `trips` table
5. Add save/load/delete trip endpoints
6. Add public/private toggle
7. Generate shareable URLs for public trips
8. Build "My Trips" dashboard page

### Phase 3 — Community Gallery (Days 5–7)

1. Build gallery page: grid of public trip cards
2. Trip card: title, stop count, miles, days, creator, mini map thumbnail
3. Sort options: recent, most cloned, trip length
4. Filter by: states, categories, trip length range
5. Search by trip name or creator
6. "Clone this trip" button → pre-fills solver form with trip's config
7. Clone counter on each trip

### Phase 4 — Preset Collections (Days 7–9)

1. Identify OSM POIs with Wikidata IDs in `tags` JSONB
2. Write SPARQL queries against Wikidata for target properties (P31, P118, etc.)
3. Match Wikidata results back to OSM POIs
4. Auto-generate `collections` and `collection_pois` entries
5. Build collection browser UI
6. Add "use as trip basis" flow: select collection → modify constraints → solve
7. Add error reporting: "flag this POI as incorrect for this collection"
8. Seed ~10–20 collections for launch

### Phase 5 — Overnight Stops + Seasonal Notes (Days 9–10)

1. Extract OSM cities/towns with population > 5,000
2. Insert as `category = 'overnight_city'` in `pois` table
3. After leg splitting, nearest-city lookup via PostGIS `ST_DWithin` or `ST_Distance`
4. Add overnight suggestions to trip result JSON and map output
5. Parse NPS API seasonal/hours data into `seasonal_notes` JSONB on relevant POIs
6. Add travel month selector to trip config form
7. Surface seasonal warnings in trip summary and map popups

### Phase 6 — What-If Re-Solving (Days 10–11)

1. Add "Add a stop" and "Remove stop" buttons to trip result view
2. Search/autocomplete for adding stops
3. Re-submit modified config to solver
4. Compute delta (miles, stops, days) between old and new result
5. Display delta summary before user confirms the change

### Phase 7 — Polish + Deploy (Days 11–14)

1. UI polish: consistent styling, mobile responsiveness, loading states
2. Error handling: OSRM down, solver timeout, empty results
3. Rate limiting on solve endpoint (solver is expensive)
4. Gallery thumbnail generation (static screenshot of Folium map)
5. End-to-end testing: full user flow from signup → configure → solve → save → share → clone
6. Deploy to Railway
7. Verify Cloudflare Tunnel stability

---

## Full Schema Summary

```sql
-- Tier 1 (unchanged)
CREATE TABLE pois (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'nps',
    category TEXT,
    state CHAR(2),
    geom GEOMETRY(Point, 4326) NOT NULL,
    tags JSONB DEFAULT '{}'::jsonb,
    seasonal_notes JSONB DEFAULT NULL,        -- Tier 3 addition
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Tier 3 additions
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    clerk_id TEXT UNIQUE NOT NULL,
    display_name TEXT,
    home_state CHAR(2),
    default_categories TEXT[],
    default_max_hours_per_day FLOAT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE trips (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    config JSONB NOT NULL,
    result JSONB NOT NULL,
    map_html TEXT,
    is_public BOOLEAN DEFAULT false,
    clone_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE amtrak_legs (
    id SERIAL PRIMARY KEY,
    origin_station_id INTEGER REFERENCES pois(id),
    destination_station_id INTEGER REFERENCES pois(id),
    travel_time_minutes INTEGER NOT NULL,
    route_name TEXT,
    frequency TEXT
);

CREATE TABLE collections (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    source TEXT NOT NULL DEFAULT 'wikidata',
    is_curated BOOLEAN DEFAULT false,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE collection_pois (
    collection_id INTEGER REFERENCES collections(id),
    poi_id INTEGER REFERENCES pois(id),
    PRIMARY KEY (collection_id, poi_id)
);
```

---

## Repo Structure (Final)

```
optitrek/
├── src/
│   ├── data_pull.py           # NPS API fetch
│   ├── osm_pull.py            # Overpass bulk extract + dedup
│   ├── amtrak_pull.py         # GTFS parsing + station loading (NEW)
│   ├── wikidata_tagger.py     # SPARQL queries + collection generation (NEW)
│   ├── spatial_join.py        # state assignment
│   ├── matrix_builder.py      # OSRM matrix generation
│   ├── solver.py              # OR-Tools solver (with Amtrak edges)
│   ├── config.py              # TripConfig dataclass
│   ├── overnight.py           # nearest-city lookup for overnight stops (NEW)
│   ├── seasonal.py            # seasonal notes parsing + warnings (NEW)
│   ├── visualize.py           # Folium map builder (train segments, overnights)
│   ├── app.py                 # FastAPI endpoints (expanded)
│   ├── auth.py                # Clerk integration (NEW)
│   ├── gallery.py             # community gallery queries (NEW)
│   └── templates/
│       ├── index.html         # trip config form
│       ├── result.html        # trip result + map view
│       ├── gallery.html       # community gallery (NEW)
│       ├── my_trips.html      # user's saved trips (NEW)
│       ├── collections.html   # preset collection browser (NEW)
│       └── profile.html       # user preferences (NEW)
├── trips/
├── data/
│   ├── nps_raw/
│   ├── osm_raw/
│   ├── amtrak_gtfs/           # raw GTFS files (NEW)
│   ├── matrix/
│   └── boundaries/
├── output/
├── docker-compose.yml
├── requirements.txt
├── OPTITREK-TIER1-PROJECT-DOC.md
├── OPTITREK-TIER2-PROJECT-DOC.md
├── OPTITREK-TIER3-PROJECT-DOC.md
└── README.md
```

---

## Known Risks

- **Amtrak GTFS complexity:** Amtrak schedules are irregular (some routes 3x/week, varying travel times). Simplification needed — use median travel time per station pair, flag infrequent routes.
- **Wikidata coverage gaps:** Not all OSM POIs have Wikidata IDs. Auto-generated collections will be incomplete. User error reports and manual curation fill gaps over time.
- **Solver performance with train edges:** Adding optional Amtrak edges increases the routing graph complexity. May need to pre-filter to only relevant station pairs (where train time < 1.5× drive time) to keep solver tractable.
- **Gallery spam/abuse:** Public trips need basic moderation. Start with manual review; add automated checks if volume grows.
- **Map thumbnail generation:** Static screenshots of Folium maps require headless browser (Selenium/Playwright). Adds a dependency and processing time per saved trip.
- **Scope creep:** This spec is already large. Phases are ordered by dependency — ship Amtrak + accounts first, gallery and presets can follow incrementally.

---

## Out of Scope (Tier 3)

- Live road closure data
- Real-time gas prices
- Hotel booking integration
- Restaurant recommendations
- Multi-vehicle / caravan routing
- International routing (Canada, Mexico)
- Native mobile app

---

## Future Considerations

- **Expand to Canada/Mexico** if the US version gets traction
- **API access** for third-party integrations
- **Premium tier** if hosting costs grow (solver compute is the expensive part)
- **Mobile app** wrapper if web usage justifies it

---

## Next Step

Complete Tier 1, the database expansion (OPTITREK-DATABASE-EXPANSION-SPEC.md), and Tier 2 builds first. This spec is the design doc — it sits on the shelf until Tier 2 is live and validated.
