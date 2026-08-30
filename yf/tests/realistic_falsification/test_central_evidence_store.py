"""Tests for compact authenticated Gate 3 central-cell evidence."""

from __future__ import annotations

import gc
import gzip
import hashlib
import importlib
import importlib.util
import json
import os
import weakref
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.economic_decision import (
    build_economic_decision_addendum,
)
from yieldforge.realistic_falsification.economic_resolution import (
    build_economic_resolution_protocol,
)


def test_central_evidence_store_module_exists() -> None:
    assert (
        importlib.util.find_spec("yieldforge.realistic_falsification.central_evidence_store")
        is not None
    )


def _store():  # type: ignore[no-untyped-def]
    return importlib.import_module("yieldforge.realistic_falsification.central_evidence_store")


def _addendum():  # type: ignore[no-untyped-def]
    return build_economic_decision_addendum(base_protocol=build_economic_resolution_protocol())


def _confirmation():  # type: ignore[no-untyped-def]
    return importlib.import_module("yieldforge.realistic_falsification.confirmation")


def _roots():  # type: ignore[no-untyped-def]
    confirmation = _confirmation()
    return confirmation.build_gate3_root_binding(
        contract_id="yfm11c-" + "1" * 24,
        contract_content_sha256="sha256:" + "1" * 64,
        population_id="yfm11pop-" + "2" * 24,
        population_content_sha256="sha256:" + "2" * 64,
        gate1_run_id="yfm11g1run-" + "3" * 24,
        gate1_run_content_sha256="sha256:" + "3" * 64,
        gate1_evaluation_result_id="yfm11g1r-" + "4" * 24,
        gate1_evaluation_result_content_sha256="sha256:" + "4" * 64,
        gate2_run_id="yfm11g2run-" + "5" * 24,
        gate2_run_content_sha256="sha256:" + "5" * 64,
        gate2_evaluation_result_id="yfm11g2r-" + "6" * 24,
        gate2_evaluation_result_content_sha256="sha256:" + "6" * 64,
        gate3_config_id="yfm11g3c-" + "7" * 24,
        gate3_config_content_sha256="sha256:" + "7" * 64,
        adapter_runtime_config_sha256="sha256:" + "8" * 64,
    )


def _baseline_freeze():  # type: ignore[no-untyped-def]
    confirmation = _confirmation()
    stream_ids = tuple(f"calibration-stream-{index}" for index in range(8))
    costs = {
        policy_id: tuple(
            "98.000000" if policy_id == "age_regularity" else "100.000000" for _ in stream_ids
        )
        for policy_id in confirmation.GATE3_BASELINE_POLICY_IDS
    }
    openings = {
        policy_id: tuple(8 for _ in stream_ids)
        for policy_id in confirmation.GATE3_BASELINE_POLICY_IDS
    }
    invalid = {policy_id: 0 for policy_id in confirmation.GATE3_BASELINE_POLICY_IDS}
    return confirmation.select_gate3_baseline_policy(
        roots=_roots(),
        corpus_id="loco-2dics",
        calibration_stream_ids=stream_ids,
        policy_stream_costs=costs,
        policy_stream_sheet_openings=openings,
        policy_invalid_stream_counts=invalid,
    )


def _ledger(purchase: str):  # type: ignore[no-untyped-def]
    return _confirmation().build_gate3_cost_ledger(
        purchase_cost=purchase,
        storage_cost="0.000000",
        return_handling_cost="0.000000",
        retrieval_handling_cost="0.000000",
        scrap_proceeds="0.000000",
        terminal_credit="0.000000",
    )


def _decision(*, position: int, arm: str):  # type: ignore[no-untyped-def]
    is_reference = arm in ("F", "K")
    return _confirmation().build_gate3_decision_trace(
        event_position=position,
        event_id=f"event-{position}",
        arm=arm,
        algorithm="m9_two_ply" if is_reference else "m7_policy",
        visibility=(
            "full_future" if arm == "F" else "known_only" if arm == "K" else "released_only"
        ),
        policy_id="age_regularity",
        standard_candidate_set_sha256="sha256:" + "a" * 64,
        search_config_sha256="sha256:" + "b" * 64,
        compute_budget_sha256="sha256:" + "c" * 64,
        search_runtime_sha256="sha256:" + "f" * 64 if is_reference else None,
        action_catalog_sha256="sha256:" + f"{position % 16:x}" * 64,
        action_ids=("standard", "remnant"),
        baseline_action_id="standard",
        selected_action_id="standard",
        selected_immediate_cost="1.000000",
        baseline_immediate_cost="1.000000",
        m9_root_scores=(
            (("remnant", "2.000000"), ("standard", "1.000000")) if is_reference else ()
        ),
        inventory_before_sha256="sha256:" + "d" * 64,
        inventory_after_sha256="sha256:" + "e" * 64,
        returned_lineage_root_ids=(),
        selected_lineage_root_id=None,
        m9_catalog_count=2 if is_reference else 0,
        m9_explicit_transition_count=2 if is_reference else 0,
        m9_continuation_event_count=0,
        m9_start_event_position=position if is_reference else None,
        m9_stop_event_position=position + 1 if is_reference else None,
    )


