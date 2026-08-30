from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from functools import cache

import pytest
from pydantic import ValidationError


def _confirmation():
    try:
        return importlib.import_module("yieldforge.realistic_falsification.confirmation")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 8 confirmation module is missing: {error}")


def _roots():
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


def test_root_binding_keeps_run_envelopes_distinct_from_nested_evaluations() -> None:
    roots = _roots()

    assert roots.gate1_run_id.startswith("yfm11g1run-")
    assert roots.gate1_evaluation_result_id.startswith("yfm11g1r-")
    assert roots.gate2_run_id.startswith("yfm11g2run-")
    assert roots.gate2_evaluation_result_id.startswith("yfm11g2r-")
    assert roots.gate3_config_id.startswith("yfm11g3c-")
    assert roots.gate3_config_content_sha256 != roots.adapter_runtime_config_sha256

    payload = roots.model_dump(mode="python", round_trip=True)
    payload["gate1_evaluation_result_id"] = roots.gate1_run_id
    with pytest.raises(ValidationError):
        _confirmation().Gate3RootBinding.model_validate(payload, strict=True)


def _calibration_costs():
    confirmation = _confirmation()
    stream_ids = tuple(f"calibration-stream-{index}" for index in range(8))
    costs = {
        policy_id: tuple("100.000000" for _ in stream_ids)
        for policy_id in confirmation.GATE3_BASELINE_POLICY_IDS
    }
    costs["known_only_m9_two_ply_scrap"] = tuple("99.000000" for _ in stream_ids)
    openings = {
        policy_id: tuple(8 for _ in stream_ids)
        for policy_id in confirmation.GATE3_BASELINE_POLICY_IDS
    }
    invalid = {policy_id: 0 for policy_id in confirmation.GATE3_BASELINE_POLICY_IDS}
    return stream_ids, costs, openings, invalid


def _baseline_freeze(*, corpus_id: str, selected_policy_id: str = "age_regularity"):
    confirmation = _confirmation()
    stream_ids, costs, openings, invalid = _calibration_costs()
    costs[selected_policy_id] = tuple("98.000000" for _ in stream_ids)
    return confirmation.select_gate3_baseline_policy(
        roots=_roots(),
        corpus_id=corpus_id,
        calibration_stream_ids=stream_ids,
        policy_stream_costs=costs,
        policy_stream_sheet_openings=openings,
        policy_invalid_stream_counts=invalid,
    )


def test_calibration_freeze_scores_every_registered_baseline_without_confirmation() -> None:
    confirmation = _confirmation()
    stream_ids, costs, openings, invalid = _calibration_costs()

    freeze = confirmation.select_gate3_baseline_policy(
        roots=_roots(),
        corpus_id="loco-2dics",
        calibration_stream_ids=stream_ids,
        policy_stream_costs=costs,
        policy_stream_sheet_openings=openings,
        policy_invalid_stream_counts=invalid,
    )

    assert freeze.registered_policy_ids == confirmation.GATE3_BASELINE_POLICY_IDS
    assert tuple(score.policy_id for score in freeze.policy_scores) == tuple(
        sorted(confirmation.GATE3_BASELINE_POLICY_IDS)
    )
    assert freeze.selected_policy_id == "known_only_m9_two_ply_scrap"
    assert freeze.confirmation_inputs_used is False
    assert freeze.calibration_stream_ids == stream_ids
    assert freeze.tied_lowest_policy_ids == ("known_only_m9_two_ply_scrap",)


def test_calibration_freeze_uses_stable_policy_id_for_exact_ties() -> None:
    confirmation = _confirmation()
    stream_ids, costs, openings, invalid = _calibration_costs()
    costs["age_regularity"] = costs["known_only_m9_two_ply_scrap"]

    freeze = confirmation.select_gate3_baseline_policy(
        roots=_roots(),
        corpus_id="lectra-m3-m4",
        calibration_stream_ids=stream_ids,
        policy_stream_costs=costs,
        policy_stream_sheet_openings=openings,
        policy_invalid_stream_counts=invalid,
    )

    assert freeze.tied_lowest_policy_ids == (
        "age_regularity",
        "known_only_m9_two_ply_scrap",
    )
    assert freeze.selected_policy_id == "age_regularity"


def test_calibration_freeze_applies_median_then_sheet_openings_before_policy_id() -> None:
    confirmation = _confirmation()
    stream_ids, costs, openings, invalid = _calibration_costs()
    costs["age_regularity"] = (
        "98.000000",
        "98.000000",
        "98.000000",
        "98.000000",
        "98.000000",
        "100.000000",
        "101.000000",
        "101.000000",
    )
    costs["known_only_m9_two_ply_scrap"] = tuple("99.000000" for _ in stream_ids)

    lower_median = confirmation.select_gate3_baseline_policy(
        roots=_roots(),
        corpus_id="lectra-m3-m4",
        calibration_stream_ids=stream_ids,
        policy_stream_costs=costs,
        policy_stream_sheet_openings=openings,
        policy_invalid_stream_counts=invalid,
    )
    assert lower_median.selected_policy_id == "age_regularity"
    assert lower_median.tied_lowest_policy_ids == ("age_regularity",)

    costs["age_regularity"] = tuple("99.000000" for _ in stream_ids)
    openings["age_regularity"] = tuple(9 for _ in stream_ids)
    fewer_openings = confirmation.select_gate3_baseline_policy(
        roots=_roots(),
        corpus_id="lectra-m3-m4",
        calibration_stream_ids=stream_ids,
        policy_stream_costs=costs,
        policy_stream_sheet_openings=openings,
        policy_invalid_stream_counts=invalid,
    )
    assert fewer_openings.selected_policy_id == "known_only_m9_two_ply_scrap"


def test_calibration_freeze_fails_closed_on_any_invalid_policy_stream() -> None:
    confirmation = _confirmation()
    stream_ids, costs, openings, invalid = _calibration_costs()
    invalid["myopic_geometry"] = 1

    with pytest.raises(ValueError, match="invalid policy stream"):
        confirmation.select_gate3_baseline_policy(
            roots=_roots(),
            corpus_id="loco-2dics",
            calibration_stream_ids=stream_ids,
            policy_stream_costs=costs,
            policy_stream_sheet_openings=openings,
            policy_invalid_stream_counts=invalid,
        )


def test_calibration_contract_requires_authenticated_material_replay_evidence() -> None:
    confirmation = _confirmation()

    assert {
        "observation_id",
        "content_sha256",
        "roots",
        "corpus_id",
        "stream_id",
        "policy_id",
        "material_replays",
        "final_costs",
        "full_sheet_opening_count",
    }.issubset(confirmation.Gate3CalibrationObservation.model_fields)
    assert "observation" in confirmation.Gate3CalibrationAttempt.model_fields
    assert "final_costs" not in confirmation.Gate3CalibrationAttempt.model_fields
    assert "full_sheet_opening_count" not in confirmation.Gate3CalibrationAttempt.model_fields

    with pytest.raises(ValidationError):
        confirmation.Gate3CalibrationObservation(
            final_costs=_ledger(purchase="100.000000"),
            full_sheet_opening_count=8,
        )


def test_calibration_freeze_rejects_an_incomplete_policy_registry() -> None:
    confirmation = _confirmation()
    stream_ids, costs, openings, invalid = _calibration_costs()
    costs.pop("myopic_geometry")

    with pytest.raises(ValueError, match="registered policy registry"):
        confirmation.select_gate3_baseline_policy(
            roots=_roots(),
            corpus_id="loco-2dics",
            calibration_stream_ids=stream_ids,
            policy_stream_costs=costs,
            policy_stream_sheet_openings=openings,
            policy_invalid_stream_counts=invalid,
        )


def test_calibration_freeze_identity_rejects_a_mutated_score() -> None:
    confirmation = _confirmation()
    stream_ids, costs, openings, invalid = _calibration_costs()
    freeze = confirmation.select_gate3_baseline_policy(
        roots=_roots(),
        corpus_id="loco-2dics",
        calibration_stream_ids=stream_ids,
        policy_stream_costs=costs,
        policy_stream_sheet_openings=openings,
        policy_invalid_stream_counts=invalid,
    )

    payload = freeze.model_dump(mode="python", round_trip=True)
    payload["selected_policy_id"] = "age_regularity"
    with pytest.raises(ValidationError):
        confirmation.Gate3BaselineCalibrationFreeze.model_validate(payload, strict=True)


def _ledger(*, purchase: str, terminal: str = "0.000000"):
    confirmation = _confirmation()
    return confirmation.build_gate3_cost_ledger(
        purchase_cost=purchase,
        storage_cost="0.000000",
        return_handling_cost="0.000000",
        retrieval_handling_cost="0.000000",
        scrap_proceeds="0.000000",
        terminal_credit=terminal,
    )


