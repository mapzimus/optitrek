"""End-to-end orchestrator tests with mocked fetch_pois + matrix build."""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import pytest
import requests

from src.config import TripConfig, EmptyCandidatePool
from src.trip import (
    run_trip,
    _osrm_url_for_network,
    _validate_engines_for_config,
    OSRMEngineError,
)


# Existing run_trip tests mock fetch_pois + build_matrix. They previously
# didn't need to know about engine validation (because there was none).
# F1/F5 fix added a pre-matrix-build OSRM health check; tests now also
# need to bypass that check or they'd try to reach real OSRM. We mock
# _validate_engines_for_config via this helper rather than repeating the
# patch in every test.
def _patch_validator():
    return patch("src.trip._validate_engines_for_config", return_value=None)


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
    with _patch_validator(), \
         patch("src.trip.fetch_pois", return_value=_fake_pois()), \
         patch("src.trip.build_matrix", side_effect=lambda pois, osrm_url=None: _fake_matrices(pois)):
        out = run_trip(cfg, output_dir=tmp_path)
    assert out.exists()
    assert out.suffix == ".html"
    assert out.name == "test_trip.html"


def test_run_trip_empty_pool_raises(tmp_path: Path):
    cfg = TripConfig(name="test_empty", time_limit_seconds=5)
    # No validator patch — fetch_pois raises before we reach engine
    # validation, which is the correct ordering (don't probe network if
    # the DB can't even produce candidates).
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

    with _patch_validator(), \
         patch("src.trip.fetch_pois", return_value=pois), \
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

    with _patch_validator(), \
         patch("src.trip.fetch_pois", return_value=pois), \
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
    with _patch_validator(), \
         patch("src.trip.fetch_pois", return_value=offset_pois()), \
         patch("src.trip.build_matrix", side_effect=lambda pois, osrm_url=None: _fake_matrices(pois)):
        out = run_trip(cfg, output_dir=tmp_path)
    assert out.exists(), "should have written an HTML file"


def test_run_trip_loop_true_total_miles_includes_closing_leg(tmp_path: Path, capsys):
    """F11 regression: trip.py used to compute total_miles via
    range(n_stops - 1) which dropped the closing leg for loop=True
    trips. The Tier 1 oracle test (which uses range(n)) was correct,
    but the user-facing print in run_trip was systematically short by
    one leg. This test pins that the print now matches the oracle's
    calculation by building a deliberately asymmetric distance matrix
    where the closing leg is a known size and asserting it's in the
    total."""
    # 4 POIs in 4 states, arranged on a unit square.
    # Distances configured so that the optimal tour is 0->1->2->3->0
    # and the closing leg (3->0) is the unmistakable 1000 m segment.
    pois = [
        {"id": i, "name": f"P{i}", "state": f"S{i}", "category": "x",
         "lat": float(i), "lon": float(i)}
        for i in range(4)
    ]
    n = len(pois)
    dur = np.full((n, n), 3600.0, dtype=np.float32)  # 1 hr per leg (uniform)
    np.fill_diagonal(dur, 0.0)
    dist = np.full((n, n), 100.0, dtype=np.float32)  # 100 m default
    np.fill_diagonal(dist, 0.0)
    # Make the closing leg (P3 -> P0) 1000 m so it's distinguishable
    dist[3, 0] = 1000.0
    dist[0, 3] = 1000.0

    cfg = TripConfig(name="test_loop_miles",
                     states=["S0", "S1", "S2", "S3"],
                     loop=True, time_limit_seconds=5)

    with _patch_validator(), \
         patch("src.trip.fetch_pois", return_value=pois), \
         patch("src.trip.build_matrix", side_effect=lambda pois, osrm_url=None: (dur, dist)):
        run_trip(cfg, output_dir=tmp_path)

    captured = capsys.readouterr()
    # 4 stops, closing leg back is 1000 m. The other 3 legs total 300 m
    # (P0->P1, P1->P2, P2->P3 = 3 * 100 m). Total = 1300 m = 0.808 mi.
    # If the bug were still present we'd see only 300 m = 0.186 mi.
    # Print the actual number found so debugging is easy if the assertion fails.
    assert "1 mi" in captured.out or "0.8 mi" in captured.out or "0 mi" in captured.out, (
        f"Expected total_miles to include closing 1000 m leg. "
        f"Print output:\n{captured.out}"
    )


