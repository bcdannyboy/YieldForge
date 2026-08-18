from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

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
from yieldforge.workbench import solver_worker
from yieldforge.workbench.contracts import SolveRequest, WorkerMessage


def make_request(*, workers: int = 1, budget: float = 2.0) -> SolveRequest:
    return SolveRequest(
        problem=StripPackingProblem(
            name="worker-test",
            strip_height=5,
            sheet_length=10,
            parts=[
                Part(
                    id="part-a",
                    shape=[(0, 0), (1, 0), (1, 1), (0, 1)],
                    demand=1,
                    allowed_orientations=[0],
                )
            ],
        ),
        config=SpyrrowRunConfig(
            seed=41,
            total_computation_time=1,
            num_workers=workers,
        ),
        max_runtime_seconds=budget,
    )


def candidate(candidate_id: str, width: float) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        report_type=CandidateReportType.FINAL,
        seed=41,
        width=width,
        density=0.5,
        placements=[Placement(part_id="part-a", rotation=0, translation=(0, 0))],
    )


def run_result(request: SolveRequest) -> SpyrrowRunResult:
    candidates = [candidate("candidate-a", 3), candidate("candidate-b", 2)]
    return SpyrrowRunResult(
        batch=CandidateBatch(
            problem=request.problem,
            solver=SolverIdentity(spyrrow_version="test", sparrow_revision="test"),
            config=request.config,
            candidates=candidates,
        ),
        final_candidate_id="candidate-b",
        native_report_count=1,
        terminal_observation_count=1,
        ignored_report_count=0,
        duplicate_candidate_count=0,
        sheet_overflow_count=0,
    )


def test_solve_request_requires_one_worker_and_at_most_ten_seconds() -> None:
    with pytest.raises(ValidationError, match="num_workers"):
        make_request(workers=2)
    with pytest.raises(ValidationError, match="less than or equal to 10"):
        make_request(budget=10.01)


def test_worker_emits_strict_ndjson_progress_and_complete_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_request()
    result = run_result(request)

    class FakeAdapter:
        def run(
            self,
            problem: StripPackingProblem,
            config: SpyrrowRunConfig,
            on_candidate: Callable[[Candidate], None],
        ) -> SpyrrowRunResult:
            assert problem == request.problem
            assert config == request.config
            for item in result.batch.candidates:
                on_candidate(item)
            return result

    monkeypatch.setattr(solver_worker, "SpyrrowAdapter", FakeAdapter)
    lines: list[str] = []

    return_code = solver_worker.execute_request(request, lines.append)

    messages = [WorkerMessage.model_validate_json(line) for line in lines]
    assert return_code == 0
    assert [message.kind for message in messages] == [
        "phase",
        "candidate",
        "candidate",
        "complete",
    ]
    assert messages[0].phase == "solving"
    assert messages[1].candidate == result.batch.candidates[0]
    assert messages[-1].result == result
    assert messages[-1].result.batch == result.batch


def test_worker_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingAdapter:
        def run(self, *_args: object, **_kwargs: object) -> SpyrrowRunResult:
            raise RuntimeError("database-password-is-secret")

    monkeypatch.setattr(solver_worker, "SpyrrowAdapter", FailingAdapter)
    lines: list[str] = []

    return_code = solver_worker.execute_request(make_request(), lines.append)

    message = WorkerMessage.model_validate_json(lines[-1])
    assert return_code == 1
    assert message.kind == "failure"
    assert message.error_code == "solver_failure"
    assert message.error_message == "solver worker failed"
    assert "secret" not in lines[-1]


def test_worker_main_reads_only_explicit_request_and_writes_ndjson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    request = make_request()
    request_path = tmp_path / "request.json"
    request_path.write_text(request.model_dump_json())
    expected = run_result(request)

    class FakeAdapter:
        def run(
            self,
            _problem: StripPackingProblem,
            _config: SpyrrowRunConfig,
            on_candidate: Callable[[Candidate], None],
        ) -> SpyrrowRunResult:
            on_candidate(expected.batch.candidates[0])
            return expected

    monkeypatch.setattr(solver_worker, "SpyrrowAdapter", FakeAdapter)

    assert solver_worker.main(["--request", str(request_path)]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert [WorkerMessage.model_validate_json(line).kind for line in output.out.splitlines()] == [
        "phase",
        "candidate",
        "complete",
    ]


def test_worker_message_contract_is_frozen_and_forbids_mixed_payloads() -> None:
    message = WorkerMessage(kind="phase", phase="solving")

    with pytest.raises(ValidationError, match="Extra inputs"):
        WorkerMessage.model_validate({**message.model_dump(), "extra": True})
    with pytest.raises(ValidationError, match="Instance is frozen"):
        message.phase = "changed"
    with pytest.raises(ValidationError):
        WorkerMessage(kind="phase", phase="solving", error_code="wrong")


def test_worker_request_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = make_request().model_dump(mode="json")
    payload["output_path"] = str(tmp_path / "attacker-controlled")

    with pytest.raises(ValidationError, match="Extra inputs"):
        SolveRequest.model_validate(payload)
