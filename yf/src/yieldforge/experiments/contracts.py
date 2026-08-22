"""Strict, content-stable contracts for registered YieldForge experiments."""

from __future__ import annotations

import hashlib
import json
import os
import stat
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


def _read_bounded_regular_file(path: Path) -> bytes:
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise ExperimentContractError(f"cannot inspect artifact: {error}") from error
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ExperimentContractError("artifact path must be a regular file and not a symlink")
    if file_stat.st_size > _MAX_ARTIFACT_BYTES:
        raise ExperimentContractError(f"artifact exceeds {_MAX_ARTIFACT_BYTES} bytes")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ExperimentContractError(f"cannot open artifact safely: {error}") from error
    try:
        data = os.read(descriptor, _MAX_ARTIFACT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > _MAX_ARTIFACT_BYTES:
        raise ExperimentContractError(f"artifact exceeds {_MAX_ARTIFACT_BYTES} bytes")
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
