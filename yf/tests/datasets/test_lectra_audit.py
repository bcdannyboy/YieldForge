import json
import math
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from yieldforge.datasets.lectra_audit import (
    LectraAuditReport,
    LectraInvalidColumnsError,
    LectraMissingColumnsError,
    NumericSummary,
    audit_frames,
    report_to_json,
)

TASK_COLUMNS = [
    "efficiency",
    "duration",
    "sheet_width",
    "sheet_length",
    "sheet_type",
    "tasks_index",
    "is_train",
    "is_val",
    "is_test",
]
CONSTRAINT_COLUMNS = [
    "type",
    "tasks_index",
    "parts_1",
    "parts_2",
    "p1_x",
    "p1_y",
    "p2_x",
    "p2_y",
    "r1_start",
    "r1_end",
    "r1_flip_x",
    "y_min",
    "y_max",
    "x_offset",
    "y_offset",
    "motif_order",
    "x_alignment_type",
    "y_alignment_type",
    "proximity_type",
    "max_distance",
    "groups_relative_orientation",
    "is_frozen",
]


def constraint_row(**overrides: object) -> dict[str, object]:
    row = dict.fromkeys(CONSTRAINT_COLUMNS)
    row.update(
        {
            "type": "opaque-a",
            "tasks_index": 10,
            "parts_1": [1],
            "parts_2": None,
            "p1_x": 1.0,
        }
    )
    row.update(overrides)
    return row


def trusted_frames() -> dict[str, pd.DataFrame]:
    tasks = pd.DataFrame(
        [
            [0.5, 10.0, 4.0, 8.0, "roll", 10, True, False, False],
            [math.nan, math.inf, 6.0, 12.0, "sheet", 11, False, True, False],
            [0.8, 20.0, 5.0, 10.0, "roll", 12, False, False, True],
        ],
        columns=TASK_COLUMNS,
    )
    tasks["future_column"] = "preserved-in-inventory"

    parts = pd.DataFrame(
        [
            {"tasks_index": 10, "parts_id": 1, "shape_hash": "shape-a"},
            {"tasks_index": 10, "parts_id": 2, "shape_hash": "shape-a"},
            {"tasks_index": 10, "parts_id": 2, "shape_hash": "shape-b"},
            {"tasks_index": 11, "parts_id": 4, "shape_hash": "shape-c"},
            {"tasks_index": 99, "parts_id": 3, "shape_hash": "shape-missing"},
        ]
    )
    shapes = pd.DataFrame(
        [
            {
                "shape_hash": "shape-a",
                "raw": [0.0, 0.0, 1.0, 0.0, 1.0, 1.0],
                "sizes": [3],
            },
            {
                "shape_hash": "shape-a",
                "raw": [0.0, 0.0, 1.0, 0.0, 1.0, 1.0],
                "sizes": [3],
            },
            {
                "shape_hash": "shape-b",
                "raw": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
                "sizes": [3],
            },
            {"shape_hash": "shape-c", "raw": [0.0, 0.0, 1.0], "sizes": [2]},
            {"shape_hash": "shape-unused", "raw": ["not-numeric"], "sizes": []},
        ]
    )
    constraints = pd.DataFrame(
        [
            constraint_row(parts_1=[1, 99]),
            constraint_row(tasks_index=99, parts_1=[3]),
            constraint_row(type="opaque-b", tasks_index=11, parts_1=4),
            constraint_row(type=None, tasks_index=11, parts_1=None, parts_2=[4, 4.5]),
        ],
        columns=CONSTRAINT_COLUMNS,
    )
    return {
        "tasks": tasks,
        "parts": parts,
        "shapes": shapes,
        "constraints": constraints,
    }


def audit() -> LectraAuditReport:
    return audit_frames(
        trusted_frames(),
        dataset_id="lectra-test",
        source_checksums={"tasks.gz": "a" * 32, "parts.gz": "b" * 32},
    )


