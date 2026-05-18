# Optitrek — Gap Audit

## Document Purpose

This document catalogs 22 gaps, ambiguities, and unresolved implementation details identified during a systematic audit of the three-tier project plan. Each gap is categorized by which tier it blocks, its severity, and a proposed resolution or decision needed.

Gaps are organized into three groups: Tier 1 blockers (must resolve before building), Tier 2 pre-build items (resolve before Tier 2 starts), and Tier 3 / cross-tier items (resolve before Tier 3 or during ongoing development).

---

## Tier 1 Blockers

These must be resolved before starting Phase 1 of Tier 1. Building without answers risks wrong results, wasted work, or architectural rework.

---

### Gap 1: NPS API Response Format and Data Quality

**Problem:** The Tier 1 spec assumes all NPS units have valid lat/lon coordinates. In practice, some NPS units may lack coordinates (administrative offices, trail systems with no single representative point, units that are regions rather than points). The API response format and field availability haven't been confirmed.

**Risk:** Silent bad data — POIs with null or zero coordinates inserted into PostGIS, causing spatial join failures or phantom stops in the solver.

**Proposed resolution:** Add a validation/filtering step to Phase 1: after pulling from the NPS API, discard any unit that lacks valid coordinates (lat/lon both present, non-zero, within contiguous US bounding box). Log discarded units for manual review. Confirm NPS API field names and response structure in a test pull before writing the full ingestion script.

**Decision needed:** None — this is an implementation detail. Just add the validation step.

---

### Gap 2: Multiple Stops Per State — Cap or Uncapped?

**Problem:** The Tier 1 spec says "at least one NPS unit per contiguous state" but doesn't say "at most one." The solver could pick 5 stops in California and 1 in Wyoming if that minimizes total drive time. This produces a different kind of trip than Olson's (exactly one per state, except California which had two).

**Risk:** The Tier 1 result isn't directly comparable to Olson's if the solver uses a different number of total stops. The blog post comparison becomes muddier.

**Options:**

| Option | Behavior | Pros | Cons |
|---|---|---|---|
| Uncapped | Solver picks as many stops as it wants per state | Truly optimal solution; may find that clustering stops in large states reduces backtracking | Total stop count unpredictable; harder to compare to Olson's 50 |
| Capped at 1 per state | Exactly 48 stops (one per state) | Clean comparison to Olson; predictable output | May miss the globally optimal route; artificially constrains the solver |
| Capped at 1 per state + optional extras | One required per state, solver can add more if they reduce total drive time | Best of both worlds | More complex solver formulation |

**Proposed resolution:** Run it both ways. First solve with exactly one per state (direct Olson comparison), then solve uncapped and compare. The blog post can present both: "here's the Olson-equivalent result, and here's what happens when we let the algorithm pick freely." Two results are more interesting than one.

**Decision needed:** Yes — confirm this approach or pick one mode.

---

### Gap 3: Washington D.C. Handling

**Problem:** Olson included D.C. as a stop (the White House). Is D.C. a "state" in our 48-state constraint set? The contiguous US has 48 states. D.C. is not a state. If we require 48 states + D.C., that's 49 coverage zones. If we fold D.C. into Maryland or Virginia, we might skip it entirely.

**Risk:** Minor but affects constraint count and comparability to Olson.

**Proposed resolution:** Treat D.C. as a 49th coverage zone. It's small, it's got multiple NPS units (National Mall, etc.), and Olson included it. The constraint becomes "at least one stop in each of the 48 contiguous states plus D.C."

**Decision needed:** Yes — confirm D.C. as 49th zone or fold into Maryland/Virginia.

---

### Gap 4: Canada Routing Leakage

**Problem:** The Tier 1 spec uses the North America OSM extract for OSRM. This means the road network includes Canada. Routes between border states (e.g., Vermont to Michigan, Maine to New York) may route through Canada if that's shorter. Olson explicitly noted this problem and added a manual Cleveland waypoint to force the route to stay in the US.

**Risk:** The "optimal US road trip" inadvertently leaves the US. This is both a constraint violation and a PR problem for the blog post.

