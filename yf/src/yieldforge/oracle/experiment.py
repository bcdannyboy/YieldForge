"""Calibration-only certificate proof and hard gate for exact M8 rollout."""

from __future__ import annotations

import gc
import hashlib
import json
import math
import multiprocessing
import os
import pickle
import secrets
import signal
import stat
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from multiprocessing.connection import Connection, wait
from pathlib import Path
from time import monotonic, perf_counter, perf_counter_ns, sleep
from typing import Literal, Self

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.archives import (
    canonical_m2_archive_references,
    verify_problem_candidates,
)
from yieldforge.baseline.contracts import (
    BaselineContractModel,
    M7CalibrationProblemView,
    TemporalInstanceBinding,
)
from yieldforge.baseline.experiment import M7FrozenBaseline, select_calibration_instances
from yieldforge.baseline.replay import (
    M7ReplayRuntime,
    build_m7_replay_input,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
)
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.oracle.checker import (
    M8CheckedFactBundleResult,
    M8FactBundleCheckRequest,
    M8ProofCheckResult,
    check_action_proofs,
    check_m8_fact_bundle,
)
from yieldforge.oracle.concurrency import (
    M8_GATE3_CONCURRENCY_BUDGET,
    M8ConcurrencyBudget,
    activate_m8_translation_audit_processes,
    current_m8_translation_audit_processes,
)
from yieldforge.oracle.contracts import M8ActionScore
from yieldforge.oracle.factored import (
    M8BundleGenerationTelemetry,
    M8UncheckedBundleRequest,
    score_unchecked_fact_bundle,
)
from yieldforge.oracle.profile_evidence import M8PortableHotspotProfileV2
from yieldforge.oracle.profiling import (
    M8ProfilePhase,
    M8ProfileReport,
    activate_m8_profile,
    increment_profile_count,
    profile_phase,
)
from yieldforge.oracle.proofs import (
    M8ActionProof,
    M8EventClassification,
    m8_suffix_sha256,
)
from yieldforge.oracle.reference import M8OracleRequest, score_reference_action
from yieldforge.oracle.source_attestation import (
    SourceAttestedOperation,
    SourceTreeSnapshot,
    activate_source_attestation,
    capture_source_tree,
    source_tree_implementation_identity,
)
from yieldforge.oracle.sparse import (
    M8CertificateActionResult,
    M8CommonFactDifferentialAudit,
    M8SparseResult,
    audit_m8_common_transition_exactness,
    score_certificate_actions,
    score_sparse_event,
)
from yieldforge.oracle.visibility import FullRealizedVisibility
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import RemnantFitConfig
from yieldforge.temporal_benchmark.contracts import (
    TemporalRegime,
    build_registered_contract,
)

_PREFIX_EVENT_COUNT = 2
_HELD_OUT_ACTION_COUNT = 550_542
_HELD_OUT_MEAN_FUTURE_EVENT_COUNT = 11.5
_PROJECTION_SAFETY_FACTOR = 2.0
_WORKER_COUNT = 8
_DISTRIBUTED_PHASE_TIMEOUT_SECONDS = 1_800.0
_M8_GATE3_MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_M8_GATE3_MAX_WORKER_TASK_BYTES = 192 * 1024 * 1024
_M8_GATE3_MAX_WORKER_RESULT_BYTES = 192 * 1024 * 1024
_M8_GATE3_MAX_RETAINED_BUNDLE_BYTES = 2 * _M8_GATE3_MAX_BUNDLE_BYTES
_M8_GATE3_MAX_CHECKER_TASK_PAYLOAD_BYTES = 2 * _M8_GATE3_MAX_WORKER_TASK_BYTES
_PORTABLE_TIMING_TOLERANCE_SECONDS = 0.000001
_M8_GATE3_PARENT_V3_PROOF_ID = "yfm8proof-b296ba919c07d55ece14c6db"
_M8_GATE3_PARENT_V3_CONTENT_SHA256 = (
    "sha256:b296ba919c07d55ece14c6dbb6ecbce1aa4a24e612dd1a251757e7a3b739739d"
)
_WITNESS_ORDER: tuple[M8EventClassification, ...] = (
    "exact_transition",
    "no_fit",
    "policy_dominated",
    "state_rejoin",
)


def _measure_proof_phase[ResultT](
    operation: Callable[[], ResultT],
) -> tuple[ResultT, float]:
    """Measure one proof phase with cyclic GC suspended and cleanup included."""

    restore_gc = gc.isenabled()
    if restore_gc:
        gc.collect()
        gc.disable()
    started = perf_counter()
    try:
        result = operation()
    finally:
        if restore_gc:
            gc.enable()
            gc.collect()
    return result, perf_counter() - started


M8ActionKind = Literal["standard", "remnant"]


def _classification_tuple(
    values: set[M8EventClassification] | tuple[M8EventClassification, ...],
) -> tuple[M8EventClassification, ...]:
    present = set(values)
    return tuple(item for item in _WITNESS_ORDER if item in present)


def _action_kind(action_id: str) -> M8ActionKind:
    if action_id.startswith("m7-standard:"):
        return "standard"
    if action_id.startswith("m7-remnant:"):
        return "remnant"
    raise ValueError("M8 audit action has an unknown exact catalog kind")


