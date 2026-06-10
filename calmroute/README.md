# Calm Route

**A safer-route picker for anxious drivers in Massachusetts.** Enter where
you're going and how many extra minutes you can spare; Calm Route returns the
fastest route plus calmer alternatives, each scored 0–100 on five years of
crash history, current winter/ice risk, and live construction — with the math
shown, not vibes.

![Calm Route UI](docs/screenshot_ui.png)

This is a **scoring tool, not an optimizer**: OSRM proposes candidate routes
(its own alternatives plus waypoint-perturbed variants), and a deterministic
GIS pipeline scores them. No ML, no LLMs — every number in the UI traces back
to a public dataset.

## How a route gets its score

```
            OSRM alternatives=3 ──┐
                                  ├── candidates ── dedup (>80% overlap) ──┐
   perpendicular waypoint         │                                        │
   perturbation (±1.5–2.5 km) ────┘                                        ▼
                                                            three layers per route
   Layer 1  crashes        MassDOT crash records, 30 m corridor,
                           fatal x5 / injury x2 / PDO x1, recency-decayed,
                           normalized per km                        weight 0.60
   Layer 2  winter/ice     NWS hourly forecasts sampled every ~10 km,
                           deterministic freeze/precip/refreeze rules,
                           bridge freeze-first multiplier            weight 0.25
   Layer 3  construction   Mass511 live events within 50 m,
                           lane closures penalize, full closures
                           disqualify                                weight 0.15

   safety = 100 − Σ (weight x layer normalized 0–100 across the candidate set)
```

The winter layer deactivates itself in warm, dry conditions and its weight
redistributes to crash history — a July query is scored almost entirely on
crashes, and the UI hides the ice row. The fastest route is **always**
returned and labeled, even when it scores worst.

## Data sources

| Layer | Source | Access |
|---|---|---|
| Crashes | [MassDOT IMPACT portal](https://apps.impact.dot.state.ma.us/cdp/home) extracts (recent years) and the public `CrashClosedYear` ArcGIS FeatureServers (2002–2019 backfill) | `etl/load_crashes.py`, upsert by crash ID |
| Bridges | OSM `bridge=yes` ways from the same MA PBF the router uses | `etl/load_bridges.py` |
| Weather | [api.weather.gov](https://api.weather.gov) hourly forecasts | live, cached per grid cell |
| Construction | [Mass511](https://mass511.com) GraphQL map feed (`roadReports`, `constructionReports`) | live, 5-minute TTL cache |
| Routing | Self-hosted [OSRM](https://project-osrm.org) on the Geofabrik Massachusetts extract | Docker, port 5002 |

## Stack

FastAPI + httpx (async) · Neon PostGIS · OSRM in Docker · vanilla JS + Leaflet ·
Railway (app) + Cloudflare Tunnel (OSRM, stays on the home server).

## Run it locally

```bash
# 1. OSRM (one-time build, ~5 min for MA)
curl -L -o data/massachusetts-latest.osm.pbf \
  https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf
docker compose run --rm osrm-build-extract
docker compose run --rm osrm-build-partition
docker compose run --rm osrm-build-customize
docker compose up -d osrm

# 2. Database — any PostGIS works; Neon in prod, a container locally:
docker run -d --name calmroute-pg -p 5432:5432 \
  -e POSTGRES_PASSWORD=calmroute -e POSTGRES_DB=calmroute postgis/postgis:16-3.4
export DATABASE_URL=postgresql://postgres:calmroute@127.0.0.1:5432/calmroute

# 3. Load data (see etl/README.md for IMPACT download instructions)
pip install -r requirements.txt
python etl/load_crashes.py --from-arcgis --year 2019   # repeat per year
python etl/load_bridges.py --pbf data/massachusetts-latest.osm.pbf

# 4. API + UI
uvicorn src.main:app --port 8000
# open http://localhost:8000
```

Tests (no network, no DB): `python -m pytest tests/ -q`

## API

`POST /api/routes` — `{"origin": str, "destination": str, "buffer_minutes": int}` →
ranked routes with encoded polylines, durations, safety scores, per-layer
subscores, and human-readable explanations. Rate-limited 10 req/min/IP.

`GET /api/health` — OSRM / database / Mass511 reachability.

## Deploy

The FastAPI app deploys to Railway from `Dockerfile` + `railway.toml`
(set `DATABASE_URL` and `CALMROUTE_OSRM_URL`). OSRM stays on the home server;
expose it with a Cloudflare Tunnel on its own subdomain:

```bash
cloudflared tunnel create calmroute-osrm
cloudflared tunnel route dns calmroute-osrm osrm-calmroute.<your-domain>
cloudflared tunnel run --url http://localhost:5002 calmroute-osrm
```

## Honest limitations

- **Crash history ≠ future risk.** Scores are descriptive of the past, not
  predictive guarantees. The footer says so, and it matters.
- Crash density correlates with traffic volume; a busy-but-fine arterial can
  out-score a genuinely sketchy back road. Exposure normalization (AADT) is
  the obvious v2.
- The ice heuristic is rule-based on forecast temperature/precipitation; it
  knows bridges freeze first but not which hill never gets sun.
- Mass511 reports what MassDOT publishes — unreported lane drops won't appear.

*Informational only. Based on historical and public data. Not a guarantee of
safety — always drive to current conditions.*
