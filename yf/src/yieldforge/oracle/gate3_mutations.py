"""Executed, content-addressed mutation checks for the M8 Gate-3 audit."""

from __future__ import annotations

import hashlib
import math
import os
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from yieldforge.baseline.replay import (
    enumerate_m7_action_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.concurrency import (
    M8_GATE3_CONCURRENCY_BUDGET,
    activate_m8_translation_audit_processes,
)
from yieldforge.oracle.experiment import (
    M8CertificateProofResult,
    M8PortableFactGate3Result,
    _ExecutionCell,
    _portable_registry_state,
    _request_for_cell,
    _run_process_phase,
)
from yieldforge.oracle.fact_checker import (
    M8CheckedFactBundleResult,
    M8FactBundleCheckRequest,
    check_m8_fact_bundle,
)
from yieldforge.oracle.facts import (
    M8ActionRootV2,
    M8UncheckedFactBundleV2,
    canonical_semantic_json,
    m8_bundle_sha256,
    m8_fact_sha256,
)
from yieldforge.oracle.gate3_evidence import (
    M8Gate3AuditSample,
    M8Gate3CheckedRootManifest,
    M8Gate3MutationManifest,
    M8Gate3MutationOutcome,
    M8Gate3MutationRecipeBinding,
    M8Gate3MutationResult,
    M8Gate3RootMembershipAttestation,
    build_gate3_mutation_manifest,
    finalize_gate3_mutation_execution,
    freeze_gate3_audit_sample,
    require_official_portable_gate3,
)
from yieldforge.oracle.proofs import m8_suffix_sha256
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.temporal_benchmark.contracts import TemporalPartition, TemporalRegime


class M8Gate3ObservedMutationFailureCode(StrEnum):
    PARENT_V3_BINDING_MISMATCH = "parent_v3_binding_mismatch"
    PORTABLE_GATE3_BINDING_MISMATCH = "portable_gate3_binding_mismatch"
    ROOT_MANIFEST_BINDING_MISMATCH = "root_manifest_binding_mismatch"
    SAMPLE_BINDING_MISMATCH = "sample_binding_mismatch"
    CHECKED_ACTION_ROOT_BINDING_MISMATCH = "checked_action_root_binding_mismatch"


class M8Gate3MutationExecutionError(ValueError):
    """A mutation could not be proven coherent, rehashed, and cross-bound."""


@dataclass(frozen=True)
class M8Gate3MutationCheckerContext:
    """Picklable authority inputs used to rebuild one real checker request in-worker."""

    execution_cell: _ExecutionCell
    rules: ResidualRuleSet
    jagua_executable: Path | None
    freeze_id: str
    freeze_sha256: str
    expected_jagua_sha256: str | None
    translation_audit_processes: int

    def __post_init__(self) -> None:
        if type(self.execution_cell) is not _ExecutionCell:
            raise TypeError("mutation checker context requires an exact execution cell")
        if type(self.rules) is not ResidualRuleSet:
            raise TypeError("mutation checker context requires an exact residual rule set")
        stream = self.execution_cell.stream
        if type(stream) is not tuple or not stream:
            raise ValueError("mutation checker context requires one nonempty stream")
        dimensions = {
            (item.regime, item.temporal_seed, item.stream_id, item.partition) for item in stream
        }
        if len(dimensions) != 1 or next(iter(dimensions))[3] is not TemporalPartition.CALIBRATION:
            raise ValueError("mutation checker context must remain in one calibration cell")
        replay_instances = self.execution_cell.replay_input.instances  # type: ignore[attr-defined]
        if (
            type(replay_instances) is not tuple
            or not replay_instances
            or any(item.partition is not TemporalPartition.CALIBRATION for item in replay_instances)
            or replay_instances != stream
        ):
            raise ValueError("mutation checker authority must equal its calibration stream")
        if self.jagua_executable is None:
            if self.expected_jagua_sha256 is not None:
                raise ValueError("mutation checker Jagua binding is incomplete")
        elif (
            not isinstance(self.jagua_executable, Path)
            or type(self.expected_jagua_sha256) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.expected_jagua_sha256) is None
        ):
            raise ValueError("mutation checker Jagua binding is invalid")
        freeze_id = re.fullmatch(r"yfm7freeze-([0-9a-f]{24})", self.freeze_id)
        freeze_sha = re.fullmatch(r"sha256:([0-9a-f]{64})", self.freeze_sha256)
        if (
            freeze_id is None
            or freeze_sha is None
            or freeze_id.group(1) != freeze_sha.group(1)[:24]
        ):
            raise ValueError("mutation checker freeze binding is invalid")
        if (
            type(self.translation_audit_processes) is not int
            or not 1 <= self.translation_audit_processes <= 2
        ):
            raise ValueError("mutation checker audit width is outside the frozen 1..2 boundary")

    @property
    def regime(self) -> TemporalRegime:
        regime = self.execution_cell.stream[0].regime
        if type(regime) is not TemporalRegime:
            raise TypeError("mutation checker regime is not an exact temporal regime")
        return regime


@dataclass(frozen=True)
class _RetainedRootSource:
    root: M8ActionRootV2
    semantic_bundle_bytes: bytes
    bundle_sha256: str
    semantic_bundle_bytes_sha256: str
    regime: TemporalRegime
    temporal_seed: int
    stream_id: str


@dataclass(frozen=True)
class _MutationWorkerResult:
    recipe_id: str
    observed_failure_code: M8Gate3ObservedMutationFailureCode
    mutated_content_sha256: str
    checker_failure_code: str | None
    first_failing_fact_sha256: str | None
    rehash_performed: bool
    mutation_rejected: bool
    worker_fork_count: int
    surviving_registry_count: int
    artifact_published: bool
    evaluation_accessed: bool


@dataclass
class _MutationCapabilityTelemetry:
    artifact_published: bool = False
    evaluation_accessed: bool = False
    fork_count: int = 0


@dataclass(frozen=True)
class _ActionRootCheckerFailure:
    observed_failure_code: M8Gate3ObservedMutationFailureCode
    checker_failure_code: str
    first_failing_fact_sha256: str


@dataclass(frozen=True)
class _MutationBindingContext:
    parent_v3: M8CertificateProofResult
    portable_fact_gate3: M8PortableFactGate3Result
    root_manifest: M8Gate3CheckedRootManifest
    sample: M8Gate3AuditSample


type _TypedArtifactTarget = (
    M8CertificateProofResult
    | M8PortableFactGate3Result
    | M8Gate3CheckedRootManifest
    | M8Gate3AuditSample
    | M8ActionRootV2
)


