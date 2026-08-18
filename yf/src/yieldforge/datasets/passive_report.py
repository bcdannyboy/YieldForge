"""Import-safe policy for reading and validating passive dataset evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from yieldforge.datasets.lectra_audit import LectraAuditReport
from yieldforge.datasets.normalized_slice import NormalizedSlice
from yieldforge.datasets.source_manifest import DatasetSourceManifest

MAX_PASSIVE_EVIDENCE_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class PassiveEvidenceError(ValueError):
    """Passive evidence was unsafe, ambiguous, invalid, or source-mismatched."""


class _DuplicateJsonKeyError(ValueError):
    """A JSON object repeated a key at any nesting level."""


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def decode_strict_json_bytes(
    payload: bytes,
    *,
    label: str,
    max_bytes: int = MAX_PASSIVE_EVIDENCE_BYTES,
) -> object:
    """Decode one finite, duplicate-free JSON value within a hard byte limit."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if len(payload) > max_bytes:
        raise PassiveEvidenceError(f"Invalid {label}: payload violates the size limit")
    if not payload:
        raise PassiveEvidenceError(f"Invalid {label}: payload is empty")
    try:
        serialized = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PassiveEvidenceError(f"Invalid {label}: payload is not UTF-8") from error
    if not serialized.strip():
        raise PassiveEvidenceError(f"Invalid {label}: payload is empty")

    try:
        return json.loads(
            serialized,
            parse_constant=_reject_nonfinite_json,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except _DuplicateJsonKeyError as error:
        raise PassiveEvidenceError(f"Invalid {label}: {error}") from error
    except json.JSONDecodeError as error:
        if error.msg == "Extra data":
            detail = "trailing JSON data after the first value"
        else:
            detail = f"malformed JSON: {error.msg}"
        raise PassiveEvidenceError(f"Invalid {label}: {detail}") from error
    except RecursionError as error:
        raise PassiveEvidenceError(
            f"Invalid {label}: JSON nesting depth exceeds the decoder limit"
        ) from error
    except ValueError as error:
        raise PassiveEvidenceError(f"Invalid {label}: {error}") from error


def _file_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_nlink,
    )


