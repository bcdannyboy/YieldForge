"""Fail-closed projection from source-faithful Lectra slices to solver input."""

import hashlib
import json
import math
from collections.abc import Sequence

from yieldforge.datasets.normalized_slice import (
    CONSTRAINT_OPAQUE_FIELD_ORDER,
    NormalizationStatus,
    NormalizedSlice,
    OpaqueInteger,
    OpaqueMissing,
    OpaqueNumber,
    OpaqueSequence,
    Point,
    ProjectionStatus,
    SupportStatus,
)
from yieldforge.domain import (
    Part,
    ProjectedTask,
    ProjectionMode,
    SolverProjectionBinding,
    StripPackingProblem,
)

S1_ORIENTATION_ASSUMPTION = "interpret_s1_degenerate_entries_as_allowed_rotations"
S1_FLIP_ASSUMPTION = "interpret_s1_flip_x_as_local_x_coordinate_negation_before_rotation"
NO_FLIP_ABLATION = "force_s1_flip_x_zero_for_ablation"
TRANSFORM_CONVENTION = "local_x_coordinate_negation_before_rotation"
_S1_PROJECTION_FIELDS = frozenset({"parts_1", "r1_start", "r1_end", "r1_flip_x"})


class ProjectionError(ValueError):
    """A selected source task cannot be truthfully projected to the solver."""


def reflect_local_x(shape: Sequence[Point]) -> tuple[tuple[float, float], ...]:
    """Negate local x coordinates without mutating the source polygon."""

    reflected = []
    for x, y in shape:
        source_x = _finite_float(x, label="source point x")
        source_y = _finite_float(y, label="source point y")
        reflected_x = 0.0 if source_x == 0 else -source_x
        reflected.append((reflected_x, source_y))
    return tuple(reflected)


