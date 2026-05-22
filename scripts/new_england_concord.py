"""Regional Tier-1 demo: optimal loop hitting every NE NPS unit, depot Concord NH.

Filters our 438-POI pool to the 6 New England states (CT, ME, MA, NH, RI, VT),
adds Concord NH as a synthetic depot, builds a small OSRM /table matrix on
the fly, solves a forced-visit TSP via our existing OR-Tools solver, then
renders a Folium map with real OSRM road geometry.

Outputs: output/new_england_concord.html

Run from /mnt/e/dev/optitrek with the WSL venv (requires OSRM running on localhost:5000):
    /root/venvs/optitrek-wsl/bin/python -m scripts.new_england_concord
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import folium
import numpy as np
import polyline as polyline_lib
import pyarrow.parquet as pq
import requests

from src.solver import Node, solve

REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX_DIR = REPO_ROOT / "data" / "matrix"
OUTPUT_DIR = REPO_ROOT / "output"
OSRM_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5000")

# Concord, NH State House — synthetic depot (not in the NPS POI pool).
# 107 N Main St, Concord, NH 03301 — the oldest state house in the US still
# in continuous legislative use (built 1816-1819).
CONCORD_NH = {
    "id": "depot_nh_statehouse",
    "name": "New Hampshire State House (Concord)",
    "state": "DEPOT",
    "category": "depot",
    "lat": 43.2070,
    "lon": -71.5378,
}

NEW_ENGLAND_STATES = {"CT", "ME", "MA", "NH", "RI", "VT"}


def osrm_table(coords: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Get duration (s) and distance (m) matrices via OSRM /table.
    coords is a list of (lat, lon). OSRM expects lon,lat order in URL."""
    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    url = (
        f"{OSRM_URL}/table/v1/driving/{coord_str}"
        f"?annotations=duration,distance"
    )
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "Ok":
        raise RuntimeError(f"OSRM /table returned {j.get('code')}: {j}")
    dur = np.asarray(j["durations"], dtype=np.float32)
    dist = np.asarray(j["distances"], dtype=np.float32)
    return dur, dist


