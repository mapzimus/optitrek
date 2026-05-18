# Optitrek — Decision Log & Planning Methodology

## Document Purpose

This document captures the full decision-making process behind Optitrek's three-tier project plan. It logs every significant choice, the reasoning behind it, alternatives considered, and why they were accepted or rejected. Written as a reference for future sessions so context isn't lost.

---

## Origin

The project was inspired by an Instagram post from @historyfeels (via @mustdoboston) about Randal Olson's 2015 "optimal road trip" project. Olson, a data scientist, calculated the shortest driving loop through all 48 contiguous US states, stopping only at National Natural Landmarks, National Historic Sites, National Parks, or National Monuments. He used Google Maps driving times between ~50 hand-picked landmarks and solved it as a traveling salesman problem (TSP) using a genetic algorithm. The final route covered 13,699 miles across ~50 stops, estimated at 224 hours of driving or 2-3 months to enjoy. The route was a loop, meaning travelers could start anywhere.

The initial prompt was: "I'd love to replicate something similar but better using 2026 GIS tools."

---

## Decision 1: Programming Language

**Decision:** Python, not R.

**Reasoning:** The project is fundamentally a routing and optimization problem. Python's ecosystem is significantly stronger here:
- OSRM and Valhalla have Python bindings and REST APIs that are trivial to call. R wrappers exist but are thinner and less maintained.
- Google OR-Tools is Python-native. No real R equivalent exists for vehicle routing / TSP at that level.
- Data wrangling for this (NPS API, GeoJSON, driving time matrices) has no meaningful advantage in R.
- If the project becomes a web app, Python + FastAPI is already a known stack (from NearestEverything).

**What R would have been better for:** Final cartographic output as a static print map (R + `tmap`). But the core pipeline — candidate stop generation, distance matrix computation, constrained TSP solving — is Python territory.

**Conclusion:** Python end to end, with Folium or Kepler.gl for interactive map output. R reserved for the African Urbanization project where it's the better fit.

---

## Decision 2: Tier Structure

**Decision:** Three tiers, built sequentially.

**Initial impulse:** Build everything at once — a fully customizable road trip planner with hotels, restaurants, Amtrak, gas prices, user accounts, and 500-stop optimization.

**Pushback:** That's a startup, not a side project. The scope expanded 50x in a single message. The tiered approach was proposed to prevent over-optimization and ensure something ships.

**Tier definitions:**
- **Tier 1:** Direct Olson replication with better tools. NPS only, ~400 stops, constrained TSP, interactive map. Self-contained, finishable in a week, produces a shareable blog post.
- **Tier 2:** Configurable solver with expanded POI database (~100k from OSM), web UI, daily driving limits. Incremental build on Tier 1 pipeline.
- **Tier 3:** Full product with user accounts, Amtrak, community gallery, presets, overnight suggestions, what-if re-solving, seasonal awareness. Gets a design doc but doesn't get built until Tier 2 is validated.

**Key principle:** Tier 1 proves the pipeline. Tier 2 proves the product concept. Tier 3 is only justified if Tier 2 gets traction. The Tier 3 spec exists as a design doc, not a commitment.

---

## Decision 3: Scope Reduction — Hotels, Restaurants, Gas

**Initial ask:** Include hotels, restaurants, gas stations, and gas prices in the database.

**Decision:** Cut all of them.

**Reasoning:**
- Hotels: requires scraping or licensing an entire lodging dataset. Constant churn.
- Restaurants: Google Places API or OSM POI extract for the entire US — millions of records, constant churn.
- Gas stations with live pricing: requires third-party API (GasBuddy has no public API).
- Each is its own data engineering project with ongoing maintenance.

**What survived:** Tourist attractions as a single broad category. NPS API (~400 units, clean, authoritative), OSM Overpass API (museums, zoos, stadiums, historic sites, etc. — ~70-100k POIs), and Amtrak GTFS (~500 stations). All free, all geocoded, no licensing issues.

**Overnight stops (Tier 3):** Instead of a lodging dataset, use OSM city/town nodes with population > 5,000 as a proxy for "has lodging." Suggest the city, not the hotel. "End of Day 3: near Springfield, IL (pop. 114,000)" — user finds their own hotel.

---

## Decision 4: Database — Neon vs. Supabase

**Decision:** Neon.

**Context:** Both were already in use — Neon for howe2math, Supabase for TappyMaps. The PostGIS experience is identical between them.

**Argument for Supabase:** Already known from TappyMaps. If Tier 3 eventually needs auth and a frontend API layer, Supabase saves a migration.

