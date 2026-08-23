"""Pure exact-geometry actions and state transitions for deterministic replay."""

from __future__ import annotations

from dataclasses import dataclass

from shapely import box

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.replay.contracts import (
    InventoryItem,
    ReplayActionEvidence,
    ReplayActionKind,
    ReplayCostLedger,
    ReplayEventRecord,
    ReplayInput,
    ReplayOrder,
    ReplayResult,
    ReplaySummary,
    ReplayTerminalRecord,
    StandardSheetSpec,
    rounded_cost,
)
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.reuse.contracts import (
    FitPlacement,
    FitSearchConfig,
    FitSearchResult,
    FitSearchStatus,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    canonical_polygon_record,
    derive_remnant_id,
)
from yieldforge.reuse.geometry import consume_remnant, material_key, transform_part
from yieldforge.reuse.search import search_fit_witness


@dataclass(frozen=True)
class SelectedAction:
    """One policy selection before exact consumption mutates replay state."""

    kind: ReplayActionKind
    stock: RemnantStock
    search_result: FitSearchResult

    @property
    def selected_remnant_id(self) -> str | None:
        if self.kind is ReplayActionKind.CONSUME_REMNANT:
            return self.stock.remnant_id
        return None


@dataclass(frozen=True)
class ExecutedAction:
    """One completely validated action and its replacement inventory."""

    evidence: ReplayActionEvidence
    inventory_after: tuple[InventoryItem, ...]


def _ledger(
    *,
    purchase_cost: float = 0.0,
    storage_cost: float = 0.0,
    return_handling_cost: float = 0.0,
    retrieval_handling_cost: float = 0.0,
    scrap_proceeds: float = 0.0,
    terminal_scrap_credit: float = 0.0,
) -> ReplayCostLedger:
    terms = {
        "purchase_cost": rounded_cost(purchase_cost),
        "storage_cost": rounded_cost(storage_cost),
        "return_handling_cost": rounded_cost(return_handling_cost),
        "retrieval_handling_cost": rounded_cost(retrieval_handling_cost),
        "scrap_proceeds": rounded_cost(scrap_proceeds),
        "terminal_scrap_credit": rounded_cost(terminal_scrap_credit),
    }
    return ReplayCostLedger(
        **terms,
        net_cost=rounded_cost(
            terms["purchase_cost"]
            + terms["storage_cost"]
            + terms["return_handling_cost"]
            + terms["retrieval_handling_cost"]
            - terms["scrap_proceeds"]
            - terms["terminal_scrap_credit"]
        ),
    )


def _add_ledgers(left: ReplayCostLedger, right: ReplayCostLedger) -> ReplayCostLedger:
    return _ledger(
        purchase_cost=left.purchase_cost + right.purchase_cost,
        storage_cost=left.storage_cost + right.storage_cost,
        return_handling_cost=left.return_handling_cost + right.return_handling_cost,
        retrieval_handling_cost=(left.retrieval_handling_cost + right.retrieval_handling_cost),
        scrap_proceeds=left.scrap_proceeds + right.scrap_proceeds,
        terminal_scrap_credit=(left.terminal_scrap_credit + right.terminal_scrap_credit),
    )


def _storage_cost(
    inventory: tuple[InventoryItem, ...],
    *,
    interval_start,  # type: ignore[no-untyped-def]
    interval_end,  # type: ignore[no-untyped-def]
    rate: float,
) -> float:
    elapsed_hours = (interval_end - interval_start).total_seconds() / 3600.0
    if elapsed_hours < 0:
        raise ValueError("replay storage interval cannot run backward")
    stored_area = sum(item.remnant.geometry.area for item in inventory)
    return rounded_cost(stored_area * elapsed_hours * rate)


def _require_unique_inventory(inventory: tuple[InventoryItem, ...]) -> None:
    remnant_ids = tuple(item.remnant.remnant_id for item in inventory)
    if len(remnant_ids) != len(set(remnant_ids)):
        raise ValueError("replay inventory contains duplicate remnant IDs")


