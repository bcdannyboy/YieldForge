"""Resume and publish the calibration stage of M11 economic resolution."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from yieldforge.realistic_falsification.confirmation import Gate3CalibrationObservation
from yieldforge.realistic_falsification.economic_evidence_store import (
    Gate3CalibrationObservationReceipt,
    Gate3EconomicEvidenceError,
    load_gate3_calibration_observation_evidence,
    publish_gate3_calibration_observation_evidence,
)
from yieldforge.realistic_falsification.economic_resolution import (
    EconomicResolutionEvidenceError,
    EconomicResolutionProtocol,
    Gate3CalibrationAttemptCheckpoint,
    Gate3CalibrationManifest,
    Gate3LegacyCalibrationAttemptReference,
    Gate3LegacyCalibrationScan,
    build_economic_resolution_protocol,
    build_gate3_calibration_attempt_checkpoint,
    build_gate3_calibration_manifest,
    discover_gate3_calibration_attempt_checkpoint,
    discover_gate3_calibration_manifest,
    load_gate3_calibration_attempt_checkpoint,
    load_gate3_calibration_manifest,
    publish_gate3_calibration_attempt_checkpoint,
    publish_gate3_calibration_manifest,
    scan_official_legacy_gate3_calibration_artifact,
    verify_economic_resolution_runtime_lineage,
)
from yieldforge.realistic_falsification.gate3_backend_impl import (
    AdapterGate3Backend,
    build_adapter_gate3_backend,
)
from yieldforge.realistic_falsification.gate3_runner import (
    M11Gate3RunnerError,
    authenticate_official_gate3_early_inputs,
)

_CORPUS_ORDER = ("lectra-m3-m4", "loco-2dics")
_DEFAULT_GATE1 = "experiments/results/" "m11-gate1-yfm11g1run-c35f10fa4f4d7b6b01c59c29.json"
_DEFAULT_GATE2 = "experiments/results/" "m11-gate2-yfm11g2run-7419e46b74e411aff5c27ee1.json"
_DEFAULT_LEGACY_GATE3 = (
    "experiments/results/" "m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json"
)
_DEFAULT_GATE3_CONFIG = "benchmarks/falsification/m11-gate3-config-v1.json"
_DEFAULT_OUTPUT = "experiments/results/m11-economic-resolution"


class M11EconomicResolutionRunnerError(ValueError):
    """Calibration orchestration failed outside an admitted backend outcome."""


@dataclass(frozen=True, slots=True)
class EconomicResolutionCalibrationRun:
    """Authenticated terminal calibration state returned by the runner."""

    protocol: EconomicResolutionProtocol
    legacy_scan: Gate3LegacyCalibrationScan
    manifest: Gate3CalibrationManifest
    manifest_path: Path


def _require_scan_bindings(
    *,
    protocol: EconomicResolutionProtocol,
    roots: object,
    scan: Gate3LegacyCalibrationScan,
) -> None:
    expected_corpora = tuple(item[0] for item in scan.calibration_stream_census)
    if (
        scan.protocol != protocol
        or scan.roots != roots
        or scan.roots.content_sha256 != protocol.legacy_root_content_sha256
        or len(scan.attempt_references) != 96
        or (scan.success_count, scan.failure_count) != (60, 36)
        or expected_corpora != _CORPUS_ORDER
        or any(len(stream_ids) != 8 for _corpus, stream_ids in scan.calibration_stream_census)
    ):
        raise M11EconomicResolutionRunnerError(
            "legacy calibration scan differs from authenticated protocol, roots, or census"
        )


def _require_backend_registry(
    backend: AdapterGate3Backend,
    scan: Gate3LegacyCalibrationScan,
) -> None:
    for corpus_id, expected_stream_ids in scan.calibration_stream_census:
        actual = backend.calibration_stream_ids(corpus_id)
        if type(actual) is not tuple or actual != expected_stream_ids:
            raise M11EconomicResolutionRunnerError(
                "calibration backend registry differs from the authenticated legacy scan"
            )


def _require_observation_matches_receipt(
    observation: Gate3CalibrationObservation,
    receipt: Gate3CalibrationObservationReceipt,
) -> None:
    if (
        observation.roots,
        observation.corpus_id,
        observation.stream_id,
        observation.policy_id,
        observation.observation_id,
        observation.content_sha256,
        observation.final_costs,
        observation.full_sheet_opening_count,
        observation.exact_event_census,
    ) != (
        receipt.roots,
        receipt.corpus_id,
        receipt.stream_id,
        receipt.policy_id,
        receipt.observation_id,
        receipt.observation_content_sha256,
        receipt.final_costs,
        receipt.full_sheet_opening_count,
        receipt.exact_event_census,
    ):
        raise M11EconomicResolutionRunnerError(
            "calibration observation read-back differs from its compact receipt"
        )


def _load_repaired_sidecar(
    output_directory: Path,
    receipt: Gate3CalibrationObservationReceipt,
) -> None:
    observation = load_gate3_calibration_observation_evidence(
        Path(output_directory) / receipt.sidecar_name,
        receipt=receipt,
        expected_roots=receipt.roots,
        expected_corpus_id=receipt.corpus_id,
        expected_stream_id=receipt.stream_id,
        expected_policy_id=receipt.policy_id,
        expected_observation_id=receipt.observation_id,
        expected_observation_content_sha256=receipt.observation_content_sha256,
        expected_final_costs=receipt.final_costs,
        expected_full_sheet_opening_count=receipt.full_sheet_opening_count,
        expected_source_lineage="repaired_runtime",
    )
    _require_observation_matches_receipt(observation, receipt)


def _load_checkpoint(
    path: Path,
    checkpoint: Gate3CalibrationAttemptCheckpoint,
) -> Gate3CalibrationAttemptCheckpoint:
    loaded = load_gate3_calibration_attempt_checkpoint(
        path,
        expected_protocol=checkpoint.protocol,
        expected_roots=checkpoint.roots,
        expected_execution_position=checkpoint.execution_position,
        expected_corpus_id=checkpoint.corpus_id,
        expected_stream_id=checkpoint.stream_id,
        expected_policy_id=checkpoint.policy_id,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        expected_content_sha256=checkpoint.content_sha256,
    )
    if loaded != checkpoint:
        raise M11EconomicResolutionRunnerError(
            "calibration checkpoint read-back differs from the published checkpoint"
        )
    return loaded


def _publish_and_load_checkpoint(
    output_directory: Path,
    checkpoint: Gate3CalibrationAttemptCheckpoint,
) -> Gate3CalibrationAttemptCheckpoint:
    path = publish_gate3_calibration_attempt_checkpoint(output_directory, checkpoint)
    return _load_checkpoint(path, checkpoint)


def _failure_fields(error: Exception) -> tuple[str, str]:
    failure_type = f"{type(error).__module__}.{type(error).__qualname__}"[:240]
    failure_detail = (str(error).strip() or "exception carried no detail")[:1000]
    return failure_type, failure_detail


def _discard_incomplete_best_effort(
    backend: AdapterGate3Backend,
    reference: Gate3LegacyCalibrationAttemptReference,
    primary_error: BaseException,
) -> None:
    try:
        backend.discard_incomplete_calibration_stream_evidence(
            corpus_id=reference.corpus_id,
            stream_id=reference.stream_id,
            policy_id=reference.policy_id,
        )
    except BaseException as cleanup_error:
        try:
            primary_error.add_note(
                "incomplete calibration cache cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        except Exception:
            pass


def _discard_incomplete_required(
    backend: AdapterGate3Backend,
    reference: Gate3LegacyCalibrationAttemptReference,
) -> None:
    backend.discard_incomplete_calibration_stream_evidence(
        corpus_id=reference.corpus_id,
        stream_id=reference.stream_id,
        policy_id=reference.policy_id,
    )


def _execute_missing_failure(
    *,
    backend: AdapterGate3Backend,
    output_directory: Path,
    protocol: EconomicResolutionProtocol,
    reference: Gate3LegacyCalibrationAttemptReference,
) -> Gate3CalibrationAttemptCheckpoint:
    common = {
        "protocol": protocol,
        "roots": reference.roots,
        "execution_position": reference.execution_position,
        "corpus_id": reference.corpus_id,
        "stream_id": reference.stream_id,
        "policy_id": reference.policy_id,
        "replaced_legacy_failure_reference": reference,
    }
    try:
        observation = backend.execute_calibration_stream(
            corpus_id=reference.corpus_id,
            stream_id=reference.stream_id,
            policy_id=reference.policy_id,
        )
    except BaseException as error:
        infrastructure = isinstance(
            error,
            OSError
            | MemoryError
            | TimeoutError
            | KeyboardInterrupt
            | SystemExit
            | EconomicResolutionEvidenceError
            | Gate3EconomicEvidenceError
            | M11Gate3RunnerError
            | M11EconomicResolutionRunnerError,
        )
        if infrastructure or not isinstance(error, Exception):
            _discard_incomplete_best_effort(backend, reference, error)
            raise
        _discard_incomplete_required(backend, reference)
        failure_type, failure_detail = _failure_fields(error)
        checkpoint = build_gate3_calibration_attempt_checkpoint(
            **common,
            failure_type=failure_type,
            failure_detail=failure_detail,
        )
        return _publish_and_load_checkpoint(output_directory, checkpoint)

    sidecar_path, receipt = publish_gate3_calibration_observation_evidence(
        output_directory,
        observation,
        source_lineage="repaired_runtime",
    )
    expected_sidecar = Path(output_directory) / receipt.sidecar_name
    if sidecar_path != expected_sidecar:
        raise M11EconomicResolutionRunnerError(
            "calibration sidecar publication path differs from its receipt"
        )
    backend.release_calibration_stream_evidence(
        corpus_id=reference.corpus_id,
        stream_id=reference.stream_id,
        policy_id=reference.policy_id,
        expected_observation_id=receipt.observation_id,
        expected_observation_content_sha256=receipt.observation_content_sha256,
    )
    _load_repaired_sidecar(output_directory, receipt)
    checkpoint = build_gate3_calibration_attempt_checkpoint(
        **common,
        repaired_receipt=receipt,
    )
    return _publish_and_load_checkpoint(output_directory, checkpoint)


def _publish_or_reuse_manifest(
    *,
    output_directory: Path,
    protocol: EconomicResolutionProtocol,
    legacy_scan: Gate3LegacyCalibrationScan,
    checkpoints: tuple[Gate3CalibrationAttemptCheckpoint, ...],
) -> tuple[Path, Gate3CalibrationManifest]:
    derived = build_gate3_calibration_manifest(checkpoints, legacy_scan=legacy_scan)
    existing = discover_gate3_calibration_manifest(
        output_directory,
        protocol=protocol,
        legacy_scan=legacy_scan,
        checkpoints=checkpoints,
    )
    if existing is not None:
        path, manifest = existing
        if manifest != derived:
            raise M11EconomicResolutionRunnerError(
                "existing calibration manifest differs from complete derived state"
            )
        return path, manifest
    path = publish_gate3_calibration_manifest(output_directory, derived)
    loaded = load_gate3_calibration_manifest(
        path,
        expected_protocol=protocol,
        expected_roots=legacy_scan.roots,
        expected_manifest_id=derived.manifest_id,
        expected_content_sha256=derived.content_sha256,
    )
    if loaded != derived:
        raise M11EconomicResolutionRunnerError(
            "calibration manifest read-back differs from complete derived state"
        )
    return path, loaded


def run_economic_resolution_calibration(
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    gate2_artifact_path: Path,
    gate3_config_path: Path,
    legacy_gate3_artifact_path: Path,
    output_directory: Path,
) -> EconomicResolutionCalibrationRun:
    """Authenticate once, resume safely, and complete only calibration evidence."""

    root = Path(repository_root).resolve()
    output = Path(output_directory)
    protocol = build_economic_resolution_protocol()
    verify_economic_resolution_runtime_lineage(root, protocol)
    authenticated = authenticate_official_gate3_early_inputs(
        repository_root=root,
        gate1_artifact_path=Path(gate1_artifact_path),
        gate2_artifact_path=Path(gate2_artifact_path),
        gate3_config_path=Path(gate3_config_path),
    )
    scan = scan_official_legacy_gate3_calibration_artifact(Path(legacy_gate3_artifact_path))
    _require_scan_bindings(
        protocol=protocol,
        roots=authenticated.roots,
        scan=scan,
    )
    backend = build_adapter_gate3_backend(
        repository_root=root,
        gate1_artifact=authenticated.gate1_artifact,
        gate2_artifact=authenticated.gate2_artifact,
        gate3_config=authenticated.gate3_config,
        roots=authenticated.roots,
    )
    _require_backend_registry(backend, scan)

    discovered = tuple(
        discover_gate3_calibration_attempt_checkpoint(
            output,
            protocol=protocol,
            legacy_reference=reference,
        )
        for reference in scan.attempt_references
    )
    for item in discovered:
        if item is None:
            continue
        _path, checkpoint = item
        if checkpoint.outcome_kind == "repaired_runtime_success":
            receipt = checkpoint.repaired_receipt
            if receipt is None:
                raise M11EconomicResolutionRunnerError(
                    "resumed repaired checkpoint omitted its sidecar receipt"
                )
            _load_repaired_sidecar(output, receipt)

    checkpoints: list[Gate3CalibrationAttemptCheckpoint] = []
    for reference, existing in zip(scan.attempt_references, discovered, strict=True):
        if existing is not None:
            checkpoints.append(existing[1])
            continue
        if reference.status == "success":
            checkpoint = build_gate3_calibration_attempt_checkpoint(
                protocol=protocol,
                roots=reference.roots,
                execution_position=reference.execution_position,
                corpus_id=reference.corpus_id,
                stream_id=reference.stream_id,
                policy_id=reference.policy_id,
                legacy_reference=reference,
            )
            checkpoints.append(_publish_and_load_checkpoint(output, checkpoint))
            continue
        checkpoints.append(
            _execute_missing_failure(
                backend=backend,
                output_directory=output,
                protocol=protocol,
                reference=reference,
            )
        )

    complete = tuple(checkpoints)
    manifest_path, manifest = _publish_or_reuse_manifest(
        output_directory=output,
        protocol=protocol,
        legacy_scan=scan,
        checkpoints=complete,
    )
    if (
        manifest.checkpoints != complete
        or manifest.protocol != protocol
        or manifest.roots != authenticated.roots
        or manifest.status not in ("complete_valid", "complete_invalid")
        or (manifest.status == "complete_valid") != (manifest.failure_count == 0)
        or (len(manifest.baseline_freezes) == 2) != (manifest.status == "complete_valid")
    ):
        raise M11EconomicResolutionRunnerError(
            "terminal calibration manifest violates completion invariants"
        )
    return EconomicResolutionCalibrationRun(
        protocol=protocol,
        legacy_scan=scan,
        manifest=manifest,
        manifest_path=manifest_path,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=project_root)
    parser.add_argument("--gate1-artifact", type=Path, default=Path(_DEFAULT_GATE1))
    parser.add_argument("--gate2-artifact", type=Path, default=Path(_DEFAULT_GATE2))
    parser.add_argument("--gate3-config", type=Path, default=Path(_DEFAULT_GATE3_CONFIG))
    parser.add_argument(
        "--legacy-gate3-artifact",
        type=Path,
        default=Path(_DEFAULT_LEGACY_GATE3),
    )
    parser.add_argument("--output-directory", type=Path, default=Path(_DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def _under_root(root: Path, candidate: Path) -> Path:
    return candidate if candidate.is_absolute() else root / candidate


def _compact_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.repository_root).resolve()
    try:
        outcome = run_economic_resolution_calibration(
            repository_root=root,
            gate1_artifact_path=_under_root(root, args.gate1_artifact),
            gate2_artifact_path=_under_root(root, args.gate2_artifact),
            gate3_config_path=_under_root(root, args.gate3_config),
            legacy_gate3_artifact_path=_under_root(root, args.legacy_gate3_artifact),
            output_directory=_under_root(root, args.output_directory),
        )
    except Exception as error:
        print(
            _compact_json(
                {
                    "error_detail": (str(error).strip() or "exception carried no detail")[:1000],
                    "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
                    "status": "infrastructure_error",
                }
            ),
            file=sys.stderr,
        )
        return 1
    manifest = outcome.manifest
    print(
        _compact_json(
            {
                "baseline_freeze_ids": [item.freeze_id for item in manifest.baseline_freezes],
                "failure_count": manifest.failure_count,
                "manifest_content_sha256": manifest.content_sha256,
                "manifest_id": manifest.manifest_id,
                "protocol_content_sha256": outcome.protocol.content_sha256,
                "protocol_id": outcome.protocol.protocol_id,
                "status": manifest.status,
                "success_count": manifest.success_count,
            }
        )
    )
    return 0 if manifest.status == "complete_valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
