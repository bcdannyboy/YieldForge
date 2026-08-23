from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from shapely import Polygon

from yieldforge.domain import Part
from yieldforge.reuse.contracts import (
    FitPlacement,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    ReuseGeometryError,
    canonical_polygon_record,
    derive_remnant_id,
)
from yieldforge.reuse.geometry import validate_fit_placement


def _material(*, grade: str = "assumed-uniform") -> MaterialIdentity:
    return MaterialIdentity(
        material_code="assumed-uniform",
        grade=grade,
        thickness="assumed-uniform",
        surface="assumed-uniform",
        grain="assumed-uniform",
        provenance=MaterialProvenance.ASSUMED,
    )


def _remnant(polygon: Polygon) -> RemnantStock:
    geometry = canonical_polygon_record(polygon)
    lineage = RemnantLineage.root(
        root_stock_id="stock-fixture",
        source_candidate_id="candidate-fixture",
        source_component_sha256=geometry.polygon_sha256,
    )
    material = _material()
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, material),
        geometry=geometry,
        material=material,
        root_sheet_area=100.0,
        root_sheet_short_side=10.0,
        lineage=lineage,
    )


def _part(
    shape: list[tuple[float, float]],
    *,
    allowed: list[float] | None = None,
) -> Part:
    return Part(
        id="part-a",
        shape=shape,
        demand=1,
        allowed_orientations=[0.0, 90.0] if allowed is None else allowed,
    )


def test_validates_rotation_then_translation_inside_concave_remnant() -> None:
    remnant = _remnant(Polygon([(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]))
    part = _part([(0.0, 0.0), (2.0, 0.0), (0.0, 1.0)])

    validated = validate_fit_placement(
        remnant,
        part,
        FitPlacement(part_id=part.id, rotation=90.0, translation=(1.0, 1.0)),
        part_material=remnant.material,
        config=RemnantFitConfig(),
    )

    remnant_polygon = Polygon([(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)])
    assert remnant_polygon.covers(validated.buffered_footprint)
    assert validated.placed_polygon.area == pytest.approx(1.0)
    assert validated.placed_polygon.equals_exact(
        Polygon([(1.0, 1.0), (1.0, 3.0), (0.0, 1.0)]),
        tolerance=1e-12,
    )


def test_rejects_placement_that_crosses_a_concavity_despite_area_and_bounds() -> None:
    remnant = _remnant(Polygon([(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]))
    part = _part([(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)])

    with pytest.raises(ReuseGeometryError) as captured:
        validate_fit_placement(
            remnant,
            part,
            FitPlacement(part_id=part.id, rotation=0.0, translation=(1.0, 1.0)),
            part_material=remnant.material,
            config=RemnantFitConfig(),
        )

    assert captured.value.code == "placement_outside_remnant"


def test_remnant_holes_are_unavailable_material() -> None:
    remnant = _remnant(
        Polygon(
            [(0, 0), (6, 0), (6, 6), (0, 6)],
            holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]],
        )
    )
    part = _part([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])

    with pytest.raises(ReuseGeometryError) as captured:
        validate_fit_placement(
            remnant,
            part,
            FitPlacement(part_id=part.id, rotation=0.0, translation=(2.0, 2.0)),
            part_material=remnant.material,
            config=RemnantFitConfig(),
        )

    assert captured.value.code == "placement_outside_remnant"


def test_boundary_touch_passes_at_zero_clearance_and_fails_with_clearance() -> None:
    remnant = _remnant(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))
    part = _part([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])
    placement = FitPlacement(part_id=part.id, rotation=0.0, translation=(0.0, 0.0))

    validated = validate_fit_placement(
        remnant,
        part,
        placement,
        part_material=remnant.material,
        config=RemnantFitConfig(),
    )
    assert validated.buffered_footprint.area == pytest.approx(4.0)

    with pytest.raises(ReuseGeometryError) as captured:
        validate_fit_placement(
            remnant,
            part,
            placement,
            part_material=remnant.material,
            config=RemnantFitConfig(clearance_distance=0.1),
        )
    assert captured.value.code == "placement_outside_remnant"


def test_rejects_unlisted_rotation_part_mismatch_and_material_mismatch() -> None:
    remnant = _remnant(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))
    part = _part(
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        allowed=[0.0],
    )

    cases = (
        (
            FitPlacement(part_id=part.id, rotation=90.0, translation=(0.0, 0.0)),
            remnant.material,
            "rotation_not_allowed",
        ),
        (
            FitPlacement(part_id="other-part", rotation=0.0, translation=(0.0, 0.0)),
            remnant.material,
            "part_id_mismatch",
        ),
        (
            FitPlacement(part_id=part.id, rotation=0.0, translation=(0.0, 0.0)),
            _material(grade="different"),
            "material_mismatch",
        ),
    )
    for placement, material, expected_code in cases:
        with pytest.raises(ReuseGeometryError) as captured:
            validate_fit_placement(
                remnant,
                part,
                placement,
                part_material=material,
                config=RemnantFitConfig(),
            )
        assert captured.value.code == expected_code


def test_contracts_reject_nonfinite_placement_and_source_geometry() -> None:
    with pytest.raises(ValidationError):
        FitPlacement(part_id="part-a", rotation=0.0, translation=(math.inf, 0.0))

    remnant = _remnant(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))
    part = _part([(0.0, 0.0), (1.0, 0.0), (math.nan, 1.0)])
    with pytest.raises(ReuseGeometryError) as captured:
        validate_fit_placement(
            remnant,
            part,
            FitPlacement(part_id=part.id, rotation=0.0, translation=(0.0, 0.0)),
            part_material=remnant.material,
            config=RemnantFitConfig(),
        )
    assert captured.value.code == "nonfinite_geometry"
