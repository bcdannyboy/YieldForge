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
    SolverIdentity,
    SourceTaskBinding,
    SpyrrowRunConfig,
    StripPackingProblem,
)


def make_batch() -> CandidateBatch:
    return CandidateBatch(
        problem=StripPackingProblem(
            name="archive-test",
            strip_height=10,
            sheet_length=20,
            parts=[
                Part(
                    id="square",
                    shape=[(0, 0), (1, 0), (1, 1), (0, 1)],
                    demand=1,
                    allowed_orientations=[0],
                )
            ],
        ),
        solver=SolverIdentity(spyrrow_version="0.9.0", sparrow_revision="test-revision"),
        config=SpyrrowRunConfig(seed=7, total_computation_time=1),
        candidates=[
            Candidate(
                candidate_id="candidate-1",
                report_type=CandidateReportType.FINAL,
                seed=7,
                width=1,
                density=1,
                placements=[Placement(part_id="square", rotation=0, translation=(0, 0))],
            )
        ],
    )


def make_source_task_binding() -> SourceTaskBinding:
    return SourceTaskBinding(
        dataset_id="lectra-7030786-v1.1",
        source_slice_sha256="d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8",
        tasks_index=13958,
        acknowledged_assumption_codes=("interpret_s1_degenerate_entries_as_allowed_rotations",),
    )


def test_archive_writes_manifest_and_jsonl_candidates(tmp_path: Path) -> None:
    batch = make_batch()
    output = tmp_path / "run-001"

    CandidateArchive.create(output, batch)

    manifest = json.loads((output / "manifest.json").read_text())
    records = (output / "candidates.jsonl").read_text().splitlines()
    assert manifest["schema_version"] == "yieldforge.candidate-archive.v1"
    assert manifest["candidate_count"] == 1
    assert manifest["batch_sha256"] == batch_content_hash(batch)
    assert "source_task_binding" not in manifest
    assert json.loads(records[0])["candidate_id"] == "candidate-1"


def test_archive_persists_exact_source_task_binding_when_present(tmp_path: Path) -> None:
    output = tmp_path / "source-run"
    binding = make_source_task_binding()

    CandidateArchive.create(output, make_batch(), source_task_binding=binding)

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["source_task_binding"] == binding.model_dump(mode="json")


def test_archive_refuses_to_overwrite_existing_path(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(FileExistsError, match="archive path already exists"):
        CandidateArchive.create(output, make_batch())


def test_batch_content_hash_is_deterministic() -> None:
    first = make_batch()
    second = CandidateBatch.model_validate(first.model_dump())

    assert batch_content_hash(first) == batch_content_hash(second)
