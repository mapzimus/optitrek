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


def test_deferred_fields_warn_when_set():
    with pytest.warns(UserWarning, match="category_priority.*activates in time-budgeted"):
        TripConfig(name="x", category_priority={"National Park": 5})
    with pytest.warns(UserWarning, match="total_trip_days.*activates in time-budgeted"):
        TripConfig(name="x", total_trip_days=14)


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
