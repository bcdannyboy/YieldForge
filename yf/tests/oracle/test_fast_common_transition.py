from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from shapely import Polygon

from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.baseline.jagua import JaguaGeneratedPrefilterResult
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    enumerate_m7_pruned_action_catalog,
    enumerate_m7_standard_only_catalog,
    initial_m7_cursor,
    m7_semantic_runtime_sha256,
    select_m7_fallback,
)
from yieldforge.oracle import certificates
from yieldforge.oracle.profiling import activate_m8_profile


def _fallback_cursor(runtime):  # type: ignore[no-untyped-def]
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=False)
    selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(
        item for item in catalog.actions if item.action_id == selection.action_id
    )
    return apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selection.decision_key,
    ).cursor


@pytest.mark.parametrize(
    "policy",
    (
        M7PolicyName.MYOPIC_GEOMETRY,
        M7PolicyName.REMNANT_FIRST,
        M7PolicyName.NET_COST,
        M7PolicyName.AGE_REGULARITY,
        M7PolicyName.KNOWN_ORDER_LOOKAHEAD,
    ),
)
def test_standard_only_catalog_exactly_matches_zero_generation_rejection(
    policy: M7PolicyName,
) -> None:
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=policy,
    )
    cursor = _fallback_cursor(runtime)
    authoritative_runtime = certificates._fresh_runtime(runtime)  # noqa: SLF001
    standard_runtime = certificates._fresh_runtime(runtime)  # noqa: SLF001

    authoritative = enumerate_m7_action_catalog(
        authoritative_runtime,
        cursor=cursor,
        complete=False,
    )
    standard_only = enumerate_m7_standard_only_catalog(
        standard_runtime,
        cursor=cursor,
        zero_generation_rejected_inventory=cursor.inventory,
    )

    assert standard_only == authoritative


@pytest.mark.parametrize(
    "policy",
    (
        M7PolicyName.MYOPIC_GEOMETRY,
        M7PolicyName.REMNANT_FIRST,
        M7PolicyName.NET_COST,
        M7PolicyName.AGE_REGULARITY,
        M7PolicyName.KNOWN_ORDER_LOOKAHEAD,
    ),
)
def test_fast_common_transition_is_exact_for_frontier_rejection(
    policy: M7PolicyName,
) -> None:
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=policy,
    )
    cursor = _fallback_cursor(runtime)
    semantic_sha256 = m7_semantic_runtime_sha256(runtime)

    authoritative = certificates._derive_m8_common_transition_fact_authoritative(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )
    fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )

    assert fast is not None
    assert fast.fact == authoritative
    assert fast.zero_generation_rejected_inventory == cursor.inventory
    assert fast.witnesses
    assert all(witness.impossible for witness in fast.witnesses)


def test_fast_common_transition_fails_closed_to_authoritative_survivor() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = _fallback_cursor(runtime)
    semantic_sha256 = m7_semantic_runtime_sha256(runtime)

    fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )
    expected = certificates._derive_m8_common_transition_fact_authoritative(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )
    actual = certificates._derive_m8_common_transition_fact_unprofiled(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )

    assert fast is None
    assert actual == expected


