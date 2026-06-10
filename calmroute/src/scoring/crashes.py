"""Layer 1 — crash density along a route corridor.

Raw score = sum over crashes within CRASH_BUFFER_M of the route of
severity_weight x recency_weight, divided by route length in km.
The composite layer normalizes raw scores across the candidate set.
"""

from dataclasses import dataclass

import psycopg

from .. import config


@dataclass
class CrashRecord:
    severity: str   # 'fatal' | 'injury' | 'pdo' | 'unknown'
    year: int


@dataclass
class CrashSubscore:
    raw: float                  # weighted crashes per km
    total: int
    fatal: int
    injury: int
    years_covered: tuple[int, int] | None
    explanation: str


def severity_weight(severity: str) -> float:
    return config.SEVERITY_WEIGHTS.get(severity, config.SEVERITY_WEIGHTS["pdo"])


def recency_weight(crash_year: int, newest_year: int) -> float:
    """Linear decay from RECENCY_WEIGHT_NEWEST (newest year) down to
    RECENCY_WEIGHT_OLDEST (newest - 4). Older crashes keep the floor weight."""
    age = newest_year - crash_year
    span = config.CRASH_YEARS_WINDOW - 1
    step = (config.RECENCY_WEIGHT_NEWEST - config.RECENCY_WEIGHT_OLDEST) / span
    return max(
        config.RECENCY_WEIGHT_NEWEST - step * age,
        config.RECENCY_WEIGHT_OLDEST,
    )


def crash_subscore(crashes: list[CrashRecord], route_length_km: float) -> CrashSubscore:
    """Pure scoring math — testable without a database."""
    if route_length_km <= 0:
        route_length_km = 0.001
    newest = max((c.year for c in crashes), default=0)
    weighted = sum(
        severity_weight(c.severity) * recency_weight(c.year, newest) for c in crashes
    )
    fatal = sum(1 for c in crashes if c.severity == "fatal")
    injury = sum(1 for c in crashes if c.severity == "injury")
    years = (min(c.year for c in crashes), newest) if crashes else None

    if crashes:
        parts = [f"{len(crashes)} crashes within {config.CRASH_BUFFER_M:.0f} m"]
        if years and years[0] != years[1]:
            parts.append(f"({years[0]}–{years[1]})")
        detail = []
        if fatal:
            detail.append(f"{fatal} fatal")
        if injury:
            detail.append(f"{injury} with injuries")
        if detail:
            parts.append("— " + ", ".join(detail))
        explanation = " ".join(parts)
    else:
        explanation = "No recorded crashes on this corridor"

    return CrashSubscore(
        raw=weighted / route_length_km,
        total=len(crashes),
        fatal=fatal,
        injury=injury,
        years_covered=years,
        explanation=explanation,
    )


def fetch_crashes_near_route(conn: psycopg.Connection, route_wkt: str) -> list[CrashRecord]:
    """All crashes within CRASH_BUFFER_M of the route line (geography distance)."""
    rows = conn.execute(
        """
        SELECT severity, year FROM crashes
        WHERE ST_DWithin(
            geom::geography,
            ST_GeomFromText(%(wkt)s, 4326)::geography,
            %(buffer_m)s
        )
        """,
        {"wkt": route_wkt, "buffer_m": config.CRASH_BUFFER_M},
    ).fetchall()
    return [CrashRecord(severity=r[0], year=r[1]) for r in rows]
