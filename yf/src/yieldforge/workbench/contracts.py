"""Strict contracts for isolated solver workers and durable jobs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    ProjectionMode,
    SourceTaskBinding,
    SpyrrowRunConfig,
    SpyrrowRunResult,
    StripPackingProblem,
)


class WorkbenchContract(BaseModel):
    """Strict and immutable persisted workbench contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SolveRequest(WorkbenchContract):
    """Complete, bounded input passed to exactly one solver subprocess."""

    schema_version: Literal["yieldforge.solve-request.v1"] = "yieldforge.solve-request.v1"
    problem: StripPackingProblem
    config: SpyrrowRunConfig
    max_runtime_seconds: float = Field(gt=0, le=10)
    source_task_binding: SourceTaskBinding | None = None
    experiment_pair_id: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    experiment_arm: ProjectionMode | None = None

    @model_validator(mode="after")
    def require_bounded_single_worker(self) -> Self:
        if self.config.num_workers != 1:
            raise ValueError("config.num_workers must equal one")
        if self.config.total_computation_time > self.max_runtime_seconds:
            raise ValueError("config.total_computation_time must fit within max_runtime_seconds")
        if (self.experiment_pair_id is None) is not (self.experiment_arm is None):
            raise ValueError("experiment pair id and arm must be provided together")
        if self.experiment_arm is not None:
            projection = (
                self.source_task_binding.solver_projection
                if self.source_task_binding is not None
                else None
            )
            if projection is None or projection.mode is not self.experiment_arm:
                raise ValueError("experiment arm must match the source solver projection")
        return self


class WorkerMessageKind(StrEnum):
    PHASE = "phase"
    CANDIDATE = "candidate"
    COMPLETE = "complete"
    FAILURE = "failure"


class WorkerMessage(WorkbenchContract):
    """One newline-delimited message emitted by the disposable worker."""

    schema_version: Literal["yieldforge.solver-worker-message.v1"] = (
        "yieldforge.solver-worker-message.v1"
    )
    kind: WorkerMessageKind
    phase: Literal["solving"] | None = None
    candidate: Candidate | None = None
    result: SpyrrowRunResult | None = None
    error_code: Literal["solver_failure"] | None = None
    error_message: Literal["solver worker failed"] | None = None

    @model_validator(mode="after")
    def require_kind_payload(self) -> Self:
        present = {
            "phase": self.phase is not None,
            "candidate": self.candidate is not None,
            "result": self.result is not None,
            "error": self.error_code is not None or self.error_message is not None,
        }
        required = {
            WorkerMessageKind.PHASE: {"phase"},
            WorkerMessageKind.CANDIDATE: {"candidate"},
            WorkerMessageKind.COMPLETE: {"result"},
            WorkerMessageKind.FAILURE: {"error"},
        }[self.kind]
        actual = {name for name, is_present in present.items() if is_present}
        if actual != required:
            raise ValueError(f"{self.kind.value} message has an invalid payload")
        if self.kind is WorkerMessageKind.FAILURE and (
            self.error_code is None or self.error_message is None
        ):
            raise ValueError("failure message requires both sanitized error fields")
        return self


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    COMPLETED = "completed"


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.CANCELLED,
        JobStatus.TIMED_OUT,
        JobStatus.FAILED,
        JobStatus.COMPLETED,
    }
)


class JobEventKind(StrEnum):
    STATUS = "status"
    PHASE = "phase"
    CANDIDATE = "candidate"
    TERMINAL = "terminal"


class JobEvent(WorkbenchContract):
    """One sequenced, replayable observation from a solver job."""

    schema_version: Literal["yieldforge.job-event.v1"] = "yieldforge.job-event.v1"
    job_id: str = Field(min_length=1)
    sequence: StrictInt = Field(ge=1)
    occurred_at: datetime
    kind: JobEventKind
    status: JobStatus | None = None
    phase: Literal["solving"] | None = None
    candidate: Candidate | None = None
    candidate_count: StrictInt = Field(default=0, ge=0)
    batch: CandidateBatch | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=80)
    error_message: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_event_shape(self) -> Self:
        if self.kind is JobEventKind.STATUS:
            valid = self.status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING}
            valid = valid and self.phase is None and self.candidate is None and self.batch is None
        elif self.kind is JobEventKind.PHASE:
            valid = self.phase == "solving"
            valid = valid and self.status is JobStatus.RUNNING
            valid = valid and self.candidate is None and self.batch is None
        elif self.kind is JobEventKind.CANDIDATE:
            valid = self.candidate is not None and self.status is JobStatus.RUNNING
            valid = valid and self.phase is None and self.batch is None
        else:
            valid = self.status in TERMINAL_JOB_STATUSES
            valid = valid and self.phase is None and self.candidate is None
            valid = valid and (self.status is JobStatus.COMPLETED) == (self.batch is not None)
        if not valid:
            raise ValueError(f"{self.kind.value} event has an invalid payload")
        if self.status is JobStatus.FAILED:
            if self.error_code is None or self.error_message is None:
                raise ValueError("failed terminal event requires sanitized error fields")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("error fields are valid only for failed jobs")
        return self


class JobTerminalMetadata(WorkbenchContract):
    """Durable terminal truth written once after a worker can no longer run."""

    schema_version: Literal["yieldforge.job-terminal.v1"] = "yieldforge.job-terminal.v1"
    job_id: str = Field(min_length=1)
    status: JobStatus
    finished_at: datetime
    final_sequence: StrictInt = Field(ge=1)
    candidate_count: StrictInt = Field(ge=0)
    archive_path: str | None = Field(default=None, min_length=1)
    error_code: str | None = Field(default=None, min_length=1, max_length=80)
    error_message: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_terminal_truth(self) -> Self:
        if self.status not in TERMINAL_JOB_STATUSES:
            raise ValueError("terminal metadata requires a terminal status")
        if (self.status is JobStatus.COMPLETED) != (self.archive_path is not None):
            raise ValueError("only completed jobs may name an archive")
        if self.status is JobStatus.FAILED:
            if self.error_code is None or self.error_message is None:
                raise ValueError("failed terminal metadata requires sanitized error fields")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("error fields are valid only for failed jobs")
        return self


class JobSnapshot(WorkbenchContract):
    """Current query view of one persisted solver job."""

    schema_version: Literal["yieldforge.job-snapshot.v1"] = "yieldforge.job-snapshot.v1"
    job_id: str = Field(min_length=1)
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    latest_sequence: StrictInt = Field(ge=1)
    candidate_count: StrictInt = Field(ge=0)
    worker_pid: StrictInt | None = Field(default=None, gt=0)
    archive_path: str | None = Field(default=None, min_length=1)
    source_task_binding: SourceTaskBinding | None = None
    experiment_pair_id: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    experiment_arm: ProjectionMode | None = None
    config: SpyrrowRunConfig
    max_runtime_seconds: StrictFloat = Field(gt=0, le=10)
    error_code: str | None = Field(default=None, min_length=1, max_length=80)
    error_message: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_experiment_identity(self) -> Self:
        if (self.experiment_pair_id is None) is not (self.experiment_arm is None):
            raise ValueError("experiment pair id and arm must be provided together")
        if self.experiment_arm is not None:
            projection = (
                self.source_task_binding.solver_projection
                if self.source_task_binding is not None
                else None
            )
            if projection is None or projection.mode is not self.experiment_arm:
                raise ValueError("experiment arm must match the source solver projection")
        return self
