from __future__ import annotations

import copy
import hashlib

import pandas as pd
import pytest

from yieldforge.datasets.lectra_slice import (
    CATALOG_CONVERSION_RULESET_VERSION,
    S1_ASSUMPTION,
    NoEligibleLectraSliceError,
    export_catalog_slice,
    export_representative_slice,
    select_catalog_task_ids,
    select_representative_task_ids,
)
from yieldforge.datasets.normalized_slice import (
    CONSTRAINT_OPAQUE_FIELD_ORDER,
    OpaqueMissing,
    OpaqueSequence,
    ProjectionStatus,
    SupportStatus,
    constraint_value,
)
from yieldforge.datasets.source_manifest import DatasetSourceManifest

CONSTRAINT_COLUMNS = [
    *CONSTRAINT_OPAQUE_FIELD_ORDER,
    "tasks_index",
    "type",
]


def _shape(shape_hash: int, *, raw: object | None = None, sizes: object | None = None) -> dict:
    coordinates = [0.0, 0.0, 2.0, 0.0, 0.0, 2.0] if raw is None else raw
    return {
        "shape_hash": shape_hash,
        "raw": coordinates,
        "sizes": [len(coordinates)] if sizes is None else sizes,
    }


def _constraint(
    task_id: int,
    part_id: int,
    *,
    kind: str = "s1",
    start: object = (0.0,),
    end: object = (0.0,),
    flip: object = (0,),
    parts_1: object | None = None,
    parts_2: object = None,
    extra_column: str | None = None,
    extra_value: object = 1,
) -> dict:
    row = dict.fromkeys(CONSTRAINT_COLUMNS)
    row.update(
        {
            "parts_1": [part_id] if parts_1 is None else parts_1,
            "parts_2": parts_2,
            "r1_start": start,
            "r1_end": end,
            "r1_flip_x": flip,
            "tasks_index": task_id,
            "type": kind,
        }
    )
    if extra_column is not None:
        row[extra_column] = extra_value
    return row


def _task(task_id: int, *, part_count: int, sheet_length: float = 20.0) -> dict:
    return {
        "duration": 3,
        "efficiency": 0.75,
        "sheet_width": 10.0,
        "sheet_length": sheet_length,
        "sheet_type": 0,
        "tasks_index": task_id,
        "is_train": True,
        "is_val": False,
        "is_test": False,
        "part_count": part_count,
    }


def _frames(
    *,
    runnable_specs: tuple[tuple[int, int, int], ...] = ((100, 35, 9),),
    include_view: bool = True,
) -> dict[str, pd.DataFrame]:
    tasks: list[dict] = []
    parts: list[dict] = []
    shapes: dict[int, dict] = {}
    constraints: list[dict] = []
    for task_id, part_count, unique_shapes in runnable_specs:
        task = _task(task_id, part_count=part_count)
        task.pop("part_count")
        tasks.append(task)
        for offset in range(part_count):
            shape_hash = task_id * 100 + offset % unique_shapes
            shapes.setdefault(shape_hash, _shape(shape_hash))
            part_id = task_id * 1000 + offset
            parts.append({"tasks_index": task_id, "part_id": part_id, "shape_hash": shape_hash})
            constraints.append(_constraint(task_id, part_id))

    if include_view:
        task_id = 900
        task = _task(task_id, part_count=20)
        task.pop("part_count")
        tasks.append(task)
        for offset in range(20):
            shape_hash = task_id * 100 + offset % 5
            shapes.setdefault(shape_hash, _shape(shape_hash))
            parts.append(
                {
                    "tasks_index": task_id,
                    "part_id": task_id * 1000 + offset,
                    "shape_hash": shape_hash,
                }
            )
        constraints.append(_constraint(task_id, task_id * 1000, kind="c1"))

    return {
        "tasks": pd.DataFrame(tasks),
        "parts": pd.DataFrame(parts),
        "shapes": pd.DataFrame(list(shapes.values())),
        "constraints": pd.DataFrame(constraints, columns=CONSTRAINT_COLUMNS),
    }


