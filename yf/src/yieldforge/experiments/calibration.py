"""Pure contracts and selection rules for the registered M2 calibration."""

from __future__ import annotations

import json
import math
import os
import secrets
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from yieldforge.datasets.corpus import CorpusSummaryDto, TaskDetailDto
from yieldforge.datasets.passive_report import decode_strict_json_bytes
from yieldforge.domain import ProjectionMode
from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    PureGeometryCalibrationProtocol,
)
from yieldforge.workbench.api_contracts import (
    CandidatePage,
    CompletedRunPage,
    CreateSolverJobRequest,
    JobView,
)
from yieldforge.workbench.contracts import TERMINAL_JOB_STATUSES, JobStatus

_MAX_API_RESPONSE_BYTES = 16 * 1024 * 1024
_REGISTERED_OUTER_RUNTIME_SECONDS = 60.0
_RETRYABLE_FAILURE_CODES = frozenset(
    {"worker_spawn", "worker_protocol", "solver_failure", "supervisor_failure"}
)


class CalibrationCell(FrozenExperimentModel):
    """One pre-registered task, duration, and seed combination."""

    parent_protocol_id: StrictStr
    tasks_index: StrictInt = Field(ge=0)
    projection_mode: Literal["source_as_recorded"] = "source_as_recorded"
    seconds_per_seed: Literal[1, 3, 10]
    seed: Literal[0, 1, 2, 3]

    @property
    def cell_id(self) -> str:
        return (
            f"{self.parent_protocol_id}--task-{self.tasks_index}"
            f"--seconds-{self.seconds_per_seed}--seed-{self.seed}"
        )


class CalibrationCandidateObservation(FrozenExperimentModel):
    """The API-visible fields required by the calibration selector."""

    candidate_id: StrictStr = Field(min_length=1)
    width: StrictFloat = Field(gt=0)
    density: StrictFloat = Field(ge=0, le=1)


