from __future__ import annotations

import gc
import weakref
from collections import OrderedDict
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from datetime import datetime, tzinfo
from types import MappingProxyType

import pytest
from shapely import box

from tests.baseline.test_replay import _two_event_runtime
from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.oracle import compiled as compiled_module


class _ExplodingWeakRef(weakref.ref):
    def __call__(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected exploding prepared weak reference")


class _FlipHash:
    armed = False

    def __hash__(self) -> int:
        if self.armed:
            raise RuntimeError("injected drifting prepared registry hash")
        return id(self)


class _DeadWeakTarget:
    pass


class _UnprintableValueError(ValueError):
    def __str__(self) -> str:
        raise RuntimeError("injected unprintable prepared cleanup failure")


class _ExplodingDiagnostic:
    def __bool__(self) -> bool:
        raise RuntimeError("injected exploding prepared diagnostic truthiness")

    def __eq__(self, _other: object) -> bool:
        raise RuntimeError("injected exploding prepared diagnostic equality")


class _CollidingExplodingKey:
    def __init__(self, target: int, *, armed: bool = True) -> None:
        self._target = target
        self.armed = armed

    def __hash__(self) -> int:
        return hash(self._target)

    def __eq__(self, _other: object) -> bool:
        if self.armed:
            raise RuntimeError("injected colliding prepared registry equality")
        return False


class _EqualIntegerAlias:
    def __init__(self, target: int) -> None:
        self._target = target

    def __hash__(self) -> int:
        return hash(self._target)

    def __eq__(self, other: object) -> bool:
        return type(other) is int and other == self._target


class _SameValueFloat(float):
    """A numerically equal scalar subclass that exact DTOs must still reject."""


class _SameValueString(str):
    __hash__ = str.__hash__


class _HostileTimezone(tzinfo):
    def __init__(self) -> None:
        self.calls = 0

    def _explode(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise KeyError("injected hostile prepared timezone")

    def utcoffset(self, _value):  # type: ignore[no-untyped-def]
        return self._explode()

    def dst(self, _value):  # type: ignore[no-untyped-def]
        return self._explode()

    def tzname(self, _value):  # type: ignore[no-untyped-def]
        return self._explode()


class _SameValueInt(int):
    pass


class _SameValueTuple(tuple):
    pass


def _drift_to_same_layout_class(
    value: object,
    *,
    hostile_getattribute: bool = False,
) -> type[object]:
    original_type = type(value)
    namespace: dict[str, object] = {}
    if hasattr(original_type, "__slots__"):
        namespace["__slots__"] = original_type.__slots__
    if hostile_getattribute:

        def exploding_getattribute(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("injected hostile same-layout class attribute access")

        namespace["__getattribute__"] = exploding_getattribute
    drifted_type = type(
        f"_Drifted{original_type.__name__}{id(value)}",
        (),
        namespace,
    )
    object.__setattr__(value, "__class__", drifted_type)
    assert type(value) is drifted_type
    return original_type


def _prepared_registry_key_sets() -> tuple[object, ...]:
    registries = (
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY,  # noqa: SLF001
        compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
        compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
        compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
        compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
        compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
        compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
    )
    return tuple(
        (
            set(registry),
            *registry._debug_integrity_state(),  # noqa: SLF001
        )
        for registry in registries
    )


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
        '"collisions":[' + collision_literal + ']} for layout in request["layouts"]]}))\'\n'
    )
    path.chmod(0o700)


def _issue_prepared_children(
    stack: ExitStack,
    runtime,
    *,
    token: str,
):  # type: ignore[no-untyped-def]
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=token,
    )
    prepared = stack.enter_context(
        compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        )
    )
    inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
        prepared,
        runtime,
        event_position=1,
        remnants=(item.remnant,),
    )
    prepared_record = _trusted_registry_value(  # noqa: SLF001
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY,
        id(prepared),
    )
    lease = next(iter(prepared_record.source_leases.values()))
    authority = next(iter(prepared_record.remnant_authorities.values()))
    return (
        prepared,
        inputs,
        {
            "frontier_inputs": inputs,
            "source_lease": lease,
            "remnant": authority,
        },
    )


def _prepared_child_registries(owner_kind: str):  # type: ignore[no-untyped-def]
    owner_registry = {
        "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
        "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
        "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
    }[owner_kind]
    main_registry = {
        "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
        "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
        "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
    }[owner_kind]
    return main_registry, owner_registry


def _trusted_registry_value(registry, key: int):  # type: ignore[no-untyped-def]
    """Inspect test-only issuance state without exercising the attack surface."""

    return registry._trusted_get(key)  # noqa: SLF001


def _require_prepared_child_capability(
    owner_kind: str,
    *,
    child,
    prepared,
    runtime,
    registered,
):  # type: ignore[no-untyped-def]
    if owner_kind == "frontier_inputs":
        return compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
            child,
            prepared=prepared,
            runtime=runtime,
            event_position=1,
        )
    if owner_kind == "source_lease":
        return compiled_module._require_registered_prepared_layout_source_lease(  # noqa: SLF001
            child,
            prepared=prepared,
            runtime=runtime,
            key=registered.key,
            event_position=1,
        )
    batch = _trusted_registry_value(  # noqa: SLF001
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY,
        id(prepared),
    )
    return compiled_module._require_prepared_remnant_measurement_authority(  # noqa: SLF001
        prepared,
        batch,
        key=registered.semantic_key,
        authority=child,
    )


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
    assert second == first
    assert second is not first
    assert all(right is not left for left, right in zip(first, second, strict=True))


@pytest.mark.parametrize(
    "audit_kind",
    ("standard_scalar", "standard_accounting", "rejection_scalar", "layout_bounds"),
)
def test_prepared_audit_consumers_reject_nested_exact_type_drift(
    audit_kind: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        if audit_kind.startswith("standard"):
            observed = compiled_module._prepared_standard_winner(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )
            profile = observed.standard_profiles[0]
            if audit_kind == "standard_scalar":
                object.__setattr__(
                    profile,
                    "candidate_width",
                    _SameValueFloat(profile.candidate_width),
                )
            else:
                accounting = profile.accounting
                clean_payload = accounting.model_dump(mode="json", warnings=False)
                object.__setattr__(
                    accounting,
                    "retained_child_area",
                    accounting.retained_child_area + 777.0,
                )
                object.__setattr__(
                    accounting,
                    "model_dump",
                    lambda **_kwargs: clean_payload,
                )
            consume = lambda: compiled_module._consume_prepared_standard_winner(  # noqa: E731, SLF001
                prepared,
                runtime,
                event_position=1,
                observed=observed,
            )
        elif audit_kind == "rejection_scalar":
            observed = compiled_module._prepared_rejection_problem(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )
            scalar = observed.frontier.retained[0]
            object.__setattr__(scalar, "width", _SameValueFloat(scalar.width))
            consume = lambda: compiled_module._consume_prepared_rejection_problem(  # noqa: E731, SLF001
                prepared,
                runtime,
                event_position=1,
                observed=observed,
            )
        else:
            observed = compiled_module._prepared_layout_footprints(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )
            footprint = observed[0]
            object.__setattr__(
                footprint,
                "bounds",
                (_SameValueFloat(footprint.bounds[0]), *footprint.bounds[1:]),
            )
            consume = lambda: compiled_module._consume_prepared_layout_footprints(  # noqa: E731, SLF001
                prepared,
                runtime,
                event_position=1,
                observed=observed,
            )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            consume()

    assert _prepared_registry_key_sets() == before


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
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
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
            with pytest.raises(ValueError, match="live source boundary"):
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

    with pytest.raises(ValueError, match="live source boundary"):
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
        with pytest.raises(ValueError, match="live source boundary"):
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


def test_prepared_consumers_revalidate_public_source_on_every_use_and_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 3.0, 20),
        material=binding.material,
        token="prepared-source-lease-deep-check-count",
    )
    original = compiled_module._prepared_layout_source_binding_from_inputs  # noqa: SLF001
    deep_checks: list[str] = []

    def counted(  # type: ignore[no-untyped-def]
        runtime,
        *,
        problem,
        verified,
        rejection_projections=None,
    ):
        deep_checks.append(problem.problem_id)
        return original(
            runtime,
            problem=problem,
            verified=verified,
            rejection_projections=rejection_projections,
        )

    monkeypatch.setattr(
        compiled_module,
        "_prepared_layout_source_binding_from_inputs",
        counted,
    )
    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        assert deep_checks
        for _ in range(3):
            before = len(deep_checks)
            compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                runtime,
                prepared=prepared,
                event_position=1,
                item=item,
            )
            assert len(deep_checks) > before

    assert len(deep_checks) > 3


def test_prepared_event_scope_keeps_repeated_consumers_on_o1_authority_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 3.0, 20),
        material=binding.material,
        token="prepared-event-scope-o1-authority",
    )
    helper_names = (
        "_preflight_prepared_source_runtime",
        "_exact_runtime_candidate_entries",
        "_prepared_translation_layout_key_fingerprint",
        "_prepared_layout_record_key_values",
        "_require_prepared_snapshot_runtime_integrity",
        "_require_prepared_trusted_runtime_integrity",
    )
    calls = {name: 0 for name in helper_names}
    for name in helper_names:
        original = getattr(compiled_module, name)

        def counted(*args, _name=name, _original=original, **kwargs):  # type: ignore[no-untyped-def]
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(compiled_module, name, counted)

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        with compiled_module._activate_prepared_event_validation(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        ):
            cold_counts = dict(calls)
            for _ in range(3):
                compiled_module._prepared_standard_winner(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
                compiled_module._prepared_rejection_problem(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
                compiled_module._prepared_layout_footprints(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
                compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                    runtime,
                    prepared=prepared,
                    event_position=1,
                    item=item,
                )
                compiled_module._prepared_source_runtime(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
            assert calls == cold_counts


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
            if source_field in {"fit_coordinate_tolerance", "event_material"}:
                with pytest.raises(
                    compiled_module.M8PreparedFrontierIntegrityError,
                    match="source lease payload",
                ):
                    compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                        runtime,
                        prepared=prepared,
                        event_position=1,
                        item=item,
                    )
            else:
                repeated = compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                    runtime,
                    prepared=prepared,
                    event_position=1,
                    item=item,
                )
                assert repeated == canonical
        finally:
            object.__setattr__(owner, field_name, original)


def test_prepared_frontier_batch_inputs_are_proof_owned_and_complete() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    first = inventory_item(
        box(0, 0, 3, 9),
        material=binding.material.model_copy(deep=True),
        token="prepared-columnar-input-first",
    )
    second = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="prepared-columnar-input-second",
    )

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
            remnants=(first.remnant, second.remnant),
        )

    assert id(inputs) not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
    assert inputs.problem is not None
    assert inputs.problem.problem_id == binding.problem_id
    assert inputs.candidate_ids == tuple(item.candidate_id for item in verified.candidates)
    assert inputs.rejection_layout_candidate_ids == tuple(
        item.candidate_id for item in verified.rejection_layouts
    )
    assert inputs.event_material_key == compiled_module.material_key(binding.material)
    assert inputs.fit_config == runtime.replay_input.fit_config
    assert inputs.fit_config_sha256 == verified.rejection_layouts[0].fit_config_sha256
    assert inputs.content_sha256 == compiled_module._prepared_frontier_batch_inputs_sha256(  # noqa: SLF001
        inputs
    )
    assert tuple(item.remnant_id for item in inputs.measurements) == (
        first.remnant.remnant_id,
        second.remnant.remnant_id,
    )


def test_prepared_frontier_issuer_rejects_rehashed_valid_lease_forgery() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="prepared-reissued-frontier-forgery",
    )

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        canonical = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
            remnants=(item.remnant,),
        )
        assert canonical.problem is not None
        registered = _trusted_registry_value(
            compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY, id(canonical)
        )
        forged_members = tuple(
            replace(
                member,
                area=member.area * 100.0,
                width=member.width * 100.0,
                height=member.height * 100.0,
            )
            for member in canonical.problem.frontier.members
        )
        forged_problem = replace(
            canonical.problem,
            frontier=compiled_module.build_pareto_frontier(forged_members),
        )
        provisional = replace(
            canonical,
            problem=forged_problem,
            content_sha256="",
        )
        forged = replace(
            provisional,
            content_sha256=compiled_module._prepared_frontier_batch_inputs_sha256(  # noqa: SLF001
                provisional
            ),
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            compiled_module._issue_prepared_frontier_batch_inputs(  # noqa: SLF001
                forged,
                prepared=prepared,
                runtime=runtime,
                event_position=1,
                source_lease=registered.source_lease,
                remnant_authorities=registered.remnant_authorities,
            )

        assert id(forged) not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001


@pytest.mark.parametrize("corruption", ("fit_config_serializer_lie", "measurement_float_subclass"))
def test_prepared_frontier_inputs_reject_nested_exact_type_drift(
    corruption: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-nested-exact-drift-{corruption}",
    )
    before = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
            remnants=(item.remnant,),
        )
        if corruption == "fit_config_serializer_lie":
            target = inputs.fit_config
            original = target.coordinate_tolerance
            original_payload = target.model_dump(mode="json", warnings=False)
            object.__setattr__(target, "coordinate_tolerance", 0.0)
            object.__setattr__(target, "model_dump", lambda **_kwargs: original_payload)
            try:
                with pytest.raises(
                    compiled_module.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                        inputs,
                        prepared=prepared,
                        runtime=runtime,
                        event_position=1,
                    )
            finally:
                object.__setattr__(target, "coordinate_tolerance", original)
                object.__getattribute__(target, "__dict__").pop("model_dump", None)
        else:
            target = inputs.measurements[0]
            original = target.area
            object.__setattr__(target, "area", _SameValueFloat(original))
            try:
                with pytest.raises(
                    compiled_module.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                        inputs,
                        prepared=prepared,
                        runtime=runtime,
                        event_position=1,
                    )
            finally:
                object.__setattr__(target, "area", original)

    assert _prepared_registry_key_sets() == before