def _add_view_candidate(
    frames: dict[str, pd.DataFrame],
    *,
    task_id: int,
    parts_1: object,
    parts_2: object = None,
) -> None:
    task = _task(task_id, part_count=35)
    task.pop("part_count")
    frames["tasks"] = pd.concat([frames["tasks"], pd.DataFrame([task])], ignore_index=True)
    new_parts = []
    new_shapes = []
    for offset in range(35):
        shape_hash = task_id * 100 + offset % 9
        new_parts.append(
            {
                "tasks_index": task_id,
                "part_id": task_id * 1000 + offset,
                "shape_hash": shape_hash,
            }
        )
        if offset < 9:
            new_shapes.append(_shape(shape_hash))
    frames["parts"] = pd.concat([frames["parts"], pd.DataFrame(new_parts)], ignore_index=True)
    frames["shapes"] = pd.concat([frames["shapes"], pd.DataFrame(new_shapes)], ignore_index=True)
    row = _constraint(
        task_id,
        task_id * 1000,
        kind="c1",
        parts_1=parts_1,
        parts_2=parts_2,
    )
    frames["constraints"] = pd.concat(
        [frames["constraints"], pd.DataFrame([row], columns=CONSTRAINT_COLUMNS)],
        ignore_index=True,
    )


def _manifest() -> DatasetSourceManifest:
    names = ("parts.gz", "constraints.gz", "shapes.gz", "tasks.gz")
    return DatasetSourceManifest.model_validate(
        {
            "schema_version": "yieldforge.dataset-source.v1",
            "dataset_id": "lectra-7030786-v1.1",
            "title": "Lectra",
            "doi": "10.5281/zenodo.7030786",
            "version": "1.1",
            "license": "CC-BY-4.0",
            "source_page": "https://example.invalid",
            "files": [
                {
                    "name": name,
                    "url": f"https://example.invalid/{name}",
                    "size_bytes": index + 1,
                    "checksum_algorithm": "md5",
                    "checksum": f"{index + 1:x}" * 32,
                }
                for index, name in enumerate(names)
            ],
        }
    )


def _export(frames: dict[str, pd.DataFrame]):
    return export_representative_slice(
        frames,
        manifest=_manifest(),
        source_manifest_sha256=hashlib.sha256(b"manifest").hexdigest(),
        audit_report_sha256=hashlib.sha256(b"audit").hexdigest(),
    )


def _catalog_frames(*, filler_count: int = 258) -> dict[str, pd.DataFrame]:
    task_ids = (13_958, 25_801, *range(30_000, 30_000 + filler_count))
    frames = _frames(
        runnable_specs=tuple((task_id, 35, 9) for task_id in task_ids),
        include_view=False,
    )
    first_25801_constraint = frames["constraints"].index[
        frames["constraints"]["tasks_index"] == 25_801
    ][0]
    frames["constraints"].at[first_25801_constraint, "type"] = "c8"
    return frames


def _export_catalog(frames: dict[str, pd.DataFrame], *, target_count: int = 256):
    return export_catalog_slice(
        frames,
        manifest=_manifest(),
        source_manifest_sha256=hashlib.sha256(b"manifest").hexdigest(),
        audit_report_sha256=hashlib.sha256(b"audit").hexdigest(),
        target_count=target_count,
    )


def _catalog_disposition(selection, task_id: int):
    return next(
        disposition
        for disposition in selection.task_dispositions
        if disposition.tasks_index == task_id
    )


def test_catalog_selects_exactly_256_stably_and_preserves_continuity_tasks() -> None:
    frames = _catalog_frames()
    selection = select_catalog_task_ids(frames)
    reordered = {
        name: frame.sample(frac=1, random_state=index + 41)
        for index, (name, frame) in enumerate(frames.items())
    }

    assert len(selection.task_ids) == 256
    assert selection.task_ids == select_catalog_task_ids(reordered).task_ids
    assert 13_958 in selection.task_ids
    assert 25_801 in selection.task_ids


def test_catalog_scans_past_invalid_ranked_candidates() -> None:
    frames = _catalog_frames(filler_count=255)
    invalid_task = 30_000
    bad_part = frames["parts"].index[frames["parts"]["tasks_index"] == invalid_task][0]
    frames["parts"].at[bad_part, "shape_hash"] = -999

    selection = select_catalog_task_ids(frames)

    assert len(selection.task_ids) == 256
    assert invalid_task not in selection.task_ids
    assert 30_254 in selection.task_ids


def test_catalog_rejects_insufficient_display_safe_tasks() -> None:
    frames = _catalog_frames(filler_count=253)

    with pytest.raises(NoEligibleLectraSliceError, match="255.*256.*display-safe"):
        select_catalog_task_ids(frames)


def test_catalog_rejects_missing_continuity_task() -> None:
    frames = _catalog_frames()
    for table in ("tasks", "parts", "constraints"):
        frames[table] = frames[table][frames[table]["tasks_index"] != 25_801]

    with pytest.raises(NoEligibleLectraSliceError, match="continuity.*25801"):
        select_catalog_task_ids(frames)


