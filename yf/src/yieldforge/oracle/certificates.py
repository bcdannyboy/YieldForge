"""Exact no-fit and policy-dominance certificates for one M8 future event."""

from __future__ import annotations

import hashlib
import os
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass, field
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
    ReusableGeometryProblem,
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
from yieldforge.baseline.jagua import (
    JaguaRepresentationError,
    run_jagua_generated_prefilter,
)
from yieldforge.baseline.policies import ActionPolicyContext, PolicyRank, rank_policy_action
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
from yieldforge.oracle.compiled import (
    CompiledRejectionProblem,
    CompiledTranslationRejection,
    _compile_prepared_translation_rejections,
    _prepared_rejection_problem,
    _prepared_standard_winner,
    _PreparedTranslationLayoutBatch,
    _registered_prepared_remnant_measurement,
    compile_rejection_problem,
    compile_translation_rejections,
)
from yieldforge.oracle.concurrency import (
    activate_m8_local_trusted_audit,
    require_m8_translation_audit_processes,
)
from yieldforge.oracle.frontier import ParetoFrontier, certify_frontier_impossible
from yieldforge.oracle.profiling import increment_profile_count, profile_phase
from yieldforge.oracle.proofs import M8EventWitness, M8InfluenceWitness
from yieldforge.oracle.translation_count_audit import audit_layout_translation_batch
from yieldforge.replay.contracts import InventoryItem
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.reuse.contracts import RemnantFitConfig
from yieldforge.reuse.geometry import material_key


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
    integrity_sha256: str
    checker_token: object | None


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


