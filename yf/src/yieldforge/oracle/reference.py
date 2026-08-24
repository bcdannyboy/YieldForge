"""Deliberately slow correctness reference for exact M8 action scoring."""

from __future__ import annotations

from dataclasses import dataclass

from yieldforge.baseline.replay import (
    M7ReplayCursor,
    M7ReplayRuntime,
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle.contracts import M8OracleDecision, build_oracle_decision
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


def _isolated_runtime(source: M7ReplayRuntime) -> M7ReplayRuntime:
    return M7ReplayRuntime(
        replay_input=source.replay_input,
        runtime_candidates=source.runtime_candidates,
        rules=source.rules,
        runtime_metrics=source.runtime_metrics,
        standard_profile_executor=source.standard_profile_executor,
        jagua_executable=source.jagua_executable,
        jagua_differential_audit=source.jagua_differential_audit,
    )


def score_reference_event(request: M8OracleRequest) -> M8ReferenceResult:
    """Score every exact current action by isolated suffix replay."""

    catalog = enumerate_m7_action_catalog(request.runtime, cursor=request.cursor)
    fallback = select_m7_fallback(catalog, policy=request.runtime.replay_input.policy)
    visible = request.visibility.visible_suffix(current_position=catalog.event_position)
    registered = request.runtime.replay_input.instances
    expected = registered[
        catalog.event_position + 1 : catalog.event_position + 1 + len(visible)
    ]
    if visible != expected:
        raise ValueError("M8 visibility provider returned a non-prefix or mutated suffix")
    stop = catalog.event_position + 1 + len(visible)
    scores = []
    event_executions = 0
    for descriptor in catalog.actions:
        step = apply_m7_action_descriptor(
            request.runtime,
            cursor=request.cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
        )
        continuation = run_m7_continuation(
            _isolated_runtime(request.runtime),
            cursor=step.cursor,
            stop_event_position=stop,
        )
        scores.append((descriptor.action_id, continuation.final_costs.net_cost))
        event_executions += len(continuation.events)
    decision = build_oracle_decision(
        baseline_action_id=fallback.action_id,
        expected_action_ids=tuple(item.action_id for item in catalog.actions),
        scores=tuple(scores),
    )
    return M8ReferenceResult(
        decision=decision,
        continuation_event_executions=event_executions,
    )


__all__ = ["M8OracleRequest", "M8ReferenceResult", "score_reference_event"]