class CalibrationCellEvidence(FrozenExperimentModel):
    """Selected terminal evidence for one registered cell."""

    cell: CalibrationCell
    archive_valid: StrictBool
    candidates: tuple[CalibrationCandidateObservation, ...]

    @model_validator(mode="after")
    def require_archive_consistent_candidates(self) -> Self:
        if not self.archive_valid and self.candidates:
            raise ValueError("an invalid archive cannot contribute candidates")
        identifiers = [candidate.candidate_id for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("one cell cannot repeat a candidate ID")
        return self


class CalibrationApiCompletedEvidence(FrozenExperimentModel):
    """Verified public identity of one completed API archive."""

    job_id: StrictStr = Field(min_length=1)
    batch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: tuple[CalibrationCandidateObservation, ...]


def registered_request(
    cell: CalibrationCell,
    assumption_codes: tuple[str, ...],
) -> CreateSolverJobRequest:
    """Build the sole request permitted for one registered calibration cell."""

    return CreateSolverJobRequest(
        tasks_index=cell.tasks_index,
        projection_mode=ProjectionMode.SOURCE_AS_RECORDED,
        acknowledged_assumption_codes=assumption_codes,
        acknowledged_intervention_codes=(),
        seed=cell.seed,
        total_computation_time=cell.seconds_per_seed,
        early_termination=False,
        min_items_separation=None,
        max_runtime_seconds=_REGISTERED_OUTER_RUNTIME_SECONDS,
    )


def _require_job_binding(
    cell: CalibrationCell,
    assumption_codes: tuple[str, ...],
    job: JobView,
) -> None:
    binding = job.source_task_binding
    projection = binding.solver_projection if binding is not None else None
    if (
        binding is None
        or binding.tasks_index != cell.tasks_index
        or binding.acknowledged_assumption_codes != assumption_codes
        or projection is None
        or projection.mode is not ProjectionMode.SOURCE_AS_RECORDED
        or projection.intervention_codes
    ):
        raise ValueError("job does not preserve the registered projection binding")


class CalibrationApiClient:
    """Strict synchronous client for server-owned calibration evidence."""

    def __init__(self, origin: str, *, request_timeout_seconds: float = 10.0) -> None:
        self.origin = origin.rstrip("/")
        if not self.origin.startswith(("http://", "https://")):
            raise ValueError("API origin must use http or https")
        if request_timeout_seconds <= 0:
            raise ValueError("API request timeout must be positive")
        self.request_timeout_seconds = request_timeout_seconds

    def _request_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        payload: BaseModel | None = None,
    ) -> object:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = payload.model_dump_json().encode()
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.origin}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw = response.read(_MAX_API_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise ValueError(f"API request failed with HTTP {error.code}") from error
        except URLError as error:
            raise ConnectionError("API request could not connect") from error
        if len(raw) > _MAX_API_RESPONSE_BYTES:
            raise ValueError("API response exceeds size limit")
        try:
            return decode_strict_json_bytes(
                raw,
                label="calibration API response",
                max_bytes=_MAX_API_RESPONSE_BYTES,
            )
        except Exception as error:
            raise ValueError("API response is not strict JSON") from error

    def _get_model[ModelType: BaseModel](
        self,
        path: str,
        model: type[ModelType],
    ) -> ModelType:
        try:
            payload = self._request_json("GET", path)
            return model.model_validate_json(
                json.dumps(
                    payload,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                strict=True,
            )
        except ValidationError as error:
            raise ValueError("API response failed validation") from error

    def require_corpus(
        self,
        *,
        dataset_id: str,
        catalog_sha256: str,
        task_count: int,
        eligible_task_count: int,
    ) -> CorpusSummaryDto:
        """Require the API to expose the exact registered catalog identity."""

        summary = self._get_model("/api/corpus/summary", CorpusSummaryDto)
        if (
            summary.source.dataset_id != dataset_id
            or summary.source.slice_sha256 != catalog_sha256
            or summary.task_count != task_count
            or summary.solve_capability.eligible_task_count != eligible_task_count
        ):
            raise ValueError("API corpus does not match the registered calibration catalog")
        return summary

    def task_detail(self, tasks_index: int) -> TaskDetailDto:
        return self._get_model(f"/api/tasks/{tasks_index}", TaskDetailDto)

    def submit(self, cell: CalibrationCell, assumption_codes: tuple[str, ...]) -> JobView:
        request = registered_request(cell, assumption_codes)
        return self.submit_request(cell, request)

    def submit_request(
        self,
        cell: CalibrationCell,
        request: CreateSolverJobRequest,
    ) -> JobView:
        if request != registered_request(cell, request.acknowledged_assumption_codes):
            raise ValueError("submission request does not match the registered calibration cell")
        try:
            payload = self._request_json("POST", "/api/solver-jobs", payload=request)
            job = JobView.model_validate_json(
                json.dumps(
                    payload,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                strict=True,
            )
        except ValidationError as error:
            raise ValueError("API response failed validation") from error
        _require_job_binding(cell, request.acknowledged_assumption_codes, job)
        return job

    def job(self, job_id: str) -> JobView:
        return self._get_model(f"/api/solver-jobs/{job_id}", JobView)

    def wait_for_terminal(
        self,
        job_id: str,
        *,
        poll_interval_seconds: float = 0.1,
    ) -> JobView:
        if poll_interval_seconds < 0:
            raise ValueError("poll interval cannot be negative")
        while True:
            job = self.job(job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                return job
            time.sleep(poll_interval_seconds)

    def candidates(
        self,
        job_id: str,
        *,
        expected_seed: int | None = None,
    ) -> tuple[CalibrationCandidateObservation, ...]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_candidates: set[str] = set()
        observations = []
        while True:
            query = {"limit": 100}
            if cursor is not None:
                query["cursor"] = cursor
            page = self._get_model(
                f"/api/solver-jobs/{job_id}/candidates?{urlencode(query)}",
                CandidatePage,
            )
            if page.next_cursor is not None:
                if page.next_cursor in seen_cursors:
                    raise ValueError("candidate cursor repeated")
                seen_cursors.add(page.next_cursor)
            for candidate in page.items:
                if candidate.candidate_id in seen_candidates:
                    raise ValueError("candidate ID repeated across API pages")
                if expected_seed is not None and candidate.seed != expected_seed:
                    raise ValueError("candidate seed does not match the registered cell")
                seen_candidates.add(candidate.candidate_id)
                observations.append(
                    CalibrationCandidateObservation(
                        candidate_id=candidate.candidate_id,
                        width=candidate.width,
                        density=candidate.density,
                    )
                )
            if page.next_cursor is None:
                return tuple(observations)
            cursor = page.next_cursor

    def completed_evidence(
        self,
        cell: CalibrationCell,
        job_id: str,
    ) -> CalibrationApiCompletedEvidence:
        page = self._get_model(
            f"/api/tasks/{cell.tasks_index}/completed-runs?limit=100",
            CompletedRunPage,
        )
        matching = tuple(item for item in page.items if item.job.job_id == job_id)
        if len(matching) != 1:
            raise ValueError("completed run history does not contain exactly one registered job")
        completed = matching[0]
        binding = completed.job.source_task_binding
        projection = binding.solver_projection if binding is not None else None
        settings = completed.settings
        if (
            completed.job.status is not JobStatus.COMPLETED
            or not completed.job.archive_available
            or binding is None
            or binding.tasks_index != cell.tasks_index
            or projection is None
            or projection.mode is not ProjectionMode.SOURCE_AS_RECORDED
            or projection.intervention_codes
            or settings.seed != cell.seed
            or settings.total_computation_time != cell.seconds_per_seed
            or settings.num_workers != 1
            or settings.early_termination
            or settings.min_items_separation is not None
            or settings.max_runtime_seconds != _REGISTERED_OUTER_RUNTIME_SECONDS
        ):
            raise ValueError("completed run does not match the registered calibration cell")
        candidates = self.candidates(job_id, expected_seed=cell.seed)
        if len(candidates) != completed.job.candidate_count:
            raise ValueError("completed candidate count does not match paginated archive")
        return CalibrationApiCompletedEvidence(
            job_id=job_id,
            batch_sha256=completed.archive.batch_sha256,
            candidates=candidates,
        )


class CalibrationRunIdentity(FrozenExperimentModel):
    """Immutable binding between one runtime directory and the registered run."""

    schema_version: Literal["yieldforge.geometry-calibration-run.v1"] = (
        "yieldforge.geometry-calibration-run.v1"
    )
    parent_protocol_id: StrictStr
    parent_protocol_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_id: StrictStr
    catalog_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    api_origin: StrictStr
    registered_cell_ids: tuple[StrictStr, ...] = Field(min_length=1)


class CalibrationAttemptIntent(FrozenExperimentModel):
    schema_version: Literal["yieldforge.geometry-calibration-attempt-intent.v1"] = (
        "yieldforge.geometry-calibration-attempt-intent.v1"
    )
    cell: CalibrationCell
    attempt_number: Literal[1, 2]
    request: CreateSolverJobRequest


class CalibrationAttemptJob(FrozenExperimentModel):
    schema_version: Literal["yieldforge.geometry-calibration-attempt-job.v1"] = (
        "yieldforge.geometry-calibration-attempt-job.v1"
    )
    cell_id: StrictStr
    attempt_number: Literal[1, 2]
    job_id: StrictStr = Field(min_length=1)


class CalibrationAttemptOutcome(FrozenExperimentModel):
    schema_version: Literal["yieldforge.geometry-calibration-attempt-outcome.v1"] = (
        "yieldforge.geometry-calibration-attempt-outcome.v1"
    )
    cell: CalibrationCell
    attempt_number: Literal[1, 2]
    job_id: StrictStr = Field(min_length=1)
    status: JobStatus
    error_code: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    archive_valid: StrictBool
    batch_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidates: tuple[CalibrationCandidateObservation, ...]

    @model_validator(mode="after")
    def require_terminal_evidence_shape(self) -> Self:
        if self.status not in TERMINAL_JOB_STATUSES:
            raise ValueError("attempt outcome requires a terminal job")
        if self.archive_valid:
            if self.status is not JobStatus.COMPLETED or self.batch_sha256 is None:
                raise ValueError("valid archive evidence requires a completed job and hash")
        elif self.batch_sha256 is not None or self.candidates:
            raise ValueError("invalid archive evidence cannot publish archive contents")
        return self


class CalibrationRunResult(FrozenExperimentModel):
    """Complete local runtime evidence and frozen selector output."""

    schema_version: Literal["yieldforge.geometry-calibration-runtime-result.v1"] = (
        "yieldforge.geometry-calibration-runtime-result.v1"
    )
    run: CalibrationRunIdentity
    attempts: tuple[CalibrationAttemptOutcome, ...]
    evidence: tuple[CalibrationCellEvidence, ...]
    evaluation: CalibrationEvaluation


def _canonical_model_bytes(value: BaseModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _write_once(path: Path, value: BaseModel) -> None:
    """Atomically publish one immutable JSON record without replacement."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    data = _canonical_model_bytes(value)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _load_model[ModelType: BaseModel](path: Path, model: type[ModelType]) -> ModelType:
    try:
        return model.model_validate_json(path.read_bytes(), strict=True)
    except (OSError, ValidationError) as error:
        raise ValueError(f"persisted calibration record failed validation: {path.name}") from error


def _require_or_create[ModelType: BaseModel](
    path: Path,
    expected: ModelType,
    model: type[ModelType],
    *,
    label: str,
) -> ModelType:
    if path.exists():
        observed = _load_model(path, model)
        if observed != expected:
            raise ValueError(f"persisted calibration {label} does not match run identity")
        return observed
    try:
        _write_once(path, expected)
    except FileExistsError:
        observed = _load_model(path, model)
        if observed != expected:
            raise ValueError(f"persisted calibration {label} does not match run identity") from None
        return observed
    return expected


def _cell_directory(output_root: Path, cell: CalibrationCell) -> Path:
    return (
        output_root
        / "cells"
        / (f"task-{cell.tasks_index}--seconds-{cell.seconds_per_seed}--seed-{cell.seed}")
    )


def _is_retryable(outcome: CalibrationAttemptOutcome) -> bool:
    return outcome.status is JobStatus.TIMED_OUT or (
        outcome.status is JobStatus.FAILED and outcome.error_code in _RETRYABLE_FAILURE_CODES
    )


def _attempt_outcome(
    *,
    cell: CalibrationCell,
    attempt_number: Literal[1, 2],
    client: CalibrationApiClient,
    cell_directory: Path,
) -> CalibrationAttemptOutcome:
    detail = client.task_detail(cell.tasks_index)
    if (
        detail.summary.tasks_index != cell.tasks_index
        or not detail.summary.solve_capability.can_solve
    ):
        raise ValueError("API task detail does not expose the registered runnable task")
    assumptions = detail.summary.solve_capability.assumption_codes
    request = registered_request(cell, assumptions)
    intent = CalibrationAttemptIntent(
        cell=cell,
        attempt_number=attempt_number,
        request=request,
    )
    intent_path = cell_directory / f"attempt-{attempt_number}-intent.json"
    _require_or_create(intent_path, intent, CalibrationAttemptIntent, label="attempt intent")

    job_path = cell_directory / f"attempt-{attempt_number}-job.json"
    if job_path.exists():
        saved_job = _load_model(job_path, CalibrationAttemptJob)
        if saved_job.cell_id != cell.cell_id or saved_job.attempt_number != attempt_number:
            raise ValueError("persisted calibration job does not match run identity")
    else:
        submitted = client.submit_request(cell, request)
        saved_job = CalibrationAttemptJob(
            cell_id=cell.cell_id,
            attempt_number=attempt_number,
            job_id=submitted.job_id,
        )
        _write_once(job_path, saved_job)

    terminal = client.wait_for_terminal(saved_job.job_id)
    _require_job_binding(cell, assumptions, terminal)
    if terminal.status is JobStatus.COMPLETED:
        try:
            archive = client.completed_evidence(cell, saved_job.job_id)
        except ConnectionError:
            raise
        except ValueError:
            outcome = CalibrationAttemptOutcome(
                cell=cell,
                attempt_number=attempt_number,
                job_id=saved_job.job_id,
                status=terminal.status,
                error_code="archive_evidence_invalid",
                archive_valid=False,
                batch_sha256=None,
                candidates=(),
            )
        else:
            outcome = CalibrationAttemptOutcome(
                cell=cell,
                attempt_number=attempt_number,
                job_id=saved_job.job_id,
                status=terminal.status,
                error_code=None,
                archive_valid=True,
                batch_sha256=archive.batch_sha256,
                candidates=archive.candidates,
            )
    else:
        outcome = CalibrationAttemptOutcome(
            cell=cell,
            attempt_number=attempt_number,
            job_id=saved_job.job_id,
            status=terminal.status,
            error_code=terminal.error_code,
            archive_valid=False,
            batch_sha256=None,
            candidates=(),
        )
    _write_once(cell_directory / f"attempt-{attempt_number}-outcome.json", outcome)
    return outcome


def orchestrate_calibration(
    *,
    protocol: PureGeometryCalibrationProtocol,
    client: CalibrationApiClient,
    output_root: Path,
    progress: Callable[[int, int, CalibrationCellEvidence], None] | None = None,
) -> CalibrationRunResult:
    """Execute or resume the exact registered calibration, one API job at a time."""

    cells = registered_cells(protocol)
    run = CalibrationRunIdentity(
        parent_protocol_id=protocol.protocol_id,
        parent_protocol_sha256=protocol.content_sha256,
        m0_contract_sha256=protocol.references.m0_contract_sha256,
        dataset_id=protocol.references.dataset_id,
        catalog_sha256=protocol.references.catalog_artifact_sha256,
        api_origin=client.origin,
        registered_cell_ids=tuple(cell.cell_id for cell in cells),
    )
    output_root.mkdir(parents=True, exist_ok=True)
    _require_or_create(
        output_root / "run.json",
        run,
        CalibrationRunIdentity,
        label="run identity",
    )

    attempts: list[CalibrationAttemptOutcome] = []
    evidence: list[CalibrationCellEvidence] = []
    for index, cell in enumerate(cells, start=1):
        directory = _cell_directory(output_root, cell)
        directory.mkdir(parents=True, exist_ok=True)
        _require_or_create(
            directory / "cell.json",
            cell,
            CalibrationCell,
            label="cell identity",
        )
        selected: CalibrationAttemptOutcome | None = None
        for attempt_number in (1, 2):
            outcome_path = directory / f"attempt-{attempt_number}-outcome.json"
            if outcome_path.exists():
                outcome = _load_model(outcome_path, CalibrationAttemptOutcome)
                if outcome.cell != cell or outcome.attempt_number != attempt_number:
                    raise ValueError("persisted calibration outcome does not match run identity")
            else:
                outcome = _attempt_outcome(
                    cell=cell,
                    attempt_number=attempt_number,
                    client=client,
                    cell_directory=directory,
                )
            attempts.append(outcome)
            if attempt_number == 1 and _is_retryable(outcome):
                continue
            selected = outcome
            break
        if selected is None:
            raise ValueError("registered retry did not produce a terminal outcome")
        cell_evidence = CalibrationCellEvidence(
            cell=cell,
            archive_valid=selected.archive_valid,
            candidates=selected.candidates,
        )
        evidence.append(cell_evidence)
        if progress is not None:
            progress(index, len(cells), cell_evidence)

    result = CalibrationRunResult(
        run=run,
        attempts=tuple(attempts),
        evidence=tuple(evidence),
        evaluation=evaluate_calibration(protocol, tuple(evidence)),
    )
    result_path = output_root / "runtime-result.json"
    _require_or_create(result_path, result, CalibrationRunResult, label="runtime result")
    return result


class CalibrationDurationSummary(FrozenExperimentModel):
    """Registered-population summary for one solver duration."""

    seconds_per_seed: Literal[1, 3, 10]
    registered_cell_count: StrictInt = Field(ge=0)
    valid_archive_count: StrictInt = Field(ge=0)
    valid_archive_rate_percent: StrictFloat = Field(ge=0, le=100)
    qualifying_task_count: StrictInt = Field(ge=0)
    registered_task_count: StrictInt = Field(ge=0)
    qualifying_task_rate_percent: StrictFloat = Field(ge=0, le=100)


class CalibrationComparison(FrozenExperimentModel):
    """One shorter duration compared with the 10-second reference."""

    seconds_per_seed: Literal[1, 3]
    qualifying_rate_gap_percentage_points: StrictFloat = Field(ge=0)
    median_best_length_degradation_percent: StrictFloat | None = Field(default=None, ge=0)
    p95_best_length_degradation_percent: StrictFloat | None = Field(default=None, ge=0)
    missing_shorter_best_task_ids: tuple[StrictInt, ...]
    registered_cell_count: StrictInt = Field(ge=0)
    valid_archive_count: StrictInt = Field(ge=0)
    valid_archive_rate_percent: StrictFloat = Field(ge=0, le=100)
    passes: StrictBool


class CalibrationEvaluation(FrozenExperimentModel):
    """Pure outcome of applying the frozen budget selector."""

    valid: StrictBool
    selected_seconds_per_seed: Literal[1, 3, 10] | None
    missing_reference_task_ids: tuple[StrictInt, ...]
    duration_summaries: tuple[CalibrationDurationSummary, ...]
    comparisons: tuple[CalibrationComparison, CalibrationComparison]

    @model_validator(mode="after")
    def require_selection_consistent_with_validity(self) -> Self:
        if self.valid is not (self.selected_seconds_per_seed is not None):
            raise ValueError("valid calibration must have exactly one selected duration")
        return self


CalibrationRunResult.model_rebuild()


def registered_cells(
    protocol: PureGeometryCalibrationProtocol,
) -> tuple[CalibrationCell, ...]:
    """Enumerate the exact calibration cells in their frozen execution order."""

    if protocol.confirmation_enabled or protocol.budget.selected_seconds_per_seed is not None:
        raise ValueError("calibration cells require a calibration-pending protocol")
    return tuple(
        CalibrationCell(
            parent_protocol_id=protocol.protocol_id,
            tasks_index=tasks_index,
            seconds_per_seed=seconds,
            seed=seed,
        )
        for tasks_index in protocol.split.calibration_task_ids
        for seconds in protocol.budget.calibration_seconds_per_seed
        for seed in protocol.budget.ordinary_seeds
    )


def nearest_rank_percentile(values: tuple[float, ...], probability: float) -> float:
    """Return the registered nearest-rank order statistic."""

    if not values:
        raise ValueError("nearest-rank percentile requires at least one value")
    if not 0 < probability <= 1:
        raise ValueError("nearest-rank probability must be in (0, 1]")
    ordered = sorted(values)
    rank = math.ceil(probability * len(ordered))
    return ordered[rank - 1]


class _DurationEvaluation:
    def __init__(
        self,
        summary: CalibrationDurationSummary,
        best_by_task: dict[int, float],
    ) -> None:
        self.summary = summary
        self.best_by_task = best_by_task


def _evaluate_duration(
    *,
    seconds: int,
    task_ids: tuple[int, ...],
    evidence: tuple[CalibrationCellEvidence, ...],
    envelope_percent: float,
) -> _DurationEvaluation:
    selected = tuple(item for item in evidence if item.cell.seconds_per_seed == seconds)
    by_task: dict[int, dict[str, CalibrationCandidateObservation]] = defaultdict(dict)
    for item in selected:
        for candidate in item.candidates:
            existing = by_task[item.cell.tasks_index].get(candidate.candidate_id)
            if existing is not None and existing != candidate:
                raise ValueError("one candidate ID has conflicting API observations")
            by_task[item.cell.tasks_index][candidate.candidate_id] = candidate

    best_by_task: dict[int, float] = {}
    qualifying = 0
    envelope_factor = 1 + envelope_percent / 100
    for tasks_index in task_ids:
        candidates = tuple(by_task[tasks_index].values())
        if not candidates:
            continue
        best = min(candidate.width for candidate in candidates)
        best_by_task[tasks_index] = best
        near_tied = sum(candidate.width <= best * envelope_factor for candidate in candidates)
        if near_tied >= 2:
            qualifying += 1

    valid_archives = sum(item.archive_valid for item in selected)
    summary = CalibrationDurationSummary(
        seconds_per_seed=seconds,
        registered_cell_count=len(selected),
        valid_archive_count=valid_archives,
        valid_archive_rate_percent=valid_archives / len(selected) * 100,
        qualifying_task_count=qualifying,
        registered_task_count=len(task_ids),
        qualifying_task_rate_percent=qualifying / len(task_ids) * 100,
    )
    return _DurationEvaluation(summary, best_by_task)


def evaluate_calibration(
    protocol: PureGeometryCalibrationProtocol,
    evidence: tuple[CalibrationCellEvidence, ...],
) -> CalibrationEvaluation:
    """Apply the approved conservative all-task budget selector."""

    expected = registered_cells(protocol)
    expected_by_id = {cell.cell_id: cell for cell in expected}
    observed_by_id = {item.cell.cell_id: item for item in evidence}
    if len(observed_by_id) != len(evidence):
        raise ValueError("calibration evidence repeats a registered cell")
    if set(observed_by_id) != set(expected_by_id):
        raise ValueError("calibration evidence does not cover the exact registered cells")
    if any(observed_by_id[cell_id].cell != cell for cell_id, cell in expected_by_id.items()):
        raise ValueError("calibration evidence changes a registered cell")

    task_ids = protocol.split.calibration_task_ids
    evaluations = {
        seconds: _evaluate_duration(
            seconds=seconds,
            task_ids=task_ids,
            evidence=evidence,
            envelope_percent=protocol.near_tie.primary_envelope_percent,
        )
        for seconds in protocol.budget.calibration_seconds_per_seed
    }
    reference = evaluations[protocol.budget.selector.reference_seconds_per_seed]
    missing_reference = tuple(
        tasks_index for tasks_index in task_ids if tasks_index not in reference.best_by_task
    )
    reference_valid = (
        not missing_reference
        and reference.summary.valid_archive_rate_percent
        >= protocol.budget.selector.minimum_valid_archive_rate_percent
    )

    comparisons = []
    for seconds in (1, 3):
        shorter = evaluations[seconds]
        missing_shorter = tuple(
            tasks_index for tasks_index in task_ids if tasks_index not in shorter.best_by_task
        )
        degradations = []
        for tasks_index in task_ids:
            reference_best = reference.best_by_task.get(tasks_index)
            shorter_best = shorter.best_by_task.get(tasks_index)
            if reference_best is None or shorter_best is None:
                degradations.append(math.inf)
            else:
                degradations.append(
                    max(0.0, (shorter_best - reference_best) / reference_best * 100)
                )
        median = statistics.median(degradations)
        p95 = nearest_rank_percentile(tuple(degradations), 0.95)
        finite_median = median if math.isfinite(median) else None
        finite_p95 = p95 if math.isfinite(p95) else None
        rate_gap = abs(
            shorter.summary.qualifying_task_rate_percent
            - reference.summary.qualifying_task_rate_percent
        )
        passes = (
            reference_valid
            and rate_gap <= protocol.budget.selector.maximum_qualifying_rate_gap_percentage_points
            and finite_median is not None
            and finite_median
            <= protocol.budget.selector.maximum_median_best_length_degradation_percent
            and finite_p95 is not None
            and finite_p95 <= protocol.budget.selector.maximum_p95_best_length_degradation_percent
            and shorter.summary.valid_archive_rate_percent
            >= protocol.budget.selector.minimum_valid_archive_rate_percent
        )
        comparisons.append(
            CalibrationComparison(
                seconds_per_seed=seconds,
                qualifying_rate_gap_percentage_points=rate_gap,
                median_best_length_degradation_percent=finite_median,
                p95_best_length_degradation_percent=finite_p95,
                missing_shorter_best_task_ids=missing_shorter,
                registered_cell_count=shorter.summary.registered_cell_count,
                valid_archive_count=shorter.summary.valid_archive_count,
                valid_archive_rate_percent=shorter.summary.valid_archive_rate_percent,
                passes=passes,
            )
        )

    selected = next(
        (comparison.seconds_per_seed for comparison in comparisons if comparison.passes),
        10 if reference_valid else None,
    )
    return CalibrationEvaluation(
        valid=selected is not None,
        selected_seconds_per_seed=selected,
        missing_reference_task_ids=missing_reference,
        duration_summaries=tuple(
            evaluations[seconds].summary for seconds in protocol.budget.calibration_seconds_per_seed
        ),
        comparisons=tuple(comparisons),
    )