def test_audit_inventories_source_tables_without_interpreting_units() -> None:
    report = audit()

    assert report.dataset_id == "lectra-test"
    assert report.source_checksums == {"tasks.gz": "a" * 32, "parts.gz": "b" * 32}
    assert report.source_unit_label == "m^-4"
    assert report.table_rows == {"tasks": 3, "parts": 5, "shapes": 5, "constraints": 4}
    assert report.columns["tasks"] == [*TASK_COLUMNS, "future_column"]
    assert report.unexpected_columns == {
        "tasks": ["future_column"],
        "parts": [],
        "shapes": [],
        "constraints": [],
    }
    assert report.missing_columns == {
        "tasks": [],
        "parts": [],
        "shapes": [],
        "constraints": [],
    }
    assert report.dtypes["tasks"]["tasks_index"] == "int64"


def test_audit_counts_duplicate_keys_and_join_failures_with_explicit_semantics() -> None:
    report = audit()

    assert report.duplicate_counts == {
        "task_key_rows_beyond_first": 0,
        "part_composite_key_rows_beyond_first": 1,
        "shape_key_rows_beyond_first": 1,
    }
    assert report.join_failures == {
        "part_rows_missing_task": 1,
        "part_rows_missing_shape": 1,
        "constraint_rows_missing_task": 1,
        "constraint_part_reference_occurrences_missing_part": 1,
    }
    assert "(10, 2)" in report.bounded_examples["duplicate_part_composite_keys"]
    assert "(10, 99)" in report.bounded_examples["missing_constraint_part_references"]
    assert all(len(examples) <= 10 for examples in report.bounded_examples.values())
    assert "part_composite_key_rows_beyond_first" in report.count_semantics
    assert "constraint_part_reference_occurrences_missing_part" in report.count_semantics


def test_audit_reports_task_and_shape_recurrence_summaries() -> None:
    report = audit()

    assert report.task_part_row_summary == NumericSummary.from_values([3, 1, 0])
    assert report.task_unique_shape_summary == NumericSummary.from_values([2, 1, 0])
    assert report.task_repeated_shape_row_summary == NumericSummary.from_values([1, 0, 0])
    assert report.shape_part_row_recurrence_summary == NumericSummary.from_values([2, 1, 1, 0])
    assert report.shape_distinct_task_recurrence_summary == NumericSummary.from_values([1, 1, 1, 0])
    assert report.unused_record_counts == {
        "task_records_without_part_rows": 1,
        "shape_records_without_part_rows": 1,
    }


def test_audit_keeps_numeric_metrics_finite_and_json_safe() -> None:
    report = audit()

    assert report.efficiency_summary.count == 3
    assert report.efficiency_summary.finite_count == 2
    assert report.efficiency_summary.nonfinite_count == 1
    assert report.efficiency_summary.missing_count == 0
    assert report.duration_summary.finite_count == 2
    assert report.duration_summary.nonfinite_count == 1
    assert report.sheet_width_summary == NumericSummary.from_values([4.0, 6.0, 5.0])
    assert report.sheet_length_summary == NumericSummary.from_values([8.0, 12.0, 10.0])

    serialized = report_to_json(report)
    assert "NaN" not in serialized
    assert "Infinity" not in serialized
    assert json.loads(serialized)["schema_version"] == "yieldforge.lectra-audit.v1"
    json.dumps(report.model_dump(mode="json"), allow_nan=False)


