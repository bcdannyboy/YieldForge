"""Fail-closed exact-vector operations for residual material."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from shapely import (
    BufferJoinStyle,
    GeometryCollection,
    MultiPolygon,
    Polygon,
    box,
    normalize,
    to_wkb,
    union_all,
)
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry

from yieldforge.domain import Candidate, Part, Placement, StripPackingProblem
from yieldforge.residuals.contracts import (
    CandidateResidualObservation,
    ResidualAccounting,
    ResidualComponentMetrics,
    ResidualGeometryConfig,
    ResidualGeometryError,
    ResidualPairComparison,
    ResidualRule,
    ResidualRuleName,
    ResidualRuleSet,
    RuleClassificationSummary,
)


@dataclass(frozen=True)
class CandidateResidualExtraction:
    """Runtime geometry paired with its persistable observation."""

    observation: CandidateResidualObservation
    stock: Polygon
    residual: BaseGeometry
    component_geometries: tuple[Polygon, ...]


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


def geometry_sha256(geometry: BaseGeometry) -> str:
    """Hash normalized little-endian two-dimensional WKB."""

    try:
        canonical = normalize(geometry)
        encoded = to_wkb(canonical, byte_order=1, output_dimension=2)
    except GEOSException as error:
        raise ResidualGeometryError(
            "geometry_operation_failed", "geometry canonicalization failed"
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def _require_valid_geometry(geometry: BaseGeometry, *, label: str) -> None:
    if not geometry.is_valid:
        raise ResidualGeometryError("invalid_overlay_geometry", f"{label} geometry is invalid")


def _polygon_components(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, Polygon):
        return (geometry,)
    if isinstance(geometry, MultiPolygon):
        return tuple(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        polygons: list[Polygon] = []
        for item in geometry.geoms:
            if item.is_empty:
                continue
            if isinstance(item, Polygon):
                polygons.append(item)
            elif isinstance(item, MultiPolygon):
                polygons.extend(item.geoms)
            else:
                raise ResidualGeometryError(
                    "nonpolygonal_residual", "residual contains nonpolygonal geometry"
                )
        return tuple(polygons)
    raise ResidualGeometryError(
        "nonpolygonal_residual", "residual material is not polygonal"
    )


def _require_reconciliation(
    *,
    stock_area: float,
    category_areas: tuple[float, ...],
    area_tolerance: float,
) -> float:
    delta = abs(stock_area - sum(category_areas))
    if delta > area_tolerance:
        raise ResidualGeometryError(
            "material_reconciliation_failed",
            f"material accounting delta {delta} exceeds tolerance {area_tolerance}",
        )
    return delta


def _forbidden_geometry(config: ResidualGeometryConfig) -> BaseGeometry:
    polygons = []
    for ring in config.forbidden_polygons:
        polygon = Polygon(ring)
        if polygon.is_empty or polygon.area <= 0 or not polygon.is_valid:
            raise ResidualGeometryError(
                "invalid_forbidden_polygon", "forbidden polygon must be valid and positive-area"
            )
        polygons.append(polygon)
    return union_all(polygons) if polygons else GeometryCollection()


def _passes_effective_width(
    component: Polygon,
    rule: ResidualRule,
    *,
    short_side: float,
) -> bool:
    minimum_width = short_side * rule.minimum_effective_width_short_side_fraction
    if minimum_width <= 0:
        return True
    try:
        inward = component.buffer(
            -minimum_width / 2.0,
            join_style=BufferJoinStyle.mitre,
        )
    except GEOSException as error:
        raise ResidualGeometryError(
            "geometry_operation_failed", "effective width evaluation failed"
        ) from error
    return not inward.is_empty


def _classify_components(
    components: tuple[Polygon, ...],
    metrics: tuple[ResidualComponentMetrics, ...],
    rules: ResidualRuleSet,
    *,
    stock_area: float,
    short_side: float,
    area_tolerance: float,
    coordinate_tolerance: float,
) -> tuple[RuleClassificationSummary, ...]:
    component_by_hash = {geometry_sha256(component): component for component in components}
    metric_by_hash = {metric.component_sha256: metric for metric in metrics}
    summaries = []
    for rule in rules.ordered():
        retained = []
        scrap = []
        for component_hash, component in component_by_hash.items():
            metric = metric_by_hash[component_hash]
            minimum_area = stock_area * rule.minimum_area_sheet_fraction
            minimum_access = (
                short_side * rule.minimum_exterior_access_short_side_fraction
            )
            eligible = (
                (not rule.requires_exterior_connection or metric.exterior_connected)
                and component.area + area_tolerance >= minimum_area
                and rule.rule_name in metric.effective_width_rule_names
                and metric.exterior_access_length + coordinate_tolerance >= minimum_access
            )
            (retained if eligible else scrap).append((component_hash, float(component.area)))

        retained_hashes = tuple(sorted(item[0] for item in retained))
        scrap_hashes = tuple(sorted(item[0] for item in scrap))
        retained_areas = [item[1] for item in retained]
        summaries.append(
            RuleClassificationSummary(
                rule_name=rule.rule_name,
                retained_component_sha256=retained_hashes,
                scrap_component_sha256=scrap_hashes,
                retained_area=float(sum(retained_areas)),
                scrap_area=float(sum(item[1] for item in scrap)),
                largest_retained_component_area=(
                    float(max(retained_areas)) if retained_areas else 0.0
                ),
            )
        )
    return tuple(summaries)


def extract_candidate_residual(
    problem: StripPackingProblem,
    candidate: Candidate,
    rules: ResidualRuleSet,
    config: ResidualGeometryConfig,
) -> CandidateResidualExtraction:
    """Partition one fixed sheet into placed, loss, and residual material."""

    placed_by_id = placed_part_polygons(problem, candidate, config)
    stock = box(0.0, 0.0, problem.sheet_length, problem.strip_height)
    area_tolerance = max(
        config.coordinate_tolerance,
        stock.area * config.relative_area_tolerance,
    )

    try:
        placed_union = union_all(tuple(placed_by_id.values())).intersection(stock)
        if config.part_buffer_distance > 0:
            buffered_union = union_all(
                tuple(
                    polygon.buffer(
                        config.part_buffer_distance,
                        join_style=BufferJoinStyle.mitre,
                    )
                    for polygon in placed_by_id.values()
                )
            ).intersection(stock)
        else:
            buffered_union = placed_union
        process_loss = buffered_union.difference(placed_union)
        forbidden_raw = _forbidden_geometry(config).intersection(stock)
        forbidden_loss = forbidden_raw.difference(union_all((placed_union, process_loss)))
        removed = union_all((placed_union, process_loss, forbidden_loss))
        residual = stock.difference(removed)
    except GEOSException as error:
        raise ResidualGeometryError(
            "geometry_operation_failed", "residual overlay failed"
        ) from error

    for label, geometry in (
        ("placed", placed_union),
        ("process loss", process_loss),
        ("forbidden loss", forbidden_loss),
        ("residual", residual),
    ):
        _require_valid_geometry(geometry, label=label)

    components = _polygon_components(residual)
    short_side = min(problem.sheet_length, problem.strip_height)
    metrics = []
    for component in components:
        access = component.boundary.intersection(stock.boundary)
        effective_width_rules = tuple(
            rule.rule_name
            for rule in rules.ordered()
            if _passes_effective_width(component, rule, short_side=short_side)
        )
        metrics.append(
            ResidualComponentMetrics(
                component_sha256=geometry_sha256(component),
                area=float(component.area),
                bounds=tuple(float(value) for value in component.bounds),
                hole_count=len(component.interiors),
                exterior_connected=access.length > config.coordinate_tolerance,
                exterior_access_length=float(access.length),
                effective_width_rule_names=effective_width_rules,
            )
        )
    metrics.sort(key=lambda item: item.component_sha256)
    metric_tuple = tuple(metrics)

    residual_area = float(residual.area)
    reconciliation_delta = _require_reconciliation(
        stock_area=float(stock.area),
        category_areas=(
            float(placed_union.area),
            float(process_loss.area),
            float(forbidden_loss.area),
            residual_area,
        ),
        area_tolerance=area_tolerance,
    )
    accounting = ResidualAccounting(
        stock_area=float(stock.area),
        placed_area=float(placed_union.area),
        process_loss_area=float(process_loss.area),
        forbidden_loss_area=float(forbidden_loss.area),
        residual_area=residual_area,
        reconciliation_delta=reconciliation_delta,
        area_tolerance=area_tolerance,
    )
    observation = CandidateResidualObservation(
        candidate_id=candidate.candidate_id,
        valid=True,
        residual_sha256=geometry_sha256(residual),
        accounting=accounting,
        components=metric_tuple,
        classifications=_classify_components(
            components,
            metric_tuple,
            rules,
            stock_area=float(stock.area),
            short_side=short_side,
            area_tolerance=area_tolerance,
            coordinate_tolerance=config.coordinate_tolerance,
        ),
    )
    return CandidateResidualExtraction(
        observation=observation,
        stock=stock,
        residual=residual,
        component_geometries=components,
    )


def _classification_changed(
    first: RuleClassificationSummary,
    second: RuleClassificationSummary,
    *,
    area_tolerance: float,
) -> bool:
    return (
        len(first.retained_component_sha256) != len(second.retained_component_sha256)
        or len(first.scrap_component_sha256) != len(second.scrap_component_sha256)
        or abs(first.retained_area - second.retained_area) > area_tolerance
        or abs(first.scrap_area - second.scrap_area) > area_tolerance
        or abs(
            first.largest_retained_component_area - second.largest_retained_component_area
        )
        > area_tolerance
    )


def compare_candidate_residuals(
    first: CandidateResidualExtraction,
    second: CandidateResidualExtraction,
) -> ResidualPairComparison:
    """Compare exact residual geometry while keeping classification a separate diagnostic."""

    if not first.stock.equals(second.stock):
        raise ResidualGeometryError(
            "stock_mismatch", "candidate residuals do not share the same fixed stock"
        )
    first_accounting = first.observation.accounting
    second_accounting = second.observation.accounting
    if first_accounting is None or second_accounting is None:
        raise ResidualGeometryError(
            "invalid_observation", "candidate residual comparison requires valid observations"
        )
    try:
        symmetric_difference_area = float(
            first.residual.symmetric_difference(second.residual).area
        )
    except GEOSException as error:
        raise ResidualGeometryError(
            "geometry_operation_failed", "residual comparison failed"
        ) from error

    first_classifications = {
        item.rule_name: item for item in first.observation.classifications
    }
    second_classifications = {
        item.rule_name: item for item in second.observation.classifications
    }
    if set(first_classifications) != set(second_classifications):
        raise ResidualGeometryError(
            "classification_mismatch", "candidate residual rule sets do not match"
        )
    area_tolerance = max(first_accounting.area_tolerance, second_accounting.area_tolerance)
    changed_rules = tuple(
        rule_name
        for rule_name in (
            ResidualRuleName.PERMISSIVE,
            ResidualRuleName.PRIMARY,
            ResidualRuleName.CONSERVATIVE,
        )
        if _classification_changed(
            first_classifications[rule_name],
            second_classifications[rule_name],
            area_tolerance=area_tolerance,
        )
    )
    stock_area = float(first.stock.area)
    return ResidualPairComparison(
        first_candidate_id=first.observation.candidate_id,
        second_candidate_id=second.observation.candidate_id,
        exact_residual_equal=(
            first.observation.residual_sha256 == second.observation.residual_sha256
        ),
        symmetric_difference_area=symmetric_difference_area,
        symmetric_difference_sheet_fraction=symmetric_difference_area / stock_area,
        classification_difference_rule_names=changed_rules,
    )
