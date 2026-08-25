from __future__ import annotations

from dataclasses import replace

import pytest
from shapely import Polygon, box

from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    M7ReplayCursor,
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    enumerate_m7_single_remnant_competitor,
    initial_m7_cursor,
    select_m7_fallback,
)
from yieldforge.oracle.certificates import (
    BranchInventoryDelta,
    certify_event_passivity,
)
from yieldforge.oracle.compiled import compile_translation_rejections
from yieldforge.reuse.contracts import MaterialIdentity
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest


def _sha(index: int) -> str:
    return f"sha256:{index:064x}"


def _common_step(runtime, *, cursor: M7ReplayCursor | None = None):  # type: ignore[no-untyped-def]
    current = cursor or initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=current)
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selected.action_id)
    step = apply_m7_action_descriptor(
        runtime,
        cursor=current,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selected.decision_key,
    )
    return current, step


def _certify(runtime, item, *, cursor=None, common_step=None):  # type: ignore[no-untyped-def]
    current, step = (
        _common_step(runtime, cursor=cursor)
        if common_step is None
        else (cursor, common_step)
    )
    assert current is not None
    return certify_event_passivity(
        runtime,
        cursor_template=current,
        event_position=current.next_event_position,
        common=step.action_binding,
        delta=BranchInventoryDelta(added=(item,), removed=()),
        common_action_id=step.action_binding.materialized_action_id,
        branch_action_id=step.action_binding.materialized_action_id,
        state_before_sha256=_sha(1),
        state_after_sha256=_sha(2),
    )


@pytest.mark.parametrize(
    ("case", "polygon", "expected_reason"),
    [
        (
            "area",
            Polygon([(0, 0), (6, 0), (6, 1), (1, 1), (1, 10), (0, 10)]),
            "footprint_area_exceeds_remnant",
        ),
        ("width", box(0, 0, 3, 20), "footprint_width_exceeds_remnant"),
    ],
)
def test_cheap_geometry_rejections_certify_added_remnant_without_search(
    case: str,
    polygon: Polygon,
    expected_reason: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    item = inventory_item(
        polygon,
        material=runtime.replay_input.instances[0].material,
        token=case,
    )

    rejections = compile_translation_rejections(runtime, event_position=0, item=item)
    result = _certify(runtime, item)

    assert {entry.certificate.reason for entry in rejections} == {expected_reason}
    assert result.passive
    assert result.witness is not None
    assert result.witness.classification == "no_fit"
    assert result.exact_search_count == 0


def test_material_rejection_certifies_added_remnant_without_search() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    source = runtime.replay_input.instances[0].material
    incompatible = MaterialIdentity(
        **(source.model_dump(mode="python") | {"grade": "incompatible-grade"})
    )
    item = inventory_item(box(0, 0, 10, 10), material=incompatible, token="material")

    rejections = compile_translation_rejections(runtime, event_position=0, item=item)
    result = _certify(runtime, item)

    assert {entry.certificate.reason for entry in rejections} == {"material_mismatch"}
    assert result.passive
    assert result.witness is not None
    assert result.witness.classification == "no_fit"
    assert result.exact_search_count == 0


def test_cheap_rejection_fails_closed_on_tampered_prepared_layout_identity() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="tampered-layout-cache",
    )
    compile_translation_rejections(runtime, event_position=0, item=item)
    cache_key = next(iter(runtime.prepared_layout_cache))
    runtime.prepared_layout_cache[cache_key] = tuple(
        reversed(runtime.prepared_layout_cache[cache_key])
    )

    with pytest.raises(ValueError, match="candidate identities"):
        compile_translation_rejections(runtime, event_position=0, item=item)


