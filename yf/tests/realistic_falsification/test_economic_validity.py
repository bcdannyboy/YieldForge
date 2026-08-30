from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.realistic_falsification import test_confirmation as confirmation_cases
from tests.realistic_falsification.test_confirmation import (
    _baseline_freeze,
    _exact_audits,
    _hard_null_controls,
    _roots,
    _twin_controls,
)
from tests.realistic_falsification.test_economic_resolution import (
    _official_references,
    _official_roots,
    _valid_checkpoints,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.confirmation import (
    evaluate_gate3_validity_controls,
)
from yieldforge.realistic_falsification.validity_evidence_store import (
    Gate3ValidityBaselineFreezeBinding,
    Gate3ValidityEvidenceReceipt,
    build_gate3_validity_evidence_receipt,
    load_gate3_validity_evidence,
    publish_gate3_validity_evidence,
    recover_gate3_validity_evidence_receipt,
)


def _resolution():  # type: ignore[no-untyped-def]
    return importlib.import_module("yieldforge.realistic_falsification.economic_resolution")


def _validity():  # type: ignore[no-untyped-def]
    return importlib.import_module("yieldforge.realistic_falsification.economic_validity")


def _calibration_manifest():  # type: ignore[no-untyped-def]
    resolution = _resolution()
    scan = resolution.build_official_legacy_calibration_scan(_official_references(resolution))
    return resolution.build_gate3_calibration_manifest(
        _valid_checkpoints(resolution),
        legacy_scan=scan,
    )


def _compact_evidence(calibration, *, status: str = "valid"):  # type: ignore[no-untyped-def]
    fake_freezes = (
        _baseline_freeze(corpus_id="lectra-m3-m4"),
        _baseline_freeze(corpus_id="loco-2dics"),
    )
    hard_nulls = _hard_null_controls(
        fail_first=status == "invalid",
        baseline_freezes=fake_freezes,
    )
    twins = _twin_controls(
        corpus_id="lectra-m3-m4",
        savings="0.300000" if status == "diagnosis_required" else "0.000000",
    ) + _twin_controls(corpus_id="loco-2dics", savings="0.000000")
    receipt = evaluate_gate3_validity_controls(
        roots=_roots(),
        hard_nulls=hard_nulls,
        twin_controls=twins,
        exact_audits=_exact_audits(baseline_freezes=fake_freezes),
    )
    evidence = build_gate3_validity_evidence_receipt(
        receipt,
        baseline_freezes=fake_freezes,
    )
    official_roots = _official_roots()
    bindings = tuple(
        Gate3ValidityBaselineFreezeBinding(
            corpus_id=item.corpus_id,
            freeze_id=item.freeze_id,
            freeze_content_sha256=item.content_sha256,
            selected_policy_id=item.selected_policy_id,
        )
        for item in calibration.baseline_freezes
    )
    twins = []
    for row in evidence.twin_controls:
        twin_semantic = {
            "schema_version": "yieldforge.m11-gate3-twin-control.v1",
            "roots": official_roots.model_dump(mode="json"),
            "source_stream_id": row.source_stream_id,
            "twin_stream_id": row.twin_stream_id,
            "corpus_id": row.corpus_id,
            "twin_cell_id": row.twin_cell_id,
            "twin_cell_content_sha256": row.twin_cell_content_sha256,
            "baseline_cost": row.baseline_cost,
            "full_future_cost": row.full_future_cost,
            "known_only_cost": row.known_only_cost,
            "no_signal_savings_percent": row.no_signal_savings_percent,
        }
        twin_digest = semantic_sha256(twin_semantic)
        twins.append(
            row.model_copy(
                update={
                    "control_id": f"yfm11g3twin-{twin_digest[:24]}",
                    "control_content_sha256": f"sha256:{twin_digest}",
                }
            )
        )
    summaries = []
    for original, rows in zip(
        evidence.no_signal_summaries,
        (twins[:20], twins[20:]),
        strict=True,
    ):
        summary_semantic = original.model_dump(
            mode="python",
            round_trip=True,
            exclude={"summary_id", "content_sha256"},
        )
        summary_semantic.update(
            {
                "control_ids": tuple(item.control_id for item in rows),
                "control_content_sha256s": tuple(item.control_content_sha256 for item in rows),
            }
        )
        summary_digest = semantic_sha256(summary_semantic)
        summaries.append(
            type(original)(
                summary_id=f"yfm11g3ns-{summary_digest[:24]}",
                content_sha256=f"sha256:{summary_digest}",
                **summary_semantic,
            )
        )
    semantic = evidence.model_dump(
        mode="python",
        round_trip=True,
        exclude={"evidence_id", "content_sha256"},
    )
    semantic.update(
        {
            "roots": official_roots,
            "baseline_freezes": bindings,
            "twin_controls": tuple(twins),
            "no_signal_summaries": tuple(summaries),
        }
    )
    digest = semantic_sha256(
        {
            key: (
                value.model_dump(mode="json")
                if hasattr(value, "model_dump")
                else [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in value
                ]
                if isinstance(value, tuple)
                else value
            )
            for key, value in semantic.items()
        }
    )
    return Gate3ValidityEvidenceReceipt(
        evidence_id=f"yfm11g3valrcpt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _real_official_validity_case(
    monkeypatch: pytest.MonkeyPatch,
):  # type: ignore[no-untyped-def]
    calibration = _calibration_manifest()
    freezes = calibration.baseline_freezes
    monkeypatch.setattr(confirmation_cases, "_roots", _official_roots)
    receipt = evaluate_gate3_validity_controls(
        roots=_official_roots(),
        hard_nulls=confirmation_cases._hard_null_controls(
            baseline_freezes=freezes,
        ),
        twin_controls=(
            confirmation_cases._twin_controls(
                corpus_id="lectra-m3-m4",
                savings="0.000000",
            )
            + confirmation_cases._twin_controls(
                corpus_id="loco-2dics",
                savings="0.000000",
            )
        ),
        exact_audits=confirmation_cases._exact_audits(
            baseline_freezes=freezes,
        ),
    )
    return calibration, freezes, receipt


def test_stage_manifest_derives_scientific_status_and_authorization() -> None:
    validity = _validity()
    calibration = _calibration_manifest()

    valid = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration, status="valid"),
    )
    diagnosis = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration, status="diagnosis_required"),
    )
    invalid = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration, status="invalid"),
    )

    assert (valid.status, valid.central_authorized) == ("valid", True)
    assert (diagnosis.status, diagnosis.central_authorized) == (
        "diagnosis_required",
        False,
    )
    assert (invalid.status, invalid.central_authorized) == ("invalid", False)
    assert valid.protocol == calibration.protocol
    assert valid.roots == calibration.roots
    assert valid.calibration_manifest_id == calibration.manifest_id
    assert valid.calibration_manifest_content_sha256 == calibration.content_sha256
    assert valid.baseline_freezes == calibration.baseline_freezes
    assert valid.validity_evidence == _compact_evidence(calibration, status="valid")
    assert valid.complete is True


