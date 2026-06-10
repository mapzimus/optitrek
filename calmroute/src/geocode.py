"""Nominatim geocoding, bounded to Massachusetts.

Public Nominatim usage policy: max 1 request/second, identifying
User-Agent. We serialize requests through an asyncio lock with a
minimum spacing rather than trusting callers to behave.
"""

import asyncio
import time

import httpx

from . import config


class GeocodeError(Exception):
    """Address could not be geocoded inside Massachusetts."""


_lock = asyncio.Lock()
_last_request_at = 0.0
_MIN_SPACING_S = 1.05


def _in_ma_bbox(lat: float, lon: float) -> bool:
    west, south, east, north = config.MA_BBOX
    return south <= lat <= north and west <= lon <= east


async def geocode(client: httpx.AsyncClient, query: str) -> dict:
    """Resolve a free-text address to {lat, lon, display_name}, MA-bounded.

    Raises GeocodeError when Nominatim has no in-state match.
    """
    global _last_request_at
    query = (query or "").strip()
    if not query:
        raise GeocodeError("Empty address.")

    west, south, east, north = config.MA_BBOX
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "viewbox": f"{west},{north},{east},{south}",
        "bounded": 1,
        "countrycodes": "us",
    }

    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        async with _lock:
            wait = _MIN_SPACING_S - (time.monotonic() - _last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_request_at = time.monotonic()
        try:
            resp = await client.get(
                f"{config.NOMINATIM_URL}/search",
                params=params,
                headers={"User-Agent": config.USER_AGENT},
                timeout=config.HTTP_TIMEOUT_S,
            )
            if resp.status_code == 429:
                # Public Nominatim throttles aggressively; give it real room.
                last_error = GeocodeError("Geocoder is rate-limiting us")
                await asyncio.sleep(5.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            results = resp.json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            await asyncio.sleep(1.0 * (attempt + 1))
    else:
        raise GeocodeError(f"Geocoding service unavailable: {last_error}")

    if not results:
        raise GeocodeError(
            f"Could not find '{query}' in Massachusetts. "
            "Calm Route only covers MA addresses."
        )

    hit = results[0]
    lat, lon = float(hit["lat"]), float(hit["lon"])
    if not _in_ma_bbox(lat, lon):
        raise GeocodeError(f"'{query}' resolved outside Massachusetts.")
    return {"lat": lat, "lon": lon, "display_name": hit.get("display_name", query)}
