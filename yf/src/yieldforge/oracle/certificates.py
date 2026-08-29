"""Exact no-fit and policy-dominance certificates for one M8 future event."""

from __future__ import annotations

import copy
import hashlib
import os
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Literal

from yieldforge.baseline.archives import (
    VerifiedCandidateRejectionLayout,
    VerifiedProblemCandidates,
)
from yieldforge.baseline.contracts import (
    LayoutFitSearchConfig,
    LayoutFitSearchResult,
    LayoutFitSearchStatus,
    M7ActionKind,
    M7CandidateSetEvidence,
    M7LayoutActionEvidence,
    ReusableGeometryProblem,
    TemporalInstanceBinding,
)
from yieldforge.baseline.geometry import (
    LayoutTranslationCandidates,
    PreparedTranslationRejectionRemnant,
    certify_translation_impossible,
    generate_layout_translations,
    prepare_layout_footprint,
    prepare_remnant_geometry,
    prepare_translation_rejection_remnant,
    search_layout_translation,
)
from yieldforge.baseline.jagua import (
    JaguaRepresentationError,
    run_jagua_generated_prefilter,
)
from yieldforge.baseline.policies import (
    ActionPolicyContext,
    M7PolicyName,
    PolicyRank,
    rank_policy_action,
)
from yieldforge.baseline.replay import (
    M7ActionDescriptor,
    M7AuthoritativeProofRuntime,
    M7CursorTransition,
    M7PolicyActionBinding,
    M7ReplayCursor,
    M7ReplayEvent,
    M7ReplayInput,
    M7ReplayRuntime,
    M7SemanticRuntimeSnapshot,
    M7StandardActionProfile,
    M7StepResult,
    apply_m7_action_descriptor,
    apply_m7_frozen_action_evidence_with_commitments,
    authoritative_m7_proof_runtime,
    enumerate_m7_action_catalog,
    enumerate_m7_pruned_action_catalog,
    enumerate_m7_single_remnant_competitor,
    enumerate_m7_standard_only_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    m7_shared_fit_search_cache_key,
    select_m7_fallback,
    snapshot_m7_replay_runtime,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import facts as portable_facts
from yieldforge.oracle.columnar import (
    C0FrontierColumns,
    C0FrontierQuery,
    C0FrontierResult,
    certify_frontier_impossible_batch,
)
from yieldforge.oracle.compiled import (
    CompiledRejectionProblem,
    CompiledTranslationRejection,
    M8PreparedFrontierIntegrityError,
    _activate_prepared_event_validation,
    _capture_prepared_remnant_source,
    _compile_prepared_translation_rejections,
    _consume_prepared_layout_footprints,
    _consume_prepared_rejection_problem,
    _consume_prepared_standard_winner,
    _exact_instance_state,
    _preflight_prepared_source_runtime,
    _prepared_frontier_batch_inputs,
    _prepared_layout_footprints,
    _prepared_rejection_problem,
    _prepared_source_runtime,
    _prepared_standard_winner,
    _PreparedTranslationLayoutBatch,
    _registered_prepared_remnant_measurement,
    _require_prepared_frontier_batch_inputs,
    _validate_exact_model_graph,
    _verified_rejection_layouts_cover_candidates,
    compile_rejection_problem,
    compile_translation_rejections,
)
from yieldforge.oracle.concurrency import (
    activate_m8_local_trusted_audit,
    require_m8_translation_audit_processes,
)
from yieldforge.oracle.frontier import ParetoFrontier, certify_frontier_impossible
from yieldforge.oracle.prepared import (
    _C0_FRONTIER_KERNEL_IDENTITY,
    _C0_FRONTIER_KERNEL_MODE,
)
from yieldforge.oracle.profiling import increment_profile_count, profile_phase
from yieldforge.oracle.proofs import M8EventWitness, M8InfluenceWitness
from yieldforge.oracle.translation_count_audit import audit_layout_translation_batch
from yieldforge.replay.contracts import InventoryItem, ReplayCostLedger
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.reuse.contracts import RemnantFitConfig
from yieldforge.reuse.geometry import material_key


def _item_ids(items: tuple[InventoryItem, ...]) -> tuple[str, ...]:
    return tuple(item.remnant.remnant_id for item in items)


@contextmanager
def _m8_authoritative_proof_runtime(
    runtime: M7ReplayRuntime,
) -> Iterator[M7AuthoritativeProofRuntime]:
    """Own M7 authority cleanup without letting it downgrade M8 integrity."""

    body_error: BaseException | None = None
    yielded = False
    try:
        with authoritative_m7_proof_runtime(runtime) as authority:
            from yieldforge.baseline import replay as replay_module

            yielded = True
            authority_key = id(authority)
            authority_registry = replay_module._AUTHORITATIVE_PROOF_RUNTIME_REGISTRY  # noqa: SLF001
            original_record = authority_registry.get(authority_key)
            try:
                yield authority
            except BaseException as error:
                body_error = error
            authority_integrity_error = None
            entries = tuple(authority_registry.items())
            valid_entries = tuple((key, value) for key, value in entries if type(key) is int)
            if len(valid_entries) != len(entries):
                authority_registry.clear()
                authority_registry.update(valid_entries)
                authority_integrity_error = M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: "
                    "authoritative proof runtime registry keys"
                )
            if authority_registry.get(authority_key) is not original_record:
                authority_registry[authority_key] = original_record
                if authority_integrity_error is None:
                    authority_integrity_error = M8PreparedFrontierIntegrityError(
                        "M8 prepared frontier integrity differs: "
                        "authoritative proof runtime capability"
                    )
            if isinstance(body_error, (KeyboardInterrupt, SystemExit)):
                raise body_error
            if isinstance(body_error, M8PreparedFrontierIntegrityError):
                raise body_error
            if authority_integrity_error is not None:
                if body_error is not None:
                    authority_integrity_error.__cause__ = body_error
                raise authority_integrity_error
            if body_error is not None:
                raise body_error
    except Exception as error:
        if error is body_error:
            raise
        if isinstance(body_error, M8PreparedFrontierIntegrityError):
            raise body_error from error
        if not yielded:
            raise
        integrity_error = M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: authoritative proof runtime capability"
        )
        integrity_error.__cause__ = error
        raise integrity_error from error


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


_C0_BRANCH_AUTHORITY_ISSUER = object()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _C0PreparedFrontierBranchAuthority:
    """Semantic authority for one indexed branch before a C0 event."""

    branch_id: int
    event_position: int
    catalog_action_id: str
    root_action_id: str
    root_step: M7StepResult
    common_before: M7ReplayCursor
    branch_before: M7ReplayCursor
    _branch_batch: object = field(repr=False, compare=False)
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _C0_BRANCH_AUTHORITY_ISSUER:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: unissued branch authority"
            )
        if type(self.branch_id) is not int or self.branch_id < 0:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch index"
            )
        if type(self.event_position) is not int or self.event_position < 0:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event identity"
            )
        if any(
            type(value) is not str or not value
            for value in (self.catalog_action_id, self.root_action_id)
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: root action identity"
            )
        if type(self.root_step) is not M7StepResult or (
            self.catalog_action_id != self.root_step.descriptor.action_id
            or self.catalog_action_id != self.root_step.action_binding.catalog_action_id
            or self.root_action_id != self.root_step.event.action.action_id
            or self.root_action_id != self.root_step.action_binding.materialized_action_id
            or self.root_step.event.sequence >= self.event_position
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: root action binding"
            )
        if (
            type(self.common_before) is not M7ReplayCursor
            or type(self.branch_before) is not M7ReplayCursor
            or self.common_before.next_event_position != self.event_position
            or self.branch_before.next_event_position != self.event_position
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: cursor event binding"
            )


_C0_BRANCH_SCOPE_ISSUER = object()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _C0PreparedFrontierGeneratorScope:
    """Opaque runtime/catalog scope allowed to issue exact C0 branch authorities."""

    _branch_batch: object = field(repr=False, compare=False)
    _record: object | None = field(repr=False, compare=False)
    _token: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _RegisteredC0PreparedFrontierGeneratorScope:
    reference: weakref.ReferenceType[_C0PreparedFrontierGeneratorScope]
    owner_pid: int
    token: object
    branch_batch: object
    generator_context: object
    generator_context_fingerprint: str
    runtime_authority: M7AuthoritativeProofRuntime
    context_runtime: M7ReplayRuntime
    context_prepared_layouts: _PreparedTranslationLayoutBatch
    consumer_runtime: M7ReplayRuntime | None
    consumer_prepared_layouts: _PreparedTranslationLayoutBatch | None
    semantic_runtime_sha256: str
    root_cursor: M7ReplayCursor
    catalog: object
    common_before: M7ReplayCursor
    source_branches: tuple[object, ...]
    source_positions_by_identity: dict[int, int]
    branch_ids: tuple[int, ...]
    descriptors: tuple[M7ActionDescriptor, ...]
    root_steps: tuple[M7StepResult, ...]
    branch_cursors: tuple[M7ReplayCursor, ...]
    kernel_mode: str
    kernel_identity: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _RegisteredC0PreparedFrontierBranchAuthority:
    reference: weakref.ReferenceType[_C0PreparedFrontierBranchAuthority]
    owner_pid: int
    token: object
    branch_batch: object
    source_scope: _C0PreparedFrontierGeneratorScope
    source_branch: object
    descriptor: M7ActionDescriptor
    root_step: M7StepResult
    branch_before: M7ReplayCursor
    common_before: M7ReplayCursor
    content_sha256: str


_C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY: dict[
    int,
    _RegisteredC0PreparedFrontierBranchAuthority,
] = {}
_C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY: dict[
    int,
    _RegisteredC0PreparedFrontierGeneratorScope,
] = {}


@dataclass(frozen=True, slots=True)
class _C0PreparedFrontierChildOwnerBinding:
    """Immutable child-to-batch ownership independent of mutable parent ledgers."""

    child_reference: weakref.ReferenceType[object]
    child_id: int
    branch_batch_reference: weakref.ReferenceType[object]
    branch_batch_id: int
    owner_pid: int


@dataclass(frozen=True, slots=True)
class _C0PreparedFrontierBatchChildIndex:
    """Exact private insertion-ordered child index for one live C0 batch."""

    branch_batch_reference: weakref.ReferenceType[object]
    branch_batch_id: int
    owner_pid: int
    scope_ids: dict[int, None]
    authority_ids: dict[int, None]


_C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY: dict[
    int,
    _C0PreparedFrontierChildOwnerBinding,
] = {}
_C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY: dict[
    int,
    _C0PreparedFrontierChildOwnerBinding,
] = {}
_C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY: dict[
    int,
    _C0PreparedFrontierBatchChildIndex,
] = {}


def _bind_c0_prepared_frontier_child_owner(
    child: object,
    *,
    branch_batch: object,
    owner_registry: dict[int, _C0PreparedFrontierChildOwnerBinding],
    is_scope: bool,
) -> None:
    """Atomically add one child owner sidecar and immutable batch-index entry."""

    child_id = id(child)
    branch_batch_id = id(branch_batch)
    index = _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.get(branch_batch_id)
    if index is None:
        index = _C0PreparedFrontierBatchChildIndex(
            branch_batch_reference=weakref.ref(branch_batch),
            branch_batch_id=branch_batch_id,
            owner_pid=os.getpid(),
            scope_ids={},
            authority_ids={},
        )
    if (
        type(index) is not _C0PreparedFrontierBatchChildIndex
        or type(index.branch_batch_reference) is not weakref.ReferenceType
        or index.branch_batch_reference() is not branch_batch
        or index.branch_batch_id != branch_batch_id
        or index.owner_pid != os.getpid()
        or type(index.scope_ids) is not dict
        or type(index.authority_ids) is not dict
        or child_id in owner_registry
        or child_id in index.scope_ids
        or child_id in index.authority_ids
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: duplicate C0 child owner"
        )
    binding = _C0PreparedFrontierChildOwnerBinding(
        child_reference=weakref.ref(child),
        child_id=child_id,
        branch_batch_reference=index.branch_batch_reference,
        branch_batch_id=branch_batch_id,
        owner_pid=os.getpid(),
    )
    owner_registry[child_id] = binding
    (index.scope_ids if is_scope else index.authority_ids)[child_id] = None
    _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY[branch_batch_id] = index


def _discard_c0_prepared_frontier_child_owner(
    child_id: int,
    *,
    branch_batch_id: int,
    owner_registry: dict[int, _C0PreparedFrontierChildOwnerBinding],
    is_scope: bool,
) -> None:
    """Remove one exact sidecar and its immutable batch-index membership."""

    owner_registry.pop(child_id, None)
    index = _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.get(branch_batch_id)
    if (
        type(index) is not _C0PreparedFrontierBatchChildIndex
        or type(index.scope_ids) is not dict
        or type(index.authority_ids) is not dict
    ):
        _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.pop(branch_batch_id, None)
        return
    index.scope_ids.pop(child_id, None)
    index.authority_ids.pop(child_id, None)
    if index.scope_ids or index.authority_ids:
        _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY[branch_batch_id] = index
    else:
        _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.pop(branch_batch_id, None)


def _require_c0_prepared_frontier_child_owner(
    child: object,
    *,
    branch_batch: object,
    owner_registry: dict[int, _C0PreparedFrontierChildOwnerBinding],
    is_scope: bool,
) -> None:
    """Validate one child owner sidecar and exact index membership in O(1)."""

    child_id = id(child)
    branch_batch_id = id(branch_batch)
    binding = owner_registry.get(child_id)
    index = _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.get(branch_batch_id)
    indexed_ids = (
        (index.scope_ids if is_scope else index.authority_ids)
        if (
            type(index) is _C0PreparedFrontierBatchChildIndex
            and type(index.scope_ids) is dict
            and type(index.authority_ids) is dict
        )
        else {}
    )
    if (
        type(binding) is not _C0PreparedFrontierChildOwnerBinding
        or type(binding.child_reference) is not weakref.ReferenceType
        or binding.child_reference() is not child
        or binding.child_id != child_id
        or type(binding.branch_batch_reference) is not weakref.ReferenceType
        or binding.branch_batch_reference() is not branch_batch
        or binding.branch_batch_id != branch_batch_id
        or binding.owner_pid != os.getpid()
        or type(index) is not _C0PreparedFrontierBatchChildIndex
        or type(index.branch_batch_reference) is not weakref.ReferenceType
        or index.branch_batch_reference() is not branch_batch
        or index.branch_batch_id != branch_batch_id
        or index.owner_pid != os.getpid()
        or type(index.scope_ids) is not dict
        or type(index.authority_ids) is not dict
        or indexed_ids.get(child_id, object()) is not None
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: immutable C0 child owner"
        )


@dataclass(frozen=True, slots=True)
class _ValidatedC0PreparedFrontierEventTransaction:
    branch_batch: object
    owner_record: object
    source_scope: _C0PreparedFrontierGeneratorScope
    scope_record: _RegisteredC0PreparedFrontierGeneratorScope
    authorities: tuple[_C0PreparedFrontierBranchAuthority, ...]
    authority_records: tuple[_RegisteredC0PreparedFrontierBranchAuthority, ...]


def _c0_prepared_frontier_branch_authority_sha256(
    authority: _C0PreparedFrontierBranchAuthority,
) -> str:
    return "sha256:" + semantic_sha256(
        {
            "schema_version": "yieldforge.m8-c0-branch-authority.v1",
            "branch_id": authority.branch_id,
            "event_position": authority.event_position,
            "catalog_action_id": authority.catalog_action_id,
            "root_action_id": authority.root_action_id,
            "root_event_position": authority.root_step.event.sequence,
            "root_cursor_sha256": m7_cursor_sha256(authority.root_step.cursor),
            "common_before_sha256": m7_cursor_sha256(authority.common_before),
            "branch_before_sha256": m7_cursor_sha256(authority.branch_before),
            "branch_batch_id": id(authority._branch_batch),  # noqa: SLF001
        }
    )


def _c0_prepared_frontier_generator_scope_sha256(
    *,
    branch_batch: object,
    generator_context: object,
    generator_context_fingerprint: str,
    runtime_authority: M7AuthoritativeProofRuntime,
    context_runtime: M7ReplayRuntime,
    context_prepared_layouts: _PreparedTranslationLayoutBatch,
    consumer_runtime: M7ReplayRuntime | None,
    consumer_prepared_layouts: _PreparedTranslationLayoutBatch | None,
    semantic_runtime_sha256: str,
    root_cursor: M7ReplayCursor,
    catalog: object,
    common_before: M7ReplayCursor,
    source_branches: tuple[object, ...],
    branch_ids: tuple[int, ...],
    kernel_mode: str,
    kernel_identity: str,
) -> str:
    return "sha256:" + semantic_sha256(
        {
            "schema_version": "yieldforge.m8-c0-generator-scope.v2",
            "branch_batch_id": id(branch_batch),
            "generator_context_id": id(generator_context),
            "generator_context_fingerprint": generator_context_fingerprint,
            "runtime_authority_id": id(runtime_authority),
            "context_runtime_id": id(context_runtime),
            "context_prepared_layouts_id": id(context_prepared_layouts),
            "consumer_runtime_id": (id(consumer_runtime) if consumer_runtime is not None else None),
            "consumer_prepared_layouts_id": (
                id(consumer_prepared_layouts) if consumer_prepared_layouts is not None else None
            ),
            "semantic_runtime_sha256": semantic_runtime_sha256,
            "root_cursor_sha256": m7_cursor_sha256(root_cursor),
            "catalog_event_position": catalog.event_position,
            "catalog_action_ids": tuple(item.action_id for item in catalog.actions),
            "common_before_sha256": m7_cursor_sha256(common_before),
            "branch_ids": branch_ids,
            "kernel_mode": kernel_mode,
            "kernel_identity": kernel_identity,
            "branches": tuple(
                {
                    "descriptor_action_id": source.descriptor.action_id,
                    "root_action_id": source.initial_step.event.action.action_id,
                    "root_cursor_sha256": m7_cursor_sha256(source.initial_step.cursor),
                    "branch_cursor_sha256": m7_cursor_sha256(source.cursor),
                }
                for source in source_branches
            ),
        }
    )


def _issue_c0_prepared_frontier_generator_scope(
    branch_batch: object,
    *,
    consumer_runtime: M7ReplayRuntime | None = None,
    consumer_prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
    common_before: M7ReplayCursor | None = None,
    source_branches: tuple[object, ...] | None = None,
    root_cursor: M7ReplayCursor | None = None,
) -> _C0PreparedFrontierGeneratorScope:
    """Mint C0 authority only from one sparse-owned live event transaction."""

    try:
        from yieldforge.oracle import sparse

        if (
            type(branch_batch) is not sparse._M8PreparedC0BranchBatch  # noqa: SLF001
            or consumer_runtime is not None
            or consumer_prepared_layouts is not None
            or common_before is not None
            or source_branches is not None
            or root_cursor is not None
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: active C0 branch batch required"
            )
        branch_batch_record = sparse._require_prepared_c0_branch_batch(  # noqa: SLF001
            branch_batch
        )
        if (
            branch_batch_record.lifecycle.scope_issued
            or branch_batch_record.lifecycle.consumed
            or branch_batch_record.generator_scope_references
            or branch_batch_record.branch_authority_references
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: duplicate event scope"
            )
        generator_context = branch_batch_record.generator_context
        generator_context.require_active()
        context_registry = sparse._PREPARED_GENERATOR_REGISTRY.get(  # noqa: SLF001
            id(generator_context)
        )
        context_fingerprint = sparse._generator_context_fingerprint(  # noqa: SLF001
            generator_context
        )
        context_runtime = generator_context._request.runtime  # noqa: SLF001
        context_prepared_layouts = generator_context._prepared_layouts  # noqa: SLF001
        common_before = branch_batch_record.common.common_fact.cursor_before
        source_branches = branch_batch_record.branches
        if (
            context_registry is None
            or context_registry[0]() is not generator_context
            or context_registry[1] != os.getpid()
            or context_registry[2] != id(generator_context._authority)  # noqa: SLF001
            or context_registry[3] != context_fingerprint
            or context_prepared_layouts is not generator_context._prepared_layouts  # noqa: SLF001
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: generator context source"
            )
        runtime_authority = generator_context._authority  # noqa: SLF001
        runtime_authority.require_active(context_runtime)
        context_prepared_layouts.require_active(context_runtime)
        root_cursor = generator_context._request.cursor  # noqa: SLF001
        catalog = generator_context._catalog  # noqa: SLF001
        catalog_positions = {
            descriptor.action_id: position for position, descriptor in enumerate(catalog.actions)
        }
        branch_ids = tuple(
            catalog_positions.get(source.descriptor.action_id, -1) for source in source_branches
        )
        if (
            any(branch_id < 0 for branch_id in branch_ids)
            or branch_ids != tuple(sorted(set(branch_ids)))
            or common_before.next_event_position <= root_cursor.next_event_position
            or common_before.next_event_position >= generator_context._stop_event_position  # noqa: SLF001
        ):
            raise ValueError("M8 C0 generator source roots differ from the catalog")
        descriptors = tuple(catalog.actions[branch_id] for branch_id in branch_ids)
        root_steps = []
        branch_cursors = []
        for source, descriptor in zip(source_branches, descriptors, strict=True):
            expected_root = apply_m7_action_descriptor(
                context_runtime,
                cursor=root_cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
            )
            if (
                source.descriptor != descriptor
                or source.initial_step != expected_root
                or type(source.cursor) is not M7ReplayCursor
                or source.cursor.next_event_position != common_before.next_event_position
            ):
                raise ValueError("M8 C0 generator branch root differs from the catalog")
            root_steps.append(source.initial_step)
            branch_cursors.append(source.cursor)
        scope = _C0PreparedFrontierGeneratorScope(
            _branch_batch=branch_batch,
            _record=None,
            _token=_C0_BRANCH_SCOPE_ISSUER,
        )
        scope_id = id(scope)
        semantic_runtime_sha256 = runtime_authority.semantic_sha256

        def discard(
            reference: weakref.ReferenceType[_C0PreparedFrontierGeneratorScope],
        ) -> None:
            registered = _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.get(scope_id)
            released = (
                type(registered) is _RegisteredC0PreparedFrontierGeneratorScope
                and registered.reference is reference
            )
            if released:
                _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.pop(scope_id, None)
            if (
                released
                and branch_batch_record.generator_scope_references.get(scope_id) is reference
            ):
                branch_batch_record.generator_scope_references.pop(scope_id, None)
            if released:
                _discard_c0_prepared_frontier_child_owner(
                    scope_id,
                    branch_batch_id=id(branch_batch),
                    owner_registry=_C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY,
                    is_scope=True,
                )
                if (
                    type(branch_batch_record.generator_scope_references) is dict
                    and type(branch_batch_record.branch_authority_references) is dict
                    and not branch_batch_record.generator_scope_references
                    and not branch_batch_record.branch_authority_references
                ):
                    branch_batch_record.lifecycle.issued_branch_ids.clear()
                    branch_batch_record.lifecycle.consumed = True

        reference = weakref.ref(scope, discard)
        registered_scope = _RegisteredC0PreparedFrontierGeneratorScope(
            reference=reference,
            owner_pid=os.getpid(),
            token=_C0_BRANCH_SCOPE_ISSUER,
            branch_batch=branch_batch,
            generator_context=generator_context,
            generator_context_fingerprint=context_fingerprint,
            runtime_authority=runtime_authority,
            context_runtime=context_runtime,
            context_prepared_layouts=context_prepared_layouts,
            consumer_runtime=None,
            consumer_prepared_layouts=None,
            semantic_runtime_sha256=semantic_runtime_sha256,
            root_cursor=root_cursor,
            catalog=catalog,
            common_before=common_before,
            source_branches=source_branches,
            source_positions_by_identity={
                id(source): position for position, source in enumerate(source_branches)
            },
            branch_ids=branch_ids,
            descriptors=descriptors,
            root_steps=tuple(root_steps),
            branch_cursors=tuple(branch_cursors),
            kernel_mode=_C0_FRONTIER_KERNEL_MODE,
            kernel_identity=_C0_FRONTIER_KERNEL_IDENTITY,
            content_sha256=_c0_prepared_frontier_generator_scope_sha256(
                branch_batch=branch_batch,
                generator_context=generator_context,
                generator_context_fingerprint=context_fingerprint,
                runtime_authority=runtime_authority,
                context_runtime=context_runtime,
                context_prepared_layouts=context_prepared_layouts,
                consumer_runtime=None,
                consumer_prepared_layouts=None,
                semantic_runtime_sha256=semantic_runtime_sha256,
                root_cursor=root_cursor,
                catalog=catalog,
                common_before=common_before,
                source_branches=source_branches,
                branch_ids=branch_ids,
                kernel_mode=_C0_FRONTIER_KERNEL_MODE,
                kernel_identity=_C0_FRONTIER_KERNEL_IDENTITY,
            ),
        )
        object.__setattr__(scope, "_record", registered_scope)
        if (
            scope_id in _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY
            or scope_id in branch_batch_record.generator_scope_references
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: duplicate generator scope"
            )
        _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY[scope_id] = registered_scope
        branch_batch_record.generator_scope_references[scope_id] = reference
        try:
            _bind_c0_prepared_frontier_child_owner(
                scope,
                branch_batch=branch_batch,
                owner_registry=_C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY,
                is_scope=True,
            )
        except Exception:
            _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.pop(scope_id, None)
            branch_batch_record.generator_scope_references.pop(scope_id, None)
            raise
        branch_batch_record.lifecycle.scope_issued = True
        return scope
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: generator scope"
        ) from error