@pytest.mark.parametrize("corruption", ("candidate", "fit_config", "material"))
def test_issued_frontier_inputs_reject_live_public_source_drift(corruption: str) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-live-source-drift-{corruption}",
    )
    before = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
            remnants=(item.remnant,),
        )
        if corruption == "candidate":
            target = verified.candidates[0]
            field_name = "width"
            replacement = target.width + 100.0
        elif corruption == "fit_config":
            target = runtime.replay_input.fit_config
            field_name = "coordinate_tolerance"
            replacement = target.coordinate_tolerance * 100.0
        else:
            target = binding.material
            field_name = "provenance"
            replacement = compiled_module.MaterialProvenance.OBSERVED
        original = getattr(target, field_name)
        object.__setattr__(target, field_name, replacement)
        try:
            with pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                    inputs,
                    prepared=prepared,
                    runtime=runtime,
                    event_position=1,
                )
        finally:
            object.__setattr__(target, field_name, original)

    assert _prepared_registry_key_sets() == before


def test_prepared_batch_rejects_nonexact_physical_candidate_mapping_key() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    candidate_set = runtime.runtime_candidates.pop(binding.problem_id)
    hostile_key = _SameValueString(binding.problem_id)
    runtime.runtime_candidates[hostile_key] = candidate_set
    before = _prepared_registry_key_sets()

    try:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ):
                pass
    finally:
        runtime.runtime_candidates.pop(hostile_key)
        runtime.runtime_candidates[binding.problem_id] = candidate_set

    assert _prepared_registry_key_sets() == before


@pytest.mark.parametrize(
    "event_positions",
    (_SameValueTuple((1,)), (_SameValueInt(1),)),
)
def test_prepared_batch_rejects_nonexact_event_position_graph(
    event_positions: tuple[int, ...],
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=event_positions,
        ):
            pass

    assert _prepared_registry_key_sets() == before


def test_prepared_batch_rejects_nonexact_nested_source_container_before_snapshot() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    original = runtime.replay_input.instances
    object.__setattr__(runtime.replay_input, "instances", _SameValueTuple(original))
    before = _prepared_registry_key_sets()

    try:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ):
                pass
    finally:
        object.__setattr__(runtime.replay_input, "instances", original)

    assert _prepared_registry_key_sets() == before


@pytest.mark.parametrize("source_field", ("horizon_end", "released_at"))
def test_prepared_batch_rejects_hostile_datetime_timezone_before_callbacks(
    source_field: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    owner = runtime.replay_input if source_field == "horizon_end" else binding
    original = getattr(owner, source_field)
    hostile_timezone = _HostileTimezone()
    hostile = datetime(
        original.year,
        original.month,
        original.day,
        original.hour,
        original.minute,
        original.second,
        original.microsecond,
        tzinfo=hostile_timezone,
        fold=original.fold,
    )
    object.__setattr__(owner, source_field, hostile)
    before = _prepared_registry_key_sets()

    try:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="source runtime capture",
        ):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ):
                pass
    finally:
        object.__setattr__(owner, source_field, original)

    assert hostile_timezone.calls == 0
    assert _prepared_registry_key_sets() == before


def test_prepared_batch_rejects_retained_layout_class_drift_before_snapshot() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    retained = runtime.runtime_candidates[binding.problem_id].rejection_layouts[0]
    original_type = type(retained)
    drifted_type = type(
        f"_DriftedRetainedLayout{id(retained)}",
        (original_type,),
        {},
    )
    object.__setattr__(retained, "__class__", drifted_type)
    before = _prepared_registry_key_sets()

    try:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="source runtime capture",
        ):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ):
                pass
    finally:
        object.__setattr__(retained, "__class__", original_type)

    assert _prepared_registry_key_sets() == before


def test_cached_prepared_accessor_rejects_nonexact_event_position() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        compiled_module._prepared_standard_winner(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="event position type",
        ):
            compiled_module._prepared_standard_winner(  # noqa: SLF001
                prepared,
                runtime,
                event_position=_SameValueInt(1),
            )

    assert _prepared_registry_key_sets() == before


def test_cached_prepared_accessor_rejects_runtime_class_drift_before_registry_use() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        compiled_module._prepared_standard_winner(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )
        original_type = _drift_to_same_layout_class(runtime)
        try:
            with pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="source boundary",
            ):
                compiled_module._prepared_standard_winner(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
        finally:
            object.__setattr__(runtime, "__class__", original_type)

    assert _prepared_registry_key_sets() == before


def test_cached_prepared_accessor_rejects_replay_input_class_drift_before_lease() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        compiled_module._prepared_standard_winner(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )
        replay_input = runtime.replay_input
        original_type = type(replay_input)
        drifted_type = type(
            f"_DriftedReplayInput{id(replay_input)}",
            (original_type,),
            {},
        )
        object.__setattr__(replay_input, "__class__", drifted_type)
        try:
            with pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="source boundary",
            ):
                compiled_module._prepared_standard_winner(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
        finally:
            object.__setattr__(replay_input, "__class__", original_type)

    assert _prepared_registry_key_sets() == before


def test_prepared_translation_rejection_captures_item_before_private_lease() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 4.5, 20),
        material=binding.material.model_copy(deep=True),
        token="prepared-item-capture-order",
    )
    before = _prepared_registry_key_sets()

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
        original_type = type(item)
        drifted_type = type(
            f"_DriftedInventoryItem{id(item)}",
            (original_type,),
            {},
        )
        object.__setattr__(item, "__class__", drifted_type)
        try:
            with pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="inventory source capture",
            ):
                compiled_module._compile_prepared_translation_rejections(  # noqa: SLF001
                    runtime,
                    prepared=prepared,
                    event_position=1,
                    item=item,
                )
        finally:
            object.__setattr__(item, "__class__", original_type)

    assert _prepared_registry_key_sets() == before


def test_prepared_snapshot_ignores_caller_cache_concrete_type() -> None:
    callbacks: list[str] = []

    class _CallerCache(OrderedDict):  # type: ignore[type-arg]
        def clear(self) -> None:
            callbacks.append("clear")
            super().clear()

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    runtime.runtime_candidates = MappingProxyType(dict(runtime.runtime_candidates))
    runtime.prepared_layout_cache = _CallerCache(runtime.prepared_layout_cache)
    before = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        winner = compiled_module._prepared_standard_winner(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )

    assert tuple(profile.candidate_width for profile in winner.standard_profiles) == (4.0, 4.0)
    assert callbacks == []
    assert _prepared_registry_key_sets() == before


def test_prepared_cleanup_drains_children_after_parent_registry_loss() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="prepared-parent-registry-loss-cleanup",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        "M8 prepared frontier integrity differs: parent registry loss sentinel"
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
                id(prepared)
            )
            raise sentinel

    assert captured.value is sentinel
    assert id(inputs) not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
    assert prepared._remnant_authorities == {}  # noqa: SLF001
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("corruption", ("owner_replacement", "malformed_record"))
def test_prepared_cleanup_drains_children_after_owner_record_replacement(
    corruption: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-child-owner-replacement-cleanup-{corruption}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        "M8 prepared frontier integrity differs: child owner replacement sentinel"
    )
    before_registry_keys = (
        set(compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY),  # noqa: SLF001
    )

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            lease_id, lease_record = next(
                (child_id, child)
                for child_id, child in (
                    compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY.items()  # noqa: SLF001
                )
                if child.prepared is prepared
            )
            authority_id, authority_record = next(
                (child_id, child)
                for child_id, child in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY.items()  # noqa: SLF001
                if child.prepared is prepared
            )
            input_id = id(inputs)
            input_record = _trusted_registry_value(
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY, input_id
            )  # noqa: SLF001
            if corruption == "owner_replacement":
                forged_owner = object()
                compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY[lease_id] = replace(  # noqa: SLF001
                    lease_record,
                    prepared=forged_owner,
                )
                compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY[authority_id] = replace(  # noqa: SLF001
                    authority_record,
                    prepared=forged_owner,
                )
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY[input_id] = replace(  # noqa: SLF001
                    input_record,
                    prepared=forged_owner,
                )
            else:
                compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY[lease_id] = object()  # type: ignore[assignment]  # noqa: SLF001
                compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY[authority_id] = object()  # type: ignore[assignment]  # noqa: SLF001
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY[input_id] = object()  # type: ignore[assignment]  # noqa: SLF001
            raise sentinel

    assert captured.value is sentinel
    assert lease_id not in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
    assert authority_id not in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
    assert input_id not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
    assert (
        set(compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY),  # noqa: SLF001
    ) == before_registry_keys


def test_prepared_cleanup_cannot_consume_foreign_active_batch_children() -> None:
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_binding = foreign_runtime.replay_input.instances[1]
    local_binding = local_runtime.replay_input.instances[1]
    foreign_item = inventory_item(
        box(0, 0, 5, 11),
        material=foreign_binding.material.model_copy(deep=True),
        token="prepared-foreign-child-owner",
    )
    local_item = inventory_item(
        box(0, 0, 5, 11),
        material=local_binding.material.model_copy(deep=True),
        token="prepared-local-child-owner",
    )
    before_registry_keys = (
        set(compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY),  # noqa: SLF001
    )

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        foreign_runtime,
        event_positions=(1,),
    ) as foreign_prepared:
        foreign_inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            foreign_prepared,
            foreign_runtime,
            event_position=1,
            remnants=(foreign_item.remnant,),
        )
        foreign_record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(foreign_prepared)
        )
        foreign_lease_key, foreign_lease = next(iter(foreign_record.source_leases.items()))
        foreign_authority_key, foreign_authority = next(
            iter(foreign_record.remnant_authorities.items())
        )
        foreign_lease_id = id(foreign_lease)
        foreign_authority_id = id(foreign_authority)
        foreign_input_id = id(foreign_inputs)

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                local_runtime,
                event_positions=(1,),
            ) as local_prepared:
                local_inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                    local_prepared,
                    local_runtime,
                    event_position=1,
                    remnants=(local_item.remnant,),
                )
                local_record = _trusted_registry_value(
                    compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(local_prepared)
                )
                local_lease_id = id(next(iter(local_record.source_leases.values())))
                local_authority_id = id(next(iter(local_record.remnant_authorities.values())))
                local_input_id = id(local_inputs)
                local_record.source_leases[
                    (f"foreign:{foreign_lease_key[0]}", foreign_lease_key[1])
                ] = foreign_lease
                local_record.frontier_inputs[foreign_input_id] = foreign_record.frontier_inputs[
                    foreign_input_id
                ]
                local_prepared._remnant_authorities[  # noqa: SLF001
                    foreign_authority_key
                ] = foreign_authority

        assert local_lease_id not in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
        assert local_authority_id not in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
        assert local_input_id not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
        assert foreign_lease_id in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
        assert foreign_authority_id in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
        assert foreign_input_id in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )

    assert (
        set(compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY),  # noqa: SLF001
        set(compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY),  # noqa: SLF001
    ) == before_registry_keys


