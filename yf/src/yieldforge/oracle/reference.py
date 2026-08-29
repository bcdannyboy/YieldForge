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
    m7_cursor_sha256,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle.compiled import M8PreparedFrontierIntegrityError
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


def _capture_reference_action_ids_source(action_ids: tuple[str, ...]) -> tuple[str, ...]:
    if type(action_ids) is not tuple:
        raise M8PreparedFrontierIntegrityError(
            "M8 reference action ID collection source differs"
        )
    captured = tuple(tuple.__iter__(action_ids))
    if any(type(action_id) is not str for action_id in captured):
        raise M8PreparedFrontierIntegrityError("M8 reference action ID source differs")
    if not captured or any(not action_id for action_id in captured) or len(captured) != len(
        set(captured)
    ):
        raise ValueError("M8 selected reference action IDs must be nonempty and unique")
    return captured


def _capture_reference_request_source(
    request: M8OracleRequest,
) -> tuple[M8OracleRequest, str, int]:
    """Capture public request semantics before the authoritative reference scope."""

    from yieldforge.oracle.certificates import (
        _capture_replay_cursor_commitment_source,
        _capture_replay_cursor_source,
        _capture_visible_suffix_source,
    )

    if type(request) is not M8OracleRequest:
        raise M8PreparedFrontierIntegrityError("M8 reference request source type differs")
    state = object.__getattribute__(request, "__dict__")
    if type(state) is not dict or set(state) != {"runtime", "cursor", "visibility"}:
        raise M8PreparedFrontierIntegrityError("M8 reference request source state differs")
    source_runtime = state["runtime"]
    source_cursor = state["cursor"]
    source_visibility = state["visibility"]
    if type(source_runtime) is not M7ReplayRuntime or type(source_cursor) is not M7ReplayCursor:
        raise M8PreparedFrontierIntegrityError("M8 reference request source graph differs")
    cursor_position, cursor_sha256 = _capture_replay_cursor_commitment_source(source_cursor)
    captured_visibility = _capture_visible_suffix_source(
        source_runtime,
        source_visibility,
        current_position=cursor_position,
    )
    current_state = object.__getattribute__(request, "__dict__")
    if (
        type(current_state) is not dict
        or current_state is not state
        or set(current_state) != {"runtime", "cursor", "visibility"}
        or current_state["runtime"] is not source_runtime
        or current_state["cursor"] is not source_cursor
        or current_state["visibility"] is not source_visibility
    ):
        raise M8PreparedFrontierIntegrityError("M8 reference request drifted during capture")
    captured_cursor = _capture_replay_cursor_source(source_cursor)
    if (
        captured_cursor.next_event_position != cursor_position
        or m7_cursor_sha256(captured_cursor) != cursor_sha256
    ):
        raise M8PreparedFrontierIntegrityError("M8 reference cursor drifted during capture")
    return (
        M8OracleRequest(
            runtime=source_runtime,
            cursor=captured_cursor,
            visibility=captured_visibility,
        ),
        captured_visibility.semantic_runtime_sha256,
        cursor_position + 1 + len(captured_visibility.bindings),
    )


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


def _score_reference_actions_event_major(
    request: M8OracleRequest,
    *,
    catalog,  # type: ignore[no-untyped-def]
    action_ids: tuple[str, ...],
    stop_event_position: int,
) -> tuple[M8ActionScore, ...]:
    """Replay independent exact branches one frozen M7 event at a time."""

    by_id = {item.action_id: item for item in catalog.actions}
    standard_profile_cache = request.runtime.standard_profile_cache
    shared_fit_search_cache = request.runtime.shared_fit_search_cache or {}
    prepared_layout_cache = request.runtime.prepared_layout_cache or OrderedDict()
    branches = []
    for action_id in action_ids:
        descriptor = by_id[action_id]
        step = apply_m7_action_descriptor(
            request.runtime,
            cursor=request.cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=(f"m8_hypothetical_action_id={action_id}",),
        )
        branches.append(
            (
                action_id,
                _isolated_runtime(
                    request.runtime,
                    standard_profile_cache=standard_profile_cache,
                    shared_fit_search_cache=shared_fit_search_cache,
                    prepared_layout_cache=prepared_layout_cache,
                ),
                step.cursor,
            )
        )

    while branches[0][2].next_event_position < stop_event_position:
        event_positions = {branch[2].next_event_position for branch in branches}
        if len(event_positions) != 1:
            raise ValueError("M8 reference branches lost event-major alignment")
        advanced = []
        for action_id, runtime, cursor in branches:
            branch_catalog = enumerate_m7_action_catalog(
                runtime,
                cursor=cursor,
                complete=False,
            )
            selection = select_m7_fallback(
                branch_catalog,
                policy=runtime.replay_input.policy,
            )
            descriptor = next(
                item
                for item in branch_catalog.actions
                if item.action_id == selection.action_id
            )
            step = apply_m7_action_descriptor(
                runtime,
                cursor=cursor,
                catalog=branch_catalog,
                descriptor=descriptor,
                decision_key=selection.decision_key,
            )
            advanced.append((action_id, runtime, step.cursor))
        branches = advanced

    return tuple(
        M8ActionScore(
            action_id=action_id,
            final_net_cost=run_m7_continuation(
                runtime,
                cursor=cursor,
                stop_event_position=stop_event_position,
            ).final_costs.net_cost,
        )
        for action_id, runtime, cursor in branches
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

    captured_action_ids = _capture_reference_action_ids_source(action_ids)
    source, semantic_runtime_sha256, stop = _capture_reference_request_source(request)
    with authoritative_m7_proof_runtime(source.runtime) as authority:
        if authority.semantic_sha256 != semantic_runtime_sha256:
            raise M8PreparedFrontierIntegrityError("M8 reference runtime source drifted")
        captured = M8OracleRequest(
            runtime=authority.runtime,
            cursor=source.cursor,
            visibility=source.visibility,
        )
        catalog = enumerate_m7_action_catalog(
            captured.runtime,
            cursor=captured.cursor,
        )
        present = {item.action_id for item in catalog.actions}
        if any(action_id not in present for action_id in captured_action_ids):
            raise ValueError("M8 selected reference action is absent from the exact catalog")
        if catalog.event_position != source.cursor.next_event_position:
            raise M8PreparedFrontierIntegrityError("M8 reference catalog position differs")
        return _score_reference_actions_event_major(
            captured,
            catalog=catalog,
            action_ids=captured_action_ids,
            stop_event_position=stop,
        )


def score_reference_event(request: M8OracleRequest) -> M8ReferenceResult:
    """Score every exact current action by isolated suffix replay."""

    source, semantic_runtime_sha256, stop = _capture_reference_request_source(request)
    with authoritative_m7_proof_runtime(source.runtime) as authority:
        if authority.semantic_sha256 != semantic_runtime_sha256:
            raise M8PreparedFrontierIntegrityError("M8 reference runtime source drifted")
        captured = M8OracleRequest(
            runtime=authority.runtime,
            cursor=source.cursor,
            visibility=source.visibility,
        )
        catalog = enumerate_m7_action_catalog(
            captured.runtime,
            cursor=captured.cursor,
        )
        fallback = select_m7_fallback(
            catalog,
            policy=captured.runtime.replay_input.policy,
        )
        if catalog.event_position != source.cursor.next_event_position:
            raise M8PreparedFrontierIntegrityError("M8 reference catalog position differs")
        action_scores = _score_reference_actions_event_major(
            captured,
            catalog=catalog,
            action_ids=tuple(descriptor.action_id for descriptor in catalog.actions),
            stop_event_position=stop,
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
