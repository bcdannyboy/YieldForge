"""Repair-lineage calibration evidence for M11 economic resolution.

This module deliberately starts a new, outcome-blind protocol.  It never amends
or rescues the preserved failed M11 Gate 3 result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictInt, StrictStr, ValidationError, model_validator

from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256
from yieldforge.realistic_falsification.confirmation import (
    GATE3_BASELINE_POLICY_IDS,
    Gate3BaselinePolicyId,
    Gate3CalibrationAttempt,
    Gate3CorpusId,
    Gate3CostLedger,
    Gate3RootBinding,
)

REPAIR_COMMIT_SHA = "3e7bcb1c587d950134639f6836341ef3d8f7d99e"
REPAIRED_SOURCE_RAW_SHA256 = (
    "sha256:2b0ffd70c079d3526277ea554e82f47604d5f28e5171fd3dd1a8ddc8651b610c"
)
LEGACY_GATE3_ARTIFACT_BYTE_COUNT = 2_270_455_752
LEGACY_GATE3_ARTIFACT_RAW_SHA256 = (
    "sha256:e5757919ddd9251bf374d1664be25faf175963e78478b223ea0d7e22f7439199"
)
LEGACY_GATE3_RUN_ID = "yfm11g3run-3dd87efab6f64ada4c5bd09c"
LEGACY_GATE3_RUN_CONTENT_SHA256 = (
    "sha256:3dd87efab6f64ada4c5bd09c0580a1696017b3115ccdce3ce041b4221c89a89f"
)
LEGACY_GATE3_ROOT_CONTENT_SHA256 = (
    "sha256:2a1a69bc188743bc5cca90a37b4655aee29ebd05f07eb588c8e0189bab5994e2"
)
ACCOUNTING_ARITHMETIC_SEMANTIC = (
    "placed_plus_process_loss_plus_retained_plus_scrap_left_to_right"
)
_MAX_ATTEMPT_OBJECT_BYTES = 64 * 1024 * 1024
_DEFAULT_SCAN_CHUNK_BYTES = 1024 * 1024
_CALIBRATION_ARRAY_MARKER = b'"calibration_attempts": ['


class EconomicResolutionEvidenceError(ValueError):
    """Economic-resolution evidence failed a bounded, fail-closed check."""


class EconomicResolutionProtocol(FrozenExperimentModel):
    """Outcome-blind constants governing the post-repair economic test."""

    schema_version: Literal["yieldforge.m11-economic-resolution-protocol.v1"] = (
        "yieldforge.m11-economic-resolution-protocol.v1"
    )
    protocol_id: StrictStr = Field(pattern=r"^yfm11econp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protocol_version: Literal["yieldforge.m11-economic-resolution.v1"] = (
        "yieldforge.m11-economic-resolution.v1"
    )
    repair_commit_sha: StrictStr = Field(pattern=r"^[0-9a-f]{40}$")
    repaired_source_path: Literal["yf/src/yieldforge/baseline/geometry.py"] = (
        "yf/src/yieldforge/baseline/geometry.py"
    )
    repaired_source_raw_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    legacy_artifact_byte_count: StrictInt = Field(gt=0)
    legacy_artifact_raw_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    legacy_run_id: StrictStr = Field(pattern=r"^yfm11g3run-[0-9a-f]{24}$")
    legacy_run_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    legacy_root_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    accounting_arithmetic_semantic: Literal[
        "placed_plus_process_loss_plus_retained_plus_scrap_left_to_right"
    ] = ACCOUNTING_ARITHMETIC_SEMANTIC
    bootstrap_bit_generator: Literal["PCG64"] = "PCG64"
    bootstrap_seed: Literal[0] = 0
    bootstrap_resamples: Literal[10000] = 10_000
    central_full_future_mean_min_percent: Literal["2.500000000000"] = (
        "2.500000000000"
    )
    central_unknown_future_contribution_min_percentage_points: Literal[
        "1.500000000000"
    ] = "1.500000000000"
    causal_known_only_mean_min_percent: Literal["1.500000000000"] = (
        "1.500000000000"
    )
    lower_confidence_bound_rule: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    median_savings_rule: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    positive_stream_fraction_rule: Literal["strictly_greater_than_one_half"] = (
        "strictly_greater_than_one_half"
    )

    @model_validator(mode="after")
    def require_frozen_lineage_and_identity(self) -> Self:
        expected = (
            REPAIR_COMMIT_SHA,
            REPAIRED_SOURCE_RAW_SHA256,
            LEGACY_GATE3_ARTIFACT_BYTE_COUNT,
            LEGACY_GATE3_ARTIFACT_RAW_SHA256,
            LEGACY_GATE3_RUN_ID,
            LEGACY_GATE3_RUN_CONTENT_SHA256,
            LEGACY_GATE3_ROOT_CONTENT_SHA256,
        )
        actual = (
            self.repair_commit_sha,
            self.repaired_source_raw_sha256,
            self.legacy_artifact_byte_count,
            self.legacy_artifact_raw_sha256,
            self.legacy_run_id,
            self.legacy_run_content_sha256,
            self.legacy_root_content_sha256,
        )
        if actual != expected:
            raise ValueError("economic-resolution protocol differs from frozen repair lineage")
        digest = semantic_sha256(self, excluded_fields={"protocol_id", "content_sha256"})
        if self.protocol_id != f"yfm11econp-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("economic-resolution protocol identity differs from content")
        return self


def build_economic_resolution_protocol() -> EconomicResolutionProtocol:
    """Build the sole admitted, outcome-blind economic-resolution protocol."""

    semantic = {
        "schema_version": "yieldforge.m11-economic-resolution-protocol.v1",
        "protocol_version": "yieldforge.m11-economic-resolution.v1",
        "repair_commit_sha": REPAIR_COMMIT_SHA,
        "repaired_source_path": "yf/src/yieldforge/baseline/geometry.py",
        "repaired_source_raw_sha256": REPAIRED_SOURCE_RAW_SHA256,
        "legacy_artifact_byte_count": LEGACY_GATE3_ARTIFACT_BYTE_COUNT,
        "legacy_artifact_raw_sha256": LEGACY_GATE3_ARTIFACT_RAW_SHA256,
        "legacy_run_id": LEGACY_GATE3_RUN_ID,
        "legacy_run_content_sha256": LEGACY_GATE3_RUN_CONTENT_SHA256,
        "legacy_root_content_sha256": LEGACY_GATE3_ROOT_CONTENT_SHA256,
        "accounting_arithmetic_semantic": ACCOUNTING_ARITHMETIC_SEMANTIC,
        "bootstrap_bit_generator": "PCG64",
        "bootstrap_seed": 0,
        "bootstrap_resamples": 10_000,
        "central_full_future_mean_min_percent": "2.500000000000",
        "central_unknown_future_contribution_min_percentage_points": (
            "1.500000000000"
        ),
        "causal_known_only_mean_min_percent": "1.500000000000",
        "lower_confidence_bound_rule": "strictly_greater_than_zero",
        "median_savings_rule": "strictly_greater_than_zero",
        "positive_stream_fraction_rule": "strictly_greater_than_one_half",
    }
    digest = semantic_sha256(semantic)
    return EconomicResolutionProtocol(
        protocol_id=f"yfm11econp-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


class Gate3LegacyCalibrationAttemptReference(FrozenExperimentModel):
    """Compact authenticated reference to one legacy calibration attempt."""

    schema_version: Literal[
        "yieldforge.m11-economic-legacy-calibration-reference.v1"
    ] = "yieldforge.m11-economic-legacy-calibration-reference.v1"
    reference_id: StrictStr = Field(pattern=r"^yfm11econlegacy-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    execution_position: StrictInt = Field(ge=0, le=95)
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1)
    policy_id: Gate3BaselinePolicyId
    attempt_id: StrictStr = Field(pattern=r"^yfm11g3calatt-[0-9a-f]{24}$")
    attempt_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt_byte_offset: StrictInt = Field(ge=0)
    attempt_byte_count: StrictInt = Field(gt=0, le=64 * 1024 * 1024)
    status: Literal["success", "failure"]
    observation_id: StrictStr | None = Field(
        default=None,
        pattern=r"^yfm11g3calobs-[0-9a-f]{24}$",
    )
    observation_content_sha256: StrictStr | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    final_costs: Gate3CostLedger | None = None
    full_sheet_opening_count: StrictInt | None = Field(default=None, ge=0)
    exact_event_census: Literal[True] | None = None
    source_lineage: Literal["legacy_success_output_equivalent"] | None = None
    failure_type: StrictStr | None = None
    failure_detail: StrictStr | None = None

    @model_validator(mode="after")
    def require_exact_outcome_and_identity(self) -> Self:
        success_fields = (
            self.observation_id,
            self.observation_content_sha256,
            self.final_costs,
            self.full_sheet_opening_count,
            self.exact_event_census,
            self.source_lineage,
        )
        if self.status == "success":
            if (
                any(value is None for value in success_fields)
                or self.failure_type is not None
                or self.failure_detail is not None
            ):
                raise ValueError("legacy success reference lacks one compact outcome")
        elif (
            any(value is not None for value in success_fields)
            or not self.failure_type
            or not self.failure_detail
        ):
            raise ValueError("legacy failure reference admits economic cost or lacks failure")
        attempt_hash = self.attempt_content_sha256.removeprefix("sha256:")
        if self.attempt_id != f"yfm11g3calatt-{attempt_hash[:24]}":
            raise ValueError("legacy calibration attempt ID/full-SHA binding differs")
        if self.observation_id is not None:
            observation_hash = self.observation_content_sha256.removeprefix(  # type: ignore[union-attr]
                "sha256:"
            )
            if self.observation_id != f"yfm11g3calobs-{observation_hash[:24]}":
                raise ValueError("legacy calibration observation ID/full-SHA binding differs")
        digest = semantic_sha256(
            self,
            excluded_fields={"reference_id", "content_sha256"},
        )
        if self.reference_id != f"yfm11econlegacy-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("legacy calibration reference identity differs from content")
        return self


def _build_legacy_calibration_reference(
    attempt: Gate3CalibrationAttempt,
    *,
    attempt_byte_offset: int,
    attempt_byte_count: int,
) -> Gate3LegacyCalibrationAttemptReference:
    observation = attempt.observation
    semantic = {
        "schema_version": "yieldforge.m11-economic-legacy-calibration-reference.v1",
        "roots": attempt.roots.model_dump(mode="json"),
        "execution_position": attempt.execution_position,
        "corpus_id": attempt.corpus_id,
        "stream_id": attempt.stream_id,
        "policy_id": attempt.policy_id,
        "attempt_id": attempt.attempt_id,
        "attempt_content_sha256": attempt.content_sha256,
        "attempt_byte_offset": attempt_byte_offset,
        "attempt_byte_count": attempt_byte_count,
        "status": attempt.status,
        "observation_id": observation.observation_id if observation else None,
        "observation_content_sha256": observation.content_sha256 if observation else None,
        "final_costs": observation.final_costs.model_dump(mode="json") if observation else None,
        "full_sheet_opening_count": (
            observation.full_sheet_opening_count if observation else None
        ),
        "exact_event_census": observation.exact_event_census if observation else None,
        "source_lineage": (
            "legacy_success_output_equivalent" if observation else None
        ),
        "failure_type": attempt.failure_type,
        "failure_detail": attempt.failure_detail,
    }
    digest = semantic_sha256(semantic)
    return Gate3LegacyCalibrationAttemptReference(
        reference_id=f"yfm11econlegacy-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=attempt.roots,
        execution_position=attempt.execution_position,
        corpus_id=attempt.corpus_id,
        stream_id=attempt.stream_id,
        policy_id=attempt.policy_id,
        attempt_id=attempt.attempt_id,
        attempt_content_sha256=attempt.content_sha256,
        attempt_byte_offset=attempt_byte_offset,
        attempt_byte_count=attempt_byte_count,
        status=attempt.status,
        observation_id=observation.observation_id if observation else None,
        observation_content_sha256=observation.content_sha256 if observation else None,
        final_costs=observation.final_costs if observation else None,
        full_sheet_opening_count=(
            observation.full_sheet_opening_count if observation else None
        ),
        exact_event_census=observation.exact_event_census if observation else None,
        source_lineage=("legacy_success_output_equivalent" if observation else None),
        failure_type=attempt.failure_type,
        failure_detail=attempt.failure_detail,
    )


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


def _require_expected_scan_inputs(
    *,
    expected_byte_count: int,
    expected_raw_sha256: str,
    expected_root_content_sha256: str,
    expected_success_count: int,
    expected_failure_count: int,
    chunk_size: int,
) -> None:
    if (
        type(expected_byte_count) is not int
        or expected_byte_count <= 0
        or type(expected_raw_sha256) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_raw_sha256) is None
        or type(expected_root_content_sha256) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_root_content_sha256) is None
        or type(expected_success_count) is not int
        or expected_success_count < 0
        or type(expected_failure_count) is not int
        or expected_failure_count < 0
        or type(chunk_size) is not int
        or chunk_size <= 0
    ):
        raise EconomicResolutionEvidenceError("legacy scan expected binding is malformed")


def _authenticated_file_pass(
    path: Path,
    *,
    expected_byte_count: int,
    expected_raw_sha256: str,
    chunk_size: int,
    expected_fingerprint: _FileFingerprint | None = None,
    consume: object | None = None,
) -> _FileFingerprint:
    candidate = Path(path)
    descriptor: int | None = None
    try:
        before = candidate.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise EconomicResolutionEvidenceError(
                "legacy calibration artifact must be a regular non-symlink file"
            )
        before_fingerprint = _fingerprint(before)
        if before.st_size != expected_byte_count:
            raise EconomicResolutionEvidenceError("legacy calibration artifact size differs")
        if (
            expected_fingerprint is not None
            and before_fingerprint != expected_fingerprint
        ):
            raise EconomicResolutionEvidenceError(
                "legacy calibration artifact changed between authenticated passes"
            )
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _fingerprint(opened) != before_fingerprint
        ):
            raise EconomicResolutionEvidenceError(
                "legacy calibration artifact identity changed during open"
            )
        digest = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(descriptor, chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            if consume is not None:
                consume(chunk, byte_count)  # type: ignore[operator]
            byte_count += len(chunk)
            if byte_count > expected_byte_count:
                raise EconomicResolutionEvidenceError(
                    "legacy calibration artifact exceeds expected size"
                )
        during = os.fstat(descriptor)
    except EconomicResolutionEvidenceError:
        raise
    except OSError as error:
        raise EconomicResolutionEvidenceError(
            "legacy calibration artifact could not be read safely"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = candidate.lstat()
    except OSError as error:
        raise EconomicResolutionEvidenceError(
            "legacy calibration artifact could not be re-inspected"
        ) from error
    if (
        byte_count != expected_byte_count
        or _fingerprint(during) != before_fingerprint
        or _fingerprint(after) != before_fingerprint
    ):
        raise EconomicResolutionEvidenceError(
            "legacy calibration artifact fingerprint changed during read"
        )
    if f"sha256:{digest.hexdigest()}" != expected_raw_sha256:
        raise EconomicResolutionEvidenceError("legacy calibration artifact raw hash differs")
    return before_fingerprint


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EconomicResolutionEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise EconomicResolutionEvidenceError(f"nonfinite JSON constant: {value}")


def _strict_attempt_reference(
    raw: bytes,
    *,
    byte_offset: int,
) -> Gate3LegacyCalibrationAttemptReference:
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        attempt = Gate3CalibrationAttempt.model_validate_json(raw, strict=True)
    except EconomicResolutionEvidenceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "legacy calibration attempt failed strict validation"
        ) from error
    return _build_legacy_calibration_reference(
        attempt,
        attempt_byte_offset=byte_offset,
        attempt_byte_count=len(raw),
    )


class _CalibrationAttemptArrayFramer:
    """Frame one top-level attempt object while retaining at most one object."""

    def __init__(self) -> None:
        self.references: list[Gate3LegacyCalibrationAttemptReference] = []
        self.marker_count = 0
        self._marker_progress = 0
        self._array_started = False
        self._array_ended = False
        self._state: Literal["value", "object", "separator"] = "value"
        self._object = bytearray()
        self._object_offset = 0
        self._object_depth = 0
        self._in_string = False
        self._escape = False

    def feed(self, chunk: bytes, chunk_offset: int) -> None:
        for local_offset, value in enumerate(chunk):
            absolute_offset = chunk_offset + local_offset
            marker_count = self.marker_count
            self._advance_marker(value)
            if self.marker_count != marker_count:
                continue
            if not self._array_started or self._array_ended:
                continue
            if self._state == "value":
                if value in b" \t\r\n":
                    continue
                if value == ord("]"):
                    self._array_ended = True
                    continue
                if value != ord("{"):
                    raise EconomicResolutionEvidenceError(
                        "legacy calibration array contains a non-object value"
                    )
                self._state = "object"
                self._object_offset = absolute_offset
                self._object = bytearray(b"{")
                self._object_depth = 1
                self._in_string = False
                self._escape = False
                continue
            if self._state == "separator":
                if value in b" \t\r\n":
                    continue
                if value == ord(","):
                    self._state = "value"
                    continue
                if value == ord("]"):
                    self._array_ended = True
                    continue
                raise EconomicResolutionEvidenceError(
                    "legacy calibration array separator is malformed"
                )
            self._object.append(value)
            if len(self._object) > _MAX_ATTEMPT_OBJECT_BYTES:
                raise EconomicResolutionEvidenceError(
                    "legacy calibration attempt exceeds object byte bound"
                )
            if self._in_string:
                if self._escape:
                    self._escape = False
                elif value == ord("\\"):
                    self._escape = True
                elif value == ord('"'):
                    self._in_string = False
                continue
            if value == ord('"'):
                self._in_string = True
            elif value == ord("{"):
                self._object_depth += 1
            elif value == ord("}"):
                self._object_depth -= 1
                if self._object_depth == 0:
                    raw = bytes(self._object)
                    reference = _strict_attempt_reference(
                        raw,
                        byte_offset=self._object_offset,
                    )
                    self.references.append(reference)
                    self._object.clear()
                    self._state = "separator"

    def _advance_marker(self, value: int) -> None:
        marker = _CALIBRATION_ARRAY_MARKER
        while self._marker_progress and value != marker[self._marker_progress]:
            self._marker_progress = 1 if value == marker[0] else 0
            if self._marker_progress:
                return
        if value == marker[self._marker_progress]:
            self._marker_progress += 1
            if self._marker_progress == len(marker):
                self.marker_count += 1
                self._marker_progress = 0
                if self.marker_count == 1:
                    self._array_started = True
                    self._state = "value"

    def finish(self) -> tuple[Gate3LegacyCalibrationAttemptReference, ...]:
        if self.marker_count != 1:
            raise EconomicResolutionEvidenceError(
                "legacy artifact must contain exactly one calibration_attempts marker"
            )
        if not self._array_ended or self._state == "object":
            raise EconomicResolutionEvidenceError(
                "legacy calibration attempt array is incomplete"
            )
        return tuple(self.references)


def _scan_legacy_calibration_attempts(
    path: Path,
    *,
    expected_byte_count: int,
    expected_raw_sha256: str,
    expected_root_content_sha256: str,
    expected_attempt_order: tuple[tuple[str, str, str], ...] | None,
    expected_success_count: int,
    expected_failure_count: int,
    chunk_size: int = _DEFAULT_SCAN_CHUNK_BYTES,
) -> tuple[Gate3LegacyCalibrationAttemptReference, ...]:
    """Authenticate twice, then retain only compact references from one array."""

    _require_expected_scan_inputs(
        expected_byte_count=expected_byte_count,
        expected_raw_sha256=expected_raw_sha256,
        expected_root_content_sha256=expected_root_content_sha256,
        expected_success_count=expected_success_count,
        expected_failure_count=expected_failure_count,
        chunk_size=chunk_size,
    )
    if expected_attempt_order is not None and (
        type(expected_attempt_order) is not tuple or not expected_attempt_order
    ):
        raise EconomicResolutionEvidenceError("legacy expected attempt order is malformed")
    first_fingerprint = _authenticated_file_pass(
        path,
        expected_byte_count=expected_byte_count,
        expected_raw_sha256=expected_raw_sha256,
        chunk_size=chunk_size,
    )
    framer = _CalibrationAttemptArrayFramer()
    _authenticated_file_pass(
        path,
        expected_byte_count=expected_byte_count,
        expected_raw_sha256=expected_raw_sha256,
        chunk_size=chunk_size,
        expected_fingerprint=first_fingerprint,
        consume=framer.feed,
    )
    references = framer.finish()
    expected_count = (
        len(expected_attempt_order)
        if expected_attempt_order is not None
        else expected_success_count + expected_failure_count
    )
    if tuple(item.execution_position for item in references) != tuple(range(expected_count)):
        raise EconomicResolutionEvidenceError(
            "legacy calibration execution positions differ from expected order"
        )
    actual_order = tuple(
        (item.corpus_id, item.stream_id, item.policy_id) for item in references
    )
    if expected_attempt_order is not None and actual_order != expected_attempt_order:
        raise EconomicResolutionEvidenceError(
            "legacy calibration corpus stream policy order differs"
        )
    if any(
        item.roots.content_sha256 != expected_root_content_sha256 for item in references
    ):
        raise EconomicResolutionEvidenceError("legacy calibration root binding differs")
    success_count = sum(item.status == "success" for item in references)
    failure_count = sum(item.status == "failure" for item in references)
    if (success_count, failure_count) != (
        expected_success_count,
        expected_failure_count,
    ):
        raise EconomicResolutionEvidenceError("legacy calibration status census differs")
    return references


class Gate3LegacyCalibrationScan(FrozenExperimentModel):
    """Complete compact authentication result for the preserved legacy artifact."""

    schema_version: Literal["yieldforge.m11-economic-legacy-calibration-scan.v1"] = (
        "yieldforge.m11-economic-legacy-calibration-scan.v1"
    )
    scan_id: StrictStr = Field(pattern=r"^yfm11econscan-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protocol: EconomicResolutionProtocol
    roots: Gate3RootBinding
    legacy_artifact_name: Literal[
        "m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json"
    ] = "m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json"
    legacy_artifact_byte_count: Literal[2270455752] = LEGACY_GATE3_ARTIFACT_BYTE_COUNT
    legacy_artifact_raw_sha256: Literal[
        "sha256:e5757919ddd9251bf374d1664be25faf175963e78478b223ea0d7e22f7439199"
    ] = LEGACY_GATE3_ARTIFACT_RAW_SHA256
    legacy_run_id: Literal["yfm11g3run-3dd87efab6f64ada4c5bd09c"] = (
        LEGACY_GATE3_RUN_ID
    )
    legacy_run_content_sha256: Literal[
        "sha256:3dd87efab6f64ada4c5bd09c0580a1696017b3115ccdce3ce041b4221c89a89f"
    ] = LEGACY_GATE3_RUN_CONTENT_SHA256
    calibration_stream_census: tuple[
        tuple[Gate3CorpusId, tuple[StrictStr, ...]],
        tuple[Gate3CorpusId, tuple[StrictStr, ...]],
    ]
    attempt_references: tuple[Gate3LegacyCalibrationAttemptReference, ...] = Field(
        min_length=96,
        max_length=96,
    )
    success_count: Literal[60] = 60
    failure_count: Literal[36] = 36
    authentication_complete: Literal[True] = True

    @model_validator(mode="after")
    def require_official_census_and_identity(self) -> Self:
        if self.protocol != build_economic_resolution_protocol():
            raise ValueError("official legacy scan protocol binding differs")
        if self.roots.content_sha256 != LEGACY_GATE3_ROOT_CONTENT_SHA256:
            raise ValueError("official legacy scan root binding differs")
        references = self.attempt_references
        if tuple(item.execution_position for item in references) != tuple(range(96)):
            raise ValueError("official legacy scan execution order differs")
        if any(item.roots != self.roots for item in references):
            raise ValueError("official legacy scan reference roots differ")
        lectra_streams = tuple(item.stream_id for item in references[:8])
        loco_streams = tuple(item.stream_id for item in references[48:56])
        expected_census = (
            ("lectra-m3-m4", lectra_streams),
            ("loco-2dics", loco_streams),
        )
        if (
            self.calibration_stream_census != expected_census
            or any(len(streams) != 8 or len(set(streams)) != 8 for _, streams in expected_census)
        ):
            raise ValueError("official legacy scan repeated stream census differs")
        expected_order = tuple(
            (corpus_id, stream_id, policy_id)
            for corpus_id, streams in expected_census
            for policy_id in GATE3_BASELINE_POLICY_IDS
            for stream_id in streams
        )
        actual_order = tuple(
            (item.corpus_id, item.stream_id, item.policy_id) for item in references
        )
        if actual_order != expected_order:
            raise ValueError("official legacy scan corpus policy stream order differs")
        actual_counts = (
            sum(item.status == "success" for item in references),
            sum(item.status == "failure" for item in references),
        )
        if actual_counts != (self.success_count, self.failure_count):
            raise ValueError("official legacy scan status census differs")
        byte_ranges = tuple(
            (item.attempt_byte_offset, item.attempt_byte_offset + item.attempt_byte_count)
            for item in references
        )
        if any(
            start < 0
            or end > self.legacy_artifact_byte_count
            or (index and start <= byte_ranges[index - 1][1])
            for index, (start, end) in enumerate(byte_ranges)
        ):
            raise ValueError("official legacy scan byte ranges overlap or escape artifact")
        digest = semantic_sha256(self, excluded_fields={"scan_id", "content_sha256"})
        if self.scan_id != f"yfm11econscan-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("official legacy scan identity differs from content")
        return self


def build_official_legacy_calibration_scan(
    references: tuple[Gate3LegacyCalibrationAttemptReference, ...],
) -> Gate3LegacyCalibrationScan:
    """Bind 96 compact references to the authenticated failed legacy run."""

    if type(references) is not tuple or not references:
        raise EconomicResolutionEvidenceError("official legacy scan references are malformed")
    roots = references[0].roots
    protocol = build_economic_resolution_protocol()
    census = (
        ("lectra-m3-m4", tuple(item.stream_id for item in references[:8])),
        ("loco-2dics", tuple(item.stream_id for item in references[48:56])),
    )
    semantic = {
        "schema_version": "yieldforge.m11-economic-legacy-calibration-scan.v1",
        "protocol": protocol.model_dump(mode="json"),
        "roots": roots.model_dump(mode="json"),
        "legacy_artifact_name": (
            "m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json"
        ),
        "legacy_artifact_byte_count": LEGACY_GATE3_ARTIFACT_BYTE_COUNT,
        "legacy_artifact_raw_sha256": LEGACY_GATE3_ARTIFACT_RAW_SHA256,
        "legacy_run_id": LEGACY_GATE3_RUN_ID,
        "legacy_run_content_sha256": LEGACY_GATE3_RUN_CONTENT_SHA256,
        "calibration_stream_census": census,
        "attempt_references": [item.model_dump(mode="json") for item in references],
        "success_count": 60,
        "failure_count": 36,
        "authentication_complete": True,
    }
    digest = semantic_sha256(semantic)
    try:
        return Gate3LegacyCalibrationScan(
            scan_id=f"yfm11econscan-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            protocol=protocol,
            roots=roots,
            calibration_stream_census=census,  # type: ignore[arg-type]
            attempt_references=references,
        )
    except (ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(str(error)) from error


def scan_official_legacy_gate3_calibration_artifact(
    path: Path,
) -> Gate3LegacyCalibrationScan:
    """Authenticate and compact the exact preserved 2.27 GB Gate 3 artifact."""

    references = _scan_legacy_calibration_attempts(
        path,
        expected_byte_count=LEGACY_GATE3_ARTIFACT_BYTE_COUNT,
        expected_raw_sha256=LEGACY_GATE3_ARTIFACT_RAW_SHA256,
        expected_root_content_sha256=LEGACY_GATE3_ROOT_CONTENT_SHA256,
        expected_attempt_order=None,
        expected_success_count=60,
        expected_failure_count=36,
    )
    return build_official_legacy_calibration_scan(references)


scan_legacy_gate3_calibration_artifact = (
    scan_official_legacy_gate3_calibration_artifact
)