@pytest.mark.parametrize("injection", ("foreign_into_local", "local_into_foreign"))
def test_prepared_cleanup_uses_independent_child_ownership_for_reverse_injection(
    injection: str,
) -> None:
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_binding = foreign_runtime.replay_input.instances[1]
    local_binding = local_runtime.replay_input.instances[1]
    foreign_item = inventory_item(
        box(0, 0, 5, 11),
        material=foreign_binding.material.model_copy(deep=True),
        token=f"prepared-independent-owner-foreign-{injection}",
    )
    local_item = inventory_item(
        box(0, 0, 5, 11),
        material=local_binding.material.model_copy(deep=True),
        token=f"prepared-independent-owner-local-{injection}",
    )
    before_registry_keys = _prepared_registry_key_sets()

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        foreign_runtime,
        event_positions=(1,),
    ) as foreign_prepared:
        foreign_inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            foreign_prepared,
            foreign_runtime,
            event_position=1,
            remnants=(foreign_item.remnant,),
        )
        foreign_record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(foreign_prepared)
        )
        foreign_lease_key, foreign_lease = next(iter(foreign_record.source_leases.items()))
        foreign_authority_key, foreign_authority = next(
            iter(foreign_record.remnant_authorities.items())
        )
        foreign_ids = (id(foreign_lease), id(foreign_authority), id(foreign_inputs))

        expected_exit = (
            pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            )
            if injection == "foreign_into_local"
            else nullcontext()
        )
        with expected_exit:
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                local_runtime,
                event_positions=(1,),
            ) as local_prepared:
                local_inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                    local_prepared,
                    local_runtime,
                    event_position=1,
                    remnants=(local_item.remnant,),
                )
                local_record = _trusted_registry_value(
                    compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(local_prepared)
                )
                local_lease_key, local_lease = next(iter(local_record.source_leases.items()))
                local_authority_key, local_authority = next(
                    iter(local_record.remnant_authorities.items())
                )
                local_ids = (id(local_lease), id(local_authority), id(local_inputs))
                if injection == "foreign_into_local":
                    local_record.source_leases[
                        (f"foreign:{foreign_lease_key[0]}", foreign_lease_key[1])
                    ] = foreign_lease
                    local_record.frontier_inputs[id(foreign_inputs)] = (
                        foreign_record.frontier_inputs[id(foreign_inputs)]
                    )
                    local_prepared._remnant_authorities[  # noqa: SLF001
                        foreign_authority_key
                    ] = foreign_authority
                    foreign_child_records = (
                        _trusted_registry_value(
                            compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY, foreign_ids[0]
                        ),
                        _trusted_registry_value(
                            compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY, foreign_ids[1]
                        ),
                        _trusted_registry_value(
                            compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY, foreign_ids[2]
                        ),
                    )
                    compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY[  # noqa: SLF001
                        foreign_ids[0]
                    ] = replace(foreign_child_records[0], prepared=local_prepared)
                    compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY[  # noqa: SLF001
                        foreign_ids[1]
                    ] = replace(foreign_child_records[1], prepared=local_prepared)
                    compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY[  # noqa: SLF001
                        foreign_ids[2]
                    ] = replace(foreign_child_records[2], prepared=local_prepared)
                    compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
                        id(foreign_prepared)
                    )
                else:
                    foreign_record.source_leases[
                        (f"local:{local_lease_key[0]}", local_lease_key[1])
                    ] = local_lease
                    foreign_record.frontier_inputs[id(local_inputs)] = local_record.frontier_inputs[
                        id(local_inputs)
                    ]
                    foreign_prepared._remnant_authorities[  # noqa: SLF001
                        local_authority_key
                    ] = local_authority

        if injection == "foreign_into_local":
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # noqa: SLF001
                id(foreign_prepared)
            ] = foreign_record
            compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY[  # noqa: SLF001
                foreign_ids[0]
            ] = foreign_child_records[0]
            compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY[  # noqa: SLF001
                foreign_ids[1]
            ] = foreign_child_records[1]
            compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY[  # noqa: SLF001
                foreign_ids[2]
            ] = foreign_child_records[2]
            assert foreign_ids[0] in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
            assert foreign_ids[1] in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
            assert foreign_ids[2] in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
            assert (
                compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                    foreign_inputs,
                    prepared=foreign_prepared,
                    runtime=foreign_runtime,
                    event_position=1,
                )
                == foreign_inputs
            )
        else:
            assert local_ids[0] not in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
            assert local_ids[1] not in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
            assert local_ids[2] not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
            foreign_record.source_leases.pop(
                (f"local:{local_lease_key[0]}", local_lease_key[1]),
                None,
            )
            foreign_record.frontier_inputs.pop(id(local_inputs), None)
            foreign_prepared._remnant_authorities.pop(  # noqa: SLF001
                local_authority_key,
                None,
            )

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "fault_phase",
    ("frontier_inputs", "source_leases", "mutation_guards", "remnant_authorities", "snapshot"),
)
@pytest.mark.parametrize("fault_kind", ("runtime", "unprintable_value"))
def test_prepared_parent_teardown_is_failure_atomic_for_each_release_phase(
    monkeypatch: pytest.MonkeyPatch,
    fault_phase: str,
    fault_kind: str,
) -> None:
    from yieldforge.baseline.replay import M7SemanticRuntimeSnapshot

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    original_parts = problem.problem.parts
    original_placements = verified.candidates[0].placements
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-failure-atomic-{fault_phase}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: teardown sentinel {fault_phase}"
    )
    close_calls = 0
    original_close = M7SemanticRuntimeSnapshot.close

    def injected_failure(phase: str) -> Exception:
        if fault_kind == "unprintable_value":
            return _UnprintableValueError()
        return RuntimeError(f"injected {phase} release failure")

    def counted_close(self):  # type: ignore[no-untyped-def]
        nonlocal close_calls
        close_calls += 1
        original_close(self)
        if fault_phase == "snapshot":
            raise injected_failure("snapshot")

    monkeypatch.setattr(M7SemanticRuntimeSnapshot, "close", counted_close)
    release_by_phase = {
        "frontier_inputs": "_release_prepared_frontier_batch_inputs",
        "source_leases": "_release_prepared_layout_source_leases",
        "mutation_guards": "_restore_prepared_source_mutation_guards",
        "remnant_authorities": "_release_prepared_remnant_measurement_authorities",
    }
    if fault_phase in release_by_phase:
        monkeypatch.setattr(
            compiled_module,
            release_by_phase[fault_phase],
            lambda *_args, **_kwargs: (_ for _ in ()).throw(injected_failure(fault_phase)),
        )

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            prepared_record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            lease = next(iter(prepared_record.source_leases.values()))
            authority = next(iter(prepared_record.remnant_authorities.values()))
            child_ids = (id(lease), id(authority), id(inputs))
            raise sentinel

    assert captured.value is sentinel
    assert child_ids[0] not in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
    assert child_ids[1] not in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
    assert child_ids[2] not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
    assert id(prepared) not in compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
    assert problem.problem.parts is original_parts
    assert verified.candidates[0].placements is original_placements
    assert close_calls == 1


@pytest.mark.parametrize(
    "malformed_ledger",
    ("frontier_inputs", "source_leases", "remnant_authorities"),
)
def test_prepared_parent_teardown_drains_children_with_malformed_ledgers(
    monkeypatch: pytest.MonkeyPatch,
    malformed_ledger: str,
) -> None:
    from yieldforge.baseline.replay import M7SemanticRuntimeSnapshot

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    original_parts = problem.problem.parts
    original_placements = verified.candidates[0].placements
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-malformed-ledger-{malformed_ledger}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: malformed ledger sentinel {malformed_ledger}"
    )
    close_calls = 0
    original_close = M7SemanticRuntimeSnapshot.close

    def counted_close(self):  # type: ignore[no-untyped-def]
        nonlocal close_calls
        close_calls += 1
        original_close(self)

    monkeypatch.setattr(M7SemanticRuntimeSnapshot, "close", counted_close)

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            prepared_record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            lease = next(iter(prepared_record.source_leases.values()))
            authority = next(iter(prepared_record.remnant_authorities.values()))
            child_ids = (id(lease), id(authority), id(inputs))
            if malformed_ledger == "remnant_authorities":
                object.__setattr__(prepared, "_remnant_authorities", object())
            else:
                object.__setattr__(prepared_record, malformed_ledger, object())
            raise sentinel

    assert captured.value is sentinel
    assert child_ids[0] not in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
    assert child_ids[1] not in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
    assert child_ids[2] not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
    assert id(prepared) not in compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
    assert problem.problem.parts is original_parts
    assert verified.candidates[0].placements is original_placements
    assert close_calls == 1


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("owner_state", ("missing", "malformed"))
def test_prepared_capability_requires_immutable_child_owner_and_fallback_drains(
    owner_kind: str,
    owner_state: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-{owner_state}-immutable-owner-{owner_kind}",
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            prepared_record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            lease = next(iter(prepared_record.source_leases.values()))
            authority = next(iter(prepared_record.remnant_authorities.values()))
            child_ids = (id(lease), id(authority), id(inputs))
            owner_registry = {
                "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
                "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
                "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
            }[owner_kind]
            child_id = {
                "frontier_inputs": child_ids[2],
                "source_lease": child_ids[0],
                "remnant": child_ids[1],
            }[owner_kind]
            if owner_state == "missing":
                owner_registry.pop(child_id)
            else:
                owner_registry[child_id] = object()  # type: ignore[assignment]
            with pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="immutable child owner binding",
            ):
                compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                    inputs,
                    prepared=prepared,
                    runtime=runtime,
                    event_position=1,
                )

    assert child_ids[0] not in compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY  # noqa: SLF001
    assert child_ids[1] not in compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY  # noqa: SLF001
    assert child_ids[2] not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize(
    "reference_kind",
    (
        "owner_child",
        "owner_prepared",
        "owner_prepared_id",
        "owner_pid",
        "owner_token",
        "main_child",
    ),
)
def test_prepared_teardown_drains_children_with_exploding_weak_reference(
    owner_kind: str,
    reference_kind: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-exploding-{reference_kind}-{owner_kind}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: exploding {reference_kind} {owner_kind}"
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            prepared_record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            lease = next(iter(prepared_record.source_leases.values()))
            authority = next(iter(prepared_record.remnant_authorities.values()))
            child = {
                "frontier_inputs": inputs,
                "source_lease": lease,
                "remnant": authority,
            }[owner_kind]
            owner_registry = {
                "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
                "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
                "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
            }[owner_kind]
            main_registry = {
                "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
                "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
                "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
            }[owner_kind]
            child_id = id(child)
            if reference_kind == "owner_child":
                owner_registry[child_id] = replace(
                    _trusted_registry_value(owner_registry, child_id),
                    child_reference=_ExplodingWeakRef(child),
                )
            elif reference_kind == "owner_prepared":
                owner_registry[child_id] = replace(
                    _trusted_registry_value(owner_registry, child_id),
                    prepared_reference=_ExplodingWeakRef(prepared),
                )
            elif reference_kind == "owner_prepared_id":
                owner_registry[child_id] = replace(
                    _trusted_registry_value(owner_registry, child_id),
                    prepared_id=object(),  # type: ignore[arg-type]
                )
            elif reference_kind == "owner_pid":
                owner_registry[child_id] = replace(
                    _trusted_registry_value(owner_registry, child_id),
                    owner_pid=object(),  # type: ignore[arg-type]
                )
            elif reference_kind == "owner_token":
                owner_registry[child_id] = replace(
                    _trusted_registry_value(owner_registry, child_id),
                    owner_token=object(),
                )
            else:
                main_registry[child_id] = replace(
                    _trusted_registry_value(main_registry, child_id),
                    reference=_ExplodingWeakRef(child),
                )
            raise sentinel

    assert captured.value is sentinel
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_ignores_drifting_non_integer_owner_key(
    owner_kind: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-drifting-owner-key-{owner_kind}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: drifting owner key {owner_kind}"
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            owner_registry = {
                "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
                "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
                "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
            }[owner_kind]
            drifting_key = _FlipHash()
            owner_registry[drifting_key] = object()  # type: ignore[index, assignment]
            drifting_key.armed = True
            raise sentinel

    assert captured.value is sentinel
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_sanitizes_colliding_non_integer_main_key(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    def issue_children(stack: ExitStack, runtime, token: str):  # type: ignore[no-untyped-def]
        binding = runtime.replay_input.instances[1]
        item = inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token=token,
        )
        prepared = stack.enter_context(
            compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            )
        )
        inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
            remnants=(item.remnant,),
        )
        prepared_record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
        )
        lease = next(iter(prepared_record.source_leases.values()))
        authority = next(iter(prepared_record.remnant_authorities.values()))
        return {
            "frontier_inputs": inputs,
            "source_lease": lease,
            "remnant": authority,
        }

    owner_registry = {
        "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
        "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
        "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
    }[owner_kind]
    main_registry = {
        "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
        "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
        "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
    }[owner_kind]
    local_stack = ExitStack()
    foreign_stack = ExitStack()
    local_child = None
    foreign_child = None
    try:
        local_children = issue_children(
            local_stack,
            local_runtime,
            f"prepared-colliding-main-local-{owner_kind}",
        )
        foreign_children = issue_children(
            foreign_stack,
            foreign_runtime,
            f"prepared-colliding-main-foreign-{owner_kind}",
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        local_id = id(local_child)
        foreign_id = id(foreign_child)
        local_record = main_registry.pop(local_id)
        malformed_key = _CollidingExplodingKey(local_id)
        main_registry[malformed_key] = local_record  # type: ignore[index]

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert all(type(key) is int for key in main_registry)
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        foreign_stack.close()
    finally:
        exact_main_entries = tuple(
            (key, value) for key, value in main_registry.items() if type(key) is int
        )
        main_registry.clear()
        main_registry.update(exact_main_entries)
        if local_child is not None:
            owner_registry.pop(id(local_child), None)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "registry_role",
    (
        "parent",
        "frontier_inputs_main",
        "frontier_inputs_owner",
        "source_lease_main",
        "source_lease_owner",
        "remnant_main",
        "remnant_owner",
    ),
)
def test_prepared_registry_denies_equal_alias_dict_base_bypass(
    registry_role: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()

    with ExitStack() as stack:
        prepared, _inputs, children = _issue_prepared_children(
            stack,
            runtime,
            token=f"prepared-equal-alias-base-bypass-{registry_role}",
        )
        if registry_role == "parent":
            registry = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
            issued_id = id(prepared)
            child = None
            owner_kind = None
        else:
            owner_kind, registry_kind = registry_role.rsplit("_", maxsplit=1)
            main_registry, owner_registry = _prepared_child_registries(owner_kind)
            registry = main_registry if registry_kind == "main" else owner_registry
            child = children[owner_kind]
            issued_id = id(child)
        issued = _trusted_registry_value(registry, issued_id)
        equal_alias = _EqualIntegerAlias(issued_id)

        assert not isinstance(registry, dict)
        with pytest.raises(TypeError):
            dict.pop(registry, issued_id)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            dict.__setitem__(registry, equal_alias, issued)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            dict.__delitem__(registry, issued_id)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            dict.clear(registry)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            dict.update(registry, {equal_alias: issued})  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            dict.setdefault(registry, equal_alias, issued)  # type: ignore[arg-type]
        detached = dict(registry._trusted_items())  # noqa: SLF001
        detached.pop(issued_id)
        detached[equal_alias] = issued
        assert _trusted_registry_value(registry, issued_id) is issued
        if registry_role == "parent":
            assert (
                compiled_module._require_prepared_translation_layout_record(  # noqa: SLF001
                    prepared,
                    runtime,
                )
                is issued
            )
        else:
            assert owner_kind is not None
            assert child is not None
            main_registry, _owner_registry = _prepared_child_registries(owner_kind)
            assert (
                _require_prepared_child_capability(
                    owner_kind,
                    child=child,
                    prepared=prepared,
                    runtime=runtime,
                    registered=_trusted_registry_value(main_registry, issued_id),
                )
                is not None
            )

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "registry_role",
    (
        "parent",
        "frontier_inputs_main",
        "frontier_inputs_owner",
        "source_lease_main",
        "source_lease_owner",
        "remnant_main",
        "remnant_owner",
    ),
)
def test_issued_record_class_drift_is_repaired_before_local_teardown(
    registry_role: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    drifted_record = None
    original_record_type = None
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-record-class-foreign-{registry_role}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-record-class-local-{registry_role}",
        )
        if registry_role == "parent":
            registry = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
            local_id = id(local_prepared)
            local_child = None
            local_registered = None
            owner_kind = None
        else:
            owner_kind, registry_kind = registry_role.rsplit("_", maxsplit=1)
            main_registry, owner_registry = _prepared_child_registries(owner_kind)
            registry = main_registry if registry_kind == "main" else owner_registry
            local_child = local_children[owner_kind]
            local_id = id(local_child)
            local_registered = _trusted_registry_value(main_registry, local_id)
            local_call_record = replace(local_registered)
        drifted_record = _trusted_registry_value(registry, local_id)
        original_record_type = _drift_to_same_layout_class(
            drifted_record,
            hostile_getattribute=True,
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            if registry_role == "parent":
                local_prepared.require_active(local_runtime)
            else:
                assert owner_kind is not None
                _require_prepared_child_capability(
                    owner_kind,
                    child=local_child,
                    prepared=local_prepared,
                    runtime=local_runtime,
                    registered=local_call_record,
                )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert type(drifted_record) is original_record_type
        assert local_id not in registry
        foreign_prepared.require_active(foreign_runtime)
        for foreign_owner_kind, foreign_child in foreign_children.items():
            main_registry, owner_registry = _prepared_child_registries(foreign_owner_kind)
            foreign_id = id(foreign_child)
            assert foreign_id in main_registry
            assert foreign_id in owner_registry
            assert (
                _require_prepared_child_capability(
                    foreign_owner_kind,
                    child=foreign_child,
                    prepared=foreign_prepared,
                    runtime=foreign_runtime,
                    registered=_trusted_registry_value(main_registry, foreign_id),
                )
                is not None
            )
        foreign_stack.close()
    finally:
        if (
            drifted_record is not None
            and original_record_type is not None
            and type(drifted_record) is not original_record_type
        ):
            object.__setattr__(drifted_record, "__class__", original_record_type)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "corruption",
    (
        "record_snapshot",
        "snapshot_state",
        "snapshot_class",
        "source_leases",
        "frontier_inputs",
        "remnant_authorities",
    ),
)
def test_parent_lifecycle_roots_reject_drift_and_close_original_snapshot_once(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.baseline.replay import M7SemanticRuntimeSnapshot

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: lifecycle root {corruption}"
    )
    closed_snapshot_ids: list[int] = []
    original_close = M7SemanticRuntimeSnapshot.close

    def counted_close(self):  # type: ignore[no-untyped-def]
        closed_snapshot_ids.append(id(self))
        original_close(self)

    monkeypatch.setattr(M7SemanticRuntimeSnapshot, "close", counted_close)

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with ExitStack() as stack:
            prepared, _inputs, children = _issue_prepared_children(
                stack,
                runtime,
                token=f"prepared-lifecycle-root-{corruption}",
            )
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            snapshot = record.source_runtime_snapshot
            snapshot_id = id(snapshot)
            snapshot_type = type(snapshot)
            source_leases = record.source_leases
            frontier_inputs = record.frontier_inputs
            remnant_authorities = record.remnant_authorities
            if corruption == "record_snapshot":
                object.__setattr__(
                    record,
                    "source_runtime_snapshot",
                    replace(snapshot, _owner_pid=-1, _jagua_private=None),
                )
            elif corruption == "snapshot_state":
                object.__setattr__(snapshot, "_owner_pid", -1)
                object.__setattr__(snapshot, "_jagua_private", None)
            elif corruption == "snapshot_class":
                _drift_to_same_layout_class(
                    snapshot,
                    hostile_getattribute=True,
                )
            elif corruption == "source_leases":
                object.__setattr__(record, "source_leases", {})
            elif corruption == "frontier_inputs":
                object.__setattr__(record, "frontier_inputs", {})
            else:
                object.__setattr__(prepared, "_remnant_authorities", object())

            with pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                prepared.require_active(runtime, deep=True)
            raise sentinel

    assert captured.value is sentinel
    assert type(snapshot) is snapshot_type
    assert closed_snapshot_ids.count(snapshot_id) == 1
    assert record.source_runtime_snapshot is snapshot
    assert record.source_leases is source_leases
    assert record.frontier_inputs is frontier_inputs
    assert record.remnant_authorities is remnant_authorities
    audit_authorities = object.__getattribute__(prepared, "_remnant_authorities")  # noqa: SLF001
    assert audit_authorities == {}
    assert audit_authorities is not remnant_authorities
    assert not source_leases
    assert not frontier_inputs
    assert not remnant_authorities
    for owner_kind, child in children.items():
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        assert id(child) not in main_registry
        assert id(child) not in owner_registry
    assert _prepared_registry_key_sets() == before_registry_keys