def test_audit_preserves_raw_encodings_sizes_and_literal_constraint_types() -> None:
    report = audit()

    assert report.raw_encoding_frequency == {
        "flat_numeric_even": 2,
        "flat_numeric_odd": 1,
        "malformed": 1,
        "point_pairs": 1,
    }
    assert report.size_relation_frequency == {
        "not_evaluable": 1,
        "pointpairs_sum_sizes_eq_raw_length": 1,
        "twice_sum_sizes_eq_raw_length": 2,
        "no_declared_relation": 1,
    }
    assert report.source_declared_subshape_count_frequency == {"1": 4}
    assert report.constraint_type_frequency == {
        "<missing>": 1,
        "opaque-a": 2,
        "opaque-b": 1,
    }
    assert report.constraint_parameter_presence["p1_x"] == {"missing": 0, "present": 4}
    assert report.constraint_parameter_presence["p1_y"] == {"missing": 4, "present": 0}
    assert report.constraint_parameter_shape["p1_x"] == {"number": 4}
    assert report.constraint_parameter_shape["p1_y"] == {"missing": 4}
    assert report.malformed_counts["raw_encoding_rows"] == 1
    assert report.malformed_counts["sizes_rows"] == 1
    assert report.malformed_counts["constraint_reference_cells"] == 1
    assert report.malformed_counts["constraint_reference_non_integral_elements"] == 1
    assert report.bounded_examples["missing_constraint_type_rows"] == ["task:11:row:3"]


def test_audit_reports_sheet_and_partition_violations() -> None:
    frames = trusted_frames()
    frames["tasks"].loc[0, "is_val"] = True
    frames["tasks"]["is_test"] = frames["tasks"]["is_test"].astype(object)
    frames["tasks"].loc[1, "is_test"] = "yes"

    report = audit_frames(
        frames,
        dataset_id="lectra-test",
        source_checksums={"tasks.gz": "a" * 32},
    )

    assert report.sheet_type_frequency == {"roll": 2, "sheet": 1}
    assert report.sheet_type_violation_counts == {"task_rows_with_missing_sheet_type": 0}
    assert report.partition_violation_counts == {
        "task_rows_with_non_boolean_partition_value": 1,
        "task_rows_not_assigned_to_exactly_one_partition": 2,
    }
    assert report.partition_true_frequency == {"is_test": 1, "is_train": 1, "is_val": 2}


def test_sheet_length_minus_one_is_preserved_as_a_valid_unconstrained_sentinel() -> None:
    frames = trusted_frames()
    frames["tasks"].loc[0, "sheet_length"] = -1.0
    frames["tasks"].loc[1, "sheet_length"] = 0.0

    report = audit_frames(
        frames,
        dataset_id="lectra-test",
        source_checksums={"tasks.gz": "a" * 32},
    )

    assert report.sheet_length_unconstrained_sentinel_count == 1
    assert report.sheet_dimension_violation_counts == {
        "task_rows_with_invalid_sheet_width": 0,
        "task_rows_with_invalid_sheet_length": 1,
    }
    assert report.bounded_examples["invalid_sheet_length_tasks"] == ["task:11:row:1"]


def test_malformed_constraint_task_key_is_counted_without_crashing() -> None:
    frames = trusted_frames()
    frames["constraints"]["tasks_index"] = frames["constraints"]["tasks_index"].astype(object)
    frames["constraints"].at[0, "tasks_index"] = [10]

    report = audit_frames(
        frames,
        dataset_id="lectra-test",
        source_checksums={"tasks.gz": "a" * 32},
    )

    assert report.key_failure_counts["constraint_rows_with_missing_or_invalid_task_key"] == 1
    assert report.join_failures["constraint_rows_missing_task"] == 2
    assert report.join_failures["constraint_part_reference_occurrences_missing_part"] == 2


def test_missing_required_columns_fail_once_with_complete_map() -> None:
    frames = trusted_frames()
    frames["tasks"] = frames["tasks"].drop(columns=["efficiency", "duration"])
    frames["parts"] = frames["parts"].drop(columns=["parts_id"])
    frames.pop("constraints")

    with pytest.raises(LectraMissingColumnsError) as caught:
        audit_frames(
            frames,
            dataset_id="lectra-test",
            source_checksums={"tasks.gz": "a" * 32},
        )

    assert caught.value.missing_columns == {
        "tasks": ["duration", "efficiency"],
        "parts": ["parts_id"],
        "shapes": [],
        "constraints": sorted(CONSTRAINT_COLUMNS),
    }
    assert "constraints" in str(caught.value)
    assert "efficiency" in str(caught.value)


