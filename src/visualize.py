"""Phase 4 — render an interactive Folium map from a solver result.

Pure-Python interface: takes the SolveResult and (optionally) OSRM-fetched
leg geometries, produces a standalone HTML file. No DB required at render
time; the SolveResult carries all the info we need plus we look up the leg
polylines from OSRM at render time.

Run from D:\\optitrek as a script (loads the saved matrix + reruns the solver
+ renders), or import render_map() from another script with a SolveResult in
hand.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import folium
import polyline as polyline_lib
import requests

from src.solver import Node, SolveResult

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OSRM_URL = "http://localhost:5000"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "optitrek_map.html"

# Visual styling. Tweak here, don't sprinkle through code.
ROUTE_COLOR = "#2c5f8d"
ROUTE_WEIGHT = 4
ROUTE_OPACITY = 0.85
MARKER_COLOR = "darkblue"
MARKER_ICON = "flag"
TILE_LAYER = "CartoDB positron"  # neutral background, route stands out
INITIAL_CENTER = (39.5, -98.35)  # geographic center of contiguous US
INITIAL_ZOOM = 4


@dataclass(frozen=True)
class StopGeo:
    """A stop with the lat/lon needed to place a marker. Solver Node carries
    only id + state; the caller has to provide coords from the POI table."""

    node: Node
    lat: float
    lon: float
    label: str           # what shows in the marker popup (name, designation)


# F4 fix: silent polyline fallbacks make the comparison map LIE — a
# fallback straight-line is visually indistinguishable from a real OSRM
# response on the rendered HTML. Track each failure so render_map can
# print a summary at the end. Module-level state is acceptable here
# because render_map's lifecycle is bounded (one call, one map).
_POLYLINE_FALLBACKS: list[tuple[str, str, str]] = []


def _osrm_route_polyline(
    a: StopGeo, b: StopGeo, osrm_url: str, timeout: int = 30
) -> list[tuple[float, float]]:
    """Fetch the real road polyline between two stops from OSRM. Returns
    a list of (lat, lon). Falls back to a straight line if OSRM is
    unreachable so the map still renders — but appends to
    `_POLYLINE_FALLBACKS` so the user sees a count at the end."""
    url = (
        f"{osrm_url.rstrip('/')}/route/v1/driving/"
        f"{a.lon:.6f},{a.lat:.6f};{b.lon:.6f},{b.lat:.6f}"
        f"?overview=full&geometries=polyline"
    )
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        blob = resp.json()
        if blob.get("code") != "Ok" or not blob.get("routes"):
            _POLYLINE_FALLBACKS.append(
                (a.label, b.label, f"OSRM returned code={blob.get('code')!r}")
            )
            return [(a.lat, a.lon), (b.lat, b.lon)]
        encoded = blob["routes"][0]["geometry"]
        # OSRM polyline default precision is 5 (matches polyline lib default).
        return polyline_lib.decode(encoded)
    except (requests.RequestException, ValueError) as exc:
        _POLYLINE_FALLBACKS.append((a.label, b.label, type(exc).__name__))
        return [(a.lat, a.lon), (b.lat, b.lon)]


def _summary_html(
    result: SolveResult,
    stop_count: int,
    state_count: int,
) -> str:
    """Inline HTML widget showing trip stats in the corner of the map."""
    hours = result.total_cost / 3600.0
    days_at_8h = hours / 8.0
    return f"""
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: white; padding: 12px 16px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font: 13px/1.4 system-ui, -apple-system, sans-serif;
                max-width: 280px;">
      <div style="font-weight:600; font-size:15px; margin-bottom:6px;">
        Optitrek route
      </div>
      <div>Stops: <b>{stop_count}</b></div>
      <div>States covered: <b>{state_count}</b></div>
      <div>Total drive time: <b>{hours:,.1f} h</b> ({days_at_8h:,.1f} days @ 8h/day)</div>
      <div style="color:#666; margin-top:6px; font-size:11px;">
        solver status: {result.status}
      </div>
    </div>
    """


def render_map(
    result: SolveResult,
    stop_geo: dict,           # node.id → StopGeo
    *,
    output_path: Path = DEFAULT_OUTPUT,
    osrm_url: str | None = None,
    use_road_geometry: bool = True,
) -> Path:
    """Build the Folium map and write it to output_path. Returns the path."""
    if not result.order:
        raise ValueError("SolveResult has no route to render")

    osrm_url = osrm_url or os.environ.get("OSRM_URL", DEFAULT_OSRM_URL)

    # F4 fix: reset the failure counter for this render. Each call to
    # render_map() owns its own counting window — if the caller renders
    # multiple maps in one process they each get their own summary.
    _POLYLINE_FALLBACKS.clear()

    m = folium.Map(
        location=INITIAL_CENTER,
        zoom_start=INITIAL_ZOOM,
        tiles=TILE_LAYER,
        control_scale=True,
    )

    # Resolve geo for every visited node.
    geos: list[StopGeo] = []
    for node in result.order:
        if node.id not in stop_geo:
            raise KeyError(f"stop_geo missing entry for node {node.id!r}")
        geos.append(stop_geo[node.id])

    # Draw the route as a closed loop: order[0] → order[1] → … → order[-1] → order[0].
    for i in range(len(geos)):
        a, b = geos[i], geos[(i + 1) % len(geos)]
        coords = (
            _osrm_route_polyline(a, b, osrm_url)
            if use_road_geometry
            else [(a.lat, a.lon), (b.lat, b.lon)]
        )
        folium.PolyLine(
            coords,
            color=ROUTE_COLOR,
            weight=ROUTE_WEIGHT,
            opacity=ROUTE_OPACITY,
        ).add_to(m)

    # Numbered markers at each stop. Stop 1 = depot (always order[0]).
    for i, geo in enumerate(geos, start=1):
        folium.Marker(
            location=(geo.lat, geo.lon),
            tooltip=f"#{i} — {geo.node.state} — {geo.label}",
            popup=folium.Popup(
                html=(
                    f"<b>Stop {i}: {geo.label}</b><br>"
                    f"State: {geo.node.state}<br>"
                    f"<small>park_id: {geo.node.id}</small>"
                ),
                max_width=300,
            ),
            icon=folium.Icon(color=MARKER_COLOR, icon=MARKER_ICON, prefix="fa"),
        ).add_to(m)

    # Summary panel
    m.get_root().html.add_child(folium.Element(_summary_html(
        result,
        stop_count=len(geos),
        state_count=len(result.states_covered),
    )))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(output_path))

    # F4 fix: surface silent polyline fallbacks. Without this, the
    # rendered map quietly substitutes straight lines for failed OSRM
    # calls and the user has no idea their geometry is partly fake.
    if _POLYLINE_FALLBACKS:
        n_fail = len(_POLYLINE_FALLBACKS)
        print(f"!! {n_fail} of {len(geos)} legs fell back to straight lines "
              f"(OSRM unreachable or returned non-Ok):")
        for src, dst, err in _POLYLINE_FALLBACKS[:5]:
            print(f"   {src} → {dst}: {err}")
        if n_fail > 5:
            print(f"   (+ {n_fail - 5} more)")

    return output_path


def stop_geos_from_poi_table(
    nodes: Iterable[Node],
    poi_rows: list[dict],
) -> dict:
    """Helper: given the solver's nodes (id+state) and the POI rows used to
    build the matrix (with name, lat, lon, etc.), produce the StopGeo lookup
    that render_map() needs. Matches by node.id == poi_row['id']."""
    by_id = {row["id"]: row for row in poi_rows}
    geos: dict = {}
    for node in nodes:
        row = by_id.get(node.id)
        if row is None:
            raise KeyError(f"no POI row found for node id {node.id!r}")
        label = row.get("name") or f"POI {node.id}"
        cat = row.get("category")
        if cat:
            label = f"{label} ({cat.replace('_', ' ')})"
        geos[node.id] = StopGeo(
            node=node,
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            label=label,
        )
    return geos


def split_into_days(
    result, max_hours_per_day: float = 8.0
) -> list[list[int]]:
    """Partition the visit order into day-indexed stop lists by walking
    leg_costs and starting a new day when adding the next leg would
    exceed max_hours_per_day. See spec §6.6.

    Returns list of lists; each inner list contains stop indices
    (referring to positions in result.order). A single leg longer than
    max_hours_per_day becomes its own day (no overnight splitting; that's
    Tier 3).
    """
    days: list[list[int]] = [[0]]
    today_hours = 0.0
    for i, leg_seconds in enumerate(result.leg_costs):
        leg_hours = leg_seconds / 3600.0
        if today_hours + leg_hours > max_hours_per_day and days[-1]:
            days.append([])
            today_hours = 0.0
        days[-1].append(i + 1)
        today_hours += leg_hours
    # If the final day got only the closing return-to-depot (no marker),
    # drop the empty list — last day is the return, not a new visit.
    if days[-1] == [len(result.order)]:
        days.pop()
    return days


# Categorical color palettes for color-by-day rendering.
# _COLORS_9 is ColorBrewer Set1 with two deliberate swaps:
#   - drop #ffff33 (yellow) — hard to see on the CartoDB Positron basemap
#   - append #1b9e77 (Dark2 teal) — keeps the count at 9 distinct hues
# Set3 is unchanged ColorBrewer Set3 (12 muted hues for longer trips).
_COLORS_9 = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#a65628", "#f781bf", "#999999", "#1b9e77",
]
_COLORS_12 = [
    "#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3",
    "#fdb462", "#b3de69", "#fccde5", "#d9d9d9", "#bc80bd",
    "#ccebc5", "#ffed6f",
]


def colors_for_days(n_days: int) -> list[str]:
    """Return n_days distinct hex colors. Uses the 9-color palette for
    ≤9 days, the 12-color palette for 10-12, and cycles the 12-palette
    beyond that."""
    palette = _COLORS_9 if n_days <= 9 else _COLORS_12
    return [palette[i % len(palette)] for i in range(n_days)]
