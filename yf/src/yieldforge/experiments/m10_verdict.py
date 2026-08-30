"""Strict evidence-ceiling investment verdict for the minimum M10 decision."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, StrictStr, model_validator

from yieldforge.baseline.contracts import BaselineContractModel
from yieldforge.experiments.contracts import semantic_sha256

M10ParentRole = Literal[
    "m0_contract",
    "m6_contract",
    "m6_population",
    "m7_evaluation",
    "m8_gate3",
    "m9_repair",
]

M10_PARENT_ROLE_ORDER: tuple[M10ParentRole, ...] = (
    "m0_contract",
    "m6_contract",
    "m6_population",
    "m7_evaluation",
    "m8_gate3",
    "m9_repair",
)

M10_MISSING_FORMAL_MEASUREMENTS = (
    "oracle_savings_percent",
    "unknown_future_contribution_percentage_points",
)

M10_MISSING_REQUIRED_CONTROLS = (
    "full_future_oracle_evaluation",
    "known_only_information_ablation",
    "no_signal_oracle_control",
    "terminal_value_evaluation_sensitivity",
    "remnant_eligibility_evaluation_sensitivity",
    "ordinary_vs_expanded_search_evaluation",
    "rollout_vs_beam_evaluation",
)

M10_REOPEN_CONDITIONS = (
    "permissioned_real_manufacturer_chronology_and_remnant_history",
    "observed_material_identities_and_economically_meaningful_costs",
    "independent_second_geometry_corpus",
    "buyer_or_operator_owned_bounded_decision",
)

M10_CLAIM_CEILING = (
    "investment_decision_only_not_formal_m0_economic_band_oracle_savings_unknown_future_"
    "contribution_physical_recoverability_buyer_demand_or_commercial_proof"
)


class M10ParentBinding(BaselineContractModel):
    """One exact semantic and raw-file parent of the M10 decision."""

    role: M10ParentRole
    repository_path: StrictStr = Field(min_length=1)
    schema_version: StrictStr = Field(min_length=1)
    semantic_id: StrictStr = Field(min_length=1)
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    raw_file_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class M10EvidenceSnapshot(BaselineContractModel):
    """The sole current evidence state admitted by the minimum M10 rule."""

    parents: tuple[M10ParentBinding, ...]
    geometry_corpus_ids: tuple[StrictStr, ...]
    required_positive_geometry_corpus_count: Literal[2]
    chronology_provenance: Literal["generated"]
    economics_provenance: Literal["generated"]
    material_provenance: Literal["assumed"]
    baseline_stream_count: Literal[36]
    baseline_repeat_count: Literal[2]
    baseline_repeat_identity_match: Literal[True]
    m8_decision: Literal["hold_performance"]
    oracle_evaluation_opened: Literal[False]
    oracle_savings_percent: None = None
    unknown_future_contribution_percentage_points: None = None
    m9_decision: Literal["pass_decision_feasibility"]

    @model_validator(mode="after")
    def require_current_evidence_census(self) -> Self:
        roles = tuple(parent.role for parent in self.parents)
        if roles != M10_PARENT_ROLE_ORDER:
            raise ValueError("M10 parent roles differ from the frozen order")
        if len({parent.repository_path for parent in self.parents}) != len(self.parents):
            raise ValueError("M10 parent repository paths must be unique")
        if len({parent.semantic_id for parent in self.parents}) != len(self.parents):
            raise ValueError("M10 parent semantic IDs must be unique")
        if (
            len(self.geometry_corpus_ids) != 1
            or not self.geometry_corpus_ids[0]
            or len(set(self.geometry_corpus_ids)) != 1
        ):
            raise ValueError("M10 minimum evidence must contain exactly one geometry corpus")
        return self


class M10MinimumInvestmentVerdict(BaselineContractModel):
    """Content-addressed roadmap decision without a fabricated M0 economic band."""

    schema_version: Literal["yieldforge.m10-minimum-investment-verdict.v1"] = (
        "yieldforge.m10-minimum-investment-verdict.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm10-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence: M10EvidenceSnapshot
    decision_basis: Literal["current_evidence_ceiling_without_formal_numeric_m0_band"] = (
        "current_evidence_ceiling_without_formal_numeric_m0_band"
    )
    formal_economic_band: Literal["not_computed"] = "not_computed"
    formal_numeric_m10_complete: Literal[False] = False
    investment_verdict: Literal["acquire_real_manufacturer_history"] = (
        "acquire_real_manufacturer_history"
    )
    productization_decision: Literal["do_not_productize"] = "do_not_productize"
    additional_virtual_oracle_investment: Literal["stop"] = "stop"
    roadmap_decision_complete: Literal[True] = True
    green_eligible: Literal[False] = False
    missing_formal_measurements: tuple[
        Literal["oracle_savings_percent"],
        Literal["unknown_future_contribution_percentage_points"],
    ] = M10_MISSING_FORMAL_MEASUREMENTS
    missing_required_controls: tuple[
        Literal["full_future_oracle_evaluation"],
        Literal["known_only_information_ablation"],
        Literal["no_signal_oracle_control"],
        Literal["terminal_value_evaluation_sensitivity"],
        Literal["remnant_eligibility_evaluation_sensitivity"],
        Literal["ordinary_vs_expanded_search_evaluation"],
        Literal["rollout_vs_beam_evaluation"],
    ] = M10_MISSING_REQUIRED_CONTROLS
    reopen_conditions: tuple[
        Literal["permissioned_real_manufacturer_chronology_and_remnant_history"],
        Literal["observed_material_identities_and_economically_meaningful_costs"],
        Literal["independent_second_geometry_corpus"],
        Literal["buyer_or_operator_owned_bounded_decision"],
    ] = M10_REOPEN_CONDITIONS
    claim_ceiling: Literal[
        "investment_decision_only_not_formal_m0_economic_band_oracle_savings_unknown_future_"
        "contribution_physical_recoverability_buyer_demand_or_commercial_proof"
    ] = M10_CLAIM_CEILING

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}" or self.result_id != (
            f"yfm10-{digest[:24]}"
        ):
            raise ValueError("M10 result identity does not match semantic content")
        return self


def build_minimum_investment_verdict(
    evidence: M10EvidenceSnapshot,
) -> M10MinimumInvestmentVerdict:
    """Derive the only authorized decision from the exact current evidence state."""

    semantic = {
        "schema_version": "yieldforge.m10-minimum-investment-verdict.v1",
        "evidence": evidence.model_dump(mode="json"),
        "decision_basis": "current_evidence_ceiling_without_formal_numeric_m0_band",
        "formal_economic_band": "not_computed",
        "formal_numeric_m10_complete": False,
        "investment_verdict": "acquire_real_manufacturer_history",
        "productization_decision": "do_not_productize",
        "additional_virtual_oracle_investment": "stop",
        "roadmap_decision_complete": True,
        "green_eligible": False,
        "missing_formal_measurements": M10_MISSING_FORMAL_MEASUREMENTS,
        "missing_required_controls": M10_MISSING_REQUIRED_CONTROLS,
        "reopen_conditions": M10_REOPEN_CONDITIONS,
        "claim_ceiling": M10_CLAIM_CEILING,
    }
    digest = semantic_sha256(semantic)
    return M10MinimumInvestmentVerdict(
        result_id=f"yfm10-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        evidence=evidence,
    )


__all__ = [
    "M10EvidenceSnapshot",
    "M10MinimumInvestmentVerdict",
    "M10ParentBinding",
    "build_minimum_investment_verdict",
]
