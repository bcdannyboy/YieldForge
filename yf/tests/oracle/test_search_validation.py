from __future__ import annotations

from functools import cache
from inspect import getsource

from tests.oracle.fixtures import exhaustive_certificate_cases
from yieldforge.baseline.contracts import M7ActionKind
from yieldforge.oracle.search_validation import (
    evaluate_search_validation,
    solve_exact_search,
)


@cache
def _matrix():  # type: ignore[no-untyped-def]
    return evaluate_search_validation(exhaustive_certificate_cases())


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