def test_catalog_skips_malformed_opaque_constraints() -> None:
    frames = _catalog_frames(filler_count=255)
    invalid_task = 30_000
    bad_constraint = frames["constraints"].index[
        frames["constraints"]["tasks_index"] == invalid_task
    ][0]
    frames["constraints"].at[bad_constraint, "p1_x"] = [[1]]

    selection = select_catalog_task_ids(frames)

    assert len(selection.task_ids) == 256
    assert invalid_task not in selection.task_ids


def test_catalog_classifies_strict_s1_and_view_only_tasks_explicitly() -> None:
    frames = _catalog_frames(filler_count=0)
    selection = select_catalog_task_ids(frames, target_count=2)

    runnable = _catalog_disposition(selection, 13_958)
    assert runnable.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
    assert runnable.projection_status is ProjectionStatus.ELIGIBLE
    assert runnable.reason_codes == ()
    assert runnable.assumption_codes == (S1_ASSUMPTION,)

    view_only = _catalog_disposition(selection, 25_801)
    assert view_only.support_status is SupportStatus.VIEW_ONLY
    assert view_only.projection_status is ProjectionStatus.BLOCKED
    assert view_only.reason_codes == ("contains_non_s1_constraints",)
    assert view_only.assumption_codes == ()


def test_catalog_export_uses_catalog_ruleset_and_selection_dispositions() -> None:
    frames = _catalog_frames(filler_count=0)

    normalized = _export_catalog(frames, target_count=2)

    assert normalized.source.conversion_ruleset_version == CATALOG_CONVERSION_RULESET_VERSION
    assert tuple(task.tasks_index for task in normalized.tasks) == (13_958, 25_801)
    assert normalized.task_dispositions[0].assumption_codes == (S1_ASSUMPTION,)
    assert normalized.task_dispositions[1].reason_codes == ("contains_non_s1_constraints",)


def test_selector_uses_exact_rank_score_then_task_id() -> None:
    frames = _frames(
        runnable_specs=(
            (300, 36, 10),  # |36-35| + |10-9| + |26-23| = 5
            (200, 34, 10),  # |34-35| + |10-9| + |24-23| = 3
            (100, 36, 11),  # |36-35| + |11-9| + |25-23| = 5
            (150, 34, 10),  # same score as task 200, lower task ID
        )
    )

    selected = select_representative_task_ids(frames)

    assert selected.runnable_tasks_index == 150
    assert selected.view_only_tasks_index == 900
    assert selected.runnable_rank_score == 3


def test_selection_is_independent_of_frame_row_order() -> None:
    frames = _frames(runnable_specs=((100, 35, 9), (200, 34, 10)))
    expected = select_representative_task_ids(frames)
    shuffled = {
        name: frame.sample(frac=1, random_state=index + 7)
        for index, (name, frame) in enumerate(frames.items())
    }

    assert select_representative_task_ids(shuffled) == expected


def test_export_preserves_source_rows_and_separates_derived_geometry() -> None:
    frames = _frames()
    view_constraint = frames["constraints"]["tasks_index"] == 900
    frames["constraints"].loc[view_constraint, "p1_x"] = (
        frames["constraints"].loc[view_constraint, "p1_x"].map(lambda _: [])
    )
    frames["tasks"].index += 10
    frames["parts"].index += 100
    frames["shapes"].index += 200
    frames["constraints"].index += 300

    normalized = _export(frames)

    assert tuple(task.tasks_index for task in normalized.tasks) == (100, 900)
    assert tuple(task.source_row_index for task in normalized.tasks) == (10, 11)
    assert normalized.constraint_value_columns == CONSTRAINT_OPAQUE_FIELD_ORDER
    assert (
        normalized.task_dispositions[0].support_status
        is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
    )
    assert normalized.task_dispositions[0].projection_status is ProjectionStatus.ELIGIBLE
    assert normalized.task_dispositions[0].assumption_codes == (
        "interpret_s1_degenerate_entries_as_allowed_rotations",
    )
    assert normalized.task_dispositions[1].support_status is SupportStatus.VIEW_ONLY
    assert normalized.task_dispositions[1].reason_codes == ("contains_non_s1_constraints",)
    assert normalized.shapes[0].raw == tuple(frames["shapes"].iloc[0]["raw"])
    assert normalized.derived_geometry[0].paired_points == (
        (0.0, 0.0),
        (2.0, 0.0),
        (0.0, 2.0),
    )
    assert normalized.derived_geometry[0].ring_closure_added is True
    first_constraint = normalized.constraints[0]
    assert isinstance(constraint_value(first_constraint, "parts_1"), OpaqueSequence)
    assert isinstance(constraint_value(first_constraint, "parts_2"), OpaqueMissing)
    view_constraint_row = next(row for row in normalized.constraints if row.tasks_index == 900)
    empty_value = constraint_value(view_constraint_row, "p1_x")
    assert isinstance(empty_value, OpaqueSequence)
    assert empty_value.items == ()


