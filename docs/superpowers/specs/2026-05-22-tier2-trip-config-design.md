# Tier 2 Phase 1 — TripConfig Pipeline (cover-all mode + full filters)

**Date:** 2026-05-22
**Status:** Approved (verbal, this session)
**Author:** Max + Claude
**Estimated effort:** ~2-3 days of implementation
**Predecessor specs:** `05-OPTITREK-TIER2-PROJECT-DOC.md` (overall Tier 2 spec)
**Predecessor result:** Tier 1 complete — 49-stop NPS loop, 193.0 h / 9,744 mi,
beats Olson 2015 by 14% time / 29% miles. See [`2026-05-21-tier1-finish-design.md`](2026-05-21-tier1-finish-design.md).

---

## 1. Purpose

Generalize the Tier 1 solver pipeline to accept a `TripConfig` dataclass,
enabling user-configurable trips (categories, states, must-include stops,
max stops, max radius, start state, loop vs point-to-point). Establishes
the API and code structure that the Tier 2 web app will consume in
Phase 2-5; produces immediate portfolio-worthy demos (regional sub-tours,
category-filtered loops) without requiring the full ~100k POI database
expansion.

**Tier 1 still works unchanged after this.** All new surface area is
additive in new files, with minimal extension points added to existing
modules.

---

## 2. Scope

### In scope (this spec)
- New `TripConfig` dataclass with 10 fields (see §4)
- YAML config loader + validation
- POI filter pipeline (`fetch_pois(config)`)
- Solver wrapper (`solve_with_config(config, pois, matrices)`) with new
  constraints: must_include, max_stops, max_radius, start_state, loop
- Daily leg splitting (post-process) + color-by-day Folium rendering
- Top-level orchestrator (`src/trip.py`)
- CLI entry point (`scripts/run_trip.py`)
- Tier 1 correctness oracle (`trips/tier1_replica.yaml` reproduces known result)
- Unit tests for each new module + extended solver tests

### Out of scope (Tier 2 Phase 2+)
- Time-budgeted solver mode (`total_trip_days`, `category_priority` fields
  are defined in TripConfig but accepted-and-ignored in this phase)
- Priority-weighted optimization objective
- FastAPI backend, web UI, Railway deployment
- Database expansion (still using ~438 NPS POIs from Tier 1)
- Overnight stop suggestions, Amtrak routing

---

## 3. Pipeline

```
trips/<name>.yaml ──► TripConfig (validated, frozen dataclass)
                          │
                          ▼
                 fetch_pois(config) ──► PostGIS query with WHERE-filters
                          │ (list[dict] of N rows)
                          ▼
              build_matrix_for_pois(pois) ──► OSRM /table (batched)
                          │ (duration_s + distance_m, both N×N float32)
                          ▼
       solve_with_config(config, pois, durations, distances) ──► OR-Tools
                          │ (SolveResult with .order, .leg_costs, .status)
                          ▼
                 split_into_days(result, max_hours)
                          │ (list of day-indexed stop lists)
                          ▼
                  render_map(result, days, pois) ──► Folium HTML
```

Each stage is independently testable; mocks at each boundary.

---

## 4. TripConfig

```python
# src/config.py
@dataclass(frozen=True)
class TripConfig:
    name: str = "untitled"

    # Filters (None = no filter)
    categories: list[str] | None = None       # e.g. ["nps_park"] for parks-only
    states: list[str] | None = None           # e.g. ["NM","AZ","UT","NV","CO"]
    max_radius_miles: float | None = None     # requires start_state

    # Required visits
    must_include: list[int] = field(default_factory=list)  # POI IDs

    # Cardinality
    max_stops: int | None = None              # must be >= num_required_states

    # Routing shape
    start_state: str | None = None
    loop: bool = True

    # Display (post-process)
    max_hours_per_day: float = 8.0

    # Solver
    time_limit_seconds: int = 300

    # ---- Deferred to Tier 2 Phase 2; accepted-but-unused in Phase 1 ----
    category_priority: dict[str, int] = field(default_factory=dict)
    total_trip_days: int | None = None
```

