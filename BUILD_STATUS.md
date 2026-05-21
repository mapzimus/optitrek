# OSRM US Build — Status Snapshot

**Last updated:** 2026-05-21 ~6:20 PM Eastern — Tier 1 pipeline complete

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
