"""Olson control experiment — pure optimizer comparison.

Loads Olson's 50 Google-Maps-distance pairs (data/olson/waypoints-dist-dur.tsv,
from his 2015 repo) into a 50x50 symmetric matrix, then runs our OR-Tools
solver to find the optimal Hamiltonian cycle. Same inputs as Olson's genetic
algorithm — eliminates routing-engine confounds (his Google vs our OSRM).
If we beat his 13,699 mi / 224 h, that's pure optimizer-quality win.

Trick: assign each waypoint its own unique "state" code so capped mode
(exactly 1 per required state) forces visiting all 50 nodes — i.e. plain
TSP, no set-cover.

Run from /mnt/e/dev/optitrek with the WSL venv:
    /root/venvs/optitrek-wsl/bin/python -m scripts.olson_control
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from src.solver import Node, solve

REPO_ROOT = Path(__file__).resolve().parent.parent
TSV = REPO_ROOT / "data" / "olson" / "waypoints-dist-dur.tsv"
OLSON_MILES = 13_699.0
OLSON_HOURS = 224.0


def parse_tsv() -> tuple[list[str], np.ndarray, np.ndarray]:
    """Returns (waypoints sorted alphabetically, dist_m 50x50, dur_s 50x50).
    Matrices are symmetric (each pair appears once in Olson's file)."""
    pairs: list[tuple[str, str, int, int]] = []
    with TSV.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader)  # header
        for w1, w2, d_m, dur_s in reader:
            pairs.append((w1, w2, int(d_m), int(dur_s)))

    wp_set: set[str] = set()
    for w1, w2, _, _ in pairs:
        wp_set.add(w1)
        wp_set.add(w2)
    waypoints = sorted(wp_set)
    idx = {w: i for i, w in enumerate(waypoints)}
    n = len(waypoints)

    dist = np.zeros((n, n), dtype=np.float32)
    dur = np.zeros((n, n), dtype=np.float32)
    for w1, w2, d_m, dur_s in pairs:
        i, j = idx[w1], idx[w2]
        dist[i][j] = dist[j][i] = d_m
        dur[i][j] = dur[j][i] = dur_s
    return waypoints, dist, dur


def main() -> int:
    waypoints, dist, dur = parse_tsv()
    n = len(waypoints)
    print(f"Loaded {n} waypoints, matrices {dist.shape}")
    print(f"  Sample pairs: dur[0,1]={dur[0,1]:.0f}s, dist[0,1]={dist[0,1]:.0f}m")

    # Give each waypoint its own unique "zone" so capped mode forces all 50
    # to be visited (plain TSP).
    nodes = [Node(id=i, state=f"Z{i:02d}") for i in range(n)]
    required = {f"Z{i:02d}" for i in range(n)}

    print(f"\nSolving 50-node TSP (capped mode, 300s budget)...")
    result = solve(
        nodes=nodes,
        distance_matrix=dur,
        required_states=required,
        mode="capped",
        depot_index=0,
        time_limit_seconds=300,
    )

    # Compute total distance from the duration-optimal tour.
    total_dist_m = 0.0
    for i in range(len(result.order)):
        a = int(result.order[i].id)
        b = int(result.order[(i + 1) % len(result.order)].id)
        total_dist_m += float(dist[a, b])

    hours = result.total_cost / 3600.0
    miles = total_dist_m / 1609.344

    print(f"\n=== Optitrek-on-Olson's-50 result ===")
    print(f"  Solver status:    {result.status}")
    print(f"  Solver runtime:   {result.runtime_seconds:.1f}s")
    print(f"  Stops visited:    {len(result.order)} (target 50)")
    print(f"  Total drive time: {hours:6.1f} h     (Olson 2015: 224.0 h)")
    print(f"  Total distance:   {miles:7.0f} mi   (Olson 2015: 13,699 mi)")
    print(f"")
    print(f"  Delta vs Olson:")
    print(f"    Time:     {hours - OLSON_HOURS:+6.1f} h   ({(hours/OLSON_HOURS - 1)*100:+5.2f}%)")
    print(f"    Distance: {miles - OLSON_MILES:+7.0f} mi  ({(miles/OLSON_MILES - 1)*100:+5.2f}%)")
    print(f"")
    print(f"  Tour order (first 5 + last):")
    for i, node in enumerate(result.order[:5]):
        print(f"    [{i:>2}] {waypoints[int(node.id)][:80]}")
    print(f"    ...")
    print(f"    [{len(result.order)-1:>2}] {waypoints[int(result.order[-1].id)][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
