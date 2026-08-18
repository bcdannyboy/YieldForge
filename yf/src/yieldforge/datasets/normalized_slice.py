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
    conversion_ruleset_version: StrictStr = Field(min_length=1)

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
    duration: SourceNumber
    efficiency: SourceNumber
    sheet_width: SourceNumber
    sheet_length: SourceNumber
    sheet_type: StrictInt
    tasks_index: NonNegativeInt
    is_train: StrictBool
    is_val: StrictBool
    is_test: StrictBool

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
        return value


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
        for part in self.parts:
            if part.tasks_index not in task_id_set:
                raise ValueError(f"part row has unresolved tasks_index {part.tasks_index}")

        shape_hashes = tuple(row.shape_hash for row in self.shapes)
        if len(shape_hashes) != len(set(shape_hashes)):
            raise ValueError("shape rows must have unique shape_hash values")
        shape_hash_set = set(shape_hashes)
        for part in self.parts:
            if part.shape_hash not in shape_hash_set:
                raise ValueError(f"part row has unresolved shape_hash {part.shape_hash}")

        for constraint in self.constraints:
            if constraint.tasks_index not in task_id_set:
                raise ValueError(
                    f"constraint row has unresolved tasks_index {constraint.tasks_index}"
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

        provenance_kinds = tuple(group.kind for group in self.provenance)
        if len(provenance_kinds) != len(set(provenance_kinds)):
            raise ValueError("provenance groups must have unique kinds")
        required_kinds = {ProvenanceKind.SOURCE_REAL, ProvenanceKind.DERIVED}
        if not required_kinds.issubset(provenance_kinds):
            raise ValueError("provenance must identify source_real and derived field groups")
        return self
