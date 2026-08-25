"""Strict, content-addressed proof contracts for exact M8 action scoring."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.contracts import BaselineContractModel
from yieldforge.experiments.contracts import semantic_sha256

M8InfluenceClassification = Literal["no_fit", "policy_dominated"]
M8EventClassification = Literal[
    "state_rejoin",
    "no_fit",
    "policy_dominated",
    "exact_transition",
]

_ACTION_ID_PATTERN = r"^yfm7a-[0-9a-f]{24}$"
_REMNANT_ID_PATTERN = r"^yfrm-[0-9a-f]{24}$"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class M8InfluenceWitness(BaselineContractModel):
    """Evidence that one branch-only remnant cannot alter one M7 decision."""

    model_config = ConfigDict(revalidate_instances="always")

    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    candidate_id: StrictStr | None = Field(default=None, min_length=1)
    classification: M8InfluenceClassification
    evidence_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    common_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    competing_action_id: StrictStr | None = Field(default=None, pattern=_ACTION_ID_PATTERN)
    common_decision_key: tuple[StrictStr, ...] = Field(min_length=1)
    competing_decision_key: tuple[StrictStr, ...] | None = None

    @model_validator(mode="after")
    def require_classification_evidence_and_action_bindings(self) -> Self:
        if f"action_id={self.common_action_id}" not in self.common_decision_key:
            raise ValueError("influence common decision key does not bind its action ID")
        if self.classification == "no_fit":
            if self.competing_action_id is not None or self.competing_decision_key is not None:
                raise ValueError("no-fit influence cannot carry a competing policy action")
            return self
        if (
            self.candidate_id is None
            or self.competing_action_id is None
            or self.competing_decision_key is None
        ):
            raise ValueError(
                "policy-dominated influence requires candidate, competing action, and decision key"
            )
        if self.competing_action_id == self.common_action_id:
            raise ValueError("policy-dominated influence requires distinct competing action")
        if f"action_id={self.competing_action_id}" not in self.competing_decision_key:
            raise ValueError("influence competing decision key does not bind its action ID")
        return self


class M8EventWitness(BaselineContractModel):
    """One ordered exact or certified transition in an M8 action proof."""

    model_config = ConfigDict(revalidate_instances="always")

    event_position: StrictInt = Field(ge=0)
    classification: M8EventClassification
    common_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    branch_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    state_before_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    state_after_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    influences: tuple[M8InfluenceWitness, ...] = ()

    @model_validator(mode="after")
    def require_classification_specific_evidence(self) -> Self:
        if self.classification in {"state_rejoin", "exact_transition"}:
            if self.influences:
                raise ValueError(f"{self.classification} event cannot carry influence witnesses")
            if (
                self.classification == "state_rejoin"
                and self.common_action_id != self.branch_action_id
            ):
                raise ValueError("state-rejoin event requires the same common and branch action")
            return self

        if self.common_action_id != self.branch_action_id:
            raise ValueError(
                "certified no-fit and policy-dominated events require the same common "
                "and branch action"
            )
        if not self.influences or any(
            item.classification != self.classification for item in self.influences
        ):
            raise ValueError(
                f"{self.classification} event requires nonempty matching influence witnesses"
            )
        if any(item.common_action_id != self.common_action_id for item in self.influences):
            raise ValueError("event influence does not bind the event common action ID")
        influence_keys = tuple(
            (item.remnant_id, item.candidate_id, item.competing_action_id)
            for item in self.influences
        )
        if len(influence_keys) != len(set(influence_keys)):
            raise ValueError("event influence witnesses must be unique")
        return self


class M8ActionProof(BaselineContractModel):
    """Complete stop-exclusive proof of one current M8 action's terminal cost."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-action-proof.v1"] = (
        "yieldforge.m8-action-proof.v1"
    )
    proof_id: StrictStr = Field(pattern=r"^yfm8ap-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    baseline_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    start_event_position: StrictInt = Field(ge=0)
    stop_event_position: StrictInt = Field(ge=1)
    suffix_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    witnesses: tuple[M8EventWitness, ...]
    final_net_cost: StrictFloat

    @model_validator(mode="after")
    def require_complete_suffix_and_content_identity(self) -> Self:
        if self.stop_event_position < self.start_event_position + 1:
            raise ValueError("M8 proof stop event position must follow the current event")
        expected_positions = tuple(
            range(self.start_event_position + 1, self.stop_event_position)
        )
        observed_positions = tuple(item.event_position for item in self.witnesses)
        if observed_positions != expected_positions:
            raise ValueError("M8 proof witnesses must provide exact ordered suffix coverage")
        digest = semantic_sha256(self, excluded_fields={"proof_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M8 action proof SHA-256 does not match semantic content")
        if self.proof_id != f"yfm8ap-{digest[:24]}":
            raise ValueError("M8 action proof ID does not match semantic content")
        return self


def build_m8_action_proof(
    *,
    action_id: str,
    baseline_action_id: str,
    start_event_position: int,
    stop_event_position: int,
    suffix_sha256: str,
    witnesses: tuple[M8EventWitness, ...],
    final_net_cost: float,
) -> M8ActionProof:
    """Build and validate one content-addressed exact action proof."""

    canonical_witnesses = tuple(
        M8EventWitness.model_validate(item.model_dump(mode="python"), strict=True)
        for item in witnesses
    )
    semantic = {
        "schema_version": "yieldforge.m8-action-proof.v1",
        "action_id": action_id,
        "baseline_action_id": baseline_action_id,
        "start_event_position": start_event_position,
        "stop_event_position": stop_event_position,
        "suffix_sha256": suffix_sha256,
        "witnesses": [item.model_dump(mode="json") for item in canonical_witnesses],
        "final_net_cost": final_net_cost,
    }
    digest = semantic_sha256(semantic)
    return M8ActionProof(
        proof_id=f"yfm8ap-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        action_id=action_id,
        baseline_action_id=baseline_action_id,
        start_event_position=start_event_position,
        stop_event_position=stop_event_position,
        suffix_sha256=suffix_sha256,
        witnesses=canonical_witnesses,
        final_net_cost=final_net_cost,
    )


__all__ = [
    "M8ActionProof",
    "M8EventClassification",
    "M8EventWitness",
    "M8InfluenceClassification",
    "M8InfluenceWitness",
    "build_m8_action_proof",
]
