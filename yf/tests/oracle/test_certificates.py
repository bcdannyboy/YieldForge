from __future__ import annotations

import base64
import gc
import os
import pickle
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from inspect import signature

import pytest
from shapely import Polygon, box

from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.baseline.archives import VerifiedProblemCandidates
from yieldforge.baseline.contracts import LayoutFitSearchResult, LayoutFitSearchStatus
from yieldforge.baseline.policies import (
    M7PolicyName,
    rank_policy_action,
    select_policy_action,
)
from yieldforge.baseline.replay import (
    M7ReplayCursor,
    apply_m7_action_descriptor,
    apply_m7_frozen_action_evidence,
    enumerate_m7_action_catalog,
    enumerate_m7_single_remnant_competitor,
    initial_m7_cursor,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    select_m7_fallback,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import certificates as certificate_module
from yieldforge.oracle import compiled as compiled_module
from yieldforge.oracle.certificates import (
    BranchInventoryDelta,
    ValidatedCommonTransition,
    build_m8_common_transition_fact,
    build_validated_m8_common_transition,
    certify_event_passivity,
    validate_m8_common_transition_fact,
)
from yieldforge.oracle.compiled import compile_translation_rejections
from yieldforge.replay.contracts import InventoryItem
from yieldforge.reuse.contracts import MaterialIdentity
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest


def _sha(index: int) -> str:
    return f"sha256:{index:064x}"


def _as_no_fit(search: LayoutFitSearchResult) -> LayoutFitSearchResult:
    return LayoutFitSearchResult(
        status=LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH,
        candidate_id=search.candidate_id,
        remnant_id=search.remnant_id,
        config=search.config,
        generated_candidate_count=search.generated_candidate_count,
        duplicate_candidate_count=search.duplicate_candidate_count,
        evaluated_candidate_count=search.evaluated_candidate_count,
        budget_truncated=search.budget_truncated,
        translation=None,
    )


def _as_fit(search: LayoutFitSearchResult) -> LayoutFitSearchResult:
    return LayoutFitSearchResult(
        status=LayoutFitSearchStatus.FIT,
        candidate_id=search.candidate_id,
        remnant_id=search.remnant_id,
        config=search.config,
        generated_candidate_count=max(1, search.generated_candidate_count),
        duplicate_candidate_count=search.duplicate_candidate_count,
        evaluated_candidate_count=max(1, search.evaluated_candidate_count),
        budget_truncated=search.budget_truncated,
        translation=(0.0, 0.0),
    )


def _common_fact(runtime, *, cursor: M7ReplayCursor | None = None):  # type: ignore[no-untyped-def]
    current = cursor or initial_m7_cursor(runtime.replay_input)
    return current, build_validated_m8_common_transition(runtime, cursor=current)


def _branch_cursor(
    common: M7ReplayCursor,
    *,
    added=(),  # type: ignore[no-untyped-def]
    removed=(),  # type: ignore[no-untyped-def]
) -> M7ReplayCursor:
    removed_ids = {item.remnant.remnant_id for item in removed}
    inventory = tuple(
        sorted(
            (
                *(item for item in common.inventory if item.remnant.remnant_id not in removed_ids),
                *added,
            ),
            key=lambda item: item.remnant.remnant_id,
        )
    )
    return replace(common, inventory=inventory)


def _certify(runtime, item, *, cursor=None, common_fact=None):  # type: ignore[no-untyped-def]
    current, fact = (
        _common_fact(runtime, cursor=cursor)
        if common_fact is None
        else (cursor, common_fact)
    )
    assert current is not None
    return certify_event_passivity(
        runtime,
        common=fact,
        branch_cursor=_branch_cursor(current, added=(item,)),
    )


def _forge_and_rehash_common_context(runtime, fact):  # type: ignore[no-untyped-def]
    context = replace(fact.step.selected_context, immediate_net_cost=-1e9)
    binding = replace(fact.step.action_binding, context=context)
    rank = rank_policy_action(runtime.replay_input.policy.name, context)
    provisional_event = fact.step.event.model_copy(
        update={
            "event_id": "yfm7e-" + "0" * 24,
            "policy_decision_key": rank.decision_key,
        }
    )
    event_digest = semantic_sha256(provisional_event, excluded_fields={"event_id"})
    event = provisional_event.model_copy(update={"event_id": f"yfm7e-{event_digest[:24]}"})
    step = replace(
        fact.step,
        selected_context=context,
        action_binding=binding,
        event=event,
    )
    forged = replace(
        fact,
        step=step,
        event_id=event.event_id,
        policy_rank=rank,
    )
    payload = certificate_module._common_fact_payload(  # noqa: SLF001
        runtime,
        event_position=forged.event_position,
        cursor_before=forged.cursor_before,
        step=forged.step,
        policy_rank=forged.policy_rank,
        semantic_runtime_sha256=forged.semantic_runtime_sha256,
    )
    return replace(forged, content_sha256=f"sha256:{semantic_sha256(payload)}")


def _strict_primary_rules(runtime):  # type: ignore[no-untyped-def]
    primary = runtime.rules.primary.model_copy(
        update={
            "minimum_area_sheet_fraction": 1.0,
            "minimum_effective_width_short_side_fraction": 1.0,
            "minimum_exterior_access_short_side_fraction": 1.0,
        }
    )
    return runtime.rules.model_copy(update={"primary": primary})


def _write_collision_jagua(path, *, collision: bool) -> None:  # type: ignore[no-untyped-def]
    collision_literal = repr(collision)
    path.write_text(
        "#!/bin/sh\n"
        "python3 -c 'import json,sys; request=json.load(sys.stdin); "
        'print(json.dumps({"schema_version":"yieldforge.m7-jagua-search-response.v1",'
        '"backend":"jagua-rs","backend_version":"0.7.0",'
        '"coordinate_precision":"f32","build_microseconds":1,'
        '"generation_microseconds":2,"query_microseconds":3,'
        '"searches":[{"layout_id":layout["layout_id"],'
        '"generated_candidate_count":1,"duplicate_candidate_count":0,'
        '"budget_truncated":False,"translations":[[0.0,0.0]],'
        '"collisions":['
        + collision_literal
        + ']} for layout in request["layouts"]]}))\'\n'
    )
    path.chmod(0o700)


def _private_jagua_path(common):  # type: ignore[no-untyped-def]
    registered = certificate_module._VALIDATED_COMMON_REGISTRY[id(common)]  # noqa: SLF001
    path = registered[5].runtime.jagua_executable
    assert path is not None
    return path


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


def test_private_prepared_layout_batch_constructs_each_layout_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="prepared-layout-batch",
    )
    original = compiled_module.prepare_layout_footprint
    constructed: list[str] = []

    def counted(problem, candidate, config):  # type: ignore[no-untyped-def]
        constructed.append(candidate.candidate_id)
        return original(problem, candidate, config)

    monkeypatch.setattr(compiled_module, "prepare_layout_footprint", counted)
    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(0,),
    ) as prepared:
        first = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
            runtime,
            prepared=prepared,
            event_position=0,
            item=item,
        )
        second = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
            runtime,
            prepared=prepared,
            event_position=0,
            item=item,
        )

    problem_id = runtime.replay_input.instances[0].problem_id
    expected = runtime.runtime_candidates[problem_id].evidence.candidate_ids
    assert tuple(constructed) == expected
    assert first == second


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


