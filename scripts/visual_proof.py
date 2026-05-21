"""Visual proof that OSRM is routing correctly.

Hits OSRM /route for a hand-picked Western parks loop, decodes the polyline
into lat/lon coords, and renders an interactive Folium map with markers
and the actual road geometry overlaid on a real basemap.

Reads OSRM_URL from env (default http://localhost:5000). Run after opening
an SSH tunnel to the build VM:

    ssh -N -L 5000:127.0.0.1:5000 mhowe@<VM>
    python -m scripts.visual_proof

Output: output/osrm_visual_proof.html (auto-opens in default browser).
"""
from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path

import folium
import polyline as polyline_lib
import requests

OSRM_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5000")
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "output" / "osrm_visual_proof.html"

# Eight iconic Western parks in clockwise-ish order. Coords are park centers
# (close to a real visitor center / main entrance road).
PARKS = [
    ("Yellowstone NP",   44.4280, -110.5885),
    ("Grand Teton NP",   43.7904, -110.6818),
    ("Arches NP",        38.7331, -109.5925),
    ("Bryce Canyon NP",  37.5930, -112.1871),
    ("Zion NP",          37.2982, -113.0263),
    ("Grand Canyon NP",  36.0544, -112.1401),
    ("Death Valley NP",  36.5054, -117.0795),
    ("Yosemite NP",      37.8651, -119.5383),
]


def fetch_leg(a, b):
    """Call OSRM /route. Returns (decoded_coords, distance_m, duration_s, waypoint_names)."""
    _, alat, alon = a
    _, blat, blon = b
    url = (
        f"{OSRM_URL}/route/v1/driving/"
        f"{alon:.6f},{alat:.6f};{blon:.6f},{blat:.6f}"
        f"?overview=full&geometries=polyline"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    blob = r.json()
    if blob.get("code") != "Ok":
        raise RuntimeError(f"OSRM returned non-Ok: {blob}")
    route = blob["routes"][0]
    coords = polyline_lib.decode(route["geometry"])
    waypoint_names = [w.get("name", "?") for w in blob["waypoints"]]
    return coords, route["distance"], route["duration"], waypoint_names


def main():
    print(f"OSRM endpoint: {OSRM_URL}")
    try:
        r = requests.get(f"{OSRM_URL}/route/v1/driving/-77.036,38.897;-71.058,42.360", timeout=5)
        if r.status_code != 200:
            print(f"ERROR: OSRM not responding (HTTP {r.status_code}). Is the SSH tunnel up?")
            return 1
    except requests.RequestException as e:
        print(f"ERROR: cannot reach OSRM at {OSRM_URL}: {e}")
        print("Open the SSH tunnel first:  ssh -N -L 5000:127.0.0.1:5000 mhowe@<VM-IP>")
        return 1

    legs = []
    total_dist_m = 0.0
    total_dur_s = 0.0
    print("\nFetching legs from OSRM...")
    for i in range(len(PARKS)):
        a = PARKS[i]
        b = PARKS[(i + 1) % len(PARKS)]
        t0 = time.perf_counter()
        coords, dist_m, dur_s, wp = fetch_leg(a, b)
        dt = (time.perf_counter() - t0) * 1000
        total_dist_m += dist_m
        total_dur_s += dur_s
        legs.append((a, b, coords, dist_m, dur_s, wp))
        print(
            f"  {a[0]:<18} -> {b[0]:<18} "
            f"{dist_m/1609.34:6.1f} mi  {dur_s/3600:5.2f} h  "
            f"({dt:.0f} ms, {len(coords)} pts)  "
            f"[{wp[0]} -> {wp[1]}]"
        )

    print(f"\nTotal loop: {total_dist_m/1609.34:,.0f} mi  /  {total_dur_s/3600:,.1f} h")

    # Render Folium map
    m = folium.Map(
        location=(40.5, -113.0),  # roughly centered on the loop
        zoom_start=5,
        tiles="CartoDB positron",
        control_scale=True,
    )

    # Draw each leg with a distinct color so visual contrast is clear.
    leg_colors = [
        "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
        "#ff7f00", "#a65628", "#f781bf", "#1b9e77",
    ]
    for i, (a, b, coords, dist_m, dur_s, wp) in enumerate(legs):
        folium.PolyLine(
            coords,
            color=leg_colors[i % len(leg_colors)],
            weight=4,
            opacity=0.85,
            tooltip=(
                f"Leg {i+1}: {a[0]} -> {b[0]}<br>"
                f"{dist_m/1609.34:.1f} mi, {dur_s/3600:.1f} h<br>"
                f"snapped: {wp[0]} -> {wp[1]}"
            ),
        ).add_to(m)

    # Numbered markers
    for i, (name, lat, lon) in enumerate(PARKS, start=1):
        folium.Marker(
            location=(lat, lon),
            tooltip=f"#{i} {name}",
            popup=folium.Popup(
                f"<b>Stop {i}: {name}</b><br>{lat:.4f}, {lon:.4f}",
                max_width=250,
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
        OSRM Visual Proof: Western Parks Loop
      </div>
      <div>Stops: <b>{len(PARKS)}</b>  Legs: <b>{len(legs)}</b></div>
      <div>Total distance: <b>{total_dist_m/1609.34:,.0f} mi</b> ({total_dist_m/1000:,.0f} km)</div>
      <div>Total drive time: <b>{total_dur_s/3600:,.1f} h</b></div>
      <div style="margin-top:8px; color:#555; font-size:11px;">
        Geometry from self-hosted OSRM on GCP VM,<br>
        served via SSH tunnel to BRONTOSAURUS.
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(panel))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUTPUT))
    print(f"\nMap written: {OUTPUT}")
    webbrowser.open(OUTPUT.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
