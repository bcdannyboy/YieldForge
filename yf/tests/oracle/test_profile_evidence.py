from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.profile_evidence import M8PortableHotspotProfileV2

_OFFICIAL_GATE3_ID = "yfm8gate3-ea8a12969396172d7dbc4774"
_OFFICIAL_GATE3_SHA256 = "sha256:ea8a12969396172d7dbc4774bd239532e2907e637ddb44b1d5505c7b9011d117"
_FROZEN_RUNTIME_SHA256 = (
    "sha256:bce0d552132a3de7ca12eb98599800e34a0d78cf8e0bc5440efb7faa28a45508"
)


def _counts() -> dict[str, int]:
    return {
        "events": 0,
        "candidates": 0,
        "frontier_entries": 13,
        "actions": 0,
        "facts": 0,
        "fallbacks": 0,
        "frontier_rejected_transitions": 0,
        "standard_only_materializations": 0,
        "full_authoritative_fallbacks": 0,
        "differential_mismatches": 0,
        "partially_pruned_transitions": 0,
        "frontier_rejected_inventory_items": 0,
        "exact_survivor_inventory_items": 0,
        "counted_no_fit_transitions": 0,
        "counted_no_fit_inventory_items": 0,
        "counted_no_fit_candidate_searches": 0,
    }


_GENERATOR_PHASE_NAMES = (
    "fact_bundle_generator_authority_reconstruction",
    "fact_bundle_generation",
    "fact_bundle_prepared_context_session",
    "action_catalog_enumeration",
    "standard_layout_materialization",
    "fact_bundle_unchecked_traversal",
    "fact_bundle_layer_assembly",
    "fact_bundle_hash_validation",
    "fact_bundle_semantic_serialization",
    "fact_bundle_strict_roundtrip",
    "fact_bundle_handoff_serialization",
    "fact_bundle_telemetry",
    "scalar_frontier_construction",
)
_CHECKER_PHASE_NAMES = (
    "fact_bundle_metadata_reconciliation",
    "fact_bundle_authority_reconstruction",
    "fact_bundle_request_snapshot",
    "fact_bundle_strict_load",
    "fact_bundle_authority_session",
    "fact_bundle_context_index_preparation",
    "standard_layout_materialization",
    "fact_bundle_common_verification",
    "counted_translation_audit_call",
    "fact_bundle_capability_registration",
    "fact_bundle_action_traversal",
    "fact_bundle_cleanup",
    "fact_bundle_result_materialization",
    "fact_bundle_request_stability",
    "fact_bundle_request_stability",
    "scalar_frontier_construction",
    "scalar_frontier_construction",
)


def _profile_report(phase_names: tuple[str, ...]) -> dict[str, object]:
    accounted = len(phase_names) * 9
    total = len(phase_names) * 10
    return {
        "schema_version": "yieldforge.m8-phase-profile.v2",
        "total_process_ns": total,
        "total_wall_ns": total,
        "accounted_process_ns": accounted,
        "accounted_process_fraction": 0.9,
        "accounted_wall_ns": accounted,
        "unattributed_wall_ns": total - accounted,
        "accounted_wall_fraction": 0.9,
        "counts": _counts(),
        "phases": tuple(
            {
                "name": name,
                "process_ns": 9,
                "wall_ns": 9,
                "children": (),
            }
            for name in phase_names
        ),
    }


def _generation_identity() -> dict[str, object]:
    return {
        "regime": "regime_shift",
        "temporal_seed": 2026082300,
        "stream_id": "yfts-f320978a2d55802395294150",
        "event_count": 2,
        "bundle_sha256": (
            "sha256:207a5fb36ae58a42e7f06e61de94df8b6b09dd613578de247778218cf06bb99f"
        ),
        "semantic_bundle_bytes_sha256": (
            "sha256:6638999ad1ee81d78f8e795dff97ecf081c08b87a63ffd111302a80dd34cec18"
        ),
        "semantic_serialized_bytes": 43_520_933,
        "fixed_layer_node_count": 2_297,
        "translation_batch_count": 459,
        "candidate_scalar_fact_count": 459,
        "frontier_fact_count": 1,
        "standard_candidate_fact_count": 459,
        "common_lemma_count": 1,
        "influence_fact_count": 459,
        "action_root_count": 459,
        "counted_inventory_evidence_count": 1,
        "counted_search_lemma_count": 1,
    }


