from __future__ import annotations

from dataclasses import replace
from itertools import product

import pytest

from tests.oracle.fixtures import two_problem_runtime
from yieldforge.baseline.policies import M7PolicyName, policy_identity
from yieldforge.baseline.replay import (
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_semantic_runtime_sha256,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.adapter import (
    M11CandidateActionParity,
    M11KnownVisibleLocalPrefix,
    M11M7ProjectionAttestation,
    M11MaterialRuntimeProjection,
    M11SourceEventMap,
)
from yieldforge.realistic_falsification.confirmation import (
    build_gate3_cost_ledger,
    build_gate3_decision_trace,
    build_gate3_root_binding,
    build_gate3_shard_trace,
)
from yieldforge.realistic_falsification.confirmation import (
    gate3_inventory_sha256 as confirmation_inventory_sha256,
)
from yieldforge.realistic_falsification.gate3_backend import (
    _six_place,
    build_gate3_known_only_runtime,
    execute_gate3_material_shard,
    gate3_action_catalog_sha256,
    gate3_compute_budget_sha256,
    gate3_inventory_sha256,
    gate3_ledger_from_replay,
    gate3_policy_identity,
    gate3_search_config_sha256,
    merge_gate3_shard_traces,
    verify_gate3_exact_audit_separability,
)
from yieldforge.replay.contracts import ReplayCostLedger
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest


def _roots():
    return build_gate3_root_binding(
        contract_id="yfm11c-" + "0" * 24,
        contract_content_sha256="sha256:" + "1" * 64,
        population_id="yfm11pop-" + "2" * 24,
        population_content_sha256="sha256:" + "3" * 64,
        gate1_run_id="yfm11g1run-" + "4" * 24,
        gate1_run_content_sha256="sha256:" + "5" * 64,
        gate1_evaluation_result_id="yfm11g1r-" + "6" * 24,
        gate1_evaluation_result_content_sha256="sha256:" + "7" * 64,
        gate2_run_id="yfm11g2run-" + "8" * 24,
        gate2_run_content_sha256="sha256:" + "9" * 64,
        gate2_evaluation_result_id="yfm11g2r-" + "a" * 24,
        gate2_evaluation_result_content_sha256="sha256:" + "b" * 64,
        gate3_config_id="yfm11g3c-" + "c" * 24,
        gate3_config_content_sha256="sha256:" + "d" * 64,
        adapter_runtime_config_sha256="sha256:" + "e" * 64,
    )


def _projection(
    *,
    visible_local: tuple[tuple[int, ...], ...] = ((0,), (0, 1), (0, 1, 2)),
) -> M11MaterialRuntimeProjection:
    roots = _roots()
    rates = FeasibilityRateManifest(
        purchase_cost_per_area=1.0,
        storage_cost_per_area_hour=0.5 / (100.0 * 30.0 * 24.0),
        return_handling_cost_per_remnant=0.25,
        retrieval_handling_cost_per_remnant=0.25,
        scrap_credit_per_area=0.1,
    )
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=6.0,
        policy=M7PolicyName.AGE_REGULARITY,
        rates=rates,
        event_count=3,
    )
    source_positions = (0, 5, 23)
    source_event_map = tuple(
        M11SourceEventMap(
            local_event_position=local_position,
            compatibility_event_id=binding.event_id,
            source_event_position=source_position,
            source_event_id=f"yfm11e-{source_position:024x}",
            source_event_content_sha256="sha256:" + f"{local_position + 1:x}" * 64,
            source_material_key="alloy-a",
            projected_material_key="alloy-a",
            payload_id=f"yfm11pl-{local_position + 1:024x}",
            released_at=binding.released_at.isoformat().replace("+00:00", "Z"),
        )
        for local_position, (source_position, binding) in enumerate(
            zip(source_positions, runtime.replay_input.instances, strict=True)
        )
    )
    visible = tuple(
        M11KnownVisibleLocalPrefix(
            local_event_position=local_position,
            source_as_of_event_position=source_positions[local_position],
            source_as_of_event_id=source_event_map[local_position].source_event_id,
            visible_source_event_positions=tuple(
                source_positions[position] for position in positions
            ),
            visible_local_event_positions=positions,
            visibility_rule="m11_known_at_filtered_to_registered_slice_and_material",
        )
        for local_position, positions in enumerate(visible_local)
    )
    parity = []
    for local_position, (mapping, binding) in enumerate(
        zip(source_event_map, runtime.replay_input.instances, strict=True)
    ):
        evidence = runtime.runtime_candidates[binding.problem_id].evidence
        candidate_ids = evidence.candidate_ids
        parity.append(
            M11CandidateActionParity(
                local_event_position=local_position,
                source_event_id=mapping.source_event_id,
                payload_id=mapping.payload_id,
                runtime_problem_id=binding.problem_id,
                source_candidate_ids=candidate_ids,
                projected_candidate_ids=candidate_ids,
                runtime_candidate_ids=candidate_ids,
                source_binding_sha256s=tuple(
                    "sha256:" + f"{index + local_position + 1:x}" * 64
                    for index in range(len(candidate_ids))
                ),
                standard_action_ids=tuple(
                    f"m7-standard:{candidate_id}" for candidate_id in candidate_ids
                ),
                projection_rule="all_registered_candidates",
            )
        )
    semantic = {
        "schema_version": "yieldforge.m11-m7-runtime-attestation.v1",
        "gate1_result_id": roots.gate1_evaluation_result_id,
        "gate1_result_content_sha256": roots.gate1_evaluation_result_content_sha256,
        "gate2_result_id": roots.gate2_evaluation_result_id,
        "gate2_result_content_sha256": roots.gate2_evaluation_result_content_sha256,
        "gate3_config_id": roots.gate3_config_id,
        "gate3_config_content_sha256": roots.gate3_config_content_sha256,
        "population_id": roots.population_id,
        "population_content_sha256": roots.population_content_sha256,
        "registration_kind": "confirmation",
        "control_kind": None,
        "registered_exact_audit_arm": None,
        "registered_exact_audit_material_rule": None,
        "source_registration_id": "synthetic-confirmation-stream",
        "source_registration_content_sha256": "sha256:" + "f" * 64,
        "source_stream_id": "yfm11st-" + "1" * 24,
        "source_stream_content_sha256": "sha256:" + "2" * 64,
        "corpus_id": "lectra-m3-m4",
        "economic_arm": "central",
        "policy": runtime.replay_input.policy.model_dump(mode="json"),
        "material_key": "alloy-a",
        "reference_area_key": "synthetic-area",
        "reference_area": 100.0,
        "rates": rates.model_dump(mode="json"),
        "source_event_map": tuple(item.model_dump(mode="json") for item in source_event_map),
        "known_visible_local_prefixes": tuple(item.model_dump(mode="json") for item in visible),
        "candidate_action_parity": tuple(item.model_dump(mode="json") for item in parity),
        "m7_replay_input_id": runtime.replay_input.input_id,
        "m7_replay_input_content_sha256": runtime.replay_input.content_sha256,
        "m7_runtime_semantic_sha256": m7_semantic_runtime_sha256(runtime),
        "collision_backend": runtime.replay_input.collision_backend,
        "ledger_scope": "single_material_independent_substream",
        "compatibility_dto_only": True,
        "native_m7_evidence_persistence_authorized": False,
    }
    digest = semantic_sha256(semantic)
    attestation = M11M7ProjectionAttestation(
        attestation_id=f"yfm11m7a-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **(
            semantic
            | {
                "policy": runtime.replay_input.policy,
                "rates": rates,
                "source_event_map": source_event_map,
                "known_visible_local_prefixes": visible,
                "candidate_action_parity": tuple(parity),
            }
        ),
    )
    return M11MaterialRuntimeProjection(attestation=attestation, runtime=runtime)