_TARGET_MODEL = {
    "parent_v3_certificate": M8CertificateProofResult,
    "portable_fact_gate3": M8PortableFactGate3Result,
    "checked_root_manifest": M8Gate3CheckedRootManifest,
    "audit_sample": M8Gate3AuditSample,
    "checked_action_root": M8ActionRootV2,
}
_FAILURE_CODE_BY_TARGET: dict[str, M8Gate3ObservedMutationFailureCode] = {
    "parent_v3_certificate": (M8Gate3ObservedMutationFailureCode.PARENT_V3_BINDING_MISMATCH),
    "portable_fact_gate3": (M8Gate3ObservedMutationFailureCode.PORTABLE_GATE3_BINDING_MISMATCH),
    "checked_root_manifest": (M8Gate3ObservedMutationFailureCode.ROOT_MANIFEST_BINDING_MISMATCH),
    "audit_sample": M8Gate3ObservedMutationFailureCode.SAMPLE_BINDING_MISMATCH,
    "checked_action_root": (
        M8Gate3ObservedMutationFailureCode.CHECKED_ACTION_ROOT_BINDING_MISMATCH
    ),
}


def _install_mutation_capability_guard(
    *,
    allowed_executable: Path | None = None,
    fork_limit: int = 0,
) -> _MutationCapabilityTelemetry:
    """Deny external capabilities except the pinned checker runtime and bounded audits."""

    if allowed_executable is not None and not isinstance(allowed_executable, Path):
        raise TypeError("mutation capability executable allowance requires an exact path")
    if type(fork_limit) is not int or not 0 <= fork_limit <= 2:
        raise ValueError("mutation capability fork limit is outside the frozen 0..2 boundary")
    allowed_path = (
        os.path.abspath(os.fspath(allowed_executable)) if allowed_executable is not None else None
    )
    telemetry = _MutationCapabilityTelemetry()
    write_events = {
        "os.chdir",
        "os.chmod",
        "os.chown",
        "os.link",
        "os.mkdir",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.symlink",
        "os.truncate",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.rmtree",
    }
    evaluation_events = {
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "urllib.Request",
    }
    fork_events = {
        "os.fork",
    }
    process_events = {
        "os.exec",
        "os.forkpty",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.spawn",
        "os.system",
        "pty.spawn",
        "subprocess.Popen",
    }

    def is_allowed_path(value: object) -> bool:
        if allowed_path is None or not isinstance(value, (str, bytes, os.PathLike)):
            return False
        return os.path.abspath(os.fsdecode(os.fspath(value))) == allowed_path

    def is_allowed_process(event: str, args: tuple[object, ...]) -> bool:
        if event not in {
            "os.exec",
            "os.posix_spawn",
            "os.posix_spawnp",
            "subprocess.Popen",
        }:
            return False
        return bool(args) and is_allowed_path(args[0])

    def deny_capability(event: str, args: tuple[object, ...]) -> None:
        if event == "open":
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            writes = (
                isinstance(mode, str) and any(marker in mode for marker in ("w", "a", "x", "+"))
            ) or (
                isinstance(flags, int)
                and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            )
            if writes:
                telemetry.artifact_published = True
                raise PermissionError(
                    "mutation worker capability guard denied filesystem publication"
                )
            if is_allowed_path(args[0] if args else None):
                return
            telemetry.evaluation_accessed = True
            raise PermissionError(
                "mutation worker capability guard denied unauthorized filesystem access"
            )
        if event in write_events:
            telemetry.artifact_published = True
            raise PermissionError("mutation worker capability guard denied publication")
        if event in evaluation_events:
            telemetry.evaluation_accessed = True
            raise PermissionError("mutation worker capability guard denied evaluation access")
        if event in fork_events:
            if telemetry.fork_count >= fork_limit:
                raise PermissionError(
                    "mutation worker capability guard exceeded its bounded fork count"
                )
            telemetry.fork_count += 1
            return
        if event in process_events:
            if is_allowed_process(event, args):
                return
            raise PermissionError("mutation worker capability guard denied child process creation")

    sys.addaudithook(deny_capability)
    return telemetry


def _replacement_sha256(recipe: M8Gate3MutationRecipeBinding, field_name: str) -> str:
    digest = hashlib.sha256(
        (
            "yieldforge.m8.gate3.executed-mutation.v1\0"
            f"{recipe.recipe_id}\0{recipe.target_content_sha256}\0{field_name}"
        ).encode()
    ).hexdigest()
    replacement = f"sha256:{digest}"
    if replacement == recipe.target_content_sha256:
        raise M8Gate3MutationExecutionError("deterministic mutation did not change identity")
    return replacement


def _strict_recipe(recipe: M8Gate3MutationRecipeBinding) -> M8Gate3MutationRecipeBinding:
    if type(recipe) is not M8Gate3MutationRecipeBinding:
        raise M8Gate3MutationExecutionError("mutation requires an exact recipe model")
    return M8Gate3MutationRecipeBinding.model_validate_json(
        recipe.model_dump_json(),
        strict=True,
    )


def _strict_artifact_target(
    recipe: M8Gate3MutationRecipeBinding,
    target: object,
) -> _TypedArtifactTarget:
    model = _TARGET_MODEL.get(recipe.target_kind)
    if model is None or type(target) is not model:
        raise M8Gate3MutationExecutionError("mutation target type differs from its recipe")
    strict = model.model_validate_json(target.model_dump_json(), strict=True)  # type: ignore[union-attr]
    content_sha256 = strict.fact_sha256 if type(strict) is M8ActionRootV2 else strict.content_sha256
    if content_sha256 != recipe.target_content_sha256:
        raise M8Gate3MutationExecutionError("mutation target differs from its recipe")
    return strict


def _mutate_parent(parent: M8CertificateProofResult) -> M8CertificateProofResult:
    payload = parent.model_dump(mode="python")
    changed_wall = math.nextafter(parent.total_wall_seconds, math.inf)
    if not math.isfinite(changed_wall) or changed_wall == parent.total_wall_seconds:
        raise M8Gate3MutationExecutionError("parent wall-time mutation is not finite")
    payload["total_wall_seconds"] = changed_wall
    digest = semantic_sha256(
        payload,
        excluded_fields={"proof_id", "content_sha256"},
    )
    payload["proof_id"] = f"yfm8proof-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return M8CertificateProofResult.model_validate(payload, strict=True)


