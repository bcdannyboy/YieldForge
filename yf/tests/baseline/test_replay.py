from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yieldforge.baseline.archives import VerifiedProblemCandidates
from yieldforge.baseline.contracts import (
    M7CandidateArchiveEvidence,
    M7CandidateSetEvidence,
    ReusableGeometryProblem,
    TemporalInstanceBinding,
)
from yieldforge.baseline.policies import M7PolicyName, policy_identity
from yieldforge.baseline.replay import (
    M7ReplayRuntimeMetrics,
    build_m7_replay_input,
    run_m7_replay,
)
from yieldforge.domain import (
    Candidate,
    CandidateReportType,
    Part,
    Placement,
    ProjectionMode,
    SolverProjectionBinding,
    StripPackingProblem,
)
from yieldforge.experiments.contracts import M0ExperimentContract, load_frozen_json, semantic_sha256
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import MaterialIdentity, MaterialProvenance, RemnantFitConfig
from yieldforge.temporal_benchmark.contracts import (
    CandidateArchiveRequirement,
    FeasibilityRateManifest,
    TemporalPartition,
    TemporalRegime,
)


def _material() -> MaterialIdentity:
    return MaterialIdentity(
        material_code="m7-replay-test",
        grade="grade",
        thickness="1",
        surface="plain",
        grain="none",
        provenance=MaterialProvenance.ASSUMED,
    )


