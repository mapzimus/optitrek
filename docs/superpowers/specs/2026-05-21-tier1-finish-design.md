# Tier 1 Finish — Implementation Design

**Date:** 2026-05-21
**Status:** Approved (verbal, this session)
**Author:** Max + Claude
**Estimated effort:** ~45 min setup + ~10 min run = ~1 hour total active work
**Predecessor docs:** `HANDOVER.md`, `DECISIONS.md`, `BUILD_STATUS.md`,
`brontosaurus-osrm-memory-ceiling.md` (memory entry)

---

## 1. Purpose

Produce the two Tier 1 output artifacts — `output/optitrek_capped.html` and
`output/optitrek_uncapped.html` — by running the existing pipeline end-to-end
against a locally-hosted OSRM instance on BRONTOSAURUS.

This is the LAST tactical step before Tier 1 Phase 5 (the blog post).

---

## 2. Context

All Tier 1 code already exists in the repo (`src/matrix_builder.py`,
`src/solver.py`, `src/run_tier1.py`, `src/visualize.py`). 17/17 unit tests
pass. Phase 1 ran live and populated Neon Postgres with 466 NPS POIs
covering all 49 required zones (48 contiguous states + DC).

The only missing input is a `data/matrix/{pois,duration,distance}.parquet`
matrix triple, which requires a reachable OSRM instance to build. Today
we verified an OSRM instance built on a GCP VM serves correct results
(visual proof at `output/osrm_visual_proof.html`), then destroyed the VM
to stop billing.

**Constraint discovered today:** BRONTOSAURUS (32 GB RAM) cannot run
`osrm-routed` on the full US extract — attempting to do so caused a BSOD
(bug check 0x1 APC_INDEX_MISMATCH, minidump
`C:\Windows\Minidumps\052126-17421-01.dmp`). See
`brontosaurus-osrm-memory-ceiling.md` for full incident detail.

**Decision (this brainstorm):** filter the input PBF to major roads only
before building OSRM artifacts. The resulting artifact set (~8-12 GB)
fits comfortably under the now-24 GB WSL memory cap.

---

## 3. Pipeline

