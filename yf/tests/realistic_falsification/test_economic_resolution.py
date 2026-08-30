from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.realistic_falsification.test_confirmation import (
    _fake_calibration_observation,
    _roots,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.confirmation import (
    GATE3_BASELINE_POLICY_IDS,
    Gate3CalibrationAttempt,
    build_gate3_cost_ledger,
    build_gate3_root_binding,
    select_gate3_baseline_policy,
)

STREAM_A = "yfm11st-" + "a" * 24
STREAM_B = "yfm11st-" + "b" * 24


def _calibration_attempt(
    *,
    position: int,
    status: str,
    stream_id: str,
) -> Gate3CalibrationAttempt:
    roots = _roots()
    observation = (
        _fake_calibration_observation("loco-2dics", stream_id, "myopic_geometry")
        if status == "success"
        else None
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-calibration-attempt.v1",
        "roots": roots.model_dump(mode="json"),
        "execution_position": position,
        "corpus_id": "loco-2dics",
        "stream_id": stream_id,
        "policy_id": "myopic_geometry",
        "status": status,
        "observation": observation.model_dump(mode="json") if observation else None,
        "failure_type": None if observation else "builtins.ValueError",
        "failure_detail": (None if observation else 'escaped quote: " and braces: {not an object}'),
    }
    digest = semantic_sha256(semantic)
    return Gate3CalibrationAttempt(
        attempt_id=f"yfm11g3calatt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        execution_position=position,
        corpus_id="loco-2dics",
        stream_id=stream_id,
        policy_id="myopic_geometry",
        status=status,  # type: ignore[arg-type]
        observation=observation,
        failure_type=None if observation else "builtins.ValueError",
        failure_detail=(None if observation else 'escaped quote: " and braces: {not an object}'),
    )


def _legacy_artifact_bytes(attempts: tuple[Gate3CalibrationAttempt, ...]) -> bytes:
    return (
        json.dumps(
            {
                "content_sha256": "sha256:" + "9" * 64,
                "result": {
                    "calibration_attempts": [item.model_dump(mode="json") for item in attempts]
                },
                "run_id": "synthetic",
            },
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _write_legacy(path: Path, raw: bytes) -> tuple[int, str]:
    path.write_bytes(raw)
    return len(raw), f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _resign_attempt(attempt, **updates):  # type: ignore[no-untyped-def]
    draft = attempt.model_copy(update=updates)
    semantic = draft.model_dump(
        mode="json",
        exclude={"attempt_id", "content_sha256"},
    )
    digest = semantic_sha256(semantic)
    return type(attempt).model_validate(
        draft.model_copy(
            update={
                "attempt_id": f"yfm11g3calatt-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            }
        ).model_dump(mode="python", round_trip=True),
        strict=True,
    )


def _official_roots():
    return build_gate3_root_binding(
        contract_id="yfm11c-e956019aeef85350f2ffa9d3",
        contract_content_sha256=(
            "sha256:e956019aeef85350f2ffa9d351ab15539d1b23137d7566118f73c5f29882143b"
        ),
        population_id="yfm11pop-a26084179d2e8f776630f8ac",
        population_content_sha256=(
            "sha256:a26084179d2e8f776630f8ac272d5651069500a5df996869d20df9893ca0bc56"
        ),
        gate1_run_id="yfm11g1run-c35f10fa4f4d7b6b01c59c29",
        gate1_run_content_sha256=(
            "sha256:c35f10fa4f4d7b6b01c59c298d3e61f82103551e3a8ec44435818dc028f1dd74"
        ),
        gate1_evaluation_result_id="yfm11g1r-b3e8d97ce476749bbdd1e562",
        gate1_evaluation_result_content_sha256=(
            "sha256:b3e8d97ce476749bbdd1e56207976f086bc17e47c8c17dcadb83080e33fa08e9"
        ),
        gate2_run_id="yfm11g2run-7419e46b74e411aff5c27ee1",
        gate2_run_content_sha256=(
            "sha256:7419e46b74e411aff5c27ee1913b3d67d703ec88d06855e8b366a16ffde6a450"
        ),
        gate2_evaluation_result_id="yfm11g2r-12b6242eeb5eccb26034e54b",
        gate2_evaluation_result_content_sha256=(
            "sha256:12b6242eeb5eccb26034e54b6cd89c88ae3a6ea0b6f564c57fd4ce85ab423faa"
        ),
        gate3_config_id="yfm11g3c-795010e6747d2c11d556ef82",
        gate3_config_content_sha256=(
            "sha256:795010e6747d2c11d556ef82071905d89fda757dd39273ab83d1c2a52606808b"
        ),
        adapter_runtime_config_sha256=(
            "sha256:9006a711a465e3b57b97f703cea5ea90b0b06fa8922a5330b95ef0979fe1e0a1"
        ),
    )


def _reference(resolution, *, position: int, status: str):  # type: ignore[no-untyped-def]
    corpus_id = "lectra-m3-m4" if position < 48 else "loco-2dics"
    local = position if position < 48 else position - 48
    policy_index, stream_index = divmod(local, 8)
    policy_id = GATE3_BASELINE_POLICY_IDS[policy_index]
    corpus_digit = 1 if corpus_id == "lectra-m3-m4" else 2
    stream_id = f"yfm11st-{corpus_digit * 8 + stream_index + 1:024x}"
    attempt_hash = f"{position + 1:064x}"
    observation_hash = f"{position + 1000:064x}"
    ledger = build_gate3_cost_ledger(
        purchase_cost=f"{100 + policy_index * 10 + stream_index}.000000",
        storage_cost="0.000000",
        return_handling_cost="0.000000",
        retrieval_handling_cost="0.000000",
        scrap_proceeds="0.000000",
        terminal_credit="0.000000",
    )
    semantic = {
        "schema_version": "yieldforge.m11-economic-legacy-calibration-reference.v1",
        "roots": _official_roots().model_dump(mode="json"),
        "execution_position": position,
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "policy_id": policy_id,
        "attempt_id": f"yfm11g3calatt-{attempt_hash[:24]}",
        "attempt_content_sha256": f"sha256:{attempt_hash}",
        "attempt_byte_offset": 10_000 + position * 1_000,
        "attempt_byte_count": 900,
        "status": status,
        "observation_id": (
            f"yfm11g3calobs-{observation_hash[:24]}" if status == "success" else None
        ),
        "observation_content_sha256": (
            f"sha256:{observation_hash}" if status == "success" else None
        ),
        "final_costs": ledger.model_dump(mode="json") if status == "success" else None,
        "full_sheet_opening_count": 1 if status == "success" else None,
        "exact_event_census": True if status == "success" else None,
        "source_lineage": ("legacy_success_output_equivalent" if status == "success" else None),
        "failure_type": None if status == "success" else "builtins.ValueError",
        "failure_detail": None if status == "success" else "preserved failure",
    }
    digest = semantic_sha256(semantic)
    return resolution.Gate3LegacyCalibrationAttemptReference(
        reference_id=f"yfm11econlegacy-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _official_references(resolution):  # type: ignore[no-untyped-def]
    return tuple(
        _reference(
            resolution,
            position=position,
            status=("success" if position < 48 or (position - 48) % 8 < 2 else "failure"),
        )
        for position in range(96)
    )


def _resign_reference(reference, **updates):  # type: ignore[no-untyped-def]
    draft = reference.model_copy(update=updates)
    semantic = draft.model_dump(
        mode="json",
        exclude={"reference_id", "content_sha256"},
    )
    digest = semantic_sha256(semantic)
    return type(reference).model_validate(
        draft.model_copy(
            update={
                "reference_id": f"yfm11econlegacy-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            }
        ).model_dump(mode="python", round_trip=True),
        strict=True,
    )


def _repaired_receipt(
    resolution,  # type: ignore[no-untyped-def]
    *,
    position: int,
    corpus_id: str,
    stream_id: str,
    policy_id: str,
):
    from yieldforge.realistic_falsification.economic_evidence_store import (
        Gate3CalibrationObservationReceipt,
    )

    policy_index = GATE3_BASELINE_POLICY_IDS.index(policy_id)
    stream_index = int(stream_id[-2:], 16) % 8
    ledger = build_gate3_cost_ledger(
        purchase_cost=f"{100 + policy_index * 10 + stream_index}.000000",
        storage_cost="0.000000",
        return_handling_cost="0.000000",
        retrieval_handling_cost="0.000000",
        scrap_proceeds="0.000000",
        terminal_credit="0.000000",
    )
    observation_hash = f"{position + 20_000:064x}"
    compressed_hash = f"{position + 30_000:064x}"
    semantic = {
        "schema_version": "yieldforge.m11-gate3-calibration-receipt.v1",
        "roots": _official_roots().model_dump(mode="json"),
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "policy_id": policy_id,
        "observation_id": f"yfm11g3calobs-{observation_hash[:24]}",
        "observation_content_sha256": f"sha256:{observation_hash}",
        "final_costs": ledger.model_dump(mode="json"),
        "full_sheet_opening_count": position % 5 + 1,
        "exact_event_census": True,
        "source_lineage": "repaired_runtime",
        "sidecar_name": (
            f"m11-gate3-calibration-observation-{observation_hash}-{compressed_hash}.json.gz"
        ),
        "compressed_raw_sha256": f"sha256:{compressed_hash}",
        "compressed_byte_count": 100,
        "uncompressed_byte_count": 200,
        "compression": "gzip-level-6-mtime-0-flags-0",
    }
    digest = semantic_sha256(semantic)
    return Gate3CalibrationObservationReceipt(
        receipt_id=f"yfm11g3calrcpt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _valid_checkpoints(resolution):  # type: ignore[no-untyped-def]
    checkpoints = []
    for reference in _official_references(resolution):
        common = {
            "protocol": resolution.build_economic_resolution_protocol(),
            "roots": _official_roots(),
            "execution_position": reference.execution_position,
            "corpus_id": reference.corpus_id,
            "stream_id": reference.stream_id,
            "policy_id": reference.policy_id,
        }
        if reference.status == "success":
            outcome = {"legacy_reference": reference}
        else:
            outcome = {
                "repaired_receipt": _repaired_receipt(
                    resolution,
                    position=reference.execution_position,
                    corpus_id=reference.corpus_id,
                    stream_id=reference.stream_id,
                    policy_id=reference.policy_id,
                ),
                "replaced_legacy_failure_reference": reference,
            }
        checkpoints.append(
            resolution.build_gate3_calibration_attempt_checkpoint(
                **common,
                **outcome,
            )
        )
    return tuple(checkpoints)


def test_repair_lineage_protocol_freezes_every_decision_input_and_no_outcome() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    protocol = resolution.build_economic_resolution_protocol()

    assert protocol.protocol_version == "yieldforge.m11-economic-resolution.v1"
    assert protocol.repair_commit_sha == "3e7bcb1c587d950134639f6836341ef3d8f7d99e"
    assert protocol.repaired_source_raw_sha256 == (
        "sha256:2b0ffd70c079d3526277ea554e82f47604d5f28e5171fd3dd1a8ddc8651b610c"
    )
    assert protocol.legacy_artifact_byte_count == 2_270_455_752
    assert protocol.legacy_artifact_raw_sha256 == (
        "sha256:e5757919ddd9251bf374d1664be25faf175963e78478b223ea0d7e22f7439199"
    )
    assert protocol.legacy_run_id == "yfm11g3run-3dd87efab6f64ada4c5bd09c"
    assert protocol.legacy_run_content_sha256 == (
        "sha256:3dd87efab6f64ada4c5bd09c0580a1696017b3115ccdce3ce041b4221c89a89f"
    )
    assert protocol.legacy_root_content_sha256 == (
        "sha256:2a1a69bc188743bc5cca90a37b4655aee29ebd05f07eb588c8e0189bab5994e2"
    )
    assert protocol.accounting_arithmetic_semantic == (
        "placed_plus_process_loss_plus_retained_plus_scrap_left_to_right"
    )
    assert protocol.bootstrap_bit_generator == "PCG64"
    assert protocol.bootstrap_generator == "numpy.Generator(PCG64(0))"
    assert protocol.bootstrap_seed == 0
    assert protocol.bootstrap_resamples == 10_000
    assert protocol.bootstrap_resampling_unit == "paired_stream"
    assert protocol.bootstrap_quantile_method == "linear_type_7"
    assert protocol.bootstrap_confidence_level == 0.95
    assert protocol.bootstrap_lower_quantile == 0.025
    assert protocol.bootstrap_upper_quantile == 0.975
    assert protocol.max_attempt_object_bytes == 64 * 1024 * 1024
    assert protocol.central_full_future_mean_min_percent == "2.500000000000"
    assert protocol.central_unknown_future_contribution_min_percentage_points == "1.500000000000"
    assert protocol.causal_known_only_mean_min_percent == "1.500000000000"
    assert protocol.lower_confidence_bound_rule == "strictly_greater_than_zero"
    assert protocol.median_savings_rule == "strictly_greater_than_zero"
    assert protocol.positive_stream_fraction_rule == "strictly_greater_than_one_half"
    assert not any(
        fragment in key
        for key in type(protocol).model_fields
        for fragment in ("outcome", "result", "verdict", "status")
    )
    assert resolution.build_economic_resolution_protocol() == protocol


def test_repair_lineage_protocol_identity_rejects_mutation() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    protocol = resolution.build_economic_resolution_protocol()
    forged = protocol.model_copy(update={"protocol_id": "yfm11econp-" + "0" * 24})

    with pytest.raises(ValidationError, match="protocol identity"):
        resolution.EconomicResolutionProtocol.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )


def test_repair_lineage_protocol_is_frozen() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    protocol = resolution.build_economic_resolution_protocol()

    with pytest.raises(ValidationError, match="frozen"):
        protocol.bootstrap_seed = 1  # type: ignore[misc]


@pytest.mark.parametrize("chunk_size", (4093, 65_521))
def test_streaming_scan_frames_attempts_across_chunks_without_retaining_graphs(
    tmp_path: Path,
    chunk_size: int,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempts = (
        _calibration_attempt(position=0, status="success", stream_id=STREAM_A),
        _calibration_attempt(position=1, status="failure", stream_id=STREAM_B),
    )
    raw = _legacy_artifact_bytes(attempts)
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)
    order = tuple((item.corpus_id, item.stream_id, item.policy_id) for item in attempts)

    references = resolution._scan_legacy_calibration_attempts(
        path,
        expected_byte_count=size,
        expected_raw_sha256=raw_sha,
        expected_root_content_sha256=_roots().content_sha256,
        expected_attempt_order=order,
        expected_success_count=1,
        expected_failure_count=1,
        chunk_size=chunk_size,
    )

    assert tuple(item.execution_position for item in references) == (0, 1)
    assert references[0].status == "success"
    assert references[0].observation_id == attempts[0].observation.observation_id  # type: ignore[union-attr]
    assert references[0].final_costs == attempts[0].observation.final_costs  # type: ignore[union-attr]
    assert references[0].source_lineage == "legacy_success_output_equivalent"
    assert references[0].failure_type is None
    assert references[1].status == "failure"
    assert references[1].final_costs is None
    assert references[1].failure_detail == attempts[1].failure_detail
    assert references[1].source_lineage is None
    assert "material_replays" not in json.dumps(
        [item.model_dump(mode="json") for item in references]
    )
    for reference, attempt in zip(references, attempts, strict=True):
        framed = raw[
            reference.attempt_byte_offset : reference.attempt_byte_offset
            + reference.attempt_byte_count
        ]
        assert json.loads(framed)["attempt_id"] == attempt.attempt_id


@pytest.mark.parametrize("malformation", ("duplicate", "nonfinite"))
def test_streaming_scan_rejects_duplicate_keys_and_nonfinite_constants(
    tmp_path: Path,
    malformation: str,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    if malformation == "duplicate":
        raw = raw.replace(
            b'"status": "failure",',
            b'"status": "failure",\n        "status": "failure",',
            1,
        )
    else:
        raw = raw.replace(
            b'"failure_type": "builtins.ValueError",',
            b'"rogue": NaN,\n        "failure_type": "builtins.ValueError",',
            1,
        )
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match=malformation):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
            chunk_size=13,
        )


def test_streaming_scan_enforces_object_bound_via_runtime_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)
    monkeypatch.setattr(resolution, "_MAX_ATTEMPT_OBJECT_BYTES", 64)

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="object byte bound"):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
            chunk_size=11,
        )


@pytest.mark.parametrize("mismatch", ("size", "hash", "root", "order", "census"))
def test_streaming_scan_rejects_authentication_binding_and_census_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)
    kwargs = {
        "expected_byte_count": size,
        "expected_raw_sha256": raw_sha,
        "expected_root_content_sha256": _roots().content_sha256,
        "expected_attempt_order": (("loco-2dics", STREAM_A, "myopic_geometry"),),
        "expected_success_count": 0,
        "expected_failure_count": 1,
        "chunk_size": 19,
    }
    if mismatch == "size":
        kwargs["expected_byte_count"] = size + 1
    elif mismatch == "hash":
        kwargs["expected_raw_sha256"] = "sha256:" + "0" * 64
    elif mismatch == "root":
        kwargs["expected_root_content_sha256"] = "sha256:" + "0" * 64
    elif mismatch == "order":
        kwargs["expected_attempt_order"] = (("loco-2dics", "wrong-stream", "myopic_geometry"),)
    else:
        kwargs["expected_success_count"] = 1
        kwargs["expected_failure_count"] = 0

    with pytest.raises(resolution.EconomicResolutionEvidenceError):
        resolution._scan_legacy_calibration_attempts(path, **kwargs)


def test_official_legacy_scan_requires_exact_96_order_repeated_census_and_60_36() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    references = _official_references(resolution)

    scan = resolution.build_official_legacy_calibration_scan(references)

    assert scan.protocol == resolution.build_economic_resolution_protocol()
    assert scan.roots == _official_roots()
    assert scan.attempt_references == references
    assert scan.success_count == 60
    assert scan.failure_count == 36
    assert scan.calibration_stream_census == (
        ("lectra-m3-m4", tuple(item.stream_id for item in references[:8])),
        ("loco-2dics", tuple(item.stream_id for item in references[48:56])),
    )
    assert scan.legacy_artifact_byte_count == 2_270_455_752
    assert scan.max_attempt_object_bytes == 64 * 1024 * 1024

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="order"):
        resolution.build_official_legacy_calibration_scan(
            (references[1], references[0], *references[2:])
        )
    changed_status = _reference(resolution, position=54, status="success")
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="status census"):
        resolution.build_official_legacy_calibration_scan(
            (*references[:54], changed_status, *references[55:])
        )


