"""Independent fail-closed checker for exact M8 action proofs."""

from __future__ import annotations

import os
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self

from pydantic import StrictBool, StrictInt, model_validator

from yieldforge.baseline.contracts import BaselineContractModel, TemporalInstanceBinding
from yieldforge.baseline.replay import (
    M7ActionCatalog,
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
    build_validated_m8_common_transition_in_context,
    certify_event_passivity,
)
from yieldforge.oracle.prepared import prepared_context_fingerprint
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


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _M8PreparedCheckerContext:
    """Independent scoped checker state for one event-major proof batch."""

    _authority: M7AuthoritativeProofRuntime
    _request: M8OracleRequest
    _catalog: M7ActionCatalog
    _fallback_step: M7StepResult
    _visible: tuple[TemporalInstanceBinding, ...]
    _stop_event_position: int
    _suffix_sha256: str

    def require_active(self) -> None:
        registered = _PREPARED_CHECKER_REGISTRY.get(id(self))
        try:
            fingerprint = _checker_context_fingerprint(self)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("M8 prepared checker capability integrity differs") from error
        if (
            type(self) is not _M8PreparedCheckerContext
            or registered is None
            or registered[0]() is not self
            or registered[1] != os.getpid()
            or registered[2] != id(self._authority)
            or registered[3] != fingerprint
        ):
            raise ValueError("M8 prepared checker capability is invalid or inactive")
        self._authority.require_active(self._request.runtime)

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared checker capabilities cannot be serialized")


_PREPARED_CHECKER_REGISTRY: dict[
    int,
    tuple[weakref.ReferenceType[_M8PreparedCheckerContext], int, int, str],
] = {}


