"""Pure exact-geometry actions and state transitions for deterministic replay."""

from __future__ import annotations

from dataclasses import dataclass

from shapely import box

from yieldforge.replay.contracts import (
    InventoryItem,
    ReplayActionEvidence,
    ReplayActionKind,
    ReplayOrder,
    StandardSheetSpec,
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
