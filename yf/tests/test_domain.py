import json

import pytest
from pydantic import ValidationError

from yieldforge.domain import Part, StripPackingProblem


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