def test_report_contract_is_strict_and_frozen() -> None:
    report = audit()

    with pytest.raises(ValidationError, match="frozen"):
        report.dataset_id = "changed"  # type: ignore[misc]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LectraAuditReport.model_validate({**report.model_dump(), "unknown": True})


def test_numeric_summary_uses_linear_interpolation_and_rejects_invalid_contract_values() -> None:
    summary = NumericSummary.from_values([None, 0.0, 10.0, 20.0, math.nan, math.inf, "bad"])

    assert summary.count == 7
    assert summary.finite_count == 3
    assert summary.missing_count == 1
    assert summary.nonfinite_count == 2
    assert summary.invalid_count == 1
    assert summary.minimum == 0.0
    assert summary.p25 == 5.0
    assert summary.median == 10.0
    assert summary.p75 == 15.0
    assert summary.p95 == 19.0
    assert summary.maximum == 20.0
    assert summary.mean == 10.0

    with pytest.raises(ValidationError):
        NumericSummary(count=1, finite_count=1, minimum=math.inf)

    extreme = NumericSummary.from_values([-1e308, 1e308])
    assert extreme.median == 0.0
    assert extreme.mean == 0.0
    json.dumps(extreme.model_dump(mode="json"), allow_nan=False)


def test_normal_package_never_deserializes_pickle() -> None:
    package_root = Path(__file__).parents[2] / "src" / "yieldforge"
    offenders = [
        path
        for path in package_root.rglob("*.py")
        if "read_pickle" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


@pytest.mark.parametrize(
    ("field", "metric"),
    [
        ("duplicate_counts", "task_key_rows_beyond_first"),
        ("join_failures", "part_rows_missing_task"),
        ("malformed_counts", "raw_encoding_rows"),
        ("unused_record_counts", "task_records_without_part_rows"),
        (
            "partition_violation_counts",
            "task_rows_with_non_boolean_partition_value",
        ),
    ],
)
def test_passive_report_rejects_negative_internal_counts(field: str, metric: str) -> None:
    payload = audit().model_dump()
    payload[field][metric] = -1

    with pytest.raises(ValidationError):
        LectraAuditReport.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "metric"),
    [
        ("duplicate_counts", "task_key_rows_beyond_first"),
        ("join_failures", "part_rows_missing_task"),
        ("malformed_counts", "raw_encoding_rows"),
        ("unused_record_counts", "task_records_without_part_rows"),
        (
            "partition_violation_counts",
            "task_rows_with_non_boolean_partition_value",
        ),
    ],
)
def test_passive_report_rejects_missing_or_unknown_metric_keys(field: str, metric: str) -> None:
    missing_payload = audit().model_dump()
    missing_payload[field].pop(metric)
    with pytest.raises(ValidationError, match="exact metric keys"):
        LectraAuditReport.model_validate(missing_payload)

    unknown_payload = audit().model_dump()
    unknown_payload[field]["bogus"] = 1
    with pytest.raises(ValidationError, match="exact metric keys"):
        LectraAuditReport.model_validate(unknown_payload)


def test_passive_report_requires_exact_bounded_example_and_semantic_keys() -> None:
    payload = audit().model_dump()

    missing_examples = deepcopy(payload)
    missing_examples["bounded_examples"].pop("duplicate_task_keys")
    with pytest.raises(ValidationError, match="exact metric keys"):
        LectraAuditReport.model_validate(missing_examples)

    unknown_examples = deepcopy(payload)
    unknown_examples["bounded_examples"]["bogus"] = []
    with pytest.raises(ValidationError, match="exact metric keys"):
        LectraAuditReport.model_validate(unknown_examples)

    too_many_examples = deepcopy(payload)
    too_many_examples["bounded_examples"]["duplicate_task_keys"] = [
        f"task:{index}" for index in range(11)
    ]
    with pytest.raises(ValidationError, match="at most 10"):
        LectraAuditReport.model_validate(too_many_examples)

    missing_semantic = deepcopy(payload)
    missing_semantic["count_semantics"].pop("task_key_rows_beyond_first")
    with pytest.raises(ValidationError, match="exact metric keys"):
        LectraAuditReport.model_validate(missing_semantic)

    unknown_semantic = deepcopy(payload)
    unknown_semantic["count_semantics"]["bogus"] = "not part of the contract"
    with pytest.raises(ValidationError, match="exact metric keys"):
        LectraAuditReport.model_validate(unknown_semantic)


