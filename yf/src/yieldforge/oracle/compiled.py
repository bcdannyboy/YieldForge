"""Compiled inventory-independent M7 winners and future relevance helpers."""

from __future__ import annotations

import os
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from yieldforge.baseline.geometry import (
    PreparedLayoutFootprint,
    TranslationRejectionCertificate,
    certify_translation_impossible,
    prepare_layout_footprint,
)
from yieldforge.baseline.replay import (
    M7ReplayCursor,
    M7ReplayRuntime,
    enumerate_m7_action_catalog,
    select_m7_fallback,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.replay.contracts import InventoryItem, ReplayCostLedger

_MAX_PREPARED_LAYOUT_CACHE_PROBLEMS = 2


@dataclass(frozen=True)
class CompiledStandardWinner:
    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    action_id: str
    candidate_id: str
    decision_key: tuple[str, ...]


@dataclass(frozen=True)
class CompiledTranslationRejection:
    candidate_id: str
    certificate: TranslationRejectionCertificate


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PreparedTranslationLayoutBatch:
    """Process-local capability for one registry-owned proof batch."""

    _runtime_id: int

    def require_active(self, runtime: M7ReplayRuntime, *, deep: bool = False) -> None:
        _require_prepared_translation_layout_record(self, runtime, deep=deep)

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared translation layout batches cannot be serialized")


_PreparedTranslationLayouts = tuple[
    tuple[tuple[str, str], tuple[PreparedLayoutFootprint, ...]], ...
]


@dataclass(frozen=True, slots=True)
class _PreparedTranslationLayoutRecord:
    """Private canonical storage that is never exposed through the capability."""

    reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]
    owner_pid: int
    runtime_id: int
    layouts: _PreparedTranslationLayouts
    fingerprint: str


_PREPARED_TRANSLATION_LAYOUT_REGISTRY: dict[
    int,
    _PreparedTranslationLayoutRecord,
] = {}


def _prepared_translation_layout_fingerprint(
    prepared: _PreparedTranslationLayoutBatch,
    layouts: _PreparedTranslationLayouts,
) -> str:
    payload = {
        "schema_version": "yieldforge.m8-prepared-translation-layout-batch.v1",
        "batch_id": id(prepared),
        "runtime_id": prepared._runtime_id,  # noqa: SLF001
        "layouts": tuple(
            {
                "problem_id": key[0],
                "candidate_set_id": key[1],
                "values": tuple(
                    {
                        "candidate_id": layout.candidate_id,
                        "geometry_wkb": layout.geometry.wkb_hex,
                        "part_polygon_wkb": tuple(
                            item.wkb_hex for item in layout.part_polygons
                        ),
                        "vertices": layout.vertices,
                        "bounds": layout.bounds,
                    }
                    for layout in candidate_layouts
                ),
            }
            for key, candidate_layouts in layouts
        ),
    }
    return f"sha256:{semantic_sha256(payload)}"


def _require_prepared_translation_layout_record(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    deep: bool = False,
) -> _PreparedTranslationLayoutRecord:
    registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(id(prepared))
    if (
        type(prepared) is not _PreparedTranslationLayoutBatch
        or registered is None
        or registered.reference() is not prepared
        or registered.owner_pid != os.getpid()
        or registered.runtime_id != id(runtime)
        or prepared._runtime_id != id(runtime)  # noqa: SLF001
    ):
        raise ValueError("M8 prepared translation layout batch is invalid or inactive")
    if deep and registered.fingerprint != _prepared_translation_layout_fingerprint(
        prepared,
        registered.layouts,
    ):
        raise ValueError("M8 prepared translation layout batch integrity differs")
    return registered


def _registered_prepared_translation_layouts(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
) -> _PreparedTranslationLayouts:
    """Return only canonical registry-owned layouts for an active capability."""

    return _require_prepared_translation_layout_record(prepared, runtime).layouts


def _prepared_key_and_inputs(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
):  # type: ignore[no-untyped-def]
    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 rejection event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    problem = next(
        problem
        for problem in runtime.replay_input.problems
        if problem.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    return (
        (problem.problem_id, verified.evidence.candidate_set_id),
        binding,
        problem,
        verified,
    )


@contextmanager
def _prepare_translation_layout_batch(
    runtime: M7ReplayRuntime,
    *,
    event_positions: tuple[int, ...],
) -> Iterator[_PreparedTranslationLayoutBatch]:
    """Build each future problem's layouts once for one private proof batch."""

    if event_positions != tuple(sorted(set(event_positions))):
        raise ValueError("M8 prepared translation event positions must be sorted unique")
    layouts_by_key: dict[tuple[str, str], tuple[PreparedLayoutFootprint, ...]] = {}
    for event_position in event_positions:
        key, _binding, problem, verified = _prepared_key_and_inputs(
            runtime,
            event_position=event_position,
        )
        if key not in layouts_by_key:
            layouts_by_key[key] = tuple(
                prepare_layout_footprint(
                    problem.problem,
                    candidate,
                    runtime.replay_input.fit_config,
                )
                for candidate in verified.candidates
            )
    layouts = tuple(sorted(layouts_by_key.items()))
    prepared = _PreparedTranslationLayoutBatch(_runtime_id=id(runtime))
    key = id(prepared)

    def discard(reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]) -> None:
        registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(key)
        if registered is not None and registered.reference is reference:
            _PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(key, None)

    reference = weakref.ref(prepared, discard)
    _PREPARED_TRANSLATION_LAYOUT_REGISTRY[key] = _PreparedTranslationLayoutRecord(
        reference=reference,
        owner_pid=os.getpid(),
        runtime_id=id(runtime),
        layouts=layouts,
        fingerprint=_prepared_translation_layout_fingerprint(prepared, layouts),
    )
    try:
        yield prepared
    finally:
        integrity_error = None
        try:
            prepared.require_active(runtime, deep=True)
        except ValueError as error:
            integrity_error = error
        registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(key)
        if registered is not None and registered.reference() is prepared:
            _PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(key, None)
        if integrity_error is not None:
            raise integrity_error


