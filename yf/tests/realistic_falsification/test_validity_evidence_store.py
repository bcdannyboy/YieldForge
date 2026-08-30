from __future__ import annotations

import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_publication_prepares_full_receipt_exactly_once(
    tmp_path: Path,
    validity_case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity = validity_case
    serialize = store.canonical_pretty_json_bytes
    compress = store.deterministic_gzip
    calls = {"serialize": 0, "compress": 0}

    def counted_serialize(value):  # type: ignore[no-untyped-def]
        calls["serialize"] += 1
        return serialize(value)

    def counted_compress(data):  # type: ignore[no-untyped-def]
        calls["compress"] += 1
        return compress(data)

    monkeypatch.setattr(store, "canonical_pretty_json_bytes", counted_serialize)
    monkeypatch.setattr(store, "deterministic_gzip", counted_compress)
    store.publish_gate3_validity_evidence(
        tmp_path,
        validity,
        baseline_freezes=freezes,
    )

    assert calls == {"serialize": 1, "compress": 1}


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


def test_loader_rederives_compact_rows_from_full_receipt(
    tmp_path: Path,
    validity_case,
) -> None:  # type: ignore[no-untyped-def]
    store = _store()
    freezes, validity, published, evidence = _publish_validity(tmp_path, validity_case)
    mutated_row = evidence.twin_controls[0].model_copy(update={"known_only_cost": "999.000000"})
    resigned = _resign_evidence_receipt(
        evidence,
        twin_controls=(mutated_row, *evidence.twin_controls[1:]),
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
