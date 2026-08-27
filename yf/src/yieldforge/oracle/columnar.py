"""Pure numeric M8 C0 frontier rejection batch."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def _require_dimension_columns(
    *,
    areas: object,
    widths: object,
    heights: object,
) -> None:
    columns = (areas, widths, heights)
    if any(type(column) is not tuple for column in columns):
        raise TypeError("C0 frontier columns must be exact tuples")
    if len(areas) != len(widths) or len(areas) != len(heights):  # type: ignore[arg-type]
        raise ValueError("C0 frontier columns must have the same length")
    for column in columns:
        for value in column:  # type: ignore[union-attr]
            if type(value) is not float:
                raise TypeError("C0 frontier dimensions must be exact Python floats")
            if not math.isfinite(value):
                raise ValueError("C0 frontier dimensions must be finite")
            if value <= 0.0:
                raise ValueError("C0 frontier dimensions must be positive")


@dataclass(frozen=True, slots=True)
class C0FrontierColumns:
    """Proof-owned numeric columns for one retained Pareto frontier."""

    areas: tuple[float, ...]
    widths: tuple[float, ...]
    heights: tuple[float, ...]

    def __post_init__(self) -> None:
        _require_dimension_columns(
            areas=self.areas,
            widths=self.widths,
            heights=self.heights,
        )


def _require_query(query: object) -> None:
    if type(query) is not C0FrontierQuery:
        raise TypeError("C0 frontier batch requires exact query records")
    if type(query.row_id) is not int:
        raise TypeError("C0 frontier row id must be an exact Python integer")
    if query.row_id < 0:
        raise ValueError("C0 frontier row id must be nonnegative")
    if type(query.material_matches) is not bool:
        raise TypeError("C0 frontier material match must be an exact Python boolean")
    measurements = (
        query.remnant_area,
        query.remnant_width,
        query.remnant_height,
        query.area_tolerance,
        query.coordinate_tolerance,
    )
    if any(type(value) is not float for value in measurements):
        raise TypeError("C0 frontier query measurements must be exact Python floats")
    if any(not math.isfinite(value) for value in measurements):
        raise ValueError("C0 frontier query measurements must be finite")
    if any(
        value <= 0.0
        for value in (
            query.remnant_area,
            query.remnant_width,
            query.remnant_height,
        )
    ):
        raise ValueError("C0 frontier query dimensions must be positive")
    if query.area_tolerance < 0.0 or query.coordinate_tolerance < 0.0:
        raise ValueError("C0 frontier query tolerances must be nonnegative")


@dataclass(frozen=True, slots=True)
class C0FrontierQuery:
    """One dense, ordered numeric frontier query."""

    row_id: int
    material_matches: bool
    remnant_area: float
    remnant_width: float
    remnant_height: float
    area_tolerance: float
    coordinate_tolerance: float

    def __post_init__(self) -> None:
        _require_query(self)


@dataclass(frozen=True, slots=True)
class C0FrontierResult:
    """One dense row result returned as an exact Python boolean."""

    row_id: int
    all_impossible: bool


def certify_frontier_impossible_batch(
    frontier: C0FrontierColumns,
    queries: tuple[C0FrontierQuery, ...],
) -> tuple[C0FrontierResult, ...]:
    """Apply frozen necessary-fit inequalities to a dense query batch."""

    if type(frontier) is not C0FrontierColumns:
        raise TypeError("C0 frontier batch requires exact frontier columns")
    _require_dimension_columns(
        areas=frontier.areas,
        widths=frontier.widths,
        heights=frontier.heights,
    )
    if type(queries) is not tuple:
        raise TypeError("C0 frontier batch requires an exact query tuple")
    for query in queries:
        _require_query(query)
    if tuple(query.row_id for query in queries) != tuple(range(len(queries))):
        raise ValueError("C0 frontier row ids must be dense input order")
    if not queries:
        return ()
    if not frontier.areas:
        return tuple(
            C0FrontierResult(row_id=query.row_id, all_impossible=False) for query in queries
        )

    areas = np.array(frontier.areas, dtype=np.float64, copy=True)[None, :]
    widths = np.array(frontier.widths, dtype=np.float64, copy=True)[None, :]
    heights = np.array(frontier.heights, dtype=np.float64, copy=True)[None, :]
    material_matches = np.array(
        tuple(query.material_matches for query in queries),
        dtype=np.bool_,
        copy=True,
    )[:, None]
    remnant_areas = np.array(
        tuple(query.remnant_area for query in queries),
        dtype=np.float64,
        copy=True,
    )[:, None]
    remnant_widths = np.array(
        tuple(query.remnant_width for query in queries),
        dtype=np.float64,
        copy=True,
    )[:, None]
    remnant_heights = np.array(
        tuple(query.remnant_height for query in queries),
        dtype=np.float64,
        copy=True,
    )[:, None]
    area_tolerances = np.array(
        tuple(query.area_tolerance for query in queries),
        dtype=np.float64,
        copy=True,
    )[:, None]
    coordinate_tolerances = np.array(
        tuple(query.coordinate_tolerance for query in queries),
        dtype=np.float64,
        copy=True,
    )[:, None]

    with np.errstate(over="ignore"):
        impossible = (
            ~material_matches
            | np.greater(areas, remnant_areas + area_tolerances)
            | np.greater(widths, remnant_widths + coordinate_tolerances)
            | np.greater(heights, remnant_heights + coordinate_tolerances)
        )
        all_impossible = np.all(impossible, axis=1)

    return tuple(
        C0FrontierResult(
            row_id=query.row_id,
            all_impossible=bool(result),
        )
        for query, result in zip(queries, all_impossible, strict=True)
    )


__all__ = [
    "C0FrontierColumns",
    "C0FrontierQuery",
    "C0FrontierResult",
    "certify_frontier_impossible_batch",
]
