---
name: render-tour-map
description: Use when rendering an Optitrek solver result as a print-quality Albers USA map via QGIS MCP, especially comparing two route orderings over the same stops as a 3-color diff (shared / A-only / B-only edges). Triggers on requests like "compare two routes on a map", "diff these tours visually", "Albers overlay of the solver result", "render the tour as a static map", or "make me a PNG of the trip" in the optitrek repo.
---

# render-tour-map

## Overview

Renders Optitrek tour solutions as print-quality Albers USA PNGs via QGIS MCP + OSRM road polylines.

## Status — what is proven vs experimental

| Workflow | Status | Evidence |
|---|---|---|
| **Path A — two-tour diff** (shared / A-only / B-only edges) | **Proven** (validated 2026-05-24) | `output/optitrek_olson_diff.png` exists; scripts run end-to-end; subagent dry-run succeeded |
| **Path B — Tier 2 config overlay** (single tour from a YAML) | **Experimental** | Scaffolding doesn't exist yet; see "Path B open issues" below |
| **Path C — single Tier 1 tour overlay** with AK/HI insets | Done once, code not extracted | See `output/optitrek_tier1_albers.png`; QGIS code only in session transcript |

If a user asks for Path B or C, tell them explicitly: "the scripted workflow doesn't exist yet — I'll adapt manually, takes ~30 extra min." Don't pretend it's end-to-end automatic.

## When to use

- Two-tour comparison over the same stops → **Path A**
- Static PNG (not Folium HTML) of any solver result
- Print-quality output for sharing/publishing

**Don't use for:** interactive web maps (use `src/visualize.py` → Folium); maps that need AK/HI tour STOPS (solver excludes them).

## Required preconditions

1. **QGIS open with qgis-mcp plugin v0.3.x running.** Call `mcp__qgis__diagnose` first; abort if `version_match` ≠ ok.
2. **WSL Ubuntu reachable** with Docker daemon up (`docker version` works inside WSL).
3. **OSRM major-roads artifacts** at `/mnt/e/dev/optitrek/data/osrm-major/`.

## Path A — two-tour diff (the proven workflow)

Inputs: two orderings over the same N stops, each represented as a list of stop indices.

1. **Build edges JSON** following `scripts/dump_olson_vs_optitrek_edges.py`. Required shape:
   ```json
   {
     "stops":           [{"index": 0, "lat": ..., "lon": ..., "short": "...", "state": "..."}, ...],
     "olson_hours":     224.1,    "olson_miles":     13702.0,
     "optitrek_hours":  221.7,    "optitrek_miles":  13681.0,
     "edges_shared":         [[i, j], ...],
     "edges_olson_only":     [[i, j], ...],
     "edges_optitrek_only":  [[i, j], ...]
   }
   ```
   Edge computation:
   ```python
   a_edges = {frozenset({order[i], order[(i+1) % N]}) for i in range(N)}
   shared = a_edges & b_edges; only_a = a_edges - b_edges; only_b = b_edges - a_edges
   ```