@pytest.mark.parametrize(
    "mutation",
    ["geometry", "part_polygons", "vertices", "bounds"],
)
def test_cheap_rejection_fails_closed_on_same_id_different_layout_value(
    mutation: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token=f"tampered-layout-{mutation}",
    )
    compile_translation_rejections(runtime, event_position=0, item=item)
    cache_key = next(iter(runtime.prepared_layout_cache))
    if mutation == "geometry":
        altered = tuple(
            replace(layout, geometry=box(0, 0, 20, 20))
            for layout in runtime.prepared_layout_cache[cache_key]
        )
    elif mutation == "part_polygons":
        altered = tuple(
            replace(layout, part_polygons=(box(0, 0, 20, 20),))
            for layout in runtime.prepared_layout_cache[cache_key]
        )
    elif mutation == "vertices":
        altered = tuple(
            replace(
                layout,
                vertices=((0.0, 0.0), (0.0, 20.0), (20.0, 0.0), (20.0, 20.0)),
            )
            for layout in runtime.prepared_layout_cache[cache_key]
        )
    else:
        altered = tuple(
            replace(layout, bounds=(0.0, 0.0, 20.0, 20.0))
            for layout in runtime.prepared_layout_cache[cache_key]
        )
    runtime.prepared_layout_cache[cache_key] = altered

    with pytest.raises(ValueError, match="layout cache value"):
        _certify(runtime, item)


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
    descriptor, context = enumerate_m7_single_remnant_competitor(
        runtime,
        event_position=0,
        item=item,
        cursor_template=initial_m7_cursor(runtime.replay_input),
    )
    assert descriptor is None and context is None
    assert _certify(runtime, item).passive
    cache_key = next(iter(runtime.fit_search_cache))
    searches = runtime.fit_search_cache[cache_key]
    runtime.fit_search_cache[cache_key] = (
        searches[0].model_copy(update={"candidate_id": "tampered-candidate"}),
        *searches[1:],
    )

    with pytest.raises(ValueError, match="search identities"):
        _certify(runtime, item)


