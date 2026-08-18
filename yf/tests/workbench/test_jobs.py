from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from yieldforge.archive import CandidateArchive, canonical_json
from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    CandidateReportType,
    Part,
    Placement,
    SolverIdentity,
    SpyrrowRunConfig,
    SpyrrowRunResult,
    StripPackingProblem,
)
from yieldforge.workbench import jobs as jobs_module
from yieldforge.workbench.contracts import (
    JobEvent,
    JobStatus,
    SolveRequest,
    WorkerMessage,
)
from yieldforge.workbench.jobs import SolverJobService


def make_request(*, budget: float = 2.0, problem_name: str = "job-test") -> SolveRequest:
    return SolveRequest(
        problem=StripPackingProblem(
            name=problem_name,
            strip_height=5,
            sheet_length=20,
            parts=[
                Part(
                    id="part-a",
                    shape=[(0, 0), (1, 0), (1, 1), (0, 1)],
                    demand=1,
                    allowed_orientations=[0],
                )
            ],
        ),
        config=SpyrrowRunConfig(seed=23, total_computation_time=1, num_workers=1),
        max_runtime_seconds=budget,
    )


def make_candidate(index: int) -> Candidate:
    return Candidate(
        candidate_id=f"candidate-{index:03d}",
        report_type=CandidateReportType.FINAL,
        seed=23,
        width=10 - index / 100,
        density=0.5,
        placements=[Placement(part_id="part-a", rotation=0, translation=(index, 0))],
    )


def make_result(request: SolveRequest, count: int) -> SpyrrowRunResult:
    candidates = [make_candidate(index) for index in range(count)]
    return SpyrrowRunResult(
        batch=CandidateBatch(
            problem=request.problem,
            solver=SolverIdentity(spyrrow_version="fake", sparrow_revision="fake"),
            config=request.config,
            candidates=candidates,
        ),
        final_candidate_id=candidates[-1].candidate_id if candidates else None,
        native_report_count=max(0, count - 1),
        terminal_observation_count=1,
        ignored_report_count=0,
        duplicate_candidate_count=0,
        sheet_overflow_count=0,
    )


def fake_command(mode: str) -> tuple[str, ...]:
    return (sys.executable, str(Path(__file__).resolve()), "--fake-worker", mode)


async def create_completed_job(tmp_path: Path, *, mode: str = "complete") -> tuple[Path, Path, str]:
    jobs = tmp_path / "jobs"
    archives = tmp_path / "archives"
    service = SolverJobService(jobs, archives, worker_command=fake_command(mode))
    created = await service.start(make_request())
    terminal = await service.wait(created.job_id)
    assert terminal.status is JobStatus.COMPLETED
    return jobs, archives, created.job_id


def run(coroutine: object) -> object:
    return asyncio.run(coroutine)


async def wait_until_running(service: SolverJobService, job_id: str) -> None:
    for _ in range(200):
        if service.get(job_id).status is JobStatus.RUNNING:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job did not start")


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_completed_job_persists_sampled_events_full_batch_and_archive(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("emit"),
            candidate_interval_seconds=0.2,
        )
        request = make_request(problem_name="../../attacker-path")

        created = await service.start(request)
        original_request = (service.job_directory(created.job_id) / "request.json").read_text()
        terminal = await service.wait(created.job_id)

        assert terminal.status is JobStatus.COMPLETED
        assert terminal.candidate_count == 20
        assert terminal.worker_pid is not None and terminal.worker_pid != os.getpid()
        assert not pid_is_alive(terminal.worker_pid)
        request_path = service.job_directory(created.job_id) / "request.json"
        assert request_path.read_text() == original_request
        assert stat.S_IMODE(request_path.stat().st_mode) & 0o222 == 0
        assert "attacker-path" not in str(service.job_directory(created.job_id))

        events = service.events(created.job_id)
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert events[0].status is JobStatus.QUEUED
        assert next(event for event in events if event.kind == "phase").phase == "solving"
        sampled = [event for event in events if event.kind == "candidate"]
        assert 1 <= len(sampled) < 20
        assert sampled[0].candidate.candidate_id == "candidate-000"
        assert sampled[-1].candidate.candidate_id == "candidate-019"
        assert events[-1].status is JobStatus.COMPLETED
        assert len(events[-1].batch.candidates) == 20

        archive = Path(terminal.archive_path)
        assert archive.is_dir()
        assert len((archive / "candidates.jsonl").read_text().splitlines()) == 20
        persisted = [
            JobEvent.model_validate_json(line)
            for line in (service.job_directory(created.job_id) / "events.jsonl")
            .read_text()
            .splitlines()
        ]
        assert persisted == events

    run(scenario())


