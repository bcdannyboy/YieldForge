"""Immutable, browser-safe queries over the content-pinned Lectra slice.

This module deliberately stays on the passive JSON side of the qualification
boundary.  It never imports pandas or pickle and has no unbound-slice fallback.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from yieldforge.datasets.normalized_slice import (
    ConstraintSourceRow,
    DerivedShapeGeometry,
    NormalizationStatus,
    NormalizedSlice,
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
    SourceNumber,
    SupportStatus,
    TaskDisposition,
    TaskSourceRow,
    constraint_part_references,
    constraint_value,
)
from yieldforge.datasets.passive_report import (
    PassiveEvidenceError,
    decode_strict_json_bytes,
    load_normalized_slice_evidence,
    parse_dataset_source_manifest,
    parse_normalized_slice,
    read_passive_evidence_file,
)
from yieldforge.datasets.source_manifest import DatasetSourceManifest
from yieldforge.domain import ProjectedTask, ProjectionMode, StripPackingProblem

YF_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_SLICE_PATH = YF_ROOT / "datasets/fixtures/lectra-representative-slice.json"
COMMITTED_MANIFEST_PATH = YF_ROOT / "datasets/sources/lectra-7030786-v1.1.json"
BOUND_AUDIT_REPORT_PATH = YF_ROOT / "var/data/reports/lectra-7030786-v1.1/lectra-audit.json"
COMMITTED_SLICE_SHA256 = "d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8"
MIN_CURSOR_SIGNING_KEY_BYTES = 32

MAX_SAFE_JSON_INTEGER = 2**53 - 1
DecimalInteger = Annotated[StrictStr, Field(pattern=r"^-?(0|[1-9][0-9]*)$")]
SafeJsonInt = Annotated[
    StrictInt,
    Field(ge=-MAX_SAFE_JSON_INTEGER, le=MAX_SAFE_JSON_INTEGER),
]
BrowserSourceNumber = DecimalInteger | StrictFloat
Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class CorpusQueryError(ValueError):
    """Base error for a rejected corpus query."""


class InvalidTaskQueryError(CorpusQueryError):
    """A task-list query violated the bounded public contract."""


class InvalidCursorError(CorpusQueryError):
    """A cursor was malformed, tampered with, stale, or reused with new filters."""


class TaskNotFoundError(CorpusQueryError):
    """No source task with the requested identifier exists in the bound slice."""


class TaskNotSolvableError(CorpusQueryError):
    """A task is blocked or its exact assumptions were not acknowledged."""


class CorpusService(Protocol):
    """Read-only corpus surface shared by passive-file and database adapters."""

    def summary(self) -> CorpusSummaryDto: ...

    def list_tasks(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        status: SupportStatus | str | None = None,
        constraint_type: str | None = None,
        task_id: int | None = None,
        min_parts: int | None = None,
        max_parts: int | None = None,
    ) -> TaskPageDto: ...

    def task_detail(self, tasks_index: int) -> TaskDetailDto: ...

    def project_problem(
        self,
        tasks_index: int,
        *,
        acknowledged_assumption_codes: tuple[str, ...],
    ) -> StripPackingProblem: ...

    def project_task(
        self,
        tasks_index: int,
        *,
        mode: ProjectionMode,
        acknowledged_assumption_codes: tuple[str, ...],
        acknowledged_intervention_codes: tuple[str, ...],
    ) -> ProjectedTask: ...


class CorpusDto(BaseModel):
    """Strict, immutable DTO that cannot emit non-finite JSON floats."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SourceChecksumDto(CorpusDto):
    name: StrictStr = Field(min_length=1)
    checksum_algorithm: Literal["md5"]
    checksum: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]


class CorpusSourceDto(CorpusDto):
    dataset_id: Literal["lectra-7030786-v1.1"]
    doi: Literal["10.5281/zenodo.7030786"]
    license: Literal["CC-BY-4.0"]
    conversion_ruleset_version: Literal[
        "lectra-slice-rules.v1",
        "lectra-catalog-rules.v1",
        "lectra-catalog-rules.v2",
    ]
    source_checksums: tuple[SourceChecksumDto, ...]
    source_manifest_sha256: Sha256
    audit_report_sha256: Sha256
    slice_sha256: Sha256
    evidence_status: Literal[
        "content_pinned_with_manifest_identity",
        "fully_bound_to_local_audit_evidence",
    ]


class CoordinateUnitDto(CorpusDto):
    literal_label: Literal["m^-4"]
    interpretation: None = None


class NamedCountDto(CorpusDto):
    name: StrictStr = Field(min_length=1)
    count: SafeJsonInt = Field(ge=0)


class CorpusSolveCapabilityDto(CorpusDto):
    eligible_task_count: SafeJsonInt = Field(ge=0)
    blocked_task_count: SafeJsonInt = Field(ge=0)
    directly_supported_task_count: SafeJsonInt = Field(ge=0)