def test_exact_no_fit_fails_closed_on_local_no_fit_to_fit_substitution() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    concave = Polygon([(0, 0), (6, 0), (6, 5), (2, 5), (2, 10), (0, 10)])
    item = inventory_item(
        concave,
        material=runtime.replay_input.instances[0].material,
        token="local-no-fit-to-fit",
    )
    descriptor, context = enumerate_m7_single_remnant_competitor(
        runtime,
        event_position=0,
        item=item,
        cursor_template=initial_m7_cursor(runtime.replay_input),
    )
    assert descriptor is None and context is None
    cache_key = next(iter(runtime.fit_search_cache))
    runtime.fit_search_cache[cache_key] = tuple(
        _as_fit(search) for search in runtime.fit_search_cache[cache_key]
    )

    with pytest.raises(ValueError, match="local fit-search cache value"):
        _certify(runtime, item)


def test_exact_competitor_fails_closed_on_local_fit_to_no_fit_substitution() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="local-fit-to-no-fit",
    )
    descriptor, context = enumerate_m7_single_remnant_competitor(
        runtime,
        event_position=0,
        item=item,
        cursor_template=initial_m7_cursor(runtime.replay_input),
    )
    assert descriptor is not None and context is not None
    initial = _certify(runtime, item)
    assert not initial.passive
    cache_key = next(iter(runtime.fit_search_cache))
    runtime.fit_search_cache[cache_key] = tuple(
        _as_no_fit(search) for search in runtime.fit_search_cache[cache_key]
    )

    with pytest.raises(ValueError, match="local fit-search cache value"):
        _certify(runtime, item)


def test_exact_competitor_fails_closed_on_shared_fit_to_no_fit_substitution() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    runtime.shared_fit_search_cache = {}
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="shared-fit-to-no-fit",
    )
    descriptor, context = enumerate_m7_single_remnant_competitor(
        runtime,
        event_position=0,
        item=item,
        cursor_template=initial_m7_cursor(runtime.replay_input),
    )
    assert descriptor is not None and context is not None
    initial = _certify(runtime, item)
    assert not initial.passive
    shared_key = next(iter(runtime.shared_fit_search_cache))
    runtime.shared_fit_search_cache[shared_key] = tuple(
        _as_no_fit(search) for search in runtime.shared_fit_search_cache[shared_key]
    )
    runtime.fit_search_cache.clear()

    with pytest.raises(ValueError, match="shared fit-search cache value"):
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


@pytest.mark.parametrize("policy", tuple(M7PolicyName))
def test_single_remnant_helper_matches_complete_catalog_policy_winner(
    policy: M7PolicyName,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0, policy=policy)
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token=f"differential-{policy.value}",
    )
    common_cursor = initial_m7_cursor(runtime.replay_input)

    descriptor, context = enumerate_m7_single_remnant_competitor(
        runtime,
        event_position=0,
        item=item,
        cursor_template=common_cursor,
    )
    branch_cursor = replace(common_cursor, inventory=(item,))
    complete = enumerate_m7_action_catalog(runtime, cursor=branch_cursor, complete=True)
    remnant_contexts = tuple(
        candidate
        for candidate in complete.contexts
        if candidate.selected_stock_id == item.remnant.remnant_id
    )
    expected = select_policy_action(policy, remnant_contexts)
    expected_descriptor = next(
        candidate for candidate in complete.actions if candidate.action_id == expected.action_id
    )
    expected_context = next(
        candidate for candidate in remnant_contexts if candidate.action_id == expected.action_id
    )

    assert descriptor == expected_descriptor
    assert context == expected_context
    assert expected.decision_key[-3:] == (
        f"candidate_id={expected_context.candidate_id}",
        f"selected_stock_id={item.remnant.remnant_id}",
        f"action_id={expected_context.action_id}",
    )