def test_stage_manifest_rejects_nonvalid_calibration_and_forged_decision() -> None:
    validity = _validity()
    calibration = _calibration_manifest()
    invalid_calibration = calibration.model_copy(update={"status": "complete_invalid"})

    with pytest.raises(
        validity.Gate3ValidityStageEvidenceError,
        match="calibration manifest",
    ):
        validity.build_gate3_validity_stage_manifest(
            calibration_manifest=invalid_calibration,
            validity_evidence=_compact_evidence(calibration),
        )

    manifest = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration),
    )
    with pytest.raises(ValidationError, match="decision binding"):
        validity.Gate3ValidityStageManifest.model_validate(
            manifest.model_copy(update={"central_authorized": False}).model_dump(
                mode="python",
                round_trip=True,
            ),
            strict=True,
        )


def _published_stage(tmp_path: Path):  # type: ignore[no-untyped-def]
    validity = _validity()
    calibration = _calibration_manifest()
    manifest = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration),
    )
    path = validity.publish_gate3_validity_stage_manifest(
        tmp_path,
        manifest,
        calibration_manifest=calibration,
    )
    return validity, calibration, manifest, path


def _load_stage(validity, calibration, manifest, path):  # type: ignore[no-untyped-def]
    return validity.load_gate3_validity_stage_manifest(
        path,
        expected_protocol=calibration.protocol,
        expected_roots=calibration.roots,
        expected_calibration_manifest=calibration,
        expected_manifest_id=manifest.manifest_id,
        expected_content_sha256=manifest.content_sha256,
    )


def test_stage_manifest_real_filesystem_round_trip_and_discovery(tmp_path: Path) -> None:
    validity, calibration, manifest, path = _published_stage(tmp_path)

    assert path.name == (
        f"m11-economic-validity-stage-{manifest.content_sha256.removeprefix('sha256:')}.json"
    )
    assert _load_stage(validity, calibration, manifest, path) == manifest
    assert validity.discover_gate3_validity_stage_manifest(
        tmp_path,
        protocol=calibration.protocol,
        roots=calibration.roots,
        calibration_manifest=calibration,
    ) == (path, manifest)
    assert (
        validity.publish_gate3_validity_stage_manifest(
            tmp_path,
            manifest,
            calibration_manifest=calibration,
        )
        == path
    )


