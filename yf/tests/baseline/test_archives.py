from __future__ import annotations

import json
from pathlib import Path

import pytest

from yieldforge.archive import CandidateArchive, batch_content_hash
from yieldforge.baseline.archives import (
    M2ArchiveReference,
    canonical_m2_archive_references,
    verify_problem_candidates,
)
from yieldforge.baseline.contracts import ReusableGeometryProblem
from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    CandidateReportType,
    Part,
    Placement,
    ProjectionMode,
    SolverIdentity,
    SolverProjectionBinding,
    SourceTaskBinding,
    SpyrrowRunConfig,
    StripPackingProblem,
)
from yieldforge.experiments.calibration import CalibrationCandidateObservation
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.temporal_benchmark.contracts import CandidateArchiveRequirement


def _problem() -> ReusableGeometryProblem:
    projection = SolverProjectionBinding(
        mode=ProjectionMode.SOURCE_AS_RECORDED,
        projection_sha256="a" * 64,
        assumption_codes=(),
        source_flip_part_count=0,
    )
    problem = StripPackingProblem(
        name="lectra-task-7",
        strip_height=10.0,
        sheet_length=20.0,
        parts=[
            Part(
                id="part-1",
                shape=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
                demand=1,
                allowed_orientations=[0.0],
            )
        ],
    )
    semantic = {
        "schema_version": "yieldforge.m7-reusable-geometry-problem.v1",
        "source_catalog_sha256": "b" * 64,
        "tasks_index": 7,
        "sheet_type": 1,
        "projection": projection.model_dump(mode="json"),
        "problem": problem.model_dump(mode="json"),
        "candidate_requirement": CandidateArchiveRequirement().model_dump(mode="json"),
        "claim_ceiling": (
            "reusable_source_geometry_and_solver_requirement_only_not_temporal_material_or_"
            "policy_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return ReusableGeometryProblem(
        problem_id=f"yfm7p-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        source_catalog_sha256="b" * 64,
        tasks_index=7,
        sheet_type=1,
        projection=projection,
        problem=problem,
        candidate_requirement=CandidateArchiveRequirement(),
    )


def _candidate(seed: int, *, offset: float = 0.0) -> Candidate:
    return Candidate(
        candidate_id="cand-shared" if offset == 0 else f"cand-{seed}",
        report_type=CandidateReportType.FINAL,
        seed=seed,
        width=2.0 + offset,
        density=0.1,
        placements=[Placement(part_id="part-1", rotation=0.0, translation=(offset, 0.0))],
    )


def _archives(
    tmp_path: Path,
    *,
    include_invalid: bool = False,
) -> tuple[ReusableGeometryProblem, tuple[M2ArchiveReference, ...]]:
    problem = _problem()
    binding = SourceTaskBinding(
        dataset_id="lectra-7030786-v1.1",
        source_slice_sha256=problem.source_catalog_sha256,
        tasks_index=problem.tasks_index,
        solver_projection=problem.projection,
    )
    references = []
    for seed in range(4):
        candidates = [_candidate(seed)]
        if seed == 3:
            candidates.append(_candidate(seed, offset=1.0))
            if include_invalid:
                candidates.append(
                    Candidate(
                        candidate_id="cand-invalid",
                        report_type=CandidateReportType.FINAL,
                        seed=seed,
                        width=2.0,
                        density=0.1,
                        placements=[
                            Placement(
                                part_id="part-1",
                                rotation=90.0,
                                translation=(0.0, 0.0),
                            )
                        ],
                    )
                )
        batch = CandidateBatch(
            problem=problem.problem,
            solver=SolverIdentity(
                spyrrow_version="0.9.0",
                sparrow_revision="881cdcbdf492ca42ba5413954ea6e41889a3becd",
            ),
            config=SpyrrowRunConfig(
                seed=seed,
                total_computation_time=10,
                early_termination=False,
                num_workers=1,
                min_items_separation=None,
            ),
            candidates=candidates,
        )
        job_id = f"job_seed_{seed}"
        CandidateArchive.create(
            tmp_path / job_id,
            batch,
            source_task_binding=binding,
        )
        references.append(
            M2ArchiveReference(
                tasks_index=problem.tasks_index,
                seed=seed,
                job_id=job_id,
                batch_sha256=batch_content_hash(batch),
                candidates=tuple(
                    CalibrationCandidateObservation(
                        candidate_id=item.candidate_id,
                        width=item.width,
                        density=item.density,
                    )
                    for item in candidates
                ),
                source_result_id="yfgcr-" + "c" * 24,
                source_result_sha256="sha256:" + "d" * 64,
            )
        )
    return problem, tuple(references)


def test_canonical_m2_results_cover_every_ordinary_task_seed() -> None:
    references = canonical_m2_archive_references()

    assert len(references) == 254 * 4
    assert len({(item.tasks_index, item.seed) for item in references}) == len(references)
    assert {item.seed for item in references} == {0, 1, 2, 3}
    assert all(item.candidates for item in references)


def test_problem_verification_requires_four_archives_and_deduplicates_layouts(
    tmp_path: Path,
) -> None:
    problem, references = _archives(tmp_path)

    verified = verify_problem_candidates(problem, references, tmp_path)

    assert tuple(item.seed for item in verified.evidence.archives) == (0, 1, 2, 3)
    assert verified.evidence.raw_candidate_count == 5
    assert verified.evidence.distinct_candidate_count == 2
    assert verified.evidence.candidate_ids == ("cand-3", "cand-shared")
    assert tuple(item.candidate_id for item in verified.candidates) == (
        "cand-3",
        "cand-shared",
    )
    assert verified.evidence.candidate_set_id == (f"yfm7c-{verified.evidence.content_sha256[7:31]}")


def test_problem_verification_records_and_excludes_invalid_exact_layouts(
    tmp_path: Path,
) -> None:
    problem, references = _archives(tmp_path, include_invalid=True)

    verified = verify_problem_candidates(problem, references, tmp_path)

    assert verified.evidence.raw_candidate_count == 6
    assert verified.evidence.distinct_candidate_count == 2
    assert verified.evidence.rejected_candidate_ids == ("cand-invalid",)
    assert tuple(item.candidate_id for item in verified.candidates) == (
        "cand-3",
        "cand-shared",
    )


def test_problem_verification_rejects_incomplete_seed_set(tmp_path: Path) -> None:
    problem, references = _archives(tmp_path)

    with pytest.raises(ValueError, match="four ordinary seeds"):
        verify_problem_candidates(problem, references[:-1], tmp_path)


def test_problem_verification_resolves_archives_across_isolated_runtime_roots(
    tmp_path: Path,
) -> None:
    problem, references = _archives(tmp_path)
    second_root = tmp_path / "second-runtime"
    second_root.mkdir()
    for reference in references[2:]:
        (tmp_path / reference.job_id).rename(second_root / reference.job_id)

    verified = verify_problem_candidates(problem, references, (tmp_path, second_root))

    assert verified.evidence.distinct_candidate_count == 2


def test_problem_verification_rejects_archive_tampering(tmp_path: Path) -> None:
    problem, references = _archives(tmp_path)
    path = tmp_path / references[0].job_id / "candidates.jsonl"
    record = json.loads(path.read_text().splitlines()[0])
    record["width"] = 9.0
    path.write_text(json.dumps(record, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="candidate archive"):
        verify_problem_candidates(problem, references, tmp_path)
