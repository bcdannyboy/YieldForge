from __future__ import annotations

from pathlib import Path

import pytest

from yieldforge.baseline.experiment import (
    M7CalibrationPolicyScore,
    M7CalibrationStreamResult,
    M7CollisionBackendDecision,
    M7CollisionDifferentialResult,
    M7EvaluationStreamResult,
    M7FeasibilityStreamResult,
    M7FrozenBaseline,
    amend_frozen_baseline_runtime,
    evaluate_collision_gate,
    finalize_calibration_result,
    finalize_collision_differential_result,
    finalize_evaluation_result,
    finalize_feasibility_result,
    publish_calibration_result,
    publish_evaluation_result,
    publish_feasibility_result,
    publish_frozen_baseline,
    select_calibration_instances,
    select_calibration_winner,
    select_evaluation_instances,
    select_feasibility_instances,
)
from yieldforge.baseline.policies import (
    M7PolicyName,
    policy_identity,
    registered_policy_identities,
)
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


def test_calibration_selection_includes_all_12_streams_and_no_evaluation(index) -> None:  # type: ignore[no-untyped-def]
    instances = select_calibration_instances(index)

    assert len(instances) == 288
    assert len({item.stream_id for item in instances}) == 12
    assert len({item.problem_id for item in instances}) == 90
    assert {item.temporal_seed for item in instances} == {2026082300, 2026082301}
    assert all(item.partition.value == "calibration" for item in instances)


def test_evaluation_selection_includes_all_36_streams_and_no_calibration(index) -> None:  # type: ignore[no-untyped-def]
    instances = select_evaluation_instances(index)

    assert len(instances) == 864
    assert len({item.stream_id for item in instances}) == 36
    assert len({item.problem_id for item in instances}) == 198
    assert {item.temporal_seed for item in instances} == set(range(2026082302, 2026082308))
    assert all(item.partition.value == "evaluation" for item in instances)


def _score(
    policy: M7PolicyName,
    *,
    mean: float,
    median_cost: float,
    sheets: int,
) -> M7CalibrationPolicyScore:
    return M7CalibrationPolicyScore(
        policy=policy_identity(policy),
        mean_final_net_cost=mean,
        median_final_net_cost=median_cost,
        total_sheet_openings=sheets,
        replay_result_ids=tuple(f"yfm7r-{policy.value}-{offset}" for offset in range(12)),
    )


def test_calibration_selector_applies_score_ties_and_failure_gate() -> None:
    scores = tuple(
        _score(policy.name, mean=10.0, median_cost=5.0, sheets=288)
        for policy in registered_policy_identities()
    )
    lower_mean = tuple(
        item.model_copy(update={"mean_final_net_cost": 9.0})
        if item.policy.name is M7PolicyName.NET_COST
        else item
        for item in scores
    )
    median_tie = tuple(
        item.model_copy(update={"median_final_net_cost": 4.0})
        if item.policy.name is M7PolicyName.REMNANT_FIRST
        else item
        for item in scores
    )

    assert select_calibration_winner(lower_mean).policy.name is M7PolicyName.NET_COST
    assert select_calibration_winner(median_tie).policy.name is M7PolicyName.REMNANT_FIRST
    assert select_calibration_winner(scores).policy.name is M7PolicyName.AGE_REGULARITY
    invalid = tuple(
        item.model_copy(update={"invalid_stream_count": 1})
        if item.policy.name is M7PolicyName.AGE_REGULARITY
        else item
        for item in scores
    )
    with pytest.raises(ValueError, match="fails closed"):
        select_calibration_winner(invalid)


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


