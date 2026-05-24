"""Tests for the Stage 1 FastAPI web frontend.

Three layers of coverage:
  1. form_parser pure-logic tests (no FastAPI, no DB)
  2. Route smoke tests via FastAPI TestClient (DB + run_trip mocked)
  3. End-to-end form submission (mocks the pipeline, exercises the
     whole request → form_parser → run_trip → template stack)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.config import TripConfig, TripConfigError
from src.web.form_parser import (
    form_to_config,
    _parse_poi_priority_textarea,
    _dict_str_int_from_prefix,
    _list_int_csv,
    _list_str,
    _opt_int,
    _opt_str,
)


# ============================================================
# 1. form_parser pure-logic tests
# ============================================================


def test_opt_str_empty_becomes_none():
    """The form parser converts blank submissions to None so
    downstream TripConfig validation ("None means no filter") works."""
    assert _opt_str({"name": ""}, "name") is None
    assert _opt_str({"name": "   "}, "name") is None
    assert _opt_str({}, "name") is None
    assert _opt_str({"name": "x"}, "name") == "x"


def test_opt_int_handles_blank_and_int_strings():
    assert _opt_int({"max_stops": ""}, "max_stops") is None
    assert _opt_int({"max_stops": "10"}, "max_stops") == 10
    assert _opt_int({}, "max_stops") is None


def test_list_str_normalizes_empty_to_none():
    """A multiselect with nothing chosen comes through as an empty list
    or absent key; both must map to None (TripConfig's "no filter")."""
    assert _list_str({}, "states") is None
    assert _list_str({"states": []}, "states") is None
    assert _list_str({"states": [""]}, "states") is None
    assert _list_str({"states": ["CA", "NV"]}, "states") == ["CA", "NV"]
    # Whitespace gets stripped
    assert _list_str({"states": ["  CA  ", " NV"]}, "states") == ["CA", "NV"]


def test_list_int_csv_parses_comma_separated_ids():
    assert _list_int_csv({"must_include": ""}, "must_include") == []
    assert _list_int_csv({"must_include": "42"}, "must_include") == [42]
    assert _list_int_csv(
        {"must_include": "42, 107, 192"}, "must_include"
    ) == [42, 107, 192]
    # Tolerates extra whitespace and trailing commas
    assert _list_int_csv(
        {"must_include": "  42 ,107,192,  "}, "must_include"
    ) == [42, 107, 192]


def test_dict_str_int_from_prefix_collects_bracketed_fields():
    """Bracketed form names like `category_priority[national_park]=10`
    get assembled into a dict. This is the pattern HTML forms use for
    structured input."""
    form = {
        "category_priority[national_park]": "10",
        "category_priority[national_monument]": "5",
        "category_priority[national_historic_site]": "",  # blank → skip
        "name": "x",                                       # unrelated
        "states": ["CA"],                                  # unrelated
    }
    result = _dict_str_int_from_prefix(form, "category_priority")
    assert result == {"national_park": 10, "national_monument": 5}


def test_dict_str_int_skips_malformed_values():
    """A non-int value (typo, accidentally pasted text) shouldn't blow
    up the whole submission — skip that row and keep the rest."""
    form = {
        "category_priority[national_park]": "10",
        "category_priority[national_monument]": "not_a_number",
    }
    result = _dict_str_int_from_prefix(form, "category_priority")
    assert result == {"national_park": 10}


def test_parse_poi_priority_textarea_happy_path():
    text = """
    # Iconic parks
    192: 25
    89:  20
    142: 22
    """
    out, warnings = _parse_poi_priority_textarea(text)
    assert out == {192: 25, 89: 20, 142: 22}
    assert warnings == []


def test_parse_poi_priority_textarea_skips_malformed_lines_with_warnings():
    text = """
    192: 25
    not_a_number: 5
    89:not_an_int
    142: 22
    """
    out, warnings = _parse_poi_priority_textarea(text)
    assert out == {192: 25, 142: 22}
    # Two warning entries — the two malformed lines
    assert len(warnings) == 2
    assert all("doesn't match" in w for w in warnings)


def test_parse_poi_priority_textarea_warns_on_duplicate_id():
    """Duplicate POI IDs in the textarea — keep the last value, warn."""
    text = """
    192: 25
    192: 30
    """
    out, warnings = _parse_poi_priority_textarea(text)
    assert out == {192: 30}  # last wins
    assert any("appears more than once" in w for w in warnings)


def test_parse_poi_priority_empty_input():
    out, warnings = _parse_poi_priority_textarea("")
    assert out == {}
    assert warnings == []
    out, warnings = _parse_poi_priority_textarea(None)
    assert out == {}
    assert warnings == []


def test_form_to_config_minimal_form():
    """Smallest valid submission: just a name and loop checked."""
    form = {"name": "test_trip", "loop": "on"}
    cfg, warnings = form_to_config(form)
    assert cfg.name == "test_trip"
    assert cfg.loop is True
    assert cfg.routing_network == "us"
    assert cfg.border_crossing_minutes == 20
    assert cfg.total_trip_days is None
    assert warnings == []


def test_form_to_config_full_form():
    """Most fields filled — verify each lands on the right TripConfig
    attribute with the right type."""
    form = {
        "name": "full_trip",
        "loop": "on",
        "states": ["CA", "NV", "AZ"],
        "categories": ["national_park"],
        "must_include": "42, 107",
        "max_stops": "20",
        "start_state": "CA",
        "max_hours_per_day": "10",
        "time_limit_seconds": "600",
        "routing_network": "us_canada",
        "border_crossing_minutes": "30",
        "total_trip_days": "7",
        "time_budget_overage_penalty": "2.5",
        "category_priority[national_park]": "10",
        "category_priority[national_monument]": "5",
        "poi_priority_textarea": "192: 25\n89: 20",
    }
    cfg, warnings = form_to_config(form)
    assert cfg.name == "full_trip"
    assert cfg.states == ["CA", "NV", "AZ"]
    assert cfg.categories == ["national_park"]
    assert cfg.must_include == [42, 107]
    assert cfg.max_stops == 20
    assert cfg.start_state == "CA"
    assert cfg.max_hours_per_day == 10.0
    assert cfg.time_limit_seconds == 600
    assert cfg.routing_network == "us_canada"
    assert cfg.border_crossing_minutes == 30
    assert cfg.total_trip_days == 7
    assert cfg.time_budget_overage_penalty == 2.5
    assert cfg.category_priority == {"national_park": 10, "national_monument": 5}
    assert cfg.poi_priority == {192: 25, 89: 20}


def test_form_to_config_unchecked_loop_defaults_true():
    """If 'loop' isn't in the form (checkbox unchecked) we still want
    the TripConfig default behavior — not silently flip to False."""
    form = {"name": "x"}  # no `loop` key
    cfg, _ = form_to_config(form)
    assert cfg.loop is True


def test_form_to_config_invalid_config_raises():
    """Form parser delegates hard validation to TripConfig — it should
    propagate TripConfigError so the route handler can render a 400."""
    form = {"name": "bad name with spaces"}
    with pytest.raises(TripConfigError, match="filename-safe"):
        form_to_config(form)


# ============================================================
# 2. Route smoke tests via TestClient
# ============================================================


# `_fetch_categories` and `_search_pois` hit the DB — patch them so
# tests don't need Neon. We test the route wiring, not the SQL.

@pytest.fixture
def client(monkeypatch):
    """TestClient with the DB-touching helpers stubbed to small fixed
    lists so tests are hermetic."""
    from src.web import main as web_main
    monkeypatch.setattr(web_main, "_fetch_categories",
                        lambda: ["national_park", "national_monument"])
    monkeypatch.setattr(web_main, "_search_pois",
                        lambda q, limit=25: (
                            [{"id": 192, "name": "Grand Canyon National Park",
                              "state": "AZ", "category": "national_park"}]
                            if "canyon" in q.lower() else []
                        ))
    return TestClient(web_main.app)


def test_get_index_renders_form(client):
    """GET / returns the form HTML with the category dropdown populated
    from the mocked _fetch_categories."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    # Sanity: form action + at least one category option
    assert 'action="/solve"' in body
    assert "national_park" in body
    # The category_priority table editor renders one row per category
    assert 'name="category_priority[national_park]"' in body


def test_get_index_handles_db_failure(client, monkeypatch):
    """If _fetch_categories raises, the route should render the error
    page (not 500)."""
    from src.web import main as web_main
    monkeypatch.setattr(web_main, "_fetch_categories",
                        lambda: (_ for _ in ()).throw(RuntimeError("DB down")))
    response = client.get("/")
    assert response.status_code == 503
    assert "Database unreachable" in response.text


def test_api_categories_returns_json(client):
    response = client.get("/api/categories")
    assert response.status_code == 200
    assert response.json() == {"categories": ["national_park", "national_monument"]}


def test_api_poi_search_returns_results_html(client):
    """The endpoint returns HTML (htmx pattern), not JSON. Verify the
    matched POI's name appears."""
    response = client.get("/api/poi-search?q=canyon")
    assert response.status_code == 200
    assert "Grand Canyon National Park" in response.text
    assert "#192" in response.text  # POI ID is rendered


def test_api_poi_search_empty_query_returns_placeholder(client):
    response = client.get("/api/poi-search?q=")
    assert response.status_code == 200
    assert "Search results will appear here" in response.text


def test_api_poi_search_no_matches(client):
    response = client.get("/api/poi-search?q=zzzz_no_match")
    assert response.status_code == 200
    assert "No POIs match" in response.text


def test_post_solve_runs_pipeline_and_renders_result(client, monkeypatch, tmp_path):
    """End-to-end happy path: post the form, get the result page back."""
    from src.web import main as web_main

    # Mock run_trip — return a fake output path that we'll claim exists.
    fake_out = tmp_path / "test_trip.html"
    fake_out.write_text("<html>fake folium map</html>")
    monkeypatch.setattr(web_main, "run_trip", lambda cfg, output_dir: fake_out)

    response = client.post("/solve", data={
        "name": "test_trip",
        "loop": "on",
        "time_limit_seconds": "5",
    })
    assert response.status_code == 200
    body = response.text
    assert "Solve complete" in body
    assert "test_trip" in body
    # The download link should reference the mocked filename
    assert "test_trip.html" in body


def test_post_solve_handles_invalid_config(client):
    """A name with a space fails TripConfig validation; route should
    render the error page (400) not crash."""
    response = client.post("/solve", data={
        "name": "name with spaces",
        "loop": "on",
    })
    assert response.status_code == 400
    assert "Invalid trip config" in response.text
    assert "filename-safe" in response.text


def test_post_solve_handles_osrm_unreachable(client, monkeypatch):
    """If run_trip raises OSRMEngineError, the route should render the
    error page with the actionable fix from the exception message."""
    from src.web import main as web_main
    from src.trip import OSRMEngineError

    def bad_run_trip(cfg, output_dir):
        raise OSRMEngineError("Primary OSRM engine for routing_network='us' "
                              "is unreachable at http://127.0.0.1:5000")

    monkeypatch.setattr(web_main, "run_trip", bad_run_trip)
    response = client.post("/solve", data={
        "name": "test_trip",
        "loop": "on",
    })
    assert response.status_code == 503
    assert "OSRM engine not ready" in response.text
    assert "127.0.0.1:5000" in response.text