@pytest.mark.parametrize("drift", ("stream", "corpus", "policy", "second_root"))
def test_official_legacy_scan_rejects_each_structural_drift(drift: str) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    references = _official_references(resolution)
    position = 8 if drift == "stream" else (48 if drift == "corpus" else 1)
    updates = {
        "stream": {"stream_id": "yfm11st-" + f"{999:024x}"},
        "corpus": {"corpus_id": "lectra-m3-m4"},
        "policy": {"policy_id": "remnant_first"},
        "second_root": {"roots": _roots()},
    }[drift]
    changed = _resign_reference(references[position], **updates)

    with pytest.raises(resolution.EconomicResolutionEvidenceError):
        resolution.build_official_legacy_calibration_scan(
            (*references[:position], changed, *references[position + 1 :])
        )


def test_public_official_scanner_wires_every_frozen_authentication_constant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    captured = {}
    references = _official_references(resolution)

    def capture(path, **kwargs):  # type: ignore[no-untyped-def]
        captured["path"] = path
        captured.update(kwargs)
        return references

    monkeypatch.setattr(resolution, "_scan_legacy_calibration_attempts", capture)
    candidate = tmp_path / "official.json"
    scan = resolution.scan_official_legacy_gate3_calibration_artifact(candidate)

    assert scan.attempt_references == references
    assert captured == {
        "path": candidate,
        "expected_byte_count": 2_270_455_752,
        "expected_raw_sha256": (
            "sha256:e5757919ddd9251bf374d1664be25faf175963e78478b223ea0d7e22f7439199"
        ),
        "expected_root_content_sha256": (
            "sha256:2a1a69bc188743bc5cca90a37b4655aee29ebd05f07eb588c8e0189bab5994e2"
        ),
        "expected_attempt_order": None,
        "expected_success_count": 60,
        "expected_failure_count": 36,
        "max_attempt_object_bytes": 64 * 1024 * 1024,
    }


