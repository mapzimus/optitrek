"""Expansion Phase 1 (spec 04 §1.1-1.3) — pull OSM tourist attractions via Overpass.

Also covers spec §3 (overnight cities) since it's the same extraction machinery:
two extra Overpass queries plus a population filter.

Run from the repo root:
    python -m src.osm_pull                       # everything (~700 queries, 1-2 h)
    python -m src.osm_pull --states CA,NV        # subset of states
    python -m src.osm_pull --categories museum   # subset of categories
    python -m src.osm_pull --places-only         # just overnight cities (2 queries)

Resumable: each (state, tag) batch's raw response is saved to data/osm_raw/ and
re-used on the next run unless --refresh is passed. The parsed output
(data/osm_parsed/osm_pois.jsonl) is rewritten from raw files every run, so a
partial pull produces a partial JSONL — run without filters before loading.

No DB dependency — loading/dedup/spatial-join is src/osm_load.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_TIMEOUT = 180          # server-side query timeout (seconds)
REQUEST_TIMEOUT = 240           # client-side; must exceed the server timeout
THROTTLE_SECONDS = 1.0          # spec §1.2: 1 request/second
MAX_RETRIES = 5
USER_AGENT = "optitrek/0.1 (github.com/mapzimus/optitrek)"

RAW_DIR = REPO_ROOT / "data" / "osm_raw"
PARSED_DIR = REPO_ROOT / "data" / "osm_parsed"
PARSED_PATH = PARSED_DIR / "osm_pois.jsonl"
DISCARD_LOG = RAW_DIR / "discarded.csv"

# Contiguous-US bounding box (spec §1.3). Stricter than data_pull's WIDE_US_BOX
# on purpose: OSM rows outside the lower 48 are discarded outright (the solver
# could never route to them and, unlike NPS, there's no curation value in
# keeping them around).
CONTIGUOUS_US_BOX = {"min_lat": 24.0, "max_lat": 50.0, "min_lon": -125.0, "max_lon": -66.0}

# (osm_key, osm_value, normalized_category) — spec §1.1 table.
# ORDER MATTERS: when one element matches several queries (common — e.g.
# tourism=museum + tourism=attraction on the same node), the first batch
# that yields it wins, so specific categories must precede the generic
# `landmark` (tourism=attraction) catch-all.
TAG_QUERIES: list[tuple[str, str, str]] = [
    ("tourism", "museum", "museum"),
    ("tourism", "zoo", "zoo"),
    ("tourism", "aquarium", "aquarium"),
    ("tourism", "theme_park", "theme_park"),
    ("tourism", "gallery", "gallery"),
    ("leisure", "stadium", "stadium"),
    ("leisure", "nature_reserve", "nature_reserve"),
    ("historic", "monument", "historic_marker"),
    ("historic", "memorial", "historic_marker"),
    ("historic", "castle", "historic_marker"),
    ("historic", "ruins", "historic_marker"),
    ("historic", "archaeological_site", "historic_marker"),
    ("tourism", "viewpoint", "viewpoint"),
    ("tourism", "attraction", "landmark"),
]

# Overnight cities (spec §3.2): towns need a population tag > threshold;
# cities without a population tag get the benefit of the doubt.
POPULATION_THRESHOLD = 5000

# 48 contiguous states + DC, same zone set as spatial_join.REQUIRED_ZONES
# (duplicated here so this module stays free of geopandas/sqlalchemy imports).
STATES = sorted({
    "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA",
    "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME",
    "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ",
    "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
})


class OverpassError(RuntimeError):
    """Raised when Overpass keeps failing after MAX_RETRIES attempts."""


def build_overpass_query(state: str, key: str, value: str) -> str:
    """One (state, tag) batch. The ["name"] filter pushes the no-name discard
    rule (spec §1.3) server-side — unnamed viewpoints alone would otherwise
    double the payload."""
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT}];\n"
        f'area["ISO3166-2"="US-{state}"][admin_level=4]->.a;\n'
        f'nwr["{key}"="{value}"]["name"](area.a);\n'
        "out center;\n"
    )


def build_places_query() -> str:
    """Overnight-city extraction (spec §3.1): one bbox query for both
    place=city and place=town nodes."""
    b = CONTIGUOUS_US_BOX
    bbox = f"({b['min_lat']},{b['min_lon']},{b['max_lat']},{b['max_lon']})"
    return (
        "[out:json][timeout:300];\n"
        "(\n"
        f'  node["place"="city"]["name"]{bbox};\n'
        f'  node["place"="town"]["name"]{bbox};\n'
        ");\n"
        "out body;\n"
    )


def fetch_overpass(query: str) -> dict[str, Any]:
    """POST a query with retry/backoff on 429, gateway timeouts, and network
    errors (spec §1.2)."""
    delay = 2.0
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code in (429, 502, 504):
                raise OverpassError(f"HTTP {resp.status_code} from Overpass")
            resp.raise_for_status()
            return resp.json()
        except (OverpassError, requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                print(f"   retry {attempt + 1}/{MAX_RETRIES - 1} in {delay:.0f}s ({exc})")
                time.sleep(delay)
                delay *= 2
    raise OverpassError(f"Overpass failed after {MAX_RETRIES} attempts: {last_error}")


def _parse_element(el: dict[str, Any], category: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (row_dict, None) on success or (None, discard_reason) on failure.
    Implements the spec §1.3 discard rules. Handles nodes (lat/lon on the
    element) and ways/relations (lat/lon under `center`, from `out center`)."""
    el_type = el.get("type") or ""
    el_id = el.get("id")
    if el_type not in ("node", "way", "relation") or el_id is None:
        return None, "missing_type_or_id"
    # node/way/relation are separate OSM ID spaces, so the stable external
    # identifier needs the type prefix.
    osm_id = f"{el_type}/{el_id}"

    tags = el.get("tags") or {}
    name = (tags.get("name") or "").strip()
    if not name:
        return None, "missing_name"

    if el_type == "node":
        lat_raw, lon_raw = el.get("lat"), el.get("lon")
    else:
        center = el.get("center") or {}
        lat_raw, lon_raw = center.get("lat"), center.get("lon")
    if lat_raw is None or lon_raw is None:
        return None, "missing_coords"
    try:
        lat, lon = float(lat_raw), float(lon_raw)
    except (TypeError, ValueError):
        return None, "non_numeric_coords"

    if not (CONTIGUOUS_US_BOX["min_lat"] <= lat <= CONTIGUOUS_US_BOX["max_lat"]):
        return None, f"lat_out_of_bounds:{lat}"
    if not (CONTIGUOUS_US_BOX["min_lon"] <= lon <= CONTIGUOUS_US_BOX["max_lon"]):
        return None, f"lon_out_of_bounds:{lon}"

    if tags.get("disused") == "yes":
        return None, "disused"
    if tags.get("abandoned") == "yes":
        return None, "abandoned"
    if tags.get("access") == "private":
        return None, "access_private"

    return (
        {
            "osm_id": osm_id,
            "name": name,
            "category": category,
            "lat": lat,
            "lon": lon,
            "tags": tags,  # full tag dict → JSONB; includes wikidata when present
        },
        None,
    )