def _arm(*, arm: str, purchase: str):  # type: ignore[no-untyped-def]
    confirmation = _confirmation()
    stream_id = "loco-confirmation-00"
    visibility = "full_future" if arm == "F" else "known_only" if arm == "K" else "released_only"
    shard = confirmation.build_gate3_shard_trace(
        roots=_roots(),
        stream_id=stream_id,
        corpus_id="loco-2dics",
        shard_id="shard-0",
        material_key="material-0",
        arm=arm,
        policy_id="age_regularity",
        visibility=visibility,
        projection_binding_sha256="sha256:" + "6" * 64,
        decisions=tuple(_decision(position=position, arm=arm) for position in range(24)),
        final_costs=_ledger(purchase),
    )
    return confirmation.merge_gate3_material_shards(
        roots=_roots(),
        stream_id=stream_id,
        corpus_id="loco-2dics",
        regime="regime_shift",
        arm=arm,
        policy_id="age_regularity",
        shards=(shard,),
    )


def _central_cell():  # type: ignore[no-untyped-def]
    return _confirmation().build_gate3_stream_cell(
        roots=_roots(),
        baseline_freeze=_baseline_freeze(),
        baseline=_arm(arm="B", purchase="100.000000"),
        full_future=_arm(arm="F", purchase="97.000000"),
        known_only=_arm(arm="K", purchase="99.000000"),
    )


def _resign_receipt(payload: dict[str, object]) -> dict[str, object]:
    semantic = dict(payload)
    semantic.pop("receipt_id", None)
    semantic.pop("content_sha256", None)
    digest = semantic_sha256(semantic)
    payload["receipt_id"] = f"yfm11g3cellrcpt-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return payload


def _resign_transport(receipt, transport: bytes, raw: bytes | None = None):  # type: ignore[no-untyped-def]
    store = _store()
    payload = receipt.model_dump(mode="python", round_trip=True)
    transport_hash = hashlib.sha256(transport).hexdigest()
    cell_hash = receipt.cell_content_sha256.removeprefix("sha256:")
    payload.update(
        {
            "sidecar_name": (f"m11-gate3-central-cell-{cell_hash}-{transport_hash}.json.gz"),
            "compressed_raw_sha256": f"sha256:{transport_hash}",
            "compressed_byte_count": len(transport),
            "uncompressed_byte_count": len(gzip.decompress(transport) if raw is None else raw),
        }
    )
    return store.Gate3CentralCellReceipt.model_validate(
        _resign_receipt(payload),
        strict=True,
    )


def _load(path: Path, cell, receipt):  # type: ignore[no-untyped-def]
    return _store().load_gate3_central_cell_evidence(
        path,
        receipt=receipt,
        decision_addendum=_addendum(),
        expected_roots=cell.roots,
        expected_corpus_id=cell.corpus_id,
        expected_stream_id=cell.stream_id,
        expected_regime=cell.regime,
        expected_baseline_freeze=cell.baseline_freeze,
        expected_cell_id=cell.cell_id,
        expected_cell_content_sha256=cell.content_sha256,
        expected_baseline_costs=cell.baseline.final_costs,
        expected_full_future_costs=cell.full_future.final_costs,
        expected_known_only_costs=cell.known_only.final_costs,
        expected_baseline_cost=cell.baseline_cost,
        expected_full_future_cost=cell.full_future_cost,
        expected_known_only_cost=cell.known_only_cost,
        expected_full_future_savings_percent=cell.full_future_savings_percent,
        expected_unknown_future_contribution_points=(cell.unknown_future_contribution_points),
        expected_known_only_causal_savings_percent=(receipt.known_only_causal_savings_percent),
        expected_baseline_visibility=cell.baseline.visibility,
        expected_full_future_visibility=cell.full_future.visibility,
        expected_known_only_visibility=cell.known_only.visibility,
        expected_arm_event_counts=(24, 24, 24),
        expected_arm_material_shard_counts=tuple(
            item.material_shard_count for item in (cell.baseline, cell.full_future, cell.known_only)
        ),
        expected_all_arm_exact_event_censuses=True,
        expected_candidate_action_parity_revalidated=True,
        expected_common_compute_and_tie_revalidated=True,
        expected_source_lineage="repaired_runtime",
    )


