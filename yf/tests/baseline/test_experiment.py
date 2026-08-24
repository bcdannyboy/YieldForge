from __future__ import annotations

from pathlib import Path

import pytest

from yieldforge.baseline.experiment import (
    M7CollisionBackendDecision,
    M7CollisionDifferentialResult,
    M7FeasibilityStreamResult,
    evaluate_collision_gate,
    finalize_collision_differential_result,
    finalize_feasibility_result,
    publish_feasibility_result,
    select_feasibility_instances,
)
from yieldforge.baseline.policies import M7PolicyName, policy_identity
from yieldforge.baseline.problems import build_registered_problem_index
from yieldforge.temporal_benchmark.contracts import TemporalRegime


@pytest.fixture(scope="module")
def index():  # type: ignore[no-untyped-def]
    return build_registered_problem_index()


def test_feasibility_slice_is_first_calibration_seed_in_every_regime(index) -> None:  # type: ignore[no-untyped-def]
    instances = select_feasibility_instances(index)

    assert len(instances) == 144
    assert len({item.stream_id for item in instances}) == 6
    assert len({item.problem_id for item in instances}) == 51
    assert {item.temporal_seed for item in instances} == {2026082300}
    assert tuple(dict.fromkeys(item.regime for item in instances)) == tuple(TemporalRegime)
    assert all(
        tuple(item.sequence for item in instances if item.stream_id == stream_id)
        == tuple(range(24))
        for stream_id in {item.stream_id for item in instances}
    )


def test_collision_gate_triggers_on_search_share_or_projected_runtime() -> None:
    search_heavy = evaluate_collision_gate(
        total_replay_seconds=60.0,
        fit_search_seconds=18.0,
    )
    runtime_heavy = evaluate_collision_gate(
        total_replay_seconds=100.0,
        fit_search_seconds=10.0,
    )
    deferred = evaluate_collision_gate(
        total_replay_seconds=30.0,
        fit_search_seconds=3.0,
    )

    assert search_heavy.decision is M7CollisionBackendDecision.BUILD_JAGUA_DIFFERENTIAL_SPIKE
    assert search_heavy.trigger_reasons == ("fit_search_share_at_least_0.30",)
    assert runtime_heavy.decision is M7CollisionBackendDecision.BUILD_JAGUA_DIFFERENTIAL_SPIKE
    assert runtime_heavy.trigger_reasons == ("projected_calibration_minutes_above_15",)
    assert deferred.decision is M7CollisionBackendDecision.DEFER_JAGUA
    assert deferred.trigger_reasons == ()


def _stream(regime: TemporalRegime, offset: int) -> M7FeasibilityStreamResult:
    return M7FeasibilityStreamResult(
        regime=regime,
        stream_id=f"yfts-{offset:024x}",
        stream_sha256="sha256:" + f"{offset:x}" * 64,
        instance_count=24,
        problem_count=1,
        candidate_set_count=1,
        raw_candidate_count=4,
        distinct_candidate_count=1,
        replay_input_id=f"yfm7ri-{offset:024x}",
        replay_input_sha256="sha256:" + f"{offset + 1:x}" * 64,
        replay_result_id=f"yfm7r-{offset:024x}",
        replay_result_sha256="sha256:" + f"{offset + 2:x}" * 64,
        replay_elapsed_seconds=5.0,
        standard_action_seconds=3.0,
        fit_search_seconds=0.5,
        remnant_action_materialization_seconds=0.5,
        action_count=100,
        fit_search_query_count=20,
        fit_search_evaluated_candidate_count=40,
        full_sheet_opening_count=12,
        remnant_retrieval_count=12,
        terminal_remnant_count=2,
        final_net_cost=10.0,
        technical_decision="pass",
    )