### YAML format

YAML keys map 1:1 to dataclass fields. Defaults are omitted.

```yaml
# trips/southwest_parks.yaml
name: southwest_parks_loop
states: [NM, AZ, UT, NV, CO]
categories: [nps_park]
loop: true
```

```yaml
# trips/tier1_replica.yaml — correctness oracle
name: tier1_replica
# all 49 zones, all NPS categories — exactly what Tier 1 ran
states: [AL, AR, AZ, CA, CO, CT, DC, DE, FL, GA, IA, ID, IL, IN, KS, KY,
         LA, MA, MD, ME, MI, MN, MO, MS, MT, NC, ND, NE, NH, NJ, NM, NV,
         NY, OH, OK, OR, PA, RI, SC, SD, TN, TX, UT, VA, VT, WA, WI, WV, WY]
loop: true
time_limit_seconds: 300
```

### Validation rules (raise on load)

- `max_radius_miles` set → `start_state` must also be set
- `max_stops` set → `max_stops >= num_required_states` (else infeasible).
  `num_required_states` = `len(states)` + `len(must_include)` minus any
  overlap (a must_include POI in a required state covers both).
- `start_state` set → `start_state ∈ states` (if `states` is also set)
- `must_include` POI IDs must exist in the database (deferred to fetch_pois)
- `categories` entries: the values in `categories` filter match the DB's
  `category` column verbatim. Use `SELECT DISTINCT category FROM pois
  WHERE source='nps'` to enumerate valid values. If the DB hasn't been
  refined per `04-OPTITREK-DATABASE-EXPANSION-SPEC.md` §4.2 yet, the
  values will be the raw NPS designation strings (e.g., "National Park",
  "National Monument") rather than the normalized `nps_park`/`nps_monument`
  taxonomy. Plan resolves which form to use.

---

## 5. Module changes

### New files

```
src/config.py            — TripConfig dataclass + YAML loader + validators
src/poi_query.py         — fetch_pois(config) -> list[dict] from PostGIS
src/trip.py              — top-level pipeline orchestrator
scripts/run_trip.py      — CLI: python -m scripts.run_trip <config.yaml>
trips/tier1_replica.yaml — Tier 1 settings reproduction (correctness oracle)
trips/southwest_parks.yaml — example Tier 2 demo (5-state parks-only loop)
tests/test_config.py     — config + YAML + validation tests
tests/test_poi_query.py  — SQL generation tests with mocked cursor
tests/test_trip.py       — end-to-end pipeline test with mocked POI + matrix
```

### Extended files (additive only)

```
src/matrix_builder.py    — factor out build_matrix_for_pois(pois) from existing main()
src/solver.py            — add solve_with_config(config, pois, dur, dist) wrapper
                           + private helpers for must_include, max_stops, loop=False
src/visualize.py         — add split_into_days(result, hours_per_day)
                           + extend render_map(..., day_colors=...) for color-by-day
tests/test_solver.py     — add tests for must_include, max_stops penalty, open-path
```

### Untouched

- `src/data_pull.py`, `src/spatial_join.py`, `src/db.py` — Tier 1 data pipeline
- `src/run_tier1.py` — Tier 1 entry point still works as-is
- `tests/test_data_pull.py`, `tests/test_visualize_smoke.py` — Tier 1 tests
- All Tier 1 scripts in `scripts/` (`build_osrm.sh`, `run_tier1_local.sh`, etc.)

---

## 6. Constraint implementations

### 6.1 must_include

Each must-include POI ID becomes a **hard constraint** via
`routing.solver().Add(routing.ActiveVar(node_index) == 1)`. This is a
different OR-Tools mechanism than the per-state disjunctions used for
coverage: disjunctions allow skipping with a penalty, `ActiveVar` forbids
skipping outright.

