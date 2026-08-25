"""Deliberately slow correctness reference for exact M8 action scoring."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from yieldforge.baseline.replay import (
    M7ReplayCursor,
    M7ReplayRuntime,
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle.contracts import M8ActionScore, M8OracleDecision, build_oracle_decision
from yieldforge.oracle.visibility import FutureVisibility


@dataclass(frozen=True)
class M8OracleRequest:
    runtime: M7ReplayRuntime
    cursor: M7ReplayCursor
    visibility: FutureVisibility


@dataclass(frozen=True)
class M8ReferenceResult:
    decision: M8OracleDecision
    continuation_event_executions: int


def _isolated_runtime(
    source: M7ReplayRuntime,
    *,
    standard_profile_cache,  # type: ignore[no-untyped-def]
    shared_fit_search_cache,  # type: ignore[no-untyped-def]
    prepared_layout_cache,  # type: ignore[no-untyped-def]
) -> M7ReplayRuntime:
    return M7ReplayRuntime(
        replay_input=source.replay_input,
        runtime_candidates=source.runtime_candidates,
        rules=source.rules,
        runtime_metrics=source.runtime_metrics,
        standard_profile_cache=standard_profile_cache,
        shared_fit_search_cache=shared_fit_search_cache,
        prepared_layout_cache=prepared_layout_cache,
        standard_profile_executor=source.standard_profile_executor,
        jagua_executable=source.jagua_executable,
        jagua_differential_audit=source.jagua_differential_audit,
    )


def _visible_stop(request: M8OracleRequest, *, event_position: int) -> int:
    visible = request.visibility.visible_suffix(current_position=event_position)
    registered = request.runtime.replay_input.instances
    expected = registered[event_position + 1 : event_position + 1 + len(visible)]
    if visible != expected:
        raise ValueError("M8 visibility provider returned a non-prefix or mutated suffix")
    return event_position + 1 + len(visible)


def score_reference_action(
    request: M8OracleRequest,
    *,
    action_id: str,
) -> M8ActionScore:
    """Score one catalog action through the unchanged exact M7 continuation."""

    catalog = enumerate_m7_action_catalog(request.runtime, cursor=request.cursor)
    descriptors = tuple(item for item in catalog.actions if item.action_id == action_id)
    if len(descriptors) != 1:
        raise ValueError("M8 reference action is absent from the exact current catalog")
    step = apply_m7_action_descriptor(
        request.runtime,
        cursor=request.cursor,
        catalog=catalog,
        descriptor=descriptors[0],
        decision_key=(f"m8_hypothetical_action_id={action_id}",),
    )
    continuation = run_m7_continuation(
        _isolated_runtime(
            request.runtime,
            standard_profile_cache=request.runtime.standard_profile_cache,
            shared_fit_search_cache=request.runtime.shared_fit_search_cache or {},
            prepared_layout_cache=request.runtime.prepared_layout_cache or OrderedDict(),
        ),
        cursor=step.cursor,
        stop_event_position=_visible_stop(request, event_position=catalog.event_position),
    )
    return M8ActionScore(
        action_id=action_id,
        final_net_cost=continuation.final_costs.net_cost,
    )


def score_reference_event(request: M8OracleRequest) -> M8ReferenceResult:
    """Score every exact current action by isolated suffix replay."""

    catalog = enumerate_m7_action_catalog(request.runtime, cursor=request.cursor)
    fallback = select_m7_fallback(catalog, policy=request.runtime.replay_input.policy)
    stop = _visible_stop(request, event_position=catalog.event_position)
    action_scores = tuple(
        score_reference_action(request, action_id=descriptor.action_id)
        for descriptor in catalog.actions
    )
    decision = build_oracle_decision(
        baseline_action_id=fallback.action_id,
        expected_action_ids=tuple(item.action_id for item in catalog.actions),
        scores=tuple((item.action_id, item.final_net_cost) for item in action_scores),
    )
    return M8ReferenceResult(
        decision=decision,
        continuation_event_executions=(
            len(catalog.actions) * (stop - catalog.event_position - 1)
        ),
    )


__all__ = [
    "M8OracleRequest",
    "M8ReferenceResult",
    "score_reference_action",
    "score_reference_event",
]