def test_stage_manifest_publication_requires_the_exact_calibration(
    tmp_path: Path,
) -> None:
    validity = _validity()
    calibration = _calibration_manifest()
    manifest = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration),
    )
    forged_calibration = calibration.model_copy(update={"status": "complete_invalid"})

    with pytest.raises(
        validity.Gate3ValidityStageEvidenceError,
        match="calibration manifest",
    ):
        validity.publish_gate3_validity_stage_manifest(
            tmp_path,
            manifest,
            calibration_manifest=forged_calibration,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["duplicate", "nonfinite", "noncanonical"])
def test_stage_manifest_load_rejects_untrusted_json_encoding(
    tmp_path: Path,
    kind: str,
) -> None:
    validity, calibration, manifest, path = _published_stage(tmp_path)
    raw = path.read_bytes()
    if kind == "duplicate":
        raw = raw.replace(b'  "complete": true,', b'  "complete": true,\n  "complete": true,')
        match = "duplicate"
    elif kind == "nonfinite":
        raw = raw.replace(b'  "central_authorized": true,', b'  "central_authorized": NaN,')
        match = "non-finite"
    else:
        raw = json.dumps(json.loads(raw), allow_nan=False, sort_keys=True).encode()
        match = "canonical"
    path.write_bytes(raw)

    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match=match):
        _load_stage(validity, calibration, manifest, path)


def test_stage_manifest_rejects_symlink_oversize_and_competing_candidates(
    tmp_path: Path,
) -> None:
    validity = _validity()
    calibration = _calibration_manifest()
    manifest = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration),
    )
    prefix = "m11-economic-validity-stage-"
    target = tmp_path / "target.json"
    target.write_text("{}")
    symlink = tmp_path / f"{prefix}{'1' * 64}.json"
    symlink.symlink_to(target)
    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match="non-symlink"):
        validity.discover_gate3_validity_stage_manifest(
            tmp_path,
            protocol=calibration.protocol,
            roots=calibration.roots,
            calibration_manifest=calibration,
        )

    symlink.unlink()
    oversized = tmp_path / f"{prefix}{'2' * 64}.json"
    with oversized.open("wb") as handle:
        handle.truncate(4 * 1024 * 1024 + 1)
    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match="bounded"):
        validity.discover_gate3_validity_stage_manifest(
            tmp_path,
            protocol=calibration.protocol,
            roots=calibration.roots,
            calibration_manifest=calibration,
        )

    oversized.unlink()
    path = validity.publish_gate3_validity_stage_manifest(
        tmp_path,
        manifest,
        calibration_manifest=calibration,
    )
    competitor = tmp_path / f"{prefix}{'3' * 64}.json"
    competitor.write_bytes(path.read_bytes())
    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match="competing"):
        validity.discover_gate3_validity_stage_manifest(
            tmp_path,
            protocol=calibration.protocol,
            roots=calibration.roots,
            calibration_manifest=calibration,
        )


def test_stage_manifest_descriptor_read_detects_mid_read_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validity, calibration, manifest, path = _published_stage(tmp_path)
    original_read = validity.os.read
    mutated = False

    def mutate_after_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, count)
        if not mutated:
            mutated = True
            with path.open("ab") as handle:
                handle.write(b" ")
                handle.flush()
                os.fsync(handle.fileno())
        return chunk

    monkeypatch.setattr(validity.os, "read", mutate_after_read)
    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match="changed during read"):
        _load_stage(validity, calibration, manifest, path)


def test_validity_sidecar_census_requires_exactly_the_manifest_bound_receipt(
    tmp_path: Path,
) -> None:
    validity = _validity()
    calibration = _calibration_manifest()
    evidence = _compact_evidence(calibration)

    assert (
        validity.require_gate3_validity_sidecar_census(
            tmp_path,
            expected_evidence=None,
        )
        is None
    )
    expected = tmp_path / evidence.sidecar_name
    expected.write_bytes(b"bounded-sidecar-placeholder")
    assert (
        validity.require_gate3_validity_sidecar_census(
            tmp_path,
            expected_evidence=evidence,
        )
        == expected
    )

    competitor = tmp_path / (f"m11-gate3-validity-receipt-{'1' * 64}-{'2' * 64}.json.gz")
    competitor.write_bytes(b"competing-sidecar-placeholder")
    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match="competing"):
        validity.require_gate3_validity_sidecar_census(
            tmp_path,
            expected_evidence=evidence,
        )