class CorpusSummaryDto(CorpusDto):
    schema_version: Literal["yieldforge.corpus-summary.v1"] = "yieldforge.corpus-summary.v1"
    source: CorpusSourceDto
    coordinate_unit: CoordinateUnitDto
    task_count: SafeJsonInt = Field(ge=0)
    part_count: SafeJsonInt = Field(ge=0)
    shape_count: SafeJsonInt = Field(ge=0)
    constraint_count: SafeJsonInt = Field(ge=0)
    support_status_counts: tuple[NamedCountDto, ...]
    constraint_type_counts: tuple[NamedCountDto, ...]
    solve_capability: CorpusSolveCapabilityDto


class TaskSourceDto(CorpusDto):
    source_row_index: SafeJsonInt = Field(ge=0)
    duration: SafeJsonInt
    efficiency: StrictFloat
    sheet_width: StrictFloat
    sheet_length: StrictFloat
    sheet_type: SafeJsonInt
    tasks_index: SafeJsonInt = Field(ge=0)
    is_train: StrictBool
    is_val: StrictBool
    is_test: StrictBool


class ProjectionOptionDto(CorpusDto):
    mode: ProjectionMode
    source_preserving: StrictBool
    assumption_codes: tuple[StrictStr, ...]
    intervention_codes: tuple[StrictStr, ...]


class S1ProjectionDiagnosticsDto(CorpusDto):
    orientation_state_count: SafeJsonInt = Field(ge=0)
    flip_constraint_count: SafeJsonInt = Field(ge=0)
    flip_part_count: SafeJsonInt = Field(ge=0)
    mixed_flip_constraint_count: SafeJsonInt = Field(ge=0)


class SolveCapabilityDto(CorpusDto):
    can_solve: StrictBool
    requires_assumption_acknowledgement: StrictBool
    normalization_status: NormalizationStatus
    support_status: SupportStatus
    projection_status: ProjectionStatus
    reason_codes: tuple[StrictStr, ...]
    assumption_codes: tuple[StrictStr, ...]
    projection_options: tuple[ProjectionOptionDto, ...]

    @model_validator(mode="after")
    def require_authoritative_status(self) -> Self:
        eligible = self.projection_status in {
            ProjectionStatus.ELIGIBLE,
            ProjectionStatus.PROJECTED,
        }
        if self.can_solve is not eligible:
            raise ValueError("can_solve must equal the authoritative projection status")
        requires_ack = eligible and bool(self.assumption_codes)
        if self.requires_assumption_acknowledgement is not requires_ack:
            raise ValueError(
                "assumption acknowledgement must match eligible assumption-backed projection"
            )
        if eligible is not bool(self.projection_options):
            raise ValueError("only eligible tasks may expose projection options")
        if self.projection_options:
            recorded = self.projection_options[0]
            if (
                recorded.mode is not ProjectionMode.SOURCE_AS_RECORDED
                or not recorded.source_preserving
                or recorded.assumption_codes != self.assumption_codes
                or recorded.intervention_codes
            ):
                raise ValueError("the default projection must preserve the recorded source mode")
            if len({option.mode for option in self.projection_options}) != len(
                self.projection_options
            ):
                raise ValueError("projection option modes must be unique")
        return self


class TaskSummaryDto(CorpusDto):
    task: TaskSourceDto
    tasks_index: SafeJsonInt = Field(ge=0)
    part_count: SafeJsonInt = Field(ge=0)
    shape_count: SafeJsonInt = Field(ge=0)
    constraint_count: SafeJsonInt = Field(ge=0)
    constraint_types: tuple[StrictStr, ...]
    solve_capability: SolveCapabilityDto


class TaskPageDto(CorpusDto):
    schema_version: Literal["yieldforge.task-page.v1"] = "yieldforge.task-page.v1"
    items: tuple[TaskSummaryDto, ...]
    next_cursor: StrictStr | None = None


class PartDto(CorpusDto):
    source_row_index: SafeJsonInt = Field(ge=0)
    tasks_index: SafeJsonInt = Field(ge=0)
    part_id: SafeJsonInt = Field(ge=0)
    shape_hash: DecimalInteger


class ShapeDto(CorpusDto):
    source_row_index: SafeJsonInt = Field(ge=0)
    shape_hash: DecimalInteger
    raw: tuple[BrowserSourceNumber, ...]
    sizes: tuple[SafeJsonInt, ...]


class OpaqueMissingDto(CorpusDto):
    kind: Literal["missing"]


class OpaqueBooleanDto(CorpusDto):
    kind: Literal["boolean"]
    value: StrictBool


class OpaqueIntegerDto(CorpusDto):
    kind: Literal["integer"]
    value: DecimalInteger


class OpaqueNumberDto(CorpusDto):
    kind: Literal["number"]
    value: StrictFloat


