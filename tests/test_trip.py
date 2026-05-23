"""End-to-end orchestrator tests with mocked fetch_pois + matrix build."""
from pathlib import Path
from unittest.mock import patch
import numpy as np

from src.config import TripConfig, EmptyCandidatePool
from src.trip import run_trip


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
