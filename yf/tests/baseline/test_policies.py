from __future__ import annotations

import pytest

from yieldforge.baseline.contracts import M7ActionKind
from yieldforge.baseline.policies import (
    ActionPolicyContext,
    M7PolicyName,
    PolicyRank,
    rank_policy_action,
    registered_policy_identities,
    select_policy_action,
)


def _choice(
    action_id: str,
    *,
    kind: M7ActionKind,
    width: float,
    cost: float,
    age: float = 0.0,
    regularity: float = 0.0,
) -> ActionPolicyContext:
    return ActionPolicyContext(
        action_id=action_id,
        kind=kind,
        candidate_id=f"candidate-{action_id}",
        candidate_width=width,
        selected_stock_id=f"stock-{action_id}",
        immediate_net_cost=cost,
        selected_remnant_age_hours=age,
        returned_regularity=regularity,
        known_order_lookahead_term=0.0,
    )


def test_registered_policies_freeze_five_as_of_safe_variants() -> None:
    identities = registered_policy_identities()

    assert tuple(item.name for item in identities) == tuple(M7PolicyName)
    assert len(identities) == 5
    assert all(item.seed == 0 for item in identities)
    lookahead = next(item for item in identities if item.name is M7PolicyName.KNOWN_ORDER_LOOKAHEAD)
    assert lookahead.information_set == "released_work_inventory_and_firm_known_orders_only"
    assert lookahead.lookahead_availability == "zero_no_pre_release_known_at_field"


def test_policy_variants_apply_their_frozen_ordering_and_stable_keys() -> None:
    remnant = _choice(
        "remnant",
        kind=M7ActionKind.CONSUME_REMNANT,
        width=6.0,
        cost=9.0,
        age=8.0,
        regularity=0.8,
    )
    sheet = _choice(
        "sheet",
        kind=M7ActionKind.OPEN_STANDARD_SHEET,
        width=4.0,
        cost=5.0,
        regularity=0.9,
    )

    myopic = select_policy_action(M7PolicyName.MYOPIC_GEOMETRY, (remnant, sheet))
    remnant_first = select_policy_action(M7PolicyName.REMNANT_FIRST, (remnant, sheet))
    net_cost = select_policy_action(M7PolicyName.NET_COST, (remnant, sheet))

    assert myopic.action_id == "sheet"
    assert remnant_first.action_id == "remnant"
    assert net_cost.action_id == "sheet"
    assert myopic == select_policy_action(M7PolicyName.MYOPIC_GEOMETRY, (sheet, remnant))
    assert myopic.decision_key
    assert all("=" in term for term in myopic.decision_key)


