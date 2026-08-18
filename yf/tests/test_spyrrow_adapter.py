from enum import Enum
from types import SimpleNamespace

from yieldforge.domain import CandidateReportType, Part, SpyrrowRunConfig, StripPackingProblem
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