def test_registered_search_no_fit_is_authoritative_after_cheap_bounds_survive() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    concave = Polygon([(0, 0), (6, 0), (6, 5), (2, 5), (2, 10), (0, 10)])
    item = inventory_item(
        concave,
        material=runtime.replay_input.instances[0].material,
        token="concave-no-fit",
    )

    assert not all(
        entry.certificate.impossible
        for entry in compile_translation_rejections(runtime, event_position=0, item=item)
    )
    result = _certify(runtime, item)

    assert result.passive
    assert result.witness is not None
    assert result.witness.classification == "no_fit"
    assert result.exact_search_count == 1


def test_exact_no_fit_fails_closed_on_tampered_cached_search_identity() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    concave = Polygon([(0, 0), (6, 0), (6, 5), (2, 5), (2, 10), (0, 10)])
    item = inventory_item(
        concave,
        material=runtime.replay_input.instances[0].material,
        token="tampered-search-cache",
    )
    assert _certify(runtime, item).passive
    cache_key = next(iter(runtime.fit_search_cache))
    searches = runtime.fit_search_cache[cache_key]
    runtime.fit_search_cache[cache_key] = (
        searches[0].model_copy(update={"candidate_id": "tampered-candidate"}),
        *searches[1:],
    )

    with pytest.raises(ValueError, match="search identities"):
        _certify(runtime, item)


def test_feasible_branch_remnant_is_passive_when_common_policy_rank_wins() -> None:
    rates = FeasibilityRateManifest(
        purchase_cost_per_area=1.0,
        storage_cost_per_area_hour=0.0,
        return_handling_cost_per_remnant=0.0,
        retrieval_handling_cost_per_remnant=200.0,
        scrap_credit_per_area=0.0,
    )
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=rates,
    )
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="dominated",
    )

    result = _certify(runtime, item)

    assert result.passive
    assert result.witness is not None
    assert result.witness.classification == "policy_dominated"
    influence = result.witness.influences[0]
    assert influence.common_action_id != influence.common_catalog_action_id
    assert influence.competing_action_id == influence.competing_catalog_action_id
    assert result.exact_search_count == 1


def test_branch_remnant_that_beats_common_winner_is_not_certified() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="winner",
    )

    result = _certify(runtime, item)

    assert not result.passive
    assert result.witness is None
    assert result.exact_search_count == 1


def test_exact_stable_stock_tail_can_dominate_an_otherwise_tied_remnant() -> None:
    zero_rates = FeasibilityRateManifest(
        purchase_cost_per_area=0.0,
        storage_cost_per_area_hour=0.0,
        return_handling_cost_per_remnant=0.0,
        retrieval_handling_cost_per_remnant=0.0,
        scrap_credit_per_area=0.0,
    )
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=zero_rates,
    )
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="exact-tie",
    )

    result = _certify(runtime, item)

    assert result.passive
    assert result.witness is not None
    influence = result.witness.influences[0]
    assert influence.common_decision_key[0] == influence.competing_decision_key[0]
    assert "selected_stock_id=current_standard_sheet" in influence.common_decision_key
    assert f"selected_stock_id={item.remnant.remnant_id}" in influence.competing_decision_key


def test_single_remnant_helper_never_substitutes_the_standard_policy_winner() -> None:
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.MYOPIC_GEOMETRY,
    )
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="myopic-competitor",
    )
    cursor = initial_m7_cursor(runtime.replay_input)

    descriptor, context = enumerate_m7_single_remnant_competitor(
        runtime,
        event_position=0,
        item=item,
        cursor_template=cursor,
    )

    assert descriptor is not None
    assert context is not None
    assert descriptor.kind.value == "consume_remnant"
    assert descriptor.selected_remnant_id == item.remnant.remnant_id
    assert context.action_id == descriptor.action_id


