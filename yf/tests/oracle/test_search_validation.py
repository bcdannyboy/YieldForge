from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import replace
from functools import cache
from inspect import getsource
from pathlib import Path

import pytest

import yieldforge.oracle.search_validation as search_validation
from tests.oracle.fixtures import exhaustive_certificate_cases
from yieldforge.baseline.contracts import M7ActionKind
from yieldforge.oracle.reference import score_reference_event
from yieldforge.oracle.search_validation import (
    evaluate_search_validation,
    evaluate_two_ply_repair_validation,
    score_two_ply_reoptimization,
    solve_exact_search,
)
from yieldforge.replay.contracts import rounded_cost

_RUNNER_PATH = Path(__file__).parents[2] / "tools" / "run_m9_minimal_search_validation.py"


@cache
def _matrix():  # type: ignore[no-untyped-def]
    return evaluate_search_validation(exhaustive_certificate_cases())


@cache
def _repair_matrix():  # type: ignore[no-untyped-def]
    return evaluate_two_ply_repair_validation(exhaustive_certificate_cases())


def _load_runner():  # type: ignore[no-untyped-def]
    module_name = "yieldforge_test_run_m9_minimal_search_validation"
    spec = importlib.util.spec_from_file_location(module_name, _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _semantic_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _all_mapping_keys(payload: object) -> tuple[str, ...]:
    if isinstance(payload, dict):
        return tuple(payload) + tuple(
            key
            for value in payload.values()
            for key in _all_mapping_keys(value)
        )
    if isinstance(payload, list):
        return tuple(key for value in payload for key in _all_mapping_keys(value))
    return ()


def _changed_telemetry_result():  # type: ignore[no-untyped-def]
    result = _matrix()
    first = result.primary.cases[0]
    changed_telemetry = replace(
        first.exact_search_telemetry,
        explored_transition_count=(
            first.exact_search_telemetry.explored_transition_count + 1
        ),
    )
    changed_first = replace(first, exact_search_telemetry=changed_telemetry)
    changed_primary = replace(
        result.primary,
        cases=(changed_first, *result.primary.cases[1:]),
    )
    return replace(result, primary=changed_primary)


def test_exact_search_matches_hand_computed_high_retrieval_case() -> None:
    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "remnant_first-one-match-fit-unequal-high-retrieval-three"
    )
    assert case.request.runtime.replay_input.rates.scrap_credit_per_area == 0.0

    result = solve_exact_search(case.request, include_terminal_credit=True)

    assert result.optimal_final_net_cost == 300.0
    assert result.optimal_first_action_ids == tuple(
        score.action_id
        for score in result.root_scores
        if score.kind is M7ActionKind.OPEN_STANDARD_SHEET
    )
    standard_scores = tuple(
        score.final_net_cost
        for score in result.root_scores
        if score.kind is M7ActionKind.OPEN_STANDARD_SHEET
    )
    remnant_scores = tuple(
        score.final_net_cost
        for score in result.root_scores
        if score.kind is M7ActionKind.CONSUME_REMNANT
    )
    assert standard_scores == (300.0, 300.0)
    assert remnant_scores == (400.0, 400.0)
    assert result.complete is True
    assert result.telemetry.truncated_catalog_count == 0
    assert result.telemetry.explored_transition_count > 0
    assert result.telemetry.terminal_leaf_count > 0