def test_legacy_reference_object_bound_is_exactly_64_mib() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    reference = _reference(resolution, position=0, status="success")
    at_limit = _resign_reference(
        reference,
        attempt_byte_count=64 * 1024 * 1024,
    )
    assert at_limit.attempt_byte_count == 64 * 1024 * 1024

    draft = at_limit.model_copy(update={"attempt_byte_count": 64 * 1024 * 1024 + 1})
    semantic = draft.model_dump(
        mode="json",
        exclude={"reference_id", "content_sha256"},
    )
    digest = semantic_sha256(semantic)
    forged = draft.model_copy(
        update={
            "reference_id": f"yfm11econlegacy-{digest[:24]}",
            "content_sha256": f"sha256:{digest}",
        }
    )
    with pytest.raises(ValidationError, match="less than or equal"):
        resolution.Gate3LegacyCalibrationAttemptReference.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )


def test_official_legacy_scan_byte_ranges_use_exclusive_endpoints() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    cursor = 10_000
    contiguous = []
    for reference in _official_references(resolution):
        contiguous.append(
            _resign_reference(
                reference,
                attempt_byte_offset=cursor,
                attempt_byte_count=100,
            )
        )
        cursor += 100

    assert resolution.build_official_legacy_calibration_scan(tuple(contiguous))