def fetch_leg_polyline(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    """Decoded road polyline between two lat/lons; straight-line fallback on error."""
    try:
        url = (
            f"{OSRM_URL}/route/v1/driving/"
            f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
            f"?overview=full&geometries=polyline"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("code") != "Ok" or not j.get("routes"):
            return [a, b]
        return polyline_lib.decode(j["routes"][0]["geometry"])
    except (requests.RequestException, ValueError, KeyError):
        return [a, b]


def main() -> int:
    # ---- 1. Load and filter POIs ----
    print(">> Loading POI table")
    pois_table = pq.read_table(MATRIX_DIR / "pois.parquet")
    all_pois = pois_table.to_pylist()
    ne_pois = [p for p in all_pois if p["state"] in NEW_ENGLAND_STATES]
    print(f"   {len(ne_pois)} NPS units in New England ({sorted(NEW_ENGLAND_STATES)})")
    state_counts = {}
    for p in ne_pois:
        state_counts[p["state"]] = state_counts.get(p["state"], 0) + 1
    for s in sorted(state_counts):
        print(f"     {s}: {state_counts[s]}")

    # ---- 2. Build the small POI list: depot first, then NPS units ----
    pois = [CONCORD_NH] + ne_pois
    n = len(pois)
    print(f"\n>> Building {n}x{n} OSRM /table matrix (depot + {n-1} NE NPS units)")
    coords = [(p["lat"], p["lon"]) for p in pois]
    dur, dist = osrm_table(coords)

    bad = np.isnan(dur)
    np.fill_diagonal(bad, False)
    if bad.any():
        bad_count = int(bad.sum())
        print(f"   WARNING: {bad_count} unreachable pairs in OSRM matrix")
        # Replace NaN with a large but finite value so the solver can proceed.
        dur = np.nan_to_num(dur, nan=1e9, posinf=1e9)
        dist = np.nan_to_num(dist, nan=1e9, posinf=1e9)
    else:
        print(f"   matrix clean (no NaN pairs)")

    # ---- 3. Solve ----
    # Each POI gets its own unique "state" code so capped mode forces visiting
    # every one (plain Hamiltonian cycle, depot fixed at index 0).
    nodes = [Node(id=p["id"], state=f"Z{i:02d}") for i, p in enumerate(pois)]
    required = {f"Z{i:02d}" for i in range(n)}

    print(f"\n>> Solving (depot = Concord NH, 120s budget)")
    result = solve(
        nodes=nodes,
        distance_matrix=dur,
        required_states=required,
        mode="capped",
        depot_index=0,
        time_limit_seconds=120,
    )

    id_to_idx = {p["id"]: i for i, p in enumerate(pois)}
    order_idx = [id_to_idx[nd.id] for nd in result.order]

    total_dist_m = sum(
        float(dist[order_idx[i], order_idx[(i + 1) % n]]) for i in range(n)
    )
    hours = result.total_cost / 3600.0
    miles = total_dist_m / 1609.344

    print(f"\n=== New England loop, starting and ending at Concord NH ===")
    print(f"  Status:    {result.status}")
    print(f"  Stops:     {n} ({n-1} NPS units + 1 depot)")
    print(f"  Drive time: {hours:.1f} h")
    print(f"  Distance:   {miles:,.0f} mi")

    # ---- 4. Print the tour ----
    print(f"\n  Visit order:")
    for visit_i, node_i in enumerate(order_idx):
        p = pois[node_i]
        marker = " (depot)" if node_i == 0 else ""
        print(f"    [{visit_i:>2}] {p['name']:<60} ({p['state']}){marker}")
    print(f"    [{n:>2}] {pois[order_idx[0]]['name']} (return to depot)")

    # ---- 5. Render Folium map with road geometry ----
    print(f"\n>> Fetching road polylines + rendering map")
    # Center on geographic midpoint of NE
    m = folium.Map(
        location=(43.5, -71.5), zoom_start=7, tiles="CartoDB positron", control_scale=True,
    )

    # Closed-loop road polyline through all stops in visit order
    full_path: list[tuple[float, float]] = []
    for i in range(n):
        a_idx = order_idx[i]
        b_idx = order_idx[(i + 1) % n]
        a = (pois[a_idx]["lat"], pois[a_idx]["lon"])
        b = (pois[b_idx]["lat"], pois[b_idx]["lon"])
        leg = fetch_leg_polyline(a, b)
        if full_path and leg and full_path[-1] == leg[0]:
            full_path.extend(leg[1:])
        else:
            full_path.extend(leg)

    folium.PolyLine(
        full_path, color="#2c5f8d", weight=4, opacity=0.85,
        tooltip=f"<b>New England loop, depot Concord NH</b><br>{hours:.1f} h · {miles:,.0f} mi · {n-1} NPS units",
    ).add_to(m)

    # Markers — depot in red star, NPS units in blue numbered flags
    for visit_i, node_i in enumerate(order_idx):
        p = pois[node_i]
        if node_i == 0:  # depot
            folium.Marker(
                (p["lat"], p["lon"]),
                tooltip="Depot: Concord, NH (start & end)",
                popup=folium.Popup(f"<b>Depot: {p['name']}</b><br>start & end of the loop", max_width=250),
                icon=folium.Icon(color="red", icon="home", prefix="fa"),
            ).add_to(m)
        else:
            folium.Marker(
                (p["lat"], p["lon"]),
                tooltip=f"#{visit_i} — {p['name']} ({p['state']})",
                popup=folium.Popup(
                    f"<b>Stop {visit_i}: {p['name']}</b><br>"
                    f"State: {p['state']}<br><small>id: {p['id']}</small>",
                    max_width=300,
                ),
                icon=folium.Icon(color="darkblue", icon="flag", prefix="fa"),
            ).add_to(m)

    # Summary panel
    panel = f"""
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: white; padding: 14px 18px; border-radius: 8px;
                box-shadow: 0 2px 12px rgba(0,0,0,0.18);
                font: 13px/1.5 system-ui, -apple-system, sans-serif;
                max-width: 320px;">
      <div style="font-weight:700; font-size:15px; margin-bottom:8px;">
        New England NPS loop
      </div>
      <div>Depot (start &amp; end): <b>Concord, NH</b></div>
      <div>NPS units visited: <b>{n-1}</b> across {len(state_counts)} states</div>
      <div>Drive time: <b>{hours:.1f} h</b></div>
      <div>Distance: <b>{miles:,.0f} mi</b></div>
      <div style="margin-top:8px; color:#555; font-size:11px;">
        Solver: OR-Tools forced-visit Hamiltonian cycle<br>
        Routing: self-hosted OSRM (major-roads US extract)
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(panel))

    out_path = OUTPUT_DIR / "new_england_concord.html"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    print(f"\n   wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
