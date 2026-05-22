"""Build two overlay maps comparing tours:
  Map 1: Olson 2015 vs Optitrek capped
  Map 2: All four — Olson + Control 1 (OR-Tools on Olson's 50) + Optitrek capped + California double

The four tours are:
  - olson:    Olson's published 2015 GA result (50 stops, his order)
  - control1: Our OR-Tools on his exact 50 addresses + his distances (50 stops, our order)
  - capped:   Our Tier 1 capped result (49 NPS, our order, 1 per state)
  - ca2:      Our Tier 1 with forced 2 California stops (50 NPS, our order)

Inputs:
  - data/olson/optimal_route.json (Olson's address-ordered tour, fetched from his gh-pages)
  - data/olson/geocoded.json (cached: address -> [lat, lon] via Nominatim)
  - data/olson/waypoints-dist-dur.tsv (Olson's Google distances for Control 1 solve)
  - data/matrix/pois.parquet + duration.parquet + distance.parquet (Tier 1 cached matrix)

Outputs:
  - output/overlay_optitrek_vs_olson.html
  - output/overlay_all_four.html

Run from /mnt/e/dev/optitrek:
  /root/venvs/optitrek-wsl/bin/python -m scripts.compare_overlays
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

import folium
import numpy as np
import pyarrow.parquet as pq
import requests

from src.solver import Node, solve

REPO_ROOT = Path(__file__).resolve().parent.parent
OLSON_DIR = REPO_ROOT / "data" / "olson"
MATRIX_DIR = REPO_ROOT / "data" / "matrix"
OUTPUT_DIR = REPO_ROOT / "output"
GEOCODE_CACHE = OLSON_DIR / "geocoded.json"
ROUTE_FILE = OLSON_DIR / "optimal_route.json"
OSRM_TSV = OLSON_DIR / "waypoints-dist-dur.tsv"

# Olson's published optimal route (51 addresses; last == first, loop closure).
# Verbatim from rhiever/optimal-roadtrip-usa gh-pages major-landmarks.html.
OLSON_ROUTE = [
    "Grand Canyon National Park Lodges, 88 Village Loop Drive, Grand Canyon Village, AZ 86023",
    "Bryce Canyon National Park, Hwy 63, Bryce, UT",
    "Craters of the Moon National Monument & Preserve, Arco, ID",
    "West Yellowstone Visitor Information Center, 30 Yellowstone Ave, West Yellowstone, MT 59758",
    "Pikes Peak, Colorado",
    "Carlsbad Caverns National Park, Carlsbad, NM",
    "The Alamo, Alamo Plaza, San Antonio, TX",
    "Chickasaw National Recreation Area, 1008 W 2nd St, Sulphur, OK 73086",
    "Toltec Mounds, Scott, AR",
    "Graceland, Elvis Presley Boulevard, Memphis, TN",
    "Vicksburg National Military Park, Clay Street, Vicksburg, MS",
    "French Quarter, New Orleans, LA",
    "USS Alabama, Battleship Parkway, Mobile, AL",
    "Cape Canaveral, FL",
    "Okefenokee Swamp Park, Okefenokee Swamp Park Road, Waycross, GA",
    "Fort Sumter National Monument, Sullivan's Island, SC",
    "Lost World Caverns, Lewisburg, WV",
    "Wright Brothers National Memorial Visitor Center, Manteo, NC",
    "Mount Vernon, Fairfax County, Virginia",
    "White House, Pennsylvania Avenue Northwest, Washington, DC",
    "Maryland State House, 100 State Cir, Annapolis, MD 21401",
    "New Castle Historic District, Delaware",
    "Congress Hall, Congress Place, Cape May, NJ 08204",
    "Liberty Bell, 6th Street, Philadelphia, PA",
    "Statue of Liberty, Liberty Island, NYC, NY",
    "The Mark Twain House & Museum, Farmington Avenue, Hartford, CT",
    "The Breakers, Ochre Point Avenue, Newport, RI",
    "USS Constitution, Boston, MA",
    "Acadia National Park, Maine",
    "Omni Mount Washington Resort, Mount Washington Hotel Road, Bretton Woods, NH",
    "Shelburne Farms, Harbor Road, Shelburne, VT",
    "USS Cod Submarine Memorial, East 9th Street, Cleveland, OH",
    "Olympia Entertainment, Woodward Avenue, Detroit, MI",
    "Spring Grove Cemetery, Spring Grove Avenue, Cincinnati, OH",
    "Mammoth Cave National Park, Mammoth Cave Pkwy, Mammoth Cave, KY",
    "West Baden Springs Hotel, West Baden Avenue, West Baden Springs, IN",
    "Lincoln Home National Historic Site Visitor Center, 426 South 7th Street, Springfield, IL",
    "Gateway Arch, Washington Avenue, St Louis, MO",
    "C. W. Parker Carousel Museum, South Esplanade Street, Leavenworth, KS",
    "Terrace Hill, Grand Avenue, Des Moines, IA",
    "Taliesin, County Road C, Spring Green, Wisconsin",
    "Fort Snelling, Tower Avenue, Saint Paul, MN",
    "Ashfall Fossil Bed, Royal, NE",
    "Mount Rushmore National Memorial, South Dakota 244, Keystone, SD",
    "Fort Union Trading Post National Historic Site, Williston, North Dakota 1804, ND",
    "Glacier National Park, 64 Grinnell Drive, West Glacier, MT 59936",
    "Hanford Site, Benton County, WA",
    "Columbia River Gorge National Scenic Area, Oregon",
    "Cable Car Museum, 94108, 1201 Mason St, San Francisco, CA 94108",
    "San Andreas Fault, San Benito County, CA",
    "Hoover Dam, Boulder City, CO",  # NB: addressed as CO in his data; actually in NV
]
# Strip the duplicate closing entry; we'll close the loop visually.

# Olson's distance TSV uses slightly different wording for some stops (Yellowstone,
# Glacier, Hoover). Map his published route names to TSV waypoint names where they differ.
ROUTE_TO_TSV = {
    "West Yellowstone Visitor Information Center, 30 Yellowstone Ave, West Yellowstone, MT 59758":
        "Yellowstone National Park, WY 82190",
    "Glacier National Park, 64 Grinnell Drive, West Glacier, MT 59936":
        "Glacier National Park, West Glacier, MT",
    "Hoover Dam, Boulder City, CO":
        "Hoover Dam, NV",
}


def load_geocode_cache() -> dict[str, list[float]]:
    if GEOCODE_CACHE.exists():
        return json.loads(GEOCODE_CACHE.read_text())
    return {}


def save_geocode_cache(cache: dict[str, list[float]]) -> None:
    OLSON_DIR.mkdir(parents=True, exist_ok=True)
    GEOCODE_CACHE.write_text(json.dumps(cache, indent=2))


def geocode_address(address: str, session: requests.Session) -> list[float] | None:
    """Geocode via OSM Nominatim. Returns [lat, lon] or None."""
    resp = session.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
        headers={"User-Agent": "Optitrek/1.0 (research project, github.com/mapzimus/optitrek)"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    rows = resp.json()
    if not rows:
        return None
    return [float(rows[0]["lat"]), float(rows[0]["lon"])]


def ensure_geocoded(addresses: list[str]) -> dict[str, list[float]]:
    """Returns address -> [lat, lon] for all addresses. Uses cache; queries
    Nominatim 1 req/sec for misses."""
    cache = load_geocode_cache()
    session = requests.Session()
    misses = [a for a in addresses if a not in cache]
    if misses:
        print(f"  Geocoding {len(misses)} new addresses via Nominatim (~1/sec)...")
        for i, addr in enumerate(misses, 1):
            coords = geocode_address(addr, session)
            if coords is None:
                # Try a simpler form — strip leading "X, " prefix until we have just the tail.
                parts = addr.split(", ")
                for j in range(1, len(parts) - 1):
                    simple = ", ".join(parts[j:])
                    coords = geocode_address(simple, session)
                    if coords is not None:
                        print(f"    [{i}/{len(misses)}] FALLBACK {simple!r} -> {coords}")
                        break
                else:
                    print(f"    [{i}/{len(misses)}] FAILED {addr!r}")
                    coords = None
            else:
                print(f"    [{i}/{len(misses)}] {addr[:60]!r} -> {coords}")
            if coords is not None:
                cache[addr] = coords
            time.sleep(1.1)  # be polite to public Nominatim
        save_geocode_cache(cache)
    missing = [a for a in addresses if a not in cache]
    if missing:
        raise RuntimeError(f"Could not geocode {len(missing)} addresses:\n  " + "\n  ".join(missing))
    return cache


def load_olson_matrices() -> tuple[list[str], np.ndarray, np.ndarray]:
    """Parse Olson's TSV → (sorted waypoints, dist_m, dur_s)."""
    pairs = []
    with OSRM_TSV.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader)
        for w1, w2, d, t in reader:
            pairs.append((w1, w2, int(d), int(t)))
    wp_set: set[str] = set()
    for w1, w2, _, _ in pairs:
        wp_set.add(w1); wp_set.add(w2)
    waypoints = sorted(wp_set)
    idx = {w: i for i, w in enumerate(waypoints)}
    n = len(waypoints)
    dist = np.zeros((n, n), dtype=np.float32)
    dur = np.zeros((n, n), dtype=np.float32)
    for w1, w2, d, t in pairs:
        i, j = idx[w1], idx[w2]
        dist[i][j] = dist[j][i] = d
        dur[i][j] = dur[j][i] = t
    return waypoints, dist, dur