def test_view_only_task_requires_a_real_nonempty_constraint_type() -> None:
    frames = _frames()
    view_constraint = frames["constraints"]["tasks_index"] == 900
    frames["constraints"].loc[view_constraint, "type"] = None

    with pytest.raises(NoEligibleLectraSliceError, match="non-s1 view-only"):
        select_representative_task_ids(frames)


@pytest.mark.parametrize(
    ("parts_1", "parts_2"),
    [
        (800_000, None),
        ([999_999], None),
        ([800_000.0], None),
        ([800_000], 800_001),
        ([800_000], [999_999]),
        ([800_000], [800_001.0]),
    ],
)
def test_malformed_or_unresolved_view_references_skip_to_next_ranked_candidate(
    parts_1: object,
    parts_2: object,
) -> None:
    frames = _frames()
    _add_view_candidate(frames, task_id=800, parts_1=parts_1, parts_2=parts_2)

    selected = select_representative_task_ids(frames)
    normalized = _export(frames)

    assert selected.view_only_tasks_index == 900
    assert tuple(task.tasks_index for task in normalized.tasks) == (100, 900)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda rows: rows.pop(), "one s1 row per part"),
        (lambda rows: rows.append(copy.deepcopy(rows[0])), "one s1 row per part"),
        (lambda rows: rows[0].update(parts_1=[-1]), "parts_1"),
        (lambda rows: rows[0].update(r1_start=[]), "nonempty"),
        (lambda rows: rows[0].update(r1_end=[1.0]), "degenerate"),
        (lambda rows: rows[0].update(r1_flip_x=[1]), "flip"),
        (lambda rows: rows[0].update(parts_2=[1]), "parts_2"),
        (lambda rows: rows[0].update(p1_x=0.0), "unrelated"),
        (lambda rows: rows[0].update(type="c1"), "non-s1"),
    ],
)
def test_all_s1_failure_modes_fail_closed(mutation, match: str) -> None:
    frames = _frames(include_view=False)
    rows = frames["constraints"].to_dict("records")
    mutation(rows)
    frames["constraints"] = pd.DataFrame(rows, columns=CONSTRAINT_COLUMNS)

    with pytest.raises(NoEligibleLectraSliceError, match=match):
        select_representative_task_ids(frames)


@pytest.mark.parametrize(
    ("raw", "sizes", "match"),
    [
        ([0.0, 0.0, 1.0], [3], "flat-even"),
        ([0.0, 0.0, float("nan"), 1.0, 0.0, 2.0], [6], "finite"),
        ([0.0, 0.0, 2.0, 2.0, 0.0, 2.0, 2.0, 0.0], [8], "valid|nonzero"),
        ([0.0, 0.0, 1.0, 0.0, 2.0, 0.0], [6], "valid|nonzero"),
        ([0.0, 0.0, 1.0, 0.0, 0.0, 1.0], [3], "sizes"),
    ],
)
def test_bad_polygons_are_not_repaired(raw: object, sizes: object, match: str) -> None:
    frames = _frames(include_view=False)
    target_hash = frames["parts"].iloc[0]["shape_hash"]
    shape_position = frames["shapes"].index[frames["shapes"]["shape_hash"] == target_hash][0]
    frames["shapes"].at[shape_position, "raw"] = raw
    frames["shapes"].at[shape_position, "sizes"] = sizes

    with pytest.raises(NoEligibleLectraSliceError, match=match):
        select_representative_task_ids(frames)


def test_selector_never_weakens_rules_when_top_256_have_no_eligible_task() -> None:
    specs = tuple((task_id, 35, 9) for task_id in range(1, 258))
    frames = _frames(runnable_specs=specs, include_view=False)
    rows = frames["constraints"].to_dict("records")
    for row in rows:
        if row["tasks_index"] <= 256:
            row["r1_flip_x"] = [1]
    frames["constraints"] = pd.DataFrame(rows, columns=CONSTRAINT_COLUMNS)

    with pytest.raises(NoEligibleLectraSliceError, match="top 256"):
        select_representative_task_ids(frames)
