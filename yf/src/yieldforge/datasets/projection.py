"""Fail-closed projection from source-faithful Lectra slices to solver input."""

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
from yieldforge.domain import Part, StripPackingProblem

S1_ORIENTATION_ASSUMPTION = "interpret_s1_degenerate_entries_as_allowed_rotations"
_S1_PROJECTION_FIELDS = frozenset({"parts_1", "r1_start", "r1_end", "r1_flip_x"})


class ProjectionError(ValueError):
    """A selected source task cannot be truthfully projected to the solver."""


def _require_sequence(value: object, *, column: str) -> OpaqueSequence:
    if not isinstance(value, OpaqueSequence):
        raise ProjectionError(f"s1 {column} must be a sequence")
    return value


def _rotation_number(value: object) -> tuple[int | float, float]:
    if not isinstance(value, (OpaqueInteger, OpaqueNumber)):
        raise ProjectionError("s1 rotation entries must be finite numbers")
    number = float(value.value)
    if not math.isfinite(number):
        raise ProjectionError("s1 rotation entries must be finite numbers")
    return value.value, number


def _constraint_orientations(
    values: tuple[object, ...],
    *,
    expected_part_ids: set[int],
) -> tuple[int, list[float]]:
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
    for start, end, flip in zip(starts.items, ends.items, flips.items, strict=True):
        start_value, start_number = _rotation_number(start)
        end_value, _ = _rotation_number(end)
        if start_value != end_value:
            raise ProjectionError("s1 rotations must be degenerate with start equal to end")
        if not isinstance(flip, OpaqueInteger) or flip.value != 0:
            raise ProjectionError("s1 flip flags must be strict integer zero")
        orientations.append(start_number)
    return part_id, orientations


def project_task(normalized: NormalizedSlice, tasks_index: int) -> StripPackingProblem:
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
    if disposition.assumption_codes != (S1_ORIENTATION_ASSUMPTION,):
        raise ProjectionError(f"task {tasks_index} lacks the exact s1 orientation assumption")

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
    for constraint in task_constraints:
        part_id, orientations = _constraint_orientations(
            constraint.values,
            expected_part_ids=expected_part_ids,
        )
        if part_id in orientations_by_part:
            raise ProjectionError(f"task {tasks_index} requires exactly one s1 row per part")
        orientations_by_part[part_id] = orientations
    if orientations_by_part.keys() != expected_part_ids:
        raise ProjectionError(f"task {tasks_index} requires exactly one s1 row per part")

    shapes_by_hash = {shape.shape_hash: shape for shape in normalized.shapes}
    projected_parts = []
    for source_part in task_parts:
        raw = shapes_by_hash[source_part.shape_hash].raw
        paired_points = list(zip(raw[::2], raw[1::2], strict=True))
        projected_parts.append(
            Part(
                id=f"lectra:{tasks_index}:part:{source_part.part_id}",
                shape=paired_points,
                demand=1,
                allowed_orientations=orientations_by_part[source_part.part_id],
            )
        )

    return StripPackingProblem(
        name=f"lectra-task-{tasks_index}",
        strip_height=task.sheet_width,
        sheet_length=task.sheet_length,
        parts=projected_parts,
    )


def placed_shape_svg_points(
    shape: Sequence[Point],
    *,
    rotation_degrees: float,
    translation: Point,
    sheet_width: float,
) -> tuple[tuple[float, float], ...]:
    """Transform solver points to SVG coordinates without mutating the source shape."""
    radians = math.radians(rotation_degrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    translate_x, translate_y = translation
    rendered = []
    for x, y in shape:
        rotated_x = x * cosine - y * sine
        rotated_y = x * sine + y * cosine
        translated_x = rotated_x + translate_x
        translated_y = rotated_y + translate_y
        rendered.append((translated_x, sheet_width - translated_y))
    return tuple(rendered)
