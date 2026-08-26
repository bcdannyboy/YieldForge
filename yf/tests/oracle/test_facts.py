"""Contracts for explicitly unchecked, portable M8 fact bundles."""

from __future__ import annotations

import hashlib
import json
import struct
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from yieldforge.oracle.experiment import M8CertificateProofResult
from yieldforge.oracle.facts import (
    M8ActionRootV2,
    M8CandidateScalarFactV2,
    M8CommonTransitionLemmaV2,
    M8FrontierFactV2,
    M8InfluenceFactV2,
    M8PortableTranslationBatch,
    M8StandardCandidateFactV2,
    M8UncheckedFactBundleV2,
    canonical_semantic_json,
    decode_canonical_f64,
    decode_canonical_utc,
    encode_canonical_f64,
    encode_canonical_utc,
    m8_bundle_sha256,
    m8_fact_sha256,
)
from yieldforge.oracle.proofs import M8ActionProof

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
RUNTIME = "sha256:" + "1" * 64
REPLAY = "sha256:" + "2" * 64
SUFFIX = "sha256:" + "3" * 64
FREEZE = "sha256:" + "4" * 64
STREAM = "yfts-" + "5" * 24


def _f(value: float) -> str:
    return encode_canonical_f64(value)


def _dt(hour: int) -> str:
    return encode_canonical_utc(datetime(2026, 8, 1, hour, tzinfo=UTC))


def _rank_key(
    cost: float,
    *,
    candidate_id: str,
    catalog_action_id: str,
    selected_stock_id: str = "current_standard_sheet",
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "component_kind": "f64",
            "f64_bits": _f(cost),
            "int_value": None,
            "string_value": None,
        },
        {
            "component_kind": "string",
            "f64_bits": None,
            "int_value": None,
            "string_value": candidate_id,
        },
        {
            "component_kind": "string",
            "f64_bits": None,
            "int_value": None,
            "string_value": selected_stock_id,
        },
        {
            "component_kind": "string",
            "f64_bits": None,
            "int_value": None,
            "string_value": catalog_action_id,
        },
    )


def _ledger(net_cost: float) -> dict[str, Any]:
    return {
        "purchase_cost_bits": _f(net_cost),
        "storage_cost_bits": _f(0.0),
        "return_handling_cost_bits": _f(0.0),
        "retrieval_handling_cost_bits": _f(0.0),
        "scrap_proceeds_bits": _f(0.0),
        "terminal_scrap_credit_bits": _f(0.0),
        "net_cost_bits": _f(net_cost),
    }


def _polygon(seed: str, area: float = 10.0) -> dict[str, Any]:
    return {
        "schema_version": "yieldforge.m8-portable-polygon.v2",
        "source_schema_version": "yieldforge.canonical-polygon.v1",
        "wkb_hex": "010300000000000000",
        "polygon_sha256": seed * 64,
        "area_bits": _f(area),
    }


def _remnant(remnant_id: str, *, seed: str = "6") -> dict[str, Any]:
    return {
        "schema_version": "yieldforge.m8-portable-remnant-stock.v2",
        "source_schema_version": "yieldforge.remnant-stock.v1",
        "remnant_id": remnant_id,
        "geometry": _polygon(seed),
        "material": {
            "schema_version": "yieldforge.m8-portable-material-identity.v2",
            "source_schema_version": "yieldforge.material-identity.v1",
            "material_code": "material-a",
            "grade": "grade-a",
            "thickness": "1.0",
            "surface": "plain",
            "grain": "none",
            "provenance": "assumed",
        },
        "root_sheet_area_bits": _f(10.0),
        "root_sheet_short_side_bits": _f(2.0),
        "lineage": {
            "schema_version": "yieldforge.m8-portable-remnant-lineage.v2",
            "source_schema_version": "yieldforge.remnant-lineage.v1",
            "root_stock_id": "current_standard_sheet",
            "parent_remnant_id": None,
            "ancestor_remnant_ids": (),
            "generation": 1,
            "source_candidate_id": "candidate-a",
            "source_component_sha256": seed * 64,
        },
    }


def _inventory_item(remnant_id: str, *, seed: str = "6") -> dict[str, Any]:
    return {"remnant": _remnant(remnant_id, seed=seed), "entered_at": _dt(0)}


def _accounting() -> dict[str, Any]:
    return {
        "parent_remnant_area_bits": _f(10.0),
        "placed_area_bits": _f(10.0),
        "process_loss_area_bits": _f(0.0),
        "retained_child_area_bits": _f(0.0),
        "scrap_area_bits": _f(0.0),
        "reconciliation_delta_bits": _f(0.0),
        "area_tolerance_bits": _f(1e-9),
    }


def _search_config() -> dict[str, Any]:
    return {
        "schema_version": "yieldforge.m8-portable-layout-search-config.v2",
        "source_schema_version": "yieldforge.m7-layout-fit-search-config.v1",
        "grid_columns": 5,
        "grid_rows": 5,
        "maximum_candidates": 256,
        "candidate_source_order": (
            "bbox_alignments",
            "vertex_alignments",
            "uniform_grid",
        ),
    }


def _search_config_sha256() -> str:
    digest = hashlib.sha256(canonical_semantic_json(_search_config())).hexdigest()
    return f"sha256:{digest}"


def _policy_context() -> dict[str, Any]:
    return {
        "action_id": "m7-standard:candidate-a",
        "kind": "open_standard_sheet",
        "candidate_id": "candidate-a",
        "candidate_width_bits": _f(5.5),
        "selected_stock_id": "current_standard_sheet",
        "immediate_net_cost_bits": _f(12.25),
        "selected_remnant_age_hours_bits": _f(0.0),
        "returned_regularity_bits": _f(0.0),
        "known_order_lookahead_term_bits": _f(0.0),
    }


def _action_evidence() -> dict[str, Any]:
    stock_id = "yfrm-" + "f" * 24
    return {
        "schema_version": "yieldforge.m8-portable-layout-action.v2",
        "source_schema_version": "yieldforge.m7-layout-action.v1",
        "action_id": "yfm7a-" + "8" * 24,
        "content_sha256": SHA_D,
        "problem_id": "yfm7p-" + "7" * 24,
        "problem_sha256": SHA_D,
        "candidate_set_id": "yfm7c-" + "e" * 24,
        "candidate_set_sha256": SHA_A,
        "candidate_id": "candidate-a",
        "kind": "open_standard_sheet",
        "selected_stock": _remnant(stock_id, seed="f"),
        "selected_remnant_id": None,
        "translation": {"x_bits": _f(0.0), "y_bits": _f(0.0)},
        "placements": (
            {
                "part_id": "part-a",
                "rotation_bits": _f(0.0),
                "translation": {"x_bits": _f(0.0), "y_bits": _f(0.0)},
            },
        ),
        "placed_parts": ({"part_id": "part-a", "geometry": _polygon("e")},),
        "search_result": None,
        "accounting": _accounting(),
        "returned_remnants": (),
    }


def _portable_common_transition() -> dict[str, Any]:
    remnant_id = "yfrm-" + "6" * 24
    inventory = (_inventory_item(remnant_id),)
    context = _policy_context()
    action = _action_evidence()
    rank = _rank_key(
        12.25,
        candidate_id="candidate-a",
        catalog_action_id="m7-standard:candidate-a",
    )
    return {
        "schema_version": "yieldforge.m8-portable-common-transition.v2",
        "source_schema_version": "yieldforge.m8-common-transition-fact.v1",
        "replay_input_id": "yfm7ri-" + "9" * 24,
        "replay_input_sha256": REPLAY,
        "semantic_runtime_sha256": RUNTIME,
        "event_position": 1,
        "cursor_before_sha256": SHA_B,
        "cursor_before": {
            "next_event_position": 1,
            "current_time": _dt(0),
            "inventory": inventory,
            "cumulative_costs": _ledger(0.0),
            "timestamp_group_sequence": 0,
            "timestamp_subsequence": 0,
            "previous_release": _dt(0),
        },
        "descriptor": {
            "action_id": "m7-standard:candidate-a",
            "kind": "open_standard_sheet",
            "candidate_id": "candidate-a",
            "selected_remnant_id": None,
            "evidence": None,
        },
        "selected_context": context,
        "action_binding": {
            "catalog_action_id": "m7-standard:candidate-a",
            "materialized_action_id": "yfm7a-" + "8" * 24,
            "context": context,
        },
        "event": {
            "sequence": 1,
            "event_id": "yfm7e-" + "a" * 24,
            "binding_id": "yfm7b-" + "b" * 24,
            "occurred_at": _dt(1),
            "timestamp_group_sequence": 1,
            "timestamp_subsequence": 0,
            "storage_interval_start": _dt(0),
            "storage_interval_end": _dt(1),
            "inventory_before": inventory,
            "action_set_size": 1,
            "standard_action_count": 1,
            "remnant_action_count": 0,
            "fit_search_query_count": 1,
            "fit_search_generated_candidate_count": 2,
            "fit_search_evaluated_candidate_count": 2,
            "fit_search_budget_truncated_count": 0,
            "policy_decision_key": ("action_id=m7-standard:candidate-a",),
            "action": action,
            "inventory_after": inventory,
            "delta_costs": _ledger(12.25),
            "cumulative_costs": _ledger(12.25),
        },
        "cursor_after_sha256": SHA_C,
        "cursor_after": {
            "next_event_position": 2,
            "current_time": _dt(1),
            "inventory": inventory,
            "cumulative_costs": _ledger(12.25),
            "timestamp_group_sequence": 1,
            "timestamp_subsequence": 0,
            "previous_release": _dt(1),
        },
        "event_id": "yfm7e-" + "a" * 24,
        "policy_rank": {
            "policy_name": "net_cost",
            "comparison_key": rank,
            "decision_key": ("action_id=m7-standard:candidate-a",),
        },
    }


def _fact(model: type[Any], payload: dict[str, Any]) -> Any:
    kind = payload["fact_kind"]
    return model.model_validate(
        {**payload, "fact_sha256": m8_fact_sha256(kind, payload)}, strict=True
    )


def _translation(*, runtime: str = RUNTIME, stream: str = STREAM) -> Any:
    return _fact(
        M8PortableTranslationBatch,
        {
            "schema_version": "yieldforge.m8-portable-translation-batch.v2",
            "fact_kind": "translation_batch",
            "semantic_runtime_sha256": runtime,
            "stream_id": stream,
            "event_position": 1,
            "remnant_id": "yfrm-" + "6" * 24,
            "candidate_id": "candidate-a",
            "fit_config_sha256": SHA_B,
            "search_config_sha256": _search_config_sha256(),
            "source_order": (
                "bbox_alignments",
                "vertex_alignments",
                "uniform_grid",
            ),
            "translations": (
                {"x_bits": _f(0.0), "y_bits": _f(1.25)},
                {"x_bits": _f(2.5), "y_bits": _f(3.75)},
            ),
            "generated_candidate_count": 2,
            "duplicate_candidate_count": 1,
            "evaluated_candidate_count": 2,
            "budget_truncated": False,
        },
    )