@pytest.mark.parametrize(
    "policy",
    (
        M7PolicyName.MYOPIC_GEOMETRY,
        M7PolicyName.REMNANT_FIRST,
        M7PolicyName.NET_COST,
        M7PolicyName.AGE_REGULARITY,
        M7PolicyName.KNOWN_ORDER_LOOKAHEAD,
    ),
)
def test_mixed_pruned_catalog_searches_only_survivors_and_remains_exact(
    monkeypatch: pytest.MonkeyPatch,
    policy: M7PolicyName,
) -> None:
    from yieldforge.baseline import replay

    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=policy,
    )
    survivor = _fallback_cursor(runtime).inventory[0]
    rejected = inventory_item(
        Polygon(((0, 0), (1, 0), (1, 1), (0, 1))),
        material=runtime.replay_input.instances[1].material,
        token="mixed-pruned-rejected",
    )
    inventory = tuple(
        sorted((rejected, survivor), key=lambda item: item.remnant.remnant_id)
    )
    cursor = replace(_fallback_cursor(runtime), inventory=inventory)
    original = replay._search_candidate_chunk  # noqa: SLF001
    searched_remnant_ids: list[str] = []

    def tracked(arguments):  # type: ignore[no-untyped-def]
        searched_remnant_ids.append(arguments[0].remnant_id)
        return original(arguments)

    monkeypatch.setattr(replay, "_search_candidate_chunk", tracked)
    authoritative = enumerate_m7_action_catalog(
        certificates._fresh_runtime(runtime),  # noqa: SLF001
        cursor=cursor,
        complete=False,
    )
    assert set(searched_remnant_ids) == {
        rejected.remnant.remnant_id,
        survivor.remnant.remnant_id,
    }

    searched_remnant_ids.clear()
    pruned = enumerate_m7_pruned_action_catalog(
        certificates._fresh_runtime(runtime),  # noqa: SLF001
        cursor=cursor,
        zero_generation_rejected_inventory=(rejected,),
    )

    assert searched_remnant_ids == [survivor.remnant.remnant_id]
    assert pruned == authoritative