def _mutate_portable(
    gate3: M8PortableFactGate3Result,
) -> M8PortableFactGate3Result:
    payload = gate3.model_dump(mode="python")
    changed_wall = math.nextafter(gate3.total_pipeline_wall_seconds, math.inf)
    if not math.isfinite(changed_wall) or changed_wall == gate3.total_pipeline_wall_seconds:
        raise M8Gate3MutationExecutionError("portable wall-time mutation is not finite")
    payload["total_pipeline_wall_seconds"] = changed_wall
    digest = semantic_sha256(
        payload,
        excluded_fields={"gate3_id", "content_sha256"},
    )
    payload["gate3_id"] = f"yfm8gate3-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return M8PortableFactGate3Result.model_validate(payload, strict=True)


def _mutate_root_manifest(
    recipe: M8Gate3MutationRecipeBinding,
    manifest: M8Gate3CheckedRootManifest,
) -> M8Gate3CheckedRootManifest:
    attestation_payload = manifest.membership_attestation.model_dump(mode="python")
    attestation_payload["producer_id"] = (
        f"{manifest.membership_attestation.producer_id}|mutation={recipe.recipe_id}"
    )
    attestation_digest = semantic_sha256(
        attestation_payload,
        excluded_fields={"attestation_id", "content_sha256"},
    )
    attestation_payload["attestation_id"] = f"yfm8g3membership-{attestation_digest[:24]}"
    attestation_payload["content_sha256"] = f"sha256:{attestation_digest}"
    changed_attestation = M8Gate3RootMembershipAttestation.model_validate(
        attestation_payload,
        strict=True,
    )

    payload = manifest.model_dump(mode="python")
    payload["membership_attestation"] = changed_attestation
    hash_payload = manifest.model_dump(mode="json")
    hash_payload["membership_attestation"] = changed_attestation.model_dump(mode="json")
    digest = semantic_sha256(
        hash_payload,
        excluded_fields={"manifest_id", "content_sha256"},
    )
    payload["manifest_id"] = f"yfm8g3roots-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return M8Gate3CheckedRootManifest.model_validate(payload, strict=True)


def _mutate_sample(
    recipe: M8Gate3MutationRecipeBinding,
    sample: M8Gate3AuditSample,
) -> M8Gate3AuditSample:
    payload = sample.model_dump(mode="python")
    payload["root_manifest_content_sha256"] = _replacement_sha256(
        recipe,
        "root_manifest_content_sha256",
    )
    digest = semantic_sha256(
        payload,
        excluded_fields={"sample_id", "content_sha256"},
    )
    payload["sample_id"] = f"yfm8g3sample-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return M8Gate3AuditSample.model_validate(payload, strict=True)


def _mutate_action_root(
    recipe: M8Gate3MutationRecipeBinding,
    root: M8ActionRootV2,
) -> M8ActionRootV2:
    payload = root.model_dump(mode="python")
    payload["final_state_sha256"] = _replacement_sha256(recipe, "final_state_sha256")
    payload["fact_sha256"] = m8_fact_sha256("action_root", payload)
    return M8ActionRootV2.model_validate(payload, strict=True)


def _build_artifact_mutation(
    recipe: M8Gate3MutationRecipeBinding,
    target: _TypedArtifactTarget,
) -> _TypedArtifactTarget:
    if recipe.target_kind == "parent_v3_certificate":
        if type(target) is not M8CertificateProofResult:
            raise M8Gate3MutationExecutionError("parent mutation target type differs")
        return _mutate_parent(target)
    if recipe.target_kind == "portable_fact_gate3":
        if type(target) is not M8PortableFactGate3Result:
            raise M8Gate3MutationExecutionError("portable mutation target type differs")
        return _mutate_portable(target)
    if recipe.target_kind == "checked_root_manifest":
        if type(target) is not M8Gate3CheckedRootManifest:
            raise M8Gate3MutationExecutionError("root-manifest mutation target type differs")
        return _mutate_root_manifest(recipe, target)
    if recipe.target_kind == "audit_sample":
        if type(target) is not M8Gate3AuditSample:
            raise M8Gate3MutationExecutionError("sample mutation target type differs")
        return _mutate_sample(recipe, target)
    if recipe.target_kind == "checked_action_root":
        if type(target) is not M8ActionRootV2:
            raise M8Gate3MutationExecutionError("action-root mutation target type differs")
        return _mutate_action_root(recipe, target)
    raise M8Gate3MutationExecutionError("mutation target kind is not an artifact")


def _direct_binding_failure(
    recipe: M8Gate3MutationRecipeBinding,
    original: _TypedArtifactTarget,
    mutated: _TypedArtifactTarget,
) -> M8Gate3ObservedMutationFailureCode:
    """Reject a changed target against its authoritative registered identity."""

    rejected = False
    if recipe.target_kind == "parent_v3_certificate":
        rejected = (
            type(original) is M8CertificateProofResult
            and type(mutated) is M8CertificateProofResult
            and (
                mutated.proof_id != original.proof_id
                or mutated.content_sha256 != original.content_sha256
            )
        )
    elif recipe.target_kind == "portable_fact_gate3":
        rejected = (
            type(original) is M8PortableFactGate3Result
            and type(mutated) is M8PortableFactGate3Result
            and (
                mutated.gate3_id != original.gate3_id
                or mutated.content_sha256 != original.content_sha256
            )
        )
    elif recipe.target_kind == "checked_root_manifest":
        rejected = (
            type(original) is M8Gate3CheckedRootManifest
            and type(mutated) is M8Gate3CheckedRootManifest
            and (
                mutated.manifest_id != original.manifest_id
                or mutated.content_sha256 != original.content_sha256
                or mutated.membership_attestation.producer_id
                != original.membership_attestation.producer_id
            )
        )
    elif recipe.target_kind == "audit_sample":
        rejected = (
            type(original) is M8Gate3AuditSample
            and type(mutated) is M8Gate3AuditSample
            and (
                mutated.sample_id != original.sample_id
                or mutated.content_sha256 != original.content_sha256
                or mutated.root_manifest_content_sha256 != original.root_manifest_content_sha256
            )
        )
    elif recipe.target_kind == "checked_action_root":
        rejected = (
            type(original) is M8ActionRootV2
            and type(mutated) is M8ActionRootV2
            and mutated.fact_sha256 != original.fact_sha256
        )
    if not rejected:
        raise M8Gate3MutationExecutionError(
            "independent registered-identity consumer accepted the mutation"
        )
    return _FAILURE_CODE_BY_TARGET[recipe.target_kind]


