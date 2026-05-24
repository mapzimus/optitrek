"""Translate an HTML form submission into a TripConfig.

The form's input names mirror TripConfig field names, with these
conventions for non-scalar types:

  list[str]  fields (`states`, `categories`)
      Sent as repeated form values. FastAPI's `Form(...)` with
      `list[str]` collects them automatically; this module just
      normalizes empty submissions to None (matching TripConfig's
      "no filter" semantic).

  list[int] fields (`must_include`)
      Sent as a single comma-separated string ("42, 107, 192"). Empty
      string -> empty list, not None.

  dict[str, int] (`category_priority`)
      Sent as multiple inputs named `category_priority[<cat>]=<int>`.
      The HTML form renders one row per category from the DB; rows
      left blank are excluded from the dict.

  dict[int, int] (`poi_priority`)
      Sent as a single textarea with one "<id>: <value>" per line.
      Lines that don't parse cleanly are skipped (with a warning
      logged) so a stray comment doesn't blow up the whole submission.

  Optional scalars (`max_radius_miles`, `max_stops`, etc.)
      Empty string from the form -> None. Numeric coercion happens
      here, not in TripConfig (which assumes its inputs are typed).

The function returns (TripConfig, list[str] of soft-warnings) so the
caller can surface any "we ignored line N of poi_priority" feedback
to the user without crashing on dirty input.
"""
from __future__ import annotations

import re
from typing import Any

from src.config import TripConfig


# Pattern for a single "id: value" line in the poi_priority textarea.
# Whitespace-flexible; tolerates `42:25`, `42 : 25`, `42  :  25`.
_POI_PRIORITY_LINE = re.compile(r"^\s*(\d+)\s*:\s*(-?\d+)\s*$")


def _opt_str(form: dict, key: str) -> str | None:
    """Form field -> Optional[str]. Empty string becomes None so
    downstream `None` checks in TripConfig validation work correctly."""
    raw = form.get(key, "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = raw.strip() if isinstance(raw, str) else raw
    return raw or None


def _opt_int(form: dict, key: str) -> int | None:
    raw = _opt_str(form, key)
    return int(raw) if raw is not None else None


def _opt_float(form: dict, key: str) -> float | None:
    raw = _opt_str(form, key)
    return float(raw) if raw is not None else None


def _list_str(form: dict, key: str) -> list[str] | None:
    """Multiselect or repeated-field -> list[str] (None if empty)."""
    raw = form.get(key, [])
    if isinstance(raw, str):
        raw = [raw]
    cleaned = [s.strip() for s in raw if s and s.strip()]
    return cleaned or None


def _list_int_csv(form: dict, key: str) -> list[int]:
    """Comma-separated IDs -> list[int]. Empty/missing -> []."""
    raw = _opt_str(form, key)
    if raw is None:
        return []
    return [int(p.strip()) for p in raw.split(",") if p.strip()]


def _dict_str_int_from_prefix(form: dict, prefix: str) -> dict[str, int]:
    """Collect all `<prefix>[<key>]=<int>` form fields into a dict.

    Skips empty values (no priority set for that key) and rows where
    the value isn't a parseable int. This is the right behavior for
    HTML-form-driven dict fields: the user fills in some rows and
    leaves others blank, and we treat blanks as "don't include."
    """
    out: dict[str, int] = {}
    needle = prefix + "["
    for full_key, raw in form.items():
        if not full_key.startswith(needle) or not full_key.endswith("]"):
            continue
        # Extract <key> between the brackets.
        inner = full_key[len(needle): -1]
        if not inner:
            continue
        val_str = raw[0] if isinstance(raw, list) else raw
        if not val_str or not str(val_str).strip():
            continue
        try:
            out[inner] = int(val_str)
        except (ValueError, TypeError):
            # Skip ill-typed rows; the warning system below could
            # surface this if needed, but a malformed priority row
            # is almost always a typo on the form.
            continue
    return out


def _parse_poi_priority_textarea(text: str | None) -> tuple[dict[int, int], list[str]]:
    """Parse the poi_priority textarea. Returns (dict, warnings).

    Lines that don't match `<int>: <int>` are skipped with a warning
    rather than raising — the form submitter sees the warnings on the
    result page and can fix without re-submitting from scratch.
    Comments (lines starting with #) are silently ignored.
    """
    out: dict[int, int] = {}
    warnings: list[str] = []
    if not text:
        return out, warnings
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _POI_PRIORITY_LINE.match(stripped)
        if not m:
            warnings.append(
                f"poi_priority line {line_no}: '{line.strip()[:40]}' "
                f"doesn't match '<id>: <value>' format — skipped"
            )
            continue
        poi_id, value = int(m.group(1)), int(m.group(2))
        if poi_id in out:
            warnings.append(
                f"poi_priority line {line_no}: POI {poi_id} appears more "
                f"than once — keeping last value ({value})"
            )
        out[poi_id] = value
    return out, warnings


def form_to_config(form: dict[str, Any]) -> tuple[TripConfig, list[str]]:
    """Build a TripConfig from a parsed-form dict.

    Returns (config, soft_warnings). The TripConfig constructor may
    still raise TripConfigError on hard validation failures (filename-
    safe name, max_radius requires start_state, etc.); those bubble up
    to the route handler which renders them as a user-visible error
    page rather than a 500.

    The `soft_warnings` list collects parse-level issues (skipped
    lines in poi_priority, etc.) that didn't prevent the config from
    being built but are worth showing the user.
    """
    poi_priority, soft_warnings = _parse_poi_priority_textarea(
        _opt_str(form, "poi_priority_textarea")
    )

    # `loop` is a checkbox: present when checked, absent when not.
    # FastAPI's Form() returns None for missing checkboxes by default.
    # We default to TripConfig's default (True) to match CLI behavior.
    loop_raw = form.get("loop")
    loop = bool(loop_raw) if loop_raw is not None else True

    cfg = TripConfig(
        name=str(form.get("name", "untitled")).strip() or "untitled",
        categories=_list_str(form, "categories"),
        states=_list_str(form, "states"),
        max_radius_miles=_opt_float(form, "max_radius_miles"),
        must_include=_list_int_csv(form, "must_include"),
        max_stops=_opt_int(form, "max_stops"),
        start_state=_opt_str(form, "start_state"),
        loop=loop,
        max_hours_per_day=_opt_float(form, "max_hours_per_day") or 8.0,
        time_limit_seconds=_opt_int(form, "time_limit_seconds") or 300,
        routing_network=_opt_str(form, "routing_network") or "us",
        border_crossing_minutes=_opt_int(form, "border_crossing_minutes") or 20,
        category_priority=_dict_str_int_from_prefix(form, "category_priority"),
        poi_priority=poi_priority,
        total_trip_days=_opt_int(form, "total_trip_days"),
        time_budget_overage_penalty=(
            _opt_float(form, "time_budget_overage_penalty") or 1.0
        ),
    )
    return cfg, soft_warnings
