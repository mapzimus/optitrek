"""Unit tests for src/osm_pull.py: the Overpass element-parsing contract.

Same philosophy as test_data_pull.py — pin the validation rules from spec 04
§1.3 (attractions) and §3.2 (overnight-city population filter) against
realistic Overpass response shapes. No network calls, no DB.
"""
from __future__ import annotations

from src.osm_pull import (
    TAG_QUERIES,
    _parse_element,
    _parse_place,
    build_overpass_query,
    build_places_query,
    dedupe_rows,
    parse_population,
)


def _make_node(**overrides) -> dict:
    base = {
        "type": "node",
        "id": 358830550,
        "lat": 41.8826,
        "lon": -87.6233,
        "tags": {
            "tourism": "museum",
            "name": "Art Institute of Chicago",
            "wikidata": "Q239303",
        },
    }
    tags = overrides.pop("tags", None)
    base.update(overrides)
    if tags is not None:
        base["tags"] = tags
    return base


def _make_way(**overrides) -> dict:
    base = {
        "type": "way",
        "id": 33508689,
        "center": {"lat": 38.8893, "lon": -77.0502},
        "tags": {"historic": "memorial", "name": "Lincoln Memorial"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- attractions

def test_happy_path_node():
    row, reason = _parse_element(_make_node(), "museum")
    assert reason is None
    assert row["osm_id"] == "node/358830550"
    assert row["name"] == "Art Institute of Chicago"
    assert row["category"] == "museum"
    assert row["lat"] == 41.8826
    assert row["lon"] == -87.6233
    # Full tag dict is preserved, including the wikidata ID for Tier 3
    # collection generation (spec §1.3).
    assert row["tags"]["wikidata"] == "Q239303"


def test_way_uses_center_coords():
    row, reason = _parse_element(_make_way(), "historic_marker")
    assert reason is None
    assert row["osm_id"] == "way/33508689"
    assert row["lat"] == 38.8893
    assert row["lon"] == -77.0502


def test_relation_uses_center_coords():
    el = _make_way(type="relation", id=2202604)
    row, reason = _parse_element(el, "historic_marker")
    assert reason is None
    assert row["osm_id"] == "relation/2202604"


def test_missing_name_discarded():
    row, reason = _parse_element(_make_node(tags={"tourism": "museum"}), "museum")
    assert row is None
    assert reason == "missing_name"


def test_whitespace_name_discarded():
    row, reason = _parse_element(
        _make_node(tags={"tourism": "museum", "name": "   "}), "museum"
    )
    assert row is None
    assert reason == "missing_name"


def test_way_without_center_discarded():
    el = _make_way()
    del el["center"]
    row, reason = _parse_element(el, "historic_marker")
    assert row is None
    assert reason == "missing_coords"


def test_outside_contiguous_us_discarded():
    """Unlike NPS (kept permissively, filtered at solve time), OSM rows in
    AK/HI are discarded at parse time per spec §1.3."""
    row, reason = _parse_element(_make_node(lat=61.2181, lon=-149.9003), "museum")  # Anchorage
    assert row is None
    assert reason.startswith("lat_out_of_bounds")

    row, reason = _parse_element(_make_node(lat=21.3069, lon=-157.8583), "museum")  # Honolulu
    assert row is None
    assert reason.startswith("lat_out_of_bounds")

    row, reason = _parse_element(_make_node(lat=44.0, lon=-150.0), "museum")
    assert row is None
    assert reason.startswith("lon_out_of_bounds")


def test_disused_abandoned_private_discarded():
    for key, reason_expected in (
        ("disused", "disused"),
        ("abandoned", "abandoned"),
    ):
        tags = {"tourism": "museum", "name": "Old Mill Museum", key: "yes"}
        row, reason = _parse_element(_make_node(tags=tags), "museum")
        assert row is None
        assert reason == reason_expected

    tags = {"tourism": "museum", "name": "Private Collection", "access": "private"}
    row, reason = _parse_element(_make_node(tags=tags), "museum")
    assert row is None
    assert reason == "access_private"


def test_access_customers_is_kept():
    """Only access=private is a discard rule; softer access values pass."""
    tags = {"tourism": "theme_park", "name": "Dollywood", "access": "customers"}
    row, reason = _parse_element(_make_node(lat=35.795, lon=-83.530, tags=tags), "theme_park")
    assert reason is None
    assert row is not None


def test_missing_id_discarded():
    el = _make_node()
    del el["id"]
    row, reason = _parse_element(el, "museum")
    assert row is None
    assert reason == "missing_type_or_id"


# ------------------------------------------------------------ overnight cities

def test_population_parsing():
    assert parse_population("114394") == 114394
    assert parse_population("114,394") == 114394
    assert parse_population("5 200") == 5200
    assert parse_population("~8000") == 8000
    assert parse_population("unknown") is None
    assert parse_population(None) is None


def _make_place(place: str, population: str | None) -> dict:
    tags = {"place": place, "name": "Springfield"}
    if population is not None:
        tags["population"] = population
    return _make_node(id=151357430, lat=39.7990, lon=-89.6440, tags=tags)


def test_city_above_threshold_kept():
    row, reason = _parse_place(_make_place("city", "114394"))
    assert reason is None
    assert row["category"] == "overnight_city"


def test_town_above_threshold_kept():
    row, reason = _parse_place(_make_place("town", "12000"))
    assert reason is None


def test_below_threshold_discarded_even_for_city():
    row, reason = _parse_place(_make_place("city", "800"))
    assert row is None
    assert reason == "population_below_threshold"


def test_city_without_population_kept():
    """Spec §3.2: cities get the benefit of the doubt when untagged."""
    row, reason = _parse_place(_make_place("city", None))
    assert reason is None


def test_town_without_population_discarded():
    row, reason = _parse_place(_make_place("town", None))
    assert row is None
    assert reason == "town_population_unknown"


def test_town_with_unparseable_population_discarded():
    row, reason = _parse_place(_make_place("town", "lots"))
    assert row is None
    assert reason == "town_population_unknown"


# ------------------------------------------------------------------- plumbing

def test_dedupe_keeps_first_occurrence():
    rows = [
        {"osm_id": "node/1", "category": "museum"},
        {"osm_id": "node/2", "category": "zoo"},
        {"osm_id": "node/1", "category": "landmark"},  # same node, later batch
    ]
    unique, n_dupes = dedupe_rows(rows)
    assert n_dupes == 1
    assert [r["osm_id"] for r in unique] == ["node/1", "node/2"]
    assert unique[0]["category"] == "museum"  # first (most specific) wins


def test_generic_landmark_query_is_last():
    """tourism=attraction is a catch-all; if it ran before the specific
    queries, first-wins dedupe would mislabel museums etc. as landmarks."""
    assert TAG_QUERIES[-1] == ("tourism", "attraction", "landmark")
    assert [c for _, _, c in TAG_QUERIES].count("landmark") == 1


def test_overpass_query_shape():
    q = build_overpass_query("CA", "tourism", "museum")
    assert '"ISO3166-2"="US-CA"' in q
    assert '["tourism"="museum"]' in q
    assert '["name"]' in q          # server-side no-name discard
    assert "out center" in q        # ways/relations need a center point


def test_places_query_shape():
    q = build_places_query()
    assert '"place"="city"' in q
    assert '"place"="town"' in q
    assert "(24.0,-125.0,50.0,-66.0)" in q  # spec §3.1 contiguous-US bbox
