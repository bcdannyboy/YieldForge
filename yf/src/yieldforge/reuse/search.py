"""Deterministic bounded witness discovery for exact remnant reuse."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from shapely import BufferJoinStyle, Polygon, prepare
from shapely.affinity import translate

from yieldforge.domain import Part
from yieldforge.reuse.contracts import (
    FitPlacement,
    FitSearchAttemptSummary,
    FitSearchConfig,
    FitSearchRejectionCount,
    FitSearchResult,
    FitSearchStatus,
    MaterialIdentity,
    RemnantFitConfig,
    RemnantStock,
    ReuseGeometryError,
    polygon_from_record,
)
from yieldforge.reuse.geometry import (
    _validate_pretransformed_fit_placement,
    material_key,
    rotate_part,
)


@dataclass(frozen=True)
class _CandidateGeneration:
    placements: tuple[FitPlacement, ...]
    remnant_polygon: Polygon
    rotated_parts: tuple[tuple[float, Polygon], ...]
    generated_candidate_count: int
    duplicate_candidate_count: int
    unique_candidate_count: int
    budget_truncated_candidate_count: int


def _canonical_zero(value: float) -> float:
    return 0.0 if value == 0.0 else float(value)


def _grid_values(start: float, stop: float, count: int) -> tuple[float, ...]:
    if count == 2:
        return (_canonical_zero(start), _canonical_zero(stop))
    step = (stop - start) / (count - 1)
    values = [start + index * step for index in range(count)]
    values[-1] = stop
    return tuple(_canonical_zero(value) for value in values)


def _vertices(polygon: Polygon) -> tuple[tuple[float, float], ...]:
    rings = (polygon.exterior, *polygon.interiors)
    return tuple(
        (_canonical_zero(float(x)), _canonical_zero(float(y)))
        for ring in rings
        for x, y in tuple(ring.coords)[:-1]
    )


def _placement(
    part_id: str,
    rotation: float,
    translation_x: float,
    translation_y: float,
) -> FitPlacement:
    return FitPlacement(
        part_id=part_id,
        rotation=_canonical_zero(rotation),
        translation=(
            _canonical_zero(translation_x),
            _canonical_zero(translation_y),
        ),
    )


def _candidate_key(
    rotation: float,
    translation_x: float,
    translation_y: float,
) -> tuple[float, float, float]:
    return (
        _canonical_zero(rotation),
        _canonical_zero(translation_x),
        _canonical_zero(translation_y),
    )


def _translation_within_bounds(
    translation_x: float,
    translation_y: float,
    *,
    minimum_x: float,
    maximum_x: float,
    minimum_y: float,
    maximum_y: float,
    tolerance: float,
) -> bool:
    return (
        minimum_x - tolerance <= translation_x <= maximum_x + tolerance
        and minimum_y - tolerance <= translation_y <= maximum_y + tolerance
    )


def _generate_fit_placements(
    remnant: RemnantStock,
    part: Part,
    *,
    config: FitSearchConfig,
    fit_config: RemnantFitConfig,
) -> _CandidateGeneration:
    if part.demand != 1:
        raise ReuseGeometryError(
            "unsupported_part_demand", "remnant search requires one explicit part instance"
        )
    if part.allowed_orientations is None:
        raise ReuseGeometryError(
            "missing_allowed_orientations", "remnant search requires explicit allowed rotations"
        )
    if not part.allowed_orientations:
        raise ReuseGeometryError(
            "missing_allowed_orientations", "remnant search requires at least one allowed rotation"
        )
    if not all(math.isfinite(value) for value in part.allowed_orientations):
        raise ReuseGeometryError("nonfinite_geometry", "allowed rotations must be finite")

    remnant_polygon = polygon_from_record(remnant.geometry)
    prepare(remnant_polygon)
    remnant_min_x, remnant_min_y, remnant_max_x, remnant_max_y = remnant_polygon.bounds
    remnant_width = remnant_max_x - remnant_min_x
    remnant_height = remnant_max_y - remnant_min_y
    remnant_vertices = _vertices(remnant_polygon)
    unique_keys: set[tuple[float, float, float]] = set()
    generated_candidate_count = 0
    rotated_parts: list[tuple[float, Polygon]] = []

    for rotation in sorted(float(value) for value in part.allowed_orientations):
        rotated = rotate_part(part, rotation)
        footprint = (
            rotated.buffer(
                fit_config.clearance_distance,
                join_style=BufferJoinStyle.mitre,
            )
            if fit_config.clearance_distance > 0
            else rotated
        )
        if not isinstance(footprint, Polygon) or footprint.is_empty or not footprint.is_valid:
            raise ReuseGeometryError(
                "invalid_buffered_footprint", "clearance produced invalid search geometry"
            )
        footprint_min_x, footprint_min_y, footprint_max_x, footprint_max_y = footprint.bounds
        footprint_width = footprint_max_x - footprint_min_x
        footprint_height = footprint_max_y - footprint_min_y
        area_tolerance = max(
            fit_config.coordinate_tolerance,
            remnant_polygon.area * fit_config.relative_area_tolerance,
        )
        if footprint.area > remnant_polygon.area + area_tolerance:
            continue
        if (
            footprint_width > remnant_width + fit_config.coordinate_tolerance
            or footprint_height > remnant_height + fit_config.coordinate_tolerance
        ):
            continue
        rotated_parts.append((_canonical_zero(rotation), rotated))

        translation_min_x = remnant_min_x - footprint_min_x
        translation_max_x = remnant_max_x - footprint_max_x
        translation_min_y = remnant_min_y - footprint_min_y
        translation_max_y = remnant_max_y - footprint_max_y

        for translation_x in (translation_min_x, translation_max_x):
            for translation_y in (translation_min_y, translation_max_y):
                generated_candidate_count += 1
                unique_keys.add(_candidate_key(rotation, translation_x, translation_y))

        for remnant_x, remnant_y in remnant_vertices:
            for part_x, part_y in _vertices(footprint):
                translation_x = remnant_x - part_x
                translation_y = remnant_y - part_y
                if _translation_within_bounds(
                    translation_x,
                    translation_y,
                    minimum_x=translation_min_x,
                    maximum_x=translation_max_x,
                    minimum_y=translation_min_y,
                    maximum_y=translation_max_y,
                    tolerance=fit_config.coordinate_tolerance,
                ):
                    generated_candidate_count += 1
                    unique_keys.add(_candidate_key(rotation, translation_x, translation_y))

        for translation_x in _grid_values(
            translation_min_x,
            translation_max_x,
            config.grid_columns,
        ):
            for translation_y in _grid_values(
                translation_min_y,
                translation_max_y,
                config.grid_rows,
            ):
                generated_candidate_count += 1
                unique_keys.add(_candidate_key(rotation, translation_x, translation_y))

    ordered_keys = tuple(sorted(unique_keys))
    budgeted_keys = ordered_keys[: config.maximum_candidates]
    budgeted = tuple(
        _placement(part.id, rotation, translation_x, translation_y)
        for rotation, translation_x, translation_y in budgeted_keys
    )
    return _CandidateGeneration(
        placements=budgeted,
        remnant_polygon=remnant_polygon,
        rotated_parts=tuple(rotated_parts),
        generated_candidate_count=generated_candidate_count,
        duplicate_candidate_count=generated_candidate_count - len(ordered_keys),
        unique_candidate_count=len(ordered_keys),
        budget_truncated_candidate_count=len(ordered_keys) - len(budgeted),
    )


def generate_fit_placements(
    remnant: RemnantStock,
    part: Part,
    *,
    config: FitSearchConfig,
    fit_config: RemnantFitConfig | None = None,
) -> tuple[FitPlacement, ...]:
    """Return the registered, deduplicated, sorted, budgeted candidate transforms."""

    return _generate_fit_placements(
        remnant,
        part,
        config=config,
        fit_config=fit_config or RemnantFitConfig(),
    ).placements


def _summary(
    generation: _CandidateGeneration,
    *,
    evaluated_candidate_count: int,
    rejection_counts: Counter[str],
) -> FitSearchAttemptSummary:
    return FitSearchAttemptSummary(
        generated_candidate_count=generation.generated_candidate_count,
        duplicate_candidate_count=generation.duplicate_candidate_count,
        unique_candidate_count=generation.unique_candidate_count,
        budgeted_candidate_count=len(generation.placements),
        evaluated_candidate_count=evaluated_candidate_count,
        budget_truncated_candidate_count=generation.budget_truncated_candidate_count,
        rejection_counts=tuple(
            FitSearchRejectionCount(error_code=code, count=count)
            for code, count in sorted(rejection_counts.items())
        ),
    )


def search_fit_witness(
    remnant: RemnantStock,
    part: Part,
    *,
    part_material: MaterialIdentity,
    fit_config: RemnantFitConfig,
    search_config: FitSearchConfig,
) -> FitSearchResult:
    """Return the first exactly validated fit in the registered bounded search."""

    if material_key(part_material) != material_key(remnant.material):
        raise ReuseGeometryError("material_mismatch", "part and remnant material are incompatible")

    generation = _generate_fit_placements(
        remnant,
        part,
        config=search_config,
        fit_config=fit_config,
    )
    rejection_counts: Counter[str] = Counter()
    rotated_by_angle = dict(generation.rotated_parts)
    for index, placement in enumerate(generation.placements, start=1):
        try:
            translation_x, translation_y = placement.translation
            placed_polygon = translate(
                rotated_by_angle[placement.rotation],
                xoff=translation_x,
                yoff=translation_y,
            )
            _validate_pretransformed_fit_placement(
                remnant,
                generation.remnant_polygon,
                part,
                placement,
                placed_polygon,
                part_material=part_material,
                config=fit_config,
            )
        except ReuseGeometryError as error:
            rejection_counts[error.code] += 1
            continue
        return FitSearchResult(
            status=FitSearchStatus.FIT,
            parent_remnant_id=remnant.remnant_id,
            part_id=part.id,
            config=search_config,
            summary=_summary(
                generation,
                evaluated_candidate_count=index,
                rejection_counts=rejection_counts,
            ),
            placement=placement,
        )

    return FitSearchResult(
        status=FitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH,
        parent_remnant_id=remnant.remnant_id,
        part_id=part.id,
        config=search_config,
        summary=_summary(
            generation,
            evaluated_candidate_count=len(generation.placements),
            rejection_counts=rejection_counts,
        ),
    )