def test_common_transition_fact_rejects_tampered_numeric_context() -> None:
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
    cursor, common = _common_fact(runtime)
    fact = common.fact
    context = replace(fact.step.selected_context, immediate_net_cost=-1e9)
    binding = replace(fact.step.action_binding, context=context)
    forged = replace(
        fact,
        step=replace(
            fact.step,
            selected_context=context,
            action_binding=binding,
        ),
    )

    with pytest.raises(ValueError, match="common transition fact"):
        validate_m8_common_transition_fact(
            runtime,
            cursor=cursor,
            fact=forged,
        )


def test_certifier_rejects_raw_portable_common_fact() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    portable = build_m8_common_transition_fact(runtime, cursor=cursor)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="raw-fact-boundary",
    )

    with pytest.raises(ValueError, match="validated common transition capability"):
        certify_event_passivity(
            runtime,
            common=portable,  # type: ignore[arg-type]
            branch_cursor=_branch_cursor(cursor, added=(item,)),
        )


def test_authoritative_revalidation_rejects_forged_rehashed_portable_fact() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    portable = build_m8_common_transition_fact(runtime, cursor=cursor)
    forged = _forge_and_rehash_common_context(runtime, portable)

    with pytest.raises(ValueError, match="authoritative common transition"):
        validate_m8_common_transition_fact(
            runtime,
            cursor=cursor,
            fact=forged,
        )


def test_copied_or_manually_constructed_validated_capability_fails_closed() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, common = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="copied-capability",
    )
    branch = _branch_cursor(cursor, added=(item,))
    copied = replace(common)
    manually_constructed = ValidatedCommonTransition(
        fact=common.fact,
        _provenance_token=common._provenance_token,
    )

    assert certify_event_passivity(runtime, common=common, branch_cursor=branch).passive
    for invalid in (copied, manually_constructed):
        with pytest.raises(ValueError, match="validated common transition capability"):
            certify_event_passivity(runtime, common=invalid, branch_cursor=branch)


def test_certificate_fails_closed_on_runtime_mutation_after_capability_entry(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, common = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="post-validation-interleave",
    )
    branch = _branch_cursor(cursor, added=(item,))
    original_delta = certificate_module._derive_branch_inventory_delta  # noqa: SLF001

    def mutate_after_validation(common_cursor, branch_cursor):  # type: ignore[no-untyped-def]
        runtime.rules = _strict_primary_rules(runtime)
        return original_delta(common_cursor, branch_cursor)

    monkeypatch.setattr(
        certificate_module,
        "_derive_branch_inventory_delta",
        mutate_after_validation,
    )

    with pytest.raises(ValueError, match="changed during M8 certificate operation"):
        certify_event_passivity(runtime, common=common, branch_cursor=branch)


def test_transient_runtime_mutation_after_capability_entry_uses_snapshot(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, common = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="transient-post-validation-interleave",
    )
    branch = _branch_cursor(cursor, added=(item,))
    expected = certify_event_passivity(runtime, common=common, branch_cursor=branch)
    original_influence = certificate_module._influence  # noqa: SLF001
    original_rules = runtime.rules

    def mutate_then_restore(runtime_argument, **kwargs):  # type: ignore[no-untyped-def]
        assert runtime_argument is not runtime
        runtime.rules = _strict_primary_rules(runtime)
        try:
            return original_influence(runtime_argument, **kwargs)
        finally:
            runtime.rules = original_rules

    monkeypatch.setattr(certificate_module, "_influence", mutate_then_restore)

    assert certify_event_passivity(runtime, common=common, branch_cursor=branch) == expected


def test_transient_runtime_mutation_during_derivation_cannot_stamp_mixed_fact(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    expected = build_m8_common_transition_fact(runtime, cursor=cursor)
    original_rules = runtime.rules
    original_fresh_runtime = certificate_module._fresh_runtime  # noqa: SLF001

    def mutate_then_restore(runtime_argument):  # type: ignore[no-untyped-def]
        with pytest.raises(AttributeError, match="dependency bindings are immutable"):
            runtime_argument.rules = _strict_primary_rules(runtime)
        runtime.rules = _strict_primary_rules(runtime)
        try:
            return original_fresh_runtime(runtime_argument)
        finally:
            runtime.rules = original_rules

    monkeypatch.setattr(certificate_module, "_fresh_runtime", mutate_then_restore)

    common = build_validated_m8_common_transition(runtime, cursor=cursor)

    assert common.step == expected.step
    assert common.fact == expected


def test_common_transition_fact_rejects_tampered_winner_search_cache() -> None:
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.REMNANT_FIRST,
    )
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="common-cache-winner",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(item,))
    enumerate_m7_action_catalog(runtime, cursor=cursor, complete=False)
    cache_key = next(iter(runtime.fit_search_cache))
    runtime.fit_search_cache[cache_key] = tuple(
        _as_no_fit(search) for search in runtime.fit_search_cache[cache_key]
    )

    with pytest.raises(ValueError, match="local fit-search cache value"):
        build_m8_common_transition_fact(runtime, cursor=cursor)


