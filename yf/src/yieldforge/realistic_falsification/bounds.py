"""Certified Gate 1 relaxed-cost bounds and constructive feasible witnesses for M11."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from itertools import product
from pathlib import Path
from typing import Literal, NamedTuple, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator
from shapely import box, union_all
from shapely.affinity import translate

from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256
from yieldforge.experiments.residual_geometry import M3ResidualInputPack, load_m3_input_pack
from yieldforge.realistic_falsification.pack import (
    M11CorpusId,
    M11PackBundle,
    M11Payload,
    M11Stream,
    load_m11_pack_bundle,
    visible_event_positions,
)
from yieldforge.realistic_falsification.sources import (
    LOCoCatalog,
    M11SourceManifest,
    load_loco_catalog,
    load_m11_source_manifest,
)
from yieldforge.residuals.geometry import placed_part_polygons
from yieldforge.reuse.contracts import polygon_from_record

_COST_QUANTUM = Decimal("0.000001")
_OPTIMISTIC_VIRGIN_COST = 100.0
_ZERO_RATE_FIELDS = (
    "scrap_and_terminal_credit",
    "return_handling",
    "retrieval_handling",
    "storage_per_reference_area_30_days",
    "process_loss_fraction",
)

GATE1_RELAXATION_ASSUMPTIONS = (
    "empty_initial_inventory",
    "fractional_virgin_stock",
    "geometry_relaxed",
    "stock_indivisibility_relaxed",
    "chronology_relaxed",
    "zero_process_loss",
    "zero_storage",
    "zero_return_handling",
    "zero_retrieval_handling",
    "perfect_inventory_identification",
    "zero_scrap_and_terminal_credit",
    "material_groups_preserved_no_contract_fungibility_declaration",
)
GATE1_FAVORABLE_RELAXATIONS = (
    "fractional_virgin_stock",
    "geometry_relaxed",
    "stock_indivisibility_relaxed",
    "chronology_relaxed",
    "zero_process_loss",
    "zero_storage",
    "zero_return_handling",
    "zero_retrieval_handling",
    "perfect_inventory_identification",
)
GATE1_NON_LATTICE_ASSUMPTIONS = (
    "empty_initial_inventory",
    "zero_scrap_and_terminal_credit",
    "material_groups_preserved_no_contract_fungibility_declaration",
)
GATE1_LOWER_BOUND_PROOF_DIRECTION = (
    "fractional_source_observed_demand_area_times_optimistic_virgin_rate_is_no_greater_"
    "than_any_geometry_stock_chronology_or_friction_constrained_feasible_cost"
)
GATE1_FEASIBLE_ALGORITHM = "one_event_one_verified_registered_fresh_opening"
GATE1_ACTION_SET_CONTRACT = "all_and_only_pack_registered_per_event_fresh_opening_candidates"
GATE1_COMPUTE_CONTRACT = "validate_every_registered_candidate_then_apply_frozen_policy_rule"

Gate1BaselinePolicyId = Literal[
    "fresh-candidate-position-0",
    "fresh-candidate-position-1",
    "fresh-minimum-used-area",
]
Gate1OpeningSelectionRule = Literal[
    "registered_candidate_position_0",
    "registered_candidate_position_1",
    "registered_minimum_used_area_then_candidate_id_then_position",
    "exhaustive_tiny_case",
]

FractionInput = Fraction | Decimal | int | float | str


class Gate1EvidenceError(ValueError):
    """Gate 1 source, geometry, or accounting evidence failed closed."""


class Gate1BoundAuditError(Gate1EvidenceError):
    """A certified-bound direction check failed and cannot be economic evidence."""


class Gate1BaselinePolicySpec(FrozenExperimentModel):
    """One preregistered, nonanticipatory fresh-opening baseline variant."""

    schema_version: Literal["yieldforge.m11-gate1-baseline-policy.v1"] = (
        "yieldforge.m11-gate1-baseline-policy.v1"
    )
    policy_id: Gate1BaselinePolicyId
    selection_rule: Gate1OpeningSelectionRule
    supported_corpora: tuple[M11CorpusId, ...] = Field(min_length=1)
    algorithm: Literal["one_event_one_verified_registered_fresh_opening"] = GATE1_FEASIBLE_ALGORITHM
    action_set_contract: Literal[
        "all_and_only_pack_registered_per_event_fresh_opening_candidates"
    ] = GATE1_ACTION_SET_CONTRACT
    compute_contract: Literal[
        "validate_every_registered_candidate_then_apply_frozen_policy_rule"
    ] = GATE1_COMPUTE_CONTRACT
    nonanticipatory: Literal[True] = True
    purchase_accounting: Literal["full_verified_stock_boundary"] = "full_verified_stock_boundary"

    @model_validator(mode="after")
    def require_preregistered_policy(self) -> Self:
        expected = {
            "fresh-candidate-position-0": (
                "registered_candidate_position_0",
                ("lectra-m3-m4", "loco-2dics"),
            ),
            "fresh-candidate-position-1": (
                "registered_candidate_position_1",
                ("lectra-m3-m4",),
            ),
            "fresh-minimum-used-area": (
                "registered_minimum_used_area_then_candidate_id_then_position",
                ("lectra-m3-m4", "loco-2dics"),
            ),
        }[self.policy_id]
        if (self.selection_rule, self.supported_corpora) != expected:
            raise ValueError("Gate 1 baseline policy differs from the preregistered family")
        return self


GATE1_BASELINE_POLICY_REGISTRY = (
    Gate1BaselinePolicySpec(
        policy_id="fresh-candidate-position-0",
        selection_rule="registered_candidate_position_0",
        supported_corpora=("lectra-m3-m4", "loco-2dics"),
    ),
    Gate1BaselinePolicySpec(
        policy_id="fresh-candidate-position-1",
        selection_rule="registered_candidate_position_1",
        supported_corpora=("lectra-m3-m4",),
    ),
    Gate1BaselinePolicySpec(
        policy_id="fresh-minimum-used-area",
        selection_rule="registered_minimum_used_area_then_candidate_id_then_position",
        supported_corpora=("lectra-m3-m4", "loco-2dics"),
    ),
)


def _fraction(value: FractionInput, *, label: str, positive: bool = False) -> Fraction:
    if isinstance(value, bool):
        raise Gate1EvidenceError(f"{label} cannot be boolean")
    try:
        if isinstance(value, Fraction):
            result = value
        elif isinstance(value, Decimal):
            result = Fraction(value)
        elif isinstance(value, int):
            result = Fraction(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError
            result = Fraction(str(value))
        elif isinstance(value, str):
            result = Fraction(value)
        else:
            raise TypeError
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise Gate1EvidenceError(f"{label} is not a finite exact number") from error
    if result < 0 or (positive and result <= 0):
        qualifier = "positive" if positive else "nonnegative"
        raise Gate1EvidenceError(f"{label} must be {qualifier}")
    return result


def _fraction_token(value: FractionInput, *, label: str, positive: bool = False) -> str:
    return str(_fraction(value, label=label, positive=positive))


def _parse_fraction_token(value: str, *, label: str, positive: bool = False) -> Fraction:
    result = _fraction(value, label=label, positive=positive)
    if value != str(result):
        raise ValueError(f"{label} must use canonical rational syntax")
    return result


def _decimal(value: Fraction) -> Decimal:
    digits = len(str(abs(value.numerator))) + len(str(value.denominator)) + 32
    with localcontext() as context:
        context.prec = max(80, digits)
        return Decimal(value.numerator) / Decimal(value.denominator)


def round_down_cost(value: FractionInput) -> float:
    """Round one nonnegative certified lower-bound cost down to six decimals."""

    exact = _fraction(value, label="lower-bound cost")
    return float(_decimal(exact).quantize(_COST_QUANTUM, rounding=ROUND_DOWN))


def round_half_up_cost(value: FractionInput) -> float:
    """Round one nonnegative realized feasible cost half-up to six decimals."""

    exact = _fraction(value, label="realized cost")
    return float(_decimal(exact).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP))


def _identity(prefix: str, semantic: dict[str, object]) -> tuple[str, str]:
    digest = semantic_sha256(semantic)
    return f"{prefix}{digest[:24]}", f"sha256:{digest}"


class Gate1DemandRecord(FrozenExperimentModel):
    """One exact source-observed demand-area contribution to the relaxation."""

    schema_version: Literal["yieldforge.m11-gate1-demand.v1"] = "yieldforge.m11-gate1-demand.v1"
    demand_id: StrictStr = Field(pattern=r"^yfm11d-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    event_position: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(min_length=1)
    geometry_reference_id: StrictStr = Field(min_length=1)
    geometry_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_binding_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_kind: Literal["lectra", "loco_2dics", "tiny"]
    source_instance: StrictStr | None = None
    material_group: StrictStr = Field(min_length=1)
    reference_area_key: StrictStr = Field(min_length=1)
    unit_area_exact: StrictStr = Field(min_length=1)
    quantity: StrictInt = Field(gt=0)
    total_area_exact: StrictStr = Field(min_length=1)
    reference_area_exact: StrictStr = Field(min_length=1)
    geometry_provenance: Literal["source_observed"] = "source_observed"

    @model_validator(mode="after")
    def require_exact_area_and_identity(self) -> Self:
        unit = _parse_fraction_token(
            self.unit_area_exact,
            label="demand unit area",
            positive=True,
        )
        total = _parse_fraction_token(
            self.total_area_exact,
            label="demand total area",
            positive=True,
        )
        _parse_fraction_token(
            self.reference_area_exact,
            label="demand reference area",
            positive=True,
        )
        if total != unit * self.quantity:
            raise ValueError("demand total area does not reconcile with unit area and quantity")
        digest = semantic_sha256(self, excluded_fields={"demand_id", "content_sha256"})
        if self.demand_id != f"yfm11d-{digest[:24]}" or self.content_sha256 != (f"sha256:{digest}"):
            raise ValueError("Gate 1 demand identity does not match semantic content")
        return self


class Gate1MaterialSubtotal(FrozenExperimentModel):
    """One exact compatible-material subtotal before the final downward rounding."""

    material_group: StrictStr = Field(min_length=1)
    reference_area_key: StrictStr = Field(min_length=1)
    source_instances: tuple[StrictStr, ...]
    demand_record_ids: tuple[StrictStr, ...] = Field(min_length=1)
    geometry_reference_ids: tuple[StrictStr, ...] = Field(min_length=1)
    demand_area_exact: StrictStr = Field(min_length=1)
    reference_area_exact: StrictStr = Field(min_length=1)
    raw_cost_exact: StrictStr = Field(min_length=1)
    rounded_down_cost: StrictFloat = Field(ge=0)

    @model_validator(mode="after")
    def require_exact_subtotal(self) -> Self:
        if self.source_instances != tuple(sorted(set(self.source_instances))):
            raise ValueError("material source instances must be sorted and unique")
        if self.demand_record_ids != tuple(sorted(set(self.demand_record_ids))):
            raise ValueError("material demand IDs must be sorted and unique")
        if self.geometry_reference_ids != tuple(sorted(set(self.geometry_reference_ids))):
            raise ValueError("material geometry references must be sorted and unique")
        demand_area = _parse_fraction_token(
            self.demand_area_exact,
            label="material demand area",
            positive=True,
        )
        reference_area = _parse_fraction_token(
            self.reference_area_exact,
            label="material reference area",
            positive=True,
        )
        raw = _parse_fraction_token(
            self.raw_cost_exact,
            label="material raw cost",
            positive=True,
        )
        expected = demand_area * Fraction(100) / reference_area
        if raw != expected or self.rounded_down_cost != round_down_cost(expected):
            raise ValueError("material lower-bound subtotal does not reconcile")
        return self


def build_gate1_demand_record(
    *,
    event_position: int,
    event_id: str,
    geometry_reference_id: str,
    geometry_sha256: str,
    source_binding_sha256: str,
    source_kind: Literal["lectra", "loco_2dics", "tiny"],
    source_instance: str | None,
    material_group: str,
    reference_area_key: str,
    unit_area: FractionInput,
    quantity: int,
    reference_area: FractionInput,
) -> Gate1DemandRecord:
    """Build one content-addressed exact demand contribution."""

    exact_unit = _fraction(unit_area, label="demand unit area", positive=True)
    exact_reference = _fraction(reference_area, label="demand reference area", positive=True)
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-demand.v1",
        "event_position": event_position,
        "event_id": event_id,
        "geometry_reference_id": geometry_reference_id,
        "geometry_sha256": geometry_sha256,
        "source_binding_sha256": source_binding_sha256,
        "source_kind": source_kind,
        "source_instance": source_instance,
        "material_group": material_group,
        "reference_area_key": reference_area_key,
        "unit_area_exact": str(exact_unit),
        "quantity": quantity,
        "total_area_exact": str(exact_unit * quantity),
        "reference_area_exact": str(exact_reference),
        "geometry_provenance": "source_observed",
    }
    identifier, content = _identity("yfm11d-", semantic)
    return Gate1DemandRecord(
        demand_id=identifier,
        content_sha256=content,
        **semantic,
    )


def _demand_order(item: Gate1DemandRecord) -> tuple[object, ...]:
    return (
        item.event_position,
        item.event_id,
        item.material_group,
        item.geometry_reference_id,
        item.demand_id,
    )


def _event_ids(demands: tuple[Gate1DemandRecord, ...]) -> tuple[str, ...]:
    by_position: dict[int, str] = {}
    by_id: dict[str, int] = {}
    for demand in demands:
        existing_id = by_position.setdefault(demand.event_position, demand.event_id)
        existing_position = by_id.setdefault(demand.event_id, demand.event_position)
        if existing_id != demand.event_id or existing_position != demand.event_position:
            raise Gate1EvidenceError("demand event identity and position mapping is contradictory")
    positions = tuple(sorted(by_position))
    if positions and positions != tuple(range(positions[-1] + 1)):
        raise Gate1EvidenceError("demand event positions are not a complete prefix")
    return tuple(by_position[position] for position in positions)


def _material_subtotals(
    demands: tuple[Gate1DemandRecord, ...],
) -> tuple[Gate1MaterialSubtotal, ...]:
    grouped: dict[str, list[Gate1DemandRecord]] = defaultdict(list)
    for demand in demands:
        grouped[demand.material_group].append(demand)
    result: list[Gate1MaterialSubtotal] = []
    for material_group, records in sorted(grouped.items()):
        reference_keys = {item.reference_area_key for item in records}
        reference_areas = {item.reference_area_exact for item in records}
        if len(reference_keys) != 1 or len(reference_areas) != 1:
            raise Gate1EvidenceError(
                "one material group is bound to more than one reference area or stock scale"
            )
        reference_area = _parse_fraction_token(
            next(iter(reference_areas)),
            label="material reference area",
            positive=True,
        )
        demand_area = sum(
            (
                _parse_fraction_token(
                    item.total_area_exact,
                    label="demand total area",
                    positive=True,
                )
                for item in records
            ),
            start=Fraction(0),
        )
        raw = demand_area * Fraction(100) / reference_area
        result.append(
            Gate1MaterialSubtotal(
                material_group=material_group,
                reference_area_key=next(iter(reference_keys)),
                source_instances=tuple(
                    sorted({item.source_instance for item in records if item.source_instance})
                ),
                demand_record_ids=tuple(sorted(item.demand_id for item in records)),
                geometry_reference_ids=tuple(
                    sorted({item.geometry_reference_id for item in records})
                ),
                demand_area_exact=str(demand_area),
                reference_area_exact=str(reference_area),
                raw_cost_exact=str(raw),
                rounded_down_cost=round_down_cost(raw),
            )
        )
    return tuple(result)


class Gate1LowerBound(FrozenExperimentModel):
    """A content-addressed certified full-information relaxed-cost lower bound."""

    schema_version: Literal["yieldforge.m11-gate1-lower-bound.v1"] = (
        "yieldforge.m11-gate1-lower-bound.v1"
    )
    bound_id: StrictStr = Field(pattern=r"^yfm11lb-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(min_length=1)
    event_ids: tuple[StrictStr, ...]
    demand_records: tuple[Gate1DemandRecord, ...]
    material_subtotals: tuple[Gate1MaterialSubtotal, ...]
    virgin_cost_per_reference_area: Literal[100.0] = _OPTIMISTIC_VIRGIN_COST
    scrap_and_terminal_credit: Literal[0.0] = 0.0
    return_handling: Literal[0.0] = 0.0
    retrieval_handling: Literal[0.0] = 0.0
    storage_per_reference_area_30_days: Literal[0.0] = 0.0
    process_loss_fraction: Literal[0.0] = 0.0
    raw_cost_exact: StrictStr = Field(min_length=1)
    lower_bound_cost: StrictFloat = Field(ge=0)
    rounding_mode: Literal["down"] = "down"
    rounding_decimal_places: Literal[6] = 6
    relaxation_assumptions: tuple[StrictStr, ...] = GATE1_RELAXATION_ASSUMPTIONS
    proof_direction: Literal[
        "fractional_source_observed_demand_area_times_optimistic_virgin_rate_is_no_greater_"
        "than_any_geometry_stock_chronology_or_friction_constrained_feasible_cost"
    ] = GATE1_LOWER_BOUND_PROOF_DIRECTION

    @model_validator(mode="after")
    def require_recomputed_bound_and_identity(self) -> Self:
        if self.relaxation_assumptions != GATE1_RELAXATION_ASSUMPTIONS:
            raise ValueError("Gate 1 lower-bound relaxations differ from the registered set")
        if self.demand_records != tuple(sorted(self.demand_records, key=_demand_order)):
            raise ValueError(
                "Gate 1 demand records are not in canonical chronology-independent order"
            )
        if self.event_ids != _event_ids(self.demand_records):
            raise ValueError("Gate 1 lower-bound event census does not reconcile")
        expected_subtotals = _material_subtotals(self.demand_records)
        if self.material_subtotals != expected_subtotals:
            raise ValueError("Gate 1 material lower-bound subtotals do not reconcile")
        raw = sum(
            (
                _parse_fraction_token(
                    item.raw_cost_exact,
                    label="material raw cost",
                    positive=True,
                )
                for item in self.material_subtotals
            ),
            start=Fraction(0),
        )
        if self.raw_cost_exact != str(raw) or self.lower_bound_cost != round_down_cost(raw):
            raise ValueError("Gate 1 lower-bound cost does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"bound_id", "content_sha256"})
        if self.bound_id != f"yfm11lb-{digest[:24]}" or self.content_sha256 != (f"sha256:{digest}"):
            raise ValueError("Gate 1 lower-bound identity does not match semantic content")
        return self


def calculate_relaxed_lower_bound(
    *,
    stream_id: str,
    demands: tuple[Gate1DemandRecord, ...],
    virgin_cost_per_reference_area: float = _OPTIMISTIC_VIRGIN_COST,
    scrap_and_terminal_credit: float = 0.0,
    return_handling: float = 0.0,
    retrieval_handling: float = 0.0,
    storage_per_reference_area_30_days: float = 0.0,
    process_loss_fraction: float = 0.0,
) -> Gate1LowerBound:
    """Calculate the sole registered Gate 1 full-information relaxation."""

    if virgin_cost_per_reference_area != _OPTIMISTIC_VIRGIN_COST or any(
        value != 0.0
        for value in (
            scrap_and_terminal_credit,
            return_handling,
            retrieval_handling,
            storage_per_reference_area_30_days,
            process_loss_fraction,
        )
    ):
        raise Gate1EvidenceError(
            "the registered Gate 1 lower bound requires the fixed virgin rate and zero credit and "
            "friction"
        )
    canonical_demands = tuple(sorted(demands, key=_demand_order))
    if len({item.demand_id for item in canonical_demands}) != len(canonical_demands):
        raise Gate1EvidenceError("Gate 1 demand evidence repeats a content identity")
    event_ids = _event_ids(canonical_demands)
    subtotals = _material_subtotals(canonical_demands)
    raw = sum(
        (
            _parse_fraction_token(
                item.raw_cost_exact,
                label="material raw cost",
                positive=True,
            )
            for item in subtotals
        ),
        start=Fraction(0),
    )
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-lower-bound.v1",
        "stream_id": stream_id,
        "event_ids": list(event_ids),
        "demand_records": [item.model_dump(mode="json") for item in canonical_demands],
        "material_subtotals": [item.model_dump(mode="json") for item in subtotals],
        "virgin_cost_per_reference_area": _OPTIMISTIC_VIRGIN_COST,
        "scrap_and_terminal_credit": 0.0,
        "return_handling": 0.0,
        "retrieval_handling": 0.0,
        "storage_per_reference_area_30_days": 0.0,
        "process_loss_fraction": 0.0,
        "raw_cost_exact": str(raw),
        "lower_bound_cost": round_down_cost(raw),
        "rounding_mode": "down",
        "rounding_decimal_places": 6,
        "relaxation_assumptions": list(GATE1_RELAXATION_ASSUMPTIONS),
        "proof_direction": GATE1_LOWER_BOUND_PROOF_DIRECTION,
    }
    identifier, content = _identity("yfm11lb-", semantic)
    return Gate1LowerBound(
        bound_id=identifier,
        content_sha256=content,
        stream_id=stream_id,
        event_ids=event_ids,
        demand_records=canonical_demands,
        material_subtotals=subtotals,
        raw_cost_exact=str(raw),
        lower_bound_cost=round_down_cost(raw),
    )


class Gate1FeasibleOpening(FrozenExperimentModel):
    """One geometrically validated standard-stock opening and purchase accrual."""

    schema_version: Literal["yieldforge.m11-gate1-feasible-opening.v1"] = (
        "yieldforge.m11-gate1-feasible-opening.v1"
    )
    opening_id: StrictStr = Field(pattern=r"^yfm11op-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    event_position: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(min_length=1)
    payload_id: StrictStr = Field(min_length=1)
    material_group: StrictStr = Field(min_length=1)
    reference_area_key: StrictStr = Field(min_length=1)
    source_kind: Literal["lectra", "loco_2dics", "tiny"]
    candidate_options: tuple[tuple[StrictStr, StrictStr], ...] = Field(min_length=1)
    selected_candidate_id: StrictStr = Field(min_length=1)
    selection_rule: Gate1OpeningSelectionRule
    verification_kind: Literal[
        "lectra_m3_candidate_geometry",
        "loco_bbox_shelf_geometry",
        "exhaustive_tiny_case",
    ]
    geometry_witness_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    known_positions_at_release: tuple[StrictInt, ...]
    used_layout_width_exact: StrictStr = Field(min_length=1)
    purchased_stock_width_exact: StrictStr = Field(min_length=1)
    purchased_stock_height_exact: StrictStr = Field(min_length=1)
    stock_area_exact: StrictStr = Field(min_length=1)
    reference_area_exact: StrictStr = Field(min_length=1)
    raw_purchase_cost_exact: StrictStr = Field(min_length=1)
    purchase_cost: StrictFloat = Field(ge=0)
    rounding_mode: Literal["half_up"] = "half_up"
    rounding_decimal_places: Literal[6] = 6

    @model_validator(mode="after")
    def require_constructive_cost_and_identity(self) -> Self:
        candidate_ids = tuple(item[0] for item in self.candidate_options)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("feasible opening candidate options repeat an identity")
        if self.selected_candidate_id not in candidate_ids:
            raise ValueError("feasible opening selection is not a validated candidate option")
        if (
            self.known_positions_at_release != tuple(range(len(self.known_positions_at_release)))
            or self.event_position not in self.known_positions_at_release
        ):
            raise ValueError("feasible opening is not bound to a visible firm-schedule prefix")
        used_width = _parse_fraction_token(
            self.used_layout_width_exact,
            label="opening used layout width",
            positive=True,
        )
        purchased_width = _parse_fraction_token(
            self.purchased_stock_width_exact,
            label="opening purchased stock width",
            positive=True,
        )
        purchased_height = _parse_fraction_token(
            self.purchased_stock_height_exact,
            label="opening purchased stock height",
            positive=True,
        )
        stock_area = _parse_fraction_token(
            self.stock_area_exact,
            label="opening stock area",
            positive=True,
        )
        reference_area = _parse_fraction_token(
            self.reference_area_exact,
            label="opening reference area",
            positive=True,
        )
        raw = _parse_fraction_token(
            self.raw_purchase_cost_exact,
            label="opening raw purchase cost",
            positive=True,
        )
        if used_width > purchased_width or stock_area != purchased_width * purchased_height:
            raise ValueError(
                "feasible opening used and purchased stock dimensions do not reconcile"
            )
        expected = stock_area * Fraction(100) / reference_area
        if raw != expected or self.purchase_cost != round_half_up_cost(expected):
            raise ValueError("feasible opening purchase cost does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"opening_id", "content_sha256"})
        if self.opening_id != f"yfm11op-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 feasible-opening identity does not match semantic content")
        return self


def build_gate1_feasible_opening(
    *,
    event_position: int,
    event_id: str,
    payload_id: str,
    material_group: str,
    reference_area_key: str,
    source_kind: Literal["lectra", "loco_2dics", "tiny"],
    candidate_options: tuple[tuple[str, str], ...],
    selected_candidate_id: str,
    selection_rule: Gate1OpeningSelectionRule,
    verification_kind: Literal[
        "lectra_m3_candidate_geometry",
        "loco_bbox_shelf_geometry",
        "exhaustive_tiny_case",
    ],
    geometry_witness_sha256: str,
    known_positions_at_release: tuple[int, ...],
    stock_area: FractionInput,
    reference_area: FractionInput,
    used_layout_width: FractionInput | None = None,
    purchased_stock_width: FractionInput | None = None,
    purchased_stock_height: FractionInput | None = None,
) -> Gate1FeasibleOpening:
    """Build one constructive purchase witness after its geometry was validated."""

    exact_stock = _fraction(stock_area, label="opening stock area", positive=True)
    exact_reference = _fraction(reference_area, label="opening reference area", positive=True)
    exact_purchased_width = _fraction(
        exact_stock if purchased_stock_width is None else purchased_stock_width,
        label="opening purchased stock width",
        positive=True,
    )
    exact_purchased_height = _fraction(
        Fraction(1) if purchased_stock_height is None else purchased_stock_height,
        label="opening purchased stock height",
        positive=True,
    )
    exact_used_width = _fraction(
        exact_purchased_width if used_layout_width is None else used_layout_width,
        label="opening used layout width",
        positive=True,
    )
    if exact_stock != exact_purchased_width * exact_purchased_height:
        raise Gate1EvidenceError("opening purchase area must equal its persisted stock dimensions")
    if exact_used_width > exact_purchased_width:
        raise Gate1EvidenceError("opening used layout width exceeds purchased stock width")
    raw = exact_stock * Fraction(100) / exact_reference
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-feasible-opening.v1",
        "event_position": event_position,
        "event_id": event_id,
        "payload_id": payload_id,
        "material_group": material_group,
        "reference_area_key": reference_area_key,
        "source_kind": source_kind,
        "candidate_options": [list(item) for item in candidate_options],
        "selected_candidate_id": selected_candidate_id,
        "selection_rule": selection_rule,
        "verification_kind": verification_kind,
        "geometry_witness_sha256": geometry_witness_sha256,
        "known_positions_at_release": list(known_positions_at_release),
        "used_layout_width_exact": str(exact_used_width),
        "purchased_stock_width_exact": str(exact_purchased_width),
        "purchased_stock_height_exact": str(exact_purchased_height),
        "stock_area_exact": str(exact_stock),
        "reference_area_exact": str(exact_reference),
        "raw_purchase_cost_exact": str(raw),
        "purchase_cost": round_half_up_cost(raw),
        "rounding_mode": "half_up",
        "rounding_decimal_places": 6,
    }
    identifier, content = _identity("yfm11op-", semantic)
    return Gate1FeasibleOpening(
        opening_id=identifier,
        content_sha256=content,
        event_position=event_position,
        event_id=event_id,
        payload_id=payload_id,
        material_group=material_group,
        reference_area_key=reference_area_key,
        source_kind=source_kind,
        candidate_options=candidate_options,
        selected_candidate_id=selected_candidate_id,
        selection_rule=selection_rule,
        verification_kind=verification_kind,
        geometry_witness_sha256=geometry_witness_sha256,
        known_positions_at_release=known_positions_at_release,
        used_layout_width_exact=str(exact_used_width),
        purchased_stock_width_exact=str(exact_purchased_width),
        purchased_stock_height_exact=str(exact_purchased_height),
        stock_area_exact=str(exact_stock),
        reference_area_exact=str(exact_reference),
        raw_purchase_cost_exact=str(raw),
        purchase_cost=round_half_up_cost(raw),
    )


class Gate1FeasiblePolicyCost(FrozenExperimentModel):
    """A complete constructive feasible policy cost, explicitly never a lower bound."""

    schema_version: Literal["yieldforge.m11-gate1-feasible-policy.v1"] = (
        "yieldforge.m11-gate1-feasible-policy.v1"
    )
    witness_id: StrictStr = Field(pattern=r"^yfm11fp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(min_length=1)
    policy_kind: Literal["baseline_as_of", "known_only"]
    registered_policy_id: StrictStr = Field(min_length=1)
    calibration_selection_id: StrictStr | None = Field(
        default=None,
        pattern=r"^yfm11bs-[0-9a-f]{24}$",
    )
    evidence_stage: Literal[
        "calibration_selection",
        "confirmation_application",
        "tiny_audit",
    ]
    algorithm: Literal["one_event_one_verified_registered_fresh_opening"] = GATE1_FEASIBLE_ALGORITHM
    action_set_contract: Literal[
        "all_and_only_pack_registered_per_event_fresh_opening_candidates"
    ] = GATE1_ACTION_SET_CONTRACT
    compute_contract: Literal[
        "validate_every_registered_candidate_then_apply_frozen_policy_rule"
    ] = GATE1_COMPUTE_CONTRACT
    unknown_events_masked: StrictBool
    ignores_future: Literal[True] = True
    evidence_role: Literal["verified_constructive_feasible_cost_never_lower_bound"] = (
        "verified_constructive_feasible_cost_never_lower_bound"
    )
    openings: tuple[Gate1FeasibleOpening, ...] = Field(min_length=1)
    raw_purchase_cost_exact: StrictStr = Field(min_length=1)
    feasible_cost: StrictFloat = Field(gt=0)
    rounding_mode: Literal["per_event_half_up_then_ledger_half_up"] = (
        "per_event_half_up_then_ledger_half_up"
    )
    rounding_decimal_places: Literal[6] = 6

    @model_validator(mode="after")
    def require_complete_constructive_cost_and_identity(self) -> Self:
        if (self.evidence_stage == "confirmation_application") is (
            self.calibration_selection_id is None
        ):
            raise ValueError(
                "confirmation feasible policy must bind calibration selection evidence"
            )
        if self.evidence_stage == "tiny_audit" and self.registered_policy_id != "exhaustive-tiny":
            raise ValueError("tiny feasible policy must use the exhaustive tiny policy identity")
        if self.evidence_stage != "tiny_audit" and self.registered_policy_id not in {
            item.policy_id for item in GATE1_BASELINE_POLICY_REGISTRY
        }:
            raise ValueError("feasible policy is absent from the preregistered baseline registry")
        expected_mask = self.policy_kind == "known_only"
        if self.unknown_events_masked is not expected_mask:
            raise ValueError("feasible policy mask does not match its information set")
        positions = tuple(item.event_position for item in self.openings)
        if positions != tuple(range(len(self.openings))):
            raise ValueError("feasible policy openings are not a complete ordered stream")
        event_ids = tuple(item.event_id for item in self.openings)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("feasible policy repeats an event witness")
        raw = sum(
            (
                _parse_fraction_token(
                    item.raw_purchase_cost_exact,
                    label="opening raw purchase cost",
                    positive=True,
                )
                for item in self.openings
            ),
            start=Fraction(0),
        )
        realized = sum((Fraction(str(item.purchase_cost)) for item in self.openings), Fraction(0))
        if self.raw_purchase_cost_exact != str(raw) or self.feasible_cost != round_half_up_cost(
            realized
        ):
            raise ValueError("feasible policy purchase ledger does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"witness_id", "content_sha256"})
        if self.witness_id != f"yfm11fp-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 feasible-policy identity does not match semantic content")
        return self


def build_gate1_feasible_policy_cost(
    *,
    stream_id: str,
    policy_kind: Literal["baseline_as_of", "known_only"],
    openings: tuple[Gate1FeasibleOpening, ...],
    registered_policy_id: str = "exhaustive-tiny",
    calibration_selection_id: str | None = None,
    evidence_stage: Literal[
        "calibration_selection",
        "confirmation_application",
        "tiny_audit",
    ] = "tiny_audit",
) -> Gate1FeasiblePolicyCost:
    """Build a complete B or K witness from geometrically verified openings."""

    if not openings:
        raise Gate1EvidenceError("a feasible policy requires at least one constructive opening")
    raw = sum(
        (
            _parse_fraction_token(
                item.raw_purchase_cost_exact,
                label="opening raw purchase cost",
                positive=True,
            )
            for item in openings
        ),
        start=Fraction(0),
    )
    realized = sum((Fraction(str(item.purchase_cost)) for item in openings), start=Fraction(0))
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-feasible-policy.v1",
        "stream_id": stream_id,
        "policy_kind": policy_kind,
        "registered_policy_id": registered_policy_id,
        "calibration_selection_id": calibration_selection_id,
        "evidence_stage": evidence_stage,
        "algorithm": GATE1_FEASIBLE_ALGORITHM,
        "action_set_contract": GATE1_ACTION_SET_CONTRACT,
        "compute_contract": GATE1_COMPUTE_CONTRACT,
        "unknown_events_masked": policy_kind == "known_only",
        "ignores_future": True,
        "evidence_role": "verified_constructive_feasible_cost_never_lower_bound",
        "openings": [item.model_dump(mode="json") for item in openings],
        "raw_purchase_cost_exact": str(raw),
        "feasible_cost": round_half_up_cost(realized),
        "rounding_mode": "per_event_half_up_then_ledger_half_up",
        "rounding_decimal_places": 6,
    }
    identifier, content = _identity("yfm11fp-", semantic)
    return Gate1FeasiblePolicyCost(
        witness_id=identifier,
        content_sha256=content,
        stream_id=stream_id,
        policy_kind=policy_kind,
        registered_policy_id=registered_policy_id,
        calibration_selection_id=calibration_selection_id,
        evidence_stage=evidence_stage,
        unknown_events_masked=policy_kind == "known_only",
        openings=openings,
        raw_purchase_cost_exact=str(raw),
        feasible_cost=round_half_up_cost(realized),
    )


class Gate1CalibrationPolicyScore(FrozenExperimentModel):
    """One registered policy's verified feasible calibration-only score."""

    schema_version: Literal["yieldforge.m11-gate1-calibration-policy-score.v1"] = (
        "yieldforge.m11-gate1-calibration-policy-score.v1"
    )
    score_id: StrictStr = Field(pattern=r"^yfm11bsc-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: M11CorpusId
    policy_id: Gate1BaselinePolicyId
    calibration_stream_costs: tuple[tuple[StrictStr, StrictFloat], ...] = Field(
        min_length=8,
        max_length=8,
    )
    total_cost_exact: StrictStr = Field(min_length=1)
    total_cost: StrictFloat = Field(gt=0)
    verified_feasible: Literal[True] = True
    calibration_only: Literal[True] = True
    confirmation_inputs_used: Literal[False] = False

    @model_validator(mode="after")
    def require_complete_calibration_score(self) -> Self:
        if self.calibration_stream_costs != tuple(sorted(self.calibration_stream_costs)):
            raise ValueError("calibration stream scores must be canonically ordered")
        stream_ids = tuple(item[0] for item in self.calibration_stream_costs)
        if len(stream_ids) != len(set(stream_ids)):
            raise ValueError("calibration policy score repeats a stream")
        exact = sum(
            (Fraction(str(cost)) for _stream_id, cost in self.calibration_stream_costs),
            start=Fraction(0),
        )
        if self.total_cost_exact != str(exact) or self.total_cost != round_half_up_cost(exact):
            raise ValueError("calibration policy total does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"score_id", "content_sha256"})
        if self.score_id != f"yfm11bsc-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 calibration score identity does not match semantic content")
        return self


class Gate1BaselineSelectionEvidence(FrozenExperimentModel):
    """Calibration-frozen strongest policy within the preregistered feasible family."""

    schema_version: Literal["yieldforge.m11-gate1-baseline-selection.v1"] = (
        "yieldforge.m11-gate1-baseline-selection.v1"
    )
    selection_id: StrictStr = Field(pattern=r"^yfm11bs-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(pattern=r"^yfm11c-[0-9a-f]{24}$")
    contract_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    population_id: StrictStr = Field(pattern=r"^yfm11pop-[0-9a-f]{24}$")
    population_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: M11CorpusId
    registered_policy_ids: tuple[Gate1BaselinePolicyId, ...]
    eligible_policy_ids: tuple[Gate1BaselinePolicyId, ...]
    calibration_stream_ids: tuple[StrictStr, ...] = Field(min_length=8, max_length=8)
    policy_scores: tuple[Gate1CalibrationPolicyScore, ...] = Field(min_length=2)
    selection_rule: Literal["lowest_verified_calibration_cost_then_policy_id"] = (
        "lowest_verified_calibration_cost_then_policy_id"
    )
    selected_policy_id: Gate1BaselinePolicyId
    tied_lowest_policy_ids: tuple[Gate1BaselinePolicyId, ...] = Field(min_length=1)
    calibration_only: Literal[True] = True
    confirmation_inputs_used: Literal[False] = False
    strongest_scope: Literal[
        "strongest_within_registered_feasible_as_of_time_family_not_universal_optimum"
    ] = "strongest_within_registered_feasible_as_of_time_family_not_universal_optimum"

    @model_validator(mode="after")
    def require_calibration_only_selection(self) -> Self:
        registered = tuple(item.policy_id for item in GATE1_BASELINE_POLICY_REGISTRY)
        eligible = tuple(
            item.policy_id
            for item in GATE1_BASELINE_POLICY_REGISTRY
            if self.corpus_id in item.supported_corpora
        )
        if self.registered_policy_ids != registered or self.eligible_policy_ids != eligible:
            raise ValueError("baseline selection registry differs from the preregistered family")
        if self.calibration_stream_ids != tuple(sorted(set(self.calibration_stream_ids))):
            raise ValueError("baseline selection calibration stream census is not canonical")
        if self.policy_scores != tuple(sorted(self.policy_scores, key=lambda item: item.policy_id)):
            raise ValueError("baseline policy scores are not canonically ordered")
        if tuple(item.policy_id for item in self.policy_scores) != eligible or any(
            item.corpus_id != self.corpus_id
            or tuple(stream_id for stream_id, _cost in item.calibration_stream_costs)
            != self.calibration_stream_ids
            for item in self.policy_scores
        ):
            raise ValueError("baseline selection policy scores cross corpus or calibration census")
        best = min(item.total_cost for item in self.policy_scores)
        tied = tuple(item.policy_id for item in self.policy_scores if item.total_cost == best)
        if self.tied_lowest_policy_ids != tied or self.selected_policy_id != min(tied):
            raise ValueError("baseline selection is not the stable lowest calibration-cost policy")
        digest = semantic_sha256(self, excluded_fields={"selection_id", "content_sha256"})
        if self.selection_id != f"yfm11bs-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 baseline selection identity does not match semantic content")
        return self


def verify_weaker_feasible_comparator_ceiling(
    *,
    lower_bound_cost: FractionInput,
    strongest_registered_cost: FractionInput,
    weaker_feasible_cost: FractionInput,
) -> bool:
    """Prove a higher feasible outer comparator cannot shrink the opportunity ceiling."""

    lower = _fraction(lower_bound_cost, label="lower-bound comparator cost")
    strongest = _fraction(
        strongest_registered_cost,
        label="strongest registered comparator cost",
        positive=True,
    )
    weaker = _fraction(
        weaker_feasible_cost,
        label="weaker feasible comparator cost",
        positive=True,
    )
    if not lower <= strongest <= weaker:
        raise Gate1EvidenceError(
            "ceiling monotonicity requires ordered feasible costs L <= strongest <= weaker"
        )
    strongest_ceiling = (strongest - lower) / strongest
    weaker_ceiling = (weaker - lower) / weaker
    if weaker_ceiling < strongest_ceiling:
        raise Gate1BoundAuditError(
            "a weaker feasible comparator understated the opportunity ceiling"
        )
    return True


def _ceiling_percent(feasible: float, lower: float) -> float:
    exact_feasible = Fraction(str(feasible))
    raw = (exact_feasible - Fraction(str(lower))) * Fraction(100) / exact_feasible
    return round_half_up_cost(raw)


class Gate1StreamCell(FrozenExperimentModel):
    """One complete stream's certified L, constructive B/K, and ceiling metrics."""

    schema_version: Literal["yieldforge.m11-gate1-stream-cell.v1"] = (
        "yieldforge.m11-gate1-stream-cell.v1"
    )
    cell_id: StrictStr = Field(pattern=r"^yfm11g1-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(min_length=1)
    corpus_id: StrictStr = Field(min_length=1)
    lower_bound: Gate1LowerBound
    baseline: Gate1FeasiblePolicyCost
    known_only: Gate1FeasiblePolicyCost
    baseline_feasible_cost: StrictFloat = Field(gt=0)
    known_only_feasible_cost: StrictFloat = Field(gt=0)
    ceiling_savings_percent: StrictFloat = Field(ge=0)
    ceiling_unknown_contribution_points: StrictFloat = Field(ge=0)
    known_only_equals_baseline: Literal[True] = True
    proof_direction: Literal[
        "subtract_one_full_information_lower_bound_from_constructive_B_and_K"
    ] = "subtract_one_full_information_lower_bound_from_constructive_B_and_K"

    @model_validator(mode="after")
    def require_bound_direction_parity_and_identity(self) -> Self:
        if (
            self.lower_bound.stream_id != self.stream_id
            or self.baseline.stream_id != self.stream_id
            or self.known_only.stream_id != self.stream_id
        ):
            raise ValueError("Gate 1 cell evidence crosses streams")
        if (
            self.baseline.policy_kind != "baseline_as_of"
            or self.known_only.policy_kind != "known_only"
        ):
            raise ValueError("Gate 1 cell feasible evidence uses the wrong information set")
        if (
            self.baseline.algorithm != self.known_only.algorithm
            or self.baseline.action_set_contract != self.known_only.action_set_contract
            or self.baseline.compute_contract != self.known_only.compute_contract
            or self.baseline.registered_policy_id != self.known_only.registered_policy_id
            or self.baseline.calibration_selection_id != self.known_only.calibration_selection_id
            or self.baseline.evidence_stage != self.known_only.evidence_stage
            or self.baseline.openings != self.known_only.openings
        ):
            raise ValueError(
                "Gate 1 B and K do not share algorithm, action set, compute, and actions"
            )
        opening_event_ids = tuple(item.event_id for item in self.baseline.openings)
        if self.lower_bound.event_ids != opening_event_ids:
            raise ValueError("Gate 1 relaxed and feasible evidence event censuses differ")
        if (
            self.baseline_feasible_cost != self.baseline.feasible_cost
            or self.known_only_feasible_cost != self.known_only.feasible_cost
            or self.baseline_feasible_cost != self.known_only_feasible_cost
        ):
            raise ValueError(
                "Gate 1 feasible costs do not reconcile or future-blind parity differs"
            )
        lower = self.lower_bound.lower_bound_cost
        if lower > self.baseline_feasible_cost or lower > self.known_only_feasible_cost:
            raise ValueError("Gate 1 lower-bound direction exceeds a constructive feasible cost")
        expected_savings = _ceiling_percent(self.baseline_feasible_cost, lower)
        expected_unknown = _ceiling_percent(self.known_only_feasible_cost, lower)
        if (
            self.ceiling_savings_percent != expected_savings
            or self.ceiling_unknown_contribution_points != expected_unknown
        ):
            raise ValueError("Gate 1 ceiling metrics do not reconcile")
        digest = semantic_sha256(self, excluded_fields={"cell_id", "content_sha256"})
        if self.cell_id != f"yfm11g1-{digest[:24]}" or self.content_sha256 != (f"sha256:{digest}"):
            raise ValueError("Gate 1 stream-cell identity does not match semantic content")
        return self


def build_gate1_stream_cell_from_evidence(
    *,
    stream_id: str,
    corpus_id: str,
    lower_bound: Gate1LowerBound,
    baseline: Gate1FeasiblePolicyCost,
    known_only: Gate1FeasiblePolicyCost,
) -> Gate1StreamCell:
    """Combine independently verified L, B, and K or fail as invalid evidence."""

    if (
        lower_bound.stream_id != stream_id
        or baseline.stream_id != stream_id
        or known_only.stream_id != stream_id
    ):
        raise Gate1EvidenceError("Gate 1 L, B, and K must bind the same stream")
    if baseline.policy_kind != "baseline_as_of" or known_only.policy_kind != "known_only":
        raise Gate1EvidenceError("Gate 1 feasible witnesses use the wrong information sets")
    if (
        baseline.algorithm != known_only.algorithm
        or baseline.action_set_contract != known_only.action_set_contract
        or baseline.compute_contract != known_only.compute_contract
        or baseline.registered_policy_id != known_only.registered_policy_id
        or baseline.calibration_selection_id != known_only.calibration_selection_id
        or baseline.evidence_stage != known_only.evidence_stage
        or baseline.openings != known_only.openings
    ):
        raise Gate1EvidenceError(
            "Gate 1 B and K must use the same algorithm, action set, compute, and actions"
        )
    if lower_bound.event_ids != tuple(item.event_id for item in baseline.openings):
        raise Gate1EvidenceError("Gate 1 relaxed and feasible evidence event censuses differ")
    lower = lower_bound.lower_bound_cost
    if lower > baseline.feasible_cost or lower > known_only.feasible_cost:
        raise Gate1BoundAuditError("L_full exceeds a constructive B_feasible or K_feasible")
    savings = _ceiling_percent(baseline.feasible_cost, lower)
    unknown = _ceiling_percent(known_only.feasible_cost, lower)
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-stream-cell.v1",
        "stream_id": stream_id,
        "corpus_id": corpus_id,
        "lower_bound": lower_bound.model_dump(mode="json"),
        "baseline": baseline.model_dump(mode="json"),
        "known_only": known_only.model_dump(mode="json"),
        "baseline_feasible_cost": baseline.feasible_cost,
        "known_only_feasible_cost": known_only.feasible_cost,
        "ceiling_savings_percent": savings,
        "ceiling_unknown_contribution_points": unknown,
        "known_only_equals_baseline": True,
        "proof_direction": ("subtract_one_full_information_lower_bound_from_constructive_B_and_K"),
    }
    identifier, content = _identity("yfm11g1-", semantic)
    return Gate1StreamCell(
        cell_id=identifier,
        content_sha256=content,
        stream_id=stream_id,
        corpus_id=corpus_id,
        lower_bound=lower_bound,
        baseline=baseline,
        known_only=known_only,
        baseline_feasible_cost=baseline.feasible_cost,
        known_only_feasible_cost=known_only.feasible_cost,
        ceiling_savings_percent=savings,
        ceiling_unknown_contribution_points=unknown,
    )


class Gate1TinyScenario(FrozenExperimentModel):
    """One hidden future scenario in the finite Gate 1 proof kernel."""

    scenario_id: StrictStr = Field(min_length=1)
    probability_exact: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def require_exact_probability(self) -> Self:
        _parse_fraction_token(
            self.probability_exact,
            label="tiny scenario probability",
            positive=True,
        )
        return self


class Gate1TinyAction(FrozenExperimentModel):
    """One finite action with exact scenario costs and relaxation penalties."""

    action_id: StrictStr = Field(min_length=1)
    known_score_exact: StrictStr = Field(min_length=1)
    scenario_relaxed_costs: tuple[tuple[StrictStr, StrictStr], ...] = Field(min_length=1)
    absent_relaxation_penalties: tuple[tuple[StrictStr, StrictStr], ...] = Field(
        min_length=len(GATE1_FAVORABLE_RELAXATIONS),
        max_length=len(GATE1_FAVORABLE_RELAXATIONS),
    )

    @model_validator(mode="after")
    def require_exact_costs_and_complete_lattice(self) -> Self:
        _parse_fraction_token(self.known_score_exact, label="tiny action known score")
        if self.scenario_relaxed_costs != tuple(sorted(self.scenario_relaxed_costs)):
            raise ValueError("tiny action scenario costs are not canonically ordered")
        if len({item[0] for item in self.scenario_relaxed_costs}) != len(
            self.scenario_relaxed_costs
        ):
            raise ValueError("tiny action repeats a scenario cost")
        for _scenario_id, cost in self.scenario_relaxed_costs:
            _parse_fraction_token(cost, label="tiny action relaxed cost", positive=True)
        if tuple(item[0] for item in self.absent_relaxation_penalties) != (
            GATE1_FAVORABLE_RELAXATIONS
        ):
            raise ValueError("tiny action relaxation penalty census differs")
        for _relaxation, penalty in self.absent_relaxation_penalties:
            _parse_fraction_token(penalty, label="tiny action relaxation penalty")
        return self


class Gate1TinyProblem(FrozenExperimentModel):
    """A content-addressed finite state/action problem for exhaustive bound checks."""

    schema_version: Literal["yieldforge.m11-gate1-tiny-problem.v1"] = (
        "yieldforge.m11-gate1-tiny-problem.v1"
    )
    problem_id: StrictStr = Field(pattern=r"^yfm11tp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_root_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    root_state_id: Literal["gate1-tiny-root"] = "gate1-tiny-root"
    favorable_relaxations: tuple[StrictStr, ...] = GATE1_FAVORABLE_RELAXATIONS
    scenarios: tuple[Gate1TinyScenario, ...] = Field(min_length=2, max_length=2)
    actions: tuple[Gate1TinyAction, ...] = Field(min_length=4, max_length=4)
    action_census_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    baseline_selection_rule: Literal["minimum_known_score_then_action_id"] = (
        "minimum_known_score_then_action_id"
    )

    @model_validator(mode="after")
    def require_complete_problem_and_identity(self) -> Self:
        if self.favorable_relaxations != GATE1_FAVORABLE_RELAXATIONS:
            raise ValueError("tiny problem favorable relaxation registry differs")
        if self.scenarios != tuple(sorted(self.scenarios, key=lambda item: item.scenario_id)):
            raise ValueError("tiny problem scenarios are not canonically ordered")
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("tiny problem repeats a scenario")
        probability = sum(
            (
                _parse_fraction_token(
                    item.probability_exact,
                    label="tiny scenario probability",
                    positive=True,
                )
                for item in self.scenarios
            ),
            start=Fraction(0),
        )
        if probability != 1:
            raise ValueError("tiny problem scenario probabilities must sum to one")
        if self.actions != tuple(sorted(self.actions, key=lambda item: item.action_id)):
            raise ValueError("tiny problem actions are not canonically ordered")
        action_ids = tuple(item.action_id for item in self.actions)
        if len(action_ids) != len(set(action_ids)) or any(
            tuple(item[0] for item in action.scenario_relaxed_costs) != scenario_ids
            for action in self.actions
        ):
            raise ValueError("tiny problem action or scenario census differs")
        census = "sha256:" + semantic_sha256(
            {"root_state_id": self.root_state_id, "action_ids": list(action_ids)}
        )
        if self.action_census_sha256 != census:
            raise ValueError("tiny problem action census digest differs")
        root = "sha256:" + semantic_sha256(
            self,
            excluded_fields={"problem_id", "content_sha256", "problem_root_sha256"},
        )
        if self.problem_root_sha256 != root:
            raise ValueError("tiny problem root digest differs")
        digest = semantic_sha256(self, excluded_fields={"problem_id", "content_sha256"})
        if self.problem_id != f"yfm11tp-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 tiny-problem identity does not match semantic content")
        return self


def build_gate1_tiny_problem() -> Gate1TinyProblem:
    """Build the preregistered two-scenario, four-action exact proof kernel."""

    scenarios = (
        Gate1TinyScenario(scenario_id="future-a", probability_exact="1/2"),
        Gate1TinyScenario(scenario_id="future-b", probability_exact="1/2"),
    )
    costs = {
        "baseline": ("0", ("110", "110"), "0"),
        "hedge": ("1", ("100", "100"), "0"),
        "specialist-a": ("2", ("60", "200"), "2"),
        "specialist-b": ("3", ("200", "60"), "2"),
    }
    actions = tuple(
        Gate1TinyAction(
            action_id=action_id,
            known_score_exact=known_score,
            scenario_relaxed_costs=tuple(
                (scenario.scenario_id, cost)
                for scenario, cost in zip(scenarios, relaxed_costs, strict=True)
            ),
            absent_relaxation_penalties=tuple(
                (relaxation, penalty) for relaxation in GATE1_FAVORABLE_RELAXATIONS
            ),
        )
        for action_id, (known_score, relaxed_costs, penalty) in costs.items()
    )
    action_census = "sha256:" + semantic_sha256(
        {
            "root_state_id": "gate1-tiny-root",
            "action_ids": [item.action_id for item in actions],
        }
    )
    root_semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-tiny-problem.v1",
        "root_state_id": "gate1-tiny-root",
        "favorable_relaxations": list(GATE1_FAVORABLE_RELAXATIONS),
        "scenarios": [item.model_dump(mode="json") for item in scenarios],
        "actions": [item.model_dump(mode="json") for item in actions],
        "action_census_sha256": action_census,
        "baseline_selection_rule": "minimum_known_score_then_action_id",
    }
    root = "sha256:" + semantic_sha256(root_semantic)
    semantic = {"problem_root_sha256": root, **root_semantic}
    identifier, content = _identity("yfm11tp-", semantic)
    return Gate1TinyProblem(
        problem_id=identifier,
        content_sha256=content,
        problem_root_sha256=root,
        scenarios=scenarios,
        actions=actions,
        action_census_sha256=action_census,
    )