@pytest.mark.parametrize("mode", ["hang", "ignore-term"])
def test_cancel_terminates_or_kills_worker_without_archive(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command(mode),
            terminate_grace_seconds=0.1,
        )
        created = await service.start(make_request())
        await wait_until_running(service, created.job_id)
        pid = service.get(created.job_id).worker_pid
        assert pid is not None and pid_is_alive(pid)

        terminal = await service.cancel(created.job_id)

        assert terminal.status is JobStatus.CANCELLED
        assert not pid_is_alive(pid)
        assert terminal.archive_path is None
        assert not (tmp_path / "archives" / created.job_id).exists()
        assert [event.status for event in service.events(created.job_id)][-2:] == [
            JobStatus.CANCELLING,
            JobStatus.CANCELLED,
        ]
        assert (await service.cancel(created.job_id)) == terminal

    run(scenario())


def test_timeout_is_distinct_and_leaves_no_worker_or_archive(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("hang"),
            terminate_grace_seconds=0.05,
        )
        created = await service.start(make_request(budget=1.0))
        terminal = await service.wait(created.job_id)

        assert terminal.status is JobStatus.TIMED_OUT
        assert terminal.worker_pid is not None and not pid_is_alive(terminal.worker_pid)
        assert terminal.archive_path is None
        assert not (tmp_path / "archives" / created.job_id).exists()

    run(scenario())


def test_failure_oversized_output_and_wrong_result_are_sanitized(tmp_path: Path) -> None:
    async def one(mode: str) -> tuple[SolverJobService, str]:
        root = tmp_path / mode
        service = SolverJobService(
            root / "jobs",
            root / "archives",
            worker_command=fake_command(mode),
            max_stdout_line_bytes=4096,
            max_stderr_bytes=128,
        )
        created = await service.start(make_request())
        terminal = await service.wait(created.job_id)
        assert terminal.status is JobStatus.FAILED
        assert terminal.error_message == "solver worker failed"
        assert terminal.error_code in {"solver_failure", "worker_protocol"}
        assert terminal.archive_path is None
        return service, created.job_id

    async def scenario() -> None:
        for mode in ("fail", "oversized", "wrong-request"):
            service, job_id = await one(mode)
            persisted = "\n".join(
                path.read_text(errors="replace")
                for path in service.job_directory(job_id).iterdir()
                if path.is_file()
            )
            assert "database-password-is-secret" not in persisted
            assert len(persisted) < 50_000

    run(scenario())


def test_only_one_job_can_be_active(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("hang"),
        )
        first = await service.start(make_request())
        await wait_until_running(service, first.job_id)

        with pytest.raises(RuntimeError, match="one solver job"):
            await service.start(make_request())

        await service.cancel(first.job_id)

    run(scenario())


def test_cancel_before_spawn_never_transitions_back_to_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("complete"),
        )
        created = await service.start(make_request())

        terminal = await service.cancel(created.job_id)

        assert terminal.status is JobStatus.CANCELLED
        assert terminal.worker_pid is None
        assert [event.status for event in service.events(created.job_id)] == [
            JobStatus.QUEUED,
            JobStatus.CANCELLING,
            JobStatus.CANCELLED,
        ]

    run(scenario())


def test_accepted_cancel_wins_over_worker_completion_race(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("complete-on-term"),
            terminate_grace_seconds=0.2,
        )
        created = await service.start(make_request())
        await wait_until_running(service, created.job_id)

        terminal = await service.cancel(created.job_id)

        assert terminal.status is JobStatus.CANCELLED
        assert terminal.archive_path is None
        assert not (tmp_path / "archives" / created.job_id).exists()
        statuses = [event.status for event in service.events(created.job_id)]
        cancelling_index = statuses.index(JobStatus.CANCELLING)
        assert JobStatus.RUNNING not in statuses[cancelling_index + 1 :]

    run(scenario())


def test_buffered_candidate_cannot_regress_accepted_cancellation_to_running(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("buffered-hang"),
            terminate_grace_seconds=0.1,
        )
        created = await service.start(make_request())
        for _ in range(200):
            if service.get(created.job_id).candidate_count == 2:
                break
            await asyncio.sleep(0.01)
        assert service.get(created.job_id).candidate_count == 2

        terminal = await service.cancel(created.job_id)

        assert terminal.status is JobStatus.CANCELLED
        statuses = [event.status for event in service.events(created.job_id)]
        cancelling_index = statuses.index(JobStatus.CANCELLING)
        assert JobStatus.RUNNING not in statuses[cancelling_index + 1 :]

    run(scenario())


