"""Tests for src/matrix_builder.py — the EXCLUDED_STATES contract.

What this file pins:
  1. The EXCLUDED_STATES set contains every state the Tier 1 solver doesn't
     plan to visit. If it diverges from src/run_tier1.py:REQUIRED_STATES,
     the solver wastes work on phantom nodes (PR before this fix) OR the
     SQL filter drops candidates the solver needs (would crash with
     "candidate set cannot cover required states").
  2. fetch_pois() returns rows whose states are all (a) non-null and
     (b) NOT in EXCLUDED_STATES.

What this file does NOT pin (yet — see TODO below):
  - The exact row count (437) or distinct-state count (49). Those are
    properties of the LIVE DB at a moment in time and will drift as the
    NPS catalog evolves. The user chose what flavor of "is the data clean"
    test to add — see the TODO block.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.matrix_builder import EXCLUDED_STATES, fetch_pois
from src.run_tier1 import REQUIRED_STATES


# ---------- Pure-constant tests (no DB) ----------

def test_excluded_states_covers_ak_hi_and_all_territories():
    """The exclusion list must contain AK, HI, and the five US territories
    that aren't in REQUIRED_STATES. Catches the original PR-leak bug AND
    any future "we added a territory NPS unit and forgot to exclude it"
    regression."""
    expected = {"AK", "HI", "PR", "VI", "GU", "MP", "AS"}
    assert EXCLUDED_STATES == expected, (
        f"EXCLUDED_STATES drifted from the territory contract. "
        f"Missing: {expected - EXCLUDED_STATES}. "
        f"Unexpected: {EXCLUDED_STATES - expected}."
    )


def test_excluded_states_and_required_states_are_disjoint():
    """The two sets MUST NOT overlap. If they do, the SQL filter drops a
    state the solver requires → solve() raises ValueError("candidate set
    cannot cover required states"). This test catches that long before
    a live run does."""
    overlap = EXCLUDED_STATES & REQUIRED_STATES
    assert overlap == set(), (
        f"EXCLUDED_STATES and REQUIRED_STATES overlap on {overlap}. "
        f"The solver would reject the candidate set at runtime."
    )


# ---------- DB-shape tests (mocked) ----------

def _mock_cursor_returning(rows: list[tuple]) -> MagicMock:
    """Build a psycopg-cursor-shaped mock that fetchall() returns `rows`.
    Mirrors the fixture pattern from tests/test_poi_query.py so future
    refactors only have to update one cursor-mock idiom across the suite."""
    cur = MagicMock()
    cur.description = []
    for name in ("id", "name", "state", "category", "lat", "lon"):
        col = MagicMock()
        col.name = name
        cur.description.append(col)
    cur.fetchall.return_value = rows
    return cur


def test_fetch_pois_passes_excluded_states_as_array_param():
    """fetch_pois must send EXCLUDED_STATES as a LIST parameter to psycopg
    v3's <> ALL(%(excluded)s) idiom. The original 2026-05 bug was using
    `IN (...)` with a tuple — psycopg v3 sent the tuple as one parameter
    and Postgres errored. This pins the array-shape contract."""
    cur = _mock_cursor_returning([])
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = cur

    with patch("src.matrix_builder.get_conn", return_value=MagicMock(
        __enter__=lambda self: fake_conn, __exit__=lambda *a: None,
    )):
        # fetch_pois will raise nothing on empty rows — it just returns []
        fetch_pois()

    # Inspect the SQL + params that fetch_pois actually sent.
    sql, params = cur.execute.call_args.args
    assert "state <> ALL(%(excluded)s)" in sql
    assert isinstance(params["excluded"], list), (
        "psycopg v3 requires a LIST for <> ALL(), not a tuple/set"
    )
    assert set(params["excluded"]) == EXCLUDED_STATES


# ---------- TODO for user contribution ----------
#
# The two tests above are STRUCTURAL — they prove the code is wired right.
# But neither catches a regression where the DB itself contains a stray
# excluded-state row that somehow leaked past the filter (e.g., trailing
# whitespace in state codes, mixed-case "Pr" vs "PR", a future schema
# change that breaks the filter without breaking the SQL string).
#
# Pick ONE of these BEHAVIORAL tests to add — each catches a different
# class of regression. They each fit in ~5-10 lines.
#
#   OPTION A — Mocked behavioral (matches existing test style, no DB)
#     def test_fetch_pois_filters_excluded_rows_from_db():
#         # Mock the cursor to return a mix of valid + excluded rows
#         # (a CA row and a PR row). Assert that fetch_pois drops the PR.
#         # CATCHES: a refactor that moves filtering from SQL to Python and
#         # then forgets to filter.
#
#   OPTION B — Live DB invariant (slower, needs Neon creds; ~3s)
#     @pytest.mark.integration
#     def test_live_db_no_excluded_states_in_results():
#         pois = fetch_pois()
#         leaked = {p["state"] for p in pois} & EXCLUDED_STATES
#         assert leaked == set(), f"Excluded states leaked: {leaked}"
#         # Optionally: assert REQUIRED_STATES <= {p["state"] for p in pois}
#         # CATCHES: data drift — a new territorial NPS unit appearing.
#         # Mark with @pytest.mark.integration so default `pytest` skips it.
#
#   OPTION C — Frozen-snapshot (today's exact 437/49 numbers)
#     def test_live_db_snapshot_437_pois_49_states():
#         pois = fetch_pois()
#         assert len(pois) == 437
#         assert len({p["state"] for p in pois}) == 49
#         # CATCHES: any DB change at all (most sensitive, most brittle).
#         # Will need updating every time NPS adds a new unit.
#
# Trade-offs:
#   A is the most consistent with the existing test_poi_query.py style.
#   B catches real-world data drift but skips by default in CI without DB.
#   C catches everything but you'll have to update the numbers ~quarterly.
#
# Pick the option that matches how you want this codebase to evolve and
# delete the other two comment blocks. Then delete this TODO header.