def test_calibration_result_reconciles_all_policies_and_freezes_winner(
    tmp_path: Path,
) -> None:
    streams = []
    for policy_offset, policy in enumerate(M7PolicyName):
        for seed_offset, seed in enumerate((2026082300, 2026082301)):
            for regime_offset, regime in enumerate(TemporalRegime):
                stream_offset = seed_offset * 6 + regime_offset + 1
                replay_offset = policy_offset * 12 + stream_offset
                streams.append(
                    M7CalibrationStreamResult(
                        policy=policy,
                        regime=regime,
                        temporal_seed=seed,
                        stream_id=f"yfts-{stream_offset:024x}",
                        stream_sha256=f"sha256:{stream_offset:064x}",
                        replay_input_id=f"yfm7ri-{replay_offset:024x}",
                        replay_input_sha256=f"sha256:{replay_offset + 1:064x}",
                        replay_result_id=f"yfm7r-{replay_offset:024x}",
                        replay_result_sha256=f"sha256:{replay_offset + 2:064x}",
                        replay_elapsed_seconds=1.0,
                        fit_search_seconds=0.2,
                        fit_search_cache_hit_count=3,
                        fit_search_cache_miss_count=1,
                        action_count=24,
                        full_sheet_opening_count=24,
                        remnant_retrieval_count=0,
                        final_net_cost=float(policy_offset + 1),
                    )
                )

    result = finalize_calibration_result(
        m0_contract_id="yfm0-" + "a" * 24,
        m0_contract_sha256="sha256:" + "b" * 64,
        problem_index_id="yfm7i-" + "c" * 24,
        problem_index_sha256="sha256:" + "d" * 64,
        feasibility_result_id="yfm7f-" + "e" * 24,
        feasibility_result_sha256="sha256:" + "f" * 64,
        collision_differential_result_id="yfm7d-" + "1" * 24,
        collision_differential_result_sha256="sha256:" + "2" * 64,
        candidate_verification_seconds=3.0,
        streams=tuple(streams),
    )
    path = publish_calibration_result(tmp_path, result)

    assert result.winning_policy.name is M7PolicyName.MYOPIC_GEOMETRY
    assert result.evaluation_partition_opened is False
    assert result.total_replay_seconds == 60.0
    assert result.total_fit_search_cache_hit_count == 180
    assert result.result_id == f"yfm7cal-{result.content_sha256[7:31]}"
    assert publish_calibration_result(tmp_path, result) == path


def _evaluation_stream(
    regime: TemporalRegime,
    seed: int,
    offset: int,
    *,
    policy: M7PolicyName = M7PolicyName.AGE_REGULARITY,
) -> M7EvaluationStreamResult:
    return M7EvaluationStreamResult(
        policy=policy_identity(policy),
        regime=regime,
        temporal_seed=seed,
        stream_id=f"yfts-{offset:024x}",
        stream_sha256=f"sha256:{offset:064x}",
        replay_input_id=f"yfm7ri-{offset:024x}",
        replay_input_sha256=f"sha256:{offset + 1:064x}",
        replay_result_id=f"yfm7r-{offset:024x}",
        replay_result_sha256=f"sha256:{offset + 2:064x}",
        action_count=48,
        full_sheet_opening_count=20,
        remnant_retrieval_count=4,
        terminal_remnant_count=3,
        final_net_cost=float(offset),
    )