def _checked_identity() -> dict[str, object]:
    return {
        **_generation_identity(),
        "checked_common_lemma_count": 1,
        "checked_influence_fact_count": 459,
        "checked_action_root_count": 459,
        "counted_translation_audit_count": 1,
        "counted_translation_audit_call_count": 1,
        "influence_translation_audit_count": 0,
        "total_exact_fallback_count": 0,
        "decision_id": "yfm8d-7a2c333838e80808a29764cd",
        "decision_content_sha256": (
            "sha256:7a2c333838e80808a29764cd6c3ace49a0a457507045a5b2b0522a88ba8cd571"
        ),
        "failure_code": "valid_action_decision",
    }


def _handoff() -> dict[str, float]:
    return {
        "task_serialization_wall_seconds": 0.01,
        "result_serialization_wall_seconds": 0.02,
        "worker_payload_handoff_wall_seconds": 0.03,
        "process_exit_validation_wall_seconds": 0.04,
    }


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    semantic = {
        key: value for key, value in payload.items() if key not in {"profile_id", "content_sha256"}
    }
    digest = semantic_sha256(semantic)
    payload["profile_id"] = f"yfm8profile-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return payload


def valid_profile_payload() -> dict[str, object]:
    generation = _generation_identity()
    payload: dict[str, object] = {
        "schema_version": "yieldforge.m8-portable-hotspot-profile.v2",
        "official_gate3_id": _OFFICIAL_GATE3_ID,
        "official_gate3_content_sha256": _OFFICIAL_GATE3_SHA256,
        "regime": "regime_shift",
        "temporal_seed": 2026082300,
        "stream_id": generation["stream_id"],
        "event_count": 2,
        "official_identity_match": True,
        "repeated_output_identity_match": True,
        "first_generation_identity": deepcopy(generation),
        "repeat_generation_identity": deepcopy(generation),
        "identity": _checked_identity(),
        "profile_implementation_id": "yieldforge-m8-portable-profile-v1",
        "profile_implementation_content_sha256": "sha256:" + "4" * 64,
        "runtime_id": "yieldforge-m8-gate3-runtime-v1",
        "runtime_content_sha256": _FROZEN_RUNTIME_SHA256,
        "generator_runtime_content_sha256": _FROZEN_RUNTIME_SHA256,
        "repeat_generator_runtime_content_sha256": _FROZEN_RUNTIME_SHA256,
        "checker_runtime_content_sha256": _FROZEN_RUNTIME_SHA256,
        "runtime_attested_workers": True,
        "source_attested_workers": True,
        "fresh_pycache_scope": True,
        "generator_worker_pid": 101,
        "repeat_generator_worker_pid": 102,
        "checker_worker_pid": 103,
        "fresh_worker_identity": True,
        "generator_worker_wall_seconds": 1.0,
        "repeat_generator_worker_wall_seconds": 2.0,
        "checker_worker_wall_seconds": 3.0,
        "core_generation_plus_checker_worker_wall_seconds": 4.0,
        "repeated_generation_plus_checker_worker_wall_seconds": 6.0,
        "first_generation_phase_wall_seconds": 1.1,
        "second_generation_phase_wall_seconds": 2.1,
        "checker_phase_wall_seconds": 3.1,
        "total_pipeline_wall_seconds": 6.4,
        "timing_semantics": "controller_phase_and_worker_operation_wall_v1",
        "generator_profile": _profile_report(_GENERATOR_PHASE_NAMES),
        "checker_profile": _profile_report(_CHECKER_PHASE_NAMES),
        "generator_accounted_wall_fraction": 0.9,
        "checker_accounted_wall_fraction": 0.9,
        "minimum_accounted_wall_fraction": 0.9,
        "measurement_complete": True,
        "measurement_decision": "profile_complete",
        "generator_handoff": _handoff(),
        "repeat_generator_handoff": _handoff(),
        "checker_handoff": _handoff(),
        "configured_outer_process_count": 1,
        "nested_translation_audit_processes": 2,
        "peak_compute_count": 2,
        "compute_slot_cap": 8,
        "evaluation_accessed": False,
        "official_six_cell_calibration_authorized": False,
        "claim_ceiling": (
            "calibration_hotspot_measurement_only_not_gate3_performance_pass_"
            "m8_advantage_savings_physical_or_commercial_evidence"
        ),
    }
    return _rehash(payload)


