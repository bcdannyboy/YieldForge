"""Compiled inventory-independent M7 winners and future relevance helpers."""

from __future__ import annotations

from dataclasses import dataclass

from yieldforge.baseline.geometry import (
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
