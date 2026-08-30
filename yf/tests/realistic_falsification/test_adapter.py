from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import yieldforge.realistic_falsification.adapter as adapter_module
from yieldforge.baseline.replay import enumerate_m7_action_catalog, initial_m7_cursor
from yieldforge.realistic_falsification.adapter import (
    AdapterEvidenceError,
    M11M7AdapterContext,
    _adapter_context_from_authenticated,
    load_authenticated_adapter_context,
    project_exact_audit,
    project_hard_null,
    project_stream,
)
from yieldforge.realistic_falsification.geometry_gate import (
    Gate2EvidenceError,
    _load_official_gate2_context,
)
from yieldforge.realistic_falsification.pack import visible_event_positions
from yieldforge.realistic_falsification.runner import M11Gate1RunArtifact

_ROOT = Path(__file__).parents[2]
_GATE1_RUN = _ROOT / "experiments/results/m11-gate1-yfm11g1run-c35f10fa4f4d7b6b01c59c29.json"


@pytest.fixture(scope="module")
def official_geometry_context():
    return _load_official_gate2_context(_ROOT)


@pytest.fixture(scope="module")
def official_gate1_result():
    return M11Gate1RunArtifact.model_validate_json(
        _GATE1_RUN.read_bytes(), strict=True
    ).gate1_result


def _gate2_root(geometry, gate1, **updates):
    values = {
        "result_id": "yfm11g2r-" + "2" * 24,
        "content_sha256": "sha256:" + "3" * 64,
        "status": "gate_2_survived",
        "evaluation_stage": "stage_b_exact_attempted",
        "opens_gate_3": True,
        "population_id": gate1.population_id,
        "population_content_sha256": gate1.population_content_sha256,
        "source_manifest_id": gate1.source_manifest_id,
        "source_manifest_content_sha256": gate1.source_manifest_content_sha256,
        "gate1_result_id": gate1.result_id,
        "gate1_result_content_sha256": gate1.content_sha256,
        "m0_contract_id": geometry.m0.contract_id,
        "m0_contract_content_sha256": geometry.m0.content_sha256,
        "m3_input_id": geometry.gate1.m3_input.input_id,
        "m3_input_content_sha256": geometry.gate1.m3_input.content_sha256,
        "m4_input_id": geometry.m4.input_id,
        "m4_input_content_sha256": geometry.m4.content_sha256,
        "loco_catalog_id": geometry.gate1.loco_catalog.catalog_id,
        "loco_catalog_content_sha256": geometry.gate1.loco_catalog.content_sha256,
    }
    values.update(updates)
    return SimpleNamespace(**values)


@pytest.fixture(scope="module")
def adapter_context(official_geometry_context, official_gate1_result):
    return _adapter_context_from_authenticated(
        repository_root=_ROOT,
        geometry_context=official_geometry_context,
        gate1_result=official_gate1_result,
        gate2_result=_gate2_root(official_geometry_context, official_gate1_result),
    )


def _population(context):
    return context.population


def test_real_lectra_and_loco_audit_slices_reconstruct_every_candidate_option(
    adapter_context,
) -> None:
    population = _population(adapter_context)
    for corpus_id, expected_candidate_count in (
        ("lectra-m3-m4", 2),
        ("loco-2dics", 1),
    ):
        audit = next(
            item
            for item in population.exact_audits
            if item.corpus_id == corpus_id and item.economic_arm == "central"
        )
        projections = project_exact_audit(adapter_context, audit.audit_id, "central")
        assert sum(len(item.source_event_map) for item in projections) == 3
        assert {
            mapping.source_event_position
            for item in projections
            for mapping in item.source_event_map
        } == set(audit.event_positions)
        assert all(item.attestation.registration_kind == "exact_audit" for item in projections)
        assert all(
            item.attestation.registered_exact_audit_arm == audit.economic_arm
            for item in projections
        )
        for item in projections:
            for parity in item.candidate_action_parity:
                assert len(parity.projected_candidate_ids) == expected_candidate_count
                assert set(parity.projected_candidate_ids) == set(parity.runtime_candidate_ids)
                assert parity.standard_action_ids == tuple(
                    f"m7-standard:{candidate_id}" for candidate_id in parity.runtime_candidate_ids
                )
        first = projections[0]
        catalog = enumerate_m7_action_catalog(
            first.runtime,
            cursor=initial_m7_cursor(first.runtime.replay_input),
        )
        assert tuple(action.action_id for action in catalog.actions) == (
            first.candidate_action_parity[0].standard_action_ids
        )


