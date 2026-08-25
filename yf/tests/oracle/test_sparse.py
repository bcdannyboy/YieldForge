from __future__ import annotations

from dataclasses import replace

import pytest

from tests.oracle.fixtures import two_problem_runtime
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_semantic_runtime_sha256,
)
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
    assert len(sparse.proofs) == sparse.decision.scored_action_count

    from yieldforge.oracle.checker import check_action_proof

    assert all(check_action_proof(request, proof).valid for proof in sparse.proofs)
    assert any(
        witness.classification in {"no_fit", "policy_dominated"}
        for proof in sparse.proofs
        for witness in proof.witnesses
    )


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
    assert any(
        witness.classification == "exact_transition"
        for proof in sparse.proofs
        for witness in proof.witnesses
    )

    from yieldforge.oracle.checker import check_action_proof

    assert all(check_action_proof(request, proof).valid for proof in sparse.proofs)


@pytest.mark.parametrize(
    "poisoned_cache",
    ["standard_profile", "fit_search", "prepared_layout", "shared_fit_search"],
)
def test_proof_paths_ignore_unhashed_operational_cache_poisoning(
    poisoned_cache: str,
) -> None:
    from yieldforge.oracle.checker import check_action_proofs
    from yieldforge.oracle.sparse import score_sparse_event

    expected_runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.MYOPIC_GEOMETRY,
    )
    expected_request = M8OracleRequest(
        runtime=expected_runtime,
        cursor=initial_m7_cursor(expected_runtime.replay_input),
        visibility=FullRealizedVisibility(expected_runtime.replay_input.instances),
    )
    expected = score_reference_event(expected_request).decision

    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.MYOPIC_GEOMETRY,
    )
    runtime.shared_fit_search_cache = {}
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    descriptor = next(
        item for item in catalog.actions if item.action_id == "m7-standard:candidate-one"
    )
    step = apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=("populate_operational_caches=true",),
    )
    enumerate_m7_action_catalog(runtime, cursor=step.cursor)
    semantic_before = m7_semantic_runtime_sha256(runtime)

    if poisoned_cache == "standard_profile":
        key = next(key for key in runtime.standard_profile_cache if key[1] == "candidate-two")
        runtime.standard_profile_cache[key] = replace(
            runtime.standard_profile_cache[key],
            candidate_width=1.0,
        )
    elif poisoned_cache == "fit_search":
        key = next(iter(runtime.fit_search_cache))
        searches = runtime.fit_search_cache[key]
        runtime.fit_search_cache[key] = (
            searches[0].model_copy(update={"candidate_id": "poisoned-candidate"}),
            *searches[1:],
        )
    elif poisoned_cache == "prepared_layout":
        key = next(iter(runtime.prepared_layout_cache))
        runtime.prepared_layout_cache[key] = ()
    elif poisoned_cache == "shared_fit_search":
        assert runtime.shared_fit_search_cache
        key = next(iter(runtime.shared_fit_search_cache))
        searches = runtime.shared_fit_search_cache[key]
        runtime.shared_fit_search_cache[key] = (
            searches[0].model_copy(update={"candidate_id": "poisoned-candidate"}),
            *searches[1:],
        )
        runtime.fit_search_cache.clear()
    else:  # pragma: no cover - parametrization is exhaustive.
        raise AssertionError(poisoned_cache)

    assert m7_semantic_runtime_sha256(runtime) == semantic_before
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    reference = score_reference_event(request)
    sparse = score_sparse_event(request)
    checks = check_action_proofs(request, sparse.proofs)

    assert reference.decision == expected
    assert sparse.decision == expected
    assert all(result.valid for result in checks)
