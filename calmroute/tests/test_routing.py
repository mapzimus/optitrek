"""Unit tests for route parsing, dedup, and waypoint perturbation —
synthetic geometries, no OSRM."""

import sys
from pathlib import Path

import polyline as polyline_codec
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.routing import (
    Route,
    _parse_osrm_routes,
    dedup_routes,
    geometry_overlap,
    perturbation_waypoints,
)


def _route(coords_lonlat, duration=600.0, distance=8000.0):
    line = LineString(coords_lonlat)
    encoded = polyline_codec.encode([(lat, lon) for lon, lat in coords_lonlat])
    return Route(geometry=line, encoded_polyline=encoded, duration_s=duration, distance_m=distance)


# Roughly Lynn -> Salem along two distinct corridors.
ROUTE_A = _route([(-70.9495, 42.4668), (-70.93, 42.48), (-70.91, 42.49), (-70.8967, 42.5018)])
ROUTE_A_JITTER = _route(
    [(-70.9495, 42.4668), (-70.9301, 42.4801), (-70.9101, 42.4901), (-70.8967, 42.5018)]
)
ROUTE_B = _route([(-70.9495, 42.4668), (-70.99, 42.50), (-70.95, 42.52), (-70.8967, 42.5018)])


def test_overlap_identical_routes():
    assert geometry_overlap(ROUTE_A, ROUTE_A) == 1.0


def test_overlap_near_identical_routes():
    assert geometry_overlap(ROUTE_A, ROUTE_A_JITTER) > config.DEDUP_OVERLAP_THRESHOLD


def test_overlap_distinct_routes():
    assert geometry_overlap(ROUTE_A, ROUTE_B) < 0.5


def test_dedup_drops_jitter_keeps_distinct():
    kept = dedup_routes([ROUTE_A, ROUTE_A_JITTER, ROUTE_B])
    assert kept == [ROUTE_A, ROUTE_B]


def test_perturbation_waypoints_offset_from_route():
    vias = perturbation_waypoints(ROUTE_A)
    assert len(vias) == len(config.PERTURBATION_POSITIONS)
    for lon, lat in vias:
        # Each via must sit off the route by roughly its configured offset
        # (within a loose band: 0.5–4 km from the line).
        dist_m = geometry_overlap  # noqa: F841 (clarity below)
        via_route = _route([(lon, lat), (lon + 1e-5, lat + 1e-5)])
        d = ROUTE_A.metric_geometry.distance(via_route.metric_geometry)
        assert 500.0 < d < 4000.0


def test_parse_osrm_routes():
    encoded = polyline_codec.encode([(42.4668, -70.9495), (42.5018, -70.8967)])
    payload = {"routes": [{"geometry": encoded, "duration": 558.4, "distance": 7639.4}]}
    routes = _parse_osrm_routes(payload)
    assert len(routes) == 1
    r = routes[0]
    assert r.duration_s == 558.4
    # Polyline lat/lon order converted to lon/lat in the shapely geometry.
    assert abs(r.geometry.coords[0][0] - (-70.9495)) < 1e-4
    assert abs(r.geometry.coords[0][1] - 42.4668) < 1e-4
