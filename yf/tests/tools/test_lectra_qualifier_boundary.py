from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from yieldforge.datasets.source_manifest import DatasetSourceManifest, SourceFile

PROJECT_ROOT = Path(__file__).parents[2]
QUALIFIER_PATH = PROJECT_ROOT / "tools" / "lectra" / "qualify.py"
RUNNER_PATH = PROJECT_ROOT / "tools" / "lectra" / "run_qualifier.py"
FIXTURE_MAKER_PATH = PROJECT_ROOT / "tools" / "lectra" / "make_trusted_fixture.py"
DOCKERFILE_PATH = PROJECT_ROOT / "tools" / "lectra" / "Dockerfile"
DOCKERIGNORE_PATH = PROJECT_ROOT / ".dockerignore"
README_PATH = PROJECT_ROOT / "tools" / "lectra" / "README.md"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_qualifier() -> ModuleType:
    return _load_module("lectra_qualify", QUALIFIER_PATH)


def _load_runner() -> ModuleType:
    return _load_module("lectra_runner", RUNNER_PATH)


def _load_fixture_maker() -> ModuleType:
    return _load_module("lectra_fixture_maker", FIXTURE_MAKER_PATH)


def _fixture_manifest(files: dict[str, bytes]) -> DatasetSourceManifest:
    return DatasetSourceManifest(
        schema_version="yieldforge.dataset-source.v1",
        dataset_id="trusted-test-fixture",
        title="Trusted test fixture",
        doi="test-doi",
        version="1",
        license="test-only",
        source_page="https://example.invalid",
        files=tuple(
            SourceFile(
                name=name,
                url=f"https://example.invalid/{name}",
                size_bytes=len(payload),
                checksum_algorithm="md5",
                checksum=hashlib.md5(payload).hexdigest(),
            )
            for name, payload in sorted(files.items())
        ),
    )


def _valid_fixture_report(tmp_path: Path) -> tuple[bytes, DatasetSourceManifest]:
    pytest.importorskip("pandas")
    from yieldforge.datasets.lectra_audit import audit_frames, report_to_json

    maker = _load_fixture_maker()
    input_dir = tmp_path / "input"
    manifest_path = tmp_path / "fixture-manifest.json"
    maker.write_fixture(input_dir, manifest_path)
    manifest = DatasetSourceManifest.model_validate_json(manifest_path.read_text())
    report = audit_frames(
        maker._trusted_frames(),
        dataset_id=manifest.dataset_id,
        source_checksums={source.name: source.checksum for source in manifest.files},
    )
    return (report_to_json(report) + "\n").encode(), manifest


