"""Deterministic chronological replay for exact M7 complete-layout actions."""

from __future__ import annotations

import copy
import hashlib
import math
import os
import secrets
import stat
import tempfile
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Executor
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
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
from yieldforge.reuse.contracts import (
    CanonicalPolygon,
    RemnantFitConfig,
    ReuseAccounting,
    polygon_from_record,
)
from yieldforge.reuse.geometry import material_key
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest

type M7SharedFitSearchCache = dict[tuple[str, str, str], tuple[LayoutFitSearchResult, ...]]
type M7PreparedLayoutCache = OrderedDict[tuple[str, str], tuple[PreparedLayoutFootprint, ...]]

_MAX_PREPARED_LAYOUT_CACHE_PROBLEMS = 2
_REMNANT_ACTION_MATERIALIZATION_BATCH_SIZE = 64


def m7_shared_fit_search_cache_key(
    *,
    geometry: CanonicalPolygon,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
    problem_id: str,
    candidate_set_id: str,
) -> tuple[str, str, str]:
    """Return the frozen content key for one reusable geometry-search result."""

    return (
        semantic_sha256(
            {
                "geometry": geometry.model_dump(mode="json"),
                "fit_config": fit_config.model_dump(mode="json"),
                "search_config": search_config.model_dump(mode="json"),
            }
        ),
        problem_id,
        candidate_set_id,
    )


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
    materialized_standard_actions: tuple[M7LayoutActionEvidence, ...] = dataclass_field(
        default=(),
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        actions = self.materialized_standard_actions
        if not actions:
            return
        profiles = self.standard_profiles
        if len(actions) != len(profiles):
            raise ValueError("M7 materialized standard actions must be complete")
        candidate_ids = tuple(action.candidate_id for action in actions)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("M7 materialized standard actions must be unique")
        if candidate_ids != tuple(profile.candidate_id for profile in profiles):
            raise ValueError("M7 materialized standard actions differ from profile order")
        if any(action.kind is not M7ActionKind.OPEN_STANDARD_SHEET for action in actions):
            raise ValueError("M7 materialized standard actions must be standard-sheet actions")
        for action, profile in zip(actions, profiles, strict=True):
            if not _standard_action_matches_profile(action, profile):
                raise ValueError(
                    "M7 materialized standard action differs from its exact cached profile"
                )


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
class _BuiltStandardProfile:
    profile: M7StandardActionProfile
    action: M7LayoutActionEvidence


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
class M7CursorTransition:
    """One applied cursor plus the commitments computed during exact validation."""

    cursor: M7ReplayCursor
    cursor_before_sha256: str
    cursor_after_sha256: str
    _provenance_token: object | None = dataclass_field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self._provenance_token is not _M7_CURSOR_TRANSITION_PROVENANCE:
            raise ValueError("M7 cursor transition lacks authoritative apply provenance")


_M7_CURSOR_TRANSITION_PROVENANCE = object()


def _trusted_m7_cursor_transition(
    applied: _M7AppliedAction,
) -> M7CursorTransition:
    transition = object.__new__(M7CursorTransition)
    object.__setattr__(transition, "cursor", applied.cursor)
    object.__setattr__(
        transition,
        "cursor_before_sha256",
        applied.cursor_before_sha256,
    )
    object.__setattr__(
        transition,
        "cursor_after_sha256",
        applied.cursor_after_sha256,
    )
    object.__setattr__(
        transition,
        "_provenance_token",
        _M7_CURSOR_TRANSITION_PROVENANCE,
    )
    transition.__post_init__()
    return transition


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
class M7PolicyActionBinding:
    """Runtime binding from a policy catalog identity to executed action evidence."""

    catalog_action_id: str
    materialized_action_id: str
    context: ActionPolicyContext

    def __post_init__(self) -> None:
        if not self.catalog_action_id or not self.materialized_action_id:
            raise ValueError("M7 action binding identities must be nonempty")
        if self.context.action_id != self.catalog_action_id:
            raise ValueError("M7 binding context action ID differs from catalog action ID")


@dataclass(frozen=True)
class M7StepResult:
    descriptor: M7ActionDescriptor
    selected_context: ActionPolicyContext
    action_binding: M7PolicyActionBinding
    event: M7ReplayEvent
    cursor: M7ReplayCursor

    def __post_init__(self) -> None:
        if self.action_binding.catalog_action_id != self.descriptor.action_id:
            raise ValueError("M7 step binding catalog action ID differs from descriptor action ID")
        if self.action_binding.materialized_action_id != self.event.action.action_id:
            raise ValueError("M7 step binding materialized action ID differs from event action ID")
        if self.action_binding.context != self.selected_context:
            raise ValueError("M7 step binding context differs from selected context")


@dataclass(frozen=True)
class M7ContinuationResult:
    events: tuple[M7ReplayEvent, ...]
    selected_contexts: tuple[ActionPolicyContext, ...]
    action_bindings: tuple[M7PolicyActionBinding, ...]
    terminal: ReplayTerminalRecord
    final_costs: ReplayCostLedger

    def __post_init__(self) -> None:
        if not (
            len(self.events) == len(self.selected_contexts) == len(self.action_bindings)
        ):
            raise ValueError("M7 continuation action bindings must remain event-aligned")
        if any(
            binding.context != context
            or binding.materialized_action_id != event.action.action_id
            for binding, context, event in zip(
                self.action_bindings,
                self.selected_contexts,
                self.events,
                strict=True,
            )
        ):
            raise ValueError("M7 continuation action bindings must remain event-aligned")


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


@dataclass(frozen=True, slots=True)
class _MaterializedStandardActionEntry:
    """One O(1)-addressable standard action bound at capability issuance."""

    descriptor: M7ActionDescriptor
    descriptor_sha256: str
    context: ActionPolicyContext
    context_sha256: str
    candidate: Candidate
    candidate_sha256: str
    profile: M7StandardActionProfile
    profile_sha256: str
    action: M7LayoutActionEvidence
    issued_action_id: str
    issued_action_content_sha256: str
    catalog_action_index: int
    catalog_context_index: int
    candidate_index: int
    standard_action_index: int


@dataclass(frozen=True, slots=True)
class _MaterializedStandardActionRecord:
    """Private process-local authority for one generated standard-action sidecar."""

    reference: weakref.ReferenceType[GeneratedActionSet]
    catalog_reference: weakref.ReferenceType[M7ActionCatalog]
    owner_pid: int
    runtime_id: int
    replay_input_id: int
    replay_problems_id: int
    replay_instances_id: int
    runtime_candidates_id: int
    rules_id: int
    rules_sha256: str
    fit_config_id: int
    fit_config_sha256: str
    rates_id: int
    rates_sha256: str
    standard_profile_cache_id: int
    standard_profiles_id: int
    materialized_standard_actions_id: int
    catalog_actions_id: int
    catalog_contexts_id: int
    verified: VerifiedProblemCandidates
    verified_candidates_id: int
    binding: TemporalInstanceBinding
    binding_sha256: str
    problem: ReusableGeometryProblem
    problem_index: int
    problem_sha256: str
    problem_id: str
    event_position: int
    cursor_sha256: str
    generated_event_metadata_sha256: str
    catalog_transition_sha256: str
    commitment_sha256: str
    entries: Mapping[str, _MaterializedStandardActionEntry]


_MATERIALIZED_STANDARD_ACTION_REGISTRY: dict[
    int,
    _MaterializedStandardActionRecord,
] = {}


def _standard_profile_payload(profile: M7StandardActionProfile) -> dict[str, object]:
    return {
        "candidate_id": profile.candidate_id,
        "candidate_width": profile.candidate_width,
        "accounting": profile.accounting.model_dump(mode="json"),
        "returned_remnant_count": profile.returned_remnant_count,
        "returned_regularity": profile.returned_regularity,
    }


def _standard_descriptor_payload(descriptor: M7ActionDescriptor) -> dict[str, object]:
    return {
        "action_id": descriptor.action_id,
        "kind": descriptor.kind.value,
        "candidate_id": descriptor.candidate_id,
        "selected_remnant_id": descriptor.selected_remnant_id,
        "evidence": descriptor.evidence,
    }


def _standard_context_payload(context: ActionPolicyContext) -> dict[str, object]:
    return {
        "action_id": context.action_id,
        "kind": context.kind.value,
        "candidate_id": context.candidate_id,
        "candidate_width": context.candidate_width,
        "selected_stock_id": context.selected_stock_id,
        "immediate_net_cost": context.immediate_net_cost,
        "selected_remnant_age_hours": context.selected_remnant_age_hours,
        "returned_regularity": context.returned_regularity,
        "known_order_lookahead_term": context.known_order_lookahead_term,
    }


def _semantic_payload_sha256(payload: object) -> str:
    return f"sha256:{semantic_sha256(payload)}"


def _generated_event_metadata_payload(generated: GeneratedActionSet) -> dict[str, object]:
    """Return only generated scalars read while persisting one retained event."""

    return {
        "standard_action_count": len(generated.standard_profiles),
        "remnant_action_count": generated.remnant_action_count,
        "fit_search_query_count": generated.fit_search_query_count,
        "fit_search_generated_candidate_count": (
            generated.fit_search_generated_candidate_count
        ),
        "fit_search_evaluated_candidate_count": (
            generated.fit_search_evaluated_candidate_count
        ),
        "fit_search_budget_truncated_count": (
            generated.fit_search_budget_truncated_count
        ),
    }


def _catalog_transition_payload(catalog: M7ActionCatalog) -> dict[str, object]:
    """Return the catalog scalars consumed by retained transition validation."""

    return {
        "event_position": catalog.event_position,
        "standard_action_count": catalog.standard_action_count,
        "remnant_action_count": catalog.remnant_action_count,
        "storage_cost": catalog.storage_cost,
        "timestamp_group_sequence": catalog.timestamp_group_sequence,
        "timestamp_subsequence": catalog.timestamp_subsequence,
    }


def _materialized_standard_action_commitment(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    generated: GeneratedActionSet,
) -> str:
    replay_input = runtime.replay_input
    if event_position < 0 or event_position >= len(replay_input.instances):
        raise ValueError("M7 materialized standard action event is outside the replay stream")
    binding = replay_input.instances[event_position]
    problem = next(
        item for item in replay_input.problems if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    payload = {
        "schema_version": "yieldforge.m7-materialized-standard-action-capability.v1",
        "owner_pid": os.getpid(),
        "generated_id": id(generated),
        "runtime_id": id(runtime),
        "replay_input_id": id(replay_input),
        "replay_input_identity": (replay_input.input_id, replay_input.content_sha256),
        "runtime_candidates_id": id(runtime.runtime_candidates),
        "rules_id": id(runtime.rules),
        "rules": runtime.rules.model_dump(mode="json"),
        "standard_profile_cache_id": id(runtime.standard_profile_cache),
        "event_position": event_position,
        "binding": binding.model_dump(mode="json"),
        "problem": problem.model_dump(mode="json"),
        "candidate_evidence": verified.evidence.model_dump(mode="json"),
        "candidates": tuple(
            candidate.model_dump(mode="json") for candidate in verified.candidates
        ),
        "standard_profiles": tuple(
            _standard_profile_payload(profile) for profile in generated.standard_profiles
        ),
        "remnant_actions": tuple(
            action.model_dump(mode="json") for action in generated.remnant_actions
        ),
        "remnant_action_count": generated.remnant_action_count,
        "fit_search_query_count": generated.fit_search_query_count,
        "fit_search_generated_candidate_count": (
            generated.fit_search_generated_candidate_count
        ),
        "fit_search_evaluated_candidate_count": (
            generated.fit_search_evaluated_candidate_count
        ),
        "fit_search_budget_truncated_count": (
            generated.fit_search_budget_truncated_count
        ),
        "materialized_standard_actions": tuple(
            action.model_dump(mode="json")
            for action in generated.materialized_standard_actions
        ),
    }
    return f"sha256:{semantic_sha256(payload)}"


def _issue_materialized_standard_action_capability(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    catalog: M7ActionCatalog,
    problem: ReusableGeometryProblem,
    verified: VerifiedProblemCandidates,
) -> None:
    """Register one generator-owned sidecar without exposing issuance authority."""

    event_position = catalog.event_position
    generated = catalog.generated
    if cursor.next_event_position != event_position:
        raise ValueError("M7 materialized standard action capability cursor differs")
    if not generated.materialized_standard_actions:
        raise ValueError("M7 materialized standard action capability requires exact actions")
    key = id(generated)
    current = _MATERIALIZED_STANDARD_ACTION_REGISTRY.get(key)
    if current is not None and current.reference() is generated:
        raise ValueError("M7 materialized standard action capability was already issued")

    def discard(reference: weakref.ReferenceType[GeneratedActionSet]) -> None:
        registered = _MATERIALIZED_STANDARD_ACTION_REGISTRY.get(key)
        if registered is not None and registered.reference is reference:
            _MATERIALIZED_STANDARD_ACTION_REGISTRY.pop(key, None)

    replay_input = runtime.replay_input
    binding = replay_input.instances[event_position]
    problem_positions = tuple(
        index for index, item in enumerate(replay_input.problems) if item is problem
    )
    if len(problem_positions) != 1 or problem.problem_id != binding.problem_id:
        raise ValueError("M7 materialized standard action capability problem binding differs")
    if runtime.runtime_candidates.get(problem.problem_id) is not verified:
        raise ValueError("M7 materialized standard action capability candidates differ")
    candidate_positions = {
        candidate.candidate_id: (index, candidate)
        for index, candidate in enumerate(verified.candidates)
    }
    if len(candidate_positions) != len(verified.candidates):
        raise ValueError("M7 materialized standard action capability candidates are not unique")
    context_positions = {
        context.action_id: (index, context)
        for index, context in enumerate(catalog.contexts)
    }
    if len(context_positions) != len(catalog.contexts):
        raise ValueError("M7 materialized standard action capability contexts are not unique")
    entries: dict[str, _MaterializedStandardActionEntry] = {}
    for index, (profile, action) in enumerate(
        zip(
            generated.standard_profiles,
            generated.materialized_standard_actions,
            strict=True,
        )
    ):
        expected_descriptor_id = f"m7-standard:{profile.candidate_id}"
        descriptor = catalog.actions[index]
        candidate_position = candidate_positions.get(profile.candidate_id)
        context_position = context_positions.get(expected_descriptor_id)
        if (
            candidate_position is None
            or context_position is None
            or descriptor.action_id != expected_descriptor_id
            or descriptor.kind is not M7ActionKind.OPEN_STANDARD_SHEET
            or descriptor.candidate_id != profile.candidate_id
            or descriptor.selected_remnant_id is not None
            or descriptor.evidence is not None
            or action.candidate_id != profile.candidate_id
            or runtime.standard_profile_cache.get((problem.problem_id, profile.candidate_id))
            is not profile
        ):
            raise ValueError("M7 materialized standard action capability bindings differ")
        candidate_index, candidate = candidate_position
        context_index, context = context_position
        entries[expected_descriptor_id] = _MaterializedStandardActionEntry(
            descriptor=descriptor,
            descriptor_sha256=_semantic_payload_sha256(
                _standard_descriptor_payload(descriptor)
            ),
            context=context,
            context_sha256=_semantic_payload_sha256(_standard_context_payload(context)),
            candidate=candidate,
            candidate_sha256=_semantic_payload_sha256(candidate.model_dump(mode="json")),
            profile=profile,
            profile_sha256=_semantic_payload_sha256(_standard_profile_payload(profile)),
            action=action,
            issued_action_id=action.action_id,
            issued_action_content_sha256=action.content_sha256,
            catalog_action_index=index,
            catalog_context_index=context_index,
            candidate_index=candidate_index,
            standard_action_index=index,
        )
    if len(entries) != len(generated.standard_profiles):
        raise ValueError("M7 materialized standard action capability entries are incomplete")

    reference = weakref.ref(generated, discard)
    _MATERIALIZED_STANDARD_ACTION_REGISTRY[key] = _MaterializedStandardActionRecord(
        reference=reference,
        catalog_reference=weakref.ref(catalog),
        owner_pid=os.getpid(),
        runtime_id=id(runtime),
        replay_input_id=id(replay_input),
        replay_problems_id=id(replay_input.problems),
        replay_instances_id=id(replay_input.instances),
        runtime_candidates_id=id(runtime.runtime_candidates),
        rules_id=id(runtime.rules),
        rules_sha256=_semantic_payload_sha256(runtime.rules.model_dump(mode="json")),
        fit_config_id=id(replay_input.fit_config),
        fit_config_sha256=_semantic_payload_sha256(
            replay_input.fit_config.model_dump(mode="json")
        ),
        rates_id=id(replay_input.rates),
        rates_sha256=_semantic_payload_sha256(replay_input.rates.model_dump(mode="json")),
        standard_profile_cache_id=id(runtime.standard_profile_cache),
        standard_profiles_id=id(generated.standard_profiles),
        materialized_standard_actions_id=id(generated.materialized_standard_actions),
        catalog_actions_id=id(catalog.actions),
        catalog_contexts_id=id(catalog.contexts),
        verified=verified,
        verified_candidates_id=id(verified.candidates),
        binding=binding,
        binding_sha256=_semantic_payload_sha256(binding.model_dump(mode="json")),
        problem=problem,
        problem_index=problem_positions[0],
        problem_sha256=_semantic_payload_sha256(problem.model_dump(mode="json")),
        problem_id=problem.problem_id,
        event_position=event_position,
        cursor_sha256=m7_cursor_sha256(cursor),
        generated_event_metadata_sha256=_semantic_payload_sha256(
            _generated_event_metadata_payload(generated)
        ),
        catalog_transition_sha256=_semantic_payload_sha256(
            _catalog_transition_payload(catalog)
        ),
        commitment_sha256=_materialized_standard_action_commitment(
            runtime,
            event_position=event_position,
            generated=generated,
        ),
        entries=MappingProxyType(entries),
    )


def _require_materialized_standard_action_capability(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    catalog: M7ActionCatalog,
    descriptor: M7ActionDescriptor,
) -> tuple[_MaterializedStandardActionRecord, _MaterializedStandardActionEntry]:
    event_position = catalog.event_position
    generated = catalog.generated
    registered = _MATERIALIZED_STANDARD_ACTION_REGISTRY.get(id(generated))
    if (
        type(generated) is not GeneratedActionSet
        or registered is None
        or registered.reference() is not generated
        or registered.catalog_reference() is not catalog
        or registered.owner_pid != os.getpid()
        or registered.runtime_id != id(runtime)
        or registered.replay_input_id != id(runtime.replay_input)
        or registered.replay_problems_id != id(runtime.replay_input.problems)
        or registered.replay_instances_id != id(runtime.replay_input.instances)
        or registered.runtime_candidates_id != id(runtime.runtime_candidates)
        or registered.rules_id != id(runtime.rules)
        or registered.fit_config_id != id(runtime.replay_input.fit_config)
        or registered.rates_id != id(runtime.replay_input.rates)
        or registered.standard_profile_cache_id != id(runtime.standard_profile_cache)
        or registered.standard_profiles_id != id(generated.standard_profiles)
        or registered.materialized_standard_actions_id
        != id(generated.materialized_standard_actions)
        or registered.catalog_actions_id != id(catalog.actions)
        or registered.catalog_contexts_id != id(catalog.contexts)
        or registered.verified_candidates_id != id(registered.verified.candidates)
        or registered.event_position != event_position
    ):
        raise ValueError("M7 materialized standard action capability is invalid or inactive")
    if not registered.commitment_sha256.startswith("sha256:"):
        raise ValueError("M7 materialized standard action capability integrity differs")
    try:
        entry = registered.entries.get(descriptor.action_id)
        structurally_bound = (
            entry is not None
            and entry.descriptor is descriptor
            and catalog.actions[entry.catalog_action_index] is descriptor
            and catalog.contexts[entry.catalog_context_index] is entry.context
            and generated.standard_profiles[entry.standard_action_index] is entry.profile
            and generated.materialized_standard_actions[entry.standard_action_index]
            is entry.action
            and runtime.replay_input.problems[registered.problem_index]
            is registered.problem
            and runtime.replay_input.instances[event_position] is registered.binding
            and runtime.runtime_candidates.get(registered.problem_id) is registered.verified
            and registered.verified.candidates[entry.candidate_index] is entry.candidate
            and runtime.standard_profile_cache.get(
                (registered.problem_id, entry.candidate.candidate_id)
            )
            is entry.profile
        )
        semantically_bound = (
            entry is not None
            and entry.descriptor_sha256
            == _semantic_payload_sha256(_standard_descriptor_payload(entry.descriptor))
            and entry.context_sha256
            == _semantic_payload_sha256(_standard_context_payload(entry.context))
            and entry.candidate_sha256
            == _semantic_payload_sha256(entry.candidate.model_dump(mode="json"))
            and entry.profile_sha256
            == _semantic_payload_sha256(_standard_profile_payload(entry.profile))
            and registered.problem_sha256
            == _semantic_payload_sha256(registered.problem.model_dump(mode="json"))
            and registered.binding_sha256
            == _semantic_payload_sha256(registered.binding.model_dump(mode="json"))
            and registered.rules_sha256
            == _semantic_payload_sha256(runtime.rules.model_dump(mode="json"))
            and registered.fit_config_sha256
            == _semantic_payload_sha256(
                runtime.replay_input.fit_config.model_dump(mode="json")
            )
            and registered.rates_sha256
            == _semantic_payload_sha256(runtime.replay_input.rates.model_dump(mode="json"))
            and registered.cursor_sha256 == m7_cursor_sha256(cursor)
            and registered.generated_event_metadata_sha256
            == _semantic_payload_sha256(_generated_event_metadata_payload(generated))
            and registered.catalog_transition_sha256
            == _semantic_payload_sha256(_catalog_transition_payload(catalog))
            and entry.action.action_id == entry.issued_action_id
            and entry.action.content_sha256 == entry.issued_action_content_sha256
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        structurally_bound = False
        semantically_bound = False
        entry = None
    if not structurally_bound or not semantically_bound or entry is None:
        raise ValueError("M7 materialized standard action capability integrity differs")
    return registered, entry


class _M7SnapshotReplayRuntime(M7ReplayRuntime):
    """Private runtime whose dependency bindings cannot drift after construction."""

    _snapshot_sealed = False

    def __setattr__(self, name: str, value: object) -> None:
        if self._snapshot_sealed:
            raise AttributeError("M7 snapshot runtime dependency bindings are immutable")
        super().__setattr__(name, value)

    def seal(self) -> None:
        object.__setattr__(self, "_snapshot_sealed", True)


@dataclass(frozen=True)
class _PrivateJaguaFileIdentity:
    path: Path
    device: int
    inode: int
    mode: int
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True)
class _PrivateJaguaResources:
    directory: Path
    directory_device: int
    directory_inode: int
    directory_mode: int
    executable: _PrivateJaguaFileIdentity
    content: bytes


@dataclass(frozen=True)
class M7SemanticRuntimeSnapshot:
    """Owned immutable semantic inputs plus private caches for one M8 proof worker."""

    runtime: M7ReplayRuntime
    semantic_sha256: str
    _owner_pid: int
    _jagua_private: _PrivateJaguaResources | None = None

    @contextmanager
    def runtime_for_proof(self) -> Iterator[M7ReplayRuntime]:
        """Yield a sealed runtime with a fresh content-bound Jagua executable."""

        if os.getpid() != self._owner_pid:
            raise ValueError("M7 semantic runtime snapshot belongs to another process")
        private = self._jagua_private
        if private is None:
            yield self.runtime
            return
        _validate_private_jagua_resources(private)
        lease = _materialize_private_jagua_file(
            private,
            prefix="proof",
        )
        proof_runtime = _M7SnapshotReplayRuntime(
            replay_input=self.runtime.replay_input,
            runtime_candidates=self.runtime.runtime_candidates,
            rules=self.runtime.rules,
            standard_profile_cache=self.runtime.standard_profile_cache,
            fit_search_cache=self.runtime.fit_search_cache,
            shared_fit_search_cache=self.runtime.shared_fit_search_cache,
            prepared_layout_cache=self.runtime.prepared_layout_cache,
            jagua_executable=lease.path,
            jagua_differential_audit=True,
        )
        proof_runtime.seal()
        try:
            yield proof_runtime
        finally:
            try:
                _validate_private_jagua_resources(private)
                _validate_private_jagua_file(lease, expected_content=private.content)
            finally:
                _unlink_owned_private_file(lease)

    def close(self) -> None:
        """Release the optional private Jagua executable copy."""

        if os.getpid() != self._owner_pid:
            return
        private = self._jagua_private
        if private is None:
            return
        _unlink_owned_private_file(private.executable)
        try:
            directory = os.lstat(private.directory)
        except OSError:
            return
        if (
            stat.S_ISDIR(directory.st_mode)
            and directory.st_dev == private.directory_device
            and directory.st_ino == private.directory_inode
        ):
            try:
                private.directory.rmdir()
            except OSError:
                return

    def __reduce__(self) -> object:
        raise TypeError("M7 semantic runtime snapshots cannot be serialized")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class M7AuthoritativeProofRuntime:
    """One active cache-free semantic runtime owned by a bounded proof operation."""

    runtime: M7ReplayRuntime
    semantic_sha256: str
    _snapshot: M7SemanticRuntimeSnapshot

    def _require_active_identity(self, runtime: M7ReplayRuntime | None = None) -> None:
        registered = _AUTHORITATIVE_PROOF_RUNTIME_REGISTRY.get(id(self))
        if (
            registered is None
            or registered[0]() is not self
            or registered[1] != os.getpid()
            or os.getpid() != self._snapshot._owner_pid
        ):
            raise ValueError("M7 authoritative proof runtime is no longer active")
        if runtime is not None and runtime is not self.runtime:
            raise ValueError("M7 proof operation used a different authoritative runtime")

    def require_active(self, runtime: M7ReplayRuntime | None = None) -> None:
        """Deep-check this capability at a prepared operation boundary."""

        self._require_active_identity(runtime)
        registered = _AUTHORITATIVE_PROOF_RUNTIME_REGISTRY[id(self)]
        try:
            fingerprint = _authoritative_proof_runtime_fingerprint(self)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("M7 authoritative proof runtime integrity differs") from error
        if registered[2] != fingerprint:
            raise ValueError("M7 authoritative proof runtime integrity differs")

    def __reduce__(self) -> object:
        raise TypeError("M7 authoritative proof runtimes cannot be serialized")


_AUTHORITATIVE_PROOF_RUNTIME_REGISTRY: dict[
    int,
    tuple[weakref.ReferenceType[M7AuthoritativeProofRuntime], int, str],
] = {}


def _authoritative_proof_runtime_fingerprint(
    authority: M7AuthoritativeProofRuntime,
) -> str:
    runtime = authority.runtime
    snapshot = authority._snapshot
    candidate_mapping = tuple(
        {
            "problem_id": problem_id,
            "verified_evidence": verified.evidence.model_dump(mode="json"),
            "candidates": tuple(
                candidate.model_dump(mode="json") for candidate in verified.candidates
            ),
        }
        for problem_id, verified in sorted(runtime.runtime_candidates.items())
    )
    payload = {
        "schema_version": "yieldforge.m7-authoritative-proof-runtime-capability.v1",
        "authority_id": id(authority),
        "runtime_id": id(runtime),
        "snapshot_id": id(snapshot),
        "snapshot_runtime_id": id(snapshot.runtime),
        "semantic_sha256": authority.semantic_sha256,
        "snapshot_semantic_sha256": snapshot.semantic_sha256,
        "owner_pid": snapshot._owner_pid,
        "replay_input_id": id(runtime.replay_input),
        "replay_input": runtime.replay_input.model_dump(mode="json"),
        "rules_id": id(runtime.rules),
        "rules": runtime.rules.model_dump(mode="json"),
        "runtime_candidates_id": id(runtime.runtime_candidates),
        "runtime_candidates": candidate_mapping,
        "standard_profile_executor_is_none": runtime.standard_profile_executor is None,
        "jagua_executable": (
            str(runtime.jagua_executable) if runtime.jagua_executable is not None else None
        ),
        "jagua_differential_audit": runtime.jagua_differential_audit,
    }
    return f"sha256:{semantic_sha256(payload)}"


def _build_standard_profile(
    problem: ReusableGeometryProblem,
    evidence: M7CandidateSetEvidence,
    candidate: Candidate,
    material,  # type: ignore[no-untyped-def]
    stock_id: str,
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
) -> _BuiltStandardProfile:
    action = build_standard_sheet_action(
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        candidate_set_id=evidence.candidate_set_id,
        candidate_set_sha256=evidence.content_sha256,
        problem=problem.problem,
        candidate=candidate,
        material=material,
        stock_id=stock_id,
        rules=rules,
        fit_config=fit_config,
    )
    return _BuiltStandardProfile(
        profile=M7StandardActionProfile(
            candidate_id=candidate.candidate_id,
            candidate_width=candidate.width,
            accounting=action.accounting,
            returned_remnant_count=len(action.returned_remnants),
            returned_regularity=_returned_regularity(action),
        ),
        action=action,
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
    retain_policy_best_remnant: bool = False,
    materialize_standard_actions: bool = False,
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
    profile_candidates = verified.candidates if materialize_standard_actions else missing
    profile_arguments = tuple(
        (
            problem,
            evidence,
            candidate,
            binding.material,
            binding.binding_id,
            rules,
            fit_config,
        )
        for candidate in profile_candidates
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
    requested_candidate_ids = tuple(candidate.candidate_id for candidate in profile_candidates)
    if (
        tuple(item.profile.candidate_id for item in built) != requested_candidate_ids
        or tuple(item.action.candidate_id for item in built) != requested_candidate_ids
    ):
        raise ValueError("M7 built standard profiles differ from requested candidates")
    for item in built:
        key = (problem.problem_id, item.profile.candidate_id)
        cached = standard_profile_cache.get(key)
        if cached is None:
            standard_profile_cache[key] = item.profile
        elif cached != item.profile:
            raise ValueError("M7 standard action differs from its exact cached profile")
    profiles = tuple(
        standard_profile_cache[(problem.problem_id, candidate.candidate_id)]
        for candidate in verified.candidates
    )
    materialized_standard_actions = (
        tuple(item.action for item in built) if materialize_standard_actions else ()
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
        shared_cache_key = m7_shared_fit_search_cache_key(
            geometry=item.remnant.geometry,
            fit_config=fit_config,
            search_config=search_config,
            problem_id=problem.problem_id,
            candidate_set_id=evidence.candidate_set_id,
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
        if retain_policy_best_remnant and remnant_action_arguments:
            best_arguments = min(
                remnant_action_arguments,
                key=lambda arguments: (
                    arguments[5].width,
                    arguments[5].candidate_id,
                    arguments[6].remnant_id,
                ),
            )
            best_action = _build_remnant_action_from_arguments(best_arguments)
            if best_action is None:
                raise ValueError("M7 fit witness did not materialize an exact remnant action")
            remnant_actions = (best_action,)
        else:
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
        materialized_standard_actions=materialized_standard_actions,
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


def _standard_action_matches_profile(
    action: M7LayoutActionEvidence,
    profile: M7StandardActionProfile,
) -> bool:
    return (
        action.candidate_id == profile.candidate_id
        and action.accounting == profile.accounting
        and len(action.returned_remnants) == profile.returned_remnant_count
        and abs(_returned_regularity(action) - profile.returned_regularity) <= 1e-9
    )


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


def _build_standard_profile_from_arguments(arguments) -> _BuiltStandardProfile:  # type: ignore[no-untyped-def]
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
    result = accelerated
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
        result = authoritative
    return result, _JaguaChunkMetrics(
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


@dataclass(frozen=True)
class _JaguaExecutableCapture:
    payload: dict[str, object]
    content: bytes


def _capture_jagua_executable(path: Path | None) -> _JaguaExecutableCapture | None:
    if path is None:
        return None
    executable = Path(path)
    try:
        resolved = executable.resolve(strict=True)
        before = resolved.stat()
    except OSError as error:
        raise ValueError("M7 Jagua executable cannot be read for semantic binding") from error
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("M7 Jagua executable must resolve to one regular file")
    try:
        content = resolved.read_bytes()
        after = resolved.stat()
    except OSError as error:
        raise ValueError("M7 Jagua executable cannot be read for semantic binding") from error
    if not stat.S_ISREG(after.st_mode):
        raise ValueError("M7 Jagua executable must resolve to one regular file")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("M7 Jagua executable changed during semantic binding")
    executable_mode = stat.S_IMODE(after.st_mode) & 0o111
    if executable_mode == 0:
        raise ValueError("M7 Jagua executable has no executable permission bits")
    return _JaguaExecutableCapture(
        payload={
            "resolved_path": str(resolved),
            "content_sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "size_bytes": len(content),
            "regular_file": True,
            "executable_mode_bits": executable_mode,
        },
        content=content,
    )


def _jagua_executable_payload(path: Path | None) -> dict[str, object] | None:
    capture = _capture_jagua_executable(path)
    return capture.payload if capture is not None else None


def _semantic_runtime_payload(
    runtime: M7ReplayRuntime,
    *,
    jagua_executable_payload: dict[str, object] | None = None,
    use_jagua_payload_override: bool = False,
) -> dict[str, object]:
    _validate_runtime(runtime)
    try:
        replay_input = M7ReplayInput.model_validate(
            runtime.replay_input.model_dump(mode="python"),
            strict=True,
        )
        rules = ResidualRuleSet.model_validate(
            runtime.rules.model_dump(mode="python"),
            strict=True,
        )
    except (AttributeError, ValueError) as error:
        raise ValueError("M7 semantic runtime contains noncanonical frozen input") from error
    if replay_input != runtime.replay_input or rules != runtime.rules:
        raise ValueError("M7 semantic runtime differs from canonical frozen input")

    candidate_mapping = []
    for problem_id in sorted(runtime.runtime_candidates):
        verified = runtime.runtime_candidates[problem_id]
        try:
            evidence = M7CandidateSetEvidence.model_validate(
                verified.evidence.model_dump(mode="python"),
                strict=True,
            )
            candidates = tuple(
                Candidate.model_validate(candidate.model_dump(mode="python"), strict=True)
                for candidate in verified.candidates
            )
        except (AttributeError, ValueError) as error:
            raise ValueError("M7 semantic runtime contains noncanonical candidates") from error
        candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
        if (
            evidence != verified.evidence
            or candidates != verified.candidates
            or evidence.problem_id != problem_id
            or candidate_ids != tuple(sorted(set(candidate_ids)))
            or candidate_ids != evidence.candidate_ids
        ):
            raise ValueError("M7 semantic runtime candidate mapping is inconsistent")
        candidate_mapping.append(
            {
                "problem_id": problem_id,
                "verified_evidence": evidence.model_dump(mode="json"),
                "candidates": tuple(
                    candidate.model_dump(mode="json") for candidate in candidates
                ),
            }
        )
    return {
        "schema_version": "yieldforge.m7-semantic-runtime.v1",
        "replay_input_id": replay_input.input_id,
        "replay_input_sha256": replay_input.content_sha256,
        "replay_input": replay_input.model_dump(mode="json"),
        "residual_rules": rules.model_dump(mode="json"),
        "runtime_candidates": tuple(candidate_mapping),
        "collision_backend": replay_input.collision_backend,
        "jagua_container_guard": replay_input.jagua_container_guard,
        "jagua_executable": (
            jagua_executable_payload
            if use_jagua_payload_override
            else _jagua_executable_payload(runtime.jagua_executable)
        ),
        "jagua_differential_audit": runtime.jagua_differential_audit,
    }


def m7_semantic_runtime_sha256(runtime: M7ReplayRuntime) -> str:
    """Hash every outcome-affecting value in one exact M7 replay runtime."""

    return f"sha256:{semantic_sha256(_semantic_runtime_payload(runtime))}"


def _private_jagua_content_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _validate_private_jagua_directory(private: _PrivateJaguaResources) -> None:
    try:
        current = os.lstat(private.directory)
    except OSError as error:
        raise ValueError("M7 private Jagua executable directory is unavailable") from error
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_dev != private.directory_device
        or current.st_ino != private.directory_inode
        or stat.S_IMODE(current.st_mode) != private.directory_mode
    ):
        raise ValueError("M7 private Jagua executable directory identity differs")


def _read_private_jagua_file(identity: _PrivateJaguaFileIdentity) -> bytes:
    try:
        before = os.lstat(identity.path)
    except OSError as error:
        raise ValueError("M7 private Jagua executable is unavailable") from error
    expected = (
        identity.device,
        identity.inode,
        identity.mode,
        identity.size_bytes,
    )
    observed = (
        before.st_dev,
        before.st_ino,
        stat.S_IMODE(before.st_mode),
        before.st_size,
    )
    if not stat.S_ISREG(before.st_mode) or observed != expected:
        raise ValueError("M7 private Jagua executable identity differs")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(identity.path, flags)
    except OSError as error:
        raise ValueError("M7 private Jagua executable cannot be opened safely") from error
    try:
        try:
            opened = os.fstat(descriptor)
            opened_identity = (
                opened.st_dev,
                opened.st_ino,
                stat.S_IMODE(opened.st_mode),
                opened.st_size,
            )
            if not stat.S_ISREG(opened.st_mode) or opened_identity != expected:
                raise ValueError("M7 private Jagua executable changed while opening")
            chunks = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            content = b"".join(chunks)
        except OSError as error:
            raise ValueError("M7 private Jagua executable changed while reading") from error
    finally:
        os.close(descriptor)
    try:
        after = os.lstat(identity.path)
    except OSError as error:
        raise ValueError("M7 private Jagua executable changed while reading") from error
    after_identity = (
        after.st_dev,
        after.st_ino,
        stat.S_IMODE(after.st_mode),
        after.st_size,
    )
    if not stat.S_ISREG(after.st_mode) or after_identity != expected:
        raise ValueError("M7 private Jagua executable changed while reading")
    return content


def _validate_private_jagua_file(
    identity: _PrivateJaguaFileIdentity,
    *,
    expected_content: bytes,
) -> None:
    content = _read_private_jagua_file(identity)
    if (
        content != expected_content
        or _private_jagua_content_sha256(content) != identity.content_sha256
    ):
        raise ValueError("M7 private Jagua executable content differs")


def _validate_private_jagua_resources(private: _PrivateJaguaResources) -> None:
    _validate_private_jagua_directory(private)
    _validate_private_jagua_file(private.executable, expected_content=private.content)
    _validate_private_jagua_directory(private)


def _write_private_jagua_content(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("private Jagua executable write made no progress")
        remaining = remaining[written:]


def _materialize_private_jagua_file(
    private: _PrivateJaguaResources,
    *,
    prefix: str,
) -> _PrivateJaguaFileIdentity:
    _validate_private_jagua_directory(private)
    content_sha256 = _private_jagua_content_sha256(private.content)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    last_error: OSError | None = None
    for _attempt in range(3):
        target = private.directory / f"{prefix}-{secrets.token_hex(24)}"
        try:
            descriptor = os.open(target, flags, 0o500)
        except OSError as error:
            last_error = error
            continue
        try:
            os.fchmod(descriptor, 0o500)
            _write_private_jagua_content(descriptor, private.content)
            os.fsync(descriptor)
            created = os.fstat(descriptor)
        except OSError:
            os.close(descriptor)
            try:
                target.unlink()
            except OSError:
                pass
            raise
        os.close(descriptor)
        identity = _PrivateJaguaFileIdentity(
            path=target,
            device=created.st_dev,
            inode=created.st_ino,
            mode=stat.S_IMODE(created.st_mode),
            size_bytes=created.st_size,
            content_sha256=content_sha256,
        )
        try:
            _validate_private_jagua_file(identity, expected_content=private.content)
        except ValueError:
            _unlink_owned_private_file(identity)
            raise
        return identity
    raise ValueError("M7 private Jagua executable lease cannot be created") from last_error


def _unlink_owned_private_file(identity: _PrivateJaguaFileIdentity) -> None:
    try:
        current = os.lstat(identity.path)
    except OSError:
        return
    if current.st_dev != identity.device or current.st_ino != identity.inode:
        return
    try:
        identity.path.unlink()
    except OSError:
        return


def _private_jagua_resources(
    capture: _JaguaExecutableCapture | None,
) -> _PrivateJaguaResources | None:
    if capture is None:
        return None
    directory = Path(tempfile.mkdtemp(prefix="yieldforge-m8-jagua-")).resolve(
        strict=True
    )
    directory.chmod(0o700)
    created_directory = os.lstat(directory)
    placeholder = _PrivateJaguaFileIdentity(
        path=directory / "unmaterialized",
        device=-1,
        inode=-1,
        mode=0o500,
        size_bytes=len(capture.content),
        content_sha256=_private_jagua_content_sha256(capture.content),
    )
    private = _PrivateJaguaResources(
        directory=directory,
        directory_device=created_directory.st_dev,
        directory_inode=created_directory.st_ino,
        directory_mode=stat.S_IMODE(created_directory.st_mode),
        executable=placeholder,
        content=capture.content,
    )
    try:
        executable = _materialize_private_jagua_file(private, prefix="bound")
        private = _PrivateJaguaResources(
            directory=directory,
            directory_device=created_directory.st_dev,
            directory_inode=created_directory.st_ino,
            directory_mode=stat.S_IMODE(created_directory.st_mode),
            executable=executable,
            content=capture.content,
        )
        _validate_private_jagua_resources(private)
        return private
    except (OSError, ValueError):
        _unlink_owned_private_file(private.executable)
        try:
            directory.rmdir()
        except OSError:
            pass
        raise


def snapshot_m7_replay_runtime(
    runtime: M7ReplayRuntime,
    *,
    maximum_capture_attempts: int = 3,
    copy_operational_caches: bool = True,
) -> M7SemanticRuntimeSnapshot:
    """Deep-capture one stable semantic runtime with isolated caches and Jagua bytes."""

    if maximum_capture_attempts < 1:
        raise ValueError("M7 semantic runtime capture requires at least one attempt")
    last_error: Exception | None = None
    for _attempt in range(maximum_capture_attempts):
        try:
            before_sha256 = m7_semantic_runtime_sha256(runtime)
            replay_input = copy.deepcopy(runtime.replay_input)
            rules = copy.deepcopy(runtime.rules)
            runtime_candidates = copy.deepcopy(runtime.runtime_candidates)
            if copy_operational_caches:
                standard_profile_cache = copy.deepcopy(runtime.standard_profile_cache)
                fit_search_cache = copy.deepcopy(runtime.fit_search_cache)
                shared_fit_search_cache = copy.deepcopy(runtime.shared_fit_search_cache)
                prepared_layout_cache = copy.deepcopy(runtime.prepared_layout_cache)
            else:
                standard_profile_cache = {}
                fit_search_cache = {}
                shared_fit_search_cache = {}
                prepared_layout_cache = OrderedDict()
            jagua_capture = _capture_jagua_executable(runtime.jagua_executable)
            jagua_differential_audit = runtime.jagua_differential_audit
            comparison_runtime = M7ReplayRuntime(
                replay_input=replay_input,
                runtime_candidates=runtime_candidates,
                rules=rules,
                standard_profile_cache=standard_profile_cache,
                fit_search_cache=fit_search_cache,
                shared_fit_search_cache=shared_fit_search_cache,
                prepared_layout_cache=prepared_layout_cache,
                jagua_executable=runtime.jagua_executable,
                jagua_differential_audit=jagua_differential_audit,
            )
            captured_sha256 = f"sha256:{semantic_sha256(_semantic_runtime_payload(
                comparison_runtime,
                jagua_executable_payload=(
                    jagua_capture.payload if jagua_capture is not None else None
                ),
                use_jagua_payload_override=True,
            ))}"
            after_sha256 = m7_semantic_runtime_sha256(runtime)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            last_error = error
            continue
        if before_sha256 != captured_sha256 or captured_sha256 != after_sha256:
            last_error = ValueError("M7 semantic runtime changed during snapshot capture")
            continue

        private = None
        try:
            private = _private_jagua_resources(jagua_capture)
            snapshot_runtime = _M7SnapshotReplayRuntime(
                replay_input=replay_input,
                runtime_candidates=MappingProxyType(runtime_candidates),
                rules=rules,
                standard_profile_cache=standard_profile_cache,
                fit_search_cache=fit_search_cache,
                shared_fit_search_cache=shared_fit_search_cache,
                prepared_layout_cache=prepared_layout_cache,
                jagua_executable=(private.executable.path if private is not None else None),
                jagua_differential_audit=jagua_differential_audit,
            )
            snapshot_runtime.seal()
            snapshot_sha256 = f"sha256:{semantic_sha256(_semantic_runtime_payload(
                snapshot_runtime,
                jagua_executable_payload=(
                    jagua_capture.payload if jagua_capture is not None else None
                ),
                use_jagua_payload_override=True,
            ))}"
            final_source_sha256 = m7_semantic_runtime_sha256(runtime)
            if snapshot_sha256 != captured_sha256 or final_source_sha256 != captured_sha256:
                raise ValueError("M7 semantic runtime changed during snapshot finalization")
            return M7SemanticRuntimeSnapshot(
                runtime=snapshot_runtime,
                semantic_sha256=captured_sha256,
                _owner_pid=os.getpid(),
                _jagua_private=private,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            if private is not None:
                snapshot = M7SemanticRuntimeSnapshot(
                    runtime=comparison_runtime,
                    semantic_sha256=captured_sha256,
                    _owner_pid=os.getpid(),
                    _jagua_private=private,
                )
                snapshot.close()
            last_error = error
            continue
    raise ValueError("M7 semantic runtime could not be captured consistently") from last_error


@contextmanager
def authoritative_m7_proof_runtime(
    runtime: M7ReplayRuntime,
) -> Iterator[M7AuthoritativeProofRuntime]:
    """Capture one stable semantic runtime with fresh proof-owned operational caches."""

    snapshot = snapshot_m7_replay_runtime(
        runtime,
        copy_operational_caches=False,
    )
    authority: M7AuthoritativeProofRuntime | None = None
    try:
        with snapshot.runtime_for_proof() as proof_runtime:
            authority = M7AuthoritativeProofRuntime(
                runtime=proof_runtime,
                semantic_sha256=snapshot.semantic_sha256,
                _snapshot=snapshot,
            )
            key = id(authority)

            def discard(
                reference: weakref.ReferenceType[M7AuthoritativeProofRuntime],
            ) -> None:
                registered = _AUTHORITATIVE_PROOF_RUNTIME_REGISTRY.get(key)
                if registered is not None and registered[0] is reference:
                    _AUTHORITATIVE_PROOF_RUNTIME_REGISTRY.pop(key, None)

            reference = weakref.ref(authority, discard)
            _AUTHORITATIVE_PROOF_RUNTIME_REGISTRY[key] = (
                reference,
                os.getpid(),
                _authoritative_proof_runtime_fingerprint(authority),
            )
            yield authority
    finally:
        integrity_error = None
        if authority is not None:
            try:
                authority.require_active()
            except ValueError as error:
                integrity_error = error
            registered = _AUTHORITATIVE_PROOF_RUNTIME_REGISTRY.get(id(authority))
            if registered is not None and registered[0]() is authority:
                _AUTHORITATIVE_PROOF_RUNTIME_REGISTRY.pop(id(authority), None)
        snapshot.close()
        if integrity_error is not None:
            raise integrity_error


def _canonical_cursor_payload(cursor: M7ReplayCursor) -> dict[str, object]:
    if not isinstance(cursor, M7ReplayCursor):
        raise ValueError("M7 cursor must use the exact runtime cursor type")
    if type(cursor.next_event_position) is not int or cursor.next_event_position < 0:
        raise ValueError("M7 cursor event position must be a nonnegative integer")
    if type(cursor.timestamp_group_sequence) is not int:
        raise ValueError("M7 cursor timestamp group sequence must be an integer")
    if type(cursor.timestamp_subsequence) is not int or cursor.timestamp_subsequence < 0:
        raise ValueError("M7 cursor timestamp subsequence must be a nonnegative integer")
    try:
        current_time = _utc(cursor.current_time, "M7 cursor current time")
        previous_release = (
            _utc(cursor.previous_release, "M7 cursor previous release")
            if cursor.previous_release is not None
            else None
        )
        inventory = tuple(
            InventoryItem.model_validate(item.model_dump(mode="python"), strict=True)
            for item in cursor.inventory
        )
        cumulative = ReplayCostLedger.model_validate(
            cursor.cumulative_costs.model_dump(mode="python"),
            strict=True,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("M7 cursor contains malformed canonical state") from error
    if inventory != cursor.inventory or cumulative != cursor.cumulative_costs:
        raise ValueError("M7 cursor differs from its canonical state")
    inventory_ids = _inventory_ids(inventory)
    if inventory_ids != tuple(sorted(set(inventory_ids))):
        raise ValueError("M7 cursor inventory must use sorted unique identities")
    return {
        "next_event_position": cursor.next_event_position,
        "current_time": current_time.isoformat().replace("+00:00", "Z"),
        "inventory": tuple(item.model_dump(mode="json") for item in inventory),
        "cumulative_costs": cumulative.model_dump(mode="json"),
        "timestamp_group_sequence": cursor.timestamp_group_sequence,
        "timestamp_subsequence": cursor.timestamp_subsequence,
        "previous_release": (
            previous_release.isoformat().replace("+00:00", "Z")
            if previous_release is not None
            else None
        ),
    }


def m7_cursor_sha256(cursor: M7ReplayCursor) -> str:
    """Hash every canonical field in one exact runtime replay cursor."""

    return f"sha256:{semantic_sha256(_canonical_cursor_payload(cursor))}"


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
    complete: bool = True,
    materialize_standard_actions: bool = False,
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
        retain_all_remnant_actions=complete,
        materialize_standard_actions=materialize_standard_actions,
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
    if complete and len(actions) != (
        len(generated.standard_profiles) + generated.remnant_action_count
    ):
        raise ValueError("M7 complete action catalog count does not reconcile")
    catalog = M7ActionCatalog(
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
    if materialize_standard_actions:
        _issue_materialized_standard_action_capability(
            runtime,
            cursor=cursor,
            catalog=catalog,
            problem=problem,
            verified=verified,
        )
    return catalog


def enumerate_m7_pruned_action_catalog(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    zero_generation_rejected_inventory: tuple[InventoryItem, ...],
    precomputed_standard_profiles: tuple[M7StandardActionProfile, ...] | None = None,
) -> M7ActionCatalog:
    """Enumerate an exact lazy catalog after external zero-generation proofs.

    Registered translation and collision search runs only for unresolved
    inventory.  Proven rejects still contribute their exact zero-generation
    query counts, so the returned catalog and applied event remain identical to
    ``enumerate_m7_action_catalog(..., complete=False)``.
    """

    _validate_runtime(runtime)
    position = cursor.next_event_position
    if position < 0 or position >= len(runtime.replay_input.instances):
        raise ValueError("M7 pruned catalog position is outside the replay stream")
    rejected_ids = tuple(
        item.remnant.remnant_id for item in zero_generation_rejected_inventory
    )
    if len(rejected_ids) != len(set(rejected_ids)):
        raise ValueError("M7 pruned rejection inventory contains duplicate identities")
    cursor_by_id = {item.remnant.remnant_id: item for item in cursor.inventory}
    if any(
        cursor_by_id.get(item.remnant.remnant_id) != item
        for item in zero_generation_rejected_inventory
    ):
        raise ValueError("M7 pruned rejection inventory differs from cursor")
    rejected_id_set = set(rejected_ids)
    survivors = tuple(
        item
        for item in cursor.inventory
        if item.remnant.remnant_id not in rejected_id_set
    )
    if not zero_generation_rejected_inventory or not survivors:
        raise ValueError("M7 mixed pruned catalog requires rejects and survivors")

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
    problem = next(
        item for item in replay_input.problems if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    if precomputed_standard_profiles is not None:
        if tuple(item.candidate_id for item in precomputed_standard_profiles) != tuple(
            item.candidate_id for item in verified.candidates
        ):
            raise ValueError("M7 precomputed standard profiles differ from candidates")
        for profile in precomputed_standard_profiles:
            runtime.standard_profile_cache[(problem.problem_id, profile.candidate_id)] = profile

    generated_survivors = _generate_actions(
        binding=binding,
        problem=problem,
        verified=verified,
        inventory=survivors,
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
        retain_all_remnant_actions=False,
    )
    compatible_rejected_count = sum(
        material_key(item.remnant.material) == material_key(binding.material)
        for item in zero_generation_rejected_inventory
    )
    generated = GeneratedActionSet(
        standard_profiles=generated_survivors.standard_profiles,
        remnant_actions=generated_survivors.remnant_actions,
        remnant_action_count=generated_survivors.remnant_action_count,
        fit_search_query_count=(
            generated_survivors.fit_search_query_count
            + compatible_rejected_count * len(verified.candidates)
        ),
        fit_search_generated_candidate_count=(
            generated_survivors.fit_search_generated_candidate_count
        ),
        fit_search_evaluated_candidate_count=(
            generated_survivors.fit_search_evaluated_candidate_count
        ),
        fit_search_budget_truncated_count=(
            generated_survivors.fit_search_budget_truncated_count
        ),
        materialized_standard_actions=(generated_survivors.materialized_standard_actions),
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


def enumerate_m7_standard_only_catalog(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    zero_generation_rejected_inventory: tuple[InventoryItem, ...],
    precomputed_standard_profiles: tuple[M7StandardActionProfile, ...] | None = None,
) -> M7ActionCatalog:
    """Materialize the exact standard catalog after an external no-fit proof.

    The caller must prove that every compatible remnant/candidate query is
    rejected before translation generation.  Keeping that proof outside the M7
    baseline avoids teaching the authoritative replay to trust M8 shortcuts,
    while this helper preserves the exact event bookkeeping the authoritative
    zero-generation searches would have emitted.
    """

    _validate_runtime(runtime)
    if zero_generation_rejected_inventory != cursor.inventory:
        raise ValueError("M7 standard-only rejection inventory differs from cursor")
    position = cursor.next_event_position
    if position < 0 or position >= len(runtime.replay_input.instances):
        raise ValueError("M7 standard-only catalog position is outside the replay stream")
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
    problem = next(
        item for item in replay_input.problems if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    if precomputed_standard_profiles is None:
        standards = _generate_actions(
            binding=binding,
            problem=problem,
            verified=verified,
            inventory=(),
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
            retain_all_remnant_actions=False,
        )
        profiles = standards.standard_profiles
        materialized_standard_actions = standards.materialized_standard_actions
    else:
        profiles = precomputed_standard_profiles
        materialized_standard_actions = ()
        if tuple(item.candidate_id for item in profiles) != tuple(
            item.candidate_id for item in verified.candidates
        ):
            raise ValueError("M7 precomputed standard profiles differ from candidates")
        for profile in profiles:
            runtime.standard_profile_cache[(problem.problem_id, profile.candidate_id)] = profile
    compatible_count = sum(
        material_key(item.remnant.material) == material_key(binding.material)
        for item in zero_generation_rejected_inventory
    )
    generated = GeneratedActionSet(
        standard_profiles=profiles,
        remnant_actions=(),
        remnant_action_count=0,
        fit_search_query_count=compatible_count * len(verified.candidates),
        fit_search_generated_candidate_count=0,
        fit_search_evaluated_candidate_count=0,
        fit_search_budget_truncated_count=0,
        materialized_standard_actions=materialized_standard_actions,
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
    )
    return M7ActionCatalog(
        event_position=position,
        actions=actions,
        contexts=contexts,
        standard_action_count=len(generated.standard_profiles),
        remnant_action_count=0,
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


def enumerate_m7_single_remnant_competitor(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    cursor_template: M7ReplayCursor,
) -> tuple[M7ActionDescriptor | None, ActionPolicyContext | None]:
    """Return the exact best feasible action for one remnant at one M7 event."""

    _validate_runtime(runtime)
    if event_position != cursor_template.next_event_position:
        raise ValueError("M7 single-remnant event position differs from cursor")
    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M7 single-remnant event position is outside the replay stream")
    replay_input = runtime.replay_input
    binding = replay_input.instances[event_position]
    problem = next(
        problem
        for problem in replay_input.problems
        if problem.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    generated = _generate_actions(
        binding=binding,
        problem=problem,
        verified=verified,
        inventory=(item,),
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
        retain_all_remnant_actions=False,
        retain_policy_best_remnant=True,
    )
    if not generated.remnant_actions:
        return None, None
    if len(generated.remnant_actions) != 1:
        raise ValueError("M7 single-remnant search retained more than one policy competitor")
    action = generated.remnant_actions[0]
    contexts = _policy_contexts(
        generated,
        candidates=verified.candidates,
        inventory=(item,),
        occurred_at=binding.released_at,
        rates=replay_input.rates,
    )
    matching = tuple(context for context in contexts if context.action_id == action.action_id)
    if len(matching) != 1:
        raise ValueError("M7 single-remnant competitor lacks one exact policy context")
    return (
        M7ActionDescriptor(
            action_id=action.action_id,
            kind=action.kind,
            candidate_id=action.candidate_id,
            selected_remnant_id=action.selected_remnant_id,
            evidence=action,
        ),
        matching[0],
    )


@dataclass(frozen=True)
class _M7AppliedAction:
    storage_cost: float
    timestamp_group_sequence: int
    timestamp_subsequence: int
    inventory_after: tuple[InventoryItem, ...]
    delta_costs: ReplayCostLedger
    cumulative_costs: ReplayCostLedger
    cursor: M7ReplayCursor
    cursor_before_sha256: str
    cursor_after_sha256: str


def _canonical_materialized_action(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    event_position: int,
    action: M7LayoutActionEvidence,
    bound_problem: ReusableGeometryProblem | None = None,
    bound_verified: VerifiedProblemCandidates | None = None,
    bound_candidate: Candidate | None = None,
) -> M7LayoutActionEvidence:
    try:
        canonical = M7LayoutActionEvidence.model_validate(
            action.model_dump(mode="python"),
            strict=True,
        )
    except (AttributeError, ValueError) as error:
        raise ValueError("M7 frozen action evidence is malformed") from error
    if canonical != action:
        raise ValueError("M7 frozen action evidence differs from canonical content")
    replay_input = runtime.replay_input
    if event_position < 0 or event_position >= len(replay_input.instances):
        raise ValueError("M7 frozen action position is outside the replay stream")
    binding = replay_input.instances[event_position]
    capability_bound = (
        bound_problem is not None
        and bound_verified is not None
        and bound_candidate is not None
    )
    if capability_bound:
        problem = bound_problem
        verified = bound_verified
        candidate_matches = canonical.candidate_id == bound_candidate.candidate_id
        if (
            problem.problem_id != binding.problem_id
            or runtime.runtime_candidates.get(binding.problem_id) is not verified
        ):
            raise ValueError("M7 frozen action capability binding differs from runtime")
    else:
        if any(
            item is not None
            for item in (bound_problem, bound_verified, bound_candidate)
        ):
            raise ValueError("M7 frozen action capability binding is incomplete")
        problem = next(
            item for item in replay_input.problems if item.problem_id == binding.problem_id
        )
        verified = runtime.runtime_candidates[binding.problem_id]
        candidate_matches = canonical.candidate_id in {
            item.candidate_id for item in verified.candidates
        }
    if (
        canonical.problem_id != problem.problem_id
        or canonical.problem_sha256 != problem.content_sha256
        or canonical.candidate_set_id != verified.evidence.candidate_set_id
        or canonical.candidate_set_sha256 != verified.evidence.content_sha256
        or not candidate_matches
        or canonical.selected_stock.material != binding.material
    ):
        raise ValueError("M7 frozen action evidence differs from the frozen replay input")
    if canonical.kind is M7ActionKind.OPEN_STANDARD_SHEET:
        if canonical.selected_stock.lineage.root_stock_id != binding.binding_id:
            raise ValueError("M7 frozen standard action belongs to another event binding")
    else:
        selected_id = canonical.selected_remnant_id
        inventory_by_id = {item.remnant.remnant_id: item for item in cursor.inventory}
        if selected_id is None or selected_id not in inventory_by_id:
            raise ValueError("M7 selected remnant is missing from inventory")
        if inventory_by_id[selected_id].remnant != canonical.selected_stock:
            raise ValueError("M7 frozen remnant action differs from branch inventory")
        search = canonical.search_result
        if (
            search is None
            or search.remnant_id != selected_id
            or search.candidate_id != canonical.candidate_id
            or search.config != replay_input.search_config
        ):
            raise ValueError("M7 frozen remnant search differs from the frozen replay input")
    return canonical


def _apply_m7_materialized_action(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    event_position: int,
    action: M7LayoutActionEvidence,
    bound_problem: ReusableGeometryProblem | None = None,
    bound_verified: VerifiedProblemCandidates | None = None,
    bound_candidate: Candidate | None = None,
) -> _M7AppliedAction:
    _validate_runtime(runtime)
    cursor_before_sha256 = m7_cursor_sha256(cursor)
    if event_position != cursor.next_event_position:
        raise ValueError("M7 frozen action position differs from cursor")
    canonical = _canonical_materialized_action(
        runtime,
        cursor=cursor,
        event_position=event_position,
        action=action,
        bound_problem=bound_problem,
        bound_verified=bound_verified,
        bound_candidate=bound_candidate,
    )
    replay_input = runtime.replay_input
    binding = replay_input.instances[event_position]
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
    inventory_after = _execute_action(canonical, cursor.inventory, binding.released_at)
    delta = _ledger(
        purchase_cost=(
            canonical.selected_stock.geometry.area * replay_input.rates.purchase_cost_per_area
            if canonical.kind is M7ActionKind.OPEN_STANDARD_SHEET
            else 0.0
        ),
        storage_cost=storage,
        return_handling_cost=(
            len(canonical.returned_remnants)
            * replay_input.rates.return_handling_cost_per_remnant
        ),
        retrieval_handling_cost=(
            replay_input.rates.retrieval_handling_cost_per_remnant
            if canonical.kind is M7ActionKind.CONSUME_REMNANT
            else 0.0
        ),
        scrap_proceeds=(
            canonical.accounting.scrap_area * replay_input.rates.scrap_credit_per_area
        ),
    )
    cumulative = _add_ledgers(cursor.cumulative_costs, delta)
    next_cursor = M7ReplayCursor(
        next_event_position=event_position + 1,
        current_time=binding.released_at,
        inventory=inventory_after,
        cumulative_costs=cumulative,
        timestamp_group_sequence=group_sequence,
        timestamp_subsequence=group_subsequence,
        previous_release=binding.released_at,
    )
    cursor_after_sha256 = m7_cursor_sha256(next_cursor)
    return _M7AppliedAction(
        storage_cost=storage,
        timestamp_group_sequence=group_sequence,
        timestamp_subsequence=group_subsequence,
        inventory_after=inventory_after,
        delta_costs=delta,
        cumulative_costs=cumulative,
        cursor=next_cursor,
        cursor_before_sha256=cursor_before_sha256,
        cursor_after_sha256=cursor_after_sha256,
    )


def apply_m7_frozen_action_evidence(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    event_position: int,
    action: M7LayoutActionEvidence,
) -> M7ReplayCursor:
    """Apply one validated materialized action without enumerating losing actions."""

    return apply_m7_frozen_action_evidence_with_commitments(
        runtime,
        cursor=cursor,
        event_position=event_position,
        action=action,
    ).cursor


def apply_m7_frozen_action_evidence_with_commitments(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    event_position: int,
    action: M7LayoutActionEvidence,
) -> M7CursorTransition:
    """Apply frozen evidence and retain the exact cursor hashes already validated."""

    applied = _apply_m7_materialized_action(
        runtime,
        cursor=cursor,
        event_position=event_position,
        action=action,
    )
    return _trusted_m7_cursor_transition(applied)


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
    replay_input = runtime.replay_input
    retained_record: _MaterializedStandardActionRecord | None = None
    retained_entry: _MaterializedStandardActionEntry | None = None
    if (
        descriptor.kind is M7ActionKind.OPEN_STANDARD_SHEET
        and catalog.generated.materialized_standard_actions
    ):
        retained_record, retained_entry = _require_materialized_standard_action_capability(
            runtime,
            cursor=cursor,
            catalog=catalog,
            descriptor=descriptor,
        )
        selected_context = retained_entry.context
        binding = retained_record.binding
        problem = retained_record.problem
        verified = retained_record.verified
    else:
        registered = {item.action_id: item for item in catalog.actions}
        if registered.get(descriptor.action_id) != descriptor:
            raise ValueError("M7 action descriptor is absent from the catalog")
        selected_contexts = tuple(
            item for item in catalog.contexts if item.action_id == descriptor.action_id
        )
        if len(selected_contexts) != 1:
            raise ValueError("M7 action descriptor must have exactly one policy context")
        selected_context = selected_contexts[0]
        binding = replay_input.instances[catalog.event_position]
        problem = next(
            item for item in replay_input.problems if item.problem_id == binding.problem_id
        )
        verified = runtime.runtime_candidates[binding.problem_id]
    if descriptor.kind is M7ActionKind.OPEN_STANDARD_SHEET:
        if retained_entry is not None:
            candidate = retained_entry.candidate
            profile = retained_entry.profile
            action = retained_entry.action
        else:
            candidate = next(
                item
                for item in verified.candidates
                if item.candidate_id == descriptor.candidate_id
            )
            matching_profiles = tuple(
                item
                for item in catalog.generated.standard_profiles
                if item.candidate_id == descriptor.candidate_id
            )
            if len(matching_profiles) != 1:
                raise ValueError("M7 standard action must have exactly one catalog profile")
            catalog_profile = matching_profiles[0]
            profile = runtime.standard_profile_cache.get(
                (problem.problem_id, descriptor.candidate_id)
            )
            if (
                profile is None
                or profile != catalog_profile
                or profile.candidate_width != candidate.width
            ):
                raise ValueError(
                    "M7 standard action catalog differs from its exact cached profile"
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
        if not _standard_action_matches_profile(action, profile):
            raise ValueError("M7 standard action differs from its exact cached profile")
    else:
        if descriptor.evidence is None:
            raise ValueError("M7 remnant descriptor has no exact evidence")
        action = descriptor.evidence
    applied = _apply_m7_materialized_action(
        runtime,
        cursor=cursor,
        event_position=catalog.event_position,
        action=action,
        bound_problem=(problem if retained_entry is not None else None),
        bound_verified=(verified if retained_entry is not None else None),
        bound_candidate=(candidate if retained_entry is not None else None),
    )
    if (
        applied.storage_cost != catalog.storage_cost
        or applied.timestamp_group_sequence != catalog.timestamp_group_sequence
        or applied.timestamp_subsequence != catalog.timestamp_subsequence
    ):
        raise ValueError("M7 action catalog transition metadata differs from cursor")
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
        inventory_after=applied.inventory_after,
        delta=applied.delta_costs,
        cumulative=applied.cumulative_costs,
    )
    return M7StepResult(
        descriptor=descriptor,
        selected_context=selected_context,
        action_binding=M7PolicyActionBinding(
            catalog_action_id=descriptor.action_id,
            materialized_action_id=event.action.action_id,
            context=selected_context,
        ),
        event=event,
        cursor=applied.cursor,
    )


def run_m7_continuation(
    runtime: M7ReplayRuntime,
    *,
    cursor: M7ReplayCursor,
    stop_event_position: int | None = None,
) -> M7ContinuationResult:
    """Run the unchanged frozen M7 policy from an arbitrary exact cursor."""

    stop = (
        len(runtime.replay_input.instances)
        if stop_event_position is None
        else stop_event_position
    )
    if stop < cursor.next_event_position or stop > len(runtime.replay_input.instances):
        raise ValueError("M7 continuation stop position is outside the remaining stream")
    events = []
    selected_contexts = []
    action_bindings = []
    while cursor.next_event_position < stop:
        catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=False)
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
        selected_contexts.append(step.selected_context)
        action_bindings.append(step.action_binding)
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
        selected_contexts=tuple(selected_contexts),
        action_bindings=tuple(action_bindings),
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
    "M7AuthoritativeProofRuntime",
    "M7ContinuationResult",
    "M7CursorTransition",
    "M7PolicyActionBinding",
    "M7ReplayEvent",
    "M7ReplayInput",
    "M7ReplayResult",
    "M7ReplayCursor",
    "M7ReplayRuntime",
    "M7ReplayRuntimeMetrics",
    "M7SemanticRuntimeSnapshot",
    "M7ReplaySummary",
    "M7StepResult",
    "M7StandardActionProfile",
    "M7SharedFitSearchCache",
    "M7PreparedLayoutCache",
    "apply_m7_action_descriptor",
    "apply_m7_frozen_action_evidence",
    "apply_m7_frozen_action_evidence_with_commitments",
    "authoritative_m7_proof_runtime",
    "build_m7_replay_input",
    "cursor_after_event",
    "enumerate_m7_action_catalog",
    "enumerate_m7_pruned_action_catalog",
    "enumerate_m7_standard_only_catalog",
    "enumerate_m7_single_remnant_competitor",
    "initial_m7_cursor",
    "m7_cursor_sha256",
    "m7_semantic_runtime_sha256",
    "m7_shared_fit_search_cache_key",
    "run_m7_replay",
    "run_m7_continuation",
    "select_m7_fallback",
    "snapshot_m7_replay_runtime",
]
