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
