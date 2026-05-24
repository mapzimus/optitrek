# Reference template for Path A (two-tour diff) — copy and adapt.
#
# This is the actual atomic execute_code block that produced
# C:\Users\mhowe\Downloads\optitrek_olson_diff.png on 2026-05-24.
# Paste into `mcp__qgis__execute_code` (the whole thing in ONE call).
#
# Inputs assumed to be on disk before running:
#   - output/<basename>_edges.json     (with `stops`, A/B hours+miles fields)
#   - output/<basename>_polylines.geojson  (LineString features with
#     `properties.category` ∈ {SHARED, A_ONLY, B_ONLY} matching the constants
#     below — DEFAULTS match what scripts/fetch_diff_polylines.py writes for
#     the Olson-vs-Optitrek case)
#
# Adjust the constants block below for each render. CATEGORY_A_KEY / CATEGORY_B_KEY
# must match BOTH (1) the GeoJSON property values and (2) the JSON keys
# `edges_<A>_only` / `edges_<B>_only` produced by the dumper. If you write a new
# dumper for a different diff (e.g. capped vs uncapped Tier 1), pick names and
# reuse them across the dumper, the polyline fetcher, and this template.
#
# Path A is for CONUS-only routes (no AK/HI nudge). If your stops include
# AK or HI, you need a different recipe — see Path B notes in SKILL.md.

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateReferenceSystem, QgsFeature,
    QgsGeometry, QgsField, QgsCoordinateTransform, QgsPointXY,
    QgsMarkerSymbol, QgsLineSymbol, QgsFillSymbol, QgsSingleSymbolRenderer,
    QgsPrintLayout, QgsLayoutItemMap, QgsLayoutItemLabel, QgsLayoutItemShape,
    QgsLayoutSize, QgsLayoutPoint, QgsUnitTypes, QgsLayoutExporter,
    QgsRectangle,
)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import QVariant
import json, os

# === Customize these for each render ============================
EDGES_JSON       = r"E:\dev\optitrek\output\<basename>_edges.json"
POLYLINES_GEOJSON = r"E:\dev\optitrek\output\<basename>_polylines.geojson"
STATES_SHP        = r"E:\dev\optitrek\data\boundaries\tl_2024_us_state.shp"
OUT_PNG           = r"C:\Users\mhowe\Downloads\optitrek_<basename>_diff.png"
TITLE             = "Tour A vs Tour B — Same Stops, Different Order"
LABEL_A           = "Tour A only"        # blue (#1f77b4)
LABEL_B           = "Tour B only"        # red  (#d62728)
LABEL_SHARED      = "Shared (both routes)"  # gray
# Category strings in the polylines GeoJSON's properties.category field.
# Defaults match scripts/fetch_diff_polylines.py for Olson-vs-Optitrek diff.
# For other diffs (e.g. capped vs uncapped), pick your own names and use the
# same strings in your dumper, fetcher, and here.
CATEGORY_A_KEY   = "olson_only"
CATEGORY_B_KEY   = "optitrek_only"
CATEGORY_SHARED  = "shared"
# Field names in the edges JSON for per-tour stats. Defaults match the
# Olson-vs-Optitrek dumper; rename in your dumper or here to match.
STAT_A_HOURS_KEY = "olson_hours"
STAT_A_MILES_KEY = "olson_miles"
STAT_B_HOURS_KEY = "optitrek_hours"
STAT_B_MILES_KEY = "optitrek_miles"
# ================================================================

project = QgsProject.instance()
for lyr in list(project.mapLayers().values()):
    project.removeMapLayer(lyr)

ALBERS = QgsCoordinateReferenceSystem("EPSG:2163")
LL = QgsCoordinateReferenceSystem("EPSG:4326")
project.setCrs(ALBERS)
to_albers = QgsCoordinateTransform(LL, ALBERS, project)