def _transient_sheet_stock(order: ReplayOrder, sheet: StandardSheetSpec) -> RemnantStock:
    geometry = canonical_polygon_record(box(0.0, 0.0, sheet.length, sheet.height))
    stock_id = f"{sheet.stock_code}:{order.order_id}"
    lineage = RemnantLineage.root(
        root_stock_id=stock_id,
        source_candidate_id=stock_id,
        source_component_sha256=geometry.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, sheet.material),
        geometry=geometry,
        material=sheet.material,
        root_sheet_area=float(sheet.length * sheet.height),
        root_sheet_short_side=float(min(sheet.length, sheet.height)),
        lineage=lineage,
    )


def select_action(
    current_order: ReplayOrder,
    inventory: tuple[InventoryItem, ...],
    sheet: StandardSheetSpec,
    fit_config: RemnantFitConfig,
    search_config: FitSearchConfig,
) -> SelectedAction:
    """Select the first sorted exact remnant witness, otherwise one standard sheet."""

    _require_unique_inventory(inventory)
    compatible = sorted(
        (
            item
            for item in inventory
            if material_key(item.remnant.material) == material_key(current_order.material)
        ),
        key=lambda item: item.remnant.remnant_id,
    )
    for item in compatible:
        search_result = search_fit_witness(
            item.remnant,
            current_order.part,
            part_material=current_order.material,
            fit_config=fit_config,
            search_config=search_config,
        )
        if search_result.status is FitSearchStatus.FIT:
            return SelectedAction(
                kind=ReplayActionKind.CONSUME_REMNANT,
                stock=item.remnant,
                search_result=search_result,
            )

    transient_sheet = _transient_sheet_stock(current_order, sheet)
    sheet_search = search_fit_witness(
        transient_sheet,
        current_order.part,
        part_material=current_order.material,
        fit_config=fit_config,
        search_config=search_config,
    )
    if sheet_search.status is not FitSearchStatus.FIT:
        raise ValueError("part has no witness within registered standard sheet search")
    return SelectedAction(
        kind=ReplayActionKind.OPEN_STANDARD_SHEET,
        stock=transient_sheet,
        search_result=sheet_search,
    )


def _reroot_sheet_children(
    children: tuple[RemnantStock, ...],
    *,
    selected_stock_id: str,
) -> tuple[RemnantStock, ...]:
    rerooted = []
    for child in children:
        lineage = RemnantLineage.root(
            root_stock_id=selected_stock_id,
            source_candidate_id=selected_stock_id,
            source_component_sha256=child.geometry.polygon_sha256,
        )
        rerooted.append(
            RemnantStock(
                remnant_id=derive_remnant_id(lineage, child.geometry, child.material),
                geometry=child.geometry,
                material=child.material,
                root_sheet_area=child.root_sheet_area,
                root_sheet_short_side=child.root_sheet_short_side,
                lineage=lineage,
            )
        )
    return tuple(sorted(rerooted, key=lambda child: child.remnant_id))