def test_two_ply_counterexample_selects_standard_through_strict_advantage() -> None:
    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "remnant_first-one-match-fit-unequal-high-retrieval-three"
    )

    result = score_two_ply_reoptimization(
        case.request,
        objective_label="scrap_only",
    )

    assert result.depth == 2
    assert result.objective_label == "scrap_only"
    assert result.objective_definition == (
        "m7_final_net_cost_including_terminal_scrap_credit"
    )
    assert tuple(
        score.bounded_objective_cost
        for score in result.root_scores
        if score.kind is M7ActionKind.OPEN_STANDARD_SHEET
    ) == (400.0, 400.0)
    assert tuple(
        score.bounded_objective_cost
        for score in result.root_scores
        if score.kind is M7ActionKind.CONSUME_REMNANT
    ) == (500.0, 500.0)
    score_by_action = {
        score.action_id: score.bounded_objective_cost for score in result.root_scores
    }
    kind_by_action = {score.action_id: score.kind for score in result.root_scores}
    assert kind_by_action[result.selected_action_id] is M7ActionKind.OPEN_STANDARD_SHEET
    assert score_by_action[result.selected_action_id] == 400.0
    assert score_by_action[result.baseline_action_id] == 500.0
    assert result.selected_action_id != result.baseline_action_id
    assert result.action_catalog_complete is True
    assert result.complete is True
    assert result.telemetry.catalog_count > 0
    assert result.telemetry.explicit_transition_count > 0
    assert result.telemetry.continuation_call_count > 0
    assert result.telemetry.continuation_event_count > 0
    assert result.telemetry.direct_terminalization_count == 0
    assert result.telemetry.truncated_catalog_count == 0
    assert result.telemetry.total_event_transition_count == (
        result.telemetry.explicit_transition_count
        + result.telemetry.continuation_event_count
    )

    scorer_source = getsource(score_two_ply_reoptimization)
    assert "solve_exact_search" not in scorer_source


def test_two_ply_terminalizes_directly_after_second_explicit_decision() -> None:
    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "remnant_first-zero-fit-equal-same-two"
    )

    result = score_two_ply_reoptimization(
        case.request,
        objective_label="scrap_only",
    )

    second_ply_transition_count = (
        result.telemetry.explicit_transition_count - len(result.root_scores)
    )
    assert second_ply_transition_count > 0
    assert result.telemetry.direct_terminalization_count == second_ply_transition_count
    assert result.telemetry.continuation_call_count == 0
    assert result.telemetry.continuation_event_count == 0
    assert len({score.bounded_objective_cost for score in result.root_scores}) == 1
    assert result.selected_action_id == result.baseline_action_id
    assert (
        result.telemetry.total_event_transition_count
        == result.telemetry.explicit_transition_count
    )
    assert result.telemetry.truncated_catalog_count == 0
    assert result.complete is True


def test_search_validation_matrix_preserves_the_bounded_counterexample() -> None:
    cases = exhaustive_certificate_cases()
    result = _matrix()
    expected_ids = tuple(case.case_id for case in cases)

    assert len(expected_ids) == len(set(expected_ids)) == 45
    assert result.case_count == 45
    assert result.objective_labels == (
        "scrap_only",
        "zero_total_terminal_credit",
    )
    assert tuple(item.case_id for item in result.primary.cases) == expected_ids
    assert tuple(item.case_id for item in result.terminal_sensitivity.cases) == expected_ids
    assert result.primary.objective_label == "scrap_only"
    assert (
        result.primary.objective_definition
        == "m7_final_net_cost_including_terminal_scrap_credit"
    )
    assert result.terminal_sensitivity.objective_label == "zero_total_terminal_credit"
    assert result.terminal_sensitivity.objective_definition == (
        "counterfactual_m7_final_net_cost_with_terminal_scrap_credit_added_back_only"
    )

    for record in (*result.primary.cases, *result.terminal_sensitivity.cases):
        assert record.complete is True
        assert record.action_catalog_complete is True
        assert record.exact_search_telemetry.truncated_catalog_count == 0
        assert record.rollout_selected_action_id in {
            score.action_id for score in record.one_step_scores
        }
        assert record.baseline_action_id in {
            score.action_id for score in record.one_step_scores
        }
        assert record.exact_optimal_first_action_ids
        assert record.exact_cost_after_selected_first_action >= record.exact_optimal_cost
        assert record.absolute_first_action_regret >= 0.0
        assert record.relative_first_action_regret >= 0.0
        assert record.exact_search_telemetry.explored_transition_count > 0
        assert record.exact_search_telemetry.terminal_leaf_count > 0

    assert result.decision == "fail_search_gap"
    assert result.primary.every_selected_action_is_globally_optimal is False
    assert result.primary.max_absolute_first_action_regret == 100.0
    assert tuple(item.case_id for item in result.primary.counterexamples) == (
        "remnant_first-one-match-fit-unequal-high-retrieval-three",
    )
    counterexample = result.primary.counterexamples[0]
    assert counterexample.rollout_selected_action_id == counterexample.baseline_action_id
    assert tuple(score.objective_cost for score in counterexample.one_step_scores) == (
        500.0,
        500.0,
        500.0,
        500.0,
    )
    assert counterexample.exact_optimal_cost == 300.0
    assert counterexample.exact_cost_after_selected_first_action == 400.0
    assert counterexample.absolute_first_action_regret == 100.0
    assert counterexample.relative_first_action_regret == 0.333333
    assert counterexample.selected_action_is_globally_optimal is False
    assert tuple(
        score.action_id
        for score in counterexample.exact_root_scores
        if score.kind is M7ActionKind.OPEN_STANDARD_SHEET
    ) == counterexample.exact_optimal_first_action_ids

    exact_source = getsource(solve_exact_search)
    assert "score_reference_event" not in exact_source
    assert "yieldforge.oracle.sparse" not in exact_source
    assert "yieldforge.oracle.factored" not in exact_source
    assert "yieldforge.oracle.checker" not in exact_source


