"""Smoke test for src/visualize.py — verifies the module imports and renders
HTML with no DB/OSRM dependencies. Uses use_road_geometry=False so OSRM
isn't contacted."""
from __future__ import annotations

from pathlib import Path

from src.solver import Node, SolveResult
from src.visualize import StopGeo, render_map


def test_render_minimal_loop(tmp_path: Path) -> None:
    nodes = [Node("a", "MA"), Node("b", "NH"), Node("c", "VT")]
    result = SolveResult(
        order=nodes,
        leg_costs=[3600.0, 3600.0, 3600.0],
        total_cost=10800.0,
        states_covered={"MA", "NH", "VT"},
        status="SUCCESS",
        runtime_seconds=0.5,
    )
    stop_geo = {
        "a": StopGeo(nodes[0], 42.36, -71.06, "Boston"),
        "b": StopGeo(nodes[1], 43.20, -71.54, "Concord NH"),
        "c": StopGeo(nodes[2], 44.26, -72.58, "Montpelier"),
    }
    out = tmp_path / "test.html"
    render_map(result, stop_geo, output_path=out, use_road_geometry=False)
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # Sanity: route summary, all stops, and a polyline are present.
    assert "Optitrek route" in html
    assert "Boston" in html and "Concord NH" in html and "Montpelier" in html
    assert "polyline" in html.lower() or "polyLine" in html