def execute_action(
    selected: SelectedAction,
    current_order: ReplayOrder,
    inventory: tuple[InventoryItem, ...],
    sheet: StandardSheetSpec,
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
) -> ExecutedAction:
    """Execute one action atomically and return complete exact replacement state."""

    _require_unique_inventory(inventory)
    placement: FitPlacement | None = selected.search_result.placement
    if placement is None:
        raise ValueError("selected replay action has no exact placement witness")

    inventory_by_id = {item.remnant.remnant_id: item for item in inventory}
    if selected.kind is ReplayActionKind.CONSUME_REMNANT:
        if selected.stock.remnant_id not in inventory_by_id:
            raise ValueError("selected remnant is missing from replay inventory")
        base_inventory = tuple(
            item for item in inventory if item.remnant.remnant_id != selected.stock.remnant_id
        )
    else:
        expected_sheet = _transient_sheet_stock(current_order, sheet)
        if selected.stock != expected_sheet:
            raise ValueError("selected transient standard sheet does not match the order")
        base_inventory = inventory

    consumption = consume_remnant(
        selected.stock,
        current_order.part,
        placement,
        part_material=current_order.material,
        rules=rules,
        config=fit_config,
    )
    if consumption.result.accounting is None:
        raise ValueError("exact replay consumption produced no accounting evidence")
    returned = consumption.children
    if selected.kind is ReplayActionKind.OPEN_STANDARD_SHEET:
        returned = _reroot_sheet_children(
            returned,
            selected_stock_id=selected.stock.remnant_id,
        )

    additions = tuple(
        InventoryItem(remnant=remnant, entered_at=current_order.released_at) for remnant in returned
    )
    inventory_after = tuple(
        sorted(
            base_inventory + additions,
            key=lambda item: item.remnant.remnant_id,
        )
    )
    evidence = ReplayActionEvidence(
        kind=selected.kind,
        order_id=current_order.order_id,
        selected_stock_id=selected.stock.remnant_id,
        selected_remnant_id=selected.selected_remnant_id,
        placement=placement,
        search_result=selected.search_result,
        placed_polygon=canonical_polygon_record(transform_part(current_order.part, placement)),
        accounting=consumption.result.accounting,
        returned_remnants=returned,
    )
    return ExecutedAction(evidence=evidence, inventory_after=inventory_after)


def _build_event(
    *,
    sequence: int,
    occurred_at,  # type: ignore[no-untyped-def]
    storage_interval_start,  # type: ignore[no-untyped-def]
    order: ReplayOrder,
    inventory_before: tuple[InventoryItem, ...],
    execution: ExecutedAction,
    delta: ReplayCostLedger,
    cumulative: ReplayCostLedger,
) -> ReplayEventRecord:
    payload = {
        "sequence": sequence,
        "occurred_at": occurred_at,
        "storage_interval_start": storage_interval_start,
        "storage_interval_end": occurred_at,
        "order_id": order.order_id,
        "inventory_before": inventory_before,
        "action": execution.evidence,
        "inventory_after": execution.inventory_after,
        "delta_costs": delta,
        "cumulative_costs": cumulative,
    }
    provisional = ReplayEventRecord.model_construct(
        event_id="yfre-" + "0" * 24,
        **payload,
    )
    digest = semantic_sha256(provisional, excluded_fields={"event_id"})
    return ReplayEventRecord(event_id=f"yfre-{digest[:24]}", **payload)


def _build_result(
    replay_input: ReplayInput,
    events: tuple[ReplayEventRecord, ...],
    terminal: ReplayTerminalRecord,
    summary: ReplaySummary,
) -> ReplayResult:
    payload = {
        "schema_version": "yieldforge.deterministic-replay-result.v1",
        "input_id": replay_input.input_id,
        "input_sha256": replay_input.content_sha256,
        "m0_contract_id": replay_input.m0_contract_id,
        "m0_contract_sha256": replay_input.m0_contract_sha256,
        "m4_result_id": replay_input.m4_result_id,
        "m4_result_sha256": replay_input.m4_result_sha256,
        "engine": replay_input.engine.model_dump(mode="json"),
        "policy": replay_input.policy.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in events],
        "terminal": terminal.model_dump(mode="json"),
        "summary": summary.model_dump(mode="json"),
        "claim_ceiling": replay_input.claim_ceiling,
    }
    digest = semantic_sha256(payload)
    return ReplayResult(
        result_id=f"yfrpr-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        input_id=replay_input.input_id,
        input_sha256=replay_input.content_sha256,
        m0_contract_id=replay_input.m0_contract_id,
        m0_contract_sha256=replay_input.m0_contract_sha256,
        m4_result_id=replay_input.m4_result_id,
        m4_result_sha256=replay_input.m4_result_sha256,
        engine=replay_input.engine,
        policy=replay_input.policy,
        events=events,
        terminal=terminal,
        summary=summary,
    )