def test_every_nonzero_failure_metric_has_deterministic_bounded_evidence() -> None:
    report = audit()

    assert report.bounded_examples["efficiency_nonfinite_values"] == ["task:11:row:1"]
    assert report.bounded_examples["duration_nonfinite_values"] == ["task:11:row:1"]
    assert report.bounded_examples["partition_rows_with_non_boolean_value"] == []
    assert report.bounded_examples["partition_rows_not_assigned_exactly_one"] == []

    payload = report.model_dump()
    payload["bounded_examples"]["efficiency_nonfinite_values"] = []
    with pytest.raises(ValidationError, match="requires bounded evidence"):
        LectraAuditReport.model_validate(payload)


def test_partition_and_sheet_failure_categories_have_row_identifiers() -> None:
    frames = trusted_frames()
    frames["tasks"].loc[0, "is_val"] = True
    frames["tasks"]["is_test"] = frames["tasks"]["is_test"].astype(object)
    frames["tasks"].loc[1, "is_test"] = "yes"
    frames["tasks"].loc[2, "sheet_width"] = 0.0
    frames["tasks"].loc[2, "sheet_length"] = 0.0
    frames["tasks"]["sheet_type"] = frames["tasks"]["sheet_type"].astype(object)
    frames["tasks"].loc[2, "sheet_type"] = None

    report = audit_frames(
        frames,
        dataset_id="lectra-test",
        source_checksums={"tasks.gz": "a" * 32},
    )

    assert report.bounded_examples["partition_rows_with_non_boolean_value"] == ["task:11:row:1"]
    assert report.bounded_examples["partition_rows_not_assigned_exactly_one"] == [
        "task:10:row:0",
        "task:11:row:1",
    ]
    assert report.bounded_examples["invalid_sheet_width_tasks"] == ["task:12:row:2"]
    assert report.bounded_examples["invalid_sheet_length_tasks"] == ["task:12:row:2"]
    assert report.bounded_examples["missing_sheet_type_tasks"] == ["task:12:row:2"]


def test_unused_record_counts_are_documented_as_census_without_failure_examples() -> None:
    report = audit()

    assert report.unused_record_counts["task_records_without_part_rows"] == 1
    assert "census" in report.count_semantics["task_records_without_part_rows"]
    assert "census" in report.count_semantics["shape_records_without_part_rows"]
    assert "unused_task_records" not in report.bounded_examples
    assert "unused_shape_records" not in report.bounded_examples


