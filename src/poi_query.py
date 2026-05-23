"""POI fetch with TripConfig filters. See spec §6.2-6.4.

Two public entry points:
  - build_query(config) -> (sql, params): pure function for testing
  - fetch_pois(config) -> list[dict]: runs the query against PostGIS
"""
from __future__ import annotations

import warnings

from src.config import (
    TripConfig,
    TripConfigError,
    EmptyCandidatePool,
    UnreachableMustInclude,
    SingleStopTour,
)
from src.db import get_conn


_EXCLUDED_STATES = ["AK", "HI"]
_METERS_PER_MILE = 1609.344


def build_query(config: TripConfig) -> tuple[str, dict]:
    """Return (sql, params) for the candidate POI query implied by config.
    Pure function — does not open a DB connection. Used for testing and
    debugging."""
    where_clauses = [
        "source = 'nps'",
        "state IS NOT NULL",
        "state <> ALL(%(excluded)s)",
    ]
    params: dict = {"excluded": list(_EXCLUDED_STATES)}

    if config.states is not None:
        where_clauses.append("state = ANY(%(states)s)")
        params["states"] = list(config.states)

    if config.categories is not None:
        where_clauses.append("category = ANY(%(categories)s)")
        params["categories"] = list(config.categories)

    if config.max_radius_miles is not None:
        # center = centroid of POIs in start_state (proxy for "regional center"
        # since we don't have state-polygon geometry in this DB).
        where_clauses.append(
            "ST_DWithin(geom::geography, "
            "  (SELECT ST_Centroid(ST_Collect(geom))::geography "
            "   FROM pois WHERE source='nps' AND state = %(center_state)s), "
            "  %(radius_meters)s)"
        )
        params["center_state"] = config.start_state
        params["radius_meters"] = config.max_radius_miles * _METERS_PER_MILE

    sql = (
        "SELECT id, name, state, category, "
        "       ST_Y(geom) AS lat, ST_X(geom) AS lon "
        "FROM pois "
        f"WHERE {' AND '.join(where_clauses)} "
        "ORDER BY state, id"
    )
    return sql, params


def fetch_pois(config: TripConfig) -> list[dict]:
    """Execute the candidate query plus must_include union. Returns a list
    of POI dicts. Raises:
      - EmptyCandidatePool if zero rows after filters AND no must_include
      - UnreachableMustInclude if any must_include POI ID isn't in the DB
      - SingleStopTour if only 1 POI matched (no meaningful tour possible)
    """
    sql, params = build_query(config)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        # must_include override: union back any must-include POIs that
        # didn't match the filters above.
        if config.must_include:
            seen_ids = {r["id"] for r in rows}
            missing_id_set = set(config.must_include) - seen_ids
            if missing_id_set:
                missing_ids = sorted(missing_id_set)  # sorted for deterministic SQL
                cur.execute(
                    "SELECT id, name, state, category, "
                    "       ST_Y(geom) AS lat, ST_X(geom) AS lon "
                    "FROM pois WHERE id = ANY(%(missing)s)",
                    {"missing": missing_ids},
                )
                extras = [dict(zip(cols, row)) for row in cur.fetchall()]
                found_extra_ids = {r["id"] for r in extras}
                truly_missing = missing_id_set - found_extra_ids
                if truly_missing:
                    raise UnreachableMustInclude(
                        f"must_include POI IDs not found in database: "
                        f"{sorted(truly_missing)}"
                    )
                for r in extras:
                    if r["id"] in missing_id_set:
                        warnings.warn(
                            f"must_include POI {r['id']} ({r['name']!r}, "
                            f"state={r['state']!r}) is outside the filter "
                            f"scope but will be visited anyway",
                            UserWarning,
                            stacklevel=2,
                        )
                rows.extend(extras)
                # Re-sort by (state, id) for determinism
                rows.sort(key=lambda r: (r["state"], r["id"]))

    if not rows:
        raise EmptyCandidatePool(
            f"No POIs match the config filters. "
            f"states={config.states}, categories={config.categories}, "
            f"max_radius_miles={config.max_radius_miles} "
            f"from start_state={config.start_state}"
        )
    if len(rows) < 2:
        # 1 POI = no tour possible (depot only, no destinations)
        raise SingleStopTour(
            f"Only {len(rows)} POI matched the config; need at least 2 for a "
            f"tour. POI: {rows[0]['name']!r} ({rows[0]['state']}). "
            f"Widen the filters (states, categories, or max_radius_miles)."
        )
    return rows
