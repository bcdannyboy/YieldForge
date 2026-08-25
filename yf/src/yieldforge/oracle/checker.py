"""Independent fail-closed checker for exact M8 action proofs."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Literal, Self

from pydantic import StrictBool, StrictInt, model_validator

from yieldforge.baseline.contracts import BaselineContractModel
from yieldforge.baseline.replay import (
    M7ReplayRuntime,
    apply_m7_action_descriptor,
    apply_m7_frozen_action_evidence,
    enumerate_m7_action_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle.certificates import (
    build_validated_m8_common_transition,
    certify_event_passivity,
)
from yieldforge.oracle.proofs import M8ActionProof, M8EventWitness, m8_suffix_sha256

if TYPE_CHECKING:
    from yieldforge.oracle.reference import M8OracleRequest


M8ProofFailureCode = Literal[
    "valid",
    "invalid_proof",
    "runtime_binding_mismatch",
    "start_state_mismatch",
    "suffix_mismatch",
    "action_catalog_mismatch",
    "initial_action_mismatch",
    "witness_mismatch",
    "terminal_mismatch",
]


class M8ProofCheckResult(BaselineContractModel):
    """Strict bounded checker outcome; no exception is interpreted as validity."""

    valid: StrictBool
    checked_event_count: StrictInt
    certificate_count: StrictInt
    exact_transition_count: StrictInt
    failure_code: M8ProofFailureCode

    @model_validator(mode="after")
    def require_reconciled_status(self) -> Self:
        if self.checked_event_count < 0:
            raise ValueError("M8 checked event count cannot be negative")
        if self.certificate_count < 0 or self.exact_transition_count < 0:
            raise ValueError("M8 checker counts cannot be negative")
        if self.valid != (self.failure_code == "valid"):
            raise ValueError("M8 checker validity differs from failure code")
        return self


class _ProofFailure(ValueError):
    def __init__(self, code: M8ProofFailureCode) -> None:
        super().__init__(code)
        self.code = code


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


def _result(
    *,
    valid: bool,
    checked: int,
    certificates: int,
    exact: int,
    failure_code: M8ProofFailureCode,
) -> M8ProofCheckResult:
    return M8ProofCheckResult(
        valid=valid,
        checked_event_count=checked,
        certificate_count=certificates,
        exact_transition_count=exact,
        failure_code=failure_code,
    )


def check_action_proof(
    request: M8OracleRequest,
    proof: M8ActionProof,
) -> M8ProofCheckResult:
    """Recompute one proof through a control flow independent from both scorers."""

    checked = 0
    certificates = 0
    exact = 0
    try:
        try:
            canonical = M8ActionProof.model_validate(
                proof.model_dump(mode="python"),
                strict=True,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise _ProofFailure("invalid_proof") from error
        if canonical != proof:
            raise _ProofFailure("invalid_proof")

        runtime = request.runtime
        semantic_runtime = m7_semantic_runtime_sha256(runtime)
        if canonical.semantic_runtime_sha256 != semantic_runtime:
            raise _ProofFailure("runtime_binding_mismatch")
        if canonical.start_state_sha256 != m7_cursor_sha256(request.cursor):
            raise _ProofFailure("start_state_mismatch")

        visible = request.visibility.visible_suffix(
            current_position=request.cursor.next_event_position
        )
        registered = runtime.replay_input.instances
        expected = registered[
            request.cursor.next_event_position
            + 1 : request.cursor.next_event_position
            + 1
            + len(visible)
        ]
        if visible != expected:
            raise _ProofFailure("suffix_mismatch")
        stop = request.cursor.next_event_position + 1 + len(visible)
        if (
            canonical.start_event_position != request.cursor.next_event_position
            or canonical.stop_event_position != stop
            or canonical.suffix_sha256
            != m8_suffix_sha256(
                semantic_runtime_sha256=semantic_runtime,
                start_event_position=request.cursor.next_event_position,
                stop_event_position=stop,
                bindings=visible,
            )
        ):
            raise _ProofFailure("suffix_mismatch")

        catalog = enumerate_m7_action_catalog(runtime, cursor=request.cursor)
        fallback = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
        if canonical.baseline_catalog_action_id != fallback.action_id:
            raise _ProofFailure("action_catalog_mismatch")
        fallback_descriptor = next(
            item for item in catalog.actions if item.action_id == fallback.action_id
        )
        fallback_step = apply_m7_action_descriptor(
            runtime,
            cursor=request.cursor,
            catalog=catalog,
            descriptor=fallback_descriptor,
            decision_key=fallback.decision_key,
        )
        if canonical.baseline_action_id != fallback_step.event.action.action_id:
            raise _ProofFailure("initial_action_mismatch")

        descriptors = tuple(
            item for item in catalog.actions if item.action_id == canonical.catalog_action_id
        )
        if len(descriptors) != 1:
            raise _ProofFailure("action_catalog_mismatch")
        initial_step = apply_m7_action_descriptor(
            runtime,
            cursor=request.cursor,
            catalog=catalog,
            descriptor=descriptors[0],
            decision_key=(
                f"m8_hypothetical_action_id={canonical.catalog_action_id}",
            ),
        )
        if canonical.action_id != initial_step.event.action.action_id:
            raise _ProofFailure("initial_action_mismatch")

        cursor = initial_step.cursor
        common_cursor = fallback_step.cursor
        for witness in canonical.witnesses:
            common = build_validated_m8_common_transition(
                runtime,
                cursor=common_cursor,
            )
            fact = common.fact
            if (
                witness.event_position != cursor.next_event_position
                or witness.event_position != fact.event_position
                or witness.common_action_id != fact.step.event.action.action_id
                or witness.state_before_sha256 != m7_cursor_sha256(cursor)
            ):
                raise _ProofFailure("witness_mismatch")

            if cursor == fact.cursor_before:
                cursor = apply_m7_frozen_action_evidence(
                    runtime,
                    cursor=cursor,
                    event_position=fact.event_position,
                    action=fact.step.event.action,
                )
                expected_witness = M8EventWitness(
                    event_position=fact.event_position,
                    classification="state_rejoin",
                    common_action_id=fact.step.event.action.action_id,
                    branch_action_id=fact.step.event.action.action_id,
                    state_before_sha256=witness.state_before_sha256,
                    state_after_sha256=m7_cursor_sha256(cursor),
                )
            else:
                passivity = certify_event_passivity(
                    runtime,
                    common=common,
                    branch_cursor=cursor,
                )
                if passivity.passive:
                    if passivity.witness is None:
                        raise _ProofFailure("witness_mismatch")
                    expected_witness = passivity.witness
                    cursor = apply_m7_frozen_action_evidence(
                        runtime,
                        cursor=cursor,
                        event_position=fact.event_position,
                        action=fact.step.event.action,
                    )
                    certificates += len(expected_witness.influences)
                else:
                    branch_catalog = enumerate_m7_action_catalog(
                        runtime,
                        cursor=cursor,
                        complete=False,
                    )
                    selected = select_m7_fallback(
                        branch_catalog,
                        policy=runtime.replay_input.policy,
                    )
                    branch_descriptor = next(
                        item
                        for item in branch_catalog.actions
                        if item.action_id == selected.action_id
                    )
                    branch_step = apply_m7_action_descriptor(
                        runtime,
                        cursor=cursor,
                        catalog=branch_catalog,
                        descriptor=branch_descriptor,
                        decision_key=selected.decision_key,
                    )
                    cursor = branch_step.cursor
                    expected_witness = M8EventWitness(
                        event_position=fact.event_position,
                        classification="exact_transition",
                        common_action_id=fact.step.event.action.action_id,
                        branch_action_id=branch_step.event.action.action_id,
                        state_before_sha256=witness.state_before_sha256,
                        state_after_sha256=m7_cursor_sha256(cursor),
                    )
                    exact += 1
            if witness != expected_witness:
                raise _ProofFailure("witness_mismatch")
            checked += 1
            common_cursor = fact.step.cursor

        if canonical.final_state_sha256 != m7_cursor_sha256(cursor):
            raise _ProofFailure("terminal_mismatch")
        terminal = run_m7_continuation(
            _isolated_runtime(runtime),
            cursor=cursor,
            stop_event_position=canonical.stop_event_position,
        )
        if terminal.events or terminal.final_costs.net_cost != canonical.final_net_cost:
            raise _ProofFailure("terminal_mismatch")
    except _ProofFailure as error:
        return _result(
            valid=False,
            checked=checked,
            certificates=certificates,
            exact=exact,
            failure_code=error.code,
        )
    except Exception:
        return _result(
            valid=False,
            checked=checked,
            certificates=certificates,
            exact=exact,
            failure_code="invalid_proof",
        )
    return _result(
        valid=True,
        checked=checked,
        certificates=certificates,
        exact=exact,
        failure_code="valid",
    )


__all__ = [
    "M8ProofCheckResult",
    "M8ProofFailureCode",
    "check_action_proof",
]
