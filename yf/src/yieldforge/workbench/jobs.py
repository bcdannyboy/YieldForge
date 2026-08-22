"""Durable, bounded supervision for disposable solver workers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yieldforge.archive import CandidateArchive, batch_content_hash, canonical_json
from yieldforge.datasets.passive_report import (
    PassiveEvidenceError,
    decode_strict_json_bytes,
    read_passive_evidence_file,
)
from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    ProjectionMode,
    SourceTaskBinding,
    SpyrrowRunResult,
)
from yieldforge.workbench.contracts import (
    TERMINAL_JOB_STATUSES,
    JobEvent,
    JobEventKind,
    JobSnapshot,
    JobStatus,
    JobTerminalMetadata,
    SolveRequest,
    WorkerMessage,
    WorkerMessageKind,
)

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_EVENTS_BYTES = 32 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 10 * 1024 * 1024
MAX_TERMINAL_BYTES = 64 * 1024
MAX_RUNTIME_BYTES = 4 * 1024
MAX_ARCHIVE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_CANDIDATES_BYTES = 16 * 1024 * 1024


class WorkerProtocolError(RuntimeError):
    """A worker violated the bounded NDJSON protocol."""


class ActiveJobError(RuntimeError):
    """A new solve was rejected because another job still owns the active slot."""

    def __init__(self, active_job_id: str) -> None:
        self.active_job_id = active_job_id
        super().__init__(f"only one solver job may be active: {active_job_id}")


@dataclass
class _ProtocolOutcome:
    result: SpyrrowRunResult | None = None
    failure_code: str | None = None


@dataclass
class _JobState:
    job_id: str
    directory: Path
    request: SolveRequest
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    events: list[JobEvent] = field(default_factory=list)
    candidate_count: int = 0
    worker_pid: int | None = None
    archive_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    process: asyncio.subprocess.Process | None = None
    runner: asyncio.Task[None] | None = None
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    completion_committed: bool = False


class SolverJobService:
    """Run one killable solver job at a time and persist replayable job truth."""

    def __init__(
        self,
        job_root: Path,
        archive_root: Path,
        *,
        worker_command: tuple[str, ...] | None = None,
        candidate_interval_seconds: float = 0.125,
        terminate_grace_seconds: float = 0.5,
        max_stdout_line_bytes: int = 8 * 1024 * 1024,
        max_stdout_bytes: int = 16 * 1024 * 1024,
        max_stderr_bytes: int = 32 * 1024,
    ) -> None:
        if not 0.1 <= candidate_interval_seconds <= 0.2:
            raise ValueError("candidate interval must sample between five and ten hertz")
        if terminate_grace_seconds <= 0:
            raise ValueError("terminate grace must be positive")
        if min(max_stdout_line_bytes, max_stdout_bytes, max_stderr_bytes) <= 0:
            raise ValueError("worker output limits must be positive")

        self.job_root = Path(job_root)
        self.archive_root = Path(archive_root)
        self.worker_command = worker_command or (
            sys.executable,
            "-m",
            "yieldforge.workbench.solver_worker",
        )
        self.candidate_interval_seconds = candidate_interval_seconds
        self.terminate_grace_seconds = terminate_grace_seconds
        self.max_stdout_line_bytes = max_stdout_line_bytes
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, _JobState] = {}
        self._active_job_ids: set[str] = set()
        self._service_lock = asyncio.Lock()
        self._recover_existing_jobs()

    async def start(self, request: SolveRequest) -> JobSnapshot:
        """Persist an immutable request and start its server-assigned worker."""

        validated = SolveRequest.model_validate(request.model_dump())
        async with self._service_lock:
            self._discard_terminal_active_ids_locked()
            if self._active_job_ids:
                raise ActiveJobError(sorted(self._active_job_ids)[0])
            state = self._create_validated_state_locked(validated)
            self._active_job_ids.add(state.job_id)
            state.runner = asyncio.create_task(
                self._run_job(state),
                name=f"solver-{state.job_id}",
            )
            return self._snapshot(state)

    async def start_pair(
        self,
        requests: tuple[SolveRequest, SolveRequest],
    ) -> tuple[JobSnapshot, JobSnapshot]:
        """Atomically reserve both bounded worker slots for one matched experiment."""

        if not isinstance(requests, tuple) or len(requests) != 2:
            raise ValueError("matched jobs require exactly two solve requests")
        validated = tuple(SolveRequest.model_validate(item.model_dump()) for item in requests)
        self._validate_matched_requests(validated)
        async with self._service_lock:
            self._discard_terminal_active_ids_locked()
            if self._active_job_ids:
                raise ActiveJobError(sorted(self._active_job_ids)[0])
            states = tuple(self._create_validated_state_locked(item) for item in validated)
            self._active_job_ids.update(state.job_id for state in states)
            for state in states:
                state.runner = asyncio.create_task(
                    self._run_job(state),
                    name=f"solver-{state.job_id}",
                )
            snapshots = tuple(self._snapshot(state) for state in states)
            return snapshots[0], snapshots[1]

    def _discard_terminal_active_ids_locked(self) -> None:
        self._active_job_ids = {
            job_id
            for job_id in self._active_job_ids
            if self._jobs[job_id].status not in TERMINAL_JOB_STATUSES
        }

    def _create_validated_state_locked(self, request: SolveRequest) -> _JobState:
        job_id = f"job_{uuid.uuid4().hex}"
        directory = self.job_root / job_id
        directory.mkdir(mode=0o700)
        self._write_new(directory / "request.json", canonical_json(request) + "\n")
        now = self._now()
        state = _JobState(
            job_id=job_id,
            directory=directory,
            request=request,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = state
        self._append_event(state, kind=JobEventKind.STATUS, status=JobStatus.QUEUED)
        return state

    @staticmethod
    def _validate_matched_requests(requests: tuple[SolveRequest, SolveRequest]) -> None:
        first, second = requests
        if (
            first.experiment_pair_id is None
            or first.experiment_pair_id != second.experiment_pair_id
            or {first.experiment_arm, second.experiment_arm}
            != {
                ProjectionMode.SOURCE_AS_RECORDED,
                ProjectionMode.FORCE_FLIP_X_ZERO,
            }
        ):
            raise ValueError("matched requests require one shared pair id and both projection arms")
        first_binding = first.source_task_binding
        second_binding = second.source_task_binding
        if first_binding is None or second_binding is None:
            raise ValueError("matched requests require source task bindings")
        first_projection = first_binding.solver_projection
        second_projection = second_binding.solver_projection
        if first_projection is None or second_projection is None:
            raise ValueError("matched requests require solver projection bindings")
        if (
            (
                first_binding.dataset_id,
                first_binding.source_slice_sha256,
                first_binding.tasks_index,
                first_binding.acknowledged_assumption_codes,
            )
            != (
                second_binding.dataset_id,
                second_binding.source_slice_sha256,
                second_binding.tasks_index,
                second_binding.acknowledged_assumption_codes,
            )
            or first.config != second.config
            or first.max_runtime_seconds != second.max_runtime_seconds
            or first_projection.projection_sha256 == second_projection.projection_sha256
        ):
            raise ValueError("matched requests must share source identity and run settings")

    async def wait(self, job_id: str) -> JobSnapshot:
        """Wait for one job to become terminal."""

        state = self._require_job(job_id)
        if state.runner is not None:
            await asyncio.shield(state.runner)
        return self._snapshot(state)

    async def cancel(self, job_id: str) -> JobSnapshot:
        """Idempotently request cancellation and wait until the worker is dead."""

        state = self._require_job(job_id)
        if state.status in TERMINAL_JOB_STATUSES:
            return self._snapshot(state)
        if state.completion_committed:
            return await self.wait(job_id)
        if state.status is not JobStatus.CANCELLING:
            state.status = JobStatus.CANCELLING
            self._append_event(
                state,
                kind=JobEventKind.STATUS,
                status=JobStatus.CANCELLING,
                candidate_count=state.candidate_count,
            )
            state.cancel_requested.set()
        return await self.wait(job_id)

    def get(self, job_id: str) -> JobSnapshot:
        """Return the current immutable job snapshot."""

        return self._snapshot(self._require_job(job_id))

    def events(self, job_id: str, *, after_sequence: int = 0) -> list[JobEvent]:
        """Return persisted events after a caller's replay cursor."""

        if after_sequence < 0:
            raise ValueError("after_sequence must be nonnegative")
        state = self._require_job(job_id)
        return [event for event in state.events if event.sequence > after_sequence]

    def job_directory(self, job_id: str) -> Path:
        """Resolve only a known server-assigned job directory."""

        return self._require_job(job_id).directory

    def completed_batch(self, job_id: str) -> CandidateBatch | None:
        """Return a defensive copy of archive-bound completed truth, if available."""

        state = self._require_job(job_id)
        if (
            state.status is not JobStatus.COMPLETED
            or not state.completion_committed
            or state.archive_path is None
        ):
            return None
        terminal = state.events[-1]
        if terminal.kind is not JobEventKind.TERMINAL or terminal.batch is None:
            return None
        self._validate_completed_archive(
            Path(state.archive_path),
            terminal.batch,
            state.request.source_task_binding,
        )
        return CandidateBatch.model_validate(terminal.batch.model_dump())

    def snapshots_for_source_task(
        self,
        *,
        dataset_id: str,
        source_slice_sha256: str,
        tasks_index: int,
    ) -> tuple[JobSnapshot, ...]:
        """Return stable snapshots matching one explicit source-task identity."""

        identity = SourceTaskBinding(
            dataset_id=dataset_id,
            source_slice_sha256=source_slice_sha256,
            tasks_index=tasks_index,
        )
        matched = (
            state
            for state in self._jobs.values()
            if state.request.source_task_binding is not None
            and state.request.source_task_binding.dataset_id == identity.dataset_id
            and state.request.source_task_binding.source_slice_sha256
            == identity.source_slice_sha256
            and state.request.source_task_binding.tasks_index == identity.tasks_index
        )
        return tuple(
            self._snapshot(state)
            for state in sorted(matched, key=lambda item: (item.created_at, item.job_id))
        )

    async def _run_job(self, state: _JobState) -> None:
        stdout_task: asyncio.Task[_ProtocolOutcome] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        process_task: asyncio.Task[int] | None = None
        spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        cancel_task: asyncio.Task[bool] | None = None
        timeout_task: asyncio.Task[None] | None = None
        archive_task: asyncio.Future[Path] | None = None
        archive_abandoned = threading.Event()
        archive_staging = state.directory / "candidate-archive.staging"
        deadline = time.monotonic() + state.request.max_runtime_seconds
        try:
            if state.cancel_requested.is_set():
                self._finish_terminal(state, JobStatus.CANCELLED)
                return
            cancel_task = asyncio.create_task(state.cancel_requested.wait())
            timeout_task = asyncio.create_task(asyncio.sleep(max(0.0, deadline - time.monotonic())))
            spawn_task = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *self.worker_command,
                    "--request",
                    str(state.directory / "request.json"),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=self.max_stdout_line_bytes + 1,
                    start_new_session=True,
                )
            )
            done, _ = await asyncio.wait(
                {spawn_task, cancel_task, timeout_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if state.cancel_requested.is_set() or timeout_task in done:
                if spawn_task.done() and not spawn_task.cancelled():
                    with contextlib.suppress(Exception):
                        process = spawn_task.result()
                        state.process = process
                        state.worker_pid = process.pid
                        await self._stop_process(process)
                else:
                    spawn_task.cancel()
                self._finish_terminal(
                    state,
                    JobStatus.CANCELLED if state.cancel_requested.is_set() else JobStatus.TIMED_OUT,
                )
                return
            try:
                process = await spawn_task
            except Exception:
                self._finish_failed(state, "worker_spawn", "solver worker failed")
                return

            state.process = process
            state.worker_pid = process.pid
            self._write_new(
                state.directory / "runtime.json",
                json.dumps(
                    {
                        "schema_version": "yieldforge.job-runtime.v1",
                        "worker_pid": process.pid,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
            )
            if state.cancel_requested.is_set():
                await self._stop_process(process)
                self._finish_terminal(state, JobStatus.CANCELLED)
                return
            if time.monotonic() >= deadline:
                await self._stop_process(process)
                self._finish_terminal(state, JobStatus.TIMED_OUT)
                return
            state.status = JobStatus.RUNNING
            self._append_event(state, kind=JobEventKind.STATUS, status=JobStatus.RUNNING)

            assert process.stdout is not None
            assert process.stderr is not None
            stdout_task = asyncio.create_task(self._consume_stdout(state, process.stdout))
            stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            process_task = asyncio.create_task(process.wait())
            while not process_task.done():
                waiting: set[asyncio.Task[Any]] = {
                    process_task,
                    cancel_task,
                    timeout_task,
                }
                if not stdout_task.done():
                    waiting.add(stdout_task)
                done, _ = await asyncio.wait(
                    waiting,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if state.cancel_requested.is_set():
                    await self._stop_process(process)
                    await self._settle_reader(stdout_task)
                    await stderr_task
                    self._finish_terminal(state, JobStatus.CANCELLED)
                    return
                if timeout_task in done:
                    await self._stop_process(process)
                    await self._settle_reader(stdout_task)
                    await stderr_task
                    self._finish_terminal(state, JobStatus.TIMED_OUT)
                    return
                if stdout_task in done and stdout_task.exception() is not None:
                    await self._stop_process(process)
                    with contextlib.suppress(Exception):
                        await stderr_task
                    self._finish_failed(state, "worker_protocol", "solver worker failed")
                    return

            return_code = await process_task
            while not (stdout_task.done() and stderr_task.done()):
                waiting = {cancel_task, timeout_task}
                if not stdout_task.done():
                    waiting.add(stdout_task)
                if not stderr_task.done():
                    waiting.add(stderr_task)
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if state.cancel_requested.is_set():
                    await self._stop_process(process)
                    await self._settle_reader(stdout_task)
                    await stderr_task
                    self._finish_terminal(state, JobStatus.CANCELLED)
                    return
                if timeout_task in done:
                    await self._stop_process(process)
                    await self._settle_reader(stdout_task)
                    await stderr_task
                    self._finish_terminal(state, JobStatus.TIMED_OUT)
                    return
                if stdout_task in done and stdout_task.exception() is not None:
                    await self._stop_process(process)
                    with contextlib.suppress(Exception):
                        await stderr_task
                    self._finish_failed(state, "worker_protocol", "solver worker failed")
                    return
            outcome = await stdout_task
            await stderr_task
            if outcome.failure_code is not None:
                self._finish_failed(state, outcome.failure_code, "solver worker failed")
                return
            if return_code != 0 or outcome.result is None:
                self._finish_failed(state, "solver_failure", "solver worker failed")
                return
            self._require_result_binding(state.request, outcome.result)

            if state.cancel_requested.is_set():
                self._finish_terminal(state, JobStatus.CANCELLED)
                return
            if time.monotonic() >= deadline:
                self._finish_terminal(state, JobStatus.TIMED_OUT)
                return

            archive_path = self.archive_root / state.job_id
            archive_error: Exception | None = None
            archive_task = self._start_daemon_archive_staging(
                archive_staging,
                outcome.result.batch,
                state.request.source_task_binding,
                archive_abandoned,
            )
            try:
                done, _ = await asyncio.wait(
                    {archive_task, cancel_task, timeout_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if state.cancel_requested.is_set():
                    archive_abandoned.set()
                    archive_task.cancel()
                    self._discard_path(archive_staging)
                    self._finish_terminal(state, JobStatus.CANCELLED)
                    return
                if timeout_task in done or time.monotonic() >= deadline:
                    archive_abandoned.set()
                    archive_task.cancel()
                    self._discard_path(archive_staging)
                    self._finish_terminal(state, JobStatus.TIMED_OUT)
                    return
                try:
                    await archive_task
                except Exception as error:
                    archive_error = error
            except asyncio.CancelledError:
                archive_abandoned.set()
                archive_task.cancel()
                self._discard_path(archive_staging)
                raise
            if timeout_task.done() or time.monotonic() >= deadline:
                archive_abandoned.set()
                self._discard_path(archive_staging)
                self._finish_terminal(state, JobStatus.TIMED_OUT)
                return
            if archive_error is not None:
                self._discard_path(archive_staging)
                self._finish_failed(state, "archive_failure", "solver worker failed")
                return
            if archive_path.exists() or archive_path.is_symlink():
                self._discard_path(archive_staging)
                self._finish_failed(state, "archive_failure", "solver worker failed")
                return
            archive_staging.rename(archive_path)
            if time.monotonic() >= deadline:
                self._discard_path(archive_path)
                self._finish_terminal(state, JobStatus.TIMED_OUT)
                return
            state.completion_committed = True
            state.archive_path = str(archive_path.resolve())
            self._finish_terminal(state, JobStatus.COMPLETED, batch=outcome.result.batch)
        except Exception:
            if state.process is not None and state.process.returncode is None:
                with contextlib.suppress(Exception):
                    await self._stop_process(state.process)
            if state.status not in TERMINAL_JOB_STATUSES:
                self._finish_failed(state, "supervisor_failure", "solver worker failed")
        finally:
            if archive_task is not None and not archive_task.done():
                archive_abandoned.set()
                archive_task.cancel()
            elif archive_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    archive_task.exception()
            if state.status is not JobStatus.COMPLETED:
                self._discard_path(archive_staging)
            if state.process is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self._stop_process(state.process))
            cleanup_tasks = (
                stdout_task,
                stderr_task,
                cancel_task,
                timeout_task,
                process_task,
                spawn_task,
            )
            for task in cleanup_tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in cleanup_tasks if task is not None),
                return_exceptions=True,
            )
            async with self._service_lock:
                self._active_job_ids.discard(state.job_id)

    async def _consume_stdout(
        self, state: _JobState, stream: asyncio.StreamReader
    ) -> _ProtocolOutcome:
        total_bytes = 0
        phase_seen = False
        complete_seen = False
        worker_candidates: list[Candidate] = []
        pending_candidate: Candidate | None = None
        last_candidate_event_at = 0.0
        outcome = _ProtocolOutcome()

        while True:
            try:
                line = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError) as error:
                raise WorkerProtocolError("worker line exceeded limit") from error
            if not line:
                break
            total_bytes += len(line)
            if len(line) > self.max_stdout_line_bytes or total_bytes > self.max_stdout_bytes:
                raise WorkerProtocolError("worker output exceeded limit")
            if not line.endswith(b"\n"):
                raise WorkerProtocolError("worker output must be newline delimited")
            try:
                message = WorkerMessage.model_validate_json(line)
            except Exception as error:
                raise WorkerProtocolError("worker emitted invalid JSON") from error
            if complete_seen:
                raise WorkerProtocolError("worker emitted output after terminal message")
            if state.cancel_requested.is_set():
                continue

            now = time.monotonic()
            if message.kind is WorkerMessageKind.PHASE:
                if phase_seen or worker_candidates:
                    raise WorkerProtocolError("worker phase ordering is invalid")
                phase_seen = True
                self._append_event(
                    state,
                    kind=JobEventKind.PHASE,
                    status=JobStatus.RUNNING,
                    phase="solving",
                )
            elif message.kind is WorkerMessageKind.CANDIDATE:
                if not phase_seen or message.candidate is None:
                    raise WorkerProtocolError("candidate preceded worker phase")
                if message.candidate.seed != state.request.config.seed:
                    raise WorkerProtocolError("candidate seed does not match request")
                worker_candidates.append(message.candidate)
                state.candidate_count = len(worker_candidates)
                if len(worker_candidates) == 1:
                    self._append_candidate_event(state, message.candidate)
                    last_candidate_event_at = now
                else:
                    pending_candidate = message.candidate
                    if now - last_candidate_event_at >= self.candidate_interval_seconds:
                        self._append_candidate_event(state, pending_candidate)
                        pending_candidate = None
                        last_candidate_event_at = now
            elif message.kind is WorkerMessageKind.COMPLETE:
                if not phase_seen or message.result is None:
                    raise WorkerProtocolError("complete message preceded worker phase")
                complete_seen = True
                if worker_candidates != message.result.batch.candidates:
                    raise WorkerProtocolError("terminal batch does not match candidate stream")
                self._require_result_binding(state.request, message.result)
                if pending_candidate is not None:
                    self._append_candidate_event(state, pending_candidate)
                    pending_candidate = None
                outcome.result = message.result
            else:
                complete_seen = True
                outcome.failure_code = message.error_code or "solver_failure"

        if pending_candidate is not None and not state.cancel_requested.is_set():
            self._append_candidate_event(state, pending_candidate)
        if not phase_seen:
            raise WorkerProtocolError("worker omitted phase")
        return outcome

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> bytes:
        retained = bytearray()
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return bytes(retained)
            remaining = self.max_stderr_bytes - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        process_group = process.pid
        if process.returncode is not None:
            self._signal_process_group(process_group, signal.SIGKILL)
            await process.wait()
            return
        self._signal_process_group(process_group, signal.SIGTERM)
        try:
            await asyncio.wait_for(
                asyncio.shield(process.wait()), timeout=self.terminate_grace_seconds
            )
        except TimeoutError:
            self._signal_process_group(process_group, signal.SIGKILL)
            await process.wait()
        else:
            # The direct worker may exit while a native descendant remains in its session.
            await asyncio.sleep(self.terminate_grace_seconds)
            self._signal_process_group(process_group, signal.SIGKILL)
        if process.returncode is None:
            raise RuntimeError("solver worker did not exit")

    @staticmethod
    def _signal_process_group(process_group: int, requested_signal: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_group, requested_signal)

    async def _settle_reader(self, task: asyncio.Task[_ProtocolOutcome]) -> None:
        if not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            except (TimeoutError, Exception):
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    def _append_candidate_event(self, state: _JobState, candidate: Candidate) -> None:
        self._append_event(
            state,
            kind=JobEventKind.CANDIDATE,
            status=JobStatus.RUNNING,
            candidate=candidate,
            candidate_count=state.candidate_count,
        )

    @staticmethod
    def _require_result_binding(request: SolveRequest, result: SpyrrowRunResult) -> None:
        SolverJobService._require_batch_binding(request, result.batch)

    @staticmethod
    def _require_batch_binding(request: SolveRequest, batch: CandidateBatch) -> None:
        if batch.problem != request.problem or batch.config != request.config:
            raise WorkerProtocolError("terminal batch does not match solve request")
        if any(candidate.seed != request.config.seed for candidate in batch.candidates):
            raise WorkerProtocolError("terminal candidate seed does not match solve request")

    def _finish_failed(self, state: _JobState, code: str, message: str) -> None:
        state.error_code = code
        state.error_message = message
        self._finish_terminal(state, JobStatus.FAILED)

    def _finish_terminal(
        self,
        state: _JobState,
        status: JobStatus,
        *,
        batch: Any | None = None,
    ) -> None:
        state.status = status
        event = self._append_event(
            state,
            kind=JobEventKind.TERMINAL,
            status=status,
            candidate_count=state.candidate_count,
            batch=batch,
            error_code=state.error_code,
            error_message=state.error_message,
        )
        metadata = JobTerminalMetadata(
            job_id=state.job_id,
            status=status,
            finished_at=event.occurred_at,
            final_sequence=event.sequence,
            candidate_count=state.candidate_count,
            archive_path=state.archive_path,
            error_code=state.error_code,
            error_message=state.error_message,
        )
        self._write_new(
            state.directory / "terminal.json", metadata.model_dump_json(exclude_none=True) + "\n"
        )

    def _append_event(self, state: _JobState, **values: Any) -> JobEvent:
        now = self._now()
        event = JobEvent(
            job_id=state.job_id,
            sequence=len(state.events) + 1,
            occurred_at=now,
            **values,
        )
        with (state.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json(exclude_none=True) + "\n")
        state.events.append(event)
        state.updated_at = now
        if event.status is not None:
            state.status = event.status
        return event

    def _recover_existing_jobs(self) -> None:
        for directory in sorted(self.job_root.iterdir()):
            if directory.is_symlink() or not directory.is_dir():
                continue
            if not directory.name.startswith("job_"):
                continue
            request_path = directory / "request.json"
            events_path = directory / "events.jsonl"
            request_payload = self._read_optional_recovery_file(
                request_path,
                label="job request",
                max_bytes=MAX_REQUEST_BYTES,
            )
            events_payload = self._read_optional_recovery_file(
                events_path,
                label="job events",
                max_bytes=MAX_EVENTS_BYTES,
            )
            if request_payload is None or events_payload is None:
                raise ValueError(f"persisted job is missing request or events: {directory.name}")
            request = SolveRequest.model_validate(
                decode_strict_json_bytes(
                    request_payload,
                    label="job request",
                    max_bytes=MAX_REQUEST_BYTES,
                )
            )
            events = self._parse_event_history(events_payload)
            self._validate_event_history(directory.name, events)
            runtime_payload = self._read_optional_recovery_file(
                directory / "runtime.json",
                label="job runtime",
                max_bytes=MAX_RUNTIME_BYTES,
            )
            worker_pid = self._load_worker_pid(runtime_payload)
            last = events[-1]
            state = _JobState(
                job_id=directory.name,
                directory=directory,
                request=request,
                status=last.status or JobStatus.RUNNING,
                created_at=events[0].occurred_at,
                updated_at=last.occurred_at,
                events=events,
                candidate_count=last.candidate_count,
                worker_pid=worker_pid,
            )
            self._jobs[state.job_id] = state
            terminal_path = directory / "terminal.json"
            terminal_payload = self._read_optional_recovery_file(
                terminal_path,
                label="job terminal metadata",
                max_bytes=MAX_TERMINAL_BYTES,
            )

            if last.kind is JobEventKind.TERMINAL:
                self._remove_terminal_staging(directory)
                assert last.status is not None
                state.status = last.status
                state.error_code = last.error_code
                state.error_message = last.error_message
                if last.status is JobStatus.COMPLETED:
                    assert last.batch is not None
                    if last.candidate_count != len(last.batch.candidates):
                        raise ValueError(f"invalid completed candidate count: {directory.name}")
                    try:
                        self._require_batch_binding(state.request, last.batch)
                    except WorkerProtocolError as error:
                        raise ValueError(
                            f"completed batch does not match solve request: {directory.name}"
                        ) from error
                    server_archive_path = self.archive_root / state.job_id
                    if server_archive_path.is_symlink():
                        raise ValueError(f"completed archive is a link: {directory.name}")
                    archive_path = server_archive_path.resolve()
                    state.archive_path = str(archive_path)
                    state.completion_committed = True
                    self._validate_completed_archive(
                        archive_path,
                        last.batch,
                        state.request.source_task_binding,
                    )
                else:
                    archive_path = self.archive_root / state.job_id
                    if archive_path.exists() or archive_path.is_symlink():
                        raise ValueError(f"non-completed job has archive: {directory.name}")

                expected_terminal = JobTerminalMetadata(
                    job_id=state.job_id,
                    status=state.status,
                    finished_at=last.occurred_at,
                    final_sequence=last.sequence,
                    candidate_count=last.candidate_count,
                    archive_path=state.archive_path,
                    error_code=state.error_code,
                    error_message=state.error_message,
                )
                if terminal_payload is not None:
                    terminal = JobTerminalMetadata.model_validate(
                        decode_strict_json_bytes(
                            terminal_payload,
                            label="job terminal metadata",
                            max_bytes=MAX_TERMINAL_BYTES,
                        )
                    )
                    if (
                        terminal.status is JobStatus.COMPLETED
                        and terminal.archive_path != state.archive_path
                    ):
                        raise ValueError(f"invalid completed archive path: {directory.name}")
                    if terminal != expected_terminal:
                        raise ValueError(f"invalid terminal metadata: {directory.name}")
                else:
                    self._write_new(
                        terminal_path,
                        expected_terminal.model_dump_json(exclude_none=True) + "\n",
                    )
                continue

            if terminal_payload is not None:
                raise ValueError(f"terminal metadata has no terminal event: {directory.name}")
            self._discard_path(self.archive_root / state.job_id)
            self._discard_path(directory / "candidate-archive.staging")
            state.error_code = "supervisor_restart"
            state.error_message = "solver supervisor restarted"
            self._finish_terminal(state, JobStatus.FAILED)

    @staticmethod
    def _read_optional_recovery_file(
        path: Path,
        *,
        label: str,
        max_bytes: int,
    ) -> bytes | None:
        try:
            return read_passive_evidence_file(path, label=label, max_bytes=max_bytes)
        except PassiveEvidenceError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                return None
            raise ValueError(str(error)) from error

    @staticmethod
    def _parse_event_history(payload: bytes) -> list[JobEvent]:
        if not payload.endswith(b"\n"):
            raise ValueError("invalid event history: JSONL must end with a newline")
        lines = payload.splitlines()
        if not lines or any(not line for line in lines):
            raise ValueError("invalid event history: JSONL contains an empty record")
        events: list[JobEvent] = []
        for line in lines:
            try:
                decoded = decode_strict_json_bytes(
                    line,
                    label="job event",
                    max_bytes=MAX_EVENT_LINE_BYTES,
                )
                events.append(JobEvent.model_validate(decoded))
            except Exception as error:
                raise ValueError("invalid event history: malformed event record") from error
        return events

    @staticmethod
    def _validate_event_history(job_id: str, events: list[JobEvent]) -> None:
        def invalid(detail: str) -> None:
            raise ValueError(f"invalid event history for {job_id}: {detail}")

        if not events:
            invalid("event stream is empty")
        if any(
            event.job_id != job_id or event.sequence != index
            for index, event in enumerate(events, start=1)
        ):
            invalid("job identity or sequence is inconsistent")
        first = events[0]
        if (
            first.kind is not JobEventKind.STATUS
            or first.status is not JobStatus.QUEUED
            or first.candidate_count != 0
        ):
            invalid("first event must be an empty queued status")

        prior_status = JobStatus.QUEUED
        prior_count = 0
        prior_candidate_count = 0
        phase_seen = False
        candidate_events: list[JobEvent] = []
        for offset, event in enumerate(events[1:], start=1):
            is_last = offset == len(events) - 1
            if event.candidate_count < prior_count:
                invalid("candidate count decreased")
            if event.kind is JobEventKind.TERMINAL and not is_last:
                invalid("terminal event was not last")

            allowed = False
            if prior_status is JobStatus.QUEUED:
                allowed = (
                    event.kind is JobEventKind.STATUS
                    and event.status in {JobStatus.RUNNING, JobStatus.CANCELLING}
                ) or (
                    event.kind is JobEventKind.TERMINAL
                    and event.status in {JobStatus.FAILED, JobStatus.TIMED_OUT}
                )
            elif prior_status is JobStatus.RUNNING:
                allowed = (
                    (
                        event.status is JobStatus.RUNNING
                        and event.kind in {JobEventKind.PHASE, JobEventKind.CANDIDATE}
                    )
                    or (event.kind is JobEventKind.STATUS and event.status is JobStatus.CANCELLING)
                    or (
                        event.kind is JobEventKind.TERMINAL
                        and event.status
                        in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMED_OUT}
                    )
                )
            elif prior_status is JobStatus.CANCELLING:
                allowed = event.kind is JobEventKind.TERMINAL and event.status in {
                    JobStatus.CANCELLED,
                    JobStatus.FAILED,
                }
            if not allowed:
                invalid(f"illegal {prior_status.value} to {event.status.value} transition")

            if event.kind is JobEventKind.PHASE:
                if phase_seen or event.candidate_count != prior_count:
                    invalid("phase event is duplicated or changes candidate count")
                phase_seen = True
            elif event.kind is JobEventKind.CANDIDATE:
                if not phase_seen or event.candidate_count <= prior_candidate_count:
                    invalid("candidate event order is inconsistent")
                prior_candidate_count = event.candidate_count
                candidate_events.append(event)

            prior_status = event.status
            prior_count = event.candidate_count

        terminal = events[-1]
        if terminal.kind is not JobEventKind.TERMINAL:
            return
        if terminal.status is JobStatus.COMPLETED:
            if terminal.batch is None or terminal.candidate_count != len(terminal.batch.candidates):
                invalid("completed terminal count does not match its batch")
            if not phase_seen:
                invalid("completed history omitted the solving phase")
            for event in candidate_events:
                candidate_index = event.candidate_count - 1
                if candidate_index >= len(terminal.batch.candidates):
                    invalid("candidate event index exceeds terminal batch")
                if event.candidate != terminal.batch.candidates[candidate_index]:
                    invalid("candidate event does not match terminal batch order and content")

    @staticmethod
    def _validate_completed_archive(
        archive_path: Path,
        batch: CandidateBatch,
        source_task_binding: SourceTaskBinding | None,
    ) -> None:
        if archive_path.is_symlink() or not archive_path.is_dir():
            raise ValueError("completed archive is missing or is not a directory")
        members = {path.name: path for path in archive_path.iterdir()}
        if set(members) != {"manifest.json", "candidates.jsonl"} or any(
            path.is_symlink() or not path.is_file() for path in members.values()
        ):
            raise ValueError("completed archive has an invalid file inventory")
        try:
            manifest_payload = read_passive_evidence_file(
                members["manifest.json"],
                label="archive manifest",
                max_bytes=MAX_ARCHIVE_MANIFEST_BYTES,
            )
            candidates_payload = read_passive_evidence_file(
                members["candidates.jsonl"],
                label="archive candidates",
                max_bytes=MAX_ARCHIVE_CANDIDATES_BYTES,
            )
            manifest = decode_strict_json_bytes(
                manifest_payload,
                label="archive manifest",
                max_bytes=MAX_ARCHIVE_MANIFEST_BYTES,
            )
            if not isinstance(manifest, dict):
                raise ValueError("archive manifest must be an object")
            if candidates_payload and not candidates_payload.endswith(b"\n"):
                raise ValueError("archive candidates JSONL must end with a newline")
            candidates = [
                Candidate.model_validate(
                    decode_strict_json_bytes(
                        line,
                        label="archive candidate",
                        max_bytes=MAX_EVENT_LINE_BYTES,
                    )
                )
                for line in candidates_payload.splitlines()
            ]
        except PassiveEvidenceError:
            raise
        except Exception as error:
            raise ValueError("completed archive is malformed") from error
        expected_binding = (
            source_task_binding.model_dump(mode="json") if source_task_binding is not None else None
        )
        if manifest.get("source_task_binding") != expected_binding or (
            ("source_task_binding" in manifest) != (source_task_binding is not None)
        ):
            raise ValueError(
                "completed archive source task binding does not match immutable request"
            )
        expected_manifest = {
            "schema_version": "yieldforge.candidate-archive.v1",
            "candidate_count": len(batch.candidates),
            "batch_sha256": batch_content_hash(batch),
            "problem": batch.problem.model_dump(mode="json"),
            "solver": batch.solver.model_dump(mode="json"),
            "config": batch.config.model_dump(mode="json"),
        }
        if source_task_binding is not None:
            expected_manifest["source_task_binding"] = expected_binding
        if manifest != expected_manifest or candidates != batch.candidates:
            raise ValueError("completed archive does not match terminal batch")

    @staticmethod
    def _start_daemon_archive_staging(
        archive_staging: Path,
        batch: CandidateBatch,
        source_task_binding: SourceTaskBinding | None,
        abandoned: threading.Event,
    ) -> asyncio.Future[Path]:
        loop = asyncio.get_running_loop()
        completion: asyncio.Future[Path] = loop.create_future()

        def deliver(result: Path | None, error: BaseException | None) -> None:
            if completion.done():
                return
            if error is not None:
                completion.set_exception(error)
            else:
                assert result is not None
                completion.set_result(result)

        def stage() -> None:
            try:
                result = SolverJobService._create_staged_archive(
                    archive_staging,
                    batch,
                    source_task_binding,
                    abandoned,
                )
            except BaseException as error:
                failure = (
                    error
                    if isinstance(error, Exception) and not isinstance(error, StopIteration)
                    else RuntimeError("archive staging aborted")
                )
                try:
                    loop.call_soon_threadsafe(deliver, None, failure)
                except RuntimeError:
                    pass
            else:
                try:
                    loop.call_soon_threadsafe(deliver, result, None)
                except RuntimeError:
                    pass

        thread = threading.Thread(
            target=stage,
            name=f"yieldforge-archive-{archive_staging.parent.name}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            completion.cancel()
            raise
        return completion

    @staticmethod
    def _create_staged_archive(
        archive_staging: Path,
        batch: CandidateBatch,
        source_task_binding: SourceTaskBinding | None,
        abandoned: threading.Event,
    ) -> Path:
        try:
            if source_task_binding is None:
                return CandidateArchive.create(archive_staging, batch)
            return CandidateArchive.create(
                archive_staging,
                batch,
                source_task_binding=source_task_binding,
            )
        finally:
            if abandoned.is_set():
                SolverJobService._discard_path(archive_staging)

    @staticmethod
    def _remove_terminal_staging(job_directory: Path) -> None:
        staging = job_directory / "candidate-archive.staging"
        try:
            metadata = os.lstat(staging)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError(f"unsafe terminal staging path: {staging}")
        try:
            shutil.rmtree(staging)
        except OSError as error:
            raise ValueError(f"unsafe terminal staging could not be removed: {staging}") from error

    @staticmethod
    def _discard_path(path: Path) -> None:
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    @staticmethod
    def _load_worker_pid(payload: bytes | None) -> int | None:
        if payload is None:
            return None
        decoded = decode_strict_json_bytes(
            payload,
            label="job runtime",
            max_bytes=MAX_RUNTIME_BYTES,
        )
        if not isinstance(decoded, dict) or set(decoded) != {
            "schema_version",
            "worker_pid",
        }:
            raise ValueError("Invalid job runtime: unexpected fields")
        if decoded["schema_version"] != "yieldforge.job-runtime.v1":
            raise ValueError("Invalid job runtime: unsupported schema version")
        pid = decoded["worker_pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("Invalid job runtime: worker_pid must be a positive integer")
        return pid

    def _snapshot(self, state: _JobState) -> JobSnapshot:
        return JobSnapshot(
            job_id=state.job_id,
            status=state.status,
            created_at=state.created_at,
            updated_at=state.updated_at,
            latest_sequence=len(state.events),
            candidate_count=state.candidate_count,
            worker_pid=state.worker_pid,
            archive_path=state.archive_path,
            source_task_binding=state.request.source_task_binding,
            experiment_pair_id=state.request.experiment_pair_id,
            experiment_arm=state.request.experiment_arm,
            config=state.request.config,
            max_runtime_seconds=state.request.max_runtime_seconds,
            error_code=state.error_code,
            error_message=state.error_message,
        )

    def _require_job(self, job_id: str) -> _JobState:
        try:
            return self._jobs[job_id]
        except KeyError as error:
            raise KeyError(f"unknown solver job: {job_id}") from error

    @staticmethod
    def _write_new(path: Path, payload: str) -> None:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
        path.chmod(0o400)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
