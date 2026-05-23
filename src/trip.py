"""Top-level Tier 2 pipeline orchestrator. See spec §3.

The pipeline:
    config → fetch_pois → build_matrix → solve_with_config → render_map
"""
from __future__ import annotations

from pathlib import Path

from src.config import TripConfig
from src.matrix_builder import build_matrix
from src.poi_query import fetch_pois
from src.solver import solve_with_config
from src.visualize import (
    StopGeo, colors_for_days, render_map, split_into_days,
    stop_geos_from_poi_table,
)


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
    """
    output_dir = output_dir or (Path(__file__).resolve().parent.parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{config.name}.html"

    pois = fetch_pois(config)
    print(f">> {len(pois)} POIs after filters")

    if dry_run:
        print(f">> Dry run — depot would be POI #0: {pois[0]['name']} ({pois[0]['state']})")
        return out_path  # path returned but not created

    durations, distances = build_matrix(pois)
    print(f">> Matrix {durations.shape}, solving (budget {config.time_limit_seconds}s)...")

    result = solve_with_config(config, pois, durations, distances)
    print(f">> {result.status}: {len(result.order)} stops, "
          f"{result.total_cost/3600:.1f} h, "
          f"{sum(distances[result.order[i].id][result.order[(i+1)%len(result.order)].id] for i in range(len(result.order)-1))/1609.344:,.0f} mi")

    days = split_into_days(result, config.max_hours_per_day)
    day_colors = colors_for_days(len(days))
    print(f">> Splitting into {len(days)} days (cap {config.max_hours_per_day}h/day)")

    stop_geo = stop_geos_from_poi_table(result.order, pois)
    render_map(
        result=result,
        stop_geo=stop_geo,
        output_path=out_path,
        osrm_url=osrm_url,
        use_road_geometry=True,
    )
    print(f">> Wrote {out_path}")
    return out_path