def _publish(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = _store()
    cell = _central_cell()
    path, receipt = store.publish_gate3_central_cell_evidence(
        tmp_path,
        cell,
        decision_addendum=_addendum(),
    )
    return cell, path, receipt


def test_compact_receipt_binds_full_cell_economics_and_decision_addendum() -> None:
    store = _store()
    cell = _central_cell()
    addendum = _addendum()

    receipt = store.build_gate3_central_cell_receipt(
        cell,
        decision_addendum=addendum,
    )

    assert receipt.decision_addendum_id == addendum.addendum_id
    assert receipt.decision_addendum_content_sha256 == addendum.content_sha256
    assert receipt.base_protocol_id == addendum.base_protocol_id
    assert receipt.base_protocol_content_sha256 == addendum.base_protocol_content_sha256
    assert receipt.roots == cell.roots
    assert (receipt.corpus_id, receipt.stream_id, receipt.regime) == (
        cell.corpus_id,
        cell.stream_id,
        cell.regime,
    )
    assert receipt.baseline_freeze_id == cell.baseline_freeze.freeze_id
    assert receipt.baseline_freeze_content_sha256 == cell.baseline_freeze.content_sha256
    assert receipt.selected_policy_id == cell.baseline_freeze.selected_policy_id
    assert (receipt.cell_id, receipt.cell_content_sha256) == (
        cell.cell_id,
        cell.content_sha256,
    )
    assert receipt.baseline_costs == cell.baseline.final_costs
    assert receipt.full_future_costs == cell.full_future.final_costs
    assert receipt.known_only_costs == cell.known_only.final_costs
    assert (
        receipt.baseline_cost,
        receipt.full_future_cost,
        receipt.known_only_cost,
    ) == ("100.000000", "97.000000", "99.000000")
    assert receipt.full_future_savings_percent == "3.000000000000"
    assert receipt.unknown_future_contribution_points == "2.000000000000"
    assert receipt.known_only_causal_savings_percent == "1.000000000000"
    assert receipt.baseline_visibility == "released_only"
    assert receipt.full_future_visibility == "full_future"
    assert receipt.known_only_visibility == "known_only"
    assert receipt.arm_event_counts == (24, 24, 24)
    assert receipt.arm_material_shard_counts == (1, 1, 1)
    assert receipt.all_arm_exact_event_censuses is True
    assert receipt.candidate_action_parity_revalidated is True
    assert receipt.common_compute_and_tie_revalidated is True
    assert receipt.source_lineage == "repaired_runtime"
    assert receipt.sidecar_name.startswith(
        "m11-gate3-central-cell-" + cell.content_sha256.removeprefix("sha256:")
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("known_only_causal_savings_percent", "1.000000000001", "metrics"),
        ("baseline_cost", "101.000000", "complete ledgers"),
        ("cell_id", "yfm11g3cell-" + "f" * 24, "full cell identity"),
        (
            "decision_addendum_id",
            "yfm11econdec-" + "f" * 24,
            "decision addendum",
        ),
        ("arm_event_counts", (24, 24, 23), "event census"),
    ),
)
def test_resigned_compact_contradictions_are_rejected(
    field: str,
    value: object,
    message: str,
) -> None:
    store = _store()
    receipt = store.build_gate3_central_cell_receipt(
        _central_cell(),
        decision_addendum=_addendum(),
    )
    payload = receipt.model_dump(mode="python", round_trip=True)
    payload[field] = value
    _resign_receipt(payload)

    with pytest.raises(ValidationError, match=message):
        store.Gate3CentralCellReceipt.model_validate(payload, strict=True)


def test_receipt_identity_and_policy_visibility_are_revalidated() -> None:
    store = _store()
    receipt = store.build_gate3_central_cell_receipt(
        _central_cell(),
        decision_addendum=_addendum(),
    )
    with pytest.raises(ValidationError, match="identity"):
        store.Gate3CentralCellReceipt.model_validate(
            receipt.model_copy(update={"receipt_id": "yfm11g3cellrcpt-" + "f" * 24}),
            strict=True,
        )

    payload = receipt.model_dump(mode="python", round_trip=True)
    payload["baseline_visibility"] = "known_only"
    _resign_receipt(payload)
    with pytest.raises(ValidationError, match="baseline visibility"):
        store.Gate3CentralCellReceipt.model_validate(payload, strict=True)


