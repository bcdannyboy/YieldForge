"""Run or resume the M11 economic-validity stage without central analysis."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from yieldforge.realistic_falsification.confirmation import (
    Gate3BaselineCalibrationFreeze,
    Gate3ValidityReceipt,
)
from yieldforge.realistic_falsification.economic_resolution import (
    EconomicResolutionProtocol,
    Gate3CalibrationManifest,
    build_economic_resolution_protocol,
    load_gate3_calibration_manifest,
    verify_economic_resolution_runtime_lineage,
)
from yieldforge.realistic_falsification.economic_validity import (
    Gate3ValidityStageManifest,
    build_gate3_validity_stage_manifest,
    discover_gate3_validity_stage_manifest,
    load_gate3_validity_stage_manifest,
    publish_gate3_validity_stage_manifest,
    require_gate3_validity_sidecar_census,
)
from yieldforge.realistic_falsification.gate3_backend_impl import (
    AdapterGate3Backend,
    build_adapter_gate3_backend,
)
from yieldforge.realistic_falsification.gate3_runner import (
    authenticate_official_gate3_early_inputs,
)
from yieldforge.realistic_falsification.validity_evidence_store import (
    Gate3ValidityEvidenceReceipt,
    load_gate3_validity_evidence,
    publish_gate3_validity_evidence,
)

_DEFAULT_GATE1 = "experiments/results/m11-gate1-yfm11g1run-c35f10fa4f4d7b6b01c59c29.json"
_DEFAULT_GATE2 = "experiments/results/m11-gate2-yfm11g2run-7419e46b74e411aff5c27ee1.json"
_DEFAULT_GATE3_CONFIG = "benchmarks/falsification/m11-gate3-config-v1.json"
_DEFAULT_OUTPUT = "experiments/results/m11-economic-resolution"
_CALIBRATION_PREFIX = "m11-economic-calibration-manifest-"
_MAX_CALIBRATION_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_DISCOVERY_ENTRIES = 4096


class M11EconomicValidityRunnerError(ValueError):
    """Validity orchestration failed outside a trusted scientific result."""


@dataclass(frozen=True, slots=True)
class EconomicValidityStageRun:
    protocol: EconomicResolutionProtocol
    calibration_manifest: Gate3CalibrationManifest
    manifest: Gate3ValidityStageManifest
    manifest_path: Path


def _calibration_binding_from_path(path: Path) -> tuple[str, str]:
    match = re.fullmatch(
        rf"{re.escape(_CALIBRATION_PREFIX)}([0-9a-f]{{64}})\.json",
        Path(path).name,
    )
    if match is None:
        raise M11EconomicValidityRunnerError(
            "calibration manifest filename does not carry an exact content binding"
        )
    digest = match.group(1)
    return f"yfm11econcalman-{digest[:24]}", f"sha256:{digest}"


def _require_complete_calibration(
    calibration: Gate3CalibrationManifest,
    *,
    protocol: EconomicResolutionProtocol,
    roots: object,
) -> tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze]:
    if (
        calibration.protocol != protocol
        or calibration.roots != roots
        or calibration.status != "complete_valid"
        or calibration.success_count != 96
        or calibration.failure_count != 0
        or len(calibration.checkpoints) != 96
        or len(calibration.baseline_freezes) != 2
        or tuple(item.corpus_id for item in calibration.baseline_freezes)
        != ("lectra-m3-m4", "loco-2dics")
        or any(item.roots != roots for item in calibration.baseline_freezes)
        or calibration.complete is not True
    ):
        raise M11EconomicValidityRunnerError(
            "validity stage requires the exact complete-valid 96/0 calibration manifest"
        )
    return cast(
        tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze],
        calibration.baseline_freezes,
    )


def _strict_validity_receipt(receipt: Gate3ValidityReceipt) -> Gate3ValidityReceipt:
    try:
        return Gate3ValidityReceipt.model_validate(
            receipt.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise M11EconomicValidityRunnerError(
            "executed validity receipt failed strict validation"
        ) from error


def _require_receipt_matches_evidence(
    receipt: Gate3ValidityReceipt,
    evidence: Gate3ValidityEvidenceReceipt,
) -> None:
    if (
        receipt.roots,
        receipt.receipt_id,
        receipt.content_sha256,
        receipt.failure_codes,
        receipt.diagnosis_codes,
        receipt.status,
        receipt.exact_control_census,
        receipt.raw_controls_revalidated,
    ) != (
        evidence.roots,
        evidence.validity_receipt_id,
        evidence.validity_receipt_content_sha256,
        evidence.failure_codes,
        evidence.diagnosis_codes,
        evidence.status,
        True,
        True,
    ):
        raise M11EconomicValidityRunnerError(
            "fresh validity sidecar differs from its compact evidence receipt"
        )


def _load_validity_sidecar(
    output_directory: Path,
    evidence: Gate3ValidityEvidenceReceipt,
    *,
    roots: object,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> Gate3ValidityReceipt:
    if evidence.roots != roots:
        raise M11EconomicValidityRunnerError(
            "validity evidence roots differ from authenticated calibration"
        )
    receipt = load_gate3_validity_evidence(
        Path(output_directory) / evidence.sidecar_name,
        evidence_receipt=evidence,
        expected_roots=evidence.roots,
        expected_baseline_freezes=baseline_freezes,
        expected_validity_receipt_id=evidence.validity_receipt_id,
        expected_validity_receipt_content_sha256=(evidence.validity_receipt_content_sha256),
        expected_hard_nulls=evidence.hard_nulls,
        expected_twin_controls=evidence.twin_controls,
        expected_exact_audits=evidence.exact_audits,
        expected_no_signal_summaries=evidence.no_signal_summaries,
        expected_failure_codes=evidence.failure_codes,
        expected_diagnosis_codes=evidence.diagnosis_codes,
        expected_status=evidence.status,
        expected_exact_control_census=True,
        expected_raw_controls_revalidated=True,
        expected_source_lineage="repaired_runtime",
    )
    _require_receipt_matches_evidence(receipt, evidence)
    return receipt


def run_economic_validity_stage(
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    gate2_artifact_path: Path,
    gate3_config_path: Path,
    calibration_manifest_path: Path,
    output_directory: Path,
) -> EconomicValidityStageRun:
    """Authenticate once, resume safely, and execute validity at most once."""

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
    calibration_id, calibration_sha = _calibration_binding_from_path(calibration_manifest_path)
    calibration = load_gate3_calibration_manifest(
        Path(calibration_manifest_path),
        expected_protocol=protocol,
        expected_roots=authenticated.roots,
        expected_manifest_id=calibration_id,
        expected_content_sha256=calibration_sha,
    )
    freezes = _require_complete_calibration(
        calibration,
        protocol=protocol,
        roots=authenticated.roots,
    )
    backend = cast(
        AdapterGate3Backend,
        build_adapter_gate3_backend(
            repository_root=root,
            gate1_artifact=authenticated.gate1_artifact,
            gate2_artifact=authenticated.gate2_artifact,
            gate3_config=authenticated.gate3_config,
            roots=authenticated.roots,
        ),
    )
    existing = discover_gate3_validity_stage_manifest(
        output,
        protocol=protocol,
        roots=authenticated.roots,
        calibration_manifest=calibration,
    )
    if existing is not None:
        manifest_path, manifest = existing
        require_gate3_validity_sidecar_census(
            output,
            expected_evidence=manifest.validity_evidence,
        )
        loaded = _load_validity_sidecar(
            output,
            manifest.validity_evidence,
            roots=authenticated.roots,
            baseline_freezes=freezes,
        )
        del loaded
        return EconomicValidityStageRun(
            protocol=protocol,
            calibration_manifest=calibration,
            manifest=manifest,
            manifest_path=manifest_path,
        )

    require_gate3_validity_sidecar_census(
        output,
        expected_evidence=None,
    )

    executed = backend.execute_validity_controls(
        roots=authenticated.roots,
        baseline_freezes=freezes,
    )
    receipt = _strict_validity_receipt(executed)
    del executed
    if receipt.roots != authenticated.roots or receipt.status not in (
        "valid",
        "diagnosis_required",
        "invalid",
    ):
        raise M11EconomicValidityRunnerError(
            "executed validity receipt differs from authenticated roots or status"
        )
    sidecar_path, evidence = publish_gate3_validity_evidence(
        output,
        receipt,
        baseline_freezes=freezes,
    )
    if sidecar_path != output / evidence.sidecar_name:
        raise M11EconomicValidityRunnerError(
            "validity sidecar publication path differs from its compact receipt"
        )
    backend.release_validity_controls_evidence(
        roots=authenticated.roots,
        baseline_freezes=freezes,
        expected_receipt_id=evidence.validity_receipt_id,
        expected_receipt_content_sha256=evidence.validity_receipt_content_sha256,
    )
    del receipt
    require_gate3_validity_sidecar_census(
        output,
        expected_evidence=evidence,
    )
    loaded = _load_validity_sidecar(
        output,
        evidence,
        roots=authenticated.roots,
        baseline_freezes=freezes,
    )
    del loaded
    derived = build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=evidence,
    )
    late = discover_gate3_validity_stage_manifest(
        output,
        protocol=protocol,
        roots=authenticated.roots,
        calibration_manifest=calibration,
    )
    if late is not None:
        raise M11EconomicValidityRunnerError(
            "validity stage found a late competing manifest before publication"
        )
    manifest_path = publish_gate3_validity_stage_manifest(
        output,
        derived,
        calibration_manifest=calibration,
    )
    manifest = load_gate3_validity_stage_manifest(
        manifest_path,
        expected_protocol=protocol,
        expected_roots=authenticated.roots,
        expected_calibration_manifest=calibration,
        expected_manifest_id=derived.manifest_id,
        expected_content_sha256=derived.content_sha256,
    )
    if manifest != derived:
        raise M11EconomicValidityRunnerError(
            "validity-stage manifest read-back differs from derived state"
        )
    discovered = discover_gate3_validity_stage_manifest(
        output,
        protocol=protocol,
        roots=authenticated.roots,
        calibration_manifest=calibration,
    )
    if discovered != (manifest_path, manifest):
        raise M11EconomicValidityRunnerError(
            "terminal validity-stage discovery differs from published state"
        )
    require_gate3_validity_sidecar_census(
        output,
        expected_evidence=manifest.validity_evidence,
    )
    terminal = _load_validity_sidecar(
        output,
        manifest.validity_evidence,
        roots=authenticated.roots,
        baseline_freezes=freezes,
    )
    del terminal
    return EconomicValidityStageRun(
        protocol=protocol,
        calibration_manifest=calibration,
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
    parser.add_argument("--calibration-manifest", type=Path)
    parser.add_argument("--output-directory", type=Path, default=Path(_DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def _under_root(root: Path, candidate: Path) -> Path:
    return candidate if candidate.is_absolute() else root / candidate


def _discover_required_calibration_manifest(output_directory: Path) -> Path:
    directory = Path(output_directory)
    try:
        metadata = directory.lstat()
    except OSError as error:
        raise M11EconomicValidityRunnerError(
            "calibration manifest must be supplied or unambiguously discovered"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise M11EconomicValidityRunnerError(
            "calibration discovery directory must be a non-symlink directory"
        )
    candidates: list[Path] = []
    descriptor: int | None = None
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
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise M11EconomicValidityRunnerError(
                "calibration discovery directory changed during open"
            )
        with os.scandir(descriptor) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > _MAX_DISCOVERY_ENTRIES:
                    raise M11EconomicValidityRunnerError(
                        "calibration discovery exceeded the directory entry bound"
                    )
                if not entry.name.startswith(_CALIBRATION_PREFIX):
                    continue
                if (
                    re.fullmatch(
                        rf"{re.escape(_CALIBRATION_PREFIX)}[0-9a-f]{{64}}\.json",
                        entry.name,
                    )
                    is None
                ):
                    raise M11EconomicValidityRunnerError(
                        "calibration discovery found a malformed prefixed entry"
                    )
                entry_metadata = entry.stat(follow_symlinks=False)
                if (
                    stat.S_ISLNK(entry_metadata.st_mode)
                    or not stat.S_ISREG(entry_metadata.st_mode)
                    or entry_metadata.st_size > _MAX_CALIBRATION_MANIFEST_BYTES
                ):
                    raise M11EconomicValidityRunnerError(
                        "calibration candidate must be a bounded regular non-symlink file"
                    )
                candidates.append(directory / entry.name)
        during = os.fstat(descriptor)
    except M11EconomicValidityRunnerError:
        raise
    except OSError as error:
        raise M11EconomicValidityRunnerError("calibration discovery failed safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = directory.lstat()
    except OSError as error:
        raise M11EconomicValidityRunnerError(
            "calibration discovery directory could not be re-inspected"
        ) from error
    fingerprint = lambda item: (  # noqa: E731
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if fingerprint(metadata) != fingerprint(during) or fingerprint(metadata) != fingerprint(after):
        raise M11EconomicValidityRunnerError("calibration discovery directory changed during scan")
    if len(candidates) != 1:
        raise M11EconomicValidityRunnerError(
            "calibration manifest must be supplied or unambiguously discovered"
        )
    return candidates[0]


def _compact_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.repository_root).resolve()
    output = _under_root(root, args.output_directory)
    try:
        calibration_path = (
            _under_root(root, args.calibration_manifest)
            if args.calibration_manifest is not None
            else _discover_required_calibration_manifest(output)
        )
        outcome = run_economic_validity_stage(
            repository_root=root,
            gate1_artifact_path=_under_root(root, args.gate1_artifact),
            gate2_artifact_path=_under_root(root, args.gate2_artifact),
            gate3_config_path=_under_root(root, args.gate3_config),
            calibration_manifest_path=calibration_path,
            output_directory=output,
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
    evidence = manifest.validity_evidence
    print(
        _compact_json(
            {
                "central_authorized": manifest.central_authorized,
                "manifest_content_sha256": manifest.content_sha256,
                "manifest_id": manifest.manifest_id,
                "manifest_path": str(outcome.manifest_path),
                "status": manifest.status,
                "validity_receipt_content_sha256": (evidence.validity_receipt_content_sha256),
                "validity_receipt_id": evidence.validity_receipt_id,
            }
        )
    )
    return 0 if manifest.status == "valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
