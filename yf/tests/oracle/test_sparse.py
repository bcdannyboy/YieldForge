from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest
from shapely import box

from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_semantic_runtime_sha256,
)
from yieldforge.oracle.reference import M8OracleRequest, score_reference_event
from yieldforge.oracle.visibility import FullRealizedVisibility


def test_public_visibility_cannot_mutate_a_detached_generator_cursor() -> None:
    import sys

    from yieldforge.oracle.sparse import score_sparse_event

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0, event_count=2)
    cursor = initial_m7_cursor(runtime.replay_input)
    binding = runtime.replay_input.instances[0]
    item = inventory_item(
        box(0, 0, 4, 10),
        material=binding.material.model_copy(deep=True),
        token="visibility-generator-cursor",
    )
    state = {"hits": 0}

    class StackVisibility:
        mode = "full_realized_future"

        def visible_suffix(self, *, current_position):  # type: ignore[no-untyped-def]
            frame = sys._getframe(1)  # noqa: SLF001
            while frame is not None:
                if (
                    frame.f_code.co_name == "_prepare_m8_generator_context"
                    and "captured_cursor" in frame.f_locals
                ):
                    state["hits"] += 1
                    object.__setattr__(frame.f_locals["captured_cursor"], "inventory", (item,))
                    break
                frame = frame.f_back
            return runtime.replay_input.instances[current_position + 1 :]

    expected = score_sparse_event(
        M8OracleRequest(
            runtime=runtime,
            cursor=cursor,
            visibility=FullRealizedVisibility(runtime.replay_input.instances),
        )
    )
    actual = score_sparse_event(
        M8OracleRequest(
            runtime=runtime,
            cursor=cursor,
            visibility=StackVisibility(),  # type: ignore[arg-type]
        )
    )

    assert actual.decision == expected.decision
    assert cursor.inventory == ()
    assert state["hits"] == 0


def test_public_generator_captures_request_cursor_before_any_profiled_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[0]
    item = inventory_item(
        box(0, 0, 4, 10),
        material=binding.material.model_copy(deep=True),
        token="early-context-order",
    )
    state = {"get": 0, "apply": 0}
    item_type = type(item)

    def evil_getattribute(self, name):  # type: ignore[no-untyped-def]
        if name == "remnant":
            state["get"] += 1
        return object.__getattribute__(self, name)

    evil_item_type = type(
        "EvilEarlyGeneratorItem",
        (item_type,),
        {"__getattribute__": evil_getattribute},
    )
    evil_item_type.__pydantic_generic_metadata__ = {
        "origin": item_type,
        "args": (),
        "parameters": (),
    }
    object.__setattr__(item, "__class__", evil_item_type)
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(item,))
    request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_apply = sparse.apply_m7_action_descriptor

    def counted_apply(*args, **kwargs):  # type: ignore[no-untyped-def]
        state["apply"] += 1
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(sparse, "apply_m7_action_descriptor", counted_apply)
    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="inventory source capture",
        ):
            sparse.score_sparse_event(request)

    assert state == {"get": 0, "apply": 0}
    counts = profiler.report().counts
    for name in (
        "events",
        "candidates",
        "frontier_entries",
        "actions",
        "facts",
        "fallbacks",
        "full_authoritative_fallbacks",
    ):
        assert counts[name] == 0


def test_public_generator_captures_action_id_before_authority() -> None:
    from yieldforge.oracle import compiled, sparse

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    requested = enumerate_m7_action_catalog(runtime, cursor=cursor).actions[0].action_id
    state = {"hash": 0}

    class EvilActionId(str):
        def __hash__(self) -> int:
            state["hash"] += 1
            return str.__hash__(self)

    with pytest.raises(
        compiled.M8PreparedFrontierIntegrityError,
        match="action ID source capture",
    ):
        sparse.score_certificate_action(request, action_id=EvilActionId(requested))

    assert state["hash"] == 0


def test_public_generator_rejects_request_class_drift_without_getters() -> None:
    from yieldforge.oracle import compiled, sparse

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    state = {"runtime_gets": 0}

    def evil_getattribute(self, name):  # type: ignore[no-untyped-def]
        if name == "runtime":
            state["runtime_gets"] += 1
        return object.__getattribute__(self, name)

    evil_request_type = type(
        "EvilGeneratorRequest",
        (M8OracleRequest,),
        {"__getattribute__": evil_getattribute},
    )
    object.__setattr__(request, "__class__", evil_request_type)

    with pytest.raises(
        compiled.M8PreparedFrontierIntegrityError,
        match="generator request source capture",
    ):
        sparse.score_sparse_event(request)

    assert state["runtime_gets"] == 0


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