def _require_c0_prepared_frontier_generator_scope_registration(
    scope: _C0PreparedFrontierGeneratorScope,
) -> tuple[_RegisteredC0PreparedFrontierGeneratorScope, object]:
    """Validate one scope's process-local registry and parent links in O(1)."""

    try:
        from yieldforge.oracle import sparse

        registered = _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.get(id(scope))
        owner_record = getattr(scope._branch_batch, "_owner_record", None)  # noqa: SLF001
        _require_c0_prepared_frontier_child_owner(
            scope,
            branch_batch=scope._branch_batch,  # noqa: SLF001
            owner_registry=_C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY,
            is_scope=True,
        )
        if (
            type(scope) is not _C0PreparedFrontierGeneratorScope
            or type(registered) is not _RegisteredC0PreparedFrontierGeneratorScope
            or type(owner_record) is not sparse._RegisteredM8PreparedC0BranchBatch  # noqa: SLF001
            or scope._record is not registered  # noqa: SLF001
            or type(registered.reference) is not weakref.ReferenceType
            or registered.reference() is not scope
            or registered.owner_pid != os.getpid()
            or registered.token is not scope._token  # noqa: SLF001
            or scope._branch_batch is not registered.branch_batch  # noqa: SLF001
            or type(owner_record.reference) is not weakref.ReferenceType
            or owner_record.reference() is not registered.branch_batch
            or owner_record.owner_pid != os.getpid()
            or owner_record.token is not registered.branch_batch._token  # noqa: SLF001
            or registered.branch_batch._owner_record is not owner_record  # noqa: SLF001
            or type(owner_record.generator_scope_references) is not dict
            or owner_record.generator_scope_references.get(id(scope)) is not registered.reference
            or type(owner_record.branch_authority_references) is not dict
            or type(owner_record.lifecycle) is not sparse._M8PreparedC0BranchBatchLifecycle  # noqa: SLF001
            or not owner_record.lifecycle.scope_issued
            or owner_record.lifecycle.consumed
            or type(owner_record.lifecycle.issued_branch_ids) is not set
            or type(registered.source_branches) is not tuple
            or type(registered.source_positions_by_identity) is not dict
            or type(registered.branch_ids) is not tuple
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: generator scope authority"
            )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: generator scope authority"
        ) from error
    return registered, owner_record


def _require_c0_prepared_frontier_generator_scope(
    scope: _C0PreparedFrontierGeneratorScope,
) -> _RegisteredC0PreparedFrontierGeneratorScope:
    try:
        from yieldforge.oracle import sparse

        registered, shallow_owner_record = (
            _require_c0_prepared_frontier_generator_scope_registration(scope)
        )
        generator_context = registered.generator_context
        branch_batch_record = sparse._require_prepared_c0_branch_batch(  # noqa: SLF001
            registered.branch_batch
        )
        if type(generator_context) is not sparse._M8PreparedGeneratorContext:  # noqa: SLF001
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: generator scope authority"
            )
        generator_context.require_active()
        current_fingerprint = sparse._generator_context_fingerprint(  # noqa: SLF001
            generator_context
        )
        registered.runtime_authority.require_active(registered.context_runtime)
        registered.context_prepared_layouts.require_active(registered.context_runtime)
        consumer_bound = (
            registered.consumer_runtime is not None
            and registered.consumer_prepared_layouts is not None
        )
        if consumer_bound:
            registered.consumer_prepared_layouts.require_active(registered.consumer_runtime)
        if (
            type(scope) is not _C0PreparedFrontierGeneratorScope
            or shallow_owner_record is not branch_batch_record
            or type(registered.reference) is not weakref.ReferenceType
            or registered.reference() is not scope
            or registered.owner_pid != os.getpid()
            or registered.token is not scope._token  # noqa: SLF001
            or scope._branch_batch is not registered.branch_batch  # noqa: SLF001
            or branch_batch_record.generator_scope_references.get(id(scope))
            is not registered.reference
            or branch_batch_record.generator_context is not generator_context
            or branch_batch_record.common.common_fact.cursor_before is not registered.common_before
            or branch_batch_record.branches is not registered.source_branches
            or generator_context._authority is not registered.runtime_authority  # noqa: SLF001
            or generator_context._request.runtime is not registered.context_runtime  # noqa: SLF001
            or generator_context._request.cursor is not registered.root_cursor  # noqa: SLF001
            or generator_context._catalog is not registered.catalog  # noqa: SLF001
            or generator_context._prepared_layouts  # noqa: SLF001
            is not registered.context_prepared_layouts
            or (
                (registered.consumer_runtime is None)
                != (registered.consumer_prepared_layouts is None)
            )
            or (
                consumer_bound
                and registered.consumer_runtime is not generator_context._source_runtime  # noqa: SLF001
                and registered.consumer_runtime is not registered.context_runtime
            )
            or registered.generator_context_fingerprint != current_fingerprint
            or registered.semantic_runtime_sha256 != registered.runtime_authority.semantic_sha256
            or tuple(source.descriptor for source in registered.source_branches)
            != registered.descriptors
            or tuple(source.initial_step for source in registered.source_branches)
            != registered.root_steps
            or tuple(source.cursor for source in registered.source_branches)
            != registered.branch_cursors
            or registered.source_positions_by_identity
            != {id(source): position for position, source in enumerate(registered.source_branches)}
            or registered.kernel_mode != _C0_FRONTIER_KERNEL_MODE
            or registered.kernel_identity != _C0_FRONTIER_KERNEL_IDENTITY
            or registered.content_sha256
            != _c0_prepared_frontier_generator_scope_sha256(
                branch_batch=registered.branch_batch,
                generator_context=generator_context,
                generator_context_fingerprint=current_fingerprint,
                runtime_authority=registered.runtime_authority,
                context_runtime=registered.context_runtime,
                context_prepared_layouts=registered.context_prepared_layouts,
                consumer_runtime=registered.consumer_runtime,
                consumer_prepared_layouts=registered.consumer_prepared_layouts,
                semantic_runtime_sha256=registered.semantic_runtime_sha256,
                root_cursor=registered.root_cursor,
                catalog=registered.catalog,
                common_before=registered.common_before,
                source_branches=registered.source_branches,
                branch_ids=registered.branch_ids,
                kernel_mode=registered.kernel_mode,
                kernel_identity=registered.kernel_identity,
            )
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: generator scope authority"
            )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: generator scope authority"
        ) from error
    return registered


def _bind_c0_prepared_frontier_generator_scope(
    scope: _C0PreparedFrontierGeneratorScope,
    *,
    runtime: M7ReplayRuntime,
    prepared_layouts: _PreparedTranslationLayoutBatch,
) -> _RegisteredC0PreparedFrontierGeneratorScope:
    """Atomically bind an issued producer scope to its first exact consumer."""

    registered = _require_c0_prepared_frontier_generator_scope(scope)
    return _bind_c0_prepared_frontier_generator_scope_registration(
        scope,
        registered=registered,
        runtime=runtime,
        prepared_layouts=prepared_layouts,
    )


def _bind_c0_prepared_frontier_generator_scope_registration(
    scope: _C0PreparedFrontierGeneratorScope,
    *,
    registered: _RegisteredC0PreparedFrontierGeneratorScope,
    runtime: M7ReplayRuntime,
    prepared_layouts: _PreparedTranslationLayoutBatch,
) -> _RegisteredC0PreparedFrontierGeneratorScope:
    """Bind one already fully validated scope without rescanning its branches."""

    current, _owner_record = _require_c0_prepared_frontier_generator_scope_registration(scope)
    if current is not registered:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: C0 consumer binding race"
        )
    if (
        registered.consumer_runtime is runtime
        and registered.consumer_prepared_layouts is prepared_layouts
    ):
        return registered
    if (
        registered.consumer_runtime is not None
        or registered.consumer_prepared_layouts is not None
        or (
            runtime is not registered.context_runtime
            and runtime is not registered.generator_context._source_runtime  # noqa: SLF001
        )
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: C0 consumer binding"
        )
    try:
        prepared_layouts.require_active(runtime)
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: C0 consumer capability"
        ) from error
    bound = replace(
        registered,
        consumer_runtime=runtime,
        consumer_prepared_layouts=prepared_layouts,
        content_sha256=_c0_prepared_frontier_generator_scope_sha256(
            branch_batch=registered.branch_batch,
            generator_context=registered.generator_context,
            generator_context_fingerprint=registered.generator_context_fingerprint,
            runtime_authority=registered.runtime_authority,
            context_runtime=registered.context_runtime,
            context_prepared_layouts=registered.context_prepared_layouts,
            consumer_runtime=runtime,
            consumer_prepared_layouts=prepared_layouts,
            semantic_runtime_sha256=registered.semantic_runtime_sha256,
            root_cursor=registered.root_cursor,
            catalog=registered.catalog,
            common_before=registered.common_before,
            source_branches=registered.source_branches,
            branch_ids=registered.branch_ids,
            kernel_mode=registered.kernel_mode,
            kernel_identity=registered.kernel_identity,
        ),
    )
    if _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.get(id(scope)) is not registered:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: C0 consumer binding race"
        )
    _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY[id(scope)] = bound
    object.__setattr__(scope, "_record", bound)
    current, _owner_record = _require_c0_prepared_frontier_generator_scope_registration(scope)
    if current is not bound:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: C0 consumer binding race"
        )
    return bound


def _release_c0_prepared_frontier_generator_scopes(
    branch_batch: object,
    *,
    owner_record: object | None = None,
) -> None:
    """Consume exactly this batch's immutable-indexed children, failure atomically."""

    from yieldforge.oracle import sparse

    integrity_error: M8PreparedFrontierIntegrityError | None = None

    def note_integrity(error: BaseException | str) -> None:
        nonlocal integrity_error
        if integrity_error is None:
            if isinstance(error, M8PreparedFrontierIntegrityError):
                integrity_error = error
            else:
                integrity_error = M8PreparedFrontierIntegrityError(
                    f"M8 prepared frontier integrity differs: C0 branch batch cleanup ({error})"
                )
                if isinstance(error, BaseException):
                    integrity_error.__cause__ = error

    branch_batch_id = id(branch_batch)
    current_owner = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY.get(  # noqa: SLF001
        branch_batch_id
    )
    if owner_record is None:
        owner_record = current_owner
    owner_type_ok = type(owner_record) is sparse._RegisteredM8PreparedC0BranchBatch  # noqa: SLF001
    if not owner_type_ok:
        note_integrity("malformed parent")

    scope_ledger_ids: tuple[int, ...] = ()
    authority_ledger_ids: tuple[int, ...] = ()
    lifecycle: object | None = None
    if owner_type_ok:
        try:
            if (
                type(owner_record.reference) is not weakref.ReferenceType
                or owner_record.reference() is not branch_batch
                or owner_record.owner_pid != os.getpid()
                or owner_record.token is not branch_batch._token  # noqa: SLF001
                or branch_batch._owner_record is not owner_record  # noqa: SLF001
                or current_owner is not owner_record
            ):
                note_integrity("replaced parent")
            if type(owner_record.generator_scope_references) is dict:
                raw_scope_ledger_ids = tuple(owner_record.generator_scope_references)
                scope_ledger_ids = tuple(
                    child_id for child_id in raw_scope_ledger_ids if type(child_id) is int
                )
                if len(scope_ledger_ids) != len(raw_scope_ledger_ids):
                    note_integrity("malformed scope ledger key")
            else:
                note_integrity("malformed scope ledger")
            if type(owner_record.branch_authority_references) is dict:
                raw_authority_ledger_ids = tuple(owner_record.branch_authority_references)
                authority_ledger_ids = tuple(
                    child_id for child_id in raw_authority_ledger_ids if type(child_id) is int
                )
                if len(authority_ledger_ids) != len(raw_authority_ledger_ids):
                    note_integrity("malformed authority ledger key")
            else:
                note_integrity("malformed authority ledger")
            lifecycle = owner_record.lifecycle
            if type(lifecycle) is not sparse._M8PreparedC0BranchBatchLifecycle:  # noqa: SLF001
                lifecycle = None
                note_integrity("malformed lifecycle")
        except Exception as error:
            note_integrity(error)

    index = _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.get(branch_batch_id)
    index_valid = (
        type(index) is _C0PreparedFrontierBatchChildIndex
        and type(index.branch_batch_reference) is weakref.ReferenceType
        and index.branch_batch_reference() is branch_batch
        and type(index.branch_batch_id) is int
        and index.branch_batch_id == branch_batch_id
        and type(index.owner_pid) is int
        and index.owner_pid == os.getpid()
        and type(index.scope_ids) is dict
        and type(index.authority_ids) is dict
        and all(
            type(child_id) is int and value is None for child_id, value in index.scope_ids.items()
        )
        and all(
            type(child_id) is int and value is None
            for child_id, value in index.authority_ids.items()
        )
    )
    if index_valid:
        indexed_scope_ids = tuple(index.scope_ids)
        indexed_authority_ids = tuple(index.authority_ids)
    else:
        already_consumed = (
            type(lifecycle) is sparse._M8PreparedC0BranchBatchLifecycle  # noqa: SLF001
            and lifecycle.consumed is True
            and not scope_ledger_ids
            and not authority_ledger_ids
            and index is None
            and integrity_error is None
        )
        if already_consumed:
            if (
                type(lifecycle.scope_issued) is bool
                and lifecycle.consumed is True
                and type(lifecycle.issued_branch_ids) is set
                and not lifecycle.issued_branch_ids
            ):
                return
            note_integrity("consumed C0 lifecycle drift")
        note_integrity("missing exact child index")
        indexed_scope_ids = ()
        indexed_authority_ids = ()

    if set(scope_ledger_ids) != set(indexed_scope_ids) or set(authority_ledger_ids) != set(
        indexed_authority_ids
    ):
        note_integrity("mutable child ledger drift")

    def cleanup_child_is_local(
        child_id: int,
        *,
        owner_registry: dict[int, _C0PreparedFrontierChildOwnerBinding],
        child_registry: dict[int, object],
        child_record_type: type[object],
        child_type: type[object],
    ) -> bool:
        """Resolve local cleanup authority without trusting one mutable record alone."""

        registered = child_registry.get(child_id)
        registered_child = (
            registered.reference()
            if type(registered) is child_record_type
            and type(registered.reference) is weakref.ReferenceType
            else None
        )
        exact_registered_child = (
            type(registered) is child_record_type
            and type(registered_child) is child_type
            and id(registered_child) == child_id
            and registered.reference() is registered_child
            and type(registered.owner_pid) is int
            and registered.owner_pid == os.getpid()
        )
        binding = owner_registry.get(child_id)
        binding_owner = (
            binding.branch_batch_reference()
            if type(binding) is _C0PreparedFrontierChildOwnerBinding
            and type(binding.branch_batch_reference) is weakref.ReferenceType
            else None
        )
        binding_child = (
            binding.child_reference()
            if type(binding) is _C0PreparedFrontierChildOwnerBinding
            and type(binding.child_reference) is weakref.ReferenceType
            else None
        )
        valid_binding_owner = (
            type(binding) is _C0PreparedFrontierChildOwnerBinding
            and type(binding.child_reference) is weakref.ReferenceType
            and type(binding.child_id) is int
            and binding.child_id == child_id
            and binding_owner is not None
            and type(binding.branch_batch_id) is int
            and binding.branch_batch_id == id(binding_owner)
            and type(binding.owner_pid) is int
            and binding.owner_pid == os.getpid()
            and (
                (
                    type(binding_child) is child_type
                    and id(binding_child) == child_id
                    and binding_child._branch_batch is binding_owner  # type: ignore[attr-defined]  # noqa: SLF001
                )
                or (binding_child is None and not exact_registered_child)
            )
        )
        if valid_binding_owner:
            if binding_owner is not branch_batch:
                note_integrity("foreign C0 child owner")
                return False
            return True

        note_integrity("missing or malformed C0 child owner")
        return (
            exact_registered_child
            and registered.branch_batch is branch_batch
            and registered_child._branch_batch is branch_batch  # type: ignore[attr-defined]  # noqa: SLF001
        )

    scope_ids = tuple(
        child_id
        for child_id in dict.fromkeys(indexed_scope_ids + scope_ledger_ids)
        if cleanup_child_is_local(
            child_id,
            owner_registry=_C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY,
            child_registry=_C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY,
            child_record_type=_RegisteredC0PreparedFrontierGeneratorScope,
            child_type=_C0PreparedFrontierGeneratorScope,
        )
    )
    authority_ids = tuple(
        child_id
        for child_id in dict.fromkeys(indexed_authority_ids + authority_ledger_ids)
        if cleanup_child_is_local(
            child_id,
            owner_registry=_C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY,
            child_registry=_C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY,
            child_record_type=_RegisteredC0PreparedFrontierBranchAuthority,
            child_type=_C0PreparedFrontierBranchAuthority,
        )
    )
    if set(scope_ids) != set(indexed_scope_ids) or set(authority_ids) != set(indexed_authority_ids):
        note_integrity("foreign C0 child index ownership")

    issued_branch_ids = set()
    for authority_id in authority_ids:
        try:
            binding = _C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY.get(authority_id)
            authority = (
                binding.child_reference()
                if type(binding) is _C0PreparedFrontierChildOwnerBinding
                and type(binding.child_reference) is weakref.ReferenceType
                else None
            )
            binding_owner = (
                binding.branch_batch_reference()
                if type(binding) is _C0PreparedFrontierChildOwnerBinding
                and type(binding.branch_batch_reference) is weakref.ReferenceType
                else None
            )
            if (
                type(binding) is not _C0PreparedFrontierChildOwnerBinding
                or type(binding.child_id) is not int
                or binding.child_id != authority_id
                or binding_owner is not branch_batch
                or type(binding.branch_batch_id) is not int
                or binding.branch_batch_id != branch_batch_id
                or type(binding.owner_pid) is not int
                or binding.owner_pid != os.getpid()
                or type(authority) is not _C0PreparedFrontierBranchAuthority
                or type(authority.branch_id) is not int
            ):
                note_integrity("authority owner drift")
            else:
                issued_branch_ids.add(authority.branch_id)
            registered_authority = _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.get(authority_id)
            if (
                type(registered_authority) is not _RegisteredC0PreparedFrontierBranchAuthority
                or type(registered_authority.reference) is not weakref.ReferenceType
                or registered_authority.reference() is not authority
                or registered_authority.branch_batch is not branch_batch
            ):
                note_integrity("authority record drift")
        except Exception as error:
            note_integrity(error)
    expected_branch_ids: set[int] | None = None
    for scope_id in scope_ids:
        try:
            binding = _C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY.get(scope_id)
            scope = (
                binding.child_reference()
                if type(binding) is _C0PreparedFrontierChildOwnerBinding
                and type(binding.child_reference) is weakref.ReferenceType
                else None
            )
            binding_owner = (
                binding.branch_batch_reference()
                if type(binding) is _C0PreparedFrontierChildOwnerBinding
                and type(binding.branch_batch_reference) is weakref.ReferenceType
                else None
            )
            if (
                type(binding) is not _C0PreparedFrontierChildOwnerBinding
                or type(binding.child_id) is not int
                or binding.child_id != scope_id
                or binding_owner is not branch_batch
                or type(binding.branch_batch_id) is not int
                or binding.branch_batch_id != branch_batch_id
                or type(binding.owner_pid) is not int
                or binding.owner_pid != os.getpid()
                or type(scope) is not _C0PreparedFrontierGeneratorScope
            ):
                note_integrity("scope owner drift")
            registered_scope = _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.get(scope_id)
            if (
                type(registered_scope) is not _RegisteredC0PreparedFrontierGeneratorScope
                or type(registered_scope.reference) is not weakref.ReferenceType
                or registered_scope.reference() is not scope
                or registered_scope.branch_batch is not branch_batch
                or type(registered_scope.branch_ids) is not tuple
                or not all(type(branch_id) is int for branch_id in registered_scope.branch_ids)
            ):
                note_integrity("scope record drift")
            elif expected_branch_ids is None:
                expected_branch_ids = set(registered_scope.branch_ids)
            else:
                note_integrity("multiple C0 scopes")
        except Exception as error:
            note_integrity(error)
    if lifecycle is not None:
        try:
            if (
                type(lifecycle.scope_issued) is not bool
                or type(lifecycle.consumed) is not bool
                or type(lifecycle.issued_branch_ids) is not set
                or lifecycle.consumed
                or lifecycle.scope_issued != (len(scope_ids) == 1)
                or lifecycle.issued_branch_ids != issued_branch_ids
                or (
                    lifecycle.scope_issued
                    and expected_branch_ids is not None
                    and issued_branch_ids != expected_branch_ids
                )
            ):
                note_integrity("C0 child lifecycle drift")
        except Exception as error:
            note_integrity(error)

    for authority_id in authority_ids:
        for registry in (
            _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY,
            _C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY,
        ):
            for _attempt in range(2):
                try:
                    registry.pop(authority_id, None)
                    break
                except Exception as error:
                    note_integrity(error)
    for scope_id in scope_ids:
        for registry in (
            _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY,
            _C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY,
        ):
            for _attempt in range(2):
                try:
                    registry.pop(scope_id, None)
                    break
                except Exception as error:
                    note_integrity(error)
    for _attempt in range(2):
        try:
            _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.pop(branch_batch_id, None)
            break
        except Exception as error:
            note_integrity(error)

    if owner_type_ok:
        for field_name in (
            "branch_authority_references",
            "generator_scope_references",
        ):
            ledger = getattr(owner_record, field_name, None)
            try:
                if type(ledger) is not dict:
                    raise TypeError("malformed C0 child ledger")
                ledger.clear()
            except Exception as error:
                note_integrity(error)
                object.__setattr__(owner_record, field_name, {})
        if lifecycle is not None:
            try:
                lifecycle.issued_branch_ids.clear()
                lifecycle.consumed = True
            except Exception as error:
                note_integrity(error)
                lifecycle.issued_branch_ids = set()
    if integrity_error is not None:
        raise integrity_error


