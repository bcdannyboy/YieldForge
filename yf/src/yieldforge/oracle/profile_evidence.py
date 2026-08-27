"""Strict, cycle-free evidence for the sealed M8 portable hotspot profile."""

from __future__ import annotations

from collections import Counter
from math import isclose
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.contracts import BaselineContractModel
from yieldforge.experiments.contracts import semantic_sha256

_OFFICIAL_GATE3_ID = "yfm8gate3-ea8a12969396172d7dbc4774"
_OFFICIAL_GATE3_SHA256 = "sha256:ea8a12969396172d7dbc4774bd239532e2907e637ddb44b1d5505c7b9011d117"
_HARD_ARM_STREAM_ID = "yfts-f320978a2d55802395294150"
_HARD_ARM_BUNDLE_SHA256 = "sha256:207a5fb36ae58a42e7f06e61de94df8b6b09dd613578de247778218cf06bb99f"
_HARD_ARM_SEMANTIC_BYTES_SHA256 = (
    "sha256:6638999ad1ee81d78f8e795dff97ecf081c08b87a63ffd111302a80dd34cec18"
)
_HARD_ARM_DECISION_ID = "yfm8d-7a2c333838e80808a29764cd"
_HARD_ARM_DECISION_SHA256 = (
    "sha256:7a2c333838e80808a29764cd6c3ace49a0a457507045a5b2b0522a88ba8cd571"
)
_FROZEN_RUNTIME_SHA256 = (
    "sha256:bce0d552132a3de7ca12eb98599800e34a0d78cf8e0bc5440efb7faa28a45508"
)
_CLAIM_CEILING = (
    "calibration_hotspot_measurement_only_not_gate3_performance_pass_"
    "m8_advantage_savings_physical_or_commercial_evidence"
)
_FRACTION_TOLERANCE = 1e-12
_WALL_SECONDS_TOLERANCE = 1e-9
_GENERATOR_PHASE_OCCURRENCES = {
    "action_catalog_enumeration": 1,
    "fact_bundle_generation": 1,
    "fact_bundle_generator_authority_reconstruction": 1,
    "fact_bundle_handoff_serialization": 1,
    "fact_bundle_hash_validation": 1,
    "fact_bundle_layer_assembly": 1,
    "fact_bundle_prepared_context_session": 1,
    "fact_bundle_semantic_serialization": 1,
    "fact_bundle_strict_roundtrip": 1,
    "fact_bundle_telemetry": 1,
    "fact_bundle_unchecked_traversal": 1,
    "scalar_frontier_construction": 1,
    "standard_layout_materialization": 1,
}
_CHECKER_PHASE_OCCURRENCES = {
    "counted_translation_audit_call": 1,
    "fact_bundle_action_traversal": 1,
    "fact_bundle_authority_reconstruction": 1,
    "fact_bundle_authority_session": 1,
    "fact_bundle_capability_registration": 1,
    "fact_bundle_cleanup": 1,
    "fact_bundle_common_verification": 1,
    "fact_bundle_context_index_preparation": 1,
    "fact_bundle_metadata_reconciliation": 1,
    "fact_bundle_request_snapshot": 1,
    "fact_bundle_request_stability": 2,
    "fact_bundle_result_materialization": 1,
    "fact_bundle_strict_load": 1,
    "scalar_frontier_construction": 2,
    "standard_layout_materialization": 1,
}


class M8PortableProfileCounts(BaselineContractModel):
    """The complete frozen counter namespace for one process-local profile."""

    events: StrictInt = Field(ge=0)
    candidates: StrictInt = Field(ge=0)
    frontier_entries: StrictInt = Field(ge=0)
    actions: StrictInt = Field(ge=0)
    facts: StrictInt = Field(ge=0)
    fallbacks: StrictInt = Field(ge=0)
    frontier_rejected_transitions: StrictInt = Field(ge=0)
    standard_only_materializations: StrictInt = Field(ge=0)
    full_authoritative_fallbacks: StrictInt = Field(ge=0)
    differential_mismatches: StrictInt = Field(ge=0)
    partially_pruned_transitions: StrictInt = Field(ge=0)
    frontier_rejected_inventory_items: StrictInt = Field(ge=0)
    exact_survivor_inventory_items: StrictInt = Field(ge=0)
    counted_no_fit_transitions: StrictInt = Field(ge=0)
    counted_no_fit_inventory_items: StrictInt = Field(ge=0)
    counted_no_fit_candidate_searches: StrictInt = Field(ge=0)


