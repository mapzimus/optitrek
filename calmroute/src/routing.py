"""OSRM client, waypoint-perturbation candidate generation, and dedup.

OSRM's `alternatives=3` rarely returns more than 2 routes for short
trips, so we synthesize extra candidates by routing through midpoints
offset perpendicular to the fastest route's geometry (1.5–2.5 km either
side), then drop candidates whose geometry overlaps an already-kept
route by more than 80%.
"""

from dataclasses import dataclass, field

import httpx
import polyline as polyline_codec
from pyproj import Transformer
from shapely.geometry import LineString, Point

from . import config

_to_metric = Transformer.from_crs("EPSG:4326", config.METRIC_CRS, always_xy=True)
_to_wgs84 = Transformer.from_crs(config.METRIC_CRS, "EPSG:4326", always_xy=True)


class RoutingError(Exception):
    """OSRM was unreachable or returned no usable route."""


@dataclass
class Route:
    geometry: LineString          # lon/lat coords
    encoded_polyline: str
    duration_s: float
    distance_m: float
    is_perturbed: bool = False
    metric_geometry: LineString = field(init=False, repr=False)

    def __post_init__(self):
        xs, ys = zip(*self.geometry.coords)
        mx, my = _to_metric.transform(xs, ys)
        self.metric_geometry = LineString(zip(mx, my))


def _parse_osrm_routes(payload: dict, is_perturbed: bool = False) -> list[Route]:
    routes = []
    for r in payload.get("routes", []):
        coords = polyline_codec.decode(r["geometry"])  # [(lat, lon), ...]
        if len(coords) < 2:
            continue
        line = LineString([(lon, lat) for lat, lon in coords])
        routes.append(
            Route(
                geometry=line,
                encoded_polyline=r["geometry"],
                duration_s=float(r["duration"]),
                distance_m=float(r["distance"]),
                is_perturbed=is_perturbed,
            )
        )
    return routes


async def _osrm_route(
    client: httpx.AsyncClient, waypoints: list[tuple[float, float]], alternatives: int = 0
) -> dict:
    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in waypoints)
    params = {"overview": "full", "geometries": "polyline"}
    if alternatives:
        params["alternatives"] = str(alternatives)
    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = await client.get(
                f"{config.OSRM_URL}/route/v1/driving/{coord_str}",
                params=params,
                timeout=config.HTTP_TIMEOUT_S,
            )
            payload = resp.json()
            if payload.get("code") != "Ok":
                raise RoutingError(f"OSRM: {payload.get('code')} {payload.get('message', '')}")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            import asyncio

            await asyncio.sleep(0.5 * (attempt + 1))
    raise RoutingError(f"OSRM unreachable after {config.MAX_RETRIES} attempts: {last_error}")


def geometry_overlap(a: Route, b: Route, buffer_m: float = config.DEDUP_BUFFER_M) -> float:
    """Fraction of route b's length that lies within buffer_m of route a."""
    if b.metric_geometry.length == 0:
        return 1.0
    corridor = a.metric_geometry.buffer(buffer_m)
    shared = b.metric_geometry.intersection(corridor)
    return shared.length / b.metric_geometry.length


def dedup_routes(routes: list[Route]) -> list[Route]:
    """Keep routes in order, dropping any that overlap a kept route >80%."""
    kept: list[Route] = []
    for candidate in routes:
        is_dupe = any(
            geometry_overlap(existing, candidate) > config.DEDUP_OVERLAP_THRESHOLD
            for existing in kept
        )
        if not is_dupe:
            kept.append(candidate)
    return kept


def perturbation_waypoints(fastest: Route) -> list[tuple[float, float]]:
    """Via-points offset perpendicular to the fastest route, as (lon, lat).

    Sampled at PERTURBATION_POSITIONS along the route; offset by each
    PERTURBATION_OFFSETS_M distance, alternating sides via sign.
    """
    line = fastest.metric_geometry
    waypoints = []
    offsets = list(config.PERTURBATION_OFFSETS_M)
    for i, frac in enumerate(config.PERTURBATION_POSITIONS):
        dist = line.length * frac
        pt = line.interpolate(dist)
        # Local direction from a short chord around the sample point.
        before = line.interpolate(max(dist - 100.0, 0.0))
        after = line.interpolate(min(dist + 100.0, line.length))
        dx, dy = after.x - before.x, after.y - before.y
        norm = (dx**2 + dy**2) ** 0.5
        if norm == 0:
            continue
        # Perpendicular unit vector.
        px, py = -dy / norm, dx / norm
        offset = offsets[i % len(offsets)]
        ox, oy = pt.x + px * offset, pt.y + py * offset
        lon, lat = _to_wgs84.transform(ox, oy)
        waypoints.append((lon, lat))
    return waypoints


async def get_candidate_routes(
    client: httpx.AsyncClient,
    origin: tuple[float, float],   # (lon, lat)
    destination: tuple[float, float],
) -> list[Route]:
    """Fastest route + deduped alternatives, sorted by duration ascending."""
    payload = await _osrm_route(client, [origin, destination], alternatives=3)
    candidates = _parse_osrm_routes(payload)
    if not candidates:
        raise RoutingError("OSRM returned no routes.")
    candidates.sort(key=lambda r: r.duration_s)
    candidates = dedup_routes(candidates)

    if len(candidates) < config.TARGET_CANDIDATES:
        for via in perturbation_waypoints(candidates[0]):
            try:
                detour_payload = await _osrm_route(client, [origin, via, destination])
            except RoutingError:
                continue   # a via-point may land off-network; skip it
            candidates.extend(_parse_osrm_routes(detour_payload, is_perturbed=True))
            candidates.sort(key=lambda r: r.duration_s)
            candidates = dedup_routes(candidates)
            if len(candidates) >= config.MAX_CANDIDATES:
                break

    return candidates[: config.MAX_CANDIDATES]
