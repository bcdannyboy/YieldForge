import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from yieldforge.cli import DatasetAuditCheckError, main
from yieldforge.datasets.lectra_audit import REQUIRED_COLUMNS, audit_frames
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


class EmptyFrame:
    """Small dataframe-shaped test double that keeps CLI tests pandas-free."""

    def __init__(self, columns: tuple[str, ...]) -> None:
        self.columns = list(columns)
        self.dtypes = dict.fromkeys(columns, "object")

    def __len__(self) -> int:
        return 0

    def itertuples(self, *, index: bool, name: None):  # type: ignore[no-untyped-def]
        assert index is False
        assert name is None
        return iter(())


def write_audit_inputs(
    tmp_path: Path,
    *,
    report_dataset_id: str = "lectra-test",
    report_checksum: str = "a" * 32,
    manifest_dataset_id: str = "lectra-test",
    manifest_checksum: str = "a" * 32,
) -> tuple[Path, Path]:
    frames = {table: EmptyFrame(columns) for table, columns in REQUIRED_COLUMNS.items()}
    report = audit_frames(
        frames,
        dataset_id=report_dataset_id,
        source_checksums={"tasks.gz": report_checksum},
    )
    report_path = tmp_path / "lectra-audit.json"
    report_path.write_text(report.model_dump_json(), encoding="utf-8")

    manifest = DatasetSourceManifest(
        schema_version="yieldforge.dataset-source.v1",
        dataset_id=manifest_dataset_id,
        title="Test Lectra corpus",
        doi="10.example/lectra",
        version="1",
        license="CC BY 4.0",
        source_page="https://example.test/lectra",
        files=(
            SourceFile(
                name="tasks.gz",
                url="https://example.test/tasks.gz",
                size_bytes=1,
                checksum_algorithm="md5",
                checksum=manifest_checksum,
            ),
        ),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return report_path, manifest_path


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


def test_datasets_audit_check_validates_passive_report(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    report_path, manifest_path = write_audit_inputs(tmp_path)

    exit_code = main(
        [
            "datasets",
            "audit-check",
            "--report",
            str(report_path),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        "Validated lectra-test audit: tasks=0, parts=0, shapes=0, constraints=0"
    )


@pytest.mark.parametrize("invalid_report", ["{", '{"schema_version": NaN}'])
def test_datasets_audit_check_rejects_malformed_or_nonfinite_report(
    tmp_path: Path, invalid_report: str
) -> None:
    report_path, manifest_path = write_audit_inputs(tmp_path)
    report_path.write_text(invalid_report, encoding="utf-8")

    with pytest.raises(DatasetAuditCheckError, match="Invalid Lectra audit report"):
        main(
            [
                "datasets",
                "audit-check",
                "--report",
                str(report_path),
                "--manifest",
                str(manifest_path),
            ]
        )


def test_datasets_audit_check_rejects_duplicate_report_keys_recursively(tmp_path: Path) -> None:
    report_path, manifest_path = write_audit_inputs(tmp_path)
    report_json = report_path.read_text(encoding="utf-8")
    report_path.write_text(
        report_json.replace(
            f'"tasks.gz":"{"a" * 32}"',
            f'"tasks.gz":"{"a" * 32}","tasks.gz":"{"a" * 32}"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetAuditCheckError, match="duplicate JSON object key.*tasks.gz"):
        main(
            [
                "datasets",
                "audit-check",
                "--report",
                str(report_path),
                "--manifest",
                str(manifest_path),
            ]
        )


def test_datasets_audit_check_rejects_duplicate_manifest_keys_recursively(tmp_path: Path) -> None:
    report_path, manifest_path = write_audit_inputs(tmp_path)
    manifest_json = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        manifest_json.replace(
            f'"checksum":"{"a" * 32}"',
            f'"checksum":"{"a" * 32}","checksum":"{"a" * 32}"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetAuditCheckError, match="duplicate JSON object key.*checksum"):
        main(
            [
                "datasets",
                "audit-check",
                "--report",
                str(report_path),
                "--manifest",
                str(manifest_path),
            ]
        )


def test_datasets_audit_check_rejects_dataset_identity_mismatch(tmp_path: Path) -> None:
    report_path, manifest_path = write_audit_inputs(
        tmp_path,
        report_dataset_id="wrong-release",
    )

    with pytest.raises(DatasetAuditCheckError, match="dataset identity mismatch"):
        main(
            [
                "datasets",
                "audit-check",
                "--report",
                str(report_path),
                "--manifest",
                str(manifest_path),
            ]
        )


def test_datasets_audit_check_rejects_source_checksum_mismatch(tmp_path: Path) -> None:
    report_path, manifest_path = write_audit_inputs(
        tmp_path,
        report_checksum="b" * 32,
    )

    with pytest.raises(DatasetAuditCheckError, match="source checksum mismatch"):
        main(
            [
                "datasets",
                "audit-check",
                "--report",
                str(report_path),
                "--manifest",
                str(manifest_path),
            ]
        )


def test_importing_and_using_normal_cli_does_not_load_pandas() -> None:
    code = """
import sys
from yieldforge.cli import build_parser
build_parser().parse_args(["datasets", "audit-check", "--report", "r", "--manifest", "m"])
assert "pandas" not in sys.modules
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
