"""Adapter from YieldForge contracts to Spyrrow's native Python API."""

import hashlib
import importlib.metadata
import json
from threading import Thread
from typing import Any

from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    CandidateReportType,
    Placement,
    SolverIdentity,
    SpyrrowRunConfig,
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
        reports = self._solve_with_progress(instance, solver_config)

        candidates: list[Candidate] = []
        seen: set[str] = set()
        for report_type, solution in reports:
            normalized_type = self._normalize_report_type(report_type)
            if normalized_type is None or solution.width > problem.sheet_length + 1e-9:
                continue
            placements = [
                Placement(
                    part_id=placed.id,
                    rotation=placed.rotation,
                    translation=tuple(placed.translation),
                )
                for placed in solution.placed_items
            ]
            candidate_id = _candidate_id(solution.width, placements)
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            candidates.append(
                Candidate(
                    candidate_id=candidate_id,
                    report_type=normalized_type,
                    seed=config.seed,
                    width=solution.width,
                    density=solution.density,
                    placements=placements,
                )
            )

        return CandidateBatch(
            problem=problem,
            solver=SolverIdentity(
                spyrrow_version=self.spyrrow_version,
                sparrow_revision=SPARROW_REVISION,
            ),
            config=config,
            candidates=candidates,
        )

    def _solve_with_progress(self, instance: Any, config: Any) -> list[tuple[Any, Any]]:
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
        reports: list[tuple[Any, Any]] = []
        while thread.is_alive():
            reports.extend(progress.drain())
            thread.join(timeout=0.05)
        reports.extend(progress.drain())
        if errors:
            raise errors[0]
        reports.append((self.api.ReportType.Final, result[0]))
        return reports

    def _normalize_report_type(self, report_type: Any) -> CandidateReportType | None:
        if report_type == self.api.ReportType.ExplFeas:
            return CandidateReportType.EXPLORATION_FEASIBLE
        if report_type == self.api.ReportType.CmprFeas:
            return CandidateReportType.COMPRESSION_FEASIBLE
        if report_type == self.api.ReportType.Final:
            return CandidateReportType.FINAL
        return None