**Argument for Neon:** It's just a database. Supabase adds auth/storage/realtime that aren't needed for Tier 1 or 2. Neon is already in the howe2math stack.

**Resolution:** No strong technical advantage either way. Decision made to stick with Neon and keep Optitrek's infra separate from TappyMaps. Auth handled by Clerk in Tier 3 (already used in howe2math), not Supabase Auth.

---

## Decision 5: Database Build Strategy — Bulk vs. Iterative

**Initial impulse:** Build the full ~100k POI database first, then build all three tiers on top of it.

**Decision:** Build the database iteratively, tier by tier.

**Reasoning:**
- Tier 1 needs only ~400 NPS points. One API pull, one table, done in an afternoon.
- The OSM bulk extract (~70-100k POIs) is a bigger data engineering lift (Overpass batching, deduplication, category normalization). It's Tier 2 work.
- The schema supports both from day one — same `pois` table, just more rows later.
- "Georeference everything" before building anything is a trap that delays shipping.

**Schema design principle:** One `pois` table with `source` (nps/osm/amtrak), `category`, `state`, `geom`, and `tags` (JSONB). Extensible without schema changes. Tier 1 puts ~400 rows in. Tier 2 adds ~100k. Tier 3 adds Amtrak stations and overnight cities.

---

## Decision 6: Project Name

**Decision:** Optitrek.

**Process:** Extensive name brainstorming and availability checking.

**Rejected names and reasons:**
- **Optiroute:** Already exists as a route planning app on Google Play, plus OptiRoute (separate app) and OptimoRoute (delivery logistics SaaS). Name is well-occupied in the route optimization space.
- **RouteForge, WayfinderUS, RoadSolve:** Early suggestions. Serviceable but not memorable enough.
- **Optimap:** Too generic.
- **OptimalMile:** Nods to Olson but doesn't sound like a product.
- **Routimizer:** Final runner-up. Clean and available, but sounds like a logistics SaaS tool ("something a fleet dispatcher pays $200/month for"), not something you'd share with friends.
- **Optitrekker:** Considered to distance from OptiTrack (motion capture company). Decided unnecessary — different spelling, different domain, different audience. Added length without adding value.

**Why Optitrek won:** Short, punchy, implies optimization + trekking/travel, no existing products with the name, sounds like something shareable rather than enterprise software. Domain availability not yet checked but name is clean in app/software search results.

**20+ alternatives were generated** across two rounds of brainstorming, exploring roots like "optimal," "optimize," "trip," "trek," "leg," "route," "mile," "path," "way," and various suffix patterns (-ize, -mize, -craft, -forge, -wise, -solver). None beat Optitrek on the combination of brevity, clarity, and availability.

---

## Decision 7: Tier 1 Constraints

**Decision:** Replicate Olson's exact constraint set with better tooling.

**Constraints locked:**
- Candidate pool: all NPS units (~400) — not hand-picked
- Must visit at least one NPS unit per contiguous state (48 states)
- Minimize total driving time
- Loop route (no fixed start/end)
- Road-network distances only (OSRM, not Google Maps API)

**Key framing:** Our route will likely be *longer* in total miles than Olson's because we're solving a harder problem — covering all 48 states optimally from a pool of 400, not hand-picking 50 convenient ones. The win is that our solution is provably better *for the constraints*, not necessarily shorter. The "better" is in methodology, not mileage.

**Loop vs. point-to-point:** Olson's route was a loop. We match this for direct comparison. Point-to-point is a Tier 2 config option.

---

## Decision 8: OSRM Matrix Feasibility

**Concern flagged:** 400×400 = 160,000 distance pairs. OSRM's table service handles ~100 sources per request.

**Resolution:** Batch into chunks (e.g., 100×400). Not a blocker, just a scripting detail. Estimated ~10-20 minutes with local OSRM. Larger matrices in Tier 2 (up to 2000×2000 = 4M pairs) may take 10-30 minutes — users need progress feedback.

**OSRM hosting:** North America OSM extract is ~10GB processed. Runs in Docker on BRONTOSAURUS. Confirmed sufficient disk space.

---

## Decision 9: NPS Coverage Validation

**Concern flagged:** If any contiguous state lacks an NPS unit, the solver can't satisfy the "one stop per state" constraint.

**Resolution:** Unlikely but must be checked in Phase 1 before proceeding. If a gap exists, options are: relax the constraint for that state, or manually add a single non-NPS waypoint.