def test_information_null_controls_are_honestly_labeled_and_tied() -> None:
    result = _matrix()
    expected_control_ids = tuple(
        case.case_id
        for case in exhaustive_certificate_cases()
        if case.case_id.endswith("zero-no-fit-equal-separated-two")
    )

    assert len(expected_control_ids) == 5
    assert result.information_null_control_case_ids == expected_control_ids
    for objective in (result.primary, result.terminal_sensitivity):
        controls = tuple(
            record for record in objective.cases if record.control_label is not None
        )
        assert tuple(record.case_id for record in controls) == expected_control_ids
        assert objective.information_null_controls_pass is True
        for record in controls:
            assert record.control_label == "tiny_information_null"
            assert "no_signal" not in record.control_label
            assert record.absolute_first_action_regret == 0.0
            assert record.relative_first_action_regret == 0.0
            assert record.rollout_selected_action_id == record.baseline_action_id
            assert len({score.objective_cost for score in record.one_step_scores}) == 1
            assert len({score.final_net_cost for score in record.exact_root_scores}) == 1


def test_terminal_objective_sensitivity_does_not_reverse_the_conclusion() -> None:
    result = _matrix()

    assert result.terminal_conclusion_does_not_reverse is True
    assert result.primary.conclusion == "fail_search_gap"
    assert result.terminal_sensitivity.conclusion == "fail_search_gap"
    assert tuple(item.case_id for item in result.terminal_sensitivity.counterexamples) == (
        "remnant_first-one-match-fit-unequal-high-retrieval-three",
    )
    assert result.terminal_sensitivity.counterexamples[0].absolute_first_action_regret == 100.0


def test_primary_one_step_scores_match_reference_for_all_registered_cases() -> None:
    cases = exhaustive_certificate_cases()
    comparisons = _matrix().primary.cases

    assert tuple(item.case_id for item in comparisons) == tuple(case.case_id for case in cases)
    for case, comparison in zip(cases, comparisons, strict=True):
        reference = score_reference_event(case.request).decision
        assert comparison.baseline_action_id == reference.baseline_action_id
        assert comparison.rollout_selected_action_id == reference.selected_action_id
        assert tuple(
            (item.action_id, item.objective_cost) for item in comparison.one_step_scores
        ) == tuple((item.action_id, item.final_net_cost) for item in reference.scores)


