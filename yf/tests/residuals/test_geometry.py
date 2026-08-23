import math
from pathlib import Path

import pytest
from shapely import Polygon

from yieldforge.domain import Candidate, CandidateReportType, Part, Placement, StripPackingProblem
from yieldforge.experiments.contracts import M0ExperimentContract
from yieldforge.residuals.contracts import (
    ResidualGeometryConfig,
    ResidualGeometryError,
    ResidualRuleName,
    rule_set_from_m0,
)
from yieldforge.residuals.geometry import (
    _require_reconciliation,
    compare_candidate_residuals,
    extract_candidate_residual,
    geometry_sha256,
    placed_part_polygons,
)

YF_ROOT = Path(__file__).parents[2]
M0_CONTRACT_PATH = YF_ROOT / "experiments" / "m0-contract-v1.json"


def _problem(*parts: Part, length: float = 10.0, height: float = 10.0) -> StripPackingProblem:
    return StripPackingProblem(
        name="residual-fixture",
        strip_height=height,
        sheet_length=length,
        parts=list(parts),
    )


def _candidate(*placements: Placement, candidate_id: str = "candidate-a") -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        report_type=CandidateReportType.FINAL,
        seed=0,
        width=10.0,
        density=0.5,
        placements=list(placements),
    )


def _square(part_id: str, *, demand: int = 1) -> Part:
    return Part(
        id=part_id,
        shape=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
        demand=demand,
        allowed_orientations=[0.0, 90.0],
    )


def _rectangle(part_id: str, width: float, height: float) -> Part:
    return Part(
        id=part_id,
        shape=[(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)],
        demand=1,
        allowed_orientations=[0.0],
    )


def _rules():  # type: ignore[no-untyped-def]
    contract = M0ExperimentContract.model_validate_json(M0_CONTRACT_PATH.read_text(), strict=True)
    return rule_set_from_m0(contract.remnant_eligibility)


def test_rotation_around_origin_precedes_translation() -> None:
    triangle = Part(
        id="triangle",
        shape=[(0.0, 0.0), (2.0, 0.0), (0.0, 1.0)],
        demand=1,
        allowed_orientations=[90.0],
    )
    problem = _problem(triangle)
    candidate = _candidate(Placement(part_id="triangle", rotation=90.0, translation=(3.0, 4.0)))

    placed = placed_part_polygons(problem, candidate, ResidualGeometryConfig())

    expected = Polygon([(3.0, 4.0), (3.0, 6.0), (2.0, 4.0)])
    assert placed["triangle"].equals_exact(expected, tolerance=1e-12)


@pytest.mark.parametrize(
    ("problem", "candidate", "error_code"),
    [
        (
            _problem(_square("a"), _square("b")),
            _candidate(Placement(part_id="a", rotation=0.0, translation=(0.0, 0.0))),
            "placement_id_mismatch",
        ),
        (
            _problem(_square("a")),
            _candidate(
                Placement(part_id="a", rotation=0.0, translation=(0.0, 0.0)),
                Placement(part_id="a", rotation=0.0, translation=(3.0, 0.0)),
            ),
            "duplicate_placement_id",
        ),
        (
            _problem(_square("a")),
            _candidate(Placement(part_id="unknown", rotation=0.0, translation=(0.0, 0.0))),
            "placement_id_mismatch",
        ),
        (
            _problem(_square("a", demand=2)),
            _candidate(Placement(part_id="a", rotation=0.0, translation=(0.0, 0.0))),
            "unsupported_part_demand",
        ),
    ],
)
def test_placement_identifiers_fail_closed(
    problem: StripPackingProblem,
    candidate: Candidate,
    error_code: str,
) -> None:
    with pytest.raises(ResidualGeometryError) as captured:
        placed_part_polygons(problem, candidate, ResidualGeometryConfig())

    assert captured.value.code == error_code


def test_nonfinite_transform_fails_closed() -> None:
    problem = _problem(_square("a"))
    candidate = _candidate(Placement(part_id="a", rotation=math.nan, translation=(0.0, 0.0)))

    with pytest.raises(ResidualGeometryError) as captured:
        placed_part_polygons(problem, candidate, ResidualGeometryConfig())

    assert captured.value.code == "nonfinite_geometry"