def test_valid_portable_hotspot_profile_is_strict_and_content_addressed() -> None:
    result = M8PortableHotspotProfileV2.model_validate(
        valid_profile_payload(),
        strict=True,
    )

    assert result.schema_version == "yieldforge.m8-portable-hotspot-profile.v2"
    assert result.first_generation_identity == result.repeat_generation_identity
    assert result.identity.checked_action_root_count == 459


def test_portable_hotspot_profile_rejects_official_gate3_identity_drift() -> None:
    payload = valid_profile_payload()
    payload["official_gate3_id"] = "yfm8gate3-" + "0" * 24
    _rehash(payload)

    with pytest.raises(ValidationError, match="official_gate3_id"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


def test_portable_hotspot_profile_rejects_repeat_generation_mismatch() -> None:
    payload = valid_profile_payload()
    repeat = payload["repeat_generation_identity"]
    assert isinstance(repeat, dict)
    repeat["bundle_sha256"] = "sha256:" + "0" * 64
    _rehash(payload)

    with pytest.raises(ValidationError, match="repeat_generation_identity.bundle_sha256"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


def test_portable_hotspot_profile_rejects_coherently_rehashed_arbitrary_identity() -> None:
    payload = valid_profile_payload()
    for field_name in (
        "first_generation_identity",
        "repeat_generation_identity",
        "identity",
    ):
        identity = payload[field_name]
        assert isinstance(identity, dict)
        identity["bundle_sha256"] = "sha256:" + "0" * 64
    _rehash(payload)

    with pytest.raises(ValidationError, match="bundle_sha256"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


def test_portable_hotspot_profile_rejects_profile_accounting_drift() -> None:
    payload = valid_profile_payload()
    report = payload["generator_profile"]
    assert isinstance(report, dict)
    report["accounted_wall_ns"] = 179
    _rehash(payload)

    with pytest.raises(ValidationError, match="accounted wall duration"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


def test_portable_hotspot_profile_requires_complete_hotspot_phase_contract() -> None:
    payload = valid_profile_payload()
    report = payload["generator_profile"]
    assert isinstance(report, dict)
    phases = report["phases"]
    assert isinstance(phases, tuple)
    report["phases"] = tuple(
        item for item in phases if item["name"] != "action_catalog_enumeration"
    )
    report["accounted_process_ns"] -= 9
    report["accounted_wall_ns"] -= 9
    report["unattributed_wall_ns"] += 9
    report["accounted_process_fraction"] = (
        report["accounted_process_ns"] / report["total_process_ns"]
    )
    report["accounted_wall_fraction"] = report["accounted_wall_ns"] / report["total_wall_ns"]
    payload["generator_accounted_wall_fraction"] = report["accounted_wall_fraction"]
    payload["measurement_complete"] = False
    payload["measurement_decision"] = "profile_incomplete"
    _rehash(payload)

    with pytest.raises(ValidationError, match="required phase"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "field_name",
    (
        "generator_runtime_content_sha256",
        "repeat_generator_runtime_content_sha256",
        "checker_runtime_content_sha256",
    ),
)
def test_portable_hotspot_profile_rejects_worker_runtime_identity_drift(
    field_name: str,
) -> None:
    payload = valid_profile_payload()
    payload[field_name] = "sha256:" + "0" * 64
    _rehash(payload)

    with pytest.raises(ValidationError, match="worker runtime identity"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


def test_portable_hotspot_profile_rejects_coherent_frozen_runtime_drift() -> None:
    payload = valid_profile_payload()
    drift = "sha256:" + "0" * 64
    payload["runtime_content_sha256"] = drift
    payload["generator_runtime_content_sha256"] = drift
    payload["repeat_generator_runtime_content_sha256"] = drift
    payload["checker_runtime_content_sha256"] = drift
    _rehash(payload)

    with pytest.raises(ValidationError, match="runtime_content_sha256"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("role", "phase_name"),
    (
        ("generator", "action_catalog_enumeration"),
        ("checker", "fact_bundle_request_stability"),
    ),
)
def test_portable_hotspot_profile_rejects_duplicate_required_phase_occurrence(
    role: str,
    phase_name: str,
) -> None:
    payload = valid_profile_payload()
    report = payload[f"{role}_profile"]
    assert isinstance(report, dict)
    phases = report["phases"]
    assert isinstance(phases, tuple)
    duplicate = next(item for item in phases if item["name"] == phase_name)
    report["phases"] = (*phases, deepcopy(duplicate))
    report["accounted_process_ns"] += 9
    report["accounted_wall_ns"] += 9
    report["unattributed_wall_ns"] -= 9
    report["accounted_process_fraction"] = (
        report["accounted_process_ns"] / report["total_process_ns"]
    )
    report["accounted_wall_fraction"] = (
        report["accounted_wall_ns"] / report["total_wall_ns"]
    )
    payload[f"{role}_accounted_wall_fraction"] = report[
        "accounted_wall_fraction"
    ]
    _rehash(payload)

    with pytest.raises(ValidationError, match="required phase"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


def test_portable_hotspot_profile_rejects_unexpected_profile_phase() -> None:
    payload = valid_profile_payload()
    report = payload["generator_profile"]
    assert isinstance(report, dict)
    phases = report["phases"]
    assert isinstance(phases, tuple)
    report["phases"] = (
        *phases,
        {
            "name": "unexpected_profile_phase",
            "process_ns": 1,
            "wall_ns": 1,
            "children": (),
        },
    )
    report["accounted_process_ns"] += 1
    report["accounted_wall_ns"] += 1
    report["unattributed_wall_ns"] -= 1
    report["accounted_process_fraction"] = (
        report["accounted_process_ns"] / report["total_process_ns"]
    )
    report["accounted_wall_fraction"] = (
        report["accounted_wall_ns"] / report["total_wall_ns"]
    )
    payload["generator_accounted_wall_fraction"] = report[
        "accounted_wall_fraction"
    ]
    _rehash(payload)

    with pytest.raises(ValidationError, match="required phase"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        (
            "core_generation_plus_checker_worker_wall_seconds",
            4.5,
            "core worker wall sum",
        ),
        (
            "repeated_generation_plus_checker_worker_wall_seconds",
            6.5,
            "repeated worker wall sum",
        ),
        (
            "first_generation_phase_wall_seconds",
            0.9,
            "phase excludes worker time",
        ),
        ("total_pipeline_wall_seconds", 6.0, "pipeline wall time"),
    ),
)
def test_portable_hotspot_profile_rejects_timing_reconciliation_drift(
    field_name: str,
    value: float,
    message: str,
) -> None:
    payload = valid_profile_payload()
    payload[field_name] = value
    _rehash(payload)

    with pytest.raises(ValidationError, match=message):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "field_name",
    (
        "profile_implementation_id",
        "profile_implementation_content_sha256",
        "runtime_id",
        "runtime_content_sha256",
    ),
)
def test_portable_hotspot_profile_requires_source_and_runtime_identity(
    field_name: str,
) -> None:
    payload = valid_profile_payload()
    payload.pop(field_name)
    _rehash(payload)

    with pytest.raises(ValidationError, match=field_name):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)


def test_portable_hotspot_profile_rejects_content_hash_drift() -> None:
    payload = valid_profile_payload()
    handoff = payload["generator_handoff"]
    assert isinstance(handoff, dict)
    handoff["task_serialization_wall_seconds"] = 0.015

    with pytest.raises(ValidationError, match="content SHA-256"):
        M8PortableHotspotProfileV2.model_validate(payload, strict=True)