def test_common_transition_fact_rejects_residual_rule_runtime_drift() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, fact = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="runtime-rule-drift",
    )
    primary = runtime.rules.primary.model_copy(
        update={
            "minimum_area_sheet_fraction": 1.0,
            "minimum_effective_width_short_side_fraction": 1.0,
            "minimum_exterior_access_short_side_fraction": 1.0,
        }
    )
    runtime.rules = runtime.rules.model_copy(update={"primary": primary})
    independently_recomputed = build_m8_common_transition_fact(runtime, cursor=cursor)

    assert independently_recomputed.step.event.action.action_id != (
        fact.step.event.action.action_id
    )
    with pytest.raises(ValueError, match="semantic runtime fingerprint"):
        certify_event_passivity(
            runtime,
            common=fact,
            branch_cursor=_branch_cursor(cursor, added=(item,)),
        )


def test_common_transition_fact_rejects_concrete_candidate_runtime_drift() -> None:
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.MYOPIC_GEOMETRY,
    )
    cursor, fact = _common_fact(runtime)
    problem_id = runtime.replay_input.instances[0].problem_id
    verified = runtime.runtime_candidates[problem_id]
    candidates = tuple(
        candidate.model_copy(update={"width": 1.0})
        if candidate.candidate_id == "candidate-two"
        else candidate
        for candidate in verified.candidates
    )
    runtime.runtime_candidates[problem_id] = VerifiedProblemCandidates(
        evidence=verified.evidence,
        candidates=candidates,
    )
    independently_recomputed = build_m8_common_transition_fact(runtime, cursor=cursor)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="runtime-candidate-drift",
    )

    assert fact.step.descriptor.action_id == "m7-standard:candidate-one"
    assert independently_recomputed.step.descriptor.action_id == "m7-standard:candidate-two"
    with pytest.raises(ValueError, match="semantic runtime fingerprint"):
        certify_event_passivity(
            runtime,
            common=fact,
            branch_cursor=_branch_cursor(cursor, added=(item,)),
        )


def test_common_transition_fact_rejects_outcome_flag_runtime_drift() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, fact = _common_fact(runtime)
    runtime.jagua_differential_audit = True
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="runtime-flag-drift",
    )

    with pytest.raises(ValueError, match="semantic runtime fingerprint"):
        certify_event_passivity(
            runtime,
            common=fact,
            branch_cursor=_branch_cursor(cursor, added=(item,)),
        )


def test_common_transition_fact_binds_jagua_executable_content(tmp_path) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "fake-jagua"
    _write_collision_jagua(executable, collision=False)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
    )
    cursor, fact = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="runtime-executable-drift",
    )
    executable.write_text("#!/bin/sh\nexit 1\n")
    executable.chmod(0o700)

    assert m7_semantic_runtime_sha256(runtime) != fact.semantic_runtime_sha256
    with pytest.raises(ValueError, match="semantic runtime fingerprint"):
        certify_event_passivity(
            runtime,
            common=fact,
            branch_cursor=_branch_cursor(cursor, added=(item,)),
        )


@pytest.mark.parametrize("tamper", ["replace_bytes", "delete", "symlink"])
def test_certificate_revalidates_private_jagua_executable(
    tmp_path,
    tamper: str,
) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "source-jagua"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
    )
    cursor, common = _common_fact(runtime)
    private = _private_jagua_path(common)
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token=f"private-jagua-{tamper}",
    )
    if tamper == "replace_bytes":
        private.chmod(0o700)
        private.write_text("#!/bin/sh\nexit 1\n")
        private.chmod(0o500)
    elif tamper == "delete":
        private.unlink()
    else:
        private.unlink()
        private.symlink_to(executable)

    with pytest.raises(ValueError, match="private Jagua executable"):
        certify_event_passivity(
            runtime,
            common=common,
            branch_cursor=_branch_cursor(cursor, added=(item,)),
        )