def test_invalid_source_polygon_fails_without_repair() -> None:
    bowtie = Part(
        id="bowtie",
        shape=[(0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0)],
        demand=1,
    )

    with pytest.raises(ResidualGeometryError) as captured:
        placed_part_polygons(
            _problem(bowtie),
            _candidate(Placement(part_id="bowtie", rotation=0.0, translation=(0.0, 0.0))),
            ResidualGeometryConfig(),
        )

    assert captured.value.code == "invalid_source_polygon"


def test_material_overlap_fails_closed() -> None:
    problem = _problem(_square("a"), _square("b"))
    candidate = _candidate(
        Placement(part_id="a", rotation=0.0, translation=(1.0, 1.0)),
        Placement(part_id="b", rotation=0.0, translation=(2.0, 2.0)),
    )

    with pytest.raises(ResidualGeometryError) as captured:
        placed_part_polygons(problem, candidate, ResidualGeometryConfig())

    assert captured.value.code == "placed_material_overlap"


def test_out_of_sheet_material_fails_closed() -> None:
    problem = _problem(_square("a"))
    candidate = _candidate(Placement(part_id="a", rotation=0.0, translation=(9.0, 9.0)))

    with pytest.raises(ResidualGeometryError) as captured:
        placed_part_polygons(problem, candidate, ResidualGeometryConfig())

    assert captured.value.code == "placed_material_out_of_sheet"


def test_simple_fixed_sheet_residual_reconciles_exact_material() -> None:
    result = extract_candidate_residual(
        _problem(_square("a")),
        _candidate(Placement(part_id="a", rotation=0.0, translation=(0.0, 0.0))),
        _rules(),
        ResidualGeometryConfig(),
    )

    accounting = result.observation.accounting
    assert accounting is not None
    assert accounting.stock_area == pytest.approx(100.0)
    assert accounting.placed_area == pytest.approx(4.0)
    assert accounting.process_loss_area == pytest.approx(0.0)
    assert accounting.forbidden_loss_area == pytest.approx(0.0)
    assert accounting.residual_area == pytest.approx(96.0)
    assert accounting.reconciliation_delta <= accounting.area_tolerance


def test_nonzero_buffer_is_accounted_as_process_loss_and_preserves_hole() -> None:
    result = extract_candidate_residual(
        _problem(_square("a")),
        _candidate(Placement(part_id="a", rotation=0.0, translation=(4.0, 4.0))),
        _rules(),
        ResidualGeometryConfig(part_buffer_distance=1.0),
    )

    accounting = result.observation.accounting
    assert accounting is not None
    assert accounting.placed_area == pytest.approx(4.0)
    assert accounting.process_loss_area == pytest.approx(12.0)
    assert accounting.residual_area == pytest.approx(84.0)
    assert len(result.observation.components) == 1
    assert result.observation.components[0].hole_count == 1


def test_overlapping_buffers_are_unioned_before_process_accounting() -> None:
    result = extract_candidate_residual(
        _problem(_square("a"), _square("b")),
        _candidate(
            Placement(part_id="a", rotation=0.0, translation=(2.0, 4.0)),
            Placement(part_id="b", rotation=0.0, translation=(5.0, 4.0)),
        ),
        _rules(),
        ResidualGeometryConfig(part_buffer_distance=1.0),
    )

    accounting = result.observation.accounting
    assert accounting is not None
    assert accounting.placed_area == pytest.approx(8.0)
    assert accounting.process_loss_area == pytest.approx(20.0)
    assert accounting.residual_area == pytest.approx(72.0)


def test_forbidden_loss_excludes_already_placed_and_process_material() -> None:
    forbidden = ((2.0, 3.0), (5.0, 3.0), (5.0, 6.0), (2.0, 6.0), (2.0, 3.0))
    result = extract_candidate_residual(
        _problem(_square("a")),
        _candidate(Placement(part_id="a", rotation=0.0, translation=(4.0, 4.0))),
        _rules(),
        ResidualGeometryConfig(
            part_buffer_distance=1.0,
            forbidden_polygons=(forbidden,),
        ),
    )

    accounting = result.observation.accounting
    assert accounting is not None
    assert accounting.process_loss_area == pytest.approx(12.0)
    assert accounting.forbidden_loss_area == pytest.approx(3.0)
    assert accounting.residual_area == pytest.approx(81.0)


