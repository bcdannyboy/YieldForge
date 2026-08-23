"""Verified M2 evidence bridge and canonical M3 residual evaluation."""

from __future__ import annotations

import json
import math
import os
import secrets
import stat
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Literal, Self

import shapely
from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from yieldforge.archive import batch_content_hash
from yieldforge.datasets.passive_report import PassiveEvidenceError, decode_strict_json_bytes
from yieldforge.domain import (
    Candidate,
    CandidateBatch,
    ProjectionMode,
    SolverIdentity,
    SourceTaskBinding,
    SpyrrowRunConfig,
    StripPackingProblem,
)
from yieldforge.experiments.calibration import (
    CalibrationAttemptOutcome,
    CalibrationCandidateObservation,
    GeometryConfirmationResult,
)
from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    M0ExperimentContract,
    semantic_sha256,
)
from yieldforge.residuals.contracts import (
    CandidateResidualObservation,
    ResidualGeometryConfig,
    ResidualGeometryError,
    ResidualPairComparison,
    ResidualRuleName,
    rule_set_from_m0,
)
from yieldforge.residuals.geometry import (
    CandidateResidualExtraction,
    compare_candidate_residuals,
    extract_candidate_residual,
)

_MAX_ARCHIVE_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_ARCHIVE_CANDIDATES_BYTES = 256 * 1024 * 1024
_MAX_CANDIDATE_RECORD_BYTES = 4 * 1024 * 1024
_MAX_M3_INPUT_BYTES = 128 * 1024 * 1024
_MAX_M3_RESULT_BYTES = 128 * 1024 * 1024
_PRIMARY_ENVELOPE_PERCENT = 0.5


class M3EvidenceError(ValueError):
    """M2 evidence could not be trusted for the M3 replay."""


class _CandidateArchiveManifest(FrozenExperimentModel):
    schema_version: Literal["yieldforge.candidate-archive.v1"]
    candidate_count: StrictInt = Field(ge=0)
    batch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    problem: StripPackingProblem
    solver: SolverIdentity
    config: SpyrrowRunConfig
    source_task_binding: SourceTaskBinding


class VerifiedCandidateArchive(FrozenExperimentModel):
    """A reconstructed archive bound to its M2 job evidence."""

    job_id: StrictStr = Field(min_length=1)
    batch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    batch: CandidateBatch
    source_task_binding: SourceTaskBinding


class M3ArchiveEvidence(FrozenExperimentModel):
    """Portable identity of one verified ordinary M2 archive."""

    seed: StrictInt = Field(ge=0)
    job_id: StrictStr = Field(min_length=1)
    batch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_count: StrictInt = Field(ge=0)


class M3SelectedCandidate(FrozenExperimentModel):
    """One selected full candidate and its verified archive provenance."""

    seed: StrictInt = Field(ge=0)
    archive_job_id: StrictStr = Field(min_length=1)
    archive_batch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    candidate: Candidate


class M3TaskPair(FrozenExperimentModel):
    """One residual-blind candidate pair for a qualifying M2 task."""

    tasks_index: StrictInt = Field(ge=0)
    source_task_binding: SourceTaskBinding
    problem: StripPackingProblem
    archives: tuple[M3ArchiveEvidence, ...] = Field(min_length=1)
    best_width: StrictFloat = Field(gt=0)
    envelope_max_width: StrictFloat = Field(gt=0)
    selected_candidates: tuple[M3SelectedCandidate, M3SelectedCandidate]

    @model_validator(mode="after")
    def require_stable_pair(self) -> Self:
        seeds = tuple(item.seed for item in self.archives)
        if seeds != tuple(sorted(set(seeds))):
            raise ValueError("task archive evidence must use sorted unique seeds")
        identifiers = tuple(item.candidate.candidate_id for item in self.selected_candidates)
        if len(set(identifiers)) != 2:
            raise ValueError("task pair requires two distinct candidate IDs")
        if self.source_task_binding.tasks_index != self.tasks_index:
            raise ValueError("task pair source binding does not match its task index")
        return self


