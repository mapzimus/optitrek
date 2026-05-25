# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An algorithmic road-trip optimizer for the contiguous US. A 2026 re-take of Randal Olson's
viral 2015 "optimal road trip" project, but solving the harder problem: choose stops AND
order from a 438-park NPS catalog (vs his 50 hand-picked landmarks, order-only).

See `README.md` for the public pitch and `01-…08-OPTITREK-*.md` for the eight planning docs.
**Authoritative state of the project:** `BUILD_STATUS.md` (updated each session). `HANDOVER.md`
is the original 2026-05-18 handover and is now historical context.

## Commands

All Python commands assume the project venv. There are TWO venvs depending on host:

- Windows host (`E:\dev\optitrek\.venv\Scripts\python.exe`) — works for tests + Phase 1
- WSL Ubuntu (`/root/venvs/optitrek-wsl/bin/python`) — required for Phase 2+ because the
  matrix builder and solver need to reach `osrm-routed` on `localhost:5000`, and Docker
  Desktop's WSL→Windows port relay is currently broken on BRONTOSAURUS (see "Known
  environment quirks" below)

```bash
# Tests (no DB or OSRM needed; 121 tests should pass in ~2.5 min)
python -m pytest tests/ -q
python -m pytest tests/test_solver.py -v          # single file
python -m pytest tests/test_solver.py::test_capped_visits_all_states  # single test

# Phase 1 — Data (live against Neon Postgres; ~1 min)
python -m src.data_pull                            # NPS API → pois table
python -m src.spatial_join                         # state assignment + coverage gate

# Phase 2-4 — full Tier 1 pipeline (needs OSRM up locally)
./scripts/run_tier1_local.sh                       # orchestrates: docker run osrm-routed,
                                                   # wait for ready, spot-check, matrix build,
                                                   # solve capped+uncapped, render maps, cleanup

# Individual phases (if OSRM is already up)
python -m src.matrix_builder                       # Phase 2 — OSRM /table → parquet matrices
python -m src.run_tier1                            # Phase 3-4 — solve + render
```

## OSRM bring-up (one-time, ~30 min)

**Do NOT try to run osrm-routed against the full US OSM extract on BRONTOSAURUS.** It crashes
the kernel (bug check 0x1, APC_INDEX_MISMATCH) under sustained memory pressure. See
`brontosaurus-osrm-memory-ceiling` in user memory and the 2026-05-21 incident notes in
`BUILD_STATUS.md`.

Always build artifacts on the filtered (major-roads-only) PBF instead:

```bash
# From WSL Ubuntu (cd /mnt/e/dev/optitrek):
curl -L -C - -o data/us-latest.osm.pbf https://download.geofabrik.de/north-america/us-latest.osm.pbf
./scripts/filter_pbf.sh data/us-latest.osm.pbf data/osrm-major/us-major.osm.pbf
OSRM_THREADS=6 ./scripts/build_osrm.sh data/osrm-major/us-major.osm.pbf data/osrm-major us-major
```

Filtered artifacts (~5 GB) fit BRONTOSAURUS's 24 GB WSL cap with comfortable headroom.

### Optional: US+Canada artifact set (for cross-border trips, per D5)

For trips where Canadian highways are actually fastest (Detroit↔Buffalo, Niagara↔Sault Ste M),
build a *second* artifact set that includes Canadian major roads. The US-only set stays the
default — this is opt-in per trip.

```bash
# From WSL Ubuntu:
./scripts/build_na_osrm.sh
# Downloads canada-latest.osm.pbf (~5 GB), filters to major roads, merges with
# the existing us-major.osm.pbf via osmium merge, then runs extract/partition/customize
# on the combined PBF. Output: data/osrm-major-na/ (~6.2 GB).
```

Run both engines side-by-side on different ports:

| Engine | Port | Container name | Data dir |
|---|---|---|---|
| US-only (D3 default) | 5000 | `optitrek-osrm-major` | `data/osrm-major/` |
| US+Canada (D5 opt-in) | 5001 | `optitrek-osrm-na` | `data/osrm-major-na/` |

Verify the cross-border engine actually routes through Canada:
```bash
./scripts/smoke_test_na_engine.sh   # starts both, probes 4 legs, shows delta
```

## Architecture

### Four-phase pipeline

