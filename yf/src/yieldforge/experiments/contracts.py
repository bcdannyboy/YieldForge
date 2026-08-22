"""Strict, content-stable contracts for registered YieldForge experiments."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from yieldforge.datasets.normalized_slice import NormalizedSlice, SupportStatus
from yieldforge.datasets.projection import S1_FLIP_ASSUMPTION

_MAX_ARTIFACT_BYTES = 1024 * 1024


class FrozenExperimentModel(BaseModel):
    """Strict immutable base for every registered experiment artifact."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ExperimentContractError(ValueError):
    """A registered experiment artifact failed a fail-closed check."""


def semantic_sha256(
    value: BaseModel | dict[str, object], *, excluded_fields: set[str] | None = None
) -> str:
    """Hash semantic JSON after excluding top-level identity fields."""

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    for field in excluded_fields or set():
        payload.pop(field, None)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ClaimCeiling(FrozenExperimentModel):
    permitted_claim: StrictStr
    excluded_claims: tuple[StrictStr, ...]


class PrimaryOutcome(FrozenExperimentModel):
    name: StrictStr
    formula: StrictStr
    required_positive_denominator: StrictBool
    net_cost_formula: StrictStr
    included_terms: tuple[StrictStr, ...]
    immediate_utilization_role: StrictStr


class CostAccounting(FrozenExperimentModel):
    purchase_accrual: StrictStr
    storage_formula: StrictStr
    storage_interval: StrictStr
    remnant_return_handling_accrual: StrictStr
    remnant_retrieval_handling_accrual: StrictStr
    scrap_proceeds_accrual: StrictStr
    process_loss_treatment: StrictStr
    excluded_process_costs: tuple[StrictStr, ...]
    rate_manifest_requirement: StrictStr


class TerminalInventory(FrozenExperimentModel):
    primary_treatment: StrictStr
    primary_option_value: StrictFloat
    required_sensitivities: tuple[StrictStr, ...]
    bounded_continuation_max_virgin_fraction: StrictFloat
    green_requires_scrap_zero_band_invariance: StrictBool
    green_may_depend_on_continuation_credit: StrictBool


class Comparators(FrozenExperimentModel):
    baseline_selection: StrictStr
    baseline_selection_unit: StrictStr
    evaluation_stream_selection: StrictStr
    known_only_control: StrictStr
    full_oracle: StrictStr
    unknown_future_contribution_formula: StrictStr
    common_denominator: StrictStr


class InformationSets(FrozenExperimentModel):
    myopic_geometry_policy: StrictStr
    commercial_as_of_time_baseline: StrictStr
    known_order_lookahead_baseline: StrictStr
    known_only_oracle_control: StrictStr
    perfect_information_rollout: StrictStr
    perfect_information_beam_search: StrictStr
    forbidden_baseline_information: StrictStr


class EventTiming(FrozenExperimentModel):
    ordered_stages: tuple[StrictStr, ...]
    released_work_fulfillment: StrictStr
    compatible_batching: StrictStr
    scheduled_work_treatment: StrictStr
    multi_sheet_treatment: StrictStr
    horizon_close: StrictStr


class CandidateParity(FrozenExperimentModel):
    shared_candidate_archive_hashes: StrictBool
    shared_projection: StrictBool
    shared_solver_seeds: StrictBool
    shared_solver_configuration: StrictBool
    shared_ordinary_compute_budget: StrictBool
    shared_stock_and_remnant_eligibility: StrictBool
    baseline_action_fallback: StrictBool
    expanded_search_treatment: StrictStr
    parity_failure_treatment: StrictStr


class RemnantRule(FrozenExperimentModel):
    name: StrictStr
    minimum_area_sheet_fraction: StrictFloat
    minimum_effective_width_short_side_fraction: StrictFloat
    minimum_exterior_access_short_side_fraction: StrictFloat
    effective_width_test: StrictStr
    requires_exterior_connection: StrictBool