def test_two_ply_repair_matrix_passes_exact_action_gate_in_fixture_order() -> None:
    cases = exhaustive_certificate_cases()
    result = _repair_matrix()
    expected_ids = tuple(case.case_id for case in cases)

    assert result.case_count == 45
    assert result.objective_labels == (
        "scrap_only",
        "zero_total_terminal_credit",
    )
    assert tuple(record.case_id for record in result.primary.cases) == expected_ids
    assert tuple(record.case_id for record in result.terminal_sensitivity.cases) == (
        expected_ids
    )
    for objective in (result.primary, result.terminal_sensitivity):
        assert objective.complete is True
        assert objective.every_selected_action_is_globally_optimal is True
        assert objective.max_absolute_first_action_regret == 0.0
        assert objective.counterexamples == ()
        assert objective.information_null_controls_pass is True
        assert objective.conclusion == "pass_decision_feasibility"
        assert len(objective.cases) == 45
        for record in objective.cases:
            bounded_by_action = {
                score.action_id: score.bounded_objective_cost
                for score in record.two_ply_root_scores
            }
            exact_by_action = {
                score.action_id: score.final_net_cost
                for score in record.exact_root_scores
            }
            assert tuple(bounded_by_action) == tuple(exact_by_action)
            assert record.baseline_action_id in bounded_by_action
            assert record.repaired_selected_action_id in bounded_by_action
            assert record.exact_optimal_first_action_ids
            assert (
                record.repaired_selected_action_id
                in record.exact_optimal_first_action_ids
            )
            assert record.exact_cost_after_selected_first_action == exact_by_action[
                record.repaired_selected_action_id
            ]
            assert record.bounded_selected_action_score == bounded_by_action[
                record.repaired_selected_action_id
            ]
            assert record.bounded_selected_action_signed_error == rounded_cost(
                record.bounded_selected_action_score
                - record.exact_cost_after_selected_first_action
            )
            assert record.bounded_selected_action_absolute_error == abs(
                record.bounded_selected_action_signed_error
            )
            assert record.absolute_first_action_regret == 0.0
            assert record.relative_first_action_regret == 0.0
            assert record.selected_action_is_globally_optimal is True
            assert record.action_catalog_complete is True
            assert record.complete is True
            assert record.two_ply_search_telemetry.truncated_catalog_count == 0
            assert record.exact_search_telemetry.truncated_catalog_count == 0

    assert result.terminal_conclusion_does_not_reverse is True
    assert result.decision == "pass_decision_feasibility"


def test_two_ply_repair_compute_totals_reconcile_with_frozen_budget() -> None:
    result = _repair_matrix()

    for objective in (result.primary, result.terminal_sensitivity):
        compute = objective.compute_budget
        assert compute.observed_catalog_count == 185
        assert compute.observed_explicit_transition_count == 650
        assert compute.observed_continuation_event_count == 500
        assert compute.observed_total_event_transition_count == 1150
        assert compute.observed_total_event_transition_count == (
            compute.observed_explicit_transition_count
            + compute.observed_continuation_event_count
        )
        assert compute.observed_truncated_catalog_count == 0
        assert compute.max_catalog_count == 200
        assert compute.max_explicit_transition_count == 700
        assert compute.max_total_event_transition_count == 1200
        assert compute.totals_reconcile is True
        assert compute.pass_budget is True
        assert sum(
            record.two_ply_search_telemetry.catalog_count
            for record in objective.cases
        ) == compute.observed_catalog_count
        assert sum(
            record.two_ply_search_telemetry.explicit_transition_count
            for record in objective.cases
        ) == compute.observed_explicit_transition_count
        assert sum(
            record.two_ply_search_telemetry.continuation_event_count
            for record in objective.cases
        ) == compute.observed_continuation_event_count
        assert sum(
            record.two_ply_search_telemetry.total_event_transition_count
            for record in objective.cases
        ) == compute.observed_total_event_transition_count
        errors = tuple(
            record.bounded_selected_action_signed_error
            for record in objective.cases
        )
        value = objective.value_error_summary
        assert value.case_count == 45
        assert value.exact_value_match_count + value.nonexact_value_count == 45
        assert value.min_signed_error == min(errors)
        assert value.max_signed_error == max(errors)
        assert value.max_absolute_error == max(abs(error) for error in errors)
        assert value.nonexact_value_count > 0


def test_two_ply_repair_controls_and_terminal_credit_sensitivity_are_honest() -> None:
    result = _repair_matrix()
    expected_control_ids = tuple(
        case.case_id
        for case in exhaustive_certificate_cases()
        if case.case_id.endswith("zero-no-fit-equal-separated-two")
    )

    assert result.information_null_control_case_ids == expected_control_ids
    for objective in (result.primary, result.terminal_sensitivity):
        controls = tuple(
            record for record in objective.cases if record.control_label is not None
        )
        assert tuple(record.case_id for record in controls) == expected_control_ids
        assert all(record.control_label == "tiny_information_null" for record in controls)
        assert all("no_signal" not in record.control_label for record in controls)
        assert all(record.absolute_first_action_regret == 0.0 for record in controls)

    primary_by_id = {record.case_id: record for record in result.primary.cases}
    sensitivity_by_id = {
        record.case_id: record for record in result.terminal_sensitivity.cases
    }
    affected_ids = tuple(
        case_id
        for case_id in primary_by_id
        if primary_by_id[case_id].exact_optimal_cost
        != sensitivity_by_id[case_id].exact_optimal_cost
    )
    assert affected_ids
    for case_id in affected_ids:
        assert primary_by_id[case_id].selected_action_is_globally_optimal is True
        assert sensitivity_by_id[case_id].selected_action_is_globally_optimal is True