def test_passive_report_rejects_task_numeric_and_recurrence_population_mismatches() -> None:
    numeric_payload = audit().model_dump()
    numeric_payload["efficiency_summary"]["count"] += 1
    numeric_payload["efficiency_summary"]["finite_count"] += 1
    with pytest.raises(ValidationError, match="task numeric summary population"):
        LectraAuditReport.model_validate(numeric_payload)

    task_recurrence_payload = audit().model_dump()
    replacement = NumericSummary.from_values([0, 1]).model_dump()
    task_recurrence_payload["task_part_row_summary"] = replacement
    with pytest.raises(ValidationError, match="task recurrence summary population"):
        LectraAuditReport.model_validate(task_recurrence_payload)

    shape_recurrence_payload = audit().model_dump()
    shape_recurrence_payload["shape_part_row_recurrence_summary"] = replacement
    with pytest.raises(ValidationError, match="shape recurrence summary population"):
        LectraAuditReport.model_validate(shape_recurrence_payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "raw_encoding_total",
        "size_relation_total",
        "subshape_total",
        "constraint_type_total",
        "constraint_parameter_presence_total",
        "constraint_parameter_shape_total",
    ],
)
def test_passive_report_rejects_inventory_totals_inconsistent_with_rows(
    mutation: str,
) -> None:
    payload = audit().model_dump()
    if mutation == "raw_encoding_total":
        payload["raw_encoding_frequency"]["malformed"] += 1
    elif mutation == "size_relation_total":
        payload["size_relation_frequency"]["not_evaluable"] += 1
    elif mutation == "subshape_total":
        payload["source_declared_subshape_count_frequency"]["1"] += 1
    elif mutation == "constraint_type_total":
        payload["constraint_type_frequency"]["opaque-a"] += 1
    elif mutation == "constraint_parameter_presence_total":
        payload["constraint_parameter_presence"]["p1_x"]["present"] += 1
    elif mutation == "constraint_parameter_shape_total":
        payload["constraint_parameter_shape"]["p1_x"]["number"] += 1
    else:  # pragma: no cover - the parametrization is exhaustive
        raise AssertionError(mutation)

    with pytest.raises(ValidationError, match="inventory total"):
        LectraAuditReport.model_validate(payload)


def test_passive_report_rejects_counts_beyond_population_and_stale_evidence() -> None:
    excessive = audit().model_dump()
    excessive["join_failures"]["part_rows_missing_task"] = excessive["table_rows"]["parts"] + 1
    with pytest.raises(ValidationError, match="exceeds its source population"):
        LectraAuditReport.model_validate(excessive)

    stale = audit().model_dump()
    assert stale["duplicate_counts"]["task_key_rows_beyond_first"] == 0
    stale["bounded_examples"]["duplicate_task_keys"] = ["task:10:row:0"]
    with pytest.raises(ValidationError, match="zero count requires empty evidence"):
        LectraAuditReport.model_validate(stale)


@pytest.mark.parametrize(
    ("field", "bad_key"),
    [
        ("raw_encoding_frequency", "unknown_encoding"),
        ("size_relation_frequency", "unknown_relation"),
        ("source_declared_subshape_count_frequency", "01"),
    ],
)
def test_passive_report_rejects_unknown_inventory_keys(field: str, bad_key: str) -> None:
    payload = audit().model_dump()
    first_key = next(iter(payload[field]))
    payload[field][bad_key] = payload[field].pop(first_key)

    with pytest.raises(ValidationError, match="inventory key"):
        LectraAuditReport.model_validate(payload)


def test_passive_report_rejects_unknown_parameter_shape_inventory_key() -> None:
    parameter_payload = audit().model_dump()
    parameter_payload["constraint_parameter_shape"]["p1_x"] = {
        "unknown_shape": parameter_payload["table_rows"]["constraints"]
    }
    with pytest.raises(ValidationError, match="shape inventory key"):
        LectraAuditReport.model_validate(parameter_payload)


def test_literal_frequency_inventory_preserves_missing_reserved_and_typed_values() -> None:
    frames = trusted_frames()
    frames["constraints"]["type"] = pd.Series(
        [None, "<missing>", 1, "1"],
        dtype=object,
    )
    frames["tasks"]["sheet_type"] = pd.Series([None, "<missing>", 1], dtype=object)

    report = audit_frames(
        frames,
        dataset_id="lectra-test",
        source_checksums={"tasks.gz": "a" * 32},
    )

    assert report.constraint_type_frequency == {
        "1": 1,
        "<missing>": 1,
        '<string:"<missing>">': 1,
        "<typed:integer:1>": 1,
    }
    assert report.malformed_counts["constraint_type_rows"] == 1
    assert report.sheet_type_frequency == {
        "<missing>": 1,
        '<string:"<missing>">': 1,
        "<typed:integer:1>": 1,
    }
    assert report.sheet_type_violation_counts == {"task_rows_with_missing_sheet_type": 1}