def _problem(*, part_width: float = 4.0) -> ReusableGeometryProblem:
    projection = SolverProjectionBinding(
        mode=ProjectionMode.SOURCE_AS_RECORDED,
        projection_sha256="a" * 64,
        assumption_codes=(),
        source_flip_part_count=0,
    )
    geometry = StripPackingProblem(
        name="lectra-task-7",
        strip_height=10.0,
        sheet_length=10.0,
        parts=[
            Part(
                id="part-1",
                shape=[
                    (0.0, 0.0),
                    (part_width, 0.0),
                    (part_width, 10.0),
                    (0.0, 10.0),
                ],
                demand=1,
                allowed_orientations=[0.0],
            )
        ],
    )
    semantic = {
        "schema_version": "yieldforge.m7-reusable-geometry-problem.v1",
        "source_catalog_sha256": "b" * 64,
        "tasks_index": 7,
        "sheet_type": 1,
        "projection": projection.model_dump(mode="json"),
        "problem": geometry.model_dump(mode="json"),
        "candidate_requirement": CandidateArchiveRequirement().model_dump(mode="json"),
        "claim_ceiling": (
            "reusable_source_geometry_and_solver_requirement_only_not_temporal_material_or_"
            "policy_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return ReusableGeometryProblem(
        problem_id=f"yfm7p-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        source_catalog_sha256="b" * 64,
        tasks_index=7,
        sheet_type=1,
        projection=projection,
        problem=geometry,
        candidate_requirement=CandidateArchiveRequirement(),
    )


def _verified(problem: ReusableGeometryProblem) -> VerifiedProblemCandidates:
    part_width = max(point[0] for point in problem.problem.parts[0].shape)
    candidate = Candidate(
        candidate_id="candidate-one",
        report_type=CandidateReportType.FINAL,
        seed=0,
        width=part_width,
        density=part_width / 10.0,
        placements=[Placement(part_id="part-1", rotation=0.0, translation=(0.0, 0.0))],
    )
    archives = tuple(
        M7CandidateArchiveEvidence(
            seed=seed,  # type: ignore[arg-type]
            job_id=f"job-{seed}",
            batch_sha256=f"{seed}" * 64,
            candidate_count=1,
            source_result_id="yfgcr-" + "c" * 24,
            source_result_sha256="sha256:" + "d" * 64,
        )
        for seed in range(4)
    )
    semantic = {
        "schema_version": "yieldforge.m7-candidate-set.v1",
        "problem_id": problem.problem_id,
        "problem_sha256": problem.content_sha256,
        "archives": [item.model_dump(mode="json") for item in archives],
        "raw_candidate_count": 4,
        "distinct_candidate_count": 1,
        "candidate_ids": [candidate.candidate_id],
        "claim_ceiling": (
            "verified_shared_geometry_candidates_only_not_actions_policy_value_or_savings_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    evidence = M7CandidateSetEvidence(
        candidate_set_id=f"yfm7c-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        archives=archives,  # type: ignore[arg-type]
        raw_candidate_count=4,
        distinct_candidate_count=1,
        candidate_ids=(candidate.candidate_id,),
    )
    return VerifiedProblemCandidates(evidence=evidence, candidates=(candidate,))


def _binding(
    problem: ReusableGeometryProblem,
    *,
    sequence: int,
    released_at: datetime,
) -> TemporalInstanceBinding:
    semantic = {
        "schema_version": "yieldforge.m7-temporal-instance-binding.v1",
        "problem_id": problem.problem_id,
        "problem_sha256": problem.content_sha256,
        "stream_id": "yfts-" + "1" * 24,
        "stream_sha256": "sha256:" + "2" * 64,
        "event_id": f"yfte-{sequence:020x}",
        "m6_batch_id": "yftb-" + "3" * 20,
        "m6_batch_sequence": 0,
        "m6_subsequence": sequence,
        "sequence": sequence,
        "tasks_index": 7,
        "released_at": released_at.isoformat().replace("+00:00", "Z"),
        "material": _material().model_dump(mode="json"),
        "regime": TemporalRegime.COMPATIBLE_BUNDLE,
        "temporal_seed": 2026082300,
        "partition": TemporalPartition.CALIBRATION,
        "decomposition_rule": "source_event_boundary_before_policy",
        "chronology_provenance": "generated",
        "material_provenance": "assumed",
    }
    digest = semantic_sha256(semantic)
    return TemporalInstanceBinding(
        binding_id=f"yfm7b-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        stream_id="yfts-" + "1" * 24,
        stream_sha256="sha256:" + "2" * 64,
        event_id=f"yfte-{sequence:020x}",
        m6_batch_id="yftb-" + "3" * 20,
        m6_batch_sequence=0,
        m6_subsequence=sequence,
        sequence=sequence,
        tasks_index=7,
        released_at=released_at,
        material=_material(),
        regime=TemporalRegime.COMPATIBLE_BUNDLE,
        temporal_seed=2026082300,
        partition=TemporalPartition.CALIBRATION,
    )


def _m0() -> M0ExperimentContract:
    return load_frozen_json(
        Path(__file__).parents[2] / "experiments/m0-contract-v1.json",
        M0ExperimentContract,
    )


def test_replay_groups_equal_timestamps_preserves_inventory_and_reconciles_costs() -> None:
    problem = _problem()
    verified = _verified(problem)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    replay_input = build_m7_replay_input(
        m0_contract_id=_m0().contract_id,
        m0_contract_sha256=_m0().content_sha256,
        problem_index_id="yfm7i-" + "4" * 24,
        problem_index_sha256="sha256:" + "5" * 64,
        m6_contract_id="yfm6-" + "6" * 24,
        m6_contract_sha256="sha256:" + "7" * 64,
        m6_population_id="yftp-" + "8" * 24,
        m6_population_sha256="sha256:" + "9" * 64,
        policy=policy_identity(M7PolicyName.REMNANT_FIRST),
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.01,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        ),
        fit_config=RemnantFitConfig(),
        problems=(problem,),
        candidate_sets=(verified.evidence,),
        instances=(
            _binding(problem, sequence=0, released_at=started),
            _binding(problem, sequence=1, released_at=started),
        ),
        horizon_end=started + timedelta(hours=1),
    )

    first = run_m7_replay(
        replay_input,
        {problem.problem_id: verified},
        rule_set_from_m0(_m0().remnant_eligibility),
    )
    second = run_m7_replay(
        replay_input,
        {problem.problem_id: verified},
        rule_set_from_m0(_m0().remnant_eligibility),
    )

    assert first == second
    assert first.result_id == f"yfm7r-{first.content_sha256[7:31]}"
    assert len(first.events) == 2
    assert first.events[0].timestamp_group_sequence == 0
    assert first.events[1].timestamp_group_sequence == 0
    assert first.events[0].timestamp_subsequence == 0
    assert first.events[1].timestamp_subsequence == 1
    assert first.events[1].storage_interval_start == started
    assert first.events[1].storage_interval_end == started
    assert first.events[1].delta_costs.storage_cost == 0.0
    assert first.events[1].inventory_before == first.events[0].inventory_after
    assert first.events[1].action.kind.value == "consume_remnant"
    assert first.terminal.cumulative_costs.net_cost == first.summary.final_net_cost
    assert first.summary.full_sheet_opening_count == 1
    assert first.summary.remnant_retrieval_count == 1
    assert first.summary.fulfilled_instance_count == 2
    assert first.summary.technical_decision == "pass"


def test_replay_rejects_runtime_candidate_set_mismatch() -> None:
    problem = _problem()
    verified = _verified(problem)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    replay_input = build_m7_replay_input(
        m0_contract_id=_m0().contract_id,
        m0_contract_sha256=_m0().content_sha256,
        problem_index_id="yfm7i-" + "4" * 24,
        problem_index_sha256="sha256:" + "5" * 64,
        m6_contract_id="yfm6-" + "6" * 24,
        m6_contract_sha256="sha256:" + "7" * 64,
        m6_population_id="yftp-" + "8" * 24,
        m6_population_sha256="sha256:" + "9" * 64,
        policy=policy_identity(M7PolicyName.NET_COST),
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.01,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        ),
        fit_config=RemnantFitConfig(),
        problems=(problem,),
        candidate_sets=(verified.evidence,),
        instances=(_binding(problem, sequence=0, released_at=started),),
        horizon_end=started + timedelta(hours=1),
    )

    with pytest.raises(ValueError, match="runtime candidate"):
        run_m7_replay(replay_input, {}, rule_set_from_m0(_m0().remnant_eligibility))


