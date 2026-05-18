# Optitrek vs. Olson (2015) — Comparative Analysis

## Document Purpose

This document provides a detailed technical and methodological comparison between Randal Olson's 2015 "optimal road trip" project and the Optitrek project across all three tiers. It identifies what Olson did well, where his approach had limitations, and how Optitrek improves on each dimension. Intended as both internal reference and source material for the Tier 1 blog post.

---

## Background: What Olson Actually Did

In March 2015, data scientist Randal Olson collaborated with Discovery News writer Tracy Staedter to compute the "optimal" road trip across all 48 contiguous US states. The project was published as a blog post and went viral.

### Olson's Process

1. **Stop selection:** Tracy Staedter manually compiled a list of 50 major US landmarks — one per state (excluding Alaska/Hawaii), plus Washington D.C. and two in California. The selection criteria were editorial: a mix of "inner city exploration, must-see historical sites, and beautiful natural landscapes." This was a human curatorial process, not algorithmic.

2. **Distance computation:** Olson used the Google Maps API to compute driving distances and times between all 50 landmarks. This produced 2,450 pairwise distances (50 × 49). The computation was automated via a Python script but depended entirely on Google's proprietary routing engine.

3. **Route optimization:** Olson treated the ordered landmark list as a traveling salesman problem (TSP). He used a genetic algorithm to find a near-optimal ordering. The algorithm ran for under a minute and produced a route of 13,699 miles (22,046 km), estimated at 224 hours (9.33 days) of continuous driving.

4. **Output:** A static map image embedded in a blog post, plus six separate Google Maps links (Google Maps only supports 10 waypoints per route). The full ordered stop list was published as text.

5. **Extras:** Olson produced a second version using TripAdvisor's top-rated city in each state instead of landmarks. He also released his Python code as open source and later created versions for Europe and South America.

### Olson's Constraints

- Must stop in all 48 contiguous states
- Stops limited to National Natural Landmarks, National Historic Sites, National Parks, or National Monuments
- Travel by car only, must stay within the US
- Route is a loop (start anywhere, end where you started)

### What Olson Did NOT Do

- Did not optimize stop selection — the 50 stops were hand-picked by a journalist. The algorithm only optimized visit order.
- Did not consider alternative stops — only one candidate per state (two for California). No systematic analysis of the ~400+ NPS units nationwide.
- Did not provide optimality guarantees — genetic algorithms find "good enough" solutions but provide no bound on distance from optimal.
- Did not offer configurability — one trip, one output, no user parameters.
- Did not use open routing data — entirely dependent on Google Maps API.
- Did not produce interactive output — static image plus fragmented Google Maps links.
- Did not account for practical trip planning — no daily driving limits, no overnight considerations, no time budgeting.

---

## Dimension-by-Dimension Comparison

### 1. Stop Selection

| Dimension | Olson (2015) | Optitrek Tier 1 | Optitrek Tier 2 | Optitrek Tier 3 |
|---|---|---|---|---|
| Candidate pool size | 50 (hand-picked) | ~400 (full NPS catalog) | ~100,000 (NPS + OSM) | ~100,000+ (NPS + OSM + Amtrak) |
| Selection method | Human editorial | Algorithmic (set cover + TSP) | Algorithmic with user filters | Algorithmic with user filters + presets |
| Selection criteria | One landmark per state | Optimal coverage from full NPS pool | User-selected categories and states | User-selected + community presets + Wikidata collections |

Olson's project answered "what's the best order to visit these 50 places?" Optitrek answers "which places should you visit AND in what order?" — a fundamentally harder and more useful problem. By considering the full candidate pool, the solver can find stops that are geographically more efficient, reducing total drive time while potentially visiting more interesting or diverse destinations.

### 2. Routing Data

