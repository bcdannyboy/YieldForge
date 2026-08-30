from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
from decimal import ROUND_HALF_EVEN, Decimal
from functools import lru_cache

import numpy as np
import pytest
from pydantic import BaseModel, ValidationError

from tests.realistic_falsification.test_central_evidence_store import (
    _central_cell,
)
from tests.realistic_falsification.test_economic_validity import (
    _calibration_manifest,
    _compact_evidence,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.central_evidence_store import (
    Gate3CentralCellReceipt,
    build_gate3_central_cell_receipt,
)
from yieldforge.realistic_falsification.confirmation import build_gate3_cost_ledger
from yieldforge.realistic_falsification.economic_decision import (
    build_economic_decision_addendum,
)
from yieldforge.realistic_falsification.economic_resolution import (
    build_economic_resolution_protocol,
)
from yieldforge.realistic_falsification.economic_validity import (
    build_gate3_validity_stage_manifest,
)


def test_economic_central_module_exists() -> None:
    assert (
        importlib.util.find_spec("yieldforge.realistic_falsification.economic_central") is not None
    )


def _central():  # type: ignore[no-untyped-def]
    return importlib.import_module("yieldforge.realistic_falsification.economic_central")


@lru_cache(maxsize=3)
def _upstream(*, validity_status: str = "valid"):  # type: ignore[no-untyped-def]
    calibration = _calibration_manifest()
    validity = build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=_compact_evidence(calibration, status=validity_status),
    )
    addendum = build_economic_decision_addendum(base_protocol=build_economic_resolution_protocol())
    return calibration, validity, addendum


def _ledger(net_cost: str):
    return build_gate3_cost_ledger(
        purchase_cost=net_cost,
        storage_cost="0.000000",
        return_handling_cost="0.000000",
        retrieval_handling_cost="0.000000",
        scrap_proceeds="0.000000",
        terminal_credit="0.000000",
    )


def _metric(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN), ".12f")


def _jsonish(value):  # type: ignore[no-untyped-def]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_jsonish(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonish(item) for key, item in value.items()}
    return value


def _receipt(
    *,
    position: int,
    corpus_id: str,
    stream_id: str,
    f_savings: str,
    k_savings: str,
) -> Gate3CentralCellReceipt:
    calibration, _validity, addendum = _upstream()
    freeze = next(item for item in calibration.baseline_freezes if item.corpus_id == corpus_id)
    template = _template_receipt()
    f_value = Decimal(f_savings)
    k_value = Decimal(k_savings)
    baseline = Decimal("100.000000")
    full = baseline - f_value
    known = baseline - k_value
    cell_digest = hashlib.sha256(f"{corpus_id}:{stream_id}:{position}".encode()).hexdigest()
    transport_digest = hashlib.sha256(f"transport:{cell_digest}".encode()).hexdigest()
    payload = template.model_dump(mode="python", round_trip=True)
    payload.update(
        {
            "roots": calibration.roots,
            "corpus_id": corpus_id,
            "stream_id": stream_id,
            "regime": ("regime_shift" if position % 4 == 3 else "recurrent"),
            "baseline_freeze_id": freeze.freeze_id,
            "baseline_freeze_content_sha256": freeze.content_sha256,
            "selected_policy_id": freeze.selected_policy_id,
            "cell_id": f"yfm11g3cell-{cell_digest[:24]}",
            "cell_content_sha256": f"sha256:{cell_digest}",
            "baseline_costs": _ledger(format(baseline, ".6f")),
            "full_future_costs": _ledger(format(full, ".6f")),
            "known_only_costs": _ledger(format(known, ".6f")),
            "baseline_cost": format(baseline, ".6f"),
            "full_future_cost": format(full, ".6f"),
            "known_only_cost": format(known, ".6f"),
            "full_future_savings_percent": _metric(f_value),
            "unknown_future_contribution_points": _metric(f_value - k_value),
            "known_only_causal_savings_percent": _metric(k_value),
            "baseline_visibility": (
                "known_only"
                if freeze.selected_policy_id == "known_only_m9_two_ply_scrap"
                else "released_only"
            ),
            "sidecar_name": (f"m11-gate3-central-cell-{cell_digest}-{transport_digest}.json.gz"),
            "compressed_raw_sha256": f"sha256:{transport_digest}",
            "compressed_byte_count": 1000 + position,
            "uncompressed_byte_count": 5000 + position,
        }
    )
    semantic = dict(payload)
    semantic.pop("receipt_id")
    semantic.pop("content_sha256")
    digest = semantic_sha256(_jsonish(semantic))
    payload["receipt_id"] = f"yfm11g3cellrcpt-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return Gate3CentralCellReceipt.model_validate(payload, strict=True)