def _scalar(*, runtime: str = RUNTIME, stream: str = STREAM) -> Any:
    return _fact(
        M8CandidateScalarFactV2,
        {
            "schema_version": "yieldforge.m8-candidate-scalar-fact.v2",
            "fact_kind": "candidate_scalar",
            "semantic_runtime_sha256": runtime,
            "stream_id": stream,
            "problem_id": "yfm7p-" + "7" * 24,
            "problem_sha256": SHA_D,
            "candidate_set_id": "yfm7c-" + "e" * 24,
            "candidate_set_sha256": SHA_A,
            "candidate_id": "candidate-a",
            "source_transform_sha256": FREEZE,
            "material_partition": "temporal_event",
            "fit_config_sha256": SHA_B,
            "layout_area_bits": _f(11.0),
            "layout_width_bits": _f(5.5),
            "layout_height_bits": _f(2.0),
        },
    )


def _frontier(scalar: Any, *, runtime: str = RUNTIME, stream: str = STREAM) -> Any:
    return _fact(
        M8FrontierFactV2,
        {
            "schema_version": "yieldforge.m8-frontier-fact.v2",
            "fact_kind": "frontier",
            "semantic_runtime_sha256": runtime,
            "stream_id": stream,
            "problem_id": "yfm7p-" + "7" * 24,
            "problem_sha256": SHA_D,
            "candidate_set_id": "yfm7c-" + "e" * 24,
            "candidate_set_sha256": SHA_A,
            "material_partition": "temporal_event",
            "fit_config_sha256": SHA_B,
            "candidate_scalar_refs": (scalar.fact_sha256,),
            "retained_candidate_scalar_refs": (scalar.fact_sha256,),
            "dominance_evidence": (),
        },
    )


def _standard(*, runtime: str = RUNTIME, stream: str = STREAM) -> Any:
    return _fact(
        M8StandardCandidateFactV2,
        {
            "schema_version": "yieldforge.m8-standard-candidate-fact.v2",
            "fact_kind": "standard_candidate",
            "semantic_runtime_sha256": runtime,
            "stream_id": stream,
            "event_position": 1,
            "profile_position": 0,
            "candidate_id": "candidate-a",
            "catalog_action_id": "m7-standard:candidate-a",
            "materialized_action_id": "yfm7a-" + "8" * 24,
            "action_kind": "open_standard_sheet",
            "selected_stock_id": "current_standard_sheet",
            "policy_name": "net_cost",
            "candidate_width_bits": _f(5.5),
            "parent_remnant_area_bits": _f(10.0),
            "placed_area_bits": _f(10.0),
            "process_loss_area_bits": _f(0.0),
            "retained_child_area_bits": _f(0.0),
            "scrap_area_bits": _f(0.0),
            "reconciliation_delta_bits": _f(0.0),
            "accounting_area_tolerance_bits": _f(1e-9),
            "purchase_cost_bits": _f(12.25),
            "storage_cost_bits": _f(0.0),
            "return_handling_cost_bits": _f(0.0),
            "retrieval_handling_cost_bits": _f(0.0),
            "scrap_proceeds_bits": _f(0.0),
            "terminal_scrap_credit_bits": _f(0.0),
            "immediate_net_cost_bits": _f(12.25),
            "returned_remnant_count": 0,
            "returned_regularity_bits": _f(0.0),
            "selected_remnant_age_hours_bits": _f(0.0),
            "known_order_lookahead_term_bits": _f(0.0),
            "comparison_key": _rank_key(
                12.25,
                candidate_id="candidate-a",
                catalog_action_id="m7-standard:candidate-a",
            ),
            "decision_key": ("action_id=m7-standard:candidate-a",),
        },
    )


def _common(
    translation: Any,
    scalar: Any,
    frontier: Any,
    standard: Any,
    *,
    runtime: str = RUNTIME,
    stream: str = STREAM,
) -> Any:
    return _fact(
        M8CommonTransitionLemmaV2,
        {
            "schema_version": "yieldforge.m8-common-transition-lemma.v2",
            "fact_kind": "common_transition_lemma",
            "replay_input_id": "yfm7ri-" + "9" * 24,
            "replay_input_sha256": REPLAY,
            "semantic_runtime_sha256": runtime,
            "stream_id": stream,
            "event_position": 1,
            "event_id": "yfm7e-" + "a" * 24,
            "legacy_common_fact_sha256": SHA_A,
            "portable_transition": _portable_common_transition(),
            "problem_id": "yfm7p-" + "7" * 24,
            "problem_sha256": SHA_D,
            "candidate_set_id": "yfm7c-" + "e" * 24,
            "candidate_set_sha256": SHA_A,
            "fit_config_sha256": SHA_B,
            "search_config_sha256": _search_config_sha256(),
            "collision_backend": "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
            "jagua_executable_sha256": FREEZE,
            "cursor_before_sha256": SHA_B,
            "cursor_after_sha256": SHA_C,
            "cursor_before_inventory_remnant_ids": ("yfrm-" + "6" * 24,),
            "cursor_after_inventory_remnant_ids": ("yfrm-" + "6" * 24,),
            "event_occurred_at": _dt(1),
            "storage_interval_start": _dt(0),
            "storage_interval_end": _dt(1),
            "cursor_current_time": _dt(1),
            "cursor_previous_release": _dt(1),
            "previous_common_lemma_ref": None,
            "baseline_fallback_cursor_sha256": SHA_B,
            "minimum_standard_candidate_ref": standard.fact_sha256,
            "selected_catalog_action_id": "m7-standard:candidate-a",
            "selected_materialized_action_id": "yfm7a-" + "8" * 24,
            "selected_candidate_id": "candidate-a",
            "policy_name": "net_cost",
            "selected_comparison_key": _rank_key(
                12.25,
                candidate_id="candidate-a",
                catalog_action_id="m7-standard:candidate-a",
            ),
            "selected_decision_key": ("action_id=m7-standard:candidate-a",),
            "selected_immediate_net_cost_bits": _f(12.25),
            "event_net_cost_bits": _f(12.25),
            "candidate_scalar_refs": (scalar.fact_sha256,),
            "frontier_refs": (frontier.fact_sha256,),
            "standard_candidate_refs": (standard.fact_sha256,),
            "inventory_classifications": (
                {
                    "remnant_id": "yfrm-" + "6" * 24,
                    "classification": "counted_no_fit",
                    "material_matches": True,
                    "remnant_area_bits": _f(10.0),
                    "remnant_width_bits": _f(5.0),
                    "remnant_height_bits": _f(2.0),
                    "area_tolerance_bits": _f(1e-9),
                    "coordinate_tolerance_bits": _f(1e-9),
                    "frontier_ref": frontier.fact_sha256,
                    "candidate_scalar_refs": (scalar.fact_sha256,),
                    "translation_batch_refs": (translation.fact_sha256,),
                    "exact_replay_reason": None,
                },
            ),
            "evidence_mode": "counted_no_fit",
            "translation_batch_refs": (translation.fact_sha256,),
            "exact_replay_reason": None,
        },
    )


def _influence(common: Any, *, runtime: str = RUNTIME, stream: str = STREAM) -> Any:
    return _fact(
        M8InfluenceFactV2,
        {
            "schema_version": "yieldforge.m8-influence-fact.v2",
            "fact_kind": "influence",
            "semantic_runtime_sha256": runtime,
            "stream_id": stream,
            "event_position": 1,
            "common_lemma_ref": common.fact_sha256,
            "root_action_id": "yfm7a-" + "b" * 24,
            "common_catalog_action_id": "m7-standard:candidate-a",
            "common_materialized_action_id": "yfm7a-" + "8" * 24,
            "branch_catalog_action_id": "m7-standard:candidate-a",
            "branch_materialized_action_id": "yfm7a-" + "8" * 24,
            "state_before_sha256": SHA_B,
            "state_after_sha256": SHA_C,
            "inventory_delta": {
                "removed_remnant_ids": ("yfrm-" + "6" * 24,),
                "added_remnant_ids": (),
            },
            "classification": "no_fit",
            "evidence_mode": "scalar_no_fit",
            "rejection_evidence": (
                {
                    "direction": "removed",
                    "remnant_id": "yfrm-" + "6" * 24,
                    "candidate_id": "candidate-a",
                    "candidate_scalar_ref": next(iter(common.candidate_scalar_refs)),
                    "impossible": True,
                    "reason": "footprint_area_exceeds_remnant",
                    "layout_area_bits": _f(11.0),
                    "remnant_area_bits": _f(10.0),
                    "layout_width_bits": _f(5.5),
                    "remnant_width_bits": _f(5.0),
                    "layout_height_bits": _f(2.0),
                    "remnant_height_bits": _f(2.0),
                    "area_tolerance_bits": _f(1e-9),
                },
            ),
            "search_evidence": (),
            "competitor_evidence": (),
        },
    )


def _root(common: Any, influence: Any, *, runtime: str = RUNTIME, stream: str = STREAM) -> Any:
    return _fact(
        M8ActionRootV2,
        {
            "schema_version": "yieldforge.m8-action-root.v2",
            "fact_kind": "action_root",
            "semantic_runtime_sha256": runtime,
            "stream_id": stream,
            "action_id": "yfm7a-" + "b" * 24,
            "catalog_action_id": "catalog-current-a",
            "baseline_action_id": "yfm7a-" + "c" * 24,
            "baseline_catalog_action_id": "catalog-baseline-a",
            "start_event_position": 0,
            "stop_event_position": 2,
            "suffix_sha256": SUFFIX,
            "start_state_sha256": SHA_A,
            "initial_state_after_sha256": SHA_B,
            "final_state_sha256": SHA_C,
            "common_lemma_refs": (common.fact_sha256,),
            "influence_fact_refs": (influence.fact_sha256,),
            "final_net_cost_bits": _f(42.5),
        },
    )


def _bundle_payload(
    translation: Any,
    scalar: Any,
    frontier: Any,
    standard: Any,
    common: Any,
    influence: Any,
    root: Any,
) -> dict[str, Any]:
    return {
        "schema_version": "yieldforge.m8-unchecked-fact-bundle.v2",
        "bundle_kind": "unchecked_fact_bundle",
        "provenance": {
            "partition": "calibration",
            "replay_input_id": "yfm7ri-" + "9" * 24,
            "replay_input_sha256": REPLAY,
            "semantic_runtime_sha256": RUNTIME,
            "stream_id": STREAM,
            "stream_sha256": SHA_A,
            "regime": "no_signal",
            "temporal_seed": 41021,
            "suffix_sha256": SUFFIX,
            "freeze_id": "yfm7freeze-" + "d" * 24,
            "freeze_sha256": FREEZE,
            "evaluation_partition_opened": False,
        },
        "translation_batches": (translation.model_dump(mode="python"),),
        "candidate_scalar_facts": (scalar.model_dump(mode="python"),),
        "frontier_facts": (frontier.model_dump(mode="python"),),
        "standard_candidate_facts": (standard.model_dump(mode="python"),),
        "common_lemmas": (common.model_dump(mode="python"),),
        "influence_facts": (influence.model_dump(mode="python"),),
        "action_roots": (root.model_dump(mode="python"),),
    }