def _finite_float(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise ProjectionError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ProjectionError(f"{label} must be a finite number") from error
    if not math.isfinite(number):
        raise ProjectionError(f"{label} must be a finite number")
    return number


def _require_sequence(value: object, *, column: str) -> OpaqueSequence:
    if not isinstance(value, OpaqueSequence):
        raise ProjectionError(f"s1 {column} must be a sequence")
    return value


def _rotation_source_number(value: object) -> int | float:
    if not isinstance(value, (OpaqueInteger, OpaqueNumber)):
        raise ProjectionError("s1 rotation entries must be finite numbers")
    if isinstance(value, OpaqueNumber) and not math.isfinite(value.value):
        raise ProjectionError("s1 rotation entries must be finite numbers")
    return value.value


def _rotation_number(value: object) -> float:
    source_number = _rotation_source_number(value)
    try:
        number = float(source_number)
    except (OverflowError, TypeError, ValueError) as error:
        raise ProjectionError(
            "s1 rotation entries must have an exact finite solver float representation"
        ) from error
    if not math.isfinite(number):
        raise ProjectionError(
            "s1 rotation entries must have an exact finite solver float representation"
        )
    if isinstance(value, OpaqueInteger) and int(number) != source_number:
        raise ProjectionError(
            "s1 rotation entries must have an exact finite solver float representation"
        )
    return number


def _constraint_orientations(
    values: tuple[object, ...],
    *,
    expected_part_ids: set[int],
) -> tuple[int, list[float], int]:
    by_column = dict(zip(CONSTRAINT_OPAQUE_FIELD_ORDER, values, strict=True))
    part_references = _require_sequence(by_column["parts_1"], column="parts_1")
    if len(part_references.items) != 1 or not isinstance(part_references.items[0], OpaqueInteger):
        raise ProjectionError("s1 parts_1 must contain exactly one integer part_id")
    part_id = part_references.items[0].value
    if part_id not in expected_part_ids:
        raise ProjectionError(f"s1 parts_1 names unknown part_id {part_id}")

    if not isinstance(by_column["parts_2"], OpaqueMissing):
        raise ProjectionError("s1 parts_2 must be missing")
    for column, value in by_column.items():
        if column in _S1_PROJECTION_FIELDS or column == "parts_2":
            continue
        if not isinstance(value, OpaqueMissing):
            raise ProjectionError(f"s1 unrelated parameter {column} must be missing")

    starts = _require_sequence(by_column["r1_start"], column="r1_start")
    ends = _require_sequence(by_column["r1_end"], column="r1_end")
    flips = _require_sequence(by_column["r1_flip_x"], column="r1_flip_x")
    if not starts.items or not ends.items or not flips.items:
        raise ProjectionError("s1 orientation sequences must be nonempty")
    if not (len(starts.items) == len(ends.items) == len(flips.items)):
        raise ProjectionError("s1 orientation sequences must have equal lengths")

    orientations = []
    flip_values: list[int] = []
    for start, end, flip in zip(starts.items, ends.items, flips.items, strict=True):
        start_value = _rotation_source_number(start)
        end_value = _rotation_source_number(end)
        if start_value != end_value:
            raise ProjectionError("s1 rotations must be degenerate with start equal to end")
        start_number = _rotation_number(start)
        if not isinstance(flip, OpaqueInteger) or flip.value not in {0, 1}:
            raise ProjectionError("s1 flip flags must be strict integer zero or one")
        orientations.append(start_number)
        flip_values.append(flip.value)
    if len(orientations) != len(set(orientations)):
        raise ProjectionError("s1 contains duplicate allowed rotations")
    if len(set(flip_values)) != 1:
        raise ProjectionError("s1 flip states must be uniform within one part")
    return part_id, orientations, flip_values[0]


def _projection_hash(
    problem: StripPackingProblem,
    *,
    mode: ProjectionMode,
    assumption_codes: tuple[str, ...],
    intervention_codes: tuple[str, ...],
    source_flip_part_count: int,
) -> str:
    payload = json.dumps(
        {
            "assumption_codes": assumption_codes,
            "intervention_codes": intervention_codes,
            "mode": mode.value,
            "problem": problem.model_dump(mode="json"),
            "source_flip_part_count": source_flip_part_count,
            "transform_convention": TRANSFORM_CONVENTION,
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def project_task(
    normalized: NormalizedSlice,
    tasks_index: int,
    *,
    mode: ProjectionMode = ProjectionMode.SOURCE_AS_RECORDED,
) -> ProjectedTask:
    """Project one explicitly eligible normalized task to a solver problem."""
    task = next((item for item in normalized.tasks if item.tasks_index == tasks_index), None)
    if task is None:
        raise ProjectionError(f"task {tasks_index} is not present in the normalized slice")
    if task.sheet_length <= 0:
        raise ProjectionError(f"task {tasks_index} requires a positive physical sheet_length")
    if task.sheet_width <= 0:
        raise ProjectionError(f"task {tasks_index} requires a positive physical sheet_width")

    disposition = next(
        item for item in normalized.task_dispositions if item.tasks_index == tasks_index
    )
    if not (
        disposition.normalization_status is NormalizationStatus.SOURCE_LOSSLESS
        and disposition.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
        and disposition.projection_status in {ProjectionStatus.ELIGIBLE, ProjectionStatus.PROJECTED}
    ):
        raise ProjectionError(f"task {tasks_index} is not explicitly eligible for projection")
    try:
        parsed_mode = ProjectionMode(mode)
    except (TypeError, ValueError) as error:
        raise ProjectionError("projection mode is not supported") from error

    task_parts = [part for part in normalized.parts if part.tasks_index == tasks_index]
    task_constraints = [
        constraint for constraint in normalized.constraints if constraint.tasks_index == tasks_index
    ]
    if any(constraint.type != "s1" for constraint in task_constraints):
        raise ProjectionError(f"task {tasks_index} permits only s1 constraints")
    if len(task_constraints) != len(task_parts):
        raise ProjectionError(f"task {tasks_index} requires exactly one s1 row per part")

    expected_part_ids = {part.part_id for part in task_parts}
    orientations_by_part: dict[int, list[float]] = {}
    flips_by_part: dict[int, int] = {}
    for constraint in task_constraints:
        part_id, orientations, flip_x = _constraint_orientations(
            constraint.values,
            expected_part_ids=expected_part_ids,
        )
        if part_id in orientations_by_part:
            raise ProjectionError(f"task {tasks_index} requires exactly one s1 row per part")
        orientations_by_part[part_id] = orientations
        flips_by_part[part_id] = flip_x
    if orientations_by_part.keys() != expected_part_ids:
        raise ProjectionError(f"task {tasks_index} requires exactly one s1 row per part")

    source_flip_part_count = sum(flips_by_part.values())
    assumption_codes = tuple(
        sorted(
            (S1_ORIENTATION_ASSUMPTION, S1_FLIP_ASSUMPTION)
            if source_flip_part_count
            else (S1_ORIENTATION_ASSUMPTION,)
        )
    )
    if disposition.assumption_codes != assumption_codes:
        raise ProjectionError(f"task {tasks_index} lacks the exact s1 orientation assumption")
    if parsed_mode is ProjectionMode.FORCE_FLIP_X_ZERO and source_flip_part_count == 0:
        raise ProjectionError("no-flip ablation requires at least one source flip")

    shapes_by_hash = {shape.shape_hash: shape for shape in normalized.shapes}
    projected_parts = []
    for source_part in task_parts:
        raw = shapes_by_hash[source_part.shape_hash].raw
        paired_points = list(zip(raw[::2], raw[1::2], strict=True))
        if (
            parsed_mode is ProjectionMode.SOURCE_AS_RECORDED
            and flips_by_part[source_part.part_id] == 1
        ):
            solver_shape: Sequence[Point] = reflect_local_x(paired_points)
        else:
            solver_shape = paired_points
        projected_parts.append(
            Part(
                id=f"lectra:{tasks_index}:part:{source_part.part_id}",
                shape=solver_shape,
                demand=1,
                allowed_orientations=orientations_by_part[source_part.part_id],
            )
        )

    suffix = "" if parsed_mode is ProjectionMode.SOURCE_AS_RECORDED else "-force-flip-x-zero"
    problem = StripPackingProblem(
        name=f"lectra-task-{tasks_index}{suffix}",
        strip_height=task.sheet_width,
        sheet_length=task.sheet_length,
        parts=projected_parts,
    )
    intervention_codes = (
        (NO_FLIP_ABLATION,) if parsed_mode is ProjectionMode.FORCE_FLIP_X_ZERO else ()
    )
    binding = SolverProjectionBinding(
        mode=parsed_mode,
        transform_convention=TRANSFORM_CONVENTION,
        projection_sha256=_projection_hash(
            problem,
            mode=parsed_mode,
            assumption_codes=assumption_codes,
            intervention_codes=intervention_codes,
            source_flip_part_count=source_flip_part_count,
        ),
        assumption_codes=assumption_codes,
        intervention_codes=intervention_codes,
        source_flip_part_count=source_flip_part_count,
    )
    return ProjectedTask(problem=problem, projection=binding)


def placed_shape_svg_points(
    shape: Sequence[Point],
    *,
    rotation_degrees: float,
    translation: Point,
    sheet_width: float,
) -> tuple[tuple[float, float], ...]:
    """Transform solver points to SVG coordinates without mutating the source shape."""
    rotation = _finite_float(rotation_degrees, label="rotation_degrees")
    translate_x = _finite_float(translation[0], label="translation x")
    translate_y = _finite_float(translation[1], label="translation y")
    render_height = _finite_float(sheet_width, label="sheet_width")
    radians = math.radians(rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    rendered = []
    for x, y in shape:
        source_x = _finite_float(x, label="source point x")
        source_y = _finite_float(y, label="source point y")
        rotated_x = source_x * cosine - source_y * sine
        rotated_y = source_x * sine + source_y * cosine
        translated_x = rotated_x + translate_x
        translated_y = rotated_y + translate_y
        render_y = render_height - translated_y
        if not (math.isfinite(translated_x) and math.isfinite(render_y)):
            raise ProjectionError("placed SVG geometry must contain only finite output points")
        rendered.append((translated_x, render_y))
    return tuple(rendered)
