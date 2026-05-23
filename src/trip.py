"""Top-level Tier 2 pipeline orchestrator. See spec §3.

The pipeline:
    config → fetch_pois → build_matrix → solve_with_config → render_map
"""
from __future__ import annotations

import os
from pathlib import Path

from src.border_crossing import apply_border_penalty, summarize_border_impact
from src.config import TripConfig
from src.matrix_builder import build_matrix
from src.poi_query import fetch_pois
from src.solver import solve_with_config
from src.visualize import (
    StopGeo, colors_for_days, render_map, split_into_days,
    stop_geos_from_poi_table,
)


def _osrm_url_for_network(routing_network: str) -> str:
    """Resolve the OSRM endpoint URL for a routing_network value.

    Environment overrides take precedence over the conventional defaults:
      OSRM_URL    — for routing_network='us'        (default http://127.0.0.1:5000)
      OSRM_URL_NA — for routing_network='us_canada' (default http://127.0.0.1:5001)

    The two engines run on different ports so they can coexist locally,
    enabling side-by-side comparison maps without container churn.
    """
    if routing_network == "us_canada":
        return os.environ.get("OSRM_URL_NA", "http://127.0.0.1:5001")
    return os.environ.get("OSRM_URL", "http://127.0.0.1:5000")


def _build_stop_geos(pois: list[dict], order_nodes) -> dict:
    """Helper: order-aware StopGeo lookup keyed by node id."""
    return stop_geos_from_poi_table(order_nodes, pois)


def run_trip(
    config: TripConfig,
    output_dir: Path | None = None,
    osrm_url: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Run the full pipeline for `config` and write the HTML map.

    Returns the path to the written HTML file. If `dry_run=True`, returns
    a path that doesn't exist after printing the post-filter candidate set
    + resolved depot.

    `output_dir` defaults to `output/` at the repo root. The HTML lands at
    `<output_dir>/<config.name>.html`.

    `osrm_url` explicitly overrides the URL derived from
    `config.routing_network`. When None (the common case), the URL is
    chosen by `_osrm_url_for_network(config.routing_network)`.
    """
    output_dir = output_dir or (Path(__file__).resolve().parent.parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{config.name}.html"

    # Explicit param > config-derived. The same URL is used for both the
    # /table call (matrix build) and the per-leg /route calls (map render),
    # so a single OSRM container can serve both phases.
    effective_osrm_url = osrm_url or _osrm_url_for_network(config.routing_network)
    print(f">> Routing engine: {config.routing_network} ({effective_osrm_url})")

    pois = fetch_pois(config)
    print(f">> {len(pois)} POIs after filters")

    if dry_run:
        print(f">> Dry run — depot would be POI #0: {pois[0]['name']} ({pois[0]['state']})")
        return out_path  # path returned but not created

    # Matrix build. For routing_network='us_canada', we additionally build the
    # US-only matrix as a detection baseline so apply_border_penalty() can
    # identify which legs actually crossed the border and inject the
    # customs/passport-check time the solver would otherwise be blind to.
    # Without this, the solver picks Canada shortcuts that lose time net of
    # border overhead — see DECISIONS.md D5 follow-up.
    durations, distances = build_matrix(pois, osrm_url=effective_osrm_url)
    if config.routing_network == "us_canada" and config.border_crossing_minutes > 0:
        baseline_url = os.environ.get("OSRM_URL", "http://127.0.0.1:5000")
        print(f">> Building US-only baseline for border-detection ({baseline_url})...")
        us_durations, _ = build_matrix(pois, osrm_url=baseline_url)
        impact = summarize_border_impact(
            us_durations, durations, config.border_crossing_minutes
        )
        print(f">> Border crossings detected: {impact['n_cross_border_legs']} legs")
        print(f"   Avg raw savings:  {impact['avg_raw_savings_minutes']:+.1f} min/leg")
        print(f"   Avg net savings:  {impact['avg_net_savings_minutes']:+.1f} min/leg "
              f"(after {config.border_crossing_minutes} min × 2 crossings)")
        if impact['n_flipped_by_penalty']:
            print(f"   ⚠ {impact['n_flipped_by_penalty']} legs become net-worse via Canada — "
                  f"solver will route around them.")
        durations, distances, n_penalized = apply_border_penalty(
            us_durations, durations, distances, config.border_crossing_minutes
        )
        print(f">> Applied border penalty to {n_penalized} matrix entries")
    print(f">> Matrix {durations.shape}, solving (budget {config.time_limit_seconds}s)...")

    result = solve_with_config(config, pois, durations, distances)

    # Compute total distance in meters by translating Node.id back to the
    # corresponding matrix row/column (positions in the `pois` list, not the
    # DB id which Node.id stores).
    id_to_idx = {p["id"]: i for i, p in enumerate(pois)}
    n_stops = len(result.order)
    total_meters = sum(
        distances[id_to_idx[result.order[i].id]][id_to_idx[result.order[(i + 1) % n_stops].id]]
        for i in range(n_stops - 1)
    )
    total_miles = total_meters / 1609.344
    print(f">> {result.status}: {n_stops} stops, "
          f"{result.total_cost/3600:.1f} h, {total_miles:,.0f} mi")

    days = split_into_days(result, config.max_hours_per_day)
    day_colors = colors_for_days(len(days))
    print(f">> Splitting into {len(days)} days (cap {config.max_hours_per_day}h/day)")

    stop_geo = stop_geos_from_poi_table(result.order, pois)
    render_map(
        result=result,
        stop_geo=stop_geo,
        output_path=out_path,
        osrm_url=effective_osrm_url,
        use_road_geometry=True,
    )
    print(f">> Wrote {out_path}")
    return out_path
