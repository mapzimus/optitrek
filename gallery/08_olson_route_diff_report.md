# Route-Difference Report — Olson 2015 vs OR-Tools (same 50 stops)

Both tours visit the **same 50 waypoints** using **Olson's exact Google-Maps
distance matrix**. Only the visit ORDER differs. This isolates the optimizer-
quality contribution: Olson used a genetic algorithm in 2015; we use Google
OR-Tools' constraint-based VRP solver in 2026.

## Headline numbers

| Metric | Olson 2015 (GA) | OR-Tools 2026 | Delta |
|---|---:|---:|---:|
| Total driving time | 224.06 h | 221.75 h | **-2.32 h** |
| Total distance | 13,702 mi | 13,681 mi | **-20 mi** |
| Stops visited | 50 | 50 | 0 |
| Edges in cycle | 50 | 50 | — |

**Net optimizer-quality win: ~1.0% on time, ~0.1% on miles.** Tiny in
relative terms, but a real and consistent improvement using the same inputs
Olson had access to in 2015.

## Cycle topology comparison

After normalizing both orderings to start at the same node and run in the
same direction:

- **44/50 edges shared** (both solvers chose this leg)
- **6 edges only in Olson's cycle** (GA picks)
- **6 edges only in OR-Tools' cycle** (replacements)

Olson spent **30.13 hours** driving the edges that OR-Tools rejected.
OR-Tools spent **27.82 hours** driving its replacement edges.
Net per-edge savings: **+2.32 hours** on
divergent edges (matches the headline ~2.3-hour gap).

## Side-by-side visit order

Both lists start at the same point and run in the same direction for fair
comparison. Differences are flagged with `→` in the OR-Tools column.

| # | Olson 2015 | OR-Tools | Match? |
|---:|---|---|:---:|
|  1 | Grand Canyon National Park | Grand Canyon National Park | ✓ |
|  2 | Bryce Canyon National Park | Bryce Canyon National Park | ✓ |
|  3 | Craters of the Moon National Monument & Pr | Craters of the Moon National Monument & Pr | ✓ |
|  4 | Yellowstone National Park | Yellowstone National Park | ✓ |
|  5 | Pikes Peak | Pikes Peak | ✓ |
|  6 | Carlsbad Caverns National Park | Carlsbad Caverns National Park | ✓ |
|  7 | The Alamo | The Alamo | ✓ |
|  8 | Chickasaw National Recreation Area | Chickasaw National Recreation Area | ✓ |
|  9 | Toltec Mounds | Toltec Mounds | ✓ |
| 10 | Graceland | Graceland | ✓ |
| 11 | Vicksburg National Military Park | Vicksburg National Military Park | ✓ |
| 12 | French Quarter | French Quarter | ✓ |
| 13 | USS Alabama | USS Alabama | ✓ |
| 14 | Cape Canaveral | Cape Canaveral | ✓ |
| 15 | Okefenokee Swamp Park | Okefenokee Swamp Park | ✓ |
| 16 | Fort Sumter National Monument | Fort Sumter National Monument | ✓ |
| 17 | Lost World Caverns | Wright Brothers National Memorial Visitor  | → |
| 18 | Wright Brothers National Memorial Visitor  | Lost World Caverns | → |
| 19 | Mount Vernon | Mount Vernon | ✓ |
| 20 | White House | White House | ✓ |
| 21 | Maryland State House | Maryland State House | ✓ |
| 22 | New Castle Historic District | New Castle Historic District | ✓ |
| 23 | Congress Hall | Congress Hall | ✓ |
| 24 | Liberty Bell | Liberty Bell | ✓ |
| 25 | Statue of Liberty | Statue of Liberty | ✓ |
| 26 | The Mark Twain House & Museum | The Mark Twain House & Museum | ✓ |
| 27 | The Breakers | The Breakers | ✓ |
| 28 | USS Constitution | USS Constitution | ✓ |
| 29 | Acadia National Park | Acadia National Park | ✓ |
| 30 | Omni Mount Washington Resort | Omni Mount Washington Resort | ✓ |
| 31 | Shelburne Farms | Shelburne Farms | ✓ |
| 32 | Olympia Entertainment | Olympia Entertainment | ✓ |
| 33 | Spring Grove Cemetery | Spring Grove Cemetery | ✓ |
| 34 | Mammoth Cave National Park | Mammoth Cave National Park | ✓ |
| 35 | West Baden Springs Hotel | West Baden Springs Hotel | ✓ |
| 36 | Lincoln Home National Historic Site Visito | Gateway Arch | → |
| 37 | Gateway Arch | Lincoln Home National Historic Site Visito | → |
| 38 | C. W. Parker Carousel Museum | Taliesin | → |
| 39 | Terrace Hill | Fort Snelling | → |
| 40 | Taliesin | Terrace Hill | → |
| 41 | Fort Snelling | C. W. Parker Carousel Museum | → |
| 42 | Ashfall Fossil Bed | Ashfall Fossil Bed | ✓ |
| 43 | Mount Rushmore National Memorial | Mount Rushmore National Memorial | ✓ |
| 44 | Fort Union Trading Post National Historic  | Fort Union Trading Post National Historic  | ✓ |
| 45 | Glacier National Park | Glacier National Park | ✓ |
| 46 | Hanford Site | Hanford Site | ✓ |
| 47 | Columbia River Gorge National Scenic Area | Columbia River Gorge National Scenic Area | ✓ |
| 48 | Cable Car Museum | Cable Car Museum | ✓ |
| 49 | San Andreas Fault | San Andreas Fault | ✓ |
| 50 | Hoover Dam | Hoover Dam | ✓ |

