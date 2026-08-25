"""Calibration-only runtime proof and hard gate for sparse exact M8."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Literal, Self

from pydantic import Field, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.archives import (
    canonical_m2_archive_references,
    verify_problem_candidates,
)
from yieldforge.baseline.contracts import BaselineContractModel, M7ProblemIndex
from yieldforge.baseline.experiment import M7FrozenBaseline, select_calibration_instances
from yieldforge.baseline.replay import (
    M7ReplayRuntime,
    build_m7_replay_input,
    initial_m7_cursor,
)
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.oracle.reference import M8OracleRequest, score_reference_event
from yieldforge.oracle.sparse import score_sparse_event
from yieldforge.oracle.visibility import FullRealizedVisibility
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import RemnantFitConfig
from yieldforge.temporal_benchmark.contracts import (
    TemporalRegime,
    build_registered_contract,
)

_PREFIX_EVENT_COUNT = 2
_HELD_OUT_ACTION_COUNT = 550_542
_PROOF_FUTURE_EVENT_COUNT = 1.0
_HELD_OUT_MEAN_FUTURE_EVENT_COUNT = 11.5
_PROJECTION_SAFETY_FACTOR = 2.0
_WORKER_COUNT = 8


class M8SparseProofCell(BaselineContractModel):
    """Reference-versus-sparse result for one registered calibration prefix."""

    regime: TemporalRegime
    temporal_seed: StrictInt
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    prefix_event_count: Literal[2] = 2
    current_action_count: StrictInt = Field(ge=1)
    reference_continuation_event_count: StrictInt = Field(ge=0)
    sparse_common_continuation_event_count: StrictInt = Field(ge=0)
    sparse_exact_branch_event_count: StrictInt = Field(ge=0)
    sparse_skipped_passive_event_count: StrictInt = Field(ge=0)
    rejection_certificate_count: StrictInt = Field(ge=0)
    survivor_pair_count: StrictInt = Field(ge=0)
    state_rejoin_count: StrictInt = Field(ge=0)
    reference_elapsed_seconds: StrictFloat = Field(gt=0)
    sparse_elapsed_seconds: StrictFloat = Field(gt=0)
    semantic_mismatch_count: StrictInt = Field(ge=0)


class M8SparseProofResult(BaselineContractModel):
    """The first M8 go/no-go artifact; calibration evidence only."""

    schema_version: Literal["yieldforge.m8-sparse-proof.v1"] = (
        "yieldforge.m8-sparse-proof.v1"
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
    proof_cell_count: Literal[6] = 6
    completed_cell_count: Literal[6] = 6
    prefix_event_count: Literal[2] = 2
    worker_count: Literal[8] = 8
    held_out_action_count: Literal[550542] = 550_542
    cells: tuple[M8SparseProofCell, ...] = Field(min_length=6, max_length=6)
    semantic_mismatch_count: StrictInt = Field(ge=0)
    reference_elapsed_seconds: StrictFloat = Field(gt=0)
    sparse_elapsed_seconds: StrictFloat = Field(gt=0)
    end_to_end_speedup: StrictFloat = Field(gt=0)
    projected_held_out_calendar_days: StrictFloat = Field(ge=0)
    evaluation_partition_opened: Literal[False] = False
    technical_decision: Literal[
        "pass_sparse_exact",
        "redesign_sparse_exact",
        "require_distributed_exact",
    ]
    claim_ceiling: Literal[
        "calibration_prefix_runtime_and_semantic_proof_only_not_evaluation_advantage_savings_"
        "physical_or_commercial_evidence"
    ] = (
        "calibration_prefix_runtime_and_semantic_proof_only_not_evaluation_advantage_savings_"
        "physical_or_commercial_evidence"
    )

    @model_validator(mode="after")
    def require_complete_gate_and_identity(self) -> Self:
        if tuple(item.regime for item in self.cells) != tuple(TemporalRegime):
            raise ValueError("M8 sparse proof cells differ from the six registered regimes")
        expected = (
            sum(item.semantic_mismatch_count for item in self.cells),
            round(sum(item.reference_elapsed_seconds for item in self.cells), 6),
            round(sum(item.sparse_elapsed_seconds for item in self.cells), 6),
        )
        observed = (
            self.semantic_mismatch_count,
            self.reference_elapsed_seconds,
            self.sparse_elapsed_seconds,
        )
        if observed != expected:
            raise ValueError("M8 sparse proof aggregate metrics do not reconcile")
        expected_decision = _gate_decision(
            mismatch_count=self.semantic_mismatch_count,
            speedup=self.end_to_end_speedup,
            projected_days=self.projected_held_out_calendar_days,
        )
        if self.technical_decision != expected_decision:
            raise ValueError("M8 sparse proof decision differs from the hard gate")
        digest = semantic_sha256(self, excluded_fields={"proof_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M8 sparse proof SHA-256 does not match semantic content")
        if self.proof_id != f"yfm8proof-{digest[:24]}":
            raise ValueError("M8 sparse proof ID does not match semantic content")
        return self


def _gate_decision(*, mismatch_count: int, speedup: float, projected_days: float) -> str:
    if mismatch_count or speedup < 20.0:
        return "redesign_sparse_exact"
    if projected_days > 7.0:
        return "require_distributed_exact"
    return "pass_sparse_exact"


def finalize_sparse_proof(
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
    cells: tuple[M8SparseProofCell, ...],
) -> M8SparseProofResult:
    """Reconcile six cells and apply the immutable 20x/seven-day gate."""

    ordered = tuple(sorted(cells, key=lambda item: tuple(TemporalRegime).index(item.regime)))
    if len(ordered) != 6:
        raise ValueError("M8 sparse proof requires all six registered cells")
    reference_seconds = round(sum(item.reference_elapsed_seconds for item in ordered), 6)
    sparse_seconds = round(sum(item.sparse_elapsed_seconds for item in ordered), 6)
    mismatch_count = sum(item.semantic_mismatch_count for item in ordered)
    speedup = round(reference_seconds / sparse_seconds, 6)
    observed_actions = sum(item.current_action_count for item in ordered)
    projected_seconds = (
        sparse_seconds
        / observed_actions
        * _HELD_OUT_ACTION_COUNT
        * (_HELD_OUT_MEAN_FUTURE_EVENT_COUNT / _PROOF_FUTURE_EVENT_COUNT)
        * _PROJECTION_SAFETY_FACTOR
        / _WORKER_COUNT
    )
    projected_days = round(projected_seconds / 86_400.0, 6)
    decision = _gate_decision(
        mismatch_count=mismatch_count,
        speedup=speedup,
        projected_days=projected_days,
    )
    semantic = {
        "schema_version": "yieldforge.m8-sparse-proof.v1",
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
        "proof_cell_count": 6,
        "completed_cell_count": 6,
        "prefix_event_count": _PREFIX_EVENT_COUNT,
        "worker_count": _WORKER_COUNT,
        "held_out_action_count": _HELD_OUT_ACTION_COUNT,
        "cells": [item.model_dump(mode="json") for item in ordered],
        "semantic_mismatch_count": mismatch_count,
        "reference_elapsed_seconds": reference_seconds,
        "sparse_elapsed_seconds": sparse_seconds,
        "end_to_end_speedup": speedup,
        "projected_held_out_calendar_days": projected_days,
        "evaluation_partition_opened": False,
        "technical_decision": decision,
        "claim_ceiling": (
            "calibration_prefix_runtime_and_semantic_proof_only_not_evaluation_advantage_"
            "savings_physical_or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    validated = dict(semantic)
    validated["cells"] = ordered
    return M8SparseProofResult(
        proof_id=f"yfm8proof-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **validated,
    )


def _runtime(  # type: ignore[no-untyped-def]
    replay_input,
    verified,
    rules,
    profile_executor,
    jagua_executable,
) -> M7ReplayRuntime:
    return M7ReplayRuntime(
        replay_input=replay_input,
        runtime_candidates=verified,
        rules=rules,
        standard_profile_executor=profile_executor,
        jagua_executable=jagua_executable,
    )


def execute_sparse_prefix_proof(
    *,
    index: M7ProblemIndex,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    archive_roots: tuple[Path, ...],
    jagua_executable: Path,
    progress=None,  # type: ignore[no-untyped-def]
) -> M8SparseProofResult:
    """Run the six registered calibration prefixes without loading evaluation streams."""

    contract = build_registered_contract()
    if (
        (index.index_id, index.content_sha256)
        != (frozen.problem_index_id, frozen.problem_index_sha256)
        or (m0.contract_id, m0.content_sha256)
        != (frozen.m0_contract_id, frozen.m0_contract_sha256)
        or (index.m6_contract_id, index.m6_contract_sha256)
        != (contract.contract_id, contract.content_sha256)
    ):
        raise ValueError("M8 sparse proof inputs do not share the frozen M0/M6/M7 boundary")
    executable = Path(jagua_executable)
    metadata = executable.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M8 sparse proof Jagua runtime must be a regular file")
    executable_sha = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    if executable_sha != frozen.runtime.jagua_executable_sha256:
        raise ValueError("M8 sparse proof Jagua runtime differs from the M7 freeze")

    calibration = select_calibration_instances(index)
    selected_streams = []
    for regime in TemporalRegime:
        candidates = tuple(item for item in calibration if item.regime is regime)
        seed = min(item.temporal_seed for item in candidates)
        stream_id = next(item.stream_id for item in candidates if item.temporal_seed == seed)
        stream = tuple(item for item in candidates if item.stream_id == stream_id)
        if len(stream) != 24:
            raise ValueError("M8 sparse proof calibration stream does not contain 24 events")
        selected_streams.append(stream[:_PREFIX_EVENT_COUNT])

    problem_by_id = {item.problem_id: item for item in index.problems}
    selected_problem_ids = sorted(
        {item.problem_id for stream in selected_streams for item in stream}
    )
    references_by_task = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)
    verified = {}
    for offset, problem_id in enumerate(selected_problem_ids, start=1):
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),
            archive_roots,
        )
        if progress is not None:
            progress(f"verified proof candidate problem {offset}/{len(selected_problem_ids)}")

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
            raise ValueError("M8 sparse proof candidate evidence differs from the M7 freeze")

    rules = rule_set_from_m0(m0.remnant_eligibility)
    cells = []
    with ProcessPoolExecutor(max_workers=min(_WORKER_COUNT, os.cpu_count() or 1)) as executor:
        for stream in selected_streams:
            problem_ids = tuple(sorted({item.problem_id for item in stream}))
            replay_input = build_m7_replay_input(
                m0_contract_id=m0.contract_id,
                m0_contract_sha256=m0.content_sha256,
                problem_index_id=index.index_id,
                problem_index_sha256=index.content_sha256,
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
            stream_verified = {item: verified[item] for item in problem_ids}
            reference_runtime = _runtime(
                replay_input,
                stream_verified,
                rules,
                executor,
                executable,
            )
            reference_request = M8OracleRequest(
                runtime=reference_runtime,
                cursor=initial_m7_cursor(replay_input),
                visibility=FullRealizedVisibility(replay_input.instances),
            )
            started = perf_counter()
            reference = score_reference_event(reference_request)
            reference_seconds = max(0.000001, round(perf_counter() - started, 6))
            if progress is not None:
                progress(
                    f"reference {stream[0].regime.value} actions="
                    f"{reference.decision.scored_action_count} seconds={reference_seconds}"
                )

            sparse_runtime = _runtime(
                replay_input,
                stream_verified,
                rules,
                executor,
                executable,
            )
            sparse_request = M8OracleRequest(
                runtime=sparse_runtime,
                cursor=initial_m7_cursor(replay_input),
                visibility=FullRealizedVisibility(replay_input.instances),
            )
            started = perf_counter()
            sparse = score_sparse_event(sparse_request)
            sparse_seconds = max(0.000001, round(perf_counter() - started, 6))
            mismatch_count = int(sparse.decision != reference.decision)
            cells.append(
                M8SparseProofCell(
                    regime=stream[0].regime,
                    temporal_seed=stream[0].temporal_seed,
                    stream_id=stream[0].stream_id,
                    current_action_count=reference.decision.scored_action_count,
                    reference_continuation_event_count=(
                        reference.continuation_event_executions
                    ),
                    sparse_common_continuation_event_count=(
                        sparse.metrics.common_continuation_event_count
                    ),
                    sparse_exact_branch_event_count=(
                        sparse.metrics.exact_branch_event_count
                    ),
                    sparse_skipped_passive_event_count=(
                        sparse.metrics.skipped_passive_event_count
                    ),
                    rejection_certificate_count=(
                        sparse.metrics.rejection_certificate_count
                    ),
                    survivor_pair_count=sparse.metrics.survivor_pair_count,
                    state_rejoin_count=sparse.metrics.state_rejoin_count,
                    reference_elapsed_seconds=reference_seconds,
                    sparse_elapsed_seconds=sparse_seconds,
                    semantic_mismatch_count=mismatch_count,
                )
            )
            if progress is not None:
                progress(
                    f"sparse {stream[0].regime.value} seconds={sparse_seconds} "
                    f"mismatches={mismatch_count}"
                )
    return finalize_sparse_proof(
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        m6_contract_id=index.m6_contract_id,
        m6_contract_sha256=index.m6_contract_sha256,
        m6_population_id=index.m6_population_id,
        m6_population_sha256=index.m6_population_sha256,
        problem_index_id=index.index_id,
        problem_index_sha256=index.content_sha256,
        freeze_id=frozen.freeze_id,
        freeze_sha256=frozen.content_sha256,
        cells=tuple(cells),
    )


def publish_sparse_proof(output_directory: Path, result: M8SparseProofResult) -> Path:
    """Publish one immutable content-addressed M8 sparse proof."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m8-sparse-proof-{result.proof_id}.json"
    data = (
        json.dumps(result.model_dump(mode="json"), allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("M8 sparse proof artifact is immutable and differs")
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
    "M8SparseProofCell",
    "M8SparseProofResult",
    "execute_sparse_prefix_proof",
    "finalize_sparse_proof",
    "publish_sparse_proof",
]
