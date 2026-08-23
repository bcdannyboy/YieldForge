import math

import pytest
from shapely import Polygon

from yieldforge.domain import Candidate, CandidateReportType, Part, Placement, StripPackingProblem
from yieldforge.residuals.contracts import ResidualGeometryConfig, ResidualGeometryError
from yieldforge.residuals.geometry import placed_part_polygons


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


def test_rotation_around_origin_precedes_translation() -> None:
    triangle = Part(
        id="triangle",
        shape=[(0.0, 0.0), (2.0, 0.0), (0.0, 1.0)],
        demand=1,
        allowed_orientations=[90.0],
    )
    problem = _problem(triangle)
    candidate = _candidate(
        Placement(part_id="triangle", rotation=90.0, translation=(3.0, 4.0))
    )

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
    candidate = _candidate(
        Placement(part_id="a", rotation=math.nan, translation=(0.0, 0.0))
    )

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
    candidate = _candidate(
        Placement(part_id="a", rotation=0.0, translation=(9.0, 9.0))
    )

    with pytest.raises(ResidualGeometryError) as captured:
        placed_part_polygons(problem, candidate, ResidualGeometryConfig())

    assert captured.value.code == "placed_material_out_of_sheet"
