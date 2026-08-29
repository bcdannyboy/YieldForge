from __future__ import annotations

from tests.oracle.fixtures import exhaustive_certificate_cases
from yieldforge.baseline.contracts import M7ActionKind
from yieldforge.oracle.search_validation import solve_exact_search


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
