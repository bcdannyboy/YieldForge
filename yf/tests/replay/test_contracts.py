from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
import shapely
from pydantic import ValidationError

from yieldforge.domain import Part
from yieldforge.replay.contracts import (
    M0_EVENT_STAGE_ORDER,
    ReplayCostLedger,
    ReplayEngineIdentity,
    ReplayPolicyIdentity,
    ReplayRateManifest,
    ReplaySummary,
    StandardSheetSpec,
    build_replay_input,
    rounded_cost,
)
from yieldforge.reuse.contracts import (
    FitSearchConfig,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
)


def _material() -> MaterialIdentity:
    return MaterialIdentity(
        material_code="m5-assumed-material",
        grade="m5-assumed-grade",
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


def _input():  # type: ignore[no-untyped-def]
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
            storage_cost_per_area_hour=0.01,
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
                starts_at + timedelta(hours=1),
                _part("m5-part-b", 3.0),
                material,
            ),
        ),
        horizon_end=starts_at + timedelta(hours=2),
    )


def test_replay_input_is_strict_content_addressed_and_chronological() -> None:
    replay_input = _input()

    assert replay_input.input_id == f"yfrpi-{replay_input.content_sha256[7:31]}"
    assert tuple(order.sequence for order in replay_input.orders) == (0, 1)
    assert tuple(order.order_id for order in replay_input.orders) == (
        "m5-order-a",
        "m5-order-b",
    )
    assert replay_input.orders[0].released_at < replay_input.orders[1].released_at
    assert replay_input.orders[-1].released_at < replay_input.horizon_end
    assert replay_input.event_stage_order == M0_EVENT_STAGE_ORDER
    assert replay_input.engine.shapely_version == shapely.__version__
    assert replay_input.policy.seed == 0
    assert replay_input.policy.information_set == "released_order_and_current_inventory_only"


def test_equivalent_timestamp_offsets_canonicalize_to_utc() -> None:
    replay_input = _input()
    payload = replay_input.model_dump(mode="python")
    payload["orders"] = tuple(
        order.model_copy(
            update={"released_at": order.released_at.astimezone(timezone(timedelta(hours=-8)))}
        )
        for order in replay_input.orders
    )
    rebound = build_replay_input(
        m0_contract_id=payload["m0_contract_id"],
        m0_contract_sha256=payload["m0_contract_sha256"],
        m4_input_id=payload["m4_input_id"],
        m4_input_sha256=payload["m4_input_sha256"],
        m4_result_id=payload["m4_result_id"],
        m4_result_sha256=payload["m4_result_sha256"],
        engine=payload["engine"],
        policy=payload["policy"],
        fit_config=payload["fit_config"],
        search_config=payload["search_config"],
        rates=payload["rates"],
        standard_sheet=payload["standard_sheet"],
        order_specs=tuple(
            (order.order_id, order.released_at, order.part, order.material)
            for order in payload["orders"]
        ),
        horizon_end=payload["horizon_end"].astimezone(timezone(timedelta(hours=-8))),
    )

    assert rebound == replay_input


def test_replay_input_rejects_nonmonotone_events_horizon_and_material_drift() -> None:
    replay_input = _input()
    starts_at = replay_input.orders[0].released_at

    with pytest.raises(ValidationError, match="strictly increasing"):
        replay_input.model_copy(
            update={
                "orders": (
                    replay_input.orders[0],
                    replay_input.orders[1].model_copy(update={"released_at": starts_at}),
                )
            }
        ).__class__.model_validate(
            replay_input.model_copy(
                update={
                    "orders": (
                        replay_input.orders[0],
                        replay_input.orders[1].model_copy(update={"released_at": starts_at}),
                    )
                }
            ).model_dump(mode="python")
        )

    with pytest.raises(ValidationError, match="horizon"):
        replay_input.__class__.model_validate(
            replay_input.model_copy(update={"horizon_end": starts_at}).model_dump(mode="python")
        )

    changed_material = _material().model_copy(update={"grade": "different"})
    with pytest.raises(ValidationError, match="material"):
        replay_input.__class__.model_validate(
            replay_input.model_copy(
                update={
                    "orders": (
                        replay_input.orders[0].model_copy(update={"material": changed_material}),
                        replay_input.orders[1],
                    )
                }
            ).model_dump(mode="python")
        )


def test_rate_manifest_and_cost_ledger_reject_nonfinite_or_unreconciled_values() -> None:
    assert rounded_cost(0.0000005) == 0.000001

    with pytest.raises(ValidationError, match="finite"):
        ReplayRateManifest(
            purchase_cost_per_area=math.inf,
            storage_cost_per_area_hour=0.01,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        )

    ledger = ReplayCostLedger(
        purchase_cost=100.0,
        storage_cost=0.9,
        return_handling_cost=4.0,
        retrieval_handling_cost=3.0,
        scrap_proceeds=0.0,
        terminal_scrap_credit=3.0,
        net_cost=104.9,
    )
    assert ledger.net_cost == 104.9

    with pytest.raises(ValidationError, match="net cost"):
        ReplayCostLedger(
            purchase_cost=100.0,
            storage_cost=0.9,
            return_handling_cost=4.0,
            retrieval_handling_cost=3.0,
            scrap_proceeds=0.0,
            terminal_scrap_credit=3.0,
            net_cost=999.0,
        )


def test_replay_summary_reconciles_action_counts() -> None:
    summary = ReplaySummary(
        order_count=2,
        fulfilled_order_count=2,
        full_sheet_opening_count=1,
        remnant_retrieval_count=1,
        returned_remnant_count=2,
        terminal_remnant_count=1,
        final_net_cost=104.9,
        technical_decision="pass",
    )
    assert summary.fulfilled_order_count == 2

    with pytest.raises(ValidationError, match="fulfilled"):
        ReplaySummary(
            order_count=2,
            fulfilled_order_count=1,
            full_sheet_opening_count=1,
            remnant_retrieval_count=1,
            returned_remnant_count=2,
            terminal_remnant_count=1,
            final_net_cost=104.9,
            technical_decision="pass",
        )