```
Phase 1: NPS API ──► PostGIS (Neon)
         data_pull.py + spatial_join.py
         Output: 466 NPS units, ST_Contains-tagged with state
         Excludes AK + HI per DECISIONS.md → 438 candidates for solver

Phase 2: PostGIS ──► OSRM /table ──► parquet
         matrix_builder.py
         Output: data/matrix/{pois,duration,distance}.parquet  (438×438)

Phase 3: parquet ──► OR-Tools ──► SolveResult
         solver.py — constrained VRP (set cover + Hamiltonian cycle)
         Two modes: "capped" (exactly 1/state) and "uncapped" (≥1/state)

Phase 4: SolveResult + OSRM /route ──► Folium HTML
         visualize.py — real road polylines, numbered markers, summary panel
         Output: output/optitrek_{capped,uncapped}.html
```

### Module boundaries

- `src/db.py` — Postgres connection. Uses **psycopg v3** (not v2). The SQL `IN (...)` idiom
  with a tuple parameter does NOT work in psycopg v3; use `<> ALL(%(arr)s)` with a list.
- `src/solver.py` — Pure-Python interface over OR-Tools `RoutingModel`. No DB or OSRM
  dependency; takes a duration matrix + node-state mapping. The two solver "modes" map
  to OR-Tools `AddDisjunction` cardinality settings. Cost is multiplied by 1000 internally
  (millisecond precision) so int64 doesn't overflow. Coverage failures are penalized at
  `10^12` so the solver never skips a required state.
- `src/visualize.py` — Folium rendering. Fetches per-leg polylines from OSRM `/route` at
  render time and decodes them with the `polyline` library. Falls back to straight lines
  if OSRM is unreachable (the map still renders for debugging).
- `src/matrix_builder.py` — Batches OSRM `/table` calls (default 100 sources/req). Writes
  `pois.parquet` with one row per POI and two N×N float32 matrices (duration in seconds,
  distance in meters). Runs `validate_matrix()` at the end and warns if any row has >10%
  unreachable pairs (Gap-10 check).
- `src/run_tier1.py` — Tier 1 glue. Loads matrices, solves both modes, renders both maps,
  prints the Olson comparison line. Env knobs: `OPTITREK_DEPOT_INDEX`, `OPTITREK_TIME_LIMIT`,
  `OSRM_URL`.

### Scripts and ops tooling

- `scripts/run_tier1_local.sh` — full Tier 1 orchestration, designed for WSL Ubuntu where
  Docker Desktop's broken integration doesn't apply. Has a trap that stops the OSRM
  container on exit.
- `scripts/filter_pbf.sh` — osmium-tool docker wrapper to tag-filter US PBF to major roads
  (motorway/trunk/primary/secondary/tertiary + their `_link` variants).