class RemnantEligibility(FrozenExperimentModel):
    primary: RemnantRule
    permissive_sensitivity: RemnantRule
    conservative_sensitivity: RemnantRule
    component_treatment: StrictStr
    hole_treatment: StrictStr
    material_compatibility_fields: tuple[StrictStr, ...]
    lineage_rule: StrictStr
    storage_eligibility: StrictStr
    handling_eligibility: StrictStr
    threshold_unit_rationale: StrictStr


class FailureHandling(FrozenExperimentModel):
    blocked_source_semantics: StrictStr
    geometry_failure_denominator: StrictStr
    economic_stream_failure: StrictStr
    invalidating_economic_failures: tuple[StrictStr, ...]
    maximum_identical_retries: StrictInt
    retryable_causes: tuple[StrictStr, ...]
    nonretryable_causes: tuple[StrictStr, ...]
    incomplete_stream_treatment: StrictStr
    missing_baseline_action_treatment: StrictStr


class StatisticalRules(FrozenExperimentModel):
    comparison_unit: StrictStr
    paired: StrictBool
    common_persisted_seeds: StrictBool
    calibration_evaluation_separation: StrictStr
    bootstrap_method: StrictStr
    bootstrap_resamples: StrictInt
    bootstrap_seed: StrictInt
    confidence_level: StrictFloat
    reported_location_statistics: tuple[StrictStr, ...]
    lower_tail_statistics: tuple[StrictStr, ...]
    positive_stream_definition: StrictStr
    positive_stream_interval: StrictStr
    outlier_concentration: tuple[StrictStr, ...]
    registered_results_reporting: StrictStr


class ExperimentalControls(FrozenExperimentModel):
    required_controls: tuple[StrictStr, ...]
    exact_small_case_green_requirement: StrictStr
    scalable_oracle_label: StrictStr


class RedGate(FrozenExperimentModel):
    oracle_savings_below_percent: StrictFloat
    unknown_future_below_percentage_points: StrictFloat
    combination: StrictStr


class YellowGate(FrozenExperimentModel):
    oracle_savings_minimum_percent: StrictFloat
    oracle_savings_below_percent: StrictFloat
    unknown_future_minimum_percentage_points: StrictFloat
    unknown_future_below_percentage_points: StrictFloat
    combination: StrictStr


class GreenGate(FrozenExperimentModel):
    minimum_oracle_savings_percent: StrictFloat
    minimum_unknown_future_percentage_points: StrictFloat
    combination: StrictStr
    requires_all_supporting_gates: StrictBool


class SupportingGates(FrozenExperimentModel):
    maximum_mean_immediate_sacrifice_percent: StrictFloat
    minimum_ordinary_candidate_availability_percent: StrictFloat
    minimum_opportunity_frequency_percent: StrictFloat
    minimum_remnant_realization_percent: StrictFloat
    maximum_top_10_decision_savings_share_percent: StrictFloat
    minimum_median_savings_percent_exclusive: StrictFloat
    minimum_lower_mean_confidence_bound_percent_exclusive: StrictFloat
    minimum_positive_stream_fraction_percent_exclusive: StrictFloat
    minimum_geometry_corpora_with_positive_evidence: StrictInt
    terminal_band_invariance_required: StrictBool
    no_signal_green_minimum_percent: StrictFloat
    no_signal_green_maximum_percent: StrictFloat
    no_signal_investigate_maximum_percent: StrictFloat
    negative_oracle_savings_treatment: StrictStr
    no_signal_above_investigate_treatment: StrictStr


class DecisionGates(FrozenExperimentModel):
    red: RedGate
    yellow: YellowGate
    green: GreenGate
    supporting: SupportingGates
    precedence: StrictStr


class ImmutabilityRules(FrozenExperimentModel):
    threshold_changes_after_evaluation: StrictStr
    semantic_change_treatment: StrictStr
    old_artifact_treatment: StrictStr
    confirmation_prerequisites: tuple[StrictStr, ...]


_APPROVED_M0_SEMANTIC_SHA256 = "29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f"


