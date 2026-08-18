from enum import Enum
from threading import Event
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from yieldforge.domain import (
    Candidate,
    CandidateReportType,
    Part,
    SpyrrowRunConfig,
    StripPackingProblem,
)
from yieldforge.spyrrow_adapter import SpyrrowAdapter


class FakeReportType(Enum):
    ExplFeas = 0
    ExplInfeas = 1
    ExplImproving = 2
    CmprFeas = 3
    Final = 4


def solution(width: float, x: float = 0) -> SimpleNamespace:
    return SimpleNamespace(
        width=width,
        density=0.5,
        placed_items=[SimpleNamespace(id="part-a", rotation=0.0, translation=(x, 0.0))],
    )


class FakeProgressQueue:
    def __init__(self, reports: list[tuple[FakeReportType, SimpleNamespace]]) -> None:
        self.reports = reports

    def drain(self) -> list[tuple[FakeReportType, SimpleNamespace]]:
        reports, self.reports = self.reports, []
        return reports


class FakeInstance:
    def __init__(self, api: "FakeSpyrrow") -> None:
        self.api = api

    def solve(self, config: object, *, progress: FakeProgressQueue) -> SimpleNamespace:
        self.api.solve_config = config
        self.api.solve_progress = progress
        if self.api.solve_error is not None:
            raise self.api.solve_error
        return self.api.final_solution


class FakeSpyrrow:
    ReportType = FakeReportType

    def __init__(self) -> None:
        self.progress_reports = [
            (FakeReportType.ExplFeas, solution(4.0)),
            (FakeReportType.ExplInfeas, solution(2.0)),
            (FakeReportType.ExplImproving, solution(2.0)),
            (FakeReportType.CmprFeas, solution(6.0)),
            (FakeReportType.CmprFeas, solution(4.0)),
            (FakeReportType.Final, solution(3.0, x=1.0)),
        ]
        self.final_solution = solution(3.0, x=1.0)
        self.solve_error: BaseException | None = None
        self.created_items: list[object] = []
        self.instance_args: tuple[object, ...] | None = None
        self.config_kwargs: dict[str, object] | None = None

    def Item(self, *args: object) -> object:
        item = SimpleNamespace(args=args)
        self.created_items.append(item)
        return item

    def StripPackingInstance(self, *args: object) -> FakeInstance:
        self.instance_args = args
        return FakeInstance(self)

    def StripPackingConfig(self, **kwargs: object) -> object:
        self.config_kwargs = kwargs
        return SimpleNamespace(**kwargs)

    def ProgressQueue(self) -> FakeProgressQueue:
        return FakeProgressQueue(list(self.progress_reports))


def problem() -> StripPackingProblem:
    return StripPackingProblem(
        name="adapter-test",
        strip_height=2,
        sheet_length=5,
        parts=[
            Part(
                id="part-a",
                shape=[(0, 0), (1, 0), (1, 1), (0, 1)],
                demand=1,
                allowed_orientations=[0, 90],
            )
        ],
    )


def test_adapter_maps_problem_and_reproducible_config() -> None:
    api = FakeSpyrrow()
    config = SpyrrowRunConfig(
        seed=17,
        total_computation_time=9,
        early_termination=False,
        num_workers=2,
        min_items_separation=0.25,
    )

    SpyrrowAdapter(api=api, spyrrow_version="test").generate(problem(), config)

    assert api.created_items[0].args == (
        "part-a",
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)],
        1,
        [0.0, 90.0],
    )
    assert api.instance_args == ("adapter-test", 2.0, api.created_items)
    assert api.config_kwargs == {
        "early_termination": False,
        "min_items_separation": 0.25,
        "num_workers": 2,
        "seed": 17,
        "total_computation_time": 9,
    }


def test_adapter_keeps_only_unique_feasible_candidates_that_fit_sheet() -> None:
    api = FakeSpyrrow()
    config = SpyrrowRunConfig(seed=17, total_computation_time=1)

    batch = SpyrrowAdapter(api=api, spyrrow_version="test").generate(problem(), config)

    assert [candidate.report_type for candidate in batch.candidates] == [
        CandidateReportType.EXPLORATION_FEASIBLE,
        CandidateReportType.FINAL,
    ]
    assert [candidate.width for candidate in batch.candidates] == [4.0, 3.0]
    assert len({candidate.candidate_id for candidate in batch.candidates}) == 2
    assert all(candidate.width <= batch.problem.sheet_length for candidate in batch.candidates)


