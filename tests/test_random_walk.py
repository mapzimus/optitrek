"""Tests for src/random_walk.py — the anti-optimizer.

No network, no OSRM. Pins:
  1. Gazetteer parsing survives the file's trailing-whitespace quirk and
     drops the road-unreachable states.
  2. The permutation is a pure function of the seed.
  3. The anchor is the town nearest the CONUS center.
  4. /route and /table responses are parsed into the record shapes the
     walk + summary expect (Ok, NoRoute, null durations).
  5. Resume picks up after the last record and never re-attempts a leg.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.random_walk import (
    Town,
    check_reachability,
    parse_gazetteer,
    pick_anchor,
    read_legs,
    resume_state,
    route_leg,
    shuffle_towns,
    summarize,
    walk,
)

# Header + values carry trailing spaces on the last column, exactly like the
# real 2020_Gaz_place_national.txt.
GAZ_FIXTURE = (
    "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG          \n"
    "AL\t0100100\t02582661\tAbanda CDP\t57\tS\t7764034\t34284\t2.998\t0.013\t33.091627\t-85.527029          \n"
    "AK\t0200100\t02419025\tAdak city\t25\tA\t1\t1\t0.1\t0.1\t51.8\t-176.6          \n"
    "HI\t1500100\t02628163\tAhuimanu CDP\t57\tS\t1\t1\t0.1\t0.1\t21.4\t-157.8          \n"
    "KS\t2039000\t00485613\tLebanon city\t25\tA\t1\t1\t0.1\t0.1\t39.810\t-98.556          \n"
    "PR\t7200100\t02417002\tAdjuntas\t62\tA\t1\t1\t0.1\t0.1\t18.2\t-66.7          \n"
    "DC\t1150000\t01702382\tWashington city\t25\tA\t1\t1\t0.1\t0.1\t38.904\t-77.017          \n"
)


def _towns(n: int) -> list[Town]:
    return [Town(f"{i:07d}", f"Town {i}", "KS", 39.0 + i * 0.01, -98.0 - i * 0.01) for i in range(n)]


# ---------- 1. parsing ----------

def test_parse_gazetteer_strips_whitespace_and_drops_excluded_states():
    towns = parse_gazetteer(GAZ_FIXTURE)
    assert [t.state for t in towns] == ["AL", "KS", "DC"]
    assert towns[0].name == "Abanda CDP"
    assert towns[0].lon == pytest.approx(-85.527029)   # trailing spaces stripped
    assert towns[0].geoid == "0100100"                  # leading zero kept


# ---------- 2. shuffle ----------

def test_shuffle_is_seed_deterministic_and_seed_sensitive():
    towns = _towns(50)
    a = shuffle_towns(towns, 42)
    b = shuffle_towns(towns, 42)
    c = shuffle_towns(towns, 43)
    assert a == b
    assert a != c
    assert sorted(a, key=lambda t: t.geoid) == towns   # permutation, not a sample
    assert towns == _towns(50)                          # input not mutated


# ---------- 3. anchor ----------

def test_pick_anchor_is_nearest_conus_center():
    towns = parse_gazetteer(GAZ_FIXTURE)
    assert towns[pick_anchor(towns)].name == "Lebanon city"


# ---------- 4. OSRM response parsing ----------

def _resp(payload: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


def test_route_leg_parses_ok_response():
    a, b = _towns(2)
    payload = {
        "code": "Ok",
        "routes": [{"distance": 160934.4, "duration": 7200.0, "geometry": "abc"}],
        "waypoints": [{"distance": 12.5}, {"distance": 250.0}],
    }
    with patch("src.random_walk.requests.get", return_value=_resp(payload)) as get:
        rec = route_leg(a, b, osrm_url="http://osrm.test")
    assert rec == {
        "status": "ok", "distance_m": 160934.4, "duration_s": 7200.0,
        "from_snap_m": 12.5, "to_snap_m": 250.0, "geometry": "abc",
    }
    url = get.call_args.args[0]
    assert url.startswith("http://osrm.test/route/v1/driving/-98.000000,39.000000;-98.010000,39.010000")
    assert "overview=simplified" in url


def test_route_leg_returns_error_code_on_noroute():
    a, b = _towns(2)
    with patch("src.random_walk.requests.get", return_value=_resp({"code": "NoRoute", "message": "x"})):
        assert route_leg(a, b, osrm_url="http://osrm.test") == {"status": "NoRoute"}


def test_check_reachability_reads_null_durations_across_blocks():
    towns = _towns(5)
    # block=2 → blocks [0,1] [2,3] [4]; town 3 is an island (null duration).
    payloads = [
        {"code": "Ok", "durations": [[10.0], [20.0]]},
        {"code": "Ok", "durations": [[30.0], [None]]},
        {"code": "Ok", "durations": [[50.0]]},
    ]
    with patch("src.random_walk.requests.get", side_effect=[_resp(p) for p in payloads]) as get:
        reachable = check_reachability(towns, anchor_idx=0, osrm_url="http://osrm.test", block=2, progress=False)
    assert reachable == [True, True, True, False, True]
    first_url = get.call_args_list[0].args[0]
    assert "sources=0;1" in first_url and "destinations=2" in first_url


# ---------- 5. walk + resume ----------

def _fake_route(a: Town, b: Town, osrm_url=None, overview="simplified") -> dict:
    return {"status": "ok", "distance_m": 1000.0, "duration_s": 60.0,
            "from_snap_m": 0.0, "to_snap_m": 0.0, "geometry": None}


def test_walk_skips_unreachable_and_keeps_origin(tmp_path):
    towns = _towns(5)
    reachable = [True, True, False, True, True]
    legs = walk(towns, reachable, tmp_path / "legs.jsonl", route_fn=_fake_route, progress_every=0)
    assert [(r["from_seq"], r["to_seq"], r["status"]) for r in legs] == [
        (0, 1, "ok"), (1, 2, "unreachable"), (1, 3, "ok"), (3, 4, "ok"),
    ]


def test_walk_resumes_without_repeating_legs(tmp_path):
    towns = _towns(6)
    reachable = [True] * 6
    path = tmp_path / "legs.jsonl"
    walk(towns, reachable, path, route_fn=_fake_route, limit=3, progress_every=0)
    assert [r["to_seq"] for r in read_legs(path)] == [1, 2]

    calls: list[tuple[int, int]] = []

    def counting_route(a, b, osrm_url=None, overview="simplified"):
        calls.append((int(a.geoid), int(b.geoid)))
        return _fake_route(a, b)

    legs = walk(towns, reachable, path, route_fn=counting_route, progress_every=0)
    assert calls == [(2, 3), (3, 4), (4, 5)]
    assert [r["to_seq"] for r in legs] == [1, 2, 3, 4, 5]


def test_resume_state_origin_is_last_successful_destination():
    legs = [
        {"from_seq": 0, "to_seq": 1, "status": "ok"},
        {"from_seq": 1, "to_seq": 2, "status": "NoRoute"},
        {"from_seq": 1, "to_seq": 3, "status": "unreachable"},
    ]
    assert resume_state(legs) == (4, 1)
    assert resume_state([]) == (0, None)


def test_walk_aborts_after_consecutive_failures(tmp_path):
    towns = _towns(8)
    reachable = [True] * 8

    def always_fail(a, b, osrm_url=None, overview="simplified"):
        return {"status": "NoRoute"}

    with pytest.raises(RuntimeError, match="consecutive route failures"):
        walk(towns, reachable, tmp_path / "legs.jsonl", route_fn=always_fail, progress_every=0)


# ---------- 6. summary ----------

def test_summarize_totals_and_ratios():
    legs = [
        {"from_seq": 0, "to_seq": 1, "from_name": "A, KS", "to_name": "B, KS",
         "status": "ok", "distance_m": 1609.344 * 100, "duration_s": 7200.0,
         "from_snap_m": 5.0, "to_snap_m": 15.0},
        {"from_seq": 1, "to_seq": 2, "from_name": "B, KS", "to_name": "C, KS",
         "status": "unreachable"},
        {"from_seq": 1, "to_seq": 3, "from_name": "B, KS", "to_name": "D, KS",
         "status": "ok", "distance_m": 1609.344 * 300, "duration_s": 3600.0 * 5,
         "from_snap_m": 0.0, "to_snap_m": 40.0},
    ]
    s = summarize(legs, n_towns=4, n_reachable=3, seed=7)
    assert s["n_legs_ok"] == 2 and s["n_skipped_unreachable"] == 1 and s["n_failed_route"] == 0
    assert s["total_miles"] == pytest.approx(400.0)
    assert s["total_hours"] == pytest.approx(7.0)
    assert s["leg_miles_max"] == pytest.approx(300.0)
    assert s["longest_leg"] == "B, KS → D, KS"
    assert s["snap_m_max"] == 40.0
    assert s["vs_tier1_optimal_miles"] == pytest.approx(400.0 / 9744.0, abs=0.05)
