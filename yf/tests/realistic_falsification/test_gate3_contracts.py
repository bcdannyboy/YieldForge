from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.contracts import M11_CLAIM_CEILING

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE3_CONFIG_FILENAME = "m11-gate3-config-v1.json"
COMMITTED_CONFIG = REPO_ROOT / "benchmarks/falsification" / GATE3_CONFIG_FILENAME


@pytest.fixture(scope="module")
def gate3() -> ModuleType:
    try:
        return importlib.import_module("yieldforge.realistic_falsification.gate3_contracts")
    except ModuleNotFoundError as error:
        pytest.fail(f"Gate 3 contracts module is missing: {error}")


@pytest.fixture(scope="module")
def config(gate3: ModuleType) -> Any:
    return gate3.build_gate3_confirmation_config(REPO_ROOT)


def test_gate3_config_binds_exact_official_roots_before_confirmation(
    config: Any,
) -> None:
    assert config.status == "frozen_before_confirmation"
    assert config.confirmation_inputs_used is False
    assert config.productization_authorized is False
    assert config.claim_ceiling == M11_CLAIM_CEILING
    assert tuple(item.role for item in config.roots) == (
        "m11_contract",
        "m11_population",
        "m11_source_manifest",
        "m0_contract",
        "m7_frozen_baseline",
        "m9_two_ply_repair",
    )
    assert tuple(item.semantic_id for item in config.roots) == (
        "yfm11c-e956019aeef85350f2ffa9d3",
        "yfm11pop-a26084179d2e8f776630f8ac",
        "yfm11sm-54426d56dcccc07b667da56f",
        "yfm0-29b7efe8ac2a0a9995c4f907",
        "yfm7freeze-5c13c3fe531828d8cd986c39",
        "yfm9r-db0829451b1b0393f2d22559",
    )
    assert all(item.raw_file_sha256.startswith("sha256:") for item in config.roots)


def test_gate3_projection_freezes_material_sharding_horizon_and_nonpublication(
    config: Any,
) -> None:
    projection = config.projection
    assert projection.material_sharding_rule == (
        "one_independent_m7_replay_shard_per_exact_material_key"
    )
    assert projection.shard_event_order == "local_contiguous_order_preserving_global_chronology"
    assert projection.shard_merge_rule == "merge_decisions_by_original_global_event_position"
    assert projection.horizon_rule == "max_due_at_across_stream_shared_by_every_material_shard"
    assert projection.purchase_rate_formula == "virgin_cost_per_reference_area / reference_area"
    assert projection.storage_rate_formula == (
        "storage_per_reference_area_30_days / (reference_area * 30 * 24)"
    )
    assert projection.scrap_terminal_rate_formula == (
        "scrap_and_terminal_credit_per_reference_area / reference_area"
    )
    assert projection.compatibility_dto_semantics == (
        "source_faithful_engine_projection_not_native_m6_or_m2_provenance"
    )
    assert projection.compatibility_dto_publication == "forbidden"
    assert projection.loco_archive_placeholders == (
        "deterministic_engine_only_never_published_as_m2_evidence"
    )
    assert projection.known_only_mask == (
        "physically_omit_unknown_instances_problems_and_candidates"
    )
    assert projection.known_event_predicate == "known_at_less_than_or_equal_to_current_release_at"


