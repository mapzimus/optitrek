# OSRM US Build — Status Snapshot

**Last updated:** 2026-05-23 — Tier 2 Phase 2 (cross-border routing) complete

## Tier 2 Phase 2 COMPLETE — Cross-border routing (2026-05-23)

Added an opt-in US+Canada routing engine alongside the existing US-only
engine. The Tier 1 oracle (193.0 h / 9,744 mi) is unchanged because the
default `routing_network` is still `"us"`. Trips that benefit from
cross-border routing (Great Lakes loops, Detroit ↔ Buffalo corridors,
Niagara ↔ Sault Ste M) can opt in per YAML.

### Why we did it

Probed four representative legs with the existing US-only OSRM and a
newly-built US+Canada OSRM to quantify D3's accuracy cost:

| Leg | US-only | US+Canada | Saved |
|---|---|---|---|
| Detroit → Buffalo | 360 mi / 7.0 h | 256 mi / 5.2 h | **−29% / −1.78 h** |
| Niagara Falls → Sault Ste M | 706 mi / 13.0 h | 537 mi / 9.7 h | **−25% / −3.29 h** |
| Acadia → Campobello Is. | 109 mi / 2.8 h | 109 mi / 2.8 h | 0 (US-1 wins) |
| Seattle → Glacier NP | 585 mi / 11.7 h | 585 mi / 11.7 h | 0 (I-90/US-2 wins) |

**Concentrated, not diffuse.** Only legs where geography forces a giant
US-side detour (Lake Superior, Lake Huron) benefit materially. Border
proximity alone doesn't predict savings — the Maine ↔ New Brunswick case
turned out *worse* via Canada (we had assumed it would help). Decision
recorded as **D5** in `DECISIONS.md`.

### What was built

| Artifact | Location | Size | Notes |
|---|---|---|---|
| Canada PBF (filtered to major roads) | `data/osrm-major-na/canada-major.osm.pbf` | 59 MB | osmium tags-filter |
| Combined US+Canada PBF | `data/osrm-major-na/north-america-major.osm.pbf` | 609 MB | osmium merge |
| OSRM artifact set (NA) | `data/osrm-major-na/north-america-major.osrm*` | ~5.6 GB | extract / partition / customize on combined PBF |
| Build script | `scripts/build_na_osrm.sh` | — | end-to-end Canada pull + merge + OSRM build |
| Smoke test | `scripts/smoke_test_na_engine.sh` | — | starts both engines side-by-side, probes 4 legs, prints delta |
| Comparison renderer | `scripts/render_comparison_map.py` + `scripts/run_comparison_map.sh` | — | dual-engine overlay HTML for any trip YAML |

### Code changes

- **`src/config.py`** — new `routing_network` field on `TripConfig`
  (`"us"` | `"us_canada"`, default `"us"`). `__post_init__` validates
  against `_VALID_NETWORKS`. **Default preserves Tier 1 oracle exactly.**
- **`src/matrix_builder.py`** — `build_matrix(pois, osrm_url=None)` and
  `_request_table_block(..., osrm_url=None)` accept an explicit OSRM URL
  that overrides the `OSRM_URL` env var. Used by `run_trip()` to route
  the `/table` call to the correct engine.
- **`src/trip.py`** — `_osrm_url_for_network(routing_network)` resolves
  the right URL per config. Honors `OSRM_URL` (US-only, default
  `http://127.0.0.1:5000`) and `OSRM_URL_NA` (US+Canada, default
  `http://127.0.0.1:5001`). `run_trip()` prints the chosen engine on
  startup and threads the URL into both `build_matrix()` and
  `render_map()` so the matrix and the rendered polylines come from the
  same engine.
- **`tests/test_config.py`** — 3 new tests:
  `test_routing_network_default_is_us`,
  `test_routing_network_accepts_known_values`,
  `test_routing_network_rejects_unknown`.
- **`tests/test_trip.py`** — `build_matrix` mock updated to accept the
  new `osrm_url=None` kwarg.
- **Total passing tests:** 43 → 46.

### How to use cross-border routing

In any trip YAML:

```yaml
name: my_great_lakes_loop
states: [MI, OH, PA, NY, WI, MN]
categories: [national_park, national_lakeshore]
loop: true
routing_network: us_canada    # ← opt in; default is "us"
```

Then start BOTH engines (the wrapper script does this for you):

