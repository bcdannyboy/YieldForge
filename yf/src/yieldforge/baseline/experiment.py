"""Registered M7 feasibility slice, collision gate, and immutable evidence."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import stat
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from statistics import fmean, median
from time import perf_counter
from typing import Literal, Self

import shapely
from pydantic import BaseModel, Field, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.archives import (
    canonical_m2_archive_references,
    verify_problem_candidates,
)
from yieldforge.baseline.contracts import (
    BaselineContractModel,
    M7CandidateSetEvidence,
    M7ProblemIndex,
)
from yieldforge.baseline.policies import (
    M7PolicyIdentity,
    M7PolicyName,
    policy_identity,
    registered_policy_identities,
)
from yieldforge.baseline.replay import (
    M7PreparedLayoutCache,
    M7ReplayRuntimeMetrics,
    M7SharedFitSearchCache,
    build_m7_replay_input,
    run_m7_replay,
)
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import RemnantFitConfig
from yieldforge.temporal_benchmark.contracts import (
    TemporalPartition,
    TemporalRegime,
    build_registered_contract,
)

REGISTERED_M7_FEASIBILITY_SEED = 2026082300
REGISTERED_M7_FEASIBILITY_POLICY = M7PolicyName.AGE_REGULARITY
REGISTERED_JAGUA_SEARCH_SHARE_THRESHOLD = 0.30
REGISTERED_JAGUA_PROJECTED_MINUTES_THRESHOLD = 15.0
REGISTERED_CALIBRATION_PROJECTION_FACTOR = 10.0
_MAX_FEASIBILITY_ARTIFACT_BYTES = 16 * 1024 * 1024


class M7CollisionBackendDecision(StrEnum):
    BUILD_JAGUA_DIFFERENTIAL_SPIKE = "build_jagua_differential_spike"
    DEFER_JAGUA = "defer_jagua"
    USE_VALIDATED_JAGUA_PREFILTER = "use_validated_jagua_prefilter"


class M7CollisionGateResult(BaselineContractModel):
    schema_version: Literal["yieldforge.m7-collision-gate.v1"] = "yieldforge.m7-collision-gate.v1"
    fit_search_share: StrictFloat = Field(ge=0, le=1)
    projected_calibration_minutes: StrictFloat = Field(ge=0)
    search_share_threshold: Literal[0.3] = 0.3
    projected_minutes_threshold: Literal[15.0] = 15.0
    calibration_projection_factor: Literal[10.0] = 10.0
    active_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ] = "shapely_authoritative"
    trigger_reasons: tuple[StrictStr, ...]
    decision: M7CollisionBackendDecision

    @model_validator(mode="after")
    def require_frozen_decision(self) -> Self:
        reasons = []
        if self.fit_search_share >= self.search_share_threshold:
            reasons.append("fit_search_share_at_least_0.30")
        if self.projected_calibration_minutes > self.projected_minutes_threshold:
            reasons.append("projected_calibration_minutes_above_15")
        if self.trigger_reasons != tuple(reasons):
            raise ValueError("M7 collision trigger reasons do not match frozen thresholds")
        if self.active_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness":
            expected = M7CollisionBackendDecision.USE_VALIDATED_JAGUA_PREFILTER
        else:
            expected = (
                M7CollisionBackendDecision.BUILD_JAGUA_DIFFERENTIAL_SPIKE
                if reasons
                else M7CollisionBackendDecision.DEFER_JAGUA
            )
        if self.decision is not expected:
            raise ValueError("M7 collision decision does not match frozen thresholds")
        return self


class M7CollisionDifferentialResult(BaselineContractModel):
    """Audited Jagua prefilter result on the first real recurrence opportunity."""

    schema_version: Literal["yieldforge.m7-collision-differential.v1"] = (
        "yieldforge.m7-collision-differential.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm7d-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    problem_index_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    temporal_seed: Literal[2026082300] = 2026082300
    regime: Literal[TemporalRegime.EXACT_RECURRENCE] = TemporalRegime.EXACT_RECURRENCE
    event_count: Literal[2] = 2
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    stream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    binding_ids: tuple[StrictStr, StrictStr]
    problem_ids: tuple[StrictStr, ...] = Field(min_length=1)
    candidate_problem_count: Literal[1] = 1
    candidate_archive_count: Literal[4] = 4
    raw_candidate_count: StrictInt = Field(ge=1)
    distinct_candidate_count: StrictInt = Field(ge=1)
    replay_input_id: StrictStr = Field(pattern=r"^yfm7ri-[0-9a-f]{24}$")
    replay_input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    replay_result_id: StrictStr = Field(pattern=r"^yfm7r-[0-9a-f]{24}$")
    replay_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    replay_elapsed_seconds: StrictFloat = Field(gt=0)
    standard_action_seconds: StrictFloat = Field(ge=0)
    translation_generation_seconds: StrictFloat = Field(gt=0)
    accelerated_evaluation_seconds: StrictFloat = Field(gt=0)
    authoritative_audit_seconds: StrictFloat = Field(gt=0)
    jagua_wall_seconds: StrictFloat = Field(ge=0)
    jagua_container_guard: Literal[1.0] = 1.0
    jagua_backend: Literal["jagua-rs"] = "jagua-rs"
    jagua_version: Literal["0.7.0"] = "0.7.0"
    jagua_coordinate_precision: Literal["f32"] = "f32"
    shapely_role: Literal["authoritative_witness_and_differential_oracle"] = (
        "authoritative_witness_and_differential_oracle"
    )
    fit_search_query_count: StrictInt = Field(ge=1)
    fit_search_evaluated_candidate_count: StrictInt = Field(ge=1)
    jagua_guarded_query_count: StrictInt = Field(ge=1)
    jagua_rejection_count: StrictInt = Field(ge=0)
    jagua_audit_search_count: StrictInt = Field(ge=1)
    jagua_audit_mismatch_count: StrictInt = Field(ge=0)
    measured_search_speedup: StrictFloat = Field(gt=0)
    measured_backend_speedup: StrictFloat = Field(gt=0)
    technical_decision: Literal[
        "validated_for_guarded_prefilter",
        "reject_guarded_prefilter",
    ]
    claim_ceiling: Literal[
        "software_collision_prefilter_differential_only_not_full_feasibility_policy_advantage_"
        "savings_physical_or_commercial_evidence"
    ] = (
        "software_collision_prefilter_differential_only_not_full_feasibility_policy_advantage_"
        "savings_physical_or_commercial_evidence"
    )

    @model_validator(mode="after")
    def require_complete_differential(self) -> Self:
        if len(set(self.binding_ids)) != self.event_count:
            raise ValueError("M7 collision differential binding IDs must be unique")
        if len(self.problem_ids) != self.candidate_problem_count:
            raise ValueError("M7 collision differential problem count differs")
        if self.distinct_candidate_count > self.raw_candidate_count:
            raise ValueError("M7 collision differential distinct candidates exceed raw")
        if self.jagua_rejection_count > self.jagua_guarded_query_count:
            raise ValueError("M7 Jagua rejections exceed guarded queries")
        if self.jagua_audit_search_count != self.fit_search_query_count:
            raise ValueError("M7 Jagua audit did not cover every layout search")
        expected_speedup = round(
            (self.translation_generation_seconds + self.authoritative_audit_seconds)
            / self.accelerated_evaluation_seconds,
            6,
        )
        if self.measured_search_speedup != expected_speedup:
            raise ValueError("M7 collision differential speedup does not reconcile")
        expected_backend_speedup = round(
            self.authoritative_audit_seconds / self.accelerated_evaluation_seconds,
            6,
        )
        if self.measured_backend_speedup != expected_backend_speedup:
            raise ValueError("M7 collision backend speedup does not reconcile")
        expected_decision = (
            "validated_for_guarded_prefilter"
            if self.jagua_audit_mismatch_count == 0
            else "reject_guarded_prefilter"
        )
        if self.technical_decision != expected_decision:
            raise ValueError("M7 collision differential decision differs from audit")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M7 collision differential SHA-256 does not match content")
        if self.result_id != f"yfm7d-{digest[:24]}":
            raise ValueError("M7 collision differential ID does not match content")
        return self


def finalize_collision_differential_result(
    *,
    m0_contract_id: str,
    m0_contract_sha256: str,
    problem_index_id: str,
    problem_index_sha256: str,
    stream_id: str,
    stream_sha256: str,
    binding_ids: tuple[str, str],
    problem_ids: tuple[str, ...],
    raw_candidate_count: int,
    distinct_candidate_count: int,
    replay_input_id: str,
    replay_input_sha256: str,
    replay_result_id: str,
    replay_result_sha256: str,
    replay_elapsed_seconds: float,
    standard_action_seconds: float,
    translation_generation_seconds: float,
    accelerated_evaluation_seconds: float,
    authoritative_audit_seconds: float,
    jagua_wall_seconds: float,
    fit_search_query_count: int,
    fit_search_evaluated_candidate_count: int,
    jagua_guarded_query_count: int,
    jagua_rejection_count: int,
    jagua_audit_search_count: int,
    jagua_audit_mismatch_count: int,
) -> M7CollisionDifferentialResult:
    """Bind observed recurrence-probe metrics to one immutable decision."""

    rounded_generation = round(translation_generation_seconds, 6)
    rounded_accelerated = round(accelerated_evaluation_seconds, 6)
    rounded_authoritative = round(authoritative_audit_seconds, 6)
    semantic = {
        "schema_version": "yieldforge.m7-collision-differential.v1",
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "problem_index_id": problem_index_id,
        "problem_index_sha256": problem_index_sha256,
        "temporal_seed": REGISTERED_M7_FEASIBILITY_SEED,
        "regime": TemporalRegime.EXACT_RECURRENCE,
        "event_count": 2,
        "stream_id": stream_id,
        "stream_sha256": stream_sha256,
        "binding_ids": binding_ids,
        "problem_ids": problem_ids,
        "candidate_problem_count": 1,
        "candidate_archive_count": 4,
        "raw_candidate_count": raw_candidate_count,
        "distinct_candidate_count": distinct_candidate_count,
        "replay_input_id": replay_input_id,
        "replay_input_sha256": replay_input_sha256,
        "replay_result_id": replay_result_id,
        "replay_result_sha256": replay_result_sha256,
        "replay_elapsed_seconds": round(replay_elapsed_seconds, 6),
        "standard_action_seconds": round(standard_action_seconds, 6),
        "translation_generation_seconds": rounded_generation,
        "accelerated_evaluation_seconds": rounded_accelerated,
        "authoritative_audit_seconds": rounded_authoritative,
        "jagua_wall_seconds": round(jagua_wall_seconds, 6),
        "jagua_container_guard": 1.0,
        "jagua_backend": "jagua-rs",
        "jagua_version": "0.7.0",
        "jagua_coordinate_precision": "f32",
        "shapely_role": "authoritative_witness_and_differential_oracle",
        "fit_search_query_count": fit_search_query_count,
        "fit_search_evaluated_candidate_count": fit_search_evaluated_candidate_count,
        "jagua_guarded_query_count": jagua_guarded_query_count,
        "jagua_rejection_count": jagua_rejection_count,
        "jagua_audit_search_count": jagua_audit_search_count,
        "jagua_audit_mismatch_count": jagua_audit_mismatch_count,
        "measured_search_speedup": round(
            (rounded_generation + rounded_authoritative) / rounded_accelerated,
            6,
        ),
        "measured_backend_speedup": round(
            rounded_authoritative / rounded_accelerated,
            6,
        ),
        "technical_decision": (
            "validated_for_guarded_prefilter"
            if jagua_audit_mismatch_count == 0
            else "reject_guarded_prefilter"
        ),
        "claim_ceiling": (
            "software_collision_prefilter_differential_only_not_full_feasibility_policy_"
            "advantage_savings_physical_or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M7CollisionDifferentialResult(
        result_id=f"yfm7d-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def execute_collision_differential_probe(
    *,
    index: M7ProblemIndex,
    m0: M0ExperimentContract,
    archive_roots: Path | tuple[Path, ...],
    jagua_executable: Path,
    progress: Callable[[str], None] | None = None,
) -> M7CollisionDifferentialResult:
    """Audit Jagua against every Shapely search in the first recurrence opportunity."""

    contract = build_registered_contract()
    if (
        index.m6_contract_id != contract.contract_id
        or index.m6_contract_sha256 != contract.content_sha256
        or (m0.contract_id, m0.content_sha256)
        != (contract.m0_contract_id, contract.m0_contract_sha256)
    ):
        raise ValueError("M7 collision probe sources do not share the registered M0/M6 binding")
    recurrence = tuple(
        sorted(
            (
                item
                for item in select_feasibility_instances(index)
                if item.regime is TemporalRegime.EXACT_RECURRENCE
            ),
            key=lambda item: item.sequence,
        )
    )
    probe_instances = recurrence[:2]
    problem_ids = tuple(sorted({item.problem_id for item in probe_instances}))
    if (
        len(probe_instances) != 2
        or tuple(item.sequence for item in probe_instances) != (0, 1)
        or len({item.stream_id for item in probe_instances}) != 1
        or len(problem_ids) != 1
    ):
        raise ValueError("M7 collision probe differs from the frozen recurrence opportunity")
    problem_by_id = {item.problem_id: item for item in index.problems}
    references_by_task = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)
    verified = {}
    for problem_id in problem_ids:
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),
            archive_roots,
        )
        if progress is not None:
            progress("verified exact-recurrence collision-probe candidates")
    replay_input = build_m7_replay_input(
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        problem_index_id=index.index_id,
        problem_index_sha256=index.content_sha256,
        m6_contract_id=index.m6_contract_id,
        m6_contract_sha256=index.m6_contract_sha256,
        m6_population_id=index.m6_population_id,
        m6_population_sha256=index.m6_population_sha256,
        policy=policy_identity(REGISTERED_M7_FEASIBILITY_POLICY),
        rates=contract.rates,
        fit_config=RemnantFitConfig(),
        problems=tuple(problem_by_id[item] for item in problem_ids),
        candidate_sets=tuple(verified[item].evidence for item in problem_ids),
        instances=probe_instances,
        horizon_end=probe_instances[-1].released_at
        + timedelta(minutes=contract.timing.interval_minutes),
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_container_guard=1.0,
    )
    runtime = M7ReplayRuntimeMetrics()
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as executor:
        replay = run_m7_replay(
            replay_input,
            verified,
            rule_set_from_m0(m0.remnant_eligibility),
            runtime_metrics=runtime,
            standard_profile_executor=executor,
            jagua_executable=Path(jagua_executable),
            jagua_differential_audit=True,
            progress=(
                (
                    lambda completed, total: progress(
                        f"audited collision-probe event {completed}/{total}"
                    )
                )
                if progress is not None
                else None
            ),
        )
    evidence = verified[problem_ids[0]].evidence
    return finalize_collision_differential_result(
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        problem_index_id=index.index_id,
        problem_index_sha256=index.content_sha256,
        stream_id=replay_input.stream_id,
        stream_sha256=replay_input.stream_sha256,
        binding_ids=tuple(item.binding_id for item in probe_instances),  # type: ignore[arg-type]
        problem_ids=problem_ids,
        raw_candidate_count=evidence.raw_candidate_count,
        distinct_candidate_count=evidence.distinct_candidate_count,
        replay_input_id=replay_input.input_id,
        replay_input_sha256=replay_input.content_sha256,
        replay_result_id=replay.result_id,
        replay_result_sha256=replay.content_sha256,
        replay_elapsed_seconds=runtime.replay_elapsed_seconds,
        standard_action_seconds=runtime.standard_action_seconds,
        translation_generation_seconds=runtime.jagua_translation_generation_seconds,
        accelerated_evaluation_seconds=runtime.jagua_accelerated_evaluation_seconds,
        authoritative_audit_seconds=runtime.jagua_authoritative_audit_seconds,
        jagua_wall_seconds=runtime.jagua_wall_seconds,
        fit_search_query_count=replay.summary.total_fit_search_query_count,
        fit_search_evaluated_candidate_count=(
            replay.summary.total_fit_search_evaluated_candidate_count
        ),
        jagua_guarded_query_count=runtime.jagua_guarded_query_count,
        jagua_rejection_count=runtime.jagua_rejection_count,
        jagua_audit_search_count=runtime.jagua_audit_search_count,
        jagua_audit_mismatch_count=runtime.jagua_audit_mismatch_count,
    )


def evaluate_collision_gate(
    *,
    total_replay_seconds: float,
    fit_search_seconds: float,
    active_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ] = "shapely_authoritative",
) -> M7CollisionGateResult:
    """Apply the preregistered Jagua gate to measured one-policy pilot runtime."""

    if total_replay_seconds <= 0 or fit_search_seconds < 0:
        raise ValueError("M7 collision gate requires positive finite replay timing")
    if fit_search_seconds > total_replay_seconds:
        raise ValueError("M7 fit-search time cannot exceed total replay time")
    share = round(fit_search_seconds / total_replay_seconds, 6)
    projected = round(
        total_replay_seconds * REGISTERED_CALIBRATION_PROJECTION_FACTOR / 60.0,
        6,
    )
    reasons = []
    if share >= REGISTERED_JAGUA_SEARCH_SHARE_THRESHOLD:
        reasons.append("fit_search_share_at_least_0.30")
    if projected > REGISTERED_JAGUA_PROJECTED_MINUTES_THRESHOLD:
        reasons.append("projected_calibration_minutes_above_15")
    return M7CollisionGateResult(
        fit_search_share=share,
        projected_calibration_minutes=projected,
        active_backend=active_backend,
        trigger_reasons=tuple(reasons),
        decision=(
            M7CollisionBackendDecision.USE_VALIDATED_JAGUA_PREFILTER
            if active_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
            else (
                M7CollisionBackendDecision.BUILD_JAGUA_DIFFERENTIAL_SPIKE
                if reasons
                else M7CollisionBackendDecision.DEFER_JAGUA
            )
        ),
    )


class M7FeasibilityStreamResult(BaselineContractModel):
    regime: TemporalRegime
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    stream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    instance_count: Literal[24] = 24
    problem_count: StrictInt = Field(ge=1)
    candidate_set_count: StrictInt = Field(ge=1)
    raw_candidate_count: StrictInt = Field(ge=1)
    distinct_candidate_count: StrictInt = Field(ge=1)
    replay_input_id: StrictStr = Field(pattern=r"^yfm7ri-[0-9a-f]{24}$")
    replay_input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    replay_result_id: StrictStr = Field(pattern=r"^yfm7r-[0-9a-f]{24}$")
    replay_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    replay_elapsed_seconds: StrictFloat = Field(gt=0)
    standard_action_seconds: StrictFloat = Field(ge=0)
    fit_search_seconds: StrictFloat = Field(ge=0)
    remnant_action_materialization_seconds: StrictFloat = Field(ge=0)
    action_count: StrictInt = Field(ge=24)
    fit_search_query_count: StrictInt = Field(ge=0)
    fit_search_evaluated_candidate_count: StrictInt = Field(ge=0)
    jagua_guarded_query_count: StrictInt = Field(default=0, ge=0)
    jagua_rejection_count: StrictInt = Field(default=0, ge=0)
    jagua_wall_seconds: StrictFloat = Field(default=0.0, ge=0)
    fit_search_cache_hit_count: StrictInt = Field(default=0, ge=0)
    fit_search_cache_miss_count: StrictInt = Field(default=0, ge=0)
    full_sheet_opening_count: StrictInt = Field(ge=0)
    remnant_retrieval_count: StrictInt = Field(ge=0)
    terminal_remnant_count: StrictInt = Field(ge=0)
    final_net_cost: StrictFloat
    technical_decision: Literal["pass"] = "pass"

    @model_validator(mode="after")
    def require_complete_stream_metrics(self) -> Self:
        if self.problem_count != self.candidate_set_count:
            raise ValueError("M7 stream problems and candidate sets do not reconcile")
        if self.distinct_candidate_count > self.raw_candidate_count:
            raise ValueError("M7 stream distinct candidates exceed raw candidates")
        if self.full_sheet_opening_count + self.remnant_retrieval_count != self.instance_count:
            raise ValueError("M7 stream action selections do not fulfill every instance")
        if self.jagua_rejection_count > self.jagua_guarded_query_count:
            raise ValueError("M7 stream Jagua rejections exceed guarded queries")
        measured = (
            self.standard_action_seconds
            + self.fit_search_seconds
            + self.remnant_action_materialization_seconds
        )
        if measured > self.replay_elapsed_seconds + 1e-6:
            raise ValueError("M7 timed action components exceed replay elapsed time")
        return self


class M7FeasibilityResult(BaselineContractModel):
    schema_version: Literal["yieldforge.m7-feasibility-result.v1"] = (
        "yieldforge.m7-feasibility-result.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm7f-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    problem_index_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    temporal_seed: Literal[2026082300] = 2026082300
    policy: M7PolicyIdentity
    stream_count: Literal[6] = 6
    instance_count: Literal[144] = 144
    candidate_problem_count: Literal[51] = 51
    candidate_archive_count: Literal[204] = 204
    raw_candidate_count: StrictInt = Field(ge=1)
    distinct_candidate_count: StrictInt = Field(ge=1)
    candidate_verification_seconds: StrictFloat = Field(ge=0)
    collision_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ] = "shapely_authoritative"
    collision_differential_result_id: StrictStr | None = None
    collision_differential_result_sha256: StrictStr | None = None
    streams: tuple[
        M7FeasibilityStreamResult,
        M7FeasibilityStreamResult,
        M7FeasibilityStreamResult,
        M7FeasibilityStreamResult,
        M7FeasibilityStreamResult,
        M7FeasibilityStreamResult,
    ]
    total_replay_seconds: StrictFloat = Field(gt=0)
    total_standard_action_seconds: StrictFloat = Field(ge=0)
    total_fit_search_seconds: StrictFloat = Field(ge=0)
    total_remnant_action_materialization_seconds: StrictFloat = Field(ge=0)
    total_action_count: StrictInt = Field(ge=144)
    total_fit_search_query_count: StrictInt = Field(ge=0)
    total_fit_search_evaluated_candidate_count: StrictInt = Field(ge=0)
    total_jagua_guarded_query_count: StrictInt = Field(default=0, ge=0)
    total_jagua_rejection_count: StrictInt = Field(default=0, ge=0)
    total_jagua_wall_seconds: StrictFloat = Field(default=0.0, ge=0)
    total_fit_search_cache_hit_count: StrictInt = Field(default=0, ge=0)
    total_fit_search_cache_miss_count: StrictInt = Field(default=0, ge=0)
    collision_gate: M7CollisionGateResult
    technical_decision: Literal[
        "ready_for_calibration",
        "ready_for_calibration_with_validated_jagua_prefilter",
        "collision_accelerator_required_before_calibration",
    ]
    claim_ceiling: Literal[
        "software_feasibility_and_collision_backend_decision_only_not_policy_advantage_savings_"
        "physical_or_commercial_evidence"
    ] = (
        "software_feasibility_and_collision_backend_decision_only_not_policy_advantage_savings_"
        "physical_or_commercial_evidence"
    )

    @model_validator(mode="after")
    def require_complete_metrics_and_identity(self) -> Self:
        if tuple(item.regime for item in self.streams) != tuple(TemporalRegime):
            raise ValueError("M7 feasibility streams must follow registered regime order")
        if len({item.stream_id for item in self.streams}) != self.stream_count:
            raise ValueError("M7 feasibility stream IDs must be unique")
        if sum(item.instance_count for item in self.streams) != self.instance_count:
            raise ValueError("M7 feasibility instance count does not reconcile")
        jagua_active = self.collision_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
        if jagua_active != (
            self.collision_differential_result_id is not None
            and self.collision_differential_result_sha256 is not None
        ):
            raise ValueError("M7 Jagua feasibility must bind its differential result")
        expected_totals = (
            round(sum(item.replay_elapsed_seconds for item in self.streams), 6),
            round(sum(item.standard_action_seconds for item in self.streams), 6),
            round(sum(item.fit_search_seconds for item in self.streams), 6),
            round(
                sum(item.remnant_action_materialization_seconds for item in self.streams),
                6,
            ),
            sum(item.action_count for item in self.streams),
            sum(item.fit_search_query_count for item in self.streams),
            sum(item.fit_search_evaluated_candidate_count for item in self.streams),
            sum(item.jagua_guarded_query_count for item in self.streams),
            sum(item.jagua_rejection_count for item in self.streams),
            round(sum(item.jagua_wall_seconds for item in self.streams), 6),
            sum(item.fit_search_cache_hit_count for item in self.streams),
            sum(item.fit_search_cache_miss_count for item in self.streams),
        )
        observed_totals = (
            self.total_replay_seconds,
            self.total_standard_action_seconds,
            self.total_fit_search_seconds,
            self.total_remnant_action_materialization_seconds,
            self.total_action_count,
            self.total_fit_search_query_count,
            self.total_fit_search_evaluated_candidate_count,
            self.total_jagua_guarded_query_count,
            self.total_jagua_rejection_count,
            self.total_jagua_wall_seconds,
            self.total_fit_search_cache_hit_count,
            self.total_fit_search_cache_miss_count,
        )
        if observed_totals != expected_totals:
            raise ValueError("M7 feasibility aggregate metrics do not reconcile")
        expected_gate = evaluate_collision_gate(
            total_replay_seconds=self.total_replay_seconds,
            fit_search_seconds=self.total_fit_search_seconds,
            active_backend=self.collision_backend,
        )
        if self.collision_gate != expected_gate:
            raise ValueError("M7 feasibility collision gate differs from measured timing")
        if jagua_active:
            expected_decision = "ready_for_calibration_with_validated_jagua_prefilter"
        else:
            expected_decision = (
                "collision_accelerator_required_before_calibration"
                if self.collision_gate.decision
                is M7CollisionBackendDecision.BUILD_JAGUA_DIFFERENTIAL_SPIKE
                else "ready_for_calibration"
            )
        if self.technical_decision != expected_decision:
            raise ValueError("M7 feasibility technical decision differs from collision gate")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M7 feasibility content SHA-256 does not match semantic content")
        if self.result_id != f"yfm7f-{digest[:24]}":
            raise ValueError("M7 feasibility result ID does not match semantic content")
        return self


def select_feasibility_instances(index: M7ProblemIndex) -> tuple:
    """Select every event in the first calibration seed for all six regimes."""

    instances = tuple(
        item
        for item in index.instances
        if item.partition is TemporalPartition.CALIBRATION
        and item.temporal_seed == REGISTERED_M7_FEASIBILITY_SEED
    )
    if (
        len(instances) != 144
        or len({item.stream_id for item in instances}) != 6
        or len({item.problem_id for item in instances}) != 51
        or tuple(dict.fromkeys(item.regime for item in instances)) != tuple(TemporalRegime)
    ):
        raise ValueError("M7 feasibility slice differs from frozen census")
    return instances


def finalize_feasibility_result(
    *,
    m0_contract_id: str,
    m0_contract_sha256: str,
    problem_index_id: str,
    problem_index_sha256: str,
    policy: M7PolicyIdentity,
    candidate_problem_count: int,
    candidate_archive_count: int,
    raw_candidate_count: int,
    distinct_candidate_count: int,
    streams: tuple[M7FeasibilityStreamResult, ...],
    candidate_verification_seconds: float = 0.0,
    collision_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ] = "shapely_authoritative",
    collision_differential_result_id: str | None = None,
    collision_differential_result_sha256: str | None = None,
) -> M7FeasibilityResult:
    """Reconcile measured stream evidence and derive the frozen backend decision."""

    ordered = tuple(sorted(streams, key=lambda item: tuple(TemporalRegime).index(item.regime)))
    total_replay = round(sum(item.replay_elapsed_seconds for item in ordered), 6)
    total_fit = round(sum(item.fit_search_seconds for item in ordered), 6)
    gate = evaluate_collision_gate(
        total_replay_seconds=total_replay,
        fit_search_seconds=total_fit,
        active_backend=collision_backend,
    )
    semantic = {
        "schema_version": "yieldforge.m7-feasibility-result.v1",
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "problem_index_id": problem_index_id,
        "problem_index_sha256": problem_index_sha256,
        "temporal_seed": REGISTERED_M7_FEASIBILITY_SEED,
        "policy": policy.model_dump(mode="json"),
        "stream_count": 6,
        "instance_count": 144,
        "candidate_problem_count": candidate_problem_count,
        "candidate_archive_count": candidate_archive_count,
        "raw_candidate_count": raw_candidate_count,
        "distinct_candidate_count": distinct_candidate_count,
        "candidate_verification_seconds": round(candidate_verification_seconds, 6),
        "collision_backend": collision_backend,
        "collision_differential_result_id": collision_differential_result_id,
        "collision_differential_result_sha256": collision_differential_result_sha256,
        "streams": [item.model_dump(mode="json") for item in ordered],
        "total_replay_seconds": total_replay,
        "total_standard_action_seconds": round(
            sum(item.standard_action_seconds for item in ordered), 6
        ),
        "total_fit_search_seconds": total_fit,
        "total_remnant_action_materialization_seconds": round(
            sum(item.remnant_action_materialization_seconds for item in ordered), 6
        ),
        "total_action_count": sum(item.action_count for item in ordered),
        "total_fit_search_query_count": sum(item.fit_search_query_count for item in ordered),
        "total_fit_search_evaluated_candidate_count": sum(
            item.fit_search_evaluated_candidate_count for item in ordered
        ),
        "total_jagua_guarded_query_count": sum(item.jagua_guarded_query_count for item in ordered),
        "total_jagua_rejection_count": sum(item.jagua_rejection_count for item in ordered),
        "total_jagua_wall_seconds": round(sum(item.jagua_wall_seconds for item in ordered), 6),
        "total_fit_search_cache_hit_count": sum(
            item.fit_search_cache_hit_count for item in ordered
        ),
        "total_fit_search_cache_miss_count": sum(
            item.fit_search_cache_miss_count for item in ordered
        ),
        "collision_gate": gate.model_dump(mode="json"),
        "technical_decision": (
            "ready_for_calibration_with_validated_jagua_prefilter"
            if collision_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
            else (
                "collision_accelerator_required_before_calibration"
                if gate.decision is M7CollisionBackendDecision.BUILD_JAGUA_DIFFERENTIAL_SPIKE
                else "ready_for_calibration"
            )
        ),
        "claim_ceiling": (
            "software_feasibility_and_collision_backend_decision_only_not_policy_advantage_"
            "savings_physical_or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M7FeasibilityResult(
        result_id=f"yfm7f-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m0_contract_id=m0_contract_id,
        m0_contract_sha256=m0_contract_sha256,
        problem_index_id=problem_index_id,
        problem_index_sha256=problem_index_sha256,
        policy=policy,
        candidate_problem_count=candidate_problem_count,
        candidate_archive_count=candidate_archive_count,
        raw_candidate_count=raw_candidate_count,
        distinct_candidate_count=distinct_candidate_count,
        candidate_verification_seconds=round(candidate_verification_seconds, 6),
        collision_backend=collision_backend,
        collision_differential_result_id=collision_differential_result_id,
        collision_differential_result_sha256=collision_differential_result_sha256,
        streams=ordered,  # type: ignore[arg-type]
        total_replay_seconds=total_replay,
        total_standard_action_seconds=semantic["total_standard_action_seconds"],
        total_fit_search_seconds=total_fit,
        total_remnant_action_materialization_seconds=semantic[
            "total_remnant_action_materialization_seconds"
        ],
        total_action_count=semantic["total_action_count"],
        total_fit_search_query_count=semantic["total_fit_search_query_count"],
        total_fit_search_evaluated_candidate_count=semantic[
            "total_fit_search_evaluated_candidate_count"
        ],
        total_jagua_guarded_query_count=semantic["total_jagua_guarded_query_count"],
        total_jagua_rejection_count=semantic["total_jagua_rejection_count"],
        total_jagua_wall_seconds=semantic["total_jagua_wall_seconds"],
        total_fit_search_cache_hit_count=semantic["total_fit_search_cache_hit_count"],
        total_fit_search_cache_miss_count=semantic["total_fit_search_cache_miss_count"],
        collision_gate=gate,
        technical_decision=semantic["technical_decision"],
    )


def execute_feasibility_slice(
    *,
    index: M7ProblemIndex,
    m0: M0ExperimentContract,
    archive_roots: Path | tuple[Path, ...],
    jagua_executable: Path | None = None,
    collision_differential: M7CollisionDifferentialResult | None = None,
    progress: Callable[[str], None] | None = None,
) -> M7FeasibilityResult:
    """Verify 204 archives and replay one stress policy over the six-stream slice."""

    contract = build_registered_contract()
    if (
        index.m6_contract_id != contract.contract_id
        or index.m6_contract_sha256 != contract.content_sha256
        or (m0.contract_id, m0.content_sha256)
        != (contract.m0_contract_id, contract.m0_contract_sha256)
    ):
        raise ValueError("M7 feasibility sources do not share the registered M0/M6 binding")
    jagua_active = jagua_executable is not None
    if jagua_active != (collision_differential is not None):
        raise ValueError("M7 Jagua feasibility requires executable and differential together")
    if collision_differential is not None and (
        collision_differential.technical_decision != "validated_for_guarded_prefilter"
        or collision_differential.jagua_audit_mismatch_count != 0
        or collision_differential.m0_contract_id != m0.contract_id
        or collision_differential.problem_index_id != index.index_id
    ):
        raise ValueError("M7 feasibility collision differential is not a validated binding")
    backend = (
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
        if jagua_active
        else "shapely_authoritative"
    )
    instances = select_feasibility_instances(index)
    selected_problem_ids = {item.problem_id for item in instances}
    problem_by_id = {item.problem_id: item for item in index.problems}
    references_by_task = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)

    verification_started = perf_counter()
    verified = {}
    for offset, problem_id in enumerate(sorted(selected_problem_ids), start=1):
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),
            archive_roots,
        )
        if progress is not None:
            progress(f"verified candidate problem {offset}/51")
    verification_seconds = perf_counter() - verification_started

    stream_results = []
    standard_profile_cache = {}
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as profile_executor:
        for regime in TemporalRegime:
            stream_instances = tuple(item for item in instances if item.regime is regime)
            stream_problem_ids = sorted({item.problem_id for item in stream_instances})
            stream_problems = tuple(problem_by_id[item] for item in stream_problem_ids)
            stream_candidates = tuple(verified[item].evidence for item in stream_problem_ids)
            replay_input = build_m7_replay_input(
                m0_contract_id=m0.contract_id,
                m0_contract_sha256=m0.content_sha256,
                problem_index_id=index.index_id,
                problem_index_sha256=index.content_sha256,
                m6_contract_id=index.m6_contract_id,
                m6_contract_sha256=index.m6_contract_sha256,
                m6_population_id=index.m6_population_id,
                m6_population_sha256=index.m6_population_sha256,
                policy=policy_identity(REGISTERED_M7_FEASIBILITY_POLICY),
                rates=contract.rates,
                fit_config=RemnantFitConfig(),
                problems=stream_problems,
                candidate_sets=stream_candidates,
                instances=stream_instances,
                horizon_end=stream_instances[-1].released_at
                + timedelta(minutes=contract.timing.interval_minutes),
                collision_backend=backend,
                jagua_container_guard=1.0 if jagua_active else None,
            )
            runtime = M7ReplayRuntimeMetrics()
            replay = run_m7_replay(
                replay_input,
                {item: verified[item] for item in stream_problem_ids},
                rule_set_from_m0(m0.remnant_eligibility),
                runtime_metrics=runtime,
                standard_profile_cache=standard_profile_cache,
                standard_profile_executor=profile_executor,
                jagua_executable=jagua_executable,
                progress=(
                    (
                        lambda completed, total, name=regime.value, metrics=runtime: progress(
                            f"replaying {name} event {completed}/{total} "
                            f"standard_seconds={metrics.standard_action_seconds:.3f} "
                            f"fit_seconds={metrics.fit_search_seconds:.3f}"
                        )
                    )
                    if progress is not None
                    else None
                ),
            )
            stream_results.append(
                M7FeasibilityStreamResult(
                    regime=regime,
                    stream_id=replay_input.stream_id,
                    stream_sha256=replay_input.stream_sha256,
                    problem_count=len(stream_problem_ids),
                    candidate_set_count=len(stream_problem_ids),
                    raw_candidate_count=sum(
                        verified[item].evidence.raw_candidate_count for item in stream_problem_ids
                    ),
                    distinct_candidate_count=sum(
                        verified[item].evidence.distinct_candidate_count
                        for item in stream_problem_ids
                    ),
                    replay_input_id=replay_input.input_id,
                    replay_input_sha256=replay_input.content_sha256,
                    replay_result_id=replay.result_id,
                    replay_result_sha256=replay.content_sha256,
                    replay_elapsed_seconds=round(runtime.replay_elapsed_seconds, 6),
                    standard_action_seconds=round(runtime.standard_action_seconds, 6),
                    fit_search_seconds=round(runtime.fit_search_seconds, 6),
                    remnant_action_materialization_seconds=round(
                        runtime.remnant_action_materialization_seconds, 6
                    ),
                    action_count=replay.summary.total_action_count,
                    fit_search_query_count=replay.summary.total_fit_search_query_count,
                    fit_search_evaluated_candidate_count=(
                        replay.summary.total_fit_search_evaluated_candidate_count
                    ),
                    jagua_guarded_query_count=runtime.jagua_guarded_query_count,
                    jagua_rejection_count=runtime.jagua_rejection_count,
                    jagua_wall_seconds=round(runtime.jagua_wall_seconds, 6),
                    fit_search_cache_hit_count=runtime.fit_search_cache_hit_count,
                    fit_search_cache_miss_count=runtime.fit_search_cache_miss_count,
                    full_sheet_opening_count=replay.summary.full_sheet_opening_count,
                    remnant_retrieval_count=replay.summary.remnant_retrieval_count,
                    terminal_remnant_count=replay.summary.terminal_remnant_count,
                    final_net_cost=replay.summary.final_net_cost,
                )
            )
            if progress is not None:
                progress(f"replayed feasibility regime {regime.value}")

    return finalize_feasibility_result(
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        problem_index_id=index.index_id,
        problem_index_sha256=index.content_sha256,
        policy=policy_identity(REGISTERED_M7_FEASIBILITY_POLICY),
        candidate_problem_count=len(selected_problem_ids),
        candidate_archive_count=len(selected_problem_ids) * 4,
        raw_candidate_count=sum(item.evidence.raw_candidate_count for item in verified.values()),
        distinct_candidate_count=sum(
            item.evidence.distinct_candidate_count for item in verified.values()
        ),
        streams=tuple(stream_results),
        candidate_verification_seconds=verification_seconds,
        collision_backend=backend,
        collision_differential_result_id=(
            collision_differential.result_id if collision_differential is not None else None
        ),
        collision_differential_result_sha256=(
            collision_differential.content_sha256 if collision_differential is not None else None
        ),
    )


class M7CalibrationStreamResult(BaselineContractModel):
    """One complete calibration-only policy replay."""

    policy: M7PolicyName
    regime: TemporalRegime
    temporal_seed: StrictInt
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    stream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    instance_count: Literal[24] = 24
    replay_input_id: StrictStr = Field(pattern=r"^yfm7ri-[0-9a-f]{24}$")
    replay_input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    replay_result_id: StrictStr = Field(pattern=r"^yfm7r-[0-9a-f]{24}$")
    replay_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    replay_elapsed_seconds: StrictFloat = Field(gt=0)
    fit_search_seconds: StrictFloat = Field(ge=0)
    fit_search_cache_hit_count: StrictInt = Field(ge=0)
    fit_search_cache_miss_count: StrictInt = Field(ge=0)
    action_count: StrictInt = Field(ge=24)
    full_sheet_opening_count: StrictInt = Field(ge=0)
    remnant_retrieval_count: StrictInt = Field(ge=0)
    final_net_cost: StrictFloat
    technical_decision: Literal["pass"] = "pass"

    @model_validator(mode="after")
    def require_complete_replay(self) -> Self:
        if self.full_sheet_opening_count + self.remnant_retrieval_count != self.instance_count:
            raise ValueError("M7 calibration replay did not fulfill every instance")
        if self.fit_search_seconds > self.replay_elapsed_seconds + 1e-6:
            raise ValueError("M7 calibration fit-search time exceeds replay time")
        return self


class M7CalibrationPolicyScore(BaselineContractModel):
    """Frozen selector terms for one preregistered policy."""

    policy: M7PolicyIdentity
    stream_count: Literal[12] = 12
    invalid_stream_count: StrictInt = Field(default=0, ge=0, le=12)
    mean_final_net_cost: StrictFloat
    median_final_net_cost: StrictFloat
    total_sheet_openings: StrictInt = Field(ge=0)
    replay_result_ids: tuple[StrictStr, ...] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def require_complete_result_ids(self) -> Self:
        if len(set(self.replay_result_ids)) != self.stream_count:
            raise ValueError("M7 calibration policy replay results must be unique")
        return self


def select_calibration_instances(index: M7ProblemIndex) -> tuple:
    """Select all and only the 12 frozen calibration streams."""

    instances = tuple(
        item for item in index.instances if item.partition is TemporalPartition.CALIBRATION
    )
    if (
        len(instances) != 288
        or len({item.stream_id for item in instances}) != 12
        or len({item.problem_id for item in instances}) != 90
        or any(
            sum(candidate.stream_id == stream_id for candidate in instances) != 24
            for stream_id in {item.stream_id for item in instances}
        )
    ):
        raise ValueError("M7 calibration population differs from frozen census")
    return instances


def select_calibration_winner(
    scores: tuple[M7CalibrationPolicyScore, ...],
) -> M7CalibrationPolicyScore:
    """Apply the preregistered score and deterministic tie-breakers."""

    expected = tuple(registered_policy_identities())
    by_name = {item.policy.name: item for item in scores}
    if len(scores) != len(expected) or set(by_name) != {item.name for item in expected}:
        raise ValueError("M7 calibration scores do not cover the five registered policies")
    if any(item.invalid_stream_count for item in scores):
        raise ValueError("M7 calibration fails closed when any policy stream is invalid")
    return min(
        scores,
        key=lambda item: (
            item.mean_final_net_cost,
            item.invalid_stream_count,
            item.median_final_net_cost,
            item.total_sheet_openings,
            item.policy.name.value,
        ),
    )


class M7CalibrationResult(BaselineContractModel):
    """All five policies on all 12 calibration streams, with no evaluation outcomes."""

    schema_version: Literal["yieldforge.m7-calibration-result.v1"] = (
        "yieldforge.m7-calibration-result.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm7cal-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    problem_index_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feasibility_result_id: StrictStr = Field(pattern=r"^yfm7f-[0-9a-f]{24}$")
    feasibility_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    collision_differential_result_id: StrictStr = Field(pattern=r"^yfm7d-[0-9a-f]{24}$")
    collision_differential_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate_problem_count: Literal[90] = 90
    candidate_archive_count: Literal[360] = 360
    candidate_verification_seconds: StrictFloat = Field(ge=0)
    policy_count: Literal[5] = 5
    stream_count_per_policy: Literal[12] = 12
    instance_count_per_policy: Literal[288] = 288
    streams: tuple[M7CalibrationStreamResult, ...] = Field(min_length=60, max_length=60)
    policy_scores: tuple[M7CalibrationPolicyScore, ...] = Field(min_length=5, max_length=5)
    winning_policy: M7PolicyIdentity
    total_replay_seconds: StrictFloat = Field(gt=0)
    total_fit_search_seconds: StrictFloat = Field(ge=0)
    total_fit_search_cache_hit_count: StrictInt = Field(ge=0)
    total_fit_search_cache_miss_count: StrictInt = Field(ge=0)
    selection_rule: Literal[
        "lowest_mean_net_cost_then_invalid_median_sheet_openings_lexical_policy"
    ] = "lowest_mean_net_cost_then_invalid_median_sheet_openings_lexical_policy"
    evaluation_partition_opened: Literal[False] = False
    technical_decision: Literal["baseline_policy_frozen"] = "baseline_policy_frozen"
    claim_ceiling: Literal[
        "calibration_only_policy_selection_not_evaluation_advantage_savings_physical_or_"
        "commercial_evidence"
    ] = (
        "calibration_only_policy_selection_not_evaluation_advantage_savings_physical_or_"
        "commercial_evidence"
    )

    @model_validator(mode="after")
    def require_complete_calibration(self) -> Self:
        expected_policies = tuple(item.name for item in registered_policy_identities())
        if tuple(dict.fromkeys(item.policy for item in self.streams)) != expected_policies:
            raise ValueError("M7 calibration stream policy order differs")
        for policy in expected_policies:
            policy_streams = tuple(item for item in self.streams if item.policy is policy)
            if (
                len(policy_streams) != self.stream_count_per_policy
                or len({item.stream_id for item in policy_streams}) != self.stream_count_per_policy
                or sum(item.instance_count for item in policy_streams)
                != self.instance_count_per_policy
            ):
                raise ValueError("M7 calibration policy does not cover all 12 streams")
        score_by_name = {item.policy.name: item for item in self.policy_scores}
        if tuple(score_by_name) != expected_policies:
            raise ValueError("M7 calibration score order differs")
        for policy in expected_policies:
            policy_streams = tuple(item for item in self.streams if item.policy is policy)
            costs = tuple(item.final_net_cost for item in policy_streams)
            expected_score = (
                round(fmean(costs), 6),
                round(median(costs), 6),
                sum(item.full_sheet_opening_count for item in policy_streams),
                tuple(item.replay_result_id for item in policy_streams),
            )
            observed = score_by_name[policy]
            if (
                observed.mean_final_net_cost,
                observed.median_final_net_cost,
                observed.total_sheet_openings,
                observed.replay_result_ids,
            ) != expected_score:
                raise ValueError("M7 calibration selector score does not reconcile")
        if self.winning_policy != select_calibration_winner(self.policy_scores).policy:
            raise ValueError("M7 calibration winner differs from frozen selector")
        expected_totals = (
            round(sum(item.replay_elapsed_seconds for item in self.streams), 6),
            round(sum(item.fit_search_seconds for item in self.streams), 6),
            sum(item.fit_search_cache_hit_count for item in self.streams),
            sum(item.fit_search_cache_miss_count for item in self.streams),
        )
        if (
            self.total_replay_seconds,
            self.total_fit_search_seconds,
            self.total_fit_search_cache_hit_count,
            self.total_fit_search_cache_miss_count,
        ) != expected_totals:
            raise ValueError("M7 calibration runtime totals do not reconcile")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}" or self.result_id != (
            f"yfm7cal-{digest[:24]}"
        ):
            raise ValueError("M7 calibration identity does not match content")
        return self


def finalize_calibration_result(
    *,
    m0_contract_id: str,
    m0_contract_sha256: str,
    problem_index_id: str,
    problem_index_sha256: str,
    feasibility_result_id: str,
    feasibility_result_sha256: str,
    collision_differential_result_id: str,
    collision_differential_result_sha256: str,
    candidate_verification_seconds: float,
    streams: tuple[M7CalibrationStreamResult, ...],
) -> M7CalibrationResult:
    """Reconcile calibration executions and select one deterministic winner."""

    policy_order = tuple(item.name for item in registered_policy_identities())
    ordered = tuple(
        sorted(
            streams,
            key=lambda item: (
                policy_order.index(item.policy),
                item.temporal_seed,
                tuple(TemporalRegime).index(item.regime),
            ),
        )
    )
    scores = tuple(
        M7CalibrationPolicyScore(
            policy=policy_identity(policy),
            mean_final_net_cost=round(
                fmean(item.final_net_cost for item in ordered if item.policy is policy), 6
            ),
            median_final_net_cost=round(
                median(item.final_net_cost for item in ordered if item.policy is policy), 6
            ),
            total_sheet_openings=sum(
                item.full_sheet_opening_count for item in ordered if item.policy is policy
            ),
            replay_result_ids=tuple(
                item.replay_result_id for item in ordered if item.policy is policy
            ),
        )
        for policy in policy_order
    )
    winner = select_calibration_winner(scores).policy
    semantic = {
        "schema_version": "yieldforge.m7-calibration-result.v1",
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "problem_index_id": problem_index_id,
        "problem_index_sha256": problem_index_sha256,
        "feasibility_result_id": feasibility_result_id,
        "feasibility_result_sha256": feasibility_result_sha256,
        "collision_differential_result_id": collision_differential_result_id,
        "collision_differential_result_sha256": collision_differential_result_sha256,
        "candidate_problem_count": 90,
        "candidate_archive_count": 360,
        "candidate_verification_seconds": round(candidate_verification_seconds, 6),
        "policy_count": 5,
        "stream_count_per_policy": 12,
        "instance_count_per_policy": 288,
        "streams": [item.model_dump(mode="json") for item in ordered],
        "policy_scores": [item.model_dump(mode="json") for item in scores],
        "winning_policy": winner.model_dump(mode="json"),
        "total_replay_seconds": round(sum(item.replay_elapsed_seconds for item in ordered), 6),
        "total_fit_search_seconds": round(sum(item.fit_search_seconds for item in ordered), 6),
        "total_fit_search_cache_hit_count": sum(
            item.fit_search_cache_hit_count for item in ordered
        ),
        "total_fit_search_cache_miss_count": sum(
            item.fit_search_cache_miss_count for item in ordered
        ),
        "selection_rule": (
            "lowest_mean_net_cost_then_invalid_median_sheet_openings_lexical_policy"
        ),
        "evaluation_partition_opened": False,
        "technical_decision": "baseline_policy_frozen",
        "claim_ceiling": (
            "calibration_only_policy_selection_not_evaluation_advantage_savings_physical_or_"
            "commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M7CalibrationResult(
        result_id=f"yfm7cal-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m0_contract_id=m0_contract_id,
        m0_contract_sha256=m0_contract_sha256,
        problem_index_id=problem_index_id,
        problem_index_sha256=problem_index_sha256,
        feasibility_result_id=feasibility_result_id,
        feasibility_result_sha256=feasibility_result_sha256,
        collision_differential_result_id=collision_differential_result_id,
        collision_differential_result_sha256=collision_differential_result_sha256,
        candidate_verification_seconds=semantic["candidate_verification_seconds"],
        streams=ordered,
        policy_scores=scores,
        winning_policy=winner,
        total_replay_seconds=semantic["total_replay_seconds"],
        total_fit_search_seconds=semantic["total_fit_search_seconds"],
        total_fit_search_cache_hit_count=semantic["total_fit_search_cache_hit_count"],
        total_fit_search_cache_miss_count=semantic["total_fit_search_cache_miss_count"],
    )


class M7BaselineRuntimeIdentity(BaselineContractModel):
    replay_engine: Literal["yieldforge.m7-baseline-replay@1.0.0"] = (
        "yieldforge.m7-baseline-replay@1.0.0"
    )
    python_implementation: StrictStr
    python_version: StrictStr
    shapely_version: StrictStr
    collision_backend: Literal["jagua_rs_0_7_0_guarded_prefilter_shapely_witness"] = (
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
    )
    jagua_backend: Literal["jagua-rs"] = "jagua-rs"
    jagua_version: Literal["0.7.0"] = "0.7.0"
    jagua_coordinate_precision: Literal["f32"] = "f32"
    jagua_executable_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class M7FrozenBaseline(BaselineContractModel):
    """Evaluation-safe immutable baseline selected only from calibration outcomes."""

    schema_version: Literal["yieldforge.m7-frozen-baseline.v1"] = "yieldforge.m7-frozen-baseline.v1"
    freeze_id: StrictStr = Field(pattern=r"^yfm7freeze-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calibration_result_id: StrictStr = Field(pattern=r"^yfm7cal-[0-9a-f]{24}$")
    calibration_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    problem_index_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    winning_policy: M7PolicyIdentity
    candidate_set_ids: tuple[StrictStr, ...] = Field(min_length=90, max_length=90)
    candidate_set_sha256s: tuple[StrictStr, ...] = Field(min_length=90, max_length=90)
    runtime: M7BaselineRuntimeIdentity
    evaluation_partition_opened: Literal[False] = False
    claim_ceiling: Literal[
        "frozen_calibration_selected_baseline_only_not_evaluation_advantage_savings_physical_or_"
        "commercial_evidence"
    ] = (
        "frozen_calibration_selected_baseline_only_not_evaluation_advantage_savings_physical_or_"
        "commercial_evidence"
    )

    @model_validator(mode="after")
    def require_frozen_identity(self) -> Self:
        if len(set(self.candidate_set_ids)) != 90 or len(self.candidate_set_ids) != len(
            self.candidate_set_sha256s
        ):
            raise ValueError("M7 frozen baseline candidate identities differ")
        digest = semantic_sha256(self, excluded_fields={"freeze_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}" or self.freeze_id != (
            f"yfm7freeze-{digest[:24]}"
        ):
            raise ValueError("M7 frozen baseline identity does not match content")
        return self


def freeze_calibrated_baseline(
    calibration: M7CalibrationResult,
    candidate_sets: tuple[M7CandidateSetEvidence, ...],
    jagua_executable: Path,
) -> M7FrozenBaseline:
    """Bind the selected policy, candidate population, and exact runtime before evaluation."""

    ordered = tuple(sorted(candidate_sets, key=lambda item: item.problem_id))
    if len(ordered) != 90 or len({item.problem_id for item in ordered}) != 90:
        raise ValueError("M7 freeze requires all 90 calibration candidate sets")
    executable = Path(jagua_executable)
    metadata = executable.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M7 Jagua runtime must be a regular file")
    runtime = M7BaselineRuntimeIdentity(
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        shapely_version=shapely.__version__,
        jagua_executable_sha256=f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}",
    )
    semantic = {
        "schema_version": "yieldforge.m7-frozen-baseline.v1",
        "calibration_result_id": calibration.result_id,
        "calibration_result_sha256": calibration.content_sha256,
        "m0_contract_id": calibration.m0_contract_id,
        "m0_contract_sha256": calibration.m0_contract_sha256,
        "problem_index_id": calibration.problem_index_id,
        "problem_index_sha256": calibration.problem_index_sha256,
        "winning_policy": calibration.winning_policy.model_dump(mode="json"),
        "candidate_set_ids": [item.candidate_set_id for item in ordered],
        "candidate_set_sha256s": [item.content_sha256 for item in ordered],
        "runtime": runtime.model_dump(mode="json"),
        "evaluation_partition_opened": False,
        "claim_ceiling": (
            "frozen_calibration_selected_baseline_only_not_evaluation_advantage_savings_"
            "physical_or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M7FrozenBaseline(
        freeze_id=f"yfm7freeze-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        calibration_result_id=calibration.result_id,
        calibration_result_sha256=calibration.content_sha256,
        m0_contract_id=calibration.m0_contract_id,
        m0_contract_sha256=calibration.m0_contract_sha256,
        problem_index_id=calibration.problem_index_id,
        problem_index_sha256=calibration.problem_index_sha256,
        winning_policy=calibration.winning_policy,
        candidate_set_ids=tuple(item.candidate_set_id for item in ordered),
        candidate_set_sha256s=tuple(item.content_sha256 for item in ordered),
        runtime=runtime,
    )


def execute_calibration(
    *,
    index: M7ProblemIndex,
    m0: M0ExperimentContract,
    archive_roots: Path | tuple[Path, ...],
    jagua_executable: Path,
    collision_differential: M7CollisionDifferentialResult,
    feasibility: M7FeasibilityResult,
    progress: Callable[[str], None] | None = None,
) -> tuple[M7CalibrationResult, M7FrozenBaseline]:
    """Execute five policies on 12 calibration streams and freeze the winner."""

    contract = build_registered_contract()
    if (
        index.m6_contract_id != contract.contract_id
        or index.m6_contract_sha256 != contract.content_sha256
        or (m0.contract_id, m0.content_sha256)
        != (contract.m0_contract_id, contract.m0_contract_sha256)
        or feasibility.problem_index_id != index.index_id
        or feasibility.m0_contract_id != m0.contract_id
        or feasibility.technical_decision != "ready_for_calibration_with_validated_jagua_prefilter"
        or collision_differential.problem_index_id != index.index_id
        or collision_differential.m0_contract_id != m0.contract_id
        or collision_differential.technical_decision != "validated_for_guarded_prefilter"
        or feasibility.collision_differential_result_id != collision_differential.result_id
    ):
        raise ValueError("M7 calibration inputs do not share the validated frozen boundary")

    instances = select_calibration_instances(index)
    selected_problem_ids = {item.problem_id for item in instances}
    problem_by_id = {item.problem_id: item for item in index.problems}
    references_by_task = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)

    verification_started = perf_counter()
    verified = {}
    for offset, problem_id in enumerate(sorted(selected_problem_ids), start=1):
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),
            archive_roots,
        )
        if progress is not None:
            progress(f"verified calibration candidate problem {offset}/90")
    verification_seconds = perf_counter() - verification_started

    stream_ids = tuple(dict.fromkeys(item.stream_id for item in instances))
    standard_profile_cache = {}
    shared_fit_cache: M7SharedFitSearchCache = {}
    prepared_layout_cache: M7PreparedLayoutCache = OrderedDict()
    stream_results = []
    rules = rule_set_from_m0(m0.remnant_eligibility)
    total_replays = len(tuple(M7PolicyName)) * len(stream_ids)
    completed_replays = 0
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as profile_executor:
        for policy in M7PolicyName:
            for stream_id in stream_ids:
                stream_instances = tuple(item for item in instances if item.stream_id == stream_id)
                stream_problem_ids = tuple(sorted({item.problem_id for item in stream_instances}))
                stream_problems = tuple(problem_by_id[item] for item in stream_problem_ids)
                replay_input = build_m7_replay_input(
                    m0_contract_id=m0.contract_id,
                    m0_contract_sha256=m0.content_sha256,
                    problem_index_id=index.index_id,
                    problem_index_sha256=index.content_sha256,
                    m6_contract_id=index.m6_contract_id,
                    m6_contract_sha256=index.m6_contract_sha256,
                    m6_population_id=index.m6_population_id,
                    m6_population_sha256=index.m6_population_sha256,
                    policy=policy_identity(policy),
                    rates=contract.rates,
                    fit_config=RemnantFitConfig(),
                    problems=stream_problems,
                    candidate_sets=tuple(verified[item].evidence for item in stream_problem_ids),
                    instances=stream_instances,
                    horizon_end=stream_instances[-1].released_at
                    + timedelta(minutes=contract.timing.interval_minutes),
                    collision_backend=("jagua_rs_0_7_0_guarded_prefilter_shapely_witness"),
                    jagua_container_guard=1.0,
                )
                runtime = M7ReplayRuntimeMetrics()
                replay_progress = None
                if progress is not None:
                    progress_name = policy.value
                    progress_seed = stream_instances[0].temporal_seed
                    progress_regime = stream_instances[0].regime.value

                    def replay_progress(
                        done: int,
                        total: int,
                        name: str = progress_name,
                        seed: int = progress_seed,
                        regime: str = progress_regime,
                    ) -> None:
                        progress(
                            f"calibrating {name} seed={seed} regime={regime} event={done}/{total}"
                        )

                replay = run_m7_replay(
                    replay_input,
                    {item: verified[item] for item in stream_problem_ids},
                    rules,
                    runtime_metrics=runtime,
                    standard_profile_cache=standard_profile_cache,
                    standard_profile_executor=profile_executor,
                    jagua_executable=jagua_executable,
                    shared_fit_search_cache=shared_fit_cache,
                    prepared_layout_cache=prepared_layout_cache,
                    progress=replay_progress,
                )
                stream_results.append(
                    M7CalibrationStreamResult(
                        policy=policy,
                        regime=stream_instances[0].regime,
                        temporal_seed=stream_instances[0].temporal_seed,
                        stream_id=replay_input.stream_id,
                        stream_sha256=replay_input.stream_sha256,
                        replay_input_id=replay_input.input_id,
                        replay_input_sha256=replay_input.content_sha256,
                        replay_result_id=replay.result_id,
                        replay_result_sha256=replay.content_sha256,
                        replay_elapsed_seconds=round(runtime.replay_elapsed_seconds, 6),
                        fit_search_seconds=round(runtime.fit_search_seconds, 6),
                        fit_search_cache_hit_count=runtime.fit_search_cache_hit_count,
                        fit_search_cache_miss_count=runtime.fit_search_cache_miss_count,
                        action_count=replay.summary.total_action_count,
                        full_sheet_opening_count=replay.summary.full_sheet_opening_count,
                        remnant_retrieval_count=replay.summary.remnant_retrieval_count,
                        final_net_cost=replay.summary.final_net_cost,
                    )
                )
                completed_replays += 1
                if progress is not None:
                    progress(f"completed calibration replay {completed_replays}/{total_replays}")

    calibration = finalize_calibration_result(
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        problem_index_id=index.index_id,
        problem_index_sha256=index.content_sha256,
        feasibility_result_id=feasibility.result_id,
        feasibility_result_sha256=feasibility.content_sha256,
        collision_differential_result_id=collision_differential.result_id,
        collision_differential_result_sha256=collision_differential.content_sha256,
        candidate_verification_seconds=verification_seconds,
        streams=tuple(stream_results),
    )
    frozen = freeze_calibrated_baseline(
        calibration,
        tuple(item.evidence for item in verified.values()),
        jagua_executable,
    )
    return calibration, frozen


def _canonical_bytes(result: BaseModel) -> bytes:
    return (
        json.dumps(result.model_dump(mode="json"), allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _read_regular(path: Path) -> bytes:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M7 feasibility artifact must be a regular file")
    if metadata.st_size > _MAX_FEASIBILITY_ARTIFACT_BYTES:
        raise ValueError("M7 feasibility artifact exceeds byte limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read(_MAX_FEASIBILITY_ARTIFACT_BYTES + 1)


def publish_feasibility_result(
    output_directory: Path,
    result: M7FeasibilityResult,
) -> Path:
    """Publish or byte-verify one immutable feasibility artifact."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m7-feasibility-{result.result_id}.json"
    data = _canonical_bytes(result)
    if path.exists():
        if _read_regular(path) != data:
            raise ValueError("M7 feasibility artifact is immutable and differs")
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