class M0ExperimentContract(FrozenExperimentModel):
    """The approved economic constitution for the YieldForge experiment."""

    schema_version: Literal["yieldforge.m0-contract.v1"] = "yieldforge.m0-contract.v1"
    contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["frozen_pending_geometry_calibration"]
    claim_ceiling: ClaimCeiling
    primary_outcome: PrimaryOutcome
    cost_accounting: CostAccounting
    terminal_inventory: TerminalInventory
    comparators: Comparators
    information_sets: InformationSets
    event_timing: EventTiming
    candidate_parity: CandidateParity
    remnant_eligibility: RemnantEligibility
    failure_handling: FailureHandling
    statistics: StatisticalRules
    experimental_controls: ExperimentalControls
    decision_gates: DecisionGates
    immutability: ImmutabilityRules

    @model_validator(mode="after")
    def require_approved_semantics_and_identity(self) -> Self:
        digest = semantic_sha256(
            self,
            excluded_fields={"contract_id", "content_sha256"},
        )
        if digest != _APPROVED_M0_SEMANTIC_SHA256:
            raise ValueError("contract differs from the approved M0 rules")
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M0 content SHA-256 does not match its semantic content")
        if self.contract_id != f"yfm0-{digest[:24]}":
            raise ValueError("M0 contract ID does not match its semantic content")
        return self


class GeometryReferences(FrozenExperimentModel):
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_id: StrictStr
    catalog_artifact_name: StrictStr
    catalog_artifact_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_size_bytes: StrictInt
    catalog_manifest_schema_version: StrictStr
    conversion_ruleset_version: StrictStr


class BlockedGeometryTask(FrozenExperimentModel):
    tasks_index: StrictInt
    reason_code: StrictStr


class GeometryPopulation(FrozenExperimentModel):
    eligible_task_ids: tuple[StrictInt, ...]
    blocked_tasks: tuple[BlockedGeometryTask, ...]
    flip_bearing_task_ids: tuple[StrictInt, ...]
    eligibility_rule: StrictStr
    population_role: StrictStr


class GeometrySplit(FrozenExperimentModel):
    algorithm: StrictStr
    salt: StrictStr
    calibration_count: StrictInt
    evaluation_count: StrictInt
    calibration_task_ids: tuple[StrictInt, ...]
    evaluation_task_ids: tuple[StrictInt, ...]
    source_partition_flags_used: StrictBool


class GeometryRepeatability(FrozenExperimentModel):
    algorithm: StrictStr
    salt: StrictStr
    task_count: StrictInt
    task_ids: tuple[StrictInt, ...]
    execution: StrictStr
    reported_metrics: tuple[StrictStr, ...]


class GeometryProjection(FrozenExperimentModel):
    primary_mode: StrictStr
    sensitivity_mode: StrictStr
    sensitivity_population: StrictStr
    sensitivity_task_ids: tuple[StrictInt, ...]
    sensitivity_in_primary: StrictBool
    required_assumption_codes: tuple[StrictStr, ...]
    required_intervention_code: StrictStr


class CalibrationSelector(FrozenExperimentModel):
    reference_seconds_per_seed: StrictInt
    maximum_qualifying_rate_gap_percentage_points: StrictFloat
    maximum_median_best_length_degradation_percent: StrictFloat
    maximum_p95_best_length_degradation_percent: StrictFloat
    minimum_valid_archive_rate_percent: StrictFloat
    selection_rule: StrictStr


class GeometryBudget(FrozenExperimentModel):
    ordinary_seeds: tuple[StrictInt, ...]
    calibration_seconds_per_seed: tuple[StrictInt, ...]
    selected_seconds_per_seed: StrictInt | None
    num_workers: StrictInt
    early_termination: StrictBool
    min_items_separation: StrictFloat | None
    selector: CalibrationSelector
    expanded_search_seeds: tuple[StrictInt, ...]
    expanded_search_role: StrictStr
    outer_timeout_formula: StrictStr
    maximum_identical_retries: StrictInt
    retryable_causes: tuple[StrictStr, ...]
    nonretryable_causes: tuple[StrictStr, ...]