def test_cancel_during_archive_staging_wins_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_started = threading.Event()
    archive_created = threading.Event()
    allow_archive = threading.Event()
    original_create = CandidateArchive.create

    def slow_create(output: Path, batch: CandidateBatch) -> Path:
        archive_started.set()
        assert allow_archive.wait(timeout=2)
        created = original_create(output, batch)
        archive_created.set()
        return created

    monkeypatch.setattr(CandidateArchive, "create", slow_create)

    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("complete"),
        )
        created = await service.start(make_request())
        assert await asyncio.to_thread(archive_started.wait, 1)

        try:
            terminal = await asyncio.wait_for(service.cancel(created.job_id), timeout=0.5)
        finally:
            allow_archive.set()

        assert terminal.status is JobStatus.CANCELLED
        assert terminal.archive_path is None
        assert not (tmp_path / "archives" / created.job_id).exists()
        assert await asyncio.to_thread(archive_created.wait, 1)
        staging = service.job_directory(created.job_id) / "candidate-archive.staging"
        for _ in range(100):
            if not staging.exists():
                break
            await asyncio.sleep(0.01)
        assert not staging.exists()

    run(scenario())


def test_supervisor_shutdown_does_not_await_hung_archive_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_started = threading.Event()
    archive_created = threading.Event()
    allow_archive = threading.Event()
    original_create = CandidateArchive.create

    def slow_create(output: Path, batch: CandidateBatch) -> Path:
        archive_started.set()
        assert allow_archive.wait(timeout=2)
        created = original_create(output, batch)
        archive_created.set()
        return created

    monkeypatch.setattr(CandidateArchive, "create", slow_create)

    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("complete"),
        )
        created = await service.start(make_request())
        assert await asyncio.to_thread(archive_started.wait, 1)
        runner = service._jobs[created.job_id].runner
        assert runner is not None

        runner.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(runner, timeout=0.5)
        finally:
            allow_archive.set()

        assert await asyncio.to_thread(archive_created.wait, 1)
        staging = service.job_directory(created.job_id) / "candidate-archive.staging"
        for _ in range(100):
            if not staging.exists():
                break
            await asyncio.sleep(0.01)
        assert not staging.exists()
        assert not (tmp_path / "archives" / created.job_id).exists()

    run(scenario())


def test_hard_deadline_during_archive_staging_publishes_no_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_started = threading.Event()
    archive_created = threading.Event()
    allow_archive = threading.Event()
    original_create = CandidateArchive.create

    def slow_create(output: Path, batch: CandidateBatch) -> Path:
        archive_started.set()
        assert allow_archive.wait(timeout=3)
        created = original_create(output, batch)
        archive_created.set()
        return created

    monkeypatch.setattr(CandidateArchive, "create", slow_create)

    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("complete"),
        )
        created = await service.start(make_request(budget=1.0))
        assert await asyncio.to_thread(archive_started.wait, 1)

        try:
            terminal = await asyncio.wait_for(service.wait(created.job_id), timeout=1.5)
        finally:
            allow_archive.set()

        assert terminal.status is JobStatus.TIMED_OUT
        assert terminal.archive_path is None
        assert not (tmp_path / "archives" / created.job_id).exists()
        assert await asyncio.to_thread(archive_created.wait, 1)
        staging = service.job_directory(created.job_id) / "candidate-archive.staging"
        for _ in range(100):
            if not staging.exists():
                break
            await asyncio.sleep(0.01)
        assert not staging.exists()

    run(scenario())


def test_hard_deadline_bounds_subprocess_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    never_spawned = asyncio.Event()

    async def stalled_spawn(*_args: object, **_kwargs: object) -> object:
        await never_spawned.wait()
        raise AssertionError("spawn should have been cancelled at the hard deadline")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", stalled_spawn)

    async def scenario() -> None:
        service = SolverJobService(tmp_path / "jobs", tmp_path / "archives")
        created = await service.start(make_request(budget=1.0))
        runner = service._jobs[created.job_id].runner
        assert runner is not None
        try:
            terminal = await asyncio.wait_for(service.wait(created.job_id), timeout=1.5)
            assert terminal.status is JobStatus.TIMED_OUT
            assert terminal.worker_pid is None
            assert terminal.archive_path is None
        finally:
            if not runner.done():
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner

    run(scenario())


