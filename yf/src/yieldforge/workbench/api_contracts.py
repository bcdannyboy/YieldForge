"""Strict, browser-facing contracts for the local research workbench API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from yieldforge.domain import (
    CandidateReportType,
    Point,
    ProjectionMode,
    SourceTaskBinding,
)
from yieldforge.order_books.domain import (
    EconomicFields,
    FieldFamilyProvenance,
    GenerationRegime,
    GenerationRequest,
    GeneratorIdentity,
    MaterialAssignment,
    OrderBookDiagnostics,
    OrderBookManifest,
    SourceSliceIdentity,
)
from yieldforge.workbench.contracts import JobEventKind, JobStatus


class ApiContract(BaseModel):
    """Immutable, extra-forbidding, finite public transport model."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ApiError(ApiContract):
    schema_version: Literal["yieldforge.api-error.v1"] = "yieldforge.api-error.v1"
    code: StrictStr = Field(min_length=1, max_length=80)
    message: StrictStr = Field(min_length=1, max_length=200)
    details: dict[str, Any] | None = None


class CreateSolverJobRequest(ApiContract):
    schema_version: Literal["yieldforge.api-solver-job-request.v2"] = (
        "yieldforge.api-solver-job-request.v2"
    )
    tasks_index: StrictInt = Field(ge=0)
    projection_mode: ProjectionMode
    acknowledged_assumption_codes: tuple[StrictStr, ...] = ()
    acknowledged_intervention_codes: tuple[StrictStr, ...] = ()
    seed: StrictInt
    total_computation_time: StrictInt = Field(gt=0, le=10)
    early_termination: StrictBool = False
    min_items_separation: StrictFloat | None = Field(default=None, ge=0)
    max_runtime_seconds: StrictFloat = Field(gt=0, le=10)

    @field_validator("projection_mode", mode="before")
    @classmethod
    def accept_json_projection_mode(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return ProjectionMode(value)
            except ValueError:
                return value
        return value

    @field_validator("acknowledged_assumption_codes", "acknowledged_intervention_codes")
    @classmethod
    def require_sorted_unique_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("acknowledged assumption codes must be sorted and unique")
        return value

    @field_validator(
        "acknowledged_assumption_codes",
        "acknowledged_intervention_codes",
        mode="before",
    )
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        # FastAPI validates an already-decoded JSON body, where arrays are lists.
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def require_solver_time_within_hard_runtime(self) -> Self:
        if self.total_computation_time > self.max_runtime_seconds:
            raise ValueError("total_computation_time must fit within max_runtime_seconds")
        return self


class CreateMatchedSolverJobsRequest(ApiContract):
    schema_version: Literal["yieldforge.api-matched-solver-jobs-request.v1"] = (
        "yieldforge.api-matched-solver-jobs-request.v1"
    )
    tasks_index: StrictInt = Field(ge=0)
    acknowledged_assumption_codes: tuple[StrictStr, ...] = ()
    acknowledged_intervention_codes: tuple[StrictStr, ...] = ()
    seed: StrictInt
    total_computation_time: StrictInt = Field(gt=0, le=10)
    early_termination: StrictBool = False
    min_items_separation: StrictFloat | None = Field(default=None, ge=0)
    max_runtime_seconds: StrictFloat = Field(gt=0, le=10)

    @field_validator("acknowledged_assumption_codes", "acknowledged_intervention_codes")
    @classmethod
    def require_sorted_unique_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("acknowledged codes must be sorted and unique")
        return value

    @field_validator(
        "acknowledged_assumption_codes",
        "acknowledged_intervention_codes",
        mode="before",
    )
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def require_solver_time_within_hard_runtime(self) -> Self:
        if self.total_computation_time > self.max_runtime_seconds:
            raise ValueError("total_computation_time must fit within max_runtime_seconds")
        return self


class JobView(ApiContract):
    schema_version: Literal["yieldforge.api-job.v1"] = "yieldforge.api-job.v1"
    job_id: StrictStr = Field(min_length=1)
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    latest_event_id: StrictInt = Field(ge=1)
    candidate_count: StrictInt = Field(ge=0)
    source_task_binding: SourceTaskBinding | None = None
    experiment_pair_id: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    experiment_arm: ProjectionMode | None = None
    archive_available: StrictBool
    error_code: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    error_message: StrictStr | None = Field(default=None, min_length=1, max_length=200)


class MatchedSolverJobsView(ApiContract):
    schema_version: Literal["yieldforge.api-matched-solver-jobs.v1"] = (
        "yieldforge.api-matched-solver-jobs.v1"
    )
    experiment_pair_id: StrictStr = Field(min_length=1, max_length=80)
    source_as_recorded: JobView
    force_flip_x_zero: JobView

    @model_validator(mode="after")
    def require_exact_pair_identity(self) -> Self:
        if (
            self.source_as_recorded.experiment_pair_id != self.experiment_pair_id
            or self.force_flip_x_zero.experiment_pair_id != self.experiment_pair_id
            or self.source_as_recorded.experiment_arm is not ProjectionMode.SOURCE_AS_RECORDED
            or self.force_flip_x_zero.experiment_arm is not ProjectionMode.FORCE_FLIP_X_ZERO
        ):
            raise ValueError("matched jobs must expose the exact pair arms")
        return self


class PublicJobEvent(ApiContract):
    schema_version: Literal["yieldforge.api-job-event.v1"] = "yieldforge.api-job-event.v1"
    job_id: StrictStr = Field(min_length=1)
    sequence: StrictInt = Field(ge=1)
    occurred_at: datetime
    kind: JobEventKind
    status: JobStatus | None = None
    phase: Literal["solving"] | None = None
    candidate_id: StrictStr | None = Field(default=None, min_length=1)
    candidate_count: StrictInt = Field(ge=0)
    archive_available: StrictBool = False
    error_code: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    error_message: StrictStr | None = Field(default=None, min_length=1, max_length=200)


class CandidateSummary(ApiContract):
    candidate_id: StrictStr = Field(min_length=1)
    report_type: CandidateReportType
    seed: StrictInt
    width: StrictFloat = Field(gt=0)
    density: StrictFloat = Field(ge=0, le=1)
    placement_count: StrictInt = Field(gt=0)


class CandidatePage(ApiContract):
    schema_version: Literal["yieldforge.api-candidate-page.v1"] = "yieldforge.api-candidate-page.v1"
    items: tuple[CandidateSummary, ...]
    next_cursor: StrictStr | None = None


class SheetGeometry(ApiContract):
    length: StrictFloat = Field(gt=0)
    width: StrictFloat = Field(gt=0)


class PlacementGeometry(ApiContract):
    part_id: StrictStr = Field(min_length=1)
    rotation: StrictFloat
    translation: Point
    projected_shape: tuple[Point, ...] = Field(min_length=4)
    svg_points: tuple[Point, ...] = Field(min_length=4)


class CandidateGeometry(ApiContract):
    schema_version: Literal["yieldforge.api-candidate-geometry.v1"] = (
        "yieldforge.api-candidate-geometry.v1"
    )
    candidate: CandidateSummary
    sheet: SheetGeometry
    provenance: Literal["derived"] = "derived"
    placements: tuple[PlacementGeometry, ...] = Field(min_length=1)


class TaskJobPage(ApiContract):
    schema_version: Literal["yieldforge.api-task-jobs.v1"] = "yieldforge.api-task-jobs.v1"
    items: tuple[JobView, ...]


class CompletedRunSettings(ApiContract):
    seed: StrictInt
    total_computation_time: StrictInt = Field(gt=0)
    num_workers: Literal[1]
    early_termination: StrictBool
    min_items_separation: StrictFloat | None = Field(default=None, ge=0)
    max_runtime_seconds: StrictFloat = Field(gt=0, le=10)


class CompletedArchiveIdentity(ApiContract):
    schema_version: Literal["yieldforge.candidate-archive.v1"] = "yieldforge.candidate-archive.v1"
    batch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class CompletedRunView(ApiContract):
    schema_version: Literal["yieldforge.api-completed-run.v1"] = "yieldforge.api-completed-run.v1"
    job: JobView
    settings: CompletedRunSettings
    archive: CompletedArchiveIdentity


class CompletedRunPage(ApiContract):
    schema_version: Literal["yieldforge.api-completed-run-page.v1"] = (
        "yieldforge.api-completed-run-page.v1"
    )
    items: tuple[CompletedRunView, ...]


class GenerateOrderBookInput(ApiContract):
    """The only caller-controlled inputs accepted by the order-book generator."""

    regime: GenerationRegime
    seed: StrictInt = Field(ge=-(2**53 - 1), le=2**53 - 1)
    event_count: StrictInt = Field(ge=2, le=100)
    starts_at: datetime
    interval_minutes: StrictInt = Field(gt=0, le=525_600)

    @field_validator("regime", mode="before")
    @classmethod
    def accept_json_regime(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return GenerationRegime(value)
            except ValueError:
                return value
        return value

    @field_validator("starts_at", mode="before")
    @classmethod
    def accept_iso_json_datetime(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    @field_validator("starts_at")
    @classmethod
    def require_aware_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at must be timezone-aware")
        return value.astimezone(UTC)


class PublicSourceTaskReference(ApiContract):
    """Source composition with opaque signed shape hashes safe for JavaScript."""

    dataset_id: StrictStr = Field(min_length=1)
    tasks_index: StrictInt = Field(ge=0)
    task_source_row_index: StrictInt = Field(ge=0)
    part_ids: tuple[StrictInt, ...] = Field(min_length=1)
    part_source_row_indices: tuple[StrictInt, ...] = Field(min_length=1)
    shape_hashes: tuple[StrictStr, ...] = Field(min_length=1)

    @field_validator("shape_hashes")
    @classmethod
    def require_canonical_decimal_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(raw != str(int(raw)) or raw == "-0" for raw in value):
            raise ValueError("shape hashes must be canonical decimal strings")
        return value


class PublicOrderEvent(ApiContract):
    sequence: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(pattern=r"^evt-[0-9a-f]{20}$")
    occurred_at: datetime
    source_task: PublicSourceTaskReference
    material: MaterialAssignment
    economics: EconomicFields


_ANALYSIS_WARNING = (
    "Analysis-only full manifest; future events and generator-only regime labels are excluded "
    "from baseline-facing views."
)


class OrderBookView(ApiContract):
    """Verified full-manifest analysis view with an explicit leakage warning."""

    schema_version: Literal["yieldforge.api-order-book.v1"] = "yieldforge.api-order-book.v1"
    manifest_schema_version: Literal["yieldforge.order-book.v1"] = "yieldforge.order-book.v1"
    analysis_scope: Literal["analysis_only_full_manifest"] = "analysis_only_full_manifest"
    analysis_warning: Literal[_ANALYSIS_WARNING] = _ANALYSIS_WARNING
    order_book_id: StrictStr = Field(pattern=r"^yfob-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generator: GeneratorIdentity
    source_slice: SourceSliceIdentity
    request: GenerationRequest
    field_provenance: tuple[FieldFamilyProvenance, ...] = Field(min_length=6, max_length=6)
    events: tuple[PublicOrderEvent, ...] = Field(min_length=2, max_length=100)
    diagnostics: OrderBookDiagnostics

    @classmethod
    def from_manifest(cls, manifest: OrderBookManifest) -> OrderBookView:
        return cls(
            order_book_id=manifest.order_book_id,
            content_sha256=manifest.content_sha256,
            generator=manifest.generator,
            source_slice=manifest.source_slice,
            request=manifest.request,
            field_provenance=manifest.field_provenance,
            events=tuple(
                PublicOrderEvent(
                    sequence=event.sequence,
                    event_id=event.event_id,
                    occurred_at=event.occurred_at,
                    source_task=PublicSourceTaskReference(
                        dataset_id=event.source_task.dataset_id,
                        tasks_index=event.source_task.tasks_index,
                        task_source_row_index=event.source_task.task_source_row_index,
                        part_ids=event.source_task.part_ids,
                        part_source_row_indices=event.source_task.part_source_row_indices,
                        shape_hashes=tuple(str(value) for value in event.source_task.shape_hashes),
                    ),
                    material=event.material,
                    economics=event.economics,
                )
                for event in manifest.events
            ),
            diagnostics=manifest.diagnostics,
        )


class OrderBookPage(ApiContract):
    schema_version: Literal["yieldforge.api-order-book-page.v1"] = (
        "yieldforge.api-order-book-page.v1"
    )
    items: tuple[OrderBookView, ...]
    next_cursor: StrictStr | None = Field(default=None, min_length=1, max_length=512)


__all__ = [
    "ApiError",
    "CandidateGeometry",
    "CandidatePage",
    "CandidateSummary",
    "CreateSolverJobRequest",
    "JobView",
    "GenerateOrderBookInput",
    "OrderBookPage",
    "OrderBookView",
    "PlacementGeometry",
    "PublicJobEvent",
    "SheetGeometry",
    "TaskJobPage",
]
