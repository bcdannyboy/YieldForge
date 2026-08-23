"""Strict persisted contracts for deterministic chronological replay."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from yieldforge.domain import Part
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.reuse.contracts import (
    CanonicalPolygon,
    FitPlacement,
    FitSearchConfig,
    FitSearchResult,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantStock,
    ReuseAccounting,
)

M0_EVENT_STAGE_ORDER = (
    "accrue_storage_to_t",
    "reveal_orders_released_at_t",
    "form_compatible_released_batch",
    "select_action",
    "execute_and_fulfill_atomically",
    "record_purchase_handling_and_scrap",
    "return_eligible_remnants_at_t",
)


class ReplayContractModel(BaseModel):
    """Immutable finite strict base for replay evidence."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def rounded_cost(value: float, decimal_places: int = 6) -> float:
    """Round one finite cost accrual to the registered decimal precision."""

    if not math.isfinite(value):
        raise ValueError("cost accrual must be finite")
    quantum = Decimal(1).scaleb(-decimal_places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


class ReplayEngineIdentity(ReplayContractModel):
    """Runtime component identity bound into every replay input."""

    name: Literal["yieldforge.deterministic-replay"] = "yieldforge.deterministic-replay"
    version: Literal["1.0.0"] = "1.0.0"
    shapely_version: StrictStr = Field(min_length=1)


class ReplayPolicyIdentity(ReplayContractModel):
    """Bounded as-of-time policy identity for the M5 mechanism proof."""

    name: Literal["first_fit_remnant_then_standard_sheet"] = "first_fit_remnant_then_standard_sheet"
    version: Literal["1.0.0"] = "1.0.0"
    seed: Literal[0] = 0
    information_set: Literal["released_order_and_current_inventory_only"] = (
        "released_order_and_current_inventory_only"
    )


class ReplayRateManifest(ReplayContractModel):
    """Generated numeric economic rates required by frozen M0 accounting."""

    schema_version: Literal["yieldforge.replay-rates.v1"] = "yieldforge.replay-rates.v1"
    cost_unit: Literal["generated_cost_unit"] = "generated_cost_unit"
    area_unit: Literal["generated_square_unit"] = "generated_square_unit"
    time_unit: Literal["hour"] = "hour"
    rounding_mode: Literal["half_up"] = "half_up"
    rounding_decimal_places: Literal[6] = 6
    provenance: Literal["generated"] = "generated"
    purchase_cost_per_area: StrictFloat = Field(ge=0)
    storage_cost_per_area_hour: StrictFloat = Field(ge=0)
    return_handling_cost_per_remnant: StrictFloat = Field(ge=0)
    retrieval_handling_cost_per_remnant: StrictFloat = Field(ge=0)
    scrap_credit_per_area: StrictFloat = Field(ge=0)


class StandardSheetSpec(ReplayContractModel):
    """One generated standard stock definition available to the M5 policy."""

    stock_code: StrictStr = Field(min_length=1)
    length: StrictFloat = Field(gt=0)
    height: StrictFloat = Field(gt=0)
    material: MaterialIdentity
    provenance: Literal["generated"] = "generated"

    @model_validator(mode="after")
    def require_assumed_material(self) -> Self:
        if self.material.provenance is not MaterialProvenance.ASSUMED:
            raise ValueError("standard-sheet material must be explicitly assumed")
        return self


class ReplayOrder(ReplayContractModel):
    """One generated single-part order released at one M5 timestamp."""

    sequence: StrictInt = Field(ge=0)
    order_id: StrictStr = Field(min_length=1)
    released_at: datetime
    part: Part
    material: MaterialIdentity
    chronology_provenance: Literal["generated"] = "generated"
    geometry_provenance: Literal["generated"] = "generated"
    material_provenance: Literal["assumed"] = "assumed"

    @field_validator("released_at")
    @classmethod
    def canonicalize_release(cls, value: datetime) -> datetime:
        return _utc(value, "order release")

    @model_validator(mode="after")
    def require_bounded_single_part(self) -> Self:
        if self.part.demand != 1:
            raise ValueError("M5 replay orders require exactly one explicit part")
        if self.material.provenance is not MaterialProvenance.ASSUMED:
            raise ValueError("replay order material must be explicitly assumed")
        return self


class ReplayInput(ReplayContractModel):
    """Content-addressed complete input to one bounded M5 replay."""

    schema_version: Literal["yieldforge.deterministic-replay-input.v1"] = (
        "yieldforge.deterministic-replay-input.v1"
    )
    input_id: StrictStr = Field(pattern=r"^yfrpi-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m4_input_id: StrictStr = Field(pattern=r"^yfri-[0-9a-f]{24}$")
    m4_input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m4_result_id: StrictStr = Field(pattern=r"^yfrr-[0-9a-f]{24}$")
    m4_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    engine: ReplayEngineIdentity
    policy: ReplayPolicyIdentity
    fit_config: RemnantFitConfig
    search_config: FitSearchConfig
    rates: ReplayRateManifest
    standard_sheet: StandardSheetSpec
    event_stage_order: tuple[StrictStr, ...] = M0_EVENT_STAGE_ORDER
    orders: tuple[ReplayOrder, ...] = Field(min_length=1)
    horizon_end: datetime
    claim_ceiling: Literal[
        "deterministic_replay_mechanics_only_not_benchmark_policy_savings_physical_or_commercial_"
        "evidence"
    ] = (
        "deterministic_replay_mechanics_only_not_benchmark_policy_savings_physical_or_commercial_"
        "evidence"
    )

    @field_validator("horizon_end")
    @classmethod
    def canonicalize_horizon(cls, value: datetime) -> datetime:
        return _utc(value, "horizon end")

    @model_validator(mode="after")
    def require_complete_identity_and_timeline(self) -> Self:
        if self.event_stage_order != M0_EVENT_STAGE_ORDER:
            raise ValueError("event stages do not match frozen M0 order")
        sequences = tuple(order.sequence for order in self.orders)
        if sequences != tuple(range(len(self.orders))):
            raise ValueError("order sequences must be contiguous from zero")
        order_ids = tuple(order.order_id for order in self.orders)
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("replay order IDs must be unique")
        releases = tuple(order.released_at for order in self.orders)
        if any(right <= left for left, right in zip(releases, releases[1:], strict=False)):
            raise ValueError("order releases must be strictly increasing")
        if releases[-1] >= self.horizon_end:
            raise ValueError("replay horizon must follow every order release")
        if any(order.material != self.standard_sheet.material for order in self.orders):
            raise ValueError("order material must match the standard-sheet material")
        if self.fit_config.clearance_distance != 0.0:
            raise ValueError("primary M5 replay must use zero clearance")
        digest = semantic_sha256(self, excluded_fields={"input_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("replay input content SHA-256 does not match semantic content")
        if self.input_id != f"yfrpi-{digest[:24]}":
            raise ValueError("replay input ID does not match semantic content")
        return self


def build_replay_input(
    *,
    m0_contract_id: str,
    m0_contract_sha256: str,
    m4_input_id: str,
    m4_input_sha256: str,
    m4_result_id: str,
    m4_result_sha256: str,
    engine: ReplayEngineIdentity,
    policy: ReplayPolicyIdentity,
    fit_config: RemnantFitConfig,
    search_config: FitSearchConfig,
    rates: ReplayRateManifest,
    standard_sheet: StandardSheetSpec,
    order_specs: tuple[tuple[str, datetime, Part, MaterialIdentity], ...],
    horizon_end: datetime,
) -> ReplayInput:
    """Build one content-addressed replay input from complete ordered semantics."""

    engine = ReplayEngineIdentity.model_validate(engine)
    policy = ReplayPolicyIdentity.model_validate(policy)
    fit_config = RemnantFitConfig.model_validate(fit_config)
    search_config = FitSearchConfig.model_validate(search_config)
    rates = ReplayRateManifest.model_validate(rates)
    standard_sheet = StandardSheetSpec.model_validate(standard_sheet)
    orders = tuple(
        ReplayOrder(
            sequence=sequence,
            order_id=order_id,
            released_at=released_at,
            part=part,
            material=material,
        )
        for sequence, (order_id, released_at, part, material) in enumerate(order_specs)
    )
    semantic_payload = {
        "schema_version": "yieldforge.deterministic-replay-input.v1",
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "m4_input_id": m4_input_id,
        "m4_input_sha256": m4_input_sha256,
        "m4_result_id": m4_result_id,
        "m4_result_sha256": m4_result_sha256,
        "engine": engine.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "fit_config": fit_config.model_dump(mode="json"),
        "search_config": search_config.model_dump(mode="json"),
        "rates": rates.model_dump(mode="json"),
        "standard_sheet": standard_sheet.model_dump(mode="json"),
        "event_stage_order": M0_EVENT_STAGE_ORDER,
        "orders": [order.model_dump(mode="json") for order in orders],
        "horizon_end": _utc(horizon_end, "horizon end").isoformat().replace("+00:00", "Z"),
        "claim_ceiling": (
            "deterministic_replay_mechanics_only_not_benchmark_policy_savings_physical_or_"
            "commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic_payload)
    return ReplayInput(
        input_id=f"yfrpi-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m0_contract_id=m0_contract_id,
        m0_contract_sha256=m0_contract_sha256,
        m4_input_id=m4_input_id,
        m4_input_sha256=m4_input_sha256,
        m4_result_id=m4_result_id,
        m4_result_sha256=m4_result_sha256,
        engine=engine,
        policy=policy,
        fit_config=fit_config,
        search_config=search_config,
        rates=rates,
        standard_sheet=standard_sheet,
        orders=orders,
        horizon_end=_utc(horizon_end, "horizon end"),
    )


class ReplayCostLedger(ReplayContractModel):
    """Complete M0 net-cost terms at one replay boundary."""

    purchase_cost: StrictFloat = Field(ge=0)
    storage_cost: StrictFloat = Field(ge=0)
    return_handling_cost: StrictFloat = Field(ge=0)
    retrieval_handling_cost: StrictFloat = Field(ge=0)
    scrap_proceeds: StrictFloat = Field(ge=0)
    terminal_scrap_credit: StrictFloat = Field(ge=0)
    net_cost: StrictFloat

    @model_validator(mode="after")
    def require_reconciled_net_cost(self) -> Self:
        expected = rounded_cost(
            self.purchase_cost
            + self.storage_cost
            + self.return_handling_cost
            + self.retrieval_handling_cost
            - self.scrap_proceeds
            - self.terminal_scrap_credit
        )
        if self.net_cost != expected:
            raise ValueError("replay net cost does not reconcile")
        return self

    @classmethod
    def zero(cls) -> ReplayCostLedger:
        return cls(
            purchase_cost=0.0,
            storage_cost=0.0,
            return_handling_cost=0.0,
            retrieval_handling_cost=0.0,
            scrap_proceeds=0.0,
            terminal_scrap_credit=0.0,
            net_cost=0.0,
        )


class InventoryItem(ReplayContractModel):
    """One exact retained remnant and the time it most recently entered inventory."""

    remnant: RemnantStock
    entered_at: datetime

    @field_validator("entered_at")
    @classmethod
    def canonicalize_entry(cls, value: datetime) -> datetime:
        return _utc(value, "inventory entry")


class ReplayActionKind(StrEnum):
    OPEN_STANDARD_SHEET = "open_standard_sheet"
    CONSUME_REMNANT = "consume_remnant"


class ReplayActionEvidence(ReplayContractModel):
    """Exact selected stock action and resulting geometry evidence."""

    kind: ReplayActionKind
    order_id: StrictStr = Field(min_length=1)
    selected_stock_id: StrictStr = Field(min_length=1)
    selected_remnant_id: StrictStr | None = Field(default=None, pattern=r"^yfrm-[0-9a-f]{24}$")
    placement: FitPlacement
    search_result: FitSearchResult
    placed_polygon: CanonicalPolygon
    accounting: ReuseAccounting
    returned_remnants: tuple[RemnantStock, ...]

    @model_validator(mode="after")
    def require_kind_consistent_stock(self) -> Self:
        if self.kind is ReplayActionKind.CONSUME_REMNANT:
            if (
                self.selected_remnant_id is None
                or self.selected_stock_id != self.selected_remnant_id
            ):
                raise ValueError("remnant action requires one matching selected remnant")
        elif self.selected_remnant_id is not None:
            raise ValueError("standard-sheet action cannot select a remnant")
        returned_ids = tuple(item.remnant_id for item in self.returned_remnants)
        if returned_ids != tuple(sorted(set(returned_ids))):
            raise ValueError("returned remnants must use sorted unique IDs")
        return self


class ReplayEventRecord(ReplayContractModel):
    """One atomic chronological order transition."""

    sequence: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(pattern=r"^yfre-[0-9a-f]{24}$")
    occurred_at: datetime
    event_stage_order: tuple[StrictStr, ...] = M0_EVENT_STAGE_ORDER
    storage_interval_start: datetime
    storage_interval_end: datetime
    order_id: StrictStr = Field(min_length=1)
    inventory_before: tuple[InventoryItem, ...]
    action: ReplayActionEvidence
    inventory_after: tuple[InventoryItem, ...]
    delta_costs: ReplayCostLedger
    cumulative_costs: ReplayCostLedger

    @field_validator("occurred_at", "storage_interval_start", "storage_interval_end")
    @classmethod
    def canonicalize_event_time(cls, value: datetime) -> datetime:
        return _utc(value, "event timestamp")


class ReplayTerminalRecord(ReplayContractModel):
    """Storage accrual and scrap-only liquidation at the explicit horizon."""

    horizon_end: datetime
    storage_interval_start: datetime
    inventory_before_liquidation: tuple[InventoryItem, ...]
    liquidated_remnant_ids: tuple[StrictStr, ...]
    delta_costs: ReplayCostLedger
    cumulative_costs: ReplayCostLedger

    @field_validator("horizon_end", "storage_interval_start")
    @classmethod
    def canonicalize_terminal_time(cls, value: datetime) -> datetime:
        return _utc(value, "terminal timestamp")


class ReplaySummary(ReplayContractModel):
    """Recomputed M5 mechanism counts and final net cost."""

    order_count: StrictInt = Field(ge=1)
    fulfilled_order_count: StrictInt = Field(ge=0)
    full_sheet_opening_count: StrictInt = Field(ge=0)
    remnant_retrieval_count: StrictInt = Field(ge=0)
    returned_remnant_count: StrictInt = Field(ge=0)
    terminal_remnant_count: StrictInt = Field(ge=0)
    final_net_cost: StrictFloat
    technical_decision: Literal["pass", "open"]

    @model_validator(mode="after")
    def require_decision_matches_fulfillment(self) -> Self:
        expected = "pass" if self.fulfilled_order_count == self.order_count else "open"
        if self.technical_decision != expected:
            raise ValueError("technical decision does not match fulfilled order count")
        return self


class ReplayResult(ReplayContractModel):
    """Content-addressed complete deterministic replay result."""

    schema_version: Literal["yieldforge.deterministic-replay-result.v1"] = (
        "yieldforge.deterministic-replay-result.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfrpr-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_id: StrictStr = Field(pattern=r"^yfrpi-[0-9a-f]{24}$")
    input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m4_result_id: StrictStr = Field(pattern=r"^yfrr-[0-9a-f]{24}$")
    m4_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    engine: ReplayEngineIdentity
    policy: ReplayPolicyIdentity
    events: tuple[ReplayEventRecord, ...]
    terminal: ReplayTerminalRecord
    summary: ReplaySummary
    claim_ceiling: Literal[
        "deterministic_replay_mechanics_only_not_benchmark_policy_savings_physical_or_commercial_"
        "evidence"
    ] = (
        "deterministic_replay_mechanics_only_not_benchmark_policy_savings_physical_or_commercial_"
        "evidence"
    )

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        if len(self.events) != self.summary.fulfilled_order_count:
            raise ValueError("replay events do not match fulfilled order count")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("replay result content SHA-256 does not match semantic content")
        if self.result_id != f"yfrpr-{digest[:24]}":
            raise ValueError("replay result ID does not match semantic content")
        return self
