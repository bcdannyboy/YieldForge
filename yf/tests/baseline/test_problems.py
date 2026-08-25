from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.baseline.problems import (
    build_registered_calibration_problem_view,
    build_registered_problem_index,
)
from yieldforge.temporal_benchmark.contracts import TemporalPartition, TemporalRegime


@pytest.fixture(scope="module")
def index():  # type: ignore[no-untyped-def]
    return build_registered_problem_index()


def test_registered_index_decomposes_every_event_and_freezes_corrected_census(index) -> None:  # type: ignore[no-untyped-def]
    assert index.m6_batch_count == 1024
    assert index.instance_count == 1152
    assert index.problem_count == 209
    assert index.calibration_instance_count == 288
    assert index.calibration_problem_count == 90
    assert index.evaluation_instance_count == 864
    assert index.evaluation_problem_count == 198
    assert index.shared_problem_count == 79
    assert len(index.instances) == 1152
    assert len(index.problems) == 209
    assert index.index_id == f"yfm7i-{index.content_sha256[7:31]}"


def test_repeated_source_task_reuses_problem_without_erasing_temporal_identity(index) -> None:  # type: ignore[no-untyped-def]
    exact = tuple(
        item
        for item in index.instances
        if item.partition is TemporalPartition.CALIBRATION
        and item.regime is TemporalRegime.EXACT_RECURRENCE
        and item.temporal_seed == 2026082300
    )

    assert len(exact) == 24
    assert len({item.problem_id for item in exact}) == 1
    assert len({item.binding_id for item in exact}) == 24
    assert len({item.event_id for item in exact}) == 24
    assert tuple(item.sequence for item in exact) == tuple(range(24))


def test_impossible_three_event_groups_decompose_at_source_event_boundaries(index) -> None:  # type: ignore[no-untyped-def]
    compatible = tuple(
        item
        for item in index.instances
        if item.partition is TemporalPartition.CALIBRATION
        and item.regime is TemporalRegime.COMPATIBLE_BUNDLE
        and item.temporal_seed == 2026082300
    )

    assert len(compatible) == 24
    assert len({item.m6_batch_id for item in compatible}) == 8
    for offset in range(0, len(compatible), 3):
        group = compatible[offset : offset + 3]
        assert len({item.m6_batch_id for item in group}) == 1
        assert tuple(item.m6_subsequence for item in group) == (0, 1, 2)
        assert len({item.problem_id for item in group}) == 3


def test_problem_is_exact_catalog_projection_and_excludes_temporal_fields(index) -> None:  # type: ignore[no-untyped-def]
    problem = index.problems[0]
    dumped = problem.model_dump(mode="json")

    assert problem.problem.name == f"lectra-task-{problem.tasks_index}"
    assert problem.projection.mode.value == "source_as_recorded"
    assert problem.candidate_requirement.seeds == (0, 1, 2, 3)
    assert problem.candidate_requirement.seconds_per_seed == 10
    assert not ({"stream_id", "event_id", "released_at", "material"} & set(dumped))


def test_problem_and_binding_content_identities_fail_closed(index) -> None:  # type: ignore[no-untyped-def]
    problem = index.problems[0]
    binding = index.instances[0]

    with pytest.raises(ValidationError, match="problem ID"):
        type(problem).model_validate({**problem.model_dump(), "problem_id": "yfm7p-" + "0" * 24})
    with pytest.raises(ValidationError, match="binding ID"):
        type(binding).model_validate({**binding.model_dump(), "binding_id": "yfm7b-" + "0" * 24})


def test_registered_problem_index_is_byte_stable(index, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    second = build_registered_problem_index()

    assert second == index
    assert second.model_dump_json() == index.model_dump_json()


def test_calibration_view_binds_full_index_without_opening_evaluation_streams(
    index,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline import problems

    population = problems.TemporalPopulationManifest.model_validate_json(
        problems._POPULATION_PATH.read_bytes(), strict=True  # noqa: SLF001
    )
    evaluation_names = {
        item.filename
        for item in population.streams
        if item.partition is TemporalPartition.EVALUATION
    }
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path.name in evaluation_names:
            raise AssertionError(f"evaluation stream opened: {path.name}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    view = build_registered_calibration_problem_view(
        full_problem_index_id=index.index_id,
        full_problem_index_sha256=index.content_sha256,
    )

    assert view.full_problem_index_id == index.index_id
    assert view.full_problem_index_sha256 == index.content_sha256
    assert view.m6_contract_id == index.m6_contract_id
    assert view.m6_population_id == index.m6_population_id
    assert view.source_catalog_sha256 == index.source_catalog_sha256
    assert view.evaluation_partition_opened is False
    assert len(view.instances) == 288
    assert len(view.problems) == 90
    assert all(
        item.partition is TemporalPartition.CALIBRATION for item in view.instances
    )
    assert view.instances == tuple(
        item
        for item in index.instances
        if item.partition is TemporalPartition.CALIBRATION
    )
    assert view.problems == tuple(
        item
        for item in index.problems
        if item.problem_id in {binding.problem_id for binding in view.instances}
    )