@pytest.mark.parametrize(
    ("policy_id", "expected"),
    (
        ("myopic_geometry", M7PolicyName.MYOPIC_GEOMETRY),
        ("remnant_first", M7PolicyName.REMNANT_FIRST),
        ("net_cost", M7PolicyName.NET_COST),
        ("age_regularity", M7PolicyName.AGE_REGULARITY),
        ("known_order_lookahead", M7PolicyName.KNOWN_ORDER_LOOKAHEAD),
        ("known_only_m9_two_ply_scrap", M7PolicyName.AGE_REGULARITY),
    ),
)
def test_gate3_policy_registry_maps_only_the_frozen_six(
    policy_id: str,
    expected: M7PolicyName,
) -> None:
    assert gate3_policy_identity(policy_id) == policy_identity(expected)


def test_gate3_policy_registry_rejects_unregistered_policy() -> None:
    with pytest.raises(ValueError, match="unregistered Gate 3 baseline policy"):
        gate3_policy_identity("future_tuned_policy")


def test_replay_ledger_is_copied_exactly_to_six_place_gate3_terms() -> None:
    source = ReplayCostLedger(
        purchase_cost=12.345678,
        storage_cost=0.000001,
        return_handling_cost=2.5,
        retrieval_handling_cost=1.25,
        scrap_proceeds=3.0,
        terminal_scrap_credit=0.5,
        net_cost=12.595679,
    )

    converted = gate3_ledger_from_replay(source)

    assert converted.purchase_cost == "12.345678"
    assert converted.storage_cost == "0.000001"
    assert converted.return_handling_cost == "2.500000"
    assert converted.retrieval_handling_cost == "1.250000"
    assert converted.scrap_proceeds == "3.000000"
    assert converted.terminal_credit == "0.500000"
    assert converted.net_cost == "12.595679"


