"""Fetch OSRM road polylines for the 56 unique edges in the Olson-vs-Optitrek
comparison, tag each by category (shared / olson_only / optitrek_only).

Reads:  output/olson_vs_optitrek_edges.json
Writes: output/olson_vs_optitrek_polylines.geojson

GeoJSON has one LineString feature per unique edge with properties:
    category:        "shared" | "olson_only" | "optitrek_only"
    from_idx, to_idx (TSV indices into the stops table)
    miles, hours
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import polyline
import requests

REPO = Path(__file__).resolve().parent.parent
IN_JSON = REPO / "output" / "olson_vs_optitrek_edges.json"
OUT_GEOJSON = REPO / "output" / "olson_vs_optitrek_polylines.geojson"
OSRM = os.environ.get("OSRM_URL", "http://127.0.0.1:5000")


def fetch_leg(from_pt: dict, to_pt: dict) -> dict:
    url = (
        f"{OSRM}/route/v1/driving/"
        f"{from_pt['lon']},{from_pt['lat']};{to_pt['lon']},{to_pt['lat']}"
        f"?overview=full&geometries=polyline"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"OSRM failed for {from_pt['short']} → {to_pt['short']}: {data}")
    route = data["routes"][0]
    coords = [[lon, lat] for lat, lon in polyline.decode(route["geometry"])]
    return {
        "coords": coords,
        "miles": route["distance"] / 1609.344,
        "hours": route["duration"] / 3600.0,
    }


def main() -> int:
    data = json.loads(IN_JSON.read_text())
    stops_by_idx = {s["index"]: s for s in data["stops"]}

    edges_to_fetch = []
    for e in data["edges_shared"]:
        edges_to_fetch.append(("shared", e[0], e[1]))
    for e in data["edges_olson_only"]:
        edges_to_fetch.append(("olson_only", e[0], e[1]))
    for e in data["edges_optitrek_only"]:
        edges_to_fetch.append(("optitrek_only", e[0], e[1]))
    print(f"Fetching {len(edges_to_fetch)} unique edges from {OSRM}")

    features = []
    for cat, i, j in edges_to_fetch:
        a = stops_by_idx[i]
        b = stops_by_idx[j]
        leg = fetch_leg(a, b)
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": leg["coords"]},
            "properties": {
                "category": cat,
                "from_idx": i,
                "to_idx": j,
                "from_short": a["short"],
                "to_short": b["short"],
                "miles": leg["miles"],
                "hours": leg["hours"],
            },
        })
        marker = {"shared": "  ", "olson_only": "OL", "optitrek_only": "OP"}[cat]
        print(f"  [{marker}] {a['short'][:30]:<30s} → {b['short'][:30]:<30s}  "
              f"{leg['miles']:6.0f} mi  {leg['hours']:5.2f} h")

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
        "counts": {
            "shared": sum(1 for f in features if f["properties"]["category"] == "shared"),
            "olson_only": sum(1 for f in features if f["properties"]["category"] == "olson_only"),
            "optitrek_only": sum(1 for f in features if f["properties"]["category"] == "optitrek_only"),
        },
    }
    OUT_GEOJSON.write_text(json.dumps(fc))
    print(f"\nWrote {OUT_GEOJSON}")
    print(f"  Counts: {fc['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