def _issue_c0_prepared_frontier_branch_authority(
    *,
    source_scope: _C0PreparedFrontierGeneratorScope | None = None,
    source_branch: object,
    branch_id: int | None = None,
    event_position: int | None = None,
    common_before: M7ReplayCursor | None = None,
) -> _C0PreparedFrontierBranchAuthority:
    """Derive one opaque C0 authority from an exact live generator branch."""

    try:
        if (
            source_scope is None
            or branch_id is not None
            or event_position is not None
            or common_before is not None
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: generator scope required"
            )
        scope, branch_batch_record = _require_c0_prepared_frontier_generator_scope_registration(
            source_scope
        )
        source_position = scope.source_positions_by_identity.get(id(source_branch))
        if (
            type(source_position) is not int
            or source_position < 0
            or source_position >= len(scope.source_branches)
            or scope.source_branches[source_position] is not source_branch
            or source_position >= len(scope.branch_ids)
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch outside generator scope"
            )
        branch_id = scope.branch_ids[source_position]
        if (
            not branch_batch_record.lifecycle.scope_issued
            or branch_batch_record.lifecycle.consumed
            or len(branch_batch_record.branch_authority_references) >= len(scope.source_branches)
            or branch_id in branch_batch_record.lifecycle.issued_branch_ids
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch authority lifecycle"
            )
        event_position = scope.common_before.next_event_position
        common_before = scope.common_before
        descriptor = source_branch.descriptor
        root_step = source_branch.initial_step
        branch_before = source_branch.cursor
    except AttributeError as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: branch source"
        ) from error
    authority = _C0PreparedFrontierBranchAuthority(
        branch_id=branch_id,
        event_position=event_position,
        catalog_action_id=descriptor.action_id,
        root_action_id=root_step.event.action.action_id,
        root_step=root_step,
        common_before=common_before,
        branch_before=branch_before,
        _branch_batch=scope.branch_batch,
        _token=_C0_BRANCH_AUTHORITY_ISSUER,
    )
    key = id(authority)

    def discard(
        reference: weakref.ReferenceType[_C0PreparedFrontierBranchAuthority],
    ) -> None:
        registered = _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.get(key)
        released = (
            type(registered) is _RegisteredC0PreparedFrontierBranchAuthority
            and registered.reference is reference
        )
        if released:
            _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.pop(key, None)
        if released and branch_batch_record.branch_authority_references.get(key) is reference:
            branch_batch_record.branch_authority_references.pop(key, None)
        if released:
            _discard_c0_prepared_frontier_child_owner(
                key,
                branch_batch_id=id(scope.branch_batch),
                owner_registry=_C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY,
                is_scope=False,
            )

    reference = weakref.ref(authority, discard)
    registered_authority = _RegisteredC0PreparedFrontierBranchAuthority(
        reference=reference,
        owner_pid=os.getpid(),
        token=_C0_BRANCH_AUTHORITY_ISSUER,
        branch_batch=scope.branch_batch,
        source_scope=source_scope,
        source_branch=source_branch,
        descriptor=descriptor,
        root_step=root_step,
        branch_before=branch_before,
        common_before=common_before,
        content_sha256=_c0_prepared_frontier_branch_authority_sha256(authority),
    )
    if (
        key in _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY
        or key in branch_batch_record.branch_authority_references
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: duplicate branch authority"
        )
    _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[key] = registered_authority
    branch_batch_record.branch_authority_references[key] = reference
    try:
        _bind_c0_prepared_frontier_child_owner(
            authority,
            branch_batch=scope.branch_batch,
            owner_registry=_C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY,
            is_scope=False,
        )
    except Exception:
        _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.pop(key, None)
        branch_batch_record.branch_authority_references.pop(key, None)
        raise
    branch_batch_record.lifecycle.issued_branch_ids.add(branch_id)
    return authority


def _require_c0_prepared_frontier_branch_authority_registration(
    authority: _C0PreparedFrontierBranchAuthority,
) -> tuple[
    _RegisteredC0PreparedFrontierBranchAuthority,
    _RegisteredC0PreparedFrontierGeneratorScope,
]:
    """Validate producer ownership without requiring a consumer binding."""

    try:
        registered = _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.get(id(authority))
        _require_c0_prepared_frontier_child_owner(
            authority,
            branch_batch=authority._branch_batch,  # noqa: SLF001
            owner_registry=_C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY,
            is_scope=False,
        )
        if registered is None:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch source authority"
            )
        scope = _require_c0_prepared_frontier_generator_scope(registered.source_scope)
        source_position = scope.source_positions_by_identity.get(id(registered.source_branch))
        branch_batch_record = scope.branch_batch._owner_record  # noqa: SLF001
        if type(source_position) is not int:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch source authority"
            )
        registered = _require_c0_prepared_frontier_branch_authority_registration_shallow(
            authority,
            branch_batch_record=branch_batch_record,
            source_scope=registered.source_scope,
            scope_record=scope,
            expected_position=source_position,
        )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: branch source authority"
        ) from error
    return registered, scope


def _require_c0_prepared_frontier_branch_authority_registration_shallow(
    authority: _C0PreparedFrontierBranchAuthority,
    *,
    branch_batch_record: object,
    source_scope: _C0PreparedFrontierGeneratorScope,
    scope_record: _RegisteredC0PreparedFrontierGeneratorScope,
    expected_position: int,
) -> _RegisteredC0PreparedFrontierBranchAuthority:
    """Validate one authority against a fully validated scope in O(1)."""

    try:
        registered = _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.get(id(authority))
        _require_c0_prepared_frontier_child_owner(
            authority,
            branch_batch=authority._branch_batch,  # noqa: SLF001
            owner_registry=_C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY,
            is_scope=False,
        )
        if (
            type(authority) is not _C0PreparedFrontierBranchAuthority
            or type(registered) is not _RegisteredC0PreparedFrontierBranchAuthority
            or type(expected_position) is not int
            or expected_position < 0
            or expected_position >= len(scope_record.source_branches)
            or expected_position >= len(scope_record.branch_ids)
            or _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.get(id(source_scope))
            is not scope_record
            or source_scope._record is not scope_record  # noqa: SLF001
            or type(scope_record.reference) is not weakref.ReferenceType
            or scope_record.reference() is not source_scope
            or scope_record.branch_batch is not registered.branch_batch
            or type(branch_batch_record.reference) is not weakref.ReferenceType
            or scope_record.branch_batch is not branch_batch_record.reference()
            or type(registered.reference) is not weakref.ReferenceType
            or registered.reference() is not authority
            or registered.owner_pid != os.getpid()
            or registered.token is not authority._token  # noqa: SLF001
            or authority._branch_batch is not registered.branch_batch  # noqa: SLF001
            or branch_batch_record.branch_authority_references.get(id(authority))
            is not registered.reference
            or registered.source_scope is not source_scope
            or registered.source_branch is not scope_record.source_branches[expected_position]
            or scope_record.source_positions_by_identity.get(id(registered.source_branch))
            != expected_position
            or scope_record.branch_ids[expected_position] != authority.branch_id
            or scope_record.common_before is not authority.common_before
            or registered.source_branch.descriptor is not registered.descriptor
            or registered.source_branch.initial_step is not registered.root_step
            or registered.source_branch.cursor is not registered.branch_before
            or authority.root_step is not registered.root_step
            or authority.branch_before is not registered.branch_before
            or authority.common_before is not registered.common_before
            or registered.content_sha256 != _c0_prepared_frontier_branch_authority_sha256(authority)
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch source authority"
            )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: branch source authority"
        ) from error
    return registered


def _require_c0_prepared_frontier_branch_authority(
    authority: _C0PreparedFrontierBranchAuthority,
    *,
    runtime: M7ReplayRuntime,
    prepared_layouts: _PreparedTranslationLayoutBatch,
) -> None:
    registered, _issued_scope = _require_c0_prepared_frontier_branch_authority_registration(
        authority
    )
    scope = _bind_c0_prepared_frontier_generator_scope(
        registered.source_scope,
        runtime=runtime,
        prepared_layouts=prepared_layouts,
    )
    if (
        scope.consumer_runtime is not runtime
        or scope.consumer_prepared_layouts is not prepared_layouts
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: branch source authority"
        )


@dataclass(frozen=True, slots=True)
class _C0BranchRemnantRow:
    """One immutable branch/remnant input to the prepared scalar batch."""

    branch_id: int
    direction: Literal["added", "removed"]
    item: InventoryItem

    def __post_init__(self) -> None:
        if type(self.branch_id) is not int or self.branch_id < 0:
            raise ValueError("M8 C0 branch id must be a nonnegative exact integer")
        if self.direction not in ("added", "removed"):
            raise ValueError("M8 C0 branch direction is unsupported")
        if type(self.item) is not InventoryItem:
            raise TypeError("M8 C0 branch row requires an exact inventory item")


@dataclass(frozen=True, slots=True)
class _C0PreparedFrontierRowBinding:
    """Frozen semantic evidence behind one dense C0 numeric query."""

    row_id: int
    branch_id: int
    event_position: int
    catalog_action_id: str
    root_action_id: str
    branch_before_sha256: str
    common_before_sha256: str
    direction: Literal["added", "removed"]
    item: InventoryItem
    delta: BranchInventoryDelta
    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    candidate_ids: tuple[str, ...]
    rejection_layout_candidate_ids: tuple[str, ...]
    rejection_layout_sha256s: tuple[str, ...]
    retained_candidate_ids: tuple[str, ...]
    fit_config_sha256: str
    partition_sha256: str
    event_material_key: tuple[str, str, str, str, str]
    material_matches: bool
    measurement: PreparedTranslationRejectionRemnant
    measurement_sha256: str
    remnant_area: float
    remnant_width: float
    remnant_height: float
    area_tolerance: float
    coordinate_tolerance: float


@dataclass(frozen=True, slots=True)
class _C0PreparedFrontierRowResult:
    """Fail-closed semantic disposition for one branch/remnant row."""

    binding: _C0PreparedFrontierRowBinding
    supported: bool
    all_impossible: bool

    def __post_init__(self) -> None:
        if type(self.supported) is not bool or type(self.all_impossible) is not bool:
            raise TypeError("M8 C0 row disposition must use exact booleans")
        if not self.supported and self.all_impossible:
            raise ValueError("M8 C0 unsupported rows cannot prove impossibility")


@dataclass(frozen=True, slots=True)
class _C0PreparedFrontierBranchResult:
    """Aggregate eligibility for every changed item in one branch."""

    branch_id: int
    event_position: int
    catalog_action_id: str
    root_action_id: str
    branch_before_sha256: str
    common_before_sha256: str
    delta: BranchInventoryDelta
    row_ids: tuple[int, ...]
    supported: bool
    compact_eligible: bool

    def __post_init__(self) -> None:
        if not self.row_ids:
            raise ValueError("M8 C0 branch result must contain at least one row")
        if type(self.supported) is not bool or type(self.compact_eligible) is not bool:
            raise TypeError("M8 C0 branch disposition must use exact booleans")
        if not self.supported and self.compact_eligible:
            raise ValueError("M8 C0 unsupported branches cannot be compact eligible")


@dataclass(frozen=True, slots=True)
class _C0PreparedFrontierBatchResult:
    """Fully validated scalar rows and their branch-level dispositions."""

    rows: tuple[_C0PreparedFrontierRowResult, ...]
    branches: tuple[_C0PreparedFrontierBranchResult, ...]

    def __post_init__(self) -> None:
        if tuple(item.binding.row_id for item in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("M8 C0 semantic row ids must be dense input order")
        expected_branch_ids = tuple(dict.fromkeys(item.binding.branch_id for item in self.rows))
        if tuple(item.branch_id for item in self.branches) != expected_branch_ids:
            raise ValueError("M8 C0 branch result order differs from semantic rows")


def _c0_frontier_partition_supported(inputs) -> bool:  # type: ignore[no-untyped-def]
    """Return soft unsupported only for an explicitly absent scalar archive."""

    try:
        candidate_ids = inputs.candidate_ids
        layout_ids = inputs.rejection_layout_candidate_ids
        layout_hashes = inputs.rejection_layout_sha256s
        if (
            type(candidate_ids) is not tuple
            or not candidate_ids
            or any(type(item) is not str or not item for item in candidate_ids)
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: candidate partition"
            )
        if (
            type(layout_ids) is not tuple
            or type(layout_hashes) is not tuple
            or len(layout_ids) != len(layout_hashes)
            or len(layout_ids) != len(set(layout_ids))
            or any(
                type(value) is not str or not value.startswith("sha256:") or len(value) != 71
                for value in layout_hashes
            )
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: rejection partition"
            )
        problem = inputs.problem
        if problem is None:
            candidate_position = {
                candidate_id: position for position, candidate_id in enumerate(candidate_ids)
            }
            retained_positions = tuple(
                candidate_position.get(candidate_id, -1) for candidate_id in layout_ids
            )
            if (
                layout_ids == candidate_ids
                or any(position < 0 for position in retained_positions)
                or retained_positions != tuple(sorted(retained_positions))
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: incomplete rejection archive"
                )
            return False
        if layout_ids != candidate_ids:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: rejection membership"
            )
        if type(problem) is not CompiledRejectionProblem or (
            problem.problem_id != inputs.problem_id
            or problem.problem_sha256 != inputs.problem_sha256
            or problem.candidate_set_id != inputs.candidate_set_id
            or problem.candidate_set_sha256 != inputs.candidate_set_sha256
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: problem binding"
            )
        frontier = problem.frontier
        if type(frontier) is not ParetoFrontier:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: frontier type"
            )
        canonical = ParetoFrontier(
            members=frontier.members,
            retained=frontier.retained,
            dominated_by=frontier.dominated_by,
        )
        if canonical != frontier or not frontier.members or not frontier.retained:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: frontier classification"
            )
        member_ids = tuple(item.candidate_id for item in frontier.members)
        if member_ids != tuple(sorted(candidate_ids)):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: frontier membership"
            )
        if any(
            item.problem_id != inputs.problem_id
            or item.problem_sha256 != inputs.problem_sha256
            or item.candidate_set_id != inputs.candidate_set_id
            or item.candidate_set_sha256 != inputs.candidate_set_sha256
            or item.material_partition != "temporal_event"
            or item.fit_config_sha256 != inputs.fit_config_sha256
            for item in frontier.members
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: frontier partition"
            )
        return True
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: malformed partition"
        ) from error


def _validated_c0_branch_authority(
    *,
    transaction: _ValidatedC0PreparedFrontierEventTransaction,
    runtime: M7ReplayRuntime,
    prepared_layouts: _PreparedTranslationLayoutBatch,
    event_position: int,
    branches: tuple[_C0PreparedFrontierBranchAuthority, ...],
    rows: tuple[_C0BranchRemnantRow, ...],
) -> tuple[tuple[_C0PreparedFrontierBranchAuthority, BranchInventoryDelta], ...]:
    """Require an exact authority-to-delta-to-row bijection before numeric work."""

    try:
        if type(branches) is not tuple or any(
            type(item) is not _C0PreparedFrontierBranchAuthority for item in branches
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch authority type"
            )
        if len(branches) != len(transaction.authorities) or any(
            authority is not expected
            for authority, expected in zip(
                branches,
                transaction.authorities,
                strict=True,
            )
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event transaction ownership"
            )
        prepared_layouts.require_active(runtime)
        scope_record = _bind_c0_prepared_frontier_generator_scope_registration(
            transaction.source_scope,
            registered=transaction.scope_record,
            runtime=runtime,
            prepared_layouts=prepared_layouts,
        )
        for expected_position, (authority, authority_record) in enumerate(
            zip(branches, transaction.authority_records, strict=True)
        ):
            if (
                _require_c0_prepared_frontier_branch_authority_registration_shallow(
                    authority,
                    branch_batch_record=transaction.owner_record,
                    source_scope=transaction.source_scope,
                    scope_record=scope_record,
                    expected_position=expected_position,
                )
                is not authority_record
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: branch source authority"
                )
        if type(rows) is not tuple or any(type(row) is not _C0BranchRemnantRow for row in rows):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch row type"
            )
        if not branches and not rows:
            return ()
        branch_ids = tuple(item.branch_id for item in branches)
        if (
            not branches
            or branch_ids != tuple(sorted(set(branch_ids)))
            or tuple(dict.fromkeys(row.branch_id for row in rows)) != branch_ids
            or any(item.event_position != event_position for item in branches)
            or len({item.catalog_action_id for item in branches}) != len(branches)
            or len({item.root_action_id for item in branches}) != len(branches)
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch authority ordering"
            )
        common_hashes = tuple(m7_cursor_sha256(item.common_before) for item in branches)
        if len(set(common_hashes)) != 1:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: common cursor binding"
            )
        validated = []
        expected_rows = []
        for authority in branches:
            delta = _derive_branch_inventory_delta(
                authority.common_before,
                authority.branch_before,
            )
            if not delta.added and not delta.removed:
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: empty branch delta"
                )
            validated.append((authority, delta))
            expected_rows.extend(
                _C0BranchRemnantRow(
                    branch_id=authority.branch_id,
                    direction=direction,
                    item=item,
                )
                for direction, items in (("added", delta.added), ("removed", delta.removed))
                for item in items
            )
        if tuple(expected_rows) != rows:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: exact inventory delta rows"
            )
        return tuple(validated)
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: malformed branch authority"
        ) from error


def _require_c0_prepared_frontier_event_transaction_children(
    branch_batch: object,
    owner_record: object,
) -> _ValidatedC0PreparedFrontierEventTransaction:
    """Require one exact indexed parent-to-child graph without sibling scans."""

    try:
        from yieldforge.oracle import sparse

        if (
            type(owner_record) is not sparse._RegisteredM8PreparedC0BranchBatch  # noqa: SLF001
            or type(owner_record.reference) is not weakref.ReferenceType
            or owner_record.reference() is not branch_batch
            or branch_batch._owner_record is not owner_record  # noqa: SLF001
            or sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY.get(id(branch_batch))  # noqa: SLF001
            is not owner_record
            or not owner_record.lifecycle.scope_issued
            or owner_record.lifecycle.consumed
            or type(owner_record.generator_scope_references) is not dict
            or type(owner_record.branch_authority_references) is not dict
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event transaction owner"
            )
        scope_ledger_ids = tuple(owner_record.generator_scope_references)
        authority_ledger_ids = tuple(owner_record.branch_authority_references)
        index = _C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY.get(id(branch_batch))
        if (
            type(index) is not _C0PreparedFrontierBatchChildIndex
            or type(index.branch_batch_reference) is not weakref.ReferenceType
            or index.branch_batch_reference() is not branch_batch
            or index.branch_batch_id != id(branch_batch)
            or index.owner_pid != os.getpid()
            or type(index.scope_ids) is not dict
            or type(index.authority_ids) is not dict
            or any(
                type(child_id) is not int or value is not None
                for child_id, value in index.scope_ids.items()
            )
            or any(
                type(child_id) is not int or value is not None
                for child_id, value in index.authority_ids.items()
            )
            or len(index.scope_ids) != 1
            or any(type(child_id) is not int for child_id in scope_ledger_ids)
            or any(type(child_id) is not int for child_id in authority_ledger_ids)
            or set(scope_ledger_ids) != set(index.scope_ids)
            or set(authority_ledger_ids) != set(index.authority_ids)
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event transaction coverage"
            )
        scope_id = next(iter(index.scope_ids))
        scope_reference = owner_record.generator_scope_references[scope_id]
        if type(scope_reference) is not weakref.ReferenceType:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event scope reference"
            )
        source_scope = scope_reference()
        scope_record = _C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.get(scope_id)
        if (
            type(source_scope) is not _C0PreparedFrontierGeneratorScope
            or type(scope_record) is not _RegisteredC0PreparedFrontierGeneratorScope
            or scope_record.reference is not scope_reference
            or scope_record.branch_batch is not branch_batch
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event scope coverage"
            )
        _require_c0_prepared_frontier_child_owner(
            source_scope,
            branch_batch=branch_batch,
            owner_registry=_C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY,
            is_scope=True,
        )
        scope_record = _require_c0_prepared_frontier_generator_scope(source_scope)
        validated_authorities = []
        for expected_position, authority_id in enumerate(index.authority_ids):
            reference = owner_record.branch_authority_references[authority_id]
            if type(reference) is not weakref.ReferenceType:
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: event branch reference"
                )
            authority = reference()
            registered = _C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.get(authority_id)
            if (
                type(authority) is not _C0PreparedFrontierBranchAuthority
                or authority._branch_batch is not branch_batch  # noqa: SLF001
                or type(registered) is not _RegisteredC0PreparedFrontierBranchAuthority
                or registered.reference is not reference
                or registered.branch_batch is not branch_batch
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: event branch ownership"
                )
            _require_c0_prepared_frontier_child_owner(
                authority,
                branch_batch=branch_batch,
                owner_registry=_C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY,
                is_scope=False,
            )
            authority_record = _require_c0_prepared_frontier_branch_authority_registration_shallow(
                authority,
                branch_batch_record=owner_record,
                source_scope=source_scope,
                scope_record=scope_record,
                expected_position=expected_position,
            )
            validated_authorities.append((authority, authority_record))
        if (
            len(validated_authorities) != len(scope_record.source_branches)
            or tuple(authority.branch_id for authority, _record in validated_authorities)
            != scope_record.branch_ids
            or tuple(
                authority_record.source_branch
                for _authority, authority_record in validated_authorities
            )
            != scope_record.source_branches
            or owner_record.lifecycle.issued_branch_ids != set(scope_record.branch_ids)
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event branch coverage"
            )
        return _ValidatedC0PreparedFrontierEventTransaction(
            branch_batch=branch_batch,
            owner_record=owner_record,
            source_scope=source_scope,
            scope_record=scope_record,
            authorities=tuple(authority for authority, _record in validated_authorities),
            authority_records=tuple(record for _authority, record in validated_authorities),
        )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event transaction children"
        ) from error


@contextmanager
def _consume_c0_prepared_frontier_branch_authorities(
    branch_batch: object,
    branches: tuple[_C0PreparedFrontierBranchAuthority, ...],
) -> Iterator[_ValidatedC0PreparedFrontierEventTransaction]:
    """Consume all exact event transactions referenced by one certification call."""

    from yieldforge.oracle import sparse

    if type(branches) is not tuple or any(
        type(authority) is not _C0PreparedFrontierBranchAuthority
        or authority._branch_batch is not branch_batch  # noqa: SLF001
        for authority in branches
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event transaction authority"
        )
    try:
        owner_record = sparse._require_prepared_c0_branch_batch(branch_batch)  # noqa: SLF001
    except M8PreparedFrontierIntegrityError:
        recovered_owner = getattr(branch_batch, "_owner_record", None)
        try:
            _release_c0_prepared_frontier_generator_scopes(
                branch_batch,
                owner_record=recovered_owner,
            )
        except M8PreparedFrontierIntegrityError:
            pass
        raise
    body_error: BaseException | None = None
    try:
        transaction = _require_c0_prepared_frontier_event_transaction_children(
            branch_batch,
            owner_record,
        )
        if branches != transaction.authorities:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: event transaction ownership"
            )
        yield transaction
    except BaseException as error:
        body_error = error
        raise
    finally:
        integrity_error: M8PreparedFrontierIntegrityError | None = None
        try:
            _release_c0_prepared_frontier_generator_scopes(
                branch_batch,
                owner_record=owner_record,
            )
        except M8PreparedFrontierIntegrityError as error:
            integrity_error = error
        if integrity_error is not None and not isinstance(
            body_error,
            M8PreparedFrontierIntegrityError,
        ):
            raise integrity_error


def _certify_prepared_frontier_batch(
    runtime: M7ReplayRuntime,
    *,
    branch_batch: object,
    prepared_layouts: _PreparedTranslationLayoutBatch,
    event_position: int,
    branches: tuple[_C0PreparedFrontierBranchAuthority, ...],
    rows: tuple[_C0BranchRemnantRow, ...],
) -> _C0PreparedFrontierBatchResult:
    """Consume one exact C0 event transaction around all numeric work."""

    captured_rows = _capture_c0_branch_remnant_rows(rows)
    with _consume_c0_prepared_frontier_branch_authorities(
        branch_batch,
        branches,
    ) as transaction:
        return _certify_prepared_frontier_batch_transaction(
            runtime,
            transaction=transaction,
            prepared_layouts=prepared_layouts,
            event_position=event_position,
            branches=branches,
            rows=captured_rows,
        )


