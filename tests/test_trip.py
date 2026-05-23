"""End-to-end orchestrator tests with mocked fetch_pois + matrix build."""
import os
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest

from src.config import TripConfig, EmptyCandidatePool
from src.trip import run_trip, _osrm_url_for_network


# ---------------------------------------------------------------------------
# _osrm_url_for_network — pure-logic helper used by run_trip() to pick which
# OSRM engine to hit based on TripConfig.routing_network. The matrix-builder
# and the polyline-fetcher MUST receive the same URL or the rendered map will
# misrepresent the solver's solution, so it's worth pinning this contract.
# ---------------------------------------------------------------------------

def test_osrm_url_for_network_us_default():
    # No env var set → falls back to 127.0.0.1:5000
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OSRM_URL", None)
        assert _osrm_url_for_network("us") == "http://127.0.0.1:5000"


def test_osrm_url_for_network_us_canada_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OSRM_URL_NA", None)
        assert _osrm_url_for_network("us_canada") == "http://127.0.0.1:5001"


def test_osrm_url_env_var_override_us():
    with patch.dict(os.environ, {"OSRM_URL": "http://elsewhere:5000"}):
        assert _osrm_url_for_network("us") == "http://elsewhere:5000"


def test_osrm_url_env_var_override_us_canada():
    with patch.dict(os.environ, {"OSRM_URL_NA": "http://elsewhere:5001"}):
        assert _osrm_url_for_network("us_canada") == "http://elsewhere:5001"


def test_osrm_url_unknown_network_falls_back_to_us():
    # Defensive: any future routing_network value the dispatcher doesn't know
    # about should land on the US-only engine (the safe default). The
    # TripConfig validator should have rejected it before we got here, but
    # we don't want the function to crash if a caller bypasses validation.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OSRM_URL", None)
        assert _osrm_url_for_network("mexico") == "http://127.0.0.1:5000"


def _fake_pois():
    return [
        {"id": i, "name": f"P{i}", "state": f"S{i % 3}",
         "category": "National Park", "lat": float(i), "lon": float(i)}
        for i in range(6)
    ]


def _fake_matrices(pois):
    n = len(pois)
    dur = np.zeros((n, n), dtype=np.float32)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                d = ((pois[i]["lat"] - pois[j]["lat"]) ** 2 +
                     (pois[i]["lon"] - pois[j]["lon"]) ** 2) ** 0.5
                dur[i][j] = d * 3600 + 600
                dist[i][j] = d * 1609.344
    return dur, dist


def test_run_trip_happy_path(tmp_path: Path):
    cfg = TripConfig(name="test_trip", time_limit_seconds=5)
    with patch("src.trip.fetch_pois", return_value=_fake_pois()), \
         patch("src.trip.build_matrix", side_effect=lambda pois, osrm_url=None: _fake_matrices(pois)):
        out = run_trip(cfg, output_dir=tmp_path)
    assert out.exists()
    assert out.suffix == ".html"
    assert out.name == "test_trip.html"


def test_run_trip_empty_pool_raises(tmp_path: Path):
    cfg = TripConfig(name="test_empty", time_limit_seconds=5)
    with patch("src.trip.fetch_pois", side_effect=EmptyCandidatePool("no rows")):
        try:
            run_trip(cfg, output_dir=tmp_path)
        except EmptyCandidatePool:
            pass  # expected
        else:
            assert False, "should have raised EmptyCandidatePool"


def test_run_trip_cross_border_builds_baseline_and_applies_penalty(tmp_path: Path):
    """When routing_network='us_canada' and border_crossing_minutes>0, the
    pipeline must build the US-only matrix as a detection baseline AND apply
    the border penalty before solving. Pin both calls."""
    cfg = TripConfig(
        name="test_xborder",
        routing_network="us_canada",
        border_crossing_minutes=20,
        time_limit_seconds=5,
    )
    pois = _fake_pois()
    dur, dist = _fake_matrices(pois)

    with patch("src.trip.fetch_pois", return_value=pois), \
         patch("src.trip.build_matrix", side_effect=lambda pois, osrm_url=None:
               (dur.copy(), dist.copy())) as mock_matrix, \
         patch("src.trip.apply_border_penalty",
               side_effect=lambda us, na, dist, mins, **kw: (na, dist, 0)) as mock_penalty:
        run_trip(cfg, output_dir=tmp_path)

    # build_matrix called twice (NA primary + US baseline for detection)
    assert mock_matrix.call_count == 2
    # Penalty helper called exactly once with the right border-minutes value
    assert mock_penalty.call_count == 1
    assert mock_penalty.call_args.args[3] == 20


def test_run_trip_cross_border_skips_penalty_when_zero(tmp_path: Path):
    """border_crossing_minutes=0 should suppress baseline build AND penalty
    application (useful for NEXUS-equipped travelers or diagnostic runs)."""
    cfg = TripConfig(
        name="test_no_penalty",
        routing_network="us_canada",
        border_crossing_minutes=0,
        time_limit_seconds=5,
    )
    pois = _fake_pois()
    dur, dist = _fake_matrices(pois)

    with patch("src.trip.fetch_pois", return_value=pois), \
         patch("src.trip.build_matrix", side_effect=lambda pois, osrm_url=None:
               (dur.copy(), dist.copy())) as mock_matrix, \
         patch("src.trip.apply_border_penalty") as mock_penalty:
        run_trip(cfg, output_dir=tmp_path)

    # Single matrix build (NA only — no baseline needed because no penalty)
    assert mock_matrix.call_count == 1
    # Penalty helper never invoked
    assert mock_penalty.call_count == 0


def test_run_trip_handles_nonsequential_poi_ids(tmp_path: Path):
    # Real POIs have DB ids like 42, 107 — not 0..n-1. Verify run_trip
    # correctly handles this rather than crashing in the summary stats.
    def offset_pois():
        # Same shape as _fake_pois but with ids offset by 100
        return [
            {"id": 100 + i, "name": f"P{i}", "state": f"S{i % 3}",
             "category": "National Park", "lat": float(i), "lon": float(i)}
            for i in range(6)
        ]

    cfg = TripConfig(name="test_ids", time_limit_seconds=5)
    with patch("src.trip.fetch_pois", return_value=offset_pois()), \
         patch("src.trip.build_matrix", side_effect=lambda pois, osrm_url=None: _fake_matrices(pois)):
        out = run_trip(cfg, output_dir=tmp_path)
    assert out.exists(), "should have written an HTML file"
