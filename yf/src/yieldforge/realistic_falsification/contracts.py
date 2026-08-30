"""Strict, content-addressed contracts for the M11 falsification test."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256

M11Provenance = Literal[
    "source_observed",
    "derived",
    "externally_anchored",
    "generated",
    "assumed",
]

M11SourceLineageKind = Literal["lectra", "loco_2dics"]
M11InvalidReasonCode = Literal[
    "deterministic_regeneration_defect",
    "artifact_integrity_defect",
    "software_implementation_defect",
    "future_information_leakage",
    "candidate_parity_failure",
    "accounting_reconciliation_failure",
    "source_lineage_failure",
    "control_failure",
    "runtime_ceiling_exceeded",
]

M11_PARENT_ROLE_ORDER = ("m0_contract", "m10_verdict")
M11_PARENT_ROLE_SPECS = MappingProxyType(
    {
        "m0_contract": ("yieldforge.m0-contract.v1", "yfm0-"),
        "m10_verdict": (
            "yieldforge.m10-minimum-investment-verdict.v1",
            "yfm10-",
        ),
    }
)
M11_SOURCE_LINEAGE_ORDER: tuple[M11SourceLineageKind, ...] = (
    "lectra",
    "loco_2dics",
)

M11_ALLOWED_PROVENANCE: tuple[M11Provenance, ...] = (
    "source_observed",
    "derived",
    "externally_anchored",
    "generated",
    "assumed",
)

M11_CLAIM_CEILING = (
    "modeled_semi_synthetic_hypothesis_disposition_only_may_abandon_or_authorize_a_bounded_"
    "real_history_operator_pilot_never_authorizes_productization_factory_representativeness_"
    "realized_roi_buyer_demand_adoption_integration_reliability_or_commercial_proof"
)


class M11EvidenceState(StrEnum):
    """The only evidence states the frozen M11 decision admits."""

    INVALID_TEST = "invalid_test"
    FALSIFIED_BY_OPTIMISTIC_CEILING = "falsified_by_optimistic_ceiling"
    INSUFFICIENT_HEADROOM = "insufficient_headroom"
    RETAIN_FOR_PILOT = "retain_for_pilot"


class M11VerdictAction(StrEnum):
    """The only actions mechanically derivable from an M11 evidence state."""

    ONE_REPAIR_AND_RERUN = "ONE_REPAIR_AND_RERUN"
    ABANDON = "ABANDON"
    CONTINUE_TO_REAL_PILOT = "CONTINUE_TO_REAL_PILOT"


class M11InvalidReasonCategory(StrEnum):
    """Closed validity-failure categories with frozen repair semantics."""

    PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT = "preregistered_integrity_or_software_defect"
    OTHER_VALIDITY_FAILURE = "other_validity_failure"
    RUNTIME_OVERRUN = "runtime_overrun"


M11_INVALID_REASON_CODES = MappingProxyType(
    {
        M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT: (
            "deterministic_regeneration_defect",
            "artifact_integrity_defect",
            "software_implementation_defect",
        ),
        M11InvalidReasonCategory.OTHER_VALIDITY_FAILURE: (
            "future_information_leakage",
            "candidate_parity_failure",
            "accounting_reconciliation_failure",
            "source_lineage_failure",
            "control_failure",
        ),
        M11InvalidReasonCategory.RUNTIME_OVERRUN: ("runtime_ceiling_exceeded",),
    }
)


class M11InvalidReason(FrozenExperimentModel):
    """One explicit invalid-test reason and its mechanically checked repair eligibility."""

    category: M11InvalidReasonCategory
    reason_code: M11InvalidReasonCode
    repair_eligible: StrictBool

    @model_validator(mode="after")
    def require_category_derived_eligibility(self) -> Self:
        expected = (
            self.category is M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT
        )
        if self.repair_eligible is not expected:
            raise ValueError("M11 invalid-reason repair eligibility does not match its category")
        if self.reason_code not in M11_INVALID_REASON_CODES[self.category]:
            raise ValueError("M11 invalid reason code does not match its category")
        return self


class M11ParentBinding(FrozenExperimentModel):
    """One exact semantic and raw-file parent admitted by M11."""

    schema_version: Literal["yieldforge.m11-parent-binding.v1"] = "yieldforge.m11-parent-binding.v1"
    binding_id: StrictStr = Field(pattern=r"^yfm11p-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    role: StrictStr = Field(min_length=1)
    repository_path: StrictStr = Field(min_length=1)
    parent_schema_version: StrictStr = Field(min_length=1)
    parent_semantic_id: StrictStr = Field(min_length=1)
    parent_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    raw_file_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        digest = semantic_sha256(
            self,
            excluded_fields={"binding_id", "content_sha256"},
        )
        if self.binding_id != f"yfm11p-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 parent identity does not match semantic content")
        return self


class M11SourceBinding(FrozenExperimentModel):
    """One source-neutral binding to a geometry lineage."""

    schema_version: Literal["yieldforge.m11-source-binding.v1"] = "yieldforge.m11-source-binding.v1"
    source_id: StrictStr = Field(pattern=r"^yfm11s-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: StrictStr = Field(min_length=1)
    lineage_kind: M11SourceLineageKind
    source_uri: StrictStr = Field(min_length=1)
    upstream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    normalized_manifest_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    coordinate_units: Literal["unknown", "declared"]
    geometry_provenance: Literal["source_observed"]

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        digest = semantic_sha256(
            self,
            excluded_fields={"source_id", "content_sha256"},
        )
        if self.source_id != f"yfm11s-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 source identity does not match semantic content")
        return self


class M11FieldProvenance(FrozenExperimentModel):
    """Provenance classification for one generated-pack field or field family."""

    field_name: StrictStr = Field(min_length=1)
    provenance: M11Provenance


class M11CorpusContract(FrozenExperimentModel):
    """Frozen population and controls for one independent geometry corpus."""

    model_config = ConfigDict(revalidate_instances="always")

    source: M11SourceBinding
    calibration_stream_ids: tuple[StrictStr, ...]
    confirmation_stream_ids: tuple[StrictStr, ...]
    shuffled_twin_stream_ids: tuple[StrictStr, ...]
    hard_null_fixture_ids: tuple[StrictStr, ...]
    exact_audit_episode_ids: tuple[StrictStr, ...]
    events_per_stream: StrictInt

    @model_validator(mode="after")
    def require_frozen_population(self) -> Self:
        if self.events_per_stream != 24:
            raise ValueError("M11 requires exactly 24 scored events per stream")
        if len(self.calibration_stream_ids) != 8:
            raise ValueError("M11 requires exactly 8 calibration streams per corpus")
        if len(self.confirmation_stream_ids) != 20:
            raise ValueError("M11 requires exactly 20 confirmation streams per corpus")
        if len(self.shuffled_twin_stream_ids) != 20:
            raise ValueError("M11 requires one shuffled twin per confirmation stream")
        if len(self.hard_null_fixture_ids) < 3:
            raise ValueError("M11 requires at least 3 hard-null fixtures per corpus")
        if len(self.exact_audit_episode_ids) < 6:
            raise ValueError("M11 requires at least 6 exact audit episodes per corpus")

        all_ids = (
            self.calibration_stream_ids
            + self.confirmation_stream_ids
            + self.shuffled_twin_stream_ids
            + self.hard_null_fixture_ids
            + self.exact_audit_episode_ids
        )
        if any(not value for value in all_ids) or len(set(all_ids)) != len(all_ids):
            raise ValueError("M11 corpus stream and control IDs must be unique and disjoint")
        return self


class M11Thresholds(FrozenExperimentModel):
    """All frozen economic, support, control, and inference thresholds."""

    savings_red_below_percent: StrictFloat = 1.5
    savings_green_minimum_percent: StrictFloat = 2.5
    unknown_red_below_percentage_points: StrictFloat = 0.5
    unknown_green_minimum_percentage_points: StrictFloat = 1.5
    maximum_mean_immediate_sacrifice_percent: StrictFloat = 0.5
    minimum_opportunity_frequency_percent: StrictFloat = 20.0
    minimum_ordinary_availability_percent: StrictFloat = 60.0
    minimum_remnant_realization_percent: StrictFloat = 60.0
    maximum_top_10_concentration_percent: StrictFloat = 25.0
    minimum_median_savings_percent_exclusive: StrictFloat = 0.0
    minimum_lower_mean_bound_percent_exclusive: StrictFloat = 0.0
    minimum_positive_stream_fraction_percent_exclusive: StrictFloat = 50.0
    fixed_adverse_minimum_savings_percent: StrictFloat = 1.5
    fixed_adverse_minimum_unknown_percentage_points: StrictFloat = 0.5
    minimum_deployable_capture_percent: StrictFloat = 50.0
    minimum_deployable_savings_percent: StrictFloat = 1.5
    minimum_deployable_unknown_percentage_points: StrictFloat = 0.5
    hard_null_accounting_tolerance: StrictFloat = 0.000001
    no_signal_diagnosis_minimum_percent: StrictFloat = 0.3
    no_signal_invalid_above_percent: StrictFloat = 0.5
    bootstrap_resamples: StrictInt = 10000
    bootstrap_seed: StrictInt = 0
    confidence_level: StrictFloat = 0.95
    runtime_ceiling_hours: StrictInt = 72
    verification_reserve_hours: StrictInt = 12

    @model_validator(mode="after")
    def require_frozen_thresholds(self) -> Self:
        expected = {
            "savings_red_below_percent": 1.5,
            "savings_green_minimum_percent": 2.5,
            "unknown_red_below_percentage_points": 0.5,
            "unknown_green_minimum_percentage_points": 1.5,
            "maximum_mean_immediate_sacrifice_percent": 0.5,
            "minimum_opportunity_frequency_percent": 20.0,
            "minimum_ordinary_availability_percent": 60.0,
            "minimum_remnant_realization_percent": 60.0,
            "maximum_top_10_concentration_percent": 25.0,
            "minimum_median_savings_percent_exclusive": 0.0,
            "minimum_lower_mean_bound_percent_exclusive": 0.0,
            "minimum_positive_stream_fraction_percent_exclusive": 50.0,
            "fixed_adverse_minimum_savings_percent": 1.5,
            "fixed_adverse_minimum_unknown_percentage_points": 0.5,
            "minimum_deployable_capture_percent": 50.0,
            "minimum_deployable_savings_percent": 1.5,
            "minimum_deployable_unknown_percentage_points": 0.5,
            "hard_null_accounting_tolerance": 0.000001,
            "no_signal_diagnosis_minimum_percent": 0.3,
            "no_signal_invalid_above_percent": 0.5,
            "bootstrap_resamples": 10000,
            "bootstrap_seed": 0,
            "confidence_level": 0.95,
            "runtime_ceiling_hours": 72,
            "verification_reserve_hours": 12,
        }
        if self.model_dump(mode="python") != expected:
            raise ValueError("M11 thresholds differ from the frozen decision contract")
        return self


class M11MetricDefinitions(FrozenExperimentModel):
    """Frozen stream-level metric identities; symbols match the M11 design."""

    full_future_savings: Literal["100 * (B_i - F_i) / B_i"] = "100 * (B_i - F_i) / B_i"
    unknown_future_contribution: Literal["100 * (K_i - F_i) / B_i"] = "100 * (K_i - F_i) / B_i"
    deployable_savings: Literal["100 * (B_i - D_i) / B_i"] = "100 * (B_i - D_i) / B_i"
    deployable_unknown_contribution: Literal["100 * (D0_i - D_i) / B_i"] = (
        "100 * (D0_i - D_i) / B_i"
    )
    ceiling_savings: Literal["100 * (B_feasible_i - L_i) / B_feasible_i"] = (
        "100 * (B_feasible_i - L_i) / B_feasible_i"
    )
    ceiling_unknown_contribution: Literal["100 * (K_feasible_i - L_i) / B_feasible_i"] = (
        "100 * (K_feasible_i - L_i) / B_feasible_i"
    )
    net_cost: Literal[
        "purchases + storage + return_handling + retrieval_handling - scrap_proceeds - "
        "terminal_credit"
    ] = (
        "purchases + storage + return_handling + retrieval_handling - scrap_proceeds - "
        "terminal_credit"
    )
    aggregate_weighting: Literal["equal_stream_within_corpus_equal_total_weight_per_corpus"] = (
        "equal_stream_within_corpus_equal_total_weight_per_corpus"
    )


class M11ExperimentContract(FrozenExperimentModel):
    """The complete preregistered M11 population and decision constitution."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-realistic-falsification-contract.v1"] = (
        "yieldforge.m11-realistic-falsification-contract.v1"
    )
    contract_id: StrictStr = Field(pattern=r"^yfm11c-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["frozen_before_confirmation"] = "frozen_before_confirmation"
    parents: tuple[M11ParentBinding, ...]
    corpora: tuple[M11CorpusContract, ...]
    field_provenance: tuple[M11FieldProvenance, ...]
    thresholds: M11Thresholds = Field(default_factory=M11Thresholds)
    metrics: M11MetricDefinitions = Field(default_factory=M11MetricDefinitions)
    claim_ceiling: Literal[
        "modeled_semi_synthetic_hypothesis_disposition_only_may_abandon_or_authorize_a_bounded_"
        "real_history_operator_pilot_never_authorizes_productization_factory_representativeness_"
        "realized_roi_buyer_demand_adoption_integration_reliability_or_commercial_proof"
    ] = M11_CLAIM_CEILING
    productization_authorized: StrictBool = False
    maximum_repairs: StrictInt = 1

    @model_validator(mode="after")
    def require_frozen_census_and_identity(self) -> Self:
        if self.productization_authorized or self.maximum_repairs != 1:
            raise ValueError("M11 cannot authorize productization or permit multiple repairs")
        roles = tuple(parent.role for parent in self.parents)
        if roles != M11_PARENT_ROLE_ORDER:
            raise ValueError("M11 parent roles differ from the frozen required order")
        if len({parent.binding_id for parent in self.parents}) != len(self.parents):
            raise ValueError("M11 parent bindings must be unique")
        for root_field in (
            "repository_path",
            "parent_semantic_id",
            "parent_content_sha256",
            "raw_file_sha256",
        ):
            roots = {getattr(parent, root_field) for parent in self.parents}
            if len(roots) != len(self.parents):
                raise ValueError("M11 requires independent parent root artifacts")
        for parent in self.parents:
            expected_schema, semantic_prefix = M11_PARENT_ROLE_SPECS[parent.role]
            semantic_suffix = parent.parent_semantic_id.removeprefix(semantic_prefix)
            if (
                parent.parent_schema_version != expected_schema
                or not parent.parent_semantic_id.startswith(semantic_prefix)
                or len(semantic_suffix) != 24
                or any(character not in "0123456789abcdef" for character in semantic_suffix)
            ):
                raise ValueError("M11 parents must use their role-specific schema and semantic ID")
        if len(self.corpora) != 2:
            raise ValueError("M11 requires exactly two distinct source lineages and corpora")
        corpus_ids = {corpus.source.corpus_id for corpus in self.corpora}
        lineage_kinds = tuple(corpus.source.lineage_kind for corpus in self.corpora)
        if len(corpus_ids) != 2 or lineage_kinds != M11_SOURCE_LINEAGE_ORDER:
            raise ValueError("M11 requires exactly two distinct source lineages and corpora")
        for origin_field in (
            "source_uri",
            "upstream_sha256",
            "normalized_manifest_sha256",
        ):
            origins = {getattr(corpus.source, origin_field) for corpus in self.corpora}
            if len(origins) != 2:
                raise ValueError("M11 sources must attest independent root origins")
        registered_ids = tuple(
            registered_id
            for corpus in self.corpora
            for registered_id in (
                corpus.calibration_stream_ids
                + corpus.confirmation_stream_ids
                + corpus.shuffled_twin_stream_ids
                + corpus.hard_null_fixture_ids
                + corpus.exact_audit_episode_ids
            )
        )
        if len(set(registered_ids)) != len(registered_ids):
            raise ValueError("M11 stream and control IDs must be globally unique and disjoint")
        fields = tuple(item.field_name for item in self.field_provenance)
        if len(fields) == 0 or len(set(fields)) != len(fields) or fields != tuple(sorted(fields)):
            raise ValueError("M11 field provenance entries must be nonempty, unique, and sorted")

        digest = semantic_sha256(
            self,
            excluded_fields={"contract_id", "content_sha256"},
        )
        if self.contract_id != f"yfm11c-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 contract identity does not match semantic content")
        return self