def test_streaming_scan_rejects_unbounded_failure_text_before_retention(
    tmp_path: Path,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _resign_attempt(
        _calibration_attempt(position=0, status="failure", stream_id=STREAM_A),
        failure_detail="x" * 1001,
    )
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)

    with pytest.raises(
        resolution.EconomicResolutionEvidenceError,
        match="failure.*bound|compact",
    ):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
        )


def test_streaming_scan_rejects_chunk_size_above_64_mib_without_os_overflow(
    tmp_path: Path,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)

    with pytest.raises(
        resolution.EconomicResolutionEvidenceError,
        match="chunk.*malformed",
    ):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
            chunk_size=64 * 1024 * 1024 + 1,
        )


def test_legacy_reference_rejects_attempt_and_observation_sha_prefix_forgery() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    reference = _reference(resolution, position=0, status="success")
    forged_attempt = reference.model_copy(update={"attempt_id": "yfm11g3calatt-" + "f" * 24})
    forged_observation = reference.model_copy(
        update={"observation_id": "yfm11g3calobs-" + "f" * 24}
    )

    for forged in (forged_attempt, forged_observation):
        with pytest.raises(ValidationError, match="binding"):
            resolution.Gate3LegacyCalibrationAttemptReference.model_validate(
                forged.model_dump(mode="python", round_trip=True),
                strict=True,
            )


def test_streaming_scan_requires_exactly_one_canonical_array_marker(
    tmp_path: Path,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    raw = raw.replace(
        b'"run_id": "synthetic"',
        b'"run_id": "synthetic calibration_attempts marker",\n  "calibration_attempts": []',
    )
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="exactly one"):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
            chunk_size=17,
        )


def test_streaming_scan_rejects_fingerprint_change_during_authenticated_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)
    real_fstat = resolution.os.fstat
    calls = 0

    def changing_fstat(descriptor: int):
        nonlocal calls
        metadata = real_fstat(descriptor)
        calls += 1
        if calls != 2:
            return metadata
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns + 1,
            st_ctime_ns=metadata.st_ctime_ns,
        )

    monkeypatch.setattr(resolution.os, "fstat", changing_fstat)
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="fingerprint"):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
            chunk_size=97,
        )


def test_streaming_scan_rejects_nonregular_and_symlink_sources(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    regular = tmp_path / "regular.json"
    size, raw_sha = _write_legacy(regular, raw)
    linked = tmp_path / "linked.json"
    linked.symlink_to(regular)
    directory = tmp_path / "directory.json"
    directory.mkdir()
    common = {
        "expected_byte_count": size,
        "expected_raw_sha256": raw_sha,
        "expected_root_content_sha256": _roots().content_sha256,
        "expected_attempt_order": (("loco-2dics", STREAM_A, "myopic_geometry"),),
        "expected_success_count": 0,
        "expected_failure_count": 1,
    }

    for candidate in (linked, directory):
        with pytest.raises(
            resolution.EconomicResolutionEvidenceError,
            match="regular non-symlink",
        ):
            resolution._scan_legacy_calibration_attempts(candidate, **common)


def test_streaming_scan_opens_both_passes_nofollow_and_nonblocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)
    real_open = resolution.os.open
    observed_flags: list[int] = []

    def recording_open(candidate, flags):  # type: ignore[no-untyped-def]
        observed_flags.append(flags)
        return real_open(candidate, flags)

    monkeypatch.setattr(resolution.os, "open", recording_open)
    resolution._scan_legacy_calibration_attempts(
        path,
        expected_byte_count=size,
        expected_raw_sha256=raw_sha,
        expected_root_content_sha256=_roots().content_sha256,
        expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
        expected_success_count=0,
        expected_failure_count=1,
    )

    assert len(observed_flags) == 2
    for flags in observed_flags:
        assert flags & resolution.os.O_NOFOLLOW
        assert flags & resolution.os.O_NONBLOCK


def test_streaming_scan_rejects_mutation_between_authenticated_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)
    real_framer = resolution._CalibrationAttemptArrayFramer

    def mutating_framer(*, max_attempt_object_bytes: int):
        metadata = path.stat()
        resolution.os.utime(
            path,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000),
        )
        return real_framer(max_attempt_object_bytes=max_attempt_object_bytes)

    monkeypatch.setattr(resolution, "_CalibrationAttemptArrayFramer", mutating_framer)
    with pytest.raises(
        resolution.EconomicResolutionEvidenceError,
        match="between authenticated passes",
    ):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
        )


def test_streaming_scan_hashes_and_rejects_changed_second_pass_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    attempt = _calibration_attempt(position=0, status="failure", stream_id=STREAM_A)
    raw = _legacy_artifact_bytes((attempt,))
    path = tmp_path / "legacy.json"
    size, raw_sha = _write_legacy(path, raw)
    real_read = resolution.os.read
    nonempty_reads = 0

    def changing_read(descriptor: int, count: int) -> bytes:
        nonlocal nonempty_reads
        data = real_read(descriptor, count)
        if data:
            nonempty_reads += 1
            if nonempty_reads == 2:
                return b"[" + data[1:]
        return data

    monkeypatch.setattr(resolution.os, "read", changing_read)
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="raw hash"):
        resolution._scan_legacy_calibration_attempts(
            path,
            expected_byte_count=size,
            expected_raw_sha256=raw_sha,
            expected_root_content_sha256=_roots().content_sha256,
            expected_attempt_order=(("loco-2dics", STREAM_A, "myopic_geometry"),),
            expected_success_count=0,
            expected_failure_count=1,
            chunk_size=size + 1,
        )


