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


def test_generator_passive_advance_applies_once_and_hashes_two_cursors(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.baseline import replay as replay_module
    from yieldforge.oracle import certificates, sparse
    from yieldforge.oracle.certificates import (
        build_validated_m8_common_transition_in_context,
    )

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    counts = {"apply": 0, "hash": 0}
    original_apply = certificates.apply_m7_frozen_action_evidence_with_commitments
    original_hash = replay_module.m7_cursor_sha256

    def counted_apply(*args, **kwargs):  # type: ignore[no-untyped-def]
        counts["apply"] += 1
        return original_apply(*args, **kwargs)

    def counted_hash(*args, **kwargs):  # type: ignore[no-untyped-def]
        counts["hash"] += 1
        return original_hash(*args, **kwargs)

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        descriptor = next(
            item
            for item in context._catalog.actions  # noqa: SLF001
            if item.action_id == "m7-standard:candidate-two"
        )
        branch = sparse._initial_branch(context, descriptor)  # noqa: SLF001
        common = build_validated_m8_common_transition_in_context(
            context._authority,  # noqa: SLF001
            cursor=context._fallback_step.cursor,  # noqa: SLF001
        )
        monkeypatch.setattr(
            certificates,
            "apply_m7_frozen_action_evidence_with_commitments",
            counted_apply,
        )
        monkeypatch.setattr(replay_module, "m7_cursor_sha256", counted_hash)

        sparse._advance_branch(context, branch, common=common)  # noqa: SLF001

    assert branch.skipped_count == 1
    assert counts == {"apply": 1, "hash": 2}


def test_generator_hashes_the_shared_start_state_once_per_batch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_hash = sparse.m7_cursor_sha256
    start_hash_count = 0

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001

        def counted_hash(cursor):  # type: ignore[no-untyped-def]
            nonlocal start_hash_count
            if cursor is context._request.cursor:  # noqa: SLF001
                start_hash_count += 1
            return original_hash(cursor)

        monkeypatch.setattr(sparse, "m7_cursor_sha256", counted_hash)
        sparse._score_prepared_certificate_actions(context)  # noqa: SLF001

    assert start_hash_count == 1


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


def test_certificate_action_batch_scores_only_frozen_ordered_subset() -> None:
    from yieldforge.oracle.checker import check_action_proofs
    from yieldforge.oracle.sparse import score_certificate_actions, score_sparse_event

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    full = score_sparse_event(request)
    action_ids = tuple(
        item.action_id for item in (full.decision.scores[-1], full.decision.scores[0])
    )

    sampled = score_certificate_actions(request, action_ids=action_ids)

    assert tuple(item.score.action_id for item in sampled) == action_ids
    assert tuple(item.score for item in sampled) == tuple(
        next(score for score in full.decision.scores if score.action_id == action_id)
        for action_id in action_ids
    )
    assert all(
        result.valid
        for result in check_action_proofs(
            request, tuple(item.proof for item in sampled)
        )
    )


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