**Options:**

| Option | Approach | Pros | Cons |
|---|---|---|---|
| US-only OSM extract | Use a US-only `.osm.pbf` instead of North America | Guarantees US-only routing; simpler | Loses border-area road accuracy; may produce unrealistic routes near borders |
| North America extract + post-validation | Use full North America extract, then check each route segment for Canada incursion | Best routing accuracy | Complex to implement; need a US boundary polygon and segment intersection check |
| North America extract + buffer stops | Like Olson's Cleveland trick — add mandatory waypoints near border crossings | Simple, proven approach | Manual, fragile, doesn't scale |

**Proposed resolution:** Use the US-only Geofabrik extract (`us-latest.osm.pbf`). This is the cleanest solution. OSRM physically cannot route through Canada if Canada's roads aren't in the network. Border-area accuracy loss is minimal — the road network near the border is well-mapped on both sides independently.

**Decision needed:** Yes — confirm US-only extract or choose another approach.

---

### Gap 5: No Success Criteria Defined

**Problem:** The Tier 1 spec defines phases and deliverables but doesn't define what "done" looks like. How do we know the build succeeded?

**Risk:** Scope creep or premature polish. Without criteria, it's tempting to keep tweaking the solver, improving the map, or refining the blog post indefinitely.

**Proposed resolution:** Tier 1 is done when all of the following are true:

1. The `pois` table contains all valid NPS units with coordinates and state assignments
2. Every contiguous state (+ D.C. if confirmed) has at least one NPS unit in the database
3. The OSRM distance matrix is computed and cached for all NPS units
4. The solver produces an ordered route that covers all required states/zones
5. The Folium map renders with real road geometries, numbered stop markers, and summary stats
6. Total mileage and drive time are computed and documented
7. A comparison to Olson's 13,699 miles / 224 hours is written up
8. The interactive map is exported as a standalone HTML file
9. Code is in a public GitHub repo with a README

**Decision needed:** Yes — confirm these criteria or modify.

---

### Gap 20 (Partial — Tier 1 Scope): No Testing Strategy

**Problem:** The solver is the most critical component. If it produces a wrong result (misses a state, picks an impossible route, miscalculates distance), the entire output is invalid. No test cases are defined anywhere in the spec.

**Risk:** Shipping a blog post with a provably wrong result. Embarrassing and undermines the "better than Olson" narrative.