def test_calibration_checkpoint_supports_exactly_three_compact_outcomes() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    protocol = resolution.build_economic_resolution_protocol()
    roots = _official_roots()
    legacy_reference = _reference(resolution, position=0, status="success")
    replaced_success_reference = _reference(resolution, position=50, status="failure")
    replaced_failure_reference = _reference(resolution, position=51, status="failure")
    repaired_receipt = _repaired_receipt(
        resolution,
        position=replaced_success_reference.execution_position,
        corpus_id=replaced_success_reference.corpus_id,
        stream_id=replaced_success_reference.stream_id,
        policy_id="myopic_geometry",
    )

    legacy = resolution.build_gate3_calibration_attempt_checkpoint(
        protocol=protocol,
        roots=roots,
        execution_position=0,
        corpus_id="lectra-m3-m4",
        stream_id=legacy_reference.stream_id,
        policy_id="myopic_geometry",
        legacy_reference=legacy_reference,
    )
    repaired = resolution.build_gate3_calibration_attempt_checkpoint(
        protocol=protocol,
        roots=roots,
        execution_position=replaced_success_reference.execution_position,
        corpus_id="loco-2dics",
        stream_id=repaired_receipt.stream_id,
        policy_id="myopic_geometry",
        repaired_receipt=repaired_receipt,
        replaced_legacy_failure_reference=replaced_success_reference,
    )
    failure = resolution.build_gate3_calibration_attempt_checkpoint(
        protocol=protocol,
        roots=roots,
        execution_position=replaced_failure_reference.execution_position,
        corpus_id="loco-2dics",
        stream_id=replaced_failure_reference.stream_id,
        policy_id="myopic_geometry",
        replaced_legacy_failure_reference=replaced_failure_reference,
        failure_type="builtins.ValueError",
        failure_detail="repaired runtime preserved failure",
    )

    assert legacy.outcome_kind == "legacy_success_reference"
    assert legacy.legacy_reference == legacy_reference
    assert repaired.outcome_kind == "repaired_runtime_success"
    assert repaired.repaired_receipt == repaired_receipt
    assert repaired.replaced_legacy_failure_reference == replaced_success_reference
    assert failure.outcome_kind == "repaired_runtime_failure"
    assert failure.replaced_legacy_failure_reference == replaced_failure_reference
    assert failure.failure_type == "builtins.ValueError"
    assert len({legacy.checkpoint_id, repaired.checkpoint_id, failure.checkpoint_id}) == 3
    assert "material_replays" not in json.dumps(
        [item.model_dump(mode="json") for item in (legacy, repaired, failure)]
    )


def test_calibration_checkpoint_rejects_multiple_outcomes_and_binding_drift() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    reference = _reference(resolution, position=0, status="success")
    replacement = _reference(resolution, position=50, status="failure")
    receipt = _repaired_receipt(
        resolution,
        position=replacement.execution_position,
        corpus_id=replacement.corpus_id,
        stream_id=replacement.stream_id,
        policy_id="myopic_geometry",
    )
    common = {
        "protocol": resolution.build_economic_resolution_protocol(),
        "roots": _official_roots(),
        "execution_position": replacement.execution_position,
        "corpus_id": replacement.corpus_id,
        "stream_id": replacement.stream_id,
        "policy_id": "myopic_geometry",
    }

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="exactly one"):
        resolution.build_gate3_calibration_attempt_checkpoint(
            **common,
            legacy_reference=reference,
            repaired_receipt=receipt,
        )
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="binding"):
        resolution.build_gate3_calibration_attempt_checkpoint(
            **(
                common
                | {
                    "execution_position": reference.execution_position,
                    "corpus_id": reference.corpus_id,
                    "stream_id": STREAM_B,
                }
            ),
            legacy_reference=reference,
        )
    with pytest.raises(
        resolution.EconomicResolutionEvidenceError,
        match="replaced legacy failure",
    ):
        resolution.build_gate3_calibration_attempt_checkpoint(
            **common,
            repaired_receipt=receipt,
        )
    for wrong_replacement in (
        reference,
        _reference(resolution, position=51, status="failure"),
    ):
        with pytest.raises(
            resolution.EconomicResolutionEvidenceError,
            match="replaced legacy failure",
        ):
            resolution.build_gate3_calibration_attempt_checkpoint(
                **common,
                repaired_receipt=receipt,
                replaced_legacy_failure_reference=wrong_replacement,
            )
    legacy_lineage = receipt.model_copy(
        update={"source_lineage": "legacy_success_output_equivalent"}
    )
    semantic = legacy_lineage.model_dump(
        mode="python",
        round_trip=True,
        exclude={"receipt_id", "content_sha256"},
    )
    digest = semantic_sha256(semantic)
    legacy_lineage = legacy_lineage.model_copy(
        update={
            "receipt_id": f"yfm11g3calrcpt-{digest[:24]}",
            "content_sha256": f"sha256:{digest}",
        }
    )
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="repaired_runtime"):
        resolution.build_gate3_calibration_attempt_checkpoint(
            **common,
            repaired_receipt=legacy_lineage,
            replaced_legacy_failure_reference=replacement,
        )


def test_checkpoint_publish_load_is_canonical_idempotent_and_bound(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoint = _valid_checkpoints(resolution)[0]

    first = resolution.publish_gate3_calibration_attempt_checkpoint(tmp_path, checkpoint)
    second = resolution.publish_gate3_calibration_attempt_checkpoint(tmp_path, checkpoint)

    assert first == second
    assert first.name == (
        f"m11-economic-calibration-checkpoint-{checkpoint.execution_position:03d}-"
        f"{checkpoint.content_sha256.removeprefix('sha256:')}.json"
    )
    loaded = resolution.load_gate3_calibration_attempt_checkpoint(
        first,
        expected_protocol=checkpoint.protocol,
        expected_roots=checkpoint.roots,
        expected_execution_position=checkpoint.execution_position,
        expected_corpus_id=checkpoint.corpus_id,
        expected_stream_id=checkpoint.stream_id,
        expected_policy_id=checkpoint.policy_id,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        expected_content_sha256=checkpoint.content_sha256,
    )
    assert loaded == checkpoint

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="expected binding"):
        resolution.load_gate3_calibration_attempt_checkpoint(
            first,
            expected_protocol=checkpoint.protocol,
            expected_roots=checkpoint.roots,
            expected_execution_position=1,
            expected_corpus_id=checkpoint.corpus_id,
            expected_stream_id=checkpoint.stream_id,
            expected_policy_id=checkpoint.policy_id,
            expected_checkpoint_id=checkpoint.checkpoint_id,
            expected_content_sha256=checkpoint.content_sha256,
        )


def test_checkpoint_loader_rejects_tamper_wrong_path_and_symlink(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoint = _valid_checkpoints(resolution)[0]
    published = resolution.publish_gate3_calibration_attempt_checkpoint(
        tmp_path,
        checkpoint,
    )
    common = {
        "expected_protocol": checkpoint.protocol,
        "expected_roots": checkpoint.roots,
        "expected_execution_position": checkpoint.execution_position,
        "expected_corpus_id": checkpoint.corpus_id,
        "expected_stream_id": checkpoint.stream_id,
        "expected_policy_id": checkpoint.policy_id,
        "expected_checkpoint_id": checkpoint.checkpoint_id,
        "expected_content_sha256": checkpoint.content_sha256,
    }
    wrong = tmp_path / "wrong.json"
    wrong.write_bytes(published.read_bytes())
    linked = tmp_path / "linked.json"
    linked.symlink_to(published)
    tampered = tmp_path / published.name
    original = tampered.read_bytes()
    tampered.write_bytes(original.replace(b'"stream_id":', b'"forged": 1, "stream_id":', 1))

    for candidate in (wrong, linked, tampered):
        with pytest.raises(resolution.EconomicResolutionEvidenceError):
            resolution.load_gate3_calibration_attempt_checkpoint(candidate, **common)


def test_checkpoint_publish_rejects_foreign_bytes_and_loader_rejects_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoint = _valid_checkpoints(resolution)[0]
    destination = tmp_path / (
        f"m11-economic-calibration-checkpoint-{checkpoint.execution_position:03d}-"
        f"{checkpoint.content_sha256.removeprefix('sha256:')}.json"
    )
    destination.write_bytes(b"foreign")
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="publication"):
        resolution.publish_gate3_calibration_attempt_checkpoint(tmp_path, checkpoint)
    destination.unlink()
    published = resolution.publish_gate3_calibration_attempt_checkpoint(
        tmp_path,
        checkpoint,
    )
    real_fstat = resolution.os.fstat
    calls = 0

    def changing_fstat(descriptor: int):
        nonlocal calls
        metadata = real_fstat(descriptor)
        calls += 1
        if calls != 2:
            return metadata
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns + 1,
        )

    monkeypatch.setattr(resolution.os, "fstat", changing_fstat)
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="changed"):
        resolution.load_gate3_calibration_attempt_checkpoint(
            published,
            expected_protocol=checkpoint.protocol,
            expected_roots=checkpoint.roots,
            expected_execution_position=checkpoint.execution_position,
            expected_corpus_id=checkpoint.corpus_id,
            expected_stream_id=checkpoint.stream_id,
            expected_policy_id=checkpoint.policy_id,
            expected_checkpoint_id=checkpoint.checkpoint_id,
            expected_content_sha256=checkpoint.content_sha256,
        )


