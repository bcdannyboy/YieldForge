import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_normalized_slice import source_identity, valid_slice

from yieldforge.datasets import passive_report
from yieldforge.datasets.lectra_audit import REQUIRED_COLUMNS, audit_frames
from yieldforge.datasets.passive_report import (
    MAX_PASSIVE_EVIDENCE_BYTES,
    PassiveEvidenceError,
    bind_lectra_audit_report,
    bind_normalized_slice_evidence,
    decode_strict_json_bytes,
    load_lectra_audit_evidence,
    load_normalized_slice_evidence,
    load_unbound_normalized_slice,
    parse_dataset_source_manifest,
    parse_lectra_audit_report,
    parse_normalized_slice,
    read_passive_evidence_file,
)
from yieldforge.datasets.source_manifest import DatasetSourceManifest, SourceFile


class EmptyFrame:
    def __init__(self, columns: tuple[str, ...]) -> None:
        self.columns = list(columns)
        self.dtypes = dict.fromkeys(columns, "object")

    def __len__(self) -> int:
        return 0

    def itertuples(self, *, index: bool, name: None):  # type: ignore[no-untyped-def]
        assert index is False
        assert name is None
        return iter(())


def valid_evidence_bytes(
    *,
    report_dataset_id: str = "lectra-test",
    manifest_dataset_id: str = "lectra-test",
    report_checksum: str = "a" * 32,
    manifest_checksum: str = "a" * 32,
) -> tuple[bytes, bytes]:
    frames = {table: EmptyFrame(columns) for table, columns in REQUIRED_COLUMNS.items()}
    report = audit_frames(
        frames,
        dataset_id=report_dataset_id,
        source_checksums={"tasks.gz": report_checksum},
    )
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
    return report.model_dump_json().encode(), manifest.model_dump_json().encode()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"   \n", "empty"),
        (b'"\xff"', "UTF-8"),
        (b"{} {}", "trailing"),
        (b'{"metric":NaN}', "non-finite"),
        (b'{"outer":{"key":1,"key":2}}', "duplicate JSON object key.*key"),
    ],
)
def test_strict_decoder_rejects_ambiguous_or_nonpassive_json(payload: bytes, message: str) -> None:
    with pytest.raises(PassiveEvidenceError, match=message):
        decode_strict_json_bytes(payload, label="test evidence")


def test_strict_decoder_rejects_payload_over_limit() -> None:
    with pytest.raises(PassiveEvidenceError, match="size limit"):
        decode_strict_json_bytes(
            b"x" * 11,
            label="test evidence",
            max_bytes=10,
        )


def test_strict_decoder_wraps_deep_json_recursion() -> None:
    depth = max(sys.getrecursionlimit() * 20, 20_000)
    payload = ("[" * depth + "0" + "]" * depth).encode()

    with pytest.raises(PassiveEvidenceError, match="nesting depth"):
        decode_strict_json_bytes(payload, label="test evidence")