def test_invalid_dataframe_column_labels_are_reported_before_schema_metrics() -> None:
    frames = trusted_frames()
    task_columns: list[object] = list(frames["tasks"].columns)
    task_columns[0] = 1
    frames["tasks"].columns = task_columns
    frames["parts"].columns = ["tasks_index", "parts_id", "parts_id"]
    shape_columns: list[object] = [1, "1", "sizes"]
    frames["shapes"].columns = shape_columns

    with pytest.raises(LectraInvalidColumnsError) as caught:
        audit_frames(
            frames,
            dataset_id="lectra-test",
            source_checksums={"tasks.gz": "a" * 32},
        )

    assert caught.value.invalid_columns == {
        "tasks": ["column[0] has non-string label of type int: 1"],
        "parts": ["column[2] duplicates string label 'parts_id' from column[1]"],
        "shapes": ["column[0] has non-string label of type int: 1"],
        "constraints": [],
    }
    assert "non-string" in str(caught.value)
    assert "duplicates" in str(caught.value)


def test_constraint_parameter_presence_must_reconcile_with_shape_inventory() -> None:
    missing_mismatch = audit().model_dump()
    missing_mismatch["constraint_parameter_presence"]["p1_x"] = {
        "missing": 1,
        "present": 3,
    }
    with pytest.raises(ValidationError, match="parameter presence and shape inventory"):
        LectraAuditReport.model_validate(missing_mismatch)

    present_mismatch = audit().model_dump()
    present_mismatch["constraint_parameter_shape"]["p1_y"] = {
        "missing": 3,
        "string": 1,
    }
    with pytest.raises(ValidationError, match="parameter presence and shape inventory"):
        LectraAuditReport.model_validate(present_mismatch)


def test_partition_true_frequency_reconciles_when_every_task_has_exact_assignment() -> None:
    incomplete = audit().model_dump()
    assert (
        incomplete["partition_violation_counts"]["task_rows_not_assigned_to_exactly_one_partition"]
        == 0
    )
    incomplete["partition_true_frequency"]["is_train"] = 0
    with pytest.raises(ValidationError, match="partition true total"):
        LectraAuditReport.model_validate(incomplete)

    excessive = audit().model_dump()
    excessive["partition_true_frequency"]["is_train"] = excessive["table_rows"]["tasks"] + 1
    with pytest.raises(ValidationError, match="partition true count exceeds"):
        LectraAuditReport.model_validate(excessive)


def test_sheet_length_sentinel_and_invalid_rows_cannot_exceed_task_population() -> None:
    payload = audit().model_dump()
    task_rows = payload["table_rows"]["tasks"]
    payload["sheet_length_unconstrained_sentinel_count"] = task_rows
    payload["sheet_dimension_violation_counts"]["task_rows_with_invalid_sheet_length"] = 1
    payload["bounded_examples"]["invalid_sheet_length_tasks"] = ["task:10:row:0"]

    with pytest.raises(ValidationError, match="sheet length classifications exceed"):
        LectraAuditReport.model_validate(payload)


def test_direct_malformed_frequency_categories_reconcile_with_counts() -> None:
    raw_payload = audit().model_dump()
    raw_payload["raw_encoding_frequency"]["malformed"] -= 1
    raw_payload["raw_encoding_frequency"]["flat_numeric_even"] += 1
    with pytest.raises(ValidationError, match="malformed raw inventory and count"):
        LectraAuditReport.model_validate(raw_payload)

    type_payload = audit().model_dump()
    type_payload["constraint_type_frequency"]["<missing>"] -= 1
    type_payload["constraint_type_frequency"]["opaque-a"] += 1
    with pytest.raises(ValidationError, match="missing constraint type inventory and count"):
        LectraAuditReport.model_validate(type_payload)
