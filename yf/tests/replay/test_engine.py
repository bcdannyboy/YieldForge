from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import shapely
from pydantic import ValidationError

from yieldforge.domain import Part
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.replay.contracts import (
    InventoryItem,
    ReplayActionKind,
    ReplayEngineIdentity,
    ReplayPolicyIdentity,
    ReplayRateManifest,
    ReplayResult,
    StandardSheetSpec,
    build_replay_input,
)
from yieldforge.replay.engine import execute_action, run_replay, select_action
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import (
    FitSearchConfig,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
)

YF_ROOT = Path(__file__).parents[2]
M0_CONTRACT_PATH = YF_ROOT / "experiments" / "m0-contract-v1.json"


def _material(*, grade: str = "m5-assumed-grade") -> MaterialIdentity:
    return MaterialIdentity(
        material_code="m5-assumed-material",
        grade=grade,
        thickness="m5-assumed-thickness",
        surface="m5-assumed-surface",
        grain="m5-assumed-grain",
        provenance=MaterialProvenance.ASSUMED,
    )


def _part(part_id: str, width: float) -> Part:
    return Part(
        id=part_id,
        shape=[(0.0, 0.0), (width, 0.0), (width, 10.0), (0.0, 10.0)],
        demand=1,
        allowed_orientations=[0.0],
    )


def _input(  # type: ignore[no-untyped-def]
    *,
    storage_rate: float = 0.01,
    second_release_hours: float = 1.0,
):
    material = _material()
    starts_at = datetime(2026, 1, 1, tzinfo=UTC)
    return build_replay_input(
        m0_contract_id="yfm0-" + "1" * 24,
        m0_contract_sha256="sha256:" + "2" * 64,
        m4_input_id="yfri-" + "3" * 24,
        m4_input_sha256="sha256:" + "4" * 64,
        m4_result_id="yfrr-" + "5" * 24,
        m4_result_sha256="sha256:" + "6" * 64,
        engine=ReplayEngineIdentity(shapely_version=shapely.__version__),
        policy=ReplayPolicyIdentity(),
        fit_config=RemnantFitConfig(),
        search_config=FitSearchConfig(
            grid_columns=5,
            grid_rows=5,
            maximum_candidates=512,
        ),
        rates=ReplayRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=storage_rate,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        ),
        standard_sheet=StandardSheetSpec(
            stock_code="m5-standard-sheet",
            length=10.0,
            height=10.0,
            material=material,
        ),
        order_specs=(
            ("m5-order-a", starts_at, _part("m5-part-a", 4.0), material),
            (
                "m5-order-b",
                starts_at + timedelta(hours=second_release_hours),
                _part("m5-part-b", 3.0),
                material,
            ),
        ),
        horizon_end=starts_at + timedelta(hours=2),
    )


def _rules():  # type: ignore[no-untyped-def]
    contract = M0ExperimentContract.model_validate_json(M0_CONTRACT_PATH.read_text(), strict=True)
    return rule_set_from_m0(contract.remnant_eligibility)


def test_policy_signature_has_no_manifest_or_future_orders() -> None:
    assert tuple(inspect.signature(select_action).parameters) == (
        "current_order",
        "inventory",
        "sheet",
        "fit_config",
        "search_config",
    )