class OpaqueStringDto(CorpusDto):
    kind: Literal["string"]
    value: StrictStr


OpaqueScalarDto = Annotated[
    OpaqueMissingDto | OpaqueBooleanDto | OpaqueIntegerDto | OpaqueNumberDto | OpaqueStringDto,
    Field(discriminator="kind"),
]


class OpaqueSequenceDto(CorpusDto):
    kind: Literal["sequence"]
    items: tuple[OpaqueScalarDto, ...]


OpaqueValueDto = Annotated[
    OpaqueMissingDto
    | OpaqueBooleanDto
    | OpaqueIntegerDto
    | OpaqueNumberDto
    | OpaqueStringDto
    | OpaqueSequenceDto,
    Field(discriminator="kind"),
]


class ConstraintDto(CorpusDto):
    source_row_index: SafeJsonInt = Field(ge=0)
    tasks_index: SafeJsonInt = Field(ge=0)
    type: StrictStr = Field(min_length=1)
    values: tuple[OpaqueValueDto, ...]


class DerivedShapeGeometryDto(CorpusDto):
    shape_hash: DecimalInteger
    paired_points: tuple[tuple[BrowserSourceNumber, BrowserSourceNumber], ...]
    closed_ring: tuple[tuple[BrowserSourceNumber, BrowserSourceNumber], ...]
    raw_scalar_count: SafeJsonInt = Field(gt=0)
    ring_closure_added: StrictBool
    is_simple: StrictBool
    is_valid: StrictBool
    has_nonzero_area: StrictBool
    area: BrowserSourceNumber
    bounds: tuple[
        BrowserSourceNumber,
        BrowserSourceNumber,
        BrowserSourceNumber,
        BrowserSourceNumber,
    ]


class ProvenanceDto(CorpusDto):
    kind: ProvenanceKind
    field_paths: tuple[StrictStr, ...]
    note: StrictStr = Field(min_length=1)


class TaskDetailDto(CorpusDto):
    schema_version: Literal["yieldforge.task-detail.v1"] = "yieldforge.task-detail.v1"
    source: CorpusSourceDto
    coordinate_unit: CoordinateUnitDto
    summary: TaskSummaryDto
    parts: tuple[PartDto, ...]
    shapes: tuple[ShapeDto, ...]
    constraints: tuple[ConstraintDto, ...]
    constraint_value_columns: tuple[StrictStr, ...]
    derived_geometry: tuple[DerivedShapeGeometryDto, ...]
    provenance: tuple[ProvenanceDto, ...]
    s1_projection_diagnostics: S1ProjectionDiagnosticsDto