def _independent_gate3_binding_failure(
    recipe: M8Gate3MutationRecipeBinding,
    mutated: _TypedArtifactTarget,
    context: _MutationBindingContext,
) -> M8Gate3ObservedMutationFailureCode:
    """Exercise the authoritative Gate-3 binding path independently of mutation creation."""

    expected_code = _FAILURE_CODE_BY_TARGET[recipe.target_kind]
    if recipe.target_kind == "parent_v3_certificate":
        if type(mutated) is not M8CertificateProofResult:
            raise M8Gate3MutationExecutionError("parent binding consumer received wrong type")
        try:
            candidate = freeze_gate3_audit_sample(mutated, context.root_manifest)
        except (TypeError, ValueError, ValidationError):
            return expected_code
        if candidate == context.sample:
            raise M8Gate3MutationExecutionError(
                "authoritative sample freeze accepted the parent mutation"
            )
        return expected_code
    if recipe.target_kind == "portable_fact_gate3":
        if type(mutated) is not M8PortableFactGate3Result:
            raise M8Gate3MutationExecutionError("portable consumer received wrong type")
        try:
            require_official_portable_gate3(
                mutated,
                label="M8 Gate-3 portable mutation consumer",
            )
        except (TypeError, ValueError, ValidationError):
            return expected_code
        raise M8Gate3MutationExecutionError(
            "official portable artifact consumer accepted the mutation"
        )
    if recipe.target_kind == "checked_root_manifest":
        if type(mutated) is not M8Gate3CheckedRootManifest:
            raise M8Gate3MutationExecutionError("root consumer received wrong type")
        try:
            candidate_sample = freeze_gate3_audit_sample(context.parent_v3, mutated)
        except (TypeError, ValueError, ValidationError):
            return expected_code
        if candidate_sample == context.sample:
            raise M8Gate3MutationExecutionError(
                "authoritative sample freeze accepted the root-manifest mutation"
            )
        return expected_code
    if recipe.target_kind == "audit_sample":
        if type(mutated) is not M8Gate3AuditSample:
            raise M8Gate3MutationExecutionError("sample consumer received wrong type")
        authoritative = freeze_gate3_audit_sample(
            context.parent_v3,
            context.root_manifest,
        )
        if authoritative != context.sample:
            raise M8Gate3MutationExecutionError(
                "authoritative sample context differs before mutation"
            )
        if mutated == authoritative:
            raise M8Gate3MutationExecutionError(
                "authoritative sample comparison accepted the mutation"
            )
        return expected_code
    if recipe.target_kind == "checked_action_root":
        raise M8Gate3MutationExecutionError(
            "action-root mutations require the independent full fact checker"
        )
    raise M8Gate3MutationExecutionError("binding consumer target kind is unsupported")


def mutate_gate3_target(
    recipe: M8Gate3MutationRecipeBinding,
    target: _TypedArtifactTarget,
) -> _TypedArtifactTarget:
    """Build one deterministic coherent mutation and recompute its content identity."""

    strict_recipe = _strict_recipe(recipe)
    strict_target = _strict_artifact_target(strict_recipe, target)
    return _build_artifact_mutation(strict_recipe, strict_target)


def validate_gate3_mutation_cross_binding(
    recipe: M8Gate3MutationRecipeBinding,
    original: _TypedArtifactTarget,
    mutated: _TypedArtifactTarget,
) -> M8Gate3ObservedMutationFailureCode:
    """Validate the exact typed diff, then return its stable binding failure code."""

    strict_recipe = _strict_recipe(recipe)
    strict_original = _strict_artifact_target(strict_recipe, original)
    model = _TARGET_MODEL.get(strict_recipe.target_kind)
    if model is None or type(mutated) is not model:
        raise M8Gate3MutationExecutionError("mutated target type differs from its recipe")
    try:
        strict_mutated = model.model_validate_json(mutated.model_dump_json(), strict=True)
    except (AttributeError, TypeError, ValueError, ValidationError) as error:
        raise M8Gate3MutationExecutionError(
            "mutated target is not a strict typed object"
        ) from error
    expected = _build_artifact_mutation(strict_recipe, strict_original)
    if strict_mutated != expected:
        raise M8Gate3MutationExecutionError("mutation contains an unregistered change")
    observed = _direct_binding_failure(strict_recipe, strict_original, strict_mutated)
    if observed.value != strict_recipe.expected_failure_code:
        raise M8Gate3MutationExecutionError("recipe failure code differs from target kind")
    return observed


def validate_gate3_mutation_payload(
    recipe: M8Gate3MutationRecipeBinding,
    original: _TypedArtifactTarget,
    mutated_payload: dict[str, object],
) -> M8Gate3ObservedMutationFailureCode:
    """Strict-load a mutated payload before applying the explicit cross-binding check."""

    strict_recipe = _strict_recipe(recipe)
    model = _TARGET_MODEL.get(strict_recipe.target_kind)
    if model is None or type(mutated_payload) is not dict:
        raise M8Gate3MutationExecutionError("mutation payload target kind is unsupported")
    try:
        mutated = model.model_validate(mutated_payload, strict=True)
    except (TypeError, ValueError, ValidationError) as error:
        raise M8Gate3MutationExecutionError(
            "mutation payload is not a strict typed object"
        ) from error
    return validate_gate3_mutation_cross_binding(strict_recipe, original, mutated)


