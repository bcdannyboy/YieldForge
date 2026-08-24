from __future__ import annotations

from tests.oracle.fixtures import two_problem_runtime
from yieldforge.baseline.replay import initial_m7_cursor
from yieldforge.oracle.reference import M8OracleRequest, score_reference_event
from yieldforge.oracle.visibility import FullRealizedVisibility


def test_sparse_passive_remnant_matches_reference_without_branch_replay() -> None:
    from yieldforge.oracle.sparse import score_sparse_event

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    reference = score_reference_event(request)
    sparse = score_sparse_event(request)

    assert sparse.decision == reference.decision
    assert sparse.metrics.skipped_passive_event_count > 0
    assert sparse.metrics.exact_branch_event_count == 0
    assert sparse.metrics.rejection_certificate_count > 0


def test_sparse_surviving_future_fit_falls_back_to_exact_and_matches_reference() -> None:
    from yieldforge.oracle.sparse import score_sparse_event

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    reference = score_reference_event(request)
    sparse = score_sparse_event(request)

    assert sparse.decision == reference.decision
    assert sparse.metrics.exact_branch_event_count > 0