def _decision(
    *,
    position: int,
    arm: str,
    candidate_sha: str = "a",
    selected: str = "standard",
    fallback: str = "standard",
    policy_id: str = "age_regularity",
    event_id: str | None = None,
):
    confirmation = _confirmation()
    is_reference = arm in ("F", "K")
    root_scores = ()
    if is_reference:
        root_scores = tuple(
            (
                action_id,
                "1.000000"
                if action_id == selected and selected != fallback
                else ("2.000000" if action_id == fallback else "3.000000"),
            )
            for action_id in ("standard", "remnant")
        )
    return confirmation.build_gate3_decision_trace(
        event_position=position,
        event_id=event_id or f"event-{position}",
        arm=arm,
        algorithm="m9_two_ply" if is_reference else "m7_policy",
        visibility=(
            "full_future"
            if is_reference and arm == "F"
            else "known_only"
            if is_reference
            else "released_only"
        ),
        policy_id=policy_id,
        standard_candidate_set_sha256="sha256:" + candidate_sha * 64,
        search_config_sha256="sha256:" + "b" * 64,
        compute_budget_sha256="sha256:" + "c" * 64,
        search_runtime_sha256="sha256:" + "f" * 64 if is_reference else None,
        action_catalog_sha256="sha256:" + f"{position % 16:x}" * 64,
        action_ids=("standard", "remnant"),
        baseline_action_id=fallback,
        selected_action_id=selected,
        selected_immediate_cost="1.000000",
        baseline_immediate_cost="1.000000",
        m9_root_scores=root_scores,
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


def _shard(
    *,
    stream_id: str,
    arm: str,
    positions: tuple[int, ...],
    purchase: str,
    ordinal: int,
    candidate_sha: str = "a",
    corpus_id: str = "loco-2dics",
    policy_id: str = "age_regularity",
):
    confirmation = _confirmation()
    return confirmation.build_gate3_shard_trace(
        roots=_roots(),
        stream_id=stream_id,
        corpus_id=corpus_id,
        shard_id=f"shard-{ordinal}",
        material_key=f"material-{ordinal}",
        arm=arm,
        policy_id=policy_id,
        visibility=(
            "full_future" if arm == "F" else "known_only" if arm == "K" else "released_only"
        ),
        projection_binding_sha256="sha256:" + f"{ordinal + 6:x}" * 64,
        decisions=tuple(
            _decision(
                position=position,
                arm=arm,
                candidate_sha=candidate_sha,
                policy_id=policy_id,
            )
            for position in positions
        ),
        final_costs=_ledger(purchase=purchase),
    )


def test_m9_decision_recomputes_selected_action_from_complete_root_scores() -> None:
    confirmation = _confirmation()

    with pytest.raises(ValueError, match="mechanical two-ply selection"):
        confirmation.build_gate3_decision_trace(
            event_position=0,
            event_id="event-0",
            arm="F",
            algorithm="m9_two_ply",
            visibility="full_future",
            policy_id="age_regularity",
            standard_candidate_set_sha256="sha256:" + "a" * 64,
            search_config_sha256="sha256:" + "b" * 64,
            compute_budget_sha256="sha256:" + "c" * 64,
            search_runtime_sha256="sha256:" + "f" * 64,
            action_catalog_sha256="sha256:" + "0" * 64,
            action_ids=("standard", "remnant"),
            baseline_action_id="standard",
            selected_action_id="remnant",
            selected_immediate_cost="1.000000",
            baseline_immediate_cost="1.000000",
            m9_root_scores=(("remnant", "2.000000"), ("standard", "1.000000")),
            inventory_before_sha256="sha256:" + "d" * 64,
            inventory_after_sha256="sha256:" + "e" * 64,
            returned_lineage_root_ids=(),
            selected_lineage_root_id=None,
            m9_start_event_position=0,
            m9_stop_event_position=3,
            m9_catalog_count=3,
            m9_explicit_transition_count=4,
            m9_continuation_event_count=2,
        )


def _arm(
    *,
    stream_id: str,
    arm: str,
    first: str,
    second: str,
    candidate_sha: str = "a",
    corpus_id: str = "loco-2dics",
    policy_id: str = "age_regularity",
):
    confirmation = _confirmation()
    shards = (
        _shard(
            stream_id=stream_id,
            arm=arm,
            positions=tuple(range(0, 24, 2)),
            purchase=first,
            ordinal=0,
            candidate_sha=candidate_sha,
            corpus_id=corpus_id,
            policy_id=policy_id,
        ),
        _shard(
            stream_id=stream_id,
            arm=arm,
            positions=tuple(range(1, 24, 2)),
            purchase=second,
            ordinal=1,
            candidate_sha=candidate_sha,
            corpus_id=corpus_id,
            policy_id=policy_id,
        ),
    )
    return confirmation.merge_gate3_material_shards(
        roots=_roots(),
        stream_id=stream_id,
        corpus_id=corpus_id,
        regime="recurrent",
        arm=arm,
        policy_id=policy_id,
        shards=shards,
    )


def test_material_shard_merge_is_exact_and_restores_original_event_order() -> None:
    arm = _arm(stream_id="confirmation-stream-0", arm="B", first="60.000000", second="40.000000")

    assert arm.final_costs.purchase_cost == "100.000000"
    assert arm.final_costs.net_cost == "100.000000"
    assert tuple(item.event_position for item in arm.decisions) == tuple(range(24))
    assert arm.material_shard_count == 2


def test_material_shard_merge_rejects_a_duplicate_or_missing_event_position() -> None:
    confirmation = _confirmation()
    shards = (
        _shard(
            stream_id="confirmation-stream-0",
            arm="B",
            positions=tuple(range(12)),
            purchase="60.000000",
            ordinal=0,
        ),
        _shard(
            stream_id="confirmation-stream-0",
            arm="B",
            positions=tuple(range(11, 23)),
            purchase="40.000000",
            ordinal=1,
        ),
    )

    with pytest.raises(ValueError, match="exactly once"):
        confirmation.merge_gate3_material_shards(
            roots=_roots(),
            stream_id="confirmation-stream-0",
            corpus_id="loco-2dics",
            regime="recurrent",
            arm="B",
            policy_id="age_regularity",
            shards=shards,
        )


def test_paired_cell_recomputes_savings_and_unknown_from_b_f_k_costs() -> None:
    confirmation = _confirmation()
    stream_id = "confirmation-stream-0"
    cell = confirmation.build_gate3_stream_cell(
        roots=_roots(),
        baseline_freeze=_baseline_freeze(corpus_id="loco-2dics"),
        baseline=_arm(stream_id=stream_id, arm="B", first="60.000000", second="40.000000"),
        full_future=_arm(stream_id=stream_id, arm="F", first="58.000000", second="39.000000"),
        known_only=_arm(stream_id=stream_id, arm="K", first="59.000000", second="40.000000"),
    )

    assert cell.baseline_cost == "100.000000"
    assert cell.full_future_cost == "97.000000"
    assert cell.known_only_cost == "99.000000"
    assert cell.full_future_savings_percent == "3.000000000000"
    assert cell.unknown_future_contribution_points == "2.000000000000"


def test_paired_cell_contains_freeze_and_rejects_baseline_policy_substitution() -> None:
    confirmation = _confirmation()
    stream_id = "confirmation-stream-freeze-binding"
    freeze = _baseline_freeze(corpus_id="loco-2dics")

    cell = confirmation.build_gate3_stream_cell(
        roots=_roots(),
        baseline_freeze=freeze,
        baseline=_arm(
            stream_id=stream_id,
            arm="B",
            first="60.000000",
            second="40.000000",
        ),
        full_future=_arm(
            stream_id=stream_id,
            arm="F",
            first="58.000000",
            second="39.000000",
        ),
        known_only=_arm(
            stream_id=stream_id,
            arm="K",
            first="59.000000",
            second="40.000000",
        ),
    )

    assert cell.baseline_freeze == freeze
    with pytest.raises(ValueError, match="selected baseline policy"):
        confirmation.build_gate3_stream_cell(
            roots=_roots(),
            baseline_freeze=freeze,
            baseline=_arm(
                stream_id=stream_id,
                arm="B",
                first="60.000000",
                second="40.000000",
                policy_id="myopic_geometry",
            ),
            full_future=_arm(
                stream_id=stream_id,
                arm="F",
                first="58.000000",
                second="39.000000",
                policy_id="myopic_geometry",
            ),
            known_only=_arm(
                stream_id=stream_id,
                arm="K",
                first="59.000000",
                second="40.000000",
                policy_id="myopic_geometry",
            ),
        )
    payload = cell.model_dump(mode="python", round_trip=True)
    payload["baseline"]["policy_id"] = "myopic_geometry"
    for shard in payload["baseline"]["shards"]:
        shard["policy_id"] = "myopic_geometry"
        for decision in shard["decisions"]:
            decision["policy_id"] = "myopic_geometry"
    with pytest.raises(ValidationError):
        confirmation.Gate3StreamCell.model_validate(payload, strict=True)


def test_paired_cell_rejects_candidate_or_compute_parity_drift() -> None:
    confirmation = _confirmation()
    stream_id = "confirmation-stream-0"
    known = _arm(
        stream_id=stream_id,
        arm="K",
        first="59.000000",
        second="40.000000",
        candidate_sha="9",
    )

    with pytest.raises(ValueError, match="candidate/config/tie parity"):
        confirmation.build_gate3_stream_cell(
            roots=_roots(),
            baseline_freeze=_baseline_freeze(corpus_id="loco-2dics"),
            baseline=_arm(
                stream_id=stream_id,
                arm="B",
                first="60.000000",
                second="40.000000",
            ),
            full_future=_arm(
                stream_id=stream_id,
                arm="F",
                first="58.000000",
                second="39.000000",
            ),
            known_only=known,
        )


def _cell(
    *,
    stream_id: str,
    corpus_id: str,
    savings_cost: str,
    unknown_cost: str,
):
    baseline = _arm(
        stream_id=stream_id,
        arm="B",
        first="60.000000",
        second="40.000000",
        corpus_id=corpus_id,
    )
    full_purchase = format(100 - float(savings_cost), ".6f")
    known_purchase = format(100 - float(savings_cost) + float(unknown_cost), ".6f")
    full = _arm(
        stream_id=stream_id,
        arm="F",
        first=full_purchase,
        second="0.000000",
        corpus_id=corpus_id,
    )
    known = _arm(
        stream_id=stream_id,
        arm="K",
        first=known_purchase,
        second="0.000000",
        corpus_id=corpus_id,
    )
    return _confirmation().build_gate3_stream_cell(
        roots=_roots(),
        baseline_freeze=_baseline_freeze(corpus_id=corpus_id),
        baseline=baseline,
        full_future=full,
        known_only=known,
    )


def _central_cells(*, lectra_savings: str, loco_savings: str, unknown: str = "2.000000"):
    return tuple(
        _cell(
            stream_id=f"lectra-confirmation-{index:02d}",
            corpus_id="lectra-m3-m4",
            savings_cost=lectra_savings,
            unknown_cost=unknown,
        )
        for index in range(20)
    ) + tuple(
        _cell(
            stream_id=f"loco-confirmation-{index:02d}",
            corpus_id="loco-2dics",
            savings_cost=loco_savings,
            unknown_cost=unknown,
        )
        for index in range(20)
    )


def test_central_statistics_are_deterministic_stratified_and_inclusive_at_green_floors() -> None:
    confirmation = _confirmation()
    cells = _central_cells(
        lectra_savings="2.500000",
        loco_savings="2.500000",
        unknown="1.500000",
    )

    lectra_ids = tuple(item.stream_id for item in cells[:20])
    loco_ids = tuple(item.stream_id for item in cells[20:])
    first = confirmation.calculate_gate3_central_statistics(
        cells,
        lectra_stream_ids=lectra_ids,
        loco_stream_ids=loco_ids,
    )
    second = confirmation.calculate_gate3_central_statistics(
        cells,
        lectra_stream_ids=lectra_ids,
        loco_stream_ids=loco_ids,
    )

    assert first == second
    assert first.content_sha256 == second.content_sha256
    assert tuple(item.group for item in first.groups) == (
        "lectra-m3-m4",
        "loco-2dics",
        "equal-corpus-pool",
    )
    assert all(item.central_green for item in first.groups)
    assert all(item.mean_savings_percent == "2.500000000000" for item in first.groups)
    assert all(item.mean_unknown_contribution_points == "1.500000000000" for item in first.groups)
    assert all(item.savings_mean_ci_lower == 2.5 for item in first.groups)
    assert first.all_groups_central_green is True


def test_central_statistics_fail_strict_positive_fraction_at_exactly_half() -> None:
    confirmation = _confirmation()
    lectra = tuple(
        _cell(
            stream_id=f"lectra-half-{index:02d}",
            corpus_id="lectra-m3-m4",
            savings_cost="3.000000" if index < 10 else "0.000000",
            unknown_cost="2.000000",
        )
        for index in range(20)
    )
    loco = tuple(
        _cell(
            stream_id=f"loco-green-{index:02d}",
            corpus_id="loco-2dics",
            savings_cost="3.000000",
            unknown_cost="2.000000",
        )
        for index in range(20)
    )

    result = confirmation.calculate_gate3_central_statistics(
        lectra + loco,
        lectra_stream_ids=tuple(item.stream_id for item in lectra),
        loco_stream_ids=tuple(item.stream_id for item in loco),
    )

    assert result.groups[0].positive_stream_count == 10
    assert result.groups[0].positive_stream_fraction_percent == 50.0
    assert result.groups[0].positive_fraction_passes is False
    assert result.groups[0].central_green is False
    assert result.all_groups_central_green is False


def test_loco_first_corpus_assessment_uses_the_frozen_loco_bootstrap_draws() -> None:
    confirmation = _confirmation()
    cells = tuple(
        _cell(
            stream_id=f"loco-only-{index:02d}",
            corpus_id="loco-2dics",
            savings_cost="3.000000" if index else "-1.000000",
            unknown_cost="2.000000",
        )
        for index in range(20)
    )

    corpus = confirmation.calculate_gate3_corpus_central_summary(
        cells,
        canonical_stream_ids=tuple(item.stream_id for item in cells),
    )
    lectra = tuple(
        _cell(
            stream_id=f"lectra-only-{index:02d}",
            corpus_id="lectra-m3-m4",
            savings_cost="3.000000",
            unknown_cost="2.000000",
        )
        for index in range(20)
    )
    combined = confirmation.calculate_gate3_central_statistics(
        lectra + cells,
        lectra_stream_ids=tuple(item.stream_id for item in lectra),
        loco_stream_ids=tuple(item.stream_id for item in cells),
    )

    assert corpus == combined.groups[1]


def test_central_bootstrap_canonicalizes_cells_to_frozen_pack_stream_order() -> None:
    confirmation = _confirmation()
    cells = tuple(
        _cell(
            stream_id=f"loco-canonical-{index:02d}",
            corpus_id="loco-2dics",
            savings_cost=format(1 + index / 10, ".6f"),
            unknown_cost="2.000000",
        )
        for index in range(20)
    )
    canonical_ids = tuple(item.stream_id for item in cells)

    canonical = confirmation.calculate_gate3_corpus_central_summary(
        cells,
        canonical_stream_ids=canonical_ids,
    )
    reordered = confirmation.calculate_gate3_corpus_central_summary(
        tuple(reversed(cells)),
        canonical_stream_ids=canonical_ids,
    )

    assert reordered == canonical
    assert canonical.stream_ids == canonical_ids
    with pytest.raises(ValueError, match="canonical stream census"):
        confirmation.calculate_gate3_corpus_central_summary(
            cells,
            canonical_stream_ids=canonical_ids[:-1] + ("unregistered-stream",),
        )


def _null_decision(
    *,
    position: int,
    arm: str,
    force_m7: bool = False,
    force_m9_known: bool = False,
    policy_id: str = "age_regularity",
):
    confirmation = _confirmation()
    is_reference = (arm in ("F", "K") or force_m9_known) and not force_m7
    return confirmation.build_gate3_decision_trace(
        event_position=position,
        event_id=f"null-event-{position}",
        arm=arm,
        algorithm="m9_two_ply" if is_reference else "m7_policy",
        visibility=(
            "full_future"
            if is_reference and arm == "F"
            else "known_only"
            if is_reference
            else "released_only"
        ),
        policy_id=policy_id,
        standard_candidate_set_sha256="sha256:" + "1" * 64,
        search_config_sha256="sha256:" + "2" * 64,
        compute_budget_sha256="sha256:" + "3" * 64,
        search_runtime_sha256="sha256:" + "4" * 64 if is_reference else None,
        action_catalog_sha256="sha256:" + f"{position + 4:x}" * 64,
        action_ids=("only-action",),
        baseline_action_id="only-action",
        selected_action_id="only-action",
        selected_immediate_cost="1.000000",
        baseline_immediate_cost="1.000000",
        m9_root_scores=(("only-action", "2.000000"),) if is_reference else (),
        inventory_before_sha256="sha256:" + "6" * 64,
        inventory_after_sha256="sha256:" + "7" * 64,
        returned_lineage_root_ids=(),
        selected_lineage_root_id=None,
        m9_catalog_count=1 if is_reference else 0,
        m9_explicit_transition_count=1 if is_reference else 0,
        m9_continuation_event_count=0,
        m9_start_event_position=position if is_reference else None,
        m9_stop_event_position=position + 1 if is_reference else None,
    )


def _hard_null_registration(*, corpus_id: str, null_kind: str, ordinal: int = 0):
    from yieldforge.experiments.contracts import semantic_sha256
    from yieldforge.realistic_falsification.pack import M11HardNull

    corpus_number = 1 if corpus_id == "lectra-m3-m4" else 2
    source_stream_id = f"yfm11st-{corpus_number * 100 + ordinal:024x}"
    event_ids = tuple(
        f"yfm11e-{corpus_number * 1000 + ordinal * 3 + index:024x}" for index in range(3)
    )
    unique, known = {
        "single_action": (False, False),
        "unique_materials_single_action": (True, False),
        "all_work_known_single_action": (False, True),
    }[null_kind]
    semantic = {
        "schema_version": "yieldforge.m11-hard-null.v1",
        "corpus_id": corpus_id,
        "null_kind": null_kind,
        "source_stream_id": source_stream_id,
        "event_ids": event_ids,
        "baseline_action_count": 1,
        "future_action_count": 1,
        "unique_material_per_event": unique,
        "all_work_known": known,
        "expected_savings_percent": 0.0,
        "zero_savings_semantics": (
            "identical_single_feasible_action_and_ledger_for_baseline_and_future"
        ),
    }
    digest = semantic_sha256(semantic)
    return M11HardNull(
        null_id=f"yfm11null-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _projection_decision(
    *,
    attestation,
    replay_input,
    arm: str,
    policy_id: str,
    selected_action_id: str | None = None,
    include_extra_action: bool = False,
):
    from yieldforge.experiments.contracts import semantic_sha256

    confirmation = _confirmation()
    mapping = attestation.source_event_map[0]
    parity = attestation.candidate_action_parity[0]
    binding = replay_input.instances[0]
    candidate_set = next(
        item for item in replay_input.candidate_sets if item.problem_id == binding.problem_id
    )
    action_ids = parity.standard_action_ids
    if include_extra_action:
        action_ids += ("m7-remnant:fake-root",)
    baseline_action_id = action_ids[0]
    selected_action_id = selected_action_id or baseline_action_id
    is_m9 = arm in ("F", "K") or (arm == "B" and policy_id == "known_only_m9_two_ply_scrap")
    scores = (
        tuple(
            (
                action_id,
                "1.000000" if action_id == selected_action_id else "2.000000",
            )
            for action_id in action_ids
        )
        if is_m9
        else ()
    )
    return confirmation.build_gate3_decision_trace(
        event_position=mapping.source_event_position,
        event_id=mapping.source_event_id,
        arm=arm,
        algorithm="m9_two_ply" if is_m9 else "m7_policy",
        visibility=("full_future" if arm == "F" else "known_only" if is_m9 else "released_only"),
        policy_id=policy_id,
        standard_candidate_set_sha256=candidate_set.content_sha256,
        search_config_sha256="sha256:" + "2" * 64,
        compute_budget_sha256="sha256:" + "3" * 64,
        search_runtime_sha256=(attestation.m7_runtime_semantic_sha256 if is_m9 else None),
        action_catalog_sha256="sha256:"
        + semantic_sha256(
            {
                "attestation": attestation.attestation_id,
                "arm": arm,
                "actions": action_ids,
            }
        ),
        action_ids=action_ids,
        baseline_action_id=baseline_action_id,
        selected_action_id=selected_action_id,
        selected_immediate_cost="1.000000",
        baseline_immediate_cost="1.000000",
        m9_root_scores=scores,
        inventory_before_sha256="sha256:" + "6" * 64,
        inventory_after_sha256="sha256:" + "7" * 64,
        returned_lineage_root_ids=(),
        selected_lineage_root_id=None,
        m9_catalog_count=len(action_ids) if is_m9 else 0,
        m9_explicit_transition_count=len(action_ids) if is_m9 else 0,
        m9_continuation_event_count=0,
        m9_start_event_position=0 if is_m9 else None,
        m9_stop_event_position=1 if is_m9 else None,
    )


def _bound_projection_shard(
    *,
    registration,
    event_index: int,
    arm: str,
    policy_id: str,
    cost: str,
    selected_action_id: str | None = None,
    include_extra_action: bool = False,
    underlying_policy_id: str | None = None,
):
    confirmation = _confirmation()
    is_exact = hasattr(registration, "audit_id")
    source_position = registration.event_positions[event_index] if is_exact else event_index
    source_event_id = registration.event_ids[event_index]
    material_key = f"fake-control-material-{event_index}"
    exact_arm = registration.economic_arm if is_exact else None
    economic_arm = "adverse" if is_exact and registration.economic_arm == "adverse" else "central"
    exact_rule = (
        "unique_material_key_per_event_information_null"
        if exact_arm == "null"
        else "preserve_registered_material_keys"
        if is_exact
        else None
    )
    attestation, runtime = _fake_projection_attestation(
        corpus_id=registration.corpus_id,
        stream_id=registration.source_stream_id,
        policy_id=policy_id,
        underlying_policy_id=underlying_policy_id,
        registration_kind="exact_audit" if is_exact else "hard_null",
        registration_id=registration.audit_id if is_exact else registration.null_id,
        registration_sha=registration.content_sha256,
        control_kind=None if is_exact else registration.null_kind,
        exact_audit_arm=exact_arm,
        exact_material_rule=exact_rule,
        economic_arm=economic_arm,
        event_positions=(source_position,),
        source_event_ids=(source_event_id,),
        material_key=material_key,
        all_work_known=(False if is_exact else registration.all_work_known),
    )
    replay_input = runtime.replay_input
    decision = _projection_decision(
        attestation=attestation,
        replay_input=replay_input,
        arm=arm,
        policy_id=policy_id,
        selected_action_id=selected_action_id,
        include_extra_action=include_extra_action,
    )
    shard = confirmation.build_gate3_shard_trace(
        roots=_roots(),
        stream_id=registration.source_stream_id,
        corpus_id=registration.corpus_id,
        shard_id=f"{attestation.attestation_id}:{arm}",
        material_key=material_key,
        arm=arm,
        policy_id=policy_id,
        visibility=decision.visibility,
        projection_binding_sha256=attestation.content_sha256,
        decisions=(decision,),
        final_costs=_ledger(purchase=cost),
    )
    runtime_receipts = (
        (
            confirmation.build_gate3_decision_runtime_receipt(
                decision=decision,
                runtime_role=(
                    "base_full_future"
                    if decision.visibility == "full_future"
                    else "known_only_physical_mask"
                ),
                retained_local_event_positions=(0,),
                runtime=runtime,
            ),
        )
        if decision.algorithm == "m9_two_ply"
        else ()
    )
    return confirmation.build_gate3_projection_shard_evidence(
        roots=_roots(),
        projection_attestation=attestation,
        replay_input=replay_input,
        shard_trace=shard,
        decision_runtime_receipts=runtime_receipts,
    )


def _null_arm(
    *,
    registration,
    arm: str,
    cost: str,
    policy_id: str = "age_regularity",
):
    confirmation = _confirmation()
    material_evidence = tuple(
        _bound_projection_shard(
            registration=registration,
            event_index=index,
            arm=arm,
            policy_id=policy_id,
            cost=cost if index == 0 else "0.000000",
        )
        for index in range(3)
    )
    return confirmation.build_gate3_hard_null_arm_trace(
        roots=_roots(),
        registration=registration,
        arm=arm,
        policy_id=policy_id,
        material_evidence=material_evidence,
    )


def test_hard_null_accepts_only_one_accounting_quantum_of_cost_drift() -> None:
    confirmation = _confirmation()
    registration = _hard_null_registration(
        corpus_id="loco-2dics", null_kind="single_action", ordinal=0
    )
    control = confirmation.build_gate3_hard_null_control(
        roots=_roots(),
        baseline_freeze=_baseline_freeze(
            corpus_id="loco-2dics", selected_policy_id="age_regularity"
        ),
        registration=registration,
        baseline=_null_arm(registration=registration, arm="B", cost="100.000000"),
        full_future=_null_arm(registration=registration, arm="F", cost="99.999999"),
        known_only=_null_arm(registration=registration, arm="K", cost="100.000000"),
    )

    assert control.maximum_absolute_cost_difference == "0.000001"
    assert control.passes is True

    failed_registration = _hard_null_registration(
        corpus_id="loco-2dics", null_kind="single_action", ordinal=1
    )
    failed = confirmation.build_gate3_hard_null_control(
        roots=_roots(),
        baseline_freeze=_baseline_freeze(
            corpus_id="loco-2dics", selected_policy_id="age_regularity"
        ),
        registration=failed_registration,
        baseline=_null_arm(registration=failed_registration, arm="B", cost="100.000000"),
        full_future=_null_arm(registration=failed_registration, arm="F", cost="99.999998"),
        known_only=_null_arm(registration=failed_registration, arm="K", cost="100.000000"),
    )
    assert failed.passes is False


def test_hard_null_frozen_rule_compares_baseline_to_full_future_only() -> None:
    confirmation = _confirmation()
    registration = _hard_null_registration(
        corpus_id="loco-2dics",
        null_kind="all_work_known_single_action",
    )

    control = confirmation.build_gate3_hard_null_control(
        roots=_roots(),
        baseline_freeze=_baseline_freeze(
            corpus_id="loco-2dics", selected_policy_id="age_regularity"
        ),
        registration=registration,
        baseline=_null_arm(
            registration=registration,
            arm="B",
            cost="100.000000",
        ),
        full_future=_null_arm(
            registration=registration,
            arm="F",
            cost="100.000000",
        ),
        known_only=_null_arm(
            registration=registration,
            arm="K",
            cost="101.000000",
        ),
    )

    assert control.maximum_absolute_cost_difference == "0.000000"
    assert control.passes is True


def test_hard_null_contract_binds_selected_m7_baseline_and_exact_arm_roles() -> None:
    confirmation = _confirmation()

    assert "baseline_freeze" in confirmation.Gate3HardNullControl.model_fields
    registration = _hard_null_registration(corpus_id="loco-2dics", null_kind="single_action")
    wrong_role = tuple(
        _bound_projection_shard(
            registration=registration,
            event_index=index,
            arm="B",
            policy_id="age_regularity",
            cost="0.000000",
        )
        for index in range(3)
    )
    with pytest.raises(ValueError, match="material differs"):
        confirmation.build_gate3_hard_null_arm_trace(
            roots=_roots(),
            registration=registration,
            arm="F",
            policy_id="age_regularity",
            material_evidence=wrong_role,
        )


def test_hard_null_remains_executable_when_calibration_selects_m9_baseline() -> None:
    confirmation = _confirmation()
    policy_id = "known_only_m9_two_ply_scrap"
    freeze = _baseline_freeze(
        corpus_id="loco-2dics",
        selected_policy_id=policy_id,
    )
    registration = _hard_null_registration(corpus_id="loco-2dics", null_kind="single_action")

    control = confirmation.build_gate3_hard_null_control(
        roots=_roots(),
        baseline_freeze=freeze,
        registration=registration,
        baseline=_null_arm(
            registration=registration,
            arm="B",
            cost="100.000000",
            policy_id=policy_id,
        ),
        full_future=_null_arm(
            registration=registration,
            arm="F",
            cost="100.000000",
            policy_id=policy_id,
        ),
        known_only=_null_arm(
            registration=registration,
            arm="K",
            cost="100.000000",
            policy_id=policy_id,
        ),
    )

    assert control.passes is True
    assert control.baseline.decisions[0].algorithm == "m9_two_ply"
    assert control.baseline.decisions[0].visibility == "known_only"


def test_sixth_policy_hard_null_rejects_a_resigned_net_cost_projection() -> None:
    registration = _hard_null_registration(
        corpus_id="loco-2dics",
        null_kind="single_action",
    )
    forged = tuple(
        _bound_projection_shard(
            registration=registration,
            event_index=index,
            arm="B",
            policy_id="known_only_m9_two_ply_scrap",
            underlying_policy_id="net_cost",
            cost="0.000000",
        )
        for index in range(3)
    )

    with pytest.raises(ValueError, match="material differs"):
        _confirmation().build_gate3_hard_null_arm_trace(
            roots=_roots(),
            registration=registration,
            arm="B",
            policy_id="known_only_m9_two_ply_scrap",
            material_evidence=forged,
        )


def test_known_only_projection_rejects_a_resigned_full_future_runtime_role() -> None:
    from yieldforge.experiments.contracts import semantic_sha256

    confirmation = _confirmation()
    registration = _hard_null_registration(
        corpus_id="loco-2dics",
        null_kind="single_action",
    )
    evidence = _bound_projection_shard(
        registration=registration,
        event_index=0,
        arm="K",
        policy_id="age_regularity",
        cost="0.000000",
    )
    receipt = evidence.decision_runtime_receipts[0]
    values = receipt.model_dump(
        mode="python",
        round_trip=True,
        exclude={"receipt_id", "content_sha256"},
    )
    values["runtime_role"] = "base_full_future"
    semantic = receipt.model_dump(
        mode="json",
        exclude={"receipt_id", "content_sha256"},
    )
    semantic["runtime_role"] = "base_full_future"
    digest = semantic_sha256(semantic)
    forged = confirmation.Gate3DecisionRuntimeReceipt(
        receipt_id=f"yfm11g3drt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **values,
    )

    with pytest.raises(ValueError, match="full-future runtime receipt"):
        confirmation.build_gate3_projection_shard_evidence(
            roots=_roots(),
            projection_attestation=evidence.projection_attestation,
            replay_input=evidence.replay_input,
            shard_trace=evidence.shard_trace,
            decision_runtime_receipts=(forged,),
        )


def _exact_registration(*, corpus_id: str, ordinal: int):
    from yieldforge.experiments.contracts import semantic_sha256
    from yieldforge.realistic_falsification.pack import (
        M11_AUDIT_ARMS,
        M11_AUDIT_POSITIONS,
        M11ExactAuditEpisode,
    )

    corpus_number = 3 if corpus_id == "lectra-m3-m4" else 4
    positions = M11_AUDIT_POSITIONS[ordinal]
    event_ids = tuple(f"yfm11e-{corpus_number * 1000 + position:024x}" for position in positions)
    semantic = {
        "schema_version": "yieldforge.m11-exact-audit.v1",
        "corpus_id": corpus_id,
        "audit_ordinal": ordinal,
        "source_stream_id": f"yfm11st-{corpus_number * 100 + ordinal:024x}",
        "event_positions": positions,
        "event_ids": event_ids,
        "economic_arm": M11_AUDIT_ARMS[ordinal],
        "search_contract": "exhaustive_three_event_exact_search",
    }
    digest = semantic_sha256(semantic)
    return M11ExactAuditEpisode(
        audit_id=f"yfm11audit-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _exact_material_audit(*, evidence, selected_optimal: bool):
    from yieldforge.baseline.contracts import M7ActionKind
    from yieldforge.oracle.search_validation import (
        M9ExactRootScore,
        M9ExactSearchResult,
        M9ExactSearchTelemetry,
    )

    decision = evidence.shard_trace.decisions[0]
    scores = tuple(
        M9ExactRootScore(
            action_id=action_id,
            kind=(
                M7ActionKind.OPEN_STANDARD_SHEET
                if action_id.startswith("m7-standard:")
                else M7ActionKind.CONSUME_REMNANT
            ),
            final_net_cost=(
                1.0 if (action_id == decision.selected_action_id) is selected_optimal else 2.0
            ),
        )
        for action_id in decision.action_ids
    )
    optimum = min(item.final_net_cost for item in scores)
    result = M9ExactSearchResult(
        start_event_position=0,
        stop_event_position=1,
        include_terminal_credit=True,
        optimal_final_net_cost=optimum,
        optimal_first_action_ids=tuple(
            item.action_id for item in scores if item.final_net_cost == optimum
        ),
        root_scores=scores,
        complete=True,
        telemetry=M9ExactSearchTelemetry(
            catalog_count=1,
            explored_transition_count=len(scores),
            terminal_leaf_count=len(scores),
            peak_branching_factor=len(scores),
            truncated_catalog_count=0,
        ),
    )
    return _confirmation().build_gate3_exact_material_audit(
        evidence=evidence,
        exact_result=result,
    )


def _fake_exact_audit(
    *,
    corpus_id: str,
    ordinal: int,
    fail: bool = False,
    fail_index: int | None = None,
    baseline_freeze=None,
):
    confirmation = _confirmation()
    registration = _exact_registration(corpus_id=corpus_id, ordinal=ordinal)
    freeze = baseline_freeze or _baseline_freeze(
        corpus_id=corpus_id,
        selected_policy_id="age_regularity",
    )
    failing_material = 0 if fail else fail_index
    material_audits = tuple(
        _exact_material_audit(
            evidence=_bound_projection_shard(
                registration=registration,
                event_index=index,
                arm="F",
                policy_id=freeze.selected_policy_id,
                cost="1.000000",
                include_extra_action=True,
            ),
            selected_optimal=index != failing_material,
        )
        for index in range(3)
    )
    return confirmation.build_gate3_exact_audit_trace(
        roots=_roots(),
        baseline_freeze=freeze,
        registration=registration,
        material_audits=material_audits,
    )


def test_exact_audit_requires_two_ply_selected_root_to_be_exact_optimal() -> None:
    passing = _fake_exact_audit(corpus_id="lectra-m3-m4", ordinal=0)
    failing = _fake_exact_audit(corpus_id="lectra-m3-m4", ordinal=0, fail=True)

    assert passing.selected_action_id == passing.material_audits[0].selected_action_id
    assert passing.economic_profile == "central"
    assert passing.payload_transform == "unmodified"
    assert passing.material_rule == "preserve_registered_material_keys"
    assert passing.registered_candidates_retained is True
    assert passing.cartesian_combination_count == 8
    assert passing.cartesian_separability_verified is True
    assert passing.passes is True
    assert failing.passes is False

    nonfirst_suboptimal = _fake_exact_audit(
        corpus_id="lectra-m3-m4",
        ordinal=0,
        fail_index=1,
    )
    assert nonfirst_suboptimal.material_audits[1].passes is False
    assert nonfirst_suboptimal.passes is True

    substituted = _exact_registration(corpus_id="lectra-m3-m4", ordinal=1)
    with pytest.raises(ValueError, match="projection registration"):
        _confirmation().build_gate3_exact_audit_trace(
            roots=_roots(),
            baseline_freeze=passing.baseline_freeze,
            registration=substituted,
            material_audits=passing.material_audits,
        )

    null = _fake_exact_audit(corpus_id="loco-2dics", ordinal=4)
    assert null.economic_profile == "central"
    assert null.payload_transform == "unmodified"
    assert null.material_rule == "unique_material_key_per_event_information_null"


def test_validity_control_contract_requires_registered_projection_and_search_evidence() -> None:
    confirmation = _confirmation()

    assert {
        "roots",
        "projection_attestation",
        "replay_input",
        "shard_trace",
    }.issubset(confirmation.Gate3ProjectionShardEvidence.model_fields)
    assert "material_evidence" in confirmation.Gate3HardNullArmTrace.model_fields
    assert "registration" in confirmation.Gate3HardNullControl.model_fields
    assert "material_audits" in confirmation.Gate3ExactAuditTrace.model_fields
    assert "registration" in confirmation.Gate3ExactAuditTrace.model_fields
    assert "baseline_freeze" in confirmation.Gate3ExactAuditTrace.model_fields


def _twin_controls(*, corpus_id: str, savings: str):
    confirmation = _confirmation()
    return tuple(
        confirmation.build_gate3_twin_control(
            roots=_roots(),
            source_stream_id=f"{corpus_id}-source-{index:02d}",
            twin_cell=_cell(
                stream_id=f"{corpus_id}-twin-{index:02d}",
                corpus_id=corpus_id,
                savings_cost=savings,
                unknown_cost="0.000000",
            ),
        )
        for index in range(20)
    )


def test_no_signal_boundaries_distinguish_clean_diagnosis_and_invalid() -> None:
    confirmation = _confirmation()

    clean = confirmation.summarize_gate3_no_signal(
        _twin_controls(corpus_id="loco-2dics", savings="0.299999")
    )
    diagnosis = confirmation.summarize_gate3_no_signal(
        _twin_controls(corpus_id="loco-2dics", savings="0.300000")
    )
    upper_diagnosis = confirmation.summarize_gate3_no_signal(
        _twin_controls(corpus_id="loco-2dics", savings="0.500000")
    )
    invalid = confirmation.summarize_gate3_no_signal(
        _twin_controls(corpus_id="loco-2dics", savings="0.500001")
    )

    assert clean.classification == "clean"
    assert diagnosis.classification == "diagnosis_required"
    assert upper_diagnosis.classification == "diagnosis_required"
    assert invalid.classification == "invalid"


def _hard_null_controls(*, fail_first: bool = False, baseline_freezes=()):
    confirmation = _confirmation()
    freeze_by_corpus = {item.corpus_id: item for item in baseline_freezes}
    output = []
    for corpus_id in ("lectra-m3-m4", "loco-2dics"):
        for index, kind in enumerate(
            (
                "single_action",
                "unique_materials_single_action",
                "all_work_known_single_action",
            )
        ):
            registration = _hard_null_registration(
                corpus_id=corpus_id,
                null_kind=kind,
                ordinal=index,
            )
            full_cost = (
                "99.999998"
                if fail_first and corpus_id == "lectra-m3-m4" and index == 0
                else "100.000000"
            )
            freeze = freeze_by_corpus.get(corpus_id) or _baseline_freeze(
                corpus_id=corpus_id,
                selected_policy_id="age_regularity",
            )
            policy_id = freeze.selected_policy_id
            output.append(
                confirmation.build_gate3_hard_null_control(
                    roots=_roots(),
                    baseline_freeze=freeze,
                    registration=registration,
                    baseline=_null_arm(
                        registration=registration,
                        arm="B",
                        cost="100.000000",
                        policy_id=policy_id,
                    ),
                    full_future=_null_arm(
                        registration=registration,
                        arm="F",
                        cost=full_cost,
                        policy_id=policy_id,
                    ),
                    known_only=_null_arm(
                        registration=registration,
                        arm="K",
                        cost="100.000000",
                        policy_id=policy_id,
                    ),
                )
            )
    return tuple(output)


def _exact_audits(*, fail_first: bool = False, baseline_freezes=()):
    freeze_by_corpus = {item.corpus_id: item for item in baseline_freezes}
    output = []
    for corpus_id in ("lectra-m3-m4", "loco-2dics"):
        for index in range(6):
            output.append(
                _fake_exact_audit(
                    corpus_id=corpus_id,
                    ordinal=index,
                    fail=fail_first and corpus_id == "lectra-m3-m4" and index == 0,
                    baseline_freeze=freeze_by_corpus.get(corpus_id),
                )
            )
    return tuple(output)


def test_validity_receipt_derives_status_from_all_raw_controls() -> None:
    confirmation = _confirmation()
    clean_twins = _twin_controls(corpus_id="lectra-m3-m4", savings="0.000000") + _twin_controls(
        corpus_id="loco-2dics", savings="0.000000"
    )

    valid = confirmation.evaluate_gate3_validity_controls(
        roots=_roots(),
        hard_nulls=_hard_null_controls(),
        twin_controls=clean_twins,
        exact_audits=_exact_audits(),
    )
    failed = confirmation.evaluate_gate3_validity_controls(
        roots=_roots(),
        hard_nulls=_hard_null_controls(fail_first=True),
        twin_controls=clean_twins,
        exact_audits=_exact_audits(),
    )
    diagnosis = confirmation.evaluate_gate3_validity_controls(
        roots=_roots(),
        hard_nulls=_hard_null_controls(),
        twin_controls=_twin_controls(corpus_id="lectra-m3-m4", savings="0.300000")
        + _twin_controls(corpus_id="loco-2dics", savings="0.000000"),
        exact_audits=_exact_audits(),
    )

    assert valid.status == "valid"
    assert valid.failure_codes == ()
    assert failed.status == "invalid"
    assert failed.failure_codes[0].startswith("hard_null:")
    assert diagnosis.status == "diagnosis_required"
    assert diagnosis.diagnosis_codes == ("no_signal:lectra-m3-m4",)

    audits = _exact_audits()
    wrong_arm_order = (
        audits[0],
        audits[1],
        audits[4],
        audits[5],
        audits[2],
        audits[3],
        audits[6],
        audits[7],
        audits[10],
        audits[11],
        audits[8],
        audits[9],
    )
    with pytest.raises(ValueError, match="registered arm order"):
        confirmation.evaluate_gate3_validity_controls(
            roots=_roots(),
            hard_nulls=_hard_null_controls(),
            twin_controls=clean_twins,
            exact_audits=wrong_arm_order,
        )


def _fake_cell_for_freeze(
    *,
    corpus_id: str,
    stream_id: str,
    freeze,
    savings: str,
    unknown: str,
):
    confirmation = _confirmation()
    baseline = _arm(
        stream_id=stream_id,
        arm="B",
        first="60.000000",
        second="40.000000",
        corpus_id=corpus_id,
    )
    full = _arm(
        stream_id=stream_id,
        arm="F",
        first=format(100 - float(savings), ".6f"),
        second="0.000000",
        corpus_id=corpus_id,
    )
    known = _arm(
        stream_id=stream_id,
        arm="K",
        first=format(100 - float(savings) + float(unknown), ".6f"),
        second="0.000000",
        corpus_id=corpus_id,
    )
    return confirmation.build_gate3_stream_cell(
        roots=_roots(),
        baseline_freeze=freeze,
        baseline=baseline,
        full_future=full,
        known_only=known,
    )


def _gate3_ledger_from_replay(ledger):
    return _confirmation().build_gate3_cost_ledger(
        purchase_cost=format(ledger.purchase_cost, ".6f"),
        storage_cost=format(ledger.storage_cost, ".6f"),
        return_handling_cost=format(ledger.return_handling_cost, ".6f"),
        retrieval_handling_cost=format(ledger.retrieval_handling_cost, ".6f"),
        scrap_proceeds=format(ledger.scrap_proceeds, ".6f"),
        terminal_credit=format(ledger.terminal_scrap_credit, ".6f"),
    )


@cache
def _fake_m7_runtime_and_result(
    policy_id: str,
    event_count: int = 24,
    economic_arm: str = "central",
):
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.policies import M7PolicyName
    from yieldforge.baseline.replay import (
        M7ReplayRuntime,
        build_m7_replay_input,
        run_m7_replay,
    )
    from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest

    reference_area = 100.0
    scrap, handling, storage = {
        "central": (10.0, 0.25, 0.5),
        "adverse": (25.0, 1.0, 2.0),
    }[economic_arm]
    rates = FeasibilityRateManifest(
        purchase_cost_per_area=100.0 / reference_area,
        storage_cost_per_area_hour=storage / (reference_area * 30.0 * 24.0),
        return_handling_cost_per_remnant=handling,
        retrieval_handling_cost_per_remnant=handling,
        scrap_credit_per_area=scrap / reference_area,
    )
    runtime = two_problem_runtime(
        first_width=6.0,
        second_width=4.0,
        policy=M7PolicyName(policy_id),
        rates=rates,
        event_count=max(2, event_count),
    )
    if event_count == 1:
        from tests.baseline.test_replay import _verified

        base = runtime.replay_input
        first_problem_id = base.instances[0].problem_id
        first_problem = next(item for item in base.problems if item.problem_id == first_problem_id)
        single_verified = _verified(first_problem, candidate_ids=("candidate-one",))
        problems = (first_problem,)
        candidate_sets = (single_verified.evidence,)
        replay_input = build_m7_replay_input(
            m0_contract_id=base.m0_contract_id,
            m0_contract_sha256=base.m0_contract_sha256,
            problem_index_id=base.problem_index_id,
            problem_index_sha256=base.problem_index_sha256,
            m6_contract_id=base.m6_contract_id,
            m6_contract_sha256=base.m6_contract_sha256,
            m6_population_id=base.m6_population_id,
            m6_population_sha256=base.m6_population_sha256,
            policy=base.policy,
            rates=base.rates,
            fit_config=base.fit_config,
            search_config=base.search_config,
            collision_backend=base.collision_backend,
            jagua_container_guard=base.jagua_container_guard,
            problems=problems,
            candidate_sets=candidate_sets,
            instances=(base.instances[0],),
            horizon_end=base.horizon_end,
        )
        runtime = M7ReplayRuntime(
            replay_input=replay_input,
            runtime_candidates={first_problem_id: single_verified},
            rules=runtime.rules,
        )
    result = run_m7_replay(
        runtime.replay_input,
        runtime.runtime_candidates,
        runtime.rules,
    )
    return runtime, result


def _fake_projection_attestation(
    *,
    corpus_id: str,
    stream_id: str,
    policy_id: str,
    registration_kind: str = "calibration",
    registration_id: str | None = None,
    registration_sha: str | None = None,
    control_kind: str | None = None,
    exact_audit_arm: str | None = None,
    exact_material_rule: str | None = None,
    economic_arm: str = "central",
    event_positions: tuple[int, ...] | None = None,
    source_event_ids: tuple[str, ...] | None = None,
    material_key: str = "fake-material",
    all_work_known: bool = False,
    underlying_policy_id: str | None = None,
):
    from yieldforge.baseline.policies import M7PolicyName, policy_identity
    from yieldforge.baseline.replay import m7_semantic_runtime_sha256
    from yieldforge.experiments.contracts import semantic_sha256
    from yieldforge.realistic_falsification.adapter import (
        M11CandidateActionParity,
        M11KnownVisibleLocalPrefix,
        M11M7ProjectionAttestation,
        M11SourceEventMap,
    )

    underlying = underlying_policy_id or (
        "age_regularity" if policy_id == "known_only_m9_two_ply_scrap" else policy_id
    )
    count = len(event_positions) if event_positions is not None else 24
    positions = event_positions or tuple(range(count))
    runtime, _ = _fake_m7_runtime_and_result(underlying, count, economic_arm)
    replay_input = runtime.replay_input
    candidate_by_problem = {item.problem_id: item for item in replay_input.candidate_sets}
    event_maps = []
    prefixes = []
    parity = []
    for local_position, instance in enumerate(replay_input.instances):
        source_position = positions[local_position]
        source_event_id = (
            source_event_ids[local_position]
            if source_event_ids is not None
            else f"yfm11e-{source_position + 1:024x}"
        )
        payload_id = f"yfm11pl-{source_position + 1:024x}"
        event_maps.append(
            M11SourceEventMap(
                local_event_position=local_position,
                compatibility_event_id=instance.event_id,
                source_event_position=source_position,
                source_event_id=source_event_id,
                source_event_content_sha256=f"sha256:{source_position + 1:064x}",
                source_material_key=material_key,
                projected_material_key=material_key,
                payload_id=payload_id,
                released_at=instance.released_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
        prefixes.append(
            M11KnownVisibleLocalPrefix(
                local_event_position=local_position,
                source_as_of_event_position=source_position,
                source_as_of_event_id=source_event_id,
                visible_source_event_positions=positions[: local_position + 1],
                visible_local_event_positions=tuple(range(local_position + 1)),
                visibility_rule=(
                    "hard_null_all_registered_work_known"
                    if all_work_known
                    else "m11_known_at_filtered_to_registered_slice_and_material"
                ),
            )
        )
        candidates = candidate_by_problem[instance.problem_id].candidate_ids
        parity.append(
            M11CandidateActionParity(
                local_event_position=local_position,
                source_event_id=source_event_id,
                payload_id=payload_id,
                runtime_problem_id=instance.problem_id,
                source_candidate_ids=candidates,
                projected_candidate_ids=candidates,
                runtime_candidate_ids=candidates,
                source_binding_sha256s=tuple(
                    f"sha256:{local_position * 16 + index + 1:064x}"
                    for index, _ in enumerate(candidates)
                ),
                standard_action_ids=tuple(
                    f"m7-standard:{candidate_id}" for candidate_id in candidates
                ),
                projection_rule=(
                    "hard_null_single_action"
                    if registration_kind == "hard_null"
                    else "all_registered_candidates"
                ),
            )
        )
    roots = _roots()
    source_sha = "sha256:" + semantic_sha256({"stream_id": stream_id})
    registration_id = registration_id or f"fake-calibration-registration:{stream_id}"
    registration_sha = registration_sha or (
        "sha256:" + semantic_sha256({"registration": registration_id})
    )
    values = {
        "schema_version": "yieldforge.m11-m7-runtime-attestation.v1",
        "gate1_result_id": roots.gate1_evaluation_result_id,
        "gate1_result_content_sha256": roots.gate1_evaluation_result_content_sha256,
        "gate2_result_id": roots.gate2_evaluation_result_id,
        "gate2_result_content_sha256": roots.gate2_evaluation_result_content_sha256,
        "gate3_config_id": roots.gate3_config_id,
        "gate3_config_content_sha256": roots.gate3_config_content_sha256,
        "population_id": roots.population_id,
        "population_content_sha256": roots.population_content_sha256,
        "registration_kind": registration_kind,
        "control_kind": control_kind,
        "registered_exact_audit_arm": exact_audit_arm,
        "registered_exact_audit_material_rule": exact_material_rule,
        "source_registration_id": registration_id,
        "source_registration_content_sha256": registration_sha,
        "source_stream_id": stream_id,
        "source_stream_content_sha256": source_sha,
        "corpus_id": corpus_id,
        "economic_arm": economic_arm,
        "policy": policy_identity(M7PolicyName(underlying)),
        "material_key": material_key,
        "reference_area_key": "fake-reference-area",
        "reference_area": 100.0,
        "rates": replay_input.rates,
        "source_event_map": tuple(event_maps),
        "known_visible_local_prefixes": tuple(prefixes),
        "candidate_action_parity": tuple(parity),
        "m7_replay_input_id": replay_input.input_id,
        "m7_replay_input_content_sha256": replay_input.content_sha256,
        "m7_runtime_semantic_sha256": m7_semantic_runtime_sha256(runtime),
        "collision_backend": replay_input.collision_backend,
        "ledger_scope": "single_material_independent_substream",
        "compatibility_dto_only": True,
        "native_m7_evidence_persistence_authorized": False,
    }
    draft = M11M7ProjectionAttestation.model_construct(
        attestation_id="yfm11m7a-" + "0" * 24,
        content_sha256="sha256:" + "0" * 64,
        **values,
    )
    digest = semantic_sha256(
        draft,
        excluded_fields={"attestation_id", "content_sha256"},
    )
    return M11M7ProjectionAttestation(
        attestation_id=f"yfm11m7a-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **values,
    ), runtime


def _fake_m7_steps(runtime):
    from yieldforge.baseline.replay import (
        apply_m7_action_descriptor,
        enumerate_m7_action_catalog,
        initial_m7_cursor,
        select_m7_fallback,
    )

    cursor = initial_m7_cursor(runtime.replay_input)
    steps = []
    while cursor.next_event_position < len(runtime.replay_input.instances):
        catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)
        selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
        descriptor = next(item for item in catalog.actions if item.action_id == selection.action_id)
        step = apply_m7_action_descriptor(
            runtime,
            cursor=cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=selection.decision_key,
        )
        steps.append(step)
        cursor = step.cursor
    return tuple(steps)


@cache
def _fake_calibration_observation(corpus_id: str, stream_id: str, policy_id: str):
    from yieldforge.experiments.contracts import semantic_sha256

    confirmation = _confirmation()
    attestation, runtime = _fake_projection_attestation(
        corpus_id=corpus_id,
        stream_id=stream_id,
        policy_id=policy_id,
    )
    replay_input = runtime.replay_input
    is_m9 = policy_id == "known_only_m9_two_ply_scrap"
    if not is_m9:
        _, result = _fake_m7_runtime_and_result(policy_id)
        steps = _fake_m7_steps(runtime)
        decisions = []
        for position, (mapping, event, step) in enumerate(
            zip(attestation.source_event_map, result.events, steps, strict=True)
        ):
            action_id = (
                f"m7-standard:{event.action.candidate_id}"
                if event.action.kind.value == "open_standard_sheet"
                else event.action.action_id
            )
            decisions.append(
                confirmation.build_gate3_decision_trace(
                    event_position=position,
                    event_id=mapping.source_event_id,
                    arm="B",
                    algorithm="m7_policy",
                    visibility="released_only",
                    policy_id=policy_id,
                    standard_candidate_set_sha256=event.action.candidate_set_sha256,
                    search_config_sha256="sha256:" + "a" * 64,
                    compute_budget_sha256="sha256:" + "b" * 64,
                    search_runtime_sha256=None,
                    action_catalog_sha256="sha256:" + semantic_sha256({"action": action_id}),
                    action_ids=(action_id,),
                    baseline_action_id=action_id,
                    selected_action_id=action_id,
                    selected_immediate_cost=format(step.selected_context.immediate_net_cost, ".6f"),
                    baseline_immediate_cost=format(step.selected_context.immediate_net_cost, ".6f"),
                    m9_root_scores=(),
                    inventory_before_sha256=confirmation.gate3_inventory_sha256(
                        event.inventory_before
                    ),
                    inventory_after_sha256=confirmation.gate3_inventory_sha256(
                        event.inventory_after
                    ),
                    returned_lineage_root_ids=(),
                    selected_lineage_root_id=None,
                    m9_catalog_count=0,
                    m9_explicit_transition_count=0,
                    m9_continuation_event_count=0,
                    m9_start_event_position=None,
                    m9_stop_event_position=None,
                )
            )
        applied_contexts = tuple(
            confirmation.build_gate3_applied_action_context(decision=decision, step=step)
            for decision, step in zip(decisions, steps, strict=True)
        )
        costs = _gate3_ledger_from_replay(result.terminal.cumulative_costs)
        shard = confirmation.build_gate3_shard_trace(
            roots=_roots(),
            stream_id=stream_id,
            corpus_id=corpus_id,
            shard_id="fake-material-shard",
            material_key="fake-material",
            arm="B",
            policy_id=policy_id,
            visibility="released_only",
            projection_binding_sha256=attestation.content_sha256,
            decisions=tuple(decisions),
            final_costs=costs,
        )
        material = confirmation.build_gate3_calibration_material_replay(
            roots=_roots(),
            corpus_id=corpus_id,
            stream_id=stream_id,
            policy_id=policy_id,
            projection_attestation=attestation,
            replay_input=replay_input,
            shard_trace=shard,
            m7_replay_result=result,
            m7_applied_contexts=applied_contexts,
        )
    else:
        decisions = []
        transitions = []
        _, applied_result = _fake_m7_runtime_and_result("age_regularity")
        applied_steps = _fake_m7_steps(runtime)
        for position, (mapping, event, step) in enumerate(
            zip(attestation.source_event_map, applied_result.events, applied_steps, strict=True)
        ):
            action_id = (
                f"m7-standard:{event.action.candidate_id}"
                if event.action.kind.value == "open_standard_sheet"
                else event.action.action_id
            )
            decision = confirmation.build_gate3_decision_trace(
                event_position=position,
                event_id=mapping.source_event_id,
                arm="B",
                algorithm="m9_two_ply",
                visibility="known_only",
                policy_id=policy_id,
                standard_candidate_set_sha256="sha256:" + "a" * 64,
                search_config_sha256="sha256:" + "a" * 64,
                compute_budget_sha256="sha256:" + "b" * 64,
                search_runtime_sha256=attestation.m7_runtime_semantic_sha256,
                action_catalog_sha256="sha256:" + semantic_sha256({"action": action_id}),
                action_ids=(action_id,),
                baseline_action_id=action_id,
                selected_action_id=action_id,
                selected_immediate_cost=format(step.selected_context.immediate_net_cost, ".6f"),
                baseline_immediate_cost=format(step.selected_context.immediate_net_cost, ".6f"),
                m9_root_scores=((action_id, format(event.cumulative_costs.net_cost, ".6f")),),
                inventory_before_sha256=confirmation.gate3_inventory_sha256(event.inventory_before),
                inventory_after_sha256=confirmation.gate3_inventory_sha256(event.inventory_after),
                returned_lineage_root_ids=(),
                selected_lineage_root_id=None,
                m9_catalog_count=1,
                m9_explicit_transition_count=1,
                m9_continuation_event_count=0,
                m9_start_event_position=position,
                m9_stop_event_position=position + 1,
            )
            decisions.append(decision)
            transitions.append(
                confirmation.build_gate3_calibration_m9_transition(
                    decision=decision,
                    step=step,
                )
            )
        terminal = applied_result.terminal
        costs = _gate3_ledger_from_replay(terminal.cumulative_costs)
        shard = confirmation.build_gate3_shard_trace(
            roots=_roots(),
            stream_id=stream_id,
            corpus_id=corpus_id,
            shard_id="fake-material-shard",
            material_key="fake-material",
            arm="B",
            policy_id=policy_id,
            visibility="known_only",
            projection_binding_sha256=attestation.content_sha256,
            decisions=tuple(decisions),
            final_costs=costs,
        )
        material = confirmation.build_gate3_calibration_material_replay(
            roots=_roots(),
            corpus_id=corpus_id,
            stream_id=stream_id,
            policy_id=policy_id,
            projection_attestation=attestation,
            replay_input=replay_input,
            shard_trace=shard,
            m9_transitions=tuple(transitions),
            m9_terminal=terminal,
        )
    return confirmation.build_gate3_calibration_observation(
        roots=_roots(),
        corpus_id=corpus_id,
        stream_id=stream_id,
        policy_id=policy_id,
        material_replays=(material,),
    )


def test_calibration_material_replay_binds_standard_catalog_id_and_raw_result() -> None:
    observation = _fake_calibration_observation(
        "loco-2dics",
        "yfm11st-0000000000000000000003e8",
        "myopic_geometry",
    )
    material = observation.material_replays[0]
    result = material.m7_replay_result

    assert result is not None
    assert material.projection_attestation.policy == result.policy == material.replay_input.policy
    standard_position = next(
        index
        for index, event in enumerate(result.events)
        if event.action.kind.value == "open_standard_sheet"
    )
    event = result.events[standard_position]
    decision = material.shard_trace.decisions[standard_position]
    assert event.action.action_id.startswith("yfm7a-")
    assert decision.selected_action_id == f"m7-standard:{event.action.candidate_id}"
    assert decision.selected_action_id != event.action.action_id


def test_backend_direct_b_packages_policy_context_cost_not_delta_ledger_cost() -> None:
    from yieldforge.realistic_falsification.adapter import M11MaterialRuntimeProjection
    from yieldforge.realistic_falsification.gate3_backend import (
        execute_gate3_material_shard,
    )

    confirmation = _confirmation()
    stream_id = "yfm11st-0000000000000000000003eb"
    attestation, runtime = _fake_projection_attestation(
        corpus_id="loco-2dics",
        stream_id=stream_id,
        policy_id="age_regularity",
    )
    execution = execute_gate3_material_shard(
        projection=M11MaterialRuntimeProjection(
            attestation=attestation,
            runtime=runtime,
        ),
        roots=_roots(),
        arm="B",
        policy_id="age_regularity",
    )
    result = execution.m7_replay_result
    assert result is not None
    contexts = tuple(
        confirmation.build_gate3_applied_action_context(decision=decision, step=step)
        for decision, step in zip(
            execution.shard_trace.decisions,
            execution.steps,
            strict=True,
        )
    )
    material = confirmation.build_gate3_calibration_material_replay(
        roots=_roots(),
        corpus_id="loco-2dics",
        stream_id=stream_id,
        policy_id="age_regularity",
        projection_attestation=attestation,
        replay_input=runtime.replay_input,
        shard_trace=execution.shard_trace,
        m7_replay_result=result,
        m7_applied_contexts=contexts,
    )

    differing = tuple(
        (decision.selected_immediate_cost, format(event.delta_costs.net_cost, ".6f"))
        for decision, event in zip(
            material.shard_trace.decisions,
            result.events,
            strict=True,
        )
        if decision.selected_immediate_cost != format(event.delta_costs.net_cost, ".6f")
    )
    assert differing
    assert material.final_costs == execution.shard_trace.final_costs


def test_calibration_m9_variant_nests_applied_transitions_and_terminal() -> None:
    observation = _fake_calibration_observation(
        "lectra-m3-m4",
        "yfm11st-0000000000000000000003e9",
        "known_only_m9_two_ply_scrap",
    )
    material = observation.material_replays[0]

    assert material.m7_replay_result is None
    assert material.projection_attestation.policy.name.value == "age_regularity"
    assert len(material.m9_transitions) == 24
    assert material.m9_terminal is not None
    assert all(
        transition.action_kind == transition.applied_event.action.kind.value
        for transition in material.m9_transitions
    )
    assert material.final_costs == observation.final_costs


def test_calibration_observation_rejects_resigned_caller_ledger_substitution() -> None:
    from yieldforge.experiments.contracts import semantic_sha256

    confirmation = _confirmation()
    observation = _fake_calibration_observation(
        "loco-2dics",
        "yfm11st-0000000000000000000003ea",
        "net_cost",
    )
    payload = observation.model_dump(mode="python", round_trip=True)
    payload["final_costs"] = _ledger(purchase="1.000000").model_dump(
        mode="python",
        round_trip=True,
    )
    semantic = observation.model_dump(mode="json")
    semantic["final_costs"] = _ledger(purchase="1.000000").model_dump(mode="json")
    semantic.pop("observation_id")
    semantic.pop("content_sha256")
    digest = semantic_sha256(semantic)
    payload["observation_id"] = f"yfm11g3calobs-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"

    with pytest.raises(ValidationError, match="ledger/opening addition"):
        confirmation.Gate3CalibrationObservation.model_validate(payload, strict=True)


@dataclass
class _FakeGate3Backend:
    loco_savings: str = "3.000000"
    lectra_savings: str = "3.000000"
    unknown: str = "2.000000"
    invalid_validity: bool = False
    wrong_exact_freeze: bool = False
    fail_central_stream: str | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def calibration_stream_ids(self, corpus_id: str) -> tuple[str, ...]:
        corpus = 1 if corpus_id == "lectra-m3-m4" else 2
        return tuple(f"yfm11st-{corpus * 100 + index:024x}" for index in range(8))

    def confirmation_stream_ids(self, corpus_id: str) -> tuple[str, ...]:
        corpus = 3 if corpus_id == "lectra-m3-m4" else 4
        return tuple(f"yfm11st-{corpus * 100 + index:024x}" for index in range(20))

    def execute_calibration_stream(self, *, corpus_id: str, stream_id: str, policy_id: str):
        self.calls.append(("calibration", corpus_id, stream_id, policy_id))
        return _fake_calibration_observation(corpus_id, stream_id, policy_id)

    def execute_validity_controls(self, *, roots, baseline_freezes):
        self.calls.append(("validity",))
        exact_freezes = baseline_freezes
        if self.wrong_exact_freeze:
            exact_freezes = tuple(
                _baseline_freeze(
                    corpus_id=item.corpus_id,
                    selected_policy_id=(
                        "net_cost" if item.selected_policy_id != "net_cost" else "age_regularity"
                    ),
                )
                for item in baseline_freezes
            )
        return _confirmation().evaluate_gate3_validity_controls(
            roots=roots,
            hard_nulls=_hard_null_controls(
                fail_first=self.invalid_validity,
                baseline_freezes=baseline_freezes,
            ),
            twin_controls=_twin_controls(corpus_id="lectra-m3-m4", savings="0.000000")
            + _twin_controls(corpus_id="loco-2dics", savings="0.000000"),
            exact_audits=_exact_audits(baseline_freezes=exact_freezes),
        )

    def execute_central_stream(self, *, roots, corpus_id, stream_id, baseline_freeze):
        self.calls.append(("central", corpus_id, stream_id))
        if stream_id == self.fail_central_stream:
            raise RuntimeError("synthetic central replay failure")
        return _fake_cell_for_freeze(
            corpus_id=corpus_id,
            stream_id=stream_id,
            freeze=baseline_freeze,
            savings=(self.loco_savings if corpus_id == "loco-2dics" else self.lectra_savings),
            unknown=self.unknown,
        )


def test_early_confirmation_runs_loco_first_and_stops_before_lectra_on_valid_red() -> None:
    confirmation = _confirmation()
    backend = _FakeGate3Backend(loco_savings="0.000000")

    result = confirmation.run_gate3_early_confirmation(roots=_roots(), backend=backend)

    assert result.status == "insufficient_headroom"
    assert result.disposition == "ABANDON"
    assert result.terminal is True
    assert len(result.calibration_attempts) == 96
    assert len(result.central_attempts) == 20
    assert all(
        item.status == "success" and item.cell is not None for item in result.central_attempts
    )
    assert tuple(item.corpus_id for item in result.central_attempts) == ("loco-2dics",) * 20
    assert result.corpus_summaries[0].central_green is False
    assert result.central_statistics is None
    assert all(call[1] != "lectra-m3-m4" for call in backend.calls if call[0] == "central")
    assert ("central_lectra", "skipped_terminal_prerequisite") in result.skipped_stages
    assert ("fixed_adverse", "skipped_terminal_prerequisite") in result.skipped_stages


def test_early_confirmation_preserves_failed_cell_and_bootstraps_only_complete_raw_cells() -> None:
    confirmation = _confirmation()
    backend = _FakeGate3Backend()
    failed_id = backend.confirmation_stream_ids("loco-2dics")[7]
    backend.fail_central_stream = failed_id

    result = confirmation.run_gate3_early_confirmation(roots=_roots(), backend=backend)

    assert result.status == "invalid_test"
    assert result.disposition == "INVALID_NONZERO"
    failed = tuple(item for item in result.central_attempts if item.status == "failure")
    assert len(failed) == 1
    assert failed[0].stream_id == failed_id
    assert "synthetic central replay failure" in failed[0].failure_detail
    assert result.corpus_summaries == ()
    assert result.central_statistics is None
    assert result.bootstrap_draw_count == 0


def test_early_confirmation_stops_before_economics_when_core_validity_fails() -> None:
    confirmation = _confirmation()
    backend = _FakeGate3Backend(invalid_validity=True)

    result = confirmation.run_gate3_early_confirmation(roots=_roots(), backend=backend)

    assert result.status == "invalid_test"
    assert result.validity_receipt is not None
    assert result.validity_receipt.status == "invalid"
    assert result.central_attempts == ()
    assert not any(call[0] == "central" for call in backend.calls)
    assert ("central_loco", "skipped_terminal_prerequisite") in result.skipped_stages


def test_early_confirmation_exposes_continuation_only_after_all_raw_central_groups_pass() -> None:
    confirmation = _confirmation()
    backend = _FakeGate3Backend()

    result = confirmation.run_gate3_early_confirmation(roots=_roots(), backend=backend)

    assert result.status == "central_survived"
    assert result.disposition == "CONTINUE_GATE3"
    assert result.terminal is False
    assert len(result.central_attempts) == 40
    assert tuple(item.corpus_id for item in result.central_attempts[:20]) == ("loco-2dics",) * 20
    assert tuple(item.corpus_id for item in result.central_attempts[20:]) == ("lectra-m3-m4",) * 20
    assert result.central_statistics is not None
    assert result.central_statistics.all_groups_central_green is True
    assert result.bootstrap_draw_count == 10000
    assert result.skipped_stages == ()
    assert result.continuation_stages == (
        "fixed_adverse",
        "zero_terminal_credit",
        "conservative_eligibility",
        "expanded_catalog_diagnostic",
        "permissive_eligibility_diagnostic",
        "support_metrics",
        "deployable_capture",
    )


def test_early_result_rederives_both_freezes_and_exact_calibration_order() -> None:
    from yieldforge.experiments.contracts import semantic_sha256

    confirmation = _confirmation()
    result = confirmation.run_gate3_early_confirmation(
        roots=_roots(),
        backend=_FakeGate3Backend(loco_savings="0.000000"),
    )
    assert (
        confirmation.Gate3EarlyConfirmationResult.model_validate(
            result.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        == result
    )

    freeze_payload = result.model_dump(mode="python", round_trip=True)
    alternate = _baseline_freeze(
        corpus_id="lectra-m3-m4",
        selected_policy_id="net_cost",
    ).model_dump(mode="python", round_trip=True)
    freeze_payload["baseline_freezes"] = (
        alternate,
        freeze_payload["baseline_freezes"][1],
    )
    with pytest.raises(ValidationError, match="raw calibration replays"):
        confirmation.Gate3EarlyConfirmationResult.model_validate(
            freeze_payload,
            strict=True,
        )

    order_payload = result.model_dump(mode="python", round_trip=True)
    attempts = list(order_payload["calibration_attempts"])
    attempts[0], attempts[1] = attempts[1], attempts[0]
    order_payload["calibration_attempts"] = tuple(attempts)
    semantic = result.model_dump(mode="json")
    semantic["calibration_attempts"][0], semantic["calibration_attempts"][1] = (
        semantic["calibration_attempts"][1],
        semantic["calibration_attempts"][0],
    )
    semantic.pop("result_id")
    semantic.pop("content_sha256")
    digest = semantic_sha256(semantic)
    order_payload["result_id"] = f"yfm11g3early-{digest[:24]}"
    order_payload["content_sha256"] = f"sha256:{digest}"
    with pytest.raises(ValidationError, match="attempt order"):
        confirmation.Gate3EarlyConfirmationResult.model_validate(
            order_payload,
            strict=True,
        )

    with pytest.raises(ValueError, match="validity controls differ from calibration freezes"):
        confirmation.run_gate3_early_confirmation(
            roots=_roots(),
            backend=_FakeGate3Backend(
                loco_savings="0.000000",
                wrong_exact_freeze=True,
            ),
        )
