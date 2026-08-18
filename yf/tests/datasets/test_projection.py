import copy
import math

import pytest
from test_normalized_slice import valid_slice

from yieldforge.datasets.normalized_slice import (
    NormalizationStatus,
    NormalizedSlice,
    OpaqueBoolean,
    OpaqueInteger,
    OpaqueMissing,
    OpaqueNumber,
    OpaqueSequence,
    ProjectionStatus,
    SupportStatus,
)
from yieldforge.datasets.projection import (
    ProjectionError,
    placed_shape_svg_points,
    project_task,
)

S1_ASSUMPTION = "interpret_s1_degenerate_entries_as_allowed_rotations"


def _sequence(*items: OpaqueInteger | OpaqueNumber) -> dict[str, object]:
    return OpaqueSequence(kind="sequence", items=items).model_dump()


def _projectable_slice() -> NormalizedSlice:
    data = valid_slice().model_dump()
    values = list(data["constraints"][0]["values"])
    values[0] = _sequence(OpaqueInteger(kind="integer", value=7))
    values[3] = _sequence(
        OpaqueNumber(kind="number", value=0.0),
        OpaqueNumber(kind="number", value=90.0),
    )
    values[4] = copy.deepcopy(values[3])
    values[5] = _sequence(
        OpaqueInteger(kind="integer", value=0),
        OpaqueInteger(kind="integer", value=0),
    )
    data["constraints"][0]["values"] = tuple(values)
    data["task_dispositions"][0]["assumption_codes"] = (S1_ASSUMPTION,)
    return NormalizedSlice.model_validate(data)


def _with_constraint_value(index: int, value: dict[str, object]) -> NormalizedSlice:
    data = _projectable_slice().model_dump()
    values = list(data["constraints"][0]["values"])
    values[index] = value
    data["constraints"][0]["values"] = tuple(values)
    return NormalizedSlice.model_validate(data)


def test_project_task_preserves_source_geometry_and_closes_only_solver_ring() -> None:
    data = _projectable_slice().model_dump()
    data["shapes"][0].update(raw=(2, 1.0, 4, 1.0, 2, 4.0), sizes=(6,))
    data["derived_geometry"][0].update(
        paired_points=((2, 1.0), (4, 1.0), (2, 4.0)),
        closed_ring=((2, 1.0), (4, 1.0), (2, 4.0), (2, 1.0)),
        area=3.0,
        bounds=(2, 1.0, 4, 4.0),
    )
    normalized = NormalizedSlice.model_validate(data)
    before = normalized.model_dump()

    problem = project_task(normalized, 17)

    assert problem.name == "lectra-task-17"
    assert problem.strip_height == 14500.0
    assert problem.sheet_length == 20000.0
    assert problem.parts[0].id == "lectra:17:part:7"
    assert problem.parts[0].shape == [
        (2.0, 1.0),
        (4.0, 1.0),
        (2.0, 4.0),
        (2.0, 1.0),
    ]
    assert problem.parts[0].demand == 1
    assert problem.parts[0].allowed_orientations == [0.0, 90.0]
    assert normalized.model_dump() == before


def test_project_task_keeps_repeated_shapes_as_distinct_source_parts() -> None:
    data = _projectable_slice().model_dump()
    second_part = copy.deepcopy(data["parts"][0])
    second_part.update(source_row_index=21, part_id=8)
    data["parts"] = (*data["parts"], second_part)
    second_constraint = copy.deepcopy(data["constraints"][0])
    second_constraint["source_row_index"] = 41
    second_values = list(second_constraint["values"])
    second_values[0] = _sequence(OpaqueInteger(kind="integer", value=8))
    second_constraint["values"] = tuple(second_values)
    data["constraints"] = (*data["constraints"], second_constraint)
    normalized = NormalizedSlice.model_validate(data)

    problem = project_task(normalized, 17)

    assert [part.id for part in problem.parts] == [
        "lectra:17:part:7",
        "lectra:17:part:8",
    ]
    assert [part.demand for part in problem.parts] == [1, 1]
    assert problem.parts[0].shape == problem.parts[1].shape


def test_project_task_requires_an_existing_task() -> None:
    with pytest.raises(ProjectionError, match="task 999.*not present"):
        project_task(_projectable_slice(), 999)


def test_project_task_rejects_nonpositive_sheet_length_sentinel() -> None:
    data = _projectable_slice().model_dump()
    data["tasks"][0]["sheet_length"] = -1.0
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="positive physical sheet_length"):
        project_task(normalized, 17)


