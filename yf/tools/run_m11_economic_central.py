"""Run or resume the M11 central economic screen."""

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
from typing import Literal, cast

from yieldforge.realistic_falsification.central_evidence_store import (
    Gate3CentralCellReceipt,
    Gate3CentralEvidenceError,
    load_gate3_central_cell_evidence,
    publish_gate3_central_cell_evidence,
    recover_gate3_central_cell_receipt,
)
from yieldforge.realistic_falsification.confirmation import (
    Gate3BaselineCalibrationFreeze,
    Gate3CorpusId,
    Gate3StreamCell,
)
from yieldforge.realistic_falsification.economic_central import (
    Gate3CentralCellCheckpoint,
    Gate3EconomicCentralManifest,
    Gate3EconomicSegmentSummary,
    build_gate3_central_cell_checkpoint,
    build_gate3_economic_central_manifest,
    build_gate3_economic_segment_summary,
    discover_gate3_central_cell_checkpoints,
    discover_gate3_economic_central_manifest,
    discover_gate3_economic_segment_summaries,
    load_gate3_central_cell_checkpoint,
    publish_gate3_central_cell_checkpoint,
    publish_gate3_economic_central_manifest,
    publish_gate3_economic_segment_summary,
)
from yieldforge.realistic_falsification.economic_decision import (
    EconomicDecisionAddendum,
    build_economic_decision_addendum,
)
from yieldforge.realistic_falsification.economic_resolution import (
    EconomicResolutionProtocol,
    Gate3CalibrationManifest,
    build_economic_resolution_protocol,
    discover_gate3_calibration_manifest,
    load_gate3_calibration_manifest,
    verify_economic_resolution_runtime_lineage,
)
from yieldforge.realistic_falsification.economic_validity import (
    Gate3ValidityStageManifest,
    discover_gate3_validity_stage_manifest,
    load_gate3_validity_stage_manifest,
)
from yieldforge.realistic_falsification.gate3_backend_impl import (
    AdapterGate3Backend,
    build_adapter_gate3_backend,
)
from yieldforge.realistic_falsification.gate3_runner import (
    authenticate_official_gate3_early_inputs,
)

_DEFAULT_GATE1 = "experiments/results/m11-gate1-yfm11g1run-c35f10fa4f4d7b6b01c59c29.json"
_DEFAULT_GATE2 = "experiments/results/m11-gate2-yfm11g2run-7419e46b74e411aff5c27ee1.json"
_DEFAULT_GATE3_CONFIG = "benchmarks/falsification/m11-gate3-config-v1.json"
_DEFAULT_OUTPUT = "experiments/results/m11-economic-resolution"
_CALIBRATION_PREFIX = "m11-economic-calibration-manifest-"
_VALIDITY_PREFIX = "m11-economic-validity-stage-"
_CENTRAL_SIDECAR_PREFIX = "m11-gate3-central-cell-"
_CENTRAL_SIDECAR_PATTERN = re.compile(r"m11-gate3-central-cell-[0-9a-f]{64}-[0-9a-f]{64}\.json\.gz")
_MAX_DISCOVERY_ENTRIES = 4096
_MAX_CENTRAL_SIDECAR_BYTES = 64 * 1024 * 1024
_REGIMES: tuple[Literal["recurrent", "mixed", "high_mix", "regime_shift"], ...] = (
    "recurrent",
    "mixed",
    "high_mix",
    "regime_shift",
)


class M11EconomicCentralRunnerError(ValueError):
    """Central orchestration failed outside a trusted economic result."""


@dataclass(frozen=True, slots=True)
class EconomicCentralStageRun:
    protocol: EconomicResolutionProtocol
    decision_addendum: EconomicDecisionAddendum
    calibration_manifest: Gate3CalibrationManifest
    validity_manifest: Gate3ValidityStageManifest
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...]
    segment_summaries: tuple[Gate3EconomicSegmentSummary, ...]
    manifest: Gate3EconomicCentralManifest
    manifest_path: Path


