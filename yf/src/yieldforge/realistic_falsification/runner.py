"""Canonical Gate 0/1 generation, execution, publication, and authenticated read-back."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, Self

from pydantic import ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.realistic_falsification.bounds import (
    Gate1BaselineSelectionEvidence,
    Gate1EvidenceError,
    Gate1StreamCell,
    Gate1TinyAudit,
    _open_official_gate1_session,
    verify_gate1_tiny_audit,
)
from yieldforge.realistic_falsification.contracts import (
    M11ExperimentContract,
    M11InvalidReasonCategory,
)
from yieldforge.realistic_falsification.evaluate import (
    Gate1EvaluationResult,
    _build_gate1_invalid_result,
    _build_gate1_valid_result,
    _build_registered_tiny_audit,
    authenticate_official_gate1_evaluation,
)
from yieldforge.realistic_falsification.pack import (
    M11PackBundle,
    M11Population,
    canonical_pack_artifact_bytes,
    generate_m11_pack,
    load_m11_pack_bundle,
)

_CONTRACT_PATH = Path("benchmarks/falsification/m11-contract-v1.json")
_POPULATION_PATH = Path("benchmarks/falsification/m11-population-v1.json")
_SOURCE_MANIFEST_PATH = Path("benchmarks/falsification/source-manifest-v1.json")
_MAX_RUN_ARTIFACT_BYTES = 128 * 1024 * 1024
_CORPUS_ORDER = ("lectra-m3-m4", "loco-2dics")

M11Gate1AttemptStatus = Literal["success", "failure"]
M11Gate1FailureKind = Literal["evidence_failure", "software_execution_failure"]
M11Gate1Disposition = Literal["ABANDON", "OPEN_GATE_2", "INVALID_NONZERO"]


class M11Gate1RunnerError(ValueError):
    """Canonical M11 generation, execution, publication, or read-back failed closed."""


class M11PackPublication(NamedTuple):
    bundle: M11PackBundle
    contract_path: Path
    population_path: Path


@dataclass(frozen=True, slots=True)
class _Gate1AttemptDraft:
    position: int
    stream_id: str
    corpus_id: Literal["lectra-m3-m4", "loco-2dics"]
    status: M11Gate1AttemptStatus
    cell: object | None
    failure: M11Gate1CellFailure | None


class M11Gate1CellFailure(FrozenExperimentModel):
    """Bounded deterministic record of one failed confirmation-cell attempt."""

    failure_kind: M11Gate1FailureKind
    exception_type: StrictStr = Field(min_length=1, max_length=240)
    detail: StrictStr = Field(min_length=1, max_length=1000)


class M11Gate1CellAttempt(FrozenExperimentModel):
    """One registered confirmation stream, including failed attempts without imputation."""

    model_config = ConfigDict(revalidate_instances="always")

    position: StrictInt = Field(ge=0, le=39)
    stream_id: StrictStr = Field(min_length=1)
    corpus_id: Literal["lectra-m3-m4", "loco-2dics"]
    status: M11Gate1AttemptStatus
    cell: Gate1StreamCell | None
    failure: M11Gate1CellFailure | None

    @model_validator(mode="after")
    def require_exactly_one_attempt_outcome(self) -> Self:
        if self.status == "success":
            if self.cell is None or self.failure is not None:
                raise ValueError("successful Gate 1 attempt requires one cell and no failure")
            if self.cell.stream_id != self.stream_id or self.cell.corpus_id != self.corpus_id:
                raise ValueError("Gate 1 attempt cell differs from its stream binding")
        elif self.cell is not None or self.failure is None:
            raise ValueError("failed Gate 1 attempt requires one failure and no numeric cell")
        return self


class M11Gate1RunArtifact(FrozenExperimentModel):
    """Complete content-addressed official execution envelope for M11 Gate 1."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-gate1-run.v1"] = "yieldforge.m11-gate1-run.v1"
    run_id: StrictStr = Field(pattern=r"^yfm11g1run-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(min_length=1)
    contract_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    population_id: StrictStr = Field(min_length=1)
    population_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_manifest_id: StrictStr = Field(min_length=1)
    source_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempts: tuple[M11Gate1CellAttempt, ...] = Field(min_length=40, max_length=40)
    baseline_selections: tuple[Gate1BaselineSelectionEvidence, ...] = Field(
        min_length=2,
        max_length=2,
    )
    tiny_audit: Gate1TinyAudit
    gate1_result: Gate1EvaluationResult
    successful_cell_count: StrictInt = Field(ge=0, le=40)
    failed_cell_count: StrictInt = Field(ge=0, le=40)
    status: Literal[
        "invalid_test",
        "falsified_by_optimistic_ceiling",
        "gate_1_survived",
    ]
    disposition: M11Gate1Disposition
    productization_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_complete_bound_execution(self) -> Self:
        result = self.gate1_result
        roots = (
            self.contract_id,
            self.contract_content_sha256,
            self.population_id,
            self.population_content_sha256,
            self.source_manifest_id,
            self.source_manifest_content_sha256,
        )
        result_roots = (
            result.contract_id,
            result.contract_content_sha256,
            result.population_id,
            result.population_content_sha256,
            result.source_manifest_id,
            result.source_manifest_content_sha256,
        )
        if roots != result_roots:
            raise ValueError("M11 Gate 1 run roots differ from its evaluation result")
        expected_ids = tuple(
            stream_id
            for corpus in result.contract.corpora
            for stream_id in corpus.confirmation_stream_ids
        )
        expected_corpora = tuple(
            corpus.source.corpus_id
            for corpus in result.contract.corpora
            for _stream_id in corpus.confirmation_stream_ids
        )
        if (
            len(expected_ids) != 40
            or tuple(item.position for item in self.attempts) != tuple(range(40))
            or tuple(item.stream_id for item in self.attempts) != expected_ids
            or tuple(item.corpus_id for item in self.attempts) != expected_corpora
        ):
            raise ValueError("M11 Gate 1 attempts differ from exact contract order")
        success_cells = tuple(item.cell for item in self.attempts if item.cell is not None)
        failure_count = sum(item.failure is not None for item in self.attempts)
        if (
            self.successful_cell_count != len(success_cells)
            or self.failed_cell_count != failure_count
            or len(success_cells) + failure_count != 40
            or self.status != result.status
        ):
            raise ValueError("M11 Gate 1 attempt census or status differs")
        canonical_tiny = verify_gate1_tiny_audit(self.tiny_audit)
        if canonical_tiny != self.tiny_audit:
            raise ValueError("M11 Gate 1 run tiny audit differs after verification")
        selection_corpora = tuple(item.corpus_id for item in self.baseline_selections)
        if selection_corpora != _CORPUS_ORDER:
            raise ValueError("M11 Gate 1 run baseline selections differ from corpus order")

        if result.status == "invalid_test":
            if failure_count == 0 or self.disposition != "INVALID_NONZERO":
                raise ValueError("invalid M11 Gate 1 run must preserve at least one failure")
            if (
                result.audit_receipt is not None
                or result.statistics is not None
                or result.repair_count != 0
            ):
                raise ValueError("invalid M11 Gate 1 run cannot contain numeric inference")
        else:
            receipt = result.audit_receipt
            if failure_count != 0 or len(success_cells) != 40 or receipt is None:
                raise ValueError("valid M11 Gate 1 run requires all forty successful cells")
            if (
                success_cells != receipt.confirmation_cells
                or self.baseline_selections != receipt.baseline_selections
                or self.tiny_audit != receipt.tiny_audit
            ):
                raise ValueError("M11 Gate 1 run differs from its complete audit receipt")
            expected_disposition: M11Gate1Disposition = (
                "ABANDON" if result.status == "falsified_by_optimistic_ceiling" else "OPEN_GATE_2"
            )
            if self.disposition != expected_disposition:
                raise ValueError("M11 Gate 1 run disposition differs from its decision branch")

        digest = semantic_sha256(self, excluded_fields={"run_id", "content_sha256"})
        if self.run_id != f"yfm11g1run-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("M11 Gate 1 run identity differs from semantic content")
        return self


def _failure_record(error: Exception) -> M11Gate1CellFailure:
    failure_kind: M11Gate1FailureKind = (
        "evidence_failure"
        if isinstance(error, Gate1EvidenceError)
        else "software_execution_failure"
    )
    detail = str(error).strip() or "exception carried no detail"
    return M11Gate1CellFailure(
        failure_kind=failure_kind,
        exception_type=f"{type(error).__module__}.{type(error).__qualname__}"[:240],
        detail=detail[:1000],
    )


def _strict_gate1_cell(value: object) -> Gate1StreamCell:
    try:
        payload = value.model_dump(mode="python", round_trip=True)
    except AttributeError as error:
        raise TypeError("Gate 1 session returned a non-model cell") from error
    return Gate1StreamCell.model_validate(payload, strict=True)


def _build_run_artifact(
    *,
    context,
    attempts: tuple[_Gate1AttemptDraft, ...],
    baseline_selections: tuple[Gate1BaselineSelectionEvidence, ...],
    tiny_audit: Gate1TinyAudit,
) -> M11Gate1RunArtifact:
    canonical_attempts = tuple(
        M11Gate1CellAttempt(
            position=item.position,
            stream_id=item.stream_id,
            corpus_id=item.corpus_id,
            status=item.status,
            cell=item.cell,
            failure=item.failure,
        )
        for item in attempts
    )
    cells = tuple(item.cell for item in canonical_attempts if item.cell is not None)
    failures = tuple(item.failure for item in canonical_attempts if item.failure is not None)
    if failures:
        software_only = all(item.failure_kind == "software_execution_failure" for item in failures)
        result = _build_gate1_invalid_result(
            context=context,
            observed_cell_ids=tuple(
                item.cell.cell_id if item.cell is not None else f"failure:{item.stream_id}"
                for item in canonical_attempts
            ),
            category=(
                M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT
                if software_only
                else M11InvalidReasonCategory.OTHER_VALIDITY_FAILURE
            ),
            reason_code=(
                "software_implementation_defect"
                if software_only
                else "accounting_reconciliation_failure"
            ),
            repair_count=0,
        )
        disposition: M11Gate1Disposition = "INVALID_NONZERO"
    else:
        result = _build_gate1_valid_result(
            context=context,
            cells=cells,
            baseline_selections=baseline_selections,
            tiny_audit=tiny_audit,
            repair_count=0,
        )
        disposition = (
            "ABANDON" if result.status == "falsified_by_optimistic_ceiling" else "OPEN_GATE_2"
        )
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-run.v1",
        "contract_id": result.contract_id,
        "contract_content_sha256": result.contract_content_sha256,
        "population_id": result.population_id,
        "population_content_sha256": result.population_content_sha256,
        "source_manifest_id": result.source_manifest_id,
        "source_manifest_content_sha256": result.source_manifest_content_sha256,
        "attempts": canonical_attempts,
        "baseline_selections": baseline_selections,
        "tiny_audit": tiny_audit,
        "gate1_result": result,
        "successful_cell_count": len(cells),
        "failed_cell_count": len(failures),
        "status": result.status,
        "disposition": disposition,
        "productization_authorized": False,
    }
    digest = semantic_sha256(
        {
            key: (
                [item.model_dump(mode="json") for item in value]
                if key in {"attempts", "baseline_selections"}
                else value.model_dump(mode="json")
                if key in {"tiny_audit", "gate1_result"}
                else value
            )
            for key, value in semantic.items()
        }
    )
    return M11Gate1RunArtifact(
        run_id=f"yfm11g1run-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def execute_official_gate1(repository_root: Path) -> M11Gate1RunArtifact:
    """Build every registered cell using exactly one authenticated execution session."""

    root = Path(repository_root).resolve()
    try:
        session = _open_official_gate1_session(root)
        selections = session.baseline_selections
        if len(selections) != 2:
            raise M11Gate1RunnerError("official Gate 1 session did not freeze two selections")
        stream_ids = tuple(
            stream_id
            for corpus in session.context.bundle.contract.corpora
            for stream_id in corpus.confirmation_stream_ids
        )
        if len(stream_ids) != 40 or len(set(stream_ids)) != 40:
            raise M11Gate1RunnerError("official Gate 1 contract does not contain forty streams")
        tiny_audit = _build_registered_tiny_audit()
    except (OSError, TypeError, ValueError, ValidationError) as error:
        if isinstance(error, M11Gate1RunnerError):
            raise
        raise M11Gate1RunnerError("official Gate 1 session could not authenticate") from error

    corpus_by_stream = {
        stream_id: corpus_id
        for corpus_id, corpus in zip(
            _CORPUS_ORDER,
            session.context.bundle.contract.corpora,
            strict=True,
        )
        for stream_id in corpus.confirmation_stream_ids
    }
    attempts: list[_Gate1AttemptDraft] = []
    for position, stream_id in enumerate(stream_ids):
        try:
            built = session.build_stream_cell(stream_id)
            cell = _strict_gate1_cell(built)
        except Exception as error:  # preserve every registered failure and continue the census
            attempts.append(
                _Gate1AttemptDraft(
                    position=position,
                    stream_id=stream_id,
                    corpus_id=corpus_by_stream[stream_id],
                    status="failure",
                    cell=None,
                    failure=_failure_record(error),
                )
            )
        else:
            attempts.append(
                _Gate1AttemptDraft(
                    position=position,
                    stream_id=stream_id,
                    corpus_id=corpus_by_stream[stream_id],
                    status="success",
                    cell=cell,
                    failure=None,
                )
            )
    return _build_run_artifact(
        context=session.context,
        attempts=tuple(attempts),
        baseline_selections=selections,
        tiny_audit=tiny_audit,
    )


def _canonical_run_bytes(artifact: M11Gate1RunArtifact) -> bytes:
    strict = M11Gate1RunArtifact.model_validate(
        artifact.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    return canonical_pretty_json_bytes(strict)


def _validate_run_bytes(data: bytes) -> bytes:
    strict = M11Gate1RunArtifact.model_validate_json(data, strict=True)
    return canonical_pretty_json_bytes(strict)


def publish_gate1_run(output_directory: Path, artifact: M11Gate1RunArtifact) -> Path:
    """Publish one immutable content-addressed Gate 1 execution artifact."""

    strict = M11Gate1RunArtifact.model_validate(
        artifact.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    path = Path(output_directory) / f"m11-gate1-{strict.run_id}.json"
    try:
        return publish_immutable_artifact(
            path,
            _canonical_run_bytes(strict),
            validate=_validate_run_bytes,
            label="M11 Gate 1 run artifact",
        )
    except M8ArtifactPublicationError as error:
        raise M11Gate1RunnerError("M11 Gate 1 immutable publication failed") from error


def _authenticate_official_invalid_run(
    artifact: M11Gate1RunArtifact,
    *,
    repository_root: Path,
) -> M11Gate1RunArtifact:
    """Authenticate invalid evidence without converting failures into numeric cells."""

    try:
        session = _open_official_gate1_session(Path(repository_root).resolve())
        context = session.context
        expected_roots = (
            context.bundle.contract.contract_id,
            context.bundle.contract.content_sha256,
            context.bundle.population.population_id,
            context.bundle.population.content_sha256,
            context.source_manifest.source_manifest_id,
            context.source_manifest.content_sha256,
        )
        artifact_roots = (
            artifact.contract_id,
            artifact.contract_content_sha256,
            artifact.population_id,
            artifact.population_content_sha256,
            artifact.source_manifest_id,
            artifact.source_manifest_content_sha256,
        )
        if (
            artifact_roots != expected_roots
            or artifact.gate1_result.contract != context.bundle.contract
            or artifact.baseline_selections != session.baseline_selections
            or artifact.tiny_audit != _build_registered_tiny_audit()
        ):
            raise Gate1EvidenceError(
                "invalid Gate 1 run roots, selections, or tiny audit differ from official"
            )

        for attempt in artifact.attempts:
            if attempt.status == "failure":
                try:
                    rebuilt = session.build_stream_cell(attempt.stream_id)
                    _strict_gate1_cell(rebuilt)
                except Exception as error:
                    if _failure_record(error) != attempt.failure:
                        raise Gate1EvidenceError(
                            "invalid Gate 1 run failure fingerprint did not reproduce"
                        ) from error
                else:
                    raise Gate1EvidenceError(
                        "invalid Gate 1 run claimed a failure for a successful stream"
                    )
                continue
            expected = _strict_gate1_cell(session.build_stream_cell(attempt.stream_id))
            if attempt.cell != expected:
                raise Gate1EvidenceError(
                    "invalid Gate 1 run successful cell differs from official reconstruction"
                )

        failures = tuple(item.failure for item in artifact.attempts if item.failure is not None)
        if not failures:
            raise Gate1EvidenceError("invalid Gate 1 run does not preserve a failed attempt")
        software_only = all(item.failure_kind == "software_execution_failure" for item in failures)
        expected_result = _build_gate1_invalid_result(
            context=context,
            observed_cell_ids=tuple(
                item.cell.cell_id if item.cell is not None else f"failure:{item.stream_id}"
                for item in artifact.attempts
            ),
            category=(
                M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT
                if software_only
                else M11InvalidReasonCategory.OTHER_VALIDITY_FAILURE
            ),
            reason_code=(
                "software_implementation_defect"
                if software_only
                else "accounting_reconciliation_failure"
            ),
            repair_count=0,
        )
        if artifact.gate1_result != expected_result:
            raise Gate1EvidenceError(
                "invalid Gate 1 result differs from its preserved attempt evidence"
            )
    except (AttributeError, OSError, TypeError, ValueError, ValidationError) as error:
        raise M11Gate1RunnerError("M11 Gate 1 invalid read-back authentication failed") from error
    return artifact


def _read_bounded_regular_file(path: Path) -> bytes:
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_RUN_ARTIFACT_BYTES
        ):
            raise M11Gate1RunnerError("M11 Gate 1 artifact must be a bounded regular file")
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            remaining = _MAX_RUN_ARTIFACT_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        final_metadata = candidate.lstat()
    except OSError as error:
        raise M11Gate1RunnerError("M11 Gate 1 artifact could not be read safely") from error
    if (
        len(raw) > _MAX_RUN_ARTIFACT_BYTES
        or (
            after.st_dev,
            after.st_ino,
            after.st_size,
        )
        != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
        )
        or (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_size,
        )
        != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
        )
        or len(raw) != metadata.st_size
    ):
        raise M11Gate1RunnerError("M11 Gate 1 artifact changed during read-back")
    return raw


