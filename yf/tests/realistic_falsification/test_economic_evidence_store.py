from __future__ import annotations

import gzip
import hashlib
import importlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.realistic_falsification.test_confirmation import (
    _fake_calibration_observation,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.confirmation import (
    build_gate3_cost_ledger,
    build_gate3_root_binding,
)


def test_deterministic_gzip_uses_frozen_header_and_round_trips() -> None:
    store = importlib.import_module(
        "yieldforge.realistic_falsification.economic_evidence_store"
    )
    raw = b'{"economic": "evidence"}\n' * 32

    first = store.deterministic_gzip(raw)
    second = store.deterministic_gzip(raw)

    assert first == second
    assert first[3] == 0  # no original filename flag
    assert first[4:8] == b"\x00\x00\x00\x00"  # frozen mtime
    assert gzip.decompress(first) == raw


def test_calibration_receipt_exactly_binds_strict_observation() -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation = _fake_calibration_observation(
        "loco-2dics",
        "yfm11st-0000000000000000000003e8",
        "myopic_geometry",
    )

    receipt = store.build_gate3_calibration_observation_receipt(
        observation,
        source_lineage="repaired_runtime",
    )

    semantic_hash = observation.content_sha256.removeprefix("sha256:")
    assert receipt.roots == observation.roots
    assert receipt.corpus_id == observation.corpus_id
    assert receipt.stream_id == observation.stream_id
    assert receipt.policy_id == observation.policy_id
    assert receipt.observation_id == observation.observation_id
    assert receipt.observation_content_sha256 == observation.content_sha256
    assert receipt.final_costs == observation.final_costs
    assert receipt.full_sheet_opening_count == observation.full_sheet_opening_count
    assert receipt.exact_event_census is True
    assert receipt.source_lineage == "repaired_runtime"
    assert receipt.sidecar_name == (
        f"m11-gate3-calibration-observation-{semantic_hash}.json.gz"
    )
    assert receipt.compression == "gzip-level-6-mtime-0-no-filename"

    legacy = store.build_gate3_calibration_observation_receipt(
        observation,
        source_lineage="legacy_success_output_equivalent",
    )
    assert legacy.receipt_id != receipt.receipt_id
    assert legacy.content_sha256 != receipt.content_sha256
    assert legacy.sidecar_name == receipt.sidecar_name

    mutated = receipt.model_copy(update={"stream_id": "forged-stream"})
    with pytest.raises(ValidationError, match="receipt identity"):
        store.Gate3CalibrationObservationReceipt.model_validate(
            mutated.model_dump(mode="python", round_trip=True),
            strict=True,
        )


def test_receipt_builder_strictly_revalidates_before_copying_fields() -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation = _fake_calibration_observation(
        "loco-2dics",
        "yfm11st-0000000000000000000003e8",
        "myopic_geometry",
    )
    malformed = observation.model_copy(update={"stream_id": "forged-stream"})

    with pytest.raises(ValidationError, match="Gate 3 calibration"):
        store.build_gate3_calibration_observation_receipt(
            malformed,
            source_lineage="repaired_runtime",
        )


def _publish_observation(tmp_path: Path):
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation = _fake_calibration_observation(
        "loco-2dics",
        "yfm11st-0000000000000000000003e8",
        "myopic_geometry",
    )
    path, receipt = store.publish_gate3_calibration_observation_evidence(
        tmp_path,
        observation,
        source_lineage="repaired_runtime",
    )
    return observation, path, receipt


def _load_observation(path: Path, receipt, observation):  # type: ignore[no-untyped-def]
    from yieldforge.realistic_falsification import economic_evidence_store as store

    return store.load_gate3_calibration_observation_evidence(
        path,
        receipt=receipt,
        expected_roots=observation.roots,
        expected_corpus_id=observation.corpus_id,
        expected_stream_id=observation.stream_id,
        expected_policy_id=observation.policy_id,
        expected_observation_id=observation.observation_id,
        expected_final_costs=observation.final_costs,
        expected_full_sheet_opening_count=observation.full_sheet_opening_count,
        expected_source_lineage="repaired_runtime",
    )


def _resign_receipt(receipt, **updates):  # type: ignore[no-untyped-def]
    from yieldforge.realistic_falsification import economic_evidence_store as store

    semantic = receipt.model_dump(
        mode="python",
        round_trip=True,
        exclude={"receipt_id", "content_sha256"},
    )
    semantic.update(updates)
    digest = semantic_sha256(semantic)
    return store.Gate3CalibrationObservationReceipt(
        receipt_id=f"yfm11g3calrcpt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def test_publish_is_idempotent_and_load_returns_exact_observation(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, first_path, first_receipt = _publish_observation(tmp_path)
    second_path, second_receipt = store.publish_gate3_calibration_observation_evidence(
        tmp_path,
        observation,
        source_lineage="repaired_runtime",
    )

    assert first_path == second_path == tmp_path / first_receipt.sidecar_name
    assert first_path.name == first_receipt.sidecar_name
    assert Path(first_receipt.sidecar_name).parent == Path(".")
    assert first_receipt == second_receipt
    assert _load_observation(first_path, first_receipt, observation) == observation


def test_publish_refuses_to_overwrite_foreign_bytes(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation = _fake_calibration_observation(
        "loco-2dics",
        "yfm11st-0000000000000000000003e8",
        "myopic_geometry",
    )
    receipt = store.build_gate3_calibration_observation_receipt(
        observation,
        source_lineage="repaired_runtime",
    )
    destination = tmp_path / receipt.sidecar_name
    destination.write_bytes(b"foreign immutable bytes")

    with pytest.raises(store.Gate3EconomicEvidenceError, match="immutable publication failed"):
        store.publish_gate3_calibration_observation_evidence(
            tmp_path,
            observation,
            source_lineage="repaired_runtime",
        )
    assert destination.read_bytes() == b"foreign immutable bytes"


def test_loader_rejects_compressed_tamper(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, published, receipt = _publish_observation(tmp_path)
    tampered_directory = tmp_path / "tampered"
    tampered_directory.mkdir()
    tampered = tampered_directory / receipt.sidecar_name
    data = bytearray(published.read_bytes())
    data[-1] ^= 1
    tampered.write_bytes(data)

    with pytest.raises(store.Gate3EconomicEvidenceError, match="compressed.*hash"):
        _load_observation(tampered, receipt, observation)

    wrong_count = _resign_receipt(
        receipt,
        compressed_byte_count=receipt.compressed_byte_count + 1,
    )
    with pytest.raises(store.Gate3EconomicEvidenceError, match="compressed byte count"):
        _load_observation(published, wrong_count, observation)


def test_loader_rejects_valid_semantic_substitution(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    expected, _, _ = _publish_observation(tmp_path)
    substituted = _fake_calibration_observation(
        "loco-2dics",
        "yfm11st-0000000000000000000003e9",
        "myopic_geometry",
    )
    substitute_path, substitute_receipt = (
        store.publish_gate3_calibration_observation_evidence(
            tmp_path,
            substituted,
            source_lineage="repaired_runtime",
        )
    )

    with pytest.raises(store.Gate3EconomicEvidenceError, match="expected binding"):
        _load_observation(substitute_path, substitute_receipt, expected)


def test_loader_rejects_source_lineage_substitution(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, published, receipt = _publish_observation(tmp_path)
    substituted_receipt = _resign_receipt(
        receipt,
        source_lineage="legacy_success_output_equivalent",
    )

    with pytest.raises(store.Gate3EconomicEvidenceError, match="source lineage"):
        store.load_gate3_calibration_observation_evidence(
            published,
            receipt=substituted_receipt,
            expected_roots=observation.roots,
            expected_corpus_id=observation.corpus_id,
            expected_stream_id=observation.stream_id,
            expected_policy_id=observation.policy_id,
            expected_observation_id=observation.observation_id,
            expected_final_costs=observation.final_costs,
            expected_full_sheet_opening_count=observation.full_sheet_opening_count,
            expected_source_lineage="repaired_runtime",
        )


def test_loader_wraps_malformed_deflate_as_evidence_error(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, _, receipt = _publish_observation(tmp_path)
    malformed = (
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff"
        b"\xff\xff\xff\xff\xff"
    )
    substituted_receipt = _resign_receipt(
        receipt,
        compressed_raw_sha256=(
            f"sha256:{hashlib.sha256(malformed).hexdigest()}"
        ),
        compressed_byte_count=len(malformed),
    )
    candidate_directory = tmp_path / "malformed"
    candidate_directory.mkdir()
    candidate = candidate_directory / receipt.sidecar_name
    candidate.write_bytes(malformed)

    with pytest.raises(store.Gate3EconomicEvidenceError, match="not valid gzip"):
        _load_observation(candidate, substituted_receipt, observation)


def test_loader_rejects_expected_ledger_and_opening_mismatch(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, published, receipt = _publish_observation(tmp_path)
    different_ledger = build_gate3_cost_ledger(
        purchase_cost="1.000000",
        storage_cost="0.000000",
        return_handling_cost="0.000000",
        retrieval_handling_cost="0.000000",
        scrap_proceeds="0.000000",
        terminal_credit="0.000000",
    )
    common = {
        "receipt": receipt,
        "expected_roots": observation.roots,
        "expected_corpus_id": observation.corpus_id,
        "expected_stream_id": observation.stream_id,
        "expected_policy_id": observation.policy_id,
        "expected_observation_id": observation.observation_id,
    }
    root_values = observation.roots.model_dump(
        mode="python",
        exclude={"schema_version", "binding_id", "content_sha256", "gate2_survived"},
    )
    root_values["adapter_runtime_config_sha256"] = "sha256:" + "f" * 64
    different_roots = build_gate3_root_binding(**root_values)

    with pytest.raises(store.Gate3EconomicEvidenceError, match="expected binding"):
        store.load_gate3_calibration_observation_evidence(
            published,
            **(common | {"expected_roots": different_roots}),
            expected_final_costs=observation.final_costs,
            expected_full_sheet_opening_count=observation.full_sheet_opening_count,
            expected_source_lineage="repaired_runtime",
        )
    with pytest.raises(store.Gate3EconomicEvidenceError, match="expected ledger"):
        store.load_gate3_calibration_observation_evidence(
            published,
            **common,
            expected_final_costs=different_ledger,
            expected_full_sheet_opening_count=observation.full_sheet_opening_count,
            expected_source_lineage="repaired_runtime",
        )
    with pytest.raises(store.Gate3EconomicEvidenceError, match="expected opening"):
        store.load_gate3_calibration_observation_evidence(
            published,
            **common,
            expected_final_costs=observation.final_costs,
            expected_full_sheet_opening_count=observation.full_sheet_opening_count + 1,
            expected_source_lineage="repaired_runtime",
        )


def test_loader_rejects_symlink_and_nonregular_file(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, published, receipt = _publish_observation(tmp_path)
    symlink_directory = tmp_path / "symlink"
    symlink_directory.mkdir()
    linked = symlink_directory / receipt.sidecar_name
    linked.symlink_to(published)
    directory = tmp_path / "directory" / receipt.sidecar_name
    directory.mkdir(parents=True)

    for candidate in (linked, directory):
        with pytest.raises(store.Gate3EconomicEvidenceError, match="bounded regular file"):
            _load_observation(candidate, receipt, observation)


def test_loader_rejects_nonregular_open_race_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, published, receipt = _publish_observation(tmp_path)
    read_descriptor, write_descriptor = os.pipe()
    os.set_blocking(read_descriptor, False)
    seen_flags = []

    def substitute_open(path, flags):  # type: ignore[no-untyped-def]
        seen_flags.append(flags)
        return read_descriptor

    monkeypatch.setattr(store.os, "open", substitute_open)
    try:
        with pytest.raises(store.Gate3EconomicEvidenceError, match="bounded regular file"):
            _load_observation(published, receipt, observation)
    finally:
        os.close(write_descriptor)

    assert seen_flags[0] & os.O_NONBLOCK


def test_loader_rejects_compressed_and_uncompressed_oversize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_evidence_store as store

    observation, published, receipt = _publish_observation(tmp_path)
    compressed_limit = store._MAX_COMPRESSED_BYTES
    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", receipt.compressed_byte_count - 1)
    with pytest.raises(store.Gate3EconomicEvidenceError, match="bounded regular file"):
        _load_observation(published, receipt, observation)

    monkeypatch.setattr(store, "_MAX_COMPRESSED_BYTES", compressed_limit)
    monkeypatch.setattr(store, "_MAX_UNCOMPRESSED_BYTES", receipt.uncompressed_byte_count - 1)
    with pytest.raises(store.Gate3EconomicEvidenceError, match="uncompressed.*bound"):
        _load_observation(published, receipt, observation)
