from __future__ import annotations

from tests.baseline.test_replay import _two_event_runtime


def test_compiled_standard_winner_matches_m7_with_empty_inventory() -> None:
    from yieldforge.baseline.replay import (
        enumerate_m7_action_catalog,
        initial_m7_cursor,
        select_m7_fallback,
    )
    from yieldforge.oracle.compiled import compile_standard_winner

    runtime = _two_event_runtime()
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    ordinary = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    compiled = compile_standard_winner(runtime, event_position=0)

    assert compiled.action_id == ordinary.action_id
    assert compiled.decision_key == ordinary.decision_key
    assert compiled.problem_id == runtime.replay_input.instances[0].problem_id