class NearTieProtocol(FrozenExperimentModel):
    performance_measure: StrictStr
    gap_formula: StrictStr
    reference_candidate: StrictStr
    envelope_grid_percent: tuple[StrictFloat, ...]
    primary_envelope_percent: StrictFloat


class CandidateDefinition(FrozenExperimentModel):
    accepted_report_types: tuple[StrictStr, ...]
    requires_finite_complete_placements: StrictBool
    requires_valid_part_instance_ids: StrictBool
    requires_verified_immutable_archive: StrictBool
    fixed_sheet_acceptance: StrictStr
    fixed_sheet_tolerance: StrictFloat
    canonical_identity: StrictStr
    placement_order_changes_identity: StrictBool
    exact_rotation_or_position_change_is_distinct: StrictBool
    tolerance_clustering_role: StrictStr
    residual_equivalence_milestone: StrictStr
    normalized_positional_difference: StrictStr
    rotation_difference: StrictStr


class GeometryOutcome(FrozenExperimentModel):
    primary_name: StrictStr
    primary_definition: StrictStr
    primary_denominator: StrictInt
    failure_treatment: StrictStr
    uncertainty_interval: StrictStr
    uncertainty_interpretation: StrictStr
    supporting_outcomes: tuple[StrictStr, ...]


class GeometryReporting(FrozenExperimentModel):
    flip_presence: StrictBool
    part_count_bands: tuple[StrictStr, ...]
    unique_shape_count_bands: tuple[StrictStr, ...]
    maximum_orientation_state_bands: tuple[StrictStr, ...]
    sheet_aspect_strata: StrictStr
    post_result_strata_allowed: StrictBool
    source_efficiency_interpretation: StrictStr


class GeometryDecisionRule(FrozenExperimentModel):
    proceed_minimum_percent: StrictFloat
    proceed_minimum_valid_archive_rate_percent: StrictFloat
    redesign_minimum_percent_inclusive: StrictFloat
    redesign_below_percent: StrictFloat
    expanded_rescue_minimum_percent: StrictFloat
    stop_ordinary_below_percent: StrictFloat
    stop_expanded_below_percent: StrictFloat
    permitted_positive_claim: StrictStr
    forbidden_positive_claims: tuple[StrictStr, ...]


_APPROVED_GEOMETRY_SEMANTIC_SHA256 = (
    "49906e93ed9ff0446705247bf6f2519588265ccbd9e6d1c9676e98ad7ed05737"
)


