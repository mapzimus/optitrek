"""Tests for src/config.py — TripConfig dataclass + YAML loader + validation."""
from pathlib import Path
import pytest
import yaml

from src.config import TripConfig, TripConfigError, load_config
from src.config import InfeasibleMaxStops


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


def test_load_config_rejects_empty_yaml(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    with pytest.raises(TripConfigError, match="empty or contains only comments"):
        load_config(p)


def test_load_config_rejects_comments_only_yaml(tmp_path: Path):
    p = tmp_path / "comments.yaml"
    p.write_text("# just a comment\n# nothing else\n")
    with pytest.raises(TripConfigError, match="empty or contains only comments"):
        load_config(p)


def test_load_config_rejects_unknown_field(tmp_path: Path):
    p = tmp_path / "typo.yaml"
    p.write_text("name: x\nmax_stop: 10\n")  # max_stops typo'd as max_stop
    with pytest.raises(TripConfigError, match="max_stop"):
        load_config(p)


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


def test_time_budgeted_fields_no_longer_deferred():
    """category_priority and total_trip_days used to emit
    'deferred — activates in Phase 2' UserWarnings; they're now wired
    into solve_with_config so setting them on a fresh config should be
    silent. Pin that to catch any regression that re-introduces a
    deferred warning."""
    import warnings as warnings_module
    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        TripConfig(name="x", category_priority={"National Park": 5})
        TripConfig(name="x", total_trip_days=14)
    deferred_messages = [
        str(w.message) for w in caught
        if "activates in" in str(w.message) or "Phase 2" in str(w.message)
    ]
    assert not deferred_messages, (
        f"Expected no 'deferred field' warnings, got: {deferred_messages}"
    )


# ---------- time-budgeted mode field validation ----------


def test_total_trip_days_must_be_positive():
    with pytest.raises(TripConfigError, match=r"total_trip_days.*must be > 0"):
        TripConfig(name="x", total_trip_days=0)
    with pytest.raises(TripConfigError, match=r"total_trip_days.*must be > 0"):
        TripConfig(name="x", total_trip_days=-3)


def test_max_hours_per_day_must_be_positive():
    with pytest.raises(TripConfigError, match=r"max_hours_per_day.*must be > 0"):
        TripConfig(name="x", max_hours_per_day=0)
    with pytest.raises(TripConfigError, match=r"max_hours_per_day.*must be > 0"):
        TripConfig(name="x", max_hours_per_day=-1.5)


def test_time_budget_overage_penalty_must_be_non_negative():
    with pytest.raises(TripConfigError, match=r"time_budget_overage_penalty.*must be >= 0"):
        TripConfig(name="x", time_budget_overage_penalty=-0.5)
    # zero IS allowed — means "hard budget, no overage permitted at all"
    # is misleading (the soft-cap mechanic still works, just with zero
    # penalty, which functionally means the budget is advisory). Test
    # documents the intent.
    cfg = TripConfig(name="x", time_budget_overage_penalty=0.0)
    assert cfg.time_budget_overage_penalty == 0.0


def test_time_budgeted_with_states_warns_about_mode_change():
    """When a trip author sets BOTH total_trip_days and states, they may
    not realize states is now just a filter (not a coverage requirement).
    Pin the warning so this UX detail can't silently regress."""
    with pytest.warns(UserWarning, match=r"time-budgeted mode.*geographic FILTER"):
        TripConfig(
            name="x",
            total_trip_days=7,
            states=["CA", "UT", "AZ"],
        )


def test_time_budgeted_without_states_does_not_warn():
    """Inverse: time-budgeted without states is the clean case, no
    warning should fire."""
    import warnings as warnings_module
    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        TripConfig(name="x", total_trip_days=7)
    mode_warnings = [w for w in caught if "geographic FILTER" in str(w.message)]
    assert not mode_warnings


def test_poi_priority_accepts_int_dict():
    cfg = TripConfig(name="x", poi_priority={42: 10, 107: 5})
    assert cfg.poi_priority == {42: 10, 107: 5}


def test_routing_network_default_is_us():
    cfg = TripConfig(name="x")
    assert cfg.routing_network == "us"


def test_routing_network_accepts_known_values():
    # Both known values are valid
    assert TripConfig(name="x", routing_network="us").routing_network == "us"
    assert TripConfig(name="x", routing_network="us_canada").routing_network == "us_canada"


def test_routing_network_rejects_unknown():
    with pytest.raises(TripConfigError, match="routing_network.*must be one of"):
        TripConfig(name="x", routing_network="mexico")
    with pytest.raises(TripConfigError, match="routing_network.*must be one of"):
        TripConfig(name="x", routing_network="UK")
    with pytest.raises(TripConfigError, match="routing_network.*must be one of"):
        TripConfig(name="x", routing_network="")


# ---------- border_crossing_minutes ----------


def test_border_crossing_default_is_20():
    # Rationale: matches the CBP/CBSA "normal weekday" average. Anyone
    # changing this default should expect Tier 1-style trips to shift.
    cfg = TripConfig(name="x")
    assert cfg.border_crossing_minutes == 20


def test_border_crossing_accepts_zero():
    # Zero disables the penalty — useful for NEXUS travelers or diagnostic
    # runs that want to see the raw OSRM savings without overhead.
    cfg = TripConfig(name="x", border_crossing_minutes=0)
    assert cfg.border_crossing_minutes == 0


def test_border_crossing_rejects_negative():
    with pytest.raises(TripConfigError, match=r"border_crossing_minutes.*\[0, 240\]"):
        TripConfig(name="x", border_crossing_minutes=-1)


def test_border_crossing_rejects_absurd_high():
    # Anything beyond 4 hours is almost certainly a typo (or the user is
    # crossing on a US presidential inauguration day, in which case they
    # should reroute through the US anyway).
    with pytest.raises(TripConfigError, match=r"border_crossing_minutes.*\[0, 240\]"):
        TripConfig(name="x", border_crossing_minutes=500)
