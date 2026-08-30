"""Authenticated execution and immutable publication for M11 Gate 3 early confirmation."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import ConfigDict, Field, StrictBool, StrictStr, ValidationError, model_validator

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
    Gate3ConfirmationBackend,
    Gate3EarlyConfirmationResult,
    Gate3RootBinding,
    build_gate3_root_binding,
    run_gate3_early_confirmation,
)
from yieldforge.realistic_falsification.gate3_contracts import (
    M11Gate3ConfirmationConfig,
    M11Gate3RootBinding,
    load_gate3_confirmation_config,
)
from yieldforge.realistic_falsification.geometry_runner import (
    M11Gate2RunArtifact,
    load_official_gate2_run,
)
from yieldforge.realistic_falsification.runner import (
    M11Gate1RunArtifact,
    load_official_gate1_run,
)

_MAX_GATE3_EARLY_RUN_BYTES = 512 * 1024 * 1024

Gate3EarlyStatus = Literal[
    "invalid_test",
    "diagnosis_required",
    "insufficient_headroom",
    "central_survived",
]
Gate3EarlyDisposition = Literal[
    "INVALID_NONZERO",
    "PAUSE_DIAGNOSIS",
    "ABANDON",
    "CONTINUE_GATE3",
]


class M11Gate3RunnerError(ValueError):
    """Official Gate 3 parent authentication, execution, or publication failed closed."""


class Gate3BackendFactory(Protocol):
    """Construct a backend only after the runner authenticates every admitted root."""

    def __call__(
        self,
        *,
        repository_root: Path,
        gate1_artifact: M11Gate1RunArtifact,
        gate2_artifact: M11Gate2RunArtifact,
        gate3_config: M11Gate3ConfirmationConfig,
        roots: Gate3RootBinding,
    ) -> Gate3ConfirmationBackend: ...


_GATE3_PREAUTHENTICATED_INPUTS_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class Gate3PreauthenticatedInputs:
    """One fully authenticated in-memory parent/config bundle for a single Gate 3 run."""

    repository_root: Path
    gate1_artifact: M11Gate1RunArtifact
    gate2_artifact: M11Gate2RunArtifact
    gate3_config: M11Gate3ConfirmationConfig
    roots: Gate3RootBinding

    def __init__(
        self,
        *,
        repository_root: Path,
        gate1_artifact: M11Gate1RunArtifact,
        gate2_artifact: M11Gate2RunArtifact,
        gate3_config: M11Gate3ConfirmationConfig,
        roots: Gate3RootBinding,
        _authentication_token: object | None = None,
    ) -> None:
        if _authentication_token is not _GATE3_PREAUTHENTICATED_INPUTS_TOKEN:
            raise M11Gate3RunnerError(
                "Gate 3 preauthenticated inputs must be created by the full authenticator"
            )
        object.__setattr__(self, "repository_root", repository_root)
        object.__setattr__(self, "gate1_artifact", gate1_artifact)
        object.__setattr__(self, "gate2_artifact", gate2_artifact)
        object.__setattr__(self, "gate3_config", gate3_config)
        object.__setattr__(self, "roots", roots)
        self.__post_init__()

    def __post_init__(self) -> None:
        root = Path(self.repository_root).resolve()
        object.__setattr__(self, "repository_root", root)
        _require_authenticated_parent_alignment(
            self.gate1_artifact,
            self.gate2_artifact,
            self.gate3_config,
        )
        if self.roots != _build_roots(
            self.gate1_artifact,
            self.gate2_artifact,
            self.gate3_config,
        ):
            raise M11Gate3RunnerError(
                "preauthenticated Gate 3 roots differ from their in-memory parents"
            )


def gate3_adapter_runtime_config_sha256(config: M11Gate3ConfirmationConfig) -> str:
    """Commit the complete outcome-blind adapter/executor semantics under a separate domain."""

    strict = M11Gate3ConfirmationConfig.model_validate(
        config.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-adapter-runtime-config.v1",
        "gate3_config_id": strict.config_id,
        "gate3_config_content_sha256": strict.content_sha256,
        "projection": strict.projection.model_dump(mode="json"),
        "policy": strict.policy.model_dump(mode="json"),
        "control_execution": {
            "accounting_rule": strict.controls.accounting_rule,
            "candidate_parity_rule": strict.controls.candidate_parity_rule,
            "exact_audit_arm_registry": [
                item.model_dump(mode="json") for item in strict.controls.exact_audit_arm_registry
            ],
            "exact_short_case_rule": strict.controls.exact_short_case_rule,
            "hard_null_rule": strict.controls.hard_null_rule,
            "no_signal_rule": strict.controls.no_signal_rule,
        },
        "early_execution": {
            "validity_controls_precede_central": (
                strict.execution.validity_controls_precede_central
            ),
            "central_order": strict.execution.central_order,
            "terminal_skip_marker": strict.execution.terminal_skip_marker,
        },
        "confirmation_protocol_schema_version": ("yieldforge.m11-gate3-early-confirmation.v1"),
        "adapter_attestation_schema_version": ("yieldforge.m11-m7-runtime-attestation.v1"),
    }
    return f"sha256:{semantic_sha256(semantic)}"


class M11Gate3EarlyRunArtifact(FrozenExperimentModel):
    """Content-addressed envelope around one official early-confirmation result."""

    model_config = ConfigDict(
        **FrozenExperimentModel.model_config,
        revalidate_instances="always",
    )

    schema_version: Literal["yieldforge.m11-gate3-early-run.v1"] = (
        "yieldforge.m11-gate3-early-run.v1"
    )
    run_id: StrictStr = Field(pattern=r"^yfm11g3run-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    gate1_run_id: StrictStr = Field(pattern=r"^yfm11g1run-[0-9a-f]{24}$")
    gate1_run_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate1_evaluation_result_id: StrictStr = Field(pattern=r"^yfm11g1r-[0-9a-f]{24}$")
    gate1_evaluation_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate2_run_id: StrictStr = Field(pattern=r"^yfm11g2run-[0-9a-f]{24}$")
    gate2_run_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate2_evaluation_result_id: StrictStr = Field(pattern=r"^yfm11g2r-[0-9a-f]{24}$")
    gate2_evaluation_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate3_config_id: StrictStr = Field(pattern=r"^yfm11g3c-[0-9a-f]{24}$")
    gate3_config_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    adapter_runtime_config_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    result_id: StrictStr = Field(pattern=r"^yfm11g3early-[0-9a-f]{24}$")
    result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    result: Gate3EarlyConfirmationResult
    status: Gate3EarlyStatus
    disposition: Gate3EarlyDisposition
    terminal: StrictBool
    productization_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_bound_result_and_identity(self) -> Self:
        roots = self.roots
        if (
            self.gate1_run_id,
            self.gate1_run_content_sha256,
            self.gate1_evaluation_result_id,
            self.gate1_evaluation_result_content_sha256,
            self.gate2_run_id,
            self.gate2_run_content_sha256,
            self.gate2_evaluation_result_id,
            self.gate2_evaluation_result_content_sha256,
            self.gate3_config_id,
            self.gate3_config_content_sha256,
            self.adapter_runtime_config_sha256,
        ) != (
            roots.gate1_run_id,
            roots.gate1_run_content_sha256,
            roots.gate1_evaluation_result_id,
            roots.gate1_evaluation_result_content_sha256,
            roots.gate2_run_id,
            roots.gate2_run_content_sha256,
            roots.gate2_evaluation_result_id,
            roots.gate2_evaluation_result_content_sha256,
            roots.gate3_config_id,
            roots.gate3_config_content_sha256,
            roots.adapter_runtime_config_sha256,
        ):
            raise ValueError("Gate 3 run parent IDs or hashes differ from its root binding")
        if (
            self.result.roots != roots
            or self.result_id != self.result.result_id
            or self.result_content_sha256 != self.result.content_sha256
            or (self.status, self.disposition, self.terminal)
            != (self.result.status, self.result.disposition, self.result.terminal)
            or self.productization_authorized
        ):
            raise ValueError("Gate 3 run differs from its nested early-confirmation result")
        digest = semantic_sha256(self, excluded_fields={"run_id", "content_sha256"})
        if self.run_id != f"yfm11g3run-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 early run identity differs from semantic evidence")
        return self


def _config_root(
    config: M11Gate3ConfirmationConfig,
    role: str,
) -> M11Gate3RootBinding:
    matches = tuple(item for item in config.roots if item.role == role)
    if len(matches) != 1:
        raise M11Gate3RunnerError(f"Gate 3 config has no unique {role} root")
    return matches[0]


def _require_authenticated_parent_alignment(
    gate1: M11Gate1RunArtifact,
    gate2: M11Gate2RunArtifact,
    config: M11Gate3ConfirmationConfig,
) -> None:
    try:
        gate1_result = gate1.gate1_result
        gate2_result = gate2.gate2_result
        contract_root = _config_root(config, "m11_contract")
        population_root = _config_root(config, "m11_population")
        if (
            gate1.status != "gate_1_survived"
            or gate1.disposition != "OPEN_GATE_2"
            or gate1_result.opens_gate_2 is not True
            or gate2.status != "gate_2_survived"
            or gate2.disposition != "OPEN_GATE_3"
            or gate2_result.status != "gate_2_survived"
            or gate2.gate1_run_id != gate1.run_id
            or gate2.gate1_run_content_sha256 != gate1.content_sha256
            or gate2.gate1_result_id != gate1_result.result_id
            or gate2.gate1_result_content_sha256 != gate1_result.content_sha256
            or gate2_result.gate1_result_id != gate1_result.result_id
            or gate2_result.gate1_result_content_sha256 != gate1_result.content_sha256
            or gate1.contract_id != contract_root.semantic_id
            or gate1.contract_content_sha256 != contract_root.semantic_content_sha256
            or gate1.population_id != population_root.semantic_id
            or gate1.population_content_sha256 != population_root.semantic_content_sha256
            or gate2_result.contract_id != gate1.contract_id
            or gate2_result.contract_content_sha256 != gate1.contract_content_sha256
            or gate2_result.population_id != gate1.population_id
            or gate2_result.population_content_sha256 != gate1.population_content_sha256
        ):
            raise M11Gate3RunnerError(
                "authenticated Gate 1/Gate 2 parents differ from the frozen Gate 3 config"
            )
    except AttributeError as error:
        raise M11Gate3RunnerError("authenticated Gate 3 parent evidence is incomplete") from error


def _load_authenticated_inputs(
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    gate2_artifact_path: Path,
    gate3_config_path: Path,
) -> tuple[M11Gate1RunArtifact, M11Gate2RunArtifact, M11Gate3ConfirmationConfig]:
    root = Path(repository_root).resolve()
    gate1_path = Path(gate1_artifact_path)
    gate2_path = Path(gate2_artifact_path)
    config_path = Path(gate3_config_path)
    try:
        gate1 = load_official_gate1_run(gate1_path, repository_root=root)
        gate2 = load_official_gate2_run(
            gate2_path,
            repository_root=root,
            gate1_artifact_path=gate1_path,
        )
        config = load_gate3_confirmation_config(config_path, repository_root=root)
    except (OSError, TypeError, ValueError, ValidationError) as error:
        raise M11Gate3RunnerError("Gate 3 parent or config authentication failed") from error
    _require_authenticated_parent_alignment(gate1, gate2, config)
    return gate1, gate2, config


def _build_roots(
    gate1: M11Gate1RunArtifact,
    gate2: M11Gate2RunArtifact,
    config: M11Gate3ConfirmationConfig,
) -> Gate3RootBinding:
    return build_gate3_root_binding(
        contract_id=gate1.contract_id,
        contract_content_sha256=gate1.contract_content_sha256,
        population_id=gate1.population_id,
        population_content_sha256=gate1.population_content_sha256,
        gate1_run_id=gate1.run_id,
        gate1_run_content_sha256=gate1.content_sha256,
        gate1_evaluation_result_id=gate1.gate1_result.result_id,
        gate1_evaluation_result_content_sha256=gate1.gate1_result.content_sha256,
        gate2_run_id=gate2.run_id,
        gate2_run_content_sha256=gate2.content_sha256,
        gate2_evaluation_result_id=gate2.gate2_result.result_id,
        gate2_evaluation_result_content_sha256=gate2.gate2_result.content_sha256,
        gate3_config_id=config.config_id,
        gate3_config_content_sha256=config.content_sha256,
        adapter_runtime_config_sha256=gate3_adapter_runtime_config_sha256(config),
    )


def authenticate_official_gate3_early_inputs(
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    gate2_artifact_path: Path,
    gate3_config_path: Path,
) -> Gate3PreauthenticatedInputs:
    """Fully authenticate Gate 3 parents/config once for the preauthenticated run path."""

    root = Path(repository_root).resolve()
    gate1, gate2, config = _load_authenticated_inputs(
        repository_root=root,
        gate1_artifact_path=Path(gate1_artifact_path),
        gate2_artifact_path=Path(gate2_artifact_path),
        gate3_config_path=Path(gate3_config_path),
    )
    return Gate3PreauthenticatedInputs(
        repository_root=root,
        gate1_artifact=gate1,
        gate2_artifact=gate2,
        gate3_config=config,
        roots=_build_roots(gate1, gate2, config),
        _authentication_token=_GATE3_PREAUTHENTICATED_INPUTS_TOKEN,
    )


def build_gate3_early_run_artifact(
    *,
    roots: Gate3RootBinding,
    result: Gate3EarlyConfirmationResult,
) -> M11Gate3EarlyRunArtifact:
    """Bind one mechanically revalidated early result to every admitted root."""

    strict_roots = Gate3RootBinding.model_validate(
        roots.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    strict_result = Gate3EarlyConfirmationResult.model_validate(
        result.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-early-run.v1",
        "roots": strict_roots.model_dump(mode="json"),
        "gate1_run_id": strict_roots.gate1_run_id,
        "gate1_run_content_sha256": strict_roots.gate1_run_content_sha256,
        "gate1_evaluation_result_id": strict_roots.gate1_evaluation_result_id,
        "gate1_evaluation_result_content_sha256": (
            strict_roots.gate1_evaluation_result_content_sha256
        ),
        "gate2_run_id": strict_roots.gate2_run_id,
        "gate2_run_content_sha256": strict_roots.gate2_run_content_sha256,
        "gate2_evaluation_result_id": strict_roots.gate2_evaluation_result_id,
        "gate2_evaluation_result_content_sha256": (
            strict_roots.gate2_evaluation_result_content_sha256
        ),
        "gate3_config_id": strict_roots.gate3_config_id,
        "gate3_config_content_sha256": strict_roots.gate3_config_content_sha256,
        "adapter_runtime_config_sha256": strict_roots.adapter_runtime_config_sha256,
        "result_id": strict_result.result_id,
        "result_content_sha256": strict_result.content_sha256,
        "result": strict_result.model_dump(mode="json"),
        "status": strict_result.status,
        "disposition": strict_result.disposition,
        "terminal": strict_result.terminal,
        "productization_authorized": False,
    }
    digest = semantic_sha256(semantic)
    return M11Gate3EarlyRunArtifact(
        run_id=f"yfm11g3run-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=strict_roots,
        result=strict_result,
        **{key: value for key, value in semantic.items() if key not in {"roots", "result"}},
    )


def canonical_gate3_early_run_bytes(artifact: M11Gate3EarlyRunArtifact) -> bytes:
    """Return canonical bytes after strict detached revalidation."""

    strict = M11Gate3EarlyRunArtifact.model_validate(
        artifact.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    return canonical_pretty_json_bytes(strict)


def _validate_gate3_early_run_bytes(data: bytes) -> bytes:
    strict = M11Gate3EarlyRunArtifact.model_validate_json(data, strict=True)
    return canonical_pretty_json_bytes(strict)


def publish_gate3_early_run(
    output_directory: Path,
    artifact: M11Gate3EarlyRunArtifact,
) -> Path:
    """Publish one immutable content-addressed Gate 3 early-confirmation run."""

    strict = M11Gate3EarlyRunArtifact.model_validate(
        artifact.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    path = Path(output_directory) / f"m11-gate3-early-{strict.run_id}.json"
    try:
        return publish_immutable_artifact(
            path,
            canonical_gate3_early_run_bytes(strict),
            validate=_validate_gate3_early_run_bytes,
            label="M11 Gate 3 early-confirmation run",
        )
    except M8ArtifactPublicationError as error:
        raise M11Gate3RunnerError("Gate 3 immutable publication failed") from error


def _read_bounded_regular_file(path: Path) -> bytes:
    candidate = Path(path)
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_GATE3_EARLY_RUN_BYTES
        ):
            raise M11Gate3RunnerError("Gate 3 run must be a bounded regular file")
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            remaining = _MAX_GATE3_EARLY_RUN_BYTES + 1
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
    except M11Gate3RunnerError:
        raise
    except OSError as error:
        raise M11Gate3RunnerError("Gate 3 run could not be read safely") from error
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if (
        len(raw) > _MAX_GATE3_EARLY_RUN_BYTES
        or len(raw) != before.st_size
        or before_identity
        != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        or before_identity
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    ):
        raise M11Gate3RunnerError("Gate 3 run changed during read-back")
    return raw


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise M11Gate3RunnerError(f"duplicate Gate 3 run key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise M11Gate3RunnerError(f"nonfinite Gate 3 run constant: {value}")


def _require_preauthenticated_inputs(
    authenticated_inputs: Gate3PreauthenticatedInputs,
) -> None:
    if not isinstance(authenticated_inputs, Gate3PreauthenticatedInputs):
        raise M11Gate3RunnerError("Gate 3 preauthenticated inputs have the wrong type")
    _require_authenticated_parent_alignment(
        authenticated_inputs.gate1_artifact,
        authenticated_inputs.gate2_artifact,
        authenticated_inputs.gate3_config,
    )
    if authenticated_inputs.roots != _build_roots(
        authenticated_inputs.gate1_artifact,
        authenticated_inputs.gate2_artifact,
        authenticated_inputs.gate3_config,
    ):
        raise M11Gate3RunnerError(
            "preauthenticated Gate 3 roots differ from their in-memory parents"
        )


def load_official_gate3_early_run_preauthenticated(
    path: Path,
    *,
    authenticated_inputs: Gate3PreauthenticatedInputs,
) -> M11Gate3EarlyRunArtifact:
    """Strict-read one run against already fully authenticated in-memory roots."""

    _require_preauthenticated_inputs(authenticated_inputs)
    raw = _read_bounded_regular_file(path)
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        artifact = M11Gate3EarlyRunArtifact.model_validate_json(raw, strict=True)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise M11Gate3RunnerError("Gate 3 run is not strict canonical evidence") from error
    if raw != canonical_gate3_early_run_bytes(artifact):
        raise M11Gate3RunnerError("Gate 3 run encoding is not canonical")
    if artifact.roots != authenticated_inputs.roots:
        raise M11Gate3RunnerError(
            "Gate 3 run roots differ from preauthenticated in-memory evidence"
        )
    expected = build_gate3_early_run_artifact(
        roots=authenticated_inputs.roots,
        result=artifact.result,
    )
    if artifact != expected:
        raise M11Gate3RunnerError("Gate 3 run envelope differs from its nested result")
    return artifact


def load_official_gate3_early_run(
    path: Path,
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    gate2_artifact_path: Path,
    gate3_config_path: Path,
) -> M11Gate3EarlyRunArtifact:
    """Strict-read and freshly authenticate parents, config, roots, and nested evidence."""

    raw = _read_bounded_regular_file(path)
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        artifact = M11Gate3EarlyRunArtifact.model_validate_json(raw, strict=True)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise M11Gate3RunnerError("Gate 3 run is not strict canonical evidence") from error
    if raw != canonical_gate3_early_run_bytes(artifact):
        raise M11Gate3RunnerError("Gate 3 run encoding is not canonical")

    gate1, gate2, config = _load_authenticated_inputs(
        repository_root=repository_root,
        gate1_artifact_path=gate1_artifact_path,
        gate2_artifact_path=gate2_artifact_path,
        gate3_config_path=gate3_config_path,
    )
    expected_roots = _build_roots(gate1, gate2, config)
    if artifact.roots != expected_roots:
        raise M11Gate3RunnerError("Gate 3 run roots differ from freshly authenticated evidence")
    expected = build_gate3_early_run_artifact(
        roots=expected_roots,
        result=artifact.result,
    )
    if artifact != expected:
        raise M11Gate3RunnerError("Gate 3 run envelope differs from its nested result")
    return artifact


def run_and_publish_official_gate3_early_preauthenticated(
    *,
    authenticated_inputs: Gate3PreauthenticatedInputs,
    output_directory: Path,
    backend_factory: Gate3BackendFactory,
) -> tuple[M11Gate3EarlyRunArtifact, Path]:
    """Execute and publish using one previously fully authenticated input bundle."""

    _require_preauthenticated_inputs(authenticated_inputs)
    root = authenticated_inputs.repository_root
    gate1 = authenticated_inputs.gate1_artifact
    gate2 = authenticated_inputs.gate2_artifact
    config = authenticated_inputs.gate3_config
    roots = authenticated_inputs.roots
    try:
        backend = backend_factory(
            repository_root=root,
            gate1_artifact=gate1,
            gate2_artifact=gate2,
            gate3_config=config,
            roots=roots,
        )
        result = run_gate3_early_confirmation(roots=roots, backend=backend)
        artifact = build_gate3_early_run_artifact(roots=roots, result=result)
    except Exception as error:
        raise M11Gate3RunnerError("Gate 3 early-confirmation execution failed") from error
    try:
        path = publish_gate3_early_run(Path(output_directory), artifact)
        readback = load_official_gate3_early_run_preauthenticated(
            path,
            authenticated_inputs=authenticated_inputs,
        )
    except (OSError, TypeError, ValueError, ValidationError) as error:
        if isinstance(error, M11Gate3RunnerError):
            raise
        raise M11Gate3RunnerError("Gate 3 publication or read-back failed") from error
    if readback != artifact:
        raise M11Gate3RunnerError("Gate 3 read-back differs from the published run")
    return readback, path


def run_and_publish_official_gate3_early(
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    gate2_artifact_path: Path,
    gate3_config_path: Path,
    output_directory: Path,
    backend_factory: Gate3BackendFactory,
) -> tuple[M11Gate3EarlyRunArtifact, Path]:
    """Authenticate, execute once, publish immutably, and authenticate fresh read-back."""

    root = Path(repository_root).resolve()
    gate1_path = Path(gate1_artifact_path)
    gate2_path = Path(gate2_artifact_path)
    config_path = Path(gate3_config_path)
    gate1, gate2, config = _load_authenticated_inputs(
        repository_root=root,
        gate1_artifact_path=gate1_path,
        gate2_artifact_path=gate2_path,
        gate3_config_path=config_path,
    )
    roots = _build_roots(gate1, gate2, config)
    try:
        backend = backend_factory(
            repository_root=root,
            gate1_artifact=gate1,
            gate2_artifact=gate2,
            gate3_config=config,
            roots=roots,
        )
        result = run_gate3_early_confirmation(roots=roots, backend=backend)
        artifact = build_gate3_early_run_artifact(roots=roots, result=result)
    except Exception as error:
        raise M11Gate3RunnerError("Gate 3 early-confirmation execution failed") from error
    try:
        path = publish_gate3_early_run(Path(output_directory), artifact)
        readback = load_official_gate3_early_run(
            path,
            repository_root=root,
            gate1_artifact_path=gate1_path,
            gate2_artifact_path=gate2_path,
            gate3_config_path=config_path,
        )
    except (OSError, TypeError, ValueError, ValidationError) as error:
        if isinstance(error, M11Gate3RunnerError):
            raise
        raise M11Gate3RunnerError("Gate 3 publication or read-back failed") from error
    if readback != artifact:
        raise M11Gate3RunnerError("Gate 3 read-back differs from the published run")
    return readback, path


__all__ = [
    "Gate3BackendFactory",
    "Gate3PreauthenticatedInputs",
    "M11Gate3EarlyRunArtifact",
    "M11Gate3RunnerError",
    "authenticate_official_gate3_early_inputs",
    "build_gate3_early_run_artifact",
    "canonical_gate3_early_run_bytes",
    "gate3_adapter_runtime_config_sha256",
    "load_official_gate3_early_run",
    "load_official_gate3_early_run_preauthenticated",
    "publish_gate3_early_run",
    "run_and_publish_official_gate3_early",
    "run_and_publish_official_gate3_early_preauthenticated",
]