def _certify_prepared_frontier_batch_transaction(
    runtime: M7ReplayRuntime,
    *,
    transaction: _ValidatedC0PreparedFrontierEventTransaction,
    prepared_layouts: _PreparedTranslationLayoutBatch,
    event_position: int,
    branches: tuple[_C0PreparedFrontierBranchAuthority, ...],
    rows: tuple[_C0BranchRemnantRow, ...],
) -> _C0PreparedFrontierBatchResult:
    """Differentially certify a complete prepared frontier batch without branch mutation."""

    validated_branches = _validated_c0_branch_authority(
        transaction=transaction,
        runtime=runtime,
        prepared_layouts=prepared_layouts,
        event_position=event_position,
        branches=branches,
        rows=rows,
    )
    if not rows:
        return _C0PreparedFrontierBatchResult(rows=(), branches=())

    with profile_phase("frontier_columnar_batch"):
        try:
            issued_inputs = _prepared_frontier_batch_inputs(
                prepared_layouts,
                runtime,
                event_position=event_position,
                remnants=tuple(row.item.remnant for row in rows),
            )
        except M8PreparedFrontierIntegrityError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: prepared capability"
            ) from error
        try:
            inputs = _require_prepared_frontier_batch_inputs(
                issued_inputs,
                prepared=prepared_layouts,
                runtime=runtime,
                event_position=event_position,
            )
        except M8PreparedFrontierIntegrityError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: prepared input commitment"
            ) from error
        supported = _c0_frontier_partition_supported(inputs)
        retained = inputs.problem.frontier.retained if supported and inputs.problem else ()
        if (
            type(inputs.measurements) is not tuple
            or len(inputs.measurements) != len(rows)
            or any(
                type(item) is not PreparedTranslationRejectionRemnant
                for item in inputs.measurements
            )
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: remnant measurements"
            )
        partition_sha256 = "sha256:" + semantic_sha256(
            {
                "schema_version": "yieldforge.m8-c0-frontier-partition.v1",
                "problem_id": inputs.problem_id,
                "problem_sha256": inputs.problem_sha256,
                "candidate_set_id": inputs.candidate_set_id,
                "candidate_set_sha256": inputs.candidate_set_sha256,
                "candidate_ids": inputs.candidate_ids,
                "rejection_layout_candidate_ids": inputs.rejection_layout_candidate_ids,
                "rejection_layout_sha256s": inputs.rejection_layout_sha256s,
                "fit_config_sha256": inputs.fit_config_sha256,
                "event_material_key": inputs.event_material_key,
                "compiled_problem": asdict(inputs.problem) if inputs.problem is not None else None,
            }
        )
        authority_by_id = {
            authority.branch_id: (authority, delta) for authority, delta in validated_branches
        }
        bindings = []
        queries = []
        for row_id, (row, measured) in enumerate(zip(rows, inputs.measurements, strict=True)):
            authority, delta = authority_by_id[row.branch_id]
            if (
                measured.remnant_id != row.item.remnant.remnant_id
                or measured.material_key != material_key(row.item.remnant.material)
                or measured.area != row.item.remnant.geometry.area
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: remnant measurement binding"
                )
            min_x, min_y, max_x, max_y = measured.bounds
            remnant_width = float(max_x - min_x)
            remnant_height = float(max_y - min_y)
            coordinate_tolerance = float(inputs.fit_config.coordinate_tolerance)
            area_tolerance = max(
                coordinate_tolerance,
                float(measured.area * inputs.fit_config.relative_area_tolerance),
            )
            material_matches = measured.material_key == inputs.event_material_key
            measurement_sha256 = "sha256:" + semantic_sha256(
                {
                    "schema_version": "yieldforge.m8-c0-remnant-measurement.v1",
                    "item": InventoryItem.model_dump(
                        row.item,
                        mode="json",
                        warnings=False,
                    ),
                    "measurement": asdict(measured),
                }
            )
            binding = _C0PreparedFrontierRowBinding(
                row_id=row_id,
                branch_id=row.branch_id,
                event_position=authority.event_position,
                catalog_action_id=authority.catalog_action_id,
                root_action_id=authority.root_action_id,
                branch_before_sha256=m7_cursor_sha256(authority.branch_before),
                common_before_sha256=m7_cursor_sha256(authority.common_before),
                direction=row.direction,
                item=row.item,
                delta=delta,
                problem_id=inputs.problem_id,
                problem_sha256=inputs.problem_sha256,
                candidate_set_id=inputs.candidate_set_id,
                candidate_set_sha256=inputs.candidate_set_sha256,
                candidate_ids=inputs.candidate_ids,
                rejection_layout_candidate_ids=inputs.rejection_layout_candidate_ids,
                rejection_layout_sha256s=inputs.rejection_layout_sha256s,
                retained_candidate_ids=tuple(item.candidate_id for item in retained),
                fit_config_sha256=inputs.fit_config_sha256,
                partition_sha256=partition_sha256,
                event_material_key=inputs.event_material_key,
                material_matches=material_matches,
                measurement=measured,
                measurement_sha256=measurement_sha256,
                remnant_area=float(measured.area),
                remnant_width=remnant_width,
                remnant_height=remnant_height,
                area_tolerance=area_tolerance,
                coordinate_tolerance=coordinate_tolerance,
            )
            bindings.append(binding)
            if supported:
                queries.append(
                    C0FrontierQuery(
                        row_id=row_id,
                        material_matches=material_matches,
                        remnant_area=binding.remnant_area,
                        remnant_width=remnant_width,
                        remnant_height=remnant_height,
                        area_tolerance=area_tolerance,
                        coordinate_tolerance=coordinate_tolerance,
                    )
                )

        if supported:
            problem = inputs.problem
            if problem is None:  # pragma: no cover - support predicate closes this branch.
                raise AssertionError("M8 C0 supported partition lacks a rejection problem")
            columns = C0FrontierColumns(
                areas=tuple(float(item.area) for item in retained),
                widths=tuple(float(item.width) for item in retained),
                heights=tuple(float(item.height) for item in retained),
            )
            kernel_queries = tuple(queries)
            columns_snapshot = asdict(columns)
            query_snapshot = tuple(asdict(query) for query in kernel_queries)
            kernel_results = certify_frontier_impossible_batch(columns, kernel_queries)
            if (
                asdict(columns) != columns_snapshot
                or tuple(asdict(query) for query in kernel_queries) != query_snapshot
                or type(kernel_results) is not tuple
                or len(kernel_results) != len(queries)
                or any(type(item) is not C0FrontierResult for item in kernel_results)
                or tuple(item.row_id for item in kernel_results) != tuple(range(len(queries)))
                or any(type(item.all_impossible) is not bool for item in kernel_results)
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: "
                    "C0 frontier batch result integrity differs"
                )
            for binding, result in zip(bindings, kernel_results, strict=True):
                scalar_result = certify_frontier_impossible(
                    problem.frontier,
                    material_matches=binding.material_matches,
                    remnant_area=binding.remnant_area,
                    remnant_width=binding.remnant_width,
                    remnant_height=binding.remnant_height,
                    area_tolerance=binding.area_tolerance,
                    coordinate_tolerance=binding.coordinate_tolerance,
                )
                if result.all_impossible is not scalar_result:
                    raise M8PreparedFrontierIntegrityError(
                        "M8 prepared frontier integrity differs: "
                        "C0 frontier batch result integrity differs"
                    )
            row_results = tuple(
                _C0PreparedFrontierRowResult(
                    binding=binding,
                    supported=True,
                    all_impossible=result.all_impossible,
                )
                for binding, result in zip(bindings, kernel_results, strict=True)
            )
        else:
            row_results = tuple(
                _C0PreparedFrontierRowResult(
                    binding=binding,
                    supported=False,
                    all_impossible=False,
                )
                for binding in bindings
            )

        rows_by_branch: dict[int, list[_C0PreparedFrontierRowResult]] = {}
        for item in row_results:
            rows_by_branch.setdefault(item.binding.branch_id, []).append(item)
        branch_results = []
        for branch_id, grouped_rows in rows_by_branch.items():
            branch_rows = tuple(grouped_rows)
            branch_supported = all(item.supported for item in branch_rows)
            authority, delta = authority_by_id[branch_id]
            branch_results.append(
                _C0PreparedFrontierBranchResult(
                    branch_id=branch_id,
                    event_position=authority.event_position,
                    catalog_action_id=authority.catalog_action_id,
                    root_action_id=authority.root_action_id,
                    branch_before_sha256=m7_cursor_sha256(authority.branch_before),
                    common_before_sha256=m7_cursor_sha256(authority.common_before),
                    delta=delta,
                    row_ids=tuple(item.binding.row_id for item in branch_rows),
                    supported=branch_supported,
                    compact_eligible=branch_supported
                    and all(item.all_impossible for item in branch_rows),
                )
            )
        revalidated_transaction = _require_c0_prepared_frontier_event_transaction_children(
            transaction.branch_batch,
            transaction.owner_record,
        )
        revalidated_branches = _validated_c0_branch_authority(
            transaction=revalidated_transaction,
            runtime=runtime,
            prepared_layouts=prepared_layouts,
            event_position=event_position,
            branches=branches,
            rows=rows,
        )
        if revalidated_branches != validated_branches:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: branch authority changed"
            )
        _require_prepared_frontier_batch_inputs(
            issued_inputs,
            prepared=prepared_layouts,
            runtime=runtime,
            event_position=event_position,
        )
        return _C0PreparedFrontierBatchResult(
            rows=row_results,
            branches=tuple(branch_results),
        )


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


@dataclass(frozen=True)
class FastCommonRejectionWitness:
    """Scalar-only proof that one inventory item cannot generate a fit query."""

    remnant_id: str
    candidate_ids: tuple[str, ...]
    material_matches: bool
    remnant_area: float
    remnant_width: float
    remnant_height: float
    area_tolerance: float
    coordinate_tolerance: float
    impossible: bool
    zero_generation: bool

    def __post_init__(self) -> None:
        if not self.remnant_id or not self.candidate_ids:
            raise ValueError("M8 fast common rejection witness identities are incomplete")
        if self.candidate_ids != tuple(sorted(set(self.candidate_ids))):
            raise ValueError("M8 fast common rejection candidates must be sorted unique")
        if not self.impossible or not self.zero_generation:
            raise ValueError("M8 fast common witness must prove zero-generation rejection")


@dataclass(frozen=True)
class FastCommonTransitionResult:
    """Exact common fact plus scalar rejects and exact-search survivors."""

    fact: M8CommonTransitionFact
    zero_generation_rejected_inventory: tuple[InventoryItem, ...]
    counted_no_fit_inventory: tuple[InventoryItem, ...]
    exact_survivor_inventory: tuple[InventoryItem, ...]
    witnesses: tuple[FastCommonRejectionWitness, ...]

    def __post_init__(self) -> None:
        if len(self.zero_generation_rejected_inventory) != len(self.witnesses):
            raise ValueError("M8 fast common witness count differs from inventory")
        if tuple(
            item.remnant.remnant_id for item in self.zero_generation_rejected_inventory
        ) != tuple(item.remnant_id for item in self.witnesses):
            raise ValueError("M8 fast common witnesses differ from inventory order")
        rejected_ids = {item.remnant.remnant_id for item in self.zero_generation_rejected_inventory}
        counted_ids = {item.remnant.remnant_id for item in self.counted_no_fit_inventory}
        survivor_ids = {item.remnant.remnant_id for item in self.exact_survivor_inventory}
        if rejected_ids & counted_ids or rejected_ids & survivor_ids or counted_ids & survivor_ids:
            raise ValueError("M8 fast common inventory classifications overlap")
        cursor_ids = {item.remnant.remnant_id for item in self.fact.cursor_before.inventory}
        if rejected_ids | counted_ids | survivor_ids != cursor_ids:
            raise ValueError("M8 fast common classification does not cover inventory")


class _CommonDerivationMode(StrEnum):
    """Closed authority modes for common source derivation."""

    TRUSTED_LOCAL = "trusted_local"
    UNCHECKED_PORTABLE = "unchecked_portable"


@dataclass(frozen=True)
class _ScalarNoFitSource:
    """Source translation evidence retained before search-result reduction."""

    searches: tuple[LayoutFitSearchResult, ...] | None
    translation_batches: tuple[LayoutTranslationCandidates, ...]
    exact_replay_reason: Literal["unsupported_representation"] | None = None

    def __post_init__(self) -> None:
        if self.exact_replay_reason is None:
            if self.searches is None and self.translation_batches:
                raise ValueError("M8 frontier survivor cannot carry translation claims")
            if self.searches is not None and len(self.searches) != len(self.translation_batches):
                if self.searches or self.translation_batches:
                    raise ValueError("M8 counted source searches and translations differ")
        elif self.searches is not None or self.translation_batches:
            raise ValueError("M8 unsupported source cannot carry counted-no-fit claims")


@dataclass(frozen=True)
class M8UncheckedTranslationBatchCapture:
    """Ordered producer claim with every count and no collision classification."""

    source: LayoutTranslationCandidates
    candidate_id: str
    remnant_id: str
    translations: tuple[tuple[float, float], ...]
    generated_candidate_count: int
    duplicate_candidate_count: int
    evaluated_candidate_count: int
    budget_truncated: bool

    def __post_init__(self) -> None:
        if (
            self.candidate_id != self.source.candidate_id
            or self.remnant_id != self.source.remnant_id
            or self.translations != self.source.translations
            or self.generated_candidate_count != self.source.generated_candidate_count
            or self.duplicate_candidate_count != self.source.duplicate_candidate_count
            or self.evaluated_candidate_count != len(self.source.translations)
            or self.budget_truncated != self.source.budget_truncated
        ):
            raise ValueError("M8 unchecked translation capture differs from its source")

    @classmethod
    def from_source(
        cls,
        source: LayoutTranslationCandidates,
    ) -> M8UncheckedTranslationBatchCapture:
        return cls(
            source=source,
            candidate_id=source.candidate_id,
            remnant_id=source.remnant_id,
            translations=source.translations,
            generated_candidate_count=source.generated_candidate_count,
            duplicate_candidate_count=source.duplicate_candidate_count,
            evaluated_candidate_count=len(source.translations),
            budget_truncated=source.budget_truncated,
        )


@dataclass(frozen=True)
class M8UncheckedCommonInventoryCapture:
    """Explicitly unchecked source classification for one common inventory item."""

    item: InventoryItem
    classification: Literal["scalar_no_fit", "counted_no_fit", "exact_survivor"]
    material_matches: bool
    remnant_area: float
    remnant_width: float
    remnant_height: float
    area_tolerance: float
    coordinate_tolerance: float
    scalar_witness: FastCommonRejectionWitness | None
    candidate_rejection_layouts: tuple[VerifiedCandidateRejectionLayout, ...]
    frontier: ParetoFrontier | None
    translation_batches: tuple[M8UncheckedTranslationBatchCapture, ...]
    exact_replay_reason: (
        Literal[
            "frontier_survivor",
            "counted_search_survivor",
            "unsupported_representation",
        ]
        | None
    ) = None

    def __post_init__(self) -> None:
        if self.classification == "scalar_no_fit":
            valid = (
                self.scalar_witness is not None
                and bool(self.candidate_rejection_layouts)
                and self.frontier is not None
                and not self.translation_batches
                and self.exact_replay_reason is None
            )
        elif self.classification == "counted_no_fit":
            valid = (
                self.scalar_witness is None
                and bool(self.candidate_rejection_layouts)
                and self.frontier is not None
                and bool(self.translation_batches)
                and self.exact_replay_reason is None
            )
        else:
            evidence_shape = (
                self.frontier is not None,
                bool(self.candidate_rejection_layouts),
                bool(self.translation_batches),
            )
            expected_reason = {
                (True, True, False): "frontier_survivor",
                (True, True, True): "counted_search_survivor",
                (False, False, False): "unsupported_representation",
            }.get(evidence_shape)
            valid = (
                self.scalar_witness is None
                and expected_reason is not None
                and self.exact_replay_reason == expected_reason
            )
        if not valid:
            raise ValueError("M8 unchecked inventory classification evidence is inconsistent")
        layout_ids = tuple(item.candidate_id for item in self.candidate_rejection_layouts)
        frontier_ids = (
            tuple(item.candidate_id for item in self.frontier.members)
            if self.frontier is not None
            else ()
        )
        if layout_ids != frontier_ids:
            raise ValueError("M8 unchecked inventory scalar and frontier sources differ")
        if self.scalar_witness is not None and (
            self.scalar_witness.remnant_id != self.item.remnant.remnant_id
            or self.scalar_witness.candidate_ids != tuple(sorted(layout_ids))
            or self.scalar_witness.material_matches != self.material_matches
            or self.scalar_witness.remnant_area != self.remnant_area
            or self.scalar_witness.remnant_width != self.remnant_width
            or self.scalar_witness.remnant_height != self.remnant_height
            or self.scalar_witness.area_tolerance != self.area_tolerance
            or self.scalar_witness.coordinate_tolerance != self.coordinate_tolerance
        ):
            raise ValueError("M8 unchecked scalar witness differs from its source measurements")
        if any(
            batch.remnant_id != self.item.remnant.remnant_id for batch in self.translation_batches
        ) or (
            self.translation_batches
            and tuple(batch.candidate_id for batch in self.translation_batches) != layout_ids
        ):
            raise ValueError("M8 unchecked translation batches differ from inventory source")

    @property
    def remnant_id(self) -> str:
        return self.item.remnant.remnant_id


@dataclass(frozen=True)
class M8UncheckedStandardCandidateCapture:
    """Complete unchecked standard profile, policy context, and rank preimage."""

    profile_position: int
    descriptor: M7ActionDescriptor
    profile: M7StandardActionProfile
    context: ActionPolicyContext
    rank: PolicyRank
    policy_immediate_net_cost: float
    selected_replay_event_net_cost: float | None

    def __post_init__(self) -> None:
        if (
            self.profile_position < 0
            or self.descriptor.kind is not M7ActionKind.OPEN_STANDARD_SHEET
            or self.descriptor.candidate_id != self.profile.candidate_id
            or self.context.action_id != self.descriptor.action_id
            or self.context.candidate_id != self.profile.candidate_id
            or self.context.immediate_net_cost != self.policy_immediate_net_cost
            or self.rank != rank_policy_action(self.rank.policy, self.context)
        ):
            raise ValueError("M8 unchecked standard candidate source is inconsistent")


@dataclass(frozen=True)
class M8UncheckedCommonSourceCapture:
    """All frozen source bindings needed to assemble portable fact leaves later."""

    replay_input_id: str
    replay_input_sha256: str
    replay_input: M7ReplayInput
    semantic_runtime_sha256: str
    stream_id: str
    stream_sha256: str
    event_binding: TemporalInstanceBinding
    problem: ReusableGeometryProblem
    candidate_set: M7CandidateSetEvidence
    verified_candidates: VerifiedProblemCandidates
    fit_config: RemnantFitConfig
    fit_config_sha256: str
    search_config: LayoutFitSearchConfig
    search_config_sha256: str
    rules: ResidualRuleSet
    rules_sha256: str
    collision_backend: str
    jagua_executable_sha256: str | None
    jagua_executable_size_bytes: int | None
    jagua_executable_mode_bits: int | None

    def __post_init__(self) -> None:
        if (
            self.replay_input.input_id != self.replay_input_id
            or self.replay_input.content_sha256 != self.replay_input_sha256
            or self.replay_input.stream_id != self.stream_id
            or self.replay_input.stream_sha256 != self.stream_sha256
            or self.replay_input.fit_config != self.fit_config
            or self.replay_input.search_config != self.search_config
            or self.replay_input.collision_backend != self.collision_backend
            or self.problem not in self.replay_input.problems
            or self.candidate_set not in self.replay_input.candidate_sets
            or self.verified_candidates.evidence != self.candidate_set
            or self.event_binding not in self.replay_input.instances
        ):
            raise ValueError("M8 unchecked source bindings differ from the replay input")
        jagua_identity = (
            self.jagua_executable_sha256 is not None,
            self.jagua_executable_size_bytes is not None,
            self.jagua_executable_mode_bits is not None,
        )
        jagua_active = self.collision_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
        if jagua_identity not in {(False, False, False), (True, True, True)} or (
            jagua_active != all(jagua_identity)
        ):
            raise ValueError("M8 unchecked source Jagua binding is incomplete")
        if jagua_active and (
            self.jagua_executable_sha256 is None
            or not self.jagua_executable_sha256.startswith("sha256:")
            or self.jagua_executable_size_bytes is None
            or self.jagua_executable_size_bytes <= 0
            or self.jagua_executable_mode_bits is None
            or not self.jagua_executable_mode_bits & 0o111
        ):
            raise ValueError("M8 unchecked source Jagua executable identity is invalid")
        expected_fit = f"sha256:{semantic_sha256(self.fit_config.model_dump(mode='json'))}"
        expected_search = f"sha256:{semantic_sha256(self.search_config.model_dump(mode='json'))}"
        expected_rules = f"sha256:{semantic_sha256(self.rules.model_dump(mode='json'))}"
        if (
            self.fit_config_sha256 != expected_fit
            or self.search_config_sha256 != expected_search
            or self.rules_sha256 != expected_rules
        ):
            raise ValueError("M8 unchecked source semantic configuration hash differs")


@dataclass(frozen=True)
class M8UncheckedProducerTransition:
    """Producer-only common record; it carries no accepted proof authority."""

    common_fact: M8CommonTransitionFact
    portable_transition: portable_facts.M8PortableCommonTransitionV2
    inventory_classifications: tuple[M8UncheckedCommonInventoryCapture, ...]
    standard_candidates: tuple[M8UncheckedStandardCandidateCapture, ...]
    source: M8UncheckedCommonSourceCapture
    authority_mode: ClassVar[Literal["unchecked_portable"]] = "unchecked_portable"

    def __post_init__(self) -> None:
        if not 0 <= self.common_fact.event_position < len(self.source.replay_input.instances):
            raise ValueError("M8 unchecked common fact event position is outside its source")
        if (
            self.common_fact.replay_input_id != self.source.replay_input_id
            or self.common_fact.replay_input_sha256 != self.source.replay_input_sha256
            or self.common_fact.semantic_runtime_sha256 != self.source.semantic_runtime_sha256
            or self.source.event_binding
            != self.source.replay_input.instances[self.common_fact.event_position]
            or self.source.problem.problem_id != self.source.event_binding.problem_id
            or self.source.candidate_set.problem_id != self.source.problem.problem_id
        ):
            raise ValueError("M8 unchecked common fact source context differs")
        inventory_ids = tuple(item.remnant_id for item in self.inventory_classifications)
        expected_ids = tuple(
            item.remnant.remnant_id for item in self.common_fact.cursor_before.inventory
        )
        if inventory_ids != expected_ids:
            raise ValueError("M8 unchecked common capture does not cover source inventory")
        if tuple(item.item for item in self.inventory_classifications) != (
            self.common_fact.cursor_before.inventory
        ):
            raise ValueError("M8 unchecked inventory capture differs from source items")
        if (
            self.portable_transition.cursor_before_sha256 != self.common_fact.cursor_before_sha256
            or self.portable_transition.cursor_after_sha256 != self.common_fact.cursor_after_sha256
            or self.portable_transition.event_id != self.common_fact.event_id
            or self.portable_transition.event_position != self.common_fact.event_position
            or self.portable_transition.replay_input_id != self.source.replay_input_id
            or self.portable_transition.replay_input_sha256 != self.source.replay_input_sha256
            or self.portable_transition.semantic_runtime_sha256
            != self.source.semantic_runtime_sha256
        ):
            raise ValueError("M8 unchecked portable transition differs from legacy common fact")
        candidate_ids = tuple(item.profile.candidate_id for item in self.standard_candidates)
        if tuple(item.profile_position for item in self.standard_candidates) != tuple(
            range(len(self.standard_candidates))
        ) or candidate_ids != tuple(
            item.candidate_id for item in self.source.verified_candidates.candidates
        ):
            raise ValueError("M8 unchecked standard capture is incomplete or out of order")
        selected_standard = tuple(
            item
            for item in self.standard_candidates
            if item.descriptor.action_id == self.common_fact.step.descriptor.action_id
        )
        if self.common_fact.step.descriptor.kind is M7ActionKind.OPEN_STANDARD_SHEET:
            if (
                len(selected_standard) != 1
                or selected_standard[0].rank != self.common_fact.policy_rank
                or selected_standard[0].selected_replay_event_net_cost
                != self.common_fact.step.event.delta_costs.net_cost
            ):
                raise ValueError("M8 unchecked selected standard source differs")
        elif selected_standard or any(
            item.selected_replay_event_net_cost is not None for item in self.standard_candidates
        ):
            raise ValueError("M8 unchecked nonselected standard carries replay event cost")