The must-include POI **keeps its real state label** in the solver, so it
also counts toward that state's disjunction (a must-include POI in
California satisfies `CA` coverage — the solver does not need to visit a
second California POI just to cover the state).

**Why not pseudo-zones (the pattern from `olson_control.py`)?** Those
scripts forced ALL nodes to be visited by giving each node a unique
pseudo-state. That works when every node is required, but for `must_include`
we want a SUBSET of nodes required while the rest remain optional. Using a
pseudo-state `MUST_<id>` would prevent the must-include POI from
contributing to its real state's coverage, forcing the solver to visit a
second POI in the same state to satisfy coverage — wrong behavior.
`ActiveVar` is the right primitive: it adds a "must visit" constraint
without touching the state-coverage disjunctions.

### 6.2 max_stops

Three semantic cases, with explicit solver behavior for each:

| Condition | Solver mode | Bound enforcement |
|---|---|---|
| `max_stops is None` | uncapped | no upper bound; solver may add any number of optional visits |
| `max_stops == num_required_states` | capped (Tier 1 default) | exactly one stop per required zone |
| `num_required_states < max_stops` | uncapped, bounded | solver may add up to (max_stops − num_required_states) extras |
| `max_stops < num_required_states` | — | config rejected at validation time (infeasible) |

The "uncapped, bounded" case is the genuinely tricky one. OR-Tools'
disjunction with `max_cardinality` controls per-zone visit cardinality, not
GLOBAL solution cardinality. The implementation plan resolves this with one
of:

- **Soft penalty per extra visit** beyond `num_required_states` (penalty
  magnitude tuned to make additional stops only worthwhile if they shorten
  the loop by more than the penalty), plus a hard post-validation that the
  returned tour has ≤ `max_stops` stops.
- **Pre-filter candidates** to at most `max_stops` POIs by dropping optional
  (non-required) candidates greedily, with a heuristic for which to keep
  (proximity to required POIs, or random for determinism).

Spec commits to: "the returned tour has between `num_required_states` and
`max_stops` stops (inclusive)." Plan picks the mechanism and tunes it.

### 6.3 max_radius
SQL `ST_DWithin` filter in `fetch_pois`, NOT a solver constraint. Center
point: centroid of POIs in `start_state` (computed via
`ST_Centroid(ST_Collect(geom))` in the WHERE clause's subquery, since we
don't have state polygons in this DB — but we do have POIs and their
centroid is a decent proxy for "regional center").

### 6.4 start_state
Depot selection priority:

1. If `must_include` has a POI in `start_state` → that POI is depot
2. Else first POI in `start_state` after `ORDER BY state, id` (deterministic)
3. If `start_state` is None → `pois[0]` (matches Tier 1 behavior)

### 6.5 loop=False (open path)
OR-Tools setup change: override the arc cost for the return edge
(last_visited_node → depot) to 0 via
`routing.GetMutableDimension(...).SetSpanCostCoefficientForVehicle(0)` or
equivalent. Effectively: solver still solves a cycle internally but the
cost is the path, not the loop.

The rendered map drops the closing polyline segment (last marker → first
marker). Daily leg splitting still works.

**Note:** the exact OR-Tools incantation is an implementation detail the
plan resolves. The spec commits to the behavior, not the API.

### 6.6 Daily leg splitting (post-process)
```python
def split_into_days(result: SolveResult, max_hours: float) -> list[list[int]]:
    """Walks leg_costs in visit order, opens a new day when adding the next
    leg would exceed max_hours. Returns list of day-indexed stop lists."""
    days: list[list[int]] = [[0]]   # depot is day 0, stop 0
    today_hours = 0.0
    for i, leg_seconds in enumerate(result.leg_costs):
        leg_hours = leg_seconds / 3600.0
        if today_hours + leg_hours > max_hours and days[-1]:
            days.append([])
            today_hours = 0.0
        days[-1].append(i + 1)  # next stop index
        today_hours += leg_hours
    return days
```

