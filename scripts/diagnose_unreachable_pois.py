"""Diagnose POIs with high unreachable-pair counts in the cached matrix.

BUILD_STATUS follow-up — `validate_matrix()` says "79 POIs have >10%
unreachable pairs" but doesn't surface WHICH POIs. This script does.

Reads the cached parquet matrices, computes per-POI bad-pair stats, and
prints a categorised report. No DB or OSRM dependency — runs in seconds
against `data/matrix/`.

Usage (from repo root):
    python scripts/diagnose_unreachable_pois.py
    python scripts/diagnose_unreachable_pois.py --threshold 0.20  # >20%
    python scripts/diagnose_unreachable_pois.py --markdown > unreachable.md

A "bad" pair is either NaN (OSRM returned no route) or duration > 48 h
(matches `validate_matrix`'s default unreachable_threshold_hours).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq


REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX_DIR = REPO_ROOT / "data" / "matrix"
UNREACHABLE_HOURS = 48.0


def _load_matrix(name: str) -> np.ndarray:
    path = MATRIX_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m src.matrix_builder` first"
        )
    return np.stack([c.to_numpy() for c in pq.read_table(path).columns], axis=1)


def _categorise(pct: float) -> str:
    """Bucket per-POI unreachability into rough severity tiers."""
    if pct >= 50:
        return "isolated"     # unreachable from majority of network
    if pct >= 25:
        return "very-remote"  # significant gap
    if pct >= 10:
        return "remote"       # noticeable gap (the original 79)
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold", type=float, default=0.10,
        help="Per-POI bad-pair-pct threshold to surface (default 0.10 = 10%%)"
    )
    parser.add_argument(
        "--markdown", action="store_true",
        help="Emit markdown tables instead of plain-text for easy paste into docs"
    )
    args = parser.parse_args()

    pois = pq.read_table(MATRIX_DIR / "pois.parquet").to_pylist()
    duration = _load_matrix("duration")
    n = len(pois)
    assert duration.shape == (n, n), f"shape mismatch: {duration.shape} vs ({n}, {n})"

    threshold_sec = UNREACHABLE_HOURS * 3600
    bad_mask = np.isnan(duration) | (duration > threshold_sec)
    np.fill_diagonal(bad_mask, False)
    per_poi_bad = bad_mask.sum(axis=1)
    per_poi_pct = per_poi_bad / (n - 1)

    # For each row, compute the next-best reachable duration as context
    # (lets the reader judge whether the POI is "genuinely remote" vs
    # "should never be in the matrix").
    masked = np.where(bad_mask | np.eye(n, dtype=bool), np.inf, duration)
    nearest_h = masked.min(axis=1) / 3600
    nearest_h[np.isinf(nearest_h)] = float("nan")  # row was 100% bad

    rows = []
    for i, p in enumerate(pois):
        pct = per_poi_pct[i] * 100
        if per_poi_pct[i] < args.threshold:
            continue
        rows.append({
            "idx": i,
            "id": p["id"],
            "name": p["name"],
            "state": p["state"],
            "category": p.get("category", "?"),
            "bad_pct": pct,
            "nearest_h": float(nearest_h[i]),
            "bucket": _categorise(pct),
        })

    rows.sort(key=lambda r: (-r["bad_pct"], r["state"], r["name"]))

    # Headline
    print(f"# Unreachable-POI diagnostic\n" if args.markdown else "")
    print(f"Matrix: {n} POIs, threshold >={args.threshold*100:.0f}% bad-pair rate")
    print(f"Surfaced: {len(rows)} POIs ({len(rows)/n*100:.1f}% of catalog)")
    print()

    # Bucket breakdown
    buckets = Counter(r["bucket"] for r in rows)
    print("## Severity buckets" if args.markdown else "Severity buckets:")
    for tier in ("isolated", "very-remote", "remote"):
        c = buckets.get(tier, 0)
        if c:
            print(f"  - **{tier}**: {c}" if args.markdown else f"  {tier:12} {c:3}")
    print()

    # State breakdown
    state_count = Counter(r["state"] for r in rows)
    print("## By state (top 10)" if args.markdown else "Top 10 states by unreachable count:")
    for st, c in state_count.most_common(10):
        print(f"  - {st}: {c}" if args.markdown else f"  {st}: {c}")
    print()

    # Category breakdown
    cat_count = Counter(r["category"] for r in rows)
    print("## By category" if args.markdown else "By category:")
    for cat, c in cat_count.most_common():
        print(f"  - {cat}: {c}" if args.markdown else f"  {cat}: {c}")
    print()

    # Detail table
    if args.markdown:
        print("## Per-POI detail (sorted by bad-pair %)")
        print()
        print("| State | Name | Category | Bad % | Nearest reachable (h) | Bucket |")
        print("|---|---|---|---:|---:|---|")
        for r in rows:
            nearest = f"{r['nearest_h']:.1f}" if not np.isnan(r['nearest_h']) else "--"
            print(f"| {r['state']} | {r['name']} | {r['category']} | "
                  f"{r['bad_pct']:.0f}% | {nearest} | {r['bucket']} |")
    else:
        print("Per-POI detail (sorted by bad-pair %, severest first):")
        print(f"{'idx':>3} {'state':>5} {'cat':<25} {'bad%':>5} {'near-h':>6} "
              f"{'bucket':<12} name")
        for r in rows:
            nearest = f"{r['nearest_h']:5.1f}" if not np.isnan(r['nearest_h']) else "  —  "
            cat = (r["category"] or "?")[:25]
            name = (r["name"] or "?")[:55]
            print(f"{r['idx']:>3} {r['state']:>5} {cat:<25} "
                  f"{r['bad_pct']:>4.0f}% {nearest:>6} "
                  f"{r['bucket']:<12} {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