def test_issued_parent_source_binding_toctou_cannot_return_forged_geometry() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()
    manager = compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    )
    prepared = manager.__enter__()
    record = _trusted_registry_value(
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
    )  # noqa: SLF001
    original = {
        name: getattr(record, name)
        for name in (
            "source_bindings",
            "layout_footprints",
            "layouts",
            "source_key_fingerprints",
        )
    }

    key, footprints = record.layout_footprints[0]
    _, layouts = record.layouts[0]
    source_binding = dict(record.source_bindings)[key]
    standard_winner = dict(record.standard_winners)[key]
    rejection_problem = dict(record.rejection_problems).get(key)
    first_footprint = footprints[0]
    forged_first_footprint = replace(
        first_footprint,
        bounds=(
            first_footprint.bounds[0],
            first_footprint.bounds[1],
            first_footprint.bounds[2] + 777.0,
            first_footprint.bounds[3],
        ),
    )
    forged_footprints = (forged_first_footprint, *footprints[1:])
    forged_layouts = (
        replace(layouts[0], bounds=forged_first_footprint.bounds),
        *layouts[1:],
    )
    forged_key_fingerprint = compiled_module._prepared_translation_layout_key_fingerprint(  # noqa: SLF001
        prepared,
        key=key,
        source_binding=source_binding,
        layout_footprints=forged_footprints,
        layouts=forged_layouts,
        rejection_problem=rejection_problem,
        standard_winner=standard_winner,
    )

    class Trigger(list):
        def __iter__(self):  # type: ignore[no-untyped-def]
            object.__setattr__(
                record,
                "layout_footprints",
                ((key, forged_footprints),),
            )
            object.__setattr__(record, "layouts", ((key, forged_layouts),))
            object.__setattr__(
                record,
                "source_key_fingerprints",
                ((key, forged_key_fingerprint),),
            )
            return list.__iter__(self)

    object.__setattr__(
        record,
        "source_bindings",
        Trigger(original["source_bindings"]),
    )
    try:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            compiled_module._prepared_layout_footprints(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )
    finally:
        for name, value in original.items():
            object.__setattr__(record, name, value)
        with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError):
            manager.__exit__(None, None, None)

    assert _prepared_registry_key_sets() == before_registry_keys


def test_transient_source_lease_erasure_remains_auditable_at_batch_exit() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            compiled_module._prepared_layout_footprints(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            record.source_leases.clear()
            gc.collect()

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("corruption", ("rates", "rules"))
def test_snapshot_runtime_semantic_drift_is_rejected_before_consumer_use(
    corruption: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            snapshot_runtime = record.source_runtime_snapshot.runtime
            if corruption == "rates":
                rates = snapshot_runtime.replay_input.rates
                object.__setattr__(
                    snapshot_runtime.replay_input,
                    "rates",
                    rates.model_copy(
                        update={"purchase_cost_per_area": (rates.purchase_cost_per_area + 777.0)}
                    ),
                )
            else:
                primary = snapshot_runtime.rules.primary.model_copy(
                    update={"minimum_area_sheet_fraction": 0.999}
                )
                object.__setattr__(
                    snapshot_runtime,
                    "rules",
                    snapshot_runtime.rules.model_copy(update={"primary": primary}),
                )
            compiled_module._prepared_source_runtime(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "cache_name",
    ("standard_profile_cache", "fit_search_cache", "prepared_layout_cache"),
)
def test_issued_snapshot_operational_cache_poison_is_rejected(
    cache_name: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            snapshot_runtime = record.source_runtime_snapshot.runtime
            cache = getattr(snapshot_runtime, cache_name)
            if cache_name == "standard_profile_cache":
                winner = record.standard_winners[0][1]
                profile = winner.standard_profiles[0]
                cache[(winner.problem_id, profile.candidate_id)] = replace(
                    profile,
                    returned_regularity=0.0,
                )
            elif cache_name == "fit_search_cache":
                cache[("forged-remnant", "forged-problem", "forged-candidates")] = ()
            else:
                cache[("forged-problem", "forged-candidates")] = ()
            compiled_module._prepared_source_runtime(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )

    assert _prepared_registry_key_sets() == before_registry_keys


def test_issued_source_lease_cannot_reach_private_operational_runtime() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()

    with ExitStack() as stack:
        prepared, _inputs, children = _issue_prepared_children(
            stack,
            runtime,
            token="prepared-source-lease-runtime-isolation",
        )
        lease = children["source_lease"]
        issued = _trusted_registry_value(
            compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY, id(lease)
        )
        assert not hasattr(issued, "operational_runtime")
        consumed = compiled_module._prepared_source_runtime(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )
        record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
        )
        assert consumed is not record.source_runtime_snapshot.runtime

    assert _prepared_registry_key_sets() == before_registry_keys


def test_issued_snapshot_toctou_cannot_reach_consumed_runtime() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            snapshot_runtime = record.source_runtime_snapshot.runtime
            candidate = next(
                candidate
                for verified in snapshot_runtime.runtime_candidates.values()
                for candidate in verified.candidates
            )
            original_dump = candidate.model_dump
            original_rates = snapshot_runtime.replay_input.rates

            def trigger(*args, **kwargs):  # type: ignore[no-untyped-def]
                object.__delattr__(candidate, "model_dump")
                payload = original_dump(*args, **kwargs)
                object.__setattr__(
                    snapshot_runtime.replay_input,
                    "rates",
                    original_rates.model_copy(
                        update={
                            "purchase_cost_per_area": (
                                original_rates.purchase_cost_per_area + 777.0
                            )
                        }
                    ),
                )
                return payload

            object.__setattr__(candidate, "model_dump", trigger)
            consumed_runtime = compiled_module._prepared_source_runtime(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )
            assert consumed_runtime is not snapshot_runtime
            assert (
                consumed_runtime.replay_input.rates.purchase_cost_per_area
                == original_rates.purchase_cost_per_area
            )

    assert _prepared_registry_key_sets() == before_registry_keys


def test_remnant_semantic_child_forge_is_rejected() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="prepared-remnant-semantic-child-forge",
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            canonical = compiled_module._registered_prepared_remnant_measurement(  # noqa: SLF001
                prepared,
                runtime,
                item.remnant,
            )
            key = compiled_module._prepared_remnant_semantic_key(item.remnant)  # noqa: SLF001
            authority = record.remnant_authorities[key]
            forged_area = canonical.area + 777.0
            object.__setattr__(canonical, "area", forged_area)
            object.__setattr__(
                canonical,
                "bounds",
                (0.0, 0.0, 1.0, forged_area),
            )
            forged_values = compiled_module._prepared_remnant_measurement_values(  # noqa: SLF001
                canonical
            )
            forged_commitment = compiled_module._prepared_remnant_measurement_commitment(  # noqa: SLF001
                key,
                canonical,
            )
            object.__setattr__(authority, "measurement_values", forged_values)
            object.__setattr__(authority, "commitment", forged_commitment)
            record.remnant_commitments[key] = forged_commitment
            record.remnant_snapshots.clear()
            record.remnant_snapshots[forged_commitment] = (
                authority.key_values,
                forged_values,
            )
            compiled_module._registered_prepared_remnant_measurement(  # noqa: SLF001
                prepared,
                runtime,
                item.remnant,
            )

    assert _prepared_registry_key_sets() == before_registry_keys


def test_coherent_remnant_authority_erasure_remains_auditable() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="prepared-remnant-authority-erasure",
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            record = _trusted_registry_value(
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
            )
            compiled_module._registered_prepared_remnant_measurement(  # noqa: SLF001
                prepared,
                runtime,
                item.remnant,
            )
            record.remnant_authorities.clear()
            record.remnant_measurements.clear()
            record.remnant_commitments.clear()
            record.remnant_snapshots.clear()
            gc.collect()

    assert _prepared_registry_key_sets() == before_registry_keys


def test_parent_teardown_restores_nested_jagua_cleanup_authority(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "fake-jagua"
    _write_collision_jagua(executable, collision=False)
    backend = "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
    foreign_runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend=backend,
        jagua_executable=executable,
    )
    local_runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend=backend,
        jagua_executable=executable,
    )
    before_registry_keys = _prepared_registry_key_sets()
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    foreign_snapshot = None
    local_snapshot = None
    local_private = None
    local_identity = None
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token="prepared-jagua-foreign",
        )
        local_prepared, _local_inputs, _local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token="prepared-jagua-local",
        )
        foreign_record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(foreign_prepared)
        )
        local_record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(local_prepared)
        )
        foreign_snapshot = foreign_record.source_runtime_snapshot
        local_snapshot = local_record.source_runtime_snapshot
        foreign_private = foreign_snapshot._jagua_private  # noqa: SLF001
        local_private = local_snapshot._jagua_private  # noqa: SLF001
        assert foreign_private is not None
        assert local_private is not None
        foreign_path = foreign_private.executable.path
        local_path = local_private.executable.path
        local_directory = local_private.directory
        local_identity = local_private.executable
        assert foreign_path != local_path
        assert foreign_path.exists()
        assert local_path.exists()

        object.__setattr__(
            local_private,
            "executable",
            foreign_private.executable,
        )
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert foreign_path.exists()
        assert not local_path.exists()
        assert not local_directory.exists()
        with foreign_snapshot.runtime_for_proof():
            pass
        foreign_prepared.require_active(foreign_runtime, deep=True)
        for owner_kind, child in foreign_children.items():
            main_registry, owner_registry = _prepared_child_registries(owner_kind)
            assert id(child) in main_registry
            assert id(child) in owner_registry
        foreign_stack.close()
    finally:
        if local_private is not None and local_identity is not None:
            object.__setattr__(local_private, "executable", local_identity)
        if local_snapshot is not None:
            type(local_snapshot).close(local_snapshot)
        if foreign_snapshot is not None:
            type(foreign_snapshot).close(foreign_snapshot)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


