"""Random walk through every town in the lower 48.

The anti-optimizer. Take every Census place in the contiguous US + DC
(~31k), shuffle them with a fixed seed, and drive from the center of town 1
to town 2 to town 3 ... to the end. No solver, no matrix — just N-1
sequential OSRM /route calls. The answer is the total (tens of millions of
miles, decades of driving) and the pile of polylines.

Why this fits the current OSRM (major-roads-only graph, see DECISIONS.md D6):
a random pair of US towns is ~1,000 road miles apart, almost all of it on
roads the filter keeps. The last-mile snap error is noise at that scale.
Island towns with no road connection are dropped by a reachability pre-pass.

Usage (from WSL with OSRM up on :5000, or via scripts/run_random_walk.sh):

    python -m src.random_walk --seed 42
    python -m src.random_walk --seed 42 --limit 200      # smoke test
    python -m src.random_walk --seed 42                  # again → resumes

Outputs land in data/random_walk/seed<seed>/:
    towns.csv      the permutation (seq, geoid, name, state, lat, lon, reachable)
    legs.jsonl     one record per attempted leg (append-only; resume reads it)
    run.json       manifest (seed, n_towns, anchor) — resume refuses a mismatch
    summary.json   totals + leg stats, rewritten at the end of every run
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import statistics
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2020_Gazetteer/2020_Gaz_place_national.zip"
)
GAZETTEER_TXT = "2020_Gaz_place_national.txt"
DEFAULT_CENSUS_DIR = Path("data/census")
DEFAULT_OUT_DIR = Path("data/random_walk")

# Same road-unreachable set Tier 1 uses (src/matrix_builder.EXCLUDED_STATES).
# Imported by value rather than by module so this file has no DB import chain.
EXCLUDED_STATES = frozenset({"AK", "HI", "PR", "VI", "GU", "MP", "AS"})

# Geographic center of the contiguous US (near Lebanon, KS). The town nearest
# this point is the reachability anchor — if a town can't route to it, it's
# not in the main road component and is dropped from the walk.
CONUS_CENTER = (39.50, -98.35)

DEFAULT_OSRM_URL = "http://127.0.0.1:5000"
DEFAULT_TIMEOUT = 60
DEFAULT_TABLE_BLOCK = 500      # sources per /table call (osrm-routed runs with --max-table-size 8000)
ROUTE_RETRIES = 3
MAX_CONSECUTIVE_FAILURES = 5   # from one origin → something is wrong, abort

METERS_PER_MILE = 1609.344
OLSON_MILES = 13_699.0         # 02-OPTITREK-OLSON-COMPARISON.md
TIER1_MILES = 9_744.0          # BUILD_STATUS.md oracle


@dataclass(frozen=True)
class Town:
    geoid: str
    name: str
    state: str
    lat: float
    lon: float


# ---------------------------------------------------------------------------
# Town list
# ---------------------------------------------------------------------------

def download_gazetteer(census_dir: Path = DEFAULT_CENSUS_DIR) -> Path:
    """Fetch the Census 2020 Gazetteer places file (1.2 MB zip) once."""
    census_dir.mkdir(parents=True, exist_ok=True)
    txt_path = census_dir / GAZETTEER_TXT
    if txt_path.exists():
        return txt_path
    print(f"[random_walk] downloading {GAZETTEER_URL}", flush=True)
    resp = requests.get(GAZETTEER_URL, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        txt_path.write_bytes(zf.read(GAZETTEER_TXT))
    return txt_path


def parse_gazetteer(text: str, excluded: frozenset[str] = EXCLUDED_STATES) -> list[Town]:
    """Parse the tab-separated Gazetteer. The header AND every row carry
    trailing whitespace on the last column (INTPTLONG) — strip everything.
    Rows in `excluded` states are dropped. Order is file order (by GEOID)."""
    lines = text.splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    idx = {name: i for i, name in enumerate(header)}
    towns: list[Town] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split("\t")]
        state = cells[idx["USPS"]]
        if state in excluded:
            continue
        towns.append(Town(
            geoid=cells[idx["GEOID"]],
            name=cells[idx["NAME"]],
            state=state,
            lat=float(cells[idx["INTPTLAT"]]),
            lon=float(cells[idx["INTPTLONG"]]),
        ))
    return towns


def load_gazetteer(census_dir: Path = DEFAULT_CENSUS_DIR) -> list[Town]:
    path = download_gazetteer(census_dir)
    return parse_gazetteer(path.read_text(encoding="utf-8"))


def shuffle_towns(towns: list[Town], seed: int) -> list[Town]:
    """Deterministic permutation. Same seed → same walk, forever."""
    out = list(towns)
    random.Random(seed).shuffle(out)
    return out


def pick_anchor(towns: list[Town], center: tuple[float, float] = CONUS_CENTER) -> int:
    """Index of the town nearest the CONUS center (flat-earth distance is fine
    at this scale; we only need *a* well-connected town in Kansas)."""
    clat, clon = center
    return min(
        range(len(towns)),
        key=lambda i: (towns[i].lat - clat) ** 2 + (towns[i].lon - clon) ** 2,
    )


# ---------------------------------------------------------------------------
# OSRM
# ---------------------------------------------------------------------------

def _osrm_url() -> str:
    return os.environ.get("OSRM_URL", DEFAULT_OSRM_URL).rstrip("/")


def _timeout() -> int:
    return int(os.environ.get("OSRM_TIMEOUT", DEFAULT_TIMEOUT))


def _coord(t: Town) -> str:
    return f"{t.lon:.6f},{t.lat:.6f}"


def check_reachability(
    towns: list[Town],
    anchor_idx: int,
    osrm_url: str | None = None,
    block: int = DEFAULT_TABLE_BLOCK,
    progress: bool = True,
) -> list[bool]:
    """One /table call per `block` towns, all against the single anchor.
    A null duration means OSRM can't route town → anchor: island, or a snap
    onto a disconnected component of the major-roads graph. ~63 calls for 31k."""
    base = osrm_url or _osrm_url()
    anchor = towns[anchor_idx]
    reachable = [False] * len(towns)
    n_blocks = (len(towns) + block - 1) // block
    for b in range(n_blocks):
        lo, hi = b * block, min((b + 1) * block, len(towns))
        chunk = towns[lo:hi]
        coords = ";".join(_coord(t) for t in chunk) + ";" + _coord(anchor)
        url = (
            f"{base}/table/v1/driving/{coords}"
            f"?annotations=duration"
            f"&sources={';'.join(str(i) for i in range(len(chunk)))}"
            f"&destinations={len(chunk)}"
        )
        blob = _get_json(url)
        if blob.get("code") != "Ok":
            raise RuntimeError(f"OSRM /table returned {blob.get('code')}: {blob}")
        for i, row in enumerate(blob["durations"]):
            reachable[lo + i] = row[0] is not None
        if progress and ((b + 1) % 10 == 0 or b + 1 == n_blocks):
            done = sum(reachable[:hi])
            print(f"[reach] block {b + 1}/{n_blocks}  reachable so far {done}/{hi}", flush=True)
    return reachable


def _get_json(url: str) -> dict:
    """GET with retry on transport errors only (OSRM `code` errors are returned)."""
    last: Exception | None = None
    for attempt in range(ROUTE_RETRIES):
        try:
            resp = requests.get(url, timeout=_timeout())
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"OSRM unreachable after {ROUTE_RETRIES} tries: {last}") from last


def route_leg(
    a: Town,
    b: Town,
    osrm_url: str | None = None,
    overview: str = "simplified",
) -> dict:
    """One /route call. Returns a leg record. `status` is "ok" or the OSRM
    error code (NoRoute, NoSegment, ...). Snap distances (meters from the
    town's internal point to where OSRM actually put it on the graph) are kept
    so the write-up can be honest about the major-roads-only last mile."""
    base = osrm_url or _osrm_url()
    url = (
        f"{base}/route/v1/driving/{_coord(a)};{_coord(b)}"
        f"?overview={overview}&geometries=polyline&steps=false"
    )
    blob = _get_json(url)
    code = blob.get("code")
    if code != "Ok" or not blob.get("routes"):
        return {"status": code or "NoRoutes"}
    route = blob["routes"][0]
    wps = blob.get("waypoints", [{}, {}])
    return {
        "status": "ok",
        "distance_m": float(route["distance"]),
        "duration_s": float(route["duration"]),
        "from_snap_m": float(wps[0].get("distance", 0.0)),
        "to_snap_m": float(wps[1].get("distance", 0.0)),
        "geometry": route.get("geometry") if overview != "false" else None,
    }


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

def read_legs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def resume_state(legs: list[dict]) -> tuple[int, int | None]:
    """(next_seq_to_attempt, current_origin_seq). Origin is the destination
    of the last *successful* leg; None means start from seq 0."""
    if not legs:
        return 0, None
    next_seq = legs[-1]["to_seq"] + 1
    for rec in reversed(legs):
        if rec["status"] == "ok":
            return next_seq, rec["to_seq"]
    return next_seq, legs[-1]["from_seq"]


def walk(
    towns: list[Town],
    reachable: list[bool],
    legs_path: Path,
    osrm_url: str | None = None,
    overview: str = "simplified",
    limit: int | None = None,
    route_fn=route_leg,
    progress_every: int = 100,
) -> list[dict]:
    """Drive the permutation in order, appending one record per attempted leg.
    Unreachable towns (from the pre-pass) are skipped without an OSRM call.
    A leg that still fails (asymmetric one-way edge, etc.) is recorded and the
    origin stays put. Resumable: re-running picks up after the last record."""
    n = len(towns) if limit is None else min(limit, len(towns))
    legs = read_legs(legs_path)
    next_seq, cur = resume_state(legs)
    if cur is None:
        # First origin = first reachable town in the permutation.
        cur = next(i for i in range(n) if reachable[i])
        next_seq = max(next_seq, cur + 1)
    if legs:
        print(f"[walk] resuming at seq {next_seq} (origin seq {cur}), {len(legs)} legs on disk", flush=True)

    consecutive_failures = 0
    t0 = time.time()
    n_new = 0
    with legs_path.open("a", encoding="utf-8") as fh:
        for j in range(next_seq, n):
            if not reachable[j]:
                rec = _record(cur, j, towns, {"status": "unreachable"})
            else:
                rec = _record(cur, j, towns, route_fn(towns[cur], towns[j], osrm_url, overview))
            fh.write(json.dumps(rec) + "\n")
            legs.append(rec)
            n_new += 1
            if rec["status"] == "ok":
                cur = j
                consecutive_failures = 0
            elif rec["status"] != "unreachable":
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    fh.flush()
                    raise RuntimeError(
                        f"{consecutive_failures} consecutive route failures from "
                        f"{towns[cur].name}, {towns[cur].state} (seq {cur}) — "
                        f"origin is probably unroutable; last status {rec['status']}"
                    )
            if progress_every and n_new % progress_every == 0:
                fh.flush()
                rate = n_new / max(time.time() - t0, 1e-9)
                remaining = (n - 1 - j) / max(rate, 1e-9)
                mi = sum(r.get("distance_m", 0.0) for r in legs) / METERS_PER_MILE
                print(
                    f"[walk] seq {j + 1}/{n}  {mi:,.0f} mi so far  "
                    f"{rate:.1f} legs/s  ~{remaining / 60:.0f} min left",
                    flush=True,
                )
    return legs


def _record(from_seq: int, to_seq: int, towns: list[Town], result: dict) -> dict:
    a, b = towns[from_seq], towns[to_seq]
    return {
        "from_seq": from_seq,
        "to_seq": to_seq,
        "from_geoid": a.geoid,
        "to_geoid": b.geoid,
        "from_name": f"{a.name}, {a.state}",
        "to_name": f"{b.name}, {b.state}",
        **result,
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summarize(legs: list[dict], n_towns: int, n_reachable: int, seed: int) -> dict:
    ok = [r for r in legs if r["status"] == "ok"]
    miles = [r["distance_m"] / METERS_PER_MILE for r in ok]
    hours = [r["duration_s"] / 3600.0 for r in ok]
    total_mi = sum(miles)
    total_h = sum(hours)
    out = {
        "seed": seed,
        "n_towns": n_towns,
        "n_reachable": n_reachable,
        "n_legs_attempted": len(legs),
        "n_legs_ok": len(ok),
        "n_skipped_unreachable": sum(r["status"] == "unreachable" for r in legs),
        "n_failed_route": sum(r["status"] not in ("ok", "unreachable") for r in legs),
        "total_miles": round(total_mi, 1),
        "total_hours": round(total_h, 1),
        "total_days_nonstop": round(total_h / 24.0, 1),
        "total_years_nonstop": round(total_h / 24.0 / 365.25, 2),
        "total_years_at_8h_per_day": round(total_h / 8.0 / 365.25, 2),
        "vs_olson_2015_miles": round(total_mi / OLSON_MILES, 1),
        "vs_tier1_optimal_miles": round(total_mi / TIER1_MILES, 1),
    }
    if ok:
        longest = max(ok, key=lambda r: r["distance_m"])
        shortest = min(ok, key=lambda r: r["distance_m"])
        snaps = sorted([r["from_snap_m"] for r in ok] + [r["to_snap_m"] for r in ok])
        out.update({
            "leg_miles_mean": round(statistics.fmean(miles), 1),
            "leg_miles_median": round(statistics.median(miles), 1),
            "leg_miles_max": round(longest["distance_m"] / METERS_PER_MILE, 1),
            "leg_miles_min": round(shortest["distance_m"] / METERS_PER_MILE, 1),
            "longest_leg": f"{longest['from_name']} → {longest['to_name']}",
            "shortest_leg": f"{shortest['from_name']} → {shortest['to_name']}",
            "snap_m_median": round(snaps[len(snaps) // 2], 0),
            "snap_m_p95": round(snaps[int(len(snaps) * 0.95)], 0),
            "snap_m_max": round(snaps[-1], 0),
        })
    return out


def _print_summary(s: dict) -> None:
    print()
    print(f"random walk · seed {s['seed']} · {s['n_legs_ok']:,} legs "
          f"over {s['n_reachable']:,} reachable of {s['n_towns']:,} towns")
    print(f"  total        {s['total_miles']:>14,.0f} mi   {s['total_hours']:>12,.0f} h")
    print(f"  nonstop      {s['total_years_nonstop']:>14,.2f} years")
    print(f"  at 8 h/day   {s['total_years_at_8h_per_day']:>14,.2f} years")
    if "leg_miles_mean" in s:
        print(f"  leg mean     {s['leg_miles_mean']:>14,.0f} mi   median {s['leg_miles_median']:,.0f} mi")
        print(f"  longest      {s['leg_miles_max']:>14,.0f} mi   {s['longest_leg']}")
        print(f"  shortest     {s['leg_miles_min']:>14,.1f} mi   {s['shortest_leg']}")
        print(f"  snap         median {s['snap_m_median']:.0f} m · p95 {s['snap_m_p95']:.0f} m · max {s['snap_m_max']:.0f} m")
    print(f"  skipped      {s['n_skipped_unreachable']:,} unreachable · {s['n_failed_route']:,} route failures")
    print(f"  = {s['vs_olson_2015_miles']:,}× Olson 2015 · {s['vs_tier1_optimal_miles']:,}× the Tier 1 optimum")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write_towns_csv(path: Path, towns: list[Town], reachable: list[bool]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["seq", "geoid", "name", "state", "lat", "lon", "reachable"])
        for i, (t, r) in enumerate(zip(towns, reachable)):
            w.writerow([i, t.geoid, t.name, t.state, t.lat, t.lon, int(r)])


def _read_towns_csv(path: Path) -> tuple[list[Town], list[bool]]:
    towns, reachable = [], []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            towns.append(Town(row["geoid"], row["name"], row["state"], float(row["lat"]), float(row["lon"])))
            reachable.append(row["reachable"] == "1")
    return towns, reachable


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit", type=int, default=None, help="only walk the first N towns of the permutation (smoke test)")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--census-dir", type=Path, default=DEFAULT_CENSUS_DIR)
    p.add_argument("--overview", choices=["simplified", "full", "false"], default="simplified",
                   help="polyline detail to store per leg (false = numbers only)")
    p.add_argument("--osrm-url", default=None, help="override OSRM_URL env / default :5000")
    args = p.parse_args(argv)

    run_dir = args.out_dir / f"seed{args.seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    towns_csv = run_dir / "towns.csv"
    legs_path = run_dir / "legs.jsonl"
    manifest_path = run_dir / "run.json"
    osrm_url = args.osrm_url or _osrm_url()
    print(f"[random_walk] OSRM {osrm_url} · run dir {run_dir}", flush=True)

    if towns_csv.exists() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["seed"] != args.seed:
            print(f"manifest seed {manifest['seed']} != --seed {args.seed}; refusing", file=sys.stderr)
            return 2
        towns, reachable = _read_towns_csv(towns_csv)
        print(f"[random_walk] loaded permutation from {towns_csv} ({len(towns):,} towns, "
              f"{sum(reachable):,} reachable)", flush=True)
    else:
        towns = shuffle_towns(load_gazetteer(args.census_dir), args.seed)
        anchor = pick_anchor(towns)
        print(f"[random_walk] {len(towns):,} towns · anchor {towns[anchor].name}, {towns[anchor].state}", flush=True)
        reachable = check_reachability(towns, anchor, osrm_url)
        _write_towns_csv(towns_csv, towns, reachable)
        manifest_path.write_text(json.dumps({
            "seed": args.seed,
            "n_towns": len(towns),
            "n_reachable": sum(reachable),
            "anchor_seq": anchor,
            "anchor": f"{towns[anchor].name}, {towns[anchor].state}",
            "gazetteer": GAZETTEER_URL,
            "osrm_url": osrm_url,
        }, indent=2))
        print(f"[random_walk] {sum(reachable):,} reachable · {len(towns) - sum(reachable):,} dropped", flush=True)

    legs = walk(towns, reachable, legs_path, osrm_url=osrm_url, overview=args.overview, limit=args.limit)
    summary = summarize(legs, len(towns), sum(reachable), args.seed)
    summary["limit"] = args.limit
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
