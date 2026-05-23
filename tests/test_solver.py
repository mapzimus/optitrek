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
