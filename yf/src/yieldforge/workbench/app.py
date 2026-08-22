"""Thin FastAPI transport for the local YieldForge research workbench."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from yieldforge.datasets.corpus import (
    CorpusQueryService,
    CorpusSummaryDto,
    InvalidCursorError,
    InvalidTaskQueryError,
    TaskDetailDto,
    TaskNotFoundError,
    TaskNotSolvableError,
    TaskPageDto,
)
from yieldforge.datasets.projection import placed_shape_svg_points
from yieldforge.domain import Candidate, SourceTaskBinding, SpyrrowRunConfig
from yieldforge.order_books.domain import GenerationRegime
from yieldforge.workbench.api_contracts import (
    ApiError,
    CandidateGeometry,
    CandidatePage,
    CandidateSummary,
    CreateSolverJobRequest,
    GenerateOrderBookInput,
    JobView,
    OrderBookPage,
    OrderBookView,
    PlacementGeometry,
    PublicJobEvent,
    SheetGeometry,
    TaskJobPage,
)
from yieldforge.workbench.contracts import (
    TERMINAL_JOB_STATUSES,
    JobEvent,
    JobSnapshot,
    JobStatus,
    SolveRequest,
)
from yieldforge.workbench.jobs import ActiveJobError, SolverJobService
from yieldforge.workbench.order_books import (
    InvalidOrderBookCursorError,
    InvalidOrderBookRequestError,
    OrderBookCapacityError,
    OrderBookIntegrityError,
    OrderBookNotFoundError,
    OrderBookService,
)

_SSE_POLL_SECONDS = 0.1
_SSE_REPLAY_CHUNK = 100
_SSE_KEEPALIVE_POLLS = 10


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ApiError(code=code, message=message, details=details)
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def _job_view(snapshot: JobSnapshot) -> JobView:
    return JobView(
        job_id=snapshot.job_id,
        status=snapshot.status,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
        latest_event_id=snapshot.latest_sequence,
        candidate_count=snapshot.candidate_count,
        source_task_binding=snapshot.source_task_binding,
        archive_available=snapshot.status is JobStatus.COMPLETED,
        error_code=snapshot.error_code,
        error_message=snapshot.error_message,
    )


def _public_event(event: JobEvent) -> PublicJobEvent:
    candidate_id = event.candidate.candidate_id if event.candidate is not None else None
    return PublicJobEvent(
        job_id=event.job_id,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        kind=event.kind,
        status=event.status,
        phase=event.phase,
        candidate_id=candidate_id,
        candidate_count=event.candidate_count,
        archive_available=event.status is JobStatus.COMPLETED,
        error_code=event.error_code,
        error_message=event.error_message,
    )


def _sse_record(event: JobEvent) -> str:
    payload = json.dumps(
        _public_event(event).model_dump(mode="json", exclude_none=True),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {event.sequence}\nevent: {event.kind.value}\ndata: {payload}\n\n"


async def _stream_job_events(
    request: Request,
    jobs: SolverJobService,
    job_id: str,
    *,
    after_sequence: int,
    poll_interval_seconds: float = _SSE_POLL_SECONDS,
) -> AsyncIterator[str]:
    """Replay durable events without owning or cancelling the underlying solve."""

    cursor = after_sequence
    empty_polls = 0
    while True:
        if await request.is_disconnected():
            return
        events = jobs.events(job_id, after_sequence=cursor)[:_SSE_REPLAY_CHUNK]
        if events:
            empty_polls = 0
            for event in events:
                cursor = event.sequence
                yield _sse_record(event)
                if event.status in TERMINAL_JOB_STATUSES:
                    return
            continue

        snapshot = jobs.get(job_id)
        if snapshot.status in TERMINAL_JOB_STATUSES and cursor >= snapshot.latest_sequence:
            return
        empty_polls += 1
        if empty_polls >= _SSE_KEEPALIVE_POLLS:
            empty_polls = 0
            yield ": keep-alive\n\n"
        await asyncio.sleep(poll_interval_seconds)


def _parse_last_event_id(value: str | None) -> int:
    if value is None:
        return 0
    if value == "0":
        return 0
    if not value or value[0] == "0" or not value.isascii() or not value.isdecimal():
        raise ValueError("Last-Event-ID must be a canonical nonnegative decimal integer")
    return int(value)


def _candidate_summary(candidate: Candidate) -> CandidateSummary:
    return CandidateSummary(
        candidate_id=candidate.candidate_id,
        report_type=candidate.report_type,
        seed=candidate.seed,
        width=candidate.width,
        density=candidate.density,
        placement_count=len(candidate.placements),
    )


def create_app(
    *,
    corpus: CorpusQueryService,
    jobs: SolverJobService,
    order_books: OrderBookService | None = None,
) -> FastAPI:
    """Create an application around explicit, already-configured local services."""

    app = FastAPI(title="YieldForge Research Workbench", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request,
        _error_value: RequestValidationError,
    ) -> JSONResponse:
        return _error(422, "request_validation", "request validation failed")

    @app.exception_handler(Exception)
    async def internal_server_error(_request: Request, _error_value: Exception) -> JSONResponse:
        return _error(500, "internal_error", "workbench request failed")

    @app.get("/api/corpus/summary", response_model=CorpusSummaryDto)
    async def corpus_summary() -> CorpusSummaryDto:
        return corpus.summary()

    @app.get("/api/tasks", response_model=TaskPageDto)
    async def list_tasks(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
        status: str | None = None,
        constraint_type: Annotated[str | None, Query(max_length=80)] = None,
        task_id: Annotated[int | None, Query(ge=0)] = None,
        min_parts: Annotated[int | None, Query(ge=0)] = None,
        max_parts: Annotated[int | None, Query(ge=0)] = None,
    ) -> TaskPageDto | JSONResponse:
        try:
            return corpus.list_tasks(
                limit=limit,
                cursor=cursor,
                status=status,
                constraint_type=constraint_type,
                task_id=task_id,
                min_parts=min_parts,
                max_parts=max_parts,
            )
        except (InvalidCursorError, InvalidTaskQueryError):
            return _error(422, "invalid_task_query", "task query was rejected")

    @app.get("/api/tasks/{tasks_index}", response_model=TaskDetailDto)
    async def task_detail(tasks_index: int) -> TaskDetailDto | JSONResponse:
        try:
            return corpus.task_detail(tasks_index)
        except TaskNotFoundError:
            return _error(404, "task_not_found", "task was not found")

    @app.post("/api/solver-jobs", response_model=JobView, status_code=202)
    async def create_solver_job(body: CreateSolverJobRequest) -> JobView | JSONResponse:
        try:
            detail = corpus.task_detail(body.tasks_index)
        except TaskNotFoundError:
            return _error(404, "task_not_found", "task was not found")
        capability = detail.summary.solve_capability
        if not capability.can_solve:
            return _error(
                422,
                "task_not_solvable",
                "task is blocked from solver projection",
                details={"reason_codes": list(capability.reason_codes)},
            )
        if body.acknowledged_assumption_codes != capability.assumption_codes:
            return _error(
                422,
                "assumption_acknowledgement_mismatch",
                "exact task assumptions must be acknowledged",
                details={"required_assumption_codes": list(capability.assumption_codes)},
            )
        try:
            problem = corpus.project_problem(
                body.tasks_index,
                acknowledged_assumption_codes=body.acknowledged_assumption_codes,
            )
        except TaskNotSolvableError:
            return _error(422, "task_not_solvable", "task is blocked from solver projection")
        except TaskNotFoundError:
            return _error(404, "task_not_found", "task was not found")

        source = corpus.summary().source
        binding = SourceTaskBinding(
            dataset_id=source.dataset_id,
            source_slice_sha256=source.slice_sha256,
            tasks_index=body.tasks_index,
            acknowledged_assumption_codes=body.acknowledged_assumption_codes,
        )
        internal = SolveRequest(
            problem=problem,
            config=SpyrrowRunConfig(
                seed=body.seed,
                total_computation_time=body.total_computation_time,
                early_termination=body.early_termination,
                num_workers=1,
                min_items_separation=body.min_items_separation,
            ),
            max_runtime_seconds=body.max_runtime_seconds,
            source_task_binding=binding,
        )
        try:
            return _job_view(await jobs.start(internal))
        except ActiveJobError as error:
            return _error(
                409,
                "active_solver_job",
                "another solver job is already active",
                details={"active_job_id": error.active_job_id},
                headers={"Location": f"/api/solver-jobs/{error.active_job_id}"},
            )

    @app.get("/api/solver-jobs/{job_id}", response_model=JobView)
    async def get_solver_job(job_id: str) -> JobView | JSONResponse:
        try:
            return _job_view(jobs.get(job_id))
        except KeyError:
            return _error(404, "job_not_found", "solver job was not found")

    @app.delete("/api/solver-jobs/{job_id}", response_model=JobView)
    async def cancel_solver_job(job_id: str) -> JobView | JSONResponse:
        try:
            return _job_view(await jobs.cancel(job_id))
        except KeyError:
            return _error(404, "job_not_found", "solver job was not found")

    @app.get("/api/solver-jobs/{job_id}/events", response_model=None)
    async def solver_job_events(
        request: Request,
        job_id: str,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse | JSONResponse:
        try:
            cursor = _parse_last_event_id(last_event_id)
            snapshot = jobs.get(job_id)
        except ValueError:
            return _error(422, "invalid_event_cursor", "Last-Event-ID was rejected")
        except KeyError:
            return _error(404, "job_not_found", "solver job was not found")
        if cursor > snapshot.latest_sequence:
            return _error(422, "invalid_event_cursor", "Last-Event-ID was rejected")
        return StreamingResponse(
            _stream_job_events(
                request,
                jobs,
                job_id,
                after_sequence=cursor,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/solver-jobs/{job_id}/candidates", response_model=CandidatePage)
    async def list_candidates(
        job_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    ) -> CandidatePage | JSONResponse:
        try:
            jobs.get(job_id)
            batch = jobs.completed_batch(job_id)
        except KeyError:
            return _error(404, "job_not_found", "solver job was not found")
        if batch is None:
            return _error(409, "job_not_completed", "candidate archive is not complete")
        start = 0
        if cursor is not None:
            identifiers = [candidate.candidate_id for candidate in batch.candidates]
            try:
                start = identifiers.index(cursor) + 1
            except ValueError:
                return _error(422, "invalid_candidate_cursor", "candidate cursor was rejected")
        selected = batch.candidates[start : start + limit]
        next_cursor = None
        if start + limit < len(batch.candidates):
            next_cursor = selected[-1].candidate_id
        return CandidatePage(
            items=tuple(_candidate_summary(candidate) for candidate in selected),
            next_cursor=next_cursor,
        )

    @app.get(
        "/api/solver-jobs/{job_id}/candidates/{candidate_id}/geometry",
        response_model=CandidateGeometry,
    )
    async def candidate_geometry(
        job_id: str,
        candidate_id: str,
    ) -> CandidateGeometry | JSONResponse:
        try:
            jobs.get(job_id)
            batch = jobs.completed_batch(job_id)
        except KeyError:
            return _error(404, "job_not_found", "solver job was not found")
        if batch is None:
            return _error(409, "job_not_completed", "candidate archive is not complete")
        candidate = next(
            (item for item in batch.candidates if item.candidate_id == candidate_id),
            None,
        )
        if candidate is None:
            return _error(404, "candidate_not_found", "candidate was not found")
        parts = {part.id: part for part in batch.problem.parts}
        placements: list[PlacementGeometry] = []
        for placement in candidate.placements:
            part = parts.get(placement.part_id)
            if part is None:
                return _error(500, "archive_integrity", "candidate archive failed validation")
            placements.append(
                PlacementGeometry(
                    part_id=placement.part_id,
                    rotation=placement.rotation,
                    translation=placement.translation,
                    projected_shape=tuple(part.shape),
                    svg_points=placed_shape_svg_points(
                        part.shape,
                        rotation_degrees=placement.rotation,
                        translation=placement.translation,
                        sheet_width=batch.problem.strip_height,
                    ),
                )
            )
        return CandidateGeometry(
            candidate=_candidate_summary(candidate),
            sheet=SheetGeometry(
                length=batch.problem.sheet_length,
                width=batch.problem.strip_height,
            ),
            placements=tuple(placements),
        )

    @app.get("/api/tasks/{tasks_index}/solver-jobs", response_model=TaskJobPage)
    async def completed_jobs_for_task(
        tasks_index: int,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> TaskJobPage | JSONResponse:
        try:
            corpus.task_detail(tasks_index)
        except TaskNotFoundError:
            return _error(404, "task_not_found", "task was not found")
        source = corpus.summary().source
        snapshots = jobs.snapshots_for_source_task(
            dataset_id=source.dataset_id,
            source_slice_sha256=source.slice_sha256,
            tasks_index=tasks_index,
        )
        completed = tuple(
            snapshot for snapshot in snapshots if snapshot.status is JobStatus.COMPLETED
        )
        return TaskJobPage(items=tuple(_job_view(snapshot) for snapshot in completed[-limit:]))

    @app.get("/api/order-books", response_model=OrderBookPage)
    async def list_order_books(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
        regime: GenerationRegime | None = None,
    ) -> OrderBookPage | JSONResponse:
        if order_books is None:
            return _error(503, "order_books_unavailable", "order-book service is unavailable")
        try:
            return order_books.list_books(limit=limit, cursor=cursor, regime=regime)
        except InvalidOrderBookCursorError:
            return _error(422, "invalid_order_book_cursor", "order-book cursor was rejected")
        except InvalidOrderBookRequestError:
            return _error(422, "invalid_order_book_request", "order-book query was rejected")
        except OrderBookIntegrityError:
            return _error(500, "order_book_integrity", "order-book catalog failed validation")

    @app.post("/api/order-books", response_model=OrderBookView, status_code=201)
    async def generate_order_book(body: GenerateOrderBookInput) -> OrderBookView | JSONResponse:
        if order_books is None:
            return _error(503, "order_books_unavailable", "order-book service is unavailable")
        try:
            return order_books.generate(body)
        except InvalidOrderBookRequestError:
            return _error(
                422,
                "invalid_order_book_request",
                "order-book generation request was rejected",
            )
        except OrderBookCapacityError:
            return _error(409, "order_book_catalog_full", "order-book catalog is full")
        except OrderBookIntegrityError:
            return _error(500, "order_book_integrity", "order-book catalog failed validation")

    @app.get("/api/order-books/{order_book_id}", response_model=OrderBookView)
    async def order_book_detail(order_book_id: str) -> OrderBookView | JSONResponse:
        if order_books is None:
            return _error(503, "order_books_unavailable", "order-book service is unavailable")
        try:
            return order_books.detail(order_book_id)
        except OrderBookNotFoundError:
            return _error(404, "order_book_not_found", "order book was not found")
        except OrderBookIntegrityError:
            return _error(500, "order_book_integrity", "order-book catalog failed validation")

    return app


def create_default_app() -> FastAPI:
    """Build the local app using only server-configured corpus and runtime paths."""

    configured_root = os.environ.get("YIELDFORGE_WORKBENCH_ROOT")
    if configured_root is not None and not configured_root.strip():
        raise ValueError("YIELDFORGE_WORKBENCH_ROOT must not be empty")
    project_root = Path(__file__).resolve().parents[3]
    runtime_root = (
        Path(configured_root).expanduser().resolve()
        if configured_root is not None
        else project_root / "var/workbench"
    )
    return create_app(
        corpus=CorpusQueryService.from_repository(),
        jobs=SolverJobService(
            runtime_root / "jobs",
            runtime_root / "candidate-archives",
        ),
        order_books=OrderBookService.from_repository(
            runtime_archive_dir=runtime_root / "order-books"
        ),
    )


__all__ = ["create_app", "create_default_app"]
