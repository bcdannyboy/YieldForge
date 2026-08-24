"""Strict content-addressed contracts for M8 rollout decisions."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.contracts import BaselineContractModel
from yieldforge.experiments.contracts import semantic_sha256


class M8ActionScore(BaselineContractModel):
    """Terminal M0 net cost for one exact current action."""

    action_id: StrictStr = Field(min_length=1)
    final_net_cost: StrictFloat


class M8OracleDecision(BaselineContractModel):
    """Complete exact score vector and deterministic rollout choice."""

    schema_version: Literal["yieldforge.m8-oracle-decision.v1"] = (
        "yieldforge.m8-oracle-decision.v1"
    )
    decision_id: StrictStr = Field(pattern=r"^yfm8d-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    baseline_action_id: StrictStr = Field(min_length=1)
    selected_action_id: StrictStr = Field(min_length=1)
    scored_action_count: StrictInt = Field(ge=1)
    action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    scores: tuple[M8ActionScore, ...] = Field(min_length=1)
    claim_ceiling: Literal[
        "exact_generated_stream_rollout_decision_only_not_global_optimality_physical_or_"
        "commercial_evidence"
    ] = (
        "exact_generated_stream_rollout_decision_only_not_global_optimality_physical_or_"
        "commercial_evidence"
    )

    @model_validator(mode="after")
    def require_complete_scores_choice_and_identity(self) -> Self:
        score_ids = tuple(item.action_id for item in self.scores)
        if self.scored_action_count != len(self.scores):
            raise ValueError("M8 scored action count does not reconcile")
        if self.action_ids != tuple(sorted(set(self.action_ids))):
            raise ValueError("M8 expected action IDs must be sorted and unique")
        if tuple(sorted(score_ids)) != self.action_ids or len(score_ids) != len(set(score_ids)):
            raise ValueError("M8 score vector differs from the expected action catalog")
        if self.baseline_action_id not in score_ids:
            raise ValueError("M8 baseline action is absent from the score vector")
        selected = min(
            self.scores,
            key=lambda item: (
                item.final_net_cost,
                item.action_id != self.baseline_action_id,
                item.action_id,
            ),
        )
        if self.selected_action_id != selected.action_id:
            raise ValueError("M8 selected action differs from the registered tie rule")
        digest = semantic_sha256(self, excluded_fields={"decision_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M8 decision content SHA-256 does not match semantic content")
        if self.decision_id != f"yfm8d-{digest[:24]}":
            raise ValueError("M8 decision ID does not match semantic content")
        return self


def build_oracle_decision(
    *,
    baseline_action_id: str,
    expected_action_ids: tuple[str, ...],
    scores: tuple[tuple[str, float], ...],
) -> M8OracleDecision:
    """Build a fail-closed decision with baseline-preferred exact ties."""

    action_ids = tuple(sorted(expected_action_ids))
    score_models = tuple(
        M8ActionScore(action_id=action_id, final_net_cost=cost)
        for action_id, cost in scores
    )
    if baseline_action_id not in {item.action_id for item in score_models}:
        raise ValueError("M8 baseline action is absent from the score vector")
    selected = min(
        score_models,
        key=lambda item: (
            item.final_net_cost,
            item.action_id != baseline_action_id,
            item.action_id,
        ),
    )
    semantic = {
        "schema_version": "yieldforge.m8-oracle-decision.v1",
        "baseline_action_id": baseline_action_id,
        "selected_action_id": selected.action_id,
        "scored_action_count": len(score_models),
        "action_ids": action_ids,
        "scores": [item.model_dump(mode="json") for item in score_models],
        "claim_ceiling": (
            "exact_generated_stream_rollout_decision_only_not_global_optimality_physical_or_"
            "commercial_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8OracleDecision(
        decision_id=f"yfm8d-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        baseline_action_id=baseline_action_id,
        selected_action_id=selected.action_id,
        scored_action_count=len(score_models),
        action_ids=action_ids,
        scores=score_models,
    )


__all__ = ["M8ActionScore", "M8OracleDecision", "build_oracle_decision"]