def test_reconciliation_fails_closed_when_categories_do_not_conserve_stock() -> None:
    with pytest.raises(ResidualGeometryError) as captured:
        _require_reconciliation(
            stock_area=100.0,
            category_areas=(20.0, 2.0, 3.0, 74.0),
            area_tolerance=1e-7,
        )

    assert captured.value.code == "material_reconciliation_failed"


def test_enclosed_component_is_scrap_while_exterior_component_is_retained() -> None:
    problem = _problem(
        _rectangle("top", 6.0, 2.0),
        _rectangle("bottom", 6.0, 2.0),
        _rectangle("left", 2.0, 2.0),
        _rectangle("right", 2.0, 2.0),
    )
    candidate = _candidate(
        Placement(part_id="top", rotation=0.0, translation=(2.0, 6.0)),
        Placement(part_id="bottom", rotation=0.0, translation=(2.0, 2.0)),
        Placement(part_id="left", rotation=0.0, translation=(2.0, 4.0)),
        Placement(part_id="right", rotation=0.0, translation=(6.0, 4.0)),
    )

    result = extract_candidate_residual(problem, candidate, _rules(), ResidualGeometryConfig())

    assert len(result.observation.components) == 2
    interior = next(
        component for component in result.observation.components if not component.exterior_connected
    )
    primary = next(
        item
        for item in result.observation.classifications
        if item.rule_name is ResidualRuleName.PRIMARY
    )
    assert interior.component_sha256 in primary.scrap_component_sha256
    assert interior.component_sha256 not in primary.retained_component_sha256
    assert primary.retained_area == pytest.approx(64.0)
    assert primary.scrap_area == pytest.approx(4.0)


def test_effective_width_separates_permissive_from_stricter_rules() -> None:
    problem = _problem(_rectangle("block", 9.9, 10.0))
    candidate = _candidate(Placement(part_id="block", rotation=0.0, translation=(0.1, 0.0)))

    result = extract_candidate_residual(problem, candidate, _rules(), ResidualGeometryConfig())

    component = result.observation.components[0]
    assert component.effective_width_rule_names == (ResidualRuleName.PERMISSIVE,)
    classifications = {item.rule_name: item for item in result.observation.classifications}
    assert classifications[ResidualRuleName.PERMISSIVE].retained_area == pytest.approx(1.0)
    assert classifications[ResidualRuleName.PRIMARY].scrap_area == pytest.approx(1.0)
    assert classifications[ResidualRuleName.CONSERVATIVE].scrap_area == pytest.approx(1.0)


def test_geometry_hash_is_invariant_to_ring_start_and_orientation() -> None:
    first = Polygon([(0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0)])
    second = Polygon([(3.0, 2.0), (3.0, 0.0), (0.0, 0.0), (0.0, 2.0)])

    assert geometry_sha256(first) == geometry_sha256(second)


def test_pair_comparison_reports_exact_geometry_not_unchanged_classification() -> None:
    problem = _problem(_square("a"))
    first = extract_candidate_residual(
        problem,
        _candidate(
            Placement(part_id="a", rotation=0.0, translation=(2.0, 2.0)),
            candidate_id="candidate-a",
        ),
        _rules(),
        ResidualGeometryConfig(),
    )
    second = extract_candidate_residual(
        problem,
        _candidate(
            Placement(part_id="a", rotation=0.0, translation=(3.0, 2.0)),
            candidate_id="candidate-b",
        ),
        _rules(),
        ResidualGeometryConfig(),
    )

    comparison = compare_candidate_residuals(first, second)

    assert comparison.exact_residual_equal is False
    assert comparison.symmetric_difference_area == pytest.approx(4.0)
    assert comparison.symmetric_difference_sheet_fraction == pytest.approx(0.04)
    assert comparison.classification_difference_rule_names == ()