def solve_on_olson_set(waypoints: list[str], dur: np.ndarray, dist: np.ndarray, budget_s: int = 60) -> tuple[list[int], float, float]:
    """Run OR-Tools on Olson's 50 in plain TSP mode. Returns
    (order as list of waypoint indices, total seconds, total meters)."""
    n = len(waypoints)
    nodes = [Node(id=i, state=f"Z{i:02d}") for i in range(n)]
    required = {f"Z{i:02d}" for i in range(n)}
    result = solve(
        nodes=nodes, distance_matrix=dur, required_states=required,
        mode="capped", depot_index=0, time_limit_seconds=budget_s,
    )
    order = [int(nd.id) for nd in result.order]
    total_dist = sum(float(dist[order[i], order[(i + 1) % n]]) for i in range(n))
    return order, result.total_cost, total_dist


def solve_optitrek(poi_rows: list[dict], duration: np.ndarray, distance_m: np.ndarray, required: set[str], budget_s: int = 60) -> tuple[list[int], float, float]:
    """Run our Tier 1 solver against the prepared candidate set. Returns
    (order as row indices into poi_rows, total seconds, total meters)."""
    n = len(poi_rows)
    nodes = [Node(id=row["id"], state=row["state"]) for row in poi_rows]
    id_to_idx = {row["id"]: i for i, row in enumerate(poi_rows)}
    result = solve(
        nodes=nodes, distance_matrix=duration, required_states=required,
        mode="capped", depot_index=0, time_limit_seconds=budget_s,
    )
    order = [id_to_idx[nd.id] for nd in result.order]
    total_dist = sum(float(distance_m[order[i], order[(i + 1) % len(order)]]) for i in range(len(order)))
    return order, result.total_cost, total_dist


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 1. Geocode Olson's 50 addresses ----
    print(">> [1/4] Geocoding Olson's 50 addresses (via Nominatim, cached)")
    olson_unique = list(dict.fromkeys(OLSON_ROUTE))  # dedupe, preserve order
    geocoded = ensure_geocoded(olson_unique)

    # Olson tour: ordered list of (lat, lon)
    olson_latlon = [tuple(geocoded[addr]) for addr in olson_unique]

    # ---- 2. Solve Control 1 (our OR-Tools on Olson's 50 + his Google distances) ----
    print("\n>> [2/4] Solving Control 1 (OR-Tools on Olson's 50)")
    tsv_wps, tsv_dist, tsv_dur = load_olson_matrices()
    c1_order, c1_dur, c1_dist = solve_on_olson_set(tsv_wps, tsv_dur, tsv_dist, budget_s=300)
    # Map TSV waypoint -> address (for geocoding). The TSV and route names
    # differ for 3 stops; use ROUTE_TO_TSV in reverse to find matching route names.
    tsv_to_route = {v: k for k, v in ROUTE_TO_TSV.items()}
    c1_addresses = [tsv_to_route.get(tsv_wps[i], tsv_wps[i]) for i in c1_order]
    # Geocode any TSV-only names we don't yet have
    geocoded = ensure_geocoded(list(set(c1_addresses)))
    control1_latlon = [tuple(geocoded[a]) for a in c1_addresses]
    c1_hours = c1_dur / 3600
    c1_miles = c1_dist / 1609.344
    print(f"   Control 1: {c1_hours:.1f} h, {c1_miles:,.0f} mi")

    # ---- 3. Load our matrix + solve Optitrek capped and California-double ----
    print("\n>> [3/4] Loading our matrix and solving capped + CA-double")
    pois_table = pq.read_table(MATRIX_DIR / "pois.parquet")
    poi_rows = pois_table.to_pylist()
    dur_table = pq.read_table(MATRIX_DIR / "duration.parquet")
    duration = np.stack([col.to_numpy() for col in dur_table.columns], axis=1)
    dist_table = pq.read_table(MATRIX_DIR / "distance.parquet")
    distance_m = np.stack([col.to_numpy() for col in dist_table.columns], axis=1)

    from src.run_tier1 import REQUIRED_STATES as TIER1_REQUIRED

    # Capped (1 per state)
    capped_order, capped_dur, capped_dist = solve_optitrek(
        poi_rows, duration, distance_m, TIER1_REQUIRED, budget_s=300)
    capped_latlon = [(poi_rows[i]["lat"], poi_rows[i]["lon"]) for i in capped_order]
    capped_h = capped_dur / 3600; capped_mi = capped_dist / 1609.344
    print(f"   Optitrek capped: {capped_h:.1f} h, {capped_mi:,.0f} mi ({len(capped_order)} stops)")

    # California double (CA split into CA-N/CA-S)
    CA_LAT_SPLIT = 36.0
    ca2_rows = []
    for row in poi_rows:
        r = dict(row)
        if row["state"] == "CA":
            r["state"] = "CA-N" if row["lat"] >= CA_LAT_SPLIT else "CA-S"
        ca2_rows.append(r)
    ca2_required = set(TIER1_REQUIRED) - {"CA"} | {"CA-N", "CA-S"}
    ca2_order, ca2_dur, ca2_dist = solve_optitrek(
        ca2_rows, duration, distance_m, ca2_required, budget_s=300)
    ca2_latlon = [(ca2_rows[i]["lat"], ca2_rows[i]["lon"]) for i in ca2_order]
    ca2_h = ca2_dur / 3600; ca2_mi = ca2_dist / 1609.344
    print(f"   California double: {ca2_h:.1f} h, {ca2_mi:,.0f} mi ({len(ca2_order)} stops)")

    # ---- 4. Render two overlay maps with real road geometry ----
    print("\n>> [4/4] Rendering overlay maps (with OSRM road geometry)")

    import polyline as polyline_lib

    OSRM_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5000")
    _leg_cache: dict[tuple[float, float, float, float], list[tuple[float, float]]] = {}

    def fetch_road_polyline(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
        """Get the actual road polyline between two lat/lon points via OSRM.
        Falls back to a straight line (just the two endpoints) on any error.
        Cached so the all-four map doesn't re-query legs the 2-route map already did."""
        key = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))
        if key in _leg_cache:
            return _leg_cache[key]
        try:
            url = (
                f"{OSRM_URL}/route/v1/driving/"
                f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
                f"?overview=full&geometries=polyline"
            )
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            blob = resp.json()
            if blob.get("code") != "Ok" or not blob.get("routes"):
                coords = [a, b]
            else:
                coords = polyline_lib.decode(blob["routes"][0]["geometry"])
        except (requests.RequestException, ValueError, KeyError):
            coords = [a, b]
        _leg_cache[key] = coords
        return coords

    def build_road_path(latlons: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], int]:
        """Build the closed-loop road polyline through all stops in order.
        Returns (full polyline, count of legs that fell back to straight line)."""
        path: list[tuple[float, float]] = []
        fallbacks = 0
        n = len(latlons)
        for i in range(n):
            a = latlons[i]
            b = latlons[(i + 1) % n]  # close the loop
            leg = fetch_road_polyline(a, b)
            if leg == [a, b]:
                fallbacks += 1
            # Avoid duplicating the join point between consecutive legs
            if path and leg and path[-1] == leg[0]:
                path.extend(leg[1:])
            else:
                path.extend(leg)
        return path, fallbacks

    def make_map(routes: list[dict], title: str, out_path: Path) -> None:
        """routes = [{'name', 'latlon', 'color', 'hours', 'miles', 'stops'}, ...].
        Each route is added as its own Folium FeatureGroup so a LayerControl
        in the top-right can toggle them independently."""
        m = folium.Map(
            location=(39.5, -98.35), zoom_start=4, tiles="CartoDB positron", control_scale=True,
        )
        for r in routes:
            print(f"     fetching road polylines for {r['name']} ({len(r['latlon'])} legs)...")
            road_coords, fallbacks = build_road_path(r["latlon"])
            if fallbacks:
                print(f"       {fallbacks} legs fell back to straight line (OSRM couldn't route)")
            # Layer name shown in the toggle: route name + headline stats so the
            # user can see what they're toggling without hovering for the tooltip.
            layer_name = (
                f'<span style="color:{r["color"]}">●</span> {r["name"]} '
                f'<span style="color:#666;font-size:11px">'
                f'({r["hours"]:.1f} h · {r["miles"]:,.0f} mi · {r["stops"]} stops)</span>'
            )
            fg = folium.FeatureGroup(name=layer_name, show=True)
            folium.PolyLine(
                road_coords, color=r["color"], weight=3, opacity=0.75,
                tooltip=f"<b>{r['name']}</b><br>{r['hours']:.1f} h · {r['miles']:,.0f} mi · {r['stops']} stops",
            ).add_to(fg)
            for lat, lon in r["latlon"]:
                folium.CircleMarker(
                    (lat, lon), radius=3, color=r["color"], fill=True, fill_opacity=0.85, weight=1,
                ).add_to(fg)
            fg.add_to(m)
        # LayerControl panel — collapsed=False keeps the checkboxes always visible.
        folium.LayerControl(collapsed=False, position="topleft").add_to(m)
        # Legend
        rows = ''.join(
            f'<div style="margin:2px 0"><span style="display:inline-block;width:18px;height:3px;'
            f'background:{r["color"]};margin-right:6px;vertical-align:middle"></span>'
            f'{r["name"]}<br><span style="color:#666;font-size:11px;margin-left:24px">'
            f'{r["hours"]:.1f} h · {r["miles"]:,.0f} mi · {r["stops"]} stops</span></div>'
            for r in routes
        )
        legend = f'''
        <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                    background: white; padding: 14px 18px; border-radius: 8px;
                    box-shadow: 0 2px 12px rgba(0,0,0,0.18);
                    font: 13px/1.5 system-ui, -apple-system, sans-serif;
                    max-width: 380px;">
          <div style="font-weight:700; font-size:15px; margin-bottom:8px;">{title}</div>
          {rows}
        </div>'''
        m.get_root().html.add_child(folium.Element(legend))
        m.save(str(out_path))
        print(f"   wrote {out_path.relative_to(REPO_ROOT)}")

    olson_route = dict(name="Olson 2015", latlon=olson_latlon, color="#e41a1c", hours=224.0, miles=13_699, stops=50)
    control1_route = dict(name="Control 1: OR-Tools on Olson's 50", latlon=control1_latlon, color="#984ea3", hours=c1_hours, miles=c1_miles, stops=50)
    capped_route = dict(name="Optitrek capped (49 NPS)", latlon=capped_latlon, color="#377eb8", hours=capped_h, miles=capped_mi, stops=len(capped_order))
    ca2_route = dict(name="Optitrek California-double (50 NPS)", latlon=ca2_latlon, color="#4daf4a", hours=ca2_h, miles=ca2_mi, stops=len(ca2_order))

    make_map(
        [olson_route, capped_route],
        "Optitrek vs Olson 2015 — apples-to-oranges (50 vs 49 stops)",
        OUTPUT_DIR / "overlay_optitrek_vs_olson.html",
    )
    make_map(
        [olson_route, control1_route, capped_route, ca2_route],
        "Four-way comparison: Olson vs OR-Tools-on-Olson vs Optitrek capped vs Optitrek 2-CA",
        OUTPUT_DIR / "overlay_all_four.html",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
