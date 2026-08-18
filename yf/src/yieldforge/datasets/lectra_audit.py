"""Pure, source-preserving structural audit for trusted Lectra frames.

This module deliberately has no pandas dependency.  The isolated qualifier is
responsible for deserialization and passes already-trusted dataframe-like
objects across this boundary.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from numbers import Integral, Real
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from yieldforge.domain import ContractModel

TABLE_ORDER = ("tasks", "parts", "shapes", "constraints")
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "tasks": (
        "efficiency",
        "duration",
        "sheet_width",
        "sheet_length",
        "sheet_type",
        "tasks_index",
        "is_train",
        "is_val",
        "is_test",
    ),
    "parts": ("tasks_index", "part_id", "shape_hash"),
    "shapes": ("shape_hash", "raw", "sizes"),
    "constraints": (
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
    ),
}
CONSTRAINT_REFERENCE_COLUMNS = ("parts_1", "parts_2")
CONSTRAINT_PARAMETER_COLUMNS = REQUIRED_COLUMNS["constraints"][4:]
PARTITION_COLUMNS = ("is_train", "is_val", "is_test")
MAX_EXAMPLES = 10
NUMERIC_TASK_COLUMNS = ("efficiency", "duration", "sheet_width", "sheet_length")
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]

KEY_FAILURE_KEYS = (
    "task_rows_with_missing_or_invalid_key",
    "part_rows_with_missing_or_invalid_composite_key",
    "shape_rows_with_missing_or_invalid_key",
    "constraint_rows_with_missing_or_invalid_task_key",
)
DUPLICATE_KEYS = (
    "task_key_rows_beyond_first",
    "part_composite_key_rows_beyond_first",
    "shape_key_rows_beyond_first",
)
JOIN_FAILURE_KEYS = (
    "part_rows_missing_task",
    "part_rows_missing_shape",
    "constraint_rows_missing_task",
    "constraint_part_reference_occurrences_missing_part",
)
SHEET_DIMENSION_VIOLATION_KEYS = (
    "task_rows_with_invalid_sheet_width",
    "task_rows_with_invalid_sheet_length",
)
SHEET_TYPE_VIOLATION_KEYS = ("task_rows_with_missing_sheet_type",)
PARTITION_VIOLATION_KEYS = (
    "task_rows_with_non_boolean_partition_value",
    "task_rows_not_assigned_to_exactly_one_partition",
)
MALFORMED_KEYS = (
    "raw_encoding_rows",
    "sizes_rows",
    "constraint_type_rows",
    "constraint_reference_cells",
    "constraint_reference_non_integral_elements",
)
UNUSED_RECORD_KEYS = (
    "task_records_without_part_rows",
    "shape_records_without_part_rows",
)
COUNT_SEMANTIC_KEYS = (
    *DUPLICATE_KEYS,
    *JOIN_FAILURE_KEYS,
    "task_repeated_shape_row_summary",
    "source_declared_subshape_count_frequency",
    "sheet_length_unconstrained_sentinel_count",
    *UNUSED_RECORD_KEYS,
)
NUMERIC_EXAMPLE_KEYS = tuple(
    f"{column}_{classification}_values"
    for column in NUMERIC_TASK_COLUMNS
    for classification in ("missing", "nonfinite", "invalid")
)
BOUNDED_EXAMPLE_KEYS = (
    "duplicate_task_keys",
    "duplicate_part_composite_keys",
    "duplicate_shape_keys",
    "task_rows_with_invalid_keys",
    "part_rows_with_invalid_composite_keys",
    "shape_rows_with_invalid_keys",
    "constraint_rows_with_invalid_task_keys",
    "part_rows_missing_task",
    "part_rows_missing_shape",
    "constraint_rows_missing_task",
    "missing_constraint_part_references",
    "malformed_raw_rows",
    "malformed_sizes_rows",
    "malformed_constraint_reference_cells",
    "non_integral_constraint_reference_elements",
    "missing_constraint_type_rows",
    "partition_rows_with_non_boolean_value",
    "partition_rows_not_assigned_exactly_one",
    "invalid_sheet_width_tasks",
    "invalid_sheet_length_tasks",
    "missing_sheet_type_tasks",
    *NUMERIC_EXAMPLE_KEYS,
)
RAW_ENCODING_KEYS = frozenset({"flat_numeric_even", "flat_numeric_odd", "point_pairs", "malformed"})
SIZE_RELATION_KEYS = frozenset(
    {
        "not_evaluable",
        "pointpairs_sum_sizes_eq_raw_length",
        "twice_sum_sizes_eq_raw_length",
        "sum_sizes_eq_raw_length",
        "no_declared_relation",
    }
)
EXACT_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "table_rows": TABLE_ORDER,
    "columns": TABLE_ORDER,
    "dtypes": TABLE_ORDER,
    "missing_columns": TABLE_ORDER,
    "unexpected_columns": TABLE_ORDER,
    "key_failure_counts": KEY_FAILURE_KEYS,
    "duplicate_counts": DUPLICATE_KEYS,
    "join_failures": JOIN_FAILURE_KEYS,
    "sheet_dimension_violation_counts": SHEET_DIMENSION_VIOLATION_KEYS,
    "sheet_type_violation_counts": SHEET_TYPE_VIOLATION_KEYS,
    "partition_true_frequency": PARTITION_COLUMNS,
    "partition_violation_counts": PARTITION_VIOLATION_KEYS,
    "malformed_counts": MALFORMED_KEYS,
    "unused_record_counts": UNUSED_RECORD_KEYS,
    "count_semantics": COUNT_SEMANTIC_KEYS,
    "bounded_examples": BOUNDED_EXAMPLE_KEYS,
}
FAILURE_EVIDENCE_MAP: dict[tuple[str, str], str] = {
    ("key_failure_counts", KEY_FAILURE_KEYS[0]): "task_rows_with_invalid_keys",
    ("key_failure_counts", KEY_FAILURE_KEYS[1]): "part_rows_with_invalid_composite_keys",
    ("key_failure_counts", KEY_FAILURE_KEYS[2]): "shape_rows_with_invalid_keys",
    ("key_failure_counts", KEY_FAILURE_KEYS[3]): "constraint_rows_with_invalid_task_keys",
    ("duplicate_counts", DUPLICATE_KEYS[0]): "duplicate_task_keys",
    ("duplicate_counts", DUPLICATE_KEYS[1]): "duplicate_part_composite_keys",
    ("duplicate_counts", DUPLICATE_KEYS[2]): "duplicate_shape_keys",
    ("join_failures", JOIN_FAILURE_KEYS[0]): "part_rows_missing_task",
    ("join_failures", JOIN_FAILURE_KEYS[1]): "part_rows_missing_shape",
    ("join_failures", JOIN_FAILURE_KEYS[2]): "constraint_rows_missing_task",
    ("join_failures", JOIN_FAILURE_KEYS[3]): "missing_constraint_part_references",
    ("malformed_counts", MALFORMED_KEYS[0]): "malformed_raw_rows",
    ("malformed_counts", MALFORMED_KEYS[1]): "malformed_sizes_rows",
    ("malformed_counts", MALFORMED_KEYS[2]): "missing_constraint_type_rows",
    ("malformed_counts", MALFORMED_KEYS[3]): "malformed_constraint_reference_cells",
    ("malformed_counts", MALFORMED_KEYS[4]): "non_integral_constraint_reference_elements",
    (
        "partition_violation_counts",
        PARTITION_VIOLATION_KEYS[0],
    ): "partition_rows_with_non_boolean_value",
    (
        "partition_violation_counts",
        PARTITION_VIOLATION_KEYS[1],
    ): "partition_rows_not_assigned_exactly_one",
    (
        "sheet_dimension_violation_counts",
        SHEET_DIMENSION_VIOLATION_KEYS[0],
    ): "invalid_sheet_width_tasks",
    (
        "sheet_dimension_violation_counts",
        SHEET_DIMENSION_VIOLATION_KEYS[1],
    ): "invalid_sheet_length_tasks",
    (
        "sheet_type_violation_counts",
        SHEET_TYPE_VIOLATION_KEYS[0],
    ): "missing_sheet_type_tasks",
}


class LectraMissingColumnsError(ValueError):
    """Raised once with every missing required column, before any metrics run."""

    def __init__(
        self,
        missing_columns: dict[str, list[str]],
        *,
        unexpected_columns: dict[str, list[str]] | None = None,
    ) -> None:
        self.missing_columns = missing_columns
        self.unexpected_columns = unexpected_columns or {table: [] for table in missing_columns}
        detail = json.dumps(
            {
                "missing_columns": self.missing_columns,
                "unexpected_columns": self.unexpected_columns,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        super().__init__(f"Lectra frame schema mismatch: {detail}")


class LectraInvalidColumnsError(ValueError):
    """Raised with all non-string or duplicate column labels before schema work."""

    def __init__(self, invalid_columns: dict[str, list[str]]) -> None:
        self.invalid_columns = invalid_columns
        detail = json.dumps(invalid_columns, sort_keys=True, separators=(",", ":"))
        super().__init__(f"Lectra frames have invalid column labels: {detail}")


class NumericSummary(ContractModel):
    """Finite, JSON-safe descriptive statistics with explicit input accounting."""

    count: NonNegativeInt
    finite_count: NonNegativeInt = 0
    missing_count: NonNegativeInt = 0
    nonfinite_count: NonNegativeInt = 0
    invalid_count: NonNegativeInt = 0
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    p25: float | None = Field(default=None, allow_inf_nan=False)
    median: float | None = Field(default=None, allow_inf_nan=False)
    p75: float | None = Field(default=None, allow_inf_nan=False)
    p95: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    mean: float | None = Field(default=None, allow_inf_nan=False)

    _STAT_FIELDS: ClassVar[tuple[str, ...]] = (
        "minimum",
        "p25",
        "median",
        "p75",
        "p95",
        "maximum",
        "mean",
    )

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        classified = (
            self.finite_count + self.missing_count + self.nonfinite_count + self.invalid_count
        )
        if classified != self.count:
            raise ValueError("numeric summary classifications must sum to count")
        stats = [getattr(self, field) for field in self._STAT_FIELDS]
        if self.finite_count == 0 and any(value is not None for value in stats):
            raise ValueError("empty finite samples cannot have statistics")
        if self.finite_count > 0 and any(value is None for value in stats):
            raise ValueError("finite samples require every statistic")
        return self

    @classmethod
    def from_values(cls, values: Iterable[object]) -> NumericSummary:
        all_values = list(values)
        finite: list[float] = []
        missing_count = 0
        nonfinite_count = 0
        invalid_count = 0
        for value in all_values:
            if _is_missing_scalar(value):
                missing_count += 1
            elif isinstance(value, Real) and not isinstance(value, bool):
                numeric = float(value)
                if math.isfinite(numeric):
                    finite.append(numeric)
                else:
                    nonfinite_count += 1
            else:
                invalid_count += 1

        if not finite:
            return cls(
                count=len(all_values),
                finite_count=0,
                missing_count=missing_count,
                nonfinite_count=nonfinite_count,
                invalid_count=invalid_count,
            )

        finite.sort()
        return cls(
            count=len(all_values),
            finite_count=len(finite),
            missing_count=missing_count,
            nonfinite_count=nonfinite_count,
            invalid_count=invalid_count,
            minimum=finite[0],
            p25=_linear_quantile(finite, 0.25),
            median=_linear_quantile(finite, 0.5),
            p75=_linear_quantile(finite, 0.75),
            p95=_linear_quantile(finite, 0.95),
            maximum=finite[-1],
            mean=math.fsum(value / len(finite) for value in finite),
        )


class LectraAuditReport(ContractModel):
    """Passive evidence inventory for one pinned Lectra release."""

    schema_version: Literal["yieldforge.lectra-audit.v1"] = "yieldforge.lectra-audit.v1"
    dataset_id: str = Field(min_length=1)
    source_checksums: dict[str, str]
    source_unit_label: Literal["m^-4"] = "m^-4"
    table_rows: dict[str, NonNegativeInt]
    columns: dict[str, list[str]]
    dtypes: dict[str, dict[str, str]]
    missing_columns: dict[str, list[str]]
    unexpected_columns: dict[str, list[str]]
    key_failure_counts: dict[str, NonNegativeInt]
    duplicate_counts: dict[str, NonNegativeInt]
    join_failures: dict[str, NonNegativeInt]
    task_part_row_summary: NumericSummary
    task_unique_shape_summary: NumericSummary
    task_repeated_shape_row_summary: NumericSummary
    shape_part_row_recurrence_summary: NumericSummary
    shape_distinct_task_recurrence_summary: NumericSummary
    efficiency_summary: NumericSummary
    duration_summary: NumericSummary
    sheet_width_summary: NumericSummary
    sheet_length_summary: NumericSummary
    sheet_length_unconstrained_sentinel_count: NonNegativeInt
    sheet_dimension_violation_counts: dict[str, NonNegativeInt]
    sheet_type_frequency: dict[str, NonNegativeInt]
    sheet_type_violation_counts: dict[str, NonNegativeInt]
    partition_true_frequency: dict[str, NonNegativeInt]
    partition_violation_counts: dict[str, NonNegativeInt]
    raw_encoding_frequency: dict[str, NonNegativeInt]
    size_relation_frequency: dict[str, NonNegativeInt]
    source_declared_subshape_count_frequency: dict[str, NonNegativeInt]
    constraint_type_frequency: dict[str, NonNegativeInt]
    constraint_parameter_presence: dict[str, dict[str, NonNegativeInt]]
    constraint_parameter_shape: dict[str, dict[str, NonNegativeInt]]
    malformed_counts: dict[str, NonNegativeInt]
    unused_record_counts: dict[str, NonNegativeInt]
    count_semantics: dict[str, str]
    bounded_examples: dict[str, list[str]]

    @field_validator(*EXACT_FIELD_KEYS)
    @classmethod
    def require_exact_internal_keys(
        cls, value: dict[str, object], info: ValidationInfo
    ) -> dict[str, object]:
        expected = set(EXACT_FIELD_KEYS[info.field_name])
        actual = set(value)
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ValueError(
                f"{info.field_name} must contain exact metric keys; "
                f"missing={missing}, unknown={unknown}"
            )
        return value

    @field_validator("constraint_parameter_presence", "constraint_parameter_shape")
    @classmethod
    def require_exact_constraint_parameter_keys(
        cls,
        value: dict[str, dict[str, NonNegativeInt]],
        info: ValidationInfo,
    ) -> dict[str, dict[str, NonNegativeInt]]:
        expected = set(CONSTRAINT_PARAMETER_COLUMNS)
        actual = set(value)
        if actual != expected:
            raise ValueError(f"{info.field_name} must contain exact parameter keys")
        if info.field_name == "constraint_parameter_presence":
            for column, inventory in value.items():
                if set(inventory) != {"missing", "present"}:
                    raise ValueError(
                        f"constraint_parameter_presence[{column}] must contain "
                        "exact metric keys missing and present"
                    )
        return value

    @field_validator("raw_encoding_frequency")
    @classmethod
    def require_known_raw_encoding_keys(
        cls, value: dict[str, NonNegativeInt]
    ) -> dict[str, NonNegativeInt]:
        if not set(value).issubset(RAW_ENCODING_KEYS):
            raise ValueError("raw encoding inventory key is not recognized")
        return value

    @field_validator("size_relation_frequency")
    @classmethod
    def require_known_size_relation_keys(
        cls, value: dict[str, NonNegativeInt]
    ) -> dict[str, NonNegativeInt]:
        if not set(value).issubset(SIZE_RELATION_KEYS):
            raise ValueError("size relation inventory key is not recognized")
        return value

    @field_validator("source_declared_subshape_count_frequency")
    @classmethod
    def require_canonical_subshape_count_keys(
        cls, value: dict[str, NonNegativeInt]
    ) -> dict[str, NonNegativeInt]:
        for key in value:
            try:
                count = int(key)
            except ValueError as error:
                raise ValueError("subshape inventory key must be a positive integer") from error
            if count < 1 or str(count) != key:
                raise ValueError("subshape inventory key must be a canonical positive integer")
        return value

    @field_validator("constraint_parameter_shape")
    @classmethod
    def require_known_parameter_shape_keys(
        cls, value: dict[str, dict[str, NonNegativeInt]]
    ) -> dict[str, dict[str, NonNegativeInt]]:
        for inventory in value.values():
            if any(not _is_parameter_shape_key(key) for key in inventory):
                raise ValueError("constraint parameter shape inventory key is not recognized")
        return value

    @field_validator("count_semantics")
    @classmethod
    def require_nonempty_count_semantics(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not description.strip() for description in value.values()):
            raise ValueError("count semantics must be nonempty")
        return value

    @field_validator("bounded_examples")
    @classmethod
    def require_deterministic_bounded_examples(
        cls, value: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        for category, examples in value.items():
            if len(examples) > MAX_EXAMPLES:
                raise ValueError(f"{category} must contain at most {MAX_EXAMPLES} examples")
            if examples != sorted(set(examples)):
                raise ValueError(f"{category} examples must be sorted and unique")
            if any(not example for example in examples):
                raise ValueError(f"{category} examples must be nonempty identifiers")
        return value

    @model_validator(mode="after")
    def require_evidence_for_nonzero_failures(self) -> Self:
        for (field, metric), example_category in FAILURE_EVIDENCE_MAP.items():
            count = getattr(self, field)[metric]
            has_evidence = bool(self.bounded_examples[example_category])
            if count > 0 and not has_evidence:
                raise ValueError(
                    f"nonzero {field}.{metric} requires bounded evidence in {example_category}"
                )
            if count == 0 and has_evidence:
                raise ValueError(f"zero count requires empty evidence in {example_category}")
        for column in NUMERIC_TASK_COLUMNS:
            summary: NumericSummary = getattr(self, f"{column}_summary")
            for classification in ("missing", "nonfinite", "invalid"):
                count = getattr(summary, f"{classification}_count")
                example_category = f"{column}_{classification}_values"
                has_evidence = bool(self.bounded_examples[example_category])
                if count > 0 and not has_evidence:
                    raise ValueError(
                        f"nonzero {column}_summary.{classification}_count requires "
                        f"bounded evidence in {example_category}"
                    )
                if count == 0 and has_evidence:
                    raise ValueError(f"zero count requires empty evidence in {example_category}")
        return self

    @model_validator(mode="after")
    def require_cross_field_consistency(self) -> Self:
        task_rows = self.table_rows["tasks"]
        part_rows = self.table_rows["parts"]
        shape_rows = self.table_rows["shapes"]
        constraint_rows = self.table_rows["constraints"]

        for table in TABLE_ORDER:
            observed = self.columns[table]
            if len(observed) != len(set(observed)):
                raise ValueError(f"{table} column inventory must be unique")
            expected_missing = sorted(set(REQUIRED_COLUMNS[table]) - set(observed))
            expected_unexpected = sorted(set(observed) - set(REQUIRED_COLUMNS[table]))
            if self.missing_columns[table] != expected_missing:
                raise ValueError(f"{table} missing-column inventory is inconsistent")
            if expected_missing:
                raise ValueError("complete audit reports cannot contain missing required columns")
            if self.unexpected_columns[table] != expected_unexpected:
                raise ValueError(f"{table} unexpected-column inventory is inconsistent")
            if set(self.dtypes[table]) != set(observed):
                raise ValueError(f"{table} dtype inventory must match observed columns")

        for column in NUMERIC_TASK_COLUMNS:
            if getattr(self, f"{column}_summary").count != task_rows:
                raise ValueError("task numeric summary population must equal task rows")

        valid_unique_tasks = _valid_unique_population(
            row_count=task_rows,
            invalid_count=self.key_failure_counts[KEY_FAILURE_KEYS[0]],
            duplicate_count=self.duplicate_counts[DUPLICATE_KEYS[0]],
            subject="task",
        )
        for field in (
            "task_part_row_summary",
            "task_unique_shape_summary",
            "task_repeated_shape_row_summary",
        ):
            summary = getattr(self, field)
            _require_count_population_summary(summary, field)
            if summary.count != valid_unique_tasks:
                raise ValueError("task recurrence summary population is inconsistent")

        valid_unique_shapes = _valid_unique_population(
            row_count=shape_rows,
            invalid_count=self.key_failure_counts[KEY_FAILURE_KEYS[2]],
            duplicate_count=self.duplicate_counts[DUPLICATE_KEYS[2]],
            subject="shape",
        )
        for field in (
            "shape_part_row_recurrence_summary",
            "shape_distinct_task_recurrence_summary",
        ):
            summary = getattr(self, field)
            _require_count_population_summary(summary, field)
            if summary.count != valid_unique_shapes:
                raise ValueError("shape recurrence summary population is inconsistent")

        _require_inventory_total(self.sheet_type_frequency, task_rows, "sheet type inventory total")
        _require_inventory_total(
            self.raw_encoding_frequency, shape_rows, "raw encoding inventory total"
        )
        _require_inventory_total(
            self.size_relation_frequency, shape_rows, "size relation inventory total"
        )
        if (
            sum(self.source_declared_subshape_count_frequency.values())
            + self.malformed_counts["sizes_rows"]
            != shape_rows
        ):
            raise ValueError("subshape inventory total is inconsistent with shape rows")
        _require_inventory_total(
            self.constraint_type_frequency,
            constraint_rows,
            "constraint type inventory total",
        )
        for column in CONSTRAINT_PARAMETER_COLUMNS:
            presence_inventory = self.constraint_parameter_presence[column]
            shape_inventory = self.constraint_parameter_shape[column]
            _require_inventory_total(
                presence_inventory,
                constraint_rows,
                "constraint parameter presence inventory total",
            )
            _require_inventory_total(
                shape_inventory,
                constraint_rows,
                "constraint parameter shape inventory total",
            )
            shape_missing = shape_inventory.get("missing", 0)
            shape_present = sum(
                count for shape, count in shape_inventory.items() if shape != "missing"
            )
            if (
                presence_inventory["missing"] != shape_missing
                or presence_inventory["present"] != shape_present
            ):
                raise ValueError(
                    f"constraint parameter presence and shape inventory disagree for {column}"
                )

        if (
            self.raw_encoding_frequency.get("malformed", 0)
            != self.malformed_counts["raw_encoding_rows"]
        ):
            raise ValueError("malformed raw inventory and count must agree")
        if (
            self.constraint_type_frequency.get("<missing>", 0)
            != self.malformed_counts["constraint_type_rows"]
        ):
            raise ValueError("missing constraint type inventory and count must agree")
        if (
            self.sheet_type_frequency.get("<missing>", 0)
            != self.sheet_type_violation_counts["task_rows_with_missing_sheet_type"]
        ):
            raise ValueError("missing sheet type inventory and count must agree")
        if self.malformed_counts["sizes_rows"] > self.size_relation_frequency.get(
            "not_evaluable", 0
        ):
            raise ValueError("malformed sizes must be represented as not evaluable")
        if self.key_failure_counts[KEY_FAILURE_KEYS[3]] > self.join_failures[JOIN_FAILURE_KEYS[2]]:
            raise ValueError("invalid constraint task keys must also fail the task join")
        if (
            self.partition_violation_counts[PARTITION_VIOLATION_KEYS[0]]
            > (self.partition_violation_counts[PARTITION_VIOLATION_KEYS[1]])
        ):
            raise ValueError("non-boolean partition rows must also violate assignment")

        population_limits = (
            (self.key_failure_counts, KEY_FAILURE_KEYS[0], task_rows),
            (self.key_failure_counts, KEY_FAILURE_KEYS[1], part_rows),
            (self.key_failure_counts, KEY_FAILURE_KEYS[2], shape_rows),
            (self.key_failure_counts, KEY_FAILURE_KEYS[3], constraint_rows),
            (self.duplicate_counts, DUPLICATE_KEYS[0], task_rows),
            (self.duplicate_counts, DUPLICATE_KEYS[1], part_rows),
            (self.duplicate_counts, DUPLICATE_KEYS[2], shape_rows),
            (self.join_failures, JOIN_FAILURE_KEYS[0], part_rows),
            (self.join_failures, JOIN_FAILURE_KEYS[1], part_rows),
            (self.join_failures, JOIN_FAILURE_KEYS[2], constraint_rows),
            (self.malformed_counts, MALFORMED_KEYS[0], shape_rows),
            (self.malformed_counts, MALFORMED_KEYS[1], shape_rows),
            (self.malformed_counts, MALFORMED_KEYS[2], constraint_rows),
            (self.malformed_counts, MALFORMED_KEYS[3], constraint_rows * 2),
            (self.unused_record_counts, UNUSED_RECORD_KEYS[0], task_rows),
            (self.unused_record_counts, UNUSED_RECORD_KEYS[1], shape_rows),
            (
                self.sheet_dimension_violation_counts,
                SHEET_DIMENSION_VIOLATION_KEYS[0],
                task_rows,
            ),
            (
                self.sheet_dimension_violation_counts,
                SHEET_DIMENSION_VIOLATION_KEYS[1],
                task_rows,
            ),
            (
                self.sheet_type_violation_counts,
                SHEET_TYPE_VIOLATION_KEYS[0],
                task_rows,
            ),
            (
                self.partition_violation_counts,
                PARTITION_VIOLATION_KEYS[0],
                task_rows,
            ),
            (
                self.partition_violation_counts,
                PARTITION_VIOLATION_KEYS[1],
                task_rows,
            ),
        )
        for metrics, key, population in population_limits:
            if metrics[key] > population:
                raise ValueError(f"{key} exceeds its source population")

        if self.sheet_length_unconstrained_sentinel_count > task_rows:
            raise ValueError("sheet length sentinel count exceeds its source population")
        if any(count > task_rows for count in self.partition_true_frequency.values()):
            raise ValueError("partition true count exceeds its source population")
        if (
            self.partition_violation_counts[PARTITION_VIOLATION_KEYS[1]] == 0
            and sum(self.partition_true_frequency.values()) != task_rows
        ):
            raise ValueError(
                "partition true total must equal task rows without assignment violations"
            )
        if (
            self.sheet_length_unconstrained_sentinel_count
            + self.sheet_dimension_violation_counts[SHEET_DIMENSION_VIOLATION_KEYS[1]]
            > task_rows
        ):
            raise ValueError("sheet length classifications exceed the task population")
        if self.unused_record_counts[UNUSED_RECORD_KEYS[0]] > (
            task_rows - self.key_failure_counts[KEY_FAILURE_KEYS[0]]
        ):
            raise ValueError("unused task census exceeds valid task rows")
        if self.unused_record_counts[UNUSED_RECORD_KEYS[1]] > (
            shape_rows - self.key_failure_counts[KEY_FAILURE_KEYS[2]]
        ):
            raise ValueError("unused shape census exceeds valid shape rows")

        _valid_unique_population(
            row_count=part_rows,
            invalid_count=self.key_failure_counts[KEY_FAILURE_KEYS[1]],
            duplicate_count=self.duplicate_counts[DUPLICATE_KEYS[1]],
            subject="part",
        )
        return self


class _BoundedExamples:
    """Keep the lexicographically first distinct examples without unbounded storage."""

    def __init__(self, categories: Iterable[str]) -> None:
        self._values = {category: [] for category in categories}

    def add(self, category: str, value: str) -> None:
        examples = self._values.setdefault(category, [])
        if value in examples:
            return
        examples.append(value)
        examples.sort()
        if len(examples) > MAX_EXAMPLES:
            examples.pop()

    def result(self) -> dict[str, list[str]]:
        return {key: list(values) for key, values in sorted(self._values.items())}


def validate_frame_schema(table_name: str, frame: Any) -> list[str]:
    """Validate one trusted frame's observed labels and required source columns.

    The return value preserves source column order for callers that need to
    inventory dtypes or unexpected columns without retaining any other frame.
    """

    if table_name not in REQUIRED_COLUMNS:
        raise ValueError(f"unknown Lectra table: {table_name!r}")

    invalid_columns = {table_name: _column_label_issues(frame)}
    if invalid_columns[table_name]:
        raise LectraInvalidColumnsError(invalid_columns)

    observed_columns = _frame_columns(frame)
    missing_columns = sorted(set(REQUIRED_COLUMNS[table_name]) - set(observed_columns))
    unexpected_columns = sorted(set(observed_columns) - set(REQUIRED_COLUMNS[table_name]))
    if missing_columns:
        raise LectraMissingColumnsError(
            {table_name: missing_columns},
            unexpected_columns={table_name: unexpected_columns},
        )

    return observed_columns


def audit_frames(
    frames: Mapping[str, Any],
    *,
    dataset_id: str,
    source_checksums: dict[str, str],
) -> LectraAuditReport:
    """Audit four already-trusted dataframe-like objects without source inference."""

    invalid_columns = {table: [] for table in TABLE_ORDER}
    observed_columns: dict[str, list[str]] = {table: [] for table in TABLE_ORDER}
    missing_columns: dict[str, list[str]] = {table: [] for table in TABLE_ORDER}
    unexpected_columns: dict[str, list[str]] = {table: [] for table in TABLE_ORDER}

    for table in TABLE_ORDER:
        if table not in frames:
            missing_columns[table] = sorted(REQUIRED_COLUMNS[table])
            continue
        try:
            observed_columns[table] = validate_frame_schema(table, frames[table])
        except LectraInvalidColumnsError as error:
            invalid_columns[table] = error.invalid_columns[table]
        except LectraMissingColumnsError as error:
            observed_columns[table] = _frame_columns(frames[table])
            missing_columns[table] = error.missing_columns[table]
            unexpected_columns[table] = error.unexpected_columns[table]

    if any(invalid_columns.values()):
        raise LectraInvalidColumnsError(invalid_columns)

    if any(missing_columns.values()):
        for table in TABLE_ORDER:
            if table in frames and not unexpected_columns[table]:
                unexpected_columns[table] = sorted(
                    set(observed_columns[table]) - set(REQUIRED_COLUMNS[table])
                )
        raise LectraMissingColumnsError(
            missing_columns,
            unexpected_columns=unexpected_columns,
        )

    table_rows = {table: len(frames[table]) for table in TABLE_ORDER}
    dtypes = {table: _frame_dtypes(frames[table], observed_columns[table]) for table in TABLE_ORDER}
    unexpected_columns = {
        table: sorted(set(observed_columns[table]) - set(REQUIRED_COLUMNS[table]))
        for table in TABLE_ORDER
    }
    examples = _BoundedExamples(BOUNDED_EXAMPLE_KEYS)

    task_data = _audit_tasks(frames["tasks"], examples)
    shape_data = _audit_shapes(frames["shapes"], examples)
    part_data = _audit_parts(frames["parts"], task_data["keys"], shape_data["keys"], examples)
    constraint_data = _audit_constraints(
        frames["constraints"], task_data["keys"], part_data["keys"], examples
    )

    task_part_counts: Counter[object] = part_data["task_part_counts"]
    task_shape_pairs: set[tuple[object, object]] = part_data["task_shape_pairs"]
    task_unique_shape_counts: Counter[object] = Counter(task for task, _ in task_shape_pairs)
    declared_task_keys: set[object] = task_data["keys"]
    declared_shape_keys: set[object] = shape_data["keys"]
    task_part_values = [task_part_counts[task] for task in declared_task_keys]
    task_unique_shape_values = [task_unique_shape_counts[task] for task in declared_task_keys]
    task_repeated_shape_values = [
        task_part_counts[task] - task_unique_shape_counts[task] for task in declared_task_keys
    ]

    shape_part_counts: Counter[object] = part_data["shape_part_counts"]
    shape_distinct_task_counts: Counter[object] = Counter(shape for _, shape in task_shape_pairs)
    shape_part_values = [shape_part_counts[shape] for shape in declared_shape_keys]
    shape_task_values = [shape_distinct_task_counts[shape] for shape in declared_shape_keys]

    key_failure_counts = {
        "task_rows_with_missing_or_invalid_key": task_data["invalid_key_rows"],
        "part_rows_with_missing_or_invalid_composite_key": part_data["invalid_key_rows"],
        "shape_rows_with_missing_or_invalid_key": shape_data["invalid_key_rows"],
        "constraint_rows_with_missing_or_invalid_task_key": constraint_data[
            "invalid_task_key_rows"
        ],
    }
    duplicate_counts = {
        "task_key_rows_beyond_first": task_data["duplicate_rows"],
        "part_composite_key_rows_beyond_first": part_data["duplicate_rows"],
        "shape_key_rows_beyond_first": shape_data["duplicate_rows"],
    }
    join_failures = {
        "part_rows_missing_task": part_data["missing_task_rows"],
        "part_rows_missing_shape": part_data["missing_shape_rows"],
        "constraint_rows_missing_task": constraint_data["missing_task_rows"],
        "constraint_part_reference_occurrences_missing_part": constraint_data[
            "missing_part_reference_occurrences"
        ],
    }
    malformed_counts = {
        "raw_encoding_rows": shape_data["malformed_raw_rows"],
        "sizes_rows": shape_data["malformed_sizes_rows"],
        "constraint_type_rows": constraint_data["missing_type_rows"],
        "constraint_reference_cells": constraint_data["malformed_reference_cells"],
        "constraint_reference_non_integral_elements": constraint_data[
            "non_integral_reference_elements"
        ],
    }
    unused_record_counts = {
        "task_records_without_part_rows": sum(
            1 for task in task_data["row_keys"] if task not in part_data["referenced_tasks"]
        ),
        "shape_records_without_part_rows": sum(
            1 for shape in shape_data["row_keys"] if shape not in part_data["referenced_shapes"]
        ),
    }

    return LectraAuditReport(
        dataset_id=dataset_id,
        source_checksums=dict(sorted(source_checksums.items())),
        table_rows=table_rows,
        columns=observed_columns,
        dtypes=dtypes,
        missing_columns=missing_columns,
        unexpected_columns=unexpected_columns,
        key_failure_counts=key_failure_counts,
        duplicate_counts=duplicate_counts,
        join_failures=join_failures,
        task_part_row_summary=NumericSummary.from_values(task_part_values),
        task_unique_shape_summary=NumericSummary.from_values(task_unique_shape_values),
        task_repeated_shape_row_summary=NumericSummary.from_values(task_repeated_shape_values),
        shape_part_row_recurrence_summary=NumericSummary.from_values(shape_part_values),
        shape_distinct_task_recurrence_summary=NumericSummary.from_values(shape_task_values),
        efficiency_summary=task_data["efficiency_summary"],
        duration_summary=task_data["duration_summary"],
        sheet_width_summary=task_data["sheet_width_summary"],
        sheet_length_summary=task_data["sheet_length_summary"],
        sheet_length_unconstrained_sentinel_count=task_data[
            "sheet_length_unconstrained_sentinel_count"
        ],
        sheet_dimension_violation_counts=task_data["sheet_dimension_violation_counts"],
        sheet_type_frequency=task_data["sheet_type_frequency"],
        sheet_type_violation_counts=task_data["sheet_type_violation_counts"],
        partition_true_frequency=task_data["partition_true_frequency"],
        partition_violation_counts=task_data["partition_violation_counts"],
        raw_encoding_frequency=shape_data["raw_encoding_frequency"],
        size_relation_frequency=shape_data["size_relation_frequency"],
        source_declared_subshape_count_frequency=shape_data[
            "source_declared_subshape_count_frequency"
        ],
        constraint_type_frequency=constraint_data["type_frequency"],
        constraint_parameter_presence=constraint_data["parameter_presence"],
        constraint_parameter_shape=constraint_data["parameter_shape"],
        malformed_counts=malformed_counts,
        unused_record_counts=unused_record_counts,
        count_semantics=_count_semantics(),
        bounded_examples=examples.result(),
    )


def report_to_json(report: LectraAuditReport, *, indent: int | None = 2) -> str:
    """Serialize a report deterministically and reject non-finite JSON numbers."""

    return json.dumps(
        report.model_dump(mode="json"),
        allow_nan=False,
        indent=indent,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
    )


def _audit_tasks(frame: Any, examples: _BoundedExamples) -> dict[str, Any]:
    keys: set[object] = set()
    row_keys: list[object] = []
    duplicate_rows = 0
    invalid_key_rows = 0
    numeric_values: dict[str, list[object]] = {column: [] for column in NUMERIC_TASK_COLUMNS}
    sheet_type_frequency: Counter[str] = Counter()
    partition_true_frequency: Counter[str] = Counter({column: 0 for column in PARTITION_COLUMNS})
    non_boolean_partition_rows = 0
    assignment_violation_rows = 0
    invalid_width_rows = 0
    invalid_length_rows = 0
    unconstrained_length_rows = 0
    missing_sheet_type_rows = 0

    columns = (
        "tasks_index",
        "efficiency",
        "duration",
        "sheet_width",
        "sheet_length",
        "sheet_type",
        *PARTITION_COLUMNS,
    )
    for row_number, row in enumerate(_iter_rows(frame, columns)):
        (
            task,
            efficiency,
            duration,
            sheet_width,
            sheet_length,
            sheet_type,
            *partitions,
        ) = row
        valid_task = _valid_key(task)
        task_example = _task_row_identifier(task, row_number)
        if not valid_task:
            invalid_key_rows += 1
            examples.add("task_rows_with_invalid_keys", task_example)
        else:
            row_keys.append(task)
            if task in keys:
                duplicate_rows += 1
                examples.add("duplicate_task_keys", task_example)
            keys.add(task)

        for column, value in zip(
            NUMERIC_TASK_COLUMNS,
            (efficiency, duration, sheet_width, sheet_length),
            strict=True,
        ):
            numeric_values[column].append(value)
            _record_numeric_anomaly(column, value, task_example, examples)
        sheet_type_frequency[_literal_frequency_key(sheet_type)] += 1
        if _is_null(sheet_type):
            missing_sheet_type_rows += 1
            examples.add("missing_sheet_type_tasks", task_example)

        valid_partition_values = [_boolean_value(value) for value in partitions]
        row_has_non_boolean = any(value is None for value in valid_partition_values)
        if row_has_non_boolean:
            non_boolean_partition_rows += 1
            examples.add("partition_rows_with_non_boolean_value", task_example)
        for column, value in zip(PARTITION_COLUMNS, valid_partition_values, strict=True):
            if value is True:
                partition_true_frequency[column] += 1
        if row_has_non_boolean or sum(value is True for value in valid_partition_values) != 1:
            assignment_violation_rows += 1
            examples.add("partition_rows_not_assigned_exactly_one", task_example)

        if not _is_positive_finite(sheet_width):
            invalid_width_rows += 1
            examples.add("invalid_sheet_width_tasks", task_example)
        if _is_exact_minus_one(sheet_length):
            unconstrained_length_rows += 1
        elif not _is_positive_finite(sheet_length):
            invalid_length_rows += 1
            examples.add("invalid_sheet_length_tasks", task_example)

    return {
        "keys": keys,
        "row_keys": row_keys,
        "duplicate_rows": duplicate_rows,
        "invalid_key_rows": invalid_key_rows,
        "efficiency_summary": NumericSummary.from_values(numeric_values["efficiency"]),
        "duration_summary": NumericSummary.from_values(numeric_values["duration"]),
        "sheet_width_summary": NumericSummary.from_values(numeric_values["sheet_width"]),
        "sheet_length_summary": NumericSummary.from_values(numeric_values["sheet_length"]),
        "sheet_length_unconstrained_sentinel_count": unconstrained_length_rows,
        "sheet_dimension_violation_counts": {
            "task_rows_with_invalid_sheet_width": invalid_width_rows,
            "task_rows_with_invalid_sheet_length": invalid_length_rows,
        },
        "sheet_type_frequency": _sorted_counter(sheet_type_frequency),
        "sheet_type_violation_counts": {
            "task_rows_with_missing_sheet_type": missing_sheet_type_rows,
        },
        "partition_true_frequency": _sorted_counter(partition_true_frequency),
        "partition_violation_counts": {
            "task_rows_with_non_boolean_partition_value": non_boolean_partition_rows,
            "task_rows_not_assigned_to_exactly_one_partition": assignment_violation_rows,
        },
    }


def _audit_shapes(frame: Any, examples: _BoundedExamples) -> dict[str, Any]:
    keys: set[object] = set()
    row_keys: list[object] = []
    duplicate_rows = 0
    invalid_key_rows = 0
    malformed_raw_rows = 0
    malformed_sizes_rows = 0
    raw_encoding_frequency: Counter[str] = Counter()
    size_relation_frequency: Counter[str] = Counter()
    subshape_count_frequency: Counter[str] = Counter()

    for row_number, (shape, raw, sizes) in enumerate(
        _iter_rows(frame, ("shape_hash", "raw", "sizes"))
    ):
        valid_shape = _valid_key(shape)
        shape_example = _display(shape) if valid_shape else f"row:{row_number}"
        if not valid_shape:
            invalid_key_rows += 1
            examples.add("shape_rows_with_invalid_keys", shape_example)
        else:
            row_keys.append(shape)
            if shape in keys:
                duplicate_rows += 1
                examples.add("duplicate_shape_keys", _display(shape))
            keys.add(shape)

        encoding, raw_length = _raw_encoding(raw)
        raw_encoding_frequency[encoding] += 1
        if encoding == "malformed":
            malformed_raw_rows += 1
            examples.add("malformed_raw_rows", shape_example)

        declared_sizes = _positive_integer_sequence(sizes)
        if declared_sizes is None:
            malformed_sizes_rows += 1
            examples.add("malformed_sizes_rows", shape_example)
            size_relation_frequency["not_evaluable"] += 1
            continue

        subshape_count_frequency[str(len(declared_sizes))] += 1
        size_relation_frequency[_size_relation(encoding, raw_length, declared_sizes)] += 1

    return {
        "keys": keys,
        "row_keys": row_keys,
        "duplicate_rows": duplicate_rows,
        "invalid_key_rows": invalid_key_rows,
        "malformed_raw_rows": malformed_raw_rows,
        "malformed_sizes_rows": malformed_sizes_rows,
        "raw_encoding_frequency": _sorted_counter(raw_encoding_frequency),
        "size_relation_frequency": _sorted_counter(size_relation_frequency),
        "source_declared_subshape_count_frequency": _sorted_counter(subshape_count_frequency),
    }


def _audit_parts(
    frame: Any,
    task_keys: set[object],
    shape_keys: set[object],
    examples: _BoundedExamples,
) -> dict[str, Any]:
    keys: set[tuple[object, int]] = set()
    duplicate_rows = 0
    invalid_key_rows = 0
    missing_task_rows = 0
    missing_shape_rows = 0
    task_part_counts: Counter[object] = Counter()
    shape_part_counts: Counter[object] = Counter()
    task_shape_pairs: set[tuple[object, object]] = set()
    referenced_tasks: set[object] = set()
    referenced_shapes: set[object] = set()

    for row_number, (task, part, shape) in enumerate(
        _iter_rows(frame, ("tasks_index", "part_id", "shape_hash"))
    ):
        valid_composite = _valid_key(task) and _is_integral(part)
        composite = (task, int(part)) if valid_composite else None
        row_example = _display_composite(task, part) if valid_composite else f"row:{row_number}"
        if not valid_composite:
            invalid_key_rows += 1
            examples.add("part_rows_with_invalid_composite_keys", row_example)
        else:
            if composite in keys:
                duplicate_rows += 1
                examples.add("duplicate_part_composite_keys", row_example)
            keys.add(composite)

        task_exists = _valid_key(task) and task in task_keys
        if not task_exists:
            missing_task_rows += 1
            examples.add("part_rows_missing_task", row_example)
        else:
            referenced_tasks.add(task)
            task_part_counts[task] += 1

        if not _valid_key(shape) or shape not in shape_keys:
            missing_shape_rows += 1
            examples.add("part_rows_missing_shape", row_example)
        else:
            referenced_shapes.add(shape)
            shape_part_counts[shape] += 1

        if task_exists and _valid_key(shape):
            task_shape_pairs.add((task, shape))

    return {
        "keys": keys,
        "duplicate_rows": duplicate_rows,
        "invalid_key_rows": invalid_key_rows,
        "missing_task_rows": missing_task_rows,
        "missing_shape_rows": missing_shape_rows,
        "task_part_counts": task_part_counts,
        "shape_part_counts": shape_part_counts,
        "task_shape_pairs": task_shape_pairs,
        "referenced_tasks": referenced_tasks,
        "referenced_shapes": referenced_shapes,
    }


def _audit_constraints(
    frame: Any,
    task_keys: set[object],
    part_keys: set[tuple[object, int]],
    examples: _BoundedExamples,
) -> dict[str, Any]:
    type_frequency: Counter[str] = Counter()
    parameter_presence = {
        column: Counter({"missing": 0, "present": 0}) for column in CONSTRAINT_PARAMETER_COLUMNS
    }
    parameter_shape = {column: Counter() for column in CONSTRAINT_PARAMETER_COLUMNS}
    missing_task_rows = 0
    invalid_task_key_rows = 0
    missing_type_rows = 0
    malformed_reference_cells = 0
    non_integral_reference_elements = 0
    missing_part_reference_occurrences = 0
    columns = (
        "type",
        "tasks_index",
        *CONSTRAINT_REFERENCE_COLUMNS,
        *CONSTRAINT_PARAMETER_COLUMNS,
    )

    for row_number, row in enumerate(_iter_rows(frame, columns)):
        constraint_type, task, *remaining = row
        references = remaining[: len(CONSTRAINT_REFERENCE_COLUMNS)]
        parameters = remaining[len(CONSTRAINT_REFERENCE_COLUMNS) :]
        task_example = _task_row_identifier(task, row_number)
        missing_constraint_type = _is_null(constraint_type)
        type_key = _literal_frequency_key(constraint_type)
        type_frequency[type_key] += 1
        if missing_constraint_type:
            missing_type_rows += 1
            examples.add("missing_constraint_type_rows", task_example)
        valid_task = _valid_key(task)
        if not valid_task:
            invalid_task_key_rows += 1
            examples.add("constraint_rows_with_invalid_task_keys", task_example)
        if not valid_task or task not in task_keys:
            missing_task_rows += 1
            examples.add("constraint_rows_missing_task", task_example)

        for column, value in zip(CONSTRAINT_PARAMETER_COLUMNS, parameters, strict=True):
            missing = _is_null(value)
            parameter_presence[column]["missing" if missing else "present"] += 1
            parameter_shape[column][_value_shape(value)] += 1

        for column, value in zip(CONSTRAINT_REFERENCE_COLUMNS, references, strict=True):
            if _is_null(value):
                continue
            sequence = _as_sequence(value)
            if sequence is None:
                malformed_reference_cells += 1
                examples.add(
                    "malformed_constraint_reference_cells",
                    f"{task_example}:{column}",
                )
                continue
            for referenced_part in sequence:
                if not _is_integral(referenced_part):
                    non_integral_reference_elements += 1
                    examples.add(
                        "non_integral_constraint_reference_elements",
                        f"{task_example}:{column}:{_display(referenced_part)}",
                    )
                    continue
                composite = (task, int(referenced_part)) if valid_task else None
                if composite is None or composite not in part_keys:
                    missing_part_reference_occurrences += 1
                    examples.add(
                        "missing_constraint_part_references",
                        _display_composite(task, referenced_part),
                    )

    return {
        "missing_task_rows": missing_task_rows,
        "invalid_task_key_rows": invalid_task_key_rows,
        "missing_type_rows": missing_type_rows,
        "malformed_reference_cells": malformed_reference_cells,
        "non_integral_reference_elements": non_integral_reference_elements,
        "missing_part_reference_occurrences": missing_part_reference_occurrences,
        "type_frequency": _sorted_counter(type_frequency),
        "parameter_presence": {
            column: _sorted_counter(counts) for column, counts in parameter_presence.items()
        },
        "parameter_shape": {
            column: _sorted_counter(counts) for column, counts in parameter_shape.items()
        },
    }


def _column_label_issues(frame: Any) -> list[str]:
    issues: list[str] = []
    first_string_position: dict[str, int] = {}
    for position, label in enumerate(frame.columns):
        if not isinstance(label, str):
            issues.append(
                f"column[{position}] has non-string label of type "
                f"{type(label).__name__}: {_stable_label_literal(label)}"
            )
            continue
        if label in first_string_position:
            issues.append(
                f"column[{position}] duplicates string label {label!r} "
                f"from column[{first_string_position[label]}]"
            )
            continue
        first_string_position[label] = position
    return issues


def _stable_label_literal(label: object) -> str:
    if label is None or isinstance(label, (bool, int, float, str)):
        return repr(label)
    return f"<{type(label).__module__}.{type(label).__qualname__}>"


def _frame_columns(frame: Any) -> list[str]:
    return list(frame.columns)


def _frame_dtypes(frame: Any, columns: list[str]) -> dict[str, str]:
    return {column: str(frame.dtypes[column]) for column in columns}


def _iter_rows(frame: Any, columns: tuple[str, ...]) -> Iterable[tuple[object, ...]]:
    all_columns = _frame_columns(frame)
    positions = tuple(all_columns.index(column) for column in columns)
    for row in frame.itertuples(index=False, name=None):
        yield tuple(row[position] for position in positions)


def _linear_quantile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _is_missing_scalar(value: object) -> bool:
    return value is None or type(value).__name__ in {"NAType", "NaTType"}


def _is_null(value: object) -> bool:
    if _is_missing_scalar(value):
        return True
    return isinstance(value, Real) and not isinstance(value, bool) and math.isnan(float(value))


def _valid_key(value: object) -> bool:
    if _is_null(value):
        return False
    try:
        hash(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_integral(value: object) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_positive_finite(value: object) -> bool:
    return _is_finite_number(value) and float(value) > 0


def _is_exact_minus_one(value: object) -> bool:
    return _is_finite_number(value) and float(value) == -1.0


def _boolean_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    value_type = type(value)
    if value_type.__module__.startswith("numpy") and value_type.__name__ in {"bool", "bool_"}:
        return bool(value)
    return None


def _as_sequence(value: object) -> list[object] | None:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    try:
        converted = value.tolist() if hasattr(value, "tolist") else list(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return converted if isinstance(converted, list) else None


def _positive_integer_sequence(value: object) -> list[int] | None:
    sequence = _as_sequence(value)
    if not sequence or not all(_is_integral(item) and int(item) > 0 for item in sequence):
        return None
    return [int(item) for item in sequence]


def _raw_encoding(value: object) -> tuple[str, int | None]:
    sequence = _as_sequence(value)
    if not sequence:
        return "malformed", None
    if all(_is_finite_number(item) for item in sequence):
        return ("flat_numeric_even" if len(sequence) % 2 == 0 else "flat_numeric_odd"), len(
            sequence
        )
    pairs = [_as_sequence(item) for item in sequence]
    if all(
        pair is not None and len(pair) == 2 and all(_is_finite_number(item) for item in pair)
        for pair in pairs
    ):
        return "point_pairs", len(sequence)
    return "malformed", len(sequence)


def _size_relation(encoding: str, raw_length: int | None, sizes: list[int]) -> str:
    if raw_length is None or encoding == "malformed":
        return "not_evaluable"
    size_total = sum(sizes)
    if encoding == "point_pairs" and size_total == raw_length:
        return "pointpairs_sum_sizes_eq_raw_length"
    if encoding.startswith("flat_numeric") and 2 * size_total == raw_length:
        return "twice_sum_sizes_eq_raw_length"
    if encoding.startswith("flat_numeric") and size_total == raw_length:
        return "sum_sizes_eq_raw_length"
    return "no_declared_relation"


def _literal_frequency_key(value: object) -> str:
    if _is_null(value):
        return "<missing>"
    if isinstance(value, str):
        if value == "<missing>" or value.startswith(("<string:", "<typed:")):
            return f"<string:{json.dumps(value, ensure_ascii=True)}>"
        return value
    boolean = _boolean_value(value)
    if boolean is not None:
        return f"<typed:boolean:{str(boolean).lower()}>"
    if _is_integral(value):
        return f"<typed:integer:{int(value)}>"
    if isinstance(value, Real):
        return f"<typed:number:{float(value)!r}>"
    type_name = f"{type(value).__module__}.{type(value).__qualname__}"
    rendered = json.dumps(str(value), ensure_ascii=True)
    return f"<typed:{type_name}:{rendered}>"


def _value_shape(value: object) -> str:
    if _is_null(value):
        return "missing"
    if isinstance(value, str):
        return "string"
    if _boolean_value(value) is not None:
        return "boolean"
    if _is_integral(value):
        return "integer"
    if isinstance(value, Real):
        return "number"
    if isinstance(value, Mapping):
        return "mapping"
    sequence = _as_sequence(value)
    if sequence is not None:
        return f"sequence_length_{len(sequence)}"
    return f"other:{type(value).__name__}"


def _record_numeric_anomaly(
    column: str,
    value: object,
    row_identifier: str,
    examples: _BoundedExamples,
) -> None:
    if _is_missing_scalar(value):
        classification = "missing"
    elif isinstance(value, Real) and not isinstance(value, bool):
        if math.isfinite(float(value)):
            return
        classification = "nonfinite"
    else:
        classification = "invalid"
    examples.add(f"{column}_{classification}_values", row_identifier)


def _task_row_identifier(task: object, row_number: int) -> str:
    return f"task:{_display(task)}:row:{row_number}"


def _display(value: object) -> str:
    return str(value)


def _display_composite(task: object, part: object) -> str:
    return f"({_display(task)}, {_display(part)})"


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _is_parameter_shape_key(key: str) -> bool:
    fixed = {"missing", "string", "boolean", "integer", "number", "mapping"}
    if key in fixed:
        return True
    if key.startswith("sequence_length_"):
        suffix = key.removeprefix("sequence_length_")
        return suffix.isdigit() and str(int(suffix)) == suffix
    if key.startswith("other:"):
        return bool(key.removeprefix("other:"))
    return False


def _valid_unique_population(
    *, row_count: int, invalid_count: int, duplicate_count: int, subject: str
) -> int:
    if invalid_count > row_count:
        raise ValueError(f"{subject} invalid-key count exceeds its source population")
    valid_rows = row_count - invalid_count
    maximum_duplicates = max(valid_rows - 1, 0)
    if duplicate_count > maximum_duplicates:
        raise ValueError(f"{subject} duplicate count exceeds its valid source population")
    return valid_rows - duplicate_count


def _require_count_population_summary(summary: NumericSummary, subject: str) -> None:
    if (
        summary.finite_count != summary.count
        or summary.missing_count
        or summary.nonfinite_count
        or summary.invalid_count
    ):
        raise ValueError(f"{subject} must summarize only finite computed counts")
    if summary.minimum is not None and summary.minimum < 0:
        raise ValueError(f"{subject} cannot contain negative computed counts")


def _require_inventory_total(inventory: Mapping[str, int], expected: int, subject: str) -> None:
    if sum(inventory.values()) != expected:
        raise ValueError(f"{subject} is inconsistent with source rows")


def _count_semantics() -> dict[str, str]:
    return {
        "task_key_rows_beyond_first": (
            "task rows after the first occurrence of an already observed tasks_index"
        ),
        "part_composite_key_rows_beyond_first": (
            "part rows after the first occurrence of an already observed (tasks_index, part_id) key"
        ),
        "shape_key_rows_beyond_first": (
            "shape rows after the first occurrence of an already observed shape_hash"
        ),
        "part_rows_missing_task": "part rows whose tasks_index is absent from tasks",
        "part_rows_missing_shape": "part rows whose shape_hash is absent from shapes",
        "constraint_rows_missing_task": ("constraint rows whose tasks_index is absent from tasks"),
        "constraint_part_reference_occurrences_missing_part": (
            "integral entries across parts_1 and parts_2 that do not resolve to an observed "
            "(constraint tasks_index, referenced part_id) part key"
        ),
        "task_repeated_shape_row_summary": (
            "per declared task, part-row count minus distinct observed shape_hash count"
        ),
        "source_declared_subshape_count_frequency": (
            "frequency of len(sizes); no hole or contour semantics are inferred"
        ),
        "sheet_length_unconstrained_sentinel_count": (
            "task rows where the observed sheet_length is exactly the documented -1 sentinel"
        ),
        "task_records_without_part_rows": (
            "census of task records whose tasks_index has no observed part row; this is not "
            "classified as a failure"
        ),
        "shape_records_without_part_rows": (
            "census of shape records whose shape_hash has no observed part row; this is not "
            "classified as a failure"
        ),
    }