def test_gate3_policy_registry_and_forecast_registry_are_finite_and_causal(
    config: Any,
) -> None:
    policy = config.policy
    assert tuple(item.name.value for item in policy.registered_m7_policies) == (
        "myopic_geometry",
        "remnant_first",
        "net_cost",
        "age_regularity",
        "known_order_lookahead",
    )
    assert policy.additional_baseline_policy_id == "known_only_m9_two_ply_scrap"
    assert policy.baseline_selection_unit == "per_corpus_eight_calibration_streams"
    assert policy.baseline_selection_rule == (
        "lowest_mean_net_cost_then_invalid_median_sheet_openings_policy_id"
    )
    assert policy.full_and_known_algorithm == "m9_complete_two_ply_reoptimization"
    assert policy.full_and_known_objective == "scrap_only"
    assert policy.full_and_known_objective_definition == (
        "m7_final_net_cost_including_terminal_scrap_credit"
    )
    assert policy.search_depth == 2
    assert policy.tie_break == "bounded_cost_then_baseline_then_action_id"
    assert policy.action_catalog_requirement == "complete_no_truncation"
    assert policy.shared_parity == "same_candidates_actions_algorithm_compute_and_tie_rule"

    forecast = config.forecast
    assert tuple(item.registry_id for item in forecast.registry) == (
        "last_seen@3",
        "last_seen@6",
        "modal_trailing_12@3",
        "modal_trailing_12@6",
        "cycle_last_4@4",
        "cycle_last_12@12",
    )
    assert tuple(item.maximum_unknown_release_slots for item in forecast.registry) == (
        3,
        6,
        3,
        6,
        4,
        12,
    )
    assert forecast.synthetic_event_payload_rule == (
        "copy_only_a_previously_released_payload_and_material_pair"
    )
    assert forecast.synthetic_timestamp_rule == "use_frozen_calendar_release_slots"
    assert forecast.hidden_field_access == "forbidden"
    assert forecast.modal_tie_break == "count_then_most_recent_occurrence_then_ids"
    assert forecast.cycle_rule == (
        "repeat_last_P_released_pairs_fallback_to_last_seen_when_history_shorter_than_P"
    )
    assert forecast.known_firm_prefix_rule == "prepend_complete_firm_known_prefix"
    assert forecast.selection_rule == "lowest_central_calibration_aggregate_then_config_id"
    assert forecast.forecast_disabled_rule == (
        "same_executor_and_selected_config_with_forecast_horizon_zero"
    )
    assert forecast.confirmation_inputs_used is False