@lru_cache(maxsize=1)
def _template_receipt() -> Gate3CentralCellReceipt:
    _calibration, _validity, addendum = _upstream()
    return build_gate3_central_cell_receipt(
        _central_cell(),
        decision_addendum=addendum,
    )


def _receipts(
    *,
    corpus_id: str = "loco-2dics",
    f_values: tuple[str, ...] | None = None,
    k_values: tuple[str, ...] | None = None,
) -> tuple[Gate3CentralCellReceipt, ...]:
    f_values = f_values or tuple(str(index + 1) for index in range(20))
    k_values = k_values or tuple(str(index / 2) for index in range(20))
    prefix = "loco" if corpus_id == "loco-2dics" else "lectra"
    return tuple(
        _receipt(
            position=index,
            corpus_id=corpus_id,
            stream_id=f"{prefix}-confirmation-{index:02d}",
            f_savings=f_values[index],
            k_savings=k_values[index],
        )
        for index in range(20)
    )


def test_segment_summary_matches_frozen_pcg64_type7_and_half_even() -> None:
    central = _central()
    calibration, validity, addendum = _upstream()
    receipts = _receipts()

    summary = central.build_gate3_economic_segment_summary(
        receipts,
        canonical_stream_ids=tuple(item.stream_id for item in receipts),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    generator = np.random.Generator(np.random.PCG64(0))
    generator.integers(0, 20, size=(10_000, 20), dtype=np.int64)  # Lectra first.
    loco_indices = generator.integers(0, 20, size=(10_000, 20), dtype=np.int64)
    f_values = np.asarray([float(item.full_future_savings_percent) for item in receipts])
    k_values = np.asarray([float(item.known_only_causal_savings_percent) for item in receipts])
    expected_f = np.quantile(f_values[loco_indices].mean(axis=1), (0.025, 0.975), method="linear")
    expected_k = np.quantile(k_values[loco_indices].mean(axis=1), (0.025, 0.975), method="linear")

    assert summary.f_mean_savings_percent == "10.500000000000"
    assert summary.f_mean_ci_lower_percent == _metric(Decimal(str(expected_f[0])))
    assert summary.f_mean_ci_upper_percent == _metric(Decimal(str(expected_f[1])))
    assert summary.k_mean_ci_lower_percent == _metric(Decimal(str(expected_k[0])))
    assert summary.k_mean_ci_upper_percent == _metric(Decimal(str(expected_k[1])))
    assert summary.f_median_savings_percent == "10.500000000000"
    assert summary.f_positive_stream_fraction == "1.000000000000"
    assert summary.k_positive_stream_fraction == "0.950000000000"
    assert summary.decision.candidate_classification == "causal_candidate"
    assert summary.decision.loco_next_step == "CONTINUE_ADVERSE_LOCO"
    assert summary.productization_authorized is False
    assert summary.bounded_pilot_authorized is False


@pytest.mark.parametrize(
    ("f_value", "k_value", "classification", "next_step"),
    (
        ("3.000000", "2.000000", "causal_candidate", "CONTINUE_ADVERSE_LOCO"),
        ("3.000000", "0.000000", "forecast_candidate", "CONTINUE_FORECAST_LOCO"),
        ("1.000000", "0.000000", "current_segment_red", "CONTINUE_LECTRA_SCREEN"),
    ),
)
def test_segment_summary_rederives_every_threshold_and_branch(
    f_value: str,
    k_value: str,
    classification: str,
    next_step: str,
) -> None:
    central = _central()
    calibration, validity, addendum = _upstream()
    receipts = _receipts(
        f_values=(f_value,) * 20,
        k_values=(k_value,) * 20,
    )

    summary = central.build_gate3_economic_segment_summary(
        receipts,
        canonical_stream_ids=tuple(item.stream_id for item in receipts),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    assert summary.decision.candidate_classification == classification
    assert summary.next_action == next_step


def test_segment_summary_rejects_invalid_validity_and_receipt_census() -> None:
    central = _central()
    calibration, invalid, addendum = _upstream(validity_status="invalid")
    receipts = _receipts()

    with pytest.raises(central.Gate3EconomicCentralEvidenceError, match="validity"):
        central.build_gate3_economic_segment_summary(
            receipts,
            canonical_stream_ids=tuple(item.stream_id for item in receipts),
            calibration_manifest=calibration,
            validity_manifest=invalid,
            decision_addendum=addendum,
        )
    calibration, validity, addendum = _upstream()
    with pytest.raises(central.Gate3EconomicCentralEvidenceError, match="twenty"):
        central.build_gate3_economic_segment_summary(
            receipts[:-1],
            canonical_stream_ids=tuple(item.stream_id for item in receipts),
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )


def test_segment_summary_rejects_resigned_metric_or_decision_contradiction() -> None:
    central = _central()
    calibration, validity, addendum = _upstream()
    receipts = _receipts()
    summary = central.build_gate3_economic_segment_summary(
        receipts,
        canonical_stream_ids=tuple(item.stream_id for item in receipts),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    payload = summary.model_dump(mode="python", round_trip=True)
    payload["f_mean_savings_percent"] = "9.000000000000"
    semantic = dict(payload)
    semantic.pop("summary_id")
    semantic.pop("content_sha256")
    digest = semantic_sha256(semantic)
    payload["summary_id"] = f"yfm11econsegsummary-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    with pytest.raises(ValidationError, match="reconcile"):
        central.Gate3EconomicSegmentSummary.model_validate(payload, strict=True)


def test_checkpoint_round_trip_binds_exact_upstream_and_receipt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    central = _central()
    calibration, validity, addendum = _upstream()
    receipt = _receipts()[0]
    checkpoint = central.build_gate3_central_cell_checkpoint(
        receipt,
        execution_position=0,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    path = central.publish_gate3_central_cell_checkpoint(
        tmp_path,
        checkpoint,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    loaded = central.load_gate3_central_cell_checkpoint(
        path,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        expected_content_sha256=checkpoint.content_sha256,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    discovered = central.discover_gate3_central_cell_checkpoints(
        tmp_path,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    assert loaded == checkpoint
    assert discovered == ((path, checkpoint),)
    assert checkpoint.receipt_id == receipt.receipt_id
    assert checkpoint.receipt_content_sha256 == receipt.content_sha256


def test_checkpoint_discovery_rejects_competitor_tamper_and_nonvalid_upstream(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    central = _central()
    calibration, validity, addendum = _upstream()
    receipt = _receipts()[0]
    checkpoint = central.build_gate3_central_cell_checkpoint(
        receipt,
        execution_position=0,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    path = central.publish_gate3_central_cell_checkpoint(
        tmp_path,
        checkpoint,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    raw = bytearray(path.read_bytes())
    raw[-2] = ord("0") if raw[-2] != ord("0") else ord("1")
    path.write_bytes(raw)
    with pytest.raises(central.Gate3EconomicCentralEvidenceError):
        central.discover_gate3_central_cell_checkpoints(
            tmp_path,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )

    other = tmp_path / ("m11-economic-central-cell-checkpoint-01-" + "a" * 64 + ".json")
    other.write_text("{}", encoding="utf-8")
    with pytest.raises(central.Gate3EconomicCentralEvidenceError):
        central.discover_gate3_central_cell_checkpoints(
            tmp_path,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
    _calibration, invalid, _addendum = _upstream(validity_status="invalid")
    with pytest.raises(central.Gate3EconomicCentralEvidenceError, match="validity"):
        central.build_gate3_central_cell_checkpoint(
            receipt,
            execution_position=0,
            calibration_manifest=calibration,
            validity_manifest=invalid,
            decision_addendum=addendum,
        )


def test_central_artifact_reader_uses_nonblocking_and_rejects_nonregular_open_race(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    central = _central()
    candidate = tmp_path / "artifact.json"
    candidate.write_bytes(b"{}")
    read_descriptor, write_descriptor = os.pipe()
    os.set_blocking(read_descriptor, False)
    seen_flags: list[int] = []

    def substitute_open(path, flags):  # type: ignore[no-untyped-def]
        seen_flags.append(flags)
        return read_descriptor

    monkeypatch.setattr(central.os, "open", substitute_open)
    try:
        with pytest.raises(central.Gate3EconomicCentralEvidenceError):
            central._read_bounded_regular_file(candidate)
    finally:
        os.close(write_descriptor)
    assert seen_flags[0] & os.O_NONBLOCK


def test_central_artifact_reader_rejects_path_replacement_after_open(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    central = _central()
    candidate = tmp_path / "artifact.json"
    replacement = tmp_path / "replacement.json"
    candidate.write_bytes(b"{}")
    replacement.write_bytes(b"{}")
    original_read = central.os.read
    replaced = False

    def replace_after_read(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        chunk = original_read(descriptor, count)
        if not replaced:
            replaced = True
            os.replace(replacement, candidate)
        return chunk

    monkeypatch.setattr(central.os, "read", replace_after_read)
    with pytest.raises(central.Gate3EconomicCentralEvidenceError, match="changed"):
        central._read_bounded_regular_file(candidate)


def _checkpoints_for(
    receipts: tuple[Gate3CentralCellReceipt, ...],
    *,
    start: int,
):
    central = _central()
    calibration, validity, addendum = _upstream()
    return tuple(
        central.build_gate3_central_cell_checkpoint(
            receipt,
            execution_position=start + offset,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
        for offset, receipt in enumerate(receipts)
    )


def test_terminal_manifest_stops_on_loco_candidate_without_lectra() -> None:
    central = _central()
    calibration, validity, addendum = _upstream()
    receipts = _receipts(
        f_values=("3.000000",) * 20,
        k_values=("2.000000",) * 20,
    )
    summary = central.build_gate3_economic_segment_summary(
        receipts,
        canonical_stream_ids=tuple(item.stream_id for item in receipts),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    manifest = central.build_gate3_economic_central_manifest(
        _checkpoints_for(receipts, start=0),
        segment_summaries=(summary,),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    assert manifest.total_cell_count == 20
    assert manifest.status == "adverse_confirmation_required"
    assert manifest.next_actions == ("CONTINUE_ADVERSE_LOCO",)
    assert manifest.economic_value_resolved is False
    assert manifest.cross_segment_decision is None
    assert manifest.productization_authorized is False
    assert manifest.bounded_pilot_authorized is False


def test_terminal_manifest_both_red_is_scoped_economic_resolution() -> None:
    central = _central()
    calibration, validity, addendum = _upstream()
    loco = _receipts(
        f_values=("1.000000",) * 20,
        k_values=("0.000000",) * 20,
    )
    lectra = _receipts(
        corpus_id="lectra-m3-m4",
        f_values=("1.000000",) * 20,
        k_values=("0.000000",) * 20,
    )
    summaries = tuple(
        central.build_gate3_economic_segment_summary(
            receipts,
            canonical_stream_ids=tuple(item.stream_id for item in receipts),
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
        for receipts in (loco, lectra)
    )

    manifest = central.build_gate3_economic_central_manifest(
        _checkpoints_for(loco, start=0) + _checkpoints_for(lectra, start=20),
        segment_summaries=summaries,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    assert manifest.total_cell_count == 40
    assert manifest.status == "insufficient_current_modeled_value"
    assert manifest.global_disposition == "INSUFFICIENT_CURRENT_MODELED_VALUE"
    assert manifest.next_actions == ()
    assert manifest.economic_value_resolved is True
    assert manifest.cross_segment_decision is not None
    assert manifest.cross_segment_decision.terminal is True
    assert (
        manifest.resolution_scope
        == "current_modeled_value_not_proof_no_possible_algorithm_can_work"
    )


def test_terminal_manifest_rejects_loco_red_without_lectra() -> None:
    central = _central()
    calibration, validity, addendum = _upstream()
    receipts = _receipts(
        f_values=("1.000000",) * 20,
        k_values=("0.000000",) * 20,
    )
    summary = central.build_gate3_economic_segment_summary(
        receipts,
        canonical_stream_ids=tuple(item.stream_id for item in receipts),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    with pytest.raises(central.Gate3EconomicCentralEvidenceError, match="Lectra"):
        central.build_gate3_economic_central_manifest(
            _checkpoints_for(receipts, start=0),
            segment_summaries=(summary,),
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )


def test_summary_and_manifest_files_round_trip_and_reject_competitors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    central = _central()
    calibration, validity, addendum = _upstream()
    receipts = _receipts(
        f_values=("3.000000",) * 20,
        k_values=("2.000000",) * 20,
    )
    checkpoints = _checkpoints_for(receipts, start=0)
    summary = central.build_gate3_economic_segment_summary(
        receipts,
        canonical_stream_ids=tuple(item.stream_id for item in receipts),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    summary_path = central.publish_gate3_economic_segment_summary(
        tmp_path,
        summary,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    manifest = central.build_gate3_economic_central_manifest(
        checkpoints,
        segment_summaries=(summary,),
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    manifest_path = central.publish_gate3_economic_central_manifest(
        tmp_path,
        manifest,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )

    assert central.discover_gate3_economic_segment_summaries(
        tmp_path,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    ) == ((summary_path, summary),)
    assert central.discover_gate3_economic_central_manifest(
        tmp_path,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    ) == (manifest_path, manifest)
    competitor = tmp_path / ("m11-economic-central-manifest-" + "b" * 64 + ".json")
    competitor.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(central.Gate3EconomicCentralEvidenceError, match="competing"):
        central.discover_gate3_economic_central_manifest(
            tmp_path,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
