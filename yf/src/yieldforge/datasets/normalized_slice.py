"""Strict, import-safe contracts for a bounded, source-faithful Lectra slice.

The models in this module contain passive JSON only.  They deliberately do not
deserialize source pickles or import dataframe libraries.  Source records and
derived facts are kept in separate fields so a consumer never has to infer
which values came from the corpus.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from yieldforge.domain import ContractModel

LECTRA_DATASET_ID = "lectra-7030786-v1.1"
LECTRA_DOI = "10.5281/zenodo.7030786"
LECTRA_LICENSE = "CC-BY-4.0"
LECTRA_SOURCE_UNIT_LABEL = "m^-4"
LECTRA_SOURCE_FILE_ORDER = ("parts.gz", "constraints.gz", "shapes.gz", "tasks.gz")
CONSTRAINT_OPAQUE_FIELD_ORDER = (
    "parts_1",
    "p1_x",
    "p1_y",
    "r1_start",
    "r1_end",
    "r1_flip_x",
    "parts_2",
    "p2_x",
    "p2_y",
    "x_offset",
    "y_offset",
    "motif_order",
    "x_alignment_type",
    "y_alignment_type",
    "proximity_type",
    "max_distance",
    "y_min",
    "y_max",
    "groups_relative_orientation",
    "is_frozen",
)

Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Md5 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
Code = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]*$")]
type SourceNumber = StrictInt | StrictFloat
type Point = tuple[SourceNumber, SourceNumber]
type ConstraintPartReferenceColumn = Literal["parts_1", "parts_2"]


class StrictContractModel(ContractModel):
    """Immutable, extra-forbidding contract that also rejects non-finite floats."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SourceChecksum(StrictContractModel):
    """One pinned source file checksum, in source-manifest order."""

    name: StrictStr = Field(min_length=1)
    checksum_algorithm: Literal["md5"] = "md5"
    checksum: Md5


class SourceUnit(StrictContractModel):
    """The literal published unit label without an invented interpretation."""

    literal_label: Literal["m^-4"]
    interpretation: None = None


class NormalizedSliceSource(StrictContractModel):
    """Exact evidence identity from which a normalized slice was exported."""

    dataset_id: Literal["lectra-7030786-v1.1"]
    source_checksums: tuple[SourceChecksum, ...] = Field(min_length=4, max_length=4)
    source_manifest_sha256: Sha256
    audit_report_sha256: Sha256
    doi: Literal["10.5281/zenodo.7030786"]
    license: Literal["CC-BY-4.0"]
    source_unit: SourceUnit
    conversion_ruleset_version: Literal["lectra-slice-rules.v1"]

    @model_validator(mode="after")
    def require_exact_source_inventory(self) -> Self:
        names = tuple(item.name for item in self.source_checksums)
        if names != LECTRA_SOURCE_FILE_ORDER:
            raise ValueError(
                "source checksum inventory must match the pinned manifest order exactly"
            )
        return self


class OpaqueMissing(StrictContractModel):
    """An explicitly missing source cell."""

    kind: Literal["missing"]


class OpaqueBoolean(StrictContractModel):
    """A source boolean cell or sequence element."""

    kind: Literal["boolean"]
    value: StrictBool


class OpaqueInteger(StrictContractModel):
    """A source integer cell or sequence element."""

    kind: Literal["integer"]
    value: StrictInt


