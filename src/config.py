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
    # ---- Deferred to Phase 2; accepted but unused ----
    category_priority: dict[str, int] = field(default_factory=dict)
    total_trip_days: int | None = None

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

        # 7. Deferred fields: warn when set
        if self.category_priority:
            warnings.warn(
                "category_priority is accepted but ignored in Phase 1; "
                "activates in time-budgeted mode (Phase 2)",
                UserWarning,
                stacklevel=2,
            )
        if self.total_trip_days is not None:
            warnings.warn(
                "total_trip_days is accepted but ignored in Phase 1; "
                "activates in time-budgeted mode (Phase 2)",
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
