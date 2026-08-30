"""Exact deterministic bipartite matching for M11 Gate 2 opportunity accounting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

MAX_EDGE_REWARD_MICRO_UNITS = 2**63 - 1


def _require_identifier(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"matching {field_name} must be a string")
    if not value or value.strip() != value:
        raise ValueError(f"matching {field_name} must be a nonempty trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class MatchingEdge:
    """One exact integer-reward edge in a bipartite opportunity graph."""

    edge_id: str
    origin_id: str
    target_id: str
    reward_micro_units: int

    def __post_init__(self) -> None:
        _require_identifier(self.edge_id, field_name="edge ID")
        _require_identifier(self.origin_id, field_name="origin ID")
        _require_identifier(self.target_id, field_name="target ID")
        if type(self.reward_micro_units) is not int:
            raise TypeError("matching rewards must be exact integer micro-units")
        if abs(self.reward_micro_units) > MAX_EDGE_REWARD_MICRO_UNITS:
            raise ValueError("matching reward exceeds the bounded signed 64-bit micro-unit range")


@dataclass(frozen=True, slots=True)
class MatchingResult:
    """The selected edge IDs and their exact unscaled total reward."""

    selected_edge_ids: tuple[str, ...]
    total_reward_micro_units: int

    def __post_init__(self) -> None:
        if (
            type(self.selected_edge_ids) is not tuple
            or any(type(edge_id) is not str for edge_id in self.selected_edge_ids)
            or tuple(sorted(self.selected_edge_ids)) != self.selected_edge_ids
            or len(set(self.selected_edge_ids)) != len(self.selected_edge_ids)
        ):
            raise ValueError("selected matching edge IDs must be a unique sorted tuple")
        if type(self.total_reward_micro_units) is not int:
            raise TypeError("matching total reward must be exact integer micro-units")
        if self.total_reward_micro_units < 0:
            raise ValueError("matching total reward cannot be negative")


@dataclass(slots=True)
class _ResidualArc:
    to_node: int
    reverse_index: int
    cost: int
    capacity: int


def _add_residual_arc(
    graph: list[list[_ResidualArc]], from_node: int, to_node: int, *, cost: int
) -> int:
    forward_index = len(graph[from_node])
    reverse_index = len(graph[to_node])
    graph[from_node].append(
        _ResidualArc(
            to_node=to_node,
            reverse_index=reverse_index,
            cost=cost,
            capacity=1,
        )
    )
    graph[to_node].append(
        _ResidualArc(
            to_node=from_node,
            reverse_index=forward_index,
            cost=-cost,
            capacity=0,
        )
    )
    return forward_index


def _shortest_residual_path(
    graph: list[list[_ResidualArc]], source: int, sink: int
) -> tuple[int | None, list[tuple[int, int] | None]]:
    """Find one exact minimum-cost residual path with deterministic Bellman-Ford scans."""

    distances: list[int | None] = [None] * len(graph)
    predecessors: list[tuple[int, int] | None] = [None] * len(graph)
    distances[source] = 0

    for _ in range(len(graph) - 1):
        changed = False
        for from_node, arcs in enumerate(graph):
            from_distance = distances[from_node]
            if from_distance is None:
                continue
            for arc_index, arc in enumerate(arcs):
                if arc.capacity == 0:
                    continue
                candidate_distance = from_distance + arc.cost
                current_distance = distances[arc.to_node]
                if current_distance is None or candidate_distance < current_distance:
                    distances[arc.to_node] = candidate_distance
                    predecessors[arc.to_node] = (from_node, arc_index)
                    changed = True
        if not changed:
            break

    return distances[sink], predecessors


def _augment_path(
    graph: list[list[_ResidualArc]],
    predecessors: list[tuple[int, int] | None],
    *,
    source: int,
    sink: int,
) -> None:
    node = sink
    while node != source:
        predecessor = predecessors[node]
        if predecessor is None:  # pragma: no cover - guarded by a finite sink distance
            raise RuntimeError("matching residual path is incomplete")
        from_node, arc_index = predecessor
        arc = graph[from_node][arc_index]
        arc.capacity = 0
        graph[node][arc.reverse_index].capacity = 1
        node = from_node


def _materialize_ids(values: Iterable[str], *, kind: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"matching {kind} IDs must be an iterable of identifiers")
    materialized = tuple(values)
    for value in materialized:
        _require_identifier(value, field_name=f"{kind} ID")
    if len(set(materialized)) != len(materialized):
        raise ValueError(f"matching has a duplicate {kind} ID")
    return tuple(sorted(materialized))


def maximum_reward_matching(
    *,
    origin_ids: Iterable[str],
    target_ids: Iterable[str],
    edges: Iterable[MatchingEdge],
) -> MatchingResult:
    """Return the exact maximum-reward one-to-one matching.

    Nonpositive edges are intentionally unavailable, so leaving either endpoint
    unmatched is always permitted. Among equal-reward matchings, the selected
    edge-ID tuple is lexicographically smallest, independently of input order.
    """

    origins = _materialize_ids(origin_ids, kind="origin")
    targets = _materialize_ids(target_ids, kind="target")
    if isinstance(edges, (str, bytes)):
        raise TypeError("matching edges must be an iterable of MatchingEdge values")
    edge_values = tuple(edges)
    if any(type(edge) is not MatchingEdge for edge in edge_values):
        raise TypeError("matching edges must contain only MatchingEdge values")

    edge_ids = tuple(edge.edge_id for edge in edge_values)
    if len(set(edge_ids)) != len(edge_ids):
        raise ValueError("matching has a duplicate edge ID")
    origin_set = set(origins)
    target_set = set(targets)
    for edge in edge_values:
        if edge.origin_id not in origin_set:
            raise ValueError(f"matching edge {edge.edge_id!r} references an unknown origin")
        if edge.target_id not in target_set:
            raise ValueError(f"matching edge {edge.edge_id!r} references an unknown target")

    positive_edges = tuple(
        sorted(
            (edge for edge in edge_values if edge.reward_micro_units > 0),
            key=lambda edge: edge.edge_id,
        )
    )
    if not origins or not targets or not positive_edges:
        return MatchingResult(selected_edge_ids=(), total_reward_micro_units=0)

    source = 0
    origin_offset = 1
    target_offset = origin_offset + len(origins)
    sink = target_offset + len(targets)
    graph: list[list[_ResidualArc]] = [[] for _ in range(sink + 1)]
    origin_nodes = {origin_id: origin_offset + index for index, origin_id in enumerate(origins)}
    target_nodes = {target_id: target_offset + index for index, target_id in enumerate(targets)}

    for origin_id in origins:
        _add_residual_arc(graph, source, origin_nodes[origin_id], cost=0)

    # Binary tie bonuses make the earliest differing edge ID dominate every
    # later ID. The scale is larger than the sum of all bonuses, so one raw
    # reward micro-unit always dominates the complete lexicographic tiebreak.
    tie_scale = 1 << len(positive_edges)
    edge_arcs: dict[str, tuple[int, int]] = {}
    edge_by_id = {edge.edge_id: edge for edge in positive_edges}
    for edge_index, edge in enumerate(positive_edges):
        tie_bonus = 1 << (len(positive_edges) - edge_index - 1)
        composite_reward = edge.reward_micro_units * tie_scale + tie_bonus
        from_node = origin_nodes[edge.origin_id]
        arc_index = _add_residual_arc(
            graph,
            from_node,
            target_nodes[edge.target_id],
            cost=-composite_reward,
        )
        edge_arcs[edge.edge_id] = (from_node, arc_index)

    for target_id in targets:
        _add_residual_arc(graph, target_nodes[target_id], sink, cost=0)

    while True:
        path_cost, predecessors = _shortest_residual_path(graph, source, sink)
        if path_cost is None or path_cost >= 0:
            break
        _augment_path(graph, predecessors, source=source, sink=sink)

    selected_edge_ids = tuple(
        edge_id
        for edge_id in sorted(edge_arcs)
        if graph[edge_arcs[edge_id][0]][edge_arcs[edge_id][1]].capacity == 0
    )
    total_reward = sum(edge_by_id[edge_id].reward_micro_units for edge_id in selected_edge_ids)
    return MatchingResult(
        selected_edge_ids=selected_edge_ids,
        total_reward_micro_units=total_reward,
    )


__all__ = [
    "MAX_EDGE_REWARD_MICRO_UNITS",
    "MatchingEdge",
    "MatchingResult",
    "maximum_reward_matching",
]