def _compile_rejections_from_layouts(
    runtime: M7ReplayRuntime,
    *,
    binding,  # type: ignore[no-untyped-def]
    verified,  # type: ignore[no-untyped-def]
    layouts: tuple[PreparedLayoutFootprint, ...],
    item: InventoryItem,
) -> tuple[CompiledTranslationRejection, ...]:
    if tuple(layout.candidate_id for layout in layouts) != tuple(
        candidate.candidate_id for candidate in verified.candidates
    ):
        raise ValueError("M8 compiled layout candidate identities differ")
    return tuple(
        CompiledTranslationRejection(
            candidate_id=candidate.candidate_id,
            certificate=certify_translation_impossible(
                layout,
                item.remnant,
                material=binding.material,
                fit_config=runtime.replay_input.fit_config,
            ),
        )
        for candidate, layout in zip(verified.candidates, layouts, strict=True)
    )


def _compile_prepared_translation_rejections(
    runtime: M7ReplayRuntime,
    *,
    prepared: _PreparedTranslationLayoutBatch,
    event_position: int,
    item: InventoryItem,
) -> tuple[CompiledTranslationRejection, ...]:
    """Use one already-validated layout set without reconstructing geometry."""

    prepared_layouts = _registered_prepared_translation_layouts(prepared, runtime)
    key, binding, _problem, verified = _prepared_key_and_inputs(
        runtime,
        event_position=event_position,
    )
    matching = tuple(
        layouts for candidate_key, layouts in prepared_layouts if candidate_key == key
    )
    if len(matching) != 1:
        raise ValueError("M8 prepared translation layouts do not cover the event problem")
    return _compile_rejections_from_layouts(
        runtime,
        binding=binding,
        verified=verified,
        layouts=matching[0],
        item=item,
    )


def _same_prepared_layout(left, right) -> bool:  # type: ignore[no-untyped-def]
    return (
        left.candidate_id == right.candidate_id
        and left.geometry.wkb == right.geometry.wkb
        and tuple(item.wkb for item in left.part_polygons)
        == tuple(item.wkb for item in right.part_polygons)
        and left.vertices == right.vertices
        and left.bounds == right.bounds
    )


def compile_translation_rejections(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
) -> tuple[CompiledTranslationRejection, ...]:
    """Evaluate every safe constant-time rejection for one remnant/event pair."""

    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 rejection event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    problem = next(
        problem
        for problem in runtime.replay_input.problems
        if problem.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    prepared_key = (problem.problem_id, verified.evidence.candidate_set_id)
    cached_layouts = runtime.prepared_layout_cache.get(prepared_key)
    expected_layouts = tuple(
        prepare_layout_footprint(
            problem.problem,
            candidate,
            runtime.replay_input.fit_config,
        )
        for candidate in verified.candidates
    )
    if cached_layouts is None:
        runtime.prepared_layout_cache[prepared_key] = expected_layouts
        while len(runtime.prepared_layout_cache) > _MAX_PREPARED_LAYOUT_CACHE_PROBLEMS:
            runtime.prepared_layout_cache.popitem(last=False)
    else:
        if tuple(layout.candidate_id for layout in cached_layouts) != tuple(
            candidate.candidate_id for candidate in verified.candidates
        ):
            raise ValueError("M8 compiled layout candidate identities differ")
        if any(
            not _same_prepared_layout(cached, expected)
            for cached, expected in zip(
                cached_layouts,
                expected_layouts,
                strict=True,
            )
        ):
            raise ValueError("M8 prepared layout cache value differs from frozen geometry")
        runtime.prepared_layout_cache.move_to_end(prepared_key)
    return tuple(
        CompiledTranslationRejection(
            candidate_id=candidate.candidate_id,
            certificate=certify_translation_impossible(
                layout,
                item.remnant,
                material=binding.material,
                fit_config=runtime.replay_input.fit_config,
            ),
        )
        for candidate, layout in zip(verified.candidates, expected_layouts, strict=True)
    )


def compile_standard_winner(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledStandardWinner:
    """Compile the exact frozen-policy standard winner for one problem occurrence."""

    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 standard-winner event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    cursor = M7ReplayCursor(
        next_event_position=event_position,
        current_time=binding.released_at,
        inventory=(),
        cumulative_costs=ReplayCostLedger.zero(),
        timestamp_group_sequence=-1,
        timestamp_subsequence=0,
        previous_release=None,
    )
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    if catalog.remnant_action_count != 0:
        raise ValueError("M8 standard compilation unexpectedly observed remnant actions")
    selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selection.action_id)
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    evidence = runtime.runtime_candidates[binding.problem_id].evidence
    return CompiledStandardWinner(
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        candidate_set_id=evidence.candidate_set_id,
        candidate_set_sha256=evidence.content_sha256,
        action_id=descriptor.action_id,
        candidate_id=descriptor.candidate_id,
        decision_key=selection.decision_key,
    )


__all__ = [
    "CompiledStandardWinner",
    "CompiledTranslationRejection",
    "compile_standard_winner",
    "compile_translation_rejections",
]
