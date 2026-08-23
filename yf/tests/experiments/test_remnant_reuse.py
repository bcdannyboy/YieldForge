from __future__ import annotations

import json
from pathlib import Path

import pytest

from yieldforge.domain import (
    Candidate,
    CandidateReportType,
    Part,
    Placement,
    ProjectionMode,
    SolverProjectionBinding,
    SourceTaskBinding,
    StripPackingProblem,
)
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.experiments.remnant_reuse import (
    M4EvidenceError,
    load_m4_input_pack,
    prepare_m4_input_pack,
    publish_m4_input_pack,
)
from yieldforge.experiments.residual_geometry import (
    M3ArchiveEvidence,
    M3ResidualGeometryResult,
    M3ResidualInputPack,
    M3SelectedCandidate,
    M3TaskPair,
    build_m3_input_pack,
    evaluate_m3_residual_geometry,
)
from yieldforge.residuals.contracts import ResidualRuleName
from yieldforge.reuse.contracts import FitSearchConfig, MaterialProvenance

YF_ROOT = Path(__file__).parents[2]
M0_CONTRACT_PATH = YF_ROOT / "experiments" / "m0-contract-v1.json"


def _m0() -> M0ExperimentContract:
    return M0ExperimentContract.model_validate_json(M0_CONTRACT_PATH.read_bytes(), strict=True)


def _binding(tasks_index: int) -> SourceTaskBinding:
    return SourceTaskBinding(
        dataset_id="lectra-7030786-v1.1",
        source_slice_sha256=f"{tasks_index:064x}",
        tasks_index=tasks_index,
        acknowledged_assumption_codes=("interpret_s1_degenerate_entries_as_allowed_rotations",),
        solver_projection=SolverProjectionBinding(
            mode=ProjectionMode.SOURCE_AS_RECORDED,
            projection_sha256=f"{tasks_index + 100:064x}",
            assumption_codes=("interpret_s1_degenerate_entries_as_allowed_rotations",),
            source_flip_part_count=0,
        ),
    )


def _candidate(tasks_index: int, suffix: str, translation_x: float) -> Candidate:
    return Candidate(
        candidate_id=f"candidate-{tasks_index}-{suffix}",
        report_type=CandidateReportType.FINAL,
        seed=0,
        width=10.0,
        density=0.04,
        placements=[
            Placement(
                part_id=f"part-{tasks_index}",
                rotation=0.0,
                translation=(translation_x, 0.0),
            )
        ],
    )


def _task_pair(tasks_index: int) -> M3TaskPair:
    problem = StripPackingProblem(
        name=f"task-{tasks_index}",
        strip_height=10.0,
        sheet_length=10.0,
        parts=[
            Part(
                id=f"part-{tasks_index}",
                shape=[(0.0, 0.0), (0.4, 0.0), (0.4, 10.0), (0.0, 10.0)],
                demand=1,
                allowed_orientations=[0.0],
            )
        ],
    )
    archive = M3ArchiveEvidence(
        seed=0,
        job_id=f"job-{tasks_index}",
        batch_sha256=f"{tasks_index + 200:064x}",
        candidate_count=2,
    )
    selected = (
        M3SelectedCandidate(
            seed=0,
            archive_job_id=archive.job_id,
            archive_batch_sha256=archive.batch_sha256,
            candidate=_candidate(tasks_index, "a", 0.2),
        ),
        M3SelectedCandidate(
            seed=0,
            archive_job_id=archive.job_id,
            archive_batch_sha256=archive.batch_sha256,
            candidate=_candidate(tasks_index, "b", 9.4),
        ),
    )
    return M3TaskPair(
        tasks_index=tasks_index,
        source_task_binding=_binding(tasks_index),
        problem=problem,
        archives=(archive,),
        best_width=10.0,
        envelope_max_width=10.05,
        selected_candidates=selected,
    )