def test_gate3_cost_format_uses_the_frozen_half_up_midpoint_rule() -> None:
    assert _six_place(1.2345645) == "1.234565"


def test_runtime_and_catalog_hashes_are_canonical_and_state_sensitive() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)

    assert gate3_inventory_sha256(cursor.inventory).startswith("sha256:")
    assert gate3_search_config_sha256(runtime) == gate3_search_config_sha256(runtime)
    assert gate3_compute_budget_sha256(runtime) == gate3_compute_budget_sha256(runtime)
    first_catalog_hash = gate3_action_catalog_sha256(
        runtime=runtime,
        cursor=cursor,
        catalog=catalog,
    )
    assert first_catalog_hash.startswith("sha256:")

    changed_catalog = replace(catalog, storage_cost=catalog.storage_cost + 0.000001)
    assert (
        gate3_action_catalog_sha256(
            runtime=runtime,
            cursor=cursor,
            catalog=changed_catalog,
        )
        != first_catalog_hash
    )


def test_backend_and_confirmation_share_one_inventory_hash_for_empty_and_nonempty() -> None:
    assert gate3_inventory_sha256(()) == confirmation_inventory_sha256(())
    execution = execute_gate3_material_shard(
        projection=_projection(),
        roots=_roots(),
        arm="B",
        policy_id="age_regularity",
    )
    inventory = execution.steps[0].event.inventory_after
    assert inventory
    assert gate3_inventory_sha256(inventory) == confirmation_inventory_sha256(inventory)


def test_direct_b_executes_complete_catalogs_and_publishes_exact_source_positions() -> None:
    projection = _projection()

    execution = execute_gate3_material_shard(
        projection=projection,
        roots=_roots(),
        arm="B",
        policy_id="age_regularity",
    )

    assert execution.m7_replay_result is not None
    assert execution.shard_trace.final_costs == gate3_ledger_from_replay(
        execution.terminal.final_costs
    )
    assert tuple(item.event_position for item in execution.shard_trace.decisions) == (
        0,
        5,
        23,
    )
    assert tuple(item.event_id for item in execution.shard_trace.decisions) == tuple(
        item.source_event_id for item in projection.source_event_map
    )
    assert all(
        item.algorithm == "m7_policy"
        and item.visibility == "released_only"
        and item.selected_action_id == item.baseline_action_id
        and item.truncated_catalog_count == 0
        for item in execution.shard_trace.decisions
    )
    assert tuple(item.action.action_id for item in execution.m7_replay_result.events) == tuple(
        item.event.action.action_id for item in execution.steps
    )


def test_known_only_runtime_physically_excludes_unknown_work_and_reuses_authority_caches() -> None:
    projection = _projection()
    base = projection.runtime
    cursor = initial_m7_cursor(base.replay_input)

    masked = build_gate3_known_only_runtime(
        projection=projection,
        cursor=cursor,
        local_event_position=0,
    )

    assert len(masked.replay_input.instances) == 1
    assert masked.replay_input.instances[0] == base.replay_input.instances[0]
    assert masked.replay_input.horizon_end == base.replay_input.horizon_end
    assert {item.problem_id for item in masked.replay_input.problems} == {
        base.replay_input.instances[0].problem_id
    }
    assert set(masked.runtime_candidates) == {base.replay_input.instances[0].problem_id}
    assert masked.standard_profile_cache is base.standard_profile_cache
    assert masked.fit_search_cache is base.fit_search_cache
    assert masked.shared_fit_search_cache is base.shared_fit_search_cache
    assert masked.prepared_layout_cache is base.prepared_layout_cache
    assert masked.jagua_executable is base.jagua_executable
    assert gate3_search_config_sha256(masked) == gate3_search_config_sha256(base)
    assert gate3_compute_budget_sha256(masked) == gate3_compute_budget_sha256(base)
    assert m7_semantic_runtime_sha256(masked) != m7_semantic_runtime_sha256(base)