def test_fast_common_transition_prunes_rejects_and_searches_survivors() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    survivor = _fallback_cursor(runtime).inventory[0]
    rejected = inventory_item(
        Polygon(((0, 0), (1, 0), (1, 1), (0, 1))),
        material=runtime.replay_input.instances[1].material,
        token="mixed-fast-common-rejected",
    )
    inventory = tuple(
        sorted((rejected, survivor), key=lambda item: item.remnant.remnant_id)
    )
    cursor = replace(_fallback_cursor(runtime), inventory=inventory)
    semantic_sha256 = m7_semantic_runtime_sha256(runtime)

    expected = certificates._derive_m8_common_transition_fact_authoritative(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )
    fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )

    assert fast is not None
    assert fast.fact == expected
    assert fast.zero_generation_rejected_inventory == (rejected,)
    assert fast.exact_survivor_inventory == (survivor,)
    assert tuple(item.remnant_id for item in fast.witnesses) == (
        rejected.remnant.remnant_id,
    )

    with activate_m8_profile() as profiler:
        actual = certificates._derive_m8_common_transition_fact_unprofiled(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_sha256,
            differential=True,
        )

    assert actual == expected
    counts = profiler.report().counts
    assert counts["partially_pruned_transitions"] == 1
    assert counts["frontier_rejected_inventory_items"] == 1
    assert counts["exact_survivor_inventory_items"] == 1
    assert counts["frontier_rejected_transitions"] == 0
    assert counts["standard_only_materializations"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert counts["differential_mismatches"] == 0


def test_area_only_rejection_synthesizes_counts_without_collision_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline import replay

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    material = runtime.replay_input.instances[1].material
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=material,
        token="area-only-fast-common",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    semantic_sha256 = m7_semantic_runtime_sha256(runtime)
    expected = certificates._derive_m8_common_transition_fact_authoritative(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )

    def unexpected_collision_search(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("area proof invoked registered collision search")

    monkeypatch.setattr(replay, "_search_candidate_chunk", unexpected_collision_search)

    fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )

    assert fast is not None
    assert fast.fact == expected
    assert fast.zero_generation_rejected_inventory == ()
    assert fast.counted_no_fit_inventory == (area_only,)
    assert fast.exact_survivor_inventory == ()

    with activate_m8_profile() as profiler:
        actual = certificates._derive_m8_common_transition_fact_unprofiled(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_sha256,
        )

    assert actual == expected
    counts = profiler.report().counts
    assert counts["counted_no_fit_transitions"] == 1
    assert counts["counted_no_fit_inventory_items"] == 1
    assert counts["counted_no_fit_candidate_searches"] == len(
        runtime.runtime_candidates[runtime.replay_input.instances[1].problem_id].candidates
    )
    assert counts["frontier_rejected_transitions"] == 0
    assert counts["standard_only_materializations"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_area_only_rejection_uses_jagua_only_for_exact_translation_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.baseline import replay

    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="area-only-jagua-counts",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    semantic_sha256 = m7_semantic_runtime_sha256(runtime)
    verified = runtime.runtime_candidates[binding.problem_id]
    python_generate = certificates.generate_layout_translations
    calls = []

    def fake_generated_prefilter(  # type: ignore[no-untyped-def]
        executable,
        *,
        remnant,
        layouts,
        fit_config,
        search_config,
        container_guard,
    ):
        calls.append((executable, container_guard))
        batches = tuple(
            python_generate(
                area_only.remnant,
                candidate,
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=layout,
                prepared_remnant=remnant,
            )
            for candidate, layout in zip(verified.candidates, layouts, strict=True)
        )
        return JaguaGeneratedPrefilterResult(
            translation_batches=batches,
            collision_masks=tuple((False,) * len(batch.translations) for batch in batches),
            guarded_query_count=sum(len(batch.translations) for batch in batches),
            jagua_rejection_count=0,
            build_microseconds=0,
            generation_microseconds=0,
            query_microseconds=0,
            wall_seconds=0.0,
        )

    monkeypatch.setattr(replay, "run_jagua_generated_prefilter", fake_generated_prefilter)
    monkeypatch.setattr(
        certificates,
        "run_jagua_generated_prefilter",
        fake_generated_prefilter,
    )
    expected = certificates._derive_m8_common_transition_fact_authoritative(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )
    calls.clear()

    def unexpected_python_generation(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("count synthesis bypassed Jagua translation generation")

    monkeypatch.setattr(
        certificates,
        "generate_layout_translations",
        unexpected_python_generation,
    )

    fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )

    assert fast is not None
    assert fast.fact == expected
    assert fast.counted_no_fit_inventory == (area_only,)
    assert calls == [(jagua_path, 1.0)]


def test_counted_no_fit_rejects_perturbed_jagua_counts_during_proof_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
        jagua_differential_audit=True,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="area-only-perturbed-counts",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    verified = runtime.runtime_candidates[binding.problem_id]
    python_generate = certificates.generate_layout_translations

    def perturbed_generated_prefilter(  # type: ignore[no-untyped-def]
        _executable,
        *,
        remnant,
        layouts,
        fit_config,
        search_config,
        container_guard: float,
    ):
        assert container_guard == 1.0
        batches = tuple(
            python_generate(
                area_only.remnant,
                candidate,
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=layout,
                prepared_remnant=remnant,
            )
            for candidate, layout in zip(verified.candidates, layouts, strict=True)
        )
        first = replace(
            batches[0],
            generated_candidate_count=batches[0].generated_candidate_count + 1,
        )
        perturbed = (first, *batches[1:])
        return JaguaGeneratedPrefilterResult(
            translation_batches=perturbed,
            collision_masks=tuple(
                (False,) * len(batch.translations) for batch in perturbed
            ),
            guarded_query_count=sum(len(batch.translations) for batch in perturbed),
            jagua_rejection_count=0,
            build_microseconds=0,
            generation_microseconds=0,
            query_microseconds=0,
            wall_seconds=0.0,
        )

    monkeypatch.setattr(
        certificates,
        "run_jagua_generated_prefilter",
        perturbed_generated_prefilter,
    )

    with pytest.raises(ValueError, match="translation counts differ"):
        certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
        )


def test_fast_common_transition_differential_mode_and_counters() -> None:
    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cursor = _fallback_cursor(runtime)

    with activate_m8_profile() as profiler:
        fact = certificates._derive_m8_common_transition_fact_unprofiled(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
            differential=True,
        )

    counts = profiler.report().counts
    assert fact.event_position == 1
    assert counts["frontier_rejected_transitions"] == 1
    assert counts["standard_only_materializations"] == 1
    assert counts["full_authoritative_fallbacks"] == 0
    assert counts["differential_mismatches"] == 0


def test_prepared_fast_common_reuses_compiled_standard_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline import replay
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cursor = _fallback_cursor(runtime)
    original = replay._build_standard_profile  # noqa: SLF001
    call_count = 0

    def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(replay, "_build_standard_profile", counted)

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        preparation_calls = call_count
        fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
            prepared_layouts=prepared,
        )

        assert fast is not None
        assert preparation_calls > 0
        assert call_count == preparation_calls