class Gate1TinyRelaxationCheck(FrozenExperimentModel):
    """One exact neighboring edge in the favorable-relaxation lattice."""

    relaxation: StrictStr = Field(min_length=1)
    constrained_optimum_exact: StrictStr = Field(min_length=1)
    relaxed_optimum_exact: StrictStr = Field(min_length=1)
    policy_evaluation_count: StrictInt = Field(gt=0)
    monotone_favorable_direction: Literal[True] = True

    @model_validator(mode="after")
    def require_monotone_edge(self) -> Self:
        constrained = _parse_fraction_token(
            self.constrained_optimum_exact,
            label="tiny constrained optimum",
        )
        relaxed = _parse_fraction_token(
            self.relaxed_optimum_exact,
            label="tiny relaxed optimum",
        )
        if relaxed > constrained:
            raise ValueError("favorable relaxation increased the tiny exact optimum")
        return self


class _TinyEnumeration(NamedTuple):
    relaxed_lower_bound: Fraction
    exact_full_optimum: Fraction
    exact_known_optimum: Fraction
    baseline_feasible: Fraction
    known_feasible: Fraction
    full_information_policy_evaluation_count: int
    known_only_policy_evaluation_count: int
    baseline_policy_evaluation_count: int
    known_policy_evaluation_count: int
    full_information_winning_mappings: tuple[tuple[tuple[str, str], ...], ...]
    exact_known_winning_action_ids: tuple[str, ...]
    baseline_selected_action_id: str
    known_selected_action_id: str
    relaxation_checks: tuple[Gate1TinyRelaxationCheck, ...]
    exhaustive_evaluation_sha256: str