def test_gate3_candidate_eligibility_terminal_and_adverse_controls_are_complete(
    config: Any,
) -> None:
    controls = config.controls
    assert controls.lectra_ordinary == (
        "committed_valid_m2_seeds_0_through_3_constrained_by_pack_registration"
    )
    assert controls.lectra_expanded == (
        "validated_m2_seeds_0_through_15_same_solver_config_and_10_seconds"
    )
    assert controls.loco_ordinary == "frozen_selected_minimum_area_generated_fallback"
    assert controls.loco_expanded == "all_eight_frozen_shelf_width_candidates"
    assert controls.expanded_role == "support_diagnostic_only_never_primary_or_rescue"
    assert controls.minimum_ordinary_availability_percent == 60.0
    assert controls.eligibility_order == ("primary", "conservative", "permissive")
    assert controls.conservative_role == "required_non_red_retain_sensitivity"
    assert controls.permissive_role == "diagnostic_only_last"
    assert controls.primary_terminal_objective == "scrap_only"
    assert controls.zero_terminal_sensitivity == "add_back_terminal_credit_only"
    assert controls.required_non_red_sensitivity_savings_percent == 1.5
    assert controls.required_non_red_sensitivity_unknown_points == 0.5
    assert controls.fixed_adverse_required is True
    assert controls.hard_null_rule == "B_and_F_equal_within_accounting_tolerance"
    assert controls.no_signal_rule == (
        "mean_above_0_5_invalidates_and_0_3_through_0_5_requires_diagnosis"
    )
    assert controls.exact_short_case_rule == (
        "two_ply_root_matches_exact_optimum_for_every_registered_case"
    )
    assert config.thresholds.model_dump(mode="python") == {
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


def test_gate3_execution_freezes_loco_first_early_stop_and_terminal_skips(
    config: Any,
) -> None:
    execution = config.execution
    assert execution.validity_controls_precede_central is True
    assert execution.central_order == (
        "loco-2dics",
        "lectra-m3-m4",
        "equal-corpus-pool",
    )
    assert execution.central_necessary_rules == (
        "mean_savings_at_least_2_5",
        "unknown_at_least_1_5",
        "bootstrap_lower_mean_savings_strictly_above_zero",
        "median_savings_strictly_above_zero",
        "positive_stream_fraction_strictly_above_50",
    )
    assert execution.valid_central_failure_state == "insufficient_headroom"
    assert execution.valid_central_failure_action == "ABANDON"
    assert execution.terminal_skip_marker == "skipped_terminal_prerequisite"
    assert execution.terminal_skip_scope == (
        "all_later_corpora_sensitivities_support_and_deployable"
    )
    assert execution.downstream_requires_all_central_pass is True
    assert execution.downstream_order == (
        "fixed_adverse",
        "zero_terminal_credit",
        "conservative_eligibility",
        "expanded_catalog_diagnostic",
        "permissive_eligibility_diagnostic",
        "support_metrics",
        "deployable_capture",
    )


def test_gate3_config_identity_and_nested_models_fail_closed(
    gate3: ModuleType,
    config: Any,
) -> None:
    payload = config.model_dump(mode="python", round_trip=True)
    payload["projection"]["horizon_rule"] = "last_release_at"
    with pytest.raises(ValidationError):
        gate3.M11Gate3ConfirmationConfig.model_validate(payload, strict=True)

    payload = config.model_dump(mode="python", round_trip=True)
    payload["config_id"] = "yfm11g3c-" + "0" * 24
    with pytest.raises(ValidationError, match="identity"):
        gate3.M11Gate3ConfirmationConfig.model_validate(payload, strict=True)


def test_gate3_config_has_no_confirmation_outputs(config: Any) -> None:
    payload = config.model_dump(mode="json")
    forbidden = {
        "stream_results",
        "confirmation_cells",
        "selected_baseline",
        "selected_forecast_config",
        "verdict",
        "evidence_state",
    }
    assert forbidden.isdisjoint(payload)
    assert payload["confirmation_inputs_used"] is False
    assert payload["forecast"]["confirmation_inputs_used"] is False


def test_gate3_config_publisher_is_canonical_idempotent_and_immutable(
    gate3: ModuleType,
    config: Any,
    tmp_path: Path,
) -> None:
    path = gate3.publish_gate3_confirmation_config(tmp_path, config)
    assert path.name == GATE3_CONFIG_FILENAME
    assert path.read_bytes() == gate3.canonical_gate3_config_bytes(config)
    assert gate3.load_gate3_confirmation_config(path, repository_root=REPO_ROOT) == config
    assert gate3.publish_gate3_confirmation_config(tmp_path, config) == path

    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="immutable|differs|publication"):
        gate3.publish_gate3_confirmation_config(tmp_path, config)


def test_gate3_config_loader_rejects_noncanonical_and_duplicate_json(
    gate3: ModuleType,
    config: Any,
    tmp_path: Path,
) -> None:
    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical"):
        gate3.load_gate3_confirmation_config(noncanonical, repository_root=REPO_ROOT)

    duplicate = tmp_path / "duplicate.json"
    canonical = gate3.canonical_gate3_config_bytes(config).decode("utf-8")
    duplicate.write_text(canonical.replace("{", '{"schema_version":"duplicate",', 1))
    with pytest.raises(ValueError, match="duplicate|strict"):
        gate3.load_gate3_confirmation_config(duplicate, repository_root=REPO_ROOT)


def test_committed_gate3_config_is_reproducible_canonical_readback(
    gate3: ModuleType,
    config: Any,
) -> None:
    assert COMMITTED_CONFIG.read_bytes() == gate3.canonical_gate3_config_bytes(config)
    assert (
        gate3.load_gate3_confirmation_config(COMMITTED_CONFIG, repository_root=REPO_ROOT) == config
    )
    digest = semantic_sha256(
        config,
        excluded_fields={"config_id", "content_sha256"},
    )
    assert config.config_id == f"yfm11g3c-{digest[:24]}"