def test_certificate_revalidates_and_cleans_fresh_jagua_lease(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "source-jagua"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
    )
    cursor, common = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="private-jagua-post-invocation",
    )
    original_influence = certificate_module._influence  # noqa: SLF001
    lease_paths = []

    def tamper_after_influence(proof_runtime, **kwargs):  # type: ignore[no-untyped-def]
        result = original_influence(proof_runtime, **kwargs)
        lease = proof_runtime.jagua_executable
        assert lease is not None
        lease_paths.append(lease)
        lease.chmod(0o700)
        lease.write_text("#!/bin/sh\nexit 1\n")
        lease.chmod(0o500)
        return result

    monkeypatch.setattr(certificate_module, "_influence", tamper_after_influence)

    with pytest.raises(ValueError, match="private Jagua executable"):
        certify_event_passivity(
            runtime,
            common=common,
            branch_cursor=_branch_cursor(cursor, added=(item,)),
        )
    assert len(lease_paths) == 1
    assert not lease_paths[0].exists()


def test_transient_fresh_jagua_lease_swap_cannot_override_shapely_fit(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "source-jagua"
    _write_collision_jagua(executable, collision=False)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
    )
    cursor, common = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="transient-private-jagua-swap",
    )
    original_influence = certificate_module._influence  # noqa: SLF001
    proof_audit_flags = []

    def swap_then_restore(proof_runtime, **kwargs):  # type: ignore[no-untyped-def]
        lease = proof_runtime.jagua_executable
        assert lease is not None
        original_content = lease.read_bytes()
        proof_audit_flags.append(proof_runtime.jagua_differential_audit)
        lease.chmod(0o700)
        _write_collision_jagua(lease, collision=True)
        try:
            return original_influence(proof_runtime, **kwargs)
        finally:
            lease.write_bytes(original_content)
            lease.chmod(0o500)

    monkeypatch.setattr(certificate_module, "_influence", swap_then_restore)

    result = certify_event_passivity(
        runtime,
        common=common,
        branch_cursor=_branch_cursor(cursor, added=(item,)),
    )

    assert not result.passive
    assert proof_audit_flags == [True]
    assert runtime.jagua_differential_audit is False


def test_all_collision_jagua_cannot_override_authoritative_shapely_fit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "all-collision-jagua"
    _write_collision_jagua(executable, collision=True)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
    )
    cursor, common = _common_fact(runtime)
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="all-collision-vs-shapely-fit",
    )

    result = certify_event_passivity(
        runtime,
        common=common,
        branch_cursor=_branch_cursor(cursor, added=(item,)),
    )

    assert not result.passive
    assert runtime.jagua_differential_audit is False


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork lifecycle semantics")
def test_forked_child_cannot_cleanup_and_replace_parent_snapshot_jagua(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "source-jagua"
    _write_collision_jagua(executable, collision=False)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
    )
    cursor, common = _common_fact(runtime)
    private = _private_jagua_path(common)
    private_directory = private.parent
    private_inode = private.stat().st_ino

    child_pid = os.fork()
    if child_pid == 0:
        del common
        gc.collect()
        if not private_directory.exists():
            private_directory.mkdir(mode=0o700)
            _write_collision_jagua(private, collision=True)
        os._exit(0)
    waited_pid, status = os.waitpid(child_pid, 0)
    assert waited_pid == child_pid
    assert os.waitstatus_to_exitcode(status) == 0

    assert private_directory.exists()
    assert private.stat().st_ino == private_inode

    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="forked-jagua-replacement",
    )
    result = certify_event_passivity(
        runtime,
        common=common,
        branch_cursor=_branch_cursor(cursor, added=(item,)),
    )
    assert not result.passive


def test_semantic_runtime_requires_regular_jagua_executable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=tmp_path,
    )

    with pytest.raises(ValueError, match="regular file"):
        m7_semantic_runtime_sha256(runtime)


