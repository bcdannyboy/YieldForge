"""Deliberately slow correctness reference for exact M8 action scoring."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from yieldforge.baseline.replay import (
    M7ReplayCursor,
    M7ReplayRuntime,
    apply_m7_action_descriptor,
    authoritative_m7_proof_runtime,
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


def _score_reference_action_from_catalog(
    request: M8OracleRequest,
    *,
    catalog,  # type: ignore[no-untyped-def]
    action_id: str,
    stop_event_position: int,
) -> M8ActionScore:
    """Score one catalog action through the unchanged exact M7 continuation."""

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
        stop_event_position=stop_event_position,
    )
    return M8ActionScore(
        action_id=action_id,
        final_net_cost=continuation.final_costs.net_cost,
    )


def score_reference_action(
    request: M8OracleRequest,
    *,
    action_id: str,
) -> M8ActionScore:
    """Score one action in a fresh cache-free stable semantic snapshot."""

    return score_reference_actions(request, action_ids=(action_id,))[0]


def score_reference_actions(
    request: M8OracleRequest,
    *,
    action_ids: tuple[str, ...],
) -> tuple[M8ActionScore, ...]:
    """Brute-score one ordered selected-action batch in a single stable snapshot."""

    if not action_ids or action_ids != tuple(dict.fromkeys(action_ids)):
        raise ValueError("M8 selected reference action IDs must be nonempty and unique")
    with authoritative_m7_proof_runtime(request.runtime) as authority:
        captured = M8OracleRequest(
            runtime=authority.runtime,
            cursor=request.cursor,
            visibility=request.visibility,
        )
        catalog = enumerate_m7_action_catalog(
            captured.runtime,
            cursor=captured.cursor,
        )
        present = {item.action_id for item in catalog.actions}
        if any(action_id not in present for action_id in action_ids):
            raise ValueError("M8 selected reference action is absent from the exact catalog")
        stop = _visible_stop(captured, event_position=catalog.event_position)
        return tuple(
            _score_reference_action_from_catalog(
                captured,
                catalog=catalog,
                action_id=action_id,
                stop_event_position=stop,
            )
            for action_id in action_ids
        )


def score_reference_event(request: M8OracleRequest) -> M8ReferenceResult:
    """Score every exact current action by isolated suffix replay."""

    with authoritative_m7_proof_runtime(request.runtime) as authority:
        captured = M8OracleRequest(
            runtime=authority.runtime,
            cursor=request.cursor,
            visibility=request.visibility,
        )
        catalog = enumerate_m7_action_catalog(
            captured.runtime,
            cursor=captured.cursor,
        )
        fallback = select_m7_fallback(
            catalog,
            policy=captured.runtime.replay_input.policy,
        )
        stop = _visible_stop(captured, event_position=catalog.event_position)
        action_scores = tuple(
            _score_reference_action_from_catalog(
                captured,
                catalog=catalog,
                action_id=descriptor.action_id,
                stop_event_position=stop,
            )
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
    "score_reference_actions",
    "score_reference_event",
]