def _tiny_action_cost(
    action: Gate1TinyAction,
    scenario_id: str,
    active_relaxations: frozenset[str],
) -> Fraction:
    relaxed = dict(action.scenario_relaxed_costs)
    penalties = dict(action.absent_relaxation_penalties)
    return _parse_fraction_token(
        relaxed[scenario_id],
        label="tiny action relaxed cost",
        positive=True,
    ) + sum(
        (
            _parse_fraction_token(
                penalties[relaxation],
                label="tiny action relaxation penalty",
            )
            for relaxation in GATE1_FAVORABLE_RELAXATIONS
            if relaxation not in active_relaxations
        ),
        start=Fraction(0),
    )


def _enumerate_gate1_tiny_problem(problem: Gate1TinyProblem) -> _TinyEnumeration:
    probabilities = {
        item.scenario_id: _parse_fraction_token(
            item.probability_exact,
            label="tiny scenario probability",
            positive=True,
        )
        for item in problem.scenarios
    }
    scenario_ids = tuple(item.scenario_id for item in problem.scenarios)
    action_by_id = {item.action_id: item for item in problem.actions}
    action_ids = tuple(action_by_id)
    all_relaxations = frozenset(GATE1_FAVORABLE_RELAXATIONS)
    masks = (frozenset(), all_relaxations) + tuple(
        all_relaxations - {relaxation} for relaxation in GATE1_FAVORABLE_RELAXATIONS
    )
    mask_results: dict[
        frozenset[str], tuple[Fraction, tuple[tuple[tuple[str, str], ...], ...]]
    ] = {}
    evaluation_rows: list[dict[str, object]] = []
    evaluation_count = 0
    for active in masks:
        scored: list[tuple[Fraction, tuple[tuple[str, str], ...]]] = []
        for chosen in product(action_ids, repeat=len(scenario_ids)):
            mapping = tuple(zip(scenario_ids, chosen, strict=True))
            cost = sum(
                (
                    probabilities[scenario_id]
                    * _tiny_action_cost(action_by_id[action_id], scenario_id, active)
                    for scenario_id, action_id in mapping
                ),
                start=Fraction(0),
            )
            scored.append((cost, mapping))
            evaluation_rows.append(
                {
                    "active_relaxations": sorted(active),
                    "mapping": [list(item) for item in mapping],
                    "cost_exact": str(cost),
                }
            )
            evaluation_count += 1
        optimum = min(item[0] for item in scored)
        winners = tuple(item[1] for item in scored if item[0] == optimum)
        mask_results[active] = (optimum, winners)

    no_relaxation = frozenset()
    exact_full, full_winners = mask_results[no_relaxation]
    relaxed_lower, _relaxed_winners = mask_results[all_relaxations]

    shared_scores = tuple(
        (
            sum(
                (
                    probabilities[scenario_id]
                    * _tiny_action_cost(action, scenario_id, no_relaxation)
                    for scenario_id in scenario_ids
                ),
                start=Fraction(0),
            ),
            action.action_id,
        )
        for action in problem.actions
    )
    exact_known = min(item[0] for item in shared_scores)
    known_winners = tuple(item[1] for item in shared_scores if item[0] == exact_known)

    baseline_action = min(
        problem.actions,
        key=lambda item: (
            _parse_fraction_token(item.known_score_exact, label="tiny action known score"),
            item.action_id,
        ),
    )
    known_action = min(
        problem.actions,
        key=lambda item: (
            _parse_fraction_token(item.known_score_exact, label="tiny action known score"),
            item.action_id,
        ),
    )
    baseline_cost = next(item[0] for item in shared_scores if item[1] == baseline_action.action_id)
    known_cost = next(item[0] for item in shared_scores if item[1] == known_action.action_id)
    checks = tuple(
        Gate1TinyRelaxationCheck(
            relaxation=relaxation,
            constrained_optimum_exact=str(mask_results[all_relaxations - {relaxation}][0]),
            relaxed_optimum_exact=str(relaxed_lower),
            policy_evaluation_count=len(action_ids) ** len(scenario_ids),
        )
        for relaxation in GATE1_FAVORABLE_RELAXATIONS
    )
    evaluation_digest = "sha256:" + semantic_sha256(
        {
            "problem_root_sha256": problem.problem_root_sha256,
            "full_information_evaluations": evaluation_rows,
            "known_only_scores": [
                {"action_id": action_id, "cost_exact": str(cost)}
                for cost, action_id in shared_scores
            ],
            "baseline_selected_action_id": baseline_action.action_id,
            "known_selected_action_id": known_action.action_id,
        }
    )
    return _TinyEnumeration(
        relaxed_lower_bound=relaxed_lower,
        exact_full_optimum=exact_full,
        exact_known_optimum=exact_known,
        baseline_feasible=baseline_cost,
        known_feasible=known_cost,
        full_information_policy_evaluation_count=evaluation_count,
        known_only_policy_evaluation_count=len(problem.actions),
        baseline_policy_evaluation_count=len(problem.actions),
        known_policy_evaluation_count=len(problem.actions),
        full_information_winning_mappings=full_winners,
        exact_known_winning_action_ids=known_winners,
        baseline_selected_action_id=baseline_action.action_id,
        known_selected_action_id=known_action.action_id,
        relaxation_checks=checks,
        exhaustive_evaluation_sha256=evaluation_digest,
    )


