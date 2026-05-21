"""Tier 1 glue: load the cached matrix, solve in both modes, render two maps.

Inputs:  data/matrix/pois.parquet, data/matrix/duration.parquet,
         data/matrix/distance.parquet
Outputs: output/optitrek_capped.html, output/optitrek_uncapped.html

Run after src/matrix_builder.py has been run successfully:
    python -m src.run_tier1

Env knobs:
    OPTITREK_DEPOT_INDEX   integer row index into pois.parquet (default 0)
    OPTITREK_TIME_LIMIT    seconds per solve (default 300; matches Gap 8)
    OSRM_URL               passed through to visualize (default http://localhost:5000)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from src.solver import Node, solve, validate
from src.visualize import render_map, stop_geos_from_poi_table

REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX_DIR = REPO_ROOT / "data" / "matrix"
OUTPUT_DIR = REPO_ROOT / "output"

# Olson's 2015 result for the apples-to-apples comparison in the writeup.
OLSON_HOURS = 224.0
OLSON_MILES = 13_699.0

# Tier 1 candidate scope per DECISIONS.md D2: 48 contiguous states + DC = 49.
REQUIRED_STATES: set[str] = {
    "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE",
    "FL", "GA", "IA", "ID", "IL", "IN", "KS", "KY",
    "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS",
    "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV",
    "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
}


def load_poi_rows() -> list[dict]:
    path = MATRIX_DIR / "pois.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m src.matrix_builder` first"
        )
    table = pq.read_table(path)
    return table.to_pylist()


def load_matrix(name: str) -> np.ndarray:
    path = MATRIX_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run matrix_builder first")
    table = pq.read_table(path)
    return np.stack([col.to_numpy() for col in table.columns], axis=1)


def tour_distance_meters(
    order: list[Node], poi_rows: list[dict], distance: np.ndarray
) -> float:
    """Walk the closed loop through the distance matrix and sum it. The
    solver only reports leg_costs in the units of its input matrix (we
    pass duration), so distance is a derived stat for the Olson compare."""
    id_to_row_index = {row["id"]: i for i, row in enumerate(poi_rows)}
    total = 0.0
    for i in range(len(order)):
        a = id_to_row_index[order[i].id]
        b = id_to_row_index[order[(i + 1) % len(order)].id]
        total += float(distance[a, b])
    return total


def summary_line(
    label: str, hours: float, miles: float, stops: int, states: int
) -> str:
    h_pct = (hours / OLSON_HOURS - 1.0) * 100.0
    m_pct = (miles / OLSON_MILES - 1.0) * 100.0
    return (
        f"  {label:<10}  {stops:>3} stops · {states:>2} states · "
        f"{hours:6.1f} h  ({h_pct:+5.1f}% vs Olson)  · "
        f"{miles:7.0f} mi ({m_pct:+5.1f}% vs Olson)"
    )


def main() -> int:
    print(">> Loading POI table + matrices")
    poi_rows = load_poi_rows()
    duration = load_matrix("duration")
    distance_m = load_matrix("distance")
    n = len(poi_rows)
    print(f"   {n} POIs, duration matrix {duration.shape}")

    nodes = [Node(id=row["id"], state=row["state"]) for row in poi_rows]

    depot_index = int(os.environ.get("OPTITREK_DEPOT_INDEX", "0"))
    if not (0 <= depot_index < n):
        raise ValueError(f"OPTITREK_DEPOT_INDEX {depot_index} out of range [0, {n})")
    depot = poi_rows[depot_index]
    print(f"   depot = #{depot_index}: {depot['name']} ({depot['state']})")

    time_limit = int(os.environ.get("OPTITREK_TIME_LIMIT", "300"))
    print(f"   time limit per solve: {time_limit}s")

    stop_geo = stop_geos_from_poi_table(nodes, poi_rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, tuple] = {}
    for mode in ("capped", "uncapped"):
        print(f"\n>> Solving ({mode})")
        result = solve(
            nodes=nodes,
            distance_matrix=duration,
            required_states=REQUIRED_STATES,
            mode=mode,
            depot_index=depot_index,
            time_limit_seconds=time_limit,
        )
        problems = validate(result, REQUIRED_STATES)
        if problems:
            print(f"!! validation problems: {problems}")
        hours = result.total_cost / 3600.0
        miles = tour_distance_meters(result.order, poi_rows, distance_m) / 1609.344
        print(
            f"   status={result.status}  runtime={result.runtime_seconds:.1f}s  "
            f"stops={len(result.order)}  hours={hours:.1f}  miles={miles:,.0f}"
        )

        out_path = OUTPUT_DIR / f"optitrek_{mode}.html"
        print(f">> Rendering map → {out_path.relative_to(REPO_ROOT)}")
        render_map(result, stop_geo, output_path=out_path)
        results[mode] = (result, hours, miles)

    print("\n>> Summary (vs Olson 2015 — 50 landmarks, 224 h, 13,699 mi)")
    for mode in ("capped", "uncapped"):
        result, hours, miles = results[mode]
        print(summary_line(
            mode, hours, miles, len(result.order), len(result.states_covered)
        ))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
