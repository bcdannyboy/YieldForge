from __future__ import annotations

from dataclasses import replace

import pytest
from shapely import box

from tests.baseline.test_replay import _two_event_runtime
from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.oracle import compiled as compiled_module


def test_compiled_standard_winner_matches_m7_with_empty_inventory() -> None:
    from yieldforge.baseline.replay import (
        enumerate_m7_action_catalog,
        initial_m7_cursor,
        select_m7_fallback,
    )
    from yieldforge.oracle.compiled import compile_standard_winner

    runtime = _two_event_runtime()
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    ordinary = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    compiled = compile_standard_winner(runtime, event_position=0)

    assert compiled.action_id == ordinary.action_id
    assert compiled.decision_key == ordinary.decision_key
    assert compiled.problem_id == runtime.replay_input.instances[0].problem_id


def test_prepared_full_layout_accessor_reuses_one_exact_layout_per_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    original = compiled_module.prepare_layout_footprint
    constructed: list[str] = []

    def counted(problem, candidate, config):  # type: ignore[no-untyped-def]
        constructed.append(candidate.candidate_id)
        return original(problem, candidate, config)

    monkeypatch.setattr(compiled_module, "prepare_layout_footprint", counted)
    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        first = compiled_module._prepared_layout_footprints(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )
        second = compiled_module._prepared_layout_footprints(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )

    expected_ids = tuple(item.candidate_id for item in verified.candidates)
    assert tuple(constructed) == expected_ids
    assert tuple(item.candidate_id for item in first) == expected_ids
    assert second is first


def test_prepared_full_layout_accessor_rejects_live_candidate_geometry_drift() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        mutated = verified.candidates[0].model_copy(
            update={"width": verified.candidates[0].width + 0.5}
        )
        runtime.runtime_candidates[binding.problem_id] = replace(
            verified,
            candidates=(mutated, *verified.candidates[1:]),
        )
        try:
            with pytest.raises(ValueError, match="source geometry differs"):
                compiled_module._prepared_layout_footprints(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
        finally:
            runtime.runtime_candidates[binding.problem_id] = verified


def test_prepared_full_layout_accessor_rejects_candidate_reorder() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        runtime.runtime_candidates[binding.problem_id] = replace(
            verified,
            candidates=tuple(reversed(verified.candidates)),
        )
        try:
            with pytest.raises(ValueError, match="source geometry differs"):
                compiled_module._prepared_layout_footprints(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
        finally:
            runtime.runtime_candidates[binding.problem_id] = verified


def test_prepared_full_layout_accessor_rejects_registry_geometry_mutation() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    with pytest.raises(ValueError, match="integrity differs"):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            record = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # noqa: SLF001
                id(prepared)
            ]
            key, layouts = record.layout_footprints[0]
            first = layouts[0]
            mutated = replace(
                first,
                bounds=(first.bounds[0], first.bounds[1], first.bounds[2] + 0.5, first.bounds[3]),
            )
            object.__setattr__(
                record,
                "layout_footprints",
                ((key, (mutated, *layouts[1:])),),
            )
            compiled_module._prepared_layout_footprints(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )


def test_prepared_full_layout_accessor_rejects_expired_capability() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        compiled_module._prepared_layout_footprints(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )

    with pytest.raises(ValueError, match="invalid or inactive"):
        compiled_module._prepared_layout_footprints(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )


@pytest.mark.parametrize(
    "consumer",
    ("frontier", "footprints", "standard", "translation"),
)
def test_every_prepared_consumer_rejects_live_problem_width_drift(
    consumer: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    part = problem.problem.parts[0]
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material,
        token=f"prepared-live-width-drift-{consumer}",
    )

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        guarded_shape = part.shape
        object.__setattr__(
            part,
            "shape",
            (guarded_shape[0], (5.0, 0.0), (5.0, 10.0), *guarded_shape[3:]),
        )
        try:
            with pytest.raises(ValueError, match="source geometry differs"):
                if consumer == "frontier":
                    compiled_module._prepared_rejection_problem(  # noqa: SLF001
                        prepared,
                        runtime,
                        event_position=1,
                    )
                elif consumer == "footprints":
                    compiled_module._prepared_layout_footprints(  # noqa: SLF001
                        prepared,
                        runtime,
                        event_position=1,
                    )
                elif consumer == "standard":
                    compiled_module._prepared_standard_winner(  # noqa: SLF001
                        prepared,
                        runtime,
                        event_position=1,
                    )
                else:
                    compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                        runtime,
                        prepared=prepared,
                        event_position=1,
                        item=item,
                    )
        finally:
            object.__setattr__(part, "shape", guarded_shape)


