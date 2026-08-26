from __future__ import annotations

import random

import pytest


def _scalar(
    candidate_id: str,
    area: float,
    width: float,
    height: float,
    *,
    material_partition: str = "temporal_event:a",
    problem_id: str = "problem-a",
    candidate_set_id: str = "candidate-set-a",
):  # type: ignore[no-untyped-def]
    from yieldforge.oracle.frontier import RejectionScalar

    return RejectionScalar(
        problem_id=problem_id,
        problem_sha256="sha256:" + "b" * 64,
        candidate_set_id=candidate_set_id,
        candidate_set_sha256="sha256:" + "c" * 64,
        candidate_id=candidate_id,
        source_transform_sha256="sha256:" + "d" * 64,
        material_partition=material_partition,
        fit_config_sha256="sha256:" + "a" * 64,
        area=area,
        width=width,
        height=height,
    )


def test_frontier_retains_incomparable_entries_and_canonical_duplicate() -> None:
    from yieldforge.oracle.frontier import build_pareto_frontier

    frontier = build_pareto_frontier(
        (
            _scalar("candidate-d", 0.5, 4.0, 1.0),
            _scalar("candidate-b", 1.0, 2.0, 3.0),
            _scalar("candidate-c", 2.0, 2.0, 4.0),
            _scalar("candidate-a", 1.0, 2.0, 3.0),
        )
    )

    assert tuple(item.candidate_id for item in frontier.retained) == (
        "candidate-a",
        "candidate-d",
    )
    assert {
        (edge.dominated_candidate_id, edge.retained_candidate_id)
        for edge in frontier.dominated_by
    } == {
        ("candidate-b", "candidate-a"),
        ("candidate-c", "candidate-a"),
    }


def test_frontier_never_compares_different_material_or_problem_partitions() -> None:
    from yieldforge.oracle.frontier import build_pareto_frontier

    frontier = build_pareto_frontier(
        (
            _scalar("candidate-a", 1.0, 1.0, 1.0),
            _scalar(
                "candidate-b",
                2.0,
                2.0,
                2.0,
                material_partition="temporal_event:b",
            ),
            _scalar(
                "candidate-c",
                3.0,
                3.0,
                3.0,
                problem_id="problem-b",
                candidate_set_id="candidate-set-b",
            ),
        )
    )

    assert tuple(item.candidate_id for item in frontier.retained) == (
        "candidate-a",
        "candidate-b",
        "candidate-c",
    )
    assert frontier.dominated_by == ()


def test_frontier_is_order_independent_and_membership_complete() -> None:
    from yieldforge.oracle.frontier import build_pareto_frontier

    values = (
        _scalar("candidate-c", 3.0, 1.0, 3.0),
        _scalar("candidate-a", 1.0, 3.0, 1.0),
        _scalar("candidate-b", 2.0, 2.0, 2.0),
        _scalar("candidate-d", 4.0, 4.0, 4.0),
    )

    expected = build_pareto_frontier(values)
    assert build_pareto_frontier(tuple(reversed(values))) == expected
    assert tuple(item.candidate_id for item in expected.members) == (
        "candidate-a",
        "candidate-b",
        "candidate-c",
        "candidate-d",
    )
    classified = {item.candidate_id for item in expected.retained} | {
        edge.dominated_candidate_id for edge in expected.dominated_by
    }
    assert classified == {item.candidate_id for item in expected.members}


def test_frontier_accepts_empty_input_but_fails_closed_for_rejection() -> None:
    from yieldforge.oracle.frontier import (
        build_pareto_frontier,
        certify_frontier_impossible,
    )

    frontier = build_pareto_frontier(())

    assert frontier.members == ()
    assert frontier.retained == ()
    assert frontier.dominated_by == ()
    assert not certify_frontier_impossible(
        frontier,
        material_matches=True,
        remnant_area=1.0,
        remnant_width=1.0,
        remnant_height=1.0,
        area_tolerance=0.0,
        coordinate_tolerance=0.0,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("area", 0.0),
        ("width", -1.0),
        ("height", float("inf")),
        ("candidate_id", ""),
    ),
)
def test_frontier_rejects_invalid_scalar(field: str, value: object) -> None:
    from yieldforge.oracle.frontier import RejectionScalar

    values = {
        "problem_id": "problem-a",
        "problem_sha256": "sha256:" + "b" * 64,
        "candidate_set_id": "candidate-set-a",
        "candidate_set_sha256": "sha256:" + "c" * 64,
        "candidate_id": "candidate-a",
        "source_transform_sha256": "sha256:" + "d" * 64,
        "material_partition": "temporal_event",
        "fit_config_sha256": "sha256:" + "a" * 64,
        "area": 1.0,
        "width": 1.0,
        "height": 1.0,
    }
    values[field] = value

    with pytest.raises(ValueError):
        RejectionScalar(**values)  # type: ignore[arg-type]


def test_frontier_rejects_duplicate_candidate_identity() -> None:
    from yieldforge.oracle.frontier import build_pareto_frontier

    with pytest.raises(ValueError, match="candidate identities must be unique"):
        build_pareto_frontier(
            (
                _scalar("candidate-a", 1.0, 1.0, 1.0),
                _scalar("candidate-a", 2.0, 2.0, 2.0),
            )
        )


def test_frontier_rejection_matches_full_set_on_fixed_random_matrix() -> None:
    from yieldforge.oracle.frontier import (
        build_pareto_frontier,
        certify_frontier_impossible,
        certify_scalar_set_impossible,
    )

    randomizer = random.Random(20260825)
    scalars = tuple(
        _scalar(
            f"candidate-{index:03d}",
            randomizer.uniform(0.5, 25.0),
            randomizer.uniform(0.5, 10.0),
            randomizer.uniform(0.5, 10.0),
        )
        for index in range(80)
    )
    frontier = build_pareto_frontier(scalars)

    for material_matches in (False, True):
        for _case in range(100):
            arguments = {
                "material_matches": material_matches,
                "remnant_area": randomizer.uniform(0.5, 30.0),
                "remnant_width": randomizer.uniform(0.5, 12.0),
                "remnant_height": randomizer.uniform(0.5, 12.0),
                "area_tolerance": 0.01,
                "coordinate_tolerance": 0.001,
            }
            assert certify_frontier_impossible(frontier, **arguments) == (
                certify_scalar_set_impossible(scalars, **arguments)
            )


def test_compiled_rejection_problem_uses_retained_verified_scalars() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle.compiled import compile_rejection_problem

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)

    compiled = compile_rejection_problem(runtime, event_position=0)

    verified = runtime.runtime_candidates[compiled.problem_id]
    assert compiled.candidate_set_id == verified.evidence.candidate_set_id
    assert tuple(item.candidate_id for item in compiled.frontier.members) == tuple(
        item.candidate_id for item in verified.candidates
    )
    assert 0 < len(compiled.frontier.retained) <= len(compiled.frontier.members)


def test_prepared_batch_compiles_each_repeated_problem_frontier_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        event_count=3,
    )
    original = compiled.build_pareto_frontier
    call_count = 0

    def counted(scalars):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original(scalars)

    monkeypatch.setattr(compiled, "build_pareto_frontier", counted)

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1, 2),
    ) as prepared:
        first = compiled._prepared_rejection_problem(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
        )
        second = compiled._prepared_rejection_problem(  # noqa: SLF001
            prepared,
            runtime,
            event_position=2,
        )

    assert first is second
    assert call_count == 1
