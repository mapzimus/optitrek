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
# Tests (no DB or OSRM needed; 17 tests should pass in ~25s)
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

## Tier 1 status

Pipeline runs end-to-end. Result: 49 stops covering 49 zones (48 states + DC), 193.0 h /
9,744 mi. Beats Olson 2015 by 14% time / 29% miles in the headline number; the
controls in `scripts/{olson,california}_control.py` decompose that into ~1% optimizer
quality + ~9% set-selection + ~4% stop-count flexibility.

**Next:** Tier 1 Phase 5 — blog post per `03-OPTITREK-TIER1-PROJECT-DOC.md`. After Tier 1
ships, the next planning doc to read is `04-OPTITREK-DATABASE-EXPANSION-SPEC.md`.
