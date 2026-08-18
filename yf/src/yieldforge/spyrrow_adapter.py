"""Adapter from YieldForge contracts to Spyrrow's native Python API."""

import hashlib
import importlib.metadata
import json
from collections.abc import Callable
from threading import Thread
from typing import Any

from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    CandidateReportType,
    Placement,
    SolverIdentity,
    SpyrrowRunConfig,
    SpyrrowRunResult,
    StripPackingProblem,
)

# Revision pinned by the source distribution used to build spyrrow 0.9.0.
SPARROW_REVISION = "881cdcbdf492ca42ba5413954ea6e41889a3becd"


def _candidate_id(width: float, placements: list[Placement]) -> str:
    normalized = {
        "width": width,
        "placements": sorted(
            (
                placement.part_id,
                placement.rotation,
                placement.translation[0],
                placement.translation[1],
            )
            for placement in placements
        ),
    }
    payload = json.dumps(normalized, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return f"cand_{hashlib.sha256(payload.encode()).hexdigest()[:20]}"


class SpyrrowAdapter:
    """Generate normalized feasible candidates from a fixed-sheet problem."""

    def __init__(self, api: Any | None = None, spyrrow_version: str | None = None) -> None:
        if api is None:
            import spyrrow

            api = spyrrow
        self.api = api
        self.spyrrow_version = spyrrow_version or importlib.metadata.version("spyrrow")

    def generate(self, problem: StripPackingProblem, config: SpyrrowRunConfig) -> CandidateBatch:
        """Generate a candidate batch using the legacy adapter interface."""

        return self.run(problem, config).batch

    def run(
        self,
        problem: StripPackingProblem,
        config: SpyrrowRunConfig,
        on_candidate: Callable[[Candidate], None] | None = None,
    ) -> SpyrrowRunResult:
        """Run Spyrrow and publish each accepted candidate as it is reported."""

        items = [
            self.api.Item(
                part.id,
                part.shape,
                part.demand,
                part.allowed_orientations,
            )
            for part in problem.parts
        ]
        instance = self.api.StripPackingInstance(problem.name, problem.strip_height, items)
        solver_config = self.api.StripPackingConfig(
            early_termination=config.early_termination,
            min_items_separation=config.min_items_separation,
            total_computation_time=config.total_computation_time,
            num_workers=config.num_workers,
            seed=config.seed,
        )

        candidates: list[Candidate] = []
        seen: set[str] = set()
        final_candidate_id: str | None = None
        native_report_count = 0
        ignored_report_count = 0
        duplicate_candidate_count = 0
        sheet_overflow_count = 0
        observer_errors: list[BaseException] = []

        def process_report(report_type: Any, solution: Any, is_terminal: bool) -> None:
            nonlocal duplicate_candidate_count
            nonlocal final_candidate_id
            nonlocal ignored_report_count
            nonlocal native_report_count
            nonlocal sheet_overflow_count

            if is_terminal:
                final_candidate_id = None
            else:
                native_report_count += 1
            normalized_type = self._normalize_report_type(report_type)
            if normalized_type is None:
                ignored_report_count += 1
                return
            if solution.width > problem.sheet_length + 1e-9:
                sheet_overflow_count += 1
                return
            placements = [
                Placement(
                    part_id=placed.id,
                    rotation=placed.rotation,
                    translation=tuple(placed.translation),
                )
                for placed in solution.placed_items
            ]
            candidate_id = _candidate_id(solution.width, placements)
            if is_terminal:
                final_candidate_id = candidate_id
            if candidate_id in seen:
                duplicate_candidate_count += 1
                return
            seen.add(candidate_id)
            candidate = Candidate(
                candidate_id=candidate_id,
                report_type=normalized_type,
                seed=config.seed,
                width=solution.width,
                density=solution.density,
                placements=placements,
            )
            candidates.append(candidate)
            if on_candidate is not None and not observer_errors:
                observer_candidate = Candidate.model_validate(candidate.model_dump())
                try:
                    on_candidate(observer_candidate)
                except BaseException as error:
                    observer_errors.append(error)

        self._solve_with_progress(instance, solver_config, process_report)
        if observer_errors:
            raise observer_errors[0]

        return SpyrrowRunResult(
            batch=CandidateBatch(
                problem=problem,
                solver=SolverIdentity(
                    spyrrow_version=self.spyrrow_version,
                    sparrow_revision=SPARROW_REVISION,
                ),
                config=config,
                candidates=candidates,
            ),
            final_candidate_id=final_candidate_id,
            native_report_count=native_report_count,
            terminal_observation_count=1,
            ignored_report_count=ignored_report_count,
            duplicate_candidate_count=duplicate_candidate_count,
            sheet_overflow_count=sheet_overflow_count,
        )

    def _solve_with_progress(
        self,
        instance: Any,
        config: Any,
        on_report: Callable[[Any, Any, bool], None],
    ) -> None:
        progress = self.api.ProgressQueue()
        result: list[Any] = []
        errors: list[BaseException] = []

        def solve() -> None:
            try:
                result.append(instance.solve(config, progress=progress))
            except BaseException as error:  # preserve native solver failures for the caller
                errors.append(error)

        thread = Thread(target=solve, name="spyrrow-solve", daemon=True)
        thread.start()
        report_errors: list[BaseException] = []

        def drain_reports() -> None:
            for report_type, solution in progress.drain():
                if report_errors:
                    continue
                try:
                    on_report(report_type, solution, False)
                except BaseException as error:
                    report_errors.append(error)

        while thread.is_alive():
            drain_reports()
            thread.join(timeout=0.05)
        drain_reports()
        if report_errors:
            raise report_errors[0]
        if errors:
            raise errors[0]
        on_report(self.api.ReportType.Final, result[0], True)

    def _normalize_report_type(self, report_type: Any) -> CandidateReportType | None:
        if report_type == self.api.ReportType.ExplFeas:
            return CandidateReportType.EXPLORATION_FEASIBLE
        if report_type == self.api.ReportType.CmprFeas:
            return CandidateReportType.COMPRESSION_FEASIBLE
        if report_type == self.api.ReportType.Final:
            return CandidateReportType.FINAL
        return None