```bash
# From WSL Ubuntu (cd /mnt/e/dev/optitrek):
./scripts/run_comparison_map.sh trips/tier1_replica.yaml
# Produces output/tier1_replica_comparison.html with both routes overlaid.

# For a single-engine run with the new network:
docker run -d --name optitrek-osrm-na --rm \
    -p 127.0.0.1:5001:5000 -v "$(pwd)/data/osrm-major-na:/data:ro" \
    ghcr.io/project-osrm/osrm-backend:latest \
    osrm-routed --algorithm mld /data/north-america-major.osrm
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python \
  -m scripts.run_trip trips/my_great_lakes_loop.yaml
```

### Known follow-ups

- The combined NA matrix isn't pre-cached to parquet — every trip with
  `routing_network: us_canada` rebuilds it from scratch (~30 s per
  466×466 matrix). If we run a lot of NA trips, cache it under
  `data/matrix-na/`.
- Comparison renderer reuses `_osrm_url_for_network()` but doesn't yet
  surface it as a public API. Fine for now; refactor if a third routing
  network ever lands.
- **Border-crossing time penalty (2026-05-23, same-day follow-up to D5).**
  OSRM is blind to customs wait time. Added `TripConfig.
  border_crossing_minutes: int = 20` and `src/border_crossing.py:
  apply_border_penalty()` which uses matrix-differencing (US-only vs NA)
  to detect cross-border legs and inject `2 × border_minutes × 60` s of
  overhead per leg BEFORE the solver runs. Threshold 60 s above the
  US-only number — anything below is network noise from osmium merge,
  not a real Canadian shortcut. `summarize_border_impact()` reports
  before-vs-after delta + count of legs flipped from net-positive to
  net-negative by the penalty. `run_trip()` now builds the US-only
  matrix as a baseline when `routing_network='us_canada' and
  border_crossing_minutes > 0`. Set `border_crossing_minutes: 0` to
  suppress (NEXUS travelers, diagnostic runs). 15 new tests
  (`tests/test_border_crossing.py` + 4 in `test_config.py` + 2
  integration tests in `test_trip.py`).
- **Solver time-budget gotcha for cross-border:** the Tier 1 oracle is
  tuned to converge on the US-only matrix in 300s. The US+Canada matrix
  has a different search landscape and OR-Tools may need 900–1200s to
  converge to a tour that's actually ≤ US-only cost (which the math
  requires — adding edges to a graph can never increase shortest paths,
  so the optimal cross-border tour must be ≤ optimal US-only tour). For
  comparison renders, use `--time-limit-override 1200` on
  `render_comparison_map.py`. For production trips, bump
  `time_limit_seconds: 1200` in YAMLs that opt into `routing_network:
  us_canada`. A first attempt at 300s on the NA matrix produced 203.6 h
  / 10,513 mi — 10.6 h *worse* than US-only — purely from solver
  non-convergence in the larger search space, not from any matrix
  problem.

---

## Tier 2 Phase 1 COMPLETE (this update)

- `TripConfig` dataclass + YAML loader at `src/config.py` with full
  validation in `__post_init__` (filename safety, max_radius requires
  start_state, loop=False requires start_state, max_stops feasibility,
  deferred-fields warn).
- POI fetch with filters at `src/poi_query.py` (categories, states,
  max_radius via ST_DWithin, must_include override with warning when
  POI is outside filter scope, typed exceptions for empty/single-stop/
  unreachable cases).
- Solver wrapper `solve_with_config()` at `src/solver.py` adds three
  new constraints: must_include (ActiveVar hard), max_stops (soft
  excess penalty + defensive post-validation), loop=False (open path).
- Daily leg splitting + ColorBrewer color-by-day in `src/visualize.py`
  (`split_into_days()` + `colors_for_days()` with 9- and 12-color
  palettes for trip lengths up to ~12 days).
- Top-level orchestrator at `src/trip.py`; CLI runner at
  `scripts/run_trip.py` (argparse with --dry-run / --output-dir /
  --time-limit-override / --verbose flags).
- Two example YAMLs: `trips/tier1_replica.yaml` (oracle) and
  `trips/southwest_parks.yaml` (demo).
- Tier 1 replica oracle at `scripts/test_tier1_replica.py` (with
  `scripts/run_oracle.sh` wrapper) reproduces 193.0 h / 9,744 mi within
  ±0.5%. The oracle caught and fixed 4 real bugs in `solve_with_config`
  during commit `5f6f674` — proof that the strict tolerance pays off.
- Gallery map 09 (`gallery/09_southwest_parks.html`) from the config
  layer: 5 stops · 5 states · 41.6 h · 1,431 mi.
