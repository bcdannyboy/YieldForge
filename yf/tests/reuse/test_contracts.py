from __future__ import annotations

import pytest
from pydantic import ValidationError
from shapely import Polygon

from yieldforge.reuse.contracts import (
    CanonicalPolygon,
    ChildRemnantSummary,
    FitPlacement,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantFitResult,
    RemnantLineage,
    RemnantStock,
    ReuseAccounting,
    ReuseGeometryError,
    canonical_polygon_record,
    child_lineage,
    derive_remnant_id,
    polygon_from_record,
)


def _material() -> MaterialIdentity:
    return MaterialIdentity(
        material_code="assumed-uniform",
        grade="assumed-uniform",
        thickness="assumed-uniform",
        surface="assumed-uniform",
        grain="assumed-uniform",
        provenance=MaterialProvenance.ASSUMED,
    )


def _root_remnant() -> RemnantStock:
    geometry = canonical_polygon_record(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))
    lineage = RemnantLineage.root(
        root_stock_id="stock-fixture",
        source_candidate_id="candidate-fixture",
        source_component_sha256=geometry.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, _material()),
        geometry=geometry,
        material=_material(),
        root_sheet_area=100.0,
        root_sheet_short_side=10.0,
        lineage=lineage,
    )


def test_canonical_polygon_round_trips_and_binds_root_remnant_identity() -> None:
    remnant = _root_remnant()

    decoded = polygon_from_record(remnant.geometry)

    assert decoded.area == 16.0
    assert decoded.is_valid
    assert remnant.lineage.generation == 1
    assert remnant.lineage.parent_remnant_id is None
    assert remnant.lineage.ancestor_remnant_ids == ()
    assert remnant.remnant_id.startswith("yfrm-")


def test_canonical_polygon_rejects_tampering_and_nonpolygonal_geometry() -> None:
    record = canonical_polygon_record(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))

    with pytest.raises(ReuseGeometryError, match="hash"):
        polygon_from_record(record.model_copy(update={"polygon_sha256": "f" * 64}))
    with pytest.raises(ValidationError):
        CanonicalPolygon(
            wkb_hex="not-hex",
            polygon_sha256="a" * 64,
            area=16.0,
        )
    with pytest.raises(ReuseGeometryError, match="polygon"):
        canonical_polygon_record(Polygon())


def test_material_and_geometry_config_are_strict_and_finite() -> None:
    assert _material().provenance is MaterialProvenance.ASSUMED
    assert RemnantFitConfig().clearance_distance == 0.0

    with pytest.raises(ValidationError):
        RemnantFitConfig(clearance_distance=-0.1)
    with pytest.raises(ValidationError):
        RemnantFitConfig(coordinate_tolerance=float("nan"))
    with pytest.raises(ValidationError):
        MaterialIdentity(
            material_code="",
            grade="g",
            thickness="t",
            surface="s",
            grain="n",
            provenance=MaterialProvenance.ASSUMED,
        )


def test_child_lineage_is_ordered_acyclic_and_generation_bound() -> None:
    parent = _root_remnant()
    child_geometry = canonical_polygon_record(Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))

    lineage = child_lineage(parent, source_component_sha256=child_geometry.polygon_sha256)
    child = RemnantStock(
        remnant_id=derive_remnant_id(lineage, child_geometry, parent.material),
        geometry=child_geometry,
        material=parent.material,
        root_sheet_area=parent.root_sheet_area,
        root_sheet_short_side=parent.root_sheet_short_side,
        lineage=lineage,
    )

    assert child.lineage.parent_remnant_id == parent.remnant_id
    assert child.lineage.ancestor_remnant_ids == (parent.remnant_id,)
    assert child.lineage.generation == 2

    with pytest.raises(ValidationError, match="ancestors"):
        RemnantLineage(
            root_stock_id="stock-fixture",
            parent_remnant_id=parent.remnant_id,
            ancestor_remnant_ids=(parent.remnant_id, parent.remnant_id),
            generation=2,
            source_candidate_id="candidate-fixture",
            source_component_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="generation"):
        RemnantLineage(
            root_stock_id="stock-fixture",
            parent_remnant_id=parent.remnant_id,
            ancestor_remnant_ids=(parent.remnant_id,),
            generation=3,
            source_candidate_id="candidate-fixture",
            source_component_sha256="a" * 64,
        )


def test_remnant_stock_rejects_identity_and_root_binding_tampering() -> None:
    remnant = _root_remnant()

    with pytest.raises(ValidationError, match="identity"):
        RemnantStock(**(remnant.model_dump() | {"remnant_id": "yfrm-" + "f" * 24}))
    with pytest.raises(ValidationError):
        RemnantStock(**(remnant.model_dump() | {"root_sheet_area": 0.0}))


def test_fit_result_requires_reconciled_success_or_error_only() -> None:
    accounting = ReuseAccounting(
        parent_remnant_area=16.0,
        placed_area=4.0,
        process_loss_area=0.0,
        retained_child_area=12.0,
        scrap_area=0.0,
        reconciliation_delta=0.0,
        area_tolerance=1e-7,
    )
    result = RemnantFitResult(
        status="fit",
        parent_remnant_id=_root_remnant().remnant_id,
        part_id="part-a",
        placement=FitPlacement(part_id="part-a", rotation=0.0, translation=(1.0, 1.0)),
        placed_polygon_sha256="a" * 64,
        accounting=accounting,
        children=(
            ChildRemnantSummary(
                remnant_id="yfrm-" + "b" * 24,
                polygon_sha256="b" * 64,
                area=12.0,
            ),
        ),
    )
    assert result.status == "fit"

    with pytest.raises(ValidationError, match="successful fit"):
        RemnantFitResult(
            status="fit",
            parent_remnant_id=_root_remnant().remnant_id,
            part_id="part-a",
            error_code="outside_remnant",
        )
    with pytest.raises(ValidationError, match="failed fit"):
        RemnantFitResult(
            status="invalid",
            parent_remnant_id=_root_remnant().remnant_id,
            part_id="part-a",
            error_code="outside_remnant",
            accounting=accounting,
        )