def _artifact_binding(path: Path, *, prefix: str, identity_prefix: str) -> tuple[str, str]:
    match = re.fullmatch(rf"{re.escape(prefix)}([0-9a-f]{{64}})\.json", Path(path).name)
    if match is None:
        raise M11EconomicCentralRunnerError(
            f"{identity_prefix} filename does not carry an exact content binding"
        )
    digest = match.group(1)
    identity = {
        "calibration": "yfm11econcalman-",
        "validity": "yfm11econvalman-",
    }[identity_prefix]
    return identity + digest[:24], f"sha256:{digest}"


def _require_validity_authorization(
    *,
    calibration: Gate3CalibrationManifest,
    validity: Gate3ValidityStageManifest,
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
        or not calibration.complete
    ):
        raise M11EconomicCentralRunnerError(
            "central stage requires exact complete-valid calibration"
        )
    if (
        validity.protocol != protocol
        or validity.roots != roots
        or validity.calibration_manifest_id != calibration.manifest_id
        or validity.calibration_manifest_content_sha256 != calibration.content_sha256
        or validity.baseline_freezes != calibration.baseline_freezes
        or validity.status != "valid"
        or validity.central_authorized is not True
        or not validity.complete
    ):
        raise M11EconomicCentralRunnerError(
            "central stage remains unresolved because validity is not exactly valid"
        )
    return cast(
        tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze],
        calibration.baseline_freezes,
    )


def _discover_central_sidecars(output_directory: Path) -> tuple[Path, ...]:
    directory = Path(output_directory)
    try:
        before = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise M11EconomicCentralRunnerError(
            "central sidecar directory failed inspection"
        ) from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise M11EconomicCentralRunnerError(
            "central sidecar discovery requires a non-symlink directory"
        )
    paths: list[Path] = []
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > _MAX_DISCOVERY_ENTRIES:
                    raise M11EconomicCentralRunnerError(
                        "central sidecar discovery exceeded its entry bound"
                    )
                if not entry.name.startswith(_CENTRAL_SIDECAR_PREFIX):
                    continue
                if _CENTRAL_SIDECAR_PATTERN.fullmatch(entry.name) is None:
                    raise M11EconomicCentralRunnerError(
                        "central sidecar discovery found a malformed prefixed entry"
                    )
                metadata = entry.stat(follow_symlinks=False)
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size > _MAX_CENTRAL_SIDECAR_BYTES
                ):
                    raise M11EconomicCentralRunnerError(
                        "central sidecar must be a bounded regular file"
                    )
                paths.append(directory / entry.name)
    except M11EconomicCentralRunnerError:
        raise
    except OSError as error:
        raise M11EconomicCentralRunnerError("central sidecar discovery failed safely") from error
    try:
        after = directory.lstat()
    except OSError as error:
        raise M11EconomicCentralRunnerError(
            "central sidecar directory changed after scan"
        ) from error
    fingerprint = lambda item: (  # noqa: E731
        item.st_dev,
        item.st_ino,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if fingerprint(before) != fingerprint(after):
        raise M11EconomicCentralRunnerError("central sidecar directory changed during scan")
    return tuple(sorted(paths))


def _sidecar_census(
    output: Path,
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...],
    *,
    allow_one_orphan: bool,
) -> tuple[
    dict[str, Path],
    tuple[Path, ...],
    dict[str, tuple[int, int, int, int, int]],
]:
    paths = _discover_central_sidecars(output)
    by_name = {item.name: item for item in paths}
    if len(by_name) != len(paths):
        raise M11EconomicCentralRunnerError("central sidecar census repeats a filename")
    expected = {item.receipt.sidecar_name for item in checkpoints}
    missing = expected - set(by_name)
    unbound = tuple(by_name[name] for name in sorted(set(by_name) - expected))
    if missing:
        raise M11EconomicCentralRunnerError(
            "central sidecar census is missing checkpoint-bound evidence"
        )
    if len(unbound) > (1 if allow_one_orphan else 0):
        raise M11EconomicCentralRunnerError(
            "central sidecar census has competing or unbound evidence"
        )
    fingerprints: dict[str, tuple[int, int, int, int, int]] = {}
    for name, path in by_name.items():
        try:
            metadata = path.lstat()
        except OSError as error:
            raise M11EconomicCentralRunnerError("central sidecar changed during census") from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_CENTRAL_SIDECAR_BYTES
        ):
            raise M11EconomicCentralRunnerError("central sidecar changed during census")
        fingerprints[name] = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
    return by_name, unbound, fingerprints