def _m3_evidence() -> tuple[M3ResidualInputPack, M3ResidualGeometryResult]:
    m0 = _m0()
    task_pairs = (_task_pair(7), _task_pair(9))
    pack = build_m3_input_pack(
        m2_result_id="yfgfr-" + "a" * 24,
        m2_result_sha256="sha256:" + "b" * 64,
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        task_pairs=task_pairs,
        expected_task_ids=(7, 9),
    )
    return pack, evaluate_m3_residual_geometry(pack, m0)


def _search_config() -> FitSearchConfig:
    return FitSearchConfig(grid_columns=5, grid_rows=5, maximum_candidates=500)


def _reidentified_m3_input(payload: dict[str, object]) -> M3ResidualInputPack:
    digest = semantic_sha256(payload, excluded_fields={"input_id", "content_sha256"})
    payload["input_id"] = f"yfgi-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return M3ResidualInputPack.model_validate_json(json.dumps(payload), strict=True)


def _reidentified_m3_result(payload: dict[str, object]) -> M3ResidualGeometryResult:
    digest = semantic_sha256(payload, excluded_fields={"result_id", "content_sha256"})
    payload["result_id"] = f"yfgr-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return M3ResidualGeometryResult.model_validate_json(json.dumps(payload), strict=True)


def _result_rebound_to_input(
    result: M3ResidualGeometryResult,
    m3_input: M3ResidualInputPack,
) -> M3ResidualGeometryResult:
    payload = result.model_dump(mode="json")
    payload["input_id"] = m3_input.input_id
    payload["input_sha256"] = m3_input.content_sha256
    return _reidentified_m3_result(payload)


