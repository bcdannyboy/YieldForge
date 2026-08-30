"""Authenticated execution and immutable publication for M11 Gate 2."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.realistic_falsification.geometry_gate import (
    Gate2EvaluationResult,
    authenticate_official_gate2_evaluation,
)
from yieldforge.realistic_falsification.runner import (
    M11Gate1RunArtifact,
    load_official_gate1_run,
)

M11Gate2Disposition = Literal[
    "ONE_REPAIR_AND_RERUN",
    "ABANDON",
    "OPEN_GATE_3",
]
_MAX_GATE2_RUN_ARTIFACT_BYTES = 512 * 1024 * 1024


class M11Gate2RunnerError(ValueError):
    """Canonical M11 Gate 2 execution, publication, or read-back failed closed."""


def _disposition_for_result(result):  # type: ignore[no-untyped-def]
    if result.status == "gate_2_survived":
        if result.verdict is not None:
            raise M11Gate2RunnerError("surviving Gate 2 result cannot carry a verdict")
        return "OPEN_GATE_3"
    if result.verdict is None:
        raise M11Gate2RunnerError("terminal Gate 2 result omitted its verdict")
    action = getattr(result.verdict.action, "value", result.verdict.action)
    if result.status == "insufficient_headroom" and action != "ABANDON":
        raise M11Gate2RunnerError("insufficient Gate 2 headroom must ABANDON")
    if result.status == "invalid_test" and action in {
        "ONE_REPAIR_AND_RERUN",
        "ABANDON",
    }:
        return action
    if result.status == "insufficient_headroom":
        return "ABANDON"
    raise M11Gate2RunnerError("Gate 2 result has an unknown disposition branch")


class M11Gate2RunArtifact(FrozenExperimentModel):
    """Complete content-addressed envelope for one official Gate 2 execution."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-gate2-run.v1"] = (
        "yieldforge.m11-gate2-run.v1"
    )
    run_id: StrictStr = Field(pattern=r"^yfm11g2run-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate1_run_id: StrictStr = Field(pattern=r"^yfm11g1run-[0-9a-f]{24}$")
    gate1_run_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate1_result_id: StrictStr = Field(pattern=r"^yfm11g1r-[0-9a-f]{24}$")
    gate1_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate2_result: Gate2EvaluationResult
    stream_count: Literal[40] = 40
    edge_count: StrictInt = Field(ge=0)
    unresolved_optimistically_counted: StrictInt = Field(ge=0)
    blocking_error_count: StrictInt = Field(ge=0)
    status: Literal["invalid_test", "insufficient_headroom", "gate_2_survived"]
    evaluation_stage: Literal[
        "stage_a_favorable_superset",
        "stage_b_exact_attempted",
    ]
    disposition: M11Gate2Disposition
    productization_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_bound_complete_census_and_identity(self) -> Self:
        result = self.gate2_result
        if (
            self.gate1_result_id != result.gate1_result_id
            or self.gate1_result_content_sha256
            != result.gate1_result_content_sha256
        ):
            raise ValueError("Gate 2 run differs from its Gate 1 result binding")
        expected_edge_count = sum(len(item.edges) for item in result.stream_results)
        if (
            self.stream_count != len(result.stream_results)
            or self.edge_count != expected_edge_count
            or self.unresolved_optimistically_counted
            != result.unresolved_optimistically_counted
            or self.blocking_error_count != result.blocking_error_count
        ):
            raise ValueError("Gate 2 run edge census differs from complete result evidence")
        if (
            self.status != result.status
            or self.evaluation_stage != result.evaluation_stage
            or self.disposition != _disposition_for_result(result)
            or self.productization_authorized
            or result.productization_authorized
        ):
            raise ValueError("Gate 2 run branch differs from its result")
        digest = semantic_sha256(
            self,
            excluded_fields={"run_id", "content_sha256"},
        )
        if self.run_id != f"yfm11g2run-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 2 run identity differs from complete evidence")
        return self


def evaluate_official_gate2(*, repository_root: Path, gate1_result):  # type: ignore[no-untyped-def]
    """Lazily dispatch to the root-only geometry evaluator."""

    from yieldforge.realistic_falsification.geometry_gate import (
        evaluate_official_gate2 as implementation,
    )

    return implementation(repository_root=repository_root, gate1_result=gate1_result)