---

## Decision 10: Hosting Architecture

**Decision:** Railway for the web app, OSRM on BRONTOSAURUS behind Cloudflare Tunnel.

**Alternatives considered:**

| Option | Pros | Cons |
|---|---|---|
| Railway for everything | Simple deployment | OSRM container needs ~4-6GB RAM, expensive on Railway |
| BRONTOSAURUS for everything | Free | Only accessible when machine is on, needs port forwarding |
| Pre-compute all matrices | No live OSRM needed | 100k × 100k = 10B pairs, not feasible for full set |
| Railway app + BRONTOSAURUS OSRM | Cheap app hosting, free OSRM | BRONTOSAURUS must be on, tunnel must be active |

**Selected:** Option 4 (Railway + BRONTOSAURUS). Best cost/capability tradeoff. OSRM is the expensive component and runs free on existing hardware. The Cloudflare Tunnel dependency is acceptable — if it drops, the app returns a clear error. Revisit if the project gets real users who expect 24/7 uptime.

---

## Decision 11: Tier 2 — CLI vs. Web UI

**Initial recommendation:** CLI with YAML config files. Keeps Tier 2 focused on the hard problem (expanded data, generalized solver) without mixing in frontend work.

**Counter-argument:** The web UI is simple — just a form that feeds the same solver. FastAPI + single HTML form + embedded Folium map. Same pattern as NearestEverything, which was already built successfully.

**Decision:** Web UI, but build the solver as a standalone module first (testable without the web layer), then wrap in FastAPI as the last phase. The CLI effectively exists as the inner loop.

---

## Decision 12: Tier 2 — OSM Data Strategy

**Decision:** Bulk extract, not on-demand queries.

**Reasoning:** On-demand Overpass queries are simpler to implement but slower per request. Bulk extraction means the data is already in PostGIS when the user submits a config — the query is just a spatial filter, not an API call. The ~100k POI dataset is small (~50MB in PostGIS) and doesn't change frequently enough to justify live queries.

**Deduplication:** NPS and OSM will overlap significantly (most national parks exist in both). Spatial proximity threshold (~500m) + fuzzy name matching. NPS record wins on conflict (authoritative source).

---

## Decision 13: Tier 3 — Amtrak Integration Depth

**Decision:** Simple integration, not full multi-modal optimization.

**Three modes offered to users:**
1. **No trains** (default) — road-only routing
2. **Trains between A and B** — user specifies two cities, solver treats as fixed train leg
3. **Open to trains** — solver evaluates whether any leg would be faster by train, suggests it

**What was explicitly deferred:** Complex multi-modal optimization where the solver automatically considers every possible train+drive combination. That's a fundamentally different routing problem with much higher computational cost.

**Implementation:** Train legs modeled as fixed-cost edges in the routing graph. For "open to trains" mode, pre-filter Amtrak station pairs where train time < drive time, inject as optional edges, let OR-Tools decide.

---

## Decision 14: Tier 3 — Preset Collections Strategy

**Options considered:**

| Option | Description | Effort |
|---|---|---|
| User-created only | No curated presets. Collections exist because users made them. | Low |
| Hand-curated seed set | Manually build 10-20 collections (MLB stadiums, NFL stadiums, etc.) | Medium |
| Wikidata auto-tagging | Query Wikidata properties linked to OSM POIs to auto-detect collections | High |

**Decision:** Option 3 — Wikidata auto-tagging.

**Reasoning:** OSM has Wikidata IDs on many major venues. Wikidata has structured properties like "instance of: baseball stadium" and "tenant: Boston Red Sox" that can be queried via SPARQL. This gets ~80% accuracy automatically. The remaining 20% is fixed over time via user error reports and manual curation.

**Tradeoff acknowledged:** More data engineering upfront, messier initial results. But imperfect collections that improve are better than no collections, and hand-curating 20 lists is tedious work that doesn't scale.

---

## Decision 15: Tier 3 — User Accounts & Community

**Decision:** Clerk for auth (already used in howe2math), saved trips with public/private toggle, community gallery of shared trips, clone-and-modify flow.

**Key features:**
- User preferences: home state, favorite categories, default daily driving limit
- Save/load trips
- Public gallery: browse by recent, most cloned, category, region
- Clone button: copies someone else's trip config into your solver form for modification
- Presets as trip starters: select "All MLB Stadiums" → modify → solve

**Deferred:** Moderation tooling for public gallery. Start with manual review. Automate if volume grows.

---

## Decision 16: Seasonal Data