def test_prepare_input_reconstructs_primary_remnants_without_searching(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    m3_input, m3_result = _m3_evidence()

    def forbidden_search(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("M4 input preparation must remain fit-result blind")

    monkeypatch.setattr("yieldforge.reuse.search.search_fit_witness", forbidden_search)
    pack = prepare_m4_input_pack(
        m3_input,
        m3_result,
        _m0(),
        search_config=_search_config(),
    )

    origin_keys = tuple(
        (
            origin.origin_tasks_index,
            origin.origin_candidate_position,
            origin.source_component_sha256,
        )
        for origin in pack.origin_remnants
    )
    assert origin_keys == tuple(sorted(origin_keys))
    assert all(
        origin.source_component_sha256 == origin.remnant.geometry.polygon_sha256
        for origin in pack.origin_remnants
    )
    assert all(
        origin.remnant.material.provenance is MaterialProvenance.ASSUMED
        for origin in pack.origin_remnants
    )
    assert tuple((role.tasks_index, role.part.id) for role in pack.future_part_roles) == (
        (7, "part-7"),
        (9, "part-9"),
    )
    assert pack.generated_order_disclaimer == (
        "greater_task_index_is_deterministic_generated_order_not_observed_chronology"
    )
    assert pack.input_id == f"yfri-{pack.content_sha256[7:31]}"

    primary_hashes = {
        component_hash
        for task in m3_result.task_results
        for observation in (task.first_observation, task.second_observation)
        for classification in observation.classifications
        if classification.rule_name is ResidualRuleName.PRIMARY
        for component_hash in classification.retained_component_sha256
    }
    assert {origin.source_component_sha256 for origin in pack.origin_remnants} == primary_hashes

    path = publish_m4_input_pack(tmp_path, pack)
    assert path.name == f"remnant-reuse-input-{pack.input_id}.json"
    assert publish_m4_input_pack(tmp_path, pack) == path
    assert load_m4_input_pack(path) == pack

    path.write_text("{}\n")
    with pytest.raises(M4EvidenceError, match="immutable"):
        publish_m4_input_pack(tmp_path, pack)


def test_prepare_rejects_m0_m2_and_m3_identity_mismatches() -> None:
    m3_input, m3_result = _m3_evidence()

    invalid_m0 = _m0().model_copy(update={"contract_id": "yfm0-" + "f" * 24})
    with pytest.raises(M4EvidenceError, match="source evidence is invalid"):
        prepare_m4_input_pack(
            m3_input,
            m3_result,
            invalid_m0,
            search_config=_search_config(),
        )

    m2_payload = m3_result.model_dump(mode="json")
    m2_payload["m2_result_id"] = "yfgfr-" + "f" * 24
    m2_payload["m2_result_sha256"] = "sha256:" + "e" * 64
    mismatched_m2_result = _reidentified_m3_result(m2_payload)
    with pytest.raises(M4EvidenceError, match="M2 identity"):
        prepare_m4_input_pack(
            m3_input,
            mismatched_m2_result,
            _m0(),
            search_config=_search_config(),
        )

    input_payload = m3_result.model_dump(mode="json")
    input_payload["input_id"] = "yfgi-" + "f" * 24
    input_payload["input_sha256"] = "sha256:" + "e" * 64
    mismatched_m3_result = _reidentified_m3_result(input_payload)
    with pytest.raises(M4EvidenceError, match="supplied M3 input"):
        prepare_m4_input_pack(
            m3_input,
            mismatched_m3_result,
            _m0(),
            search_config=_search_config(),
        )


def test_prepare_recomputes_and_rejects_reidentified_residual_tampering() -> None:
    m3_input, m3_result = _m3_evidence()
    payload = m3_result.model_dump(mode="json")
    payload["task_results"][0]["first_observation"]["components"][0]["area"] += 1.0  # type: ignore[index,operator]
    tampered_result = _reidentified_m3_result(payload)

    with pytest.raises(M4EvidenceError, match="recomputed M3 result"):
        prepare_m4_input_pack(
            m3_input,
            tampered_result,
            _m0(),
            search_config=_search_config(),
        )


def test_prepare_rejects_reidentified_m3_comparison_tampering() -> None:
    m3_input, m3_result = _m3_evidence()
    payload = m3_result.model_dump(mode="json")
    payload["task_results"][0]["comparison"]["symmetric_difference_area"] += 1.0  # type: ignore[index,operator]
    tampered_result = _reidentified_m3_result(payload)

    with pytest.raises(M4EvidenceError, match="recomputed M3 result"):
        prepare_m4_input_pack(
            m3_input,
            tampered_result,
            _m0(),
            search_config=_search_config(),
        )


def test_prepare_rejects_nonprimary_component_substitution() -> None:
    m3_input, m3_result = _m3_evidence()
    payload = m3_result.model_dump(mode="json")
    classifications = payload["task_results"][0]["first_observation"]["classifications"]  # type: ignore[index]
    primary = next(item for item in classifications if item["rule_name"] == "primary")
    assert primary["retained_component_sha256"]
    assert primary["scrap_component_sha256"]
    primary["retained_component_sha256"], primary["scrap_component_sha256"] = (
        primary["scrap_component_sha256"],
        primary["retained_component_sha256"],
    )
    substituted = _reidentified_m3_result(payload)

    with pytest.raises(M4EvidenceError, match="recomputed M3 result"):
        prepare_m4_input_pack(
            m3_input,
            substituted,
            _m0(),
            search_config=_search_config(),
        )


def test_prepare_rejects_invalid_projected_future_source_part() -> None:
    m3_input, m3_result = _m3_evidence()
    payload = m3_input.model_dump(mode="json")
    payload["task_pairs"][1]["problem"]["parts"][0]["allowed_orientations"] = None  # type: ignore[index]
    invalid_input = _reidentified_m3_input(payload)
    rebound_result = _result_rebound_to_input(m3_result, invalid_input)

    with pytest.raises(M4EvidenceError, match="future source part"):
        prepare_m4_input_pack(
            invalid_input,
            rebound_result,
            _m0(),
            search_config=_search_config(),
        )
