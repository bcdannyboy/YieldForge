from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from yieldforge.datasets.corpus import (
    CoordinateUnitDto,
    CorpusSolveCapabilityDto,
    CorpusSourceDto,
    CorpusSummaryDto,
    SolveCapabilityDto,
    TaskDetailDto,
    TaskPageDto,
    TaskSourceDto,
    TaskSummaryDto,
)
from yieldforge.datasets.normalized_slice import (
    NormalizationStatus,
    ProjectionStatus,
    SupportStatus,
)
from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    CandidateReportType,
    Part,
    Placement,
    SolverIdentity,
    SourceTaskBinding,
    SpyrrowRunConfig,
    StripPackingProblem,
)
from yieldforge.workbench import app as app_module
from yieldforge.workbench.app import _stream_job_events, create_app, create_default_app
from yieldforge.workbench.contracts import (
    JobEvent,
    JobEventKind,
    JobSnapshot,
    JobStatus,
    SolveRequest,
)
from yieldforge.workbench.jobs import ActiveJobError

DATASET_ID = "lectra-7030786-v1.1"
SLICE_SHA256 = "d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8"
ASSUMPTION = "interpret_s1_degenerate_entries_as_allowed_rotations"
NOW = datetime(2026, 8, 18, tzinfo=UTC)


class _FakeCorpus:
    def __init__(self) -> None:
        self.project_calls: list[tuple[int, tuple[str, ...]]] = []
        self.list_calls: list[dict[str, object]] = []

    def _source(self) -> CorpusSourceDto:
        return CorpusSourceDto(
            dataset_id=DATASET_ID,
            doi="10.5281/zenodo.7030786",
            license="CC-BY-4.0",
            conversion_ruleset_version="lectra-slice-rules.v1",
            source_checksums=(),
            source_manifest_sha256="a" * 64,
            audit_report_sha256="b" * 64,
            slice_sha256=SLICE_SHA256,
            evidence_status="content_pinned_with_manifest_identity",
        )

    def summary(self) -> CorpusSummaryDto:
        return CorpusSummaryDto(
            source=self._source(),
            coordinate_unit=CoordinateUnitDto(literal_label="m^-4", interpretation=None),
            task_count=2,
            part_count=2,
            shape_count=1,
            constraint_count=1,
            support_status_counts=(),
            constraint_type_counts=(),
            solve_capability=CorpusSolveCapabilityDto(
                eligible_task_count=1,
                blocked_task_count=1,
                directly_supported_task_count=0,
            ),
        )

    def list_tasks(self, **values: object) -> TaskPageDto:
        self.list_calls.append(values)
        return TaskPageDto(items=(), next_cursor=None)

    def task_detail(self, tasks_index: int) -> TaskDetailDto:
        if tasks_index == 999:
            from yieldforge.datasets.corpus import TaskNotFoundError

            raise TaskNotFoundError(tasks_index)
        can_solve = tasks_index == 13958
        capability = SolveCapabilityDto(
            can_solve=can_solve,
            requires_assumption_acknowledgement=can_solve,
            normalization_status=NormalizationStatus.SOURCE_LOSSLESS,
            support_status=(
                SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
                if can_solve
                else SupportStatus.VIEW_ONLY
            ),
            projection_status=(
                ProjectionStatus.ELIGIBLE if can_solve else ProjectionStatus.BLOCKED
            ),
            assumption_codes=(ASSUMPTION,) if can_solve else (),
            reason_codes=() if can_solve else ("contains_non_s1_constraints",),
        )
        summary = TaskSummaryDto(
            task=TaskSourceDto(
                source_row_index=tasks_index,
                duration=1,
                efficiency=0.5,
                sheet_width=50.0,
                sheet_length=100.0,
                sheet_type=0,
                tasks_index=tasks_index,
                is_train=True,
                is_val=False,
                is_test=False,
            ),
            tasks_index=tasks_index,
            part_count=1,
            shape_count=1,
            constraint_count=1,
            constraint_types=("s1",) if can_solve else ("c8",),
            solve_capability=capability,
        )
        return TaskDetailDto(
            source=self._source(),
            coordinate_unit=CoordinateUnitDto(literal_label="m^-4", interpretation=None),
            summary=summary,
            parts=(),
            shapes=(),
            constraints=(),
            derived_geometry=(),
            constraint_value_columns=(),
            provenance=(),
        )

    def project_problem(
        self,
        tasks_index: int,
        *,
        acknowledged_assumption_codes: tuple[str, ...],
    ) -> StripPackingProblem:
        self.project_calls.append((tasks_index, acknowledged_assumption_codes))
        if tasks_index != 13958 or acknowledged_assumption_codes != (ASSUMPTION,):
            raise ValueError("task cannot be projected")
        return _problem()