@pytest.mark.parametrize("malformation", ("duplicate", "nonfinite"))
def test_checkpoint_loader_rejects_duplicate_and_nonfinite_json(
    tmp_path: Path,
    malformation: str,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoint = _valid_checkpoints(resolution)[0]
    published = resolution.publish_gate3_calibration_attempt_checkpoint(
        tmp_path,
        checkpoint,
    )
    raw = published.read_bytes()
    if malformation == "duplicate":
        raw = raw.replace(
            b'"stream_id":',
            b'"stream_id": "duplicate",\n  "stream_id":',
            1,
        )
    else:
        raw = raw.replace(b'"stream_id":', b'"rogue": NaN,\n  "stream_id":', 1)
    published.write_bytes(raw)
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match=malformation):
        resolution.load_gate3_calibration_attempt_checkpoint(
            published,
            expected_protocol=checkpoint.protocol,
            expected_roots=checkpoint.roots,
            expected_execution_position=checkpoint.execution_position,
            expected_corpus_id=checkpoint.corpus_id,
            expected_stream_id=checkpoint.stream_id,
            expected_policy_id=checkpoint.policy_id,
            expected_checkpoint_id=checkpoint.checkpoint_id,
            expected_content_sha256=checkpoint.content_sha256,
        )


def test_complete_invalid_manifest_embeds_96_checkpoints_and_no_freezes() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoints = []
    for reference in _official_references(resolution):
        common = {
            "protocol": resolution.build_economic_resolution_protocol(),
            "roots": _official_roots(),
            "execution_position": reference.execution_position,
            "corpus_id": reference.corpus_id,
            "stream_id": reference.stream_id,
            "policy_id": reference.policy_id,
        }
        checkpoints.append(
            resolution.build_gate3_calibration_attempt_checkpoint(
                **common,
                **(
                    {"legacy_reference": reference}
                    if reference.status == "success"
                    else {
                        "replaced_legacy_failure_reference": reference,
                        "failure_type": reference.failure_type,
                        "failure_detail": reference.failure_detail,
                    }
                ),
            )
        )

    legacy_scan = resolution.build_official_legacy_calibration_scan(
        _official_references(resolution)
    )
    manifest = resolution.build_gate3_calibration_manifest(
        tuple(checkpoints),
        legacy_scan=legacy_scan,
    )

    assert manifest.status == "complete_invalid"
    assert manifest.success_count == 60
    assert manifest.failure_count == 36
    assert manifest.baseline_freezes == ()
    assert manifest.checkpoints == tuple(checkpoints)
    assert manifest.legacy_scan_id == legacy_scan.scan_id
    assert manifest.legacy_scan_content_sha256 == legacy_scan.content_sha256


def test_complete_valid_manifest_rederives_both_exact_baseline_freezes() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoints = _valid_checkpoints(resolution)
    legacy_scan = resolution.build_official_legacy_calibration_scan(
        _official_references(resolution)
    )
    manifest = resolution.build_gate3_calibration_manifest(
        checkpoints,
        legacy_scan=legacy_scan,
    )

    assert manifest.status == "complete_valid"
    assert manifest.success_count == 96
    assert manifest.failure_count == 0
    assert tuple(item.corpus_id for item in manifest.baseline_freezes) == (
        "lectra-m3-m4",
        "loco-2dics",
    )
    for corpus_id, stream_ids in manifest.calibration_stream_census:
        per_corpus = tuple(item for item in checkpoints if item.corpus_id == corpus_id)
        expected = select_gate3_baseline_policy(
            roots=_official_roots(),
            corpus_id=corpus_id,
            calibration_stream_ids=stream_ids,
            policy_stream_costs={
                policy_id: tuple(
                    (
                        item.legacy_reference.final_costs.net_cost  # type: ignore[union-attr]
                        if item.outcome_kind == "legacy_success_reference"
                        else item.repaired_receipt.final_costs.net_cost  # type: ignore[union-attr]
                    )
                    for item in per_corpus
                    if item.policy_id == policy_id
                )
                for policy_id in GATE3_BASELINE_POLICY_IDS
            },
            policy_stream_sheet_openings={
                policy_id: tuple(
                    (
                        item.legacy_reference.full_sheet_opening_count  # type: ignore[union-attr]
                        if item.outcome_kind == "legacy_success_reference"
                        else item.repaired_receipt.full_sheet_opening_count  # type: ignore[union-attr]
                    )
                    for item in per_corpus
                    if item.policy_id == policy_id
                )
                for policy_id in GATE3_BASELINE_POLICY_IDS
            },
            policy_invalid_stream_counts={policy_id: 0 for policy_id in GATE3_BASELINE_POLICY_IDS},
        )
        assert manifest.baseline_freezes[0 if corpus_id == "lectra-m3-m4" else 1] == expected


def test_manifest_rejects_order_mutation_and_publish_load_round_trip(
    tmp_path: Path,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoints = _valid_checkpoints(resolution)
    legacy_scan = resolution.build_official_legacy_calibration_scan(
        _official_references(resolution)
    )
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="order"):
        resolution.build_gate3_calibration_manifest(
            (checkpoints[1], checkpoints[0], *checkpoints[2:]),
            legacy_scan=legacy_scan,
        )

    manifest = resolution.build_gate3_calibration_manifest(
        checkpoints,
        legacy_scan=legacy_scan,
    )
    first = resolution.publish_gate3_calibration_manifest(tmp_path, manifest)
    second = resolution.publish_gate3_calibration_manifest(tmp_path, manifest)
    assert first == second
    assert first.name == (
        f"m11-economic-calibration-manifest-{manifest.content_sha256.removeprefix('sha256:')}.json"
    )
    assert (
        resolution.load_gate3_calibration_manifest(
            first,
            expected_protocol=manifest.protocol,
            expected_roots=manifest.roots,
            expected_manifest_id=manifest.manifest_id,
            expected_content_sha256=manifest.content_sha256,
        )
        == manifest
    )