def _load_gate3_retained_root_sources(
    retained_bundle_bytes: tuple[bytes, ...],
    target_root_sha256s: tuple[str, ...],
) -> tuple[_RetainedRootSource, ...]:
    """Strict-load canonical bundles and retain the source binding for each target root."""

    if type(retained_bundle_bytes) is not tuple or not retained_bundle_bytes:
        raise M8Gate3MutationExecutionError("retained mutation bundles are absent")
    if (
        type(target_root_sha256s) is not tuple
        or not target_root_sha256s
        or len(target_root_sha256s) != len(set(target_root_sha256s))
        or any(
            type(item) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
            for item in target_root_sha256s
        )
    ):
        raise M8Gate3MutationExecutionError("mutation target root identities are invalid")

    by_hash: dict[str, list[_RetainedRootSource]] = {}
    for raw in retained_bundle_bytes:
        if type(raw) is not bytes or not raw or len(raw) > 134_217_728:
            raise M8Gate3MutationExecutionError("retained mutation bundle bytes are invalid")
        try:
            bundle = M8UncheckedFactBundleV2.model_validate_json(raw, strict=True)
        except (TypeError, ValueError, ValidationError) as error:
            raise M8Gate3MutationExecutionError(
                "retained mutation bundle is not a strict typed bundle"
            ) from error
        if canonical_semantic_json(bundle.model_dump(mode="json")) != raw:
            raise M8Gate3MutationExecutionError("retained mutation bundle bytes are not canonical")
        try:
            regime = TemporalRegime(bundle.provenance.regime)
        except ValueError as error:
            raise M8Gate3MutationExecutionError(
                "retained mutation bundle regime is unsupported"
            ) from error
        raw_sha256 = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        for root in bundle.action_roots:
            if root.fact_sha256 in target_root_sha256s:
                by_hash.setdefault(root.fact_sha256, []).append(
                    _RetainedRootSource(
                        root=root,
                        semantic_bundle_bytes=raw,
                        bundle_sha256=bundle.bundle_sha256,
                        semantic_bundle_bytes_sha256=raw_sha256,
                        regime=regime,
                        temporal_seed=bundle.provenance.temporal_seed,
                        stream_id=bundle.provenance.stream_id,
                    )
                )

    if set(by_hash) != set(target_root_sha256s) or any(
        len(matches) != 1 for matches in by_hash.values()
    ):
        raise M8Gate3MutationExecutionError(
            "retained mutation roots are missing, duplicated, or unrelated"
        )
    return tuple(by_hash[item][0] for item in target_root_sha256s)


def load_gate3_retained_action_roots(
    retained_bundle_bytes: tuple[bytes, ...],
    target_root_sha256s: tuple[str, ...],
) -> tuple[M8ActionRootV2, ...]:
    """Strict-load canonical retained bundles and resolve each target root exactly once."""

    return tuple(
        item.root
        for item in _load_gate3_retained_root_sources(
            retained_bundle_bytes,
            target_root_sha256s,
        )
    )