def _problem() -> StripPackingProblem:
    return StripPackingProblem(
        name="lectra-task-13958",
        strip_height=50,
        sheet_length=100,
        parts=[
            Part(
                id="lectra:13958:part:1",
                shape=[(0, 0), (1, 0), (1, 1), (0, 1)],
                demand=1,
                allowed_orientations=[0, 90],
            )
        ],
    )


def _binding() -> SourceTaskBinding:
    return SourceTaskBinding(
        dataset_id=DATASET_ID,
        source_slice_sha256=SLICE_SHA256,
        tasks_index=13958,
        acknowledged_assumption_codes=(ASSUMPTION,),
    )


def _snapshot(status: JobStatus = JobStatus.QUEUED, *, sequence: int = 1) -> JobSnapshot:
    return JobSnapshot(
        job_id="job_abc",
        status=status,
        created_at=NOW,
        updated_at=NOW,
        latest_sequence=sequence,
        candidate_count=2 if status is JobStatus.COMPLETED else 0,
        worker_pid=4321,
        archive_path="/private/server/archive/job_abc" if status is JobStatus.COMPLETED else None,
        source_task_binding=_binding(),
    )


def _batch() -> CandidateBatch:
    problem = _problem()
    return CandidateBatch(
        problem=problem,
        solver=SolverIdentity(spyrrow_version="test", sparrow_revision="test"),
        config=SpyrrowRunConfig(seed=23, total_computation_time=1, num_workers=1),
        candidates=[
            Candidate(
                candidate_id="candidate-a",
                report_type=CandidateReportType.EXPLORATION_FEASIBLE,
                seed=23,
                width=12,
                density=0.4,
                placements=[
                    Placement(
                        part_id="lectra:13958:part:1",
                        rotation=0,
                        translation=(0, 0),
                    )
                ],
            ),
            Candidate(
                candidate_id="candidate-b",
                report_type=CandidateReportType.FINAL,
                seed=23,
                width=10,
                density=0.5,
                placements=[
                    Placement(
                        part_id="lectra:13958:part:1",
                        rotation=90,
                        translation=(10, 20),
                    )
                ],
            ),
        ],
    )


class _FakeJobs:
    def __init__(self, *, active_conflict: bool = False) -> None:
        self.active_conflict = active_conflict
        self.started: list[SolveRequest] = []
        self.cancel_calls = 0
        self._start_lock = threading.Lock()
        self._active = False
        self.snapshot = _snapshot()
        self.batch: CandidateBatch | None = None
        self.event_records: list[JobEvent] = []

    async def start(self, request: SolveRequest) -> JobSnapshot:
        with self._start_lock:
            if self.active_conflict or self._active:
                raise ActiveJobError("job_existing")
            self._active = True
            self.started.append(request)
        await asyncio.sleep(0.01)
        return self.snapshot

    def get(self, job_id: str) -> JobSnapshot:
        if job_id != self.snapshot.job_id:
            raise KeyError(job_id)
        return self.snapshot

    async def cancel(self, job_id: str) -> JobSnapshot:
        self.cancel_calls += 1
        self.get(job_id)
        self.snapshot = _snapshot(JobStatus.CANCELLED, sequence=2)
        return self.snapshot

    def events(self, job_id: str, *, after_sequence: int = 0) -> list[JobEvent]:
        self.get(job_id)
        return [event for event in self.event_records if event.sequence > after_sequence]

    def completed_batch(self, job_id: str) -> CandidateBatch | None:
        self.get(job_id)
        return self.batch

    def snapshots_for_source_task(
        self,
        *,
        dataset_id: str,
        source_slice_sha256: str,
        tasks_index: int,
    ) -> tuple[JobSnapshot, ...]:
        if (dataset_id, source_slice_sha256, tasks_index) != (
            DATASET_ID,
            SLICE_SHA256,
            13958,
        ):
            return ()
        return (self.snapshot,)