def test_cancel_kills_worker_descendants_in_its_process_group(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("child-ignore-term"),
            terminate_grace_seconds=0.1,
        )
        created = await service.start(make_request())
        await wait_until_running(service, created.job_id)
        child_path = service.job_directory(created.job_id) / "child.pid"
        for _ in range(200):
            if child_path.is_file():
                break
            await asyncio.sleep(0.01)
        child_pid = int(child_path.read_text())
        try:
            terminal = await service.cancel(created.job_id)
            assert terminal.status is JobStatus.CANCELLED
            for _ in range(100):
                if not pid_is_alive(child_pid):
                    break
                await asyncio.sleep(0.01)
            assert not pid_is_alive(child_pid)
        finally:
            if pid_is_alive(child_pid):
                os.kill(child_pid, signal.SIGKILL)

    run(scenario())


def test_exceptional_supervisor_cancellation_kills_worker_process_group(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SolverJobService(
            tmp_path / "jobs",
            tmp_path / "archives",
            worker_command=fake_command("child-ignore-term"),
            terminate_grace_seconds=0.1,
        )
        created = await service.start(make_request())
        await wait_until_running(service, created.job_id)
        child_path = service.job_directory(created.job_id) / "child.pid"
        for _ in range(200):
            if child_path.is_file():
                break
            await asyncio.sleep(0.01)
        child_pid = int(child_path.read_text())
        runner = service._jobs[created.job_id].runner
        assert runner is not None
        try:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
            for _ in range(100):
                if not pid_is_alive(child_pid):
                    break
                await asyncio.sleep(0.01)
            assert not pid_is_alive(child_pid)
        finally:
            if pid_is_alive(child_pid):
                os.kill(child_pid, signal.SIGKILL)

    run(scenario())


def test_recovery_rejects_completed_metadata_with_non_server_archive(tmp_path: Path) -> None:
    async def create_complete() -> tuple[Path, Path]:
        jobs = tmp_path / "jobs"
        archives = tmp_path / "archives"
        service = SolverJobService(jobs, archives, worker_command=fake_command("complete"))
        created = await service.start(make_request())
        await service.wait(created.job_id)
        return jobs, archives

    jobs, archives = run(create_complete())
    terminal_path = next(jobs.iterdir()) / "terminal.json"
    payload = json.loads(terminal_path.read_text())
    payload["archive_path"] = str(tmp_path / "attacker-archive")
    terminal_path.chmod(0o600)
    terminal_path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="completed archive"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


def test_recovery_reconstructs_metadata_from_durable_completed_event(tmp_path: Path) -> None:
    async def create_complete() -> tuple[Path, Path, str]:
        jobs = tmp_path / "jobs"
        archives = tmp_path / "archives"
        service = SolverJobService(jobs, archives, worker_command=fake_command("complete"))
        created = await service.start(make_request())
        await service.wait(created.job_id)
        return jobs, archives, created.job_id

    jobs, archives, job_id = run(create_complete())
    terminal_path = jobs / job_id / "terminal.json"
    terminal_path.unlink()

    recovered = SolverJobService(jobs, archives, worker_command=fake_command("complete"))

    assert recovered.get(job_id).status is JobStatus.COMPLETED
    assert terminal_path.is_file()
    assert [event.status for event in recovered.events(job_id)].count(JobStatus.COMPLETED) == 1


def test_recovery_rejects_archive_content_that_disagrees_with_terminal_batch(
    tmp_path: Path,
) -> None:
    async def create_complete() -> tuple[Path, Path, str]:
        jobs = tmp_path / "jobs"
        archives = tmp_path / "archives"
        service = SolverJobService(jobs, archives, worker_command=fake_command("complete"))
        created = await service.start(make_request())
        await service.wait(created.job_id)
        return jobs, archives, created.job_id

    jobs, archives, job_id = run(create_complete())
    candidates_path = archives / job_id / "candidates.jsonl"
    payload = json.loads(candidates_path.read_text().splitlines()[0])
    payload["width"] = payload["width"] + 1
    candidates_path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="does not match terminal batch"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


def test_recovery_rejects_completed_archive_symlink_even_if_metadata_follows_it(
    tmp_path: Path,
) -> None:
    async def create_complete() -> tuple[Path, Path, str]:
        jobs = tmp_path / "jobs"
        archives = tmp_path / "archives"
        service = SolverJobService(jobs, archives, worker_command=fake_command("complete"))
        created = await service.start(make_request())
        await service.wait(created.job_id)
        return jobs, archives, created.job_id

    jobs, archives, job_id = run(create_complete())
    server_archive = archives / job_id
    moved_archive = tmp_path / "moved-archive"
    server_archive.rename(moved_archive)
    server_archive.symlink_to(moved_archive, target_is_directory=True)
    terminal_path = jobs / job_id / "terminal.json"
    payload = json.loads(terminal_path.read_text())
    payload["archive_path"] = str(moved_archive)
    terminal_path.chmod(0o600)
    terminal_path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="completed archive.*link"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


def test_recovery_rebinds_completed_event_and_archive_to_immutable_request(
    tmp_path: Path,
) -> None:
    async def create_complete() -> tuple[Path, Path, str]:
        jobs = tmp_path / "jobs"
        archives = tmp_path / "archives"
        service = SolverJobService(jobs, archives, worker_command=fake_command("complete"))
        created = await service.start(make_request())
        await service.wait(created.job_id)
        return jobs, archives, created.job_id

    jobs, archives, job_id = run(create_complete())
    request_path = jobs / job_id / "request.json"
    payload = json.loads(request_path.read_text())
    payload["problem"]["name"] = "different-immutable-request"
    request_path.chmod(0o600)
    request_path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="does not match solve request"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


@pytest.mark.parametrize("mutation", ["transition", "candidate_count", "candidate_content"])
def test_recovery_rejects_internally_invalid_event_history(tmp_path: Path, mutation: str) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))
    events_path = jobs / job_id / "events.jsonl"
    records = [json.loads(line) for line in events_path.read_text().splitlines()]
    phase_index = next(index for index, record in enumerate(records) if record["kind"] == "phase")
    candidate_index = next(
        index for index, record in enumerate(records) if record["kind"] == "candidate"
    )
    if mutation == "transition":
        records[phase_index].pop("phase")
        records[phase_index]["kind"] = "status"
        records[phase_index]["status"] = "cancelling"
    elif mutation == "candidate_count":
        records[phase_index]["candidate_count"] = 2
    else:
        records[candidate_index]["candidate"]["width"] += 1
    events_path.write_text("".join(json.dumps(record) + "\n" for record in records))

    with pytest.raises(ValueError, match="event history"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


def test_recovery_reads_evidence_without_path_read_convenience_methods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))

    def forbidden_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("recovery must use one bounded non-following descriptor")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    monkeypatch.setattr(Path, "read_text", forbidden_read)

    recovered = SolverJobService(jobs, archives, worker_command=fake_command("complete"))

    assert recovered.get(job_id).status is JobStatus.COMPLETED


