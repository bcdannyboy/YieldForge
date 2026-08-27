"""Independent common-lemma checker for portable M8 fact bundles.

The bundle remains an unchecked transport object until this module binds it to an independently
supplied calibration runtime, reconstructs every common transition, and retires every local
capability.  Action-root and influence authorization intentionally belongs to the next layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import weakref
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
    prepare_layout_footprint,
    prepare_remnant_geometry,
    prepare_translation_rejection_remnant,
)
from yieldforge.baseline.jagua import JaguaRepresentationError, run_jagua_generated_prefilter
from yieldforge.baseline.policies import rank_policy_action
from yieldforge.baseline.replay import (
    GeneratedActionSet,
    M7ActionCatalog,
    M7AuthoritativeProofRuntime,
    M7ReplayCursor,
    M7StepResult,
    apply_m7_action_descriptor,
    authoritative_m7_proof_runtime,
    enumerate_m7_action_catalog,
    enumerate_m7_standard_only_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    select_m7_fallback,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import facts
from yieldforge.oracle.certificates import (
    M8CommonTransitionFact,
    ValidatedCommonTransition,
    _common_fact_payload,
    _portable_common_transition,
    _portable_search_config,
    _register_checker_validated_common_transition,
    _release_validated_common_transition,
)
from yieldforge.oracle.compiled import compile_rejection_problem
from yieldforge.oracle.concurrency import (
    activate_m8_local_trusted_audit,
    require_m8_translation_audit_processes,
)
from yieldforge.oracle.frontier import certify_frontier_impossible
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
    authority.require_active(authority.runtime)


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
    rejection_layouts_supported = bool(verified.rejection_layouts) and tuple(
        item.candidate_id for item in verified.rejection_layouts
    ) == tuple(item.candidate_id for item in verified.candidates)
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
                audit_layout_translation_batch(
                    remnant=prepared_remnant,
                    layouts=layouts,
                    expected=expected_batches,
                    fit_config=runtime.replay_input.fit_config,
                    search_config=runtime.replay_input.search_config,
                    process_count=require_m8_translation_audit_processes(),
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
    "M8CommonFactCheckRequest",
    "M8CommonFactCheckResult",
    "M8CommonFactFailureCode",
    "check_m8_common_fact_bundle",
]