**Decision:** Static metadata on POIs, not live feeds.

**What's included:** Seasonal closure notes (e.g., "Going-to-the-Sun Road typically closed November through April"), best season tags, sourced from NPS API operating hours data and manual additions for well-known closures.

**What's excluded:** Live road closure feeds from FHWA or state DOTs. That's its own integration project and was deemed aspirational for Tier 3.

**User-facing:** Optional "travel month" selector in trip config. If set, solver warns (but doesn't exclude) about stops with seasonal closures during that month.

---

## Decision 17: Time Budget & Radius Constraints

**Identified late in planning:** For circular routes (same start and end), the real constraint users care about is trip length, not state coverage. "I have 2 weeks for a road trip from Boston" means "stay within a time budget," not "visit all 48 states."

**Decision:** Two solver modes.

**Mode 1 — Cover all states (Tier 1 behavior):** When `total_trip_days` is not set and `states` includes all 48, the solver minimizes total drive time to cover every state.

**Mode 2 — Time-budgeted trip (new in Tier 2):** When `total_trip_days` is set, the solver has a finite time budget (`total_trip_days × max_hours_per_day`). The objective flips from "minimize time covering all states" to "maximize stops/coverage within the time budget."

**Radius as optional additional filter:** If `max_radius_miles` is set, the candidate pool is pre-filtered to POIs within that radius of the start point before the solver runs. This is for users who know they want to stay regional (e.g., "New England only").

**Both constraints were added to the Tier 2 and Tier 3 specs** after initial drafting. The TripConfig dataclass, solver flow, and web UI form were all updated.

---

## Decision 18: Stop Prioritization When Time-Constrained

**Problem identified:** When a time budget exists (Decision 17), the solver must drop some stops. Nothing in the spec defined how it chooses which stops survive and which get cut. Without a prioritization mechanism, the solver defaults to maximizing stop count, which treats a random roadside historical marker the same as the Grand Canyon.

**Three options considered:**

| Option | Description | Pros | Cons |
|---|---|---|---|
| Equal weighting | Maximize stop count within budget | Simple, no extra UX | Dumb — ignores quality differences |
| Category-weighted scoring | User ranks categories by priority, solver maximizes total priority score | Clean UX, no external data dependency | Doesn't distinguish within categories |
| Popularity/quality signal | External metric (ratings, review counts, Wikidata importance) | More nuanced ranking | Adds data dependency, hard to normalize, introduces bias |

**Decision:** Option 2 (category-weighted scoring) for Tier 2. Option 2 + additional signals for Tier 3.

**How it works in Tier 2:**
- User selects categories AND ranks them by importance (drag-and-drop or numbered dropdown)
- Each category gets an integer priority weight (e.g., national_park = 5, museum = 3, stadium = 1)
- Solver objective in time-budgeted mode: `maximize sum(category_priority[stop.category] for stop in selected_stops)` subject to total drive time ≤ budget
- **Tiebreaker: shortest distance wins.** When two candidate routes have equal priority scores, the solver picks the shorter one. Geographic efficiency is always the secondary objective.
- Default: all categories weighted equally, which reduces to "maximize stop count" (backward-compatible with the pre-prioritization spec)

**How it extends in Tier 3:**
- Must-include stops = infinite priority (always in route)
- Preset collection membership = highest category weight automatically
- User-starred individual stops = elevated above their category default
- Community popularity signal = optional, very low weight (0.1 coefficient), avoids herding
- Category ranking = baseline, inherited from Tier 2
- Geographic efficiency = final tiebreaker, unchanged

**Why not popularity/quality signals in Tier 2:** Adding external quality data (ratings, review counts) introduces a data dependency and normalization problem. Different sources rate things differently, coverage is uneven, and it's unclear whose preferences the ratings represent. Category ranking puts the user in control of what matters to them, which is more honest than pretending an algorithm knows what's "better."

**Relationship to Olson:** Olson sidestepped prioritization entirely by hand-picking exactly 50 stops. The editorial judgment was Tracy Staedter's — she decided the Grand Canyon mattered and some other Arizona landmark didn't. Optitrek makes that judgment explicit and user-controlled rather than implicit and editorial. This is a meaningful methodological improvement: the user's preferences are an input to the optimization, not a hidden assumption baked into the candidate list.

**All five project documents were updated** to reflect this decision: Tier 1 documents its pure-geographic-efficiency approach, Tier 2 adds `category_priority` to TripConfig and the solver objective, Tier 3 extends the priority stack, the Olson comparison addresses the prioritization gap, and this log records the reasoning.

---

## Decision 19: Database Expansion Timing

**Problem:** The full POI database (~100k rows from OSM + Amtrak + overnight cities) is needed for Tier 2 but not Tier 1. Two options for when to build it:

| Option | Description | Pros | Cons |
|---|---|---|---|
| Build everything upfront | Full database before any solver work | Only set up ingestion pipeline once; data ready when needed | Delays Tier 1 by a week; front-loads easy work, defers risky work (solver) |
| Build iteratively per tier | NPS for Tier 1, expand for Tier 2 | Tier 1 ships faster; solver is validated early | Ingestion pipeline touched twice |
| Build as standalone task between tiers | NPS for Tier 1, full expansion after Tier 1 ships but before Tier 2 solver work | Tier 1 ships fast; database expansion is its own deliverable with its own validation; Tier 2 starts with complete data | Two separate build phases for data |

**Decision:** Option 3 — standalone expansion between Tier 1 and Tier 2.

**Reasoning:**
- Tier 1 needs only ~400 NPS rows. Adding OSM/Amtrak work delays shipping a complete, shareable result (blog post + map) by a week for no benefit.
- The solver is the risky, uncertain component. Validating it on a small clean dataset (NPS) before throwing 100k messy OSM rows at it is good engineering practice.
- The database expansion is a distinct deliverable — it has its own validation criteria, its own failure modes (Overpass rate limits, dedup logic, GTFS parsing), and its own definition of "done." Mixing it with solver work creates two concurrent streams of debugging.
- Once Tier 1 is shipped and the expansion is complete, Tier 2 solver generalization starts with the full dataset already in place and verified. No data surprises mid-solver-build.

**Build order is now:**
1. Tier 1 (NPS database → solver → map → blog post)
2. Database expansion (OSM + Amtrak + overnight cities — standalone spec)
3. Tier 2 (generalized solver → web UI → deploy)
4. Tier 3

**A dedicated spec document (OPTITREK-DATABASE-EXPANSION-SPEC.md) was created** covering all five phases: OSM extraction, Amtrak GTFS ingestion, overnight cities, taxonomy finalization, and final validation. Estimated 5–6 days of work.

---

## Summary of Tech Stack Decisions

| Component | Choice | Reason |
|---|---|---|
| Language | Python | Strongest ecosystem for routing + optimization |
| Database | Neon (PostGIS) | Already in stack, keeps Optitrek separate from TappyMaps |
| Routing | OSRM (self-hosted Docker) | Free, open-source, no API limits |
| Solver | Google OR-Tools | Best open-source TSP/VRP solver, Python-native |
| Visualization | Folium | Interactive web maps, standalone HTML export |
| Backend | FastAPI | Known stack, async support, lightweight |
| Frontend | Vanilla HTML/JS | Simple form, no framework overhead needed |
| Auth | Clerk | Already used in howe2math |
| App hosting | Railway | Known platform, cheap for lightweight apps |
| OSRM hosting | BRONTOSAURUS + Cloudflare Tunnel | Free, sufficient resources |
| Data sources | NPS API + OSM Overpass + Amtrak GTFS + Census TIGER + Wikidata SPARQL | All free, all geocoded, no licensing |

---

## Build Order

1. **Tier 1** — NPS data → OSRM matrix → OR-Tools solver → Folium map → blog post
2. **Database Expansion** — OSM tourist attractions + Amtrak GTFS + overnight cities + taxonomy finalization + validation (standalone spec)
3. **Tier 2** — Generalized solver → FastAPI + web UI → Railway deploy
4. **Tier 3** — Amtrak routing → Clerk auth → saved trips → community gallery → Wikidata presets → overnight stops → what-if → seasonal notes → polish

Each tier is a complete, shippable product. Tier 1 is a blog post. The database expansion is a data engineering deliverable. Tier 2 is a tool. Tier 3 is a product.

---

## Open Items for Future Sessions

- Domain registration for optitrek.com (not yet checked)
- BRONTOSAURUS Cloudflare Tunnel setup (not yet configured)
- NPS API key registration (may be needed for bulk pulls)
- Exact OR-Tools solver formulation for set cover + TSP hybrid (implementation detail for Claude Code)
- Folium vs. Kepler.gl final decision for Tier 2+ (Folium confirmed for Tier 1)
- Gallery thumbnail generation strategy (headless browser screenshots)
- Rate limiting strategy for solve endpoint in Tier 2+