def load_official_gate1_run(
    path: Path,
    *,
    repository_root: Path,
) -> M11Gate1RunArtifact:
    """Strict-read and independently authenticate an official Gate 1 artifact."""

    raw = _read_bounded_regular_file(path)
    try:
        json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite)
        artifact = M11Gate1RunArtifact.model_validate_json(raw, strict=True)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise M11Gate1RunnerError("M11 Gate 1 artifact is not strict canonical evidence") from error
    if raw != _canonical_run_bytes(artifact):
        raise M11Gate1RunnerError("M11 Gate 1 artifact encoding is not canonical")
    if artifact.status == "invalid_test":
        _authenticate_official_invalid_run(
            artifact,
            repository_root=Path(repository_root).resolve(),
        )
    else:
        try:
            authenticated = authenticate_official_gate1_evaluation(
                artifact.gate1_result,
                repository_root=Path(repository_root).resolve(),
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise M11Gate1RunnerError("M11 Gate 1 read-back authentication failed") from error
        if authenticated != artifact.gate1_result:
            raise M11Gate1RunnerError("M11 Gate 1 read-back result differs from official evidence")
        receipt = authenticated.audit_receipt
        if receipt is None:
            raise M11Gate1RunnerError("valid M11 Gate 1 read-back omitted its receipt")
        if (
            tuple(item.cell for item in artifact.attempts) != receipt.confirmation_cells
            or artifact.baseline_selections != receipt.baseline_selections
            or artifact.tiny_audit != receipt.tiny_audit
        ):
            raise M11Gate1RunnerError("M11 Gate 1 run envelope differs from authenticated evidence")
    return artifact


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise M11Gate1RunnerError(f"duplicate M11 Gate 1 artifact key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise M11Gate1RunnerError(f"nonfinite M11 Gate 1 artifact constant: {value}")


def run_and_publish_official_gate1(
    *,
    repository_root: Path,
    output_directory: Path,
) -> tuple[M11Gate1RunArtifact, Path]:
    """Execute, publish, then authenticate a fresh exact read-back."""

    root = Path(repository_root).resolve()
    validate_official_m11_pack(root)
    artifact = execute_official_gate1(root)
    path = publish_gate1_run(Path(output_directory), artifact)
    readback = load_official_gate1_run(path, repository_root=root)
    if readback != artifact:
        raise M11Gate1RunnerError("M11 Gate 1 read-back differs from the published artifact")
    return readback, path


def _validate_contract_bytes(data: bytes) -> bytes:
    return canonical_pretty_json_bytes(M11ExperimentContract.model_validate_json(data, strict=True))


def _validate_population_bytes(data: bytes) -> bytes:
    return canonical_pretty_json_bytes(M11Population.model_validate_json(data, strict=True))


def generate_and_publish_m11_pack(repository_root: Path) -> M11PackPublication:
    """Deterministically regenerate and immutably publish the two official pack roots."""

    root = Path(repository_root).resolve()
    try:
        generated = generate_m11_pack(root)
        artifacts = canonical_pack_artifact_bytes(generated)
        contract_path = publish_immutable_artifact(
            root / _CONTRACT_PATH,
            artifacts.contract,
            validate=_validate_contract_bytes,
            label="M11 contract artifact",
        )
        population_path = publish_immutable_artifact(
            root / _POPULATION_PATH,
            artifacts.population,
            validate=_validate_population_bytes,
            label="M11 population artifact",
        )
        loaded = load_m11_pack_bundle(
            repository_root=root,
            contract_path=contract_path,
            population_path=population_path,
            source_manifest_path=root / _SOURCE_MANIFEST_PATH,
        )
    except (OSError, TypeError, ValueError, ValidationError) as error:
        raise M11Gate1RunnerError("M11 deterministic pack publication failed") from error
    if loaded != generated:
        raise M11Gate1RunnerError("published M11 pack differs from deterministic regeneration")
    return M11PackPublication(loaded, contract_path, population_path)


def validate_official_m11_pack(repository_root: Path) -> M11PackBundle:
    """Regenerate and exact-compare the complete committed official pack."""

    root = Path(repository_root).resolve()
    try:
        loaded = load_m11_pack_bundle(
            repository_root=root,
            contract_path=root / _CONTRACT_PATH,
            population_path=root / _POPULATION_PATH,
            source_manifest_path=root / _SOURCE_MANIFEST_PATH,
        )
        regenerated = generate_m11_pack(root)
        if canonical_pack_artifact_bytes(loaded) != canonical_pack_artifact_bytes(regenerated):
            raise M11Gate1RunnerError("official M11 pack differs from deterministic regeneration")
    except (OSError, TypeError, ValueError, ValidationError) as error:
        if isinstance(error, M11Gate1RunnerError):
            raise
        raise M11Gate1RunnerError("official M11 pack validation failed") from error
    return loaded


__all__ = [
    "M11Gate1CellAttempt",
    "M11Gate1CellFailure",
    "M11Gate1RunArtifact",
    "M11Gate1RunnerError",
    "M11PackPublication",
    "execute_official_gate1",
    "generate_and_publish_m11_pack",
    "load_official_gate1_run",
    "publish_gate1_run",
    "run_and_publish_official_gate1",
    "validate_official_m11_pack",
]