def _strict_execution_inputs(
    manifest: M8Gate3MutationManifest,
    parent_v3: M8CertificateProofResult,
    portable_fact_gate3: M8PortableFactGate3Result,
    root_manifest: M8Gate3CheckedRootManifest,
    sample: M8Gate3AuditSample,
) -> tuple[
    M8Gate3MutationManifest,
    M8CertificateProofResult,
    M8PortableFactGate3Result,
    M8Gate3CheckedRootManifest,
    M8Gate3AuditSample,
]:
    exact = (
        (manifest, M8Gate3MutationManifest),
        (parent_v3, M8CertificateProofResult),
        (portable_fact_gate3, M8PortableFactGate3Result),
        (root_manifest, M8Gate3CheckedRootManifest),
        (sample, M8Gate3AuditSample),
    )
    if any(type(value) is not model for value, model in exact):
        raise M8Gate3MutationExecutionError("mutation execution inputs have unexpected types")
    try:
        registered = M8Gate3MutationManifest.model_validate_json(
            manifest.model_dump_json(), strict=True
        )
        parent = M8CertificateProofResult.model_validate_json(
            parent_v3.model_dump_json(), strict=True
        )
        gate3 = M8PortableFactGate3Result.model_validate_json(
            portable_fact_gate3.model_dump_json(), strict=True
        )
        roots = M8Gate3CheckedRootManifest.model_validate_json(
            root_manifest.model_dump_json(), strict=True
        )
        frozen = M8Gate3AuditSample.model_validate_json(sample.model_dump_json(), strict=True)
    except (TypeError, ValueError, ValidationError) as error:
        raise M8Gate3MutationExecutionError(
            "mutation execution inputs are not strict typed artifacts"
        ) from error

    required = {
        parent.content_sha256,
        gate3.content_sha256,
        roots.content_sha256,
        frozen.content_sha256,
    }
    additional = tuple(item for item in registered.base_content_sha256s if item not in required)
    try:
        rebuilt = build_gate3_mutation_manifest(
            parent,
            gate3,
            roots,
            frozen,
            harness_id=registered.authorized_harness_id,
            harness_content_sha256=registered.authorized_harness_content_sha256,
            runtime_id=registered.authorized_runtime_id,
            runtime_content_sha256=registered.authorized_runtime_content_sha256,
            additional_base_content_sha256s=additional,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise M8Gate3MutationExecutionError(
            "mutation execution inputs do not rebuild the registered manifest"
        ) from error
    if rebuilt != registered:
        raise M8Gate3MutationExecutionError(
            "mutation execution inputs differ from the registered manifest"
        )
    return registered, parent, gate3, roots, frozen


def _require_retained_cell_bindings(
    gate3: M8PortableFactGate3Result,
    retained_bundle_bytes: tuple[bytes, ...],
) -> None:
    if len(retained_bundle_bytes) != 2:
        raise M8Gate3MutationExecutionError(
            "mutation execution requires the two retained Gate-3 bundles"
        )
    by_regime: dict[TemporalRegime, tuple[M8UncheckedFactBundleV2, str]] = {}
    for raw in retained_bundle_bytes:
        if type(raw) is not bytes or not raw or len(raw) > 134_217_728:
            raise M8Gate3MutationExecutionError("retained Gate-3 bundle bytes are invalid")
        try:
            bundle = M8UncheckedFactBundleV2.model_validate_json(raw, strict=True)
        except (TypeError, ValueError, ValidationError) as error:
            raise M8Gate3MutationExecutionError(
                "retained Gate-3 bundle is not a strict typed bundle"
            ) from error
        if canonical_semantic_json(bundle.model_dump(mode="json")) != raw:
            raise M8Gate3MutationExecutionError("retained Gate-3 bundle is not canonical")
        try:
            regime = TemporalRegime(bundle.provenance.regime)
        except ValueError as error:
            raise M8Gate3MutationExecutionError(
                "retained Gate-3 bundle has an unsupported regime"
            ) from error
        if regime in by_regime:
            raise M8Gate3MutationExecutionError("retained Gate-3 bundle regime is duplicated")
        by_regime[regime] = (bundle, f"sha256:{hashlib.sha256(raw).hexdigest()}")

    if set(by_regime) != {TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT}:
        raise M8Gate3MutationExecutionError("retained Gate-3 bundle regimes differ")
    for cell in gate3.cells:
        bundle, raw_sha256 = by_regime[cell.regime]
        observed = (
            bundle.provenance.temporal_seed,
            bundle.provenance.stream_id,
            bundle.bundle_sha256,
            raw_sha256,
            len(bundle.action_roots),
            bundle.provenance.evaluation_partition_opened,
        )
        expected = (
            cell.temporal_seed,
            cell.stream_id,
            cell.first_bundle_sha256,
            cell.first_semantic_bundle_bytes_sha256,
            cell.checked_action_root_count,
            False,
        )
        if observed != expected:
            raise M8Gate3MutationExecutionError(
                "retained Gate-3 bundle differs from its portable cell"
            )


def _require_sampled_root_binding(
    source: _RetainedRootSource,
    action: object,
) -> None:
    root = source.root
    observed = (
        source.regime,
        source.temporal_seed,
        source.stream_id,
        source.bundle_sha256,
        root.fact_sha256,
        root.action_id,
        root.catalog_action_id,
        root.baseline_action_id,
        root.baseline_catalog_action_id,
        root.start_event_position,
        root.stop_event_position,
        root.suffix_sha256,
        root.semantic_runtime_sha256,
        root.start_state_sha256,
        root.initial_state_after_sha256,
        root.final_state_sha256,
    )
    expected = (
        action.regime,
        action.temporal_seed,
        action.stream_id,
        action.source_bundle_sha256,
        action.root_fact_sha256,
        action.action_id,
        action.catalog_action_id,
        action.baseline_action_id,
        action.baseline_catalog_action_id,
        action.start_event_position,
        action.stop_event_position,
        action.suffix_sha256,
        action.semantic_runtime_sha256,
        action.start_state_sha256,
        action.initial_state_after_sha256,
        action.final_state_sha256,
    )
    if observed != expected:
        raise M8Gate3MutationExecutionError(
            "sampled action root differs from its retained strict bundle"
        )


def _strict_checker_contexts(
    checker_contexts: tuple[M8Gate3MutationCheckerContext, ...],
) -> dict[TemporalRegime, M8Gate3MutationCheckerContext]:
    if type(checker_contexts) is not tuple or len(checker_contexts) != 2:
        raise M8Gate3MutationExecutionError(
            "mutation execution requires two checker authority contexts"
        )
    contexts: dict[TemporalRegime, M8Gate3MutationCheckerContext] = {}
    for context in checker_contexts:
        if type(context) is not M8Gate3MutationCheckerContext:
            raise M8Gate3MutationExecutionError(
                "mutation checker authority context has an unexpected type"
            )
        try:
            context.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise M8Gate3MutationExecutionError(
                "mutation checker authority context is invalid"
            ) from error
        if context.regime in contexts:
            raise M8Gate3MutationExecutionError("mutation checker authority regime is duplicated")
        if context.jagua_executable is not None:
            executable = context.jagua_executable
            if executable.is_symlink() or not executable.is_file():
                raise M8Gate3MutationExecutionError(
                    "mutation checker Jagua runtime is not a regular file"
                )
            observed = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
            if observed != context.expected_jagua_sha256:
                raise M8Gate3MutationExecutionError(
                    "mutation checker Jagua runtime differs from its authority"
                )
        contexts[context.regime] = context
    if set(contexts) != {TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT}:
        raise M8Gate3MutationExecutionError("mutation checker authority regimes differ from Gate 3")
    return contexts


def _mutated_action_root_bundle_bytes(
    source: _RetainedRootSource,
    original: M8ActionRootV2,
    mutated: M8ActionRootV2,
) -> bytes:
    """Replace exactly one retained root and coherently recompute the bundle root."""

    try:
        bundle = M8UncheckedFactBundleV2.model_validate_json(
            source.semantic_bundle_bytes,
            strict=True,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise M8Gate3MutationExecutionError(
            "action-root mutation source is not a strict retained bundle"
        ) from error
    payload = bundle.model_dump(mode="json")
    matches = tuple(
        index
        for index, item in enumerate(payload["action_roots"])
        if item["fact_sha256"] == original.fact_sha256
    )
    if len(matches) != 1 or original != source.root:
        raise M8Gate3MutationExecutionError(
            "action-root mutation source does not contain exactly one target"
        )
    payload["action_roots"][matches[0]] = mutated.model_dump(mode="json")
    payload["action_roots"].sort(
        key=lambda item: (
            item["stream_id"],
            item["action_id"],
            item["fact_sha256"],
        )
    )
    payload["bundle_sha256"] = m8_bundle_sha256(payload)
    raw = canonical_semantic_json(payload)
    if (
        canonical_semantic_json(payload) != raw
        or payload["bundle_sha256"] == source.bundle_sha256
        or payload["bundle_sha256"] != m8_bundle_sha256(payload)
        or sum(item == mutated.model_dump(mode="json") for item in payload["action_roots"]) != 1
        or any(item["fact_sha256"] == original.fact_sha256 for item in payload["action_roots"])
    ):
        raise M8Gate3MutationExecutionError(
            "coherently rehashed action-root bundle did not change exactly one root"
        )
    return raw


def _build_action_root_checker_request(
    semantic_bundle_bytes: bytes,
    context: M8Gate3MutationCheckerContext,
) -> M8FactBundleCheckRequest:
    request = _request_for_cell(
        context.execution_cell,
        rules=context.rules,
        jagua_executable=context.jagua_executable,  # type: ignore[arg-type]
    )
    runtime = request.runtime
    cursor = request.cursor
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    visible = request.visibility.visible_suffix(current_position=catalog.event_position)
    stop = catalog.event_position + 1 + len(visible)
    runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    return M8FactBundleCheckRequest(
        semantic_bundle_bytes=semantic_bundle_bytes,
        oracle_request=request,
        expected_semantic_runtime_sha256=runtime_sha256,
        expected_current_cursor_sha256=m7_cursor_sha256(cursor),
        expected_catalog_event_position=catalog.event_position,
        expected_catalog_action_ids=tuple(item.action_id for item in catalog.actions),
        expected_stop_event_position=stop,
        expected_suffix_sha256=m8_suffix_sha256(
            semantic_runtime_sha256=runtime_sha256,
            start_event_position=catalog.event_position,
            stop_event_position=stop,
            bindings=visible,
        ),
        expected_freeze_id=context.freeze_id,
        expected_freeze_sha256=context.freeze_sha256,
        allow_exact_replay=False,
    )


def _real_checker_action_root_failure(
    mutated: M8ActionRootV2,
    semantic_bundle_bytes: bytes,
    context: M8Gate3MutationCheckerContext,
) -> _ActionRootCheckerFailure:
    request = _build_action_root_checker_request(
        semantic_bundle_bytes,
        context,
    )
    with activate_m8_translation_audit_processes(context.translation_audit_processes):
        result = check_m8_fact_bundle(request)
    if type(result) is not M8CheckedFactBundleResult:
        raise M8Gate3MutationExecutionError(
            "action-root checker returned an unexpected result type"
        )
    try:
        checked = M8CheckedFactBundleResult.model_validate_json(
            result.model_dump_json(),
            strict=True,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise M8Gate3MutationExecutionError(
            "action-root checker result is not strict typed"
        ) from error
    if (
        checked.valid
        or checked.failure_code != "m8_state_chain_mismatch"
        or checked.first_failing_fact_sha256 != mutated.fact_sha256
    ):
        raise M8Gate3MutationExecutionError(
            "action-root mutation did not produce its exact typed checker failure"
        )
    return _ActionRootCheckerFailure(
        observed_failure_code=(
            M8Gate3ObservedMutationFailureCode.CHECKED_ACTION_ROOT_BINDING_MISMATCH
        ),
        checker_failure_code=checked.failure_code,
        first_failing_fact_sha256=checked.first_failing_fact_sha256,
    )


def _target_content_sha256(target: _TypedArtifactTarget) -> str:
    return target.fact_sha256 if type(target) is M8ActionRootV2 else target.content_sha256


def _execute_gate3_mutation_worker(
    recipe: M8Gate3MutationRecipeBinding,
    target: _TypedArtifactTarget,
    context: _MutationBindingContext,
    retained_source: _RetainedRootSource | None,
    checker_context: M8Gate3MutationCheckerContext | None,
) -> _MutationWorkerResult:
    before = _portable_registry_state()
    if not before.is_clean:
        raise M8Gate3MutationExecutionError("mutation worker started with a process-local registry")
    mutated = mutate_gate3_target(recipe, target)
    observed = validate_gate3_mutation_cross_binding(recipe, target, mutated)
    if recipe.target_kind == "checked_action_root":
        if (
            type(target) is not M8ActionRootV2
            or type(mutated) is not M8ActionRootV2
            or type(retained_source) is not _RetainedRootSource
            or type(checker_context) is not M8Gate3MutationCheckerContext
        ):
            raise M8Gate3MutationExecutionError(
                "action-root mutation lacks its retained bundle/checker authority"
            )
        mutated_bundle_bytes = _mutated_action_root_bundle_bytes(
            retained_source,
            target,
            mutated,
        )
    elif retained_source is not None or checker_context is not None:
        raise M8Gate3MutationExecutionError(
            "typed artifact mutation received action-root checker authority"
        )
    telemetry = _install_mutation_capability_guard(
        allowed_executable=(
            checker_context.jagua_executable
            if type(checker_context) is M8Gate3MutationCheckerContext
            else None
        ),
        fork_limit=(
            checker_context.translation_audit_processes
            if type(checker_context) is M8Gate3MutationCheckerContext
            else 0
        ),
    )
    if recipe.target_kind == "checked_action_root":
        assert type(mutated) is M8ActionRootV2
        assert type(checker_context) is M8Gate3MutationCheckerContext
        checker_failure = _real_checker_action_root_failure(
            mutated,
            mutated_bundle_bytes,
            checker_context,
        )
        independently_observed = checker_failure.observed_failure_code
        checker_failure_code = checker_failure.checker_failure_code
        first_failing_fact_sha256 = checker_failure.first_failing_fact_sha256
    else:
        independently_observed = _independent_gate3_binding_failure(
            recipe,
            mutated,
            context,
        )
        checker_failure_code = None
        first_failing_fact_sha256 = None
    if independently_observed is not observed:
        raise M8Gate3MutationExecutionError(
            "mutation shape and independent binding consumers disagree"
        )
    after = _portable_registry_state()
    if not after.is_clean:
        raise M8Gate3MutationExecutionError("mutation worker leaked a process-local registry")
    if telemetry.artifact_published or telemetry.evaluation_accessed:
        raise M8Gate3MutationExecutionError(
            "mutation worker attempted a forbidden external capability"
        )
    rehash_performed = _target_content_sha256(mutated) != recipe.target_content_sha256
    return _MutationWorkerResult(
        recipe_id=recipe.recipe_id,
        observed_failure_code=observed,
        mutated_content_sha256=_target_content_sha256(mutated),
        checker_failure_code=checker_failure_code,
        first_failing_fact_sha256=first_failing_fact_sha256,
        rehash_performed=rehash_performed,
        mutation_rejected=observed.value == recipe.expected_failure_code,
        worker_fork_count=telemetry.fork_count,
        surviving_registry_count=0,
        artifact_published=telemetry.artifact_published,
        evaluation_accessed=telemetry.evaluation_accessed,
    )


def execute_gate3_mutation_manifest(
    manifest: M8Gate3MutationManifest,
    *,
    parent_v3: M8CertificateProofResult,
    portable_fact_gate3: M8PortableFactGate3Result,
    root_manifest: M8Gate3CheckedRootManifest,
    sample: M8Gate3AuditSample,
    retained_bundle_bytes: tuple[bytes, ...],
    checker_contexts: tuple[M8Gate3MutationCheckerContext, ...],
    process_count: int = 4,
    timeout_seconds: float = 120.0,
) -> M8Gate3MutationResult:
    """Execute every registered mutation once in a fresh supervised worker."""

    if type(process_count) is not int or not 1 <= process_count <= 8:
        raise M8Gate3MutationExecutionError("mutation process count is outside 1..8")
    if (
        type(timeout_seconds) is not float
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0.0
    ):
        raise M8Gate3MutationExecutionError("mutation worker timeout is invalid")
    registered, parent, gate3, roots, frozen = _strict_execution_inputs(
        manifest,
        parent_v3,
        portable_fact_gate3,
        root_manifest,
        sample,
    )
    _require_retained_cell_bindings(gate3, retained_bundle_bytes)
    if tuple(item.root_fact_sha256 for item in frozen.actions) != (
        registered.sample_action_root_sha256s
    ):
        raise M8Gate3MutationExecutionError("mutation sample root order differs from the manifest")
    retained_sources = _load_gate3_retained_root_sources(
        retained_bundle_bytes,
        registered.sample_action_root_sha256s,
    )
    checker_by_regime = _strict_checker_contexts(checker_contexts)
    if (
        process_count * max(item.translation_audit_processes for item in checker_by_regime.values())
        > M8_GATE3_CONCURRENCY_BUDGET.total_compute_slots
    ):
        raise M8Gate3MutationExecutionError(
            "mutation checker concurrency exceeds the frozen compute-slot budget"
        )
    for source, action in zip(retained_sources, frozen.actions, strict=True):
        _require_sampled_root_binding(source, action)
        checker = checker_by_regime[source.regime]
        checker_dimension = (
            checker.regime,
            checker.execution_cell.stream[0].temporal_seed,
            checker.execution_cell.stream[0].stream_id,
        )
        if checker_dimension != (
            source.regime,
            source.temporal_seed,
            source.stream_id,
        ):
            raise M8Gate3MutationExecutionError(
                "mutation checker authority differs from its retained bundle cell"
            )

    targets: dict[str, _TypedArtifactTarget] = {
        parent.content_sha256: parent,
        gate3.content_sha256: gate3,
        roots.content_sha256: roots,
        frozen.content_sha256: frozen,
        **{item.root.fact_sha256: item.root for item in retained_sources},
    }
    binding_context = _MutationBindingContext(
        parent_v3=parent,
        portable_fact_gate3=gate3,
        root_manifest=roots,
        sample=frozen,
    )
    retained_by_root = {item.root.fact_sha256: item for item in retained_sources}
    try:
        tasks = tuple(
            (
                recipe,
                targets[recipe.target_content_sha256],
                binding_context,
                retained_by_root.get(recipe.target_content_sha256),
                (
                    checker_by_regime[retained_by_root[recipe.target_content_sha256].regime]
                    if recipe.target_kind == "checked_action_root"
                    else None
                ),
            )
            for recipe in registered.recipes
        )
    except KeyError as error:
        raise M8Gate3MutationExecutionError(
            "mutation manifest target is absent from retained inputs"
        ) from error
    try:
        worker_results = _run_process_phase(
            _execute_gate3_mutation_worker,
            tasks,
            process_count=process_count,
            timeout_seconds=timeout_seconds,
        )
    except (RuntimeError, TimeoutError, TypeError, ValueError) as error:
        raise M8Gate3MutationExecutionError("supervised mutation worker failed") from error

    outcomes = []
    for recipe, worker in zip(registered.recipes, worker_results, strict=True):
        if type(worker) is not _MutationWorkerResult or worker.recipe_id != recipe.recipe_id:
            raise M8Gate3MutationExecutionError(
                "supervised mutation worker result differs from its recipe"
            )
        fork_limit = (
            checker_by_regime[
                retained_by_root[recipe.target_content_sha256].regime
            ].translation_audit_processes
            if recipe.target_kind == "checked_action_root"
            else 0
        )
        if worker.worker_fork_count > fork_limit:
            raise M8Gate3MutationExecutionError(
                "mutation worker exceeded its registered fork limit"
            )
        if recipe.target_kind == "checked_action_root":
            if (
                worker.checker_failure_code != "m8_state_chain_mismatch"
                or worker.first_failing_fact_sha256 != worker.mutated_content_sha256
            ):
                raise M8Gate3MutationExecutionError(
                    "action-root worker omitted its exact typed checker evidence"
                )
        elif (
            worker.checker_failure_code is not None
            or worker.first_failing_fact_sha256 is not None
            or worker.worker_fork_count != 0
        ):
            raise M8Gate3MutationExecutionError(
                "typed artifact worker emitted action-root checker evidence"
            )
        outcomes.append(
            M8Gate3MutationOutcome(
                recipe_id=recipe.recipe_id,
                recipe_sha256=recipe.recipe_sha256,
                target_content_sha256=recipe.target_content_sha256,
                mutated_content_sha256=worker.mutated_content_sha256,
                expected_failure_code=recipe.expected_failure_code,
                observed_failure_code=worker.observed_failure_code.value,
                rehash_required=recipe.rehash_required,
                rehash_performed=worker.rehash_performed,
                mutation_rejected=worker.mutation_rejected,
                checker_failure_code=worker.checker_failure_code,
                first_failing_fact_sha256=worker.first_failing_fact_sha256,
                worker_exit_code=0,
                worker_fork_limit=fork_limit,
                worker_fork_count=worker.worker_fork_count,
                surviving_descendant_count=0,
                surviving_registry_count=worker.surviving_registry_count,
                artifact_published=worker.artifact_published,
                evaluation_accessed=worker.evaluation_accessed,
            )
        )
    return finalize_gate3_mutation_execution(
        registered,
        harness_id=registered.authorized_harness_id,
        harness_content_sha256=registered.authorized_harness_content_sha256,
        runtime_id=registered.authorized_runtime_id,
        runtime_content_sha256=registered.authorized_runtime_content_sha256,
        outcomes=tuple(outcomes),
    )


def execute_gate3_mutations(
    parent_v3: M8CertificateProofResult,
    portable_fact_gate3: M8PortableFactGate3Result,
    root_manifest: M8Gate3CheckedRootManifest,
    sample: M8Gate3AuditSample,
    retained_bundle_bytes: tuple[bytes, ...],
    *,
    checker_contexts: tuple[M8Gate3MutationCheckerContext, ...],
    harness_id: str,
    harness_content_sha256: str,
    runtime_id: str,
    runtime_content_sha256: str,
    additional_base_content_sha256s: tuple[str, ...] = (),
    process_count: int = 4,
    timeout_seconds: float = 120.0,
) -> M8Gate3MutationResult:
    """Build and execute the exact 16-recipe mutation manifest."""

    try:
        manifest = build_gate3_mutation_manifest(
            parent_v3,
            portable_fact_gate3,
            root_manifest,
            sample,
            harness_id=harness_id,
            harness_content_sha256=harness_content_sha256,
            runtime_id=runtime_id,
            runtime_content_sha256=runtime_content_sha256,
            additional_base_content_sha256s=additional_base_content_sha256s,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise M8Gate3MutationExecutionError(
            "mutation evidence could not build the exact registered manifest"
        ) from error
    return execute_gate3_mutation_manifest(
        manifest,
        parent_v3=parent_v3,
        portable_fact_gate3=portable_fact_gate3,
        root_manifest=root_manifest,
        sample=sample,
        retained_bundle_bytes=retained_bundle_bytes,
        checker_contexts=checker_contexts,
        process_count=process_count,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "M8Gate3MutationCheckerContext",
    "M8Gate3MutationExecutionError",
    "M8Gate3ObservedMutationFailureCode",
    "execute_gate3_mutation_manifest",
    "execute_gate3_mutations",
    "load_gate3_retained_action_roots",
    "mutate_gate3_target",
    "validate_gate3_mutation_cross_binding",
    "validate_gate3_mutation_payload",
]