**Edge case:** a single leg longer than `max_hours_per_day` becomes its own
day with no split (you'd need an overnight stop midway; Tier 3 problem).

**Visualization:** ColorBrewer Set1 palette (9 distinct colors) for trips
of up to 9 days; Set3 (12 colors) for longer. Each day's polyline gets
its color; each marker carries a "Day N" tooltip.

---

## 7. Testing plan

| Layer | File | Coverage |
|---|---|---|
| Config | `tests/test_config.py` | YAML load roundtrip, defaults, validation rules (radius-needs-start-state, max_stops feasibility, start_state-in-states) |
| Query | `tests/test_poi_query.py` | SQL generation for each filter combo with a mocked psycopg cursor; assert WHERE clauses and parameter dict |
| Solver | `tests/test_solver.py` (extended) | Hand-crafted 5-10 node graphs: (a) must_include forces visit of an off-route node; (b) max_stops penalty respected when many candidates per state; (c) loop=False produces shorter cost than loop=True on the same graph |
| Pipeline | `tests/test_trip.py` | End-to-end with mocked `fetch_pois` and mocked matrix; one happy path, one config-validation failure |
| **Oracle** | `scripts/test_tier1_replica.py` | Loads `trips/tier1_replica.yaml`, runs full pipeline against real DB + OSRM, asserts result is within 1% of 193 h / 9,744 mi |

Existing tests must continue to pass (17/17). The `pytest tests/` invocation
should grow to roughly 25-30 passing tests after this work.

The Tier 1 replica test is the strongest correctness signal: it requires real
DB + real OSRM, so it's a separate integration step that runs via
`run_tier1_local.sh`-style orchestration, not part of `pytest tests/`.

---

## 8. Success criteria

Tier 2 Phase 1 is complete when ALL of the following are true:

- [ ] `src/config.py`, `src/poi_query.py`, `src/trip.py` exist and have tests
- [ ] `scripts/run_trip.py trips/tier1_replica.yaml` reproduces the Tier 1 result
      (within solver-randomness tolerance: ±2% on time and miles)
- [ ] `scripts/run_trip.py trips/southwest_parks.yaml` produces a valid 5-state
      parks-only loop (no states from outside [NM,AZ,UT,NV,CO]; visits all 5)
- [ ] Existing 17 tests still pass; ~10-15 new tests added covering new modules
- [ ] Daily leg splitting renders multi-colored polylines on the output map
- [ ] At least one new gallery-worthy map produced and copied to `gallery/`
      (suggest: `gallery/08_southwest_parks.html`)
- [ ] `BUILD_STATUS.md` updated to "Tier 2 Phase 1 complete"
- [ ] Commits on `main` at `E:\dev\optitrek`

---

## 9. Known risks / open questions

- **`SetArcCostEvaluator` for the return edge** (loop=False) — OR-Tools' API
  for "drop the return cost" is non-obvious. The plan needs to resolve this
  with a small test program before committing to the implementation.
- **Centroid-of-POIs as `start_state` center for max_radius** — works fine
  for states with many POIs, weak for states with one or two POIs (centroid
  ≈ that POI, radius filter becomes "within R miles of that POI"). Acceptable
  approximation; document the caveat.
- **YAML library choice** — `pyyaml` is heavy but standard. Alternative: write
  configs as plain Python in `trips/<name>.py` modules. Spec commits to YAML
  but plan can revisit if pyyaml adds noticeable startup cost.
- **Tier 1 replica reproducibility** — OR-Tools' metaheuristic has some
  non-determinism. The ±2% tolerance accommodates this, but if the new
  pipeline systematically produces worse results than Tier 1 (e.g., 5%+
  worse), that's a refactor bug, not solver noise.