| Dimension | Olson (2015) | Optitrek (All Tiers) |
|---|---|---|
| Source | Google Maps API | OSRM (self-hosted) |
| Cost | Free tier with rate limits | Free, unlimited |
| Transparency | Proprietary black box | Open source, inspectable road network |
| Pairwise computations | 2,450 (50 × 49) | 160,000+ (Tier 1), millions (Tier 2+) |
| Route geometries | Not extracted (distances only) | Full road polylines for map display |
| Scalability | Limited by API rate limits and cost | Limited only by local compute |

Olson's 2,450 API calls were manageable in 2015 but don't scale. Optitrek's self-hosted OSRM handles orders of magnitude more distance computations with no external dependencies. We also extract actual route geometries for map visualization — Olson only got point-to-point distances and relied on Google Maps links for the visual route.

### 3. Solver Quality

| Dimension | Olson (2015) | Optitrek (All Tiers) |
|---|---|---|
| Algorithm | Genetic algorithm | Google OR-Tools (constrained VRP/TSP) |
| Problem type | Pure TSP (ordering only) | Set cover + TSP (selection + ordering) |
| Optimality guarantee | None ("good enough") | Near-optimal with quality bounds |
| Constraint handling | None beyond "visit all 50" | State coverage, time budget, must-include, max stops, daily limits, radius |
| Solve time | < 1 minute | Minutes (varies by problem size) |
| Solver maturity | Custom implementation | Industry-standard library used in production logistics worldwide |

Genetic algorithms were a reasonable choice in 2015 for a weekend project. But they provide no guarantee of solution quality — you don't know if you're 1% or 20% away from optimal. OR-Tools uses metaheuristics with bounds, meaning we can quantify how good our solution is. More importantly, OR-Tools natively handles the multi-constraint problem (state coverage + time budgets + must-include stops) that Olson never attempted.

### 4. Constraints and Configurability

| Constraint | Olson (2015) | Optitrek Tier 1 | Optitrek Tier 2 | Optitrek Tier 3 |
|---|---|---|---|---|
| State coverage | All 48 (hardcoded) | All 48 (hardcoded) | User selects subset or all | User selects subset or all |
| POI categories | NPS landmarks only | NPS only | User picks from 10+ categories | User picks + preset collections |
| Start/end point | Loop, start anywhere | Loop, start anywhere | Configurable start, loop or point-to-point | Configurable with home state default |
| Daily driving limit | None | None | 4-12 hours/day, user-configurable | User-configurable |
| Total trip length | Unlimited (224 hrs) | Unlimited | Time-budgeted mode (total days) | Time-budgeted mode |
| Max radius from start | N/A | N/A | Optional | Optional |
| Must-include stops | N/A | N/A | User pins specific POIs | User pins specific POIs |
| Max stops | 50 (fixed) | Solver decides | User-configurable | User-configurable |
| Multi-modal (train) | No | No | No | Amtrak integration (3 modes) |

Olson's project was a single fixed output with no user input beyond "here are 50 stops, optimize them." Every parameter was hardcoded. Optitrek progressively unlocks configurability: Tier 1 matches Olson's constraint set for direct comparison, Tier 2 opens everything up to the user, and Tier 3 adds train routing and community-driven customization.

### 5. Output and Visualization

| Dimension | Olson (2015) | Optitrek Tier 1 | Optitrek Tier 2 | Optitrek Tier 3 |
|---|---|---|---|---|
| Map type | Static image | Interactive Folium HTML | Interactive Folium HTML | Interactive Folium HTML |
| Route display | Straight lines on static map | Real road geometries from OSRM | Real road geometries | Real road geometries + train segments |
| Stop information | Text list in blog post | Clickable markers with popups | Clickable markers with popups | Markers + seasonal notes + overnight suggestions |
| Daily breakdown | None | None | Color-coded daily legs | Color-coded legs + overnight city suggestions |
| Summary stats | Total miles and hours only | Miles, hours, stop count, states | Miles, hours, stops, states, days | Full breakdown + delta comparisons |
| Shareability | Blog post link | Standalone HTML file | Hosted web app with URL | Shareable trip URLs + community gallery |
| Google Maps dependency | 6 separate links (10 waypoint limit) | None | None | None |

