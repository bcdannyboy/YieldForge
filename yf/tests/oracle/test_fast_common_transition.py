from __future__ import annotations

from dataclasses import replace

import pytest
from shapely import Polygon

from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
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


def test_area_only_rejection_falls_back_when_search_counts_are_not_scalar_derived() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    material = runtime.replay_input.instances[1].material
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=material,
        token="area-only-fast-common",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))

    fast = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )

    assert fast is None


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
    assert counts["differential_mismatches"] == 0