## Divergent sub-sequences

Where the orderings actually disagree, broken into self-contained regions
(a divergent region begins where the orders first differ and ends where
they re-sync to the same node at the same position):

### Region 1: stops 17 through 18 (2 stops affected)

Olson order in this region (drive time within: 6.08 h):
  - Lost World Caverns
  - Wright Brothers National Memorial Visitor Center

OR-Tools order in this region (drive time within: 6.08 h):
  - Wright Brothers National Memorial Visitor Center
  - Lost World Caverns

**Sub-sequence savings:** `+0.00 hours` (+0.0% within this region)

### Region 2: stops 36 through 41 (6 stops affected)

Olson order in this region (drive time within: 17.09 h):
  - Lincoln Home National Historic Site Visitor Center
  - Gateway Arch
  - C. W. Parker Carousel Museum
  - Terrace Hill
  - Taliesin
  - Fort Snelling

OR-Tools order in this region (drive time within: 16.88 h):
  - Gateway Arch
  - Lincoln Home National Historic Site Visitor Center
  - Taliesin
  - Fort Snelling
  - Terrace Hill
  - C. W. Parker Carousel Museum

**Sub-sequence savings:** `+0.21 hours` (-1.2% within this region)

## What this means

On this specific input (50 nodes, symmetric matrix, Google Maps 2015
distances), OR-Tools' constraint-based VRP solver finds a route ~2.3 hours
shorter than the genetic-algorithm result Olson published. That's roughly
1.0% improvement.

The improvement comes from a small number of local re-orderings (the
divergent regions above) — the two cycles share most of their edges. Both
solvers agree on the geographically forced parts of the tour (long
interstate stretches across the Plains and Mountain West); they disagree
mostly in dense clusters where small re-orderings can shave a few minutes
to an hour per region.

This is the expected behavior: genetic algorithms reach "near-optimal"
on TSPs of this size in seconds, but lack optimality bounds. OR-Tools'
metaheuristic search converges closer to the true optimum, and we can
quantify how much closer.

## Reproducibility

```bash
cd /e/dev/optitrek
/root/venvs/optitrek-wsl/bin/python -m scripts.olson_route_diff
```

All inputs are committed: `data/olson/waypoints-dist-dur.tsv` is Olson's
verbatim TSV from his 2015 repo; the published order is hardcoded in
`scripts/olson_route_diff.py` from his rhiever/optimal-roadtrip-usa
gh-pages major-landmarks.html. The 180-second OR-Tools budget should
produce a deterministic result on this size of problem (50 nodes).