import json
from pathlib import Path

import pytest

from yieldforge.archive import CandidateArchive, batch_content_hash
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
from yieldforge.experiments.calibration import (
    CalibrationAttemptOutcome,
    CalibrationCandidateObservation,
    CalibrationCell,
    CalibrationSelectedAttempt,
    ConfirmationRunIdentity,
    GeometryConfirmationEvaluation,
    GeometryConfirmationResult,
)
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.experiments.residual_geometry import (
    M3EvidenceError,
    M3ResidualGeometryResult,
    VerifiedCandidateArchive,
    build_m3_input_pack,
    build_m3_task_pair,
    evaluate_m3_residual_geometry,
    load_m3_input_pack,
    load_m3_result,
    load_verified_candidate_archive,
    prepare_m3_input_pack,
    publish_m3_input_pack,
    publish_m3_result,
)
from yieldforge.workbench.contracts import JobStatus

YF_ROOT = Path(__file__).parents[2]
M0_CONTRACT_PATH = YF_ROOT / "experiments" / "m0-contract-v1.json"
COMMITTED_M3_INPUT_PATH = (
    YF_ROOT
    / "experiments"
    / "results"
    / "residual-geometry-input-yfgi-2fe5b848ea643d282c284f90.json"
)
COMMITTED_M3_RESULT_PATH = (
    YF_ROOT
    / "experiments"
    / "results"
    / "residual-geometry-result-yfgr-0ac2c37f0938d9d399e7a076.json"
)


def _problem(*, name: str = "lectra-task-7") -> StripPackingProblem:
    return StripPackingProblem(
        name=name,
        strip_height=10.0,
        sheet_length=20.0,
        parts=[
            Part(
                id="part-a",
                shape=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
                demand=1,
                allowed_orientations=[0.0],
            )
        ],
    )


def _candidate(
    candidate_id: str,
    width: float,
    *,
    seed: int,
    translation_x: float,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        report_type=CandidateReportType.FINAL,
        seed=seed,
        width=width,
        density=4.0 / (width * 10.0),
        placements=[Placement(part_id="part-a", rotation=0.0, translation=(translation_x, 0.0))],
    )


def _batch(
    seed: int = 0,
    *,
    problem: StripPackingProblem | None = None,
    candidates: list[Candidate] | None = None,
) -> CandidateBatch:
    return CandidateBatch(
        problem=problem or _problem(),
        solver=SolverIdentity(spyrrow_version="0.9.0", sparrow_revision="fixture"),
        config=SpyrrowRunConfig(
            seed=seed,
            total_computation_time=10,
            early_termination=False,
            num_workers=1,
            min_items_separation=None,
        ),
        candidates=candidates or [_candidate("candidate-a", 10.0, seed=seed, translation_x=0.0)],
    )


def _binding(tasks_index: int = 7) -> SourceTaskBinding:
    return SourceTaskBinding(
        dataset_id="lectra-7030786-v1.1",
        source_slice_sha256="b" * 64,
        tasks_index=tasks_index,
        acknowledged_assumption_codes=("interpret_s1_degenerate_entries_as_allowed_rotations",),
        solver_projection=SolverProjectionBinding(
            mode=ProjectionMode.SOURCE_AS_RECORDED,
            projection_sha256="c" * 64,
            assumption_codes=("interpret_s1_degenerate_entries_as_allowed_rotations",),
            source_flip_part_count=0,
        ),
    )


def _observations(batch: CandidateBatch) -> tuple[CalibrationCandidateObservation, ...]:
    return tuple(
        CalibrationCandidateObservation(
            candidate_id=candidate.candidate_id,
            width=candidate.width,
            density=candidate.density,
        )
        for candidate in batch.candidates
    )


def _archive(tmp_path: Path, *, seed: int = 0) -> tuple[Path, CandidateBatch]:
    batch = _batch(seed)
    path = tmp_path / f"job-seed-{seed}"
    CandidateArchive.create(path, batch, source_task_binding=_binding())
    return path, batch


