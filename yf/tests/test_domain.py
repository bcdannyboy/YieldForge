import json

import pytest
from pydantic import ValidationError

from yieldforge.domain import (
    Part,
    ProjectionMode,
    SolverProjectionBinding,
    SourceTaskBinding,
    StripPackingProblem,
)


def _projection_binding(**updates: object) -> SolverProjectionBinding:
    values: dict[str, object] = {
        "mode": ProjectionMode.SOURCE_AS_RECORDED,
        "transform_convention": "local_x_coordinate_negation_before_rotation",
        "projection_sha256": "a" * 64,
        "assumption_codes": (
            "interpret_s1_degenerate_entries_as_allowed_rotations",
            "interpret_s1_flip_x_as_local_x_coordinate_negation_before_rotation",
        ),
        "intervention_codes": (),
        "source_flip_part_count": 1,
    }
    values.update(updates)
    return SolverProjectionBinding.model_validate(values)


def test_projection_binding_requires_mode_consistent_interventions() -> None:
    assert _projection_binding().mode is ProjectionMode.SOURCE_AS_RECORDED

    with pytest.raises(ValidationError, match="recorded projection.*intervention"):
        _projection_binding(intervention_codes=("force_s1_flip_x_zero_for_ablation",))

    ablation = _projection_binding(
        mode=ProjectionMode.FORCE_FLIP_X_ZERO,
        intervention_codes=("force_s1_flip_x_zero_for_ablation",),
    )
    assert ablation.mode is ProjectionMode.FORCE_FLIP_X_ZERO


def test_projection_binding_requires_sorted_unique_codes_and_hash() -> None:
    with pytest.raises(ValidationError, match="sorted and unique"):
        _projection_binding(assumption_codes=("z", "a"))
    with pytest.raises(ValidationError, match="String should match pattern"):
        _projection_binding(projection_sha256="not-a-hash")


def test_source_binding_reads_legacy_payload_without_projection() -> None:
    binding = SourceTaskBinding.model_validate(
        {
            "dataset_id": "lectra-7030786-v1.1",
            "source_slice_sha256": "b" * 64,
            "tasks_index": 17,
            "acknowledged_assumption_codes": (),
        }
    )

    assert binding.solver_projection is None


def test_part_closes_an_open_polygon() -> None:
    part = Part(
        id="square",
        shape=[(0, 0), (2, 0), (2, 2), (0, 2)],
        demand=1,
        allowed_orientations=[0],
    )

    assert part.shape == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]


def test_part_rejects_non_positive_demand() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        Part(
            id="square",
            shape=[(0, 0), (1, 0), (1, 1), (0, 0)],
            demand=0,
            allowed_orientations=[0],
        )


def test_problem_rejects_duplicate_part_ids() -> None:
    part = Part(
        id="duplicate",
        shape=[(0, 0), (1, 0), (1, 1), (0, 0)],
        demand=1,
        allowed_orientations=[0],
    )

    with pytest.raises(ValidationError, match="part IDs must be unique"):
        StripPackingProblem(
            name="duplicates",
            strip_height=10,
            sheet_length=20,
            parts=[part, part],
        )


def test_problem_loads_from_canonical_json() -> None:
    raw = json.dumps(
        {
            "name": "json-round-trip",
            "strip_height": 10,
            "sheet_length": 20,
            "parts": [
                {
                    "id": "triangle",
                    "shape": [[0, 0], [2, 0], [1, 1]],
                    "demand": 2,
                    "allowed_orientations": [0, 180],
                }
            ],
        }
    )

    problem = StripPackingProblem.model_validate_json(raw)

    assert problem.name == "json-round-trip"
    assert problem.parts[0].shape[-1] == problem.parts[0].shape[0]
    assert problem.model_dump(mode="json")["sheet_length"] == 20.0