def _freeze_map(
    freezes: tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze],
) -> dict[Gate3CorpusId, Gate3BaselineCalibrationFreeze]:
    return {item.corpus_id: item for item in freezes}


def _load_cell_sidecar(
    output: Path,
    receipt: Gate3CentralCellReceipt,
    *,
    addendum: EconomicDecisionAddendum,
    freeze: Gate3BaselineCalibrationFreeze,
) -> Gate3StreamCell:
    loaded = load_gate3_central_cell_evidence(
        output / receipt.sidecar_name,
        receipt=receipt,
        decision_addendum=addendum,
        expected_roots=receipt.roots,
        expected_corpus_id=receipt.corpus_id,
        expected_stream_id=receipt.stream_id,
        expected_regime=receipt.regime,
        expected_baseline_freeze=freeze,
        expected_cell_id=receipt.cell_id,
        expected_cell_content_sha256=receipt.cell_content_sha256,
        expected_baseline_costs=receipt.baseline_costs,
        expected_full_future_costs=receipt.full_future_costs,
        expected_known_only_costs=receipt.known_only_costs,
        expected_baseline_cost=receipt.baseline_cost,
        expected_full_future_cost=receipt.full_future_cost,
        expected_known_only_cost=receipt.known_only_cost,
        expected_full_future_savings_percent=receipt.full_future_savings_percent,
        expected_unknown_future_contribution_points=(receipt.unknown_future_contribution_points),
        expected_known_only_causal_savings_percent=(receipt.known_only_causal_savings_percent),
        expected_baseline_visibility=receipt.baseline_visibility,
        expected_full_future_visibility=receipt.full_future_visibility,
        expected_known_only_visibility=receipt.known_only_visibility,
        expected_arm_event_counts=receipt.arm_event_counts,
        expected_arm_material_shard_counts=receipt.arm_material_shard_counts,
        expected_all_arm_exact_event_censuses=receipt.all_arm_exact_event_censuses,
        expected_candidate_action_parity_revalidated=(receipt.candidate_action_parity_revalidated),
        expected_common_compute_and_tie_revalidated=(receipt.common_compute_and_tie_revalidated),
        expected_source_lineage="repaired_runtime",
    )
    if loaded.cell_id != receipt.cell_id or loaded.content_sha256 != receipt.cell_content_sha256:
        raise M11EconomicCentralRunnerError(
            "fresh central sidecar differs from its compact receipt"
        )
    return loaded


def _registry(
    backend: AdapterGate3Backend,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    loco = tuple(backend.confirmation_stream_ids("loco-2dics"))
    lectra = tuple(backend.confirmation_stream_ids("lectra-m3-m4"))
    if (
        len(loco) != 20
        or len(lectra) != 20
        or len(set(loco)) != 20
        or len(set(lectra)) != 20
        or set(loco) & set(lectra)
    ):
        raise M11EconomicCentralRunnerError(
            "central backend confirmation registry differs from the canonical census"
        )
    return loco, lectra, loco + lectra


def _require_checkpoint_prefix(
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...],
    *,
    canonical_streams: tuple[str, ...],
) -> None:
    if (
        len(checkpoints) > 40
        or tuple(item.execution_position for item in checkpoints) != tuple(range(len(checkpoints)))
        or tuple(item.stream_id for item in checkpoints) != canonical_streams[: len(checkpoints)]
        or (len(checkpoints) not in range(0, 21) and len(checkpoints) not in range(20, 41))
    ):
        raise M11EconomicCentralRunnerError(
            "central checkpoints are not one canonical contiguous LOCo-first prefix"
        )


