from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import experiment
from yieldforge.oracle.facts import encode_canonical_f64
from yieldforge.oracle.gate3_evidence import (
    M8Gate3AuditComputationIdentity,
    M8Gate3AuditSample,
    M8Gate3AuditSampleAction,
    M8Gate3CheckedActionRoot,
    M8Gate3CheckedV2AuditRecord,
    M8Gate3MutationOutcome,
    M8Gate3MutationRecipeBinding,
    M8Gate3NormalizedActionRecord,
    M8Gate3NormalizedEventEvidence,
    M8Gate3NormalizedInfluenceEvidence,
    M8Gate3NormalizedPolicyEvidence,
    M8Gate3ReferenceCostAttestation,
    M8Gate3RootMembershipAttestation,
    M8Gate3RootMembershipBinding,
    M8Gate3V1CheckerAuditRecord,
    M8Gate3V1GeneratorAuditRecord,
    build_gate3_mutation_manifest,
    build_gate3_mutation_recipe,
    build_gate3_mutation_result,
    build_gate3_reference_timing,
    finalize_gate3_audit,
    finalize_gate3_decision,
    finalize_gate3_mutation_execution,
    finalize_gate3_performance,
    freeze_gate3_audit_sample,
    freeze_gate3_checked_root_manifest,
    gate3_checked_root_sequence_sha256,
    load_parent_v3_certificate_proof,
    normalized_gate3_action_semantic_sha256,
)
from yieldforge.temporal_benchmark.contracts import TemporalRegime

_PARENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "experiments/results/m8-certificate-proof-yfm8proof-b296ba919c07d55ece14c6db.json"
)


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_id(prefix: str, content_sha256: str) -> str:
    return f"{prefix}{content_sha256.removeprefix('sha256:')[:24]}"


def _portable_gate3_result(
    *,
    total_pipeline_wall_seconds: float = 4.0,
    outcome_salt: str = "base",
) -> experiment.M8PortableFactGate3Result:
    registry = experiment.M8PortableRegistryEvidence()
    timing = experiment.M8PortableFactPhaseTiming(
        first_generation_worker_wall_seconds=0.4,
        second_generation_worker_wall_seconds=0.5,
        producer_bundle_serialization_wall_seconds=0.1,
        producer_handoff_serialization_wall_seconds=0.1,
        metadata_reconciliation_wall_seconds=0.05,
        authority_reconstruction_wall_seconds=0.05,
        checker_worker_wall_seconds=1.0,
        checker_strict_load_inclusive_wall_seconds=0.1,
        common_verification_inclusive_wall_seconds=0.2,
        action_traversal_inclusive_wall_seconds=0.3,
        exact_fallback_nested_exclusive_wall_seconds=0.0,
        capability_cleanup_inclusive_wall_seconds=0.1,
    )

    def cell(
        regime: TemporalRegime,
        digit: str,
        roots: int,
        byte_count: int,
    ) -> experiment.M8PortableFactGate3Cell:
        fixed = 1 + 1 + 1 + 1 + 2 + 3 + roots
        bundle_sha = _sha(f"bundle:{regime.value}:{outcome_salt}")
        decision_sha = _sha(f"decision:{regime.value}:{outcome_salt}")
        return experiment.M8PortableFactGate3Cell(
            regime=regime,
            temporal_seed=2026082300,
            stream_id="yfts-" + digit * 24,
            event_count=2,
            first_bundle_sha256=bundle_sha,
            second_bundle_sha256=bundle_sha,
            first_semantic_bundle_bytes_sha256=bundle_sha,
            second_semantic_bundle_bytes_sha256=bundle_sha,
            semantic_serialized_bytes=byte_count,
            repeated_semantic_serialized_bytes=byte_count,
            fixed_layer_node_count=fixed,
            translation_batch_count=1,
            candidate_scalar_fact_count=1,
            frontier_fact_count=1,
            standard_candidate_fact_count=1,
            common_lemma_count=2,
            influence_fact_count=3,
            generated_action_root_count=roots,
            checked_common_lemma_count=2,
            checked_influence_fact_count=3,
            checked_action_root_count=roots,
            decision_id=_content_id("yfm8d-", decision_sha),
            decision_content_sha256=decision_sha,
            producer_counted_inventory_evidence_row_count=3,
            producer_counted_search_lemma_count=2,
            counted_translation_audit_count=2,
            counted_translation_audit_call_count=2,
            influence_translation_audit_count=3,
            common_exact_fallback_wall_seconds=0.0,
            influence_exact_fallback_wall_seconds=0.0,
            total_exact_fallback_wall_seconds=0.0,
            timing=timing,
            first_generator_registry_state=registry,
            second_generator_registry_state=registry,
            checker_registry_state=registry,
        )

    cells = (
        cell(TemporalRegime.NO_SIGNAL, "1", 428, 1000),
        cell(TemporalRegime.REGIME_SHIFT, "2", 459, 1100),
    )
    payload = {
        "m0_contract_id": "yfm0-" + "1" * 24,
        "m0_contract_sha256": "sha256:" + "1" * 64,
        "m6_contract_id": "yfm6-" + "2" * 24,
        "m6_contract_sha256": "sha256:" + "2" * 64,
        "m6_population_id": "yftp-" + "3" * 24,
        "m6_population_sha256": "sha256:" + "3" * 64,
        "problem_index_id": "yfm7i-" + "4" * 24,
        "problem_index_sha256": "sha256:" + "4" * 64,
        "freeze_id": "yfm7freeze-" + "5" * 24,
        "freeze_sha256": "sha256:" + "5" * 64,
        "calibration_view_id": "yfm7cv-" + "6" * 24,
        "calibration_view_sha256": "sha256:" + "6" * 64,
        "cells": cells,
        "semantic_serialized_bytes": 2100,
        "fixed_layer_node_count": sum(item.fixed_layer_node_count for item in cells),
        "generated_action_root_count": 887,
        "checked_action_root_count": 887,
        "total_exact_fallback_count": 0,
        "total_exact_fallback_wall_seconds": 0.0,
        "first_generation_phase_wall_seconds": 1.0,
        "second_generation_phase_wall_seconds": 1.0,
        "checker_phase_wall_seconds": 1.5,
        "task_serialization_wall_seconds": 0.2,
        "result_serialization_wall_seconds": 0.2,
        "inbound_payload_handoff_wall_seconds": 0.1,
        "outbound_payload_handoff_wall_seconds": 0.2,
        "worker_payload_handoff_wall_seconds": 0.3,
        "process_exit_validation_wall_seconds": 0.1,
        "worker_task_payload_bytes": 300,
        "worker_result_payload_bytes": 400,
        "retained_first_generation_bundle_bytes": 2100,
        "checker_task_payload_bytes": 300,
        "total_pipeline_wall_seconds": total_pipeline_wall_seconds,
        "controller_registry_state_before": registry,
        "controller_registry_state_after": registry,
    }
    draft = experiment.M8PortableFactGate3Result.model_construct(
        gate3_id="yfm8gate3-" + "0" * 24,
        content_sha256="sha256:" + "0" * 64,
        **payload,
    )
    digest = semantic_sha256(draft, excluded_fields={"gate3_id", "content_sha256"})
    return experiment.M8PortableFactGate3Result(
        gate3_id=f"yfm8gate3-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **payload,
    )


