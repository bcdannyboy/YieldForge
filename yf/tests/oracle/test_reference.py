from __future__ import annotations

from tests.baseline.test_replay import _two_event_runtime


def test_reference_scores_every_current_action_and_prefers_exact_m7_tie() -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import M8OracleRequest, score_reference_event
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = _two_event_runtime()
    result = score_reference_event(
        M8OracleRequest(
            runtime=runtime,
            cursor=initial_m7_cursor(runtime.replay_input),
            visibility=FullRealizedVisibility(runtime.replay_input.instances),
        )
    )

    assert result.decision.scored_action_count == 2
    assert result.decision.selected_action_id == result.decision.baseline_action_id
    assert {item.action_id for item in result.decision.scores} == {
        "m7-standard:candidate-one",
        "m7-standard:candidate-two",
    }
    assert result.continuation_event_executions == 2


def test_reference_known_only_scores_current_action_then_terminal() -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import M8OracleRequest, score_reference_event
    from yieldforge.oracle.visibility import KnownOnlyVisibility

    runtime = _two_event_runtime()
    result = score_reference_event(
        M8OracleRequest(
            runtime=runtime,
            cursor=initial_m7_cursor(runtime.replay_input),
            visibility=KnownOnlyVisibility(runtime.replay_input.instances),
        )
    )
    assert result.continuation_event_executions == 0
    assert result.decision.selected_action_id == result.decision.baseline_action_id


def test_reference_all_action_scoring_delegates_to_exact_single_action_scores() -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import (
        M8OracleRequest,
        score_reference_action,
        score_reference_event,
    )
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = _two_event_runtime()
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    result = score_reference_event(request)

    assert tuple(
        score_reference_action(request, action_id=item.action_id)
        for item in result.decision.scores
    ) == result.decision.scores