- `scripts/build_osrm.sh` — three-stage OSRM build (extract/partition/customize) with
  positional args for PBF path + output dir + base name. Defaults preserve the original
  full-US behavior. **Customize sentinel is `.osrm.cell_metrics`** (was originally
  `.osrm.cells`, which is partition's output — caused customize to silently skip).
- `scripts/run_build_osrm.sh` — Git Bash wrapper that sets `MSYS_NO_PATHCONV=1` + an
  explicit PATH so `build_osrm.sh` can be launched from PowerShell `Start-Process`.
- `scripts/visual_proof.py` — hits OSRM `/route` for 8 Western parks, renders a Folium
  overlay (`output/osrm_visual_proof.html`). Smoke test used during VM-to-local validation.
- `scripts/olson_control.py` — Control 1: OR-Tools on Olson's exact 50 waypoints using his
  Google distances. Isolates optimizer-quality contribution. Result: ~1% time / 0.1% miles.
- `scripts/california_control.py` — Control 2: forces 2 California stops in our 438-pool
  via a `CA-N`/`CA-S` pseudo-zone trick (lat split at 36.0). Apples-to-apples vs Olson's
  50-stop trip shape. Result: -9.2% time / -24.3% miles vs Olson.
- `scripts/compare_overlays.py` — renders two Folium overlay maps comparing the four tours
  (Olson 2015, Control 1, Optitrek capped, Optitrek 2-CA).
- `scripts/build_na_osrm.sh` — Canada PBF download → osmium tags-filter → osmium merge with
  `us-major.osm.pbf` → `osrm-extract`/`partition`/`customize` on the combined PBF. Output
  `data/osrm-major-na/`. One-time, ~30-60 min. Idempotent (skips finished stages).
- `scripts/smoke_test_na_engine.sh` — starts BOTH OSRM engines side-by-side, probes 4
  representative legs (Detroit→Buffalo, Niagara→Sault Ste M, Acadia→Campobello,
  Seattle→Glacier) against each, prints the delta. Validates the D5 dual-engine setup.
  Trap stops both containers on exit.
- `scripts/render_comparison_map.py` + `scripts/run_comparison_map.sh` — renders a single
  Folium HTML with both US-only and US+Canada routes as toggleable FeatureGroups. The
  wrapper ensures both engines are up, activates the venv, and runs the Python renderer.
  Engines are left running after exit for fast re-runs.

### Decisions and gaps

`DECISIONS.md` holds the four locked Tier 1 decisions (D1–D4). The deeper 19-decision log
is `07-OPTITREK-DECISION-LOG.md`. Known scope gaps and their resolutions are
`08-OPTITREK-GAP-AUDIT.md`.

## Known environment quirks (BRONTOSAURUS, 2026-05-21)

- **Docker Desktop is broken.** Stale socket files in the Inference Manager and Secrets
  Engine subsystems prevent the daemon from starting on Windows. Use WSL-native Docker
  instead (`wsl -d Ubuntu -u root -- docker …`). The `docker compose` plugin is a symlink
  into Docker Desktop's mount and is therefore also unavailable; `docker-compose.yml`
  documents intent but you must use `docker run` directly until DD is repaired.
- **Git Bash mangles `/root/...` paths.** When invoking WSL commands that reference WSL
  paths from PowerShell Bash, set `MSYS_NO_PATHCONV=1` first or the path gets translated
  to `C:/Program Files/Git/root/...`. The `run_tier1_local.sh` orchestrator handles this
  by being a bash script that WSL invokes directly.
- **WSL2 idle timeout.** WSL shuts down its vmmem after 60s of inactivity, taking the
  Docker daemon and any `--rm` containers with it. The orchestrator bundles all
  matrix/solve/render work into one bash session so the VM stays alive throughout.
- **`.wslconfig` is at `memory=24GB`.** Lowered from 30 GB after the 2026-05-21 BSOD.
  Do NOT raise it without re-introducing the BSOD risk if osrm-routed runs on the full-US
  artifact set.

## Repository conventions

- **Commit style:** `<type>(<scope>): <subject>` — types: `feat`, `fix`, `refactor`,
  `chore`, `docs`. See git log for examples.
- **Branch:** all work on `main`; this is a solo project on a personal GitHub
  (`github.com/mapzimus/optitrek`). The C:\ tree at `C:\Users\mhowe\Desktop\optitrek\` is
  a stale pre-migration copy with `.claude/worktrees/...` — the authoritative repo is at
  `E:\dev\optitrek\` (`main` branch). The C:\ tree can be deleted after closing this
  Claude session.
- **Tests are colocated by phase**, not by file structure: `test_data_pull.py` pins the
  NPS API contract, `test_solver.py` includes a hand-crafted shortcut-insertion case that
  proves uncapped > capped when the data supports it, `test_visualize_smoke.py` is a
  3-stop hand-built loop.
- **Data subdirs are gitignored:** `data/nps_raw/`, `data/boundaries/`, `data/matrix/`,
  `data/osrm/`, `data/osrm-major/`, `data/us-latest.osm.pbf`. `data/olson/` is tracked
  (small enough; Olson's TSV is included verbatim for reproducibility of his comparison).

## Tier 2 entry point (config-driven)

```bash
# CLI: python -m scripts.run_trip <yaml_path> [flags]
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python \
  -m scripts.run_trip trips/southwest_parks.yaml
```

Useful flags:
- `--dry-run` — print resolved depot + candidate count, no solve
- `--time-limit-override N` — override config's time_limit_seconds
- `--output-dir <dir>` — write the HTML somewhere other than `output/`

YAML config schema documented at
`docs/superpowers/specs/2026-05-22-tier2-trip-config-design.md` §4.

Tier 1 entry point (`scripts/run_tier1.py`) is untouched and still
works. Tier 2 reproduces it exactly via `trips/tier1_replica.yaml`
(see `scripts/run_oracle.sh` for the OSRM lifecycle).

## Time-budgeted solver mode (Tier 2 headline)

Two solver MODES live in `src/solver.py:solve_with_config()`:

- **State-coverage** (default): visit ≥1 POI per state in `config.states`,
  minimize total travel time. The "Tier 1 with filters" semantic.
- **Time-budgeted**: activated when `config.total_trip_days is not None`.
  Switches to score-maximization-within-soft-budget. Each POI has a value:
  ```
  poi_priority[poi.id]           # explicit per-POI value
   ?? category_priority[category] # category fallback
   ?? 0                           # incidental (visited only as waypoint)
  ```
  Budget = `total_trip_days × max_hours_per_day × 3600` seconds. Cap is
  SOFT — each hour over budget costs `time_budget_overage_penalty`
  priority points, so the solver can exceed the budget if a high-value
  POI sits just past the line.

When time-budgeted mode is on, `states` becomes a geographic FILTER only
(at fetch time, via SQL) — it no longer enforces coverage. `must_include`
still hard-forces specific POIs. `loop=False` still trims the closing
leg from `total_cost`.

**Crucial unit convention** (`PRIORITY_TO_DRIVE_HOUR = 3600 * cost_scale`):
1 priority point ≡ 1 hour of driving's worth of solver cost. So
`category_priority.national_park: 10` reads naturally as "I'd drive 10 h
round-trip to visit a National Park." An earlier version had the wrong
unit math (skip_penalty = value × cost_scale, forgetting the
seconds-per-hour factor) and was caught by
`test_time_budgeted_visits_high_value_when_budget_loose` — solver was
visiting nothing because skip penalty was 360× too cheap relative to
drive cost. The PRIORITY_TO_DRIVE_HOUR constant in `_solve_time_budgeted`
documents the conversion explicitly.

Example: `trips/southwest_7day_budget.yaml` — 7-day SW parks loop with
category_priority + a Grand Canyon poi_priority override.

## Web frontend (Stage 1, local-dev only)

FastAPI + Jinja2 + htmx + Tailwind CDN. Structured form covering every
TripConfig field, sync solve, real-time POI autocomplete from Neon.
Launch with `./scripts/run_web.sh` (binds 0.0.0.0:8000 by default; pass
`OPTITREK_WEB_PORT=8765` if you have a port collision on Windows — WSL2
silently no-ops port forwarding when Windows already owns the port).

```
src/web/
├── main.py              FastAPI app: 4 routes + /maps static mount
├── form_parser.py       form-dict → TripConfig (handles nested keys for
│                        category_priority[<cat>] and the poi_priority
│                        textarea, soft-warns on malformed lines)
└── templates/
    ├── base.html        Tailwind CDN + htmx layout
    ├── index.html       6-section form (basics, filters, must_include,
    │                    daily, routing, time-budgeted)
    ├── result.html      iframe-embedded Folium map + download link
    ├── error.html       friendly error page (no stack traces)
    └── partials/poi_search_results.html  htmx-driven autocomplete rows
```

Routes:
- `GET /` — form (categories populated from DB)
- `POST /solve` — parse + validate + run_trip + return result page
- `GET /api/categories` — JSON variant for dropdown re-renders
- `GET /api/poi-search?q=…` — HTML partial for htmx autocomplete
- `GET /maps/{file}` — static mount serving rendered Folium HTMLs

**Error handling:** three branches in `/solve` — `TripConfigError` → 400,
`OSRMEngineError` → 503 with "docker start optitrek-osrm-..." hint,
other exceptions → 500 with traceback (Stage 1 is local-dev; Stage 3
would route to a monitoring sink).

**Watch out for the Starlette template-API change.** With Starlette ≥0.34,
the old `templates.TemplateResponse(name, {"request": request, ...})`
form silently trips Jinja's autoescape cache with an unhashable tuple
key — confusing TypeError. The new positional form
`templates.TemplateResponse(request, name, context)` is the only safe
shape. All seven call sites in `main.py` use the new positional form.

**Known UX issue:** the form exposes every TripConfig field directly,
which feels redundant in places (e.g., `states` does different things
depending on whether `total_trip_days` is set). User feedback was
"hard to use, redundant, confusing." Stage 2 (async + email) and
Stage 3 (deploy) are planned; UX polish is deferred. The codebase's
*real* interface is the YAML config — the web form is a clumsy
generator for it.

## Cross-border routing (D5, opt-in)

D3 picks US-only OSRM as the default. D5 adds an opt-in US+Canada engine for trips
that benefit from cross-border routing (Detroit↔Buffalo via Ontario, Niagara↔Sault
Ste M via the Trans-Canada). The default stays US-only so the Tier 1 oracle
(193.0 h / 9,744 mi) is preserved exactly. See `DECISIONS.md` D5 for the rationale
and the measured per-leg savings.

### Per-trip opt-in via YAML

```yaml
# trips/my_trip.yaml
name: my_great_lakes_loop
states: [MI, OH, PA, NY, WI, MN]
loop: true
routing_network: us_canada       # default is "us"; this opts into the NA engine
border_crossing_minutes: 20      # default 20 min × 2 crossings = 40 min/leg overhead
                                  # set to 0 for NEXUS travelers or diagnostic runs
                                  # bump to 30+ for summer/holiday-weekend trips
```

`TripConfig.routing_network` is validated by `__post_init__` against the closed
set `{"us", "us_canada"}`. `src/trip.py:_osrm_url_for_network()` maps it to the
right URL.

### Border-crossing time (D5 follow-up)

OSRM doesn't model customs wait time, but every cross-border leg incurs ~20-30
min per crossing × 2 crossings per round-trip leg. `src/border_crossing.py:
apply_border_penalty()` uses matrix-differencing (US-only vs NA) to detect
cross-border legs and inject `2 × border_crossing_minutes` of overhead BEFORE
the solver runs — so the solver only picks Canada shortcuts where the routing
savings genuinely exceed the customs overhead. Without this, modest cross-
border legs (e.g., Acadia → Campobello) get falsely promoted.

When `routing_network='us_canada' and border_crossing_minutes > 0`, `run_trip()`
builds BOTH matrices (NA for solving, US for cross-border detection). Cost:
~30s extra matrix-build time per trip. Worth it for correctness.

### URL resolution

Environment overrides take precedence over defaults:

| `routing_network` | Env var | Default URL | Container |
|---|---|---|---|
| `"us"` (default) | `OSRM_URL` | `http://127.0.0.1:5000` | `optitrek-osrm-major` |
| `"us_canada"` | `OSRM_URL_NA` | `http://127.0.0.1:5001` | `optitrek-osrm-na` |

`run_trip()` prints the resolved engine on startup and threads the URL into BOTH
`build_matrix()` and `render_map()` — the matrix and the rendered polylines have
to come from the same engine or the map will visually lie about the solver's
solution.

### Running a comparison

```bash
cd /mnt/e/dev/optitrek
./scripts/run_comparison_map.sh trips/tier1_replica.yaml
# → output/tier1_replica_comparison.html
```

Both routes are drawn as toggleable Folium FeatureGroups with distinct colors
(US-only blue, US+Canada red). The banner shows total hours / miles / stops for
each plus the delta saved by cross-border routing.

### Alaska — conditionally reachable (D5 follow-up, 2026-05-25)

D3 excluded AK from the candidate pool because the US-only OSRM extract can't
route to it. With D5's US+Canada engine, the Alaska Highway through BC + Yukon
is in the routable graph. Verified empirically: Seattle→Anchorage on `:5001`
returns 2,363 mi / 51 h, accurate for the actual Alcan drive.

`src/poi_query.py:_excluded_states_for_config()` resolves the SQL exclusion
list per-trip:
- `routing_network='us'` (default) → exclude `["AK", "HI", "PR", "VI", "GU", "MP", "AS"]` (437 candidate POIs)
- `routing_network='us_canada'` → exclude `["HI", "PR", "VI", "GU", "MP", "AS"]` (456 candidate POIs, +19 from AK)

Tier 1's `matrix_builder.EXCLUDED_STATES = {"AK", "HI"}` stays unconditional —
Tier 1 always runs on the US-only engine. The conditional logic lives only in
Tier 2's `poi_query`. Run `python -m scripts.probe_ak_optin` to verify the
two candidate counts live against Neon.

### KNOWN GAP: ferries (route=ferry) are filtered out of the PBF

`scripts/filter_pbf.sh:51` only keeps `w/highway=...` ways. Ferry routes in OSM
are tagged `route=ferry` with no `highway` value, so `osmium tags-filter` strips
them before OSRM ever sees them. Empirical evidence: the current US engine
routes Seattle→Bainbridge as 92 mi / 127 min (driving around via Tacoma) when
the actual Washington State Ferry is 12 mi / 35 min.

**Impact:** Puget Sound, CT↔Long Island Cross Sound, Lake Champlain, Cape Cod,
and the **Alaska Marine Highway** (which is a US Interstate Highway designation,
Bellingham WA → Whittier AK) all currently invisible to the solver.

**Pending fix:** add `w/route=ferry` to the filter, rebuild both engines
(~1-2 h wall-clock for `us-major` + `north-america-major`), capture a new Tier 1
oracle baseline (the 9,744 mi number will shift down — some legs get shorter,
some previously-unreachable POIs like Isle Royale NP and Cumberland Island NS
come into scope). Tracked in `BUILD_STATUS.md`.

## Tier 1 status

Pipeline runs end-to-end. Result: 49 stops covering 49 zones (48 states + DC), 193.0 h /
9,744 mi. Beats Olson 2015 by 14% time / 29% miles in the headline number; the
controls in `scripts/{olson,california}_control.py` decompose that into ~1% optimizer
quality + ~9% set-selection + ~4% stop-count flexibility.

**Next:** Tier 1 Phase 5 — blog post per `03-OPTITREK-TIER1-PROJECT-DOC.md`. After Tier 1
ships, the next planning doc to read is `04-OPTITREK-DATABASE-EXPANSION-SPEC.md`.