@dataclass(frozen=True)
class M8UncheckedInfluenceCapture:
    """Full typed influence preimage retained before the legacy digest reduction."""

    event_position: int
    item: InventoryItem
    direction: Literal["added", "removed"]
    delta: BranchInventoryDelta
    common: M7PolicyActionBinding
    common_rank: PolicyRank
    common_fact_sha256: str
    common_action_id: str
    branch_action_id: str
    state_before_sha256: str
    state_after_sha256: str
    rejections: tuple[CompiledTranslationRejection, ...]
    searches: tuple[LayoutFitSearchResult, ...]
    translation_batches: tuple[M8UncheckedTranslationBatchCapture, ...]
    competitor: M7ActionDescriptor | None
    competitor_context: ActionPolicyContext | None
    competitor_rank: PolicyRank | None
    classification: Literal["no_fit", "policy_dominated", "policy_not_dominated"]
    legacy_evidence_sha256: str | None

    def __post_init__(self) -> None:
        delta_items = self.delta.added if self.direction == "added" else self.delta.removed
        if (
            self.item not in delta_items
            or self.common_action_id != self.common.materialized_action_id
            or self.common_rank != rank_policy_action(self.common_rank.policy, self.common.context)
            or not self.state_before_sha256.startswith("sha256:")
            or not self.state_after_sha256.startswith("sha256:")
        ):
            raise ValueError("M8 unchecked influence source bindings differ")
        if self.translation_batches:
            if len(self.searches) != len(self.translation_batches):
                raise ValueError("M8 unchecked influence searches and translations differ")
            for search, batch in zip(self.searches, self.translation_batches, strict=True):
                if (
                    search.candidate_id != batch.candidate_id
                    or search.remnant_id != batch.remnant_id
                    or search.generated_candidate_count != batch.generated_candidate_count
                    or search.duplicate_candidate_count != batch.duplicate_candidate_count
                    or search.budget_truncated != batch.budget_truncated
                    or (
                        search.translation is not None
                        and (
                            search.evaluated_candidate_count <= 0
                            or search.evaluated_candidate_count > len(batch.translations)
                            or batch.translations[search.evaluated_candidate_count - 1]
                            != search.translation
                        )
                    )
                ):
                    raise ValueError("M8 unchecked influence search differs from its source batch")
        has_competitor = (
            self.competitor is not None,
            self.competitor_context is not None,
            self.competitor_rank is not None,
        )
        if has_competitor not in {(False, False, False), (True, True, True)}:
            raise ValueError("M8 unchecked influence competitor source is incomplete")
        if self.competitor is not None and (
            self.competitor.evidence is None
            or self.competitor_context is None
            or self.competitor_rank is None
            or self.competitor.action_id != self.competitor_context.action_id
            or self.competitor_rank
            != rank_policy_action(self.competitor_rank.policy, self.competitor_context)
        ):
            raise ValueError("M8 unchecked influence competitor bindings differ")
        if (self.classification == "no_fit" and any(has_competitor)) or (
            self.classification != "no_fit" and not all(has_competitor)
        ):
            raise ValueError("M8 unchecked influence classification differs from competitor")
        if self.competitor_rank is not None and (
            (self.classification == "policy_dominated")
            != (self.common_rank <= self.competitor_rank)
        ):
            raise ValueError("M8 unchecked influence policy comparison differs")
        if (self.classification == "policy_not_dominated") != (self.legacy_evidence_sha256 is None):
            raise ValueError("M8 unchecked influence legacy digest shape differs")

    @property
    def remnant_id(self) -> str:
        return self.item.remnant.remnant_id

    @property
    def legacy_evidence_payload(self) -> dict[str, object]:
        if self.classification == "policy_not_dominated":
            raise ValueError("nonpassive influence has no accepted legacy evidence digest")
        return _evidence_payload(
            event_position=self.event_position,
            remnant_id=self.remnant_id,
            classification=self.classification,
            direction=self.direction,
            delta=self.delta,
            common=self.common,
            common_decision_key=self.common_rank.decision_key,
            common_fact_sha256=self.common_fact_sha256,
            branch_action_id=self.branch_action_id,
            state_before_sha256=self.state_before_sha256,
            state_after_sha256=self.state_after_sha256,
            rejections=self.rejections,
            searches=_search_payload(self.searches, label="unchecked influence capture"),
            competitor=self.competitor,
            competitor_rank=self.competitor_rank,
        )


@dataclass(frozen=True)
class M8UncheckedEventPassivityCapture:
    """Producer-only branch traversal result with no trusted witness provenance."""

    passive: bool
    classification: Literal["no_fit", "policy_dominated"] | None
    branch_after: M7ReplayCursor | None
    state_before_sha256: str | None
    state_after_sha256: str | None
    influences: tuple[M8UncheckedInfluenceCapture, ...]
    exact_search_count: int
    authority_mode: ClassVar[Literal["unchecked_portable"]] = "unchecked_portable"

    def __post_init__(self) -> None:
        if self.exact_search_count < 0:
            raise ValueError("M8 unchecked exact search count cannot be negative")
        if self.passive != (
            self.classification is not None
            and self.branch_after is not None
            and self.state_before_sha256 is not None
            and self.state_after_sha256 is not None
        ):
            raise ValueError("M8 unchecked passivity capture shape is inconsistent")


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
_VALIDATED_COMMON_REGISTRATION_PROVENANCE = object()


@dataclass
class _ValidatedCommonEntry:
    reference: weakref.ReferenceType[ValidatedCommonTransition]
    binding_token: object
    owner_pid: int
    snapshot: M7SemanticRuntimeSnapshot
    authority: M7AuthoritativeProofRuntime | None
    owns_snapshot: bool
    canonical_fact: M8CommonTransitionFact
    canonical_fact_binding: tuple[object, ...]
    integrity_sha256: str
    checker_token: object | None


_VALIDATED_COMMON_REGISTRY: dict[
    int,
    _ValidatedCommonEntry,
] = {}


def _sanitize_validated_common_registry_keys() -> bool:
    entries = tuple(_VALIDATED_COMMON_REGISTRY.items())
    valid_entries = tuple((key, value) for key, value in entries if type(key) is int)
    if len(valid_entries) == len(entries):
        return False
    _VALIDATED_COMMON_REGISTRY.clear()
    _VALIDATED_COMMON_REGISTRY.update(valid_entries)
    return True


def _common_fact_registry_binding(fact: M8CommonTransitionFact) -> tuple[object, ...]:
    return (
        fact.replay_input_id,
        fact.replay_input_sha256,
        fact.event_position,
        id(fact.cursor_before),
        fact.cursor_before_sha256,
        id(fact.step),
        fact.cursor_after_sha256,
        fact.event_id,
        id(fact.policy_rank),
        fact.semantic_runtime_sha256,
        fact.content_sha256,
    )


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


def _common_transition_fact_from_catalog(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    semantic_runtime_sha256: str,
    execution_runtime: M7ReplayRuntime,
    catalog,  # type: ignore[no-untyped-def]
) -> M8CommonTransitionFact:
    event_position = cursor.next_event_position
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selected.action_id)
    step = apply_m7_action_descriptor(
        execution_runtime,
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


def _portable_translation_point(
    translation: tuple[float, float],
) -> portable_facts.M8TranslationPointV2:
    return portable_facts.M8TranslationPointV2(
        x_bits=portable_facts.encode_canonical_f64(float(translation[0])),
        y_bits=portable_facts.encode_canonical_f64(float(translation[1])),
    )


def _portable_polygon(polygon):  # type: ignore[no-untyped-def]
    return portable_facts.M8PortablePolygonV2(
        wkb_hex=polygon.wkb_hex,
        polygon_sha256=polygon.polygon_sha256,
        area_bits=portable_facts.encode_canonical_f64(float(polygon.area)),
    )


def _portable_material(material):  # type: ignore[no-untyped-def]
    return portable_facts.M8PortableMaterialIdentityV2(
        material_code=material.material_code,
        grade=material.grade,
        thickness=material.thickness,
        surface=material.surface,
        grain=material.grain,
        provenance=material.provenance.value,
    )


def _portable_remnant(remnant):  # type: ignore[no-untyped-def]
    lineage = remnant.lineage
    return portable_facts.M8PortableRemnantStockV2(
        remnant_id=remnant.remnant_id,
        geometry=_portable_polygon(remnant.geometry),
        material=_portable_material(remnant.material),
        root_sheet_area_bits=portable_facts.encode_canonical_f64(float(remnant.root_sheet_area)),
        root_sheet_short_side_bits=portable_facts.encode_canonical_f64(
            float(remnant.root_sheet_short_side)
        ),
        lineage=portable_facts.M8PortableRemnantLineageV2(
            root_stock_id=lineage.root_stock_id,
            parent_remnant_id=lineage.parent_remnant_id,
            ancestor_remnant_ids=lineage.ancestor_remnant_ids,
            generation=lineage.generation,
            source_candidate_id=lineage.source_candidate_id,
            source_component_sha256=lineage.source_component_sha256,
        ),
    )


def _portable_inventory_item(item: InventoryItem) -> portable_facts.M8PortableInventoryItemV2:
    return portable_facts.M8PortableInventoryItemV2(
        remnant=_portable_remnant(item.remnant),
        entered_at=portable_facts.encode_canonical_utc(item.entered_at),
    )


def _portable_ledger(ledger):  # type: ignore[no-untyped-def]
    return portable_facts.M8PortableCostLedgerV2(
        purchase_cost_bits=portable_facts.encode_canonical_f64(float(ledger.purchase_cost)),
        storage_cost_bits=portable_facts.encode_canonical_f64(float(ledger.storage_cost)),
        return_handling_cost_bits=portable_facts.encode_canonical_f64(
            float(ledger.return_handling_cost)
        ),
        retrieval_handling_cost_bits=portable_facts.encode_canonical_f64(
            float(ledger.retrieval_handling_cost)
        ),
        scrap_proceeds_bits=portable_facts.encode_canonical_f64(float(ledger.scrap_proceeds)),
        terminal_scrap_credit_bits=portable_facts.encode_canonical_f64(
            float(ledger.terminal_scrap_credit)
        ),
        net_cost_bits=portable_facts.encode_canonical_f64(float(ledger.net_cost)),
    )


def _portable_search_config(
    config: LayoutFitSearchConfig,
) -> portable_facts.M8PortableLayoutSearchConfigV2:
    return portable_facts.M8PortableLayoutSearchConfigV2(
        grid_columns=config.grid_columns,
        grid_rows=config.grid_rows,
        maximum_candidates=config.maximum_candidates,
        candidate_source_order=config.candidate_source_order,
    )


def _portable_search_result(
    search: LayoutFitSearchResult,
) -> portable_facts.M8PortableLayoutSearchResultV2:
    return portable_facts.M8PortableLayoutSearchResultV2(
        status=search.status.value,
        candidate_id=search.candidate_id,
        remnant_id=search.remnant_id,
        config=_portable_search_config(search.config),
        generated_candidate_count=search.generated_candidate_count,
        duplicate_candidate_count=search.duplicate_candidate_count,
        evaluated_candidate_count=search.evaluated_candidate_count,
        budget_truncated=search.budget_truncated,
        translation=(
            _portable_translation_point(search.translation)
            if search.translation is not None
            else None
        ),
    )


def _portable_accounting(accounting):  # type: ignore[no-untyped-def]
    return portable_facts.M8PortableAccountingV2(
        parent_remnant_area_bits=portable_facts.encode_canonical_f64(
            float(accounting.parent_remnant_area)
        ),
        placed_area_bits=portable_facts.encode_canonical_f64(float(accounting.placed_area)),
        process_loss_area_bits=portable_facts.encode_canonical_f64(
            float(accounting.process_loss_area)
        ),
        retained_child_area_bits=portable_facts.encode_canonical_f64(
            float(accounting.retained_child_area)
        ),
        scrap_area_bits=portable_facts.encode_canonical_f64(float(accounting.scrap_area)),
        reconciliation_delta_bits=portable_facts.encode_canonical_f64(
            float(accounting.reconciliation_delta)
        ),
        area_tolerance_bits=portable_facts.encode_canonical_f64(float(accounting.area_tolerance)),
    )


def _portable_action(action):  # type: ignore[no-untyped-def]
    return portable_facts.M8PortableLayoutActionV2(
        action_id=action.action_id,
        content_sha256=action.content_sha256,
        problem_id=action.problem_id,
        problem_sha256=action.problem_sha256,
        candidate_set_id=action.candidate_set_id,
        candidate_set_sha256=action.candidate_set_sha256,
        candidate_id=action.candidate_id,
        kind=action.kind.value,
        selected_stock=_portable_remnant(action.selected_stock),
        selected_remnant_id=action.selected_remnant_id,
        translation=_portable_translation_point(action.translation),
        placements=tuple(
            portable_facts.M8PortablePlacementV2(
                part_id=item.part_id,
                rotation_bits=portable_facts.encode_canonical_f64(float(item.rotation)),
                translation=_portable_translation_point(item.translation),
            )
            for item in action.placements
        ),
        placed_parts=tuple(
            portable_facts.M8PortablePlacedPartV2(
                part_id=item.part_id,
                geometry=_portable_polygon(item.geometry),
            )
            for item in action.placed_parts
        ),
        search_result=(
            _portable_search_result(action.search_result)
            if action.search_result is not None
            else None
        ),
        accounting=_portable_accounting(action.accounting),
        returned_remnants=tuple(_portable_remnant(item) for item in action.returned_remnants),
    )


def _portable_policy_context(
    context: ActionPolicyContext,
) -> portable_facts.M8PortablePolicyContextV2:
    return portable_facts.M8PortablePolicyContextV2(
        action_id=context.action_id,
        kind=context.kind.value,
        candidate_id=context.candidate_id,
        candidate_width_bits=portable_facts.encode_canonical_f64(float(context.candidate_width)),
        selected_stock_id=context.selected_stock_id,
        immediate_net_cost_bits=portable_facts.encode_canonical_f64(
            float(context.immediate_net_cost)
        ),
        selected_remnant_age_hours_bits=portable_facts.encode_canonical_f64(
            float(context.selected_remnant_age_hours)
        ),
        returned_regularity_bits=portable_facts.encode_canonical_f64(
            float(context.returned_regularity)
        ),
        known_order_lookahead_term_bits=portable_facts.encode_canonical_f64(
            float(context.known_order_lookahead_term)
        ),
    )


def _portable_policy_rank_components(
    values: tuple[object, ...],
) -> tuple[portable_facts.M8PolicyRankComponentV2, ...]:
    components = []
    for value in values:
        if type(value) is float:
            components.append(
                portable_facts.M8PolicyRankComponentV2(
                    component_kind="f64",
                    f64_bits=portable_facts.encode_canonical_f64(value),
                )
            )
        elif type(value) is int:
            components.append(
                portable_facts.M8PolicyRankComponentV2(
                    component_kind="int",
                    int_value=value,
                )
            )
        elif type(value) is str:
            components.append(
                portable_facts.M8PolicyRankComponentV2(
                    component_kind="string",
                    string_value=value,
                )
            )
        else:
            raise TypeError("M8 policy rank contains an unsupported component type")
    return tuple(components)


def _portable_cursor(cursor: M7ReplayCursor) -> portable_facts.M8PortableReplayCursorV2:
    return portable_facts.M8PortableReplayCursorV2(
        next_event_position=cursor.next_event_position,
        current_time=portable_facts.encode_canonical_utc(cursor.current_time),
        inventory=tuple(_portable_inventory_item(item) for item in cursor.inventory),
        cumulative_costs=_portable_ledger(cursor.cumulative_costs),
        timestamp_group_sequence=cursor.timestamp_group_sequence,
        timestamp_subsequence=cursor.timestamp_subsequence,
        previous_release=(
            portable_facts.encode_canonical_utc(cursor.previous_release)
            if cursor.previous_release is not None
            else None
        ),
    )


def _portable_common_transition(
    fact: M8CommonTransitionFact,
) -> portable_facts.M8PortableCommonTransitionV2:
    step = fact.step
    event = step.event
    action = _portable_action(event.action)
    descriptor_evidence = (
        _portable_action(step.descriptor.evidence) if step.descriptor.evidence is not None else None
    )
    portable_context = _portable_policy_context(step.selected_context)
    return portable_facts.M8PortableCommonTransitionV2(
        replay_input_id=fact.replay_input_id,
        replay_input_sha256=fact.replay_input_sha256,
        semantic_runtime_sha256=fact.semantic_runtime_sha256,
        event_position=fact.event_position,
        cursor_before_sha256=fact.cursor_before_sha256,
        cursor_before=_portable_cursor(fact.cursor_before),
        descriptor=portable_facts.M8PortableActionDescriptorV2(
            action_id=step.descriptor.action_id,
            kind=step.descriptor.kind.value,
            candidate_id=step.descriptor.candidate_id,
            selected_remnant_id=step.descriptor.selected_remnant_id,
            evidence=descriptor_evidence,
        ),
        selected_context=portable_context,
        action_binding=portable_facts.M8PortableActionBindingV2(
            catalog_action_id=step.action_binding.catalog_action_id,
            materialized_action_id=step.action_binding.materialized_action_id,
            context=_portable_policy_context(step.action_binding.context),
        ),
        event=portable_facts.M8PortableReplayEventV2(
            sequence=event.sequence,
            event_id=event.event_id,
            binding_id=event.binding_id,
            occurred_at=portable_facts.encode_canonical_utc(event.occurred_at),
            timestamp_group_sequence=event.timestamp_group_sequence,
            timestamp_subsequence=event.timestamp_subsequence,
            storage_interval_start=portable_facts.encode_canonical_utc(
                event.storage_interval_start
            ),
            storage_interval_end=portable_facts.encode_canonical_utc(event.storage_interval_end),
            inventory_before=tuple(
                _portable_inventory_item(item) for item in event.inventory_before
            ),
            action_set_size=event.action_set_size,
            standard_action_count=event.standard_action_count,
            remnant_action_count=event.remnant_action_count,
            fit_search_query_count=event.fit_search_query_count,
            fit_search_generated_candidate_count=event.fit_search_generated_candidate_count,
            fit_search_evaluated_candidate_count=event.fit_search_evaluated_candidate_count,
            fit_search_budget_truncated_count=event.fit_search_budget_truncated_count,
            policy_decision_key=event.policy_decision_key,
            action=action,
            inventory_after=tuple(_portable_inventory_item(item) for item in event.inventory_after),
            delta_costs=_portable_ledger(event.delta_costs),
            cumulative_costs=_portable_ledger(event.cumulative_costs),
        ),
        cursor_after_sha256=fact.cursor_after_sha256,
        cursor_after=_portable_cursor(step.cursor),
        event_id=fact.event_id,
        policy_rank=portable_facts.M8PortablePolicyRankV2(
            policy_name=fact.policy_rank.policy.value,
            comparison_key=_portable_policy_rank_components(fact.policy_rank.comparison_key),
            decision_key=fact.policy_rank.decision_key,
        ),
    )


def _capture_common_policy_context_source(
    context: ActionPolicyContext,
) -> ActionPolicyContext:
    """Detach one exact policy context without caller attribute dispatch."""

    context_fields = (
        "action_id",
        "kind",
        "candidate_id",
        "candidate_width",
        "selected_stock_id",
        "immediate_net_cost",
        "selected_remnant_age_hours",
        "returned_regularity",
        "known_order_lookahead_term",
    )
    state = _exact_instance_state(context, ActionPolicyContext, context_fields)
    if (
        type(state["action_id"]) is not str
        or type(state["kind"]) is not M7ActionKind
        or type(state["candidate_id"]) is not str
        or type(state["candidate_width"]) is not float
        or type(state["selected_stock_id"]) is not str
        or type(state["immediate_net_cost"]) is not float
        or type(state["selected_remnant_age_hours"]) is not float
        or type(state["returned_regularity"]) is not float
        or type(state["known_order_lookahead_term"]) is not float
    ):
        raise TypeError("M8 common policy context physical state differs")
    return ActionPolicyContext(**state)


def _capture_common_action_descriptor_source(
    descriptor: M7ActionDescriptor,
) -> M7ActionDescriptor:
    """Detach one exact submitted descriptor and its optional evidence graph."""

    descriptor_fields = (
        "action_id",
        "kind",
        "candidate_id",
        "selected_remnant_id",
        "evidence",
    )
    state = _exact_instance_state(descriptor, M7ActionDescriptor, descriptor_fields)
    selected_remnant_id = state["selected_remnant_id"]
    if (
        type(state["action_id"]) is not str
        or type(state["kind"]) is not M7ActionKind
        or type(state["candidate_id"]) is not str
        or (selected_remnant_id is not None and type(selected_remnant_id) is not str)
    ):
        raise TypeError("M8 common action descriptor physical state differs")
    evidence = state["evidence"]
    captured_evidence = None
    if evidence is not None:
        _validate_exact_model_graph(evidence, M7LayoutActionEvidence)
        captured_evidence = copy.deepcopy(evidence)
        _validate_exact_model_graph(captured_evidence, M7LayoutActionEvidence)
    return M7ActionDescriptor(
        action_id=state["action_id"],
        kind=state["kind"],
        candidate_id=state["candidate_id"],
        selected_remnant_id=selected_remnant_id,
        evidence=captured_evidence,
    )


def _capture_common_action_binding_source(
    binding: M7PolicyActionBinding,
) -> M7PolicyActionBinding:
    """Detach one exact submitted catalog-to-action binding."""

    state = _exact_instance_state(
        binding,
        M7PolicyActionBinding,
        ("catalog_action_id", "materialized_action_id", "context"),
    )
    if (
        type(state["catalog_action_id"]) is not str
        or type(state["materialized_action_id"]) is not str
    ):
        raise TypeError("M8 common action binding physical state differs")
    return M7PolicyActionBinding(
        catalog_action_id=state["catalog_action_id"],
        materialized_action_id=state["materialized_action_id"],
        context=_capture_common_policy_context_source(state["context"]),
    )


def _capture_common_step_source(step: M7StepResult) -> M7StepResult:
    """Detach the complete exact submitted step graph before proof authority."""

    state = _exact_instance_state(
        step,
        M7StepResult,
        ("descriptor", "selected_context", "action_binding", "event", "cursor"),
    )
    event = state["event"]
    _validate_exact_model_graph(event, M7ReplayEvent)
    captured_event = copy.deepcopy(event)
    _validate_exact_model_graph(captured_event, M7ReplayEvent)
    return M7StepResult(
        descriptor=_capture_common_action_descriptor_source(state["descriptor"]),
        selected_context=_capture_common_policy_context_source(state["selected_context"]),
        action_binding=_capture_common_action_binding_source(state["action_binding"]),
        event=captured_event,
        cursor=_capture_replay_cursor_source(state["cursor"]),
    )


def _capture_common_policy_rank_source(rank: PolicyRank) -> PolicyRank:
    """Detach the exact supported physical policy-rank representation."""

    state = _exact_instance_state(
        rank,
        PolicyRank,
        ("policy", "comparison_key", "decision_key"),
    )
    comparison_key = state["comparison_key"]
    decision_key = state["decision_key"]
    if (
        type(state["policy"]) is not M7PolicyName
        or type(comparison_key) is not tuple
        or type(decision_key) is not tuple
    ):
        raise TypeError("M8 common policy rank physical state differs")
    captured_comparison_key = tuple(tuple.__iter__(comparison_key))
    captured_decision_key = tuple(tuple.__iter__(decision_key))
    if any(type(value) not in (float, int, str) for value in captured_comparison_key) or any(
        type(value) is not str for value in captured_decision_key
    ):
        raise TypeError("M8 common policy rank component type differs")
    return PolicyRank(
        policy=state["policy"],
        comparison_key=captured_comparison_key,
        decision_key=captured_decision_key,
    )


def _capture_common_transition_graph_source(
    fact: M8CommonTransitionFact,
) -> tuple[M8CommonTransitionFact, str]:
    """Physically attest and detach every node in one public common fact."""

    fact_fields = tuple(item.name for item in fields(M8CommonTransitionFact))
    state = _exact_instance_state(fact, M8CommonTransitionFact, fact_fields)
    scalar_fields = (
        "replay_input_id",
        "replay_input_sha256",
        "cursor_before_sha256",
        "cursor_after_sha256",
        "event_id",
        "semantic_runtime_sha256",
        "content_sha256",
    )
    if type(state["event_position"]) is not int or any(
        type(state[name]) is not str for name in scalar_fields
    ):
        raise TypeError("M8 common transition scalar physical state differs")
    captured = M8CommonTransitionFact(
        replay_input_id=state["replay_input_id"],
        replay_input_sha256=state["replay_input_sha256"],
        event_position=state["event_position"],
        cursor_before=_capture_replay_cursor_source(state["cursor_before"]),
        cursor_before_sha256=state["cursor_before_sha256"],
        step=_capture_common_step_source(state["step"]),
        cursor_after_sha256=state["cursor_after_sha256"],
        event_id=state["event_id"],
        policy_rank=_capture_common_policy_rank_source(state["policy_rank"]),
        semantic_runtime_sha256=state["semantic_runtime_sha256"],
        content_sha256=state["content_sha256"],
    )
    return captured, state["content_sha256"]


def _capture_common_transition_fact_source(
    runtime: M7ReplayRuntime,
    fact: M8CommonTransitionFact,
) -> tuple[portable_facts.M8PortableCommonTransitionV2, str]:
    """Capture a public fact before any snapshot or proof authority exists."""

    try:
        _preflight_prepared_source_runtime(runtime)
        semantic_before = m7_semantic_runtime_sha256(runtime)
        captured, content_sha256 = _capture_common_transition_graph_source(fact)
        portable = _portable_common_transition(captured)
        _validate_exact_model_graph(
            portable,
            portable_facts.M8PortableCommonTransitionV2,
        )
        _preflight_prepared_source_runtime(runtime)
        if m7_semantic_runtime_sha256(runtime) != semantic_before:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: common fact capture runtime drift"
            )
        return portable, content_sha256
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 common transition fact differs from authoritative common transition: "
            "source capture integrity"
        ) from error