def _require_tiny_cell_matches(cell: Gate1StreamCell, result: _TinyEnumeration) -> None:
    lower = Fraction(str(cell.lower_bound.lower_bound_cost))
    baseline = Fraction(str(cell.baseline_feasible_cost))
    known = Fraction(str(cell.known_only_feasible_cost))
    if lower != result.relaxed_lower_bound:
        raise Gate1BoundAuditError("Gate 1 cell lower bound differs from enumerated relaxation")
    if baseline != result.baseline_feasible:
        raise Gate1BoundAuditError("Gate 1 cell differs from enumerated feasible baseline")
    if known != result.known_feasible:
        raise Gate1BoundAuditError("Gate 1 cell differs from enumerated known-only policy")
    failures: list[str] = []
    if not lower <= result.exact_full_optimum:
        failures.append("L_full <= exact_full_optimum")
    if not result.exact_full_optimum <= baseline:
        failures.append("exact_full_optimum <= B_feasible")
    if not result.exact_known_optimum <= known:
        failures.append("exact_known_optimum <= K_feasible")
    if not known - lower >= result.exact_known_optimum - result.exact_full_optimum:
        failures.append("unknown-future gap upper bound")
    if failures:
        raise Gate1BoundAuditError("Gate 1 tiny audit failed: " + "; ".join(failures))