def _solve_payload(*, tasks_index: int = 13958, assumptions: list[str] | None = None) -> dict:
    return {
        "schema_version": "yieldforge.api-solver-job-request.v1",
        "tasks_index": tasks_index,
        "acknowledged_assumption_codes": assumptions if assumptions is not None else [ASSUMPTION],
        "seed": 23,
        "total_computation_time": 1,
        "early_termination": False,
        "min_items_separation": None,
        "max_runtime_seconds": 2.0,
    }


def _client(
    *, corpus: _FakeCorpus | None = None, jobs: _FakeJobs | None = None
) -> tuple[TestClient, _FakeCorpus, _FakeJobs]:
    corpus = corpus or _FakeCorpus()
    jobs = jobs or _FakeJobs()
    return TestClient(create_app(corpus=corpus, jobs=jobs)), corpus, jobs


def test_corpus_routes_delegate_bounded_queries_and_return_structured_not_found() -> None:
    client, corpus, _ = _client()

    summary = client.get("/api/corpus/summary")
    page = client.get("/api/tasks", params={"limit": 10, "status": "view_only"})
    missing = client.get("/api/tasks/999")

    assert summary.status_code == 200
    assert summary.json()["source"]["slice_sha256"] == SLICE_SHA256
    assert page.status_code == 200
    assert corpus.list_calls == [
        {
            "limit": 10,
            "cursor": None,
            "status": "view_only",
            "constraint_type": None,
            "task_id": None,
            "min_parts": None,
            "max_parts": None,
        }
    ]
    assert missing.status_code == 404
    assert missing.json()["code"] == "task_not_found"


def test_unexpected_server_failure_is_sanitized_and_structured() -> None:
    corpus = _FakeCorpus()

    def explode() -> CorpusSummaryDto:
        raise RuntimeError("database-password-is-secret")

    corpus.summary = explode  # type: ignore[method-assign]
    client = TestClient(create_app(corpus=corpus, jobs=_FakeJobs()), raise_server_exceptions=False)

    response = client.get("/api/corpus/summary")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("tasks_index", "assumptions", "code"),
    [
        (13958, [], "assumption_acknowledgement_mismatch"),
        (13958, [ASSUMPTION, "stale_assumption"], "assumption_acknowledgement_mismatch"),
        (25801, [], "task_not_solvable"),
    ],
)
def test_create_rejects_blocked_or_inexact_assumptions_before_spawn(
    tasks_index: int,
    assumptions: list[str],
    code: str,
) -> None:
    client, _, jobs = _client()

    response = client.post(
        "/api/solver-jobs",
        json=_solve_payload(tasks_index=tasks_index, assumptions=assumptions),
    )

    assert response.status_code == 422
    assert response.json()["code"] == code
    assert jobs.started == []


def test_create_projects_server_side_forces_one_worker_and_persists_binding() -> None:
    client, corpus, jobs = _client()

    response = client.post("/api/solver-jobs", json=_solve_payload())

    assert response.status_code == 202
    assert corpus.project_calls == [(13958, (ASSUMPTION,))]
    assert len(jobs.started) == 1
    persisted = jobs.started[0]
    assert persisted.problem == _problem()
    assert persisted.config.num_workers == 1
    assert persisted.source_task_binding == _binding()
    serialized = json.dumps(response.json())
    assert "worker_pid" not in serialized
    assert "archive_path" not in serialized
    assert "problem" not in serialized
    assert "/private/server" not in serialized


def test_public_numeric_runtime_fields_accept_json_integer_numbers() -> None:
    client, _, jobs = _client()
    payload = _solve_payload()
    payload["min_items_separation"] = 0
    payload["max_runtime_seconds"] = 2

    response = client.post("/api/solver-jobs", json=payload)

    assert response.status_code == 202
    assert jobs.started[0].config.min_items_separation == 0.0
    assert jobs.started[0].max_runtime_seconds == 2.0