def _derive_m8_common_transition_fact_authoritative(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    semantic_runtime_sha256: str,
) -> M8CommonTransitionFact:
    """Run the original cache-free common transition as the differential oracle."""

    event_position = cursor.next_event_position
    for item in cursor.inventory:
        compile_translation_rejections(
            runtime,
            event_position=event_position,
            item=item,
        )
    authoritative_runtime = _fresh_runtime(runtime)
    with profile_phase("action_catalog_enumeration"):
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
    return _common_transition_fact_from_catalog(
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_runtime_sha256,
        execution_runtime=authoritative_runtime,
        catalog=catalog,
    )


def _capture_inventory_item_source(item: InventoryItem) -> InventoryItem:
    """Detach one exact caller inventory item before consulting private authority."""

    try:
        _validate_exact_model_graph(item, InventoryItem)
        item_state = _exact_instance_state(item, InventoryItem, ("remnant", "entered_at"))
        captured = InventoryItem(
            remnant=_capture_prepared_remnant_source(item_state["remnant"]),
            entered_at=item_state["entered_at"],
        )
        _validate_exact_model_graph(captured, InventoryItem)
        return captured
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: inventory source capture"
        ) from error


def _capture_replay_cursor_source(cursor: M7ReplayCursor) -> M7ReplayCursor:
    """Detach exact cursor state before consulting any prepared authority."""

    fields = (
        "next_event_position",
        "current_time",
        "inventory",
        "cumulative_costs",
        "timestamp_group_sequence",
        "timestamp_subsequence",
        "previous_release",
    )
    try:
        state = _exact_instance_state(cursor, M7ReplayCursor, fields)
        inventory = state["inventory"]
        current_time = state["current_time"]
        previous_release = state["previous_release"]
        if (
            type(state["next_event_position"]) is not int
            or type(state["timestamp_group_sequence"]) is not int
            or type(state["timestamp_subsequence"]) is not int
            or type(current_time) is not datetime
            or current_time.tzinfo is not UTC
            or (
                previous_release is not None
                and (type(previous_release) is not datetime or previous_release.tzinfo is not UTC)
            )
            or type(inventory) is not tuple
        ):
            raise TypeError("M8 unchecked cursor scalar type differs")
        ledger = state["cumulative_costs"]
        _validate_exact_model_graph(ledger, ReplayCostLedger)
        ledger_state = _exact_instance_state(
            ledger,
            ReplayCostLedger,
            tuple(ReplayCostLedger.model_fields),
        )
        captured = M7ReplayCursor(
            next_event_position=state["next_event_position"],
            current_time=current_time,
            inventory=tuple(
                _capture_inventory_item_source(item) for item in tuple.__iter__(inventory)
            ),
            cumulative_costs=ReplayCostLedger(**ledger_state),
            timestamp_group_sequence=state["timestamp_group_sequence"],
            timestamp_subsequence=state["timestamp_subsequence"],
            previous_release=previous_release,
        )
        if type(captured) is not M7ReplayCursor:
            raise TypeError("M8 unchecked cursor capture differs")
        return captured
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: cursor source capture"
        ) from error


def _capture_replay_cursor_commitment_source(
    cursor: M7ReplayCursor,
) -> tuple[int, str]:
    """Return immutable cursor commitments without retaining a callback-visible DTO."""

    captured = _capture_replay_cursor_source(cursor)
    return captured.next_event_position, m7_cursor_sha256(captured)


@dataclass(frozen=True)
class _CapturedFutureVisibility:
    """Callback-free visible suffix captured before any private authority exists."""

    mode: str
    current_position: int
    bindings: tuple[TemporalInstanceBinding, ...]
    semantic_runtime_sha256: str

    def visible_suffix(
        self,
        *,
        current_position: int,
    ) -> tuple[TemporalInstanceBinding, ...]:
        if type(current_position) is not int or current_position != self.current_position:
            raise ValueError("M8 captured visibility position differs")
        return self.bindings


def _capture_visible_suffix_source(
    runtime: M7ReplayRuntime,
    visibility: object,
    *,
    current_position: int,
) -> _CapturedFutureVisibility:
    """Invoke caller visibility between exact public-runtime stability checks."""

    if type(current_position) is not int or current_position < 0:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: visibility position capture"
        )
    try:
        _preflight_prepared_source_runtime(runtime)
        semantic_before = m7_semantic_runtime_sha256(runtime)
        mode = visibility.mode
        visible_source = visibility.visible_suffix(current_position=current_position)
        if type(mode) is not str or not mode or type(visible_source) is not tuple:
            raise TypeError("M8 visibility source type differs")
        visible = []
        for binding in tuple.__iter__(visible_source):
            _validate_exact_model_graph(binding, TemporalInstanceBinding)
            captured_binding = copy.deepcopy(binding)
            _validate_exact_model_graph(captured_binding, TemporalInstanceBinding)
            visible.append(captured_binding)
        captured_visible = tuple(visible)
        _preflight_prepared_source_runtime(runtime)
        semantic_after = m7_semantic_runtime_sha256(runtime)
        if semantic_after != semantic_before:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: visibility runtime drift"
            )
        registered_source = runtime.replay_input.instances
        if type(registered_source) is not tuple or current_position >= len(registered_source):
            raise TypeError("M8 visibility runtime suffix differs")
        registered = []
        for binding in tuple.__iter__(registered_source):
            _validate_exact_model_graph(binding, TemporalInstanceBinding)
            captured_binding = copy.deepcopy(binding)
            _validate_exact_model_graph(captured_binding, TemporalInstanceBinding)
            registered.append(captured_binding)
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: visibility source capture"
        ) from error
    if mode == "known_only":
        expected = ()
    elif mode == "full_realized_future":
        expected = tuple(registered)[current_position + 1 :]
    else:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: visibility mode source"
        )
    if captured_visible != expected:
        raise ValueError("M8 visibility provider returned data inconsistent with its mode")
    return _CapturedFutureVisibility(
        mode=mode,
        current_position=current_position,
        bindings=expected,
        semantic_runtime_sha256=semantic_after,
    )


def _capture_c0_branch_remnant_rows(
    rows: tuple[_C0BranchRemnantRow, ...],
) -> tuple[_C0BranchRemnantRow, ...]:
    """Detach every public row and item before a prepared transaction begins."""

    try:
        if type(rows) is not tuple:
            raise TypeError("M8 C0 row collection type differs")
        captured = []
        for row in tuple.__iter__(rows):
            if type(row) is not _C0BranchRemnantRow:
                raise TypeError("M8 C0 row type differs")
            branch_id = object.__getattribute__(row, "branch_id")
            direction = object.__getattribute__(row, "direction")
            item = object.__getattribute__(row, "item")
            if (
                type(branch_id) is not int
                or branch_id < 0
                or type(direction) is not str
                or direction not in ("added", "removed")
            ):
                raise TypeError("M8 C0 row scalar type differs")
            captured.append(
                _C0BranchRemnantRow(
                    branch_id=branch_id,
                    direction=direction,
                    item=_capture_inventory_item_source(item),
                )
            )
        return tuple(captured)
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: C0 row source capture"
        ) from error


def _zero_generation_rejection_witness(
    runtime: M7ReplayRuntime,
    *,
    compiled: CompiledRejectionProblem,
    event_position: int,
    item: InventoryItem,
    prepared_layouts: _PreparedTranslationLayoutBatch | None,
) -> FastCommonRejectionWitness | None:
    captured_item = _capture_inventory_item_source(item)
    captured_remnant = captured_item.remnant
    source_runtime = (
        runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
    )
    binding = source_runtime.replay_input.instances[event_position]
    measured = (
        prepare_translation_rejection_remnant(captured_remnant)
        if prepared_layouts is None
        else _registered_prepared_remnant_measurement(
            prepared_layouts,
            runtime,
            captured_remnant,
        )
    )
    min_x, min_y, max_x, max_y = measured.bounds
    remnant_width = float(max_x - min_x)
    remnant_height = float(max_y - min_y)
    fit_config = source_runtime.replay_input.fit_config
    area_tolerance = max(
        fit_config.coordinate_tolerance,
        measured.area * fit_config.relative_area_tolerance,
    )
    material_matches = material_key(captured_remnant.material) == material_key(binding.material)
    query = {
        "material_matches": material_matches,
        "remnant_area": measured.area,
        "remnant_width": remnant_width,
        "remnant_height": remnant_height,
        "area_tolerance": area_tolerance,
        "coordinate_tolerance": fit_config.coordinate_tolerance,
    }
    impossible = certify_frontier_impossible(compiled.frontier, **query)
    zero_generation = not material_matches or all(
        scalar.width > remnant_width + fit_config.coordinate_tolerance
        or scalar.height > remnant_height + fit_config.coordinate_tolerance
        for scalar in compiled.frontier.members
    )
    if not impossible or not zero_generation:
        return None
    return FastCommonRejectionWitness(
        remnant_id=captured_remnant.remnant_id,
        candidate_ids=tuple(sorted(scalar.candidate_id for scalar in compiled.frontier.members)),
        material_matches=material_matches,
        remnant_area=measured.area,
        remnant_width=remnant_width,
        remnant_height=remnant_height,
        area_tolerance=area_tolerance,
        coordinate_tolerance=fit_config.coordinate_tolerance,
        impossible=True,
        zero_generation=True,
    )


def _is_zero_generation_no_fit(search: LayoutFitSearchResult) -> bool:
    return (
        search.status is LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH
        and search.generated_candidate_count == 0
        and search.duplicate_candidate_count == 0
        and search.evaluated_candidate_count == 0
        and not search.budget_truncated
        and search.translation is None
    )


def _zero_generation_searches(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
) -> tuple[LayoutFitSearchResult, ...]:
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    return tuple(
        LayoutFitSearchResult(
            status=LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH,
            candidate_id=candidate.candidate_id,
            remnant_id=item.remnant.remnant_id,
            config=runtime.replay_input.search_config,
            generated_candidate_count=0,
            duplicate_candidate_count=0,
            evaluated_candidate_count=0,
            budget_truncated=False,
            translation=None,
        )
        for candidate in verified.candidates
    )


def _synthesize_scalar_no_fit_source(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    mode: _CommonDerivationMode,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
) -> _ScalarNoFitSource:
    """Generate exact search counts while preserving their ordered source batches."""

    captured_item = _capture_inventory_item_source(item)
    captured_remnant = captured_item.remnant
    source_runtime = (
        runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
    )
    binding = source_runtime.replay_input.instances[event_position]
    if material_key(captured_remnant.material) != material_key(binding.material):
        return _ScalarNoFitSource(searches=(), translation_batches=())
    problem = next(
        problem
        for problem in source_runtime.replay_input.problems
        if problem.problem_id == binding.problem_id
    )
    verified = source_runtime.runtime_candidates[binding.problem_id]
    prepared_remnant = prepare_remnant_geometry(captured_remnant)
    if prepared_layouts is None:
        layouts = tuple(
            prepare_layout_footprint(
                problem.problem,
                candidate,
                source_runtime.replay_input.fit_config,
            )
            for candidate in verified.candidates
        )
    else:
        observed_layouts = _prepared_layout_footprints(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
        layouts = _consume_prepared_layout_footprints(
            prepared_layouts,
            runtime,
            event_position=event_position,
            observed=observed_layouts,
        )
    certificates = (
        tuple(
            certify_translation_impossible(
                layout,
                captured_remnant,
                material=binding.material,
                fit_config=source_runtime.replay_input.fit_config,
            )
            for layout in layouts
        )
        if prepared_layouts is None
        else tuple(
            compiled_rejection.certificate
            for compiled_rejection in _compile_prepared_translation_rejections(
                runtime,
                prepared=prepared_layouts,
                event_position=event_position,
                item=captured_item,
            )
        )
    )
    if any(not certificate.impossible for certificate in certificates):
        return _ScalarNoFitSource(searches=None, translation_batches=())
    rust_generated = source_runtime.jagua_executable is not None and not any(
        polygon.interiors for layout in layouts for polygon in layout.part_polygons
    )
    if rust_generated:
        try:
            generated = run_jagua_generated_prefilter(
                source_runtime.jagua_executable,
                remnant=prepared_remnant,
                layouts=layouts,
                fit_config=source_runtime.replay_input.fit_config,
                search_config=source_runtime.replay_input.search_config,
                container_guard=source_runtime.replay_input.jagua_container_guard or 1.0,
            )
        except JaguaRepresentationError:
            if mode is _CommonDerivationMode.UNCHECKED_PORTABLE:
                return _ScalarNoFitSource(
                    searches=None,
                    translation_batches=(),
                    exact_replay_reason="unsupported_representation",
                )
            rust_generated = False
        else:
            translations = generated.translation_batches
            if mode is _CommonDerivationMode.TRUSTED_LOCAL:
                with (
                    activate_m8_local_trusted_audit(),
                    profile_phase("translation_count_audit"),
                ):
                    audited_counts = audit_layout_translation_batch(
                        remnant=prepared_remnant,
                        layouts=layouts,
                        expected=translations,
                        fit_config=source_runtime.replay_input.fit_config,
                        search_config=source_runtime.replay_input.search_config,
                        process_count=require_m8_translation_audit_processes(),
                    )
                for batch, audited in zip(translations, audited_counts, strict=True):
                    if (
                        batch.generated_candidate_count != audited.generated_candidate_count
                        or batch.duplicate_candidate_count != audited.duplicate_candidate_count
                        or len(batch.translations) != audited.evaluated_candidate_count
                        or batch.budget_truncated != audited.budget_truncated
                    ):
                        raise ValueError(
                            "M8 Jagua translation counts differ from independent audit"
                        )
    if not rust_generated:
        translations = tuple(
            generate_layout_translations(
                captured_remnant,
                candidate,
                fit_config=source_runtime.replay_input.fit_config,
                search_config=source_runtime.replay_input.search_config,
                prepared_layout=layout,
                prepared_remnant=prepared_remnant,
            )
            for candidate, layout in zip(verified.candidates, layouts, strict=True)
        )
    return _ScalarNoFitSource(
        searches=tuple(
            LayoutFitSearchResult(
                status=LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH,
                candidate_id=candidate.candidate_id,
                remnant_id=captured_remnant.remnant_id,
                config=source_runtime.replay_input.search_config,
                generated_candidate_count=batch.generated_candidate_count,
                duplicate_candidate_count=batch.duplicate_candidate_count,
                evaluated_candidate_count=len(batch.translations),
                budget_truncated=batch.budget_truncated,
                translation=None,
            )
            for candidate, batch in zip(verified.candidates, translations, strict=True)
        ),
        translation_batches=translations,
    )


def _synthesize_scalar_no_fit_searches(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
) -> tuple[LayoutFitSearchResult, ...] | None:
    """Trusted-local count synthesis retained for existing v1 capability paths."""

    return _synthesize_scalar_no_fit_source(
        runtime,
        event_position=event_position,
        item=item,
        mode=_CommonDerivationMode.TRUSTED_LOCAL,
        prepared_layouts=prepared_layouts,
    ).searches


def _seed_fit_searches(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    searches: tuple[LayoutFitSearchResult, ...],
) -> None:
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    _require_search_bindings(
        runtime,
        event_position=event_position,
        item=item,
        searches=searches,
        require_remnant_id=True,
    )
    runtime.fit_search_cache[
        (
            item.remnant.remnant_id,
            binding.problem_id,
            verified.evidence.candidate_set_id,
        )
    ] = searches


def _require_zero_generation_search_caches(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    inventory: tuple[InventoryItem, ...],
) -> None:
    """Reject stale cache claims without recomputing any placement search."""

    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    for item in inventory:
        if material_key(item.remnant.material) != material_key(binding.material):
            continue
        local_key = (
            item.remnant.remnant_id,
            binding.problem_id,
            verified.evidence.candidate_set_id,
        )
        cached_local = runtime.fit_search_cache.get(local_key)
        if cached_local is not None:
            canonical = _canonical_searches(cached_local, label="fast common local")
            _require_search_bindings(
                runtime,
                event_position=event_position,
                item=item,
                searches=canonical,
                require_remnant_id=True,
            )
            if any(not _is_zero_generation_no_fit(search) for search in canonical):
                raise ValueError(
                    "M8 local fit-search cache value differs from zero-generation rejection"
                )
        if runtime.shared_fit_search_cache is None:
            continue
        cached_shared = runtime.shared_fit_search_cache.get(
            m7_shared_fit_search_cache_key(
                geometry=item.remnant.geometry,
                fit_config=runtime.replay_input.fit_config,
                search_config=runtime.replay_input.search_config,
                problem_id=binding.problem_id,
                candidate_set_id=verified.evidence.candidate_set_id,
            )
        )
        if cached_shared is None:
            continue
        canonical = _canonical_searches(cached_shared, label="fast common shared")
        _require_search_bindings(
            runtime,
            event_position=event_position,
            item=item,
            searches=canonical,
            require_remnant_id=False,
        )
        if any(not _is_zero_generation_no_fit(search) for search in canonical):
            raise ValueError(
                "M8 shared fit-search cache value differs from zero-generation rejection"
            )


def _try_derive_m8_common_transition_fact_fast(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    semantic_runtime_sha256: str,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
) -> FastCommonTransitionResult | None:
    """Prune scalar rejects while preserving exact search for every survivor."""

    event_position = cursor.next_event_position
    source_runtime = (
        runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
    )
    compiled_standard = None
    if prepared_layouts is not None:
        observed_standard = _prepared_standard_winner(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
        compiled_standard = _consume_prepared_standard_winner(
            prepared_layouts,
            runtime,
            event_position=event_position,
            observed=observed_standard,
        )
    compiled = None
    if cursor.inventory:
        if prepared_layouts is None:
            binding = source_runtime.replay_input.instances[event_position]
            verified = source_runtime.runtime_candidates[binding.problem_id]
            if not _verified_rejection_layouts_cover_candidates(verified):
                return None
            compiled = compile_rejection_problem(
                source_runtime,
                event_position=event_position,
            )
        else:
            binding = source_runtime.replay_input.instances[event_position]
            verified = source_runtime.runtime_candidates[binding.problem_id]
            if not _verified_rejection_layouts_cover_candidates(verified):
                return None
            observed_compiled = _prepared_rejection_problem(
                prepared_layouts,
                runtime,
                event_position=event_position,
            )
            compiled = _consume_prepared_rejection_problem(
                prepared_layouts,
                runtime,
                event_position=event_position,
                observed=observed_compiled,
            )
    rejected = []
    counted_no_fit = []
    counted_searches: dict[str, tuple[LayoutFitSearchResult, ...]] = {}
    survivors = []
    witnesses = []
    with profile_phase("frontier_rejection"):
        for item in cursor.inventory:
            if compiled is None:  # pragma: no cover - inventory establishes the frontier.
                raise AssertionError("M8 fast common inventory lacks a rejection frontier")
            witness = _zero_generation_rejection_witness(
                runtime,
                compiled=compiled,
                event_position=event_position,
                item=item,
                prepared_layouts=prepared_layouts,
            )
            if witness is None:
                with profile_phase("translation_count_synthesis"):
                    searches = _synthesize_scalar_no_fit_searches(
                        runtime,
                        event_position=event_position,
                        item=item,
                        prepared_layouts=prepared_layouts,
                    )
                if searches is None:
                    survivors.append(item)
                else:
                    counted_no_fit.append(item)
                    counted_searches[item.remnant.remnant_id] = searches
            else:
                rejected.append(item)
                witnesses.append(witness)
    if survivors and not rejected and not counted_no_fit:
        increment_profile_count("exact_survivor_inventory_items", len(survivors))
        return None
    _require_zero_generation_search_caches(
        source_runtime,
        event_position=event_position,
        inventory=tuple(rejected),
    )
    execution_runtime = _fresh_runtime(source_runtime)
    standard_profiles = None if compiled_standard is None else compiled_standard.standard_profiles
    if counted_no_fit:
        binding = execution_runtime.replay_input.instances[event_position]
        verified = execution_runtime.runtime_candidates[binding.problem_id]
        if standard_profiles is not None:
            if tuple(item.candidate_id for item in standard_profiles) != tuple(
                item.candidate_id for item in verified.candidates
            ):
                raise ValueError("M8 prepared standard profiles differ from candidates")
            for profile in standard_profiles:
                execution_runtime.standard_profile_cache[
                    (binding.problem_id, profile.candidate_id)
                ] = profile
        for item in rejected:
            if material_key(item.remnant.material) == material_key(binding.material):
                _seed_fit_searches(
                    execution_runtime,
                    event_position=event_position,
                    item=item,
                    searches=_zero_generation_searches(
                        execution_runtime,
                        event_position=event_position,
                        item=item,
                    ),
                )
        for item in counted_no_fit:
            _seed_fit_searches(
                execution_runtime,
                event_position=event_position,
                item=item,
                searches=counted_searches[item.remnant.remnant_id],
            )
        with profile_phase("counted_no_fit_materialization"):
            catalog = enumerate_m7_action_catalog(
                execution_runtime,
                cursor=cursor,
                complete=False,
            )
        _require_common_search_caches_match_authoritative(
            source_runtime,
            authoritative_runtime=execution_runtime,
            event_position=event_position,
            inventory=cursor.inventory,
        )
    else:
        with profile_phase("standard_only_materialization"):
            if survivors:
                catalog = enumerate_m7_pruned_action_catalog(
                    execution_runtime,
                    cursor=cursor,
                    zero_generation_rejected_inventory=tuple(rejected),
                    precomputed_standard_profiles=standard_profiles,
                )
            else:
                catalog = enumerate_m7_standard_only_catalog(
                    execution_runtime,
                    cursor=cursor,
                    zero_generation_rejected_inventory=tuple(rejected),
                    precomputed_standard_profiles=standard_profiles,
                )
        if survivors:
            _require_common_search_caches_match_authoritative(
                source_runtime,
                authoritative_runtime=execution_runtime,
                event_position=event_position,
                inventory=tuple(survivors),
            )
    if compiled_standard is not None and not survivors:
        selection = select_m7_fallback(catalog, policy=source_runtime.replay_input.policy)
        descriptor = next(item for item in catalog.actions if item.action_id == selection.action_id)
        if (
            selection.action_id != compiled_standard.action_id
            or selection.decision_key != compiled_standard.decision_key
            or descriptor.candidate_id != compiled_standard.candidate_id
        ):
            raise ValueError("M8 prepared standard winner differs from exact profiles")
    fact = _common_transition_fact_from_catalog(
        source_runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_runtime_sha256,
        execution_runtime=execution_runtime,
        catalog=catalog,
    )
    return FastCommonTransitionResult(
        fact=fact,
        zero_generation_rejected_inventory=tuple(rejected),
        counted_no_fit_inventory=tuple(counted_no_fit),
        exact_survivor_inventory=tuple(survivors),
        witnesses=tuple(witnesses),
    )


def _capture_executable_identity(
    runtime: M7ReplayRuntime,
) -> tuple[str | None, int | None, int | None]:
    executable = runtime.jagua_executable
    if executable is None:
        return None, None, None
    before = executable.stat()
    content = executable.read_bytes()
    after = executable.stat()
    before_identity = (before.st_dev, before.st_ino, before.st_mode, before.st_size)
    after_identity = (after.st_dev, after.st_ino, after.st_mode, after.st_size)
    if before_identity != after_identity or len(content) != after.st_size:
        raise ValueError("M8 Jagua executable changed during unchecked source capture")
    return (
        f"sha256:{hashlib.sha256(content).hexdigest()}",
        after.st_size,
        after.st_mode & 0o777,
    )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _M8UncheckedPreparedSourceGuard:
    """Private O(1) lease for one already-deep-checked producer common source."""

    _token: object = field(repr=False, compare=False)

    def __reduce__(self) -> object:
        raise TypeError("M8 unchecked prepared source guards cannot be serialized")


@dataclass(frozen=True)
class _RegisteredUncheckedPreparedSourceGuard:
    reference: weakref.ReferenceType[_M8UncheckedPreparedSourceGuard]
    owner_pid: int
    token: object
    runtime: M7ReplayRuntime
    runtime_authority: M7AuthoritativeProofRuntime
    scope_owner: object
    prepared_layouts: _PreparedTranslationLayoutBatch
    common: M8UncheckedProducerTransition
    common_fact: M8CommonTransitionFact
    source: M8UncheckedCommonSourceCapture
    semantic_runtime_sha256: str
    replay_input_id: str
    replay_input_sha256: str
    executable_identity: tuple[str | None, int | None, int | None]


_UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY: dict[
    int,
    _RegisteredUncheckedPreparedSourceGuard,
] = {}


def _sanitize_unchecked_source_guard_registry_keys() -> bool:
    entries = tuple(_UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.items())
    valid_entries = tuple((key, value) for key, value in entries if type(key) is int)
    if len(valid_entries) == len(entries):
        return False
    _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.clear()
    _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.update(valid_entries)
    return True


def _require_unchecked_prepared_source_guard(
    guard: _M8UncheckedPreparedSourceGuard,
    *,
    runtime: M7ReplayRuntime,
    common: M8UncheckedProducerTransition,
    prepared_layouts: _PreparedTranslationLayoutBatch,
    scope_owner: object | None = None,
) -> None:
    """Perform only scope and object-identity checks inside the branch hot loop."""

    malformed_registry_keys = _sanitize_unchecked_source_guard_registry_keys()
    registered = _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.get(id(guard))
    try:
        invalid = (
            malformed_registry_keys
            or type(guard) is not _M8UncheckedPreparedSourceGuard
            or type(registered) is not _RegisteredUncheckedPreparedSourceGuard
            or type(registered.reference) is not weakref.ReferenceType
            or registered.reference() is not guard
            or type(registered.owner_pid) is not int
            or registered.owner_pid != os.getpid()
            or registered.token is not guard._token  # noqa: SLF001
            or registered.runtime is not runtime
            or type(registered.runtime_authority) is not M7AuthoritativeProofRuntime
            or registered.runtime_authority.runtime is not runtime
            or (scope_owner is not None and registered.scope_owner is not scope_owner)
            or registered.prepared_layouts is not prepared_layouts
            or registered.common is not common
            or registered.common_fact is not common.common_fact
            or registered.source is not common.source
            or registered.semantic_runtime_sha256 != common.common_fact.semantic_runtime_sha256
            or registered.semantic_runtime_sha256 != common.source.semantic_runtime_sha256
            or registered.semantic_runtime_sha256 != registered.runtime_authority.semantic_sha256
            or registered.replay_input_id != runtime.replay_input.input_id
            or registered.replay_input_id != common.common_fact.replay_input_id
            or registered.replay_input_sha256 != runtime.replay_input.content_sha256
            or registered.replay_input_sha256 != common.common_fact.replay_input_sha256
            or registered.executable_identity
            != (
                common.source.jagua_executable_sha256,
                common.source.jagua_executable_size_bytes,
                common.source.jagua_executable_mode_bits,
            )
        )
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: unchecked source guard invalid or inactive"
        ) from error
    if invalid:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: unchecked source guard invalid or inactive"
        )
    try:
        registered.runtime_authority._require_active_identity(runtime)  # noqa: SLF001
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: unchecked runtime authority capability"
        ) from error
    try:
        prepared_layouts.require_active(runtime)
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: unchecked layout capability"
        ) from error