class Gate1TinyAudit(FrozenExperimentModel):
    """A self-verifying exhaustive certificate for every Gate 1 bound inequality."""

    schema_version: Literal["yieldforge.m11-gate1-tiny-audit.v2"] = (
        "yieldforge.m11-gate1-tiny-audit.v2"
    )
    audit_id: StrictStr = Field(pattern=r"^yfm11ta-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem: Gate1TinyProblem
    cell: Gate1StreamCell
    problem_root_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    root_state_id: StrictStr = Field(min_length=1)
    action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    action_census_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    exhaustive_evaluation_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    full_information_policy_evaluation_count: StrictInt = Field(gt=0)
    known_only_policy_evaluation_count: StrictInt = Field(gt=0)
    baseline_policy_evaluation_count: StrictInt = Field(gt=0)
    known_policy_evaluation_count: StrictInt = Field(gt=0)
    full_information_winning_mappings: tuple[tuple[tuple[StrictStr, StrictStr], ...], ...] = Field(
        min_length=1
    )
    exact_known_winning_action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    baseline_selected_action_id: StrictStr = Field(min_length=1)
    known_selected_action_id: StrictStr = Field(min_length=1)
    relaxed_lower_bound_exact: StrictStr = Field(min_length=1)
    exact_full_optimum: StrictStr = Field(min_length=1)
    exact_known_optimum: StrictStr = Field(min_length=1)
    baseline_feasible_exact: StrictStr = Field(min_length=1)
    known_feasible_exact: StrictStr = Field(min_length=1)
    relaxation_checks: tuple[Gate1TinyRelaxationCheck, ...] = Field(
        min_length=len(GATE1_FAVORABLE_RELAXATIONS),
        max_length=len(GATE1_FAVORABLE_RELAXATIONS),
    )
    complete: Literal[True] = True
    truncated_count: Literal[0] = 0
    lower_not_above_exact_full: Literal[True] = True
    exact_full_not_above_baseline: Literal[True] = True
    exact_known_not_above_known_feasible: Literal[True] = True
    unknown_gap_upper_bound_holds: Literal[True] = True
    all_inequalities_hold: Literal[True] = True

    @model_validator(mode="after")
    def require_recomputed_certificate_and_identity(self) -> Self:
        result = _enumerate_gate1_tiny_problem(self.problem)
        _require_tiny_cell_matches(self.cell, result)
        expected = (
            self.problem.problem_root_sha256,
            self.problem.root_state_id,
            tuple(item.action_id for item in self.problem.actions),
            self.problem.action_census_sha256,
            result.exhaustive_evaluation_sha256,
            result.full_information_policy_evaluation_count,
            result.known_only_policy_evaluation_count,
            result.baseline_policy_evaluation_count,
            result.known_policy_evaluation_count,
            result.full_information_winning_mappings,
            result.exact_known_winning_action_ids,
            result.baseline_selected_action_id,
            result.known_selected_action_id,
            str(result.relaxed_lower_bound),
            str(result.exact_full_optimum),
            str(result.exact_known_optimum),
            str(result.baseline_feasible),
            str(result.known_feasible),
            result.relaxation_checks,
        )
        actual = (
            self.problem_root_sha256,
            self.root_state_id,
            self.action_ids,
            self.action_census_sha256,
            self.exhaustive_evaluation_sha256,
            self.full_information_policy_evaluation_count,
            self.known_only_policy_evaluation_count,
            self.baseline_policy_evaluation_count,
            self.known_policy_evaluation_count,
            self.full_information_winning_mappings,
            self.exact_known_winning_action_ids,
            self.baseline_selected_action_id,
            self.known_selected_action_id,
            self.relaxed_lower_bound_exact,
            self.exact_full_optimum,
            self.exact_known_optimum,
            self.baseline_feasible_exact,
            self.known_feasible_exact,
            self.relaxation_checks,
        )
        if actual != expected:
            raise ValueError("Gate 1 tiny certificate differs from exhaustive enumeration")
        digest = semantic_sha256(self, excluded_fields={"audit_id", "content_sha256"})
        if self.audit_id != f"yfm11ta-{digest[:24]}" or self.content_sha256 != (f"sha256:{digest}"):
            raise ValueError("Gate 1 tiny-audit identity does not match semantic content")
        return self


def audit_tiny_gate1_bounds(
    cell: Gate1StreamCell,
    *,
    problem: Gate1TinyProblem,
    expected_problem_root_sha256: str,
) -> Gate1TinyAudit:
    """Enumerate the finite proof kernel and bind its checked certificate to one tiny cell."""

    if problem.problem_root_sha256 != expected_problem_root_sha256:
        raise Gate1EvidenceError("Gate 1 tiny problem root differs from the preregistered root")
    result = _enumerate_gate1_tiny_problem(problem)
    _require_tiny_cell_matches(cell, result)
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-tiny-audit.v2",
        "problem": problem.model_dump(mode="json"),
        "cell": cell.model_dump(mode="json"),
        "problem_root_sha256": problem.problem_root_sha256,
        "root_state_id": problem.root_state_id,
        "action_ids": [item.action_id for item in problem.actions],
        "action_census_sha256": problem.action_census_sha256,
        "exhaustive_evaluation_sha256": result.exhaustive_evaluation_sha256,
        "full_information_policy_evaluation_count": (
            result.full_information_policy_evaluation_count
        ),
        "known_only_policy_evaluation_count": result.known_only_policy_evaluation_count,
        "baseline_policy_evaluation_count": result.baseline_policy_evaluation_count,
        "known_policy_evaluation_count": result.known_policy_evaluation_count,
        "full_information_winning_mappings": [
            [list(item) for item in mapping] for mapping in result.full_information_winning_mappings
        ],
        "exact_known_winning_action_ids": list(result.exact_known_winning_action_ids),
        "baseline_selected_action_id": result.baseline_selected_action_id,
        "known_selected_action_id": result.known_selected_action_id,
        "relaxed_lower_bound_exact": str(result.relaxed_lower_bound),
        "exact_full_optimum": str(result.exact_full_optimum),
        "exact_known_optimum": str(result.exact_known_optimum),
        "baseline_feasible_exact": str(result.baseline_feasible),
        "known_feasible_exact": str(result.known_feasible),
        "relaxation_checks": [item.model_dump(mode="json") for item in result.relaxation_checks],
        "complete": True,
        "truncated_count": 0,
        "lower_not_above_exact_full": True,
        "exact_full_not_above_baseline": True,
        "exact_known_not_above_known_feasible": True,
        "unknown_gap_upper_bound_holds": True,
        "all_inequalities_hold": True,
    }
    identifier, content = _identity("yfm11ta-", semantic)
    return Gate1TinyAudit(
        audit_id=identifier,
        content_sha256=content,
        problem=problem,
        cell=cell,
        problem_root_sha256=problem.problem_root_sha256,
        root_state_id=problem.root_state_id,
        action_ids=tuple(item.action_id for item in problem.actions),
        action_census_sha256=problem.action_census_sha256,
        exhaustive_evaluation_sha256=result.exhaustive_evaluation_sha256,
        full_information_policy_evaluation_count=(result.full_information_policy_evaluation_count),
        known_only_policy_evaluation_count=result.known_only_policy_evaluation_count,
        baseline_policy_evaluation_count=result.baseline_policy_evaluation_count,
        known_policy_evaluation_count=result.known_policy_evaluation_count,
        full_information_winning_mappings=result.full_information_winning_mappings,
        exact_known_winning_action_ids=result.exact_known_winning_action_ids,
        baseline_selected_action_id=result.baseline_selected_action_id,
        known_selected_action_id=result.known_selected_action_id,
        relaxed_lower_bound_exact=str(result.relaxed_lower_bound),
        exact_full_optimum=str(result.exact_full_optimum),
        exact_known_optimum=str(result.exact_known_optimum),
        baseline_feasible_exact=str(result.baseline_feasible),
        known_feasible_exact=str(result.known_feasible),
        relaxation_checks=result.relaxation_checks,
    )