def _safe_int(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not -MAX_SAFE_JSON_INTEGER <= value <= MAX_SAFE_JSON_INTEGER:
        raise PassiveEvidenceError(f"{label} is not browser-safe as a JSON integer")
    return value


def _browser_number(value: SourceNumber) -> str | float:
    if type(value) is int:
        return str(value)
    if type(value) is not float or not math.isfinite(value):
        raise PassiveEvidenceError("source geometry contains a non-finite numeric value")
    return value


def _source_dto(row: TaskSourceRow) -> TaskSourceDto:
    return TaskSourceDto(
        source_row_index=_safe_int(row.source_row_index, label="task source_row_index"),
        duration=_safe_int(row.duration, label="task duration"),
        efficiency=row.efficiency,
        sheet_width=row.sheet_width,
        sheet_length=row.sheet_length,
        sheet_type=_safe_int(row.sheet_type, label="task sheet_type"),
        tasks_index=_safe_int(row.tasks_index, label="tasks_index"),
        is_train=row.is_train,
        is_val=row.is_val,
        is_test=row.is_test,
    )


def _s1_projection_diagnostics(
    constraints: tuple[ConstraintSourceRow, ...],
) -> S1ProjectionDiagnosticsDto:
    orientation_state_count = 0
    flip_constraint_count = 0
    flipped_parts: set[int] = set()
    mixed_flip_constraint_count = 0
    for row in constraints:
        if row.type != "s1":
            continue
        starts = constraint_value(row, "r1_start")
        if isinstance(starts, OpaqueSequence):
            orientation_state_count += len(starts.items)
        flips = constraint_value(row, "r1_flip_x")
        if not isinstance(flips, OpaqueSequence):
            continue
        strict_flip_values = tuple(
            item.value for item in flips.items if isinstance(item, OpaqueInteger)
        )
        if len(set(strict_flip_values)) > 1:
            mixed_flip_constraint_count += 1
        if 1 not in strict_flip_values:
            continue
        flip_constraint_count += 1
        flipped_parts.update(constraint_part_references(row, "parts_1"))
    return S1ProjectionDiagnosticsDto(
        orientation_state_count=orientation_state_count,
        flip_constraint_count=flip_constraint_count,
        flip_part_count=len(flipped_parts),
        mixed_flip_constraint_count=mixed_flip_constraint_count,
    )


def _capability(
    disposition: TaskDisposition,
    diagnostics: S1ProjectionDiagnosticsDto,
) -> SolveCapabilityDto:
    eligible = disposition.projection_status in {
        ProjectionStatus.ELIGIBLE,
        ProjectionStatus.PROJECTED,
    }
    options: tuple[ProjectionOptionDto, ...] = ()
    if eligible:
        recorded = ProjectionOptionDto(
            mode=ProjectionMode.SOURCE_AS_RECORDED,
            source_preserving=True,
            assumption_codes=disposition.assumption_codes,
            intervention_codes=(),
        )
        options = (recorded,)
        if diagnostics.flip_part_count:
            from yieldforge.datasets.projection import NO_FLIP_ABLATION

            options += (
                ProjectionOptionDto(
                    mode=ProjectionMode.FORCE_FLIP_X_ZERO,
                    source_preserving=False,
                    assumption_codes=disposition.assumption_codes,
                    intervention_codes=(NO_FLIP_ABLATION,),
                ),
            )
    return SolveCapabilityDto(
        can_solve=eligible,
        requires_assumption_acknowledgement=eligible and bool(disposition.assumption_codes),
        normalization_status=disposition.normalization_status,
        support_status=disposition.support_status,
        projection_status=disposition.projection_status,
        reason_codes=disposition.reason_codes,
        assumption_codes=disposition.assumption_codes,
        projection_options=options,
    )


def _part_dto(row: PartSourceRow) -> PartDto:
    return PartDto(
        source_row_index=_safe_int(row.source_row_index, label="part source_row_index"),
        tasks_index=_safe_int(row.tasks_index, label="tasks_index"),
        part_id=_safe_int(row.part_id, label="part_id"),
        shape_hash=str(row.shape_hash),
    )


def _shape_dto(row: ShapeSourceRow) -> ShapeDto:
    return ShapeDto(
        source_row_index=_safe_int(row.source_row_index, label="shape source_row_index"),
        shape_hash=str(row.shape_hash),
        raw=tuple(_browser_number(value) for value in row.raw),
        sizes=tuple(_safe_int(value, label="shape size") for value in row.sizes),
    )


def _opaque_dto(value: OpaqueValue) -> OpaqueValueDto:
    if isinstance(value, OpaqueMissing):
        return OpaqueMissingDto(kind="missing")
    if isinstance(value, OpaqueBoolean):
        return OpaqueBooleanDto(kind="boolean", value=value.value)
    if isinstance(value, OpaqueInteger):
        return OpaqueIntegerDto(kind="integer", value=str(value.value))
    if isinstance(value, OpaqueNumber):
        return OpaqueNumberDto(kind="number", value=value.value)
    if isinstance(value, OpaqueString):
        return OpaqueStringDto(kind="string", value=value.value)
    if isinstance(value, OpaqueSequence):
        return OpaqueSequenceDto(
            kind="sequence",
            items=tuple(_opaque_dto(item) for item in value.items),
        )
    raise TypeError(f"unsupported opaque value {type(value)!r}")


def _constraint_dto(row: ConstraintSourceRow) -> ConstraintDto:
    return ConstraintDto(
        source_row_index=_safe_int(
            row.source_row_index,
            label="constraint source_row_index",
        ),
        tasks_index=_safe_int(row.tasks_index, label="tasks_index"),
        type=row.type,
        values=tuple(_opaque_dto(value) for value in row.values),
    )


def _geometry_dto(row: DerivedShapeGeometry) -> DerivedShapeGeometryDto:
    return DerivedShapeGeometryDto(
        shape_hash=str(row.shape_hash),
        paired_points=tuple(
            (_browser_number(point[0]), _browser_number(point[1])) for point in row.paired_points
        ),
        closed_ring=tuple(
            (_browser_number(point[0]), _browser_number(point[1])) for point in row.closed_ring
        ),
        raw_scalar_count=_safe_int(row.raw_scalar_count, label="raw_scalar_count"),
        ring_closure_added=row.ring_closure_added,
        is_simple=row.is_simple,
        is_valid=row.is_valid,
        has_nonzero_area=row.has_nonzero_area,
        area=_browser_number(row.area),
        bounds=tuple(_browser_number(value) for value in row.bounds),  # type: ignore[arg-type]
    )


def _provenance_dto(group: ProvenanceGroup) -> ProvenanceDto:
    return ProvenanceDto(kind=group.kind, field_paths=group.field_paths, note=group.note)


class CorpusQueryService:
    """Read-only indexed view of one content-pinned normalized slice.

    Opaque cursors are stable for this service's lifetime.  The default random
    private key intentionally invalidates cursors after process restart; a
    future server may inject a persistent private key when restart stability is
    required.
    """

    def __init__(
        self,
        normalized: NormalizedSlice,
        *,
        slice_sha256: str,
        evidence_status: Literal[
            "content_pinned_with_manifest_identity",
            "fully_bound_to_local_audit_evidence",
        ],
        cursor_signing_key: bytes | None = None,
    ) -> None:
        if cursor_signing_key is None:
            cursor_signing_key = secrets.token_bytes(MIN_CURSOR_SIGNING_KEY_BYTES)
        elif (
            type(cursor_signing_key) is not bytes
            or len(cursor_signing_key) < MIN_CURSOR_SIGNING_KEY_BYTES
        ):
            raise ValueError("cursor signing key must contain at least 32 private bytes")
        self._normalized = normalized
        self._slice_sha256 = slice_sha256
        self._tasks = tuple(
            sorted(normalized.tasks, key=lambda row: (row.source_row_index, row.tasks_index))
        )
        self._tasks_by_key = {(row.source_row_index, row.tasks_index): row for row in self._tasks}
        self._task_keys = frozenset(self._tasks_by_key)
        self._tasks_by_id = {row.tasks_index: row for row in self._tasks}
        self._parts_by_task = {
            task.tasks_index: tuple(
                part for part in normalized.parts if part.tasks_index == task.tasks_index
            )
            for task in self._tasks
        }
        self._constraints_by_task = {
            task.tasks_index: tuple(
                row for row in normalized.constraints if row.tasks_index == task.tasks_index
            )
            for task in self._tasks
        }
        self._dispositions = {
            disposition.tasks_index: disposition for disposition in normalized.task_dispositions
        }
        self._shapes_by_hash = {shape.shape_hash: shape for shape in normalized.shapes}
        self._geometry_by_hash = {
            geometry.shape_hash: geometry for geometry in normalized.derived_geometry
        }
        self._source = CorpusSourceDto(
            dataset_id=normalized.source.dataset_id,
            doi=normalized.source.doi,
            license=normalized.source.license,
            conversion_ruleset_version=normalized.source.conversion_ruleset_version,
            source_checksums=tuple(
                SourceChecksumDto.model_validate(item.model_dump())
                for item in normalized.source.source_checksums
            ),
            source_manifest_sha256=normalized.source.source_manifest_sha256,
            audit_report_sha256=normalized.source.audit_report_sha256,
            slice_sha256=slice_sha256,
            evidence_status=evidence_status,
        )
        self._unit = CoordinateUnitDto(
            literal_label=normalized.source.source_unit.literal_label,
            interpretation=normalized.source.source_unit.interpretation,
        )
        self._cursor_key = cursor_signing_key

    @classmethod
    def load_bound(
        cls,
        *,
        slice_path: Path,
        report_path: Path,
        manifest_path: Path,
        cursor_signing_key: bytes | None = None,
    ) -> CorpusQueryService:
        """Load through exact slice, audit-report, and manifest evidence binding."""

        slice_payload_before = read_passive_evidence_file(
            slice_path,
            label="normalized Lectra slice",
        )
        normalized, _, _ = load_normalized_slice_evidence(
            slice_path,
            report_path,
            manifest_path,
        )
        slice_payload_after = read_passive_evidence_file(
            slice_path,
            label="normalized Lectra slice",
        )
        if not hmac.compare_digest(slice_payload_before, slice_payload_after):
            raise PassiveEvidenceError("normalized Lectra slice changed during corpus load")
        if parse_normalized_slice(slice_payload_before) != normalized:
            raise PassiveEvidenceError(
                "bound normalized Lectra slice does not match the safely read payload"
            )
        return cls(
            normalized,
            slice_sha256=hashlib.sha256(slice_payload_before).hexdigest(),
            evidence_status="fully_bound_to_local_audit_evidence",
            cursor_signing_key=cursor_signing_key,
        )

    @staticmethod
    def _require_manifest_identity(
        normalized: NormalizedSlice,
        manifest: DatasetSourceManifest,
        *,
        manifest_payload: bytes,
    ) -> None:
        source = normalized.source
        actual_manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
        if not hmac.compare_digest(source.source_manifest_sha256, actual_manifest_sha256):
            raise PassiveEvidenceError(
                "committed normalized slice does not identify the committed manifest bytes"
            )
        if (
            source.dataset_id != manifest.dataset_id
            or source.doi != manifest.doi
            or source.license != manifest.license
        ):
            raise PassiveEvidenceError(
                "committed normalized slice source identity does not match the manifest"
            )
        expected_checksums = tuple(
            (item.name, item.checksum_algorithm, item.checksum) for item in manifest.files
        )
        observed_checksums = tuple(
            (item.name, item.checksum_algorithm, item.checksum) for item in source.source_checksums
        )
        if observed_checksums != expected_checksums:
            raise PassiveEvidenceError(
                "committed normalized slice checksums do not match the manifest"
            )

    @classmethod
    def from_repository(
        cls,
        *,
        cursor_signing_key: bytes | None = None,
    ) -> CorpusQueryService:
        """Load the exact committed slice and manifest without claiming a fresh audit bind."""

        slice_payload = read_passive_evidence_file(
            COMMITTED_SLICE_PATH,
            label="committed normalized Lectra slice",
        )
        actual_slice_sha256 = hashlib.sha256(slice_payload).hexdigest()
        if not hmac.compare_digest(actual_slice_sha256, COMMITTED_SLICE_SHA256):
            raise PassiveEvidenceError(
                "committed normalized Lectra slice does not match its pinned content hash"
            )
        normalized = parse_normalized_slice(slice_payload)
        manifest_payload = read_passive_evidence_file(
            COMMITTED_MANIFEST_PATH,
            label="committed dataset source manifest",
        )
        manifest = parse_dataset_source_manifest(manifest_payload)
        cls._require_manifest_identity(
            normalized,
            manifest,
            manifest_payload=manifest_payload,
        )
        return cls(
            normalized,
            slice_sha256=actual_slice_sha256,
            evidence_status="content_pinned_with_manifest_identity",
            cursor_signing_key=cursor_signing_key,
        )

    def summary(self) -> CorpusSummaryDto:
        support_counts = Counter(
            disposition.support_status.value for disposition in self._normalized.task_dispositions
        )
        constraint_counts = Counter(row.type for row in self._normalized.constraints)
        eligible = sum(
            disposition.projection_status in {ProjectionStatus.ELIGIBLE, ProjectionStatus.PROJECTED}
            for disposition in self._normalized.task_dispositions
        )
        return CorpusSummaryDto(
            source=self._source,
            coordinate_unit=self._unit,
            task_count=len(self._normalized.tasks),
            part_count=len(self._normalized.parts),
            shape_count=len(self._normalized.shapes),
            constraint_count=len(self._normalized.constraints),
            support_status_counts=tuple(
                NamedCountDto(name=name, count=count)
                for name, count in sorted(support_counts.items())
            ),
            constraint_type_counts=tuple(
                NamedCountDto(name=name, count=count)
                for name, count in sorted(constraint_counts.items())
            ),
            solve_capability=CorpusSolveCapabilityDto(
                eligible_task_count=eligible,
                blocked_task_count=len(self._normalized.tasks) - eligible,
                directly_supported_task_count=sum(
                    disposition.support_status is SupportStatus.DIRECTLY_SUPPORTED
                    for disposition in self._normalized.task_dispositions
                ),
            ),
        )

    def _task_summary(self, task: TaskSourceRow) -> TaskSummaryDto:
        parts = self._parts_by_task[task.tasks_index]
        constraints = self._constraints_by_task[task.tasks_index]
        diagnostics = _s1_projection_diagnostics(constraints)
        return TaskSummaryDto(
            task=_source_dto(task),
            tasks_index=_safe_int(task.tasks_index, label="tasks_index"),
            part_count=len(parts),
            shape_count=len({part.shape_hash for part in parts}),
            constraint_count=len(constraints),
            constraint_types=tuple(sorted({row.type for row in constraints})),
            solve_capability=_capability(self._dispositions[task.tasks_index], diagnostics),
        )

    @staticmethod
    def _validate_query(
        *,
        limit: int,
        status: SupportStatus | str | None,
        constraint_type: str | None,
        task_id: int | None,
        min_parts: int | None,
        max_parts: int | None,
    ) -> tuple[SupportStatus | None, str | None]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise InvalidTaskQueryError("limit must be an integer from 1 through 50")
        try:
            parsed_status = SupportStatus(status) if status is not None else None
        except ValueError as error:
            raise InvalidTaskQueryError("status is not a recognized support status") from error
        if constraint_type is not None:
            if not isinstance(constraint_type, str) or not constraint_type.strip():
                raise InvalidTaskQueryError("constraint_type must be a nonempty string")
            if constraint_type != constraint_type.strip() or len(constraint_type) > 80:
                raise InvalidTaskQueryError("constraint_type must be trimmed and at most 80 chars")
        for name, value in (
            ("task_id", task_id),
            ("min_parts", min_parts),
            ("max_parts", max_parts),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_SAFE_JSON_INTEGER
            ):
                raise InvalidTaskQueryError(f"{name} must be a browser-safe nonnegative integer")
        if min_parts is not None and max_parts is not None and min_parts > max_parts:
            raise InvalidTaskQueryError("min_parts cannot exceed max_parts")
        return parsed_status, constraint_type

    def _filter_digest(
        self,
        *,
        status: SupportStatus | None,
        constraint_type: str | None,
        task_id: int | None,
        min_parts: int | None,
        max_parts: int | None,
    ) -> str:
        payload = json.dumps(
            {
                "constraint_type": constraint_type,
                "max_parts": max_parts,
                "min_parts": min_parts,
                "status": status.value if status is not None else None,
                "task_id": task_id,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _encode_cursor(self, *, after: tuple[int, int], filter_digest: str) -> str:
        payload = json.dumps(
            {
                "after": list(after),
                "filters": filter_digest,
                "slice": self._slice_sha256,
                "v": 1,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        signature = hmac.new(self._cursor_key, payload, hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def _decode_cursor(
        self,
        cursor: str,
        *,
        filter_digest: str,
    ) -> tuple[int, int]:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
            raise InvalidCursorError("cursor must be one bounded opaque string")
        try:
            body, signature = cursor.split(".", maxsplit=1)
            if len(signature) != 64 or any(char not in "0123456789abcdef" for char in signature):
                raise ValueError("invalid signature")
            padding = "=" * (-len(body) % 4)
            payload = base64.b64decode(body + padding, altchars=b"-_", validate=True)
        except (ValueError, UnicodeError) as error:
            raise InvalidCursorError("cursor is malformed or tampered with") from error
        expected = hmac.new(self._cursor_key, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCursorError("cursor is malformed or tampered with")
        try:
            decoded = decode_strict_json_bytes(
                payload,
                label="task cursor",
                max_bytes=512,
            )
        except PassiveEvidenceError as error:
            raise InvalidCursorError("cursor is malformed or tampered with") from error
        if not isinstance(decoded, dict) or set(decoded) != {"after", "filters", "slice", "v"}:
            raise InvalidCursorError("cursor is malformed or tampered with")
        after = decoded["after"]
        if (
            type(decoded["v"]) is not int
            or decoded["v"] != 1
            or not isinstance(decoded["slice"], str)
            or decoded["slice"] != self._slice_sha256
            or not isinstance(decoded["filters"], str)
            or not isinstance(after, list)
            or len(after) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in after
            )
        ):
            raise InvalidCursorError("cursor is stale or malformed")
        if decoded["filters"] != filter_digest:
            raise InvalidCursorError("cursor cannot be reused with different filters")
        after_key = (after[0], after[1])
        if after_key not in self._task_keys:
            raise InvalidCursorError("cursor does not identify a real task member")
        return after_key

    def _matches_filters(
        self,
        task: TaskSourceRow,
        *,
        status: SupportStatus | None,
        constraint_type: str | None,
        task_id: int | None,
        min_parts: int | None,
        max_parts: int | None,
    ) -> bool:
        parts = self._parts_by_task[task.tasks_index]
        constraints = self._constraints_by_task[task.tasks_index]
        disposition = self._dispositions[task.tasks_index]
        if status is not None and disposition.support_status is not status:
            return False
        if constraint_type is not None and all(row.type != constraint_type for row in constraints):
            return False
        if task_id is not None and task.tasks_index != task_id:
            return False
        if min_parts is not None and len(parts) < min_parts:
            return False
        return max_parts is None or len(parts) <= max_parts

    def list_tasks(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        status: SupportStatus | str | None = None,
        constraint_type: str | None = None,
        task_id: int | None = None,
        min_parts: int | None = None,
        max_parts: int | None = None,
    ) -> TaskPageDto:
        parsed_status, parsed_constraint_type = self._validate_query(
            limit=limit,
            status=status,
            constraint_type=constraint_type,
            task_id=task_id,
            min_parts=min_parts,
            max_parts=max_parts,
        )
        filter_digest = self._filter_digest(
            status=parsed_status,
            constraint_type=parsed_constraint_type,
            task_id=task_id,
            min_parts=min_parts,
            max_parts=max_parts,
        )
        if cursor is None:
            after = (-1, -1)
        else:
            after = self._decode_cursor(cursor, filter_digest=filter_digest)
            if not self._matches_filters(
                self._tasks_by_key[after],
                status=parsed_status,
                constraint_type=parsed_constraint_type,
                task_id=task_id,
                min_parts=min_parts,
                max_parts=max_parts,
            ):
                raise InvalidCursorError(
                    "cursor does not identify a member of the exact filtered result"
                )
        matches: list[TaskSourceRow] = []
        for task in self._tasks:
            task_key = (task.source_row_index, task.tasks_index)
            if task_key <= after:
                continue
            if not self._matches_filters(
                task,
                status=parsed_status,
                constraint_type=parsed_constraint_type,
                task_id=task_id,
                min_parts=min_parts,
                max_parts=max_parts,
            ):
                continue
            matches.append(task)

        selected = matches[:limit]
        next_cursor = None
        if len(matches) > limit:
            next_cursor = self._encode_cursor(
                after=(selected[-1].source_row_index, selected[-1].tasks_index),
                filter_digest=filter_digest,
            )
        return TaskPageDto(
            items=tuple(self._task_summary(task) for task in selected),
            next_cursor=next_cursor,
        )

    def task_detail(self, tasks_index: int) -> TaskDetailDto:
        if isinstance(tasks_index, bool) or not isinstance(tasks_index, int) or tasks_index < 0:
            raise TaskNotFoundError("task was not found")
        task = self._tasks_by_id.get(tasks_index)
        if task is None:
            raise TaskNotFoundError(f"task {tasks_index} was not found")
        parts = self._parts_by_task[tasks_index]
        referenced_hashes = {part.shape_hash for part in parts}
        shapes = tuple(
            shape for shape in self._normalized.shapes if shape.shape_hash in referenced_hashes
        )
        geometry = tuple(self._geometry_by_hash[shape.shape_hash] for shape in shapes)
        constraints = self._constraints_by_task[tasks_index]
        return TaskDetailDto(
            source=self._source,
            coordinate_unit=self._unit,
            summary=self._task_summary(task),
            parts=tuple(_part_dto(part) for part in parts),
            shapes=tuple(_shape_dto(shape) for shape in shapes),
            constraints=tuple(_constraint_dto(row) for row in constraints),
            constraint_value_columns=self._normalized.constraint_value_columns,
            derived_geometry=tuple(_geometry_dto(row) for row in geometry),
            provenance=tuple(_provenance_dto(group) for group in self._normalized.provenance),
            s1_projection_diagnostics=_s1_projection_diagnostics(constraints),
        )

    def project_problem(
        self,
        tasks_index: int,
        *,
        acknowledged_assumption_codes: tuple[str, ...],
    ) -> StripPackingProblem:
        """Project only an eligible task with its exact assumptions acknowledged."""

        return self.project_task(
            tasks_index,
            mode=ProjectionMode.SOURCE_AS_RECORDED,
            acknowledged_assumption_codes=acknowledged_assumption_codes,
            acknowledged_intervention_codes=(),
        ).problem

    def project_task(
        self,
        tasks_index: int,
        *,
        mode: ProjectionMode,
        acknowledged_assumption_codes: tuple[str, ...],
        acknowledged_intervention_codes: tuple[str, ...],
    ) -> ProjectedTask:
        """Return one server-owned projection only after exact option acknowledgement."""

        task = self._tasks_by_id.get(tasks_index)
        if task is None:
            raise TaskNotFoundError(f"task {tasks_index} was not found")
        if not isinstance(acknowledged_assumption_codes, tuple) or any(
            not isinstance(code, str) for code in acknowledged_assumption_codes
        ):
            raise TaskNotSolvableError("assumption acknowledgement must be an exact tuple")
        if not isinstance(acknowledged_intervention_codes, tuple) or any(
            not isinstance(code, str) for code in acknowledged_intervention_codes
        ):
            raise TaskNotSolvableError("intervention acknowledgement must be an exact tuple")
        try:
            parsed_mode = ProjectionMode(mode)
        except (TypeError, ValueError) as error:
            raise TaskNotSolvableError("projection mode is not available for this task") from error
        summary = self._task_summary(task)
        if not summary.solve_capability.can_solve:
            raise TaskNotSolvableError(f"task {tasks_index} is blocked from solving")
        option = next(
            (
                item
                for item in summary.solve_capability.projection_options
                if item.mode is parsed_mode
            ),
            None,
        )
        if option is None:
            raise TaskNotSolvableError("projection mode is not available for this task")
        if acknowledged_assumption_codes != option.assumption_codes:
            raise TaskNotSolvableError(
                f"task {tasks_index} requires exact acknowledgement of its assumptions"
            )
        if acknowledged_intervention_codes != option.intervention_codes:
            raise TaskNotSolvableError(
                f"task {tasks_index} requires exact acknowledgement of its interventions"
            )
        from yieldforge.datasets.projection import project_task

        projected = project_task(self._normalized, tasks_index, mode=parsed_mode)
        if (
            projected.projection.assumption_codes != option.assumption_codes
            or projected.projection.intervention_codes != option.intervention_codes
        ):
            raise TaskNotSolvableError("projection evidence does not match the selected option")
        return projected


__all__ = [
    "BOUND_AUDIT_REPORT_PATH",
    "COMMITTED_MANIFEST_PATH",
    "COMMITTED_SLICE_SHA256",
    "COMMITTED_SLICE_PATH",
    "ConstraintDto",
    "CoordinateUnitDto",
    "CorpusQueryError",
    "CorpusQueryService",
    "CorpusService",
    "CorpusSolveCapabilityDto",
    "CorpusSourceDto",
    "CorpusSummaryDto",
    "DerivedShapeGeometryDto",
    "InvalidCursorError",
    "InvalidTaskQueryError",
    "NamedCountDto",
    "PartDto",
    "ProjectionOptionDto",
    "ProvenanceDto",
    "S1ProjectionDiagnosticsDto",
    "ShapeDto",
    "SolveCapabilityDto",
    "TaskDetailDto",
    "TaskNotFoundError",
    "TaskNotSolvableError",
    "TaskPageDto",
    "TaskSourceDto",
    "TaskSummaryDto",
]
