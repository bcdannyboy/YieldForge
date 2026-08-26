from __future__ import annotations

import pytest
from shapely import Polygon, box

from tests.oracle.fixtures import two_problem_runtime
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    select_m7_fallback,
)
from yieldforge.oracle.convex_translation import (
    certify_convex_hull_translation_impossible,
    classify_convex_survivor_coverage,
)


def _fallback_cursor(runtime):  # type: ignore[no-untyped-def]
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=False)
    selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(
        item for item in catalog.actions if item.action_id == selection.action_id
    )
    return apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selection.decision_key,
    ).cursor


def test_convex_certificate_accepts_a_feasible_translation() -> None:
    certificate = certify_convex_hull_translation_impossible(
        box(5.0, 7.0, 7.0, 9.0),
        box(0.0, 0.0, 10.0, 10.0),
        coordinate_tolerance=1e-9,
    )

    assert not certificate.impossible
    assert certificate.feasible_translation is not None


def test_convex_certificate_rejects_an_axis_scalar_survivor() -> None:
    lower_left = Polygon(((0.0, 0.0), (10.0, 0.0), (0.0, 10.0)))
    upper_right = Polygon(((0.0, 10.0), (10.0, 10.0), (10.0, 0.0)))

    certificate = certify_convex_hull_translation_impossible(
        upper_right,
        lower_left,
        coordinate_tolerance=1e-9,
    )

    assert certificate.impossible
    assert certificate.feasible_translation is None
    assert certificate.layout_bounds == certificate.remnant_bounds
    assert certificate.layout_area == certificate.remnant_area


def test_convex_certificate_uses_hulls_as_a_conservative_relaxation() -> None:
    nonconvex_remnant = Polygon(
        ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (2.0, 2.0), (2.0, 10.0), (0.0, 10.0))
    )
    candidate = box(0.0, 0.0, 2.0, 2.0)

    certificate = certify_convex_hull_translation_impossible(
        candidate,
        nonconvex_remnant,
        coordinate_tolerance=1e-9,
    )

    assert not certificate.impossible


def test_convex_certificate_fails_closed_within_tolerance() -> None:
    certificate = certify_convex_hull_translation_impossible(
        box(0.0, 0.0, 10.0005, 10.0),
        box(0.0, 0.0, 10.0, 10.0),
        coordinate_tolerance=0.001,
    )

    assert not certificate.impossible


@pytest.mark.parametrize(
    ("layout", "remnant", "tolerance", "message"),
    (
        (Polygon(), box(0.0, 0.0, 1.0, 1.0), 1e-9, "positive-area polygon"),
        (box(0.0, 0.0, 1.0, 1.0), Polygon(), 1e-9, "positive-area polygon"),
        (box(0.0, 0.0, 1.0, 1.0), box(0.0, 0.0, 1.0, 1.0), -1.0, "tolerance"),
    ),
)
def test_convex_certificate_rejects_invalid_inputs(
    layout: Polygon,
    remnant: Polygon,
    tolerance: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        certify_convex_hull_translation_impossible(
            layout,
            remnant,
            coordinate_tolerance=tolerance,
        )


def test_runtime_coverage_reports_an_unresolved_convex_survivor() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)

    coverage = classify_convex_survivor_coverage(
        runtime,
        cursor=_fallback_cursor(runtime),
    )

    assert len(coverage.items) == 1
    assert coverage.items[0].unresolved_candidate_ids
    assert not coverage.complete_no_fit


def test_runtime_coverage_reports_complete_scalar_rejection() -> None:
    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)

    coverage = classify_convex_survivor_coverage(
        runtime,
        cursor=_fallback_cursor(runtime),
    )

    assert len(coverage.items) == 1
    assert coverage.items[0].scalar_rejected_candidate_ids
    assert not coverage.items[0].unresolved_candidate_ids
    assert coverage.complete_no_fit