class PureGeometryCalibrationProtocol(FrozenExperimentModel):
    """Pre-registered calibration protocol preceding geometry confirmation."""

    schema_version: Literal["yieldforge.pure-geometry-protocol.v1"] = (
        "yieldforge.pure-geometry-protocol.v1"
    )
    protocol_id: StrictStr = Field(pattern=r"^yfgp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["calibration_pending"]
    confirmation_enabled: Literal[False]
    references: GeometryReferences
    population: GeometryPopulation
    split: GeometrySplit
    repeatability: GeometryRepeatability
    projection: GeometryProjection
    budget: GeometryBudget
    near_tie: NearTieProtocol
    candidate_definition: CandidateDefinition
    outcome: GeometryOutcome
    reporting: GeometryReporting
    decision_rule: GeometryDecisionRule

    @model_validator(mode="after")
    def require_approved_semantics_and_identity(self) -> Self:
        digest = semantic_sha256(
            self,
            excluded_fields={"protocol_id", "content_sha256"},
        )
        if digest != _APPROVED_GEOMETRY_SEMANTIC_SHA256:
            raise ValueError("protocol differs from the approved pure-geometry rules")
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("geometry content SHA-256 does not match its semantic content")
        if self.protocol_id != f"yfgp-{digest[:24]}":
            raise ValueError("geometry protocol ID does not match its semantic content")
        return self


_APPROVED_GEOMETRY_CALIBRATION_RESULT_ID = "yfgcr-c333f934c363abc0d78082ec"
_APPROVED_GEOMETRY_CALIBRATION_RESULT_SHA256 = (
    "sha256:c333f934c363abc0d78082ecdb60d8020ee0be8a08992b9e80e5caf4e349cbec"
)


class GeometryCalibrationResultReference(FrozenExperimentModel):
    result_id: StrictStr = Field(pattern=r"^yfgcr-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selected_seconds_per_seed: Literal[10]


class PureGeometryConfirmationProtocol(FrozenExperimentModel):
    """Confirmation-ready protocol preserving every frozen v1 rule."""

    schema_version: Literal["yieldforge.pure-geometry-protocol.v2"] = (
        "yieldforge.pure-geometry-protocol.v2"
    )
    protocol_id: StrictStr = Field(pattern=r"^yfgp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["confirmation_ready"]
    confirmation_enabled: Literal[True]
    calibration_result: GeometryCalibrationResultReference
    references: GeometryReferences
    population: GeometryPopulation
    split: GeometrySplit
    repeatability: GeometryRepeatability
    projection: GeometryProjection
    budget: GeometryBudget
    near_tie: NearTieProtocol
    candidate_definition: CandidateDefinition
    outcome: GeometryOutcome
    reporting: GeometryReporting
    decision_rule: GeometryDecisionRule

    @model_validator(mode="after")
    def require_exact_calibration_and_frozen_rules(self) -> Self:
        if (
            self.calibration_result.result_id != _APPROVED_GEOMETRY_CALIBRATION_RESULT_ID
            or self.calibration_result.content_sha256
            != _APPROVED_GEOMETRY_CALIBRATION_RESULT_SHA256
            or self.budget.selected_seconds_per_seed
            != self.calibration_result.selected_seconds_per_seed
        ):
            raise ValueError("confirmation protocol does not bind the approved calibration result")
        PureGeometryCalibrationProtocol(
            protocol_id="yfgp-49906e93ed9ff0446705247b",
            content_sha256=(
                "sha256:49906e93ed9ff0446705247bf6f2519588265ccbd9e6d1c9676e98ad7ed05737"
            ),
            status="calibration_pending",
            confirmation_enabled=False,
            references=self.references,
            population=self.population,
            split=self.split,
            repeatability=self.repeatability,
            projection=self.projection,
            budget=self.budget.model_copy(update={"selected_seconds_per_seed": None}),
            near_tie=self.near_tie,
            candidate_definition=self.candidate_definition,
            outcome=self.outcome,
            reporting=self.reporting,
            decision_rule=self.decision_rule,
        )
        digest = semantic_sha256(
            self,
            excluded_fields={"protocol_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("geometry content SHA-256 does not match its semantic content")
        if self.protocol_id != f"yfgp-{digest[:24]}":
            raise ValueError("geometry protocol ID does not match its semantic content")
        return self


def build_geometry_confirmation_protocol(
    calibration: PureGeometryCalibrationProtocol,
    *,
    result_id: str,
    result_sha256: str,
    selected_seconds_per_seed: int,
) -> PureGeometryConfirmationProtocol:
    """Create the sole confirmation-ready successor permitted by calibration v1."""

    payload = calibration.model_dump(mode="json")
    payload.update(
        {
            "schema_version": "yieldforge.pure-geometry-protocol.v2",
            "status": "confirmation_ready",
            "confirmation_enabled": True,
            "calibration_result": {
                "result_id": result_id,
                "content_sha256": result_sha256,
                "selected_seconds_per_seed": selected_seconds_per_seed,
            },
        }
    )
    budget = dict(payload["budget"])
    budget["selected_seconds_per_seed"] = selected_seconds_per_seed
    payload["budget"] = budget
    digest = semantic_sha256(payload, excluded_fields={"protocol_id", "content_sha256"})
    return PureGeometryConfirmationProtocol.model_validate_json(
        json.dumps(
            {
                **payload,
                "protocol_id": f"yfgp-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        strict=True,
    )


def rank_task_ids(task_ids: Iterable[int], *, salt: str, catalog_sha256: str) -> tuple[int, ...]:
    """Rank task IDs with the frozen SHA-256 selection rule."""

    return tuple(
        sorted(
            task_ids,
            key=lambda task_id: (
                hashlib.sha256(f"{salt}:{catalog_sha256}:{task_id}".encode()).hexdigest(),
                task_id,
            ),
        )
    )


class CatalogArtifact(FrozenExperimentModel):
    name: StrictStr
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: StrictInt


class CatalogEvidence(FrozenExperimentModel):
    source_manifest_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    audit_report_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    conversion_ruleset_version: StrictStr


class CatalogCounts(FrozenExperimentModel):
    tasks: StrictInt
    parts: StrictInt
    shapes: StrictInt
    derived_geometry: StrictInt
    constraints: StrictInt


class CatalogCapabilityDistribution(FrozenExperimentModel):
    runnable_with_explicit_assumptions: StrictInt
    view_only: StrictInt


class CatalogManifest(FrozenExperimentModel):
    schema_version: Literal["yieldforge.catalog-manifest.v1"]
    dataset_id: StrictStr
    artifact: CatalogArtifact
    evidence: CatalogEvidence
    counts: CatalogCounts
    capability_distribution: CatalogCapabilityDistribution


@dataclass(frozen=True)
class ValidatedExperimentBundle:
    """Validated M0, geometry, and source-catalog identities."""

    m0: M0ExperimentContract
    geometry: PureGeometryCalibrationProtocol
    catalog_sha256: str


def _validate_json_syntax(data: bytes) -> None:
    try:
        json.loads(
            data,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except ExperimentContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExperimentContractError(f"artifact is not valid JSON: {error}") from error


def _load_catalog_manifest(path: Path) -> CatalogManifest:
    data = _read_bounded_regular_file(path)
    _validate_json_syntax(data)
    try:
        return CatalogManifest.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise ExperimentContractError(f"catalog manifest validation failed: {error}") from error


def validate_experiment_bundle(
    *,
    m0_path: Path,
    geometry_path: Path,
    catalog_path: Path,
    catalog_manifest_path: Path,
) -> ValidatedExperimentBundle:
    """Validate the frozen contracts against each other and the committed catalog."""

    m0 = load_frozen_json(m0_path, M0ExperimentContract)
    geometry = load_frozen_json(geometry_path, PureGeometryCalibrationProtocol)
    if geometry.references.m0_contract_sha256 != m0.content_sha256:
        raise ExperimentContractError("geometry protocol references a different M0 contract")

    manifest = _load_catalog_manifest(catalog_manifest_path)
    catalog_data = _read_bounded_regular_file(catalog_path, max_bytes=16 * 1024 * 1024)
    catalog_sha256 = hashlib.sha256(catalog_data).hexdigest()
    if manifest.artifact.sha256 != catalog_sha256:
        raise ExperimentContractError("catalog artifact SHA-256 does not match its manifest")
    if manifest.artifact.size_bytes != len(catalog_data):
        raise ExperimentContractError("catalog artifact size does not match its manifest")
    if manifest.artifact.name != catalog_path.name:
        raise ExperimentContractError("catalog artifact name does not match its manifest")

    references = geometry.references
    if (
        references.catalog_artifact_sha256 != catalog_sha256
        or references.catalog_size_bytes != len(catalog_data)
        or references.catalog_artifact_name != catalog_path.name
        or references.catalog_manifest_schema_version != manifest.schema_version
        or references.dataset_id != manifest.dataset_id
        or references.conversion_ruleset_version != manifest.evidence.conversion_ruleset_version
    ):
        raise ExperimentContractError("geometry protocol catalog reference drifted")

    _validate_json_syntax(catalog_data)
    try:
        catalog = NormalizedSlice.model_validate_json(catalog_data)
    except ValidationError as error:
        raise ExperimentContractError(f"catalog validation failed: {error}") from error
    if (
        catalog.source.dataset_id != references.dataset_id
        or catalog.source.conversion_ruleset_version != references.conversion_ruleset_version
    ):
        raise ExperimentContractError("catalog source identity drifted")

    eligible = tuple(
        sorted(
            item.tasks_index
            for item in catalog.task_dispositions
            if item.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
        )
    )
    blocked = tuple(
        BlockedGeometryTask(tasks_index=item.tasks_index, reason_code=item.reason_codes[0])
        for item in catalog.task_dispositions
        if item.support_status is SupportStatus.VIEW_ONLY
    )
    flip_bearing = tuple(
        sorted(
            item.tasks_index
            for item in catalog.task_dispositions
            if item.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
            and S1_FLIP_ASSUMPTION in item.assumption_codes
        )
    )
    population = geometry.population
    if population.eligible_task_ids != eligible:
        raise ExperimentContractError("geometry eligible population does not match catalog")
    if population.blocked_tasks != blocked:
        raise ExperimentContractError("geometry blocked population does not match catalog")
    if population.flip_bearing_task_ids != flip_bearing:
        raise ExperimentContractError("geometry flip-bearing population does not match catalog")
    if geometry.projection.sensitivity_task_ids != flip_bearing:
        raise ExperimentContractError("projection sensitivity population does not match catalog")
    if (
        manifest.capability_distribution.runnable_with_explicit_assumptions != len(eligible)
        or manifest.capability_distribution.view_only != len(blocked)
        or manifest.counts.tasks != len(catalog.tasks)
    ):
        raise ExperimentContractError("catalog manifest population counts drifted")

    ranked = rank_task_ids(
        eligible,
        salt=geometry.split.salt,
        catalog_sha256=catalog_sha256,
    )
    expected_calibration = ranked[: geometry.split.calibration_count]
    expected_evaluation = ranked[geometry.split.calibration_count :]
    if geometry.split.calibration_task_ids != expected_calibration:
        raise ExperimentContractError("calibration split does not match frozen ranking")
    if geometry.split.evaluation_task_ids != expected_evaluation:
        raise ExperimentContractError("evaluation split does not match frozen ranking")
    expected_repeatability = rank_task_ids(
        expected_evaluation,
        salt=geometry.repeatability.salt,
        catalog_sha256=catalog_sha256,
    )[: geometry.repeatability.task_count]
    if geometry.repeatability.task_ids != expected_repeatability:
        raise ExperimentContractError("repeatability subset does not match frozen ranking")

    return ValidatedExperimentBundle(
        m0=m0,
        geometry=geometry,
        catalog_sha256=catalog_sha256,
    )


def canonical_pretty_json_bytes(value: BaseModel) -> bytes:
    """Return the sole accepted committed encoding for one experiment artifact."""

    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExperimentContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise ExperimentContractError(f"nonfinite JSON constant: {value}")


def _read_bounded_regular_file(path: Path, *, max_bytes: int = _MAX_ARTIFACT_BYTES) -> bytes:
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise ExperimentContractError(f"cannot inspect artifact: {error}") from error
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ExperimentContractError("artifact path must be a regular file and not a symlink")
    if file_stat.st_size > max_bytes:
        raise ExperimentContractError(f"artifact exceeds {max_bytes} bytes")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ExperimentContractError(f"cannot open artifact safely: {error}") from error
    try:
        data = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(data) > max_bytes:
        raise ExperimentContractError(f"artifact exceeds {max_bytes} bytes")
    return data


def load_frozen_json[ExperimentModel: FrozenExperimentModel](
    path: Path, model: type[ExperimentModel]
) -> ExperimentModel:
    """Load one bounded canonical JSON artifact into a strict frozen model."""

    data = _read_bounded_regular_file(Path(path))
    try:
        json.loads(
            data,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except ExperimentContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExperimentContractError(f"artifact is not valid JSON: {error}") from error
    try:
        result = model.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise ExperimentContractError(f"contract validation failed: {error}") from error
    if data != canonical_pretty_json_bytes(result):
        raise ExperimentContractError("artifact does not use canonical JSON encoding")
    return result