class M8PortableProfilePhase(BaselineContractModel):
    """One inclusive timing node in a finite nested phase tree."""

    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    process_ns: StrictInt = Field(ge=0)
    wall_ns: StrictInt = Field(ge=0)
    children: tuple[M8PortableProfilePhase, ...] = ()

    @model_validator(mode="after")
    def require_child_accounting(self) -> Self:
        if sum(child.process_ns for child in self.children) > self.process_ns:
            raise ValueError("M8 profile child process durations exceed their parent")
        if sum(child.wall_ns for child in self.children) > self.wall_ns:
            raise ValueError("M8 profile child wall durations exceed their parent")
        return self


class M8PortableProfileReport(BaselineContractModel):
    """One strictly reconciled process/wall report with a true phase tree."""

    schema_version: Literal["yieldforge.m8-phase-profile.v2"] = "yieldforge.m8-phase-profile.v2"
    total_process_ns: StrictInt = Field(gt=0)
    total_wall_ns: StrictInt = Field(gt=0)
    accounted_process_ns: StrictInt = Field(ge=0)
    accounted_process_fraction: StrictFloat = Field(ge=0.0, le=1.0)
    accounted_wall_ns: StrictInt = Field(ge=0)
    unattributed_wall_ns: StrictInt = Field(ge=0)
    accounted_wall_fraction: StrictFloat = Field(ge=0.0, le=1.0)
    counts: M8PortableProfileCounts
    phases: tuple[M8PortableProfilePhase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_reconciled_tree(self) -> Self:
        active: set[int] = set()
        visited: set[int] = set()

        def visit(phase: M8PortableProfilePhase) -> None:
            identity = id(phase)
            if identity in active:
                raise ValueError("M8 profile phase tree contains a cycle")
            if identity in visited:
                raise ValueError("M8 profile phase node is reused outside a tree")
            active.add(identity)
            visited.add(identity)
            for child in phase.children:
                visit(child)
            active.remove(identity)

        for phase in self.phases:
            visit(phase)

        expected_process_ns = sum(phase.process_ns for phase in self.phases)
        expected_wall_ns = sum(phase.wall_ns for phase in self.phases)
        if self.accounted_process_ns != expected_process_ns:
            raise ValueError("M8 profile accounted process duration does not reconcile")
        if self.accounted_wall_ns != expected_wall_ns:
            raise ValueError("M8 profile accounted wall duration does not reconcile")
        if self.accounted_process_ns > self.total_process_ns:
            raise ValueError("M8 profile accounted process duration exceeds its total")
        if self.accounted_wall_ns > self.total_wall_ns:
            raise ValueError("M8 profile accounted wall duration exceeds its total")
        if self.unattributed_wall_ns != self.total_wall_ns - self.accounted_wall_ns:
            raise ValueError("M8 profile unattributed wall duration does not reconcile")

        expected_process_fraction = self.accounted_process_ns / self.total_process_ns
        expected_wall_fraction = self.accounted_wall_ns / self.total_wall_ns
        if not isclose(
            self.accounted_process_fraction,
            expected_process_fraction,
            rel_tol=0.0,
            abs_tol=_FRACTION_TOLERANCE,
        ):
            raise ValueError("M8 profile accounted process fraction does not reconcile")
        if not isclose(
            self.accounted_wall_fraction,
            expected_wall_fraction,
            rel_tol=0.0,
            abs_tol=_FRACTION_TOLERANCE,
        ):
            raise ValueError("M8 profile accounted wall fraction does not reconcile")
        return self


class M8PortableGenerationIdentity(BaselineContractModel):
    """Complete semantic identity of one sealed hard-arm generation."""

    regime: Literal["regime_shift"]
    temporal_seed: Literal[2026082300]
    stream_id: Literal[_HARD_ARM_STREAM_ID]
    event_count: Literal[2]
    bundle_sha256: Literal[_HARD_ARM_BUNDLE_SHA256]
    semantic_bundle_bytes_sha256: Literal[_HARD_ARM_SEMANTIC_BYTES_SHA256]
    semantic_serialized_bytes: Literal[43_520_933]
    fixed_layer_node_count: Literal[2_297]
    translation_batch_count: Literal[459]
    candidate_scalar_fact_count: Literal[459]
    frontier_fact_count: Literal[1]
    standard_candidate_fact_count: Literal[459]
    common_lemma_count: Literal[1]
    influence_fact_count: Literal[459]
    action_root_count: Literal[459]
    counted_inventory_evidence_count: Literal[1]
    counted_search_lemma_count: Literal[1]

    @model_validator(mode="after")
    def require_layer_accounting(self) -> Self:
        layer_total = (
            self.translation_batch_count
            + self.candidate_scalar_fact_count
            + self.frontier_fact_count
            + self.standard_candidate_fact_count
            + self.common_lemma_count
            + self.influence_fact_count
            + self.action_root_count
        )
        if self.fixed_layer_node_count != layer_total:
            raise ValueError("M8 portable generation layer counts do not reconcile")
        return self


class M8PortableCheckedIdentity(M8PortableGenerationIdentity):
    """Generation identity extended by independent checker evidence."""

    checked_common_lemma_count: Literal[1]
    checked_influence_fact_count: Literal[459]
    checked_action_root_count: Literal[459]
    counted_translation_audit_count: Literal[1]
    counted_translation_audit_call_count: Literal[1]
    influence_translation_audit_count: Literal[0]
    total_exact_fallback_count: Literal[0]
    decision_id: Literal[_HARD_ARM_DECISION_ID]
    decision_content_sha256: Literal[_HARD_ARM_DECISION_SHA256]
    failure_code: Literal["valid_action_decision"]

    @model_validator(mode="after")
    def require_checker_reconciliation(self) -> Self:
        if (
            self.checked_common_lemma_count != self.common_lemma_count
            or self.checked_influence_fact_count != self.influence_fact_count
            or self.checked_action_root_count != self.action_root_count
            or self.counted_translation_audit_count != self.counted_search_lemma_count
            or self.counted_translation_audit_call_count != self.counted_search_lemma_count
        ):
            raise ValueError("M8 portable checked identity does not reconcile")
        digest = self.decision_content_sha256.removeprefix("sha256:")
        if self.decision_id != f"yfm8d-{digest[:24]}":
            raise ValueError("M8 portable decision identity does not match its content hash")
        return self


class M8PortableHandoffTimings(BaselineContractModel):
    """Controller/worker serialization, handoff, and cleanup timings."""

    task_serialization_wall_seconds: StrictFloat = Field(ge=0.0)
    result_serialization_wall_seconds: StrictFloat = Field(ge=0.0)
    worker_payload_handoff_wall_seconds: StrictFloat = Field(ge=0.0)
    process_exit_validation_wall_seconds: StrictFloat = Field(ge=0.0)


class M8PortableHotspotProfileV2(BaselineContractModel):
    """Content-addressed evidence for the sealed M8 regime-shift hotspot run."""

    schema_version: Literal["yieldforge.m8-portable-hotspot-profile.v2"] = (
        "yieldforge.m8-portable-hotspot-profile.v2"
    )
    profile_id: StrictStr = Field(pattern=r"^yfm8profile-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    official_gate3_id: Literal[_OFFICIAL_GATE3_ID] = _OFFICIAL_GATE3_ID
    official_gate3_content_sha256: Literal[_OFFICIAL_GATE3_SHA256] = _OFFICIAL_GATE3_SHA256
    regime: Literal["regime_shift"] = "regime_shift"
    temporal_seed: Literal[2026082300] = 2026082300
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    event_count: Literal[2] = 2
    official_identity_match: Literal[True] = True
    repeated_output_identity_match: Literal[True] = True
    first_generation_identity: M8PortableGenerationIdentity
    repeat_generation_identity: M8PortableGenerationIdentity
    identity: M8PortableCheckedIdentity
    profile_implementation_id: Literal["yieldforge-m8-portable-profile-v1"]
    profile_implementation_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_id: Literal["yieldforge-m8-gate3-runtime-v1"]
    runtime_content_sha256: Literal[_FROZEN_RUNTIME_SHA256]
    generator_runtime_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repeat_generator_runtime_content_sha256: StrictStr = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    checker_runtime_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_attested_workers: Literal[True] = True
    source_attested_workers: Literal[True] = True
    fresh_pycache_scope: Literal[True] = True
    generator_worker_pid: StrictInt = Field(gt=0)
    repeat_generator_worker_pid: StrictInt = Field(gt=0)
    checker_worker_pid: StrictInt = Field(gt=0)
    fresh_worker_identity: Literal[True] = True
    generator_worker_wall_seconds: StrictFloat = Field(gt=0.0)
    repeat_generator_worker_wall_seconds: StrictFloat = Field(gt=0.0)
    checker_worker_wall_seconds: StrictFloat = Field(gt=0.0)
    core_generation_plus_checker_worker_wall_seconds: StrictFloat = Field(gt=0.0)
    repeated_generation_plus_checker_worker_wall_seconds: StrictFloat = Field(gt=0.0)
    first_generation_phase_wall_seconds: StrictFloat = Field(gt=0.0)
    second_generation_phase_wall_seconds: StrictFloat = Field(gt=0.0)
    checker_phase_wall_seconds: StrictFloat = Field(gt=0.0)
    total_pipeline_wall_seconds: StrictFloat = Field(gt=0.0)
    timing_semantics: Literal["controller_phase_and_worker_operation_wall_v1"] = (
        "controller_phase_and_worker_operation_wall_v1"
    )
    generator_profile: M8PortableProfileReport
    checker_profile: M8PortableProfileReport
    generator_accounted_wall_fraction: StrictFloat = Field(ge=0.0, le=1.0)
    checker_accounted_wall_fraction: StrictFloat = Field(ge=0.0, le=1.0)
    minimum_accounted_wall_fraction: Literal[0.9] = 0.9
    measurement_complete: StrictBool
    measurement_decision: Literal["profile_complete", "profile_incomplete"]
    generator_handoff: M8PortableHandoffTimings
    repeat_generator_handoff: M8PortableHandoffTimings
    checker_handoff: M8PortableHandoffTimings
    configured_outer_process_count: Literal[1] = 1
    nested_translation_audit_processes: Literal[2] = 2
    peak_compute_count: Literal[2] = 2
    compute_slot_cap: Literal[8] = 8
    evaluation_accessed: Literal[False] = False
    official_six_cell_calibration_authorized: Literal[False] = False
    claim_ceiling: Literal[_CLAIM_CEILING] = _CLAIM_CEILING

    @model_validator(mode="after")
    def require_reconciled_evidence(self) -> Self:
        first = self.first_generation_identity
        if first != self.repeat_generation_identity:
            raise ValueError("M8 portable repeated generation identity differs")
        generation_fields = M8PortableGenerationIdentity.model_fields
        if any(getattr(first, name) != getattr(self.identity, name) for name in generation_fields):
            raise ValueError("M8 portable first and checked generation identities differ")
        if (self.regime, self.temporal_seed, self.stream_id, self.event_count) != (
            first.regime,
            first.temporal_seed,
            first.stream_id,
            first.event_count,
        ):
            raise ValueError("M8 portable top-level generation identity differs")
        if (
            len(
                {
                    self.generator_worker_pid,
                    self.repeat_generator_worker_pid,
                    self.checker_worker_pid,
                }
            )
            != 3
        ):
            raise ValueError("M8 portable profile requires three distinct worker PIDs")
        if any(
            worker_runtime != self.runtime_content_sha256
            for worker_runtime in (
                self.generator_runtime_content_sha256,
                self.repeat_generator_runtime_content_sha256,
                self.checker_runtime_content_sha256,
            )
        ):
            raise ValueError("M8 portable worker runtime identity differs")

        expected_core_seconds = (
            self.generator_worker_wall_seconds + self.checker_worker_wall_seconds
        )
        expected_charged_seconds = (
            self.generator_worker_wall_seconds
            + self.repeat_generator_worker_wall_seconds
            + self.checker_worker_wall_seconds
        )
        if not isclose(
            self.core_generation_plus_checker_worker_wall_seconds,
            expected_core_seconds,
            rel_tol=0.0,
            abs_tol=_WALL_SECONDS_TOLERANCE,
        ):
            raise ValueError("M8 portable core worker wall sum does not reconcile")
        if not isclose(
            self.repeated_generation_plus_checker_worker_wall_seconds,
            expected_charged_seconds,
            rel_tol=0.0,
            abs_tol=_WALL_SECONDS_TOLERANCE,
        ):
            raise ValueError("M8 portable repeated worker wall sum does not reconcile")

        worker_and_phase_seconds = (
            (
                self.generator_worker_wall_seconds,
                self.first_generation_phase_wall_seconds,
            ),
            (
                self.repeat_generator_worker_wall_seconds,
                self.second_generation_phase_wall_seconds,
            ),
            (self.checker_worker_wall_seconds, self.checker_phase_wall_seconds),
        )
        if any(
            worker_seconds > phase_seconds + _WALL_SECONDS_TOLERANCE
            for worker_seconds, phase_seconds in worker_and_phase_seconds
        ):
            raise ValueError("M8 portable controller phase excludes worker time")
        phase_sum_seconds = sum(
            phase_seconds for _worker_seconds, phase_seconds in worker_and_phase_seconds
        )
        if self.total_pipeline_wall_seconds + _WALL_SECONDS_TOLERANCE < phase_sum_seconds:
            raise ValueError("M8 portable pipeline wall time is shorter than its phase sum")

        if not isclose(
            self.generator_accounted_wall_fraction,
            self.generator_profile.accounted_wall_fraction,
            rel_tol=0.0,
            abs_tol=_FRACTION_TOLERANCE,
        ):
            raise ValueError("M8 portable generator accounted wall fraction differs")
        if not isclose(
            self.checker_accounted_wall_fraction,
            self.checker_profile.accounted_wall_fraction,
            rel_tol=0.0,
            abs_tol=_FRACTION_TOLERANCE,
        ):
            raise ValueError("M8 portable checker accounted wall fraction differs")

        def phase_occurrences(report: M8PortableProfileReport) -> Counter[str]:
            observed: Counter[str] = Counter()

            def visit(phase: M8PortableProfilePhase) -> None:
                observed[phase.name] += 1
                for child in phase.children:
                    visit(child)

            for phase in report.phases:
                visit(phase)
            return observed

        required_reports = (
            ("generator", self.generator_profile, _GENERATOR_PHASE_OCCURRENCES),
            ("checker", self.checker_profile, _CHECKER_PHASE_OCCURRENCES),
        )
        for role, report, required in required_reports:
            if phase_occurrences(report) != Counter(required):
                raise ValueError(
                    f"M8 portable {role} required phase occurrence contract differs"
                )
        expected_complete = (
            self.generator_accounted_wall_fraction >= self.minimum_accounted_wall_fraction
            and self.checker_accounted_wall_fraction >= self.minimum_accounted_wall_fraction
        )
        expected_decision = "profile_complete" if expected_complete else "profile_incomplete"
        if (
            self.measurement_complete is not expected_complete
            or self.measurement_decision != expected_decision
        ):
            raise ValueError("M8 portable measurement decision does not reconcile")

        digest = semantic_sha256(self, excluded_fields={"profile_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M8 portable profile content SHA-256 does not match semantic content")
        if self.profile_id != f"yfm8profile-{digest[:24]}":
            raise ValueError("M8 portable profile ID does not match semantic content")
        return self


__all__ = [
    "M8PortableCheckedIdentity",
    "M8PortableGenerationIdentity",
    "M8PortableHandoffTimings",
    "M8PortableHotspotProfileV2",
    "M8PortableProfileCounts",
    "M8PortableProfilePhase",
    "M8PortableProfileReport",
]
