"""Unit tests for src/data_pull.py: _parse_park()

These pin the validation contract on the NPS API response shape so we catch
regressions if the API changes (e.g. coords becoming strings, designation
labels shifting). No network calls, no DB.
"""
from __future__ import annotations

from src.data_pull import _parse_park


# A realistic NPS API response shape (trimmed; the actual response has more
# fields but _parse_park only reads these).
def _make_park(**overrides) -> dict:
    base = {
        "parkCode": "yell",
        "fullName": "Yellowstone National Park",
        "designation": "National Park",
        "latitude": "44.4280",
        "longitude": "-110.5885",
        "states": "ID,MT,WY",
    }
    base.update(overrides)
    return base


def test_happy_path_known_designation():
    row, reason = _parse_park(_make_park())
    assert reason is None
    assert row["park_code"] == "yell"
    assert row["name"] == "Yellowstone National Park"
    assert row["category"] == "national_park"
    assert row["designation"] == "National Park"
    assert row["lat"] == 44.4280
    assert row["lon"] == -110.5885
    assert row["api_states"] == "ID,MT,WY"


def test_unknown_designation_falls_through_to_nps_other():
    row, reason = _parse_park(_make_park(designation="National Geologic Curiosity"))
    assert reason is None
    assert row["category"] == "nps_other"


def test_designation_aliases_map_to_canonical_category():
    # Several NPS labels map to the same normalized category. Pin a couple.
    park = _make_park(parkCode="vick", designation="National Military Park")
    row, _ = _parse_park(park)
    assert row["category"] == "national_battlefield"

    park = _make_park(parkCode="appa", designation="National Scenic Trail")
    row, _ = _parse_park(park)
    assert row["category"] == "national_trail"


def test_missing_coords_discarded():
    row, reason = _parse_park(_make_park(latitude="", longitude=""))
    assert row is None
    assert reason == "missing_coords"

    row, reason = _parse_park(_make_park(latitude=None, longitude=None))
    assert row is None
    assert reason == "missing_coords"


def test_non_numeric_coords_discarded():
    row, reason = _parse_park(_make_park(latitude="not-a-number", longitude="-110"))
    assert row is None
    assert reason == "non_numeric_coords"


def test_zero_coords_discarded():
    """0,0 is the classic 'forgot to set coords' default. Treat as bad data."""
    row, reason = _parse_park(_make_park(latitude="0", longitude="0"))
    assert row is None
    assert reason == "zero_coords"


def test_coords_outside_bounding_box_discarded():
    # Way south of the wide US box (e.g., a bogus 0/-100 isn't caught by zero
    # check but should still be in-bounds, so test a real out-of-range one).
    row, reason = _parse_park(_make_park(latitude="-5.0", longitude="-100"))
    assert row is None
    assert reason.startswith("lat_out_of_bounds")

    row, reason = _parse_park(_make_park(latitude="40.0", longitude="20.0"))
    assert row is None
    assert reason.startswith("lon_out_of_bounds")


def test_missing_park_code_discarded():
    row, reason = _parse_park(_make_park(parkCode=""))
    assert row is None
    assert reason == "missing_park_code_or_name"


def test_missing_name_discarded():
    row, reason = _parse_park(_make_park(fullName="", **{"name": ""}))
    assert row is None
    assert reason == "missing_park_code_or_name"


def test_alaska_park_is_kept_not_discarded():
    """AK parks have valid coords just outside the contiguous US. They should
    pass parsing and be loaded into the DB — the solver filters them out
    later, but the data layer is permissive."""
    row, reason = _parse_park(_make_park(
        parkCode="denai",
        fullName="Denali National Park and Preserve",
        latitude="63.1148",
        longitude="-151.1926",
        states="AK",
    ))
    assert reason is None
    assert row["park_code"] == "denai"
