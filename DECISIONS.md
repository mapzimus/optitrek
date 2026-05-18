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

## Implementation notes (not blocking decisions, but worth recording)

- **Alaska & Hawaii NPS units** will be ingested into the `pois` table for future use but **excluded from the coverage requirement and the solver candidate set** for Tier 1 (which is contiguous-US only). They are filtered out at solve time via `WHERE state NOT IN ('AK','HI')`, not deleted.
- **Park-code dedup key**: NPS `parkCode` (e.g. `yell` for Yellowstone) is stable across API responses and is the natural upsert key for the `pois` table. Stored in `tags->>'park_code'`.
