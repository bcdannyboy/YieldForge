from __future__ import annotations

import pytest
from pydantic import ValidationError
from shapely import Polygon

from yieldforge.domain import Part
from yieldforge.reuse.contracts import (
    FitPlacement,
    FitSearchAttemptSummary,
    FitSearchConfig,
    FitSearchRejectionCount,
    FitSearchResult,
    FitSearchStatus,
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
from yieldforge.reuse.search import generate_fit_placements, search_fit_witness


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
        allowed_orientations=[0.0] if allowed is None else allowed,
    )


def _summary(*, evaluated: int, rejected: int) -> FitSearchAttemptSummary:
    rejections = (
        (
            FitSearchRejectionCount(
                error_code="placement_outside_remnant",
                count=rejected,
            ),
        )
        if rejected
        else ()
    )
    return FitSearchAttemptSummary(
        generated_candidate_count=5,
        duplicate_candidate_count=1,
        unique_candidate_count=4,
        budgeted_candidate_count=4,
        evaluated_candidate_count=evaluated,
        budget_truncated_candidate_count=0,
        rejection_counts=rejections,
    )


def test_search_contracts_freeze_budget_ordering_and_exact_no_witness_status() -> None:
    config = FitSearchConfig(grid_columns=5, grid_rows=7, maximum_candidates=100)
    no_witness = FitSearchResult(
        status=FitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH,
        parent_remnant_id="yfrm-" + "a" * 24,
        part_id="part-a",
        config=config,
        summary=_summary(evaluated=4, rejected=4),
    )

    assert config.candidate_source_order == (
        "bbox_alignments",
        "vertex_alignments",
        "uniform_grid",
    )
    assert config.rotation_order == "ascending_numeric"
    assert config.transform_order == "rotation_translation_lexicographic"
    assert no_witness.status.value == "no_witness_within_registered_search"

    with pytest.raises(ValidationError):
        FitSearchConfig(grid_columns=1, grid_rows=7, maximum_candidates=100)
    with pytest.raises(ValidationError):
        FitSearchConfig(grid_columns=5, grid_rows=7, maximum_candidates=0)


def test_search_result_requires_consistent_fit_or_inconclusive_exhaustion() -> None:
    config = FitSearchConfig(grid_columns=3, grid_rows=3, maximum_candidates=10)
    fit = FitSearchResult(
        status=FitSearchStatus.FIT,
        parent_remnant_id="yfrm-" + "a" * 24,
        part_id="part-a",
        config=config,
        summary=_summary(evaluated=3, rejected=2),
        placement=FitPlacement(part_id="part-a", rotation=0.0, translation=(1.0, 1.0)),
    )

    assert fit.placement is not None

    with pytest.raises(ValidationError, match="fit result"):
        FitSearchResult(
            status=FitSearchStatus.FIT,
            parent_remnant_id="yfrm-" + "a" * 24,
            part_id="part-a",
            config=config,
            summary=_summary(evaluated=3, rejected=3),
        )
    with pytest.raises(ValidationError, match="no-witness"):
        FitSearchResult(
            status=FitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH,
            parent_remnant_id="yfrm-" + "a" * 24,
            part_id="part-a",
            config=config,
            summary=_summary(evaluated=3, rejected=2),
            placement=FitPlacement(part_id="part-a", rotation=0.0, translation=(1.0, 1.0)),
        )


def test_candidate_generation_is_deterministic_and_search_returns_exact_first_witness() -> None:
    remnant = _remnant(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))
    part = _part([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])
    search_config = FitSearchConfig(grid_columns=3, grid_rows=3, maximum_candidates=100)

    first = generate_fit_placements(remnant, part, config=search_config)
    second = generate_fit_placements(remnant, part, config=search_config)
    keys = tuple(
        (placement.rotation, placement.translation[0], placement.translation[1])
        for placement in first
    )

    assert first == second
    assert keys == tuple(sorted(set(keys)))
    assert all(
        0.0 <= placement.translation[0] <= 2.0 and 0.0 <= placement.translation[1] <= 2.0
        for placement in first
    )

    result = search_fit_witness(
        remnant,
        part,
        part_material=remnant.material,
        fit_config=RemnantFitConfig(),
        search_config=search_config,
    )

    assert result.status is FitSearchStatus.FIT
    assert result.placement is not None
    assert result.summary.generated_candidate_count > result.summary.unique_candidate_count
    validated = validate_fit_placement(
        remnant,
        part,
        result.placement,
        part_material=remnant.material,
        config=RemnantFitConfig(),
    )
    assert validated.buffered_footprint.area == pytest.approx(4.0)