def test_manifest_model_rejects_freeze_mutation_even_with_valid_nested_models() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    manifest = resolution.build_gate3_calibration_manifest(
        _valid_checkpoints(resolution),
        legacy_scan=resolution.build_official_legacy_calibration_scan(
            _official_references(resolution)
        ),
    )
    forged = manifest.model_copy(update={"baseline_freezes": ()})

    with pytest.raises(ValidationError, match="freezes differ"):
        resolution.Gate3CalibrationManifest.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )


def test_manifest_rejects_scan_whose_legacy_failure_reference_differs() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    references = _official_references(resolution)
    changed = _resign_reference(
        references[54],
        failure_detail="different authenticated legacy failure",
    )
    changed_scan = resolution.build_official_legacy_calibration_scan(
        (*references[:54], changed, *references[55:])
    )

    with pytest.raises(
        resolution.EconomicResolutionEvidenceError,
        match="legacy scan|legacy reference",
    ):
        resolution.build_gate3_calibration_manifest(
            _valid_checkpoints(resolution),
            legacy_scan=changed_scan,
        )


def _install_lineage_git_stub(
    monkeypatch: pytest.MonkeyPatch,
    resolution,  # type: ignore[no-untyped-def]
    *,
    repository_root: Path,
    source_raw: bytes,
    commit_exists: bool = True,
    commit_is_ancestor: bool = True,
    commit_blob_raw: bytes | None = None,
) -> None:
    blob_raw = source_raw if commit_blob_raw is None else commit_blob_raw
    blob_id = "a" * 40

    def fake_git(
        cwd: Path,
        arguments: tuple[str, ...],
        *,
        max_stdout_bytes: int,
        timeout_seconds: float = 10.0,
    ) -> SimpleNamespace:
        del max_stdout_bytes, timeout_seconds
        assert cwd == repository_root
        if arguments == ("rev-parse", "--show-toplevel"):
            return SimpleNamespace(
                returncode=0,
                stdout=f"{repository_root}\n".encode(),
                stderr=b"",
            )
        if arguments == (
            "cat-file",
            "-e",
            f"{resolution.REPAIR_COMMIT_SHA}^{{commit}}",
        ):
            return SimpleNamespace(
                returncode=0 if commit_exists else 1,
                stdout=b"",
                stderr=b"missing" if not commit_exists else b"",
            )
        if arguments == (
            "merge-base",
            "--is-ancestor",
            resolution.REPAIR_COMMIT_SHA,
            "HEAD",
        ):
            return SimpleNamespace(
                returncode=0 if commit_is_ancestor else 1,
                stdout=b"",
                stderr=b"",
            )
        if arguments == (
            "rev-parse",
            "--verify",
            (f"{resolution.REPAIR_COMMIT_SHA}:yf/src/yieldforge/baseline/geometry.py"),
        ):
            return SimpleNamespace(returncode=0, stdout=f"{blob_id}\n".encode(), stderr=b"")
        if arguments == ("cat-file", "-t", blob_id):
            return SimpleNamespace(returncode=0, stdout=b"blob\n", stderr=b"")
        if arguments == ("cat-file", "-s", blob_id):
            return SimpleNamespace(
                returncode=0,
                stdout=f"{len(blob_raw)}\n".encode(),
                stderr=b"",
            )
        if arguments == ("cat-file", "blob", blob_id):
            return SimpleNamespace(returncode=0, stdout=blob_raw, stderr=b"")
        raise AssertionError(f"unexpected git invocation: {arguments!r}")

    monkeypatch.setattr(resolution, "_run_git_bounded", fake_git)


def _prepare_lineage_repository(
    tmp_path: Path,
    resolution,  # type: ignore[no-untyped-def]
) -> tuple[Path, bytes]:
    repository_root = tmp_path / "repo"
    source_path = repository_root / "yf/src/yieldforge/baseline/geometry.py"
    source_path.parent.mkdir(parents=True)
    actual_repository_root = Path(resolution.__file__).resolve().parents[4]
    source_raw = (actual_repository_root / "yf/src/yieldforge/baseline/geometry.py").read_bytes()
    assert f"sha256:{hashlib.sha256(source_raw).hexdigest()}" == (
        resolution.REPAIRED_SOURCE_RAW_SHA256
    )
    source_path.write_bytes(source_raw)
    return repository_root, source_raw


def test_runtime_lineage_verifies_commit_ancestry_blob_and_current_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    repository_root, source_raw = _prepare_lineage_repository(tmp_path, resolution)
    _install_lineage_git_stub(
        monkeypatch,
        resolution,
        repository_root=repository_root,
        source_raw=source_raw,
    )

    assert (
        resolution.verify_economic_resolution_runtime_lineage(
            repository_root,
            resolution.build_economic_resolution_protocol(),
        )
        is None
    )


@pytest.mark.parametrize(
    ("commit_exists", "commit_is_ancestor", "expected"),
    (
        (False, True, "repair commit"),
        (True, False, "ancestor"),
    ),
)
def test_runtime_lineage_rejects_missing_or_nonancestor_repair_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    commit_exists: bool,
    commit_is_ancestor: bool,
    expected: str,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    repository_root, source_raw = _prepare_lineage_repository(tmp_path, resolution)
    _install_lineage_git_stub(
        monkeypatch,
        resolution,
        repository_root=repository_root,
        source_raw=source_raw,
        commit_exists=commit_exists,
        commit_is_ancestor=commit_is_ancestor,
    )

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match=expected):
        resolution.verify_economic_resolution_runtime_lineage(
            repository_root,
            resolution.build_economic_resolution_protocol(),
        )


def test_runtime_lineage_rejects_changed_commit_blob_current_source_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    repository_root, source_raw = _prepare_lineage_repository(tmp_path, resolution)
    protocol = resolution.build_economic_resolution_protocol()
    _install_lineage_git_stub(
        monkeypatch,
        resolution,
        repository_root=repository_root,
        source_raw=source_raw,
        commit_blob_raw=source_raw + b"changed",
    )
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="commit blob"):
        resolution.verify_economic_resolution_runtime_lineage(repository_root, protocol)

    _install_lineage_git_stub(
        monkeypatch,
        resolution,
        repository_root=repository_root,
        source_raw=source_raw,
    )
    source_path = repository_root / protocol.repaired_source_path
    source_path.write_bytes(source_raw + b"changed")
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="current source"):
        resolution.verify_economic_resolution_runtime_lineage(repository_root, protocol)

    source_path.unlink()
    target = repository_root / "geometry-target.py"
    target.write_bytes(source_raw)
    source_path.symlink_to(target)
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="non-symlink"):
        resolution.verify_economic_resolution_runtime_lineage(repository_root, protocol)