def _bundle() -> M8UncheckedFactBundleV2:
    translation = _translation()
    scalar = _scalar()
    frontier = _frontier(scalar)
    standard = _standard()
    common = _common(translation, scalar, frontier, standard)
    influence = _influence(common)
    root = _root(common, influence)
    payload = _bundle_payload(translation, scalar, frontier, standard, common, influence, root)
    return M8UncheckedFactBundleV2.model_validate(
        {**payload, "bundle_sha256": m8_bundle_sha256(payload)}, strict=True
    )


def _two_candidate_bundle_raw(*, include_second_translation: bool) -> dict[str, Any]:
    raw = _bundle().model_dump(mode="python")
    first_scalar = raw["candidate_scalar_facts"][0]
    second_scalar = deepcopy(first_scalar)
    second_scalar.update(
        {
            "candidate_id": "candidate-b",
            "layout_area_bits": _f(12.0),
            "layout_width_bits": _f(6.0),
        }
    )
    _rehash_fact(second_scalar)
    raw["candidate_scalar_facts"] = tuple(
        sorted((first_scalar, second_scalar), key=lambda item: item["fact_sha256"])
    )

    frontier = raw["frontier_facts"][0]
    scalar_refs = tuple(item["fact_sha256"] for item in raw["candidate_scalar_facts"])
    frontier.update(
        {
            "candidate_scalar_refs": scalar_refs,
            "retained_candidate_scalar_refs": scalar_refs,
        }
    )
    _rehash_fact(frontier)

    first_standard = raw["standard_candidate_facts"][0]
    second_standard = deepcopy(first_standard)
    second_standard.update(
        {
            "profile_position": 1,
            "candidate_id": "candidate-b",
            "catalog_action_id": "m7-standard:candidate-b",
            "materialized_action_id": None,
            "purchase_cost_bits": _f(13.0),
            "immediate_net_cost_bits": _f(13.0),
            "comparison_key": _rank_key(
                13.0,
                candidate_id="candidate-b",
                catalog_action_id="m7-standard:candidate-b",
            ),
            "decision_key": ("action_id=m7-standard:candidate-b",),
        }
    )
    _rehash_fact(second_standard)
    raw["standard_candidate_facts"] = (first_standard, second_standard)

    translation_refs = (raw["translation_batches"][0]["fact_sha256"],)
    if include_second_translation:
        second_translation = deepcopy(raw["translation_batches"][0])
        second_translation["candidate_id"] = "candidate-b"
        _rehash_fact(second_translation)
        raw["translation_batches"] = tuple(
            sorted(
                (*raw["translation_batches"], second_translation),
                key=lambda item: item["fact_sha256"],
            )
        )
        translation_refs = tuple(item["fact_sha256"] for item in raw["translation_batches"])

    common = raw["common_lemmas"][0]
    common.update(
        {
            "candidate_scalar_refs": scalar_refs,
            "frontier_refs": (frontier["fact_sha256"],),
            "standard_candidate_refs": (
                first_standard["fact_sha256"],
                second_standard["fact_sha256"],
            ),
            "translation_batch_refs": translation_refs,
        }
    )
    common["inventory_classifications"][0].update(
        {
            "frontier_ref": frontier["fact_sha256"],
            "candidate_scalar_refs": scalar_refs,
            "translation_batch_refs": translation_refs,
        }
    )
    common["legacy_common_fact_sha256"] = SHA_D
    common["portable_transition"]["event"].update(
        {
            "action_set_size": 2,
            "standard_action_count": 2,
            "fit_search_query_count": 2,
            "fit_search_generated_candidate_count": 4,
            "fit_search_evaluated_candidate_count": 4,
        }
    )
    _rehash_fact(common)

    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)
    return raw


def _two_event_bundle_raw() -> dict[str, Any]:
    raw = _bundle().model_dump(mode="python")

    translation = deepcopy(raw["translation_batches"][0])
    translation["event_position"] = 2
    _rehash_fact(translation)

    standard = deepcopy(raw["standard_candidate_facts"][0])
    standard.update(
        {
            "event_position": 2,
            "comparison_key": _rank_key(
                12.25,
                candidate_id="candidate-a",
                catalog_action_id="m7-standard:candidate-a",
            ),
        }
    )
    _rehash_fact(standard)

    first_common = raw["common_lemmas"][0]
    common = deepcopy(first_common)
    common.update(
        {
            "event_position": 2,
            "event_id": "yfm7e-" + "b" * 24,
            "legacy_common_fact_sha256": SHA_D,
            "cursor_before_sha256": first_common["cursor_after_sha256"],
            "cursor_after_sha256": SHA_D,
            "event_occurred_at": _dt(2),
            "storage_interval_start": _dt(1),
            "storage_interval_end": _dt(2),
            "cursor_current_time": _dt(2),
            "cursor_previous_release": _dt(2),
            "previous_common_lemma_ref": first_common["fact_sha256"],
            "baseline_fallback_cursor_sha256": None,
            "minimum_standard_candidate_ref": standard["fact_sha256"],
            "selected_comparison_key": _rank_key(
                12.25,
                candidate_id="candidate-a",
                catalog_action_id="m7-standard:candidate-a",
            ),
            "standard_candidate_refs": (standard["fact_sha256"],),
            "translation_batch_refs": (translation["fact_sha256"],),
            "inventory_classifications": (
                {
                    **first_common["inventory_classifications"][0],
                    "translation_batch_refs": (translation["fact_sha256"],),
                },
            ),
        }
    )
    transition = common["portable_transition"]
    transition.update(
        {
            "event_position": 2,
            "event_id": "yfm7e-" + "b" * 24,
            "cursor_before_sha256": first_common["cursor_after_sha256"],
            "cursor_after_sha256": SHA_D,
        }
    )
    transition["cursor_before"].update(
        {
            "next_event_position": 2,
            "current_time": _dt(1),
            "cumulative_costs": _ledger(12.25),
            "timestamp_group_sequence": 1,
            "previous_release": _dt(1),
        }
    )
    transition["event"].update(
        {
            "sequence": 2,
            "event_id": "yfm7e-" + "b" * 24,
            "binding_id": "yfm7b-" + "c" * 24,
            "occurred_at": _dt(2),
            "timestamp_group_sequence": 2,
            "storage_interval_start": _dt(1),
            "storage_interval_end": _dt(2),
            "cumulative_costs": _ledger(24.5),
        }
    )
    transition["cursor_after"].update(
        {
            "next_event_position": 3,
            "current_time": _dt(2),
            "cumulative_costs": _ledger(24.5),
            "timestamp_group_sequence": 2,
            "previous_release": _dt(2),
        }
    )
    _rehash_fact(common)

    influence = deepcopy(raw["influence_facts"][0])
    influence.update(
        {
            "event_position": 2,
            "common_lemma_ref": common["fact_sha256"],
            "state_before_sha256": SHA_C,
            "state_after_sha256": SHA_D,
            "rejection_evidence": (
                {
                    **raw["influence_facts"][0]["rejection_evidence"][0],
                },
            ),
        }
    )
    _rehash_fact(influence)

    root = raw["action_roots"][0]
    root.update(
        {
            "stop_event_position": 3,
            "final_state_sha256": SHA_D,
            "common_lemma_refs": (
                first_common["fact_sha256"],
                common["fact_sha256"],
            ),
            "influence_fact_refs": (
                raw["influence_facts"][0]["fact_sha256"],
                influence["fact_sha256"],
            ),
        }
    )
    _rehash_fact(root)

    raw["translation_batches"] = tuple(
        sorted((*raw["translation_batches"], translation), key=lambda item: item["fact_sha256"])
    )
    raw["standard_candidate_facts"] = (*raw["standard_candidate_facts"], standard)
    raw["common_lemmas"] = (first_common, common)
    raw["influence_facts"] = (*raw["influence_facts"], influence)
    _rehash_bundle(raw)
    return raw


def _rehash_fact(raw: dict[str, Any]) -> None:
    raw["fact_sha256"] = m8_fact_sha256(
        raw["fact_kind"],
        {key: value for key, value in raw.items() if key != "fact_sha256"},
    )


def _rehash_bundle(raw: dict[str, Any]) -> None:
    payload = {key: value for key, value in raw.items() if key != "bundle_sha256"}
    raw["bundle_sha256"] = m8_bundle_sha256(payload)


def _first_validation_error(raw: dict[str, Any]) -> dict[str, Any]:
    with pytest.raises(ValidationError) as raised:
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    errors = raised.value.errors(include_url=False)
    assert len(errors) == 1
    return errors[0]


def _shallow_bundle_hash_input(raw: dict[str, Any]) -> dict[str, Any]:
    shallow = {
        "schema_version": raw["schema_version"],
        "bundle_kind": raw["bundle_kind"],
        "provenance": raw["provenance"],
    }
    for field in (
        "translation_batches",
        "candidate_scalar_facts",
        "frontier_facts",
        "standard_candidate_facts",
        "common_lemmas",
        "influence_facts",
        "action_roots",
    ):
        shallow[field] = tuple({"fact_sha256": entry["fact_sha256"]} for entry in raw[field])
    return shallow


def test_canonical_f64_round_trips_exact_bits_and_normalizes_negative_zero() -> None:
    values = (0.0, -0.0, 1.0, -1.25, 1e-300, 1.7976931348623157e308)
    for value in values:
        encoded = encode_canonical_f64(value)
        decoded = decode_canonical_f64(encoded)
        expected = 0.0 if value == 0.0 else value
        assert struct.pack(">d", decoded) == struct.pack(">d", expected)
    assert encode_canonical_f64(-0.0) == "f64:0000000000000000"
    with pytest.raises(ValueError, match="negative zero"):
        decode_canonical_f64("f64:8000000000000000")


def test_bundle_hash_shallow_input_is_equivalent_at_hundreds_of_roots() -> None:
    deep = _bundle().model_dump(mode="python", exclude={"bundle_sha256"})
    deep_hash = m8_bundle_sha256(deep)
    assert deep_hash == "sha256:37a51ef534a187d7fd0420ca94fdf0dee11112ca9ad2cd8c59efdce4daf2a2a4"
    assert deep_hash == m8_bundle_sha256(_shallow_bundle_hash_input(deep))

    template = deep["action_roots"][0]
    deep["action_roots"] = tuple(
        {
            **template,
            "fact_sha256": f"sha256:{hashlib.sha256(str(index).encode()).hexdigest()}",
        }
        for index in range(512)
    )
    shallow = _shallow_bundle_hash_input(deep)

    assert len(shallow["action_roots"]) == 512
    assert m8_bundle_sha256(deep) == m8_bundle_sha256(shallow)


