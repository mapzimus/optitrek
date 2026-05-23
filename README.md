# Optitrek

An algorithmic road-trip optimizer for the United States. A 2026 redo of Randal Olson's 2015 "optimal road trip" project — with a 400-stop candidate pool (Tier 1) growing to 100,000+ (Tier 2/3), a real constrained optimizer (OR-Tools), open-source self-hosted routing (OSRM), and an interactive web map (Folium).

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
├── 01-…08-OPTITREK-*.md   # planning docs (source of truth)
├── DECISIONS.md           # locked decisions
├── README.md              # this file
├── requirements.txt
├── .env.example
├── docker-compose.yml     # stub for OSRM (Phase 2)
├── src/
│   ├── db.py              # DB connection helper
│   ├── schema.sql         # PostGIS DDL
│   ├── data_pull.py       # Phase 1A: NPS → PostGIS
│   └── spatial_join.py    # Phase 1B: state assignment + coverage gate
├── data/
│   ├── nps_raw/           # raw API responses (gitignored)
│   ├── boundaries/        # Census TIGER shapefiles (gitignored)
│   └── matrix/            # cached OSRM matrices (gitignored, Phase 2)
└── output/                # Folium maps (gitignored, Phase 4)
```