def test_resigned_compact_receipt_requires_positive_baseline_cost() -> None:
    store = _store()
    receipt = store.build_gate3_central_cell_receipt(
        _central_cell(),
        decision_addendum=_addendum(),
    )
    payload = receipt.model_dump(mode="python", round_trip=True)
    payload["baseline_costs"] = _ledger("0.000000").model_dump(mode="python")
    payload["baseline_cost"] = "0.000000"
    _resign_receipt(payload)

    with pytest.raises(ValidationError, match="baseline cost must be positive"):
        store.Gate3CentralCellReceipt.model_validate(payload, strict=True)


def test_half_quantum_metric_uses_authoritative_half_even_rounding() -> None:
    store = _store()
    confirmation = _confirmation()
    cell = confirmation.build_gate3_stream_cell(
        roots=_roots(),
        baseline_freeze=_baseline_freeze(),
        baseline=_arm(arm="B", purchase="200000000.000000"),
        full_future=_arm(arm="F", purchase="199999999.999999"),
        known_only=_arm(arm="K", purchase="199999999.999999"),
    )
    assert cell.full_future_savings_percent == "0.000000000000"

    receipt = store.build_gate3_central_cell_receipt(
        cell,
        decision_addendum=_addendum(),
    )
    assert receipt.full_future_savings_percent == "0.000000000000"
    assert receipt.known_only_causal_savings_percent == "0.000000000000"


def test_build_revalidates_malformed_full_cell_and_addendum() -> None:
    store = _store()
    cell = _central_cell()
    addendum = _addendum()

    with pytest.raises(store.Gate3CentralEvidenceError, match="full cell"):
        store.build_gate3_central_cell_receipt(
            cell.model_copy(update={"full_future_cost": "96.000000"}),
            decision_addendum=addendum,
        )
    with pytest.raises(store.Gate3CentralEvidenceError, match="addendum"):
        store.build_gate3_central_cell_receipt(
            cell,
            decision_addendum=addendum.model_copy(
                update={"addendum_id": "yfm11econdec-" + "f" * 24}
            ),
        )
    malformed_ledger = cell.baseline.final_costs.model_copy(
        update={"purchase_cost": "9" * 10_000 + ".000000"}
    )
    malformed_arm = cell.baseline.model_copy(update={"final_costs": malformed_ledger})
    with pytest.raises(store.Gate3CentralEvidenceError, match="full cell"):
        store.build_gate3_central_cell_receipt(
            cell.model_copy(update={"baseline": malformed_arm}),
            decision_addendum=addendum,
        )


def test_compact_receipt_bounds_imported_cost_strings_and_material_census() -> None:
    store = _store()
    receipt = store.build_gate3_central_cell_receipt(
        _central_cell(),
        decision_addendum=_addendum(),
    )
    oversized = receipt.model_dump(mode="python", round_trip=True)
    oversized["baseline_costs"]["purchase_cost"] = "9" * 10_000 + ".000000"
    with pytest.raises(ValidationError, match="string bound"):
        store.Gate3CentralCellReceipt.model_validate(oversized, strict=True)

    impossible_census = receipt.model_dump(mode="python", round_trip=True)
    impossible_census["arm_material_shard_counts"] = (25, 1, 1)
    _resign_receipt(impossible_census)
    with pytest.raises(ValidationError, match="material census"):
        store.Gate3CentralCellReceipt.model_validate(impossible_census, strict=True)


def test_deterministic_gzip_has_frozen_header_and_round_trips() -> None:
    store = _store()
    raw = b'{"central": "evidence"}\n' * 32

    first = store.deterministic_gzip(raw)
    second = store.deterministic_gzip(raw)

    assert first == second
    assert first[:3] == b"\x1f\x8b\x08"
    assert first[3] == 0
    assert first[4:8] == b"\x00\x00\x00\x00"
    assert gzip.decompress(first) == raw


def test_publish_is_immutable_idempotent_and_loads_exact_full_cell(
    tmp_path: Path,
) -> None:
    store = _store()
    cell, first_path, first_receipt = _publish(tmp_path)
    second_path, second_receipt = store.publish_gate3_central_cell_evidence(
        tmp_path,
        cell,
        decision_addendum=_addendum(),
    )

    assert first_path == second_path == tmp_path / first_receipt.sidecar_name
    assert first_receipt == second_receipt
    assert first_receipt.compressed_byte_count == first_path.stat().st_size
    assert _load(first_path, cell, first_receipt) == cell