@pytest.mark.parametrize(
    "encoded",
    [
        "f64:7ff0000000000000",
        "f64:fff0000000000000",
        "f64:7ff8000000000000",
        "F64:3ff0000000000000",
        "f64:3FF0000000000000",
        "3ff0000000000000",
    ],
)
def test_canonical_f64_rejects_nonfinite_and_noncanonical_strings(encoded: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        decode_canonical_f64(encoded)


def test_canonical_f64_encoder_is_strict() -> None:
    for value in (True, 1, "1.0"):
        with pytest.raises(TypeError):
            encode_canonical_f64(value)  # type: ignore[arg-type]


def test_canonical_utc_round_trips_all_outcome_defining_categories() -> None:
    value = datetime(2026, 8, 1, 12, 34, 56, 123456, tzinfo=UTC)
    encoded = "2026-08-01T12:34:56.123456Z"
    assert encode_canonical_utc(value) == encoded
    assert decode_canonical_utc(encoded) == value
    common = _bundle().common_lemmas[0]
    for category in (
        common.event_occurred_at,
        common.storage_interval_start,
        common.storage_interval_end,
        common.cursor_current_time,
        common.cursor_previous_release,
    ):
        assert category is None or encode_canonical_utc(decode_canonical_utc(category)) == category


def test_canonical_utc_encoder_rejects_naive_input() -> None:
    value = datetime(2026, 8, 1)
    with pytest.raises(ValueError):
        encode_canonical_utc(value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-01T12:34:56Z",
        "2026-08-01T12:34:56.123456+00:00",
        "2026-08-01T12:34:56.123456z",
        "2026-08-01 12:34:56.123456Z",
    ],
)
def test_canonical_utc_decoder_rejects_noncanonical_strings(value: str) -> None:
    with pytest.raises(ValueError):
        decode_canonical_utc(value)


def test_fact_hash_is_domain_separated_and_canonical_json_is_stable() -> None:
    payload = {"z": ("one", "two"), "a": {"value": 3}}
    first = canonical_semantic_json(payload)
    second = canonical_semantic_json({"a": {"value": 3}, "z": ("one", "two")})
    assert first == second
    assert m8_fact_sha256("candidate_scalar", payload) != m8_fact_sha256("frontier", payload)
    assert (
        m8_fact_sha256("candidate_scalar", {"schema_version": "x", "value": "same"})
        == "sha256:eff6c4f3fabfdcfab12c0aee71cdcea0f4fed02e877d45a35fc9480e47565707"
    )


def test_all_eight_named_contracts_strict_load_minimal_fixture() -> None:
    bundle = _bundle()
    expected_types = (
        M8PortableTranslationBatch,
        M8CandidateScalarFactV2,
        M8FrontierFactV2,
        M8StandardCandidateFactV2,
        M8CommonTransitionLemmaV2,
        M8InfluenceFactV2,
        M8ActionRootV2,
        M8UncheckedFactBundleV2,
    )
    observed = (
        bundle.translation_batches[0],
        bundle.candidate_scalar_facts[0],
        bundle.frontier_facts[0],
        bundle.standard_candidate_facts[0],
        bundle.common_lemmas[0],
        bundle.influence_facts[0],
        bundle.action_roots[0],
        bundle,
    )
    assert tuple(type(item) for item in observed) == expected_types


def test_bundle_root_failure_has_stable_structured_error_and_bundle_identity() -> None:
    raw = _bundle().model_dump(mode="python")
    raw["bundle_sha256"] = SHA_D

    error = _first_validation_error(raw)

    assert error["type"] == "m8_bundle_hash_mismatch"
    assert error["ctx"] == {"bundle_sha256": SHA_D}


def test_dangling_cross_fact_failure_has_owner_and_dependency_identities() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = SHA_D
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    error = _first_validation_error(raw)

    assert error["type"] == "m8_dangling_reference"
    assert error["ctx"] == {
        "fact_sha256": influence["fact_sha256"],
        "dependency_sha256": SHA_D,
    }


def test_cross_context_failure_identifies_first_offending_fact() -> None:
    raw = _bundle().model_dump(mode="python")
    scalar = raw["candidate_scalar_facts"][0]
    scalar["semantic_runtime_sha256"] = SHA_C
    _rehash_fact(scalar)
    _rehash_bundle(raw)

    error = _first_validation_error(raw)

    assert error["type"] == "m8_context_mismatch"
    assert error["ctx"] == {"fact_sha256": scalar["fact_sha256"]}


def test_unused_fact_failure_identifies_first_unreachable_fact() -> None:
    raw = _bundle().model_dump(mode="python")
    unused = deepcopy(raw["candidate_scalar_facts"][0])
    unused["candidate_id"] = "candidate-unused"
    _rehash_fact(unused)
    raw["candidate_scalar_facts"] = tuple(
        sorted((*raw["candidate_scalar_facts"], unused), key=lambda item: item["fact_sha256"])
    )
    _rehash_bundle(raw)

    error = _first_validation_error(raw)

    assert error["type"] == "m8_unused_fact"
    assert error["ctx"] == {"fact_sha256": unused["fact_sha256"]}


def test_portable_leaves_bind_full_profile_and_search_context() -> None:
    bundle = _bundle()
    translation = bundle.translation_batches[0]
    standard = bundle.standard_candidate_facts[0]
    common = bundle.common_lemmas[0]

    assert translation.fit_config_sha256 == common.fit_config_sha256
    assert translation.search_config_sha256 == common.search_config_sha256
    assert decode_canonical_f64(standard.parent_remnant_area_bits) == 10.0
    assert decode_canonical_f64(standard.placed_area_bits) == 10.0
    assert standard.selected_stock_id == "current_standard_sheet"
    assert bundle.candidate_scalar_facts[0].source_transform_sha256 == FREEZE
    assert common.problem_sha256 == SHA_D
    assert common.candidate_set_id == "yfm7c-" + "e" * 24
    assert common.jagua_executable_sha256 == FREEZE
    assert common.cursor_before_inventory_remnant_ids == ("yfrm-" + "6" * 24,)
    assert bundle.provenance.stream_sha256 == SHA_A


def test_common_lemma_carries_complete_typed_legacy_transition_preimage() -> None:
    translation = _translation()
    scalar = _scalar()
    frontier = _frontier(scalar)
    standard = _standard()
    raw = _common(translation, scalar, frontier, standard).model_dump(mode="python")
    raw.update(
        {
            "event_id": "yfm7e-" + "a" * 24,
            "legacy_common_fact_sha256": SHA_A,
            "portable_transition": _portable_common_transition(),
        }
    )
    _rehash_fact(raw)

    loaded = M8CommonTransitionLemmaV2.model_validate(raw, strict=True)
    assert loaded.legacy_common_fact_sha256 == SHA_A
    assert loaded.portable_transition.event.action.accounting.parent_remnant_area_bits == _f(10.0)
    assert loaded.portable_transition.cursor_before.inventory[0].remnant.geometry.wkb_hex
    assert loaded.portable_transition.cursor_after.cumulative_costs.net_cost_bits == _f(12.25)


def _portable_source_mirrors() -> tuple[Any, ...]:
    bundle = M8UncheckedFactBundleV2.model_validate(_exact_remnant_bundle_raw(), strict=True)
    transition = bundle.common_lemmas[0].portable_transition
    action = transition.event.action
    stock = action.selected_stock
    search_result = action.search_result
    assert search_result is not None
    return (
        stock.geometry,
        stock.material,
        stock.lineage,
        stock,
        search_result.config,
        search_result,
        action,
        transition,
    )


def test_portable_source_mirrors_have_unique_v2_and_explicit_source_schema_ids() -> None:
    expected = (
        ("yieldforge.m8-portable-polygon.v2", "yieldforge.canonical-polygon.v1"),
        ("yieldforge.m8-portable-material-identity.v2", "yieldforge.material-identity.v1"),
        ("yieldforge.m8-portable-remnant-lineage.v2", "yieldforge.remnant-lineage.v1"),
        ("yieldforge.m8-portable-remnant-stock.v2", "yieldforge.remnant-stock.v1"),
        (
            "yieldforge.m8-portable-layout-search-config.v2",
            "yieldforge.m7-layout-fit-search-config.v1",
        ),
        (
            "yieldforge.m8-portable-layout-search-result.v2",
            "yieldforge.m7-layout-fit-search-result.v1",
        ),
        ("yieldforge.m8-portable-layout-action.v2", "yieldforge.m7-layout-action.v1"),
        (
            "yieldforge.m8-portable-common-transition.v2",
            "yieldforge.m8-common-transition-fact.v1",
        ),
    )
    observed = tuple(
        (
            item.model_dump(mode="python").get("schema_version"),
            item.model_dump(mode="python").get("source_schema_version"),
        )
        for item in _portable_source_mirrors()
    )

    assert observed == expected
    assert len({schema for schema, _ in observed}) == len(observed)
    assert {schema for schema, _ in observed}.isdisjoint({source for _, source in observed})


def test_portable_source_mirrors_reject_source_v1_as_dispatch_schema() -> None:
    for item in _portable_source_mirrors():
        raw = item.model_dump(mode="python")
        source_schema = raw.get("source_schema_version", raw["schema_version"])
        raw["schema_version"] = source_schema

        with pytest.raises(ValidationError):
            type(item).model_validate(raw, strict=True)


def test_root_preserves_preinitial_and_postinitial_state_boundaries() -> None:
    raw = _bundle().model_dump(mode="python")
    root = raw["action_roots"][0]
    root["start_state_sha256"] = SHA_A
    root["initial_state_after_sha256"] = SHA_B
    _rehash_fact(root)
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.action_roots[0].start_state_sha256 == SHA_A
    assert loaded.action_roots[0].initial_state_after_sha256 == SHA_B


