"""Fail-closed exact-vector operations for remnant reuse."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import BufferJoinStyle, Polygon
from shapely.errors import GEOSException

from yieldforge.domain import Part
from yieldforge.reuse.contracts import (
    FitPlacement,
    MaterialIdentity,
    RemnantFitConfig,
    RemnantStock,
    ReuseGeometryError,
    polygon_from_record,
)


@dataclass(frozen=True)
class ValidatedFitPlacement:
    """Runtime geometry for one exactly validated remnant placement."""

    placement: FitPlacement
    placed_polygon: Polygon
    buffered_footprint: Polygon
    area_tolerance: float


def _material_key(material: MaterialIdentity) -> tuple[str, str, str, str, str]:
    return (
        material.material_code,
        material.grade,
        material.thickness,
        material.surface,
        material.grain,
    )


def _source_polygon(part: Part) -> Polygon:
    if any(not math.isfinite(value) for point in part.shape for value in point[:2]):
        raise ReuseGeometryError("nonfinite_geometry", "part coordinates must be finite")
    polygon = Polygon(part.shape)
    if polygon.is_empty or polygon.area <= 0 or not polygon.is_valid:
        raise ReuseGeometryError(
            "invalid_part_polygon", "part must be a valid positive-area polygon"
        )
    return polygon


def _rotation_allowed(rotation: float, allowed: list[float], tolerance: float) -> bool:
    return any(abs(math.remainder(rotation - value, 360.0)) <= tolerance for value in allowed)


def _transform_part(part: Part, placement: FitPlacement) -> Polygon:
    source = _source_polygon(part)
    radians = math.radians(placement.rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    translate_x, translate_y = placement.translation
    transformed = Polygon(
        [
            (
                x * cosine - y * sine + translate_x,
                x * sine + y * cosine + translate_y,
            )
            for x, y in source.exterior.coords
        ]
    )
    if transformed.is_empty or transformed.area <= 0 or not transformed.is_valid:
        raise ReuseGeometryError(
            "invalid_transformed_polygon", "part placement produced invalid geometry"
        )
    return transformed


def validate_fit_placement(
    remnant: RemnantStock,
    part: Part,
    placement: FitPlacement,
    *,
    part_material: MaterialIdentity,
    config: RemnantFitConfig,
) -> ValidatedFitPlacement:
    """Validate one explicit part transform against an exact irregular remnant."""

    if placement.part_id != part.id:
        raise ReuseGeometryError("part_id_mismatch", "placement part ID does not match the part")
    if part.demand != 1:
        raise ReuseGeometryError(
            "unsupported_part_demand", "remnant fit requires one explicit part instance"
        )
    if _material_key(part_material) != _material_key(remnant.material):
        raise ReuseGeometryError("material_mismatch", "part and remnant material are incompatible")
    if part.allowed_orientations is None:
        raise ReuseGeometryError(
            "missing_allowed_orientations", "remnant fit requires explicit allowed rotations"
        )
    if not all(math.isfinite(value) for value in part.allowed_orientations):
        raise ReuseGeometryError("nonfinite_geometry", "allowed rotations must be finite")
    if not _rotation_allowed(
        placement.rotation,
        part.allowed_orientations,
        config.coordinate_tolerance,
    ):
        raise ReuseGeometryError(
            "rotation_not_allowed", "placement rotation is not allowed by the source part"
        )

    remnant_polygon = polygon_from_record(remnant.geometry)
    placed_polygon = _transform_part(part, placement)
    try:
        buffered = (
            placed_polygon.buffer(
                config.clearance_distance,
                join_style=BufferJoinStyle.mitre,
            )
            if config.clearance_distance > 0
            else placed_polygon
        )
        if not isinstance(buffered, Polygon) or buffered.is_empty or not buffered.is_valid:
            raise ReuseGeometryError(
                "invalid_buffered_footprint", "clearance produced invalid footprint geometry"
            )
        area_tolerance = max(
            config.coordinate_tolerance,
            remnant_polygon.area * config.relative_area_tolerance,
        )
        outside_area = float(buffered.difference(remnant_polygon).area)
    except GEOSException as error:
        raise ReuseGeometryError(
            "geometry_operation_failed", "remnant containment validation failed"
        ) from error
    if outside_area > area_tolerance:
        raise ReuseGeometryError(
            "placement_outside_remnant", "part footprint lies outside remnant material"
        )
    return ValidatedFitPlacement(
        placement=placement,
        placed_polygon=placed_polygon,
        buffered_footprint=buffered,
        area_tolerance=area_tolerance,
    )
