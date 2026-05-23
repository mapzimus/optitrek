"""Integration test: run trips/tier1_replica.yaml end-to-end and assert
the result matches Tier 1's known good output within ±0.5%.

This is NOT part of pytest tests/ — it requires a live PostgreSQL DB
and a live OSRM instance. Run from a context where both are up:
    /root/venvs/optitrek-wsl/bin/python -m scripts.test_tier1_replica

Tier 1 baseline (from BUILD_STATUS.md, 2026-05-21):
    49 stops, 193.0 hours, 9,744 miles

Implementation note — cached matrices vs live rebuild:
    Tier 1 (src/run_tier1.py) solved against the pre-built parquet matrices
    at data/matrix/ (438 POIs including PR, built 2026-05-21).  Rebuilding
    the matrix live via OSRM produces a 437-POI matrix (PR excluded by the
    tier1_replica.yaml state list) and — because the OSRM snap positions
    differ slightly across requests — a marginally different tour.  To
    reproduce the exact Tier 1 baseline we load the same cached matrices,
    then let solve_with_config handle the PR node: since PR is not in the
    config's state list it ends up in a 0-penalty disjunction and is never
    visited, matching Tier 1's capped solver behaviour exactly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from src.config import load_config
from src.solver import solve_with_config


TIER1_HOURS = 193.0
TIER1_MILES = 9744.0
TOLERANCE = 0.005  # ±0.5%

REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX_DIR = REPO_ROOT / "data" / "matrix"


def _load_matrix(name: str) -> np.ndarray:
    path = MATRIX_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m src.matrix_builder` first"
        )
    table = pq.read_table(path)
    return np.stack([col.to_numpy() for col in table.columns], axis=1)


def main() -> int:
    config_path = REPO_ROOT / "trips" / "tier1_replica.yaml"
    config = load_config(config_path)
    print(f"Loaded {config_path.name}")

    # Load the same cached matrices that Tier 1 used so we solve against an
    # identical input.  The PR node (id=388) is present in the parquet but
    # excluded from the tour by solve_with_config's disjunction logic (PR is
    # not in the config's state list).
    pois = pq.read_table(MATRIX_DIR / "pois.parquet").to_pylist()
    durations = _load_matrix("duration")
    distances = _load_matrix("distance")
    print(f"Loaded cached matrices: {len(pois)} POIs, duration {durations.shape}")

    result = solve_with_config(config, pois, durations, distances)
    n = len(result.order)

    # Use id_to_idx to translate Node.id → matrix position (same fix as
    # src/trip.py per T2-09 code review). Node.id is the DB primary key
    # (e.g., 42), NOT the matrix row/column index.
    id_to_idx = {p["id"]: i for i, p in enumerate(pois)}
    total_dist_m = sum(
        float(distances[id_to_idx[result.order[i].id]][id_to_idx[result.order[(i + 1) % n].id]])
        for i in range(n)
    )
    hours = result.total_cost / 3600
    miles = total_dist_m / 1609.344

    print(f"\n=== Result ===")
    print(f"  Stops:    {n}")
    print(f"  Hours:    {hours:.2f}  (Tier 1: {TIER1_HOURS:.2f})")
    print(f"  Miles:    {miles:,.0f}  (Tier 1: {TIER1_MILES:,.0f})")

    hours_pct = abs(hours - TIER1_HOURS) / TIER1_HOURS
    miles_pct = abs(miles - TIER1_MILES) / TIER1_MILES
    print(f"  Delta hours: {hours_pct*100:+.2f}%  (tolerance +/-{TOLERANCE*100:.1f}%)")
    print(f"  Delta miles: {miles_pct*100:+.2f}%  (tolerance +/-{TOLERANCE*100:.1f}%)")

    failures = []
    if n != 49:
        failures.append(f"expected 49 stops, got {n}")
    if hours_pct > TOLERANCE:
        failures.append(f"hours drift {hours_pct*100:.2f}% exceeds {TOLERANCE*100:.1f}%")
    if miles_pct > TOLERANCE:
        failures.append(f"miles drift {miles_pct*100:.2f}% exceeds {TOLERANCE*100:.1f}%")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"\nTier 1 replica reproduced within +/-{TOLERANCE*100:.1f}% -- refactor is correct.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
