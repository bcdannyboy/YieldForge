"""Authenticated paired-confirmation evidence for M11 Gate 3."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Literal, Protocol, Self

import numpy as np
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.policies import M7PolicyName, policy_identity
from yieldforge.baseline.replay import (
    M7ReplayEvent,
    M7ReplayInput,
    M7ReplayResult,
    M7ReplayRuntime,
    M7StepResult,
    m7_semantic_runtime_sha256,
)
from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256
from yieldforge.oracle.search_validation import M9ExactSearchResult
from yieldforge.realistic_falsification.adapter import M11M7ProjectionAttestation
from yieldforge.realistic_falsification.pack import M11ExactAuditEpisode, M11HardNull
from yieldforge.realistic_falsification.statistics import linear_quantile, wilson_interval_percent
from yieldforge.replay.contracts import ReplayTerminalRecord

Gate3CorpusId = Literal["lectra-m3-m4", "loco-2dics"]
Gate3BaselinePolicyId = Literal[
    "myopic_geometry",
    "remnant_first",
    "net_cost",
    "age_regularity",
    "known_order_lookahead",
    "known_only_m9_two_ply_scrap",
]

GATE3_BASELINE_POLICY_IDS: tuple[Gate3BaselinePolicyId, ...] = (
    "myopic_geometry",
    "remnant_first",
    "net_cost",
    "age_regularity",
    "known_order_lookahead",
    "known_only_m9_two_ply_scrap",
)

_COST_PATTERN = r"^(?:0|[1-9][0-9]*)\.[0-9]{6}$"
_SIGNED_COST_PATTERN = r"^-?(?:0|[1-9][0-9]*)\.[0-9]{6}$"
_METRIC_PATTERN = r"^-?(?:0|[1-9][0-9]*)\.[0-9]{12}$"
_COST_QUANTUM = Decimal("0.000001")
_METRIC_QUANTUM = Decimal("0.000000000001")


def _cost(value: str, *, label: str) -> Decimal:
    if type(value) is not str:
        raise TypeError(f"{label} must be a canonical six-place cost string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{label} is not a decimal cost") from error
    if not parsed.is_finite() or parsed < 0 or parsed.quantize(_COST_QUANTUM) != parsed:
        raise ValueError(f"{label} must be finite, nonnegative, and six-place exact")
    if format(parsed, ".6f") != value:
        raise ValueError(f"{label} is not canonically encoded")
    return parsed


def _format_cost(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise ValueError("cost must be finite and nonnegative")
    return format(value.quantize(_COST_QUANTUM), ".6f")


def _signed_cost(value: str, *, label: str) -> Decimal:
    if type(value) is not str:
        raise TypeError(f"{label} must be a canonical six-place cost string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{label} is not a decimal cost") from error
    if not parsed.is_finite() or parsed.quantize(_COST_QUANTUM) != parsed:
        raise ValueError(f"{label} must be finite and six-place exact")
    if format(parsed, ".6f") != value:
        raise ValueError(f"{label} is not canonically encoded")
    return parsed


def _format_signed_cost(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("cost must be finite")
    return format(value.quantize(_COST_QUANTUM), ".6f")


def _format_metric(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("metric must be finite")
    return format(value.quantize(_METRIC_QUANTUM), ".12f")


class Gate3CostLedger(FrozenExperimentModel):
    """One exact six-place M0 ledger, suitable for additive material shards."""

    schema_version: Literal["yieldforge.m11-gate3-cost-ledger.v1"] = (
        "yieldforge.m11-gate3-cost-ledger.v1"
    )
    ledger_id: StrictStr = Field(pattern=r"^yfm11g3led-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    purchase_cost: StrictStr = Field(pattern=_COST_PATTERN)
    storage_cost: StrictStr = Field(pattern=_COST_PATTERN)
    return_handling_cost: StrictStr = Field(pattern=_COST_PATTERN)
    retrieval_handling_cost: StrictStr = Field(pattern=_COST_PATTERN)
    scrap_proceeds: StrictStr = Field(pattern=_COST_PATTERN)
    terminal_credit: StrictStr = Field(pattern=_COST_PATTERN)
    net_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)

    @model_validator(mode="after")
    def require_reconciliation_and_identity(self) -> Self:
        expected = (
            _cost(self.purchase_cost, label="purchase cost")
            + _cost(self.storage_cost, label="storage cost")
            + _cost(self.return_handling_cost, label="return handling cost")
            + _cost(self.retrieval_handling_cost, label="retrieval handling cost")
            - _cost(self.scrap_proceeds, label="scrap proceeds")
            - _cost(self.terminal_credit, label="terminal credit")
        )
        if self.net_cost != _format_signed_cost(expected):
            raise ValueError("Gate 3 cost ledger does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"ledger_id", "content_sha256"})
        if self.ledger_id != f"yfm11g3led-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 ledger identity does not match semantic content")
        return self


def build_gate3_cost_ledger(
    *,
    purchase_cost: str,
    storage_cost: str,
    return_handling_cost: str,
    retrieval_handling_cost: str,
    scrap_proceeds: str,
    terminal_credit: str,
) -> Gate3CostLedger:
    terms = {
        "purchase_cost": _cost(purchase_cost, label="purchase cost"),
        "storage_cost": _cost(storage_cost, label="storage cost"),
        "return_handling_cost": _cost(return_handling_cost, label="return handling cost"),
        "retrieval_handling_cost": _cost(
            retrieval_handling_cost,
            label="retrieval handling cost",
        ),
        "scrap_proceeds": _cost(scrap_proceeds, label="scrap proceeds"),
        "terminal_credit": _cost(terminal_credit, label="terminal credit"),
    }
    net = (
        terms["purchase_cost"]
        + terms["storage_cost"]
        + terms["return_handling_cost"]
        + terms["retrieval_handling_cost"]
        - terms["scrap_proceeds"]
        - terms["terminal_credit"]
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-cost-ledger.v1",
        **{key: _format_cost(value) for key, value in terms.items()},
        "net_cost": _format_signed_cost(net),
    }
    digest = semantic_sha256(semantic)
    return Gate3CostLedger(
        ledger_id=f"yfm11g3led-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _sum_ledgers(values: tuple[Gate3CostLedger, ...]) -> Gate3CostLedger:
    if not values:
        raise ValueError("Gate 3 ledger sum requires at least one material shard")
    fields = (
        "purchase_cost",
        "storage_cost",
        "return_handling_cost",
        "retrieval_handling_cost",
        "scrap_proceeds",
        "terminal_credit",
    )
    terms = {
        field: _format_cost(
            sum((_cost(getattr(value, field), label=field) for value in values), Decimal(0))
        )
        for field in fields
    }
    return build_gate3_cost_ledger(**terms)


Gate3Arm = Literal["B", "F", "K"]
Gate3Visibility = Literal["released_only", "full_future", "known_only"]
Gate3Algorithm = Literal["m7_policy", "m9_two_ply"]
Gate3TieRule = Literal[
    "m7_registered_policy_comparison",
    "bounded_cost_then_baseline_then_action_id",
]


class Gate3M9RootScore(FrozenExperimentModel):
    action_id: StrictStr = Field(min_length=1)
    bounded_objective_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)


class Gate3DecisionTrace(FrozenExperimentModel):
    """Raw current-state action evidence for one material-shard event."""

    schema_version: Literal["yieldforge.m11-gate3-decision-trace.v1"] = (
        "yieldforge.m11-gate3-decision-trace.v1"
    )
    decision_id: StrictStr = Field(pattern=r"^yfm11g3dec-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    event_position: StrictInt = Field(ge=0, le=23)
    event_id: StrictStr = Field(min_length=1)
    arm: Gate3Arm
    algorithm: Gate3Algorithm
    visibility: Gate3Visibility
    policy_id: StrictStr = Field(min_length=1)
    standard_candidate_set_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    search_config_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    compute_budget_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tie_rule: Gate3TieRule
    action_catalog_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    baseline_action_id: StrictStr = Field(min_length=1)
    selected_action_id: StrictStr = Field(min_length=1)
    selected_immediate_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    baseline_immediate_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    search_runtime_sha256: StrictStr | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    m9_root_scores: tuple[Gate3M9RootScore, ...] = ()
    selected_bounded_cost: StrictStr | None = Field(default=None, pattern=_SIGNED_COST_PATTERN)
    baseline_bounded_cost: StrictStr | None = Field(default=None, pattern=_SIGNED_COST_PATTERN)
    inventory_before_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    inventory_after_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    returned_lineage_root_ids: tuple[StrictStr, ...] = ()
    selected_lineage_root_id: StrictStr | None = None
    m9_catalog_count: StrictInt = Field(ge=0)
    m9_explicit_transition_count: StrictInt = Field(ge=0)
    m9_continuation_event_count: StrictInt = Field(ge=0)
    m9_start_event_position: StrictInt | None = Field(default=None, ge=0)
    m9_stop_event_position: StrictInt | None = Field(default=None, ge=1)
    m9_objective_label: Literal["scrap_only"] | None = None
    m9_objective_definition: Literal["m7_final_net_cost_including_terminal_scrap_credit"] | None = (
        None
    )
    m9_depth: Literal[2] | None = None
    m9_complete: StrictBool = False
    action_catalog_complete: Literal[True] = True
    truncated_catalog_count: Literal[0] = 0
    truncated_transition_count: Literal[0] = 0

    @model_validator(mode="after")
    def require_algorithm_evidence_and_identity(self) -> Self:
        if len(set(self.action_ids)) != len(self.action_ids):
            raise ValueError("Gate 3 action catalog IDs must be unique")
        if self.baseline_action_id not in self.action_ids or self.selected_action_id not in (
            self.action_ids
        ):
            raise ValueError("Gate 3 selected or baseline action is absent from its catalog")
        _signed_cost(self.selected_immediate_cost, label="selected immediate cost")
        _signed_cost(self.baseline_immediate_cost, label="baseline immediate cost")
        bounded = (self.selected_bounded_cost, self.baseline_bounded_cost)
        if self.algorithm == "m9_two_ply":
            if (
                any(value is None for value in bounded)
                or self.tie_rule != "bounded_cost_then_baseline_then_action_id"
                or self.visibility == "released_only"
                or self.search_runtime_sha256 is None
                or self.m9_start_event_position is None
                or self.m9_stop_event_position is None
                or self.m9_stop_event_position <= self.m9_start_event_position
                or self.m9_objective_label != "scrap_only"
                or self.m9_objective_definition
                != "m7_final_net_cost_including_terminal_scrap_credit"
                or self.m9_depth != 2
                or not self.m9_complete
                or self.m9_catalog_count <= 0
                or self.m9_explicit_transition_count <= 0
            ):
                raise ValueError("Gate 3 M9 decision lacks complete two-ply evidence")
            for value in bounded:
                _signed_cost(value, label="bounded cost")  # type: ignore[arg-type]
            if self.m9_root_scores != tuple(
                sorted(self.m9_root_scores, key=lambda item: item.action_id)
            ) or tuple(item.action_id for item in self.m9_root_scores) != tuple(
                sorted(self.action_ids)
            ):
                raise ValueError("Gate 3 M9 root score catalog differs from its action catalog")
            scores = {
                item.action_id: _signed_cost(
                    item.bounded_objective_cost,
                    label="M9 bounded objective cost",
                )
                for item in self.m9_root_scores
            }
            selected = min(
                scores,
                key=lambda action_id: (
                    scores[action_id],
                    action_id != self.baseline_action_id,
                    action_id,
                ),
            )
            if (
                self.selected_action_id != selected
                or self.selected_bounded_cost != _format_signed_cost(scores[selected])
                or self.baseline_bounded_cost
                != _format_signed_cost(scores[self.baseline_action_id])
            ):
                raise ValueError("Gate 3 mechanical two-ply selection differs from root scores")
        elif (
            any(value is not None for value in bounded)
            or self.tie_rule != "m7_registered_policy_comparison"
            or self.visibility != "released_only"
            or self.search_runtime_sha256 is not None
            or self.m9_root_scores
            or self.m9_catalog_count
            or self.m9_explicit_transition_count
            or self.m9_continuation_event_count
            or self.m9_start_event_position is not None
            or self.m9_stop_event_position is not None
            or self.m9_objective_label is not None
            or self.m9_objective_definition is not None
            or self.m9_depth is not None
            or self.m9_complete
        ):
            raise ValueError("Gate 3 M7 decision contains inconsistent two-ply evidence")
        if self.returned_lineage_root_ids != tuple(sorted(set(self.returned_lineage_root_ids))):
            raise ValueError("Gate 3 returned lineage roots must be sorted and unique")
        digest = semantic_sha256(self, excluded_fields={"decision_id", "content_sha256"})
        if self.decision_id != f"yfm11g3dec-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 decision identity does not match semantic content")
        return self

    @property
    def beneficial_divergence(self) -> bool:
        if self.algorithm != "m9_two_ply" or self.selected_action_id == self.baseline_action_id:
            return False
        return _signed_cost(
            self.selected_bounded_cost or "0.000000",
            label="selected bounded cost",
        ) < _signed_cost(
            self.baseline_bounded_cost or "0.000000",
            label="baseline bounded cost",
        )


def build_gate3_decision_trace(
    *,
    event_position: int,
    event_id: str,
    arm: Gate3Arm,
    algorithm: Gate3Algorithm,
    visibility: Gate3Visibility,
    policy_id: str,
    standard_candidate_set_sha256: str,
    search_config_sha256: str,
    compute_budget_sha256: str,
    search_runtime_sha256: str | None,
    action_catalog_sha256: str,
    action_ids: tuple[str, ...],
    baseline_action_id: str,
    selected_action_id: str,
    selected_immediate_cost: str,
    baseline_immediate_cost: str,
    m9_root_scores: tuple[tuple[str, str], ...],
    inventory_before_sha256: str,
    inventory_after_sha256: str,
    returned_lineage_root_ids: tuple[str, ...],
    selected_lineage_root_id: str | None,
    m9_catalog_count: int,
    m9_explicit_transition_count: int,
    m9_continuation_event_count: int,
    m9_start_event_position: int | None,
    m9_stop_event_position: int | None,
) -> Gate3DecisionTrace:
    tie_rule: Gate3TieRule = (
        "bounded_cost_then_baseline_then_action_id"
        if algorithm == "m9_two_ply"
        else "m7_registered_policy_comparison"
    )
    root_scores = tuple(
        sorted(
            (
                Gate3M9RootScore(
                    action_id=action_id,
                    bounded_objective_cost=cost,
                )
                for action_id, cost in m9_root_scores
            ),
            key=lambda item: item.action_id,
        )
    )
    scores = {
        item.action_id: _signed_cost(
            item.bounded_objective_cost,
            label="M9 bounded objective cost",
        )
        for item in root_scores
    }
    selected_bounded_cost = (
        _format_signed_cost(scores[selected_action_id]) if algorithm == "m9_two_ply" else None
    )
    baseline_bounded_cost = (
        _format_signed_cost(scores[baseline_action_id]) if algorithm == "m9_two_ply" else None
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-decision-trace.v1",
        "event_position": event_position,
        "event_id": event_id,
        "arm": arm,
        "algorithm": algorithm,
        "visibility": visibility,
        "policy_id": policy_id,
        "standard_candidate_set_sha256": standard_candidate_set_sha256,
        "search_config_sha256": search_config_sha256,
        "compute_budget_sha256": compute_budget_sha256,
        "tie_rule": tie_rule,
        "action_catalog_sha256": action_catalog_sha256,
        "action_ids": action_ids,
        "baseline_action_id": baseline_action_id,
        "selected_action_id": selected_action_id,
        "selected_immediate_cost": selected_immediate_cost,
        "baseline_immediate_cost": baseline_immediate_cost,
        "search_runtime_sha256": search_runtime_sha256,
        "m9_root_scores": tuple(item.model_dump(mode="json") for item in root_scores),
        "selected_bounded_cost": selected_bounded_cost,
        "baseline_bounded_cost": baseline_bounded_cost,
        "inventory_before_sha256": inventory_before_sha256,
        "inventory_after_sha256": inventory_after_sha256,
        "returned_lineage_root_ids": tuple(sorted(returned_lineage_root_ids)),
        "selected_lineage_root_id": selected_lineage_root_id,
        "m9_catalog_count": m9_catalog_count,
        "m9_explicit_transition_count": m9_explicit_transition_count,
        "m9_continuation_event_count": m9_continuation_event_count,
        "m9_start_event_position": m9_start_event_position,
        "m9_stop_event_position": m9_stop_event_position,
        "m9_objective_label": "scrap_only" if algorithm == "m9_two_ply" else None,
        "m9_objective_definition": (
            "m7_final_net_cost_including_terminal_scrap_credit"
            if algorithm == "m9_two_ply"
            else None
        ),
        "m9_depth": 2 if algorithm == "m9_two_ply" else None,
        "m9_complete": algorithm == "m9_two_ply",
        "action_catalog_complete": True,
        "truncated_catalog_count": 0,
        "truncated_transition_count": 0,
    }
    digest = semantic_sha256(semantic)
    return Gate3DecisionTrace(
        decision_id=f"yfm11g3dec-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


class Gate3ShardTrace(FrozenExperimentModel):
    """One exact material-isolated replay result returned by the adapter."""

    schema_version: Literal["yieldforge.m11-gate3-shard-trace.v1"] = (
        "yieldforge.m11-gate3-shard-trace.v1"
    )
    trace_id: StrictStr = Field(pattern=r"^yfm11g3sh-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    root_binding_id: StrictStr = Field(pattern=r"^yfm11g3root-[0-9a-f]{24}$")
    root_binding_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(min_length=1)
    corpus_id: Gate3CorpusId
    shard_id: StrictStr = Field(min_length=1)
    material_key: StrictStr = Field(min_length=1)
    arm: Gate3Arm
    policy_id: StrictStr = Field(min_length=1)
    visibility: Gate3Visibility
    projection_binding_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decisions: tuple[Gate3DecisionTrace, ...] = Field(min_length=1)
    final_costs: Gate3CostLedger
    source_revalidated: Literal[True] = True

    @model_validator(mode="after")
    def require_ordered_shard_and_identity(self) -> Self:
        positions = tuple(item.event_position for item in self.decisions)
        if positions != tuple(sorted(set(positions))):
            raise ValueError("Gate 3 shard decisions must use sorted unique event positions")
        if any(
            item.arm != self.arm
            or item.policy_id != self.policy_id
            or item.visibility != self.visibility
            for item in self.decisions
        ):
            raise ValueError("Gate 3 shard decisions differ from their arm binding")
        digest = semantic_sha256(self, excluded_fields={"trace_id", "content_sha256"})
        if self.trace_id != f"yfm11g3sh-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 shard identity does not match semantic content")
        return self


def build_gate3_shard_trace(
    *,
    roots: Gate3RootBinding,
    stream_id: str,
    corpus_id: Gate3CorpusId,
    shard_id: str,
    material_key: str,
    arm: Gate3Arm,
    policy_id: str,
    visibility: Gate3Visibility,
    projection_binding_sha256: str,
    decisions: tuple[Gate3DecisionTrace, ...],
    final_costs: Gate3CostLedger,
) -> Gate3ShardTrace:
    ordered = tuple(sorted(decisions, key=lambda item: item.event_position))
    semantic = {
        "schema_version": "yieldforge.m11-gate3-shard-trace.v1",
        "root_binding_id": roots.binding_id,
        "root_binding_content_sha256": roots.content_sha256,
        "stream_id": stream_id,
        "corpus_id": corpus_id,
        "shard_id": shard_id,
        "material_key": material_key,
        "arm": arm,
        "policy_id": policy_id,
        "visibility": visibility,
        "projection_binding_sha256": projection_binding_sha256,
        "decisions": [item.model_dump(mode="json") for item in ordered],
        "final_costs": final_costs.model_dump(mode="json"),
        "source_revalidated": True,
    }
    digest = semantic_sha256(semantic)
    return Gate3ShardTrace(
        trace_id=f"yfm11g3sh-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        root_binding_id=roots.binding_id,
        root_binding_content_sha256=roots.content_sha256,
        stream_id=stream_id,
        corpus_id=corpus_id,
        shard_id=shard_id,
        material_key=material_key,
        arm=arm,
        policy_id=policy_id,
        visibility=visibility,
        projection_binding_sha256=projection_binding_sha256,
        decisions=ordered,
        final_costs=final_costs,
    )


class Gate3ArmTrace(FrozenExperimentModel):
    """One stream arm reconstructed by exact addition of disjoint material shards."""

    schema_version: Literal["yieldforge.m11-gate3-arm-trace.v1"] = (
        "yieldforge.m11-gate3-arm-trace.v1"
    )
    trace_id: StrictStr = Field(pattern=r"^yfm11g3arm-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    stream_id: StrictStr = Field(min_length=1)
    corpus_id: Gate3CorpusId
    regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"]
    arm: Gate3Arm
    policy_id: StrictStr = Field(min_length=1)
    visibility: Gate3Visibility
    material_shard_count: StrictInt = Field(gt=0)
    shards: tuple[Gate3ShardTrace, ...] = Field(min_length=1)
    final_costs: Gate3CostLedger
    exact_event_census: Literal[True] = True

    @property
    def decisions(self) -> tuple[Gate3DecisionTrace, ...]:
        return tuple(
            sorted(
                (decision for shard in self.shards for decision in shard.decisions),
                key=lambda item: item.event_position,
            )
        )

    @model_validator(mode="after")
    def require_exact_merge_and_identity(self) -> Self:
        if self.shards != tuple(
            sorted(self.shards, key=lambda item: (item.material_key, item.shard_id))
        ):
            raise ValueError("Gate 3 material shards must use canonical material order")
        if self.material_shard_count != len(self.shards):
            raise ValueError("Gate 3 material shard count does not reconcile")
        if any(
            item.root_binding_id != self.roots.binding_id
            or item.root_binding_content_sha256 != self.roots.content_sha256
            or item.stream_id != self.stream_id
            or item.corpus_id != self.corpus_id
            or item.arm != self.arm
            or item.policy_id != self.policy_id
            or item.visibility != self.visibility
            for item in self.shards
        ):
            raise ValueError("Gate 3 material shard binding differs from its stream arm")
        positions = tuple(item.event_position for item in self.decisions)
        if positions != tuple(range(24)):
            raise ValueError("Gate 3 material shards must cover every stream event exactly once")
        if self.final_costs != _sum_ledgers(tuple(item.final_costs for item in self.shards)):
            raise ValueError("Gate 3 material shard ledger merge does not reconcile")
        expected = {
            "F": ("full_future", "m9_two_ply"),
            "K": ("known_only", "m9_two_ply"),
        }.get(self.arm)
        if expected is not None and (
            self.visibility != expected[0]
            or any(item.algorithm != expected[1] for item in self.decisions)
        ):
            raise ValueError("Gate 3 reference arm algorithm or visibility differs")
        if self.arm == "B" and not (
            (
                self.visibility == "released_only"
                and all(item.algorithm == "m7_policy" for item in self.decisions)
            )
            or (
                self.visibility == "known_only"
                and all(item.algorithm == "m9_two_ply" for item in self.decisions)
            )
        ):
            raise ValueError("Gate 3 baseline arm algorithm or visibility differs")
        digest = semantic_sha256(self, excluded_fields={"trace_id", "content_sha256"})
        if self.trace_id != f"yfm11g3arm-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 arm identity does not match semantic content")
        return self


def merge_gate3_material_shards(
    *,
    roots: Gate3RootBinding,
    stream_id: str,
    corpus_id: Gate3CorpusId,
    regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"],
    arm: Gate3Arm,
    policy_id: str,
    shards: tuple[Gate3ShardTrace, ...],
) -> Gate3ArmTrace:
    if not shards:
        raise ValueError("Gate 3 arm requires at least one material shard")
    canonical = tuple(sorted(shards, key=lambda item: (item.material_key, item.shard_id)))
    positions = tuple(
        sorted(decision.event_position for shard in canonical for decision in shard.decisions)
    )
    if positions != tuple(range(24)):
        raise ValueError("Gate 3 material shards must cover every stream event exactly once")
    visibility = canonical[0].visibility
    final_costs = _sum_ledgers(tuple(item.final_costs for item in canonical))
    semantic = {
        "schema_version": "yieldforge.m11-gate3-arm-trace.v1",
        "roots": roots.model_dump(mode="json"),
        "stream_id": stream_id,
        "corpus_id": corpus_id,
        "regime": regime,
        "arm": arm,
        "policy_id": policy_id,
        "visibility": visibility,
        "material_shard_count": len(canonical),
        "shards": [item.model_dump(mode="json") for item in canonical],
        "final_costs": final_costs.model_dump(mode="json"),
        "exact_event_census": True,
    }
    digest = semantic_sha256(semantic)
    return Gate3ArmTrace(
        trace_id=f"yfm11g3arm-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        stream_id=stream_id,
        corpus_id=corpus_id,
        regime=regime,
        arm=arm,
        policy_id=policy_id,
        visibility=visibility,
        material_shard_count=len(canonical),
        shards=canonical,
        final_costs=final_costs,
    )


class Gate3StreamCell(FrozenExperimentModel):
    """Complete paired central B/F/K evidence for one confirmation stream."""

    schema_version: Literal["yieldforge.m11-gate3-stream-cell.v1"] = (
        "yieldforge.m11-gate3-stream-cell.v1"
    )
    cell_id: StrictStr = Field(pattern=r"^yfm11g3cell-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    baseline_freeze: Gate3BaselineCalibrationFreeze
    baseline_freeze_id: StrictStr = Field(pattern=r"^yfm11g3bf-[0-9a-f]{24}$")
    baseline_freeze_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(min_length=1)
    corpus_id: Gate3CorpusId
    regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"]
    baseline: Gate3ArmTrace
    full_future: Gate3ArmTrace
    known_only: Gate3ArmTrace
    baseline_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    full_future_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    known_only_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    full_future_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    unknown_future_contribution_points: StrictStr = Field(pattern=_METRIC_PATTERN)
    savings_formula: Literal["100 * (B_i - F_i) / B_i"] = "100 * (B_i - F_i) / B_i"
    unknown_formula: Literal["100 * (K_i - F_i) / B_i"] = "100 * (K_i - F_i) / B_i"
    candidate_action_parity_revalidated: Literal[True] = True
    common_compute_and_tie_revalidated: Literal[True] = True

    @model_validator(mode="after")
    def require_paired_evidence_metrics_and_identity(self) -> Self:
        arms = (self.baseline, self.full_future, self.known_only)
        if (
            self.baseline_freeze.roots != self.roots
            or self.baseline_freeze.corpus_id != self.corpus_id
            or self.baseline_freeze_id != self.baseline_freeze.freeze_id
            or self.baseline_freeze_content_sha256 != self.baseline_freeze.content_sha256
            or any(item.policy_id != self.baseline_freeze.selected_policy_id for item in arms)
        ):
            raise ValueError("Gate 3 stream cell differs from its selected baseline policy freeze")
        if tuple(item.arm for item in arms) != ("B", "F", "K") or any(
            item.roots != self.roots
            or item.stream_id != self.stream_id
            or item.corpus_id != self.corpus_id
            or item.regime != self.regime
            for item in arms
        ):
            raise ValueError("Gate 3 stream cell arm binding differs")
        aligned = zip(*(item.decisions for item in arms), strict=True)
        for baseline, full, known in aligned:
            bindings = {
                (
                    item.event_id,
                    item.standard_candidate_set_sha256,
                    item.search_config_sha256,
                    item.compute_budget_sha256,
                )
                for item in (baseline, full, known)
            }
            if len(bindings) != 1 or full.tie_rule != known.tie_rule:
                raise ValueError("Gate 3 candidate/config/tie parity differs across paired arms")
        costs = tuple(
            _signed_cost(item.final_costs.net_cost, label="arm net cost") for item in arms
        )
        if costs[0] <= 0:
            raise ValueError("Gate 3 stream baseline cost must be positive")
        expected_costs = tuple(item.final_costs.net_cost for item in arms)
        if (self.baseline_cost, self.full_future_cost, self.known_only_cost) != expected_costs:
            raise ValueError("Gate 3 stream cell costs differ from arm evidence")
        with localcontext() as context:
            context.prec = 50
            savings = Decimal(100) * (costs[0] - costs[1]) / costs[0]
            unknown = Decimal(100) * (costs[2] - costs[1]) / costs[0]
        if self.full_future_savings_percent != _format_metric(savings) or (
            self.unknown_future_contribution_points != _format_metric(unknown)
        ):
            raise ValueError("Gate 3 stream metrics do not reconcile with B/F/K costs")
        digest = semantic_sha256(self, excluded_fields={"cell_id", "content_sha256"})
        if self.cell_id != f"yfm11g3cell-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 stream cell identity does not match semantic content")
        return self


def build_gate3_stream_cell(
    *,
    roots: Gate3RootBinding,
    baseline_freeze: Gate3BaselineCalibrationFreeze,
    baseline: Gate3ArmTrace,
    full_future: Gate3ArmTrace,
    known_only: Gate3ArmTrace,
) -> Gate3StreamCell:
    if (
        baseline_freeze.roots != roots
        or baseline_freeze.corpus_id != baseline.corpus_id
        or any(
            item.policy_id != baseline_freeze.selected_policy_id
            for item in (baseline, full_future, known_only)
        )
    ):
        raise ValueError("Gate 3 arms differ from the selected baseline policy freeze")
    costs = tuple(
        _signed_cost(item.final_costs.net_cost, label="arm net cost")
        for item in (baseline, full_future, known_only)
    )
    if costs[0] <= 0:
        raise ValueError("Gate 3 stream baseline cost must be positive")
    with localcontext() as context:
        context.prec = 50
        savings = Decimal(100) * (costs[0] - costs[1]) / costs[0]
        unknown = Decimal(100) * (costs[2] - costs[1]) / costs[0]
    semantic = {
        "schema_version": "yieldforge.m11-gate3-stream-cell.v1",
        "roots": roots.model_dump(mode="json"),
        "baseline_freeze": baseline_freeze.model_dump(mode="json"),
        "baseline_freeze_id": baseline_freeze.freeze_id,
        "baseline_freeze_content_sha256": baseline_freeze.content_sha256,
        "stream_id": baseline.stream_id,
        "corpus_id": baseline.corpus_id,
        "regime": baseline.regime,
        "baseline": baseline.model_dump(mode="json"),
        "full_future": full_future.model_dump(mode="json"),
        "known_only": known_only.model_dump(mode="json"),
        "baseline_cost": baseline.final_costs.net_cost,
        "full_future_cost": full_future.final_costs.net_cost,
        "known_only_cost": known_only.final_costs.net_cost,
        "full_future_savings_percent": _format_metric(savings),
        "unknown_future_contribution_points": _format_metric(unknown),
        "savings_formula": "100 * (B_i - F_i) / B_i",
        "unknown_formula": "100 * (K_i - F_i) / B_i",
        "candidate_action_parity_revalidated": True,
        "common_compute_and_tie_revalidated": True,
    }
    digest = semantic_sha256(semantic)
    return Gate3StreamCell(
        cell_id=f"yfm11g3cell-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        baseline_freeze=baseline_freeze,
        baseline_freeze_id=baseline_freeze.freeze_id,
        baseline_freeze_content_sha256=baseline_freeze.content_sha256,
        stream_id=baseline.stream_id,
        corpus_id=baseline.corpus_id,
        regime=baseline.regime,
        baseline=baseline,
        full_future=full_future,
        known_only=known_only,
        baseline_cost=baseline.final_costs.net_cost,
        full_future_cost=full_future.final_costs.net_cost,
        known_only_cost=known_only.final_costs.net_cost,
        full_future_savings_percent=_format_metric(savings),
        unknown_future_contribution_points=_format_metric(unknown),
    )


Gate3StatisticsGroup = Literal["lectra-m3-m4", "loco-2dics", "equal-corpus-pool"]


class Gate3CentralGroupSummary(FrozenExperimentModel):
    """Frozen central B/F/K statistics for one corpus or equal-corpus pool."""

    schema_version: Literal["yieldforge.m11-gate3-central-group.v1"] = (
        "yieldforge.m11-gate3-central-group.v1"
    )
    summary_id: StrictStr = Field(pattern=r"^yfm11g3grp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    group: Gate3StatisticsGroup
    stream_ids: tuple[StrictStr, ...] = Field(min_length=20, max_length=40)
    cell_ids: tuple[StrictStr, ...] = Field(min_length=20, max_length=40)
    cell_content_sha256s: tuple[StrictStr, ...] = Field(min_length=20, max_length=40)
    stream_count: StrictInt = Field(ge=20, le=40)
    mean_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    savings_mean_ci_lower: StrictFloat
    savings_mean_ci_upper: StrictFloat
    mean_unknown_contribution_points: StrictStr = Field(pattern=_METRIC_PATTERN)
    unknown_mean_ci_lower: StrictFloat
    unknown_mean_ci_upper: StrictFloat
    median_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    positive_stream_count: StrictInt = Field(ge=0, le=40)
    positive_stream_fraction_percent: StrictFloat
    positive_stream_wilson_lower_percent: StrictFloat
    positive_stream_wilson_upper_percent: StrictFloat
    savings_green: StrictBool
    unknown_green: StrictBool
    lower_mean_bound_passes: StrictBool
    median_passes: StrictBool
    positive_fraction_passes: StrictBool
    central_green: StrictBool

    @model_validator(mode="after")
    def require_statistics_and_identity(self) -> Self:
        expected_count = 40 if self.group == "equal-corpus-pool" else 20
        if (
            self.stream_count != expected_count
            or len(self.stream_ids) != expected_count
            or len(self.cell_ids) != expected_count
            or len(self.cell_content_sha256s) != expected_count
            or len(set(self.cell_ids)) != expected_count
            or len(set(self.cell_content_sha256s)) != expected_count
            or len(set(self.stream_ids)) != expected_count
        ):
            raise ValueError("Gate 3 central group cell census differs")
        mean_savings = Decimal(self.mean_savings_percent)
        mean_unknown = Decimal(self.mean_unknown_contribution_points)
        median = Decimal(self.median_savings_percent)
        numeric = (
            self.savings_mean_ci_lower,
            self.savings_mean_ci_upper,
            self.unknown_mean_ci_lower,
            self.unknown_mean_ci_upper,
            self.positive_stream_fraction_percent,
            self.positive_stream_wilson_lower_percent,
            self.positive_stream_wilson_upper_percent,
        )
        if not all(np.isfinite(value) for value in numeric):
            raise ValueError("Gate 3 central group statistics must be finite")
        if not (
            self.savings_mean_ci_lower <= float(mean_savings) <= self.savings_mean_ci_upper
            and self.unknown_mean_ci_lower <= float(mean_unknown) <= self.unknown_mean_ci_upper
            and 0 <= self.positive_stream_count <= self.stream_count
            and self.positive_stream_fraction_percent
            == 100.0 * self.positive_stream_count / self.stream_count
            and 0.0
            <= self.positive_stream_wilson_lower_percent
            <= self.positive_stream_fraction_percent
            <= self.positive_stream_wilson_upper_percent
            <= 100.0
        ):
            raise ValueError("Gate 3 central group statistics do not reconcile")
        flags = (
            mean_savings >= Decimal("2.5"),
            mean_unknown >= Decimal("1.5"),
            self.savings_mean_ci_lower > 0.0,
            median > 0,
            self.positive_stream_fraction_percent > 50.0,
        )
        if (
            self.savings_green,
            self.unknown_green,
            self.lower_mean_bound_passes,
            self.median_passes,
            self.positive_fraction_passes,
        ) != flags or self.central_green is not all(flags):
            raise ValueError("Gate 3 central group decision flags differ from frozen thresholds")
        digest = semantic_sha256(self, excluded_fields={"summary_id", "content_sha256"})
        if self.summary_id != f"yfm11g3grp-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 central group identity does not match semantic content")
        return self


class Gate3CentralStatistics(FrozenExperimentModel):
    """The three deterministic paired-bootstrap central decision groups."""

    schema_version: Literal["yieldforge.m11-gate3-central-statistics.v1"] = (
        "yieldforge.m11-gate3-central-statistics.v1"
    )
    statistics_id: StrictStr = Field(pattern=r"^yfm11g3stats-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bootstrap_generator: Literal["numpy.Generator(PCG64(0))"] = "numpy.Generator(PCG64(0))"
    bootstrap_resamples: Literal[10000] = 10000
    bootstrap_seed: Literal[0] = 0
    quantile_method: Literal["linear_type_7"] = "linear_type_7"
    confidence_level: Literal[0.95] = 0.95
    resampling_unit: Literal["complete_paired_stream_b_f_k_vector"] = (
        "complete_paired_stream_b_f_k_vector"
    )
    aggregation: Literal["equal_stream_within_corpus_then_equal_corpus_pool"] = (
        "equal_stream_within_corpus_then_equal_corpus_pool"
    )
    groups: tuple[
        Gate3CentralGroupSummary,
        Gate3CentralGroupSummary,
        Gate3CentralGroupSummary,
    ]
    all_groups_central_green: StrictBool

    @model_validator(mode="after")
    def require_group_order_decision_and_identity(self) -> Self:
        if tuple(item.group for item in self.groups) != (
            "lectra-m3-m4",
            "loco-2dics",
            "equal-corpus-pool",
        ):
            raise ValueError("Gate 3 central statistics groups differ from frozen order")
        if self.all_groups_central_green is not all(item.central_green for item in self.groups):
            raise ValueError("Gate 3 central aggregate decision does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"statistics_id", "content_sha256"})
        if self.statistics_id != f"yfm11g3stats-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 central statistics identity does not match semantic content")
        return self


def _gate3_bootstrap_indices() -> tuple[np.ndarray, np.ndarray]:
    generator = np.random.Generator(np.random.PCG64(0))
    lectra = generator.integers(0, 20, size=(10000, 20), dtype=np.int64)
    loco = generator.integers(0, 20, size=(10000, 20), dtype=np.int64)
    return lectra, loco


def _median_decimal(values: tuple[Decimal, ...]) -> Decimal:
    ordered = tuple(sorted(values))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _build_central_group(
    *,
    group: Gate3StatisticsGroup,
    cells: tuple[Gate3StreamCell, ...],
    savings_bootstrap: np.ndarray,
    unknown_bootstrap: np.ndarray,
) -> Gate3CentralGroupSummary:
    savings = tuple(Decimal(item.full_future_savings_percent) for item in cells)
    unknown = tuple(Decimal(item.unknown_future_contribution_points) for item in cells)
    with localcontext() as context:
        context.prec = 50
        mean_savings = sum(savings, Decimal(0)) / Decimal(len(savings))
        mean_unknown = sum(unknown, Decimal(0)) / Decimal(len(unknown))
    savings_lower = linear_quantile(savings_bootstrap, 0.025)
    savings_upper = linear_quantile(savings_bootstrap, 0.975)
    unknown_lower = linear_quantile(unknown_bootstrap, 0.025)
    unknown_upper = linear_quantile(unknown_bootstrap, 0.975)
    positive = sum(value > 0 for value in savings)
    positive_fraction = 100.0 * positive / len(savings)
    wilson_lower, wilson_upper = wilson_interval_percent(positive, len(savings))
    median = _median_decimal(savings)
    flags = (
        mean_savings >= Decimal("2.5"),
        mean_unknown >= Decimal("1.5"),
        savings_lower > 0.0,
        median > 0,
        positive_fraction > 50.0,
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-central-group.v1",
        "group": group,
        "stream_ids": tuple(item.stream_id for item in cells),
        "cell_ids": tuple(item.cell_id for item in cells),
        "cell_content_sha256s": tuple(item.content_sha256 for item in cells),
        "stream_count": len(cells),
        "mean_savings_percent": _format_metric(mean_savings),
        "savings_mean_ci_lower": savings_lower,
        "savings_mean_ci_upper": savings_upper,
        "mean_unknown_contribution_points": _format_metric(mean_unknown),
        "unknown_mean_ci_lower": unknown_lower,
        "unknown_mean_ci_upper": unknown_upper,
        "median_savings_percent": _format_metric(median),
        "positive_stream_count": positive,
        "positive_stream_fraction_percent": positive_fraction,
        "positive_stream_wilson_lower_percent": wilson_lower,
        "positive_stream_wilson_upper_percent": wilson_upper,
        "savings_green": flags[0],
        "unknown_green": flags[1],
        "lower_mean_bound_passes": flags[2],
        "median_passes": flags[3],
        "positive_fraction_passes": flags[4],
        "central_green": all(flags),
    }
    digest = semantic_sha256(semantic)
    return Gate3CentralGroupSummary(
        summary_id=f"yfm11g3grp-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _corpus_group_summary(
    cells: tuple[Gate3StreamCell, ...],
    *,
    lectra_indices: np.ndarray,
    loco_indices: np.ndarray,
) -> Gate3CentralGroupSummary:
    if len(cells) != 20 or len({item.cell_id for item in cells}) != 20:
        raise ValueError("Gate 3 corpus central summary requires twenty unique paired cells")
    corpus_ids = {item.corpus_id for item in cells}
    if len(corpus_ids) != 1:
        raise ValueError("Gate 3 corpus central summary cannot mix corpora")
    corpus = cells[0].corpus_id
    indices = lectra_indices if corpus == "lectra-m3-m4" else loco_indices
    savings = np.asarray(
        [float(item.full_future_savings_percent) for item in cells],
        dtype=np.float64,
    )
    unknown = np.asarray(
        [float(item.unknown_future_contribution_points) for item in cells],
        dtype=np.float64,
    )
    return _build_central_group(
        group=corpus,
        cells=cells,
        savings_bootstrap=savings[indices].mean(axis=1),
        unknown_bootstrap=unknown[indices].mean(axis=1),
    )


def _canonicalize_gate3_cells(
    cells: tuple[Gate3StreamCell, ...],
    *,
    canonical_stream_ids: tuple[str, ...],
    expected_corpus: Gate3CorpusId,
) -> tuple[Gate3StreamCell, ...]:
    if len(canonical_stream_ids) != 20 or len(set(canonical_stream_ids)) != 20:
        raise ValueError("Gate 3 canonical stream census requires twenty unique pack IDs")
    by_stream = {item.stream_id: item for item in cells}
    if (
        len(cells) != 20
        or len(by_stream) != 20
        or set(by_stream) != set(canonical_stream_ids)
        or any(item.corpus_id != expected_corpus for item in cells)
    ):
        raise ValueError("Gate 3 cells differ from the canonical stream census")
    return tuple(by_stream[stream_id] for stream_id in canonical_stream_ids)


def calculate_gate3_corpus_central_summary(
    cells: tuple[Gate3StreamCell, ...],
    *,
    canonical_stream_ids: tuple[str, ...],
) -> Gate3CentralGroupSummary:
    """Classify one corpus using its frozen position in the shared PCG64(0) draw stream."""

    if not cells:
        raise ValueError("Gate 3 corpus central summary requires raw cells")
    canonical = _canonicalize_gate3_cells(
        cells,
        canonical_stream_ids=canonical_stream_ids,
        expected_corpus=cells[0].corpus_id,
    )
    lectra_indices, loco_indices = _gate3_bootstrap_indices()
    return _corpus_group_summary(
        canonical,
        lectra_indices=lectra_indices,
        loco_indices=loco_indices,
    )


def calculate_gate3_central_statistics(
    cells: tuple[Gate3StreamCell, ...],
    *,
    lectra_stream_ids: tuple[str, ...],
    loco_stream_ids: tuple[str, ...],
) -> Gate3CentralStatistics:
    """Recompute all central decision groups from forty raw paired B/F/K cells."""

    if len(cells) != 40 or len({item.cell_id for item in cells}) != 40:
        raise ValueError("Gate 3 central statistics require forty unique paired cells")
    lectra = _canonicalize_gate3_cells(
        tuple(item for item in cells if item.corpus_id == "lectra-m3-m4"),
        canonical_stream_ids=lectra_stream_ids,
        expected_corpus="lectra-m3-m4",
    )
    loco = _canonicalize_gate3_cells(
        tuple(item for item in cells if item.corpus_id == "loco-2dics"),
        canonical_stream_ids=loco_stream_ids,
        expected_corpus="loco-2dics",
    )
    canonical_cells = lectra + loco
    lectra_indices, loco_indices = _gate3_bootstrap_indices()
    lectra_summary = _corpus_group_summary(
        lectra,
        lectra_indices=lectra_indices,
        loco_indices=loco_indices,
    )
    loco_summary = _corpus_group_summary(
        loco,
        lectra_indices=lectra_indices,
        loco_indices=loco_indices,
    )
    lectra_savings = np.asarray(
        [float(item.full_future_savings_percent) for item in lectra], dtype=np.float64
    )
    loco_savings = np.asarray(
        [float(item.full_future_savings_percent) for item in loco], dtype=np.float64
    )
    lectra_unknown = np.asarray(
        [float(item.unknown_future_contribution_points) for item in lectra], dtype=np.float64
    )
    loco_unknown = np.asarray(
        [float(item.unknown_future_contribution_points) for item in loco], dtype=np.float64
    )
    pool_summary = _build_central_group(
        group="equal-corpus-pool",
        cells=canonical_cells,
        savings_bootstrap=(
            lectra_savings[lectra_indices].mean(axis=1) + loco_savings[loco_indices].mean(axis=1)
        )
        / 2.0,
        unknown_bootstrap=(
            lectra_unknown[lectra_indices].mean(axis=1) + loco_unknown[loco_indices].mean(axis=1)
        )
        / 2.0,
    )
    groups = (lectra_summary, loco_summary, pool_summary)
    semantic = {
        "schema_version": "yieldforge.m11-gate3-central-statistics.v1",
        "bootstrap_generator": "numpy.Generator(PCG64(0))",
        "bootstrap_resamples": 10000,
        "bootstrap_seed": 0,
        "quantile_method": "linear_type_7",
        "confidence_level": 0.95,
        "resampling_unit": "complete_paired_stream_b_f_k_vector",
        "aggregation": "equal_stream_within_corpus_then_equal_corpus_pool",
        "groups": [item.model_dump(mode="json") for item in groups],
        "all_groups_central_green": all(item.central_green for item in groups),
    }
    digest = semantic_sha256(semantic)
    return Gate3CentralStatistics(
        statistics_id=f"yfm11g3stats-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        groups=groups,
        all_groups_central_green=all(item.central_green for item in groups),
    )


Gate3HardNullKind = Literal[
    "single_action",
    "unique_materials_single_action",
    "all_work_known_single_action",
]


def _gate3_underlying_policy(policy_id: str):
    return policy_identity(
        M7PolicyName.AGE_REGULARITY
        if policy_id == "known_only_m9_two_ply_scrap"
        else M7PolicyName(policy_id)
    )


class Gate3DecisionRuntimeReceipt(FrozenExperimentModel):
    """The exact full or physically masked runtime used by one M9 decision."""

    schema_version: Literal["yieldforge.m11-gate3-decision-runtime.v1"] = (
        "yieldforge.m11-gate3-decision-runtime.v1"
    )
    receipt_id: StrictStr = Field(pattern=r"^yfm11g3drt-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_id: StrictStr = Field(pattern=r"^yfm11g3dec-[0-9a-f]{24}$")
    decision_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_role: Literal["base_full_future", "known_only_physical_mask"]
    retained_local_event_positions: tuple[StrictInt, ...] = Field(min_length=1)
    runtime_input: M7ReplayInput
    runtime_semantic_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        if self.retained_local_event_positions != tuple(
            sorted(set(self.retained_local_event_positions))
        ):
            raise ValueError("Gate 3 decision runtime positions are not canonical")
        digest = semantic_sha256(self, excluded_fields={"receipt_id", "content_sha256"})
        if self.receipt_id != f"yfm11g3drt-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 decision runtime identity differs from semantic content")
        return self


def build_gate3_decision_runtime_receipt(
    *,
    decision: Gate3DecisionTrace,
    runtime_role: Literal["base_full_future", "known_only_physical_mask"],
    retained_local_event_positions: tuple[int, ...],
    runtime: M7ReplayRuntime,
) -> Gate3DecisionRuntimeReceipt:
    semantic = {
        "schema_version": "yieldforge.m11-gate3-decision-runtime.v1",
        "decision_id": decision.decision_id,
        "decision_content_sha256": decision.content_sha256,
        "runtime_role": runtime_role,
        "retained_local_event_positions": retained_local_event_positions,
        "runtime_input": runtime.replay_input.model_dump(mode="json"),
        "runtime_semantic_sha256": m7_semantic_runtime_sha256(runtime),
    }
    digest = semantic_sha256(semantic)
    return Gate3DecisionRuntimeReceipt(
        receipt_id=f"yfm11g3drt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        decision_id=decision.decision_id,
        decision_content_sha256=decision.content_sha256,
        runtime_role=runtime_role,
        retained_local_event_positions=retained_local_event_positions,
        runtime_input=runtime.replay_input,
        runtime_semantic_sha256=m7_semantic_runtime_sha256(runtime),
    )


def _binding_payload_without_local_identity(value: object) -> dict[str, object]:
    return value.model_dump(  # type: ignore[attr-defined,no-any-return]
        mode="json",
        exclude={"binding_id", "content_sha256", "sequence"},
    )


def _require_runtime_receipt_binding(
    *,
    receipt: Gate3DecisionRuntimeReceipt,
    decision: Gate3DecisionTrace,
    attestation: M11M7ProjectionAttestation,
    base_replay: M7ReplayInput,
    local_event_position: int,
) -> None:
    if (
        receipt.decision_id != decision.decision_id
        or receipt.decision_content_sha256 != decision.content_sha256
        or receipt.runtime_semantic_sha256 != decision.search_runtime_sha256
        or receipt.runtime_input.policy != base_replay.policy
        or receipt.runtime_input.rates != base_replay.rates
        or receipt.runtime_input.fit_config != base_replay.fit_config
        or receipt.runtime_input.search_config != base_replay.search_config
        or receipt.runtime_input.collision_backend != base_replay.collision_backend
        or receipt.runtime_input.jagua_container_guard != base_replay.jagua_container_guard
        or receipt.runtime_input.horizon_end != base_replay.horizon_end
    ):
        raise ValueError("Gate 3 decision runtime receipt differs from its decision/base runtime")
    if receipt.runtime_role == "base_full_future":
        if (
            decision.visibility != "full_future"
            or receipt.retained_local_event_positions != tuple(range(len(base_replay.instances)))
            or receipt.runtime_input != base_replay
            or receipt.runtime_semantic_sha256 != attestation.m7_runtime_semantic_sha256
        ):
            raise ValueError("Gate 3 full-future runtime receipt differs from the base runtime")
        return
    visibility = attestation.known_visible_local_prefixes[local_event_position]
    expected_positions = tuple(
        sorted(set(range(local_event_position + 1)) | set(visibility.visible_local_event_positions))
    )
    runtime_input = receipt.runtime_input
    if (
        decision.visibility != "known_only"
        or receipt.retained_local_event_positions != expected_positions
        or len(runtime_input.instances) != len(expected_positions)
        or tuple(item.sequence for item in runtime_input.instances)
        != tuple(range(len(expected_positions)))
        or tuple(_binding_payload_without_local_identity(item) for item in runtime_input.instances)
        != tuple(
            _binding_payload_without_local_identity(base_replay.instances[position])
            for position in expected_positions
        )
    ):
        raise ValueError("Gate 3 known-only runtime receipt differs from the physical mask")
    expected_problem_ids = {
        base_replay.instances[position].problem_id for position in expected_positions
    }
    if tuple(runtime_input.problems) != tuple(
        item for item in base_replay.problems if item.problem_id in expected_problem_ids
    ) or tuple(runtime_input.candidate_sets) != tuple(
        item for item in base_replay.candidate_sets if item.problem_id in expected_problem_ids
    ):
        raise ValueError("Gate 3 known-only runtime receipt changed source candidates")


class Gate3ProjectionShardEvidence(FrozenExperimentModel):
    """One material shard bound to the exact adapter DTO that produced it."""

    schema_version: Literal["yieldforge.m11-gate3-projection-shard.v1"] = (
        "yieldforge.m11-gate3-projection-shard.v1"
    )
    evidence_id: StrictStr = Field(pattern=r"^yfm11g3pse-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    projection_attestation: M11M7ProjectionAttestation
    replay_input: M7ReplayInput
    shard_trace: Gate3ShardTrace
    decision_runtime_receipts: tuple[Gate3DecisionRuntimeReceipt, ...] = ()

    @model_validator(mode="after")
    def require_projection_runtime_and_shard_identity(self) -> Self:
        attestation = self.projection_attestation
        replay = self.replay_input
        shard = self.shard_trace
        source_map = attestation.source_event_map
        parity = attestation.candidate_action_parity
        root_values = (
            attestation.gate1_result_id,
            attestation.gate1_result_content_sha256,
            attestation.gate2_result_id,
            attestation.gate2_result_content_sha256,
            attestation.gate3_config_id,
            attestation.gate3_config_content_sha256,
            attestation.population_id,
            attestation.population_content_sha256,
        )
        expected_roots = (
            self.roots.gate1_evaluation_result_id,
            self.roots.gate1_evaluation_result_content_sha256,
            self.roots.gate2_evaluation_result_id,
            self.roots.gate2_evaluation_result_content_sha256,
            self.roots.gate3_config_id,
            self.roots.gate3_config_content_sha256,
            self.roots.population_id,
            self.roots.population_content_sha256,
        )
        if (
            root_values != expected_roots
            or replay.input_id != attestation.m7_replay_input_id
            or replay.content_sha256 != attestation.m7_replay_input_content_sha256
            or replay.policy != attestation.policy
            or replay.rates != attestation.rates
            or replay.collision_backend != attestation.collision_backend
            or len(replay.instances) != len(source_map)
            or len(parity) != len(source_map)
            or tuple(item.event_id for item in replay.instances)
            != tuple(item.compatibility_event_id for item in source_map)
            or shard.root_binding_id != self.roots.binding_id
            or shard.root_binding_content_sha256 != self.roots.content_sha256
            or shard.stream_id != attestation.source_stream_id
            or shard.corpus_id != attestation.corpus_id
            or shard.material_key != attestation.material_key
            or shard.projection_binding_sha256 != attestation.content_sha256
            or tuple(item.event_position for item in shard.decisions)
            != tuple(item.source_event_position for item in source_map)
            or tuple(item.event_id for item in shard.decisions)
            != tuple(item.source_event_id for item in source_map)
        ):
            raise ValueError("Gate 3 projection shard differs from adapter/source roots")
        candidate_sets = {item.problem_id: item for item in replay.candidate_sets}
        if len(candidate_sets) != len(replay.candidate_sets):
            raise ValueError("Gate 3 projection replay candidate sets repeat")
        for local_position, (binding, mapping, action_parity, decision) in enumerate(
            zip(replay.instances, source_map, parity, shard.decisions, strict=True)
        ):
            candidate_set = candidate_sets.get(binding.problem_id)
            if (
                candidate_set is None
                or mapping.local_event_position != local_position
                or action_parity.local_event_position != local_position
                or action_parity.source_event_id != mapping.source_event_id
                or action_parity.runtime_problem_id != binding.problem_id
                or action_parity.runtime_candidate_ids != candidate_set.candidate_ids
                or decision.standard_candidate_set_sha256 != candidate_set.content_sha256
                or not set(action_parity.standard_action_ids).issubset(decision.action_ids)
            ):
                raise ValueError("Gate 3 projection shard candidate/runtime evidence differs")
        receipt_by_decision = {item.decision_id: item for item in self.decision_runtime_receipts}
        if len(receipt_by_decision) != len(self.decision_runtime_receipts):
            raise ValueError("Gate 3 decision runtime receipts repeat")
        m9_decisions = tuple(item for item in shard.decisions if item.algorithm == "m9_two_ply")
        if set(receipt_by_decision) != {item.decision_id for item in m9_decisions} or any(
            item.search_runtime_sha256 is not None
            for item in shard.decisions
            if item.algorithm == "m7_policy"
        ):
            raise ValueError("Gate 3 decision runtime receipt census differs")
        local_by_source_position = {
            item.source_event_position: item.local_event_position for item in source_map
        }
        for decision in m9_decisions:
            _require_runtime_receipt_binding(
                receipt=receipt_by_decision[decision.decision_id],
                decision=decision,
                attestation=attestation,
                base_replay=replay,
                local_event_position=local_by_source_position[decision.event_position],
            )
        digest = semantic_sha256(self, excluded_fields={"evidence_id", "content_sha256"})
        if self.evidence_id != f"yfm11g3pse-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 projection shard identity differs from semantic content")
        return self


def build_gate3_projection_shard_evidence(
    *,
    roots: Gate3RootBinding,
    projection_attestation: M11M7ProjectionAttestation,
    replay_input: M7ReplayInput,
    shard_trace: Gate3ShardTrace,
    decision_runtime_receipts: tuple[Gate3DecisionRuntimeReceipt, ...] = (),
) -> Gate3ProjectionShardEvidence:
    semantic = {
        "schema_version": "yieldforge.m11-gate3-projection-shard.v1",
        "roots": roots.model_dump(mode="json"),
        "projection_attestation": projection_attestation.model_dump(mode="json"),
        "replay_input": replay_input.model_dump(mode="json"),
        "shard_trace": shard_trace.model_dump(mode="json"),
        "decision_runtime_receipts": [
            item.model_dump(mode="json") for item in decision_runtime_receipts
        ],
    }
    digest = semantic_sha256(semantic)
    return Gate3ProjectionShardEvidence(
        evidence_id=f"yfm11g3pse-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        projection_attestation=projection_attestation,
        replay_input=replay_input,
        shard_trace=shard_trace,
        decision_runtime_receipts=decision_runtime_receipts,
    )


class Gate3HardNullArmTrace(FrozenExperimentModel):
    """Three-event single-action raw evidence for one hard-null arm."""

    schema_version: Literal["yieldforge.m11-gate3-hard-null-arm.v1"] = (
        "yieldforge.m11-gate3-hard-null-arm.v1"
    )
    trace_id: StrictStr = Field(pattern=r"^yfm11g3hna-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    registration: M11HardNull
    arm: Gate3Arm
    policy_id: StrictStr = Field(min_length=1)
    material_evidence: tuple[Gate3ProjectionShardEvidence, ...] = Field(min_length=1)

    @property
    def control_id(self) -> str:
        return self.registration.null_id

    @property
    def corpus_id(self) -> Gate3CorpusId:
        return self.registration.corpus_id

    @property
    def null_kind(self) -> Gate3HardNullKind:
        return self.registration.null_kind

    @property
    def decisions(self) -> tuple[Gate3DecisionTrace, ...]:
        return tuple(
            sorted(
                (
                    decision
                    for evidence in self.material_evidence
                    for decision in evidence.shard_trace.decisions
                ),
                key=lambda item: item.event_position,
            )
        )

    @property
    def final_costs(self) -> Gate3CostLedger:
        return _sum_ledgers(
            tuple(evidence.shard_trace.final_costs for evidence in self.material_evidence)
        )

    @model_validator(mode="after")
    def require_single_action_null_and_identity(self) -> Self:
        if tuple(
            item.projection_attestation.material_key for item in self.material_evidence
        ) != tuple(
            sorted(item.projection_attestation.material_key for item in self.material_evidence)
        ) or len(
            {item.projection_attestation.material_key for item in self.material_evidence}
        ) != len(self.material_evidence):
            raise ValueError("Gate 3 hard-null material evidence is not canonical")
        expected_policy = _gate3_underlying_policy(self.policy_id)
        for evidence in self.material_evidence:
            attestation = evidence.projection_attestation
            shard = evidence.shard_trace
            if (
                evidence.roots != self.roots
                or attestation.registration_kind != "hard_null"
                or attestation.source_registration_id != self.registration.null_id
                or attestation.source_registration_content_sha256
                != self.registration.content_sha256
                or attestation.source_stream_id != self.registration.source_stream_id
                or attestation.corpus_id != self.registration.corpus_id
                or attestation.control_kind != self.registration.null_kind
                or attestation.economic_arm != "central"
                or attestation.policy != expected_policy
                or evidence.replay_input.policy != expected_policy
                or shard.arm != self.arm
                or shard.policy_id != self.policy_id
            ):
                raise ValueError("Gate 3 hard-null material differs from its registration")
        source_maps = tuple(
            sorted(
                (
                    mapping
                    for evidence in self.material_evidence
                    for mapping in evidence.projection_attestation.source_event_map
                ),
                key=lambda item: item.source_event_position,
            )
        )
        if (
            len(source_maps) != 3
            or len({item.source_event_position for item in source_maps}) != 3
            or tuple(item.source_event_id for item in source_maps) != self.registration.event_ids
            or tuple(item.event_id for item in self.decisions) != self.registration.event_ids
        ):
            raise ValueError("Gate 3 hard-null decisions differ from registered events")
        expected_visibility_rule = (
            "hard_null_all_registered_work_known"
            if self.registration.all_work_known
            else "m11_known_at_filtered_to_registered_slice_and_material"
        )
        if any(
            prefix.visibility_rule != expected_visibility_rule
            for evidence in self.material_evidence
            for prefix in evidence.projection_attestation.known_visible_local_prefixes
        ):
            raise ValueError("Gate 3 hard-null information transform differs")
        if self.registration.unique_material_per_event and (
            len(self.material_evidence) != 3
            or any(
                len(item.projection_attestation.source_event_map) != 1
                for item in self.material_evidence
            )
        ):
            raise ValueError("Gate 3 hard-null unique-material transform differs")
        if any(
            item.arm != self.arm
            or item.policy_id != self.policy_id
            or len(item.action_ids) != 1
            or item.selected_action_id != item.baseline_action_id
            for item in self.decisions
        ):
            raise ValueError("Gate 3 hard-null arm does not preserve one feasible action")
        expected_role = {
            "B": (
                ("m9_two_ply", "known_only")
                if self.policy_id == "known_only_m9_two_ply_scrap"
                else ("m7_policy", "released_only")
            ),
            "F": ("m9_two_ply", "full_future"),
            "K": ("m9_two_ply", "known_only"),
        }[self.arm]
        if any((item.algorithm, item.visibility) != expected_role for item in self.decisions):
            raise ValueError("Gate 3 hard-null arm algorithm or visibility differs")
        digest = semantic_sha256(self, excluded_fields={"trace_id", "content_sha256"})
        if self.trace_id != f"yfm11g3hna-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 hard-null arm identity does not match semantic content")
        return self


def build_gate3_hard_null_arm_trace(
    *,
    roots: Gate3RootBinding,
    registration: M11HardNull,
    arm: Gate3Arm,
    policy_id: str,
    material_evidence: tuple[Gate3ProjectionShardEvidence, ...],
) -> Gate3HardNullArmTrace:
    semantic = {
        "schema_version": "yieldforge.m11-gate3-hard-null-arm.v1",
        "roots": roots.model_dump(mode="json"),
        "registration": registration.model_dump(mode="json"),
        "arm": arm,
        "policy_id": policy_id,
        "material_evidence": [item.model_dump(mode="json") for item in material_evidence],
    }
    digest = semantic_sha256(semantic)
    return Gate3HardNullArmTrace(
        trace_id=f"yfm11g3hna-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        registration=registration,
        arm=arm,
        policy_id=policy_id,
        material_evidence=material_evidence,
    )


class Gate3HardNullControl(FrozenExperimentModel):
    """Paired hard-null result with a mechanically derived accounting tolerance decision."""

    schema_version: Literal["yieldforge.m11-gate3-hard-null-control.v1"] = (
        "yieldforge.m11-gate3-hard-null-control.v1"
    )
    control_trace_id: StrictStr = Field(pattern=r"^yfm11g3hn-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    baseline_freeze: Gate3BaselineCalibrationFreeze
    registration: M11HardNull
    baseline: Gate3HardNullArmTrace
    full_future: Gate3HardNullArmTrace
    known_only: Gate3HardNullArmTrace
    maximum_absolute_cost_difference: StrictStr = Field(pattern=_COST_PATTERN)
    accounting_tolerance: Literal["0.000001"] = "0.000001"
    candidate_action_parity_revalidated: Literal[True] = True
    passes: StrictBool

    @property
    def control_id(self) -> str:
        return self.registration.null_id

    @property
    def corpus_id(self) -> Gate3CorpusId:
        return self.registration.corpus_id

    @property
    def null_kind(self) -> Gate3HardNullKind:
        return self.registration.null_kind

    @model_validator(mode="after")
    def require_paired_null_and_identity(self) -> Self:
        arms = (self.baseline, self.full_future, self.known_only)
        if (
            self.baseline_freeze.roots != self.roots
            or self.baseline_freeze.corpus_id != self.corpus_id
            or any(item.registration != self.registration for item in arms)
            or any(item.policy_id != self.baseline_freeze.selected_policy_id for item in arms)
        ):
            raise ValueError("Gate 3 hard-null differs from its selected M7 baseline freeze")
        if tuple(item.arm for item in arms) != ("B", "F", "K") or any(
            item.roots != self.roots
            or item.control_id != self.control_id
            or item.corpus_id != self.corpus_id
            or item.null_kind != self.null_kind
            for item in arms
        ):
            raise ValueError("Gate 3 hard-null arm binding differs")
        evidence_bindings = tuple(
            tuple(
                (
                    item.projection_attestation.material_key,
                    item.projection_attestation.attestation_id,
                    item.projection_attestation.content_sha256,
                    item.replay_input.input_id,
                    item.replay_input.content_sha256,
                )
                for item in arm.material_evidence
            )
            for arm in arms
        )
        if len(set(evidence_bindings)) != 1:
            raise ValueError("Gate 3 hard-null arms do not share one adapter projection")
        for decisions in zip(*(item.decisions for item in arms), strict=True):
            parity = {
                (
                    item.event_id,
                    item.standard_candidate_set_sha256,
                    item.search_config_sha256,
                    item.compute_budget_sha256,
                    item.action_ids,
                )
                for item in decisions
            }
            if len(parity) != 1:
                raise ValueError("Gate 3 hard-null candidate/action parity differs")
        baseline_cost = _signed_cost(
            self.baseline.final_costs.net_cost,
            label="hard-null baseline cost",
        )
        full_cost = _signed_cost(
            self.full_future.final_costs.net_cost,
            label="hard-null full-future cost",
        )
        maximum = abs(baseline_cost - full_cost)
        expected = _format_cost(maximum)
        if self.maximum_absolute_cost_difference != expected or self.passes is not (
            maximum <= _COST_QUANTUM
        ):
            raise ValueError("Gate 3 hard-null tolerance decision does not reconcile")
        digest = semantic_sha256(
            self,
            excluded_fields={"control_trace_id", "content_sha256"},
        )
        if self.control_trace_id != f"yfm11g3hn-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 hard-null identity does not match semantic content")
        return self


def build_gate3_hard_null_control(
    *,
    roots: Gate3RootBinding,
    baseline_freeze: Gate3BaselineCalibrationFreeze,
    registration: M11HardNull,
    baseline: Gate3HardNullArmTrace,
    full_future: Gate3HardNullArmTrace,
    known_only: Gate3HardNullArmTrace,
) -> Gate3HardNullControl:
    maximum = abs(
        _signed_cost(baseline.final_costs.net_cost, label="hard-null baseline cost")
        - _signed_cost(full_future.final_costs.net_cost, label="hard-null full-future cost")
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-hard-null-control.v1",
        "roots": roots.model_dump(mode="json"),
        "baseline_freeze": baseline_freeze.model_dump(mode="json"),
        "registration": registration.model_dump(mode="json"),
        "baseline": baseline.model_dump(mode="json"),
        "full_future": full_future.model_dump(mode="json"),
        "known_only": known_only.model_dump(mode="json"),
        "maximum_absolute_cost_difference": _format_cost(maximum),
        "accounting_tolerance": "0.000001",
        "candidate_action_parity_revalidated": True,
        "passes": maximum <= _COST_QUANTUM,
    }
    digest = semantic_sha256(semantic)
    return Gate3HardNullControl(
        control_trace_id=f"yfm11g3hn-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        baseline_freeze=baseline_freeze,
        registration=registration,
        baseline=baseline,
        full_future=full_future,
        known_only=known_only,
        maximum_absolute_cost_difference=_format_cost(maximum),
        passes=maximum <= _COST_QUANTUM,
    )


class Gate3ExactRootScore(FrozenExperimentModel):
    action_id: StrictStr = Field(min_length=1)
    action_kind: Literal["open_standard_sheet", "consume_remnant"]
    exact_final_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)


class Gate3ExactSearchTelemetry(FrozenExperimentModel):
    catalog_count: StrictInt = Field(gt=0)
    explored_transition_count: StrictInt = Field(gt=0)
    terminal_leaf_count: StrictInt = Field(gt=0)
    peak_branching_factor: StrictInt = Field(gt=0)
    truncated_catalog_count: Literal[0] = 0


class Gate3ExactMaterialAudit(FrozenExperimentModel):
    """One material-local exhaustive search bound to the executed two-ply shard."""

    schema_version: Literal["yieldforge.m11-gate3-exact-material.v1"] = (
        "yieldforge.m11-gate3-exact-material.v1"
    )
    material_audit_id: StrictStr = Field(pattern=r"^yfm11g3xmat-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence: Gate3ProjectionShardEvidence
    exact_start_event_position: Literal[0] = 0
    exact_stop_event_position: StrictInt = Field(gt=0, le=3)
    include_terminal_credit: Literal[True] = True
    exact_optimal_final_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    exact_root_scores: tuple[Gate3ExactRootScore, ...] = Field(min_length=1)
    exact_optimal_action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    exact_complete: Literal[True] = True
    telemetry: Gate3ExactSearchTelemetry
    selected_action_id: StrictStr = Field(min_length=1)
    selected_is_exact_optimal: StrictBool
    passes: StrictBool

    @property
    def first_decision(self) -> Gate3DecisionTrace:
        return self.evidence.shard_trace.decisions[0]

    @model_validator(mode="after")
    def require_exact_result_and_identity(self) -> Self:
        attestation = self.evidence.projection_attestation
        decision = self.first_decision
        if (
            attestation.registration_kind != "exact_audit"
            or self.evidence.shard_trace.arm != "F"
            or self.evidence.shard_trace.visibility != "full_future"
            or any(item.algorithm != "m9_two_ply" for item in self.evidence.shard_trace.decisions)
            or self.exact_stop_event_position != len(self.evidence.replay_input.instances)
            or decision.m9_start_event_position != 0
            or decision.search_runtime_sha256 != attestation.m7_runtime_semantic_sha256
            or self.selected_action_id != decision.selected_action_id
        ):
            raise ValueError("Gate 3 exact material differs from its projection/two-ply trace")
        if self.exact_root_scores != tuple(
            sorted(self.exact_root_scores, key=lambda item: item.action_id)
        ) or len({item.action_id for item in self.exact_root_scores}) != len(
            self.exact_root_scores
        ):
            raise ValueError("Gate 3 exact root scores must use sorted unique actions")
        costs = {
            item.action_id: _signed_cost(item.exact_final_cost, label="exact root cost")
            for item in self.exact_root_scores
        }
        if set(costs) != set(decision.action_ids):
            raise ValueError("Gate 3 exact root catalog differs from its two-ply decision")
        optimum = min(costs.values())
        optimal_ids = tuple(sorted(key for key, value in costs.items() if value == optimum))
        selected_optimal = self.selected_action_id in optimal_ids
        if (
            self.exact_optimal_final_cost != _format_signed_cost(optimum)
            or self.exact_optimal_action_ids != optimal_ids
            or self.selected_is_exact_optimal is not selected_optimal
            or self.passes is not selected_optimal
        ):
            raise ValueError("Gate 3 exact material result does not reconcile")
        digest = semantic_sha256(
            self,
            excluded_fields={"material_audit_id", "content_sha256"},
        )
        if self.material_audit_id != f"yfm11g3xmat-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 exact material identity differs from semantic content")
        return self


def _exact_search_cost(value: float) -> str:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("Gate 3 exact-search cost must be finite")
    return format(parsed.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP), ".6f")


def build_gate3_exact_material_audit(
    *,
    evidence: Gate3ProjectionShardEvidence,
    exact_result: M9ExactSearchResult,
) -> Gate3ExactMaterialAudit:
    scores = tuple(
        sorted(
            (
                Gate3ExactRootScore(
                    action_id=item.action_id,
                    action_kind=item.kind.value,
                    exact_final_cost=_exact_search_cost(item.final_net_cost),
                )
                for item in exact_result.root_scores
            ),
            key=lambda item: item.action_id,
        )
    )
    costs = {
        item.action_id: _signed_cost(item.exact_final_cost, label="exact root cost")
        for item in scores
    }
    if not costs:
        raise ValueError("Gate 3 exact search returned no root scores")
    optimum = min(costs.values())
    optimal_ids = tuple(sorted(key for key, value in costs.items() if value == optimum))
    if (
        not exact_result.complete
        or exact_result.telemetry.truncated_catalog_count != 0
        or _exact_search_cost(exact_result.optimal_final_net_cost) != _format_signed_cost(optimum)
        or tuple(sorted(exact_result.optimal_first_action_ids)) != optimal_ids
    ):
        raise ValueError("Gate 3 exact search is incomplete or internally inconsistent")
    selected = evidence.shard_trace.decisions[0].selected_action_id
    telemetry = Gate3ExactSearchTelemetry(
        catalog_count=exact_result.telemetry.catalog_count,
        explored_transition_count=exact_result.telemetry.explored_transition_count,
        terminal_leaf_count=exact_result.telemetry.terminal_leaf_count,
        peak_branching_factor=exact_result.telemetry.peak_branching_factor,
        truncated_catalog_count=0,
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-exact-material.v1",
        "evidence": evidence.model_dump(mode="json"),
        "exact_start_event_position": exact_result.start_event_position,
        "exact_stop_event_position": exact_result.stop_event_position,
        "include_terminal_credit": exact_result.include_terminal_credit,
        "exact_optimal_final_cost": _format_signed_cost(optimum),
        "exact_root_scores": [item.model_dump(mode="json") for item in scores],
        "exact_optimal_action_ids": optimal_ids,
        "exact_complete": True,
        "telemetry": telemetry.model_dump(mode="json"),
        "selected_action_id": selected,
        "selected_is_exact_optimal": selected in optimal_ids,
        "passes": selected in optimal_ids,
    }
    digest = semantic_sha256(semantic)
    return Gate3ExactMaterialAudit(
        material_audit_id=f"yfm11g3xmat-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        evidence=evidence,
        exact_start_event_position=exact_result.start_event_position,
        exact_stop_event_position=exact_result.stop_event_position,
        include_terminal_credit=exact_result.include_terminal_credit,
        exact_optimal_final_cost=_format_signed_cost(optimum),
        exact_root_scores=scores,
        exact_optimal_action_ids=optimal_ids,
        exact_complete=True,
        telemetry=telemetry,
        selected_action_id=selected,
        selected_is_exact_optimal=selected in optimal_ids,
        passes=selected in optimal_ids,
    )


class Gate3ExactAuditTrace(FrozenExperimentModel):
    """Registered three-event audit reconstructed from exact material searches."""

    schema_version: Literal["yieldforge.m11-gate3-exact-audit-trace.v1"] = (
        "yieldforge.m11-gate3-exact-audit-trace.v1"
    )
    trace_id: StrictStr = Field(pattern=r"^yfm11g3audit-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    baseline_freeze: Gate3BaselineCalibrationFreeze
    registration: M11ExactAuditEpisode
    economic_arm: Literal["central", "adverse", "null"]
    economic_transform: Literal[
        "central_unmodified",
        "adverse_unmodified",
        "central_unique_material_event_information_null",
    ]
    economic_profile: Literal["central", "adverse"]
    payload_transform: Literal["unmodified"] = "unmodified"
    material_rule: Literal[
        "preserve_registered_material_keys",
        "unique_material_key_per_event_information_null",
    ]
    registered_candidates_retained: Literal[True] = True
    event_positions: tuple[StrictInt, StrictInt, StrictInt]
    event_ids: tuple[StrictStr, StrictStr, StrictStr]
    material_audits: tuple[Gate3ExactMaterialAudit, ...] = Field(min_length=1, max_length=3)
    episode_root_scores: tuple[Gate3ExactRootScore, ...] = Field(min_length=1)
    selected_action_id: StrictStr = Field(min_length=1)
    exact_optimal_action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    cartesian_combination_count: StrictInt = Field(gt=0)
    cartesian_optimal_final_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    cartesian_separability_verified: Literal[True] = True
    selected_is_exact_optimal: StrictBool
    passes: StrictBool

    @property
    def audit_id(self) -> str:
        return self.registration.audit_id

    @property
    def audit_registration_content_sha256(self) -> str:
        return self.registration.content_sha256

    @property
    def corpus_id(self) -> Gate3CorpusId:
        return self.registration.corpus_id

    @model_validator(mode="after")
    def require_exact_search_decision_and_identity(self) -> Self:
        if (
            self.baseline_freeze.roots != self.roots
            or self.baseline_freeze.corpus_id != self.registration.corpus_id
            or self.economic_arm != self.registration.economic_arm
            or self.event_positions != self.registration.event_positions
            or self.event_ids != self.registration.event_ids
        ):
            raise ValueError("Gate 3 exact audit differs from its canonical registration")
        expected_transform = {
            "central": "central_unmodified",
            "adverse": "adverse_unmodified",
            "null": "central_unique_material_event_information_null",
        }[self.economic_arm]
        expected_profile, expected_material = {
            "central": ("central", "preserve_registered_material_keys"),
            "adverse": ("adverse", "preserve_registered_material_keys"),
            "null": (
                "central",
                "unique_material_key_per_event_information_null",
            ),
        }[self.economic_arm]
        if (
            self.economic_transform != expected_transform
            or self.economic_profile != expected_profile
            or self.material_rule != expected_material
        ):
            raise ValueError("Gate 3 exact audit arm transform differs")
        if tuple(
            item.evidence.projection_attestation.material_key for item in self.material_audits
        ) != tuple(
            sorted(
                item.evidence.projection_attestation.material_key for item in self.material_audits
            )
        ) or len(
            {item.evidence.projection_attestation.material_key for item in self.material_audits}
        ) != len(self.material_audits):
            raise ValueError("Gate 3 exact-audit material evidence is not canonical")
        expected_policy = _gate3_underlying_policy(self.baseline_freeze.selected_policy_id)
        source_maps = []
        for material in self.material_audits:
            attestation = material.evidence.projection_attestation
            shard = material.evidence.shard_trace
            if (
                material.evidence.roots != self.roots
                or attestation.registration_kind != "exact_audit"
                or attestation.source_registration_id != self.registration.audit_id
                or attestation.source_registration_content_sha256
                != self.registration.content_sha256
                or attestation.source_stream_id != self.registration.source_stream_id
                or attestation.corpus_id != self.registration.corpus_id
                or attestation.registered_exact_audit_arm != self.registration.economic_arm
                or attestation.registered_exact_audit_material_rule != expected_material
                or attestation.economic_arm != expected_profile
                or attestation.policy != expected_policy
                or material.evidence.replay_input.policy != expected_policy
                or shard.policy_id != self.baseline_freeze.selected_policy_id
                or any(
                    parity.projection_rule != "all_registered_candidates"
                    for parity in attestation.candidate_action_parity
                )
            ):
                raise ValueError("Gate 3 exact audit differs from projection registration")
            source_maps.extend(attestation.source_event_map)
        source_maps.sort(key=lambda item: item.source_event_position)
        if (
            tuple(item.source_event_position for item in source_maps) != self.event_positions
            or tuple(item.source_event_id for item in source_maps) != self.event_ids
        ):
            raise ValueError("Gate 3 exact audit projection does not cover registered events")
        if self.economic_arm == "null" and (
            len(self.material_audits) != 3
            or any(
                len(item.evidence.projection_attestation.source_event_map) != 1
                for item in self.material_audits
            )
        ):
            raise ValueError("Gate 3 exact-audit information-null material transform differs")
        first_material = next(
            (
                item
                for item in self.material_audits
                if item.evidence.projection_attestation.source_event_map[0].source_event_position
                == self.event_positions[0]
            ),
            None,
        )
        if first_material is None:
            raise ValueError("Gate 3 exact audit lacks its first chronological material")
        other_optimum = sum(
            (
                _signed_cost(item.exact_optimal_final_cost, label="material exact optimum")
                for item in self.material_audits
                if item is not first_material
            ),
            Decimal(0),
        )
        expected_episode_scores = tuple(
            Gate3ExactRootScore(
                action_id=item.action_id,
                action_kind=item.action_kind,
                exact_final_cost=_format_signed_cost(
                    _signed_cost(item.exact_final_cost, label="exact root cost") + other_optimum
                ),
            )
            for item in first_material.exact_root_scores
        )
        costs = {
            item.action_id: _signed_cost(item.exact_final_cost, label="episode exact root cost")
            for item in expected_episode_scores
        }
        optimum = min(costs.values())
        optimal_ids = tuple(sorted(key for key, value in costs.items() if value == optimum))
        selected = first_material.selected_action_id
        combinations = 1
        for item in self.material_audits:
            combinations *= len(item.exact_root_scores)
        cartesian_optimum = sum(
            (
                _signed_cost(item.exact_optimal_final_cost, label="material exact optimum")
                for item in self.material_audits
            ),
            Decimal(0),
        )
        episode_pass = first_material.passes
        if (
            self.episode_root_scores != expected_episode_scores
            or self.selected_action_id != selected
            or self.exact_optimal_action_ids != optimal_ids
            or self.cartesian_combination_count != combinations
            or self.cartesian_optimal_final_cost != _format_signed_cost(cartesian_optimum)
            or self.selected_is_exact_optimal is not (selected in optimal_ids)
            or self.passes is not episode_pass
        ):
            raise ValueError("Gate 3 exact audit result does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"trace_id", "content_sha256"})
        if self.trace_id != f"yfm11g3audit-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 exact audit identity does not match semantic content")
        return self


def build_gate3_exact_audit_trace(
    *,
    roots: Gate3RootBinding,
    baseline_freeze: Gate3BaselineCalibrationFreeze,
    registration: M11ExactAuditEpisode,
    material_audits: tuple[Gate3ExactMaterialAudit, ...],
) -> Gate3ExactAuditTrace:
    ordered = tuple(
        sorted(
            material_audits,
            key=lambda item: item.evidence.projection_attestation.material_key,
        )
    )
    first_material = next(
        (
            item
            for item in ordered
            if item.evidence.projection_attestation.source_event_map[0].source_event_position
            == registration.event_positions[0]
        ),
        None,
    )
    if first_material is None:
        raise ValueError("Gate 3 exact audit projection registration lacks the first event")
    other_optimum = sum(
        (
            _signed_cost(item.exact_optimal_final_cost, label="material exact optimum")
            for item in ordered
            if item is not first_material
        ),
        Decimal(0),
    )
    episode_scores = tuple(
        Gate3ExactRootScore(
            action_id=item.action_id,
            action_kind=item.action_kind,
            exact_final_cost=_format_signed_cost(
                _signed_cost(item.exact_final_cost, label="exact root cost") + other_optimum
            ),
        )
        for item in first_material.exact_root_scores
    )
    episode_costs = {
        item.action_id: _signed_cost(item.exact_final_cost, label="episode exact root cost")
        for item in episode_scores
    }
    optimum = min(episode_costs.values())
    optimal_ids = tuple(sorted(key for key, value in episode_costs.items() if value == optimum))
    selected_action_id = first_material.selected_action_id
    combinations = 1
    for item in ordered:
        combinations *= len(item.exact_root_scores)
    cartesian_optimum = sum(
        (
            _signed_cost(item.exact_optimal_final_cost, label="material exact optimum")
            for item in ordered
        ),
        Decimal(0),
    )
    economic_arm = registration.economic_arm
    economic_transform = {
        "central": "central_unmodified",
        "adverse": "adverse_unmodified",
        "null": "central_unique_material_event_information_null",
    }[economic_arm]
    economic_profile, material_rule = {
        "central": ("central", "preserve_registered_material_keys"),
        "adverse": ("adverse", "preserve_registered_material_keys"),
        "null": ("central", "unique_material_key_per_event_information_null"),
    }[economic_arm]
    semantic = {
        "schema_version": "yieldforge.m11-gate3-exact-audit-trace.v1",
        "roots": roots.model_dump(mode="json"),
        "baseline_freeze": baseline_freeze.model_dump(mode="json"),
        "registration": registration.model_dump(mode="json"),
        "economic_arm": economic_arm,
        "economic_transform": economic_transform,
        "economic_profile": economic_profile,
        "payload_transform": "unmodified",
        "material_rule": material_rule,
        "registered_candidates_retained": True,
        "event_positions": registration.event_positions,
        "event_ids": registration.event_ids,
        "material_audits": [item.model_dump(mode="json") for item in ordered],
        "episode_root_scores": [item.model_dump(mode="json") for item in episode_scores],
        "selected_action_id": selected_action_id,
        "exact_optimal_action_ids": optimal_ids,
        "cartesian_combination_count": combinations,
        "cartesian_optimal_final_cost": _format_signed_cost(cartesian_optimum),
        "cartesian_separability_verified": True,
        "selected_is_exact_optimal": selected_action_id in optimal_ids,
        "passes": first_material.passes,
    }
    digest = semantic_sha256(semantic)
    return Gate3ExactAuditTrace(
        trace_id=f"yfm11g3audit-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        baseline_freeze=baseline_freeze,
        registration=registration,
        economic_arm=economic_arm,
        economic_transform=economic_transform,
        economic_profile=economic_profile,
        material_rule=material_rule,
        event_positions=registration.event_positions,
        event_ids=registration.event_ids,
        material_audits=ordered,
        episode_root_scores=episode_scores,
        selected_action_id=selected_action_id,
        exact_optimal_action_ids=optimal_ids,
        cartesian_combination_count=combinations,
        cartesian_optimal_final_cost=_format_signed_cost(cartesian_optimum),
        cartesian_separability_verified=True,
        selected_is_exact_optimal=selected_action_id in optimal_ids,
        passes=first_material.passes,
    )


class Gate3TwinControl(FrozenExperimentModel):
    """One no-signal twin cell reference and recomputable B/F/K metric."""

    schema_version: Literal["yieldforge.m11-gate3-twin-control.v1"] = (
        "yieldforge.m11-gate3-twin-control.v1"
    )
    control_id: StrictStr = Field(pattern=r"^yfm11g3twin-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    source_stream_id: StrictStr = Field(min_length=1)
    twin_stream_id: StrictStr = Field(min_length=1)
    corpus_id: Gate3CorpusId
    twin_cell_id: StrictStr = Field(pattern=r"^yfm11g3cell-[0-9a-f]{24}$")
    twin_cell_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    baseline_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    full_future_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    known_only_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    no_signal_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)

    @model_validator(mode="after")
    def require_metric_and_identity(self) -> Self:
        baseline = _signed_cost(self.baseline_cost, label="twin baseline cost")
        full = _signed_cost(self.full_future_cost, label="twin full cost")
        if baseline <= 0:
            raise ValueError("Gate 3 twin baseline cost must be positive")
        with localcontext() as context:
            context.prec = 50
            expected = Decimal(100) * (baseline - full) / baseline
        if self.no_signal_savings_percent != _format_metric(expected):
            raise ValueError("Gate 3 twin savings does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"control_id", "content_sha256"})
        if self.control_id != f"yfm11g3twin-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 twin identity does not match semantic content")
        return self


def build_gate3_twin_control(
    *,
    roots: Gate3RootBinding,
    source_stream_id: str,
    twin_cell: Gate3StreamCell,
) -> Gate3TwinControl:
    if twin_cell.roots != roots:
        raise ValueError("Gate 3 twin cell roots differ")
    semantic = {
        "schema_version": "yieldforge.m11-gate3-twin-control.v1",
        "roots": roots.model_dump(mode="json"),
        "source_stream_id": source_stream_id,
        "twin_stream_id": twin_cell.stream_id,
        "corpus_id": twin_cell.corpus_id,
        "twin_cell_id": twin_cell.cell_id,
        "twin_cell_content_sha256": twin_cell.content_sha256,
        "baseline_cost": twin_cell.baseline_cost,
        "full_future_cost": twin_cell.full_future_cost,
        "known_only_cost": twin_cell.known_only_cost,
        "no_signal_savings_percent": twin_cell.full_future_savings_percent,
    }
    digest = semantic_sha256(semantic)
    return Gate3TwinControl(
        control_id=f"yfm11g3twin-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


class Gate3NoSignalSummary(FrozenExperimentModel):
    """Mean no-signal leakage classification at the frozen strict boundaries."""

    schema_version: Literal["yieldforge.m11-gate3-no-signal-summary.v1"] = (
        "yieldforge.m11-gate3-no-signal-summary.v1"
    )
    summary_id: StrictStr = Field(pattern=r"^yfm11g3ns-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: Gate3CorpusId
    control_ids: tuple[StrictStr, ...] = Field(min_length=20, max_length=20)
    control_content_sha256s: tuple[StrictStr, ...] = Field(min_length=20, max_length=20)
    mean_no_signal_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    diagnosis_minimum_percent: Literal[0.3] = 0.3
    invalid_above_percent: Literal[0.5] = 0.5
    classification: Literal["clean", "diagnosis_required", "invalid"]

    @model_validator(mode="after")
    def require_boundary_and_identity(self) -> Self:
        if len(set(self.control_ids)) != 20 or len(set(self.control_content_sha256s)) != 20:
            raise ValueError("Gate 3 no-signal summary requires twenty unique controls")
        mean = Decimal(self.mean_no_signal_savings_percent)
        expected = (
            "invalid"
            if mean > Decimal("0.5")
            else "diagnosis_required"
            if mean >= Decimal("0.3")
            else "clean"
        )
        if self.classification != expected:
            raise ValueError("Gate 3 no-signal classification differs from frozen boundaries")
        digest = semantic_sha256(self, excluded_fields={"summary_id", "content_sha256"})
        if self.summary_id != f"yfm11g3ns-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 no-signal identity does not match semantic content")
        return self


def summarize_gate3_no_signal(
    controls: tuple[Gate3TwinControl, ...],
) -> Gate3NoSignalSummary:
    if len(controls) != 20 or len({item.control_id for item in controls}) != 20:
        raise ValueError("Gate 3 no-signal summary requires twenty unique twin controls")
    corpus_ids = {item.corpus_id for item in controls}
    if len(corpus_ids) != 1:
        raise ValueError("Gate 3 no-signal summary cannot mix corpora")
    corpus_id = controls[0].corpus_id
    with localcontext() as context:
        context.prec = 50
        mean = sum(
            (Decimal(item.no_signal_savings_percent) for item in controls),
            Decimal(0),
        ) / Decimal(20)
    classification = (
        "invalid"
        if mean > Decimal("0.5")
        else "diagnosis_required"
        if mean >= Decimal("0.3")
        else "clean"
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-no-signal-summary.v1",
        "corpus_id": corpus_id,
        "control_ids": tuple(item.control_id for item in controls),
        "control_content_sha256s": tuple(item.content_sha256 for item in controls),
        "mean_no_signal_savings_percent": _format_metric(mean),
        "diagnosis_minimum_percent": 0.3,
        "invalid_above_percent": 0.5,
        "classification": classification,
    }
    digest = semantic_sha256(semantic)
    return Gate3NoSignalSummary(
        summary_id=f"yfm11g3ns-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


class Gate3ValidityReceipt(FrozenExperimentModel):
    """Complete cheap Gate 3 validity controls, classified without caller summaries."""

    schema_version: Literal["yieldforge.m11-gate3-validity-receipt.v1"] = (
        "yieldforge.m11-gate3-validity-receipt.v1"
    )
    receipt_id: StrictStr = Field(pattern=r"^yfm11g3valid-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    hard_nulls: tuple[
        Gate3HardNullControl,
        Gate3HardNullControl,
        Gate3HardNullControl,
        Gate3HardNullControl,
        Gate3HardNullControl,
        Gate3HardNullControl,
    ]
    twin_controls: tuple[Gate3TwinControl, ...] = Field(min_length=40, max_length=40)
    exact_audits: tuple[Gate3ExactAuditTrace, ...] = Field(min_length=12, max_length=12)
    no_signal_summaries: tuple[Gate3NoSignalSummary, Gate3NoSignalSummary]
    failure_codes: tuple[StrictStr, ...]
    diagnosis_codes: tuple[StrictStr, ...]
    status: Literal["valid", "diagnosis_required", "invalid"]
    exact_control_census: Literal[True] = True
    raw_controls_revalidated: Literal[True] = True

    @model_validator(mode="after")
    def require_raw_control_decision_and_identity(self) -> Self:
        if (
            any(item.roots != self.roots for item in self.hard_nulls)
            or any(item.roots != self.roots for item in self.twin_controls)
            or any(item.roots != self.roots for item in self.exact_audits)
        ):
            raise ValueError("Gate 3 validity control roots differ")
        corpus_order = ("lectra-m3-m4",) * 3 + ("loco-2dics",) * 3
        if tuple(item.corpus_id for item in self.hard_nulls) != corpus_order:
            raise ValueError("Gate 3 hard-null corpus census differs")
        hard_null_kind_order = (
            "single_action",
            "unique_materials_single_action",
            "all_work_known_single_action",
        ) * 2
        if tuple(item.null_kind for item in self.hard_nulls) != hard_null_kind_order:
            raise ValueError("Gate 3 hard-null registered kind order differs")
        if tuple(item.corpus_id for item in self.twin_controls) != (
            ("lectra-m3-m4",) * 20 + ("loco-2dics",) * 20
        ):
            raise ValueError("Gate 3 twin-control corpus census differs")
        if tuple(item.corpus_id for item in self.exact_audits) != (
            ("lectra-m3-m4",) * 6 + ("loco-2dics",) * 6
        ):
            raise ValueError("Gate 3 exact-audit corpus census differs")
        exact_arm_order = ("central", "central", "adverse", "adverse", "null", "null") * 2
        if (
            tuple(item.economic_arm for item in self.exact_audits) != exact_arm_order
            or tuple(item.registration.audit_ordinal for item in self.exact_audits)
            != tuple(range(6)) * 2
        ):
            raise ValueError("Gate 3 exact-audit registered arm order differs")
        expected_summaries = (
            summarize_gate3_no_signal(self.twin_controls[:20]),
            summarize_gate3_no_signal(self.twin_controls[20:]),
        )
        if self.no_signal_summaries != expected_summaries:
            raise ValueError("Gate 3 no-signal summaries differ from raw twin cells")
        failures = tuple(
            sorted(
                (
                    *(
                        f"hard_null:{item.control_id}"
                        for item in self.hard_nulls
                        if not item.passes
                    ),
                    *(
                        f"exact_audit:{item.audit_id}"
                        for item in self.exact_audits
                        if not item.passes
                    ),
                    *(
                        f"no_signal:{item.corpus_id}"
                        for item in self.no_signal_summaries
                        if item.classification == "invalid"
                    ),
                )
            )
        )
        diagnoses = tuple(
            sorted(
                f"no_signal:{item.corpus_id}"
                for item in self.no_signal_summaries
                if item.classification == "diagnosis_required"
            )
        )
        status = "invalid" if failures else "diagnosis_required" if diagnoses else "valid"
        if (
            self.failure_codes != failures
            or self.diagnosis_codes != diagnoses
            or self.status != status
        ):
            raise ValueError("Gate 3 validity status differs from raw controls")
        digest = semantic_sha256(self, excluded_fields={"receipt_id", "content_sha256"})
        if self.receipt_id != f"yfm11g3valid-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 validity receipt identity does not match semantic content")
        return self


def evaluate_gate3_validity_controls(
    *,
    roots: Gate3RootBinding,
    hard_nulls: tuple[Gate3HardNullControl, ...],
    twin_controls: tuple[Gate3TwinControl, ...],
    exact_audits: tuple[Gate3ExactAuditTrace, ...],
) -> Gate3ValidityReceipt:
    """Derive validity only from the complete registered raw control population."""

    if len(hard_nulls) != 6 or len(twin_controls) != 40 or len(exact_audits) != 12:
        raise ValueError("Gate 3 validity controls differ from the frozen 6/40/12 census")
    if (
        len({item.control_id for item in hard_nulls}) != 6
        or len({item.control_id for item in twin_controls}) != 40
        or len({item.audit_id for item in exact_audits}) != 12
    ):
        raise ValueError("Gate 3 validity controls require unique registered IDs")
    hard_null_tuple = tuple(hard_nulls)
    if len(hard_null_tuple) != 6:  # narrows the fixed Pydantic tuple for static checkers.
        raise AssertionError("unreachable Gate 3 hard-null census")
    summaries = (
        summarize_gate3_no_signal(twin_controls[:20]),
        summarize_gate3_no_signal(twin_controls[20:]),
    )
    failures = tuple(
        sorted(
            (
                *(f"hard_null:{item.control_id}" for item in hard_null_tuple if not item.passes),
                *(f"exact_audit:{item.audit_id}" for item in exact_audits if not item.passes),
                *(
                    f"no_signal:{item.corpus_id}"
                    for item in summaries
                    if item.classification == "invalid"
                ),
            )
        )
    )
    diagnoses = tuple(
        sorted(
            f"no_signal:{item.corpus_id}"
            for item in summaries
            if item.classification == "diagnosis_required"
        )
    )
    status = "invalid" if failures else "diagnosis_required" if diagnoses else "valid"
    semantic = {
        "schema_version": "yieldforge.m11-gate3-validity-receipt.v1",
        "roots": roots.model_dump(mode="json"),
        "hard_nulls": [item.model_dump(mode="json") for item in hard_null_tuple],
        "twin_controls": [item.model_dump(mode="json") for item in twin_controls],
        "exact_audits": [item.model_dump(mode="json") for item in exact_audits],
        "no_signal_summaries": [item.model_dump(mode="json") for item in summaries],
        "failure_codes": failures,
        "diagnosis_codes": diagnoses,
        "status": status,
        "exact_control_census": True,
        "raw_controls_revalidated": True,
    }
    digest = semantic_sha256(semantic)
    return Gate3ValidityReceipt(
        receipt_id=f"yfm11g3valid-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        hard_nulls=hard_null_tuple,  # type: ignore[arg-type]
        twin_controls=twin_controls,
        exact_audits=exact_audits,
        no_signal_summaries=summaries,
        failure_codes=failures,
        diagnosis_codes=diagnoses,
        status=status,
    )


class Gate3RootBinding(FrozenExperimentModel):
    """Exact upstream roots and adapter configuration admitted to Gate 3."""

    schema_version: Literal["yieldforge.m11-gate3-root-binding.v1"] = (
        "yieldforge.m11-gate3-root-binding.v1"
    )
    binding_id: StrictStr = Field(pattern=r"^yfm11g3root-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(pattern=r"^yfm11c-[0-9a-f]{24}$")
    contract_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    population_id: StrictStr = Field(pattern=r"^yfm11pop-[0-9a-f]{24}$")
    population_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate1_run_id: StrictStr = Field(pattern=r"^yfm11g1run-[0-9a-f]{24}$")
    gate1_run_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate1_evaluation_result_id: StrictStr = Field(pattern=r"^yfm11g1r-[0-9a-f]{24}$")
    gate1_evaluation_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate2_run_id: StrictStr = Field(pattern=r"^yfm11g2run-[0-9a-f]{24}$")
    gate2_run_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate2_evaluation_result_id: StrictStr = Field(pattern=r"^yfm11g2r-[0-9a-f]{24}$")
    gate2_evaluation_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate3_config_id: StrictStr = Field(pattern=r"^yfm11g3c-[0-9a-f]{24}$")
    gate3_config_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    adapter_runtime_config_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate2_survived: Literal[True] = True

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        digest = semantic_sha256(self, excluded_fields={"binding_id", "content_sha256"})
        if self.binding_id != f"yfm11g3root-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 root binding identity does not match semantic content")
        return self


class Gate3BaselineCalibrationScore(FrozenExperimentModel):
    """Calibration-only total for one registered as-of baseline policy."""

    schema_version: Literal["yieldforge.m11-gate3-baseline-score.v1"] = (
        "yieldforge.m11-gate3-baseline-score.v1"
    )
    score_id: StrictStr = Field(pattern=r"^yfm11g3bsc-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    root_binding_id: StrictStr = Field(pattern=r"^yfm11g3root-[0-9a-f]{24}$")
    root_binding_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: Gate3CorpusId
    policy_id: Gate3BaselinePolicyId
    calibration_stream_costs: tuple[tuple[StrictStr, StrictStr], ...] = Field(
        min_length=8,
        max_length=8,
    )
    calibration_stream_sheet_openings: tuple[tuple[StrictStr, StrictInt], ...] = Field(
        min_length=8,
        max_length=8,
    )
    invalid_stream_count: StrictInt = Field(ge=0, le=8)
    total_cost: StrictStr = Field(pattern=_COST_PATTERN)
    mean_cost: StrictStr = Field(pattern=_COST_PATTERN)
    median_cost: StrictStr = Field(pattern=_COST_PATTERN)
    total_sheet_openings: StrictInt = Field(ge=0)
    verified_feasible: Literal[True] = True
    calibration_only: Literal[True] = True
    confirmation_inputs_used: Literal[False] = False

    @model_validator(mode="after")
    def require_costs_and_identity(self) -> Self:
        stream_ids = tuple(stream_id for stream_id, _ in self.calibration_stream_costs)
        if len(set(stream_ids)) != 8 or any(not stream_id for stream_id in stream_ids):
            raise ValueError("Gate 3 calibration score requires eight unique stream IDs")
        opening_ids = tuple(stream_id for stream_id, _ in self.calibration_stream_sheet_openings)
        openings = tuple(value for _, value in self.calibration_stream_sheet_openings)
        if opening_ids != stream_ids or any(
            type(value) is not int or value < 0 for value in openings
        ):
            raise ValueError("Gate 3 calibration sheet openings differ from the stream census")
        costs = tuple(
            _cost(value, label="calibration stream cost")
            for _, value in self.calibration_stream_costs
        )
        total = sum(costs, start=Decimal(0))
        ordered = tuple(sorted(costs))
        mean = (total / Decimal(8)).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)
        median = ((ordered[3] + ordered[4]) / Decimal(2)).quantize(
            _COST_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if (
            self.total_cost,
            self.mean_cost,
            self.median_cost,
            self.total_sheet_openings,
        ) != (
            _format_cost(total),
            _format_cost(mean),
            _format_cost(median),
            sum(openings),
        ):
            raise ValueError("Gate 3 calibration selector terms do not reconcile")
        digest = semantic_sha256(self, excluded_fields={"score_id", "content_sha256"})
        if self.score_id != f"yfm11g3bsc-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 calibration score identity does not match semantic content")
        return self


class Gate3BaselineCalibrationFreeze(FrozenExperimentModel):
    """Complete outcome-blind baseline selection frozen before confirmation."""

    schema_version: Literal["yieldforge.m11-gate3-baseline-freeze.v1"] = (
        "yieldforge.m11-gate3-baseline-freeze.v1"
    )
    freeze_id: StrictStr = Field(pattern=r"^yfm11g3bf-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    corpus_id: Gate3CorpusId
    registered_policy_ids: tuple[Gate3BaselinePolicyId, ...]
    calibration_stream_ids: tuple[StrictStr, ...] = Field(min_length=8, max_length=8)
    policy_scores: tuple[Gate3BaselineCalibrationScore, ...] = Field(
        min_length=6,
        max_length=6,
    )
    selection_rule: Literal["lowest_mean_net_cost_then_invalid_median_sheet_openings_policy_id"] = (
        "lowest_mean_net_cost_then_invalid_median_sheet_openings_policy_id"
    )
    selected_policy_id: Gate3BaselinePolicyId
    tied_lowest_policy_ids: tuple[Gate3BaselinePolicyId, ...] = Field(min_length=1)
    calibration_only: Literal[True] = True
    confirmation_inputs_used: StrictBool = False

    @model_validator(mode="after")
    def require_complete_selection_and_identity(self) -> Self:
        if self.registered_policy_ids != GATE3_BASELINE_POLICY_IDS:
            raise ValueError("Gate 3 freeze differs from the registered policy registry")
        if len(set(self.calibration_stream_ids)) != 8:
            raise ValueError("Gate 3 freeze requires eight unique calibration streams")
        expected_score_order = tuple(sorted(GATE3_BASELINE_POLICY_IDS))
        if tuple(item.policy_id for item in self.policy_scores) != expected_score_order:
            raise ValueError("Gate 3 calibration scores do not cover the policy registry")
        if any(
            item.root_binding_id != self.roots.binding_id
            or item.root_binding_content_sha256 != self.roots.content_sha256
            or item.corpus_id != self.corpus_id
            or tuple(stream_id for stream_id, _ in item.calibration_stream_costs)
            != self.calibration_stream_ids
            or tuple(stream_id for stream_id, _ in item.calibration_stream_sheet_openings)
            != self.calibration_stream_ids
            for item in self.policy_scores
        ):
            raise ValueError("Gate 3 calibration score roots or stream census differ")
        if any(item.invalid_stream_count for item in self.policy_scores):
            raise ValueError("Gate 3 baseline freeze contains an invalid policy stream")
        selector_terms = {
            item.policy_id: (
                _cost(item.mean_cost, label="policy mean"),
                item.invalid_stream_count,
                _cost(item.median_cost, label="policy median"),
                item.total_sheet_openings,
            )
            for item in self.policy_scores
        }
        lowest = min(selector_terms.values())
        tied = tuple(
            sorted(policy_id for policy_id, value in selector_terms.items() if value == lowest)
        )
        if self.tied_lowest_policy_ids != tied or self.selected_policy_id != tied[0]:
            raise ValueError("Gate 3 selected baseline differs from exact calibration scores")
        if self.confirmation_inputs_used:
            raise ValueError("Gate 3 baseline freeze cannot use confirmation inputs")
        digest = semantic_sha256(self, excluded_fields={"freeze_id", "content_sha256"})
        if self.freeze_id != f"yfm11g3bf-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 baseline freeze identity does not match semantic content")
        return self


def build_gate3_root_binding(
    *,
    contract_id: str,
    contract_content_sha256: str,
    population_id: str,
    population_content_sha256: str,
    gate1_run_id: str,
    gate1_run_content_sha256: str,
    gate1_evaluation_result_id: str,
    gate1_evaluation_result_content_sha256: str,
    gate2_run_id: str,
    gate2_run_content_sha256: str,
    gate2_evaluation_result_id: str,
    gate2_evaluation_result_content_sha256: str,
    gate3_config_id: str,
    gate3_config_content_sha256: str,
    adapter_runtime_config_sha256: str,
) -> Gate3RootBinding:
    semantic = {
        "schema_version": "yieldforge.m11-gate3-root-binding.v1",
        "contract_id": contract_id,
        "contract_content_sha256": contract_content_sha256,
        "population_id": population_id,
        "population_content_sha256": population_content_sha256,
        "gate1_run_id": gate1_run_id,
        "gate1_run_content_sha256": gate1_run_content_sha256,
        "gate1_evaluation_result_id": gate1_evaluation_result_id,
        "gate1_evaluation_result_content_sha256": gate1_evaluation_result_content_sha256,
        "gate2_run_id": gate2_run_id,
        "gate2_run_content_sha256": gate2_run_content_sha256,
        "gate2_evaluation_result_id": gate2_evaluation_result_id,
        "gate2_evaluation_result_content_sha256": gate2_evaluation_result_content_sha256,
        "gate3_config_id": gate3_config_id,
        "gate3_config_content_sha256": gate3_config_content_sha256,
        "adapter_runtime_config_sha256": adapter_runtime_config_sha256,
        "gate2_survived": True,
    }
    digest = semantic_sha256(semantic)
    return Gate3RootBinding(
        binding_id=f"yfm11g3root-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _build_baseline_score(
    *,
    roots: Gate3RootBinding,
    corpus_id: Gate3CorpusId,
    policy_id: Gate3BaselinePolicyId,
    calibration_stream_ids: tuple[str, ...],
    stream_costs: tuple[str, ...],
    stream_sheet_openings: tuple[int, ...],
    invalid_stream_count: int,
) -> Gate3BaselineCalibrationScore:
    pairs = tuple(zip(calibration_stream_ids, stream_costs, strict=True))
    opening_pairs = tuple(zip(calibration_stream_ids, stream_sheet_openings, strict=True))
    costs = tuple(_cost(value, label="calibration stream cost") for value in stream_costs)
    total = sum(costs, Decimal(0))
    ordered = tuple(sorted(costs))
    mean = (total / Decimal(8)).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)
    median = ((ordered[3] + ordered[4]) / Decimal(2)).quantize(
        _COST_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-baseline-score.v1",
        "root_binding_id": roots.binding_id,
        "root_binding_content_sha256": roots.content_sha256,
        "corpus_id": corpus_id,
        "policy_id": policy_id,
        "calibration_stream_costs": pairs,
        "calibration_stream_sheet_openings": opening_pairs,
        "invalid_stream_count": invalid_stream_count,
        "total_cost": _format_cost(total),
        "mean_cost": _format_cost(mean),
        "median_cost": _format_cost(median),
        "total_sheet_openings": sum(stream_sheet_openings),
        "verified_feasible": True,
        "calibration_only": True,
        "confirmation_inputs_used": False,
    }
    digest = semantic_sha256(semantic)
    return Gate3BaselineCalibrationScore(
        score_id=f"yfm11g3bsc-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def select_gate3_baseline_policy(
    *,
    roots: Gate3RootBinding,
    corpus_id: Gate3CorpusId,
    calibration_stream_ids: tuple[str, ...],
    policy_stream_costs: Mapping[str, tuple[str, ...]],
    policy_stream_sheet_openings: Mapping[str, tuple[int, ...]],
    policy_invalid_stream_counts: Mapping[str, int],
) -> Gate3BaselineCalibrationFreeze:
    """Score the complete finite baseline family using calibration inputs only."""

    if len(calibration_stream_ids) != 8 or len(set(calibration_stream_ids)) != 8:
        raise ValueError("Gate 3 baseline selection requires eight unique calibration streams")
    registry = set(GATE3_BASELINE_POLICY_IDS)
    if (
        set(policy_stream_costs) != registry
        or set(policy_stream_sheet_openings) != registry
        or set(policy_invalid_stream_counts) != registry
    ):
        raise ValueError("Gate 3 baseline selection differs from the registered policy registry")
    if any(len(costs) != 8 for costs in policy_stream_costs.values()) or any(
        len(openings) != 8 for openings in policy_stream_sheet_openings.values()
    ):
        raise ValueError("Gate 3 baseline policy score requires eight stream outcomes")
    if any(
        type(count) is not int or count < 0 or count > 8
        for count in policy_invalid_stream_counts.values()
    ):
        raise ValueError("Gate 3 baseline invalid-stream counts are malformed")
    if any(policy_invalid_stream_counts.values()):
        raise ValueError("Gate 3 baseline selection encountered an invalid policy stream")
    if any(
        type(value) is not int or value < 0
        for openings in policy_stream_sheet_openings.values()
        for value in openings
    ):
        raise ValueError("Gate 3 baseline sheet-opening evidence is malformed")
    scores = tuple(
        _build_baseline_score(
            roots=roots,
            corpus_id=corpus_id,
            policy_id=policy_id,
            calibration_stream_ids=calibration_stream_ids,
            stream_costs=policy_stream_costs[policy_id],
            stream_sheet_openings=policy_stream_sheet_openings[policy_id],
            invalid_stream_count=policy_invalid_stream_counts[policy_id],
        )
        for policy_id in sorted(GATE3_BASELINE_POLICY_IDS)
    )
    selector_terms = {
        item.policy_id: (
            _cost(item.mean_cost, label="policy mean"),
            item.invalid_stream_count,
            _cost(item.median_cost, label="policy median"),
            item.total_sheet_openings,
        )
        for item in scores
    }
    lowest = min(selector_terms.values())
    tied = tuple(
        sorted(policy_id for policy_id, value in selector_terms.items() if value == lowest)
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-baseline-freeze.v1",
        "roots": roots.model_dump(mode="json"),
        "corpus_id": corpus_id,
        "registered_policy_ids": GATE3_BASELINE_POLICY_IDS,
        "calibration_stream_ids": calibration_stream_ids,
        "policy_scores": [item.model_dump(mode="json") for item in scores],
        "selection_rule": ("lowest_mean_net_cost_then_invalid_median_sheet_openings_policy_id"),
        "selected_policy_id": tied[0],
        "tied_lowest_policy_ids": tied,
        "calibration_only": True,
        "confirmation_inputs_used": False,
    }
    digest = semantic_sha256(semantic)
    return Gate3BaselineCalibrationFreeze(
        freeze_id=f"yfm11g3bf-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        corpus_id=corpus_id,
        registered_policy_ids=GATE3_BASELINE_POLICY_IDS,
        calibration_stream_ids=calibration_stream_ids,
        policy_scores=scores,
        selected_policy_id=tied[0],
        tied_lowest_policy_ids=tied,
        confirmation_inputs_used=False,
    )


def _ledger_from_m7_ledger(ledger: object) -> Gate3CostLedger:
    return build_gate3_cost_ledger(
        purchase_cost=_format_cost(Decimal(str(ledger.purchase_cost))),  # type: ignore[attr-defined]
        storage_cost=_format_cost(Decimal(str(ledger.storage_cost))),  # type: ignore[attr-defined]
        return_handling_cost=_format_cost(  # type: ignore[attr-defined]
            Decimal(str(ledger.return_handling_cost))
        ),
        retrieval_handling_cost=_format_cost(  # type: ignore[attr-defined]
            Decimal(str(ledger.retrieval_handling_cost))
        ),
        scrap_proceeds=_format_cost(Decimal(str(ledger.scrap_proceeds))),  # type: ignore[attr-defined]
        terminal_credit=_format_cost(  # type: ignore[attr-defined]
            Decimal(str(ledger.terminal_scrap_credit))
        ),
    )


def _ledger_from_m7_result(result: M7ReplayResult) -> Gate3CostLedger:
    return _ledger_from_m7_ledger(result.terminal.cumulative_costs)


def _m7_event_catalog_action_id(event: object) -> str:
    action = event.action  # type: ignore[attr-defined]
    if action.kind.value == "open_standard_sheet":
        return f"m7-standard:{action.candidate_id}"
    return action.action_id


def gate3_inventory_sha256(inventory: tuple[object, ...]) -> str:
    """Return the canonical hash used to bind a decision to an executed M7 inventory."""

    digest = semantic_sha256(
        {
            "inventory": [item.model_dump(mode="json") for item in inventory],  # type: ignore[attr-defined]
        }
    )
    return f"sha256:{digest}"


class Gate3AppliedActionContext(FrozenExperimentModel):
    """Executed catalog-to-materialized action binding and its policy-visible cost."""

    schema_version: Literal["yieldforge.m11-gate3-applied-context.v1"] = (
        "yieldforge.m11-gate3-applied-context.v1"
    )
    context_id: StrictStr = Field(pattern=r"^yfm11g3ctx-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_id: StrictStr = Field(pattern=r"^yfm11g3dec-[0-9a-f]{24}$")
    decision_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    applied_event_id: StrictStr = Field(pattern=r"^yfm7e-[0-9a-f]{24}$")
    catalog_action_id: StrictStr = Field(min_length=1)
    materialized_action_id: StrictStr = Field(min_length=1)
    action_kind: Literal["open_standard_sheet", "consume_remnant"]
    candidate_id: StrictStr = Field(min_length=1)
    selected_stock_id: StrictStr = Field(min_length=1)
    candidate_width: StrictFloat = Field(gt=0)
    immediate_net_cost: StrictStr = Field(pattern=_SIGNED_COST_PATTERN)
    selected_remnant_age_hours: StrictFloat = Field(ge=0)
    returned_regularity: StrictFloat = Field(ge=0, le=1)
    known_order_lookahead_term: Literal[0.0] = 0.0

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        digest = semantic_sha256(self, excluded_fields={"context_id", "content_sha256"})
        if self.context_id != f"yfm11g3ctx-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 applied action context identity differs")
        return self


def build_gate3_applied_action_context(
    *,
    decision: Gate3DecisionTrace,
    step: M7StepResult,
) -> Gate3AppliedActionContext:
    binding = step.action_binding
    context = step.selected_context
    event = step.event
    immediate = format(
        Decimal(str(context.immediate_net_cost)).quantize(
            _COST_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
        ".6f",
    )
    expected_catalog_action = _m7_event_catalog_action_id(event)
    if (
        binding.catalog_action_id != context.action_id
        or binding.catalog_action_id != decision.selected_action_id
        or binding.catalog_action_id != expected_catalog_action
        or binding.materialized_action_id != event.action.action_id
        or context.kind != event.action.kind
        or context.candidate_id != event.action.candidate_id
        or decision.selected_immediate_cost != immediate
    ):
        raise ValueError("Gate 3 applied policy context differs from its decision/event")
    semantic = {
        "schema_version": "yieldforge.m11-gate3-applied-context.v1",
        "decision_id": decision.decision_id,
        "decision_content_sha256": decision.content_sha256,
        "applied_event_id": event.event_id,
        "catalog_action_id": binding.catalog_action_id,
        "materialized_action_id": binding.materialized_action_id,
        "action_kind": context.kind.value,
        "candidate_id": context.candidate_id,
        "selected_stock_id": context.selected_stock_id,
        "candidate_width": context.candidate_width,
        "immediate_net_cost": immediate,
        "selected_remnant_age_hours": context.selected_remnant_age_hours,
        "returned_regularity": context.returned_regularity,
        "known_order_lookahead_term": context.known_order_lookahead_term,
    }
    digest = semantic_sha256(semantic)
    return Gate3AppliedActionContext(
        context_id=f"yfm11g3ctx-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


class Gate3CalibrationM9Transition(FrozenExperimentModel):
    """One executed M9-selected transition used to reconstruct a calibration ledger."""

    schema_version: Literal["yieldforge.m11-gate3-calibration-m9-transition.v1"] = (
        "yieldforge.m11-gate3-calibration-m9-transition.v1"
    )
    transition_id: StrictStr = Field(pattern=r"^yfm11g3m9tr-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    event_position: StrictInt = Field(ge=0, le=23)
    event_id: StrictStr = Field(min_length=1)
    decision_id: StrictStr = Field(pattern=r"^yfm11g3dec-[0-9a-f]{24}$")
    decision_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selected_action_id: StrictStr = Field(min_length=1)
    action_kind: Literal["open_standard_sheet", "consume_remnant"]
    applied_event_id: StrictStr = Field(pattern=r"^yfm7e-[0-9a-f]{24}$")
    applied_event_semantic_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    applied_event: M7ReplayEvent
    applied_context: Gate3AppliedActionContext
    inventory_before_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    inventory_after_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    delta_costs: Gate3CostLedger

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        event_digest = semantic_sha256(self.applied_event, excluded_fields={"event_id"})
        expected_action_id = _m7_event_catalog_action_id(self.applied_event)
        if (
            self.applied_event_id != self.applied_event.event_id
            or self.applied_event_semantic_sha256 != f"sha256:{event_digest}"
            or self.selected_action_id != expected_action_id
            or self.action_kind != self.applied_event.action.kind.value
            or self.applied_context.decision_id != self.decision_id
            or self.applied_context.decision_content_sha256 != self.decision_content_sha256
            or self.applied_context.applied_event_id != self.applied_event_id
            or self.applied_context.catalog_action_id != self.selected_action_id
            or self.applied_context.materialized_action_id != self.applied_event.action.action_id
            or self.inventory_before_sha256
            != gate3_inventory_sha256(self.applied_event.inventory_before)
            or self.inventory_after_sha256
            != gate3_inventory_sha256(self.applied_event.inventory_after)
            or self.delta_costs != _ledger_from_m7_ledger(self.applied_event.delta_costs)
        ):
            raise ValueError("Gate 3 M9 transition differs from its applied M7 event")
        digest = semantic_sha256(self, excluded_fields={"transition_id", "content_sha256"})
        if self.transition_id != f"yfm11g3m9tr-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 M9 transition identity differs from semantic content")
        return self


def build_gate3_calibration_m9_transition(
    *,
    decision: Gate3DecisionTrace,
    step: M7StepResult,
) -> Gate3CalibrationM9Transition:
    applied_event = step.event
    event_digest = semantic_sha256(applied_event, excluded_fields={"event_id"})
    action_kind = applied_event.action.kind.value
    delta_costs = _ledger_from_m7_ledger(applied_event.delta_costs)
    applied_context = build_gate3_applied_action_context(decision=decision, step=step)
    semantic = {
        "schema_version": "yieldforge.m11-gate3-calibration-m9-transition.v1",
        "event_position": decision.event_position,
        "event_id": decision.event_id,
        "decision_id": decision.decision_id,
        "decision_content_sha256": decision.content_sha256,
        "selected_action_id": decision.selected_action_id,
        "action_kind": action_kind,
        "applied_event_id": applied_event.event_id,
        "applied_event_semantic_sha256": f"sha256:{event_digest}",
        "applied_event": applied_event.model_dump(mode="json"),
        "applied_context": applied_context.model_dump(mode="json"),
        "inventory_before_sha256": gate3_inventory_sha256(applied_event.inventory_before),
        "inventory_after_sha256": gate3_inventory_sha256(applied_event.inventory_after),
        "delta_costs": delta_costs.model_dump(mode="json"),
    }
    digest = semantic_sha256(semantic)
    return Gate3CalibrationM9Transition(
        transition_id=f"yfm11g3m9tr-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        event_position=decision.event_position,
        event_id=decision.event_id,
        decision_id=decision.decision_id,
        decision_content_sha256=decision.content_sha256,
        selected_action_id=decision.selected_action_id,
        action_kind=action_kind,
        applied_event_id=applied_event.event_id,
        applied_event_semantic_sha256=f"sha256:{event_digest}",
        applied_event=applied_event,
        applied_context=applied_context,
        inventory_before_sha256=gate3_inventory_sha256(applied_event.inventory_before),
        inventory_after_sha256=gate3_inventory_sha256(applied_event.inventory_after),
        delta_costs=delta_costs,
    )


class Gate3CalibrationMaterialReplay(FrozenExperimentModel):
    """Authenticated execution evidence for one calibration material substream."""

    schema_version: Literal["yieldforge.m11-gate3-calibration-material.v1"] = (
        "yieldforge.m11-gate3-calibration-material.v1"
    )
    material_replay_id: StrictStr = Field(pattern=r"^yfm11g3calmat-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1)
    policy_id: Gate3BaselinePolicyId
    material_key: StrictStr = Field(min_length=1)
    source_registration_id: StrictStr = Field(min_length=1)
    source_registration_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_stream_id: StrictStr = Field(min_length=1)
    source_stream_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_event_positions: tuple[StrictInt, ...] = Field(min_length=1)
    projection_attestation_id: StrictStr = Field(pattern=r"^yfm11m7a-[0-9a-f]{24}$")
    projection_attestation_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    projection_attestation: M11M7ProjectionAttestation
    replay_input_id: StrictStr = Field(pattern=r"^yfm7ri-[0-9a-f]{24}$")
    replay_input_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    replay_input: M7ReplayInput
    m7_runtime_semantic_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_kind: Literal["m7_replay", "m9_two_ply_known_only"]
    result_evidence_id: StrictStr = Field(min_length=1)
    result_evidence_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m7_replay_result: M7ReplayResult | None = None
    m7_applied_contexts: tuple[Gate3AppliedActionContext, ...] = ()
    shard_trace: Gate3ShardTrace
    m9_transitions: tuple[Gate3CalibrationM9Transition, ...] = ()
    m9_terminal: ReplayTerminalRecord | None = None
    final_costs: Gate3CostLedger
    full_sheet_opening_count: StrictInt = Field(ge=0)
    source_revalidated: Literal[True] = True

    @model_validator(mode="after")
    def require_authenticated_execution_and_identity(self) -> Self:
        attestation = self.projection_attestation
        replay_input = self.replay_input
        source_map = attestation.source_event_map
        expected_positions = tuple(item.source_event_position for item in source_map)
        expected_policy = (
            policy_identity(M7PolicyName.AGE_REGULARITY)
            if self.policy_id == "known_only_m9_two_ply_scrap"
            else policy_identity(M7PolicyName(self.policy_id))
        )
        if (
            attestation.attestation_id != self.projection_attestation_id
            or attestation.content_sha256 != self.projection_attestation_content_sha256
            or attestation.gate1_result_id != self.roots.gate1_evaluation_result_id
            or attestation.gate1_result_content_sha256
            != self.roots.gate1_evaluation_result_content_sha256
            or attestation.gate2_result_id != self.roots.gate2_evaluation_result_id
            or attestation.gate2_result_content_sha256
            != self.roots.gate2_evaluation_result_content_sha256
            or attestation.gate3_config_id != self.roots.gate3_config_id
            or attestation.gate3_config_content_sha256 != self.roots.gate3_config_content_sha256
            or attestation.population_id != self.roots.population_id
            or attestation.population_content_sha256 != self.roots.population_content_sha256
            or attestation.registration_kind != "calibration"
            or attestation.control_kind is not None
            or attestation.corpus_id != self.corpus_id
            or attestation.economic_arm != "central"
            or attestation.material_key != self.material_key
            or attestation.source_registration_id != self.source_registration_id
            or attestation.source_registration_content_sha256
            != self.source_registration_content_sha256
            or attestation.source_stream_id != self.source_stream_id
            or attestation.source_stream_content_sha256 != self.source_stream_content_sha256
            or self.source_stream_id != self.stream_id
            or self.source_event_positions != expected_positions
            or expected_positions != tuple(sorted(set(expected_positions)))
            or attestation.policy != expected_policy
            or replay_input.policy != expected_policy
            or replay_input.input_id != self.replay_input_id
            or replay_input.content_sha256 != self.replay_input_content_sha256
            or attestation.m7_replay_input_id != self.replay_input_id
            or attestation.m7_replay_input_content_sha256 != self.replay_input_content_sha256
            or attestation.m7_runtime_semantic_sha256 != self.m7_runtime_semantic_sha256
            or replay_input.rates != attestation.rates
            or replay_input.collision_backend != attestation.collision_backend
            or len(replay_input.instances) != len(source_map)
            or tuple(item.event_id for item in replay_input.instances)
            != tuple(item.compatibility_event_id for item in source_map)
        ):
            raise ValueError("Gate 3 calibration material differs from adapter/source roots")
        decisions = self.shard_trace.decisions
        if (
            self.shard_trace.root_binding_id != self.roots.binding_id
            or self.shard_trace.root_binding_content_sha256 != self.roots.content_sha256
            or self.shard_trace.stream_id != self.stream_id
            or self.shard_trace.corpus_id != self.corpus_id
            or self.shard_trace.material_key != self.material_key
            or self.shard_trace.policy_id != self.policy_id
            or tuple(item.event_position for item in decisions) != expected_positions
            or tuple(item.event_id for item in decisions)
            != tuple(item.source_event_id for item in source_map)
            or self.shard_trace.projection_binding_sha256 != attestation.content_sha256
        ):
            raise ValueError("Gate 3 calibration shard differs from its adapter attestation")
        if self.execution_kind == "m7_replay":
            result = self.m7_replay_result
            if (
                self.policy_id == "known_only_m9_two_ply_scrap"
                or result is None
                or self.m9_transitions
                or self.m9_terminal is not None
                or len(self.m7_applied_contexts) != len(decisions)
                or self.shard_trace.arm != "B"
                or self.shard_trace.visibility != "released_only"
                or any(item.algorithm != "m7_policy" for item in decisions)
                or result.result_id != self.result_evidence_id
                or result.content_sha256 != self.result_evidence_content_sha256
                or result.input_id != self.replay_input_id
                or result.input_sha256 != self.replay_input_content_sha256
                or result.policy != expected_policy
                or len(result.events) != len(source_map)
                or result.summary.instance_count != len(source_map)
                or result.summary.fulfilled_instance_count != len(source_map)
                or result.summary.technical_decision != "pass"
                or tuple(item.binding_id for item in result.events)
                != tuple(item.binding_id for item in replay_input.instances)
                or tuple(_m7_event_catalog_action_id(item) for item in result.events)
                != tuple(item.selected_action_id for item in decisions)
                or any(item.selected_action_id != item.baseline_action_id for item in decisions)
            ):
                raise ValueError("Gate 3 calibration M7 result binding differs")
            for decision, event, context in zip(
                decisions,
                result.events,
                self.m7_applied_contexts,
                strict=True,
            ):
                if (
                    context.decision_id != decision.decision_id
                    or context.decision_content_sha256 != decision.content_sha256
                    or context.applied_event_id != event.event_id
                    or context.catalog_action_id != decision.selected_action_id
                    or context.materialized_action_id != event.action.action_id
                    or context.action_kind != event.action.kind.value
                    or context.candidate_id != event.action.candidate_id
                    or decision.inventory_before_sha256
                    != gate3_inventory_sha256(event.inventory_before)
                    or decision.inventory_after_sha256
                    != gate3_inventory_sha256(event.inventory_after)
                    or decision.selected_immediate_cost != context.immediate_net_cost
                    or decision.baseline_immediate_cost != context.immediate_net_cost
                ):
                    raise ValueError("Gate 3 calibration M7 policy context binding differs")
            derived_costs = _ledger_from_m7_result(result)
            openings = result.summary.full_sheet_opening_count
        else:
            if (
                self.policy_id != "known_only_m9_two_ply_scrap"
                or self.m7_replay_result is not None
                or self.m7_applied_contexts
                or self.shard_trace.arm != "B"
                or self.shard_trace.visibility != "known_only"
                or any(item.algorithm != "m9_two_ply" for item in decisions)
                or self.m9_terminal is None
                or len(self.m9_transitions) != len(decisions)
                or self.result_evidence_id != self.shard_trace.trace_id
                or self.result_evidence_content_sha256 != self.shard_trace.content_sha256
            ):
                raise ValueError("Gate 3 calibration M9 result binding differs")
            for decision, transition in zip(decisions, self.m9_transitions, strict=True):
                if (
                    transition.event_position != decision.event_position
                    or transition.event_id != decision.event_id
                    or transition.decision_id != decision.decision_id
                    or transition.decision_content_sha256 != decision.content_sha256
                    or transition.selected_action_id != decision.selected_action_id
                    or transition.inventory_before_sha256 != decision.inventory_before_sha256
                    or transition.inventory_after_sha256 != decision.inventory_after_sha256
                ):
                    raise ValueError("Gate 3 calibration M9 transition differs from its decision")
            for previous, current in zip(
                self.m9_transitions,
                self.m9_transitions[1:],
                strict=False,
            ):
                if previous.inventory_after_sha256 != current.inventory_before_sha256:
                    raise ValueError("Gate 3 calibration M9 inventory continuity differs")
            if (
                self.m9_terminal.inventory_before_liquidation
                != self.m9_transitions[-1].applied_event.inventory_after
                or self.m9_terminal.storage_interval_start
                != self.m9_transitions[-1].applied_event.occurred_at
            ):
                raise ValueError("Gate 3 calibration M9 terminal differs from final transition")
            derived_costs = _sum_ledgers(
                tuple(item.delta_costs for item in self.m9_transitions)
                + (_ledger_from_m7_ledger(self.m9_terminal.delta_costs),)
            )
            if derived_costs != _ledger_from_m7_ledger(self.m9_terminal.cumulative_costs):
                raise ValueError("Gate 3 calibration M9 terminal cumulative ledger differs")
            openings = sum(
                item.action_kind == "open_standard_sheet" for item in self.m9_transitions
            )
        if (
            self.final_costs != derived_costs
            or self.shard_trace.final_costs != derived_costs
            or self.full_sheet_opening_count != openings
        ):
            raise ValueError("Gate 3 calibration costs/openings differ from executed replay")
        digest = semantic_sha256(
            self,
            excluded_fields={"material_replay_id", "content_sha256"},
        )
        if self.material_replay_id != f"yfm11g3calmat-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 calibration material identity differs from semantic content")
        return self


def build_gate3_calibration_material_replay(
    *,
    roots: Gate3RootBinding,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    policy_id: Gate3BaselinePolicyId,
    projection_attestation: M11M7ProjectionAttestation,
    replay_input: M7ReplayInput,
    shard_trace: Gate3ShardTrace,
    m7_replay_result: M7ReplayResult | None = None,
    m7_applied_contexts: tuple[Gate3AppliedActionContext, ...] = (),
    m9_transitions: tuple[Gate3CalibrationM9Transition, ...] = (),
    m9_terminal: ReplayTerminalRecord | None = None,
) -> Gate3CalibrationMaterialReplay:
    is_m9 = policy_id == "known_only_m9_two_ply_scrap"
    if is_m9:
        if m9_terminal is None:
            raise ValueError("Gate 3 M9 calibration replay requires terminal costs")
        costs = _sum_ledgers(
            tuple(item.delta_costs for item in m9_transitions)
            + (_ledger_from_m7_ledger(m9_terminal.delta_costs),)
        )
        openings = sum(item.action_kind == "open_standard_sheet" for item in m9_transitions)
        result_id = shard_trace.trace_id
        result_sha = shard_trace.content_sha256
    else:
        if m7_replay_result is None:
            raise ValueError("Gate 3 M7 calibration replay requires an M7 result")
        costs = _ledger_from_m7_result(m7_replay_result)
        openings = m7_replay_result.summary.full_sheet_opening_count
        result_id = m7_replay_result.result_id
        result_sha = m7_replay_result.content_sha256
    attestation = projection_attestation
    semantic = {
        "schema_version": "yieldforge.m11-gate3-calibration-material.v1",
        "roots": roots.model_dump(mode="json"),
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "policy_id": policy_id,
        "material_key": attestation.material_key,
        "source_registration_id": attestation.source_registration_id,
        "source_registration_content_sha256": attestation.source_registration_content_sha256,
        "source_stream_id": attestation.source_stream_id,
        "source_stream_content_sha256": attestation.source_stream_content_sha256,
        "source_event_positions": tuple(
            item.source_event_position for item in attestation.source_event_map
        ),
        "projection_attestation_id": attestation.attestation_id,
        "projection_attestation_content_sha256": attestation.content_sha256,
        "projection_attestation": attestation.model_dump(mode="json"),
        "replay_input_id": replay_input.input_id,
        "replay_input_content_sha256": replay_input.content_sha256,
        "replay_input": replay_input.model_dump(mode="json"),
        "m7_runtime_semantic_sha256": attestation.m7_runtime_semantic_sha256,
        "execution_kind": "m9_two_ply_known_only" if is_m9 else "m7_replay",
        "result_evidence_id": result_id,
        "result_evidence_content_sha256": result_sha,
        "m7_replay_result": (
            m7_replay_result.model_dump(mode="json") if m7_replay_result is not None else None
        ),
        "m7_applied_contexts": [item.model_dump(mode="json") for item in m7_applied_contexts],
        "shard_trace": shard_trace.model_dump(mode="json"),
        "m9_transitions": [item.model_dump(mode="json") for item in m9_transitions],
        "m9_terminal": m9_terminal.model_dump(mode="json") if m9_terminal is not None else None,
        "final_costs": costs.model_dump(mode="json"),
        "full_sheet_opening_count": openings,
        "source_revalidated": True,
    }
    digest = semantic_sha256(semantic)
    return Gate3CalibrationMaterialReplay(
        material_replay_id=f"yfm11g3calmat-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        corpus_id=corpus_id,
        stream_id=stream_id,
        policy_id=policy_id,
        material_key=attestation.material_key,
        source_registration_id=attestation.source_registration_id,
        source_registration_content_sha256=attestation.source_registration_content_sha256,
        source_stream_id=attestation.source_stream_id,
        source_stream_content_sha256=attestation.source_stream_content_sha256,
        source_event_positions=tuple(
            item.source_event_position for item in attestation.source_event_map
        ),
        projection_attestation_id=attestation.attestation_id,
        projection_attestation_content_sha256=attestation.content_sha256,
        projection_attestation=attestation,
        replay_input_id=replay_input.input_id,
        replay_input_content_sha256=replay_input.content_sha256,
        replay_input=replay_input,
        m7_runtime_semantic_sha256=attestation.m7_runtime_semantic_sha256,
        execution_kind="m9_two_ply_known_only" if is_m9 else "m7_replay",
        result_evidence_id=result_id,
        result_evidence_content_sha256=result_sha,
        m7_replay_result=m7_replay_result,
        m7_applied_contexts=m7_applied_contexts,
        shard_trace=shard_trace,
        m9_transitions=m9_transitions,
        m9_terminal=m9_terminal,
        final_costs=costs,
        full_sheet_opening_count=openings,
    )


class Gate3CalibrationObservation(FrozenExperimentModel):
    """One source-complete calibration stream reconstructed from material replays."""

    schema_version: Literal["yieldforge.m11-gate3-calibration-observation.v1"] = (
        "yieldforge.m11-gate3-calibration-observation.v1"
    )
    observation_id: StrictStr = Field(pattern=r"^yfm11g3calobs-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1)
    policy_id: Gate3BaselinePolicyId
    material_replays: tuple[Gate3CalibrationMaterialReplay, ...] = Field(min_length=1)
    final_costs: Gate3CostLedger
    full_sheet_opening_count: StrictInt = Field(ge=0)
    exact_event_census: Literal[True] = True
    calibration_only: Literal[True] = True
    confirmation_inputs_used: Literal[False] = False

    @model_validator(mode="after")
    def require_exact_partition_addition_and_identity(self) -> Self:
        if self.material_replays != tuple(
            sorted(
                self.material_replays,
                key=lambda item: (item.material_key, item.material_replay_id),
            )
        ):
            raise ValueError("Gate 3 calibration materials must use canonical order")
        if any(
            item.roots != self.roots
            or item.corpus_id != self.corpus_id
            or item.stream_id != self.stream_id
            or item.policy_id != self.policy_id
            for item in self.material_replays
        ):
            raise ValueError("Gate 3 calibration material binding differs")
        positions = tuple(
            sorted(
                position
                for item in self.material_replays
                for position in item.source_event_positions
            )
        )
        if positions != tuple(range(24)):
            raise ValueError("Gate 3 calibration materials must partition all 24 events")
        if self.final_costs != _sum_ledgers(
            tuple(item.final_costs for item in self.material_replays)
        ) or self.full_sheet_opening_count != sum(
            item.full_sheet_opening_count for item in self.material_replays
        ):
            raise ValueError("Gate 3 calibration material ledger/opening addition differs")
        digest = semantic_sha256(self, excluded_fields={"observation_id", "content_sha256"})
        if self.observation_id != f"yfm11g3calobs-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 calibration observation identity differs from content")
        return self


def build_gate3_calibration_observation(
    *,
    roots: Gate3RootBinding,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    policy_id: Gate3BaselinePolicyId,
    material_replays: tuple[Gate3CalibrationMaterialReplay, ...],
) -> Gate3CalibrationObservation:
    canonical = tuple(
        sorted(material_replays, key=lambda item: (item.material_key, item.material_replay_id))
    )
    if not canonical:
        raise ValueError("Gate 3 calibration observation requires material replay evidence")
    costs = _sum_ledgers(tuple(item.final_costs for item in canonical))
    openings = sum(item.full_sheet_opening_count for item in canonical)
    semantic = {
        "schema_version": "yieldforge.m11-gate3-calibration-observation.v1",
        "roots": roots.model_dump(mode="json"),
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "policy_id": policy_id,
        "material_replays": [item.model_dump(mode="json") for item in canonical],
        "final_costs": costs.model_dump(mode="json"),
        "full_sheet_opening_count": openings,
        "exact_event_census": True,
        "calibration_only": True,
        "confirmation_inputs_used": False,
    }
    digest = semantic_sha256(semantic)
    return Gate3CalibrationObservation(
        observation_id=f"yfm11g3calobs-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        corpus_id=corpus_id,
        stream_id=stream_id,
        policy_id=policy_id,
        material_replays=canonical,
        final_costs=costs,
        full_sheet_opening_count=openings,
    )


class Gate3CalibrationAttempt(FrozenExperimentModel):
    """One content-addressed calibration attempt, including preserved failures."""

    schema_version: Literal["yieldforge.m11-gate3-calibration-attempt.v1"] = (
        "yieldforge.m11-gate3-calibration-attempt.v1"
    )
    attempt_id: StrictStr = Field(pattern=r"^yfm11g3calatt-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    execution_position: StrictInt = Field(ge=0, le=95)
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1)
    policy_id: Gate3BaselinePolicyId
    status: Literal["success", "failure"]
    observation: Gate3CalibrationObservation | None = None
    failure_type: StrictStr | None = None
    failure_detail: StrictStr | None = None

    @model_validator(mode="after")
    def require_exactly_one_outcome_and_identity(self) -> Self:
        if self.status == "success":
            if (
                self.observation is None
                or self.failure_type is not None
                or self.failure_detail is not None
                or self.observation.roots != self.roots
                or self.observation.corpus_id != self.corpus_id
                or self.observation.stream_id != self.stream_id
                or self.observation.policy_id != self.policy_id
            ):
                raise ValueError("successful Gate 3 calibration attempt lacks one raw outcome")
        elif self.observation is not None or not self.failure_type or not self.failure_detail:
            raise ValueError("failed Gate 3 calibration attempt lacks one preserved failure")
        digest = semantic_sha256(self, excluded_fields={"attempt_id", "content_sha256"})
        if self.attempt_id != f"yfm11g3calatt-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 calibration attempt identity differs from semantic content")
        return self


class Gate3CentralAttempt(FrozenExperimentModel):
    """One content-addressed held-out stream attempt, including preserved failures."""

    schema_version: Literal["yieldforge.m11-gate3-central-attempt.v1"] = (
        "yieldforge.m11-gate3-central-attempt.v1"
    )
    attempt_id: StrictStr = Field(pattern=r"^yfm11g3cenatt-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    execution_position: StrictInt = Field(ge=0, le=39)
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1)
    status: Literal["success", "failure"]
    cell: Gate3StreamCell | None = None
    failure_type: StrictStr | None = None
    failure_detail: StrictStr | None = None

    @model_validator(mode="after")
    def require_exactly_one_outcome_and_identity(self) -> Self:
        if self.status == "success":
            if (
                self.cell is None
                or self.failure_type is not None
                or self.failure_detail is not None
                or self.cell.roots != self.roots
                or self.cell.corpus_id != self.corpus_id
                or self.cell.stream_id != self.stream_id
            ):
                raise ValueError("successful Gate 3 central attempt lacks one bound raw cell")
        elif self.cell is not None or not self.failure_type or not self.failure_detail:
            raise ValueError("failed Gate 3 central attempt lacks one preserved failure")
        digest = semantic_sha256(self, excluded_fields={"attempt_id", "content_sha256"})
        if self.attempt_id != f"yfm11g3cenatt-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 central attempt identity differs from semantic content")
        return self


def _calibration_stream_census(
    attempts: tuple[Gate3CalibrationAttempt, ...],
) -> tuple[
    tuple[Literal["lectra-m3-m4"], tuple[str, ...]],
    tuple[Literal["loco-2dics"], tuple[str, ...]],
]:
    if len(attempts) != 96:
        raise ValueError("Gate 3 calibration census requires exactly 96 attempts")
    return (
        ("lectra-m3-m4", tuple(item.stream_id for item in attempts[:8])),
        ("loco-2dics", tuple(item.stream_id for item in attempts[48:56])),
    )


def _rederive_calibration_freezes(
    *,
    roots: Gate3RootBinding,
    attempts: tuple[Gate3CalibrationAttempt, ...],
    stream_census: tuple[tuple[Gate3CorpusId, tuple[str, ...]], ...],
) -> tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze]:
    freezes: list[Gate3BaselineCalibrationFreeze] = []
    for corpus_id, stream_ids in stream_census:
        per_corpus = tuple(item for item in attempts if item.corpus_id == corpus_id)
        costs = {
            policy_id: tuple(
                item.observation.final_costs.net_cost
                for item in per_corpus
                if item.policy_id == policy_id and item.observation is not None
            )
            for policy_id in GATE3_BASELINE_POLICY_IDS
        }
        openings = {
            policy_id: tuple(
                item.observation.full_sheet_opening_count
                for item in per_corpus
                if item.policy_id == policy_id and item.observation is not None
            )
            for policy_id in GATE3_BASELINE_POLICY_IDS
        }
        freezes.append(
            select_gate3_baseline_policy(
                roots=roots,
                corpus_id=corpus_id,
                calibration_stream_ids=stream_ids,
                policy_stream_costs=costs,
                policy_stream_sheet_openings=openings,
                policy_invalid_stream_counts={
                    policy_id: 0 for policy_id in GATE3_BASELINE_POLICY_IDS
                },
            )
        )
    if len(freezes) != 2:
        raise ValueError("Gate 3 calibration freeze reconstruction requires both corpora")
    return freezes[0], freezes[1]


class Gate3StageFailure(FrozenExperimentModel):
    stage: Literal["validity_controls"]
    failure_type: StrictStr = Field(min_length=1)
    failure_detail: StrictStr = Field(min_length=1)


Gate3EarlyStatus = Literal[
    "invalid_test",
    "diagnosis_required",
    "insufficient_headroom",
    "central_survived",
]
Gate3EarlyDisposition = Literal[
    "INVALID_NONZERO",
    "PAUSE_DIAGNOSIS",
    "ABANDON",
    "CONTINUE_GATE3",
]

_GATE3_DOWNSTREAM_STAGES: tuple[str, ...] = (
    "fixed_adverse",
    "zero_terminal_credit",
    "conservative_eligibility",
    "expanded_catalog_diagnostic",
    "permissive_eligibility_diagnostic",
    "support_metrics",
    "deployable_capture",
)


class Gate3EarlyConfirmationResult(FrozenExperimentModel):
    """Outcome-blind Gate 3 early path, terminal only on validity or central failure."""

    schema_version: Literal["yieldforge.m11-gate3-early-confirmation.v1"] = (
        "yieldforge.m11-gate3-early-confirmation.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm11g3early-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    calibration_attempts: tuple[Gate3CalibrationAttempt, ...] = Field(
        min_length=96,
        max_length=96,
    )
    calibration_stream_census: tuple[
        tuple[Gate3CorpusId, tuple[StrictStr, ...]],
        tuple[Gate3CorpusId, tuple[StrictStr, ...]],
    ]
    baseline_freezes: tuple[Gate3BaselineCalibrationFreeze, ...] = Field(max_length=2)
    validity_receipt: Gate3ValidityReceipt | None
    stage_failures: tuple[Gate3StageFailure, ...]
    central_attempts: tuple[Gate3CentralAttempt, ...] = Field(max_length=40)
    corpus_summaries: tuple[Gate3CentralGroupSummary, ...] = Field(max_length=2)
    central_statistics: Gate3CentralStatistics | None
    bootstrap_draw_count: Literal[0, 10000]
    status: Gate3EarlyStatus
    disposition: Gate3EarlyDisposition
    terminal: StrictBool
    skipped_stages: tuple[tuple[StrictStr, Literal["skipped_terminal_prerequisite"]], ...]
    continuation_stages: tuple[StrictStr, ...]
    confirmation_inputs_used_for_calibration: Literal[False] = False
    downstream_outcomes_present: Literal[False] = False

    @model_validator(mode="after")
    def require_raw_recomputation_and_identity(self) -> Self:
        if tuple(item.execution_position for item in self.calibration_attempts) != tuple(range(96)):
            raise ValueError("Gate 3 calibration attempt order differs from the frozen census")
        if any(item.roots != self.roots for item in self.calibration_attempts):
            raise ValueError("Gate 3 calibration attempt roots differ")
        expected_census = _calibration_stream_census(self.calibration_attempts)
        if (
            self.calibration_stream_census != expected_census
            or tuple(item[0] for item in self.calibration_stream_census)
            != ("lectra-m3-m4", "loco-2dics")
            or any(
                len(stream_ids) != 8 or len(set(stream_ids)) != 8
                for _, stream_ids in self.calibration_stream_census
            )
        ):
            raise ValueError("Gate 3 calibration stream census differs from the frozen order")
        expected_attempt_order = tuple(
            (corpus_id, stream_id, policy_id)
            for corpus_id, stream_ids in self.calibration_stream_census
            for policy_id in GATE3_BASELINE_POLICY_IDS
            for stream_id in stream_ids
        )
        if (
            tuple(
                (item.corpus_id, item.stream_id, item.policy_id)
                for item in self.calibration_attempts
            )
            != expected_attempt_order
        ):
            raise ValueError("Gate 3 calibration corpus/policy/stream order differs")
        calibration_failed = any(item.status == "failure" for item in self.calibration_attempts)
        if calibration_failed:
            if self.baseline_freezes:
                raise ValueError("Gate 3 failed calibration cannot publish baseline freezes")
        elif self.baseline_freezes != _rederive_calibration_freezes(
            roots=self.roots,
            attempts=self.calibration_attempts,
            stream_census=self.calibration_stream_census,
        ):
            raise ValueError("Gate 3 baseline freezes differ from raw calibration replays")
        if tuple(item.execution_position for item in self.central_attempts) != tuple(
            range(len(self.central_attempts))
        ) or any(item.roots != self.roots for item in self.central_attempts):
            raise ValueError("Gate 3 central attempt order or roots differ")
        if self.validity_receipt is not None and self.validity_receipt.roots != self.roots:
            raise ValueError("Gate 3 validity receipt roots differ")
        if any(item.roots != self.roots for item in self.baseline_freezes):
            raise ValueError("Gate 3 baseline freeze roots differ")
        if self.validity_receipt is not None and self.baseline_freezes:
            freeze_by_corpus = {item.corpus_id: item for item in self.baseline_freezes}
            if any(
                item.baseline_freeze != freeze_by_corpus[item.corpus_id]
                for item in self.validity_receipt.hard_nulls
            ) or any(
                item.baseline_freeze != freeze_by_corpus[item.corpus_id]
                for item in self.validity_receipt.exact_audits
            ):
                raise ValueError("Gate 3 validity controls differ from calibration freezes")
        successful = tuple(item.cell for item in self.central_attempts if item.cell is not None)
        if self.central_statistics is not None:
            if len(successful) != 40:
                raise ValueError("Gate 3 statistics require all forty raw cells")
            lectra = tuple(item for item in successful if item.corpus_id == "lectra-m3-m4")
            loco = tuple(item for item in successful if item.corpus_id == "loco-2dics")
            lectra_ids = tuple(
                item.stream_id for item in self.central_attempts if item.corpus_id == "lectra-m3-m4"
            )
            loco_ids = tuple(
                item.stream_id for item in self.central_attempts if item.corpus_id == "loco-2dics"
            )
            if self.central_statistics != calculate_gate3_central_statistics(
                lectra + loco,
                lectra_stream_ids=lectra_ids,
                loco_stream_ids=loco_ids,
            ):
                raise ValueError("Gate 3 central statistics differ from raw B/F/K cells")
        elif self.bootstrap_draw_count == 10000 and not self.corpus_summaries:
            raise ValueError("Gate 3 bootstrap count lacks a raw-cell summary")
        if self.bootstrap_draw_count != (10000 if self.corpus_summaries else 0):
            raise ValueError("Gate 3 bootstrap draw count differs from executed raw inference")
        expected_summary_groups: tuple[str, ...] = tuple(
            "loco-2dics" if index == 0 else "lectra-m3-m4"
            for index in range(len(self.corpus_summaries))
        )
        if tuple(item.group for item in self.corpus_summaries) != expected_summary_groups:
            raise ValueError("Gate 3 corpus summaries differ from LOCo-first execution")
        for summary in self.corpus_summaries:
            cells = tuple(
                item.cell
                for item in self.central_attempts
                if item.cell is not None and item.corpus_id == summary.group
            )
            canonical_ids = tuple(
                item.stream_id for item in self.central_attempts if item.corpus_id == summary.group
            )
            if summary != calculate_gate3_corpus_central_summary(
                cells,
                canonical_stream_ids=canonical_ids,
            ):
                raise ValueError("Gate 3 corpus summary differs from raw B/F/K cells")
        expected = _classify_gate3_early_fields(
            calibration_attempts=self.calibration_attempts,
            baseline_freezes=self.baseline_freezes,
            validity_receipt=self.validity_receipt,
            stage_failures=self.stage_failures,
            central_attempts=self.central_attempts,
            corpus_summaries=self.corpus_summaries,
            central_statistics=self.central_statistics,
        )
        if (
            self.status,
            self.disposition,
            self.terminal,
            self.skipped_stages,
            self.continuation_stages,
        ) != expected:
            raise ValueError("Gate 3 early branch differs from raw attempts and controls")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.result_id != f"yfm11g3early-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 early result identity differs from semantic content")
        return self


class Gate3ConfirmationBackend(Protocol):
    """Narrow injectable execution seam; it cannot supply aggregate decisions."""

    def calibration_stream_ids(self, corpus_id: Gate3CorpusId) -> tuple[str, ...]: ...

    def confirmation_stream_ids(self, corpus_id: Gate3CorpusId) -> tuple[str, ...]: ...

    def execute_calibration_stream(
        self,
        *,
        corpus_id: Gate3CorpusId,
        stream_id: str,
        policy_id: Gate3BaselinePolicyId,
    ) -> Gate3CalibrationObservation: ...

    def execute_validity_controls(
        self,
        *,
        roots: Gate3RootBinding,
        baseline_freezes: tuple[
            Gate3BaselineCalibrationFreeze,
            Gate3BaselineCalibrationFreeze,
        ],
    ) -> Gate3ValidityReceipt: ...

    def execute_central_stream(
        self,
        *,
        roots: Gate3RootBinding,
        corpus_id: Gate3CorpusId,
        stream_id: str,
        baseline_freeze: Gate3BaselineCalibrationFreeze,
    ) -> Gate3StreamCell: ...


def _failure_fields(error: Exception) -> tuple[str, str]:
    failure_type = f"{type(error).__module__}.{type(error).__qualname__}"[:240]
    detail = (str(error).strip() or "exception carried no detail")[:1000]
    return failure_type, detail


def _build_calibration_attempt(
    *,
    roots: Gate3RootBinding,
    execution_position: int,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    policy_id: Gate3BaselinePolicyId,
    outcome: Gate3CalibrationObservation | None,
    error: Exception | None,
) -> Gate3CalibrationAttempt:
    failure_type, failure_detail = _failure_fields(error) if error is not None else (None, None)
    semantic = {
        "schema_version": "yieldforge.m11-gate3-calibration-attempt.v1",
        "roots": roots.model_dump(mode="json"),
        "execution_position": execution_position,
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "policy_id": policy_id,
        "status": "success" if outcome is not None else "failure",
        "observation": outcome.model_dump(mode="json") if outcome is not None else None,
        "failure_type": failure_type,
        "failure_detail": failure_detail,
    }
    digest = semantic_sha256(semantic)
    return Gate3CalibrationAttempt(
        attempt_id=f"yfm11g3calatt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        execution_position=execution_position,
        corpus_id=corpus_id,
        stream_id=stream_id,
        policy_id=policy_id,
        status="success" if outcome is not None else "failure",
        observation=outcome,
        failure_type=failure_type,
        failure_detail=failure_detail,
    )


def _build_central_attempt(
    *,
    roots: Gate3RootBinding,
    execution_position: int,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    cell: Gate3StreamCell | None,
    error: Exception | None,
) -> Gate3CentralAttempt:
    failure_type, failure_detail = _failure_fields(error) if error is not None else (None, None)
    semantic = {
        "schema_version": "yieldforge.m11-gate3-central-attempt.v1",
        "roots": roots.model_dump(mode="json"),
        "execution_position": execution_position,
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "status": "success" if cell is not None else "failure",
        "cell": cell.model_dump(mode="json") if cell is not None else None,
        "failure_type": failure_type,
        "failure_detail": failure_detail,
    }
    digest = semantic_sha256(semantic)
    return Gate3CentralAttempt(
        attempt_id=f"yfm11g3cenatt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        execution_position=execution_position,
        corpus_id=corpus_id,
        stream_id=stream_id,
        status="success" if cell is not None else "failure",
        cell=cell,
        failure_type=failure_type,
        failure_detail=failure_detail,
    )


def _terminal_skips(*stages: str) -> tuple[tuple[str, str], ...]:
    return tuple((stage, "skipped_terminal_prerequisite") for stage in stages)


def _classify_gate3_early_fields(
    *,
    calibration_attempts: tuple[Gate3CalibrationAttempt, ...],
    baseline_freezes: tuple[Gate3BaselineCalibrationFreeze, ...],
    validity_receipt: Gate3ValidityReceipt | None,
    stage_failures: tuple[Gate3StageFailure, ...],
    central_attempts: tuple[Gate3CentralAttempt, ...],
    corpus_summaries: tuple[Gate3CentralGroupSummary, ...],
    central_statistics: Gate3CentralStatistics | None,
) -> tuple[
    Gate3EarlyStatus,
    Gate3EarlyDisposition,
    bool,
    tuple[tuple[str, str], ...],
    tuple[str, ...],
]:
    central_loco = ("central_loco", "central_lectra", "equal-corpus-pool")
    if any(item.status == "failure" for item in calibration_attempts) or len(baseline_freezes) != 2:
        return (
            "invalid_test",
            "INVALID_NONZERO",
            True,
            _terminal_skips(*central_loco, *_GATE3_DOWNSTREAM_STAGES),
            (),
        )
    if stage_failures or validity_receipt is None or validity_receipt.status == "invalid":
        return (
            "invalid_test",
            "INVALID_NONZERO",
            True,
            _terminal_skips(*central_loco, *_GATE3_DOWNSTREAM_STAGES),
            (),
        )
    if validity_receipt.status == "diagnosis_required":
        return (
            "diagnosis_required",
            "PAUSE_DIAGNOSIS",
            True,
            _terminal_skips(*central_loco, *_GATE3_DOWNSTREAM_STAGES),
            (),
        )
    failures = tuple(item for item in central_attempts if item.status == "failure")
    if failures:
        attempted_corpora = {item.corpus_id for item in central_attempts}
        remaining = (
            ("central_lectra", "equal-corpus-pool")
            if attempted_corpora == {"loco-2dics"}
            else ("equal-corpus-pool",)
        )
        return (
            "invalid_test",
            "INVALID_NONZERO",
            True,
            _terminal_skips(*remaining, *_GATE3_DOWNSTREAM_STAGES),
            (),
        )
    if corpus_summaries and not corpus_summaries[-1].central_green:
        remaining = (
            ("central_lectra", "equal-corpus-pool")
            if len(corpus_summaries) == 1
            else ("equal-corpus-pool",)
        )
        return (
            "insufficient_headroom",
            "ABANDON",
            True,
            _terminal_skips(*remaining, *_GATE3_DOWNSTREAM_STAGES),
            (),
        )
    if central_statistics is not None:
        if not central_statistics.groups[2].central_green:
            return (
                "insufficient_headroom",
                "ABANDON",
                True,
                _terminal_skips(*_GATE3_DOWNSTREAM_STAGES),
                (),
            )
        return (
            "central_survived",
            "CONTINUE_GATE3",
            False,
            (),
            _GATE3_DOWNSTREAM_STAGES,
        )
    raise ValueError("Gate 3 early evidence does not reach a registered branch")


def _build_gate3_early_result(
    *,
    roots: Gate3RootBinding,
    calibration_attempts: tuple[Gate3CalibrationAttempt, ...],
    baseline_freezes: tuple[Gate3BaselineCalibrationFreeze, ...],
    validity_receipt: Gate3ValidityReceipt | None,
    stage_failures: tuple[Gate3StageFailure, ...],
    central_attempts: tuple[Gate3CentralAttempt, ...],
    corpus_summaries: tuple[Gate3CentralGroupSummary, ...],
    central_statistics: Gate3CentralStatistics | None,
) -> Gate3EarlyConfirmationResult:
    status, disposition, terminal, skipped, continuation = _classify_gate3_early_fields(
        calibration_attempts=calibration_attempts,
        baseline_freezes=baseline_freezes,
        validity_receipt=validity_receipt,
        stage_failures=stage_failures,
        central_attempts=central_attempts,
        corpus_summaries=corpus_summaries,
        central_statistics=central_statistics,
    )
    bootstrap_draw_count = 10000 if corpus_summaries else 0
    calibration_stream_census = _calibration_stream_census(calibration_attempts)
    semantic = {
        "schema_version": "yieldforge.m11-gate3-early-confirmation.v1",
        "roots": roots.model_dump(mode="json"),
        "calibration_attempts": [item.model_dump(mode="json") for item in calibration_attempts],
        "calibration_stream_census": calibration_stream_census,
        "baseline_freezes": [item.model_dump(mode="json") for item in baseline_freezes],
        "validity_receipt": (
            validity_receipt.model_dump(mode="json") if validity_receipt is not None else None
        ),
        "stage_failures": [item.model_dump(mode="json") for item in stage_failures],
        "central_attempts": [item.model_dump(mode="json") for item in central_attempts],
        "corpus_summaries": [item.model_dump(mode="json") for item in corpus_summaries],
        "central_statistics": (
            central_statistics.model_dump(mode="json") if central_statistics is not None else None
        ),
        "bootstrap_draw_count": bootstrap_draw_count,
        "status": status,
        "disposition": disposition,
        "terminal": terminal,
        "skipped_stages": skipped,
        "continuation_stages": continuation,
        "confirmation_inputs_used_for_calibration": False,
        "downstream_outcomes_present": False,
    }
    digest = semantic_sha256(semantic)
    return Gate3EarlyConfirmationResult(
        result_id=f"yfm11g3early-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=roots,
        calibration_attempts=calibration_attempts,
        calibration_stream_census=calibration_stream_census,
        baseline_freezes=baseline_freezes,
        validity_receipt=validity_receipt,
        stage_failures=stage_failures,
        central_attempts=central_attempts,
        corpus_summaries=corpus_summaries,
        central_statistics=central_statistics,
        bootstrap_draw_count=bootstrap_draw_count,
        status=status,
        disposition=disposition,
        terminal=terminal,
        skipped_stages=skipped,  # type: ignore[arg-type]
        continuation_stages=continuation,
    )


def run_gate3_early_confirmation(
    *,
    roots: Gate3RootBinding,
    backend: Gate3ConfirmationBackend,
) -> Gate3EarlyConfirmationResult:
    """Run only the frozen calibration, validity, and LOCo-first central path."""

    corpus_order: tuple[Gate3CorpusId, Gate3CorpusId] = (
        "lectra-m3-m4",
        "loco-2dics",
    )
    calibration_ids = {
        corpus_id: backend.calibration_stream_ids(corpus_id) for corpus_id in corpus_order
    }
    if any(len(values) != 8 or len(set(values)) != 8 for values in calibration_ids.values()):
        raise ValueError("Gate 3 backend calibration stream census differs from 8+8")
    calibration_attempts_list: list[Gate3CalibrationAttempt] = []
    position = 0
    for corpus_id in corpus_order:
        for policy_id in GATE3_BASELINE_POLICY_IDS:
            for stream_id in calibration_ids[corpus_id]:
                try:
                    outcome = backend.execute_calibration_stream(
                        corpus_id=corpus_id,
                        stream_id=stream_id,
                        policy_id=policy_id,
                    )
                    strict_outcome = Gate3CalibrationObservation.model_validate(
                        outcome.model_dump(mode="python", round_trip=True),
                        strict=True,
                    )
                    error = None
                except Exception as caught:  # preserve every registered attempt failure
                    strict_outcome = None
                    error = caught
                calibration_attempts_list.append(
                    _build_calibration_attempt(
                        roots=roots,
                        execution_position=position,
                        corpus_id=corpus_id,
                        stream_id=stream_id,
                        policy_id=policy_id,
                        outcome=strict_outcome,
                        error=error,
                    )
                )
                position += 1
    calibration_attempts = tuple(calibration_attempts_list)
    if any(item.status == "failure" for item in calibration_attempts):
        return _build_gate3_early_result(
            roots=roots,
            calibration_attempts=calibration_attempts,
            baseline_freezes=(),
            validity_receipt=None,
            stage_failures=(),
            central_attempts=(),
            corpus_summaries=(),
            central_statistics=None,
        )
    baseline_freezes = _rederive_calibration_freezes(
        roots=roots,
        attempts=calibration_attempts,
        stream_census=tuple((corpus_id, calibration_ids[corpus_id]) for corpus_id in corpus_order),
    )
    freeze_by_corpus = {item.corpus_id: item for item in baseline_freezes}
    try:
        receipt_value = backend.execute_validity_controls(
            roots=roots,
            baseline_freezes=baseline_freezes,  # type: ignore[arg-type]
        )
        validity_receipt = Gate3ValidityReceipt.model_validate(
            receipt_value.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        stage_failures: tuple[Gate3StageFailure, ...] = ()
    except Exception as error:
        failure_type, detail = _failure_fields(error)
        validity_receipt = None
        stage_failures = (
            Gate3StageFailure(
                stage="validity_controls",
                failure_type=failure_type,
                failure_detail=detail,
            ),
        )
    if validity_receipt is None or validity_receipt.status != "valid":
        return _build_gate3_early_result(
            roots=roots,
            calibration_attempts=calibration_attempts,
            baseline_freezes=baseline_freezes,
            validity_receipt=validity_receipt,
            stage_failures=stage_failures,
            central_attempts=(),
            corpus_summaries=(),
            central_statistics=None,
        )
    central_attempts_list: list[Gate3CentralAttempt] = []
    corpus_summaries_list: list[Gate3CentralGroupSummary] = []
    for corpus_id in ("loco-2dics", "lectra-m3-m4"):
        stream_ids = backend.confirmation_stream_ids(corpus_id)
        if len(stream_ids) != 20 or len(set(stream_ids)) != 20:
            raise ValueError("Gate 3 backend confirmation stream census differs from 20+20")
        starting_position = len(central_attempts_list)
        for offset, stream_id in enumerate(stream_ids):
            try:
                cell_value = backend.execute_central_stream(
                    roots=roots,
                    corpus_id=corpus_id,
                    stream_id=stream_id,
                    baseline_freeze=freeze_by_corpus[corpus_id],
                )
                cell = Gate3StreamCell.model_validate(
                    cell_value.model_dump(mode="python", round_trip=True),
                    strict=True,
                )
                error = None
            except Exception as caught:  # preserve every registered stream failure
                cell = None
                error = caught
            central_attempts_list.append(
                _build_central_attempt(
                    roots=roots,
                    execution_position=starting_position + offset,
                    corpus_id=corpus_id,
                    stream_id=stream_id,
                    cell=cell,
                    error=error,
                )
            )
        corpus_attempts = tuple(
            item for item in central_attempts_list if item.corpus_id == corpus_id
        )
        if any(item.status == "failure" for item in corpus_attempts):
            return _build_gate3_early_result(
                roots=roots,
                calibration_attempts=calibration_attempts,
                baseline_freezes=baseline_freezes,
                validity_receipt=validity_receipt,
                stage_failures=(),
                central_attempts=tuple(central_attempts_list),
                corpus_summaries=tuple(corpus_summaries_list),
                central_statistics=None,
            )
        summary = calculate_gate3_corpus_central_summary(
            tuple(item.cell for item in corpus_attempts if item.cell is not None),
            canonical_stream_ids=stream_ids,
        )
        corpus_summaries_list.append(summary)
        if not summary.central_green:
            return _build_gate3_early_result(
                roots=roots,
                calibration_attempts=calibration_attempts,
                baseline_freezes=baseline_freezes,
                validity_receipt=validity_receipt,
                stage_failures=(),
                central_attempts=tuple(central_attempts_list),
                corpus_summaries=tuple(corpus_summaries_list),
                central_statistics=None,
            )
    lectra_cells = tuple(
        item.cell
        for item in central_attempts_list
        if item.corpus_id == "lectra-m3-m4" and item.cell is not None
    )
    loco_cells = tuple(
        item.cell
        for item in central_attempts_list
        if item.corpus_id == "loco-2dics" and item.cell is not None
    )
    statistics = calculate_gate3_central_statistics(
        lectra_cells + loco_cells,
        lectra_stream_ids=backend.confirmation_stream_ids("lectra-m3-m4"),
        loco_stream_ids=backend.confirmation_stream_ids("loco-2dics"),
    )
    return _build_gate3_early_result(
        roots=roots,
        calibration_attempts=calibration_attempts,
        baseline_freezes=baseline_freezes,
        validity_receipt=validity_receipt,
        stage_failures=(),
        central_attempts=tuple(central_attempts_list),
        corpus_summaries=tuple(corpus_summaries_list),
        central_statistics=statistics,
    )


__all__ = [
    "GATE3_BASELINE_POLICY_IDS",
    "Gate3AppliedActionContext",
    "Gate3Algorithm",
    "Gate3Arm",
    "Gate3ArmTrace",
    "Gate3BaselineCalibrationFreeze",
    "Gate3BaselineCalibrationScore",
    "Gate3BaselinePolicyId",
    "Gate3CalibrationAttempt",
    "Gate3CalibrationM9Transition",
    "Gate3CalibrationMaterialReplay",
    "Gate3CalibrationObservation",
    "Gate3CentralAttempt",
    "Gate3CentralGroupSummary",
    "Gate3CentralStatistics",
    "Gate3ConfirmationBackend",
    "Gate3CorpusId",
    "Gate3CostLedger",
    "Gate3DecisionTrace",
    "Gate3DecisionRuntimeReceipt",
    "Gate3EarlyConfirmationResult",
    "Gate3EarlyDisposition",
    "Gate3EarlyStatus",
    "Gate3ExactAuditTrace",
    "Gate3ExactMaterialAudit",
    "Gate3ExactRootScore",
    "Gate3ExactSearchTelemetry",
    "Gate3HardNullArmTrace",
    "Gate3HardNullControl",
    "Gate3HardNullKind",
    "Gate3M9RootScore",
    "Gate3NoSignalSummary",
    "Gate3RootBinding",
    "Gate3ProjectionShardEvidence",
    "Gate3ShardTrace",
    "Gate3StageFailure",
    "Gate3StreamCell",
    "Gate3TieRule",
    "Gate3TwinControl",
    "Gate3ValidityReceipt",
    "Gate3Visibility",
    "build_gate3_applied_action_context",
    "build_gate3_calibration_m9_transition",
    "build_gate3_calibration_material_replay",
    "build_gate3_calibration_observation",
    "build_gate3_cost_ledger",
    "build_gate3_decision_runtime_receipt",
    "build_gate3_decision_trace",
    "build_gate3_exact_audit_trace",
    "build_gate3_exact_material_audit",
    "build_gate3_hard_null_arm_trace",
    "build_gate3_hard_null_control",
    "build_gate3_projection_shard_evidence",
    "build_gate3_root_binding",
    "build_gate3_shard_trace",
    "build_gate3_stream_cell",
    "build_gate3_twin_control",
    "calculate_gate3_central_statistics",
    "calculate_gate3_corpus_central_summary",
    "evaluate_gate3_validity_controls",
    "gate3_inventory_sha256",
    "merge_gate3_material_shards",
    "run_gate3_early_confirmation",
    "select_gate3_baseline_policy",
    "summarize_gate3_no_signal",
]
