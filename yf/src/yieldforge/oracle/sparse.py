"""Certificate-based exact delta evaluator for M8 current-action rollouts."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from yieldforge.baseline.replay import (
    M7ActionCatalog,
    M7ReplayRuntime,
    M7StepResult,
    apply_m7_action_descriptor,
    apply_m7_frozen_action_evidence,
    enumerate_m7_action_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle.certificates import (
    ValidatedCommonTransition,
    build_validated_m8_common_transition,
    certify_event_passivity,
)
from yieldforge.oracle.contracts import M8ActionScore, M8OracleDecision, build_oracle_decision
from yieldforge.oracle.proofs import (
    M8ActionProof,
    M8EventWitness,
    build_m8_action_proof,
    m8_suffix_sha256,
)
from yieldforge.oracle.reference import M8OracleRequest


@dataclass(frozen=True)
class M8SparseMetrics:
    common_continuation_event_count: int
    exact_branch_event_count: int
    skipped_passive_event_count: int
    rejection_certificate_count: int
    survivor_pair_count: int
    state_rejoin_count: int


@dataclass(frozen=True)
class M8SparseResult:
    decision: M8OracleDecision
    proofs: tuple[M8ActionProof, ...]
    metrics: M8SparseMetrics


@dataclass(frozen=True)
class M8CertificateActionResult:
    score: M8ActionScore
    proof: M8ActionProof
    exact_branch_event_count: int
    skipped_passive_event_count: int
    rejection_certificate_count: int
    survivor_pair_count: int
    state_rejoin_count: int


@dataclass(frozen=True)
class _CommonPath:
    catalog: M7ActionCatalog
    initial_step: M7StepResult
    transitions: tuple[ValidatedCommonTransition, ...]
    stop_event_position: int
    semantic_runtime_sha256: str
    suffix_sha256: str


def _isolated_runtime(source: M7ReplayRuntime) -> M7ReplayRuntime:
    return M7ReplayRuntime(
        replay_input=source.replay_input,
        runtime_candidates=source.runtime_candidates,
        rules=source.rules,
        runtime_metrics=source.runtime_metrics,
        standard_profile_cache=source.standard_profile_cache,
        shared_fit_search_cache=source.shared_fit_search_cache or {},
        prepared_layout_cache=source.prepared_layout_cache or OrderedDict(),
        standard_profile_executor=source.standard_profile_executor,
        jagua_executable=source.jagua_executable,
        jagua_differential_audit=source.jagua_differential_audit,
    )


def _build_common_path(
    request: M8OracleRequest,
    *,
    catalog: M7ActionCatalog,
) -> _CommonPath:
    runtime = request.runtime
    fallback = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(
        item for item in catalog.actions if item.action_id == fallback.action_id
    )
    initial_step = apply_m7_action_descriptor(
        runtime,
        cursor=request.cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=fallback.decision_key,
    )
    visible = request.visibility.visible_suffix(current_position=catalog.event_position)
    registered = runtime.replay_input.instances
    expected = registered[
        catalog.event_position + 1 : catalog.event_position + 1 + len(visible)
    ]
    if visible != expected:
        raise ValueError("M8 visibility provider returned a non-prefix or mutated suffix")
    stop = catalog.event_position + 1 + len(visible)
    semantic_runtime = m7_semantic_runtime_sha256(runtime)
    transitions = []
    cursor = initial_step.cursor
    while cursor.next_event_position < stop:
        common = build_validated_m8_common_transition(runtime, cursor=cursor)
        transitions.append(common)
        cursor = common.step.cursor
    return _CommonPath(
        catalog=catalog,
        initial_step=initial_step,
        transitions=tuple(transitions),
        stop_event_position=stop,
        semantic_runtime_sha256=semantic_runtime,
        suffix_sha256=m8_suffix_sha256(
            semantic_runtime_sha256=semantic_runtime,
            start_event_position=catalog.event_position,
            stop_event_position=stop,
            bindings=visible,
        ),
    )


def score_certificate_action(
    request: M8OracleRequest,
    *,
    action_id: str,
    common: _CommonPath | None = None,
) -> M8CertificateActionResult:
    """Generate one exact score and complete checkable proof."""

    path = common or _build_common_path(
        request,
        catalog=enumerate_m7_action_catalog(request.runtime, cursor=request.cursor),
    )
    matching = tuple(item for item in path.catalog.actions if item.action_id == action_id)
    if len(matching) != 1:
        raise ValueError("M8 certificate action is absent from the exact current catalog")
    descriptor = matching[0]
    initial = apply_m7_action_descriptor(
        request.runtime,
        cursor=request.cursor,
        catalog=path.catalog,
        descriptor=descriptor,
        decision_key=(f"m8_hypothetical_action_id={action_id}",),
    )
    cursor = initial.cursor
    witnesses = []
    exact_count = 0
    skipped_count = 0
    rejection_count = 0
    survivor_count = 0
    rejoin_count = 0

    for common_transition in path.transitions:
        fact = common_transition.fact
        state_before = m7_cursor_sha256(cursor)
        if cursor == fact.cursor_before:
            cursor = apply_m7_frozen_action_evidence(
                request.runtime,
                cursor=cursor,
                event_position=fact.event_position,
                action=fact.step.event.action,
            )
            witness = M8EventWitness(
                event_position=fact.event_position,
                classification="state_rejoin",
                common_action_id=fact.step.event.action.action_id,
                branch_action_id=fact.step.event.action.action_id,
                state_before_sha256=state_before,
                state_after_sha256=m7_cursor_sha256(cursor),
            )
            rejoin_count += 1
        else:
            passivity = certify_event_passivity(
                request.runtime,
                common=common_transition,
                branch_cursor=cursor,
            )
            survivor_count += passivity.exact_search_count
            if passivity.passive:
                if passivity.witness is None:
                    raise ValueError("M8 passive result lacks its event witness")
                cursor = apply_m7_frozen_action_evidence(
                    request.runtime,
                    cursor=cursor,
                    event_position=fact.event_position,
                    action=fact.step.event.action,
                )
                witness = passivity.witness
                if witness.state_after_sha256 != m7_cursor_sha256(cursor):
                    raise ValueError("M8 generated certificate state differs after application")
                skipped_count += 1
                rejection_count += len(witness.influences)
            else:
                branch_catalog = enumerate_m7_action_catalog(
                    request.runtime,
                    cursor=cursor,
                    complete=False,
                )
                selection = select_m7_fallback(
                    branch_catalog,
                    policy=request.runtime.replay_input.policy,
                )
                branch_descriptor = next(
                    item
                    for item in branch_catalog.actions
                    if item.action_id == selection.action_id
                )
                branch_step = apply_m7_action_descriptor(
                    request.runtime,
                    cursor=cursor,
                    catalog=branch_catalog,
                    descriptor=branch_descriptor,
                    decision_key=selection.decision_key,
                )
                cursor = branch_step.cursor
                witness = M8EventWitness(
                    event_position=fact.event_position,
                    classification="exact_transition",
                    common_action_id=fact.step.event.action.action_id,
                    branch_action_id=branch_step.event.action.action_id,
                    state_before_sha256=state_before,
                    state_after_sha256=m7_cursor_sha256(cursor),
                )
                exact_count += 1
        witnesses.append(witness)

    terminal = run_m7_continuation(
        _isolated_runtime(request.runtime),
        cursor=cursor,
        stop_event_position=path.stop_event_position,
    )
    proof = build_m8_action_proof(
        action_id=initial.event.action.action_id,
        catalog_action_id=descriptor.action_id,
        baseline_action_id=path.initial_step.event.action.action_id,
        baseline_catalog_action_id=path.initial_step.descriptor.action_id,
        start_event_position=path.catalog.event_position,
        stop_event_position=path.stop_event_position,
        suffix_sha256=path.suffix_sha256,
        semantic_runtime_sha256=path.semantic_runtime_sha256,
        start_state_sha256=m7_cursor_sha256(request.cursor),
        witnesses=tuple(witnesses),
        final_net_cost=terminal.final_costs.net_cost,
        final_state_sha256=m7_cursor_sha256(cursor),
    )
    return M8CertificateActionResult(
        score=M8ActionScore(
            action_id=descriptor.action_id,
            final_net_cost=terminal.final_costs.net_cost,
        ),
        proof=proof,
        exact_branch_event_count=exact_count,
        skipped_passive_event_count=skipped_count,
        rejection_certificate_count=rejection_count,
        survivor_pair_count=survivor_count,
        state_rejoin_count=rejoin_count,
    )


def score_sparse_event(request: M8OracleRequest) -> M8SparseResult:
    """Score every current action and emit one exact proof per action."""

    catalog = enumerate_m7_action_catalog(request.runtime, cursor=request.cursor)
    common = _build_common_path(request, catalog=catalog)
    action_results = tuple(
        score_certificate_action(request, action_id=item.action_id, common=common)
        for item in catalog.actions
    )
    decision = build_oracle_decision(
        baseline_action_id=common.initial_step.descriptor.action_id,
        expected_action_ids=tuple(item.action_id for item in catalog.actions),
        scores=tuple(
            (item.score.action_id, item.score.final_net_cost) for item in action_results
        ),
    )
    return M8SparseResult(
        decision=decision,
        proofs=tuple(item.proof for item in action_results),
        metrics=M8SparseMetrics(
            common_continuation_event_count=len(common.transitions),
            exact_branch_event_count=sum(
                item.exact_branch_event_count for item in action_results
            ),
            skipped_passive_event_count=sum(
                item.skipped_passive_event_count for item in action_results
            ),
            rejection_certificate_count=sum(
                item.rejection_certificate_count for item in action_results
            ),
            survivor_pair_count=sum(item.survivor_pair_count for item in action_results),
            state_rejoin_count=sum(item.state_rejoin_count for item in action_results),
        ),
    )


__all__ = [
    "M8CertificateActionResult",
    "M8SparseMetrics",
    "M8SparseResult",
    "score_certificate_action",
    "score_sparse_event",
]
