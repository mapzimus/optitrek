# Optitrek — Handover (2026-05-18)

> Single source of truth for "where are we, what was done today, what's next."
> Update this at the end of each working session so the next pickup is clean.
> Source-of-truth for *scope* lives in `01-…08-OPTITREK-*.md`; this doc is
> *state*.

---

## TL;DR

The repo went from **8 planning docs and nothing else** → **a public GitHub
repo with Phase 1 running live against Neon + Phase 2–4 code written and
unit-tested**. 17 tests pass. Database has 466 NPS units with every required
zone covered. Everything past Phase 1 needs OSRM, which means **the next
session has to be on BRONTOSAURUS** (or wherever OSRM can run). Estimated
~1 day of work remaining to ship Tier 1 (the blog post + interactive map).

---

## What's live right now

| Thing | Where | Notes |
|---|---|---|
| Code | https://github.com/mapzimus/optitrek (public) | 3 commits on `main` |
| Working copy | `D:\optitrek` (Windows) | Will move to BRONTOSAURUS |
| Database | Neon project `optitrek` (us-east-1) | 466 NPS rows in `pois` table |
| Tests | `pytest tests/` | 17 passing, 0 failing |
| Credentials | `D:\optitrek\.env` (gitignored) | NPS API key + Neon pooled URL |
| GitHub auth | `gh` CLI, account `mapzimus` | Token stored in Windows keyring |

---

## What was done today (2026-05-18)

