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
    # Walk: stop 0 starts day. leg 0 (3h) → stop 1, today=3.
    # leg 1 (3h) → stop 2, today=6. leg 2 (3h) → 9>8 → new day,
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