def _fresh_validate_checkpoints(
    output: Path,
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...],
    *,
    addendum: EconomicDecisionAddendum,
    freezes: dict[Gate3CorpusId, Gate3BaselineCalibrationFreeze],
) -> None:
    _paths, _unbound, before = _sidecar_census(
        output,
        checkpoints,
        allow_one_orphan=False,
    )
    for checkpoint in checkpoints:
        loaded = _load_cell_sidecar(
            output,
            checkpoint.receipt,
            addendum=addendum,
            freeze=freezes[checkpoint.corpus_id],
        )
        del loaded
    _paths, _unbound, after = _sidecar_census(
        output,
        checkpoints,
        allow_one_orphan=False,
    )
    if after != before:
        raise M11EconomicCentralRunnerError(
            "central sidecar evidence changed during fresh validation"
        )


def _recover_orphan(
    orphan: Path,
    *,
    position: int,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    output: Path,
    calibration: Gate3CalibrationManifest,
    validity: Gate3ValidityStageManifest,
    addendum: EconomicDecisionAddendum,
    freeze: Gate3BaselineCalibrationFreeze,
) -> tuple[Path, Gate3CentralCellCheckpoint]:
    recovered: list[Gate3CentralCellReceipt] = []
    for regime in _REGIMES:
        try:
            receipt = recover_gate3_central_cell_receipt(
                orphan,
                decision_addendum=addendum,
                expected_roots=calibration.roots,
                expected_corpus_id=corpus_id,
                expected_stream_id=stream_id,
                expected_regime=regime,
                expected_baseline_freeze=freeze,
            )
        except (Gate3CentralEvidenceError, TypeError, ValueError):
            continue
        recovered.append(receipt)
    if len(recovered) != 1:
        raise M11EconomicCentralRunnerError(
            "central orphan sidecar does not authenticate uniquely as the next stream"
        )
    checkpoint = build_gate3_central_cell_checkpoint(
        recovered[0],
        execution_position=position,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    path = publish_gate3_central_cell_checkpoint(
        output,
        checkpoint,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    loaded = load_gate3_central_cell_checkpoint(
        path,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        expected_content_sha256=checkpoint.content_sha256,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if loaded != checkpoint:
        raise M11EconomicCentralRunnerError("recovered central checkpoint differs after read-back")
    return path, loaded


def _execute_cell(
    *,
    backend: AdapterGate3Backend,
    position: int,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    output: Path,
    calibration: Gate3CalibrationManifest,
    validity: Gate3ValidityStageManifest,
    addendum: EconomicDecisionAddendum,
    freeze: Gate3BaselineCalibrationFreeze,
) -> tuple[Path, Gate3CentralCellCheckpoint]:
    try:
        cell = backend.execute_central_stream(
            roots=calibration.roots,
            corpus_id=corpus_id,
            stream_id=stream_id,
            baseline_freeze=freeze,
        )
    except Exception as error:
        try:
            backend.discard_incomplete_central_stream_evidence(
                roots=calibration.roots,
                corpus_id=corpus_id,
                stream_id=stream_id,
                baseline_freeze=freeze,
            )
        except Exception as discard_error:
            raise M11EconomicCentralRunnerError(
                "central execution and exact partial-cache discard both failed"
            ) from discard_error
        raise error
    if (
        cell.roots != calibration.roots
        or cell.corpus_id != corpus_id
        or cell.stream_id != stream_id
        or cell.baseline_freeze_id != freeze.freeze_id
        or cell.baseline_freeze_content_sha256 != freeze.content_sha256
    ):
        raise M11EconomicCentralRunnerError(
            "executed central cell differs from exact stream bindings"
        )
    sidecar_path, receipt = publish_gate3_central_cell_evidence(
        output,
        cell,
        decision_addendum=addendum,
    )
    if sidecar_path != output / receipt.sidecar_name:
        raise M11EconomicCentralRunnerError(
            "central sidecar publication path differs from its receipt"
        )
    persisted = _load_cell_sidecar(output, receipt, addendum=addendum, freeze=freeze)
    del persisted
    checkpoint = build_gate3_central_cell_checkpoint(
        receipt,
        execution_position=position,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    checkpoint_path = publish_gate3_central_cell_checkpoint(
        output,
        checkpoint,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    loaded_checkpoint = load_gate3_central_cell_checkpoint(
        checkpoint_path,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        expected_content_sha256=checkpoint.content_sha256,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if loaded_checkpoint != checkpoint:
        raise M11EconomicCentralRunnerError(
            "central checkpoint read-back differs from derived state"
        )
    discovered = discover_gate3_central_cell_checkpoints(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if not any(path == checkpoint_path and item == checkpoint for path, item in discovered):
        raise M11EconomicCentralRunnerError(
            "central checkpoint discovery differs after publication"
        )
    backend.release_central_stream_evidence(
        roots=calibration.roots,
        corpus_id=corpus_id,
        stream_id=stream_id,
        baseline_freeze=freeze,
        expected_cell_id=receipt.cell_id,
        expected_cell_content_sha256=receipt.cell_content_sha256,
    )
    del cell
    return checkpoint_path, checkpoint


def _publish_or_confirm_summary(
    *,
    corpus_id: Gate3CorpusId,
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...],
    canonical_stream_ids: tuple[str, ...],
    existing: tuple[tuple[Path, Gate3EconomicSegmentSummary], ...],
    output: Path,
    calibration: Gate3CalibrationManifest,
    validity: Gate3ValidityStageManifest,
    addendum: EconomicDecisionAddendum,
) -> Gate3EconomicSegmentSummary:
    derived = build_gate3_economic_segment_summary(
        tuple(item.receipt for item in checkpoints),
        canonical_stream_ids=canonical_stream_ids,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    matches = tuple(item for _path, item in existing if item.corpus_id == corpus_id)
    if matches:
        if len(matches) != 1 or matches[0] != derived:
            raise M11EconomicCentralRunnerError(
                "existing central segment summary differs from checkpoints"
            )
        return matches[0]
    path = publish_gate3_economic_segment_summary(
        output,
        derived,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    discovered = discover_gate3_economic_segment_summaries(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if (path, derived) not in discovered:
        raise M11EconomicCentralRunnerError(
            "central segment summary discovery differs after publication"
        )
    return derived


def _publish_or_confirm_manifest(
    *,
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...],
    summaries: tuple[Gate3EconomicSegmentSummary, ...],
    existing: tuple[Path, Gate3EconomicCentralManifest] | None,
    output: Path,
    calibration: Gate3CalibrationManifest,
    validity: Gate3ValidityStageManifest,
    addendum: EconomicDecisionAddendum,
) -> tuple[Path, Gate3EconomicCentralManifest]:
    derived = build_gate3_economic_central_manifest(
        checkpoints,
        segment_summaries=summaries,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if existing is not None:
        if existing[1] != derived:
            raise M11EconomicCentralRunnerError(
                "existing central manifest differs from checkpoint-derived state"
            )
        return existing
    path = publish_gate3_economic_central_manifest(
        output,
        derived,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    discovered = discover_gate3_economic_central_manifest(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if discovered != (path, derived):
        raise M11EconomicCentralRunnerError(
            "terminal central manifest discovery differs after publication"
        )
    return path, derived


def _fresh_validate_upstream_manifests(
    *,
    calibration_path: Path,
    validity_path: Path,
    protocol: EconomicResolutionProtocol,
    roots: object,
    calibration: Gate3CalibrationManifest,
    validity: Gate3ValidityStageManifest,
) -> None:
    loaded_calibration = load_gate3_calibration_manifest(
        calibration_path,
        expected_protocol=protocol,
        expected_roots=roots,
        expected_manifest_id=calibration.manifest_id,
        expected_content_sha256=calibration.content_sha256,
    )
    calibration_discovery = discover_gate3_calibration_manifest(
        calibration_path.parent,
        protocol=protocol,
        legacy_scan=calibration.legacy_scan,
        checkpoints=calibration.checkpoints,
    )
    if loaded_calibration != calibration or calibration_discovery != (
        calibration_path,
        calibration,
    ):
        raise M11EconomicCentralRunnerError(
            "central calibration manifest changed during terminal validation"
        )
    loaded_validity = load_gate3_validity_stage_manifest(
        validity_path,
        expected_protocol=protocol,
        expected_roots=roots,
        expected_calibration_manifest=calibration,
        expected_manifest_id=validity.manifest_id,
        expected_content_sha256=validity.content_sha256,
    )
    validity_discovery = discover_gate3_validity_stage_manifest(
        validity_path.parent,
        protocol=protocol,
        roots=roots,
        calibration_manifest=calibration,
    )
    if loaded_validity != validity or validity_discovery != (validity_path, validity):
        raise M11EconomicCentralRunnerError(
            "central validity manifest changed during terminal validation"
        )


def run_economic_central_stage(
    *,
    repository_root: Path,
    gate1_artifact_path: Path,
    gate2_artifact_path: Path,
    gate3_config_path: Path,
    calibration_manifest_path: Path,
    validity_manifest_path: Path,
    output_directory: Path,
) -> EconomicCentralStageRun:
    """Authenticate, resume, and classify central economics LOCo first."""

    root = Path(repository_root).resolve()
    output = Path(output_directory)
    protocol = build_economic_resolution_protocol()
    addendum = build_economic_decision_addendum(base_protocol=protocol)
    verify_economic_resolution_runtime_lineage(root, protocol)
    authenticated = authenticate_official_gate3_early_inputs(
        repository_root=root,
        gate1_artifact_path=Path(gate1_artifact_path),
        gate2_artifact_path=Path(gate2_artifact_path),
        gate3_config_path=Path(gate3_config_path),
    )
    calibration_id, calibration_sha = _artifact_binding(
        calibration_manifest_path,
        prefix=_CALIBRATION_PREFIX,
        identity_prefix="calibration",
    )
    calibration = load_gate3_calibration_manifest(
        Path(calibration_manifest_path),
        expected_protocol=protocol,
        expected_roots=authenticated.roots,
        expected_manifest_id=calibration_id,
        expected_content_sha256=calibration_sha,
    )
    validity_id, validity_sha = _artifact_binding(
        validity_manifest_path,
        prefix=_VALIDITY_PREFIX,
        identity_prefix="validity",
    )
    validity = load_gate3_validity_stage_manifest(
        Path(validity_manifest_path),
        expected_protocol=protocol,
        expected_roots=authenticated.roots,
        expected_calibration_manifest=calibration,
        expected_manifest_id=validity_id,
        expected_content_sha256=validity_sha,
    )
    calibration_discovery = discover_gate3_calibration_manifest(
        Path(calibration_manifest_path).parent,
        protocol=protocol,
        legacy_scan=calibration.legacy_scan,
        checkpoints=calibration.checkpoints,
    )
    if calibration_discovery != (Path(calibration_manifest_path), calibration):
        raise M11EconomicCentralRunnerError(
            "central stage calibration discovery differs from the exact supplied manifest"
        )
    validity_discovery = discover_gate3_validity_stage_manifest(
        Path(validity_manifest_path).parent,
        protocol=protocol,
        roots=authenticated.roots,
        calibration_manifest=calibration,
    )
    if validity_discovery != (Path(validity_manifest_path), validity):
        raise M11EconomicCentralRunnerError(
            "central stage validity discovery differs from the exact supplied manifest"
        )
    freezes_tuple = _require_validity_authorization(
        calibration=calibration,
        validity=validity,
        protocol=protocol,
        roots=authenticated.roots,
    )
    freezes = _freeze_map(freezes_tuple)
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
    loco_streams, lectra_streams, canonical_streams = _registry(backend)

    discovered_checkpoints = discover_gate3_central_cell_checkpoints(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    checkpoints = tuple(item for _path, item in discovered_checkpoints)
    _require_checkpoint_prefix(checkpoints, canonical_streams=canonical_streams)
    summaries_state = discover_gate3_economic_segment_summaries(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    manifest_state = discover_gate3_economic_central_manifest(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    _by_name, unbound, _fingerprints = _sidecar_census(
        output,
        checkpoints,
        allow_one_orphan=manifest_state is None,
    )
    for checkpoint in checkpoints:
        loaded = _load_cell_sidecar(
            output,
            checkpoint.receipt,
            addendum=addendum,
            freeze=freezes[checkpoint.corpus_id],
        )
        del loaded
    if unbound:
        if manifest_state is not None or len(checkpoints) >= 40:
            raise M11EconomicCentralRunnerError("terminal central state has an unbound sidecar")
        position = len(checkpoints)
        corpus_id: Gate3CorpusId = "loco-2dics" if position < 20 else "lectra-m3-m4"
        _path, recovered = _recover_orphan(
            unbound[0],
            position=position,
            corpus_id=corpus_id,
            stream_id=canonical_streams[position],
            output=output,
            calibration=calibration,
            validity=validity,
            addendum=addendum,
            freeze=freezes[corpus_id],
        )
        checkpoints = (*checkpoints, recovered)
        _require_checkpoint_prefix(checkpoints, canonical_streams=canonical_streams)
        _sidecar_census(output, checkpoints, allow_one_orphan=False)

    if manifest_state is not None:
        manifest_path, manifest = manifest_state
        summaries = tuple(item for _path, item in summaries_state)
        if manifest.checkpoints != checkpoints or manifest.segment_summaries != summaries:
            raise M11EconomicCentralRunnerError(
                "terminal central manifest differs from discovered evidence"
            )
        _fresh_validate_checkpoints(
            output,
            checkpoints,
            addendum=addendum,
            freezes=freezes,
        )
        terminal_checkpoints = discover_gate3_central_cell_checkpoints(
            output,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
        terminal_summaries = discover_gate3_economic_segment_summaries(
            output,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
        terminal_manifest = discover_gate3_economic_central_manifest(
            output,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
        if (
            tuple(item for _path, item in terminal_checkpoints) != checkpoints
            or tuple(item for _path, item in terminal_summaries) != summaries
            or terminal_manifest != manifest_state
        ):
            raise M11EconomicCentralRunnerError(
                "terminal central artifacts changed during fresh validation"
            )
        _fresh_validate_upstream_manifests(
            calibration_path=Path(calibration_manifest_path),
            validity_path=Path(validity_manifest_path),
            protocol=protocol,
            roots=authenticated.roots,
            calibration=calibration,
            validity=validity,
        )
        return EconomicCentralStageRun(
            protocol=protocol,
            decision_addendum=addendum,
            calibration_manifest=calibration,
            validity_manifest=validity,
            checkpoints=checkpoints,
            segment_summaries=summaries,
            manifest=manifest,
            manifest_path=manifest_path,
        )

    for position in range(len(checkpoints), 20):
        _path, checkpoint = _execute_cell(
            backend=backend,
            position=position,
            corpus_id="loco-2dics",
            stream_id=loco_streams[position],
            output=output,
            calibration=calibration,
            validity=validity,
            addendum=addendum,
            freeze=freezes["loco-2dics"],
        )
        checkpoints = (*checkpoints, checkpoint)
    _fresh_validate_checkpoints(
        output,
        checkpoints,
        addendum=addendum,
        freezes=freezes,
    )
    loco_summary = _publish_or_confirm_summary(
        corpus_id="loco-2dics",
        checkpoints=checkpoints[:20],
        canonical_stream_ids=loco_streams,
        existing=summaries_state,
        output=output,
        calibration=calibration,
        validity=validity,
        addendum=addendum,
    )
    summaries: tuple[Gate3EconomicSegmentSummary, ...] = (loco_summary,)
    if loco_summary.decision.candidate_classification == "current_segment_red":
        for position in range(max(len(checkpoints), 20), 40):
            lectra_index = position - 20
            _path, checkpoint = _execute_cell(
                backend=backend,
                position=position,
                corpus_id="lectra-m3-m4",
                stream_id=lectra_streams[lectra_index],
                output=output,
                calibration=calibration,
                validity=validity,
                addendum=addendum,
                freeze=freezes["lectra-m3-m4"],
            )
            checkpoints = (*checkpoints, checkpoint)
        _fresh_validate_checkpoints(
            output,
            checkpoints,
            addendum=addendum,
            freezes=freezes,
        )
        lectra_summary = _publish_or_confirm_summary(
            corpus_id="lectra-m3-m4",
            checkpoints=checkpoints[20:],
            canonical_stream_ids=lectra_streams,
            existing=summaries_state,
            output=output,
            calibration=calibration,
            validity=validity,
            addendum=addendum,
        )
        summaries = (loco_summary, lectra_summary)

    manifest_path, manifest = _publish_or_confirm_manifest(
        checkpoints=checkpoints,
        summaries=summaries,
        existing=None,
        output=output,
        calibration=calibration,
        validity=validity,
        addendum=addendum,
    )
    _fresh_validate_checkpoints(
        output,
        checkpoints,
        addendum=addendum,
        freezes=freezes,
    )
    terminal_checkpoints = discover_gate3_central_cell_checkpoints(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    terminal_summaries = discover_gate3_economic_segment_summaries(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    terminal_manifest = discover_gate3_economic_central_manifest(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if (
        tuple(item for _path, item in terminal_checkpoints) != checkpoints
        or tuple(item for _path, item in terminal_summaries) != summaries
        or terminal_manifest != (manifest_path, manifest)
    ):
        raise M11EconomicCentralRunnerError(
            "terminal central artifacts changed during final fresh validation"
        )
    _fresh_validate_upstream_manifests(
        calibration_path=Path(calibration_manifest_path),
        validity_path=Path(validity_manifest_path),
        protocol=protocol,
        roots=authenticated.roots,
        calibration=calibration,
        validity=validity,
    )
    return EconomicCentralStageRun(
        protocol=protocol,
        decision_addendum=addendum,
        calibration_manifest=calibration,
        validity_manifest=validity,
        checkpoints=checkpoints,
        segment_summaries=summaries,
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
    parser.add_argument("--calibration-manifest", type=Path, required=True)
    parser.add_argument("--validity-manifest", type=Path, required=True)
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
        outcome = run_economic_central_stage(
            repository_root=root,
            gate1_artifact_path=_under_root(root, args.gate1_artifact),
            gate2_artifact_path=_under_root(root, args.gate2_artifact),
            gate3_config_path=_under_root(root, args.gate3_config),
            calibration_manifest_path=_under_root(root, args.calibration_manifest),
            validity_manifest_path=_under_root(root, args.validity_manifest),
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
                "economic_value_resolved": manifest.economic_value_resolved,
                "global_disposition": manifest.global_disposition,
                "manifest_content_sha256": manifest.content_sha256,
                "manifest_id": manifest.manifest_id,
                "manifest_path": str(outcome.manifest_path),
                "next_actions": list(manifest.next_actions),
                "status": manifest.status,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