def build_gate2_run_artifact(
    *,
    gate1_artifact: M11Gate1RunArtifact,
    gate2_result: Gate2EvaluationResult,
) -> M11Gate2RunArtifact:
    """Bind a complete Gate 2 result to the exact authenticated Gate 1 run."""

    gate1 = M11Gate1RunArtifact.model_validate(
        gate1_artifact.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    result = Gate2EvaluationResult.model_validate(
        gate2_result.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    receipt = gate1.gate1_result.audit_receipt
    if (
        gate1.status != "gate_1_survived"
        or gate1.disposition != "OPEN_GATE_2"
        or not gate1.gate1_result.opens_gate_2
        or receipt is None
    ):
        raise M11Gate2RunnerError("Gate 2 run requires an exact OPEN_GATE_2 artifact")
    if (
        result.gate1_result_id != gate1.gate1_result.result_id
        or result.gate1_result_content_sha256 != gate1.gate1_result.content_sha256
        or result.gate1_receipt_id != receipt.receipt_id
        or result.gate1_receipt_content_sha256 != receipt.content_sha256
        or result.gate1_cell_ids != receipt.cell_ids
        or result.gate1_cell_content_sha256s != receipt.cell_content_sha256s
        or result.contract_id != gate1.contract_id
        or result.contract_content_sha256 != gate1.contract_content_sha256
        or result.population_id != gate1.population_id
        or result.population_content_sha256 != gate1.population_content_sha256
        or result.source_manifest_id != gate1.source_manifest_id
        or result.source_manifest_content_sha256
        != gate1.source_manifest_content_sha256
    ):
        raise M11Gate2RunnerError("Gate 2 result differs from exact Gate 1 evidence")
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate2-run.v1",
        "gate1_run_id": gate1.run_id,
        "gate1_run_content_sha256": gate1.content_sha256,
        "gate1_result_id": gate1.gate1_result.result_id,
        "gate1_result_content_sha256": gate1.gate1_result.content_sha256,
        "gate2_result": result.model_dump(mode="json"),
        "stream_count": len(result.stream_results),
        "edge_count": sum(len(item.edges) for item in result.stream_results),
        "unresolved_optimistically_counted": (
            result.unresolved_optimistically_counted
        ),
        "blocking_error_count": result.blocking_error_count,
        "status": result.status,
        "evaluation_stage": result.evaluation_stage,
        "disposition": _disposition_for_result(result),
        "productization_authorized": False,
    }
    digest = semantic_sha256(semantic)
    return M11Gate2RunArtifact(
        run_id=f"yfm11g2run-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        gate1_run_id=gate1.run_id,
        gate1_run_content_sha256=gate1.content_sha256,
        gate1_result_id=gate1.gate1_result.result_id,
        gate1_result_content_sha256=gate1.gate1_result.content_sha256,
        gate2_result=result,
        stream_count=40,
        edge_count=semantic["edge_count"],
        unresolved_optimistically_counted=result.unresolved_optimistically_counted,
        blocking_error_count=result.blocking_error_count,
        status=result.status,
        evaluation_stage=result.evaluation_stage,
        disposition=semantic["disposition"],
    )


def _canonical_gate2_run_bytes(artifact: M11Gate2RunArtifact) -> bytes:
    strict = M11Gate2RunArtifact.model_validate(
        artifact.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    return canonical_pretty_json_bytes(strict)


def _validate_gate2_run_bytes(data: bytes) -> bytes:
    strict = M11Gate2RunArtifact.model_validate_json(data, strict=True)
    return canonical_pretty_json_bytes(strict)


def publish_gate2_run(
    output_directory: Path,
    artifact: M11Gate2RunArtifact,
) -> Path:
    """Publish one immutable content-addressed Gate 2 execution artifact."""

    strict = M11Gate2RunArtifact.model_validate(
        artifact.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    path = Path(output_directory) / f"m11-gate2-{strict.run_id}.json"
    try:
        return publish_immutable_artifact(
            path,
            _canonical_gate2_run_bytes(strict),
            validate=_validate_gate2_run_bytes,
            label="M11 Gate 2 run artifact",
        )
    except M8ArtifactPublicationError as error:
        raise M11Gate2RunnerError("M11 Gate 2 immutable publication failed") from error


def _read_bounded_gate2_run(path: Path) -> bytes:
    candidate = Path(path)
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_GATE2_RUN_ARTIFACT_BYTES
        ):
            raise M11Gate2RunnerError(
                "M11 Gate 2 artifact must be a bounded regular file"
            )
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            remaining = _MAX_GATE2_RUN_ARTIFACT_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            opened = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = candidate.lstat()
    except OSError as error:
        raise M11Gate2RunnerError(
            "M11 Gate 2 artifact could not be read safely"
        ) from error
    expected_identity = (before.st_dev, before.st_ino, before.st_size)
    if (
        len(raw) > _MAX_GATE2_RUN_ARTIFACT_BYTES
        or (opened.st_dev, opened.st_ino, opened.st_size) != expected_identity
        or (after.st_dev, after.st_ino, after.st_size) != expected_identity
        or len(raw) != before.st_size
    ):
        raise M11Gate2RunnerError("M11 Gate 2 artifact changed during read-back")
    return raw


def _reject_duplicate_gate2_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise M11Gate2RunnerError(f"duplicate M11 Gate 2 artifact key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_gate2(value: str) -> None:
    raise M11Gate2RunnerError(f"nonfinite M11 Gate 2 artifact constant: {value}")


def load_official_gate2_run(
    path: Path,
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
) -> M11Gate2RunArtifact:
    """Strict-read and freshly authenticate Gate 1 plus every Gate 2 edge."""

    raw = _read_bounded_gate2_run(path)
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_gate2_keys,
            parse_constant=_reject_nonfinite_gate2,
        )
        artifact = M11Gate2RunArtifact.model_validate_json(raw, strict=True)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise M11Gate2RunnerError(
            "M11 Gate 2 artifact is not strict canonical evidence"
        ) from error
    if raw != _canonical_gate2_run_bytes(artifact):
        raise M11Gate2RunnerError("M11 Gate 2 artifact encoding is not canonical")

    root = Path(repository_root).resolve()
    gate1_path = Path(gate1_artifact_path)
    try:
        gate1 = load_official_gate1_run(gate1_path, repository_root=root)
    except (OSError, TypeError, ValueError, ValidationError) as error:
        raise M11Gate2RunnerError(
            "M11 Gate 2 Gate 1 authentication failed"
        ) from error
    if (
        gate1.status != "gate_1_survived"
        or gate1.disposition != "OPEN_GATE_2"
        or not gate1.gate1_result.opens_gate_2
        or artifact.gate1_run_id != gate1.run_id
        or artifact.gate1_run_content_sha256 != gate1.content_sha256
        or artifact.gate1_result_id != gate1.gate1_result.result_id
        or artifact.gate1_result_content_sha256
        != gate1.gate1_result.content_sha256
    ):
        raise M11Gate2RunnerError(
            "M11 Gate 2 Gate 1 run binding differs from authenticated evidence"
        )
    try:
        authenticated = authenticate_official_gate2_evaluation(
            artifact.gate2_result,
            repository_root=root,
            gate1_result=gate1.gate1_result,
        )
    except (OSError, TypeError, ValueError, ValidationError) as error:
        raise M11Gate2RunnerError(
            "M11 Gate 2 official authentication failed"
        ) from error
    if authenticated != artifact.gate2_result:
        raise M11Gate2RunnerError(
            "M11 Gate 2 authenticated result differs from published evidence"
        )
    expected = build_gate2_run_artifact(
        gate1_artifact=gate1,
        gate2_result=authenticated,
    )
    if expected != artifact:
        raise M11Gate2RunnerError(
            "M11 Gate 2 run envelope differs from authenticated evidence"
        )
    return artifact


def run_and_publish_official_gate2(
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    output_directory: Path,
):  # type: ignore[no-untyped-def]
    """Authenticate Gate 1, execute Gate 2 once, then authenticate fresh read-back."""

    root = Path(repository_root).resolve()
    gate1_path = Path(gate1_artifact_path)
    try:
        gate1 = load_official_gate1_run(gate1_path, repository_root=root)
    except (OSError, TypeError, ValueError) as error:
        raise M11Gate2RunnerError("M11 Gate 1 authentication failed") from error
    if (
        gate1.status != "gate_1_survived"
        or gate1.disposition != "OPEN_GATE_2"
        or gate1.gate1_result.opens_gate_2 is not True
    ):
        raise M11Gate2RunnerError("official Gate 1 artifact did not exactly OPEN_GATE_2")
    try:
        result = evaluate_official_gate2(
            repository_root=root,
            gate1_result=gate1.gate1_result,
        )
        artifact = build_gate2_run_artifact(
            gate1_artifact=gate1,
            gate2_result=result,
        )
    except (OSError, TypeError, ValueError) as error:
        raise M11Gate2RunnerError("M11 Gate 2 execution failed") from error
    try:
        path = publish_gate2_run(Path(output_directory), artifact)
        readback = load_official_gate2_run(
            path,
            repository_root=root,
            gate1_artifact_path=gate1_path,
        )
    except (OSError, TypeError, ValueError) as error:
        if isinstance(error, M11Gate2RunnerError):
            raise
        raise M11Gate2RunnerError(
            "M11 Gate 2 publication or read-back failed"
        ) from error
    if readback != artifact:
        raise M11Gate2RunnerError(
            "M11 Gate 2 read-back differs from the published artifact"
        )
    return readback, path


__all__ = [
    "M11Gate2RunArtifact",
    "M11Gate2RunnerError",
    "build_gate2_run_artifact",
    "load_official_gate2_run",
    "publish_gate2_run",
    "run_and_publish_official_gate2",
]
