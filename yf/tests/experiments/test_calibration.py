from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from yieldforge.datasets.corpus import CorpusQueryService
from yieldforge.datasets.passive_report import parse_normalized_slice
from yieldforge.domain import ProjectionMode, SourceTaskBinding
from yieldforge.experiments.calibration import (
    CalibrationApiClient,
    CalibrationCandidateObservation,
    CalibrationCellEvidence,
    evaluate_calibration,
    nearest_rank_percentile,
    registered_cells,
)
from yieldforge.experiments.contracts import (
    PureGeometryCalibrationProtocol,
    load_frozen_json,
)

YF_ROOT = Path(__file__).parents[2]
GEOMETRY_PROTOCOL_PATH = YF_ROOT / "experiments" / "pure-geometry-calibration-v1.json"
CATALOG_PATH = YF_ROOT / "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"
NOW = datetime(2026, 8, 22, tzinfo=UTC)


def _protocol() -> PureGeometryCalibrationProtocol:
    return load_frozen_json(GEOMETRY_PROTOCOL_PATH, PureGeometryCalibrationProtocol)


def test_registered_cells_are_exactly_the_frozen_calibration_population() -> None:
    protocol = _protocol()

    cells = registered_cells(protocol)

    assert len(cells) == 612
    assert {cell.tasks_index for cell in cells} == set(protocol.split.calibration_task_ids)
    assert not {cell.tasks_index for cell in cells} & set(protocol.split.evaluation_task_ids)
    assert {cell.seed for cell in cells} == {0, 1, 2, 3}
    assert {cell.seconds_per_seed for cell in cells} == {1, 3, 10}
    assert {cell.projection_mode for cell in cells} == {"source_as_recorded"}
    assert len({cell.cell_id for cell in cells}) == 612
    assert [(cell.tasks_index, cell.seconds_per_seed, cell.seed) for cell in cells[:12]] == [
        (protocol.split.calibration_task_ids[0], seconds, seed)
        for seconds in (1, 3, 10)
        for seed in (0, 1, 2, 3)
    ]


def test_nearest_rank_percentile_uses_the_registered_order_statistic() -> None:
    values = tuple(float(index) for index in range(1, 52))

    assert nearest_rank_percentile(values, 0.95) == 49.0


def _complete_evidence(
    *,
    missing_candidates: set[tuple[int, int]] | None = None,
    invalid_archives: set[tuple[int, int, int]] | None = None,
    widths: dict[tuple[int, int], float] | None = None,
) -> tuple[CalibrationCellEvidence, ...]:
    missing = missing_candidates or set()
    invalid = invalid_archives or set()
    selected_widths = widths or {}
    evidence = []
    for cell in registered_cells(_protocol()):
        archive_valid = (cell.tasks_index, cell.seconds_per_seed, cell.seed) not in invalid
        candidates: tuple[CalibrationCandidateObservation, ...] = ()
        if archive_valid and (cell.tasks_index, cell.seconds_per_seed) not in missing:
            best = selected_widths.get((cell.tasks_index, cell.seconds_per_seed), 100.0)
            if cell.seed in {0, 1}:
                candidates = (
                    CalibrationCandidateObservation(
                        candidate_id=f"cand_{cell.tasks_index}_{cell.seconds_per_seed}_{cell.seed}",
                        width=best if cell.seed == 0 else best * 1.004,
                        density=0.5,
                    ),
                )
        evidence.append(
            CalibrationCellEvidence(
                cell=cell,
                archive_valid=archive_valid,
                candidates=candidates,
            )
        )
    return tuple(evidence)


def test_selector_chooses_smallest_budget_meeting_every_frozen_limit() -> None:
    evaluation = evaluate_calibration(_protocol(), _complete_evidence())

    assert evaluation.valid is True
    assert evaluation.selected_seconds_per_seed == 1
    one_second = evaluation.comparisons[0]
    assert one_second.seconds_per_seed == 1
    assert one_second.qualifying_rate_gap_percentage_points == 0.0
    assert one_second.median_best_length_degradation_percent == 0.0
    assert one_second.p95_best_length_degradation_percent == 0.0
    assert one_second.valid_archive_rate_percent == 100.0
    assert one_second.passes is True


def test_one_missing_shorter_task_counts_as_infinite_without_leaving_denominator() -> None:
    task_id = _protocol().split.calibration_task_ids[0]

    evaluation = evaluate_calibration(
        _protocol(),
        _complete_evidence(missing_candidates={(task_id, 1)}),
    )

    one_second = evaluation.comparisons[0]
    assert one_second.missing_shorter_best_task_ids == (task_id,)
    assert one_second.qualifying_rate_gap_percentage_points == pytest.approx(100 / 51)
    assert one_second.median_best_length_degradation_percent == 0.0
    assert one_second.p95_best_length_degradation_percent == 0.0