class M3ResidualInputPack(FrozenExperimentModel):
    """Content-addressed portable input for the exact M3 replay."""

    schema_version: Literal["yieldforge.m3-residual-input.v1"] = "yieldforge.m3-residual-input.v1"
    input_id: StrictStr = Field(pattern=r"^yfgi-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m2_result_id: StrictStr = Field(pattern=r"^yfgfr-[0-9a-f]{24}$")
    m2_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    shapely_version: StrictStr = Field(min_length=1)
    selection_rule: Literal[
        "first_two_distinct_by_width_then_candidate_id_within_m2_0_5_percent_envelope"
    ] = "first_two_distinct_by_width_then_candidate_id_within_m2_0_5_percent_envelope"
    primary_geometry_config: ResidualGeometryConfig
    expected_task_ids: tuple[StrictInt, ...] = Field(min_length=1)
    task_pairs: tuple[M3TaskPair, ...] = Field(min_length=1)
    claim_ceiling: Literal[
        "residual_geometry_input_only_not_remnant_reuse_savings_or_physical_process_evidence"
    ] = "residual_geometry_input_only_not_remnant_reuse_savings_or_physical_process_evidence"

    @field_validator("expected_task_ids")
    @classmethod
    def require_sorted_unique_task_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("expected M3 task IDs must be sorted and unique")
        return value

    @model_validator(mode="after")
    def require_complete_content_addressed_input(self) -> Self:
        if tuple(pair.tasks_index for pair in self.task_pairs) != self.expected_task_ids:
            raise ValueError("M3 task pairs do not cover the expected task IDs in order")
        if self.primary_geometry_config.part_buffer_distance != 0.0:
            raise ValueError("primary M3 input must use zero process buffer")
        if self.primary_geometry_config.forbidden_polygons:
            raise ValueError("primary M3 input must not invent forbidden regions")
        digest = semantic_sha256(
            self,
            excluded_fields={"input_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M3 input content SHA-256 does not match semantic content")
        if self.input_id != f"yfgi-{digest[:24]}":
            raise ValueError("M3 input ID does not match semantic content")
        return self


class M3TaskResult(FrozenExperimentModel):
    """One complete pair evaluation or one visible fail-closed task result."""

    tasks_index: StrictInt = Field(ge=0)
    first_candidate_id: StrictStr = Field(min_length=1)
    second_candidate_id: StrictStr = Field(min_length=1)
    valid: StrictBool
    error_code: StrictStr | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    first_observation: CandidateResidualObservation
    second_observation: CandidateResidualObservation
    comparison: ResidualPairComparison | None = None

    @model_validator(mode="after")
    def require_consistent_task_evidence(self) -> Self:
        if self.first_observation.candidate_id != self.first_candidate_id:
            raise ValueError("first residual observation changes candidate identity")
        if self.second_observation.candidate_id != self.second_candidate_id:
            raise ValueError("second residual observation changes candidate identity")
        if self.valid:
            if (
                self.error_code is not None
                or not self.first_observation.valid
                or not self.second_observation.valid
                or self.comparison is None
            ):
                raise ValueError("valid M3 task result requires two observations and comparison")
        elif (
            self.error_code is None
            or (self.first_observation.valid and self.second_observation.valid)
            or self.comparison is not None
        ):
            raise ValueError("invalid M3 task result must preserve one geometry failure")
        return self


class M3RuleDifferenceSummary(FrozenExperimentModel):
    rule_name: ResidualRuleName
    difference_count: StrictInt = Field(ge=0)
    difference_percent: StrictFloat = Field(ge=0, le=100)


class M3FailureSummary(FrozenExperimentModel):
    error_code: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    task_count: StrictInt = Field(gt=0)


class M3Summary(FrozenExperimentModel):
    """Recomputed aggregate over the complete registered M3 task population."""

    registered_task_count: StrictInt = Field(gt=0)
    evaluated_task_count: StrictInt = Field(ge=0)
    valid_task_count: StrictInt = Field(ge=0)
    failure_count: StrictInt = Field(ge=0)
    failures: tuple[M3FailureSummary, ...]
    exact_residual_difference_count: StrictInt = Field(ge=0)
    exact_residual_difference_percent: StrictFloat = Field(ge=0, le=100)
    classification_differences: tuple[M3RuleDifferenceSummary, ...]
    maximum_reconciliation_delta: StrictFloat = Field(ge=0)
    symmetric_difference_sheet_fraction_mean: StrictFloat = Field(ge=0, le=1)
    symmetric_difference_sheet_fraction_median: StrictFloat = Field(ge=0, le=1)
    symmetric_difference_sheet_fraction_p95: StrictFloat = Field(ge=0, le=1)
    symmetric_difference_sheet_fraction_maximum: StrictFloat = Field(ge=0, le=1)
    technical_decision: Literal["pass", "fail"]


class M3ResidualGeometryResult(FrozenExperimentModel):
    """Canonical bounded result of the exact M3 residual replay."""

    schema_version: Literal["yieldforge.m3-residual-result.v1"] = "yieldforge.m3-residual-result.v1"
    result_id: StrictStr = Field(pattern=r"^yfgr-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_id: StrictStr = Field(pattern=r"^yfgi-[0-9a-f]{24}$")
    input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m2_result_id: StrictStr = Field(pattern=r"^yfgfr-[0-9a-f]{24}$")
    m2_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    shapely_version: StrictStr = Field(min_length=1)
    expected_task_ids: tuple[StrictInt, ...] = Field(min_length=1)
    task_results: tuple[M3TaskResult, ...] = Field(min_length=1)
    summary: M3Summary
    claim_ceiling: Literal[
        "exact_residual_geometry_only_not_future_reuse_material_savings_roi_"
        "or_physical_process_evidence"
    ] = (
        "exact_residual_geometry_only_not_future_reuse_material_savings_roi_"
        "or_physical_process_evidence"
    )

    @model_validator(mode="after")
    def require_recomputed_content(self) -> Self:
        if tuple(item.tasks_index for item in self.task_results) != self.expected_task_ids:
            raise ValueError("M3 task results do not cover expected task IDs in order")
        if recompute_m3_summary(self.task_results, len(self.expected_task_ids)) != self.summary:
            raise ValueError("M3 result summary does not match task evidence")
        digest = semantic_sha256(
            self,
            excluded_fields={"result_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M3 result content SHA-256 does not match semantic content")
        if self.result_id != f"yfgr-{digest[:24]}":
            raise ValueError("M3 result ID does not match semantic content")
        return self


def _read_regular_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise M3EvidenceError(f"{label} could not be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise M3EvidenceError(f"{label} must be a regular file and not a symlink")
    if metadata.st_size > max_bytes:
        raise M3EvidenceError(f"{label} exceeds its byte limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(max_bytes + 1)
    except OSError as error:
        raise M3EvidenceError(f"{label} could not be read safely") from error
    if len(data) > max_bytes:
        raise M3EvidenceError(f"{label} exceeds its byte limit")
    return data


def _load_manifest(path: Path) -> _CandidateArchiveManifest:
    payload = _read_regular_file(
        path,
        label="candidate archive manifest",
        max_bytes=_MAX_ARCHIVE_MANIFEST_BYTES,
    )
    try:
        decoded = decode_strict_json_bytes(
            payload,
            label="candidate archive manifest",
            max_bytes=_MAX_ARCHIVE_MANIFEST_BYTES,
        )
        return _CandidateArchiveManifest.model_validate_json(
            json.dumps(decoded, allow_nan=False), strict=True
        )
    except (PassiveEvidenceError, ValidationError) as error:
        raise M3EvidenceError("candidate archive manifest is invalid") from error


def _load_candidates(path: Path) -> tuple[Candidate, ...]:
    payload = _read_regular_file(
        path,
        label="candidate archive JSONL",
        max_bytes=_MAX_ARCHIVE_CANDIDATES_BYTES,
    )
    if payload and not payload.endswith(b"\n"):
        raise M3EvidenceError("candidate JSONL must end with a newline")
    records = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line:
            raise M3EvidenceError("candidate JSONL contains a blank record")
        try:
            decoded = decode_strict_json_bytes(
                line,
                label=f"candidate JSONL record {line_number}",
                max_bytes=_MAX_CANDIDATE_RECORD_BYTES,
            )
            records.append(
                Candidate.model_validate_json(json.dumps(decoded, allow_nan=False), strict=True)
            )
        except (PassiveEvidenceError, ValidationError) as error:
            raise M3EvidenceError("candidate JSONL contains an invalid record") from error
    return tuple(records)


def _candidate_observations(
    candidates: tuple[Candidate, ...],
) -> tuple[CalibrationCandidateObservation, ...]:
    return tuple(
        CalibrationCandidateObservation(
            candidate_id=candidate.candidate_id,
            width=candidate.width,
            density=candidate.density,
        )
        for candidate in candidates
    )


def load_verified_candidate_archive(
    archive_path: Path,
    *,
    job_id: str,
    expected_batch_sha256: str,
    expected_candidates: tuple[CalibrationCandidateObservation, ...],
    expected_tasks_index: int,
    expected_seed: int,
    expected_problem: StripPackingProblem | None = None,
) -> VerifiedCandidateArchive:
    """Reconstruct one archive and bind every material field to canonical M2 evidence."""

    archive = Path(archive_path)
    try:
        metadata = archive.lstat()
    except OSError as error:
        raise M3EvidenceError("candidate archive directory could not be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise M3EvidenceError("candidate archive must be a directory and not a symlink")

    manifest = _load_manifest(archive / "manifest.json")
    candidates = _load_candidates(archive / "candidates.jsonl")
    if manifest.candidate_count != len(candidates):
        raise M3EvidenceError("candidate archive candidate count does not match JSONL")
    if manifest.batch_sha256 != expected_batch_sha256:
        raise M3EvidenceError("candidate archive batch SHA-256 does not match M2 evidence")
    if _candidate_observations(candidates) != expected_candidates:
        raise M3EvidenceError("candidate archive does not match M2 candidate observations")
    if expected_problem is not None and manifest.problem != expected_problem:
        raise M3EvidenceError("candidate archive projected problem does not match its task")
    binding = manifest.source_task_binding
    projection = binding.solver_projection
    if (
        binding.tasks_index != expected_tasks_index
        or projection is None
        or projection.mode is not ProjectionMode.SOURCE_AS_RECORDED
        or projection.intervention_codes
    ):
        raise M3EvidenceError("candidate archive source projection binding is invalid")
    config = manifest.config
    if (
        config.seed != expected_seed
        or config.total_computation_time != 10
        or config.early_termination
        or config.num_workers != 1
        or config.min_items_separation is not None
    ):
        raise M3EvidenceError("candidate archive solver configuration is not ordinary M2")

    batch = CandidateBatch(
        problem=manifest.problem,
        solver=manifest.solver,
        config=manifest.config,
        candidates=list(candidates),
    )
    if batch_content_hash(batch) != manifest.batch_sha256:
        raise M3EvidenceError("candidate archive batch SHA-256 failed recomputation")
    return VerifiedCandidateArchive(
        job_id=job_id,
        batch_sha256=manifest.batch_sha256,
        batch=batch,
        source_task_binding=binding,
    )


def _candidate_geometry_content(candidate: Candidate) -> dict[str, object]:
    """Return candidate content that its canonical geometry ID is expected to identify."""

    payload = candidate.model_dump(mode="json")
    payload.pop("seed", None)
    payload.pop("report_type", None)
    return payload


def build_m3_task_pair(
    *,
    tasks_index: int,
    archives: tuple[VerifiedCandidateArchive, ...],
) -> M3TaskPair:
    """Select the frozen residual-blind pair across verified ordinary archives."""

    if not archives:
        raise M3EvidenceError("M3 task selection requires at least one verified archive")
    ordered_archives = tuple(sorted(archives, key=lambda item: item.batch.config.seed))
    seeds = tuple(item.batch.config.seed for item in ordered_archives)
    if len(seeds) != len(set(seeds)):
        raise M3EvidenceError("M3 task archives repeat a solver seed")
    problem = ordered_archives[0].batch.problem
    binding = ordered_archives[0].source_task_binding
    if binding.tasks_index != tasks_index:
        raise M3EvidenceError("M3 task archive binding does not match its task index")
    for archive in ordered_archives:
        if archive.batch.problem != problem:
            raise M3EvidenceError("M3 task archives do not share one projected problem")
        if archive.source_task_binding != binding:
            raise M3EvidenceError("M3 task archives do not share one source projection binding")

    unique: dict[str, tuple[Candidate, VerifiedCandidateArchive]] = {}
    for archive in ordered_archives:
        for candidate in archive.batch.candidates:
            existing = unique.get(candidate.candidate_id)
            if existing is not None:
                if _candidate_geometry_content(existing[0]) != _candidate_geometry_content(
                    candidate
                ):
                    raise M3EvidenceError(
                        "one canonical candidate ID has conflicting geometry across archives"
                    )
                continue
            unique[candidate.candidate_id] = (candidate, archive)
    if not unique:
        raise M3EvidenceError("M3 task archives contain no candidates")

    best_width = min(candidate.width for candidate, _archive in unique.values())
    envelope_max_width = best_width * (1 + _PRIMARY_ENVELOPE_PERCENT / 100.0)
    eligible = sorted(
        (
            (candidate, archive)
            for candidate, archive in unique.values()
            if candidate.width <= envelope_max_width
        ),
        key=lambda item: (item[0].width, item[0].candidate_id),
    )
    if len(eligible) < 2:
        raise M3EvidenceError("M3 task requires two distinct candidates inside the frozen envelope")

    selected = tuple(
        M3SelectedCandidate(
            seed=archive.batch.config.seed,
            archive_job_id=archive.job_id,
            archive_batch_sha256=archive.batch_sha256,
            candidate=candidate,
        )
        for candidate, archive in eligible[:2]
    )
    archive_evidence = tuple(
        M3ArchiveEvidence(
            seed=archive.batch.config.seed,
            job_id=archive.job_id,
            batch_sha256=archive.batch_sha256,
            candidate_count=len(archive.batch.candidates),
        )
        for archive in ordered_archives
    )
    return M3TaskPair(
        tasks_index=tasks_index,
        source_task_binding=binding,
        problem=problem,
        archives=archive_evidence,
        best_width=best_width,
        envelope_max_width=envelope_max_width,
        selected_candidates=selected,  # type: ignore[arg-type]
    )


def build_m3_input_pack(
    *,
    m2_result_id: str,
    m2_result_sha256: str,
    m0_contract_id: str,
    m0_contract_sha256: str,
    task_pairs: tuple[M3TaskPair, ...],
    expected_task_ids: tuple[int, ...],
) -> M3ResidualInputPack:
    """Build one canonical source-faithful M3 input artifact."""

    ordered_ids = tuple(sorted(expected_task_ids))
    ordered_pairs = tuple(sorted(task_pairs, key=lambda item: item.tasks_index))
    payload = {
        "schema_version": "yieldforge.m3-residual-input.v1",
        "m2_result_id": m2_result_id,
        "m2_result_sha256": m2_result_sha256,
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "shapely_version": shapely.__version__,
        "selection_rule": (
            "first_two_distinct_by_width_then_candidate_id_within_m2_0_5_percent_envelope"
        ),
        "primary_geometry_config": ResidualGeometryConfig().model_dump(mode="json"),
        "expected_task_ids": list(ordered_ids),
        "task_pairs": [item.model_dump(mode="json") for item in ordered_pairs],
        "claim_ceiling": (
            "residual_geometry_input_only_not_remnant_reuse_savings_or_physical_process_evidence"
        ),
    }
    digest = semantic_sha256(payload)
    return M3ResidualInputPack(
        input_id=f"yfgi-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m2_result_id=m2_result_id,
        m2_result_sha256=m2_result_sha256,
        m0_contract_id=m0_contract_id,
        m0_contract_sha256=m0_contract_sha256,
        shapely_version=shapely.__version__,
        primary_geometry_config=ResidualGeometryConfig(),
        expected_task_ids=ordered_ids,
        task_pairs=ordered_pairs,
    )


def _canonical_model_bytes(value: FrozenExperimentModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def publish_m3_input_pack(output_directory: Path, pack: M3ResidualInputPack) -> Path:
    """Publish or verify the immutable canonical M3 input artifact."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"residual-geometry-input-{pack.input_id}.json"
    data = _canonical_model_bytes(pack)
    if path.exists():
        existing = _read_regular_file(
            path,
            label="M3 input artifact",
            max_bytes=_MAX_M3_INPUT_BYTES,
        )
        if existing != data:
            raise M3EvidenceError("M3 input artifact is immutable and differs from existing bytes")
        return path

    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.rename(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_m3_input_pack(path: Path) -> M3ResidualInputPack:
    """Load one bounded canonical M3 input artifact."""

    data = _read_regular_file(
        Path(path),
        label="M3 input artifact",
        max_bytes=_MAX_M3_INPUT_BYTES,
    )
    try:
        pack = M3ResidualInputPack.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise M3EvidenceError("M3 input artifact validation failed") from error
    if _canonical_model_bytes(pack) != data:
        raise M3EvidenceError("M3 input artifact does not use canonical JSON encoding")
    return pack


def prepare_m3_input_pack(
    confirmation: GeometryConfirmationResult,
    m0: M0ExperimentContract,
    archive_root: Path,
    *,
    required_task_count: int = 203,
) -> M3ResidualInputPack:
    """Recompute the complete qualifying M2 population and freeze portable pairs."""

    if required_task_count <= 0:
        raise ValueError("required M3 task count must be positive")
    evaluation = confirmation.evaluation
    if (
        evaluation.decision != "proceed_to_m3"
        or evaluation.registered_task_count != required_task_count
        or evaluation.qualifying_task_count != required_task_count
        or evaluation.registered_cell_count != required_task_count * 4
        or evaluation.valid_archive_count != required_task_count * 4
    ):
        raise M3EvidenceError(
            "canonical M2 result does not contain the required all-qualifying population"
        )
    if confirmation.run.m0_contract_sha256 != m0.content_sha256:
        raise M3EvidenceError("canonical M2 result does not bind to the supplied M0 contract")

    root = Path(archive_root)
    try:
        metadata = root.lstat()
    except OSError as error:
        raise M3EvidenceError("M2 archive root could not be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise M3EvidenceError("M2 archive root must be a directory and not a symlink")

    attempts_by_key: dict[tuple[str, int], CalibrationAttemptOutcome] = {
        (attempt.cell.cell_id, attempt.attempt_number): attempt for attempt in confirmation.attempts
    }
    selected_by_task: dict[int, list[CalibrationAttemptOutcome]] = defaultdict(list)
    for selected in confirmation.selected_attempts:
        attempt = attempts_by_key.get((selected.cell_id, selected.attempt_number))
        if attempt is None or attempt.job_id != selected.job_id or not attempt.archive_valid:
            raise M3EvidenceError("canonical M2 selected attempt evidence is incomplete")
        selected_by_task[attempt.cell.tasks_index].append(attempt)
    if len(selected_by_task) != required_task_count:
        raise M3EvidenceError("canonical M2 selected attempts do not cover the required tasks")

    task_pairs = []
    for tasks_index in sorted(selected_by_task):
        task_attempts = sorted(selected_by_task[tasks_index], key=lambda item: item.cell.seed)
        if tuple(item.cell.seed for item in task_attempts) != (0, 1, 2, 3):
            raise M3EvidenceError("one M2 task does not contain the four frozen ordinary seeds")
        verified_archives = []
        expected_problem = None
        for attempt in task_attempts:
            if attempt.batch_sha256 is None:
                raise M3EvidenceError("valid M2 attempt is missing its archive hash")
            verified = load_verified_candidate_archive(
                root / attempt.job_id,
                job_id=attempt.job_id,
                expected_batch_sha256=attempt.batch_sha256,
                expected_candidates=attempt.candidates,
                expected_tasks_index=tasks_index,
                expected_seed=attempt.cell.seed,
                expected_problem=expected_problem,
            )
            if verified.source_task_binding.dataset_id != confirmation.run.dataset_id:
                raise M3EvidenceError("M2 archive dataset binding does not match its result")
            expected_problem = verified.batch.problem
            verified_archives.append(verified)
        task_pairs.append(
            build_m3_task_pair(
                tasks_index=tasks_index,
                archives=tuple(verified_archives),
            )
        )

    expected_task_ids = tuple(sorted(selected_by_task))
    return build_m3_input_pack(
        m2_result_id=confirmation.result_id,
        m2_result_sha256=confirmation.content_sha256,
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        task_pairs=tuple(task_pairs),
        expected_task_ids=expected_task_ids,
    )


def _nearest_rank_p95(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def recompute_m3_summary(
    task_results: tuple[M3TaskResult, ...],
    registered_task_count: int,
) -> M3Summary:
    """Recompute every M3 aggregate solely from persisted task evidence."""

    valid = tuple(item for item in task_results if item.valid)
    failures: dict[str, int] = defaultdict(int)
    for item in task_results:
        if not item.valid and item.error_code is not None:
            failures[item.error_code] += 1
    exact_differences = sum(
        item.comparison is not None and not item.comparison.exact_residual_equal for item in valid
    )
    rule_counts = {rule_name: 0 for rule_name in ResidualRuleName}
    fractions = []
    reconciliation_deltas = []
    for item in valid:
        assert item.comparison is not None
        fractions.append(item.comparison.symmetric_difference_sheet_fraction)
        for rule_name in item.comparison.classification_difference_rule_names:
            rule_counts[rule_name] += 1
        for observation in (item.first_observation, item.second_observation):
            assert observation.accounting is not None
            reconciliation_deltas.append(observation.accounting.reconciliation_delta)
    fraction_tuple = tuple(fractions)
    decision = (
        "pass"
        if len(task_results) == registered_task_count
        and len(valid) == registered_task_count
        and exact_differences >= 1
        else "fail"
    )
    denominator = registered_task_count
    return M3Summary(
        registered_task_count=registered_task_count,
        evaluated_task_count=len(task_results),
        valid_task_count=len(valid),
        failure_count=len(task_results) - len(valid),
        failures=tuple(
            M3FailureSummary(error_code=code, task_count=count)
            for code, count in sorted(failures.items())
        ),
        exact_residual_difference_count=exact_differences,
        exact_residual_difference_percent=exact_differences / denominator * 100.0,
        classification_differences=tuple(
            M3RuleDifferenceSummary(
                rule_name=rule_name,
                difference_count=rule_counts[rule_name],
                difference_percent=rule_counts[rule_name] / denominator * 100.0,
            )
            for rule_name in (
                ResidualRuleName.PERMISSIVE,
                ResidualRuleName.PRIMARY,
                ResidualRuleName.CONSERVATIVE,
            )
        ),
        maximum_reconciliation_delta=(
            float(max(reconciliation_deltas)) if reconciliation_deltas else 0.0
        ),
        symmetric_difference_sheet_fraction_mean=(
            float(statistics.fmean(fraction_tuple)) if fraction_tuple else 0.0
        ),
        symmetric_difference_sheet_fraction_median=(
            float(statistics.median(fraction_tuple)) if fraction_tuple else 0.0
        ),
        symmetric_difference_sheet_fraction_p95=_nearest_rank_p95(fraction_tuple),
        symmetric_difference_sheet_fraction_maximum=(
            float(max(fraction_tuple)) if fraction_tuple else 0.0
        ),
        technical_decision=decision,
    )


def _extract_or_error(
    pair: M3TaskPair,
    selected: M3SelectedCandidate,
    m0: M0ExperimentContract,
    config: ResidualGeometryConfig,
) -> tuple[CandidateResidualObservation, CandidateResidualExtraction | None]:
    try:
        extraction = extract_candidate_residual(
            pair.problem,
            selected.candidate,
            rule_set_from_m0(m0.remnant_eligibility),
            config,
        )
    except ResidualGeometryError as error:
        return (
            CandidateResidualObservation(
                candidate_id=selected.candidate.candidate_id,
                valid=False,
                error_code=error.code,
            ),
            None,
        )
    return extraction.observation, extraction


def _evaluate_task_pair(
    pair: M3TaskPair,
    m0: M0ExperimentContract,
    config: ResidualGeometryConfig,
) -> M3TaskResult:
    first_selected, second_selected = pair.selected_candidates
    first_observation, first = _extract_or_error(pair, first_selected, m0, config)
    second_observation, second = _extract_or_error(pair, second_selected, m0, config)
    if first is None or second is None:
        prefix = "first" if first is None else "second"
        failed = first_observation if first is None else second_observation
        assert failed.error_code is not None
        return M3TaskResult(
            tasks_index=pair.tasks_index,
            first_candidate_id=first_selected.candidate.candidate_id,
            second_candidate_id=second_selected.candidate.candidate_id,
            valid=False,
            error_code=f"{prefix}_{failed.error_code}",
            first_observation=first_observation,
            second_observation=second_observation,
        )
    comparison = compare_candidate_residuals(first, second)
    return M3TaskResult(
        tasks_index=pair.tasks_index,
        first_candidate_id=first_selected.candidate.candidate_id,
        second_candidate_id=second_selected.candidate.candidate_id,
        valid=True,
        first_observation=first_observation,
        second_observation=second_observation,
        comparison=comparison,
    )


def evaluate_m3_residual_geometry(
    pack: M3ResidualInputPack,
    m0: M0ExperimentContract,
) -> M3ResidualGeometryResult:
    """Evaluate every frozen candidate pair and publish no dropped failures."""

    if pack.m0_contract_id != m0.contract_id or pack.m0_contract_sha256 != m0.content_sha256:
        raise M3EvidenceError("M3 input does not bind to the supplied M0 contract")
    if pack.shapely_version != shapely.__version__:
        raise M3EvidenceError("M3 input Shapely version does not match the runtime")
    task_results = tuple(
        _evaluate_task_pair(pair, m0, pack.primary_geometry_config) for pair in pack.task_pairs
    )
    summary = recompute_m3_summary(task_results, len(pack.expected_task_ids))
    payload = {
        "schema_version": "yieldforge.m3-residual-result.v1",
        "input_id": pack.input_id,
        "input_sha256": pack.content_sha256,
        "m2_result_id": pack.m2_result_id,
        "m2_result_sha256": pack.m2_result_sha256,
        "m0_contract_id": pack.m0_contract_id,
        "m0_contract_sha256": pack.m0_contract_sha256,
        "shapely_version": pack.shapely_version,
        "expected_task_ids": list(pack.expected_task_ids),
        "task_results": [item.model_dump(mode="json") for item in task_results],
        "summary": summary.model_dump(mode="json"),
        "claim_ceiling": (
            "exact_residual_geometry_only_not_future_reuse_material_savings_roi_or_physical_process_evidence"
        ),
    }
    digest = semantic_sha256(payload)
    return M3ResidualGeometryResult(
        result_id=f"yfgr-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        input_id=pack.input_id,
        input_sha256=pack.content_sha256,
        m2_result_id=pack.m2_result_id,
        m2_result_sha256=pack.m2_result_sha256,
        m0_contract_id=pack.m0_contract_id,
        m0_contract_sha256=pack.m0_contract_sha256,
        shapely_version=pack.shapely_version,
        expected_task_ids=pack.expected_task_ids,
        task_results=task_results,
        summary=summary,
    )


def publish_m3_result(output_directory: Path, result: M3ResidualGeometryResult) -> Path:
    """Publish or verify the immutable canonical M3 result artifact."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"residual-geometry-result-{result.result_id}.json"
    data = _canonical_model_bytes(result)
    if path.exists():
        existing = _read_regular_file(
            path,
            label="M3 result artifact",
            max_bytes=_MAX_M3_RESULT_BYTES,
        )
        if existing != data:
            raise M3EvidenceError("M3 result artifact is immutable and differs from existing bytes")
        return path

    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.rename(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_m3_result(path: Path) -> M3ResidualGeometryResult:
    """Load one bounded canonical M3 result and recompute its summary and identity."""

    data = _read_regular_file(
        Path(path),
        label="M3 result artifact",
        max_bytes=_MAX_M3_RESULT_BYTES,
    )
    try:
        result = M3ResidualGeometryResult.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise M3EvidenceError("M3 result artifact validation failed") from error
    if _canonical_model_bytes(result) != data:
        raise M3EvidenceError("M3 result artifact does not use canonical JSON encoding")
    return result
