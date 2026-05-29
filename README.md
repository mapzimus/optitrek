# Optitrek

An algorithmic road-trip optimizer for the United States. A 2026 redo of Randal Olson's 2015 "optimal road trip" project — with a 438-stop candidate pool of National Park Service units (Tier 1, NPS-only; planned DB expansion to ~100,000 stops via OSM + Amtrak + overnight cities for Tier 2 is on the roadmap but not yet built), a real constrained optimizer (OR-Tools), open-source self-hosted routing (OSRM), and an interactive web map (Folium).

## Planning documents

The project is defined by 8 docs in this folder. Read in order:

1. [`01-OPTITREK-PROJECT-BLUEPRINT.md`](01-OPTITREK-PROJECT-BLUEPRINT.md) — master overview
2. [`02-OPTITREK-OLSON-COMPARISON.md`](02-OPTITREK-OLSON-COMPARISON.md) — what Olson did, what we improve
3. [`03-OPTITREK-TIER1-PROJECT-DOC.md`](03-OPTITREK-TIER1-PROJECT-DOC.md) — Tier 1 build spec (where we are now)
4. [`04-OPTITREK-DATABASE-EXPANSION-SPEC.md`](04-OPTITREK-DATABASE-EXPANSION-SPEC.md) — DB expansion (between Tier 1 and Tier 2)
5. [`05-OPTITREK-TIER2-PROJECT-DOC.md`](05-OPTITREK-TIER2-PROJECT-DOC.md) — Tier 2 build spec
6. [`06-OPTITREK-TIER3-PROJECT-DOC.md`](06-OPTITREK-TIER3-PROJECT-DOC.md) — Tier 3 build spec
7. [`07-OPTITREK-DECISION-LOG.md`](07-OPTITREK-DECISION-LOG.md) — 19 planning decisions
8. [`08-OPTITREK-GAP-AUDIT.md`](08-OPTITREK-GAP-AUDIT.md) — 22 gaps with proposed resolutions

Locked decisions tracked in [`DECISIONS.md`](DECISIONS.md).

## Status

**Tier 1 is complete.** Pipeline runs end-to-end: 49 stops covering 49 zones (48
contiguous states + DC), **193.0 h / 9,744 mi**, beating Olson 2015 by 13.8% time
and 28.9% miles. Two interactive Folium maps in `output/`.

**Tier 2 (config-driven trips)** is also complete. Trip authors describe what they
want in YAML (`trips/*.yaml`) — filters by state, category, radius, force-include
specific POIs, set max stops, cap drive hours per day, choose loop vs open path,
and (per `DECISIONS.md` D5) opt into cross-border US+Canada routing for trips
where Canadian highways are genuinely faster.

See [`BUILD_STATUS.md`](BUILD_STATUS.md) for the live state of the world.
[`HANDOVER.md`](HANDOVER.md) is the original 2026-05-18 handover, kept for
historical context.

## Quickstart

Run from the repo root (wherever it lives — paths inside the scripts are
relative to `src/`, so the project is portable).

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env                      # paste NPS_API_KEY and DATABASE_URL
python -m src.data_pull           # Phase 1A — NPS API → PostGIS
python -m src.spatial_join        # Phase 1B — state assignment + coverage gate
```

**Linux / macOS (e.g. BRONTOSAURUS):**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
$EDITOR .env                      # paste NPS_API_KEY and DATABASE_URL
python -m src.data_pull
python -m src.spatial_join
```

After Phase 1 succeeds (every contiguous state + DC has ≥1 NPS unit), the next plan covers Phase 2 (OSRM matrix), Phase 3 (OR-Tools solver), Phase 4 (Folium map), Phase 5 (writeup).

## Repo layout

```
optitrek/
├── 01-…08-OPTITREK-*.md     # planning docs (source of truth)
├── DECISIONS.md             # locked decisions (D1–D5 + follow-ups)
├── BUILD_STATUS.md          # live state of the project
├── CLAUDE.md                # AI agent operating manual
├── README.md                # this file
├── HANDOVER.md              # historical 2026-05-18 handover
├── diagnostics_unreachable_pois.md  # 79 POIs >10% bad-pair rate report
├── requirements.txt
├── .env.example
├── .gitattributes           # LF lock for *.sh and *.py
├── docker-compose.yml       # stub (Docker Desktop is broken; use WSL docker)
├── src/
│   ├── db.py                # Neon Postgres connection (psycopg v3)
│   ├── schema.sql           # PostGIS DDL (idempotent)
│   ├── data_pull.py         # Phase 1A: NPS API → PostGIS
│   ├── spatial_join.py      # Phase 1B: state assignment + coverage gate
│   ├── matrix_builder.py    # Phase 2: OSRM /table → parquet matrices
│   ├── solver.py            # Phase 3: OR-Tools VRP solver + Tier 2 modes
│   ├── visualize.py         # Phase 4: Folium maps with per-leg polylines
│   ├── config.py            # Tier 2 TripConfig YAML loader + validation
│   ├── poi_query.py         # Tier 2 POI fetch with filters
│   ├── trip.py              # Tier 2 orchestrator (config → matrix → solve → render)
│   ├── run_tier1.py         # Tier 1 glue (capped + uncapped solve, both maps)
│   ├── border_crossing.py   # D5 follow-up: customs-time penalty
│   └── web/                 # Stage 1 FastAPI form (local-dev only; UX known clumsy)
├── scripts/                 # ops + analysis + diagnostic tooling (~30 files)
│   ├── filter_pbf.sh        # tag-filter US PBF to major roads
│   ├── build_osrm.sh        # 3-stage OSRM build (extract/partition/customize)
│   ├── build_na_osrm.sh     # combined US+Canada OSRM build (D5)
│   ├── run_tier1_local.sh   # full Tier 1 orchestration
│   ├── run_oracle.sh        # Tier 1 oracle replay via tier1_replica.yaml
│   ├── run_trip.py          # Tier 2 CLI entrypoint
│   ├── test_tier1_replica.py  # Tier 1 oracle (±0.5% drift check)
│   ├── probe_ak_optin.py    # verify AK opt-in candidate counts (D5 follow-up)
│   ├── diagnose_unreachable_pois.py  # surface high-unreachability POIs
│   ├── olson_control.py     # Control 1: OR-Tools on Olson's 50 stops
│   ├── california_control.py # Control 2: force 2 CA stops in 438-pool
│   ├── olson_route_diff.py  # Olson 2015 vs OR-Tools edge-by-edge diff
│   ├── visual_proof.py      # 8-leg Western parks Folium overlay
│   ├── smoke_test_na_engine.sh  # cross-border engine smoke test
│   ├── render_comparison_map.py # dual-engine route overlay (US vs US+Canada)
│   └── …                    # other dump/fetch/render helpers
├── trips/                   # Tier 2 YAML configs (tier1_replica + ~9 example trips)
├── tests/                   # 121 tests (pytest, runs in ~2:30)
├── gallery/                 # showcase Folium maps + screenshots
├── data/
│   ├── nps_raw/             # raw API responses (gitignored)
│   ├── boundaries/          # Census TIGER shapefiles (gitignored)
│   ├── matrix/              # cached OSRM matrices (gitignored)
│   ├── osrm-major/          # US-only OSRM artifacts (gitignored, ~5 GB)
│   └── osrm-major-na/       # US+Canada OSRM artifacts (gitignored, ~5.6 GB)
└── output/                  # Folium maps (gitignored, Phase 4)
```
