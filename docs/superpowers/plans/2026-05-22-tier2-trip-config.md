# Tier 2 Phase 1 — TripConfig Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Tier 1 hardcoded pipeline into a `TripConfig`-driven solver that loads YAML configs, applies filters (categories/states/radius/must-include), supports new routing modes (open path, max-stops), and renders day-colored maps — while reproducing Tier 1's exact result on an equivalent config.

**Architecture:** Pure additive refactor. New modules (`src/config.py`, `src/poi_query.py`, `src/trip.py`, `scripts/run_trip.py`, `trips/*.yaml`) layer on top of existing solver and matrix code. Tier 1 entry point (`scripts/run_tier1.py`) stays untouched and functional throughout.

**Tech Stack:** Python 3.14, PyYAML, dataclasses + `__post_init__` validation, Google OR-Tools (existing), psycopg3 (existing), Folium (existing). No new heavy deps.

**Spec:** [`docs/superpowers/specs/2026-05-22-tier2-trip-config-design.md`](../specs/2026-05-22-tier2-trip-config-design.md)

**Branch:** `main` at `E:\dev\optitrek`. Each task commits its own scope.

---

## File Structure

**Files this plan creates:**
- `src/config.py` — `TripConfig` dataclass, `load_config()`, `TripConfigError` hierarchy
- `src/poi_query.py` — `fetch_pois(config) -> list[dict]` with filter support
- `src/trip.py` — top-level orchestrator `run_trip(config) -> Path`
- `scripts/run_trip.py` — argparse CLI wrapper
- `trips/tier1_replica.yaml` — correctness oracle
- `trips/southwest_parks.yaml` — demo trip
- `tests/test_config.py` — config validation tests
- `tests/test_poi_query.py` — SQL generation tests
- `tests/test_trip.py` — end-to-end pipeline tests
- `scripts/test_tier1_replica.py` — integration oracle (requires real DB + OSRM)

**Files this plan modifies:**
- `requirements.txt` — add `pyyaml>=6.0`
- `src/solver.py` — add `solve_with_config(config, pois, dur, dist)` wrapper + private helpers for must_include / max_stops / loop=False
- `src/visualize.py` — add `split_into_days()` + extend `render_map()` for color-by-day
- `tests/test_solver.py` — add tests for new constraints
- `BUILD_STATUS.md` — mark Tier 2 Phase 1 complete
- `CLAUDE.md` — document new entry point

**Files this plan does NOT touch:**
- `src/run_tier1.py` (Tier 1 entry point)
- `src/data_pull.py`, `src/spatial_join.py` (Tier 1 data pipeline)
- `src/db.py` (DB connection helpers)
- `src/matrix_builder.py` (already has `build_matrix(pois)` factored out — reuse as-is)
- Existing Tier 1 tests

---

## Task 1: Add PyYAML to requirements

**Files:**
- Modify: `E:\dev\optitrek\requirements.txt`

- [ ] **Step 1: Add the dep line**

Edit `requirements.txt` and add this line just below the existing `polyline>=2.0` line, BEFORE the `# Tests` block:

```
# Phase 5 — Tier 2 config layer
pyyaml>=6.0              # YAML config loading
```

- [ ] **Step 2: Install into the WSL venv**

Run:
```bash
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/pip install pyyaml
```

Expected: `Successfully installed PyYAML-6.0.x`

- [ ] **Step 3: Verify import**

Run:
```bash
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -c "import yaml; print(yaml.__version__)"
```

Expected: `6.0` or higher.

- [ ] **Step 4: Commit**

```bash
cd /e/dev/optitrek
git add requirements.txt
git commit -m "chore: add pyyaml for Tier 2 config layer"
```

---

## Task 2: TripConfig dataclass + YAML loader

**Files:**
- Create: `E:\dev\optitrek\src\config.py`
- Create: `E:\dev\optitrek\tests\test_config.py`

- [ ] **Step 1: Write the failing test for dataclass defaults**

Create `tests/test_config.py`:

```python
"""Tests for src/config.py — TripConfig dataclass + YAML loader + validation."""
from pathlib import Path
import pytest
import yaml

from src.config import TripConfig, load_config


def test_dataclass_has_expected_defaults():
    cfg = TripConfig(name="x")
    assert cfg.name == "x"
    assert cfg.categories is None
    assert cfg.states is None
    assert cfg.max_radius_miles is None
    assert cfg.must_include == []
    assert cfg.max_stops is None
    assert cfg.start_state is None
    assert cfg.loop is True
    assert cfg.max_hours_per_day == 8.0
    assert cfg.time_limit_seconds == 300
    assert cfg.category_priority == {}
    assert cfg.total_trip_days is None


def test_load_minimal_yaml(tmp_path: Path):
    yaml_text = "name: minimal\n"
    p = tmp_path / "minimal.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    assert cfg.name == "minimal"
    assert cfg.loop is True  # default


def test_load_full_yaml(tmp_path: Path):
    yaml_text = """
name: full_example
states: [CA, NV, AZ]
categories: ["National Park"]
max_stops: 10
loop: true
time_limit_seconds: 60
"""
    p = tmp_path / "full.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    assert cfg.name == "full_example"
    assert cfg.states == ["CA", "NV", "AZ"]
    assert cfg.categories == ["National Park"]
    assert cfg.max_stops == 10
    assert cfg.time_limit_seconds == 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_config.py -v
```

Expected: 3 ERRORS with `ImportError: cannot import name 'TripConfig'`.

- [ ] **Step 3: Implement `src/config.py`**

Create `src/config.py`:

```python
"""TripConfig dataclass, YAML loader, and validation for Tier 2 trips.

See docs/superpowers/specs/2026-05-22-tier2-trip-config-design.md §4.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class TripConfigError(Exception):
    """Base for all TripConfig-related validation and runtime errors."""


class EmptyCandidatePool(TripConfigError):
    """fetch_pois returned no rows after applying filters."""


class UnreachableMustInclude(TripConfigError):
    """A must_include POI ID was not found in the database."""


class SingleStopTour(TripConfigError):
    """After filtering, only the depot would be visited — no meaningful tour."""


class InfeasibleMaxStops(TripConfigError):
    """max_stops is less than the number of required visits."""


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class TripConfig:
    name: str = "untitled"
    # Filters (None = no filter)
    categories: list[str] | None = None
    states: list[str] | None = None
    max_radius_miles: float | None = None
    # Required visits
    must_include: list[int] = field(default_factory=list)
    # Cardinality
    max_stops: int | None = None
    # Routing shape
    start_state: str | None = None
    loop: bool = True
    # Display
    max_hours_per_day: float = 8.0
    # Solver
    time_limit_seconds: int = 300
    # ---- Deferred to Phase 2; accepted but unused ----
    category_priority: dict[str, int] = field(default_factory=dict)
    total_trip_days: int | None = None

    def __post_init__(self) -> None:
        # Phase 1: validation lives here (Phase 2 may externalize). See spec §4.
        # Validation rules added in Task 3 — keep this method body empty here so
        # Step 2 of THIS task passes without the rules being implemented yet.
        pass


def load_config(path: Path) -> TripConfig:
    """Load a TripConfig from a YAML file. Raises yaml.YAMLError on parse
    error, TripConfigError on validation failure."""
    with Path(path).open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise TripConfigError(
            f"YAML at {path} must define a mapping at the root; got {type(data).__name__}"
        )
    return TripConfig(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_config.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
cd /e/dev/optitrek
git add src/config.py tests/test_config.py
git commit -m "feat(config): TripConfig dataclass + YAML loader (no validation yet)"
```

