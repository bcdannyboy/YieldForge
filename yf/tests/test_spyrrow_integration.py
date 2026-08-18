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