def test_feasibility_result_reconciles_complete_metrics_and_publishes_idempotently(
    tmp_path: Path,
) -> None:
    streams = tuple(_stream(regime, offset + 1) for offset, regime in enumerate(TemporalRegime))
    result = finalize_feasibility_result(
        m0_contract_id="yfm0-" + "a" * 24,
        m0_contract_sha256="sha256:" + "b" * 64,
        problem_index_id="yfm7i-" + "c" * 24,
        problem_index_sha256="sha256:" + "d" * 64,
        policy=policy_identity(M7PolicyName.AGE_REGULARITY),
        candidate_problem_count=51,
        candidate_archive_count=204,
        raw_candidate_count=500,
        distinct_candidate_count=400,
        streams=streams,
    )

    first = publish_feasibility_result(tmp_path, result)
    first_bytes = first.read_bytes()
    second = publish_feasibility_result(tmp_path, result)

    assert first == second
    assert second.read_bytes() == first_bytes
    assert result.instance_count == 144
    assert result.stream_count == 6
    assert result.total_action_count == 600
    assert result.total_fit_search_query_count == 120
    assert result.total_replay_seconds == 30.0
    assert result.collision_gate.decision is M7CollisionBackendDecision.DEFER_JAGUA
    assert result.technical_decision == "ready_for_calibration"
    assert result.result_id == f"yfm7f-{result.content_sha256[7:31]}"


def test_collision_differential_requires_zero_search_mismatches() -> None:
    validated = finalize_collision_differential_result(
        m0_contract_id="yfm0-" + "a" * 24,
        m0_contract_sha256="sha256:" + "b" * 64,
        problem_index_id="yfm7i-" + "c" * 24,
        problem_index_sha256="sha256:" + "d" * 64,
        stream_id="yfts-" + "e" * 24,
        stream_sha256="sha256:" + "f" * 64,
        binding_ids=("yfm7b-" + "1" * 24, "yfm7b-" + "2" * 24),
        problem_ids=("yfm7p-" + "3" * 24,),
        raw_candidate_count=800,
        distinct_candidate_count=709,
        replay_input_id="yfm7ri-" + "4" * 24,
        replay_input_sha256="sha256:" + "5" * 64,
        replay_result_id="yfm7r-" + "6" * 24,
        replay_result_sha256="sha256:" + "7" * 64,
        replay_elapsed_seconds=180.0,
        standard_action_seconds=10.0,
        translation_generation_seconds=20.1234567,
        accelerated_evaluation_seconds=1.0123456,
        authoritative_audit_seconds=160.7654321,
        jagua_wall_seconds=0.5,
        fit_search_query_count=709,
        fit_search_evaluated_candidate_count=174_626,
        jagua_guarded_query_count=174_626,
        jagua_rejection_count=170_000,
        jagua_audit_search_count=709,
        jagua_audit_mismatch_count=0,
    )

    assert isinstance(validated, M7CollisionDifferentialResult)
    assert validated.measured_search_speedup == round(
        (validated.translation_generation_seconds + validated.authoritative_audit_seconds)
        / validated.accelerated_evaluation_seconds,
        6,
    )
    assert validated.measured_backend_speedup == round(
        validated.authoritative_audit_seconds / validated.accelerated_evaluation_seconds,
        6,
    )
    assert validated.technical_decision == "validated_for_guarded_prefilter"
    assert validated.result_id == f"yfm7d-{validated.content_sha256[7:31]}"


def test_feasibility_can_bind_validated_jagua_backend() -> None:
    streams = tuple(
        _stream(regime, offset + 1).model_copy(
            update={
                "jagua_guarded_query_count": 100,
                "jagua_rejection_count": 90,
                "jagua_wall_seconds": 0.25,
            }
        )
        for offset, regime in enumerate(TemporalRegime)
    )
    result = finalize_feasibility_result(
        m0_contract_id="yfm0-" + "a" * 24,
        m0_contract_sha256="sha256:" + "b" * 64,
        problem_index_id="yfm7i-" + "c" * 24,
        problem_index_sha256="sha256:" + "d" * 64,
        policy=policy_identity(M7PolicyName.AGE_REGULARITY),
        candidate_problem_count=51,
        candidate_archive_count=204,
        raw_candidate_count=500,
        distinct_candidate_count=400,
        streams=streams,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        collision_differential_result_id="yfm7d-" + "e" * 24,
        collision_differential_result_sha256="sha256:" + "f" * 64,
    )

    assert (
        result.collision_gate.decision is M7CollisionBackendDecision.USE_VALIDATED_JAGUA_PREFILTER
    )
    assert result.total_jagua_guarded_query_count == 600
    assert result.total_jagua_rejection_count == 540
    assert result.technical_decision == "ready_for_calibration_with_validated_jagua_prefilter"
