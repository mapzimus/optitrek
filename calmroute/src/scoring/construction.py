"""Layer 3 — construction / incidents from the Mass511 event feed.

Mass511 (Castle Rock platform) serves its map data from an open GraphQL
endpoint at mass511.com/api/graphql — the same MapFeatures query the
site's own JS issues. Layer slugs are camelCase: "roadReports" carries
incidents/closures, "constructionReports" carries roadwork. Verified
live 2026-06-10 (74 events statewide).

Events are matched to a route when within CONSTRUCTION_BUFFER_M of its
geometry. Lane closures and work zones add penalty weight; an event
classified as a full closure disqualifies the route (the API drops it
unless it is the fastest route, which is always kept and flagged).
"""

import asyncio
import re
import time
from dataclasses import dataclass, field

import httpx
from pyproj import Transformer
from shapely.geometry import LineString, Point

from .. import config

_to_metric = Transformer.from_crs("EPSG:4326", config.METRIC_CRS, always_xy=True)

MAPFEATURES_QUERY = """
query MapFeatures($input: MapFeaturesArgs!) {
  mapFeaturesQuery(input: $input) {
    mapFeatures {
      bbox title tooltip uri
      features { id geometry properties }
      __typename
      ... on Event { priority }
    }
    error { message }
  }
}
"""


@dataclass
class TrafficEvent:
    event_id: str
    title: str
    tooltip: str
    lon: float
    lat: float
    icon_url: str
    kind: str = field(init=False)        # full_closure | lane_closure | roadwork | incident | other

    def __post_init__(self):
        self.kind = classify_event(self.title, self.tooltip, self.icon_url)


@dataclass
class ConstructionSubscore:
    raw: float
    events: list[TrafficEvent]
    disqualified: bool
    explanation: str


_FULL_CLOSURE_RE = re.compile(
    r"\b(road closed|all lanes closed|full closure|closed to (all )?traffic"
    r"|closed in both directions)\b",
    re.IGNORECASE,
)
_LANE_CLOSURE_RE = re.compile(r"\b(lane[s]? (closed|blocked)|some lanes closed)\b", re.IGNORECASE)
_INCIDENT_RE = re.compile(r"\b(crash|accident|disabled vehicle|incident)\b", re.IGNORECASE)
_ROADWORK_RE = re.compile(r"\b(roadwork|road work|construction|maintenance|paving)\b", re.IGNORECASE)


def classify_event(title: str, tooltip: str, icon_url: str = "") -> str:
    text = f"{title} {tooltip}"
    if _FULL_CLOSURE_RE.search(text) or "closure-critical" in icon_url:
        return "full_closure"
    if _LANE_CLOSURE_RE.search(text):
        return "lane_closure"
    if _INCIDENT_RE.search(text):
        return "incident"
    if _ROADWORK_RE.search(text) or "contruction" in icon_url or "construction" in icon_url:
        return "roadwork"
    return "other"


_STRIP_TAGS_RE = re.compile(r"<[^>]+>")


def _plain_tooltip(tooltip: str) -> str:
    return _STRIP_TAGS_RE.sub(" ", tooltip or "").replace("\xa0", " ").strip()


def events_near_route(events: list[TrafficEvent], metric_route: LineString) -> list[TrafficEvent]:
    corridor = metric_route.buffer(config.CONSTRUCTION_BUFFER_M)
    near = []
    for e in events:
        x, y = _to_metric.transform(e.lon, e.lat)
        if corridor.contains(Point(x, y)):
            near.append(e)
    return near


def construction_subscore(matched: list[TrafficEvent]) -> ConstructionSubscore:
    """Pure aggregation over events already matched to the route."""
    disqualified = any(e.kind == "full_closure" for e in matched)
    raw = sum(
        config.EVENT_WEIGHTS.get(e.kind) or 0.0
        for e in matched
        if e.kind != "full_closure"
    )
    if disqualified:
        closure = next(e for e in matched if e.kind == "full_closure")
        explanation = f"Road closure on this route: {closure.title}"
    elif matched:
        kinds = {}
        for e in matched:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        labels = {
            "lane_closure": "lane closure",
            "roadwork": "active work zone",
            "incident": "incident",
            "other": "advisory",
        }
        parts = [f"{n} {labels[k]}{'s' if n > 1 else ''}" for k, n in sorted(kinds.items())]
        first = _plain_tooltip(matched[0].tooltip)
        explanation = ", ".join(parts) + " on this route"
        if first:
            explanation += f" — e.g. {first[:140]}"
    else:
        explanation = "No active construction or incidents on this route"
    return ConstructionSubscore(raw, matched, disqualified, explanation)


# ---------------------------------------------------------------------------
# Feed fetch with 5-minute TTL cache

_cache: dict = {"at": 0.0, "events": None}
_cache_lock = asyncio.Lock()


async def fetch_events(client: httpx.AsyncClient) -> list[TrafficEvent]:
    async with _cache_lock:
        if _cache["events"] is not None and time.time() - _cache["at"] < config.MASS511_CACHE_TTL_S:
            return _cache["events"]

        west, south, east, north = config.MA_BBOX
        variables = {
            "input": {
                "north": north, "south": south, "east": east, "west": west,
                "zoom": 15,   # high zoom => no clustering, individual events
                "layerSlugs": ["roadReports", "constructionReports"],
                "nonClusterableUris": [],
            }
        }
        last_error: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            try:
                resp = await client.post(
                    config.MASS511_GRAPHQL_URL,
                    json={"query": MAPFEATURES_QUERY, "variables": variables},
                    headers={"User-Agent": config.USER_AGENT},
                    timeout=config.HTTP_TIMEOUT_S,
                )
                resp.raise_for_status()
                body = resp.json()
                features = body["data"]["mapFeaturesQuery"]["mapFeatures"] or []
                events = []
                for f in features:
                    if f.get("__typename") != "Event":
                        continue
                    geom = (f.get("features") or [{}])[0].get("geometry") or {}
                    coords = geom.get("coordinates")
                    if geom.get("type") != "Point" or not coords:
                        continue
                    icon = (
                        ((f.get("features") or [{}])[0].get("properties") or {})
                        .get("icon", {}).get("url", "")
                    )
                    events.append(
                        TrafficEvent(
                            event_id=f.get("uri", ""),
                            title=f.get("title", ""),
                            tooltip=f.get("tooltip", ""),
                            lon=float(coords[0]),
                            lat=float(coords[1]),
                            icon_url=icon,
                        )
                    )
                _cache["events"] = events
                _cache["at"] = time.time()
                return events
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                last_error = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Mass511 feed unavailable: {last_error}")
