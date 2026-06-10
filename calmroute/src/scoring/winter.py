"""Layer 2 — winter / ice risk along a route.

Deterministic heuristic over NWS hourly forecasts at points sampled
along the route (~every 10 km, capped to limit API calls). No ML.

Risk per sample point in [0, 1]:
  - 0 when warm (> FREEZE_TEMP_F) with no frozen precipitation
  - at/below freezing margin: base risk + precipitation-driven terms
    (snow/sleet, freezing rain, overnight refreeze)
Route risk = mean of sampled risks x bridge multiplier (bridges freeze
first; multiplier grows with the fraction of route length on bridges).

When every sample is below WINTER_ACTIVE_THRESHOLD the layer reports
inactive and the composite redistributes its weight to the crash layer.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

import httpx
import psycopg
from shapely.geometry import LineString

from .. import config


@dataclass
class HourlyConditions:
    temp_f: float
    precip_prob: float        # 0-100
    short_forecast: str       # e.g. "Light Snow", "Rain", "Sunny"
    hour: int                 # local hour of day 0-23


@dataclass
class WinterSubscore:
    raw: float                # 0-1 risk before normalization
    active: bool
    bridge_km: float
    explanation: str


FROZEN_TERMS = ("snow", "sleet", "ice", "freezing", "wintry", "flurr", "blizzard")
WET_TERMS = ("rain", "shower", "drizzle", "thunderstorm")


def hourly_risk(c: HourlyConditions) -> float:
    """Deterministic per-hour ice risk in [0, 1]."""
    forecast = c.short_forecast.lower()
    frozen = any(t in forecast for t in FROZEN_TERMS)
    wet = any(t in forecast for t in WET_TERMS)

    if c.temp_f > config.FREEZE_TEMP_F and not frozen:
        return 0.0

    risk = 0.0
    if c.temp_f <= config.FREEZE_TEMP_F:
        risk += 0.3
    if frozen:
        risk += 0.5 * max(c.precip_prob, 30.0) / 100.0
    elif wet and c.temp_f <= config.FREEZE_TEMP_F - 1:
        # Rain onto a sub-freezing surface: freezing rain territory.
        risk += 0.6 * max(c.precip_prob, 30.0) / 100.0
    if c.temp_f <= config.HARD_FREEZE_TEMP_F and c.hour in config.REFREEZE_HOURS:
        risk += 0.2
    return min(risk, 1.0)


def point_risk(hours: list[HourlyConditions]) -> float:
    """Risk at a sample point = worst hour in the 6-hour window."""
    return max((hourly_risk(h) for h in hours), default=0.0)


def bridge_multiplier(bridge_km: float, route_km: float) -> float:
    """1.0 with no bridges, rising with bridge share, capped at
    BRIDGE_MULTIPLIER_MAX. The x5 makes a 10% bridge share hit the cap —
    bridge exposure matters well before it dominates route length."""
    if route_km <= 0:
        return 1.0
    frac = min(bridge_km / route_km, 1.0)
    return min(1.0 + frac * 5.0, config.BRIDGE_MULTIPLIER_MAX)


def winter_subscore(
    point_risks: list[float], bridge_km: float, route_km: float
) -> WinterSubscore:
    """Pure aggregation — testable without NWS or the database."""
    if not point_risks:
        return WinterSubscore(0.0, False, bridge_km, "Winter conditions unavailable")
    base = sum(point_risks) / len(point_risks)
    active = max(point_risks) >= config.WINTER_ACTIVE_THRESHOLD
    raw = min(base * bridge_multiplier(bridge_km, route_km), 1.0) if active else 0.0

    if not active:
        explanation = "No winter risk in current conditions"
    else:
        level = "high" if raw > 0.6 else "moderate" if raw > 0.3 else "low"
        explanation = f"Ice risk: {level}"
        if bridge_km > 0.3 and bridge_multiplier(bridge_km, route_km) > 1.05:
            explanation += f" ({bridge_km:.1f} km on bridges — bridges freeze first)"
    return WinterSubscore(raw=raw, active=active, bridge_km=bridge_km, explanation=explanation)


def sample_points(route: LineString, metric_route: LineString) -> list[tuple[float, float]]:
    """(lat, lon) samples every WINTER_SAMPLE_SPACING_M, capped."""
    n = max(int(metric_route.length // config.WINTER_SAMPLE_SPACING_M) + 1, 2)
    n = min(n, config.WINTER_MAX_SAMPLE_POINTS)
    points = []
    for i in range(n):
        pt = route.interpolate(i / (n - 1), normalized=True)
        points.append((pt.y, pt.x))
    return points


# Grid-point forecasts cover ~2.5 km cells; cache by rounded coordinate so
# nearby samples (and nearby routes in the same request) share lookups.
_forecast_cache: dict[tuple[float, float], list[HourlyConditions]] = {}


async def fetch_point_conditions(
    client: httpx.AsyncClient, lat: float, lon: float
) -> list[HourlyConditions]:
    """Next-6-hours conditions at a point from api.weather.gov."""
    key = (round(lat, 2), round(lon, 2))
    if key in _forecast_cache:
        return _forecast_cache[key]

    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/geo+json"}
    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            meta = await client.get(
                f"{config.NWS_API_URL}/points/{lat:.4f},{lon:.4f}",
                headers=headers, timeout=config.HTTP_TIMEOUT_S,
            )
            meta.raise_for_status()
            hourly_url = meta.json()["properties"]["forecastHourly"]
            fc = await client.get(hourly_url, headers=headers, timeout=config.HTTP_TIMEOUT_S)
            fc.raise_for_status()
            periods = fc.json()["properties"]["periods"][:6]
            hours = []
            for p in periods:
                start = datetime.fromisoformat(p["startTime"])
                hours.append(
                    HourlyConditions(
                        temp_f=float(p["temperature"]),
                        precip_prob=float(
                            (p.get("probabilityOfPrecipitation") or {}).get("value") or 0
                        ),
                        short_forecast=p.get("shortForecast", ""),
                        hour=start.hour,
                    )
                )
            _forecast_cache[key] = hours
            return hours
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last_error = exc
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"NWS unavailable: {last_error}")


def fetch_bridge_km(conn: psycopg.Connection, route_wkt: str) -> float:
    """Total length (km) of bridge segments within BRIDGE_BUFFER_M of the route."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(ST_Length(geom::geography)), 0) / 1000.0
        FROM bridges
        WHERE ST_DWithin(
            geom::geography,
            ST_GeomFromText(%(wkt)s, 4326)::geography,
            %(buffer_m)s
        )
        """,
        {"wkt": route_wkt, "buffer_m": config.BRIDGE_BUFFER_M},
    ).fetchone()
    return float(row[0])
