from __future__ import annotations

import math
import random
import sys
import warnings
from dataclasses import fields

import numpy as np
import pytest

from yieldforge.oracle.columnar import (
    C0FrontierColumns,
    C0FrontierQuery,
    C0FrontierResult,
    certify_frontier_impossible_batch,
)
from yieldforge.oracle.frontier import (
    RejectionScalar,
    build_pareto_frontier,
    certify_frontier_impossible,
)


def _columns(
    vectors: tuple[tuple[float, float, float], ...],
) -> C0FrontierColumns:
    return C0FrontierColumns(
        areas=tuple(vector[0] for vector in vectors),
        widths=tuple(vector[1] for vector in vectors),
        heights=tuple(vector[2] for vector in vectors),
    )


def _query(row_id: int = 0, **overrides: object) -> C0FrontierQuery:
    values: dict[str, object] = {
        "row_id": row_id,
        "material_matches": True,
        "remnant_area": 1.0,
        "remnant_width": 1.0,
        "remnant_height": 1.0,
        "area_tolerance": 0.0,
        "coordinate_tolerance": 0.0,
    }
    values.update(overrides)
    return C0FrontierQuery(**values)  # type: ignore[arg-type]


def _scalar(
    candidate_id: str,
    area: float,
    width: float,
    height: float,
) -> RejectionScalar:
    return RejectionScalar(
        problem_id="problem-a",
        problem_sha256="sha256:" + "a" * 64,
        candidate_set_id="candidate-set-a",
        candidate_set_sha256="sha256:" + "b" * 64,
        candidate_id=candidate_id,
        source_transform_sha256="sha256:" + "c" * 64,
        material_partition="temporal_event:a",
        fit_config_sha256="sha256:" + "d" * 64,
        area=area,
        width=width,
        height=height,
    )


def test_columnar_records_are_frozen_and_slotted() -> None:
    records = (
        C0FrontierColumns(areas=(1.0,), widths=(1.0,), heights=(1.0,)),
        _query(),
        C0FrontierResult(row_id=0, all_impossible=False),
    )

    for record in records:
        assert record.__dataclass_params__.frozen  # type: ignore[attr-defined]
        assert tuple(field.name for field in fields(record)) == record.__slots__
        assert not hasattr(record, "__dict__")


def test_batch_matches_scalar_frontier_on_fixed_random_cases() -> None:
    randomizer = random.Random(20260827)
    scalar_frontier = build_pareto_frontier(
        tuple(
            _scalar(
                f"candidate-{index:03d}",
                randomizer.uniform(0.25, 40.0),
                randomizer.uniform(0.25, 15.0),
                randomizer.uniform(0.25, 15.0),
            )
            for index in range(40)
        )
    )
    frontier = C0FrontierColumns(
        areas=tuple(item.area for item in scalar_frontier.retained),
        widths=tuple(item.width for item in scalar_frontier.retained),
        heights=tuple(item.height for item in scalar_frontier.retained),
    )
    queries = tuple(
        C0FrontierQuery(
            row_id=row_id,
            material_matches=row_id % 3 != 0,
            remnant_area=randomizer.uniform(0.25, 45.0),
            remnant_width=randomizer.uniform(0.25, 18.0),
            remnant_height=randomizer.uniform(0.25, 18.0),
            area_tolerance=randomizer.uniform(0.0, 0.01),
            coordinate_tolerance=randomizer.uniform(0.0, 0.001),
        )
        for row_id in range(100)
    )

    results = certify_frontier_impossible_batch(frontier, queries)
    expected = tuple(
        certify_frontier_impossible(
            scalar_frontier,
            material_matches=query.material_matches,
            remnant_area=query.remnant_area,
            remnant_width=query.remnant_width,
            remnant_height=query.remnant_height,
            area_tolerance=query.area_tolerance,
            coordinate_tolerance=query.coordinate_tolerance,
        )
        for query in queries
    )

    assert tuple(result.row_id for result in results) == tuple(range(len(queries)))
    assert tuple(result.all_impossible for result in results) == expected
    assert all(type(result.row_id) is int for result in results)
    assert all(type(result.all_impossible) is bool for result in results)