@contextmanager
def _guard_unchecked_prepared_common_source(
    runtime: M7ReplayRuntime,
    *,
    runtime_authority: M7AuthoritativeProofRuntime,
    scope_owner: object,
    prepared_layouts: _PreparedTranslationLayoutBatch,
    common: M8UncheckedProducerTransition,
) -> Iterator[_M8UncheckedPreparedSourceGuard]:
    """Deep-check one common source around all of its producer branch consumers."""

    if type(common) is not M8UncheckedProducerTransition:
        raise ValueError("M8 unchecked prepared guard requires a producer transition")
    expected_executable_identity = (
        common.source.jagua_executable_sha256,
        common.source.jagua_executable_size_bytes,
        common.source.jagua_executable_mode_bits,
    )

    def require_expensive_boundary() -> None:
        try:
            prepared_layouts.require_owned(runtime)
        except M8PreparedFrontierIntegrityError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: unchecked layout capability"
            ) from error
        _require_unchecked_runtime_source_identity(
            runtime,
            semantic_runtime_sha256=common.common_fact.semantic_runtime_sha256,
            runtime_authority=runtime_authority,
            operation="traversal",
        )
        if _capture_executable_identity(runtime) != expected_executable_identity:
            raise ValueError("M8 unchecked prepared source executable binding differs")

    require_expensive_boundary()
    token = object()
    guard = _M8UncheckedPreparedSourceGuard(token)
    key = id(guard)

    def discard(reference: weakref.ReferenceType[_M8UncheckedPreparedSourceGuard]) -> None:
        _sanitize_unchecked_source_guard_registry_keys()
        registered = _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.get(key)
        if (
            type(registered) is _RegisteredUncheckedPreparedSourceGuard
            and registered.reference is reference
        ):
            _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.pop(key, None)

    reference = weakref.ref(guard, discard)
    registered = _RegisteredUncheckedPreparedSourceGuard(
        reference=reference,
        owner_pid=os.getpid(),
        token=token,
        runtime=runtime,
        runtime_authority=runtime_authority,
        scope_owner=scope_owner,
        prepared_layouts=prepared_layouts,
        common=common,
        common_fact=common.common_fact,
        source=common.source,
        semantic_runtime_sha256=common.common_fact.semantic_runtime_sha256,
        replay_input_id=runtime.replay_input.input_id,
        replay_input_sha256=runtime.replay_input.content_sha256,
        executable_identity=expected_executable_identity,
    )
    _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY[key] = registered
    body_error: BaseException | None = None
    try:
        with _activate_prepared_event_validation(
            prepared_layouts,
            runtime,
            event_position=common.common_fact.event_position,
        ):
            yield guard
    except BaseException as error:
        body_error = error
        raise
    finally:
        integrity_error: M8PreparedFrontierIntegrityError | None = None
        try:
            _require_unchecked_prepared_source_guard(
                guard,
                runtime=runtime,
                common=common,
                scope_owner=scope_owner,
                prepared_layouts=prepared_layouts,
            )
        except M8PreparedFrontierIntegrityError as error:
            integrity_error = error
        except (AttributeError, TypeError, ValueError) as error:
            integrity_error = M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: unchecked source guard"
            )
            integrity_error.__cause__ = error
        malformed_registry_keys = _sanitize_unchecked_source_guard_registry_keys()
        current = _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.get(key)
        _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.pop(key, None)
        if (malformed_registry_keys or current is not registered) and integrity_error is None:
            integrity_error = M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: unchecked source guard cleanup"
            )
        try:
            # This detects persistent boundary changes. A swap restored between reads remains
            # explicitly unchecked producer provenance for the fresh Task-5 checker to audit.
            require_expensive_boundary()
        except (AttributeError, OSError, TypeError, ValueError) as error:
            if integrity_error is None:
                integrity_error = M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs during branch traversal"
                )
                integrity_error.__cause__ = error
        if integrity_error is not None and not isinstance(
            body_error,
            M8PreparedFrontierIntegrityError,
        ):
            raise integrity_error


def _require_unchecked_runtime_source_identity(
    runtime: M7ReplayRuntime,
    *,
    semantic_runtime_sha256: str,
    runtime_authority: M7AuthoritativeProofRuntime | None,
    operation: Literal["common capture", "traversal"],
) -> None:
    """Bind source capture to either a direct runtime or its active proof lease."""

    if runtime_authority is None:
        if m7_semantic_runtime_sha256(runtime) != semantic_runtime_sha256:
            raise ValueError(f"M8 unchecked {operation} runtime fingerprint differs")
        return
    try:
        runtime_authority.require_active(runtime)
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            f"M8 prepared frontier integrity differs: unchecked {operation} authority capability"
        ) from error
    if runtime_authority.semantic_sha256 != semantic_runtime_sha256:
        raise ValueError(f"M8 unchecked {operation} authority fingerprint differs")


def _capture_unchecked_m8_common_transition(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    semantic_runtime_sha256: str,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
    runtime_authority: M7AuthoritativeProofRuntime | None = None,
) -> M8UncheckedProducerTransition:
    """Capture one producer-only common transition without issuing authority."""

    captured_cursor = _capture_replay_cursor_source(cursor)
    _require_unchecked_runtime_source_identity(
        runtime,
        semantic_runtime_sha256=semantic_runtime_sha256,
        runtime_authority=runtime_authority,
        operation="common capture",
    )
    event_position = captured_cursor.next_event_position
    source_runtime = (
        runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
    )
    executable_identity_before = _capture_executable_identity(source_runtime)
    binding = source_runtime.replay_input.instances[event_position]
    verified = source_runtime.runtime_candidates[binding.problem_id]
    problem = next(
        item
        for item in source_runtime.replay_input.problems
        if item.problem_id == binding.problem_id
    )
    compiled_standard = None
    if prepared_layouts is not None:
        observed_standard = _prepared_standard_winner(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
        compiled_standard = _consume_prepared_standard_winner(
            prepared_layouts,
            runtime,
            event_position=event_position,
            observed=observed_standard,
        )
    compiled = None
    if captured_cursor.inventory:
        if prepared_layouts is None:
            if not _verified_rejection_layouts_cover_candidates(verified):
                compiled = None
            else:
                compiled = compile_rejection_problem(
                    source_runtime,
                    event_position=event_position,
                )
        else:
            if _verified_rejection_layouts_cover_candidates(verified):
                observed_compiled = _prepared_rejection_problem(
                    prepared_layouts,
                    runtime,
                    event_position=event_position,
                )
                compiled = _consume_prepared_rejection_problem(
                    prepared_layouts,
                    runtime,
                    event_position=event_position,
                    observed=observed_compiled,
                )

    rejected: list[InventoryItem] = []
    counted_no_fit: list[InventoryItem] = []
    counted_searches: dict[str, tuple[LayoutFitSearchResult, ...]] = {}
    survivors: list[InventoryItem] = []
    classifications: list[M8UncheckedCommonInventoryCapture] = []
    for item in captured_cursor.inventory:
        measured = (
            prepare_translation_rejection_remnant(item.remnant)
            if prepared_layouts is None
            else _registered_prepared_remnant_measurement(
                prepared_layouts,
                runtime,
                item.remnant,
            )
        )
        min_x, min_y, max_x, max_y = measured.bounds
        remnant_width = float(max_x - min_x)
        remnant_height = float(max_y - min_y)
        area_tolerance = max(
            source_runtime.replay_input.fit_config.coordinate_tolerance,
            measured.area * source_runtime.replay_input.fit_config.relative_area_tolerance,
        )
        material_matches = material_key(item.remnant.material) == material_key(binding.material)
        witness = (
            _zero_generation_rejection_witness(
                runtime,
                compiled=compiled,
                event_position=event_position,
                item=item,
                prepared_layouts=prepared_layouts,
            )
            if compiled is not None
            else None
        )
        if witness is not None:
            rejected.append(item)
            classifications.append(
                M8UncheckedCommonInventoryCapture(
                    item=item,
                    classification="scalar_no_fit",
                    material_matches=material_matches,
                    remnant_area=measured.area,
                    remnant_width=remnant_width,
                    remnant_height=remnant_height,
                    area_tolerance=area_tolerance,
                    coordinate_tolerance=(
                        source_runtime.replay_input.fit_config.coordinate_tolerance
                    ),
                    scalar_witness=witness,
                    candidate_rejection_layouts=verified.rejection_layouts,
                    frontier=compiled.frontier,
                    translation_batches=(),
                )
            )
            continue
        source = (
            _synthesize_scalar_no_fit_source(
                runtime,
                event_position=event_position,
                item=item,
                mode=_CommonDerivationMode.UNCHECKED_PORTABLE,
                prepared_layouts=prepared_layouts,
            )
            if compiled is not None
            else _ScalarNoFitSource(
                searches=None,
                translation_batches=(),
                exact_replay_reason="unsupported_representation",
            )
        )
        if source.searches is not None:
            if not source.translation_batches:
                raise ValueError("M8 unchecked counted-no-fit capture lacks translation batches")
            counted_no_fit.append(item)
            counted_searches[item.remnant.remnant_id] = source.searches
            classifications.append(
                M8UncheckedCommonInventoryCapture(
                    item=item,
                    classification="counted_no_fit",
                    material_matches=material_matches,
                    remnant_area=measured.area,
                    remnant_width=remnant_width,
                    remnant_height=remnant_height,
                    area_tolerance=area_tolerance,
                    coordinate_tolerance=(
                        source_runtime.replay_input.fit_config.coordinate_tolerance
                    ),
                    scalar_witness=None,
                    candidate_rejection_layouts=verified.rejection_layouts,
                    frontier=compiled.frontier if compiled is not None else None,
                    translation_batches=tuple(
                        M8UncheckedTranslationBatchCapture.from_source(batch)
                        for batch in source.translation_batches
                    ),
                )
            )
            continue
        survivors.append(item)
        unsupported_representation = source.exact_replay_reason == "unsupported_representation"
        classifications.append(
            M8UncheckedCommonInventoryCapture(
                item=item,
                classification="exact_survivor",
                material_matches=material_matches,
                remnant_area=measured.area,
                remnant_width=remnant_width,
                remnant_height=remnant_height,
                area_tolerance=area_tolerance,
                coordinate_tolerance=(source_runtime.replay_input.fit_config.coordinate_tolerance),
                scalar_witness=None,
                candidate_rejection_layouts=(
                    verified.rejection_layouts
                    if compiled is not None and not unsupported_representation
                    else ()
                ),
                frontier=(
                    compiled.frontier
                    if compiled is not None and not unsupported_representation
                    else None
                ),
                translation_batches=(),
                exact_replay_reason=(source.exact_replay_reason or "frontier_survivor"),
            )
        )

    execution_runtime = _fresh_runtime(source_runtime)
    standard_profiles = None if compiled_standard is None else compiled_standard.standard_profiles
    if counted_no_fit:
        if standard_profiles is not None:
            if tuple(item.candidate_id for item in standard_profiles) != tuple(
                item.candidate_id for item in verified.candidates
            ):
                raise ValueError("M8 prepared standard profiles differ from candidates")
            for profile in standard_profiles:
                execution_runtime.standard_profile_cache[
                    (binding.problem_id, profile.candidate_id)
                ] = profile
        for item in rejected:
            if material_key(item.remnant.material) == material_key(binding.material):
                _seed_fit_searches(
                    execution_runtime,
                    event_position=event_position,
                    item=item,
                    searches=_zero_generation_searches(
                        execution_runtime,
                        event_position=event_position,
                        item=item,
                    ),
                )
        for item in counted_no_fit:
            _seed_fit_searches(
                execution_runtime,
                event_position=event_position,
                item=item,
                searches=counted_searches[item.remnant.remnant_id],
            )
        catalog = enumerate_m7_action_catalog(
            execution_runtime,
            cursor=captured_cursor,
            complete=False,
        )
        _require_common_search_caches_match_authoritative(
            source_runtime,
            authoritative_runtime=execution_runtime,
            event_position=event_position,
            inventory=captured_cursor.inventory,
        )
    elif survivors and rejected:
        catalog = enumerate_m7_pruned_action_catalog(
            execution_runtime,
            cursor=captured_cursor,
            zero_generation_rejected_inventory=tuple(rejected),
            precomputed_standard_profiles=standard_profiles,
        )
        _require_common_search_caches_match_authoritative(
            source_runtime,
            authoritative_runtime=execution_runtime,
            event_position=event_position,
            inventory=tuple(survivors),
        )
    elif survivors:
        catalog = enumerate_m7_action_catalog(
            execution_runtime,
            cursor=captured_cursor,
            complete=False,
        )
        _require_common_search_caches_match_authoritative(
            source_runtime,
            authoritative_runtime=execution_runtime,
            event_position=event_position,
            inventory=captured_cursor.inventory,
        )
    else:
        catalog = enumerate_m7_standard_only_catalog(
            execution_runtime,
            cursor=captured_cursor,
            zero_generation_rejected_inventory=tuple(rejected),
            precomputed_standard_profiles=standard_profiles,
        )
    fact = _common_transition_fact_from_catalog(
        source_runtime,
        cursor=captured_cursor,
        semantic_runtime_sha256=semantic_runtime_sha256,
        execution_runtime=execution_runtime,
        catalog=catalog,
    )

    if standard_profiles is None:
        standard_profiles = tuple(
            execution_runtime.standard_profile_cache[(binding.problem_id, candidate.candidate_id)]
            for candidate in verified.candidates
        )
    profiles_by_candidate = {item.candidate_id: item for item in standard_profiles}
    contexts_by_action = {item.action_id: item for item in catalog.contexts}
    standard_descriptors = tuple(
        item for item in catalog.actions if item.kind is M7ActionKind.OPEN_STANDARD_SHEET
    )
    expected_candidate_ids = tuple(item.candidate_id for item in verified.candidates)
    if (
        tuple(item.candidate_id for item in standard_descriptors) != expected_candidate_ids
        or tuple(item.candidate_id for item in standard_profiles) != expected_candidate_ids
        or len(contexts_by_action) != len(catalog.contexts)
        or any(item.action_id not in contexts_by_action for item in standard_descriptors)
    ):
        raise ValueError("M8 unchecked capture lacks the complete ordered standard candidates")
    standard_candidates = tuple(
        M8UncheckedStandardCandidateCapture(
            profile_position=position,
            descriptor=descriptor,
            profile=profiles_by_candidate[descriptor.candidate_id],
            context=contexts_by_action[descriptor.action_id],
            rank=rank_policy_action(
                source_runtime.replay_input.policy.name,
                contexts_by_action[descriptor.action_id],
            ),
            policy_immediate_net_cost=contexts_by_action[descriptor.action_id].immediate_net_cost,
            selected_replay_event_net_cost=(
                fact.step.event.delta_costs.net_cost
                if descriptor.action_id == fact.step.descriptor.action_id
                else None
            ),
        )
        for position, descriptor in enumerate(standard_descriptors)
    )
    executable_identity_after = _capture_executable_identity(source_runtime)
    if executable_identity_after != executable_identity_before:
        raise ValueError("M8 Jagua executable changed during unchecked source capture")
    _require_unchecked_runtime_source_identity(
        runtime,
        semantic_runtime_sha256=semantic_runtime_sha256,
        runtime_authority=runtime_authority,
        operation="common capture",
    )
    jagua_sha256, jagua_size, jagua_mode = executable_identity_after
    fit_config_sha256 = "sha256:" + semantic_sha256(
        source_runtime.replay_input.fit_config.model_dump(mode="json")
    )
    search_config_sha256 = "sha256:" + semantic_sha256(
        source_runtime.replay_input.search_config.model_dump(mode="json")
    )
    source = M8UncheckedCommonSourceCapture(
        replay_input_id=source_runtime.replay_input.input_id,
        replay_input_sha256=source_runtime.replay_input.content_sha256,
        replay_input=source_runtime.replay_input,
        semantic_runtime_sha256=semantic_runtime_sha256,
        stream_id=source_runtime.replay_input.stream_id,
        stream_sha256=source_runtime.replay_input.stream_sha256,
        event_binding=binding,
        problem=problem,
        candidate_set=verified.evidence,
        verified_candidates=verified,
        fit_config=source_runtime.replay_input.fit_config,
        fit_config_sha256=fit_config_sha256,
        search_config=source_runtime.replay_input.search_config,
        search_config_sha256=search_config_sha256,
        rules=source_runtime.rules,
        rules_sha256=(f"sha256:{semantic_sha256(source_runtime.rules.model_dump(mode='json'))}"),
        collision_backend=source_runtime.replay_input.collision_backend,
        jagua_executable_sha256=jagua_sha256,
        jagua_executable_size_bytes=jagua_size,
        jagua_executable_mode_bits=jagua_mode,
    )
    return M8UncheckedProducerTransition(
        common_fact=fact,
        portable_transition=_portable_common_transition(fact),
        inventory_classifications=tuple(classifications),
        standard_candidates=standard_candidates,
        source=source,
    )


def _derive_m8_common_transition_fact_unprofiled(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    semantic_runtime_sha256: str,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
    differential: bool = False,
) -> M8CommonTransitionFact:
    source_runtime = (
        runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            runtime,
            event_position=cursor.next_event_position,
        )
    )
    fast = _try_derive_m8_common_transition_fact_fast(
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_runtime_sha256,
        prepared_layouts=prepared_layouts,
    )
    if fast is None:
        increment_profile_count("full_authoritative_fallbacks")
        return _derive_m8_common_transition_fact_authoritative(
            source_runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_runtime_sha256,
        )
    increment_profile_count(
        "frontier_rejected_inventory_items",
        len(fast.zero_generation_rejected_inventory),
    )
    increment_profile_count(
        "exact_survivor_inventory_items",
        len(fast.exact_survivor_inventory),
    )
    increment_profile_count(
        "counted_no_fit_inventory_items",
        len(fast.counted_no_fit_inventory),
    )
    if fast.counted_no_fit_inventory:
        binding = source_runtime.replay_input.instances[cursor.next_event_position]
        candidate_count = len(source_runtime.runtime_candidates[binding.problem_id].candidates)
        increment_profile_count("counted_no_fit_transitions")
        increment_profile_count(
            "counted_no_fit_candidate_searches",
            len(fast.counted_no_fit_inventory) * candidate_count,
        )
    if fast.exact_survivor_inventory:
        increment_profile_count("partially_pruned_transitions")
    elif not fast.counted_no_fit_inventory:
        increment_profile_count("frontier_rejected_transitions")
        increment_profile_count("standard_only_materializations")
    if differential:
        authoritative = _derive_m8_common_transition_fact_authoritative(
            source_runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_runtime_sha256,
        )
        if fast.fact != authoritative:
            increment_profile_count("differential_mismatches")
            raise ValueError("M8 fast common transition differs from authoritative replay")
    return fast.fact


def _derive_m8_common_transition_fact(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    semantic_runtime_sha256: str,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
    differential: bool = False,
) -> M8CommonTransitionFact:
    """Profile one authoritative common transition without changing its semantics."""

    with profile_phase("common_transition_derivation"):
        fact = _derive_m8_common_transition_fact_unprofiled(
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_runtime_sha256,
            prepared_layouts=prepared_layouts,
            differential=differential,
        )
    increment_profile_count("events")
    increment_profile_count("facts")
    return fact


def build_m8_common_transition_fact(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
) -> M8CommonTransitionFact:
    """Build one deterministic portable fact; callers must validate it before use."""

    try:
        _preflight_prepared_source_runtime(runtime)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: common fact runtime source capture"
        ) from error
    captured_cursor = _capture_replay_cursor_source(cursor)
    snapshot = snapshot_m7_replay_runtime(runtime)
    try:
        with snapshot.runtime_for_proof() as proof_runtime:
            fact = _derive_m8_common_transition_fact(
                proof_runtime,
                cursor=captured_cursor,
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
    *,
    deep: bool = False,
) -> _ValidatedCommonEntry:
    malformed_registry_keys = _sanitize_validated_common_registry_keys()
    registered = _VALIDATED_COMMON_REGISTRY.get(id(common))
    try:
        invalid = (
            malformed_registry_keys
            or type(common) is not ValidatedCommonTransition
            or common._provenance_token is not _VALIDATED_COMMON_PROVENANCE
            or type(registered) is not _ValidatedCommonEntry
            or type(registered.reference) is not weakref.ReferenceType
            or registered.reference() is not common
            or registered.binding_token is not common._binding_token
            or type(registered.owner_pid) is not int
            or registered.owner_pid != os.getpid()
            or type(registered.snapshot) is not M7SemanticRuntimeSnapshot
            or registered.snapshot._owner_pid != registered.owner_pid  # noqa: SLF001
            or type(registered.canonical_fact) is not M8CommonTransitionFact
            or registered.snapshot.semantic_sha256
            != registered.canonical_fact.semantic_runtime_sha256
            or type(registered.canonical_fact_binding) is not tuple
            or type(registered.integrity_sha256) is not str
            or type(registered.owns_snapshot) is not bool
            or (
                registered.authority is not None
                and type(registered.authority) is not M7AuthoritativeProofRuntime
            )
        )
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: validated common transition capability"
        ) from error
    if invalid:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: validated common transition capability"
        )
    try:
        current_binding = _common_fact_registry_binding(registered.canonical_fact)
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: common transition registry integrity differs"
        ) from error
    if registered.canonical_fact_binding != current_binding:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: common transition registry integrity differs"
        )
    if registered.authority is not None:
        try:
            registered.authority._require_active_identity()  # noqa: SLF001
            if registered.checker_token is not None:
                from yieldforge.oracle.fact_checker import _require_checker_registration_token

                _require_checker_registration_token(
                    registered.checker_token,  # type: ignore[arg-type]
                    registered.authority,
                    registered.canonical_fact,
                )
        except M8PreparedFrontierIntegrityError:
            raise
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: "
                "validated common transition authority capability"
            ) from error
    if deep:
        try:
            integrity_sha256 = _common_registry_integrity_sha256(
                registered.snapshot.runtime,
                registered.canonical_fact,
            )
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: "
                "common transition registry integrity differs"
            ) from error
        if registered.integrity_sha256 != integrity_sha256:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: "
                "common transition registry integrity differs"
            )
    return registered


