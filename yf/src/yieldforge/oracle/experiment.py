"""Calibration-only certificate proof and hard gate for exact M8 rollout."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import perf_counter
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
    initial_m7_cursor,
)
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.oracle.checker import check_action_proofs
from yieldforge.oracle.proofs import M8ActionProof, M8EventClassification
from yieldforge.oracle.reference import M8OracleRequest, score_reference_action
from yieldforge.oracle.sparse import (
    M8SparseResult,
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
_WITNESS_ORDER: tuple[M8EventClassification, ...] = (
    "exact_transition",
    "no_fit",
    "policy_dominated",
    "state_rejoin",
)

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
        if self.witness_classifications != _classification_tuple(
            self.witness_classifications
        ):
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
            or self.proof_catalog_action_ids
            != tuple(sorted(set(self.proof_catalog_action_ids)))
            or self.current_action_ids != self.proof_catalog_action_ids
            or len(self.current_action_ids) != self.current_action_count
        ):
            raise ValueError("M8 cell proof action IDs do not cover the current catalog")
        if not set(self.audit_action_ids) <= set(self.current_action_ids):
            raise ValueError("M8 cell audit IDs are absent from the current catalog")
        if self.witness_classifications != _classification_tuple(
            self.witness_classifications
        ):
            raise ValueError("M8 cell witness classifications must be canonical and unique")
        if (
            self.checked_action_count != self.current_action_count
            or self.valid_proof_count + self.checker_failure_count
            != self.checked_action_count
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

    schema_version: Literal["yieldforge.m8-certificate-proof.v2"] = (
        "yieldforge.m8-certificate-proof.v2"
    )
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
    measured_process_count: Literal[1] = 1
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
    uncovered_witness_classifications: tuple[M8EventClassification, ...]
    uncovered_action_kinds: tuple[M8ActionKind, ...]
    uncovered_future_event_counts: tuple[StrictInt, ...]
    uncovered_regimes: tuple[TemporalRegime, ...]
    certificate_elapsed_seconds: StrictFloat = Field(gt=0)
    checker_elapsed_seconds: StrictFloat = Field(gt=0)
    certificate_pipeline_elapsed_seconds: StrictFloat = Field(gt=0)
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
        "require_distributed_exact",
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
        aggregates = _aggregate_certificate_metrics(self.cells, self.audit_bindings)
        for field_name, expected in aggregates.items():
            if getattr(self, field_name) != expected:
                raise ValueError(
                    f"M8 certificate aggregate {field_name} does not reconcile"
                )
        expected_decision = _gate_decision(
            current_action_count=self.current_action_count,
            checked_action_count=self.checked_action_count,
            valid_proof_count=self.valid_proof_count,
            checker_failure_count=self.checker_failure_count,
            sampled_checker_failure_count=self.sampled_checker_failure_count,
            audit_mismatch_count=self.audit_mismatch_count,
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
    tuple[M8ActionKind, ...],
    tuple[int, ...],
    tuple[TemporalRegime, ...],
]:
    by_cell = _audit_by_cell(audit_bindings)
    missing_witnesses: set[M8EventClassification] = set()
    missing_kinds: set[M8ActionKind] = set()
    for cell in cells:
        bindings = by_cell[cell.regime]
        audited_witnesses = {
            classification
            for item in bindings
            for classification in item.witness_classifications
        }
        audited_kinds = {item.action_kind for item in bindings}
        missing_witnesses.update(set(cell.witness_classifications) - audited_witnesses)
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
    return (
        _classification_tuple(missing_witnesses),
        tuple(sorted(missing_kinds)),
        missing_horizons,
        missing_regimes,
    )


def _aggregate_certificate_metrics(
    cells: tuple[M8CertificateProofCell, ...],
    audit_bindings: tuple[M8AuditActionBinding, ...],
) -> dict[str, object]:
    current = sum(item.current_action_count for item in cells)
    checked = sum(item.checked_action_count for item in cells)
    valid = sum(item.valid_proof_count for item in cells)
    failures = sum(item.checker_failure_count for item in cells)
    sampled_failures = sum(item.sampled_checker_failure_count for item in cells)
    mismatches = sum(item.audit_mismatch_count for item in cells)
    certificate_seconds = round(
        sum(item.certificate_elapsed_seconds for item in cells), 6
    )
    checker_seconds = round(sum(item.checker_elapsed_seconds for item in cells), 6)
    pipeline_seconds = round(certificate_seconds + checker_seconds, 6)
    reference_seconds = round(
        sum(item.sampled_reference_elapsed_seconds for item in cells), 6
    )
    sampled_certificate_seconds = round(
        sum(item.sampled_certificate_elapsed_seconds for item in cells), 6
    )
    sampled_checker_seconds = round(
        sum(item.sampled_checker_elapsed_seconds for item in cells), 6
    )
    sampled_pipeline_seconds = round(
        sampled_certificate_seconds + sampled_checker_seconds, 6
    )
    speedup = round(reference_seconds / sampled_pipeline_seconds, 6)
    throughput = round(current / pipeline_seconds, 6)
    observed_action_events = sum(
        item.current_action_count * max(1, item.future_event_count) for item in cells
    )
    projected_seconds = (
        pipeline_seconds
        / observed_action_events
        * _HELD_OUT_ACTION_COUNT
        * _HELD_OUT_MEAN_FUTURE_EVENT_COUNT
        * _PROJECTION_SAFETY_FACTOR
    )
    projected_days = round(projected_seconds / 86_400.0, 6)
    witness_classifications = _classification_tuple(
        {
            classification
            for item in cells
            for classification in item.witness_classifications
        }
    )
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
        "uncovered_witness_classifications": gaps[0],
        "uncovered_action_kinds": gaps[1],
        "uncovered_future_event_counts": gaps[2],
        "uncovered_regimes": gaps[3],
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
        or uncovered_witness_classifications
        or uncovered_action_kinds
        or uncovered_future_event_counts
        or uncovered_regimes
        or speedup < 20.0
    ):
        return "redesign_certificate_proof"
    if projected_days > 7.0:
        return "require_distributed_exact"
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
) -> M8CertificateProofResult:
    """Reconcile the six calibration cells and apply the revised hard gate."""

    ordered_cells = tuple(
        sorted(cells, key=lambda item: tuple(TemporalRegime).index(item.regime))
    )
    if len(ordered_cells) != 6 or tuple(
        item.regime for item in ordered_cells
    ) != tuple(TemporalRegime):
        raise ValueError("M8 certificate proof requires all six registered cells")
    ordered_bindings = tuple(sorted(audit_bindings, key=_audit_binding_key))
    _require_audit_reconciliation(ordered_cells, ordered_bindings)
    aggregates = _aggregate_certificate_metrics(ordered_cells, ordered_bindings)
    decision = _gate_decision(
        current_action_count=int(aggregates["current_action_count"]),
        checked_action_count=int(aggregates["checked_action_count"]),
        valid_proof_count=int(aggregates["valid_proof_count"]),
        checker_failure_count=int(aggregates["checker_failure_count"]),
        sampled_checker_failure_count=int(
            aggregates["sampled_checker_failure_count"]
        ),
        audit_mismatch_count=int(aggregates["audit_mismatch_count"]),
        uncovered_witness_classifications=aggregates[
            "uncovered_witness_classifications"
        ],  # type: ignore[arg-type]
        uncovered_action_kinds=aggregates["uncovered_action_kinds"],  # type: ignore[arg-type]
        uncovered_future_event_counts=aggregates[
            "uncovered_future_event_counts"
        ],  # type: ignore[arg-type]
        uncovered_regimes=aggregates["uncovered_regimes"],  # type: ignore[arg-type]
        speedup=float(aggregates["sampled_speedup"]),
        projected_days=float(aggregates["projected_held_out_calendar_days"]),
    )
    semantic = {
        "schema_version": "yieldforge.m8-certificate-proof.v2",
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
        "measured_process_count": 1,
        "held_out_action_count": _HELD_OUT_ACTION_COUNT,
        "audit_bindings": [item.model_dump(mode="json") for item in ordered_bindings],
        "audit_sample_sha256": audit_sample_sha256(ordered_bindings),
        "cells": [item.model_dump(mode="json") for item in ordered_cells],
        **aggregates,
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
class _AuditCandidate:
    binding: M8AuditActionBinding
    labels: frozenset[tuple[str, str, str]]


def _proof_classifications(
    proof: M8ActionProof,
) -> tuple[M8EventClassification, ...]:
    return _classification_tuple(
        {item.classification for item in proof.witnesses}
    )


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
        ("witness", regime, classification)
        for classification in binding.witness_classifications
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
    if {label for item in frozen for label in _candidate_labels(
        item, horizon_targets=horizon_targets
    )} != required:
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


def _execute_timed_cell(
    cell: _ExecutionCell,
    *,
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    audit_bindings: tuple[M8AuditActionBinding, ...],
    progress,  # type: ignore[no-untyped-def]
) -> M8CertificateProofCell:
    request = _request_for_cell(
        cell,
        rules=rules,
        jagua_executable=jagua_executable,
    )
    started = perf_counter()
    sparse = score_sparse_event(request)
    certificate_seconds = max(0.000001, round(perf_counter() - started, 6))
    started = perf_counter()
    checks = check_action_proofs(request, sparse.proofs)
    checker_seconds = max(0.000001, round(perf_counter() - started, 6))

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

    full_scores = {
        item.action_id: item.final_net_cost for item in sparse.decision.scores
    }
    audit_action_ids = tuple(item.catalog_action_id for item in audit_bindings)
    started = perf_counter()
    sampled = score_certificate_actions(request, action_ids=audit_action_ids)
    sampled_certificate_seconds = max(
        0.000001, round(perf_counter() - started, 6)
    )
    sampled_proofs = tuple(item.proof for item in sampled)
    started = perf_counter()
    sampled_checks = check_action_proofs(request, sampled_proofs)
    sampled_checker_seconds = max(0.000001, round(perf_counter() - started, 6))
    if (
        tuple(item.score.action_id for item in sampled) != audit_action_ids
        or len(sampled_checks) != len(audit_bindings)
    ):
        raise ValueError("M8 matched certificate batch differs from its frozen audit IDs")
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

    started = perf_counter()
    reference_scores = tuple(
        score_reference_action(request, action_id=item.catalog_action_id)
        for item in audit_bindings
    )
    reference_seconds = max(0.000001, round(perf_counter() - started, 6))
    audit_mismatches = sum(
        (
            item.final_net_cost
            != sampled_by_id[item.action_id].score.final_net_cost
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
    current_kinds = tuple(sorted({_action_kind(item) for item in current_action_ids}))
    valid = sum(item.valid for item in checks)
    failures = len(checks) - valid
    sampled_failures = sum(not item.valid for item in sampled_checks)
    if progress is not None:
        progress(
            f"certificate {cell.stream[0].regime.value} "
            f"actions={len(current_action_ids)} valid={valid} failures={failures} "
            f"certificate_seconds={certificate_seconds} checker_seconds={checker_seconds}"
        )
        progress(
            f"certificate audit {cell.stream[0].regime.value} "
            f"actions={len(audit_bindings)} sampled_checker_failures={sampled_failures} "
            f"mismatches={audit_mismatches} "
            f"sampled_certificate_seconds={sampled_certificate_seconds} "
            f"sampled_checker_seconds={sampled_checker_seconds} "
            f"reference_seconds={reference_seconds}"
        )
    proof_runtime_hashes = {item.semantic_runtime_sha256 for item in sparse.proofs}
    if len(proof_runtime_hashes) != 1:
        raise ValueError("M8 timed certificate proof runtime bindings differ")
    return M8CertificateProofCell(
        regime=cell.stream[0].regime,
        temporal_seed=cell.stream[0].temporal_seed,
        stream_id=cell.stream[0].stream_id,
        future_event_count=next(iter(audit_bindings)).future_event_count,
        semantic_runtime_sha256=next(iter(proof_runtime_hashes)),
        audit_action_ids=tuple(item.catalog_action_id for item in audit_bindings),
        audit_sample_sha256=audit_sample_sha256(audit_bindings),
        current_action_kinds=current_kinds,
        current_action_ids=tuple(sorted(current_action_ids)),
        proof_catalog_action_ids=tuple(sorted(proof_ids)),
        current_action_count=len(current_action_ids),
        checked_action_count=len(checks),
        valid_proof_count=valid,
        checker_failure_count=failures,
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
        certificate_elapsed_seconds=certificate_seconds,
        checker_elapsed_seconds=checker_seconds,
        sampled_certificate_elapsed_seconds=sampled_certificate_seconds,
        sampled_checker_elapsed_seconds=sampled_checker_seconds,
        sampled_checker_failure_count=sampled_failures,
        sampled_reference_elapsed_seconds=reference_seconds,
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

    contract = build_registered_contract()
    if (
        (index.full_problem_index_id, index.full_problem_index_sha256)
        != (frozen.problem_index_id, frozen.problem_index_sha256)
        or (m0.contract_id, m0.content_sha256)
        != (frozen.m0_contract_id, frozen.m0_contract_sha256)
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
    for offset, problem_id in enumerate(selected_problem_ids, start=1):
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),  # type: ignore[arg-type]
            archive_roots,
        )
        if progress is not None:
            progress(
                f"verified certificate candidate problem {offset}/{len(selected_problem_ids)}"
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
    candidates = []
    for cell in execution_cells:
        request = _request_for_cell(
            cell,
            rules=rules,
            jagua_executable=executable,
        )
        candidates.extend(
            _audit_candidates_for_cell(cell.stream, score_sparse_event(request))
        )
    frozen_audit = _freeze_audit_bindings(tuple(candidates))
    if progress is not None:
        progress(
            f"froze certificate audit actions={len(frozen_audit)} "
            f"sample={audit_sample_sha256(frozen_audit)}"
        )

    audit_by_cell = _audit_by_cell(frozen_audit)
    cells = tuple(
        _execute_timed_cell(
            cell,
            rules=rules,
            jagua_executable=executable,
            audit_bindings=audit_by_cell[cell.stream[0].regime],
            progress=progress,
        )
        for cell in execution_cells
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
        cells=cells,
        audit_bindings=frozen_audit,
    )


def publish_sparse_proof(
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
        json.dumps(result.model_dump(mode="json"), allow_nan=False, indent=2, sort_keys=True)
        + "\n"
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


__all__ = [
    "M8AuditActionBinding",
    "M8CertificateProofCell",
    "M8CertificateProofResult",
    "audit_sample_sha256",
    "execute_sparse_prefix_proof",
    "finalize_certificate_proof",
    "publish_sparse_proof",
]