def test_recovery_rejects_runtime_symlink_without_exposing_outside_pid(tmp_path: Path) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))
    runtime_path = jobs / job_id / "runtime.json"
    runtime_path.unlink()
    outside = tmp_path / "outside-runtime.json"
    outside.write_text(
        json.dumps(
            {
                "schema_version": "yieldforge.job-runtime.v1",
                "worker_pid": os.getpid(),
            }
        )
        + "\n"
    )
    runtime_path.symlink_to(outside)

    with pytest.raises(ValueError, match="runtime.*regular file"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


def test_recovery_rejects_unbounded_runtime_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))
    runtime_path = jobs / job_id / "runtime.json"
    runtime_path.chmod(0o600)
    runtime_path.write_text(json.dumps({"worker_pid": os.getpid(), "padding": "x" * 100}) + "\n")
    monkeypatch.setattr(jobs_module, "MAX_RUNTIME_BYTES", 32, raising=False)

    with pytest.raises(ValueError, match="runtime.*size limit"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


def test_recovery_rejects_unbounded_archive_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))
    monkeypatch.setattr(jobs_module, "MAX_ARCHIVE_CANDIDATES_BYTES", 32, raising=False)

    with pytest.raises(ValueError, match="archive candidates.*size limit"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))


def test_recovery_removes_server_owned_staging_from_terminal_job(tmp_path: Path) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))
    staging = jobs / job_id / "candidate-archive.staging"
    staging.mkdir()
    (staging / "leftover").write_text("server-owned generated state")

    recovered = SolverJobService(jobs, archives, worker_command=fake_command("complete"))

    assert recovered.get(job_id).status is JobStatus.COMPLETED
    assert not staging.exists()