@pytest.mark.parametrize(
    "outcome_kind",
    ("legacy_success_reference", "repaired_runtime_success", "repaired_runtime_failure"),
)
def test_checkpoint_discovery_returns_none_or_exact_bound_checkpoint(
    tmp_path: Path,
    outcome_kind: str,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    valid = _valid_checkpoints(resolution)
    checkpoint = valid[0] if outcome_kind == "legacy_success_reference" else valid[54]
    reference = checkpoint.legacy_reference or checkpoint.replaced_legacy_failure_reference
    assert reference is not None
    if outcome_kind == "repaired_runtime_failure":
        checkpoint = resolution.build_gate3_calibration_attempt_checkpoint(
            protocol=checkpoint.protocol,
            roots=checkpoint.roots,
            execution_position=checkpoint.execution_position,
            corpus_id=checkpoint.corpus_id,
            stream_id=checkpoint.stream_id,
            policy_id=checkpoint.policy_id,
            replaced_legacy_failure_reference=reference,
            failure_type="builtins.RuntimeError",
            failure_detail="bounded repaired runtime failure",
        )
    assert (
        resolution.discover_gate3_calibration_attempt_checkpoint(
            tmp_path,
            protocol=checkpoint.protocol,
            legacy_reference=reference,
        )
        is None
    )

    published = resolution.publish_gate3_calibration_attempt_checkpoint(tmp_path, checkpoint)
    assert resolution.discover_gate3_calibration_attempt_checkpoint(
        tmp_path,
        protocol=checkpoint.protocol,
        legacy_reference=reference,
    ) == (published, checkpoint)


@pytest.mark.parametrize(
    "bad_kind",
    ("malformed", "bad_content", "symlink", "competing"),
)
def test_checkpoint_discovery_rejects_prefixed_malformed_or_competing_entries(
    tmp_path: Path,
    bad_kind: str,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoint = _valid_checkpoints(resolution)[0]
    reference = checkpoint.legacy_reference
    assert reference is not None
    prefix = f"m11-economic-calibration-checkpoint-{reference.execution_position:03d}-"
    if bad_kind == "malformed":
        (tmp_path / f"{prefix}not-a-hash.json").write_text("{}")
    elif bad_kind == "bad_content":
        (tmp_path / f"{prefix}{'a' * 64}.json").write_text("{}")
    elif bad_kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}")
        (tmp_path / f"{prefix}{'a' * 64}.json").symlink_to(target)
    else:
        resolution.publish_gate3_calibration_attempt_checkpoint(tmp_path, checkpoint)
        (tmp_path / f"{prefix}{'b' * 64}.json").write_text("{}")

    with pytest.raises(resolution.EconomicResolutionEvidenceError):
        resolution.discover_gate3_calibration_attempt_checkpoint(
            tmp_path,
            protocol=checkpoint.protocol,
            legacy_reference=reference,
        )


def test_checkpoint_discovery_rejects_outcome_that_does_not_match_legacy_reference(
    tmp_path: Path,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoint = _valid_checkpoints(resolution)[0]
    reference = checkpoint.legacy_reference
    assert reference is not None and reference.final_costs is not None
    changed_reference = _resign_reference(reference, full_sheet_opening_count=2)
    changed_checkpoint = resolution.build_gate3_calibration_attempt_checkpoint(
        protocol=checkpoint.protocol,
        roots=checkpoint.roots,
        execution_position=checkpoint.execution_position,
        corpus_id=checkpoint.corpus_id,
        stream_id=checkpoint.stream_id,
        policy_id=checkpoint.policy_id,
        legacy_reference=changed_reference,
    )
    resolution.publish_gate3_calibration_attempt_checkpoint(tmp_path, changed_checkpoint)

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="legacy reference"):
        resolution.discover_gate3_calibration_attempt_checkpoint(
            tmp_path,
            protocol=checkpoint.protocol,
            legacy_reference=reference,
        )


def test_manifest_discovery_returns_none_or_exact_rederived_manifest(tmp_path: Path) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoints = _valid_checkpoints(resolution)
    legacy_scan = resolution.build_official_legacy_calibration_scan(
        _official_references(resolution)
    )
    protocol = resolution.build_economic_resolution_protocol()
    assert (
        resolution.discover_gate3_calibration_manifest(
            tmp_path,
            protocol=protocol,
            legacy_scan=legacy_scan,
            checkpoints=checkpoints,
        )
        is None
    )

    manifest = resolution.build_gate3_calibration_manifest(
        checkpoints,
        legacy_scan=legacy_scan,
    )
    published = resolution.publish_gate3_calibration_manifest(tmp_path, manifest)
    assert resolution.discover_gate3_calibration_manifest(
        tmp_path,
        protocol=protocol,
        legacy_scan=legacy_scan,
        checkpoints=checkpoints,
    ) == (published, manifest)


@pytest.mark.parametrize(
    "bad_kind",
    ("malformed", "bad_content", "symlink", "competing"),
)
def test_manifest_discovery_rejects_prefixed_malformed_or_competing_entries(
    tmp_path: Path,
    bad_kind: str,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoints = _valid_checkpoints(resolution)
    legacy_scan = resolution.build_official_legacy_calibration_scan(
        _official_references(resolution)
    )
    protocol = resolution.build_economic_resolution_protocol()
    prefix = "m11-economic-calibration-manifest-"
    if bad_kind == "malformed":
        (tmp_path / f"{prefix}not-a-hash.json").write_text("{}")
    elif bad_kind == "bad_content":
        (tmp_path / f"{prefix}{'a' * 64}.json").write_text("{}")
    elif bad_kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}")
        (tmp_path / f"{prefix}{'a' * 64}.json").symlink_to(target)
    else:
        manifest = resolution.build_gate3_calibration_manifest(
            checkpoints,
            legacy_scan=legacy_scan,
        )
        resolution.publish_gate3_calibration_manifest(tmp_path, manifest)
        (tmp_path / f"{prefix}{'b' * 64}.json").write_text("{}")

    with pytest.raises(resolution.EconomicResolutionEvidenceError):
        resolution.discover_gate3_calibration_manifest(
            tmp_path,
            protocol=protocol,
            legacy_scan=legacy_scan,
            checkpoints=checkpoints,
        )


def test_manifest_discovery_rejects_manifest_not_equal_to_rederived_state(
    tmp_path: Path,
) -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    checkpoints = _valid_checkpoints(resolution)
    legacy_scan = resolution.build_official_legacy_calibration_scan(
        _official_references(resolution)
    )
    reference = legacy_scan.attempt_references[54]
    mismatched_checkpoint = resolution.build_gate3_calibration_attempt_checkpoint(
        protocol=checkpoints[54].protocol,
        roots=checkpoints[54].roots,
        execution_position=checkpoints[54].execution_position,
        corpus_id=checkpoints[54].corpus_id,
        stream_id=checkpoints[54].stream_id,
        policy_id=checkpoints[54].policy_id,
        replaced_legacy_failure_reference=reference,
        failure_type="builtins.RuntimeError",
        failure_detail="bounded repaired runtime failure",
    )
    mismatched = (*checkpoints[:54], mismatched_checkpoint, *checkpoints[55:])
    manifest = resolution.build_gate3_calibration_manifest(
        mismatched,
        legacy_scan=legacy_scan,
    )
    resolution.publish_gate3_calibration_manifest(tmp_path, manifest)

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="rederived"):
        resolution.discover_gate3_calibration_manifest(
            tmp_path,
            protocol=resolution.build_economic_resolution_protocol(),
            legacy_scan=legacy_scan,
            checkpoints=checkpoints,
        )