def test_two_ply_repair_preserves_original_failure_evidence() -> None:
    original = _matrix()
    repaired = _repair_matrix()

    assert original.decision == "fail_search_gap"
    assert tuple(case.case_id for case in original.primary.counterexamples) == (
        "remnant_first-one-match-fit-unequal-high-retrieval-three",
    )
    assert original.primary.max_absolute_first_action_regret == 100.0
    assert repaired.decision == "pass_decision_feasibility"
    assert repaired.primary.counterexamples == ()


def test_two_ply_repair_rejects_wrong_sensitivity_objective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_scorer = search_validation.score_two_ply_reoptimization

    def wrong_sensitivity_scorer(request, *, objective_label):  # type: ignore[no-untyped-def]
        return original_scorer(
            request,
            objective_label=(
                "scrap_only"
                if objective_label == "zero_total_terminal_credit"
                else objective_label
            ),
        )

    monkeypatch.setattr(
        search_validation,
        "score_two_ply_reoptimization",
        wrong_sensitivity_scorer,
    )

    with pytest.raises(RuntimeError, match="objective identity"):
        evaluate_two_ply_repair_validation(exhaustive_certificate_cases())


def test_two_ply_terminal_credit_objective_changes_positive_credit_fixture() -> None:
    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "remnant_first-zero-no-fit-equal-separated-two"
    )
    assert case.request.runtime.replay_input.rates.scrap_credit_per_area == 0.1

    primary = score_two_ply_reoptimization(
        case.request,
        objective_label="scrap_only",
    )
    sensitivity = score_two_ply_reoptimization(
        case.request,
        objective_label="zero_total_terminal_credit",
    )

    assert tuple(score.action_id for score in primary.root_scores) == tuple(
        score.action_id for score in sensitivity.root_scores
    )
    assert tuple(score.bounded_objective_cost for score in primary.root_scores) == (
        197.8,
        197.8,
    )
    assert tuple(score.bounded_objective_cost for score in sensitivity.root_scores) == (
        204.8,
        204.8,
    )


def test_two_ply_repair_rejects_bounded_root_cost_below_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "remnant_first-zero-fit-equal-same-two"
    )
    original_scorer = search_validation.score_two_ply_reoptimization

    def underestimated_scorer(request, *, objective_label):  # type: ignore[no-untyped-def]
        result = original_scorer(request, objective_label=objective_label)
        underestimated = replace(
            result.root_scores[0],
            bounded_objective_cost=-1_000_000.0,
        )
        return replace(
            result,
            root_scores=(underestimated, *result.root_scores[1:]),
        )

    monkeypatch.setattr(
        search_validation,
        "score_two_ply_reoptimization",
        underestimated_scorer,
    )

    with pytest.raises(RuntimeError, match="below its exact reachable cost"):
        evaluate_two_ply_repair_validation((case,))


def test_two_ply_repair_rejects_position_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = exhaustive_certificate_cases()[0]
    original_scorer = search_validation.score_two_ply_reoptimization

    def shifted_scorer(request, *, objective_label):  # type: ignore[no-untyped-def]
        result = original_scorer(request, objective_label=objective_label)
        return replace(
            result,
            stop_event_position=result.stop_event_position - 1,
        )

    monkeypatch.setattr(
        search_validation,
        "score_two_ply_reoptimization",
        shifted_scorer,
    )

    with pytest.raises(RuntimeError, match="search positions"):
        evaluate_two_ply_repair_validation((case,))