def read_passive_evidence_file(
    path: Path,
    *,
    label: str,
    max_bytes: int = MAX_PASSIVE_EVIDENCE_BYTES,
) -> bytes:
    """Read one stable regular file by a single non-following descriptor."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        file_descriptor = os.open(os.fspath(path), flags)
    except OSError as error:
        raise PassiveEvidenceError(
            f"Invalid {label}: path must be one readable regular file, not a link: {path}"
        ) from error

    try:
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PassiveEvidenceError(
                f"Invalid {label}: path must be one readable regular file: {path}"
            )
        if before.st_size > max_bytes:
            raise PassiveEvidenceError(f"Invalid {label}: file violates the size limit")

        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            try:
                chunk = os.read(
                    file_descriptor,
                    min(_READ_CHUNK_BYTES, max_bytes + 1 - total),
                )
            except BlockingIOError as error:
                raise PassiveEvidenceError(
                    f"Invalid {label}: regular file could not be read without blocking"
                ) from error
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            raise PassiveEvidenceError(f"Invalid {label}: file violates the size limit")

        after = os.fstat(file_descriptor)
        if _file_fingerprint(after) != _file_fingerprint(before) or total != before.st_size:
            raise PassiveEvidenceError(f"Invalid {label}: file changed while being read")
        return b"".join(chunks)
    except OSError as error:
        raise PassiveEvidenceError(f"Invalid {label}: regular file read failed") from error
    finally:
        os.close(file_descriptor)


def parse_lectra_audit_report(
    payload: bytes,
    *,
    max_bytes: int = MAX_PASSIVE_EVIDENCE_BYTES,
) -> LectraAuditReport:
    """Parse strict passive JSON into the complete Lectra audit contract."""
    decoded = decode_strict_json_bytes(
        payload,
        label="Lectra audit report",
        max_bytes=max_bytes,
    )
    try:
        return LectraAuditReport.model_validate(decoded)
    except ValidationError as error:
        raise PassiveEvidenceError(f"Invalid Lectra audit report: {error}") from error


def parse_dataset_source_manifest(
    payload: bytes,
    *,
    max_bytes: int = MAX_PASSIVE_EVIDENCE_BYTES,
) -> DatasetSourceManifest:
    """Parse strict passive JSON into the pinned source-manifest contract."""
    decoded = decode_strict_json_bytes(
        payload,
        label="dataset source manifest",
        max_bytes=max_bytes,
    )
    try:
        return DatasetSourceManifest.model_validate(decoded)
    except ValidationError as error:
        raise PassiveEvidenceError(f"Invalid dataset source manifest: {error}") from error


def parse_normalized_slice(
    payload: bytes,
    *,
    max_bytes: int = MAX_PASSIVE_EVIDENCE_BYTES,
) -> NormalizedSlice:
    """Parse strict passive JSON into the normalized Lectra slice contract."""
    decoded = decode_strict_json_bytes(
        payload,
        label="normalized Lectra slice",
        max_bytes=max_bytes,
    )
    try:
        return NormalizedSlice.model_validate(decoded)
    except ValidationError as error:
        raise PassiveEvidenceError(f"Invalid normalized Lectra slice: {error}") from error


def bind_lectra_audit_report(
    report: LectraAuditReport,
    manifest: DatasetSourceManifest,
) -> None:
    """Require the report to identify exactly the pinned manifest release and files."""
    if report.dataset_id != manifest.dataset_id:
        raise PassiveEvidenceError(
            "Lectra audit dataset identity mismatch: "
            f"report={report.dataset_id!r}, manifest={manifest.dataset_id!r}"
        )
    expected_checksums = {source.name: source.checksum for source in manifest.files}
    if report.source_checksums != expected_checksums:
        raise PassiveEvidenceError(
            "Lectra audit source checksum mismatch: "
            f"report={report.source_checksums!r}, manifest={expected_checksums!r}"
        )


def bind_normalized_slice_evidence(
    normalized: NormalizedSlice,
    report: LectraAuditReport,
    manifest: DatasetSourceManifest,
    *,
    report_payload: bytes,
    manifest_payload: bytes,
) -> None:
    """Bind a slice to the exact parsed manifest and audit-report byte streams."""
    parsed_report = parse_lectra_audit_report(report_payload)
    if parsed_report != report:
        raise PassiveEvidenceError(
            "Normalized slice audit report payload does not match the supplied model"
        )
    parsed_manifest = parse_dataset_source_manifest(manifest_payload)
    if parsed_manifest != manifest:
        raise PassiveEvidenceError(
            "Normalized slice source manifest payload does not match the supplied model"
        )
    bind_lectra_audit_report(report, manifest)
    source = normalized.source
    if source.dataset_id != manifest.dataset_id:
        raise PassiveEvidenceError(
            "Normalized slice dataset identity mismatch: "
            f"slice={source.dataset_id!r}, manifest={manifest.dataset_id!r}"
        )
    if source.doi != manifest.doi:
        raise PassiveEvidenceError(
            f"Normalized slice DOI mismatch: slice={source.doi!r}, manifest={manifest.doi!r}"
        )
    if source.license != manifest.license:
        raise PassiveEvidenceError(
            "Normalized slice license mismatch: "
            f"slice={source.license!r}, manifest={manifest.license!r}"
        )

    expected_checksums = tuple(
        (item.name, item.checksum_algorithm, item.checksum) for item in manifest.files
    )
    observed_checksums = tuple(
        (item.name, item.checksum_algorithm, item.checksum) for item in source.source_checksums
    )
    if observed_checksums != expected_checksums:
        raise PassiveEvidenceError(
            "Normalized slice source checksum mismatch: "
            f"slice={observed_checksums!r}, manifest={expected_checksums!r}"
        )
    if source.source_unit.literal_label != report.source_unit_label:
        raise PassiveEvidenceError(
            "Normalized slice source unit mismatch: "
            f"slice={source.source_unit.literal_label!r}, report={report.source_unit_label!r}"
        )
    if source.source_unit.interpretation is not None:
        raise PassiveEvidenceError("Normalized slice source unit interpretation must remain null")

    expected_manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    if not hmac.compare_digest(source.source_manifest_sha256, expected_manifest_sha256):
        raise PassiveEvidenceError(
            "Normalized slice source manifest SHA-256 mismatch: "
            f"slice={source.source_manifest_sha256}, actual={expected_manifest_sha256}"
        )
    expected_report_sha256 = hashlib.sha256(report_payload).hexdigest()
    if not hmac.compare_digest(source.audit_report_sha256, expected_report_sha256):
        raise PassiveEvidenceError(
            "Normalized slice audit report SHA-256 mismatch: "
            f"slice={source.audit_report_sha256}, actual={expected_report_sha256}"
        )


def load_lectra_audit_evidence(
    report_path: Path,
    manifest_path: Path,
) -> tuple[LectraAuditReport, DatasetSourceManifest]:
    """Safely load, validate, and bind one report-manifest evidence pair."""
    report = parse_lectra_audit_report(
        read_passive_evidence_file(report_path, label="Lectra audit report")
    )
    manifest = parse_dataset_source_manifest(
        read_passive_evidence_file(manifest_path, label="dataset source manifest")
    )
    bind_lectra_audit_report(report, manifest)
    return report, manifest


def load_normalized_slice(path: Path) -> NormalizedSlice:
    """Safely read and validate one passive normalized-slice file."""
    return parse_normalized_slice(read_passive_evidence_file(path, label="normalized Lectra slice"))


def load_normalized_slice_evidence(
    slice_path: Path,
    report_path: Path,
    manifest_path: Path,
) -> tuple[NormalizedSlice, LectraAuditReport, DatasetSourceManifest]:
    """Safely read, validate, and bind a slice and both of its evidence files."""
    slice_payload = read_passive_evidence_file(slice_path, label="normalized Lectra slice")
    report_payload = read_passive_evidence_file(report_path, label="Lectra audit report")
    manifest_payload = read_passive_evidence_file(manifest_path, label="dataset source manifest")

    normalized = parse_normalized_slice(slice_payload)
    report = parse_lectra_audit_report(report_payload)
    manifest = parse_dataset_source_manifest(manifest_payload)
    bind_normalized_slice_evidence(
        normalized,
        report,
        manifest,
        report_payload=report_payload,
        manifest_payload=manifest_payload,
    )
    return normalized, report, manifest