**Proposed resolution for Tier 1:** Create a small test case before running the full solve. Example: pick 5-8 NPS units in the Northeast, compute the matrix, solve, and manually verify the result makes sense (route doesn't backtrack absurdly, total distance is plausible, all states covered). This takes an hour and catches solver formulation bugs before the full 400-point run.

Also: after the full solve, validate programmatically that every required state appears in the result. This is a one-line check that should never be skipped.

**Decision needed:** None — just do it. Adding to Phase 3 as a validation step.

---

## Tier 2 Pre-Build Items

These should be resolved before starting Tier 2 but do not block Tier 1.

---

### Gap 6: OSM Data Quality Filtering Rules

**Problem:** The Tier 2 spec says "spot-check" after the OSM bulk extract but doesn't define minimum criteria for a POI to be included in the database.

**Risk:** Junk data in the solver — nameless nodes, mislocated points, permanently closed venues. These waste solver capacity and produce bad recommendations.

**Proposed resolution:** Define concrete inclusion criteria before the OSM pull:

- Must have a `name` tag (discard unnamed POIs)
- Coordinates must fall within contiguous US bounding box (24°N–50°N, 125°W–66°W)
- Must match one of the target OSM tag categories
- Discard POIs tagged `disused=yes`, `abandoned=yes`, or `access=private`
- Log discarded POIs with reasons for manual review

**Decision needed:** None — these are sensible defaults. Refine during implementation if needed.

---

### Gap 7: Category Taxonomy Collisions (NPS vs. OSM)

**Problem:** NPS "National Monument" and OSM `historic=monument` mean different things. NPS "National Historic Site" and OSM `historic=*` overlap but aren't identical. A user selecting "monuments" could reasonably expect either or both.

**Risk:** Confusing UX — user selects a category and gets unexpected results, or misses expected results.

**Proposed resolution:** The normalized taxonomy should use distinct, user-friendly labels that don't collide:

- `nps_park` (NPS National Parks)
- `nps_monument` (NPS National Monuments)
- `nps_historic` (NPS National Historic Sites)
- `nps_other` (NPS battlefields, seashores, memorials, etc.)
- `museum` (OSM tourism=museum)
- `zoo` (OSM tourism=zoo)
- `historic_marker` (OSM historic=monument, memorial, etc.)
- etc.

The prefix distinguishes NPS from OSM sources. Users see clean labels ("National Parks," "Museums," "Historic Markers") and the mapping is unambiguous.

**Decision needed:** Yes — confirm this prefix approach or choose a different taxonomy strategy.

---

### Gap 8: Solver Timeout Behavior

**Problem:** No maximum solve time is defined. For large candidate sets (1,000+ nodes), OR-Tools could run for a very long time searching for a better solution. The user stares at a spinner indefinitely.

**Risk:** Bad UX, potential server resource exhaustion, users abandoning the app.

**Proposed resolution:** Set a hard time limit on the solver (e.g., 5 minutes). OR-Tools supports this natively via `time_limit` parameter. If the limit is hit, return the best solution found so far with a note: "This is the best route found in the time allowed. Results may improve with a smaller candidate set or fewer constraints." The solution quality metric (gap from optimal bound) should be included if available.

**Decision needed:** None — 5 minutes is a reasonable default. Can be tuned during implementation.

---

### Gap 9: Matrix Computation Time for Large Candidate Sets

**Problem:** 2,000 × 2,000 = 4 million OSRM table lookups. Estimated 10-30 minutes. This is a long wait for a web form submission.

**Risk:** Users abandon the app. Server resources tied up on single requests.

**Proposed resolution:** Two-pronged approach:

1. **Pre-filter aggressively.** Before computing the matrix, reduce the candidate set. Heuristic: for each state, keep only the top N POIs closest to the geographic centroid of the selected states (or closest to the start point). N = 10-20 per state keeps the candidate set manageable (~500-1000) while still giving the solver real choices.

2. **Progress feedback.** The async job endpoint should report progress: "Computing distances: 45% complete" so the user knows it's working.

**Decision needed:** Yes — confirm the pre-filtering heuristic or choose a different approach to managing candidate set size.

---

### Gap 10: OSRM Unreachable Pairs

**Problem:** Some POIs may be on islands, in pedestrian-only areas, or otherwise not routable by car via OSRM. The OSRM table service returns `null` or very large values for these pairs.

**Risk:** Solver crashes, produces impossible routes, or silently ignores unreachable stops.

**Proposed resolution:** After computing the distance matrix, scan for null or extreme values (e.g., drive time > 48 hours for a single leg). Drop any POI that is unreachable from more than 10% of the other candidates. Log dropped POIs. For remaining pairs with null values, set a very high penalty cost so the solver avoids them but doesn't crash.

**Decision needed:** None — this is defensive implementation. Define thresholds during testing.

---

### Gap 11: Infeasible Time-Budgeted Requests

**Problem:** A user could select all 48 states + 5 days at 6 hours/day = 30 driving hours. It's physically impossible to cover all 48 states in 30 hours. The spec says "at least one stop per selected state where feasible" but doesn't define what happens when it's infeasible.

**Risk:** Solver fails with no result, or produces a weird partial route with no explanation.

**Proposed resolution:** Three-step approach:

1. **Pre-solve feasibility check.** Before running the full solver, compute a rough lower bound: minimum spanning tree of state centroids × OSRM average speed. If the time budget is below this lower bound, warn the user: "This time budget can't cover all selected states. The solver will maximize coverage within your budget."

2. **Graceful degradation.** In time-budgeted mode, state coverage is a soft constraint (maximize states covered) rather than a hard constraint (must cover all). The solver maximizes priority score first, state coverage second, within the time budget.

3. **Result transparency.** The output should clearly state: "Covered 23 of 48 selected states in 5 days. Add more days or reduce states to improve coverage."

**Decision needed:** Yes — confirm soft constraint approach for time-budgeted mode.

---

### Gap 12: Result Persistence and Storage

**Problem:** When a user solves a trip in Tier 2, the result is a Folium HTML map. Where is it stored? For how long? The spec mentions shareable URLs but doesn't define storage.

**Risk:** Railway storage fills up with cached results. Or results disappear and shared URLs break.

**Proposed resolution for Tier 2:** Store results in Neon as JSON (the stop list, leg distances, and config) — not the rendered HTML. Re-render the Folium map on demand from the stored JSON. This is cheap (JSON is small), persistent (database-backed), and avoids storing large HTML blobs. For Tier 2, results can be ephemeral (deleted after 7 days for unauthenticated users). Tier 3 saved trips persist indefinitely for authenticated users.

**Decision needed:** Yes — confirm ephemeral results for Tier 2 (7-day TTL for unauthenticated) and persistent for Tier 3 (authenticated users).

---

## Tier 3 Pre-Build Items

These should be resolved before starting Tier 3 but do not block Tier 1 or 2.

---

### Gap 13: Amtrak Schedule Simplification

**Problem:** Amtrak schedules are irregular — some routes run daily, some 3x/week, with varying travel times by day. The spec says "use median travel time" but this loses day-of-week information. The solver could pick a train leg on a Tuesday when that train only runs Monday/Wednesday/Friday.

**Risk:** Route suggests a train leg that doesn't actually exist on the planned travel day.

**Proposed resolution:** Store both median travel time AND frequency in the `amtrak_legs` table (the schema already has a `frequency` field). The solver uses median travel time for optimization. The output includes a disclaimer per train leg: "This train runs daily" or "This train runs Mon/Wed/Fri — verify schedule before booking." Day-of-week validation is out of scope for Tier 3; it would require integrating the trip's day-by-day calendar with GTFS schedules, which is a significant complexity increase.

**Decision needed:** None — disclaimer approach is sufficient for Tier 3.

---

### Gap 14: Collection Accuracy and Launch Process

**Problem:** Wikidata auto-tagging is estimated at ~80% accuracy. Launching collections with 20% error rate (wrong stadiums, missing venues, miscategorized POIs) undermines trust.

**Risk:** Users see obviously wrong results in curated-looking preset collections and lose confidence in the tool.

**Proposed resolution:** Two-tier collection system:

- **Verified collections** (top 10-20): auto-generated then manually reviewed before launch. Displayed with a "verified" badge. These are the seed content for the gallery.
- **Auto-generated collections** (everything else): displayed with an "auto-generated" label and a "report error" button. No verification required before launch. Errors get fixed over time via user reports.

This gives you high-quality seed content without requiring manual review of every collection.

**Decision needed:** Yes — confirm two-tier approach or single-tier with disclaimers.

---

### Gap 15: Gallery Thumbnail Generation

**Problem:** The spec calls for mini map thumbnails on gallery trip cards. Generating these via headless browser screenshots of Folium maps (Selenium/Playwright) is heavy infrastructure for Railway.

**Risk:** Slow gallery page loads, high server resource usage, deployment complexity.

**Proposed resolution:** Use a lightweight static map image instead of screenshotting Folium. Options:

- **Matplotlib + Cartopy:** Render a simple US outline with route polylines. Fast, lightweight, no browser needed. Less pretty but functional.
- **Static map tile API:** Use a free static map API (e.g., Mapbox static images, OpenStreetMap static maps) to generate thumbnails. Adds an external dependency but produces attractive thumbnails.
- **Defer entirely:** Launch gallery without thumbnails — just show text metadata (title, stops, miles, days) on cards. Add thumbnails later if the gallery gets traction.

**Proposed pick:** Matplotlib + Cartopy. No external dependency, runs on the server, good enough for a thumbnail.

**Decision needed:** Yes — confirm approach.

---

### Gap 16: Clone Count Gaming

**Problem:** Gallery sorting includes "most cloned." Users could clone their own trips repeatedly to boost ranking.

**Risk:** Minor gaming that pollutes gallery rankings.

**Proposed resolution:** Count unique user clones only. A user cloning the same trip multiple times counts as 1 clone. Trivial to implement with a `UNIQUE(trip_id, user_id)` constraint on a `clones` table. Unauthenticated users can't clone (cloning requires an account), which also solves the anonymous spam vector.

**Decision needed:** None — unique user clones is the obvious answer.

---

### Gap 17: What-If Re-Solve Latency

**Problem:** The what-if feature ("add a stop, see the impact") triggers a full re-solve. For large candidate sets, this takes minutes. The UX implies fast inline feedback.

**Risk:** UX promise doesn't match reality. Users expect instant feedback from an "add stop" button but get a multi-minute wait.

**Proposed resolution:** Two approaches depending on trip size:

- **Small trips (< 50 stops):** Re-solve is fast (seconds). Inline feedback works as designed.
- **Large trips (50+ stops):** Show a "re-optimizing..." state with the old map still visible. Display estimated wait time. When done, show the new map with a delta summary. Don't pretend it's instant.

Also consider: for add/remove of a single stop, a heuristic insertion (find the cheapest position to insert the new stop without re-solving the full route) could give instant approximate feedback, with a "fully re-optimize" button for exact results. This is an implementation optimization, not a spec change.

**Decision needed:** None — the two-tier latency approach is reasonable. Heuristic insertion is a nice-to-have optimization.

---

### Gap 18: Rate Limiting

**Problem:** The solve endpoint is computationally expensive (OSRM matrix + OR-Tools). No rate limiting is specified. One user (or bot) could submit many solves and exhaust server resources.

**Risk:** Denial of service (accidental or intentional), degraded experience for all users.

**Proposed resolution:**

- **Tier 2 (no auth):** Rate limit by IP address. 3 solves per hour per IP. Display remaining quota on the form.
- **Tier 3 (with auth):** Rate limit by user account. 10 solves per hour for authenticated users. Higher limits for any future premium tier.
- **Implementation:** FastAPI middleware with Redis or in-memory counter. Lightweight.

**Decision needed:** Yes — confirm rate limits or adjust numbers.

---

### Gap 19: Monitoring and Logging

**Problem:** No monitoring, alerting, or logging is specified anywhere. When OSRM goes down, the Cloudflare Tunnel drops, Neon is unreachable, or the solver produces garbage, there's no way to know until a user complains.

**Risk:** Silent failures, stale OSRM data, undetected bugs.

**Proposed resolution:** Minimum viable monitoring:

- **Health check endpoint** (already in Tier 2 spec): `/health` returns status of OSRM, Neon, and solver availability
- **Structured logging:** Log every solve request (config hash, candidate set size, solve time, result quality, errors) to stdout. Railway captures stdout logs.
- **Uptime check:** Use a free service (UptimeRobot, Freshping) to ping `/health` every 5 minutes and email on failure.
- **No custom dashboards, no Datadog, no PagerDuty.** Keep it simple.

**Decision needed:** None — this is baseline operational hygiene. Implement during Tier 2 Phase 6 (deploy).

---

## Cross-Tier Items

These affect multiple tiers and should be addressed as ongoing concerns rather than blocking any single tier.

---

### Gap 20 (Full Scope): Testing Strategy

**Problem:** No testing strategy is defined for any tier. The solver, data pipeline, and API endpoints are all untested by spec.

**Risk:** Bugs in the solver produce wrong routes. Data pipeline ingests garbage. API returns errors users never see explanations for.

**Proposed resolution by tier:**

**Tier 1:**
- Manual test case: 5-8 NPS units in Northeast, solve, verify by hand
- Programmatic validation: confirm every required state appears in the result
- Sanity check: total mileage is between 10,000 and 20,000 miles (if outside this range, something is wrong)

**Tier 2:**
- Unit tests for solver with known small inputs and expected outputs
- Integration test: submit a config via API, verify result contains all required states and respects time budget
- Data quality tests: count POIs per state, verify no null geometries, verify deduplication worked

**Tier 3:**
- End-to-end test: signup → configure → solve → save → share → clone → modify → re-solve
- Amtrak routing test: verify train legs appear only when they save time
- Collection accuracy test: spot-check auto-generated collections against ground truth

**Decision needed:** None — just build tests alongside features, not after.

---

### Gap 21: POI Database Versioning

**Problem:** When OSM data is re-pulled (things open and close, coordinates get corrected), POI IDs may change. Saved trips in Tier 3 reference POI IDs. If a POI is deleted or moved in a re-pull, saved trips break.

**Risk:** Shared trip URLs show missing stops, solver re-runs fail on stale POI IDs, user trust erodes.

**Proposed resolution:** Two strategies:

- **Stable IDs:** Use OSM node IDs (which are stable across Overpass pulls) rather than auto-increment serial IDs for OSM-sourced POIs. NPS units use their NPS API park codes. This makes `pois.id` a stable external identifier rather than a database-internal one.

- **Trip snapshots:** When a trip is saved (Tier 3), snapshot the relevant POI data (name, coordinates, category) into the trip's `result` JSONB. The saved trip is self-contained and doesn't break if the POI table changes. The POI ID is kept for linking back to the current database, but the snapshot is the authoritative record for that trip.

**Proposed pick:** Both. Stable external IDs as the primary key strategy, plus trip snapshots for saved trips. Belt and suspenders.

**Decision needed:** Yes — confirm stable external IDs + snapshot approach.

---

### Gap 22: Mobile Consideration

**Problem:** The Folium map is technically mobile-responsive, but the Tier 2/3 web form (multiple sliders, dropdowns, drag-and-drop category ranking, state multi-select) could be painful on a phone screen.

**Risk:** Poor mobile UX limits adoption. Road trips are planned on phones as much as desktops.

**Proposed resolution:** Don't try to make the full config form mobile-friendly in Tier 2. Focus on desktop. For Tier 3, consider a simplified mobile flow:

- Step-by-step wizard instead of a single long form
- Preset-first UX on mobile: "Pick a preset → modify → solve" rather than building from scratch
- Map results are already mobile-friendly (Folium handles this)
- Gallery browsing and trip viewing are mobile-friendly by default (read-only content)

Full mobile optimization is a Tier 3+ concern. Don't let it slow down Tier 2.

**Decision needed:** Yes — confirm desktop-first for Tier 2, mobile consideration deferred to Tier 3.

---

## Summary: Decisions Needed Before Tier 1

| Gap | Question | Proposed Answer |
|---|---|---|
| 2 | Multiple stops per state: capped or uncapped? | Run both ways, present both results |
| 3 | Is D.C. a coverage zone? | Yes, 49th zone (48 states + D.C.) |
| 4 | US-only or North America OSRM extract? | US-only extract |
| 5 | What are the Tier 1 success criteria? | 9-point checklist defined above |

## Summary: Decisions Needed Before Tier 2

| Gap | Question | Proposed Answer |
|---|---|---|
| 7 | How to handle NPS/OSM category collisions? | Prefix-based taxonomy (nps_park, museum, historic_marker) |
| 9 | How to manage large candidate sets? | Pre-filter to top N per state by proximity heuristic |
| 11 | What happens when time budget is infeasible? | Soft constraint — maximize coverage, warn user |
| 12 | How long do results persist? | 7-day TTL for unauthenticated, permanent for authenticated (Tier 3) |
| 18 | Rate limits? | 3/hour by IP (Tier 2), 10/hour by user (Tier 3) |

## Summary: Decisions Needed Before Tier 3

| Gap | Question | Proposed Answer |
|---|---|---|
| 14 | Collection launch process? | Two-tier: verified (top 20) + auto-generated with disclaimers |
| 15 | Gallery thumbnail approach? | Matplotlib + Cartopy static images |
| 21 | POI database versioning? | Stable external IDs + trip snapshots |
| 22 | Mobile support? | Desktop-first for Tier 2, mobile wizard for Tier 3 |