def test_removed_common_winner_is_unresolved_before_any_search() -> None:
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.REMNANT_FIRST,
    )
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="removed-winner",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(item,))
    _, step = _common_step(runtime, cursor=cursor)

    result = certify_event_passivity(
        runtime,
        cursor_template=cursor,
        event_position=0,
        common=step.action_binding,
        delta=BranchInventoryDelta(added=(), removed=(item,)),
        common_action_id=step.action_binding.materialized_action_id,
        branch_action_id=step.action_binding.materialized_action_id,
        state_before_sha256=_sha(1),
        state_after_sha256=_sha(2),
    )

    assert step.action_binding.context.selected_stock_id == item.remnant.remnant_id
    assert not result.passive
    assert result.witness is None
    assert result.exact_search_count == 0


def test_removed_nonwinner_is_certified_by_exact_same_policy_dominance() -> None:
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.REMNANT_FIRST,
    )
    material = runtime.replay_input.instances[0].material
    items = tuple(
        sorted(
            (
                inventory_item(box(0, 0, 4, 10), material=material, token="remove-a"),
                inventory_item(box(0, 0, 4, 10), material=material, token="remove-b"),
            ),
            key=lambda item: item.remnant.remnant_id,
        )
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=items)
    _, step = _common_step(runtime, cursor=cursor)
    removed = next(
        item
        for item in items
        if item.remnant.remnant_id != step.action_binding.context.selected_stock_id
    )

    result = certify_event_passivity(
        runtime,
        cursor_template=cursor,
        event_position=0,
        common=step.action_binding,
        delta=BranchInventoryDelta(added=(), removed=(removed,)),
        common_action_id=step.action_binding.materialized_action_id,
        branch_action_id=step.action_binding.materialized_action_id,
        state_before_sha256=_sha(1),
        state_after_sha256=_sha(2),
    )

    assert result.passive
    assert result.witness is not None
    assert result.witness.classification == "policy_dominated"
    assert result.witness.influences[0].competing_action_id == (
        result.witness.influences[0].competing_catalog_action_id
    )


def test_event_can_mix_no_fit_and_policy_dominated_influences() -> None:
    rates = FeasibilityRateManifest(
        purchase_cost_per_area=1.0,
        storage_cost_per_area_hour=0.0,
        return_handling_cost_per_remnant=0.0,
        retrieval_handling_cost_per_remnant=200.0,
        scrap_credit_per_area=0.0,
    )
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=rates,
    )
    material = runtime.replay_input.instances[0].material
    items = tuple(
        sorted(
            (
                inventory_item(box(0, 0, 3, 20), material=material, token="mixed-no-fit"),
                inventory_item(box(0, 0, 4, 10), material=material, token="mixed-dominated"),
            ),
            key=lambda item: item.remnant.remnant_id,
        )
    )
    cursor, step = _common_step(runtime)

    result = certify_event_passivity(
        runtime,
        cursor_template=cursor,
        event_position=0,
        common=step.action_binding,
        delta=BranchInventoryDelta(added=items, removed=()),
        common_action_id=step.action_binding.materialized_action_id,
        branch_action_id=step.action_binding.materialized_action_id,
        state_before_sha256=_sha(1),
        state_after_sha256=_sha(2),
    )

    assert result.passive
    assert result.witness is not None
    assert result.witness.classification == "policy_dominated"
    assert {item.classification for item in result.witness.influences} == {
        "no_fit",
        "policy_dominated",
    }


def test_branch_delta_requires_sorted_unique_disjoint_remnant_identities() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    material = runtime.replay_input.instances[0].material
    items = tuple(
        sorted(
            (
                inventory_item(box(0, 0, 3, 20), material=material, token="delta-a"),
                inventory_item(box(0, 0, 4, 10), material=material, token="delta-b"),
            ),
            key=lambda item: item.remnant.remnant_id,
        )
    )

    with pytest.raises(ValueError, match="sorted unique"):
        BranchInventoryDelta(added=tuple(reversed(items)), removed=())
    with pytest.raises(ValueError, match="sorted unique"):
        BranchInventoryDelta(added=(items[0], items[0]), removed=())
    with pytest.raises(ValueError, match="disjoint"):
        BranchInventoryDelta(added=(items[0],), removed=(items[0],))