def _load(path: Path, batch: CandidateBatch):  # type: ignore[no-untyped-def]
    return load_verified_candidate_archive(
        path,
        job_id=path.name,
        expected_batch_sha256=batch_content_hash(batch),
        expected_candidates=_observations(batch),
        expected_tasks_index=7,
        expected_seed=batch.config.seed,
        expected_problem=batch.problem,
    )


def test_verified_archive_reconstructs_the_exact_candidate_batch(tmp_path: Path) -> None:
    path, batch = _archive(tmp_path)

    verified = _load(path, batch)

    assert verified.job_id == path.name
    assert verified.batch_sha256 == batch_content_hash(batch)
    assert verified.batch == batch
    assert verified.source_task_binding == _binding()


def test_archive_loader_rejects_symlinked_evidence_file(tmp_path: Path) -> None:
    path, batch = _archive(tmp_path)
    target = tmp_path / "candidates-target.jsonl"
    target.write_bytes((path / "candidates.jsonl").read_bytes())
    (path / "candidates.jsonl").unlink()
    (path / "candidates.jsonl").symlink_to(target)

    with pytest.raises(M3EvidenceError, match="regular file"):
        _load(path, batch)


def test_archive_loader_rejects_malformed_jsonl(tmp_path: Path) -> None:
    path, batch = _archive(tmp_path)
    (path / "candidates.jsonl").write_text("{not-json}\n")

    with pytest.raises(M3EvidenceError, match="candidate JSONL"):
        _load(path, batch)


def test_archive_loader_rejects_tampered_candidate_content(tmp_path: Path) -> None:
    path, batch = _archive(tmp_path)
    record = json.loads((path / "candidates.jsonl").read_text())
    record["placements"][0]["translation"][0] = 1.0
    (path / "candidates.jsonl").write_text(json.dumps(record) + "\n")

    with pytest.raises(M3EvidenceError, match="batch SHA-256"):
        _load(path, batch)