def publish_collision_differential_result(
    output_directory: Path,
    result: M7CollisionDifferentialResult,
) -> Path:
    """Publish or byte-verify one immutable Jagua differential artifact."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m7-collision-differential-{result.result_id}.json"
    data = _canonical_bytes(result)
    if path.exists():
        if _read_regular(path) != data:
            raise ValueError("M7 collision differential artifact is immutable and differs")
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


def publish_calibration_result(
    output_directory: Path,
    result: M7CalibrationResult,
) -> Path:
    """Publish or byte-verify one immutable calibration artifact."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m7-calibration-{result.result_id}.json"
    data = _canonical_bytes(result)
    if path.exists():
        if _read_regular(path) != data:
            raise ValueError("M7 calibration artifact is immutable and differs")
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


def publish_frozen_baseline(
    output_directory: Path,
    frozen: M7FrozenBaseline,
) -> Path:
    """Publish or byte-verify the single evaluation-safe baseline freeze."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "m7-frozen-baseline-v1.json"
    data = _canonical_bytes(frozen)
    if path.exists():
        if _read_regular(path) != data:
            raise ValueError("M7 frozen baseline is immutable and differs")
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


def publish_problem_index(output_directory: Path, index: M7ProblemIndex) -> Path:
    """Publish or byte-verify the deterministic M7 problem index."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m7-problem-index-{index.index_id}.json"
    data = _canonical_bytes(index)
    if path.exists():
        if _read_regular(path) != data:
            raise ValueError("M7 problem index artifact is immutable and differs")
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
    "M7CollisionBackendDecision",
    "M7CollisionDifferentialResult",
    "M7CollisionGateResult",
    "M7FeasibilityResult",
    "M7FeasibilityStreamResult",
    "evaluate_collision_gate",
    "execute_collision_differential_probe",
    "execute_feasibility_slice",
    "finalize_collision_differential_result",
    "finalize_feasibility_result",
    "publish_feasibility_result",
    "publish_collision_differential_result",
    "publish_problem_index",
    "select_feasibility_instances",
]