def _zero_generation_rejection_witness(
    runtime: M7ReplayRuntime,
    *,
    compiled: CompiledRejectionProblem,
    event_position: int,
    item: InventoryItem,
    prepared_layouts: _PreparedTranslationLayoutBatch | None,
) -> FastCommonRejectionWitness | None:
    binding = runtime.replay_input.instances[event_position]
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
    fit_config = runtime.replay_input.fit_config
    area_tolerance = max(
        fit_config.coordinate_tolerance,
        measured.area * fit_config.relative_area_tolerance,
    )
    material_matches = material_key(item.remnant.material) == material_key(binding.material)
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
        remnant_id=item.remnant.remnant_id,
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
) -> _ScalarNoFitSource:
    """Generate exact search counts while preserving their ordered source batches."""

    binding = runtime.replay_input.instances[event_position]
    if material_key(item.remnant.material) != material_key(binding.material):
        return _ScalarNoFitSource(searches=(), translation_batches=())
    problem = next(
        problem
        for problem in runtime.replay_input.problems
        if problem.problem_id == binding.problem_id
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
    certificates = tuple(
        certify_translation_impossible(
            layout,
            item.remnant,
            material=binding.material,
            fit_config=runtime.replay_input.fit_config,
        )
        for layout in layouts
    )
    if any(not certificate.impossible for certificate in certificates):
        return _ScalarNoFitSource(searches=None, translation_batches=())
    rust_generated = runtime.jagua_executable is not None and not any(
        polygon.interiors for layout in layouts for polygon in layout.part_polygons
    )
    if rust_generated:
        try:
            generated = run_jagua_generated_prefilter(
                runtime.jagua_executable,
                remnant=prepared_remnant,
                layouts=layouts,
                fit_config=runtime.replay_input.fit_config,
                search_config=runtime.replay_input.search_config,
                container_guard=runtime.replay_input.jagua_container_guard or 1.0,
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
                        fit_config=runtime.replay_input.fit_config,
                        search_config=runtime.replay_input.search_config,
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
                item.remnant,
                candidate,
                fit_config=runtime.replay_input.fit_config,
                search_config=runtime.replay_input.search_config,
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
                remnant_id=item.remnant.remnant_id,
                config=runtime.replay_input.search_config,
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
) -> tuple[LayoutFitSearchResult, ...] | None:
    """Trusted-local count synthesis retained for existing v1 capability paths."""

    return _synthesize_scalar_no_fit_source(
        runtime,
        event_position=event_position,
        item=item,
        mode=_CommonDerivationMode.TRUSTED_LOCAL,
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
    compiled_standard = (
        None
        if prepared_layouts is None
        else _prepared_standard_winner(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
    )
    compiled = None
    if cursor.inventory:
        if prepared_layouts is None:
            binding = runtime.replay_input.instances[event_position]
            verified = runtime.runtime_candidates[binding.problem_id]
            if not verified.rejection_layouts or tuple(
                item.candidate_id for item in verified.rejection_layouts
            ) != tuple(item.candidate_id for item in verified.candidates):
                return None
            compiled = compile_rejection_problem(runtime, event_position=event_position)
        else:
            compiled = _prepared_rejection_problem(
                prepared_layouts,
                runtime,
                event_position=event_position,
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
        runtime,
        event_position=event_position,
        inventory=tuple(rejected),
    )
    execution_runtime = _fresh_runtime(runtime)
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
            runtime,
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
                runtime,
                authoritative_runtime=execution_runtime,
                event_position=event_position,
                inventory=tuple(survivors),
            )
    if compiled_standard is not None and not survivors:
        selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
        descriptor = next(item for item in catalog.actions if item.action_id == selection.action_id)
        if (
            selection.action_id != compiled_standard.action_id
            or selection.decision_key != compiled_standard.decision_key
            or descriptor.candidate_id != compiled_standard.candidate_id
        ):
            raise ValueError("M8 prepared standard winner differs from exact profiles")
    fact = _common_transition_fact_from_catalog(
        runtime,
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


def _require_unchecked_prepared_source_guard(
    guard: _M8UncheckedPreparedSourceGuard,
    *,
    runtime: M7ReplayRuntime,
    common: M8UncheckedProducerTransition,
    prepared_layouts: _PreparedTranslationLayoutBatch,
    scope_owner: object | None = None,
) -> None:
    """Perform only scope and object-identity checks inside the branch hot loop."""

    registered = _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.get(id(guard))
    if (
        type(guard) is not _M8UncheckedPreparedSourceGuard
        or registered is None
        or registered.reference() is not guard
        or registered.owner_pid != os.getpid()
        or registered.token is not guard._token  # noqa: SLF001
        or registered.runtime is not runtime
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
    ):
        raise ValueError("M8 unchecked prepared source guard is invalid or inactive")


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
        registered = _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.get(key)
        if registered is not None and registered.reference is reference:
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
    try:
        yield guard
    finally:
        integrity_error: ValueError | None = None
        try:
            _require_unchecked_prepared_source_guard(
                guard,
                runtime=runtime,
                common=common,
                scope_owner=scope_owner,
                prepared_layouts=prepared_layouts,
            )
        except (AttributeError, TypeError, ValueError) as error:
            integrity_error = ValueError("M8 unchecked prepared source guard integrity differs")
            integrity_error.__cause__ = error
        current = _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.get(key)
        if current is registered:
            _UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.pop(key, None)
        elif integrity_error is None:
            integrity_error = ValueError("M8 unchecked prepared source guard cleanup differs")
        try:
            # This detects persistent boundary changes. A swap restored between reads remains
            # explicitly unchecked producer provenance for the fresh Task-5 checker to audit.
            require_expensive_boundary()
        except (AttributeError, OSError, TypeError, ValueError) as error:
            if integrity_error is None:
                integrity_error = ValueError(
                    "M8 unchecked prepared source changed during branch traversal"
                )
                integrity_error.__cause__ = error
        if integrity_error is not None:
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
    runtime_authority.require_active(runtime)
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

    _require_unchecked_runtime_source_identity(
        runtime,
        semantic_runtime_sha256=semantic_runtime_sha256,
        runtime_authority=runtime_authority,
        operation="common capture",
    )
    executable_identity_before = _capture_executable_identity(runtime)
    event_position = cursor.next_event_position
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    compiled_standard = (
        None
        if prepared_layouts is None
        else _prepared_standard_winner(
            prepared_layouts,
            runtime,
            event_position=event_position,
        )
    )
    compiled = None
    if cursor.inventory:
        if prepared_layouts is None:
            if not verified.rejection_layouts or tuple(
                item.candidate_id for item in verified.rejection_layouts
            ) != tuple(item.candidate_id for item in verified.candidates):
                compiled = None
            else:
                compiled = compile_rejection_problem(runtime, event_position=event_position)
        else:
            compiled = _prepared_rejection_problem(
                prepared_layouts,
                runtime,
                event_position=event_position,
            )

    rejected: list[InventoryItem] = []
    counted_no_fit: list[InventoryItem] = []
    counted_searches: dict[str, tuple[LayoutFitSearchResult, ...]] = {}
    survivors: list[InventoryItem] = []
    classifications: list[M8UncheckedCommonInventoryCapture] = []
    for item in cursor.inventory:
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
            runtime.replay_input.fit_config.coordinate_tolerance,
            measured.area * runtime.replay_input.fit_config.relative_area_tolerance,
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
                    coordinate_tolerance=runtime.replay_input.fit_config.coordinate_tolerance,
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
                    coordinate_tolerance=runtime.replay_input.fit_config.coordinate_tolerance,
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
                coordinate_tolerance=runtime.replay_input.fit_config.coordinate_tolerance,
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

    execution_runtime = _fresh_runtime(runtime)
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
            cursor=cursor,
            complete=False,
        )
        _require_common_search_caches_match_authoritative(
            runtime,
            authoritative_runtime=execution_runtime,
            event_position=event_position,
            inventory=cursor.inventory,
        )
    elif survivors and rejected:
        catalog = enumerate_m7_pruned_action_catalog(
            execution_runtime,
            cursor=cursor,
            zero_generation_rejected_inventory=tuple(rejected),
            precomputed_standard_profiles=standard_profiles,
        )
        _require_common_search_caches_match_authoritative(
            runtime,
            authoritative_runtime=execution_runtime,
            event_position=event_position,
            inventory=tuple(survivors),
        )
    elif survivors:
        catalog = enumerate_m7_action_catalog(
            execution_runtime,
            cursor=cursor,
            complete=False,
        )
        _require_common_search_caches_match_authoritative(
            runtime,
            authoritative_runtime=execution_runtime,
            event_position=event_position,
            inventory=cursor.inventory,
        )
    else:
        catalog = enumerate_m7_standard_only_catalog(
            execution_runtime,
            cursor=cursor,
            zero_generation_rejected_inventory=tuple(rejected),
            precomputed_standard_profiles=standard_profiles,
        )
    fact = _common_transition_fact_from_catalog(
        runtime,
        cursor=cursor,
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
                runtime.replay_input.policy.name,
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
    executable_identity_after = _capture_executable_identity(runtime)
    if executable_identity_after != executable_identity_before:
        raise ValueError("M8 Jagua executable changed during unchecked source capture")
    _require_unchecked_runtime_source_identity(
        runtime,
        semantic_runtime_sha256=semantic_runtime_sha256,
        runtime_authority=runtime_authority,
        operation="common capture",
    )
    jagua_sha256, jagua_size, jagua_mode = executable_identity_after
    fit_config_sha256 = (
        f"sha256:{semantic_sha256(runtime.replay_input.fit_config.model_dump(mode='json'))}"
    )
    search_config_sha256 = (
        f"sha256:{semantic_sha256(runtime.replay_input.search_config.model_dump(mode='json'))}"
    )
    source = M8UncheckedCommonSourceCapture(
        replay_input_id=runtime.replay_input.input_id,
        replay_input_sha256=runtime.replay_input.content_sha256,
        replay_input=runtime.replay_input,
        semantic_runtime_sha256=semantic_runtime_sha256,
        stream_id=runtime.replay_input.stream_id,
        stream_sha256=runtime.replay_input.stream_sha256,
        event_binding=binding,
        problem=problem,
        candidate_set=verified.evidence,
        verified_candidates=verified,
        fit_config=runtime.replay_input.fit_config,
        fit_config_sha256=fit_config_sha256,
        search_config=runtime.replay_input.search_config,
        search_config_sha256=search_config_sha256,
        rules=runtime.rules,
        rules_sha256=f"sha256:{semantic_sha256(runtime.rules.model_dump(mode='json'))}",
        collision_backend=runtime.replay_input.collision_backend,
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
    fast = _try_derive_m8_common_transition_fact_fast(
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_runtime_sha256,
        prepared_layouts=prepared_layouts,
    )
    if fast is None:
        increment_profile_count("full_authoritative_fallbacks")
        return _derive_m8_common_transition_fact_authoritative(
            runtime,
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
        binding = runtime.replay_input.instances[cursor.next_event_position]
        candidate_count = len(runtime.runtime_candidates[binding.problem_id].candidates)
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
            runtime,
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
        or registered.snapshot.semantic_sha256 != registered.canonical_fact.semantic_runtime_sha256
    ):
        raise ValueError("M8 certifier requires a validated common transition capability")
    if registered.authority is not None:
        registered.authority._require_active_identity()  # noqa: SLF001
        if registered.checker_token is not None:
            from yieldforge.oracle.fact_checker import _require_checker_registration_token

            _require_checker_registration_token(
                registered.checker_token,  # type: ignore[arg-type]
                registered.authority,
                registered.canonical_fact,
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

    authority.require_active(authority.runtime)
    _consume_checker_registration_token(checker_token, authority, fact)  # type: ignore[arg-type]
    result = None
    try:
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
    except BaseException:
        if result is not None:
            _release_validated_common_transition(result)
        raise


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

    authority._require_active_identity()  # noqa: SLF001 - bounded prepared operation.
    fact = _derive_m8_common_transition_fact(
        authority.runtime,
        cursor=cursor,
        semantic_runtime_sha256=authority.semantic_sha256,
        prepared_layouts=prepared_layouts,
        differential=differential,
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
            runtime,
            event_position=event_position,
            item=item,
            cursor_template=cursor_template,
        )
    else:
        competitor, context, authoritative_searches = _authoritative_competitor(
            runtime,
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

    competitor_rank = rank_policy_action(runtime.replay_input.policy.name, context)
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
    branch_cursor: M7ReplayCursor,
    prepared_layouts: _PreparedTranslationLayoutBatch | None = None,
) -> M8UncheckedEventPassivityCapture:
    """Build one unchecked branch result beneath the source-integrity boundary."""

    if type(common) is not M8UncheckedProducerTransition:
        raise ValueError("M8 unchecked traversal requires a producer-only transition record")
    fact = common.common_fact
    if (
        fact.semantic_runtime_sha256 != common.source.semantic_runtime_sha256
        or fact.replay_input_id != runtime.replay_input.input_id
        or fact.replay_input_sha256 != runtime.replay_input.content_sha256
    ):
        raise ValueError("M8 unchecked producer transition differs from runtime context")
    delta = _derive_branch_inventory_delta(fact.cursor_before, branch_cursor)
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
    transition = apply_m7_frozen_action_evidence_with_commitments(
        runtime,
        cursor=branch_cursor,
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
            branch_cursor=branch_cursor,
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

    fact, snapshot, authority = _require_validated_common_transition(runtime, common)
    proof_context = (
        nullcontext(authority.runtime) if authority is not None else snapshot.runtime_for_proof()
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

            transition = apply_m7_frozen_action_evidence_with_commitments(
                proof_runtime,
                cursor=branch_cursor,
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