def test_nonmatching_material_makes_every_nonempty_frontier_entry_impossible() -> None:
    result = certify_frontier_impossible_batch(
        _columns(((0.5, 0.5, 0.5),)),
        (_query(material_matches=False),),
    )

    assert result == (C0FrontierResult(row_id=0, all_impossible=True),)


def test_empty_frontier_fails_closed_for_every_material_and_preserves_query_order() -> None:
    queries = (
        _query(0, material_matches=False),
        _query(1, material_matches=True),
        _query(2, material_matches=False),
    )

    results = certify_frontier_impossible_batch(_columns(()), queries)

    assert results == tuple(
        C0FrontierResult(row_id=row_id, all_impossible=False) for row_id in range(len(queries))
    )


def test_empty_query_batch_returns_empty_results() -> None:
    assert (
        certify_frontier_impossible_batch(
            _columns(((2.0, 2.0, 2.0),)),
            (),
        )
        == ()
    )


def test_batch_preserves_dense_query_order() -> None:
    queries = (
        _query(0, remnant_area=0.5, remnant_width=0.5, remnant_height=0.5),
        _query(1, remnant_area=3.0, remnant_width=3.0, remnant_height=3.0),
        _query(2, material_matches=False),
    )

    results = certify_frontier_impossible_batch(
        _columns(((2.0, 2.0, 2.0),)),
        queries,
    )

    assert results == (
        C0FrontierResult(row_id=0, all_impossible=True),
        C0FrontierResult(row_id=1, all_impossible=False),
        C0FrontierResult(row_id=2, all_impossible=True),
    )


def test_batch_is_independent_of_frontier_column_order() -> None:
    vectors = (
        (8.0, 1.0, 1.0),
        (1.0, 8.0, 1.0),
        (1.0, 1.0, 8.0),
        (1.0, 1.0, 1.0),
    )
    queries = (
        _query(0, remnant_area=0.5, remnant_width=0.5, remnant_height=0.5),
        _query(1, remnant_area=2.0, remnant_width=2.0, remnant_height=2.0),
    )

    forward = certify_frontier_impossible_batch(_columns(vectors), queries)
    reverse = certify_frontier_impossible_batch(_columns(tuple(reversed(vectors))), queries)

    assert reverse == forward


@pytest.mark.parametrize("dimension", ("area", "width", "height"))
@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        (math.nextafter(1.0, -math.inf), False),
        (1.0, False),
        (math.nextafter(1.0, math.inf), True),
    ),
)
def test_batch_uses_strict_comparison_at_each_threshold(
    dimension: str,
    candidate: float,
    expected: bool,
) -> None:
    vector = {"area": 1.0, "width": 1.0, "height": 1.0}
    vector[dimension] = candidate

    result = certify_frontier_impossible_batch(
        _columns(((vector["area"], vector["width"], vector["height"]),)),
        (_query(),),
    )

    assert result[0].all_impossible is expected


@pytest.mark.parametrize("dimension", ("area", "width", "height"))
def test_tolerance_addition_uses_float64_rounding_before_strict_comparison(
    dimension: str,
) -> None:
    half_ulp = 2.0**-53
    assert 1.0 + half_ulp == 1.0
    vector = {"area": 1.0, "width": 1.0, "height": 1.0}
    vector[dimension] = math.nextafter(1.0, math.inf)
    query_overrides = {
        "area_tolerance": half_ulp if dimension == "area" else 0.0,
        "coordinate_tolerance": half_ulp if dimension != "area" else 0.0,
    }
    scalar_frontier = build_pareto_frontier(
        (_scalar("candidate-a", vector["area"], vector["width"], vector["height"]),)
    )
    query = _query(**query_overrides)

    result = certify_frontier_impossible_batch(_columns((tuple(vector.values()),)), (query,))
    expected = certify_frontier_impossible(
        scalar_frontier,
        material_matches=query.material_matches,
        remnant_area=query.remnant_area,
        remnant_width=query.remnant_width,
        remnant_height=query.remnant_height,
        area_tolerance=query.area_tolerance,
        coordinate_tolerance=query.coordinate_tolerance,
    )

    assert expected is True
    assert result[0].all_impossible is expected


