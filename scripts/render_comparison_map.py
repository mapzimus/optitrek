"""scripts/render_comparison_map.py — dual-engine comparison gallery map.

Runs the same trip config against BOTH OSRM engines (US-only on :5000 and
US+Canada on :5001), then renders a single Folium HTML where each route is
its own toggleable FeatureGroup. The summary banner shows the delta.

Why this exists: it makes the Canada routing value visible. Tier 1's known
result is 193.0 h / 9,744 mi via US-only. If cross-border lops off the
Detroit→Buffalo (−1.8 h) and Niagara→Sault Ste M (−3.3 h) legs the solver
otherwise has to swallow, the comparison map shows exactly which segments
of the loop diverged and by how much.

Prereqs (both must be up):
    docker run --name optitrek-osrm-major  -d -p 127.0.0.1:5000:5000 \
        -v data/osrm-major:/data:ro    ghcr.io/project-osrm/osrm-backend:latest \
        osrm-routed --algorithm mld /data/us-major.osrm
    docker run --name optitrek-osrm-na     -d -p 127.0.0.1:5001:5000 \
        -v data/osrm-major-na:/data:ro ghcr.io/project-osrm/osrm-backend:latest \
        osrm-routed --algorithm mld /data/north-america-major.osrm

Run (from repo root, venv active):
    python scripts/render_comparison_map.py                       # tier1_replica default
    python scripts/render_comparison_map.py trips/all_national_parks.yaml
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import folium
import polyline as polyline_lib
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.config import load_config  # noqa: E402
from src.matrix_builder import build_matrix  # noqa: E402
from src.poi_query import fetch_pois  # noqa: E402
from src.solver import solve_with_config  # noqa: E402
from src.trip import _osrm_url_for_network  # noqa: E402
from src.visualize import StopGeo, stop_geos_from_poi_table  # noqa: E402

# Distinct route colors — chosen for high contrast on CartoDB Positron.
US_COLOR = "#2c5f8d"        # muted blue (matches default render_map color)
NA_COLOR = "#c0392b"        # warm red — clearly different, prints well
ROUTE_WEIGHT = 4
ROUTE_OPACITY = 0.75


def _fetch_polyline(
    a: StopGeo, b: StopGeo, osrm_url: str, timeout: int = 15
) -> list[tuple[float, float]]:
    """Pull the real road geometry from OSRM for one leg. Falls back to a
    straight line if the engine is unreachable (so the map still renders).

    IMPORTANT: each route must fetch its geometry from the engine that
    produced its matrix. Otherwise the US+Canada route's Niagara→Sault leg
    would be drawn going around Lake Superior — visually inconsistent with
    the solver's actual cost basis.
    """
    url = (
        f"{osrm_url.rstrip('/')}/route/v1/driving/"
        f"{a.lon:.6f},{a.lat:.6f};{b.lon:.6f},{b.lat:.6f}"
        f"?overview=full&geometries=polyline"
    )
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        blob = resp.json()
        if blob.get("code") == "Ok" and blob.get("routes"):
            return polyline_lib.decode(blob["routes"][0]["geometry"])
    except (requests.RequestException, ValueError):
        pass
    return [(a.lat, a.lon), (b.lat, b.lon)]


def _draw_route_group(
    name: str,
    color: str,
    result,
    stop_geo: dict,
    osrm_url: str,
    show: bool,
) -> folium.FeatureGroup:
    """Build one toggleable FeatureGroup containing the polyline of a route."""
    fg = folium.FeatureGroup(name=name, show=show)
    geos = [stop_geo[node.id] for node in result.order]
    n = len(geos)
    for i in range(n):
        a, b = geos[i], geos[(i + 1) % n]
        coords = _fetch_polyline(a, b, osrm_url)
        folium.PolyLine(
            coords,
            color=color,
            weight=ROUTE_WEIGHT,
            opacity=ROUTE_OPACITY,
        ).add_to(fg)
    return fg


def _draw_markers_group(
    name: str,
    color: str,
    result,
    stop_geo: dict,
    show: bool,
) -> folium.FeatureGroup:
    """Build a FeatureGroup of numbered CircleMarkers for one route's stops."""
    fg = folium.FeatureGroup(name=name, show=show)
    for i, node in enumerate(result.order, start=1):
        geo = stop_geo[node.id]
        folium.CircleMarker(
            location=(geo.lat, geo.lon),
            radius=5,
            color=color,
            weight=2,
            fill=True,
            fill_color="#ffffff",
            fill_opacity=1.0,
            tooltip=f"#{i} — {geo.node.state} — {geo.label}",
            popup=folium.Popup(
                html=f"<b>Stop {i}: {geo.label}</b><br>State: {geo.node.state}",
                max_width=300,
            ),
        ).add_to(fg)
    return fg