def test_prepared_batch_exit_revalidates_every_live_source_binding() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    item = inventory_item(
        box(0, 0, 3.0, 20),
        material=binding.material,
        token="prepared-exit-live-source-revalidation",
    )
    batch_registry_before = dict(
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
    )
    lease_registry_before = dict(
        compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
    )

    with pytest.raises(ValueError, match="source geometry differs"):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            object.__setattr__(
                problem.problem.parts[0],
                "shape",
                ((0.0, 0.0), (5.0, 0.0), (5.0, 10.0), (0.0, 10.0), (0.0, 0.0)),
            )

    assert (
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
        == batch_registry_before
    )
    assert (
        compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
        == lease_registry_before
    )


def test_prepared_batch_cleanup_survives_malformed_live_config_root() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    original_shape = problem.problem.parts[0].shape
    original_config = runtime.replay_input.fit_config
    item = inventory_item(
        box(0, 0, 3.0, 20),
        material=binding.material,
        token="prepared-malformed-config-cleanup",
    )
    batch_registry_before = dict(
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
    )
    lease_registry_before = dict(
        compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
    )

    try:
        with pytest.raises(ValueError, match="source geometry differs"):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ) as prepared:
                compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                    runtime,
                    prepared=prepared,
                    event_position=1,
                    item=item,
                )
                object.__setattr__(runtime.replay_input, "fit_config", None)
    finally:
        object.__setattr__(runtime.replay_input, "fit_config", original_config)

    assert problem.problem.parts[0].shape is original_shape
    assert (
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
        == batch_registry_before
    )
    assert (
        compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
        == lease_registry_before
    )


def test_prepared_consumers_deep_check_one_key_once_then_revalidate_at_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 3.0, 20),
        material=binding.material,
        token="prepared-source-lease-deep-check-count",
    )
    original = compiled_module._prepared_layout_source_binding  # noqa: SLF001
    deep_checks: list[int] = []

    def counted(runtime, *, event_position):  # type: ignore[no-untyped-def]
        deep_checks.append(event_position)
        return original(runtime, event_position=event_position)

    monkeypatch.setattr(compiled_module, "_prepared_layout_source_binding", counted)
    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        assert deep_checks == [1]
        for _ in range(3):
            compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
        assert deep_checks == [1, 1]

    assert deep_checks == [1, 1, 1]


def test_prepared_repeat_lease_does_not_walk_live_problem_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 3.0, 20),
        material=binding.material,
        token="prepared-source-lease-direct-repeat",
    )
    original = compiled_module._prepared_key_and_inputs  # noqa: SLF001

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        canonical = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
            runtime,
            prepared=prepared,
            event_position=1,
            item=item,
        )

        def fail_live_problem_lookup(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a repeated lease must not walk the live problem collection")

        monkeypatch.setattr(compiled_module, "_prepared_key_and_inputs", fail_live_problem_lookup)
        try:
            repeated = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
        finally:
            monkeypatch.setattr(compiled_module, "_prepared_key_and_inputs", original)

    assert repeated == canonical


def test_prepared_lease_blocks_transient_nested_shape_mutation() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    part = problem.problem.parts[0]
    original_shape = part.shape
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material,
        token="prepared-transient-shape-mutation",
    )

    with pytest.raises(ValueError, match="source mutated during prepared use"):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            part.shape[1:3] = [(5.0, 0.0), (5.0, 10.0)]
            try:
                with pytest.raises(ValueError, match="source mutated during prepared use"):
                    compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                        runtime,
                        prepared=prepared,
                        event_position=1,
                        item=item,
                    )
            finally:
                part.shape[:] = original_shape

    assert part.shape is original_shape


def test_prepared_snapshot_is_stable_during_transient_base_list_shape_mutation() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    part = problem.problem.parts[0]
    original_shape = part.shape
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material,
        token="prepared-base-list-shape-mutation",
    )

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        guarded_shape = part.shape
        guarded_values = list(guarded_shape)
        canonical = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
            runtime,
            prepared=prepared,
            event_position=1,
            item=item,
        )
        list.__setitem__(
            guarded_shape,
            slice(1, 3),
            [(5.0, 0.0), (5.0, 10.0)],
        )
        try:
            during_mutation = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
        finally:
            list.__setitem__(
                guarded_shape,
                slice(None),
                guarded_values,
            )
        repeated = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
            runtime,
            prepared=prepared,
            event_position=1,
            item=item,
        )

    assert during_mutation == repeated == canonical
    assert part.shape is original_shape


def test_prepared_exit_detects_persistent_base_list_shape_mutation() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    part = problem.problem.parts[0]
    original_shape = part.shape
    original_values = list(original_shape)
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material,
        token="prepared-persistent-base-list-shape-mutation",
    )

    with pytest.raises(ValueError, match="source"):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            canonical = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            guarded_shape = part.shape
            list.__setitem__(
                guarded_shape,
                slice(1, 3),
                [(5.0, 0.0), (5.0, 10.0)],
            )
            repeated = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            assert repeated == canonical

    assert part.shape is original_shape
    assert part.shape == original_values