def test_stream_projection_is_material_additive_and_rates_are_per_area_hour(
    adapter_context,
) -> None:
    population = _population(adapter_context)
    stream = next(
        item
        for item in population.streams
        if item.corpus_id == "lectra-m3-m4"
        and item.partition == "calibration"
        and item.stream_kind == "primary"
    )
    projections = project_stream(adapter_context, stream.stream_id, "central")
    mapped = tuple(
        mapping.source_event_position for item in projections for mapping in item.source_event_map
    )
    assert sorted(mapped) == list(range(24))
    assert len(mapped) == len(set(mapped))
    assert all(
        {binding.material.material_code for binding in item.runtime.replay_input.instances}
        == {item.material_key}
        for item in projections
    )
    global_horizon = max(
        datetime.strptime(item.due_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        for item in stream.events
    )
    assert {item.runtime.replay_input.horizon_end for item in projections} == {global_horizon}
    for item in projections:
        assert item.rates.purchase_cost_per_area == pytest.approx(100.0 / item.reference_area)
        assert item.rates.scrap_credit_per_area == pytest.approx(10.0 / item.reference_area)
        assert item.rates.storage_cost_per_area_hour == pytest.approx(
            0.5 / (item.reference_area * 30.0 * 24.0)
        )
        assert item.attestation.compatibility_dto_only is True
        assert item.attestation.native_m7_evidence_persistence_authorized is False


def test_known_visible_prefix_is_filtered_to_the_local_material_substream(
    adapter_context,
) -> None:
    population = _population(adapter_context)
    stream = next(
        item
        for item in population.streams
        if item.corpus_id == "lectra-m3-m4"
        and item.partition == "calibration"
        and item.stream_kind == "primary"
    )
    for projection in project_stream(adapter_context, stream.stream_id, "adverse"):
        local_by_source = {
            item.source_event_position: item.local_event_position
            for item in projection.source_event_map
        }
        for prefix in projection.known_visible_local_prefixes:
            source_event = stream.events[prefix.source_as_of_event_position]
            expected = tuple(
                local_by_source[position]
                for position in visible_event_positions(stream, source_event.released_at)
                if position in local_by_source
            )
            assert prefix.visible_local_event_positions == expected


def test_shuffled_twin_and_all_three_hard_null_kinds_remain_distinct(
    adapter_context,
) -> None:
    population = _population(adapter_context)
    twin = next(item for item in population.streams if item.stream_kind == "shuffled_twin")
    twin_projections = project_stream(adapter_context, twin.stream_id, "central")
    assert len(twin_projections) == 24
    assert all(item.attestation.registration_kind == "shuffled_twin" for item in twin_projections)

    lectra_nulls = tuple(item for item in population.hard_nulls if item.corpus_id == "lectra-m3-m4")
    assert {item.null_kind for item in lectra_nulls} == {
        "single_action",
        "unique_materials_single_action",
        "all_work_known_single_action",
    }
    for control in lectra_nulls:
        projections = project_hard_null(adapter_context, control.null_id, "central")
        assert sum(len(item.source_event_map) for item in projections) == 3
        assert all(item.attestation.control_kind == control.null_kind for item in projections)
        assert all(
            len(parity.projected_candidate_ids) == 1
            for item in projections
            for parity in item.candidate_action_parity
        )
        if control.unique_material_per_event:
            assert len(projections) == 3
        if control.all_work_known:
            assert all(
                prefix.visible_local_event_positions == tuple(range(len(item.source_event_map)))
                for item in projections
                for prefix in item.known_visible_local_prefixes
            )


def test_projection_is_deterministic_and_tampered_jagua_falls_back(
    official_geometry_context,
    official_gate1_result,
    tmp_path: Path,
) -> None:
    tampered = tmp_path / "yieldforge-m7-jagua-spike"
    tampered.write_bytes(b"not-the-pinned-binary")
    tampered.chmod(0o755)
    geometry = replace(official_geometry_context, jagua_executable=tampered)
    context = _adapter_context_from_authenticated(
        repository_root=_ROOT,
        geometry_context=geometry,
        gate1_result=official_gate1_result,
        gate2_result=_gate2_root(geometry, official_gate1_result),
    )
    audit = next(item for item in context.population.exact_audits if item.corpus_id == "loco-2dics")
    first = project_exact_audit(context, audit.audit_id, "adverse")
    second = project_exact_audit(context, audit.audit_id, "adverse")
    assert tuple(item.attestation for item in first) == tuple(item.attestation for item in second)
    assert all(item.runtime.jagua_executable is None for item in first)
    assert all(
        item.runtime.replay_input.collision_backend == "shapely_authoritative" for item in first
    )


def test_authenticated_loader_fails_closed_on_tamper_and_source_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    official_geometry_context,
    official_gate1_result,
) -> None:
    gate2 = _gate2_root(official_geometry_context, official_gate1_result)
    monkeypatch.setattr(
        adapter_module,
        "authenticate_official_gate1_evaluation",
        lambda result, *, repository_root: result,
    )
    monkeypatch.setattr(
        adapter_module,
        "_load_official_gate2_context",
        lambda repository_root: official_geometry_context,
    )

    def reject_gate2(*args, **kwargs):
        raise Gate2EvidenceError("tampered Gate 2")

    monkeypatch.setattr(
        adapter_module,
        "authenticate_official_gate2_evaluation",
        reject_gate2,
    )
    with pytest.raises(AdapterEvidenceError, match="Gate 2"):
        load_authenticated_adapter_context(_ROOT, official_gate1_result, gate2)

    monkeypatch.setattr(
        adapter_module,
        "authenticate_official_gate2_evaluation",
        lambda result, *, repository_root, gate1_result: result,
    )
    mismatched = _gate2_root(
        official_geometry_context,
        official_gate1_result,
        population_content_sha256="sha256:" + "9" * 64,
    )
    with pytest.raises(AdapterEvidenceError, match="root"):
        load_authenticated_adapter_context(_ROOT, official_gate1_result, mismatched)

    forged = M11M7AdapterContext(
        repository_root=_ROOT,
        gate1_result=official_gate1_result,
        gate2_result=gate2,
        geometry_context=official_geometry_context,
    )
    stream_id = forged.population.streams[0].stream_id
    with pytest.raises(AdapterEvidenceError, match="authenticated authority"):
        project_stream(forged, stream_id, "central")