def test_parent_teardown_drains_nested_jagua_path_class_drift(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "fake-jagua-path-drift"
    _write_collision_jagua(executable, collision=False)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
    )
    before_registry_keys = _prepared_registry_key_sets()
    manager = compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    )
    prepared = manager.__enter__()
    record = _trusted_registry_value(
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
    )  # noqa: SLF001
    snapshot = record.source_runtime_snapshot
    private = snapshot._jagua_private  # noqa: SLF001
    assert private is not None
    path = private.executable.path
    path_type = type(path)
    path_text = str(path)
    directory_text = str(private.directory)

    class HostilePath(path_type):
        __slots__ = ()

        def __fspath__(self) -> str:
            raise RuntimeError("injected hostile private Jagua path")

    object.__setattr__(path, "__class__", HostilePath)
    try:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            manager.__exit__(None, None, None)
        assert not path_type(path_text).exists()
        assert not path_type(directory_text).exists()
    finally:
        if type(path) is not path_type:
            object.__setattr__(path, "__class__", path_type)
        type(snapshot).close(snapshot)

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "corruption",
    ("source_binding", "fit_config", "event_materials"),
)
def test_persistent_source_lease_semantic_drift_is_rejected_at_batch_exit(
    corruption: str,
) -> None:
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()

    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ) as prepared:
                registered, _material = compiled_module._prepared_layout_source_lease_and_inputs(  # noqa: SLF001
                    prepared,
                    runtime,
                    event_position=1,
                )
                if corruption == "source_binding":
                    object.__setattr__(
                        registered,
                        "source_binding",
                        replace(
                            registered.source_binding,
                            candidate_set_sha256="sha256:" + "f" * 64,
                        ),
                    )
                    object.__setattr__(registered, "rejection_problem", None)
                elif corruption == "fit_config":
                    object.__setattr__(
                        registered,
                        "fit_config",
                        registered.fit_config.model_copy(
                            update={
                                "clearance_distance": (
                                    registered.fit_config.clearance_distance + 1.0
                                )
                            }
                        ),
                    )
                else:
                    event_materials = list(registered.event_materials)
                    event_position = next(
                        index
                        for index, material in enumerate(event_materials)
                        if material is not None
                    )
                    material = event_materials[event_position]
                    assert material is not None
                    event_materials[event_position] = material.model_copy(
                        update={"grade": f"{material.grade}-forged"}
                    )
                    object.__setattr__(
                        registered,
                        "event_materials",
                        tuple(event_materials),
                    )

    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("key_kind", ("colliding", "drifting", "wrong_integer"))
@pytest.mark.parametrize("relocation_kind", ("move", "alias"))
def test_prepared_parent_teardown_sanitizes_malformed_registry_key_failure_atomically(
    monkeypatch: pytest.MonkeyPatch,
    key_kind: str,
    relocation_kind: str,
) -> None:
    from yieldforge.baseline.replay import M7SemanticRuntimeSnapshot

    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_binding = local_runtime.replay_input.instances[1]
    local_verified = local_runtime.runtime_candidates[local_binding.problem_id]
    local_problem = next(
        problem
        for problem in local_runtime.replay_input.problems
        if problem.problem_id == local_binding.problem_id
    )
    original_parts = local_problem.problem.parts
    original_placements = local_verified.candidates[0].placements
    close_ids: list[int] = []
    original_close = M7SemanticRuntimeSnapshot.close

    def counted_close(self):  # type: ignore[no-untyped-def]
        close_ids.append(id(self))
        original_close(self)

    monkeypatch.setattr(M7SemanticRuntimeSnapshot, "close", counted_close)
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: malformed parent key {key_kind} {relocation_kind}"
    )

    with ExitStack() as foreign_stack:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-parent-key-foreign-{key_kind}",
        )
        foreign_parent_id = id(foreign_prepared)
        foreign_child_ids = tuple(id(child) for child in foreign_children.values())
        local_snapshot_id = 0
        with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
            with ExitStack() as local_stack:
                local_prepared, _local_inputs, local_children = _issue_prepared_children(
                    local_stack,
                    local_runtime,
                    token=f"prepared-parent-key-local-{key_kind}-{relocation_kind}",
                )
                local_parent_id = id(local_prepared)
                local_child_ids = tuple(id(child) for child in local_children.values())
                local_record = _trusted_registry_value(
                    compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, local_parent_id
                )
                local_snapshot_id = id(local_record.source_runtime_snapshot)
                if key_kind == "colliding":
                    malformed_key = _CollidingExplodingKey(
                        local_parent_id,
                        armed=relocation_kind == "move",
                    )
                elif key_kind == "drifting":
                    malformed_key = _FlipHash()
                else:
                    malformed_key = local_parent_id + 1_000_000_007
                    while (
                        malformed_key in compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
                    ):
                        malformed_key += 1
                if relocation_kind == "move":
                    compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
                        local_parent_id
                    )
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # type: ignore[index]  # noqa: SLF001
                    malformed_key
                ] = local_record
                if key_kind in {"colliding", "drifting"}:
                    malformed_key.armed = True
                raise sentinel

        assert captured.value is sentinel
        assert close_ids.count(local_snapshot_id) == 1
        assert local_problem.problem.parts is original_parts
        assert local_verified.candidates[0].placements is original_placements
        assert local_parent_id not in compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
        for local_id, main_registry, owner_registry in zip(
            local_child_ids,
            (
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
            ),
            (
                compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
            ),
            strict=True,
        ):
            assert local_id not in main_registry
            assert local_id not in owner_registry
        assert foreign_parent_id in compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
        for foreign_id, main_registry, owner_registry in zip(
            foreign_child_ids,
            (
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
            ),
            (
                compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
                compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
            ),
            strict=True,
        ):
            assert foreign_id in main_registry
            assert foreign_id in owner_registry
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )

    assert len(close_ids) == 2
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_preserves_foreign_exact_main_on_sidecar_owner_conflict(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-main-owner-conflict-foreign-{owner_kind}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-main-owner-conflict-local-{owner_kind}",
        )
        foreign_child = foreign_children[owner_kind]
        local_child = local_children[owner_kind]
        child_id = id(foreign_child)
        owner_registry = {
            "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
            "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
            "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
        }[owner_kind]
        main_registry = {
            "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
            "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
            "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
        }[owner_kind]
        original_binding = _trusted_registry_value(owner_registry, child_id)
        original_owner_token = foreign_child._owner_token  # noqa: SLF001
        owner_registry[child_id] = replace(
            original_binding,
            prepared_reference=weakref.ref(local_prepared),
            prepared_id=id(local_prepared),
            owner_token=local_prepared._owner_token,  # noqa: SLF001
        )
        object.__setattr__(
            foreign_child,
            "_owner_token",
            local_prepared._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert id(local_child) not in main_registry
        assert id(local_child) not in owner_registry
        assert child_id in main_registry
        assert child_id in owner_registry
        assert _trusted_registry_value(main_registry, child_id).prepared is foreign_prepared
        owner_registry[child_id] = original_binding
        object.__setattr__(foreign_child, "_owner_token", original_owner_token)
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_preserves_credible_foreign_main_when_parent_record_missing(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    foreign_parent_record = None
    original_binding = None
    child_id = 0
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-missing-foreign-parent-{owner_kind}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-missing-foreign-parent-local-{owner_kind}",
        )
        foreign_child = foreign_children[owner_kind]
        local_child = local_children[owner_kind]
        child_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        original_binding = _trusted_registry_value(owner_registry, child_id)
        foreign_parent_record = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
            id(foreign_prepared)
        )
        owner_registry[child_id] = replace(
            original_binding,
            prepared_reference=weakref.ref(local_prepared),
            prepared_id=id(local_prepared),
            owner_token=local_prepared._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert id(local_child) not in main_registry
        assert id(local_child) not in owner_registry
        assert child_id in main_registry
        assert child_id in owner_registry
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # noqa: SLF001
            id(foreign_prepared)
        ] = foreign_parent_record
        owner_registry[child_id] = original_binding
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
        foreign_parent_record = None
        original_binding = None
        child_id = 0
    finally:
        if foreign_parent_record is not None:
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.setdefault(  # noqa: SLF001
                id(foreign_prepared),
                foreign_parent_record,
            )
        if original_binding is not None and child_id:
            _main_registry, owner_registry = _prepared_child_registries(owner_kind)
            owner_registry.setdefault(child_id, original_binding)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_canonical_foreign_main_beats_forged_local_alias(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    foreign_parent_record = None
    wrong_key = 0
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-canonical-foreign-main-{owner_kind}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-forged-local-main-alias-{owner_kind}",
        )
        foreign_child = foreign_children[owner_kind]
        local_child = local_children[owner_kind]
        child_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        foreign_parent_record = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
            id(foreign_prepared)
        )
        wrong_key = child_id + 1_000_000_007
        while wrong_key in main_registry:
            wrong_key += 1
        main_registry[wrong_key] = replace(
            _trusted_registry_value(main_registry, child_id),
            prepared=local_prepared,
            owner_token=local_prepared._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert id(local_child) not in main_registry
        assert id(local_child) not in owner_registry
        assert child_id in main_registry
        assert child_id in owner_registry
        assert wrong_key not in main_registry
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # noqa: SLF001
            id(foreign_prepared)
        ] = foreign_parent_record
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
        foreign_parent_record = None
        wrong_key = 0
    finally:
        if foreign_parent_record is not None:
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.setdefault(  # noqa: SLF001
                id(foreign_prepared),
                foreign_parent_record,
            )
        if wrong_key:
            main_registry, _owner_registry = _prepared_child_registries(owner_kind)
            main_registry.pop(wrong_key, None)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_canonical_foreign_sidecar_beats_forged_local_alias(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    foreign_parent_record = None
    foreign_main_record = None
    wrong_key = 0
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-canonical-foreign-sidecar-{owner_kind}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-forged-local-sidecar-alias-{owner_kind}",
        )
        foreign_child = foreign_children[owner_kind]
        local_child = local_children[owner_kind]
        child_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        foreign_parent_record = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
            id(foreign_prepared)
        )
        foreign_main_record = main_registry.pop(child_id)
        wrong_key = child_id + 1_000_000_007
        while wrong_key in owner_registry:
            wrong_key += 1
        owner_registry[wrong_key] = replace(
            _trusted_registry_value(owner_registry, child_id),
            prepared_reference=weakref.ref(local_prepared),
            prepared_id=id(local_prepared),
            owner_token=local_prepared._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert id(local_child) not in main_registry
        assert id(local_child) not in owner_registry
        assert child_id in main_registry
        assert child_id in owner_registry
        assert wrong_key not in owner_registry
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # noqa: SLF001
            id(foreign_prepared)
        ] = foreign_parent_record
        main_registry[child_id] = foreign_main_record
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
        foreign_parent_record = None
        foreign_main_record = None
        wrong_key = 0
    finally:
        if foreign_parent_record is not None:
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.setdefault(  # noqa: SLF001
                id(foreign_prepared),
                foreign_parent_record,
            )
        if foreign_main_record is not None:
            main_registry, _owner_registry = _prepared_child_registries(owner_kind)
            main_registry.setdefault(child_id, foreign_main_record)
        if wrong_key:
            _main_registry, owner_registry = _prepared_child_registries(owner_kind)
            owner_registry.pop(wrong_key, None)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_native_local_child_beats_forged_foreign_sidecar(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-native-local-foreign-peer-{owner_kind}",
        )
        _local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-native-local-child-{owner_kind}",
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        local_id = id(local_child)
        foreign_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        main_registry.pop(local_id)
        owner_registry[local_id] = replace(
            _trusted_registry_value(owner_registry, local_id),
            prepared_reference=weakref.ref(foreign_prepared),
            prepared_id=id(foreign_prepared),
            owner_token=foreign_prepared._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_canonical_local_sidecar_beats_forged_alias_agreement(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    wrong_key = 0
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-alias-agreement-foreign-{owner_kind}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-alias-agreement-local-{owner_kind}",
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        local_id = id(local_child)
        foreign_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        local_main = main_registry.pop(local_id)
        wrong_key = local_id + 1_000_000_007
        while wrong_key in main_registry or wrong_key in owner_registry:
            wrong_key += 1
        main_registry[wrong_key] = replace(
            local_main,
            prepared=foreign_prepared,
            owner_token=foreign_prepared._owner_token,  # noqa: SLF001
        )
        owner_registry[wrong_key] = replace(
            _trusted_registry_value(owner_registry, local_id),
            prepared_reference=weakref.ref(foreign_prepared),
            prepared_id=id(foreign_prepared),
            owner_token=foreign_prepared._owner_token,  # noqa: SLF001
        )
        object.__setattr__(local_child, "_owner_token", object())

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert wrong_key not in main_registry
        assert wrong_key not in owner_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
        wrong_key = 0
    finally:
        if wrong_key:
            main_registry, owner_registry = _prepared_child_registries(owner_kind)
            main_registry.pop(wrong_key, None)
            owner_registry.pop(wrong_key, None)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("foreign_parent_present", (True, False))
def test_prepared_teardown_local_issuance_token_rejects_forged_foreign_parent(
    owner_kind: str,
    foreign_parent_present: bool,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    foreign_parent_record = None
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-reverse-main-foreign-{owner_kind}",
        )
        _local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-reverse-main-local-{owner_kind}",
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        local_id = id(local_child)
        foreign_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        owner_registry.pop(local_id)
        main_registry[local_id] = replace(
            _trusted_registry_value(main_registry, local_id),
            prepared=foreign_prepared,
        )
        if not foreign_parent_present:
            foreign_parent_record = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
                id(foreign_prepared)
            )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        if foreign_parent_record is not None:
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # noqa: SLF001
                id(foreign_prepared)
            ] = foreign_parent_record
            foreign_parent_record = None
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
    finally:
        if foreign_parent_record is not None:
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.setdefault(  # noqa: SLF001
                id(foreign_prepared),
                foreign_parent_record,
            )
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("registry_kind", ("main", "owner"))
def test_prepared_teardown_dead_wrong_key_alias_cannot_override_foreign_canonical(
    owner_kind: str,
    registry_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    foreign_parent_record = None
    wrong_key = 0
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-dead-alias-foreign-{registry_kind}-{owner_kind}",
        )
        local_prepared, _local_inputs, _local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-dead-alias-local-{registry_kind}-{owner_kind}",
        )
        foreign_child = foreign_children[owner_kind]
        foreign_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        foreign_parent_record = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
            id(foreign_prepared)
        )
        wrong_key = foreign_id + 1_000_000_007
        selected_registry = main_registry if registry_kind == "main" else owner_registry
        while wrong_key in selected_registry:
            wrong_key += 1
        dead_target = _DeadWeakTarget()
        dead_reference = weakref.ref(dead_target)
        del dead_target
        gc.collect()
        assert dead_reference() is None
        if registry_kind == "main":
            main_registry[wrong_key] = replace(
                _trusted_registry_value(main_registry, foreign_id),
                reference=dead_reference,
                prepared=local_prepared,
                owner_token=local_prepared._owner_token,  # noqa: SLF001
            )
        else:
            owner_registry[wrong_key] = replace(
                _trusted_registry_value(owner_registry, foreign_id),
                child_reference=dead_reference,
                prepared_reference=weakref.ref(local_prepared),
                prepared_id=id(local_prepared),
                owner_token=local_prepared._owner_token,  # noqa: SLF001
            )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert wrong_key not in selected_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # noqa: SLF001
            id(foreign_prepared)
        ] = foreign_parent_record
        foreign_parent_record = None
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
        wrong_key = 0
    finally:
        if foreign_parent_record is not None:
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY.setdefault(  # noqa: SLF001
                id(foreign_prepared),
                foreign_parent_record,
            )
        if wrong_key:
            main_registry, owner_registry = _prepared_child_registries(owner_kind)
            selected_registry = main_registry if registry_kind == "main" else owner_registry
            selected_registry.pop(wrong_key, None)
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("mutation_kind", ("overwrite", "move"))
def test_prepared_parent_hot_lookup_rejects_equal_non_integer_key_mutation(
    mutation_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    stack = ExitStack()
    try:
        prepared = stack.enter_context(
            compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            )
        )
        registry = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
        prepared_id = id(prepared)
        genuine = _trusted_registry_value(registry, prepared_id)
        if mutation_kind == "move":
            registry.pop(prepared_id)
        registry[_EqualIntegerAlias(prepared_id)] = replace(  # type: ignore[index]
            genuine,
            owner_token=object(),
        )
        if mutation_kind == "overwrite":
            assert _trusted_registry_value(registry, prepared_id) is genuine
        else:
            assert prepared_id not in registry

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared registry lookup",
        ):
            prepared.require_active(runtime)

        registry[prepared_id] = genuine
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            stack.close()
    finally:
        try:
            stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("registry_kind", ("main", "owner"))