- Tests: `tests/test_config.py`, `tests/test_poi_query.py`,
  `tests/test_trip.py`, `tests/test_visualize_days.py`, plus extensions
  to `tests/test_solver.py`. Total passing tests grew from 17 to 43.

### Running a Tier 2 trip

```bash
cd /e/dev/optitrek
# Author a YAML in trips/, then:
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash -c \
  "cd /mnt/e/dev/optitrek && ./scripts/run_oracle.sh"   # for tier1_replica
# OR for any other trip, start OSRM via render_overlays.sh pattern then:
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python \
  -m scripts.run_trip trips/southwest_parks.yaml
```

To author a new trip: copy `trips/southwest_parks.yaml`, edit the fields,
run with `scripts/run_trip.py`.

---

## Tier 1 PIPELINE COMPLETE (this update)

- OSRM artifacts built on filtered (major-roads-only) US PBF, **5.2 GB** at `data/osrm-major/`
- Matrix built: **438 POIs × 438** (driving duration + distance), at `data/matrix/`
  (438 = 466 NPS units minus 19 AK + 9 HI, per DECISIONS.md scope)
- Tier 1 solver run in both capped and uncapped modes
- Two output maps rendered: `output/optitrek_capped.html`, `output/optitrek_uncapped.html`
- Validation: 3-route spot-check within rounding error of full-network ground truth (0.0% / 0.0% on Yellowstone↔Yosemite, +0.2% / -3.7% on Grand Teton→Arches, 0.0% / 0.0% on Zion→Grand Canyon); 17/17 unit tests pass

### Tier 1 result vs Olson 2015

| Metric | Olson 2015 | Optitrek (capped & uncapped) | Delta |
|---|---|---|---|
| Stops | 50 | 49 | -1 |
| Total drive time | 224 h | 193.0 h | **-13.8%** |
| Total distance | 13,699 mi | 9,744 mi (capped), 9,756 mi (uncapped) | **-28.9%** |

Capped and uncapped converged on essentially the same 49-stop solution — the constrained-set-cover-TSP problem has a clean optimum at exactly 49 stops on the filtered network.

### How to re-run Tier 1 (after this session)

```bash
# From WSL Ubuntu (Docker Desktop currently broken on BRONTOSAURUS,
# so docker compose is not available; use the orchestration script):
cd /mnt/e/dev/optitrek
./scripts/run_tier1_local.sh
```

The script handles: docker run osrm-routed, wait for ready, spot-check
against full-network ground truth, build matrix, solve both modes, render,
cleanup. Reads from `/root/venvs/optitrek-wsl/` (the WSL Python venv).

### How to rebuild OSRM artifacts from scratch

(only needed if Geofabrik publishes a new US extract, or if data/osrm-major/ is deleted)

```bash
# From WSL Ubuntu:
cd /mnt/e/dev/optitrek
curl -L -C - -o data/us-latest.osm.pbf https://download.geofabrik.de/north-america/us-latest.osm.pbf
./scripts/filter_pbf.sh data/us-latest.osm.pbf data/osrm-major/us-major.osm.pbf
OSRM_THREADS=6 ./scripts/build_osrm.sh data/osrm-major/us-major.osm.pbf data/osrm-major us-major
```

### Known follow-ups (not Tier 1 blockers)

- 79 of 438 POIs have >10% unreachable pairs on the major-roads network
  (likely remote backcountry units). Solver routed around them; no broken
  legs. Worth investigating which parks specifically — could widen filter
  to add `unclassified` for those, or drop them from the candidate set.
- `data/osrm/` (72 GB full-network archive from the destroyed GCP VM) is
  now unused on E:. Safe to delete; rebuildable from PBF in ~20 min.
- Docker Desktop on BRONTOSAURUS is broken (stale socket files in
  Inference Manager + Secrets Engine). Worked around via WSL-native docker
  and an inline orchestration script. Fixable separately.

### Next: Tier 1 Phase 5 — the blog post

See `03-OPTITREK-TIER1-PROJECT-DOC.md` Phase 5 for the writeup brief.
~1 day of content work; no more code changes needed for Tier 1.

---

## Earlier today (artifact migration + BSOD-driven WSL cap)

**Last updated:** 2026-05-21 ~3:45 PM Eastern (build complete + validated + project migrated)

## TL;DR — DONE

