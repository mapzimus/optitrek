"""Tests for src/solver.py.

Strategy: hand-build small synthetic distance matrices where the optimal tour
is obvious, then verify the solver finds it. Pure-Python — no DB, no OSRM.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.solver import Node, solve, validate


def _build_square_matrix() -> tuple[list[Node], np.ndarray]:
    """4 states × 2 nodes each, laid out so the obvious optimum is to visit
    one node per state walking around a unit square.

    Each state has a "primary" node at a square corner and a "decoy" node
    parked 100 units away from the loop. Capped mode should pick the four
    primaries (total cost = 4.0). Uncapped should not bother adding decoys.

    Layout (only primaries shown):
        A_primary (0,0) ── B_primary (1,0)
            │                  │
        D_primary (0,1) ── C_primary (1,1)
    """
    coords = {
        ("A", "primary"): (0.0, 0.0),
        ("A", "decoy"):   (0.0, 100.0),
        ("B", "primary"): (1.0, 0.0),
        ("B", "decoy"):   (1.0, 100.0),
        ("C", "primary"): (1.0, 1.0),
        ("C", "decoy"):   (1.0, 101.0),
        ("D", "primary"): (0.0, 1.0),
        ("D", "decoy"):   (0.0, 101.0),
    }
    nodes = [Node(id=f"{st}_{role}", state=st) for (st, role) in coords]
    pts = np.array(list(coords.values()), dtype=float)
    diff = pts[:, None, :] - pts[None, :, :]
    matrix = np.linalg.norm(diff, axis=-1)
    return nodes, matrix


def test_capped_mode_picks_one_per_state():
    nodes, matrix = _build_square_matrix()
    required = {"A", "B", "C", "D"}

    result = solve(
        nodes=nodes,
        distance_matrix=matrix,
        required_states=required,
        mode="capped",
        time_limit_seconds=5,
    )

    assert validate(result, required) == []
    # Exactly one node per state (capped at 1).
    assert len(result.order) == 4
    assert {n.state for n in result.order} == required
    # All primaries, no decoys (decoys are 100 units off the loop).
    visited_ids = {n.id for n in result.order}
    expected_primaries = {f"{s}_primary" for s in required}
    assert visited_ids == expected_primaries
    # Total cost should be the unit-square perimeter (4.0), with float tolerance
    # for the int-scaling round-trip inside the solver.
    assert result.total_cost == pytest.approx(4.0, abs=0.01)


def test_uncapped_mode_covers_states_and_skips_decoys():
    nodes, matrix = _build_square_matrix()
    required = {"A", "B", "C", "D"}

    result = solve(
        nodes=nodes,
        distance_matrix=matrix,
        required_states=required,
        mode="uncapped",
        time_limit_seconds=5,
    )

    assert validate(result, required) == []
    # Uncapped can add stops, but decoys are 100 units away — adding any
    # decoy would add ~200 to the loop cost. The solver should stick to
    # the four primaries.
    assert {n.state for n in result.order} == required
    assert result.total_cost == pytest.approx(4.0, abs=0.01)


def test_uncapped_mode_can_add_a_shortcut():
    """Construct a case where adding an extra (non-required) stop strictly
    shortens the loop. Uncapped should add it; capped cannot."""
    # 3 required states (A, B, C) at corners of a long triangle.
    # 1 non-required state (X) sits on the shortest path between B and C.
    # Visiting X reduces the B→C leg from 100 to 2×30 = 60.
    nodes = [
        Node("A1", "A"),
        Node("B1", "B"),
        Node("C1", "C"),
        Node("X1", "X"),  # not in required_states
    ]
    matrix = np.array([
        # A    B    C    X
        [  0, 50,  50, 60],   # A
        [ 50,  0, 100, 30],   # B
        [ 50,100,   0, 30],   # C
        [ 60, 30,  30,  0],   # X
    ], dtype=float)
    required = {"A", "B", "C"}

    capped = solve(
        nodes=nodes, distance_matrix=matrix, required_states=required,
        mode="capped", time_limit_seconds=5,
    )
    uncapped = solve(
        nodes=nodes, distance_matrix=matrix, required_states=required,
        mode="uncapped", time_limit_seconds=5,
    )

    assert validate(capped, required) == []
    assert validate(uncapped, required) == []

    # Capped is forced to A-B-C-A (or reverse): 50+100+50 = 200.
    assert len(capped.order) == 3
    assert capped.total_cost == pytest.approx(200.0, abs=0.01)

    # Uncapped should insert X between B and C: A-B-X-C-A = 50+30+30+50 = 160.
    assert len(uncapped.order) == 4
    assert uncapped.total_cost == pytest.approx(160.0, abs=0.01)
    assert "X1" in {n.id for n in uncapped.order}


def test_raises_when_required_state_has_no_candidates():
    nodes = [Node("A1", "A"), Node("B1", "B")]
    matrix = np.array([[0, 1], [1, 0]], dtype=float)
    with pytest.raises(ValueError, match="cannot cover"):
        solve(
            nodes=nodes,
            distance_matrix=matrix,
            required_states={"A", "B", "Z"},  # Z has no candidates
            mode="capped",
            time_limit_seconds=1,
        )


def test_validate_catches_missing_state():
    from src.solver import SolveResult
    r = SolveResult(
        order=[Node("A1", "A")],
        leg_costs=[0.0],
        total_cost=0.0,
        states_covered={"A"},
        status="SUCCESS",
    )
    problems = validate(r, required_states={"A", "B"})
    assert any("missing required states" in p for p in problems)


def test_validate_catches_duplicate_node():
    from src.solver import SolveResult
    n = Node("dup", "A")
    r = SolveResult(
        order=[n, n],
        leg_costs=[1.0, 1.0],
        total_cost=2.0,
        states_covered={"A"},
        status="SUCCESS",
    )
    problems = validate(r, required_states={"A"})
    assert any("duplicate" in p for p in problems)


from src.config import TripConfig
from src.solver import solve_with_config


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

    visited_ids = {node.id for node in result.order}
    assert 5 in visited_ids, "must_include POI 5 should be visited"
    st3_visits = sum(1 for node in result.order if node.state == "ST3")
    assert st3_visits == 1


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

    cfg_loop = TripConfig(name="x", states=["S0", "S1", "S2", "S3"],
                          loop=True, time_limit_seconds=5)
    res_loop = solve_with_config(cfg_loop, pois, dur, dist)

    cfg_open = TripConfig(name="x", states=["S0", "S1", "S2", "S3"],
                          loop=False, start_state="S0", time_limit_seconds=5)
    res_open = solve_with_config(cfg_open, pois, dur, dist)

    assert res_open.total_cost < res_loop.total_cost, (
        f"Open path ({res_open.total_cost}s) should be shorter than "
        f"loop ({res_loop.total_cost}s)"
    )


def test_max_stops_is_soft_warns_when_violated():
    """max_stops is a SOFT penalty, not a hard constraint. When the routing
    savings from adding extra stops exceed `excess_stop_penalty`, the
    solver knowingly visits more than max_stops AND emits a UserWarning
    pointing at the knob to turn. This characterization test pins both:

      - the soft semantics (a future refactor that makes it hard would
        change this assertion)
      - the warning message shape (any rewording would break the regex
        and force the docs / CLI help text to be updated in lockstep)

    Setup: 3 states, S0 has 3 colinear POIs that make great waypoints
    between S1 and S2. The natural uncapped optimum visits 4 stops to use
    one S0 waypoint. With max_stops=3 and the current default penalty,
    the routing savings (~50%) dominate the cap penalty, so the solver
    intentionally violates.

    A complementary test, `test_max_stops_keeps_tour_under_cap`, covers
    the case where the cap DOES bind (different geometry — see that test
    for the bound-cap regime)."""
    pois = [
        # S0 has 3 colinear POIs that could serve as waypoints
        {"id": 0, "name": "S0_W", "state": "S0", "category": "x",
         "lat": 0.0, "lon": 0.0},
        {"id": 1, "name": "S0_M", "state": "S0", "category": "x",
         "lat": 0.0, "lon": 5.0},
        {"id": 2, "name": "S0_E", "state": "S0", "category": "x",
         "lat": 0.0, "lon": 10.0},
        # S1 in the middle vertically
        {"id": 3, "name": "S1",   "state": "S1", "category": "x",
         "lat": 5.0, "lon": 5.0},
        # S2 far to the east
        {"id": 4, "name": "S2",   "state": "S2", "category": "x",
         "lat": 0.0, "lon": 15.0},
    ]
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = ((pois[i]["lat"] - pois[j]["lat"]) ** 2 +
                     (pois[i]["lon"] - pois[j]["lon"]) ** 2) ** 0.5
                dur[i][j] = d * 3600
                dist[i][j] = d * 1609.344

    cfg_capped = TripConfig(
        name="x", states=["S0", "S1", "S2"], max_stops=3, time_limit_seconds=10,
    )
    # Solver should violate the cap AND warn. Both have to fire — a future
    # refactor that drops the warning but keeps the violation would silently
    # leave callers in the dark.
    with pytest.warns(UserWarning, match=r"exceeding max_stops=3"):
        result = solve_with_config(cfg_capped, pois, dur, dist)

    assert len(result.order) > 3, (
        f"Tour was {len(result.order)} stops — expected > 3 since the soft "
        f"penalty should not bind on this waypoint-shaped graph. If this "
        f"asserts, either the penalty got stronger or the graph no longer "
        f"makes extras valuable enough to outweigh it."
    )


def test_compound_states_categories_must_include():
    """All three filter-y config knobs together. This is the interaction
    test — each constraint has its own unit test, but cross-interactions
    (must_include forcing a stop outside the `states` list, while
    `categories` rides through unused by the solver) only show up when
    everything's applied at once.

    Setup: states=[S0, S1], categories=[type1] (carried but the solver
    doesn't filter on it — fetch_pois does). must_include=[99] forces a
    POI in S2 (outside `states`, different category). No max_stops here
    — the soft-cap interaction is exercised by
    test_max_stops_is_soft_warns_when_violated above. The compound test
    deliberately keeps max_stops loose so the failure mode we're hunting
    is "did must_include + state coverage interact correctly" not "did
    the penalty math become unsatisfiable."
    """
    pois = [
        {"id": 10, "name": "S0_t1_a", "state": "S0", "category": "type1",
         "lat": 0.0, "lon": 0.0},
        {"id": 11, "name": "S0_t1_b", "state": "S0", "category": "type1",
         "lat": 0.0, "lon": 1.0},
        {"id": 20, "name": "S1_t1",   "state": "S1", "category": "type1",
         "lat": 1.0, "lon": 0.0},
        # Forced inclusion target: NOT in states list, NOT in categories list.
        # In a real trip this row would come from fetch_pois's must_include
        # union (covered by test_must_include_outside_filter_emits_warning).
        # Here we hand it to the solver directly to isolate the solver-side
        # behavior.
        {"id": 99, "name": "S2_t2",   "state": "S2", "category": "type2",
         "lat": 2.0, "lon": 2.0},
    ]
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = ((pois[i]["lat"] - pois[j]["lat"]) ** 2 +
                     (pois[i]["lon"] - pois[j]["lon"]) ** 2) ** 0.5
                dur[i][j] = d * 3600
                dist[i][j] = d * 1609.344

    cfg = TripConfig(
        name="x",
        states=["S0", "S1"],
        categories=["type1"],       # carried through but unused by solver
        must_include=[99],
        # No max_stops — the soft cap is tested elsewhere; here we want
        # to verify state-coverage + must_include compose cleanly.
        time_limit_seconds=10,
    )
    result = solve_with_config(cfg, pois, dur, dist)

    visited_ids = {node.id for node in result.order}
    visited_states = {node.state for node in result.order}

    # 1. Forced POI is in the tour
    assert 99 in visited_ids, (
        f"must_include POI 99 was dropped. visited_ids={visited_ids}, "
        f"status={result.status}"
    )
    # 2. Required states are covered (S0 and S1 — S2 isn't required, it's
    #    only present because of must_include)
    assert "S0" in visited_states and "S1" in visited_states, (
        f"Required state coverage broken. visited_states={visited_states}"
    )
    # 3. Tour is non-degenerate (the empty-result failure mode we hit while
    #    developing this test). At minimum we need 2 state-cover stops + 1
    #    forced stop = 3.
    assert len(result.order) >= 3


# ===========================================================================
# Time-budgeted mode (Tier 2 Phase 2). The "headline" feature from doc 05:
# when total_trip_days is set, the solver maximizes POI value within a soft
# time budget instead of requiring state coverage. Tests below pin the
# mechanic at increasing levels of integration.
# ===========================================================================

from src.solver import _poi_value, _solve_time_budgeted


def test_poi_value_uses_poi_priority_first():
    """Per-POI override beats category fallback."""
    cfg = TripConfig(
        name="x",
        poi_priority={42: 99},
        category_priority={"national_park": 5},
    )
    poi = {"id": 42, "name": "P", "state": "S0",
           "category": "national_park", "lat": 0.0, "lon": 0.0}
    assert _poi_value(poi, cfg) == 99


def test_poi_value_falls_back_to_category():
    cfg = TripConfig(
        name="x",
        poi_priority={42: 99},
        category_priority={"national_park": 5},
    )
    # POI id 100 isn't in poi_priority, so category should win
    poi = {"id": 100, "name": "P", "state": "S0",
           "category": "national_park", "lat": 0.0, "lon": 0.0}
    assert _poi_value(poi, cfg) == 5


def test_poi_value_defaults_to_zero():
    """No poi_priority, no category_priority → 0 (incidental POI)."""
    cfg = TripConfig(name="x")
    poi = {"id": 1, "name": "P", "state": "S0",
           "category": "unknown_category", "lat": 0.0, "lon": 0.0}
    assert _poi_value(poi, cfg) == 0


def _budget_test_pois():
    """5 POIs in a line, varying values. Total drive (visiting all from
    depot at idx 0 and returning) is 10 units. A tight budget forces the
    solver to skip lower-value POIs."""
    return [
        {"id": 0, "name": "depot", "state": "S0",
         "category": "depot", "lat": 0.0, "lon": 0.0},
        {"id": 1, "name": "lowA",  "state": "S1",
         "category": "tier3", "lat": 0.0, "lon": 1.0},
        {"id": 2, "name": "highB", "state": "S2",
         "category": "tier1", "lat": 0.0, "lon": 2.0},
        {"id": 3, "name": "lowC",  "state": "S3",
         "category": "tier3", "lat": 0.0, "lon": 3.0},
        {"id": 4, "name": "highD", "state": "S4",
         "category": "tier1", "lat": 0.0, "lon": 4.0},
    ]


def _budget_test_matrices(pois):
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = abs(pois[i]["lon"] - pois[j]["lon"])
                dur[i][j] = d * 3600  # 1 lon-unit = 1 hour
                dist[i][j] = d * 1609.344
    return dur, dist


def test_time_budgeted_visits_high_value_when_budget_loose():
    """Loose budget (12h) + 4 candidates spread over 8h round-trip:
    solver should pick all 4 since the value-vs-time tradeoff favors
    visiting everything."""
    pois = _budget_test_pois()
    dur, dist = _budget_test_matrices(pois)
    cfg = TripConfig(
        name="x",
        total_trip_days=2,            # 2 × 8 = 16h budget
        category_priority={"tier1": 10, "tier3": 1},
        time_limit_seconds=10,
    )
    result = _solve_time_budgeted(cfg, pois, dur, dist)
    visited = {n.id for n in result.order}
    # All 4 candidates + depot = 5 visited; check at minimum the two
    # high-value POIs (2 and 4) are present.
    assert 2 in visited and 4 in visited, (
        f"High-value POIs (id=2, id=4) should be visited under loose budget. "
        f"Got: {visited}"
    )


def test_time_budgeted_skips_low_value_when_budget_tight():
    """Tight budget (3h, but driving to all 4 is 8h+) forces the solver
    to prioritize: skip low-value POIs, keep high-value ones."""
    pois = _budget_test_pois()
    dur, dist = _budget_test_matrices(pois)
    cfg = TripConfig(
        name="x",
        total_trip_days=1,
        max_hours_per_day=3.0,        # 3h budget — visiting all 4 is impossible
        category_priority={"tier1": 100, "tier3": 1},
        time_budget_overage_penalty=50.0,  # strong penalty (50 pts/hour over)
        time_limit_seconds=10,
    )
    result = _solve_time_budgeted(cfg, pois, dur, dist)
    visited = {n.id for n in result.order}
    # Solver should prefer the tier1 POIs (id 2 and 4, value=100) over
    # the tier3 POIs (id 1 and 3, value=1). With 3h budget and 4 going
    # to be 8h, expect tier3 POIs to drop first.
    tier3_visited = (1 in visited) + (3 in visited)
    tier1_visited = (2 in visited) + (4 in visited)
    assert tier1_visited >= tier3_visited, (
        f"Expected solver to prefer high-value POIs under tight budget. "
        f"Visited tier1={tier1_visited}, tier3={tier3_visited}, "
        f"all visited={visited}"
    )


def test_time_budgeted_must_include_overrides_value_ranking():
    """A low/zero-value POI forced via must_include must still be visited,
    even if the budget-aware solver wouldn't pick it on value grounds."""
    pois = _budget_test_pois()
    dur, dist = _budget_test_matrices(pois)
    cfg = TripConfig(
        name="x",
        total_trip_days=1,
        max_hours_per_day=3.0,        # very tight
        category_priority={"tier1": 100},  # tier3 has value=0
        must_include=[1],             # force the low-value POI id=1
        time_limit_seconds=10,
    )
    result = _solve_time_budgeted(cfg, pois, dur, dist)
    visited = {n.id for n in result.order}
    assert 1 in visited, (
        f"must_include POI 1 must be visited regardless of value. "
        f"Got: {visited}"
    )


def test_time_budgeted_loop_false_excludes_closing_leg():
    """With loop=False the reported total_cost should not include the
    return-to-depot leg cost. Verify against a loop=True baseline on the
    same setup."""
    pois = _budget_test_pois()
    dur, dist = _budget_test_matrices(pois)
    base_kwargs = dict(
        name="x",
        total_trip_days=2,
        category_priority={"tier1": 100, "tier3": 50},
        time_limit_seconds=10,
    )
    result_loop = _solve_time_budgeted(
        TripConfig(loop=True, **base_kwargs), pois, dur, dist
    )
    result_open = _solve_time_budgeted(
        TripConfig(loop=False, start_state="S0", **base_kwargs), pois, dur, dist
    )
    # If both tours visit the same set, the open path is strictly shorter
    # by exactly the closing leg cost. Allow equality if the tours differ
    # (solver might pick different stops).
    assert result_open.total_cost <= result_loop.total_cost, (
        f"Open path cost ({result_open.total_cost:.0f}) should not exceed "
        f"loop cost ({result_loop.total_cost:.0f})"
    )


def test_solve_with_config_dispatches_to_time_budgeted_when_total_days_set():
    """The MODE switch in solve_with_config: presence of total_trip_days
    activates time-budgeted; absence keeps state-coverage. Pin both
    branches via patch counts."""
    from unittest.mock import patch
    from src.solver import solve_with_config

    pois = _budget_test_pois()
    dur, dist = _budget_test_matrices(pois)

    # Branch A: total_trip_days=None → state-coverage (the old path)
    cfg_state = TripConfig(name="x", states=["S1", "S2"], time_limit_seconds=5)
    with patch("src.solver._solve_time_budgeted") as mock_tb:
        solve_with_config(cfg_state, pois, dur, dist)
    assert mock_tb.call_count == 0, "State-coverage config must not call time-budgeted"

    # Branch B: total_trip_days=2 → time-budgeted. Note: `states` would
    # normally trigger the mode-change warning; we set it to None to
    # keep this test focused on dispatch.
    cfg_tb = TripConfig(name="x", total_trip_days=2, time_limit_seconds=5)
    with patch("src.solver._solve_time_budgeted") as mock_tb:
        mock_tb.return_value = "sentinel"
        result = solve_with_config(cfg_tb, pois, dur, dist)
    assert mock_tb.call_count == 1, "Time-budgeted config must call _solve_time_budgeted"
    assert result == "sentinel", "Dispatch must return the time-budgeted result"
