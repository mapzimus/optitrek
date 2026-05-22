"""California-double control — Tier 1 with 2 California stops (matching Olson's setup).

Re-runs our solver against the existing 438-POI matrix but with California
re-labeled into two pseudo-zones (CA-N, CA-S) split by latitude. The capped
mode then visits exactly 1 from each pseudo-zone = 2 California stops total,
giving us 50 total stops covering 49 unique geographic regions — the same
shape as Olson's 50-stop trip.

Lat split: POIs with latitude >= 36.0 go to CA-N, below go to CA-S.
(36.0 N is roughly the Monterey Bay area; splits California into roughly
equal halves by area.)

Run from /mnt/e/dev/optitrek with the WSL venv:
    /root/venvs/optitrek-wsl/bin/python -m scripts.california_control
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from src.run_tier1 import (
    OLSON_HOURS,
    OLSON_MILES,
    REQUIRED_STATES as TIER1_REQUIRED_STATES,
    load_poi_rows,
    load_matrix,
    summary_line,
    tour_distance_meters,
)
from src.solver import Node, solve, validate
from src.visualize import render_map, stop_geos_from_poi_table

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"

# Latitude that splits California's POIs into N/S buckets. 36.0 N is
# roughly Monterey Bay / Big Sur — splits CA into roughly even halves.
CA_LAT_SPLIT = 36.0


def main() -> int:
    print(">> Loading existing matrix + POI table")
    poi_rows = load_poi_rows()
    duration = load_matrix("duration")
    distance_m = load_matrix("distance")
    n = len(poi_rows)
    print(f"   {n} POIs, duration matrix {duration.shape}")

    # Relabel California POIs into CA-N / CA-S pseudo-zones.
    ca_count_n = 0
    ca_count_s = 0
    relabeled: list[dict] = []
    for row in poi_rows:
        if row["state"] == "CA":
            new_state = "CA-N" if row["lat"] >= CA_LAT_SPLIT else "CA-S"
            r = dict(row)
            r["state"] = new_state
            if new_state == "CA-N":
                ca_count_n += 1
            else:
                ca_count_s += 1
            relabeled.append(r)
        else:
            relabeled.append(row)
    print(f"   California split: {ca_count_n} POIs in CA-N (lat>={CA_LAT_SPLIT}), "
          f"{ca_count_s} POIs in CA-S (lat<{CA_LAT_SPLIT})")

    # Build the new required-states set: original Tier 1 set minus CA plus CA-N and CA-S.
    required = set(TIER1_REQUIRED_STATES)
    required.discard("CA")
    required.add("CA-N")
    required.add("CA-S")
    print(f"   Required zones: {len(required)} (vs original {len(TIER1_REQUIRED_STATES)})")

    nodes = [Node(id=row["id"], state=row["state"]) for row in relabeled]

    depot_index = int(os.environ.get("OPTITREK_DEPOT_INDEX", "0"))
    if not (0 <= depot_index < n):
        raise ValueError(f"OPTITREK_DEPOT_INDEX {depot_index} out of range [0, {n})")
    depot = relabeled[depot_index]
    print(f"   depot = #{depot_index}: {depot['name']} ({depot['state']})")

    time_limit = int(os.environ.get("OPTITREK_TIME_LIMIT", "300"))
    print(f"   time limit: {time_limit}s per solve")

    stop_geo = stop_geos_from_poi_table(nodes, relabeled)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n>> Solving (capped, with 2 California stops enforced)")
    result = solve(
        nodes=nodes,
        distance_matrix=duration,
        required_states=required,
        mode="capped",
        depot_index=depot_index,
        time_limit_seconds=time_limit,
    )

    # Count California stops in the tour to verify.
    ca_visits = sum(1 for n in result.order if n.state in {"CA-N", "CA-S"})

    hours = result.total_cost / 3600.0
    miles = tour_distance_meters(result.order, relabeled, distance_m) / 1609.344
    print(
        f"   status={result.status}  runtime={result.runtime_seconds:.1f}s  "
        f"stops={len(result.order)}  CA-stops={ca_visits}  "
        f"hours={hours:.1f}  miles={miles:,.0f}"
    )

    # Render map.
    out_path = OUTPUT_DIR / "optitrek_california_double.html"
    print(f">> Rendering map → {out_path.relative_to(REPO_ROOT)}")
    render_map(result, stop_geo, output_path=out_path, use_road_geometry=True)
    # use_road_geometry=True pulls per-leg polylines from OSRM /route. Requires
    # OSRM running at OSRM_URL (default http://localhost:5000). If OSRM is
    # unreachable, visualize.py silently falls back to straight lines.

    print()
    print(">> Comparison vs Olson 2015 + original Tier 1")
    print(summary_line("California double", hours, miles, len(result.order), len({n.state for n in result.order} - {"CA-N", "CA-S"} | {"CA"})))
    print()
    print(f"   Recall: original Tier 1 (49 stops, 1 CA): 193.0 h / 9,744 mi")
    print(f"   Olson 2015 (50 stops, 2 CA):              224.0 h / 13,699 mi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