def test_public_request_is_strict_and_does_not_accept_problem_workers_or_paths() -> None:
    client, _, jobs = _client()
    payload = _solve_payload()
    payload["problem"] = _problem().model_dump(mode="json")
    payload["num_workers"] = 9
    payload["output_path"] = "/tmp/attacker"

    response = client.post("/api/solver-jobs", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation"
    assert jobs.started == []


@pytest.mark.parametrize(
    "assumptions",
    [[ASSUMPTION, ASSUMPTION], ["z_assumption", ASSUMPTION]],
)
def test_public_request_rejects_duplicate_or_unsorted_acknowledgements(
    assumptions: list[str],
) -> None:
    client, _, jobs = _client()

    response = client.post(
        "/api/solver-jobs",
        json=_solve_payload(assumptions=assumptions),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation"
    assert jobs.started == []


def test_only_one_concurrent_create_is_accepted_and_conflict_names_active_job() -> None:
    corpus = _FakeCorpus()
    jobs = _FakeJobs()
    app = create_app(corpus=corpus, jobs=jobs)

    def submit() -> tuple[int, dict[str, Any]]:
        with TestClient(app) as client:
            response = client.post("/api/solver-jobs", json=_solve_payload())
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: submit(), range(2)))

    assert sorted(status for status, _ in results) == [202, 409]
    conflict = next(payload for status, payload in results if status == 409)
    assert conflict["code"] == "active_solver_job"
    assert conflict["details"]["active_job_id"] in {"job_abc", "job_existing"}
    assert len(jobs.started) == 1


def test_get_and_delete_job_are_sanitized_and_cancellation_is_idempotent() -> None:
    client, _, jobs = _client()

    queried = client.get("/api/solver-jobs/job_abc")
    cancelled = client.delete("/api/solver-jobs/job_abc")
    again = client.delete("/api/solver-jobs/job_abc")

    assert queried.status_code == cancelled.status_code == again.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert again.json()["status"] == "cancelled"
    assert all("worker_pid" not in response.text for response in (queried, cancelled, again))
    assert all("archive_path" not in response.text for response in (queried, cancelled, again))