def verify_gate1_tiny_audit(
    audit: Gate1TinyAudit,
    *,
    expected_problem_root_sha256: str,
) -> Gate1TinyAudit:
    """Revalidate a persisted exhaustive certificate against its preregistered root."""

    if audit.problem_root_sha256 != expected_problem_root_sha256:
        raise Gate1EvidenceError("Gate 1 tiny audit problem root differs")
    return Gate1TinyAudit.model_validate(
        audit.model_dump(mode="python", round_trip=True),
        strict=True,
    )


class Gate1SourceContext(NamedTuple):
    """Strictly loaded official pack and its exact geometry parents."""

    bundle: M11PackBundle
    source_manifest: M11SourceManifest
    m3_input: M3ResidualInputPack
    loco_catalog: LOCoCatalog


def load_official_gate1_context(repository_root: Path) -> Gate1SourceContext:
    """Strict-load the official e561fec-compatible pack and all Gate 1 source leaves."""

    root = Path(repository_root)
    contract_path = root / "benchmarks/falsification/m11-contract-v1.json"
    population_path = root / "benchmarks/falsification/m11-population-v1.json"
    source_manifest_path = root / "benchmarks/falsification/source-manifest-v1.json"
    bundle = load_m11_pack_bundle(
        repository_root=root,
        contract_path=contract_path,
        population_path=population_path,
        source_manifest_path=source_manifest_path,
    )
    source_manifest = load_m11_source_manifest(source_manifest_path)
    m3_input = load_m3_input_pack(root / source_manifest.lectra.m3_input_repository_path)
    loco_catalog = load_loco_catalog(root / "datasets/catalogs/loco-2dics-v1/loco-catalog.json")
    if (
        m3_input.input_id != source_manifest.lectra.m3_input_id
        or m3_input.content_sha256 != source_manifest.lectra.m3_input_content_sha256
        or loco_catalog.catalog_id != source_manifest.loco.catalog_id
        or loco_catalog.content_sha256 != source_manifest.loco.catalog_content_sha256
    ):
        raise Gate1EvidenceError("Gate 1 source context does not match the official attestation")
    return Gate1SourceContext(bundle, source_manifest, m3_input, loco_catalog)


def _polygon_area(points: tuple[tuple[Fraction, Fraction], ...]) -> Fraction:
    if len(points) < 3:
        raise Gate1EvidenceError("source polygon requires at least three vertices")
    vertices = points[:-1] if points[0] == points[-1] else points
    if len(set(vertices)) < 3:
        raise Gate1EvidenceError("source polygon requires three distinct vertices")
    twice = sum(
        (
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(vertices, vertices[1:] + vertices[:1], strict=True)
        ),
        start=Fraction(0),
    )
    area = abs(twice) / 2
    if area <= 0:
        raise Gate1EvidenceError("source polygon has nonpositive exact area")
    return area


def _point_fraction(value: float) -> Fraction:
    if not math.isfinite(value):
        raise Gate1EvidenceError("source coordinate must be finite")
    return Fraction(str(value))


def _float_matches_within_one_ulp(left: float, right: float) -> bool:
    """Accept exact equality or one adjacent finite IEEE-754 serialization value."""

    if not math.isfinite(left) or not math.isfinite(right):
        return False
    return (
        left == right or math.nextafter(left, right) == right or math.nextafter(right, left) == left
    )


def _rotation_allowed(rotation: float, allowed: list[float] | None, tolerance: float) -> bool:
    return allowed is not None and any(
        abs(math.remainder(rotation - item, 360.0)) <= tolerance for item in allowed
    )


@dataclass(frozen=True)
class _DemandComponent:
    geometry_reference_id: str
    geometry_sha256: str
    source_binding_sha256: str
    source_instance: str | None
    exact_area: Fraction
    quantity: int


@dataclass(frozen=True)
class _PayloadProof:
    source_kind: Literal["lectra", "loco_2dics"]
    demand_components: tuple[_DemandComponent, ...]
    candidate_options: tuple[tuple[str, str], ...]
    candidate_used_layout_widths: tuple[Fraction, ...]
    candidate_geometry_witness_sha256s: tuple[str, ...]
    selected_candidate_id: str
    selected_used_layout_width: Fraction
    purchased_stock_width: Fraction
    purchased_stock_height: Fraction
    selected_stock_area: Fraction
    reference_area_key: str
    reference_area: Fraction
    verification_kind: Literal["lectra_m3_candidate_geometry", "loco_bbox_shelf_geometry"]
    geometry_witness_sha256: str


def _reference_registry(context: Gate1SourceContext) -> dict[str, dict[str, Fraction]]:
    result: dict[str, dict[str, Fraction]] = {}
    for corpus in context.bundle.population.reference_areas:
        result[corpus.corpus_id] = {
            key: _fraction(value, label="registered reference area", positive=True)
            for key, value in corpus.by_material
        }
    return result


