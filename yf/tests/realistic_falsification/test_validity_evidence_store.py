from __future__ import annotations

import gc
import gzip
import hashlib
import importlib
import json
import os
import weakref
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from tests.realistic_falsification.test_confirmation import (
    _baseline_freeze,
    _exact_audits,
    _hard_null_controls,
    _roots,
    _twin_controls,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.confirmation import evaluate_gate3_validity_controls


def _store():
    return importlib.import_module("yieldforge.realistic_falsification.validity_evidence_store")


@pytest.fixture(scope="module")
def validity_case():  # type: ignore[no-untyped-def]
    freezes = (
        _baseline_freeze(corpus_id="lectra-m3-m4"),
        _baseline_freeze(corpus_id="loco-2dics"),
    )
    receipt = evaluate_gate3_validity_controls(
        roots=_roots(),
        hard_nulls=_hard_null_controls(baseline_freezes=freezes),
        twin_controls=(
            _twin_controls(corpus_id="lectra-m3-m4", savings="0.000000")
            + _twin_controls(corpus_id="loco-2dics", savings="0.000000")
        ),
        exact_audits=_exact_audits(baseline_freezes=freezes),
    )
    return freezes, receipt


def test_compact_validity_receipt_exactly_rederives_full_control_census(
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case

    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )

    assert receipt.roots == validity.roots
    assert tuple(item.corpus_id for item in receipt.baseline_freezes) == (
        "lectra-m3-m4",
        "loco-2dics",
    )
    assert tuple(item.freeze_id for item in receipt.baseline_freezes) == tuple(
        item.freeze_id for item in freezes
    )
    assert tuple(item.freeze_content_sha256 for item in receipt.baseline_freezes) == tuple(
        item.content_sha256 for item in freezes
    )
    assert tuple(item.selected_policy_id for item in receipt.baseline_freezes) == tuple(
        item.selected_policy_id for item in freezes
    )
    assert receipt.validity_receipt_id == validity.receipt_id
    assert receipt.validity_receipt_content_sha256 == validity.content_sha256
    assert len(receipt.hard_nulls) == 6
    assert len(receipt.twin_controls) == 40
    assert len(receipt.exact_audits) == 12
    assert len(receipt.no_signal_summaries) == 2
    assert "passes" not in store.Gate3ValidityTwinControlRow.model_fields
    assert receipt.failure_codes == validity.failure_codes
    assert receipt.diagnosis_codes == validity.diagnosis_codes
    assert receipt.status == validity.status
    assert receipt.exact_control_census is True
    assert receipt.raw_controls_revalidated is True
    assert receipt.source_lineage == "repaired_runtime"
    assert receipt.compression == "gzip-level-6-mtime-0-flags-0"

    hard_null = validity.hard_nulls[0]
    compact_null = receipt.hard_nulls[0]
    assert compact_null.registration_id == hard_null.registration.null_id
    assert compact_null.registration_content_sha256 == hard_null.registration.content_sha256
    assert compact_null.control_trace_id == hard_null.control_trace_id
    assert compact_null.control_content_sha256 == hard_null.content_sha256
    assert compact_null.corpus_id == hard_null.corpus_id
    assert compact_null.null_kind == hard_null.null_kind
    assert (
        compact_null.maximum_absolute_cost_difference == hard_null.maximum_absolute_cost_difference
    )
    assert compact_null.passes is hard_null.passes

    twin = validity.twin_controls[0]
    compact_twin = receipt.twin_controls[0]
    assert compact_twin.control_id == twin.control_id
    assert compact_twin.control_content_sha256 == twin.content_sha256
    assert compact_twin.source_stream_id == twin.source_stream_id
    assert compact_twin.twin_stream_id == twin.twin_stream_id
    assert compact_twin.twin_cell_id == twin.twin_cell_id
    assert compact_twin.twin_cell_content_sha256 == twin.twin_cell_content_sha256
    assert compact_twin.corpus_id == twin.corpus_id
    assert compact_twin.baseline_cost == twin.baseline_cost
    assert compact_twin.full_future_cost == twin.full_future_cost
    assert compact_twin.known_only_cost == twin.known_only_cost
    assert compact_twin.no_signal_savings_percent == twin.no_signal_savings_percent

    audit = validity.exact_audits[0]
    compact_audit = receipt.exact_audits[0]
    assert compact_audit.registration_id == audit.registration.audit_id
    assert compact_audit.registration_content_sha256 == audit.registration.content_sha256
    assert compact_audit.trace_id == audit.trace_id
    assert compact_audit.trace_content_sha256 == audit.content_sha256
    assert compact_audit.corpus_id == audit.corpus_id
    assert compact_audit.audit_ordinal == audit.registration.audit_ordinal
    assert compact_audit.economic_arm == audit.economic_arm
    assert compact_audit.selected_action_id == audit.selected_action_id
    assert compact_audit.passes is audit.passes


def test_validity_evidence_receipt_identity_rejects_compact_mutation(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    forged_row = receipt.twin_controls[0].model_copy(update={"known_only_cost": "999.000000"})
    forged = receipt.model_copy(update={"twin_controls": (forged_row, *receipt.twin_controls[1:])})

    with pytest.raises(ValidationError, match="identity"):
        store.Gate3ValidityEvidenceReceipt.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )


def test_validity_evidence_receipt_rejects_duplicate_compact_id(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    duplicated = receipt.model_copy(
        update={
            "twin_controls": (
                receipt.twin_controls[0],
                receipt.twin_controls[0],
                *receipt.twin_controls[2:],
            )
        }
    )

    with pytest.raises(ValidationError, match="unique"):
        store.Gate3ValidityEvidenceReceipt.model_validate(
            duplicated.model_dump(mode="python", round_trip=True),
            strict=True,
        )


def test_resigned_hard_null_cannot_contradict_frozen_tolerance(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    first = receipt.hard_nulls[0]
    contradicted = first.model_copy(update={"passes": False})
    failure_code = f"hard_null:{first.registration_id}"

    with pytest.raises(ValidationError, match="hard-null tolerance"):
        _resign_evidence_receipt(
            receipt,
            hard_nulls=(contradicted, *receipt.hard_nulls[1:]),
            failure_codes=(failure_code,),
            status="invalid",
        )


def test_resigned_twin_requires_positive_baseline_and_exact_metric(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    first = receipt.twin_controls[0]

    for update, match in (
        ({"baseline_cost": "0.000000"}, "positive baseline"),
        ({"full_future_cost": "99.000000"}, "savings does not reconcile"),
    ):
        contradicted = first.model_copy(update=update)
        with pytest.raises(ValidationError, match=match):
            _resign_evidence_receipt(
                receipt,
                twin_controls=(contradicted, *receipt.twin_controls[1:]),
            )


def test_resigned_twin_row_identity_is_rederived_from_roots(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    first = receipt.twin_controls[0]
    contradicted = first.model_copy(update={"known_only_cost": "999.000000"})

    with pytest.raises(ValidationError, match="twin identity"):
        _resign_evidence_receipt(
            receipt,
            twin_controls=(contradicted, *receipt.twin_controls[1:]),
        )


def test_exact_audit_compact_pass_is_bound_to_exact_optimality(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )

    assert "selected_is_exact_optimal" in store.Gate3ValidityExactAuditRow.model_fields
    first = receipt.exact_audits[0]
    contradicted = first.model_copy(update={"passes": not first.selected_is_exact_optimal})
    failure_code = f"exact_audit:{first.registration_id}"
    with pytest.raises(ValidationError, match="exact-audit decision"):
        _resign_evidence_receipt(
            receipt,
            exact_audits=(contradicted, *receipt.exact_audits[1:]),
            failure_codes=(failure_code,),
            status="invalid",
        )


def test_resigned_status_and_summary_are_rederived_from_compact_rows(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )

    with pytest.raises(ValidationError, match="validity decision"):
        _resign_evidence_receipt(receipt, status="invalid")

    first_summary = receipt.no_signal_summaries[0]
    summary_semantic = first_summary.model_dump(
        mode="python",
        round_trip=True,
        exclude={"summary_id", "content_sha256"},
    )
    summary_semantic.update(
        mean_no_signal_savings_percent="0.300000000000",
        classification="diagnosis_required",
    )
    summary_digest = semantic_sha256(summary_semantic)
    contradicted_summary = type(first_summary)(
        summary_id=f"yfm11g3ns-{summary_digest[:24]}",
        content_sha256=f"sha256:{summary_digest}",
        **summary_semantic,
    )
    with pytest.raises(ValidationError, match="no-signal summary"):
        _resign_evidence_receipt(
            receipt,
            no_signal_summaries=(
                contradicted_summary,
                receipt.no_signal_summaries[1],
            ),
            diagnosis_codes=("no_signal:lectra-m3-m4",),
            status="diagnosis_required",
        )


def _resign_full_twin(control, **updates):  # type: ignore[no-untyped-def]
    semantic = control.model_dump(
        mode="python",
        round_trip=True,
        exclude={"control_id", "content_sha256"},
    )
    semantic.update(updates)
    digest = semantic_sha256(semantic)
    return type(control)(
        control_id=f"yfm11g3twin-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _resign_full_summary(summary, **updates):  # type: ignore[no-untyped-def]
    semantic = summary.model_dump(
        mode="python",
        round_trip=True,
        exclude={"summary_id", "content_sha256"},
    )
    semantic.update(updates)
    digest = semantic_sha256(semantic)
    return type(summary)(
        summary_id=f"yfm11g3ns-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def test_half_even_row_and_summary_boundaries_accept_authoritative_full_receipt(
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, valid = validity_case
    lectra = list(valid.twin_controls[:20])
    lectra[0] = _resign_full_twin(
        lectra[0],
        baseline_cost="200000000.000000",
        full_future_cost="199999999.999999",
        known_only_cost="200000000.000000",
        no_signal_savings_percent="0.000000000000",
    )
    for index in range(1, 11):
        lectra[index] = _resign_full_twin(
            lectra[index],
            baseline_cost="100000000.000000",
            full_future_cost="99999999.999999",
            known_only_cost="100000000.000000",
            no_signal_savings_percent="0.000000000001",
        )
    full = evaluate_gate3_validity_controls(
        roots=valid.roots,
        hard_nulls=valid.hard_nulls,
        twin_controls=tuple(lectra) + valid.twin_controls[20:],
        exact_audits=valid.exact_audits,
    )

    compact = store.build_gate3_validity_evidence_receipt(
        full,
        baseline_freezes=freezes,
    )

    assert full.twin_controls[0].no_signal_savings_percent == "0.000000000000"
    assert full.no_signal_summaries[0].mean_no_signal_savings_percent == ("0.000000000000")
    assert compact.twin_controls[0].no_signal_savings_percent == "0.000000000000"
    assert compact.no_signal_summaries[0] == full.no_signal_summaries[0]


def test_compact_receipt_bounds_imported_summary_strings(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    first_summary = receipt.no_signal_summaries[0]
    oversized = _resign_full_summary(
        first_summary,
        control_ids=("x" * 129, *first_summary.control_ids[1:]),
    )

    with pytest.raises(ValidationError, match="string bound"):
        _resign_evidence_receipt(
            receipt,
            no_signal_summaries=(oversized, receipt.no_signal_summaries[1]),
        )


def test_builder_normalizes_compact_validation_error(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case

    def invalid_compact_rows(_receipt):  # type: ignore[no-untyped-def]
        return (store.Gate3ValidityTwinControlRow(control_id="invalid"),)

    monkeypatch.setattr(store, "_compact_twins", invalid_compact_rows)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="compact validity receipt"):
        store.build_gate3_validity_evidence_receipt(
            validity,
            baseline_freezes=freezes,
        )


def test_compact_receipt_preserves_invalid_and_diagnosis_decisions(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, valid = validity_case
    invalid = evaluate_gate3_validity_controls(
        roots=valid.roots,
        hard_nulls=_hard_null_controls(
            fail_first=True,
            baseline_freezes=freezes,
        ),
        twin_controls=valid.twin_controls,
        exact_audits=valid.exact_audits,
    )
    diagnosis = evaluate_gate3_validity_controls(
        roots=valid.roots,
        hard_nulls=valid.hard_nulls,
        twin_controls=(
            _twin_controls(corpus_id="lectra-m3-m4", savings="0.300000") + valid.twin_controls[20:]
        ),
        exact_audits=valid.exact_audits,
    )

    invalid_compact = store.build_gate3_validity_evidence_receipt(
        invalid,
        baseline_freezes=freezes,
    )
    diagnosis_compact = store.build_gate3_validity_evidence_receipt(
        diagnosis,
        baseline_freezes=freezes,
    )

    assert invalid_compact.status == "invalid"
    assert invalid_compact.failure_codes == invalid.failure_codes
    assert diagnosis_compact.status == "diagnosis_required"
    assert diagnosis_compact.diagnosis_codes == diagnosis.diagnosis_codes


def _resign_evidence_receipt(receipt, **updates):  # type: ignore[no-untyped-def]
    store = _store()
    semantic = receipt.model_dump(
        mode="python",
        round_trip=True,
        exclude={"evidence_id", "content_sha256"},
    )
    semantic.update(updates)

    def json_value(value):  # type: ignore[no-untyped-def]
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {key: json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_value(item) for item in value]
        return value

    digest = semantic_sha256(json_value(semantic))
    return store.Gate3ValidityEvidenceReceipt(
        evidence_id=f"yfm11g3valrcpt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _resign_transport(receipt, transport: bytes, raw: bytes | None = None):  # type: ignore[no-untyped-def]
    compressed_hash = hashlib.sha256(transport).hexdigest()
    validity_hash = receipt.validity_receipt_content_sha256.removeprefix("sha256:")
    updates = {
        "compressed_raw_sha256": f"sha256:{compressed_hash}",
        "compressed_byte_count": len(transport),
        "sidecar_name": (f"m11-gate3-validity-receipt-{validity_hash}-{compressed_hash}.json.gz"),
    }
    if raw is not None:
        updates["uncompressed_byte_count"] = len(raw)
    return _resign_evidence_receipt(receipt, **updates)


def _publish_validity(tmp_path: Path, validity_case):  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    path, evidence = store.publish_gate3_validity_evidence(
        tmp_path,
        validity,
        baseline_freezes=freezes,
    )
    return freezes, validity, path, evidence


def _load_validity(path: Path, freezes, validity, evidence):  # type: ignore[no-untyped-def]
    store = _store()
    return store.load_gate3_validity_evidence(
        path,
        evidence_receipt=evidence,
        expected_roots=validity.roots,
        expected_baseline_freezes=freezes,
        expected_validity_receipt_id=validity.receipt_id,
        expected_validity_receipt_content_sha256=validity.content_sha256,
        expected_hard_nulls=evidence.hard_nulls,
        expected_twin_controls=evidence.twin_controls,
        expected_exact_audits=evidence.exact_audits,
        expected_no_signal_summaries=evidence.no_signal_summaries,
        expected_failure_codes=validity.failure_codes,
        expected_diagnosis_codes=validity.diagnosis_codes,
        expected_status=validity.status,
        expected_exact_control_census=True,
        expected_raw_controls_revalidated=True,
        expected_source_lineage="repaired_runtime",
    )


def test_validity_deterministic_gzip_has_frozen_header_and_round_trips() -> None:
    store = _store()
    raw = b'{"validity": "evidence"}\n' * 32

    first = store.deterministic_gzip(raw)
    second = store.deterministic_gzip(raw)

    assert first == second
    assert first[:3] == b"\x1f\x8b\x08"
    assert first[3] == 0
    assert first[4:8] == b"\x00\x00\x00\x00"
    assert gzip.decompress(first) == raw


def test_publish_is_immutable_idempotent_and_loads_exact_full_receipt(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, first_path, first_evidence = _publish_validity(
        tmp_path,
        validity_case,
    )
    second_path, second_evidence = store.publish_gate3_validity_evidence(
        tmp_path,
        validity,
        baseline_freezes=freezes,
    )

    assert first_path == second_path == tmp_path / first_evidence.sidecar_name
    assert first_evidence == second_evidence
    assert first_evidence.compressed_byte_count == first_path.stat().st_size
    assert _load_validity(first_path, freezes, validity, first_evidence) == validity


def test_recover_orphan_sidecar_reconstructs_exact_compact_receipt_and_releases_full_graph(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    parse = store._parse_strict_validity_json
    parsed_refs = []

    def capture_parsed(raw):  # type: ignore[no-untyped-def]
        parsed = parse(raw)
        parsed_refs.append(weakref.ref(parsed))
        return parsed

    monkeypatch.setattr(store, "_parse_strict_validity_json", capture_parsed)
    recovered = store.recover_gate3_validity_evidence_receipt(
        published,
        expected_roots=validity.roots,
        expected_baseline_freezes=freezes,
    )
    gc.collect()

    assert recovered == evidence
    assert parsed_refs[0]() is None


def test_recover_uses_actual_historical_transport_without_recompression(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    alternate = bytearray(published.read_bytes())
    alternate[9] = 3 if alternate[9] != 3 else 255
    alternate_bytes = bytes(alternate)
    alternate_receipt = _resign_transport(evidence, alternate_bytes)
    alternate_path = tmp_path / alternate_receipt.sidecar_name
    alternate_path.write_bytes(alternate_bytes)

    def reject_recompression(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("recovery must bind actual historical gzip bytes")

    monkeypatch.setattr(store, "_stream_canonical_gzip", reject_recompression)
    monkeypatch.setattr(store, "deterministic_gzip", reject_recompression)
    recovered = store.recover_gate3_validity_evidence_receipt(
        alternate_path,
        expected_roots=validity.roots,
        expected_baseline_freezes=freezes,
    )

    assert recovered == alternate_receipt
    assert recovered.compressed_raw_sha256 == (
        f"sha256:{hashlib.sha256(alternate_bytes).hexdigest()}"
    )


def test_recover_rejects_wrong_filename_tamper_and_freeze_substitution(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    wrong_name = tmp_path / "wrong-name.json.gz"
    wrong_name.write_bytes(published.read_bytes())
    with pytest.raises(store.Gate3ValidityEvidenceError, match="filename"):
        store.recover_gate3_validity_evidence_receipt(
            wrong_name,
            expected_roots=validity.roots,
            expected_baseline_freezes=freezes,
        )

    tampered = bytearray(published.read_bytes())
    tampered[-1] ^= 1
    tampered_directory = tmp_path / "tampered-recovery"
    tampered_directory.mkdir()
    tampered_path = tampered_directory / evidence.sidecar_name
    tampered_path.write_bytes(tampered)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="not valid gzip"):
        store.recover_gate3_validity_evidence_receipt(
            tampered_path,
            expected_roots=validity.roots,
            expected_baseline_freezes=freezes,
        )

    substituted_freezes = (
        _baseline_freeze(
            corpus_id="lectra-m3-m4",
            selected_policy_id="remnant_first",
        ),
        freezes[1],
    )
    with pytest.raises(store.Gate3ValidityEvidenceError, match="supplied baseline freezes"):
        store.recover_gate3_validity_evidence_receipt(
            published,
            expected_roots=validity.roots,
            expected_baseline_freezes=substituted_freezes,
        )


def test_publication_streams_full_receipt_exactly_once(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    iterate = store._iter_canonical_json_bytes
    calls = 0

    def counted_chunks(value):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        yield from iterate(value)

    monkeypatch.setattr(store, "_iter_canonical_json_bytes", counted_chunks)
    store.publish_gate3_validity_evidence(
        tmp_path,
        validity,
        baseline_freezes=freezes,
    )

    assert calls == 1


def test_preparation_retains_only_transport_and_compact_receipt(validity_case) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case

    prepared = store._prepare_validity_evidence(
        validity,
        baseline_freezes=freezes,
    )

    assert set(prepared.__dataclass_fields__) == {
        "compressed_bytes",
        "evidence_receipt",
    }
    assert not hasattr(prepared, "validity_receipt")
    assert not hasattr(prepared, "canonical_bytes")


def test_preparation_releases_detached_full_receipt_graph(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    strict_validate = store._strict_validity_receipt_and_canonical
    detached_refs = []

    def capture_detached(value):  # type: ignore[no-untyped-def]
        detached, canonical = strict_validate(value)
        detached_refs.append(weakref.ref(detached))
        return detached, canonical

    monkeypatch.setattr(store, "_strict_validity_receipt_and_canonical", capture_detached)
    prepared = store._prepare_validity_evidence(
        validity,
        baseline_freezes=freezes,
    )
    gc.collect()

    assert prepared.compressed_bytes
    assert detached_refs[0]() is None


def test_preparation_streams_canonical_encoding_and_stops_at_bound(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    consumed = []

    def bounded_chunks(_value):  # type: ignore[no-untyped-def]
        consumed.append("oversize")
        yield b"x" * 11
        consumed.append("forbidden-tail")
        raise AssertionError("serializer consumed beyond the first oversize chunk")

    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", 10)
    monkeypatch.setattr(store, "_iter_canonical_json_bytes", bounded_chunks)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="uncompressed byte bound"):
        store.build_gate3_validity_evidence_receipt(
            validity,
            baseline_freezes=freezes,
        )
    assert consumed == ["oversize"]


def test_canonical_iterator_traverses_models_without_model_dump_graph(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    _, validity = validity_case
    expected = store.canonical_gate3_validity_receipt_bytes(validity)

    def reject_model_dump(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("canonical iterator must traverse the existing graph")

    monkeypatch.setattr(BaseModel, "model_dump", reject_model_dump)
    assert b"".join(store._iter_canonical_json_bytes(validity)) == expected


def test_canonical_iterator_matches_pydantic_json_temporal_scalars() -> None:
    store = _store()

    class TemporalPayload(BaseModel):
        occurred_at: datetime
        calendar_date: date
        clock_time: time

    payload = TemporalPayload(
        occurred_at=datetime(2026, 8, 30, 12, 34, 56, 789012, tzinfo=UTC),
        calendar_date=date(2026, 8, 30),
        clock_time=time(12, 34, 56, 789012, tzinfo=UTC),
    )
    expected = (
        json.dumps(
            payload.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )

    assert b"".join(store._iter_canonical_json_bytes(payload)) == expected


def test_compressed_sink_stops_before_consuming_incompressible_tail(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    _, validity = validity_case
    consumed = 0
    total_chunks = 256

    def incompressible_chunks(_value):  # type: ignore[no-untyped-def]
        nonlocal consumed
        for index in range(total_chunks):
            consumed += 1
            yield hashlib.shake_256(str(index).encode()).digest(4096)

    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", 1024)
    monkeypatch.setattr(store, "_iter_canonical_json_bytes", incompressible_chunks)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="compressed byte bound"):
        store._stream_canonical_gzip(validity)
    assert consumed < total_chunks


def test_preparation_uses_bounded_internal_serializer(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case

    def reject_materialization(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("prepared evidence must stream canonical bytes")

    monkeypatch.setattr(store, "deterministic_gzip", reject_materialization)
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    assert receipt.uncompressed_byte_count > 0


def test_publish_refuses_to_replace_foreign_bytes(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    destination = tmp_path / receipt.sidecar_name
    destination.write_bytes(b"foreign immutable bytes")

    with pytest.raises(store.Gate3ValidityEvidenceError, match="immutable publication"):
        store.publish_gate3_validity_evidence(
            tmp_path,
            validity,
            baseline_freezes=freezes,
        )
    assert destination.read_bytes() == b"foreign immutable bytes"


def test_loader_rejects_receipt_tamper_and_expected_mismatch_before_io(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    forged = evidence.model_copy(update={"status": "invalid"})
    with pytest.raises(store.Gate3ValidityEvidenceError, match="strict validation"):
        _load_validity(published, freezes, validity, forged)

    reads = []

    def reject_read(path):  # type: ignore[no-untyped-def]
        reads.append(path)
        raise AssertionError("expected mismatch must fail before file I/O")

    monkeypatch.setattr(store, "_read_bounded_regular_file", reject_read)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="expected compact values"):
        store.load_gate3_validity_evidence(
            published,
            evidence_receipt=evidence,
            expected_roots=validity.roots,
            expected_baseline_freezes=freezes,
            expected_validity_receipt_id=validity.receipt_id,
            expected_validity_receipt_content_sha256=validity.content_sha256,
            expected_hard_nulls=evidence.hard_nulls,
            expected_twin_controls=evidence.twin_controls,
            expected_exact_audits=evidence.exact_audits,
            expected_no_signal_summaries=evidence.no_signal_summaries,
            expected_failure_codes=validity.failure_codes,
            expected_diagnosis_codes=validity.diagnosis_codes,
            expected_status="invalid",
            expected_exact_control_census=True,
            expected_raw_controls_revalidated=True,
            expected_source_lineage="repaired_runtime",
        )
    assert reads == []


def test_expected_code_bounds_fail_as_public_store_error_before_io(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)

    def reject_read(_path):  # type: ignore[no-untyped-def]
        raise AssertionError("malformed expected values must fail before I/O")

    monkeypatch.setattr(store, "_read_bounded_regular_file", reject_read)
    common = {
        "evidence_receipt": evidence,
        "expected_roots": validity.roots,
        "expected_baseline_freezes": freezes,
        "expected_validity_receipt_id": validity.receipt_id,
        "expected_validity_receipt_content_sha256": validity.content_sha256,
        "expected_hard_nulls": evidence.hard_nulls,
        "expected_twin_controls": evidence.twin_controls,
        "expected_exact_audits": evidence.exact_audits,
        "expected_no_signal_summaries": evidence.no_signal_summaries,
        "expected_status": validity.status,
        "expected_exact_control_census": True,
        "expected_raw_controls_revalidated": True,
        "expected_source_lineage": "repaired_runtime",
    }
    for failures, diagnoses in (
        (tuple("x" for _ in range(21)), ()),
        ((), ("x", "y", "z")),
        (("x" * 129,), ()),
        (("",), ()),
        ((), ("x" * 129,)),
    ):
        with pytest.raises(
            store.Gate3ValidityEvidenceError,
            match="expected compact values",
        ):
            store.load_gate3_validity_evidence(
                published,
                **common,
                expected_failure_codes=failures,
                expected_diagnosis_codes=diagnoses,
            )

    oversized_summary = _resign_full_summary(
        evidence.no_signal_summaries[0],
        control_ids=("x" * 129, *evidence.no_signal_summaries[0].control_ids[1:]),
    )
    with pytest.raises(
        store.Gate3ValidityEvidenceError,
        match="expected no-signal summaries",
    ):
        summary_common = {
            **common,
            "expected_no_signal_summaries": (
                oversized_summary,
                evidence.no_signal_summaries[1],
            ),
        }
        store.load_gate3_validity_evidence(
            published,
            **summary_common,
            expected_failure_codes=(),
            expected_diagnosis_codes=(),
        )


def test_loader_rederives_compact_rows_from_full_receipt(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    mutated_row = evidence.hard_nulls[0].model_copy(
        update={"control_content_sha256": "sha256:" + "f" * 64}
    )
    resigned = _resign_evidence_receipt(
        evidence,
        hard_nulls=(mutated_row, *evidence.hard_nulls[1:]),
    )

    with pytest.raises(store.Gate3ValidityEvidenceError, match="compact receipt"):
        _load_validity(published, freezes, validity, resigned)


def test_builder_rejects_freeze_substitution_and_malformed_full_receipt(
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    substituted_freezes = (
        _baseline_freeze(
            corpus_id="lectra-m3-m4",
            selected_policy_id="remnant_first",
        ),
        freezes[1],
    )
    with pytest.raises(store.Gate3ValidityEvidenceError, match="supplied baseline freezes"):
        store.build_gate3_validity_evidence_receipt(
            validity,
            baseline_freezes=substituted_freezes,
        )

    malformed = validity.model_copy(update={"status": "invalid"})
    with pytest.raises(store.Gate3ValidityEvidenceError, match="strict validation"):
        store.build_gate3_validity_evidence_receipt(
            malformed,
            baseline_freezes=freezes,
        )


def test_strict_preflight_rejects_oversize_forged_graph_before_model_dump(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    forged_twin = validity.twin_controls[0].model_copy(update={"source_stream_id": "x" * 2048})
    forged = validity.model_copy(
        update={"twin_controls": (forged_twin, *validity.twin_controls[1:])}
    )

    def reject_model_dump(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("preflight must reject before detaching the receipt graph")

    monkeypatch.setattr(store, "_MAX_STRICT_RECEIPT_BYTES", 1024, raising=False)
    monkeypatch.setattr(BaseModel, "model_dump", reject_model_dump)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="strict receipt byte bound"):
        store.build_gate3_validity_evidence_receipt(
            forged,
            baseline_freezes=freezes,
        )


def test_strict_preflight_exact_size_accepts_complete_control_census(
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    _, validity = validity_case
    canonical = store.canonical_gate3_validity_receipt_bytes(validity)

    assert store._bounded_existing_canonical_size(
        validity,
        maximum_bytes=len(canonical),
    ) == len(canonical)


def test_recovery_enforces_strict_receipt_cap_before_json_parse(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    parsed = []

    def reject_parse(_raw):  # type: ignore[no-untyped-def]
        parsed.append(True)
        raise AssertionError("strict cap must stop decompression before JSON parsing")

    monkeypatch.setattr(
        store,
        "_MAX_STRICT_RECEIPT_BYTES",
        evidence.uncompressed_byte_count - 1,
        raising=False,
    )
    monkeypatch.setattr(store, "_parse_strict_validity_json", reject_parse)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="strict receipt byte bound"):
        store.recover_gate3_validity_evidence_receipt(
            published,
            expected_roots=validity.roots,
            expected_baseline_freezes=freezes,
        )
    assert parsed == []


def test_build_rejects_deep_model_copy_graph_as_public_store_error(
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    nested: object = "leaf"
    for _ in range(2000):
        nested = [nested]
    forged = validity.model_copy(update={"roots": nested})

    with pytest.raises(store.Gate3ValidityEvidenceError, match="nesting depth"):
        store.build_gate3_validity_evidence_receipt(
            forged,
            baseline_freezes=freezes,
        )


def test_json_nesting_depth_accepts_exact_limit_and_rejects_next_level() -> None:
    store = _store()
    exact = b"[" * store._MAX_JSON_NESTING_DEPTH + b"0" + b"]" * (store._MAX_JSON_NESTING_DEPTH)
    too_deep = b"[" + exact + b"]"

    store._validate_json_nesting_depth(exact)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="nesting depth"):
        store._validate_json_nesting_depth(too_deep)


def test_load_and_recover_reject_deep_json_as_public_store_error(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, _, evidence = _publish_validity(tmp_path, validity_case)
    raw = b"[" * 10_000 + b"0" + b"]" * 10_000
    assert len(raw) < store._MAX_STRICT_RECEIPT_BYTES
    transport = store.deterministic_gzip(raw)
    resigned = _resign_transport(evidence, transport, raw)
    candidate = tmp_path / "deep-json" / resigned.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)

    with pytest.raises(store.Gate3ValidityEvidenceError, match="nesting depth"):
        _load_validity(candidate, freezes, validity, resigned)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="nesting depth"):
        store.recover_gate3_validity_evidence_receipt(
            candidate,
            expected_roots=validity.roots,
            expected_baseline_freezes=freezes,
        )


def test_builder_normalizes_decimal_failure_from_allowed_large_cost_string(
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    forged_twin = validity.twin_controls[0].model_copy(
        update={"baseline_cost": f"{'9' * 57}.000000"}
    )
    forged = validity.model_copy(
        update={"twin_controls": (forged_twin, *validity.twin_controls[1:])}
    )

    with pytest.raises(store.Gate3ValidityEvidenceError, match="strict validation"):
        store.build_gate3_validity_evidence_receipt(
            forged,
            baseline_freezes=freezes,
        )


def test_load_and_recover_normalize_decimal_failure_in_sidecar(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    payload = json.loads(gzip.decompress(published.read_bytes()))
    payload["twin_controls"][0]["baseline_cost"] = f"{'9' * 57}.000000"
    raw = (
        json.dumps(
            payload,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    transport = store.deterministic_gzip(raw)
    resigned = _resign_transport(evidence, transport, raw)
    candidate = tmp_path / "decimal-failure" / resigned.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)

    with pytest.raises(store.Gate3ValidityEvidenceError, match="semantic validation"):
        _load_validity(candidate, freezes, validity, resigned)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="semantic validation"):
        store.recover_gate3_validity_evidence_receipt(
            candidate,
            expected_roots=validity.roots,
            expected_baseline_freezes=freezes,
        )


def test_loader_accepts_distinct_bound_gzip_transport_without_recompression(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    alternate = bytearray(published.read_bytes())
    alternate[9] = 3 if alternate[9] != 3 else 255
    alternate_bytes = bytes(alternate)
    alternate_receipt = _resign_transport(evidence, alternate_bytes)
    alternate_path = tmp_path / alternate_receipt.sidecar_name
    alternate_path.write_bytes(alternate_bytes)

    def reject_recompression(_data):  # type: ignore[no-untyped-def]
        raise AssertionError("loader must not recreate historical gzip transport")

    monkeypatch.setattr(store, "deterministic_gzip", reject_recompression)
    assert alternate_path != published
    assert gzip.decompress(alternate_bytes) == gzip.decompress(published.read_bytes())
    assert _load_validity(alternate_path, freezes, validity, alternate_receipt) == validity


def test_loader_stream_compares_canonical_encoding_without_materializing_copy(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)

    assert _load_validity(published, freezes, validity, evidence) == validity


def test_validity_store_freezes_materially_bounded_peak_contract() -> None:
    store = _store()

    assert store._MAX_UNCOMPRESSED_BYTES == 128 * 1024 * 1024
    assert store._MAX_COMPRESSED_BYTES == 32 * 1024 * 1024
    assert store._MAX_STRICT_RECEIPT_BYTES == 32 * 1024 * 1024


@pytest.mark.parametrize("header_mutation", ("mtime", "fname"))
def test_loader_rejects_resigned_noncanonical_gzip_header(
    tmp_path: Path,
    validity_case,
    header_mutation: str,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    mutated = bytearray(published.read_bytes())
    if header_mutation == "mtime":
        mutated[4] = 1
    else:
        mutated[3] |= 0x08
        mutated[10:10] = b"forged-name.json\x00"
    transport = bytes(mutated)
    mutated_receipt = _resign_transport(evidence, transport)
    candidate = tmp_path / "headers" / mutated_receipt.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)
    assert gzip.decompress(transport) == gzip.decompress(published.read_bytes())

    with pytest.raises(store.Gate3ValidityEvidenceError, match="gzip header"):
        _load_validity(candidate, freezes, validity, mutated_receipt)


def test_loader_rejects_transport_tamper_and_bounded_decompression(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    tampered = bytearray(published.read_bytes())
    tampered[-1] ^= 1
    candidate = tmp_path / "tampered" / evidence.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(tampered)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="compressed raw hash"):
        _load_validity(candidate, freezes, validity, evidence)

    compressed_limit = store._MAX_COMPRESSED_BYTES
    monkeypatch.setattr(
        store,
        "_MAX_COMPRESSED_BYTES",
        evidence.compressed_byte_count - 1,
    )
    with pytest.raises(store.Gate3ValidityEvidenceError, match="bounded regular file"):
        _load_validity(published, freezes, validity, evidence)

    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", compressed_limit)
    monkeypatch.setattr(
        store,
        "_MAX_UNCOMPRESSED_BYTES",
        evidence.uncompressed_byte_count - 1,
    )
    with pytest.raises(store.Gate3ValidityEvidenceError, match="uncompressed byte bound"):
        _load_validity(published, freezes, validity, evidence)


@pytest.mark.parametrize("mutation", ("duplicate", "nonfinite", "noncanonical"))
def test_loader_rejects_duplicate_nonfinite_and_noncanonical_json(
    tmp_path: Path,
    validity_case,
    mutation: str,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    raw = gzip.decompress(published.read_bytes())
    if mutation == "duplicate":
        raw = raw.replace(
            b'{\n  "content_sha256"',
            b'{\n  "status": "valid",\n  "content_sha256"',
            1,
        )
        expected = "duplicate"
    elif mutation == "nonfinite":
        raw = raw.replace(b'"exact_control_census": true', b'"exact_control_census": NaN')
        expected = "non-finite"
    else:
        parsed = json.loads(raw)
        raw = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        expected = "canonical"
    transport = store.deterministic_gzip(raw)
    resigned = _resign_transport(evidence, transport, raw)
    candidate = tmp_path / mutation / resigned.sidecar_name
    candidate.parent.mkdir()
    candidate.write_bytes(transport)

    with pytest.raises(store.Gate3ValidityEvidenceError, match=expected):
        _load_validity(candidate, freezes, validity, resigned)


def test_loader_rejects_symlink_directory_and_nonregular_open_race(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    symlink = tmp_path / "link" / evidence.sidecar_name
    symlink.parent.mkdir()
    symlink.symlink_to(published)
    directory = tmp_path / "directory" / evidence.sidecar_name
    directory.mkdir(parents=True)
    for candidate in (symlink, directory):
        with pytest.raises(store.Gate3ValidityEvidenceError, match="bounded regular file"):
            _load_validity(candidate, freezes, validity, evidence)

    read_descriptor, write_descriptor = os.pipe()
    os.set_blocking(read_descriptor, False)
    seen_flags = []

    def substitute_open(path, flags):  # type: ignore[no-untyped-def]
        seen_flags.append(flags)
        return read_descriptor

    monkeypatch.setattr(store.os, "open", substitute_open)
    try:
        with pytest.raises(store.Gate3ValidityEvidenceError, match="bounded regular file"):
            _load_validity(published, freezes, validity, evidence)
    finally:
        os.close(write_descriptor)
    assert seen_flags[0] & os.O_NONBLOCK


def test_loader_rejects_regular_file_fingerprint_change_during_read(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    real_fstat = store.os.fstat
    calls = 0

    def changed_fstat(descriptor):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        result = real_fstat(descriptor)
        if calls == 2:
            values = list(result)
            values[9] += 1  # st_ctime moves while descriptor remains the same regular file.
            return os.stat_result(values)
        return result

    monkeypatch.setattr(store.os, "fstat", changed_fstat)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="changed during read-back"):
        _load_validity(published, freezes, validity, evidence)


def test_receipt_and_preparation_enforce_exact_size_bounds(
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    canonical = store.canonical_gate3_validity_receipt_bytes(validity)
    compressed = store.deterministic_gzip(canonical)
    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", len(canonical))
    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", len(compressed))
    receipt = store.build_gate3_validity_evidence_receipt(
        validity,
        baseline_freezes=freezes,
    )
    assert receipt.uncompressed_byte_count == len(canonical)
    assert receipt.compressed_byte_count == len(compressed)

    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", len(canonical) - 1)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="uncompressed byte bound"):
        store.build_gate3_validity_evidence_receipt(
            validity,
            baseline_freezes=freezes,
        )
    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", len(canonical))
    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", len(compressed) - 1)
    with pytest.raises(store.Gate3ValidityEvidenceError, match="compressed byte bound"):
        store.build_gate3_validity_evidence_receipt(
            validity,
            baseline_freezes=freezes,
        )
