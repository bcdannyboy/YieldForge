from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
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
    assert all(".env" not in rule for rule in rules[1:])


def test_trusted_fixture_has_representative_constraint_and_test_manifest(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    maker = _load_fixture_maker()
    input_dir = tmp_path / "input"
    manifest_path = tmp_path / "fixture-manifest.json"

    maker.write_fixture(input_dir, manifest_path)

    assert {path.name for path in input_dir.iterdir()} == set(maker.EXPECTED_FILENAMES)
    assert len(maker._trusted_frames()["constraints"]) == 1
    manifest = DatasetSourceManifest.model_validate_json(manifest_path.read_text())
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


@pytest.mark.integration
def test_docker_runner_smoke(tmp_path: Path) -> None:
    image = os.environ.get("YIELDFORGE_LECTRA_FIXTURE_IMAGE")
    input_path = os.environ.get("YIELDFORGE_LECTRA_FIXTURE_INPUT")
    manifest_path = os.environ.get("YIELDFORGE_LECTRA_FIXTURE_MANIFEST")
    if not all((image, input_path, manifest_path)):
        pytest.skip("trusted Docker fixture environment is not configured")
    runner = _load_runner()

    report_path = runner.run_qualifier(
        image=image,
        input_dir=Path(input_path),
        output_dir=tmp_path,
        manifest_path=Path(manifest_path),
        timeout_seconds=30,
    )

    assert report_path.name == runner.REPORT_NAME
    assert {path.name for path in tmp_path.iterdir()} == {runner.REPORT_NAME}
    assert b'\n  "' not in report_path.read_bytes()


@pytest.mark.integration
def test_adversarial_pickle_cannot_create_host_output_artifact(tmp_path: Path) -> None:
    image = os.environ.get("YIELDFORGE_LECTRA_ADVERSARIAL_IMAGE")
    input_path = os.environ.get("YIELDFORGE_LECTRA_ADVERSARIAL_INPUT")
    manifest_path = os.environ.get("YIELDFORGE_LECTRA_ADVERSARIAL_MANIFEST")
    if not all((image, input_path, manifest_path)):
        pytest.skip("adversarial Docker fixture environment is not configured")
    runner = _load_runner()

    report_path = runner.run_qualifier(
        image=image,
        input_dir=Path(input_path),
        output_dir=tmp_path,
        manifest_path=Path(manifest_path),
        timeout_seconds=30,
    )

    assert report_path.name == runner.REPORT_NAME
    assert {path.name for path in tmp_path.iterdir()} == {runner.REPORT_NAME}
    assert not (tmp_path / "pickle-escape").exists()