def _validate_lectra_payload(
    context: Gate1SourceContext,
    payload: M11Payload,
    references: dict[str, Fraction],
) -> _PayloadProof:
    try:
        tasks_index = int(payload.source_case_id.removeprefix("lectra-task:"))
    except ValueError as error:
        raise Gate1EvidenceError("Lectra payload source task identity is invalid") from error
    pairs = tuple(item for item in context.m3_input.task_pairs if item.tasks_index == tasks_index)
    if len(pairs) != 1:
        raise Gate1EvidenceError("Lectra payload does not map to exactly one pinned M3 task")
    pair = pairs[0]
    geometry_reference = payload.geometry_references[0]
    task_digest = hashlib.sha256(
        (f"{context.m3_input.input_id}|{context.m3_input.content_sha256}|{tasks_index}").encode()
    ).hexdigest()
    if (
        geometry_reference.reference_id != payload.source_case_id
        or geometry_reference.content_sha256 != f"sha256:{task_digest}"
        or geometry_reference.geometry_sha256 != task_digest
    ):
        raise Gate1EvidenceError("Lectra payload task geometry binding differs from pinned M3")

    options: list[tuple[str, str]] = []
    candidate_areas: list[tuple[Fraction, str, int, Fraction]] = []
    geometry_evidence: list[dict[str, object]] = []
    tolerance = context.m3_input.primary_geometry_config.coordinate_tolerance
    for position, (reference, selected) in enumerate(
        zip(payload.candidate_references, pair.selected_candidates, strict=True)
    ):
        archive_matches = tuple(
            item
            for item in pair.archives
            if item.seed == selected.seed
            and item.job_id == selected.archive_job_id
            and item.batch_sha256 == selected.archive_batch_sha256
        )
        expected_content = (
            "sha256:"
            + hashlib.sha256(
                (
                    f"{context.m3_input.input_id}|{context.m3_input.content_sha256}|{tasks_index}|"
                    f"{position}|{selected.archive_batch_sha256}|{selected.candidate.candidate_id}"
                ).encode()
            ).hexdigest()
        )
        if (
            len(archive_matches) != 1
            or reference.position != position
            or reference.candidate_id != selected.candidate.candidate_id
            or reference.content_sha256 != expected_content
        ):
            raise Gate1EvidenceError("Lectra candidate/archive binding differs from pinned M3")
        candidate = selected.candidate
        if candidate.width > pair.problem.sheet_length:
            raise Gate1EvidenceError("Lectra candidate purchase width exceeds source stock")
        placements = {item.part_id: item for item in candidate.placements}
        if any(
            not _rotation_allowed(
                placements[part.id].rotation,
                part.allowed_orientations,
                tolerance,
            )
            for part in pair.problem.parts
        ):
            raise Gate1EvidenceError("Lectra candidate uses a source-disallowed rotation")
        placed = placed_part_polygons(
            pair.problem,
            candidate,
            context.m3_input.primary_geometry_config,
        )
        candidate_stock = box(0.0, 0.0, candidate.width, pair.problem.strip_height)
        area_tolerance = max(
            tolerance,
            candidate_stock.area * context.m3_input.primary_geometry_config.relative_area_tolerance,
        )
        outside = sum(item.difference(candidate_stock).area for item in placed.values())
        if outside > area_tolerance:
            raise Gate1EvidenceError("Lectra candidate exceeds its verified used-layout boundary")
        used_width = Fraction(str(candidate.width))
        used_area = used_width * Fraction(str(pair.problem.strip_height))
        options.append((candidate.candidate_id, reference.content_sha256))
        candidate_areas.append((used_area, candidate.candidate_id, position, used_width))
        geometry_evidence.append(
            {
                "position": position,
                "candidate_id": candidate.candidate_id,
                "archive_job_id": selected.archive_job_id,
                "archive_batch_sha256": selected.archive_batch_sha256,
                "used_layout_width_exact": str(used_width),
                "used_layout_area_exact": str(used_area),
                "purchased_stock_width_exact": str(Fraction(str(pair.problem.sheet_length))),
                "purchased_stock_height_exact": str(Fraction(str(pair.problem.strip_height))),
                "part_count": len(placed),
            }
        )
    _selected_used_area, selected_candidate_id, _position, selected_used_width = min(
        candidate_areas
    )
    purchased_width = Fraction(str(pair.problem.sheet_length))
    purchased_height = Fraction(str(pair.problem.strip_height))
    selected_area = purchased_width * purchased_height

    components: list[_DemandComponent] = []
    for part in pair.problem.parts:
        points = tuple((_point_fraction(x), _point_fraction(y)) for x, y in part.shape)
        exact_area = _polygon_area(points)
        geometry_digest = hashlib.sha256(
            (part.id + "|" + "|".join(f"{str(x)},{str(y)}" for x, y in points)).encode()
        ).hexdigest()
        components.append(
            _DemandComponent(
                geometry_reference_id=f"{payload.source_case_id}#{part.id}",
                geometry_sha256=geometry_digest,
                source_binding_sha256=geometry_reference.content_sha256,
                source_instance=None,
                exact_area=exact_area,
                quantity=part.demand,
            )
        )
    if len(set(references.values())) != 1:
        raise Gate1EvidenceError("Lectra fixed reference-area registry is contradictory")
    reference_area = next(iter(references.values()))
    candidate_witnesses = tuple(
        "sha256:"
        + semantic_sha256(
            {
                "m3_input_id": context.m3_input.input_id,
                "m3_content_sha256": context.m3_input.content_sha256,
                "tasks_index": tasks_index,
                "candidates": geometry_evidence,
                "selected_candidate_id": candidate_id,
            }
        )
        for candidate_id, _content_sha256 in options
    )
    selected_position = next(
        position
        for position, (candidate_id, _content) in enumerate(options)
        if candidate_id == selected_candidate_id
    )
    return _PayloadProof(
        source_kind="lectra",
        demand_components=tuple(components),
        candidate_options=tuple(options),
        candidate_used_layout_widths=tuple(item[3] for item in candidate_areas),
        candidate_geometry_witness_sha256s=candidate_witnesses,
        selected_candidate_id=selected_candidate_id,
        selected_used_layout_width=selected_used_width,
        purchased_stock_width=purchased_width,
        purchased_stock_height=purchased_height,
        selected_stock_area=selected_area,
        reference_area_key="lectra-fixed-median",
        reference_area=reference_area,
        verification_kind="lectra_m3_candidate_geometry",
        geometry_witness_sha256=candidate_witnesses[selected_position],
    )


def _validate_loco_payload(
    context: Gate1SourceContext,
    payload: M11Payload,
    references: dict[str, Fraction],
) -> _PayloadProof:
    items = {item.item_id: item for item in context.loco_catalog.items}
    components: list[_DemandComponent] = []
    polygons = {}
    instance_names: set[str] = set()
    for reference, quantity in zip(
        payload.geometry_references,
        payload.quantities,
        strict=True,
    ):
        try:
            item = items[reference.reference_id]
        except KeyError as error:
            raise Gate1EvidenceError("LOCo payload references an absent source item") from error
        exact_points = tuple((Fraction(x), Fraction(y)) for x, y in item.exact_normalized_vertices)
        exact_area = _polygon_area(exact_points)
        min_x = min(point[0] for point in exact_points)
        max_x = max(point[0] for point in exact_points)
        min_y = min(point[1] for point in exact_points)
        max_y = max(point[1] for point in exact_points)
        if (
            reference.content_sha256 != item.content_sha256
            or reference.geometry_sha256 != item.geometry.polygon_sha256
            or reference.instance_name != item.instance_name
            or reference.source_item_index != item.source_item_index
            or reference.source_demand != item.source_demand
            or reference.bbox_width != float(max_x - min_x)
            or reference.bbox_height != float(max_y - min_y)
            or not math.isclose(float(exact_area), item.geometry.area, rel_tol=0.0, abs_tol=1e-9)
        ):
            raise Gate1EvidenceError("LOCo payload geometry differs from the pinned exact source")
        polygons[item.item_id] = polygon_from_record(item.geometry)
        instance_names.add(item.instance_name)
        components.append(
            _DemandComponent(
                geometry_reference_id=item.item_id,
                geometry_sha256=item.geometry.polygon_sha256,
                source_binding_sha256=item.content_sha256,
                source_instance=item.instance_name,
                exact_area=exact_area,
                quantity=quantity,
            )
        )
    if len(instance_names) != 1 or payload.fallback_stock is None:
        raise Gate1EvidenceError("LOCo payload crosses source instances or lacks fallback stock")
    instance = next(iter(instance_names))
    reference_area_key = f"loco:{instance}"
    try:
        reference_area = references[reference_area_key]
    except KeyError as error:
        raise Gate1EvidenceError("LOCo payload lacks its source-instance reference area") from error

    fallback = payload.fallback_stock
    stock = box(0.0, 0.0, fallback.width, fallback.height)
    placed = []
    for placement in fallback.placements:
        try:
            source = polygons[placement.geometry_reference_id]
        except KeyError as error:
            raise Gate1EvidenceError("LOCo fallback placement lacks source geometry") from error
        transformed = translate(source, xoff=placement.x, yoff=placement.y)
        if transformed.difference(stock).area > 1e-9:
            raise Gate1EvidenceError("LOCo fallback polygon exceeds generated stock")
        placed.append(transformed)
    union = union_all(placed)
    overlap = sum(item.area for item in placed) - union.area
    if overlap > max(1e-9, stock.area * 1e-12):
        raise Gate1EvidenceError("LOCo fallback polygons overlap")
    exact_stock_area = Fraction(str(fallback.width)) * Fraction(str(fallback.height))
    if not _float_matches_within_one_ulp(fallback.area, float(exact_stock_area)):
        raise Gate1EvidenceError("LOCo fallback purchase area does not reconcile")
    option = payload.candidate_references[0]
    if option.candidate_id != fallback.stock_id or option.content_sha256 != fallback.content_sha256:
        raise Gate1EvidenceError("LOCo fallback candidate identity does not reconcile")
    witness_digest = semantic_sha256(
        {
            "fallback": fallback.model_dump(mode="json"),
            "source_item_content_sha256s": [
                component.source_binding_sha256 for component in components
            ],
            "placed_union_area": float(union.area),
        }
    )
    return _PayloadProof(
        source_kind="loco_2dics",
        demand_components=tuple(components),
        candidate_options=((option.candidate_id, option.content_sha256),),
        candidate_used_layout_widths=(Fraction(str(fallback.width)),),
        candidate_geometry_witness_sha256s=(f"sha256:{witness_digest}",),
        selected_candidate_id=option.candidate_id,
        selected_used_layout_width=Fraction(str(fallback.width)),
        purchased_stock_width=Fraction(str(fallback.width)),
        purchased_stock_height=Fraction(str(fallback.height)),
        selected_stock_area=exact_stock_area,
        reference_area_key=reference_area_key,
        reference_area=reference_area,
        verification_kind="loco_bbox_shelf_geometry",
        geometry_witness_sha256=f"sha256:{witness_digest}",
    )


def _payload_proof(
    context: Gate1SourceContext,
    payload: M11Payload,
    references: dict[str, dict[str, Fraction]],
) -> _PayloadProof:
    if payload.source_kind == "lectra":
        return _validate_lectra_payload(context, payload, references["lectra-m3-m4"])
    return _validate_loco_payload(context, payload, references["loco-2dics"])


def _policy_spec(policy_id: Gate1BaselinePolicyId) -> Gate1BaselinePolicySpec:
    matches = tuple(item for item in GATE1_BASELINE_POLICY_REGISTRY if item.policy_id == policy_id)
    if len(matches) != 1:
        raise Gate1EvidenceError("Gate 1 baseline policy is absent from the frozen registry")
    return matches[0]


def _select_verified_candidate(
    proof: _PayloadProof,
    policy: Gate1BaselinePolicySpec,
) -> tuple[str, Fraction, str]:
    if len(proof.candidate_options) != len(proof.candidate_used_layout_widths) or len(
        proof.candidate_options
    ) != len(proof.candidate_geometry_witness_sha256s):
        raise Gate1EvidenceError("verified candidate option evidence has contradictory censuses")
    if policy.policy_id == "fresh-candidate-position-0":
        position = 0
    elif policy.policy_id == "fresh-candidate-position-1":
        if len(proof.candidate_options) < 2:
            raise Gate1EvidenceError("candidate-position-1 is unavailable for this source payload")
        position = 1
    else:
        position = min(
            range(len(proof.candidate_options)),
            key=lambda item: (
                proof.candidate_used_layout_widths[item],
                proof.candidate_options[item][0],
                item,
            ),
        )
    return (
        proof.candidate_options[position][0],
        proof.candidate_used_layout_widths[position],
        proof.candidate_geometry_witness_sha256s[position],
    )


