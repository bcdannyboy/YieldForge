from pathlib import Path

import pytest

from yieldforge.domain import SpyrrowRunConfig, StripPackingProblem
from yieldforge.spyrrow_adapter import SpyrrowAdapter


@pytest.mark.integration
def test_installed_spyrrow_generates_a_feasible_candidate() -> None:
    fixture = Path(__file__).parents[1] / "benchmarks" / "static" / "m0-smoke.json"
    problem = StripPackingProblem.model_validate_json(fixture.read_text())

    batch = SpyrrowAdapter().generate(
        problem,
        SpyrrowRunConfig(seed=0, total_computation_time=1, num_workers=1),
    )

    assert batch.solver.spyrrow_version == "0.9.0"
    assert batch.candidates
    assert all(candidate.width <= problem.sheet_length for candidate in batch.candidates)
    assert all(
        len(candidate.placements) == sum(part.demand for part in problem.parts)
        for candidate in batch.candidates
    )


@pytest.mark.integration
def test_installed_spyrrow_streams_the_same_candidates_it_returns() -> None:
    fixture = Path(__file__).parents[1] / "benchmarks" / "static" / "m0-smoke.json"
    problem = StripPackingProblem.model_validate_json(fixture.read_text())
    callback_ids: list[str] = []

    result = SpyrrowAdapter().run(
        problem,
        SpyrrowRunConfig(seed=0, total_computation_time=1, num_workers=1),
        on_candidate=lambda candidate: callback_ids.append(candidate.candidate_id),
    )

    candidate_ids = [candidate.candidate_id for candidate in result.batch.candidates]
    assert callback_ids == candidate_ids
    assert result.final_candidate_id in candidate_ids
    assert result.terminal_observation_count == 1
    assert result.native_report_count + result.terminal_observation_count == (
        len(candidate_ids)
        + result.ignored_report_count
        + result.duplicate_candidate_count
        + result.sheet_overflow_count
    )
