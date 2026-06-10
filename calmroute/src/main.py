"""Calm Route API — FastAPI app.

POST /api/routes  {origin, destination, buffer_minutes}
GET  /api/health
Static frontend served from /static, index at /.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import config
from .geocode import GeocodeError, geocode
from .routing import Route, RoutingError, get_candidate_routes
from .scoring import composite, construction, crashes, winter

log = logging.getLogger("calmroute")
logging.basicConfig(level=logging.INFO)

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient()
    yield
    await app.state.http.aclose()


app = FastAPI(title="Calm Route", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_static_dir = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(_static_dir / "index.html")


class RouteRequest(BaseModel):
    origin: str
    destination: str
    buffer_minutes: int = Field(
        default=config.DEFAULT_BUFFER_MINUTES, ge=0, le=config.MAX_BUFFER_MINUTES
    )


def _db_conn() -> psycopg.Connection:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(config.DATABASE_URL)


async def _score_crashes(route: Route) -> crashes.CrashSubscore:
    def work():
        with _db_conn() as conn:
            records = crashes.fetch_crashes_near_route(conn, route.geometry.wkt)
        return crashes.crash_subscore(records, route.distance_m / 1000.0)

    return await asyncio.to_thread(work)


async def _score_winter(client: httpx.AsyncClient, route: Route) -> winter.WinterSubscore:
    points = winter.sample_points(route.geometry, route.metric_geometry)
    conditions = await asyncio.gather(
        *(winter.fetch_point_conditions(client, lat, lon) for lat, lon in points)
    )
    risks = [winter.point_risk(hours) for hours in conditions]

    bridge_km = 0.0
    if max(risks, default=0.0) >= config.WINTER_ACTIVE_THRESHOLD:
        def bridge_work():
            with _db_conn() as conn:
                return winter.fetch_bridge_km(conn, route.geometry.wkt)

        bridge_km = await asyncio.to_thread(bridge_work)
    return winter.winter_subscore(risks, bridge_km, route.distance_m / 1000.0)


async def _score_construction(
    client: httpx.AsyncClient, route: Route
) -> construction.ConstructionSubscore:
    events = await construction.fetch_events(client)
    matched = construction.events_near_route(events, route.metric_geometry)
    return construction.construction_subscore(matched)


@app.post("/api/routes")
@limiter.limit(config.RATE_LIMIT)
async def api_routes(request: Request, body: RouteRequest):
    client: httpx.AsyncClient = app.state.http
    warnings: list[str] = []

    # 1. Geocode (sequential — Nominatim allows 1 req/s).
    try:
        origin = await geocode(client, body.origin)
        destination = await geocode(client, body.destination)
    except GeocodeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # 2. Candidate routes from OSRM (+ perturbation).
    try:
        candidates = await get_candidate_routes(
            client,
            (origin["lon"], origin["lat"]),
            (destination["lon"], destination["lat"]),
        )
    except RoutingError as exc:
        return JSONResponse(
            {"error": f"Routing engine unavailable: {exc}"}, status_code=503
        )

    # 3. Time-budget filter. The fastest route is ALWAYS kept.
    fastest = candidates[0]
    budget_s = fastest.duration_s + body.buffer_minutes * 60
    candidates = [r for r in candidates if r.duration_s <= budget_s or r is fastest]

    # 4. Score the three layers per route, layers degrading independently.
    async def safe(coro, fallback, label):
        try:
            return await coro
        except Exception as exc:    # noqa: BLE001 — any layer failure degrades, never 500s
            log.warning("%s layer failed: %s", label, exc)
            return fallback

    crash_subs, winter_subs, constr_subs = await asyncio.gather(
        asyncio.gather(*(safe(_score_crashes(r), None, "crash") for r in candidates)),
        asyncio.gather(*(safe(_score_winter(client, r), None, "winter") for r in candidates)),
        asyncio.gather(*(safe(_score_construction(client, r), None, "construction") for r in candidates)),
    )
    if any(s is None for s in crash_subs):
        warnings.append("Crash history unavailable — scores exclude crash data.")
        crash_subs = [s or crashes.crash_subscore([], r.distance_m / 1000.0)
                      for s, r in zip(crash_subs, candidates)]
    if any(s is None for s in winter_subs):
        warnings.append("Winter conditions unavailable — ice layer skipped.")
        winter_subs = [s or winter.winter_subscore([], 0.0, 1.0) for s in winter_subs]
    if any(s is None for s in constr_subs):
        warnings.append("Mass511 feed unavailable — construction layer skipped.")
        constr_subs = [s or construction.construction_subscore([]) for s in constr_subs]

    winter_active = any(s.active for s in winter_subs)

    # 5. Drop fully-closed routes (never the fastest — it stays, flagged).
    keep = [
        i for i, s in enumerate(constr_subs)
        if not s.disqualified or candidates[i] is fastest
    ]
    candidates = [candidates[i] for i in keep]
    crash_subs = [crash_subs[i] for i in keep]
    winter_subs = [winter_subs[i] for i in keep]
    constr_subs = [constr_subs[i] for i in keep]

    # 6. Composite score, normalized across the surviving candidate set.
    layers = [
        composite.ScoredLayers(c.raw, w.raw, k.raw)
        for c, w, k in zip(crash_subs, winter_subs, constr_subs)
    ]
    scored = composite.composite_scores(layers, winter_active)

    routes_payload = []
    for idx, (route, score) in enumerate(zip(candidates, scored)):
        explanations = [crash_subs[idx].explanation]
        if winter_active:
            explanations.append(winter_subs[idx].explanation)
        explanations.append(constr_subs[idx].explanation)
        routes_payload.append(
            {
                "id": f"route-{idx}",
                "label": "Fastest" if route is fastest else f"Alternative {idx}",
                "geometry": route.encoded_polyline,
                "duration_s": round(route.duration_s),
                "distance_m": round(route.distance_m),
                "delta_vs_fastest_s": round(route.duration_s - fastest.duration_s),
                "safety_score": score["safety_score"],
                "subscores": score["subscores"],
                "explanations": explanations,
                "is_fastest": route is fastest,
                "closed": constr_subs[idx].disqualified,
            }
        )
    # Ranked by safety, descending. The fastest stays in the list wherever
    # it lands; the UI flags it.
    routes_payload.sort(key=lambda r: r["safety_score"], reverse=True)

    return {
        "origin": origin,
        "destination": destination,
        "routes": routes_payload,
        "conditions": {"winter_active": winter_active},
        "warnings": warnings,
    }


@app.get("/api/health")
async def health():
    client: httpx.AsyncClient = app.state.http
    checks = {}

    try:
        r = await client.get(
            f"{config.OSRM_URL}/route/v1/driving/-71.06,42.36;-71.05,42.37",
            params={"overview": "false"}, timeout=5,
        )
        checks["osrm"] = r.json().get("code") == "Ok"
    except Exception:
        checks["osrm"] = False

    def db_check():
        try:
            with _db_conn() as conn:
                conn.execute("SELECT count(*) FROM crashes").fetchone()
            return True
        except Exception:
            return False

    checks["database"] = await asyncio.to_thread(db_check)

    try:
        events = await construction.fetch_events(client)
        checks["mass511"] = isinstance(events, list)
    except Exception:
        checks["mass511"] = False

    ok = all(checks.values())
    return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)