Olson's output was fragmented — a static map image that couldn't be interacted with, plus six separate Google Maps links because of the 10-waypoint limitation. Optitrek produces a single interactive map with real road geometries, clickable stops, and progressive detail across tiers.

### 6. Practical Trip Planning

| Dimension | Olson (2015) | Optitrek Tier 1 | Optitrek Tier 2 | Optitrek Tier 3 |
|---|---|---|---|---|
| Daily driving limits | None (224 hrs continuous) | None | User sets max hours/day | User sets max hours/day |
| Trip duration planning | "2-3 months to complete" | Not addressed | Time-budgeted mode | Time-budgeted mode |
| Overnight stops | Not addressed | Not addressed | Day labels only | City suggestions (pop > 5,000) |
| Seasonal awareness | Not addressed | Not addressed | Not addressed | Static closure/season metadata + warnings |
| What-if modifications | Start over | Start over | Re-submit form | Inline add/remove stop + re-solve with delta |
| Saved trips | None | None | None | User accounts with save/load |
| Community sharing | Blog post only | Standalone HTML | Hosted URL | Gallery + clone + presets |

Olson's project was an academic exercise — interesting to read about, not practical to actually use for planning. The 224 hours of continuous driving with no daily breakdown, no overnight planning, and no way to modify the route made it a thought experiment. Optitrek progressively adds practical planning features that make the output actionable.

---

## What Olson Got Right (And What We Should Learn)

### 1. The Framing Was Perfect

"The optimal road trip across the U.S." is an irresistible hook. It's specific, visual, and immediately understandable. The post went viral not because of the algorithm but because of the framing. Optitrek's Tier 1 blog post should follow the same playbook: clear hook, accessible explanation, visual output, and a direct comparison to Olson's results.

### 2. He Explained the Problem Accessibly

Olson explained TSP, referenced the xkcd comic, gave concrete numbers ("9.64 × 10⁵² years to compute exhaustively"), and made the algorithmic complexity tangible. The Tier 1 writeup should do the same — explain why this is hard, why our approach is better, and show the result.

### 3. He Released the Code

Open-sourcing the Python code extended the project's life and credibility. People forked it, adapted it, learned from it. Optitrek should do the same — public GitHub repo with clear documentation.

### 4. He Made a Second Version

The "popular cities" variant doubled the content's reach and showed the method was generalizable. Optitrek Tier 2's configurability naturally produces this — users can generate their own variants, and the community gallery (Tier 3) surfaces the best ones.

### 5. He Kept It Scoped

Olson didn't try to build a product. He solved one interesting problem, wrote it up well, and shipped it in a weekend. Tier 1 should follow this discipline — one week, one output, one blog post.

---

## Where Optitrek Is Categorically Different

### Olson solved a toy problem. Optitrek solves the real one.

Olson's project was TSP on 50 hand-picked points. That's an interesting algorithmic exercise but it sidesteps the actual hard question: given thousands of possible destinations, which ones should you visit? The set cover + TSP formulation in Optitrek is a fundamentally different and harder optimization problem.

### Olson produced content. Optitrek produces a tool.

Olson's output was a blog post. You can't modify the route, can't change the stops, can't adapt it to your preferences. Optitrek (Tier 2+) is a tool that produces personalized outputs. The blog post is marketing for the tool, not the deliverable itself.

### Olson's work was static. Optitrek's work compounds.

Every trip generated in Optitrek adds to the community gallery. Every user modification tests a new configuration. The system gets more useful as more people use it. Olson's project was a one-time computation with no network effects.

### Olson's prioritization was hidden. Optitrek's is explicit.

