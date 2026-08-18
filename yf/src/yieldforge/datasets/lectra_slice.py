"""Deterministic, fail-closed selection of a bounded Lectra source slice."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Any

from shapely.geometry import Polygon

from yieldforge.datasets.normalized_slice import (
    ASSUMED_PROVENANCE_PATH,
    CONSTRAINT_OPAQUE_FIELD_ORDER,
    DERIVED_PROVENANCE_PATHS,
    SOURCE_REAL_PROVENANCE_PATHS,
    ConstraintSourceRow,
    DerivedShapeGeometry,
    NormalizationStatus,
    NormalizedSlice,
    NormalizedSliceSource,
    OpaqueBoolean,
    OpaqueInteger,
    OpaqueMissing,
    OpaqueNumber,
    OpaqueSequence,
    OpaqueString,
    OpaqueValue,
    PartSourceRow,
    ProjectionStatus,
    ProvenanceGroup,
    ProvenanceKind,
    ShapeSourceRow,
    SourceChecksum,
    SourceUnit,
    SupportStatus,
    TaskDisposition,
    TaskSourceRow,
)
from yieldforge.datasets.source_manifest import DatasetSourceManifest

MAX_RANKED_CANDIDATES = 256
MIN_PARTS = 20
MAX_PARTS = 50
TARGET_PARTS = 35
TARGET_UNIQUE_SHAPES = 9
TARGET_REPEATED_PART_ROWS = 23
CONVERSION_RULESET_VERSION = "lectra-slice-rules.v1"
S1_ASSUMPTION = "interpret_s1_degenerate_entries_as_allowed_rotations"
_S1_FIELDS = frozenset({"parts_1", "r1_start", "r1_end", "r1_flip_x"})


class NoEligibleLectraSliceError(ValueError):
    """No task satisfied the fixed, bounded representative-slice rules."""


@dataclass(frozen=True)
class RepresentativeTaskSelection:
    """The two deterministic task roles selected from the bounded ranking."""

    runnable_tasks_index: int
    view_only_tasks_index: int
    runnable_rank_score: int
    view_only_rank_score: int


@dataclass(frozen=True)
class _RankedCandidate:
    tasks_index: int
    rank_score: int


@dataclass(frozen=True)
class _GeometryFacts:
    raw: tuple[int | float, ...]
    sizes: tuple[int, ...]
    paired_points: tuple[tuple[int | float, int | float], ...]
    closed_ring: tuple[tuple[int | float, int | float], ...]
    ring_closure_added: bool
    is_simple: bool
    is_valid: bool
    area: float
    bounds: tuple[int | float, int | float, int | float, int | float]


def _python_scalar(value: Any) -> Any:
    item = getattr(value, "item", None)
    if callable(item):
        try:
            converted = item()
        except (TypeError, ValueError):
            return value
        if not isinstance(converted, (list, tuple, dict)):
            return converted
    return value


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    value = _python_scalar(value)
    if type(value).__name__ in {"NAType", "NaTType"}:
        return True
    return isinstance(value, float) and math.isnan(value)


def _source_int(value: Any, *, label: str, nonnegative: bool = False) -> int:
    value = _python_scalar(value)
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{label} must be an integer")
    result = int(value)
    if nonnegative and result < 0:
        raise ValueError(f"{label} must be nonnegative")
    return result


def _source_float(value: Any, *, label: str, positive: bool = False) -> float:
    value = _python_scalar(value)
    if isinstance(value, bool) or isinstance(value, Integral) or not isinstance(value, Real):
        raise ValueError(f"{label} must retain its source floating-point dtype")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _source_bool(value: Any, *, label: str) -> bool:
    value = _python_scalar(value)
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def _source_number(value: Any, *, label: str) -> int | float:
    value = _python_scalar(value)
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be numeric")
    if isinstance(value, Integral):
        return int(value)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _sequence(value: Any, *, label: str, nonempty: bool = True) -> tuple[Any, ...]:
    if _is_missing(value):
        raise ValueError(f"{label} must be a nonempty sequence")
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ValueError(f"{label} must be a nonempty sequence")
    if isinstance(value, Sequence):
        result = tuple(value)
    else:
        tolist = getattr(value, "tolist", None)
        if not callable(tolist):
            raise ValueError(f"{label} must be a nonempty sequence")
        converted = tolist()
        if not isinstance(converted, list):
            raise ValueError(f"{label} must be a nonempty sequence")
        result = tuple(converted)
    if nonempty and not result:
        raise ValueError(f"{label} must be a nonempty sequence")
    return result


def _constraint_type(value: Any) -> str:
    value = _python_scalar(value)
    if not isinstance(value, str) or not value:
        raise ValueError("constraint type must be a real nonempty source string")
    return value


def _source_row_index(frame: Any, position: int, *, table: str) -> int:
    return _source_int(frame.index[position], label=f"{table} source row index", nonnegative=True)


def _cell(frame: Any, position: int, column: str) -> Any:
    return frame[column].iloc[position]


def _task_rows_by_id(tasks: Any) -> dict[int, int]:
    result: dict[int, int] = {}
    duplicates: set[int] = set()
    for position, value in enumerate(tasks["tasks_index"]):
        try:
            task_id = _source_int(value, label="tasks_index", nonnegative=True)
        except ValueError:
            continue
        if task_id in result:
            duplicates.add(task_id)
        else:
            result[task_id] = position
    for task_id in duplicates:
        result.pop(task_id, None)
    return result


def _base_task_is_eligible(tasks: Any, position: int) -> bool:
    try:
        return (
            _source_bool(_cell(tasks, position, "is_train"), label="is_train")
            and not _source_bool(_cell(tasks, position, "is_val"), label="is_val")
            and not _source_bool(_cell(tasks, position, "is_test"), label="is_test")
            and _source_int(_cell(tasks, position, "sheet_type"), label="sheet_type") == 0
            and _source_float(
                _cell(tasks, position, "sheet_width"), label="sheet_width", positive=True
            )
            > 0
            and _source_float(
                _cell(tasks, position, "sheet_length"), label="sheet_length", positive=True
            )
            > 0
        )
    except ValueError:
        return False


def _ranked_candidates(frames: Mapping[str, Any]) -> list[_RankedCandidate]:
    tasks = frames["tasks"]
    parts = frames["parts"]
    task_positions = _task_rows_by_id(tasks)

    grouped = parts.groupby("tasks_index", sort=False, dropna=False)["shape_hash"].agg(
        part_count="size", unique_shapes="nunique"
    )
    candidates: list[_RankedCandidate] = []
    for raw_task_id, row in grouped.iterrows():
        try:
            task_id = _source_int(raw_task_id, label="parts tasks_index", nonnegative=True)
            part_count = _source_int(row["part_count"], label="part count")
            unique_shapes = _source_int(row["unique_shapes"], label="unique shape count")
        except ValueError:
            continue
        task_position = task_positions.get(task_id)
        if task_position is None or not _base_task_is_eligible(tasks, task_position):
            continue
        if not MIN_PARTS <= part_count <= MAX_PARTS:
            continue
        repeated_rows = part_count - unique_shapes
        score = (
            abs(part_count - TARGET_PARTS)
            + abs(unique_shapes - TARGET_UNIQUE_SHAPES)
            + abs(repeated_rows - TARGET_REPEATED_PART_ROWS)
        )
        candidates.append(_RankedCandidate(tasks_index=task_id, rank_score=score))
    candidates.sort(key=lambda item: (item.rank_score, item.tasks_index))
    return candidates[:MAX_RANKED_CANDIDATES]


def _subset_positions(frame: Any, task_ids: set[int]) -> dict[int, list[int]]:
    result = {task_id: [] for task_id in task_ids}
    mask = frame["tasks_index"].isin(task_ids)
    for position in range(len(frame)):
        if not bool(mask.iloc[position]):
            continue
        task_id = _source_int(_cell(frame, position, "tasks_index"), label="tasks_index")
        if task_id in result:
            result[task_id].append(position)
    return result


def _shape_positions_for_candidates(
    shapes: Any,
    parts: Any,
    parts_by_task: Mapping[int, list[int]],
) -> dict[int, int]:
    hashes: set[int] = set()
    for positions in parts_by_task.values():
        for position in positions:
            hashes.add(_source_int(_cell(parts, position, "shape_hash"), label="shape_hash"))
    positions: dict[int, int] = {}
    duplicates: set[int] = set()
    mask = shapes["shape_hash"].isin(hashes)
    for position in range(len(shapes)):
        if not bool(mask.iloc[position]):
            continue
        shape_hash = _source_int(_cell(shapes, position, "shape_hash"), label="shape_hash")
        if shape_hash in positions:
            duplicates.add(shape_hash)
        else:
            positions[shape_hash] = position
    for shape_hash in duplicates:
        positions.pop(shape_hash, None)
    return positions


def _geometry_facts(shapes: Any, position: int) -> _GeometryFacts:
    raw_values = _sequence(_cell(shapes, position, "raw"), label="shape raw")
    if len(raw_values) < 6 or len(raw_values) % 2:
        raise ValueError("shape raw must be finite flat-even geometry with at least three points")
    raw = tuple(_source_number(value, label="shape raw scalar") for value in raw_values)
    sizes_values = _sequence(_cell(shapes, position, "sizes"), label="shape sizes")
    sizes = tuple(_source_int(value, label="shape size") for value in sizes_values)
    if sizes != (len(raw),):
        raise ValueError("shape sizes must equal [len(raw)]")
    paired = tuple(zip(raw[::2], raw[1::2], strict=True))
    ring_closure_added = paired[0] != paired[-1]
    closed_ring = paired + (paired[0],) if ring_closure_added else paired
    polygon = Polygon(paired)
    if not math.isfinite(polygon.area) or polygon.area <= 0:
        raise ValueError("shape polygon must have finite nonzero area")
    if not polygon.is_valid:
        raise ValueError("shape polygon must be valid without repair")
    if not polygon.is_simple:
        raise ValueError("shape polygon must be simple without repair")
    bounds = (
        min(point[0] for point in paired),
        min(point[1] for point in paired),
        max(point[0] for point in paired),
        max(point[1] for point in paired),
    )
    return _GeometryFacts(
        raw=raw,
        sizes=sizes,
        paired_points=paired,
        closed_ring=closed_ring,
        ring_closure_added=ring_closure_added,
        is_simple=bool(polygon.is_simple),
        is_valid=bool(polygon.is_valid),
        area=float(polygon.area),
        bounds=bounds,
    )


def _validate_task_geometry(
    task_id: int,
    *,
    parts: Any,
    shapes: Any,
    part_positions: list[int],
    shape_positions: Mapping[int, int],
) -> None:
    if not MIN_PARTS <= len(part_positions) <= MAX_PARTS:
        raise ValueError("task must contain 20-50 part rows")
    part_ids: set[int] = set()
    for position in part_positions:
        part_id = _source_int(_cell(parts, position, "part_id"), label="part_id", nonnegative=True)
        if part_id in part_ids:
            raise ValueError("task part_id values must be unique")
        part_ids.add(part_id)
        shape_hash = _source_int(_cell(parts, position, "shape_hash"), label="shape_hash")
        shape_position = shape_positions.get(shape_hash)
        if shape_position is None:
            raise ValueError(f"task {task_id} has an unresolved or duplicate shape_hash")
        _geometry_facts(shapes, shape_position)


def _validate_s1_task(
    task_id: int,
    *,
    parts: Any,
    constraints: Any,
    part_positions: list[int],
    constraint_positions: list[int],
) -> None:
    non_s1 = [
        position
        for position in constraint_positions
        if _constraint_type(_cell(constraints, position, "type")) != "s1"
    ]
    if non_s1:
        raise ValueError("runnable task contains non-s1 constraints")
    if len(constraint_positions) != len(part_positions):
        raise ValueError("runnable task requires exactly one s1 row per part")

    expected_parts = {
        _source_int(_cell(parts, position, "part_id"), label="part_id", nonnegative=True)
        for position in part_positions
    }
    observed_parts: set[int] = set()
    for position in constraint_positions:
        parts_1 = _sequence(_cell(constraints, position, "parts_1"), label="s1 parts_1")
        if len(parts_1) != 1:
            raise ValueError("s1 parts_1 must contain exactly one part_id")
        part_id = _source_int(parts_1[0], label="s1 parts_1 part_id", nonnegative=True)
        if part_id not in expected_parts:
            raise ValueError("s1 parts_1 names the wrong part_id")
        if part_id in observed_parts:
            raise ValueError("runnable task requires exactly one s1 row per part")
        observed_parts.add(part_id)

        if not _is_missing(_cell(constraints, position, "parts_2")):
            raise ValueError("s1 parts_2 must be missing")
        for column in CONSTRAINT_OPAQUE_FIELD_ORDER:
            if column in _S1_FIELDS or column == "parts_2":
                continue
            if not _is_missing(_cell(constraints, position, column)):
                raise ValueError(f"s1 unrelated parameter {column} must be missing")

        starts = _sequence(_cell(constraints, position, "r1_start"), label="s1 r1_start")
        ends = _sequence(_cell(constraints, position, "r1_end"), label="s1 r1_end")
        flips = _sequence(_cell(constraints, position, "r1_flip_x"), label="s1 r1_flip_x")
        if not (len(starts) == len(ends) == len(flips)):
            raise ValueError("s1 r1_start, r1_end, and r1_flip_x must have equal lengths")
        for start, end, flip in zip(starts, ends, flips, strict=True):
            start_number = _source_number(start, label="s1 rotation start")
            end_number = _source_number(end, label="s1 rotation end")
            if start_number != end_number:
                raise ValueError("s1 rotations must be degenerate with r1_start equal to r1_end")
            if _source_int(flip, label="s1 flip flag") != 0:
                raise ValueError("s1 flip flags must be strict integer zero")
    if observed_parts != expected_parts:
        raise ValueError("runnable task requires exactly one s1 row per part")


def _validate_opaque_value(value: Any) -> OpaqueValue:
    if _is_missing(value):
        return OpaqueMissing(kind="missing")
    value = _python_scalar(value)
    if type(value) is bool:
        return OpaqueBoolean(kind="boolean", value=value)
    if isinstance(value, Integral) and not isinstance(value, bool):
        return OpaqueInteger(kind="integer", value=int(value))
    if isinstance(value, Real) and not isinstance(value, Integral):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("opaque constraint number must be finite or missing")
        return OpaqueNumber(kind="number", value=number)
    if isinstance(value, str):
        return OpaqueString(kind="string", value=value)
    sequence = _sequence(value, label="opaque constraint value", nonempty=False)
    items = []
    for item in sequence:
        converted = _validate_opaque_value(item)
        if isinstance(converted, OpaqueSequence):
            raise ValueError("nested opaque constraint sequences are not supported")
        items.append(converted)
    return OpaqueSequence(kind="sequence", items=tuple(items))


def select_representative_task_ids(
    frames: Mapping[str, Any],
) -> RepresentativeTaskSelection:
    """Select one strict runnable task and one non-s1 view task from a top-256 ranking."""
    ranked = _ranked_candidates(frames)
    if not ranked:
        raise NoEligibleLectraSliceError("no tasks enter the bounded top 256 ranking")
    top_ids = {item.tasks_index for item in ranked}
    parts_by_task = _subset_positions(frames["parts"], top_ids)
    constraints_by_task = _subset_positions(frames["constraints"], top_ids)
    shape_positions = _shape_positions_for_candidates(
        frames["shapes"], frames["parts"], parts_by_task
    )

    runnable: _RankedCandidate | None = None
    view_only: _RankedCandidate | None = None
    first_runnable_failure: str | None = None
    for candidate in ranked:
        task_id = candidate.tasks_index
        try:
            _validate_task_geometry(
                task_id,
                parts=frames["parts"],
                shapes=frames["shapes"],
                part_positions=parts_by_task[task_id],
                shape_positions=shape_positions,
            )
        except ValueError as error:
            if first_runnable_failure is None:
                first_runnable_failure = str(error)
            continue

        constraint_positions = constraints_by_task[task_id]
        try:
            constraint_types = tuple(
                _constraint_type(_cell(frames["constraints"], position, "type"))
                for position in constraint_positions
            )
        except ValueError:
            constraint_types = ()
        has_non_s1 = bool(constraint_types) and any(
            constraint_type != "s1" for constraint_type in constraint_types
        )
        if has_non_s1 and view_only is None:
            try:
                for position in constraint_positions:
                    for column in CONSTRAINT_OPAQUE_FIELD_ORDER:
                        _validate_opaque_value(_cell(frames["constraints"], position, column))
            except ValueError:
                pass
            else:
                view_only = candidate
        try:
            _validate_s1_task(
                task_id,
                parts=frames["parts"],
                constraints=frames["constraints"],
                part_positions=parts_by_task[task_id],
                constraint_positions=constraint_positions,
            )
        except ValueError as error:
            if first_runnable_failure is None:
                first_runnable_failure = str(error)
        else:
            if runnable is None:
                runnable = candidate

    if runnable is None:
        detail = first_runnable_failure or "no task satisfied the strict s1 rule"
        raise NoEligibleLectraSliceError(f"no runnable task in top 256: {detail}")
    if view_only is None or view_only.tasks_index == runnable.tasks_index:
        raise NoEligibleLectraSliceError("no separate positive non-s1 view-only task in top 256")
    return RepresentativeTaskSelection(
        runnable_tasks_index=runnable.tasks_index,
        view_only_tasks_index=view_only.tasks_index,
        runnable_rank_score=runnable.rank_score,
        view_only_rank_score=view_only.rank_score,
    )


def _ordered_positions(frame: Any, positions: list[int], *, table: str) -> list[int]:
    indexed = [
        (_source_row_index(frame, position, table=table), position) for position in positions
    ]
    if len({source_index for source_index, _ in indexed}) != len(indexed):
        raise NoEligibleLectraSliceError(f"selected {table} source indexes must be unique")
    indexed.sort()
    return [position for _, position in indexed]


def export_representative_slice(
    frames: Mapping[str, Any],
    *,
    manifest: DatasetSourceManifest,
    source_manifest_sha256: str,
    audit_report_sha256: str,
) -> NormalizedSlice:
    """Export the two selected task roles into the strict passive slice contract."""
    selection = select_representative_task_ids(frames)
    selected_ids = {selection.runnable_tasks_index, selection.view_only_tasks_index}
    task_positions_by_id = _task_rows_by_id(frames["tasks"])
    task_positions = [task_positions_by_id[task_id] for task_id in selected_ids]
    parts_by_task = _subset_positions(frames["parts"], selected_ids)
    constraints_by_task = _subset_positions(frames["constraints"], selected_ids)
    part_positions = [position for task_id in selected_ids for position in parts_by_task[task_id]]
    constraint_positions = [
        position for task_id in selected_ids for position in constraints_by_task[task_id]
    ]
    selected_hashes = {
        _source_int(_cell(frames["parts"], position, "shape_hash"), label="shape_hash")
        for position in part_positions
    }
    shape_positions = [
        position
        for position, value in enumerate(frames["shapes"]["shape_hash"])
        if _source_int(value, label="shape_hash") in selected_hashes
    ]

    task_positions = _ordered_positions(frames["tasks"], task_positions, table="tasks")
    part_positions = _ordered_positions(frames["parts"], part_positions, table="parts")
    shape_positions = _ordered_positions(frames["shapes"], shape_positions, table="shapes")
    constraint_positions = _ordered_positions(
        frames["constraints"], constraint_positions, table="constraints"
    )

    tasks = tuple(
        TaskSourceRow(
            source_row_index=_source_row_index(frames["tasks"], position, table="tasks"),
            duration=_source_int(_cell(frames["tasks"], position, "duration"), label="duration"),
            efficiency=_source_float(
                _cell(frames["tasks"], position, "efficiency"), label="efficiency"
            ),
            sheet_width=_source_float(
                _cell(frames["tasks"], position, "sheet_width"), label="sheet_width"
            ),
            sheet_length=_source_float(
                _cell(frames["tasks"], position, "sheet_length"), label="sheet_length"
            ),
            sheet_type=_source_int(
                _cell(frames["tasks"], position, "sheet_type"), label="sheet_type"
            ),
            tasks_index=_source_int(
                _cell(frames["tasks"], position, "tasks_index"),
                label="tasks_index",
                nonnegative=True,
            ),
            is_train=_source_bool(_cell(frames["tasks"], position, "is_train"), label="is_train"),
            is_val=_source_bool(_cell(frames["tasks"], position, "is_val"), label="is_val"),
            is_test=_source_bool(_cell(frames["tasks"], position, "is_test"), label="is_test"),
        )
        for position in task_positions
    )
    parts = tuple(
        PartSourceRow(
            source_row_index=_source_row_index(frames["parts"], position, table="parts"),
            tasks_index=_source_int(
                _cell(frames["parts"], position, "tasks_index"),
                label="tasks_index",
                nonnegative=True,
            ),
            part_id=_source_int(
                _cell(frames["parts"], position, "part_id"), label="part_id", nonnegative=True
            ),
            shape_hash=_source_int(
                _cell(frames["parts"], position, "shape_hash"), label="shape_hash"
            ),
        )
        for position in part_positions
    )

    shapes = []
    derived_geometry = []
    for position in shape_positions:
        shape_hash = _source_int(
            _cell(frames["shapes"], position, "shape_hash"), label="shape_hash"
        )
        facts = _geometry_facts(frames["shapes"], position)
        shapes.append(
            ShapeSourceRow(
                source_row_index=_source_row_index(frames["shapes"], position, table="shapes"),
                shape_hash=shape_hash,
                raw=facts.raw,
                sizes=facts.sizes,
            )
        )
        derived_geometry.append(
            DerivedShapeGeometry(
                shape_hash=shape_hash,
                paired_points=facts.paired_points,
                closed_ring=facts.closed_ring,
                raw_scalar_count=len(facts.raw),
                ring_closure_added=facts.ring_closure_added,
                is_simple=facts.is_simple,
                is_valid=facts.is_valid,
                has_nonzero_area=facts.area > 0,
                area=facts.area,
                bounds=facts.bounds,
            )
        )

    constraints = tuple(
        ConstraintSourceRow(
            source_row_index=_source_row_index(
                frames["constraints"], position, table="constraints"
            ),
            tasks_index=_source_int(
                _cell(frames["constraints"], position, "tasks_index"),
                label="tasks_index",
                nonnegative=True,
            ),
            type=_constraint_type(_cell(frames["constraints"], position, "type")),
            values=tuple(
                _validate_opaque_value(_cell(frames["constraints"], position, column))
                for column in CONSTRAINT_OPAQUE_FIELD_ORDER
            ),
        )
        for position in constraint_positions
    )
    task_dispositions = tuple(
        TaskDisposition(
            tasks_index=task.tasks_index,
            normalization_status=NormalizationStatus.SOURCE_LOSSLESS,
            support_status=(
                SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
                if task.tasks_index == selection.runnable_tasks_index
                else SupportStatus.VIEW_ONLY
            ),
            projection_status=(
                ProjectionStatus.ELIGIBLE
                if task.tasks_index == selection.runnable_tasks_index
                else ProjectionStatus.BLOCKED
            ),
            reason_codes=(
                ()
                if task.tasks_index == selection.runnable_tasks_index
                else ("contains_non_s1_constraints",)
            ),
            assumption_codes=(
                (S1_ASSUMPTION,) if task.tasks_index == selection.runnable_tasks_index else ()
            ),
        )
        for task in tasks
    )
    provenance = (
        ProvenanceGroup(
            kind=ProvenanceKind.SOURCE_REAL,
            field_paths=SOURCE_REAL_PROVENANCE_PATHS,
            note="Exact selected source rows and typed opaque constraint cells.",
        ),
        ProvenanceGroup(
            kind=ProvenanceKind.DERIVED,
            field_paths=DERIVED_PROVENANCE_PATHS,
            note=(
                "Adjacent-scalar pairing, ring closure, polygon facts, and support classification."
            ),
        ),
        ProvenanceGroup(
            kind=ProvenanceKind.ASSUMED,
            field_paths=(ASSUMED_PROVENANCE_PATH,),
            note="Only the declared degenerate-s1 orientation interpretation.",
        ),
    )
    return NormalizedSlice(
        schema_version="yieldforge.normalized-slice.v1",
        source=NormalizedSliceSource(
            dataset_id="lectra-7030786-v1.1",
            source_checksums=tuple(
                SourceChecksum(
                    name=source.name,
                    checksum_algorithm=source.checksum_algorithm,
                    checksum=source.checksum,
                )
                for source in manifest.files
            ),
            source_manifest_sha256=source_manifest_sha256,
            audit_report_sha256=audit_report_sha256,
            doi="10.5281/zenodo.7030786",
            license="CC-BY-4.0",
            source_unit=SourceUnit(literal_label="m^-4", interpretation=None),
            conversion_ruleset_version=CONVERSION_RULESET_VERSION,
        ),
        tasks=tasks,
        parts=parts,
        shapes=tuple(shapes),
        constraints=constraints,
        constraint_value_columns=CONSTRAINT_OPAQUE_FIELD_ORDER,
        derived_geometry=tuple(derived_geometry),
        task_dispositions=task_dispositions,
        provenance=provenance,
    )