@pytest.mark.parametrize(
    ("normalization", "support", "projection"),
    [
        (
            NormalizationStatus.REJECTED,
            SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS,
            ProjectionStatus.BLOCKED,
        ),
        (
            NormalizationStatus.SOURCE_LOSSLESS,
            SupportStatus.VIEW_ONLY,
            ProjectionStatus.BLOCKED,
        ),
        (
            NormalizationStatus.SOURCE_LOSSLESS,
            SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS,
            ProjectionStatus.BLOCKED,
        ),
    ],
)
def test_project_task_requires_the_exact_runnable_disposition(
    normalization: NormalizationStatus,
    support: SupportStatus,
    projection: ProjectionStatus,
) -> None:
    data = _projectable_slice().model_dump()
    data["task_dispositions"][0].update(
        normalization_status=normalization,
        support_status=support,
        projection_status=projection,
        reason_codes=("projection_blocked",),
    )
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="not explicitly eligible"):
        project_task(normalized, 17)


def test_project_task_requires_only_the_explicit_s1_assumption() -> None:
    data = _projectable_slice().model_dump()
    data["task_dispositions"][0]["assumption_codes"] = ("assume_free_rotation",)
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="exact s1 orientation assumption"):
        project_task(normalized, 17)


def test_project_task_accepts_an_already_projected_disposition() -> None:
    data = _projectable_slice().model_dump()
    data["task_dispositions"][0]["projection_status"] = ProjectionStatus.PROJECTED
    normalized = NormalizedSlice.model_validate(data)

    assert project_task(normalized, 17).parts[0].id == "lectra:17:part:7"


def test_project_task_rejects_non_s1_constraints() -> None:
    data = _projectable_slice().model_dump()
    data["constraints"][0]["type"] = "x1"
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="only s1 constraints"):
        project_task(normalized, 17)


def test_project_task_requires_one_s1_row_per_part() -> None:
    data = _projectable_slice().model_dump()
    duplicate = copy.deepcopy(data["constraints"][0])
    duplicate["source_row_index"] = 41
    data["constraints"] = (*data["constraints"], duplicate)
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="exactly one s1 row per part"):
        project_task(normalized, 17)


def test_project_task_rejects_multi_part_s1_references() -> None:
    normalized = _with_constraint_value(
        0,
        _sequence(
            OpaqueInteger(kind="integer", value=7),
            OpaqueInteger(kind="integer", value=7),
        ),
    )

    with pytest.raises(ProjectionError, match="parts_1.*exactly one"):
        project_task(normalized, 17)


@pytest.mark.parametrize(
    ("column_index", "value", "message"),
    [
        (
            6,
            _sequence(OpaqueInteger(kind="integer", value=7)),
            "parts_2 must be missing",
        ),
        (1, OpaqueInteger(kind="integer", value=0).model_dump(), "p1_x must be missing"),
    ],
)
def test_project_task_rejects_secondary_or_unrelated_s1_parameters(
    column_index: int,
    value: dict[str, object],
    message: str,
) -> None:
    normalized = _with_constraint_value(column_index, value)

    with pytest.raises(ProjectionError, match=message):
        project_task(normalized, 17)


@pytest.mark.parametrize(
    ("column_index", "value", "message"),
    [
        (3, OpaqueMissing(kind="missing").model_dump(), "r1_start.*sequence"),
        (3, _sequence(), "orientation sequences must be nonempty"),
        (
            4,
            _sequence(OpaqueNumber(kind="number", value=0.0)),
            "orientation sequences must have equal lengths",
        ),
        (
            3,
            OpaqueSequence(
                kind="sequence",
                items=(
                    OpaqueBoolean(kind="boolean", value=False),
                    OpaqueBoolean(kind="boolean", value=False),
                ),
            ).model_dump(),
            "rotation entries must be finite numbers",
        ),
    ],
)
def test_project_task_rejects_malformed_orientation_sequences(
    column_index: int,
    value: dict[str, object],
    message: str,
) -> None:
    normalized = _with_constraint_value(column_index, value)

    with pytest.raises(ProjectionError, match=message):
        project_task(normalized, 17)


def test_project_task_rejects_interval_or_mirror_orientation_data() -> None:
    interval = _with_constraint_value(
        4,
        _sequence(
            OpaqueNumber(kind="number", value=0.0),
            OpaqueNumber(kind="number", value=180.0),
        ),
    )
    with pytest.raises(ProjectionError, match="degenerate.*start.*end"):
        project_task(interval, 17)

    mirrored = _with_constraint_value(
        5,
        _sequence(
            OpaqueInteger(kind="integer", value=0),
            OpaqueInteger(kind="integer", value=1),
        ),
    )
    with pytest.raises(ProjectionError, match="flip flags.*integer zero"):
        project_task(mirrored, 17)


