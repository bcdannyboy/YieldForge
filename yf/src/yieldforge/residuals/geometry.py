"""Fail-closed exact-vector operations for residual material."""

from __future__ import annotations

import math

from shapely import Polygon, box, union_all
from shapely.errors import GEOSException

from yieldforge.domain import Candidate, Part, Placement, StripPackingProblem
from yieldforge.residuals.contracts import ResidualGeometryConfig, ResidualGeometryError


def _require_finite(value: float, *, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ResidualGeometryError("nonfinite_geometry", f"{label} must be finite")
    return number


def _source_polygon(part: Part) -> Polygon:
    points = [
        (
            _require_finite(x, label=f"{part.id} source x"),
            _require_finite(y, label=f"{part.id} source y"),
        )
        for x, y in part.shape
    ]
    polygon = Polygon(points)
    if polygon.is_empty or polygon.area <= 0 or not polygon.is_valid:
        raise ResidualGeometryError(
            "invalid_source_polygon", f"part {part.id} is not a valid positive-area polygon"
        )
    return polygon


def _transform_polygon(part: Part, placement: Placement) -> Polygon:
    source = _source_polygon(part)
    rotation = _require_finite(placement.rotation, label=f"{part.id} rotation")
    translate_x = _require_finite(placement.translation[0], label=f"{part.id} translation x")
    translate_y = _require_finite(placement.translation[1], label=f"{part.id} translation y")
    radians = math.radians(rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
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
        raise ResidualGeometryError(
            "invalid_transformed_polygon",
            f"placement for part {part.id} produced invalid geometry",
        )
    return transformed


def placed_part_polygons(
    problem: StripPackingProblem,
    candidate: Candidate,
    config: ResidualGeometryConfig,
) -> dict[str, Polygon]:
    """Replay one complete demand-one candidate and reject infeasible material geometry."""

    if any(part.demand != 1 for part in problem.parts):
        raise ResidualGeometryError(
            "unsupported_part_demand",
            "residual replay requires explicit demand-one placement identifiers",
        )

    placement_ids = [placement.part_id for placement in candidate.placements]
    if len(placement_ids) != len(set(placement_ids)):
        raise ResidualGeometryError(
            "duplicate_placement_id", "candidate repeats a placement part ID"
        )
    parts = {part.id: part for part in problem.parts}
    if set(placement_ids) != set(parts):
        raise ResidualGeometryError(
            "placement_id_mismatch", "candidate placement IDs do not match problem part IDs"
        )

    sheet_length = _require_finite(problem.sheet_length, label="sheet length")
    strip_height = _require_finite(problem.strip_height, label="strip height")
    stock = box(0.0, 0.0, sheet_length, strip_height)
    area_tolerance = max(
        config.coordinate_tolerance,
        stock.area * config.relative_area_tolerance,
    )

    placements = {placement.part_id: placement for placement in candidate.placements}
    placed = {
        part.id: _transform_polygon(part, placements[part.id]) for part in problem.parts
    }
    try:
        for part_id, polygon in placed.items():
            if polygon.difference(stock).area > area_tolerance:
                raise ResidualGeometryError(
                    "placed_material_out_of_sheet",
                    f"placed material for part {part_id} lies outside the fixed sheet",
                )
        union = union_all(tuple(placed.values()))
    except GEOSException as error:
        raise ResidualGeometryError(
            "geometry_operation_failed", "placed geometry validation failed"
        ) from error

    overlap_area = sum(polygon.area for polygon in placed.values()) - union.area
    if overlap_area > area_tolerance:
        raise ResidualGeometryError(
            "placed_material_overlap", "placed part polygons overlap in material area"
        )
    return placed
