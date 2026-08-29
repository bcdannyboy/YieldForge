from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests.baseline.test_replay import _two_event_runtime
from tests.oracle.fixtures import exhaustive_certificate_cases


def test_oracle_package_exports_selected_reference_batch() -> None:
    import yieldforge.oracle as oracle
    from yieldforge.oracle import score_reference_actions
    from yieldforge.oracle.reference import score_reference_actions as module_function

    assert score_reference_actions is module_function
    assert oracle.score_reference_actions is module_function
    assert "score_reference_actions" in oracle.__all__


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


def test_reference_runs_public_visibility_before_catalog_authority() -> None:
    import sys

    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import M8OracleRequest, score_reference_event
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = _two_event_runtime()
    state = {"hits": 0}

    class StackVisibility:
        mode = "full_realized_future"

        def visible_suffix(self, *, current_position):  # type: ignore[no-untyped-def]
            frame = sys._getframe(1)  # noqa: SLF001
            while frame is not None:
                if frame.f_code.co_name == "score_reference_event" and "fallback" in frame.f_locals:
                    state["hits"] += 1
                    break
                frame = frame.f_back
            return runtime.replay_input.instances[current_position + 1 :]

    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=StackVisibility(),  # type: ignore[arg-type]
    )
    expected = score_reference_event(
        M8OracleRequest(
            runtime=runtime,
            cursor=request.cursor,
            visibility=FullRealizedVisibility(runtime.replay_input.instances),
        )
    )

    assert score_reference_event(request) == expected
    assert state["hits"] == 0


def test_reference_rejects_callback_capable_action_identity_before_authority() -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.compiled import M8PreparedFrontierIntegrityError
    from yieldforge.oracle.reference import M8OracleRequest, score_reference_action
    from yieldforge.oracle.visibility import FullRealizedVisibility

    state = {"hashes": 0}

    class CallbackActionId(str):
        def __hash__(self) -> int:
            state["hashes"] += 1
            return str.__hash__(self)

    runtime = _two_event_runtime()
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    with pytest.raises(M8PreparedFrontierIntegrityError, match="action ID source"):
        score_reference_action(
            request,
            action_id=CallbackActionId("m7-standard:candidate-one"),
        )

    assert state["hashes"] == 0


def test_reference_rejects_visibility_data_inconsistent_with_known_only_mode() -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import M8OracleRequest, score_reference_event

    runtime = _two_event_runtime()

    class LeakingKnownOnlyVisibility:
        mode = "known_only"

        def visible_suffix(self, *, current_position):  # type: ignore[no-untyped-def]
            return runtime.replay_input.instances[current_position + 1 :]

    with pytest.raises(ValueError, match="inconsistent with its mode"):
        score_reference_event(
            M8OracleRequest(
                runtime=runtime,
                cursor=initial_m7_cursor(runtime.replay_input),
                visibility=LeakingKnownOnlyVisibility(),  # type: ignore[arg-type]
            )
        )


def test_reference_rejects_truncated_full_realized_visibility() -> None:
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.reference import M8OracleRequest, score_reference_event

    runtime = _two_event_runtime()

    class TruncatedFullVisibility:
        mode = "full_realized_future"

        def visible_suffix(self, *, current_position):  # type: ignore[no-untyped-def]
            del current_position
            return ()

    with pytest.raises(ValueError, match="inconsistent with its mode"):
        score_reference_event(
            M8OracleRequest(
                runtime=runtime,
                cursor=initial_m7_cursor(runtime.replay_input),
                visibility=TruncatedFullVisibility(),  # type: ignore[arg-type]
            )
        )


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
    counts = {"snapshots": 0, "current_catalogs": 0, "continuation_catalogs": 0}
    original_authority = reference.authoritative_m7_proof_runtime
    original_catalog = reference.enumerate_m7_action_catalog

    @contextmanager
    def counted_authority(runtime):  # type: ignore[no-untyped-def]
        counts["snapshots"] += 1
        with original_authority(runtime) as authority:
            yield authority

    def counted_catalog(*args, **kwargs):  # type: ignore[no-untyped-def]
        key = "current_catalogs" if kwargs.get("complete", True) else "continuation_catalogs"
        counts[key] += 1
        return original_catalog(*args, **kwargs)

    monkeypatch.setattr(reference, "authoritative_m7_proof_runtime", counted_authority)
    monkeypatch.setattr(reference, "enumerate_m7_action_catalog", counted_catalog)

    reference.score_reference_actions(
        request,
        action_ids=("m7-standard:candidate-two", "m7-standard:candidate-one"),
    )

    assert counts == {
        "snapshots": 1,
        "current_catalogs": 1,
        "continuation_catalogs": 2,
    }


@pytest.mark.parametrize(
    "case",
    exhaustive_certificate_cases(),
    ids=lambda case: case.case_id,
)
def test_selected_reference_batch_matches_repeated_single_action_replay(case) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.baseline.replay import enumerate_m7_action_catalog
    from yieldforge.oracle.reference import (
        score_reference_action,
        score_reference_actions,
    )

    catalog = enumerate_m7_action_catalog(
        case.request.runtime,
        cursor=case.request.cursor,
    )
    action_ids = tuple(item.action_id for item in reversed(catalog.actions))

    assert score_reference_actions(case.request, action_ids=action_ids) == tuple(
        score_reference_action(case.request, action_id=action_id)
        for action_id in action_ids
    )


def test_selected_reference_batch_advances_branches_event_major(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import reference

    case = next(
        item
        for item in exhaustive_certificate_cases()
        if len(item.request.runtime.replay_input.instances) == 4
    )
    original_catalog = reference.enumerate_m7_action_catalog
    event_positions: list[int] = []

    def observed_catalog(runtime, *, cursor, **kwargs):  # type: ignore[no-untyped-def]
        event_positions.append(cursor.next_event_position)
        return original_catalog(runtime, cursor=cursor, **kwargs)

    monkeypatch.setattr(reference, "enumerate_m7_action_catalog", observed_catalog)
    initial = original_catalog(case.request.runtime, cursor=case.request.cursor)
    action_ids = tuple(item.action_id for item in initial.actions)

    reference.score_reference_actions(case.request, action_ids=action_ids)

    expected_continuation_positions = tuple(
        position
        for position in range(case.request.cursor.next_event_position + 1, 4)
        for _action_id in action_ids
    )
    assert tuple(event_positions) == (
        case.request.cursor.next_event_position,
        *expected_continuation_positions,
    )
