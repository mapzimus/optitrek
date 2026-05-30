# Optitrek — Locked Decisions

This file records decisions that supplement the planning docs. Each links back to the gap in `08-OPTITREK-GAP-AUDIT.md` (or decision in `07-OPTITREK-DECISION-LOG.md`) that motivated it.

---

## Tier 1 Blockers (resolved 2026-05-18)

### D1 — Stops per state: **both capped and uncapped**
**Source:** Gap 2 in the gap audit.
**Resolution:** Run the solver twice. First with a hard cap of one stop per state (direct Olson-comparable, exactly 49 stops). Then with no cap (truly optimal). Publish both results in the blog post. Two answers are more interesting than one.

### D2 — D.C. handling: **49th coverage zone**
**Source:** Gap 3 in the gap audit.
**Resolution:** Treat Washington D.C. as a required coverage zone alongside the 48 contiguous states. Total required zones = 49. D.C. has multiple NPS units (National Mall, Lincoln Memorial, etc.), and Olson included it.

### D3 — OSRM extract: **US-only Geofabrik**
**Source:** Gap 4 in the gap audit.
**Resolution:** Build OSRM from `us-latest.osm.pbf` (Geofabrik North America → United States). Canada and Mexico roads are intentionally absent so the solver cannot leak routes through Canada (the exact problem Olson hacked around with a manual Cleveland waypoint). Border-area accuracy loss is acceptable.

### D4 — Tier 1 success criteria: **9-point checklist**
**Source:** Gap 5 in the gap audit. Verbatim list reproduced for execution reference.

Tier 1 is "done" only when **all** of the following are true:

1. The `pois` table contains all valid NPS units with coordinates and state assignments.
2. Every contiguous state + D.C. has at least one NPS unit in the database.
3. The OSRM distance matrix is computed and cached for all NPS units.
4. The solver produces an ordered route that covers all required states/zones.
5. The Folium map renders with real road geometries, numbered stop markers, and summary stats.
6. Total mileage and drive time are computed and documented.
7. A comparison to Olson's 13,699 miles / 224 hours is written up.
8. The interactive map is exported as a standalone HTML file.
9. Code is in a public GitHub repo with a README.

---

## Tier 2 Extensions (added during Tier 2 build)

### D5 — Cross-border routing: **dual-engine opt-in via `routing_network` field** (2026-05-23)
**Source:** Empirical probe of D3's accuracy cost. Same Tier 1 POI set, four representative legs, side-by-side comparison.