def _checker_context_fingerprint(context: _M8PreparedCheckerContext) -> str:
    return prepared_context_fingerprint(
        kind="checker",
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
class _CheckedBranch:
    proof: M8ActionProof
    cursor: M7ReplayCursor
    checked: int = 0
    certificates: int = 0
    exact: int = 0


class _ProofFailure(ValueError):
    def __init__(self, code: M8ProofFailureCode) -> None:
        super().__init__(code)
        self.code = code


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


def _failed(
    code: M8ProofFailureCode,
    branch: _CheckedBranch | None = None,
) -> M8ProofCheckResult:
    return _result(
        valid=False,
        checked=branch.checked if branch is not None else 0,
        certificates=branch.certificates if branch is not None else 0,
        exact=branch.exact if branch is not None else 0,
        failure_code=code,
    )


@contextmanager
def _prepare_m8_checker_context(
    request: M8OracleRequest,
) -> Iterator[_M8PreparedCheckerContext]:
    """Own a checker-only stable snapshot and exact common-path prefix."""

    with authoritative_m7_proof_runtime(request.runtime) as authority:
        from yieldforge.oracle.reference import M8OracleRequest

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
        descriptor = next(
            item for item in catalog.actions if item.action_id == fallback.action_id
        )
        fallback_step = apply_m7_action_descriptor(
            captured.runtime,
            cursor=captured.cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=fallback.decision_key,
        )
        visible = captured.visibility.visible_suffix(
            current_position=catalog.event_position
        )
        registered = captured.runtime.replay_input.instances
        expected = registered[
            catalog.event_position + 1 : catalog.event_position + 1 + len(visible)
        ]
        if visible != expected:
            raise ValueError("M8 visibility provider returned a non-prefix or mutated suffix")
        stop = catalog.event_position + 1 + len(visible)
        context = _M8PreparedCheckerContext(
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
        )
        key = id(context)

        def discard(
            reference: weakref.ReferenceType[_M8PreparedCheckerContext],
        ) -> None:
            registered = _PREPARED_CHECKER_REGISTRY.get(key)
            if registered is not None and registered[0] is reference:
                _PREPARED_CHECKER_REGISTRY.pop(key, None)

        reference = weakref.ref(context, discard)
        _PREPARED_CHECKER_REGISTRY[key] = (
            reference,
            os.getpid(),
            id(authority),
            _checker_context_fingerprint(context),
        )
        try:
            yield context
        finally:
            integrity_error = None
            try:
                context.require_active()
            except ValueError as error:
                integrity_error = error
            registered = _PREPARED_CHECKER_REGISTRY.get(key)
            if registered is not None and registered[0]() is context:
                _PREPARED_CHECKER_REGISTRY.pop(key, None)
            if integrity_error is not None:
                raise integrity_error


def _initialize_branch(
    context: _M8PreparedCheckerContext,
    proof: M8ActionProof,
) -> _CheckedBranch:
    try:
        canonical = M8ActionProof.model_validate(
            proof.model_dump(mode="python"),
            strict=True,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise _ProofFailure("invalid_proof") from error
    if canonical != proof:
        raise _ProofFailure("invalid_proof")
    if canonical.semantic_runtime_sha256 != context._authority.semantic_sha256:  # noqa: SLF001
        raise _ProofFailure("runtime_binding_mismatch")
    request = context._request  # noqa: SLF001
    if canonical.start_state_sha256 != m7_cursor_sha256(request.cursor):
        raise _ProofFailure("start_state_mismatch")
    if (
        canonical.start_event_position != context._catalog.event_position  # noqa: SLF001
        or canonical.stop_event_position != context._stop_event_position  # noqa: SLF001
        or canonical.suffix_sha256 != context._suffix_sha256  # noqa: SLF001
    ):
        raise _ProofFailure("suffix_mismatch")
    fallback_step = context._fallback_step  # noqa: SLF001
    if canonical.baseline_catalog_action_id != fallback_step.descriptor.action_id:
        raise _ProofFailure("action_catalog_mismatch")
    if canonical.baseline_action_id != fallback_step.event.action.action_id:
        raise _ProofFailure("initial_action_mismatch")
    descriptors = tuple(
        item
        for item in context._catalog.actions  # noqa: SLF001
        if item.action_id == canonical.catalog_action_id
    )
    if len(descriptors) != 1:
        raise _ProofFailure("action_catalog_mismatch")
    initial = apply_m7_action_descriptor(
        request.runtime,
        cursor=request.cursor,
        catalog=context._catalog,  # noqa: SLF001
        descriptor=descriptors[0],
        decision_key=(f"m8_hypothetical_action_id={canonical.catalog_action_id}",),
    )
    if canonical.action_id != initial.event.action.action_id:
        raise _ProofFailure("initial_action_mismatch")
    return _CheckedBranch(proof=canonical, cursor=initial.cursor)


def _check_event(
    context: _M8PreparedCheckerContext,
    branch: _CheckedBranch,
    *,
    witness: M8EventWitness,
    common,  # type: ignore[no-untyped-def]
) -> None:
    runtime = context._request.runtime  # noqa: SLF001
    fact = common.fact
    if (
        witness.event_position != branch.cursor.next_event_position
        or witness.event_position != fact.event_position
        or witness.common_action_id != fact.step.event.action.action_id
        or witness.state_before_sha256 != m7_cursor_sha256(branch.cursor)
    ):
        raise _ProofFailure("witness_mismatch")
    if branch.cursor == fact.cursor_before:
        branch.cursor = apply_m7_frozen_action_evidence(
            runtime,
            cursor=branch.cursor,
            event_position=fact.event_position,
            action=fact.step.event.action,
        )
        expected = M8EventWitness(
            event_position=fact.event_position,
            classification="state_rejoin",
            common_action_id=fact.step.event.action.action_id,
            branch_action_id=fact.step.event.action.action_id,
            state_before_sha256=witness.state_before_sha256,
            state_after_sha256=m7_cursor_sha256(branch.cursor),
        )
    else:
        passivity = certify_event_passivity(
            runtime,
            common=common,
            branch_cursor=branch.cursor,
        )
        if passivity.passive:
            if passivity.witness is None:
                raise _ProofFailure("witness_mismatch")
            expected = passivity.witness
            branch.cursor = apply_m7_frozen_action_evidence(
                runtime,
                cursor=branch.cursor,
                event_position=fact.event_position,
                action=fact.step.event.action,
            )
            branch.certificates += len(expected.influences)
        else:
            catalog = enumerate_m7_action_catalog(
                runtime,
                cursor=branch.cursor,
                complete=False,
            )
            selected = select_m7_fallback(
                catalog,
                policy=runtime.replay_input.policy,
            )
            descriptor = next(
                item for item in catalog.actions if item.action_id == selected.action_id
            )
            step = apply_m7_action_descriptor(
                runtime,
                cursor=branch.cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=selected.decision_key,
            )
            branch.cursor = step.cursor
            expected = M8EventWitness(
                event_position=fact.event_position,
                classification="exact_transition",
                common_action_id=fact.step.event.action.action_id,
                branch_action_id=step.event.action.action_id,
                state_before_sha256=witness.state_before_sha256,
                state_after_sha256=m7_cursor_sha256(branch.cursor),
            )
            branch.exact += 1
    if witness != expected:
        raise _ProofFailure("witness_mismatch")
    branch.checked += 1


def _check_prepared_action_proofs(
    context: _M8PreparedCheckerContext,
    proofs: tuple[M8ActionProof, ...],
) -> tuple[M8ProofCheckResult, ...]:
    """Check a proof batch event-major against one independent common path."""

    if type(context) is not _M8PreparedCheckerContext:
        raise ValueError("M8 prepared checker capability has the wrong type")
    context.require_active()
    branches: list[_CheckedBranch | None] = []
    results: list[M8ProofCheckResult | None] = []
    for proof in proofs:
        try:
            branches.append(_initialize_branch(context, proof))
            results.append(None)
        except _ProofFailure as error:
            branches.append(None)
            results.append(_failed(error.code))

    common_cursor = context._fallback_step.cursor  # noqa: SLF001
    event_index = 0
    while common_cursor.next_event_position < context._stop_event_position:  # noqa: SLF001
        common = build_validated_m8_common_transition_in_context(
            context._authority,  # noqa: SLF001
            cursor=common_cursor,
        )
        for index, branch in enumerate(branches):
            if branch is None:
                continue
            try:
                _check_event(
                    context,
                    branch,
                    witness=branch.proof.witnesses[event_index],
                    common=common,
                )
            except (IndexError, _ProofFailure) as error:
                code = error.code if isinstance(error, _ProofFailure) else "invalid_proof"
                results[index] = _failed(code, branch)
                branches[index] = None
        common_cursor = common.step.cursor
        event_index += 1
        del common

    for index, branch in enumerate(branches):
        if branch is None:
            continue
        try:
            if branch.proof.final_state_sha256 != m7_cursor_sha256(branch.cursor):
                raise _ProofFailure("terminal_mismatch")
            terminal = run_m7_continuation(
                context._request.runtime,  # noqa: SLF001
                cursor=branch.cursor,
                stop_event_position=context._stop_event_position,  # noqa: SLF001
            )
            if terminal.events or terminal.final_costs.net_cost != branch.proof.final_net_cost:
                raise _ProofFailure("terminal_mismatch")
        except _ProofFailure as error:
            results[index] = _failed(error.code, branch)
        else:
            results[index] = _result(
                valid=True,
                checked=branch.checked,
                certificates=branch.certificates,
                exact=branch.exact,
                failure_code="valid",
            )
    context.require_active()
    if any(item is None for item in results):
        raise ValueError("M8 checker left a proof without a terminal result")
    return tuple(item for item in results if item is not None)


def check_action_proofs(
    request: M8OracleRequest,
    proofs: tuple[M8ActionProof, ...],
) -> tuple[M8ProofCheckResult, ...]:
    """Check a batch in one owned checker-only authoritative context."""

    try:
        with _prepare_m8_checker_context(request) as context:
            return _check_prepared_action_proofs(context, proofs)
    except Exception:
        return tuple(_failed("invalid_proof") for _proof in proofs)


def check_action_proof(
    request: M8OracleRequest,
    proof: M8ActionProof,
) -> M8ProofCheckResult:
    """Check one proof through the independent event-major batch control flow."""

    return check_action_proofs(request, (proof,))[0]


__all__ = [
    "M8ProofCheckResult",
    "M8ProofFailureCode",
    "check_action_proof",
    "check_action_proofs",
]