def test_prepared_mixed_common_reuses_profiles_and_remains_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline import replay
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    survivor = _fallback_cursor(runtime).inventory[0]
    rejected = inventory_item(
        Polygon(((0, 0), (1, 0), (1, 1), (0, 1))),
        material=runtime.replay_input.instances[1].material,
        token="prepared-mixed-rejected",
    )
    cursor = replace(
        _fallback_cursor(runtime),
        inventory=tuple(
            sorted((rejected, survivor), key=lambda item: item.remnant.remnant_id)
        ),
    )
    semantic_sha256 = m7_semantic_runtime_sha256(runtime)
    expected = certificates._derive_m8_common_transition_fact_authoritative(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_sha256,
    )
    runtime.standard_profile_cache.clear()
    original = replay._build_standard_profile  # noqa: SLF001
    call_count = 0

    def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(replay, "_build_standard_profile", counted)
    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        preparation_calls = call_count
        fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_sha256,
            prepared_layouts=prepared,
        )

        assert fast is not None
        assert fast.fact == expected
        assert fast.zero_generation_rejected_inventory == (rejected,)
        assert fast.exact_survivor_inventory == (survivor,)
        assert preparation_calls > 0
        assert call_count == preparation_calls


def test_mixed_common_rejects_a_conflicting_cached_search() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    survivor = _fallback_cursor(runtime).inventory[0]
    rejected = inventory_item(
        Polygon(((0, 0), (1, 0), (1, 1), (0, 1))),
        material=runtime.replay_input.instances[1].material,
        token="mixed-conflicting-cache",
    )
    cursor = replace(
        _fallback_cursor(runtime),
        inventory=tuple(
            sorted((rejected, survivor), key=lambda item: item.remnant.remnant_id)
        ),
    )
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    exact_runtime = certificates._fresh_runtime(runtime)  # noqa: SLF001
    enumerate_m7_action_catalog(exact_runtime, cursor=cursor, complete=False)
    survivor_key = (
        survivor.remnant.remnant_id,
        binding.problem_id,
        verified.evidence.candidate_set_id,
    )
    survivor_searches = exact_runtime.fit_search_cache[survivor_key]
    assert any(search.generated_candidate_count > 0 for search in survivor_searches)
    rejected_key = (
        rejected.remnant.remnant_id,
        binding.problem_id,
        verified.evidence.candidate_set_id,
    )
    runtime.fit_search_cache[rejected_key] = tuple(
        search.model_copy(update={"remnant_id": rejected.remnant.remnant_id})
        for search in survivor_searches
    )

    with pytest.raises(
        ValueError,
        match="local fit-search cache value differs from zero-generation rejection",
    ):
        certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
        )


def test_survivor_increments_full_authoritative_fallback_counter() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = _fallback_cursor(runtime)

    with activate_m8_profile() as profiler:
        certificates._derive_m8_common_transition_fact_unprofiled(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
        )

    counts = profiler.report().counts
    assert counts["frontier_rejected_transitions"] == 0
    assert counts["standard_only_materializations"] == 0
    assert counts["full_authoritative_fallbacks"] == 1
    assert counts["exact_survivor_inventory_items"] == len(cursor.inventory)
    assert counts["differential_mismatches"] == 0
