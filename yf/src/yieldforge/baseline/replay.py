"""Deterministic chronological replay for exact M7 complete-layout actions."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
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
    LayoutFitSearchResult,
    LayoutFitSearchStatus,
    M7ActionKind,
    M7CandidateSetEvidence,
    M7LayoutActionEvidence,
    ReusableGeometryProblem,
    TemporalInstanceBinding,
)
from yieldforge.baseline.geometry import (
    PreparedLayoutFootprint,
    generate_layout_translations,
    prepare_layout_footprint,
    prepare_remnant_geometry,
    search_layout_translation,
)
from yieldforge.baseline.jagua import (
    JaguaPrefilterResult,
    JaguaRepresentationError,
    run_jagua_generated_prefilter,
    run_jagua_prefilter,
)
from yieldforge.baseline.policies import (
    ActionPolicyContext,
    M7PolicyIdentity,
    M7PolicyName,
    select_policy_action,
)
from yieldforge.domain import Candidate
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.replay.contracts import (
    M0_EVENT_STAGE_ORDER,
    InventoryItem,
    ReplayCostLedger,
    ReplayTerminalRecord,
    rounded_cost,
)
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.reuse.contracts import RemnantFitConfig, ReuseAccounting, polygon_from_record
from yieldforge.reuse.geometry import material_key
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest

type M7SharedFitSearchCache = dict[tuple[str, str, str], tuple[LayoutFitSearchResult, ...]]
type M7PreparedLayoutCache = OrderedDict[tuple[str, str], tuple[PreparedLayoutFootprint, ...]]

_MAX_PREPARED_LAYOUT_CACHE_PROBLEMS = 2
_REMNANT_ACTION_MATERIALIZATION_BATCH_SIZE = 64


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
    version: Literal["1.0.0", "1.0.1"] = "1.0.1"
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
    collision_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ] = "shapely_authoritative"
    jagua_container_guard: StrictFloat | None = None
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
        if (self.collision_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness") != (
            self.jagua_container_guard is not None
        ):
            raise ValueError("M7 Jagua backend and container guard must appear together")
        if self.jagua_container_guard is not None and self.jagua_container_guard <= 0:
            raise ValueError("M7 Jagua container guard must be positive")
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
    collision_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ] = "shapely_authoritative",
    jagua_container_guard: float | None = None,
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
        "collision_backend": collision_backend,
        "jagua_container_guard": jagua_container_guard,
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
        collision_backend=collision_backend,
        jagua_container_guard=jagua_container_guard,
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
    standard_profiles: tuple[M7StandardActionProfile, ...]
    remnant_actions: tuple[M7LayoutActionEvidence, ...]
    remnant_action_count: int
    fit_search_query_count: int
    fit_search_generated_candidate_count: int
    fit_search_evaluated_candidate_count: int
    fit_search_budget_truncated_count: int


@dataclass
class M7ReplayRuntimeMetrics:
    """Non-persisted wall-clock observations; never part of deterministic identities."""

    replay_elapsed_seconds: float = 0.0
    standard_action_seconds: float = 0.0
    fit_search_seconds: float = 0.0
    remnant_action_materialization_seconds: float = 0.0
    remnant_action_materialization_peak_batch_size: int = 0
    remnant_action_peak_retained_count: int = 0
    jagua_wall_seconds: float = 0.0
    jagua_guarded_query_count: int = 0
    jagua_rejection_count: int = 0
    jagua_audit_search_count: int = 0
    jagua_audit_mismatch_count: int = 0
    jagua_accelerated_search_seconds: float = 0.0
    jagua_authoritative_audit_seconds: float = 0.0
    jagua_translation_generation_seconds: float = 0.0
    jagua_accelerated_evaluation_seconds: float = 0.0
    jagua_representation_fallback_count: int = 0
    fit_search_cache_hit_count: int = 0
    fit_search_cache_miss_count: int = 0


@dataclass(frozen=True)
class M7StandardActionProfile:
    """Stock-independent exact score terms cached for one standard-sheet layout."""

    candidate_id: str
    candidate_width: float
    accounting: ReuseAccounting
    returned_remnant_count: int
    returned_regularity: float


@dataclass(frozen=True)
class M7ReplayCursor:
    """Exact M7 state immediately before ``next_event_position``."""

    next_event_position: int
    current_time: datetime
    inventory: tuple[InventoryItem, ...]
    cumulative_costs: ReplayCostLedger
    timestamp_group_sequence: int
    timestamp_subsequence: int
    previous_release: datetime | None


@dataclass(frozen=True)
class M7ActionDescriptor:
    """Lazy standard action or exact feasible remnant action at one M7 event."""

    action_id: str
    kind: M7ActionKind
    candidate_id: str
    selected_remnant_id: str | None
    evidence: M7LayoutActionEvidence | None = None


@dataclass(frozen=True)
class M7ActionCatalog:
    """Complete current M7 action set and policy-visible terms."""

    event_position: int
    actions: tuple[M7ActionDescriptor, ...]
    contexts: tuple[ActionPolicyContext, ...]
    standard_action_count: int
    remnant_action_count: int
    storage_cost: float
    timestamp_group_sequence: int
    timestamp_subsequence: int
    generated: GeneratedActionSet


@dataclass(frozen=True)
class M7StepResult:
    descriptor: M7ActionDescriptor
    event: M7ReplayEvent
    cursor: M7ReplayCursor


@dataclass(frozen=True)
class M7ContinuationResult:
    events: tuple[M7ReplayEvent, ...]
    terminal: ReplayTerminalRecord
    final_costs: ReplayCostLedger


@dataclass
class M7ReplayRuntime:
    """Runtime-only dependencies and caches for exact arbitrary-state replay."""

    replay_input: M7ReplayInput
    runtime_candidates: dict[str, VerifiedProblemCandidates]
    rules: ResidualRuleSet
    runtime_metrics: M7ReplayRuntimeMetrics | None = None
    standard_profile_cache: dict[tuple[str, str], M7StandardActionProfile] = dataclass_field(
        default_factory=dict
    )
    fit_search_cache: dict[
        tuple[str, str, str], tuple[LayoutFitSearchResult, ...]
    ] = dataclass_field(default_factory=dict)
    shared_fit_search_cache: M7SharedFitSearchCache | None = None
    prepared_layout_cache: M7PreparedLayoutCache = dataclass_field(default_factory=OrderedDict)
    standard_profile_executor: Executor | None = None
    jagua_executable: Path | None = None
    jagua_differential_audit: bool = False


def _build_standard_profile(
    problem: ReusableGeometryProblem,
    evidence: M7CandidateSetEvidence,
    candidate: Candidate,
    material,  # type: ignore[no-untyped-def]
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
) -> M7StandardActionProfile:
    template = build_standard_sheet_action(
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        candidate_set_id=evidence.candidate_set_id,
        candidate_set_sha256=evidence.content_sha256,
        problem=problem.problem,
        candidate=candidate,
        material=material,
        stock_id=f"m7-profile-{problem.problem_id}",
        rules=rules,
        fit_config=fit_config,
    )
    return M7StandardActionProfile(
        candidate_id=candidate.candidate_id,
        candidate_width=candidate.width,
        accounting=template.accounting,
        returned_remnant_count=len(template.returned_remnants),
        returned_regularity=_returned_regularity(template),
    )


def _generate_actions(
    *,
    binding: TemporalInstanceBinding,
    problem: ReusableGeometryProblem,
    verified: VerifiedProblemCandidates,
    inventory: tuple[InventoryItem, ...],
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
    policy: M7PolicyName,
    rates: FeasibilityRateManifest,
    runtime_metrics: M7ReplayRuntimeMetrics | None,
    standard_profile_cache: dict[tuple[str, str], M7StandardActionProfile],
    standard_profile_executor: Executor | None,
    jagua_executable: Path | None,
    jagua_container_guard: float,
    jagua_differential_audit: bool,
    fit_search_cache: dict[tuple[str, str, str], tuple[LayoutFitSearchResult, ...]],
    shared_fit_search_cache: M7SharedFitSearchCache | None,
    prepared_layout_cache: M7PreparedLayoutCache,
    retain_all_remnant_actions: bool = False,
) -> GeneratedActionSet:
    evidence = verified.evidence
    if (
        evidence.problem_id != problem.problem_id
        or evidence.problem_sha256 != problem.content_sha256
        or tuple(item.candidate_id for item in verified.candidates) != evidence.candidate_ids
    ):
        raise ValueError("M7 runtime candidate evidence does not match replay input")
    standard_started = perf_counter()
    missing = tuple(
        candidate
        for candidate in verified.candidates
        if (problem.problem_id, candidate.candidate_id) not in standard_profile_cache
    )
    profile_arguments = tuple(
        (problem, evidence, candidate, binding.material, rules, fit_config) for candidate in missing
    )
    if standard_profile_executor is None:
        built = tuple(_build_standard_profile(*arguments) for arguments in profile_arguments)
    else:
        built = tuple(
            standard_profile_executor.map(
                _build_standard_profile_from_arguments,
                profile_arguments,
            )
        )
    for profile in built:
        standard_profile_cache[(problem.problem_id, profile.candidate_id)] = profile
    profiles = tuple(
        standard_profile_cache[(problem.problem_id, candidate.candidate_id)]
        for candidate in verified.candidates
    )
    if runtime_metrics is not None:
        runtime_metrics.standard_action_seconds += perf_counter() - standard_started
    remnant_action_arguments = []
    query_count = 0
    generated_count = 0
    evaluated_count = 0
    truncated_count = 0
    compatible_inventory = tuple(
        item
        for item in inventory
        if material_key(item.remnant.material) == material_key(binding.material)
    )
    prepared_layouts: tuple[PreparedLayoutFootprint, ...] = ()
    if compatible_inventory:
        prepared_key = (problem.problem_id, evidence.candidate_set_id)
        cached_layouts = prepared_layout_cache.get(prepared_key)
        if cached_layouts is None:
            layout_arguments = tuple(
                (problem.problem, candidate, fit_config) for candidate in verified.candidates
            )
            if standard_profile_executor is None:
                cached_layouts = tuple(
                    _prepare_layout_from_arguments(arguments) for arguments in layout_arguments
                )
            else:
                cached_layouts = tuple(
                    standard_profile_executor.map(
                        _prepare_layout_from_arguments,
                        layout_arguments,
                    )
                )
            prepared_layout_cache[prepared_key] = cached_layouts
            while len(prepared_layout_cache) > _MAX_PREPARED_LAYOUT_CACHE_PROBLEMS:
                prepared_layout_cache.popitem(last=False)
        else:
            prepared_layout_cache.move_to_end(prepared_key)
        prepared_layouts = cached_layouts

    for item in compatible_inventory:
        cache_key = (item.remnant.remnant_id, problem.problem_id, evidence.candidate_set_id)
        searches = fit_search_cache.get(cache_key)
        cache_hit_recorded = False
        shared_cache_key = (
            semantic_sha256(
                {
                    "geometry": item.remnant.geometry.model_dump(mode="json"),
                    "fit_config": fit_config.model_dump(mode="json"),
                    "search_config": search_config.model_dump(mode="json"),
                }
            ),
            problem.problem_id,
            evidence.candidate_set_id,
        )
        if searches is None and shared_fit_search_cache is not None:
            cached_shared = shared_fit_search_cache.get(shared_cache_key)
            if cached_shared is not None:
                expected_candidate_ids = tuple(
                    candidate.candidate_id for candidate in verified.candidates
                )
                if tuple(
                    search.candidate_id for search in cached_shared
                ) != expected_candidate_ids or any(
                    search.config != search_config for search in cached_shared
                ):
                    raise ValueError("M7 shared geometry-search cache differs from current search")
                searches = tuple(
                    search.model_copy(update={"remnant_id": item.remnant.remnant_id})
                    for search in cached_shared
                )
                fit_search_cache[cache_key] = searches
                if runtime_metrics is not None:
                    runtime_metrics.fit_search_cache_hit_count += 1
                    cache_hit_recorded = True
        if searches is None:
            if runtime_metrics is not None:
                runtime_metrics.fit_search_cache_miss_count += 1
            search_started = perf_counter()
            if standard_profile_executor is None:
                searches, jagua_metrics = _search_candidate_chunk(
                    (
                        item.remnant,
                        problem.problem,
                        verified.candidates,
                        binding.material,
                        fit_config,
                        search_config,
                        prepared_layouts,
                        jagua_executable,
                        jagua_container_guard,
                        jagua_differential_audit,
                    )
                )
            else:
                chunk_size = max(1, math.ceil(len(verified.candidates) / 32))
                chunk_offsets = tuple(range(0, len(verified.candidates), chunk_size))
                chunks = tuple(
                    verified.candidates[offset : offset + chunk_size] for offset in chunk_offsets
                )
                arguments = tuple(
                    (
                        item.remnant,
                        problem.problem,
                        chunk,
                        binding.material,
                        fit_config,
                        search_config,
                        prepared_layouts[offset : offset + len(chunk)],
                        jagua_executable,
                        jagua_container_guard,
                        jagua_differential_audit,
                    )
                    for offset, chunk in zip(chunk_offsets, chunks, strict=True)
                )
                chunk_results = tuple(
                    standard_profile_executor.map(
                        _search_candidate_chunk,
                        arguments,
                    )
                )
                searches = tuple(
                    search for result_chunk, _metrics in chunk_results for search in result_chunk
                )
                jagua_metrics = _merge_jagua_metrics(
                    tuple(metrics for _searches, metrics in chunk_results)
                )
            fit_search_cache[cache_key] = searches
            if shared_fit_search_cache is not None:
                shared_fit_search_cache[shared_cache_key] = searches
            if runtime_metrics is not None:
                runtime_metrics.fit_search_seconds += perf_counter() - search_started
                runtime_metrics.jagua_wall_seconds += jagua_metrics.wall_seconds
                runtime_metrics.jagua_guarded_query_count += jagua_metrics.guarded_query_count
                runtime_metrics.jagua_rejection_count += jagua_metrics.rejection_count
                runtime_metrics.jagua_audit_search_count += jagua_metrics.audit_search_count
                runtime_metrics.jagua_audit_mismatch_count += jagua_metrics.audit_mismatch_count
                runtime_metrics.jagua_accelerated_search_seconds += (
                    jagua_metrics.accelerated_search_seconds
                )
                runtime_metrics.jagua_authoritative_audit_seconds += (
                    jagua_metrics.authoritative_audit_seconds
                )
                runtime_metrics.jagua_translation_generation_seconds += (
                    jagua_metrics.translation_generation_seconds
                )
                runtime_metrics.jagua_accelerated_evaluation_seconds += (
                    jagua_metrics.accelerated_evaluation_seconds
                )
                runtime_metrics.jagua_representation_fallback_count += (
                    jagua_metrics.representation_fallback_count
                )
        elif runtime_metrics is not None and not cache_hit_recorded:
            runtime_metrics.fit_search_cache_hit_count += 1
        for candidate, search in zip(verified.candidates, searches, strict=True):
            query_count += 1
            generated_count += search.generated_candidate_count
            evaluated_count += search.evaluated_candidate_count
            truncated_count += int(search.budget_truncated)
            if search.status is LayoutFitSearchStatus.FIT:
                remnant_action_arguments.append(
                    (
                        problem.problem_id,
                        problem.content_sha256,
                        evidence.candidate_set_id,
                        evidence.content_sha256,
                        problem.problem,
                        candidate,
                        item.remnant,
                        binding.material,
                        rules,
                        fit_config,
                        search,
                    )
                )
    materialization_started = perf_counter()
    if retain_all_remnant_actions:
        materialized_actions = []
        materialized_action_ids: set[str] = set()
        for offset in range(
            0,
            len(remnant_action_arguments),
            _REMNANT_ACTION_MATERIALIZATION_BATCH_SIZE,
        ):
            batch = remnant_action_arguments[
                offset : offset + _REMNANT_ACTION_MATERIALIZATION_BATCH_SIZE
            ]
            if runtime_metrics is not None:
                runtime_metrics.remnant_action_materialization_peak_batch_size = max(
                    runtime_metrics.remnant_action_materialization_peak_batch_size,
                    len(batch),
                )
            if standard_profile_executor is None:
                built_batch = map(_build_remnant_action_from_arguments, batch)
            else:
                built_batch = standard_profile_executor.map(
                    _build_remnant_action_from_arguments,
                    batch,
                )
            for action in built_batch:
                if action is None:
                    raise ValueError("M7 fit witness did not materialize an exact remnant action")
                if action.action_id in materialized_action_ids:
                    raise ValueError("M7 action set contains duplicate identities")
                materialized_action_ids.add(action.action_id)
                materialized_actions.append(action)
        remnant_actions = tuple(materialized_actions)
        if runtime_metrics is not None:
            runtime_metrics.remnant_action_peak_retained_count = max(
                runtime_metrics.remnant_action_peak_retained_count,
                len(remnant_actions),
            )
    elif policy is M7PolicyName.MYOPIC_GEOMETRY:
        best_standard_key = min(
            (
                profile.candidate_width,
                profile.candidate_id,
                "current_standard_sheet",
            )
            for profile in profiles
        )
        if remnant_action_arguments:
            best_remnant_key = min(
                (
                    arguments[5].width,
                    arguments[5].candidate_id,
                    arguments[6].remnant_id,
                )
                for arguments in remnant_action_arguments
            )
            if best_remnant_key < best_standard_key:
                raise ValueError("M7 myopic lazy-materialization ordering invariant failed")
        remnant_actions = ()
    else:
        candidate_widths = {item.candidate_id: item.width for item in verified.candidates}
        inventory_by_id = {item.remnant.remnant_id: item for item in inventory}
        selected_remnant_action = None
        selected_remnant_context = None
        materialized_action_ids: set[str] = set()
        for offset in range(
            0,
            len(remnant_action_arguments),
            _REMNANT_ACTION_MATERIALIZATION_BATCH_SIZE,
        ):
            batch = remnant_action_arguments[
                offset : offset + _REMNANT_ACTION_MATERIALIZATION_BATCH_SIZE
            ]
            if runtime_metrics is not None:
                runtime_metrics.remnant_action_materialization_peak_batch_size = max(
                    runtime_metrics.remnant_action_materialization_peak_batch_size,
                    len(batch),
                )
            if standard_profile_executor is None:
                built_batch = map(_build_remnant_action_from_arguments, batch)
            else:
                built_batch = standard_profile_executor.map(
                    _build_remnant_action_from_arguments,
                    batch,
                )
            for action in built_batch:
                if action is None:
                    raise ValueError("M7 fit witness did not materialize an exact remnant action")
                if action.action_id in materialized_action_ids:
                    raise ValueError("M7 action set contains duplicate identities")
                materialized_action_ids.add(action.action_id)
                context = _remnant_policy_context(
                    action,
                    candidate_widths=candidate_widths,
                    inventory_by_id=inventory_by_id,
                    occurred_at=binding.released_at,
                    rates=rates,
                )
                if selected_remnant_context is None:
                    selected_remnant_action = action
                    selected_remnant_context = context
                else:
                    selection = select_policy_action(
                        policy,
                        (selected_remnant_context, context),
                    )
                    if selection.action_id == context.action_id:
                        selected_remnant_action = action
                        selected_remnant_context = context
                if runtime_metrics is not None:
                    runtime_metrics.remnant_action_peak_retained_count = max(
                        runtime_metrics.remnant_action_peak_retained_count,
                        int(selected_remnant_action is not None),
                    )
        remnant_actions = (
            (selected_remnant_action,) if selected_remnant_action is not None else ()
        )
    if (
        not retain_all_remnant_actions
        and policy is not M7PolicyName.MYOPIC_GEOMETRY
        and bool(remnant_actions) != bool(remnant_action_arguments)
    ):
        raise ValueError("M7 fit witness did not produce an exact policy winner")
    if runtime_metrics is not None:
        runtime_metrics.remnant_action_materialization_seconds += (
            perf_counter() - materialization_started
        )
    ordered_remnant = tuple(sorted(remnant_actions, key=lambda item: item.action_id))
    if not profiles and not ordered_remnant:
        raise ValueError("M7 instance has no valid action")
    if len({item.action_id for item in ordered_remnant}) != len(ordered_remnant):
        raise ValueError("M7 action set contains duplicate identities")
    return GeneratedActionSet(
        standard_profiles=profiles,
        remnant_actions=ordered_remnant,
        remnant_action_count=len(remnant_action_arguments),
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


def _remnant_policy_context(
    action: M7LayoutActionEvidence,
    *,
    candidate_widths: dict[str, float],
    inventory_by_id: dict[str, InventoryItem],
    occurred_at: datetime,
    rates: FeasibilityRateManifest,
) -> ActionPolicyContext:
    retained_area = sum(item.geometry.area for item in action.returned_remnants)
    immediate = rounded_cost(
        len(action.returned_remnants) * rates.return_handling_cost_per_remnant
        + rates.retrieval_handling_cost_per_remnant
        + retained_area * rates.storage_cost_per_area_hour
        - action.accounting.scrap_area * rates.scrap_credit_per_area
    )
    if action.selected_remnant_id is None:
        raise ValueError("M7 generated remnant action has no selected remnant")
    selected = inventory_by_id.get(action.selected_remnant_id)
    if selected is None:
        raise ValueError("M7 action selected a remnant absent from inventory")
    age_hours = (occurred_at - selected.entered_at).total_seconds() / 3600.0
    if age_hours < 0:
        raise ValueError("M7 selected remnant cannot have a future inventory entry")
    return ActionPolicyContext(
        action_id=action.action_id,
        kind=action.kind,
        candidate_id=action.candidate_id,
        candidate_width=candidate_widths[action.candidate_id],
        selected_stock_id=action.selected_stock.remnant_id,
        immediate_net_cost=immediate,
        selected_remnant_age_hours=age_hours,
        returned_regularity=_returned_regularity(action),
        known_order_lookahead_term=0.0,
    )


def _build_standard_profile_from_arguments(arguments) -> M7StandardActionProfile:  # type: ignore[no-untyped-def]
    return _build_standard_profile(*arguments)


def _prepare_layout_from_arguments(arguments) -> PreparedLayoutFootprint:  # type: ignore[no-untyped-def]
    return prepare_layout_footprint(*arguments)


def _build_remnant_action_from_arguments(arguments):  # type: ignore[no-untyped-def]
    (
        problem_id,
        problem_sha256,
        candidate_set_id,
        candidate_set_sha256,
        problem,
        candidate,
        remnant,
        material,
        rules,
        fit_config,
        search_result,
    ) = arguments
    return build_remnant_action_from_search(
        problem_id=problem_id,
        problem_sha256=problem_sha256,
        candidate_set_id=candidate_set_id,
        candidate_set_sha256=candidate_set_sha256,
        problem=problem,
        candidate=candidate,
        remnant=remnant,
        material=material,
        rules=rules,
        fit_config=fit_config,
        search_result=search_result,
    )


@dataclass(frozen=True)
class _JaguaChunkMetrics:
    wall_seconds: float = 0.0
    guarded_query_count: int = 0
    rejection_count: int = 0
    audit_search_count: int = 0
    audit_mismatch_count: int = 0
    accelerated_search_seconds: float = 0.0
    authoritative_audit_seconds: float = 0.0
    translation_generation_seconds: float = 0.0
    accelerated_evaluation_seconds: float = 0.0
    representation_fallback_count: int = 0


def _merge_jagua_metrics(values: tuple[_JaguaChunkMetrics, ...]) -> _JaguaChunkMetrics:
    return _JaguaChunkMetrics(
        wall_seconds=sum(item.wall_seconds for item in values),
        guarded_query_count=sum(item.guarded_query_count for item in values),
        rejection_count=sum(item.rejection_count for item in values),
        audit_search_count=sum(item.audit_search_count for item in values),
        audit_mismatch_count=sum(item.audit_mismatch_count for item in values),
        accelerated_search_seconds=sum(item.accelerated_search_seconds for item in values),
        authoritative_audit_seconds=sum(item.authoritative_audit_seconds for item in values),
        translation_generation_seconds=sum(item.translation_generation_seconds for item in values),
        accelerated_evaluation_seconds=sum(item.accelerated_evaluation_seconds for item in values),
        representation_fallback_count=sum(
            item.representation_fallback_count for item in values
        ),
    )


def _search_candidate_chunk(  # type: ignore[no-untyped-def]
    arguments,
) -> tuple[tuple[LayoutFitSearchResult, ...], _JaguaChunkMetrics]:
    (
        remnant,
        problem,
        candidates,
        material,
        fit_config,
        search_config,
        prepared_layouts,
        jagua_executable,
        jagua_container_guard,
        jagua_differential_audit,
    ) = arguments
    prepared_remnant = prepare_remnant_geometry(remnant)
    rust_generated = jagua_executable is not None and not any(
        polygon.interiors for layout in prepared_layouts for polygon in layout.part_polygons
    )
    representation_fallback_count = 0
    if rust_generated:
        evaluation_started = perf_counter()
        try:
            prefilter = run_jagua_generated_prefilter(
                jagua_executable,
                remnant=prepared_remnant,
                layouts=prepared_layouts,
                fit_config=fit_config,
                search_config=search_config,
                container_guard=jagua_container_guard,
            )
        except JaguaRepresentationError:
            rust_generated = False
            representation_fallback_count = 1
        else:
            translation_batches = prefilter.translation_batches
            generation_seconds = 0.0
    if not rust_generated:
        generation_started = perf_counter()
        translation_batches = tuple(
            generate_layout_translations(
                remnant,
                candidate,
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=prepared_layout,
                prepared_remnant=prepared_remnant,
            )
            for candidate, prepared_layout in zip(candidates, prepared_layouts, strict=True)
        )
        generation_seconds = perf_counter() - generation_started
        evaluation_started = perf_counter()
        if jagua_executable is None or representation_fallback_count:
            prefilter = JaguaPrefilterResult(
                collision_masks=tuple(
                    (False,) * len(item.translations) for item in translation_batches
                ),
                guarded_query_count=0,
                jagua_rejection_count=0,
                build_microseconds=0,
                query_microseconds=0,
                wall_seconds=0.0,
            )
        else:
            try:
                prefilter = run_jagua_prefilter(
                    jagua_executable,
                    remnant=prepared_remnant,
                    layouts=prepared_layouts,
                    translations=translation_batches,
                    container_guard=jagua_container_guard,
                )
            except JaguaRepresentationError:
                representation_fallback_count = 1
                prefilter = JaguaPrefilterResult(
                    collision_masks=tuple(
                        (False,) * len(item.translations) for item in translation_batches
                    ),
                    guarded_query_count=0,
                    jagua_rejection_count=0,
                    build_microseconds=0,
                    query_microseconds=0,
                    wall_seconds=0.0,
                )
    accelerated = tuple(
        search_layout_translation(
            remnant,
            problem,
            candidate,
            material=material,
            fit_config=fit_config,
            search_config=search_config,
            prepared_layout=prepared_layout,
            prepared_remnant=prepared_remnant,
            translation_candidates=translations,
            collision_prefilter=collision_mask,
        )
        for candidate, prepared_layout, translations, collision_mask in zip(
            candidates,
            prepared_layouts,
            translation_batches,
            prefilter.collision_masks,
            strict=True,
        )
    )
    evaluation_seconds = perf_counter() - evaluation_started
    accelerated_seconds = generation_seconds + evaluation_seconds
    audit_count = 0
    mismatch_count = 0
    audit_seconds = 0.0
    if jagua_executable is not None and jagua_differential_audit:
        if rust_generated:
            generation_started = perf_counter()
            authoritative_batches = tuple(
                generate_layout_translations(
                    remnant,
                    candidate,
                    fit_config=fit_config,
                    search_config=search_config,
                    prepared_layout=prepared_layout,
                    prepared_remnant=prepared_remnant,
                )
                for candidate, prepared_layout in zip(candidates, prepared_layouts, strict=True)
            )
            generation_seconds = perf_counter() - generation_started
        else:
            authoritative_batches = translation_batches
        audit_started = perf_counter()
        authoritative = tuple(
            search_layout_translation(
                remnant,
                problem,
                candidate,
                material=material,
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=prepared_layout,
                prepared_remnant=prepared_remnant,
                translation_candidates=translations,
            )
            for candidate, prepared_layout, translations in zip(
                candidates,
                prepared_layouts,
                authoritative_batches,
                strict=True,
            )
        )
        audit_count = len(authoritative)
        mismatch_count = sum(
            left != right
            for left, right in zip(authoritative_batches, translation_batches, strict=True)
        ) + sum(left != right for left, right in zip(authoritative, accelerated, strict=True))
        audit_seconds = perf_counter() - audit_started
    return accelerated, _JaguaChunkMetrics(
        wall_seconds=prefilter.wall_seconds,
        guarded_query_count=prefilter.guarded_query_count,
        rejection_count=prefilter.jagua_rejection_count,
        audit_search_count=audit_count,
        audit_mismatch_count=mismatch_count,
        accelerated_search_seconds=accelerated_seconds,
        authoritative_audit_seconds=audit_seconds,
        translation_generation_seconds=generation_seconds,
        accelerated_evaluation_seconds=evaluation_seconds,
        representation_fallback_count=representation_fallback_count,
    )


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
    candidates: tuple[Candidate, ...],
    inventory: tuple[InventoryItem, ...],
    occurred_at: datetime,
    rates: FeasibilityRateManifest,
) -> tuple[ActionPolicyContext, ...]:
    candidate_width = {item.candidate_id: item.width for item in candidates}
    inventory_by_id = {item.remnant.remnant_id: item for item in inventory}
    contexts = []
    for profile in generated.standard_profiles:
        retained_area = profile.accounting.retained_child_area
        immediate = rounded_cost(
            profile.accounting.parent_remnant_area * rates.purchase_cost_per_area
            + profile.returned_remnant_count * rates.return_handling_cost_per_remnant
            + retained_area * rates.storage_cost_per_area_hour
            - profile.accounting.scrap_area * rates.scrap_credit_per_area
        )
        contexts.append(
            ActionPolicyContext(
                action_id=f"m7-standard:{profile.candidate_id}",
                kind=M7ActionKind.OPEN_STANDARD_SHEET,
                candidate_id=profile.candidate_id,
                candidate_width=profile.candidate_width,
                selected_stock_id="current_standard_sheet",
                immediate_net_cost=immediate,
                selected_remnant_age_hours=0.0,
                returned_regularity=profile.returned_regularity,
                known_order_lookahead_term=0.0,
            )
        )
    for action in generated.remnant_actions:
        contexts.append(
            _remnant_policy_context(
                action,
                candidate_widths=candidate_width,
                inventory_by_id=inventory_by_id,
                occurred_at=occurred_at,
                rates=rates,
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
        "action_set_size": len(generated.standard_profiles) + generated.remnant_action_count,
        "standard_action_count": len(generated.standard_profiles),
        "remnant_action_count": generated.remnant_action_count,
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


def _validate_runtime(runtime: M7ReplayRuntime) -> None:
    expected_evidence = {
        item.problem_id: item for item in runtime.replay_input.candidate_sets
    }
    if set(runtime.runtime_candidates) != set(expected_evidence) or any(
        runtime.runtime_candidates[key].evidence != expected_evidence[key]
        for key in expected_evidence
    ):
        raise ValueError("M7 runtime candidate sets differ from replay input")
    jagua_enabled = (
        runtime.replay_input.collision_backend
        == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
    )
    if jagua_enabled != (runtime.jagua_executable is not None):
        raise ValueError("M7 replay collision backend differs from runtime extension")


def initial_m7_cursor(replay_input: M7ReplayInput) -> M7ReplayCursor:
    """Return the exact empty state before the first registered event."""

    return M7ReplayCursor(
        next_event_position=0,
        current_time=replay_input.instances[0].released_at,
        inventory=(),
        cumulative_costs=ReplayCostLedger.zero(),
        timestamp_group_sequence=-1,
        timestamp_subsequence=0,
        previous_release=None,
    )


def cursor_after_event(result: M7ReplayResult, *, sequence: int) -> M7ReplayCursor:
    """Reconstruct an exact continuation cursor from persisted M7 evidence."""

    if sequence < 0 or sequence >= len(result.events):
        raise ValueError("M7 cursor event sequence is outside the replay result")
    event = result.events[sequence]
    return M7ReplayCursor(
        next_event_position=sequence + 1,
        current_time=event.occurred_at,
        inventory=event.inventory_after,
        cumulative_costs=event.cumulative_costs,
        timestamp_group_sequence=event.timestamp_group_sequence,
        timestamp_subsequence=event.timestamp_subsequence,
        previous_release=event.occurred_at,
    )


def enumerate_m7_action_catalog(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    event_position: int | None = None,
) -> M7ActionCatalog:
    """Enumerate every exact feasible action at one arbitrary M7 cursor."""

    _validate_runtime(runtime)
    position = cursor.next_event_position if event_position is None else event_position
    if position != cursor.next_event_position:
        raise ValueError("M7 catalog position differs from cursor")
    if position < 0 or position >= len(runtime.replay_input.instances):
        raise ValueError("M7 catalog position is outside the replay stream")
    replay_input = runtime.replay_input
    binding = replay_input.instances[position]
    if binding.released_at != cursor.previous_release:
        group_sequence = cursor.timestamp_group_sequence + 1
        group_subsequence = 0
    else:
        group_sequence = cursor.timestamp_group_sequence
        group_subsequence = cursor.timestamp_subsequence + 1
    storage = _storage_cost(
        cursor.inventory,
        start=cursor.current_time,
        end=binding.released_at,
        rate=replay_input.rates.storage_cost_per_area_hour,
    )
    problem_by_id = {item.problem_id: item for item in replay_input.problems}
    problem = problem_by_id[binding.problem_id]
    verified = runtime.runtime_candidates[binding.problem_id]
    generated = _generate_actions(
        binding=binding,
        problem=problem,
        verified=verified,
        inventory=cursor.inventory,
        rules=runtime.rules,
        fit_config=replay_input.fit_config,
        search_config=replay_input.search_config,
        policy=replay_input.policy.name,
        rates=replay_input.rates,
        runtime_metrics=runtime.runtime_metrics,
        standard_profile_cache=runtime.standard_profile_cache,
        standard_profile_executor=runtime.standard_profile_executor,
        jagua_executable=runtime.jagua_executable,
        jagua_container_guard=replay_input.jagua_container_guard or 1.0,
        jagua_differential_audit=runtime.jagua_differential_audit,
        fit_search_cache=runtime.fit_search_cache,
        shared_fit_search_cache=runtime.shared_fit_search_cache,
        prepared_layout_cache=runtime.prepared_layout_cache,
        retain_all_remnant_actions=True,
    )
    contexts = _policy_contexts(
        generated,
        candidates=verified.candidates,
        inventory=cursor.inventory,
        occurred_at=binding.released_at,
        rates=replay_input.rates,
    )
    actions = tuple(
        M7ActionDescriptor(
            action_id=f"m7-standard:{profile.candidate_id}",
            kind=M7ActionKind.OPEN_STANDARD_SHEET,
            candidate_id=profile.candidate_id,
            selected_remnant_id=None,
        )
        for profile in generated.standard_profiles
    ) + tuple(
        M7ActionDescriptor(
            action_id=action.action_id,
            kind=action.kind,
            candidate_id=action.candidate_id,
            selected_remnant_id=action.selected_remnant_id,
            evidence=action,
        )
        for action in generated.remnant_actions
    )
    if len(actions) != len(generated.standard_profiles) + generated.remnant_action_count:
        raise ValueError("M7 complete action catalog count does not reconcile")
    return M7ActionCatalog(
        event_position=position,
        actions=actions,
        contexts=contexts,
        standard_action_count=len(generated.standard_profiles),
        remnant_action_count=generated.remnant_action_count,
        storage_cost=storage,
        timestamp_group_sequence=group_sequence,
        timestamp_subsequence=group_subsequence,
        generated=generated,
    )


def select_m7_fallback(
    catalog: M7ActionCatalog,
    *,
    policy: M7PolicyIdentity | M7PolicyName,
):  # type: ignore[no-untyped-def]
    """Select the exact frozen M7 fallback from a complete catalog."""

    name = policy.name if isinstance(policy, M7PolicyIdentity) else policy
    selection = select_policy_action(name, catalog.contexts)
    if selection.action_id not in {item.action_id for item in catalog.actions}:
        raise ValueError("M7 fallback is absent from the complete action catalog")
    return selection


def apply_m7_action_descriptor(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    catalog: M7ActionCatalog,
    descriptor: M7ActionDescriptor,
    decision_key: tuple[str, ...],
) -> M7StepResult:
    """Materialize and execute one catalog action with exact M7 accounting."""

    if catalog.event_position != cursor.next_event_position:
        raise ValueError("M7 action catalog differs from cursor")
    registered = {item.action_id: item for item in catalog.actions}
    if registered.get(descriptor.action_id) != descriptor:
        raise ValueError("M7 action descriptor is absent from the catalog")
    replay_input = runtime.replay_input
    binding = replay_input.instances[catalog.event_position]
    problem = next(
        item for item in replay_input.problems if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    if descriptor.kind is M7ActionKind.OPEN_STANDARD_SHEET:
        candidate = next(
            item for item in verified.candidates if item.candidate_id == descriptor.candidate_id
        )
        materialization_started = perf_counter()
        action = build_standard_sheet_action(
            problem_id=problem.problem_id,
            problem_sha256=problem.content_sha256,
            candidate_set_id=verified.evidence.candidate_set_id,
            candidate_set_sha256=verified.evidence.content_sha256,
            problem=problem.problem,
            candidate=candidate,
            material=binding.material,
            stock_id=binding.binding_id,
            rules=runtime.rules,
            fit_config=replay_input.fit_config,
        )
        if runtime.runtime_metrics is not None:
            runtime.runtime_metrics.standard_action_seconds += (
                perf_counter() - materialization_started
            )
        profile = runtime.standard_profile_cache[(problem.problem_id, descriptor.candidate_id)]
        if (
            action.accounting != profile.accounting
            or len(action.returned_remnants) != profile.returned_remnant_count
            or abs(_returned_regularity(action) - profile.returned_regularity) > 1e-9
        ):
            raise ValueError("M7 standard action differs from its exact cached profile")
    else:
        if descriptor.evidence is None:
            raise ValueError("M7 remnant descriptor has no exact evidence")
        action = descriptor.evidence
    inventory_after = _execute_action(action, cursor.inventory, binding.released_at)
    delta = _ledger(
        purchase_cost=(
            action.selected_stock.geometry.area * replay_input.rates.purchase_cost_per_area
            if action.kind is M7ActionKind.OPEN_STANDARD_SHEET
            else 0.0
        ),
        storage_cost=catalog.storage_cost,
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
    cumulative = _add_ledgers(cursor.cumulative_costs, delta)
    event = _event(
        sequence=catalog.event_position,
        binding=binding,
        group_sequence=catalog.timestamp_group_sequence,
        group_subsequence=catalog.timestamp_subsequence,
        storage_interval_start=cursor.current_time,
        inventory_before=cursor.inventory,
        generated=catalog.generated,
        decision_key=decision_key,
        action=action,
        inventory_after=inventory_after,
        delta=delta,
        cumulative=cumulative,
    )
    next_cursor = M7ReplayCursor(
        next_event_position=catalog.event_position + 1,
        current_time=binding.released_at,
        inventory=inventory_after,
        cumulative_costs=cumulative,
        timestamp_group_sequence=catalog.timestamp_group_sequence,
        timestamp_subsequence=catalog.timestamp_subsequence,
        previous_release=binding.released_at,
    )
    return M7StepResult(descriptor=descriptor, event=event, cursor=next_cursor)


def run_m7_continuation(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
) -> M7ContinuationResult:
    """Run the unchanged frozen M7 policy from an arbitrary exact cursor."""

    events = []
    while cursor.next_event_position < len(runtime.replay_input.instances):
        catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
        selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
        descriptor = next(
            item for item in catalog.actions if item.action_id == selection.action_id
        )
        step = apply_m7_action_descriptor(
            runtime,
            cursor=cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=selection.decision_key,
        )
        events.append(step.event)
        cursor = step.cursor
    terminal_storage = _storage_cost(
        cursor.inventory,
        start=cursor.current_time,
        end=runtime.replay_input.horizon_end,
        rate=runtime.replay_input.rates.storage_cost_per_area_hour,
    )
    terminal_credit = rounded_cost(
        sum(item.remnant.geometry.area for item in cursor.inventory)
        * runtime.replay_input.rates.scrap_credit_per_area
    )
    terminal_delta = _ledger(
        storage_cost=terminal_storage,
        terminal_scrap_credit=terminal_credit,
    )
    cumulative = _add_ledgers(cursor.cumulative_costs, terminal_delta)
    terminal = ReplayTerminalRecord(
        horizon_end=runtime.replay_input.horizon_end,
        storage_interval_start=cursor.current_time,
        inventory_before_liquidation=cursor.inventory,
        liquidated_remnant_ids=_inventory_ids(cursor.inventory),
        delta_costs=terminal_delta,
        cumulative_costs=cumulative,
    )
    return M7ContinuationResult(
        events=tuple(events),
        terminal=terminal,
        final_costs=cumulative,
    )


def run_m7_replay(
    replay_input: M7ReplayInput,
    runtime_candidates: dict[str, VerifiedProblemCandidates],
    rules: ResidualRuleSet,
    *,
    runtime_metrics: M7ReplayRuntimeMetrics | None = None,
    progress: Callable[[int, int], None] | None = None,
    standard_profile_cache: dict[tuple[str, str], M7StandardActionProfile] | None = None,
    standard_profile_executor: Executor | None = None,
    jagua_executable: Path | None = None,
    jagua_differential_audit: bool = False,
    shared_fit_search_cache: M7SharedFitSearchCache | None = None,
    prepared_layout_cache: M7PreparedLayoutCache | None = None,
) -> M7ReplayResult:
    """Execute one M7 stream deterministically with exact shared action generation."""

    replay_started = perf_counter()
    problem_by_id = {item.problem_id: item for item in replay_input.problems}
    expected_evidence = {item.problem_id: item for item in replay_input.candidate_sets}
    if set(runtime_candidates) != set(expected_evidence) or any(
        runtime_candidates[key].evidence != expected_evidence[key] for key in expected_evidence
    ):
        raise ValueError("M7 runtime candidate sets differ from replay input")
    profile_cache = standard_profile_cache if standard_profile_cache is not None else {}
    layout_cache = prepared_layout_cache if prepared_layout_cache is not None else OrderedDict()
    fit_search_cache: dict[tuple[str, str, str], tuple[LayoutFitSearchResult, ...]] = {}
    jagua_enabled = (
        replay_input.collision_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
    )
    if jagua_enabled != (jagua_executable is not None):
        raise ValueError("M7 replay collision backend differs from runtime extension")
    jagua_guard = replay_input.jagua_container_guard or 1.0

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
            policy=replay_input.policy.name,
            rates=replay_input.rates,
            runtime_metrics=runtime_metrics,
            standard_profile_cache=profile_cache,
            standard_profile_executor=standard_profile_executor,
            jagua_executable=jagua_executable,
            jagua_container_guard=jagua_guard,
            jagua_differential_audit=jagua_differential_audit,
            fit_search_cache=fit_search_cache,
            shared_fit_search_cache=shared_fit_search_cache,
            prepared_layout_cache=layout_cache,
        )
        contexts = _policy_contexts(
            generated,
            candidates=verified.candidates,
            inventory=inventory_before,
            occurred_at=binding.released_at,
            rates=replay_input.rates,
        )
        selection = select_policy_action(replay_input.policy.name, contexts)
        if selection.action_id.startswith("m7-standard:"):
            selected_candidate_id = selection.action_id.removeprefix("m7-standard:")
            candidate_by_id = {item.candidate_id: item for item in verified.candidates}
            candidate = candidate_by_id[selected_candidate_id]
            materialization_started = perf_counter()
            action = build_standard_sheet_action(
                problem_id=problem.problem_id,
                problem_sha256=problem.content_sha256,
                candidate_set_id=verified.evidence.candidate_set_id,
                candidate_set_sha256=verified.evidence.content_sha256,
                problem=problem.problem,
                candidate=candidate,
                material=binding.material,
                stock_id=binding.binding_id,
                rules=rules,
                fit_config=replay_input.fit_config,
            )
            if runtime_metrics is not None:
                runtime_metrics.standard_action_seconds += perf_counter() - materialization_started
            profile = profile_cache[(problem.problem_id, selected_candidate_id)]
            if (
                action.accounting != profile.accounting
                or len(action.returned_remnants) != profile.returned_remnant_count
                or abs(_returned_regularity(action) - profile.returned_regularity) > 1e-9
            ):
                raise ValueError(
                    "M7 selected standard action differs from its exact cached profile"
                )
        else:
            action_by_id = {item.action_id: item for item in generated.remnant_actions}
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
        if progress is not None:
            progress(sequence + 1, len(replay_input.instances))
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
    result = _result(replay_input, event_tuple, terminal, summary)
    if runtime_metrics is not None:
        runtime_metrics.replay_elapsed_seconds += perf_counter() - replay_started
    return result


__all__ = [
    "M7ActionCatalog",
    "M7ActionDescriptor",
    "M7ContinuationResult",
    "M7ReplayEvent",
    "M7ReplayInput",
    "M7ReplayResult",
    "M7ReplayCursor",
    "M7ReplayRuntime",
    "M7ReplayRuntimeMetrics",
    "M7ReplaySummary",
    "M7StepResult",
    "M7StandardActionProfile",
    "M7SharedFitSearchCache",
    "M7PreparedLayoutCache",
    "apply_m7_action_descriptor",
    "build_m7_replay_input",
    "cursor_after_event",
    "enumerate_m7_action_catalog",
    "initial_m7_cursor",
    "run_m7_replay",
    "run_m7_continuation",
    "select_m7_fallback",
]
