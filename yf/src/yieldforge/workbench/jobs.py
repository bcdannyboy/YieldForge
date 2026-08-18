"""Durable, single-active-job supervision for disposable solver workers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yieldforge.archive import CandidateArchive, batch_content_hash, canonical_json
from yieldforge.domain import Candidate, CandidateBatch, SpyrrowRunResult
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


class WorkerProtocolError(RuntimeError):
    """A worker violated the bounded NDJSON protocol."""


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
        self._active_job_id: str | None = None
        self._service_lock = asyncio.Lock()
        self._recover_existing_jobs()

    async def start(self, request: SolveRequest) -> JobSnapshot:
        """Persist an immutable request and start its server-assigned worker."""

        validated = SolveRequest.model_validate(request.model_dump())
        async with self._service_lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if active.status not in TERMINAL_JOB_STATUSES:
                    raise RuntimeError("only one solver job may be active")
                self._active_job_id = None

            job_id = f"job_{uuid.uuid4().hex}"
            directory = self.job_root / job_id
            directory.mkdir(mode=0o700)
            self._write_new(directory / "request.json", canonical_json(validated) + "\n")
            now = self._now()
            state = _JobState(
                job_id=job_id,
                directory=directory,
                request=validated,
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job_id] = state
            self._append_event(state, kind=JobEventKind.STATUS, status=JobStatus.QUEUED)
            self._active_job_id = job_id
            state.runner = asyncio.create_task(self._run_job(state), name=f"solver-{job_id}")
            return self._snapshot(state)

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

    async def _run_job(self, state: _JobState) -> None:
        stdout_task: asyncio.Task[_ProtocolOutcome] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        process_task: asyncio.Task[int] | None = None
        cancel_task: asyncio.Task[bool] | None = None
        timeout_task: asyncio.Task[None] | None = None
        archive_staging = state.directory / "candidate-archive.staging"
        try:
            if state.cancel_requested.is_set():
                self._finish_terminal(state, JobStatus.CANCELLED)
                return
            try:
                process = await asyncio.create_subprocess_exec(
                    *self.worker_command,
                    "--request",
                    str(state.directory / "request.json"),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=self.max_stdout_line_bytes + 1,
                    start_new_session=True,
                )
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
            state.status = JobStatus.RUNNING
            self._append_event(state, kind=JobEventKind.STATUS, status=JobStatus.RUNNING)

            assert process.stdout is not None
            assert process.stderr is not None
            stdout_task = asyncio.create_task(self._consume_stdout(state, process.stdout))
            stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            process_task = asyncio.create_task(process.wait())
            cancel_task = asyncio.create_task(state.cancel_requested.wait())
            timeout_task = asyncio.create_task(
                asyncio.sleep(state.request.max_runtime_seconds)
            )

            while True:
                done, _ = await asyncio.wait(
                    {stdout_task, process_task, cancel_task, timeout_task},
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
                if process_task in done:
                    break

            return_code = await process_task
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

            archive_path = self.archive_root / state.job_id
            try:
                await asyncio.to_thread(
                    CandidateArchive.create, archive_staging, outcome.result.batch
                )
            except Exception:
                self._discard_path(archive_staging)
                self._finish_failed(state, "archive_failure", "solver worker failed")
                return
            if state.cancel_requested.is_set():
                self._discard_path(archive_staging)
                self._finish_terminal(state, JobStatus.CANCELLED)
                return
            if archive_path.exists() or archive_path.is_symlink():
                self._discard_path(archive_staging)
                self._finish_failed(state, "archive_failure", "solver worker failed")
                return
            archive_staging.rename(archive_path)
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
            if state.status is not JobStatus.COMPLETED:
                self._discard_path(archive_staging)
            if state.process is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self._stop_process(state.process))
            cleanup_tasks = (stdout_task, stderr_task, cancel_task, timeout_task, process_task)
            for task in cleanup_tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in cleanup_tasks if task is not None),
                return_exceptions=True,
            )
            async with self._service_lock:
                if self._active_job_id == state.job_id:
                    self._active_job_id = None

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
            request_path = directory / "request.json"
            events_path = directory / "events.jsonl"
            if request_path.is_symlink() or events_path.is_symlink():
                raise ValueError(f"persisted job contains a link: {directory.name}")
            if not request_path.is_file() or not events_path.is_file():
                continue
            request = SolveRequest.model_validate_json(request_path.read_bytes())
            events = [
                JobEvent.model_validate_json(line)
                for line in events_path.read_bytes().splitlines()
                if line
            ]
            if not events or any(
                event.job_id != directory.name or event.sequence != index
                for index, event in enumerate(events, start=1)
            ):
                raise ValueError(f"invalid persisted event sequence: {directory.name}")
            worker_pid = self._load_worker_pid(directory / "runtime.json")
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
            if terminal_path.is_symlink():
                raise ValueError(f"persisted terminal metadata is a link: {directory.name}")

            if last.kind is JobEventKind.TERMINAL:
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
                        raise ValueError(
                            f"completed archive is a link: {directory.name}"
                        )
                    archive_path = server_archive_path.resolve()
                    state.archive_path = str(archive_path)
                    state.completion_committed = True
                    self._validate_completed_archive(archive_path, last.batch)
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
                if terminal_path.is_file():
                    terminal = JobTerminalMetadata.model_validate_json(
                        terminal_path.read_bytes()
                    )
                    if (
                        terminal.status is JobStatus.COMPLETED
                        and terminal.archive_path != state.archive_path
                    ):
                        raise ValueError(
                            f"invalid completed archive path: {directory.name}"
                        )
                    if terminal != expected_terminal:
                        raise ValueError(f"invalid terminal metadata: {directory.name}")
                else:
                    self._write_new(
                        terminal_path,
                        expected_terminal.model_dump_json(exclude_none=True) + "\n",
                    )
                continue

            if terminal_path.exists():
                raise ValueError(f"terminal metadata has no terminal event: {directory.name}")
            self._discard_path(self.archive_root / state.job_id)
            self._discard_path(directory / "candidate-archive.staging")
            state.error_code = "supervisor_restart"
            state.error_message = "solver supervisor restarted"
            self._finish_terminal(state, JobStatus.FAILED)

    @staticmethod
    def _validate_completed_archive(archive_path: Path, batch: CandidateBatch) -> None:
        if archive_path.is_symlink() or not archive_path.is_dir():
            raise ValueError("completed archive is missing or is not a directory")
        members = {path.name: path for path in archive_path.iterdir()}
        if set(members) != {"manifest.json", "candidates.jsonl"} or any(
            path.is_symlink() or not path.is_file() for path in members.values()
        ):
            raise ValueError("completed archive has an invalid file inventory")
        try:
            manifest = json.loads(members["manifest.json"].read_text())
            candidates = [
                Candidate.model_validate_json(line)
                for line in members["candidates.jsonl"].read_bytes().splitlines()
                if line
            ]
        except Exception as error:
            raise ValueError("completed archive is malformed") from error
        expected_manifest = {
            "schema_version": "yieldforge.candidate-archive.v1",
            "candidate_count": len(batch.candidates),
            "batch_sha256": batch_content_hash(batch),
            "problem": batch.problem.model_dump(mode="json"),
            "solver": batch.solver.model_dump(mode="json"),
            "config": batch.config.model_dump(mode="json"),
        }
        if manifest != expected_manifest or candidates != batch.candidates:
            raise ValueError("completed archive does not match terminal batch")

    @staticmethod
    def _discard_path(path: Path) -> None:
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    @staticmethod
    def _load_worker_pid(path: Path) -> int | None:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text())
        pid = payload.get("worker_pid")
        return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None

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
