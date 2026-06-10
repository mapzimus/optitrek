"""Unit tests for the three scoring layers and the composite — all
synthetic, no DB / OSRM / network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.scoring.composite import ScoredLayers, composite_scores, effective_weights, normalize
from src.scoring.construction import ConstructionSubscore, TrafficEvent, classify_event, construction_subscore
from src.scoring.crashes import CrashRecord, crash_subscore, recency_weight, severity_weight
from src.scoring.winter import HourlyConditions, bridge_multiplier, hourly_risk, point_risk, winter_subscore

# ---------------------------------------------------------------------------
# Layer 1 — crashes


def test_severity_weights():
    assert severity_weight("fatal") == 5.0
    assert severity_weight("injury") == 2.0
    assert severity_weight("pdo") == 1.0
    assert severity_weight("unknown") == 1.0   # falls back to PDO weight


def test_recency_weight_linear_decay():
    assert recency_weight(2025, 2025) == 1.0
    assert recency_weight(2021, 2025) == 0.4
    assert abs(recency_weight(2023, 2025) - 0.7) < 1e-9
    # Older than the window keeps the floor, never goes negative.
    assert recency_weight(2010, 2025) == 0.4


def test_crash_subscore_math():
    crashes = [
        CrashRecord("fatal", 2025),    # 5.0 * 1.0
        CrashRecord("injury", 2023),   # 2.0 * 0.7
        CrashRecord("pdo", 2021),      # 1.0 * 0.4
    ]
    sub = crash_subscore(crashes, route_length_km=2.0)
    assert abs(sub.raw - (5.0 + 1.4 + 0.4) / 2.0) < 1e-9
    assert sub.total == 3 and sub.fatal == 1 and sub.injury == 1
    assert "3 crashes" in sub.explanation
    assert "1 fatal" in sub.explanation


def test_crash_subscore_normalizes_by_length():
    crashes = [CrashRecord("pdo", 2025)] * 10
    short = crash_subscore(crashes, route_length_km=1.0)
    long = crash_subscore(crashes, route_length_km=10.0)
    assert short.raw > long.raw


def test_crash_subscore_empty():
    sub = crash_subscore([], route_length_km=5.0)
    assert sub.raw == 0.0
    assert "No recorded crashes" in sub.explanation


# ---------------------------------------------------------------------------
# Layer 2 — winter


def _hour(temp_f, forecast="Sunny", prob=0.0, hour=12):
    return HourlyConditions(temp_f=temp_f, precip_prob=prob, short_forecast=forecast, hour=hour)


def test_warm_dry_is_zero_risk():
    assert hourly_risk(_hour(55.0)) == 0.0
    assert hourly_risk(_hour(40.0, "Rain", prob=90)) == 0.0


def test_cold_dry_has_base_risk():
    r = hourly_risk(_hour(30.0))
    assert 0.0 < r < 0.5


def test_snow_raises_risk():
    cold_dry = hourly_risk(_hour(30.0))
    snowing = hourly_risk(_hour(30.0, "Light Snow", prob=80))
    assert snowing > cold_dry


def test_freezing_rain_raises_risk():
    cold_dry = hourly_risk(_hour(31.0))
    frz_rain = hourly_risk(_hour(31.0, "Rain", prob=90))
    assert frz_rain > cold_dry


def test_overnight_refreeze_adds_risk():
    day = hourly_risk(_hour(30.0, hour=13))
    night = hourly_risk(_hour(30.0, hour=2))
    assert night > day


def test_point_risk_takes_worst_hour():
    hours = [_hour(40.0), _hour(28.0, "Snow", prob=90), _hour(40.0)]
    assert point_risk(hours) == hourly_risk(hours[1])


def test_bridge_multiplier():
    assert bridge_multiplier(0.0, 10.0) == 1.0
    assert bridge_multiplier(1.0, 10.0) == config.BRIDGE_MULTIPLIER_MAX
    assert 1.0 < bridge_multiplier(0.1, 10.0) < config.BRIDGE_MULTIPLIER_MAX


def test_winter_inactive_when_calm():
    sub = winter_subscore([0.0, 0.01], bridge_km=2.0, route_km=10.0)
    assert not sub.active
    assert sub.raw == 0.0


def test_winter_active_with_risk_and_bridges():
    sub = winter_subscore([0.5, 0.7], bridge_km=2.0, route_km=10.0)
    assert sub.active
    assert sub.raw > 0.6        # mean 0.6 x bridge multiplier
    assert "bridges freeze first" in sub.explanation


# ---------------------------------------------------------------------------
# Layer 3 — construction


def _event(title, tooltip="", icon=""):
    return TrafficEvent(event_id="e1", title=title, tooltip=tooltip, lon=-71.0, lat=42.3, icon_url=icon)


def test_classify_events():
    assert classify_event("Route 1: Road closed.", "", "") == "full_closure"
    assert classify_event("Bay St: Roadwork.", "Some lanes closed.", "") == "lane_closure"
    assert classify_event("I-93: Crash.", "Right shoulder.", "") == "incident"
    assert classify_event("Rt 9: Roadwork.", "Expect delays.", "") == "roadwork"
    assert classify_event("Special event", "", "") == "other"
    assert classify_event("Rt 2", "", "/images/road-report-closure-critical.svg") == "full_closure"


def test_full_closure_disqualifies():
    sub = construction_subscore([_event("Main St: Road closed.")])
    assert sub.disqualified
    assert "closure" in sub.explanation.lower()


def test_lane_closure_penalizes_without_disqualifying():
    sub = construction_subscore(
        [_event("Rt 1: Roadwork.", "Some lanes closed. For the next month.")]
    )
    assert not sub.disqualified
    assert sub.raw == config.EVENT_WEIGHTS["lane_closure"]
    assert "lane closure" in sub.explanation


def test_no_events():
    sub = construction_subscore([])
    assert sub.raw == 0.0 and not sub.disqualified


# ---------------------------------------------------------------------------
# Composite


def test_normalize_max_scales():
    assert normalize([2.0, 1.0, 0.0]) == [100.0, 50.0, 0.0]
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


def test_weight_redistribution_when_winter_inactive():
    w = effective_weights(winter_active=False)
    assert w["ice"] == 0.0
    assert abs(w["crash"] - (config.WEIGHTS["crash"] + config.WEIGHTS["ice"])) < 1e-9
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert abs(sum(effective_weights(True).values()) - 1.0) < 1e-9


def test_composite_ranks_safer_route_higher():
    layers = [
        ScoredLayers(crash_raw=10.0, ice_raw=0.0, construction_raw=3.0),   # risky
        ScoredLayers(crash_raw=2.0, ice_raw=0.0, construction_raw=0.0),    # calm
    ]
    scores = composite_scores(layers, winter_active=False)
    assert scores[1]["safety_score"] > scores[0]["safety_score"]
    assert scores[0]["subscores"]["crash"] == 100.0
    assert scores[0]["subscores"]["ice"] == 0.0    # inactive layer contributes nothing


def test_composite_winter_changes_ranking():
    # Route A: fewer crashes but icy; Route B: more crashes, no ice.
    layers = [
        ScoredLayers(crash_raw=2.0, ice_raw=0.9, construction_raw=0.0),
        ScoredLayers(crash_raw=3.0, ice_raw=0.05, construction_raw=0.0),
    ]
    summer = composite_scores(layers, winter_active=False)
    winter = composite_scores(layers, winter_active=True)
    assert summer[0]["safety_score"] > summer[1]["safety_score"]
    assert winter[0]["safety_score"] < winter[1]["safety_score"]
