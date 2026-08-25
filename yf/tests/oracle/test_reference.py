from __future__ import annotations

from contextlib import contextmanager

import pytest

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


def test_selected_reference_batch_preserves_order_and_matches_full_reference() -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import (
        M8OracleRequest,
        score_reference_actions,
        score_reference_event,
    )
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = _two_event_runtime()
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    full = score_reference_event(request)
    requested = tuple(reversed(full.decision.action_ids))

    selected = score_reference_actions(request, action_ids=requested)

    assert tuple(item.action_id for item in selected) == requested
    assert selected == tuple(
        next(item for item in full.decision.scores if item.action_id == action_id)
        for action_id in requested
    )


@pytest.mark.parametrize(
    "action_ids",
    [
        ("m7-standard:candidate-one", "m7-standard:candidate-one"),
        ("m7-standard:absent",),
    ],
)
def test_selected_reference_batch_rejects_duplicate_or_absent_ids(
    action_ids: tuple[str, ...],
) -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import M8OracleRequest, score_reference_actions
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = _two_event_runtime()
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    with pytest.raises(ValueError, match="unique|absent"):
        score_reference_actions(request, action_ids=action_ids)


def test_selected_reference_batch_owns_one_snapshot_and_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle import reference
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = _two_event_runtime()
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    counts = {"snapshots": 0, "catalogs": 0}
    original_authority = reference.authoritative_m7_proof_runtime
    original_catalog = reference.enumerate_m7_action_catalog

    @contextmanager
    def counted_authority(runtime):  # type: ignore[no-untyped-def]
        counts["snapshots"] += 1
        with original_authority(runtime) as authority:
            yield authority

    def counted_catalog(*args, **kwargs):  # type: ignore[no-untyped-def]
        counts["catalogs"] += 1
        return original_catalog(*args, **kwargs)

    monkeypatch.setattr(reference, "authoritative_m7_proof_runtime", counted_authority)
    monkeypatch.setattr(reference, "enumerate_m7_action_catalog", counted_catalog)

    reference.score_reference_actions(
        request,
        action_ids=("m7-standard:candidate-two", "m7-standard:candidate-one"),
    )

    assert counts == {"snapshots": 1, "catalogs": 1}
