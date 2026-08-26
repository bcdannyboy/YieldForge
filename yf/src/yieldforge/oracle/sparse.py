"""Certificate-based exact delta evaluator for M8 current-action rollouts."""

from __future__ import annotations

import os
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from yieldforge.baseline.contracts import TemporalInstanceBinding
from yieldforge.baseline.replay import (
    M7ActionCatalog,
    M7ActionDescriptor,
    M7AuthoritativeProofRuntime,
    M7ReplayCursor,
    M7StepResult,
    apply_m7_action_descriptor,
    apply_m7_frozen_action_evidence,
    authoritative_m7_proof_runtime,
    enumerate_m7_action_catalog,
    m7_cursor_sha256,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle.certificates import (
    M8UncheckedInfluenceCapture,
    M8UncheckedProducerTransition,
    _capture_unchecked_event_passivity,
    _capture_unchecked_m8_common_transition,
    _derive_m8_common_transition_fact_unprofiled,
    _release_validated_common_transition,
    _validated_common_transition_fact,
    build_validated_m8_common_transition_in_context,
    certify_event_passivity,
)
from yieldforge.oracle.compiled import (
    _prepare_translation_layout_batch,
    _PreparedTranslationLayoutBatch,
)
from yieldforge.oracle.contracts import M8ActionScore, M8OracleDecision, build_oracle_decision
from yieldforge.oracle.prepared import prepared_context_fingerprint
from yieldforge.oracle.profiling import increment_profile_count, profile_phase
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
class M8CommonFactDifferentialAudit:
    """Exact portable identity established against authoritative frozen M7 replay."""

    event_position: int
    event_id: str
    content_sha256: str


@dataclass(frozen=True)
class M8CertificateActionResult:
    score: M8ActionScore
    proof: M8ActionProof
    exact_branch_event_count: int
    skipped_passive_event_count: int
    rejection_certificate_count: int
    survivor_pair_count: int
    state_rejoin_count: int


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _M8PreparedGeneratorContext:
    """Scoped cache-free generator state; invalid immediately after context exit."""

    _authority: M7AuthoritativeProofRuntime
    _request: M8OracleRequest
    _catalog: M7ActionCatalog
    _fallback_step: M7StepResult
    _visible: tuple[TemporalInstanceBinding, ...]
    _stop_event_position: int
    _suffix_sha256: str
    _prepared_layouts: _PreparedTranslationLayoutBatch

    def require_active(self) -> None:
        registered = _PREPARED_GENERATOR_REGISTRY.get(id(self))
        try:
            fingerprint = _generator_context_fingerprint(self)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("M8 prepared generator capability integrity differs") from error
        if (
            type(self) is not _M8PreparedGeneratorContext
            or registered is None
            or registered[0]() is not self
            or registered[1] != os.getpid()
            or registered[2] != id(self._authority)
            or registered[3] != fingerprint
        ):
            raise ValueError("M8 prepared generator capability is invalid or inactive")
        self._authority.require_active(self._request.runtime)

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared generator capabilities cannot be serialized")


_PREPARED_GENERATOR_REGISTRY: dict[
    int,
    tuple[weakref.ReferenceType[_M8PreparedGeneratorContext], int, int, str],
] = {}


def _generator_context_fingerprint(context: _M8PreparedGeneratorContext) -> str:
    return prepared_context_fingerprint(
        kind=f"generator:{id(context._prepared_layouts)}",  # noqa: SLF001
        context_id=id(context),
        authority=context._authority,
        request=context._request,
        catalog=context._catalog,
        fallback_step=context._fallback_step,
        visible=context._visible,
        stop_event_position=context._stop_event_position,
        suffix_sha256=context._suffix_sha256,
    )


@dataclass
class _BranchState:
    descriptor: M7ActionDescriptor
    initial_step: M7StepResult
    cursor: M7ReplayCursor
    witnesses: list[M8EventWitness] = field(default_factory=list)
    exact_count: int = 0
    skipped_count: int = 0
    rejection_count: int = 0
    survivor_count: int = 0
    rejoin_count: int = 0


@dataclass(frozen=True)
class M8UncheckedBranchEventCapture:
    """One explicitly unchecked producer traversal event."""

    event_position: int
    classification: Literal[
        "state_rejoin",
        "no_fit",
        "policy_dominated",
        "exact_transition",
    ]
    common_action_id: str
    branch_action_id: str
    state_before_sha256: str
    state_after_sha256: str
    branch_after: M7ReplayCursor
    influences: tuple[M8UncheckedInfluenceCapture, ...] = ()
    attempted_influences: tuple[M8UncheckedInfluenceCapture, ...] = ()
    exact_step: M7StepResult | None = None
    authority_mode: Literal["unchecked_portable"] = "unchecked_portable"

    def __post_init__(self) -> None:
        if self.state_after_sha256 != m7_cursor_sha256(self.branch_after):
            raise ValueError("M8 unchecked branch-after state commitment differs")
        if self.classification == "exact_transition":
            attempted_prefix_is_valid = not self.attempted_influences or (
                self.attempted_influences[-1].classification == "policy_not_dominated"
                and all(
                    item.classification in {"no_fit", "policy_dominated"}
                    for item in self.attempted_influences[:-1]
                )
            )
            if self.exact_step is None:
                raise ValueError("M8 unchecked exact transition lacks its source step")
            if self.influences:
                raise ValueError("M8 unchecked exact transition cannot claim passive influences")
            if (
                self.exact_step.cursor != self.branch_after
                or self.exact_step.event.sequence != self.event_position
                or self.exact_step.event.action.action_id != self.branch_action_id
                or not attempted_prefix_is_valid
            ):
                raise ValueError("M8 unchecked exact transition source bindings differ")
        elif self.exact_step is not None:
            raise ValueError("M8 unchecked passive event cannot carry an exact fallback step")
        elif self.attempted_influences:
            raise ValueError("M8 unchecked passive/rejoin event cannot carry attempted influences")
        if self.classification == "state_rejoin" and self.influences:
            raise ValueError("M8 unchecked rejoin event cannot carry passive influences")
        if self.classification in {"no_fit", "policy_dominated"} and (
            not self.influences
            or any(item.classification == "policy_not_dominated" for item in self.influences)
        ):
            raise ValueError("M8 unchecked passive event influence source differs")


@dataclass
class _UncheckedBranchState:
    descriptor: M7ActionDescriptor
    initial_step: M7StepResult
    cursor: M7ReplayCursor
    events: list[M8UncheckedBranchEventCapture] = field(default_factory=list)
    exact_count: int = 0
    skipped_count: int = 0
    rejection_count: int = 0
    survivor_count: int = 0
    rejoin_count: int = 0


@dataclass(frozen=True)
class M8UncheckedBranchTraversalCapture:
    """Frozen producer-only branch state after the captured common suffix."""

    descriptor: M7ActionDescriptor
    initial_step: M7StepResult
    cursor: M7ReplayCursor
    events: tuple[M8UncheckedBranchEventCapture, ...]
    exact_count: int
    skipped_count: int
    rejection_count: int
    survivor_count: int
    rejoin_count: int
    authority_mode: Literal["unchecked_portable"] = "unchecked_portable"


@dataclass(frozen=True)
class M8UncheckedTraversalCapture:
    """Event-major producer capture; it contains no accepted M8 proofs."""

    common_transitions: tuple[M8UncheckedProducerTransition, ...]
    branches: tuple[M8UncheckedBranchTraversalCapture, ...]
    authority_mode: Literal["unchecked_portable"] = "unchecked_portable"


@contextmanager
def _prepare_m8_generator_context(
    request: M8OracleRequest,
) -> Iterator[_M8PreparedGeneratorContext]:
    """Own one stable semantic snapshot for an event-major generator batch."""

    with authoritative_m7_proof_runtime(request.runtime) as authority:
        captured = M8OracleRequest(
            runtime=authority.runtime,
            cursor=request.cursor,
            visibility=request.visibility,
        )
        with profile_phase("action_catalog_enumeration"):
            catalog = enumerate_m7_action_catalog(
                captured.runtime,
                cursor=captured.cursor,
            )
        fallback = select_m7_fallback(
            catalog,
            policy=captured.runtime.replay_input.policy,
        )
        fallback_descriptor = next(
            item for item in catalog.actions if item.action_id == fallback.action_id
        )
        fallback_step = apply_m7_action_descriptor(
            captured.runtime,
            cursor=captured.cursor,
            catalog=catalog,
            descriptor=fallback_descriptor,
            decision_key=fallback.decision_key,
        )
        visible = captured.visibility.visible_suffix(current_position=catalog.event_position)
        registered = captured.runtime.replay_input.instances
        expected = registered[
            catalog.event_position + 1 : catalog.event_position + 1 + len(visible)
        ]
        if visible != expected:
            raise ValueError("M8 visibility provider returned a non-prefix or mutated suffix")
        stop = catalog.event_position + 1 + len(visible)
        event_positions = tuple(range(catalog.event_position + 1, stop))
        with _prepare_translation_layout_batch(
            authority.runtime,
            event_positions=event_positions,
        ) as prepared_layouts:
            context = _M8PreparedGeneratorContext(
                _authority=authority,
                _request=captured,
                _catalog=catalog,
                _fallback_step=fallback_step,
                _visible=visible,
                _stop_event_position=stop,
                _suffix_sha256=m8_suffix_sha256(
                    semantic_runtime_sha256=authority.semantic_sha256,
                    start_event_position=catalog.event_position,
                    stop_event_position=stop,
                    bindings=visible,
                ),
                _prepared_layouts=prepared_layouts,
            )
            key = id(context)

            def discard(
                reference: weakref.ReferenceType[_M8PreparedGeneratorContext],
            ) -> None:
                registered = _PREPARED_GENERATOR_REGISTRY.get(key)
                if registered is not None and registered[0] is reference:
                    _PREPARED_GENERATOR_REGISTRY.pop(key, None)

            reference = weakref.ref(context, discard)
            _PREPARED_GENERATOR_REGISTRY[key] = (
                reference,
                os.getpid(),
                id(authority),
                _generator_context_fingerprint(context),
            )
            try:
                yield context
            finally:
                integrity_error = None
                try:
                    context.require_active()
                except ValueError as error:
                    integrity_error = error
                registered = _PREPARED_GENERATOR_REGISTRY.get(key)
                if registered is not None and registered[0]() is context:
                    _PREPARED_GENERATOR_REGISTRY.pop(key, None)
                if integrity_error is not None:
                    raise integrity_error


def _initial_branch(
    context: _M8PreparedGeneratorContext,
    descriptor: M7ActionDescriptor,
) -> _BranchState:
    initial = apply_m7_action_descriptor(
        context._request.runtime,  # noqa: SLF001 - scoped prepared context.
        cursor=context._request.cursor,  # noqa: SLF001
        catalog=context._catalog,  # noqa: SLF001
        descriptor=descriptor,
        decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
    )
    return _BranchState(
        descriptor=descriptor,
        initial_step=initial,
        cursor=initial.cursor,
    )


def _initial_unchecked_branch(
    context: _M8PreparedGeneratorContext,
    descriptor: M7ActionDescriptor,
) -> _UncheckedBranchState:
    initial = apply_m7_action_descriptor(
        context._request.runtime,  # noqa: SLF001 - scoped prepared context.
        cursor=context._request.cursor,  # noqa: SLF001
        catalog=context._catalog,  # noqa: SLF001
        descriptor=descriptor,
        decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
    )
    return _UncheckedBranchState(
        descriptor=descriptor,
        initial_step=initial,
        cursor=initial.cursor,
    )


def _advance_unchecked_branch(
    context: _M8PreparedGeneratorContext,
    branch: _UncheckedBranchState,
    *,
    common: M8UncheckedProducerTransition,
) -> None:
    """Advance one branch from a producer record without accepting proof authority."""

    if type(common) is not M8UncheckedProducerTransition:
        raise ValueError("M8 producer traversal requires an unchecked common transition")
    runtime = context._request.runtime  # noqa: SLF001
    fact = common.common_fact
    if branch.cursor == fact.cursor_before:
        state_before = m7_cursor_sha256(branch.cursor)
        branch.cursor = apply_m7_frozen_action_evidence(
            runtime,
            cursor=branch.cursor,
            event_position=fact.event_position,
            action=fact.step.event.action,
        )
        captured = M8UncheckedBranchEventCapture(
            event_position=fact.event_position,
            classification="state_rejoin",
            common_action_id=fact.step.event.action.action_id,
            branch_action_id=fact.step.event.action.action_id,
            state_before_sha256=state_before,
            state_after_sha256=m7_cursor_sha256(branch.cursor),
            branch_after=branch.cursor,
        )
        branch.rejoin_count += 1
    else:
        passivity = _capture_unchecked_event_passivity(
            runtime,
            common=common,
            branch_cursor=branch.cursor,
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            runtime_authority=context._authority,  # noqa: SLF001
        )
        branch.survivor_count += passivity.exact_search_count
        if passivity.passive:
            if (
                passivity.classification is None
                or passivity.branch_after is None
                or passivity.state_before_sha256 is None
                or passivity.state_after_sha256 is None
            ):
                raise ValueError("M8 unchecked passive capture lacks its branch transition")
            branch.cursor = passivity.branch_after
            captured = M8UncheckedBranchEventCapture(
                event_position=fact.event_position,
                classification=passivity.classification,
                common_action_id=fact.step.event.action.action_id,
                branch_action_id=fact.step.event.action.action_id,
                state_before_sha256=passivity.state_before_sha256,
                state_after_sha256=passivity.state_after_sha256,
                branch_after=branch.cursor,
                influences=passivity.influences,
            )
            branch.skipped_count += 1
            branch.rejection_count += len(passivity.influences)
        else:
            state_before = m7_cursor_sha256(branch.cursor)
            with profile_phase("action_catalog_enumeration"):
                branch_catalog = enumerate_m7_action_catalog(
                    runtime,
                    cursor=branch.cursor,
                    complete=False,
                )
            increment_profile_count("fallbacks")
            selection = select_m7_fallback(
                branch_catalog,
                policy=runtime.replay_input.policy,
            )
            descriptor = next(
                item for item in branch_catalog.actions if item.action_id == selection.action_id
            )
            step = apply_m7_action_descriptor(
                runtime,
                cursor=branch.cursor,
                catalog=branch_catalog,
                descriptor=descriptor,
                decision_key=selection.decision_key,
            )
            branch.cursor = step.cursor
            captured = M8UncheckedBranchEventCapture(
                event_position=fact.event_position,
                classification="exact_transition",
                common_action_id=fact.step.event.action.action_id,
                branch_action_id=step.event.action.action_id,
                state_before_sha256=state_before,
                state_after_sha256=m7_cursor_sha256(branch.cursor),
                branch_after=branch.cursor,
                attempted_influences=passivity.influences,
                exact_step=step,
            )
            branch.exact_count += 1
    branch.events.append(captured)


def _capture_prepared_unchecked_traversal(
    context: _M8PreparedGeneratorContext,
    *,
    action_ids: tuple[str, ...] | None = None,
) -> M8UncheckedTraversalCapture:
    """Capture one event-major suffix without creating any trusted common capability."""

    if type(context) is not _M8PreparedGeneratorContext:
        raise ValueError("M8 prepared generator capability has the wrong type")
    context.require_active()
    requested = (
        tuple(item.action_id for item in context._catalog.actions)  # noqa: SLF001
        if action_ids is None
        else action_ids
    )
    if requested != tuple(dict.fromkeys(requested)):
        raise ValueError("M8 prepared action IDs must be unique and ordered")
    by_id = {item.action_id: item for item in context._catalog.actions}  # noqa: SLF001
    if any(action_id not in by_id for action_id in requested):
        raise ValueError("M8 prepared action is absent from the exact current catalog")
    branches = [_initial_unchecked_branch(context, by_id[action_id]) for action_id in requested]
    commons = []
    common_cursor = context._fallback_step.cursor  # noqa: SLF001
    while common_cursor.next_event_position < context._stop_event_position:  # noqa: SLF001
        common = _capture_unchecked_m8_common_transition(
            context._request.runtime,  # noqa: SLF001
            cursor=common_cursor,
            semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            runtime_authority=context._authority,  # noqa: SLF001
        )
        commons.append(common)
        for branch in branches:
            _advance_unchecked_branch(context, branch, common=common)
        common_cursor = common.common_fact.step.cursor
    context.require_active()
    return M8UncheckedTraversalCapture(
        common_transitions=tuple(commons),
        branches=tuple(
            M8UncheckedBranchTraversalCapture(
                descriptor=branch.descriptor,
                initial_step=branch.initial_step,
                cursor=branch.cursor,
                events=tuple(branch.events),
                exact_count=branch.exact_count,
                skipped_count=branch.skipped_count,
                rejection_count=branch.rejection_count,
                survivor_count=branch.survivor_count,
                rejoin_count=branch.rejoin_count,
            )
            for branch in branches
        ),
    )


def _advance_branch(
    context: _M8PreparedGeneratorContext,
    branch: _BranchState,
    *,
    common,  # type: ignore[no-untyped-def]
) -> None:
    runtime = context._request.runtime  # noqa: SLF001
    fact = _validated_common_transition_fact(runtime, common)
    if branch.cursor == fact.cursor_before:
        state_before = m7_cursor_sha256(branch.cursor)
        branch.cursor = apply_m7_frozen_action_evidence(
            runtime,
            cursor=branch.cursor,
            event_position=fact.event_position,
            action=fact.step.event.action,
        )
        witness = M8EventWitness(
            event_position=fact.event_position,
            classification="state_rejoin",
            common_action_id=fact.step.event.action.action_id,
            branch_action_id=fact.step.event.action.action_id,
            state_before_sha256=state_before,
            state_after_sha256=m7_cursor_sha256(branch.cursor),
        )
        branch.rejoin_count += 1
    else:
        passivity = certify_event_passivity(
            runtime,
            common=common,
            branch_cursor=branch.cursor,
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
        )
        branch.survivor_count += passivity.exact_search_count
        if passivity.passive:
            if passivity.witness is None or passivity.branch_after is None:
                raise ValueError("M8 passive result lacks its event witness or cursor")
            branch.cursor = passivity.branch_after
            witness = passivity.witness
            branch.skipped_count += 1
            branch.rejection_count += len(witness.influences)
        else:
            state_before = m7_cursor_sha256(branch.cursor)
            with profile_phase("action_catalog_enumeration"):
                branch_catalog = enumerate_m7_action_catalog(
                    runtime,
                    cursor=branch.cursor,
                    complete=False,
                )
            increment_profile_count("fallbacks")
            selection = select_m7_fallback(
                branch_catalog,
                policy=runtime.replay_input.policy,
            )
            descriptor = next(
                item for item in branch_catalog.actions if item.action_id == selection.action_id
            )
            step = apply_m7_action_descriptor(
                runtime,
                cursor=branch.cursor,
                catalog=branch_catalog,
                descriptor=descriptor,
                decision_key=selection.decision_key,
            )
            branch.cursor = step.cursor
            witness = M8EventWitness(
                event_position=fact.event_position,
                classification="exact_transition",
                common_action_id=fact.step.event.action.action_id,
                branch_action_id=step.event.action.action_id,
                state_before_sha256=state_before,
                state_after_sha256=m7_cursor_sha256(branch.cursor),
            )
            branch.exact_count += 1
    branch.witnesses.append(witness)


def _score_prepared_certificate_actions(
    context: _M8PreparedGeneratorContext,
    *,
    action_ids: tuple[str, ...] | None = None,
) -> tuple[M8CertificateActionResult, ...]:
    """Score a prepared action subset event-major with one common capability at a time."""

    if type(context) is not _M8PreparedGeneratorContext:
        raise ValueError("M8 prepared generator capability has the wrong type")
    context.require_active()
    requested = (
        tuple(item.action_id for item in context._catalog.actions)  # noqa: SLF001
        if action_ids is None
        else action_ids
    )
    if requested != tuple(dict.fromkeys(requested)):
        raise ValueError("M8 prepared action IDs must be unique and ordered")
    by_id = {item.action_id: item for item in context._catalog.actions}  # noqa: SLF001
    if any(action_id not in by_id for action_id in requested):
        raise ValueError("M8 prepared action is absent from the exact current catalog")
    branches = [_initial_branch(context, by_id[action_id]) for action_id in requested]
    common_cursor = context._fallback_step.cursor  # noqa: SLF001
    while common_cursor.next_event_position < context._stop_event_position:  # noqa: SLF001
        common = build_validated_m8_common_transition_in_context(
            context._authority,  # noqa: SLF001
            cursor=common_cursor,
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
        )
        try:
            fact = _validated_common_transition_fact(
                context._request.runtime,  # noqa: SLF001
                common,
            )
            for branch in branches:
                _advance_branch(context, branch, common=common)
            common_cursor = fact.step.cursor
        finally:
            _release_validated_common_transition(common)

    start_state_sha256 = m7_cursor_sha256(context._request.cursor)  # noqa: SLF001
    results = []
    for branch in branches:
        terminal = run_m7_continuation(
            context._request.runtime,  # noqa: SLF001
            cursor=branch.cursor,
            stop_event_position=context._stop_event_position,  # noqa: SLF001
        )
        with profile_phase("fact_serialization"):
            proof = build_m8_action_proof(
                action_id=branch.initial_step.event.action.action_id,
                catalog_action_id=branch.descriptor.action_id,
                baseline_action_id=context._fallback_step.event.action.action_id,  # noqa: SLF001
                baseline_catalog_action_id=context._fallback_step.descriptor.action_id,  # noqa: SLF001
                start_event_position=context._catalog.event_position,  # noqa: SLF001
                stop_event_position=context._stop_event_position,  # noqa: SLF001
                suffix_sha256=context._suffix_sha256,  # noqa: SLF001
                semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
                start_state_sha256=start_state_sha256,
                witnesses=tuple(branch.witnesses),
                final_net_cost=terminal.final_costs.net_cost,
                final_state_sha256=m7_cursor_sha256(branch.cursor),
            )
        increment_profile_count("actions")
        results.append(
            M8CertificateActionResult(
                score=M8ActionScore(
                    action_id=branch.descriptor.action_id,
                    final_net_cost=terminal.final_costs.net_cost,
                ),
                proof=proof,
                exact_branch_event_count=branch.exact_count,
                skipped_passive_event_count=branch.skipped_count,
                rejection_certificate_count=branch.rejection_count,
                survivor_pair_count=branch.survivor_count,
                state_rejoin_count=branch.rejoin_count,
            )
        )
    context.require_active()
    return tuple(results)


def score_certificate_action(
    request: M8OracleRequest,
    *,
    action_id: str,
) -> M8CertificateActionResult:
    """Generate one exact score and proof in one owned authoritative context."""

    with profile_phase("certificate_generation"):
        with _prepare_m8_generator_context(request) as context:
            return _score_prepared_certificate_actions(context, action_ids=(action_id,))[0]


def score_certificate_actions(
    request: M8OracleRequest,
    *,
    action_ids: tuple[str, ...],
) -> tuple[M8CertificateActionResult, ...]:
    """Generate one event-major proof batch for an exact frozen action subset."""

    with profile_phase("certificate_generation"):
        with _prepare_m8_generator_context(request) as context:
            return _score_prepared_certificate_actions(context, action_ids=action_ids)


def audit_m8_common_transition_exactness(
    request: M8OracleRequest,
) -> M8CommonFactDifferentialAudit:
    """Compare one fast common fact with fresh authoritative M7 replay."""

    with _prepare_m8_generator_context(request) as context:
        cursor = context._fallback_step.cursor  # noqa: SLF001
        if cursor.next_event_position >= context._stop_event_position:  # noqa: SLF001
            raise ValueError("M8 common-fact audit requires one visible future event")
        with profile_phase("common_fact_differential_audit"):
            fact = _derive_m8_common_transition_fact_unprofiled(
                context._request.runtime,  # noqa: SLF001
                cursor=cursor,
                semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
                prepared_layouts=context._prepared_layouts,  # noqa: SLF001
                differential=True,
            )
    return M8CommonFactDifferentialAudit(
        event_position=fact.event_position,
        event_id=fact.event_id,
        content_sha256=fact.content_sha256,
    )


def _score_sparse_event_unprofiled(request: M8OracleRequest) -> M8SparseResult:
    with _prepare_m8_generator_context(request) as context:
        action_results = _score_prepared_certificate_actions(context)
        decision = build_oracle_decision(
            baseline_action_id=context._fallback_step.descriptor.action_id,  # noqa: SLF001
            expected_action_ids=tuple(
                item.action_id
                for item in context._catalog.actions  # noqa: SLF001
            ),
            scores=tuple(
                (item.score.action_id, item.score.final_net_cost) for item in action_results
            ),
        )
        return M8SparseResult(
            decision=decision,
            proofs=tuple(item.proof for item in action_results),
            metrics=M8SparseMetrics(
                common_continuation_event_count=len(context._visible),  # noqa: SLF001
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


def score_sparse_event(request: M8OracleRequest) -> M8SparseResult:
    """Score every current action event-major and emit one exact proof per action."""

    with profile_phase("certificate_generation"):
        return _score_sparse_event_unprofiled(request)


__all__ = [
    "M8CertificateActionResult",
    "M8SparseMetrics",
    "M8SparseResult",
    "score_certificate_action",
    "score_certificate_actions",
    "score_sparse_event",
]