def test_sse_replays_after_last_event_id_in_order_and_omits_terminal_batch() -> None:
    jobs = _FakeJobs()
    jobs.snapshot = _snapshot(JobStatus.COMPLETED, sequence=3)
    batch = _batch()
    jobs.batch = batch
    jobs.event_records = [
        JobEvent(
            job_id="job_abc",
            sequence=1,
            occurred_at=NOW,
            kind=JobEventKind.STATUS,
            status=JobStatus.QUEUED,
        ),
        JobEvent(
            job_id="job_abc",
            sequence=2,
            occurred_at=NOW,
            kind=JobEventKind.STATUS,
            status=JobStatus.RUNNING,
        ),
        JobEvent(
            job_id="job_abc",
            sequence=3,
            occurred_at=NOW,
            kind=JobEventKind.TERMINAL,
            status=JobStatus.COMPLETED,
            candidate_count=2,
            batch=batch,
        ),
    ]
    client, _, _ = _client(jobs=jobs)

    response = client.get(
        "/api/solver-jobs/job_abc/events",
        headers={"Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" not in response.text
    assert response.text.index("id: 2\n") < response.text.index("id: 3\n")
    assert '"kind":"terminal"' in response.text
    assert '"batch"' not in response.text
    assert "candidate-a" not in response.text


def test_sse_disconnect_returns_without_cancelling_job() -> None:
    jobs = _FakeJobs()
    jobs.event_records = [
        JobEvent(
            job_id="job_abc",
            sequence=1,
            occurred_at=NOW,
            kind=JobEventKind.STATUS,
            status=JobStatus.QUEUED,
        )
    ]

    class DisconnectingRequest:
        checks = 0

        async def is_disconnected(self) -> bool:
            self.checks += 1
            return self.checks > 1

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in _stream_job_events(
                DisconnectingRequest(),
                jobs,
                "job_abc",
                after_sequence=0,
                poll_interval_seconds=0,
            )
        ]

    chunks = asyncio.run(collect())

    assert chunks and chunks[0].startswith("id: 1\n")
    assert jobs.cancel_calls == 0


@pytest.mark.parametrize("last_event_id", ["-1", "+1", "01", "garbage", "2"])
def test_sse_rejects_noncanonical_last_event_id(last_event_id: str) -> None:
    client, _, _ = _client()

    response = client.get(
        "/api/solver-jobs/job_abc/events",
        headers={"Last-Event-ID": last_event_id},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_event_cursor"


def test_completed_candidate_pages_are_stable_and_running_jobs_are_rejected() -> None:
    jobs = _FakeJobs()
    jobs.snapshot = _snapshot(JobStatus.COMPLETED, sequence=3)
    jobs.batch = _batch()
    client, _, _ = _client(jobs=jobs)

    first = client.get("/api/solver-jobs/job_abc/candidates", params={"limit": 1})
    second = client.get(
        "/api/solver-jobs/job_abc/candidates",
        params={"limit": 1, "cursor": first.json()["next_cursor"]},
    )

    assert first.status_code == second.status_code == 200
    assert [item["candidate_id"] for item in first.json()["items"]] == ["candidate-a"]
    assert [item["candidate_id"] for item in second.json()["items"]] == ["candidate-b"]
    assert second.json()["next_cursor"] is None

    jobs.snapshot = _snapshot(JobStatus.RUNNING, sequence=2)
    jobs.batch = None
    blocked = client.get("/api/solver-jobs/job_abc/candidates")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "job_not_completed"


def test_candidate_cursor_and_candidate_id_errors_are_structured() -> None:
    jobs = _FakeJobs()
    jobs.snapshot = _snapshot(JobStatus.COMPLETED, sequence=3)
    jobs.batch = _batch()
    client, _, _ = _client(jobs=jobs)

    cursor = client.get(
        "/api/solver-jobs/job_abc/candidates",
        params={"cursor": "not-a-candidate"},
    )
    candidate = client.get("/api/solver-jobs/job_abc/candidates/not-a-candidate/geometry")

    assert cursor.status_code == 422
    assert cursor.json()["code"] == "invalid_candidate_cursor"
    assert candidate.status_code == 404
    assert candidate.json()["code"] == "candidate_not_found"


def test_candidate_geometry_applies_rotate_translate_then_svg_y_flip() -> None:
    jobs = _FakeJobs()
    jobs.snapshot = _snapshot(JobStatus.COMPLETED, sequence=3)
    jobs.batch = _batch()
    client, _, _ = _client(jobs=jobs)

    response = client.get("/api/solver-jobs/job_abc/candidates/candidate-b/geometry")

    assert response.status_code == 200
    body = response.json()
    assert body["sheet"] == {"length": 100.0, "width": 50.0}
    assert body["provenance"] == "derived"
    points = body["placements"][0]["svg_points"]
    expected = [[10.0, 30.0], [10.0, 29.0], [9.0, 29.0], [9.0, 30.0], [10.0, 30.0]]
    assert len(points) == len(expected)
    for observed, target in zip(points, expected, strict=True):
        assert observed == pytest.approx(target)


def test_completed_jobs_for_task_are_source_bound_and_hide_internal_state() -> None:
    jobs = _FakeJobs()
    jobs.snapshot = _snapshot(JobStatus.COMPLETED, sequence=3)
    jobs.batch = _batch()
    client, _, _ = _client(jobs=jobs)

    response = client.get("/api/tasks/13958/solver-jobs")

    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["items"]] == ["job_abc"]
    assert all(item["status"] == "completed" for item in response.json()["items"])
    assert "archive_path" not in response.text
    assert "worker_pid" not in response.text


def test_api_import_does_not_load_pandas_or_pickle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import yieldforge.workbench.app; "
            "assert 'pandas' not in sys.modules; assert 'pickle' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_default_factory_uses_server_owned_runtime_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = _FakeCorpus()
    jobs = _FakeJobs()
    observed: list[tuple[Path, Path]] = []

    monkeypatch.setenv("YIELDFORGE_WORKBENCH_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        app_module.CorpusQueryService,
        "from_repository",
        classmethod(lambda _cls: corpus),
    )

    def job_service(job_root: Path, archive_root: Path) -> _FakeJobs:
        observed.append((job_root, archive_root))
        return jobs

    monkeypatch.setattr(app_module, "SolverJobService", job_service)

    application = create_default_app()

    assert application.title == "YieldForge Research Workbench"
    assert observed == [
        (tmp_path / "runtime" / "jobs", tmp_path / "runtime" / "candidate-archives")
    ]