def test_missing_ten_second_reference_invalidates_calibration() -> None:
    task_id = _protocol().split.calibration_task_ids[0]

    evaluation = evaluate_calibration(
        _protocol(),
        _complete_evidence(missing_candidates={(task_id, 10)}),
    )

    assert evaluation.valid is False
    assert evaluation.selected_seconds_per_seed is None
    assert evaluation.missing_reference_task_ids == (task_id,)


def test_selector_falls_back_to_ten_seconds_when_shorter_degradation_is_too_large() -> None:
    protocol = _protocol()
    widths = {
        (tasks_index, seconds): (101.0 if seconds in {1, 3} else 100.0)
        for tasks_index in protocol.split.calibration_task_ids
        for seconds in (1, 3, 10)
    }

    evaluation = evaluate_calibration(protocol, _complete_evidence(widths=widths))

    assert evaluation.valid is True
    assert evaluation.selected_seconds_per_seed == 10
    assert all(comparison.passes is False for comparison in evaluation.comparisons)
    assert all(
        comparison.median_best_length_degradation_percent == 1.0
        for comparison in evaluation.comparisons
    )


def test_archive_validity_uses_all_204_registered_cells_per_duration() -> None:
    protocol = _protocol()
    invalid = {
        (cell.tasks_index, 1, cell.seed)
        for cell in registered_cells(protocol)
        if cell.seconds_per_seed == 1
    }
    invalid = set(sorted(invalid)[:11])

    evaluation = evaluate_calibration(
        protocol,
        _complete_evidence(invalid_archives=invalid),
    )

    one_second = evaluation.comparisons[0]
    assert one_second.valid_archive_count == 193
    assert one_second.registered_cell_count == 204
    assert one_second.valid_archive_rate_percent == 193 / 204 * 100
    assert one_second.passes is False


class _HttpStub:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[tuple[int, object]]] = {}
        self.requests: list[tuple[str, str, object | None]] = []

    def respond(self, method: str, path: str, payload: object, *, status: int = 200) -> None:
        self.routes.setdefault((method, path), []).append((status, payload))


@contextmanager
def _serve(stub: _HttpStub) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            stub.requests.append((self.command, self.path, body))
            queue = stub.routes.get((self.command, self.path), [])
            if not queue:
                status, payload = 404, {"error": "missing stub route"}
            else:
                status, payload = queue.pop(0)
            encoded = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = _handle
        do_POST = _handle

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _catalog_service() -> CorpusQueryService:
    payload = CATALOG_PATH.read_bytes()
    return CorpusQueryService(
        parse_normalized_slice(payload, max_bytes=16 * 1024 * 1024),
        slice_sha256=hashlib.sha256(payload).hexdigest(),
        evidence_status="fully_bound_to_local_audit_evidence",
        cursor_signing_key=b"m2-calibration-test-key-material!",
    )


def _job_payload(tasks_index: int, *, status: str = "queued", candidate_count: int = 0) -> dict:
    service = _catalog_service()
    detail = service.task_detail(tasks_index)
    assumptions = detail.summary.solve_capability.assumption_codes
    projected = service.project_task(
        tasks_index,
        mode=ProjectionMode.SOURCE_AS_RECORDED,
        acknowledged_assumption_codes=assumptions,
        acknowledged_intervention_codes=(),
    )
    binding = SourceTaskBinding(
        dataset_id=service.summary().source.dataset_id,
        source_slice_sha256=service.summary().source.slice_sha256,
        tasks_index=tasks_index,
        acknowledged_assumption_codes=assumptions,
        solver_projection=projected.projection,
    )
    return {
        "schema_version": "yieldforge.api-job.v1",
        "job_id": "job-calibration-test",
        "status": status,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "latest_event_id": 2 if status == "completed" else 1,
        "candidate_count": candidate_count,
        "source_task_binding": binding.model_dump(mode="json"),
        "experiment_pair_id": None,
        "experiment_arm": None,
        "archive_available": status == "completed",
        "error_code": None,
        "error_message": None,
    }