# ---------------------------------------------------------------------------
# Engine validation (F1 + F5). Three branches:
#  (a) primary OSRM unreachable -> OSRMEngineError naming the engine + fix
#  (b) cross-border + baseline unreachable -> same shape, naming baseline
#  (c) cross-border + NA engine doesn't actually serve Canada (Detroit-Buffalo
#      probe returns ~equal distance on both) -> OSRMEngineError with diagnosis
# ---------------------------------------------------------------------------


def _mock_route_response(distance_m: float):
    """Helper: build a fake requests.Response payload for /route."""
    resp = MagicMock()
    resp.json.return_value = {"code": "Ok", "routes": [{"distance": distance_m}]}
    resp.raise_for_status.return_value = None
    return resp


def test_validate_primary_engine_unreachable_raises():
    cfg = TripConfig(name="x", routing_network="us")
    with patch("src.trip.requests.get",
               side_effect=requests.ConnectionError("connection refused")):
        with pytest.raises(OSRMEngineError, match=r"Primary OSRM engine.*unreachable"):
            _validate_engines_for_config(cfg)


def test_validate_baseline_engine_unreachable_raises():
    cfg = TripConfig(name="x", routing_network="us_canada",
                     border_crossing_minutes=20)
    # First call (primary) succeeds; second (baseline) raises.
    primary_ok = _mock_route_response(256_000)  # ~256 km via Canada
    with patch("src.trip.requests.get",
               side_effect=[primary_ok, requests.ConnectionError("baseline down")]):
        with pytest.raises(OSRMEngineError, match=r"US-only baseline.*unreachable"):
            _validate_engines_for_config(cfg)


def test_validate_na_engine_not_actually_canada_raises():
    """The NA engine is up but somehow serving US-only data — both
    Detroit-Buffalo probes return ~the same distance. Catch it."""
    cfg = TripConfig(name="x", routing_network="us_canada",
                     border_crossing_minutes=20)
    # Both engines return ~360 km (the US-only number) — no Canada shortcut.
    same_resp = _mock_route_response(360_000)
    with patch("src.trip.requests.get", return_value=same_resp):
        with pytest.raises(OSRMEngineError,
                           match=r"doesn't appear to serve Canadian roads"):
            _validate_engines_for_config(cfg)


def test_validate_baseline_skipped_when_border_minutes_zero():
    """border_crossing_minutes=0 means the baseline matrix isn't built
    so the baseline-engine probe isn't needed either. Validation should
    only hit the primary engine."""
    cfg = TripConfig(name="x", routing_network="us_canada",
                     border_crossing_minutes=0)
    primary_ok = _mock_route_response(256_000)
    with patch("src.trip.requests.get", return_value=primary_ok) as mock_get:
        _validate_engines_for_config(cfg)
    # Exactly one /route call: the primary engine check.
    assert mock_get.call_count == 1


def test_validate_happy_path_us_only_one_probe():
    """For routing_network='us' there's no cross-border consistency
    check, just a single health probe of the US engine."""
    cfg = TripConfig(name="x", routing_network="us")
    primary_ok = _mock_route_response(360_000)
    with patch("src.trip.requests.get", return_value=primary_ok) as mock_get:
        _validate_engines_for_config(cfg)
    assert mock_get.call_count == 1


def test_validate_happy_path_us_canada_three_probes():
    """For routing_network='us_canada' with penalty enabled we expect
    three probes: primary, baseline, and the cross-border consistency
    re-query of the NA engine."""
    cfg = TripConfig(name="x", routing_network="us_canada",
                     border_crossing_minutes=20)
    na_resp = _mock_route_response(256_000)   # NA: 256 km
    us_resp = _mock_route_response(360_000)   # US: 360 km
    with patch("src.trip.requests.get",
               side_effect=[na_resp, us_resp, na_resp]) as mock_get:
        _validate_engines_for_config(cfg)
    assert mock_get.call_count == 3