def test_candidate_ids_are_stable_across_identical_runs() -> None:
    config = SpyrrowRunConfig(seed=17, total_computation_time=1)

    first = SpyrrowAdapter(api=FakeSpyrrow(), spyrrow_version="test").generate(problem(), config)
    second = SpyrrowAdapter(api=FakeSpyrrow(), spyrrow_version="test").generate(problem(), config)

    assert [candidate.candidate_id for candidate in first.candidates] == [
        candidate.candidate_id for candidate in second.candidates
    ]


def test_run_reports_progress_accounting_and_final_identity() -> None:
    api = FakeSpyrrow()
    callback_ids: list[str] = []

    result = SpyrrowAdapter(api=api, spyrrow_version="test").run(
        problem(),
        SpyrrowRunConfig(seed=17, total_computation_time=1),
        on_candidate=lambda candidate: callback_ids.append(candidate.candidate_id),
    )

    candidate_ids = [candidate.candidate_id for candidate in result.batch.candidates]
    assert callback_ids == candidate_ids
    assert [candidate.report_type for candidate in result.batch.candidates] == [
        CandidateReportType.EXPLORATION_FEASIBLE,
        CandidateReportType.FINAL,
    ]
    assert result.final_candidate_id == candidate_ids[-1]
    assert result.native_report_count == 7
    assert result.ignored_report_count == 2
    assert result.duplicate_candidate_count == 2
    assert result.sheet_overflow_count == 1


def test_run_result_is_a_strict_frozen_contract() -> None:
    result = SpyrrowAdapter(api=FakeSpyrrow(), spyrrow_version="test").run(
        problem(),
        SpyrrowRunConfig(seed=17, total_computation_time=1),
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(result).model_validate({**result.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError, match="Instance is frozen"):
        result.native_report_count = 0


def test_generate_remains_equivalent_to_run_batch() -> None:
    config = SpyrrowRunConfig(seed=17, total_computation_time=1)

    generated = SpyrrowAdapter(api=FakeSpyrrow(), spyrrow_version="test").generate(
        problem(), config
    )
    run_batch = (
        SpyrrowAdapter(api=FakeSpyrrow(), spyrrow_version="test").run(problem(), config).batch
    )

    assert generated.model_dump_json() == run_batch.model_dump_json()


class StreamingProgressQueue:
    def __init__(self, api: "StreamingFakeSpyrrow") -> None:
        self.api = api
        self.drained = False

    def drain(self) -> list[tuple[FakeReportType, SimpleNamespace]]:
        if self.drained:
            return []
        self.drained = True
        return [(FakeReportType.ExplFeas, solution(4.0))]


class StreamingInstance:
    def __init__(self, api: "StreamingFakeSpyrrow") -> None:
        self.api = api

    def solve(self, config: object, *, progress: StreamingProgressQueue) -> SimpleNamespace:
        self.api.callback_seen.wait(timeout=0.2)
        self.api.solve_finished = True
        return self.api.final_solution


class StreamingFakeSpyrrow(FakeSpyrrow):
    def __init__(self) -> None:
        super().__init__()
        self.callback_seen = Event()
        self.solve_finished = False

    def StripPackingInstance(self, *args: object) -> StreamingInstance:
        self.instance_args = args
        return StreamingInstance(self)

    def ProgressQueue(self) -> StreamingProgressQueue:
        return StreamingProgressQueue(self)


def test_run_invokes_callback_while_native_solver_is_still_running() -> None:
    api = StreamingFakeSpyrrow()
    solver_finished_at_callback: list[bool] = []

    def record_candidate(candidate: Candidate) -> None:
        solver_finished_at_callback.append(api.solve_finished)
        api.callback_seen.set()

    SpyrrowAdapter(api=api, spyrrow_version="test").run(
        problem(),
        SpyrrowRunConfig(seed=17, total_computation_time=1),
        on_candidate=record_candidate,
    )

    assert solver_finished_at_callback[0] is False


def test_run_preserves_native_solver_errors() -> None:
    api = FakeSpyrrow()
    api.solve_error = RuntimeError("native solver failed")

    with pytest.raises(RuntimeError, match="native solver failed"):
        SpyrrowAdapter(api=api, spyrrow_version="test").run(
            problem(),
            SpyrrowRunConfig(seed=17, total_computation_time=1),
        )