def test_common_transition_fact_is_deterministic_and_valid_across_spawn_process() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, common = _common_fact(runtime)
    fact = common.fact
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="spawn-fact",
    )
    branch = _branch_cursor(cursor, added=(item,))
    second_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    _, second_common = _common_fact(second_runtime)

    assert fact.content_sha256 == second_common.fact.content_sha256
    assert "_auth_sha256" not in fact.__dataclass_fields__
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(common)
    encoded = base64.b64encode(pickle.dumps((fact, cursor, branch)))
    script = """
import base64
import pickle
import sys
from tests.oracle.fixtures import two_problem_runtime
from yieldforge.oracle.certificates import (
    certify_event_passivity,
    validate_m8_common_transition_fact,
)

fact, cursor, branch = pickle.loads(base64.b64decode(sys.stdin.buffer.read()))
runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
common = validate_m8_common_transition_fact(runtime, cursor=cursor, fact=fact)
print(certify_event_passivity(runtime, common=common, branch_cursor=branch).passive)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        input=encoded,
        capture_output=True,
        check=True,
    )

    assert completed.stdout.strip() == b"True"
    tampered = replace(fact, content_sha256=_sha(97))
    rejected = subprocess.run(
        [sys.executable, "-c", script],
        input=base64.b64encode(pickle.dumps((tampered, cursor, branch))),
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0


def test_common_transition_fact_rejects_nonwinner_action() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, common = _common_fact(runtime)
    fact = common.fact
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    nonwinner = next(item for item in catalog.actions if item.action_id != selected.action_id)
    nonwinner_context = next(
        item for item in catalog.contexts if item.action_id == nonwinner.action_id
    )
    nonwinner_selection = select_policy_action(
        runtime.replay_input.policy.name,
        (nonwinner_context,),
    )
    nonwinner_step = apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=nonwinner,
        decision_key=nonwinner_selection.decision_key,
    )
    forged = replace(fact, step=nonwinner_step)
    with pytest.raises(ValueError, match="common transition fact"):
        validate_m8_common_transition_fact(
            runtime,
            cursor=cursor,
            fact=forged,
        )


@pytest.mark.parametrize("mutation", ["digest", "event", "cursor", "runtime_fingerprint"])
def test_common_transition_fact_rejects_tampered_bound_content(mutation: str) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, common = _common_fact(runtime)
    fact = common.fact
    if mutation == "digest":
        forged = replace(fact, content_sha256=_sha(99))
    elif mutation == "event":
        event = fact.step.event.model_copy(update={"event_id": "yfm7e-" + "0" * 24})
        forged = replace(fact, step=replace(fact.step, event=event))
    elif mutation == "cursor":
        forged = replace(
            fact,
            cursor_before=replace(
                fact.cursor_before,
                current_time=fact.cursor_before.current_time + timedelta(seconds=1),
            ),
        )
    else:
        forged = replace(fact, semantic_runtime_sha256=_sha(98))
    with pytest.raises(ValueError, match="common transition fact|common fact|replay event"):
        validate_m8_common_transition_fact(
            runtime,
            cursor=cursor,
            fact=forged,
        )


@pytest.mark.parametrize("mutation", ["catalog_action_id", "materialized_action_id", "rank"])
def test_common_transition_fact_rejects_tampered_action_identities_and_rank(
    mutation: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, common = _common_fact(runtime)
    fact = common.fact
    if mutation == "catalog_action_id":
        action_id = "m7-standard:forged-candidate"
        context = replace(fact.step.selected_context, action_id=action_id)
        binding = replace(
            fact.step.action_binding,
            catalog_action_id=action_id,
            context=context,
        )
        descriptor = replace(fact.step.descriptor, action_id=action_id)
        forged = replace(
            fact,
            step=replace(
                fact.step,
                descriptor=descriptor,
                selected_context=context,
                action_binding=binding,
            ),
        )
    elif mutation == "materialized_action_id":
        action_id = "yfm7a-" + "0" * 24
        action = fact.step.event.action.model_copy(update={"action_id": action_id})
        event = fact.step.event.model_copy(update={"action": action})
        binding = replace(fact.step.action_binding, materialized_action_id=action_id)
        forged = replace(
            fact,
            step=replace(fact.step, action_binding=binding, event=event),
        )
    else:
        forged = replace(
            fact,
            policy_rank=replace(fact.policy_rank, comparison_key=("forged",)),
        )
    with pytest.raises(ValueError, match="common transition fact|replay event"):
        validate_m8_common_transition_fact(
            runtime,
            cursor=cursor,
            fact=forged,
        )


def test_certifier_derives_complete_delta_and_state_hashes_from_branch_cursor() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, fact = _common_fact(runtime)
    items = tuple(
        sorted(
            (
                inventory_item(
                    box(0, 0, 3, 20),
                    material=runtime.replay_input.instances[0].material,
                    token="derived-one",
                ),
                inventory_item(
                    box(0, 0, 2, 20),
                    material=runtime.replay_input.instances[0].material,
                    token="derived-two",
                ),
            ),
            key=lambda item: item.remnant.remnant_id,
        )
    )
    branch = _branch_cursor(cursor, added=items)

    result = certify_event_passivity(runtime, common=fact, branch_cursor=branch)

    assert result.passive
    assert result.witness is not None
    assert tuple(item.remnant_id for item in result.witness.influences) == tuple(
        item.remnant.remnant_id for item in items
    )
    assert result.witness.state_before_sha256 == m7_cursor_sha256(branch)
    expected_after = apply_m7_frozen_action_evidence(
        runtime,
        cursor=branch,
        event_position=0,
        action=fact.step.event.action,
    )
    assert result.witness.state_after_sha256 == m7_cursor_sha256(expected_after)
    assert result.witness.common_action_id == fact.step.event.action.action_id
    assert result.witness.branch_action_id == fact.step.event.action.action_id
    assert "delta" not in signature(certify_event_passivity).parameters
    assert "state_before_sha256" not in signature(certify_event_passivity).parameters


def test_certifier_rejects_changed_shared_remnant_record() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    item = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[0].material,
        token="changed-shared-record",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(item,))
    _, fact = _common_fact(runtime, cursor=cursor)
    changed = item.model_copy(update={"entered_at": item.entered_at + timedelta(hours=1)})

    with pytest.raises(ValueError, match="shared remnant record"):
        certify_event_passivity(
            runtime,
            common=fact,
            branch_cursor=replace(cursor, inventory=(changed,)),
        )


@pytest.mark.parametrize(
    "field",
    [
        "next_event_position",
        "current_time",
        "timestamp_group_sequence",
        "timestamp_subsequence",
        "previous_release",
    ],
)
def test_certifier_rejects_branch_cursor_metadata_mismatch(field: str) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, fact = _common_fact(runtime)
    values = {
        "next_event_position": cursor.next_event_position + 1,
        "current_time": cursor.current_time + timedelta(seconds=1),
        "timestamp_group_sequence": cursor.timestamp_group_sequence + 1,
        "timestamp_subsequence": cursor.timestamp_subsequence + 1,
        "previous_release": cursor.current_time,
    }

    with pytest.raises(ValueError, match="cursor metadata"):
        certify_event_passivity(
            runtime,
            common=fact,
            branch_cursor=replace(cursor, **{field: values[field]}),
        )


def test_frozen_action_application_matches_ordinary_standard_transition() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, fact = _common_fact(runtime)

    frozen = apply_m7_frozen_action_evidence(
        runtime,
        cursor=cursor,
        event_position=0,
        action=fact.step.event.action,
    )

    assert fact.step.event.action.kind.value == "open_standard_sheet"
    assert frozen == fact.step.cursor


def test_frozen_action_application_rejects_returned_remnant_identity_conflict() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor, fact = _common_fact(runtime)
    returned = fact.step.event.action.returned_remnants
    assert returned
    conflict = InventoryItem(remnant=returned[0], entered_at=cursor.current_time)

    with pytest.raises(ValueError, match="duplicate remnant identities"):
        apply_m7_frozen_action_evidence(
            runtime,
            cursor=replace(cursor, inventory=(conflict,)),
            event_position=0,
            action=fact.step.event.action,
        )


def test_frozen_action_application_matches_ordinary_remnant_transition() -> None:
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.REMNANT_FIRST,
    )
    item = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[0].material,
        token="frozen-remnant",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(item,))
    _, fact = _common_fact(runtime, cursor=cursor)

    frozen = apply_m7_frozen_action_evidence(
        runtime,
        cursor=cursor,
        event_position=0,
        action=fact.step.event.action,
    )

    assert fact.step.event.action.kind.value == "consume_remnant"
    assert frozen == fact.step.cursor
    with pytest.raises(ValueError, match="selected remnant is missing"):
        apply_m7_frozen_action_evidence(
            runtime,
            cursor=replace(cursor, inventory=()),
            event_position=0,
            action=fact.step.event.action,
        )


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
    _, fact = _common_fact(runtime, cursor=cursor)

    result = certify_event_passivity(
        runtime,
        common=fact,
        branch_cursor=_branch_cursor(cursor, removed=(item,)),
    )

    assert fact.step.action_binding.context.selected_stock_id == item.remnant.remnant_id
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
    _, fact = _common_fact(runtime, cursor=cursor)
    removed = next(
        item
        for item in items
        if item.remnant.remnant_id != fact.step.action_binding.context.selected_stock_id
    )

    result = certify_event_passivity(
        runtime,
        common=fact,
        branch_cursor=_branch_cursor(cursor, removed=(removed,)),
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
    cursor, fact = _common_fact(runtime)

    result = certify_event_passivity(
        runtime,
        common=fact,
        branch_cursor=_branch_cursor(cursor, added=items),
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