def test_reader_reads_one_regular_file_descriptor(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    evidence_path = tmp_path / "report.json"
    evidence_path.write_bytes(b'{"safe":true}')
    original_open = passive_report.os.open
    opened_flags: list[int] = []

    def recording_open(path, flags: int):  # type: ignore[no-untyped-def]
        opened_flags.append(flags)
        return original_open(path, flags)

    monkeypatch.setattr(passive_report.os, "open", recording_open)

    assert read_passive_evidence_file(evidence_path, label="test evidence") == b'{"safe":true}'
    assert len(opened_flags) == 1
    assert opened_flags[0] & os.O_NOFOLLOW
    assert opened_flags[0] & os.O_CLOEXEC
    assert opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_ACCMODE == os.O_RDONLY


def test_reader_rejects_oversized_regular_file_before_read(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    evidence_path = tmp_path / "oversized.json"
    with evidence_path.open("wb") as file:
        file.truncate(MAX_PASSIVE_EVIDENCE_BYTES + 1)

    def unexpected_read(*args):  # type: ignore[no-untyped-def]
        raise AssertionError(f"oversized file reached os.read: {args!r}")

    monkeypatch.setattr(passive_report.os, "read", unexpected_read)

    with pytest.raises(PassiveEvidenceError, match="size limit"):
        read_passive_evidence_file(evidence_path, label="test evidence")


def test_reader_rejects_file_that_grows_past_limit_during_read(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    evidence_path = tmp_path / "growing.json"
    evidence_path.write_bytes(b"x" * MAX_PASSIVE_EVIDENCE_BYTES)
    original_read = passive_report.os.read
    first_read = True

    def grow_then_read(file_descriptor: int, size: int) -> bytes:
        nonlocal first_read
        if first_read:
            first_read = False
            with evidence_path.open("ab") as file:
                file.write(b"x")
        return original_read(file_descriptor, size)

    monkeypatch.setattr(passive_report.os, "read", grow_then_read)

    with pytest.raises(PassiveEvidenceError, match="size limit"):
        read_passive_evidence_file(evidence_path, label="test evidence")


def test_reader_rejects_same_size_mutation_during_read(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    evidence_path = tmp_path / "mutating.json"
    evidence_path.write_bytes(b'{"value":1}')
    original_read = passive_report.os.read
    first_read = True

    def mutate_then_read(file_descriptor: int, size: int) -> bytes:
        nonlocal first_read
        if first_read:
            first_read = False
            evidence_path.write_bytes(b'{"value":2}')
        return original_read(file_descriptor, size)

    monkeypatch.setattr(passive_report.os, "read", mutate_then_read)

    with pytest.raises(PassiveEvidenceError, match="changed while being read"):
        read_passive_evidence_file(evidence_path, label="test evidence")


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is unavailable")
@pytest.mark.parametrize("broken", [False, True])
def test_reader_rejects_live_and_broken_symlinks(tmp_path: Path, broken: bool) -> None:
    target = tmp_path / "target.json"
    if not broken:
        target.write_bytes(b"{}")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)

    with pytest.raises(PassiveEvidenceError, match="regular file"):
        read_passive_evidence_file(linked, label="test evidence")


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
    reason="nonblocking FIFOs are unavailable",
)
def test_reader_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "report.fifo"
    os.mkfifo(fifo)

    with pytest.raises(PassiveEvidenceError, match="regular file"):
        read_passive_evidence_file(fifo, label="test evidence")


@pytest.mark.skipif(not Path("/dev/null").exists(), reason="no portable special file available")
def test_reader_rejects_special_file() -> None:
    with pytest.raises(PassiveEvidenceError, match="regular file"):
        read_passive_evidence_file(Path("/dev/null"), label="test evidence")


def test_report_and_manifest_parsers_validate_and_bind() -> None:
    report_bytes, manifest_bytes = valid_evidence_bytes()

    report = parse_lectra_audit_report(report_bytes)
    manifest = parse_dataset_source_manifest(manifest_bytes)

    bind_lectra_audit_report(report, manifest)
    assert report.dataset_id == "lectra-test"


@pytest.mark.parametrize(
    "evidence",
    [
        {"report_dataset_id": "other"},
        {"report_checksum": "b" * 32},
    ],
)
def test_report_binding_rejects_identity_mismatch(evidence: dict[str, str]) -> None:
    report_bytes, manifest_bytes = valid_evidence_bytes(**evidence)
    report = parse_lectra_audit_report(report_bytes)
    manifest = parse_dataset_source_manifest(manifest_bytes)

    with pytest.raises(PassiveEvidenceError, match="mismatch"):
        bind_lectra_audit_report(report, manifest)


def test_load_evidence_uses_safe_reader_and_shared_policy(tmp_path: Path) -> None:
    report_bytes, manifest_bytes = valid_evidence_bytes()
    report_path = tmp_path / "report.json"
    manifest_path = tmp_path / "manifest.json"
    report_path.write_bytes(report_bytes)
    manifest_path.write_bytes(manifest_bytes)

    report, manifest = load_lectra_audit_evidence(report_path, manifest_path)

    assert report.dataset_id == manifest.dataset_id == "lectra-test"


def test_passive_policy_imports_without_pandas_or_pickle() -> None:
    code = """
import sys
import yieldforge.datasets.passive_report
assert "pandas" not in sys.modules
assert "pickle" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def normalized_evidence_bytes() -> tuple[bytes, bytes, bytes]:
    report_bytes, manifest_bytes = valid_evidence_bytes(
        report_dataset_id="lectra-7030786-v1.1",
        manifest_dataset_id="lectra-7030786-v1.1",
        report_checksum="4" * 32,
        manifest_checksum="4" * 32,
    )
    manifest_data = json.loads(manifest_bytes)
    manifest_data.update(
        doi="10.5281/zenodo.7030786",
        license="CC-BY-4.0",
        files=[
            {
                "name": name,
                "url": f"https://example.test/{name}",
                "size_bytes": 1,
                "checksum_algorithm": "md5",
                "checksum": str(index) * 32,
            }
            for index, name in enumerate(
                ("parts.gz", "constraints.gz", "shapes.gz", "tasks.gz"), start=1
            )
        ],
    )
    manifest_bytes = json.dumps(manifest_data, separators=(",", ":")).encode()
    report_data = json.loads(report_bytes)
    report_data["source_checksums"] = {
        name: str(index) * 32
        for index, name in enumerate(
            ("parts.gz", "constraints.gz", "shapes.gz", "tasks.gz"), start=1
        )
    }
    report_data["source_unit_label"] = "m^-4"
    report_bytes = json.dumps(report_data, separators=(",", ":")).encode()
    source = source_identity().model_copy(
        update={
            "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "audit_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        }
    )
    slice_bytes = valid_slice().model_copy(update={"source": source}).model_dump_json().encode()
    return slice_bytes, report_bytes, manifest_bytes


def test_normalized_slice_parser_and_loader_delegate_strict_passive_policy(tmp_path: Path) -> None:
    slice_bytes, _, _ = normalized_evidence_bytes()
    slice_path = tmp_path / "slice.json"
    slice_path.write_bytes(slice_bytes)

    assert parse_normalized_slice(slice_bytes).source.dataset_id == "lectra-7030786-v1.1"
    assert (
        load_unbound_normalized_slice(slice_path).schema_version == "yieldforge.normalized-slice.v1"
    )
    assert not hasattr(passive_report, "load_normalized_slice")

    with pytest.raises(PassiveEvidenceError, match="duplicate JSON object key"):
        parse_normalized_slice(b'{"schema_version":"x","schema_version":"y"}')
    with pytest.raises(PassiveEvidenceError, match="trailing"):
        parse_normalized_slice(slice_bytes + b" {}")


def test_normalized_slice_loader_rejects_links_and_size_via_shared_reader(tmp_path: Path) -> None:
    slice_bytes, _, _ = normalized_evidence_bytes()
    target = tmp_path / "target.json"
    target.write_bytes(slice_bytes)
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)

    with pytest.raises(PassiveEvidenceError, match="regular file"):
        load_unbound_normalized_slice(linked)
    with pytest.raises(PassiveEvidenceError, match="size limit"):
        parse_normalized_slice(slice_bytes, max_bytes=10)


def test_normalized_slice_binding_requires_exact_manifest_and_audit_hashes() -> None:
    slice_bytes, report_bytes, manifest_bytes = normalized_evidence_bytes()
    normalized = parse_normalized_slice(slice_bytes)
    report = parse_lectra_audit_report(report_bytes)
    manifest = parse_dataset_source_manifest(manifest_bytes)

    bind_normalized_slice_evidence(
        normalized,
        report,
        manifest,
        report_payload=report_bytes,
        manifest_payload=manifest_bytes,
    )

    with pytest.raises(PassiveEvidenceError, match="audit report SHA-256 mismatch"):
        bind_normalized_slice_evidence(
            normalized,
            report,
            manifest,
            report_payload=report_bytes + b" ",
            manifest_payload=manifest_bytes,
        )
    with pytest.raises(PassiveEvidenceError, match="source manifest SHA-256 mismatch"):
        bind_normalized_slice_evidence(
            normalized,
            report,
            manifest,
            report_payload=report_bytes,
            manifest_payload=manifest_bytes + b" ",
        )


def test_normalized_binding_parses_payloads_and_matches_the_supplied_models() -> None:
    slice_bytes, report_bytes, manifest_bytes = normalized_evidence_bytes()
    normalized = parse_normalized_slice(slice_bytes)
    report = parse_lectra_audit_report(report_bytes)
    manifest = parse_dataset_source_manifest(manifest_bytes)

    altered_report = report.model_copy(update={"source_unit_label": "invented"})
    with pytest.raises(PassiveEvidenceError, match="audit report payload.*supplied model"):
        bind_normalized_slice_evidence(
            normalized,
            altered_report,
            manifest,
            report_payload=report_bytes,
            manifest_payload=manifest_bytes,
        )

    altered_manifest = manifest.model_copy(update={"source_page": "https://other.test/source"})
    with pytest.raises(PassiveEvidenceError, match="manifest payload.*supplied model"):
        bind_normalized_slice_evidence(
            normalized,
            report,
            altered_manifest,
            report_payload=report_bytes,
            manifest_payload=manifest_bytes,
        )

    with pytest.raises(PassiveEvidenceError, match="Invalid Lectra audit report"):
        bind_normalized_slice_evidence(
            normalized,
            report,
            manifest,
            report_payload=b"{}",
            manifest_payload=manifest_bytes,
        )


def test_load_normalized_slice_evidence_reads_validates_and_binds_all_files(tmp_path: Path) -> None:
    slice_bytes, report_bytes, manifest_bytes = normalized_evidence_bytes()
    slice_path = tmp_path / "slice.json"
    report_path = tmp_path / "audit.json"
    manifest_path = tmp_path / "manifest.json"
    slice_path.write_bytes(slice_bytes)
    report_path.write_bytes(report_bytes)
    manifest_path.write_bytes(manifest_bytes)

    normalized, report, manifest = load_normalized_slice_evidence(
        slice_path,
        report_path,
        manifest_path,
    )

    assert normalized.source.dataset_id == report.dataset_id == manifest.dataset_id