class M8AuditActionBinding(BaselineContractModel):
    """One audit action frozen before any gate timing is observed."""

    model_config = ConfigDict(revalidate_instances="always")

    regime: TemporalRegime
    temporal_seed: StrictInt
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    future_event_count: StrictInt = Field(ge=0)
    semantic_runtime_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    catalog_action_id: StrictStr = Field(pattern=r"^m7-(standard|remnant):.+$")
    action_kind: M8ActionKind
    proof_id: StrictStr = Field(pattern=r"^yfm8ap-[0-9a-f]{24}$")
    witness_classifications: tuple[M8EventClassification, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_canonical_binding(self) -> Self:
        if self.witness_classifications != _classification_tuple(self.witness_classifications):
            raise ValueError("M8 audit witness classifications must be canonical and unique")
        if _action_kind(self.catalog_action_id) != self.action_kind:
            raise ValueError("M8 audit action kind differs from its exact catalog ID")
        return self


def _audit_binding_key(
    binding: M8AuditActionBinding,
) -> tuple[int, str]:
    return (tuple(TemporalRegime).index(binding.regime), binding.catalog_action_id)


def audit_sample_sha256(bindings: tuple[M8AuditActionBinding, ...]) -> str:
    """Hash a deterministic audit set with all of its semantic bindings."""

    ordered = tuple(sorted(bindings, key=_audit_binding_key))
    if len({(item.regime, item.catalog_action_id) for item in ordered}) != len(ordered):
        raise ValueError("M8 audit bindings must identify unique regime actions")
    payload = {
        "schema_version": "yieldforge.m8-audit-sample.v1",
        "bindings": tuple(item.model_dump(mode="json") for item in ordered),
    }
    return f"sha256:{semantic_sha256(payload)}"


class M8CertificateProofCell(BaselineContractModel):
    """Full certificate/checker run plus its frozen sampled-reference audit."""

    model_config = ConfigDict(revalidate_instances="always")

    regime: TemporalRegime
    temporal_seed: StrictInt
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    prefix_event_count: Literal[2] = 2
    future_event_count: StrictInt = Field(ge=0)
    semantic_runtime_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    audit_action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    audit_sample_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    current_action_kinds: tuple[M8ActionKind, ...]
    current_action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    proof_catalog_action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    current_action_count: StrictInt = Field(ge=1)
    checked_action_count: StrictInt = Field(ge=0)
    valid_proof_count: StrictInt = Field(ge=0)
    checker_failure_count: StrictInt = Field(ge=0)
    audit_mismatch_count: StrictInt = Field(ge=0)
    witness_classifications: tuple[M8EventClassification, ...] = Field(min_length=1)
    certified_event_count: StrictInt = Field(ge=0)
    exact_escape_count: StrictInt = Field(ge=0)
    state_rejoin_count: StrictInt = Field(ge=0)
    certificate_elapsed_seconds: StrictFloat = Field(gt=0)
    checker_elapsed_seconds: StrictFloat = Field(gt=0)
    sampled_certificate_elapsed_seconds: StrictFloat = Field(gt=0)
    sampled_checker_elapsed_seconds: StrictFloat = Field(gt=0)
    sampled_checker_failure_count: StrictInt = Field(ge=0)
    sampled_reference_elapsed_seconds: StrictFloat = Field(gt=0)

    @model_validator(mode="after")
    def require_canonical_sets(self) -> Self:
        if self.audit_action_ids != tuple(sorted(set(self.audit_action_ids))):
            raise ValueError("M8 cell audit action IDs must be sorted and unique")
        if self.current_action_kinds != tuple(sorted(set(self.current_action_kinds))):
            raise ValueError("M8 current action kinds must be sorted and unique")
        if (
            self.current_action_ids != tuple(sorted(set(self.current_action_ids)))
            or self.proof_catalog_action_ids != tuple(sorted(set(self.proof_catalog_action_ids)))
            or self.current_action_ids != self.proof_catalog_action_ids
            or len(self.current_action_ids) != self.current_action_count
        ):
            raise ValueError("M8 cell proof action IDs do not cover the current catalog")
        if not set(self.audit_action_ids) <= set(self.current_action_ids):
            raise ValueError("M8 cell audit IDs are absent from the current catalog")
        if self.witness_classifications != _classification_tuple(self.witness_classifications):
            raise ValueError("M8 cell witness classifications must be canonical and unique")
        if (
            self.checked_action_count != self.current_action_count
            or self.valid_proof_count + self.checker_failure_count != self.checked_action_count
        ):
            raise ValueError("M8 cell proof counts do not reconcile")
        if self.sampled_checker_failure_count > len(self.audit_action_ids):
            raise ValueError("M8 sampled checker failures exceed frozen audit actions")
        if self.audit_mismatch_count > len(self.audit_action_ids):
            raise ValueError("M8 audit mismatches exceed frozen audit actions")
        return self


class M8CertificateProofResult(BaselineContractModel):
    """The revised first M8 go/no-go artifact; calibration evidence only."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-certificate-proof.v3"] = (
        "yieldforge.m8-certificate-proof.v3"
    )
    execution_mode: Literal["distributed_exact"] = "distributed_exact"
    proof_id: StrictStr = Field(pattern=r"^yfm8proof-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    m6_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_population_id: StrictStr = Field(pattern=r"^yftp-[0-9a-f]{24}$")
    m6_population_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    problem_index_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    freeze_id: StrictStr = Field(pattern=r"^yfm7freeze-[0-9a-f]{24}$")
    freeze_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calibration_view_id: StrictStr = Field(pattern=r"^yfm7cv-[0-9a-f]{24}$")
    calibration_view_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    proof_cell_count: Literal[6] = 6
    completed_cell_count: Literal[6] = 6
    prefix_event_count: Literal[2] = 2
    configured_worker_count: Literal[8] = 8
    measured_process_count: Literal[6] = 6
    held_out_action_count: Literal[550542] = 550_542
    audit_bindings: tuple[M8AuditActionBinding, ...] = Field(min_length=6)
    audit_sample_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cells: tuple[M8CertificateProofCell, ...] = Field(min_length=6, max_length=6)
    current_action_count: StrictInt = Field(ge=1)
    checked_action_count: StrictInt = Field(ge=0)
    valid_proof_count: StrictInt = Field(ge=0)
    checker_failure_count: StrictInt = Field(ge=0)
    sampled_checker_failure_count: StrictInt = Field(ge=0)
    audit_action_count: StrictInt = Field(ge=1)
    audit_mismatch_count: StrictInt = Field(ge=0)
    witness_classifications: tuple[M8EventClassification, ...] = Field(min_length=1)
    missing_full_run_witness_classifications: tuple[M8EventClassification, ...]
    missing_audit_witness_classifications: tuple[M8EventClassification, ...]
    uncovered_witness_classifications: tuple[M8EventClassification, ...]
    uncovered_action_kinds: tuple[M8ActionKind, ...]
    uncovered_future_event_counts: tuple[StrictInt, ...]
    uncovered_regimes: tuple[TemporalRegime, ...]
    certified_event_count: StrictInt = Field(ge=0)
    exact_escape_count: StrictInt = Field(ge=0)
    state_rejoin_count: StrictInt = Field(ge=0)
    certificate_elapsed_seconds: StrictFloat = Field(gt=0)
    checker_elapsed_seconds: StrictFloat = Field(gt=0)
    certificate_pipeline_elapsed_seconds: StrictFloat = Field(gt=0)
    generator_wall_seconds: StrictFloat = Field(gt=0)
    checker_wall_seconds: StrictFloat = Field(gt=0)
    certificate_pipeline_wall_seconds: StrictFloat = Field(gt=0)
    audit_wall_seconds: StrictFloat = Field(gt=0)
    total_wall_seconds: StrictFloat = Field(gt=0)
    sampled_reference_elapsed_seconds: StrictFloat = Field(gt=0)
    sampled_certificate_elapsed_seconds: StrictFloat = Field(gt=0)
    sampled_checker_elapsed_seconds: StrictFloat = Field(gt=0)
    sampled_certificate_pipeline_elapsed_seconds: StrictFloat = Field(gt=0)
    sampled_speedup: StrictFloat = Field(gt=0)
    full_certificate_actions_per_second: StrictFloat = Field(gt=0)
    projected_held_out_calendar_days: StrictFloat = Field(ge=0)
    evaluation_partition_opened: Literal[False] = False
    technical_decision: Literal[
        "pass_certificate_exact",
        "redesign_certificate_proof",
        "require_action_sharding",
    ]
    claim_ceiling: Literal[
        "calibration_certificate_runtime_and_semantic_proof_only_not_evaluation_advantage_"
        "savings_physical_or_commercial_evidence"
    ] = (
        "calibration_certificate_runtime_and_semantic_proof_only_not_evaluation_advantage_"
        "savings_physical_or_commercial_evidence"
    )

    @model_validator(mode="after")
    def require_complete_gate_and_identity(self) -> Self:
        if tuple(item.regime for item in self.cells) != tuple(TemporalRegime):
            raise ValueError("M8 certificate proof cells differ from all six registered cells")
        _require_audit_reconciliation(self.cells, self.audit_bindings)
        expected_sample_sha = audit_sample_sha256(self.audit_bindings)
        if self.audit_sample_sha256 != expected_sample_sha:
            raise ValueError("M8 certificate audit sample SHA-256 does not reconcile")
        expected_pipeline_wall = round(
            self.generator_wall_seconds + self.checker_wall_seconds,
            6,
        )
        if self.certificate_pipeline_wall_seconds != expected_pipeline_wall:
            raise ValueError("M8 distributed pipeline wall time does not reconcile")
        if self.total_wall_seconds < round(
            self.certificate_pipeline_wall_seconds + self.audit_wall_seconds,
            6,
        ):
            raise ValueError("M8 distributed total wall time does not reconcile")
        aggregates = _aggregate_certificate_metrics(
            self.cells,
            self.audit_bindings,
            certificate_pipeline_wall_seconds=self.certificate_pipeline_wall_seconds,
        )
        for field_name, expected in aggregates.items():
            if getattr(self, field_name) != expected:
                raise ValueError(f"M8 certificate aggregate {field_name} does not reconcile")
        expected_decision = _gate_decision(
            current_action_count=self.current_action_count,
            checked_action_count=self.checked_action_count,
            valid_proof_count=self.valid_proof_count,
            checker_failure_count=self.checker_failure_count,
            sampled_checker_failure_count=self.sampled_checker_failure_count,
            audit_mismatch_count=self.audit_mismatch_count,
            certified_event_count=self.certified_event_count,
            exact_escape_count=self.exact_escape_count,
            state_rejoin_count=self.state_rejoin_count,
            uncovered_witness_classifications=self.uncovered_witness_classifications,
            uncovered_action_kinds=self.uncovered_action_kinds,
            uncovered_future_event_counts=self.uncovered_future_event_counts,
            uncovered_regimes=self.uncovered_regimes,
            speedup=self.sampled_speedup,
            projected_days=self.projected_held_out_calendar_days,
        )
        if self.technical_decision != expected_decision:
            raise ValueError("M8 certificate proof decision differs from the hard gate")
        digest = semantic_sha256(self, excluded_fields={"proof_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M8 certificate proof SHA-256 does not match semantic content")
        if self.proof_id != f"yfm8proof-{digest[:24]}":
            raise ValueError("M8 certificate proof ID does not match semantic content")
        return self


_PORTABLE_TIMING_SEMANTICS = (
    "checker_phase_wall_fields_are_inclusive_and_may_overlap_nested_exact_work;"
    "exact_fallback_is_nested_exclusive;task_and_result_serialization_are_separate;"
    "worker_payload_handoff_is_post_serialization_ipc_and_scheduling;payload_handoff_"
    "and_process_exit_validation_are_summed_per_worker_and_non_additive_to_pipeline_wall"
)


class M8PortableFactPhaseTiming(BaselineContractModel):
    """Observed cell timing with explicit inclusive versus nested semantics."""

    first_generation_worker_wall_seconds: StrictFloat = Field(ge=0.0)
    second_generation_worker_wall_seconds: StrictFloat = Field(ge=0.0)
    producer_bundle_serialization_wall_seconds: StrictFloat = Field(ge=0.0)
    producer_handoff_serialization_wall_seconds: StrictFloat = Field(ge=0.0)
    metadata_reconciliation_wall_seconds: StrictFloat = Field(ge=0.0)
    authority_reconstruction_wall_seconds: StrictFloat = Field(ge=0.0)
    checker_worker_wall_seconds: StrictFloat = Field(ge=0.0)
    checker_strict_load_inclusive_wall_seconds: StrictFloat = Field(ge=0.0)
    common_verification_inclusive_wall_seconds: StrictFloat = Field(ge=0.0)
    action_traversal_inclusive_wall_seconds: StrictFloat = Field(ge=0.0)
    exact_fallback_nested_exclusive_wall_seconds: StrictFloat = Field(ge=0.0)
    capability_cleanup_inclusive_wall_seconds: StrictFloat = Field(ge=0.0)
    timing_semantics: Literal[_PORTABLE_TIMING_SEMANTICS] = _PORTABLE_TIMING_SEMANTICS

    @model_validator(mode="after")
    def require_contained_worker_timings(self) -> Self:
        tolerance = _PORTABLE_TIMING_TOLERANCE_SECONDS
        generation_serialization = (
            self.producer_bundle_serialization_wall_seconds
            + self.producer_handoff_serialization_wall_seconds
        )
        if (
            self.first_generation_worker_wall_seconds
            + self.second_generation_worker_wall_seconds
            + tolerance
            < generation_serialization
        ):
            raise ValueError("M8 portable generation serialization exceeds worker wall time")
        checker_phases = sum(
            (
                self.checker_strict_load_inclusive_wall_seconds,
                self.common_verification_inclusive_wall_seconds,
                self.action_traversal_inclusive_wall_seconds,
                self.capability_cleanup_inclusive_wall_seconds,
            )
        )
        if self.checker_worker_wall_seconds + tolerance < (
            self.metadata_reconciliation_wall_seconds
            + self.authority_reconstruction_wall_seconds
            + checker_phases
        ):
            raise ValueError("M8 portable checker phases exceed worker wall time")
        return self


class M8PortableRegistryEvidence(BaselineContractModel):
    """Named proof that every process-local authority registry is empty."""

    authoritative_proof_runtime: Literal[0] = 0
    legacy_prepared_checker: Literal[0] = 0
    prepared_generator: Literal[0] = 0
    prepared_translation_layout: Literal[0] = 0
    unchecked_prepared_source_guard: Literal[0] = 0
    validated_common: Literal[0] = 0
    fact_checker_registration_token: Literal[0] = 0
    full_traversal_guard: Literal[0] = 0
    translation_audit_processes: None = None


class M8PortableFactGate3Cell(BaselineContractModel):
    """One checked calibration probe; no M8 hypothesis verdict is implied."""

    regime: TemporalRegime
    temporal_seed: Literal[2026082300]
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    event_count: Literal[2]
    first_bundle_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    second_bundle_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    first_semantic_bundle_bytes_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    second_semantic_bundle_bytes_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bundle_repeat_match: Literal[True] = True
    bundle_byte_cap: Literal[134217728] = _M8_GATE3_MAX_BUNDLE_BYTES
    semantic_serialized_bytes: StrictInt = Field(gt=0)
    repeated_semantic_serialized_bytes: StrictInt = Field(gt=0)
    fixed_layer_node_count: StrictInt = Field(gt=0)
    translation_batch_count: StrictInt = Field(ge=0)
    candidate_scalar_fact_count: StrictInt = Field(ge=0)
    frontier_fact_count: StrictInt = Field(ge=0)
    standard_candidate_fact_count: StrictInt = Field(ge=0)
    common_lemma_count: StrictInt = Field(ge=0)
    influence_fact_count: StrictInt = Field(ge=0)
    generated_action_root_count: StrictInt = Field(gt=0)
    checked_common_lemma_count: StrictInt = Field(ge=0)
    checked_influence_fact_count: StrictInt = Field(ge=0)
    checked_action_root_count: StrictInt = Field(gt=0)
    decision_id: StrictStr = Field(pattern=r"^yfm8d-[0-9a-f]{24}$")
    decision_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer_counted_inventory_evidence_row_count: StrictInt = Field(ge=0)
    producer_counted_search_lemma_count: StrictInt = Field(ge=0)
    counted_translation_audit_count: StrictInt = Field(ge=0)
    counted_translation_audit_call_count: StrictInt = Field(ge=0)
    influence_translation_audit_count: StrictInt = Field(ge=0)
    common_exact_fallback_count: Literal[0] = 0
    influence_exact_fallback_count: Literal[0] = 0
    total_exact_fallback_count: Literal[0] = 0
    common_exact_fallback_wall_seconds: StrictFloat = Field(ge=0.0)
    influence_exact_fallback_wall_seconds: StrictFloat = Field(ge=0.0)
    total_exact_fallback_wall_seconds: StrictFloat = Field(ge=0.0)
    check_valid: Literal[True] = True
    failure_code: Literal["valid_action_decision"] = "valid_action_decision"
    timing: M8PortableFactPhaseTiming
    first_generator_registry_state: M8PortableRegistryEvidence
    second_generator_registry_state: M8PortableRegistryEvidence
    checker_registry_state: M8PortableRegistryEvidence
    surviving_registry_count: Literal[0] = 0
    surviving_descendant_count: Literal[0] = 0
    evaluation_accessed: Literal[False] = False

    @model_validator(mode="after")
    def require_reconciled_cell(self) -> Self:
        layer_total = sum(
            (
                self.translation_batch_count,
                self.candidate_scalar_fact_count,
                self.frontier_fact_count,
                self.standard_candidate_fact_count,
                self.common_lemma_count,
                self.influence_fact_count,
                self.generated_action_root_count,
            )
        )
        if (
            self.first_bundle_sha256 != self.second_bundle_sha256
            or self.first_semantic_bundle_bytes_sha256 != self.second_semantic_bundle_bytes_sha256
            or self.semantic_serialized_bytes != self.repeated_semantic_serialized_bytes
            or self.semantic_serialized_bytes > self.bundle_byte_cap
            or self.fixed_layer_node_count != layer_total
            or self.checked_common_lemma_count != self.common_lemma_count
            or self.checked_influence_fact_count != self.influence_fact_count
            or self.checked_action_root_count != self.generated_action_root_count
            or self.counted_translation_audit_count != self.producer_counted_search_lemma_count
            or self.counted_translation_audit_call_count != self.producer_counted_search_lemma_count
        ):
            raise ValueError("M8 portable Gate-3 cell metadata does not reconcile")
        expected_roots = 428 if self.regime is TemporalRegime.NO_SIGNAL else 459
        if (
            self.regime
            not in {
                TemporalRegime.NO_SIGNAL,
                TemporalRegime.REGIME_SHIFT,
            }
            or self.generated_action_root_count != expected_roots
        ):
            raise ValueError("M8 portable Gate-3 cell root count differs from the freeze")
        if self.total_exact_fallback_wall_seconds != (
            self.common_exact_fallback_wall_seconds + self.influence_exact_fallback_wall_seconds
        ):
            raise ValueError("M8 portable Gate-3 fallback timing does not reconcile")
        if self.total_exact_fallback_wall_seconds != 0.0:
            raise ValueError("M8 portable Gate-3 cell performed exact fallback")
        return self


class M8PortableFactGate3Result(BaselineContractModel):
    """Separate calibration-only portable-fact pipeline artifact."""

    schema_version: Literal["yieldforge.m8-gate3-portable-fact-evidence.v1"] = (
        "yieldforge.m8-gate3-portable-fact-evidence.v1"
    )
    execution_mode: Literal["fresh_process_unchecked_bytes_v2"] = "fresh_process_unchecked_bytes_v2"
    parent_official_schema_version: Literal["yieldforge.m8-certificate-proof.v3"] = (
        "yieldforge.m8-certificate-proof.v3"
    )
    parent_v3_proof_id: Literal["yfm8proof-b296ba919c07d55ece14c6db"] = _M8_GATE3_PARENT_V3_PROOF_ID
    parent_v3_content_sha256: Literal[
        "sha256:b296ba919c07d55ece14c6dbb6ecbce1aa4a24e612dd1a251757e7a3b739739d"
    ] = _M8_GATE3_PARENT_V3_CONTENT_SHA256
    gate3_id: StrictStr = Field(pattern=r"^yfm8gate3-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    m6_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_population_id: StrictStr = Field(pattern=r"^yftp-[0-9a-f]{24}$")
    m6_population_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    problem_index_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    freeze_id: StrictStr = Field(pattern=r"^yfm7freeze-[0-9a-f]{24}$")
    freeze_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calibration_view_id: StrictStr = Field(pattern=r"^yfm7cv-[0-9a-f]{24}$")
    calibration_view_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    probe_count: Literal[2] = 2
    cells: tuple[M8PortableFactGate3Cell, M8PortableFactGate3Cell]
    bundle_byte_cap: Literal[134217728] = _M8_GATE3_MAX_BUNDLE_BYTES
    bundle_root_repeat_match: Literal[True] = True
    semantic_serialized_bytes: StrictInt = Field(gt=0)
    fixed_layer_node_count: StrictInt = Field(gt=0)
    generated_action_root_count: Literal[887] = 887
    checked_action_root_count: Literal[887] = 887
    total_exact_fallback_count: Literal[0] = 0
    total_exact_fallback_wall_seconds: StrictFloat = Field(ge=0.0)
    configured_outer_process_count: Literal[4] = 4
    measured_outer_process_count: Literal[2] = 2
    nested_processes_per_outer_worker: Literal[2] = 2
    peak_compute_count: Literal[4] = 4
    compute_slot_cap: Literal[8] = 8
    first_generation_phase_wall_seconds: StrictFloat = Field(gt=0.0)
    second_generation_phase_wall_seconds: StrictFloat = Field(gt=0.0)
    checker_phase_wall_seconds: StrictFloat = Field(gt=0.0)
    task_serialization_wall_seconds: StrictFloat = Field(ge=0.0)
    result_serialization_wall_seconds: StrictFloat = Field(ge=0.0)
    inbound_payload_handoff_wall_seconds: StrictFloat = Field(ge=0.0)
    outbound_payload_handoff_wall_seconds: StrictFloat = Field(ge=0.0)
    worker_payload_handoff_wall_seconds: StrictFloat = Field(ge=0.0)
    process_exit_validation_wall_seconds: StrictFloat = Field(ge=0.0)
    worker_task_payload_bytes: StrictInt = Field(ge=0)
    worker_result_payload_bytes: StrictInt = Field(ge=0)
    per_worker_task_payload_byte_cap: Literal[201326592] = _M8_GATE3_MAX_WORKER_TASK_BYTES
    per_worker_result_payload_byte_cap: Literal[201326592] = _M8_GATE3_MAX_WORKER_RESULT_BYTES
    retained_first_generation_bundle_bytes: StrictInt = Field(ge=0)
    retained_first_generation_bundle_byte_cap: Literal[268435456] = (
        _M8_GATE3_MAX_RETAINED_BUNDLE_BYTES
    )
    checker_task_payload_bytes: StrictInt = Field(ge=0)
    checker_task_payload_byte_cap: Literal[402653184] = _M8_GATE3_MAX_CHECKER_TASK_PAYLOAD_BYTES
    total_pipeline_wall_seconds: StrictFloat = Field(gt=0.0)
    timing_semantics: Literal[_PORTABLE_TIMING_SEMANTICS] = _PORTABLE_TIMING_SEMANTICS
    controller_registry_state_before: M8PortableRegistryEvidence
    controller_registry_state_after: M8PortableRegistryEvidence
    surviving_registry_count: Literal[0] = 0
    surviving_descendant_count: Literal[0] = 0
    evaluation_accessed: Literal[False] = False
    pipeline_decision: Literal["pass_portable_fact_pipeline"] = "pass_portable_fact_pipeline"
    claim_ceiling: Literal[
        "calibration_portable_fact_pipeline_software_evidence_only_not_gate3_"
        "hypothesis_evaluation_advantage_savings_physical_or_commercial_evidence"
    ] = (
        "calibration_portable_fact_pipeline_software_evidence_only_not_gate3_"
        "hypothesis_evaluation_advantage_savings_physical_or_commercial_evidence"
    )

    @model_validator(mode="after")
    def require_reconciled_artifact(self) -> Self:
        if tuple(item.regime for item in self.cells) != (
            TemporalRegime.NO_SIGNAL,
            TemporalRegime.REGIME_SHIFT,
        ) or any(item.temporal_seed != 2026082300 or item.event_count != 2 for item in self.cells):
            raise ValueError("M8 portable Gate-3 probes differ from the frozen selector")
        if (
            self.semantic_serialized_bytes
            != sum(item.semantic_serialized_bytes for item in self.cells)
            or self.fixed_layer_node_count
            != sum(item.fixed_layer_node_count for item in self.cells)
            or self.generated_action_root_count
            != sum(item.generated_action_root_count for item in self.cells)
            or self.checked_action_root_count
            != sum(item.checked_action_root_count for item in self.cells)
            or self.generated_action_root_count != self.checked_action_root_count
            or self.total_exact_fallback_wall_seconds
            != sum(item.total_exact_fallback_wall_seconds for item in self.cells)
        ):
            raise ValueError("M8 portable Gate-3 aggregates do not reconcile")
        if self.peak_compute_count > self.compute_slot_cap:
            raise ValueError("M8 portable Gate-3 exceeds the compute-slot cap")
        if (
            abs(
                self.worker_payload_handoff_wall_seconds
                - (
                    self.inbound_payload_handoff_wall_seconds
                    + self.outbound_payload_handoff_wall_seconds
                )
            )
            > _PORTABLE_TIMING_TOLERANCE_SECONDS
        ):
            raise ValueError("M8 portable Gate-3 payload handoff timing does not reconcile")
        if (
            self.retained_first_generation_bundle_bytes != self.semantic_serialized_bytes
            or self.retained_first_generation_bundle_bytes
            > self.retained_first_generation_bundle_byte_cap
            or self.checker_task_payload_bytes > self.checker_task_payload_byte_cap
        ):
            raise ValueError("M8 portable Gate-3 retained transport bytes exceed the cap")
        tolerance = _PORTABLE_TIMING_TOLERANCE_SECONDS
        if self.first_generation_phase_wall_seconds + tolerance < max(
            item.timing.first_generation_worker_wall_seconds for item in self.cells
        ):
            raise ValueError("M8 portable first generation phase excludes worker time")
        if self.second_generation_phase_wall_seconds + tolerance < max(
            item.timing.second_generation_worker_wall_seconds for item in self.cells
        ):
            raise ValueError("M8 portable second generation phase excludes worker time")
        if self.checker_phase_wall_seconds + tolerance < max(
            item.timing.checker_worker_wall_seconds for item in self.cells
        ):
            raise ValueError("M8 portable checker phase excludes worker time")
        phase_sum = (
            self.first_generation_phase_wall_seconds
            + self.second_generation_phase_wall_seconds
            + self.checker_phase_wall_seconds
        )
        if self.total_pipeline_wall_seconds + tolerance < phase_sum:
            raise ValueError("M8 portable Gate-3 pipeline wall time is incomplete")
        digest = semantic_sha256(self, excluded_fields={"gate3_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}" or self.gate3_id != (
            f"yfm8gate3-{digest[:24]}"
        ):
            raise ValueError("M8 portable Gate-3 content identity differs")
        return self


def _audit_by_cell(
    audit_bindings: tuple[M8AuditActionBinding, ...],
) -> dict[TemporalRegime, tuple[M8AuditActionBinding, ...]]:
    return {
        regime: tuple(
            sorted(
                (item for item in audit_bindings if item.regime is regime),
                key=_audit_binding_key,
            )
        )
        for regime in TemporalRegime
    }


def _require_audit_reconciliation(
    cells: tuple[M8CertificateProofCell, ...],
    audit_bindings: tuple[M8AuditActionBinding, ...],
) -> None:
    ordered = tuple(sorted(audit_bindings, key=_audit_binding_key))
    if audit_bindings != ordered:
        raise ValueError("M8 certificate audit bindings must be in deterministic order")
    by_cell = _audit_by_cell(ordered)
    for cell in cells:
        bindings = by_cell[cell.regime]
        if (
            tuple(item.catalog_action_id for item in bindings) != cell.audit_action_ids
            or audit_sample_sha256(bindings) != cell.audit_sample_sha256
        ):
            raise ValueError("M8 certificate cell audit sample does not reconcile")
        if any(
            (
                item.temporal_seed,
                item.stream_id,
                item.future_event_count,
                item.semantic_runtime_sha256,
            )
            != (
                cell.temporal_seed,
                cell.stream_id,
                cell.future_event_count,
                cell.semantic_runtime_sha256,
            )
            for item in bindings
        ):
            raise ValueError("M8 certificate audit binding differs from its proof cell")


def _coverage_gaps(
    cells: tuple[M8CertificateProofCell, ...],
    audit_bindings: tuple[M8AuditActionBinding, ...],
) -> tuple[
    tuple[M8EventClassification, ...],
    tuple[M8EventClassification, ...],
    tuple[M8EventClassification, ...],
    tuple[M8ActionKind, ...],
    tuple[int, ...],
    tuple[TemporalRegime, ...],
]:
    by_cell = _audit_by_cell(audit_bindings)
    per_cell_missing_witnesses: set[M8EventClassification] = set()
    missing_kinds: set[M8ActionKind] = set()
    for cell in cells:
        bindings = by_cell[cell.regime]
        audited_witnesses = {
            classification for item in bindings for classification in item.witness_classifications
        }
        audited_kinds = {item.action_kind for item in bindings}
        per_cell_missing_witnesses.update(set(cell.witness_classifications) - audited_witnesses)
        missing_kinds.update(set(cell.current_action_kinds) - audited_kinds)
    present_horizons = sorted({item.future_event_count for item in cells})
    if present_horizons:
        horizon_targets = {
            present_horizons[0],
            present_horizons[len(present_horizons) // 2],
            present_horizons[-1],
        }
    else:
        horizon_targets = set()
    audited_horizons = {item.future_event_count for item in audit_bindings}
    missing_horizons = tuple(sorted(horizon_targets - audited_horizons))
    audited_regimes = {item.regime for item in audit_bindings}
    missing_regimes = tuple(item for item in TemporalRegime if item not in audited_regimes)
    required_witnesses = set(_WITNESS_ORDER)
    full_witnesses = {
        classification for cell in cells for classification in cell.witness_classifications
    }
    audit_witnesses = {
        classification
        for binding in audit_bindings
        for classification in binding.witness_classifications
    }
    missing_full = required_witnesses - full_witnesses
    missing_audit = required_witnesses - audit_witnesses
    return (
        _classification_tuple(per_cell_missing_witnesses | missing_full | missing_audit),
        _classification_tuple(missing_full),
        _classification_tuple(missing_audit),
        tuple(sorted(missing_kinds)),
        missing_horizons,
        missing_regimes,
    )


def _aggregate_certificate_metrics(
    cells: tuple[M8CertificateProofCell, ...],
    audit_bindings: tuple[M8AuditActionBinding, ...],
    *,
    certificate_pipeline_wall_seconds: float,
) -> dict[str, object]:
    current = sum(item.current_action_count for item in cells)
    checked = sum(item.checked_action_count for item in cells)
    valid = sum(item.valid_proof_count for item in cells)
    failures = sum(item.checker_failure_count for item in cells)
    sampled_failures = sum(item.sampled_checker_failure_count for item in cells)
    mismatches = sum(item.audit_mismatch_count for item in cells)
    certificate_seconds = round(sum(item.certificate_elapsed_seconds for item in cells), 6)
    checker_seconds = round(sum(item.checker_elapsed_seconds for item in cells), 6)
    pipeline_seconds = round(certificate_seconds + checker_seconds, 6)
    reference_seconds = round(sum(item.sampled_reference_elapsed_seconds for item in cells), 6)
    sampled_certificate_seconds = round(
        sum(item.sampled_certificate_elapsed_seconds for item in cells), 6
    )
    sampled_checker_seconds = round(sum(item.sampled_checker_elapsed_seconds for item in cells), 6)
    sampled_pipeline_seconds = round(sampled_certificate_seconds + sampled_checker_seconds, 6)
    speedup = round(reference_seconds / sampled_pipeline_seconds, 6)
    throughput = round(current / certificate_pipeline_wall_seconds, 6)
    observed_action_events = sum(
        item.current_action_count * max(1, item.future_event_count) for item in cells
    )
    projected_seconds = (
        certificate_pipeline_wall_seconds
        / observed_action_events
        * _HELD_OUT_ACTION_COUNT
        * _HELD_OUT_MEAN_FUTURE_EVENT_COUNT
        * _PROJECTION_SAFETY_FACTOR
    )
    projected_days = round(projected_seconds / 86_400.0, 6)
    witness_classifications = _classification_tuple(
        {classification for item in cells for classification in item.witness_classifications}
    )
    certified_event_count = sum(item.certified_event_count for item in cells)
    exact_escape_count = sum(item.exact_escape_count for item in cells)
    state_rejoin_count = sum(item.state_rejoin_count for item in cells)
    gaps = _coverage_gaps(cells, audit_bindings)
    return {
        "current_action_count": current,
        "checked_action_count": checked,
        "valid_proof_count": valid,
        "checker_failure_count": failures,
        "sampled_checker_failure_count": sampled_failures,
        "audit_action_count": len(audit_bindings),
        "audit_mismatch_count": mismatches,
        "witness_classifications": witness_classifications,
        "missing_full_run_witness_classifications": gaps[1],
        "missing_audit_witness_classifications": gaps[2],
        "uncovered_witness_classifications": gaps[0],
        "uncovered_action_kinds": gaps[3],
        "uncovered_future_event_counts": gaps[4],
        "uncovered_regimes": gaps[5],
        "certified_event_count": certified_event_count,
        "exact_escape_count": exact_escape_count,
        "state_rejoin_count": state_rejoin_count,
        "certificate_elapsed_seconds": certificate_seconds,
        "checker_elapsed_seconds": checker_seconds,
        "certificate_pipeline_elapsed_seconds": pipeline_seconds,
        "sampled_reference_elapsed_seconds": reference_seconds,
        "sampled_certificate_elapsed_seconds": sampled_certificate_seconds,
        "sampled_checker_elapsed_seconds": sampled_checker_seconds,
        "sampled_certificate_pipeline_elapsed_seconds": sampled_pipeline_seconds,
        "sampled_speedup": speedup,
        "full_certificate_actions_per_second": throughput,
        "projected_held_out_calendar_days": projected_days,
    }


def _gate_decision(
    *,
    current_action_count: int,
    checked_action_count: int,
    valid_proof_count: int,
    checker_failure_count: int,
    sampled_checker_failure_count: int,
    audit_mismatch_count: int,
    certified_event_count: int,
    exact_escape_count: int,
    state_rejoin_count: int,
    uncovered_witness_classifications: tuple[M8EventClassification, ...],
    uncovered_action_kinds: tuple[M8ActionKind, ...],
    uncovered_future_event_counts: tuple[int, ...],
    uncovered_regimes: tuple[TemporalRegime, ...],
    speedup: float,
    projected_days: float,
) -> str:
    if (
        checked_action_count != current_action_count
        or valid_proof_count != current_action_count
        or checker_failure_count
        or sampled_checker_failure_count
        or audit_mismatch_count
        or not certified_event_count
        or not exact_escape_count
        or not state_rejoin_count
        or uncovered_witness_classifications
        or uncovered_action_kinds
        or uncovered_future_event_counts
        or uncovered_regimes
        or speedup < 20.0
    ):
        return "redesign_certificate_proof"
    if projected_days > 7.0:
        return "require_action_sharding"
    return "pass_certificate_exact"


def finalize_certificate_proof(
    *,
    m0_contract_id: str,
    m0_contract_sha256: str,
    m6_contract_id: str,
    m6_contract_sha256: str,
    m6_population_id: str,
    m6_population_sha256: str,
    problem_index_id: str,
    problem_index_sha256: str,
    freeze_id: str,
    freeze_sha256: str,
    calibration_view_id: str,
    calibration_view_sha256: str,
    cells: tuple[M8CertificateProofCell, ...],
    audit_bindings: tuple[M8AuditActionBinding, ...],
    measured_process_count: int,
    generator_wall_seconds: float,
    checker_wall_seconds: float,
    audit_wall_seconds: float,
    total_wall_seconds: float,
) -> M8CertificateProofResult:
    """Reconcile the six calibration cells and apply the revised hard gate."""

    ordered_cells = tuple(sorted(cells, key=lambda item: tuple(TemporalRegime).index(item.regime)))
    if len(ordered_cells) != 6 or tuple(item.regime for item in ordered_cells) != tuple(
        TemporalRegime
    ):
        raise ValueError("M8 certificate proof requires all six registered cells")
    ordered_bindings = tuple(sorted(audit_bindings, key=_audit_binding_key))
    _require_audit_reconciliation(ordered_cells, ordered_bindings)
    pipeline_wall_seconds = round(
        generator_wall_seconds + checker_wall_seconds,
        6,
    )
    aggregates = _aggregate_certificate_metrics(
        ordered_cells,
        ordered_bindings,
        certificate_pipeline_wall_seconds=pipeline_wall_seconds,
    )
    decision = _gate_decision(
        current_action_count=int(aggregates["current_action_count"]),
        checked_action_count=int(aggregates["checked_action_count"]),
        valid_proof_count=int(aggregates["valid_proof_count"]),
        checker_failure_count=int(aggregates["checker_failure_count"]),
        sampled_checker_failure_count=int(aggregates["sampled_checker_failure_count"]),
        audit_mismatch_count=int(aggregates["audit_mismatch_count"]),
        certified_event_count=int(aggregates["certified_event_count"]),
        exact_escape_count=int(aggregates["exact_escape_count"]),
        state_rejoin_count=int(aggregates["state_rejoin_count"]),
        uncovered_witness_classifications=aggregates["uncovered_witness_classifications"],  # type: ignore[arg-type]
        uncovered_action_kinds=aggregates["uncovered_action_kinds"],  # type: ignore[arg-type]
        uncovered_future_event_counts=aggregates["uncovered_future_event_counts"],  # type: ignore[arg-type]
        uncovered_regimes=aggregates["uncovered_regimes"],  # type: ignore[arg-type]
        speedup=float(aggregates["sampled_speedup"]),
        projected_days=float(aggregates["projected_held_out_calendar_days"]),
    )
    semantic = {
        "schema_version": "yieldforge.m8-certificate-proof.v3",
        "execution_mode": "distributed_exact",
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "m6_contract_id": m6_contract_id,
        "m6_contract_sha256": m6_contract_sha256,
        "m6_population_id": m6_population_id,
        "m6_population_sha256": m6_population_sha256,
        "problem_index_id": problem_index_id,
        "problem_index_sha256": problem_index_sha256,
        "freeze_id": freeze_id,
        "freeze_sha256": freeze_sha256,
        "calibration_view_id": calibration_view_id,
        "calibration_view_sha256": calibration_view_sha256,
        "proof_cell_count": 6,
        "completed_cell_count": 6,
        "prefix_event_count": _PREFIX_EVENT_COUNT,
        "configured_worker_count": _WORKER_COUNT,
        "measured_process_count": measured_process_count,
        "held_out_action_count": _HELD_OUT_ACTION_COUNT,
        "audit_bindings": [item.model_dump(mode="json") for item in ordered_bindings],
        "audit_sample_sha256": audit_sample_sha256(ordered_bindings),
        "cells": [item.model_dump(mode="json") for item in ordered_cells],
        **aggregates,
        "generator_wall_seconds": round(generator_wall_seconds, 6),
        "checker_wall_seconds": round(checker_wall_seconds, 6),
        "certificate_pipeline_wall_seconds": pipeline_wall_seconds,
        "audit_wall_seconds": round(audit_wall_seconds, 6),
        "total_wall_seconds": round(total_wall_seconds, 6),
        "evaluation_partition_opened": False,
        "technical_decision": decision,
        "claim_ceiling": (
            "calibration_certificate_runtime_and_semantic_proof_only_not_evaluation_advantage_"
            "savings_physical_or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    validated = dict(semantic)
    validated["audit_bindings"] = ordered_bindings
    validated["cells"] = ordered_cells
    return M8CertificateProofResult(
        proof_id=f"yfm8proof-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **validated,
    )


def _profile_phase_wall_seconds(report: M8ProfileReport, name: str) -> float:
    total_ns = 0

    def visit(phases) -> None:  # type: ignore[no-untyped-def]
        nonlocal total_ns
        for phase in phases:
            if phase.name == name:
                total_ns += phase.wall_ns
            visit(phase.children)

    visit(report.phases)
    return total_ns / 1_000_000_000.0


def _portable_registry_evidence(
    state: _PortableRegistryState,
) -> M8PortableRegistryEvidence:
    if not state.is_clean:
        raise ValueError("M8 portable registry evidence is not clean")
    return M8PortableRegistryEvidence.model_validate(
        {
            name: getattr(state, name)
            for name in M8PortableRegistryEvidence.model_fields
        },
        strict=True,
    )


def _strict_portable_pipeline_execution(
    value: object,
) -> _PortableFactPipelineExecution:
    """Reconstruct every untrusted worker payload before finalization."""

    if type(value) is not _PortableFactPipelineExecution or type(value.cells) is not tuple:
        raise TypeError("M8 portable pipeline has an unexpected envelope")
    cells = []
    for cell in value.cells:
        if type(cell) is not _PortableFactCellExecution:
            raise TypeError("M8 portable pipeline cell has an unexpected envelope")
        first = _strict_portable_bundle_identity(
            cell.first_generation,
            require_bytes=False,
        )
        second = _strict_portable_bundle_identity(
            cell.second_generation,
            require_bytes=False,
        )
        checked = _strict_portable_check_worker_result(cell.check)
        first, checked = _reconcile_portable_fact_handoff(first, checked)
        if _portable_generation_metadata(first) != _portable_generation_metadata(second):
            raise ValueError("M8 portable repeated generation identity differs")
        if len({first.worker_pid, second.worker_pid, checked.worker_pid}) != 3:
            raise ValueError("M8 portable phases did not use distinct fresh workers")
        cells.append(
            _PortableFactCellExecution(
                first_generation=first,
                second_generation=second,
                check=checked,
            )
        )
    payload = dict(vars(value))
    payload["cells"] = tuple(cells)
    for field_name in (
        "first_generation_phase_wall_seconds",
        "second_generation_phase_wall_seconds",
        "checker_phase_wall_seconds",
        "task_serialization_wall_seconds",
        "result_serialization_wall_seconds",
        "inbound_payload_handoff_wall_seconds",
        "outbound_payload_handoff_wall_seconds",
        "worker_payload_handoff_wall_seconds",
        "process_exit_validation_wall_seconds",
        "total_pipeline_wall_seconds",
    ):
        payload[field_name] = _require_exact_nonnegative_float(
            payload[field_name],
            field_name=field_name,
        )
    for field_name in (
        "worker_task_payload_bytes",
        "worker_result_payload_bytes",
        "per_worker_task_payload_byte_cap",
        "per_worker_result_payload_byte_cap",
        "retained_first_generation_bundle_bytes",
        "retained_first_generation_bundle_byte_cap",
        "checker_task_payload_bytes",
        "checker_task_payload_byte_cap",
        "outer_process_count",
        "nested_process_count",
        "peak_compute_count",
    ):
        payload[field_name] = _require_exact_nonnegative_int(
            payload[field_name],
            field_name=field_name,
        )
    payload["controller_registry_state_before"] = _strict_portable_registry_state(
        value.controller_registry_state_before
    )
    payload["controller_registry_state_after"] = _strict_portable_registry_state(
        value.controller_registry_state_after
    )
    strict = _PortableFactPipelineExecution(**payload)
    if (
        not strict.controller_registry_state_before.is_clean
        or not strict.controller_registry_state_after.is_clean
        or abs(
            strict.worker_payload_handoff_wall_seconds
            - (
                strict.inbound_payload_handoff_wall_seconds
                + strict.outbound_payload_handoff_wall_seconds
            )
        )
        > _PORTABLE_TIMING_TOLERANCE_SECONDS
        or strict.retained_first_generation_bundle_bytes
        > strict.retained_first_generation_bundle_byte_cap
        or strict.checker_task_payload_bytes > strict.checker_task_payload_byte_cap
    ):
        raise ValueError("M8 portable pipeline operational evidence does not reconcile")
    return strict


def finalize_portable_fact_gate3(
    *,
    m0_contract_id: str,
    m0_contract_sha256: str,
    m6_contract_id: str,
    m6_contract_sha256: str,
    m6_population_id: str,
    m6_population_sha256: str,
    problem_index_id: str,
    problem_index_sha256: str,
    freeze_id: str,
    freeze_sha256: str,
    calibration_view_id: str,
    calibration_view_sha256: str,
    pipeline: _PortableFactPipelineExecution,
) -> M8PortableFactGate3Result:
    """Build the separate two-probe portable-fact pipeline artifact."""

    pipeline = _strict_portable_pipeline_execution(pipeline)
    if len(pipeline.cells) != 2:
        raise ValueError("M8 portable Gate-3 requires exactly two completed probes")
    parent_registry_state = _strict_portable_registry_state(_portable_registry_state())
    if not parent_registry_state.is_clean:
        raise ValueError("M8 portable Gate-3 parent process has live registries")
    cells = []
    for execution in pipeline.cells:
        first = execution.first_generation
        second = execution.second_generation
        checked = execution.check
        if _portable_generation_metadata(first) != _portable_generation_metadata(second):
            raise ValueError("M8 portable Gate-3 repeated bytes differ")
        _reconcile_portable_fact_handoff(first, checked)
        result = checked.check
        if result.decision is None:
            raise ValueError("M8 portable Gate-3 checker omitted its decision identity")
        timing = M8PortableFactPhaseTiming(
            first_generation_worker_wall_seconds=float(first.generation_wall_seconds),
            second_generation_worker_wall_seconds=float(second.generation_wall_seconds),
            producer_bundle_serialization_wall_seconds=float(
                first.telemetry.serialization_seconds + second.telemetry.serialization_seconds
            ),
            producer_handoff_serialization_wall_seconds=float(
                first.handoff_serialization_wall_seconds + second.handoff_serialization_wall_seconds
            ),
            metadata_reconciliation_wall_seconds=float(
                checked.metadata_reconciliation_wall_seconds
            ),
            authority_reconstruction_wall_seconds=float(
                checked.authority_reconstruction_wall_seconds
            ),
            checker_worker_wall_seconds=float(checked.checker_wall_seconds),
            checker_strict_load_inclusive_wall_seconds=_profile_phase_wall_seconds(
                checked.profile,
                "fact_bundle_strict_load",
            ),
            common_verification_inclusive_wall_seconds=_profile_phase_wall_seconds(
                checked.profile,
                "fact_bundle_common_verification",
            ),
            action_traversal_inclusive_wall_seconds=_profile_phase_wall_seconds(
                checked.profile,
                "fact_bundle_action_traversal",
            ),
            exact_fallback_nested_exclusive_wall_seconds=float(
                result.total_exact_fallback_wall_seconds
            ),
            capability_cleanup_inclusive_wall_seconds=_profile_phase_wall_seconds(
                checked.profile,
                "fact_bundle_cleanup",
            ),
        )
        cells.append(
            M8PortableFactGate3Cell(
                regime=first.regime,
                temporal_seed=first.temporal_seed,
                stream_id=first.stream_id,
                event_count=first.event_count,
                first_bundle_sha256=first.bundle_sha256,
                second_bundle_sha256=second.bundle_sha256,
                first_semantic_bundle_bytes_sha256=(first.semantic_bundle_bytes_sha256),
                second_semantic_bundle_bytes_sha256=(second.semantic_bundle_bytes_sha256),
                semantic_serialized_bytes=first.semantic_serialized_bytes,
                repeated_semantic_serialized_bytes=second.semantic_serialized_bytes,
                fixed_layer_node_count=first.fixed_layer_node_count,
                translation_batch_count=first.translation_batch_count,
                candidate_scalar_fact_count=first.candidate_scalar_fact_count,
                frontier_fact_count=first.frontier_fact_count,
                standard_candidate_fact_count=first.standard_candidate_fact_count,
                common_lemma_count=first.common_lemma_count,
                influence_fact_count=first.influence_fact_count,
                generated_action_root_count=first.action_root_count,
                checked_common_lemma_count=result.checked_common_lemma_count,
                checked_influence_fact_count=result.checked_influence_fact_count,
                checked_action_root_count=result.checked_action_root_count,
                decision_id=result.decision.decision_id,
                decision_content_sha256=result.decision.content_sha256,
                producer_counted_inventory_evidence_row_count=(
                    first.telemetry.counted_inventory_evidence_count
                ),
                producer_counted_search_lemma_count=(first.counted_search_lemma_count),
                counted_translation_audit_count=result.counted_translation_audit_count,
                counted_translation_audit_call_count=(checked.counted_translation_audit_call_count),
                influence_translation_audit_count=(result.influence_translation_audit_count),
                common_exact_fallback_count=result.common_exact_fallback_count,
                influence_exact_fallback_count=result.influence_exact_fallback_count,
                total_exact_fallback_count=result.total_exact_fallback_count,
                common_exact_fallback_wall_seconds=float(result.common_exact_fallback_wall_seconds),
                influence_exact_fallback_wall_seconds=float(
                    result.influence_exact_fallback_wall_seconds
                ),
                total_exact_fallback_wall_seconds=float(result.total_exact_fallback_wall_seconds),
                check_valid=result.valid,
                failure_code=result.failure_code,
                timing=timing,
                first_generator_registry_state=_portable_registry_evidence(
                    first.registry_state_after
                ),
                second_generator_registry_state=_portable_registry_evidence(
                    second.registry_state_after
                ),
                checker_registry_state=_portable_registry_evidence(checked.registry_state_after),
            )
        )
    ordered_cells = tuple(
        sorted(
            cells,
            key=lambda item: (0 if item.regime is TemporalRegime.NO_SIGNAL else 1,),
        )
    )
    if len(ordered_cells) != 2:
        raise ValueError("M8 portable Gate-3 cell count differs")
    semantic = {
        "schema_version": "yieldforge.m8-gate3-portable-fact-evidence.v1",
        "execution_mode": "fresh_process_unchecked_bytes_v2",
        "parent_official_schema_version": "yieldforge.m8-certificate-proof.v3",
        "parent_v3_proof_id": _M8_GATE3_PARENT_V3_PROOF_ID,
        "parent_v3_content_sha256": _M8_GATE3_PARENT_V3_CONTENT_SHA256,
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "m6_contract_id": m6_contract_id,
        "m6_contract_sha256": m6_contract_sha256,
        "m6_population_id": m6_population_id,
        "m6_population_sha256": m6_population_sha256,
        "problem_index_id": problem_index_id,
        "problem_index_sha256": problem_index_sha256,
        "freeze_id": freeze_id,
        "freeze_sha256": freeze_sha256,
        "calibration_view_id": calibration_view_id,
        "calibration_view_sha256": calibration_view_sha256,
        "probe_count": 2,
        "cells": tuple(item.model_dump(mode="json") for item in ordered_cells),
        "bundle_byte_cap": _M8_GATE3_MAX_BUNDLE_BYTES,
        "bundle_root_repeat_match": True,
        "semantic_serialized_bytes": sum(item.semantic_serialized_bytes for item in ordered_cells),
        "fixed_layer_node_count": sum(item.fixed_layer_node_count for item in ordered_cells),
        "generated_action_root_count": sum(
            item.generated_action_root_count for item in ordered_cells
        ),
        "checked_action_root_count": sum(item.checked_action_root_count for item in ordered_cells),
        "total_exact_fallback_count": 0,
        "total_exact_fallback_wall_seconds": float(
            sum(item.total_exact_fallback_wall_seconds for item in ordered_cells)
        ),
        "configured_outer_process_count": 4,
        "measured_outer_process_count": pipeline.outer_process_count,
        "nested_processes_per_outer_worker": pipeline.nested_process_count,
        "peak_compute_count": pipeline.peak_compute_count,
        "compute_slot_cap": 8,
        "first_generation_phase_wall_seconds": float(pipeline.first_generation_phase_wall_seconds),
        "second_generation_phase_wall_seconds": float(
            pipeline.second_generation_phase_wall_seconds
        ),
        "checker_phase_wall_seconds": float(pipeline.checker_phase_wall_seconds),
        "task_serialization_wall_seconds": float(pipeline.task_serialization_wall_seconds),
        "result_serialization_wall_seconds": float(pipeline.result_serialization_wall_seconds),
        "inbound_payload_handoff_wall_seconds": float(
            pipeline.inbound_payload_handoff_wall_seconds
        ),
        "outbound_payload_handoff_wall_seconds": float(
            pipeline.outbound_payload_handoff_wall_seconds
        ),
        "worker_payload_handoff_wall_seconds": float(pipeline.worker_payload_handoff_wall_seconds),
        "process_exit_validation_wall_seconds": float(
            pipeline.process_exit_validation_wall_seconds
        ),
        "worker_task_payload_bytes": pipeline.worker_task_payload_bytes,
        "worker_result_payload_bytes": pipeline.worker_result_payload_bytes,
        "per_worker_task_payload_byte_cap": pipeline.per_worker_task_payload_byte_cap,
        "per_worker_result_payload_byte_cap": pipeline.per_worker_result_payload_byte_cap,
        "retained_first_generation_bundle_bytes": (pipeline.retained_first_generation_bundle_bytes),
        "retained_first_generation_bundle_byte_cap": (
            pipeline.retained_first_generation_bundle_byte_cap
        ),
        "checker_task_payload_bytes": pipeline.checker_task_payload_bytes,
        "checker_task_payload_byte_cap": pipeline.checker_task_payload_byte_cap,
        "total_pipeline_wall_seconds": float(pipeline.total_pipeline_wall_seconds),
        "timing_semantics": _PORTABLE_TIMING_SEMANTICS,
        "controller_registry_state_before": _portable_registry_evidence(
            pipeline.controller_registry_state_before
        ).model_dump(mode="json"),
        "controller_registry_state_after": _portable_registry_evidence(
            pipeline.controller_registry_state_after
        ).model_dump(mode="json"),
        "surviving_registry_count": 0,
        "surviving_descendant_count": 0,
        "evaluation_accessed": False,
        "pipeline_decision": "pass_portable_fact_pipeline",
        "claim_ceiling": (
            "calibration_portable_fact_pipeline_software_evidence_only_not_gate3_"
            "hypothesis_evaluation_advantage_savings_physical_or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8PortableFactGate3Result(
        gate3_id=f"yfm8gate3-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **{
            **semantic,
            "cells": ordered_cells,
            "controller_registry_state_before": _portable_registry_evidence(
                pipeline.controller_registry_state_before
            ),
            "controller_registry_state_after": _portable_registry_evidence(
                pipeline.controller_registry_state_after
            ),
        },
    )


def _runtime(  # type: ignore[no-untyped-def]
    replay_input,
    verified,
    rules,
    jagua_executable,
) -> M7ReplayRuntime:
    return M7ReplayRuntime(
        replay_input=replay_input,
        runtime_candidates=verified,
        rules=rules,
        standard_profile_executor=None,
        jagua_executable=jagua_executable,
    )


@dataclass(frozen=True)
class _ExecutionCell:
    stream: tuple[TemporalInstanceBinding, ...]
    problem_ids: tuple[str, ...]
    replay_input: object
    verified: dict[str, object]


@dataclass(frozen=True)
class _SparsePreflightResult:
    cell: _ExecutionCell
    sparse: M8SparseResult
    elapsed_seconds: float

    @property
    def regime(self) -> TemporalRegime:
        return self.cell.stream[0].regime


@dataclass(frozen=True)
class _FullCheckerResult:
    regime: TemporalRegime
    checks: tuple[M8ProofCheckResult, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class _PortableRegistryState:
    """Named process-local authority state captured outside all worker scopes."""

    authoritative_proof_runtime: int = 0
    materialized_standard_action: int = 0
    legacy_prepared_checker: int = 0
    prepared_generator: int = 0
    prepared_translation_layout: int = 0
    unchecked_prepared_source_guard: int = 0
    validated_common: int = 0
    fact_checker_registration_token: int = 0
    full_traversal_guard: int = 0
    translation_audit_processes: int | None = None

    @property
    def is_clean(self) -> bool:
        return self.translation_audit_processes is None and all(
            value == 0
            for name, value in vars(self).items()
            if name != "translation_audit_processes"
        )


@dataclass(frozen=True)
class _PortableBundleIdentityWorkerResult:
    """Small repeat-generation identity; no unchecked semantic bytes escape."""

    regime: TemporalRegime
    temporal_seed: int
    stream_id: str
    event_count: int
    worker_pid: int
    semantic_bundle_bytes_sha256: str
    bundle_sha256: str
    telemetry: M8BundleGenerationTelemetry
    fixed_layer_node_count: int
    translation_batch_count: int
    candidate_scalar_fact_count: int
    frontier_fact_count: int
    standard_candidate_fact_count: int
    common_lemma_count: int
    counted_search_lemma_count: int
    influence_fact_count: int
    action_root_count: int
    generation_wall_seconds: float
    handoff_serialization_wall_seconds: float
    registry_state_after: _PortableRegistryState

    @property
    def semantic_serialized_bytes(self) -> int:
        return self.telemetry.semantic_serialized_bytes


@dataclass(frozen=True)
class _PortableBundleWorkerResult(_PortableBundleIdentityWorkerResult):
    """First-generation exact unchecked bytes plus untrusted producer telemetry."""

    semantic_bundle_bytes: bytes


@dataclass(frozen=True)
class _PortableGenerationProfileWorkerResult:
    """One fresh unchecked generation plus its non-semantic phase profile."""

    generation: _PortableBundleWorkerResult
    profile: M8ProfileReport
    runtime_id: str
    runtime_content_sha256: str


@dataclass(frozen=True)
class _PortableRepeatGenerationProfileWorkerResult:
    """One fresh repeat generation plus its worker-local runtime identity."""

    generation: _PortableBundleIdentityWorkerResult
    runtime_id: str
    runtime_content_sha256: str


@dataclass(frozen=True)
class _PortableCheckProfileWorkerResult:
    """One fresh checker result plus its worker-local runtime identity."""

    check: _PortableCheckWorkerResult
    runtime_id: str
    runtime_content_sha256: str


@dataclass(frozen=True)
class _PortableCheckWorkerResult:
    """Fresh-check result and observational timing evidence; no capabilities escape."""

    regime: TemporalRegime
    temporal_seed: int
    stream_id: str
    event_count: int
    worker_pid: int
    semantic_bundle_bytes_sha256: str
    bundle_sha256: str
    semantic_serialized_bytes: int
    fixed_layer_node_count: int
    layer_counts: tuple[int, int, int, int, int, int, int]
    check: M8CheckedFactBundleResult
    profile: M8ProfileReport
    metadata_reconciliation_wall_seconds: float
    authority_reconstruction_wall_seconds: float
    checker_wall_seconds: float
    counted_search_lemma_count: int
    counted_translation_audit_call_count: int
    registry_state_after: _PortableRegistryState


@dataclass(frozen=True)
class _PortableFactCheckedSource:
    """Canonical first-generation bytes retained only after a successful check."""

    first_generation: _PortableBundleIdentityWorkerResult
    semantic_bundle_bytes: bytes
    check: _PortableCheckWorkerResult

    @property
    def cell_identity(self) -> tuple[TemporalRegime, int, str, int]:
        return (
            self.first_generation.regime,
            self.first_generation.temporal_seed,
            self.first_generation.stream_id,
            self.first_generation.event_count,
        )


@dataclass(frozen=True)
class _PortableFactCellExecution:
    first_generation: _PortableBundleIdentityWorkerResult
    second_generation: _PortableBundleIdentityWorkerResult
    check: _PortableCheckWorkerResult


@dataclass(frozen=True)
class _PortableFactProfileTiming:
    """Controller-observed profile timing with the official Gate-3 boundaries."""

    first_generation_phase_wall_seconds: float
    second_generation_phase_wall_seconds: float
    checker_phase_wall_seconds: float
    total_pipeline_wall_seconds: float
    generator_runtime_content_sha256: str
    repeat_generator_runtime_content_sha256: str
    checker_runtime_content_sha256: str


@dataclass(frozen=True)
class _PortableFactPipelineExecution:
    cells: tuple[_PortableFactCellExecution, ...]
    first_generation_phase_wall_seconds: float
    second_generation_phase_wall_seconds: float
    checker_phase_wall_seconds: float
    task_serialization_wall_seconds: float
    result_serialization_wall_seconds: float
    inbound_payload_handoff_wall_seconds: float
    outbound_payload_handoff_wall_seconds: float
    worker_payload_handoff_wall_seconds: float
    process_exit_validation_wall_seconds: float
    worker_task_payload_bytes: int
    worker_result_payload_bytes: int
    per_worker_task_payload_byte_cap: int
    per_worker_result_payload_byte_cap: int
    retained_first_generation_bundle_bytes: int
    retained_first_generation_bundle_byte_cap: int
    checker_task_payload_bytes: int
    checker_task_payload_byte_cap: int
    total_pipeline_wall_seconds: float
    outer_process_count: int
    nested_process_count: int
    peak_compute_count: int
    controller_registry_state_before: _PortableRegistryState
    controller_registry_state_after: _PortableRegistryState


@dataclass(frozen=True)
class _SampleAuditGeneratorResult:
    regime: TemporalRegime
    sampled: tuple[M8CertificateActionResult, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class _SampleAuditCheckerResult:
    regime: TemporalRegime
    checks: tuple[M8ProofCheckResult, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class _ReferenceAuditCellResult:
    regime: TemporalRegime
    scores: tuple[M8ActionScore, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class _ReferenceAuditActionResult:
    regime: TemporalRegime
    score: M8ActionScore
    elapsed_seconds: float


@dataclass(frozen=True)
class _AuditPhaseResult:
    regime: TemporalRegime
    sampled: tuple[M8CertificateActionResult, ...]
    sampled_checks: tuple[M8ProofCheckResult, ...]
    reference_scores: tuple[M8ActionScore, ...]
    sampled_certificate_elapsed_seconds: float
    sampled_checker_elapsed_seconds: float
    sampled_reference_elapsed_seconds: float


@dataclass(frozen=True)
class _AuditCandidate:
    binding: M8AuditActionBinding
    labels: frozenset[tuple[str, str, str]]


def _order_worker_results(results):  # type: ignore[no-untyped-def]
    """Require one worker result per registered regime and return canonical order."""

    by_regime = {item.regime: item for item in results}
    if len(results) != len(TemporalRegime) or len(by_regime) != len(TemporalRegime):
        raise ValueError("M8 distributed phase requires exactly one result per regime")
    try:
        return tuple(by_regime[regime] for regime in TemporalRegime)
    except KeyError as error:
        raise ValueError("M8 distributed phase requires exactly one result per regime") from error


def _run_process_phase(
    operation,  # type: ignore[no-untyped-def]
    tasks: tuple[tuple[object, ...], ...],
    *,
    process_count: int,
    timeout_seconds: float = _DISTRIBUTED_PHASE_TIMEOUT_SECONDS,
    report_payload_handoff: bool = False,
    aggregate_task_payload_byte_cap: int | None = None,
):
    """Run one bounded fail-closed process phase and terminate owned workers."""

    if not tasks:
        raise ValueError("M8 distributed phase requires at least one task")
    if not 1 <= process_count <= _WORKER_COUNT:
        raise ValueError("M8 distributed process count is outside the frozen boundary")
    if timeout_seconds <= 0:
        raise ValueError("M8 distributed phase timeout must be positive")
    if aggregate_task_payload_byte_cap is not None and (
        type(aggregate_task_payload_byte_cap) is not int or aggregate_task_payload_byte_cap <= 0
    ):
        raise ValueError("M8 distributed aggregate task payload cap must be positive")

    context = multiprocessing.get_context("spawn")
    results = [None] * len(tasks)
    pending = iter(enumerate(tasks))
    active: dict[Connection, tuple[int, _OwnedPhaseProcess]] = {}
    owned_processes: list[_OwnedPhaseProcess] = []
    inbound_payload_handoff_ns = 0
    outbound_payload_handoff_ns = 0
    process_exit_validation_ns = 0
    task_serialization_ns = 0
    result_serialization_ns = 0
    task_payload_bytes = 0
    result_payload_bytes = 0

    def start_next() -> bool:
        nonlocal task_payload_bytes, task_serialization_ns
        try:
            index, task = next(pending)
        except StopIteration:
            return False
        serialization_started_ns = perf_counter_ns()
        serialized_task = pickle.dumps(task, protocol=5)
        task_serialization_ns += perf_counter_ns() - serialization_started_ns
        if len(serialized_task) > _M8_GATE3_MAX_WORKER_TASK_BYTES:
            raise ValueError("M8 distributed task exceeds the frozen payload cap")
        if (
            aggregate_task_payload_byte_cap is not None
            and task_payload_bytes + len(serialized_task) > aggregate_task_payload_byte_cap
        ):
            raise ValueError("M8 distributed aggregate task payload exceeds the frozen cap")
        task_payload_bytes += len(serialized_task)
        receiver, sender = context.Pipe(duplex=True)
        process = context.Process(
            target=_process_phase_entry,
            args=(operation, sender),
        )
        owned = _OwnedPhaseProcess(
            process=process,
            connection=receiver,
            deadline=monotonic() + timeout_seconds,
            task_payload=serialized_task,
        )
        owned_processes.append(owned)
        try:
            process.start()
        finally:
            sender.close()
        active[receiver] = (index, owned)
        return True

    completed = 0
    try:
        for _ in range(min(process_count, len(tasks))):
            start_next()
        while completed < len(tasks):
            remaining = min(owned.deadline for _index, owned in active.values()) - monotonic()
            ready = wait(
                tuple(active),
                timeout=max(0.0, min(1.0, remaining)),
            )
            if not ready:
                failed_sender = next(
                    (
                        owned
                        for _index, owned in active.values()
                        if owned.payload_sender_error is not None
                    ),
                    None,
                )
                if failed_sender is not None:
                    raise RuntimeError("M8 distributed task payload handoff failed") from (
                        failed_sender.payload_sender_error
                    )
                if remaining <= 0:
                    phase = (
                        "startup handshake"
                        if any(owned.group_id is None for _index, owned in active.values())
                        else "task"
                    )
                    raise TimeoutError(f"M8 distributed {phase} exceeded {timeout_seconds} seconds")
                continue
            for receiver in ready:
                index, owned = active[receiver]
                process = owned.process
                if monotonic() > owned.deadline:
                    phase = "startup handshake" if owned.group_id is None else "task"
                    raise TimeoutError(f"M8 distributed {phase} exceeded {timeout_seconds} seconds")
                try:
                    message = receiver.recv()
                except EOFError as error:
                    raise RuntimeError(
                        "M8 distributed worker exited without a result "
                        f"(exit_code={process.exitcode})"
                    ) from error
                if monotonic() > owned.deadline:
                    phase = "startup handshake" if owned.group_id is None else "task"
                    raise TimeoutError(f"M8 distributed {phase} exceeded {timeout_seconds} seconds")
                status, payload, details, remote_timestamp_ns = message
                if status == "ready":
                    if owned.group_id is not None or payload != process.pid:
                        raise RuntimeError("M8 distributed worker process-group handshake differs")
                    owned.group_id = payload
                    owned.deadline = monotonic() + timeout_seconds
                    owned.payload_send_started_ns = perf_counter_ns()
                    owned.payload_sender = threading.Thread(
                        target=_send_owned_payload,
                        args=(owned,),
                        name=f"m8-payload-sender-{process.pid}",
                        daemon=True,
                    )
                    owned.payload_sender.start()
                    continue
                if status == "started":
                    if owned.payload_send_started_ns is None:
                        raise RuntimeError("M8 distributed worker accepted a task before handoff")
                    inbound_payload_handoff_ns += max(
                        0,
                        remote_timestamp_ns - owned.payload_send_started_ns,
                    )
                    if owned.payload_sender is None:
                        raise RuntimeError("M8 distributed payload sender was not started")
                    owned.payload_sender.join(timeout=0.1)
                    if owned.payload_sender.is_alive():
                        raise RuntimeError("M8 distributed payload sender outlived receipt")
                    if owned.payload_sender_error is not None:
                        raise RuntimeError("M8 distributed task payload handoff failed") from (
                            owned.payload_sender_error
                        )
                    continue
                if status != "result_ready":
                    raise RuntimeError("M8 distributed worker handshake status differs")
                if type(payload) is not int or not 0 <= payload <= (
                    _M8_GATE3_MAX_WORKER_RESULT_BYTES
                ):
                    raise RuntimeError("M8 distributed result payload size is invalid")
                result_serialization_ns += int(details)
                result_payload_bytes += payload
                result_box: list[bytes | BaseException] = []
                receiver_thread = threading.Thread(
                    target=_receive_connection_bytes,
                    args=(receiver, _M8_GATE3_MAX_WORKER_RESULT_BYTES, result_box),
                    name=f"m8-payload-receiver-{process.pid}",
                    daemon=True,
                )
                owned.payload_receiver = receiver_thread
                receiver_thread.start()
                receiver_thread.join(timeout=max(0.0, owned.deadline - monotonic()))
                if receiver_thread.is_alive():
                    raise TimeoutError(
                        "M8 distributed task exceeded "
                        f"{timeout_seconds} seconds during result handoff"
                    )
                if len(result_box) != 1 or isinstance(result_box[0], BaseException):
                    error = result_box[0] if result_box else None
                    raise RuntimeError("M8 distributed result payload handoff failed") from (
                        error if isinstance(error, BaseException) else None
                    )
                serialized_result = result_box[0]
                if len(serialized_result) != payload:
                    raise RuntimeError("M8 distributed result payload length differs")
                outbound_payload_handoff_ns += max(
                    0,
                    perf_counter_ns() - remote_timestamp_ns,
                )
                try:
                    worker_status, worker_payload, worker_details = pickle.loads(serialized_result)
                except (
                    pickle.PickleError,
                    EOFError,
                    AttributeError,
                    TypeError,
                    ValueError,
                ) as error:
                    raise RuntimeError("M8 distributed result payload is invalid") from error
                active.pop(receiver)
                receiver.close()
                exit_started_ns = perf_counter_ns()
                process.join(timeout=1.0)
                if process.is_alive():
                    raise RuntimeError(
                        "M8 distributed worker did not exit after returning a result"
                    )
                if process.exitcode != 0:
                    raise RuntimeError(
                        "M8 distributed worker returned a result but exited nonzero "
                        f"(exit_code={process.exitcode})"
                    )
                if worker_status != "ok":
                    raise RuntimeError(
                        f"M8 distributed worker failed: {worker_payload}\n{worker_details}"
                    )
                if owned.group_id is not None and _process_group_exists(owned.group_id):
                    try:
                        os.killpg(owned.group_id, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    raise RuntimeError(
                        "M8 distributed worker left a surviving process-group descendant"
                    )
                process_exit_validation_ns += max(
                    0,
                    perf_counter_ns() - exit_started_ns,
                )
                owned.resolved = True
                results[index] = worker_payload
                completed += 1
                start_next()
    except BaseException:
        _terminate_owned_processes(owned_processes)
        raise
    finally:
        for owned in owned_processes:
            owned.connection.close()
    _terminate_owned_processes(owned_processes)
    if any(item is None for item in results):
        raise RuntimeError("M8 distributed phase left a task without a result")
    ordered_results = tuple(results)
    if report_payload_handoff:
        return _ProcessPhaseExecution(
            results=ordered_results,
            inbound_payload_handoff_wall_seconds=(inbound_payload_handoff_ns / 1_000_000_000.0),
            outbound_payload_handoff_wall_seconds=(outbound_payload_handoff_ns / 1_000_000_000.0),
            process_exit_validation_wall_seconds=(process_exit_validation_ns / 1_000_000_000.0),
            task_serialization_wall_seconds=(task_serialization_ns / 1_000_000_000.0),
            result_serialization_wall_seconds=(result_serialization_ns / 1_000_000_000.0),
            task_payload_bytes=task_payload_bytes,
            result_payload_bytes=result_payload_bytes,
            task_payload_byte_cap=_M8_GATE3_MAX_WORKER_TASK_BYTES,
            result_payload_byte_cap=_M8_GATE3_MAX_WORKER_RESULT_BYTES,
        )
    return ordered_results


def _process_phase_entry(
    operation,  # type: ignore[no-untyped-def]
    sender: Connection,
) -> None:
    """Execute one task and return only serialized evidence or serialized failure."""

    try:
        if not hasattr(os, "setsid"):
            raise RuntimeError("M8 distributed workers require POSIX process groups")
        os.setsid()
        sender.send(("ready", os.getpid(), "", perf_counter_ns()))
        serialized_task = sender.recv_bytes(
            maxlength=_M8_GATE3_MAX_WORKER_TASK_BYTES,
        )
        try:
            task = pickle.loads(serialized_task)
        except (
            pickle.PickleError,
            EOFError,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise RuntimeError("M8 distributed worker task payload is invalid") from error
        if type(task) is not tuple:
            raise RuntimeError("M8 distributed worker start handshake differs")
        sender.send(("started", None, "", perf_counter_ns()))
        try:
            result = operation(*task)
            worker_status = "ok"
            worker_payload = result
            worker_details = ""
        except BaseException as error:
            worker_status = "error"
            worker_payload = f"{type(error).__name__}: {error}"
            worker_details = traceback.format_exc()
        serialization_started_ns = perf_counter_ns()
        serialized_result = pickle.dumps(
            (worker_status, worker_payload, worker_details),
            protocol=5,
        )
        serialization_ns = perf_counter_ns() - serialization_started_ns
        if len(serialized_result) > _M8_GATE3_MAX_WORKER_RESULT_BYTES:
            serialized_result = pickle.dumps(
                (
                    "error",
                    "ValueError: M8 distributed result exceeds the frozen payload cap",
                    "",
                ),
                protocol=5,
            )
        send_started_ns = perf_counter_ns()
        sender.send(
            (
                "result_ready",
                len(serialized_result),
                serialization_ns,
                send_started_ns,
            )
        )
        sender.send_bytes(serialized_result)
    except BaseException as error:
        try:
            serialization_started_ns = perf_counter_ns()
            serialized_error = pickle.dumps(
                (
                    "error",
                    f"{type(error).__name__}: {error}",
                    traceback.format_exc(),
                ),
                protocol=5,
            )
            serialization_ns = perf_counter_ns() - serialization_started_ns
            send_started_ns = perf_counter_ns()
            sender.send(
                (
                    "result_ready",
                    len(serialized_error),
                    serialization_ns,
                    send_started_ns,
                )
            )
            sender.send_bytes(serialized_error)
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        sender.close()


@dataclass
class _OwnedPhaseProcess:
    process: multiprocessing.Process
    connection: Connection
    deadline: float
    task_payload: bytes
    group_id: int | None = None
    resolved: bool = False
    payload_send_started_ns: int | None = None
    payload_sender: threading.Thread | None = None
    payload_sender_error: BaseException | None = None
    payload_receiver: threading.Thread | None = None


@dataclass(frozen=True)
class _ProcessPhaseExecution:
    """Explicit spawn-pipe payload timing, including serialization and scheduling."""

    results: tuple[object, ...]
    inbound_payload_handoff_wall_seconds: float
    outbound_payload_handoff_wall_seconds: float
    process_exit_validation_wall_seconds: float
    task_serialization_wall_seconds: float
    result_serialization_wall_seconds: float
    task_payload_bytes: int
    result_payload_bytes: int
    task_payload_byte_cap: int
    result_payload_byte_cap: int

    @property
    def worker_payload_handoff_wall_seconds(self) -> float:
        return (
            self.inbound_payload_handoff_wall_seconds + self.outbound_payload_handoff_wall_seconds
        )


def _process_group_exists(group_id: int) -> bool:
    """Probe a child-owned process group without signaling any member."""

    try:
        os.kill(-group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _send_connection_bytes(connection: Connection, payload: bytes) -> None:
    connection.send_bytes(payload)


def _send_owned_payload(owned: _OwnedPhaseProcess) -> None:
    try:
        _send_connection_bytes(owned.connection, owned.task_payload)
    except BaseException as error:
        owned.payload_sender_error = error


def _receive_connection_bytes(
    connection: Connection,
    byte_cap: int,
    result_box: list[bytes | BaseException],
) -> None:
    try:
        result_box.append(connection.recv_bytes(maxlength=byte_cap))
    except BaseException as error:
        result_box.append(error)


def _terminate_owned_processes(processes: list[_OwnedPhaseProcess]) -> None:
    """Terminate every owned process/thread and prove its process group is absent."""

    for owned in processes:
        try:
            owned.connection.close()
        except OSError:
            pass
        process = owned.process
        if not owned.resolved and owned.group_id is not None:
            try:
                os.killpg(owned.group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # A just-exited session leader can remain as a zombie until it is
                # joined.  Reap the leader below, then probe the group again; a
                # still-live group is reported as a cleanup failure.
                pass
        elif not owned.resolved and process.pid is not None and process.is_alive():
            process.kill()
    cleanup_failures = []
    for owned in processes:
        process = owned.process
        if process.pid is not None:
            process.join(timeout=1.0)
            if process.is_alive():
                cleanup_failures.append(f"leader:{process.pid}")
        for thread in (owned.payload_sender, owned.payload_receiver):
            if thread is not None:
                thread.join(timeout=1.0)
                if thread.is_alive():
                    cleanup_failures.append(f"thread:{thread.name}")
        if owned.group_id is not None:
            deadline = monotonic() + 1.0
            while _process_group_exists(owned.group_id) and monotonic() < deadline:
                try:
                    os.killpg(owned.group_id, signal.SIGKILL)
                except ProcessLookupError:
                    break
                except PermissionError:
                    # Signaling denial is not evidence of absence.  Keep probing
                    # until the bounded cleanup deadline and fail if it persists.
                    pass
                sleep(0.01)
            if _process_group_exists(owned.group_id):
                cleanup_failures.append(f"group:{owned.group_id}")
    if cleanup_failures:
        raise RuntimeError(
            "M8 distributed cleanup could not prove all owned work absent: "
            + ",".join(cleanup_failures)
        )


def _portable_registry_state() -> _PortableRegistryState:
    """Return all process-local authority/producer registries used by the v2 path."""

    from yieldforge.baseline import replay
    from yieldforge.oracle import certificates, checker, compiled, fact_checker, sparse

    return _PortableRegistryState(
        authoritative_proof_runtime=len(  # noqa: SLF001
            replay._AUTHORITATIVE_PROOF_RUNTIME_REGISTRY
        ),
        materialized_standard_action=len(  # noqa: SLF001
            replay._MATERIALIZED_STANDARD_ACTION_REGISTRY
        ),
        legacy_prepared_checker=len(checker._PREPARED_CHECKER_REGISTRY),  # noqa: SLF001
        prepared_generator=len(sparse._PREPARED_GENERATOR_REGISTRY),  # noqa: SLF001
        prepared_translation_layout=len(  # noqa: SLF001
            compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY
        ),
        unchecked_prepared_source_guard=len(  # noqa: SLF001
            certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY
        ),
        validated_common=len(certificates._VALIDATED_COMMON_REGISTRY),  # noqa: SLF001
        fact_checker_registration_token=len(  # noqa: SLF001
            fact_checker._CHECKER_REGISTRATION_TOKENS
        ),
        full_traversal_guard=len(fact_checker._FULL_TRAVERSAL_GUARDS),  # noqa: SLF001
        translation_audit_processes=current_m8_translation_audit_processes(),
    )


def _require_exact_nonnegative_int(value: object, *, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"M8 portable {field_name} must be a nonnegative exact integer")
    return value


def _require_exact_nonnegative_float(value: object, *, field_name: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        raise ValueError(f"M8 portable {field_name} must be a finite nonnegative float")
    return value


def _require_prefixed_hex(
    value: object,
    *,
    prefix: str,
    hex_length: int,
    field_name: str,
) -> str:
    if (
        type(value) is not str
        or not value.startswith(prefix)
        or len(value) != len(prefix) + hex_length
        or any(character not in "0123456789abcdef" for character in value[len(prefix) :])
    ):
        raise ValueError(f"M8 portable {field_name} has an invalid content identity")
    return value


def _strict_portable_registry_state(value: object) -> _PortableRegistryState:
    if type(value) is not _PortableRegistryState:
        raise TypeError("M8 portable worker registry state has an unexpected type")
    state = _PortableRegistryState(
        authoritative_proof_runtime=_require_exact_nonnegative_int(
            value.authoritative_proof_runtime,
            field_name="authoritative proof runtime registry count",
        ),
        materialized_standard_action=_require_exact_nonnegative_int(
            value.materialized_standard_action,
            field_name="materialized standard action registry count",
        ),
        legacy_prepared_checker=_require_exact_nonnegative_int(
            value.legacy_prepared_checker,
            field_name="legacy prepared checker registry count",
        ),
        prepared_generator=_require_exact_nonnegative_int(
            value.prepared_generator,
            field_name="prepared generator registry count",
        ),
        prepared_translation_layout=_require_exact_nonnegative_int(
            value.prepared_translation_layout,
            field_name="prepared translation layout registry count",
        ),
        unchecked_prepared_source_guard=_require_exact_nonnegative_int(
            value.unchecked_prepared_source_guard,
            field_name="unchecked source guard registry count",
        ),
        validated_common=_require_exact_nonnegative_int(
            value.validated_common,
            field_name="validated common registry count",
        ),
        fact_checker_registration_token=_require_exact_nonnegative_int(
            value.fact_checker_registration_token,
            field_name="fact checker token registry count",
        ),
        full_traversal_guard=_require_exact_nonnegative_int(
            value.full_traversal_guard,
            field_name="full traversal guard registry count",
        ),
        translation_audit_processes=(
            None
            if value.translation_audit_processes is None
            else _require_exact_nonnegative_int(
                value.translation_audit_processes,
                field_name="translation audit process context",
            )
        ),
    )
    return state


def _strict_bundle_telemetry(value: object) -> M8BundleGenerationTelemetry:
    if type(value) is not M8BundleGenerationTelemetry:
        raise TypeError("M8 portable generator telemetry has an unexpected type")
    portable_sizes = value.portable_transition_serialized_bytes
    if type(portable_sizes) is not tuple:
        raise TypeError("M8 portable transition telemetry must be an exact tuple")
    return M8BundleGenerationTelemetry(
        semantic_serialized_bytes=_require_exact_nonnegative_int(
            value.semantic_serialized_bytes,
            field_name="semantic serialized byte count",
        ),
        serialization_seconds=_require_exact_nonnegative_float(
            value.serialization_seconds,
            field_name="bundle serialization timing",
        ),
        portable_transition_serialized_bytes=tuple(
            _require_exact_nonnegative_int(
                item,
                field_name="portable transition serialized byte count",
            )
            for item in portable_sizes
        ),
        common_event_count=_require_exact_nonnegative_int(
            value.common_event_count,
            field_name="common event count",
        ),
        action_root_count=_require_exact_nonnegative_int(
            value.action_root_count,
            field_name="action root count",
        ),
        counted_inventory_evidence_count=_require_exact_nonnegative_int(
            value.counted_inventory_evidence_count,
            field_name="counted inventory evidence row count",
        ),
        translation_batch_count=_require_exact_nonnegative_int(
            value.translation_batch_count,
            field_name="translation batch count",
        ),
        exact_transition_count=_require_exact_nonnegative_int(
            value.exact_transition_count,
            field_name="exact transition count",
        ),
    )


def _strict_profile_phase(value: object) -> M8ProfilePhase:
    if type(value) is not M8ProfilePhase or type(value.children) is not tuple:
        raise TypeError("M8 portable profile phase has an unexpected type")
    if type(value.name) is not str:
        raise TypeError("M8 portable profile phase name must be an exact string")
    return M8ProfilePhase(
        name=value.name,
        process_ns=_require_exact_nonnegative_int(
            value.process_ns,
            field_name="profile process duration",
        ),
        wall_ns=_require_exact_nonnegative_int(
            value.wall_ns,
            field_name="profile wall duration",
        ),
        children=tuple(_strict_profile_phase(child) for child in value.children),
    )


def _strict_profile_report(value: object) -> M8ProfileReport:
    if (
        type(value) is not M8ProfileReport
        or type(value.phases) is not tuple
        or type(value._counts) is not tuple  # noqa: SLF001
        or type(value.schema_version) is not str
    ):
        raise TypeError("M8 portable profile report has an unexpected type")
    if value.schema_version != "yieldforge.m8-phase-profile.v2":
        raise ValueError("M8 portable profile report schema differs")
    counts = []
    for item in value._counts:  # noqa: SLF001
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise TypeError("M8 portable profile counter entry has an unexpected type")
        counts.append(
            (
                item[0],
                _require_exact_nonnegative_int(
                    item[1],
                    field_name="profile counter",
                ),
            )
        )
    report = M8ProfileReport(
        total_process_ns=_require_exact_nonnegative_int(
            value.total_process_ns,
            field_name="profile total process duration",
        ),
        total_wall_ns=_require_exact_nonnegative_int(
            value.total_wall_ns,
            field_name="profile total wall duration",
        ),
        phases=tuple(_strict_profile_phase(phase) for phase in value.phases),
        _counts=tuple(counts),  # type: ignore[arg-type]
        schema_version=value.schema_version,
    )
    if sum(phase.wall_ns for phase in report.phases) > report.total_wall_ns:
        raise ValueError("M8 portable profile phases exceed total wall duration")
    return report


def _strict_portable_bundle_identity(
    value: object,
    *,
    require_bytes: bool,
) -> _PortableBundleIdentityWorkerResult:
    expected_type = (
        _PortableBundleWorkerResult if require_bytes else _PortableBundleIdentityWorkerResult
    )
    if type(value) is not expected_type:
        raise TypeError("M8 portable generator returned an unexpected envelope")
    telemetry = _strict_bundle_telemetry(value.telemetry)
    if type(value.regime) is not TemporalRegime:
        raise TypeError("M8 portable generator regime has an unexpected type")
    if type(value.stream_id) is not str or not value.stream_id.startswith("yfts-"):
        raise ValueError("M8 portable generator stream identity differs")
    layer_counts = tuple(
        _require_exact_nonnegative_int(item, field_name="fixed layer node count")
        for item in (
            value.translation_batch_count,
            value.candidate_scalar_fact_count,
            value.frontier_fact_count,
            value.standard_candidate_fact_count,
            value.common_lemma_count,
            value.influence_fact_count,
            value.action_root_count,
        )
    )
    counted_search_lemma_count = _require_exact_nonnegative_int(
        value.counted_search_lemma_count,
        field_name="counted search lemma count",
    )
    fixed_count = _require_exact_nonnegative_int(
        value.fixed_layer_node_count,
        field_name="fixed layer node count",
    )
    if (
        fixed_count != sum(layer_counts)
        or telemetry.translation_batch_count != layer_counts[0]
        or telemetry.common_event_count != layer_counts[4]
        or telemetry.action_root_count != layer_counts[6]
        or counted_search_lemma_count > layer_counts[4]
    ):
        raise ValueError("M8 portable generator telemetry does not reconcile")
    registry_state = _strict_portable_registry_state(value.registry_state_after)
    if not registry_state.is_clean:
        raise ValueError("M8 portable generator registry evidence is not clean")
    common = dict(
        regime=value.regime,
        temporal_seed=_require_exact_nonnegative_int(
            value.temporal_seed,
            field_name="temporal seed",
        ),
        stream_id=value.stream_id,
        event_count=_require_exact_nonnegative_int(
            value.event_count,
            field_name="event count",
        ),
        worker_pid=_require_exact_nonnegative_int(
            value.worker_pid,
            field_name="generator worker pid",
        ),
        semantic_bundle_bytes_sha256=_require_prefixed_hex(
            value.semantic_bundle_bytes_sha256,
            prefix="sha256:",
            hex_length=64,
            field_name="serialized bundle SHA-256",
        ),
        bundle_sha256=_require_prefixed_hex(
            value.bundle_sha256,
            prefix="sha256:",
            hex_length=64,
            field_name="bundle root",
        ),
        telemetry=telemetry,
        fixed_layer_node_count=fixed_count,
        translation_batch_count=layer_counts[0],
        candidate_scalar_fact_count=layer_counts[1],
        frontier_fact_count=layer_counts[2],
        standard_candidate_fact_count=layer_counts[3],
        common_lemma_count=layer_counts[4],
        counted_search_lemma_count=counted_search_lemma_count,
        influence_fact_count=layer_counts[5],
        action_root_count=layer_counts[6],
        generation_wall_seconds=_require_exact_nonnegative_float(
            value.generation_wall_seconds,
            field_name="generator worker timing",
        ),
        handoff_serialization_wall_seconds=_require_exact_nonnegative_float(
            value.handoff_serialization_wall_seconds,
            field_name="generator handoff serialization timing",
        ),
        registry_state_after=registry_state,
    )
    if require_bytes:
        semantic_bytes = value.semantic_bundle_bytes
        if type(semantic_bytes) is not bytes:
            raise TypeError("M8 portable generator semantic payload must be exact bytes")
        raw_sha = f"sha256:{hashlib.sha256(semantic_bytes).hexdigest()}"
        if (
            len(semantic_bytes) != telemetry.semantic_serialized_bytes
            or raw_sha != common["semantic_bundle_bytes_sha256"]
        ):
            raise ValueError("M8 portable generator serialized bytes do not reconcile")
        return _PortableBundleWorkerResult(
            **common,
            semantic_bundle_bytes=semantic_bytes,
        )
    return _PortableBundleIdentityWorkerResult(**common)


def _strict_portable_generation_profile_worker_result(
    value: object,
) -> _PortableGenerationProfileWorkerResult:
    if type(value) is not _PortableGenerationProfileWorkerResult:
        raise TypeError("M8 portable profile generator returned an unexpected envelope")
    generated = _strict_portable_bundle_identity(
        value.generation,
        require_bytes=True,
    )
    if type(generated) is not _PortableBundleWorkerResult:
        raise RuntimeError("M8 portable profile generator bytes were discarded")
    return _PortableGenerationProfileWorkerResult(
        generation=generated,
        profile=_strict_profile_report(value.profile),
        runtime_id=_require_portable_worker_runtime_id(value.runtime_id),
        runtime_content_sha256=_require_prefixed_hex(
            value.runtime_content_sha256,
            prefix="sha256:",
            hex_length=64,
            field_name="profile generator worker runtime",
        ),
    )


def _require_portable_worker_runtime_id(value: object) -> str:
    if value != "yieldforge-m8-gate3-runtime-v1":
        raise ValueError("M8 portable worker runtime ID differs")
    return value


def _strict_portable_repeat_generation_profile_worker_result(
    value: object,
) -> _PortableRepeatGenerationProfileWorkerResult:
    if type(value) is not _PortableRepeatGenerationProfileWorkerResult:
        raise TypeError("M8 portable profile repeat generator returned an unexpected envelope")
    generated = _strict_portable_bundle_identity(value.generation, require_bytes=False)
    if type(generated) is not _PortableBundleIdentityWorkerResult:
        raise RuntimeError("M8 portable profile repeat generator retained canonical bytes")
    return _PortableRepeatGenerationProfileWorkerResult(
        generation=generated,
        runtime_id=_require_portable_worker_runtime_id(value.runtime_id),
        runtime_content_sha256=_require_prefixed_hex(
            value.runtime_content_sha256,
            prefix="sha256:",
            hex_length=64,
            field_name="profile repeat generator worker runtime",
        ),
    )


def _strict_portable_check_profile_worker_result(
    value: object,
) -> _PortableCheckProfileWorkerResult:
    if type(value) is not _PortableCheckProfileWorkerResult:
        raise TypeError("M8 portable profile checker returned an unexpected envelope")
    return _PortableCheckProfileWorkerResult(
        check=_strict_portable_check_worker_result(value.check),
        runtime_id=_require_portable_worker_runtime_id(value.runtime_id),
        runtime_content_sha256=_require_prefixed_hex(
            value.runtime_content_sha256,
            prefix="sha256:",
            hex_length=64,
            field_name="profile checker worker runtime",
        ),
    )


def _strict_portable_check_worker_result(
    value: object,
) -> _PortableCheckWorkerResult:
    if type(value) is not _PortableCheckWorkerResult:
        raise TypeError("M8 portable checker returned an unexpected envelope")
    if type(value.regime) is not TemporalRegime:
        raise TypeError("M8 portable checker regime has an unexpected type")
    if type(value.layer_counts) is not tuple or len(value.layer_counts) != 7:
        raise TypeError("M8 portable checker layer counts have an unexpected type")
    layer_counts = tuple(
        _require_exact_nonnegative_int(item, field_name="checker fixed layer count")
        for item in value.layer_counts
    )
    fixed_count = _require_exact_nonnegative_int(
        value.fixed_layer_node_count,
        field_name="checker fixed layer node count",
    )
    if fixed_count != sum(layer_counts):
        raise ValueError("M8 portable checker fixed layer counts do not reconcile")
    if type(value.check) is not M8CheckedFactBundleResult:
        raise TypeError("M8 portable checker Task6 result has an unexpected type")
    check = M8CheckedFactBundleResult.model_validate_json(
        value.check.model_dump_json(),
        strict=True,
    )
    profile = _strict_profile_report(value.profile)
    counted_search_lemma_count = _require_exact_nonnegative_int(
        value.counted_search_lemma_count,
        field_name="counted search lemma count",
    )
    counted_translation_audit_call_count = _require_exact_nonnegative_int(
        value.counted_translation_audit_call_count,
        field_name="counted translation audit call count",
    )
    if (
        check.counted_translation_audit_count != counted_search_lemma_count
        or counted_translation_audit_call_count != counted_search_lemma_count
        or counted_translation_audit_call_count
        != _portable_profile_phase_count(
            profile,
            "counted_translation_audit_call",
        )
    ):
        raise ValueError("M8 portable counted-search lemma count does not reconcile")
    registry_state = _strict_portable_registry_state(value.registry_state_after)
    if not registry_state.is_clean:
        raise ValueError("M8 portable checker registry evidence is not clean")
    return _PortableCheckWorkerResult(
        regime=value.regime,
        temporal_seed=_require_exact_nonnegative_int(
            value.temporal_seed,
            field_name="checker temporal seed",
        ),
        stream_id=value.stream_id,
        event_count=_require_exact_nonnegative_int(
            value.event_count,
            field_name="checker event count",
        ),
        worker_pid=_require_exact_nonnegative_int(
            value.worker_pid,
            field_name="checker worker pid",
        ),
        semantic_bundle_bytes_sha256=_require_prefixed_hex(
            value.semantic_bundle_bytes_sha256,
            prefix="sha256:",
            hex_length=64,
            field_name="checker serialized bundle SHA-256",
        ),
        bundle_sha256=_require_prefixed_hex(
            value.bundle_sha256,
            prefix="sha256:",
            hex_length=64,
            field_name="checker bundle root",
        ),
        semantic_serialized_bytes=_require_exact_nonnegative_int(
            value.semantic_serialized_bytes,
            field_name="checker semantic serialized byte count",
        ),
        fixed_layer_node_count=fixed_count,
        layer_counts=layer_counts,  # type: ignore[arg-type]
        check=check,
        profile=profile,
        metadata_reconciliation_wall_seconds=_require_exact_nonnegative_float(
            value.metadata_reconciliation_wall_seconds,
            field_name="metadata reconciliation timing",
        ),
        authority_reconstruction_wall_seconds=_require_exact_nonnegative_float(
            value.authority_reconstruction_wall_seconds,
            field_name="authority reconstruction timing",
        ),
        checker_wall_seconds=_require_exact_nonnegative_float(
            value.checker_wall_seconds,
            field_name="checker worker timing",
        ),
        counted_search_lemma_count=counted_search_lemma_count,
        counted_translation_audit_call_count=(counted_translation_audit_call_count),
        registry_state_after=registry_state,
    )


def _verify_portable_jagua_executable(
    executable: Path | None,
    expected_sha256: str | None,
) -> None:
    if executable is None or expected_sha256 is None:
        if executable is not None or expected_sha256 is not None:
            raise ValueError("M8 portable worker Jagua runtime binding is incomplete")
        return
    path = Path(executable)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M8 portable worker Jagua runtime must be a regular file")
    observed = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    if observed != expected_sha256:
        raise ValueError("M8 portable worker Jagua runtime differs from the freeze")


def _portable_worker_runtime_identity(
    jagua_executable: Path | None,
    expected_jagua_sha256: str | None,
) -> tuple[str, str]:
    """Compute the Gate-3 runtime identity inside the executing process."""

    if expected_jagua_sha256 is None:
        if jagua_executable is None:
            jagua_sha256 = "sha256:" + "0" * 64
        else:
            executable = Path(jagua_executable)
            jagua_sha256 = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    else:
        jagua_sha256 = expected_jagua_sha256
    from yieldforge.oracle.gate3_execution import _runtime_identity

    return _runtime_identity(jagua_executable_sha256=jagua_sha256)


def _require_portable_profile_worker_runtime_identities(
    *,
    expected: tuple[str, str],
    observed: tuple[tuple[str, str], tuple[str, str], tuple[str, str]],
) -> None:
    """Reject any worker envelope that differs from the controller runtime."""

    if (
        type(expected) is not tuple
        or len(expected) != 2
        or any(type(item) is not str for item in expected)
        or type(observed) is not tuple
        or len(observed) != 3
        or any(
            type(identity) is not tuple
            or len(identity) != 2
            or any(type(item) is not str for item in identity)
            for identity in observed
        )
    ):
        raise TypeError("M8 portable profile worker runtime identity is malformed")
    if any(identity != expected for identity in observed):
        raise ValueError("M8 portable profile worker runtime identity differs")


def _generate_portable_fact_bundle_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    translation_audit_processes: int,
    return_semantic_bytes: bool = True,
) -> _PortableBundleWorkerResult | _PortableBundleIdentityWorkerResult:
    """Return only unchecked canonical bytes and untrusted producer measurements."""

    if not _portable_registry_state().is_clean:
        raise RuntimeError("M8 portable generator worker started with live registries")
    if type(return_semantic_bytes) is not bool:
        raise TypeError("M8 portable generator byte-return mode must be exact bool")
    _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    started = perf_counter()
    with activate_m8_translation_audit_processes(translation_audit_processes):
        with profile_phase("fact_bundle_generator_authority_reconstruction"):
            request = _request_for_cell(
                cell,
                rules=rules,
                jagua_executable=jagua_executable,
            )
        with profile_phase("fact_bundle_generation"):
            generated = score_unchecked_fact_bundle(
                M8UncheckedBundleRequest(
                    oracle_request=request,
                    freeze_id=freeze_id,
                    freeze_sha256=freeze_sha256,
                )
            )
    handoff_serialization_started = perf_counter()
    with profile_phase("fact_bundle_handoff_serialization"):
        semantic_bytes = generated.semantic_bytes
    handoff_serialization_wall_seconds = perf_counter() - handoff_serialization_started
    if len(semantic_bytes) > _M8_GATE3_MAX_BUNDLE_BYTES:
        raise ValueError("M8 portable generator bundle exceeds the frozen byte cap")
    bundle = generated.bundle
    layer_counts = (
        len(bundle.translation_batches),
        len(bundle.candidate_scalar_facts),
        len(bundle.frontier_facts),
        len(bundle.standard_candidate_facts),
        len(bundle.common_lemmas),
        len(bundle.influence_facts),
        len(bundle.action_roots),
    )
    registry_state = _portable_registry_state()
    if not registry_state.is_clean:
        raise RuntimeError("M8 portable generator worker leaked a process-local registry")
    _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    common = dict(
        regime=cell.stream[0].regime,
        temporal_seed=cell.stream[0].temporal_seed,
        stream_id=cell.stream[0].stream_id,
        event_count=len(cell.stream),
        worker_pid=os.getpid(),
        semantic_bundle_bytes_sha256=(f"sha256:{hashlib.sha256(semantic_bytes).hexdigest()}"),
        bundle_sha256=bundle.bundle_sha256,
        telemetry=generated.telemetry,
        fixed_layer_node_count=sum(layer_counts),
        translation_batch_count=layer_counts[0],
        candidate_scalar_fact_count=layer_counts[1],
        frontier_fact_count=layer_counts[2],
        standard_candidate_fact_count=layer_counts[3],
        common_lemma_count=layer_counts[4],
        counted_search_lemma_count=sum(
            any(item.classification == "counted_no_fit" for item in lemma.inventory_classifications)
            for lemma in bundle.common_lemmas
        ),
        influence_fact_count=layer_counts[5],
        action_root_count=layer_counts[6],
        generation_wall_seconds=max(0.000001, perf_counter() - started),
        handoff_serialization_wall_seconds=handoff_serialization_wall_seconds,
        registry_state_after=registry_state,
    )
    if return_semantic_bytes:
        return _PortableBundleWorkerResult(
            **common,
            semantic_bundle_bytes=semantic_bytes,
        )
    return _PortableBundleIdentityWorkerResult(**common)


def _profile_portable_generation_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    translation_audit_processes: int,
) -> _PortableGenerationProfileWorkerResult:
    """Profile one fresh generator without granting proof authority to its output."""

    runtime_before = _portable_worker_runtime_identity(
        jagua_executable,
        expected_jagua_sha256,
    )
    with activate_m8_profile() as profiler:
        generated = _generate_portable_fact_bundle_worker(
            cell,
            rules,
            jagua_executable,
            freeze_id,
            freeze_sha256,
            expected_jagua_sha256,
            translation_audit_processes,
            True,
        )
    if type(generated) is not _PortableBundleWorkerResult:
        raise RuntimeError("M8 portable profile generator omitted canonical bytes")
    runtime_after = _portable_worker_runtime_identity(
        jagua_executable,
        expected_jagua_sha256,
    )
    if runtime_after != runtime_before:
        raise ValueError("M8 portable profile generator runtime changed during execution")
    return _PortableGenerationProfileWorkerResult(
        generation=generated,
        profile=profiler.report(),
        runtime_id=runtime_after[0],
        runtime_content_sha256=runtime_after[1],
    )


def _repeat_portable_generation_profile_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    translation_audit_processes: int,
) -> _PortableRepeatGenerationProfileWorkerResult:
    """Repeat generation while attesting the runtime inside its fresh worker."""

    runtime_before = _portable_worker_runtime_identity(
        jagua_executable,
        expected_jagua_sha256,
    )
    generated = _generate_portable_fact_bundle_worker(
        cell,
        rules,
        jagua_executable,
        freeze_id,
        freeze_sha256,
        expected_jagua_sha256,
        translation_audit_processes,
        False,
    )
    if type(generated) is not _PortableBundleIdentityWorkerResult:
        raise RuntimeError("M8 portable profile repeat generator retained canonical bytes")
    runtime_after = _portable_worker_runtime_identity(
        jagua_executable,
        expected_jagua_sha256,
    )
    if runtime_after != runtime_before:
        raise ValueError("M8 portable profile repeat runtime changed during execution")
    return _PortableRepeatGenerationProfileWorkerResult(
        generation=generated,
        runtime_id=runtime_after[0],
        runtime_content_sha256=runtime_after[1],
    )


def _check_portable_fact_bundle_worker(
    semantic_bundle_bytes: bytes,
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    translation_audit_processes: int,
) -> _PortableCheckWorkerResult:
    """Check serialized bytes against independently rebuilt calibration authority."""

    if type(semantic_bundle_bytes) is not bytes:
        raise TypeError("M8 portable checker worker requires exact serialized bytes")
    if len(semantic_bundle_bytes) > _M8_GATE3_MAX_BUNDLE_BYTES:
        raise ValueError("M8 portable bundle exceeds the frozen byte cap")
    worker_started = perf_counter()
    if not _portable_registry_state().is_clean:
        raise RuntimeError("M8 portable checker worker started with live registries")
    _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    with activate_m8_profile() as profiler:
        metadata_started = perf_counter()
        with profile_phase("fact_bundle_metadata_reconciliation"):
            metadata_bundle = json.loads(semantic_bundle_bytes)
            if type(metadata_bundle) is not dict:
                raise ValueError("M8 portable metadata reconciliation requires an object")
            bundle_sha256 = metadata_bundle.get("bundle_sha256")
            if type(bundle_sha256) is not str:
                raise ValueError("M8 portable metadata reconciliation lacks a bundle root")
            layer_names = (
                "translation_batches",
                "candidate_scalar_facts",
                "frontier_facts",
                "standard_candidate_facts",
                "common_lemmas",
                "influence_facts",
                "action_roots",
            )
            layers = tuple(metadata_bundle.get(name) for name in layer_names)
            if any(type(layer) is not list for layer in layers):
                raise ValueError("M8 portable metadata reconciliation lacks a fixed layer")
            layer_counts = (
                *(len(layer) for layer in layers),  # type: ignore[arg-type]
            )
            counted_search_lemma_count = 0
            for lemma in layers[4]:  # type: ignore[union-attr]
                if type(lemma) is not dict:
                    raise ValueError("M8 portable metadata common lemma must be an object")
                classifications = lemma.get("inventory_classifications")
                if type(classifications) is not list:
                    raise ValueError("M8 portable metadata common classifications must be a list")
                if any(
                    type(item) is not dict or type(item.get("classification")) is not str
                    for item in classifications
                ):
                    raise ValueError("M8 portable metadata common classification differs")
                counted_search_lemma_count += any(
                    item["classification"] == "counted_no_fit" for item in classifications
                )
        metadata_reconciliation_wall_seconds = perf_counter() - metadata_started
        authority_started = perf_counter()
        with profile_phase("fact_bundle_authority_reconstruction"):
            request = _request_for_cell(
                cell,
                rules=rules,
                jagua_executable=jagua_executable,
            )
            runtime = request.runtime
            cursor = request.cursor
            catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
            visible = request.visibility.visible_suffix(
                current_position=catalog.event_position,
            )
            stop = catalog.event_position + 1 + len(visible)
            runtime_sha256 = m7_semantic_runtime_sha256(runtime)
            check_request = M8FactBundleCheckRequest(
                semantic_bundle_bytes=semantic_bundle_bytes,
                oracle_request=request,
                expected_semantic_runtime_sha256=runtime_sha256,
                expected_current_cursor_sha256=m7_cursor_sha256(cursor),
                expected_catalog_event_position=catalog.event_position,
                expected_catalog_action_ids=tuple(item.action_id for item in catalog.actions),
                expected_stop_event_position=stop,
                expected_suffix_sha256=m8_suffix_sha256(
                    semantic_runtime_sha256=runtime_sha256,
                    start_event_position=catalog.event_position,
                    stop_event_position=stop,
                    bindings=visible,
                ),
                expected_freeze_id=freeze_id,
                expected_freeze_sha256=freeze_sha256,
                allow_exact_replay=False,
            )
        authority_reconstruction_wall_seconds = perf_counter() - authority_started
        with activate_m8_translation_audit_processes(translation_audit_processes):
            check = check_m8_fact_bundle(check_request)
    profile = profiler.report()
    counted_translation_audit_call_count = _portable_profile_phase_count(
        profile,
        "counted_translation_audit_call",
    )
    _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    registry_state = _portable_registry_state()
    if not registry_state.is_clean:
        raise RuntimeError("M8 portable checker worker leaked a process-local registry")
    if not check.valid or check.decision is None:
        raise ValueError(
            "M8 portable fact checker rejected serialized bytes: "
            f"failure_code={check.failure_code};"
            f"first_failing_fact_sha256={check.first_failing_fact_sha256}"
        )
    if (
        check.counted_translation_audit_count != counted_search_lemma_count
        or counted_translation_audit_call_count != counted_search_lemma_count
    ):
        raise ValueError("M8 portable counted-search lemma count differs")
    checker_wall_seconds = max(0.000001, perf_counter() - worker_started)
    return _PortableCheckWorkerResult(
        regime=cell.stream[0].regime,
        temporal_seed=cell.stream[0].temporal_seed,
        stream_id=cell.stream[0].stream_id,
        event_count=len(cell.stream),
        worker_pid=os.getpid(),
        semantic_bundle_bytes_sha256=(
            f"sha256:{hashlib.sha256(semantic_bundle_bytes).hexdigest()}"
        ),
        bundle_sha256=bundle_sha256,
        semantic_serialized_bytes=len(semantic_bundle_bytes),
        fixed_layer_node_count=sum(layer_counts),
        layer_counts=layer_counts,
        check=check,
        profile=profile,
        metadata_reconciliation_wall_seconds=(metadata_reconciliation_wall_seconds),
        authority_reconstruction_wall_seconds=(authority_reconstruction_wall_seconds),
        checker_wall_seconds=checker_wall_seconds,
        counted_search_lemma_count=counted_search_lemma_count,
        counted_translation_audit_call_count=(counted_translation_audit_call_count),
        registry_state_after=registry_state,
    )


def _check_portable_fact_bundle_profile_worker(
    semantic_bundle_bytes: bytes,
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    translation_audit_processes: int,
) -> _PortableCheckProfileWorkerResult:
    """Check one bundle while attesting the runtime inside its fresh worker."""

    runtime_before = _portable_worker_runtime_identity(
        jagua_executable,
        expected_jagua_sha256,
    )
    checked = _check_portable_fact_bundle_worker(
        semantic_bundle_bytes,
        cell,
        rules,
        jagua_executable,
        freeze_id,
        freeze_sha256,
        expected_jagua_sha256,
        translation_audit_processes,
    )
    runtime_after = _portable_worker_runtime_identity(
        jagua_executable,
        expected_jagua_sha256,
    )
    if runtime_after != runtime_before:
        raise ValueError("M8 portable profile checker runtime changed during execution")
    return _PortableCheckProfileWorkerResult(
        check=checked,
        runtime_id=runtime_after[0],
        runtime_content_sha256=runtime_after[1],
    )


def _portable_generation_metadata(
    generated: _PortableBundleIdentityWorkerResult,
) -> tuple[object, ...]:
    return (
        generated.semantic_bundle_bytes_sha256,
        generated.bundle_sha256,
        generated.semantic_serialized_bytes,
        generated.fixed_layer_node_count,
        generated.translation_batch_count,
        generated.candidate_scalar_fact_count,
        generated.frontier_fact_count,
        generated.standard_candidate_fact_count,
        generated.common_lemma_count,
        generated.counted_search_lemma_count,
        generated.influence_fact_count,
        generated.action_root_count,
    )


def _require_portable_profile_repeat(
    generated: _PortableBundleIdentityWorkerResult,
    repeated: _PortableBundleIdentityWorkerResult,
) -> _PortableBundleIdentityWorkerResult:
    """Require a second fresh generation with the complete first-run identity."""

    generated = _strict_portable_bundle_identity(
        generated,
        require_bytes=type(generated) is _PortableBundleWorkerResult,
    )
    repeated = _strict_portable_bundle_identity(repeated, require_bytes=False)
    if (
        (
            generated.regime,
            generated.temporal_seed,
            generated.stream_id,
            generated.event_count,
        )
        != (
            repeated.regime,
            repeated.temporal_seed,
            repeated.stream_id,
            repeated.event_count,
        )
        or _portable_generation_metadata(generated)
        != _portable_generation_metadata(repeated)
    ):
        raise ValueError("M8 portable profile repeated generation identity differs")
    return repeated


def _reconcile_portable_fact_handoff(
    generated: _PortableBundleIdentityWorkerResult,
    checked: _PortableCheckWorkerResult,
) -> tuple[_PortableBundleIdentityWorkerResult, _PortableCheckWorkerResult]:
    """Promote producer metadata only after the independent successful checker agrees."""

    generated = _strict_portable_bundle_identity(
        generated,
        require_bytes=type(generated) is _PortableBundleWorkerResult,
    )
    checked = _strict_portable_check_worker_result(checked)
    checked_metadata = (
        checked.semantic_bundle_bytes_sha256,
        checked.bundle_sha256,
        checked.semantic_serialized_bytes,
        checked.fixed_layer_node_count,
        *checked.layer_counts[:5],
        checked.counted_search_lemma_count,
        *checked.layer_counts[5:],
    )
    if _portable_generation_metadata(generated) != checked_metadata:
        raise ValueError("M8 portable producer and checker metadata differ")
    if (
        (
            generated.regime,
            generated.temporal_seed,
            generated.stream_id,
            generated.event_count,
        )
        != (
            checked.regime,
            checked.temporal_seed,
            checked.stream_id,
            checked.event_count,
        )
        or not generated.registry_state_after.is_clean
        or not checked.registry_state_after.is_clean
        or not checked.check.valid
        or checked.check.decision is None
        or checked.check.checked_action_root_count != generated.action_root_count
        or checked.check.decision.scored_action_count != generated.action_root_count
        or checked.check.counted_translation_audit_count != generated.counted_search_lemma_count
        or checked.counted_translation_audit_call_count != generated.counted_search_lemma_count
    ):
        raise ValueError("M8 portable producer/checker result does not reconcile")
    return generated, checked


def _portable_profile_phase_names(report: M8ProfileReport) -> set[str]:
    names: set[str] = set()

    def visit(phases) -> None:  # type: ignore[no-untyped-def]
        for phase in phases:
            names.add(phase.name)
            visit(phase.children)

    visit(report.phases)
    return names


def _portable_profile_phase_count(report: M8ProfileReport, name: str) -> int:
    count = 0

    def visit(phases) -> None:  # type: ignore[no-untyped-def]
        nonlocal count
        for phase in phases:
            count += phase.name == name
            visit(phase.children)

    visit(report.phases)
    return count


def _retain_portable_fact_checked_source(
    generated: _PortableBundleWorkerResult,
    checked: _PortableCheckWorkerResult,
) -> _PortableFactCheckedSource:
    """Bind canonical bytes to the independently reconciled successful checker."""

    generated = _strict_portable_bundle_identity(generated, require_bytes=True)
    if type(generated) is not _PortableBundleWorkerResult:
        raise TypeError("M8 portable retained source requires exact canonical bytes")
    generated, checked = _reconcile_portable_fact_handoff(generated, checked)
    if type(generated) is not _PortableBundleWorkerResult:
        raise TypeError("M8 portable retained source lost its canonical bytes")
    if checked.check.total_exact_fallback_count != 0:
        raise ValueError("M8 Gate-3 portable checker performed forbidden exact fallback")
    required_phases = {
        "fact_bundle_strict_load",
        "fact_bundle_common_verification",
        "fact_bundle_action_traversal",
        "fact_bundle_cleanup",
    }
    if not required_phases <= _portable_profile_phase_names(checked.profile):
        raise ValueError("M8 portable checker omitted required timing phases")
    semantic_bundle_bytes = generated.semantic_bundle_bytes
    return _PortableFactCheckedSource(
        first_generation=_discard_portable_bundle_bytes(generated),
        semantic_bundle_bytes=semantic_bundle_bytes,
        check=checked,
    )


def _order_portable_worker_results(
    results: tuple[object, ...],
    execution_cells: tuple[_ExecutionCell, ...],
) -> tuple[object, ...]:
    expected = tuple(cell.stream[0].regime for cell in execution_cells)
    if len(expected) != len(set(expected)):
        raise ValueError("M8 portable execution requires unique regime cells")
    by_regime = {item.regime: item for item in results}  # type: ignore[attr-defined]
    if len(results) != len(expected) or len(by_regime) != len(expected):
        raise ValueError("M8 portable phase omitted or duplicated a regime")
    try:
        return tuple(by_regime[regime] for regime in expected)
    except KeyError as error:
        raise ValueError("M8 portable phase returned an unexpected regime") from error


def _discard_portable_bundle_bytes(
    generated: _PortableBundleWorkerResult,
) -> _PortableBundleIdentityWorkerResult:
    payload = dict(vars(generated))
    payload.pop("semantic_bundle_bytes")
    return _PortableBundleIdentityWorkerResult(**payload)


def _capture_portable_fact_checked_sources(
    execution_cells: tuple[_ExecutionCell, ...],
    *,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    budget: M8ConcurrencyBudget,
) -> tuple[_PortableFactCheckedSource, ...]:
    """Run one fresh generation/check pair and retain only checked canonical bytes."""

    if not execution_cells:
        raise ValueError("M8 portable source capture requires a calibration cell")
    controller_before = _strict_portable_registry_state(_portable_registry_state())
    if not controller_before.is_clean:
        raise ValueError("M8 portable source capture has live registries before spawn")
    _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    outer_process_count = min(budget.cell_phase_processes, len(execution_cells))
    common_tasks = tuple(
        (
            cell,
            rules,
            jagua_executable,
            freeze_id,
            freeze_sha256,
            expected_jagua_sha256,
            budget.translation_audit_processes_per_cell,
        )
        for cell in execution_cells
    )
    try:
        generation_phase = _run_process_phase(
            _generate_portable_fact_bundle_worker,
            tuple((*task, True) for task in common_tasks),
            process_count=outer_process_count,
            report_payload_handoff=True,
        )
        if type(generation_phase) is not _ProcessPhaseExecution:
            raise RuntimeError("M8 portable source generation omitted handoff telemetry")
        generated = _order_portable_worker_results(
            tuple(
                _strict_portable_bundle_identity(item, require_bytes=True)
                for item in generation_phase.results
            ),
            execution_cells,
        )
        retained_bundle_bytes = sum(item.semantic_serialized_bytes for item in generated)
        if retained_bundle_bytes > _M8_GATE3_MAX_RETAINED_BUNDLE_BYTES:
            raise ValueError("M8 portable retained bundles exceed the aggregate cap")

        checker_tasks = tuple(
            (
                item.semantic_bundle_bytes,
                cell,
                rules,
                jagua_executable,
                freeze_id,
                freeze_sha256,
                expected_jagua_sha256,
                budget.translation_audit_processes_per_cell,
            )
            for item, cell in zip(generated, execution_cells, strict=True)
        )
        checker_phase = _run_process_phase(
            _check_portable_fact_bundle_worker,
            checker_tasks,
            process_count=outer_process_count,
            report_payload_handoff=True,
            aggregate_task_payload_byte_cap=(_M8_GATE3_MAX_CHECKER_TASK_PAYLOAD_BYTES),
        )
        if type(checker_phase) is not _ProcessPhaseExecution:
            raise RuntimeError("M8 portable source checker omitted handoff telemetry")
        checked = _order_portable_worker_results(
            tuple(_strict_portable_check_worker_result(item) for item in checker_phase.results),
            execution_cells,
        )

        retained_sources = []
        for generated_item, checked_item in zip(generated, checked, strict=True):
            retained_source = _retain_portable_fact_checked_source(
                generated_item,
                checked_item,
            )
            if retained_source.first_generation.worker_pid == retained_source.check.worker_pid:
                raise ValueError("M8 portable source phases did not use distinct fresh workers")
            retained_sources.append(retained_source)
        _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    except BaseException as error:
        controller_after_failure = _strict_portable_registry_state(_portable_registry_state())
        if not controller_after_failure.is_clean:
            raise RuntimeError(
                "M8 portable source capture leaked registries during failure"
            ) from error
        raise

    controller_after = _strict_portable_registry_state(_portable_registry_state())
    if not controller_after.is_clean:
        raise RuntimeError("M8 portable source capture leaked registries")
    return tuple(retained_sources)


def _profile_portable_fact_cell(
    cell: _ExecutionCell,
    *,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    budget: M8ConcurrencyBudget,
    source_tree: SourceTreeSnapshot,
    expected_runtime_identity: tuple[str, str] | None = None,
) -> tuple[
    _PortableFactCheckedSource,
    _PortableBundleIdentityWorkerResult,
    M8ProfileReport,
    _ProcessPhaseExecution,
    _ProcessPhaseExecution,
    _ProcessPhaseExecution,
    _PortableFactProfileTiming,
]:
    """Measure one frozen probe with repeated generation and a fresh checker."""

    controller_before = _strict_portable_registry_state(_portable_registry_state())
    if not controller_before.is_clean:
        raise ValueError("M8 portable profile has live registries before spawn")
    if type(source_tree) is not SourceTreeSnapshot:
        raise TypeError("M8 portable profile requires an exact source-tree snapshot")
    _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    controller_runtime_identity = _portable_worker_runtime_identity(
        jagua_executable,
        expected_jagua_sha256,
    )
    if expected_runtime_identity is not None:
        if (
            type(expected_runtime_identity) is not tuple
            or len(expected_runtime_identity) != 2
            or any(type(item) is not str for item in expected_runtime_identity)
        ):
            raise TypeError("M8 portable profile expected runtime identity is malformed")
        if controller_runtime_identity != expected_runtime_identity:
            raise ValueError("M8 portable profile controller runtime identity differs")
    else:
        expected_runtime_identity = controller_runtime_identity
    common_task = (
        cell,
        rules,
        jagua_executable,
        freeze_id,
        freeze_sha256,
        expected_jagua_sha256,
        budget.translation_audit_processes_per_cell,
    )
    pipeline_started = perf_counter()
    try:
        with activate_source_attestation(source_tree):
            phase_started = perf_counter()
            generation_phase = _run_process_phase(
                SourceAttestedOperation(
                    operation=_profile_portable_generation_worker,
                    source_tree=source_tree,
                    expected_module_name="yieldforge.oracle.experiment",
                    expected_function_name="_profile_portable_generation_worker",
                ),
                (common_task,),
                process_count=1,
                report_payload_handoff=True,
            )
            if type(generation_phase) is not _ProcessPhaseExecution:
                raise RuntimeError("M8 portable profile generation omitted handoff telemetry")
            first_generation_phase_wall_seconds = max(
                0.000001,
                perf_counter() - phase_started,
            )
            if len(generation_phase.results) != 1:
                raise RuntimeError("M8 portable profile generation omitted its hard arm")
            profiled_generation = _strict_portable_generation_profile_worker_result(
                generation_phase.results[0]
            )
            generated = profiled_generation.generation

            phase_started = perf_counter()
            repeat_generation_phase = _run_process_phase(
                SourceAttestedOperation(
                    operation=_repeat_portable_generation_profile_worker,
                    source_tree=source_tree,
                    expected_module_name="yieldforge.oracle.experiment",
                    expected_function_name="_repeat_portable_generation_profile_worker",
                ),
                (common_task,),
                process_count=1,
                report_payload_handoff=True,
            )
            if type(repeat_generation_phase) is not _ProcessPhaseExecution:
                raise RuntimeError(
                    "M8 portable profile repeat generation omitted handoff telemetry"
                )
            second_generation_phase_wall_seconds = max(
                0.000001,
                perf_counter() - phase_started,
            )
            if len(repeat_generation_phase.results) != 1:
                raise RuntimeError("M8 portable profile repeat generation omitted its hard arm")
            profiled_repeat = _strict_portable_repeat_generation_profile_worker_result(
                repeat_generation_phase.results[0]
            )
            repeated_generation = _require_portable_profile_repeat(
                generated,
                profiled_repeat.generation,
            )

            phase_started = perf_counter()
            checker_phase = _run_process_phase(
                SourceAttestedOperation(
                    operation=_check_portable_fact_bundle_profile_worker,
                    source_tree=source_tree,
                    expected_module_name="yieldforge.oracle.experiment",
                    expected_function_name="_check_portable_fact_bundle_profile_worker",
                ),
                (
                    (
                        generated.semantic_bundle_bytes,
                        *common_task,
                    ),
                ),
                process_count=1,
                report_payload_handoff=True,
                aggregate_task_payload_byte_cap=(_M8_GATE3_MAX_CHECKER_TASK_PAYLOAD_BYTES),
            )
            if type(checker_phase) is not _ProcessPhaseExecution:
                raise RuntimeError("M8 portable profile checker omitted handoff telemetry")
            checker_phase_wall_seconds = max(
                0.000001,
                perf_counter() - phase_started,
            )
            if len(checker_phase.results) != 1:
                raise RuntimeError("M8 portable profile checker omitted its hard arm")
            profiled_check = _strict_portable_check_profile_worker_result(
                checker_phase.results[0]
            )
            checked = profiled_check.check
            retained = _retain_portable_fact_checked_source(generated, checked)
            if len(
                {
                    generated.worker_pid,
                    repeated_generation.worker_pid,
                    checked.worker_pid,
                }
            ) != 3:
                raise ValueError("M8 portable profile did not use distinct fresh workers")
            observed_runtime_identities = (
                (
                    profiled_generation.runtime_id,
                    profiled_generation.runtime_content_sha256,
                ),
                (profiled_repeat.runtime_id, profiled_repeat.runtime_content_sha256),
                (profiled_check.runtime_id, profiled_check.runtime_content_sha256),
            )
            _require_portable_profile_worker_runtime_identities(
                expected=expected_runtime_identity,
                observed=observed_runtime_identities,
            )
            _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    except BaseException as error:
        controller_after_failure = _strict_portable_registry_state(_portable_registry_state())
        if not controller_after_failure.is_clean:
            raise RuntimeError("M8 portable profile leaked registries during failure") from error
        raise
    controller_after = _strict_portable_registry_state(_portable_registry_state())
    if not controller_after.is_clean:
        raise RuntimeError("M8 portable profile leaked registries")
    timing = _PortableFactProfileTiming(
        first_generation_phase_wall_seconds=first_generation_phase_wall_seconds,
        second_generation_phase_wall_seconds=second_generation_phase_wall_seconds,
        checker_phase_wall_seconds=checker_phase_wall_seconds,
        total_pipeline_wall_seconds=max(0.000001, perf_counter() - pipeline_started),
        generator_runtime_content_sha256=(
            profiled_generation.runtime_content_sha256
        ),
        repeat_generator_runtime_content_sha256=(
            profiled_repeat.runtime_content_sha256
        ),
        checker_runtime_content_sha256=profiled_check.runtime_content_sha256,
    )
    return (
        retained,
        repeated_generation,
        profiled_generation.profile,
        generation_phase,
        repeat_generation_phase,
        checker_phase,
        timing,
    )


def _execute_portable_fact_cells_with_sources(
    execution_cells: tuple[_ExecutionCell, ...],
    *,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    budget: M8ConcurrencyBudget,
) -> tuple[
    _PortableFactPipelineExecution,
    tuple[_PortableFactCheckedSource, ...],
]:
    """Run two unchecked generations and one independent bytes-only check phase."""

    if not execution_cells:
        raise ValueError("M8 portable pipeline requires at least one calibration cell")
    controller_before = _strict_portable_registry_state(_portable_registry_state())
    if not controller_before.is_clean:
        raise ValueError("M8 portable pipeline controller has live registries before spawn")
    _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    outer_process_count = min(budget.cell_phase_processes, len(execution_cells))
    common_tasks = tuple(
        (
            cell,
            rules,
            jagua_executable,
            freeze_id,
            freeze_sha256,
            expected_jagua_sha256,
            budget.translation_audit_processes_per_cell,
        )
        for cell in execution_cells
    )
    pipeline_started = perf_counter()
    try:
        phase_started = perf_counter()
        first_phase = _run_process_phase(
            _generate_portable_fact_bundle_worker,
            tuple((*task, True) for task in common_tasks),
            process_count=outer_process_count,
            report_payload_handoff=True,
        )
        if type(first_phase) is not _ProcessPhaseExecution:
            raise RuntimeError("M8 portable first generation omitted handoff telemetry")
        first_wall = max(0.000001, perf_counter() - phase_started)
        first = _order_portable_worker_results(
            tuple(
                _strict_portable_bundle_identity(item, require_bytes=True)
                for item in first_phase.results
            ),
            execution_cells,
        )
        retained_bundle_bytes = sum(item.semantic_serialized_bytes for item in first)
        if retained_bundle_bytes > _M8_GATE3_MAX_RETAINED_BUNDLE_BYTES:
            raise ValueError("M8 portable retained bundles exceed the aggregate cap")

        phase_started = perf_counter()
        second_phase = _run_process_phase(
            _generate_portable_fact_bundle_worker,
            tuple((*task, False) for task in common_tasks),
            process_count=outer_process_count,
            report_payload_handoff=True,
        )
        if type(second_phase) is not _ProcessPhaseExecution:
            raise RuntimeError("M8 portable second generation omitted handoff telemetry")
        second_wall = max(0.000001, perf_counter() - phase_started)
        second = _order_portable_worker_results(
            tuple(
                _strict_portable_bundle_identity(item, require_bytes=False)
                for item in second_phase.results
            ),
            execution_cells,
        )

        for first_item, second_item in zip(first, second, strict=True):
            if _portable_generation_metadata(first_item) != (
                _portable_generation_metadata(second_item)
            ):
                raise ValueError("M8 portable repeated generation identity differs")

        checker_tasks = tuple(
            (
                generated.semantic_bundle_bytes,
                cell,
                rules,
                jagua_executable,
                freeze_id,
                freeze_sha256,
                expected_jagua_sha256,
                budget.translation_audit_processes_per_cell,
            )
            for generated, cell in zip(first, execution_cells, strict=True)
        )
        phase_started = perf_counter()
        checker_phase = _run_process_phase(
            _check_portable_fact_bundle_worker,
            checker_tasks,
            process_count=outer_process_count,
            report_payload_handoff=True,
            aggregate_task_payload_byte_cap=(_M8_GATE3_MAX_CHECKER_TASK_PAYLOAD_BYTES),
        )
        if type(checker_phase) is not _ProcessPhaseExecution:
            raise RuntimeError("M8 portable checker omitted handoff telemetry")
        checker_wall = max(0.000001, perf_counter() - phase_started)
        checked = _order_portable_worker_results(
            tuple(_strict_portable_check_worker_result(item) for item in checker_phase.results),
            execution_cells,
        )

        cell_results = []
        retained_sources = []
        for first_item, second_item, checked_item in zip(
            first,
            second,
            checked,
            strict=True,
        ):
            retained_source = _retain_portable_fact_checked_source(
                first_item,
                checked_item,
            )
            first_item = retained_source.first_generation
            checked_item = retained_source.check
            if (
                len(
                    {
                        first_item.worker_pid,
                        second_item.worker_pid,
                        checked_item.worker_pid,
                    }
                )
                != 3
            ):
                raise ValueError("M8 portable phases did not use distinct fresh workers")
            cell_results.append(
                _PortableFactCellExecution(
                    first_generation=first_item,
                    second_generation=second_item,
                    check=checked_item,
                )
            )
            retained_sources.append(retained_source)
        _verify_portable_jagua_executable(jagua_executable, expected_jagua_sha256)
    except BaseException as error:
        controller_after_failure = _strict_portable_registry_state(_portable_registry_state())
        if not controller_after_failure.is_clean:
            raise RuntimeError(
                "M8 portable pipeline controller leaked registries during failure"
            ) from error
        raise

    controller_after = _strict_portable_registry_state(_portable_registry_state())
    if not controller_after.is_clean:
        raise RuntimeError("M8 portable pipeline controller leaked registries")
    phases = (first_phase, second_phase, checker_phase)
    pipeline = _PortableFactPipelineExecution(
        cells=tuple(cell_results),
        first_generation_phase_wall_seconds=first_wall,
        second_generation_phase_wall_seconds=second_wall,
        checker_phase_wall_seconds=checker_wall,
        task_serialization_wall_seconds=sum(
            item.task_serialization_wall_seconds for item in phases
        ),
        result_serialization_wall_seconds=sum(
            item.result_serialization_wall_seconds for item in phases
        ),
        inbound_payload_handoff_wall_seconds=sum(
            item.inbound_payload_handoff_wall_seconds for item in phases
        ),
        outbound_payload_handoff_wall_seconds=sum(
            item.outbound_payload_handoff_wall_seconds for item in phases
        ),
        worker_payload_handoff_wall_seconds=sum(
            item.worker_payload_handoff_wall_seconds for item in phases
        ),
        process_exit_validation_wall_seconds=sum(
            item.process_exit_validation_wall_seconds for item in phases
        ),
        worker_task_payload_bytes=sum(item.task_payload_bytes for item in phases),
        worker_result_payload_bytes=sum(item.result_payload_bytes for item in phases),
        per_worker_task_payload_byte_cap=_M8_GATE3_MAX_WORKER_TASK_BYTES,
        per_worker_result_payload_byte_cap=_M8_GATE3_MAX_WORKER_RESULT_BYTES,
        retained_first_generation_bundle_bytes=retained_bundle_bytes,
        retained_first_generation_bundle_byte_cap=(_M8_GATE3_MAX_RETAINED_BUNDLE_BYTES),
        checker_task_payload_bytes=checker_phase.task_payload_bytes,
        checker_task_payload_byte_cap=_M8_GATE3_MAX_CHECKER_TASK_PAYLOAD_BYTES,
        total_pipeline_wall_seconds=max(0.000001, perf_counter() - pipeline_started),
        outer_process_count=outer_process_count,
        nested_process_count=budget.translation_audit_processes_per_cell,
        peak_compute_count=(outer_process_count * budget.translation_audit_processes_per_cell),
        controller_registry_state_before=controller_before,
        controller_registry_state_after=controller_after,
    )
    return pipeline, tuple(retained_sources)


def _execute_portable_fact_cells(
    execution_cells: tuple[_ExecutionCell, ...],
    *,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path | None,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str | None,
    budget: M8ConcurrencyBudget,
) -> _PortableFactPipelineExecution:
    """Compatibility path that deliberately discards private retained sources."""

    pipeline, _retained_sources = _execute_portable_fact_cells_with_sources(
        execution_cells,
        rules=rules,
        jagua_executable=jagua_executable,
        freeze_id=freeze_id,
        freeze_sha256=freeze_sha256,
        expected_jagua_sha256=expected_jagua_sha256,
        budget=budget,
    )
    return pipeline


def _generate_cell_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    translation_audit_processes: int,
) -> _SparsePreflightResult:
    """Generate one complete cell proof batch in an owned worker process."""

    with activate_m8_translation_audit_processes(translation_audit_processes):
        request = _request_for_cell(
            cell,
            rules=rules,
            jagua_executable=jagua_executable,
        )
        sparse, sparse_elapsed = _measure_proof_phase(lambda: score_sparse_event(request))
    return _SparsePreflightResult(
        cell=cell,
        sparse=sparse,
        elapsed_seconds=max(0.000001, round(sparse_elapsed, 6)),
    )


def _check_cell_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    proofs: tuple[M8ActionProof, ...],
    translation_audit_processes: int,
) -> _FullCheckerResult:
    """Check one complete proof batch in a fresh owned worker process."""

    with activate_m8_translation_audit_processes(translation_audit_processes):
        request = _request_for_cell(
            cell,
            rules=rules,
            jagua_executable=jagua_executable,
        )
        checks, elapsed = _measure_proof_phase(lambda: check_action_proofs(request, proofs))
    return _FullCheckerResult(
        regime=cell.stream[0].regime,
        checks=checks,
        elapsed_seconds=max(0.000001, round(elapsed, 6)),
    )


def _sample_audit_generator_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    audit_bindings: tuple[M8AuditActionBinding, ...],
    translation_audit_processes: int,
) -> _SampleAuditGeneratorResult:
    """Regenerate one frozen per-regime certificate batch in a fresh process."""

    return _gate3_v1_generator_worker(
        cell,
        rules,
        jagua_executable,
        tuple(item.catalog_action_id for item in audit_bindings),
        translation_audit_processes,
    )


def _gate3_v1_generator_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    action_ids: tuple[str, ...],
    translation_audit_processes: int,
) -> _SampleAuditGeneratorResult:
    """Generate the exact frozen Gate-3 v1 action vector in a fresh process."""

    if type(action_ids) is not tuple or any(type(item) is not str for item in action_ids):
        raise TypeError("M8 Gate-3 v1 action IDs must be an exact tuple of strings")
    if not action_ids or len(action_ids) != len(set(action_ids)):
        raise ValueError("M8 Gate-3 v1 action IDs must be unique nonempty values")
    for action_id in action_ids:
        _action_kind(action_id)
    with activate_m8_translation_audit_processes(translation_audit_processes):
        request = _request_for_cell(
            cell,
            rules=rules,
            jagua_executable=jagua_executable,
        )
        sampled, certificate_elapsed = _measure_proof_phase(
            lambda: score_certificate_actions(request, action_ids=action_ids)
        )
    if tuple(item.score.action_id for item in sampled) != action_ids:
        raise ValueError("M8 sampled generator worker returned different actions")
    return _SampleAuditGeneratorResult(
        regime=cell.stream[0].regime,
        sampled=sampled,
        elapsed_seconds=max(0.000001, round(certificate_elapsed, 6)),
    )


def _sample_audit_checker_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    proofs: tuple[M8ActionProof, ...],
    translation_audit_processes: int,
) -> _SampleAuditCheckerResult:
    """Check one frozen per-regime proof batch in a separate fresh process."""

    with activate_m8_translation_audit_processes(translation_audit_processes):
        request = _request_for_cell(
            cell,
            rules=rules,
            jagua_executable=jagua_executable,
        )
        checks, elapsed = _measure_proof_phase(lambda: check_action_proofs(request, proofs))
    if len(checks) != len(proofs):
        raise ValueError("M8 sampled checker worker returned a different action count")
    return _SampleAuditCheckerResult(
        regime=cell.stream[0].regime,
        checks=checks,
        elapsed_seconds=max(0.000001, round(elapsed, 6)),
    )


def _reference_audit_action_worker(
    cell: _ExecutionCell,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    action_id: str,
) -> _ReferenceAuditActionResult:
    """Brute-score one frozen audit action in a fresh owned process."""

    request = _request_for_cell(
        cell,
        rules=rules,
        jagua_executable=jagua_executable,
    )
    score, elapsed = _measure_proof_phase(
        lambda: score_reference_action(request, action_id=action_id)
    )
    if score.action_id != action_id:
        raise ValueError("M8 reference audit worker returned a different action")
    return _ReferenceAuditActionResult(
        regime=cell.stream[0].regime,
        score=score,
        elapsed_seconds=max(0.000001, round(elapsed, 6)),
    )


def _assemble_reference_audit_actions(
    results: tuple[_ReferenceAuditActionResult, ...],
    *,
    audit_by_cell: dict[TemporalRegime, tuple[M8AuditActionBinding, ...]],
) -> tuple[_ReferenceAuditCellResult, ...]:
    """Restore frozen per-regime vectors after independent reference branches."""

    regimes = tuple(regime for regime in TemporalRegime if regime in audit_by_cell)
    expected = tuple(
        (regime, binding.catalog_action_id)
        for regime in regimes
        for binding in audit_by_cell[regime]
    )
    by_key = {(item.regime, item.score.action_id): item for item in results}
    if (
        len(results) != len(expected)
        or len(by_key) != len(expected)
        or set(by_key) != set(expected)
    ):
        raise ValueError("M8 split audit reference actions are incomplete or duplicate")
    return tuple(
        _ReferenceAuditCellResult(
            regime=regime,
            scores=tuple(
                by_key[(regime, binding.catalog_action_id)].score
                for binding in audit_by_cell[regime]
            ),
            elapsed_seconds=round(
                sum(
                    by_key[(regime, binding.catalog_action_id)].elapsed_seconds
                    for binding in audit_by_cell[regime]
                ),
                6,
            ),
        )
        for regime in regimes
    )


def _assemble_audit_results(
    *,
    sampled: tuple[_SampleAuditGeneratorResult, ...],
    checked: tuple[_SampleAuditCheckerResult, ...],
    references: tuple[_ReferenceAuditCellResult, ...],
    audit_by_cell: dict[TemporalRegime, tuple[M8AuditActionBinding, ...]],
) -> tuple[_AuditPhaseResult, ...]:
    """Reassemble split audit phases only after exact frozen-ID reconciliation."""

    regimes = tuple(regime for regime in TemporalRegime if regime in audit_by_cell)
    if not regimes or any(not audit_by_cell[regime] for regime in regimes):
        raise ValueError("M8 split audit bindings require nonempty registered regimes")

    def order_phase(results, phase: str):  # type: ignore[no-untyped-def]
        by_regime = {}
        for item in results:
            if item.regime in by_regime:
                raise ValueError(f"M8 split audit {phase} are duplicate")
            by_regime[item.regime] = item
        if set(by_regime) != set(regimes) or len(results) != len(regimes):
            raise ValueError(f"M8 split audit {phase} are incomplete")
        return tuple(by_regime[regime] for regime in regimes)

    ordered_sampled = order_phase(sampled, "sampled generator")
    ordered_checked = order_phase(checked, "sampled checker")
    ordered_references = order_phase(references, "reference actions")

    assembled = []
    for regime, sampled_item, checked_item, reference_item in zip(
        regimes,
        ordered_sampled,
        ordered_checked,
        ordered_references,
        strict=True,
    ):
        bindings = audit_by_cell[regime]
        action_ids = tuple(item.catalog_action_id for item in bindings)
        if (
            tuple(item.score.action_id for item in sampled_item.sampled) != action_ids
            or len(checked_item.checks) != len(action_ids)
            or tuple(item.action_id for item in reference_item.scores) != action_ids
        ):
            raise ValueError("M8 split audit actions differ from the frozen batch")
        assembled.append(
            _AuditPhaseResult(
                regime=regime,
                sampled=sampled_item.sampled,
                sampled_checks=checked_item.checks,
                reference_scores=reference_item.scores,
                sampled_certificate_elapsed_seconds=sampled_item.elapsed_seconds,
                sampled_checker_elapsed_seconds=checked_item.elapsed_seconds,
                sampled_reference_elapsed_seconds=reference_item.elapsed_seconds,
            )
        )
    return tuple(assembled)


def _proof_classifications(
    proof: M8ActionProof,
) -> tuple[M8EventClassification, ...]:
    return _classification_tuple({item.classification for item in proof.witnesses})


def _candidate_labels(
    binding: M8AuditActionBinding,
    *,
    horizon_targets: set[int],
) -> frozenset[tuple[str, str, str]]:
    regime = binding.regime.value
    labels: set[tuple[str, str, str]] = {
        ("regime", regime, ""),
        ("kind", regime, binding.action_kind),
    }
    labels.update(
        ("witness", regime, classification) for classification in binding.witness_classifications
    )
    if binding.future_event_count in horizon_targets:
        labels.add(("horizon", str(binding.future_event_count), ""))
    return frozenset(labels)


def _freeze_audit_bindings(
    candidates: tuple[M8AuditActionBinding, ...],
) -> tuple[M8AuditActionBinding, ...]:
    """Select a deterministic minimum-cover-style audit before timing begins."""

    horizons = sorted({item.future_event_count for item in candidates})
    horizon_targets = (
        {horizons[0], horizons[len(horizons) // 2], horizons[-1]} if horizons else set()
    )
    choices = tuple(
        _AuditCandidate(
            binding=item,
            labels=_candidate_labels(item, horizon_targets=horizon_targets),
        )
        for item in sorted(candidates, key=_audit_binding_key)
    )
    required = {label for item in choices for label in item.labels}
    uncovered = set(required)
    selected: list[M8AuditActionBinding] = []
    remaining = list(choices)
    while uncovered:
        best = min(
            remaining,
            key=lambda item: (
                -len(item.labels & uncovered),
                _audit_binding_key(item.binding),
            ),
        )
        contribution = best.labels & uncovered
        if not contribution:
            raise ValueError("M8 deterministic audit cannot cover its present strata")
        selected.append(best.binding)
        uncovered -= contribution
        remaining.remove(best)
    frozen = tuple(sorted(selected, key=_audit_binding_key))
    if {
        label
        for item in frozen
        for label in _candidate_labels(item, horizon_targets=horizon_targets)
    } != required:
        raise ValueError("M8 deterministic audit omitted a present required stratum")
    return frozen


def _request_for_cell(
    cell: _ExecutionCell,
    *,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
) -> M8OracleRequest:
    runtime = _runtime(
        cell.replay_input,
        cell.verified,
        rules,
        jagua_executable,
    )
    return M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(cell.replay_input),  # type: ignore[arg-type]
        visibility=FullRealizedVisibility(cell.replay_input.instances),  # type: ignore[attr-defined]
    )


def _audit_candidates_for_cell(
    stream: tuple[TemporalInstanceBinding, ...],
    sparse: M8SparseResult,
) -> tuple[M8AuditActionBinding, ...]:
    if not sparse.proofs:
        raise ValueError("M8 certificate preflight produced no current action proofs")
    runtime_hashes = {item.semantic_runtime_sha256 for item in sparse.proofs}
    if len(runtime_hashes) != 1:
        raise ValueError("M8 certificate preflight proof runtime bindings differ")
    future_event_count = sparse.proofs[0].stop_event_position - (
        sparse.proofs[0].start_event_position + 1
    )
    bindings = []
    for proof in sparse.proofs:
        classifications = _proof_classifications(proof)
        if not classifications:
            raise ValueError("M8 certificate audit cannot bind an empty witness suffix")
        bindings.append(
            M8AuditActionBinding(
                regime=stream[0].regime,
                temporal_seed=stream[0].temporal_seed,
                stream_id=stream[0].stream_id,
                future_event_count=future_event_count,
                semantic_runtime_sha256=proof.semantic_runtime_sha256,
                catalog_action_id=proof.catalog_action_id,
                action_kind=_action_kind(proof.catalog_action_id),
                proof_id=proof.proof_id,
                witness_classifications=classifications,
            )
        )
    return tuple(bindings)


def _freeze_preflight_audit(
    preflights: tuple[_SparsePreflightResult, ...],
) -> tuple[M8AuditActionBinding, ...]:
    """Freeze only semantic proof strata; elapsed observations cannot affect selection."""

    candidates = tuple(
        candidate
        for preflight in preflights
        for candidate in _audit_candidates_for_cell(
            preflight.cell.stream,
            preflight.sparse,
        )
    )
    return _freeze_audit_bindings(candidates)


def _build_execution_cells(
    *,
    index: M7CalibrationProblemView,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    verified,  # type: ignore[no-untyped-def]
    selected_streams: list[tuple[TemporalInstanceBinding, ...]],
) -> tuple[_ExecutionCell, ...]:
    contract = build_registered_contract()
    problem_by_id = {item.problem_id: item for item in index.problems}
    cells = []
    for stream in selected_streams:
        problem_ids = tuple(sorted({item.problem_id for item in stream}))
        replay_input = build_m7_replay_input(
            m0_contract_id=m0.contract_id,
            m0_contract_sha256=m0.content_sha256,
            problem_index_id=index.full_problem_index_id,
            problem_index_sha256=index.full_problem_index_sha256,
            m6_contract_id=index.m6_contract_id,
            m6_contract_sha256=index.m6_contract_sha256,
            m6_population_id=index.m6_population_id,
            m6_population_sha256=index.m6_population_sha256,
            policy=frozen.winning_policy,
            rates=contract.rates,
            fit_config=RemnantFitConfig(),
            problems=tuple(problem_by_id[item] for item in problem_ids),
            candidate_sets=tuple(verified[item].evidence for item in problem_ids),
            instances=stream,
            horizon_end=stream[-1].released_at
            + timedelta(minutes=contract.timing.interval_minutes),
            collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
            jagua_container_guard=1.0,
        )
        cells.append(
            _ExecutionCell(
                stream=stream,
                problem_ids=problem_ids,
                replay_input=replay_input,
                verified={item: verified[item] for item in problem_ids},
            )
        )
    return tuple(cells)


def _select_portable_gate3_probe_streams(
    index: M7CalibrationProblemView,
) -> tuple[tuple[TemporalInstanceBinding, ...], ...]:
    """Select only the frozen hard/easy two-event calibration probes."""

    if type(index) is not M7CalibrationProblemView:
        raise TypeError("M8 portable Gate-3 requires an exact calibration view")
    if index.evaluation_partition_opened:
        raise ValueError("M8 portable Gate-3 cannot open evaluation bindings")
    calibration = select_calibration_instances(index)
    selected = []
    for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT):
        matching = tuple(
            item
            for item in calibration
            if item.regime is regime and item.temporal_seed == 2026082300
        )
        stream_ids = tuple(sorted({item.stream_id for item in matching}))
        if len(stream_ids) != 1:
            raise ValueError("M8 portable Gate-3 probe selector is not unique")
        stream = tuple(
            sorted(
                (item for item in matching if item.stream_id == stream_ids[0]),
                key=lambda item: (item.sequence, item.binding_id),
            )
        )
        if len(stream) != 24:
            raise ValueError("M8 portable Gate-3 calibration stream does not contain 24 events")
        if tuple(item.sequence for item in stream) != tuple(range(24)):
            raise ValueError("M8 portable Gate-3 calibration stream positions differ")
        selected.append(stream[:2])
    return tuple(selected)


def _assemble_timed_cell(
    generated: _SparsePreflightResult,
    *,
    checked: _FullCheckerResult,
    audited: _AuditPhaseResult,
    audit_bindings: tuple[M8AuditActionBinding, ...],
) -> M8CertificateProofCell:
    """Reconcile independently generated, checked, and audited cell evidence."""

    if not (generated.regime is checked.regime and generated.regime is audited.regime):
        raise ValueError("M8 distributed worker regime identities do not reconcile")
    sparse = generated.sparse
    checks = checked.checks
    current_action_ids = tuple(item.action_id for item in sparse.decision.scores)
    proof_ids = tuple(item.catalog_action_id for item in sparse.proofs)
    if (
        len(sparse.proofs) != sparse.decision.scored_action_count
        or proof_ids != current_action_ids
        or len(set(proof_ids)) != len(proof_ids)
        or len(checks) != len(sparse.proofs)
    ):
        raise ValueError("M8 certificate batch does not cover its exact current catalog")

    timed_by_id = {item.catalog_action_id: item for item in sparse.proofs}
    for binding in audit_bindings:
        proof = timed_by_id.get(binding.catalog_action_id)
        if proof is None or (
            proof.proof_id,
            proof.semantic_runtime_sha256,
            _proof_classifications(proof),
        ) != (
            binding.proof_id,
            binding.semantic_runtime_sha256,
            binding.witness_classifications,
        ):
            raise ValueError("M8 timed certificate differs from its pre-timing audit freeze")

    audit_action_ids = tuple(item.catalog_action_id for item in audit_bindings)
    sampled = audited.sampled
    sampled_checks = audited.sampled_checks
    reference_scores = audited.reference_scores
    if (
        tuple(item.score.action_id for item in sampled) != audit_action_ids
        or len(sampled_checks) != len(audit_bindings)
        or tuple(item.action_id for item in reference_scores) != audit_action_ids
    ):
        raise ValueError("M8 matched audit batch differs from its frozen audit IDs")
    sampled_by_id = {item.score.action_id: item for item in sampled}
    for binding in audit_bindings:
        sampled_item = sampled_by_id[binding.catalog_action_id]
        if (
            sampled_item.proof.proof_id,
            sampled_item.proof.semantic_runtime_sha256,
            _proof_classifications(sampled_item.proof),
        ) != (
            binding.proof_id,
            binding.semantic_runtime_sha256,
            binding.witness_classifications,
        ):
            raise ValueError("M8 matched certificate differs from its pre-timing audit freeze")

    full_scores = {item.action_id: item.final_net_cost for item in sparse.decision.scores}
    audit_mismatches = sum(
        (
            item.final_net_cost != sampled_by_id[item.action_id].score.final_net_cost
            or item.final_net_cost != full_scores[item.action_id]
        )
        for item in reference_scores
    )
    witness_classifications = _classification_tuple(
        {
            classification
            for proof in sparse.proofs
            for classification in _proof_classifications(proof)
        }
    )
    valid = sum(item.valid for item in checks)
    proof_runtime_hashes = {item.semantic_runtime_sha256 for item in sparse.proofs}
    if len(proof_runtime_hashes) != 1:
        raise ValueError("M8 timed certificate proof runtime bindings differ")
    return M8CertificateProofCell(
        regime=generated.regime,
        temporal_seed=generated.cell.stream[0].temporal_seed,
        stream_id=generated.cell.stream[0].stream_id,
        future_event_count=next(iter(audit_bindings)).future_event_count,
        semantic_runtime_sha256=next(iter(proof_runtime_hashes)),
        audit_action_ids=audit_action_ids,
        audit_sample_sha256=audit_sample_sha256(audit_bindings),
        current_action_kinds=tuple(sorted({_action_kind(item) for item in current_action_ids})),
        current_action_ids=tuple(sorted(current_action_ids)),
        proof_catalog_action_ids=tuple(sorted(proof_ids)),
        current_action_count=len(current_action_ids),
        checked_action_count=len(checks),
        valid_proof_count=valid,
        checker_failure_count=len(checks) - valid,
        audit_mismatch_count=audit_mismatches,
        witness_classifications=witness_classifications,
        certified_event_count=sum(
            witness.classification in {"no_fit", "policy_dominated"}
            for proof in sparse.proofs
            for witness in proof.witnesses
        ),
        exact_escape_count=sum(
            witness.classification == "exact_transition"
            for proof in sparse.proofs
            for witness in proof.witnesses
        ),
        state_rejoin_count=sum(
            witness.classification == "state_rejoin"
            for proof in sparse.proofs
            for witness in proof.witnesses
        ),
        certificate_elapsed_seconds=generated.elapsed_seconds,
        checker_elapsed_seconds=checked.elapsed_seconds,
        sampled_certificate_elapsed_seconds=(audited.sampled_certificate_elapsed_seconds),
        sampled_checker_elapsed_seconds=audited.sampled_checker_elapsed_seconds,
        sampled_checker_failure_count=sum(not item.valid for item in sampled_checks),
        sampled_reference_elapsed_seconds=(audited.sampled_reference_elapsed_seconds),
    )


@dataclass(frozen=True)
class _DistributedCellExecution:
    cells: tuple[M8CertificateProofCell, ...]
    audit_bindings: tuple[M8AuditActionBinding, ...]
    measured_process_count: int
    cell_phase_process_count: int
    translation_audit_processes_per_cell: int
    reference_phase_process_count: int
    peak_compute_count: int
    generator_wall_seconds: float
    checker_wall_seconds: float
    audit_wall_seconds: float
    total_wall_seconds: float


def _execute_distributed_cells(
    execution_cells: tuple[_ExecutionCell, ...],
    *,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    budget: M8ConcurrencyBudget,
    progress=None,  # type: ignore[no-untyped-def]
) -> _DistributedCellExecution:
    """Run generation, checking, and three split audit phases in fresh pools."""

    if len(execution_cells) != len(TemporalRegime):
        raise ValueError("M8 distributed execution requires all six regime cells")
    cell_process_count = min(budget.cell_phase_processes, len(execution_cells))
    total_started = perf_counter()

    if progress is not None:
        progress(
            f"phase_start regime=all phase=distributed_generator processes={cell_process_count}"
        )
    phase_started = perf_counter()
    generated = _order_worker_results(
        _run_process_phase(
            _generate_cell_worker,
            tuple(
                (
                    cell,
                    rules,
                    jagua_executable,
                    budget.translation_audit_processes_per_cell,
                )
                for cell in execution_cells
            ),
            process_count=cell_process_count,
        )
    )
    generator_wall_seconds = max(
        0.000001,
        round(perf_counter() - phase_started, 6),
    )
    if progress is not None:
        for item in generated:
            progress(
                f"phase_complete regime={item.regime.value} "
                "phase=distributed_generator "
                f"actions={len(item.sparse.proofs)} "
                f"worker_seconds={item.elapsed_seconds}"
            )
        progress(
            "phase_complete regime=all phase=distributed_generator "
            f"wall_seconds={generator_wall_seconds}"
        )

    if progress is not None:
        progress("phase_start regime=all phase=audit_freeze")
    frozen_audit = _freeze_preflight_audit(generated)
    if progress is not None:
        progress(
            f"phase_complete regime=all phase=audit_freeze actions={len(frozen_audit)} "
            f"sample={audit_sample_sha256(frozen_audit)}"
        )
    audit_by_cell = _audit_by_cell(frozen_audit)
    generated_by_regime = {item.regime: item for item in generated}
    audit_regime_schedule = tuple(
        sorted(
            TemporalRegime,
            key=lambda regime: (
                -generated_by_regime[regime].elapsed_seconds,
                tuple(TemporalRegime).index(regime),
            ),
        )
    )
    audit_cell_tasks = tuple(
        (
            generated_by_regime[regime].cell,
            rules,
            jagua_executable,
            audit_by_cell[regime],
            budget.translation_audit_processes_per_cell,
        )
        for regime in audit_regime_schedule
    )
    audit_process_count = min(budget.cell_phase_processes, len(audit_cell_tasks))

    if progress is not None:
        progress(f"phase_start regime=all phase=distributed_checker processes={cell_process_count}")
    phase_started = perf_counter()
    checked = _order_worker_results(
        _run_process_phase(
            _check_cell_worker,
            tuple(
                (
                    item.cell,
                    rules,
                    jagua_executable,
                    item.sparse.proofs,
                    budget.translation_audit_processes_per_cell,
                )
                for item in generated
            ),
            process_count=cell_process_count,
        )
    )
    checker_wall_seconds = max(
        0.000001,
        round(perf_counter() - phase_started, 6),
    )
    if progress is not None:
        for item in checked:
            progress(
                f"phase_complete regime={item.regime.value} "
                "phase=distributed_checker "
                f"actions={len(item.checks)} worker_seconds={item.elapsed_seconds}"
            )
        progress(
            "phase_complete regime=all phase=distributed_checker "
            f"wall_seconds={checker_wall_seconds}"
        )

    audit_wall_seconds = 0.0
    if progress is not None:
        progress(
            "phase_start regime=all phase=distributed_audit_generator "
            f"processes={audit_process_count} actions={len(frozen_audit)}"
        )
    phase_started = perf_counter()
    sampled = _run_process_phase(
        _sample_audit_generator_worker,
        audit_cell_tasks,
        process_count=audit_process_count,
    )
    sampled_generator_wall_seconds = max(
        0.000001,
        round(perf_counter() - phase_started, 6),
    )
    audit_wall_seconds += sampled_generator_wall_seconds
    if progress is not None:
        for item in sampled:
            progress(
                f"phase_complete regime={item.regime.value} "
                "phase=distributed_audit_generator "
                f"actions={len(item.sampled)} worker_seconds={item.elapsed_seconds}"
            )
        progress(
            "phase_complete regime=all phase=distributed_audit_generator "
            f"wall_seconds={sampled_generator_wall_seconds}"
        )

    if progress is not None:
        progress(
            "phase_start regime=all phase=distributed_audit_checker "
            f"processes={audit_process_count} actions={len(sampled)}"
        )
    phase_started = perf_counter()
    sampled_checked = _run_process_phase(
        _sample_audit_checker_worker,
        tuple(
            (
                generated_by_regime[sampled_item.regime].cell,
                rules,
                jagua_executable,
                tuple(item.proof for item in sampled_item.sampled),
                budget.translation_audit_processes_per_cell,
            )
            for sampled_item in sampled
        ),
        process_count=audit_process_count,
    )
    sampled_checker_wall_seconds = max(
        0.000001,
        round(perf_counter() - phase_started, 6),
    )
    audit_wall_seconds += sampled_checker_wall_seconds
    if progress is not None:
        for item in sampled_checked:
            progress(
                f"phase_complete regime={item.regime.value} "
                "phase=distributed_audit_checker "
                f"actions={len(item.checks)} worker_seconds={item.elapsed_seconds}"
            )
        progress(
            "phase_complete regime=all phase=distributed_audit_checker "
            f"wall_seconds={sampled_checker_wall_seconds}"
        )

    reference_tasks = tuple(
        (
            generated_by_regime[regime].cell,
            rules,
            jagua_executable,
            binding.catalog_action_id,
        )
        for regime in audit_regime_schedule
        for binding in audit_by_cell[regime]
    )
    reference_process_count = min(
        budget.reference_phase_processes,
        len(reference_tasks),
    )
    if progress is not None:
        progress(
            "phase_start regime=all phase=distributed_audit_reference "
            f"processes={reference_process_count} actions={len(frozen_audit)}"
        )
    phase_started = perf_counter()
    reference_actions = _run_process_phase(
        _reference_audit_action_worker,
        reference_tasks,
        process_count=reference_process_count,
    )
    references = _assemble_reference_audit_actions(
        reference_actions,
        audit_by_cell=audit_by_cell,
    )
    reference_wall_seconds = max(
        0.000001,
        round(perf_counter() - phase_started, 6),
    )
    audit_wall_seconds = round(audit_wall_seconds + reference_wall_seconds, 6)
    if progress is not None:
        for item in references:
            progress(
                f"phase_complete regime={item.regime.value} "
                "phase=distributed_audit_reference "
                f"actions={len(item.scores)} worker_seconds={item.elapsed_seconds}"
            )
        progress(
            "phase_complete regime=all phase=distributed_audit_reference "
            f"wall_seconds={reference_wall_seconds}"
        )

    audited = _assemble_audit_results(
        sampled=sampled,
        checked=sampled_checked,
        references=references,
        audit_by_cell=audit_by_cell,
    )
    measured_process_count = max(
        cell_process_count,
        audit_process_count,
        reference_process_count,
    )
    if progress is not None:
        progress(
            f"phase_complete regime=all phase=distributed_audit wall_seconds={audit_wall_seconds}"
        )

    cells = tuple(
        _assemble_timed_cell(
            generated_item,
            checked=checked_item,
            audited=audited_item,
            audit_bindings=audit_by_cell[generated_item.regime],
        )
        for generated_item, checked_item, audited_item in zip(
            generated,
            checked,
            audited,
            strict=True,
        )
    )
    phase_sum = round(
        generator_wall_seconds + checker_wall_seconds + audit_wall_seconds,
        6,
    )
    total_wall_seconds = max(
        phase_sum,
        max(0.000001, round(perf_counter() - total_started, 6)),
    )
    return _DistributedCellExecution(
        cells=cells,
        audit_bindings=frozen_audit,
        measured_process_count=measured_process_count,
        cell_phase_process_count=cell_process_count,
        translation_audit_processes_per_cell=(budget.translation_audit_processes_per_cell),
        reference_phase_process_count=reference_process_count,
        peak_compute_count=budget.peak_compute,
        generator_wall_seconds=generator_wall_seconds,
        checker_wall_seconds=checker_wall_seconds,
        audit_wall_seconds=audit_wall_seconds,
        total_wall_seconds=total_wall_seconds,
    )


def execute_sparse_prefix_proof(
    *,
    index: M7CalibrationProblemView,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    archive_roots: tuple[Path, ...],
    jagua_executable: Path,
    progress=None,  # type: ignore[no-untyped-def]
) -> M8CertificateProofResult:
    """Run the revised calibration-only gate without loading evaluation streams."""

    gate_started = perf_counter()
    contract = build_registered_contract()
    if (
        (index.full_problem_index_id, index.full_problem_index_sha256)
        != (frozen.problem_index_id, frozen.problem_index_sha256)
        or (m0.contract_id, m0.content_sha256) != (frozen.m0_contract_id, frozen.m0_contract_sha256)
        or (index.m6_contract_id, index.m6_contract_sha256)
        != (contract.contract_id, contract.content_sha256)
        or index.evaluation_partition_opened
    ):
        raise ValueError("M8 certificate proof inputs do not share the frozen M0/M6/M7 boundary")
    executable = Path(jagua_executable)
    metadata = executable.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M8 certificate proof Jagua runtime must be a regular file")
    executable_sha = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    if executable_sha != frozen.runtime.jagua_executable_sha256:
        raise ValueError("M8 certificate proof Jagua runtime differs from the M7 freeze")

    calibration = select_calibration_instances(index)
    selected_streams: list[tuple[TemporalInstanceBinding, ...]] = []
    for regime in TemporalRegime:
        candidates = tuple(item for item in calibration if item.regime is regime)
        seed = min(item.temporal_seed for item in candidates)
        stream_id = next(item.stream_id for item in candidates if item.temporal_seed == seed)
        stream = tuple(item for item in candidates if item.stream_id == stream_id)
        if len(stream) != 24:
            raise ValueError("M8 certificate calibration stream does not contain 24 events")
        selected_streams.append(stream[:_PREFIX_EVENT_COUNT])

    problem_by_id = {item.problem_id: item for item in index.problems}
    selected_problem_ids = sorted(
        {item.problem_id for stream in selected_streams for item in stream}
    )
    references_by_task: dict[int, list[object]] = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)
    verified = {}
    if progress is not None:
        progress(
            f"phase_start regime=all phase=candidate_verification "
            f"problems={len(selected_problem_ids)}"
        )
    with profile_phase("candidate_verification"):
        for offset, problem_id in enumerate(selected_problem_ids, start=1):
            problem = problem_by_id[problem_id]
            verified[problem_id] = verify_problem_candidates(
                problem,
                tuple(references_by_task[problem.tasks_index]),  # type: ignore[arg-type]
                archive_roots,
            )
            increment_profile_count(
                "candidates",
                len(verified[problem_id].candidates),
            )
            if progress is not None:
                progress(
                    f"verified certificate candidate problem {offset}/{len(selected_problem_ids)}"
                )
    if progress is not None:
        progress(
            f"phase_complete regime=all phase=candidate_verification "
            f"problems={len(selected_problem_ids)}"
        )

    calibration_problem_ids = sorted({item.problem_id for item in calibration})
    frozen_by_problem = {
        problem_id: (candidate_id, candidate_sha)
        for problem_id, candidate_id, candidate_sha in zip(
            calibration_problem_ids,
            frozen.candidate_set_ids,
            frozen.candidate_set_sha256s,
            strict=True,
        )
    }
    for problem_id, candidates in verified.items():
        if (
            candidates.evidence.candidate_set_id,
            candidates.evidence.content_sha256,
        ) != frozen_by_problem[problem_id]:
            raise ValueError("M8 certificate candidate evidence differs from the M7 freeze")

    execution_cells = _build_execution_cells(
        index=index,
        m0=m0,
        frozen=frozen,
        verified=verified,
        selected_streams=selected_streams,
    )
    rules = rule_set_from_m0(m0.remnant_eligibility)
    distributed = _execute_distributed_cells(
        execution_cells,
        rules=rules,
        jagua_executable=executable,
        budget=M8_GATE3_CONCURRENCY_BUDGET,
        progress=progress,
    )
    measured_total_wall = max(
        distributed.total_wall_seconds,
        round(perf_counter() - gate_started, 6),
    )
    return finalize_certificate_proof(
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        m6_contract_id=index.m6_contract_id,
        m6_contract_sha256=index.m6_contract_sha256,
        m6_population_id=index.m6_population_id,
        m6_population_sha256=index.m6_population_sha256,
        problem_index_id=index.full_problem_index_id,
        problem_index_sha256=index.full_problem_index_sha256,
        freeze_id=frozen.freeze_id,
        freeze_sha256=frozen.content_sha256,
        calibration_view_id=index.view_id,
        calibration_view_sha256=index.content_sha256,
        cells=distributed.cells,
        audit_bindings=distributed.audit_bindings,
        measured_process_count=distributed.measured_process_count,
        generator_wall_seconds=distributed.generator_wall_seconds,
        checker_wall_seconds=distributed.checker_wall_seconds,
        audit_wall_seconds=distributed.audit_wall_seconds,
        total_wall_seconds=measured_total_wall,
    )


def execute_portable_fact_gate3(
    *,
    index: M7CalibrationProblemView,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    archive_roots: tuple[Path, ...],
    jagua_executable: Path,
    progress=None,  # type: ignore[no-untyped-def]
) -> M8PortableFactGate3Result:
    """Run the separate frozen two-probe portable-fact calibration pipeline."""

    if (
        type(index) is not M7CalibrationProblemView
        or type(m0) is not M0ExperimentContract
        or type(frozen) is not M7FrozenBaseline
    ):
        raise TypeError("M8 portable Gate-3 requires exact frozen input contracts")
    index = M7CalibrationProblemView.model_validate(
        index.model_dump(mode="python"),
        strict=True,
    )
    m0 = M0ExperimentContract.model_validate(
        m0.model_dump(mode="python"),
        strict=True,
    )
    frozen = M7FrozenBaseline.model_validate(
        frozen.model_dump(mode="python"),
        strict=True,
    )
    contract = build_registered_contract()
    if (
        (index.full_problem_index_id, index.full_problem_index_sha256)
        != (frozen.problem_index_id, frozen.problem_index_sha256)
        or (m0.contract_id, m0.content_sha256) != (frozen.m0_contract_id, frozen.m0_contract_sha256)
        or (index.m6_contract_id, index.m6_contract_sha256)
        != (contract.contract_id, contract.content_sha256)
        or index.evaluation_partition_opened
    ):
        raise ValueError("M8 portable Gate-3 inputs differ from the sealed boundary")
    executable = Path(jagua_executable)
    metadata = executable.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M8 portable Gate-3 Jagua runtime must be a regular file")
    executable_sha = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    if executable_sha != frozen.runtime.jagua_executable_sha256:
        raise ValueError("M8 portable Gate-3 Jagua runtime differs from the M7 freeze")

    selected_streams = _select_portable_gate3_probe_streams(index)
    calibration = select_calibration_instances(index)
    problem_by_id = {item.problem_id: item for item in index.problems}
    selected_problem_ids = tuple(
        sorted({item.problem_id for stream in selected_streams for item in stream})
    )
    references_by_task: dict[int, list[object]] = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)
    verified = {}
    if progress is not None:
        progress(
            "phase_start regime=all phase=portable_candidate_verification "
            f"problems={len(selected_problem_ids)}"
        )
    for problem_id in selected_problem_ids:
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),  # type: ignore[arg-type]
            archive_roots,
        )
    calibration_problem_ids = tuple(sorted({item.problem_id for item in calibration}))
    frozen_by_problem = {
        problem_id: (candidate_id, candidate_sha)
        for problem_id, candidate_id, candidate_sha in zip(
            calibration_problem_ids,
            frozen.candidate_set_ids,
            frozen.candidate_set_sha256s,
            strict=True,
        )
    }
    for problem_id, candidates in verified.items():
        if (
            candidates.evidence.candidate_set_id,
            candidates.evidence.content_sha256,
        ) != frozen_by_problem[problem_id]:
            raise ValueError("M8 portable Gate-3 candidates differ from the M7 freeze")
    if progress is not None:
        progress(
            "phase_complete regime=all phase=portable_candidate_verification "
            f"problems={len(selected_problem_ids)}"
        )

    execution_cells = _build_execution_cells(
        index=index,
        m0=m0,
        frozen=frozen,
        verified=verified,
        selected_streams=list(selected_streams),
    )
    pipeline = _execute_portable_fact_cells(
        execution_cells,
        rules=rule_set_from_m0(m0.remnant_eligibility),
        jagua_executable=executable,
        freeze_id=frozen.freeze_id,
        freeze_sha256=frozen.content_sha256,
        expected_jagua_sha256=frozen.runtime.jagua_executable_sha256,
        budget=M8_GATE3_CONCURRENCY_BUDGET,
    )
    return finalize_portable_fact_gate3(
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        m6_contract_id=index.m6_contract_id,
        m6_contract_sha256=index.m6_contract_sha256,
        m6_population_id=index.m6_population_id,
        m6_population_sha256=index.m6_population_sha256,
        problem_index_id=index.full_problem_index_id,
        problem_index_sha256=index.full_problem_index_sha256,
        freeze_id=frozen.freeze_id,
        freeze_sha256=frozen.content_sha256,
        calibration_view_id=index.view_id,
        calibration_view_sha256=index.content_sha256,
        pipeline=pipeline,
    )


def _portable_profile_identity_from_official(
    cell: M8PortableFactGate3Cell,
) -> dict[str, object]:
    return {
        "regime": cell.regime.value,
        "temporal_seed": cell.temporal_seed,
        "stream_id": cell.stream_id,
        "event_count": cell.event_count,
        "bundle_sha256": cell.first_bundle_sha256,
        "semantic_bundle_bytes_sha256": cell.first_semantic_bundle_bytes_sha256,
        "semantic_serialized_bytes": cell.semantic_serialized_bytes,
        "fixed_layer_node_count": cell.fixed_layer_node_count,
        "translation_batch_count": cell.translation_batch_count,
        "candidate_scalar_fact_count": cell.candidate_scalar_fact_count,
        "frontier_fact_count": cell.frontier_fact_count,
        "standard_candidate_fact_count": cell.standard_candidate_fact_count,
        "common_lemma_count": cell.common_lemma_count,
        "influence_fact_count": cell.influence_fact_count,
        "action_root_count": cell.generated_action_root_count,
        "counted_inventory_evidence_count": (cell.producer_counted_inventory_evidence_row_count),
        "counted_search_lemma_count": cell.producer_counted_search_lemma_count,
        "checked_common_lemma_count": cell.checked_common_lemma_count,
        "checked_influence_fact_count": cell.checked_influence_fact_count,
        "checked_action_root_count": cell.checked_action_root_count,
        "counted_translation_audit_count": cell.counted_translation_audit_count,
        "counted_translation_audit_call_count": (cell.counted_translation_audit_call_count),
        "influence_translation_audit_count": (cell.influence_translation_audit_count),
        "total_exact_fallback_count": cell.total_exact_fallback_count,
        "decision_id": cell.decision_id,
        "decision_content_sha256": cell.decision_content_sha256,
        "failure_code": cell.failure_code,
    }


def _portable_profile_identity_from_source(
    source: _PortableFactCheckedSource,
) -> dict[str, object]:
    generated = source.first_generation
    checked = source.check
    result = checked.check
    if result.decision is None:
        raise ValueError("M8 portable profile checker omitted its decision")
    return {
        "regime": generated.regime.value,
        "temporal_seed": generated.temporal_seed,
        "stream_id": generated.stream_id,
        "event_count": generated.event_count,
        "bundle_sha256": generated.bundle_sha256,
        "semantic_bundle_bytes_sha256": generated.semantic_bundle_bytes_sha256,
        "semantic_serialized_bytes": generated.semantic_serialized_bytes,
        "fixed_layer_node_count": generated.fixed_layer_node_count,
        "translation_batch_count": generated.translation_batch_count,
        "candidate_scalar_fact_count": generated.candidate_scalar_fact_count,
        "frontier_fact_count": generated.frontier_fact_count,
        "standard_candidate_fact_count": generated.standard_candidate_fact_count,
        "common_lemma_count": generated.common_lemma_count,
        "influence_fact_count": generated.influence_fact_count,
        "action_root_count": generated.action_root_count,
        "counted_inventory_evidence_count": (generated.telemetry.counted_inventory_evidence_count),
        "counted_search_lemma_count": generated.counted_search_lemma_count,
        "checked_common_lemma_count": result.checked_common_lemma_count,
        "checked_influence_fact_count": result.checked_influence_fact_count,
        "checked_action_root_count": result.checked_action_root_count,
        "counted_translation_audit_count": result.counted_translation_audit_count,
        "counted_translation_audit_call_count": (checked.counted_translation_audit_call_count),
        "influence_translation_audit_count": (result.influence_translation_audit_count),
        "total_exact_fallback_count": result.total_exact_fallback_count,
        "decision_id": result.decision.decision_id,
        "decision_content_sha256": result.decision.content_sha256,
        "failure_code": result.failure_code,
    }


def _portable_profile_generation_identity(
    generated: _PortableBundleIdentityWorkerResult,
) -> dict[str, object]:
    """Return every immutable hard-arm generation field bound by profile v2."""

    generated = _strict_portable_bundle_identity(
        generated,
        require_bytes=type(generated) is _PortableBundleWorkerResult,
    )
    return {
        "regime": generated.regime.value,
        "temporal_seed": generated.temporal_seed,
        "stream_id": generated.stream_id,
        "event_count": generated.event_count,
        "bundle_sha256": generated.bundle_sha256,
        "semantic_bundle_bytes_sha256": generated.semantic_bundle_bytes_sha256,
        "semantic_serialized_bytes": generated.semantic_serialized_bytes,
        "fixed_layer_node_count": generated.fixed_layer_node_count,
        "translation_batch_count": generated.translation_batch_count,
        "candidate_scalar_fact_count": generated.candidate_scalar_fact_count,
        "frontier_fact_count": generated.frontier_fact_count,
        "standard_candidate_fact_count": generated.standard_candidate_fact_count,
        "common_lemma_count": generated.common_lemma_count,
        "influence_fact_count": generated.influence_fact_count,
        "action_root_count": generated.action_root_count,
        "counted_inventory_evidence_count": (
            generated.telemetry.counted_inventory_evidence_count
        ),
        "counted_search_lemma_count": generated.counted_search_lemma_count,
    }


def _require_portable_profile_identity(
    official: M8PortableFactGate3Cell,
    source: _PortableFactCheckedSource,
) -> dict[str, object]:
    expected = _portable_profile_identity_from_official(official)
    observed = _portable_profile_identity_from_source(source)
    mismatches = tuple(
        key for key, expected_value in expected.items() if observed.get(key) != expected_value
    )
    if mismatches:
        raise ValueError(
            "M8 portable profile differs from the official hard arm: " + ",".join(mismatches)
        )
    return observed


def _require_official_portable_profile_gate3(
    value: M8PortableFactGate3Result,
) -> M8PortableFactGate3Result:
    """Pin the profile to the one committed portable Gate-3 timing artifact."""

    from yieldforge.oracle.gate3_evidence import require_official_portable_gate3

    return require_official_portable_gate3(
        value,
        label="M8 portable profile",
    )


def _portable_profile_runtime_identity(
    frozen: M7FrozenBaseline,
    *,
    jagua_executable_sha256: str,
) -> tuple[str, str]:
    """Require the frozen Python/Shapely runtime and bind the full current runtime."""

    import platform

    import shapely

    if (
        frozen.runtime.python_implementation != platform.python_implementation()
        or frozen.runtime.python_version != platform.python_version()
        or frozen.runtime.shapely_version != shapely.__version__
        or frozen.runtime.jagua_executable_sha256 != jagua_executable_sha256
    ):
        raise ValueError("M8 portable profile runtime differs from the M7 freeze")
    from yieldforge.oracle.gate3_execution import _runtime_identity

    return _runtime_identity(jagua_executable_sha256=jagua_executable_sha256)


def execute_portable_fact_profile(
    *,
    index: M7CalibrationProblemView,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    official_gate3: M8PortableFactGate3Result,
    archive_roots: tuple[Path, ...],
    jagua_executable: Path,
    progress=None,  # type: ignore[no-untyped-def]
) -> M8PortableHotspotProfileV2:
    """Profile only the sealed two-event regime-shift portable hard arm."""

    if (
        type(index) is not M7CalibrationProblemView
        or type(m0) is not M0ExperimentContract
        or type(frozen) is not M7FrozenBaseline
        or type(official_gate3) is not M8PortableFactGate3Result
    ):
        raise TypeError("M8 portable profile requires exact frozen input contracts")
    index = M7CalibrationProblemView.model_validate(index.model_dump(mode="python"), strict=True)
    m0 = M0ExperimentContract.model_validate(m0.model_dump(mode="python"), strict=True)
    frozen = M7FrozenBaseline.model_validate(frozen.model_dump(mode="python"), strict=True)
    official_gate3 = _require_official_portable_profile_gate3(
        M8PortableFactGate3Result.model_validate_json(
            official_gate3.model_dump_json(), strict=True
        )
    )
    contract = build_registered_contract()
    expected_boundary = (
        m0.contract_id,
        m0.content_sha256,
        index.m6_contract_id,
        index.m6_contract_sha256,
        index.m6_population_id,
        index.m6_population_sha256,
        index.full_problem_index_id,
        index.full_problem_index_sha256,
        frozen.freeze_id,
        frozen.content_sha256,
        index.view_id,
        index.content_sha256,
    )
    official_boundary = (
        official_gate3.m0_contract_id,
        official_gate3.m0_contract_sha256,
        official_gate3.m6_contract_id,
        official_gate3.m6_contract_sha256,
        official_gate3.m6_population_id,
        official_gate3.m6_population_sha256,
        official_gate3.problem_index_id,
        official_gate3.problem_index_sha256,
        official_gate3.freeze_id,
        official_gate3.freeze_sha256,
        official_gate3.calibration_view_id,
        official_gate3.calibration_view_sha256,
    )
    if (
        (index.full_problem_index_id, index.full_problem_index_sha256)
        != (frozen.problem_index_id, frozen.problem_index_sha256)
        or (m0.contract_id, m0.content_sha256) != (frozen.m0_contract_id, frozen.m0_contract_sha256)
        or (index.m6_contract_id, index.m6_contract_sha256)
        != (contract.contract_id, contract.content_sha256)
        or expected_boundary != official_boundary
        or index.evaluation_partition_opened
        or official_gate3.evaluation_accessed
    ):
        raise ValueError("M8 portable profile inputs differ from the sealed Gate-3 boundary")
    executable = Path(jagua_executable)
    metadata = executable.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M8 portable profile Jagua runtime must be a regular file")
    executable_sha = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    if executable_sha != frozen.runtime.jagua_executable_sha256:
        raise ValueError("M8 portable profile Jagua runtime differs from the M7 freeze")
    runtime_identity = _portable_profile_runtime_identity(
        frozen,
        jagua_executable_sha256=executable_sha,
    )

    official_cells = tuple(
        cell for cell in official_gate3.cells if cell.regime is TemporalRegime.REGIME_SHIFT
    )
    if len(official_cells) != 1:
        raise ValueError("M8 portable profile official hard arm is not unique")
    official_cell = official_cells[0]
    selected_streams = _select_portable_gate3_probe_streams(index)
    selected = tuple(
        stream for stream in selected_streams if stream[0].regime is TemporalRegime.REGIME_SHIFT
    )
    if len(selected) != 1:
        raise ValueError("M8 portable profile hard-arm stream is not unique")
    selected_stream = selected[0]
    if (
        selected_stream[0].temporal_seed != official_cell.temporal_seed
        or selected_stream[0].stream_id != official_cell.stream_id
        or len(selected_stream) != official_cell.event_count
    ):
        raise ValueError("M8 portable profile stream differs from the official hard arm")

    calibration = select_calibration_instances(index)
    problem_by_id = {item.problem_id: item for item in index.problems}
    selected_problem_ids = tuple(sorted({item.problem_id for item in selected_stream}))
    references_by_task: dict[int, list[object]] = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)
    if progress is not None:
        progress(
            "phase_start regime=regime_shift phase=portable_profile_candidate_verification "
            f"problems={len(selected_problem_ids)}"
        )
    verified = {}
    for problem_id in selected_problem_ids:
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),  # type: ignore[arg-type]
            archive_roots,
        )
    calibration_problem_ids = tuple(sorted({item.problem_id for item in calibration}))
    frozen_by_problem = {
        problem_id: (candidate_id, candidate_sha)
        for problem_id, candidate_id, candidate_sha in zip(
            calibration_problem_ids,
            frozen.candidate_set_ids,
            frozen.candidate_set_sha256s,
            strict=True,
        )
    }
    for problem_id, candidates in verified.items():
        if (
            candidates.evidence.candidate_set_id,
            candidates.evidence.content_sha256,
        ) != frozen_by_problem[problem_id]:
            raise ValueError("M8 portable profile candidates differ from the M7 freeze")
    if progress is not None:
        progress(
            "phase_complete regime=regime_shift phase=portable_profile_candidate_verification "
            f"problems={len(selected_problem_ids)}"
        )

    execution_cell = _build_execution_cells(
        index=index,
        m0=m0,
        frozen=frozen,
        verified=verified,
        selected_streams=[selected_stream],
    )[0]
    if progress is not None:
        progress("phase_start regime=regime_shift phase=portable_profile_generation_check")
    source_tree = capture_source_tree()
    implementation_identity = source_tree_implementation_identity(
        "portable-profile",
        (Path(__file__),),
        source_tree=source_tree,
    )
    (
        source,
        repeated_generation,
        generator_profile,
        generation_phase,
        repeat_generation_phase,
        checker_phase,
        timing,
    ) = _profile_portable_fact_cell(
        execution_cell,
        rules=rule_set_from_m0(m0.remnant_eligibility),
        jagua_executable=executable,
        freeze_id=frozen.freeze_id,
        freeze_sha256=frozen.content_sha256,
        expected_jagua_sha256=frozen.runtime.jagua_executable_sha256,
        budget=M8_GATE3_CONCURRENCY_BUDGET,
        source_tree=source_tree,
        expected_runtime_identity=runtime_identity,
    )
    identity = _require_portable_profile_identity(official_cell, source)
    generator_report = generator_profile.model_dump()
    checker_report = source.check.profile.model_dump()
    measurement_complete = (
        generator_profile.accounted_wall_fraction >= 0.90
        and source.check.profile.accounted_wall_fraction >= 0.90
    )
    if progress is not None:
        progress(
            "phase_complete regime=regime_shift phase=portable_profile_generation_check "
            f"roots={identity['action_root_count']} "
            f"measurement_complete={str(measurement_complete).lower()}"
        )
    payload: dict[str, object] = {
        "schema_version": "yieldforge.m8-portable-hotspot-profile.v2",
        "official_gate3_id": official_gate3.gate3_id,
        "official_gate3_content_sha256": official_gate3.content_sha256,
        "regime": TemporalRegime.REGIME_SHIFT.value,
        "temporal_seed": official_cell.temporal_seed,
        "stream_id": official_cell.stream_id,
        "event_count": official_cell.event_count,
        "official_identity_match": True,
        "repeated_output_identity_match": True,
        "first_generation_identity": _portable_profile_generation_identity(
            source.first_generation
        ),
        "repeat_generation_identity": _portable_profile_generation_identity(
            repeated_generation
        ),
        "identity": identity,
        "profile_implementation_id": implementation_identity[0],
        "profile_implementation_content_sha256": implementation_identity[1],
        "runtime_id": runtime_identity[0],
        "runtime_content_sha256": runtime_identity[1],
        "generator_runtime_content_sha256": (
            timing.generator_runtime_content_sha256
        ),
        "repeat_generator_runtime_content_sha256": (
            timing.repeat_generator_runtime_content_sha256
        ),
        "checker_runtime_content_sha256": timing.checker_runtime_content_sha256,
        "runtime_attested_workers": True,
        "source_attested_workers": True,
        "fresh_pycache_scope": True,
        "generator_worker_pid": source.first_generation.worker_pid,
        "repeat_generator_worker_pid": repeated_generation.worker_pid,
        "checker_worker_pid": source.check.worker_pid,
        "fresh_worker_identity": (
            len(
                {
                    source.first_generation.worker_pid,
                    repeated_generation.worker_pid,
                    source.check.worker_pid,
                }
            )
            == 3
        ),
        "generator_worker_wall_seconds": (source.first_generation.generation_wall_seconds),
        "repeat_generator_worker_wall_seconds": repeated_generation.generation_wall_seconds,
        "checker_worker_wall_seconds": source.check.checker_wall_seconds,
        "core_generation_plus_checker_worker_wall_seconds": (
            source.first_generation.generation_wall_seconds + source.check.checker_wall_seconds
        ),
        "repeated_generation_plus_checker_worker_wall_seconds": (
            source.first_generation.generation_wall_seconds
            + repeated_generation.generation_wall_seconds
            + source.check.checker_wall_seconds
        ),
        "first_generation_phase_wall_seconds": (
            timing.first_generation_phase_wall_seconds
        ),
        "second_generation_phase_wall_seconds": (
            timing.second_generation_phase_wall_seconds
        ),
        "checker_phase_wall_seconds": timing.checker_phase_wall_seconds,
        "total_pipeline_wall_seconds": timing.total_pipeline_wall_seconds,
        "timing_semantics": "controller_phase_and_worker_operation_wall_v1",
        "generator_profile": generator_report,
        "checker_profile": checker_report,
        "generator_accounted_wall_fraction": (generator_profile.accounted_wall_fraction),
        "checker_accounted_wall_fraction": (source.check.profile.accounted_wall_fraction),
        "minimum_accounted_wall_fraction": 0.90,
        "measurement_complete": measurement_complete,
        "measurement_decision": (
            "profile_complete" if measurement_complete else "profile_incomplete"
        ),
        "generator_handoff": {
            "task_serialization_wall_seconds": (generation_phase.task_serialization_wall_seconds),
            "result_serialization_wall_seconds": (
                generation_phase.result_serialization_wall_seconds
            ),
            "worker_payload_handoff_wall_seconds": (
                generation_phase.worker_payload_handoff_wall_seconds
            ),
            "process_exit_validation_wall_seconds": (
                generation_phase.process_exit_validation_wall_seconds
            ),
        },
        "checker_handoff": {
            "task_serialization_wall_seconds": checker_phase.task_serialization_wall_seconds,
            "result_serialization_wall_seconds": (checker_phase.result_serialization_wall_seconds),
            "worker_payload_handoff_wall_seconds": (
                checker_phase.worker_payload_handoff_wall_seconds
            ),
            "process_exit_validation_wall_seconds": (
                checker_phase.process_exit_validation_wall_seconds
            ),
        },
        "repeat_generator_handoff": {
            "task_serialization_wall_seconds": (
                repeat_generation_phase.task_serialization_wall_seconds
            ),
            "result_serialization_wall_seconds": (
                repeat_generation_phase.result_serialization_wall_seconds
            ),
            "worker_payload_handoff_wall_seconds": (
                repeat_generation_phase.worker_payload_handoff_wall_seconds
            ),
            "process_exit_validation_wall_seconds": (
                repeat_generation_phase.process_exit_validation_wall_seconds
            ),
        },
        "configured_outer_process_count": 1,
        "nested_translation_audit_processes": (
            M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell
        ),
        "peak_compute_count": (M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell),
        "compute_slot_cap": M8_GATE3_CONCURRENCY_BUDGET.peak_compute,
        "evaluation_accessed": False,
        "official_six_cell_calibration_authorized": False,
        "claim_ceiling": (
            "calibration_hotspot_measurement_only_not_gate3_performance_pass_"
            "m8_advantage_savings_physical_or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(payload)
    identified = {
        **payload,
        "profile_id": f"yfm8profile-{digest[:24]}",
        "content_sha256": f"sha256:{digest}",
    }
    return M8PortableHotspotProfileV2.model_validate_json(
        json.dumps(identified, allow_nan=False, sort_keys=True),
        strict=True,
    )


def _profile_result_payload(
    *,
    regime: TemporalRegime,
    temporal_seed: int,
    stream_id: str,
    event_count: int,
    sparse: M8SparseResult,
    checks: tuple[M8ProofCheckResult, ...],
    reference_action_id: str,
    reference_matches: bool,
    common_fact_audit: M8CommonFactDifferentialAudit,
    profile: M8ProfileReport,
) -> dict[str, object]:
    semantic_hashes = {proof.semantic_runtime_sha256 for proof in sparse.proofs}
    if len(semantic_hashes) != 1:
        raise ValueError("M8 profile proof runtime bindings differ")
    return {
        "schema_version": "yieldforge.m8-certificate-profile.v1",
        "regime": regime.value,
        "temporal_seed": temporal_seed,
        "stream_id": stream_id,
        "event_count": event_count,
        "evaluation_accessed": False,
        "semantic_runtime_sha256": semantic_hashes.pop(),
        "action_count": len(sparse.proofs),
        "valid_check_count": sum(check.valid for check in checks),
        "checker_failure_count": sum(not check.valid for check in checks),
        "reference_action_id": reference_action_id,
        "reference_matches": reference_matches,
        "common_fact_exact_match": True,
        "common_fact_event_position": common_fact_audit.event_position,
        "common_fact_event_id": common_fact_audit.event_id,
        "common_fact_content_sha256": common_fact_audit.content_sha256,
        "profile": profile.model_dump(),
    }


def execute_certificate_profile(
    *,
    index: M7CalibrationProblemView,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    archive_roots: tuple[Path, ...],
    jagua_executable: Path,
    regime: TemporalRegime,
    temporal_seed: int,
    event_count: int,
) -> dict[str, object]:
    """Profile one explicit calibration-only M8 stream prefix in-process."""

    if type(temporal_seed) is not int:
        raise TypeError("M8 profile temporal seed must be an exact integer")
    if type(event_count) is not int or not 2 <= event_count <= 24:
        raise ValueError("M8 profile event count must be an exact integer from 2 to 24")
    contract = build_registered_contract()
    if (
        (index.full_problem_index_id, index.full_problem_index_sha256)
        != (frozen.problem_index_id, frozen.problem_index_sha256)
        or (m0.contract_id, m0.content_sha256) != (frozen.m0_contract_id, frozen.m0_contract_sha256)
        or (index.m6_contract_id, index.m6_contract_sha256)
        != (contract.contract_id, contract.content_sha256)
        or index.evaluation_partition_opened
    ):
        raise ValueError("M8 profile inputs do not share the sealed calibration boundary")
    executable = Path(jagua_executable)
    metadata = executable.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M8 profile Jagua runtime must be a regular file")
    executable_sha = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    if executable_sha != frozen.runtime.jagua_executable_sha256:
        raise ValueError("M8 profile Jagua runtime differs from the M7 freeze")

    calibration = select_calibration_instances(index)
    matching = tuple(
        item
        for item in calibration
        if item.regime is regime and item.temporal_seed == temporal_seed
    )
    stream_ids = tuple(sorted({item.stream_id for item in matching}))
    if len(stream_ids) != 1:
        raise ValueError("M8 profile selection must identify exactly one calibration stream")
    stream = tuple(item for item in matching if item.stream_id == stream_ids[0])
    if len(stream) != 24:
        raise ValueError("M8 profile calibration stream does not contain 24 events")
    selected_stream = stream[:event_count]

    problem_by_id = {item.problem_id: item for item in index.problems}
    selected_problem_ids = tuple(sorted({item.problem_id for item in selected_stream}))
    references_by_task: dict[int, list[object]] = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)

    with activate_m8_profile() as profiler:
        verified = {}
        with profile_phase("candidate_verification"):
            for problem_id in selected_problem_ids:
                problem = problem_by_id[problem_id]
                verified[problem_id] = verify_problem_candidates(
                    problem,
                    tuple(references_by_task[problem.tasks_index]),  # type: ignore[arg-type]
                    archive_roots,
                )
                increment_profile_count(
                    "candidates",
                    len(verified[problem_id].candidates),
                )

        calibration_problem_ids = tuple(sorted({item.problem_id for item in calibration}))
        frozen_by_problem = {
            problem_id: (candidate_id, candidate_sha)
            for problem_id, candidate_id, candidate_sha in zip(
                calibration_problem_ids,
                frozen.candidate_set_ids,
                frozen.candidate_set_sha256s,
                strict=True,
            )
        }
        for problem_id, candidates in verified.items():
            if (
                candidates.evidence.candidate_set_id,
                candidates.evidence.content_sha256,
            ) != frozen_by_problem[problem_id]:
                raise ValueError("M8 profile candidate evidence differs from the M7 freeze")

        cell = _build_execution_cells(
            index=index,
            m0=m0,
            frozen=frozen,
            verified=verified,
            selected_streams=[selected_stream],
        )[0]
        request = _request_for_cell(
            cell,
            rules=rule_set_from_m0(m0.remnant_eligibility),
            jagua_executable=executable,
        )
        sparse = score_sparse_event(request)
        checks = check_action_proofs(request, sparse.proofs)
        common_fact_audit = audit_m8_common_transition_exactness(request)
        reference_action_id = sparse.decision.scores[0].action_id
        with profile_phase("reference_audit"):
            reference = score_reference_action(
                request,
                action_id=reference_action_id,
            )
        expected_reference_cost = next(
            score.final_net_cost
            for score in sparse.decision.scores
            if score.action_id == reference_action_id
        )
        reference_matches = reference.final_net_cost == expected_reference_cost

    report = profiler.report()
    if not all(check.valid for check in checks) or not reference_matches:
        raise ValueError("M8 profile semantic verification failed")
    return _profile_result_payload(
        regime=regime,
        temporal_seed=temporal_seed,
        stream_id=stream_ids[0],
        event_count=event_count,
        sparse=sparse,
        checks=checks,
        reference_action_id=reference_action_id,
        reference_matches=reference_matches,
        common_fact_audit=common_fact_audit,
        profile=report,
    )


def publish_certificate_profile(output_path: Path, result: dict[str, object]) -> Path:
    """Write one explicit profiling report without content-addressed gate promotion."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _open_portable_profile_parent(output_path: Path) -> tuple[Path, int]:
    """Open/create every parent through no-follow directory descriptors."""

    absolute = Path(os.path.abspath(Path(output_path)))
    if not absolute.name:
        raise ValueError("M8 portable profile output filename is absent")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parts = absolute.parent.parts
    if not parts or parts[0] != absolute.anchor:
        raise ValueError("M8 portable profile parent directory is malformed")
    current = os.open(absolute.anchor, directory_flags)
    try:
        for component in parts[1:]:
            try:
                following = os.open(
                    component,
                    directory_flags,
                    dir_fd=current,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
                else:
                    os.fsync(current)
                try:
                    following = os.open(
                        component,
                        directory_flags,
                        dir_fd=current,
                    )
                except OSError as error:
                    raise ValueError(
                        "M8 portable profile parent directory is not a regular directory"
                    ) from error
            except OSError as error:
                raise ValueError(
                    "M8 portable profile parent directory is not a regular directory"
                ) from error
            metadata = os.fstat(following)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(following)
                raise ValueError(
                    "M8 portable profile parent directory is not a regular directory"
                )
            os.close(current)
            current = following
        return absolute, current
    except BaseException:
        os.close(current)
        raise


def _read_portable_profile_entry(
    parent_descriptor: int,
    filename: str,
) -> tuple[bytes, os.stat_result] | None:
    """Read one stable regular entry without following its final component."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError("M8 portable existing profile differs") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("M8 portable existing profile differs")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        entry = os.stat(
            filename,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        entry_identity = (
            entry.st_dev,
            entry.st_ino,
            entry.st_size,
            entry.st_mtime_ns,
        )
        if before_identity != after_identity or after_identity != entry_identity:
            raise ValueError("M8 portable profile publication integrity differs")
        return b"".join(chunks), after
    except FileNotFoundError as error:
        raise ValueError("M8 portable profile publication integrity differs") from error
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("M8 portable profile write made no progress")
        remaining = remaining[written:]


def publish_portable_fact_profile(
    output_path: Path,
    result: M8PortableHotspotProfileV2,
) -> Path:
    """Publish a bounded profiling artifact after verifying its content identity."""

    if type(result) is not M8PortableHotspotProfileV2:
        raise TypeError("M8 portable profile publisher requires the exact result model")
    strict = M8PortableHotspotProfileV2.model_validate_json(
        result.model_dump_json(),
        strict=True,
    )
    path = Path(output_path)
    data = (
        json.dumps(
            strict.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    absolute, parent_descriptor = _open_portable_profile_parent(path)
    filename = absolute.name
    temporary = f".{filename}.tmp-{secrets.token_hex(16)}"
    try:
        existing = _read_portable_profile_entry(parent_descriptor, filename)
        if existing is not None:
            if existing[0] != data:
                raise ValueError("M8 portable existing profile differs")
            return path
        descriptor = os.open(
            temporary,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        linked_destination = False
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
            source = os.fstat(descriptor)
            source_entry = os.stat(
                temporary,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(source.st_mode)
                or (source.st_dev, source.st_ino) != (source_entry.st_dev, source_entry.st_ino)
            ):
                raise ValueError("M8 portable profile publication integrity differs")
            try:
                os.link(
                    temporary,
                    filename,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                linked_destination = True
            except FileExistsError:
                existing = _read_portable_profile_entry(parent_descriptor, filename)
                if existing is None or existing[0] != data:
                    raise ValueError("M8 portable existing profile differs") from None
            else:
                published = _read_portable_profile_entry(parent_descriptor, filename)
                if (
                    published is None
                    or published[0] != data
                    or (published[1].st_dev, published[1].st_ino)
                    != (source.st_dev, source.st_ino)
                ):
                    raise ValueError("M8 portable profile publication integrity differs")
                os.fsync(parent_descriptor)
        except BaseException:
            if linked_destination:
                try:
                    os.unlink(filename, dir_fd=parent_descriptor)
                except FileNotFoundError:
                    pass
                os.fsync(parent_descriptor)
            raise
        finally:
            os.close(descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        else:
            os.fsync(parent_descriptor)
        os.close(parent_descriptor)
    return path


def publish_portable_fact_gate3(
    output_directory: Path,
    result: M8PortableFactGate3Result,
) -> Path:
    """Atomically publish the separate immutable calibration-only Gate-3 artifact."""

    if type(result) is not M8PortableFactGate3Result:
        raise TypeError("M8 portable Gate-3 publisher requires the exact result model")
    strict = M8PortableFactGate3Result.model_validate_json(
        result.model_dump_json(),
        strict=True,
    )
    output = Path(output_directory)
    if output.exists() and not output.is_dir():
        raise ValueError("M8 portable Gate-3 output must be a directory")
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m8-portable-fact-gate3-{strict.gate3_id}.json"
    data = (
        json.dumps(
            strict.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("M8 portable Gate-3 artifact is immutable and differs")
        return path
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("M8 portable Gate-3 artifact is immutable and differs") from None
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _publish_sparse_proof_unprofiled(
    output_directory: Path,
    result: M8CertificateProofResult,
) -> Path:
    """Publish one immutable content-addressed M8 certificate proof."""

    output = Path(output_directory)
    if output.exists() and not output.is_dir():
        raise ValueError("M8 certificate proof output must be a directory")
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m8-certificate-proof-{result.proof_id}.json"
    data = (
        json.dumps(result.model_dump(mode="json"), allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("M8 certificate proof artifact is immutable and differs")
        return path
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.rename(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def publish_sparse_proof(
    output_directory: Path,
    result: M8CertificateProofResult,
) -> Path:
    """Publish one immutable result while exposing artifact-write timing."""

    with profile_phase("artifact_write"):
        return _publish_sparse_proof_unprofiled(output_directory, result)


__all__ = [
    "M8AuditActionBinding",
    "M8CertificateProofCell",
    "M8CertificateProofResult",
    "M8PortableFactGate3Cell",
    "M8PortableFactGate3Result",
    "M8PortableFactPhaseTiming",
    "M8PortableHotspotProfileV2",
    "M8PortableRegistryEvidence",
    "audit_sample_sha256",
    "execute_certificate_profile",
    "execute_portable_fact_profile",
    "execute_portable_fact_gate3",
    "execute_sparse_prefix_proof",
    "finalize_certificate_proof",
    "finalize_portable_fact_gate3",
    "publish_certificate_profile",
    "publish_portable_fact_profile",
    "publish_portable_fact_gate3",
    "publish_sparse_proof",
]