def test_runner_rebuilds_twice_and_publishes_content_addressed_fail_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    fixture_builds: list[tuple[object, ...]] = []
    evaluations: list[tuple[str, ...]] = []

    def fresh_cases():  # type: ignore[no-untyped-def]
        cases = exhaustive_certificate_cases()
        fixture_builds.append(cases)
        return cases

    def cached_evaluator(cases):  # type: ignore[no-untyped-def]
        evaluations.append(tuple(case.case_id for case in cases))
        return _matrix()

    monkeypatch.setattr(runner, "exhaustive_certificate_cases", fresh_cases)
    monkeypatch.setattr(runner, "evaluate_search_validation", cached_evaluator)

    outcome = runner.run_minimal_search_validation(output_directory=tmp_path)

    assert len(fixture_builds) == 2
    assert fixture_builds[0] is not fixture_builds[1]
    assert all(
        first is not second
        for first, second in zip(fixture_builds[0], fixture_builds[1], strict=True)
    )
    assert len(evaluations) == 2
    assert evaluations[0] == evaluations[1]
    assert outcome.decision == "fail_search_gap"
    assert len(outcome.pass_wall_seconds) == 2
    assert all(value >= 0.0 for value in outcome.pass_wall_seconds)
    assert outcome.artifact_path.is_file()

    raw = outcome.artifact_path.read_bytes()
    payload = json.loads(raw)
    assert raw.startswith(b"{\n") and raw.endswith(b"\n")
    assert payload["schema_version"] == "yieldforge.m9-minimal-search-validation.v1"
    assert payload["fixture_source"] == (
        "tests.oracle.fixtures.exhaustive_certificate_cases"
    )
    assert payload["fixture_build_count"] == 2
    assert payload["repeat_count"] == 2
    assert payload["repeat_semantic_identity_match"] is True
    assert payload["evaluation_partition_opened"] is False
    assert payload["evaluator_result"]["decision"] == "fail_search_gap"
    assert len(payload["ordered_case_ids"]) == 45
    assert not any("wall" in key for key in _all_mapping_keys(payload))

    evaluator_bytes = _semantic_bytes(payload["evaluator_result"])
    assert payload["reproducibility_sha256"] == (
        f"sha256:{hashlib.sha256(evaluator_bytes).hexdigest()}"
    )
    semantic_core = dict(payload)
    result_id = semantic_core.pop("result_id")
    content_sha256 = semantic_core.pop("content_sha256")
    digest = hashlib.sha256(_semantic_bytes(semantic_core)).hexdigest()
    assert result_id == f"yfm9-{digest[:24]}"
    assert content_sha256 == f"sha256:{digest}"
    assert outcome.result_id == result_id
    assert outcome.content_sha256 == content_sha256
    assert outcome.artifact_path.name == (
        f"m9-minimal-search-validation-{result_id}.json"
    )

    assert runner.main(["--output-directory", str(tmp_path)]) == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["decision"] == "fail_search_gap"
    assert Path(cli_payload["artifact_path"]) == outcome.artifact_path
    assert outcome.artifact_path.read_bytes() == raw


def test_runner_refuses_to_replace_different_existing_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    monkeypatch.setattr(
        runner,
        "evaluate_search_validation",
        lambda cases: _matrix(),
    )
    outcome = runner.run_minimal_search_validation(output_directory=tmp_path)
    outcome.artifact_path.write_bytes(b"foreign artifact\n")

    with pytest.raises(runner.M9RunnerError, match="different existing artifact"):
        runner.run_minimal_search_validation(output_directory=tmp_path)

    assert outcome.artifact_path.read_bytes() == b"foreign artifact\n"


@pytest.mark.parametrize("destination_kind", ["symlink", "directory"])
def test_runner_refuses_nonregular_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    destination_kind: str,
) -> None:
    runner = _load_runner()
    monkeypatch.setattr(
        runner,
        "evaluate_search_validation",
        lambda cases: _matrix(),
    )
    outcome = runner.run_minimal_search_validation(output_directory=tmp_path)
    outcome.artifact_path.unlink()
    if destination_kind == "symlink":
        target = tmp_path / "foreign.json"
        target.write_text("foreign\n", encoding="utf-8")
        outcome.artifact_path.symlink_to(target)
    else:
        outcome.artifact_path.mkdir()

    with pytest.raises(runner.M9RunnerError, match="regular file"):
        runner.run_minimal_search_validation(output_directory=tmp_path)


def test_runner_publishes_nothing_when_second_semantic_pass_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    results = iter((_matrix(), _changed_telemetry_result()))
    monkeypatch.setattr(
        runner,
        "evaluate_search_validation",
        lambda cases: next(results),
    )

    with pytest.raises(runner.M9RunnerError, match="semantic results differ"):
        runner.run_minimal_search_validation(output_directory=tmp_path)

    assert not tuple(tmp_path.glob("m9-minimal-search-validation-*.json"))