def test_policy_opens_sheet_then_reuses_first_sorted_compatible_remnant() -> None:
    replay_input = _input()
    first_order, second_order = replay_input.orders

    first = select_action(
        first_order,
        (),
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    assert first.kind is ReplayActionKind.OPEN_STANDARD_SHEET

    first_execution = execute_action(
        first,
        first_order,
        (),
        replay_input.standard_sheet,
        _rules(),
        replay_input.fit_config,
    )
    returned = tuple(
        InventoryItem(remnant=remnant, entered_at=first_order.released_at)
        for remnant in first_execution.evidence.returned_remnants
    )
    assert len(returned) == 1

    second = select_action(
        second_order,
        tuple(reversed(returned)),
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    assert second.kind is ReplayActionKind.CONSUME_REMNANT
    assert second.selected_remnant_id == returned[0].remnant.remnant_id


def test_exact_sheet_and_recursive_remnant_execution_reconcile_geometry() -> None:
    replay_input = _input()
    first_order, second_order = replay_input.orders
    first = select_action(
        first_order,
        (),
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    first_execution = execute_action(
        first,
        first_order,
        (),
        replay_input.standard_sheet,
        _rules(),
        replay_input.fit_config,
    )

    assert first_execution.evidence.accounting.parent_remnant_area == 100.0
    assert first_execution.evidence.accounting.placed_area == 40.0
    assert first_execution.evidence.accounting.retained_child_area == 60.0
    assert first_execution.evidence.accounting.scrap_area == 0.0
    assert len(first_execution.inventory_after) == 1
    first_remnant = first_execution.inventory_after[0].remnant
    assert first_remnant.geometry.area == 60.0
    assert first_remnant.lineage.generation == 1

    second = select_action(
        second_order,
        first_execution.inventory_after,
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    second_execution = execute_action(
        second,
        second_order,
        first_execution.inventory_after,
        replay_input.standard_sheet,
        _rules(),
        replay_input.fit_config,
    )

    assert second_execution.evidence.accounting.parent_remnant_area == 60.0
    assert second_execution.evidence.accounting.placed_area == 30.0
    assert second_execution.evidence.accounting.retained_child_area == 30.0
    assert second_execution.evidence.accounting.scrap_area == 0.0
    assert len(second_execution.inventory_after) == 1
    second_remnant = second_execution.inventory_after[0].remnant
    assert second_remnant.geometry.area == 30.0
    assert second_remnant.lineage.generation == 2
    assert second_remnant.lineage.parent_remnant_id == first_remnant.remnant_id


def test_material_mismatch_and_bounded_miss_fall_back_to_sheet() -> None:
    replay_input = _input()
    first_order, second_order = replay_input.orders
    first = select_action(
        first_order,
        (),
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    execution = execute_action(
        first,
        first_order,
        (),
        replay_input.standard_sheet,
        _rules(),
        replay_input.fit_config,
    )
    incompatible = execution.inventory_after[0].model_copy(
        update={
            "remnant": execution.inventory_after[0].remnant.model_copy(
                update={"material": _material(grade="different")}
            )
        }
    )

    action = select_action(
        second_order,
        (incompatible,),
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    assert action.kind is ReplayActionKind.OPEN_STANDARD_SHEET

    too_large_for_remnant = second_order.model_copy(update={"part": _part("m5-part-wide", 7.0)})
    bounded_miss = select_action(
        too_large_for_remnant,
        execution.inventory_after,
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    assert bounded_miss.kind is ReplayActionKind.OPEN_STANDARD_SHEET


def test_duplicate_or_missing_inventory_use_fails_closed() -> None:
    replay_input = _input()
    first_order, second_order = replay_input.orders
    first = select_action(
        first_order,
        (),
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    first_execution = execute_action(
        first,
        first_order,
        (),
        replay_input.standard_sheet,
        _rules(),
        replay_input.fit_config,
    )
    item = first_execution.inventory_after[0]

    with pytest.raises(ValueError, match="duplicate"):
        select_action(
            second_order,
            (item, item),
            replay_input.standard_sheet,
            replay_input.fit_config,
            replay_input.search_config,
        )

    selected = select_action(
        second_order,
        (item,),
        replay_input.standard_sheet,
        replay_input.fit_config,
        replay_input.search_config,
    )
    with pytest.raises(ValueError, match="missing"):
        execute_action(
            selected,
            second_order,
            (),
            replay_input.standard_sheet,
            _rules(),
            replay_input.fit_config,
        )


def test_full_sheet_infeasibility_fails_closed() -> None:
    replay_input = _input()
    impossible = replay_input.orders[0].model_copy(update={"part": _part("too-wide", 11.0)})

    with pytest.raises(ValueError, match="standard sheet"):
        select_action(
            impossible,
            (),
            replay_input.standard_sheet,
            replay_input.fit_config,
            replay_input.search_config,
        )


def test_replay_costs_chronology_and_recursive_inventory() -> None:
    replay_input = _input()
    result = run_replay(replay_input, _rules())

    assert len(result.events) == 2
    first, second = result.events
    assert first.event_stage_order == replay_input.event_stage_order
    assert first.storage_interval_start == first.storage_interval_end
    assert first.action.kind is ReplayActionKind.OPEN_STANDARD_SHEET
    assert first.delta_costs.purchase_cost == 100.0
    assert first.delta_costs.return_handling_cost == 2.0
    assert first.delta_costs.net_cost == 102.0
    assert first.cumulative_costs.net_cost == 102.0

    assert second.storage_interval_start == first.occurred_at
    assert second.storage_interval_end == second.occurred_at
    assert second.action.kind is ReplayActionKind.CONSUME_REMNANT
    assert second.delta_costs.storage_cost == 0.6
    assert second.delta_costs.retrieval_handling_cost == 3.0
    assert second.delta_costs.return_handling_cost == 2.0
    assert second.delta_costs.net_cost == 5.6
    assert second.cumulative_costs.net_cost == 107.6

    assert result.terminal.storage_interval_start == second.occurred_at
    assert result.terminal.delta_costs.storage_cost == 0.3
    assert result.terminal.delta_costs.terminal_scrap_credit == 3.0
    assert result.terminal.delta_costs.net_cost == -2.7
    assert result.terminal.cumulative_costs.net_cost == 104.9
    assert len(result.terminal.inventory_before_liquidation) == 1
    final_remnant = result.terminal.inventory_before_liquidation[0].remnant
    assert final_remnant.geometry.area == 30.0
    assert final_remnant.lineage.generation == 2

    assert result.summary.order_count == 2
    assert result.summary.fulfilled_order_count == 2
    assert result.summary.full_sheet_opening_count == 1
    assert result.summary.remnant_retrieval_count == 1
    assert result.summary.returned_remnant_count == 2
    assert result.summary.terminal_remnant_count == 1
    assert result.summary.final_net_cost == 104.9
    assert result.summary.technical_decision == "pass"


def test_replay_is_deterministic_and_identity_changes_with_semantics() -> None:
    replay_input = _input()
    first = run_replay(replay_input, _rules())
    second = run_replay(replay_input, _rules())

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.result_id == f"yfrpr-{first.content_sha256[7:31]}"

    changed_rate_input = _input(storage_rate=0.02)
    changed_rate_result = run_replay(changed_rate_input, _rules())
    assert changed_rate_input.input_id != replay_input.input_id
    assert changed_rate_result.result_id != first.result_id
    assert changed_rate_result.summary.final_net_cost == 105.8

    changed_time_input = _input(second_release_hours=0.5)
    changed_time_result = run_replay(changed_time_input, _rules())
    assert changed_time_input.input_id != replay_input.input_id
    assert changed_time_result.result_id != first.result_id
    assert changed_time_result.summary.final_net_cost == 104.75


def test_event_identity_and_timeline_tampering_fail_closed() -> None:
    result = run_replay(_input(), _rules())
    first, second = result.events

    with pytest.raises(ValidationError, match="event ID"):
        first.__class__.model_validate(
            first.model_copy(update={"event_id": "yfre-" + "f" * 24}).model_dump(mode="python")
        )

    with pytest.raises(ValidationError, match="storage interval"):
        second.__class__.model_validate(
            second.model_copy(
                update={"storage_interval_start": second.occurred_at + timedelta(hours=1)}
            ).model_dump(mode="python")
        )


def _reidentify_result_payload(payload: dict[str, object]) -> dict[str, object]:
    payload.pop("result_id", None)
    payload.pop("content_sha256", None)
    digest = semantic_sha256(payload)
    payload["result_id"] = f"yfrpr-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return payload


def test_result_rejects_reidentified_order_count_and_cumulative_cost_drift() -> None:
    result = run_replay(_input(), _rules())

    count_payload = result.model_dump(mode="json")
    count_payload["summary"]["order_count"] = 3  # type: ignore[index]
    count_payload["summary"]["technical_decision"] = "open"  # type: ignore[index]
    with pytest.raises(ValidationError, match="order count"):
        ReplayResult.model_validate_json(
            json.dumps(_reidentify_result_payload(count_payload)), strict=True
        )

    cost_payload = result.model_dump(mode="json")
    first_event = cost_payload["events"][0]  # type: ignore[index]
    first_event["cumulative_costs"] = {  # type: ignore[index]
        "purchase_cost": 999.0,
        "storage_cost": 0.0,
        "return_handling_cost": 0.0,
        "retrieval_handling_cost": 0.0,
        "scrap_proceeds": 0.0,
        "terminal_scrap_credit": 0.0,
        "net_cost": 999.0,
    }
    first_event.pop("event_id")  # type: ignore[union-attr]
    event_digest = semantic_sha256(first_event)  # type: ignore[arg-type]
    first_event["event_id"] = f"yfre-{event_digest[:24]}"  # type: ignore[index]
    with pytest.raises(ValidationError, match="cumulative costs"):
        ReplayResult.model_validate_json(
            json.dumps(_reidentify_result_payload(cost_payload)), strict=True
        )