@pytest.mark.parametrize(
    "entrypoint",
    ("sparse", "actions", "action"),
)
def test_public_generator_apis_preserve_typed_body_error_over_cleanup_drift(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    action_id = enumerate_m7_action_catalog(runtime, cursor=cursor).actions[0].action_id
    sentinel = compiled.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: public generator {entrypoint} sentinel"
    )

    def corrupt_body(context, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        sparse._PREPARED_GENERATOR_REGISTRY.pop(id(context))  # noqa: SLF001
        compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
            id(context._prepared_layouts)  # noqa: SLF001
        )
        raise sentinel

    monkeypatch.setattr(sparse, "_score_prepared_certificate_actions", corrupt_body)
    with activate_m8_profile() as profiler:
        with pytest.raises(compiled.M8PreparedFrontierIntegrityError) as captured:
            if entrypoint == "sparse":
                sparse.score_sparse_event(request)
            elif entrypoint == "actions":
                sparse.score_certificate_actions(request, action_ids=(action_id,))
            else:
                sparse.score_certificate_action(request, action_id=action_id)

    assert captured.value is sentinel
    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


@pytest.mark.parametrize("registry_kind", ("authority", "context"))
@pytest.mark.parametrize("registry_state", ("missing", "malformed"))
def test_public_generator_rejects_capability_registry_drift_before_traversal(
    monkeypatch: pytest.MonkeyPatch,
    registry_kind: str,
    registry_state: str,
) -> None:
    from yieldforge.baseline import replay
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_prepare = sparse._prepare_m8_generator_context  # noqa: SLF001
    original_build = sparse.build_validated_m8_common_transition_in_context
    traversals = 0

    @contextmanager
    def corrupt_context(request):  # type: ignore[no-untyped-def]
        with original_prepare(request) as context:
            if registry_kind == "authority":
                registry = replay._AUTHORITATIVE_PROOF_RUNTIME_REGISTRY  # noqa: SLF001
                capability_id = id(context._authority)  # noqa: SLF001
            else:
                registry = sparse._PREPARED_GENERATOR_REGISTRY  # noqa: SLF001
                capability_id = id(context)
            record = registry[capability_id]
            if registry_state == "missing":
                registry.pop(capability_id)
            else:
                registry[capability_id] = object()  # type: ignore[assignment]
            try:
                yield context
            finally:
                registry[capability_id] = record

    def count_traversal(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal traversals
        traversals += 1
        return original_build(*args, **kwargs)

    monkeypatch.setattr(sparse, "_prepare_m8_generator_context", corrupt_context)
    monkeypatch.setattr(
        sparse,
        "build_validated_m8_common_transition_in_context",
        count_traversal,
    )
    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match=(
                "generator proof authority capability"
                if registry_kind == "authority"
                else "prepared generator capability"
            ),
        ):
            sparse.score_sparse_event(request)

    counts = profiler.report().counts
    assert traversals == 0
    assert counts["actions"] == 0
    assert counts["facts"] == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_public_generator_drains_persistently_malformed_context_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse

    local_runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    foreign_runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    local_request = M8OracleRequest(
        runtime=local_runtime,
        cursor=initial_m7_cursor(local_runtime.replay_input),
        visibility=FullRealizedVisibility(local_runtime.replay_input.instances),
    )
    foreign_request = M8OracleRequest(
        runtime=foreign_runtime,
        cursor=initial_m7_cursor(foreign_runtime.replay_input),
        visibility=FullRealizedVisibility(foreign_runtime.replay_input.instances),
    )
    original_prepare = sparse._prepare_m8_generator_context  # noqa: SLF001

    with original_prepare(foreign_request) as foreign_context:
        foreign_record = sparse._PREPARED_GENERATOR_REGISTRY[id(foreign_context)]  # noqa: SLF001

        @contextmanager
        def corrupt_context(request):  # type: ignore[no-untyped-def]
            with original_prepare(request) as context:
                sparse._PREPARED_GENERATOR_REGISTRY[id(context)] = object()  # type: ignore[assignment]  # noqa: SLF001
                yield context

        monkeypatch.setattr(sparse, "_prepare_m8_generator_context", corrupt_context)
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared generator capability",
        ):
            sparse.score_sparse_event(local_request)

        assert sparse._PREPARED_GENERATOR_REGISTRY == {  # noqa: SLF001
            id(foreign_context): foreign_record
        }


def test_prepared_generator_retains_complete_standard_actions_without_descriptor_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline import replay as replay_module
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original = replay_module.build_standard_sheet_action
    built = []

    def recording_build(*args, **kwargs):  # type: ignore[no-untyped-def]
        action = original(*args, **kwargs)
        built.append(action)
        return action

    monkeypatch.setattr(replay_module, "build_standard_sheet_action", recording_build)
    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        catalog = context._catalog  # noqa: SLF001
        retained = catalog.generated.materialized_standard_actions
        binding_id = runtime.replay_input.instances[0].binding_id
        current_event_built = tuple(
            item
            for item in built
            if item.selected_stock.lineage.root_stock_id == binding_id
        )
        standard_descriptors = tuple(
            item
            for item in catalog.actions
            if item.kind.value == "open_standard_sheet"
        )

        assert len(current_event_built) == catalog.standard_action_count
        assert tuple(item.candidate_id for item in retained) == tuple(
            item.candidate_id for item in standard_descriptors
        )
        assert all(item.evidence is None for item in standard_descriptors)


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