@pytest.mark.parametrize(
    ("policy", "expected_comparison_prefix", "expected_decision_key"),
    (
        (
            M7PolicyName.MYOPIC_GEOMETRY,
            (4.0,),
            (
                "candidate_width=4",
                "candidate_id=candidate-a",
                "selected_stock_id=stock-a",
                "action_id=action-a",
            ),
        ),
        (
            M7PolicyName.REMNANT_FIRST,
            (0, 5.0),
            (
                "remnant_first_rank=0",
                "immediate_net_cost=5",
                "candidate_id=candidate-a",
                "selected_stock_id=stock-a",
                "action_id=action-a",
            ),
        ),
        (
            M7PolicyName.NET_COST,
            (5.0,),
            (
                "immediate_net_cost=5",
                "candidate_id=candidate-a",
                "selected_stock_id=stock-a",
                "action_id=action-a",
            ),
        ),
        (
            M7PolicyName.AGE_REGULARITY,
            (5.0, -8.0, -0.75),
            (
                "immediate_net_cost=5",
                "selected_remnant_age_hours=8",
                "returned_regularity=0.75",
                "candidate_id=candidate-a",
                "selected_stock_id=stock-a",
                "action_id=action-a",
            ),
        ),
        (
            M7PolicyName.KNOWN_ORDER_LOOKAHEAD,
            (5.0, 0.0),
            (
                "combined_known_cost=5",
                "known_order_lookahead_term=0",
                "candidate_id=candidate-a",
                "selected_stock_id=stock-a",
                "action_id=action-a",
            ),
        ),
    ),
)
def test_public_policy_rank_matches_selection_with_stable_identity_tail(
    policy: M7PolicyName,
    expected_comparison_prefix: tuple[object, ...],
    expected_decision_key: tuple[str, ...],
) -> None:
    first = ActionPolicyContext(
        action_id="action-z",
        kind=M7ActionKind.CONSUME_REMNANT,
        candidate_id="candidate-z",
        candidate_width=4.0,
        selected_stock_id="stock-z",
        immediate_net_cost=5.0,
        selected_remnant_age_hours=8.0,
        returned_regularity=0.75,
        known_order_lookahead_term=0.0,
    )
    second = ActionPolicyContext(
        action_id="action-a",
        kind=M7ActionKind.CONSUME_REMNANT,
        candidate_id="candidate-a",
        candidate_width=4.0,
        selected_stock_id="stock-a",
        immediate_net_cost=5.0,
        selected_remnant_age_hours=8.0,
        returned_regularity=0.75,
        known_order_lookahead_term=0.0,
    )

    first_rank = rank_policy_action(policy, first)
    second_rank = rank_policy_action(policy, second)
    selected = select_policy_action(policy, (first, second))
    expected_context, expected_rank = min(
        ((first, first_rank), (second, second_rank)),
        key=lambda pair: pair[1],
    )

    assert isinstance(first_rank, PolicyRank)
    assert first_rank.comparison_key[-3:] == (
        first.candidate_id,
        first.selected_stock_id,
        first.action_id,
    )
    assert second_rank.comparison_key[-3:] == (
        second.candidate_id,
        second.selected_stock_id,
        second.action_id,
    )
    assert selected.action_id == expected_context.action_id
    assert expected_rank.comparison_key == (
        *expected_comparison_prefix,
        expected_context.candidate_id,
        expected_context.selected_stock_id,
        expected_context.action_id,
    )
    assert expected_rank.decision_key == expected_decision_key
    assert selected.decision_key == expected_rank.decision_key


def test_age_regularity_prefers_age_then_regular_children_after_equal_cost() -> None:
    younger = _choice(
        "younger",
        kind=M7ActionKind.CONSUME_REMNANT,
        width=4.0,
        cost=5.0,
        age=1.0,
        regularity=1.0,
    )
    older = _choice(
        "older",
        kind=M7ActionKind.CONSUME_REMNANT,
        width=4.0,
        cost=5.0,
        age=8.0,
        regularity=0.5,
    )

    selected = select_policy_action(M7PolicyName.AGE_REGULARITY, (younger, older))

    assert selected.action_id == "older"


def test_known_order_lookahead_is_explicitly_zero_and_matches_net_cost() -> None:
    expensive = _choice(
        "expensive",
        kind=M7ActionKind.CONSUME_REMNANT,
        width=3.0,
        cost=7.0,
    )
    cheap = _choice(
        "cheap",
        kind=M7ActionKind.OPEN_STANDARD_SHEET,
        width=5.0,
        cost=4.0,
    )

    lookahead = select_policy_action(
        M7PolicyName.KNOWN_ORDER_LOOKAHEAD,
        (expensive, cheap),
    )
    net_cost = select_policy_action(M7PolicyName.NET_COST, (expensive, cheap))

    assert lookahead.action_id == net_cost.action_id == "cheap"
    assert "known_order_lookahead_term=0" in lookahead.decision_key


def test_policy_fails_closed_when_no_action_exists() -> None:
    with pytest.raises(ValueError, match="no action"):
        select_policy_action(M7PolicyName.NET_COST, ())
