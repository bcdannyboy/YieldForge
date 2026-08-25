"""Exact no-fit and policy-dominance certificates for one M8 future event."""

from __future__ import annotations

import os
import weakref
from collections import OrderedDict
from collections.abc import Callable
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass, field

from yieldforge.baseline.contracts import LayoutFitSearchResult
from yieldforge.baseline.policies import ActionPolicyContext, PolicyRank, rank_policy_action
from yieldforge.baseline.replay import (
    M7ActionDescriptor,
    M7AuthoritativeProofRuntime,
    M7PolicyActionBinding,
    M7ReplayCursor,
    M7ReplayEvent,
    M7ReplayRuntime,
    M7SemanticRuntimeSnapshot,
    M7StepResult,
    apply_m7_action_descriptor,
    apply_m7_frozen_action_evidence,
    enumerate_m7_action_catalog,
    enumerate_m7_single_remnant_competitor,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    m7_shared_fit_search_cache_key,
    select_m7_fallback,
    snapshot_m7_replay_runtime,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.compiled import (
    CompiledTranslationRejection,
    _compile_prepared_translation_rejections,
    _PreparedTranslationLayoutBatch,
    compile_translation_rejections,
)
from yieldforge.oracle.proofs import M8EventWitness, M8InfluenceWitness
from yieldforge.replay.contracts import InventoryItem


def _item_ids(items: tuple[InventoryItem, ...]) -> tuple[str, ...]:
    return tuple(item.remnant.remnant_id for item in items)


@dataclass(frozen=True)
class BranchInventoryDelta:
    """Exact branch inventory differences relative to one common M7 cursor."""

    added: tuple[InventoryItem, ...]
    removed: tuple[InventoryItem, ...]

    def __post_init__(self) -> None:
        added_ids = _item_ids(self.added)
        removed_ids = _item_ids(self.removed)
        if added_ids != tuple(sorted(set(added_ids))):
            raise ValueError("M8 added remnant identities must be sorted unique")
        if removed_ids != tuple(sorted(set(removed_ids))):
            raise ValueError("M8 removed remnant identities must be sorted unique")
        if set(added_ids) & set(removed_ids):
            raise ValueError("M8 added and removed remnant identities must be disjoint")


@dataclass(frozen=True)
class EventPassivityResult:
    passive: bool
    witness: M8EventWitness | None
    branch_after: M7ReplayCursor | None
    exact_search_count: int
    _branch_after_sha256: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _provenance_token: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.exact_search_count < 0:
            raise ValueError("M8 exact search count cannot be negative")
        if self.passive != (self.witness is not None and self.branch_after is not None):
            raise ValueError(
                "M8 passive result must carry exactly one event witness and resulting cursor"
            )
        if not self.passive and (self.witness is not None or self.branch_after is not None):
            raise ValueError("M8 nonpassive result cannot carry event evidence")
        if self.passive and (
            self._provenance_token is not _EVENT_PASSIVITY_PROVENANCE
            or self._branch_after_sha256 != self.witness.state_after_sha256
        ):
            raise ValueError("M8 passive result lacks trusted resulting-cursor provenance")
        if not self.passive and (
            self._branch_after_sha256 is not None or self._provenance_token is not None
        ):
            raise ValueError("M8 nonpassive result cannot carry resulting-cursor provenance")


_EVENT_PASSIVITY_PROVENANCE = object()


def _trusted_passive_result(
    *,
    witness: M8EventWitness,
    branch_after: M7ReplayCursor,
    exact_search_count: int,
    branch_after_sha256: str,
) -> EventPassivityResult:
    result = object.__new__(EventPassivityResult)
    object.__setattr__(result, "passive", True)
    object.__setattr__(result, "witness", witness)
    object.__setattr__(result, "branch_after", branch_after)
    object.__setattr__(result, "exact_search_count", exact_search_count)
    object.__setattr__(result, "_branch_after_sha256", branch_after_sha256)
    object.__setattr__(result, "_provenance_token", _EVENT_PASSIVITY_PROVENANCE)
    result.__post_init__()
    return result


@dataclass(frozen=True)
class M8CommonTransitionFact:
    """Content-addressed exact M7 winner and transition for one common event."""

    replay_input_id: str
    replay_input_sha256: str
    event_position: int
    cursor_before: M7ReplayCursor
    cursor_before_sha256: str
    step: M7StepResult
    cursor_after_sha256: str
    event_id: str
    policy_rank: PolicyRank
    semantic_runtime_sha256: str
    content_sha256: str


@dataclass(frozen=True, slots=True, weakref_slot=True, init=False)
class ValidatedCommonTransition:
    """Opaque process-local identity for one privately registered common fact."""

    _binding_token: object = field(repr=False, compare=False)
    _provenance_token: object = field(repr=False, compare=False)

    def __init__(
        self,
        fact: M8CommonTransitionFact | None = None,
        _provenance_token: object | None = None,
        _binding_token: object | None = None,
    ) -> None:
        # ``fact`` remains an accepted constructor argument so reconstructed
        # lookalikes fail at the registry boundary instead of at construction.
        del fact
        object.__setattr__(self, "_binding_token", _binding_token or object())
        object.__setattr__(self, "_provenance_token", _provenance_token)

    @property
    def fact(self) -> M8CommonTransitionFact:
        """Return an untrusted copy for portable serialization and inspection."""

        return deepcopy(_registered_common_entry(self).canonical_fact)

    @property
    def step(self) -> M7StepResult:
        return self.fact.step

    @property
    def cursor_before(self) -> M7ReplayCursor:
        return self.fact.cursor_before

    @property
    def event_position(self) -> int:
        return self.fact.event_position

    @property
    def policy_rank(self) -> PolicyRank:
        return self.fact.policy_rank

    @property
    def semantic_runtime_sha256(self) -> str:
        return self.fact.semantic_runtime_sha256

    @property
    def content_sha256(self) -> str:
        return self.fact.content_sha256

    def __reduce__(self) -> object:
        raise TypeError("validated M8 common transition capabilities cannot be serialized")


_VALIDATED_COMMON_PROVENANCE = object()


@dataclass
class _ValidatedCommonEntry:
    reference: weakref.ReferenceType[ValidatedCommonTransition]
    binding_token: object
    owner_pid: int
    snapshot: M7SemanticRuntimeSnapshot
    authority: M7AuthoritativeProofRuntime | None
    owns_snapshot: bool
    canonical_fact: M8CommonTransitionFact
    integrity_sha256: str


_VALIDATED_COMMON_REGISTRY: dict[
    int,
    _ValidatedCommonEntry,
] = {}


def _rank_payload(rank: PolicyRank) -> dict[str, object]:
    return {
        "policy": rank.policy.value,
        "comparison_key": rank.comparison_key,
        "decision_key": rank.decision_key,
    }


def _context_payload(context: ActionPolicyContext) -> dict[str, object]:
    return {
        "action_id": context.action_id,
        "kind": context.kind.value,
        "candidate_id": context.candidate_id,
        "candidate_width": context.candidate_width,
        "selected_stock_id": context.selected_stock_id,
        "immediate_net_cost": context.immediate_net_cost,
        "selected_remnant_age_hours": context.selected_remnant_age_hours,
        "returned_regularity": context.returned_regularity,
        "known_order_lookahead_term": context.known_order_lookahead_term,
    }


def _common_fact_payload(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    cursor_before: M7ReplayCursor,
    step: M7StepResult,
    policy_rank: PolicyRank,
    semantic_runtime_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": "yieldforge.m8-common-transition-fact.v1",
        "replay_input_id": runtime.replay_input.input_id,
        "replay_input_sha256": runtime.replay_input.content_sha256,
        "semantic_runtime_sha256": semantic_runtime_sha256,
        "event_position": event_position,
        "cursor_before_sha256": m7_cursor_sha256(cursor_before),
        "descriptor": {
            "action_id": step.descriptor.action_id,
            "kind": step.descriptor.kind.value,
            "candidate_id": step.descriptor.candidate_id,
            "selected_remnant_id": step.descriptor.selected_remnant_id,
            "evidence": (
                step.descriptor.evidence.model_dump(mode="json")
                if step.descriptor.evidence is not None
                else None
            ),
        },
        "selected_context": _context_payload(step.selected_context),
        "action_binding": {
            "catalog_action_id": step.action_binding.catalog_action_id,
            "materialized_action_id": step.action_binding.materialized_action_id,
            "context": _context_payload(step.action_binding.context),
        },
        "event": step.event.model_dump(mode="json"),
        "cursor_after_sha256": m7_cursor_sha256(step.cursor),
        "event_id": step.event.event_id,
        "policy_rank": _rank_payload(policy_rank),
    }


def _derive_m8_common_transition_fact(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    semantic_runtime_sha256: str,
) -> M8CommonTransitionFact:
    event_position = cursor.next_event_position
    for item in cursor.inventory:
        compile_translation_rejections(
            runtime,
            event_position=event_position,
            item=item,
        )
    authoritative_runtime = _fresh_runtime(runtime)
    catalog = enumerate_m7_action_catalog(
        authoritative_runtime,
        cursor=cursor,
        complete=False,
    )
    _require_common_search_caches_match_authoritative(
        runtime,
        authoritative_runtime=authoritative_runtime,
        event_position=event_position,
        inventory=cursor.inventory,
    )
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(
        item for item in catalog.actions if item.action_id == selected.action_id
    )
    step = apply_m7_action_descriptor(
        authoritative_runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selected.decision_key,
    )
    rank = rank_policy_action(runtime.replay_input.policy.name, step.selected_context)
    payload = _common_fact_payload(
        runtime,
        event_position=event_position,
        cursor_before=cursor,
        step=step,
        policy_rank=rank,
        semantic_runtime_sha256=semantic_runtime_sha256,
    )
    content_sha256 = f"sha256:{semantic_sha256(payload)}"
    return M8CommonTransitionFact(
        replay_input_id=runtime.replay_input.input_id,
        replay_input_sha256=runtime.replay_input.content_sha256,
        event_position=event_position,
        cursor_before=cursor,
        cursor_before_sha256=m7_cursor_sha256(cursor),
        step=step,
        cursor_after_sha256=m7_cursor_sha256(step.cursor),
        event_id=step.event.event_id,
        policy_rank=rank,
        semantic_runtime_sha256=semantic_runtime_sha256,
        content_sha256=content_sha256,
    )


def build_m8_common_transition_fact(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
) -> M8CommonTransitionFact:
    """Build one deterministic portable fact; callers must validate it before use."""

    snapshot = snapshot_m7_replay_runtime(runtime)
    try:
        with snapshot.runtime_for_proof() as proof_runtime:
            fact = _derive_m8_common_transition_fact(
                proof_runtime,
                cursor=cursor,
                semantic_runtime_sha256=snapshot.semantic_sha256,
            )
            _require_caller_runtime_stable(
                runtime,
                expected_sha256=snapshot.semantic_sha256,
                operation="M8 common fact derivation",
            )
        return fact
    finally:
        snapshot.close()


def _validate_portable_common_transition_fact(
    runtime: M7ReplayRuntime,
    fact: M8CommonTransitionFact,
    *,
    semantic_runtime_sha256: str,
) -> None:
    if not isinstance(fact, M8CommonTransitionFact):
        raise ValueError("M8 common transition fact has the wrong runtime type")
    if fact.semantic_runtime_sha256 != semantic_runtime_sha256:
        raise ValueError("M8 common fact semantic runtime fingerprint differs")
    if (
        fact.replay_input_id != runtime.replay_input.input_id
        or fact.replay_input_sha256 != runtime.replay_input.content_sha256
        or type(fact.event_position) is not int
        or fact.event_position < 0
        or fact.event_position >= len(runtime.replay_input.instances)
        or fact.event_position != fact.cursor_before.next_event_position
    ):
        raise ValueError("M8 common transition fact differs from the frozen replay input")
    before_sha256 = m7_cursor_sha256(fact.cursor_before)
    after_sha256 = m7_cursor_sha256(fact.step.cursor)
    if (
        fact.cursor_before_sha256 != before_sha256
        or fact.cursor_after_sha256 != after_sha256
        or fact.event_id != fact.step.event.event_id
    ):
        raise ValueError("M8 common transition fact cursor or event binding differs")
    try:
        canonical_event = M7ReplayEvent.model_validate(
            fact.step.event.model_dump(mode="python"),
            strict=True,
        )
    except (AttributeError, ValueError) as error:
        raise ValueError("M8 common replay event is not canonical") from error
    if canonical_event != fact.step.event:
        raise ValueError("M8 common replay event differs from canonical persisted evidence")
    binding = runtime.replay_input.instances[fact.event_position]
    step = fact.step
    expected_stock_id = (
        "current_standard_sheet"
        if step.event.action.selected_remnant_id is None
        else step.event.action.selected_remnant_id
    )
    if (
        step.action_binding.catalog_action_id != step.descriptor.action_id
        or step.action_binding.materialized_action_id != step.event.action.action_id
        or step.action_binding.context != step.selected_context
        or step.descriptor.kind is not step.event.action.kind
        or step.descriptor.candidate_id != step.event.action.candidate_id
        or step.descriptor.selected_remnant_id != step.event.action.selected_remnant_id
        or step.selected_context.kind is not step.event.action.kind
        or step.selected_context.candidate_id != step.event.action.candidate_id
        or step.selected_context.selected_stock_id != expected_stock_id
        or step.event.sequence != fact.event_position
        or step.event.binding_id != binding.binding_id
        or step.event.occurred_at != binding.released_at
        or step.event.storage_interval_start != fact.cursor_before.current_time
        or step.event.inventory_before != fact.cursor_before.inventory
        or step.event.inventory_after != step.cursor.inventory
        or step.event.cumulative_costs != step.cursor.cumulative_costs
        or step.event.timestamp_group_sequence != step.cursor.timestamp_group_sequence
        or step.event.timestamp_subsequence != step.cursor.timestamp_subsequence
        or step.cursor.next_event_position != fact.event_position + 1
        or step.cursor.current_time != binding.released_at
        or step.cursor.previous_release != binding.released_at
    ):
        raise ValueError("M8 common transition fact has inconsistent exact transition fields")
    expected_rank = rank_policy_action(
        runtime.replay_input.policy.name,
        step.selected_context,
    )
    if (
        fact.policy_rank != expected_rank
        or step.event.policy_decision_key != expected_rank.decision_key
    ):
        raise ValueError("M8 common transition fact has inconsistent exact policy rank")
    payload = _common_fact_payload(
        runtime,
        event_position=fact.event_position,
        cursor_before=fact.cursor_before,
        step=step,
        policy_rank=fact.policy_rank,
        semantic_runtime_sha256=semantic_runtime_sha256,
    )
    expected_content = f"sha256:{semantic_sha256(payload)}"
    if fact.content_sha256 != expected_content:
        raise ValueError("M8 common transition fact content hash differs")


def _common_registry_integrity_sha256(
    runtime: M7ReplayRuntime,
    fact: M8CommonTransitionFact,
) -> str:
    """Commit every registry-owned fact field for one exit-boundary deep check."""

    payload = {
        "schema_version": "yieldforge.m8-common-transition-registry.v1",
        "portable_fields": {
            "replay_input_id": fact.replay_input_id,
            "replay_input_sha256": fact.replay_input_sha256,
            "event_position": fact.event_position,
            "cursor_before_sha256": fact.cursor_before_sha256,
            "cursor_after_sha256": fact.cursor_after_sha256,
            "event_id": fact.event_id,
            "semantic_runtime_sha256": fact.semantic_runtime_sha256,
            "content_sha256": fact.content_sha256,
        },
        "semantic_fact": _common_fact_payload(
            runtime,
            event_position=fact.event_position,
            cursor_before=fact.cursor_before,
            step=fact.step,
            policy_rank=fact.policy_rank,
            semantic_runtime_sha256=fact.semantic_runtime_sha256,
        ),
    }
    return f"sha256:{semantic_sha256(payload)}"


def _registered_common_entry(
    common: ValidatedCommonTransition,
) -> _ValidatedCommonEntry:
    if type(common) is not ValidatedCommonTransition:
        raise ValueError("M8 certifier requires a validated common transition capability")
    registered = _VALIDATED_COMMON_REGISTRY.get(id(common))
    if (
        common._provenance_token is not _VALIDATED_COMMON_PROVENANCE
        or registered is None
        or registered.reference() is not common
        or registered.binding_token is not common._binding_token
        or registered.owner_pid != os.getpid()
        or registered.snapshot._owner_pid != registered.owner_pid  # noqa: SLF001
        or registered.snapshot.semantic_sha256
        != registered.canonical_fact.semantic_runtime_sha256
    ):
        raise ValueError("M8 certifier requires a validated common transition capability")
    if registered.authority is not None:
        registered.authority._require_active_identity()  # noqa: SLF001
    return registered


def _register_validated_common_transition(
    fact: M8CommonTransitionFact,
    snapshot: M7SemanticRuntimeSnapshot,
    *,
    authority: M7AuthoritativeProofRuntime | None = None,
    owns_snapshot: bool = True,
) -> ValidatedCommonTransition:
    canonical_fact = deepcopy(fact)
    binding_token = object()
    validated = ValidatedCommonTransition(
        _provenance_token=_VALIDATED_COMMON_PROVENANCE,
        _binding_token=binding_token,
    )
    key = id(validated)

    def discard(reference: weakref.ReferenceType[ValidatedCommonTransition]) -> None:
        registered = _VALIDATED_COMMON_REGISTRY.get(key)
        if registered is not None and registered.reference is reference:
            if os.getpid() != registered.owner_pid:
                return
            _VALIDATED_COMMON_REGISTRY.pop(key, None)
            if registered.owns_snapshot:
                registered.snapshot.close()

    reference = weakref.ref(validated, discard)
    _VALIDATED_COMMON_REGISTRY[key] = _ValidatedCommonEntry(
        reference=reference,
        binding_token=binding_token,
        owner_pid=os.getpid(),
        snapshot=snapshot,
        authority=authority,
        owns_snapshot=owns_snapshot,
        canonical_fact=canonical_fact,
        integrity_sha256=_common_registry_integrity_sha256(
            snapshot.runtime,
            canonical_fact,
        ),
    )
    return validated


def _release_validated_common_transition(common: ValidatedCommonTransition) -> None:
    """Deep-check and retire one event-scoped private common fact."""

    registered = _VALIDATED_COMMON_REGISTRY.get(id(common))
    integrity_error = None
    try:
        if registered is None or _registered_common_entry(common) is not registered:
            raise ValueError("M8 certifier requires a validated common transition capability")
        if registered.integrity_sha256 != _common_registry_integrity_sha256(
            registered.snapshot.runtime,
            registered.canonical_fact,
        ):
            integrity_error = ValueError("M8 common transition registry integrity differs")
    except (AttributeError, TypeError, ValueError) as error:
        integrity_error = ValueError("M8 common transition registry integrity differs")
        integrity_error.__cause__ = error
    finally:
        current = _VALIDATED_COMMON_REGISTRY.get(id(common))
        if registered is not None and current is registered:
            _VALIDATED_COMMON_REGISTRY.pop(id(common), None)
        if registered is not None and registered.owns_snapshot:
            registered.snapshot.close()
    if integrity_error is not None:
        raise integrity_error


def build_validated_m8_common_transition(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
) -> ValidatedCommonTransition:
    """Reconstruct one exact local common transition and issue a process capability."""

    snapshot = snapshot_m7_replay_runtime(runtime)
    registered = False
    try:
        with snapshot.runtime_for_proof() as proof_runtime:
            fact = _derive_m8_common_transition_fact(
                proof_runtime,
                cursor=cursor,
                semantic_runtime_sha256=snapshot.semantic_sha256,
            )
            _validate_portable_common_transition_fact(
                proof_runtime,
                fact,
                semantic_runtime_sha256=snapshot.semantic_sha256,
            )
            _require_caller_runtime_stable(
                runtime,
                expected_sha256=snapshot.semantic_sha256,
                operation="M8 common capability derivation",
            )
        result = _register_validated_common_transition(fact, snapshot)
        registered = True
        return result
    finally:
        if not registered:
            snapshot.close()


def build_validated_m8_common_transition_in_context(
    authority: M7AuthoritativeProofRuntime,
    *,
    cursor: M7ReplayCursor,
) -> ValidatedCommonTransition:
    """Derive one common capability inside an active shared proof runtime."""

    authority._require_active_identity()  # noqa: SLF001 - bounded prepared operation.
    fact = _derive_m8_common_transition_fact(
        authority.runtime,
        cursor=cursor,
        semantic_runtime_sha256=authority.semantic_sha256,
    )
    _validate_portable_common_transition_fact(
        authority.runtime,
        fact,
        semantic_runtime_sha256=authority.semantic_sha256,
    )
    authority._require_active_identity()  # noqa: SLF001 - bounded prepared operation.
    return _register_validated_common_transition(
        fact,
        authority._snapshot,  # noqa: SLF001 - capability shares the authority lifetime.
        authority=authority,
        owns_snapshot=False,
    )


def validate_m8_common_transition_fact(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    fact: M8CommonTransitionFact,
) -> ValidatedCommonTransition:
    """Independently reconstruct a portable fact before granting local authority."""

    snapshot = snapshot_m7_replay_runtime(runtime)
    registered = False
    try:
        with snapshot.runtime_for_proof() as proof_runtime:
            _validate_portable_common_transition_fact(
                proof_runtime,
                fact,
                semantic_runtime_sha256=snapshot.semantic_sha256,
            )
            authoritative = _derive_m8_common_transition_fact(
                proof_runtime,
                cursor=cursor,
                semantic_runtime_sha256=snapshot.semantic_sha256,
            )
            if fact != authoritative:
                raise ValueError("M8 portable fact differs from authoritative common transition")
            _require_caller_runtime_stable(
                runtime,
                expected_sha256=snapshot.semantic_sha256,
                operation="M8 common capability validation",
            )
        result = _register_validated_common_transition(fact, snapshot)
        registered = True
        return result
    finally:
        if not registered:
            snapshot.close()


def _require_caller_runtime_stable(
    runtime: M7ReplayRuntime,
    *,
    expected_sha256: str,
    operation: str,
) -> None:
    if m7_semantic_runtime_sha256(runtime) != expected_sha256:
        raise ValueError(f"M8 semantic runtime fingerprint changed during {operation}")


def _require_validated_common_transition(
    runtime: M7ReplayRuntime,
    common: ValidatedCommonTransition,
) -> tuple[
    M8CommonTransitionFact,
    M7SemanticRuntimeSnapshot,
    M7AuthoritativeProofRuntime | None,
]:
    registered = _registered_common_entry(common)
    fact = registered.canonical_fact
    authority = registered.authority
    if authority is None:
        _require_caller_runtime_stable(
            runtime,
            expected_sha256=fact.semantic_runtime_sha256,
            operation="M8 certificate capability entry",
        )
    else:
        authority._require_active_identity(runtime)  # noqa: SLF001
    return fact, registered.snapshot, authority


def _validated_common_transition_fact(
    runtime: M7ReplayRuntime,
    common: ValidatedCommonTransition,
) -> M8CommonTransitionFact:
    """Retrieve only the private canonical fact after O(1) capability checks."""

    return _require_validated_common_transition(runtime, common)[0]


def _derive_branch_inventory_delta(
    common: M7ReplayCursor,
    branch: M7ReplayCursor,
) -> BranchInventoryDelta:
    if (
        branch.next_event_position != common.next_event_position
        or branch.current_time != common.current_time
        or branch.timestamp_group_sequence != common.timestamp_group_sequence
        or branch.timestamp_subsequence != common.timestamp_subsequence
        or branch.previous_release != common.previous_release
    ):
        raise ValueError("M8 branch cursor metadata differs from the common cursor")
    common_by_id = {item.remnant.remnant_id: item for item in common.inventory}
    branch_by_id = {item.remnant.remnant_id: item for item in branch.inventory}
    for remnant_id in set(common_by_id) & set(branch_by_id):
        if common_by_id[remnant_id] != branch_by_id[remnant_id]:
            raise ValueError("M8 shared remnant record differs between common and branch")
    return BranchInventoryDelta(
        added=tuple(branch_by_id[key] for key in sorted(set(branch_by_id) - set(common_by_id))),
        removed=tuple(common_by_id[key] for key in sorted(set(common_by_id) - set(branch_by_id))),
    )


def _rejection_payload(
    rejections: tuple[CompiledTranslationRejection, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "candidate_id": item.candidate_id,
            "certificate": asdict(item.certificate),
        }
        for item in rejections
    )


def _search_payload(
    searches: tuple[LayoutFitSearchResult, ...],
    *,
    label: str,
) -> tuple[dict[str, object], ...]:
    canonical = _canonical_searches(searches, label=label)
    return tuple(search.model_dump(mode="json") for search in canonical)


def _canonical_searches(
    searches: tuple[LayoutFitSearchResult, ...],
    *,
    label: str,
) -> tuple[LayoutFitSearchResult, ...]:
    try:
        return tuple(
            LayoutFitSearchResult.model_validate(
                search.model_dump(mode="python"),
                strict=True,
            )
            for search in searches
        )
    except ValueError as error:
        raise ValueError(f"M8 {label} fit-search cache value is invalid") from error


def _require_search_bindings(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    searches: tuple[LayoutFitSearchResult, ...],
    require_remnant_id: bool,
) -> None:
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    if (
        tuple(search.candidate_id for search in searches)
        != tuple(candidate.candidate_id for candidate in verified.candidates)
        or any(search.config != runtime.replay_input.search_config for search in searches)
        or (
            require_remnant_id
            and any(search.remnant_id != item.remnant.remnant_id for search in searches)
        )
    ):
        raise ValueError("M8 exact search identities differ from the frozen request")


def _fresh_runtime(runtime: M7ReplayRuntime) -> M7ReplayRuntime:
    return M7ReplayRuntime(
        replay_input=runtime.replay_input,
        runtime_candidates=runtime.runtime_candidates,
        rules=runtime.rules,
        standard_profile_executor=runtime.standard_profile_executor,
        jagua_executable=runtime.jagua_executable,
        jagua_differential_audit=runtime.jagua_differential_audit,
        prepared_layout_cache=OrderedDict(),
    )


def _require_cached_searches_match_authoritative(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    authoritative: tuple[LayoutFitSearchResult, ...],
) -> None:
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    local_key = (
        item.remnant.remnant_id,
        binding.problem_id,
        verified.evidence.candidate_set_id,
    )
    cached_local = runtime.fit_search_cache.get(local_key)
    if cached_local is not None:
        canonical_local = _canonical_searches(cached_local, label="local")
        _require_search_bindings(
            runtime,
            event_position=event_position,
            item=item,
            searches=canonical_local,
            require_remnant_id=True,
        )
        if canonical_local != authoritative:
            raise ValueError(
                "M8 local fit-search cache value differs from authoritative registered search"
            )

    if runtime.shared_fit_search_cache is not None:
        cached_shared = runtime.shared_fit_search_cache.get(
            m7_shared_fit_search_cache_key(
                geometry=item.remnant.geometry,
                fit_config=runtime.replay_input.fit_config,
                search_config=runtime.replay_input.search_config,
                problem_id=binding.problem_id,
                candidate_set_id=verified.evidence.candidate_set_id,
            )
        )
        if cached_shared is not None:
            canonical_shared = _canonical_searches(cached_shared, label="shared")
            _require_search_bindings(
                runtime,
                event_position=event_position,
                item=item,
                searches=canonical_shared,
                require_remnant_id=False,
            )
            rebound_shared = tuple(
                search.model_copy(update={"remnant_id": item.remnant.remnant_id})
                for search in canonical_shared
            )
            if rebound_shared != authoritative:
                raise ValueError(
                    "M8 shared fit-search cache value differs from authoritative registered "
                    "search"
                )


def _require_common_search_caches_match_authoritative(
    runtime: M7ReplayRuntime,
    *,
    authoritative_runtime: M7ReplayRuntime,
    event_position: int,
    inventory: tuple[InventoryItem, ...],
) -> None:
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    for item in inventory:
        local_key = (
            item.remnant.remnant_id,
            binding.problem_id,
            verified.evidence.candidate_set_id,
        )
        authoritative = authoritative_runtime.fit_search_cache.get(local_key)
        if authoritative is None:
            continue
        canonical = _canonical_searches(authoritative, label="authoritative common")
        _require_search_bindings(
            runtime,
            event_position=event_position,
            item=item,
            searches=canonical,
            require_remnant_id=True,
        )
        _require_cached_searches_match_authoritative(
            runtime,
            event_position=event_position,
            item=item,
            authoritative=canonical,
        )


def _authoritative_competitor(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    cursor_template: M7ReplayCursor,
) -> tuple[
    M7ActionDescriptor | None,
    ActionPolicyContext | None,
    tuple[LayoutFitSearchResult, ...],
]:
    fresh = _fresh_runtime(runtime)
    competitor, context = enumerate_m7_single_remnant_competitor(
        fresh,
        event_position=event_position,
        item=item,
        cursor_template=cursor_template,
    )
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    local_key = (
        item.remnant.remnant_id,
        binding.problem_id,
        verified.evidence.candidate_set_id,
    )
    authoritative = fresh.fit_search_cache.get(local_key)
    if authoritative is None:
        raise ValueError("M8 fresh exact search did not produce authoritative evidence")
    authoritative = _canonical_searches(authoritative, label="authoritative")
    _require_search_bindings(
        runtime,
        event_position=event_position,
        item=item,
        searches=authoritative,
        require_remnant_id=True,
    )

    _require_cached_searches_match_authoritative(
        runtime,
        event_position=event_position,
        item=item,
        authoritative=authoritative,
    )
    return competitor, context, authoritative


def _evidence_sha256(
    *,
    event_position: int,
    remnant_id: str,
    classification: str,
    direction: str,
    delta: BranchInventoryDelta,
    common: M7PolicyActionBinding,
    common_decision_key: tuple[str, ...],
    common_fact_sha256: str,
    branch_action_id: str,
    state_before_sha256: str,
    state_after_sha256: str,
    rejections: tuple[CompiledTranslationRejection, ...],
    searches: tuple[dict[str, object], ...],
    competitor: M7ActionDescriptor | None,
    competitor_rank: PolicyRank | None,
) -> str:
    """Commit one influence through already-validated parent commitments.

    The common fact commits the semantic runtime, replay input, common cursor,
    transition, action binding, event and rank.  The two state commitments bind
    the complete branch states.  This v2 node therefore hashes only their
    content addresses plus the small influence-specific evidence.  Its security
    relies on SHA-256 collision and second-preimage resistance.
    """

    competitor_evidence = None
    if competitor is not None:
        if competitor.evidence is None:
            raise ValueError("M8 remnant competitor lacks exact materialized evidence")
        competitor_evidence = {
            "catalog_action_id": competitor.action_id,
            "candidate_id": competitor.candidate_id,
            "selected_remnant_id": competitor.selected_remnant_id,
            "materialized_action_id": competitor.evidence.action_id,
            "materialized_content_sha256": competitor.evidence.content_sha256,
            "rank": _rank_payload(competitor_rank) if competitor_rank is not None else None,
        }
    payload = {
        "schema_version": "yieldforge.m8-event-influence-evidence.v2",
        "commitments": {
            "common_transition_fact_sha256": common_fact_sha256,
            "state_before_sha256": state_before_sha256,
            "state_after_sha256": state_after_sha256,
        },
        "event_position": event_position,
        "direction": direction,
        "remnant_id": remnant_id,
        "classification": classification,
        "delta_added_ids": _item_ids(delta.added),
        "delta_removed_ids": _item_ids(delta.removed),
        "common": {
            "catalog_action_id": common.catalog_action_id,
            "materialized_action_id": common.materialized_action_id,
            "decision_key": common_decision_key,
        },
        "branch_action_id": branch_action_id,
        "cheap_rejections": _rejection_payload(rejections),
        "exact_searches": searches,
        "competitor": competitor_evidence,
    }
    return f"sha256:{semantic_sha256(payload)}"


def _influence(
    runtime: M7ReplayRuntime,
    *,
    cursor_template: M7ReplayCursor,
    event_position: int,
    item: InventoryItem,
    direction: str,
    delta: BranchInventoryDelta,
    common: M7PolicyActionBinding,
    common_rank: PolicyRank,
    common_fact_sha256: str,
    common_action_id: str,
    branch_action_id: str,
    state_before_sha256: str,
    state_after_sha256: str,
    prepared_layouts: _PreparedTranslationLayoutBatch | None,
) -> tuple[M8InfluenceWitness | None, int]:
    rejections = (
        compile_translation_rejections(
            runtime,
            event_position=event_position,
            item=item,
        )
        if prepared_layouts is None
        else _compile_prepared_translation_rejections(
            runtime,
            prepared=prepared_layouts,
            event_position=event_position,
            item=item,
        )
    )
    if rejections and all(entry.certificate.impossible for entry in rejections):
        digest = _evidence_sha256(
            event_position=event_position,
            remnant_id=item.remnant.remnant_id,
            classification="no_fit",
            direction=direction,
            delta=delta,
            common=common,
            common_decision_key=common_rank.decision_key,
            common_fact_sha256=common_fact_sha256,
            branch_action_id=branch_action_id,
            state_before_sha256=state_before_sha256,
            state_after_sha256=state_after_sha256,
            rejections=rejections,
            searches=(),
            competitor=None,
            competitor_rank=None,
        )
        return (
            M8InfluenceWitness(
                remnant_id=item.remnant.remnant_id,
                classification="no_fit",
                evidence_sha256=digest,
                common_action_id=common_action_id,
                common_catalog_action_id=common.catalog_action_id,
                common_decision_key=common_rank.decision_key,
            ),
            0,
        )

    competitor, context, authoritative_searches = _authoritative_competitor(
        runtime,
        event_position=event_position,
        item=item,
        cursor_template=cursor_template,
    )
    searches = _search_payload(authoritative_searches, label="authoritative")
    if competitor is None or context is None:
        if competitor is not None or context is not None:
            raise ValueError("M8 exact competitor descriptor and context must appear together")
        digest = _evidence_sha256(
            event_position=event_position,
            remnant_id=item.remnant.remnant_id,
            classification="no_fit",
            direction=direction,
            delta=delta,
            common=common,
            common_decision_key=common_rank.decision_key,
            common_fact_sha256=common_fact_sha256,
            branch_action_id=branch_action_id,
            state_before_sha256=state_before_sha256,
            state_after_sha256=state_after_sha256,
            rejections=rejections,
            searches=searches,
            competitor=None,
            competitor_rank=None,
        )
        return (
            M8InfluenceWitness(
                remnant_id=item.remnant.remnant_id,
                classification="no_fit",
                evidence_sha256=digest,
                common_action_id=common_action_id,
                common_catalog_action_id=common.catalog_action_id,
                common_decision_key=common_rank.decision_key,
            ),
            1,
        )

    competitor_rank = rank_policy_action(runtime.replay_input.policy.name, context)
    if not common_rank <= competitor_rank:
        return None, 1
    if competitor.evidence is None:
        raise ValueError("M8 exact remnant competitor lacks materialized evidence")
    digest = _evidence_sha256(
        event_position=event_position,
        remnant_id=item.remnant.remnant_id,
        classification="policy_dominated",
        direction=direction,
        delta=delta,
        common=common,
        common_decision_key=common_rank.decision_key,
        common_fact_sha256=common_fact_sha256,
        branch_action_id=branch_action_id,
        state_before_sha256=state_before_sha256,
        state_after_sha256=state_after_sha256,
        rejections=rejections,
        searches=searches,
        competitor=competitor,
        competitor_rank=competitor_rank,
    )
    return (
        M8InfluenceWitness(
            remnant_id=item.remnant.remnant_id,
            candidate_id=competitor.candidate_id,
            classification="policy_dominated",
            evidence_sha256=digest,
            common_action_id=common_action_id,
            common_catalog_action_id=common.catalog_action_id,
            competing_action_id=competitor.evidence.action_id,
            competing_catalog_action_id=competitor.action_id,
            common_decision_key=common_rank.decision_key,
            competing_decision_key=competitor_rank.decision_key,
        ),
        1,
    )


def _build_passive_event_result(
    *,
    branch_after: M7ReplayCursor,
    state_before_sha256: str,
    event_position: int,
    common_action_id: str,
    branch_action_id: str,
    build_influences: Callable[
        [str], tuple[tuple[M8InfluenceWitness, ...] | None, int]
    ],
) -> EventPassivityResult:
    """Bind one authoritative resulting cursor to both its witness and result."""

    state_after_sha256 = m7_cursor_sha256(branch_after)
    influences, exact_search_count = build_influences(state_after_sha256)
    if influences is None:
        return EventPassivityResult(
            passive=False,
            witness=None,
            branch_after=None,
            exact_search_count=exact_search_count,
        )
    classification = (
        "no_fit"
        if all(item.classification == "no_fit" for item in influences)
        else "policy_dominated"
    )
    witness = M8EventWitness(
        event_position=event_position,
        classification=classification,
        common_action_id=common_action_id,
        branch_action_id=branch_action_id,
        state_before_sha256=state_before_sha256,
        state_after_sha256=state_after_sha256,
        influences=influences,
    )
    return _trusted_passive_result(
        witness=witness,
        branch_after=branch_after,
        exact_search_count=exact_search_count,
        branch_after_sha256=state_after_sha256,
    )


def certify_event_passivity(
    runtime: M7ReplayRuntime,
    *,
    common: ValidatedCommonTransition,
    branch_cursor: M7ReplayCursor,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
) -> EventPassivityResult:
    """Prove one branch event selects the exact common M7 action, or fail closed."""

    fact, snapshot, authority = _require_validated_common_transition(runtime, common)
    proof_context = (
        nullcontext(authority.runtime)
        if authority is not None
        else snapshot.runtime_for_proof()
    )
    with proof_context as proof_runtime:
        try:
            delta = _derive_branch_inventory_delta(fact.cursor_before, branch_cursor)
            if not delta.added and not delta.removed:
                return EventPassivityResult(
                    passive=False,
                    witness=None,
                    branch_after=None,
                    exact_search_count=0,
                )
            binding = fact.step.action_binding
            common_action_id = fact.step.event.action.action_id
            branch_action_id = common_action_id
            if binding.context.selected_stock_id in set(_item_ids(delta.removed)):
                return EventPassivityResult(
                    passive=False,
                    witness=None,
                    branch_after=None,
                    exact_search_count=0,
                )

            branch_after = apply_m7_frozen_action_evidence(
                proof_runtime,
                cursor=branch_cursor,
                event_position=fact.event_position,
                action=fact.step.event.action,
            )
            state_before_sha256 = m7_cursor_sha256(branch_cursor)
            common_rank = fact.policy_rank

            def build_influences(
                state_after_sha256: str,
            ) -> tuple[tuple[M8InfluenceWitness, ...] | None, int]:
                influences = []
                exact_search_count = 0
                for direction, items in (
                    ("added", delta.added),
                    ("removed", delta.removed),
                ):
                    for item in items:
                        influence, searches = _influence(
                            proof_runtime,
                            cursor_template=fact.cursor_before,
                            event_position=fact.event_position,
                            item=item,
                            direction=direction,
                            delta=delta,
                            common=binding,
                            common_rank=common_rank,
                            common_fact_sha256=fact.content_sha256,
                            common_action_id=common_action_id,
                            branch_action_id=branch_action_id,
                            state_before_sha256=state_before_sha256,
                            state_after_sha256=state_after_sha256,
                            prepared_layouts=prepared_layouts,
                        )
                        exact_search_count += searches
                        if influence is None:
                            return None, exact_search_count
                        influences.append(influence)
                return tuple(influences), exact_search_count

            return _build_passive_event_result(
                branch_after=branch_after,
                state_before_sha256=state_before_sha256,
                event_position=fact.event_position,
                common_action_id=common_action_id,
                branch_action_id=branch_action_id,
                build_influences=build_influences,
            )
        finally:
            if authority is None:
                _require_caller_runtime_stable(
                    runtime,
                    expected_sha256=snapshot.semantic_sha256,
                    operation="M8 certificate operation",
                )
            else:
                authority._require_active_identity(runtime)  # noqa: SLF001


__all__ = [
    "BranchInventoryDelta",
    "EventPassivityResult",
    "M8CommonTransitionFact",
    "ValidatedCommonTransition",
    "build_m8_common_transition_fact",
    "build_validated_m8_common_transition",
    "build_validated_m8_common_transition_in_context",
    "certify_event_passivity",
    "validate_m8_common_transition_fact",
]