def test_validity_evidence_checkpoint_round_trip_and_competing_discovery(
    tmp_path: Path,
) -> None:
    validity = _validity()
    calibration = _calibration_manifest()
    evidence = _compact_evidence(calibration)
    checkpoint = validity.build_gate3_validity_evidence_checkpoint(
        calibration_manifest=calibration,
        validity_evidence=evidence,
    )

    assert checkpoint.protocol == calibration.protocol
    assert checkpoint.roots == calibration.roots
    assert checkpoint.calibration_manifest_id == calibration.manifest_id
    assert checkpoint.calibration_manifest_content_sha256 == calibration.content_sha256
    assert checkpoint.baseline_freezes == calibration.baseline_freezes
    assert checkpoint.validity_evidence == evidence
    assert checkpoint.complete is True

    path = validity.publish_gate3_validity_evidence_checkpoint(
        tmp_path,
        checkpoint,
        calibration_manifest=calibration,
    )
    loaded = validity.load_gate3_validity_evidence_checkpoint(
        path,
        expected_protocol=calibration.protocol,
        expected_roots=calibration.roots,
        expected_calibration_manifest=calibration,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        expected_content_sha256=checkpoint.content_sha256,
    )
    assert loaded == checkpoint
    assert validity.discover_gate3_validity_evidence_checkpoint(
        tmp_path,
        protocol=calibration.protocol,
        roots=calibration.roots,
        calibration_manifest=calibration,
    ) == (path, checkpoint)

    competitor = tmp_path / (f"m11-economic-validity-evidence-checkpoint-{'4' * 64}.json")
    competitor.write_bytes(path.read_bytes())
    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match="competing"):
        validity.discover_gate3_validity_evidence_checkpoint(
            tmp_path,
            protocol=calibration.protocol,
            roots=calibration.roots,
            calibration_manifest=calibration,
        )


@pytest.mark.parametrize("kind", ["duplicate", "nonfinite", "noncanonical"])
def test_validity_evidence_checkpoint_load_rejects_untrusted_json_encoding(
    tmp_path: Path,
    kind: str,
) -> None:
    validity = _validity()
    calibration = _calibration_manifest()
    checkpoint = validity.build_gate3_validity_evidence_checkpoint(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration),
    )
    path = validity.publish_gate3_validity_evidence_checkpoint(
        tmp_path,
        checkpoint,
        calibration_manifest=calibration,
    )
    raw = path.read_bytes()
    if kind == "duplicate":
        raw = raw.replace(b'  "complete": true,', b'  "complete": true,\n  "complete": true,')
        match = "duplicate"
    elif kind == "nonfinite":
        raw = raw.replace(
            b'  "calibration_failure_count": 0,', b'  "calibration_failure_count": NaN,'
        )
        match = "non-finite"
    else:
        raw = json.dumps(json.loads(raw), allow_nan=False, sort_keys=True).encode()
        match = "canonical"
    path.write_bytes(raw)

    with pytest.raises(validity.Gate3ValidityStageEvidenceError, match=match):
        validity.load_gate3_validity_evidence_checkpoint(
            path,
            expected_protocol=calibration.protocol,
            expected_roots=calibration.roots,
            expected_calibration_manifest=calibration,
            expected_checkpoint_id=checkpoint.checkpoint_id,
            expected_content_sha256=checkpoint.content_sha256,
        )


def test_real_synthetic_validity_sidecar_recovers_checkpoint_and_terminal_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validity = _validity()
    calibration, freezes, receipt = _real_official_validity_case(monkeypatch)
    sidecar_path, evidence = publish_gate3_validity_evidence(
        tmp_path,
        receipt,
        baseline_freezes=freezes,
    )

    assert validity.discover_sole_gate3_validity_sidecar(tmp_path) == sidecar_path
    recovered_evidence = recover_gate3_validity_evidence_receipt(
        sidecar_path,
        expected_roots=calibration.roots,
        expected_baseline_freezes=freezes,
    )
    assert recovered_evidence == evidence

    loaded = load_gate3_validity_evidence(
        sidecar_path,
        evidence_receipt=evidence,
        expected_roots=calibration.roots,
        expected_baseline_freezes=freezes,
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
    assert loaded == receipt

    checkpoint = validity.build_gate3_validity_evidence_checkpoint(
        calibration_manifest=calibration,
        validity_evidence=recovered_evidence,
    )
    checkpoint_path = validity.publish_gate3_validity_evidence_checkpoint(
        tmp_path,
        checkpoint,
        calibration_manifest=calibration,
    )
    stage = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=recovered_evidence,
    )
    stage_path = validity.publish_gate3_validity_stage_manifest(
        tmp_path,
        stage,
        calibration_manifest=calibration,
    )

    assert validity.discover_gate3_validity_evidence_checkpoint(
        tmp_path,
        protocol=calibration.protocol,
        roots=calibration.roots,
        calibration_manifest=calibration,
    ) == (checkpoint_path, checkpoint)
    assert validity.discover_gate3_validity_stage_manifest(
        tmp_path,
        protocol=calibration.protocol,
        roots=calibration.roots,
        calibration_manifest=calibration,
    ) == (stage_path, stage)
    assert (
        validity.require_gate3_validity_sidecar_census(
            tmp_path,
            expected_evidence=evidence,
        )
        == sidecar_path
    )
