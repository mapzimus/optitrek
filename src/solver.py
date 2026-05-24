"""Phase 3 — constrained TSP solver (set cover + Hamiltonian cycle).

Pure-Python interface: takes a distance matrix and a node→state mapping, returns
an ordered loop that visits at least one node per required state with minimum
total travel time. No DB or OSRM dependency — Phase 2 produces the matrix that
feeds in here.

Two modes (per DECISIONS.md D1):
  - "capped":   exactly one node per required state (49 stops total).
  - "uncapped": at least one node per required state; solver may add more
                stops if doing so shortens the loop.

Time budget defaults to 5 minutes (Gap 8). The solver returns the best tour
found in that window even if it isn't proven optimal; the SolveResult.status
field records what kind of solution it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from src.config import TripConfigError

Mode = Literal["capped", "uncapped"]

# Scale factor: OR-Tools routing solver works on integer costs. Multiplying
# float seconds by 1000 keeps millisecond precision without overflow risk
# (a 24-hour leg → 86,400,000, well within int64).
COST_SCALE = 1000

# Penalty for failing to cover a required state. Must dominate any plausible
# tour cost so the solver never chooses to skip a state. A continental US loop
# is < 500h drive time → < 1.8 billion scaled cost. 1e12 is safely above that.
COVERAGE_PENALTY = 10**12


@dataclass(frozen=True)
class Node:
    """One candidate stop. `id` is opaque to the solver (NPS park_code in
    production, integer index in tests). `state` is the USPS code used for
    the coverage constraint."""

    id: str | int
    state: str


@dataclass
class SolveResult:
    """Returned by solve(). `order` is the visit sequence (excluding the
    virtual depot); the loop closes order[-1] → order[0]."""

    order: list[Node] = field(default_factory=list)
    leg_costs: list[float] = field(default_factory=list)  # seconds, len == len(order)
    total_cost: float = 0.0                                # seconds
    states_covered: set[str] = field(default_factory=set)
    status: str = ""                                       # OR-Tools status string
    runtime_seconds: float = 0.0


# OR-Tools status enum → human-readable string.
_STATUS_NAMES = {
    0: "NOT_SOLVED",
    1: "SUCCESS",
    2: "FAIL",
    3: "FAIL_TIMEOUT",
    4: "INVALID",
}


def solve(
    *,
    nodes: list[Node],
    distance_matrix: np.ndarray,
    required_states: set[str],
    mode: Mode = "capped",
    depot_index: int = 0,
    time_limit_seconds: int = 300,
    first_solution_strategy: int | None = None,
    log_search: bool = False,
) -> SolveResult:
    """Solve the constrained-TSP for the given candidate set.

    distance_matrix[i, j] = drive time in seconds from node i to node j.
    Asymmetric matrices are fine (OSRM produces symmetric in practice).

    Modeling note on depot_index: OR-Tools routing minimizes the cost of a
    path that starts and ends at the depot. To get the cost of a true closed
    LOOP (you drive home at the end of the road trip), the depot must be a
    real node whose own outgoing/incoming edges are part of the cost. So we
    use one of the candidate nodes as the depot. The depot is always
    "visited"; for a sane choice, pass a node whose state is in
    required_states (so it would have been visited anyway). For a symmetric
    TSP the choice of depot does not change the optimal *cycle* cost — it
    only changes which point on the cycle is called the start.
    """
    n = len(nodes)
    if distance_matrix.shape != (n, n):
        raise ValueError(
            f"distance_matrix shape {distance_matrix.shape} doesn't match node count {n}"
        )
    if mode not in ("capped", "uncapped"):
        raise ValueError(f"mode must be 'capped' or 'uncapped', got {mode!r}")
    if not (0 <= depot_index < n):
        raise ValueError(f"depot_index {depot_index} out of range [0, {n})")

    # Verify the candidate set could cover every required state before we
    # spin up the solver — otherwise the result is misleading.
    available_states = {node.state for node in nodes}
    missing = required_states - available_states
    if missing:
        raise ValueError(
            f"candidate set cannot cover required states: {sorted(missing)}"
        )

    # --- Build the routing model ---
    # Depot is a real node. Solver returns depot → ... → depot, so the
    # objective is the true closed-loop cost.
    manager = pywrapcp.RoutingIndexManager(n, 1, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    scaled = (distance_matrix * COST_SCALE).round().astype(np.int64)

    def distance_cb(from_index: int, to_index: int) -> int:
        a = manager.IndexToNode(from_index)
        b = manager.IndexToNode(to_index)
        return int(scaled[a, b])

    transit_idx = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    # --- State coverage constraint ---
    # Group nodes by state. For required states, force ≥1 visit. For
    # non-required states (AK, HI, territories), every node is freely optional
    # with no penalty for skipping.
    #
    # The depot is always visited and can't appear in a disjunction (OR-Tools
    # would reject it). Exclude it from every disjunction; its state already
    # counts as "covered" for the purposes of the required-state check.
    state_to_indices: dict[str, list[int]] = {}
    for i, node in enumerate(nodes):
        state_to_indices.setdefault(node.state, []).append(i)

    depot_state = nodes[depot_index].state

    for state, node_indices in state_to_indices.items():
        non_depot = [i for i in node_indices if i != depot_index]
        routing_indices = [manager.NodeToIndex(i) for i in non_depot]
        if not routing_indices:
            # Only the depot covers this state. Nothing else to constrain.
            continue
        if state in required_states:
            if mode == "capped":
                # If the depot covers this state, no more nodes from it may
                # be added — cap = 1 already used by the always-active depot.
                # All other state nodes become hard-skipped (penalty 0).
                if state == depot_state:
                    for ri in routing_indices:
                        routing.AddDisjunction([ri], 0)
                else:
                    # max_cardinality=1 → at most one node from this state
                    # active; penalty COVERAGE_PENALTY if zero. Net: exactly 1.
                    routing.AddDisjunction(routing_indices, COVERAGE_PENALTY, 1)
            else:  # uncapped
                # Make each node individually optional, then add a hard
                # CP-level constraint that the state is covered. (If state ==
                # depot_state, the depot already covers it; no extra needed.)
                for ri in routing_indices:
                    routing.AddDisjunction([ri], 0)
                if state != depot_state:
                    cp = routing.solver()
                    cp.Add(
                        sum(routing.ActiveVar(ri) for ri in routing_indices) >= 1
                    )
        else:
            # Non-required state. Capped mode is the strict Olson comparison
            # (exactly len(required_states) stops total) — forbid any extras.
            # Uncapped mode allows them as free shortcuts.
            for ri in routing_indices:
                routing.AddDisjunction([ri], 0)
            if mode == "capped":
                cp = routing.solver()
                for ri in routing_indices:
                    cp.Add(routing.ActiveVar(ri) == 0)

    # --- Search parameters ---
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        first_solution_strategy
        if first_solution_strategy is not None
        else routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(time_limit_seconds)
    search_params.log_search = log_search

    # --- Solve ---
    import time

    t0 = time.monotonic()
    solution = routing.SolveWithParameters(search_params)
    runtime = time.monotonic() - t0

    result = SolveResult(
        status=_STATUS_NAMES.get(routing.status(), f"UNKNOWN_{routing.status()}"),
        runtime_seconds=runtime,
    )
    if solution is None:
        return result

    # --- Extract the tour ---
    # The route is depot → ... → depot. Walk it, recording each node and the
    # cost of the edge to its successor (which includes the closing edge back
    # to the depot). The returned `order` lists nodes in visit order starting
    # at the depot; the loop closes order[-1] → order[0] and that edge is the
    # last entry in `leg_costs`.
    index = routing.Start(0)
    visit_sequence: list[int] = []
    edge_costs: list[float] = []
    while not routing.IsEnd(index):
        node_idx = manager.IndexToNode(index)
        next_index = solution.Value(routing.NextVar(index))
        next_node_idx = manager.IndexToNode(next_index)
        visit_sequence.append(node_idx)
        edge_costs.append(float(distance_matrix[node_idx, next_node_idx]))
        index = next_index

    if not visit_sequence:
        return result  # degenerate — should never happen since depot is always active

    result.order = [nodes[i] for i in visit_sequence]
    result.leg_costs = edge_costs
    result.total_cost = sum(edge_costs)
    result.states_covered = {n.state for n in result.order}
    return result


def validate(result: SolveResult, required_states: set[str]) -> list[str]:
    """Return a list of problems with the solve result, or [] if clean.
    Belt-and-suspenders: run this before trusting the output."""
    problems: list[str] = []
    if not result.order:
        problems.append("empty route")
        return problems

    missing = required_states - result.states_covered
    if missing:
        problems.append(f"missing required states: {sorted(missing)}")

    seen_ids: set = set()
    for node in result.order:
        if node.id in seen_ids:
            problems.append(f"duplicate node in route: {node.id}")
        seen_ids.add(node.id)

    if any(c < 0 for c in result.leg_costs):
        problems.append("negative leg cost — distance matrix is corrupt")

    return problems


# ---------- Tier 2 config-driven wrapper ----------

def _depot_index_for_config(config, pois: list[dict]) -> int:
    """Resolve depot index from config priority:
      1. must_include POI in start_state
      2. First POI in start_state (sorted by state, id — pois already sorted)
      3. pois[0] if start_state is None
    See spec §6.4.
    """
    if config.start_state is None:
        return 0
    if config.must_include:
        for i, p in enumerate(pois):
            if p["id"] in config.must_include and p["state"] == config.start_state:
                return i
    for i, p in enumerate(pois):
        if p["state"] == config.start_state:
            return i
    raise TripConfigError(
        f"start_state={config.start_state!r} has no POIs in the candidate "
        f"set after filtering"
    )


def _poi_value(poi: dict, config) -> int:
    """Score for visiting `poi` under the config's priority hierarchy.

    Order of precedence:
      1. poi_priority[poi.id]       — explicit per-POI value (highest)
      2. category_priority[category] — category fallback
      3. 0                          — unranked default

    Returns an integer because OR-Tools cost-scaling works on int64. A
    fractional priority would round to zero and silently disappear.
    """
    if poi["id"] in config.poi_priority:
        return int(config.poi_priority[poi["id"]])
    return int(config.category_priority.get(poi["category"], 0))


def _solve_time_budgeted(
    config,
    pois: list[dict],
    durations,
    distances,
) -> SolveResult:
    """Score-maximizing solver: pick the subset of POIs whose tour fits
    within a soft time budget and maximizes total `_poi_value` score.

    Mechanic (OR-Tools):
      - Each non-depot POI is wrapped in `AddDisjunction([node], penalty, 1)`
        where penalty = poi_value * cost_scale. Higher penalty = stronger
        cost to skip, so the solver wants to visit high-value POIs.
      - A "time" Dimension accumulates the per-leg duration (same cost as
        the arc cost). `SetCumulVarSoftUpperBound` on the End-of-route
        cumul var sets the budget; each second of overage costs
        (overage_penalty_per_hour * cost_scale / 3600) units.
      - The arc cost evaluator returns scaled durations. The objective
        the solver minimizes is therefore:
            sum(arc costs) + sum(skip penalties for unvisited POIs)
          + (overage penalty if total time > budget)
        Minimizing skip penalties == maximizing total visited value.
      - `must_include` adds ActiveVar==1 hard constraints (same as the
        state-coverage path), forcing those POIs into the tour even if
        their value is low.
      - `loop=False` excludes the closing leg cost from the reported
        total — OR-Tools still solves a cycle, we just don't charge it.

    Why disjunctions instead of an Orienteering-Problem solver: OR-Tools
    doesn't ship one, but its VRP-with-disjunctions reduction is the
    standard mapping for OP and works at our problem scale (≤ a few
    hundred POIs).
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    import time
    import warnings

    nodes = [Node(id=p["id"], state=p["state"]) for p in pois]
    n = len(nodes)
    depot_index = _depot_index_for_config(config, pois)

    manager = pywrapcp.RoutingIndexManager(n, 1, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    cost_scale = 1000  # millisecond precision (matches state-coverage path)

    # Pre-round once. NaN cells become huge negative int64 (same caveat
    # as state-coverage solver — driven by the same `validate_matrix`
    # guard during matrix build; defensive comment kept identical).
    scaled_durations = (np.asarray(durations) * cost_scale).round().astype(np.int64)

    def time_callback(from_idx, to_idx):
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        return int(scaled_durations[i, j])

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # ---- Time dimension with soft upper bound = budget ----
    #
    # Budget = total_trip_days × max_hours_per_day × 3600 seconds, scaled
    # into cost_scale units. The Dimension's hard capacity is set well
    # above the budget so the SOFT upper bound is what actually binds.
    # Setting hard cap = budget would force a feasibility check that
    # rejects tours whose cumulative time exceeds the budget by even
    # one second, defeating the "soft" semantics.
    budget_seconds = int(config.total_trip_days * config.max_hours_per_day * 3600)
    budget_scaled = budget_seconds * cost_scale
    hard_cap_scaled = budget_scaled * 10  # 10× headroom for the soft bound to bind first

    routing.AddDimension(
        transit_callback_index,
        0,                # no slack between visits (no waiting allowed)
        hard_cap_scaled,  # generous hard cap; soft cap below is what binds
        True,             # fix_start_cumul_to_zero — depot starts at t=0
        "time",
    )
    time_dim = routing.GetDimensionOrDie("time")

    # Translate user's "priority points per excess hour" into the soft-
    # upper-bound coefficient. Unit analysis (see PRIORITY_TO_DRIVE_HOUR
    # below for the full derivation):
    #   - cumul over budget by D scaled-cost units = D/(3600*cost_scale) hours
    #   - we want penalty = (penalty_per_hour points) × (PRIORITY_TO_DRIVE_HOUR)
    #                       × (hours of overage) scaled-cost units
    #   - solving: coefficient on (D / (3600*cost_scale)) is
    #     penalty_per_hour × 3600 × cost_scale
    #     → coefficient applied to D itself is just penalty_per_hour
    overage_coefficient = (
        max(1, int(config.time_budget_overage_penalty))
        if config.time_budget_overage_penalty > 0 else 0
    )

    if overage_coefficient > 0:
        time_dim.SetCumulVarSoftUpperBound(
            routing.End(0), budget_scaled, overage_coefficient
        )

    # ---- Per-POI value-as-skip-penalty ----
    #
    # PRIORITY_TO_DRIVE_HOUR — the fundamental unit conversion. The user
    # writes priority points (e.g., national_park: 10). The solver
    # minimizes scaled-cost units (cost_scale × seconds). To make those
    # comparable, we define:
    #
    #     1 priority point ≡ 1 hour of driving worth of cost-pressure
    #
    # So skip_penalty (scaled-cost units) = value × 3600 × cost_scale.
    # That means value=10 ⇔ "the solver will drive up to 10 hours
    # round-trip to visit this POI," which matches the intuitive read
    # of priority numbers in the YAML.
    #
    # The AddDisjunction `1` is max_cardinality: "visit this node at most
    # once" (implicit in TSP but required by OR-Tools to mark it optional;
    # without the disjunction the node would be REQUIRED).
    #
    # Negative values are clamped to 0 — a "this POI should be avoided"
    # semantic would need a different mechanic and isn't Tier 2 v1 scope.
    PRIORITY_TO_DRIVE_HOUR = 3600 * cost_scale
    cp = routing.solver()
    for i, p in enumerate(pois):
        if i == depot_index:
            continue
        value = _poi_value(p, config)
        skip_penalty = max(0, value) * PRIORITY_TO_DRIVE_HOUR
        routing.AddDisjunction([manager.NodeToIndex(i)], skip_penalty, 1)

    # ---- must_include: hard ActiveVar==1 (same pattern as state-coverage) ----
    for must_id in config.must_include:
        for i, p in enumerate(pois):
            if p["id"] == must_id:
                if i != depot_index:
                    node_idx = manager.NodeToIndex(i)
                    cp.Add(routing.ActiveVar(node_idx) == 1)
                break

    # ---- Search params (mirrors state-coverage path) ----
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(int(config.time_limit_seconds))

    t0 = time.perf_counter()
    solution = routing.SolveWithParameters(search_params)
    runtime = time.perf_counter() - t0

    if solution is None:
        return SolveResult(
            order=[], total_cost=float("inf"), leg_costs=[],
            states_covered=set(), status="FAILED", runtime_seconds=runtime,
        )

    # ---- Extract the route (same walk-the-NextVar pattern) ----
    index = routing.Start(0)
    visited_node_indices: list[int] = []
    while not routing.IsEnd(index):
        visited_node_indices.append(manager.IndexToNode(index))
        index = solution.Value(routing.NextVar(index))

    leg_costs: list[float] = []
    for i in range(len(visited_node_indices) - 1):
        a = visited_node_indices[i]
        b = visited_node_indices[i + 1]
        leg_costs.append(float(durations[a][b]))
    # Loop=True: charge the closing leg. Loop=False: open path.
    if config.loop:
        leg_costs.append(
            float(durations[visited_node_indices[-1]][depot_index])
        )

    order_nodes = [nodes[i] for i in visited_node_indices]
    total_cost = sum(leg_costs)
    states_covered = {nd.state for nd in order_nodes}

    # Diagnostic: compare actual cost vs budget so the user can see
    # whether the soft cap bound. Don't make this a warning — exceeding
    # is intentional in soft-cap semantics.
    actual_hours = total_cost / 3600
    budget_hours = budget_seconds / 3600
    overage_h = actual_hours - budget_hours
    total_value = sum(
        _poi_value(p, config) for p in pois
        if any(n.id == p["id"] for n in order_nodes)
    )
    print(
        f">> Time-budgeted: visited {len(order_nodes)} POIs "
        f"(value={total_value}); drive={actual_hours:.1f}h vs "
        f"budget={budget_hours:.1f}h ({overage_h:+.1f}h)"
    )

    return SolveResult(
        order=order_nodes,
        total_cost=total_cost,
        leg_costs=leg_costs,
        states_covered=states_covered,
        status="SUCCESS",
        runtime_seconds=runtime,
    )


def solve_with_config(
    config,
    pois: list[dict],
    durations,
    distances,
):
    """Solve the TSP defined by `config` over `pois` with `durations`/
    `distances` matrices. Returns a SolveResult. See spec §6.

    Tier 2 has two solver MODES:
      - State-coverage (default): visit ≥1 POI per state in `config.states`,
        minimize total travel time. The "Tier 1 with filters" semantic.
      - Time-budgeted: when `config.total_trip_days is not None`, switch
        to score-maximization-within-soft-budget. POI value is computed
        via poi_priority → category_priority → 0. The solver picks the
        subset that maximizes total value while keeping total drive time
        near `total_trip_days * max_hours_per_day` hours (soft cap;
        overage costs `time_budget_overage_penalty` per hour). `states`
        becomes a geographic filter at fetch-time only — coverage is no
        longer required.

    Tier 1 callers continue to use the original solve() unchanged — this
    wrapper is for config-driven trips only.
    """
    if config.total_trip_days is not None:
        return _solve_time_budgeted(config, pois, durations, distances)

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    nodes = [Node(id=p["id"], state=p["state"]) for p in pois]
    n = len(nodes)
    depot_index = _depot_index_for_config(config, pois)

    pois_states = {p["state"] for p in pois}
    if config.states is not None:
        required = set(config.states) & pois_states
    else:
        required = pois_states

    manager = pywrapcp.RoutingIndexManager(n, 1, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    cost_scale = 1000  # millisecond precision (existing solver convention)

    # Pre-round the scaled cost matrix to match solve()'s behaviour exactly.
    # solve() does: scaled = (distance_matrix * COST_SCALE).round().astype(int64)
    # Using round() rather than truncation keeps the two cost models numerically
    # identical, which gives GLS the same objective landscape.
    scaled_durations = (np.asarray(durations) * cost_scale).round().astype(np.int64)

    def time_callback(from_idx, to_idx):
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        return int(scaled_durations[i, j])

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Build state→node-indices map in pois list order (stable, matches solve()).
    # Iterating a bare `required` set gives hash-ordered output which changes
    # which disjunction PATH_CHEAPEST_ARC seeds first and can shift GLS.
    state_to_indices: dict[str, list[int]] = {}
    for i, p in enumerate(pois):
        state_to_indices.setdefault(p["state"], []).append(i)

    SKIP_PENALTY = 10**12
    depot_state = pois[depot_index]["state"]
    cp = routing.solver()
    for state, node_indices in state_to_indices.items():  # stable pois-order
        non_depot = [i for i in node_indices if i != depot_index]
        routing_indices = [manager.NodeToIndex(i) for i in non_depot]
        if not routing_indices:
            continue
        if state in required:
            if state == depot_state:
                # Depot already covers this state; its node cannot be placed in
                # a disjunction (OR-Tools routing constraint — depot must be
                # free). Make all same-state non-depot nodes individually
                # optional (0 penalty to skip), matching solve() capped mode.
                for ri in routing_indices:
                    routing.AddDisjunction([ri], 0)
            else:
                routing.AddDisjunction(routing_indices, SKIP_PENALTY, 1)
        else:
            # Non-required state: soft-optional + hard CP forbid, matching
            # solve() capped mode. Removes nodes from GLS search space entirely.
            #
            # EXCEPTION: must_include POIs in a non-required state. The
            # must_include loop below adds ActiveVar == 1 for those nodes
            # — adding ActiveVar == 0 here too would be a hard contradiction
            # and OR-Tools would return FAIL with an empty solution. Skip
            # them here and let the must_include loop force the visit.
            for i_poi in non_depot:
                if pois[i_poi]["id"] in config.must_include:
                    continue
                ri = manager.NodeToIndex(i_poi)
                routing.AddDisjunction([ri], 0)
                cp.Add(routing.ActiveVar(ri) == 0)

    # max_stops: soft penalty per stop beyond num_required, scaled in
    # cost-scaled seconds. Per spec §6.2, penalty = 1 hour worth of
    # cost-scaled units = 3600 * cost_scale. This makes adding a stop
    # only worth it if it shortens the tour by >= 1 hour.
    if config.max_stops is not None:
        excess_penalty = 3600 * cost_scale  # 1 hour in scaled units
        # For each NON-must-include, NON-required-state POI, add a
        # disjunction with the excess penalty so the solver pays this
        # cost per added optional stop.
        for i, p in enumerate(pois):
            already_constrained = (
                p["state"] in required  # in a state disjunction already
                or p["id"] in config.must_include  # in a hard constraint
            )
            if not already_constrained:
                # Optional stop: rebuild as penalty-disjunction
                # (overrides the earlier 0-penalty one we added for non-required)
                routing.AddDisjunction([manager.NodeToIndex(i)], excess_penalty, 1)

    # must_include: hard constraint — these nodes MUST be visited
    #
    # F8 fix: when a must_include POI is in a state that's also in
    # `required`, the state disjunction (max_cardinality=1) silently
    # excludes every OTHER POI in that state. The forced POI becomes
    # THE representative for that state. This is correct behavior but
    # invisible — print a diagnostic so the user understands why their
    # preferred-looking alternative POI in the same state didn't show
    # up in the tour.
    for must_id in config.must_include:
        for i, p in enumerate(pois):
            if p["id"] == must_id:
                node_idx = manager.NodeToIndex(i)
                routing.solver().Add(routing.ActiveVar(node_idx) == 1)
                if p["state"] in required:
                    other_in_state = [
                        q for q in pois
                        if q["state"] == p["state"] and q["id"] != must_id
                    ]
                    if other_in_state:
                        print(
                            f">> must_include[{must_id}] ({p['name']!r}) forces "
                            f"it as the {p['state']} representative; "
                            f"{len(other_in_state)} other {p['state']} POI(s) "
                            f"cannot be selected for this trip."
                        )
                break

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(int(config.time_limit_seconds))

    import time
    t0 = time.perf_counter()
    solution = routing.SolveWithParameters(search_params)
    runtime = time.perf_counter() - t0

    if solution is None:
        return SolveResult(
            order=[], total_cost=float("inf"), leg_costs=[],
            states_covered=set(), status="FAILED", runtime_seconds=runtime,
        )

    # Walk the solution, collecting visited node indices.
    index = routing.Start(0)
    visited_node_indices: list[int] = []
    while not routing.IsEnd(index):
        visited_node_indices.append(manager.IndexToNode(index))
        index = solution.Value(routing.NextVar(index))

    # Read leg costs directly from durations (raw precision, matching solve()).
    leg_costs: list[float] = []
    for i in range(len(visited_node_indices) - 1):
        a = visited_node_indices[i]
        b = visited_node_indices[i + 1]
        leg_costs.append(float(durations[a][b]))
    # Closing leg (last → depot), but only if config.loop is True. Open
    # paths exclude the return-to-depot leg. The OR-Tools solver itself
    # internally still solves a cycle — we're just not charging the user
    # for the return leg in the reported total.
    if config.loop:
        leg_costs.append(float(durations[visited_node_indices[-1]][depot_index]))

    order_nodes = [nodes[i] for i in visited_node_indices]
    total_cost = sum(leg_costs)
    states_covered = {n.state for n in order_nodes}

    if config.max_stops is not None and len(order_nodes) > config.max_stops:
        # The penalty should keep us under the cap; this is a defensive check.
        # If it ever fires, the penalty needs tuning upward.
        import warnings
        warnings.warn(
            f"Tour has {len(order_nodes)} stops, exceeding max_stops="
            f"{config.max_stops}. Consider raising excess_stop_penalty.",
            UserWarning, stacklevel=2,
        )

    return SolveResult(
        order=order_nodes,
        total_cost=total_cost,
        leg_costs=leg_costs,
        states_covered=states_covered,
        status="SUCCESS",
        runtime_seconds=runtime,
    )