def test_project_task_compares_large_integer_interval_endpoints_before_float_conversion() -> None:
    data = _projectable_slice().model_dump()
    values = list(data["constraints"][0]["values"])
    values[3] = _sequence(OpaqueInteger(kind="integer", value=9_007_199_254_740_992))
    values[4] = _sequence(OpaqueInteger(kind="integer", value=9_007_199_254_740_993))
    values[5] = _sequence(OpaqueInteger(kind="integer", value=0))
    data["constraints"][0]["values"] = tuple(values)
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="degenerate.*start.*end"):
        project_task(normalized, 17)


@pytest.mark.parametrize(
    "rotation",
    [9_007_199_254_740_993, 10**400],
)
def test_project_task_rejects_rotations_without_an_exact_finite_solver_float(
    rotation: int,
) -> None:
    data = _projectable_slice().model_dump()
    values = list(data["constraints"][0]["values"])
    values[3] = _sequence(OpaqueInteger(kind="integer", value=rotation))
    values[4] = _sequence(OpaqueInteger(kind="integer", value=rotation))
    values[5] = _sequence(OpaqueInteger(kind="integer", value=0))
    data["constraints"][0]["values"] = tuple(values)
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="exact finite solver float"):
        project_task(normalized, 17)


def test_project_task_rejects_duplicate_allowed_rotations() -> None:
    data = _projectable_slice().model_dump()
    values = list(data["constraints"][0]["values"])
    repeated = _sequence(
        OpaqueNumber(kind="number", value=90.0),
        OpaqueNumber(kind="number", value=90.0),
    )
    values[3] = repeated
    values[4] = copy.deepcopy(repeated)
    data["constraints"][0]["values"] = tuple(values)
    normalized = NormalizedSlice.model_validate(data)

    with pytest.raises(ProjectionError, match="duplicate allowed rotations"):
        project_task(normalized, 17)


@pytest.mark.parametrize(
    ("shape", "rotation", "translation", "sheet_width"),
    [
        (((0.0, 0.0),), math.nan, (0.0, 0.0), 10.0),
        (((0.0, 0.0),), math.inf, (0.0, 0.0), 10.0),
        (((0.0, 0.0),), 0.0, (math.nan, 0.0), 10.0),
        (((0.0, 0.0),), 0.0, (0.0, -math.inf), 10.0),
        (((0.0, 0.0),), 0.0, (0.0, 0.0), math.nan),
        (((0.0, 0.0),), 0.0, (0.0, 0.0), math.inf),
        (((math.nan, 0.0),), 0.0, (0.0, 0.0), 10.0),
        (((0.0, math.inf),), 0.0, (0.0, 0.0), 10.0),
    ],
)
def test_placed_shape_svg_points_rejects_nonfinite_inputs(
    shape: tuple[tuple[float, float], ...],
    rotation: float,
    translation: tuple[float, float],
    sheet_width: float,
) -> None:
    with pytest.raises(ProjectionError, match="finite"):
        placed_shape_svg_points(
            shape,
            rotation_degrees=rotation,
            translation=translation,
            sheet_width=sheet_width,
        )


def test_placed_shape_svg_points_rejects_nonfinite_outputs() -> None:
    with pytest.raises(ProjectionError, match="finite"):
        placed_shape_svg_points(
            ((1e308, 0.0),),
            rotation_degrees=0.0,
            translation=(1e308, 0.0),
            sheet_width=10.0,
        )


def test_placed_shape_svg_points_rotates_then_translates_then_flips_y() -> None:
    shape = ((1, 0.0), (0, 1.0), (0, 0.0), (1, 0.0))
    before = copy.deepcopy(shape)

    rendered = placed_shape_svg_points(
        shape,
        rotation_degrees=90.0,
        translation=(10, 20.0),
        sheet_width=100.0,
    )

    assert rendered[0] == pytest.approx((10.0, 79.0), abs=1e-12)
    assert rendered[1] == pytest.approx((9.0, 80.0), abs=1e-12)
    assert rendered[2] == pytest.approx((10.0, 80.0), abs=1e-12)
    assert rendered[3] == pytest.approx((10.0, 79.0), abs=1e-12)
    assert shape == before
