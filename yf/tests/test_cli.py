import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_datasets_catalog_import_passes_all_explicit_evidence_paths(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    paths = {
        name: tmp_path / name
        for name in ("catalog.json", "catalog-manifest.json", "source-manifest.json", "audit.json")
    }
    calls = []

    def fake_import_catalog(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return SimpleNamespace(
            task_count=256,
            catalog_sha256="4" * 64,
        )

    monkeypatch.setattr("yieldforge.cli.import_catalog", fake_import_catalog)

    exit_code = main(
        [
            "datasets",
            "catalog-import",
            "--catalog",
            str(paths["catalog.json"]),
            "--catalog-manifest",
            str(paths["catalog-manifest.json"]),
            "--source-manifest",
            str(paths["source-manifest.json"]),
            "--audit-report",
            str(paths["audit.json"]),
            "--database-url",
            "postgresql://example.test/yieldforge",
        ]
    )

    assert exit_code == 0
    assert calls == [
        {
            "database_url": "postgresql://example.test/yieldforge",
            "catalog_path": paths["catalog.json"],
            "catalog_manifest_path": paths["catalog-manifest.json"],
            "source_manifest_path": paths["source-manifest.json"],
            "audit_report_path": paths["audit.json"],
        }
    ]
    assert capsys.readouterr().out.strip() == (
        f"Imported 256 catalog tasks with artifact SHA-256 {'4' * 64}"
    )


def test_datasets_catalog_import_uses_approved_manifest_spelling_and_adjacent_catalog_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    catalog = tmp_path / "qualified" / "catalog.json"
    source_manifest = tmp_path / "source-manifest.json"
    audit_report = tmp_path / "audit.json"
    calls = []

    def fake_import_catalog(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return SimpleNamespace(task_count=256, catalog_sha256="4" * 64)

    monkeypatch.setattr("yieldforge.cli.import_catalog", fake_import_catalog)

    assert (
        main(
            [
                "datasets",
                "catalog-import",
                "--catalog",
                str(catalog),
                "--manifest",
                str(source_manifest),
                "--audit-report",
                str(audit_report),
                "--database-url",
                "postgresql://example.test/yieldforge",
            ]
        )
        == 0
    )
    assert calls == [
        {
            "database_url": "postgresql://example.test/yieldforge",
            "catalog_path": catalog,
            "catalog_manifest_path": catalog.parent / "catalog-manifest.json",
            "source_manifest_path": source_manifest,
            "audit_report_path": audit_report,
        }
    ]


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


def test_datasets_audit_check_wraps_deep_json_recursion(tmp_path: Path) -> None:
    report_path, manifest_path = write_audit_inputs(tmp_path)
    depth = max(sys.getrecursionlimit() * 20, 20_000)
    report_path.write_text("[" * depth + "0" + "]" * depth, encoding="utf-8")

    with pytest.raises(DatasetAuditCheckError, match="nesting depth"):
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


def test_experiments_validate_checks_the_frozen_bundle(capsys) -> None:  # type: ignore[no-untyped-def]
    yf_root = Path(__file__).parents[1]

    exit_code = main(
        [
            "experiments",
            "validate",
            "--m0",
            str(yf_root / "experiments" / "m0-contract-v1.json"),
            "--geometry",
            str(yf_root / "experiments" / "pure-geometry-calibration-v1.json"),
            "--catalog",
            str(yf_root / "datasets" / "catalogs" / "lectra-7030786-v1.1" / "lectra-catalog.json"),
            "--catalog-manifest",
            str(
                yf_root / "datasets" / "catalogs" / "lectra-7030786-v1.1" / "catalog-manifest.json"
            ),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out.strip()
    assert "M0=yfm0-29b7efe8ac2a0a9995c4f907" in output
    assert "geometry=yfgp-49906e93ed9ff0446705247b" in output
    assert "calibration=51" in output
    assert "evaluation=203" in output
    assert "confirmation=disabled" in output


def test_experiments_validate_fails_closed_on_tampered_contract(tmp_path: Path) -> None:
    yf_root = Path(__file__).parents[1]
    geometry = json.loads(
        (yf_root / "experiments" / "pure-geometry-calibration-v1.json").read_text()
    )
    geometry["unexpected"] = True
    tampered = tmp_path / "geometry.json"
    tampered.write_text(json.dumps(geometry, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="contract validation failed"):
        main(
            [
                "experiments",
                "validate",
                "--m0",
                str(yf_root / "experiments" / "m0-contract-v1.json"),
                "--geometry",
                str(tampered),
                "--catalog",
                str(
                    yf_root
                    / "datasets"
                    / "catalogs"
                    / "lectra-7030786-v1.1"
                    / "lectra-catalog.json"
                ),
                "--catalog-manifest",
                str(
                    yf_root
                    / "datasets"
                    / "catalogs"
                    / "lectra-7030786-v1.1"
                    / "catalog-manifest.json"
                ),
            ]
        )


def _calibration_cli_args(yf_root: Path, output: Path) -> list[str]:
    return [
        "experiments",
        "calibrate-geometry-api",
        "--m0",
        str(yf_root / "experiments" / "m0-contract-v1.json"),
        "--geometry",
        str(yf_root / "experiments" / "pure-geometry-calibration-v1.json"),
        "--catalog",
        str(yf_root / "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"),
        "--catalog-manifest",
        str(yf_root / "datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json"),
        "--api-origin",
        "http://127.0.0.1:18082",
        "--output",
        str(output),
    ]


def test_calibration_preflight_validates_api_identity_without_creating_output(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    yf_root = Path(__file__).parents[1]
    output = tmp_path / "calibration"
    corpus_calls = []

    class FakeClient:
        def __init__(self, origin: str) -> None:
            assert origin == "http://127.0.0.1:18082"
            self.origin = origin

        def require_corpus(self, **kwargs):  # type: ignore[no-untyped-def]
            corpus_calls.append(kwargs)

    monkeypatch.setattr("yieldforge.cli.CalibrationApiClient", FakeClient)

    exit_code = main(_calibration_cli_args(yf_root, output) + ["--preflight-only"])

    assert exit_code == 0
    assert corpus_calls == [
        {
            "dataset_id": "lectra-7030786-v1.1",
            "catalog_sha256": hashlib.sha256(
                (yf_root / "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json").read_bytes()
            ).hexdigest(),
            "task_count": 256,
            "eligible_task_count": 254,
        }
    ]
    assert not output.exists()
    assert "registered_cells=612" in capsys.readouterr().out


def test_calibration_command_runs_registered_orchestrator(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    yf_root = Path(__file__).parents[1]
    output = tmp_path / "calibration"
    calls = []

    class FakeClient:
        def __init__(self, origin: str) -> None:
            self.origin = origin

        def require_corpus(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs

    def fake_orchestrate(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return SimpleNamespace(evaluation=SimpleNamespace(valid=True, selected_seconds_per_seed=3))

    monkeypatch.setattr("yieldforge.cli.CalibrationApiClient", FakeClient)
    monkeypatch.setattr("yieldforge.cli.orchestrate_calibration", fake_orchestrate)

    exit_code = main(_calibration_cli_args(yf_root, output))

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["output_root"] == output
    assert calls[0]["protocol"].protocol_id == "yfgp-49906e93ed9ff0446705247b"
    assert "selected_seconds_per_seed=3" in capsys.readouterr().out


def test_confirmation_command_runs_exact_registered_evaluation(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    yf_root = Path(__file__).parents[1]
    output = tmp_path / "confirmation"
    result_output = tmp_path / "confirmation-result.json"
    calls = []
    publications = []

    class FakeClient:
        def __init__(self, origin: str) -> None:
            self.origin = origin

        def require_corpus(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs

    def fake_orchestrate(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return SimpleNamespace(
            evaluation=SimpleNamespace(
                decision="proceed_to_m3",
                qualifying_task_rate_percent=100.0,
                valid_archive_rate_percent=100.0,
            )
        )

    def fake_build(protocol, runtime):  # type: ignore[no-untyped-def]
        return SimpleNamespace(result_id="yfgfr-0123456789abcdef01234567")

    def fake_publish(path, result):  # type: ignore[no-untyped-def]
        publications.append((path, result))

    monkeypatch.setattr("yieldforge.cli.CalibrationApiClient", FakeClient)
    monkeypatch.setattr("yieldforge.cli.orchestrate_confirmation", fake_orchestrate)
    monkeypatch.setattr("yieldforge.cli.build_geometry_confirmation_result", fake_build)
    monkeypatch.setattr("yieldforge.cli.publish_geometry_confirmation_result", fake_publish)

    exit_code = main(
        [
            "experiments",
            "confirm-geometry-api",
            "--geometry",
            str(yf_root / "experiments/pure-geometry-confirmation-v2.json"),
            "--api-origin",
            "http://127.0.0.1:18082",
            "--output",
            str(output),
            "--result-output",
            str(result_output),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["output_root"] == output
    assert calls[0]["protocol"].protocol_id == "yfgp-392644d98bb7035fdc218512"
    assert publications[0][0] == result_output
    output_text = capsys.readouterr().out
    assert "registered_cells=812" in output_text
    assert "result_id=yfgfr-0123456789abcdef01234567" in output_text


def test_prepare_residual_geometry_command_binds_m2_archives_and_m0(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    confirmation_path = tmp_path / "confirmation.json"
    m0_path = tmp_path / "m0.json"
    archive_root = tmp_path / "archives"
    output = tmp_path / "results"
    confirmation = SimpleNamespace(result_id="yfgfr-" + "a" * 24)
    m0 = SimpleNamespace(contract_id="yfm0-" + "b" * 24)
    pack = SimpleNamespace(input_id="yfgi-" + "c" * 24, expected_task_ids=tuple(range(203)))
    calls = []

    monkeypatch.setattr(
        "yieldforge.cli.load_geometry_confirmation_result",
        lambda path: confirmation if path == confirmation_path else None,
    )
    monkeypatch.setattr(
        "yieldforge.cli.load_frozen_json",
        lambda path, model: m0 if path == m0_path else None,
    )

    def fake_prepare(confirmation_arg, m0_arg, archive_root_arg):  # type: ignore[no-untyped-def]
        calls.append((confirmation_arg, m0_arg, archive_root_arg))
        return pack

    monkeypatch.setattr("yieldforge.cli.prepare_m3_input_pack", fake_prepare)
    monkeypatch.setattr(
        "yieldforge.cli.publish_m3_input_pack",
        lambda output_arg, pack_arg: output_arg
        / f"residual-geometry-input-{pack_arg.input_id}.json",
    )

    exit_code = main(
        [
            "experiments",
            "prepare-residual-geometry",
            "--m0",
            str(m0_path),
            "--confirmation-result",
            str(confirmation_path),
            "--archive-root",
            str(archive_root),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert calls == [(confirmation, m0, archive_root)]
    text = capsys.readouterr().out
    assert "input_id=yfgi-" in text
    assert "task_pairs=203" in text


def test_evaluate_residual_geometry_command_publishes_canonical_result(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    input_path = tmp_path / "input.json"
    m0_path = tmp_path / "m0.json"
    output = tmp_path / "results"
    pack = SimpleNamespace(input_id="yfgi-" + "a" * 24)
    m0 = SimpleNamespace(contract_id="yfm0-" + "b" * 24)
    result = SimpleNamespace(
        result_id="yfgr-" + "c" * 24,
        summary=SimpleNamespace(
            registered_task_count=203,
            valid_task_count=203,
            exact_residual_difference_count=200,
            technical_decision="pass",
        ),
    )
    calls = []

    monkeypatch.setattr(
        "yieldforge.cli.load_m3_input_pack",
        lambda path: pack if path == input_path else None,
    )
    monkeypatch.setattr(
        "yieldforge.cli.load_frozen_json",
        lambda path, model: m0 if path == m0_path else None,
    )

    def fake_evaluate(pack_arg, m0_arg):  # type: ignore[no-untyped-def]
        calls.append((pack_arg, m0_arg))
        return result

    monkeypatch.setattr("yieldforge.cli.evaluate_m3_residual_geometry", fake_evaluate)
    monkeypatch.setattr(
        "yieldforge.cli.publish_m3_result",
        lambda output_arg, result_arg: output_arg
        / f"residual-geometry-result-{result_arg.result_id}.json",
    )

    exit_code = main(
        [
            "experiments",
            "evaluate-residual-geometry",
            "--m0",
            str(m0_path),
            "--input",
            str(input_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert calls == [(pack, m0)]
    text = capsys.readouterr().out
    assert "result_id=yfgr-" in text
    assert "valid_tasks=203/203" in text
    assert "exact_residual_differences=200" in text
    assert "decision=pass" in text
