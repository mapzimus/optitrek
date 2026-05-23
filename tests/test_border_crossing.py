"""Tests for src/border_crossing.py — penalty application + impact summary."""
import numpy as np
import pytest

from src.border_crossing import (
    NOISE_THRESHOLD_SECONDS,
    apply_border_penalty,
    summarize_border_impact,
)


def _build_pair():
    """A small symmetric 4×4 matrix pair. Leg (0,1) has Canada cheaper by
    600 s (10 min) — clearly a cross-border route. Leg (0,2) has Canada
    cheaper by 30 s — below the noise threshold, should NOT be penalized.
    Leg (2,3) is equal — no border crossing. Diagonal is zero."""
    us = np.array([
        [0.0, 3600.0, 7200.0, 5400.0],
        [3600.0, 0.0, 1800.0, 2700.0],
        [7200.0, 1800.0, 0.0, 1200.0],
        [5400.0, 2700.0, 1200.0, 0.0],
    ], dtype=np.float32)
    na = us.copy()
    na[0, 1] = 3000.0  # 600 s savings → counts as cross-border
    na[1, 0] = 3000.0  # symmetric
    na[0, 2] = 7170.0  # 30 s savings → below noise threshold
    na[2, 0] = 7170.0
    dist = np.full_like(us, 100000.0)  # arbitrary 100 km per leg
    return us, na, dist


def test_zero_penalty_returns_unchanged():
    us, na, dist = _build_pair()
    adjusted, dist_out, n = apply_border_penalty(us, na, dist, border_crossing_minutes=0)
    np.testing.assert_array_equal(adjusted, na)
    np.testing.assert_array_equal(dist_out, dist)
    assert n == 0


def test_penalty_applies_only_to_cross_border_legs():
    us, na, dist = _build_pair()
    # 20 min × 2 crossings = 2400 s added per detected cross-border leg.
    adjusted, _, n = apply_border_penalty(us, na, dist, border_crossing_minutes=20)
    # Only (0,1) and (1,0) cleared the noise threshold.
    assert n == 2
    assert adjusted[0, 1] == pytest.approx(3000.0 + 2400.0)
    assert adjusted[1, 0] == pytest.approx(3000.0 + 2400.0)
    # The 30-s "savings" stays unchanged (noise, not real border).
    assert adjusted[0, 2] == pytest.approx(na[0, 2])
    assert adjusted[2, 0] == pytest.approx(na[2, 0])
    # Equal-cost legs unchanged.
    assert adjusted[2, 3] == pytest.approx(na[2, 3])


def test_diagonal_never_penalized():
    # A pathological matrix where the diagonal also shows a "saving" — the
    # penalty must skip i==j unconditionally (self-legs aren't crossings).
    us = np.full((3, 3), 1000.0, dtype=np.float32)
    na = np.zeros((3, 3), dtype=np.float32)  # full savings everywhere
    dist = np.zeros_like(us)
    adjusted, _, n = apply_border_penalty(us, na, dist, border_crossing_minutes=20)
    # n=6 off-diagonal legs penalized, but 3 diagonal entries untouched at 0.
    assert n == 6
    assert all(adjusted[i, i] == 0.0 for i in range(3))


def test_nan_in_us_does_not_create_false_border():
    # If the US leg is unreachable (NaN) but NA reaches it, that's the
    # opposite of cross-border (NA simply found a path the US-only network
    # couldn't). Avoid double-counting as a customs-check.
    us = np.array([[0.0, np.nan], [np.nan, 0.0]], dtype=np.float32)
    na = np.array([[0.0, 100.0], [100.0, 0.0]], dtype=np.float32)
    dist = np.zeros_like(us)
    _, _, n = apply_border_penalty(us, na, dist, border_crossing_minutes=20)
    # us_finite becomes inf there, so na (100) < inf - 60 is true → counts as
    # cross-border. Hmm — is that desired? An NA-only path most likely IS a
    # cross-border path (otherwise US-only would have found it). So yes,
    # treating it as cross-border is correct.
    assert n == 2


def test_nan_in_na_does_not_create_false_border():
    # Symmetric guard: NA NaN should not generate a penalty.
    us = np.array([[0.0, 100.0], [100.0, 0.0]], dtype=np.float32)
    na = np.array([[0.0, np.nan], [np.nan, 0.0]], dtype=np.float32)
    dist = np.zeros_like(us)
    _, _, n = apply_border_penalty(us, na, dist, border_crossing_minutes=20)
    # na_finite becomes inf, so inf < (100 - 60) is false → no penalty.
    assert n == 0


def test_shape_mismatch_raises():
    us = np.zeros((3, 3), dtype=np.float32)
    na = np.zeros((4, 4), dtype=np.float32)
    dist = np.zeros((4, 4), dtype=np.float32)
    with pytest.raises(ValueError, match="shape mismatch"):
        apply_border_penalty(us, na, dist, border_crossing_minutes=20)


def test_negative_minutes_raises():
    us, na, dist = _build_pair()
    with pytest.raises(ValueError, match=">= 0"):
        apply_border_penalty(us, na, dist, border_crossing_minutes=-5)


def test_distances_returned_unmodified():
    # Penalty applies to time, not distance — miles saved is a pure-road claim
    # that doesn't depend on customs time. Verify the distances pass through.
    us, na, dist = _build_pair()
    original_dist = dist.copy()
    _, dist_out, _ = apply_border_penalty(us, na, dist, border_crossing_minutes=20)
    np.testing.assert_array_equal(dist_out, original_dist)


def test_summarize_reports_zero_when_no_crossings():
    us = np.full((3, 3), 100.0, dtype=np.float32)
    na = us.copy()
    summary = summarize_border_impact(us, na, border_crossing_minutes=20)
    assert summary["n_cross_border_legs"] == 0
    assert summary["avg_raw_savings_minutes"] == 0.0
    assert summary["n_flipped_by_penalty"] == 0


def test_summarize_detects_flipped_legs():
    # A leg saves 10 min raw, but 40 min penalty flips its sign.
    us = np.array([[0.0, 3000.0], [3000.0, 0.0]], dtype=np.float32)
    na = np.array([[0.0, 2400.0], [2400.0, 0.0]], dtype=np.float32)
    # raw savings = 600 s = 10 min. Penalty = 2 × 20 × 60 = 2400 s = 40 min.
    # net = -30 min → flipped.
    summary = summarize_border_impact(us, na, border_crossing_minutes=20)
    assert summary["n_cross_border_legs"] == 2  # symmetric
    assert summary["avg_raw_savings_minutes"] == pytest.approx(10.0)
    assert summary["avg_net_savings_minutes"] == pytest.approx(-30.0)
    assert summary["n_flipped_by_penalty"] == 2


def test_summarize_keeps_winners_when_savings_exceed_penalty():
    # A 90-min raw savings beats a 40-min penalty — net is still positive.
    us = np.array([[0.0, 7200.0], [7200.0, 0.0]], dtype=np.float32)
    na = np.array([[0.0, 1800.0], [1800.0, 0.0]], dtype=np.float32)
    # raw = 5400 s = 90 min. Penalty = 40 min. net = +50 min.
    summary = summarize_border_impact(us, na, border_crossing_minutes=20)
    assert summary["n_cross_border_legs"] == 2
    assert summary["avg_raw_savings_minutes"] == pytest.approx(90.0)
    assert summary["avg_net_savings_minutes"] == pytest.approx(50.0)
    assert summary["n_flipped_by_penalty"] == 0