def test_finite_threshold_addition_may_overflow_to_infinity_without_warning() -> None:
    maximum = sys.float_info.max
    query = _query(
        remnant_area=maximum,
        remnant_width=maximum,
        remnant_height=maximum,
        area_tolerance=maximum,
        coordinate_tolerance=maximum,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = certify_frontier_impossible_batch(
            _columns(((maximum, maximum, maximum),)),
            (query,),
        )

    assert result == (C0FrontierResult(row_id=0, all_impossible=False),)


@pytest.mark.parametrize("field", ("areas", "widths", "heights"))
@pytest.mark.parametrize(
    "value",
    (
        float("nan"),
        float("inf"),
        -float("inf"),
        np.float64(1.0),
        1,
        True,
    ),
)
def test_frontier_columns_reject_nonfinite_or_nonexact_floats(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "areas": (1.0,),
        "widths": (1.0,),
        "heights": (1.0,),
    }
    values[field] = (value,)

    with pytest.raises((TypeError, ValueError)):
        C0FrontierColumns(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ("areas", "widths", "heights"))
@pytest.mark.parametrize("value", (0.0, -1.0))
def test_frontier_columns_reject_nonpositive_dimensions(field: str, value: float) -> None:
    values = {
        "areas": (1.0,),
        "widths": (1.0,),
        "heights": (1.0,),
    }
    values[field] = (value,)

    with pytest.raises(ValueError, match="positive"):
        C0FrontierColumns(**values)


@pytest.mark.parametrize("value", ([1.0], np.asarray([1.0], dtype=np.float64)))
def test_frontier_columns_reject_caller_owned_array_like_containers(value: object) -> None:
    with pytest.raises(TypeError, match="tuples"):
        C0FrontierColumns(
            areas=value,  # type: ignore[arg-type]
            widths=(1.0,),
            heights=(1.0,),
        )


def test_frontier_columns_reject_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        C0FrontierColumns(
            areas=(1.0, 2.0),
            widths=(1.0,),
            heights=(1.0, 2.0),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("row_id", -1),
        ("row_id", True),
        ("row_id", np.int64(0)),
        ("material_matches", 1),
        ("material_matches", np.bool_(True)),
    ),
)
def test_query_rejects_invalid_identity_or_material_fields(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _query(**{field: value})


@pytest.mark.parametrize(
    "field",
    (
        "remnant_area",
        "remnant_width",
        "remnant_height",
        "area_tolerance",
        "coordinate_tolerance",
    ),
)
@pytest.mark.parametrize(
    "value",
    (
        float("nan"),
        float("inf"),
        -float("inf"),
        np.float64(1.0),
        1,
        True,
    ),
)
def test_query_rejects_nonfinite_or_nonexact_float_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _query(**{field: value})


@pytest.mark.parametrize("field", ("remnant_area", "remnant_width", "remnant_height"))
@pytest.mark.parametrize("value", (0.0, -1.0))
def test_query_rejects_nonpositive_dimensions(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        _query(**{field: value})


@pytest.mark.parametrize("field", ("area_tolerance", "coordinate_tolerance"))
def test_query_rejects_negative_tolerances(field: str) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        _query(**{field: -1.0})


@pytest.mark.parametrize(
    "queries",
    (
        (_query(1),),
        (_query(0), _query(0)),
        (_query(1), _query(0)),
        (_query(0), _query(2)),
    ),
)
def test_batch_rejects_non_dense_duplicate_or_reordered_row_ids(
    queries: tuple[C0FrontierQuery, ...],
) -> None:
    with pytest.raises(ValueError, match="dense input order"):
        certify_frontier_impossible_batch(_columns(((1.0, 1.0, 1.0),)), queries)


def test_batch_rejects_non_tuple_queries_and_foreign_records() -> None:
    frontier = _columns(((1.0, 1.0, 1.0),))

    with pytest.raises(TypeError, match="query tuple"):
        certify_frontier_impossible_batch(frontier, [_query()])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="query records"):
        certify_frontier_impossible_batch(frontier, (object(),))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="frontier columns"):
        certify_frontier_impossible_batch(object(), ())  # type: ignore[arg-type]
