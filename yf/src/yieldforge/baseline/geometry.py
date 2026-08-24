"""Exact complete-layout validation, remnant search, and residual consumption for M7."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import Polygon, union_all
from shapely.affinity import translate
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep

from yieldforge.baseline.contracts import (
    LayoutFitSearchConfig,
    LayoutFitSearchResult,
    LayoutFitSearchStatus,
    PlacedPartEvidence,
)
from yieldforge.domain import Candidate, Part, StripPackingProblem
from yieldforge.residuals.contracts import ResidualGeometryError, ResidualRuleName, ResidualRuleSet
from yieldforge.residuals.geometry import (
    classify_residual_components,
    geometry_sha256,
    measure_residual_components,
    polygon_components,
)
from yieldforge.reuse.contracts import (
    FitPlacement,
    MaterialIdentity,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    ReuseAccounting,
    canonical_polygon_record,
    child_lineage,
    derive_remnant_id,
    polygon_from_record,
)
from yieldforge.reuse.geometry import material_key, transform_part, validate_fit_placement


@dataclass(frozen=True)
class LayoutConsumption:
    placements: tuple[FitPlacement, ...]
    placed_parts: tuple[PlacedPartEvidence, ...]
    accounting: ReuseAccounting
    children: tuple[RemnantStock, ...]


@dataclass(frozen=True)
class PreparedLayoutFootprint:
    candidate_id: str
    geometry: BaseGeometry
    part_polygons: tuple[Polygon, ...]
    vertices: tuple[tuple[float, float], ...]
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class PreparedRemnantGeometry:
    remnant_id: str
    geometry: BaseGeometry
    prepared: PreparedGeometry
    vertices: tuple[tuple[float, float], ...]
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class LayoutTranslationCandidates:
    """One bounded registered translation sequence, reusable across collision engines."""

    candidate_id: str
    remnant_id: str
    translations: tuple[tuple[float, float], ...]
    generated_candidate_count: int
    duplicate_candidate_count: int
    budget_truncated: bool


def _rotation_allowed(rotation: float, allowed: list[float], tolerance: float) -> bool:
    return any(abs(math.remainder(rotation - item, 360.0)) <= tolerance for item in allowed)


def _translated_layout(
    problem: StripPackingProblem,
    candidate: Candidate,
    translation_xy: tuple[float, float],
    *,
    coordinate_tolerance: float,
    area_tolerance: float,
) -> tuple[tuple[FitPlacement, ...], tuple[Polygon, ...], BaseGeometry]:
    if any(part.demand != 1 for part in problem.parts):
        raise ValueError("M7 complete-layout actions require explicit demand-one parts")
    parts = {part.id: part for part in problem.parts}
    placement_ids = tuple(item.part_id for item in candidate.placements)
    if len(placement_ids) != len(set(placement_ids)) or set(placement_ids) != set(parts):
        raise ValueError("layout placements do not match problem parts uniquely")
    by_id = {item.part_id: item for item in candidate.placements}
    placements = []
    polygons = []
    for part in problem.parts:
        source = by_id[part.id]
        if part.allowed_orientations is None or not _rotation_allowed(
            source.rotation, part.allowed_orientations, coordinate_tolerance
        ):
            raise ValueError("layout uses a rotation not allowed by source geometry")
        placement = FitPlacement(
            part_id=part.id,
            rotation=source.rotation,
            translation=(
                source.translation[0] + translation_xy[0],
                source.translation[1] + translation_xy[1],
            ),
        )
        placements.append(placement)
        polygons.append(transform_part(part, placement))
    union = union_all(polygons)
    overlap_area = sum(item.area for item in polygons) - union.area
    if overlap_area > area_tolerance:
        raise ValueError("complete layout contains part overlap")
    return tuple(placements), tuple(polygons), union


def _vertices(geometry: BaseGeometry) -> tuple[tuple[float, float], ...]:
    polygons = polygon_components(geometry)
    return tuple(
        sorted(
            {
                (float(x), float(y))
                for polygon in polygons
                for ring in (polygon.exterior, *polygon.interiors)
                for x, y in tuple(ring.coords)[:-1]
            }
        )
    )


def _grid(start: float, stop: float, count: int) -> tuple[float, ...]:
    if count == 2:
        return (start, stop)
    step = (stop - start) / (count - 1)
    values = [start + index * step for index in range(count)]
    values[-1] = stop
    return tuple(0.0 if value == 0.0 else float(value) for value in values)


def prepare_layout_footprint(
    problem: StripPackingProblem,
    candidate: Candidate,
    fit_config: RemnantFitConfig,
) -> PreparedLayoutFootprint:
    """Validate and cache one rigid layout footprint for repeated remnant searches."""

    _, polygons, footprint = _translated_layout(
        problem,
        candidate,
        (0.0, 0.0),
        coordinate_tolerance=fit_config.coordinate_tolerance,
        area_tolerance=max(
            fit_config.coordinate_tolerance,
            problem.sheet_length * problem.strip_height * fit_config.relative_area_tolerance,
        ),
    )
    return PreparedLayoutFootprint(
        candidate_id=candidate.candidate_id,
        geometry=footprint,
        part_polygons=polygons,
        vertices=_vertices(footprint),
        bounds=footprint.bounds,
    )


def prepare_remnant_geometry(remnant: RemnantStock) -> PreparedRemnantGeometry:
    """Decode and prepare one remnant once for every candidate query in an event."""

    parent = polygon_from_record(remnant.geometry)
    return PreparedRemnantGeometry(
        remnant_id=remnant.remnant_id,
        geometry=parent,
        prepared=prep(parent),
        vertices=_vertices(parent),
        bounds=parent.bounds,
    )


def generate_layout_translations(
    remnant: RemnantStock,
    candidate: Candidate,
    *,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
    prepared_layout: PreparedLayoutFootprint,
    prepared_remnant: PreparedRemnantGeometry,
) -> LayoutTranslationCandidates:
    """Generate the frozen bounded translation sequence independently of a backend."""

    layout = prepared_layout
    parent_search = prepared_remnant
    if layout.candidate_id != candidate.candidate_id:
        raise ValueError("prepared layout does not match candidate")
    if parent_search.remnant_id != remnant.remnant_id:
        raise ValueError("prepared remnant geometry does not match stock")
    parent_min_x, parent_min_y, parent_max_x, parent_max_y = parent_search.bounds
    foot_min_x, foot_min_y, foot_max_x, foot_max_y = layout.bounds
    min_x = parent_min_x - foot_min_x
    max_x = parent_max_x - foot_max_x
    min_y = parent_min_y - foot_min_y
    max_y = parent_max_y - foot_max_y
    if (
        min_x > max_x + fit_config.coordinate_tolerance
        or min_y > max_y + fit_config.coordinate_tolerance
    ):
        return LayoutTranslationCandidates(
            candidate_id=candidate.candidate_id,
            remnant_id=remnant.remnant_id,
            translations=(),
            generated_candidate_count=0,
            duplicate_candidate_count=0,
            budget_truncated=False,
        )

    unique = []
    seen = set()
    duplicates = 0
    truncated = False

    def add_candidate(x: float, y: float) -> bool:
        nonlocal duplicates, truncated
        key = (0.0 if x == 0.0 else float(x), 0.0 if y == 0.0 else float(y))
        if key in seen:
            duplicates += 1
            return False
        seen.add(key)
        unique.append(key)
        if len(unique) > search_config.maximum_candidates:
            truncated = True
            return True
        return False

    for x, y in (
        (min_x, min_y),
        (min_x, max_y),
        (max_x, min_y),
        (max_x, max_y),
    ):
        if add_candidate(x, y):
            break
    for parent_x, parent_y in parent_search.vertices:
        if truncated:
            break
        for foot_x, foot_y in layout.vertices:
            x = parent_x - foot_x
            y = parent_y - foot_y
            if (
                min_x - fit_config.coordinate_tolerance
                <= x
                <= max_x + fit_config.coordinate_tolerance
                and min_y - fit_config.coordinate_tolerance
                <= y
                <= max_y + fit_config.coordinate_tolerance
            ):
                if add_candidate(x, y):
                    break
    if not truncated:
        for x in _grid(min_x, max_x, search_config.grid_columns):
            if truncated:
                break
            for y in _grid(min_y, max_y, search_config.grid_rows):
                if add_candidate(x, y):
                    break
    return LayoutTranslationCandidates(
        candidate_id=candidate.candidate_id,
        remnant_id=remnant.remnant_id,
        translations=tuple(unique[: search_config.maximum_candidates]),
        generated_candidate_count=len(unique),
        duplicate_candidate_count=duplicates,
        budget_truncated=truncated,
    )


def search_layout_translation(
    remnant: RemnantStock,
    problem: StripPackingProblem,
    candidate: Candidate,
    *,
    material: MaterialIdentity,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
    prepared_layout: PreparedLayoutFootprint | None = None,
    prepared_remnant: PreparedRemnantGeometry | None = None,
    translation_candidates: LayoutTranslationCandidates | None = None,
    collision_prefilter: tuple[bool, ...] | None = None,
) -> LayoutFitSearchResult:
    """Find the first bounded exact translation of a complete archived layout."""

    if material_key(material) != material_key(remnant.material):
        raise ValueError("layout material is incompatible with remnant")
    layout = prepared_layout or prepare_layout_footprint(problem, candidate, fit_config)
    parent_search = prepared_remnant or prepare_remnant_geometry(remnant)
    candidates = translation_candidates or generate_layout_translations(
        remnant,
        candidate,
        fit_config=fit_config,
        search_config=search_config,
        prepared_layout=layout,
        prepared_remnant=parent_search,
    )
    if (
        candidates.candidate_id != candidate.candidate_id
        or candidates.remnant_id != remnant.remnant_id
    ):
        raise ValueError("prepared translation candidates do not match search")
    if collision_prefilter is not None and len(collision_prefilter) != len(candidates.translations):
        raise ValueError("collision prefilter count differs from translation candidates")
    footprint = layout.geometry
    parent = parent_search.geometry
    area_tolerance = max(
        fit_config.coordinate_tolerance,
        parent.area * fit_config.relative_area_tolerance,
    )
    collisions = collision_prefilter or (False,) * len(candidates.translations)
    for evaluated, (offset, prefiltered_collision) in enumerate(
        zip(candidates.translations, collisions, strict=True),
        start=1,
    ):
        if prefiltered_collision:
            continue
        moved = translate(footprint, xoff=offset[0], yoff=offset[1])
        outside_area = (
            0.0 if parent_search.prepared.covers(moved) else moved.difference(parent).area
        )
        if outside_area <= area_tolerance:
            return LayoutFitSearchResult(
                status=LayoutFitSearchStatus.FIT,
                candidate_id=candidate.candidate_id,
                remnant_id=remnant.remnant_id,
                config=search_config,
                generated_candidate_count=candidates.generated_candidate_count,
                duplicate_candidate_count=candidates.duplicate_candidate_count,
                evaluated_candidate_count=evaluated,
                budget_truncated=candidates.budget_truncated,
                translation=offset,
            )
    return LayoutFitSearchResult(
        status=LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH,
        candidate_id=candidate.candidate_id,
        remnant_id=remnant.remnant_id,
        config=search_config,
        generated_candidate_count=candidates.generated_candidate_count,
        duplicate_candidate_count=candidates.duplicate_candidate_count,
        evaluated_candidate_count=len(candidates.translations),
        budget_truncated=candidates.budget_truncated,
    )


def consume_layout(
    stock: RemnantStock,
    problem: StripPackingProblem,
    candidate: Candidate,
    translation_xy: tuple[float, float],
    *,
    material: MaterialIdentity,
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
    reroot_standard_sheet: bool,
) -> LayoutConsumption:
    """Atomically consume every part in one archived layout from exact stock geometry."""

    placements, polygons, _union = _translated_layout(
        problem,
        candidate,
        translation_xy,
        coordinate_tolerance=fit_config.coordinate_tolerance,
        area_tolerance=max(
            fit_config.coordinate_tolerance,
            problem.sheet_length * problem.strip_height * fit_config.relative_area_tolerance,
        ),
    )
    part_by_id: dict[str, Part] = {item.id: item for item in problem.parts}
    validated = []
    for placement in placements:
        try:
            item = validate_fit_placement(
                stock,
                part_by_id[placement.part_id],
                placement,
                part_material=material,
                config=fit_config,
            )
        except ValueError as error:
            raise ValueError("complete layout part lies outside selected stock") from error
        validated.append(item)
    parent = polygon_from_record(stock.geometry)
    placed_union = union_all(tuple(item.placed_polygon for item in validated))
    buffered_union = union_all(tuple(item.buffered_footprint for item in validated))
    process_loss = buffered_union.difference(placed_union)
    unused = parent.difference(buffered_union)
    for label, geometry in (
        ("placed", placed_union),
        ("process loss", process_loss),
        ("unused", unused),
    ):
        if not geometry.is_valid:
            raise ValueError(f"complete-layout {label} geometry is invalid")
    try:
        components = polygon_components(unused)
        metrics = measure_residual_components(
            components,
            access_boundary=parent.boundary,
            rules=rules,
            reference_short_side=stock.root_sheet_short_side,
            coordinate_tolerance=fit_config.coordinate_tolerance,
        )
        area_tolerance = max(
            fit_config.coordinate_tolerance,
            parent.area * fit_config.relative_area_tolerance,
        )
        classifications = classify_residual_components(
            components,
            metrics,
            rules,
            reference_area=stock.root_sheet_area,
            reference_short_side=stock.root_sheet_short_side,
            area_tolerance=area_tolerance,
            coordinate_tolerance=fit_config.coordinate_tolerance,
        )
    except ResidualGeometryError as error:
        raise ValueError("complete-layout residual classification failed") from error
    primary = next(item for item in classifications if item.rule_name is ResidualRuleName.PRIMARY)
    component_by_hash = {geometry_sha256(item): item for item in components}
    children = []
    for component_hash in primary.retained_component_sha256:
        geometry = canonical_polygon_record(component_by_hash[component_hash])
        lineage = (
            RemnantLineage.root(
                root_stock_id=stock.lineage.root_stock_id,
                source_candidate_id=candidate.candidate_id,
                source_component_sha256=geometry.polygon_sha256,
            )
            if reroot_standard_sheet
            else child_lineage(stock, source_component_sha256=geometry.polygon_sha256)
        )
        children.append(
            RemnantStock(
                remnant_id=derive_remnant_id(lineage, geometry, material),
                geometry=geometry,
                material=material,
                root_sheet_area=stock.root_sheet_area,
                root_sheet_short_side=stock.root_sheet_short_side,
                lineage=lineage,
            )
        )
    children.sort(key=lambda item: item.remnant_id)
    category_areas = (
        float(placed_union.area),
        float(process_loss.area),
        primary.retained_area,
        primary.scrap_area,
    )
    delta = abs(parent.area - sum(category_areas))
    if delta > area_tolerance:
        raise ValueError("complete-layout material accounting does not reconcile")
    accounting = ReuseAccounting(
        parent_remnant_area=float(parent.area),
        placed_area=category_areas[0],
        process_loss_area=category_areas[1],
        retained_child_area=category_areas[2],
        scrap_area=category_areas[3],
        reconciliation_delta=delta,
        area_tolerance=area_tolerance,
    )
    return LayoutConsumption(
        placements=placements,
        placed_parts=tuple(
            PlacedPartEvidence(
                part_id=placement.part_id,
                geometry=canonical_polygon_record(polygon),
            )
            for placement, polygon in zip(placements, polygons, strict=True)
        ),
        accounting=accounting,
        children=tuple(children),
    )
