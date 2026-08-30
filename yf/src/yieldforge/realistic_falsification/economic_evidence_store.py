"""Compact authenticated evidence sidecars for Gate 3 economic resolution."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import zlib
from dataclasses import dataclass
from gzip import GzipFile
from io import BytesIO
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictInt, StrictStr, ValidationError, model_validator

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
    GATE3_BASELINE_POLICY_IDS,
    Gate3BaselinePolicyId,
    Gate3CalibrationObservation,
    Gate3CorpusId,
    Gate3CostLedger,
    Gate3RootBinding,
)

Gate3CalibrationSourceLineage = Literal[
    "legacy_success_output_equivalent",
    "repaired_runtime",
]
_COMPRESSION = "gzip-level-6-mtime-0-no-filename"
_MAX_COMPRESSED_BYTES = 128 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


class Gate3EconomicEvidenceError(ValueError):
    """Compact Gate 3 evidence failed a bounded fail-closed check."""


def deterministic_gzip(data: bytes) -> bytes:
    """Generate repeatable gzip transport bytes with frozen header settings."""

    output = BytesIO()
    with GzipFile(
        filename="",
        mode="wb",
        compresslevel=6,
        fileobj=output,
        mtime=0,
    ) as archive:
        archive.write(data)
    return output.getvalue()


class Gate3CalibrationObservationReceipt(FrozenExperimentModel):
    """Compact content-addressed receipt for one full calibration observation."""

    schema_version: Literal["yieldforge.m11-gate3-calibration-receipt.v1"] = (
        "yieldforge.m11-gate3-calibration-receipt.v1"
    )
    receipt_id: StrictStr = Field(pattern=r"^yfm11g3calrcpt-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1)
    policy_id: Gate3BaselinePolicyId
    observation_id: StrictStr = Field(pattern=r"^yfm11g3calobs-[0-9a-f]{24}$")
    observation_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    final_costs: Gate3CostLedger
    full_sheet_opening_count: StrictInt = Field(ge=0)
    exact_event_census: Literal[True] = True
    source_lineage: Gate3CalibrationSourceLineage
    sidecar_name: StrictStr = Field(
        pattern=r"^m11-gate3-calibration-observation-[0-9a-f]{64}\.json\.gz$"
    )
    compressed_raw_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    compressed_byte_count: StrictInt = Field(gt=0, le=_MAX_COMPRESSED_BYTES)
    uncompressed_byte_count: StrictInt = Field(gt=0, le=_MAX_UNCOMPRESSED_BYTES)
    compression: Literal["gzip-level-6-mtime-0-no-filename"] = _COMPRESSION

    @model_validator(mode="after")
    def require_observation_binding_and_identity(self) -> Self:
        observation_hash = self.observation_content_sha256.removeprefix("sha256:")
        if (
            self.observation_id != f"yfm11g3calobs-{observation_hash[:24]}"
            or self.sidecar_name
            != f"m11-gate3-calibration-observation-{observation_hash}.json.gz"
        ):
            raise ValueError("Gate 3 calibration receipt observation binding differs")
        digest = semantic_sha256(self, excluded_fields={"receipt_id", "content_sha256"})
        if self.receipt_id != f"yfm11g3calrcpt-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 calibration receipt identity differs from semantic content")
        return self


def canonical_gate3_calibration_observation_bytes(
    observation: Gate3CalibrationObservation,
) -> bytes:
    """Return canonical bytes only after detached strict validation."""

    strict = Gate3CalibrationObservation.model_validate(
        observation.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    return _canonical_strict_gate3_calibration_observation_bytes(strict)


def _canonical_strict_gate3_calibration_observation_bytes(
    observation: Gate3CalibrationObservation,
) -> bytes:
    """Serialize an observation already detached and strictly validated."""

    return canonical_pretty_json_bytes(observation)


@dataclass(frozen=True, slots=True)
class _PreparedCalibrationObservationEvidence:
    observation: Gate3CalibrationObservation
    canonical_bytes: bytes
    compressed_bytes: bytes
    receipt: Gate3CalibrationObservationReceipt


def _build_receipt_from_prepared_bytes(
    observation: Gate3CalibrationObservation,
    *,
    canonical_bytes: bytes,
    compressed_bytes: bytes,
    source_lineage: Gate3CalibrationSourceLineage,
) -> Gate3CalibrationObservationReceipt:
    observation_hash = observation.content_sha256.removeprefix("sha256:")
    semantic = {
        "schema_version": "yieldforge.m11-gate3-calibration-receipt.v1",
        "roots": observation.roots.model_dump(mode="json"),
        "corpus_id": observation.corpus_id,
        "stream_id": observation.stream_id,
        "policy_id": observation.policy_id,
        "observation_id": observation.observation_id,
        "observation_content_sha256": observation.content_sha256,
        "final_costs": observation.final_costs.model_dump(mode="json"),
        "full_sheet_opening_count": observation.full_sheet_opening_count,
        "exact_event_census": True,
        "source_lineage": source_lineage,
        "sidecar_name": (
            f"m11-gate3-calibration-observation-{observation_hash}.json.gz"
        ),
        "compressed_raw_sha256": (
            f"sha256:{hashlib.sha256(compressed_bytes).hexdigest()}"
        ),
        "compressed_byte_count": len(compressed_bytes),
        "uncompressed_byte_count": len(canonical_bytes),
        "compression": _COMPRESSION,
    }
    digest = semantic_sha256(semantic)
    return Gate3CalibrationObservationReceipt(
        receipt_id=f"yfm11g3calrcpt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _prepare_calibration_observation_evidence(
    observation: Gate3CalibrationObservation,
    *,
    source_lineage: Gate3CalibrationSourceLineage,
) -> _PreparedCalibrationObservationEvidence:
    strict = Gate3CalibrationObservation.model_validate(
        observation.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    canonical = _canonical_strict_gate3_calibration_observation_bytes(strict)
    if len(canonical) > _MAX_UNCOMPRESSED_BYTES:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar exceeds the uncompressed byte bound"
        )
    compressed = deterministic_gzip(canonical)
    if len(compressed) > _MAX_COMPRESSED_BYTES:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar exceeds the compressed byte bound"
        )
    receipt = _build_receipt_from_prepared_bytes(
        strict,
        canonical_bytes=canonical,
        compressed_bytes=compressed,
        source_lineage=source_lineage,
    )
    return _PreparedCalibrationObservationEvidence(
        observation=strict,
        canonical_bytes=canonical,
        compressed_bytes=compressed,
        receipt=receipt,
    )


def build_gate3_calibration_observation_receipt(
    observation: Gate3CalibrationObservation,
    *,
    source_lineage: Gate3CalibrationSourceLineage,
) -> Gate3CalibrationObservationReceipt:
    """Strictly detach, compress, and summarize one complete observation."""

    return _prepare_calibration_observation_evidence(
        observation,
        source_lineage=source_lineage,
    ).receipt


def _strict_receipt(
    receipt: Gate3CalibrationObservationReceipt,
) -> Gate3CalibrationObservationReceipt:
    try:
        return Gate3CalibrationObservationReceipt.model_validate(
            receipt.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration receipt failed strict validation"
        ) from error


def _read_bounded_regular_file(path: Path) -> bytes:
    candidate = Path(path)
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_COMPRESSED_BYTES
        ):
            raise Gate3EconomicEvidenceError(
                "Gate 3 calibration sidecar must be a bounded regular file"
            )
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise Gate3EconomicEvidenceError(
                    "Gate 3 calibration sidecar must be a bounded regular file"
                )
            chunks: list[bytes] = []
            remaining = _MAX_COMPRESSED_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            during = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = candidate.lstat()
    except Gate3EconomicEvidenceError:
        raise
    except OSError as error:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar could not be read safely"
        ) from error
    before_fingerprint = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if (
        len(raw) > _MAX_COMPRESSED_BYTES
        or len(raw) != before.st_size
        or before_fingerprint
        != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        or before_fingerprint
        != (
            during.st_dev,
            during.st_ino,
            during.st_size,
            during.st_mtime_ns,
            during.st_ctime_ns,
        )
        or before_fingerprint
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    ):
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar changed during read-back"
        )
    return raw


def _bounded_gzip_decompress(data: bytes) -> bytes:
    try:
        with GzipFile(fileobj=BytesIO(data), mode="rb") as archive:
            chunks: list[bytes] = []
            remaining = _MAX_UNCOMPRESSED_BYTES + 1
            while remaining:
                chunk = archive.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
    except (EOFError, OSError, zlib.error) as error:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar is not valid gzip"
        ) from error
    raw = b"".join(chunks)
    if len(raw) > _MAX_UNCOMPRESSED_BYTES:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar exceeds the uncompressed byte bound"
        )
    return raw


def _validate_sidecar_bytes(
    data: bytes,
    *,
    receipt: Gate3CalibrationObservationReceipt,
) -> Gate3CalibrationObservation:
    if type(data) is not bytes:
        raise Gate3EconomicEvidenceError("Gate 3 calibration sidecar requires exact bytes")
    if len(data) > _MAX_COMPRESSED_BYTES:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar exceeds the compressed byte bound"
        )
    if len(data) != receipt.compressed_byte_count:
        raise Gate3EconomicEvidenceError("Gate 3 calibration compressed byte count differs")
    compressed_sha = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if compressed_sha != receipt.compressed_raw_sha256:
        raise Gate3EconomicEvidenceError("Gate 3 calibration compressed raw hash differs")
    raw = _bounded_gzip_decompress(data)
    if len(raw) != receipt.uncompressed_byte_count:
        raise Gate3EconomicEvidenceError("Gate 3 calibration uncompressed byte count differs")
    try:
        observation = Gate3CalibrationObservation.model_validate_json(raw, strict=True)
    except (TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration observation failed strict validation"
        ) from error
    canonical = canonical_gate3_calibration_observation_bytes(observation)
    if raw != canonical:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration observation encoding is not canonical"
        )
    if (
        observation.roots != receipt.roots
        or observation.corpus_id != receipt.corpus_id
        or observation.stream_id != receipt.stream_id
        or observation.policy_id != receipt.policy_id
        or observation.observation_id != receipt.observation_id
        or observation.content_sha256 != receipt.observation_content_sha256
        or observation.final_costs != receipt.final_costs
        or observation.full_sheet_opening_count != receipt.full_sheet_opening_count
        or observation.exact_event_census != receipt.exact_event_census
    ):
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration sidecar differs from its receipt binding"
        )
    return observation


def _strict_expected_binding(
    *,
    expected_roots: Gate3RootBinding,
    expected_corpus_id: Gate3CorpusId,
    expected_stream_id: str,
    expected_policy_id: Gate3BaselinePolicyId,
    expected_observation_id: str,
    expected_observation_content_sha256: str,
    expected_final_costs: Gate3CostLedger,
    expected_full_sheet_opening_count: int,
    expected_source_lineage: Gate3CalibrationSourceLineage,
) -> tuple[
    Gate3RootBinding,
    Gate3CorpusId,
    str,
    Gate3BaselinePolicyId,
    str,
    str,
    Gate3CostLedger,
    int,
    Gate3CalibrationSourceLineage,
]:
    try:
        strict_roots = Gate3RootBinding.model_validate(
            expected_roots.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        strict_ledger = Gate3CostLedger.model_validate(
            expected_final_costs.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration expected binding is malformed"
        ) from error
    if (
        type(expected_corpus_id) is not str
        or expected_corpus_id not in ("lectra-m3-m4", "loco-2dics")
        or type(expected_stream_id) is not str
        or not expected_stream_id
        or type(expected_policy_id) is not str
        or expected_policy_id not in GATE3_BASELINE_POLICY_IDS
        or type(expected_observation_id) is not str
        or re.fullmatch(r"yfm11g3calobs-[0-9a-f]{24}", expected_observation_id) is None
        or type(expected_observation_content_sha256) is not str
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            expected_observation_content_sha256,
        )
        is None
        or type(expected_full_sheet_opening_count) is not int
        or expected_full_sheet_opening_count < 0
        or type(expected_source_lineage) is not str
        or expected_source_lineage
        not in ("legacy_success_output_equivalent", "repaired_runtime")
    ):
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration expected binding is malformed"
        )
    return (
        strict_roots,
        expected_corpus_id,
        expected_stream_id,
        expected_policy_id,
        expected_observation_id,
        expected_observation_content_sha256,
        strict_ledger,
        expected_full_sheet_opening_count,
        expected_source_lineage,
    )


def publish_gate3_calibration_observation_evidence(
    output_directory: Path,
    observation: Gate3CalibrationObservation,
    *,
    source_lineage: Gate3CalibrationSourceLineage,
) -> tuple[Path, Gate3CalibrationObservationReceipt]:
    """Prepare once and publish one immutable calibration evidence sidecar."""

    prepared = _prepare_calibration_observation_evidence(
        observation,
        source_lineage=source_lineage,
    )
    path = Path(output_directory) / prepared.receipt.sidecar_name
    try:
        published = publish_immutable_artifact(
            path,
            prepared.compressed_bytes,
            validate=lambda data: data if data == prepared.compressed_bytes else b"",
            label="M11 Gate 3 calibration observation evidence",
        )
    except M8ArtifactPublicationError as error:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration immutable publication failed"
        ) from error
    return published, prepared.receipt


def load_gate3_calibration_observation_evidence(
    path: Path,
    *,
    receipt: Gate3CalibrationObservationReceipt,
    expected_roots: Gate3RootBinding,
    expected_corpus_id: Gate3CorpusId,
    expected_stream_id: str,
    expected_policy_id: Gate3BaselinePolicyId,
    expected_observation_id: str,
    expected_observation_content_sha256: str,
    expected_final_costs: Gate3CostLedger,
    expected_full_sheet_opening_count: int,
    expected_source_lineage: Gate3CalibrationSourceLineage,
) -> Gate3CalibrationObservation:
    """Load one sidecar only under exact receipt and caller-supplied bindings."""

    strict_receipt = _strict_receipt(receipt)
    expected = _strict_expected_binding(
        expected_roots=expected_roots,
        expected_corpus_id=expected_corpus_id,
        expected_stream_id=expected_stream_id,
        expected_policy_id=expected_policy_id,
        expected_observation_id=expected_observation_id,
        expected_observation_content_sha256=expected_observation_content_sha256,
        expected_final_costs=expected_final_costs,
        expected_full_sheet_opening_count=expected_full_sheet_opening_count,
        expected_source_lineage=expected_source_lineage,
    )
    (
        strict_roots,
        strict_corpus_id,
        strict_stream_id,
        strict_policy_id,
        strict_observation_id,
        strict_observation_content_sha256,
        strict_ledger,
        strict_opening_count,
        strict_source_lineage,
    ) = expected
    if strict_receipt.source_lineage != strict_source_lineage:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration receipt differs from the expected source lineage"
        )
    if strict_receipt.final_costs != strict_ledger:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration receipt differs from the expected ledger"
        )
    if strict_receipt.full_sheet_opening_count != strict_opening_count:
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration receipt differs from the expected opening count"
        )
    if (
        strict_receipt.roots != strict_roots
        or strict_receipt.corpus_id != strict_corpus_id
        or strict_receipt.stream_id != strict_stream_id
        or strict_receipt.policy_id != strict_policy_id
        or strict_receipt.observation_id != strict_observation_id
        or strict_receipt.observation_content_sha256
        != strict_observation_content_sha256
    ):
        raise Gate3EconomicEvidenceError(
            "Gate 3 calibration receipt differs from the expected binding"
        )
    candidate = Path(path)
    if candidate.name != strict_receipt.sidecar_name:
        raise Gate3EconomicEvidenceError("Gate 3 calibration sidecar filename differs")
    raw = _read_bounded_regular_file(candidate)
    observation = _validate_sidecar_bytes(raw, receipt=strict_receipt)
    return observation