def test_preparation_retains_only_transport_and_compact_receipt() -> None:
    store = _store()
    cell = _central_cell()

    prepared = store._prepare_central_cell_evidence(
        cell,
        decision_addendum=_addendum(),
    )

    assert set(prepared.__dataclass_fields__) == {"compressed_bytes", "receipt"}
    assert not hasattr(prepared, "cell")
    assert not hasattr(prepared, "canonical_bytes")


def test_preparation_releases_detached_full_cell_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell = _central_cell()
    strict_validate = store._strict_cell_and_canonical
    detached_refs = []

    def capture_detached(value):  # type: ignore[no-untyped-def]
        detached, canonical = strict_validate(value)
        detached_refs.append(weakref.ref(detached))
        return detached, canonical

    monkeypatch.setattr(store, "_strict_cell_and_canonical", capture_detached)
    prepared = store._prepare_central_cell_evidence(
        cell,
        decision_addendum=_addendum(),
    )
    gc.collect()

    assert prepared.compressed_bytes
    assert detached_refs[0]() is None


def test_strict_preflight_rejects_oversize_forged_graph_before_model_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell = _central_cell()
    forged = cell.model_copy(update={"stream_id": "x" * 2048})
    addendum = _addendum()

    def reject_model_dump(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("preflight must reject before detaching the cell graph")

    monkeypatch.setattr(store, "_MAX_STRICT_CELL_BYTES", 1024, raising=False)
    monkeypatch.setattr(BaseModel, "model_dump", reject_model_dump)
    with pytest.raises(store.Gate3CentralEvidenceError, match="strict cell byte bound"):
        store.build_gate3_central_cell_receipt(
            forged,
            decision_addendum=addendum,
        )


def test_strict_preflight_exact_size_accepts_complete_cell() -> None:
    store = _store()
    cell = _central_cell()
    canonical = store.canonical_gate3_central_cell_bytes(cell)

    assert store._bounded_existing_canonical_size(
        cell,
        maximum_bytes=len(canonical),
    ) == len(canonical)


def test_publication_streams_full_cell_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell = _central_cell()
    iterate = store._iter_canonical_json_bytes
    calls = 0

    def counted_chunks(value):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        yield from iterate(value)

    monkeypatch.setattr(store, "_iter_canonical_json_bytes", counted_chunks)
    store.publish_gate3_central_cell_evidence(
        tmp_path,
        cell,
        decision_addendum=_addendum(),
    )
    assert calls == 1


def test_preparation_avoids_unbounded_contract_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def reject_materialization(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("prepared central evidence must stream canonical bytes")

    monkeypatch.setattr(store, "deterministic_gzip", reject_materialization)
    receipt = store.build_gate3_central_cell_receipt(
        _central_cell(),
        decision_addendum=_addendum(),
    )
    assert receipt.uncompressed_byte_count > 0


def test_canonical_iterator_matches_contract_without_model_dump_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell = _central_cell()
    expected = store.canonical_gate3_central_cell_bytes(cell)

    def reject_model_dump(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("canonical iterator must traverse the existing graph")

    monkeypatch.setattr(BaseModel, "model_dump", reject_model_dump)
    assert b"".join(store._iter_canonical_json_bytes(cell)) == expected


def test_streaming_stops_at_raw_and_compressed_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell = _central_cell()
    consumed = []

    def oversized(_value):  # type: ignore[no-untyped-def]
        consumed.append("oversize")
        yield b"x" * 11
        consumed.append("forbidden")
        raise AssertionError("serializer consumed after raw cap")

    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", 10)
    monkeypatch.setattr(store, "_iter_canonical_json_bytes", oversized)
    with pytest.raises(store.Gate3CentralEvidenceError, match="uncompressed byte bound"):
        store._stream_canonical_gzip(cell)
    assert consumed == ["oversize"]

    total_chunks = 256
    compressed_consumed = 0

    def incompressible(_value):  # type: ignore[no-untyped-def]
        nonlocal compressed_consumed
        for index in range(total_chunks):
            compressed_consumed += 1
            yield hashlib.shake_256(str(index).encode()).digest(4096)

    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", 1024)
    monkeypatch.setattr(store, "_iter_canonical_json_bytes", incompressible)
    with pytest.raises(store.Gate3CentralEvidenceError, match="compressed byte bound"):
        store._stream_canonical_gzip(cell)
    assert compressed_consumed < total_chunks


def test_central_store_freezes_materially_bounded_peak_contract() -> None:
    store = _store()
    assert store._MAX_STRICT_CELL_BYTES == 256 * 1024 * 1024
    assert store._MAX_UNCOMPRESSED_BYTES == 256 * 1024 * 1024
    assert store._MAX_COMPRESSED_BYTES == 64 * 1024 * 1024


def test_publish_refuses_to_replace_foreign_bytes(tmp_path: Path) -> None:
    store = _store()
    cell = _central_cell()
    receipt = store.build_gate3_central_cell_receipt(
        cell,
        decision_addendum=_addendum(),
    )
    destination = tmp_path / receipt.sidecar_name
    destination.write_bytes(b"foreign immutable bytes")

    with pytest.raises(store.Gate3CentralEvidenceError, match="immutable publication"):
        store.publish_gate3_central_cell_evidence(
            tmp_path,
            cell,
            decision_addendum=_addendum(),
        )
    assert destination.read_bytes() == b"foreign immutable bytes"


def test_loader_rejects_receipt_tamper_and_expected_mismatch_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    forged_receipt = receipt.model_copy(
        update={"known_only_causal_savings_percent": "9.000000000000"}
    )
    with pytest.raises(store.Gate3CentralEvidenceError, match="strict validation"):
        _load(published, cell, forged_receipt)

    reads = []

    def reject_read(path):  # type: ignore[no-untyped-def]
        reads.append(path)
        raise AssertionError("expected mismatch must fail before file I/O")

    monkeypatch.setattr(store, "_read_bounded_regular_file", reject_read)
    wrong_expected = cell.model_copy(update={"stream_id": "wrong-stream"})
    with pytest.raises(store.Gate3CentralEvidenceError, match="expected compact values"):
        _load(published, wrong_expected, receipt)
    assert reads == []


def test_loader_accepts_distinct_bound_gzip_transport_without_recompression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    alternate = bytearray(published.read_bytes())
    alternate[9] = 3 if alternate[9] != 3 else 255
    alternate_bytes = bytes(alternate)
    alternate_receipt = _resign_transport(receipt, alternate_bytes)
    alternate_path = tmp_path / "alternate" / alternate_receipt.sidecar_name
    alternate_path.parent.mkdir()
    alternate_path.write_bytes(alternate_bytes)

    def reject_recompression(_data):  # type: ignore[no-untyped-def]
        raise AssertionError("loader must not recreate historical gzip transport")

    monkeypatch.setattr(store, "deterministic_gzip", reject_recompression)
    assert gzip.decompress(alternate_bytes) == gzip.decompress(published.read_bytes())
    assert _load(alternate_path, cell, alternate_receipt) == cell


def test_loader_rejects_resigned_noncanonical_gzip_header(tmp_path: Path) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    mutated = bytearray(published.read_bytes())
    mutated[4] = 1
    transport = bytes(mutated)
    resigned = _resign_transport(receipt, transport)
    candidate = tmp_path / "header" / resigned.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)

    with pytest.raises(store.Gate3CentralEvidenceError, match="gzip header"):
        _load(candidate, cell, resigned)


def test_loader_rejects_transport_tamper_and_bounded_decompression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    tampered = bytearray(published.read_bytes())
    tampered[-1] ^= 1
    candidate = tmp_path / "tampered" / receipt.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(tampered)
    with pytest.raises(store.Gate3CentralEvidenceError, match="compressed raw hash"):
        _load(candidate, cell, receipt)

    compressed_limit = store._MAX_COMPRESSED_BYTES
    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", receipt.compressed_byte_count - 1)
    with pytest.raises(store.Gate3CentralEvidenceError, match="bounded regular file"):
        _load(published, cell, receipt)

    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", compressed_limit)
    monkeypatch.setattr(
        store,
        "_MAX_UNCOMPRESSED_BYTES",
        receipt.uncompressed_byte_count - 1,
    )
    with pytest.raises(store.Gate3CentralEvidenceError, match="uncompressed byte bound"):
        _load(published, cell, receipt)


@pytest.mark.parametrize("mutation", ("duplicate", "nonfinite", "noncanonical"))
def test_loader_rejects_duplicate_nonfinite_and_noncanonical_json(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    raw = gzip.decompress(published.read_bytes())
    if mutation == "duplicate":
        raw = raw.replace(
            b'{\n  "baseline"',
            b'{\n  "cell_id": "' + cell.cell_id.encode() + b'",\n  "baseline"',
            1,
        )
        expected = "duplicate"
    elif mutation == "nonfinite":
        raw = raw.replace(
            b'"candidate_action_parity_revalidated": true',
            b'"candidate_action_parity_revalidated": NaN',
            1,
        )
        expected = "non-finite"
    else:
        parsed = json.loads(raw)
        raw = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        expected = "canonical"
    transport = store.deterministic_gzip(raw)
    resigned = _resign_transport(receipt, transport, raw)
    candidate = tmp_path / mutation / resigned.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)

    with pytest.raises(store.Gate3CentralEvidenceError, match=expected):
        _load(candidate, cell, resigned)


def test_loader_rejects_symlink_directory_and_nonregular_open_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    symlink = tmp_path / "link" / receipt.sidecar_name
    symlink.parent.mkdir()
    symlink.symlink_to(published)
    directory = tmp_path / "directory" / receipt.sidecar_name
    directory.mkdir(parents=True)
    for candidate in (symlink, directory):
        with pytest.raises(store.Gate3CentralEvidenceError, match="bounded regular file"):
            _load(candidate, cell, receipt)

    read_descriptor, write_descriptor = os.pipe()
    os.set_blocking(read_descriptor, False)
    seen_flags = []

    def substitute_open(path, flags):  # type: ignore[no-untyped-def]
        seen_flags.append(flags)
        return read_descriptor

    monkeypatch.setattr(store.os, "open", substitute_open)
    try:
        with pytest.raises(store.Gate3CentralEvidenceError, match="bounded regular file"):
            _load(published, cell, receipt)
    finally:
        os.close(write_descriptor)
    assert seen_flags[0] & os.O_NONBLOCK


def test_loader_rejects_regular_file_fingerprint_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    real_fstat = store.os.fstat
    calls = 0

    def changed_fstat(descriptor):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        result = real_fstat(descriptor)
        if calls == 2:
            values = list(result)
            values[9] += 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(store.os, "fstat", changed_fstat)
    with pytest.raises(store.Gate3CentralEvidenceError, match="changed during read-back"):
        _load(published, cell, receipt)


def test_receipt_and_preparation_enforce_exact_size_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell = _central_cell()
    canonical = store.canonical_gate3_central_cell_bytes(cell)
    compressed = store.deterministic_gzip(canonical)
    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", len(canonical))
    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", len(compressed))
    receipt = store.build_gate3_central_cell_receipt(
        cell,
        decision_addendum=_addendum(),
    )
    assert receipt.uncompressed_byte_count == len(canonical)
    assert receipt.compressed_byte_count == len(compressed)

    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", len(canonical) - 1)
    with pytest.raises(store.Gate3CentralEvidenceError, match="uncompressed byte bound"):
        store.build_gate3_central_cell_receipt(cell, decision_addendum=_addendum())
    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", len(canonical))
    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", len(compressed) - 1)
    with pytest.raises(store.Gate3CentralEvidenceError, match="compressed byte bound"):
        store.build_gate3_central_cell_receipt(cell, decision_addendum=_addendum())


def test_orphan_recovery_binds_actual_transport_and_exact_full_cell(
    tmp_path: Path,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    alternate = bytearray(published.read_bytes())
    alternate[9] = 3 if alternate[9] != 3 else 255
    transport = bytes(alternate)
    actual_receipt = _resign_transport(receipt, transport)
    orphan = tmp_path / "orphan" / actual_receipt.sidecar_name
    orphan.parent.mkdir()
    orphan.write_bytes(transport)

    recovered = store.recover_gate3_central_cell_receipt(
        orphan,
        decision_addendum=_addendum(),
        expected_roots=cell.roots,
        expected_corpus_id=cell.corpus_id,
        expected_stream_id=cell.stream_id,
        expected_regime=cell.regime,
        expected_baseline_freeze=cell.baseline_freeze,
    )
    assert recovered == actual_receipt
    assert recovered.compressed_raw_sha256 != receipt.compressed_raw_sha256


def test_orphan_recovery_rejects_wrong_filename_and_binding(tmp_path: Path) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    wrong_name = tmp_path / "wrong" / ("x" + receipt.sidecar_name)
    wrong_name.parent.mkdir()
    wrong_name.write_bytes(published.read_bytes())
    with pytest.raises(store.Gate3CentralEvidenceError, match="filename"):
        store.recover_gate3_central_cell_receipt(
            wrong_name,
            decision_addendum=_addendum(),
            expected_roots=cell.roots,
            expected_corpus_id=cell.corpus_id,
            expected_stream_id=cell.stream_id,
            expected_regime=cell.regime,
            expected_baseline_freeze=cell.baseline_freeze,
        )
    with pytest.raises(store.Gate3CentralEvidenceError, match="expected bindings"):
        store.recover_gate3_central_cell_receipt(
            published,
            decision_addendum=_addendum(),
            expected_roots=cell.roots,
            expected_corpus_id=cell.corpus_id,
            expected_stream_id="wrong-stream",
            expected_regime=cell.regime,
            expected_baseline_freeze=cell.baseline_freeze,
        )


def test_recovery_enforces_strict_cell_cap_before_json_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    parsed = []

    def reject_parse(_raw):  # type: ignore[no-untyped-def]
        parsed.append(True)
        raise AssertionError("strict cap must stop decompression before JSON parsing")

    monkeypatch.setattr(
        store,
        "_MAX_STRICT_CELL_BYTES",
        receipt.uncompressed_byte_count - 1,
        raising=False,
    )
    monkeypatch.setattr(store, "_parse_strict_cell_json", reject_parse)
    with pytest.raises(store.Gate3CentralEvidenceError, match="strict cell byte bound"):
        store.recover_gate3_central_cell_receipt(
            published,
            decision_addendum=_addendum(),
            expected_roots=cell.roots,
            expected_corpus_id=cell.corpus_id,
            expected_stream_id=cell.stream_id,
            expected_regime=cell.regime,
            expected_baseline_freeze=cell.baseline_freeze,
        )
    assert parsed == []


def test_builder_and_expected_freeze_normalize_decimal_failures(tmp_path: Path) -> None:
    store = _store()
    cell, published, _receipt = _publish(tmp_path)
    large_cost = f"{'9' * 57}.000000"
    malformed_ledger = cell.baseline.final_costs.model_copy(update={"purchase_cost": large_cost})
    malformed_arm = cell.baseline.model_copy(update={"final_costs": malformed_ledger})
    with pytest.raises(store.Gate3CentralEvidenceError, match="strict validation"):
        store.build_gate3_central_cell_receipt(
            cell.model_copy(update={"baseline": malformed_arm}),
            decision_addendum=_addendum(),
        )

    score = cell.baseline_freeze.policy_scores[0].model_copy(update={"total_cost": large_cost})
    malformed_freeze = cell.baseline_freeze.model_copy(
        update={
            "policy_scores": (
                score,
                *cell.baseline_freeze.policy_scores[1:],
            )
        }
    )
    with pytest.raises(
        store.Gate3CentralEvidenceError,
        match="baseline freeze failed strict validation",
    ):
        store.recover_gate3_central_cell_receipt(
            published,
            decision_addendum=_addendum(),
            expected_roots=cell.roots,
            expected_corpus_id=cell.corpus_id,
            expected_stream_id=cell.stream_id,
            expected_regime=cell.regime,
            expected_baseline_freeze=malformed_freeze,
        )


def test_load_and_recover_normalize_decimal_failure_in_sidecar(tmp_path: Path) -> None:
    store = _store()
    cell, published, receipt = _publish(tmp_path)
    payload = json.loads(gzip.decompress(published.read_bytes()))
    payload["baseline"]["final_costs"]["purchase_cost"] = f"{'9' * 57}.000000"
    raw = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    transport = store.deterministic_gzip(raw)
    resigned = _resign_transport(receipt, transport, raw)
    candidate = tmp_path / "decimal-failure" / resigned.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)

    with pytest.raises(store.Gate3CentralEvidenceError, match="semantic validation"):
        _load(candidate, cell, resigned)
    with pytest.raises(store.Gate3CentralEvidenceError, match="semantic validation"):
        store.recover_gate3_central_cell_receipt(
            candidate,
            decision_addendum=_addendum(),
            expected_roots=cell.roots,
            expected_corpus_id=cell.corpus_id,
            expected_stream_id=cell.stream_id,
            expected_regime=cell.regime,
            expected_baseline_freeze=cell.baseline_freeze,
        )


def test_load_and_recover_normalize_deep_json_recursion(tmp_path: Path) -> None:
    store = _store()
    cell, _published, receipt = _publish(tmp_path)
    depth = 5000
    raw = b"[" * depth + b"0" + b"]" * depth + b"\n"
    assert len(raw) < 200 * 1024
    transport = store.deterministic_gzip(raw)
    resigned = _resign_transport(receipt, transport, raw)
    candidate = tmp_path / "deep-json" / resigned.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)

    with pytest.raises(store.Gate3CentralEvidenceError, match="semantic validation"):
        _load(candidate, cell, resigned)
    with pytest.raises(store.Gate3CentralEvidenceError, match="semantic validation"):
        store.recover_gate3_central_cell_receipt(
            candidate,
            decision_addendum=_addendum(),
            expected_roots=cell.roots,
            expected_corpus_id=cell.corpus_id,
            expected_stream_id=cell.stream_id,
            expected_regime=cell.regime,
            expected_baseline_freeze=cell.baseline_freeze,
        )
