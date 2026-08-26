"""Conservative convex-hull translation impossibility certificates for M8."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from yieldforge.baseline.geometry import (
    certify_translation_impossible,
    prepare_layout_footprint,
)
from yieldforge.baseline.replay import M7ReplayCursor, M7ReplayRuntime
from yieldforge.reuse.contracts import polygon_from_record


@dataclass(frozen=True, slots=True)
class ConvexTranslationCertificate:
    """A no-fit proof only when even the convex relaxation is infeasible."""

    impossible: bool
    layout_area: float
    remnant_area: float
    layout_bounds: tuple[float, float, float, float]
    remnant_bounds: tuple[float, float, float, float]
    constraint_count: int
    feasible_translation: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class ConvexSurvivorItemCoverage:
    """Candidate-level proof coverage for one inventory remnant."""

    remnant_id: str
    scalar_rejected_candidate_ids: tuple[str, ...]
    convex_rejected_candidate_ids: tuple[str, ...]
    unresolved_candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConvexSurvivorCoverage:
    """Collision-free survivor coverage for one exact future event."""

    event_position: int
    problem_id: str
    candidate_set_id: str
    items: tuple[ConvexSurvivorItemCoverage, ...]

    @property
    def complete_no_fit(self) -> bool:
        return all(not item.unresolved_candidate_ids for item in self.items)


def _convex_vertices(
    geometry: BaseGeometry,
    *,
    label: str,
) -> tuple[tuple[float, float], ...]:
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0.0:
        raise ValueError(f"M8 convex {label} must be a valid positive-area polygon")
    hull = geometry.convex_hull
    if hull.geom_type != "Polygon" or hull.is_empty or hull.area <= 0.0:
        raise ValueError(f"M8 convex {label} must have a positive-area polygon hull")
    vertices = tuple((float(x), float(y)) for x, y in tuple(hull.exterior.coords)[:-1])
    signed_double_area = sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(vertices, vertices[1:] + vertices[:1], strict=True)
    )
    if signed_double_area == 0.0:
        raise ValueError(f"M8 convex {label} hull is degenerate")
    return vertices if signed_double_area > 0.0 else tuple(reversed(vertices))


def _translation_constraints(
    layout_vertices: tuple[tuple[float, float], ...],
    remnant_vertices: tuple[tuple[float, float], ...],
    *,
    coordinate_tolerance: float,
) -> tuple[tuple[float, float, float], ...]:
    constraints = []
    for start, stop in zip(
        remnant_vertices,
        remnant_vertices[1:] + remnant_vertices[:1],
        strict=True,
    ):
        delta_x = stop[0] - start[0]
        delta_y = stop[1] - start[1]
        length = math.hypot(delta_x, delta_y)
        if length <= 0.0:
            raise ValueError("M8 convex remnant hull contains a degenerate edge")
        outward_x = delta_y / length
        outward_y = -delta_x / length
        layout_support = max(
            outward_x * x + outward_y * y for x, y in layout_vertices
        )
        bound = (
            outward_x * start[0]
            + outward_y * start[1]
            + coordinate_tolerance
            - layout_support
        )
        constraints.append((outward_x, outward_y, bound))
    return tuple(constraints)


def _satisfies(
    point: tuple[float, float],
    constraints: tuple[tuple[float, float, float], ...],
) -> bool:
    x, y = point
    for normal_x, normal_y, bound in constraints:
        value = normal_x * x + normal_y * y
        scale = max(1.0, abs(value), abs(bound))
        numerical_slack = 64.0 * math.ulp(scale)
        if value > bound + numerical_slack:
            return False
    return True


def _feasible_translation(
    constraints: tuple[tuple[float, float, float], ...],
) -> tuple[float, float] | None:
    origin = (0.0, 0.0)
    if _satisfies(origin, constraints):
        return origin
    for left_index, left in enumerate(constraints):
        for right in constraints[left_index + 1 :]:
            determinant = left[0] * right[1] - right[0] * left[1]
            if abs(determinant) <= 64.0 * math.ulp(1.0):
                continue
            x = (left[2] * right[1] - right[2] * left[1]) / determinant
            y = (left[0] * right[2] - right[0] * left[2]) / determinant
            point = (0.0 if x == 0.0 else x, 0.0 if y == 0.0 else y)
            if _satisfies(point, constraints):
                return point
    return None


def certify_convex_hull_translation_impossible(
    layout: BaseGeometry,
    remnant: BaseGeometry,
    *,
    coordinate_tolerance: float,
) -> ConvexTranslationCertificate:
    """Reject only when no translation fits the layout hull inside the remnant hull.

    Convex hulls relax the original geometry.  Therefore infeasibility is a
    valid no-fit proof, while feasibility remains deliberately inconclusive.
    """

    if not math.isfinite(coordinate_tolerance) or coordinate_tolerance < 0.0:
        raise ValueError("M8 convex translation tolerance must be finite and nonnegative")
    layout_vertices = _convex_vertices(layout, label="layout")
    remnant_vertices = _convex_vertices(remnant, label="remnant")
    constraints = _translation_constraints(
        layout_vertices,
        remnant_vertices,
        coordinate_tolerance=coordinate_tolerance,
    )
    feasible = _feasible_translation(constraints)
    layout_hull = layout.convex_hull
    remnant_hull = remnant.convex_hull
    return ConvexTranslationCertificate(
        impossible=feasible is None,
        layout_area=float(layout_hull.area),
        remnant_area=float(remnant_hull.area),
        layout_bounds=tuple(float(value) for value in layout_hull.bounds),
        remnant_bounds=tuple(float(value) for value in remnant_hull.bounds),
        constraint_count=len(constraints),
        feasible_translation=feasible,
    )


def classify_convex_survivor_coverage(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
) -> ConvexSurvivorCoverage:
    """Classify scalar and convex no-fit coverage without collision search."""

    event_position = cursor.next_event_position
    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 convex coverage event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    problem = next(
        item
        for item in runtime.replay_input.problems
        if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    layouts = tuple(
        prepare_layout_footprint(
            problem.problem,
            candidate,
            runtime.replay_input.fit_config,
        )
        for candidate in verified.candidates
    )
    item_coverages = []
    for item in cursor.inventory:
        remnant_geometry = polygon_from_record(item.remnant.geometry)
        scalar_rejected = []
        convex_rejected = []
        unresolved = []
        for candidate, layout in zip(verified.candidates, layouts, strict=True):
            scalar = certify_translation_impossible(
                layout,
                item.remnant,
                material=binding.material,
                fit_config=runtime.replay_input.fit_config,
            )
            if scalar.impossible:
                scalar_rejected.append(candidate.candidate_id)
                continue
            convex = certify_convex_hull_translation_impossible(
                layout.geometry,
                remnant_geometry,
                coordinate_tolerance=runtime.replay_input.fit_config.coordinate_tolerance,
            )
            if convex.impossible:
                convex_rejected.append(candidate.candidate_id)
            else:
                unresolved.append(candidate.candidate_id)
        item_coverages.append(
            ConvexSurvivorItemCoverage(
                remnant_id=item.remnant.remnant_id,
                scalar_rejected_candidate_ids=tuple(scalar_rejected),
                convex_rejected_candidate_ids=tuple(convex_rejected),
                unresolved_candidate_ids=tuple(unresolved),
            )
        )
    return ConvexSurvivorCoverage(
        event_position=event_position,
        problem_id=problem.problem_id,
        candidate_set_id=verified.evidence.candidate_set_id,
        items=tuple(item_coverages),
    )


__all__ = [
    "ConvexTranslationCertificate",
    "ConvexSurvivorCoverage",
    "ConvexSurvivorItemCoverage",
    "certify_convex_hull_translation_impossible",
    "classify_convex_survivor_coverage",
]