@pytest.mark.parametrize("mutation_kind", ("overwrite", "move"))
def test_prepared_child_hot_lookup_rejects_equal_non_integer_key_mutation(
    owner_kind: str,
    registry_kind: str,
    mutation_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=(f"prepared-equal-key-foreign-{mutation_kind}-{registry_kind}-{owner_kind}"),
        )
        local_prepared, _local_inputs, _local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=(f"prepared-equal-key-local-{mutation_kind}-{registry_kind}-{owner_kind}"),
        )
        foreign_child = foreign_children[owner_kind]
        child_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        selected_registry = main_registry if registry_kind == "main" else owner_registry
        genuine = _trusted_registry_value(selected_registry, child_id)
        registered = _trusted_registry_value(main_registry, child_id)
        forged = (
            replace(
                genuine,
                prepared=local_prepared,
                owner_token=local_prepared._owner_token,  # noqa: SLF001
            )
            if registry_kind == "main"
            else replace(
                genuine,
                prepared_reference=weakref.ref(local_prepared),
                prepared_id=id(local_prepared),
                owner_token=local_prepared._owner_token,  # noqa: SLF001
            )
        )
        if mutation_kind == "move":
            selected_registry.pop(child_id)
        selected_registry[_EqualIntegerAlias(child_id)] = forged  # type: ignore[index]
        if mutation_kind == "overwrite":
            assert _trusted_registry_value(selected_registry, child_id) is genuine
        else:
            assert child_id not in selected_registry

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            _require_prepared_child_capability(
                owner_kind,
                child=foreign_child,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                registered=registered,
            )

        selected_registry[child_id] = genuine
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert child_id in main_registry
        assert child_id in owner_registry
        assert _trusted_registry_value(main_registry, child_id) is registered
        assert (
            _require_prepared_child_capability(
                owner_kind,
                child=foreign_child,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                registered=registered,
            )
            is not None
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("registry_kind", ("main", "owner"))
@pytest.mark.parametrize("direction", ("local_to_foreign", "foreign_to_local"))
@pytest.mark.parametrize("mutation_mode", ("replacement", "in_place"))
def test_prepared_registry_issuance_shadow_resolves_symmetric_owner_mutation(
    owner_kind: str,
    registry_kind: str,
    direction: str,
    mutation_mode: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=(
                f"prepared-shadow-foreign-{mutation_mode}-{direction}-{registry_kind}-{owner_kind}"
            ),
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=(
                f"prepared-shadow-local-{mutation_mode}-{direction}-{registry_kind}-{owner_kind}"
            ),
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        local_id = id(local_child)
        foreign_id = id(foreign_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        selected_registry = main_registry if registry_kind == "main" else owner_registry
        victim_child, victim_prepared, victim_runtime, claimed_prepared = (
            (local_child, local_prepared, local_runtime, foreign_prepared)
            if direction == "local_to_foreign"
            else (foreign_child, foreign_prepared, foreign_runtime, local_prepared)
        )
        victim_id = id(victim_child)
        genuine = _trusted_registry_value(selected_registry, victim_id)
        victim_registered = _trusted_registry_value(main_registry, victim_id)
        if registry_kind == "main":
            forged = replace(
                genuine,
                prepared=claimed_prepared,
                owner_token=claimed_prepared._owner_token,  # noqa: SLF001
            )
            if mutation_mode == "replacement":
                selected_registry[victim_id] = forged
            else:
                object.__setattr__(genuine, "prepared", claimed_prepared)
                object.__setattr__(
                    genuine,
                    "owner_token",
                    claimed_prepared._owner_token,  # noqa: SLF001
                )
        else:
            forged = replace(
                genuine,
                prepared_reference=weakref.ref(claimed_prepared),
                prepared_id=id(claimed_prepared),
                owner_token=claimed_prepared._owner_token,  # noqa: SLF001
            )
            if mutation_mode == "replacement":
                selected_registry[victim_id] = forged
            else:
                object.__setattr__(
                    genuine,
                    "prepared_reference",
                    weakref.ref(claimed_prepared),
                )
                object.__setattr__(genuine, "prepared_id", id(claimed_prepared))
                object.__setattr__(
                    genuine,
                    "owner_token",
                    claimed_prepared._owner_token,  # noqa: SLF001
                )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            _require_prepared_child_capability(
                owner_kind,
                child=victim_child,
                prepared=victim_prepared,
                runtime=victim_runtime,
                registered=victim_registered,
            )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        foreign_registered = _trusted_registry_value(main_registry, foreign_id)
        assert (
            _require_prepared_child_capability(
                owner_kind,
                child=foreign_child,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                registered=foreign_registered,
            )
            is not None
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_local_teardown_preserves_issued_foreign_pair_when_native_child_drifts(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-native-drift-foreign-{owner_kind}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-native-drift-local-{owner_kind}",
        )
        foreign_child = foreign_children[owner_kind]
        local_child = local_children[owner_kind]
        foreign_id = id(foreign_child)
        local_id = id(local_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        foreign_main = _trusted_registry_value(main_registry, foreign_id)
        foreign_owner = _trusted_registry_value(owner_registry, foreign_id)
        original_token = foreign_child._token  # noqa: SLF001
        original_owner_token = foreign_child._owner_token  # noqa: SLF001
        object.__setattr__(foreign_child, "_token", object())
        object.__setattr__(
            foreign_child,
            "_owner_token",
            local_prepared._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert _trusted_registry_value(main_registry, foreign_id) is foreign_main
        assert _trusted_registry_value(owner_registry, foreign_id) is foreign_owner
        object.__setattr__(foreign_child, "_token", original_token)
        object.__setattr__(foreign_child, "_owner_token", original_owner_token)
        assert (
            _require_prepared_child_capability(
                owner_kind,
                child=foreign_child,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                registered=foreign_main,
            )
            is not None
        )
        foreign_stack.close()
    finally:
        try:
            object.__setattr__(foreign_child, "_token", original_token)
            object.__setattr__(foreign_child, "_owner_token", original_owner_token)
        except UnboundLocalError:
            pass
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


def test_local_teardown_preserves_issued_foreign_parent_when_native_class_drifts() -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    original_foreign_type = None
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token="prepared-native-parent-class-foreign",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token="prepared-native-parent-class-local",
        )
        foreign_id = id(foreign_prepared)
        local_id = id(local_prepared)
        parent_registry = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
        foreign_record = _trusted_registry_value(parent_registry, foreign_id)
        original_foreign_type = _drift_to_same_layout_class(
            foreign_prepared,
            hostile_getattribute=True,
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in parent_registry
        assert _trusted_registry_value(parent_registry, foreign_id) is foreign_record
        for owner_kind, foreign_child in foreign_children.items():
            main_registry, owner_registry = _prepared_child_registries(owner_kind)
            assert id(local_children[owner_kind]) not in main_registry
            assert id(local_children[owner_kind]) not in owner_registry
            assert id(foreign_child) in main_registry
            assert id(foreign_child) in owner_registry
        object.__setattr__(foreign_prepared, "__class__", original_foreign_type)
        foreign_prepared.require_active(foreign_runtime)
        for owner_kind, foreign_child in foreign_children.items():
            main_registry, _owner_registry = _prepared_child_registries(owner_kind)
            assert (
                _require_prepared_child_capability(
                    owner_kind,
                    child=foreign_child,
                    prepared=foreign_prepared,
                    runtime=foreign_runtime,
                    registered=_trusted_registry_value(main_registry, id(foreign_child)),
                )
                is not None
            )
        foreign_stack.close()
    finally:
        try:
            if (
                original_foreign_type is not None
                and type(foreign_prepared) is not original_foreign_type
            ):
                object.__setattr__(
                    foreign_prepared,
                    "__class__",
                    original_foreign_type,
                )
        except UnboundLocalError:
            pass
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


def test_local_native_parent_class_drift_drains_trusted_lifecycle_ledgers() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    before_registry_keys = _prepared_registry_key_sets()
    stack = ExitStack()
    original_prepared_type = None
    try:
        prepared, _inputs, children = _issue_prepared_children(
            stack,
            runtime,
            token="prepared-native-parent-class-local-drain",
        )
        record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
        )
        source_leases = record.source_leases
        frontier_inputs = record.frontier_inputs
        remnant_authorities = record.remnant_authorities
        original_prepared_type = _drift_to_same_layout_class(
            prepared,
            hostile_getattribute=True,
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            stack.close()

        object.__setattr__(prepared, "__class__", original_prepared_type)
        audit_authorities = object.__getattribute__(prepared, "_remnant_authorities")  # noqa: SLF001
        assert audit_authorities == {}
        assert audit_authorities is not remnant_authorities
        assert not source_leases
        assert not frontier_inputs
        assert not remnant_authorities
        for owner_kind, child in children.items():
            main_registry, owner_registry = _prepared_child_registries(owner_kind)
            assert id(child) not in main_registry
            assert id(child) not in owner_registry
    finally:
        try:
            if original_prepared_type is not None and type(prepared) is not original_prepared_type:
                object.__setattr__(prepared, "__class__", original_prepared_type)
        except UnboundLocalError:
            pass
        try:
            stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_local_teardown_preserves_issued_foreign_child_when_native_class_drifts(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    original_foreign_type = None
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-native-child-class-foreign-{owner_kind}",
        )
        _local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-native-child-class-local-{owner_kind}",
        )
        foreign_child = foreign_children[owner_kind]
        local_child = local_children[owner_kind]
        foreign_id = id(foreign_child)
        local_id = id(local_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        foreign_main = _trusted_registry_value(main_registry, foreign_id)
        foreign_owner = _trusted_registry_value(owner_registry, foreign_id)
        original_foreign_type = _drift_to_same_layout_class(
            foreign_child,
            hostile_getattribute=True,
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert _trusted_registry_value(main_registry, foreign_id) is foreign_main
        assert _trusted_registry_value(owner_registry, foreign_id) is foreign_owner
        object.__setattr__(foreign_child, "__class__", original_foreign_type)
        assert (
            _require_prepared_child_capability(
                owner_kind,
                child=foreign_child,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                registered=foreign_main,
            )
            is not None
        )
        foreign_stack.close()
    finally:
        try:
            if (
                original_foreign_type is not None
                and type(foreign_child) is not original_foreign_type
            ):
                object.__setattr__(
                    foreign_child,
                    "__class__",
                    original_foreign_type,
                )
        except UnboundLocalError:
            pass
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "field_name",
    ("owner_pid", "owner_token", "runtime_id", "layout_fingerprint"),
)
def test_prepared_parent_provenance_normalizes_in_place_diagnostic_drift(
    field_name: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    stack = ExitStack()
    prepared = stack.enter_context(
        compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        )
    )
    record = _trusted_registry_value(
        compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
    )  # noqa: SLF001
    object.__setattr__(record, field_name, _ExplodingDiagnostic())

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        prepared.require_active(runtime)
    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        stack.close()

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("registry_kind", ("main", "owner"))
def test_prepared_child_provenance_normalizes_in_place_owner_pid_drift(
    owner_kind: str,
    registry_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    stack = ExitStack()
    prepared, _inputs, children = _issue_prepared_children(
        stack,
        runtime,
        token=f"prepared-owner-pid-drift-{registry_kind}-{owner_kind}",
    )
    child = children[owner_kind]
    main_registry, owner_registry = _prepared_child_registries(owner_kind)
    selected_registry = main_registry if registry_kind == "main" else owner_registry
    registered = _trusted_registry_value(main_registry, id(child))
    object.__setattr__(
        _trusted_registry_value(selected_registry, id(child)), "owner_pid", _ExplodingDiagnostic()
    )

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        _require_prepared_child_capability(
            owner_kind,
            child=child,
            prepared=prepared,
            runtime=runtime,
            registered=registered,
        )
    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        stack.close()

    assert _prepared_registry_key_sets() == before_registry_keys


def test_prepared_parent_cannot_be_resurrected_after_close() -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        prepared_id = id(prepared)
        saved_record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, prepared_id
        )

    registry = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
    registry[prepared_id] = saved_record
    assert prepared_id not in registry
    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared registry lookup",
    ):
        prepared.require_active(runtime)
    registry.clear()
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("registry_kind", ("main", "owner"))
def test_prepared_child_registry_cannot_be_resurrected_after_close(
    owner_kind: str,
    registry_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    stack = ExitStack()
    _prepared, _inputs, children = _issue_prepared_children(
        stack,
        runtime,
        token=f"prepared-child-no-resurrection-{registry_kind}-{owner_kind}",
    )
    child_id = id(children[owner_kind])
    main_registry, owner_registry = _prepared_child_registries(owner_kind)
    selected_registry = main_registry if registry_kind == "main" else owner_registry
    saved_record = _trusted_registry_value(selected_registry, child_id)
    stack.close()

    selected_registry[child_id] = saved_record
    assert child_id not in selected_registry
    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        compiled_module._prepared_registry_value_at_key(  # noqa: SLF001
            selected_registry,
            key=child_id,
            detail="post-close child resurrection",
        )
    selected_registry.clear()
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("registry_kind", ("main", "owner"))
@pytest.mark.parametrize("relocation_kind", ("move", "alias"))
def test_prepared_teardown_drains_local_child_relocated_to_wrong_exact_integer_key(
    owner_kind: str,
    registry_kind: str,
    relocation_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=(f"prepared-wrong-key-foreign-{relocation_kind}-{registry_kind}-{owner_kind}"),
        )
        _local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=(f"prepared-wrong-key-local-{relocation_kind}-{registry_kind}-{owner_kind}"),
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        owner_registry = {
            "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
            "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
            "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
        }[owner_kind]
        main_registry = {
            "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
            "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
            "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
        }[owner_kind]
        relocated_registry = main_registry if registry_kind == "main" else owner_registry
        local_id = id(local_child)
        foreign_id = id(foreign_child)
        wrong_key = local_id + 1_000_000_007
        while wrong_key in relocated_registry:
            wrong_key += 1
        relocated_record = _trusted_registry_value(relocated_registry, local_id)
        if relocation_kind == "move":
            relocated_registry.pop(local_id)
        relocated_registry[wrong_key] = relocated_record

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert wrong_key not in relocated_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_drains_local_child_after_live_owner_token_drift(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with ExitStack() as stack:
            _prepared, _inputs, children = _issue_prepared_children(
                stack,
                runtime,
                token=f"prepared-child-owner-token-drift-{owner_kind}",
            )
            child = children[owner_kind]
            object.__setattr__(child, "_owner_token", object())

    assert _prepared_registry_key_sets() == before_registry_keys


def test_prepared_teardown_drains_all_children_after_parent_owner_token_drift() -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with ExitStack() as stack:
            prepared, _inputs, children = _issue_prepared_children(
                stack,
                runtime,
                token="prepared-parent-owner-token-drift",
            )
            child_ids = tuple(id(child) for child in children.values())
            object.__setattr__(prepared, "_owner_token", object())

    for child_id, main_registry, owner_registry in zip(
        child_ids,
        (
            compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
            compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
            compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
        ),
        (
            compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
            compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
            compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
        ),
        strict=True,
    ):
        assert child_id not in main_registry
        assert child_id not in owner_registry
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("registry_kind", ("main", "owner"))
def test_prepared_teardown_sanitizes_unattributable_wrong_integer_child_entry(
    owner_kind: str,
    registry_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-malformed-extra-foreign-{registry_kind}-{owner_kind}",
        )
        _local_prepared, _local_inputs, _local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-malformed-extra-local-{registry_kind}-{owner_kind}",
        )
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        malformed_registry = main_registry if registry_kind == "main" else owner_registry
        wrong_key = 1_000_000_007
        while wrong_key in malformed_registry:
            wrong_key += 1
        malformed_registry[wrong_key] = object()

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        foreign_id = id(foreign_children[owner_kind])
        assert wrong_key not in malformed_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("replacement_kind", ("canonical", "wrong_integer"))
def test_prepared_parent_teardown_sanitizes_unattributable_exact_integer_record(
    replacement_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        "M8 prepared frontier integrity differs: malformed exact parent record"
    )

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with ExitStack() as stack:
            prepared, _inputs, _children = _issue_prepared_children(
                stack,
                runtime,
                token=f"prepared-malformed-parent-{replacement_kind}",
            )
            parent_id = id(prepared)
            if replacement_kind == "canonical":
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # type: ignore[assignment]  # noqa: SLF001
                    parent_id
                ] = object()
            else:
                wrong_key = parent_id + 1_000_000_007
                while (
                    wrong_key in compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
                ):
                    wrong_key += 1
                compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY[  # type: ignore[assignment]  # noqa: SLF001
                    wrong_key
                ] = object()
            raise sentinel

    assert captured.value is sentinel
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
def test_prepared_teardown_trusts_local_main_over_forged_foreign_sidecar(
    owner_kind: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-main-precedence-foreign-{owner_kind}",
        )
        _local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-main-precedence-local-{owner_kind}",
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        child_id = id(local_child)
        main_registry, owner_registry = _prepared_child_registries(owner_kind)
        original_binding = _trusted_registry_value(owner_registry, child_id)
        owner_registry[child_id] = replace(
            original_binding,
            prepared_reference=weakref.ref(foreign_prepared),
            prepared_id=id(foreign_prepared),
            owner_token=foreign_prepared._owner_token,  # noqa: SLF001
        )
        object.__setattr__(
            local_child,
            "_owner_token",
            foreign_prepared._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert child_id not in main_registry
        assert child_id not in owner_registry
        foreign_id = id(foreign_child)
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        assert (
            compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                foreign_inputs,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                event_position=1,
            )
            == foreign_inputs
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


def test_prepared_hot_capability_checks_do_not_scan_sibling_registries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    with ExitStack() as foreign_stack, ExitStack() as local_stack:
        _foreign_prepared, _foreign_inputs, _foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token="prepared-no-hot-global-scan-foreign",
        )
        local_prepared, local_inputs, _local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token="prepared-no-hot-global-scan-local",
        )

        def forbidden_scan(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("hot prepared capability scanned a global registry")

        with monkeypatch.context() as hot_path:
            hot_path.setattr(
                compiled_module,
                "_prepared_translation_layout_registry_state",
                forbidden_scan,
            )
            hot_path.setattr(
                compiled_module,
                "_sanitize_exact_integer_registry_keys",
                forbidden_scan,
            )
            local_prepared.require_active(local_runtime)
            assert compiled_module._prepared_layout_footprints(  # noqa: SLF001
                local_prepared,
                local_runtime,
                event_position=1,
            )
            assert (
                compiled_module._require_prepared_frontier_batch_inputs(  # noqa: SLF001
                    local_inputs,
                    prepared=local_prepared,
                    runtime=local_runtime,
                    event_position=1,
                )
                == local_inputs
            )


@pytest.mark.parametrize("owner_kind", ("frontier_inputs", "source_lease", "remnant"))
@pytest.mark.parametrize("claim_direction", ("local_claims_foreign", "foreign_claims_local"))
@pytest.mark.parametrize("child_reference_state", ("live", "dead"))
def test_prepared_teardown_does_not_trust_cross_owner_sidecar_claim(
    owner_kind: str,
    claim_direction: str,
    child_reference_state: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    def issue_children(stack: ExitStack, runtime, token: str):  # type: ignore[no-untyped-def]
        binding = runtime.replay_input.instances[1]
        item = inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token=token,
        )
        prepared = stack.enter_context(
            compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            )
        )
        inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
            remnants=(item.remnant,),
        )
        prepared_record = _trusted_registry_value(
            compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY, id(prepared)
        )
        lease = next(iter(prepared_record.source_leases.values()))
        authority = next(iter(prepared_record.remnant_authorities.values()))
        return prepared, {
            "frontier_inputs": inputs,
            "source_lease": lease,
            "remnant": authority,
        }

    owner_registry = {
        "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,  # noqa: SLF001
        "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,  # noqa: SLF001
        "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,  # noqa: SLF001
    }[owner_kind]
    main_registry = {
        "frontier_inputs": compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY,  # noqa: SLF001
        "source_lease": compiled_module._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # noqa: SLF001
        "remnant": compiled_module._PREPARED_REMNANT_AUTHORITY_REGISTRY,  # noqa: SLF001
    }[owner_kind]
    local_stack = ExitStack()
    foreign_stack = ExitStack()
    try:
        local_prepared, local_children = issue_children(
            local_stack,
            local_runtime,
            f"prepared-cross-owner-local-{owner_kind}-{claim_direction}",
        )
        foreign_prepared, foreign_children = issue_children(
            foreign_stack,
            foreign_runtime,
            f"prepared-cross-owner-foreign-{owner_kind}-{claim_direction}",
        )
        local_child = local_children[owner_kind]
        foreign_child = foreign_children[owner_kind]
        forged_child, claimed_owner = (
            (local_child, foreign_prepared)
            if claim_direction == "local_claims_foreign"
            else (foreign_child, local_prepared)
        )
        child_reference = weakref.ref(forged_child)
        if child_reference_state == "dead":
            dead_child = _DeadWeakTarget()
            child_reference = weakref.ref(dead_child)
            del dead_child
            assert child_reference() is None
        owner_registry[id(forged_child)] = replace(
            _trusted_registry_value(owner_registry, id(forged_child)),
            child_reference=child_reference,
            prepared_reference=weakref.ref(claimed_owner),
            prepared_id=id(claimed_owner),
            owner_token=claimed_owner._owner_token,  # noqa: SLF001
        )

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert id(local_child) not in main_registry
        assert id(local_child) not in owner_registry
        assert id(foreign_child) in main_registry
        assert id(foreign_child) in owner_registry
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("corrupted_registry", ("main", "owner"))
def test_prepared_frontier_child_callback_releases_registry_pair_atomically(
    corrupted_registry: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-callback-pair-{corrupted_registry}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: callback pair {corrupted_registry}"
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            inputs_id = id(inputs)
            if corrupted_registry == "main":
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY[inputs_id] = object()  # type: ignore[assignment]  # noqa: SLF001
            else:
                compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY[inputs_id] = object()  # type: ignore[assignment]  # noqa: SLF001
            del inputs
            gc.collect()
            assert inputs_id not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
            assert inputs_id not in compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY  # noqa: SLF001
            raise sentinel

    assert captured.value is sentinel
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("corrupted_registry", ("main", "owner"))
def test_prepared_frontier_child_callback_restores_target_without_iteration(
    monkeypatch: pytest.MonkeyPatch,
    corrupted_registry: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-callback-o1-restore-{corrupted_registry}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: callback O(1) restore {corrupted_registry}"
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            inputs_id = id(inputs)
            main_registry, owner_registry = _prepared_child_registries("frontier_inputs")
            selected_registry = main_registry if corrupted_registry == "main" else owner_registry
            selected_registry.pop(inputs_id)

            def forbidden_scan(*_args, **_kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("prepared child callback performed a registry scan")

            registry_type = type(main_registry)
            with monkeypatch.context() as callback_patch:
                callback_patch.setattr(
                    registry_type,
                    "_repair_untrusted_mutations",
                    forbidden_scan,
                )
                callback_patch.setattr(registry_type, "items", forbidden_scan)
                callback_patch.setattr(registry_type, "__iter__", forbidden_scan)
                del inputs
                gc.collect()
                assert inputs_id not in main_registry
                assert inputs_id not in owner_registry
            raise sentinel

    assert captured.value is sentinel
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("alias_registry", ("main", "owner"))
def test_prepared_frontier_child_callback_releases_distinct_weakref_alias(
    alias_registry: str,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-callback-distinct-alias-{alias_registry}",
    )
    sentinel = compiled_module.M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: callback alias {alias_registry}"
    )
    before_registry_keys = _prepared_registry_key_sets()

    with pytest.raises(compiled_module.M8PreparedFrontierIntegrityError) as captured:
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            inputs = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )
            inputs_id = id(inputs)
            wrong_key = inputs_id + 1_000_000_007
            while (
                wrong_key in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
                or wrong_key in compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY  # noqa: SLF001
            ):
                wrong_key += 1
            distinct_reference = weakref.ref(inputs)
            canonical_record = _trusted_registry_value(
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY, inputs_id
            )
            assert distinct_reference is not canonical_record.reference
            if alias_registry == "main":
                compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY[  # noqa: SLF001
                    wrong_key
                ] = replace(canonical_record, reference=distinct_reference)
            else:
                binding_record = _trusted_registry_value(
                    compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY, inputs_id
                )
                compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY[  # noqa: SLF001
                    wrong_key
                ] = replace(binding_record, child_reference=distinct_reference)

            del inputs
            gc.collect()
            assert inputs_id not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
            assert wrong_key not in compiled_module._PREPARED_FRONTIER_INPUT_REGISTRY  # noqa: SLF001
            assert inputs_id not in (  # noqa: SLF001
                compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY
            )
            assert wrong_key not in (  # noqa: SLF001
                compiled_module._PREPARED_FRONTIER_INPUT_OWNER_REGISTRY
            )
            raise sentinel

    assert captured.value is sentinel
    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize("mutated_registry", ("main", "owner"))
def test_prepared_frontier_callback_cannot_launder_sibling_owner_mutation(
    mutated_registry: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    foreign_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    local_runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    foreign_stack = ExitStack()
    local_stack = ExitStack()
    try:
        foreign_prepared, _foreign_inputs, foreign_children = _issue_prepared_children(
            foreign_stack,
            foreign_runtime,
            token=f"prepared-callback-launder-foreign-{mutated_registry}",
        )
        local_prepared, _local_inputs, local_children = _issue_prepared_children(
            local_stack,
            local_runtime,
            token=f"prepared-callback-launder-local-{mutated_registry}",
        )
        foreign_child = foreign_children["frontier_inputs"]
        local_child = local_children["frontier_inputs"]
        foreign_id = id(foreign_child)
        local_id = id(local_child)
        main_registry, owner_registry = _prepared_child_registries("frontier_inputs")
        if mutated_registry == "main":
            victim = _trusted_registry_value(main_registry, foreign_id)
            object.__setattr__(victim, "prepared", local_prepared)
            object.__setattr__(
                victim,
                "owner_token",
                local_prepared._owner_token,  # noqa: SLF001
            )
        else:
            victim = _trusted_registry_value(owner_registry, foreign_id)
            object.__setattr__(
                victim,
                "prepared_reference",
                weakref.ref(local_prepared),
            )
            object.__setattr__(victim, "prepared_id", id(local_prepared))
            object.__setattr__(
                victim,
                "owner_token",
                local_prepared._owner_token,  # noqa: SLF001
            )

        binding = local_runtime.replay_input.instances[1]
        trigger_item = inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token=f"prepared-callback-launder-trigger-{mutated_registry}",
        )
        trigger = compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
            local_prepared,
            local_runtime,
            event_position=1,
            remnants=(trigger_item.remnant,),
        )
        trigger_id = id(trigger)
        del trigger
        gc.collect()
        assert trigger_id not in main_registry
        assert trigger_id not in owner_registry

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()

        assert local_id not in main_registry
        assert local_id not in owner_registry
        assert foreign_id in main_registry
        assert foreign_id in owner_registry
        foreign_registered = _trusted_registry_value(main_registry, foreign_id)
        assert (
            _require_prepared_child_capability(
                "frontier_inputs",
                child=foreign_child,
                prepared=foreign_prepared,
                runtime=foreign_runtime,
                registered=foreign_registered,
            )
            is not foreign_child
        )
        foreign_stack.close()
    finally:
        try:
            local_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass
        try:
            foreign_stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


def test_prepared_batch_rejects_corrupt_frontier_compiler_scalars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    original = compiled_module._build_compiled_rejection_problem  # noqa: SLF001

    def corrupt(  # type: ignore[no-untyped-def]
        problem,
        verified,
        *,
        projections=None,
        identity=None,
    ):
        compiled = original(
            problem,
            verified,
            projections=projections,
            identity=identity,
        )
        forged_members = tuple(
            replace(
                member,
                area=member.area * 100.0,
                width=member.width * 100.0,
                height=member.height * 100.0,
            )
            for member in compiled.frontier.members
        )
        return replace(
            compiled,
            frontier=compiled_module.build_pareto_frontier(forged_members),
        )

    monkeypatch.setattr(compiled_module, "_build_compiled_rejection_problem", corrupt)
    with pytest.raises(
        compiled_module.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ):
            pass


def test_prepared_batch_rejects_corrupt_same_area_remnant_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    item = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="prepared-corrupt-same-area-bounds",
    )
    original = compiled_module.prepare_translation_rejection_remnant

    def corrupt(remnant):  # type: ignore[no-untyped-def]
        measured = original(remnant)
        return replace(measured, bounds=(0.0, 0.0, 1.0, measured.area))

    monkeypatch.setattr(
        compiled_module,
        "prepare_translation_rejection_remnant",
        corrupt,
    )
    with compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            compiled_module._prepared_frontier_batch_inputs(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
                remnants=(item.remnant,),
            )


@pytest.mark.parametrize(
    ("source_field", "error_match"),
    (
        ("candidate_width", "source geometry differs"),
        ("fit_config", "source lease payload"),
        ("event_material", "source lease payload"),
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
            with pytest.raises(
                compiled_module.M8PreparedFrontierIntegrityError,
                match="source lease payload",
            ):
                tuple(
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
    assert canonical


@pytest.mark.parametrize(
    "accessor",
    ("getitem", "get", "items", "values", "setdefault"),
)
def test_public_parent_registry_read_revokes_semantic_authority_and_drains(
    accessor: str,
) -> None:
    """An escaped parent mirror can never remain a production authority."""

    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    stack = ExitStack()
    try:
        prepared = stack.enter_context(
            compiled_module._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            )
        )
        registry = compiled_module._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
        prepared_id = id(prepared)
        if accessor == "getitem":
            exposed = registry[prepared_id]
        elif accessor == "get":
            exposed = registry.get(prepared_id)
        elif accessor == "items":
            exposed = dict(registry.items())[prepared_id]
        elif accessor == "values":
            exposed = next(iter(registry.values()))
        else:
            exposed = registry.setdefault(prepared_id, object())
        assert type(exposed) is compiled_module._PreparedTranslationLayoutRecord  # noqa: SLF001

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            compiled_module._prepared_standard_winner(  # noqa: SLF001
                prepared,
                runtime,
                event_position=1,
            )
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            stack.close()
    finally:
        try:
            stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "owner_kind",
    ("frontier_inputs", "source_lease", "remnant"),
)
def test_public_child_registry_read_revokes_only_that_authority_and_drains(
    owner_kind: str,
) -> None:
    """Every issued child record follows the same fail-closed exposure rule."""

    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    stack = ExitStack()
    try:
        prepared, _inputs, children = _issue_prepared_children(
            stack,
            runtime,
            token=f"prepared-public-child-exposure-{owner_kind}",
        )
        child = children[owner_kind]
        main_registry, _owner_registry = _prepared_child_registries(owner_kind)
        exposed = main_registry[id(child)]

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            _require_prepared_child_capability(
                owner_kind,
                child=child,
                prepared=prepared,
                runtime=runtime,
                registered=exposed,
            )
        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            stack.close()
    finally:
        try:
            stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert _prepared_registry_key_sets() == before_registry_keys


@pytest.mark.parametrize(
    "registry_name",
    (
        "_PREPARED_TRANSLATION_LAYOUT_REGISTRY",
        "_PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY",
        "_PREPARED_REMNANT_AUTHORITY_REGISTRY",
        "_PREPARED_FRONTIER_INPUT_REGISTRY",
        "_PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY",
        "_PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY",
        "_PREPARED_FRONTIER_INPUT_OWNER_REGISTRY",
    ),
)
def test_registry_class_drift_is_restored_before_failure_atomic_cleanup(
    registry_name: str,
) -> None:
    before_registry_keys = _prepared_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    stack = ExitStack()
    registry = getattr(compiled_module, registry_name)
    canonical_type = type(registry)
    drifted_type = type(
        f"_DriftedRegistry{registry_name}",
        (canonical_type,),
        {"__slots__": ()},
    )
    try:
        _prepared, _inputs, _children = _issue_prepared_children(
            stack,
            runtime,
            token=f"prepared-registry-class-drift-{registry_name}",
        )
        object.__setattr__(registry, "__class__", drifted_type)
        assert type(registry) is drifted_type

        with pytest.raises(
            compiled_module.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            stack.close()
    finally:
        if type(registry) is not canonical_type:
            object.__setattr__(registry, "__class__", canonical_type)
        try:
            stack.close()
        except compiled_module.M8PreparedFrontierIntegrityError:
            pass

    assert type(registry) is canonical_type
    assert _prepared_registry_key_sets() == before_registry_keys
