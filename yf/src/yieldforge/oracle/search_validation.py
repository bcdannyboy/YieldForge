"""Calibration-only exact finite search for minimal M9 validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from yieldforge.baseline.contracts import M7ActionKind
from yieldforge.baseline.replay import (
    M7ActionCatalog,
    M7ReplayCursor,
    M7ReplayRuntime,
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    run_m7_continuation,
)
from yieldforge.replay.contracts import rounded_cost


class _FutureVisibility(Protocol):
    def visible_suffix(self, *, current_position: int) -> tuple[object, ...]: ...


class M9ExactSearchRequest(Protocol):
    runtime: M7ReplayRuntime
    cursor: M7ReplayCursor
    visibility: _FutureVisibility


@dataclass(frozen=True)
class M9ExactRootScore:
    """Exact terminal cost reachable from one root action."""

    action_id: str
    kind: M7ActionKind
    final_net_cost: float


@dataclass(frozen=True)
class M9ExactSearchTelemetry:
    """Bounded work counts for one complete finite search."""

    catalog_count: int
    explored_transition_count: int
    terminal_leaf_count: int
    peak_branching_factor: int
    truncated_catalog_count: int


@dataclass(frozen=True)
class M9ExactSearchResult:
    """Complete root score vector and globally optimal first-action set."""

    start_event_position: int
    stop_event_position: int
    include_terminal_credit: bool
    optimal_final_net_cost: float
    optimal_first_action_ids: tuple[str, ...]
    root_scores: tuple[M9ExactRootScore, ...]
    complete: bool
    telemetry: M9ExactSearchTelemetry


@dataclass
class _MutableTelemetry:
    catalog_count: int = 0
    explored_transition_count: int = 0
    terminal_leaf_count: int = 0
    peak_branching_factor: int = 0
    truncated_catalog_count: int = 0


def _terminal_cost(
    runtime: M7ReplayRuntime,
    cursor: M7ReplayCursor,
    stop_event_position: int,
    *,
    include_terminal_credit: bool,
) -> float:
    terminal = run_m7_continuation(
        runtime,
        cursor=cursor,
        stop_event_position=stop_event_position,
    )
    if terminal.events:
        raise RuntimeError("M9 exact terminalization replayed an event")
    cost = terminal.final_costs.net_cost
    if not include_terminal_credit:
        cost = rounded_cost(cost + terminal.final_costs.terminal_scrap_credit)
    return cost


def _complete_catalog(
    runtime: M7ReplayRuntime,
    cursor: M7ReplayCursor,
    telemetry: _MutableTelemetry,
) -> M7ActionCatalog:
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)
    telemetry.catalog_count += 1
    telemetry.peak_branching_factor = max(
        telemetry.peak_branching_factor,
        len(catalog.actions),
    )
    truncated = catalog.generated.fit_search_budget_truncated_count
    telemetry.truncated_catalog_count += truncated
    if truncated:
        raise ValueError("M9 exact search encountered a truncated action catalog")
    if not catalog.actions:
        raise ValueError("M9 exact search encountered an empty action catalog")
    return catalog


def solve_exact_search(
    request: M9ExactSearchRequest,
    *,
    include_terminal_credit: bool = True,
) -> M9ExactSearchResult:
    """Exhaustively reoptimize every visible future M7 decision."""

    runtime = request.runtime
    root_cursor = request.cursor
    start = root_cursor.next_event_position
    visible_suffix = request.visibility.visible_suffix(current_position=start)
    stop = start + 1 + len(visible_suffix)
    if stop > len(runtime.replay_input.instances):
        raise ValueError("M9 exact search visibility extends beyond the replay stream")

    telemetry = _MutableTelemetry()

    def recurse(cursor: M7ReplayCursor) -> float:
        if cursor.next_event_position == stop:
            telemetry.terminal_leaf_count += 1
            return _terminal_cost(
                runtime,
                cursor,
                stop,
                include_terminal_credit=include_terminal_credit,
            )
        if cursor.next_event_position > stop:
            raise RuntimeError("M9 exact search advanced beyond the visible suffix")
        catalog = _complete_catalog(runtime, cursor, telemetry)
        branch_costs: list[float] = []
        for descriptor in catalog.actions:
            telemetry.explored_transition_count += 1
            step = apply_m7_action_descriptor(
                runtime,
                cursor=cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=(f"m9_exact_action_id={descriptor.action_id}",),
            )
            branch_costs.append(recurse(step.cursor))
        return min(branch_costs)

    root_catalog = _complete_catalog(runtime, root_cursor, telemetry)
    root_scores = []
    for descriptor in root_catalog.actions:
        telemetry.explored_transition_count += 1
        step = apply_m7_action_descriptor(
            runtime,
            cursor=root_cursor,
            catalog=root_catalog,
            descriptor=descriptor,
            decision_key=(f"m9_exact_action_id={descriptor.action_id}",),
        )
        root_scores.append(
            M9ExactRootScore(
                action_id=descriptor.action_id,
                kind=descriptor.kind,
                final_net_cost=recurse(step.cursor),
            )
        )

    score_tuple = tuple(root_scores)
    optimum = min(item.final_net_cost for item in score_tuple)
    immutable_telemetry = M9ExactSearchTelemetry(
        catalog_count=telemetry.catalog_count,
        explored_transition_count=telemetry.explored_transition_count,
        terminal_leaf_count=telemetry.terminal_leaf_count,
        peak_branching_factor=telemetry.peak_branching_factor,
        truncated_catalog_count=telemetry.truncated_catalog_count,
    )
    return M9ExactSearchResult(
        start_event_position=start,
        stop_event_position=stop,
        include_terminal_credit=include_terminal_credit,
        optimal_final_net_cost=optimum,
        optimal_first_action_ids=tuple(
            item.action_id for item in score_tuple if item.final_net_cost == optimum
        ),
        root_scores=score_tuple,
        complete=immutable_telemetry.truncated_catalog_count == 0,
        telemetry=immutable_telemetry,
    )


__all__ = [
    "M9ExactRootScore",
    "M9ExactSearchRequest",
    "M9ExactSearchResult",
    "M9ExactSearchTelemetry",
    "solve_exact_search",
]
