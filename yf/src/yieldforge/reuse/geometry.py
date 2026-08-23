"""Fail-closed exact-vector operations for remnant reuse."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import BufferJoinStyle, Polygon
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry

from yieldforge.domain import Part
from yieldforge.residuals.contracts import (
    ResidualGeometryError,
    ResidualRuleName,
    ResidualRuleSet,
)
from yieldforge.residuals.geometry import (
    classify_residual_components,
    geometry_sha256,
    measure_residual_components,
    polygon_components,
)
from yieldforge.reuse.contracts import (
    ChildRemnantSummary,
    FitPlacement,
    MaterialIdentity,
    RemnantFitConfig,
    RemnantFitResult,
    RemnantStock,
    ReuseAccounting,
    ReuseGeometryError,
    canonical_polygon_record,
    child_lineage,
    derive_remnant_id,
    polygon_from_record,
)


@dataclass(frozen=True)
class ValidatedFitPlacement:
    """Runtime geometry for one exactly validated remnant placement."""

    placement: FitPlacement
    placed_polygon: Polygon
    buffered_footprint: Polygon
    area_tolerance: float


@dataclass(frozen=True)
class RemnantConsumption:
    """Runtime polygons paired with one persisted reuse result."""

    result: RemnantFitResult
    children: tuple[RemnantStock, ...]
    unused_geometry: BaseGeometry
    scrap_components: tuple[Polygon, ...]


def material_key(material: MaterialIdentity) -> tuple[str, str, str, str, str]:
    """Return the exact five-field M0 compatibility identity."""

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


def rotate_part(part: Part, rotation: float) -> Polygon:
    """Apply the canonical local-origin rotation to one source part."""

    source = _source_polygon(part)
    radians = math.radians(rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    rotated = Polygon(
        [
            (
                x * cosine - y * sine,
                x * sine + y * cosine,
            )
            for x, y in source.exterior.coords
        ]
    )
    if rotated.is_empty or rotated.area <= 0 or not rotated.is_valid:
        raise ReuseGeometryError(
            "invalid_transformed_polygon", "part rotation produced invalid geometry"
        )
    return rotated


def transform_part(part: Part, placement: FitPlacement) -> Polygon:
    """Apply the canonical rotation-before-translation convention to one part."""

    rotated = rotate_part(part, placement.rotation)
    translate_x, translate_y = placement.translation
    transformed = Polygon([(x + translate_x, y + translate_y) for x, y in rotated.exterior.coords])
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

    return _validate_fit_placement_against_polygon(
        remnant,
        polygon_from_record(remnant.geometry),
        part,
        placement,
        part_material=part_material,
        config=config,
    )


def _validate_fit_placement_against_polygon(
    remnant: RemnantStock,
    remnant_polygon: Polygon,
    part: Part,
    placement: FitPlacement,
    *,
    part_material: MaterialIdentity,
    config: RemnantFitConfig,
) -> ValidatedFitPlacement:
    """Validate against one already canonicalized polygon from the same remnant record."""

    return _validate_pretransformed_fit_placement(
        remnant,
        remnant_polygon,
        part,
        placement,
        transform_part(part, placement),
        part_material=part_material,
        config=config,
    )


def _validate_pretransformed_fit_placement(
    remnant: RemnantStock,
    remnant_polygon: Polygon,
    part: Part,
    placement: FitPlacement,
    placed_polygon: Polygon,
    *,
    part_material: MaterialIdentity,
    config: RemnantFitConfig,
) -> ValidatedFitPlacement:
    """Validate one exact cached transform against an already canonicalized remnant."""

    if placement.part_id != part.id:
        raise ReuseGeometryError("part_id_mismatch", "placement part ID does not match the part")
    if part.demand != 1:
        raise ReuseGeometryError(
            "unsupported_part_demand", "remnant fit requires one explicit part instance"
        )
    if material_key(part_material) != material_key(remnant.material):
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

    if placed_polygon.is_empty or placed_polygon.area <= 0 or not placed_polygon.is_valid:
        raise ReuseGeometryError(
            "invalid_transformed_polygon", "part placement produced invalid geometry"
        )
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
        outside_area = (
            0.0
            if remnant_polygon.covers(buffered)
            else float(buffered.difference(remnant_polygon).area)
        )
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


def _reconciliation_delta(parent_area: float, *category_areas: float) -> float:
    return abs(parent_area - sum(category_areas))


def consume_remnant(
    remnant: RemnantStock,
    part: Part,
    placement: FitPlacement,
    *,
    part_material: MaterialIdentity,
    rules: ResidualRuleSet,
    config: RemnantFitConfig,
) -> RemnantConsumption:
    """Consume one exactly placed part and create classified child remnants."""

    validated = validate_fit_placement(
        remnant,
        part,
        placement,
        part_material=part_material,
        config=config,
    )
    parent = polygon_from_record(remnant.geometry)
    try:
        placed_material = validated.placed_polygon.intersection(parent)
        buffered_material = validated.buffered_footprint.intersection(parent)
        process_loss = buffered_material.difference(placed_material)
        unused = parent.difference(buffered_material)
    except GEOSException as error:
        raise ReuseGeometryError(
            "geometry_operation_failed", "remnant consumption overlay failed"
        ) from error
    for label, geometry in (
        ("placed", placed_material),
        ("process loss", process_loss),
        ("unused", unused),
    ):
        if not geometry.is_valid:
            raise ReuseGeometryError("invalid_overlay_geometry", f"{label} geometry is invalid")

    try:
        components = polygon_components(unused)
        metrics = measure_residual_components(
            components,
            access_boundary=parent.boundary,
            rules=rules,
            reference_short_side=remnant.root_sheet_short_side,
            coordinate_tolerance=config.coordinate_tolerance,
        )
        classifications = classify_residual_components(
            components,
            metrics,
            rules,
            reference_area=remnant.root_sheet_area,
            reference_short_side=remnant.root_sheet_short_side,
            area_tolerance=validated.area_tolerance,
            coordinate_tolerance=config.coordinate_tolerance,
        )
    except ResidualGeometryError as error:
        raise ReuseGeometryError(error.code, "child residual classification failed") from error

    primary = next(item for item in classifications if item.rule_name is ResidualRuleName.PRIMARY)
    component_by_hash = {geometry_sha256(component): component for component in components}
    children = []
    for component_hash in primary.retained_component_sha256:
        geometry = canonical_polygon_record(component_by_hash[component_hash])
        lineage = child_lineage(remnant, source_component_sha256=geometry.polygon_sha256)
        children.append(
            RemnantStock(
                remnant_id=derive_remnant_id(lineage, geometry, remnant.material),
                geometry=geometry,
                material=remnant.material,
                root_sheet_area=remnant.root_sheet_area,
                root_sheet_short_side=remnant.root_sheet_short_side,
                lineage=lineage,
            )
        )
    children.sort(key=lambda child: child.remnant_id)
    child_tuple = tuple(children)
    scrap_components = tuple(
        component_by_hash[component_hash] for component_hash in primary.scrap_component_sha256
    )

    category_areas = (
        float(placed_material.area),
        float(process_loss.area),
        primary.retained_area,
        primary.scrap_area,
    )
    delta = _reconciliation_delta(float(parent.area), *category_areas)
    if delta > validated.area_tolerance:
        raise ReuseGeometryError(
            "material_reconciliation_failed",
            f"material accounting delta {delta} exceeds tolerance {validated.area_tolerance}",
        )
    accounting = ReuseAccounting(
        parent_remnant_area=float(parent.area),
        placed_area=category_areas[0],
        process_loss_area=category_areas[1],
        retained_child_area=category_areas[2],
        scrap_area=category_areas[3],
        reconciliation_delta=delta,
        area_tolerance=validated.area_tolerance,
    )
    placed_record = canonical_polygon_record(validated.placed_polygon)
    result = RemnantFitResult(
        status="fit",
        parent_remnant_id=remnant.remnant_id,
        part_id=part.id,
        placement=placement,
        placed_polygon_sha256=placed_record.polygon_sha256,
        accounting=accounting,
        children=tuple(
            ChildRemnantSummary(
                remnant_id=child.remnant_id,
                polygon_sha256=child.geometry.polygon_sha256,
                area=child.geometry.area,
            )
            for child in child_tuple
        ),
    )
    return RemnantConsumption(
        result=result,
        children=child_tuple,
        unused_geometry=unused,
        scrap_components=scrap_components,
    )
