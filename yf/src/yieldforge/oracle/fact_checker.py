"""Independent common and fixed-layer checker for portable M8 fact bundles.

The bundle remains an unchecked transport object until this module binds it to an independently
supplied calibration runtime, reconstructs every common transition, and retires every local
capability.  The explicit full-check API additionally reconstructs every influence and action root
before exposing a bounded oracle decision; neither API claims an M8, savings, or commercial verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import weakref
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import ClassVar, Literal

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError, model_validator

from yieldforge.baseline.contracts import (
    BaselineContractModel,
    M7ActionKind,
    TemporalInstanceBinding,
)
from yieldforge.baseline.geometry import (
    LayoutTranslationCandidates,
    certify_translation_impossible,
    generate_layout_translations,
    prepare_layout_footprint,
    prepare_remnant_geometry,
    prepare_translation_rejection_remnant,
    search_layout_translation,
)
from yieldforge.baseline.jagua import JaguaRepresentationError, run_jagua_generated_prefilter
from yieldforge.baseline.policies import rank_policy_action
from yieldforge.baseline.replay import (
    GeneratedActionSet,
    M7ActionCatalog,
    M7AuthoritativeProofRuntime,
    M7ReplayCursor,
    M7ReplayRuntime,
    M7StepResult,
    apply_m7_action_descriptor,
    apply_m7_frozen_action_evidence,
    authoritative_m7_proof_runtime,
    enumerate_m7_action_catalog,
    enumerate_m7_single_remnant_competitor,
    enumerate_m7_standard_only_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import facts
from yieldforge.oracle.certificates import (
    _VALIDATED_COMMON_REGISTRY,
    M8CommonTransitionFact,
    ValidatedCommonTransition,
    _common_fact_payload,
    _portable_common_transition,
    _portable_search_config,
    _register_checker_validated_common_transition,
    _release_validated_common_transition,
    _validated_common_transition_fact,
)
from yieldforge.oracle.compiled import (
    _verified_rejection_layouts_cover_candidates,
    compile_rejection_problem,
    compile_translation_rejections,
)
from yieldforge.oracle.concurrency import (
    activate_m8_local_trusted_audit,
    require_m8_translation_audit_processes,
)
from yieldforge.oracle.contracts import M8OracleDecision, build_oracle_decision
from yieldforge.oracle.frontier import certify_frontier_impossible
from yieldforge.oracle.profiling import profile_phase
from yieldforge.oracle.proofs import m8_suffix_sha256
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.translation_count_audit import audit_layout_translation_batch
from yieldforge.replay.contracts import rounded_cost
from yieldforge.reuse.geometry import material_key
from yieldforge.temporal_benchmark.contracts import TemporalPartition

M8CommonFactFailureCode = Literal[
    "valid_common_facts",
    "invalid_request",
    "noncanonical_bundle",
    "structural_bundle_failure",
    "runtime_binding_mismatch",
    "catalog_binding_mismatch",
    "suffix_binding_mismatch",
    "freeze_binding_mismatch",
    "candidate_scalar_mismatch",
    "frontier_mismatch",
    "inventory_classification_mismatch",
    "translation_count_mismatch",
    "standard_profile_mismatch",
    "policy_minimum_mismatch",
    "portable_transition_mismatch",
    "cursor_chain_mismatch",
    "implicit_exact_replay",
    "capability_registration_failure",
    "internal_checker_failure",
    "m8_bundle_hash_mismatch",
    "m8_fixed_layer_order",
    "m8_duplicate_fact",
    "m8_duplicate_identity",
    "m8_context_mismatch",
    "m8_dangling_reference",
    "m8_partition_mismatch",
    "m8_event_order_mismatch",
    "m8_cursor_chain_mismatch",
    "m8_incomplete_evidence",
    "m8_configuration_mismatch",
    "m8_standard_profile_mismatch",
    "m8_policy_minimum_mismatch",
    "m8_replay_context_mismatch",
    "m8_action_binding_mismatch",
    "m8_translation_mismatch",
    "m8_state_chain_mismatch",
    "m8_unused_fact",
    "m8_root_context_mismatch",
]

M8CheckedFactBundleFailureCode = (
    M8CommonFactFailureCode
    | Literal[
        "valid_action_decision",
        "influence_classification_mismatch",
        "influence_rejection_mismatch",
        "influence_search_mismatch",
        "influence_competitor_mismatch",
        "influence_action_mismatch",
        "influence_state_mismatch",
        "root_catalog_mismatch",
        "root_state_mismatch",
        "root_terminal_mismatch",
    ]
)


@dataclass(frozen=True)
class M8CommonFactCheckRequest:
    """Explicit out-of-band calibration authority and transport assertions."""

    semantic_bundle_bytes: bytes
    oracle_request: M8OracleRequest
    expected_semantic_runtime_sha256: str
    expected_current_cursor_sha256: str
    expected_catalog_event_position: int
    expected_catalog_action_ids: tuple[str, ...]
    expected_stop_event_position: int
    expected_suffix_sha256: str
    expected_freeze_id: str
    expected_freeze_sha256: str
    allow_exact_replay: bool = False
    _runtime_object_id: int = field(init=False, repr=False, compare=False)
    _oracle_request_object_id: int = field(init=False, repr=False, compare=False)
    _replay_input_object_id: int = field(init=False, repr=False, compare=False)
    _cursor_object_id: int = field(init=False, repr=False, compare=False)
    _visibility_object_id: int = field(init=False, repr=False, compare=False)
    _claim_snapshot: tuple[object, ...] = field(init=False, repr=False, compare=False)
    _runtime_reference: object = field(init=False, repr=False, compare=False)
    _cursor_reference: object = field(init=False, repr=False, compare=False)
    _visibility_reference: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._require_shape()
        object.__setattr__(self, "_oracle_request_object_id", id(self.oracle_request))
        object.__setattr__(self, "_runtime_object_id", id(self.oracle_request.runtime))
        object.__setattr__(
            self,
            "_replay_input_object_id",
            id(self.oracle_request.runtime.replay_input),
        )
        object.__setattr__(self, "_cursor_object_id", id(self.oracle_request.cursor))
        object.__setattr__(self, "_visibility_object_id", id(self.oracle_request.visibility))
        object.__setattr__(self, "_claim_snapshot", self._current_claim_snapshot())
        object.__setattr__(self, "_runtime_reference", self.oracle_request.runtime)
        object.__setattr__(self, "_cursor_reference", self.oracle_request.cursor)
        object.__setattr__(self, "_visibility_reference", self.oracle_request.visibility)

    def _current_claim_snapshot(self) -> tuple[object, ...]:
        return (
            self.semantic_bundle_bytes,
            id(self.oracle_request),
            self.expected_semantic_runtime_sha256,
            self.expected_current_cursor_sha256,
            self.expected_catalog_event_position,
            self.expected_catalog_action_ids,
            self.expected_stop_event_position,
            self.expected_suffix_sha256,
            self.expected_freeze_id,
            self.expected_freeze_sha256,
            self.allow_exact_replay,
        )

    def _require_shape(self) -> None:
        if type(self.semantic_bundle_bytes) is not bytes:
            raise TypeError("M8 common checker requires exact canonical bytes")
        if type(self.oracle_request) is not M8OracleRequest:
            raise TypeError("M8 common checker requires an exact oracle request")
        for value, label in (
            (self.expected_semantic_runtime_sha256, "semantic runtime"),
            (self.expected_current_cursor_sha256, "current cursor"),
            (self.expected_suffix_sha256, "suffix"),
        ):
            if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
                raise ValueError(f"M8 expected {label} SHA-256 is invalid")
        if type(self.expected_catalog_event_position) is not int or (
            self.expected_catalog_event_position < 0
        ):
            raise ValueError("M8 expected catalog position must be a nonnegative exact integer")
        if type(self.expected_stop_event_position) is not int or (
            self.expected_stop_event_position <= self.expected_catalog_event_position
        ):
            raise ValueError("M8 expected stop position must follow the current catalog")
        if (
            type(self.expected_catalog_action_ids) is not tuple
            or not self.expected_catalog_action_ids
            or len(self.expected_catalog_action_ids) != len(set(self.expected_catalog_action_ids))
            or any(type(item) is not str or not item for item in self.expected_catalog_action_ids)
        ):
            raise ValueError("M8 expected catalog action identities must be unique and nonempty")
        if type(self.allow_exact_replay) is not bool:
            raise TypeError("M8 exact-replay permission must be an exact boolean")
        freeze_id = re.fullmatch(r"yfm7freeze-([0-9a-f]{24})", self.expected_freeze_id)
        freeze_sha = re.fullmatch(r"sha256:([0-9a-f]{64})", self.expected_freeze_sha256)
        if (
            freeze_id is None
            or freeze_sha is None
            or freeze_id.group(1) != freeze_sha.group(1)[:24]
        ):
            raise ValueError("M8 expected freeze ID and SHA-256 are not mutually bound")
        instances = self.oracle_request.runtime.replay_input.instances
        if any(item.partition is not TemporalPartition.CALIBRATION for item in instances):
            raise ValueError("M8 common fact checking is calibration-only")

    def require_valid(self) -> None:
        """Reject nested caller drift before and after the checker-owned snapshot."""

        self._require_shape()
        runtime = self.oracle_request.runtime
        if (
            self._current_claim_snapshot() != self._claim_snapshot
            or id(self.oracle_request) != self._oracle_request_object_id
            or id(runtime) != self._runtime_object_id
            or runtime is not self._runtime_reference
            or id(runtime.replay_input) != self._replay_input_object_id
            or id(self.oracle_request.cursor) != self._cursor_object_id
            or self.oracle_request.cursor is not self._cursor_reference
            or id(self.oracle_request.visibility) != self._visibility_object_id
            or self.oracle_request.visibility is not self._visibility_reference
            or m7_semantic_runtime_sha256(runtime) != self.expected_semantic_runtime_sha256
            or m7_cursor_sha256(self.oracle_request.cursor) != self.expected_current_cursor_sha256
        ):
            raise ValueError("M8 common checker out-of-band runtime or cursor drifted")


@dataclass(frozen=True)
class M8FactBundleCheckRequest(M8CommonFactCheckRequest):
    """Separate full fixed-layer request; exact permission covers every declared fallback."""


class M8CommonFactCheckResult(BaselineContractModel):
    """Common-only result; never an accepted action decision or Gate-3 verdict."""

    valid: StrictBool
    checked_common_lemma_count: StrictInt
    counted_translation_audit_count: StrictInt
    issued_common_capability_count: StrictInt
    exact_replay_fallback_count: StrictInt
    exact_replay_fallback_wall_seconds: StrictFloat
    failure_code: M8CommonFactFailureCode
    first_failing_fact_sha256: StrictStr | None = None
    authority_mode: ClassVar[Literal["checked_common_only"]] = "checked_common_only"

    @model_validator(mode="after")
    def require_reconciled_result(self):  # type: ignore[no-untyped-def]
        counts = (
            self.checked_common_lemma_count,
            self.counted_translation_audit_count,
            self.issued_common_capability_count,
            self.exact_replay_fallback_count,
        )
        if any(item < 0 for item in counts) or self.exact_replay_fallback_wall_seconds < 0.0:
            raise ValueError("M8 common checker measurements must be nonnegative")
        if self.valid != (self.failure_code == "valid_common_facts"):
            raise ValueError("M8 common checker validity differs from its failure code")
        if self.valid and self.first_failing_fact_sha256 is not None:
            raise ValueError("valid M8 common facts cannot identify a failing fact")
        if self.valid and self.issued_common_capability_count != self.checked_common_lemma_count:
            raise ValueError("valid M8 common facts require one retired capability per lemma")
        return self


class M8CheckedFactBundleResult(BaselineContractModel):
    """Accepted checked action decision only; never a Gate-3 or M8 hypothesis verdict."""

    valid: StrictBool
    decision: M8OracleDecision | None
    checked_common_lemma_count: StrictInt
    checked_influence_fact_count: StrictInt
    checked_action_root_count: StrictInt
    counted_translation_audit_count: StrictInt
    influence_translation_audit_count: StrictInt
    issued_common_capability_count: StrictInt
    common_exact_fallback_count: StrictInt
    influence_exact_fallback_count: StrictInt
    total_exact_fallback_count: StrictInt
    common_exact_fallback_wall_seconds: StrictFloat
    influence_exact_fallback_wall_seconds: StrictFloat
    total_exact_fallback_wall_seconds: StrictFloat
    failure_code: M8CheckedFactBundleFailureCode
    first_failing_fact_sha256: StrictStr | None = None
    authority_mode: ClassVar[Literal["checked_fixed_layer_actions"]] = "checked_fixed_layer_actions"

    @model_validator(mode="after")
    def require_reconciled_result(self):  # type: ignore[no-untyped-def]
        counts = (
            self.checked_common_lemma_count,
            self.checked_influence_fact_count,
            self.checked_action_root_count,
            self.counted_translation_audit_count,
            self.influence_translation_audit_count,
            self.issued_common_capability_count,
            self.common_exact_fallback_count,
            self.influence_exact_fallback_count,
            self.total_exact_fallback_count,
        )
        seconds = (
            self.common_exact_fallback_wall_seconds,
            self.influence_exact_fallback_wall_seconds,
            self.total_exact_fallback_wall_seconds,
        )
        if any(item < 0 for item in counts) or any(item < 0.0 for item in seconds):
            raise ValueError("M8 full fact-checker measurements must be nonnegative")
        if any(not math.isfinite(item) for item in seconds):
            raise ValueError("M8 full fact-checker timings must be finite")
        if self.failure_code == "valid_common_facts":
            raise ValueError("M8 full fact-checker cannot emit the common-only success code")
        if self.valid != (self.failure_code == "valid_action_decision"):
            raise ValueError("M8 full fact-checker validity differs from its failure code")
        if self.valid != (self.decision is not None):
            raise ValueError("M8 full fact-checker decision exposure differs from validity")
        if self.valid and self.first_failing_fact_sha256 is not None:
            raise ValueError("valid M8 full facts cannot identify a failing fact")
        if self.valid and (
            self.issued_common_capability_count != self.checked_common_lemma_count
            or self.decision is None
            or self.checked_action_root_count != self.decision.scored_action_count
        ):
            raise ValueError("valid M8 full facts require complete capability and root coverage")
        if self.total_exact_fallback_count != (
            self.common_exact_fallback_count + self.influence_exact_fallback_count
        ):
            raise ValueError("M8 full exact fallback counts do not reconcile")
        if self.total_exact_fallback_wall_seconds != (
            self.common_exact_fallback_wall_seconds + self.influence_exact_fallback_wall_seconds
        ):
            raise ValueError("M8 full exact fallback timing does not reconcile")
        return self


class _CommonFactFailure(ValueError):
    def __init__(
        self,
        code: M8CommonFactFailureCode,
        fact_sha256: str | None = None,
        *,
        fallback_count: int = 0,
        fallback_seconds: float = 0.0,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.fact_sha256 = fact_sha256
        self.fallback_count = fallback_count
        self.fallback_seconds = fallback_seconds


@dataclass
class _CommonCheckState:
    """Operation-local indexes and one-check-only shared-node state."""

    scalar_by_sha: dict[str, facts.M8CandidateScalarFactV2]
    frontier_by_sha: dict[str, facts.M8FrontierFactV2]
    standard_by_sha: dict[str, facts.M8StandardCandidateFactV2]
    translation_by_sha: dict[str, facts.M8PortableTranslationBatch]
    validated_scalar_sha256s: set[str] = field(default_factory=set)
    validated_frontier_sha256s: set[str] = field(default_factory=set)
    compiled_by_partition: dict[tuple[str, str], object] = field(default_factory=dict)
    audited_counted_lemma_sha256s: set[str] = field(default_factory=set)
    exact_replay_fallback_count: int = 0
    exact_replay_fallback_wall_seconds: float = 0.0


class _FullFactFailure(ValueError):
    def __init__(
        self,
        code: M8CheckedFactBundleFailureCode,
        fact_sha256: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.fact_sha256 = fact_sha256


@dataclass
class _FullCheckState:
    common: _CommonCheckState
    checked_influence_sha256s: set[str] = field(default_factory=set)
    influence_transition_by_sha256: dict[str, tuple[str, str, M7ReplayCursor]] = field(
        default_factory=dict
    )
    audited_influence_translation_sha256s: set[str] = field(default_factory=set)
    influence_translation_audit_count: int = 0
    influence_exact_fallback_sha256s: set[str] = field(default_factory=set)
    influence_exact_fallback_wall_seconds: float = 0.0


@dataclass(frozen=True, slots=True, weakref_slot=True, init=False)
class _M8FactCheckerRegistrationToken:
    """Unforgeable process-local issuance lease owned solely by this checker."""

    _binding: object = field(repr=False, compare=False)

    def __init__(self, binding: object) -> None:
        object.__setattr__(self, "_binding", binding)

    def __reduce__(self) -> object:
        raise TypeError("M8 fact-checker registration tokens cannot be serialized")


@dataclass(frozen=True)
class _RegisteredCheckerToken:
    reference: weakref.ReferenceType[_M8FactCheckerRegistrationToken]
    binding: object
    owner_pid: int
    authority_id: int
    runtime_id: int
    semantic_runtime_sha256: str
    replay_input_id: str
    replay_input_sha256: str
    approved_fact_sha256s: tuple[str, ...]
    consumed_fact_sha256s: set[str]


_CHECKER_REGISTRATION_TOKENS: dict[int, _RegisteredCheckerToken] = {}


@dataclass(frozen=True, slots=True, weakref_slot=True, init=False)
class _M8FullTraversalGuard:
    """Unforgeable O(1) lease for one already-deep-checked root traversal."""

    _binding: object = field(repr=False, compare=False)

    def __init__(self, binding: object) -> None:
        object.__setattr__(self, "_binding", binding)

    def __reduce__(self) -> object:
        raise TypeError("M8 full traversal guards cannot be serialized")


@dataclass(frozen=True)
class _RegisteredFullTraversalGuard:
    reference: weakref.ReferenceType[_M8FullTraversalGuard]
    binding: object
    owner_pid: int
    source_request: M8FactBundleCheckRequest
    captured_request: M8FactBundleCheckRequest
    source_claim_snapshot: tuple[object, ...]
    captured_claim_snapshot: tuple[object, ...]
    authority: M7AuthoritativeProofRuntime
    checker_token: _M8FactCheckerRegistrationToken
    runtime_replay_input: object
    capabilities: tuple[ValidatedCommonTransition, ...]
    capability_entries: tuple[object, ...]


_FULL_TRAVERSAL_GUARDS: dict[int, _RegisteredFullTraversalGuard] = {}

_FACT_LAYER_NAMES = frozenset(
    {
        "translation_batches",
        "candidate_scalar_facts",
        "frontier_facts",
        "standard_candidate_facts",
        "common_lemmas",
        "influence_facts",
        "action_roots",
    }
)


def _require_checker_registration_token(
    token: _M8FactCheckerRegistrationToken,
    authority: M7AuthoritativeProofRuntime,
    fact: M8CommonTransitionFact,
) -> None:
    _require_checker_registration_scope_token(token, authority)
    registered = _CHECKER_REGISTRATION_TOKENS.get(id(token))
    if (
        registered is None
        or registered.semantic_runtime_sha256 != fact.semantic_runtime_sha256
        or registered.replay_input_id != authority.runtime.replay_input.input_id
        or registered.replay_input_id != fact.replay_input_id
        or registered.replay_input_sha256 != authority.runtime.replay_input.content_sha256
        or registered.replay_input_sha256 != fact.replay_input_sha256
        or fact.content_sha256 not in registered.approved_fact_sha256s
    ):
        raise ValueError("M8 checker common registration token is invalid or inactive")


def _require_checker_registration_scope_token(
    token: _M8FactCheckerRegistrationToken,
    authority: M7AuthoritativeProofRuntime,
) -> None:
    _require_checker_registration_scope_token_identity(token, authority)
    authority.require_active(authority.runtime)


def _require_checker_registration_scope_token_identity(
    token: _M8FactCheckerRegistrationToken,
    authority: M7AuthoritativeProofRuntime,
) -> None:
    registered = _CHECKER_REGISTRATION_TOKENS.get(id(token))
    if (
        type(token) is not _M8FactCheckerRegistrationToken
        or registered is None
        or registered.reference() is not token
        or registered.binding is not token._binding  # noqa: SLF001
        or registered.owner_pid != os.getpid()
        or registered.authority_id != id(authority)
        or registered.runtime_id != id(authority.runtime)
        or registered.semantic_runtime_sha256 != authority.semantic_sha256
    ):
        raise ValueError("M8 checker common registration token is invalid or inactive")
    authority._require_active_identity(authority.runtime)  # noqa: SLF001


def _consume_checker_registration_token(
    token: _M8FactCheckerRegistrationToken,
    authority: M7AuthoritativeProofRuntime,
    fact: M8CommonTransitionFact,
) -> None:
    _require_checker_registration_token(token, authority, fact)
    registered = _CHECKER_REGISTRATION_TOKENS[id(token)]
    if fact.content_sha256 in registered.consumed_fact_sha256s:
        raise ValueError("M8 checker common registration token approval was already consumed")
    registered.consumed_fact_sha256s.add(fact.content_sha256)


@contextmanager
def _checker_registration_scope(
    authority: M7AuthoritativeProofRuntime,
    approved_facts: tuple[M8CommonTransitionFact, ...],
) -> Iterator[_M8FactCheckerRegistrationToken]:
    authority.require_active(authority.runtime)
    binding = object()
    token = _M8FactCheckerRegistrationToken(binding)
    key = id(token)

    def discard(reference: weakref.ReferenceType[_M8FactCheckerRegistrationToken]) -> None:
        registered = _CHECKER_REGISTRATION_TOKENS.get(key)
        if registered is not None and registered.reference is reference:
            _CHECKER_REGISTRATION_TOKENS.pop(key, None)

    reference = weakref.ref(token, discard)
    replay_input = authority.runtime.replay_input
    approved_fact_sha256s = tuple(item.content_sha256 for item in approved_facts)
    if len(approved_fact_sha256s) != len(set(approved_fact_sha256s)):
        raise ValueError("M8 checker registration approvals must be unique")
    registered = _RegisteredCheckerToken(
        reference=reference,
        binding=binding,
        owner_pid=os.getpid(),
        authority_id=id(authority),
        runtime_id=id(authority.runtime),
        semantic_runtime_sha256=authority.semantic_sha256,
        replay_input_id=replay_input.input_id,
        replay_input_sha256=replay_input.content_sha256,
        approved_fact_sha256s=approved_fact_sha256s,
        consumed_fact_sha256s=set(),
    )
    _CHECKER_REGISTRATION_TOKENS[key] = registered
    try:
        yield token
        _require_checker_registration_scope_token(token, authority)
        if registered.consumed_fact_sha256s != set(registered.approved_fact_sha256s):
            raise ValueError("M8 checker did not consume every approved common fact exactly once")
    finally:
        current = _CHECKER_REGISTRATION_TOKENS.get(key)
        if current is registered:
            _CHECKER_REGISTRATION_TOKENS.pop(key, None)


def _require_full_traversal_guard(guard: _M8FullTraversalGuard) -> None:
    registered = _FULL_TRAVERSAL_GUARDS.get(id(guard))
    if (
        type(guard) is not _M8FullTraversalGuard
        or registered is None
        or registered.reference() is not guard
        or registered.binding is not guard._binding  # noqa: SLF001
        or registered.owner_pid != os.getpid()
    ):
        raise ValueError("M8 full traversal guard is invalid or inactive")
    source = registered.source_request
    captured = registered.captured_request
    if (
        source._current_claim_snapshot() != registered.source_claim_snapshot  # noqa: SLF001
        or captured._current_claim_snapshot() != registered.captured_claim_snapshot  # noqa: SLF001
        or source.oracle_request.runtime is not source._runtime_reference  # noqa: SLF001
        or source.oracle_request.cursor is not source._cursor_reference  # noqa: SLF001
        or source.oracle_request.visibility is not source._visibility_reference  # noqa: SLF001
        or captured.oracle_request.runtime is not captured._runtime_reference  # noqa: SLF001
        or captured.oracle_request.cursor is not captured._cursor_reference  # noqa: SLF001
        or captured.oracle_request.visibility is not captured._visibility_reference  # noqa: SLF001
        or registered.authority.runtime.replay_input is not registered.runtime_replay_input
    ):
        raise ValueError("M8 full traversal request identity drifted")
    _require_checker_registration_scope_token_identity(
        registered.checker_token,
        registered.authority,
    )


def _require_full_traversal_capabilities(guard: _M8FullTraversalGuard) -> None:
    _require_full_traversal_guard(guard)
    registered = _FULL_TRAVERSAL_GUARDS[id(guard)]
    for capability, expected_entry in zip(
        registered.capabilities,
        registered.capability_entries,
        strict=True,
    ):
        current = _VALIDATED_COMMON_REGISTRY.get(id(capability))
        if (
            current is not expected_entry
            or current.reference() is not capability
            or current.owner_pid != registered.owner_pid
            or current.authority is not registered.authority
            or current.checker_token is not registered.checker_token
        ):
            raise ValueError("M8 full traversal common capability is inactive")


@contextmanager
def _full_traversal_guard_scope(
    source_request: M8FactBundleCheckRequest,
    captured_request: M8FactBundleCheckRequest,
    authority: M7AuthoritativeProofRuntime,
    checker_token: _M8FactCheckerRegistrationToken,
    capabilities: tuple[ValidatedCommonTransition, ...],
) -> Iterator[_M8FullTraversalGuard]:
    _require_full_request_stable(source_request, captured_request, authority)
    capability_entries = tuple(
        _VALIDATED_COMMON_REGISTRY.get(id(capability)) for capability in capabilities
    )
    if any(entry is None for entry in capability_entries):
        raise ValueError("M8 full traversal common capability is inactive")
    binding = object()
    guard = _M8FullTraversalGuard(binding)
    key = id(guard)

    def discard(reference: weakref.ReferenceType[_M8FullTraversalGuard]) -> None:
        registered = _FULL_TRAVERSAL_GUARDS.get(key)
        if registered is not None and registered.reference is reference:
            _FULL_TRAVERSAL_GUARDS.pop(key, None)

    reference = weakref.ref(guard, discard)
    registered = _RegisteredFullTraversalGuard(
        reference=reference,
        binding=binding,
        owner_pid=os.getpid(),
        source_request=source_request,
        captured_request=captured_request,
        source_claim_snapshot=source_request._current_claim_snapshot(),  # noqa: SLF001
        captured_claim_snapshot=captured_request._current_claim_snapshot(),  # noqa: SLF001
        authority=authority,
        checker_token=checker_token,
        runtime_replay_input=authority.runtime.replay_input,
        capabilities=capabilities,
        capability_entries=capability_entries,  # type: ignore[arg-type]
    )
    _FULL_TRAVERSAL_GUARDS[key] = registered
    try:
        _require_full_traversal_capabilities(guard)
        yield guard
        _require_full_traversal_capabilities(guard)
    finally:
        current = _FULL_TRAVERSAL_GUARDS.get(key)
        if current is registered:
            _FULL_TRAVERSAL_GUARDS.pop(key, None)


def _canonical_load(semantic_bytes: bytes) -> facts.M8UncheckedFactBundleV2:
    if type(semantic_bytes) is not bytes:
        raise _CommonFactFailure("noncanonical_bundle")
    try:
        bundle = facts.M8UncheckedFactBundleV2.model_validate_json(
            semantic_bytes,
            strict=True,
        )
    except ValidationError as error:
        first = error.errors(include_url=False)[0] if error.errors() else {}
        context = first.get("ctx") or {}
        fact_sha256 = context.get("fact_sha256")
        if type(fact_sha256) is not str:
            location = first.get("loc") or ()
            if len(location) >= 2 and location[0] in _FACT_LAYER_NAMES and type(location[1]) is int:
                try:
                    submitted = json.loads(semantic_bytes)
                    entry = submitted[location[0]][location[1]]
                    candidate = entry.get("fact_sha256")
                except (
                    AttributeError,
                    IndexError,
                    KeyError,
                    TypeError,
                    UnicodeDecodeError,
                    ValueError,
                ):
                    candidate = None
                if type(candidate) is str and re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    candidate,
                ):
                    fact_sha256 = candidate
        code = str(first.get("type", ""))
        failure: M8CommonFactFailureCode = (
            code if code.startswith("m8_") else "noncanonical_bundle"  # type: ignore[assignment]
        )
        raise _CommonFactFailure(
            failure,
            fact_sha256 if type(fact_sha256) is str else None,
        ) from error
    except (TypeError, ValueError) as error:
        raise _CommonFactFailure("noncanonical_bundle") from error
    canonical = facts.canonical_semantic_json(bundle.model_dump(mode="json"))
    if canonical != semantic_bytes:
        first = bundle.common_lemmas[0].fact_sha256 if bundle.common_lemmas else None
        raise _CommonFactFailure("noncanonical_bundle", first)
    return bundle


def _fail(
    code: M8CommonFactFailureCode,
    *,
    checked: int = 0,
    audits: int = 0,
    issued: int = 0,
    fallbacks: int = 0,
    fallback_seconds: float = 0.0,
    fact_sha256: str | None = None,
) -> M8CommonFactCheckResult:
    return M8CommonFactCheckResult(
        valid=False,
        checked_common_lemma_count=checked,
        counted_translation_audit_count=audits,
        issued_common_capability_count=issued,
        exact_replay_fallback_count=fallbacks,
        exact_replay_fallback_wall_seconds=float(fallback_seconds),
        failure_code=code,
        first_failing_fact_sha256=fact_sha256,
    )


def _full_result(
    *,
    valid: bool,
    decision: M8OracleDecision | None,
    code: M8CheckedFactBundleFailureCode,
    state: _FullCheckState | None = None,
    checked_common: int = 0,
    checked_influences: int = 0,
    checked_roots: int = 0,
    issued: int = 0,
    fact_sha256: str | None = None,
) -> M8CheckedFactBundleResult:
    common = state.common if state is not None else None
    common_fallbacks = common.exact_replay_fallback_count if common is not None else 0
    common_seconds = common.exact_replay_fallback_wall_seconds if common is not None else 0.0
    influence_fallbacks = len(state.influence_exact_fallback_sha256s) if state is not None else 0
    influence_seconds = state.influence_exact_fallback_wall_seconds if state is not None else 0.0
    return M8CheckedFactBundleResult(
        valid=valid,
        decision=decision,
        checked_common_lemma_count=checked_common,
        checked_influence_fact_count=checked_influences,
        checked_action_root_count=checked_roots,
        counted_translation_audit_count=(
            len(common.audited_counted_lemma_sha256s) if common is not None else 0
        ),
        influence_translation_audit_count=(
            state.influence_translation_audit_count if state is not None else 0
        ),
        issued_common_capability_count=issued,
        common_exact_fallback_count=common_fallbacks,
        influence_exact_fallback_count=influence_fallbacks,
        total_exact_fallback_count=common_fallbacks + influence_fallbacks,
        common_exact_fallback_wall_seconds=float(common_seconds),
        influence_exact_fallback_wall_seconds=float(influence_seconds),
        total_exact_fallback_wall_seconds=float(common_seconds + influence_seconds),
        failure_code=code,
        first_failing_fact_sha256=fact_sha256,
    )


def _drain_common_capabilities(
    capabilities: list[ValidatedCommonTransition],
) -> None:
    first_error: BaseException | None = None
    while capabilities:
        capability = capabilities.pop()
        try:
            _release_validated_common_transition(capability)
        except BaseException as error:  # cleanup must continue across integrity failures.
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


def _rank_values(components: tuple[facts.M8PolicyRankComponentV2, ...]) -> tuple[object, ...]:
    return tuple(item.orderable_value() for item in components)


def _validate_standard_candidates(
    runtime,  # type: ignore[no-untyped-def]
    *,
    lemma: facts.M8CommonTransitionLemmaV2,
    catalog: M7ActionCatalog,
    selected_step: M7StepResult,
    standard_by_sha: dict[str, facts.M8StandardCandidateFactV2],
) -> None:
    standards = tuple(standard_by_sha[reference] for reference in lemma.standard_candidate_refs)
    profiles = catalog.generated.standard_profiles
    descriptors = tuple(
        item for item in catalog.actions if item.kind is M7ActionKind.OPEN_STANDARD_SHEET
    )
    contexts = {item.action_id: item for item in catalog.contexts}
    if len(standards) != len(profiles) or len(standards) != len(descriptors):
        raise _CommonFactFailure("standard_profile_mismatch", lemma.fact_sha256)
    rates = runtime.replay_input.rates
    for position, (portable, profile, descriptor) in enumerate(
        zip(standards, profiles, descriptors, strict=True)
    ):
        context = contexts.get(descriptor.action_id)
        if context is None:
            raise _CommonFactFailure("standard_profile_mismatch", portable.fact_sha256)
        accounting = profile.accounting
        rank = rank_policy_action(runtime.replay_input.policy.name, context)
        expected = (
            position,
            profile.candidate_id,
            descriptor.action_id,
            descriptor.candidate_id,
            context.selected_stock_id,
            runtime.replay_input.policy.name.value,
            facts.encode_canonical_f64(float(profile.candidate_width)),
            facts.encode_canonical_f64(float(accounting.parent_remnant_area)),
            facts.encode_canonical_f64(float(accounting.placed_area)),
            facts.encode_canonical_f64(float(accounting.process_loss_area)),
            facts.encode_canonical_f64(float(accounting.retained_child_area)),
            facts.encode_canonical_f64(float(accounting.scrap_area)),
            facts.encode_canonical_f64(float(accounting.reconciliation_delta)),
            facts.encode_canonical_f64(float(accounting.area_tolerance)),
            facts.encode_canonical_f64(
                rounded_cost(accounting.parent_remnant_area * rates.purchase_cost_per_area)
            ),
            facts.encode_canonical_f64(
                rounded_cost(accounting.retained_child_area * rates.storage_cost_per_area_hour)
            ),
            facts.encode_canonical_f64(
                rounded_cost(
                    profile.returned_remnant_count * rates.return_handling_cost_per_remnant
                )
            ),
            facts.encode_canonical_f64(0.0),
            facts.encode_canonical_f64(
                rounded_cost(accounting.scrap_area * rates.scrap_credit_per_area)
            ),
            facts.encode_canonical_f64(0.0),
            facts.encode_canonical_f64(float(context.immediate_net_cost)),
            profile.returned_remnant_count,
            facts.encode_canonical_f64(float(profile.returned_regularity)),
            facts.encode_canonical_f64(float(context.selected_remnant_age_hours)),
            facts.encode_canonical_f64(float(context.known_order_lookahead_term)),
            rank.comparison_key,
            rank.decision_key,
        )
        observed = (
            portable.profile_position,
            portable.candidate_id,
            portable.catalog_action_id,
            portable.candidate_id,
            portable.selected_stock_id,
            portable.policy_name,
            portable.candidate_width_bits,
            portable.parent_remnant_area_bits,
            portable.placed_area_bits,
            portable.process_loss_area_bits,
            portable.retained_child_area_bits,
            portable.scrap_area_bits,
            portable.reconciliation_delta_bits,
            portable.accounting_area_tolerance_bits,
            portable.purchase_cost_bits,
            portable.storage_cost_bits,
            portable.return_handling_cost_bits,
            portable.retrieval_handling_cost_bits,
            portable.scrap_proceeds_bits,
            portable.terminal_scrap_credit_bits,
            portable.immediate_net_cost_bits,
            portable.returned_remnant_count,
            portable.returned_regularity_bits,
            portable.selected_remnant_age_hours_bits,
            portable.known_order_lookahead_term_bits,
            _rank_values(portable.comparison_key),
            portable.decision_key,
        )
        if observed != expected:
            raise _CommonFactFailure("standard_profile_mismatch", portable.fact_sha256)
        expected_materialized_action_id = (
            selected_step.event.action.action_id
            if descriptor.action_id == selected_step.descriptor.action_id
            else None
        )
        if portable.materialized_action_id != expected_materialized_action_id:
            raise _CommonFactFailure("standard_profile_mismatch", portable.fact_sha256)
    minimum = min(standards, key=lambda item: _rank_values(item.comparison_key))
    if minimum.fact_sha256 != lemma.minimum_standard_candidate_ref:
        raise _CommonFactFailure("policy_minimum_mismatch", lemma.fact_sha256)


def _validate_inventory_evidence(
    runtime,  # type: ignore[no-untyped-def]
    *,
    lemma: facts.M8CommonTransitionLemmaV2,
    cursor: M7ReplayCursor,
    state: _CommonCheckState,
) -> tuple[int, GeneratedActionSet | None]:
    position = lemma.event_position
    binding = runtime.replay_input.instances[position]
    verified = runtime.runtime_candidates[binding.problem_id]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    rejection_layouts_supported = _verified_rejection_layouts_cover_candidates(verified)
    compiled = None
    if cursor.inventory and rejection_layouts_supported:
        partition_key = (binding.problem_id, verified.evidence.candidate_set_id)
        compiled = state.compiled_by_partition.get(partition_key)
        if compiled is None:
            compiled = compile_rejection_problem(runtime, event_position=position)
            state.compiled_by_partition[partition_key] = compiled
    expected_layouts = {item.candidate_id: item for item in verified.rejection_layouts}
    inventory_by_id = {item.remnant.remnant_id: item for item in cursor.inventory}
    generated = 0
    evaluated = 0
    truncated = 0
    audits = 0
    genuine_exact_survivor_count = 0
    for classification in lemma.inventory_classifications:
        inventory_item = inventory_by_id.get(classification.remnant_id)
        if inventory_item is None:
            raise _CommonFactFailure("inventory_classification_mismatch", lemma.fact_sha256)
        measured = prepare_translation_rejection_remnant(inventory_item.remnant)
        min_x, min_y, max_x, max_y = measured.bounds
        material_matches = material_key(inventory_item.remnant.material) == material_key(
            binding.material
        )
        area_tolerance = max(
            runtime.replay_input.fit_config.coordinate_tolerance,
            measured.area * runtime.replay_input.fit_config.relative_area_tolerance,
        )
        measurements = (
            classification.material_matches,
            classification.remnant_area_bits,
            classification.remnant_width_bits,
            classification.remnant_height_bits,
            classification.area_tolerance_bits,
            classification.coordinate_tolerance_bits,
        )
        expected_measurements = (
            material_matches,
            facts.encode_canonical_f64(float(measured.area)),
            facts.encode_canonical_f64(float(max_x - min_x)),
            facts.encode_canonical_f64(float(max_y - min_y)),
            facts.encode_canonical_f64(float(area_tolerance)),
            facts.encode_canonical_f64(float(runtime.replay_input.fit_config.coordinate_tolerance)),
        )
        if measurements != expected_measurements:
            raise _CommonFactFailure("inventory_classification_mismatch", lemma.fact_sha256)
        portable_scalars = tuple(
            state.scalar_by_sha[reference] for reference in classification.candidate_scalar_refs
        )
        if classification.classification != "exact_survivor" or portable_scalars:
            if compiled is None or classification.frontier_ref is None:
                raise _CommonFactFailure("frontier_mismatch", lemma.fact_sha256)
            portable_frontier = state.frontier_by_sha[classification.frontier_ref]
            if set(item.candidate_id for item in portable_scalars) != set(expected_layouts):
                raise _CommonFactFailure("candidate_scalar_mismatch", lemma.fact_sha256)
            scalar_ref_by_candidate = {
                item.candidate_id: item.fact_sha256 for item in portable_scalars
            }
            for portable in portable_scalars:
                if portable.fact_sha256 in state.validated_scalar_sha256s:
                    continue
                source = expected_layouts.get(portable.candidate_id)
                if source is None:
                    raise _CommonFactFailure("candidate_scalar_mismatch", portable.fact_sha256)
                expected_scalar = (
                    source.problem_id,
                    source.problem_sha256,
                    source.candidate_set_id,
                    source.candidate_set_sha256,
                    source.source_transform_sha256,
                    source.material_binding_scope,
                    source.fit_config_sha256,
                    facts.encode_canonical_f64(float(source.layout_area)),
                    facts.encode_canonical_f64(float(source.layout_width)),
                    facts.encode_canonical_f64(float(source.layout_height)),
                )
                observed_scalar = (
                    portable.problem_id,
                    portable.problem_sha256,
                    portable.candidate_set_id,
                    portable.candidate_set_sha256,
                    portable.source_transform_sha256,
                    portable.material_partition,
                    portable.fit_config_sha256,
                    portable.layout_area_bits,
                    portable.layout_width_bits,
                    portable.layout_height_bits,
                )
                if observed_scalar != expected_scalar:
                    raise _CommonFactFailure("candidate_scalar_mismatch", portable.fact_sha256)
                state.validated_scalar_sha256s.add(portable.fact_sha256)
            expected_members = tuple(
                scalar_ref_by_candidate[item.candidate_id] for item in compiled.frontier.members
            )
            expected_retained = tuple(
                sorted(
                    scalar_ref_by_candidate[item.candidate_id]
                    for item in compiled.frontier.retained
                )
            )
            expected_dominance = tuple(
                sorted(
                    (
                        scalar_ref_by_candidate[item.dominated_candidate_id],
                        scalar_ref_by_candidate[item.retained_candidate_id],
                    )
                    for item in compiled.frontier.dominated_by
                )
            )
            observed_dominance = tuple(
                (
                    item.dominated_candidate_scalar_ref,
                    item.retained_candidate_scalar_ref,
                )
                for item in portable_frontier.dominance_evidence
            )
            if portable_frontier.fact_sha256 not in state.validated_frontier_sha256s:
                if (
                    set(portable_frontier.candidate_scalar_refs) != set(expected_members)
                    or portable_frontier.retained_candidate_scalar_refs != expected_retained
                    or observed_dominance != expected_dominance
                ):
                    raise _CommonFactFailure("frontier_mismatch", portable_frontier.fact_sha256)
                state.validated_frontier_sha256s.add(portable_frontier.fact_sha256)
            query = {
                "material_matches": material_matches,
                "remnant_area": measured.area,
                "remnant_width": float(max_x - min_x),
                "remnant_height": float(max_y - min_y),
                "area_tolerance": area_tolerance,
                "coordinate_tolerance": runtime.replay_input.fit_config.coordinate_tolerance,
            }
            if classification.classification in {"scalar_no_fit", "counted_no_fit"} and not (
                certify_frontier_impossible(compiled.frontier, **query)
            ):
                raise _CommonFactFailure("inventory_classification_mismatch", lemma.fact_sha256)
        if classification.classification == "scalar_no_fit":
            zero_generation = not material_matches or all(
                item.width
                > float(max_x - min_x) + runtime.replay_input.fit_config.coordinate_tolerance
                or item.height
                > float(max_y - min_y) + runtime.replay_input.fit_config.coordinate_tolerance
                for item in compiled.frontier.members  # type: ignore[union-attr]
            )
            if not zero_generation:
                raise _CommonFactFailure("inventory_classification_mismatch", lemma.fact_sha256)
        elif classification.classification == "counted_no_fit" or (
            classification.exact_replay_reason == "counted_search_survivor"
        ):
            batches = tuple(
                state.translation_by_sha[reference]
                for reference in classification.translation_batch_refs
            )
            candidate_by_id = {item.candidate_id: item for item in verified.candidates}
            layouts = tuple(
                prepare_layout_footprint(
                    problem.problem,
                    candidate_by_id[batch.candidate_id],
                    runtime.replay_input.fit_config,
                )
                for batch in batches
            )
            prepared_remnant = prepare_remnant_geometry(inventory_item.remnant)
            expected_batches = tuple(
                LayoutTranslationCandidates(
                    candidate_id=batch.candidate_id,
                    remnant_id=batch.remnant_id,
                    translations=tuple(
                        (
                            facts.decode_canonical_f64(point.x_bits),
                            facts.decode_canonical_f64(point.y_bits),
                        )
                        for point in batch.translations
                    ),
                    generated_candidate_count=batch.generated_candidate_count,
                    duplicate_candidate_count=batch.duplicate_candidate_count,
                    budget_truncated=batch.budget_truncated,
                )
                for batch in batches
            )
            try:
                process_count = require_m8_translation_audit_processes()
                with profile_phase("counted_translation_audit_call"):
                    audit_layout_translation_batch(
                        remnant=prepared_remnant,
                        layouts=layouts,
                        expected=expected_batches,
                        fit_config=runtime.replay_input.fit_config,
                        search_config=runtime.replay_input.search_config,
                        process_count=process_count,
                    )
            except (TypeError, ValueError) as error:
                raise _CommonFactFailure("translation_count_mismatch", lemma.fact_sha256) from error
            state.audited_counted_lemma_sha256s.add(lemma.fact_sha256)
            audits += 1
            conservative_batches = tuple(
                certify_translation_impossible(
                    layout,
                    inventory_item.remnant,
                    material=binding.material,
                    fit_config=runtime.replay_input.fit_config,
                )
                for layout in layouts
            )
            if classification.classification == "counted_no_fit" and any(
                not item.impossible for item in conservative_batches
            ):
                raise _CommonFactFailure(
                    "inventory_classification_mismatch",
                    lemma.fact_sha256,
                )
            generated += sum(item.generated_candidate_count for item in batches)
            evaluated += sum(item.evaluated_candidate_count for item in batches)
            truncated += sum(item.budget_truncated for item in batches)
        elif classification.classification != "exact_survivor":
            raise _CommonFactFailure("inventory_classification_mismatch", lemma.fact_sha256)
        if classification.classification == "exact_survivor":
            candidates = verified.candidates
            prepared_layouts = tuple(
                prepare_layout_footprint(
                    problem.problem,
                    candidate,
                    runtime.replay_input.fit_config,
                )
                for candidate in candidates
            )
            conservative = tuple(
                certify_translation_impossible(
                    layout,
                    inventory_item.remnant,
                    material=binding.material,
                    fit_config=runtime.replay_input.fit_config,
                )
                for layout in prepared_layouts
            )
            if classification.exact_replay_reason == "unsupported_representation":
                if not rejection_layouts_supported:
                    pass
                elif runtime.jagua_executable is None:
                    raise _CommonFactFailure(
                        "inventory_classification_mismatch",
                        lemma.fact_sha256,
                    )
                else:
                    try:
                        run_jagua_generated_prefilter(
                            runtime.jagua_executable,
                            remnant=prepare_remnant_geometry(inventory_item.remnant),
                            layouts=prepared_layouts,
                            fit_config=runtime.replay_input.fit_config,
                            search_config=runtime.replay_input.search_config,
                            container_guard=runtime.replay_input.jagua_container_guard or 1.0,
                        )
                    except JaguaRepresentationError:
                        pass
                    else:
                        raise _CommonFactFailure(
                            "inventory_classification_mismatch",
                            lemma.fact_sha256,
                        )
            elif all(item.impossible for item in conservative):
                raise _CommonFactFailure(
                    "inventory_classification_mismatch",
                    lemma.fact_sha256,
                )
            genuine_exact_survivor_count += 1
    if (lemma.evidence_mode == "exact_replay") != bool(genuine_exact_survivor_count):
        raise _CommonFactFailure("inventory_classification_mismatch", lemma.fact_sha256)
    if lemma.evidence_mode == "exact_replay":
        return int(audits > 0), None
    return int(audits > 0), GeneratedActionSet(
        standard_profiles=(),
        remnant_actions=(),
        remnant_action_count=0,
        fit_search_query_count=sum(
            material_key(item.remnant.material) == material_key(binding.material)
            for item in cursor.inventory
        )
        * len(verified.candidates),
        fit_search_generated_candidate_count=generated,
        fit_search_evaluated_candidate_count=evaluated,
        fit_search_budget_truncated_count=truncated,
    )


def _fact_from_step(
    runtime,  # type: ignore[no-untyped-def]
    *,
    cursor: M7ReplayCursor,
    step: M7StepResult,
    semantic_runtime_sha256: str,
) -> M8CommonTransitionFact:
    rank = rank_policy_action(runtime.replay_input.policy.name, step.selected_context)
    payload = _common_fact_payload(
        runtime,
        event_position=cursor.next_event_position,
        cursor_before=cursor,
        step=step,
        policy_rank=rank,
        semantic_runtime_sha256=semantic_runtime_sha256,
    )
    return M8CommonTransitionFact(
        replay_input_id=runtime.replay_input.input_id,
        replay_input_sha256=runtime.replay_input.content_sha256,
        event_position=cursor.next_event_position,
        cursor_before=cursor,
        cursor_before_sha256=m7_cursor_sha256(cursor),
        step=step,
        cursor_after_sha256=m7_cursor_sha256(step.cursor),
        event_id=step.event.event_id,
        policy_rank=rank,
        semantic_runtime_sha256=semantic_runtime_sha256,
        content_sha256=f"sha256:{semantic_sha256(payload)}",
    )


def _reconcile_common_transition(
    authority: M7AuthoritativeProofRuntime,
    *,
    lemma: facts.M8CommonTransitionLemmaV2,
    cursor: M7ReplayCursor,
    state: _CommonCheckState,
    catalog: M7ActionCatalog,
    audits: int,
) -> tuple[M8CommonTransitionFact, int, int, float]:
    runtime = authority.runtime
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selected.action_id)
    step = apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selected.decision_key,
    )
    _validate_standard_candidates(
        runtime,
        lemma=lemma,
        catalog=catalog,
        selected_step=step,
        standard_by_sha=state.standard_by_sha,
    )
    expected_fact = _fact_from_step(
        runtime,
        cursor=cursor,
        step=step,
        semantic_runtime_sha256=authority.semantic_sha256,
    )
    if selected.action_id != lemma.selected_catalog_action_id:
        raise _CommonFactFailure("policy_minimum_mismatch", lemma.fact_sha256)
    if _portable_common_transition(expected_fact) != lemma.portable_transition or (
        expected_fact.content_sha256 != lemma.legacy_common_fact_sha256
    ):
        raise _CommonFactFailure("portable_transition_mismatch", lemma.fact_sha256)
    return expected_fact, audits, 0, 0.0


def _validate_one_common_unmeasured(
    authority: M7AuthoritativeProofRuntime,
    *,
    lemma: facts.M8CommonTransitionLemmaV2,
    cursor: M7ReplayCursor,
    bundle: facts.M8UncheckedFactBundleV2,
    state: _CommonCheckState,
    allow_exact_replay: bool,
    expected_jagua_sha256: str | None,
) -> tuple[M8CommonTransitionFact, int, int, float]:
    runtime = authority.runtime
    if lemma.cursor_before_sha256 != m7_cursor_sha256(cursor):
        raise _CommonFactFailure("cursor_chain_mismatch", lemma.fact_sha256)
    binding = runtime.replay_input.instances[lemma.event_position]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    fit_config_sha256 = (
        f"sha256:{semantic_sha256(runtime.replay_input.fit_config.model_dump(mode='json'))}"
    )
    portable_search_config = _portable_search_config(runtime.replay_input.search_config)
    search_config_sha256 = (
        "sha256:"
        + hashlib.sha256(
            facts.canonical_semantic_json(portable_search_config.model_dump(mode="json"))
        ).hexdigest()
    )
    expected_source = (
        problem.problem_id,
        problem.content_sha256,
        verified.evidence.candidate_set_id,
        verified.evidence.content_sha256,
        fit_config_sha256,
        search_config_sha256,
        runtime.replay_input.collision_backend,
        expected_jagua_sha256,
    )
    observed_source = (
        lemma.problem_id,
        lemma.problem_sha256,
        lemma.candidate_set_id,
        lemma.candidate_set_sha256,
        lemma.fit_config_sha256,
        lemma.search_config_sha256,
        lemma.collision_backend,
        lemma.jagua_executable_sha256,
    )
    if observed_source != expected_source:
        raise _CommonFactFailure("runtime_binding_mismatch", lemma.fact_sha256)
    if lemma.evidence_mode == "exact_replay":
        if not allow_exact_replay:
            raise _CommonFactFailure("implicit_exact_replay", lemma.fact_sha256)
    audits, generated_counts = _validate_inventory_evidence(
        runtime,
        lemma=lemma,
        cursor=cursor,
        state=state,
    )
    if lemma.evidence_mode == "exact_replay":
        state.exact_replay_fallback_count += 1
        started = perf_counter()
        try:
            catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=False)
            return _reconcile_common_transition(
                authority,
                lemma=lemma,
                cursor=cursor,
                state=state,
                catalog=catalog,
                audits=audits,
            )
        finally:
            state.exact_replay_fallback_wall_seconds += perf_counter() - started

    catalog = enumerate_m7_standard_only_catalog(
        runtime,
        cursor=cursor,
        zero_generation_rejected_inventory=cursor.inventory,
    )
    if generated_counts is None:  # pragma: no cover - exact mode returned above.
        raise AssertionError("fact-certified common lacks generated counts")
    catalog = replace(
        catalog,
        generated=replace(
            catalog.generated,
            fit_search_query_count=generated_counts.fit_search_query_count,
            fit_search_generated_candidate_count=(
                generated_counts.fit_search_generated_candidate_count
            ),
            fit_search_evaluated_candidate_count=(
                generated_counts.fit_search_evaluated_candidate_count
            ),
            fit_search_budget_truncated_count=(generated_counts.fit_search_budget_truncated_count),
        ),
    )
    return _reconcile_common_transition(
        authority,
        lemma=lemma,
        cursor=cursor,
        state=state,
        catalog=catalog,
        audits=audits,
    )


def _validate_one_common(
    authority: M7AuthoritativeProofRuntime,
    *,
    lemma: facts.M8CommonTransitionLemmaV2,
    cursor: M7ReplayCursor,
    bundle: facts.M8UncheckedFactBundleV2,
    state: _CommonCheckState,
    allow_exact_replay: bool,
    expected_jagua_sha256: str | None,
) -> tuple[M8CommonTransitionFact, int, int, float]:
    """Validate one common lemma and return operation-local fallback deltas."""

    fallback_count_before = state.exact_replay_fallback_count
    fallback_seconds_before = state.exact_replay_fallback_wall_seconds
    fact, audits, _fallback_count, _fallback_seconds = _validate_one_common_unmeasured(
        authority,
        lemma=lemma,
        cursor=cursor,
        bundle=bundle,
        state=state,
        allow_exact_replay=allow_exact_replay,
        expected_jagua_sha256=expected_jagua_sha256,
    )
    return (
        fact,
        audits,
        state.exact_replay_fallback_count - fallback_count_before,
        state.exact_replay_fallback_wall_seconds - fallback_seconds_before,
    )


def _require_full_request_stable(
    source: M8FactBundleCheckRequest,
    captured: M8FactBundleCheckRequest,
    authority: M7AuthoritativeProofRuntime | None = None,
    *,
    fact_sha256: str | None = None,
) -> None:
    try:
        source.require_valid()
        captured.require_valid()
        if authority is not None:
            authority.require_active(authority.runtime)
    except (AttributeError, TypeError, ValueError) as error:
        raise _FullFactFailure("runtime_binding_mismatch", fact_sha256) from error


def _fresh_exact_runtime(runtime: M7ReplayRuntime) -> M7ReplayRuntime:
    """Build one cache-free exact runtime without invoking producer/capture authority."""

    return M7ReplayRuntime(
        replay_input=runtime.replay_input,
        runtime_candidates=runtime.runtime_candidates,
        rules=runtime.rules,
        standard_profile_cache={},
        fit_search_cache={},
        shared_fit_search_cache={},
        prepared_layout_cache=OrderedDict(),
        standard_profile_executor=runtime.standard_profile_executor,
        jagua_executable=runtime.jagua_executable,
        jagua_differential_audit=runtime.jagua_differential_audit,
    )


def _derive_inventory_delta(
    common_cursor: M7ReplayCursor,
    branch_cursor: M7ReplayCursor,
    *,
    owner_sha256: str,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    common_metadata = (
        common_cursor.next_event_position,
        common_cursor.current_time,
        common_cursor.timestamp_group_sequence,
        common_cursor.timestamp_subsequence,
        common_cursor.previous_release,
    )
    branch_metadata = (
        branch_cursor.next_event_position,
        branch_cursor.current_time,
        branch_cursor.timestamp_group_sequence,
        branch_cursor.timestamp_subsequence,
        branch_cursor.previous_release,
    )
    if common_metadata != branch_metadata:
        raise _FullFactFailure("influence_state_mismatch", owner_sha256)
    common_by_id = {item.remnant.remnant_id: item for item in common_cursor.inventory}
    branch_by_id = {item.remnant.remnant_id: item for item in branch_cursor.inventory}
    for remnant_id in set(common_by_id) & set(branch_by_id):
        if common_by_id[remnant_id] != branch_by_id[remnant_id]:
            raise _FullFactFailure("influence_state_mismatch", owner_sha256)
    added = tuple(branch_by_id[key] for key in sorted(set(branch_by_id) - set(common_by_id)))
    removed = tuple(common_by_id[key] for key in sorted(set(common_by_id) - set(branch_by_id)))
    return added, removed


def _validate_influence_scalar(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    scalar_ref: str,
    expected_candidate_id: str,
    state: _FullCheckState,
    owner_sha256: str,
) -> facts.M8CandidateScalarFactV2:
    portable = state.common.scalar_by_sha.get(scalar_ref)
    if portable is None or portable.candidate_id != expected_candidate_id:
        raise _FullFactFailure("influence_rejection_mismatch", owner_sha256)
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    source = next(
        (item for item in verified.rejection_layouts if item.candidate_id == expected_candidate_id),
        None,
    )
    if source is None:
        raise _FullFactFailure("influence_rejection_mismatch", owner_sha256)
    expected = (
        source.problem_id,
        source.problem_sha256,
        source.candidate_set_id,
        source.candidate_set_sha256,
        source.source_transform_sha256,
        source.material_binding_scope,
        source.fit_config_sha256,
        facts.encode_canonical_f64(float(source.layout_area)),
        facts.encode_canonical_f64(float(source.layout_width)),
        facts.encode_canonical_f64(float(source.layout_height)),
    )
    observed = (
        portable.problem_id,
        portable.problem_sha256,
        portable.candidate_set_id,
        portable.candidate_set_sha256,
        portable.source_transform_sha256,
        portable.material_partition,
        portable.fit_config_sha256,
        portable.layout_area_bits,
        portable.layout_width_bits,
        portable.layout_height_bits,
    )
    if observed != expected:
        raise _FullFactFailure("influence_rejection_mismatch", owner_sha256)
    state.common.validated_scalar_sha256s.add(portable.fact_sha256)
    return portable


def _validate_rejections_for_item(
    runtime: M7ReplayRuntime,
    *,
    influence: facts.M8InfluenceFactV2,
    direction: Literal["added", "removed"],
    item,  # type: ignore[no-untyped-def]
    state: _FullCheckState,
    require_portable: bool,
) -> tuple[bool, ...]:
    expected = compile_translation_rejections(
        runtime,
        event_position=influence.event_position,
        item=item,
    )
    portable_by_candidate = {
        row.candidate_id: row
        for row in influence.rejection_evidence
        if row.direction == direction and row.remnant_id == item.remnant.remnant_id
    }
    if require_portable and set(portable_by_candidate) != {row.candidate_id for row in expected}:
        raise _FullFactFailure("influence_rejection_mismatch", influence.fact_sha256)
    if not require_portable:
        if portable_by_candidate:
            raise _FullFactFailure("influence_rejection_mismatch", influence.fact_sha256)
        return tuple(row.certificate.impossible for row in expected)
    impossible = []
    for row in expected:
        portable = portable_by_candidate[row.candidate_id]
        scalar = _validate_influence_scalar(
            runtime,
            event_position=influence.event_position,
            scalar_ref=portable.candidate_scalar_ref,
            expected_candidate_id=row.candidate_id,
            state=state,
            owner_sha256=influence.fact_sha256,
        )
        certificate = row.certificate
        expected_values = (
            direction,
            item.remnant.remnant_id,
            row.candidate_id,
            scalar.fact_sha256,
            certificate.impossible,
            certificate.reason,
            facts.encode_canonical_f64(float(certificate.layout_area)),
            facts.encode_canonical_f64(float(certificate.remnant_area)),
            facts.encode_canonical_f64(float(certificate.layout_width)),
            facts.encode_canonical_f64(float(certificate.remnant_width)),
            facts.encode_canonical_f64(float(certificate.layout_height)),
            facts.encode_canonical_f64(float(certificate.remnant_height)),
            facts.encode_canonical_f64(float(certificate.area_tolerance)),
        )
        observed_values = (
            portable.direction,
            portable.remnant_id,
            portable.candidate_id,
            portable.candidate_scalar_ref,
            portable.impossible,
            portable.reason,
            portable.layout_area_bits,
            portable.remnant_area_bits,
            portable.layout_width_bits,
            portable.remnant_width_bits,
            portable.layout_height_bits,
            portable.remnant_height_bits,
            portable.area_tolerance_bits,
        )
        if observed_values != expected_values:
            raise _FullFactFailure("influence_rejection_mismatch", influence.fact_sha256)
        impossible.append(certificate.impossible)
    return tuple(impossible)


def _portable_search_config_values(config) -> tuple[object, ...]:  # type: ignore[no-untyped-def]
    return (
        config.grid_columns,
        config.grid_rows,
        config.maximum_candidates,
        config.candidate_source_order,
    )


def _validate_exact_item_evidence(
    runtime: M7ReplayRuntime,
    *,
    common_fact: M8CommonTransitionFact,
    influence: facts.M8InfluenceFactV2,
    direction: Literal["added", "removed"],
    item,  # type: ignore[no-untyped-def]
    state: _FullCheckState,
    require_portable: bool,
) -> Literal["no_fit", "policy_dominated", "exact_transition"]:
    binding = runtime.replay_input.instances[influence.event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    problem = next(
        source
        for source in runtime.replay_input.problems
        if source.problem_id == binding.problem_id
    )
    prepared_remnant = prepare_remnant_geometry(item.remnant)
    layouts = tuple(
        prepare_layout_footprint(
            problem.problem,
            candidate,
            runtime.replay_input.fit_config,
        )
        for candidate in verified.candidates
    )
    batches = tuple(
        generate_layout_translations(
            item.remnant,
            candidate,
            fit_config=runtime.replay_input.fit_config,
            search_config=runtime.replay_input.search_config,
            prepared_layout=layout,
            prepared_remnant=prepared_remnant,
        )
        for candidate, layout in zip(verified.candidates, layouts, strict=True)
    )
    searches = tuple(
        search_layout_translation(
            item.remnant,
            problem.problem,
            candidate,
            material=binding.material,
            fit_config=runtime.replay_input.fit_config,
            search_config=runtime.replay_input.search_config,
            prepared_layout=layout,
            prepared_remnant=prepared_remnant,
            translation_candidates=batch,
            collision_prefilter=None,
        )
        for candidate, layout, batch in zip(
            verified.candidates,
            layouts,
            batches,
            strict=True,
        )
    )
    fresh = _fresh_exact_runtime(runtime)
    cache_key = (
        item.remnant.remnant_id,
        binding.problem_id,
        verified.evidence.candidate_set_id,
    )
    fresh.fit_search_cache[cache_key] = searches
    competitor, context = enumerate_m7_single_remnant_competitor(
        fresh,
        event_position=influence.event_position,
        item=item,
        cursor_template=common_fact.cursor_before,
    )
    portable_rows = tuple(
        row
        for row in influence.search_evidence
        if row.direction == direction and row.remnant_id == item.remnant.remnant_id
    )
    portable_by_candidate = {row.candidate_id: row for row in portable_rows}
    if require_portable and set(portable_by_candidate) != {
        candidate.candidate_id for candidate in verified.candidates
    }:
        raise _FullFactFailure("influence_search_mismatch", influence.fact_sha256)
    if not require_portable and portable_rows:
        raise _FullFactFailure("influence_search_mismatch", influence.fact_sha256)
    if require_portable:
        portable_batches = []
        for candidate, search, expected_batch in zip(
            verified.candidates,
            searches,
            batches,
            strict=True,
        ):
            portable = portable_by_candidate[candidate.candidate_id]
            selected = (
                facts.M8TranslationPointV2(
                    x_bits=facts.encode_canonical_f64(float(search.translation[0])),
                    y_bits=facts.encode_canonical_f64(float(search.translation[1])),
                )
                if search.translation is not None
                else None
            )
            expected_search = (
                direction,
                item.remnant.remnant_id,
                search.candidate_id,
                _portable_search_config_values(search.config),
                search.generated_candidate_count,
                search.duplicate_candidate_count,
                search.evaluated_candidate_count,
                search.budget_truncated,
                search.status.value,
                selected,
            )
            observed_search = (
                portable.direction,
                portable.remnant_id,
                portable.candidate_id,
                _portable_search_config_values(portable.search_config),
                portable.generated_candidate_count,
                portable.duplicate_candidate_count,
                portable.evaluated_candidate_count,
                portable.budget_truncated,
                portable.result,
                portable.selected_translation,
            )
            if observed_search != expected_search or (
                _portable_search_config_values(portable.search_config)
                != _portable_search_config_values(runtime.replay_input.search_config)
            ):
                raise _FullFactFailure("influence_search_mismatch", influence.fact_sha256)
            batch = state.common.translation_by_sha.get(portable.translation_batch_ref)
            if batch is None:
                raise _FullFactFailure("influence_search_mismatch", influence.fact_sha256)
            portable_batch = LayoutTranslationCandidates(
                candidate_id=batch.candidate_id,
                remnant_id=batch.remnant_id,
                translations=tuple(
                    (
                        facts.decode_canonical_f64(point.x_bits),
                        facts.decode_canonical_f64(point.y_bits),
                    )
                    for point in batch.translations
                ),
                generated_candidate_count=batch.generated_candidate_count,
                duplicate_candidate_count=batch.duplicate_candidate_count,
                budget_truncated=batch.budget_truncated,
            )
            if portable_batch != expected_batch:
                raise _FullFactFailure("influence_search_mismatch", influence.fact_sha256)
            portable_batches.append(
                LayoutTranslationCandidates(
                    candidate_id=batch.candidate_id,
                    remnant_id=batch.remnant_id,
                    translations=tuple(
                        (
                            facts.decode_canonical_f64(point.x_bits),
                            facts.decode_canonical_f64(point.y_bits),
                        )
                        for point in batch.translations
                    ),
                    generated_candidate_count=batch.generated_candidate_count,
                    duplicate_candidate_count=batch.duplicate_candidate_count,
                    budget_truncated=batch.budget_truncated,
                )
            )
        audit_refs = {row.translation_batch_ref for row in portable_rows}
        if not audit_refs <= state.audited_influence_translation_sha256s:
            try:
                audit_layout_translation_batch(
                    remnant=prepared_remnant,
                    layouts=layouts,
                    expected=tuple(portable_batches),
                    fit_config=runtime.replay_input.fit_config,
                    search_config=runtime.replay_input.search_config,
                    process_count=require_m8_translation_audit_processes(),
                )
            except (TypeError, ValueError) as error:
                raise _FullFactFailure(
                    "influence_search_mismatch",
                    influence.fact_sha256,
                ) from error
            state.audited_influence_translation_sha256s.update(audit_refs)
            state.influence_translation_audit_count += 1

    portable_competitors = tuple(
        row
        for row in influence.competitor_evidence
        if row.direction == direction and row.selected_remnant_id == item.remnant.remnant_id
    )
    if not require_portable and portable_competitors:
        raise _FullFactFailure("influence_competitor_mismatch", influence.fact_sha256)
    if competitor is None or context is None:
        if competitor is not None or context is not None or portable_competitors:
            raise _FullFactFailure("influence_competitor_mismatch", influence.fact_sha256)
        return "no_fit"
    if competitor.evidence is None:
        raise _FullFactFailure("influence_competitor_mismatch", influence.fact_sha256)
    rank = rank_policy_action(runtime.replay_input.policy.name, context)
    if require_portable:
        if len(portable_competitors) != 1:
            raise _FullFactFailure("influence_competitor_mismatch", influence.fact_sha256)
        portable = portable_competitors[0]
        expected_competitor = (
            direction,
            competitor.candidate_id,
            competitor.action_id,
            competitor.evidence.action_id,
            competitor.evidence.content_sha256,
            competitor.selected_remnant_id,
            competitor.kind.value,
            context.selected_stock_id,
            facts.encode_canonical_f64(float(context.candidate_width)),
            facts.encode_canonical_f64(float(context.immediate_net_cost)),
            facts.encode_canonical_f64(float(context.selected_remnant_age_hours)),
            facts.encode_canonical_f64(float(context.returned_regularity)),
            facts.encode_canonical_f64(float(context.known_order_lookahead_term)),
            rank.policy.value,
            rank.comparison_key,
            rank.decision_key,
        )
        observed_competitor = (
            portable.direction,
            portable.candidate_id,
            portable.catalog_action_id,
            portable.materialized_action_id,
            portable.materialized_content_sha256,
            portable.selected_remnant_id,
            portable.action_kind,
            portable.selected_stock_id,
            portable.candidate_width_bits,
            portable.immediate_net_cost_bits,
            portable.selected_remnant_age_hours_bits,
            portable.returned_regularity_bits,
            portable.known_order_lookahead_term_bits,
            portable.policy_name,
            _rank_values(portable.comparison_key),
            portable.decision_key,
        )
        if observed_competitor != expected_competitor:
            raise _FullFactFailure("influence_competitor_mismatch", influence.fact_sha256)
    if common_fact.policy_rank <= rank:
        return "policy_dominated"
    return "exact_transition"


def _derive_influence_classification(
    runtime: M7ReplayRuntime,
    *,
    common_fact: M8CommonTransitionFact,
    influence: facts.M8InfluenceFactV2,
    branch_cursor: M7ReplayCursor,
    state: _FullCheckState,
    require_portable_exact: bool,
    declared_exact_mode: bool,
) -> tuple[
    Literal["state_rejoin", "no_fit", "policy_dominated", "exact_transition"],
    tuple[object, ...],
    tuple[object, ...],
]:
    if branch_cursor == common_fact.cursor_before:
        return "state_rejoin", (), ()
    added, removed = _derive_inventory_delta(
        common_fact.cursor_before,
        branch_cursor,
        owner_sha256=influence.fact_sha256,
    )
    observed_delta = (
        tuple(item.remnant.remnant_id for item in removed),
        tuple(item.remnant.remnant_id for item in added),
    )
    expected_delta = (
        influence.inventory_delta.removed_remnant_ids,
        influence.inventory_delta.added_remnant_ids,
    )
    if observed_delta != expected_delta:
        raise _FullFactFailure("influence_state_mismatch", influence.fact_sha256)
    if not added and not removed:
        return "exact_transition", added, removed
    selected_stock_id = common_fact.step.action_binding.context.selected_stock_id
    if selected_stock_id in {item.remnant.remnant_id for item in removed}:
        return "exact_transition", added, removed
    binding = runtime.replay_input.instances[influence.event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    if not _verified_rejection_layouts_cover_candidates(verified):
        return "exact_transition", added, removed

    item_classifications = []
    for direction, items in (("added", added), ("removed", removed)):
        for item in items:
            rejections = _validate_rejections_for_item(
                runtime,
                influence=influence,
                direction=direction,
                item=item,
                state=state,
                require_portable=(influence.classification != "exact_transition"),
            )
            if rejections and all(rejections):
                if any(
                    row.direction == direction and row.remnant_id == item.remnant.remnant_id
                    for row in influence.search_evidence
                ):
                    raise _FullFactFailure("influence_search_mismatch", influence.fact_sha256)
                item_classifications.append("no_fit")
                continue
            if not declared_exact_mode:
                raise _FullFactFailure(
                    "influence_classification_mismatch",
                    influence.fact_sha256,
                )
            item_classifications.append(
                _validate_exact_item_evidence(
                    runtime,
                    common_fact=common_fact,
                    influence=influence,
                    direction=direction,
                    item=item,
                    state=state,
                    require_portable=require_portable_exact,
                )
            )
    if "exact_transition" in item_classifications:
        return "exact_transition", added, removed
    if "policy_dominated" in item_classifications:
        return "policy_dominated", added, removed
    return "no_fit", added, removed


def _validate_one_influence(
    authority: M7AuthoritativeProofRuntime,
    *,
    influence: facts.M8InfluenceFactV2,
    common_fact: M8CommonTransitionFact,
    branch_cursor: M7ReplayCursor,
    state: _FullCheckState,
    allow_exact_replay: bool,
) -> M7ReplayCursor:
    cached = state.influence_transition_by_sha256.get(influence.fact_sha256)
    branch_sha256 = m7_cursor_sha256(branch_cursor)
    if cached is not None:
        if cached[0] != branch_sha256:
            raise _FullFactFailure("influence_state_mismatch", influence.fact_sha256)
        return cached[2]
    if (
        influence.event_position != branch_cursor.next_event_position
        or influence.event_position != common_fact.event_position
        or influence.state_before_sha256 != branch_sha256
        or influence.common_catalog_action_id != common_fact.step.descriptor.action_id
        or influence.common_materialized_action_id != common_fact.step.event.action.action_id
    ):
        raise _FullFactFailure("influence_state_mismatch", influence.fact_sha256)
    if influence.classification == "exact_transition":
        if influence.rejection_evidence:
            raise _FullFactFailure("influence_rejection_mismatch", influence.fact_sha256)
        if influence.search_evidence:
            raise _FullFactFailure("influence_search_mismatch", influence.fact_sha256)
        if influence.competitor_evidence:
            raise _FullFactFailure("influence_competitor_mismatch", influence.fact_sha256)
    exact_mode = influence.evidence_mode in {
        "policy_dominated_exact_check",
        "exact_transition",
    }
    if exact_mode and not allow_exact_replay:
        raise _FullFactFailure("implicit_exact_replay", influence.fact_sha256)
    started = 0.0
    if exact_mode:
        state.influence_exact_fallback_sha256s.add(influence.fact_sha256)
        started = perf_counter()
    try:
        actual, _added, _removed = _derive_influence_classification(
            authority.runtime,
            common_fact=common_fact,
            influence=influence,
            branch_cursor=branch_cursor,
            state=state,
            require_portable_exact=(influence.classification != "exact_transition"),
            declared_exact_mode=exact_mode,
        )
        if actual != influence.classification:
            raise _FullFactFailure(
                "influence_classification_mismatch",
                influence.fact_sha256,
            )
        if actual == "state_rejoin":
            if (
                influence.inventory_delta.removed_remnant_ids
                or influence.inventory_delta.added_remnant_ids
                or influence.evidence_mode != "state_rejoin"
            ):
                raise _FullFactFailure(
                    "influence_classification_mismatch",
                    influence.fact_sha256,
                )
            next_cursor = apply_m7_frozen_action_evidence(
                authority.runtime,
                cursor=branch_cursor,
                event_position=influence.event_position,
                action=common_fact.step.event.action,
            )
            expected_materialized = common_fact.step.event.action.action_id
        elif actual in {"no_fit", "policy_dominated"}:
            expected_mode = (
                "scalar_no_fit"
                if actual == "no_fit" and not influence.search_evidence
                else ("exact_transition" if actual == "no_fit" else "policy_dominated_exact_check")
            )
            if influence.evidence_mode != expected_mode:
                raise _FullFactFailure(
                    "influence_classification_mismatch",
                    influence.fact_sha256,
                )
            next_cursor = apply_m7_frozen_action_evidence(
                authority.runtime,
                cursor=branch_cursor,
                event_position=influence.event_position,
                action=common_fact.step.event.action,
            )
            expected_materialized = common_fact.step.event.action.action_id
        else:
            if influence.evidence_mode != "exact_transition":
                raise _FullFactFailure(
                    "influence_classification_mismatch",
                    influence.fact_sha256,
                )
            catalog = enumerate_m7_action_catalog(
                authority.runtime,
                cursor=branch_cursor,
                complete=False,
            )
            selected = select_m7_fallback(
                catalog,
                policy=authority.runtime.replay_input.policy,
            )
            descriptor = next(
                item for item in catalog.actions if item.action_id == selected.action_id
            )
            step = apply_m7_action_descriptor(
                authority.runtime,
                cursor=branch_cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=selected.decision_key,
            )
            if influence.branch_catalog_action_id != descriptor.action_id:
                raise _FullFactFailure("influence_action_mismatch", influence.fact_sha256)
            next_cursor = step.cursor
            expected_materialized = step.event.action.action_id
        if actual != "exact_transition" and (
            influence.branch_catalog_action_id != common_fact.step.descriptor.action_id
            or influence.branch_materialized_action_id != common_fact.step.event.action.action_id
        ):
            raise _FullFactFailure("influence_action_mismatch", influence.fact_sha256)
        if influence.branch_materialized_action_id != expected_materialized:
            raise _FullFactFailure("influence_action_mismatch", influence.fact_sha256)
        after_sha256 = m7_cursor_sha256(next_cursor)
        if influence.state_after_sha256 != after_sha256:
            raise _FullFactFailure("influence_state_mismatch", influence.fact_sha256)
        state.checked_influence_sha256s.add(influence.fact_sha256)
        state.influence_transition_by_sha256[influence.fact_sha256] = (
            branch_sha256,
            after_sha256,
            next_cursor,
        )
        return next_cursor
    finally:
        if exact_mode:
            state.influence_exact_fallback_wall_seconds += perf_counter() - started


def _validate_context(
    request: M8CommonFactCheckRequest,
    authority: M7AuthoritativeProofRuntime,
    bundle: facts.M8UncheckedFactBundleV2,
    checker_cursor: M7ReplayCursor,
) -> tuple[M7StepResult, int, str]:
    runtime = authority.runtime
    cursor = checker_cursor
    if authority.semantic_sha256 != request.expected_semantic_runtime_sha256:
        raise _CommonFactFailure("runtime_binding_mismatch")
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    if (
        catalog.event_position != request.expected_catalog_event_position
        or tuple(item.action_id for item in catalog.actions) != request.expected_catalog_action_ids
    ):
        raise _CommonFactFailure("catalog_binding_mismatch")
    stop = request.expected_stop_event_position
    registered = runtime.replay_input.instances
    if stop > len(registered):
        raise _CommonFactFailure("suffix_binding_mismatch")
    expected_visible = registered[catalog.event_position + 1 : stop]
    visible_source = request.oracle_request.visibility.visible_suffix(
        current_position=catalog.event_position
    )
    try:
        visible = tuple(
            TemporalInstanceBinding.model_validate(
                deepcopy(item).model_dump(mode="python"),
                strict=True,
            )
            for item in visible_source
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise _CommonFactFailure("suffix_binding_mismatch") from error
    if visible != visible_source:
        raise _CommonFactFailure("suffix_binding_mismatch")
    if visible != expected_visible or any(
        item.partition is not TemporalPartition.CALIBRATION for item in visible
    ):
        raise _CommonFactFailure("suffix_binding_mismatch")
    suffix = m8_suffix_sha256(
        semantic_runtime_sha256=authority.semantic_sha256,
        start_event_position=catalog.event_position,
        stop_event_position=stop,
        bindings=visible,
    )
    if stop != request.expected_stop_event_position or suffix != request.expected_suffix_sha256:
        raise _CommonFactFailure("suffix_binding_mismatch")
    provenance = bundle.provenance
    replay_input = runtime.replay_input
    if (
        provenance.replay_input_id != replay_input.input_id
        or provenance.replay_input_sha256 != replay_input.content_sha256
        or provenance.semantic_runtime_sha256 != authority.semantic_sha256
        or provenance.stream_id != replay_input.stream_id
        or provenance.stream_sha256 != replay_input.stream_sha256
    ):
        first = bundle.common_lemmas[0].fact_sha256 if bundle.common_lemmas else None
        raise _CommonFactFailure("runtime_binding_mismatch", first)
    dimension = replay_input.instances[0]
    if provenance.regime != dimension.regime.value or (
        provenance.temporal_seed != dimension.temporal_seed
    ):
        first = bundle.common_lemmas[0].fact_sha256 if bundle.common_lemmas else None
        raise _CommonFactFailure("runtime_binding_mismatch", first)
    if provenance.suffix_sha256 != suffix:
        first = bundle.common_lemmas[0].fact_sha256 if bundle.common_lemmas else None
        raise _CommonFactFailure("suffix_binding_mismatch", first)
    if (
        provenance.freeze_id != request.expected_freeze_id
        or provenance.freeze_sha256 != request.expected_freeze_sha256
    ):
        first = bundle.common_lemmas[0].fact_sha256 if bundle.common_lemmas else None
        raise _CommonFactFailure("freeze_binding_mismatch", first)
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selected.action_id)
    fallback = apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selected.decision_key,
    )
    return fallback, stop, suffix


def _validate_action_root(
    authority: M7AuthoritativeProofRuntime,
    *,
    captured_request: M8FactBundleCheckRequest,
    traversal_guard: _M8FullTraversalGuard,
    root: facts.M8ActionRootV2,
    catalog: M7ActionCatalog,
    fallback: M7StepResult,
    checker_cursor: M7ReplayCursor,
    stop: int,
    common_refs: tuple[str, ...],
    common_fact_by_ref: dict[str, M8CommonTransitionFact],
    influence_by_ref: dict[str, facts.M8InfluenceFactV2],
    state: _FullCheckState,
) -> tuple[str, float]:
    _require_full_traversal_guard(traversal_guard)
    if (
        root.semantic_runtime_sha256 != authority.semantic_sha256
        or root.stream_id != authority.runtime.replay_input.stream_id
        or root.start_event_position != catalog.event_position
        or root.stop_event_position != stop
        or root.suffix_sha256 != captured_request.expected_suffix_sha256
        or root.start_state_sha256 != m7_cursor_sha256(checker_cursor)
        or root.baseline_catalog_action_id != fallback.descriptor.action_id
        or root.baseline_action_id != fallback.event.action.action_id
        or root.common_lemma_refs != common_refs
    ):
        raise _FullFactFailure("root_catalog_mismatch", root.fact_sha256)
    descriptors = tuple(
        item for item in catalog.actions if item.action_id == root.catalog_action_id
    )
    if len(descriptors) != 1:
        raise _FullFactFailure("root_catalog_mismatch", root.fact_sha256)
    initial = apply_m7_action_descriptor(
        authority.runtime,
        cursor=checker_cursor,
        catalog=catalog,
        descriptor=descriptors[0],
        decision_key=(f"m8_hypothetical_action_id={root.catalog_action_id}",),
    )
    if (
        initial.event.action.action_id != root.action_id
        or m7_cursor_sha256(initial.cursor) != root.initial_state_after_sha256
    ):
        raise _FullFactFailure("root_state_mismatch", root.fact_sha256)
    branch_cursor = initial.cursor
    for common_ref, influence_ref in zip(
        root.common_lemma_refs,
        root.influence_fact_refs,
        strict=True,
    ):
        _require_full_traversal_guard(traversal_guard)
        common_fact = common_fact_by_ref.get(common_ref)
        influence = influence_by_ref.get(influence_ref)
        if common_fact is None or influence is None or influence.common_lemma_ref != common_ref:
            raise _FullFactFailure("m8_dangling_reference", root.fact_sha256)
        if influence.root_action_id != root.action_id:
            raise _FullFactFailure("m8_action_binding_mismatch", influence.fact_sha256)
        branch_cursor = _validate_one_influence(
            authority,
            influence=influence,
            common_fact=common_fact,
            branch_cursor=branch_cursor,
            state=state,
            allow_exact_replay=captured_request.allow_exact_replay,
        )
        _require_full_traversal_guard(traversal_guard)
    if branch_cursor.next_event_position != stop or (
        m7_cursor_sha256(branch_cursor) != root.final_state_sha256
    ):
        raise _FullFactFailure("root_state_mismatch", root.fact_sha256)
    terminal = run_m7_continuation(
        authority.runtime,
        cursor=branch_cursor,
        stop_event_position=stop,
    )
    expected_cost_bits = facts.encode_canonical_f64(float(terminal.final_costs.net_cost))
    if terminal.events or root.final_net_cost_bits != expected_cost_bits:
        raise _FullFactFailure("root_terminal_mismatch", root.fact_sha256)
    _require_full_traversal_guard(traversal_guard)
    return root.catalog_action_id, facts.decode_canonical_f64(root.final_net_cost_bits)


def _capture_full_request(
    request: M8FactBundleCheckRequest,
) -> tuple[M8FactBundleCheckRequest, M7ReplayCursor]:
    try:
        request.require_valid()
    except (AttributeError, TypeError, ValueError) as error:
        raise _FullFactFailure("runtime_binding_mismatch") from error
    construction_claims = request._claim_snapshot  # noqa: SLF001
    (
        semantic_bundle_bytes,
        _construction_oracle_id,
        expected_semantic_runtime_sha256,
        expected_current_cursor_sha256,
        expected_catalog_event_position,
        expected_catalog_action_ids,
        expected_stop_event_position,
        expected_suffix_sha256,
        expected_freeze_id,
        expected_freeze_sha256,
        allow_exact_replay,
    ) = construction_claims
    captured_oracle_request = M8OracleRequest(
        runtime=request._runtime_reference,  # type: ignore[arg-type]  # noqa: SLF001
        cursor=request._cursor_reference,  # type: ignore[arg-type]  # noqa: SLF001
        visibility=request._visibility_reference,  # type: ignore[arg-type]  # noqa: SLF001
    )
    captured = M8FactBundleCheckRequest(
        semantic_bundle_bytes=semantic_bundle_bytes,  # type: ignore[arg-type]
        oracle_request=captured_oracle_request,
        expected_semantic_runtime_sha256=expected_semantic_runtime_sha256,  # type: ignore[arg-type]
        expected_current_cursor_sha256=expected_current_cursor_sha256,  # type: ignore[arg-type]
        expected_catalog_event_position=expected_catalog_event_position,  # type: ignore[arg-type]
        expected_catalog_action_ids=expected_catalog_action_ids,  # type: ignore[arg-type]
        expected_stop_event_position=expected_stop_event_position,  # type: ignore[arg-type]
        expected_suffix_sha256=expected_suffix_sha256,  # type: ignore[arg-type]
        expected_freeze_id=expected_freeze_id,  # type: ignore[arg-type]
        expected_freeze_sha256=expected_freeze_sha256,  # type: ignore[arg-type]
        allow_exact_replay=allow_exact_replay,  # type: ignore[arg-type]
    )
    _require_full_request_stable(request, captured)
    captured_claims = captured._claim_snapshot  # noqa: SLF001
    if (
        construction_claims[0:1] + construction_claims[2:]
        != captured_claims[0:1] + captured_claims[2:]
        or captured._runtime_reference is not request._runtime_reference  # noqa: SLF001
        or captured._cursor_reference is not request._cursor_reference  # noqa: SLF001
        or captured._visibility_reference is not request._visibility_reference  # noqa: SLF001
    ):
        raise _FullFactFailure("runtime_binding_mismatch")
    before = m7_cursor_sha256(captured.oracle_request.cursor)
    checker_cursor = deepcopy(captured.oracle_request.cursor)
    after = m7_cursor_sha256(captured.oracle_request.cursor)
    if (
        before != captured.expected_current_cursor_sha256
        or after != before
        or m7_cursor_sha256(checker_cursor) != before
    ):
        raise _FullFactFailure("runtime_binding_mismatch")
    return captured, checker_cursor


def _traverse_action_roots(
    authority: M7AuthoritativeProofRuntime,
    *,
    captured_request: M8FactBundleCheckRequest,
    traversal_guard: _M8FullTraversalGuard,
    bundle: facts.M8UncheckedFactBundleV2,
    catalog: M7ActionCatalog,
    fallback: M7StepResult,
    checker_cursor: M7ReplayCursor,
    stop: int,
    common_fact_by_ref: dict[str, M8CommonTransitionFact],
    state: _FullCheckState,
    first_fact: str | None,
) -> tuple[M8OracleDecision, int, int]:
    expected_catalog_ids = tuple(item.action_id for item in catalog.actions)
    root_by_catalog_id = {root.catalog_action_id: root for root in bundle.action_roots}
    if len(root_by_catalog_id) != len(bundle.action_roots) or set(root_by_catalog_id) != set(
        expected_catalog_ids
    ):
        owner = bundle.action_roots[0].fact_sha256 if bundle.action_roots else first_fact
        raise _FullFactFailure("root_catalog_mismatch", owner)
    common_refs = tuple(item.fact_sha256 for item in bundle.common_lemmas)
    influence_by_ref = {item.fact_sha256: item for item in bundle.influence_facts}
    scores = []
    checked_roots = 0
    for catalog_action_id in expected_catalog_ids:
        root = root_by_catalog_id[catalog_action_id]
        score = _validate_action_root(
            authority,
            captured_request=captured_request,
            traversal_guard=traversal_guard,
            root=root,
            catalog=catalog,
            fallback=fallback,
            checker_cursor=checker_cursor,
            stop=stop,
            common_refs=common_refs,
            common_fact_by_ref=common_fact_by_ref,
            influence_by_ref=influence_by_ref,
            state=state,
        )
        scores.append(score)
        checked_roots += 1
    checked_influences = len(state.checked_influence_sha256s)
    if checked_influences != len(bundle.influence_facts):
        missing = next(
            item.fact_sha256
            for item in bundle.influence_facts
            if item.fact_sha256 not in state.checked_influence_sha256s
        )
        raise _FullFactFailure("m8_unused_fact", missing)
    _require_full_traversal_guard(traversal_guard)
    decision = build_oracle_decision(
        baseline_action_id=fallback.descriptor.action_id,
        expected_action_ids=expected_catalog_ids,
        scores=tuple(scores),
    )
    _require_full_traversal_guard(traversal_guard)
    return decision, checked_roots, checked_influences


def check_m8_fact_bundle(
    request: M8FactBundleCheckRequest,
) -> M8CheckedFactBundleResult:
    """Validate the complete fixed-layer bundle before exposing one checked decision."""

    if type(request) is not M8FactBundleCheckRequest:
        return _full_result(valid=False, decision=None, code="invalid_request")
    checked_common = checked_influences = checked_roots = issued = 0
    first_fact: str | None = None
    state: _FullCheckState | None = None
    capabilities: list[ValidatedCommonTransition] = []
    try:
        captured, checker_cursor = _capture_full_request(request)
        try:
            with profile_phase("fact_bundle_strict_load"):
                bundle = _canonical_load(captured.semantic_bundle_bytes)
        except _CommonFactFailure as error:
            raise _FullFactFailure(error.code, error.fact_sha256) from error
        first_fact = bundle.common_lemmas[0].fact_sha256 if bundle.common_lemmas else None
        with (
            activate_m8_local_trusted_audit(),
            authoritative_m7_proof_runtime(captured.oracle_request.runtime) as authority,
        ):
            try:
                fallback, stop, _suffix = _validate_context(
                    captured,
                    authority,
                    bundle,
                    checker_cursor,
                )
            except _CommonFactFailure as error:
                raise _FullFactFailure(error.code, error.fact_sha256) from error
            catalog = enumerate_m7_action_catalog(authority.runtime, cursor=checker_cursor)
            if (
                catalog.event_position != captured.expected_catalog_event_position
                or tuple(item.action_id for item in catalog.actions)
                != captured.expected_catalog_action_ids
            ):
                raise _FullFactFailure("catalog_binding_mismatch", first_fact)
            expected_positions = tuple(range(fallback.cursor.next_event_position, stop))
            if tuple(item.event_position for item in bundle.common_lemmas) != expected_positions:
                raise _FullFactFailure("cursor_chain_mismatch", first_fact)
            common_state = _CommonCheckState(
                scalar_by_sha={item.fact_sha256: item for item in bundle.candidate_scalar_facts},
                frontier_by_sha={item.fact_sha256: item for item in bundle.frontier_facts},
                standard_by_sha={
                    item.fact_sha256: item for item in bundle.standard_candidate_facts
                },
                translation_by_sha={item.fact_sha256: item for item in bundle.translation_batches},
            )
            state = _FullCheckState(common=common_state)
            expected_jagua_sha256 = None
            if authority.runtime.jagua_executable is not None:
                expected_jagua_sha256 = (
                    "sha256:"
                    + hashlib.sha256(authority.runtime.jagua_executable.read_bytes()).hexdigest()
                )
            common_cursor = fallback.cursor
            checked_facts = []
            with profile_phase("fact_bundle_common_verification"):
                for lemma in bundle.common_lemmas:
                    try:
                        fact, _audits, _fallbacks, _seconds = _validate_one_common(
                            authority,
                            lemma=lemma,
                            cursor=common_cursor,
                            bundle=bundle,
                            state=common_state,
                            allow_exact_replay=captured.allow_exact_replay,
                            expected_jagua_sha256=expected_jagua_sha256,
                        )
                    except _CommonFactFailure as error:
                        raise _FullFactFailure(error.code, error.fact_sha256) from error
                    checked_facts.append(fact)
                    common_cursor = fact.step.cursor
                    checked_common += 1
            _require_full_request_stable(request, captured, authority, fact_sha256=first_fact)
            common_fact_by_ref: dict[str, M8CommonTransitionFact] = {}
            decision: M8OracleDecision | None = None
            try:
                with _checker_registration_scope(authority, tuple(checked_facts)) as token:
                    pending: BaseException | None = None
                    try:
                        for lemma, fact in zip(
                            bundle.common_lemmas,
                            checked_facts,
                            strict=True,
                        ):
                            capability = _register_checker_validated_common_transition(
                                fact,
                                authority,
                                checker_token=token,
                            )
                            capabilities.append(capability)
                            bound_fact = _validated_common_transition_fact(
                                authority.runtime,
                                capability,
                            )
                            if bound_fact != fact:
                                raise ValueError(
                                    "M8 registered common capability differs from checked fact"
                                )
                            common_fact_by_ref[lemma.fact_sha256] = bound_fact
                            issued += 1
                    except BaseException as error:
                        pending = _FullFactFailure(
                            "capability_registration_failure",
                            first_fact,
                        )
                        pending.__cause__ = error
                    if pending is None:
                        try:
                            with _full_traversal_guard_scope(
                                request,
                                captured,
                                authority,
                                token,
                                tuple(capabilities),
                            ) as traversal_guard:
                                with profile_phase("fact_bundle_action_traversal"):
                                    decision, checked_roots, checked_influences = (
                                        _traverse_action_roots(
                                            authority,
                                            captured_request=captured,
                                            traversal_guard=traversal_guard,
                                            bundle=bundle,
                                            catalog=catalog,
                                            fallback=fallback,
                                            checker_cursor=checker_cursor,
                                            stop=stop,
                                            common_fact_by_ref=common_fact_by_ref,
                                            state=state,
                                            first_fact=first_fact,
                                        )
                                    )
                        except _FullFactFailure as error:
                            pending = error
                        except BaseException as error:
                            pending = _FullFactFailure(
                                "internal_checker_failure",
                                first_fact,
                            )
                            pending.__cause__ = error
                    with profile_phase("fact_bundle_cleanup"):
                        try:
                            _drain_common_capabilities(capabilities)
                        except BaseException as error:
                            pending = _FullFactFailure(
                                "capability_registration_failure",
                                first_fact,
                            )
                            pending.__cause__ = error
                    if pending is not None:
                        raise pending
            except BaseException as error:
                if isinstance(error, _FullFactFailure):
                    raise error
                raise _FullFactFailure("capability_registration_failure", first_fact) from error
            _require_full_request_stable(
                request,
                captured,
                authority,
                fact_sha256=first_fact,
            )
            if decision is None:
                raise _FullFactFailure("internal_checker_failure", first_fact)
        return _full_result(
            valid=True,
            decision=decision,
            code="valid_action_decision",
            state=state,
            checked_common=checked_common,
            checked_influences=checked_influences,
            checked_roots=checked_roots,
            issued=issued,
        )
    except _FullFactFailure as error:
        checked_influences = (
            len(state.checked_influence_sha256s) if state is not None else checked_influences
        )
        return _full_result(
            valid=False,
            decision=None,
            code=error.code,
            state=state,
            checked_common=checked_common,
            checked_influences=checked_influences,
            checked_roots=checked_roots,
            issued=issued,
            fact_sha256=error.fact_sha256 or first_fact,
        )
    except (AttributeError, KeyError, RuntimeError, StopIteration, TypeError, ValueError):
        checked_influences = (
            len(state.checked_influence_sha256s) if state is not None else checked_influences
        )
        return _full_result(
            valid=False,
            decision=None,
            code="internal_checker_failure",
            state=state,
            checked_common=checked_common,
            checked_influences=checked_influences,
            checked_roots=checked_roots,
            issued=issued,
            fact_sha256=first_fact,
        )
    finally:
        if capabilities:
            try:
                _drain_common_capabilities(capabilities)
            except BaseException:
                pass


def check_m8_common_fact_bundle(
    request: M8CommonFactCheckRequest,
) -> M8CommonFactCheckResult:
    """Check every common lemma and retire its authority before returning."""

    if type(request) is not M8CommonFactCheckRequest:
        return _fail("invalid_request")
    checked = audits = issued = fallbacks = 0
    fallback_seconds = 0.0
    first_fact: str | None = None
    state: _CommonCheckState | None = None
    try:
        try:
            request.require_valid()
        except (AttributeError, TypeError, ValueError) as error:
            raise _CommonFactFailure("runtime_binding_mismatch") from error
        construction_claims = request._claim_snapshot  # noqa: SLF001
        (
            semantic_bundle_bytes,
            _construction_oracle_id,
            expected_semantic_runtime_sha256,
            expected_current_cursor_sha256,
            expected_catalog_event_position,
            expected_catalog_action_ids,
            expected_stop_event_position,
            expected_suffix_sha256,
            expected_freeze_id,
            expected_freeze_sha256,
            allow_exact_replay,
        ) = construction_claims
        captured_oracle_request = M8OracleRequest(
            runtime=request._runtime_reference,  # type: ignore[arg-type]  # noqa: SLF001
            cursor=request._cursor_reference,  # type: ignore[arg-type]  # noqa: SLF001
            visibility=request._visibility_reference,  # type: ignore[arg-type]  # noqa: SLF001
        )
        captured_request = M8CommonFactCheckRequest(
            semantic_bundle_bytes=semantic_bundle_bytes,  # type: ignore[arg-type]
            oracle_request=captured_oracle_request,
            expected_semantic_runtime_sha256=expected_semantic_runtime_sha256,  # type: ignore[arg-type]
            expected_current_cursor_sha256=expected_current_cursor_sha256,  # type: ignore[arg-type]
            expected_catalog_event_position=expected_catalog_event_position,  # type: ignore[arg-type]
            expected_catalog_action_ids=expected_catalog_action_ids,  # type: ignore[arg-type]
            expected_stop_event_position=expected_stop_event_position,  # type: ignore[arg-type]
            expected_suffix_sha256=expected_suffix_sha256,  # type: ignore[arg-type]
            expected_freeze_id=expected_freeze_id,  # type: ignore[arg-type]
            expected_freeze_sha256=expected_freeze_sha256,  # type: ignore[arg-type]
            allow_exact_replay=allow_exact_replay,  # type: ignore[arg-type]
        )
        request.require_valid()
        captured_claims = captured_request._claim_snapshot  # noqa: SLF001
        if (
            construction_claims[0:1] + construction_claims[2:]
            != captured_claims[0:1] + captured_claims[2:]
            or captured_request._runtime_reference  # noqa: SLF001
            is not request._runtime_reference  # noqa: SLF001
            or captured_request._cursor_reference  # noqa: SLF001
            is not request._cursor_reference  # noqa: SLF001
            or captured_request._visibility_reference  # noqa: SLF001
            is not request._visibility_reference  # noqa: SLF001
        ):
            raise _CommonFactFailure("runtime_binding_mismatch")
        cursor_before_sha256 = m7_cursor_sha256(captured_request.oracle_request.cursor)
        checker_cursor = deepcopy(captured_request.oracle_request.cursor)
        cursor_after_sha256 = m7_cursor_sha256(captured_request.oracle_request.cursor)
        if (
            cursor_before_sha256 != captured_request.expected_current_cursor_sha256
            or m7_cursor_sha256(checker_cursor) != cursor_before_sha256
            or cursor_after_sha256 != cursor_before_sha256
        ):
            raise _CommonFactFailure("runtime_binding_mismatch")
        bundle = _canonical_load(captured_request.semantic_bundle_bytes)
        first_fact = bundle.common_lemmas[0].fact_sha256 if bundle.common_lemmas else None
        with (
            activate_m8_local_trusted_audit(),
            authoritative_m7_proof_runtime(captured_request.oracle_request.runtime) as authority,
        ):
            fallback, stop, _suffix = _validate_context(
                captured_request,
                authority,
                bundle,
                checker_cursor,
            )
            expected_jagua_sha256 = None
            if authority.runtime.jagua_executable is not None:
                expected_jagua_sha256 = (
                    "sha256:"
                    + hashlib.sha256(authority.runtime.jagua_executable.read_bytes()).hexdigest()
                )
            expected_positions = tuple(range(fallback.cursor.next_event_position, stop))
            if tuple(item.event_position for item in bundle.common_lemmas) != expected_positions:
                raise _CommonFactFailure("cursor_chain_mismatch", first_fact)
            cursor = fallback.cursor
            state = _CommonCheckState(
                scalar_by_sha={item.fact_sha256: item for item in bundle.candidate_scalar_facts},
                frontier_by_sha={item.fact_sha256: item for item in bundle.frontier_facts},
                standard_by_sha={
                    item.fact_sha256: item for item in bundle.standard_candidate_facts
                },
                translation_by_sha={item.fact_sha256: item for item in bundle.translation_batches},
            )
            checked_facts: list[M8CommonTransitionFact] = []
            for lemma in bundle.common_lemmas:
                fact, local_audits, local_fallbacks, local_seconds = _validate_one_common(
                    authority,
                    lemma=lemma,
                    cursor=cursor,
                    bundle=bundle,
                    state=state,
                    allow_exact_replay=captured_request.allow_exact_replay,
                    expected_jagua_sha256=expected_jagua_sha256,
                )
                checked_facts.append(fact)
                cursor = fact.step.cursor
                checked += 1
                audits += local_audits
                fallbacks += local_fallbacks
                fallback_seconds += local_seconds
            try:
                captured_request.require_valid()
                request.require_valid()
            except (AttributeError, TypeError, ValueError) as error:
                raise _CommonFactFailure("runtime_binding_mismatch", first_fact) from error
            capabilities: list[ValidatedCommonTransition] = []
            try:
                with _checker_registration_scope(authority, tuple(checked_facts)) as token:
                    for fact in checked_facts:
                        capabilities.append(
                            _register_checker_validated_common_transition(
                                fact,
                                authority,
                                checker_token=token,
                            )
                        )
                        issued += 1
                    _drain_common_capabilities(capabilities)
            except BaseException as error:
                try:
                    _drain_common_capabilities(capabilities)
                except BaseException:
                    pass
                raise _CommonFactFailure(
                    "capability_registration_failure",
                    first_fact,
                ) from error
            try:
                captured_request.require_valid()
                request.require_valid()
            except (AttributeError, TypeError, ValueError) as error:
                raise _CommonFactFailure("runtime_binding_mismatch", first_fact) from error
        return M8CommonFactCheckResult(
            valid=True,
            checked_common_lemma_count=checked,
            counted_translation_audit_count=audits,
            issued_common_capability_count=issued,
            exact_replay_fallback_count=fallbacks,
            exact_replay_fallback_wall_seconds=float(fallback_seconds),
            failure_code="valid_common_facts",
            first_failing_fact_sha256=None,
        )
    except _CommonFactFailure as error:
        if state is None:
            fallbacks += error.fallback_count
            fallback_seconds += error.fallback_seconds
        else:
            audits = len(state.audited_counted_lemma_sha256s)
            fallbacks = state.exact_replay_fallback_count
            fallback_seconds = state.exact_replay_fallback_wall_seconds
        return _fail(
            error.code,
            checked=checked,
            audits=audits,
            issued=issued,
            fallbacks=fallbacks,
            fallback_seconds=fallback_seconds,
            fact_sha256=error.fact_sha256 or first_fact,
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        if state is not None:
            audits = len(state.audited_counted_lemma_sha256s)
            fallbacks = state.exact_replay_fallback_count
            fallback_seconds = state.exact_replay_fallback_wall_seconds
        return _fail(
            "internal_checker_failure",
            checked=checked,
            audits=audits,
            issued=issued,
            fallbacks=fallbacks,
            fallback_seconds=fallback_seconds,
            fact_sha256=first_fact,
        )


__all__ = [
    "M8CheckedFactBundleFailureCode",
    "M8CheckedFactBundleResult",
    "M8CommonFactCheckRequest",
    "M8CommonFactCheckResult",
    "M8CommonFactFailureCode",
    "M8FactBundleCheckRequest",
    "check_m8_fact_bundle",
    "check_m8_common_fact_bundle",
]