class M11VerdictResult(FrozenExperimentModel):
    """A content-addressed forced action derived from an exact M11 contract."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-realistic-falsification-verdict.v1"] = (
        "yieldforge.m11-realistic-falsification-verdict.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm11r-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract: M11ExperimentContract
    evidence_state: M11EvidenceState
    invalid_reason: M11InvalidReason | None = None
    repair_count: StrictInt
    action: M11VerdictAction
    claim_ceiling: Literal[
        "modeled_semi_synthetic_hypothesis_disposition_only_may_abandon_or_authorize_a_bounded_"
        "real_history_operator_pilot_never_authorizes_productization_factory_representativeness_"
        "realized_roi_buyer_demand_adoption_integration_reliability_or_commercial_proof"
    ] = M11_CLAIM_CEILING
    productization_authorized: StrictBool = False

    @model_validator(mode="after")
    def require_derived_action_and_identity(self) -> Self:
        if self.repair_count not in (0, 1):
            raise ValueError("M11 repair count must be 0 or 1")
        if self.productization_authorized:
            raise ValueError("M11 modeled evidence cannot authorize productization")
        if self.evidence_state is M11EvidenceState.INVALID_TEST:
            if self.invalid_reason is None:
                raise ValueError("M11 invalid_test requires an explicit invalid reason")
        elif self.invalid_reason is not None:
            raise ValueError("M11 non-invalid evidence cannot carry an invalid reason")
        expected_action = _action_for(
            self.evidence_state,
            self.repair_count,
            self.invalid_reason,
        )
        if self.action is not expected_action:
            raise ValueError("M11 verdict action does not match the frozen decision rule")
        digest = semantic_sha256(
            self,
            excluded_fields={"result_id", "content_sha256"},
        )
        if self.result_id != f"yfm11r-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 verdict identity does not match semantic content")
        return self


def build_m11_parent_binding(
    *,
    role: str,
    repository_path: str,
    schema_version: str,
    parent_semantic_id: str,
    parent_content_sha256: str,
    raw_file_sha256: str,
) -> M11ParentBinding:
    """Build a content-addressed binding to one immutable parent artifact."""

    semantic = {
        "schema_version": "yieldforge.m11-parent-binding.v1",
        "role": role,
        "repository_path": repository_path,
        "parent_schema_version": schema_version,
        "parent_semantic_id": parent_semantic_id,
        "parent_content_sha256": parent_content_sha256,
        "raw_file_sha256": raw_file_sha256,
    }
    digest = semantic_sha256(semantic)
    return M11ParentBinding(
        binding_id=f"yfm11p-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def build_m11_source_binding(
    *,
    corpus_id: str,
    lineage_kind: M11SourceLineageKind,
    source_uri: str,
    upstream_sha256: str,
    normalized_manifest_sha256: str,
    coordinate_units: Literal["unknown", "declared"],
    geometry_provenance: Literal["source_observed"],
) -> M11SourceBinding:
    """Build a content-addressed, source-neutral geometry binding."""

    semantic = {
        "schema_version": "yieldforge.m11-source-binding.v1",
        "corpus_id": corpus_id,
        "lineage_kind": lineage_kind,
        "source_uri": source_uri,
        "upstream_sha256": upstream_sha256,
        "normalized_manifest_sha256": normalized_manifest_sha256,
        "coordinate_units": coordinate_units,
        "geometry_provenance": geometry_provenance,
    }
    digest = semantic_sha256(semantic)
    return M11SourceBinding(
        source_id=f"yfm11s-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def build_m11_contract(
    *,
    parents: tuple[M11ParentBinding, ...],
    corpora: tuple[M11CorpusContract, ...],
    field_provenance: tuple[M11FieldProvenance, ...],
) -> M11ExperimentContract:
    """Revalidate, detach, and freeze the complete M11 decision contract."""

    canonical_parents = tuple(
        M11ParentBinding.model_validate(
            parent.model_dump(mode="python", round_trip=True, warnings=False),
            strict=True,
        )
        for parent in parents
    )
    canonical_corpora = tuple(
        M11CorpusContract.model_validate(
            corpus.model_dump(mode="python", round_trip=True, warnings=False),
            strict=True,
        )
        for corpus in corpora
    )
    canonical_provenance = tuple(
        sorted(
            (
                M11FieldProvenance.model_validate(
                    item.model_dump(mode="python", round_trip=True, warnings=False),
                    strict=True,
                )
                for item in field_provenance
            ),
            key=lambda item: item.field_name,
        )
    )
    semantic = {
        "schema_version": "yieldforge.m11-realistic-falsification-contract.v1",
        "status": "frozen_before_confirmation",
        "parents": canonical_parents,
        "corpora": canonical_corpora,
        "field_provenance": canonical_provenance,
        "thresholds": M11Thresholds(),
        "metrics": M11MetricDefinitions(),
        "claim_ceiling": M11_CLAIM_CEILING,
        "productization_authorized": False,
        "maximum_repairs": 1,
    }
    hashable_semantic = {
        **semantic,
        "parents": [item.model_dump(mode="json") for item in canonical_parents],
        "corpora": [item.model_dump(mode="json") for item in canonical_corpora],
        "field_provenance": [item.model_dump(mode="json") for item in canonical_provenance],
        "thresholds": semantic["thresholds"].model_dump(mode="json"),
        "metrics": semantic["metrics"].model_dump(mode="json"),
    }
    digest = semantic_sha256(hashable_semantic)
    return M11ExperimentContract(
        contract_id=f"yfm11c-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _action_for(
    evidence_state: M11EvidenceState,
    repair_count: int,
    invalid_reason: M11InvalidReason | None,
) -> M11VerdictAction:
    if evidence_state is M11EvidenceState.RETAIN_FOR_PILOT:
        return M11VerdictAction.CONTINUE_TO_REAL_PILOT
    if (
        evidence_state is M11EvidenceState.INVALID_TEST
        and repair_count == 0
        and invalid_reason is not None
        and invalid_reason.repair_eligible
    ):
        return M11VerdictAction.ONE_REPAIR_AND_RERUN
    return M11VerdictAction.ABANDON


def build_m11_verdict(
    *,
    contract: M11ExperimentContract,
    evidence_state: M11EvidenceState,
    repair_count: Literal[0, 1],
    invalid_reason: M11InvalidReason | None = None,
) -> M11VerdictResult:
    """Apply the frozen M11 decision rule without accepting a caller-selected action."""

    canonical_contract = M11ExperimentContract.model_validate(
        contract.model_dump(mode="python", round_trip=True, warnings=False),
        strict=True,
    )
    canonical_invalid_reason = (
        None
        if invalid_reason is None
        else M11InvalidReason.model_validate(
            invalid_reason.model_dump(mode="python", round_trip=True, warnings=False),
            strict=True,
        )
    )
    semantic = {
        "schema_version": "yieldforge.m11-realistic-falsification-verdict.v1",
        "contract": canonical_contract,
        "evidence_state": evidence_state,
        "invalid_reason": canonical_invalid_reason,
        "repair_count": repair_count,
        "action": _action_for(evidence_state, repair_count, canonical_invalid_reason),
        "claim_ceiling": M11_CLAIM_CEILING,
        "productization_authorized": False,
    }
    hashable_semantic = {
        **semantic,
        "contract": canonical_contract.model_dump(mode="json"),
        "invalid_reason": (
            None
            if canonical_invalid_reason is None
            else canonical_invalid_reason.model_dump(mode="json")
        ),
    }
    digest = semantic_sha256(hashable_semantic)
    return M11VerdictResult(
        result_id=f"yfm11r-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


__all__ = [
    "M11_ALLOWED_PROVENANCE",
    "M11_CLAIM_CEILING",
    "M11CorpusContract",
    "M11EvidenceState",
    "M11ExperimentContract",
    "M11FieldProvenance",
    "M11_INVALID_REASON_CODES",
    "M11InvalidReasonCode",
    "M11InvalidReason",
    "M11InvalidReasonCategory",
    "M11MetricDefinitions",
    "M11_PARENT_ROLE_ORDER",
    "M11_PARENT_ROLE_SPECS",
    "M11ParentBinding",
    "M11Provenance",
    "M11SourceBinding",
    "M11SourceLineageKind",
    "M11_SOURCE_LINEAGE_ORDER",
    "M11Thresholds",
    "M11VerdictAction",
    "M11VerdictResult",
    "build_m11_contract",
    "build_m11_parent_binding",
    "build_m11_source_binding",
    "build_m11_verdict",
]
