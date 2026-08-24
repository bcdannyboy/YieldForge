from __future__ import annotations

import pytest


def test_oracle_decision_requires_baseline_score_and_candidate_parity() -> None:
    from yieldforge.oracle.contracts import build_oracle_decision

    with pytest.raises(ValueError, match="baseline action"):
        build_oracle_decision(
            baseline_action_id="missing",
            expected_action_ids=("a", "b"),
            scores=(("a", 2.0), ("b", 1.0)),
        )


def test_oracle_exact_tie_prefers_m7_fallback_then_lexical_id() -> None:
    from yieldforge.oracle.contracts import build_oracle_decision

    decision = build_oracle_decision(
        baseline_action_id="b",
        expected_action_ids=("a", "b", "c"),
        scores=(("a", 1.0), ("b", 1.0), ("c", 1.0)),
    )
    assert decision.selected_action_id == "b"
