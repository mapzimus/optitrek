"""Tests for src/poi_query.py — SQL generation with a mocked psycopg cursor."""
from unittest.mock import MagicMock

import pytest

from src.config import TripConfig
from src.poi_query import build_query, fetch_pois


def test_minimal_query_excludes_ak_hi_and_territories():
    """Default config (routing_network='us'): AK is excluded because the
    US-only OSRM engine can't route to it. HI + territories are excluded
    unconditionally (no road)."""
    cfg = TripConfig(name="x")
    sql, params = build_query(cfg)
    assert "source = 'nps'" in sql
    assert "state <> ALL(%(excluded)s)" in sql
    # AK is contiguous-unreachable on the US-only engine; HI is an island;
    # PR/VI/GU/MP/AS are US territories outside REQUIRED_STATES. All
    # excluded to keep the Tier 2 candidate set aligned with the routable
    # universe of the active OSRM engine.
    assert set(params["excluded"]) == {"AK", "HI", "PR", "VI", "GU", "MP", "AS"}


def test_us_canada_engine_drops_ak_from_exclusion():
    """D5 follow-up: when routing_network='us_canada', AK is reachable via
    the Alaska Highway (BC + Yukon). The NA OSRM engine has the Alcan in
    its routable graph (verified Seattle→Anchorage = 2,363 mi / 51 h).
    So AK comes OUT of the exclusion list and AK NPS units enter the
    candidate pool. HI + territories stay excluded — they're road-
    unreachable regardless of which engine is in use."""
    cfg = TripConfig(name="x", routing_network="us_canada")
    sql, params = build_query(cfg)
    excluded = set(params["excluded"])
    assert "AK" not in excluded, (
        "AK should be reachable via the us_canada engine. Excluded list: "
        f"{sorted(excluded)}"
    )
    # The rest of the exclusion list is unchanged
    assert excluded == {"HI", "PR", "VI", "GU", "MP", "AS"}


def test_us_engine_explicitly_keeps_ak_excluded():
    """Explicit `routing_network='us'` (not just the default) still drops
    AK. The exclusion key isn't 'engine is non-default' — it's 'engine
    can't route there.' Pin that for symmetry with the us_canada test."""
    cfg = TripConfig(name="x", routing_network="us")
    sql, params = build_query(cfg)
    assert "AK" in set(params["excluded"])


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


def test_must_include_outside_filter_emits_warning():
    """When a must_include POI is in the DB but outside the natural filter
    scope, fetch_pois unions it back AND emits a UserWarning. The warning is
    the only signal a trip author has that an override happened — without
    this characterization, a future refactor could silently drop the warn()
    call and the visible UX would only break for trips that hit the override
    path. Pin the message text so the test catches both behavior changes
    (warning dropped) AND wording changes that would break documentation."""
    from unittest.mock import patch, MagicMock

    fake_cur = MagicMock()
    cols = ["id", "name", "state", "category", "lat", "lon"]
    fake_cur.description = []
    for name in cols:
        col = MagicMock()
        col.name = name
        fake_cur.description.append(col)

    # First execute: natural-filter result (just Acadia in ME — config says
    # states=[ME], categories=[national_park]). Second execute: the
    # must_include POI (Grand Canyon in AZ — outside the filter scope).
    fake_cur.fetchall.side_effect = [
        [(2, "Acadia National Park", "ME", "national_park", 44.35, -68.21)],
        [(1, "Grand Canyon National Park", "AZ", "national_park", 36.06, -112.14)],
    ]

    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    cfg = TripConfig(
        name="x",
        states=["ME"],
        categories=["national_park"],
        must_include=[1],
    )
    with patch("src.poi_query.get_conn", return_value=MagicMock(
        __enter__=lambda self: fake_conn, __exit__=lambda *a: None,
    )):
        with pytest.warns(UserWarning, match=r"must_include POI 1.*outside the filter"):
            pois = fetch_pois(cfg)

    # Override succeeded: both rows present, sorted by (state, id).
    # AZ < ME so Grand Canyon (id=1) comes first.
    assert len(pois) == 2
    assert pois[0]["id"] == 1 and pois[0]["state"] == "AZ"
    assert pois[1]["id"] == 2 and pois[1]["state"] == "ME"


def test_must_include_inside_filter_does_not_warn():
    """Inverse case: must_include POI that's ALSO in the natural filter
    result should NOT emit a warning — there's no override happening, the
    POI was already going to be visited. This pins that we don't warn
    spuriously on trips that pre-include their must-haves."""
    from unittest.mock import patch, MagicMock
    import warnings as warnings_module

    fake_cur = MagicMock()
    cols = ["id", "name", "state", "category", "lat", "lon"]
    fake_cur.description = []
    for name in cols:
        col = MagicMock()
        col.name = name
        fake_cur.description.append(col)

    # Natural filter returns BOTH Acadia and a second ME POI — must_include
    # asks for the second one, which is already in the result, so no
    # second query happens.
    fake_cur.fetchall.side_effect = [
        [
            (2, "Acadia National Park", "ME", "national_park", 44.35, -68.21),
            (7, "Katahdin Woods", "ME", "national_monument", 46.10, -68.86),
        ],
    ]

    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    cfg = TripConfig(name="x", states=["ME"], must_include=[7])
    with patch("src.poi_query.get_conn", return_value=MagicMock(
        __enter__=lambda self: fake_conn, __exit__=lambda *a: None,
    )):
        with warnings_module.catch_warnings(record=True) as caught:
            warnings_module.simplefilter("always")
            pois = fetch_pois(cfg)
        # No must_include-related UserWarning fired
        assert not any("must_include" in str(w.message) for w in caught)

    assert len(pois) == 2
