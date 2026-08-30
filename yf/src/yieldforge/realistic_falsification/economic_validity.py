"""Compact terminal state for the M11 economic-validity stage."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictStr, ValidationError, model_validator

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.realistic_falsification.confirmation import (
    Gate3BaselineCalibrationFreeze,
    Gate3RootBinding,
)
from yieldforge.realistic_falsification.economic_resolution import (
    EconomicResolutionProtocol,
    Gate3CalibrationManifest,
    build_economic_resolution_protocol,
)
from yieldforge.realistic_falsification.validity_evidence_store import (
    Gate3ValidityBaselineFreezeBinding,
    Gate3ValidityEvidenceReceipt,
)

_CORPUS_ORDER = ("lectra-m3-m4", "loco-2dics")
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_VALIDITY_SIDECAR_BYTES = 32 * 1024 * 1024
_MAX_DISCOVERY_DIRECTORY_ENTRIES = 4096
_DISCOVERY_DIRECTORY_SCAN_SECONDS = 2.0
_MANIFEST_PREFIX = "m11-economic-validity-stage-"
_VALIDITY_SIDECAR_PREFIX = "m11-gate3-validity-receipt-"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class Gate3ValidityStageEvidenceError(ValueError):
    """A validity-stage artifact failed a bounded fail-closed check."""


class Gate3ValidityStageManifest(FrozenExperimentModel):
    """Complete compact binding from calibration to one validity result."""

    schema_version: Literal["yieldforge.m11-economic-validity-stage.v1"] = (
        "yieldforge.m11-economic-validity-stage.v1"
    )
    manifest_id: StrictStr = Field(pattern=r"^yfm11econvalman-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protocol: EconomicResolutionProtocol
    roots: Gate3RootBinding
    calibration_manifest_id: StrictStr = Field(pattern=r"^yfm11econcalman-[0-9a-f]{24}$")
    calibration_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calibration_status: Literal["complete_valid"] = "complete_valid"
    calibration_success_count: Literal[96] = 96
    calibration_failure_count: Literal[0] = 0
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ]
    validity_evidence: Gate3ValidityEvidenceReceipt
    status: Literal["valid", "diagnosis_required", "invalid"]
    central_authorized: StrictBool
    complete: Literal[True] = True

    @model_validator(mode="after")
    def require_exact_bindings_and_decision(self) -> Self:
        if self.protocol != build_economic_resolution_protocol():
            raise ValueError("validity-stage protocol binding differs")
        if (
            self.roots.content_sha256 != self.protocol.legacy_root_content_sha256
            or tuple(item.corpus_id for item in self.baseline_freezes) != _CORPUS_ORDER
            or any(item.roots != self.roots for item in self.baseline_freezes)
        ):
            raise ValueError("validity-stage root or baseline-freeze binding differs")
        expected_freeze_bindings = tuple(
            Gate3ValidityBaselineFreezeBinding(
                corpus_id=item.corpus_id,
                freeze_id=item.freeze_id,
                freeze_content_sha256=item.content_sha256,
                selected_policy_id=item.selected_policy_id,
            )
            for item in self.baseline_freezes
        )
        if (
            self.validity_evidence.roots != self.roots
            or self.validity_evidence.baseline_freezes != expected_freeze_bindings
            or self.status != self.validity_evidence.status
            or self.central_authorized is not (self.status == "valid")
        ):
            raise ValueError("validity-stage receipt or decision binding differs")
        digest = semantic_sha256(self, excluded_fields={"manifest_id", "content_sha256"})
        if self.manifest_id != f"yfm11econvalman-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("validity-stage manifest identity differs from content")
        return self


def _strict_calibration_manifest(
    calibration_manifest: Gate3CalibrationManifest,
) -> Gate3CalibrationManifest:
    try:
        strict = Gate3CalibrationManifest.model_validate(
            calibration_manifest.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityStageEvidenceError(
            "validity stage calibration manifest failed strict validation"
        ) from error
    if (
        strict.protocol != build_economic_resolution_protocol()
        or strict.status != "complete_valid"
        or strict.success_count != 96
        or strict.failure_count != 0
        or len(strict.checkpoints) != 96
        or len(strict.baseline_freezes) != 2
        or not strict.complete
    ):
        raise Gate3ValidityStageEvidenceError(
            "validity stage requires one exact complete-valid calibration manifest"
        )
    return strict


def _strict_validity_evidence(
    validity_evidence: Gate3ValidityEvidenceReceipt,
) -> Gate3ValidityEvidenceReceipt:
    try:
        return Gate3ValidityEvidenceReceipt.model_validate(
            validity_evidence.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityStageEvidenceError(
            "validity stage evidence receipt failed strict validation"
        ) from error


def build_gate3_validity_stage_manifest(
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_evidence: Gate3ValidityEvidenceReceipt,
) -> Gate3ValidityStageManifest:
    """Bind one successful validity execution to exact complete calibration."""

    calibration = _strict_calibration_manifest(calibration_manifest)
    evidence = _strict_validity_evidence(validity_evidence)
    semantic = {
        "schema_version": "yieldforge.m11-economic-validity-stage.v1",
        "protocol": calibration.protocol.model_dump(mode="json"),
        "roots": calibration.roots.model_dump(mode="json"),
        "calibration_manifest_id": calibration.manifest_id,
        "calibration_manifest_content_sha256": calibration.content_sha256,
        "calibration_status": calibration.status,
        "calibration_success_count": calibration.success_count,
        "calibration_failure_count": calibration.failure_count,
        "baseline_freezes": [item.model_dump(mode="json") for item in calibration.baseline_freezes],
        "validity_evidence": evidence.model_dump(mode="json"),
        "status": evidence.status,
        "central_authorized": evidence.status == "valid",
        "complete": True,
    }
    digest = semantic_sha256(semantic)
    try:
        return Gate3ValidityStageManifest(
            manifest_id=f"yfm11econvalman-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            protocol=calibration.protocol,
            roots=calibration.roots,
            calibration_manifest_id=calibration.manifest_id,
            calibration_manifest_content_sha256=calibration.content_sha256,
            calibration_status="complete_valid",
            calibration_success_count=96,
            calibration_failure_count=0,
            baseline_freezes=calibration.baseline_freezes,
            validity_evidence=evidence,
            status=evidence.status,
            central_authorized=evidence.status == "valid",
            complete=True,
        )
    except (ValidationError, ValueError) as error:
        raise Gate3ValidityStageEvidenceError(str(error)) from error


@dataclass(frozen=True, slots=True)
class _FileFingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _fingerprint(metadata: os.stat_result) -> _FileFingerprint:
    return _FileFingerprint(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_bounded_regular_file(path: Path, *, label: str) -> bytes:
    candidate = Path(path)
    descriptor: int | None = None
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_MANIFEST_BYTES
        ):
            raise Gate3ValidityStageEvidenceError(
                f"{label} must be a bounded regular non-symlink file"
            )
        before_fingerprint = _fingerprint(before)
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _fingerprint(opened) != before_fingerprint:
            raise Gate3ValidityStageEvidenceError(f"{label} identity changed during open")
        chunks: list[bytes] = []
        remaining = _MAX_MANIFEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        during = os.fstat(descriptor)
    except Gate3ValidityStageEvidenceError:
        raise
    except OSError as error:
        raise Gate3ValidityStageEvidenceError(f"{label} could not be read safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = candidate.lstat()
    except OSError as error:
        raise Gate3ValidityStageEvidenceError(f"{label} could not be re-inspected") from error
    if (
        len(raw) > _MAX_MANIFEST_BYTES
        or len(raw) != before.st_size
        or _fingerprint(during) != before_fingerprint
        or _fingerprint(after) != before_fingerprint
    ):
        raise Gate3ValidityStageEvidenceError(f"{label} changed during read")
    return raw


def _reject_duplicate_json_keys(pairs):  # type: ignore[no-untyped-def]
    output = {}
    for key, value in pairs:
        if key in output:
            raise Gate3ValidityStageEvidenceError(
                "validity-stage manifest JSON contains a duplicate object key"
            )
        output[key] = value
    return output


def _reject_nonfinite_json_constant(value: str) -> None:
    raise Gate3ValidityStageEvidenceError(
        f"validity-stage manifest JSON contains non-finite value {value}"
    )


def _parse_canonical_manifest(raw: bytes) -> Gate3ValidityStageManifest:
    if type(raw) is not bytes or len(raw) > _MAX_MANIFEST_BYTES:
        raise Gate3ValidityStageEvidenceError("validity-stage manifest exceeds byte bound")
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        manifest = Gate3ValidityStageManifest.model_validate_json(raw, strict=True)
    except Gate3ValidityStageEvidenceError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError) as error:
        raise Gate3ValidityStageEvidenceError(
            "validity-stage manifest failed strict validation"
        ) from error
    if canonical_pretty_json_bytes(manifest) != raw:
        raise Gate3ValidityStageEvidenceError("validity-stage manifest encoding is not canonical")
    return manifest


def _strict_expected_stage_bindings(
    *,
    expected_protocol: EconomicResolutionProtocol,
    expected_roots: Gate3RootBinding,
    expected_calibration_manifest: Gate3CalibrationManifest,
    expected_manifest_id: str,
    expected_content_sha256: str,
) -> tuple[EconomicResolutionProtocol, Gate3RootBinding, Gate3CalibrationManifest]:
    calibration = _strict_calibration_manifest(expected_calibration_manifest)
    try:
        protocol = EconomicResolutionProtocol.model_validate(
            expected_protocol.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        roots = Gate3RootBinding.model_validate(
            expected_roots.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityStageEvidenceError(
            "validity-stage expected protocol or roots are malformed"
        ) from error
    if (
        protocol != build_economic_resolution_protocol()
        or calibration.protocol != protocol
        or calibration.roots != roots
        or type(expected_manifest_id) is not str
        or re.fullmatch(r"yfm11econvalman-[0-9a-f]{24}", expected_manifest_id) is None
        or type(expected_content_sha256) is not str
        or re.fullmatch(_SHA256_PATTERN, expected_content_sha256) is None
    ):
        raise Gate3ValidityStageEvidenceError(
            "validity-stage expected bindings differ from complete calibration"
        )
    return protocol, roots, calibration


def _manifest_filename(manifest: Gate3ValidityStageManifest) -> str:
    return f"{_MANIFEST_PREFIX}{manifest.content_sha256.removeprefix('sha256:')}.json"


def publish_gate3_validity_stage_manifest(
    output_directory: Path,
    manifest: Gate3ValidityStageManifest,
    *,
    calibration_manifest: Gate3CalibrationManifest,
) -> Path:
    """Publish one canonical validity-stage manifest immutably."""

    try:
        strict = Gate3ValidityStageManifest.model_validate(
            manifest.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityStageEvidenceError(
            "validity-stage manifest failed strict validation"
        ) from error
    calibration = _strict_calibration_manifest(calibration_manifest)
    if (
        strict.protocol != calibration.protocol
        or strict.roots != calibration.roots
        or strict.calibration_manifest_id != calibration.manifest_id
        or strict.calibration_manifest_content_sha256 != calibration.content_sha256
        or strict.baseline_freezes != calibration.baseline_freezes
    ):
        raise Gate3ValidityStageEvidenceError(
            "validity-stage manifest differs from the exact calibration manifest"
        )
    existing = _discover_single_prefixed_regular_file(
        output_directory,
        prefix=_MANIFEST_PREFIX,
        filename_pattern=re.compile(rf"{re.escape(_MANIFEST_PREFIX)}([0-9a-f]{{64}})\.json"),
        max_bytes=_MAX_MANIFEST_BYTES,
        label="validity-stage manifest publication",
    )
    if existing is not None:
        existing_manifest = _parse_canonical_manifest(
            _read_bounded_regular_file(existing.path, label="validity-stage manifest")
        )
        _require_discovery_directory_unchanged(
            existing,
            label="validity-stage manifest publication",
        )
        if existing_manifest != strict:
            raise Gate3ValidityStageEvidenceError(
                "validity-stage publication found a competing manifest"
            )
        return existing.path
    raw = canonical_pretty_json_bytes(strict)
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise Gate3ValidityStageEvidenceError("validity-stage manifest exceeds byte bound")
    destination = Path(output_directory) / _manifest_filename(strict)
    try:
        return publish_immutable_artifact(
            destination,
            raw,
            validate=lambda data: canonical_pretty_json_bytes(_parse_canonical_manifest(data)),
            label="M11 economic validity-stage manifest",
        )
    except M8ArtifactPublicationError as error:
        raise Gate3ValidityStageEvidenceError(
            "validity-stage manifest immutable publication failed"
        ) from error


@dataclass(frozen=True, slots=True)
class _PrefixedFileDiscovery:
    path: Path
    filename_match: re.Match[str]
    directory: Path
    directory_fingerprint: _FileFingerprint


def _require_discovery_directory_unchanged(
    discovery: _PrefixedFileDiscovery,
    *,
    label: str,
) -> None:
    try:
        metadata = discovery.directory.lstat()
    except OSError as error:
        raise Gate3ValidityStageEvidenceError(
            f"{label} directory could not be re-inspected"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or _fingerprint(metadata) != discovery.directory_fingerprint
    ):
        raise Gate3ValidityStageEvidenceError(f"{label} directory changed during discovery")


def _discover_single_prefixed_regular_file(
    output_directory: Path,
    *,
    prefix: str,
    filename_pattern: re.Pattern[str],
    max_bytes: int,
    label: str,
) -> _PrefixedFileDiscovery | None:
    directory = Path(output_directory)
    descriptor: int | None = None
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Gate3ValidityStageEvidenceError(
            f"{label} directory could not be inspected"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise Gate3ValidityStageEvidenceError(f"{label} directory must be a non-symlink directory")
    before_fingerprint = _fingerprint(metadata)
    candidate: tuple[Path, re.Match[str]] | None = None
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _fingerprint(opened) != before_fingerprint:
            raise Gate3ValidityStageEvidenceError(f"{label} directory identity changed during open")
        deadline = time.monotonic() + _DISCOVERY_DIRECTORY_SCAN_SECONDS
        entry_count = 0
        with os.scandir(descriptor) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > _MAX_DISCOVERY_DIRECTORY_ENTRIES:
                    raise Gate3ValidityStageEvidenceError(
                        f"{label} exceeded the directory entry bound"
                    )
                if time.monotonic() > deadline:
                    raise Gate3ValidityStageEvidenceError(
                        f"{label} exceeded the directory scan deadline"
                    )
                if not entry.name.startswith(prefix):
                    continue
                match = filename_pattern.fullmatch(entry.name)
                if match is None:
                    raise Gate3ValidityStageEvidenceError(f"{label} has a malformed prefixed entry")
                entry_metadata = entry.stat(follow_symlinks=False)
                if (
                    stat.S_ISLNK(entry_metadata.st_mode)
                    or not stat.S_ISREG(entry_metadata.st_mode)
                    or entry_metadata.st_size > max_bytes
                ):
                    raise Gate3ValidityStageEvidenceError(
                        f"{label} candidate must be a bounded regular non-symlink file"
                    )
                if candidate is not None:
                    raise Gate3ValidityStageEvidenceError(
                        f"{label} has competing prefixed candidates"
                    )
                candidate = directory / entry.name, match
        if time.monotonic() > deadline:
            raise Gate3ValidityStageEvidenceError(f"{label} exceeded the directory scan deadline")
        during = os.fstat(descriptor)
    except Gate3ValidityStageEvidenceError:
        raise
    except (OSError, OverflowError) as error:
        raise Gate3ValidityStageEvidenceError(f"{label} directory scan failed safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = directory.lstat()
    except OSError as error:
        raise Gate3ValidityStageEvidenceError(
            f"{label} directory could not be re-inspected"
        ) from error
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISDIR(after.st_mode)
        or _fingerprint(during) != before_fingerprint
        or _fingerprint(after) != before_fingerprint
    ):
        raise Gate3ValidityStageEvidenceError(f"{label} directory changed during scan")
    if candidate is None:
        return None
    path, filename_match = candidate
    return _PrefixedFileDiscovery(
        path=path,
        filename_match=filename_match,
        directory=directory,
        directory_fingerprint=before_fingerprint,
    )


def load_gate3_validity_stage_manifest(
    path: Path,
    *,
    expected_protocol: EconomicResolutionProtocol,
    expected_roots: Gate3RootBinding,
    expected_calibration_manifest: Gate3CalibrationManifest,
    expected_manifest_id: str,
    expected_content_sha256: str,
) -> Gate3ValidityStageManifest:
    """Load a canonical stage manifest only under exact caller bindings."""

    protocol, roots, calibration = _strict_expected_stage_bindings(
        expected_protocol=expected_protocol,
        expected_roots=expected_roots,
        expected_calibration_manifest=expected_calibration_manifest,
        expected_manifest_id=expected_manifest_id,
        expected_content_sha256=expected_content_sha256,
    )
    candidate = Path(path)
    expected_name = f"{_MANIFEST_PREFIX}{expected_content_sha256.removeprefix('sha256:')}.json"
    if candidate.name != expected_name:
        raise Gate3ValidityStageEvidenceError(
            "validity-stage manifest path differs from expected binding"
        )
    manifest = _parse_canonical_manifest(
        _read_bounded_regular_file(candidate, label="validity-stage manifest")
    )
    if (
        manifest.protocol,
        manifest.roots,
        manifest.calibration_manifest_id,
        manifest.calibration_manifest_content_sha256,
        manifest.calibration_status,
        manifest.calibration_success_count,
        manifest.calibration_failure_count,
        manifest.baseline_freezes,
        manifest.manifest_id,
        manifest.content_sha256,
    ) != (
        protocol,
        roots,
        calibration.manifest_id,
        calibration.content_sha256,
        "complete_valid",
        96,
        0,
        calibration.baseline_freezes,
        expected_manifest_id,
        expected_content_sha256,
    ):
        raise Gate3ValidityStageEvidenceError(
            "validity-stage manifest differs from expected calibration binding"
        )
    return manifest


def discover_gate3_validity_stage_manifest(
    output_directory: Path,
    *,
    protocol: EconomicResolutionProtocol,
    roots: Gate3RootBinding,
    calibration_manifest: Gate3CalibrationManifest,
) -> tuple[Path, Gate3ValidityStageManifest] | None:
    """Discover the sole stage manifest, failing closed on ambiguity."""

    calibration = _strict_calibration_manifest(calibration_manifest)
    try:
        strict_protocol = EconomicResolutionProtocol.model_validate(
            protocol.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        strict_roots = Gate3RootBinding.model_validate(
            roots.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityStageEvidenceError(
            "validity-stage discovery bindings are malformed"
        ) from error
    if (
        strict_protocol != build_economic_resolution_protocol()
        or calibration.protocol != strict_protocol
        or calibration.roots != strict_roots
    ):
        raise Gate3ValidityStageEvidenceError(
            "validity-stage discovery differs from complete calibration"
        )
    discovered = _discover_single_prefixed_regular_file(
        output_directory,
        prefix=_MANIFEST_PREFIX,
        filename_pattern=re.compile(rf"{re.escape(_MANIFEST_PREFIX)}([0-9a-f]{{64}})\.json"),
        max_bytes=_MAX_MANIFEST_BYTES,
        label="validity-stage manifest discovery",
    )
    if discovered is None:
        return None
    digest = discovered.filename_match.group(1)
    manifest = load_gate3_validity_stage_manifest(
        discovered.path,
        expected_protocol=strict_protocol,
        expected_roots=strict_roots,
        expected_calibration_manifest=calibration,
        expected_manifest_id=f"yfm11econvalman-{digest[:24]}",
        expected_content_sha256=f"sha256:{digest}",
    )
    _require_discovery_directory_unchanged(
        discovered,
        label="validity-stage manifest discovery",
    )
    return discovered.path, manifest


def require_gate3_validity_sidecar_census(
    output_directory: Path,
    *,
    expected_evidence: Gate3ValidityEvidenceReceipt | None,
) -> Path | None:
    """Require zero unbound sidecars or one exact manifest-bound sidecar."""

    evidence = None if expected_evidence is None else _strict_validity_evidence(expected_evidence)
    discovered = _discover_single_prefixed_regular_file(
        output_directory,
        prefix=_VALIDITY_SIDECAR_PREFIX,
        filename_pattern=re.compile(
            rf"{re.escape(_VALIDITY_SIDECAR_PREFIX)}"
            r"([0-9a-f]{64})-([0-9a-f]{64})\.json\.gz"
        ),
        max_bytes=_MAX_VALIDITY_SIDECAR_BYTES,
        label="validity sidecar census",
    )
    if discovered is None:
        if evidence is None:
            return None
        raise Gate3ValidityStageEvidenceError(
            "validity sidecar census is missing its expected receipt"
        )
    if evidence is None or discovered.path.name != evidence.sidecar_name:
        raise Gate3ValidityStageEvidenceError(
            "validity sidecar census has an unbound or competing candidate"
        )
    _require_discovery_directory_unchanged(
        discovered,
        label="validity sidecar census",
    )
    return discovered.path