def parse_population(value: Any) -> int | None:
    """OSM population values are free text ('114,394', '5 200', '~8000').
    Return an int when one can be extracted conservatively, else None."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace(" ", "").lstrip("~")
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_place(el: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Overnight-city parse: the common rules plus the population filter
    (spec §3.2). Cities pass without a population tag; towns don't."""
    row, reason = _parse_element(el, "overnight_city")
    if row is None:
        return None, reason
    tags = row["tags"]
    population = parse_population(tags.get("population"))
    if population is not None:
        if population <= POPULATION_THRESHOLD:
            return None, "population_below_threshold"
    elif tags.get("place") != "city":
        return None, "town_population_unknown"
    return row, None


def dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop repeat osm_ids, keeping the first occurrence (spec §1.3). Repeats
    happen when an element matches several tag queries or sits in a border
    area covered by two state polygons. TAG_QUERIES order makes first-wins
    keep the most specific category."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        if row["osm_id"] in seen:
            continue
        seen.add(row["osm_id"])
        unique.append(row)
    return unique, len(rows) - len(unique)


def _fetch_batch_cached(raw_path: Path, query: str, refresh: bool) -> dict[str, Any]:
    """Fetch one batch, or reuse the saved raw response (spec §1.2 says save
    raw for reproducibility; reuse also makes a 700-query pull resumable)."""
    if raw_path.exists() and raw_path.stat().st_size > 0 and not refresh:
        return json.loads(raw_path.read_text(encoding="utf-8"))
    blob = fetch_overpass(query)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(blob), encoding="utf-8")
    time.sleep(THROTTLE_SECONDS)
    return blob


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--states", help="comma-separated state codes (default: all 48+DC)")
    parser.add_argument("--categories", help="comma-separated normalized categories to pull")
    parser.add_argument("--skip-places", action="store_true", help="skip overnight cities")
    parser.add_argument("--places-only", action="store_true", help="only overnight cities")
    parser.add_argument("--refresh", action="store_true", help="re-fetch even if raw file exists")
    args = parser.parse_args(argv)

    states = sorted(s.strip().upper() for s in args.states.split(",")) if args.states else STATES
    unknown_states = set(states) - set(STATES)
    if unknown_states:
        parser.error(f"unknown/unsupported state codes: {sorted(unknown_states)}")

    tag_queries = TAG_QUERIES
    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",")}
        unknown = wanted - {cat for _, _, cat in TAG_QUERIES}
        if unknown:
            parser.error(f"unknown categories: {sorted(unknown)}")
        tag_queries = [(k, v, c) for k, v, c in TAG_QUERIES if c in wanted]

    rows: list[dict[str, Any]] = []
    discards: list[tuple[str, str, str]] = []  # (osm_id, name, reason)

    def _consume(elements: list[dict[str, Any]], parse) -> int:
        kept = 0
        for el in elements:
            row, reason = parse(el)
            if row is None:
                discards.append(
                    (
                        f"{el.get('type', '?')}/{el.get('id', '?')}",
                        ((el.get("tags") or {}).get("name") or "").strip(),
                        reason or "unknown",
                    )
                )
                continue
            rows.append(row)
            kept += 1
        return kept

    if not args.places_only:
        n_batches = len(states) * len(tag_queries)
        print(f">> Pulling {len(tag_queries)} tag queries × {len(states)} states = {n_batches} batches")
        batch = 0
        for state in states:
            for key, value, category in tag_queries:
                batch += 1
                raw_path = RAW_DIR / f"{state}_{key}-{value}.json"
                blob = _fetch_batch_cached(raw_path, build_overpass_query(state, key, value), args.refresh)
                kept = _consume(blob.get("elements", []), lambda el, c=category: _parse_element(el, c))
                print(f"   [{batch}/{n_batches}] {state} {key}={value}: {kept} kept")

    if not args.skip_places:
        print(">> Pulling overnight cities (place=city/town)")
        blob = _fetch_batch_cached(RAW_DIR / "places_city_town.json", build_places_query(), args.refresh)
        kept = _consume(blob.get("elements", []), _parse_place)
        print(f"   {kept} overnight cities kept")

    unique, n_dupes = dedupe_rows(rows)
    print(f">> Parsed {len(unique)} unique POIs ({n_dupes} cross-batch duplicates, {len(discards)} discards)")

    print(">> Per-category counts:")
    for category, n in sorted(Counter(r["category"] for r in unique).items()):
        print(f"   {category:<20}{n:>8}")

    if discards:
        DISCARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DISCARD_LOG.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["osm_id", "name", "reason"])
            w.writerows(discards)
        print(f"   discard log: {DISCARD_LOG.relative_to(REPO_ROOT)}")
        # Bounds reasons carry the coordinate; collapse for the tally.
        tally = Counter(d[2].split(":")[0] for d in discards)
        for reason, n in tally.most_common():
            print(f"     {n:>6}  {reason}")

    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    with PARSED_PATH.open("w", encoding="utf-8") as fh:
        for row in unique:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f">> Wrote {PARSED_PATH.relative_to(REPO_ROOT)}")
    print(">> Next: python -m src.osm_load")
    return 0


if __name__ == "__main__":
    sys.exit(main())