Olson's project embedded a critical judgment call that was never surfaced: Tracy Staedter decided which 50 stops mattered. Why the Alamo over Big Bend? Why Graceland over Great Smoky Mountains? Those were editorial decisions baked into the input, invisible to the algorithm and the reader. The optimization only applied to ordering — the far more consequential question of which stops deserve your limited time was answered by one person's taste.

Optitrek makes prioritization an explicit, user-controlled input. In Tier 2, users rank their selected categories by importance, and the solver maximizes a weighted priority score within the time budget. The Grand Canyon beats a random roadside marker not because an editor said so, but because the user told the solver that national parks matter more to them than historic markers. In Tier 3, the priority stack deepens further: must-include stops, preset collection membership, individual star-boosts, and an optional community popularity signal all feed into the solver's objective function. The tiebreaker is always geographic efficiency — shorter route wins when scores are equal.

This is a meaningful methodological difference. Olson's "optimal" route was optimal only for the ordering of a subjectively chosen set. Optitrek's route is optimal for both selection and ordering, with the user's own preferences as the selection criterion.

### Olson used 2015 tools. Optitrek uses 2026 tools.

Google Maps API (proprietary, rate-limited) → OSRM (open, self-hosted, unlimited). Genetic algorithm (no optimality bounds) → OR-Tools (near-optimal with quality guarantees). Static map image → interactive Folium map with real road geometries. Blog post → web application with user accounts and sharing.

---

## Expected Tier 1 Results vs. Olson

### Will Optitrek's Route Be Shorter?

Not necessarily, and that's the point. Olson's 50 stops were hand-picked to be geographically convenient — one per state, chosen partly for how well they fit a road trip. Optitrek's solver chooses from ~400 NPS candidates based purely on optimization, which may select stops that are individually more interesting but geographically less convenient.

The meaningful comparison is not raw mileage but optimality within constraints. Olson's route was near-optimal for ordering 50 fixed points. Optitrek's route is near-optimal for both selecting AND ordering stops from a pool 8× larger, with provable quality bounds.

If Optitrek's route IS shorter, that's a strong result — it means the algorithm found geographically smarter stops that Staedter's editorial process missed. If it's longer, that's still a strong result — it means the algorithm solved a harder problem (full candidate evaluation) and the extra mileage reflects the cost of systematic coverage vs. editorial cherry-picking.

### What the Blog Post Should Emphasize

The story is not "we beat Olson's mileage." The story is "we solved a harder problem with better tools, and here's what the optimal answer actually looks like when you don't hand-pick the stops." The comparison to Olson is the hook, not the conclusion.

---

## Summary Table

| Dimension | Olson (2015) | Optitrek |
|---|---|---|
| Year | 2015 | 2026 |
| Candidate pool | 50 hand-picked | 400 (Tier 1) → 100,000+ (Tier 2-3) |
| Problem solved | TSP (ordering only) | Set cover + TSP (selection + ordering) |
| Routing engine | Google Maps API | OSRM (self-hosted, open source) |
| Solver | Genetic algorithm | Google OR-Tools |
| Optimality guarantee | None | Near-optimal with bounds |
| Constraints | Fixed (all 48 states, 50 stops) | Configurable (categories, states, time budget, radius, daily limits) |
| Stop prioritization | Hidden editorial judgment (hand-picked by journalist) | Explicit user-controlled category ranking; tiebreaker = shortest distance (Tier 2+) |
| Multi-modal | Car only | Car + Amtrak (Tier 3) |
| Output | Static image + 6 Google Maps links | Interactive Folium map with real road geometries |
| User configurability | None | Full (Tier 2+) |
| Accounts and sharing | None | Clerk auth, saved trips, community gallery (Tier 3) |
| Presets | None | Wikidata auto-generated collections (Tier 3) |
| Practical planning | None (224 hrs continuous driving) | Daily limits, overnight suggestions, seasonal notes |
| Code released | Yes (open source) | Yes (open source) |
| Viral potential | Proven (went viral in 2015) | Designed for it (shareable, interactive, community-driven) |