def _checked_roots(
    gate3: experiment.M8PortableFactGate3Result,
) -> tuple[M8Gate3CheckedActionRoot, ...]:
    roots: list[M8Gate3CheckedActionRoot] = []
    for cell in gate3.cells:
        baseline_action_id = (
            "yfm7a-"
            + hashlib.sha256(f"{cell.regime.value}:baseline-action".encode()).hexdigest()[:24]
        )
        baseline_catalog_action_id = f"m7-standard:{cell.regime.value}:0"
        for index in range(cell.generated_action_root_count):
            action_id = (
                baseline_action_id
                if index == 0
                else "yfm7a-"
                + hashlib.sha256(f"{cell.regime.value}:action:{index}".encode()).hexdigest()[:24]
            )
            roots.append(
                M8Gate3CheckedActionRoot(
                    regime=cell.regime,
                    temporal_seed=cell.temporal_seed,
                    stream_id=cell.stream_id,
                    source_bundle_sha256=cell.first_bundle_sha256,
                    checker_decision_id=cell.decision_id,
                    checker_decision_content_sha256=cell.decision_content_sha256,
                    root_fact_sha256=_sha(
                        f"{cell.first_bundle_sha256}:root:{cell.regime.value}:{index}"
                    ),
                    action_id=action_id,
                    catalog_action_id=f"m7-standard:{cell.regime.value}:{index}",
                    baseline_action_id=baseline_action_id,
                    baseline_catalog_action_id=baseline_catalog_action_id,
                    start_event_position=0,
                    stop_event_position=2,
                    suffix_sha256=_sha(f"{cell.regime.value}:suffix"),
                    semantic_runtime_sha256=_sha(f"{cell.regime.value}:runtime"),
                    start_state_sha256=_sha(f"{cell.regime.value}:start"),
                    initial_state_after_sha256=_sha(f"{cell.regime.value}:initial:{index}"),
                    final_state_sha256=_sha(f"{cell.regime.value}:final:{index}"),
                )
            )
    return tuple(roots)


def _membership_attestation(
    gate3: experiment.M8PortableFactGate3Result,
    roots: tuple[M8Gate3CheckedActionRoot, ...],
) -> M8Gate3RootMembershipAttestation:
    bindings = tuple(
        M8Gate3RootMembershipBinding(
            regime=cell.regime,
            source_bundle_sha256=cell.first_bundle_sha256,
            source_semantic_bundle_bytes_sha256=(cell.first_semantic_bundle_bytes_sha256),
            checker_decision_id=cell.decision_id,
            checker_decision_content_sha256=cell.decision_content_sha256,
            checked_root_count=cell.checked_action_root_count,
        )
        for cell in gate3.cells
    )
    payload = {
        "schema_version": "yieldforge.m8-gate3-root-membership-attestation.v1",
        "portable_gate3_id": gate3.gate3_id,
        "portable_gate3_content_sha256": gate3.content_sha256,
        "producer_id": "m8-task8-root-membership-extractor-v1",
        "producer_content_sha256": _sha("root-membership-extractor"),
        "runtime_id": "python-test-runtime",
        "runtime_content_sha256": _sha("python-test-runtime"),
        "bindings": bindings,
        "checked_root_count": 887,
        "checked_root_sequence_sha256": gate3_checked_root_sequence_sha256(roots),
        "source_verification_scope": (
            "external_strict_canonical_bundle_and_checked_result_membership"
        ),
        "canonical_bundle_bytes_retained_by_executor": True,
        "checked_result_retained_by_executor": True,
        "producer_exit_code": 0,
        "surviving_descendant_count": 0,
        "surviving_registry_count": 0,
        "evaluation_accessed": False,
        "claim_ceiling": (
            "executor_attested_checked_bundle_membership_only_source_bytes_not_embedded_or_"
            "reverified_here"
        ),
    }
    draft = M8Gate3RootMembershipAttestation.model_construct(
        attestation_id="yfm8g3membership-" + "0" * 24,
        content_sha256="sha256:" + "0" * 64,
        **payload,
    )
    digest = semantic_sha256(
        draft,
        excluded_fields={"attestation_id", "content_sha256"},
    )
    return M8Gate3RootMembershipAttestation(
        attestation_id=f"yfm8g3membership-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **payload,
    )