def test_budget_truncation_is_visible_and_never_promoted_to_no_fit() -> None:
    remnant = _remnant(Polygon([(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]))
    part = _part([(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)])
    search_config = FitSearchConfig(grid_columns=3, grid_rows=3, maximum_candidates=1)

    result = search_fit_witness(
        remnant,
        part,
        part_material=remnant.material,
        fit_config=RemnantFitConfig(),
        search_config=search_config,
    )

    assert result.status is FitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH
    assert result.placement is None
    assert result.summary.budgeted_candidate_count == 1
    assert result.summary.evaluated_candidate_count == 1
    assert result.summary.budget_truncated_candidate_count > 0
    assert result.summary.rejection_counts == (
        FitSearchRejectionCount(error_code="placement_outside_remnant", count=1),
    )


@pytest.mark.parametrize(
    "remnant_polygon",
    [
        Polygon([(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]),
        Polygon(
            [(0, 0), (6, 0), (6, 6), (0, 6)],
            holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]],
        ),
    ],
)
def test_search_respects_concavities_and_holes(remnant_polygon: Polygon) -> None:
    remnant = _remnant(remnant_polygon)
    part = _part([(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)])

    result = search_fit_witness(
        remnant,
        part,
        part_material=remnant.material,
        fit_config=RemnantFitConfig(),
        search_config=FitSearchConfig(
            grid_columns=7,
            grid_rows=7,
            maximum_candidates=1000,
        ),
    )

    assert result.status is FitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH
    assert result.summary.evaluated_candidate_count > 0
    assert result.summary.rejection_counts == (
        FitSearchRejectionCount(
            error_code="placement_outside_remnant",
            count=result.summary.evaluated_candidate_count,
        ),
    )


def test_search_finds_rotated_fit_and_prefilters_impossible_dimensions(monkeypatch) -> None:
    remnant = _remnant(Polygon([(0, 0), (3, 0), (3, 2), (0, 2)]))
    rotated_part = _part(
        [(0.0, 0.0), (2.0, 0.0), (2.0, 3.0), (0.0, 3.0)],
        allowed=[0.0, 90.0],
    )
    config = FitSearchConfig(grid_columns=3, grid_rows=3, maximum_candidates=100)

    result = search_fit_witness(
        remnant,
        rotated_part,
        part_material=remnant.material,
        fit_config=RemnantFitConfig(),
        search_config=config,
    )

    assert result.status is FitSearchStatus.FIT
    assert result.placement is not None
    assert result.placement.rotation == 90.0

    impossible_part = _part(
        [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)],
        allowed=[0.0],
    )

    def fail_if_evaluated(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("safe dimensional prefilter should prevent exact evaluation")

    monkeypatch.setattr("yieldforge.reuse.search.validate_fit_placement", fail_if_evaluated)
    impossible = search_fit_witness(
        remnant,
        impossible_part,
        part_material=remnant.material,
        fit_config=RemnantFitConfig(),
        search_config=config,
    )

    assert impossible.status is FitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH
    assert impossible.summary.generated_candidate_count == 0
    assert impossible.summary.evaluated_candidate_count == 0


def test_material_compatibility_fails_closed_before_safe_geometry_prefilters() -> None:
    remnant = _remnant(Polygon([(0, 0), (3, 0), (3, 2), (0, 2)]))
    dimensionally_impossible = _part(
        [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)],
        allowed=[0.0],
    )

    with pytest.raises(ReuseGeometryError) as captured:
        search_fit_witness(
            remnant,
            dimensionally_impossible,
            part_material=_material(grade="different"),
            fit_config=RemnantFitConfig(),
            search_config=FitSearchConfig(
                grid_columns=3,
                grid_rows=3,
                maximum_candidates=100,
            ),
        )

    assert captured.value.code == "material_mismatch"
