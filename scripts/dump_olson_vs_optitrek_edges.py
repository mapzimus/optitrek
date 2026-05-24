"""Run Optitrek-on-Olson's-50-stops, dump stop coords + edge sets to JSON.

Outputs: output/olson_vs_optitrek_edges.json

  {
    "stops": [
      {"index": 0, "tsv_name": "...", "short": "Grand Canyon", "state": "AZ",
       "lat": 36.05, "lon": -112.14, "olson_order_pos": 0, "optitrek_order_pos": 0},
      ...
    ],
    "olson_order":   [0, 1, 2, ...],   # 50 indices in his published order
    "optitrek_order": [0, 7, 2, ...],  # 50 indices in OR-Tools order
    "olson_hours": 224.0,        "olson_miles": 13_699.0,
    "optitrek_hours": ...,       "optitrek_miles": ...,
    "edges_shared":         [[i, j], ...],
    "edges_olson_only":     [[i, j], ...],
    "edges_optitrek_only":  [[i, j], ...],
  }

Run from WSL Ubuntu (solver is pure Python; no OSRM needed for this step):
    /root/venvs/optitrek-wsl/bin/python -m scripts.dump_olson_vs_optitrek_edges
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from scripts.olson_route_diff import (
    OLSON_ROUTE_BLOG, BLOG_TO_TSV, parse_tsv, tour_cost,
    rotate_to_start, maybe_reverse,
)
from src.solver import Node, solve

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "output" / "olson_vs_optitrek_edges.json"
GEOCODED = REPO / "data" / "olson" / "geocoded.json"


def short_name(tsv_addr: str) -> tuple[str, str]:
    """Return (short_landmark_name, state_code) for label display."""
    landmark = tsv_addr.split(",", 1)[0].strip()
    # Strip parenthesized "National Park" suffixes for brevity
    landmark = landmark.replace(" National Park", " NP")
    landmark = landmark.replace(" National Monument", " NM")
    landmark = landmark.replace(" National Historic Site", " NHS")
    landmark = landmark.replace(" National Memorial", " Mem")
    landmark = landmark.replace(" National Military Park", " NMP")
    landmark = landmark.replace(" National Recreation Area", " NRA")

    state = "??"
    states = ("AL AR AZ CA CO CT DC DE FL GA IA ID IL IN KS KY LA MA MD ME MI "
              "MN MO MS MT NC ND NE NH NJ NM NV NY OH OK OR PA RI SC SD TN TX "
              "UT VA VT WA WI WV WY").split()
    for st in states:
        if re.search(rf'(^|[\s,]){st}([\s,]|$|\d)', tsv_addr):
            state = st
            break
    if state == "??":
        if "Wisconsin" in tsv_addr: state = "WI"
        elif "Virginia" in tsv_addr and "West" not in tsv_addr: state = "VA"
        elif "Maine" in tsv_addr: state = "ME"
        elif "Oregon" in tsv_addr: state = "OR"
        elif "Colorado" in tsv_addr: state = "CO"
    return landmark, state


def resolve_coords(tsv_name: str, geocoded: dict) -> tuple[float, float]:
    """Look up coords for a TSV waypoint name. The geocoded.json keys are
    sometimes from the blog format, so we also check the reverse-of-BLOG_TO_TSV
    mapping when the direct lookup misses."""
    if tsv_name in geocoded:
        lat, lon = geocoded[tsv_name]
        return lat, lon
    # Find blog name that maps to this TSV name
    for blog, tsv in BLOG_TO_TSV.items():
        if tsv == tsv_name and blog in geocoded:
            lat, lon = geocoded[blog]
            return lat, lon
    # Last resort: fuzzy match by first chunk of name
    first_chunk = tsv_name.split(",", 1)[0].strip().lower()
    for k, v in geocoded.items():
        if first_chunk in k.lower():
            return v[0], v[1]
    raise KeyError(f"No coords for '{tsv_name}'")


def main() -> int:
    print(">> Loading Olson TSV + geocoded.json")
    waypoints, dist_m, dur_s = parse_tsv()
    geocoded = json.loads(GEOCODED.read_text())
    n = len(waypoints)
    print(f"   {n} TSV waypoints, {len(geocoded)} geocoded entries")

    idx_of = {w: i for i, w in enumerate(waypoints)}
    olson_addrs = [BLOG_TO_TSV.get(a, a) for a in OLSON_ROUTE_BLOG]
    olson_order = [idx_of[a] for a in olson_addrs]
    olson_dur = tour_cost(olson_order, dur_s)
    olson_dist = tour_cost(olson_order, dist_m)
    print(f"   Olson published: {olson_dur/3600:.1f} h  /  {olson_dist/1609.344:,.0f} mi")

    print("\n>> Solving with OR-Tools on Olson's matrix (180s)")
    nodes = [Node(id=i, state=f"Z{i:02d}") for i in range(n)]
    required = {f"Z{i:02d}" for i in range(n)}
    result = solve(
        nodes=nodes, distance_matrix=dur_s, required_states=required,
        mode="capped", depot_index=0, time_limit_seconds=180,
    )
    raw = [int(nd.id) for nd in result.order]
    # Align to same start + direction for readability of order positions
    opti_order = rotate_to_start(raw, olson_order[0])
    opti_order = maybe_reverse(opti_order, olson_order)
    opti_dur = tour_cost(opti_order, dur_s)
    opti_dist = tour_cost(opti_order, dist_m)
    print(f"   OR-Tools order: {opti_dur/3600:.1f} h  /  {opti_dist/1609.344:,.0f} mi")

    # Edge sets (undirected — frozenset of node indices)
    olson_edges = {frozenset({olson_order[i], olson_order[(i+1) % n]}) for i in range(n)}
    opti_edges = {frozenset({opti_order[i], opti_order[(i+1) % n]}) for i in range(n)}
    shared = olson_edges & opti_edges
    only_olson = olson_edges - opti_edges
    only_opti = opti_edges - olson_edges
    print(f"   Edges: shared={len(shared)}  olson-only={len(only_olson)}  optitrek-only={len(only_opti)}")

    # Build stops list with coords + order positions
    olson_pos = {idx: pos for pos, idx in enumerate(olson_order)}
    opti_pos = {idx: pos for pos, idx in enumerate(opti_order)}
    stops = []
    for i, name in enumerate(waypoints):
        landmark, state = short_name(name)
        lat, lon = resolve_coords(name, geocoded)
        stops.append({
            "index": i,
            "tsv_name": name,
            "short": landmark,
            "state": state,
            "lat": lat,
            "lon": lon,
            "olson_pos": olson_pos[i] + 1,    # human-readable 1-50
            "optitrek_pos": opti_pos[i] + 1,
        })

    payload = {
        "stops": stops,
        "olson_order": olson_order,
        "optitrek_order": opti_order,
        "olson_hours": olson_dur / 3600,
        "olson_miles": olson_dist / 1609.344,
        "optitrek_hours": opti_dur / 3600,
        "optitrek_miles": opti_dist / 1609.344,
        "edges_shared":         sorted([sorted(list(e)) for e in shared]),
        "edges_olson_only":     sorted([sorted(list(e)) for e in only_olson]),
        "edges_optitrek_only":  sorted([sorted(list(e)) for e in only_opti]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