def test_api_client_validates_corpus_and_submits_exact_registered_request() -> None:
    protocol = _protocol()
    cell = registered_cells(protocol)[0]
    service = _catalog_service()
    detail = service.task_detail(cell.tasks_index)
    stub = _HttpStub()
    stub.respond("GET", "/api/corpus/summary", service.summary().model_dump(mode="json"))
    stub.respond(
        "GET",
        f"/api/tasks/{cell.tasks_index}",
        detail.model_dump(mode="json"),
    )
    stub.respond("POST", "/api/solver-jobs", _job_payload(cell.tasks_index))

    with _serve(stub) as origin:
        client = CalibrationApiClient(origin)
        client.require_corpus(
            dataset_id=protocol.references.dataset_id,
            catalog_sha256=protocol.references.catalog_artifact_sha256,
            task_count=256,
            eligible_task_count=254,
        )
        observed_detail = client.task_detail(cell.tasks_index)
        job = client.submit(cell, observed_detail.summary.solve_capability.assumption_codes)

    assert job.job_id == "job-calibration-test"
    assert stub.requests[-1] == (
        "POST",
        "/api/solver-jobs",
        {
            "schema_version": "yieldforge.api-solver-job-request.v2",
            "tasks_index": cell.tasks_index,
            "projection_mode": "source_as_recorded",
            "acknowledged_assumption_codes": list(detail.summary.solve_capability.assumption_codes),
            "acknowledged_intervention_codes": [],
            "seed": cell.seed,
            "total_computation_time": cell.seconds_per_seed,
            "early_termination": False,
            "min_items_separation": None,
            "max_runtime_seconds": 60.0,
        },
    )


def test_api_client_paginates_candidates_and_rejects_cursor_cycles() -> None:
    first = {
        "schema_version": "yieldforge.api-candidate-page.v1",
        "items": [
            {
                "candidate_id": "cand_a",
                "report_type": "exploration_feasible",
                "seed": 0,
                "width": 100.0,
                "density": 0.5,
                "placement_count": 2,
            }
        ],
        "next_cursor": "cand_a",
    }
    second = {
        "schema_version": "yieldforge.api-candidate-page.v1",
        "items": [
            {
                "candidate_id": "cand_b",
                "report_type": "final",
                "seed": 0,
                "width": 99.9,
                "density": 0.51,
                "placement_count": 2,
            }
        ],
        "next_cursor": None,
    }
    stub = _HttpStub()
    stub.respond("GET", "/api/solver-jobs/job-1/candidates?limit=100", first)
    stub.respond("GET", "/api/solver-jobs/job-1/candidates?limit=100&cursor=cand_a", second)

    with _serve(stub) as origin:
        candidates = CalibrationApiClient(origin).candidates("job-1")

    assert [candidate.candidate_id for candidate in candidates] == ["cand_a", "cand_b"]

    cycling = _HttpStub()
    cycling.respond("GET", "/api/solver-jobs/job-1/candidates?limit=100", first)
    cycling.respond("GET", "/api/solver-jobs/job-1/candidates?limit=100&cursor=cand_a", first)
    with _serve(cycling) as origin:
        with pytest.raises(ValueError, match="candidate cursor repeated"):
            CalibrationApiClient(origin).candidates("job-1")


def test_api_client_requires_matching_completed_archive_evidence() -> None:
    protocol = _protocol()
    cell = registered_cells(protocol)[0]
    job = _job_payload(cell.tasks_index, status="completed", candidate_count=1)
    completed = {
        "schema_version": "yieldforge.api-completed-run-page.v1",
        "items": [
            {
                "schema_version": "yieldforge.api-completed-run.v1",
                "job": job,
                "settings": {
                    "seed": cell.seed,
                    "total_computation_time": cell.seconds_per_seed,
                    "num_workers": 1,
                    "early_termination": False,
                    "min_items_separation": None,
                    "max_runtime_seconds": 60.0,
                },
                "archive": {
                    "schema_version": "yieldforge.candidate-archive.v1",
                    "batch_sha256": "a" * 64,
                },
            }
        ],
    }
    candidates = {
        "schema_version": "yieldforge.api-candidate-page.v1",
        "items": [
            {
                "candidate_id": "cand_a",
                "report_type": "final",
                "seed": cell.seed,
                "width": 100.0,
                "density": 0.5,
                "placement_count": 2,
            }
        ],
        "next_cursor": None,
    }
    stub = _HttpStub()
    stub.respond(
        "GET",
        f"/api/tasks/{cell.tasks_index}/completed-runs?limit=100",
        completed,
    )
    stub.respond(
        "GET",
        "/api/solver-jobs/job-calibration-test/candidates?limit=100",
        candidates,
    )

    with _serve(stub) as origin:
        evidence = CalibrationApiClient(origin).completed_evidence(
            cell,
            "job-calibration-test",
        )

    assert evidence.batch_sha256 == "a" * 64
    assert [candidate.candidate_id for candidate in evidence.candidates] == ["cand_a"]


def test_api_client_rejects_unknown_response_fields() -> None:
    summary = _catalog_service().summary().model_dump(mode="json")
    summary["unexpected"] = True
    stub = _HttpStub()
    stub.respond("GET", "/api/corpus/summary", summary)

    with _serve(stub) as origin:
        with pytest.raises(ValueError, match="API response failed validation"):
            CalibrationApiClient(origin).require_corpus(
                dataset_id="lectra-7030786-v1.1",
                catalog_sha256=hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest(),
                task_count=256,
                eligible_task_count=254,
            )