def _valid_fixture_slice(
    tmp_path: Path,
) -> tuple[bytes, bytes, bytes, DatasetSourceManifest, object]:
    pytest.importorskip("pandas")
    from yieldforge.datasets.lectra_audit import audit_frames, report_to_json
    from yieldforge.datasets.lectra_slice import export_representative_slice

    maker = _load_fixture_maker()
    input_dir = tmp_path / "input"
    manifest_path = tmp_path / "fixture-manifest.json"
    maker.write_fixture(input_dir, manifest_path)
    manifest_payload = manifest_path.read_bytes()
    manifest = DatasetSourceManifest.model_validate_json(manifest_payload)
    report = audit_frames(
        maker._trusted_frames(),
        dataset_id=manifest.dataset_id,
        source_checksums={source.name: source.checksum for source in manifest.files},
    )
    report_payload = (report_to_json(report) + "\n").encode()
    normalized = export_representative_slice(
        maker._trusted_frames(),
        manifest=manifest,
        source_manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
        audit_report_sha256=hashlib.sha256(report_payload).hexdigest(),
    )
    payload = (
        json.dumps(normalized.model_dump(mode="json"), separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    return payload, report_payload, manifest_payload, manifest, report


def _valid_fixture_catalog(
    tmp_path: Path,
) -> tuple[bytes, bytes, bytes, DatasetSourceManifest, object]:
    pytest.importorskip("pandas")
    from yieldforge.datasets.lectra_audit import audit_frames, report_to_json
    from yieldforge.datasets.lectra_slice import export_catalog_slice

    maker = _load_fixture_maker()
    input_dir = tmp_path / "catalog-input"
    manifest_path = tmp_path / "catalog-manifest.json"
    maker.write_fixture(input_dir, manifest_path)
    manifest_payload = manifest_path.read_bytes()
    manifest = DatasetSourceManifest.model_validate_json(manifest_payload)
    frames = {name: frame.copy(deep=True) for name, frame in maker._trusted_frames().items()}
    for table in ("tasks", "parts", "constraints"):
        frames[table]["tasks_index"] = frames[table]["tasks_index"].replace(
            {100: 13_958, 900: 25_801}
        )
    report = audit_frames(
        frames,
        dataset_id=manifest.dataset_id,
        source_checksums={source.name: source.checksum for source in manifest.files},
    )
    report_payload = (report_to_json(report) + "\n").encode()
    normalized = export_catalog_slice(
        frames,
        manifest=manifest,
        source_manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
        audit_report_sha256=hashlib.sha256(report_payload).hexdigest(),
        target_count=2,
    )
    payload = (
        json.dumps(normalized.model_dump(mode="json"), separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    return payload, report_payload, manifest_payload, manifest, report


def test_only_qualifier_deserializes_pickles() -> None:
    production_files = [
        *PROJECT_ROOT.glob("src/**/*.py"),
        *PROJECT_ROOT.glob("tools/**/*.py"),
    ]
    callers = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in production_files
        if "read_pickle" in path.read_text()
    }

    assert callers == {"tools/lectra/qualify.py"}


def test_qualifier_has_explicit_audit_slice_and_catalog_modes() -> None:
    qualifier = _load_qualifier()

    assert qualifier.QUALIFIER_MODES == frozenset({"audit", "catalog", "slice"})
    assert qualifier.SLICE_NAME == "lectra-slice.json"
    assert qualifier.CATALOG_NAME == "lectra-catalog.json"
    assert qualifier.MAX_REPORT_BYTES == 4 * 1024 * 1024
    assert qualifier.MAX_CATALOG_BYTES == 64 * 1024 * 1024
    assert "source_manifest_sha256" in qualifier._qualify_payload.__annotations__
    assert "audit_report_sha256" in qualifier._qualify_payload.__annotations__


@pytest.mark.parametrize("mode", ["slice", "catalog"])
def test_qualifier_export_modes_require_exact_evidence_hash_arguments(mode: str) -> None:
    qualifier = _load_qualifier()

    with pytest.raises(SystemExit):
        qualifier._parse_args(["--mode", mode])

    args = qualifier._parse_args(
        [
            "--mode",
            mode,
            "--source-manifest-sha256",
            "a" * 64,
            "--audit-report-sha256",
            "b" * 64,
        ]
    )
    assert args.mode == mode
    assert args.source_manifest_sha256 == "a" * 64
    assert args.audit_report_sha256 == "b" * 64


def test_catalog_mode_rejects_noncanonical_evidence_hashes() -> None:
    qualifier = _load_qualifier()

    with pytest.raises(qualifier.QualificationBoundaryError, match="lowercase manifest SHA-256"):
        qualifier._qualify_payload(
            mode="catalog",
            source_manifest_sha256="A" * 64,
            audit_report_sha256="b" * 64,
        )


def test_catalog_mode_calls_only_catalog_exporter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yieldforge.datasets import lectra_audit, lectra_slice

    qualifier = _load_qualifier()
    payload, report_payload, manifest_payload, manifest, _ = _valid_fixture_catalog(tmp_path)
    normalized = json.loads(payload)
    frames = _load_fixture_maker()._trusted_frames()
    events: list[str] = []

    @contextmanager
    def fake_memfds(*_: object) -> object:
        yield frames

    class CatalogArtifact:
        def model_dump(self, *, mode: str) -> object:
            assert mode == "json"
            return normalized

    def fake_catalog_export(*_: object, **kwargs: object) -> CatalogArtifact:
        events.append("catalog")
        assert kwargs["source_manifest_sha256"] == hashlib.sha256(manifest_payload).hexdigest()
        assert kwargs["audit_report_sha256"] == hashlib.sha256(report_payload).hexdigest()
        return CatalogArtifact()

    monkeypatch.setattr(qualifier, "MANIFEST_PATH", tmp_path / "catalog-manifest.json")
    monkeypatch.setattr(qualifier, "_load_manifest", lambda: manifest)
    monkeypatch.setattr(qualifier, "_verified_memfds", fake_memfds)
    monkeypatch.setattr(qualifier, "_read_verified_pickle", lambda frame: frame)
    monkeypatch.setattr(qualifier, "_emit_stage_telemetry", lambda stage: events.append(stage))
    monkeypatch.setattr(qualifier, "MAX_REPORT_BYTES", len(payload) - 1)
    monkeypatch.setattr(qualifier, "MAX_CATALOG_BYTES", len(payload))
    monkeypatch.setattr(lectra_audit, "validate_frame_schema", lambda *_: None)
    monkeypatch.setattr(
        lectra_audit,
        "audit_frames",
        lambda *_args, **_kwargs: pytest.fail("catalog mode must not run audit export"),
    )
    monkeypatch.setattr(lectra_slice, "export_catalog_slice", fake_catalog_export)
    monkeypatch.setattr(
        lectra_slice,
        "export_representative_slice",
        lambda *_args, **_kwargs: pytest.fail("catalog mode must not run slice export"),
    )

    result = qualifier._qualify_payload(
        mode="catalog",
        source_manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
        audit_report_sha256=hashlib.sha256(report_payload).hexdigest(),
    )

    assert json.loads(result) == normalized
    assert events.count("catalog") == 1
    assert events[-1] == "catalog-complete"


def test_qualifier_stages_verified_bytes_in_sealed_memfds() -> None:
    source = QUALIFIER_PATH.read_text()

    assert "os.O_NOFOLLOW" in source
    assert "os.memfd_create" in source
    assert "fcntl.F_ADD_SEALS" in source
    assert "fcntl.F_SEAL_WRITE" in source
    assert "fcntl.F_SEAL_GROW" in source
    assert "fcntl.F_SEAL_SHRINK" in source
    assert "fcntl.F_SEAL_SEAL" in source
    assert 'pd.read_pickle(handle, compression="gzip")' in source
    assert "pd.read_pickle(path" not in source


def test_pickle_loader_passes_the_exact_staged_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    qualifier = _load_qualifier()
    handle = io.BytesIO(b"sealed bytes")
    observed: dict[str, object] = {}

    def fake_read_pickle(received: object, *, compression: str) -> str:
        observed.update(handle=received, compression=compression)
        return "frame"

    monkeypatch.setitem(sys.modules, "pandas", SimpleNamespace(read_pickle=fake_read_pickle))

    assert qualifier._read_verified_pickle(handle) == "frame"
    assert observed == {"handle": handle, "compression": "gzip"}


def test_qualifier_validates_each_table_immediately_and_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.datasets import lectra_audit

    qualifier = _load_qualifier()
    payloads = {name: name.encode() for name in qualifier.EXPECTED_FILENAMES}
    manifest = _fixture_manifest(payloads)
    staged = {name.removesuffix(".gz"): name for name in sorted(payloads)}
    events: list[str] = []

    @contextmanager
    def fake_memfds(*_: object) -> object:
        yield staged

    def fake_read(handle: str) -> object:
        events.append(f"read:{handle.removesuffix('.gz')}")
        return object()

    def fake_validate(table_name: str, _: object) -> list[str]:
        events.append(f"validate:{table_name}")
        if table_name == "parts":
            raise RuntimeError("invalid parts schema")
        return []

    monkeypatch.setattr(qualifier, "_load_manifest", lambda: manifest)
    monkeypatch.setattr(qualifier, "_verified_memfds", fake_memfds)
    monkeypatch.setattr(qualifier, "_read_verified_pickle", fake_read)
    monkeypatch.setattr(qualifier, "_emit_stage_telemetry", events.append)
    monkeypatch.setattr(lectra_audit, "validate_frame_schema", fake_validate)
    monkeypatch.setattr(
        lectra_audit,
        "audit_frames",
        lambda *_args, **_kwargs: pytest.fail("audit must not retain an invalid frame"),
    )

    with pytest.raises(RuntimeError, match="invalid parts schema"):
        qualifier._qualify_payload()

    assert events == [
        "sealed-staging",
        "read:constraints",
        "validate:constraints",
        "table-constraints-validated",
        "read:parts",
        "validate:parts",
    ]


def test_stage_telemetry_is_bounded_stderr_only_and_omits_unavailable_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    qualifier = _load_qualifier()
    current = tmp_path / "memory.current"
    current.write_text("123456\n")
    missing_peak = tmp_path / "memory.peak"
    writes: list[tuple[int, bytes]] = []

    monkeypatch.setattr(
        qualifier,
        "CGROUP_MEMORY_FILES",
        (("memory.current", current), ("memory.peak", missing_peak)),
    )
    monkeypatch.setattr(qualifier.sys, "stderr", SimpleNamespace(fileno=lambda: 2))
    monkeypatch.setattr(
        qualifier,
        "_write_all",
        lambda file_descriptor, payload: writes.append((file_descriptor, payload)),
    )

    qualifier._emit_stage_telemetry("table-parts-validated")

    assert writes == [(2, b"stage=table-parts-validated memory.current=123456\n")]
    assert len(writes[0][1]) <= qualifier.MAX_TELEMETRY_LINE_BYTES


@pytest.mark.skipif(sys.platform != "linux", reason="Linux memfd seals are container-only")
def test_staged_file_is_sealed_and_rewound_on_linux(tmp_path: Path) -> None:
    import fcntl

    qualifier = _load_qualifier()
    payloads = {name: name.encode() for name in qualifier.EXPECTED_FILENAMES}
    manifest = _fixture_manifest(payloads)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for name, payload in payloads.items():
        (input_dir / name).write_bytes(payload)

    with qualifier._verified_memfds(input_dir, manifest) as staged:
        handle = staged["tasks"]
        assert handle.tell() == 0
        assert handle.read() == payloads["tasks.gz"]
        seals = fcntl.fcntl(handle.fileno(), fcntl.F_GET_SEALS)
        expected = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        assert seals & expected == expected


def test_runner_rejects_extra_missing_and_symlinked_input(tmp_path: Path) -> None:
    runner = _load_runner()
    payloads = {name: name.encode() for name in runner.EXPECTED_FILENAMES}
    manifest = _fixture_manifest(payloads)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for name, payload in payloads.items():
        (input_dir / name).write_bytes(payload)

    assert runner._validate_input_dir(input_dir, manifest) == input_dir.resolve()

    (input_dir / "unexpected.txt").write_text("not allowed")
    with pytest.raises(runner.QualifierRunnerError, match="exactly"):
        runner._validate_input_dir(input_dir, manifest)
    (input_dir / "unexpected.txt").unlink()

    tasks = input_dir / "tasks.gz"
    tasks.unlink()
    with pytest.raises(runner.QualifierRunnerError, match="exactly"):
        runner._validate_input_dir(input_dir, manifest)

    target = tmp_path / "tasks.gz"
    target.write_bytes(payloads["tasks.gz"])
    tasks.symlink_to(target)
    with pytest.raises(runner.QualifierRunnerError, match="regular"):
        runner._validate_input_dir(input_dir, manifest)


def test_report_validation_is_size_bounded_strict_and_manifest_bound(tmp_path: Path) -> None:
    runner = _load_runner()
    payload, manifest = _valid_fixture_report(tmp_path)

    report = runner._validate_report(payload, manifest, max_bytes=len(payload))
    assert report.dataset_id == manifest.dataset_id

    with pytest.raises(runner.QualifierRunnerError, match="size limit"):
        runner._validate_report(payload, manifest, max_bytes=len(payload) - 1)
    with pytest.raises(runner.QualifierRunnerError, match="trailing JSON data"):
        runner._validate_report(payload + b"noise", manifest, max_bytes=len(payload) + 5)

    changed = json.loads(payload)
    changed["dataset_id"] = "wrong-dataset"
    with pytest.raises(runner.QualifierRunnerError, match="dataset identity"):
        runner._validate_report(json.dumps(changed).encode(), manifest, max_bytes=100_000)

    changed = json.loads(payload)
    changed["source_checksums"]["tasks.gz"] = "f" * 32
    with pytest.raises(runner.QualifierRunnerError, match="source checksum mismatch"):
        runner._validate_report(json.dumps(changed).encode(), manifest, max_bytes=100_000)

    with pytest.raises(runner.QualifierRunnerError, match="duplicate JSON object key"):
        runner._validate_report(b'{"outer":{"x":1,"x":2}}', manifest, max_bytes=100_000)


def test_runner_wraps_deep_json_recursion_as_qualification_error(tmp_path: Path) -> None:
    runner = _load_runner()
    _, manifest = _valid_fixture_report(tmp_path)
    depth = max(sys.getrecursionlimit() * 20, 20_000)
    payload = ("[" * depth + "0" + "]" * depth).encode()

    with pytest.raises(runner.QualifierRunnerError, match="nesting depth"):
        runner._validate_report(payload, manifest)


def test_runner_delegates_passive_report_policy() -> None:
    source = RUNNER_PATH.read_text()

    assert "parse_lectra_audit_report" in source
    assert "bind_lectra_audit_report" in source
    assert "parse_dataset_source_manifest" in source
    assert "read_passive_evidence_file" in source
    assert "JSONDecoder" not in source


def test_validated_report_is_reserialized_as_canonical_finite_json(tmp_path: Path) -> None:
    runner = _load_runner()
    payload, manifest = _valid_fixture_report(tmp_path)
    report = runner._validate_report(payload, manifest)

    canonical = runner._canonical_report_bytes(report)

    assert canonical.endswith(b"\n")
    assert canonical != payload
    assert b'\n  "' not in canonical
    assert json.loads(canonical) == report.model_dump(mode="json")


def test_slice_validation_is_strict_and_bound_to_exact_evidence_bytes(tmp_path: Path) -> None:
    runner = _load_runner()
    payload, report_payload, manifest_payload, manifest, report = _valid_fixture_slice(tmp_path)

    normalized = runner._validate_slice(
        payload,
        manifest,
        report,
        manifest_payload=manifest_payload,
        report_payload=report_payload,
    )

    assert tuple(task.tasks_index for task in normalized.tasks) == (100, 900)
    with pytest.raises(runner.QualifierRunnerError, match="audit report SHA-256 mismatch"):
        runner._validate_slice(
            payload,
            manifest,
            report,
            manifest_payload=manifest_payload,
            report_payload=report_payload + b" ",
        )
    with pytest.raises(runner.QualifierRunnerError, match="duplicate JSON object key"):
        runner._validate_slice(
            b'{"schema_version":"x","schema_version":"y"}',
            manifest,
            report,
            manifest_payload=manifest_payload,
            report_payload=report_payload,
        )


def test_catalog_validation_is_strict_bound_canonical_and_separately_bounded(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    payload, report_payload, manifest_payload, manifest, report = _valid_fixture_catalog(tmp_path)

    normalized = runner._validate_slice(
        payload,
        manifest,
        report,
        manifest_payload=manifest_payload,
        report_payload=report_payload,
        expected_ruleset="lectra-catalog-rules.v1",
        max_bytes=runner.MAX_CATALOG_BYTES,
    )
    canonical = runner._canonical_slice_bytes(
        normalized,
        max_bytes=runner.MAX_CATALOG_BYTES,
    )

    assert normalized.source.conversion_ruleset_version == "lectra-catalog-rules.v1"
    assert canonical.endswith(b"\n")
    assert b'\n  "' not in canonical
    assert json.loads(canonical) == normalized.model_dump(mode="json")
    with pytest.raises(runner.QualifierRunnerError, match="audit report SHA-256 mismatch"):
        runner._validate_slice(
            payload,
            manifest,
            report,
            manifest_payload=manifest_payload,
            report_payload=report_payload + b" ",
            expected_ruleset="lectra-catalog-rules.v1",
            max_bytes=runner.MAX_CATALOG_BYTES,
        )
    with pytest.raises(runner.QualifierRunnerError, match="size limit"):
        runner._validate_slice(
            payload,
            manifest,
            report,
            manifest_payload=manifest_payload,
            report_payload=report_payload,
            expected_ruleset="lectra-catalog-rules.v1",
            max_bytes=len(payload) - 1,
        )
    with pytest.raises(runner.QualifierRunnerError, match="ruleset"):
        runner._validate_slice(
            payload,
            manifest,
            report,
            manifest_payload=manifest_payload,
            report_payload=report_payload,
            expected_ruleset="lectra-slice-rules.v1",
            max_bytes=runner.MAX_CATALOG_BYTES,
        )
    with pytest.raises(runner.QualifierRunnerError, match="size limit"):
        runner._canonical_slice_bytes(normalized, max_bytes=len(canonical) - 1)


def test_mode_limits_keep_audit_and_slice_at_four_mib() -> None:
    runner = _load_runner()

    assert runner._mode_max_bytes("audit") == 4 * 1024 * 1024
    assert runner._mode_max_bytes("slice") == 4 * 1024 * 1024
    assert runner._mode_max_bytes("catalog") == 64 * 1024 * 1024


def test_slice_publisher_uses_fixed_name_and_canonical_exact_bytes(tmp_path: Path) -> None:
    runner = _load_runner()
    payload, report_payload, manifest_payload, manifest, report = _valid_fixture_slice(tmp_path)
    normalized = runner._validate_slice(
        payload,
        manifest,
        report,
        manifest_payload=manifest_payload,
        report_payload=report_payload,
    )
    canonical = runner._canonical_slice_bytes(normalized)
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with runner._open_output_dir(output_dir) as opened:
        destination = runner._publish_report(opened, canonical, name=runner.SLICE_NAME)

    assert destination.name == "lectra-slice.json"
    assert destination.read_bytes() == canonical
    assert {path.name for path in output_dir.iterdir()} == {"lectra-slice.json"}


def test_catalog_publisher_uses_allowlisted_name_limit_and_atomic_exact_bytes(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    payload, report_payload, manifest_payload, manifest, report = _valid_fixture_catalog(tmp_path)
    normalized = runner._validate_slice(
        payload,
        manifest,
        report,
        manifest_payload=manifest_payload,
        report_payload=report_payload,
        expected_ruleset="lectra-catalog-rules.v1",
        max_bytes=runner.MAX_CATALOG_BYTES,
    )
    canonical = runner._canonical_slice_bytes(normalized, max_bytes=runner.MAX_CATALOG_BYTES)
    output_dir = tmp_path / "catalog-output"
    output_dir.mkdir()

    with runner._open_output_dir(output_dir) as opened:
        destination = runner._publish_report(
            opened,
            canonical,
            name=runner.CATALOG_NAME,
            max_bytes=runner.MAX_CATALOG_BYTES,
        )
        assert (
            runner._read_published_report(
                opened,
                runner.CATALOG_NAME,
                max_bytes=runner.MAX_CATALOG_BYTES,
            )
            == canonical
        )
        with pytest.raises(runner.QualifierRunnerError, match="size limit"):
            runner._read_published_report(
                opened,
                runner.CATALOG_NAME,
                max_bytes=len(canonical) - 1,
            )

    assert destination.name == "lectra-catalog.json"
    assert destination.read_bytes() == canonical
    assert {path.name for path in output_dir.iterdir()} == {"lectra-catalog.json"}

    overflow_output = tmp_path / "catalog-overflow"
    overflow_output.mkdir()
    with runner._open_output_dir(overflow_output) as opened:
        with pytest.raises(runner.QualifierRunnerError, match="size limit"):
            runner._publish_report(
                opened,
                canonical,
                name=runner.CATALOG_NAME,
                max_bytes=len(canonical) - 1,
            )
    assert list(overflow_output.iterdir()) == []

    unknown_output = tmp_path / "unknown-output"
    unknown_output.mkdir()
    with runner._open_output_dir(unknown_output) as opened:
        with pytest.raises(runner.QualifierRunnerError, match="unknown passive artifact name"):
            runner._publish_report(
                opened,
                canonical,
                name="renamed-catalog.json",
                max_bytes=runner.MAX_CATALOG_BYTES,
            )
    assert list(unknown_output.iterdir()) == []


def test_publisher_is_atomic_no_clobber_and_exact(tmp_path: Path) -> None:
    runner = _load_runner()
    payload = b'{"safe":true}\n'
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with runner._open_output_dir(output_dir) as opened:
        destination = runner._publish_report(opened, payload)
    assert destination.read_bytes() == payload
    assert {path.name for path in output_dir.iterdir()} == {runner.REPORT_NAME}

    with pytest.raises(runner.QualifierRunnerError, match="empty"):
        with runner._open_output_dir(output_dir):
            pass
    assert destination.read_bytes() == payload


def test_publisher_rejects_symlink_and_unknown_output(tmp_path: Path) -> None:
    runner = _load_runner()
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(real_output, target_is_directory=True)

    with pytest.raises(runner.QualifierRunnerError, match="regular directory"):
        with runner._open_output_dir(linked_output):
            pass

    (real_output / "unknown.txt").write_text("preserve me")
    with pytest.raises(runner.QualifierRunnerError, match="empty"):
        with runner._open_output_dir(real_output):
            pass
    assert (real_output / "unknown.txt").read_text() == "preserve me"


def test_publisher_fails_closed_if_output_path_is_swapped(tmp_path: Path) -> None:
    runner = _load_runner()
    output_dir = tmp_path / "output"
    moved_dir = tmp_path / "moved-output"
    output_dir.mkdir()

    with runner._open_output_dir(output_dir) as opened:
        output_dir.rename(moved_dir)
        output_dir.mkdir()
        with pytest.raises(runner.QualifierRunnerError, match="identity changed"):
            runner._publish_report(opened, b'{"safe":true}\n')

    assert list(moved_dir.iterdir()) == []
    assert list(output_dir.iterdir()) == []


def test_bounded_capture_stops_process_on_stdout_limit() -> None:
    runner = _load_runner()
    aborted: list[bool] = []

    with pytest.raises(runner.QualifierRunnerError, match="stdout size limit"):
        runner._capture_process(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
            timeout_seconds=5,
            stdout_limit=128,
            stderr_limit=128,
            on_abort=lambda: aborted.append(True),
        )
    assert aborted == [True]


def test_bounded_capture_stops_process_on_timeout() -> None:
    runner = _load_runner()
    aborted: list[bool] = []

    with pytest.raises(runner.QualifierRunnerError, match="runtime timeout"):
        runner._capture_process(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_seconds=0.05,
            stdout_limit=128,
            stderr_limit=128,
            on_abort=lambda: aborted.append(True),
        )
    assert aborted == [True]


def test_abort_cleanup_failure_still_terminates_local_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    terminated: list[int] = []
    original_terminate = runner._terminate_process

    def record_termination(process: object) -> None:
        terminated.append(process.pid)
        original_terminate(process)

    def cleanup_failure() -> None:
        raise runner.QualifierRunnerError("could not prove container test-name absent")

    monkeypatch.setattr(runner, "_terminate_process", record_termination)
    with pytest.raises(runner.QualifierRunnerError, match="test-name"):
        runner._capture_process(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_seconds=0.05,
            stdout_limit=128,
            stderr_limit=128,
            on_abort=cleanup_failure,
        )
    assert len(terminated) == 1


def test_container_cleanup_accepts_only_explicit_absence_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    container_name = "yieldforge-lectra-cleanup-test"
    responses = iter(
        [
            runner.subprocess.CompletedProcess([], 1, b"", b"already absent"),
            runner.subprocess.CompletedProcess(
                [], 1, b"", f"Error: No such object: {container_name}".encode()
            ),
        ]
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> object:
        calls.append(command)
        return next(responses)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner._ensure_container_absent(container_name)

    assert calls[0][:3] == ["docker", "rm", "--force"]
    assert calls[1][:4] == ["docker", "inspect", "--type", "container"]


@pytest.mark.parametrize("failure", ["timeout", "oserror", "inspect-present"])
def test_container_cleanup_failure_names_the_unconfirmed_container(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    runner = _load_runner()
    container_name = f"yieldforge-lectra-{failure}"

    def fake_run(command: list[str], **_: object) -> object:
        if failure == "timeout":
            raise runner.subprocess.TimeoutExpired(command, 1)
        if failure == "oserror":
            raise OSError("docker unavailable")
        return runner.subprocess.CompletedProcess(command, 0, b"still-present", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    with pytest.raises(runner.QualifierRunnerError, match=container_name):
        runner._ensure_container_absent(container_name, attempts=2)


def test_runner_refuses_root_and_never_mounts_host_output(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner.os, "getuid", lambda: 0)
    monkeypatch.setattr(runner.os, "getgid", lambda: 0)
    with pytest.raises(runner.QualifierRunnerError, match="root"):
        runner._nonroot_identity()

    command = runner._docker_command(
        image="yieldforge-lectra-qualifier:test",
        container_name="yieldforge-lectra-test",
        input_dir=Path("/absolute/input"),
        uid=501,
        gid=20,
    )
    joined = " ".join(command)
    assert "dst=/input,readonly" in joined
    assert "dst=/output" not in joined
    assert "--network none" in joined
    assert "--read-only" in joined
    assert "--cap-drop ALL" in joined
    assert command[command.index("--memory") + 1] == "16g"
    assert command[command.index("--memory-swap") + 1] == "16g"


@pytest.mark.parametrize("mode", ["slice", "catalog"])
def test_export_docker_command_passes_only_mode_and_evidence_hashes(mode: str) -> None:
    runner = _load_runner()
    command = runner._docker_command(
        image="yieldforge-lectra-qualifier:test",
        container_name="yieldforge-lectra-test",
        input_dir=Path("/absolute/input"),
        uid=501,
        gid=20,
        qualifier_args=(
            "--mode",
            mode,
            "--source-manifest-sha256",
            "a" * 64,
            "--audit-report-sha256",
            "b" * 64,
        ),
    )

    assert command[-6:] == [
        "--mode",
        mode,
        "--source-manifest-sha256",
        "a" * 64,
        "--audit-report-sha256",
        "b" * 64,
    ]
    assert all("audit.json" not in argument for argument in command)
    assert all("manifest.json" not in argument for argument in command)


def test_catalog_runner_applies_catalog_limit_to_every_artifact_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = _load_runner()
    payload, report_payload, _, _, _ = _valid_fixture_catalog(tmp_path)
    report_path = tmp_path / "lectra-audit.json"
    report_path.write_bytes(report_payload)
    output_dir = tmp_path / "catalog-output"
    output_dir.mkdir()
    observed: dict[str, object] = {}
    original_validate = runner._validate_slice
    original_canonical = runner._canonical_slice_bytes
    original_publish = runner._publish_report

    monkeypatch.setattr(runner, "_validate_input_dir", lambda path, _manifest: path.absolute())
    monkeypatch.setattr(runner, "_nonroot_identity", lambda: (501, 20))

    def fake_docker_command(**kwargs: object) -> list[str]:
        observed["qualifier_args"] = kwargs["qualifier_args"]
        return ["docker", "run", "catalog-test"]

    def fake_capture(*_args: object, **kwargs: object) -> tuple[int, bytes, bytes]:
        observed["stdout_limit"] = kwargs["stdout_limit"]
        return 0, payload, b""

    def validate_with_limit(*args: object, **kwargs: object) -> object:
        observed["validation_limit"] = kwargs["max_bytes"]
        observed["expected_ruleset"] = kwargs["expected_ruleset"]
        return original_validate(*args, **kwargs)

    def canonical_with_limit(*args: object, **kwargs: object) -> bytes:
        observed["canonical_limit"] = kwargs["max_bytes"]
        return original_canonical(*args, **kwargs)

    def publish_with_limit(*args: object, **kwargs: object) -> Path:
        observed["publication_limit"] = kwargs["max_bytes"]
        observed["artifact_name"] = kwargs["name"]
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(runner, "_docker_command", fake_docker_command)
    monkeypatch.setattr(runner, "_capture_process", fake_capture)
    monkeypatch.setattr(runner, "_validate_slice", validate_with_limit)
    monkeypatch.setattr(runner, "_canonical_slice_bytes", canonical_with_limit)
    monkeypatch.setattr(runner, "_publish_report", publish_with_limit)
    monkeypatch.setattr(runner, "_ensure_container_absent", lambda _name: None)

    destination = runner.run_qualifier(
        mode="catalog",
        image="yieldforge-lectra-qualifier:test",
        input_dir=tmp_path / "unused-input",
        output_dir=output_dir,
        manifest_path=tmp_path / "catalog-manifest.json",
        audit_report_path=report_path,
        timeout_seconds=30,
    )

    expected_limit = 64 * 1024 * 1024
    assert destination.name == "lectra-catalog.json"
    assert observed["stdout_limit"] == expected_limit
    assert observed["validation_limit"] == expected_limit
    assert observed["canonical_limit"] == expected_limit
    assert observed["publication_limit"] == expected_limit
    assert observed["expected_ruleset"] == "lectra-catalog-rules.v1"
    assert observed["artifact_name"] == "lectra-catalog.json"
    assert observed["qualifier_args"] == (
        "--mode",
        "catalog",
        "--source-manifest-sha256",
        hashlib.sha256((tmp_path / "catalog-manifest.json").read_bytes()).hexdigest(),
        "--audit-report-sha256",
        hashlib.sha256(report_payload).hexdigest(),
    )


def test_runner_cli_accepts_catalog_mode_and_audit_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = _load_runner()
    observed: dict[str, object] = {}
    destination = tmp_path / runner.CATALOG_NAME

    def fake_run_qualifier(**kwargs: object) -> Path:
        observed.update(kwargs)
        return destination

    monkeypatch.setattr(runner, "run_qualifier", fake_run_qualifier)

    assert (
        runner.main(
            [
                "--mode",
                "catalog",
                "--input",
                str(tmp_path / "input"),
                "--output",
                str(tmp_path / "output"),
                "--audit-report",
                str(tmp_path / "lectra-audit.json"),
            ]
        )
        == 0
    )
    assert observed["mode"] == "catalog"
    assert observed["audit_report_path"] == tmp_path / "lectra-audit.json"


def test_dockerfile_is_pinned_minimal_and_nonroot() -> None:
    dockerfile = DOCKERFILE_PATH.read_text()

    assert (
        "python:3.12.11-slim-bookworm@sha256:"
        "519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7" in dockerfile
    )
    assert (
        "ghcr.io/astral-sh/uv:0.10.8@sha256:"
        "88234bc9e09c2b2f6d176a3daf411419eb0370d450a08129257410de9cfafd2a" in dockerfile
    )
    assert "uv sync --locked --only-group data --no-install-project" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "COPY --from=builder /app/.venv /app/.venv" in dockerfile
    assert "run_qualifier.py" not in dockerfile
    assert "src/yieldforge/datasets/normalized_slice.py" in dockerfile
    assert "src/yieldforge/datasets/lectra_slice.py" in dockerfile


def test_dockerignore_denies_by_default_and_allows_only_build_inputs() -> None:
    rules = [
        line.strip()
        for line in DOCKERIGNORE_PATH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert rules[0] == "**"
    assert "!pyproject.toml" in rules
    assert "!uv.lock" in rules
    assert "!datasets/sources/lectra-7030786-v1.1.json" in rules
    assert "!tools/lectra/qualify.py" in rules
    assert "!src/yieldforge/datasets/normalized_slice.py" in rules
    assert "!src/yieldforge/datasets/lectra_slice.py" in rules
    assert all(".env" not in rule for rule in rules[1:])


def test_trusted_fixture_has_representative_constraint_and_test_manifest(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    maker = _load_fixture_maker()
    input_dir = tmp_path / "input"
    manifest_path = tmp_path / "fixture-manifest.json"

    maker.write_fixture(input_dir, manifest_path)

    assert {path.name for path in input_dir.iterdir()} == set(maker.EXPECTED_FILENAMES)
    frames = maker._trusted_frames()
    assert list(frames["parts"].columns) == ["tasks_index", "part_id", "shape_hash"]
    runnable_task = frames["tasks"].loc[frames["tasks"]["tasks_index"] == 100]
    view_task = frames["tasks"].loc[frames["tasks"]["tasks_index"] == 900]
    assert len(runnable_task) == 1
    assert len(view_task) == 1
    assert len(frames["parts"].loc[frames["parts"]["tasks_index"] == 100]) >= 20
    runnable_constraints = frames["constraints"].loc[frames["constraints"]["tasks_index"] == 100]
    assert set(runnable_constraints["type"]) == {"s1"}
    assert len(runnable_constraints) == len(
        frames["parts"].loc[frames["parts"]["tasks_index"] == 100]
    )
    assert any(
        kind != "s1"
        for kind in frames["constraints"].loc[frames["constraints"]["tasks_index"] == 900, "type"]
    )
    manifest = DatasetSourceManifest.model_validate_json(manifest_path.read_text())
    assert manifest.dataset_id == "lectra-7030786-v1.1"
    assert manifest.doi == "10.5281/zenodo.7030786"
    for source in manifest.files:
        payload = (input_dir / source.name).read_bytes()
        assert source.size_bytes == len(payload)
        assert source.checksum == hashlib.md5(payload).hexdigest()


def test_readme_documents_runner_and_no_output_mount() -> None:
    readme = README_PATH.read_text()

    assert "run_qualifier.py" in readme
    assert "sealed memfd" in readme
    assert "stdout" in readme
    assert "No host output path is mounted into the container" in readme
    assert "--memory 16g" in readme


@pytest.mark.integration
def test_docker_runner_smoke(tmp_path: Path) -> None:
    image = os.environ.get("YIELDFORGE_LECTRA_FIXTURE_IMAGE")
    input_path = os.environ.get("YIELDFORGE_LECTRA_FIXTURE_INPUT")
    manifest_path = os.environ.get("YIELDFORGE_LECTRA_FIXTURE_MANIFEST")
    if not all((image, input_path, manifest_path)):
        pytest.skip("trusted Docker fixture environment is not configured")
    runner = _load_runner()
    audit_output = tmp_path / "audit"
    slice_output = tmp_path / "slice"
    audit_output.mkdir()
    slice_output.mkdir()

    report_path = runner.run_qualifier(
        image=image,
        input_dir=Path(input_path),
        output_dir=audit_output,
        manifest_path=Path(manifest_path),
        timeout_seconds=30,
    )
    slice_path = runner.run_qualifier(
        mode="slice",
        image=image,
        input_dir=Path(input_path),
        output_dir=slice_output,
        manifest_path=Path(manifest_path),
        audit_report_path=report_path,
        timeout_seconds=30,
    )

    assert report_path.name == runner.REPORT_NAME
    assert {path.name for path in audit_output.iterdir()} == {runner.REPORT_NAME}
    assert slice_path.name == runner.SLICE_NAME
    assert {path.name for path in slice_output.iterdir()} == {runner.SLICE_NAME}
    assert b'\n  "' not in report_path.read_bytes()
    assert b'\n  "' not in slice_path.read_bytes()


@pytest.mark.integration
def test_adversarial_pickle_cannot_create_host_output_artifact(tmp_path: Path) -> None:
    image = os.environ.get("YIELDFORGE_LECTRA_ADVERSARIAL_IMAGE")
    input_path = os.environ.get("YIELDFORGE_LECTRA_ADVERSARIAL_INPUT")
    manifest_path = os.environ.get("YIELDFORGE_LECTRA_ADVERSARIAL_MANIFEST")
    if not all((image, input_path, manifest_path)):
        pytest.skip("adversarial Docker fixture environment is not configured")
    runner = _load_runner()
    audit_output = tmp_path / "audit"
    slice_output = tmp_path / "slice"
    audit_output.mkdir()
    slice_output.mkdir()

    report_path = runner.run_qualifier(
        image=image,
        input_dir=Path(input_path),
        output_dir=audit_output,
        manifest_path=Path(manifest_path),
        timeout_seconds=30,
    )
    slice_path = runner.run_qualifier(
        mode="slice",
        image=image,
        input_dir=Path(input_path),
        output_dir=slice_output,
        manifest_path=Path(manifest_path),
        audit_report_path=report_path,
        timeout_seconds=30,
    )

    assert report_path.name == runner.REPORT_NAME
    assert slice_path.name == runner.SLICE_NAME
    assert {path.name for path in audit_output.iterdir()} == {runner.REPORT_NAME}
    assert {path.name for path in slice_output.iterdir()} == {runner.SLICE_NAME}
    assert not (tmp_path / "pickle-escape").exists()