def test_recovery_removes_terminal_staging_before_rejecting_other_evidence(
    tmp_path: Path,
) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))
    staging = jobs / job_id / "candidate-archive.staging"
    staging.mkdir()
    (staging / "leftover").write_text("server-owned generated state")
    (archives / job_id / "manifest.json").chmod(0o600)
    (archives / job_id / "manifest.json").write_text("{}\n")

    with pytest.raises(ValueError, match="archive does not match"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))
    assert not staging.exists()


@pytest.mark.parametrize("unsafe_kind", ["file", "symlink"])
def test_recovery_fails_visibly_for_unsafe_terminal_staging(
    tmp_path: Path, unsafe_kind: str
) -> None:
    jobs, archives, job_id = run(create_completed_job(tmp_path))
    staging = jobs / job_id / "candidate-archive.staging"
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    if unsafe_kind == "file":
        staging.write_text("not a server-owned staging directory")
    else:
        staging.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe terminal staging"):
        SolverJobService(jobs, archives, worker_command=fake_command("complete"))
    assert outside.is_dir()


def test_restart_marks_unterminated_job_failed_without_signalling_stale_pid(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    archives = tmp_path / "archives"
    job_dir = jobs / "job_stale"
    job_dir.mkdir(parents=True)
    request = make_request()
    (job_dir / "request.json").write_text(canonical_json(request) + "\n")
    queued = JobEvent(
        job_id="job_stale",
        sequence=1,
        occurred_at="2026-08-17T00:00:00Z",
        kind="status",
        status="queued",
    )
    (job_dir / "events.jsonl").write_text(queued.model_dump_json() + "\n")
    (job_dir / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": "yieldforge.job-runtime.v1",
                "worker_pid": os.getpid(),
            }
        )
        + "\n"
    )

    service = SolverJobService(jobs, archives, worker_command=fake_command("complete"))

    snapshot = service.get("job_stale")
    assert snapshot.status is JobStatus.FAILED
    assert snapshot.error_code == "supervisor_restart"
    assert snapshot.worker_pid == os.getpid()
    assert pid_is_alive(os.getpid())
    assert [event.sequence for event in service.events("job_stale")] == [1, 2]
    assert (job_dir / "terminal.json").is_file()


def _fake_worker_main(mode: str, request_path: Path) -> int:
    request = SolveRequest.model_validate_json(request_path.read_bytes())

    def emit(message: WorkerMessage) -> None:
        print(message.model_dump_json(exclude_none=True), flush=True)

    emit(WorkerMessage(kind="phase", phase="solving"))
    if mode == "fail":
        print("database-password-is-secret" * 100, file=sys.stderr, flush=True)
        emit(
            WorkerMessage(
                kind="failure",
                error_code="solver_failure",
                error_message="solver worker failed",
            )
        )
        return 1
    if mode == "oversized":
        print("x" * 20_000, flush=True)
        return 1
    if mode == "wrong-request":
        wrong_problem = request.problem.model_copy(update={"name": "wrong-problem"})
        wrong_request = request.model_copy(update={"problem": wrong_problem})
        result = make_result(wrong_request, 1)
        emit(WorkerMessage(kind="candidate", candidate=result.batch.candidates[0]))
        emit(WorkerMessage(kind="complete", result=result))
        return 0
    if mode == "hang":
        time.sleep(60)
        return 0
    if mode == "buffered-hang":
        result = make_result(request, 2)
        for item in result.batch.candidates:
            emit(WorkerMessage(kind="candidate", candidate=item))
        time.sleep(60)
        return 0
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
        return 0
    if mode == "child-ignore-term":
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
            ]
        )
        (request_path.parent / "child.pid").write_text(str(child.pid))
        time.sleep(60)
        return 0
    if mode == "complete-on-term":
        terminate_requested = False

        def request_completion(_signum: int, _frame: object) -> None:
            nonlocal terminate_requested
            terminate_requested = True

        signal.signal(signal.SIGTERM, request_completion)
        while not terminate_requested:
            time.sleep(0.01)
        result = make_result(request, 1)
        emit(WorkerMessage(kind="candidate", candidate=result.batch.candidates[0]))
        emit(WorkerMessage(kind="complete", result=result))
        return 0

    count = 20 if mode == "emit" else 1
    result = make_result(request, count)
    for item in result.batch.candidates:
        emit(WorkerMessage(kind="candidate", candidate=item))
    emit(WorkerMessage(kind="complete", result=result))
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked by subprocess tests
    mode_index = sys.argv.index("--fake-worker")
    request_index = sys.argv.index("--request")
    raise SystemExit(_fake_worker_main(sys.argv[mode_index + 1], Path(sys.argv[request_index + 1])))