- Build on GCP VM **completed successfully** overnight 2026-05-20 → 2026-05-21
- Artifacts transferred to BRONTOSAURUS, then **migrated to `E:\dev\optitrek\`** (Dev Drive — much faster than C:)
- **18 .osrm.* artifact files** at `E:\dev\optitrek\data\osrm\` (~72.5 GB)
- **Tests 17/17 passing** on the new E: location, venv rebuilt with Python 3.14.3
- **Live routing validated** against the VM via SSH tunnel — 8-leg Western parks loop renders correctly (`output/osrm_visual_proof.html`)
- **VM scheduled for destruction** — see `output/osrm_visual_proof.html` for the visual proof before pulling the plug

## What was actually built

| Phase | Result |
|---|---|
| Download `us-latest.osm.pbf` | ~10 GB, completed |
| `osrm-extract` | OOM'd at 65 GB the first time → succeeded after 32 GB swap added |
| `osrm-partition` | Completed |
| `osrm-customize` (MLD) | Completed — produced `us-latest.osrm.cell_metrics` (the big one) + `us-latest.osrm.mldgr` |
| Transfer to BRONTOSAURUS | Initially to C:, then robocopy'd to E:\dev\optitrek\ |

## Verification (2026-05-21)

Two-stage verification:

1. **Local osrm-routed attempt: FAILED with BSOD.** `osrm-routed` inside WSL Ubuntu (Docker daemon, not Docker Desktop) on BRONTOSAURUS pinned at 28.6 GB / 29.4 GB WSL working set, plus `com.docker.backend.exe` consuming 100-131 GB virtual memory. After ~30 minutes of page-file thrash, the Windows kernel crashed: bug check 0x00000001 (APC_INDEX_MISMATCH), minidump `C:\Windows\Minidumps\052126-17421-01.dmp`. **This confirmed the memory-ceiling memory entry** ([[brontosaurus-osrm-memory-ceiling]]) — BRONTOSAURUS's 32 GB physically cannot run osrm-routed on the full US extract.

2. **VM-side osrm-routed: PASSED.** Spun up the same `osrm/osrm-backend:latest` container on the GCP VM (e2-highmem-8, 64 GB RAM), ran 8 route queries via SSH tunnel from BRONTOSAURUS. All 8 returned `code: Ok` with realistic distances/durations and OSM road names snapped correctly to actual park-access roads (Grand Loop Rd, Tioga Rd, Kolob Terrace Rd, Mosaic Canyon Rd, etc.). Total loop: 2,800 mi / 59.3 h. Visual map: `output/osrm_visual_proof.html`.

## What changed on BRONTOSAURUS today

- Migrated `C:\Users\mhowe\Desktop\optitrek\` → `E:\dev\optitrek\` via robocopy (186 files, 20.7 MB)
- Rebuilt `.venv` at new location (`E:\dev\optitrek\.venv\`, Python 3.14.3)
- Lowered WSL2 memory cap in `%USERPROFILE%\.wslconfig` from `memory=30GB` to `memory=24GB` (defense-in-depth — guarantees we can never re-trigger today's BSOD)
- Updated `scripts/run_build_osrm.sh` paths to reference E: drive
- Added `scripts/visual_proof.py` for end-to-end OSRM smoke test

## Decisions for next session

- Run `osrm-routed` **on the VM, not BRONTOSAURUS** — either keep this VM around or spin up a fresh one when the matrix builder needs OSRM
- For the actual Tier 1 matrix build (Phase 2), options:
  1. **Recreate the VM** when needed, route through SSH tunnel — what we did today
  2. **Tag-filter the PBF** to major-roads-only (~2-3 GB) so artifacts fit BRONTOSAURUS's 24 GB cap — see [[brontosaurus-osrm-memory-ceiling]] option 1
  3. **Spin up a cheaper VM** (e2-highmem-4, 32 GB) just for runtime (build needs 64 GB, runtime fits in less if you skip cell_metrics by using `--algorithm CH` instead of MLD)

## Files / state

- `E:\dev\optitrek\data\osrm\` — 18 `.osrm.*` artifact files (72.5 GB)
- `E:\dev\optitrek\output\osrm_visual_proof.html` — interactive Folium map, today's proof
- `E:\dev\optitrek\scripts\visual_proof.py` — script that built the map (re-runnable any time OSRM is up)
- `C:\Users\mhowe\.ssh\id_ed25519` — SSH key for the VM (still valid until VM is destroyed)
- `C:\Users\mhowe\Desktop\optitrek\` — **stale**, safe to delete after closing the current Claude session
