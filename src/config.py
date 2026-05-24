"""TripConfig dataclass, YAML loader, and validation for Tier 2 trips.

See docs/superpowers/specs/2026-05-22-tier2-trip-config-design.md §4.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class TripConfigError(Exception):
    """Base for all TripConfig-related validation and runtime errors."""


class EmptyCandidatePool(TripConfigError):
    """fetch_pois returned no rows after applying filters."""


class UnreachableMustInclude(TripConfigError):
    """A must_include POI ID was not found in the database."""


class SingleStopTour(TripConfigError):
    """After filtering, only the depot would be visited — no meaningful tour."""


class InfeasibleMaxStops(TripConfigError):
    """max_stops is less than the number of required visits."""


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class TripConfig:
    """Immutable trip configuration loaded from YAML. See spec §4.

    Note: `frozen=True` prevents mutation after construction. It does NOT
    make instances hashable, because `list` and `dict` fields fall back to
    `__hash__ = None`. If a future task needs hashing (e.g., caching solver
    results keyed by config), convert list/dict fields to tuple/frozenset
    and re-evaluate YAML deserialization.
    """
    name: str = "untitled"
    # Filters (None = no filter)
    categories: list[str] | None = None
    states: list[str] | None = None
    max_radius_miles: float | None = None
    # Required visits
    must_include: list[int] = field(default_factory=list)
    # Cardinality
    max_stops: int | None = None
    # Routing shape
    start_state: str | None = None
    loop: bool = True
    # Display
    max_hours_per_day: float = 8.0
    # Solver
    time_limit_seconds: int = 300
    # Routing engine selection. "us" uses the US-only OSRM (Tier 1 baseline,
    # oracle-verified at 193 h / 9,744 mi). "us_canada" uses the combined
    # cross-border OSRM, which fixes routes where Canadian highways are
    # actually fastest (e.g., Detroit→Buffalo via Ontario saves ~80 mi / 2 h).
    # Default "us" preserves Tier 1 reproducibility — opt in per-trip in YAML.
    routing_network: str = "us"
    # Wall-clock minutes added per US-Canada border crossing — OSRM models the
    # road network but is blind to customs/passport-check time. A round-trip
    # leg through Canada (e.g., Detroit→Buffalo via Ontario) crosses 2× so the
    # solver sees 2 × border_crossing_minutes added to that leg's duration.
    # Only applied when routing_network='us_canada'; ignored otherwise.
    # 20 minutes per crossing (40 min per leg) matches CBP/CBSA published
    # averages at major crossings (Ambassador Bridge, Peace Bridge, Sault Ste M)
    # under normal weekday traffic. Bump to 30+ for summer/holiday trips, or
    # set to 0 to suppress the penalty entirely (useful for diagnostic runs
    # or for travelers with NEXUS who clear in under 5 min).
    border_crossing_minutes: int = 20
    # ---- Time-budgeted mode (Tier 2 Phase 2; the headline feature from
    #      doc 05). When `total_trip_days` is set, the solver switches
    #      from "state coverage" to "score maximization within budget":
    #      compute each POI's value = poi_priority[id] ?? category_priority[cat]
    #      ?? 0, then find the tour whose total drive time fits within
    #      total_trip_days * max_hours_per_day * 3600 seconds while
    #      maximizing summed value. The budget is SOFT — overage costs
    #      time_budget_overage_penalty priority points per excess hour
    #      so the solver may slightly exceed if a high-value POI sits
    #      just past the line.
    #
    #      When `total_trip_days is None` the existing state-coverage
    #      solver runs (`states` requires visiting ≥1 POI per state).
    #      In time-budgeted mode `states` becomes a geographic FILTER
    #      only (restrict candidate pool); it no longer enforces
    #      coverage. `must_include` still hard-forces specific POIs.
    category_priority: dict[str, int] = field(default_factory=dict)
    poi_priority: dict[int, int] = field(default_factory=dict)
    total_trip_days: int | None = None
    time_budget_overage_penalty: float = 1.0  # priority points lost per excess hour

    def __post_init__(self) -> None:
        # 1. name must be filename-safe
        if not _NAME_PATTERN.match(self.name):
            raise TripConfigError(
                f"config.name={self.name!r} must be filename-safe "
                f"(match {_NAME_PATTERN.pattern})"
            )

        # 2. max_radius_miles requires start_state
        if self.max_radius_miles is not None and self.start_state is None:
            raise TripConfigError(
                "max_radius_miles requires start_state to provide a center point"
            )

        # 3. loop=False requires start_state for a deterministic depot
        if not self.loop and self.start_state is None:
            raise TripConfigError(
                "loop=False requires start_state to provide an unambiguous start point"
            )

        # 4. start_state must be in states if states is set
        if (
            self.start_state is not None
            and self.states is not None
            and self.start_state not in self.states
        ):
            raise TripConfigError(
                f"start_state {self.start_state!r} not in states={self.states!r}"
            )

        # 5. max_stops feasibility (against state coverage; the must_include
        #    overlap deduction is finalized once we have the DB connection in
        #    fetch_pois — at config-load time we can only check the state floor).
        if self.max_stops is not None and self.states is not None:
            min_floor = len(self.states)
            if self.max_stops < min_floor:
                raise InfeasibleMaxStops(
                    f"max_stops={self.max_stops} is less than the number of "
                    f"required states ({min_floor}). Increase max_stops or "
                    f"reduce states."
                )

        # 6. routing_network must be a known value
        _VALID_NETWORKS = {"us", "us_canada"}
        if self.routing_network not in _VALID_NETWORKS:
            raise TripConfigError(
                f"routing_network={self.routing_network!r} must be one of "
                f"{sorted(_VALID_NETWORKS)}"
            )

        # 6b. border_crossing_minutes must be non-negative. Bound at 240
        #     (4 hours) — beyond that and a sane traveler reroutes through
        #     the US anyway, so the value is almost certainly a typo.
        if not (0 <= self.border_crossing_minutes <= 240):
            raise TripConfigError(
                f"border_crossing_minutes={self.border_crossing_minutes} "
                f"must be in [0, 240]; got an absurd value"
            )

        # 7. Time-budgeted-mode validation. These fields used to emit
        # "deferred" UserWarnings; they're now wired into solve_with_config.
        if self.total_trip_days is not None and self.total_trip_days <= 0:
            raise TripConfigError(
                f"total_trip_days={self.total_trip_days} must be > 0 "
                f"(a zero-day trip can't visit anything)"
            )
        if self.max_hours_per_day <= 0:
            raise TripConfigError(
                f"max_hours_per_day={self.max_hours_per_day} must be > 0"
            )
        if self.time_budget_overage_penalty < 0:
            raise TripConfigError(
                f"time_budget_overage_penalty={self.time_budget_overage_penalty} "
                f"must be >= 0 (negative would reward exceeding the budget)"
            )
        # Soft caveat (warning, not error): in time-budgeted mode `states`
        # becomes a geographic filter only, not a coverage requirement.
        # Surface this so a trip author who set both doesn't get confused.
        if self.total_trip_days is not None and self.states is not None:
            warnings.warn(
                f"total_trip_days={self.total_trip_days} engages "
                f"time-budgeted mode; states={self.states} is now used "
                f"only as a geographic FILTER (restrict the candidate pool) "
                f"and no longer requires visiting ≥1 POI per state. Set "
                f"must_include for hard 'visit this' requirements.",
                UserWarning,
                stacklevel=2,
            )


def load_config(path: Path) -> TripConfig:
    """Load a TripConfig from a YAML file. Raises yaml.YAMLError on parse
    error, TripConfigError on empty/non-dict YAML or unknown fields."""
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        raise TripConfigError(f"YAML at {path} is empty or contains only comments")
    if not isinstance(data, dict):
        raise TripConfigError(
            f"YAML at {path} must define a mapping at the root; got {type(data).__name__}"
        )
    try:
        return TripConfig(**data)
    except TypeError as exc:
        # TripConfig.__init__ raises TypeError for unknown kwargs (typo'd field
        # names). Re-raise as TripConfigError so CLI runner can catch the family.
        raise TripConfigError(f"Invalid config field in {path}: {exc}") from exc