def test_evaluation_result_accepts_only_frozen_policy_and_reproduces_identically(
    tmp_path: Path,
) -> None:
    streams = tuple(
        _evaluation_stream(regime, seed, offset + 1)
        for offset, (seed, regime) in enumerate(
            (seed, regime)
            for seed in range(2026082302, 2026082308)
            for regime in TemporalRegime
        )
    )
    candidate_set_ids = tuple(f"yfm7c-{offset:024x}" for offset in range(1, 199))
    candidate_set_sha256s = tuple(f"sha256:{offset:064x}" for offset in range(1, 199))
    first_pass = (
        streams[0].model_copy(update={"jagua_representation_fallback_count": 1}),
    ) + streams[1:]
    result = finalize_evaluation_result(
        freeze_id="yfm7freeze-" + "a" * 24,
        freeze_sha256="sha256:" + "b" * 64,
        calibration_result_id="yfm7cal-" + "c" * 24,
        calibration_result_sha256="sha256:" + "d" * 64,
        m0_contract_id="yfm0-" + "e" * 24,
        m0_contract_sha256="sha256:" + "f" * 64,
        problem_index_id="yfm7i-" + "1" * 24,
        problem_index_sha256="sha256:" + "2" * 64,
        frozen_policy=policy_identity(M7PolicyName.AGE_REGULARITY),
        candidate_set_ids=candidate_set_ids,
        candidate_set_sha256s=candidate_set_sha256s,
        raw_candidate_count=60_000,
        distinct_candidate_count=50_000,
        rejected_candidate_count=1,
        first_pass_streams=first_pass,
        second_pass_streams=streams,
    )

    assert result.stream_count == 36
    assert result.instance_count == 864
    assert result.repeat_count == 2
    assert result.repeat_content_identity_match is True
    assert result.total_jagua_representation_fallbacks == 1
    assert result.evaluation_partition_opened is True
    assert "policy_scores" not in result.model_dump()
    assert result.result_id == f"yfm7eval-{result.content_sha256[7:31]}"
    assert publish_evaluation_result(tmp_path, result) == publish_evaluation_result(
        tmp_path, result
    )

    changed_policy = streams[:-1] + (
        streams[-1].model_copy(update={"policy": policy_identity(M7PolicyName.NET_COST)}),
    )
    with pytest.raises(ValueError, match="frozen policy"):
        finalize_evaluation_result(
            freeze_id="yfm7freeze-" + "a" * 24,
            freeze_sha256="sha256:" + "b" * 64,
            calibration_result_id="yfm7cal-" + "c" * 24,
            calibration_result_sha256="sha256:" + "d" * 64,
            m0_contract_id="yfm0-" + "e" * 24,
            m0_contract_sha256="sha256:" + "f" * 64,
            problem_index_id="yfm7i-" + "1" * 24,
            problem_index_sha256="sha256:" + "2" * 64,
            frozen_policy=policy_identity(M7PolicyName.AGE_REGULARITY),
            candidate_set_ids=candidate_set_ids,
            candidate_set_sha256s=candidate_set_sha256s,
            raw_candidate_count=60_000,
            distinct_candidate_count=50_000,
            rejected_candidate_count=1,
            first_pass_streams=changed_policy,
            second_pass_streams=changed_policy,
        )

    changed_result = streams[:-1] + (
        streams[-1].model_copy(update={"final_net_cost": 999.0}),
    )
    with pytest.raises(ValueError, match="repeat execution differs"):
        finalize_evaluation_result(
            freeze_id="yfm7freeze-" + "a" * 24,
            freeze_sha256="sha256:" + "b" * 64,
            calibration_result_id="yfm7cal-" + "c" * 24,
            calibration_result_sha256="sha256:" + "d" * 64,
            m0_contract_id="yfm0-" + "e" * 24,
            m0_contract_sha256="sha256:" + "f" * 64,
            problem_index_id="yfm7i-" + "1" * 24,
            problem_index_sha256="sha256:" + "2" * 64,
            frozen_policy=policy_identity(M7PolicyName.AGE_REGULARITY),
            candidate_set_ids=candidate_set_ids,
            candidate_set_sha256s=candidate_set_sha256s,
            raw_candidate_count=60_000,
            distinct_candidate_count=50_000,
            rejected_candidate_count=1,
            first_pass_streams=streams,
            second_pass_streams=changed_result,
        )


def test_runtime_amendment_preserves_frozen_inputs_and_versions_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        Path(__file__).parents[2]
        / "experiments"
        / "results"
        / "m7-frozen-baseline-v1.json"
    )
    original = M7FrozenBaseline.model_validate_json(source.read_text(), strict=True)
    amended_runtime = original.runtime.model_copy(
        update={"replay_engine": "yieldforge.m7-baseline-replay@1.0.1"}
    )
    monkeypatch.setattr(
        "yieldforge.baseline.experiment._baseline_runtime_identity",
        lambda _executable: amended_runtime,
    )

    amended = amend_frozen_baseline_runtime(
        original,
        jagua_executable=Path("/unused/jagua"),
        trigger_stream_id="yfts-" + "9" * 24,
    )
    published = publish_frozen_baseline(tmp_path, amended)

    assert original.runtime.replay_engine == "yieldforge.m7-baseline-replay@1.0.0"
    assert amended.runtime.replay_engine == "yieldforge.m7-baseline-replay@1.0.1"
    assert amended.freeze_id != original.freeze_id
    assert amended.content_sha256 != original.content_sha256
    assert amended.winning_policy == original.winning_policy
    assert amended.candidate_set_ids == original.candidate_set_ids
    assert amended.candidate_set_sha256s == original.candidate_set_sha256s
    assert amended.runtime_amendment is not None
    assert amended.runtime_amendment.prior_freeze_id == original.freeze_id
    assert amended.runtime_amendment.policy_changed is False
    assert amended.runtime_amendment.candidate_population_changed is False
    assert published.name == "m7-frozen-baseline-v1.0.1.json"
    assert M7FrozenBaseline.model_validate_json(published.read_text(), strict=True) == amended
