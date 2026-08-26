"""Verify and bind the frozen ordinary M2 candidate archives for M7."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yieldforge.baseline.contracts import (
    M2ArchiveReference,
    M7CandidateArchiveEvidence,
    M7CandidateSetEvidence,
    ReusableGeometryProblem,
)
from yieldforge.baseline.geometry import PreparedLayoutFootprint, prepare_layout_footprint
from yieldforge.domain import Candidate
from yieldforge.experiments.calibration import (
    GeometryCalibrationResult,
    GeometryConfirmationResult,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.experiments.residual_geometry import (
    M3EvidenceError,
    load_verified_candidate_archive,
)
from yieldforge.reuse.contracts import RemnantFitConfig

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_CALIBRATION_RESULT_PATH = (
    _PACKAGE_ROOT
    / "experiments/results/pure-geometry-calibration-yfgcr-c333f934c363abc0d78082ec.json"
)
_CONFIRMATION_RESULT_PATH = (
    _PACKAGE_ROOT
    / "experiments/results/pure-geometry-confirmation-yfgfr-47d42952e0003154baceee02.json"
)


@dataclass(frozen=True)
class VerifiedCandidateRejectionLayout:
    """Portable rejection-only geometry retained from exact archive verification.

    Candidate geometry is reusable across temporal events, but material identity is
    not: M6 binds material at each event. The explicit scope prevents callers from
    treating a source sheet type as an M0 material-compatibility identity.
    """

    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    candidate_id: str
    source_transform_sha256: str
    fit_config_sha256: str
    layout_area: float
    layout_width: float
    layout_height: float
    layout_bounds: tuple[float, float, float, float]
    material_binding_scope: Literal["temporal_event"] = "temporal_event"

    def __post_init__(self) -> None:
        if not all(
            (
                self.problem_id,
                self.problem_sha256,
                self.candidate_set_id,
                self.candidate_set_sha256,
                self.candidate_id,
                self.source_transform_sha256,
                self.fit_config_sha256,
            )
        ):
            raise ValueError("verified rejection layout identities must be nonempty")
        if any(
            not math.isfinite(value)
            for value in (
                self.layout_area,
                self.layout_width,
                self.layout_height,
                *self.layout_bounds,
            )
        ):
            raise ValueError("verified rejection layout measurements must be finite")
        if self.layout_area <= 0 or self.layout_width <= 0 or self.layout_height <= 0:
            raise ValueError("verified rejection layout measurements must be positive")
        min_x, min_y, max_x, max_y = self.layout_bounds
        if max_x < min_x or max_y < min_y:
            raise ValueError("verified rejection layout bounds must be ordered")
        if (
            self.layout_width != float(max_x - min_x)
            or self.layout_height != float(max_y - min_y)
        ):
            raise ValueError("verified rejection layout dimensions differ from bounds")


@dataclass(frozen=True)
class VerifiedProblemCandidates:
    """Runtime candidate layouts paired with portable verified evidence."""

    evidence: M7CandidateSetEvidence
    candidates: tuple[Candidate, ...]
    rejection_layouts: tuple[VerifiedCandidateRejectionLayout, ...] = ()


def _references_from_result(
    result: GeometryCalibrationResult | GeometryConfirmationResult,
) -> tuple[M2ArchiveReference, ...]:
    attempt_by_key = {(item.cell.cell_id, item.attempt_number): item for item in result.attempts}
    references = []
    for selected in result.selected_attempts:
        attempt = attempt_by_key[(selected.cell_id, selected.attempt_number)]
        cell = attempt.cell
        if cell.seconds_per_seed != 10:
            continue
        if not attempt.archive_valid or attempt.batch_sha256 is None or not attempt.candidates:
            raise ValueError("canonical M2 ordinary result contains invalid archive evidence")
        references.append(
            M2ArchiveReference(
                tasks_index=cell.tasks_index,
                seed=cell.seed,
                job_id=selected.job_id,
                batch_sha256=attempt.batch_sha256,
                candidates=attempt.candidates,
                source_result_id=result.result_id,
                source_result_sha256=result.content_sha256,
            )
        )
    return tuple(references)


def canonical_m2_archive_references(
    *,
    calibration_result_path: Path = _CALIBRATION_RESULT_PATH,
    confirmation_result_path: Path = _CONFIRMATION_RESULT_PATH,
) -> tuple[M2ArchiveReference, ...]:
    """Load the canonical M2 mappings for all 254 eligible tasks and four ordinary seeds."""

    calibration = GeometryCalibrationResult.model_validate_json(
        Path(calibration_result_path).read_bytes(), strict=True
    )
    confirmation = GeometryConfirmationResult.model_validate_json(
        Path(confirmation_result_path).read_bytes(), strict=True
    )
    combined = _references_from_result(calibration) + _references_from_result(confirmation)
    ordered = tuple(sorted(combined, key=lambda item: (item.tasks_index, item.seed)))
    keys = tuple((item.tasks_index, item.seed) for item in ordered)
    if len(ordered) != 254 * 4 or len(keys) != len(set(keys)):
        raise ValueError("canonical M2 results do not cover 254 tasks by four ordinary seeds")
    if any(
        tuple(item.seed for item in ordered if item.tasks_index == tasks_index) != (0, 1, 2, 3)
        for tasks_index in {item.tasks_index for item in ordered}
    ):
        raise ValueError("canonical M2 task evidence does not contain four ordinary seeds")
    return ordered


def _candidate_geometry_content(candidate: Candidate) -> dict[str, object]:
    payload = candidate.model_dump(mode="json")
    payload.pop("seed", None)
    payload.pop("report_type", None)
    return payload


def _prepare_valid_exact_layout(
    problem: ReusableGeometryProblem,
    candidate: Candidate,
    config: RemnantFitConfig,
) -> PreparedLayoutFootprint | None:
    try:
        prepared = prepare_layout_footprint(problem.problem, candidate, config)
    except ValueError:
        return None
    min_x, min_y, max_x, max_y = prepared.bounds
    tolerance = config.coordinate_tolerance
    valid = (
        min_x >= -tolerance
        and min_y >= -tolerance
        and max_x <= candidate.width + tolerance
        and max_x <= problem.problem.sheet_length + tolerance
        and max_y <= problem.problem.strip_height + tolerance
    )
    return prepared if valid else None


def _rejection_layout(
    *,
    problem: ReusableGeometryProblem,
    evidence: M7CandidateSetEvidence,
    candidate: Candidate,
    prepared: PreparedLayoutFootprint,
    fit_config: RemnantFitConfig,
) -> VerifiedCandidateRejectionLayout:
    min_x, min_y, max_x, max_y = tuple(float(value) for value in prepared.bounds)
    transform_payload = {
        "schema_version": "yieldforge.m7-candidate-transform.v1",
        "candidate_id": candidate.candidate_id,
        "placements": [item.model_dump(mode="json") for item in candidate.placements],
    }
    return VerifiedCandidateRejectionLayout(
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        candidate_set_id=evidence.candidate_set_id,
        candidate_set_sha256=evidence.content_sha256,
        candidate_id=candidate.candidate_id,
        source_transform_sha256=f"sha256:{semantic_sha256(transform_payload)}",
        fit_config_sha256=f"sha256:{semantic_sha256(fit_config.model_dump(mode='json'))}",
        layout_area=float(prepared.geometry.area),
        layout_width=float(max_x - min_x),
        layout_height=float(max_y - min_y),
        layout_bounds=(min_x, min_y, max_x, max_y),
    )


def _resolve_archive(
    archive_roots: Path | tuple[Path, ...],
    job_id: str,
) -> Path:
    roots = (
        (Path(archive_roots),)
        if isinstance(archive_roots, (str, Path))
        else tuple(Path(item) for item in archive_roots)
    )
    matching = tuple(root / job_id for root in roots if (root / job_id).exists())
    if len(matching) != 1:
        raise ValueError("candidate archive job must resolve in exactly one runtime root")
    return matching[0]


def verify_problem_candidates(
    problem: ReusableGeometryProblem,
    references: tuple[M2ArchiveReference, ...],
    archive_root: Path | tuple[Path, ...],
) -> VerifiedProblemCandidates:
    """Re-open four immutable M2 archives and build one common deduplicated candidate set."""

    ordered = tuple(sorted(references, key=lambda item: item.seed))
    if (
        len(ordered) != 4
        or tuple(item.seed for item in ordered) != (0, 1, 2, 3)
        or any(item.tasks_index != problem.tasks_index for item in ordered)
    ):
        raise ValueError("problem candidate verification requires the four ordinary seeds")

    verified_archives = []
    archive_evidence = []
    try:
        for reference in ordered:
            archive = load_verified_candidate_archive(
                _resolve_archive(archive_root, reference.job_id),
                job_id=reference.job_id,
                expected_batch_sha256=reference.batch_sha256,
                expected_candidates=reference.candidates,
                expected_tasks_index=problem.tasks_index,
                expected_seed=reference.seed,
                expected_problem=problem.problem,
            )
            if (
                archive.source_task_binding.source_slice_sha256 != problem.source_catalog_sha256
                or archive.source_task_binding.solver_projection != problem.projection
            ):
                raise ValueError("candidate archive source projection differs from M7 problem")
            verified_archives.append(archive)
            archive_evidence.append(
                M7CandidateArchiveEvidence(
                    seed=reference.seed,
                    job_id=reference.job_id,
                    batch_sha256=reference.batch_sha256,
                    candidate_count=len(archive.batch.candidates),
                    source_result_id=reference.source_result_id,
                    source_result_sha256=reference.source_result_sha256,
                )
            )
    except (M3EvidenceError, OSError, ValueError) as error:
        raise ValueError("candidate archive failed M7 verification") from error

    unique: dict[str, Candidate] = {}
    for archive in verified_archives:
        for candidate in archive.batch.candidates:
            existing = unique.get(candidate.candidate_id)
            if existing is not None:
                if _candidate_geometry_content(existing) != _candidate_geometry_content(candidate):
                    raise ValueError("candidate archive repeats one ID with conflicting geometry")
                continue
            unique[candidate.candidate_id] = candidate
    if not unique:
        raise ValueError("candidate archive set contains zero candidates")
    fit_config = RemnantFitConfig()
    prepared_by_id = {
        candidate_id: _prepare_valid_exact_layout(
            problem,
            unique[candidate_id],
            fit_config,
        )
        for candidate_id in sorted(unique)
    }
    rejected_candidate_ids = tuple(
        candidate_id
        for candidate_id, prepared in prepared_by_id.items()
        if prepared is None
    )
    rejected = set(rejected_candidate_ids)
    candidates = tuple(unique[item] for item in sorted(unique) if item not in rejected)
    if not candidates:
        raise ValueError("candidate archive set contains zero exact-layout-valid candidates")
    semantic = {
        "schema_version": "yieldforge.m7-candidate-set.v1",
        "problem_id": problem.problem_id,
        "problem_sha256": problem.content_sha256,
        "archives": [item.model_dump(mode="json") for item in archive_evidence],
        "raw_candidate_count": sum(item.candidate_count for item in archive_evidence),
        "distinct_candidate_count": len(candidates),
        "candidate_ids": [item.candidate_id for item in candidates],
        "rejected_candidate_ids": list(rejected_candidate_ids),
        "claim_ceiling": (
            "verified_shared_geometry_candidates_only_not_actions_policy_value_or_savings_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    evidence = M7CandidateSetEvidence(
        candidate_set_id=f"yfm7c-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        archives=tuple(archive_evidence),  # type: ignore[arg-type]
        raw_candidate_count=sum(item.candidate_count for item in archive_evidence),
        distinct_candidate_count=len(candidates),
        candidate_ids=tuple(item.candidate_id for item in candidates),
        rejected_candidate_ids=rejected_candidate_ids,
    )
    rejection_layouts = tuple(
        _rejection_layout(
            problem=problem,
            evidence=evidence,
            candidate=candidate,
            prepared=prepared_by_id[candidate.candidate_id],  # type: ignore[arg-type]
            fit_config=fit_config,
        )
        for candidate in candidates
    )
    return VerifiedProblemCandidates(
        evidence=evidence,
        candidates=candidates,
        rejection_layouts=rejection_layouts,
    )


__all__ = [
    "M2ArchiveReference",
    "VerifiedCandidateRejectionLayout",
    "VerifiedProblemCandidates",
    "canonical_m2_archive_references",
    "verify_problem_candidates",
]