@pytest.mark.parametrize(
    "source_field",
    (
        "placement_translation",
        "candidate_width",
        "fit_coordinate_tolerance",
        "event_material",
    ),
)
def test_prepared_snapshot_is_issuance_bound_during_reflective_source_mutation(
    source_field: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    placement = verified.candidates[0].placements[0]
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-proof-owned-snapshot-{source_field}",
    )

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        canonical = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
            runtime,
            prepared=prepared,
            event_position=1,
            item=item,
        )
        if source_field == "placement_translation":
            owner, field_name = placement, "translation"
            mutated = (placement.translation[0] + 0.5, placement.translation[1])
        elif source_field == "candidate_width":
            owner, field_name = verified.candidates[0], "width"
            mutated = owner.width + 0.5
        elif source_field == "fit_coordinate_tolerance":
            owner, field_name = runtime.replay_input.fit_config, "coordinate_tolerance"
            mutated = owner.coordinate_tolerance * 2.0
        else:
            owner, field_name = binding.material, "grade"
            mutated = f"{binding.material.grade}-mutated"
        original = getattr(owner, field_name)
        object.__setattr__(owner, field_name, mutated)
        try:
            repeated = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
        finally:
            object.__setattr__(owner, field_name, original)

    assert repeated == canonical


@pytest.mark.parametrize(
    ("source_field", "error_match"),
    (
        ("candidate_width", "source geometry differs"),
        ("fit_config", "source geometry differs"),
        ("event_material", "event source snapshot differs"),
        ("search_config", "semantic runtime source differs"),
    ),
)
def test_prepared_snapshot_exit_rejects_persistent_reflective_source_drift(
    source_field: str,
    error_match: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    if source_field == "candidate_width":
        owner, field_name = verified.candidates[0], "width"
        mutated = owner.width + 0.5
    elif source_field == "fit_config":
        owner, field_name = runtime.replay_input.fit_config, "coordinate_tolerance"
        mutated = owner.coordinate_tolerance * 2.0
    elif source_field == "search_config":
        owner, field_name = runtime.replay_input.search_config, "maximum_candidates"
        mutated = owner.maximum_candidates + 1
    else:
        owner, field_name = binding.material, "grade"
        mutated = f"{binding.material.grade}-persistent"
    original = getattr(owner, field_name)
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-persistent-reflective-drift-{source_field}",
    )

    try:
        with pytest.raises(ValueError, match=error_match):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ) as prepared:
                canonical = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                    runtime,
                    prepared=prepared,
                    event_position=1,
                    item=item,
                )
                object.__setattr__(owner, field_name, mutated)
                repeated = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                    runtime,
                    prepared=prepared,
                    event_position=1,
                    item=item,
                )
                assert repeated == canonical
    finally:
        object.__setattr__(owner, field_name, original)


def test_prepared_source_restores_original_list_after_persistent_replacement() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    part = problem.problem.parts[0]
    original_shape = part.shape
    original_values = list(original_shape)
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material,
        token="prepared-persistent-shape-replacement-cleanup",
    )

    with pytest.raises(ValueError, match="source"):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            canonical = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            replacement = list(part.shape)
            replacement[1:3] = [(5.0, 0.0), (5.0, 10.0)]
            object.__setattr__(part, "shape", replacement)
            repeated = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            assert repeated == canonical

    assert part.shape is original_shape
    assert part.shape == original_values


def test_prepared_source_restores_original_list_after_persistent_tracked_mutation() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    part = problem.problem.parts[0]
    original_shape = part.shape
    original_values = list(original_shape)
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material,
        token="prepared-persistent-tracked-shape-cleanup",
    )

    with pytest.raises(ValueError, match="source"):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            part.shape[1:3] = [(5.0, 0.0), (5.0, 10.0)]

    assert part.shape is original_shape
    assert part.shape == original_values


def test_prepared_shared_fit_config_snapshot_is_stable_for_every_leased_key() -> None:
    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    fit_config = runtime.replay_input.fit_config
    original_tolerance = fit_config.coordinate_tolerance
    items = tuple(
        inventory_item(
            box(0, 0, 4.5, 20),
            material=runtime.replay_input.instances[event_position].material,
            token=f"prepared-shared-fit-config-{event_position}",
        )
        for event_position in (0, 1)
    )

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(0, 1),
    ) as prepared:
        canonical = tuple(
            compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=event_position,
                item=items[event_position],
            )
            for event_position in (0, 1)
        )
        object.__setattr__(
            fit_config,
            "coordinate_tolerance",
            original_tolerance * 2.0,
        )
        try:
            repeated = tuple(
                compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                    runtime,
                    prepared=prepared,
                    event_position=event_position,
                    item=items[event_position],
                )
                for event_position in (0, 1)
            )
        finally:
            object.__setattr__(
                fit_config,
                "coordinate_tolerance",
                original_tolerance,
            )

    assert repeated == canonical
