"""Top-level Tier 2 pipeline orchestrator. See spec §3.

The pipeline:
    config → fetch_pois → build_matrix → solve_with_config → render_map
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from src.border_crossing import apply_border_penalty, summarize_border_impact
from src.config import TripConfig
from src.matrix_builder import build_matrix
from src.poi_query import fetch_pois
from src.solver import solve_with_config
from src.visualize import (
    StopGeo, colors_for_days, render_map, split_into_days,
    stop_geos_from_poi_table,
)


class OSRMEngineError(RuntimeError):
    """Raised when an OSRM engine isn't reachable or isn't serving the
    network the trip config claims. Distinct from generic requests errors
    so callers can catch the "your routing setup is wrong" case
    separately from "the engine crashed mid-trip."
    """


# Detroit → Buffalo road geometry passes through Ontario when Canadian
# roads are available. US-only OSRM has to detour around Lake Erie and
# reports ~360 mi. US+Canada OSRM goes through Windsor/London/Hamilton
# and reports ~256 mi. We use this as a sanity probe — if the engine
# advertised as 'us_canada' doesn't show meaningfully shorter distance
# than the US-only engine on this leg, it's misconfigured (e.g., wrong
# port mapping, container restarted on US-only artifact).
#
# Coords are (lon, lat) per OSRM's convention.
_PROBE_DETROIT = (-83.0458, 42.3314)
_PROBE_BUFFALO = (-78.8784, 42.8864)
_CANADA_SHORTCUT_THRESHOLD_METERS = 30_000  # ~19 mi — well below the ~167 km savings


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


def _osrm_route_distance_meters(url: str, a: tuple, b: tuple, timeout: int = 5) -> float:
    """Query a single /route leg and return the distance in meters. Raises
    requests.RequestException on network failures and OSRMEngineError on
    a non-Ok OSRM response (signals a real engine misconfig, not just a
    transient network blip)."""
    coords = f"{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}"
    resp = requests.get(
        f"{url.rstrip('/')}/route/v1/driving/{coords}?overview=false",
        timeout=timeout,
    )
    resp.raise_for_status()
    blob = resp.json()
    if blob.get("code") != "Ok" or not blob.get("routes"):
        raise OSRMEngineError(
            f"OSRM at {url} returned code={blob.get('code')!r} on the "
            f"health-probe route. The engine is up but not serving routes."
        )
    return float(blob["routes"][0]["distance"])


def _validate_engines_for_config(config: TripConfig) -> None:
    """Fail fast with actionable errors if the OSRM engines required by
    `config` aren't reachable AND serving the right network. Catches:

      F1: cross-border trip with US-only baseline engine down — would
          otherwise fail 30s into the matrix build with an error blaming
          the NA engine.
      F5: cross-border trip with OSRM_URL_NA pointed at the wrong port
          (e.g., a US-only engine restarted on :5001 by mistake). Would
          otherwise silently degrade to US-only routing while claiming
          us_canada in the banner.

    Costs ~1-3 seconds against a healthy setup; trivially cheap insurance
    against the ~30s wasted matrix builds the silent failures cause.
    """
    primary_url = _osrm_url_for_network(config.routing_network)

    # Step 1: primary engine is reachable.
    try:
        _osrm_route_distance_meters(primary_url, _PROBE_DETROIT, _PROBE_BUFFALO)
    except requests.RequestException as exc:
        raise OSRMEngineError(
            f"Primary OSRM engine for routing_network={config.routing_network!r} "
            f"is unreachable at {primary_url}: {exc}.\n"
            f"  Fix: start it. For 'us': docker start optitrek-osrm-major.\n"
            f"       For 'us_canada': docker start optitrek-osrm-na."
        ) from exc

    # Step 2: cross-border consistency check (only when both apply).
    if config.routing_network == "us_canada" and config.border_crossing_minutes > 0:
        baseline_url = os.environ.get("OSRM_URL", "http://127.0.0.1:5000")
        try:
            us_dist = _osrm_route_distance_meters(
                baseline_url, _PROBE_DETROIT, _PROBE_BUFFALO
            )
        except requests.RequestException as exc:
            raise OSRMEngineError(
                f"Cross-border routing needs the US-only baseline engine to "
                f"detect which legs actually cross the border, but it's "
                f"unreachable at {baseline_url}: {exc}.\n"
                f"  Fix: docker start optitrek-osrm-major (port 5000).\n"
                f"  Or:  set border_crossing_minutes=0 in the YAML to skip "
                f"the penalty (useful for NEXUS travelers or diagnostics)."
            ) from exc

        # Re-query the NA engine for the same leg so the comparison is
        # apples-to-apples (single round trip, no network jitter delta).
        na_dist = _osrm_route_distance_meters(
            primary_url, _PROBE_DETROIT, _PROBE_BUFFALO
        )
        if us_dist - na_dist < _CANADA_SHORTCUT_THRESHOLD_METERS:
            raise OSRMEngineError(
                f"routing_network='us_canada' selected, but the engine at "
                f"{primary_url} doesn't appear to serve Canadian roads.\n"
                f"  Detroit→Buffalo probe: US-only={us_dist/1609.344:.0f} mi, "
                f"NA={na_dist/1609.344:.0f} mi — expected NA to be ~100 mi shorter.\n"
                f"  Most likely cause: the NA container was restarted on the "
                f"wrong .osrm artifact (it's serving US-only data on port 5001).\n"
                f"  Fix: docker stop optitrek-osrm-na && ./scripts/build_na_osrm.sh "
                f"if needed, then re-run."
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

    # F1/F5 fix: validate OSRM engines BEFORE the slow matrix build. Cheap
    # (~1-3s) insurance against (a) baseline engine down silently mis-blaming
    # the NA engine in the error message and (b) misconfigured OSRM_URL_NA
    # pointing at the wrong port (a US-only engine on :5001 by mistake).
    # Skipped when an explicit osrm_url was passed — the caller is being
    # specific about routing, validation would just second-guess them.
    if osrm_url is None:
        _validate_engines_for_config(config)

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
    #
    # F11 fix: for a loop trip we sum N legs (including the closing leg
    # order[N-1] -> order[0]); for an open path we sum N-1 legs. Before
    # this fix we always used range(N-1), which for the Tier 1 49-stop
    # loop dropped ~one leg of distance (the report was low by ~200 mi).
    # The Tier 1 oracle in scripts/test_tier1_replica.py already used the
    # correct range(N), so this only affected the user-facing print.
    id_to_idx = {p["id"]: i for i, p in enumerate(pois)}
    n_stops = len(result.order)
    n_legs = n_stops if config.loop else n_stops - 1
    total_meters = sum(
        distances[id_to_idx[result.order[i].id]][id_to_idx[result.order[(i + 1) % n_stops].id]]
        for i in range(n_legs)
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
