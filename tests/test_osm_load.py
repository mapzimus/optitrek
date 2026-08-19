"""Unit tests for src/osm_load.py: read_parsed_rows().

The rest of osm_load is SQL against live PostGIS (staging, dedup, spatial
join) and is exercised by running the module itself; the JSONL contract is
the part worth pinning without a DB.
"""
from __future__ import annotations

import json

import pytest

from src.osm_load import read_parsed_rows


def _row(**overrides) -> dict:
    base = {
        "osm_id": "node/358830550",
        "name": "Art Institute of Chicago",
        "category": "museum",
        "lat": 41.8826,
        "lon": -87.6233,
        "tags": {"tourism": "museum"},
    }
    base.update(overrides)
    return base


def test_reads_rows_and_skips_blank_lines(tmp_path):
    p = tmp_path / "osm_pois.jsonl"
    p.write_text(
        json.dumps(_row()) + "\n\n" + json.dumps(_row(osm_id="way/2", name="Field Museum")) + "\n",
        encoding="utf-8",
    )
    rows = read_parsed_rows(p)
    assert len(rows) == 2
    assert rows[1]["osm_id"] == "way/2"


def test_invalid_json_raises_with_line_number(tmp_path):
    p = tmp_path / "osm_pois.jsonl"
    p.write_text(json.dumps(_row()) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="osm_pois.jsonl:2"):
        read_parsed_rows(p)


def test_missing_key_raises(tmp_path):
    p = tmp_path / "osm_pois.jsonl"
    incomplete = _row()
    del incomplete["category"]
    p.write_text(json.dumps(incomplete) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing keys.*category"):
        read_parsed_rows(p)
