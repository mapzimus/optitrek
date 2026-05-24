"""Fetch real road polylines for each tour leg from OSRM and dump GeoJSON.

Reads:  output/tier1_tour.json   (produced by scripts/dump_tier1_tour.py)
Writes: output/tier1_tour_polylines.geojson

The tour is a closed loop with N stops → N legs (the last leg closes
order[-1] → order[0]). Each leg becomes one LineString feature with the
real driving geometry returned by OSRM's `/route` endpoint, decoded from
the polyline-encoded `routes[0].geometry` field.

Requires:
    - OSRM major-roads engine running on http://127.0.0.1:5000
    - `polyline` Python package (already in requirements.txt for visualize.py)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import polyline
import requests

REPO = Path(__file__).resolve().parent.parent
TOUR_IN = REPO / "output" / "tier1_tour.json"
GEOJSON_OUT = REPO / "output" / "tier1_tour_polylines.geojson"
OSRM = os.environ.get("OSRM_URL", "http://127.0.0.1:5000")


def fetch_leg(from_pt: dict, to_pt: dict) -> dict:
    """Hit OSRM /route for one leg. Returns a dict with decoded coords
    (list of [lon, lat]) and the leg's distance + duration."""
    url = (
        f"{OSRM}/route/v1/driving/"
        f"{from_pt['lon']},{from_pt['lat']};{to_pt['lon']},{to_pt['lat']}"
        f"?overview=full&geometries=polyline"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"OSRM bad response for {from_pt['name']} → {to_pt['name']}: {data}")
    route = data["routes"][0]
    # polyline.decode returns (lat, lon) pairs; GeoJSON wants [lon, lat].
    coords = [[lon, lat] for lat, lon in polyline.decode(route["geometry"])]
    return {
        "coords": coords,
        "distance_meters": route["distance"],
        "duration_seconds": route["duration"],
    }


def main() -> int:
    tour_data = json.loads(TOUR_IN.read_text())
    stops = tour_data["tour"]
    n = len(stops)
    print(f"Fetching {n} legs from {OSRM} ({n} stops, closed loop)...")

    features = []
    total_distance = 0.0
    total_duration = 0.0
    for i in range(n):
        a = stops[i]
        b = stops[(i + 1) % n]
        leg = fetch_leg(a, b)
        total_distance += leg["distance_meters"]
        total_duration += leg["duration_seconds"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": leg["coords"]},
            "properties": {
                "leg_index": i,
                "from_index": a["order_index"],
                "to_index": b["order_index"],
                "from_name": a["name"],
                "to_name": b["name"],
                "from_state": a["state"],
                "to_state": b["state"],
                "distance_meters": leg["distance_meters"],
                "duration_seconds": leg["duration_seconds"],
            },
        })
        print(f"  leg {i:2d}: {a['state']} {a['name'][:40]:<40s} → {b['state']} {b['name'][:40]:<40s}  "
              f"{leg['distance_meters']/1609.344:6.1f} mi  {leg['duration_seconds']/3600:5.2f} h")

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
        "summary": {
            "total_legs": n,
            "total_distance_miles": total_distance / 1609.344,
            "total_duration_hours": total_duration / 3600.0,
        },
    }
    GEOJSON_OUT.write_text(json.dumps(fc))
    print(f"\nTotal: {total_distance / 1609.344:,.0f} mi  /  {total_duration / 3600:.1f} h")
    print(f"Wrote {GEOJSON_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
