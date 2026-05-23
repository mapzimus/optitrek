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