def test_archive_loader_rejects_manifest_candidate_count_drift(tmp_path: Path) -> None:
    path, batch = _archive(tmp_path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["candidate_count"] = 2
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(M3EvidenceError, match="candidate count"):
        _load(path, batch)


def test_archive_loader_rejects_candidates_not_in_m2_evidence(tmp_path: Path) -> None:
    path, batch = _archive(tmp_path)
    unexpected = _observations(batch) + (
        CalibrationCandidateObservation(
            candidate_id="candidate-unknown",
            width=10.0,
            density=0.04,
        ),
    )

    with pytest.raises(M3EvidenceError, match="M2 candidate observations"):
        load_verified_candidate_archive(
            path,
            job_id=path.name,
            expected_batch_sha256=batch_content_hash(batch),
            expected_candidates=unexpected,
            expected_tasks_index=7,
            expected_seed=0,
            expected_problem=batch.problem,
        )


def test_archive_loader_rejects_mismatched_projected_problem(tmp_path: Path) -> None:
    path, batch = _archive(tmp_path)

    with pytest.raises(M3EvidenceError, match="projected problem"):
        load_verified_candidate_archive(
            path,
            job_id=path.name,
            expected_batch_sha256=batch_content_hash(batch),
            expected_candidates=_observations(batch),
            expected_tasks_index=7,
            expected_seed=0,
            expected_problem=_problem(name="different-problem"),
        )


def _verified_archive(
    seed: int,
    candidates: list[Candidate],
    *,
    job_id: str | None = None,
) -> VerifiedCandidateArchive:
    batch = _batch(seed, candidates=candidates)
    return VerifiedCandidateArchive(
        job_id=job_id or f"job-{seed}",
        batch_sha256=batch_content_hash(batch),
        batch=batch,
        source_task_binding=_binding(),
    )


def _selection_archives() -> tuple[VerifiedCandidateArchive, ...]:
    return (
        _verified_archive(
            0,
            [
                _candidate("candidate-b", 10.03, seed=0, translation_x=1.0),
                _candidate("candidate-a", 10.0, seed=0, translation_x=0.0),
            ],
        ),
        _verified_archive(
            1,
            [
                _candidate("candidate-d", 10.051, seed=1, translation_x=3.0),
                _candidate("candidate-c", 10.049, seed=1, translation_x=2.0),
            ],
        ),
        _verified_archive(
            2,
            [_candidate("candidate-b", 10.03, seed=2, translation_x=1.0)],
        ),
        _verified_archive(
            3,
            [_candidate("candidate-e", 11.0, seed=3, translation_x=4.0)],
        ),
    )


def test_task_pair_selection_is_residual_blind_and_uses_frozen_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_residual_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("selection must not compute residual geometry")

    monkeypatch.setattr(
        "yieldforge.residuals.geometry.extract_candidate_residual",
        forbidden_residual_call,
    )

    pair = build_m3_task_pair(tasks_index=7, archives=_selection_archives())

    assert pair.best_width == 10.0
    assert pair.envelope_max_width == pytest.approx(10.05)
    assert tuple(item.candidate.candidate_id for item in pair.selected_candidates) == (
        "candidate-a",
        "candidate-b",
    )
    assert tuple(item.seed for item in pair.selected_candidates) == (0, 0)
    assert tuple(item.seed for item in pair.archives) == (0, 1, 2, 3)


def test_duplicate_candidate_id_with_conflicting_geometry_fails_closed() -> None:
    archives = (
        _verified_archive(
            0,
            [_candidate("candidate-a", 10.0, seed=0, translation_x=0.0)],
        ),
        _verified_archive(
            1,
            [_candidate("candidate-a", 10.0, seed=1, translation_x=1.0)],
        ),
    )

    with pytest.raises(M3EvidenceError, match="conflicting geometry"):
        build_m3_task_pair(tasks_index=7, archives=archives)


def test_task_pair_requires_two_distinct_candidates_inside_the_envelope() -> None:
    archives = (
        _verified_archive(
            0,
            [_candidate("candidate-a", 10.0, seed=0, translation_x=0.0)],
        ),
        _verified_archive(
            1,
            [_candidate("candidate-b", 10.1, seed=1, translation_x=1.0)],
        ),
    )

    with pytest.raises(M3EvidenceError, match="two distinct"):
        build_m3_task_pair(tasks_index=7, archives=archives)


def test_input_pack_is_content_addressed_canonical_and_immutable(tmp_path: Path) -> None:
    pair = build_m3_task_pair(tasks_index=7, archives=_selection_archives())
    pack = build_m3_input_pack(
        m2_result_id="yfgfr-" + "a" * 24,
        m2_result_sha256="sha256:" + "b" * 64,
        m0_contract_id="yfm0-" + "c" * 24,
        m0_contract_sha256="sha256:" + "d" * 64,
        task_pairs=(pair,),
        expected_task_ids=(7,),
    )

    assert pack.input_id == f"yfgi-{pack.content_sha256[7:31]}"
    assert pack.primary_geometry_config.part_buffer_distance == 0.0
    assert pack.primary_geometry_config.forbidden_polygons == ()
    path = publish_m3_input_pack(tmp_path, pack)
    assert path.name == f"residual-geometry-input-{pack.input_id}.json"
    assert publish_m3_input_pack(tmp_path, pack) == path
    assert load_m3_input_pack(path) == pack

    path.write_text("{}\n")
    with pytest.raises(M3EvidenceError, match="immutable"):
        publish_m3_input_pack(tmp_path, pack)


def _confirmation_result(
    archives: tuple[VerifiedCandidateArchive, ...],
) -> GeometryConfirmationResult:
    m0 = M0ExperimentContract.model_validate_json(M0_CONTRACT_PATH.read_bytes(), strict=True)
    parent_id = "yfgp-" + "1" * 24
    cells = tuple(
        CalibrationCell(
            parent_protocol_id=parent_id,
            tasks_index=7,
            seconds_per_seed=10,
            seed=archive.batch.config.seed,
        )
        for archive in archives
    )
    attempts = tuple(
        CalibrationAttemptOutcome(
            cell=cell,
            attempt_number=1,
            job_id=archive.job_id,
            status=JobStatus.COMPLETED,
            archive_valid=True,
            batch_sha256=archive.batch_sha256,
            candidates=_observations(archive.batch),
        )
        for cell, archive in zip(cells, archives, strict=True)
    )
    run = ConfirmationRunIdentity(
        parent_protocol_id=parent_id,
        parent_protocol_sha256="sha256:" + "1" * 64,
        m0_contract_sha256=m0.content_sha256,
        calibration_result_id="yfgcr-" + "3" * 24,
        calibration_result_sha256="sha256:" + "3" * 64,
        dataset_id="lectra-7030786-v1.1",
        catalog_sha256="4" * 64,
        api_origin="http://127.0.0.1:8765",
        registered_cell_ids=tuple(cell.cell_id for cell in cells),
    )
    selected = tuple(
        CalibrationSelectedAttempt(
            cell_id=cell.cell_id,
            attempt_number=1,
            job_id=archive.job_id,
        )
        for cell, archive in zip(cells, archives, strict=True)
    )
    evaluation = GeometryConfirmationEvaluation(
        registered_task_count=1,
        registered_cell_count=4,
        valid_archive_count=4,
        valid_archive_rate_percent=100.0,
        qualifying_task_count=1,
        qualifying_task_rate_percent=100.0,
        wilson_95_lower_percent=20.65441548815036,
        wilson_95_upper_percent=100.0,
        decision="proceed_to_m3",
    )
    payload = {
        "schema_version": "yieldforge.geometry-confirmation-result.v1",
        "run": run.model_dump(mode="json"),
        "attempts": [item.model_dump(mode="json") for item in attempts],
        "selected_attempts": [item.model_dump(mode="json") for item in selected],
        "evaluation": evaluation.model_dump(mode="json"),
        "claim_ceiling": (
            "source_recorded_near_tied_geometry_only_not_residual_or_economic_evidence"
        ),
    }
    digest = semantic_sha256(payload)
    return GeometryConfirmationResult(
        result_id=f"yfgfr-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        run=run,
        attempts=attempts,
        selected_attempts=selected,
        evaluation=evaluation,
    )


def test_prepare_input_pack_recomputes_pair_from_canonical_m2_archives(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archives"
    archive_root.mkdir()
    verified = []
    for archive in _selection_archives():
        path = archive_root / archive.job_id
        CandidateArchive.create(
            path,
            archive.batch,
            source_task_binding=archive.source_task_binding,
        )
        verified.append(archive)
    confirmation = _confirmation_result(tuple(verified))
    m0 = M0ExperimentContract.model_validate_json(M0_CONTRACT_PATH.read_bytes(), strict=True)

    pack = prepare_m3_input_pack(
        confirmation,
        m0,
        archive_root,
        required_task_count=1,
    )

    assert pack.expected_task_ids == (7,)
    assert tuple(
        item.candidate.candidate_id for item in pack.task_pairs[0].selected_candidates
    ) == ("candidate-a", "candidate-b")
    assert pack.m2_result_id == confirmation.result_id
    assert pack.m0_contract_id == m0.contract_id


def _m0() -> M0ExperimentContract:
    return M0ExperimentContract.model_validate_json(M0_CONTRACT_PATH.read_bytes(), strict=True)


def _pack_from_archives(
    archives: tuple[VerifiedCandidateArchive, ...],
):  # type: ignore[no-untyped-def]
    m0 = _m0()
    pair = build_m3_task_pair(tasks_index=7, archives=archives)
    return build_m3_input_pack(
        m2_result_id="yfgfr-" + "a" * 24,
        m2_result_sha256="sha256:" + "b" * 64,
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        task_pairs=(pair,),
        expected_task_ids=(7,),
    )


def test_evaluator_records_exact_difference_and_passes_complete_population() -> None:
    pack = _pack_from_archives(_selection_archives())

    result = evaluate_m3_residual_geometry(pack, _m0())

    assert isinstance(result, M3ResidualGeometryResult)
    assert len(result.task_results) == 1
    assert result.task_results[0].valid is True
    assert result.task_results[0].comparison is not None
    assert result.task_results[0].comparison.exact_residual_equal is False
    assert result.summary.registered_task_count == 1
    assert result.summary.valid_task_count == 1
    assert result.summary.exact_residual_difference_count == 1
    assert result.summary.technical_decision == "pass"
    assert result.result_id == f"yfgr-{result.content_sha256[7:31]}"


def test_evaluator_fails_when_distinct_candidates_have_equal_residuals() -> None:
    archives = (
        _verified_archive(
            0,
            [
                _candidate("candidate-a", 10.0, seed=0, translation_x=1.0),
                _candidate("candidate-b", 10.01, seed=0, translation_x=1.0),
            ],
        ),
    )
    pack = _pack_from_archives(archives)

    result = evaluate_m3_residual_geometry(pack, _m0())

    assert result.summary.exact_residual_difference_count == 0
    assert result.summary.technical_decision == "fail"


def test_evaluator_records_invalid_geometry_without_dropping_task() -> None:
    archives = (
        _verified_archive(
            0,
            [
                _candidate("candidate-a", 10.0, seed=0, translation_x=19.0),
                _candidate("candidate-b", 10.01, seed=0, translation_x=1.0),
            ],
        ),
    )
    pack = _pack_from_archives(archives)

    result = evaluate_m3_residual_geometry(pack, _m0())

    assert len(result.task_results) == 1
    assert result.task_results[0].valid is False
    assert result.task_results[0].error_code == "first_placed_material_out_of_sheet"
    assert result.summary.failure_count == 1
    assert result.summary.technical_decision == "fail"


def test_result_rejects_reidentified_summary_tampering() -> None:
    result = evaluate_m3_residual_geometry(
        _pack_from_archives(_selection_archives()),
        _m0(),
    )
    payload = result.model_dump(mode="json")
    payload["summary"]["valid_task_count"] = 0
    digest = semantic_sha256(payload, excluded_fields={"result_id", "content_sha256"})
    payload["result_id"] = f"yfgr-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"

    with pytest.raises(ValueError, match="summary"):
        M3ResidualGeometryResult.model_validate_json(json.dumps(payload), strict=True)


def test_result_is_canonical_immutable_and_rejects_tampering(tmp_path: Path) -> None:
    result = evaluate_m3_residual_geometry(
        _pack_from_archives(_selection_archives()),
        _m0(),
    )
    path = publish_m3_result(tmp_path, result)

    assert path.name == f"residual-geometry-result-{result.result_id}.json"
    assert publish_m3_result(tmp_path, result) == path
    assert load_m3_result(path) == result

    payload = result.model_dump(mode="json")
    payload["task_results"][0]["first_observation"]["components"][0]["component_sha256"] = "f" * 64
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(M3EvidenceError, match="immutable"):
        publish_m3_result(tmp_path, result)


def test_committed_m3_input_is_canonical() -> None:
    pack = load_m3_input_pack(COMMITTED_M3_INPUT_PATH)

    assert pack.input_id == "yfgi-2fe5b848ea643d282c284f90"
    assert pack.content_sha256 == (
        "sha256:2fe5b848ea643d282c284f90fc645ecc8d00a8467e6a7f53fed506cb9fa0eaa0"
    )
    assert len(pack.expected_task_ids) == 203
    assert len(pack.task_pairs) == 203
    assert sum(len(pair.selected_candidates) for pair in pack.task_pairs) == 406


def test_committed_m3_result_is_canonical_and_recomputes_gate() -> None:
    result = load_m3_result(COMMITTED_M3_RESULT_PATH)

    assert result.result_id == "yfgr-0ac2c37f0938d9d399e7a076"
    assert result.content_sha256 == (
        "sha256:0ac2c37f0938d9d399e7a076238bd574ed6d24ec7db3d8ea4e3af7b37165412d"
    )
    assert result.input_id == "yfgi-2fe5b848ea643d282c284f90"
    assert len(result.task_results) == 203
    assert result.summary.valid_task_count == 203
    assert result.summary.exact_residual_difference_count == 202
    assert result.summary.technical_decision == "pass"