def test_known_only_runtime_resequences_a_known_future_around_an_unknown_event() -> None:
    projection = _projection(visible_local=((0, 2), (0, 1), (0, 1, 2)))
    base = projection.runtime

    masked = build_gate3_known_only_runtime(
        projection=projection,
        cursor=initial_m7_cursor(base.replay_input),
        local_event_position=0,
    )

    assert tuple(item.event_id for item in masked.replay_input.instances) == (
        base.replay_input.instances[0].event_id,
        base.replay_input.instances[2].event_id,
    )
    assert tuple(item.sequence for item in masked.replay_input.instances) == (0, 1)
    assert masked.replay_input.instances[1].binding_id != (
        base.replay_input.instances[2].binding_id
    )
    assert base.replay_input.instances[1].event_id not in {
        item.event_id for item in masked.replay_input.instances
    }


def test_full_and_known_arms_use_receding_two_ply_with_physical_runtime_hashes() -> None:
    full_projection = _projection()
    known_projection = _projection()

    full = execute_gate3_material_shard(
        projection=full_projection,
        roots=_roots(),
        arm="F",
        policy_id="age_regularity",
    )
    known = execute_gate3_material_shard(
        projection=known_projection,
        roots=_roots(),
        arm="K",
        policy_id="age_regularity",
    )

    assert full.m7_replay_result is None
    assert known.m7_replay_result is None
    assert all(
        item.algorithm == "m9_two_ply"
        and item.visibility == "full_future"
        and item.m9_complete
        and item.m9_catalog_count > 0
        and item.truncated_catalog_count == 0
        for item in full.shard_trace.decisions
    )
    assert all(
        item.algorithm == "m9_two_ply"
        and item.visibility == "known_only"
        and item.m9_complete
        and item.m9_catalog_count > 0
        and item.truncated_catalog_count == 0
        for item in known.shard_trace.decisions
    )
    base_runtime_sha = m7_semantic_runtime_sha256(full_projection.runtime)
    assert {item.search_runtime_sha256 for item in full.shard_trace.decisions} == {base_runtime_sha}
    assert known.shard_trace.decisions[0].search_runtime_sha256 != base_runtime_sha
    assert (
        full.shard_trace.decisions[0].search_config_sha256
        == known.shard_trace.decisions[0].search_config_sha256
    )
    assert (
        full.shard_trace.decisions[0].compute_budget_sha256
        == known.shard_trace.decisions[0].compute_budget_sha256
    )
    assert (
        full.shard_trace.decisions[0].action_catalog_sha256
        == known.shard_trace.decisions[0].action_catalog_sha256
    )


def test_sixth_baseline_uses_known_only_m9_with_age_regularity_continuation() -> None:
    projection = _projection()

    execution = execute_gate3_material_shard(
        projection=projection,
        roots=_roots(),
        arm="B",
        policy_id="known_only_m9_two_ply_scrap",
    )

    assert execution.m7_replay_result is None
    assert execution.shard_trace.policy_id == "known_only_m9_two_ply_scrap"
    assert execution.shard_trace.visibility == "known_only"
    assert all(
        item.algorithm == "m9_two_ply" and item.visibility == "known_only"
        for item in execution.shard_trace.decisions
    )
    assert projection.runtime.replay_input.policy == policy_identity(M7PolicyName.AGE_REGULARITY)


