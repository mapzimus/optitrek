# Optitrek — Project Blueprint

## What Is Optitrek?

Optitrek is an algorithmic road trip optimizer for the United States. Given a database of ~100,000 tourist attractions, national parks, museums, stadiums, historic sites, and train stations, it computes the optimal driving route subject to user-defined constraints: which categories of stops to visit, which states to cover, how many days you have, how far you want to drive each day, and what matters most to you.

The project originated from a simple question: can we take Randal Olson's viral 2015 "optimal road trip" project and do it properly with 2026 tools? The answer is yes, and then some. Olson hand-picked 50 stops and optimized their order with a genetic algorithm. Optitrek selects stops AND optimizes their order from a pool of 100,000+ candidates using a constrained optimization solver, self-hosted open-source routing, and an interactive web interface.

---

## The Vision in One Sentence

**Tier 1** proves the algorithm. **Tier 2** builds the tool. **Tier 3** ships the product.

---

## Build Order

| Step | Deliverable | Estimated Time | What Ships |
|---|---|---|---|
| 1. Tier 1 | NPS-only optimal loop across 48 states + D.C. | ~5 days | Blog post + interactive map + GitHub repo |
| 2. Database Expansion | Full POI database (~100k rows: OSM + Amtrak + overnight cities) | ~5–6 days | Validated PostGIS database ready for Tier 2 |
| 3. Tier 2 | Configurable solver with web UI | ~6 days | Hosted web app on Railway |
| 4. Tier 3 | Full product: accounts, Amtrak routing, gallery, presets, sharing | ~14 days | Consumer-facing trip planning product |

Total estimated build time: ~30 days of focused work across all four steps.

---

## Tech Stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| Database | Neon (PostGIS) |
| Routing engine | OSRM (Docker on BRONTOSAURUS via Cloudflare Tunnel) |
| Solver | Google OR-Tools |
| Visualization | Folium (interactive web maps) |
| Backend | FastAPI |
| Frontend | Vanilla HTML/JS |
| Auth (Tier 3) | Clerk |
| App hosting | Railway |
| Data sources | NPS API, OSM Overpass API, Amtrak GTFS, Census TIGER, Wikidata SPARQL |

All data sources are free, open, and geocoded. No proprietary API dependencies for core functionality.

---

## What Makes This Better Than Olson (2015)

| Dimension | Olson | Optitrek |
|---|---|---|
| Candidate pool | 50 hand-picked by a journalist | 400 (Tier 1) → 100,000+ (Tier 2–3) |
| Problem solved | Ordering only (TSP) | Selection + ordering (set cover + TSP) |
| Solver | Genetic algorithm, no optimality guarantee | OR-Tools, near-optimal with quality bounds |
| Routing | Google Maps API (proprietary, rate-limited) | OSRM (open source, self-hosted, unlimited) |
| Prioritization | Hidden editorial judgment | Explicit user-controlled category ranking |
| Configurability | None — one trip, one output | Categories, states, time budget, radius, daily limits, must-include stops |
| Output | Static image + 6 fragmented Google Maps links | Single interactive map with real road geometries |
| Practical planning | 224 hours continuous driving, no daily breakdown | Daily legs, overnight city suggestions, seasonal warnings |
| Sharing | Blog post | Shareable URLs, community gallery, clone-and-modify |

The full comparison is in the dedicated Olson Comparison document.

---

## How the Solver Works

**Tier 1 (cover all states):** Given ~400 NPS units, select the subset and ordering that visits at least one stop per state while minimizing total driving time. Tiebreaker for stop selection within a state: whichever NPS unit makes the overall loop shortest. This is a set cover + TSP hybrid solved by OR-Tools.

**Tier 2+ (time-budgeted):** Given a user's category selections, state preferences, and time budget (total days × max hours/day), maximize the total priority score of selected stops within the driving time budget. Users rank their categories by importance (national parks = 5, museums = 3, stadiums = 1). When two candidate routes score equally, the shorter route wins. This makes geographic efficiency the perpetual tiebreaker — the solver never picks a longer route when a shorter one has the same priority score.

**Tier 3 extends the priority stack:** must-include stops (infinite priority) → preset collection membership → user-starred individual stops → category ranking → optional community popularity signal → geographic efficiency tiebreaker.

---

## The Database

One PostGIS table (`pois`) holds everything:

| Source | Rows | Loaded When |
|---|---|---|
| NPS API | ~400 | Tier 1 |
| OSM Overpass (museums, zoos, stadiums, historic sites, etc.) | ~70,000–100,000 | Database Expansion |
| Amtrak GTFS stations | ~500 | Database Expansion |
| Overnight cities (OSM, pop > 5,000) | ~5,000–10,000 | Database Expansion |