# 1. State background (CONUS+DC only, no AK/HI nudge)
src = QgsVectorLayer(STATES_SHP, "us_states_raw", "ogr")
xform = QgsCoordinateTransform(src.crs(), ALBERS, project)
EXCLUDED = {"AS", "GU", "MP", "VI", "PR", "AK", "HI"}
states = QgsVectorLayer("MultiPolygon?crs=EPSG:2163", "states", "memory")
states.dataProvider().addAttributes([QgsField("STUSPS", QVariant.String)])
states.updateFields()
sf = []
for f in src.getFeatures():
    if f["STUSPS"] in EXCLUDED: continue
    g = QgsGeometry(f.geometry()); g.transform(xform)
    nf = QgsFeature(states.fields()); nf.setGeometry(g)
    nf.setAttribute("STUSPS", f["STUSPS"])
    sf.append(nf)
states.dataProvider().addFeatures(sf); states.updateExtents()
project.addMapLayer(states)

# 2. Stop markers
data = json.loads(open(EDGES_JSON).read())
stops = QgsVectorLayer("Point?crs=EPSG:2163", "stops", "memory")
stops.dataProvider().addAttributes([QgsField("idx", QVariant.Int)])
stops.updateFields()
sf = []
for s in data["stops"]:
    g = QgsGeometry.fromPointXY(QgsPointXY(s["lon"], s["lat"])); g.transform(to_albers)
    nf = QgsFeature(stops.fields()); nf.setGeometry(g)
    nf.setAttribute("idx", s["index"])
    sf.append(nf)
stops.dataProvider().addFeatures(sf); stops.updateExtents()
project.addMapLayer(stops)

# 3. Three line layers (one per edge category)
fc = json.loads(open(POLYLINES_GEOJSON).read())
def make_lines(name, category):
    lyr = QgsVectorLayer("LineString?crs=EPSG:2163", name, "memory")
    lyr.dataProvider().addAttributes([QgsField("miles", QVariant.Double)])
    lyr.updateFields()
    feats = []
    for feat in fc["features"]:
        if feat["properties"]["category"] != category: continue
        pts = [QgsPointXY(lon, lat) for lon, lat in feat["geometry"]["coordinates"]]
        g = QgsGeometry.fromPolylineXY(pts); g.transform(to_albers)
        nf = QgsFeature(lyr.fields()); nf.setGeometry(g)
        nf.setAttribute("miles", feat["properties"].get("miles", 0))
        feats.append(nf)
    lyr.dataProvider().addFeatures(feats); lyr.updateExtents()
    return lyr, len(feats)

shared_layer, n_shared = make_lines(LABEL_SHARED, CATEGORY_SHARED)
a_layer, n_a            = make_lines(LABEL_A,      CATEGORY_A_KEY)
b_layer, n_b            = make_lines(LABEL_B,      CATEGORY_B_KEY)
project.addMapLayer(shared_layer); project.addMapLayer(a_layer); project.addMapLayer(b_layer)

# 4. Styling — light state fill, subdued shared lines, bold tour-only lines
states.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
    "color": "245,245,245,255", "outline_color": "170,170,170,255", "outline_width": "0.15"})))
shared_layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({
    "color": "85,85,85,230", "width": "0.5", "capstyle": "round", "joinstyle": "round"})))
a_layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({
    "color": "31,119,180,255", "width": "1.0", "capstyle": "round", "joinstyle": "round"})))
b_layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({
    "color": "214,39,40,255", "width": "1.0", "capstyle": "round", "joinstyle": "round"})))
stops.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({
    "name": "circle", "size": "2.6", "color": "33,33,33,255",
    "outline_color": "255,255,255,255", "outline_width": "0.3"})))
for l in (states, shared_layer, a_layer, b_layer, stops):
    l.triggerRepaint()

# 5. Print layout (A3 landscape) — extent = bbox of all polylines + 6% pad
xs, ys = [], []
for ln in (shared_layer, a_layer, b_layer):
    for f in ln.getFeatures():
        bb = f.geometry().boundingBox()
        xs.extend([bb.xMinimum(), bb.xMaximum()])
        ys.extend([bb.yMinimum(), bb.yMaximum()])
xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
padx = (xmax - xmin) * 0.06; pady = (ymax - ymin) * 0.06
extent = QgsRectangle(xmin - padx, ymin - pady, xmax + padx, ymax + pady)

mgr = project.layoutManager()
for old in list(mgr.printLayouts()):
    if old.name() == "diff_map": mgr.removeLayout(old)

layout = QgsPrintLayout(project)
layout.setName("diff_map"); layout.initializeDefaults()
page = layout.pageCollection().pages()[0]
page.setPageSize(QgsLayoutSize(420, 297, QgsUnitTypes.LayoutMillimeters))

mi = QgsLayoutItemMap(layout)
mi.attemptMove(QgsLayoutPoint(10, 38, QgsUnitTypes.LayoutMillimeters))
mi.attemptResize(QgsLayoutSize(400, 232, QgsUnitTypes.LayoutMillimeters))
mi.setExtent(extent); mi.setCrs(ALBERS)
mi.setLayers([stops, a_layer, b_layer, shared_layer, states])
mi.setKeepLayerSet(True); mi.setBackgroundColor(QColor(255, 255, 255))
layout.addLayoutItem(mi)

# Title — keep short, fits one line at 18pt bold
title = QgsLayoutItemLabel(layout)
title.setText(TITLE)
tfont = QFont("Arial"); tfont.setPointSizeF(18); tfont.setBold(True)
title.setFont(tfont); title.adjustSizeToText()
title.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
layout.addLayoutItem(title)

# Subtitle — per-tour stats (caller can override)
sub = QgsLayoutItemLabel(layout)
sub.setText(
    f"Tour A: {data.get(STAT_A_HOURS_KEY, 0):.1f} h / {data.get(STAT_A_MILES_KEY, 0):,.0f} mi   ·   "
    f"Tour B: {data.get(STAT_B_HOURS_KEY, 0):.1f} h / {data.get(STAT_B_MILES_KEY, 0):,.0f} mi"
)
sfont = QFont("Arial"); sfont.setPointSizeF(10); sfont.setItalic(True)
sub.setFont(sfont); sub.adjustSizeToText()
sub.attemptMove(QgsLayoutPoint(10, 23, QgsUnitTypes.LayoutMillimeters))
layout.addLayoutItem(sub)

# Legend — proper colored squares via QgsLayoutItemShape
for i, (text, color) in enumerate([
    (f"{LABEL_SHARED} ({n_shared} edges)",  QColor(85, 85, 85)),
    (f"{LABEL_A} ({n_a} edges)",            QColor(31, 119, 180)),
    (f"{LABEL_B} ({n_b} edges)",            QColor(214, 39, 40)),
]):
    y = 248 + i * 7
    shape = QgsLayoutItemShape(layout); shape.setShapeType(QgsLayoutItemShape.Rectangle)
    shape.setSymbol(QgsFillSymbol.createSimple({
        "color": f"{color.red()},{color.green()},{color.blue()},255",
        "outline_color": "0,0,0,255", "outline_width": "0.1"}))
    shape.attemptMove(QgsLayoutPoint(14, y, QgsUnitTypes.LayoutMillimeters))
    shape.attemptResize(QgsLayoutSize(5.5, 5.5, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(shape)
    lab = QgsLayoutItemLabel(layout); lab.setText(text)
    lf = QFont("Arial"); lf.setPointSizeF(10); lab.setFont(lf); lab.adjustSizeToText()
    lab.attemptMove(QgsLayoutPoint(22, y + 0.4, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(lab)

mgr.addLayout(layout)

# 6. Export
exp = QgsLayoutExporter(layout)
settings = QgsLayoutExporter.ImageExportSettings(); settings.dpi = 200
status = exp.exportToImage(OUT_PNG, settings)
print(f"Export: {'OK' if status == 0 else f'FAIL ({status})'}  ({os.path.getsize(OUT_PNG):,} bytes)")
