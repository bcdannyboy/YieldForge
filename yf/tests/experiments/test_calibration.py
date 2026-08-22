from __future__ import annotations

from pathlib import Path

import pytest

from yieldforge.experiments.calibration import (
    CalibrationCandidateObservation,
    CalibrationCellEvidence,
    evaluate_calibration,
    nearest_rank_percentile,
    registered_cells,
)
from yieldforge.experiments.contracts import (
    PureGeometryCalibrationProtocol,
    load_frozen_json,
)

YF_ROOT = Path(__file__).parents[2]
GEOMETRY_PROTOCOL_PATH = YF_ROOT / "experiments" / "pure-geometry-calibration-v1.json"


def _protocol() -> PureGeometryCalibrationProtocol:
    return load_frozen_json(GEOMETRY_PROTOCOL_PATH, PureGeometryCalibrationProtocol)


def test_registered_cells_are_exactly_the_frozen_calibration_population() -> None:
    protocol = _protocol()

    cells = registered_cells(protocol)

    assert len(cells) == 612
    assert {cell.tasks_index for cell in cells} == set(protocol.split.calibration_task_ids)
    assert not {cell.tasks_index for cell in cells} & set(protocol.split.evaluation_task_ids)
    assert {cell.seed for cell in cells} == {0, 1, 2, 3}
    assert {cell.seconds_per_seed for cell in cells} == {1, 3, 10}
    assert {cell.projection_mode for cell in cells} == {"source_as_recorded"}
    assert len({cell.cell_id for cell in cells}) == 612
    assert [(cell.tasks_index, cell.seconds_per_seed, cell.seed) for cell in cells[:12]] == [
        (protocol.split.calibration_task_ids[0], seconds, seed)
        for seconds in (1, 3, 10)
        for seed in (0, 1, 2, 3)
    ]


def test_nearest_rank_percentile_uses_the_registered_order_statistic() -> None:
    values = tuple(float(index) for index in range(1, 52))

    assert nearest_rank_percentile(values, 0.95) == 49.0


def _complete_evidence(
    *,
    missing_candidates: set[tuple[int, int]] | None = None,
    invalid_archives: set[tuple[int, int, int]] | None = None,
    widths: dict[tuple[int, int], float] | None = None,
) -> tuple[CalibrationCellEvidence, ...]:
    missing = missing_candidates or set()
    invalid = invalid_archives or set()
    selected_widths = widths or {}
    evidence = []
    for cell in registered_cells(_protocol()):
        archive_valid = (cell.tasks_index, cell.seconds_per_seed, cell.seed) not in invalid
        candidates: tuple[CalibrationCandidateObservation, ...] = ()
        if archive_valid and (cell.tasks_index, cell.seconds_per_seed) not in missing:
            best = selected_widths.get((cell.tasks_index, cell.seconds_per_seed), 100.0)
            if cell.seed in {0, 1}:
                candidates = (
                    CalibrationCandidateObservation(
                        candidate_id=f"cand_{cell.tasks_index}_{cell.seconds_per_seed}_{cell.seed}",
                        width=best if cell.seed == 0 else best * 1.004,
                        density=0.5,
                    ),
                )
        evidence.append(
            CalibrationCellEvidence(
                cell=cell,
                archive_valid=archive_valid,
                candidates=candidates,
            )
        )
    return tuple(evidence)


def test_selector_chooses_smallest_budget_meeting_every_frozen_limit() -> None:
    evaluation = evaluate_calibration(_protocol(), _complete_evidence())

    assert evaluation.valid is True
    assert evaluation.selected_seconds_per_seed == 1
    one_second = evaluation.comparisons[0]
    assert one_second.seconds_per_seed == 1
    assert one_second.qualifying_rate_gap_percentage_points == 0.0
    assert one_second.median_best_length_degradation_percent == 0.0
    assert one_second.p95_best_length_degradation_percent == 0.0
    assert one_second.valid_archive_rate_percent == 100.0
    assert one_second.passes is True


def test_one_missing_shorter_task_counts_as_infinite_without_leaving_denominator() -> None:
    task_id = _protocol().split.calibration_task_ids[0]

    evaluation = evaluate_calibration(
        _protocol(),
        _complete_evidence(missing_candidates={(task_id, 1)}),
    )

    one_second = evaluation.comparisons[0]
    assert one_second.missing_shorter_best_task_ids == (task_id,)
    assert one_second.qualifying_rate_gap_percentage_points == pytest.approx(100 / 51)
    assert one_second.median_best_length_degradation_percent == 0.0
    assert one_second.p95_best_length_degradation_percent == 0.0


def test_missing_ten_second_reference_invalidates_calibration() -> None:
    task_id = _protocol().split.calibration_task_ids[0]

    evaluation = evaluate_calibration(
        _protocol(),
        _complete_evidence(missing_candidates={(task_id, 10)}),
    )

    assert evaluation.valid is False
    assert evaluation.selected_seconds_per_seed is None
    assert evaluation.missing_reference_task_ids == (task_id,)


def test_selector_falls_back_to_ten_seconds_when_shorter_degradation_is_too_large() -> None:
    protocol = _protocol()
    widths = {
        (tasks_index, seconds): (101.0 if seconds in {1, 3} else 100.0)
        for tasks_index in protocol.split.calibration_task_ids
        for seconds in (1, 3, 10)
    }

    evaluation = evaluate_calibration(protocol, _complete_evidence(widths=widths))

    assert evaluation.valid is True
    assert evaluation.selected_seconds_per_seed == 10
    assert all(comparison.passes is False for comparison in evaluation.comparisons)
    assert all(
        comparison.median_best_length_degradation_percent == 1.0
        for comparison in evaluation.comparisons
    )


def test_archive_validity_uses_all_204_registered_cells_per_duration() -> None:
    protocol = _protocol()
    invalid = {
        (cell.tasks_index, 1, cell.seed)
        for cell in registered_cells(protocol)
        if cell.seconds_per_seed == 1
    }
    invalid = set(sorted(invalid)[:11])

    evaluation = evaluate_calibration(
        protocol,
        _complete_evidence(invalid_archives=invalid),
    )

    one_second = evaluation.comparisons[0]
    assert one_second.valid_archive_count == 193
    assert one_second.registered_cell_count == 204
    assert one_second.valid_archive_rate_percent == 193 / 204 * 100
    assert one_second.passes is False
