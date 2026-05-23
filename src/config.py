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
    # ---- Deferred to Phase 2; accepted but unused ----
    category_priority: dict[str, int] = field(default_factory=dict)
    total_trip_days: int | None = None

    def __post_init__(self) -> None:
        # Phase 1: validation lives here (Phase 2 may externalize). See spec §4.
        # Validation rules added in Task 3 — keep this method body empty here so
        # Step 2 of THIS task passes without the rules being implemented yet.
        pass


def load_config(path: Path) -> TripConfig:
    """Load a TripConfig from a YAML file. Raises yaml.YAMLError on parse
    error, TripConfigError on validation failure."""
    with Path(path).open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise TripConfigError(
            f"YAML at {path} must define a mapping at the root; got {type(data).__name__}"
        )
    return TripConfig(**data)