def test_selected_standard_candidate_must_be_policy_minimum_after_rehash() -> None:
    raw = _bundle().model_dump(mode="python")
    first = raw["standard_candidate_facts"][0]
    second = deepcopy(first)
    second.update(
        {
            "profile_position": 1,
            "candidate_id": "candidate-cheaper",
            "catalog_action_id": "m7-standard:candidate-cheaper",
            "materialized_action_id": None,
            "purchase_cost_bits": _f(1.0),
            "immediate_net_cost_bits": _f(1.0),
            "comparison_key": _rank_key(
                1.0,
                candidate_id="candidate-cheaper",
                catalog_action_id="m7-standard:candidate-cheaper",
            ),
            "decision_key": ("action_id=m7-standard:candidate-cheaper",),
        }
    )
    _rehash_fact(second)
    raw["standard_candidate_facts"] = (first, second)
    common = raw["common_lemmas"][0]
    common["standard_candidate_refs"] = (first["fact_sha256"], second["fact_sha256"])
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "translation_batch_refs": (),
            "inventory_classifications": (
                {
                    **common["inventory_classifications"][0],
                    "classification": "exact_survivor",
                    "frontier_ref": None,
                    "candidate_scalar_refs": (),
                    "translation_batch_refs": (),
                    "exact_replay_reason": "unsupported_representation",
                },
            ),
            "evidence_mode": "exact_replay",
            "exact_replay_reason": "exact_survivor_unsupported_representation",
        }
    )
    common["portable_transition"]["event"].update(
        {"action_set_size": 2, "standard_action_count": 2}
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "classification": "exact_transition",
            "evidence_mode": "exact_transition",
            "rejection_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["translation_batches"] = ()
    raw["candidate_scalar_facts"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="policy minimum"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_state_rejoin_requires_empty_inventory_delta() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "classification": "state_rejoin",
            "evidence_mode": "state_rejoin",
            "rejection_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="empty inventory delta"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_influence_evidence_direction_covers_exact_inventory_delta() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    influence["rejection_evidence"][0]["direction"] = "removed"
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.influence_facts[0].rejection_evidence[0].direction == "removed"


def test_influence_rejects_wrong_direction_and_uncovered_delta_item() -> None:
    for mutation in ("wrong_direction", "uncovered"):
        raw = _bundle().model_dump(mode="python")
        influence = raw["influence_facts"][0]
        influence["rejection_evidence"][0]["direction"] = "removed"
        if mutation == "wrong_direction":
            influence["rejection_evidence"][0]["direction"] = "added"
        else:
            influence["inventory_delta"]["added_remnant_ids"] = ("yfrm-" + "d" * 24,)
        _rehash_fact(influence)
        root = raw["action_roots"][0]
        root["influence_fact_refs"] = (influence["fact_sha256"],)
        _rehash_fact(root)
        _rehash_bundle(raw)
        with pytest.raises(ValidationError, match="inventory delta coverage"):
            M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_exact_search_no_fit_still_requires_common_branch_action() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    rejection = influence["rejection_evidence"][0]
    rejection.update({"direction": "removed", "impossible": False, "reason": None})
    influence.update(
        {
            "evidence_mode": "exact_transition",
            "branch_catalog_action_id": "foreign-catalog-action",
            "branch_materialized_action_id": "yfm7a-" + "e" * 24,
            "search_evidence": (
                {
                    "direction": "removed",
                    "remnant_id": "yfrm-" + "6" * 24,
                    "candidate_id": "candidate-a",
                    "search_config": _search_config(),
                    "search_config_sha256": _search_config_sha256(),
                    "translation_batch_ref": raw["translation_batches"][0]["fact_sha256"],
                    "generated_candidate_count": 2,
                    "duplicate_candidate_count": 1,
                    "evaluated_candidate_count": 2,
                    "budget_truncated": False,
                    "result": "no_witness_within_registered_search",
                    "selected_translation": None,
                },
            ),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="unchecked branch action"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def _exact_search_no_fit_bundle_raw(*, include_rejections: bool = True) -> dict[str, Any]:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    rejection = influence["rejection_evidence"][0]
    rejection.update({"impossible": False, "reason": None})
    influence.update(
        {
            "classification": "no_fit",
            "evidence_mode": "exact_transition",
            "rejection_evidence": (rejection,) if include_rejections else (),
            "search_evidence": (
                {
                    "direction": "removed",
                    "remnant_id": "yfrm-" + "6" * 24,
                    "candidate_id": "candidate-a",
                    "search_config": _search_config(),
                    "search_config_sha256": _search_config_sha256(),
                    "translation_batch_ref": raw["translation_batches"][0]["fact_sha256"],
                    "generated_candidate_count": 2,
                    "duplicate_candidate_count": 1,
                    "evaluated_candidate_count": 2,
                    "budget_truncated": False,
                    "result": "no_witness_within_registered_search",
                    "selected_translation": None,
                },
            ),
            "competitor_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)
    return raw


def test_exact_search_no_fit_requires_complete_rejection_preimage() -> None:
    raw = _exact_search_no_fit_bundle_raw(include_rejections=False)

    with pytest.raises(ValidationError, match="complete rejection candidate set"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_exact_search_no_fit_rejects_positive_fit_evidence() -> None:
    raw = _exact_search_no_fit_bundle_raw()
    search = raw["influence_facts"][0]["search_evidence"][0]
    search.update(
        {
            "evaluated_candidate_count": 1,
            "result": "fit",
            "selected_translation": deepcopy(raw["translation_batches"][0]["translations"][0]),
        }
    )
    _rehash_fact(raw["influence_facts"][0])
    raw["action_roots"][0]["influence_fact_refs"] = (raw["influence_facts"][0]["fact_sha256"],)
    _rehash_fact(raw["action_roots"][0])
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="no-fit.*positive fit"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_embedded_search_config_must_match_its_semantic_sha256() -> None:
    raw = _exact_search_no_fit_bundle_raw()
    influence = raw["influence_facts"][0]
    influence["search_evidence"][0]["search_config"]["grid_columns"] = 6
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="search configuration SHA-256"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


@pytest.mark.parametrize("binding", ["fit", "search"])
def test_influence_translation_must_share_common_registered_configuration(
    binding: str,
) -> None:
    raw = _exact_search_no_fit_bundle_raw()
    common = raw["common_lemmas"][0]
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "translation_batch_refs": (),
            "inventory_classifications": (
                {
                    **common["inventory_classifications"][0],
                    "classification": "exact_survivor",
                    "frontier_ref": None,
                    "candidate_scalar_refs": (),
                    "translation_batch_refs": (),
                    "exact_replay_reason": "unsupported_representation",
                },
            ),
            "evidence_mode": "exact_replay",
            "exact_replay_reason": "exact_survivor_unsupported_representation",
        }
    )
    if binding == "search":
        common["search_config_sha256"] = SHA_D
    _rehash_fact(common)
    translation = raw["translation_batches"][0]
    if binding == "fit":
        translation["fit_config_sha256"] = SHA_A
    _rehash_fact(translation)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "branch_catalog_action_id": "branch-exact-catalog",
            "branch_materialized_action_id": "yfm7a-" + "e" * 24,
            "classification": "exact_transition",
            "rejection_evidence": (),
        }
    )
    influence["search_evidence"][0]["translation_batch_ref"] = translation["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["candidate_scalar_facts"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="translation configuration bindings"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def _policy_dominated_bundle_raw() -> dict[str, Any]:
    raw = _bundle().model_dump(mode="python")
    remnant_id = "yfrm-" + "6" * 24
    catalog_action_id = "m7-remnant:candidate-a:remnant-a"
    influence = raw["influence_facts"][0]
    rejection = influence["rejection_evidence"][0]
    rejection.update({"impossible": False, "reason": None})
    influence.update(
        {
            "classification": "policy_dominated",
            "evidence_mode": "policy_dominated_exact_check",
            "rejection_evidence": (rejection,),
            "search_evidence": (
                {
                    "direction": "removed",
                    "remnant_id": remnant_id,
                    "candidate_id": "candidate-a",
                    "search_config": _search_config(),
                    "search_config_sha256": _search_config_sha256(),
                    "translation_batch_ref": raw["translation_batches"][0]["fact_sha256"],
                    "generated_candidate_count": 2,
                    "duplicate_candidate_count": 1,
                    "evaluated_candidate_count": 1,
                    "budget_truncated": False,
                    "result": "fit",
                    "selected_translation": deepcopy(
                        raw["translation_batches"][0]["translations"][0]
                    ),
                },
            ),
            "competitor_evidence": (
                {
                    "direction": "removed",
                    "candidate_id": "candidate-a",
                    "catalog_action_id": catalog_action_id,
                    "materialized_action_id": "yfm7a-" + "e" * 24,
                    "materialized_content_sha256": SHA_A,
                    "selected_remnant_id": remnant_id,
                    "action_kind": "consume_remnant",
                    "selected_stock_id": remnant_id,
                    "candidate_width_bits": _f(5.5),
                    "immediate_net_cost_bits": _f(20.0),
                    "selected_remnant_age_hours_bits": _f(1.0),
                    "returned_regularity_bits": _f(0.0),
                    "known_order_lookahead_term_bits": _f(0.0),
                    "policy_name": "net_cost",
                    "comparison_key": _rank_key(
                        20.0,
                        candidate_id="candidate-a",
                        catalog_action_id=catalog_action_id,
                        selected_stock_id=remnant_id,
                    ),
                    "decision_key": (f"action_id={catalog_action_id}",),
                },
            ),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)
    return raw


def test_policy_dominated_competitor_must_bind_a_fit_search() -> None:
    raw = _policy_dominated_bundle_raw()
    search = raw["influence_facts"][0]["search_evidence"][0]
    search.update(
        {
            "evaluated_candidate_count": 2,
            "result": "no_witness_within_registered_search",
            "selected_translation": None,
        }
    )
    _rehash_fact(raw["influence_facts"][0])
    raw["action_roots"][0]["influence_fact_refs"] = (raw["influence_facts"][0]["fact_sha256"],)
    _rehash_fact(raw["action_roots"][0])
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="competitor.*fit search"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_policy_dominated_competitor_must_use_common_policy_and_rank_shape() -> None:
    for mutation in ("policy", "rank_shape"):
        raw = _policy_dominated_bundle_raw()
        competitor = raw["influence_facts"][0]["competitor_evidence"][0]
        if mutation == "policy":
            competitor["policy_name"] = "myopic_geometry"
        else:
            competitor["comparison_key"] = (
                {
                    "component_kind": "int",
                    "f64_bits": None,
                    "int_value": 1,
                    "string_value": None,
                },
            )
        _rehash_fact(raw["influence_facts"][0])
        raw["action_roots"][0]["influence_fact_refs"] = (raw["influence_facts"][0]["fact_sha256"],)
        _rehash_fact(raw["action_roots"][0])
        _rehash_bundle(raw)
        with pytest.raises(ValidationError, match="policy|rank.*shape"):
            M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_policy_dominated_fit_remnant_has_exactly_one_selected_competitor() -> None:
    raw = _policy_dominated_bundle_raw()
    influence = raw["influence_facts"][0]
    first = influence["competitor_evidence"][0]
    second = deepcopy(first)
    catalog_action_id = "z-remnant:candidate-a:remnant-a"
    second.update(
        {
            "catalog_action_id": catalog_action_id,
            "materialized_action_id": "yfm7a-" + "f" * 24,
            "materialized_content_sha256": SHA_B,
            "comparison_key": _rank_key(
                21.0,
                candidate_id="candidate-a",
                catalog_action_id=catalog_action_id,
                selected_stock_id="yfrm-" + "6" * 24,
            ),
            "decision_key": (f"action_id={catalog_action_id}",),
        }
    )
    influence["competitor_evidence"] = (first, second)
    _rehash_fact(influence)
    raw["action_roots"][0]["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(raw["action_roots"][0])
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="exactly one selected competitor"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_exact_transition_mode_is_explicit_without_synthetic_search_preimage() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "classification": "exact_transition",
            "evidence_mode": "exact_transition",
            "branch_catalog_action_id": "branch-exact-catalog",
            "branch_materialized_action_id": "yfm7a-" + "e" * 24,
            "rejection_evidence": (),
            "search_evidence": (),
            "competitor_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.influence_facts[0].evidence_mode == "exact_transition"


def test_common_translation_candidate_must_belong_to_frontier_scalar_set() -> None:
    raw = _bundle().model_dump(mode="python")
    translation = raw["translation_batches"][0]
    translation["candidate_id"] = "foreign-candidate"
    _rehash_fact(translation)
    common = raw["common_lemmas"][0]
    common["translation_batch_refs"] = (translation["fact_sha256"],)
    common["inventory_classifications"][0]["translation_batch_refs"] = (translation["fact_sha256"],)
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="translation candidate"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_counted_common_requires_complete_candidate_translation_set() -> None:
    raw = _two_candidate_bundle_raw(include_second_translation=False)
    common = raw["common_lemmas"][0]

    error = _first_validation_error(raw)

    assert error["type"] == "m8_incomplete_evidence"
    assert error["ctx"] == {
        "fact_sha256": common["fact_sha256"],
        "dependency_sha256": common["inventory_classifications"][0]["frontier_ref"],
    }


def test_scalar_no_fit_requires_complete_rejection_candidate_set() -> None:
    raw = _two_candidate_bundle_raw(include_second_translation=True)
    influence = raw["influence_facts"][0]
    common = raw["common_lemmas"][0]

    error = _first_validation_error(raw)

    assert error["type"] == "m8_incomplete_evidence"
    assert error["ctx"] == {
        "fact_sha256": influence["fact_sha256"],
        "dependency_sha256": common["fact_sha256"],
    }


@pytest.mark.parametrize(
    "mutation",
    ["budget_flag", "no_fit_not_exhausted", "fit_translation_mismatch"],
)
def test_influence_search_must_match_complete_translation_batch(mutation: str) -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    rejection = influence["rejection_evidence"][0]
    rejection.update({"impossible": False, "reason": None})
    search = {
        "direction": "removed",
        "remnant_id": "yfrm-" + "6" * 24,
        "candidate_id": "candidate-a",
        "search_config": _search_config(),
        "search_config_sha256": _search_config_sha256(),
        "translation_batch_ref": raw["translation_batches"][0]["fact_sha256"],
        "generated_candidate_count": 2,
        "duplicate_candidate_count": 1,
        "evaluated_candidate_count": 2,
        "budget_truncated": False,
        "result": "no_witness_within_registered_search",
        "selected_translation": None,
    }
    if mutation == "budget_flag":
        search["budget_truncated"] = True
    elif mutation == "no_fit_not_exhausted":
        search["evaluated_candidate_count"] = 1
    else:
        influence["classification"] = "exact_transition"
        search.update(
            {
                "evaluated_candidate_count": 1,
                "result": "fit",
                "selected_translation": {"x_bits": _f(99.0), "y_bits": _f(99.0)},
            }
        )
    influence.update(
        {
            "evidence_mode": "exact_transition",
            "search_evidence": (search,),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(
        ValidationError,
        match="translation (configuration bindings|identity|sequence)",
    ):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


@pytest.mark.parametrize(
    ("generated", "truncated"),
    [(3, False), (4, True)],
)
def test_translation_batch_count_shape_is_exact(generated: int, truncated: bool) -> None:
    raw = _translation().model_dump(mode="python")
    raw["generated_candidate_count"] = generated
    raw["budget_truncated"] = truncated
    _rehash_fact(raw)
    with pytest.raises(ValidationError, match="count shape"):
        M8PortableTranslationBatch.model_validate(raw, strict=True)


def test_frontier_rejects_retained_and_dominated_overlap() -> None:
    first = _scalar()
    second_raw = first.model_dump(mode="python")
    second_raw["candidate_id"] = "candidate-b"
    _rehash_fact(second_raw)
    second = M8CandidateScalarFactV2.model_validate(second_raw, strict=True)
    references = tuple(sorted((first.fact_sha256, second.fact_sha256)))
    retained = references[0]
    payload = {
        **_frontier(first).model_dump(mode="python"),
        "candidate_scalar_refs": references,
        "retained_candidate_scalar_refs": references,
        "dominance_evidence": (
            {
                "dominated_candidate_scalar_ref": retained,
                "retained_candidate_scalar_ref": references[1],
                "relation": "componentwise_necessary_fit",
            },
        ),
    }
    _rehash_fact(payload)

    with pytest.raises(ValidationError, match="disjoint"):
        M8FrontierFactV2.model_validate(payload, strict=True)


def test_exact_search_no_fit_retains_nonrejection_and_search_preimages() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    rejection = influence["rejection_evidence"][0]
    rejection["impossible"] = False
    rejection["reason"] = None
    influence.update(
        {
            "evidence_mode": "exact_transition",
            "search_evidence": (
                {
                    "direction": "removed",
                    "remnant_id": "yfrm-" + "6" * 24,
                    "candidate_id": "candidate-a",
                    "search_config": _search_config(),
                    "search_config_sha256": _search_config_sha256(),
                    "translation_batch_ref": raw["translation_batches"][0]["fact_sha256"],
                    "generated_candidate_count": 2,
                    "duplicate_candidate_count": 1,
                    "evaluated_candidate_count": 2,
                    "budget_truncated": False,
                    "result": "no_witness_within_registered_search",
                    "selected_translation": None,
                },
            ),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.influence_facts[0].classification == "no_fit"
    assert loaded.influence_facts[0].rejection_evidence[0].reason is None


def test_common_exact_survivor_is_explicit_even_with_counted_evidence() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    classification = common["inventory_classifications"][0]
    classification["classification"] = "exact_survivor"
    classification["exact_replay_reason"] = "counted_search_survivor"
    common["evidence_mode"] = "exact_replay"
    common["exact_replay_reason"] = "exact_survivor_counted_search"
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.common_lemmas[0].inventory_classifications[0].classification == "exact_survivor"


def _frontier_exact_replay_raw(
    *,
    item_reason: str,
    common_reason: str,
) -> dict[str, Any]:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    classification = common["inventory_classifications"][0]
    classification.update(
        {
            "classification": "exact_survivor",
            "translation_batch_refs": (),
            "exact_replay_reason": item_reason,
        }
    )
    common.update(
        {
            "evidence_mode": "exact_replay",
            "translation_batch_refs": (),
            "exact_replay_reason": common_reason,
        }
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["translation_batches"] = ()
    _rehash_bundle(raw)
    return raw


@pytest.mark.parametrize(
    ("item_reason", "common_reason"),
    [
        ("frontier_survvor", "exact_survivor_frontier"),
        ("frontier_survivor", "exact_survivor_frontiers"),
    ],
)
def test_exact_replay_reasons_reject_unregistered_codes(
    item_reason: str,
    common_reason: str,
) -> None:
    raw = _frontier_exact_replay_raw(
        item_reason=item_reason,
        common_reason=common_reason,
    )

    with pytest.raises(ValidationError, match="Input should be"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_exact_replay_summary_must_match_item_reason_aggregation() -> None:
    raw = _frontier_exact_replay_raw(
        item_reason="frontier_survivor",
        common_reason="exact_survivor_unsupported_representation",
    )

    with pytest.raises(ValidationError, match="exact-replay reason summary"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_exact_survivor_reason_must_match_its_evidence_path() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    classification = common["inventory_classifications"][0]
    classification.update(
        {
            "classification": "exact_survivor",
            "exact_replay_reason": "frontier_survivor",
        }
    )
    common.update(
        {
            "evidence_mode": "exact_replay",
            "exact_replay_reason": "exact_survivor_frontier",
        }
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="reason differs from its evidence path"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_exact_replay_uses_mixed_summary_for_multiple_item_reason_codes() -> None:
    raw = _frontier_exact_replay_raw(
        item_reason="frontier_survivor",
        common_reason="exact_survivor_frontier",
    )
    common = raw["common_lemmas"][0]
    second_remnant_id = "yfrm-" + "7" * 24
    inventory = (
        *common["portable_transition"]["cursor_before"]["inventory"],
        _inventory_item(second_remnant_id, seed="7"),
    )
    transition = common["portable_transition"]
    transition["cursor_before"]["inventory"] = inventory
    transition["cursor_after"]["inventory"] = inventory
    transition["event"]["inventory_before"] = inventory
    transition["event"]["inventory_after"] = inventory
    common["cursor_before_inventory_remnant_ids"] = (
        "yfrm-" + "6" * 24,
        second_remnant_id,
    )
    common["cursor_after_inventory_remnant_ids"] = common["cursor_before_inventory_remnant_ids"]
    second_classification = deepcopy(common["inventory_classifications"][0])
    second_classification.update(
        {
            "remnant_id": second_remnant_id,
            "frontier_ref": None,
            "candidate_scalar_refs": (),
            "exact_replay_reason": "unsupported_representation",
        }
    )
    common["inventory_classifications"] = (
        *common["inventory_classifications"],
        second_classification,
    )
    common["exact_replay_reason"] = "exact_survivor_mixed"
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.common_lemmas[0].exact_replay_reason == "exact_survivor_mixed"


def test_common_exact_replay_requires_one_or_more_exact_survivors() -> None:
    raw = _frontier_exact_replay_raw(
        item_reason="frontier_survivor",
        common_reason="exact_survivor_frontier",
    )
    common = raw["common_lemmas"][0]
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "inventory_classifications": (),
            "cursor_before_inventory_remnant_ids": (),
            "cursor_after_inventory_remnant_ids": (),
        }
    )
    transition = common["portable_transition"]
    transition["cursor_before"]["inventory"] = ()
    transition["cursor_after"]["inventory"] = ()
    transition["event"]["inventory_before"] = ()
    transition["event"]["inventory_after"] = ()
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "inventory_delta": {"removed_remnant_ids": (), "added_remnant_ids": ()},
            "classification": "state_rejoin",
            "evidence_mode": "state_rejoin",
            "rejection_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["candidate_scalar_facts"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="one or more exact survivors"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_explicit_exact_replay_allows_unsupported_frontier_capture() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "translation_batch_refs": (),
            "evidence_mode": "exact_replay",
            "exact_replay_reason": "exact_survivor_unsupported_representation",
        }
    )
    common["inventory_classifications"] = (
        {
            **common["inventory_classifications"][0],
            "classification": "exact_survivor",
            "frontier_ref": None,
            "candidate_scalar_refs": (),
            "translation_batch_refs": (),
            "exact_replay_reason": "unsupported_representation",
        },
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "inventory_delta": {"removed_remnant_ids": (), "added_remnant_ids": ()},
            "classification": "state_rejoin",
            "evidence_mode": "state_rejoin",
            "rejection_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["translation_batches"] = ()
    raw["candidate_scalar_facts"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.common_lemmas[0].evidence_mode == "exact_replay"


def _exact_remnant_bundle_raw() -> dict[str, Any]:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    remnant_id = "yfrm-" + "6" * 24
    catalog_action_id = "m7-remnant:candidate-a:remnant-a"
    materialized_action_id = "yfm7a-" + "e" * 24
    context = {
        **_policy_context(),
        "action_id": catalog_action_id,
        "kind": "consume_remnant",
        "selected_stock_id": remnant_id,
        "immediate_net_cost_bits": _f(5.0),
        "selected_remnant_age_hours_bits": _f(1.0),
    }
    transition = common["portable_transition"]
    action = transition["event"]["action"]
    action.update(
        {
            "action_id": materialized_action_id,
            "content_sha256": SHA_A,
            "kind": "consume_remnant",
            "selected_stock": deepcopy(transition["event"]["inventory_before"][0]["remnant"]),
            "selected_remnant_id": remnant_id,
            "search_result": {
                "schema_version": "yieldforge.m8-portable-layout-search-result.v2",
                "source_schema_version": "yieldforge.m7-layout-fit-search-result.v1",
                "status": "fit",
                "candidate_id": "candidate-a",
                "remnant_id": remnant_id,
                "config": _search_config(),
                "generated_candidate_count": 1,
                "duplicate_candidate_count": 0,
                "evaluated_candidate_count": 1,
                "budget_truncated": False,
                "translation": deepcopy(action["translation"]),
            },
        }
    )
    transition["descriptor"].update(
        {
            "action_id": catalog_action_id,
            "kind": "consume_remnant",
            "selected_remnant_id": remnant_id,
            "evidence": deepcopy(action),
        }
    )
    transition["selected_context"] = context
    transition["action_binding"] = {
        "catalog_action_id": catalog_action_id,
        "materialized_action_id": materialized_action_id,
        "context": context,
    }
    rank = _rank_key(
        5.0,
        candidate_id="candidate-a",
        catalog_action_id=catalog_action_id,
        selected_stock_id=remnant_id,
    )
    transition["policy_rank"] = {
        "policy_name": "net_cost",
        "comparison_key": rank,
        "decision_key": (f"action_id={catalog_action_id}",),
    }
    transition["event"]["policy_decision_key"] = (f"action_id={catalog_action_id}",)
    common.update(
        {
            "selected_catalog_action_id": catalog_action_id,
            "selected_materialized_action_id": materialized_action_id,
            "selected_comparison_key": rank,
            "selected_decision_key": (f"action_id={catalog_action_id}",),
            "selected_immediate_net_cost_bits": _f(5.0),
            "evidence_mode": "exact_replay",
            "exact_replay_reason": "exact_survivor_counted_search",
            "inventory_classifications": (
                {
                    **common["inventory_classifications"][0],
                    "classification": "exact_survivor",
                    "exact_replay_reason": "counted_search_survivor",
                },
            ),
        }
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "common_catalog_action_id": catalog_action_id,
            "common_materialized_action_id": materialized_action_id,
            "branch_catalog_action_id": catalog_action_id,
            "branch_materialized_action_id": materialized_action_id,
            "classification": "exact_transition",
            "evidence_mode": "exact_transition",
            "rejection_evidence": (),
            "search_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)
    return raw


def test_exact_replay_can_select_remnant_while_retaining_standard_minimum() -> None:
    raw = _exact_remnant_bundle_raw()

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.common_lemmas[0].portable_transition.event.action.kind == ("consume_remnant")


def test_exact_remnant_winner_requires_matching_exact_survivor_classification() -> None:
    raw = _exact_remnant_bundle_raw()
    common = raw["common_lemmas"][0]
    classification = common["inventory_classifications"][0]
    classification["material_matches"] = False
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="selected remnant.*exact survivor"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_bundle_json_is_byte_identical_across_repeated_construction() -> None:
    first = _bundle().model_dump_json()
    second = _bundle().model_dump_json()
    assert first == second
    assert json.loads(first)["bundle_kind"] == "unchecked_fact_bundle"


def test_standard_candidates_and_references_use_profile_order_not_hash_order() -> None:
    raw = _bundle().model_dump(mode="python")
    first = raw["standard_candidate_facts"][0]
    second = deepcopy(first)
    for index in range(1, 100):
        candidate_id = f"candidate-{index}"
        catalog_action_id = f"catalog-standard-{index}"
        second.update(
            {
                "profile_position": 1,
                "candidate_id": candidate_id,
                "catalog_action_id": catalog_action_id,
                "materialized_action_id": None,
                "comparison_key": _rank_key(
                    13.0 + index,
                    candidate_id=candidate_id,
                    catalog_action_id=catalog_action_id,
                ),
                "decision_key": (f"action_id={catalog_action_id}",),
            }
        )
        _rehash_fact(second)
        if second["fact_sha256"] < first["fact_sha256"]:
            break
    else:  # pragma: no cover - cryptographic ordering makes this effectively impossible
        raise AssertionError("failed to construct reverse hash/profile ordering")

    raw["standard_candidate_facts"] = (first, second)
    common = raw["common_lemmas"][0]
    common["standard_candidate_refs"] = (
        first["fact_sha256"],
        second["fact_sha256"],
    )
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "translation_batch_refs": (),
            "inventory_classifications": (
                {
                    **common["inventory_classifications"][0],
                    "classification": "exact_survivor",
                    "frontier_ref": None,
                    "candidate_scalar_refs": (),
                    "translation_batch_refs": (),
                    "exact_replay_reason": "unsupported_representation",
                },
            ),
            "evidence_mode": "exact_replay",
            "exact_replay_reason": "exact_survivor_unsupported_representation",
        }
    )
    common["portable_transition"]["event"].update(
        {"action_set_size": 2, "standard_action_count": 2}
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "classification": "exact_transition",
            "evidence_mode": "exact_transition",
            "rejection_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["translation_batches"] = ()
    raw["candidate_scalar_facts"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert tuple(item.profile_position for item in loaded.standard_candidate_facts) == (0, 1)


def test_vacuous_frontier_mode_accepts_empty_remnant_evidence() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "inventory_classifications": (),
            "cursor_before_inventory_remnant_ids": (),
            "cursor_after_inventory_remnant_ids": (),
            "evidence_mode": "frontier_no_fit",
            "translation_batch_refs": (),
        }
    )
    transition = common["portable_transition"]
    transition["cursor_before"]["inventory"] = ()
    transition["cursor_after"]["inventory"] = ()
    transition["event"]["inventory_before"] = ()
    transition["event"]["inventory_after"] = ()
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "inventory_delta": {"removed_remnant_ids": (), "added_remnant_ids": ()},
            "classification": "state_rejoin",
            "evidence_mode": "state_rejoin",
            "rejection_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["translation_batches"] = ()
    raw["candidate_scalar_facts"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.common_lemmas[0].candidate_scalar_refs == ()


def test_terminal_action_root_accepts_empty_future_fact_layers() -> None:
    raw = _bundle().model_dump(mode="python")
    root = raw["action_roots"][0]
    root.update(
        {
            "stop_event_position": 1,
            "final_state_sha256": root["initial_state_after_sha256"],
            "common_lemma_refs": (),
            "influence_fact_refs": (),
        }
    )
    _rehash_fact(root)
    for field in (
        "translation_batches",
        "candidate_scalar_facts",
        "frontier_facts",
        "standard_candidate_facts",
        "common_lemmas",
        "influence_facts",
    ):
        raw[field] = ()
    _rehash_bundle(raw)

    loaded = M8UncheckedFactBundleV2.model_validate(raw, strict=True)
    assert loaded.action_roots[0].common_lemma_refs == ()


def test_action_roots_must_share_one_suffix_and_baseline_context() -> None:
    raw = _bundle().model_dump(mode="python")
    first = raw["action_roots"][0]
    first.update(
        {
            "stop_event_position": 1,
            "final_state_sha256": first["initial_state_after_sha256"],
            "common_lemma_refs": (),
            "influence_fact_refs": (),
        }
    )
    _rehash_fact(first)
    second = deepcopy(first)
    second.update(
        {
            "action_id": "yfm7a-" + "d" * 24,
            "catalog_action_id": "catalog-current-b",
            "baseline_action_id": "yfm7a-" + "e" * 24,
            "initial_state_after_sha256": SHA_D,
            "final_state_sha256": SHA_D,
        }
    )
    _rehash_fact(second)
    raw["action_roots"] = (first, second)
    for field in (
        "translation_batches",
        "candidate_scalar_facts",
        "frontier_facts",
        "standard_candidate_facts",
        "common_lemmas",
        "influence_facts",
    ):
        raw[field] = ()
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="root suffix/baseline context"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_first_common_lemma_fallback_must_equal_cursor_before() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    common["baseline_fallback_cursor_sha256"] = SHA_D
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="baseline fallback"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_chain_requires_cursor_continuity() -> None:
    raw = _two_event_bundle_raw()
    second = raw["common_lemmas"][1]
    second["cursor_before_sha256"] = SHA_A
    second["portable_transition"]["cursor_before_sha256"] = SHA_A
    _rehash_fact(second)
    influence = raw["influence_facts"][1]
    influence["common_lemma_ref"] = second["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (
        raw["common_lemmas"][0]["fact_sha256"],
        second["fact_sha256"],
    )
    root["influence_fact_refs"] = (
        raw["influence_facts"][0]["fact_sha256"],
        influence["fact_sha256"],
    )
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="cursor chain"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_chain_requires_full_portable_cursor_continuity() -> None:
    raw = _two_event_bundle_raw()
    second = raw["common_lemmas"][1]
    second["portable_transition"]["cursor_before"]["cumulative_costs"] = _ledger(999.0)
    _rehash_fact(second)
    influence = raw["influence_facts"][1]
    influence["common_lemma_ref"] = second["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (
        raw["common_lemmas"][0]["fact_sha256"],
        second["fact_sha256"],
    )
    root["influence_fact_refs"] = (
        raw["influence_facts"][0]["fact_sha256"],
        influence["fact_sha256"],
    )
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="full portable cursor chain"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_selected_cost_must_match_selected_standard_profile() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    common["selected_immediate_net_cost_bits"] = _f(99.0)
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="legacy common action|selected immediate cost"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_requires_every_standard_profile_declared_by_portable_event() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    common["portable_transition"]["event"].update(
        {"action_set_size": 2, "standard_action_count": 2}
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="standard action count"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


@pytest.mark.parametrize("duplicate_field", ["candidate_id", "catalog_action_id"])
def test_common_rejects_duplicate_standard_candidate_or_catalog_identity(
    duplicate_field: str,
) -> None:
    raw = _bundle().model_dump(mode="python")
    first = raw["standard_candidate_facts"][0]
    second = deepcopy(first)
    second.update(
        {
            "profile_position": 1,
            "candidate_id": "candidate-b",
            "catalog_action_id": "m7-standard:candidate-b",
            "materialized_action_id": None,
            "purchase_cost_bits": _f(13.0),
            "immediate_net_cost_bits": _f(13.0),
        }
    )
    if duplicate_field == "candidate_id":
        second["candidate_id"] = first["candidate_id"]
    else:
        second["catalog_action_id"] = first["catalog_action_id"]
    second["comparison_key"] = _rank_key(
        13.0,
        candidate_id=second["candidate_id"],
        catalog_action_id=second["catalog_action_id"],
    )
    second["decision_key"] = (f"action_id={second['catalog_action_id']}",)
    _rehash_fact(second)
    raw["standard_candidate_facts"] = (first, second)

    common = raw["common_lemmas"][0]
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "standard_candidate_refs": (first["fact_sha256"], second["fact_sha256"]),
            "inventory_classifications": (
                {
                    **common["inventory_classifications"][0],
                    "classification": "exact_survivor",
                    "frontier_ref": None,
                    "candidate_scalar_refs": (),
                    "translation_batch_refs": (),
                    "exact_replay_reason": "unsupported_representation",
                },
            ),
            "evidence_mode": "exact_replay",
            "translation_batch_refs": (),
            "exact_replay_reason": "exact_survivor_unsupported_representation",
        }
    )
    common["portable_transition"]["event"].update(
        {"action_set_size": 2, "standard_action_count": 2}
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence.update(
        {
            "common_lemma_ref": common["fact_sha256"],
            "classification": "exact_transition",
            "evidence_mode": "exact_transition",
            "rejection_evidence": (),
        }
    )
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["translation_batches"] = ()
    raw["candidate_scalar_facts"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="duplicate candidate or catalog identities"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_selected_standard_profile_binds_portable_policy_width() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    transition = common["portable_transition"]
    transition["selected_context"]["candidate_width_bits"] = _f(6.0)
    transition["action_binding"]["context"]["candidate_width_bits"] = _f(6.0)
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="standard profile.*policy context"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_selected_standard_profile_binds_portable_action_accounting() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    accounting = common["portable_transition"]["event"]["action"]["accounting"]
    accounting["parent_remnant_area_bits"] = _f(11.0)
    accounting["placed_area_bits"] = _f(11.0)
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="standard profile.*action accounting"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_common_selected_standard_profile_binds_shared_event_cost_components() -> None:
    raw = _bundle().model_dump(mode="python")
    standard = raw["standard_candidate_facts"][0]
    standard["purchase_cost_bits"] = _f(99.0)
    _rehash_fact(standard)
    common = raw["common_lemmas"][0]
    common["minimum_standard_candidate_ref"] = standard["fact_sha256"]
    common["standard_candidate_refs"] = (standard["fact_sha256"],)
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="shared portable event cost components"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_rehashed_portable_common_action_cannot_cross_problem_context() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    common["portable_transition"]["event"]["action"]["problem_id"] = "yfm7p-" + "f" * 24
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="portable legacy common problem"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_rehashed_standard_descriptor_cannot_smuggle_exact_action_evidence() -> None:
    raw = _bundle().model_dump(mode="python")
    common = raw["common_lemmas"][0]
    transition = common["portable_transition"]
    transition["descriptor"]["evidence"] = deepcopy(transition["event"]["action"])
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="descriptor evidence"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_influence_rejection_must_bind_referenced_scalar_identity() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    influence["rejection_evidence"][0]["candidate_id"] = "foreign-candidate"
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="scalar identity|complete rejection"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_influence_rejects_duplicate_rejection_preimage_with_different_reason() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    first = influence["rejection_evidence"][0]
    second = deepcopy(first)
    second["reason"] = "footprint_width_exceeds_remnant"
    influence["rejection_evidence"] = (first, second)
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="rejection evidence.*unique"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_influence_rejection_binds_copied_scalar_measurements() -> None:
    raw = _bundle().model_dump(mode="python")
    influence = raw["influence_facts"][0]
    influence["rejection_evidence"][0]["layout_width_bits"] = _f(6.0)
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="scalar measurements"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_influence_rejection_scalar_must_share_complete_common_partition() -> None:
    raw = _bundle().model_dump(mode="python")
    scalar = raw["candidate_scalar_facts"][0]
    scalar.update(
        {
            "problem_sha256": SHA_A,
            "candidate_set_sha256": SHA_B,
            "fit_config_sha256": SHA_C,
        }
    )
    _rehash_fact(scalar)
    common = raw["common_lemmas"][0]
    common.update(
        {
            "candidate_scalar_refs": (),
            "frontier_refs": (),
            "translation_batch_refs": (),
            "inventory_classifications": (
                {
                    **common["inventory_classifications"][0],
                    "classification": "exact_survivor",
                    "frontier_ref": None,
                    "candidate_scalar_refs": (),
                    "translation_batch_refs": (),
                    "exact_replay_reason": "unsupported_representation",
                },
            ),
            "evidence_mode": "exact_replay",
            "exact_replay_reason": "exact_survivor_unsupported_representation",
        }
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    influence["rejection_evidence"][0]["candidate_scalar_ref"] = scalar["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    raw["translation_batches"] = ()
    raw["frontier_facts"] = ()
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match="scalar partition"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


@pytest.mark.parametrize(
    ("target", "field", "value", "message"),
    [
        ("influence", "root_action_id", "yfm7a-" + "f" * 24, "root action"),
        (
            "influence",
            "branch_materialized_action_id",
            "yfm7a-" + "e" * 24,
            "unchecked branch action",
        ),
        ("root", "final_state_sha256", SHA_D, "terminal state"),
    ],
)
def test_action_root_binds_branch_action_and_state_chain(
    target: str,
    field: str,
    value: str,
    message: str,
) -> None:
    raw = _bundle().model_dump(mode="python")
    if target == "influence":
        raw["influence_facts"][0][field] = value
        _rehash_fact(raw["influence_facts"][0])
        raw["action_roots"][0]["influence_fact_refs"] = (raw["influence_facts"][0]["fact_sha256"],)
    else:
        raw["action_roots"][0][field] = value
    _rehash_fact(raw["action_roots"][0])
    _rehash_bundle(raw)

    with pytest.raises(ValidationError, match=message):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_position", True),
        ("event_position", "1"),
        ("generated_candidate_count", False),
    ],
)
def test_counts_reject_bool_and_coercion(field: str, value: object) -> None:
    raw = _translation().model_dump(mode="python")
    raw[field] = value
    _rehash_fact(raw)
    with pytest.raises(ValidationError):
        M8PortableTranslationBatch.model_validate(raw, strict=True)


def test_unknown_schema_and_modes_fail_closed() -> None:
    raw = _bundle().model_dump(mode="python")
    raw["schema_version"] = "yieldforge.m8-unchecked-fact-bundle.v999"
    _rehash_bundle(raw)
    with pytest.raises(ValidationError):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)

    raw = _bundle().model_dump(mode="python")
    raw["common_lemmas"][0]["evidence_mode"] = "trust_me"
    _rehash_fact(raw["common_lemmas"][0])
    _rehash_bundle(raw)
    with pytest.raises(ValidationError):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_bundle_rejects_dangling_and_illegal_layer_references() -> None:
    raw = _bundle().model_dump(mode="python")
    raw["frontier_facts"][0]["candidate_scalar_refs"] = (SHA_D,)
    raw["frontier_facts"][0]["retained_candidate_scalar_refs"] = (SHA_D,)
    _rehash_fact(raw["frontier_facts"][0])
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="dangling candidate-scalar"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)

    raw = _bundle().model_dump(mode="python")
    influence_hash = raw["influence_facts"][0]["fact_sha256"]
    raw["common_lemmas"][0]["frontier_refs"] = (influence_hash,)
    raw["common_lemmas"][0]["inventory_classifications"][0]["frontier_ref"] = influence_hash
    _rehash_fact(raw["common_lemmas"][0])
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="dangling frontier"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_bundle_rejects_duplicate_serialized_and_duplicate_identity_entries() -> None:
    raw = _bundle().model_dump(mode="python")
    raw["translation_batches"] = (
        raw["translation_batches"][0],
        deepcopy(raw["translation_batches"][0]),
    )
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="duplicate"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_bundle_rejects_unused_fact() -> None:
    raw = _bundle().model_dump(mode="python")
    extra = deepcopy(raw["candidate_scalar_facts"][0])
    extra["candidate_id"] = "unused-candidate"
    _rehash_fact(extra)
    raw["candidate_scalar_facts"] = tuple(
        sorted(
            (raw["candidate_scalar_facts"][0], extra),
            key=lambda item: item["fact_sha256"],
        )
    )
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="unused"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


@pytest.mark.parametrize("context_field", ["semantic_runtime_sha256", "stream_id"])
def test_bundle_rejects_self_consistent_cross_context_fact(context_field: str) -> None:
    raw = _bundle().model_dump(mode="python")
    replacement = SHA_D if context_field.endswith("sha256") else "yfts-" + "e" * 24
    raw["influence_facts"][0][context_field] = replacement
    _rehash_fact(raw["influence_facts"][0])
    raw["action_roots"][0]["influence_fact_refs"] = (raw["influence_facts"][0]["fact_sha256"],)
    _rehash_fact(raw["action_roots"][0])
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="context"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_bundle_rejects_out_of_order_fixed_layer_tuple() -> None:
    raw = _bundle().model_dump(mode="python")
    first = raw["translation_batches"][0]
    second = deepcopy(first)
    second["candidate_id"] = "candidate-b"
    _rehash_fact(second)
    ordered = sorted((first, second), key=lambda item: item["fact_sha256"])
    raw["translation_batches"] = tuple(reversed(ordered))
    common = raw["common_lemmas"][0]
    common["translation_batch_refs"] = tuple(item["fact_sha256"] for item in ordered)
    common["inventory_classifications"][0]["translation_batch_refs"] = tuple(
        item["fact_sha256"] for item in ordered
    )
    _rehash_fact(common)
    influence = raw["influence_facts"][0]
    influence["common_lemma_ref"] = common["fact_sha256"]
    _rehash_fact(influence)
    root = raw["action_roots"][0]
    root["common_lemma_refs"] = (common["fact_sha256"],)
    root["influence_fact_refs"] = (influence["fact_sha256"],)
    _rehash_fact(root)
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="deterministic order"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_action_root_requires_ordered_complete_event_coverage() -> None:
    raw = _bundle().model_dump(mode="python")
    raw["action_roots"][0]["stop_event_position"] = 3
    _rehash_fact(raw["action_roots"][0])
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="complete event coverage"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_recomputed_hash_cannot_hide_semantic_context_mutation() -> None:
    raw = _bundle().model_dump(mode="python")
    raw["common_lemmas"][0]["event_occurred_at"] = _dt(2)
    _rehash_fact(raw["common_lemmas"][0])
    raw["influence_facts"][0]["common_lemma_ref"] = raw["common_lemmas"][0]["fact_sha256"]
    _rehash_fact(raw["influence_facts"][0])
    raw["action_roots"][0]["common_lemma_refs"] = (raw["common_lemmas"][0]["fact_sha256"],)
    raw["action_roots"][0]["influence_fact_refs"] = (raw["influence_facts"][0]["fact_sha256"],)
    _rehash_fact(raw["action_roots"][0])
    _rehash_bundle(raw)
    with pytest.raises(ValidationError, match="legacy common transition|storage interval"):
        M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_evaluation_partition_and_opened_evaluation_fail_closed() -> None:
    for field, value in (("partition", "evaluation"), ("evaluation_partition_opened", True)):
        raw = _bundle().model_dump(mode="python")
        raw["provenance"][field] = value
        _rehash_bundle(raw)
        with pytest.raises(ValidationError):
            M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def test_committed_v3_artifact_and_v1_action_proof_strict_load_unchanged() -> None:
    artifact_path = (
        "experiments/results/m8-certificate-proof-yfm8proof-b296ba919c07d55ece14c6db.json"
    )
    raw_artifact = open(artifact_path, encoding="utf-8").read()
    loaded = M8CertificateProofResult.model_validate_json(raw_artifact, strict=True)
    assert loaded.schema_version == "yieldforge.m8-certificate-proof.v3"

    frozen_v1_json = (
        '{"schema_version":"yieldforge.m8-action-proof.v1",'
        '"proof_id":"yfm8ap-5e3db4a4801bb99dca2b6312",'
        '"content_sha256":"sha256:5e3db4a4801bb99dca2b6312cb444e5fe0677e9aa624f9d341a0f764dc492efd",'
        '"action_id":"yfm7a-111111111111111111111111",'
        '"catalog_action_id":"catalog-a",'
        '"baseline_action_id":"yfm7a-222222222222222222222222",'
        '"baseline_catalog_action_id":"catalog-b","start_event_position":0,'
        '"stop_event_position":1,'
        '"suffix_sha256":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"semantic_runtime_sha256":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"start_state_sha256":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        '"witnesses":[],"final_net_cost":1.0,'
        '"final_state_sha256":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'
    )
    proof = M8ActionProof.model_validate_json(frozen_v1_json, strict=True)
    assert proof.schema_version == "yieldforge.m8-action-proof.v1"
