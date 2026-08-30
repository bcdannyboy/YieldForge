from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
        "failure_detail": (
            None if observation else 'escaped quote: " and braces: {not an object}'
        ),
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
        failure_detail=(
            None if observation else 'escaped quote: " and braces: {not an object}'
        ),
    )


def _legacy_artifact_bytes(attempts: tuple[Gate3CalibrationAttempt, ...]) -> bytes:
    return (
        json.dumps(
            {
                "content_sha256": "sha256:" + "9" * 64,
                "result": {
                    "calibration_attempts": [
                        item.model_dump(mode="json") for item in attempts
                    ]
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
        "source_lineage": (
            "legacy_success_output_equivalent" if status == "success" else None
        ),
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
            status=(
                "success"
                if position < 48 or (position - 48) % 8 < 2
                else "failure"
            ),
        )
        for position in range(96)
    )


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
    assert protocol.bootstrap_seed == 0
    assert protocol.bootstrap_resamples == 10_000
    assert protocol.central_full_future_mean_min_percent == "2.500000000000"
    assert (
        protocol.central_unknown_future_contribution_min_percentage_points
        == "1.500000000000"
    )
    assert protocol.causal_known_only_mean_min_percent == "1.500000000000"
    assert protocol.lower_confidence_bound_rule == "strictly_greater_than_zero"
    assert protocol.median_savings_rule == "strictly_greater_than_zero"
    assert protocol.positive_stream_fraction_rule == "strictly_greater_than_one_half"
    assert not any(
        fragment in key
        for key in type(protocol).model_fields
        for fragment in ("outcome", "attempt", "verdict", "status")
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
    order = tuple(
        (item.corpus_id, item.stream_id, item.policy_id) for item in attempts
    )

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
        "expected_attempt_order": (
            ("loco-2dics", STREAM_A, "myopic_geometry"),
        ),
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
        kwargs["expected_attempt_order"] = (
            ("loco-2dics", "wrong-stream", "myopic_geometry"),
        )
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

    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="order"):
        resolution.build_official_legacy_calibration_scan(
            (references[1], references[0], *references[2:])
        )
    changed_status = _reference(resolution, position=54, status="success")
    with pytest.raises(resolution.EconomicResolutionEvidenceError, match="status census"):
        resolution.build_official_legacy_calibration_scan(
            (*references[:54], changed_status, *references[55:])
        )


def test_legacy_reference_rejects_attempt_and_observation_sha_prefix_forgery() -> None:
    from yieldforge.realistic_falsification import economic_resolution as resolution

    reference = _reference(resolution, position=0, status="success")
    forged_attempt = reference.model_copy(
        update={"attempt_id": "yfm11g3calatt-" + "f" * 24}
    )
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
        b'"run_id": "synthetic calibration_attempts marker",\n'
        b'  "calibration_attempts": []',
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