```
┌──────────────────────────────────────────────────────────────────┐
│ ONE-TIME SETUP (~30-45 min total, BRONTOSAURUS)                  │
├──────────────────────────────────────────────────────────────────┤
│ 1. Install osmium-tool via Conda (Miniforge3 already present)    │
│ 2. Download us-latest.osm.pbf from Geofabrik (~10 GB, ~10 min)   │
│ 3. osmium tags-filter to major roads → us-major.osm.pbf (~2-3 GB)│
│ 4. osrm-extract / osrm-partition / osrm-customize on filtered    │
│    PBF → ~8-12 GB of artifacts at data/osrm-major/               │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ TIER 1 RUN (~10 min total, fully local, repeatable)              │
├──────────────────────────────────────────────────────────────────┤
│ 5. docker compose up osrm  (using data/osrm-major/)              │
│ 6. python -m src.matrix_builder                                  │
│       → data/matrix/{pois,duration,distance}.parquet             │
│ 7. python -m src.run_tier1                                       │
│       → output/optitrek_capped.html                              │
│       → output/optitrek_uncapped.html                            │
│       + Olson comparison printed to stdout                       │
│ 8. docker compose down osrm                                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Components

| Component | Status | Action this session |
|---|---|---|
| `scripts/filter_pbf.sh` | NEW | Create — osmium tags-filter wrapper (~20 lines) |
| `scripts/build_osrm.sh` | EXISTS | Edit to accept a PBF path argument and an output dir; default to filtered |
| `docker-compose.yml` | EXISTS | Edit `volumes:` to mount `data/osrm-major/` |
| `data/osrm-major/` | NEW | Created by step 4 |
| `data/matrix/` | EXISTS (empty) | Populated by step 6 |
| `src/matrix_builder.py` | EXISTS | Run as-is |
| `src/run_tier1.py` | EXISTS | Run as-is |
| `src/solver.py` | EXISTS | Used by run_tier1 |
| `src/visualize.py` | EXISTS | Used by run_tier1, hits OSRM /route |
| `output/optitrek_*.html` | NEW | Produced by step 7 |

The full-network artifact set at `data/osrm/` (72.5 GB, built on the
destroyed VM) is kept for now as ground-truth reference. May be deleted
after Tier 1 ships and we're confident in the filtered network.

---

## 5. Configuration choices

### 5.1 Road-type filter

Major-roads filter for osmium tags-filter:

```
w/highway=motorway,trunk,primary,secondary,tertiary
w/highway=motorway_link,trunk_link,primary_link,secondary_link,tertiary_link
```

This is the standard "long-distance routable network" cut. Excludes
`unclassified`, `residential`, `service`, `track`, `path`, etc.

**Why this set:** every NPS park has at least one road of these classes
within a few miles of its main entrance. Today's visual proof showed
OSRM snapping the Arches park-center coordinate to a 4WD jeep road, then
routing onto US-191 — with the filter applied, the snap will jump
directly to US-191 (no intermediate jeep-road hop), but the actual
inter-park leg distance changes by only the ~5 mi of approach road.

### 5.2 Paths and naming

- `data/osrm-major/us-major.osrm` — main graph file produced by
  osrm-extract on filtered PBF
- `data/osrm-major/us-major.osm.pbf` — intermediate filtered PBF (kept
  for inspection / reproducibility; ~2-3 GB)
- `data/us-latest.osm.pbf` — raw Geofabrik download (~10 GB; can be
  deleted after step 4 finishes successfully)

### 5.3 WSL memory cap

Already set to 24 GB this session (`%USERPROFILE%\.wslconfig`).
Filtered-network osrm-routed peak working set expected to be ~8-12 GB.

### 5.4 Solver time limit

Default 300s per mode (capped + uncapped), configurable via
`OPTITREK_TIME_LIMIT` env var. 300s × 2 modes = 10 min worst case.

### 5.5 Depot

Default `OPTITREK_DEPOT_INDEX=0` (first row in pois.parquet, sorted by
`(state, id)` → likely an Alabama park). Per HANDOVER, depot choice
doesn't affect symmetric TSP cost.

---

## 6. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Some NPS unit unreachable on major-roads-only network | Medium | `validate_matrix()` already flags rows with >10% bad pairs. If any flagged, widen filter to include `unclassified`. Re-run from step 4. |
| Filtered network gives meaningfully wrong distances | Low | Spot-check 3 routes from today's `osrm_visual_proof.html` against the filtered build. Tolerance: 5% distance, 10% duration. |
| osrm-extract OOMs on filtered PBF | Very low | 2-3 GB PBF is far below the 24 GB cap. If it happens, lower `--threads` parameter. |
| Geofabrik download interrupted | Low | Use `curl -C -` (resume) in build script. |
| Conda not available / osmium-tool install fails | Low | User has Miniforge3 per global instructions. Fallback: download standalone osmium binary from osmcode.org. |
| Some NPS row has `state IS NULL` | Very low | matrix_builder already filters this in its WHERE clause. |
| Neon connection times out during matrix build | Low | Build only reads POIs once at the start (Postgres call is ~1 sec). |

---

## 7. Validation plan

Performed after step 7 completes:

1. **Tests still pass:** `pytest tests/` → 17/17
2. **Matrix sanity:** `validate_matrix()` output shows 0 rows above 10%
   bad pairs (printed by matrix_builder.py at end)
3. **Three-route spot check** against today's full-network visual proof.
   The "Filtered network" column is measured after step 7 and inserted here
   (these are forward placeholders, not missing requirements):
   | Route | Full network (today) | Filtered network | Tolerance |
   |---|---|---|---|
   | Yellowstone → Yosemite | 907.7 mi / 17.62 h | _(measured at step 7)_ | ±5% dist / ±10% dur |
   | Grand Teton → Arches | 513.5 mi / 11.04 h | _(measured at step 7)_ | ±5% dist / ±10% dur |
   | Zion → Grand Canyon | 272.0 mi / 6.15 h | _(measured at step 7)_ | ±5% dist / ±10% dur |
4. **Visual review of both HTML outputs:**
   - `optitrek_capped.html`: exactly 49 markers, each a different state
   - `optitrek_uncapped.html`: ≥49 markers, all required states present
   - Both: loop is geographically continuous (no transatlantic jumps),
     Olson comparison panel shows results in same ballpark or better
     than 224 h / 13,699 mi

If any validation step fails, stop and triage before declaring done.

---

## 8. Out of scope (explicitly)

- **Blog post writing** (HANDOVER Phase 5) — separate ~1-day task
- **Tier 2 / Tier 3 features** — separate project docs (`05-`/`06-`)
- **Multi-region / Alaska / Hawaii routing** — excluded per DECISIONS.md
- **Bicycle / walking profiles** — Tier 1 is driving-only
- **Deleting `data/osrm/` (72 GB full-network archive)** — defer until
  after Tier 1 ships and filtered network is proven equivalent
- **Cleaning up the stale CockroachDB plugin hook** noise — separate
  global Claude Code config issue
- **Auto-recreating the GCP VM** for re-validation — only if filtered
  validation fails

---

## 9. Success criteria

Tier 1 finish is complete when ALL of the following are true:

- [ ] `data/osrm-major/` contains the filtered OSRM artifact set
- [ ] `docker compose up osrm` serves /route + /table successfully against it
- [ ] `data/matrix/{pois,duration,distance}.parquet` exist and load without error
- [ ] `output/optitrek_capped.html` and `output/optitrek_uncapped.html` exist
- [ ] Validation steps 1-4 (Section 7) all pass
- [ ] `BUILD_STATUS.md` updated to "Tier 1 done; ready for blog post"
- [ ] Changes committed to the working repository at `E:\dev\optitrek`
  (currently on branch `main` after this session's migration). The
  prior C:\ worktrees on `claude/epic-pascal-743577` and siblings remain
  at their last commit and are now stale.
