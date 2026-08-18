"""Strict, browser-facing contracts for the local research workbench API."""

from __future__ import annotations

from datetime import datetime
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
    SourceTaskBinding,
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
    schema_version: Literal["yieldforge.api-solver-job-request.v1"] = (
        "yieldforge.api-solver-job-request.v1"
    )
    tasks_index: StrictInt = Field(ge=0)
    acknowledged_assumption_codes: tuple[StrictStr, ...] = ()
    seed: StrictInt
    total_computation_time: StrictInt = Field(gt=0, le=10)
    early_termination: StrictBool = False
    min_items_separation: StrictFloat | None = Field(default=None, ge=0)
    max_runtime_seconds: StrictFloat = Field(gt=0, le=10)

    @field_validator("acknowledged_assumption_codes")
    @classmethod
    def require_sorted_unique_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("acknowledged assumption codes must be sorted and unique")
        return value

    @field_validator("acknowledged_assumption_codes", mode="before")
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


class JobView(ApiContract):
    schema_version: Literal["yieldforge.api-job.v1"] = "yieldforge.api-job.v1"
    job_id: StrictStr = Field(min_length=1)
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    latest_event_id: StrictInt = Field(ge=1)
    candidate_count: StrictInt = Field(ge=0)
    source_task_binding: SourceTaskBinding | None = None
    archive_available: StrictBool
    error_code: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    error_message: StrictStr | None = Field(default=None, min_length=1, max_length=200)


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


__all__ = [
    "ApiError",
    "CandidateGeometry",
    "CandidatePage",
    "CandidateSummary",
    "CreateSolverJobRequest",
    "JobView",
    "PlacementGeometry",
    "PublicJobEvent",
    "SheetGeometry",
    "TaskJobPage",
]
