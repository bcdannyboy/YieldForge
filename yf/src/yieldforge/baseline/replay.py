"""Deterministic chronological replay for exact M7 complete-layout actions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Self

import shapely
from pydantic import Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator
from shapely.affinity import rotate

from yieldforge.baseline.actions import (
    build_remnant_action_from_search,
    build_standard_sheet_action,
)
from yieldforge.baseline.archives import VerifiedProblemCandidates
from yieldforge.baseline.contracts import (
    BaselineContractModel,
    LayoutFitSearchConfig,
    M7ActionKind,
    M7CandidateSetEvidence,
    M7LayoutActionEvidence,
    ReusableGeometryProblem,
    TemporalInstanceBinding,
)
from yieldforge.baseline.geometry import search_layout_translation
from yieldforge.baseline.policies import (
    ActionPolicyContext,
    M7PolicyIdentity,
    select_policy_action,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.replay.contracts import (
    M0_EVENT_STAGE_ORDER,
    InventoryItem,
    ReplayCostLedger,
    ReplayTerminalRecord,
    rounded_cost,
)
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.reuse.contracts import RemnantFitConfig, polygon_from_record
from yieldforge.reuse.geometry import material_key
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


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
        terminal_scrap_credit=left.terminal_scrap_credit + right.terminal_scrap_credit,
    )


def _inventory_ids(inventory: tuple[InventoryItem, ...]) -> tuple[str, ...]:
    return tuple(item.remnant.remnant_id for item in inventory)


def _storage_cost(
    inventory: tuple[InventoryItem, ...],
    *,
    start: datetime,
    end: datetime,
    rate: float,
) -> float:
    elapsed_hours = (end - start).total_seconds() / 3600.0
    if elapsed_hours < 0:
        raise ValueError("M7 storage interval cannot run backward")
    area = sum(item.remnant.geometry.area for item in inventory)
    return rounded_cost(area * elapsed_hours * rate)


class M7ReplayEngineIdentity(BaselineContractModel):
    name: Literal["yieldforge.m7-baseline-replay"] = "yieldforge.m7-baseline-replay"
    version: Literal["1.0.0"] = "1.0.0"
    shapely_version: StrictStr = Field(min_length=1)


class M7ReplayInput(BaselineContractModel):
    """One content-addressed stream replay with shared problem and candidate bindings."""

    schema_version: Literal["yieldforge.m7-replay-input.v1"] = "yieldforge.m7-replay-input.v1"
    input_id: StrictStr = Field(pattern=r"^yfm7ri-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    problem_index_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    m6_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_population_id: StrictStr = Field(pattern=r"^yftp-[0-9a-f]{24}$")
    m6_population_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    stream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    engine: M7ReplayEngineIdentity
    policy: M7PolicyIdentity
    rates: FeasibilityRateManifest
    fit_config: RemnantFitConfig
    search_config: LayoutFitSearchConfig
    event_stage_order: tuple[StrictStr, ...] = M0_EVENT_STAGE_ORDER
    problems: tuple[ReusableGeometryProblem, ...] = Field(min_length=1)
    candidate_sets: tuple[M7CandidateSetEvidence, ...] = Field(min_length=1)
    instances: tuple[TemporalInstanceBinding, ...] = Field(min_length=1)
    horizon_end: datetime
    claim_ceiling: Literal[
        "deterministic_strong_baseline_execution_only_not_policy_advantage_savings_physical_or_"
        "commercial_evidence"
    ] = (
        "deterministic_strong_baseline_execution_only_not_policy_advantage_savings_physical_or_"
        "commercial_evidence"
    )

    @field_validator("horizon_end")
    @classmethod
    def canonicalize_horizon(cls, value: datetime) -> datetime:
        return _utc(value, "M7 horizon")

    @model_validator(mode="after")
    def require_complete_stream_and_identity(self) -> Self:
        if self.event_stage_order != M0_EVENT_STAGE_ORDER:
            raise ValueError("M7 event stage order differs from M0")
        problem_ids = tuple(item.problem_id for item in self.problems)
        if problem_ids != tuple(sorted(set(problem_ids))):
            raise ValueError("M7 replay problems must use sorted unique IDs")
        candidate_problem_ids = tuple(item.problem_id for item in self.candidate_sets)
        if candidate_problem_ids != problem_ids:
            raise ValueError("M7 replay candidate sets must bind every problem in order")
        candidate_by_problem = {item.problem_id: item for item in self.candidate_sets}
        problem_by_id = {item.problem_id: item for item in self.problems}
        for problem_id, problem in problem_by_id.items():
            evidence = candidate_by_problem[problem_id]
            if evidence.problem_sha256 != problem.content_sha256:
                raise ValueError("M7 replay candidate set problem hash differs")
        sequences = tuple(item.sequence for item in self.instances)
        if sequences != tuple(range(len(self.instances))):
            raise ValueError("M7 replay instance sequences must be contiguous from zero")
        if any(
            (item.stream_id, item.stream_sha256) != (self.stream_id, self.stream_sha256)
            for item in self.instances
        ):
            raise ValueError("M7 replay instances must belong to one bound stream")
        if any(item.problem_id not in problem_by_id for item in self.instances):
            raise ValueError("M7 replay instance refers to an unbound problem")
        releases = tuple(item.released_at for item in self.instances)
        if any(right < left for left, right in zip(releases, releases[1:], strict=False)):
            raise ValueError("M7 replay instance releases cannot go backward")
        if releases[-1] >= self.horizon_end:
            raise ValueError("M7 replay horizon must follow every instance")
        stream_dimensions = {
            (item.regime, item.temporal_seed, item.partition) for item in self.instances
        }
        if len(stream_dimensions) != 1:
            raise ValueError("M7 replay instances must share one stream dimension cell")
        digest = semantic_sha256(self, excluded_fields={"input_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M7 replay input content SHA-256 does not match semantic content")
        if self.input_id != f"yfm7ri-{digest[:24]}":
            raise ValueError("M7 replay input ID does not match semantic content")
        return self


def build_m7_replay_input(
    *,
    m0_contract_id: str,
    m0_contract_sha256: str,
    problem_index_id: str,
    problem_index_sha256: str,
    m6_contract_id: str,
    m6_contract_sha256: str,
    m6_population_id: str,
    m6_population_sha256: str,
    policy: M7PolicyIdentity,
    rates: FeasibilityRateManifest,
    fit_config: RemnantFitConfig,
    problems: tuple[ReusableGeometryProblem, ...],
    candidate_sets: tuple[M7CandidateSetEvidence, ...],
    instances: tuple[TemporalInstanceBinding, ...],
    horizon_end: datetime,
    search_config: LayoutFitSearchConfig | None = None,
) -> M7ReplayInput:
    """Build one strict replay input after canonical stream ordering."""

    ordered_problems = tuple(sorted(problems, key=lambda item: item.problem_id))
    candidate_by_problem = {item.problem_id: item for item in candidate_sets}
    ordered_candidates = tuple(candidate_by_problem[item.problem_id] for item in ordered_problems)
    if not instances:
        raise ValueError("M7 replay requires at least one temporal instance")
    engine = M7ReplayEngineIdentity(shapely_version=shapely.__version__)
    search = search_config or LayoutFitSearchConfig()
    semantic = {
        "schema_version": "yieldforge.m7-replay-input.v1",
        "m0_contract_id": m0_contract_id,
        "m0_contract_sha256": m0_contract_sha256,
        "problem_index_id": problem_index_id,
        "problem_index_sha256": problem_index_sha256,
        "m6_contract_id": m6_contract_id,
        "m6_contract_sha256": m6_contract_sha256,
        "m6_population_id": m6_population_id,
        "m6_population_sha256": m6_population_sha256,
        "stream_id": instances[0].stream_id,
        "stream_sha256": instances[0].stream_sha256,
        "engine": engine.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "rates": rates.model_dump(mode="json"),
        "fit_config": fit_config.model_dump(mode="json"),
        "search_config": search.model_dump(mode="json"),
        "event_stage_order": M0_EVENT_STAGE_ORDER,
        "problems": [item.model_dump(mode="json") for item in ordered_problems],
        "candidate_sets": [item.model_dump(mode="json") for item in ordered_candidates],
        "instances": [item.model_dump(mode="json") for item in instances],
        "horizon_end": _utc(horizon_end, "M7 horizon").isoformat().replace("+00:00", "Z"),
        "claim_ceiling": (
            "deterministic_strong_baseline_execution_only_not_policy_advantage_savings_physical_"
            "or_commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M7ReplayInput(
        input_id=f"yfm7ri-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m0_contract_id=m0_contract_id,
        m0_contract_sha256=m0_contract_sha256,
        problem_index_id=problem_index_id,
        problem_index_sha256=problem_index_sha256,
        m6_contract_id=m6_contract_id,
        m6_contract_sha256=m6_contract_sha256,
        m6_population_id=m6_population_id,
        m6_population_sha256=m6_population_sha256,
        stream_id=instances[0].stream_id,
        stream_sha256=instances[0].stream_sha256,
        engine=engine,
        policy=policy,
        rates=rates,
        fit_config=fit_config,
        search_config=search,
        problems=ordered_problems,
        candidate_sets=ordered_candidates,
        instances=instances,
        horizon_end=horizon_end,
    )


class M7ReplayEvent(BaselineContractModel):
    sequence: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(pattern=r"^yfm7e-[0-9a-f]{24}$")
    binding_id: StrictStr = Field(pattern=r"^yfm7b-[0-9a-f]{24}$")
    occurred_at: datetime
    timestamp_group_sequence: StrictInt = Field(ge=0)
    timestamp_subsequence: StrictInt = Field(ge=0)
    storage_interval_start: datetime
    storage_interval_end: datetime
    inventory_before: tuple[InventoryItem, ...]
    action_set_size: StrictInt = Field(ge=1)
    standard_action_count: StrictInt = Field(ge=1)
    remnant_action_count: StrictInt = Field(ge=0)
    fit_search_query_count: StrictInt = Field(ge=0)
    fit_search_generated_candidate_count: StrictInt = Field(ge=0)
    fit_search_evaluated_candidate_count: StrictInt = Field(ge=0)
    fit_search_budget_truncated_count: StrictInt = Field(ge=0)
    policy_decision_key: tuple[StrictStr, ...] = Field(min_length=1)
    action: M7LayoutActionEvidence
    inventory_after: tuple[InventoryItem, ...]
    delta_costs: ReplayCostLedger
    cumulative_costs: ReplayCostLedger

    @field_validator("occurred_at", "storage_interval_start", "storage_interval_end")
    @classmethod
    def canonicalize_time(cls, value: datetime) -> datetime:
        return _utc(value, "M7 event time")

    @model_validator(mode="after")
    def require_transition_and_identity(self) -> Self:
        if self.storage_interval_end != self.occurred_at:
            raise ValueError("M7 event storage interval must end at occurrence")
        if self.storage_interval_start > self.storage_interval_end:
            raise ValueError("M7 event storage interval cannot run backward")
        before_ids = _inventory_ids(self.inventory_before)
        after_ids = _inventory_ids(self.inventory_after)
        if before_ids != tuple(sorted(set(before_ids))):
            raise ValueError("M7 inventory before must use sorted unique IDs")
        if after_ids != tuple(sorted(set(after_ids))):
            raise ValueError("M7 inventory after must use sorted unique IDs")
        if self.action_set_size != self.standard_action_count + self.remnant_action_count:
            raise ValueError("M7 action-set counts do not reconcile")
        digest = semantic_sha256(self, excluded_fields={"event_id"})
        if self.event_id != f"yfm7e-{digest[:24]}":
            raise ValueError("M7 replay event ID does not match semantic content")
        return self


class M7ReplaySummary(BaselineContractModel):
    instance_count: StrictInt = Field(ge=1)
    fulfilled_instance_count: StrictInt = Field(ge=0)
    timestamp_group_count: StrictInt = Field(ge=1)
    full_sheet_opening_count: StrictInt = Field(ge=0)
    remnant_retrieval_count: StrictInt = Field(ge=0)
    returned_remnant_count: StrictInt = Field(ge=0)
    terminal_remnant_count: StrictInt = Field(ge=0)
    total_action_count: StrictInt = Field(ge=0)
    total_fit_search_query_count: StrictInt = Field(ge=0)
    total_fit_search_evaluated_candidate_count: StrictInt = Field(ge=0)
    final_net_cost: StrictFloat
    technical_decision: Literal["pass", "open"]

    @model_validator(mode="after")
    def require_decision(self) -> Self:
        expected = "pass" if self.fulfilled_instance_count == self.instance_count else "open"
        if self.technical_decision != expected:
            raise ValueError("M7 technical decision does not match fulfillment")
        return self


class M7ReplayResult(BaselineContractModel):
    schema_version: Literal["yieldforge.m7-replay-result.v1"] = "yieldforge.m7-replay-result.v1"
    result_id: StrictStr = Field(pattern=r"^yfm7r-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_id: StrictStr = Field(pattern=r"^yfm7ri-[0-9a-f]{24}$")
    input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy: M7PolicyIdentity
    events: tuple[M7ReplayEvent, ...] = Field(min_length=1)
    terminal: ReplayTerminalRecord
    summary: M7ReplaySummary
    claim_ceiling: Literal[
        "deterministic_strong_baseline_execution_only_not_policy_advantage_savings_physical_or_"
        "commercial_evidence"
    ] = (
        "deterministic_strong_baseline_execution_only_not_policy_advantage_savings_physical_or_"
        "commercial_evidence"
    )

    @model_validator(mode="after")
    def require_complete_result(self) -> Self:
        if tuple(item.sequence for item in self.events) != tuple(range(len(self.events))):
            raise ValueError("M7 result event sequences must be contiguous")
        for previous, current in zip(self.events, self.events[1:], strict=False):
            if current.inventory_before != previous.inventory_after:
                raise ValueError("M7 replay inventory continuity failed")
            if current.storage_interval_start != previous.occurred_at:
                raise ValueError("M7 replay storage intervals are not continuous")
        if self.terminal.storage_interval_start != self.events[-1].occurred_at:
            raise ValueError("M7 terminal interval must follow final event")
        if self.terminal.inventory_before_liquidation != self.events[-1].inventory_after:
            raise ValueError("M7 terminal inventory differs from final event")
        if self.summary.fulfilled_instance_count != len(self.events):
            raise ValueError("M7 fulfilled count does not match event evidence")
        if self.summary.final_net_cost != self.terminal.cumulative_costs.net_cost:
            raise ValueError("M7 final cost does not match terminal ledger")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M7 replay result content SHA-256 does not match semantic content")
        if self.result_id != f"yfm7r-{digest[:24]}":
            raise ValueError("M7 replay result ID does not match semantic content")
        return self


@dataclass(frozen=True)
class GeneratedActionSet:
    actions: tuple[M7LayoutActionEvidence, ...]
    fit_search_query_count: int
    fit_search_generated_candidate_count: int
    fit_search_evaluated_candidate_count: int
    fit_search_budget_truncated_count: int


def _generate_actions(
    *,
    binding: TemporalInstanceBinding,
    problem: ReusableGeometryProblem,
    verified: VerifiedProblemCandidates,
    inventory: tuple[InventoryItem, ...],
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
) -> GeneratedActionSet:
    evidence = verified.evidence
    if (
        evidence.problem_id != problem.problem_id
        or evidence.problem_sha256 != problem.content_sha256
        or tuple(item.candidate_id for item in verified.candidates) != evidence.candidate_ids
    ):
        raise ValueError("M7 runtime candidate evidence does not match replay input")
    standard = tuple(
        build_standard_sheet_action(
            problem_id=problem.problem_id,
            problem_sha256=problem.content_sha256,
            candidate_set_id=evidence.candidate_set_id,
            candidate_set_sha256=evidence.content_sha256,
            problem=problem.problem,
            candidate=candidate,
            material=binding.material,
            stock_id=binding.binding_id,
            rules=rules,
            fit_config=fit_config,
        )
        for candidate in verified.candidates
    )
    remnant_actions = []
    query_count = 0
    generated_count = 0
    evaluated_count = 0
    truncated_count = 0
    for item in inventory:
        if material_key(item.remnant.material) != material_key(binding.material):
            continue
        for candidate in verified.candidates:
            search = search_layout_translation(
                item.remnant,
                problem.problem,
                candidate,
                material=binding.material,
                fit_config=fit_config,
                search_config=search_config,
            )
            query_count += 1
            generated_count += search.generated_candidate_count
            evaluated_count += search.evaluated_candidate_count
            truncated_count += int(search.budget_truncated)
            action = build_remnant_action_from_search(
                problem_id=problem.problem_id,
                problem_sha256=problem.content_sha256,
                candidate_set_id=evidence.candidate_set_id,
                candidate_set_sha256=evidence.content_sha256,
                problem=problem.problem,
                candidate=candidate,
                remnant=item.remnant,
                material=binding.material,
                rules=rules,
                fit_config=fit_config,
                search_result=search,
            )
            if action is not None:
                remnant_actions.append(action)
    actions = tuple(sorted(standard + tuple(remnant_actions), key=lambda item: item.action_id))
    if not actions:
        raise ValueError("M7 instance has no valid action")
    if len({item.action_id for item in actions}) != len(actions):
        raise ValueError("M7 action set contains duplicate identities")
    return GeneratedActionSet(
        actions=actions,
        fit_search_query_count=query_count,
        fit_search_generated_candidate_count=generated_count,
        fit_search_evaluated_candidate_count=evaluated_count,
        fit_search_budget_truncated_count=truncated_count,
    )


def _returned_regularity(action: M7LayoutActionEvidence) -> float:
    if not action.returned_remnants:
        return 0.0
    area = sum(item.geometry.area for item in action.returned_remnants)
    rectangle_area = sum(
        _minimum_rotated_rectangle_area(polygon_from_record(item.geometry))
        for item in action.returned_remnants
    )
    if rectangle_area <= 0.0:
        return 0.0
    return min(1.0, max(0.0, area / rectangle_area))


def _minimum_rotated_rectangle_area(geometry) -> float:  # type: ignore[no-untyped-def]
    """Compute the convex-hull rotating-box area without GEOS oriented-envelope warnings."""

    hull = geometry.convex_hull
    if hull.geom_type != "Polygon" or hull.area <= 0.0:
        return 0.0
    coordinates = tuple(hull.exterior.coords)
    best = math.inf
    for left, right in zip(coordinates, coordinates[1:], strict=False):
        delta_x = right[0] - left[0]
        delta_y = right[1] - left[1]
        if delta_x == 0.0 and delta_y == 0.0:
            continue
        aligned = rotate(
            hull,
            -math.atan2(delta_y, delta_x),
            origin=(0.0, 0.0),
            use_radians=True,
        )
        min_x, min_y, max_x, max_y = aligned.bounds
        best = min(best, (max_x - min_x) * (max_y - min_y))
    if not math.isfinite(best) or best <= 0.0:
        return 0.0
    return float(best)


def _policy_contexts(
    generated: GeneratedActionSet,
    *,
    candidates: tuple,  # type: ignore[type-arg]
    inventory: tuple[InventoryItem, ...],
    occurred_at: datetime,
    rates: FeasibilityRateManifest,
) -> tuple[ActionPolicyContext, ...]:
    candidate_width = {item.candidate_id: item.width for item in candidates}
    inventory_by_id = {item.remnant.remnant_id: item for item in inventory}
    contexts = []
    for action in generated.actions:
        is_sheet = action.kind is M7ActionKind.OPEN_STANDARD_SHEET
        retained_area = sum(item.geometry.area for item in action.returned_remnants)
        immediate = rounded_cost(
            (
                action.selected_stock.geometry.area * rates.purchase_cost_per_area
                if is_sheet
                else 0.0
            )
            + len(action.returned_remnants) * rates.return_handling_cost_per_remnant
            + (0.0 if is_sheet else rates.retrieval_handling_cost_per_remnant)
            + retained_area * rates.storage_cost_per_area_hour
            - action.accounting.scrap_area * rates.scrap_credit_per_area
        )
        age_hours = 0.0
        if action.selected_remnant_id is not None:
            selected = inventory_by_id.get(action.selected_remnant_id)
            if selected is None:
                raise ValueError("M7 action selected a remnant absent from inventory")
            age_hours = (occurred_at - selected.entered_at).total_seconds() / 3600.0
            if age_hours < 0:
                raise ValueError("M7 selected remnant cannot have a future inventory entry")
        contexts.append(
            ActionPolicyContext(
                action_id=action.action_id,
                kind=action.kind,
                candidate_id=action.candidate_id,
                candidate_width=candidate_width[action.candidate_id],
                selected_stock_id=action.selected_stock.remnant_id,
                immediate_net_cost=immediate,
                selected_remnant_age_hours=age_hours,
                returned_regularity=_returned_regularity(action),
                known_order_lookahead_term=0.0,
            )
        )
    return tuple(contexts)


def _execute_action(
    action: M7LayoutActionEvidence,
    inventory: tuple[InventoryItem, ...],
    occurred_at: datetime,
) -> tuple[InventoryItem, ...]:
    inventory_by_id = {item.remnant.remnant_id: item for item in inventory}
    if action.kind is M7ActionKind.CONSUME_REMNANT:
        selected_id = action.selected_remnant_id
        if selected_id is None or selected_id not in inventory_by_id:
            raise ValueError("M7 selected remnant is missing from inventory")
        remaining = tuple(item for item in inventory if item.remnant.remnant_id != selected_id)
    else:
        remaining = inventory
    additions = tuple(
        InventoryItem(remnant=item, entered_at=occurred_at) for item in action.returned_remnants
    )
    result = tuple(sorted(remaining + additions, key=lambda item: item.remnant.remnant_id))
    if len(_inventory_ids(result)) != len(set(_inventory_ids(result))):
        raise ValueError("M7 inventory transition created duplicate remnant identities")
    return result


def _event(
    *,
    sequence: int,
    binding: TemporalInstanceBinding,
    group_sequence: int,
    group_subsequence: int,
    storage_interval_start: datetime,
    inventory_before: tuple[InventoryItem, ...],
    generated: GeneratedActionSet,
    decision_key: tuple[str, ...],
    action: M7LayoutActionEvidence,
    inventory_after: tuple[InventoryItem, ...],
    delta: ReplayCostLedger,
    cumulative: ReplayCostLedger,
) -> M7ReplayEvent:
    payload = {
        "sequence": sequence,
        "binding_id": binding.binding_id,
        "occurred_at": binding.released_at,
        "timestamp_group_sequence": group_sequence,
        "timestamp_subsequence": group_subsequence,
        "storage_interval_start": storage_interval_start,
        "storage_interval_end": binding.released_at,
        "inventory_before": inventory_before,
        "action_set_size": len(generated.actions),
        "standard_action_count": sum(
            item.kind is M7ActionKind.OPEN_STANDARD_SHEET for item in generated.actions
        ),
        "remnant_action_count": sum(
            item.kind is M7ActionKind.CONSUME_REMNANT for item in generated.actions
        ),
        "fit_search_query_count": generated.fit_search_query_count,
        "fit_search_generated_candidate_count": (generated.fit_search_generated_candidate_count),
        "fit_search_evaluated_candidate_count": (generated.fit_search_evaluated_candidate_count),
        "fit_search_budget_truncated_count": generated.fit_search_budget_truncated_count,
        "policy_decision_key": decision_key,
        "action": action,
        "inventory_after": inventory_after,
        "delta_costs": delta,
        "cumulative_costs": cumulative,
    }
    provisional = M7ReplayEvent.model_construct(event_id="yfm7e-" + "0" * 24, **payload)
    digest = semantic_sha256(provisional, excluded_fields={"event_id"})
    return M7ReplayEvent(event_id=f"yfm7e-{digest[:24]}", **payload)


def _result(
    replay_input: M7ReplayInput,
    events: tuple[M7ReplayEvent, ...],
    terminal: ReplayTerminalRecord,
    summary: M7ReplaySummary,
) -> M7ReplayResult:
    semantic = {
        "schema_version": "yieldforge.m7-replay-result.v1",
        "input_id": replay_input.input_id,
        "input_sha256": replay_input.content_sha256,
        "policy": replay_input.policy.model_dump(mode="json"),
        "events": [item.model_dump(mode="json") for item in events],
        "terminal": terminal.model_dump(mode="json"),
        "summary": summary.model_dump(mode="json"),
        "claim_ceiling": replay_input.claim_ceiling,
    }
    digest = semantic_sha256(semantic)
    return M7ReplayResult(
        result_id=f"yfm7r-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        input_id=replay_input.input_id,
        input_sha256=replay_input.content_sha256,
        policy=replay_input.policy,
        events=events,
        terminal=terminal,
        summary=summary,
    )


def run_m7_replay(
    replay_input: M7ReplayInput,
    runtime_candidates: dict[str, VerifiedProblemCandidates],
    rules: ResidualRuleSet,
) -> M7ReplayResult:
    """Execute one M7 stream deterministically with exact shared action generation."""

    problem_by_id = {item.problem_id: item for item in replay_input.problems}
    expected_evidence = {item.problem_id: item for item in replay_input.candidate_sets}
    if set(runtime_candidates) != set(expected_evidence) or any(
        runtime_candidates[key].evidence != expected_evidence[key] for key in expected_evidence
    ):
        raise ValueError("M7 runtime candidate sets differ from replay input")

    current_time = replay_input.instances[0].released_at
    inventory: tuple[InventoryItem, ...] = ()
    cumulative = ReplayCostLedger.zero()
    events = []
    group_sequence = -1
    group_subsequence = 0
    previous_release: datetime | None = None

    for sequence, binding in enumerate(replay_input.instances):
        if binding.released_at != previous_release:
            group_sequence += 1
            group_subsequence = 0
        else:
            group_subsequence += 1
        inventory_before = inventory
        storage = _storage_cost(
            inventory_before,
            start=current_time,
            end=binding.released_at,
            rate=replay_input.rates.storage_cost_per_area_hour,
        )
        problem = problem_by_id[binding.problem_id]
        verified = runtime_candidates[binding.problem_id]
        generated = _generate_actions(
            binding=binding,
            problem=problem,
            verified=verified,
            inventory=inventory_before,
            rules=rules,
            fit_config=replay_input.fit_config,
            search_config=replay_input.search_config,
        )
        contexts = _policy_contexts(
            generated,
            candidates=verified.candidates,
            inventory=inventory_before,
            occurred_at=binding.released_at,
            rates=replay_input.rates,
        )
        selection = select_policy_action(replay_input.policy.name, contexts)
        action_by_id = {item.action_id: item for item in generated.actions}
        action = action_by_id[selection.action_id]
        inventory = _execute_action(action, inventory_before, binding.released_at)
        delta = _ledger(
            purchase_cost=(
                action.selected_stock.geometry.area * replay_input.rates.purchase_cost_per_area
                if action.kind is M7ActionKind.OPEN_STANDARD_SHEET
                else 0.0
            ),
            storage_cost=storage,
            return_handling_cost=(
                len(action.returned_remnants) * replay_input.rates.return_handling_cost_per_remnant
            ),
            retrieval_handling_cost=(
                replay_input.rates.retrieval_handling_cost_per_remnant
                if action.kind is M7ActionKind.CONSUME_REMNANT
                else 0.0
            ),
            scrap_proceeds=action.accounting.scrap_area * replay_input.rates.scrap_credit_per_area,
        )
        cumulative = _add_ledgers(cumulative, delta)
        events.append(
            _event(
                sequence=sequence,
                binding=binding,
                group_sequence=group_sequence,
                group_subsequence=group_subsequence,
                storage_interval_start=current_time,
                inventory_before=inventory_before,
                generated=generated,
                decision_key=selection.decision_key,
                action=action,
                inventory_after=inventory,
                delta=delta,
                cumulative=cumulative,
            )
        )
        current_time = binding.released_at
        previous_release = binding.released_at

    terminal_storage = _storage_cost(
        inventory,
        start=current_time,
        end=replay_input.horizon_end,
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
    cumulative = _add_ledgers(cumulative, terminal_delta)
    terminal = ReplayTerminalRecord(
        horizon_end=replay_input.horizon_end,
        storage_interval_start=current_time,
        inventory_before_liquidation=inventory,
        liquidated_remnant_ids=_inventory_ids(inventory),
        delta_costs=terminal_delta,
        cumulative_costs=cumulative,
    )
    event_tuple = tuple(events)
    summary = M7ReplaySummary(
        instance_count=len(replay_input.instances),
        fulfilled_instance_count=len(event_tuple),
        timestamp_group_count=group_sequence + 1,
        full_sheet_opening_count=sum(
            item.action.kind is M7ActionKind.OPEN_STANDARD_SHEET for item in event_tuple
        ),
        remnant_retrieval_count=sum(
            item.action.kind is M7ActionKind.CONSUME_REMNANT for item in event_tuple
        ),
        returned_remnant_count=sum(len(item.action.returned_remnants) for item in event_tuple),
        terminal_remnant_count=len(inventory),
        total_action_count=sum(item.action_set_size for item in event_tuple),
        total_fit_search_query_count=sum(item.fit_search_query_count for item in event_tuple),
        total_fit_search_evaluated_candidate_count=sum(
            item.fit_search_evaluated_candidate_count for item in event_tuple
        ),
        final_net_cost=cumulative.net_cost,
        technical_decision="pass",
    )
    return _result(replay_input, event_tuple, terminal, summary)


__all__ = [
    "M7ReplayEvent",
    "M7ReplayInput",
    "M7ReplayResult",
    "M7ReplaySummary",
    "build_m7_replay_input",
    "run_m7_replay",
]
