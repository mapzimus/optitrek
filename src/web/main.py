"""Optitrek web frontend — Stage 1 (Tier 2 Phase 6).

Routes:
  GET  /                  the trip-builder form
  POST /solve             validate + run_trip + return result page
  GET  /api/categories    distinct POI categories (htmx + dropdowns)
  GET  /api/poi-search    POI autocomplete by name substring (htmx)
  GET  /maps/{file}       rendered Folium HTMLs (static mount)

Lifecycle: the app does NOT spin up OSRM. Local-dev expectation is that
the user has docker-started the engines themselves (e.g. via
./scripts/run_oracle.sh or run_comparison_map.sh) before hitting Solve.
The engine-validation step inside run_trip catches missing engines and
the route handler turns OSRMEngineError into a clean error page rather
than a 500.

To launch:
    cd /mnt/e/dev/optitrek
    /root/venvs/optitrek-wsl/bin/uvicorn src.web.main:app --reload --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import TripConfigError
from src.db import get_conn
from src.trip import OSRMEngineError, run_trip
from src.web.form_parser import form_to_config


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
MAPS_DIR = REPO_ROOT / "output" / "web"

# Ensure the mount target exists before FastAPI's StaticFiles wraps it,
# otherwise it raises on startup.
MAPS_DIR.mkdir(parents=True, exist_ok=True)


app = FastAPI(
    title="Optitrek",
    description="Algorithmic road-trip optimizer for the contiguous US",
    version="0.6.0",
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Mount the rendered Folium HTMLs at /maps so the result page can iframe
# them AND offer a download link. The HTMLs are full-page documents
# (Folium emits a complete <html>); they live under output/web/ which
# is already gitignored via output/.
app.mount("/maps", StaticFiles(directory=str(MAPS_DIR)), name="maps")


# ---------- helpers ----------

# Hardcoded list of US state codes the solver knows about. The DB only
# stores the 48 contiguous + DC for solver-eligible POIs (AK/HI are
# filtered out at fetch time per matrix_builder.EXCLUDED_STATES), so
# offering AK/HI in the form's state dropdowns would be misleading.
US_STATES = [
    "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA",
    "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME",
    "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ",
    "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
]


def _fetch_categories() -> list[str]:
    """Distinct POI categories from the DB, alphabetical. Used to
    populate the multiselect AND the category_priority table editor.
    Called on every /api/categories hit; light enough to skip caching
    for Stage 1 (table has ~13 distinct categories)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT category FROM pois "
            "WHERE source = 'nps' AND category IS NOT NULL "
            "ORDER BY category"
        )
        return [r[0] for r in cur.fetchall()]


def _search_pois(q: str, limit: int = 25) -> list[dict]:
    """Case-insensitive substring match against POI name. Returns the
    fields the autocomplete UI needs to render a suggestion row."""
    if not q.strip():
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, state, category FROM pois "
            "WHERE source = 'nps' AND name ILIKE %(q)s "
            "ORDER BY name "
            "LIMIT %(limit)s",
            {"q": f"%{q.strip()}%", "limit": limit},
        )
        return [
            {"id": r[0], "name": r[1], "state": r[2], "category": r[3]}
            for r in cur.fetchall()
        ]


# ---------- routes ----------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Render the trip-builder form. Categories come from the DB so
    the priority table editor always reflects what's currently in
    the catalog."""
    try:
        categories = _fetch_categories()
    except Exception as exc:
        # Most likely DATABASE_URL not set or Neon unreachable. We could
        # render an empty-categories form, but the form would be useless
        # without the category list — better to surface the issue.
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": "Database unreachable",
                "message": str(exc),
                "fix": (
                    "Check that DATABASE_URL is set in .env and that the "
                    "Neon Postgres project is online. Run `python -m "
                    "src.spatial_join --validate` from the CLI to verify "
                    "connectivity."
                ),
            },
            status_code=503,
        )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"states": US_STATES, "categories": categories},
    )


@app.get("/api/categories", response_class=JSONResponse)
def api_categories():
    """JSON variant for htmx-driven re-renders of the category dropdown."""
    return {"categories": _fetch_categories()}


@app.get("/api/poi-search", response_class=HTMLResponse)
def api_poi_search(request: Request, q: str = ""):
    """Returns HTML (not JSON) so htmx can swap it directly into the
    suggestions container. Returning HTML keeps the client-side
    JavaScript footprint at zero — htmx + Jinja partial = done."""
    results = _search_pois(q)
    return templates.TemplateResponse(
        request,
        "partials/poi_search_results.html",
        {"results": results, "q": q},
    )


@app.post("/solve", response_class=HTMLResponse)
async def solve(request: Request):
    """Parse the form, validate the config, run the pipeline, render
    the result page. Errors land on a friendly error page instead of
    a stack-trace 500."""
    # FastAPI's Form() decorator is awkward for nested-key forms (the
    # category_priority[<cat>] pattern), so we parse the raw form
    # ourselves. await is mandatory; the form body hasn't been read yet.
    form_dict = dict(await request.form())

    # ---- 1. Config build (catches TripConfigError) ----
    try:
        cfg, parse_warnings = form_to_config(form_dict)
    except TripConfigError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": "Invalid trip config",
                "message": str(exc),
                "fix": (
                    "Edit the form and re-submit. See the field-level "
                    "validation rules in src/config.py:__post_init__."
                ),
            },
            status_code=400,
        )

    # ---- 2. Run the pipeline (catches OSRMEngineError + others) ----
    try:
        out_path = run_trip(cfg, output_dir=MAPS_DIR)
    except OSRMEngineError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": "OSRM engine not ready",
                "message": str(exc),
                "fix": (
                    "Start the required OSRM container(s) and retry. See "
                    "`scripts/run_comparison_map.sh` for the docker run "
                    "commands."
                ),
            },
            status_code=503,
        )
    except Exception as exc:
        # Last-resort catch. Production would log the full traceback to
        # a monitoring sink; for Stage 1 we just show it inline since
        # the user is the developer running this locally.
        import traceback
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": f"{type(exc).__name__} during solve",
                "message": str(exc),
                "fix": traceback.format_exc(),
            },
            status_code=500,
        )

    # ---- 3. Render the result page ----
    map_filename = out_path.name
    map_url = f"/maps/{map_filename}"
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "trip_name": cfg.name,
            "map_url": map_url,
            "map_filename": map_filename,
            "parse_warnings": parse_warnings,
            "config_summary": {
                "routing_network": cfg.routing_network,
                "loop": cfg.loop,
                "time_limit_seconds": cfg.time_limit_seconds,
                "total_trip_days": cfg.total_trip_days,
                "must_include": cfg.must_include,
            },
        },
    )