def run_replay(replay_input: ReplayInput, rules: ResidualRuleSet) -> ReplayResult:
    """Run the complete M5 half-open chronological replay without external mutation."""

    current_time = replay_input.orders[0].released_at
    inventory: tuple[InventoryItem, ...] = ()
    cumulative = ReplayCostLedger.zero()
    events: list[ReplayEventRecord] = []

    for order in replay_input.orders:
        inventory_before = inventory
        storage = _storage_cost(
            inventory_before,
            interval_start=current_time,
            interval_end=order.released_at,
            rate=replay_input.rates.storage_cost_per_area_hour,
        )
        selected = select_action(
            order,
            inventory_before,
            replay_input.standard_sheet,
            replay_input.fit_config,
            replay_input.search_config,
        )
        execution = execute_action(
            selected,
            order,
            inventory_before,
            replay_input.standard_sheet,
            rules,
            replay_input.fit_config,
        )
        accounting = execution.evidence.accounting
        delta = _ledger(
            purchase_cost=(
                replay_input.standard_sheet.length
                * replay_input.standard_sheet.height
                * replay_input.rates.purchase_cost_per_area
                if selected.kind is ReplayActionKind.OPEN_STANDARD_SHEET
                else 0.0
            ),
            storage_cost=storage,
            return_handling_cost=(
                len(execution.evidence.returned_remnants)
                * replay_input.rates.return_handling_cost_per_remnant
            ),
            retrieval_handling_cost=(
                replay_input.rates.retrieval_handling_cost_per_remnant
                if selected.kind is ReplayActionKind.CONSUME_REMNANT
                else 0.0
            ),
            scrap_proceeds=(accounting.scrap_area * replay_input.rates.scrap_credit_per_area),
        )
        next_cumulative = _add_ledgers(cumulative, delta)
        event = _build_event(
            sequence=order.sequence,
            occurred_at=order.released_at,
            storage_interval_start=current_time,
            order=order,
            inventory_before=inventory_before,
            execution=execution,
            delta=delta,
            cumulative=next_cumulative,
        )
        events.append(event)
        inventory = execution.inventory_after
        cumulative = next_cumulative
        current_time = order.released_at

    terminal_storage = _storage_cost(
        inventory,
        interval_start=current_time,
        interval_end=replay_input.horizon_end,
        rate=replay_input.rates.storage_cost_per_area_hour,
    )
    terminal_credit = rounded_cost(
        sum(item.remnant.geometry.area for item in inventory)
        * replay_input.rates.scrap_credit_per_area
    )
    terminal_delta = _ledger(
        storage_cost=terminal_storage,
        terminal_scrap_credit=terminal_credit,
    )
    final_cumulative = _add_ledgers(cumulative, terminal_delta)
    terminal = ReplayTerminalRecord(
        horizon_end=replay_input.horizon_end,
        storage_interval_start=current_time,
        inventory_before_liquidation=inventory,
        liquidated_remnant_ids=tuple(item.remnant.remnant_id for item in inventory),
        delta_costs=terminal_delta,
        cumulative_costs=final_cumulative,
    )
    event_tuple = tuple(events)
    summary = ReplaySummary(
        order_count=len(replay_input.orders),
        fulfilled_order_count=len(event_tuple),
        full_sheet_opening_count=sum(
            event.action.kind is ReplayActionKind.OPEN_STANDARD_SHEET for event in event_tuple
        ),
        remnant_retrieval_count=sum(
            event.action.kind is ReplayActionKind.CONSUME_REMNANT for event in event_tuple
        ),
        returned_remnant_count=sum(len(event.action.returned_remnants) for event in event_tuple),
        terminal_remnant_count=len(inventory),
        final_net_cost=final_cumulative.net_cost,
        technical_decision="pass",
    )
    return _build_result(replay_input, event_tuple, terminal, summary)