def test_replay_can_use_guarded_jagua_prefilter(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "fake-jagua"
    executable.write_text(
        "#!/bin/sh\n"
        "python3 -c 'import json,sys; request=json.load(sys.stdin); "
        'print(json.dumps({"schema_version":"yieldforge.m7-jagua-search-response.v1",'
        '"backend":"jagua-rs","backend_version":"0.7.0","coordinate_precision":"f32",'
        '"build_microseconds":1,"generation_microseconds":2,"query_microseconds":3,'
        '"searches":[{"layout_id":request["layouts"][0]["layout_id"],'
        '"generated_candidate_count":1,"duplicate_candidate_count":0,'
        '"budget_truncated":False,"translations":[[4.0,0.0]],'
        '"collisions":[False]}]}))\'\n'
    )
    executable.chmod(0o700)
    problem = _problem()
    verified = _verified(problem)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    replay_input = build_m7_replay_input(
        m0_contract_id=_m0().contract_id,
        m0_contract_sha256=_m0().content_sha256,
        problem_index_id="yfm7i-" + "4" * 24,
        problem_index_sha256="sha256:" + "5" * 64,
        m6_contract_id="yfm6-" + "6" * 24,
        m6_contract_sha256="sha256:" + "7" * 64,
        m6_population_id="yftp-" + "8" * 24,
        m6_population_sha256="sha256:" + "9" * 64,
        policy=policy_identity(M7PolicyName.REMNANT_FIRST),
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.01,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        ),
        fit_config=RemnantFitConfig(),
        problems=(problem,),
        candidate_sets=(verified.evidence,),
        instances=(
            _binding(problem, sequence=0, released_at=started),
            _binding(problem, sequence=1, released_at=started),
        ),
        horizon_end=started + timedelta(hours=1),
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_container_guard=1.0,
    )
    metrics = M7ReplayRuntimeMetrics()

    accelerated = run_m7_replay(
        replay_input,
        {problem.problem_id: verified},
        rule_set_from_m0(_m0().remnant_eligibility),
        runtime_metrics=metrics,
        jagua_executable=executable,
    )

    assert accelerated.summary.fulfilled_instance_count == 2
    assert metrics.jagua_guarded_query_count > 0
    assert metrics.jagua_audit_search_count == 0
    assert metrics.jagua_audit_mismatch_count == 0


def test_replay_caches_immutable_no_fit_remnant_searches() -> None:
    problem = _problem(part_width=6.0)
    verified = _verified(problem)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    replay_input = build_m7_replay_input(
        m0_contract_id=_m0().contract_id,
        m0_contract_sha256=_m0().content_sha256,
        problem_index_id="yfm7i-" + "4" * 24,
        problem_index_sha256="sha256:" + "5" * 64,
        m6_contract_id="yfm6-" + "6" * 24,
        m6_contract_sha256="sha256:" + "7" * 64,
        m6_population_id="yftp-" + "8" * 24,
        m6_population_sha256="sha256:" + "9" * 64,
        policy=policy_identity(M7PolicyName.REMNANT_FIRST),
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.01,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        ),
        fit_config=RemnantFitConfig(),
        problems=(problem,),
        candidate_sets=(verified.evidence,),
        instances=tuple(
            _binding(problem, sequence=sequence, released_at=started + timedelta(hours=sequence))
            for sequence in range(3)
        ),
        horizon_end=started + timedelta(hours=3),
    )
    metrics = M7ReplayRuntimeMetrics()

    result = run_m7_replay(
        replay_input,
        {problem.problem_id: verified},
        rule_set_from_m0(_m0().remnant_eligibility),
        runtime_metrics=metrics,
    )

    assert result.summary.full_sheet_opening_count == 3
    assert result.summary.remnant_retrieval_count == 0
    assert metrics.fit_search_cache_miss_count == 2
    assert metrics.fit_search_cache_hit_count == 1