def _summary_html(
    cfg_name: str,
    result_us,
    result_na,
    miles_us: float,
    miles_na: float,
) -> str:
    """Comparison banner shown in the upper-right of the map."""
    us_h = result_us.total_cost / 3600
    na_h = result_na.total_cost / 3600
    delta_h = us_h - na_h
    delta_mi = miles_us - miles_na
    pct = (delta_h / us_h * 100) if us_h else 0.0
    return f"""
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: white; padding: 14px 18px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font: 13px/1.45 system-ui, -apple-system, sans-serif;
                max-width: 340px;">
      <div style="font-weight:600; font-size:15px; margin-bottom:8px;">
        US-only vs US+Canada — {cfg_name}
      </div>
      <div>
        <span style="color:{US_COLOR}; font-weight:600;">━━</span>
        US-only:&nbsp;
        <b>{us_h:,.1f} h</b> / <b>{miles_us:,.0f} mi</b> /
        {len(result_us.order)} stops
      </div>
      <div>
        <span style="color:{NA_COLOR}; font-weight:600;">━━</span>
        US+Canada:&nbsp;
        <b>{na_h:,.1f} h</b> / <b>{miles_na:,.0f} mi</b> /
        {len(result_na.order)} stops
      </div>
      <div style="margin-top:8px; padding-top:8px;
                  border-top:1px solid #e3e3e3; font-weight:600;">
        Saved by cross-border:
        <span style="color:#1f7a35;">
          {delta_h:+.1f} h ({pct:+.1f}%) / {delta_mi:+,.0f} mi
        </span>
      </div>
      <div style="color:#666; margin-top:6px; font-size:11px;">
        Toggle layers in the upper-right to compare routes.
      </div>
    </div>
    """


def _total_miles(result, pois: list[dict], distances) -> float:
    """Total loop distance in miles, walking the matrix by POI id → row."""
    id_to_idx = {p["id"]: i for i, p in enumerate(pois)}
    n = len(result.order)
    meters = sum(
        distances[id_to_idx[result.order[i].id]]
                  [id_to_idx[result.order[(i + 1) % n].id]]
        for i in range(n)
    )
    return float(meters) / 1609.344


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config_path", nargs="?", default="trips/tier1_replica.yaml",
        help="Path to the trip YAML (default: trips/tier1_replica.yaml)",
    )
    parser.add_argument(
        "--time-limit-override", type=int, default=None,
        help=("Override config.time_limit_seconds for BOTH solves. The Tier 1 "
              "oracle was tuned for 300s on the US-only matrix; the US+Canada "
              "matrix has a different search landscape and may need more time "
              "(900-1800s) to converge to a true cross-border optimum. Critical "
              "for fair comparison — otherwise the NA solve may get stuck in a "
              "worse local minimum and the map will misrepresent the value of "
              "cross-border routing."),
    )
    args = parser.parse_args()
    config_path = Path(args.config_path)
    cfg = load_config(config_path)
    if args.time_limit_override is not None:
        cfg = replace(cfg, time_limit_seconds=args.time_limit_override)
        print(f">> Time-limit override: {args.time_limit_override}s per solve")

    us_url = _osrm_url_for_network("us")
    na_url = _osrm_url_for_network("us_canada")

    print(f">> Comparison map for: {cfg.name}")
    print(f"   US-only engine:   {us_url}")
    print(f"   US+Canada engine: {na_url}")

    # POI filtering is matrix-independent, so we fetch once and share.
    cfg_us = replace(cfg, name=f"{cfg.name}_us",        routing_network="us")
    cfg_na = replace(cfg, name=f"{cfg.name}_us_canada", routing_network="us_canada")
    pois = fetch_pois(cfg_us)
    print(f">> {len(pois)} POIs after filters")

    print(">> Building US-only matrix...")
    dur_us, dist_us = build_matrix(pois, osrm_url=us_url)
    print(">> Solving US-only...")
    result_us = solve_with_config(cfg_us, pois, dur_us, dist_us)
    miles_us = _total_miles(result_us, pois, dist_us)
    print(f"   {result_us.status}: {len(result_us.order)} stops, "
          f"{result_us.total_cost/3600:.1f} h, {miles_us:,.0f} mi")

    print(">> Building US+Canada matrix...")
    dur_na, dist_na = build_matrix(pois, osrm_url=na_url)
    print(">> Solving US+Canada...")
    result_na = solve_with_config(cfg_na, pois, dur_na, dist_na)
    miles_na = _total_miles(result_na, pois, dist_na)
    print(f"   {result_na.status}: {len(result_na.order)} stops, "
          f"{result_na.total_cost/3600:.1f} h, {miles_na:,.0f} mi")

    delta_h = (result_us.total_cost - result_na.total_cost) / 3600
    delta_mi = miles_us - miles_na
    print(f">> Delta: {delta_h:+.2f} h, {delta_mi:+,.1f} mi saved by cross-border")

    # Compose map
    m = folium.Map(
        location=(44.0, -85.0),   # Great Lakes-centric — that's where the action is
        zoom_start=5,
        tiles="CartoDB positron",
        control_scale=True,
    )

    stop_geo_us = stop_geos_from_poi_table(result_us.order, pois)
    stop_geo_na = stop_geos_from_poi_table(result_na.order, pois)

    # Route polylines (both visible by default — that's the whole point of
    # the comparison view; user can hide either to inspect one in isolation)
    _draw_route_group("US-only route",      US_COLOR, result_us, stop_geo_us,
                      us_url, show=True).add_to(m)
    _draw_route_group("US+Canada route",    NA_COLOR, result_na, stop_geo_na,
                      na_url, show=True).add_to(m)

    # Markers — separated by route. NA markers hidden by default since most
    # overlap with US markers (same POI set, different ordering) and would
    # clutter the view. User can toggle on if they want to inspect the
    # US+Canada stop sequence.
    _draw_markers_group("US-only stops",   US_COLOR, result_us, stop_geo_us,
                        show=True).add_to(m)
    _draw_markers_group("US+Canada stops", NA_COLOR, result_na, stop_geo_na,
                        show=False).add_to(m)

    folium.LayerControl(collapsed=False, position="topleft").add_to(m)

    m.get_root().html.add_child(folium.Element(
        _summary_html(cfg.name, result_us, result_na, miles_us, miles_na)
    ))

    out_path = REPO_ROOT / "output" / f"{cfg.name}_comparison.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    print(f">> Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