def _build_official_openings(
    *,
    context: Gate1SourceContext,
    stream: M11Stream,
    policy: Gate1BaselinePolicySpec,
    payloads: dict[str, M11Payload],
    references: dict[str, dict[str, Fraction]],
    proof_cache: dict[str, _PayloadProof],
) -> tuple[Gate1FeasibleOpening, ...]:
    openings: list[Gate1FeasibleOpening] = []
    for event in stream.events:
        try:
            payload = payloads[event.payload_id]
        except KeyError as error:
            raise Gate1EvidenceError("Gate 1 event references an absent payload") from error
        proof = proof_cache.get(payload.payload_id)
        if proof is None:
            proof = _payload_proof(context, payload, references)
            proof_cache[payload.payload_id] = proof
        selected_id, selected_used_width, selected_witness = _select_verified_candidate(
            proof,
            policy,
        )
        reference_area_key = (
            event.material_key
            if stream.corpus_id == "lectra-m3-m4"
            and event.material_key in references[stream.corpus_id]
            else proof.reference_area_key
        )
        openings.append(
            build_gate1_feasible_opening(
                event_position=event.position,
                event_id=event.event_id,
                payload_id=payload.payload_id,
                material_group=event.material_key,
                reference_area_key=reference_area_key,
                source_kind=proof.source_kind,
                candidate_options=proof.candidate_options,
                selected_candidate_id=selected_id,
                selection_rule=policy.selection_rule,
                verification_kind=proof.verification_kind,
                geometry_witness_sha256=selected_witness,
                known_positions_at_release=visible_event_positions(stream, event.released_at),
                stock_area=proof.selected_stock_area,
                reference_area=proof.reference_area,
                used_layout_width=selected_used_width,
                purchased_stock_width=proof.purchased_stock_width,
                purchased_stock_height=proof.purchased_stock_height,
            )
        )
    return tuple(openings)


def _build_calibration_score(
    *,
    corpus_id: M11CorpusId,
    policy_id: Gate1BaselinePolicyId,
    stream_costs: tuple[tuple[str, float], ...],
) -> Gate1CalibrationPolicyScore:
    canonical = tuple(sorted(stream_costs))
    exact = sum((Fraction(str(cost)) for _stream_id, cost in canonical), start=Fraction(0))
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-calibration-policy-score.v1",
        "corpus_id": corpus_id,
        "policy_id": policy_id,
        "calibration_stream_costs": [list(item) for item in canonical],
        "total_cost_exact": str(exact),
        "total_cost": round_half_up_cost(exact),
        "verified_feasible": True,
        "calibration_only": True,
        "confirmation_inputs_used": False,
    }
    identifier, content = _identity("yfm11bsc-", semantic)
    return Gate1CalibrationPolicyScore(
        score_id=identifier,
        content_sha256=content,
        corpus_id=corpus_id,
        policy_id=policy_id,
        calibration_stream_costs=canonical,
        total_cost_exact=str(exact),
        total_cost=round_half_up_cost(exact),
    )


def select_gate1_baseline_policy(
    context: Gate1SourceContext,
    corpus_id: M11CorpusId,
) -> Gate1BaselineSelectionEvidence:
    """Freeze the lowest-cost registered feasible policy using calibration streams only."""

    calibration = tuple(
        sorted(
            (
                stream
                for stream in context.bundle.population.streams
                if stream.corpus_id == corpus_id
                and stream.partition == "calibration"
                and stream.stream_kind == "primary"
            ),
            key=lambda item: item.stream_id,
        )
    )
    if len(calibration) != 8:
        raise Gate1EvidenceError("baseline selection requires exactly eight calibration streams")
    eligible = tuple(
        item for item in GATE1_BASELINE_POLICY_REGISTRY if corpus_id in item.supported_corpora
    )
    payloads = {item.payload_id: item for item in context.bundle.population.payloads}
    references = _reference_registry(context)
    proof_cache: dict[str, _PayloadProof] = {}
    scores: list[Gate1CalibrationPolicyScore] = []
    for policy in eligible:
        stream_costs: list[tuple[str, float]] = []
        for stream in calibration:
            openings = _build_official_openings(
                context=context,
                stream=stream,
                policy=policy,
                payloads=payloads,
                references=references,
                proof_cache=proof_cache,
            )
            witness = build_gate1_feasible_policy_cost(
                stream_id=stream.stream_id,
                policy_kind="baseline_as_of",
                openings=openings,
                registered_policy_id=policy.policy_id,
                evidence_stage="calibration_selection",
            )
            stream_costs.append((stream.stream_id, witness.feasible_cost))
        scores.append(
            _build_calibration_score(
                corpus_id=corpus_id,
                policy_id=policy.policy_id,
                stream_costs=tuple(stream_costs),
            )
        )
    canonical_scores = tuple(sorted(scores, key=lambda item: item.policy_id))
    best = min(item.total_cost for item in canonical_scores)
    tied = tuple(item.policy_id for item in canonical_scores if item.total_cost == best)
    calibration_ids = tuple(item.stream_id for item in calibration)
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-baseline-selection.v1",
        "contract_id": context.bundle.contract.contract_id,
        "contract_content_sha256": context.bundle.contract.content_sha256,
        "population_id": context.bundle.population.population_id,
        "population_content_sha256": context.bundle.population.content_sha256,
        "corpus_id": corpus_id,
        "registered_policy_ids": [item.policy_id for item in GATE1_BASELINE_POLICY_REGISTRY],
        "eligible_policy_ids": [item.policy_id for item in eligible],
        "calibration_stream_ids": list(calibration_ids),
        "policy_scores": [item.model_dump(mode="json") for item in canonical_scores],
        "selection_rule": "lowest_verified_calibration_cost_then_policy_id",
        "selected_policy_id": min(tied),
        "tied_lowest_policy_ids": list(tied),
        "calibration_only": True,
        "confirmation_inputs_used": False,
        "strongest_scope": (
            "strongest_within_registered_feasible_as_of_time_family_not_universal_optimum"
        ),
    }
    identifier, content = _identity("yfm11bs-", semantic)
    return Gate1BaselineSelectionEvidence(
        selection_id=identifier,
        content_sha256=content,
        contract_id=context.bundle.contract.contract_id,
        contract_content_sha256=context.bundle.contract.content_sha256,
        population_id=context.bundle.population.population_id,
        population_content_sha256=context.bundle.population.content_sha256,
        corpus_id=corpus_id,
        registered_policy_ids=tuple(item.policy_id for item in GATE1_BASELINE_POLICY_REGISTRY),
        eligible_policy_ids=tuple(item.policy_id for item in eligible),
        calibration_stream_ids=calibration_ids,
        policy_scores=canonical_scores,
        selected_policy_id=min(tied),
        tied_lowest_policy_ids=tied,
    )


def build_gate1_stream_cell(
    context: Gate1SourceContext,
    stream: M11Stream,
    *,
    baseline_selection: Gate1BaselineSelectionEvidence | None = None,
) -> Gate1StreamCell:
    """Build one complete official stream cell from exact demand and fallback geometry."""

    if baseline_selection is None:
        raise Gate1EvidenceError("official Gate 1 cells require calibration selection evidence")
    canonical_streams = tuple(
        item for item in context.bundle.population.streams if item.stream_id == stream.stream_id
    )
    if len(canonical_streams) != 1 or canonical_streams[0] != stream:
        raise Gate1EvidenceError("Gate 1 stream is not the canonical official population member")
    calibration_ids = tuple(
        sorted(
            item.stream_id
            for item in context.bundle.population.streams
            if item.corpus_id == stream.corpus_id
            and item.partition == "calibration"
            and item.stream_kind == "primary"
        )
    )
    if (
        baseline_selection.contract_id != context.bundle.contract.contract_id
        or baseline_selection.contract_content_sha256 != context.bundle.contract.content_sha256
        or baseline_selection.population_id != context.bundle.population.population_id
        or baseline_selection.population_content_sha256 != context.bundle.population.content_sha256
        or baseline_selection.corpus_id != stream.corpus_id
        or baseline_selection.calibration_stream_ids != calibration_ids
    ):
        raise Gate1EvidenceError(
            "Gate 1 cell calibration selection evidence does not match its corpus and pack"
        )
    policy = _policy_spec(baseline_selection.selected_policy_id)
    if stream.corpus_id not in policy.supported_corpora:
        raise Gate1EvidenceError("selected baseline policy does not support this source corpus")
    if (
        context.m3_input.input_id != context.source_manifest.lectra.m3_input_id
        or context.m3_input.content_sha256 != context.source_manifest.lectra.m3_input_content_sha256
        or context.loco_catalog.catalog_id != context.source_manifest.loco.catalog_id
        or context.loco_catalog.content_sha256
        != context.source_manifest.loco.catalog_content_sha256
    ):
        raise Gate1EvidenceError("Gate 1 context source identities changed after strict loading")
    payloads = {item.payload_id: item for item in context.bundle.population.payloads}
    references = _reference_registry(context)
    proof_cache: dict[str, _PayloadProof] = {}
    demands: list[Gate1DemandRecord] = []
    for event in stream.events:
        try:
            payload = payloads[event.payload_id]
        except KeyError as error:
            raise Gate1EvidenceError("Gate 1 event references an absent payload") from error
        proof = proof_cache.get(payload.payload_id)
        if proof is None:
            proof = _payload_proof(context, payload, references)
            proof_cache[payload.payload_id] = proof
        reference_area_key = (
            event.material_key
            if stream.corpus_id == "lectra-m3-m4"
            and event.material_key in references[stream.corpus_id]
            else proof.reference_area_key
        )
        for component in proof.demand_components:
            demands.append(
                build_gate1_demand_record(
                    event_position=event.position,
                    event_id=event.event_id,
                    geometry_reference_id=component.geometry_reference_id,
                    geometry_sha256=component.geometry_sha256,
                    source_binding_sha256=component.source_binding_sha256,
                    source_kind=proof.source_kind,
                    source_instance=component.source_instance,
                    material_group=event.material_key,
                    reference_area_key=reference_area_key,
                    unit_area=component.exact_area,
                    quantity=component.quantity,
                    reference_area=proof.reference_area,
                )
            )
    lower = calculate_relaxed_lower_bound(stream_id=stream.stream_id, demands=tuple(demands))
    opening_tuple = _build_official_openings(
        context=context,
        stream=stream,
        policy=policy,
        payloads=payloads,
        references=references,
        proof_cache=proof_cache,
    )
    baseline = build_gate1_feasible_policy_cost(
        stream_id=stream.stream_id,
        policy_kind="baseline_as_of",
        openings=opening_tuple,
        registered_policy_id=policy.policy_id,
        calibration_selection_id=baseline_selection.selection_id,
        evidence_stage="confirmation_application",
    )
    known_only = build_gate1_feasible_policy_cost(
        stream_id=stream.stream_id,
        policy_kind="known_only",
        openings=opening_tuple,
        registered_policy_id=policy.policy_id,
        calibration_selection_id=baseline_selection.selection_id,
        evidence_stage="confirmation_application",
    )
    return build_gate1_stream_cell_from_evidence(
        stream_id=stream.stream_id,
        corpus_id=stream.corpus_id,
        lower_bound=lower,
        baseline=baseline,
        known_only=known_only,
    )


__all__ = [
    "GATE1_ACTION_SET_CONTRACT",
    "GATE1_BASELINE_POLICY_REGISTRY",
    "GATE1_COMPUTE_CONTRACT",
    "GATE1_FAVORABLE_RELAXATIONS",
    "GATE1_FEASIBLE_ALGORITHM",
    "GATE1_LOWER_BOUND_PROOF_DIRECTION",
    "GATE1_NON_LATTICE_ASSUMPTIONS",
    "GATE1_RELAXATION_ASSUMPTIONS",
    "Gate1BoundAuditError",
    "Gate1BaselinePolicySpec",
    "Gate1BaselineSelectionEvidence",
    "Gate1CalibrationPolicyScore",
    "Gate1DemandRecord",
    "Gate1EvidenceError",
    "Gate1FeasibleOpening",
    "Gate1FeasiblePolicyCost",
    "Gate1LowerBound",
    "Gate1MaterialSubtotal",
    "Gate1SourceContext",
    "Gate1StreamCell",
    "Gate1TinyAudit",
    "Gate1TinyAction",
    "Gate1TinyProblem",
    "Gate1TinyRelaxationCheck",
    "Gate1TinyScenario",
    "audit_tiny_gate1_bounds",
    "build_gate1_demand_record",
    "build_gate1_feasible_opening",
    "build_gate1_feasible_policy_cost",
    "build_gate1_stream_cell",
    "build_gate1_stream_cell_from_evidence",
    "build_gate1_tiny_problem",
    "calculate_relaxed_lower_bound",
    "load_official_gate1_context",
    "round_down_cost",
    "round_half_up_cost",
    "select_gate1_baseline_policy",
    "verify_weaker_feasible_comparator_ceiling",
    "verify_gate1_tiny_audit",
]