def _register_validated_common_transition(
    fact: M8CommonTransitionFact,
    snapshot: M7SemanticRuntimeSnapshot,
    *,
    registration_provenance: object,
    authority: M7AuthoritativeProofRuntime | None = None,
    owns_snapshot: bool = True,
    checker_token: object | None = None,
) -> ValidatedCommonTransition:
    if registration_provenance is not _VALIDATED_COMMON_REGISTRATION_PROVENANCE:
        raise ValueError("M8 common registration lacks internal provenance")
    canonical_fact = deepcopy(fact)
    binding_token = object()
    validated = ValidatedCommonTransition(
        _provenance_token=_VALIDATED_COMMON_PROVENANCE,
        _binding_token=binding_token,
    )
    key = id(validated)

    def discard(reference: weakref.ReferenceType[ValidatedCommonTransition]) -> None:
        _sanitize_validated_common_registry_keys()
        registered = _VALIDATED_COMMON_REGISTRY.get(key)
        if type(registered) is _ValidatedCommonEntry and registered.reference is reference:
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
        canonical_fact_binding=_common_fact_registry_binding(canonical_fact),
        integrity_sha256=_common_registry_integrity_sha256(
            snapshot.runtime,
            canonical_fact,
        ),
        checker_token=checker_token,
    )
    return validated


def _register_checker_validated_common_transition(
    fact: M8CommonTransitionFact,
    authority: M7AuthoritativeProofRuntime,
    *,
    checker_token: object,
) -> ValidatedCommonTransition:
    """Checker-only v2 authority boundary guarded by an active private token."""

    from yieldforge.oracle.fact_checker import _consume_checker_registration_token

    result = None
    try:
        authority.require_active(authority.runtime)
        _consume_checker_registration_token(checker_token, authority, fact)  # type: ignore[arg-type]
        result = _register_validated_common_transition(
            fact,
            authority._snapshot,  # noqa: SLF001 - capability shares checker authority lifetime.
            registration_provenance=_VALIDATED_COMMON_REGISTRATION_PROVENANCE,
            authority=authority,
            owns_snapshot=False,
            checker_token=checker_token,
        )
        authority.require_active(authority.runtime)
        return result
    except BaseException as body_error:
        cleanup_error = None
        if result is not None:
            try:
                _release_validated_common_transition(result)
            except BaseException as error:
                cleanup_error = error
        if isinstance(body_error, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(body_error, M8PreparedFrontierIntegrityError):
            raise body_error
        integrity_error = M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: checker common capability registration"
        )
        cause = cleanup_error or body_error
        raise integrity_error from cause


def _release_validated_common_transition(common: ValidatedCommonTransition) -> None:
    """Deep-check and retire one event-scoped private common fact."""

    malformed_registry_keys = _sanitize_validated_common_registry_keys()
    registered = _VALIDATED_COMMON_REGISTRY.get(id(common))
    integrity_error: M8PreparedFrontierIntegrityError | None = None
    try:
        if (
            malformed_registry_keys
            or type(registered) is not _ValidatedCommonEntry
            or _registered_common_entry(common, deep=True) is not registered
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: "
                "common transition registry integrity differs"
            )
    except M8PreparedFrontierIntegrityError as error:
        integrity_error = error
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
        integrity_error = M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: common transition registry integrity differs"
        )
        integrity_error.__cause__ = error
    finally:
        _sanitize_validated_common_registry_keys()
        _VALIDATED_COMMON_REGISTRY.pop(id(common), None)
        if type(registered) is _ValidatedCommonEntry and registered.owns_snapshot:
            try:
                registered.snapshot.close()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
                if integrity_error is None:
                    integrity_error = M8PreparedFrontierIntegrityError(
                        "M8 prepared frontier integrity differs: common transition snapshot cleanup"
                    )
                    integrity_error.__cause__ = error
    if integrity_error is not None:
        raise integrity_error


def build_validated_m8_common_transition(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
) -> ValidatedCommonTransition:
    """Reconstruct one exact local common transition and issue a process capability."""

    try:
        _preflight_prepared_source_runtime(runtime)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: common capability runtime source capture"
        ) from error
    captured_cursor = _capture_replay_cursor_source(cursor)
    snapshot = snapshot_m7_replay_runtime(runtime)
    registered = False
    try:
        with snapshot.runtime_for_proof() as proof_runtime:
            fact = _derive_m8_common_transition_fact(
                proof_runtime,
                cursor=captured_cursor,
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
        result = _register_validated_common_transition(
            fact,
            snapshot,
            registration_provenance=_VALIDATED_COMMON_REGISTRATION_PROVENANCE,
        )
        registered = True
        return result
    finally:
        if not registered:
            snapshot.close()


def build_validated_m8_common_transition_in_context(
    authority: M7AuthoritativeProofRuntime,
    *,
    cursor: M7ReplayCursor,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
    differential: bool = False,
) -> ValidatedCommonTransition:
    """Derive one common capability inside an active shared proof runtime."""

    captured_cursor = _capture_replay_cursor_source(cursor)
    authority._require_active_identity()  # noqa: SLF001 - bounded prepared operation.
    fact = _derive_m8_common_transition_fact(
        authority.runtime,
        cursor=captured_cursor,
        semantic_runtime_sha256=authority.semantic_sha256,
        prepared_layouts=prepared_layouts,
        differential=differential,
    )
    validation_runtime = (
        authority.runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            authority.runtime,
            event_position=captured_cursor.next_event_position,
        )
    )
    _validate_portable_common_transition_fact(
        validation_runtime,
        fact,
        semantic_runtime_sha256=authority.semantic_sha256,
    )
    authority._require_active_identity()  # noqa: SLF001 - bounded prepared operation.
    return _register_validated_common_transition(
        fact,
        authority._snapshot,  # noqa: SLF001 - capability shares the authority lifetime.
        registration_provenance=_VALIDATED_COMMON_REGISTRATION_PROVENANCE,
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

    captured_cursor = _capture_replay_cursor_source(cursor)
    submitted_portable, submitted_content_sha256 = _capture_common_transition_fact_source(
        runtime,
        fact,
    )
    snapshot = snapshot_m7_replay_runtime(runtime)
    registered = False
    try:
        with snapshot.runtime_for_proof() as proof_runtime:
            authoritative = _derive_m8_common_transition_fact(
                proof_runtime,
                cursor=captured_cursor,
                semantic_runtime_sha256=snapshot.semantic_sha256,
            )
            _validate_portable_common_transition_fact(
                proof_runtime,
                authoritative,
                semantic_runtime_sha256=snapshot.semantic_sha256,
            )
            if (
                submitted_content_sha256 != authoritative.content_sha256
                or submitted_portable != _portable_common_transition(authoritative)
            ):
                raise ValueError(
                    "M8 common transition fact differs from authoritative common transition"
                )
            _require_caller_runtime_stable(
                runtime,
                expected_sha256=snapshot.semantic_sha256,
                operation="M8 common capability validation",
            )
        result = _register_validated_common_transition(
            authoritative,
            snapshot,
            registration_provenance=_VALIDATED_COMMON_REGISTRATION_PROVENANCE,
        )
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
    try:
        _preflight_prepared_source_runtime(runtime)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            f"M8 prepared frontier integrity differs: {operation} runtime source"
        ) from error
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
        try:
            authority._require_active_identity(runtime)  # noqa: SLF001
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: "
                "validated common transition authority capability"
            ) from error
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
                    "M8 shared fit-search cache value differs from authoritative registered search"
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


def _capture_unchecked_competitor_source(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    cursor_template: M7ReplayCursor,
) -> tuple[
    M7ActionDescriptor | None,
    ActionPolicyContext | None,
    tuple[LayoutFitSearchResult, ...],
    tuple[M8UncheckedTranslationBatchCapture, ...],
]:
    """Search one exact source sequence once, then consume those results."""

    binding = runtime.replay_input.instances[event_position]
    problem = next(
        source
        for source in runtime.replay_input.problems
        if source.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    prepared_remnant = prepare_remnant_geometry(item.remnant)
    layouts = tuple(
        prepare_layout_footprint(
            problem.problem,
            candidate,
            runtime.replay_input.fit_config,
        )
        for candidate in verified.candidates
    )
    sources = tuple(
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
    authoritative = tuple(
        search_layout_translation(
            item.remnant,
            problem.problem,
            candidate,
            material=binding.material,
            fit_config=runtime.replay_input.fit_config,
            search_config=runtime.replay_input.search_config,
            prepared_layout=layout,
            prepared_remnant=prepared_remnant,
            translation_candidates=source,
        )
        for candidate, layout, source in zip(
            verified.candidates,
            layouts,
            sources,
            strict=True,
        )
    )
    fresh = _fresh_runtime(runtime)
    _seed_fit_searches(
        fresh,
        event_position=event_position,
        item=item,
        searches=authoritative,
    )
    competitor, context = enumerate_m7_single_remnant_competitor(
        fresh,
        event_position=event_position,
        item=item,
        cursor_template=cursor_template,
    )
    _require_cached_searches_match_authoritative(
        runtime,
        event_position=event_position,
        item=item,
        authoritative=authoritative,
    )
    return (
        competitor,
        context,
        authoritative,
        tuple(M8UncheckedTranslationBatchCapture.from_source(source) for source in sources),
    )


def _evidence_payload(
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
) -> dict[str, object]:
    """Build the exact legacy influence preimage before digest reduction.

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
    return {
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
    """Commit one influence through already-validated parent commitments."""

    payload = _evidence_payload(
        event_position=event_position,
        remnant_id=remnant_id,
        classification=classification,
        direction=direction,
        delta=delta,
        common=common,
        common_decision_key=common_decision_key,
        common_fact_sha256=common_fact_sha256,
        branch_action_id=branch_action_id,
        state_before_sha256=state_before_sha256,
        state_after_sha256=state_after_sha256,
        rejections=rejections,
        searches=searches,
        competitor=competitor,
        competitor_rank=competitor_rank,
    )
    return f"sha256:{semantic_sha256(payload)}"


def _calculate_influence_source(
    runtime: M7ReplayRuntime,
    *,
    cursor_template: M7ReplayCursor,
    event_position: int,
    item: InventoryItem,
    direction: Literal["added", "removed"],
    delta: BranchInventoryDelta,
    common: M7PolicyActionBinding,
    common_rank: PolicyRank,
    common_fact_sha256: str,
    common_action_id: str,
    branch_action_id: str,
    state_before_sha256: str,
    state_after_sha256: str,
    prepared_layouts: _PreparedTranslationLayoutBatch | None,
    mode: _CommonDerivationMode,
) -> tuple[M8UncheckedInfluenceCapture, int]:
    source_runtime = (
        runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
    )
    rejections = (
        compile_translation_rejections(
            source_runtime,
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
            M8UncheckedInfluenceCapture(
                event_position=event_position,
                item=item,
                direction=direction,
                delta=delta,
                common=common,
                common_rank=common_rank,
                common_fact_sha256=common_fact_sha256,
                common_action_id=common_action_id,
                branch_action_id=branch_action_id,
                state_before_sha256=state_before_sha256,
                state_after_sha256=state_after_sha256,
                rejections=rejections,
                searches=(),
                translation_batches=(),
                competitor=None,
                competitor_context=None,
                competitor_rank=None,
                classification="no_fit",
                legacy_evidence_sha256=digest,
            ),
            0,
        )

    if mode is _CommonDerivationMode.UNCHECKED_PORTABLE:
        (
            competitor,
            context,
            authoritative_searches,
            translation_batches,
        ) = _capture_unchecked_competitor_source(
            source_runtime,
            event_position=event_position,
            item=item,
            cursor_template=cursor_template,
        )
    else:
        competitor, context, authoritative_searches = _authoritative_competitor(
            source_runtime,
            event_position=event_position,
            item=item,
            cursor_template=cursor_template,
        )
        translation_batches = ()
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
            M8UncheckedInfluenceCapture(
                event_position=event_position,
                item=item,
                direction=direction,
                delta=delta,
                common=common,
                common_rank=common_rank,
                common_fact_sha256=common_fact_sha256,
                common_action_id=common_action_id,
                branch_action_id=branch_action_id,
                state_before_sha256=state_before_sha256,
                state_after_sha256=state_after_sha256,
                rejections=rejections,
                searches=authoritative_searches,
                translation_batches=translation_batches,
                competitor=None,
                competitor_context=None,
                competitor_rank=None,
                classification="no_fit",
                legacy_evidence_sha256=digest,
            ),
            1,
        )

    competitor_rank = rank_policy_action(source_runtime.replay_input.policy.name, context)
    if not common_rank <= competitor_rank:
        return (
            M8UncheckedInfluenceCapture(
                event_position=event_position,
                item=item,
                direction=direction,
                delta=delta,
                common=common,
                common_rank=common_rank,
                common_fact_sha256=common_fact_sha256,
                common_action_id=common_action_id,
                branch_action_id=branch_action_id,
                state_before_sha256=state_before_sha256,
                state_after_sha256=state_after_sha256,
                rejections=rejections,
                searches=authoritative_searches,
                translation_batches=translation_batches,
                competitor=competitor,
                competitor_context=context,
                competitor_rank=competitor_rank,
                classification="policy_not_dominated",
                legacy_evidence_sha256=None,
            ),
            1,
        )
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
        M8UncheckedInfluenceCapture(
            event_position=event_position,
            item=item,
            direction=direction,
            delta=delta,
            common=common,
            common_rank=common_rank,
            common_fact_sha256=common_fact_sha256,
            common_action_id=common_action_id,
            branch_action_id=branch_action_id,
            state_before_sha256=state_before_sha256,
            state_after_sha256=state_after_sha256,
            rejections=rejections,
            searches=authoritative_searches,
            translation_batches=translation_batches,
            competitor=competitor,
            competitor_context=context,
            competitor_rank=competitor_rank,
            classification="policy_dominated",
            legacy_evidence_sha256=digest,
        ),
        1,
    )


def _influence(
    runtime: M7ReplayRuntime,
    *,
    cursor_template: M7ReplayCursor,
    event_position: int,
    item: InventoryItem,
    direction: Literal["added", "removed"],
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
    """Trusted-local wrapper that reduces the shared source to the v1 witness."""

    captured, search_count = _calculate_influence_source(
        runtime,
        cursor_template=cursor_template,
        event_position=event_position,
        item=item,
        direction=direction,
        delta=delta,
        common=common,
        common_rank=common_rank,
        common_fact_sha256=common_fact_sha256,
        common_action_id=common_action_id,
        branch_action_id=branch_action_id,
        state_before_sha256=state_before_sha256,
        state_after_sha256=state_after_sha256,
        prepared_layouts=prepared_layouts,
        mode=_CommonDerivationMode.TRUSTED_LOCAL,
    )
    if captured.classification == "policy_not_dominated":
        return None, search_count
    digest = captured.legacy_evidence_sha256
    if digest is None:  # pragma: no cover - typed classification closes this branch.
        raise AssertionError("M8 passive influence lacks its legacy digest")
    if captured.classification == "no_fit":
        return (
            M8InfluenceWitness(
                remnant_id=captured.remnant_id,
                classification="no_fit",
                evidence_sha256=digest,
                common_action_id=common_action_id,
                common_catalog_action_id=common.catalog_action_id,
                common_decision_key=common_rank.decision_key,
            ),
            search_count,
        )
    competitor = captured.competitor
    competitor_rank = captured.competitor_rank
    if competitor is None or competitor.evidence is None or competitor_rank is None:
        raise AssertionError("M8 policy-dominated capture lacks competitor evidence")
    return (
        M8InfluenceWitness(
            remnant_id=captured.remnant_id,
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
        search_count,
    )


def _build_passive_event_result(
    *,
    transition: M7CursorTransition,
    event_position: int,
    common_action_id: str,
    branch_action_id: str,
    build_influences: Callable[[str], tuple[tuple[M8InfluenceWitness, ...] | None, int]],
) -> EventPassivityResult:
    """Bind one authoritative applied transition to both witness and result."""

    state_before_sha256 = transition.cursor_before_sha256
    state_after_sha256 = transition.cursor_after_sha256
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
        branch_after=transition.cursor,
        exact_search_count=exact_search_count,
        branch_after_sha256=state_after_sha256,
    )


def _capture_unchecked_event_passivity_body(
    runtime: M7ReplayRuntime,
    *,
    common: M8UncheckedProducerTransition,
    captured_branch_cursor: M7ReplayCursor,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
) -> M8UncheckedEventPassivityCapture:
    """Build one unchecked branch result from caller state detached above the boundary."""

    if type(common) is not M8UncheckedProducerTransition:
        raise ValueError("M8 unchecked traversal requires a producer-only transition record")
    fact = common.common_fact
    source_runtime = (
        runtime
        if prepared_layouts is None
        else _prepared_source_runtime(
            prepared_layouts,
            runtime,
            event_position=fact.event_position,
        )
    )
    if (
        fact.semantic_runtime_sha256 != common.source.semantic_runtime_sha256
        or fact.replay_input_id != source_runtime.replay_input.input_id
        or fact.replay_input_sha256 != source_runtime.replay_input.content_sha256
    ):
        raise ValueError("M8 unchecked producer transition differs from runtime context")
    delta = _derive_branch_inventory_delta(fact.cursor_before, captured_branch_cursor)
    if not delta.added and not delta.removed:
        return M8UncheckedEventPassivityCapture(
            passive=False,
            classification=None,
            branch_after=None,
            state_before_sha256=None,
            state_after_sha256=None,
            influences=(),
            exact_search_count=0,
        )
    binding = fact.step.action_binding
    common_action_id = fact.step.event.action.action_id
    if binding.context.selected_stock_id in set(_item_ids(delta.removed)):
        return M8UncheckedEventPassivityCapture(
            passive=False,
            classification=None,
            branch_after=None,
            state_before_sha256=None,
            state_after_sha256=None,
            influences=(),
            exact_search_count=0,
        )
    verified = source_runtime.runtime_candidates[
        source_runtime.replay_input.instances[fact.event_position].problem_id
    ]
    if not _verified_rejection_layouts_cover_candidates(verified):
        return M8UncheckedEventPassivityCapture(
            passive=False,
            classification=None,
            branch_after=None,
            state_before_sha256=None,
            state_after_sha256=None,
            influences=(),
            exact_search_count=0,
        )
    transition = apply_m7_frozen_action_evidence_with_commitments(
        source_runtime,
        cursor=captured_branch_cursor,
        event_position=fact.event_position,
        action=fact.step.event.action,
    )
    influences = []
    exact_search_count = 0
    for direction, items in (("added", delta.added), ("removed", delta.removed)):
        for item in items:
            captured, searches = _calculate_influence_source(
                runtime,
                cursor_template=fact.cursor_before,
                event_position=fact.event_position,
                item=item,
                direction=direction,
                delta=delta,
                common=binding,
                common_rank=fact.policy_rank,
                common_fact_sha256=fact.content_sha256,
                common_action_id=common_action_id,
                branch_action_id=common_action_id,
                state_before_sha256=transition.cursor_before_sha256,
                state_after_sha256=transition.cursor_after_sha256,
                prepared_layouts=prepared_layouts,
                mode=_CommonDerivationMode.UNCHECKED_PORTABLE,
            )
            exact_search_count += searches
            influences.append(captured)
            if captured.classification == "policy_not_dominated":
                return M8UncheckedEventPassivityCapture(
                    passive=False,
                    classification=None,
                    branch_after=None,
                    state_before_sha256=transition.cursor_before_sha256,
                    state_after_sha256=transition.cursor_after_sha256,
                    influences=tuple(influences),
                    exact_search_count=exact_search_count,
                )
    classification: Literal["no_fit", "policy_dominated"] = (
        "no_fit"
        if all(item.classification == "no_fit" for item in influences)
        else "policy_dominated"
    )
    return M8UncheckedEventPassivityCapture(
        passive=True,
        classification=classification,
        branch_after=transition.cursor,
        state_before_sha256=transition.cursor_before_sha256,
        state_after_sha256=transition.cursor_after_sha256,
        influences=tuple(influences),
        exact_search_count=exact_search_count,
    )


def _capture_unchecked_event_passivity(
    runtime: M7ReplayRuntime,
    *,
    common: M8UncheckedProducerTransition,
    branch_cursor: M7ReplayCursor,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
    prepared_source_guard: _M8UncheckedPreparedSourceGuard | None = None,
) -> M8UncheckedEventPassivityCapture:
    """Traverse one branch using only a producer record and return unchecked source."""

    captured_branch_cursor = _capture_replay_cursor_source(branch_cursor)
    if type(common) is not M8UncheckedProducerTransition:
        raise ValueError("M8 unchecked traversal requires a producer-only transition record")
    expected_executable_identity = (
        common.source.jagua_executable_sha256,
        common.source.jagua_executable_size_bytes,
        common.source.jagua_executable_mode_bits,
    )

    def require_source_integrity() -> None:
        if prepared_source_guard is not None:
            if prepared_layouts is None:
                raise ValueError("M8 unchecked prepared traversal lacks exact layout scope")
            _require_unchecked_prepared_source_guard(
                prepared_source_guard,
                runtime=runtime,
                common=common,
                prepared_layouts=prepared_layouts,
            )
            return
        if _capture_executable_identity(runtime) != expected_executable_identity:
            raise ValueError("M8 unchecked traversal Jagua executable binding differs")
        _require_unchecked_runtime_source_identity(
            runtime,
            semantic_runtime_sha256=common.common_fact.semantic_runtime_sha256,
            runtime_authority=None,
            operation="traversal",
        )

    require_source_integrity()
    try:
        return _capture_unchecked_event_passivity_body(
            runtime,
            common=common,
            captured_branch_cursor=captured_branch_cursor,
            prepared_layouts=prepared_layouts,
        )
    finally:
        require_source_integrity()


def certify_event_passivity(
    runtime: M7ReplayRuntime,
    *,
    common: ValidatedCommonTransition,
    branch_cursor: M7ReplayCursor,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
) -> EventPassivityResult:
    """Prove one branch event selects the exact common M7 action, or fail closed."""

    captured_branch_cursor = _capture_replay_cursor_source(branch_cursor)
    fact, snapshot, authority = _require_validated_common_transition(runtime, common)
    proof_context = (
        nullcontext(authority.runtime) if authority is not None else snapshot.runtime_for_proof()
    )
    with proof_context as proof_runtime:
        try:
            source_runtime = (
                proof_runtime
                if prepared_layouts is None
                else _prepared_source_runtime(
                    prepared_layouts,
                    proof_runtime,
                    event_position=fact.event_position,
                )
            )
            delta = _derive_branch_inventory_delta(
                fact.cursor_before,
                captured_branch_cursor,
            )
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

            transition = apply_m7_frozen_action_evidence_with_commitments(
                source_runtime,
                cursor=captured_branch_cursor,
                event_position=fact.event_position,
                action=fact.step.event.action,
            )
            state_before_sha256 = transition.cursor_before_sha256
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
                transition=transition,
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
                try:
                    authority._require_active_identity(runtime)  # noqa: SLF001
                except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
                    raise M8PreparedFrontierIntegrityError(
                        "M8 prepared frontier integrity differs: "
                        "certificate proof authority capability"
                    ) from error


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
