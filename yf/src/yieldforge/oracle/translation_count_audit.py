"""Independent vectorized audit of frozen M7 translation-search counts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from multiprocessing import get_context

import numpy as np

from yieldforge.baseline.contracts import LayoutFitSearchConfig
from yieldforge.baseline.geometry import (
    LayoutTranslationCandidates,
    PreparedLayoutFootprint,
    PreparedRemnantGeometry,
)
from yieldforge.reuse.contracts import RemnantFitConfig


@dataclass(frozen=True, slots=True)
class TranslationCountAudit:
    """Exact count-only reconstruction of one registered translation sequence."""

    generated_candidate_count: int
    duplicate_candidate_count: int
    evaluated_candidate_count: int
    budget_truncated: bool


@dataclass(frozen=True, slots=True)
class _TranslationCountAuditCall:
    remnant: PreparedRemnantGeometry
    layout: PreparedLayoutFootprint
    expected: LayoutTranslationCandidates
    fit_config: RemnantFitConfig
    search_config: LayoutFitSearchConfig


_FORK_AUDIT_CALLS: tuple[_TranslationCountAuditCall, ...] | None = None


def _grid(start: float, stop: float, count: int) -> tuple[float, ...]:
    if count == 2:
        return (start, stop)
    step = (stop - start) / (count - 1)
    values = [start + index * step for index in range(count)]
    values[-1] = stop
    return tuple(0.0 if value == 0.0 else float(value) for value in values)


def _normalized_keys(points: np.ndarray) -> np.ndarray:
    normalized = np.ascontiguousarray(points, dtype=np.float64)
    normalized[normalized == 0.0] = 0.0
    return normalized.view(np.dtype((np.void, 16))).reshape(-1)


def _require_expected_counts(
    audit: TranslationCountAudit,
    expected: LayoutTranslationCandidates,
) -> TranslationCountAudit:
    if (
        audit.generated_candidate_count != expected.generated_candidate_count
        or audit.duplicate_candidate_count != expected.duplicate_candidate_count
        or audit.evaluated_candidate_count != len(expected.translations)
        or audit.budget_truncated != expected.budget_truncated
    ):
        raise ValueError("M8 Jagua translation counts differ from independent audit")
    return audit


def audit_layout_translation_counts(
    *,
    remnant: PreparedRemnantGeometry,
    layout: PreparedLayoutFootprint,
    expected: LayoutTranslationCandidates,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
) -> TranslationCountAudit:
    """Reconstruct M7 counts without using Jagua or collision classification.

    The implementation validates Jagua's returned unique sequence against an
    independently reconstructed source stream. Source ordering, IEEE-754
    identity, duplicate accounting, and the first over-budget candidate remain
    exact without collision classification or the production generator.
    """

    if (
        expected.candidate_id != layout.candidate_id
        or expected.remnant_id != remnant.remnant_id
    ):
        raise ValueError("M8 translation-count audit identities differ")
    if search_config.candidate_source_order != (
        "bbox_alignments",
        "vertex_alignments",
        "uniform_grid",
    ):
        raise ValueError("M8 translation-count audit source order differs from M7")
    parent_min_x, parent_min_y, parent_max_x, parent_max_y = remnant.bounds
    foot_min_x, foot_min_y, foot_max_x, foot_max_y = layout.bounds
    values = (*remnant.bounds, *layout.bounds, fit_config.coordinate_tolerance)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("M8 translation-count audit requires finite bounds")
    min_x = parent_min_x - foot_min_x
    max_x = parent_max_x - foot_max_x
    min_y = parent_min_y - foot_min_y
    max_y = parent_max_y - foot_max_y
    if (
        min_x > max_x + fit_config.coordinate_tolerance
        or min_y > max_y + fit_config.coordinate_tolerance
    ):
        return _require_expected_counts(
            TranslationCountAudit(
                generated_candidate_count=0,
                duplicate_candidate_count=0,
                evaluated_candidate_count=0,
                budget_truncated=False,
            ),
            expected,
        )

    maximum = search_config.maximum_candidates
    expected_points = np.asarray(expected.translations, dtype=np.float64).reshape((-1, 2))
    expected_keys = _normalized_keys(expected_points)
    if len(np.unique(expected_keys)) != len(expected_keys):
        raise ValueError("M8 Jagua translation sequence contains duplicate identities")
    if expected.budget_truncated and len(expected_keys) != maximum:
        raise ValueError("M8 truncated Jagua translation sequence has the wrong length")
    sorted_order = np.argsort(expected_keys)
    sorted_keys = expected_keys[sorted_order]
    first_positions = np.full(len(expected_keys), -1, dtype=np.int64)
    source_position = 0

    def map_expected(points: np.ndarray) -> np.ndarray:
        keys = _normalized_keys(points)
        mapped = np.full(len(keys), -1, dtype=np.int64)
        if not len(sorted_keys) or not len(keys):
            return mapped
        positions = np.searchsorted(sorted_keys, keys)
        in_bounds = positions < len(sorted_keys)
        local_indexes = np.flatnonzero(in_bounds)
        if len(local_indexes):
            candidate_positions = positions[local_indexes]
            matches = sorted_keys[candidate_positions] == keys[local_indexes]
            matched_indexes = local_indexes[matches]
            mapped[matched_indexes] = sorted_order[positions[matched_indexes]]
        return mapped

    def consume(points: np.ndarray) -> TranslationCountAudit | None:
        nonlocal source_position
        if not len(points):
            return None
        mapped = map_expected(points)
        foreign = np.flatnonzero(mapped < 0)
        prefix_length = int(foreign[0]) + 1 if len(foreign) else len(mapped)
        prefix = mapped[:prefix_length]
        for expected_index in np.unique(prefix[prefix >= 0]):
            if first_positions[expected_index] < 0:
                first_local = int(np.flatnonzero(prefix == expected_index)[0])
                first_positions[expected_index] = source_position + first_local
        if len(foreign):
            foreign_position = source_position + int(foreign[0])
            if (
                not expected.budget_truncated
                or np.any(first_positions < 0)
                or not np.all(first_positions[:-1] < first_positions[1:])
                or first_positions[-1] >= foreign_position
            ):
                raise ValueError(
                    "M8 Jagua translation sequence differs from independent source order"
                )
            generated = len(expected_keys) + 1
            return _require_expected_counts(
                TranslationCountAudit(
                    generated_candidate_count=generated,
                    duplicate_candidate_count=foreign_position + 1 - generated,
                    evaluated_candidate_count=len(expected_keys),
                    budget_truncated=True,
                ),
                expected,
            )
        source_position += len(mapped)
        return None

    bounding = np.asarray(
        (
            (min_x, min_y),
            (min_x, max_y),
            (max_x, min_y),
            (max_x, max_y),
        ),
        dtype=np.float64,
    )
    result = consume(bounding)
    if result is not None:
        return result
    parent_vertices = np.asarray(remnant.vertices, dtype=np.float64)
    layout_vertices = np.asarray(layout.vertices, dtype=np.float64)
    tolerance = fit_config.coordinate_tolerance
    chunk_size = 1024
    for parent_vertex in parent_vertices:
        for offset in range(0, len(layout_vertices), chunk_size):
            vertex_points = parent_vertex - layout_vertices[offset : offset + chunk_size]
            vertex_mask = (
                (vertex_points[:, 0] >= min_x - tolerance)
                & (vertex_points[:, 0] <= max_x + tolerance)
                & (vertex_points[:, 1] >= min_y - tolerance)
                & (vertex_points[:, 1] <= max_y + tolerance)
            )
            result = consume(vertex_points[vertex_mask])
            if result is not None:
                return result
    grid_x = np.asarray(_grid(min_x, max_x, search_config.grid_columns))
    grid_y = np.asarray(_grid(min_y, max_y, search_config.grid_rows))
    grid_points = np.column_stack(
        (
            np.repeat(grid_x, len(grid_y)),
            np.tile(grid_y, len(grid_x)),
        )
    )
    result = consume(grid_points)
    if result is not None:
        return result
    if (
        expected.budget_truncated
        or np.any(first_positions < 0)
        or not np.all(first_positions[:-1] < first_positions[1:])
    ):
        raise ValueError(
            "M8 Jagua translation sequence differs from independent source order"
        )
    generated = len(expected_keys)
    return _require_expected_counts(
        TranslationCountAudit(
            generated_candidate_count=generated,
            duplicate_candidate_count=source_position - generated,
            evaluated_candidate_count=generated,
            budget_truncated=False,
        ),
        expected,
    )


def _audit_forked_call(index: int) -> TranslationCountAudit:
    calls = _FORK_AUDIT_CALLS
    if calls is None:
        raise RuntimeError("M8 forked translation-count audit lacks inherited calls")
    call = calls[index]
    return audit_layout_translation_counts(
        remnant=call.remnant,
        layout=call.layout,
        expected=call.expected,
        fit_config=call.fit_config,
        search_config=call.search_config,
    )


def audit_layout_translation_batch(
    *,
    remnant: PreparedRemnantGeometry,
    layouts: tuple[PreparedLayoutFootprint, ...],
    expected: tuple[LayoutTranslationCandidates, ...],
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
    process_count: int,
) -> tuple[TranslationCountAudit, ...]:
    """Audit a large immutable batch in measured forked workers."""

    if type(process_count) is not int or process_count <= 0:
        raise ValueError("M8 translation audit process count must be a positive integer")
    if len(layouts) != len(expected) or not layouts:
        raise ValueError("M8 translation-count audit batch is not aligned")
    calls = tuple(
        _TranslationCountAuditCall(
            remnant=remnant,
            layout=layout,
            expected=batch,
            fit_config=fit_config,
            search_config=search_config,
        )
        for layout, batch in zip(layouts, expected, strict=True)
    )
    workers = min(process_count, len(calls))
    if len(calls) < 32 or workers == 1:
        return tuple(
            audit_layout_translation_counts(
                remnant=call.remnant,
                layout=call.layout,
                expected=call.expected,
                fit_config=call.fit_config,
                search_config=call.search_config,
            )
            for call in calls
        )
    global _FORK_AUDIT_CALLS
    if _FORK_AUDIT_CALLS is not None:
        raise RuntimeError("M8 translation-count audit batch cannot be nested")
    _FORK_AUDIT_CALLS = calls
    try:
        with get_context("fork").Pool(processes=workers) as pool:
            return tuple(
                pool.map(
                    _audit_forked_call,
                    range(len(calls)),
                    chunksize=max(1, len(calls) // (workers * 8)),
                )
            )
    finally:
        _FORK_AUDIT_CALLS = None


__all__ = [
    "TranslationCountAudit",
    "audit_layout_translation_batch",
    "audit_layout_translation_counts",
]