**Background.** D3 chose the US-only Geofabrik extract to prevent the solver from "leaking" routes through Canada (Olson's manual-Cleveland-waypoint problem). That was the right call for Tier 1, but it pays a measurable accuracy cost on the small number of legs where Canadian highways are genuinely faster than the US alternative.

**Measured penalty** (US-only vs combined US+Canada engine, same OSRM `/route` query):

| Leg | US-only | US+Canada | Saved |
|---|---|---|---|
| Detroit → Buffalo | 360 mi / 7.0 h | 256 mi / 5.2 h | **−104 mi / −1.78 h (−29%)** |
| Niagara Falls → Sault Ste M | 706 mi / 13.0 h | 537 mi / 9.7 h | **−169 mi / −3.29 h (−25%)** |
| Acadia → Campobello Is. | 109 mi / 2.8 h | 109 mi / 2.8 h | 0 (US-1 still shortest) |
| Seattle → Glacier NP | 585 mi / 11.7 h | 585 mi / 11.7 h | 0 (I-90/US-2 beats BC Hwy 3) |

Benefits are **concentrated, not diffuse**: the Great Lakes corridor (Lake Superior + Lake Huron force massive US-side detours) is the dominant case; everywhere else the US Interstate system holds up well. Proximity to the border is not the predictor — geography is.

**Resolution.** Build a *second* OSRM artifact set from a Canada + US-major merged PBF (`data/osrm-major-na/`, ~6.2 GB) and run it side-by-side on port 5001 while the US-only engine continues on 5000. `TripConfig` gains a `routing_network` field (`"us"` | `"us_canada"`, default `"us"`). `src/trip.py` resolves the right URL per config. Trip authors opt in per YAML — the default stays US-only, which **preserves the Tier 1 oracle baseline (193.0 h / 9,744 mi) exactly**.

D3 is *not* invalidated — it remains the Tier 1 default and the basis for the Olson comparison numbers. D5 is an opt-in extension for trips where cross-border accuracy matters (Great Lakes loops, Maine ↔ Detroit corridors, etc.).

**Why dual-engine instead of one combined engine.** Three reasons:
1. **Oracle preservation** — the Tier 1 oracle's ±0.5% tolerance only holds against the US-only matrix. Replacing the engine globally would force re-baselining and invalidate the existing oracle test.
2. **Per-trip opt-in is declarative** — the YAML reader sees `routing_network: us_canada` and picks the right URL with zero ambiguity. No `--allow-canada` flag juggling.
3. **Both matrices are still useful** — the US-only matrix is the "policy-compliant" baseline for the blog post and comparison work; the US+Canada matrix is the "geographically optimal" version. Keeping both available makes the trade-off visible.

**Smoke test:** `scripts/smoke_test_na_engine.sh` brings both engines up and probes the four legs above. Confirmed working 2026-05-23.

**D5 follow-up (2026-05-23): customs time, `border_crossing_minutes`.**

OSRM models the road network but is blind to US-Canada customs wait time. The smoke-test "savings" above are pure driving time. A round-trip Canada leg crosses the border twice (entering at one bridge, leaving at another), and at major crossings (Ambassador Bridge, Peace Bridge, Sault Ste M) a passenger vehicle waits 15-30 min per crossing under normal weekday traffic — so a cross-border leg costs roughly **40 minutes of customs overhead** beyond what OSRM reports.

Without this, the solver picks Canada shortcuts that lose time net of customs. With it, the solver makes the correct trade-off:

| Leg | OSRM raw savings | Net after 40 min penalty | Decision |
|---|---|---|---|
| Detroit → Buffalo | −1.78 h | −1.11 h | still wins |
| Niagara → Sault Ste M | −3.29 h | −2.62 h | still wins big |
| Acadia → Campobello | 0 | +0.67 h | demoted (loses) |
| Seattle → Glacier | 0 | +0.67 h | demoted (loses) |

**Implementation.** `TripConfig.border_crossing_minutes: int = 20` (configurable per-trip; clamped [0, 240]). `src/border_crossing.py:apply_border_penalty()` uses matrix differencing — for any leg where the US+Canada duration is meaningfully less than the US-only duration (60 s noise floor), it adds `2 × border_crossing_minutes × 60` seconds to that cell of the NA matrix BEFORE the solver sees it. No GIS work or border-shape data needed — the cost delta is the signal. Setting `border_crossing_minutes: 0` suppresses the penalty (useful for NEXUS travelers and diagnostic runs).

The penalty only applies when `routing_network: us_canada`. The `us` engine's matrix is built once for both purposes: detection baseline and (when applicable) the solver's input.

**D5 follow-up (2026-05-25): Alaska becomes conditionally reachable.**

D3 excluded AK from the candidate pool because the US-only OSRM extract can't route to it. With D5's US+Canada engine, AK is reachable via the Alaska Highway (BC + Yukon). Verified empirically: Seattle → Anchorage on the NA engine returns 2,363 mi / 51.0 h — accurate for the actual Alcan drive.

The exclusion was therefore split:
- `_ALWAYS_EXCLUDED = ["HI", "PR", "VI", "GU", "MP", "AS"]` — road-unreachable regardless of engine
- `_AK_REQUIRES_NA_ENGINE = "AK"` — included only when `routing_network='us_canada'`

End-to-end effect (`scripts/probe_ak_optin.py`):
- `routing_network='us'` → 437 candidates, 0 in AK (preserves Tier 1 oracle exactly)
- `routing_network='us_canada'` → 456 candidates, 19 in AK (Denali, Wrangell-St. Elias, Gates of the Arctic, Glacier Bay, …)

Tier 1's `matrix_builder.EXCLUDED_STATES = {"AK", "HI", "PR", "VI", "GU", "MP", "AS"}` stays unconditional because Tier 1 always runs on the US-only engine (D3). Territories (PR/VI/GU/MP/AS) were added defensively after the AK opt-in landed; only PR currently has an NPS unit, but VI/GU/MP/AS could appear in future pulls and would silently waste OSRM `/table` calls or sit as phantom nodes in the solver search space (none are in `REQUIRED_STATES` in `src/run_tier1.py`). The conditional AK opt-in logic lives only in Tier 2's `src/poi_query.py:_excluded_states_for_config()`.

Why this matters: an AK-anchored trip (e.g., `must_include` Denali, depot in Seattle) is now solver-reachable but extremely expensive (~50 h one-way drive on top of intra-AK travel). The time-budgeted solver's economy handles it correctly — an AK POI's priority value must outweigh several hundred priority-points-worth of drive time to be picked.

---

## Known Limitations (won't-fix)

### D6 — Ferry routing: won't-fix on current hardware (investigated 2026-05-24 & 2026-05-25)
**Source:** Surfaced during Tier 1 connectivity diagnostics, *after* `08-OPTITREK-GAP-AUDIT.md` was written — so there is no gap-audit entry to link back to. This D6 is the canonical record; it consolidates a two-round investigation that previously lived in a long `CLAUDE.md` agent-guidance section plus a `BUILD_STATUS.md` bullet.

**Symptom.** OSRM's graph cannot route over most car-carrying ferries — it drives *around* them, which inflates a handful of legs badly:

| Leg | Current routing | Ferry-aware engine should give |
|---|---|---|
| Seattle Ferry Term → Bainbridge | 92.1 mi / 126.6 min | ~12 mi / ~35–45 min |
| Bridgeport CT → Port Jefferson NY | 99.1 mi / 137.5 min | ~15 mi / ~75–90 min |
| Burlington VT → Port Kent NY | 96.0 mi / 123.1 min | ~15 mi / ~45–60 min |
| Bellingham WA → Whittier AK (NA :5001) | 2,334 mi / 50.6 h | varies (ferry vs Alcan) |

(Drive distances/durations captured 2026-05-24.)

**Root cause — the graph build, not the filter.** `osrm-extract` only creates an edge between two ways that share a node. The Cross Sound ferry IS present in the filtered PBF as way `w54689694` ("Bridgeport, CT - Port Jefferson, NY", `route=ferry`, `motor_vehicle=yes`), but its terminal nodes reach the road network only through `highway=service`/`residential` ways — exactly the classes the major-roads filter strips. The ferry survives the filter as a *disconnected component*: present on the map, unreachable by the router. `/route?steps=true` confirms it — a "Ferry Access Road" step (0.1 mi) for the road approach, then no ferry edge. (Washington State Ferries happen to work because WSF *relations* carry `platform`/dock ways that share nodes with surviving highways — luck, not design.) This is the **same disconnection mechanism** that strands the 79 poorly-connected POIs in `diagnostics_unreachable_pois.md`: islands and ferry terminals are graph islands in a road-only network.

**Two rounds, both dead ends:**

1. **Filter edit (2026-05-24) — partial fix, rolled back.** Adding `w/route=ferry` to `scripts/filter_pbf.sh:51` (and `build_na_osrm.sh`'s inline filter) pulled 890 ferry ways into the rebuilt PBF (vs 0 before), and Seattle→Bainbridge dropped to 8.9 mi / 39 min. But Cross Sound, Lake Champlain, and the AMHS still drove around — the filter was never the binding constraint. Rolled back per the "no partial fixes" policy. `r/route=ferry` would not help either: 193 ferry *relations* exist in source, but Cross Sound is a way (not a relation), and Lake Champlain has neither a way nor a relation in its `(-73.5,44.0)-(-73.0,45.0)` bbox (a separate OSM data gap).
2. **Broaden the filter (2026-05-25) — OOM, not viable on this hardware.** Adding `highway=service` to keep the terminal connectors blew the PBF from 548 MB to 1.7 GB (3.1×). `osrm-extract` was OOM-killed mid-run on the 24 GB WSL cap — reached "Generating edge-expanded graph representation," died silently around "Edge compression ratio" (~13 min in), and wrote no `.osrm.geometry`/`.osrm.edges`/`.osrm.fileIndex` (only the ~15 MB early sentinels survived). Reproducible; confirmed against the `brontosaurus-osrm-memory-ceiling` ceiling. Raising the `.wslconfig` cap above 24 GB risks re-triggering the 2026-05-21 BSOD — so this is hardware-blocked, not a tuning problem.

**Resolution — won't-fix on current hardware.** Tier 1 visits no island-only park, so the optimizer cost of the dead ferries is small (`diagnostics_unreachable_pois.md`), and the 193.0 h / 9,744 mi oracle is unaffected. **Do not retry either round on BRONTOSAURUS:** the filter edit is a known partial/rolled-back false start, and the broaden-filter OOMs under the 24 GB cap.

**Escape hatches (if ever revisited):**
- **Surgical terminal preservation** — pre-enumerate every `amenity=ferry_terminal` node, then preserve all ways within ~500 m via `osmium extract --polygon` *before* the major-roads filter. Keeps the PBF small; most promising on-machine option.
- **A different engine** — Valhalla or GraphHopper may snap ferry approaches without filter surgery. Significant porting work, but moves off the memory ceiling.
- **A bigger build host** — e.g. a 64 GB GCP `e2-highmem-8`: build artifacts there, transfer local. Reverses the 2026-05-21 migration; ~$0.40/hr while it exists.

---

## Implementation notes (not blocking decisions, but worth recording)

- **Alaska & Hawaii NPS units** were originally ingested but **excluded from the Tier 1 candidate set**. Hawaii stays excluded (no road). Alaska is **conditionally included** when a trip opts into `routing_network: us_canada` (see D5 follow-up above).
- **Park-code dedup key**: NPS `parkCode` (e.g. `yell` for Yellowstone) is stable across API responses and is the natural upsert key for the `pois` table. Stored in `tags->>'park_code'`.
