"""Re-solve the Tier 1 capped tour and dump the ordered 49-stop list to JSON.

Inputs:  data/matrix/pois.parquet, data/matrix/duration.parquet
Output:  output/tier1_tour.json

Schema:
    {
      "mode": "capped",
      "total_hours": float,
      "stops_count": int,
      "states_covered": ["AL", "AR", ...],
      "tour": [
        {"order_index": 0, "id": int, "name": str, "state": str,
         "category": str, "lat": float, "lon": float},
        ...
      ]
    }

Notes:
    - 60s time limit — solver typically converges close to optimal in that
      window on 438 nodes; we only need a representative tour for the QGIS
      overlay, not a fresh proof of optimality.
    - depot_index = 0 (same as run_tier1.py default).
    - No OSRM needed — this is pure Phase 3.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from src.run_tier1 import REQUIRED_STATES, load_matrix, load_poi_rows
from src.solver import Node, solve

OUTPUT = Path(__file__).resolve().parent.parent / "output" / "tier1_tour.json"


def main() -> int:
    poi_rows = load_poi_rows()
    duration = load_matrix("duration")
    nodes = [Node(id=row["id"], state=row["state"]) for row in poi_rows]

    print(f"Solving capped Tier 1 over {len(nodes)} POIs (60s budget)...")
    result = solve(
        nodes=nodes,
        distance_matrix=duration,
        required_states=REQUIRED_STATES,
        mode="capped",
        depot_index=0,
        time_limit_seconds=60,
    )
    print(
        f"  status={result.status}  stops={len(result.order)}  "
        f"hours={result.total_cost / 3600:.1f}"
    )

    id_to_row = {row["id"]: row for row in poi_rows}
    tour = []
    for i, node in enumerate(result.order):
        row = id_to_row[node.id]
        tour.append({
            "order_index": i,
            "id": int(node.id),
            "name": row["name"],
            "state": row["state"],
            "category": row["category"],
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
        })

    payload = {
        "mode": "capped",
        "total_hours": result.total_cost / 3600.0,
        "stops_count": len(tour),
        "states_covered": sorted(result.states_covered),
        "tour": tour,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
