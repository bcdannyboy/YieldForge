"""Frozen deterministic policy variants for the M7 strong baseline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, StrictStr, model_validator

from yieldforge.baseline.contracts import BaselineContractModel, M7ActionKind


class M7PolicyName(StrEnum):
    MYOPIC_GEOMETRY = "myopic_geometry"
    REMNANT_FIRST = "remnant_first"
    NET_COST = "net_cost"
    AGE_REGULARITY = "age_regularity"
    KNOWN_ORDER_LOOKAHEAD = "known_order_lookahead"


class M7PolicyIdentity(BaselineContractModel):
    """Frozen policy and as-of information boundary."""

    schema_version: Literal["yieldforge.m7-policy.v1"] = "yieldforge.m7-policy.v1"
    name: M7PolicyName
    version: Literal["1.0.0"] = "1.0.0"
    seed: Literal[0] = 0
    information_set: StrictStr = Field(min_length=1)
    lookahead_availability: Literal[
        "not_applicable",
        "zero_no_pre_release_known_at_field",
    ]

    @model_validator(mode="after")
    def require_registered_information_boundary(self) -> Self:
        if self.name is M7PolicyName.KNOWN_ORDER_LOOKAHEAD:
            expected = (
                "released_work_inventory_and_firm_known_orders_only",
                "zero_no_pre_release_known_at_field",
            )
        else:
            expected = ("released_work_and_current_inventory_only", "not_applicable")
        if (self.information_set, self.lookahead_availability) != expected:
            raise ValueError("M7 policy information boundary differs from its frozen variant")
        return self


@dataclass(frozen=True)
class ActionPolicyContext:
    """Policy-visible terms for one exact action; no unreleased event data is present."""

    action_id: str
    kind: M7ActionKind
    candidate_id: str
    candidate_width: float
    selected_stock_id: str
    immediate_net_cost: float
    selected_remnant_age_hours: float
    returned_regularity: float
    known_order_lookahead_term: float = 0.0

    def __post_init__(self) -> None:
        numeric = (
            self.candidate_width,
            self.immediate_net_cost,
            self.selected_remnant_age_hours,
            self.returned_regularity,
            self.known_order_lookahead_term,
        )
        if not all(math.isfinite(item) for item in numeric):
            raise ValueError("policy action terms must be finite")
        if self.candidate_width <= 0 or self.selected_remnant_age_hours < 0:
            raise ValueError("policy width and remnant age must be nonnegative")
        if not 0.0 <= self.returned_regularity <= 1.0:
            raise ValueError("policy returned regularity must be in [0, 1]")
        if self.known_order_lookahead_term != 0.0:
            raise ValueError("M6 provides no pre-release known_at field; lookahead must be zero")


class PolicySelection(BaselineContractModel):
    action_id: StrictStr = Field(min_length=1)
    decision_key: tuple[StrictStr, ...] = Field(min_length=1)


def policy_identity(name: M7PolicyName) -> M7PolicyIdentity:
    if name is M7PolicyName.KNOWN_ORDER_LOOKAHEAD:
        return M7PolicyIdentity(
            name=name,
            information_set="released_work_inventory_and_firm_known_orders_only",
            lookahead_availability="zero_no_pre_release_known_at_field",
        )
    return M7PolicyIdentity(
        name=name,
        information_set="released_work_and_current_inventory_only",
        lookahead_availability="not_applicable",
    )


def registered_policy_identities() -> tuple[M7PolicyIdentity, ...]:
    return tuple(policy_identity(name) for name in M7PolicyName)


def _number(value: float) -> str:
    if value == 0.0:
        return "0"
    return format(value, ".12g")


def _stable_tail(item: ActionPolicyContext) -> tuple[str, str, str]:
    return item.candidate_id, item.selected_stock_id, item.action_id


def select_policy_action(
    policy: M7PolicyName,
    choices: tuple[ActionPolicyContext, ...],
) -> PolicySelection:
    """Choose one action using only the registered visible score terms."""

    if not choices:
        raise ValueError("M7 policy has no action to select")

    def ranked(item: ActionPolicyContext) -> tuple[tuple[object, ...], tuple[str, ...]]:
        tail = _stable_tail(item)
        if policy is M7PolicyName.MYOPIC_GEOMETRY:
            key = (item.candidate_width, *tail)
            evidence = (
                f"candidate_width={_number(item.candidate_width)}",
                f"candidate_id={item.candidate_id}",
                f"selected_stock_id={item.selected_stock_id}",
                f"action_id={item.action_id}",
            )
        elif policy is M7PolicyName.REMNANT_FIRST:
            kind_rank = 0 if item.kind is M7ActionKind.CONSUME_REMNANT else 1
            key = (kind_rank, item.immediate_net_cost, *tail)
            evidence = (
                f"remnant_first_rank={kind_rank}",
                f"immediate_net_cost={_number(item.immediate_net_cost)}",
                f"candidate_id={item.candidate_id}",
                f"selected_stock_id={item.selected_stock_id}",
                f"action_id={item.action_id}",
            )
        elif policy is M7PolicyName.NET_COST:
            key = (item.immediate_net_cost, *tail)
            evidence = (
                f"immediate_net_cost={_number(item.immediate_net_cost)}",
                f"candidate_id={item.candidate_id}",
                f"selected_stock_id={item.selected_stock_id}",
                f"action_id={item.action_id}",
            )
        elif policy is M7PolicyName.AGE_REGULARITY:
            key = (
                item.immediate_net_cost,
                -item.selected_remnant_age_hours,
                -item.returned_regularity,
                *tail,
            )
            evidence = (
                f"immediate_net_cost={_number(item.immediate_net_cost)}",
                f"selected_remnant_age_hours={_number(item.selected_remnant_age_hours)}",
                f"returned_regularity={_number(item.returned_regularity)}",
                f"candidate_id={item.candidate_id}",
                f"selected_stock_id={item.selected_stock_id}",
                f"action_id={item.action_id}",
            )
        elif policy is M7PolicyName.KNOWN_ORDER_LOOKAHEAD:
            combined = item.immediate_net_cost + item.known_order_lookahead_term
            key = (combined, item.known_order_lookahead_term, *tail)
            evidence = (
                f"combined_known_cost={_number(combined)}",
                "known_order_lookahead_term=0",
                f"candidate_id={item.candidate_id}",
                f"selected_stock_id={item.selected_stock_id}",
                f"action_id={item.action_id}",
            )
        else:  # pragma: no cover - StrEnum validation closes this branch.
            raise ValueError("unregistered M7 policy")
        return key, evidence

    ranked_choices = tuple((ranked(item), item) for item in choices)
    (comparison_key, decision_key), selected = min(
        ranked_choices,
        key=lambda pair: pair[0][0],
    )
    del comparison_key
    return PolicySelection(action_id=selected.action_id, decision_key=decision_key)


__all__ = [
    "ActionPolicyContext",
    "M7PolicyIdentity",
    "M7PolicyName",
    "PolicySelection",
    "policy_identity",
    "registered_policy_identities",
    "select_policy_action",
]