def test_projection_and_root_tampering_fail_before_runtime_execution() -> None:
    projection = _projection()
    tampered_attestation = projection.attestation.model_copy(
        update={"population_content_sha256": "sha256:" + "0" * 64}
    )
    tampered_projection = M11MaterialRuntimeProjection(
        attestation=tampered_attestation,
        runtime=projection.runtime,
    )
    with pytest.raises(ValueError, match="authenticated adapter attestation"):
        execute_gate3_material_shard(
            projection=tampered_projection,
            roots=_roots(),
            arm="B",
            policy_id="age_regularity",
        )

    tampered_roots = _roots().model_copy(update={"population_content_sha256": "sha256:" + "0" * 64})
    with pytest.raises(ValueError, match="root binding failed strict"):
        execute_gate3_material_shard(
            projection=projection,
            roots=tampered_roots,
            arm="B",
            policy_id="age_regularity",
        )


def _fake_shard(*, material_key: str, positions: tuple[int, ...]):
    roots = _roots()
    decisions = tuple(
        build_gate3_decision_trace(
            event_position=position,
            event_id=f"source-event-{position}",
            arm="B",
            algorithm="m7_policy",
            visibility="released_only",
            policy_id="age_regularity",
            standard_candidate_set_sha256="sha256:" + "1" * 64,
            search_config_sha256="sha256:" + "2" * 64,
            compute_budget_sha256="sha256:" + "3" * 64,
            search_runtime_sha256=None,
            action_catalog_sha256="sha256:" + f"{position % 16:x}" * 64,
            action_ids=(f"action-{position}",),
            baseline_action_id=f"action-{position}",
            selected_action_id=f"action-{position}",
            selected_immediate_cost="1.000000",
            baseline_immediate_cost="1.000000",
            m9_root_scores=(),
            inventory_before_sha256="sha256:" + "4" * 64,
            inventory_after_sha256="sha256:" + "5" * 64,
            returned_lineage_root_ids=(),
            selected_lineage_root_id=None,
            m9_catalog_count=0,
            m9_explicit_transition_count=0,
            m9_continuation_event_count=0,
            m9_start_event_position=None,
            m9_stop_event_position=None,
        )
        for position in positions
    )
    costs = build_gate3_cost_ledger(
        purchase_cost="12.000000",
        storage_cost="0.000000",
        return_handling_cost="0.000000",
        retrieval_handling_cost="0.000000",
        scrap_proceeds="0.000000",
        terminal_credit="0.000000",
    )
    return build_gate3_shard_trace(
        roots=roots,
        stream_id="stream-24",
        corpus_id="lectra-m3-m4",
        shard_id=f"shard-{material_key}",
        material_key=material_key,
        arm="B",
        policy_id="age_regularity",
        visibility="released_only",
        projection_binding_sha256="sha256:" + "6" * 64,
        decisions=decisions,
        final_costs=costs,
    )


def test_material_shards_merge_in_canonical_order_with_exact_addition() -> None:
    odd = _fake_shard(material_key="zinc", positions=tuple(range(1, 24, 2)))
    even = _fake_shard(material_key="alloy", positions=tuple(range(0, 24, 2)))

    arm = merge_gate3_shard_traces(
        roots=_roots(),
        stream_id="stream-24",
        corpus_id="lectra-m3-m4",
        regime="mixed",
        arm="B",
        policy_id="age_regularity",
        shards=(odd, even),
    )

    assert tuple(item.material_key for item in arm.shards) == ("alloy", "zinc")
    assert arm.final_costs.purchase_cost == "24.000000"
    assert tuple(item.event_position for item in arm.decisions) == tuple(range(24))


def test_exact_audit_material_scores_equal_the_full_cartesian_product() -> None:
    material_root_costs = (
        ("alloy", (("a0", "1.000000"), ("a1", "2.000000"))),
        ("zinc", (("z0", "3.000000"), ("z1", "5.000000"))),
    )
    cartesian_costs = tuple(
        (
            (("alloy", alloy[0]), ("zinc", zinc[0])),
            f"{float(alloy[1]) + float(zinc[1]):.6f}",
        )
        for alloy, zinc in product(
            material_root_costs[0][1],
            material_root_costs[1][1],
        )
    )

    assert verify_gate3_exact_audit_separability(
        material_root_costs=material_root_costs,
        cartesian_costs=cartesian_costs,
    )
    tampered = cartesian_costs[:-1] + ((cartesian_costs[-1][0], "8.000001"),)
    with pytest.raises(ValueError, match="Cartesian costs differ"):
        verify_gate3_exact_audit_separability(
            material_root_costs=material_root_costs,
            cartesian_costs=tampered,
        )
