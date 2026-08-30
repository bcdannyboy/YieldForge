"""Frozen, outcome-blind contracts for M11 Gate 3 confirmation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from yieldforge.baseline.experiment import M7FrozenBaseline
from yieldforge.baseline.policies import M7PolicyIdentity, registered_policy_identities
from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    M0ExperimentContract,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.realistic_falsification.contracts import (
    M11_CLAIM_CEILING,
    M11ExperimentContract,
    M11Thresholds,
)
from yieldforge.realistic_falsification.pack import M11Population
from yieldforge.realistic_falsification.sources import M11SourceManifest

GATE3_CONFIG_FILENAME = "m11-gate3-config-v1.json"

_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_ROOT_BYTES = 8 * 1024 * 1024

M11Gate3RootRole = Literal[
    "m11_contract",
    "m11_population",
    "m11_source_manifest",
    "m0_contract",
    "m7_frozen_baseline",
    "m9_two_ply_repair",
]

_ROOT_ROLE_ORDER: tuple[M11Gate3RootRole, ...] = (
    "m11_contract",
    "m11_population",
    "m11_source_manifest",
    "m0_contract",
    "m7_frozen_baseline",
    "m9_two_ply_repair",
)

_ROOT_SPECS = MappingProxyType(
    {
        "m11_contract": (
            "benchmarks/falsification/m11-contract-v1.json",
            "yieldforge.m11-realistic-falsification-contract.v1",
            "contract_id",
            "yfm11c-e956019aeef85350f2ffa9d3",
            "sha256:e956019aeef85350f2ffa9d351ab15539d1b23137d7566118f73c5f29882143b",
        ),
        "m11_population": (
            "benchmarks/falsification/m11-population-v1.json",
            "yieldforge.m11-population.v1",
            "population_id",
            "yfm11pop-a26084179d2e8f776630f8ac",
            "sha256:a26084179d2e8f776630f8ac272d5651069500a5df996869d20df9893ca0bc56",
        ),
        "m11_source_manifest": (
            "benchmarks/falsification/source-manifest-v1.json",
            "yieldforge.m11-source-manifest.v1",
            "source_manifest_id",
            "yfm11sm-54426d56dcccc07b667da56f",
            "sha256:54426d56dcccc07b667da56fd1106f2e463baf3aaf57358a50f47e76fc073605",
        ),
        "m0_contract": (
            "experiments/m0-contract-v1.json",
            "yieldforge.m0-contract.v1",
            "contract_id",
            "yfm0-29b7efe8ac2a0a9995c4f907",
            "sha256:29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f",
        ),
        "m7_frozen_baseline": (
            "experiments/results/m7-frozen-baseline-v1.0.1.json",
            "yieldforge.m7-frozen-baseline.v1",
            "freeze_id",
            "yfm7freeze-5c13c3fe531828d8cd986c39",
            "sha256:5c13c3fe531828d8cd986c3980104afebb2d959a2885ed4c3b61de29961bf7a8",
        ),
        "m9_two_ply_repair": (
            "experiments/results/m9-two-ply-repair-validation-yfm9r-db0829451b1b0393f2d22559.json",
            "yieldforge.m9-two-ply-repair-validation.v1",
            "result_id",
            "yfm9r-db0829451b1b0393f2d22559",
            "sha256:db0829451b1b0393f2d2255990ade1ce783b27a8527f73f3c7bf07e6716438ba",
        ),
    }
)

_MODEL_BY_ROLE = MappingProxyType(
    {
        "m11_contract": M11ExperimentContract,
        "m11_population": M11Population,
        "m11_source_manifest": M11SourceManifest,
        "m0_contract": M0ExperimentContract,
        "m7_frozen_baseline": M7FrozenBaseline,
    }
)


class M11Gate3ConfigError(ValueError):
    """Gate 3 preregistration generation, publication, or read-back failed closed."""


class M11Gate3RootBinding(FrozenExperimentModel):
    """One exact upstream root admitted by the Gate 3 preregistration."""

    schema_version: Literal["yieldforge.m11-gate3-root-binding.v1"] = (
        "yieldforge.m11-gate3-root-binding.v1"
    )
    binding_id: StrictStr = Field(pattern=r"^yfm11g3b-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    role: M11Gate3RootRole
    repository_path: StrictStr = Field(min_length=1)
    parent_schema_version: StrictStr = Field(min_length=1)
    semantic_id: StrictStr = Field(min_length=1)
    semantic_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    raw_file_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_exact_root_and_identity(self) -> Self:
        path, schema, _id_field, semantic_id, content_sha256 = _ROOT_SPECS[self.role]
        if (
            self.repository_path,
            self.parent_schema_version,
            self.semantic_id,
            self.semantic_content_sha256,
        ) != (path, schema, semantic_id, content_sha256):
            raise ValueError("Gate 3 root binding differs from the frozen official root")
        digest = semantic_sha256(
            self,
            excluded_fields={"binding_id", "content_sha256"},
        )
        if self.binding_id != f"yfm11g3b-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 root binding identity does not match semantic content")
        return self


class M11Gate3ProjectionConfig(FrozenExperimentModel):
    """Exact boundary between source-faithful M11 data and the reused M7 engine."""

    material_sharding_rule: Literal["one_independent_m7_replay_shard_per_exact_material_key"] = (
        "one_independent_m7_replay_shard_per_exact_material_key"
    )
    shard_order: Literal["sorted_exact_material_key"] = "sorted_exact_material_key"
    shard_event_order: Literal["local_contiguous_order_preserving_global_chronology"] = (
        "local_contiguous_order_preserving_global_chronology"
    )
    shard_merge_rule: Literal["merge_decisions_by_original_global_event_position"] = (
        "merge_decisions_by_original_global_event_position"
    )
    horizon_rule: Literal["max_due_at_across_stream_shared_by_every_material_shard"] = (
        "max_due_at_across_stream_shared_by_every_material_shard"
    )
    reference_area_rule: Literal["exact_corpus_material_reference_area_registry"] = (
        "exact_corpus_material_reference_area_registry"
    )
    purchase_rate_formula: Literal["virgin_cost_per_reference_area / reference_area"] = (
        "virgin_cost_per_reference_area / reference_area"
    )
    storage_rate_formula: Literal[
        "storage_per_reference_area_30_days / (reference_area * 30 * 24)"
    ] = "storage_per_reference_area_30_days / (reference_area * 30 * 24)"
    return_rate_formula: Literal["return_handling_per_eligible_remnant"] = (
        "return_handling_per_eligible_remnant"
    )
    retrieval_rate_formula: Literal["retrieval_handling_per_remnant_use"] = (
        "retrieval_handling_per_remnant_use"
    )
    scrap_terminal_rate_formula: Literal[
        "scrap_and_terminal_credit_per_reference_area / reference_area"
    ] = "scrap_and_terminal_credit_per_reference_area / reference_area"
    ledger_rounding: Literal["six_decimal_half_up"] = "six_decimal_half_up"
    compatibility_dto_semantics: Literal[
        "source_faithful_engine_projection_not_native_m6_or_m2_provenance"
    ] = "source_faithful_engine_projection_not_native_m6_or_m2_provenance"
    compatibility_dto_publication: Literal["forbidden"] = "forbidden"
    loco_archive_placeholders: Literal[
        "deterministic_engine_only_never_published_as_m2_evidence"
    ] = "deterministic_engine_only_never_published_as_m2_evidence"
    registered_candidate_rule: Literal[
        "all_and_only_pack_registered_candidates_never_gate1_selection_only"
    ] = "all_and_only_pack_registered_candidates_never_gate1_selection_only"
    known_only_mask: Literal["physically_omit_unknown_instances_problems_and_candidates"] = (
        "physically_omit_unknown_instances_problems_and_candidates"
    )
    known_event_predicate: Literal["known_at_less_than_or_equal_to_current_release_at"] = (
        "known_at_less_than_or_equal_to_current_release_at"
    )


class M11Gate3PolicyConfig(FrozenExperimentModel):
    """Calibration-frozen B registry and identical paired F/K search."""

    model_config = ConfigDict(revalidate_instances="always")

    registered_m7_policies: tuple[M7PolicyIdentity, ...] = registered_policy_identities()
    additional_baseline_policy_id: Literal["known_only_m9_two_ply_scrap"] = (
        "known_only_m9_two_ply_scrap"
    )
    baseline_selection_unit: Literal["per_corpus_eight_calibration_streams"] = (
        "per_corpus_eight_calibration_streams"
    )
    baseline_economic_arm: Literal["central"] = "central"
    baseline_selection_rule: Literal[
        "lowest_mean_net_cost_then_invalid_median_sheet_openings_policy_id"
    ] = "lowest_mean_net_cost_then_invalid_median_sheet_openings_policy_id"
    full_and_known_algorithm: Literal["m9_complete_two_ply_reoptimization"] = (
        "m9_complete_two_ply_reoptimization"
    )
    full_and_known_objective: Literal["scrap_only"] = "scrap_only"
    full_and_known_objective_definition: Literal[
        "m7_final_net_cost_including_terminal_scrap_credit"
    ] = "m7_final_net_cost_including_terminal_scrap_credit"
    search_depth: Literal[2] = 2
    tie_break: Literal["bounded_cost_then_baseline_then_action_id"] = (
        "bounded_cost_then_baseline_then_action_id"
    )
    action_catalog_requirement: Literal["complete_no_truncation"] = "complete_no_truncation"
    shared_parity: Literal["same_candidates_actions_algorithm_compute_and_tie_rule"] = (
        "same_candidates_actions_algorithm_compute_and_tie_rule"
    )
    full_visibility: Literal["complete_remaining_released_and_unreleased_suffix"] = (
        "complete_remaining_released_and_unreleased_suffix"
    )
    known_visibility: Literal["firm_known_prefix_only_with_physical_unknown_mask"] = (
        "firm_known_prefix_only_with_physical_unknown_mask"
    )
    confirmation_inputs_used: Literal[False] = False

    @model_validator(mode="after")
    def require_exact_policy_registry(self) -> Self:
        if self.registered_m7_policies != registered_policy_identities():
            raise ValueError("Gate 3 baseline registry differs from all five M7 policies")
        return self


M11ForecastRegistryId = Literal[
    "last_seen@3",
    "last_seen@6",
    "modal_trailing_12@3",
    "modal_trailing_12@6",
    "cycle_last_4@4",
    "cycle_last_12@12",
]

_FORECAST_SPECS = MappingProxyType(
    {
        "last_seen@3": ("last_seen", 1, 3),
        "last_seen@6": ("last_seen", 1, 6),
        "modal_trailing_12@3": ("modal_trailing_12", 12, 3),
        "modal_trailing_12@6": ("modal_trailing_12", 12, 6),
        "cycle_last_4@4": ("cycle_last_4", 4, 4),
        "cycle_last_12@12": ("cycle_last_12", 12, 12),
    }
)


class M11Gate3ForecastVariant(FrozenExperimentModel):
    """One finite causal deployable-policy forecast candidate."""

    registry_id: M11ForecastRegistryId
    generator: Literal["last_seen", "modal_trailing_12", "cycle_last_4", "cycle_last_12"]
    history_release_slots: StrictInt = Field(ge=1, le=12)
    maximum_unknown_release_slots: StrictInt = Field(ge=1, le=12)

    @model_validator(mode="after")
    def require_registered_variant(self) -> Self:
        if (self.generator, self.history_release_slots, self.maximum_unknown_release_slots) != (
            _FORECAST_SPECS[self.registry_id]
        ):
            raise ValueError("Gate 3 forecast variant differs from the finite registry")
        return self


def _forecast_registry() -> tuple[M11Gate3ForecastVariant, ...]:
    return tuple(
        M11Gate3ForecastVariant(
            registry_id=registry_id,
            generator=generator,
            history_release_slots=history,
            maximum_unknown_release_slots=horizon,
        )
        for registry_id, (generator, history, horizon) in _FORECAST_SPECS.items()
    )


class M11Gate3ForecastConfig(FrozenExperimentModel):
    """Complete causal D/D0 calibration registry and leakage boundary."""

    model_config = ConfigDict(revalidate_instances="always")

    registry: tuple[M11Gate3ForecastVariant, ...] = Field(min_length=6, max_length=6)
    suffix_rule: Literal["maximum_unknown_release_slots"] = "maximum_unknown_release_slots"
    synthetic_event_payload_rule: Literal[
        "copy_only_a_previously_released_payload_and_material_pair"
    ] = "copy_only_a_previously_released_payload_and_material_pair"
    synthetic_timestamp_rule: Literal["use_frozen_calendar_release_slots"] = (
        "use_frozen_calendar_release_slots"
    )
    hidden_field_access: Literal["forbidden"] = "forbidden"
    modal_tie_break: Literal["count_then_most_recent_occurrence_then_ids"] = (
        "count_then_most_recent_occurrence_then_ids"
    )
    cycle_rule: Literal[
        "repeat_last_P_released_pairs_fallback_to_last_seen_when_history_shorter_than_P"
    ] = "repeat_last_P_released_pairs_fallback_to_last_seen_when_history_shorter_than_P"
    known_firm_prefix_rule: Literal["prepend_complete_firm_known_prefix"] = (
        "prepend_complete_firm_known_prefix"
    )
    selection_unit: Literal["per_corpus_eight_calibration_streams"] = (
        "per_corpus_eight_calibration_streams"
    )
    selection_rule: Literal["lowest_central_calibration_aggregate_then_config_id"] = (
        "lowest_central_calibration_aggregate_then_config_id"
    )
    forecast_enabled_policy: Literal["selected_config_same_m9_two_ply_executor"] = (
        "selected_config_same_m9_two_ply_executor"
    )
    forecast_disabled_rule: Literal[
        "same_executor_and_selected_config_with_forecast_horizon_zero"
    ] = "same_executor_and_selected_config_with_forecast_horizon_zero"
    confirmation_inputs_used: Literal[False] = False

    @model_validator(mode="after")
    def require_exact_registry(self) -> Self:
        if self.registry != _forecast_registry():
            raise ValueError("Gate 3 forecast registry differs from the frozen six variants")
        return self


M11Gate3ExactAuditArm = Literal["central", "adverse", "null"]


class M11Gate3ExactAuditArmRule(FrozenExperimentModel):
    """Outcome-blind execution semantics for one registered exact-audit arm."""

    audit_arm: M11Gate3ExactAuditArm
    economic_profile: Literal["central", "adverse"]
    event_slice_rule: Literal["unmodified_registered_three_event_slice"] = (
        "unmodified_registered_three_event_slice"
    )
    material_rule: Literal[
        "preserve_registered_material_keys",
        "unique_material_key_per_event_information_null",
    ]
    candidate_rule: Literal["retain_all_registered_candidates"] = "retain_all_registered_candidates"

    @model_validator(mode="after")
    def require_exact_arm_mapping(self) -> Self:
        expected = {
            "central": ("central", "preserve_registered_material_keys"),
            "adverse": ("adverse", "preserve_registered_material_keys"),
            "null": ("central", "unique_material_key_per_event_information_null"),
        }[self.audit_arm]
        if (self.economic_profile, self.material_rule) != expected:
            raise ValueError("Gate 3 exact-audit arm semantics differ from the frozen mapping")
        return self


def _exact_audit_arm_registry() -> tuple[M11Gate3ExactAuditArmRule, ...]:
    return (
        M11Gate3ExactAuditArmRule(
            audit_arm="central",
            economic_profile="central",
            material_rule="preserve_registered_material_keys",
        ),
        M11Gate3ExactAuditArmRule(
            audit_arm="adverse",
            economic_profile="adverse",
            material_rule="preserve_registered_material_keys",
        ),
        M11Gate3ExactAuditArmRule(
            audit_arm="null",
            economic_profile="central",
            material_rule="unique_material_key_per_event_information_null",
        ),
    )


class M11Gate3ControlConfig(FrozenExperimentModel):
    """Candidate, terminal, eligibility, adverse, and validity-control semantics."""

    lectra_ordinary: Literal[
        "committed_valid_m2_seeds_0_through_3_constrained_by_pack_registration"
    ] = "committed_valid_m2_seeds_0_through_3_constrained_by_pack_registration"
    lectra_expanded: Literal[
        "validated_m2_seeds_0_through_15_same_solver_config_and_10_seconds"
    ] = "validated_m2_seeds_0_through_15_same_solver_config_and_10_seconds"
    lectra_expanded_seed_order: tuple[StrictInt, ...] = tuple(range(16))
    lectra_expanded_seconds_per_seed: Literal[10] = 10
    loco_ordinary: Literal["frozen_selected_minimum_area_generated_fallback"] = (
        "frozen_selected_minimum_area_generated_fallback"
    )
    loco_expanded: Literal["all_eight_frozen_shelf_width_candidates"] = (
        "all_eight_frozen_shelf_width_candidates"
    )
    expanded_role: Literal["support_diagnostic_only_never_primary_or_rescue"] = (
        "support_diagnostic_only_never_primary_or_rescue"
    )
    minimum_ordinary_availability_percent: Literal[60.0] = 60.0
    candidate_parity_rule: Literal[
        "same_verified_candidate_hashes_projection_seeds_config_compute_eligibility_and_actions"
    ] = "same_verified_candidate_hashes_projection_seeds_config_compute_eligibility_and_actions"
    eligibility_order: tuple[Literal["primary"], Literal["conservative"], Literal["permissive"]] = (
        "primary",
        "conservative",
        "permissive",
    )
    conservative_role: Literal["required_non_red_retain_sensitivity"] = (
        "required_non_red_retain_sensitivity"
    )
    permissive_role: Literal["diagnostic_only_last"] = "diagnostic_only_last"
    primary_terminal_objective: Literal["scrap_only"] = "scrap_only"
    zero_terminal_sensitivity: Literal["add_back_terminal_credit_only"] = (
        "add_back_terminal_credit_only"
    )
    required_non_red_sensitivity_savings_percent: Literal[1.5] = 1.5
    required_non_red_sensitivity_unknown_points: Literal[0.5] = 0.5
    fixed_adverse_required: Literal[True] = True
    hard_null_rule: Literal["B_and_F_equal_within_accounting_tolerance"] = (
        "B_and_F_equal_within_accounting_tolerance"
    )
    shuffled_twin_rule: Literal["mandatory_no_signal_unique_material_control"] = (
        "mandatory_no_signal_unique_material_control"
    )
    no_signal_rule: Literal["mean_above_0_5_invalidates_and_0_3_through_0_5_requires_diagnosis"] = (
        "mean_above_0_5_invalidates_and_0_3_through_0_5_requires_diagnosis"
    )
    exact_short_case_rule: Literal[
        "two_ply_root_matches_exact_optimum_for_every_registered_case"
    ] = "two_ply_root_matches_exact_optimum_for_every_registered_case"
    exact_audit_arm_registry: tuple[M11Gate3ExactAuditArmRule, ...] = _exact_audit_arm_registry()
    accounting_rule: Literal["complete_six_decimal_half_up_ledger_reconciliation"] = (
        "complete_six_decimal_half_up_ledger_reconciliation"
    )
    incomplete_stream_rule: Literal["invalid_no_numeric_imputation"] = (
        "invalid_no_numeric_imputation"
    )
    zero_support_denominator_rule: Literal["fail_support_gate_not_validity"] = (
        "fail_support_gate_not_validity"
    )

    @model_validator(mode="after")
    def require_expanded_registry(self) -> Self:
        if self.lectra_expanded_seed_order != tuple(range(16)):
            raise ValueError("Gate 3 Lectra expanded seeds differ from 0 through 15")
        if self.exact_audit_arm_registry != _exact_audit_arm_registry():
            raise ValueError("Gate 3 exact-audit arm registry differs from the frozen mapping")
        return self


class M11Gate3ExecutionConfig(FrozenExperimentModel):
    """Cost-saving early-stop order without changing any pass threshold."""

    validity_controls_precede_central: Literal[True] = True
    central_order: tuple[
        Literal["loco-2dics"],
        Literal["lectra-m3-m4"],
        Literal["equal-corpus-pool"],
    ] = ("loco-2dics", "lectra-m3-m4", "equal-corpus-pool")
    central_necessary_rules: tuple[StrictStr, ...] = (
        "mean_savings_at_least_2_5",
        "unknown_at_least_1_5",
        "bootstrap_lower_mean_savings_strictly_above_zero",
        "median_savings_strictly_above_zero",
        "positive_stream_fraction_strictly_above_50",
    )
    valid_central_failure_state: Literal["insufficient_headroom"] = "insufficient_headroom"
    valid_central_failure_action: Literal["ABANDON"] = "ABANDON"
    terminal_skip_marker: Literal["skipped_terminal_prerequisite"] = "skipped_terminal_prerequisite"
    terminal_skip_scope: Literal["all_later_corpora_sensitivities_support_and_deployable"] = (
        "all_later_corpora_sensitivities_support_and_deployable"
    )
    downstream_requires_all_central_pass: Literal[True] = True
    downstream_order: tuple[StrictStr, ...] = (
        "fixed_adverse",
        "zero_terminal_credit",
        "conservative_eligibility",
        "expanded_catalog_diagnostic",
        "permissive_eligibility_diagnostic",
        "support_metrics",
        "deployable_capture",
    )

    @model_validator(mode="after")
    def require_exact_execution_order(self) -> Self:
        if self.central_necessary_rules != (
            "mean_savings_at_least_2_5",
            "unknown_at_least_1_5",
            "bootstrap_lower_mean_savings_strictly_above_zero",
            "median_savings_strictly_above_zero",
            "positive_stream_fraction_strictly_above_50",
        ):
            raise ValueError("Gate 3 central necessary rules differ from the frozen stop")
        if self.downstream_order != (
            "fixed_adverse",
            "zero_terminal_credit",
            "conservative_eligibility",
            "expanded_catalog_diagnostic",
            "permissive_eligibility_diagnostic",
            "support_metrics",
            "deployable_capture",
        ):
            raise ValueError("Gate 3 downstream execution order differs from the frozen stop")
        return self


class M11Gate3ConfirmationConfig(FrozenExperimentModel):
    """Complete content-addressed M11 Gate 3 preregistration, with no outcomes."""

    model_config = ConfigDict(
        **FrozenExperimentModel.model_config,
        revalidate_instances="always",
    )

    schema_version: Literal["yieldforge.m11-gate3-confirmation-config.v1"] = (
        "yieldforge.m11-gate3-confirmation-config.v1"
    )
    config_id: StrictStr = Field(pattern=r"^yfm11g3c-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["frozen_before_confirmation"] = "frozen_before_confirmation"
    roots: tuple[M11Gate3RootBinding, ...] = Field(min_length=6, max_length=6)
    corpus_order: tuple[Literal["lectra-m3-m4"], Literal["loco-2dics"]] = (
        "lectra-m3-m4",
        "loco-2dics",
    )
    calibration_streams_per_corpus: Literal[8] = 8
    confirmation_streams_per_corpus: Literal[20] = 20
    events_per_stream: Literal[24] = 24
    projection: M11Gate3ProjectionConfig
    policy: M11Gate3PolicyConfig
    forecast: M11Gate3ForecastConfig
    controls: M11Gate3ControlConfig
    execution: M11Gate3ExecutionConfig
    thresholds: M11Thresholds
    confirmation_inputs_used: Literal[False] = False
    claim_ceiling: StrictStr = M11_CLAIM_CEILING
    productization_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_complete_frozen_identity(self) -> Self:
        if tuple(item.role for item in self.roots) != _ROOT_ROLE_ORDER:
            raise ValueError("Gate 3 roots differ from the frozen role order")
        if len({item.binding_id for item in self.roots}) != len(self.roots):
            raise ValueError("Gate 3 root bindings must be unique")
        if self.claim_ceiling != M11_CLAIM_CEILING or self.productization_authorized:
            raise ValueError("Gate 3 configuration exceeds the M11 claim ceiling")
        if self.thresholds != M11Thresholds():
            raise ValueError("Gate 3 thresholds differ from the M11 decision contract")
        digest = semantic_sha256(
            self,
            excluded_fields={"config_id", "content_sha256"},
        )
        if self.config_id != f"yfm11g3c-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 confirmation config identity does not match semantic content")
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise M11Gate3ConfigError(f"duplicate Gate 3 JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise M11Gate3ConfigError(f"nonfinite Gate 3 JSON constant: {value}")


def _parse_json(data: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except M11Gate3ConfigError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise M11Gate3ConfigError("Gate 3 artifact is not strict JSON") from error
    if type(value) is not dict:
        raise M11Gate3ConfigError("Gate 3 artifact root must be a JSON object")
    return value


def _read_bounded_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    candidate = Path(path)
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > maximum_bytes
        ):
            raise M11Gate3ConfigError("Gate 3 artifact must be a bounded regular file")
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            remaining = maximum_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            during = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = candidate.lstat()
    except M11Gate3ConfigError:
        raise
    except OSError as error:
        raise M11Gate3ConfigError("Gate 3 artifact could not be read safely") from error
    before_fingerprint = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if (
        len(raw) > maximum_bytes
        or len(raw) != before.st_size
        or before_fingerprint
        != (
            during.st_dev,
            during.st_ino,
            during.st_size,
            during.st_mtime_ns,
            during.st_ctime_ns,
        )
        or before_fingerprint
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    ):
        raise M11Gate3ConfigError("Gate 3 artifact changed during read-back")
    return raw


def _canonical_mapping_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_root_models(repository_root: Path) -> dict[M11Gate3RootRole, tuple[bytes, object]]:
    root = Path(repository_root).resolve()
    loaded: dict[M11Gate3RootRole, tuple[bytes, object]] = {}
    for role in _ROOT_ROLE_ORDER:
        relative_path, schema, id_field, semantic_id, content_sha256 = _ROOT_SPECS[role]
        raw = _read_bounded_regular_file(root / relative_path, maximum_bytes=_MAX_ROOT_BYTES)
        payload = _parse_json(raw)
        if (
            payload.get("schema_version") != schema
            or payload.get(id_field) != semantic_id
            or payload.get("content_sha256") != content_sha256
        ):
            raise M11Gate3ConfigError(f"Gate 3 {role} root identity differs")
        if role == "m9_two_ply_repair":
            digest = semantic_sha256(
                payload,
                excluded_fields={"result_id", "content_sha256"},
            )
            if (
                payload.get("evaluation_partition_opened") is not False
                or payload.get("repair_semantics")
                != {
                    "action_catalogs": "complete",
                    "continuation_policy": "frozen_m7",
                    "search_depth": 2,
                    "tie_break": "bounded_cost_then_baseline_then_action_id",
                }
                or payload.get("content_sha256") != f"sha256:{digest}"
                or raw != _canonical_mapping_bytes(payload)
            ):
                raise M11Gate3ConfigError("Gate 3 M9 two-ply root is not sealed canonical evidence")
            loaded[role] = (raw, payload)
            continue
        model = _MODEL_BY_ROLE[role]
        try:
            strict = model.model_validate_json(raw, strict=True)
        except ValidationError as error:
            raise M11Gate3ConfigError(f"Gate 3 {role} root failed strict validation") from error
        if raw != canonical_pretty_json_bytes(strict):
            raise M11Gate3ConfigError(f"Gate 3 {role} root is not canonical")
        loaded[role] = (raw, strict)

    contract = loaded["m11_contract"][1]
    population = loaded["m11_population"][1]
    source_manifest = loaded["m11_source_manifest"][1]
    m0 = loaded["m0_contract"][1]
    m7 = loaded["m7_frozen_baseline"][1]
    if not isinstance(contract, M11ExperimentContract):
        raise M11Gate3ConfigError("Gate 3 contract root has the wrong strict type")
    if not isinstance(population, M11Population) or not isinstance(
        source_manifest, M11SourceManifest
    ):
        raise M11Gate3ConfigError("Gate 3 population roots have the wrong strict type")
    if not isinstance(m0, M0ExperimentContract) or not isinstance(m7, M7FrozenBaseline):
        raise M11Gate3ConfigError("Gate 3 engine roots have the wrong strict type")
    if (
        population.contract_id != contract.contract_id
        or population.contract_content_sha256 != contract.content_sha256
        or population.source_manifest_id != source_manifest.source_manifest_id
        or population.source_manifest_sha256 != source_manifest.content_sha256
        or m7.m0_contract_id != m0.contract_id
        or m7.m0_contract_sha256 != m0.content_sha256
        or m7.evaluation_partition_opened
    ):
        raise M11Gate3ConfigError("Gate 3 upstream roots do not cross-bind or remain sealed")
    return loaded


def _build_root_binding(
    role: M11Gate3RootRole,
    raw: bytes,
) -> M11Gate3RootBinding:
    path, schema, _id_field, semantic_id, semantic_content_sha256 = _ROOT_SPECS[role]
    semantic = {
        "schema_version": "yieldforge.m11-gate3-root-binding.v1",
        "role": role,
        "repository_path": path,
        "parent_schema_version": schema,
        "semantic_id": semantic_id,
        "semantic_content_sha256": semantic_content_sha256,
        "raw_file_sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}",
    }
    digest = semantic_sha256(semantic)
    return M11Gate3RootBinding(
        binding_id=f"yfm11g3b-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def build_gate3_confirmation_config(repository_root: Path) -> M11Gate3ConfirmationConfig:
    """Build the outcome-blind Gate 3 preregistration from exact committed roots."""

    loaded = _load_root_models(Path(repository_root))
    roots = tuple(_build_root_binding(role, loaded[role][0]) for role in _ROOT_ROLE_ORDER)
    projection = M11Gate3ProjectionConfig()
    policy = M11Gate3PolicyConfig()
    forecast = M11Gate3ForecastConfig(registry=_forecast_registry())
    controls = M11Gate3ControlConfig()
    execution = M11Gate3ExecutionConfig()
    thresholds = M11Thresholds()
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate3-confirmation-config.v1",
        "status": "frozen_before_confirmation",
        "roots": [item.model_dump(mode="json") for item in roots],
        "corpus_order": ["lectra-m3-m4", "loco-2dics"],
        "calibration_streams_per_corpus": 8,
        "confirmation_streams_per_corpus": 20,
        "events_per_stream": 24,
        "projection": projection.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "forecast": forecast.model_dump(mode="json"),
        "controls": controls.model_dump(mode="json"),
        "execution": execution.model_dump(mode="json"),
        "thresholds": thresholds.model_dump(mode="json"),
        "confirmation_inputs_used": False,
        "claim_ceiling": M11_CLAIM_CEILING,
        "productization_authorized": False,
    }
    digest = semantic_sha256(semantic)
    return M11Gate3ConfirmationConfig(
        config_id=f"yfm11g3c-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        projection=projection,
        policy=policy,
        forecast=forecast,
        controls=controls,
        execution=execution,
        thresholds=thresholds,
    )


def canonical_gate3_config_bytes(config: M11Gate3ConfirmationConfig) -> bytes:
    """Return exact canonical bytes after strict detached revalidation."""

    strict = M11Gate3ConfirmationConfig.model_validate(
        config.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    return canonical_pretty_json_bytes(strict)


def _validate_gate3_config_bytes(data: bytes) -> bytes:
    _parse_json(data)
    strict = M11Gate3ConfirmationConfig.model_validate_json(data, strict=True)
    return canonical_pretty_json_bytes(strict)


def publish_gate3_confirmation_config(
    output_directory: Path,
    config: M11Gate3ConfirmationConfig,
) -> Path:
    """Publish the immutable preregistration without executing confirmation."""

    strict = M11Gate3ConfirmationConfig.model_validate(
        config.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    path = Path(output_directory) / GATE3_CONFIG_FILENAME
    try:
        return publish_immutable_artifact(
            path,
            canonical_gate3_config_bytes(strict),
            validate=_validate_gate3_config_bytes,
            label="M11 Gate 3 confirmation config",
        )
    except M8ArtifactPublicationError as error:
        raise M11Gate3ConfigError("M11 Gate 3 immutable publication failed") from error


def load_gate3_confirmation_config(
    path: Path,
    *,
    repository_root: Path,
) -> M11Gate3ConfirmationConfig:
    """Strict-read, root-authenticate, and regenerate one Gate 3 preregistration."""

    raw = _read_bounded_regular_file(Path(path), maximum_bytes=_MAX_CONFIG_BYTES)
    _parse_json(raw)
    try:
        config = M11Gate3ConfirmationConfig.model_validate_json(raw, strict=True)
    except ValidationError as error:
        raise M11Gate3ConfigError("M11 Gate 3 config failed strict validation") from error
    if raw != canonical_gate3_config_bytes(config):
        raise M11Gate3ConfigError("M11 Gate 3 config encoding is not canonical")
    regenerated = build_gate3_confirmation_config(Path(repository_root))
    if config != regenerated:
        raise M11Gate3ConfigError("M11 Gate 3 config differs from authenticated frozen roots")
    return config


__all__ = [
    "GATE3_CONFIG_FILENAME",
    "M11Gate3ConfigError",
    "M11Gate3ConfirmationConfig",
    "M11Gate3ControlConfig",
    "M11Gate3ExecutionConfig",
    "M11Gate3ExactAuditArmRule",
    "M11Gate3ForecastConfig",
    "M11Gate3ForecastVariant",
    "M11Gate3PolicyConfig",
    "M11Gate3ProjectionConfig",
    "M11Gate3RootBinding",
    "build_gate3_confirmation_config",
    "canonical_gate3_config_bytes",
    "load_gate3_confirmation_config",
    "publish_gate3_confirmation_config",
]
