"""Repair-lineage calibration evidence for M11 economic resolution.

This module deliberately starts a new, outcome-blind protocol.  It never amends
or rescues the preserved failed M11 Gate 3 result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import time
from dataclasses import dataclass
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
    Gate3BaselineCalibrationFreeze,
    Gate3BaselinePolicyId,
    Gate3CalibrationAttempt,
    Gate3CorpusId,
    Gate3CostLedger,
    Gate3RootBinding,
    select_gate3_baseline_policy,
)
from yieldforge.realistic_falsification.economic_evidence_store import (
    Gate3CalibrationObservationReceipt,
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
ACCOUNTING_ARITHMETIC_SEMANTIC = "placed_plus_process_loss_plus_retained_plus_scrap_left_to_right"
_MAX_ATTEMPT_OBJECT_BYTES = 64 * 1024 * 1024
_DEFAULT_SCAN_CHUNK_BYTES = 1024 * 1024
_MAX_SCAN_CHUNK_BYTES = 64 * 1024 * 1024
_MAX_FAILURE_TYPE_CHARS = 240
_MAX_FAILURE_DETAIL_CHARS = 1000
_CALIBRATION_ARRAY_MARKER = b'"calibration_attempts": ['
_MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_REPAIRED_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_GIT_METADATA_BYTES = 4096
_MAX_GIT_STDERR_BYTES = 16 * 1024
_GIT_TIMEOUT_SECONDS = 10.0


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
    bootstrap_generator: Literal["numpy.Generator(PCG64(0))"] = "numpy.Generator(PCG64(0))"
    bootstrap_seed: Literal[0] = 0
    bootstrap_resamples: Literal[10000] = 10_000
    bootstrap_resampling_unit: Literal["paired_stream"] = "paired_stream"
    bootstrap_quantile_method: Literal["linear_type_7"] = "linear_type_7"
    bootstrap_confidence_level: Literal[0.95] = 0.95
    bootstrap_lower_quantile: Literal[0.025] = 0.025
    bootstrap_upper_quantile: Literal[0.975] = 0.975
    max_attempt_object_bytes: Literal[67108864] = _MAX_ATTEMPT_OBJECT_BYTES
    central_full_future_mean_min_percent: Literal["2.500000000000"] = "2.500000000000"
    central_unknown_future_contribution_min_percentage_points: Literal["1.500000000000"] = (
        "1.500000000000"
    )
    causal_known_only_mean_min_percent: Literal["1.500000000000"] = "1.500000000000"
    lower_confidence_bound_rule: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    median_savings_rule: Literal["strictly_greater_than_zero"] = "strictly_greater_than_zero"
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
        "bootstrap_generator": "numpy.Generator(PCG64(0))",
        "bootstrap_seed": 0,
        "bootstrap_resamples": 10_000,
        "bootstrap_resampling_unit": "paired_stream",
        "bootstrap_quantile_method": "linear_type_7",
        "bootstrap_confidence_level": 0.95,
        "bootstrap_lower_quantile": 0.025,
        "bootstrap_upper_quantile": 0.975,
        "max_attempt_object_bytes": _MAX_ATTEMPT_OBJECT_BYTES,
        "central_full_future_mean_min_percent": "2.500000000000",
        "central_unknown_future_contribution_min_percentage_points": ("1.500000000000"),
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


@dataclass(frozen=True, slots=True)
class _BoundedGitResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _run_git_bounded(
    cwd: Path,
    arguments: tuple[str, ...],
    *,
    max_stdout_bytes: int,
    timeout_seconds: float = _GIT_TIMEOUT_SECONDS,
) -> _BoundedGitResult:
    """Run one read-only Git query without a shell or unbounded pipe reads."""

    if (
        type(arguments) is not tuple
        or not arguments
        or any(type(item) is not str or not item or "\x00" in item for item in arguments)
        or type(max_stdout_bytes) is not int
        or not 0 <= max_stdout_bytes <= _MAX_REPAIRED_SOURCE_BYTES
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= 30
    ):
        raise EconomicResolutionEvidenceError("Git lineage query bounds are malformed")
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    try:
        environment = os.environ.copy()
        for variable in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            environment.pop(variable, None)
        environment.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "LC_ALL": "C",
            }
        )
        process = subprocess.Popen(
            ("git", "--no-pager", "-C", os.fspath(cwd), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            env=environment,
            shell=False,
        )
        if process.stdout is None or process.stderr is None:
            raise EconomicResolutionEvidenceError("Git lineage query pipes are unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, (stdout, max_stdout_bytes))
        selector.register(process.stderr, selectors.EVENT_READ, (stderr, _MAX_GIT_STDERR_BYTES))
        deadline = time.monotonic() + float(timeout_seconds)
        while selector.get_map():
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise EconomicResolutionEvidenceError("Git lineage query timed out")
            events = selector.select(remaining_seconds)
            if not events:
                raise EconomicResolutionEvidenceError("Git lineage query timed out")
            for key, _ in events:
                buffer, limit = key.data
                chunk = os.read(key.fd, min(64 * 1024, limit - len(buffer) + 1))
                if chunk:
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        raise EconomicResolutionEvidenceError(
                            "Git lineage query exceeded its output bound"
                        )
                else:
                    selector.unregister(key.fileobj)
        wait_seconds = max(0.001, deadline - time.monotonic())
        returncode = process.wait(timeout=wait_seconds)
        return _BoundedGitResult(returncode, bytes(stdout), bytes(stderr))
    except EconomicResolutionEvidenceError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise EconomicResolutionEvidenceError("Git lineage query failed safely") from error
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.SubprocessError:
                pass
        if selector is not None:
            selector.close()
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _one_git_output_line(result: _BoundedGitResult, *, label: str) -> bytes:
    if result.returncode != 0:
        raise EconomicResolutionEvidenceError(f"{label} Git query failed")
    if b"\x00" in result.stdout:
        raise EconomicResolutionEvidenceError(f"{label} Git output is malformed")
    lines = result.stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise EconomicResolutionEvidenceError(f"{label} Git output is malformed")
    return lines[0]


def verify_economic_resolution_runtime_lineage(
    repository_root: Path,
    protocol: EconomicResolutionProtocol,
) -> None:
    """Require HEAD and the working source to retain the frozen repair lineage."""

    try:
        strict_protocol = EconomicResolutionProtocol.model_validate(
            protocol.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "economic-resolution runtime protocol failed strict validation"
        ) from error
    if strict_protocol != build_economic_resolution_protocol():
        raise EconomicResolutionEvidenceError(
            "economic-resolution runtime protocol differs from frozen lineage"
        )
    start = Path(repository_root)
    try:
        start_metadata = start.lstat()
        if stat.S_ISLNK(start_metadata.st_mode) or not stat.S_ISDIR(start_metadata.st_mode):
            raise EconomicResolutionEvidenceError(
                "repository root must be a regular non-symlink directory"
            )
        start = start.resolve(strict=True)
    except EconomicResolutionEvidenceError:
        raise
    except (OSError, RuntimeError) as error:
        raise EconomicResolutionEvidenceError("repository root could not be resolved") from error

    top_level_result = _run_git_bounded(
        start,
        ("rev-parse", "--show-toplevel"),
        max_stdout_bytes=_MAX_GIT_METADATA_BYTES,
    )
    top_level_raw = _one_git_output_line(top_level_result, label="repository top-level")
    try:
        top_level_candidate = Path(os.fsdecode(top_level_raw))
        top_level_metadata = top_level_candidate.lstat()
        if (
            not top_level_candidate.is_absolute()
            or stat.S_ISLNK(top_level_metadata.st_mode)
            or not stat.S_ISDIR(top_level_metadata.st_mode)
        ):
            raise EconomicResolutionEvidenceError(
                "repository top-level must be an absolute non-symlink directory"
            )
        top_level = top_level_candidate.resolve(strict=True)
        if not start.is_relative_to(top_level):
            raise EconomicResolutionEvidenceError(
                "discovered repository top-level does not contain the requested root"
            )
    except EconomicResolutionEvidenceError:
        raise
    except (OSError, RuntimeError, UnicodeError) as error:
        raise EconomicResolutionEvidenceError(
            "repository top-level Git output could not be resolved"
        ) from error

    commit_sha = strict_protocol.repair_commit_sha
    commit_result = _run_git_bounded(
        top_level,
        ("cat-file", "-e", f"{commit_sha}^{{commit}}"),
        max_stdout_bytes=0,
    )
    if commit_result.returncode != 0 or commit_result.stdout:
        raise EconomicResolutionEvidenceError("repair commit does not exist as a commit")
    ancestor_result = _run_git_bounded(
        top_level,
        ("merge-base", "--is-ancestor", commit_sha, "HEAD"),
        max_stdout_bytes=0,
    )
    if ancestor_result.returncode != 0 or ancestor_result.stdout:
        raise EconomicResolutionEvidenceError("repair commit is not an ancestor of HEAD")

    blob_expression = f"{commit_sha}:{strict_protocol.repaired_source_path}"
    try:
        blob_id = _one_git_output_line(
            _run_git_bounded(
                top_level,
                ("rev-parse", "--verify", blob_expression),
                max_stdout_bytes=_MAX_GIT_METADATA_BYTES,
            ),
            label="repair commit blob",
        ).decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise EconomicResolutionEvidenceError("repair commit blob identity is malformed") from error
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", blob_id) is None:
        raise EconomicResolutionEvidenceError("repair commit blob identity is malformed")
    blob_type = _one_git_output_line(
        _run_git_bounded(
            top_level,
            ("cat-file", "-t", blob_id),
            max_stdout_bytes=_MAX_GIT_METADATA_BYTES,
        ),
        label="repair commit blob type",
    )
    if blob_type != b"blob":
        raise EconomicResolutionEvidenceError("repair commit source object is not a blob")
    size_raw = _one_git_output_line(
        _run_git_bounded(
            top_level,
            ("cat-file", "-s", blob_id),
            max_stdout_bytes=_MAX_GIT_METADATA_BYTES,
        ),
        label="repair commit blob size",
    )
    try:
        if re.fullmatch(rb"[0-9]+", size_raw) is None:
            raise ValueError
        blob_size = int(size_raw)
    except (OverflowError, ValueError) as error:
        raise EconomicResolutionEvidenceError("repair commit blob size is malformed") from error
    if not 0 <= blob_size <= _MAX_REPAIRED_SOURCE_BYTES:
        raise EconomicResolutionEvidenceError("repair commit blob exceeds the source byte bound")
    blob_result = _run_git_bounded(
        top_level,
        ("cat-file", "blob", blob_id),
        max_stdout_bytes=blob_size,
    )
    if blob_result.returncode != 0 or len(blob_result.stdout) != blob_size:
        raise EconomicResolutionEvidenceError("repair commit blob could not be read exactly")
    if f"sha256:{hashlib.sha256(blob_result.stdout).hexdigest()}" != (
        strict_protocol.repaired_source_raw_sha256
    ):
        raise EconomicResolutionEvidenceError("repair commit blob raw SHA-256 differs")

    current_source = top_level / strict_protocol.repaired_source_path
    current_raw = _read_bounded_regular_file(
        current_source,
        max_bytes=_MAX_REPAIRED_SOURCE_BYTES,
        label="runtime repaired source",
    )
    if f"sha256:{hashlib.sha256(current_raw).hexdigest()}" != (
        strict_protocol.repaired_source_raw_sha256
    ):
        raise EconomicResolutionEvidenceError("current source raw SHA-256 differs from repair")


class Gate3LegacyCalibrationAttemptReference(FrozenExperimentModel):
    """Compact authenticated reference to one legacy calibration attempt."""

    schema_version: Literal["yieldforge.m11-economic-legacy-calibration-reference.v1"] = (
        "yieldforge.m11-economic-legacy-calibration-reference.v1"
    )
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
    failure_type: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_FAILURE_TYPE_CHARS,
    )
    failure_detail: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_FAILURE_DETAIL_CHARS,
    )

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
        "full_sheet_opening_count": (observation.full_sheet_opening_count if observation else None),
        "exact_event_census": observation.exact_event_census if observation else None,
        "source_lineage": ("legacy_success_output_equivalent" if observation else None),
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
        full_sheet_opening_count=(observation.full_sheet_opening_count if observation else None),
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
        or chunk_size > _MAX_SCAN_CHUNK_BYTES
    ):
        if type(chunk_size) is not int or not 0 < chunk_size <= _MAX_SCAN_CHUNK_BYTES:
            raise EconomicResolutionEvidenceError("legacy scan chunk size is malformed")
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
        if expected_fingerprint is not None and before_fingerprint != expected_fingerprint:
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
        if not stat.S_ISREG(opened.st_mode) or _fingerprint(opened) != before_fingerprint:
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
    except (OSError, OverflowError) as error:
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
    if attempt.status == "failure" and (
        attempt.failure_type is None
        or attempt.failure_detail is None
        or len(attempt.failure_type) > _MAX_FAILURE_TYPE_CHARS
        or len(attempt.failure_detail) > _MAX_FAILURE_DETAIL_CHARS
    ):
        raise EconomicResolutionEvidenceError(
            "legacy calibration failure text exceeds compact retention bound"
        )
    try:
        return _build_legacy_calibration_reference(
            attempt,
            attempt_byte_offset=byte_offset,
            attempt_byte_count=len(raw),
        )
    except (ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "legacy calibration attempt could not be compacted safely"
        ) from error


class _CalibrationAttemptArrayFramer:
    """Frame one top-level attempt object while retaining at most one object."""

    def __init__(self, *, max_attempt_object_bytes: int) -> None:
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
        self._max_attempt_object_bytes = max_attempt_object_bytes

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
            if len(self._object) > self._max_attempt_object_bytes:
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
            raise EconomicResolutionEvidenceError("legacy calibration attempt array is incomplete")
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
    max_attempt_object_bytes: int | None = None,
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
    object_byte_bound = (
        _MAX_ATTEMPT_OBJECT_BYTES if max_attempt_object_bytes is None else max_attempt_object_bytes
    )
    if (
        type(object_byte_bound) is not int
        or object_byte_bound <= 0
        or object_byte_bound > _MAX_ATTEMPT_OBJECT_BYTES
    ):
        raise EconomicResolutionEvidenceError(
            "legacy calibration attempt object byte bound is malformed"
        )
    first_fingerprint = _authenticated_file_pass(
        path,
        expected_byte_count=expected_byte_count,
        expected_raw_sha256=expected_raw_sha256,
        chunk_size=chunk_size,
    )
    framer = _CalibrationAttemptArrayFramer(
        max_attempt_object_bytes=object_byte_bound,
    )
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
    actual_order = tuple((item.corpus_id, item.stream_id, item.policy_id) for item in references)
    if expected_attempt_order is not None and actual_order != expected_attempt_order:
        raise EconomicResolutionEvidenceError(
            "legacy calibration corpus stream policy order differs"
        )
    if any(item.roots.content_sha256 != expected_root_content_sha256 for item in references):
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
    legacy_artifact_name: Literal["m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json"] = (
        "m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json"
    )
    legacy_artifact_byte_count: Literal[2270455752] = LEGACY_GATE3_ARTIFACT_BYTE_COUNT
    legacy_artifact_raw_sha256: Literal[
        "sha256:e5757919ddd9251bf374d1664be25faf175963e78478b223ea0d7e22f7439199"
    ] = LEGACY_GATE3_ARTIFACT_RAW_SHA256
    legacy_run_id: Literal["yfm11g3run-3dd87efab6f64ada4c5bd09c"] = LEGACY_GATE3_RUN_ID
    legacy_run_content_sha256: Literal[
        "sha256:3dd87efab6f64ada4c5bd09c0580a1696017b3115ccdce3ce041b4221c89a89f"
    ] = LEGACY_GATE3_RUN_CONTENT_SHA256
    max_attempt_object_bytes: Literal[67108864] = _MAX_ATTEMPT_OBJECT_BYTES
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
        if self.max_attempt_object_bytes != self.protocol.max_attempt_object_bytes:
            raise ValueError("official legacy scan object byte bound differs")
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
        if self.calibration_stream_census != expected_census or any(
            len(streams) != 8 or len(set(streams)) != 8 for _, streams in expected_census
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
            or (index and start < byte_ranges[index - 1][1])
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
        "legacy_artifact_name": ("m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json"),
        "legacy_artifact_byte_count": LEGACY_GATE3_ARTIFACT_BYTE_COUNT,
        "legacy_artifact_raw_sha256": LEGACY_GATE3_ARTIFACT_RAW_SHA256,
        "legacy_run_id": LEGACY_GATE3_RUN_ID,
        "legacy_run_content_sha256": LEGACY_GATE3_RUN_CONTENT_SHA256,
        "max_attempt_object_bytes": _MAX_ATTEMPT_OBJECT_BYTES,
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
        max_attempt_object_bytes=_MAX_ATTEMPT_OBJECT_BYTES,
    )
    return build_official_legacy_calibration_scan(references)


scan_legacy_gate3_calibration_artifact = scan_official_legacy_gate3_calibration_artifact


class Gate3CalibrationAttemptCheckpoint(FrozenExperimentModel):
    """One immutable compact calibration outcome under the repair protocol."""

    schema_version: Literal["yieldforge.m11-economic-calibration-checkpoint.v1"] = (
        "yieldforge.m11-economic-calibration-checkpoint.v1"
    )
    checkpoint_id: StrictStr = Field(pattern=r"^yfm11econcal-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protocol: EconomicResolutionProtocol
    roots: Gate3RootBinding
    execution_position: StrictInt = Field(ge=0, le=95)
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1)
    policy_id: Gate3BaselinePolicyId
    outcome_kind: Literal[
        "legacy_success_reference",
        "repaired_runtime_success",
        "repaired_runtime_failure",
    ]
    legacy_reference: Gate3LegacyCalibrationAttemptReference | None = None
    replaced_legacy_failure_reference: Gate3LegacyCalibrationAttemptReference | None = None
    repaired_receipt: Gate3CalibrationObservationReceipt | None = None
    failure_type: StrictStr | None = Field(default=None, max_length=240)
    failure_detail: StrictStr | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_one_bound_outcome_and_identity(self) -> Self:
        if self.protocol != build_economic_resolution_protocol():
            raise ValueError("calibration checkpoint protocol binding differs")
        if self.roots.content_sha256 != self.protocol.legacy_root_content_sha256:
            raise ValueError("calibration checkpoint root binding differs")
        binding = (
            self.roots,
            self.execution_position,
            self.corpus_id,
            self.stream_id,
            self.policy_id,
        )
        if self.outcome_kind == "legacy_success_reference":
            reference = self.legacy_reference
            if (
                reference is None
                or self.replaced_legacy_failure_reference is not None
                or self.repaired_receipt is not None
                or self.failure_type is not None
                or self.failure_detail is not None
                or reference.status != "success"
                or reference.source_lineage != "legacy_success_output_equivalent"
                or (
                    reference.roots,
                    reference.execution_position,
                    reference.corpus_id,
                    reference.stream_id,
                    reference.policy_id,
                )
                != binding
            ):
                raise ValueError(
                    "legacy calibration checkpoint binding differs or outcome is not unique"
                )
        elif self.outcome_kind == "repaired_runtime_success":
            receipt = self.repaired_receipt
            replaced = self.replaced_legacy_failure_reference
            if (
                receipt is None
                or self.legacy_reference is not None
                or replaced is None
                or replaced.status != "failure"
                or self.failure_type is not None
                or self.failure_detail is not None
                or receipt.source_lineage != "repaired_runtime"
                or (
                    receipt.roots,
                    self.execution_position,
                    receipt.corpus_id,
                    receipt.stream_id,
                    receipt.policy_id,
                )
                != binding
                or (
                    replaced.roots,
                    replaced.execution_position,
                    replaced.corpus_id,
                    replaced.stream_id,
                    replaced.policy_id,
                )
                != binding
            ):
                raise ValueError("repaired_runtime calibration checkpoint binding differs")
        elif (
            self.legacy_reference is not None
            or self.repaired_receipt is not None
            or self.replaced_legacy_failure_reference is None
            or self.replaced_legacy_failure_reference.status != "failure"
            or (
                self.replaced_legacy_failure_reference.roots,
                self.replaced_legacy_failure_reference.execution_position,
                self.replaced_legacy_failure_reference.corpus_id,
                self.replaced_legacy_failure_reference.stream_id,
                self.replaced_legacy_failure_reference.policy_id,
            )
            != binding
            or not self.failure_type
            or not self.failure_detail
        ):
            raise ValueError("repaired failure checkpoint lacks exactly one preserved failure")
        digest = semantic_sha256(
            self,
            excluded_fields={"checkpoint_id", "content_sha256"},
        )
        if self.checkpoint_id != f"yfm11econcal-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("calibration checkpoint identity differs from content")
        return self


def build_gate3_calibration_attempt_checkpoint(
    *,
    protocol: EconomicResolutionProtocol,
    roots: Gate3RootBinding,
    execution_position: int,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    policy_id: Gate3BaselinePolicyId,
    legacy_reference: Gate3LegacyCalibrationAttemptReference | None = None,
    replaced_legacy_failure_reference: (Gate3LegacyCalibrationAttemptReference | None) = None,
    repaired_receipt: Gate3CalibrationObservationReceipt | None = None,
    failure_type: str | None = None,
    failure_detail: str | None = None,
) -> Gate3CalibrationAttemptCheckpoint:
    """Build exactly one legacy success, repaired success, or repaired failure."""

    outcomes = (
        legacy_reference is not None,
        repaired_receipt is not None,
        failure_type is not None or failure_detail is not None,
    )
    if sum(outcomes) != 1 or outcomes[2] and (not failure_type or not failure_detail):
        raise EconomicResolutionEvidenceError(
            "calibration checkpoint requires exactly one complete outcome"
        )
    expected_binding = (
        roots,
        execution_position,
        corpus_id,
        stream_id,
        policy_id,
    )
    if legacy_reference is not None:
        if replaced_legacy_failure_reference is not None:
            raise EconomicResolutionEvidenceError(
                "legacy success checkpoint cannot replace a legacy failure"
            )
        outcome_kind = "legacy_success_reference"
    elif repaired_receipt is not None:
        if repaired_receipt.source_lineage != "repaired_runtime":
            raise EconomicResolutionEvidenceError(
                "repaired receipt must use source_lineage=repaired_runtime"
            )
        if (
            replaced_legacy_failure_reference is None
            or replaced_legacy_failure_reference.status != "failure"
            or (
                replaced_legacy_failure_reference.roots,
                replaced_legacy_failure_reference.execution_position,
                replaced_legacy_failure_reference.corpus_id,
                replaced_legacy_failure_reference.stream_id,
                replaced_legacy_failure_reference.policy_id,
            )
            != expected_binding
        ):
            raise EconomicResolutionEvidenceError(
                "repaired checkpoint replaced legacy failure binding differs"
            )
        outcome_kind = "repaired_runtime_success"
    else:
        if (
            replaced_legacy_failure_reference is None
            or replaced_legacy_failure_reference.status != "failure"
            or (
                replaced_legacy_failure_reference.roots,
                replaced_legacy_failure_reference.execution_position,
                replaced_legacy_failure_reference.corpus_id,
                replaced_legacy_failure_reference.stream_id,
                replaced_legacy_failure_reference.policy_id,
            )
            != expected_binding
        ):
            raise EconomicResolutionEvidenceError(
                "repaired checkpoint replaced legacy failure binding differs"
            )
        outcome_kind = "repaired_runtime_failure"
    semantic = {
        "schema_version": "yieldforge.m11-economic-calibration-checkpoint.v1",
        "protocol": protocol.model_dump(mode="json"),
        "roots": roots.model_dump(mode="json"),
        "execution_position": execution_position,
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "policy_id": policy_id,
        "outcome_kind": outcome_kind,
        "legacy_reference": (
            legacy_reference.model_dump(mode="json") if legacy_reference else None
        ),
        "replaced_legacy_failure_reference": (
            replaced_legacy_failure_reference.model_dump(mode="json")
            if replaced_legacy_failure_reference
            else None
        ),
        "repaired_receipt": (
            repaired_receipt.model_dump(mode="json") if repaired_receipt else None
        ),
        "failure_type": failure_type,
        "failure_detail": failure_detail,
    }
    digest = semantic_sha256(semantic)
    try:
        return Gate3CalibrationAttemptCheckpoint(
            checkpoint_id=f"yfm11econcal-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            protocol=protocol,
            roots=roots,
            execution_position=execution_position,
            corpus_id=corpus_id,
            stream_id=stream_id,
            policy_id=policy_id,
            outcome_kind=outcome_kind,  # type: ignore[arg-type]
            legacy_reference=legacy_reference,
            replaced_legacy_failure_reference=replaced_legacy_failure_reference,
            repaired_receipt=repaired_receipt,
            failure_type=failure_type,
            failure_detail=failure_detail,
        )
    except (ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(str(error)) from error


def _checkpoint_filename(checkpoint: Gate3CalibrationAttemptCheckpoint) -> str:
    digest = checkpoint.content_sha256.removeprefix("sha256:")
    return f"m11-economic-calibration-checkpoint-{checkpoint.execution_position:03d}-{digest}.json"


def _read_bounded_regular_file(path: Path, *, max_bytes: int, label: str) -> bytes:
    candidate = Path(path)
    descriptor: int | None = None
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > max_bytes
        ):
            raise EconomicResolutionEvidenceError(
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
            raise EconomicResolutionEvidenceError(f"{label} identity changed during open")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        during = os.fstat(descriptor)
    except EconomicResolutionEvidenceError:
        raise
    except OSError as error:
        raise EconomicResolutionEvidenceError(f"{label} could not be read safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = candidate.lstat()
    except OSError as error:
        raise EconomicResolutionEvidenceError(f"{label} could not be re-inspected") from error
    if (
        len(raw) > max_bytes
        or len(raw) != before.st_size
        or _fingerprint(during) != before_fingerprint
        or _fingerprint(after) != before_fingerprint
    ):
        raise EconomicResolutionEvidenceError(f"{label} changed during read")
    return raw


def _parse_canonical_checkpoint(raw: bytes) -> Gate3CalibrationAttemptCheckpoint:
    if type(raw) is not bytes or len(raw) > _MAX_CHECKPOINT_BYTES:
        raise EconomicResolutionEvidenceError("calibration checkpoint exceeds byte bound")
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        checkpoint = Gate3CalibrationAttemptCheckpoint.model_validate_json(raw, strict=True)
    except EconomicResolutionEvidenceError:
        raise
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "calibration checkpoint failed strict validation"
        ) from error
    if canonical_pretty_json_bytes(checkpoint) != raw:
        raise EconomicResolutionEvidenceError("calibration checkpoint encoding is not canonical")
    return checkpoint


def publish_gate3_calibration_attempt_checkpoint(
    output_directory: Path,
    checkpoint: Gate3CalibrationAttemptCheckpoint,
) -> Path:
    """Publish one compact checkpoint immutably under its full semantic hash."""

    try:
        strict = Gate3CalibrationAttemptCheckpoint.model_validate(
            checkpoint.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "calibration checkpoint failed strict validation"
        ) from error
    raw = canonical_pretty_json_bytes(strict)
    if len(raw) > _MAX_CHECKPOINT_BYTES:
        raise EconomicResolutionEvidenceError("calibration checkpoint exceeds byte bound")
    destination = Path(output_directory) / _checkpoint_filename(strict)
    try:
        return publish_immutable_artifact(
            destination,
            raw,
            validate=lambda data: canonical_pretty_json_bytes(_parse_canonical_checkpoint(data)),
            label="M11 economic calibration checkpoint",
        )
    except M8ArtifactPublicationError as error:
        raise EconomicResolutionEvidenceError(
            "calibration checkpoint immutable publication failed"
        ) from error


def load_gate3_calibration_attempt_checkpoint(
    path: Path,
    *,
    expected_protocol: EconomicResolutionProtocol,
    expected_roots: Gate3RootBinding,
    expected_execution_position: int,
    expected_corpus_id: Gate3CorpusId,
    expected_stream_id: str,
    expected_policy_id: Gate3BaselinePolicyId,
    expected_checkpoint_id: str,
    expected_content_sha256: str,
) -> Gate3CalibrationAttemptCheckpoint:
    """Load a small canonical checkpoint only under every caller binding."""

    expected = (
        expected_protocol,
        expected_roots,
        expected_execution_position,
        expected_corpus_id,
        expected_stream_id,
        expected_policy_id,
        expected_checkpoint_id,
        expected_content_sha256,
    )
    if (
        type(expected_execution_position) is not int
        or not 0 <= expected_execution_position <= 95
        or type(expected_stream_id) is not str
        or not expected_stream_id
        or type(expected_checkpoint_id) is not str
        or re.fullmatch(r"yfm11econcal-[0-9a-f]{24}", expected_checkpoint_id) is None
        or type(expected_content_sha256) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_content_sha256) is None
    ):
        raise EconomicResolutionEvidenceError("checkpoint expected binding is malformed")
    candidate = Path(path)
    expected_name = (
        "m11-economic-calibration-checkpoint-"
        f"{expected_execution_position:03d}-"
        f"{expected_content_sha256.removeprefix('sha256:')}.json"
    )
    if candidate.name != expected_name:
        raise EconomicResolutionEvidenceError("checkpoint path differs from expected binding")
    raw = _read_bounded_regular_file(
        candidate,
        max_bytes=_MAX_CHECKPOINT_BYTES,
        label="calibration checkpoint",
    )
    checkpoint = _parse_canonical_checkpoint(raw)
    actual = (
        checkpoint.protocol,
        checkpoint.roots,
        checkpoint.execution_position,
        checkpoint.corpus_id,
        checkpoint.stream_id,
        checkpoint.policy_id,
        checkpoint.checkpoint_id,
        checkpoint.content_sha256,
    )
    if actual != expected:
        raise EconomicResolutionEvidenceError(
            "calibration checkpoint differs from expected binding"
        )
    return checkpoint


build_gate3_calibration_checkpoint = build_gate3_calibration_attempt_checkpoint
publish_gate3_calibration_checkpoint = publish_gate3_calibration_attempt_checkpoint
load_gate3_calibration_checkpoint = load_gate3_calibration_attempt_checkpoint


def _discover_single_prefixed_regular_file(
    output_directory: Path,
    *,
    prefix: str,
    filename_pattern: re.Pattern[str],
    max_bytes: int,
    label: str,
) -> tuple[Path, re.Match[str]] | None:
    directory = Path(output_directory)
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise EconomicResolutionEvidenceError(
            f"{label} directory could not be inspected"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise EconomicResolutionEvidenceError(f"{label} directory must be a non-symlink directory")
    candidate: tuple[Path, re.Match[str]] | None = None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.name.startswith(prefix):
                    continue
                match = filename_pattern.fullmatch(entry.name)
                if match is None:
                    raise EconomicResolutionEvidenceError(f"{label} has a malformed prefixed entry")
                entry_metadata = entry.stat(follow_symlinks=False)
                if (
                    stat.S_ISLNK(entry_metadata.st_mode)
                    or not stat.S_ISREG(entry_metadata.st_mode)
                    or entry_metadata.st_size > max_bytes
                ):
                    raise EconomicResolutionEvidenceError(
                        f"{label} candidate must be a bounded regular non-symlink file"
                    )
                if candidate is not None:
                    raise EconomicResolutionEvidenceError(
                        f"{label} has competing prefixed candidates"
                    )
                candidate = Path(entry.path), match
    except EconomicResolutionEvidenceError:
        raise
    except OSError as error:
        raise EconomicResolutionEvidenceError(f"{label} directory scan failed safely") from error
    return candidate


def discover_gate3_calibration_attempt_checkpoint(
    output_directory: Path,
    *,
    protocol: EconomicResolutionProtocol,
    legacy_reference: Gate3LegacyCalibrationAttemptReference,
) -> tuple[Path, Gate3CalibrationAttemptCheckpoint] | None:
    """Discover one exact resumable checkpoint, failing closed on ambiguity."""

    try:
        strict_protocol = EconomicResolutionProtocol.model_validate(
            protocol.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        strict_reference = Gate3LegacyCalibrationAttemptReference.model_validate(
            legacy_reference.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "checkpoint discovery inputs failed strict validation"
        ) from error
    if (
        strict_protocol != build_economic_resolution_protocol()
        or strict_reference.roots.content_sha256 != strict_protocol.legacy_root_content_sha256
    ):
        raise EconomicResolutionEvidenceError("checkpoint discovery lineage binding differs")
    prefix = f"m11-economic-calibration-checkpoint-{strict_reference.execution_position:03d}-"
    discovered = _discover_single_prefixed_regular_file(
        output_directory,
        prefix=prefix,
        filename_pattern=re.compile(rf"{re.escape(prefix)}([0-9a-f]{{64}})\.json"),
        max_bytes=_MAX_CHECKPOINT_BYTES,
        label="calibration checkpoint discovery",
    )
    if discovered is None:
        return None
    path, filename_match = discovered
    content_digest = filename_match.group(1)
    checkpoint = load_gate3_calibration_attempt_checkpoint(
        path,
        expected_protocol=strict_protocol,
        expected_roots=strict_reference.roots,
        expected_execution_position=strict_reference.execution_position,
        expected_corpus_id=strict_reference.corpus_id,
        expected_stream_id=strict_reference.stream_id,
        expected_policy_id=strict_reference.policy_id,
        expected_checkpoint_id=f"yfm11econcal-{content_digest[:24]}",
        expected_content_sha256=f"sha256:{content_digest}",
    )
    if strict_reference.status == "success":
        if (
            checkpoint.outcome_kind != "legacy_success_reference"
            or checkpoint.legacy_reference != strict_reference
        ):
            raise EconomicResolutionEvidenceError("discovered checkpoint legacy reference differs")
    elif (
        checkpoint.outcome_kind not in {"repaired_runtime_success", "repaired_runtime_failure"}
        or checkpoint.replaced_legacy_failure_reference != strict_reference
    ):
        raise EconomicResolutionEvidenceError(
            "discovered checkpoint replaced legacy failure reference differs"
        )
    return path, checkpoint


def _checkpoint_cost_and_openings(
    checkpoint: Gate3CalibrationAttemptCheckpoint,
) -> tuple[Gate3CostLedger, int] | None:
    if checkpoint.outcome_kind == "legacy_success_reference":
        reference = checkpoint.legacy_reference
        if reference is None or reference.final_costs is None:
            return None
        return reference.final_costs, reference.full_sheet_opening_count  # type: ignore[return-value]
    if checkpoint.outcome_kind == "repaired_runtime_success":
        receipt = checkpoint.repaired_receipt
        if receipt is None:
            return None
        return receipt.final_costs, receipt.full_sheet_opening_count
    return None


def _rederive_checkpoint_freezes(
    *,
    roots: Gate3RootBinding,
    checkpoints: tuple[Gate3CalibrationAttemptCheckpoint, ...],
    stream_census: tuple[tuple[Gate3CorpusId, tuple[str, ...]], ...],
) -> tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze]:
    freezes: list[Gate3BaselineCalibrationFreeze] = []
    for corpus_id, stream_ids in stream_census:
        per_corpus = tuple(item for item in checkpoints if item.corpus_id == corpus_id)
        costs: dict[str, tuple[str, ...]] = {}
        openings: dict[str, tuple[int, ...]] = {}
        for policy_id in GATE3_BASELINE_POLICY_IDS:
            outcomes = tuple(
                _checkpoint_cost_and_openings(item)
                for item in per_corpus
                if item.policy_id == policy_id
            )
            if len(outcomes) != 8 or any(item is None for item in outcomes):
                raise ValueError("complete calibration freeze requires eight successes per policy")
            successful = tuple(item for item in outcomes if item is not None)
            costs[policy_id] = tuple(item[0].net_cost for item in successful)
            openings[policy_id] = tuple(item[1] for item in successful)
        freezes.append(
            select_gate3_baseline_policy(
                roots=roots,
                corpus_id=corpus_id,
                calibration_stream_ids=stream_ids,
                policy_stream_costs=costs,
                policy_stream_sheet_openings=openings,
                policy_invalid_stream_counts={
                    policy_id: 0 for policy_id in GATE3_BASELINE_POLICY_IDS
                },
            )
        )
    if len(freezes) != 2:
        raise ValueError("complete calibration requires both corpus freezes")
    return freezes[0], freezes[1]


class Gate3CalibrationManifest(FrozenExperimentModel):
    """Complete compact 96-attempt calibration state and optional freezes."""

    schema_version: Literal["yieldforge.m11-economic-calibration-manifest.v1"] = (
        "yieldforge.m11-economic-calibration-manifest.v1"
    )
    manifest_id: StrictStr = Field(pattern=r"^yfm11econcalman-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protocol: EconomicResolutionProtocol
    roots: Gate3RootBinding
    legacy_scan_id: StrictStr = Field(pattern=r"^yfm11econscan-[0-9a-f]{24}$")
    legacy_scan_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    legacy_scan: Gate3LegacyCalibrationScan
    calibration_stream_census: tuple[
        tuple[Gate3CorpusId, tuple[StrictStr, ...]],
        tuple[Gate3CorpusId, tuple[StrictStr, ...]],
    ]
    checkpoints: tuple[Gate3CalibrationAttemptCheckpoint, ...] = Field(
        min_length=96,
        max_length=96,
    )
    success_count: StrictInt = Field(ge=0, le=96)
    failure_count: StrictInt = Field(ge=0, le=96)
    status: Literal["complete_valid", "complete_invalid"]
    baseline_freezes: tuple[Gate3BaselineCalibrationFreeze, ...] = Field(max_length=2)
    complete: Literal[True] = True

    @model_validator(mode="after")
    def require_complete_rederivation_and_identity(self) -> Self:
        if self.protocol != build_economic_resolution_protocol():
            raise ValueError("calibration manifest protocol binding differs")
        if self.roots.content_sha256 != self.protocol.legacy_root_content_sha256:
            raise ValueError("calibration manifest root binding differs")
        if (
            self.legacy_scan_id != self.legacy_scan.scan_id
            or self.legacy_scan_content_sha256 != self.legacy_scan.content_sha256
            or self.legacy_scan.protocol != self.protocol
            or self.legacy_scan.roots != self.roots
        ):
            raise ValueError("calibration manifest legacy scan binding differs")
        checkpoints = self.checkpoints
        if tuple(item.execution_position for item in checkpoints) != tuple(range(96)):
            raise ValueError("calibration manifest checkpoint order differs")
        if any(item.protocol != self.protocol or item.roots != self.roots for item in checkpoints):
            raise ValueError("calibration manifest checkpoint binding differs")
        for checkpoint, reference in zip(
            checkpoints,
            self.legacy_scan.attempt_references,
            strict=True,
        ):
            if checkpoint.outcome_kind == "legacy_success_reference":
                if reference.status != "success" or checkpoint.legacy_reference != reference:
                    raise ValueError(
                        "calibration manifest legacy success reference differs from scan"
                    )
            elif (
                reference.status != "failure"
                or checkpoint.replaced_legacy_failure_reference != reference
            ):
                raise ValueError(
                    "calibration manifest replaced legacy reference differs from legacy scan"
                )
        lectra_streams = tuple(item.stream_id for item in checkpoints[:8])
        loco_streams = tuple(item.stream_id for item in checkpoints[48:56])
        expected_census = (
            ("lectra-m3-m4", lectra_streams),
            ("loco-2dics", loco_streams),
        )
        if self.calibration_stream_census != expected_census or any(
            len(streams) != 8 or len(set(streams)) != 8 for _, streams in expected_census
        ):
            raise ValueError("calibration manifest repeated stream census differs")
        expected_order = tuple(
            (corpus_id, stream_id, policy_id)
            for corpus_id, streams in expected_census
            for policy_id in GATE3_BASELINE_POLICY_IDS
            for stream_id in streams
        )
        actual_order = tuple(
            (item.corpus_id, item.stream_id, item.policy_id) for item in checkpoints
        )
        if actual_order != expected_order:
            raise ValueError("calibration manifest corpus policy stream order differs")
        success_count = sum(item.outcome_kind != "repaired_runtime_failure" for item in checkpoints)
        failure_count = 96 - success_count
        if (self.success_count, self.failure_count) != (success_count, failure_count):
            raise ValueError("calibration manifest success/failure counts differ")
        if failure_count:
            if self.status != "complete_invalid" or self.baseline_freezes:
                raise ValueError("invalid calibration manifest cannot publish freezes")
        else:
            expected_freezes = _rederive_checkpoint_freezes(
                roots=self.roots,
                checkpoints=checkpoints,
                stream_census=expected_census,
            )
            if self.status != "complete_valid" or self.baseline_freezes != expected_freezes:
                raise ValueError("valid calibration manifest freezes differ from ledgers")
        digest = semantic_sha256(
            self,
            excluded_fields={"manifest_id", "content_sha256"},
        )
        if self.manifest_id != f"yfm11econcalman-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("calibration manifest identity differs from content")
        return self


def build_gate3_calibration_manifest(
    checkpoints: tuple[Gate3CalibrationAttemptCheckpoint, ...],
    *,
    legacy_scan: Gate3LegacyCalibrationScan,
) -> Gate3CalibrationManifest:
    """Build a terminal compact calibration manifest without imputation."""

    if type(checkpoints) is not tuple or len(checkpoints) != 96:
        raise EconomicResolutionEvidenceError(
            "calibration manifest requires exactly 96 checkpoints"
        )
    if tuple(item.execution_position for item in checkpoints) != tuple(range(96)):
        raise EconomicResolutionEvidenceError("calibration manifest checkpoint order differs")
    protocol = checkpoints[0].protocol
    roots = checkpoints[0].roots
    try:
        strict_legacy_scan = Gate3LegacyCalibrationScan.model_validate(
            legacy_scan.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "calibration manifest legacy scan failed strict validation"
        ) from error
    if strict_legacy_scan.protocol != protocol or strict_legacy_scan.roots != roots:
        raise EconomicResolutionEvidenceError("calibration manifest legacy scan binding differs")
    census: tuple[tuple[Gate3CorpusId, tuple[str, ...]], ...] = (
        ("lectra-m3-m4", tuple(item.stream_id for item in checkpoints[:8])),
        ("loco-2dics", tuple(item.stream_id for item in checkpoints[48:56])),
    )
    success_count = sum(item.outcome_kind != "repaired_runtime_failure" for item in checkpoints)
    failure_count = 96 - success_count
    status = "complete_invalid" if failure_count else "complete_valid"
    try:
        freezes = (
            ()
            if failure_count
            else _rederive_checkpoint_freezes(
                roots=roots,
                checkpoints=checkpoints,
                stream_census=census,
            )
        )
        semantic = {
            "schema_version": "yieldforge.m11-economic-calibration-manifest.v1",
            "protocol": protocol.model_dump(mode="json"),
            "roots": roots.model_dump(mode="json"),
            "legacy_scan_id": strict_legacy_scan.scan_id,
            "legacy_scan_content_sha256": strict_legacy_scan.content_sha256,
            "legacy_scan": strict_legacy_scan.model_dump(mode="json"),
            "calibration_stream_census": census,
            "checkpoints": [item.model_dump(mode="json") for item in checkpoints],
            "success_count": success_count,
            "failure_count": failure_count,
            "status": status,
            "baseline_freezes": [item.model_dump(mode="json") for item in freezes],
            "complete": True,
        }
        digest = semantic_sha256(semantic)
        return Gate3CalibrationManifest(
            manifest_id=f"yfm11econcalman-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            protocol=protocol,
            roots=roots,
            legacy_scan_id=strict_legacy_scan.scan_id,
            legacy_scan_content_sha256=strict_legacy_scan.content_sha256,
            legacy_scan=strict_legacy_scan,
            calibration_stream_census=census,  # type: ignore[arg-type]
            checkpoints=checkpoints,
            success_count=success_count,
            failure_count=failure_count,
            status=status,  # type: ignore[arg-type]
            baseline_freezes=freezes,
        )
    except (ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(str(error)) from error


def _manifest_filename(manifest: Gate3CalibrationManifest) -> str:
    return (
        f"m11-economic-calibration-manifest-{manifest.content_sha256.removeprefix('sha256:')}.json"
    )


def _parse_canonical_manifest(raw: bytes) -> Gate3CalibrationManifest:
    if type(raw) is not bytes or len(raw) > _MAX_MANIFEST_BYTES:
        raise EconomicResolutionEvidenceError("calibration manifest exceeds byte bound")
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        manifest = Gate3CalibrationManifest.model_validate_json(raw, strict=True)
    except EconomicResolutionEvidenceError:
        raise
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "calibration manifest failed strict validation"
        ) from error
    if canonical_pretty_json_bytes(manifest) != raw:
        raise EconomicResolutionEvidenceError("calibration manifest encoding is not canonical")
    return manifest


def publish_gate3_calibration_manifest(
    output_directory: Path,
    manifest: Gate3CalibrationManifest,
) -> Path:
    """Publish the complete compact calibration manifest immutably."""

    try:
        strict = Gate3CalibrationManifest.model_validate(
            manifest.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "calibration manifest failed strict validation"
        ) from error
    raw = canonical_pretty_json_bytes(strict)
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise EconomicResolutionEvidenceError("calibration manifest exceeds byte bound")
    destination = Path(output_directory) / _manifest_filename(strict)
    try:
        return publish_immutable_artifact(
            destination,
            raw,
            validate=lambda data: canonical_pretty_json_bytes(_parse_canonical_manifest(data)),
            label="M11 economic calibration manifest",
        )
    except M8ArtifactPublicationError as error:
        raise EconomicResolutionEvidenceError(
            "calibration manifest immutable publication failed"
        ) from error


def load_gate3_calibration_manifest(
    path: Path,
    *,
    expected_protocol: EconomicResolutionProtocol,
    expected_roots: Gate3RootBinding,
    expected_manifest_id: str,
    expected_content_sha256: str,
) -> Gate3CalibrationManifest:
    """Load a complete manifest only under exact immutable bindings."""

    if (
        type(expected_manifest_id) is not str
        or re.fullmatch(r"yfm11econcalman-[0-9a-f]{24}", expected_manifest_id) is None
        or type(expected_content_sha256) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_content_sha256) is None
    ):
        raise EconomicResolutionEvidenceError("manifest expected binding is malformed")
    candidate = Path(path)
    expected_name = (
        f"m11-economic-calibration-manifest-{expected_content_sha256.removeprefix('sha256:')}.json"
    )
    if candidate.name != expected_name:
        raise EconomicResolutionEvidenceError("manifest path differs from expected binding")
    raw = _read_bounded_regular_file(
        candidate,
        max_bytes=_MAX_MANIFEST_BYTES,
        label="calibration manifest",
    )
    manifest = _parse_canonical_manifest(raw)
    if (
        manifest.protocol,
        manifest.roots,
        manifest.manifest_id,
        manifest.content_sha256,
    ) != (
        expected_protocol,
        expected_roots,
        expected_manifest_id,
        expected_content_sha256,
    ):
        raise EconomicResolutionEvidenceError("calibration manifest differs from expected binding")
    return manifest


def discover_gate3_calibration_manifest(
    output_directory: Path,
    *,
    protocol: EconomicResolutionProtocol,
    legacy_scan: Gate3LegacyCalibrationScan,
    checkpoints: tuple[Gate3CalibrationAttemptCheckpoint, ...],
) -> tuple[Path, Gate3CalibrationManifest] | None:
    """Discover the sole manifest and require exact rederivation from checkpoints."""

    try:
        strict_protocol = EconomicResolutionProtocol.model_validate(
            protocol.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        strict_scan = Gate3LegacyCalibrationScan.model_validate(
            legacy_scan.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise EconomicResolutionEvidenceError(
            "manifest discovery inputs failed strict validation"
        ) from error
    expected_manifest = build_gate3_calibration_manifest(
        checkpoints,
        legacy_scan=strict_scan,
    )
    if (
        strict_protocol != build_economic_resolution_protocol()
        or expected_manifest.protocol != strict_protocol
        or strict_scan.protocol != strict_protocol
    ):
        raise EconomicResolutionEvidenceError("manifest discovery lineage binding differs")
    prefix = "m11-economic-calibration-manifest-"
    discovered = _discover_single_prefixed_regular_file(
        output_directory,
        prefix=prefix,
        filename_pattern=re.compile(rf"{re.escape(prefix)}([0-9a-f]{{64}})\.json"),
        max_bytes=_MAX_MANIFEST_BYTES,
        label="calibration manifest discovery",
    )
    if discovered is None:
        return None
    path, filename_match = discovered
    content_digest = filename_match.group(1)
    manifest = load_gate3_calibration_manifest(
        path,
        expected_protocol=strict_protocol,
        expected_roots=expected_manifest.roots,
        expected_manifest_id=f"yfm11econcalman-{content_digest[:24]}",
        expected_content_sha256=f"sha256:{content_digest}",
    )
    if manifest != expected_manifest:
        raise EconomicResolutionEvidenceError(
            "discovered calibration manifest differs from rederived state"
        )
    return path, manifest
