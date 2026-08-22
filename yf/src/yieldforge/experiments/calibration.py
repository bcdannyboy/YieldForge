"""Pure contracts and selection rules for the registered M2 calibration."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    PureGeometryCalibrationProtocol,
)


class CalibrationCell(FrozenExperimentModel):
    """One pre-registered task, duration, and seed combination."""

    parent_protocol_id: StrictStr
    tasks_index: StrictInt = Field(ge=0)
    projection_mode: Literal["source_as_recorded"] = "source_as_recorded"
    seconds_per_seed: Literal[1, 3, 10]
    seed: Literal[0, 1, 2, 3]

    @property
    def cell_id(self) -> str:
        return (
            f"{self.parent_protocol_id}--task-{self.tasks_index}"
            f"--seconds-{self.seconds_per_seed}--seed-{self.seed}"
        )


class CalibrationCandidateObservation(FrozenExperimentModel):
    """The API-visible fields required by the calibration selector."""

    candidate_id: StrictStr = Field(min_length=1)
    width: StrictFloat = Field(gt=0)
    density: StrictFloat = Field(ge=0, le=1)


class CalibrationCellEvidence(FrozenExperimentModel):
    """Selected terminal evidence for one registered cell."""

    cell: CalibrationCell
    archive_valid: StrictBool
    candidates: tuple[CalibrationCandidateObservation, ...]

    @model_validator(mode="after")
    def require_archive_consistent_candidates(self) -> Self:
        if not self.archive_valid and self.candidates:
            raise ValueError("an invalid archive cannot contribute candidates")
        identifiers = [candidate.candidate_id for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("one cell cannot repeat a candidate ID")
        return self


class CalibrationDurationSummary(FrozenExperimentModel):
    """Registered-population summary for one solver duration."""

    seconds_per_seed: Literal[1, 3, 10]
    registered_cell_count: StrictInt = Field(ge=0)
    valid_archive_count: StrictInt = Field(ge=0)
    valid_archive_rate_percent: StrictFloat = Field(ge=0, le=100)
    qualifying_task_count: StrictInt = Field(ge=0)
    registered_task_count: StrictInt = Field(ge=0)
    qualifying_task_rate_percent: StrictFloat = Field(ge=0, le=100)


class CalibrationComparison(FrozenExperimentModel):
    """One shorter duration compared with the 10-second reference."""

    seconds_per_seed: Literal[1, 3]
    qualifying_rate_gap_percentage_points: StrictFloat = Field(ge=0)
    median_best_length_degradation_percent: StrictFloat | None = Field(default=None, ge=0)
    p95_best_length_degradation_percent: StrictFloat | None = Field(default=None, ge=0)
    missing_shorter_best_task_ids: tuple[StrictInt, ...]
    registered_cell_count: StrictInt = Field(ge=0)
    valid_archive_count: StrictInt = Field(ge=0)
    valid_archive_rate_percent: StrictFloat = Field(ge=0, le=100)
    passes: StrictBool


class CalibrationEvaluation(FrozenExperimentModel):
    """Pure outcome of applying the frozen budget selector."""

    valid: StrictBool
    selected_seconds_per_seed: Literal[1, 3, 10] | None
    missing_reference_task_ids: tuple[StrictInt, ...]
    duration_summaries: tuple[CalibrationDurationSummary, ...]
    comparisons: tuple[CalibrationComparison, CalibrationComparison]

    @model_validator(mode="after")
    def require_selection_consistent_with_validity(self) -> Self:
        if self.valid is not (self.selected_seconds_per_seed is not None):
            raise ValueError("valid calibration must have exactly one selected duration")
        return self


def registered_cells(
    protocol: PureGeometryCalibrationProtocol,
) -> tuple[CalibrationCell, ...]:
    """Enumerate the exact calibration cells in their frozen execution order."""

    if protocol.confirmation_enabled or protocol.budget.selected_seconds_per_seed is not None:
        raise ValueError("calibration cells require a calibration-pending protocol")
    return tuple(
        CalibrationCell(
            parent_protocol_id=protocol.protocol_id,
            tasks_index=tasks_index,
            seconds_per_seed=seconds,
            seed=seed,
        )
        for tasks_index in protocol.split.calibration_task_ids
        for seconds in protocol.budget.calibration_seconds_per_seed
        for seed in protocol.budget.ordinary_seeds
    )


def nearest_rank_percentile(values: tuple[float, ...], probability: float) -> float:
    """Return the registered nearest-rank order statistic."""

    if not values:
        raise ValueError("nearest-rank percentile requires at least one value")
    if not 0 < probability <= 1:
        raise ValueError("nearest-rank probability must be in (0, 1]")
    ordered = sorted(values)
    rank = math.ceil(probability * len(ordered))
    return ordered[rank - 1]


class _DurationEvaluation:
    def __init__(
        self,
        summary: CalibrationDurationSummary,
        best_by_task: dict[int, float],
    ) -> None:
        self.summary = summary
        self.best_by_task = best_by_task


def _evaluate_duration(
    *,
    seconds: int,
    task_ids: tuple[int, ...],
    evidence: tuple[CalibrationCellEvidence, ...],
    envelope_percent: float,
) -> _DurationEvaluation:
    selected = tuple(item for item in evidence if item.cell.seconds_per_seed == seconds)
    by_task: dict[int, dict[str, CalibrationCandidateObservation]] = defaultdict(dict)
    for item in selected:
        for candidate in item.candidates:
            existing = by_task[item.cell.tasks_index].get(candidate.candidate_id)
            if existing is not None and existing != candidate:
                raise ValueError("one candidate ID has conflicting API observations")
            by_task[item.cell.tasks_index][candidate.candidate_id] = candidate

    best_by_task: dict[int, float] = {}
    qualifying = 0
    envelope_factor = 1 + envelope_percent / 100
    for tasks_index in task_ids:
        candidates = tuple(by_task[tasks_index].values())
        if not candidates:
            continue
        best = min(candidate.width for candidate in candidates)
        best_by_task[tasks_index] = best
        near_tied = sum(candidate.width <= best * envelope_factor for candidate in candidates)
        if near_tied >= 2:
            qualifying += 1

    valid_archives = sum(item.archive_valid for item in selected)
    summary = CalibrationDurationSummary(
        seconds_per_seed=seconds,
        registered_cell_count=len(selected),
        valid_archive_count=valid_archives,
        valid_archive_rate_percent=valid_archives / len(selected) * 100,
        qualifying_task_count=qualifying,
        registered_task_count=len(task_ids),
        qualifying_task_rate_percent=qualifying / len(task_ids) * 100,
    )
    return _DurationEvaluation(summary, best_by_task)


def evaluate_calibration(
    protocol: PureGeometryCalibrationProtocol,
    evidence: tuple[CalibrationCellEvidence, ...],
) -> CalibrationEvaluation:
    """Apply the approved conservative all-task budget selector."""

    expected = registered_cells(protocol)
    expected_by_id = {cell.cell_id: cell for cell in expected}
    observed_by_id = {item.cell.cell_id: item for item in evidence}
    if len(observed_by_id) != len(evidence):
        raise ValueError("calibration evidence repeats a registered cell")
    if set(observed_by_id) != set(expected_by_id):
        raise ValueError("calibration evidence does not cover the exact registered cells")
    if any(observed_by_id[cell_id].cell != cell for cell_id, cell in expected_by_id.items()):
        raise ValueError("calibration evidence changes a registered cell")

    task_ids = protocol.split.calibration_task_ids
    evaluations = {
        seconds: _evaluate_duration(
            seconds=seconds,
            task_ids=task_ids,
            evidence=evidence,
            envelope_percent=protocol.near_tie.primary_envelope_percent,
        )
        for seconds in protocol.budget.calibration_seconds_per_seed
    }
    reference = evaluations[protocol.budget.selector.reference_seconds_per_seed]
    missing_reference = tuple(
        tasks_index for tasks_index in task_ids if tasks_index not in reference.best_by_task
    )
    reference_valid = (
        not missing_reference
        and reference.summary.valid_archive_rate_percent
        >= protocol.budget.selector.minimum_valid_archive_rate_percent
    )

    comparisons = []
    for seconds in (1, 3):
        shorter = evaluations[seconds]
        missing_shorter = tuple(
            tasks_index for tasks_index in task_ids if tasks_index not in shorter.best_by_task
        )
        degradations = []
        for tasks_index in task_ids:
            reference_best = reference.best_by_task.get(tasks_index)
            shorter_best = shorter.best_by_task.get(tasks_index)
            if reference_best is None or shorter_best is None:
                degradations.append(math.inf)
            else:
                degradations.append(
                    max(0.0, (shorter_best - reference_best) / reference_best * 100)
                )
        median = statistics.median(degradations)
        p95 = nearest_rank_percentile(tuple(degradations), 0.95)
        finite_median = median if math.isfinite(median) else None
        finite_p95 = p95 if math.isfinite(p95) else None
        rate_gap = abs(
            shorter.summary.qualifying_task_rate_percent
            - reference.summary.qualifying_task_rate_percent
        )
        passes = (
            reference_valid
            and rate_gap <= protocol.budget.selector.maximum_qualifying_rate_gap_percentage_points
            and finite_median is not None
            and finite_median
            <= protocol.budget.selector.maximum_median_best_length_degradation_percent
            and finite_p95 is not None
            and finite_p95 <= protocol.budget.selector.maximum_p95_best_length_degradation_percent
            and shorter.summary.valid_archive_rate_percent
            >= protocol.budget.selector.minimum_valid_archive_rate_percent
        )
        comparisons.append(
            CalibrationComparison(
                seconds_per_seed=seconds,
                qualifying_rate_gap_percentage_points=rate_gap,
                median_best_length_degradation_percent=finite_median,
                p95_best_length_degradation_percent=finite_p95,
                missing_shorter_best_task_ids=missing_shorter,
                registered_cell_count=shorter.summary.registered_cell_count,
                valid_archive_count=shorter.summary.valid_archive_count,
                valid_archive_rate_percent=shorter.summary.valid_archive_rate_percent,
                passes=passes,
            )
        )

    selected = next(
        (comparison.seconds_per_seed for comparison in comparisons if comparison.passes),
        10 if reference_valid else None,
    )
    return CalibrationEvaluation(
        valid=selected is not None,
        selected_seconds_per_seed=selected,
        missing_reference_task_ids=missing_reference,
        duration_summaries=tuple(
            evaluations[seconds].summary for seconds in protocol.budget.calibration_seconds_per_seed
        ),
        comparisons=tuple(comparisons),
    )
