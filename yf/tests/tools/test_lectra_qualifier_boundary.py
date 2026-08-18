from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from yieldforge.datasets.source_manifest import DatasetSourceManifest, SourceFile

PROJECT_ROOT = Path(__file__).parents[2]
QUALIFIER_PATH = PROJECT_ROOT / "tools" / "lectra" / "qualify.py"
FIXTURE_MAKER_PATH = PROJECT_ROOT / "tools" / "lectra" / "make_trusted_fixture.py"
DOCKERFILE_PATH = PROJECT_ROOT / "tools" / "lectra" / "Dockerfile"
DOCKERIGNORE_PATH = PROJECT_ROOT / ".dockerignore"
README_PATH = PROJECT_ROOT / "tools" / "lectra" / "README.md"


def _load_qualifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lectra_qualify", QUALIFIER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_fixture_maker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lectra_fixture_maker", FIXTURE_MAKER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_qualifier_requires_exact_verified_input_set(tmp_path: Path) -> None:
    qualifier = _load_qualifier()
    payloads = {name: name.encode() for name in qualifier.EXPECTED_FILENAMES}
    manifest = _fixture_manifest(payloads)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for name, payload in payloads.items():
        (input_dir / name).write_bytes(payload)

    verified = qualifier._verify_input(input_dir, manifest)
    assert set(verified) == qualifier.EXPECTED_FILENAMES

    (input_dir / "unexpected.txt").write_text("not allowed")
    with pytest.raises(qualifier.QualificationBoundaryError, match="exactly"):
        qualifier._verify_input(input_dir, manifest)


def test_qualifier_rejects_missing_mismatched_and_symlinked_input(tmp_path: Path) -> None:
    qualifier = _load_qualifier()
    payloads = {name: name.encode() for name in qualifier.EXPECTED_FILENAMES}
    manifest = _fixture_manifest(payloads)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for name, payload in payloads.items():
        (input_dir / name).write_bytes(payload)

    missing = input_dir / "tasks.gz"
    missing.unlink()
    with pytest.raises(qualifier.QualificationBoundaryError, match="exactly"):
        qualifier._verify_input(input_dir, manifest)

    missing.write_bytes(b"wrong")
    with pytest.raises(qualifier.QualificationBoundaryError, match="size"):
        qualifier._verify_input(input_dir, manifest)

    missing.write_bytes(b"TASKS.GZ")
    with pytest.raises(qualifier.QualificationBoundaryError, match="MD5"):
        qualifier._verify_input(input_dir, manifest)

    missing.unlink()
    target = tmp_path / "tasks.gz"
    target.write_bytes(payloads["tasks.gz"])
    missing.symlink_to(target)
    with pytest.raises(qualifier.QualificationBoundaryError, match="regular files"):
        qualifier._verify_input(input_dir, manifest)


def test_qualifier_refuses_nonempty_output_and_writes_atomically(tmp_path: Path) -> None:
    qualifier = _load_qualifier()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = qualifier._atomic_write_report(output_dir, '{"ok":true}\n')
    assert result == output_dir / "lectra-audit.json"
    assert result.read_text() == '{"ok":true}\n'
    assert [path.name for path in output_dir.iterdir()] == ["lectra-audit.json"]

    with pytest.raises(qualifier.QualificationBoundaryError, match="empty"):
        qualifier._atomic_write_report(output_dir, '{"ok":false}\n')


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
    assert "src/yieldforge/archive.py" not in dockerfile
    assert "src/yieldforge/spyrrow_adapter.py" not in dockerfile


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


def test_readme_documents_the_complete_hardened_runtime_boundary() -> None:
    readme = README_PATH.read_text()
    required_flags = {
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges:true",
        "--pids-limit 128",
        "--memory 8g",
        "--cpus 4",
        "--ulimit nofile=1024:1024",
        "--ulimit nproc=128:128",
        "--ipc none",
        "--init",
        "--tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m",
        '--user "$(id -u):$(id -g)"',
        "dst=/input,readonly",
        "dst=/output",
    }

    assert all(flag in readme for flag in required_flags)


def test_trusted_fixture_matches_exact_boundary_and_emits_test_manifest(tmp_path: Path) -> None:
    maker = _load_fixture_maker()
    input_dir = tmp_path / "input"
    manifest_path = tmp_path / "fixture-manifest.json"

    maker.write_fixture(input_dir, manifest_path)

    assert {path.name for path in input_dir.iterdir()} == set(maker.EXPECTED_FILENAMES)
    manifest = DatasetSourceManifest.model_validate_json(manifest_path.read_text())
    assert {source.name for source in manifest.files} == set(maker.EXPECTED_FILENAMES)
    for source in manifest.files:
        payload = (input_dir / source.name).read_bytes()
        assert source.size_bytes == len(payload)
        assert source.checksum == hashlib.md5(payload).hexdigest()

    with pytest.raises(ValueError, match="empty"):
        maker.write_fixture(input_dir)