class OpaqueNumber(StrictContractModel):
    """A source floating-point cell or sequence element."""

    kind: Literal["number"]
    value: StrictFloat

    @field_validator("value", mode="before")
    @classmethod
    def require_source_float(cls, value: object) -> object:
        # StrictFloat accepts Python integers by converting them to floats, so
        # distinguish the source kind before Pydantic performs that conversion.
        if type(value) is not float:
            raise ValueError("opaque number value must be a source float")
        return value

    @field_validator("value")
    @classmethod
    def require_finite_source_float(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("opaque number must be finite")
        return value


class OpaqueString(StrictContractModel):
    """A source string cell or sequence element."""

    kind: Literal["string"]
    value: StrictStr


type OpaqueScalar = Annotated[
    OpaqueMissing | OpaqueBoolean | OpaqueInteger | OpaqueNumber | OpaqueString,
    Field(discriminator="kind"),
]


class OpaqueSequence(StrictContractModel):
    """An ordered source sequence whose scalar kinds remain explicit."""

    kind: Literal["sequence"]
    items: tuple[OpaqueScalar, ...]


type OpaqueValue = Annotated[
    OpaqueMissing | OpaqueBoolean | OpaqueInteger | OpaqueNumber | OpaqueString | OpaqueSequence,
    Field(discriminator="kind"),
]


class TaskSourceRow(StrictContractModel):
    """One task row copied without unit conversion."""

    source_row_index: NonNegativeInt
    duration: StrictInt
    efficiency: StrictFloat
    sheet_width: StrictFloat
    sheet_length: StrictFloat
    sheet_type: StrictInt
    tasks_index: NonNegativeInt
    is_train: StrictBool
    is_val: StrictBool
    is_test: StrictBool

    @field_validator("efficiency", "sheet_width", "sheet_length", mode="before")
    @classmethod
    def require_observed_float_dtype(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("source field must retain its audited floating-point dtype")
        return value

    @model_validator(mode="after")
    def require_one_partition(self) -> Self:
        if sum((self.is_train, self.is_val, self.is_test)) != 1:
            raise ValueError("task source row must belong to exactly one source partition")
        return self


class PartSourceRow(StrictContractModel):
    """One part row copied in source order."""

    source_row_index: NonNegativeInt
    tasks_index: NonNegativeInt
    part_id: NonNegativeInt
    shape_hash: StrictInt


class ShapeSourceRow(StrictContractModel):
    """One shape row with its raw scalars and sizes preserved exactly."""

    source_row_index: NonNegativeInt
    shape_hash: StrictInt
    raw: tuple[SourceNumber, ...] = Field(min_length=1)
    sizes: tuple[PositiveInt, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_bound_v11_sizes_encoding(self) -> Self:
        if self.sizes != (len(self.raw),):
            raise ValueError("sizes must equal exactly (len(raw),) for the bound v1.1 slice")
        return self


class ConstraintSourceRow(StrictContractModel):
    """One constraint row with 20 positional opaque source fields.

    ``values`` follows :data:`CONSTRAINT_OPAQUE_FIELD_ORDER`.  Keeping one
    fixed positional inventory avoids lossy JSON objects and makes missing
    cells explicit.
    """

    source_row_index: NonNegativeInt
    tasks_index: NonNegativeInt
    type: StrictStr = Field(min_length=1)
    values: tuple[OpaqueValue, ...]

    @field_validator("values")
    @classmethod
    def require_complete_field_inventory(
        cls, value: tuple[OpaqueValue, ...]
    ) -> tuple[OpaqueValue, ...]:
        if len(value) != len(CONSTRAINT_OPAQUE_FIELD_ORDER):
            raise ValueError(
                f"constraint values must contain exactly {len(CONSTRAINT_OPAQUE_FIELD_ORDER)} "
                "opaque fields"
            )
        return value

    @model_validator(mode="after")
    def require_typed_part_reference_cells(self) -> Self:
        for column in ("parts_1", "parts_2"):
            constraint_part_references(self, column)
        return self


def constraint_value(
    row: ConstraintSourceRow,
    column: str,
) -> OpaqueValue:
    """Return one typed opaque value using the exact observed column positions."""
    try:
        position = CONSTRAINT_OPAQUE_FIELD_ORDER.index(column)
    except ValueError as error:
        raise ValueError(f"unknown opaque constraint column {column!r}") from error
    return row.values[position]


def constraint_part_references(
    row: ConstraintSourceRow,
    column: ConstraintPartReferenceColumn,
) -> tuple[int, ...]:
    """Decode one audited reference cell without numeric coercion."""
    value = constraint_value(row, column)
    if isinstance(value, OpaqueMissing):
        return ()
    if not isinstance(value, OpaqueSequence):
        raise ValueError(f"constraint {column} must be missing or a sequence")
    if any(not isinstance(item, OpaqueInteger) for item in value.items):
        raise ValueError(f"constraint {column} sequence must contain only integral elements")
    return tuple(item.value for item in value.items if isinstance(item, OpaqueInteger))


class DerivedShapeGeometry(StrictContractModel):
    """Declared, reversible geometry facts derived from one raw shape row."""

    shape_hash: StrictInt
    paired_points: tuple[Point, ...] = Field(min_length=3)
    closed_ring: tuple[Point, ...] = Field(min_length=4)
    raw_scalar_count: PositiveInt
    ring_closure_added: StrictBool
    is_simple: StrictBool
    is_valid: StrictBool
    has_nonzero_area: StrictBool
    area: Annotated[SourceNumber, Field(ge=0)]
    bounds: tuple[SourceNumber, SourceNumber, SourceNumber, SourceNumber]

    @model_validator(mode="after")
    def validate_derived_geometry(self) -> Self:
        from shapely.geometry import Polygon

        if self.raw_scalar_count != len(self.paired_points) * 2:
            raise ValueError("raw_scalar_count must equal twice the paired point count")
        expected_ring = (
            self.paired_points + (self.paired_points[0],)
            if self.ring_closure_added
            else self.paired_points
        )
        if len(self.closed_ring) != len(expected_ring) or any(
            not _point_exact(observed, expected)
            for observed, expected in zip(self.closed_ring, expected_ring, strict=True)
        ):
            raise ValueError("closed_ring must be the declared reversible ring closure")
        if self.closed_ring[0] != self.closed_ring[-1]:
            raise ValueError("closed_ring must start and end at the same point")
        min_x, min_y, max_x, max_y = self.bounds
        if min_x > max_x or min_y > max_y:
            raise ValueError("geometry bounds must be ordered")
        expected_bounds = (
            min(point[0] for point in self.paired_points),
            min(point[1] for point in self.paired_points),
            max(point[0] for point in self.paired_points),
            max(point[1] for point in self.paired_points),
        )
        if self.bounds != expected_bounds:
            raise ValueError("geometry bounds must equal the paired-point extent")
        if self.has_nonzero_area != (self.area > 0):
            raise ValueError("has_nonzero_area must agree with area")
        polygon = Polygon(self.paired_points)
        if self.area != polygon.area:
            raise ValueError("area must match the Shapely planar polygon area")
        if self.is_valid is not polygon.is_valid:
            raise ValueError("is_valid must match the Shapely planar polygon truth")
        if self.is_simple is not polygon.is_simple:
            raise ValueError("is_simple must match the Shapely planar polygon truth")
        if self.has_nonzero_area is not (polygon.area > 0):
            raise ValueError("has_nonzero_area must match the Shapely planar polygon truth")
        return self


class NormalizationStatus(StrEnum):
    """Whether the selected records were preserved without source loss."""

    SOURCE_LOSSLESS = "source_lossless"
    REJECTED = "rejected"


class SupportStatus(StrEnum):
    """What the current adapter can truthfully claim for a task."""

    DIRECTLY_SUPPORTED = "directly_supported"
    RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS = "runnable_with_explicit_assumptions"
    VIEW_ONLY = "view_only"


class ProjectionStatus(StrEnum):
    """Whether a solver projection exists or is allowed."""

    NOT_ATTEMPTED = "not_attempted"
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    PROJECTED = "projected"


def _require_sorted_unique_codes(value: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if tuple(sorted(set(value))) != value:
        raise ValueError(f"{label} must be sorted and unique")
    return value


class TaskDisposition(StrictContractModel):
    """Independent normalization, support, and projection claims for one task."""

    tasks_index: NonNegativeInt
    normalization_status: NormalizationStatus
    support_status: SupportStatus
    projection_status: ProjectionStatus
    reason_codes: tuple[Code, ...] = ()
    assumption_codes: tuple[Code, ...] = ()

    @field_validator("reason_codes")
    @classmethod
    def require_sorted_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted_unique_codes(value, label="reason_codes")

    @field_validator("assumption_codes")
    @classmethod
    def require_sorted_assumptions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted_unique_codes(value, label="assumption_codes")

    @model_validator(mode="after")
    def require_truthful_status_evidence(self) -> Self:
        if self.support_status is SupportStatus.VIEW_ONLY and not self.reason_codes:
            raise ValueError("view-only support requires at least one reason code")
        if self.normalization_status is NormalizationStatus.REJECTED and not self.reason_codes:
            raise ValueError("rejected normalization requires at least one reason code")
        if (
            self.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
            and not self.assumption_codes
        ):
            raise ValueError("runnable support requires explicit assumptions")
        if self.support_status is SupportStatus.DIRECTLY_SUPPORTED and self.assumption_codes:
            raise ValueError("directly supported tasks cannot carry projection assumptions")
        if self.projection_status is ProjectionStatus.BLOCKED and not self.reason_codes:
            raise ValueError("blocked projection requires at least one reason code")
        if self.projection_status in {ProjectionStatus.ELIGIBLE, ProjectionStatus.PROJECTED}:
            if self.normalization_status is not NormalizationStatus.SOURCE_LOSSLESS:
                raise ValueError("eligible projection requires source-lossless normalization")
            if self.support_status is SupportStatus.VIEW_ONLY:
                raise ValueError("view-only tasks cannot be eligible for projection")
        return self


class ProvenanceKind(StrEnum):
    """Evidence family used by the research workbench."""

    SOURCE_REAL = "source_real"
    DERIVED = "derived"
    GENERATED = "generated"
    ASSUMED = "assumed"


SOURCE_REAL_PROVENANCE_PATHS = (
    "/constraint_value_columns",
    "/constraints",
    "/parts",
    "/shapes",
    "/source/dataset_id",
    "/source/doi",
    "/source/license",
    "/source/source_checksums",
    "/source/source_unit/interpretation",
    "/source/source_unit/literal_label",
    "/tasks",
)
DERIVED_PROVENANCE_PATHS = (
    "/derived_geometry",
    "/source/audit_report_sha256",
    "/source/conversion_ruleset_version",
    "/source/source_manifest_sha256",
    "/task_dispositions/normalization_status",
    "/task_dispositions/projection_status",
    "/task_dispositions/reason_codes",
    "/task_dispositions/support_status",
    "/task_dispositions/tasks_index",
)
ASSUMED_PROVENANCE_PATH = "/task_dispositions/assumption_codes"


_PROVENANCE_ROOT_FIELDS: dict[str, frozenset[str]] = {
    "schema_version": frozenset(),
    "source": frozenset(NormalizedSliceSource.model_fields),
    "tasks": frozenset(TaskSourceRow.model_fields),
    "parts": frozenset(PartSourceRow.model_fields),
    "shapes": frozenset(ShapeSourceRow.model_fields),
    "constraints": frozenset(ConstraintSourceRow.model_fields),
    "constraint_value_columns": frozenset(),
    "derived_geometry": frozenset(DerivedShapeGeometry.model_fields),
    "task_dispositions": frozenset(TaskDisposition.model_fields),
    "provenance": frozenset({"field_paths", "kind", "note"}),
}


def _validate_provenance_path(path: str) -> None:
    if not path.startswith("/"):
        raise ValueError("provenance field paths must use JSON-pointer-like rooted paths")
    segments = path[1:].split("/")
    if not segments or any(not segment or not segment.isidentifier() for segment in segments):
        raise ValueError("provenance field paths must use JSON-pointer-like field segments")
    root = segments[0]
    if root not in _PROVENANCE_ROOT_FIELDS:
        raise ValueError(f"provenance path has no artifact root {root!r}")
    if len(segments) > 3:
        raise ValueError("provenance paths exceed the supported artifact field depth")
    if len(segments) == 2 and segments[1] not in _PROVENANCE_ROOT_FIELDS[root]:
        raise ValueError(
            f"provenance path {path!r} identifies no nested field on artifact root {root!r}"
        )
    if len(segments) == 3 and (
        segments[:2] != ["source", "source_unit"] or segments[2] not in SourceUnit.model_fields
    ):
        raise ValueError(f"provenance path {path!r} identifies no nested artifact leaf field")


def _is_source_real_provenance_path(path: str) -> bool:
    return path in SOURCE_REAL_PROVENANCE_PATHS


def _is_derived_provenance_path(path: str) -> bool:
    return path in DERIVED_PROVENANCE_PATHS


class ProvenanceGroup(StrictContractModel):
    """A group of JSON field paths that share one provenance family."""

    kind: ProvenanceKind
    field_paths: tuple[StrictStr, ...] = Field(min_length=1)
    note: StrictStr = Field(min_length=1)

    @field_validator("field_paths")
    @classmethod
    def require_sorted_unique_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not path.strip() for path in value):
            raise ValueError("provenance field paths must be non-empty")
        if tuple(sorted(set(value))) != value:
            raise ValueError("provenance field paths must be sorted and unique")
        for path in value:
            _validate_provenance_path(path)
        return value

    @model_validator(mode="after")
    def require_kind_specific_paths(self) -> Self:
        if self.kind is ProvenanceKind.GENERATED:
            raise ValueError("GENERATED provenance is not supported by this source-slice schema")
        if self.kind is ProvenanceKind.SOURCE_REAL:
            if "/source" in self.field_paths:
                raise ValueError(
                    "SOURCE_REAL provenance requires exhaustive leaf-level source paths"
                )
            if any(not _is_source_real_provenance_path(path) for path in self.field_paths):
                raise ValueError("SOURCE_REAL provenance cannot point at derived or assumed fields")
        if self.kind is ProvenanceKind.DERIVED and any(
            not _is_derived_provenance_path(path) for path in self.field_paths
        ):
            raise ValueError("DERIVED provenance cannot point at source or assumed fields")
        if self.kind is ProvenanceKind.ASSUMED and self.field_paths != (ASSUMED_PROVENANCE_PATH,):
            raise ValueError("ASSUMED provenance is limited to task disposition assumption_codes")
        return self


type SourceIndexedRow = TaskSourceRow | PartSourceRow | ShapeSourceRow | ConstraintSourceRow


def _require_source_order(rows: tuple[SourceIndexedRow, ...], *, table: str) -> None:
    indexes = tuple(row.source_row_index for row in rows)
    if any(current >= following for current, following in zip(indexes, indexes[1:], strict=False)):
        raise ValueError(f"{table} source_row_index values must be strictly increasing")


def _point_exact(left: Point, right: Point) -> bool:
    return all(
        type(left_value) is type(right_value) and left_value == right_value
        for left_value, right_value in zip(left, right, strict=True)
    )


class NormalizedSlice(StrictContractModel):
    """A bounded set of lossless source rows plus separately labelled facts."""

    schema_version: Literal["yieldforge.normalized-slice.v1"]
    source: NormalizedSliceSource
    tasks: tuple[TaskSourceRow, ...] = Field(min_length=1)
    parts: tuple[PartSourceRow, ...] = Field(min_length=1)
    shapes: tuple[ShapeSourceRow, ...] = Field(min_length=1)
    constraints: tuple[ConstraintSourceRow, ...]
    constraint_value_columns: tuple[StrictStr, ...]
    derived_geometry: tuple[DerivedShapeGeometry, ...] = Field(min_length=1)
    task_dispositions: tuple[TaskDisposition, ...]
    provenance: tuple[ProvenanceGroup, ...] = Field(min_length=1)

    @field_validator("constraint_value_columns")
    @classmethod
    def require_observed_constraint_value_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != CONSTRAINT_OPAQUE_FIELD_ORDER:
            raise ValueError("constraint_value_columns must match the exact observed source order")
        return value

    @model_validator(mode="after")
    def validate_source_preservation_and_references(self) -> Self:
        for table, rows in (
            ("tasks", self.tasks),
            ("parts", self.parts),
            ("shapes", self.shapes),
            ("constraints", self.constraints),
        ):
            _require_source_order(rows, table=table)

        task_ids = tuple(row.tasks_index for row in self.tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task rows must have unique tasks_index values")
        task_id_set = set(task_ids)

        part_keys = tuple((row.tasks_index, row.part_id) for row in self.parts)
        if len(part_keys) != len(set(part_keys)):
            raise ValueError("part rows must have unique (tasks_index, part_id) keys")
        part_key_set = set(part_keys)
        for part in self.parts:
            if part.tasks_index not in task_id_set:
                raise ValueError(f"part row has unresolved tasks_index {part.tasks_index}")
        part_task_ids = {part.tasks_index for part in self.parts}
        for task_id in task_ids:
            if task_id not in part_task_ids:
                raise ValueError(f"task {task_id} must have at least one part row")

        shape_hashes = tuple(row.shape_hash for row in self.shapes)
        if len(shape_hashes) != len(set(shape_hashes)):
            raise ValueError("shape rows must have unique shape_hash values")
        shape_hash_set = set(shape_hashes)
        for part in self.parts:
            if part.shape_hash not in shape_hash_set:
                raise ValueError(f"part row has unresolved shape_hash {part.shape_hash}")
        referenced_shape_hashes = {part.shape_hash for part in self.parts}
        for shape_hash in shape_hashes:
            if shape_hash not in referenced_shape_hashes:
                raise ValueError(f"shape {shape_hash} must be referenced by at least one part row")

        for constraint in self.constraints:
            if constraint.tasks_index not in task_id_set:
                raise ValueError(
                    f"constraint row has unresolved tasks_index {constraint.tasks_index}"
                )
            for column in ("parts_1", "parts_2"):
                for part_id in constraint_part_references(constraint, column):
                    if (constraint.tasks_index, part_id) not in part_key_set:
                        raise ValueError(
                            f"constraint has unresolved {column} reference {part_id} "
                            f"for task {constraint.tasks_index}"
                        )

        geometry_hashes = tuple(geometry.shape_hash for geometry in self.derived_geometry)
        if geometry_hashes != shape_hashes:
            raise ValueError("derived geometry must match shape rows exactly and in source order")
        for shape, geometry in zip(self.shapes, self.derived_geometry, strict=True):
            if len(shape.raw) % 2:
                raise ValueError(f"shape {shape.shape_hash} raw scalars cannot be paired evenly")
            paired = tuple(zip(shape.raw[::2], shape.raw[1::2], strict=True))
            if len(paired) != len(geometry.paired_points) or any(
                not _point_exact(source, derived)
                for source, derived in zip(paired, geometry.paired_points, strict=True)
            ):
                raise ValueError(
                    f"shape {shape.shape_hash} derived points must equal adjacent raw scalars"
                )
            if geometry.raw_scalar_count != len(shape.raw):
                raise ValueError(
                    f"shape {shape.shape_hash} derived scalar count must equal source raw length"
                )

        disposition_ids = tuple(item.tasks_index for item in self.task_dispositions)
        if disposition_ids != task_ids:
            raise ValueError("tasks must have exactly one disposition in source task order")
        geometry_by_hash = {geometry.shape_hash: geometry for geometry in self.derived_geometry}
        for disposition in self.task_dispositions:
            if disposition.projection_status not in {
                ProjectionStatus.ELIGIBLE,
                ProjectionStatus.PROJECTED,
            }:
                continue
            referenced_geometry = (
                geometry_by_hash[part.shape_hash]
                for part in self.parts
                if part.tasks_index == disposition.tasks_index
            )
            if any(
                not (geometry.is_simple and geometry.is_valid and geometry.has_nonzero_area)
                for geometry in referenced_geometry
            ):
                raise ValueError(
                    f"eligible or projected task {disposition.tasks_index} shapes must be simple, "
                    "valid, and nonzero"
                )

        provenance_kinds = tuple(group.kind for group in self.provenance)
        if len(provenance_kinds) != len(set(provenance_kinds)):
            raise ValueError("provenance groups must have unique kinds")
        required_kinds = {ProvenanceKind.SOURCE_REAL, ProvenanceKind.DERIVED}
        if not required_kinds.issubset(provenance_kinds):
            raise ValueError("provenance must identify source_real and derived field groups")
        groups_by_kind = {group.kind: group for group in self.provenance}
        source_real_paths = set(groups_by_kind[ProvenanceKind.SOURCE_REAL].field_paths)
        missing_source_real_paths = set(SOURCE_REAL_PROVENANCE_PATHS) - source_real_paths
        if missing_source_real_paths:
            raise ValueError(
                "SOURCE_REAL provenance minimum coverage is missing "
                f"{sorted(missing_source_real_paths)!r}"
            )
        derived_paths = set(groups_by_kind[ProvenanceKind.DERIVED].field_paths)
        missing_derived_paths = set(DERIVED_PROVENANCE_PATHS) - derived_paths
        if missing_derived_paths:
            raise ValueError(
                f"DERIVED provenance minimum coverage is missing {sorted(missing_derived_paths)!r}"
            )
        has_assumptions = any(item.assumption_codes for item in self.task_dispositions)
        if has_assumptions and ProvenanceKind.ASSUMED not in provenance_kinds:
            raise ValueError("task assumptions require an ASSUMED provenance group")
        if has_assumptions:
            assumed_group = next(
                group for group in self.provenance if group.kind is ProvenanceKind.ASSUMED
            )
            if ASSUMED_PROVENANCE_PATH not in assumed_group.field_paths:
                raise ValueError(
                    "ASSUMED provenance must identify /task_dispositions/assumption_codes"
                )
        elif ProvenanceKind.ASSUMED in provenance_kinds:
            raise ValueError("ASSUMED provenance must be absent when the slice has no assumptions")
        return self
