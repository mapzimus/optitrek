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