### Decisions locked → `DECISIONS.md`
All 4 Tier 1 blockers from Gap Audit:
- **D1** — Run solver in both **capped** (exactly 1 per required state, 49 stops total) and **uncapped** (≥1 per state, may add shortcuts) modes. Publish both.
- **D2** — D.C. is the **49th required zone** alongside the 48 contiguous states.
- **D3** — Use **US-only Geofabrik extract** for OSRM (prevents routing through Canada — Olson's "Cleveland waypoint" problem).
- **D4** — Tier 1 is done per the **9-point success checklist** from Gap 5.

### Repo scaffolding
- `.gitignore`, `.env.example`, `README.md`, `requirements.txt`, `docker-compose.yml`
- Empty dirs (with `.gitkeep`): `data/{nps_raw,boundaries,matrix,osrm}/`, `output/`
- Python package skeleton: `src/__init__.py`, `src/db.py`, `src/schema.sql`

### Phase 1 — Data (RAN LIVE, succeeded)
- `src/data_pull.py` — pulls full NPS catalog, validates coords, upserts into PostGIS.
  - **Live result:** 474 parks fetched, 8 territory parks discarded (Virgin Islands, American Samoa, etc.), 466 inserted.
- `src/spatial_join.py` — downloads Census TIGER state polygons, runs `ST_Contains` to tag each POI with a state, includes coverage validation gate.
  - **Live result:** 465/466 assigned by ST_Contains, 1 fallback (Roosevelt Campobello International Park — coords in Canada, assigned to ME via `api_states`). **All 49 required zones covered.**

### Phase 2 — OSRM matrix (CODE READY, not run — needs OSRM up)
- `src/matrix_builder.py` — batched OSRM `/table` client (default 100 sources/request), caches to parquet, includes Gap-10 unreachable-pair scan.
- Output: `data/matrix/{pois,duration,distance}.parquet`.

### Phase 3 — Solver (CODE READY + UNIT TESTED, not run on real data)
- `src/solver.py` — OR-Tools constrained TSP, set-cover + Hamiltonian-cycle.
  - Capped and uncapped modes per D1.
  - **Real-node depot** (not virtual) so the objective is the true closed-loop cost — the open-path bug from the first draft is documented in code comments.
  - Configurable time limit (default 300s per Gap 8).
  - `validate()` helper to catch missing states, dupes, negative legs.
- `tests/test_solver.py` — 6 tests, including a shortcut-insertion case that proves uncapped beats capped when allowed (160 vs 200 on a hand-crafted graph).

### Phase 4 — Folium viz (CODE READY + SMOKE TESTED, not run on real data)
- `src/visualize.py` — renders standalone HTML with road geometries pulled from OSRM `/route`, numbered markers, popups, and a summary panel (drive hours / days-at-8h/day / status).
- Falls back to straight lines if OSRM is unreachable (so the map still renders for debugging).
- `tests/test_visualize_smoke.py` — 1 test, hand-built 3-stop MA/NH/VT loop.

### Tests
- `tests/test_data_pull.py` — 10 tests pinning the NPS API response contract on `_parse_park()`. Catches regressions if NPS changes their schema.
- Plus the solver + viz tests above. **17 passing total.**

### OSRM bring-up (STAGED for BRONTOSAURUS)
- `scripts/build_osrm.sh` — idempotent bash script: downloads `us-latest.osm.pbf` (~10 GB), runs `osrm-extract` → `osrm-partition` → `osrm-customize`. Checks for each artifact before recomputing.
- `docker-compose.yml` — activated (was a stub), exposes OSRM on `:5000` with `--max-table-size 8000` and a healthcheck.

### Infrastructure
- Local Python venv at `D:\optitrek\.venv` (Python 3.13.12) with full `requirements.txt` installed.
- `git init -b main`, 3 commits, pushed to `origin`.
- `gh` CLI installed via winget, auth'd as `mapzimus`.
- `git config --global --add safe.directory D:/optitrek` set (Windows cross-drive quirk; won't apply on BRONTOSAURUS).

### Bug fixes uncovered by running for real
1. `data_pull.py` upsert needed explicit `::text` casts so PostgreSQL could infer `jsonb_build_object` param types.
2. `spatial_join.py` SQLAlchemy DSN needed rewriting to `postgresql+psycopg://` because we have psycopg v3, not psycopg2.
3. Console print of `≥` crashed on Windows cp1252 — swapped for `>=`.
4. Roosevelt Campobello (NB, Canada) needed an `api_states` fallback in `spatial_join.py`.

---

## What's left to ship Tier 1

The blueprint's Tier 1 = "NPS-only optimal loop across 48 states + DC" with
an interactive map + blog post + GitHub repo. Repo box is checked. Five
items remain:

### 1. Move / clone to BRONTOSAURUS (blocking everything else) — ~10 min
```bash
git clone https://github.com/mapzimus/optitrek
cd optitrek
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp /path/to/keys-source .env    # rebuild from scratch; don't copy from Windows
```

### 2. Bring up OSRM — ~30-60 min
```bash
chmod +x scripts/build_osrm.sh
./scripts/build_osrm.sh         # downloads ~10 GB, runs extract/partition/customize
docker compose up -d osrm
# Smoke test:
curl 'http://localhost:5000/route/v1/driving/-77.0365,38.8977;-71.0589,42.3601'
```

The partition step is the slow one (~30 min on a typical box). The script
is idempotent — restartable.

### 3. Build the distance matrix — ~10-20 min
```bash
python -m src.matrix_builder
```
Writes `data/matrix/{pois,duration,distance}.parquet`. Will print per-batch
progress and a Gap-10 reachability summary.

### 4. Write the solve-and-render glue script — ~30 lines of code
**This is the only piece of NEW code remaining.** It needs to:
1. Load `data/matrix/pois.parquet` + `data/matrix/duration.parquet`.
2. Build a list of `Node(id, state)` from the POI rows.
3. Pick a depot_index (any row in a required state — `0` is fine since the POIs are sorted by `(state, id)`).
4. Call `solver.solve()` twice — once `mode="capped"`, once `mode="uncapped"`.
5. For each result: call `visualize.stop_geos_from_poi_table()` then `visualize.render_map()` → write `output/optitrek_capped.html` and `output/optitrek_uncapped.html`.
6. Print summary stats (total hours, stops, comparison to Olson's 13,699 mi / 224 h).

Suggested filename: `src/run_tier1.py`. Should be runnable as `python -m src.run_tier1`.

### 5. Write the blog post — ~1 day
Per Tier 1 Phase 5: methodology, results, both maps embedded, link to repo,
the Olson comparison table. Markdown → publishable wherever.

---

## File map (what's in the repo)

```
optitrek/
├── 01-OPTITREK-PROJECT-BLUEPRINT.md        ← source-of-truth planning docs
├── 02-OPTITREK-OLSON-COMPARISON.md           (don't edit; see DECISIONS.md
├── 03-OPTITREK-TIER1-PROJECT-DOC.md          for delta from these)
├── 04-OPTITREK-DATABASE-EXPANSION-SPEC.md
├── 05-OPTITREK-TIER2-PROJECT-DOC.md
├── 06-OPTITREK-TIER3-PROJECT-DOC.md
├── 07-OPTITREK-DECISION-LOG.md
├── 08-OPTITREK-GAP-AUDIT.md
├── DECISIONS.md                            ← 4 locked Tier 1 decisions
├── HANDOVER.md                             ← this file
├── README.md
├── requirements.txt
├── .env.example                            ← .env is gitignored
├── .gitignore
├── docker-compose.yml                      ← OSRM service
├── scripts/
│   └── build_osrm.sh                       ← one-shot OSRM bring-up
├── src/
│   ├── __init__.py
│   ├── db.py                               ← get_conn(), apply_schema()
│   ├── schema.sql                          ← pois table DDL
│   ├── data_pull.py                        ← Phase 1A
│   ├── spatial_join.py                     ← Phase 1B
│   ├── matrix_builder.py                   ← Phase 2
│   ├── solver.py                           ← Phase 3
│   └── visualize.py                        ← Phase 4
├── tests/
│   ├── __init__.py
│   ├── test_data_pull.py                   ← 10 tests
│   ├── test_solver.py                      ← 6 tests
│   └── test_visualize_smoke.py             ← 1 test
├── data/
│   ├── nps_raw/                            ← raw API pages (gitignored)
│   ├── boundaries/                         ← TIGER shapefile (gitignored)
│   ├── matrix/                             ← parquet matrices (gitignored)
│   └── osrm/                               ← .osm.pbf + .osrm artifacts (gitignored)
└── output/                                 ← rendered HTML maps (gitignored)
```

---

## Known caveats / things future-you should know

- **8 NPS units discarded** during ingest as out-of-bounding-box. All
  territories (Virgin Islands, American Samoa, etc.). Logged to
  `data/nps_raw/discarded.csv`.
- **Roosevelt Campobello (ME)** has coords on a Canadian island; assigned to
  ME via the `api_states` fallback in `spatial_join.py`. The fallback is
  generic — applies to any future NPS unit in the same situation.
- **AK (19 units) + HI (9 units)** are ingested into the DB but excluded from
  the Tier 1 solver candidate set in `matrix_builder.py:EXCLUDED_STATES`.
  This is intentional per the contiguous-US scope. If you want them included
  later, just drop them from that set.
- **Solver depot is `nodes[0]` by default.** For a symmetric TSP, the choice
  of depot doesn't change the optimal cycle's cost (it just changes which
  point on the cycle is called "start"). Fine for our use case. If we ever
  introduce asymmetric edges (one-way roads, ferry schedules), reconsider.
- **NPS API `parkCode` is the upsert key**, stored in `tags->>'park_code'`.
  Re-running `data_pull.py` is safe — existing rows are updated, not
  duplicated. The unique constraint is enforced by a partial unique index
  in `schema.sql`.
- **psycopg v3 vs psycopg2.** We use v3. The SQLAlchemy DSN in
  `spatial_join.py` is rewritten to `postgresql+psycopg://` (the
  `_sqlalchemy_url()` helper). Don't switch to psycopg2 without thinking.

---

## Next session checklist (in order)

1. [ ] Pull the repo on BRONTOSAURUS: `git clone https://github.com/mapzimus/optitrek`
2. [ ] Recreate `.env` with NPS API key + Neon `DATABASE_URL` (don't copy across — re-paste from the keys file)
3. [ ] Create venv + install requirements
4. [ ] Run `pytest tests/` to confirm 17/17 still pass on the new box
5. [ ] Run `./scripts/build_osrm.sh` (long; ~30-60 min)
6. [ ] `docker compose up -d osrm` and smoke-test with `curl`
7. [ ] `python -m src.matrix_builder`
8. [ ] Write `src/run_tier1.py` (the glue script — see "What's left" item 4)
9. [ ] Run it, verify two HTMLs land in `output/`
10. [ ] Update **this** doc with results and pending blockers
11. [ ] Start the blog post (Tier 1 Phase 5)

After Tier 1 ships, the next doc to read is `04-OPTITREK-DATABASE-EXPANSION-SPEC.md`.