Schema: `id`, `name`, `source`, `category`, `state`, `geom` (Point, 4326), `tags` (JSONB), `seasonal_notes` (JSONB, Tier 3). One table, multiple sources, extensible without schema changes.

The `amtrak_legs` table (created during database expansion) stores station-to-station travel times and frequencies for Tier 3 train routing.

---

## Architecture

```
[User Browser]
    │
    ▼
[Railway: FastAPI app + Clerk auth (Tier 3)]
    │
    ├──► [Neon: PostGIS] ──── POI queries, user data, saved trips
    │
    └──► [BRONTOSAURUS: OSRM] ──── distance matrices + route geometries
              (via Cloudflare Tunnel)
```

Tier 1 runs entirely on BRONTOSAURUS (local). Tier 2+ splits: the app lives on Railway, OSRM stays on BRONTOSAURUS behind a Cloudflare Tunnel. This keeps compute costs near zero — OSRM is the expensive component and runs free on existing hardware.

---

## Key Design Decisions

Nineteen decisions were made during planning, all documented with reasoning, alternatives considered, and tradeoffs. The highlights:

1. **Python over R** — routing and optimization ecosystem is stronger in Python
2. **Three tiers, built sequentially** — each tier ships a complete deliverable; no tier depends on a later tier being built
3. **Hotels, restaurants, and gas cut from scope** — tourist attractions only; overnight cities as a proxy for lodging
4. **Neon over Supabase** — already in stack, no need for Supabase's extra features
5. **Database built iteratively** — NPS for Tier 1, full expansion as standalone task before Tier 2
6. **Category-weighted prioritization** — user ranks categories, solver maximizes weighted score, shortest distance breaks ties
7. **OSRM on BRONTOSAURUS** — free compute, Railway hosts only the lightweight app
8. **US-only OSRM extract** — prevents routes from leaking through Canada

The full decision log with all 19 decisions is in its own document.

---

## Known Gaps and Open Decisions

A 22-item gap audit was conducted across all three tiers. Four decisions must be resolved before Tier 1 starts:

| Gap | Question | Proposed Answer |
|---|---|---|
| Multiple stops per state | Capped at 1 or uncapped? | Run both ways, present both in blog post |
| D.C. handling | Is D.C. a coverage zone? | Yes — 49th zone (48 states + D.C.) |
| OSRM extract | US-only or North America? | US-only (prevents Canada routing) |
| Success criteria | When is Tier 1 done? | 9-point checklist in the gap audit |

Additional gaps are documented for Tier 2 (solver timeout, matrix computation time, infeasible requests, rate limiting) and Tier 3 (Amtrak schedule simplification, collection accuracy, gallery thumbnails, mobile UX). None block Tier 1.

---

## Document Index

This project is defined by eight documents. They should be read in the following order:

| # | Document | Purpose |
|---|---|---|
| 01 | **OPTITREK-PROJECT-BLUEPRINT.md** | This document. Master overview, summary, and execution roadmap. |
| 02 | **OPTITREK-OLSON-COMPARISON.md** | Detailed comparison to Olson's 2015 project — what he did, what we improve, and why. |
| 03 | **OPTITREK-TIER1-PROJECT-DOC.md** | Tier 1 build spec. NPS-only optimal loop, 5 phases, ~5 days. |
| 04 | **OPTITREK-DATABASE-EXPANSION-SPEC.md** | Standalone database expansion. OSM + Amtrak + overnight cities, 5 phases, ~5–6 days. |
| 05 | **OPTITREK-TIER2-PROJECT-DOC.md** | Tier 2 build spec. Configurable solver + web UI, 5 phases, ~6 days. |
| 06 | **OPTITREK-TIER3-PROJECT-DOC.md** | Tier 3 build spec. Full product with accounts, Amtrak, gallery, presets, 7 phases, ~14 days. |
| 07 | **OPTITREK-DECISION-LOG.md** | All 19 planning decisions with reasoning, alternatives, and tradeoffs. |
| 08 | **OPTITREK-GAP-AUDIT.md** | 22 identified gaps, organized by tier, with proposed resolutions and open decisions. |

---

## What Happens Next

1. Resolve the four Tier 1 blocking decisions from the gap audit
2. Create the `optitrek` repo on BRONTOSAURUS
3. Drop all eight documents into the repo root
4. Open Claude Code and start Tier 1 Phase 1: NPS API pull into PostGIS

The algorithm starts with 400 points and a simple question: what's the shortest loop through America? Everything else builds from there.