def _freeze_manifest(
    gate3: experiment.M8PortableFactGate3Result,
    roots: tuple[M8Gate3CheckedActionRoot, ...],
):  # type: ignore[no-untyped-def]
    return freeze_gate3_checked_root_manifest(
        gate3,
        roots,
        membership_attestation=_membership_attestation(gate3, roots),
    )


@pytest.fixture(scope="module")
def parent_v3():  # type: ignore[no-untyped-def]
    return load_parent_v3_certificate_proof(_PARENT_PATH)


@pytest.fixture(scope="module")
def gate3() -> experiment.M8PortableFactGate3Result:
    return _portable_gate3_result()


@pytest.fixture(scope="module")
def manifest(gate3):  # type: ignore[no-untyped-def]
    return _freeze_manifest(gate3, _checked_roots(gate3))


@pytest.fixture(scope="module")
def sample(parent_v3, manifest):  # type: ignore[no-untyped-def]
    return freeze_gate3_audit_sample(parent_v3, manifest)


def test_parent_v3_is_strict_loaded_and_only_four_target_bindings_are_lineage(
    parent_v3,
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    assert parent_v3.proof_id == "yfm8proof-b296ba919c07d55ece14c6db"
    assert sample.parent_v3_target_regime_binding_count == 4
    assert sample.parent_v3_total_binding_count == 12
    assert sample.inherited_parent_proof_count == 0
    assert sample.parent_v3_sample_reused is False
    assert tuple(item.regime for item in sample.parent_v3_target_regime_bindings) == (
        TemporalRegime.NO_SIGNAL,
        TemporalRegime.NO_SIGNAL,
        TemporalRegime.REGIME_SHIFT,
        TemporalRegime.REGIME_SHIFT,
    )
    forged = parent_v3.model_copy(update={"content_sha256": _sha("forged")})
    with pytest.raises(ValidationError, match="SHA-256"):
        freeze_gate3_audit_sample(forged, manifest)


def test_root_manifest_is_complete_content_addressed_and_derives_action_events(
    gate3,
    manifest,
) -> None:  # type: ignore[no-untyped-def]
    assert manifest.checked_root_count == 887
    assert manifest.no_signal_checked_root_count == 428
    assert manifest.regime_shift_checked_root_count == 459
    assert manifest.observed_action_event_count == 887
    assert manifest == _freeze_manifest(
        gate3,
        tuple(reversed(manifest.roots)),
    )
    assert type(manifest).model_validate_json(manifest.model_dump_json(), strict=True) == manifest

    with pytest.raises(ValueError, match="external membership attestation"):
        freeze_gate3_checked_root_manifest(gate3, manifest.roots)

    changed_root = manifest.roots[0].model_copy(
        update={"root_fact_sha256": _sha("coherent-but-different-root")}
    )
    wrong_attestation = _membership_attestation(
        gate3,
        (changed_root, *manifest.roots[1:]),
    )
    with pytest.raises(ValueError, match="root sequence"):
        freeze_gate3_checked_root_manifest(
            gate3,
            manifest.roots,
            membership_attestation=wrong_attestation,
        )

    inflated = manifest.roots[0].model_copy(update={"stop_event_position": 1002})
    with pytest.raises(ValidationError, match="stop_event_position"):
        _freeze_manifest(
            gate3,
            (inflated, *manifest.roots[1:]),
        )


def test_root_identity_uniqueness_is_regime_qualified(gate3) -> None:  # type: ignore[no-untyped-def]
    roots = list(_checked_roots(gate3))
    no_signal = next(
        item for item in roots if item.regime is TemporalRegime.NO_SIGNAL and not item.is_baseline
    )
    shift_index = next(
        index
        for index, item in enumerate(roots)
        if item.regime is TemporalRegime.REGIME_SHIFT and not item.is_baseline
    )
    roots[shift_index] = roots[shift_index].model_copy(
        update={"catalog_action_id": no_signal.catalog_action_id}
    )
    result = _freeze_manifest(gate3, tuple(roots))
    assert len({(item.regime, item.catalog_action_id) for item in result.roots}) == 887


def test_cost_blind_selection_is_invariant_to_coherent_outcome_hash_changes(
    parent_v3,
) -> None:  # type: ignore[no-untyped-def]
    first_gate3 = _portable_gate3_result(outcome_salt="first")
    second_gate3 = _portable_gate3_result(outcome_salt="second")
    first_manifest = _freeze_manifest(
        first_gate3,
        _checked_roots(first_gate3),
    )
    second_manifest = _freeze_manifest(
        second_gate3,
        _checked_roots(second_gate3),
    )
    first = freeze_gate3_audit_sample(parent_v3, first_manifest)
    second = freeze_gate3_audit_sample(parent_v3, second_manifest)

    def selections(value):  # type: ignore[no-untyped-def]
        return tuple(
            (
                item.regime,
                item.catalog_action_id,
                item.selection_rank_sha256,
                item.is_baseline,
            )
            for item in value.actions
        )

    assert selections(first) == selections(second)
    assert first.content_sha256 != second.content_sha256
    assert tuple(item.root_fact_sha256 for item in first.actions) != tuple(
        item.root_fact_sha256 for item in second.actions
    )
    assert tuple(item.source_bundle_sha256 for item in first.actions) != tuple(
        item.source_bundle_sha256 for item in second.actions
    )


def test_sample_is_baseline_plus_five_lowest_pre_outcome_ranks_per_probe(
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT):
        selected = tuple(item for item in sample.actions if item.regime is regime)
        candidates = tuple(item for item in manifest.roots if item.regime is regime)
        expected = tuple(
            sorted(
                (item for item in candidates if not item.is_baseline),
                key=lambda item: (
                    item.audit_rank_sha256,
                    item.catalog_action_id,
                ),
            )[:5]
        )
        assert len(selected) == 6
        assert selected[0].is_baseline is True
        assert tuple(item.catalog_action_id for item in selected[1:]) == tuple(
            item.catalog_action_id for item in expected
        )


def _normalized_record(action, *, cost: float = 10.0):  # type: ignore[no-untyped-def]
    common = M8Gate3NormalizedPolicyEvidence(
        action_id=action.action_id,
        catalog_action_id=action.catalog_action_id,
        decision_key=(f"action_id={action.catalog_action_id}", "cost=f64"),
    )
    influence = M8Gate3NormalizedInfluenceEvidence(
        remnant_id="yfrm-" + "1" * 24,
        candidate_id="candidate-1",
        classification="no_fit",
        evidence_sha256=_sha(f"influence:{action.catalog_action_id}"),
        common_policy=common,
    )
    event = M8Gate3NormalizedEventEvidence(
        event_position=1,
        classification="no_fit",
        common_action_id=action.action_id,
        branch_action_id=action.action_id,
        state_before_sha256=action.initial_state_after_sha256,
        state_after_sha256=action.final_state_sha256,
        influences=(influence,),
    )
    return M8Gate3NormalizedActionRecord(
        action_id=action.action_id,
        catalog_action_id=action.catalog_action_id,
        baseline_action_id=action.baseline_action_id,
        baseline_catalog_action_id=action.baseline_catalog_action_id,
        start_event_position=action.start_event_position,
        stop_event_position=action.stop_event_position,
        suffix_sha256=action.suffix_sha256,
        semantic_runtime_sha256=action.semantic_runtime_sha256,
        start_state_sha256=action.start_state_sha256,
        initial_state_after_sha256=action.initial_state_after_sha256,
        final_state_sha256=action.final_state_sha256,
        ordered_event_evidence=(event,),
        final_net_cost_bits=encode_canonical_f64(cost),
    )


def _computation_identity(
    role: str,
    *,
    output_content_sha256: str,
) -> M8Gate3AuditComputationIdentity:
    return M8Gate3AuditComputationIdentity(
        role=role,
        implementation_id=f"m8-gate3-{role}-implementation-v1",
        implementation_content_sha256=_sha(f"implementation:{role}"),
        runtime_id=f"m8-gate3-{role}-runtime-v1",
        runtime_content_sha256=_sha(f"runtime:{role}"),
        output_content_sha256=output_content_sha256,
        worker_exit_code=0,
        evaluation_accessed=False,
    )


def _audit_inputs(sample):  # type: ignore[no-untyped-def]
    generators = []
    checkers = []
    checked_v2 = []
    references = []
    for action in sample.actions:
        normalized = _normalized_record(action)
        proof_sha = _sha(f"proof:{action.regime.value}:{action.catalog_action_id}")
        proof_id = _content_id("yfm8ap-", proof_sha)
        generators.append(
            M8Gate3V1GeneratorAuditRecord(
                action=action,
                computation=_computation_identity(
                    "v1_generator",
                    output_content_sha256=proof_sha,
                ),
                generated_proof_id=proof_id,
                generated_proof_content_sha256=proof_sha,
                normalized=normalized,
            )
        )
        checkers.append(
            M8Gate3V1CheckerAuditRecord(
                action=action,
                computation=_computation_identity(
                    "v1_checker",
                    output_content_sha256=_sha(
                        f"v1-checker:{action.regime.value}:{action.catalog_action_id}"
                    ),
                ),
                checked_proof_id=proof_id,
                checked_proof_content_sha256=proof_sha,
                checked_semantic_sha256=normalized_gate3_action_semantic_sha256(normalized),
                checked_final_net_cost_bits=normalized.final_net_cost_bits,
                checker_valid=True,
                checked_event_count=1,
                certificate_count=1,
                exact_transition_count=0,
                failure_code="valid",
            )
        )
        checked_v2.append(
            M8Gate3CheckedV2AuditRecord(
                action=action,
                computation=_computation_identity(
                    "checked_v2",
                    output_content_sha256=action.root_fact_sha256,
                ),
                normalized=normalized,
            )
        )
        references.append(
            M8Gate3ReferenceCostAttestation(
                computation=_computation_identity(
                    "reference",
                    output_content_sha256=_sha(
                        f"reference:{action.regime.value}:{action.catalog_action_id}"
                    ),
                ),
                regime=action.regime,
                action_id=action.action_id,
                catalog_action_id=action.catalog_action_id,
                root_fact_sha256=action.root_fact_sha256,
                final_net_cost_bits=normalized.final_net_cost_bits,
            )
        )
    return tuple(generators), tuple(checkers), tuple(checked_v2), tuple(references)


def test_four_way_audit_distinguishes_generator_checker_checked_v2_and_reference(
    sample,
) -> None:  # type: ignore[no-untyped-def]
    generators, checkers, checked_v2, references = _audit_inputs(sample)
    result = finalize_gate3_audit(
        sample,
        generators,
        checkers,
        checked_v2,
        references,
    )
    assert result.proof_binding_mismatch_count == 0
    assert result.independence_mismatch_count == 0
    assert result.semantic_mismatch_count == 0
    assert result.cost_mismatch_count == 0
    assert result.total_mismatch_count == 0
    assert result.proof_decision == "pass_proof_audit"
    assert type(result.comparisons[0].v1_generator) is M8Gate3V1GeneratorAuditRecord
    assert type(result.comparisons[0].v1_checker) is M8Gate3V1CheckerAuditRecord
    assert type(result.comparisons[0].checked_v2) is M8Gate3CheckedV2AuditRecord
    assert not hasattr(result.comparisons[0].reference, "normalized")


def test_four_way_audit_records_proof_semantic_and_canonical_cost_mismatches(
    sample,
) -> None:  # type: ignore[no-untyped-def]
    generators, checkers, checked_v2, references = _audit_inputs(sample)
    wrong_proof_sha = _sha("different-proof")
    wrong_proof = checkers[0].model_copy(
        update={
            "checked_proof_id": _content_id("yfm8ap-", wrong_proof_sha),
            "checked_proof_content_sha256": wrong_proof_sha,
        }
    )
    event = checked_v2[1].normalized.ordered_event_evidence[0]
    influence = event.influences[0].model_copy(
        update={"evidence_sha256": _sha("different-influence")}
    )
    semantic_mismatch = checked_v2[1].model_copy(
        update={
            "normalized": checked_v2[1].normalized.model_copy(
                update={
                    "ordered_event_evidence": (
                        event.model_copy(update={"influences": (influence,)}),
                    )
                }
            )
        }
    )
    cost_mismatch = references[2].model_copy(
        update={"final_net_cost_bits": encode_canonical_f64(11.0)}
    )
    result = finalize_gate3_audit(
        sample,
        generators,
        (wrong_proof, *checkers[1:]),
        (checked_v2[0], semantic_mismatch, *checked_v2[2:]),
        (references[0], references[1], cost_mismatch, *references[3:]),
    )
    assert result.proof_binding_mismatch_count == 1
    assert result.semantic_mismatch_count == 1
    assert result.cost_mismatch_count == 1
    assert result.total_mismatch_count == 3
    assert result.proof_decision == "redesign_proof"

    copied_identities = tuple(
        item.model_copy(
            update={
                "computation": item.computation.model_copy(
                    update={
                        "implementation_id": generators[0].computation.implementation_id,
                        "implementation_content_sha256": (
                            generators[0].computation.implementation_content_sha256
                        ),
                    }
                )
            }
        )
        for item in checked_v2
    )
    independent = finalize_gate3_audit(
        sample,
        generators,
        checkers,
        copied_identities,
        references,
    )
    assert independent.independence_mismatch_count == 12
    assert independent.proof_decision == "redesign_proof"

    wrong_counts = checkers[0].model_copy(update={"checked_event_count": 2})
    count_result = finalize_gate3_audit(
        sample,
        generators,
        (wrong_counts, *checkers[1:]),
        checked_v2,
        references,
    )
    assert count_result.proof_binding_mismatch_count == 1
    assert count_result.proof_decision == "redesign_proof"

    collapsed_generators = tuple(
        item.model_copy(
            update={
                "computation": item.computation.model_copy(
                    update={"output_content_sha256": generators[0].generated_proof_content_sha256}
                ),
                "generated_proof_id": generators[0].generated_proof_id,
                "generated_proof_content_sha256": (generators[0].generated_proof_content_sha256),
            }
        )
        for item in generators
    )
    collapsed_checkers = tuple(
        item.model_copy(
            update={
                "computation": item.computation.model_copy(
                    update={
                        "output_content_sha256": (checkers[0].computation.output_content_sha256)
                    }
                ),
                "checked_proof_id": generators[0].generated_proof_id,
                "checked_proof_content_sha256": (generators[0].generated_proof_content_sha256),
            }
        )
        for item in checkers
    )
    with pytest.raises(ValueError, match="unique per-action outputs"):
        finalize_gate3_audit(
            sample,
            collapsed_generators,
            collapsed_checkers,
            checked_v2,
            references,
        )


def test_four_way_audit_rejects_one_action_role_implementation_or_runtime_drift(
    sample,
) -> None:  # type: ignore[no-untyped-def]
    generators, checkers, checked_v2, references = _audit_inputs(sample)
    for identity_update in (
        {
            "implementation_id": "m8-gate3-v1-checker-implementation-v2",
            "implementation_content_sha256": _sha("implementation:v1_checker:v2"),
        },
        {
            "runtime_id": "m8-gate3-v1-checker-runtime-v2",
            "runtime_content_sha256": _sha("runtime:v1_checker:v2"),
        },
    ):
        drifted_checker = checkers[0].model_copy(
            update={
                "computation": checkers[0].computation.model_copy(update=identity_update),
            }
        )
        computations = (
            generators[0].computation,
            drifted_checker.computation,
            checked_v2[0].computation,
            references[0].computation,
        )
        assert len({item.implementation_content_sha256 for item in computations}) == 4
        assert len({item.output_content_sha256 for item in computations}) == 4

        with pytest.raises(ValueError, match="implementation/runtime identities drift"):
            finalize_gate3_audit(
                sample,
                generators,
                (drifted_checker, *checkers[1:]),
                checked_v2,
                references,
            )


def test_four_way_audit_rejects_cross_action_cross_role_output_reuse(
    sample,
) -> None:  # type: ignore[no-untyped-def]
    generators, checkers, checked_v2, references = _audit_inputs(sample)
    reference_outputs = tuple(item.computation.output_content_sha256 for item in references)
    rotated_reference_outputs = (*reference_outputs[1:], reference_outputs[0])
    colliding_checkers = tuple(
        item.model_copy(
            update={
                "computation": item.computation.model_copy(
                    update={"output_content_sha256": rotated_reference_outputs[index]}
                )
            }
        )
        for index, item in enumerate(checkers)
    )
    role_outputs = (
        tuple(item.computation.output_content_sha256 for item in generators),
        tuple(item.computation.output_content_sha256 for item in colliding_checkers),
        tuple(item.computation.output_content_sha256 for item in checked_v2),
        reference_outputs,
    )
    assert tuple(len(set(items)) for items in role_outputs) == (12, 12, 12, 12)
    assert len({item for items in role_outputs for item in items}) == 36
    assert all(
        len(
            {
                generator.computation.output_content_sha256,
                checker.computation.output_content_sha256,
                v2.computation.output_content_sha256,
                reference.computation.output_content_sha256,
            }
        )
        == 4
        for generator, checker, v2, reference in zip(
            generators,
            colliding_checkers,
            checked_v2,
            references,
            strict=True,
        )
    )

    with pytest.raises(ValueError, match="output identities overlap across roles"):
        finalize_gate3_audit(
            sample,
            generators,
            colliding_checkers,
            checked_v2,
            references,
        )


def test_normalized_witness_semantics_fail_closed(sample) -> None:  # type: ignore[no-untyped-def]
    action = sample.actions[0]
    with pytest.raises(ValidationError, match="state-rejoin"):
        M8Gate3NormalizedEventEvidence(
            event_position=1,
            classification="state_rejoin",
            common_action_id=action.action_id,
            branch_action_id=sample.actions[1].action_id,
            state_before_sha256=action.initial_state_after_sha256,
            state_after_sha256=action.final_state_sha256,
        )

    record = _normalized_record(action)
    duplicate = record.ordered_event_evidence[0].influences[0]
    with pytest.raises(ValidationError, match="unique"):
        M8Gate3NormalizedEventEvidence(
            event_position=1,
            classification="no_fit",
            common_action_id=action.action_id,
            branch_action_id=action.action_id,
            state_before_sha256=action.initial_state_after_sha256,
            state_after_sha256=action.final_state_sha256,
            influences=(duplicate, duplicate),
        )


def _mutation_evidence(parent_v3, gate3, manifest, sample):  # type: ignore[no-untyped-def]
    mutation_manifest = build_gate3_mutation_manifest(
        parent_v3,
        gate3,
        manifest,
        sample,
        harness_id="m8-gate3-mutation-harness-v1",
        harness_content_sha256=_sha("harness"),
        runtime_id="python-runtime-test",
        runtime_content_sha256=_sha("runtime"),
        additional_base_content_sha256s=(_sha("checker-source"),),
    )
    outcomes = tuple(
        M8Gate3MutationOutcome(
            recipe_id=item.recipe_id,
            recipe_sha256=item.recipe_sha256,
            target_content_sha256=item.target_content_sha256,
            expected_failure_code=item.expected_failure_code,
            observed_failure_code=item.expected_failure_code,
            rehash_required=item.rehash_required,
            rehash_performed=item.rehash_required,
            mutation_rejected=True,
            worker_exit_code=0,
            surviving_descendant_count=0,
            surviving_registry_count=0,
            artifact_published=False,
            evaluation_accessed=False,
        )
        for item in mutation_manifest.recipes
    )
    result = finalize_gate3_mutation_execution(
        mutation_manifest,
        harness_id="m8-gate3-mutation-harness-v1",
        harness_content_sha256=_sha("harness"),
        runtime_id="python-runtime-test",
        runtime_content_sha256=_sha("runtime"),
        outcomes=outcomes,
    )
    return mutation_manifest, outcomes, result


def test_external_mutation_counts_cannot_pass_and_bound_execution_reconciles_manifest(
    parent_v3,
    gate3,
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="external mutation counts"):
        build_gate3_mutation_result(
            manifest_sha256=_sha("external"),
            planned_mutation_count=200,
            executed_mutation_count=200,
            rejected_mutation_count=200,
        )
    mutation_manifest, outcomes, result = _mutation_evidence(
        parent_v3,
        gate3,
        manifest,
        sample,
    )
    assert result.manifest == mutation_manifest
    assert result.registered_recipe_count == 16
    assert result.complete_manifest_reconciliation is True
    assert result.all_expected_failure_codes_match is True
    assert result.all_required_rehashes_performed is True
    assert result.all_worker_exits_clean is True
    assert result.evaluation_accessed is False
    assert result.mutation_decision == "pass_executed_mutations"

    incomplete = finalize_gate3_mutation_execution(
        mutation_manifest,
        harness_id="m8-gate3-mutation-harness-v1",
        harness_content_sha256=_sha("harness"),
        runtime_id="python-runtime-test",
        runtime_content_sha256=_sha("runtime"),
        outcomes=outcomes[:-1],
    )
    assert incomplete.complete_manifest_reconciliation is False
    assert incomplete.mutation_decision == "redesign_proof"

    with pytest.raises(ValidationError, match="target_kind"):
        M8Gate3MutationRecipeBinding(
            recipe_id="mutation-" + "0" * 24,
            recipe_sha256="sha256:" + "0" * 64,
            target_kind="unrelated_dummy",
            target_content_sha256=_sha("unrelated"),
            expected_failure_code="valid",
            rehash_required=False,
        )

    root_recipe = build_gate3_mutation_recipe(
        target_kind="checked_action_root",
        target_content_sha256=sample.actions[0].root_fact_sha256,
    )
    assert root_recipe.expected_failure_code == "checked_action_root_binding_mismatch"
    assert root_recipe.rehash_required is True

    with pytest.raises(ValueError, match="authorized harness"):
        finalize_gate3_mutation_execution(
            mutation_manifest,
            harness_id="unregistered-harness",
            harness_content_sha256=_sha("unregistered-harness"),
            runtime_id="python-runtime-test",
            runtime_content_sha256=_sha("runtime"),
            outcomes=outcomes,
        )


def test_mutation_execution_fails_closed_for_codes_rehash_worker_cleanup_and_evaluation(
    parent_v3,
    gate3,
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    mutation_manifest, outcomes, _ = _mutation_evidence(
        parent_v3,
        gate3,
        manifest,
        sample,
    )
    broken = outcomes[0].model_copy(
        update={
            "observed_failure_code": "wrong_failure",
            "rehash_performed": False,
            "worker_exit_code": 1,
            "surviving_descendant_count": 1,
            "evaluation_accessed": True,
        }
    )
    result = finalize_gate3_mutation_execution(
        mutation_manifest,
        harness_id="m8-gate3-mutation-harness-v1",
        harness_content_sha256=_sha("harness"),
        runtime_id="python-runtime-test",
        runtime_content_sha256=_sha("runtime"),
        outcomes=(broken, *outcomes[1:]),
    )
    assert result.complete_manifest_reconciliation is True
    assert result.all_expected_failure_codes_match is False
    assert result.all_required_rehashes_performed is False
    assert result.all_worker_exits_clean is False
    assert result.surviving_descendant_count == 1
    assert result.evaluation_accessed is True
    assert result.mutation_decision == "redesign_proof"


def _reference_timings(sample, seconds: float = 10.0):  # type: ignore[no-untyped-def]
    return tuple(
        build_gate3_reference_timing(
            regime=item.regime,
            action_id=item.action_id,
            catalog_action_id=item.catalog_action_id,
            root_fact_sha256=item.root_fact_sha256,
            computation=_computation_identity(
                "reference",
                output_content_sha256=_sha(
                    f"reference:{item.regime.value}:{item.catalog_action_id}"
                ),
            ),
            worker_seconds=seconds,
        )
        for item in sample.actions
    )


def test_performance_denominator_comes_from_manifest_and_timings_are_keyed_to_sample(
    gate3,
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    timings = _reference_timings(sample)
    result = finalize_gate3_performance(
        gate3,
        manifest,
        sample,
        reference_timings=tuple(reversed(timings)),
    )
    expected_days = 4.0 / 887 * 550_542 * 11.5 * 2 / 86_400
    expected_reference_wall = (sum(item.worker_seconds for item in timings) / 12 * 887) / 8
    assert result.observed_action_event_count == manifest.observed_action_event_count == 887
    assert tuple(
        (item.regime, item.catalog_action_id) for item in result.reference_timings
    ) == tuple((item.regime, item.catalog_action_id) for item in sample.actions)
    assert result.projected_held_out_calendar_days == expected_days
    assert result.reference_equal_8_slot_wall_seconds == expected_reference_wall
    assert result.reference_equivalent_speedup == expected_reference_wall / 4.0
    assert result.performance_decision == "pass_abbreviated_performance"
    assert result.sensitivity.gating is False

    wrong = build_gate3_reference_timing(
        regime=timings[0].regime,
        action_id=timings[0].action_id,
        catalog_action_id=timings[0].catalog_action_id,
        root_fact_sha256=_sha("wrong-root"),
        computation=timings[0].computation,
        worker_seconds=timings[0].worker_seconds,
    )
    with pytest.raises(ValueError, match="sample action"):
        finalize_gate3_performance(
            gate3,
            manifest,
            sample,
            reference_timings=(wrong, *timings[1:]),
        )

    tampered = timings[0].model_copy(update={"worker_seconds": 0.001})
    with pytest.raises(ValidationError, match="content identity"):
        finalize_gate3_performance(
            gate3,
            manifest,
            sample,
            reference_timings=(tampered, *timings[1:]),
        )


def test_performance_thresholds_are_inclusive_and_one_ulp_beyond_holds(
    parent_v3,
    gate3,
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    speed_wall = 4.0
    exact_speed_timing = 25.0 * speed_wall * 8 / 887
    exact_speed = finalize_gate3_performance(
        gate3,
        manifest,
        sample,
        reference_timings=_reference_timings(sample, exact_speed_timing),
    )
    assert exact_speed.reference_equivalent_speedup == 25.0
    assert exact_speed.performance_decision == "pass_abbreviated_performance"

    below_speed = finalize_gate3_performance(
        gate3,
        manifest,
        sample,
        reference_timings=_reference_timings(
            sample,
            math.nextafter(exact_speed_timing, 0.0),
        ),
    )
    assert below_speed.reference_equivalent_speedup < 25.0
    assert below_speed.performance_decision == "hold_performance"

    exact_days_wall = 5.0 * 86_400 * 887 / (550_542 * 11.5 * 2)
    exact_days_gate3 = _portable_gate3_result(total_pipeline_wall_seconds=exact_days_wall)
    exact_days_manifest = _freeze_manifest(
        exact_days_gate3,
        _checked_roots(exact_days_gate3),
    )
    exact_days_sample = freeze_gate3_audit_sample(
        parent_v3,
        exact_days_manifest,
    )
    exact_days = finalize_gate3_performance(
        exact_days_gate3,
        exact_days_manifest,
        exact_days_sample,
        reference_timings=_reference_timings(exact_days_sample, 100.0),
    )
    assert exact_days.projected_held_out_calendar_days <= 5.0
    assert exact_days.performance_decision == "pass_abbreviated_performance"

    over_days_gate3 = _portable_gate3_result(
        total_pipeline_wall_seconds=math.nextafter(exact_days_wall, math.inf)
    )
    over_days_manifest = _freeze_manifest(
        over_days_gate3,
        _checked_roots(over_days_gate3),
    )
    over_days_sample = freeze_gate3_audit_sample(
        parent_v3,
        over_days_manifest,
    )
    over_days = finalize_gate3_performance(
        over_days_gate3,
        over_days_manifest,
        over_days_sample,
        reference_timings=_reference_timings(over_days_sample, 100.0),
    )
    assert over_days.projected_held_out_calendar_days > 5.0
    assert over_days.performance_decision == "hold_performance"


def _complete_evidence(parent_v3, gate3, manifest, sample):  # type: ignore[no-untyped-def]
    audit = finalize_gate3_audit(sample, *_audit_inputs(sample))
    _, _, mutations = _mutation_evidence(parent_v3, gate3, manifest, sample)
    performance = finalize_gate3_performance(
        gate3,
        manifest,
        sample,
        reference_timings=_reference_timings(sample),
    )
    return audit, mutations, performance


def test_outer_decision_authorizes_only_complete_bound_evidence(
    parent_v3,
    gate3,
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    audit, mutations, performance = _complete_evidence(
        parent_v3,
        gate3,
        manifest,
        sample,
    )
    authorized = finalize_gate3_decision(
        parent_v3,
        gate3,
        manifest,
        sample,
        audit,
        mutations,
        performance,
    )
    assert authorized.decision == "authorize_official_six_cell_calibration"
    assert authorized.official_six_cell_calibration_authorized is True
    assert authorized.evaluation_opened is False
    assert authorized.official_six_cell_executed is False
    assert type(authorized.portable_fact_gate3) is experiment.M8PortableFactGate3Result

    held = finalize_gate3_decision(
        parent_v3,
        gate3,
        manifest,
        sample,
        audit,
        mutations,
        None,
    )
    assert held.decision == "hold_performance"

    timings = list(performance.reference_timings)
    different_output = timings[0].computation.model_copy(
        update={"output_content_sha256": _sha("different-reference-output")}
    )
    timings[0] = build_gate3_reference_timing(
        regime=timings[0].regime,
        action_id=timings[0].action_id,
        catalog_action_id=timings[0].catalog_action_id,
        root_fact_sha256=timings[0].root_fact_sha256,
        computation=different_output,
        worker_seconds=timings[0].worker_seconds,
    )
    mismatched_performance = finalize_gate3_performance(
        gate3,
        manifest,
        sample,
        reference_timings=tuple(timings),
    )
    with pytest.raises(ValueError, match="performance differs"):
        finalize_gate3_decision(
            parent_v3,
            gate3,
            manifest,
            sample,
            audit,
            mutations,
            mismatched_performance,
        )


def _rehash_sample(sample: M8Gate3AuditSample, actions):  # type: ignore[no-untyped-def]
    draft = sample.model_copy(
        update={
            "actions": actions,
            "sample_id": "yfm8g3sample-" + "0" * 24,
            "content_sha256": "sha256:" + "0" * 64,
        }
    )
    digest = semantic_sha256(draft, excluded_fields={"sample_id", "content_sha256"})
    forged = draft.model_copy(
        update={
            "sample_id": f"yfm8g3sample-{digest[:24]}",
            "content_sha256": f"sha256:{digest}",
        }
    )
    return M8Gate3AuditSample.model_validate(
        forged,
        strict=True,
    )


def test_decision_refreezes_manifest_and_rejects_coherently_rehashed_higher_rank_substitution(
    parent_v3,
    gate3,
    manifest,
    sample,
) -> None:  # type: ignore[no-untyped-def]
    audit, mutations, performance = _complete_evidence(
        parent_v3,
        gate3,
        manifest,
        sample,
    )
    regime = TemporalRegime.NO_SIGNAL
    selected_ids = {item.catalog_action_id for item in sample.actions if item.regime is regime}
    substitute_root = max(
        (
            item
            for item in manifest.roots
            if item.regime is regime
            and not item.is_baseline
            and item.catalog_action_id not in selected_ids
        ),
        key=lambda item: item.audit_rank_sha256,
    )
    substitute = M8Gate3AuditSampleAction(
        **substitute_root.model_dump(mode="python"),
        selection_rank_sha256=substitute_root.audit_rank_sha256,
    )
    actions = list(sample.actions)
    replacement_index = next(
        index
        for index, item in enumerate(actions)
        if item.regime is regime and not item.is_baseline
    )
    actions[replacement_index] = substitute
    actions.sort(
        key=lambda item: (
            0 if item.regime is TemporalRegime.NO_SIGNAL else 1,
            0 if item.is_baseline else 1,
            item.selection_rank_sha256,
            item.catalog_action_id,
        )
    )
    forged_sample = _rehash_sample(sample, tuple(actions))

    with pytest.raises(ValueError, match="re-freeze"):
        finalize_gate3_decision(
            parent_v3,
            gate3,
            manifest,
            forged_sample,
            audit,
            mutations,
            performance,
        )