2. **Fetch polylines** via `scripts/fetch_diff_polylines_wsl.sh`. Bundles OSRM start + poll + fetch + stop into one WSL invocation (required to dodge WSL2's 60s idle timeout). Produces `output/<basename>_polylines.geojson` with `properties.category` ∈ {shared, olson_only, optitrek_only}.

3. **Render in QGIS** via one atomic `mcp__qgis__execute_code` call. **Use [render_path_a_reference.py](render_path_a_reference.py) as the canonical template** — it's the exact code that produced `output/optitrek_olson_diff.png` on 2026-05-24. Edit the constants at the top of the file (EDGES_JSON, POLYLINES_GEOJSON, OUT_PNG, TITLE, labels, plus CATEGORY_*_KEY / STAT_*_KEY for non-Olson diffs), paste the rest into execute_code, run once.

### Coupling — same names across dumper, fetcher, and render template

The diff workflow has THREE places where category strings + JSON keys must agree:
1. **Your dumper** (the script that produces `edges.json`) emits keys like `edges_olson_only` and `edges_optitrek_only`, plus stats keys like `olson_hours`/`optitrek_hours`.
2. **`scripts/fetch_diff_polylines.py:53–58`** reads those exact key names (`edges_shared`, `edges_olson_only`, `edges_optitrek_only`) and tags the GeoJSON output with `category: "olson_only" | "optitrek_only" | "shared"`.
3. **The render template** reads the GeoJSON's `category` field and looks up the stats via `STAT_*_KEY` constants.

For any diff that ISN'T Olson-vs-Optitrek (e.g., capped vs uncapped, US-only vs cross-border), pick a different pair of names and use them **identically** in all three places — OR keep the `olson_only` / `optitrek_only` strings as inert labels and only change the human-facing `LABEL_A` / `LABEL_B` in the render template. The latter is the least-invasive path.

## Path B — Tier 2 config overlay (experimental)

**Path B doesn't have working scripts yet.** Before claiming "end-to-end automatic", build out these prerequisites:

### Path B open issues (must be resolved before workflow works)

1. **No Tier 2-aware tour dumper.** `scripts/dump_tier1_tour.py` hardcodes Tier 1: imports `REQUIRED_STATES` from `run_tier1`, reads the whole-US `data/matrix/pois.parquet`, forces `mode="capped"`. Need a new `scripts/dump_trip_tour.py` that takes a YAML config path, calls `src.trip.run_trip`-style machinery, and writes the tour JSON in `tier1_tour.json` shape.

2. **Polyline fetcher path is hardcoded.** `scripts/fetch_tour_polylines.py` reads `output/tier1_tour.json` and writes `output/tier1_tour_polylines.geojson` — running it for a Tier 2 trip clobbers Tier 1 outputs. Either (a) add CLI args `--tour-json` and `--geojson-out`, or (b) inject paths via `OPTITREK_TOUR_JSON` env vars before invocation.

3. **`run_trip` emits no intermediate JSON.** `src/trip.py:run_trip()` writes only `<output_dir>/<config.name>.html`. The polyline fetcher needs a JSON. Either the dumper from issue #1 calls the solver itself, or we modify `run_trip` to optionally emit JSON alongside the HTML.

4. **Filtered POI base source unstated.** For "filtered POI base + tour stops + polylines", source the POIs from `src.poi_query.fetch_pois(config)` (already does the state + category + radius filtering), NOT from the whole-US `pois.parquet`.

5. **No reusable CONUS-only render block.** When the YAML's state filter excludes AK/HI, the AK/HI nudge is skipped — bbox-of-data + 6% padding gives the extent. The Path A reference handles this correctly; a Path B render template needs to add the filtered POI base layer.

### Path B execution sketch (until scripts exist)

1. Run `python -m scripts.run_trip <yaml> --output-dir output/` from WSL venv → produces HTML.
2. Manually run the solver via Python REPL to get the tour order; serialize as JSON matching `tier1_tour.json` schema.
3. Patch `scripts/fetch_tour_polylines.py` to point at your JSON (or add env-var indirection).
4. Adapt the Path A render template ([render_path_a_reference.py](render_path_a_reference.py)) to:
   - Use ONE line layer (the tour), not three
   - Add a filtered POI base layer (small gray dots) from `fetch_pois(config)` results
   - Skip the diff legend; use a simple tour-stats subtitle instead

## Albers AK/HI inset recipe (CONUS+AK+HI maps only)

Project CRS: `EPSG:2163`. Pre-computed parameters:

| State | Centroid in EPSG:2163 | Scale | Target |
|---|---|---|---|
| AK | (-2,372,041, 2,935,069) | 0.35 | (-2,000,000, -1,700,000) |
| HI | compute live (≈ -5,613,951, -678,589) | 0.45 | (-900,000, -1,900,000) |

Apply via `shapely.affinity.scale` + `shapely.affinity.translate` (NOT `QgsGeometry.scale` — doesn't exist). The full working code for this is in the 2026-05-24 Tier 1 overlay render; **the Path A reference does NOT contain the AK/HI nudge** because Path A is CONUS-only. If you need this, the recipe is:

```python
import shapely.wkt as swkt, shapely.affinity as saff
def nudge(geom_wkt, cx, cy, scale, tx, ty):
    s = swkt.loads(geom_wkt)
    s = saff.scale(s, xfact=scale, yfact=scale, origin=(cx, cy))
    return saff.translate(s, xoff=(tx-cx), yoff=(ty-cy)).wkt
```

For each feature in AK or HI: transform to Albers via the regular CRS transform, then convert to WKT, run `nudge`, convert back via `QgsGeometry.fromWkt(...)`. HI's centroid in EPSG:2163 must be computed live — it's far west (~−5.6M m) because Hawaii sits well beyond the projection origin.

Map extent for AK/HI-inclusive layouts: `(-2,800,000, -2,400,000, 2,800,000, 1,100,000)`.

## Atomic execute_code (critical)

**ALWAYS bundle layer building + styling + layout + export into ONE `mcp__qgis__execute_code` call.** Splitting across calls causes PyQt to GC orphaned layer references; you'll get a blank map.

Specifically: do NOT manipulate the layer tree via `removeChildNode` + `clone` + `addChildNode` to reorder layers. That destroys the underlying `QgsMapLayer` objects. Set ordering only on `map_item.setLayers([...])` — legend panel order is cosmetic.

## Optional enhancements (proven 2026-05-24)

### Direction arrows on tour lines

Add `QgsMarkerLineSymbolLayer` with `filled_arrowhead` marker, `rotateSymbols=True`, placement `CentralPoint` (one arrow per unique edge) or `Interval` (arrows every ~80mm along shared edges). See `line_with_arrow()` in the reference file.

**Critical**: OSRM fetches polylines in serialized-index order (edge `[3, 17]` is fetched 3→17), NOT in route traversal order. With `rotateSymbols=True`, arrows will point in arbitrary directions unless you re-orient the polylines first. Use `directed_coords()` from the reference file — it uses each tour's position map to flip polylines as needed, with a special case for the closing depot edge (pos N → pos 1).

In our Olson vs Optitrek diff, this fix re-oriented **21 of 44 shared edges, 5 of 6 olson-only, 2 of 6 optitrek-only** — almost half were pointing wrong before the fix.

### Hillshade base layer (terrain context)

ESRI World Hillshade is free, no API key, reprojects from EPSG:3857 → Albers cleanly:

```python
hillshade_url = ("type=xyz&url=https://services.arcgisonline.com/arcgis/rest/"
                 "services/Elevation/World_Hillshade/MapServer/tile/"
                 "%7Bz%7D/%7By%7D/%7Bx%7D&zmax=15&zmin=0")
hillshade = QgsRasterLayer(hillshade_url, "hillshade", "wms")
project.addMapLayer(hillshade)
# Put hillshade at the BOTTOM of map_item.setLayers([...])
```

**Critical**: state polygons obscure hillshade unless fill is fully transparent. Use `"color": "0,0,0,0"` (outline-only) and a slightly darker outline (`"60,60,60,255"`). At alpha 110 (43% opacity) the white state fill washes out the gray hillshade entirely.

**Tile-cache warm-up**: print layout export may complete before XYZ tiles fetch. If hillshade is missing from a first-time export, warm the canvas first:
```python
canvas = iface.mapCanvas()
canvas.setDestinationCrs(map_item.crs())
canvas.setExtent(map_item.extent())
canvas.setLayers([hillshade])
canvas.refresh()
QTimer.singleShot(8000, loop.quit); loop.exec_()  # 8s wait for tiles
# Now restore full layer set and export
```

## QGIS MCP v0.3.x gotchas

| Symptom | Fix |
|---|---|
| `'LabelPlacement' expected not 'LabelPredefinedPointPosition'` | Use `QgsPalLayerSettings.Placement.OverPoint` (explicit nested), never bare `QgsPalLayerSettings.OverPoint` |
| `QgsGeometry has no attribute 'scale'` | Use shapely.affinity for affine ops |
| Map renders empty / no features | One-big-execute_code rule above |
| `QFont(family, float)` crashes | `QFont(family); f.setPointSizeF(size)` |
| TIGER state polys claim Great Lakes water | Overlay Natural Earth lakes (`D:\tmp\ne_lakes\ne_10m_lakes.shp`) |
| 1 stray PR point in `pois.parquet` | Filter `state == "PR"` explicitly (matrix_builder's `EXCLUDED_STATES` doesn't include territories) — only an issue for Path A or C, not Path B (which uses filtered POIs) |
| Map renders BLANK with only chrome visible | **CRS mismatch.** Project CRS got reset to EPSG:4326 (default) but data is in EPSG:2163 (Albers). Map item inherits project CRS unless explicitly set. Fix: ALWAYS call `project.setCrs(ALBERS)` AND `map_item.setCrs(ALBERS)` explicitly. Both. |
| Map item only shows tiny cluster of features at center, mostly blank | Same CRS mismatch — features rendering at Albers coordinates interpreted as degrees, all collapsing to a tiny area near (0,0) in whatever unit system the map is in. Fix same as above. |
| Project state lost when QGIS closes | Memory-provider layers don't persist. Save project as `.qgz` (`File → Save As`). The .qgz embeds memory layers as GeoPackages. Reload to restore everything. |

## Full-bleed composition (the v13 pattern)

The "map fills entire canvas, chrome overlays on translucent panels" composition that finally worked for the Olson diff:

1. **Map item = full page**: `map_item.attemptMove(QgsLayoutPoint(0, 0, MM))` and `attemptResize(QgsLayoutSize(420, 297, MM))`. Disable frame: `setFrameEnabled(False)`.
2. **Expand extent to page aspect**: compute route data bbox, then extend vertically (or horizontally) so the extent aspect matches the page aspect. This fills the page with map data + padding (oceans north/south).
3. **All chrome overlays sit ON the map** with translucent backgrounds:
   - `QgsLayoutItemShape` rectangles with fill `QColor(252,250,244,222)` (87% opaque cream) and a thin `QColor(110,100,85,200)` border
   - Title/subtitle/stats/legend/scale/N-arrow placed in OCEAN AREAS of the map (Pacific corner, Gulf, Atlantic offshore — where the route doesn't go)
4. **Critical CRS-pin** (see gotcha above): explicitly set `project.setCrs(ALBERS)` AND `map_item.setCrs(ALBERS)`.

## Ocean rendering recipe

For a real "land vs water" map (vs the blank-page-with-routes look):

1. Download `ne_10m_ocean.shp` from naciscdn.org (~7MB)
2. Add as layer with opaque blue fill: `"color": "175,206,227,255"`, no outline
3. Place oceans at the BOTTOM of the map item layer stack — drawn first, covered by states
4. States layer: opaque cream fill `"color": "248,244,234,255"` + warm-gray outline `"color": "140,130,110,200"`, width 0.18mm — covers ocean on land
5. Hillshade: set blend mode to multiply (`hillshade.setBlendMode(QPainter.CompositionMode_Multiply)`) and opacity 0.65 — darkens both land (terrain) and ocean (subtle bathymetry feel)
6. Layer stack (top→bottom): depot, stops, routes, cities, lakes, **hillshade (multiply)**, urban, roads, states, oceans

## Label placement gotchas (PAL labeling engine)

These came out of placing region annotations ("Midwest reroute", "Southeast reroute") on the Olson diff. They generalize to any text that has to be readable over a busy map.

**`OverPoint` clips at map edges** — `OverPoint` placement anchors the label's CENTER on the feature point and extends the text symmetrically left/right. A 50mm-wide subtitle centered on a point within ~25mm of the map item's right/left edge will have its outer half clipped off-canvas. Prefer `AroundPoint` with `quadOffset` set to a specific quadrant (e.g. `QuadrantPosition.QuadrantAbove`) for labels near edges — the anchor moves to a corner so the text grows toward the map interior. Same pattern works for labels you specifically want to sit above/below a point (depot's "START" label).

**Use `QgsTextBackgroundSettings.ShapeRectangle` for must-be-readable callouts.** White halo (text buffer) becomes hard to read over busy backgrounds like hillshade or thick lines. A solid white "chip" with a thin dark border (`stroke_width = 0.3mm`, `fill_color = (255,255,255,245)`, `size_type = SizeBuffer` with 2.0×1.5mm padding) gives crisp legibility regardless of what's underneath. Standard pattern for region annotations, scale bar labels, north arrow text.

**PAL silently suppresses overlapping labels.** If a label collides with another label OR with an obstacle layer, the labeler drops it (no error). For labels that MUST render (depot label, region callouts), set `apal.displayAll = True` and bump `apal.priority` (default 5, max 10). If those still don't render, check that the feature's geometry is inside the map item's extent — features outside the visible area are skipped without warning.

## OSRM gotchas

- Docker image: `ghcr.io/project-osrm/osrm-backend:latest` — NOT `osrm/osrm-backend` (Docker Hub mirror is 4 years stale, file-format incompatible)
- Container name + ports: `optitrek-osrm-major` on `127.0.0.1:5000`; cross-border `optitrek-osrm-na` on `127.0.0.1:5001`
- **BSOD risk**: never run osrm-routed against the full-US PBF on BRONTOSAURUS. Use only the filtered major-roads artifacts. See `brontosaurus-osrm-memory-ceiling` in user memory.
- Bring up + tear down via the wrapper scripts (`scripts/fetch_polylines_wsl.sh` or `scripts/fetch_diff_polylines_wsl.sh`). The bundled scripts keep vmmem alive throughout.

## WSL gotchas

- Always prefix WSL calls from PowerShell with `MSYS_NO_PATHCONV=1`, or Git Bash translates `/mnt/...` to `C:/Program Files/Git/mnt/...`.
- Docker Desktop is broken on BRONTOSAURUS; use WSL-native docker (`docker version 29.1.3` confirmed).
- WSL venv lives outside the repo at `/root/venvs/optitrek-wsl/`.

## Key paths

| Item | Path |
|---|---|
| TIGER state shapefile | `E:\dev\optitrek\data\boundaries\tl_2024_us_state.shp` |
| US-only POIs | `E:\dev\optitrek\data\matrix\pois.parquet` |
| OSRM US-only artifacts | `E:\dev\optitrek\data\osrm-major\` |
| OSRM US+Canada artifacts | `E:\dev\optitrek\data\osrm-major-na\` |
| Natural Earth lakes (Great Lakes overlay) | `D:\tmp\ne_lakes\ne_10m_lakes.shp` |
| Output PNGs | `C:\Users\mhowe\Downloads\` |
| Output JSON / GeoJSON | `E:\dev\optitrek\output\` |

## Output convention

- Path A: `C:\Users\mhowe\Downloads\optitrek_<a>_vs_<b>_diff.png`
- Path B (when working): `C:\Users\mhowe\Downloads\optitrek_<trip_name>_albers.png`
- A3 landscape (420×297mm), 200 DPI, ~700–800 KB typical

## Reference files

| Purpose | File |
|---|---|
| Path A QGIS render template (canonical, copy-paste into execute_code) | [render_path_a_reference.py](render_path_a_reference.py) |
| Edge-set diff between two tours | `scripts/dump_olson_vs_optitrek_edges.py` |
| Tour JSON from a Tier 1 solver result | `scripts/dump_tier1_tour.py` (Tier 1 only — see Path B issue #1) |
| OSRM polyline fetcher (single tour) | `scripts/fetch_tour_polylines.py` + `scripts/fetch_polylines_wsl.sh` |
| OSRM polyline fetcher (diff edges) | `scripts/fetch_diff_polylines.py` + `scripts/fetch_diff_polylines_wsl.sh` |
