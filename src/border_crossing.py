"""Border-crossing time penalty for cross-border routing.

OSRM models the road network — it knows nothing about US-Canada customs.
At a major crossing (Ambassador Bridge, Peace Bridge, Sault Ste Marie, etc.)
a passenger vehicle waits 15-30 min under normal traffic, 30-60 under heavy,
60-120+ during holiday peaks. A trip leg that enters Canada and re-enters
the US (e.g., Detroit → Ontario → Buffalo) crosses the border **twice**, so
the wall-clock overhead is double the per-crossing wait.

This module makes that overhead visible to the solver. Without it, the
solver sees only OSRM's road-time and happily chooses Ontario shortcuts
that lose time net of customs. With it, the solver picks Canada only when
the routing savings genuinely exceed border overhead.

Detection strategy: matrix differencing. For each leg, if the US+Canada
duration is meaningfully less than the US-only duration, OSRM took a
cross-border path. No GIS work or border-shape geometry needed — the cost
delta is the signal.
"""
from __future__ import annotations

import numpy as np

# Minimum duration improvement (seconds) below which we assume the difference
# is network-modeling noise rather than a genuine Canadian shortcut. Three
# common sources of noise at ~10 sec scale: (a) minor lane-routing
# differences within border cities where OSRM resolves a slightly different
# entrance ramp on the merged PBF; (b) ferry/border-crossing nodes that
# osmium-merge resolves differently; (c) floating-point round-off in the
# OSRM /table response. 60 s is comfortably above all three.
NOISE_THRESHOLD_SECONDS = 60.0


def apply_border_penalty(
    us_durations: np.ndarray,
    na_durations: np.ndarray,
    na_distances: np.ndarray,
    border_crossing_minutes: int,
    crossings_per_leg: int = 2,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Add wall-clock border-crossing penalty to NA matrix entries where the
    leg crosses the US-Canada border.

    Args:
        us_durations: N×N float32 (seconds) — durations from the US-only OSRM
            engine. Used solely as the detection baseline.
        na_durations: N×N float32 (seconds) — durations from the US+Canada
            engine. Returned with penalty added on cross-border legs.
        na_distances: N×N float32 (meters) — distances from the US+Canada
            engine. Returned unmodified (the penalty is time, not miles —
            this preserves the report's "X mi saved by cross-border" number,
            which is a pure-road-distance claim).
        border_crossing_minutes: Wall-clock minutes per single crossing. Must
            be >= 0. Set to 0 to disable the penalty (returns na_durations
            unchanged, useful for diagnostic runs or NEXUS-equipped travelers).
        crossings_per_leg: How many crossings a single matrix leg incurs.
            Defaults to 2 because our POI set is US-only, so any cross-border
            leg enters Canada and re-enters the US within that one leg. If
            Tier 3 ever adds Canadian POIs, the depot-to-Canada leg becomes
            a 1-crossing leg — drop this to 1 for those entries.

    Returns:
        (adjusted_na_durations, na_distances_unchanged, n_legs_penalized).
        Caller can use n_legs_penalized for telemetry.

    The penalty is applied as:
        crosses_border = na_durations < (us_durations - NOISE_THRESHOLD_SECONDS)
        adjusted = na_durations + crosses_border * crossings_per_leg
                                                  * border_crossing_minutes * 60
    """
    if border_crossing_minutes < 0:
        raise ValueError(
            f"border_crossing_minutes must be >= 0, got {border_crossing_minutes}"
        )
    if us_durations.shape != na_durations.shape:
        raise ValueError(
            f"shape mismatch: us_durations {us_durations.shape} vs "
            f"na_durations {na_durations.shape}"
        )

    if border_crossing_minutes == 0:
        return na_durations.copy(), na_distances, 0

    # NaN-safe comparison: treat NaN cells as "not a cross-border route". We
    # don't want to add a penalty to an unreachable leg, since the cell is
    # going to be a huge negative int64 after the solver's cast either way.
    us_finite = np.where(np.isnan(us_durations), np.inf, us_durations)
    na_finite = np.where(np.isnan(na_durations), np.inf, na_durations)
    crosses_border = na_finite < (us_finite - NOISE_THRESHOLD_SECONDS)
    np.fill_diagonal(crosses_border, False)  # i==j is never a border crossing

    penalty_seconds = crossings_per_leg * border_crossing_minutes * 60
    adjusted = na_durations.copy()
    adjusted[crosses_border] += penalty_seconds

    n_legs_penalized = int(crosses_border.sum())
    return adjusted, na_distances, n_legs_penalized


def summarize_border_impact(
    us_durations: np.ndarray,
    na_durations: np.ndarray,
    border_crossing_minutes: int,
    crossings_per_leg: int = 2,
) -> dict:
    """Diagnostic summary of where the penalty bites the matrix. Useful for
    printing on trip startup so the user can sanity-check the assumption.

    Returns a dict with:
        n_cross_border_legs:        how many (i,j) pairs OSRM would route through Canada
        avg_raw_savings_minutes:    mean of (us_dur - na_dur) on those legs, in min
        avg_net_savings_minutes:    same but minus the border penalty
        n_flipped_by_penalty:       legs that were cheaper via Canada raw but
                                    become more expensive once penalty applied
    """
    us_finite = np.where(np.isnan(us_durations), np.inf, us_durations)
    na_finite = np.where(np.isnan(na_durations), np.inf, na_durations)
    raw_delta = us_finite - na_finite  # positive where Canada is faster
    crosses_border = raw_delta > NOISE_THRESHOLD_SECONDS
    np.fill_diagonal(crosses_border, False)

    penalty_seconds = crossings_per_leg * border_crossing_minutes * 60
    net_delta = raw_delta - penalty_seconds  # what the solver sees

    if not crosses_border.any():
        return {
            "n_cross_border_legs": 0,
            "avg_raw_savings_minutes": 0.0,
            "avg_net_savings_minutes": 0.0,
            "n_flipped_by_penalty": 0,
        }

    raw_on_border = raw_delta[crosses_border]
    net_on_border = net_delta[crosses_border]
    flipped = (raw_on_border > 0) & (net_on_border <= 0)
    return {
        "n_cross_border_legs": int(crosses_border.sum()),
        "avg_raw_savings_minutes": float(raw_on_border.mean()) / 60.0,
        "avg_net_savings_minutes": float(net_on_border.mean()) / 60.0,
        "n_flipped_by_penalty": int(flipped.sum()),
    }
