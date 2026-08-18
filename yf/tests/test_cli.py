import hashlib
from pathlib import Path

from yieldforge.cli import main
from yieldforge.datasets.source_manifest import DatasetSourceManifest, SourceFile
from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    CandidateReportType,
    Placement,
    SolverIdentity,
)


class RecordingAdapter:
    def __init__(self) -> None:
        self.problem = None
        self.config = None

    def generate(self, problem, config) -> CandidateBatch:  # type: ignore[no-untyped-def]
        self.problem = problem
        self.config = config
        return CandidateBatch(
            problem=problem,
            solver=SolverIdentity(spyrrow_version="test"),
            config=config,
            candidates=[
                Candidate(
                    candidate_id="cli-candidate",
                    report_type=CandidateReportType.FINAL,
                    seed=config.seed,
                    width=1,
                    density=1,
                    placements=[
                        Placement(
                            part_id=problem.parts[0].id,
                            rotation=0,
                            translation=(0, 0),
                        )
                    ],
                )
            ],
        )


def test_candidates_generate_writes_an_archive(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source = Path(__file__).parents[1] / "benchmarks" / "static" / "m0-smoke.json"
    output = tmp_path / "run-001"
    adapter = RecordingAdapter()
    monkeypatch.setattr("yieldforge.cli.SpyrrowAdapter", lambda: adapter)

    exit_code = main(
        [
            "candidates",
            "generate",
            "--input",
            str(source),
            "--output",
            str(output),
            "--seed",
            "23",
            "--seconds",
            "4",
            "--workers",
            "2",
        ]
    )

    assert exit_code == 0
    assert adapter.problem.name == "m0-smoke"
    assert adapter.config.seed == 23
    assert adapter.config.total_computation_time == 4
    assert adapter.config.num_workers == 2
    assert (output / "manifest.json").is_file()
    assert (output / "candidates.jsonl").is_file()
    assert f"Archived 1 candidate to {output}" in capsys.readouterr().out


def test_datasets_fetch_downloads_each_manifest_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    sources = tmp_path / "sources"
    sources.mkdir()
    source_files = []
    for name, payload in (("parts.gz", b"parts"), ("tasks.gz", b"tasks")):
        source = sources / name
        source.write_bytes(payload)
        source_files.append(
            SourceFile(
                name=name,
                url=source.as_uri(),
                size_bytes=len(payload),
                checksum_algorithm="md5",
                checksum=hashlib.md5(payload).hexdigest(),
            )
        )

    manifest = DatasetSourceManifest(
        schema_version="yieldforge.dataset-source.v1",
        dataset_id="test-corpus",
        title="Test corpus",
        doi="10.example/test",
        version="1",
        license="CC0",
        source_page="https://example.test/corpus",
        files=tuple(source_files),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json())
    output = tmp_path / "raw"

    exit_code = main(
        ["datasets", "fetch", "--manifest", str(manifest_path), "--output", str(output)]
    )

    assert exit_code == 0
    assert (output / "parts.gz").read_bytes() == b"parts"
    assert (output / "tasks.gz").read_bytes() == b"tasks"
    assert capsys.readouterr().out.splitlines() == [
        "parts.gz: downloaded",
        "tasks.gz: downloaded",
    ]