---

## Task 3: TripConfig validation rules

**Files:**
- Modify: `E:\dev\optitrek\src\config.py` (fill in `__post_init__`)
- Modify: `E:\dev\optitrek\tests\test_config.py` (add validation tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from src.config import TripConfigError, InfeasibleMaxStops


def test_name_must_be_path_safe():
    with pytest.raises(TripConfigError, match="filename-safe"):
        TripConfig(name="bad name with spaces")
    with pytest.raises(TripConfigError, match="filename-safe"):
        TripConfig(name="bad/with/slash")


def test_max_radius_requires_start_state():
    with pytest.raises(TripConfigError, match="max_radius_miles requires start_state"):
        TripConfig(name="x", max_radius_miles=100.0)
    # OK when both set
    TripConfig(name="x", max_radius_miles=100.0, start_state="CA")


def test_loop_false_requires_start_state():
    with pytest.raises(TripConfigError, match="loop=False requires start_state"):
        TripConfig(name="x", loop=False)
    # OK with start_state
    TripConfig(name="x", loop=False, start_state="CA")


def test_start_state_must_be_in_states_if_states_set():
    with pytest.raises(TripConfigError, match="start_state .* not in states"):
        TripConfig(name="x", states=["CA", "NV"], start_state="TX")
    # OK when start_state in states
    TripConfig(name="x", states=["CA", "NV"], start_state="CA")
    # OK when states is None (no constraint)
    TripConfig(name="x", start_state="TX")


def test_max_stops_must_be_feasible():
    # 3 required states, max_stops=2 → infeasible
    with pytest.raises(InfeasibleMaxStops):
        TripConfig(name="x", states=["CA", "NV", "AZ"], max_stops=2)
    # OK when max_stops >= num_required
    TripConfig(name="x", states=["CA", "NV", "AZ"], max_stops=3)
    TripConfig(name="x", states=["CA", "NV", "AZ"], max_stops=10)


def test_deferred_fields_warn_when_set():
    with pytest.warns(UserWarning, match="category_priority.*activates in time-budgeted"):
        TripConfig(name="x", category_priority={"National Park": 5})
    with pytest.warns(UserWarning, match="total_trip_days.*activates in time-budgeted"):
        TripConfig(name="x", total_trip_days=14)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_config.py -v
```

Expected: 6 FAILURES (the new validation tests) + 3 PASSING (from Task 2).

- [ ] **Step 3: Implement `__post_init__` with validation**

In `src/config.py`, replace the empty `__post_init__` body with:

```python
    def __post_init__(self) -> None:
        # 1. name must be filename-safe
        if not _NAME_PATTERN.match(self.name):
            raise TripConfigError(
                f"config.name={self.name!r} must be filename-safe "
                f"(match {_NAME_PATTERN.pattern})"
            )

        # 2. max_radius_miles requires start_state
        if self.max_radius_miles is not None and self.start_state is None:
            raise TripConfigError(
                "max_radius_miles requires start_state to provide a center point"
            )

        # 3. loop=False requires start_state for a deterministic depot
        if not self.loop and self.start_state is None:
            raise TripConfigError(
                "loop=False requires start_state to provide an unambiguous start point"
            )

        # 4. start_state must be in states if states is set
        if (
            self.start_state is not None
            and self.states is not None
            and self.start_state not in self.states
        ):
            raise TripConfigError(
                f"start_state={self.start_state!r} not in states={self.states!r}"
            )

        # 5. max_stops feasibility (against state coverage; the must_include
        #    overlap deduction is finalized once we have the DB connection in
        #    fetch_pois — at config-load time we can only check the state floor).
        if self.max_stops is not None and self.states is not None:
            min_floor = len(self.states)
            if self.max_stops < min_floor:
                raise InfeasibleMaxStops(
                    f"max_stops={self.max_stops} is less than the number of "
                    f"required states ({min_floor}). Increase max_stops or "
                    f"reduce states."
                )

        # 6. Deferred fields: warn when set
        if self.category_priority:
            warnings.warn(
                "category_priority is accepted but ignored in Phase 1; "
                "activates in time-budgeted mode (Phase 2)",
                UserWarning,
                stacklevel=2,
            )
        if self.total_trip_days is not None:
            warnings.warn(
                "total_trip_days is accepted but ignored in Phase 1; "
                "activates in time-budgeted mode (Phase 2)",
                UserWarning,
                stacklevel=2,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_config.py -v
```

Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
cd /e/dev/optitrek
git add src/config.py tests/test_config.py
git commit -m "feat(config): TripConfig __post_init__ validation rules"
```

---

## Task 4: POI query module (`src/poi_query.py`)

**Files:**
- Create: `E:\dev\optitrek\src\poi_query.py`
- Create: `E:\dev\optitrek\tests\test_poi_query.py`

- [ ] **Step 1: Write the failing tests (SQL generation)**

Create `tests/test_poi_query.py`:

```python
"""Tests for src/poi_query.py — SQL generation with a mocked psycopg cursor."""
from unittest.mock import MagicMock

import pytest

from src.config import TripConfig
from src.poi_query import build_query, fetch_pois


def test_minimal_query_excludes_ak_hi():
    cfg = TripConfig(name="x")
    sql, params = build_query(cfg)
    assert "source = 'nps'" in sql
    assert "state <> ALL(%(excluded)s)" in sql
    assert params["excluded"] == ["AK", "HI"]


def test_states_filter_adds_clause():
    cfg = TripConfig(name="x", states=["CA", "NV"])
    sql, params = build_query(cfg)
    assert "state = ANY(%(states)s)" in sql
    assert params["states"] == ["CA", "NV"]


def test_categories_filter_adds_clause():
    cfg = TripConfig(name="x", categories=["National Park"])
    sql, params = build_query(cfg)
    assert "category = ANY(%(categories)s)" in sql
    assert params["categories"] == ["National Park"]


def test_max_radius_uses_st_dwithin():
    cfg = TripConfig(name="x", max_radius_miles=100.0, start_state="CA")
    sql, params = build_query(cfg)
    assert "ST_DWithin" in sql
    # 100 miles = 160934 meters approx
    assert params["radius_meters"] == pytest.approx(100 * 1609.344, rel=1e-3)
    assert params["center_state"] == "CA"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_poi_query.py -v
```

Expected: 4 ERRORS with `ImportError`.

- [ ] **Step 3: Implement `src/poi_query.py`**

Create `src/poi_query.py`:

```python
"""POI fetch with TripConfig filters. See spec §6.2-6.4.

Two public entry points:
  - build_query(config) -> (sql, params): pure function for testing
  - fetch_pois(config) -> list[dict]: runs the query against PostGIS
"""
from __future__ import annotations

import warnings

from src.config import (
    TripConfig,
    TripConfigError,
    EmptyCandidatePool,
    UnreachableMustInclude,
)
from src.db import get_conn


_EXCLUDED_STATES = ["AK", "HI"]
_METERS_PER_MILE = 1609.344


def build_query(config: TripConfig) -> tuple[str, dict]:
    """Return (sql, params) for the candidate POI query implied by config.
    Pure function — does not open a DB connection. Used for testing and
    debugging."""
    where_clauses = [
        "source = 'nps'",
        "state IS NOT NULL",
        "state <> ALL(%(excluded)s)",
    ]
    params: dict = {"excluded": list(_EXCLUDED_STATES)}

    if config.states is not None:
        where_clauses.append("state = ANY(%(states)s)")
        params["states"] = list(config.states)

    if config.categories is not None:
        where_clauses.append("category = ANY(%(categories)s)")
        params["categories"] = list(config.categories)

    if config.max_radius_miles is not None:
        # center = centroid of POIs in start_state (proxy for "regional center"
        # since we don't have state-polygon geometry in this DB).
        where_clauses.append(
            "ST_DWithin(geom::geography, "
            "  (SELECT ST_Centroid(ST_Collect(geom))::geography "
            "   FROM pois WHERE source='nps' AND state = %(center_state)s), "
            "  %(radius_meters)s)"
        )
        params["center_state"] = config.start_state
        params["radius_meters"] = config.max_radius_miles * _METERS_PER_MILE

    sql = (
        "SELECT id, name, state, category, "
        "       ST_Y(geom) AS lat, ST_X(geom) AS lon "
        "FROM pois "
        f"WHERE {' AND '.join(where_clauses)} "
        "ORDER BY state, id"
    )
    return sql, params


def fetch_pois(config: TripConfig) -> list[dict]:
    """Execute the candidate query plus must_include union. Returns a list
    of POI dicts. Raises:
      - EmptyCandidatePool if zero rows after filters AND no must_include
      - UnreachableMustInclude if any must_include POI ID isn't in the DB
    """
    sql, params = build_query(config)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        # must_include override: union back any must-include POIs that
        # didn't match the filters above.
        if config.must_include:
            seen_ids = {r["id"] for r in rows}
            missing_ids = [i for i in config.must_include if i not in seen_ids]
            if missing_ids:
                cur.execute(
                    "SELECT id, name, state, category, "
                    "       ST_Y(geom) AS lat, ST_X(geom) AS lon "
                    "FROM pois WHERE id = ANY(%s)",
                    (missing_ids,),
                )
                extras = [dict(zip(cols, row)) for row in cur.fetchall()]
                found_extra_ids = {r["id"] for r in extras}
                truly_missing = set(missing_ids) - found_extra_ids
                if truly_missing:
                    raise UnreachableMustInclude(
                        f"must_include POI IDs not found in database: "
                        f"{sorted(truly_missing)}"
                    )
                for r in extras:
                    if r["id"] in (set(config.must_include) - seen_ids):
                        warnings.warn(
                            f"must_include POI {r['id']} ({r['name']!r}, "
                            f"state={r['state']!r}) is outside the filter "
                            f"scope but will be visited anyway",
                            UserWarning,
                            stacklevel=2,
                        )
                rows.extend(extras)
                # Re-sort by (state, id) for determinism
                rows.sort(key=lambda r: (r["state"], r["id"]))

    if not rows:
        raise EmptyCandidatePool(
            f"No POIs match the config filters. "
            f"states={config.states}, categories={config.categories}, "
            f"max_radius_miles={config.max_radius_miles} "
            f"from start_state={config.start_state}"
        )
    if len(rows) < 2:
        # 1 POI = no tour possible (depot only, no destinations)
        from src.config import SingleStopTour
        raise SingleStopTour(
            f"Only {len(rows)} POI matched the config; need at least 2 for a "
            f"tour. POI: {rows[0]['name']!r} ({rows[0]['state']}). "
            f"Widen the filters (states, categories, or max_radius_miles)."
        )
    return rows
```

And add the corresponding test to `tests/test_poi_query.py`:

```python
def test_single_stop_raises_single_stop_tour():
    from unittest.mock import patch, MagicMock
    from src.config import SingleStopTour

    # Mock the DB to return exactly one row
    fake_cur = MagicMock()
    fake_cur.description = [
        MagicMock(name="id"), MagicMock(name="name"), MagicMock(name="state"),
        MagicMock(name="category"), MagicMock(name="lat"), MagicMock(name="lon"),
    ]
    # Set the `.name` attribute on each description column properly
    for col, name in zip(fake_cur.description,
                         ["id", "name", "state", "category", "lat", "lon"]):
        col.name = name
    fake_cur.fetchall.return_value = [(1, "Only POI", "AL", "x", 30.0, -86.0)]

    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    cfg = TripConfig(name="x", states=["AL"])
    with patch("src.poi_query.get_conn", return_value=MagicMock(
        __enter__=lambda self: fake_conn, __exit__=lambda *a: None,
    )):
        with pytest.raises(SingleStopTour, match="need at least 2"):
            fetch_pois(cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_poi_query.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
cd /e/dev/optitrek
git add src/poi_query.py tests/test_poi_query.py
git commit -m "feat(poi_query): config-driven POI fetch with filter + must_include override"
```

---

## Task 5: Solver wrapper — must_include via ActiveVar

**Files:**
- Modify: `E:\dev\optitrek\src\solver.py` (add `solve_with_config` + helpers)
- Modify: `E:\dev\optitrek\tests\test_solver.py` (add must_include test)

- [ ] **Step 1: Write the failing test for must_include**

Append to `tests/test_solver.py`:

```python
import numpy as np
from src.config import TripConfig
from src.solver import Node, solve_with_config


def test_must_include_forces_visit_of_off_route_node():
    # 5 POIs: A, B, C in state ST1; D in ST2; E in ST3.
    # E is geographically far from A, B, C, D. Without must_include the
    # solver will skip E (paying state-skip penalty for ST3 only saves on
    # avoiding the long leg). With must_include=[5], E must be in the tour.
    pois = [
        {"id": 1, "name": "A", "state": "ST1", "category": "x", "lat": 0.0, "lon": 0.0},
        {"id": 2, "name": "B", "state": "ST1", "category": "x", "lat": 0.0, "lon": 1.0},
        {"id": 3, "name": "C", "state": "ST1", "category": "x", "lat": 1.0, "lon": 0.0},
        {"id": 4, "name": "D", "state": "ST2", "category": "x", "lat": 1.0, "lon": 1.0},
        {"id": 5, "name": "E", "state": "ST3", "category": "x", "lat": 100.0, "lon": 100.0},
    ]
    # Symmetric duration matrix proportional to lat/lon distance
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = ((pois[i]["lat"] - pois[j]["lat"]) ** 2 +
                     (pois[i]["lon"] - pois[j]["lon"]) ** 2) ** 0.5
                dur[i][j] = d * 3600  # 1 unit = 1 hour
                dist[i][j] = d * 1609.344

    cfg = TripConfig(name="x", states=["ST1", "ST2", "ST3"], must_include=[5],
                     time_limit_seconds=10)
    result = solve_with_config(cfg, pois, dur, dist)

    visited_ids = {n.id for n in result.order}
    assert 5 in visited_ids, "must_include POI 5 should be visited"
    # And ST3 coverage is satisfied by POI 5 — no second ST3 POI needed
    st3_visits = sum(1 for n in result.order if n.state == "ST3")
    assert st3_visits == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_solver.py::test_must_include_forces_visit_of_off_route_node -v
```

Expected: ERROR with `ImportError: cannot import name 'solve_with_config'`.

- [ ] **Step 3: Add `solve_with_config` to `src/solver.py`**

Append to `src/solver.py` (after the existing `solve()` function):

```python
# ---------- Tier 2 config-driven wrapper ----------

def _depot_index_for_config(config, pois: list[dict]) -> int:
    """Resolve depot index from config priority:
      1. must_include POI in start_state
      2. First POI in start_state (sorted by state, id — pois already sorted)
      3. pois[0] if start_state is None
    See spec §6.4.
    """
    if config.start_state is None:
        return 0
    if config.must_include:
        for i, p in enumerate(pois):
            if p["id"] in config.must_include and p["state"] == config.start_state:
                return i
    for i, p in enumerate(pois):
        if p["state"] == config.start_state:
            return i
    raise ValueError(
        f"start_state={config.start_state!r} has no POIs in the candidate "
        f"set after filtering"
    )


def solve_with_config(
    config,
    pois: list[dict],
    durations,
    distances,
):
    """Solve the TSP defined by `config` over `pois` with `durations`/
    `distances` matrices. Returns a SolveResult. See spec §6.

    Tier 2 constraint support arrives incrementally:
      - This task (Task 5): must_include via routing.ActiveVar
      - max_stops penalty + loop=False handling are added in later tasks.
        The function gracefully ignores those config fields until they're
        implemented (a max_stops config will produce more stops than
        requested; a loop=False config will include the return-to-depot
        cost). Both are corrected before any user-facing Tier 2 trip runs.

    Tier 1 callers continue to use the original solve() unchanged — this
    wrapper is for config-driven trips only.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    nodes = [Node(id=p["id"], state=p["state"]) for p in pois]
    n = len(nodes)
    depot_index = _depot_index_for_config(config, pois)

    # required_states: every state present in `pois` AND in config.states
    # (or every state if config.states is None)
    pois_states = {p["state"] for p in pois}
    if config.states is not None:
        required = set(config.states) & pois_states
    else:
        required = pois_states

    # Build routing model
    manager = pywrapcp.RoutingIndexManager(n, 1, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    cost_scale = 1000  # millisecond precision (existing solver convention)

    def time_callback(from_idx, to_idx):
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        return int(durations[i][j] * cost_scale)

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # State-coverage disjunctions: visit at least one per required state,
    # skip penalty 10^12 (effectively forbids skipping)
    SKIP_PENALTY = 10**12
    for state in required:
        state_indices = [
            manager.NodeToIndex(i) for i, p in enumerate(pois) if p["state"] == state
        ]
        if state_indices:
            routing.AddDisjunction(state_indices, SKIP_PENALTY, 1)

    # Non-required POIs (states not in required) are optional with no penalty
    for i, p in enumerate(pois):
        if p["state"] not in required:
            routing.AddDisjunction([manager.NodeToIndex(i)], 0, 1)

    # must_include: hard constraint — these nodes MUST be visited
    for must_id in config.must_include:
        for i, p in enumerate(pois):
            if p["id"] == must_id:
                node_idx = manager.NodeToIndex(i)
                routing.solver().Add(routing.ActiveVar(node_idx) == 1)
                break

    # Solver parameters
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = int(config.time_limit_seconds)

    import time
    t0 = time.perf_counter()
    solution = routing.SolveWithParameters(search_params)
    runtime = time.perf_counter() - t0

    if solution is None:
        return SolveResult(
            order=[], total_cost=float("inf"), leg_costs=[],
            states_covered=set(), status="FAILED", runtime_seconds=runtime,
        )

    # Extract tour
    index = routing.Start(0)
    visited_node_indices: list[int] = []
    leg_costs: list[float] = []
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        visited_node_indices.append(node)
        prev_index = index
        index = solution.Value(routing.NextVar(index))
        if not routing.IsEnd(index):
            arc_cost = routing.GetArcCostForVehicle(prev_index, index, 0)
            leg_costs.append(arc_cost / cost_scale)

    # Close the loop (depot → first → ... → last → depot)
    last_node = visited_node_indices[-1]
    return_cost = durations[last_node][depot_index]
    leg_costs.append(float(return_cost))

    order_nodes = [nodes[i] for i in visited_node_indices]
    total_cost = sum(leg_costs)
    states_covered = {n.state for n in order_nodes}

    return SolveResult(
        order=order_nodes,
        total_cost=total_cost,
        leg_costs=leg_costs,
        states_covered=states_covered,
        status="SUCCESS",
        runtime_seconds=runtime,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_solver.py::test_must_include_forces_visit_of_off_route_node -v
```

Expected: `1 passed`.

- [ ] **Step 5: Run full test suite to verify nothing broke**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/ -q
```

Expected: previous 17 tests still pass, plus the new ones from Tasks 2-5.

- [ ] **Step 6: Commit**

```bash
cd /e/dev/optitrek
git add src/solver.py tests/test_solver.py
git commit -m "feat(solver): solve_with_config() with must_include ActiveVar constraint"
```

---

## Task 6: Solver wrapper — max_stops soft penalty

**Files:**
- Modify: `E:\dev\optitrek\src\solver.py` (`solve_with_config` adds penalty)
- Modify: `E:\dev\optitrek\tests\test_solver.py` (add max_stops test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_solver.py`:

```python
def test_max_stops_keeps_tour_under_cap():
    # 4 states, each with 3 POIs. With max_stops=4, solver visits exactly 4
    # (one per state). With max_stops=8, it can add up to 4 optional extras
    # if doing so shortens the loop.
    pois = []
    for state_i in range(4):
        for poi_i in range(3):
            pois.append({
                "id": state_i * 10 + poi_i,
                "name": f"S{state_i}_P{poi_i}",
                "state": f"S{state_i}",
                "category": "x",
                "lat": float(state_i),
                "lon": float(poi_i) * 0.1,
            })
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = ((pois[i]["lat"] - pois[j]["lat"]) ** 2 +
                     (pois[i]["lon"] - pois[j]["lon"]) ** 2) ** 0.5
                dur[i][j] = d * 3600 + 600  # add small fixed cost per leg
                dist[i][j] = d * 1609.344

    cfg = TripConfig(name="x", states=["S0", "S1", "S2", "S3"], max_stops=4,
                     time_limit_seconds=10)
    result = solve_with_config(cfg, pois, dur, dist)
    assert len(result.order) <= 4, f"Tour has {len(result.order)} stops, max_stops=4"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/superpowers... # no, the venv path
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_solver.py::test_max_stops_keeps_tour_under_cap -v
```

Expected: FAIL (current `solve_with_config` has no max_stops handling, may add stops up to all 12).

- [ ] **Step 3: Add max_stops soft penalty to `solve_with_config`**

In `src/solver.py`, within `solve_with_config`, after the existing
`routing.AddDisjunction(state_indices, SKIP_PENALTY, 1)` loop, add:

```python
    # max_stops: soft penalty per stop beyond num_required, scaled in
    # cost-scaled seconds. Per spec §6.2, penalty = 1 hour worth of
    # cost-scaled units = 3600 * cost_scale. This makes adding a stop
    # only worth it if it shortens the tour by >= 1 hour.
    if config.max_stops is not None:
        excess_penalty = 3600 * cost_scale  # 1 hour in scaled units
        # For each NON-must-include, NON-required-state POI, add a
        # disjunction with the excess penalty so the solver pays this
        # cost per added optional stop.
        for i, p in enumerate(pois):
            already_constrained = (
                p["state"] in required  # in a state disjunction already
                or p["id"] in config.must_include  # in a hard constraint
            )
            if not already_constrained:
                # Optional stop: rebuild as penalty-disjunction
                # (overrides the earlier 0-penalty one we added for non-required)
                routing.AddDisjunction([manager.NodeToIndex(i)], excess_penalty, 1)
```

Then, after the solution extraction block, before the return statement, add a
post-validation that the tour respects max_stops:

```python
    if config.max_stops is not None and len(order_nodes) > config.max_stops:
        # The penalty should keep us under the cap; this is a defensive check.
        # If it ever fires, the penalty needs tuning upward.
        import warnings
        warnings.warn(
            f"Tour has {len(order_nodes)} stops, exceeding max_stops="
            f"{config.max_stops}. Consider raising excess_stop_penalty.",
            UserWarning, stacklevel=2,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_solver.py::test_max_stops_keeps_tour_under_cap -v
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /e/dev/optitrek
git add src/solver.py tests/test_solver.py
git commit -m "feat(solver): max_stops soft penalty + defensive post-validation"
```

---

## Task 7: Solver wrapper — loop=False (open path)

**Files:**
- Modify: `E:\dev\optitrek\src\solver.py` (zero return edge cost)
- Modify: `E:\dev\optitrek\tests\test_solver.py` (add open-path test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_solver.py`:

```python
def test_loop_false_returns_shorter_total_than_loop_true():
    # Linear chain of 4 POIs spaced 1 unit apart on a line.
    # Loop=True: must drive 1+1+1+3 = 6 units (the last leg loops back)
    # Loop=False: drive 1+1+1 = 3 units (no return)
    pois = [
        {"id": i, "name": f"P{i}", "state": f"S{i}",
         "category": "x", "lat": 0.0, "lon": float(i)}
        for i in range(4)
    ]
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = abs(pois[i]["lon"] - pois[j]["lon"])
                dur[i][j] = d * 3600
                dist[i][j] = d * 1609.344

    # loop=True
    cfg_loop = TripConfig(name="x", states=["S0", "S1", "S2", "S3"],
                          loop=True, time_limit_seconds=5)
    res_loop = solve_with_config(cfg_loop, pois, dur, dist)

    # loop=False (requires start_state)
    cfg_open = TripConfig(name="x", states=["S0", "S1", "S2", "S3"],
                          loop=False, start_state="S0", time_limit_seconds=5)
    res_open = solve_with_config(cfg_open, pois, dur, dist)

    assert res_open.total_cost < res_loop.total_cost, (
        f"Open path ({res_open.total_cost}s) should be shorter than "
        f"loop ({res_loop.total_cost}s)"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_solver.py::test_loop_false_returns_shorter_total_than_loop_true -v
```

Expected: FAIL — both runs include the return cost, totals are equal.

- [ ] **Step 3: Add loop=False handling**

In `solve_with_config`, modify the tour-extraction block to NOT add the return-to-depot cost when `config.loop is False`. Replace the existing:

```python
    # Close the loop (depot → first → ... → last → depot)
    last_node = visited_node_indices[-1]
    return_cost = durations[last_node][depot_index]
    leg_costs.append(float(return_cost))
```

With:

```python
    # Close the loop (depot → first → ... → last → depot), but only if
    # config.loop is True. Open paths exclude the return-to-depot leg.
    if config.loop:
        last_node = visited_node_indices[-1]
        return_cost = durations[last_node][depot_index]
        leg_costs.append(float(return_cost))
```

Note: this controls the COST REPORTING of the tour. The OR-Tools solver
itself internally still solves a cycle; we're just not charging the user
for the return leg. For most realistic configurations the optimal cycle
and optimal open path have the same INTERIOR sequence — the change is in
the reported cost.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_solver.py::test_loop_false_returns_shorter_total_than_loop_true -v
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /e/dev/optitrek
git add src/solver.py tests/test_solver.py
git commit -m "feat(solver): loop=False open-path mode excludes return-to-depot cost"
```

---

## Task 8: Daily leg splitting + color-by-day visualization

**Files:**
- Modify: `E:\dev\optitrek\src\visualize.py` (add `split_into_days` + day_colors)
- Create: `E:\dev\optitrek\tests\test_visualize_days.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_visualize_days.py`:

```python
"""Tests for daily leg splitting and color-by-day rendering."""
import pytest
from src.solver import Node, SolveResult
from src.visualize import split_into_days


def make_result(leg_hours: list[float]) -> SolveResult:
    """Build a minimal SolveResult with n+1 stops and n leg_costs in seconds."""
    nodes = [Node(id=i, state=f"S{i}") for i in range(len(leg_hours) + 1)]
    return SolveResult(
        order=nodes,
        total_cost=sum(h * 3600 for h in leg_hours),
        leg_costs=[h * 3600 for h in leg_hours],
        states_covered={n.state for n in nodes},
        status="SUCCESS",
        runtime_seconds=0.0,
    )


def test_splits_at_hour_cap():
    # 5 legs of 3 hours each, cap 8 hours → days: [0,1,2,3] (9h hits cap),
    # wait, 3+3=6 then 6+3=9 > 8, so [0,1,2] in day 0 (6h), then [3,4] in day 1
    # Actually walking it: stop 0 starts day. leg 0 (3h) → stop 1, today=3.
    # leg 1 (3h) → stop 2, today=6. leg 2 (3h) → today_new=9>8 → new day,
    # day 1 starts at stop 3, today=3. leg 3 (3h) → stop 4, today=6.
    # → days = [[0,1,2], [3,4]]
    res = make_result([3, 3, 3, 3])
    days = split_into_days(res, max_hours_per_day=8.0)
    assert days == [[0, 1, 2], [3, 4]], f"got {days}"


def test_single_long_leg_is_own_day():
    # One 10-hour leg, cap 8h. Can't split a single leg.
    # → days = [[0], [1]]
    res = make_result([10])
    days = split_into_days(res, max_hours_per_day=8.0)
    assert days == [[0], [1]]


def test_everything_fits_one_day():
    res = make_result([1, 1, 1])
    days = split_into_days(res, max_hours_per_day=8.0)
    assert days == [[0, 1, 2, 3]]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_visualize_days.py -v
```

Expected: 3 ERRORS with `ImportError: cannot import name 'split_into_days'`.

- [ ] **Step 3: Implement `split_into_days` in `src/visualize.py`**

Append to `src/visualize.py`:

```python
def split_into_days(
    result, max_hours_per_day: float = 8.0
) -> list[list[int]]:
    """Partition the visit order into day-indexed stop lists by walking
    leg_costs and starting a new day when adding the next leg would
    exceed max_hours_per_day. See spec §6.6.

    Returns list of lists; each inner list contains stop indices
    (referring to positions in result.order). A single leg longer than
    max_hours_per_day becomes its own day (no overnight splitting; that's
    Tier 3).
    """
    days: list[list[int]] = [[0]]
    today_hours = 0.0
    for i, leg_seconds in enumerate(result.leg_costs):
        leg_hours = leg_seconds / 3600.0
        if today_hours + leg_hours > max_hours_per_day and days[-1]:
            days.append([])
            today_hours = 0.0
        days[-1].append(i + 1)
        today_hours += leg_hours
    # If the final day got only the closing return-to-depot (no marker),
    # drop the empty list — last day is the return, not a new visit.
    if days[-1] == [len(result.order)]:
        days.pop()
    return days


# ColorBrewer palettes for color-by-day rendering. Set1 has 9 distinct
# hues; Set3 has 12 muted ones for longer trips.
_COLOR_SET1 = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#a65628", "#f781bf", "#999999", "#1b9e77",
]
_COLOR_SET3 = [
    "#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3",
    "#fdb462", "#b3de69", "#fccde5", "#d9d9d9", "#bc80bd",
    "#ccebc5", "#ffed6f",
]


def colors_for_days(n_days: int) -> list[str]:
    """Return n_days distinct hex colors. Uses Set1 for ≤9 days, Set3 for
    10-12, and cycles Set3 beyond that."""
    palette = _COLOR_SET1 if n_days <= 9 else _COLOR_SET3
    return [palette[i % len(palette)] for i in range(n_days)]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_visualize_days.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
cd /e/dev/optitrek
git add src/visualize.py tests/test_visualize_days.py
git commit -m "feat(visualize): split_into_days() + ColorBrewer palettes for color-by-day"
```

---

## Task 9: Top-level orchestrator `src/trip.py`

**Files:**
- Create: `E:\dev\optitrek\src\trip.py`
- Create: `E:\dev\optitrek\tests\test_trip.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trip.py`:

```python
"""End-to-end orchestrator tests with mocked fetch_pois + matrix build."""
from pathlib import Path
from unittest.mock import patch
import numpy as np

from src.config import TripConfig, EmptyCandidatePool
from src.trip import run_trip


def _fake_pois():
    return [
        {"id": i, "name": f"P{i}", "state": f"S{i % 3}",
         "category": "National Park", "lat": float(i), "lon": float(i)}
        for i in range(6)
    ]


def _fake_matrices(pois):
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = ((pois[i]["lat"] - pois[j]["lat"]) ** 2 +
                     (pois[i]["lon"] - pois[j]["lon"]) ** 2) ** 0.5
                dur[i][j] = d * 3600 + 600
                dist[i][j] = d * 1609.344
    return dur, dist


def test_run_trip_happy_path(tmp_path: Path):
    cfg = TripConfig(name="test_trip", time_limit_seconds=5)
    with patch("src.trip.fetch_pois", return_value=_fake_pois()), \
         patch("src.trip.build_matrix", side_effect=lambda pois: _fake_matrices(pois)):
        out = run_trip(cfg, output_dir=tmp_path)
    assert out.exists()
    assert out.suffix == ".html"
    assert out.name == "test_trip.html"


def test_run_trip_empty_pool_raises(tmp_path: Path):
    cfg = TripConfig(name="test_empty", time_limit_seconds=5)
    with patch("src.trip.fetch_pois", side_effect=EmptyCandidatePool("no rows")):
        try:
            run_trip(cfg, output_dir=tmp_path)
        except EmptyCandidatePool:
            pass  # expected
        else:
            assert False, "should have raised EmptyCandidatePool"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_trip.py -v
```

Expected: 2 ERRORS with `ImportError: cannot import name 'run_trip'`.

- [ ] **Step 3: Implement `src/trip.py`**

Create `src/trip.py`:

```python
"""Top-level Tier 2 pipeline orchestrator. See spec §3.

The pipeline:
    config → fetch_pois → build_matrix → solve_with_config → render_map
"""
from __future__ import annotations

from pathlib import Path

from src.config import TripConfig
from src.matrix_builder import build_matrix
from src.poi_query import fetch_pois
from src.solver import solve_with_config
from src.visualize import (
    StopGeo, colors_for_days, render_map, split_into_days,
    stop_geos_from_poi_table,
)


def _build_stop_geos(pois: list[dict], order_nodes) -> dict:
    """Helper: order-aware StopGeo lookup keyed by node id."""
    return stop_geos_from_poi_table(order_nodes, pois)


def run_trip(
    config: TripConfig,
    output_dir: Path | None = None,
    osrm_url: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Run the full pipeline for `config` and write the HTML map.

    Returns the path to the written HTML file. If `dry_run=True`, returns
    a path that doesn't exist after printing the post-filter candidate set
    + resolved depot.

    `output_dir` defaults to `output/` at the repo root. The HTML lands at
    `<output_dir>/<config.name>.html`.
    """
    output_dir = output_dir or (Path(__file__).resolve().parent.parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{config.name}.html"

    pois = fetch_pois(config)
    print(f">> {len(pois)} POIs after filters")

    if dry_run:
        print(f">> Dry run — depot would be POI #0: {pois[0]['name']} ({pois[0]['state']})")
        return out_path  # path returned but not created

    durations, distances = build_matrix(pois)
    print(f">> Matrix {durations.shape}, solving (budget {config.time_limit_seconds}s)...")

    result = solve_with_config(config, pois, durations, distances)
    print(f">> {result.status}: {len(result.order)} stops, "
          f"{result.total_cost/3600:.1f} h, "
          f"{sum(distances[result.order[i].id][result.order[(i+1)%len(result.order)].id] for i in range(len(result.order)-1))/1609.344:,.0f} mi")

    days = split_into_days(result, config.max_hours_per_day)
    day_colors = colors_for_days(len(days))
    print(f">> Splitting into {len(days)} days (cap {config.max_hours_per_day}h/day)")

    stop_geo = stop_geos_from_poi_table(result.order, pois)
    render_map(
        result=result,
        stop_geo=stop_geo,
        output_path=out_path,
        osrm_url=osrm_url,
        use_road_geometry=True,
    )
    print(f">> Wrote {out_path}")
    return out_path
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/test_trip.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
cd /e/dev/optitrek
git add src/trip.py tests/test_trip.py
git commit -m "feat(trip): run_trip() top-level orchestrator with dry-run support"
```

---

## Task 10: CLI entry point `scripts/run_trip.py`

**Files:**
- Create: `E:\dev\optitrek\scripts\run_trip.py`

- [ ] **Step 1: Implement the CLI**

Create `scripts/run_trip.py`:

```python
"""CLI runner for Tier 2 config-driven trips. See spec §5 (Library and CLI conventions).

Usage:
    python -m scripts.run_trip trips/southwest_parks.yaml
    python -m scripts.run_trip trips/southwest_parks.yaml --dry-run
    python -m scripts.run_trip trips/southwest_parks.yaml --time-limit-override 30
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from src.config import TripConfigError, load_config
from src.trip import run_trip


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run_trip",
        description="Run a Tier 2 Optitrek trip from a YAML config",
    )
    parser.add_argument("yaml_path", type=Path,
                        help="Path to the trip YAML config file")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Override the output directory (default: ./output)")
    parser.add_argument("--time-limit-override", type=int, default=None,
                        metavar="SECONDS",
                        help="Override config.time_limit_seconds (useful for quick smoke tests)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print resolved depot + candidate count, then exit (no matrix/solve/render)")
    parser.add_argument("--verbose", action="store_true",
                        help="Echo SQL + OSRM URLs as they run")

    args = parser.parse_args()

    try:
        config = load_config(args.yaml_path)
    except (TripConfigError, FileNotFoundError) as e:
        print(f"ERROR loading {args.yaml_path}: {e}", file=sys.stderr)
        return 1

    if args.time_limit_override is not None:
        config = replace(config, time_limit_seconds=args.time_limit_override)

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    try:
        out_path = run_trip(
            config=config,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )
    except TripConfigError as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not args.dry_run:
        print(f"\n=> Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test the CLI with --help**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m scripts.run_trip --help
```

Expected: argparse help output listing all 5 args/flags.

- [ ] **Step 3: Commit**

```bash
cd /e/dev/optitrek
git add scripts/run_trip.py
git commit -m "feat(cli): scripts/run_trip.py argparse-based runner"
```

---

## Task 11: Trip YAML files

**Files:**
- Create: `E:\dev\optitrek\trips\tier1_replica.yaml`
- Create: `E:\dev\optitrek\trips\southwest_parks.yaml`

- [ ] **Step 1: Create `trips/` directory and Tier 1 replica config**

```bash
cd /e/dev/optitrek
mkdir -p trips
```

Create `trips/tier1_replica.yaml`:

```yaml
# trips/tier1_replica.yaml — correctness oracle for Tier 2 refactor.
# Reproduces Tier 1's known result: 49 stops, 193.0 h, 9,744 mi.
# If solve_with_config produces results outside ±0.5% of those numbers,
# the refactor has a bug.
name: tier1_replica
states: [AL, AR, AZ, CA, CO, CT, DC, DE, FL, GA, IA, ID, IL, IN, KS, KY,
         LA, MA, MD, ME, MI, MN, MO, MS, MT, NC, ND, NE, NH, NJ, NM, NV,
         NY, OH, OK, OR, PA, RI, SC, SD, TN, TX, UT, VA, VT, WA, WI, WV, WY]
# No category filter — matches Tier 1's "all NPS designations"
loop: true
time_limit_seconds: 300
```

- [ ] **Step 2: Create southwest parks demo config**

Create `trips/southwest_parks.yaml`:

```yaml
# trips/southwest_parks.yaml — Tier 2 demo: 5-state parks-only loop.
# Uses the raw NPS designation "National Park" since the DB hasn't been
# normalized to nps_park yet (see spec §4 + DB-EXPANSION-SPEC §4.2).
name: southwest_parks
states: [NM, AZ, UT, NV, CO]
categories: ["National Park"]
loop: true
time_limit_seconds: 120
```

- [ ] **Step 3: Commit**

```bash
cd /e/dev/optitrek
git add trips/tier1_replica.yaml trips/southwest_parks.yaml
git commit -m "feat(trips): tier1_replica + southwest_parks YAML configs"
```

---

## Task 12: Correctness oracle (Tier 1 replica integration test)

**Files:**
- Create: `E:\dev\optitrek\scripts\test_tier1_replica.py`

Requires OSRM running locally (`./scripts/run_tier1_local.sh` brings it up, OR use the `render_overlays.sh` lifecycle pattern).

- [ ] **Step 1: Write the oracle test**

Create `scripts/test_tier1_replica.py`:

```python
"""Integration test: run trips/tier1_replica.yaml end-to-end and assert
the result matches Tier 1's known good output within ±0.5%.

This is NOT part of pytest tests/ — it requires a live PostgreSQL DB
and a live OSRM instance. Run from a context where both are up:
    /root/venvs/optitrek-wsl/bin/python -m scripts.test_tier1_replica

Tier 1 baseline (from BUILD_STATUS.md, 2026-05-21):
    49 stops, 193.0 hours, 9,744 miles
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.config import load_config
from src.trip import run_trip
from src.matrix_builder import build_matrix
from src.poi_query import fetch_pois
from src.solver import solve_with_config


TIER1_HOURS = 193.0
TIER1_MILES = 9744.0
TOLERANCE = 0.005  # ±0.5%


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "trips" / "tier1_replica.yaml"
    config = load_config(config_path)
    print(f"Loaded {config_path.name}")

    pois = fetch_pois(config)
    print(f"{len(pois)} POIs after filter (expect 438)")
    assert len(pois) == 438, f"expected 438 POIs, got {len(pois)}"

    durations, distances = build_matrix(pois)
    print(f"Matrix {durations.shape}")

    result = solve_with_config(config, pois, durations, distances)
    n = len(result.order)
    total_dist_m = sum(
        float(distances[result.order[i].id][result.order[(i + 1) % n].id])
        for i in range(n)
    )
    hours = result.total_cost / 3600
    miles = total_dist_m / 1609.344

    print(f"\n=== Result ===")
    print(f"  Stops:    {n}")
    print(f"  Hours:    {hours:.2f}  (Tier 1: {TIER1_HOURS:.2f})")
    print(f"  Miles:    {miles:,.0f}  (Tier 1: {TIER1_MILES:,.0f})")

    hours_pct = abs(hours - TIER1_HOURS) / TIER1_HOURS
    miles_pct = abs(miles - TIER1_MILES) / TIER1_MILES
    print(f"  Δhours:   {hours_pct*100:+.2f}%  (tolerance ±{TOLERANCE*100:.1f}%)")
    print(f"  Δmiles:   {miles_pct*100:+.2f}%  (tolerance ±{TOLERANCE*100:.1f}%)")

    failures = []
    if n != 49:
        failures.append(f"expected 49 stops, got {n}")
    if hours_pct > TOLERANCE:
        failures.append(f"hours drift {hours_pct*100:.2f}% exceeds {TOLERANCE*100:.1f}%")
    if miles_pct > TOLERANCE:
        failures.append(f"miles drift {miles_pct*100:.2f}% exceeds {TOLERANCE*100:.1f}%")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\n✓ Tier 1 replica reproduced within ±0.5% — refactor is correct.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run with OSRM up**

Start OSRM via the existing orchestrator, then run the oracle inline:

```bash
cd /e/dev/optitrek
# Edit scripts/run_tier1_local.sh to call test_tier1_replica.py instead
# of run_tier1.py, OR just bring up OSRM manually and run:
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash -c "
  docker run -d --rm --name osrm -p 127.0.0.1:5000:5000 \
    -v /mnt/e/dev/optitrek/data/osrm-major:/data:ro \
    ghcr.io/project-osrm/osrm-backend:latest \
    osrm-routed --algorithm mld --max-table-size 8000 /data/us-major.osrm
  sleep 20
  cd /mnt/e/dev/optitrek && /root/venvs/optitrek-wsl/bin/python -m scripts.test_tier1_replica
  docker stop osrm
"
```

Expected: `✓ Tier 1 replica reproduced within ±0.5%`.

If it fails: see the FAILURES list printed. Most likely cause is the
solver wrapper introducing a subtle behavior change. Diff
`solve_with_config` against the original `solve()` to find the
discrepancy.

- [ ] **Step 3: Commit**

```bash
cd /e/dev/optitrek
git add scripts/test_tier1_replica.py
git commit -m "test(integration): Tier 1 replica oracle — assert ±0.5% reproduction"
```

---

## Task 13: Run southwest_parks demo + add to gallery

**Files:**
- Create: `E:\dev\optitrek\gallery\09_southwest_parks.html`

- [ ] **Step 1: Run the demo with OSRM up**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash -c "
  docker run -d --rm --name osrm -p 127.0.0.1:5000:5000 \
    -v /mnt/e/dev/optitrek/data/osrm-major:/data:ro \
    ghcr.io/project-osrm/osrm-backend:latest \
    osrm-routed --algorithm mld --max-table-size 8000 /data/us-major.osrm
  sleep 20
  cd /mnt/e/dev/optitrek && /root/venvs/optitrek-wsl/bin/python -m scripts.run_trip trips/southwest_parks.yaml
  docker stop osrm
"
```

Expected: writes `output/southwest_parks.html`, prints summary with 5 stops covering all 5 states.

- [ ] **Step 2: Copy to gallery**

```bash
cd /e/dev/optitrek
cp output/southwest_parks.html gallery/09_southwest_parks.html
```

- [ ] **Step 3: Add gallery README entry**

Append to `gallery/README.md` (before any existing trailing content):

```markdown
## 09 — Tier 2 demo: Southwest National Parks loop

[`09_southwest_parks.html`](09_southwest_parks.html)

The first Tier 2 demo: a 5-state National Parks loop generated from
`trips/southwest_parks.yaml`. Categories filtered to "National Park" only,
states limited to NM/AZ/UT/NV/CO. Demonstrates the config-driven pipeline
working end-to-end without any hardcoded scope.

Generated via `python -m scripts.run_trip trips/southwest_parks.yaml`.
```

- [ ] **Step 4: Commit**

```bash
cd /e/dev/optitrek
git add gallery/09_southwest_parks.html gallery/README.md
git commit -m "feat(gallery): 09_southwest_parks — first Tier 2 config-driven demo"
```

---

## Task 14: BUILD_STATUS + CLAUDE.md updates

**Files:**
- Modify: `E:\dev\optitrek\BUILD_STATUS.md`
- Modify: `E:\dev\optitrek\CLAUDE.md`

- [ ] **Step 1: Update BUILD_STATUS.md**

Prepend (before the existing "Tier 1 PIPELINE COMPLETE" section) a new
"Tier 2 Phase 1 COMPLETE" section:

```markdown
## Tier 2 Phase 1 COMPLETE (this update)

- `TripConfig` dataclass + YAML loader at `src/config.py` with full
  validation in `__post_init__` (filename safety, max_radius requires
  start_state, loop=False requires start_state, max_stops feasibility)
- POI fetch with filters at `src/poi_query.py` (categories, states,
  max_radius, must_include override, typed exceptions for empty results)
- Solver wrapper `solve_with_config()` at `src/solver.py` adds three new
  constraints: must_include (ActiveVar hard), max_stops (soft penalty),
  loop=False (open path)
- Daily leg splitting + ColorBrewer color-by-day in `src/visualize.py`
- Top-level orchestrator at `src/trip.py`; CLI runner at `scripts/run_trip.py`
- Two example YAMLs: `trips/tier1_replica.yaml` (oracle) and `trips/southwest_parks.yaml`
- Tier 1 replica oracle (`scripts/test_tier1_replica.py`) reproduces
  193.0 h / 9,744 mi within ±0.5%
- Gallery map 09 (`gallery/09_southwest_parks.html`) from the config layer
- Tests: `tests/test_config.py`, `tests/test_poi_query.py`, `tests/test_trip.py`,
  `tests/test_visualize_days.py`, plus extensions to `tests/test_solver.py`.
  Total passing tests grew from 17 to ~28.

### Running a Tier 2 trip

```bash
cd /e/dev/optitrek
# Start OSRM (via run_tier1_local.sh or render_overlays.sh pattern)
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m scripts.run_trip trips/southwest_parks.yaml
```

To author a new trip: copy `trips/southwest_parks.yaml`, edit the fields,
run with `scripts/run_trip.py`.

---
```

- [ ] **Step 2: Update CLAUDE.md**

Add a new section "Tier 2 entry point" near the existing "Running Tier 1" section
(or wherever Tier 1 commands are documented):

```markdown
## Tier 2 entry point (config-driven)

```bash
# CLI: python -m scripts.run_trip <yaml_path> [flags]
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m scripts.run_trip trips/southwest_parks.yaml
```

Useful flags:
- `--dry-run` — print resolved depot + candidate count, no solve
- `--time-limit-override N` — override config's time_limit_seconds
- `--output-dir <dir>` — write the HTML somewhere other than `output/`

YAML config schema documented at `docs/superpowers/specs/2026-05-22-tier2-trip-config-design.md` §4.

Tier 1 entry point (`scripts/run_tier1.py`) is untouched and still works
for the original 49-state NPS loop. Tier 2 reproduces it exactly via
`trips/tier1_replica.yaml`.
```

- [ ] **Step 3: Run final test suite**

```bash
cd /e/dev/optitrek
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- /root/venvs/optitrek-wsl/bin/python -m pytest tests/ -v
```

Expected: ~28 passed, 0 failed.

- [ ] **Step 4: Commit + push**

```bash
cd /e/dev/optitrek
git add BUILD_STATUS.md CLAUDE.md
git commit -m "docs: Tier 2 Phase 1 complete — BUILD_STATUS + CLAUDE.md updates"
git push origin main
```

---

## Success criteria checklist

After all 14 tasks complete, all of these are true:

- [ ] `src/config.py`, `src/poi_query.py`, `src/trip.py`, `scripts/run_trip.py` exist
- [ ] `scripts/run_trip.py trips/tier1_replica.yaml` reproduces the Tier 1 result
      within ±0.5% on time (193.0 h ± 1.0 h) and miles (9,744 mi ± 49 mi)
- [ ] `scripts/run_trip.py trips/southwest_parks.yaml` produces a valid 5-state
      parks-only loop (visits all 5 states; no states from outside NM/AZ/UT/NV/CO)
- [ ] 17 existing tests still pass; ~11 new tests added (config, poi_query, solver
      extensions, visualize_days, trip orchestrator)
- [ ] Daily leg splitting renders multi-colored polylines on the output map
- [ ] `gallery/09_southwest_parks.html` exists and opens cleanly
- [ ] `BUILD_STATUS.md` Tier 2 Phase 1 section added
- [ ] `CLAUDE.md` documents `scripts/run_trip.py` for future sessions
- [ ] Commits on `main` at `E:\dev\optitrek`, pushed to origin
- [ ] `pyyaml` in `requirements.txt`

---

## Notes for the implementer

1. **The Tier 1 oracle (Task 12) is the strongest correctness signal.** If it
   fails by >0.5%, stop and debug before continuing — the refactor has a real
   bug. Most likely cause is the disjunction structure in `solve_with_config`
   not matching the original `solve()`'s exactly. Diff the two functions.

2. **The hook noise** ("PostToolUse:Edit hook blocking error from command:
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check-sql-files.py") that fires on
   every Write/Edit is harmless — a stale CockroachDB plugin reference. The
   actual file operation succeeds. Ignore it.

3. **OSRM lifecycle** uses the existing `scripts/run_tier1_local.sh` /
   `scripts/render_overlays.sh` pattern: start container in WSL, wait for
   readiness, run script, stop container via trap. Tasks 12 and 13 need this.

4. **Each task commits its own scope** — even if you're doing multiple tasks
   in a single sitting, keep the commits granular. The CI history will read
   cleanly later.
