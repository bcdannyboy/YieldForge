"""Outcome-blind decision rules for the M11 economic-value test.

This addendum changes only how unopened central economic outcomes will be
interpreted.  It neither changes nor revalidates calibration evidence.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictStr, model_validator

from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256
from yieldforge.realistic_falsification.economic_resolution import (
    EconomicResolutionProtocol,
    build_economic_resolution_protocol,
)

BASE_ECONOMIC_RESOLUTION_PROTOCOL_ID = "yfm11econp-fa9218f96bb810350e8526e4"
BASE_ECONOMIC_RESOLUTION_PROTOCOL_CONTENT_SHA256 = (
    "sha256:fa9218f96bb810350e8526e48bd2f9cfe7404eea47146ef2516878c6aa266cfd"
)
_DECIMAL_PATTERN = r"^-?(?:0|[1-9][0-9]{0,6})\.[0-9]{12}$"
_DECIMAL_RE = re.compile(_DECIMAL_PATTERN)
_MAX_ABSOLUTE_METRIC = Decimal("1000000.000000000000")

EconomicCorpusId = Literal["loco-2dics", "lectra-m3-m4"]
EconomicCandidateClassification = Literal[
    "causal_candidate",
    "forecast_candidate",
    "current_segment_red",
]
LocoEconomicNextStep = Literal[
    "CONTINUE_ADVERSE_LOCO",
    "CONTINUE_FORECAST_LOCO",
    "CONTINUE_LECTRA_SCREEN",
]
EconomicGlobalDisposition = Literal[
    "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION",
    "CONTINUE_FORECAST_SEGMENT_CONFIRMATION",
    "INSUFFICIENT_CURRENT_MODELED_VALUE",
]
EconomicNextAction = Literal[
    "CONTINUE_ADVERSE_LOCO",
    "CONTINUE_ADVERSE_LECTRA",
    "CONTINUE_FORECAST_LOCO",
    "CONTINUE_FORECAST_LECTRA",
]


class EconomicDecisionAddendum(FrozenExperimentModel):
    """Content-addressed correction to central economic interpretation only."""

    schema_version: Literal["yieldforge.m11-economic-decision-addendum.v1"] = (
        "yieldforge.m11-economic-decision-addendum.v1"
    )
    addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_protocol_id: StrictStr = Field(pattern=r"^yfm11econp-[0-9a-f]{24}$")
    base_protocol_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    supersession_scope: Literal["central_economic_interpretation_only"] = (
        "central_economic_interpretation_only"
    )
    calibration_evidence_revalidation_required: Literal[False] = False
    outcomes_opened_at_registration: Literal[False] = False
    bootstrap_bit_generator: Literal["PCG64"] = "PCG64"
    bootstrap_generator: Literal["numpy.Generator(PCG64(0))"] = "numpy.Generator(PCG64(0))"
    bootstrap_seed: Literal[0] = 0
    bootstrap_resamples: Literal[10000] = 10_000
    bootstrap_resampling_unit: Literal["paired_stream"] = "paired_stream"
    bootstrap_quantile_method: Literal["linear_type_7"] = "linear_type_7"
    bootstrap_confidence_level: Literal["0.950000000000"] = "0.950000000000"
    bootstrap_lower_quantile: Literal["0.025000000000"] = "0.025000000000"
    bootstrap_upper_quantile: Literal["0.975000000000"] = "0.975000000000"
    f_mean_savings_min_percent: Literal["2.500000000000"] = "2.500000000000"
    unknown_headroom_mean_min_percentage_points: Literal["1.500000000000"] = "1.500000000000"
    unknown_headroom_role: Literal["diagnostic_only_nonveto"] = "diagnostic_only_nonveto"
    k_mean_savings_min_percent: Literal["1.500000000000"] = "1.500000000000"
    lower_confidence_bound_rule: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    median_savings_rule: Literal["strictly_greater_than_zero"] = "strictly_greater_than_zero"
    positive_stream_fraction_rule: Literal["strictly_greater_than_one_half"] = (
        "strictly_greater_than_one_half"
    )
    f_proven_upper_bound_of_k: Literal[False] = False
    f_alone_may_abandon: Literal[False] = False
    segment_candidate_rule: Literal[
        "k_green_then_causal_else_f_green_then_forecast_else_current_segment_red"
    ] = "k_green_then_causal_else_f_green_then_forecast_else_current_segment_red"
    loco_branch_rule: Literal["causal_adverse_else_forecast_branch_else_lectra_screen"] = (
        "causal_adverse_else_forecast_branch_else_lectra_screen"
    )
    cross_segment_rule: Literal[
        "any_causal_adverse_for_each_else_any_forecast_for_each_else_both_red_insufficient"
    ] = "any_causal_adverse_for_each_else_any_forecast_for_each_else_both_red_insufficient"
    global_insufficient_rule: Literal["both_segments_current_red_only"] = (
        "both_segments_current_red_only"
    )
    productization_authorized: Literal[False] = False
    positive_result_ceiling: Literal[
        "bounded_pilot_candidate_only_after_adverse_or_deployable_confirmation"
    ] = "bounded_pilot_candidate_only_after_adverse_or_deployable_confirmation"

    @model_validator(mode="after")
    def require_base_binding_and_identity(self) -> Self:
        if (
            self.base_protocol_id,
            self.base_protocol_content_sha256,
        ) != (
            BASE_ECONOMIC_RESOLUTION_PROTOCOL_ID,
            BASE_ECONOMIC_RESOLUTION_PROTOCOL_CONTENT_SHA256,
        ):
            raise ValueError("economic-decision addendum base protocol binding differs")
        digest = semantic_sha256(self, excluded_fields={"addendum_id", "content_sha256"})
        if self.addendum_id != f"yfm11econdec-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("economic-decision addendum identity differs from content")
        return self


def build_economic_decision_addendum(
    *, base_protocol: EconomicResolutionProtocol
) -> EconomicDecisionAddendum:
    """Build the sole addendum bound to the exact current resolution protocol."""

    strict_base = EconomicResolutionProtocol.model_validate(
        base_protocol.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    current_base = build_economic_resolution_protocol()
    if strict_base != current_base or (
        strict_base.protocol_id,
        strict_base.content_sha256,
    ) != (
        BASE_ECONOMIC_RESOLUTION_PROTOCOL_ID,
        BASE_ECONOMIC_RESOLUTION_PROTOCOL_CONTENT_SHA256,
    ):
        raise ValueError("economic-decision addendum requires the exact current base protocol")
    semantic = {
        "schema_version": "yieldforge.m11-economic-decision-addendum.v1",
        "base_protocol_id": strict_base.protocol_id,
        "base_protocol_content_sha256": strict_base.content_sha256,
        "supersession_scope": "central_economic_interpretation_only",
        "calibration_evidence_revalidation_required": False,
        "outcomes_opened_at_registration": False,
        "bootstrap_bit_generator": "PCG64",
        "bootstrap_generator": "numpy.Generator(PCG64(0))",
        "bootstrap_seed": 0,
        "bootstrap_resamples": 10_000,
        "bootstrap_resampling_unit": "paired_stream",
        "bootstrap_quantile_method": "linear_type_7",
        "bootstrap_confidence_level": "0.950000000000",
        "bootstrap_lower_quantile": "0.025000000000",
        "bootstrap_upper_quantile": "0.975000000000",
        "f_mean_savings_min_percent": "2.500000000000",
        "unknown_headroom_mean_min_percentage_points": "1.500000000000",
        "unknown_headroom_role": "diagnostic_only_nonveto",
        "k_mean_savings_min_percent": "1.500000000000",
        "lower_confidence_bound_rule": "strictly_greater_than_zero",
        "median_savings_rule": "strictly_greater_than_zero",
        "positive_stream_fraction_rule": "strictly_greater_than_one_half",
        "f_proven_upper_bound_of_k": False,
        "f_alone_may_abandon": False,
        "segment_candidate_rule": (
            "k_green_then_causal_else_f_green_then_forecast_else_current_segment_red"
        ),
        "loco_branch_rule": "causal_adverse_else_forecast_branch_else_lectra_screen",
        "cross_segment_rule": (
            "any_causal_adverse_for_each_else_any_forecast_for_each_else_both_red_insufficient"
        ),
        "global_insufficient_rule": "both_segments_current_red_only",
        "productization_authorized": False,
        "positive_result_ceiling": (
            "bounded_pilot_candidate_only_after_adverse_or_deployable_confirmation"
        ),
    }
    digest = semantic_sha256(semantic)
    return EconomicDecisionAddendum(
        addendum_id=f"yfm11econdec-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _strict_addendum(addendum: EconomicDecisionAddendum) -> EconomicDecisionAddendum:
    strict = EconomicDecisionAddendum.model_validate(
        addendum.model_dump(mode="python", round_trip=True),
        strict=True,
    )
    expected = build_economic_decision_addendum(base_protocol=build_economic_resolution_protocol())
    if strict != expected:
        raise ValueError("economic segment decision requires the exact decision addendum")
    return strict


def _decimal(
    value: str,
    *,
    label: str,
    minimum: Decimal = -_MAX_ABSOLUTE_METRIC,
    maximum: Decimal = _MAX_ABSOLUTE_METRIC,
) -> Decimal:
    if type(value) is not str or _DECIMAL_RE.fullmatch(value) is None:
        raise TypeError(f"{label} must be a canonical twelve-place decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:  # pragma: no cover - guarded by the regular expression
        raise ValueError(f"{label} must be a finite decimal") from error
    if not parsed.is_finite() or not minimum <= parsed <= maximum:
        raise ValueError(f"{label} lies outside its frozen bounds")
    if parsed == 0 and value.startswith("-"):
        raise ValueError(f"{label} cannot use negative zero")
    return parsed


def _segment_flags(values: dict[str, str]) -> tuple[bool, ...]:
    f_mean = _decimal(values["f_mean_savings_percent"], label="F mean savings")
    f_lcb = _decimal(values["f_mean_ci_lower_percent"], label="F mean CI lower")
    f_median = _decimal(values["f_median_savings_percent"], label="F median savings")
    f_fraction = _decimal(
        values["f_positive_stream_fraction"],
        label="F positive stream fraction",
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    unknown = _decimal(
        values["unknown_headroom_mean_percentage_points"],
        label="unknown headroom mean",
    )
    k_mean = _decimal(values["k_mean_savings_percent"], label="K mean savings")
    k_lcb = _decimal(values["k_mean_ci_lower_percent"], label="K mean CI lower")
    k_median = _decimal(values["k_median_savings_percent"], label="K median savings")
    k_fraction = _decimal(
        values["k_positive_stream_fraction"],
        label="K positive stream fraction",
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    if f_lcb > f_mean or k_lcb > k_mean:
        raise ValueError("economic segment lower confidence bound exceeds its mean")
    f_components = (
        f_mean >= Decimal("2.500000000000"),
        f_lcb > 0,
        f_median > 0,
        f_fraction > Decimal("0.500000000000"),
    )
    k_components = (
        k_mean >= Decimal("1.500000000000"),
        k_lcb > 0,
        k_median > 0,
        k_fraction > Decimal("0.500000000000"),
    )
    return (
        *f_components,
        all(f_components),
        unknown >= Decimal("1.500000000000"),
        *k_components,
        all(k_components),
    )


def _candidate(f_economic_green: bool, k_causal_green: bool) -> EconomicCandidateClassification:
    if k_causal_green:
        return "causal_candidate"
    if f_economic_green:
        return "forecast_candidate"
    return "current_segment_red"


def _loco_next_step(candidate: EconomicCandidateClassification) -> LocoEconomicNextStep:
    return {
        "causal_candidate": "CONTINUE_ADVERSE_LOCO",
        "forecast_candidate": "CONTINUE_FORECAST_LOCO",
        "current_segment_red": "CONTINUE_LECTRA_SCREEN",
    }[candidate]  # type: ignore[return-value]


class EconomicSegmentDecision(FrozenExperimentModel):
    """Pure decision over already-calculated central summary scalars and flags."""

    schema_version: Literal["yieldforge.m11-economic-segment-decision.v1"] = (
        "yieldforge.m11-economic-segment-decision.v1"
    )
    decision_id: StrictStr = Field(pattern=r"^yfm11econseg-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    addendum_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: EconomicCorpusId
    source_summary_id: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    source_summary_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    f_mean_savings_percent: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    f_mean_ci_lower_percent: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    f_median_savings_percent: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    f_positive_stream_fraction: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    unknown_headroom_mean_percentage_points: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    k_mean_savings_percent: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    k_mean_ci_lower_percent: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    k_median_savings_percent: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    k_positive_stream_fraction: StrictStr = Field(pattern=_DECIMAL_PATTERN)
    f_mean_passes: StrictBool
    f_lcb_passes: StrictBool
    f_median_passes: StrictBool
    f_positive_fraction_passes: StrictBool
    f_economic_green: StrictBool
    unknown_headroom_diagnostic_green: StrictBool
    k_mean_passes: StrictBool
    k_lcb_passes: StrictBool
    k_median_passes: StrictBool
    k_positive_fraction_passes: StrictBool
    k_causal_green: StrictBool
    candidate_classification: EconomicCandidateClassification
    loco_next_step: LocoEconomicNextStep | None = None
    global_product_decision_terminal: Literal[False] = False
    f_proven_upper_bound_of_k: Literal[False] = False
    productization_authorized: Literal[False] = False
    bounded_pilot_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_metrics_decision_and_identity(self) -> Self:
        addendum = build_economic_decision_addendum(
            base_protocol=build_economic_resolution_protocol()
        )
        if (self.addendum_id, self.addendum_content_sha256) != (
            addendum.addendum_id,
            addendum.content_sha256,
        ):
            raise ValueError("economic segment addendum binding differs")
        values = {
            field: getattr(self, field)
            for field in (
                "f_mean_savings_percent",
                "f_mean_ci_lower_percent",
                "f_median_savings_percent",
                "f_positive_stream_fraction",
                "unknown_headroom_mean_percentage_points",
                "k_mean_savings_percent",
                "k_mean_ci_lower_percent",
                "k_median_savings_percent",
                "k_positive_stream_fraction",
            )
        }
        expected_flags = _segment_flags(values)
        actual_flags = (
            self.f_mean_passes,
            self.f_lcb_passes,
            self.f_median_passes,
            self.f_positive_fraction_passes,
            self.f_economic_green,
            self.unknown_headroom_diagnostic_green,
            self.k_mean_passes,
            self.k_lcb_passes,
            self.k_median_passes,
            self.k_positive_fraction_passes,
            self.k_causal_green,
        )
        if actual_flags != expected_flags:
            raise ValueError("economic segment flags contradict frozen thresholds")
        expected_candidate = _candidate(self.f_economic_green, self.k_causal_green)
        if self.candidate_classification != expected_candidate:
            raise ValueError("economic segment candidate classification differs")
        expected_loco_step = (
            _loco_next_step(expected_candidate) if self.corpus_id == "loco-2dics" else None
        )
        if self.loco_next_step != expected_loco_step:
            raise ValueError("economic segment LOCo disposition differs")
        digest = semantic_sha256(self, excluded_fields={"decision_id", "content_sha256"})
        if self.decision_id != f"yfm11econseg-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("economic segment decision identity differs from content")
        return self


def build_economic_segment_decision(
    *,
    addendum: EconomicDecisionAddendum,
    corpus_id: EconomicCorpusId,
    source_summary_id: str,
    source_summary_content_sha256: str,
    f_mean_savings_percent: str,
    f_mean_ci_lower_percent: str,
    f_median_savings_percent: str,
    f_positive_stream_fraction: str,
    unknown_headroom_mean_percentage_points: str,
    k_mean_savings_percent: str,
    k_mean_ci_lower_percent: str,
    k_median_savings_percent: str,
    k_positive_stream_fraction: str,
) -> EconomicSegmentDecision:
    """Classify one segment without executing or opening any underlying data."""

    strict_addendum = _strict_addendum(addendum)
    values = {
        "f_mean_savings_percent": f_mean_savings_percent,
        "f_mean_ci_lower_percent": f_mean_ci_lower_percent,
        "f_median_savings_percent": f_median_savings_percent,
        "f_positive_stream_fraction": f_positive_stream_fraction,
        "unknown_headroom_mean_percentage_points": unknown_headroom_mean_percentage_points,
        "k_mean_savings_percent": k_mean_savings_percent,
        "k_mean_ci_lower_percent": k_mean_ci_lower_percent,
        "k_median_savings_percent": k_median_savings_percent,
        "k_positive_stream_fraction": k_positive_stream_fraction,
    }
    flags = _segment_flags(values)
    candidate = _candidate(flags[4], flags[10])
    semantic = {
        "schema_version": "yieldforge.m11-economic-segment-decision.v1",
        "addendum_id": strict_addendum.addendum_id,
        "addendum_content_sha256": strict_addendum.content_sha256,
        "corpus_id": corpus_id,
        "source_summary_id": source_summary_id,
        "source_summary_content_sha256": source_summary_content_sha256,
        **values,
        "f_mean_passes": flags[0],
        "f_lcb_passes": flags[1],
        "f_median_passes": flags[2],
        "f_positive_fraction_passes": flags[3],
        "f_economic_green": flags[4],
        "unknown_headroom_diagnostic_green": flags[5],
        "k_mean_passes": flags[6],
        "k_lcb_passes": flags[7],
        "k_median_passes": flags[8],
        "k_positive_fraction_passes": flags[9],
        "k_causal_green": flags[10],
        "candidate_classification": candidate,
        "loco_next_step": _loco_next_step(candidate) if corpus_id == "loco-2dics" else None,
        "global_product_decision_terminal": False,
        "f_proven_upper_bound_of_k": False,
        "productization_authorized": False,
        "bounded_pilot_authorized": False,
    }
    digest = semantic_sha256(semantic)
    return EconomicSegmentDecision(
        decision_id=f"yfm11econseg-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _strict_segment(decision: EconomicSegmentDecision) -> EconomicSegmentDecision:
    return EconomicSegmentDecision.model_validate(
        decision.model_dump(mode="python", round_trip=True),
        strict=True,
    )


def _cross_segment_fields(
    decisions: tuple[EconomicSegmentDecision, EconomicSegmentDecision],
) -> tuple[EconomicGlobalDisposition, tuple[EconomicNextAction, ...], bool]:
    causal_actions = tuple(
        ("CONTINUE_ADVERSE_LOCO" if item.corpus_id == "loco-2dics" else "CONTINUE_ADVERSE_LECTRA")
        for item in decisions
        if item.candidate_classification == "causal_candidate"
    )
    if causal_actions:
        return "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION", causal_actions, False  # type: ignore[return-value]
    forecast_actions = tuple(
        ("CONTINUE_FORECAST_LOCO" if item.corpus_id == "loco-2dics" else "CONTINUE_FORECAST_LECTRA")
        for item in decisions
        if item.candidate_classification == "forecast_candidate"
    )
    if forecast_actions:
        return "CONTINUE_FORECAST_SEGMENT_CONFIRMATION", forecast_actions, False  # type: ignore[return-value]
    return "INSUFFICIENT_CURRENT_MODELED_VALUE", (), True


class EconomicCrossSegmentDecision(FrozenExperimentModel):
    """Final reducer across LOCo and Lectra segment candidates."""

    schema_version: Literal["yieldforge.m11-economic-cross-segment-decision.v1"] = (
        "yieldforge.m11-economic-cross-segment-decision.v1"
    )
    decision_id: StrictStr = Field(pattern=r"^yfm11econglobal-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    addendum_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    segment_decisions: tuple[EconomicSegmentDecision, EconomicSegmentDecision]
    global_disposition: EconomicGlobalDisposition
    next_actions: tuple[EconomicNextAction, ...] = Field(max_length=2)
    terminal: StrictBool
    product_falsification_scope: Literal[
        "current_modeled_value_not_proof_no_possible_algorithm_can_work"
    ] = "current_modeled_value_not_proof_no_possible_algorithm_can_work"
    productization_authorized: Literal[False] = False
    bounded_pilot_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_segments_disposition_and_identity(self) -> Self:
        addendum = build_economic_decision_addendum(
            base_protocol=build_economic_resolution_protocol()
        )
        if (self.addendum_id, self.addendum_content_sha256) != (
            addendum.addendum_id,
            addendum.content_sha256,
        ):
            raise ValueError("cross-segment decision addendum binding differs")
        if tuple(item.corpus_id for item in self.segment_decisions) != (
            "loco-2dics",
            "lectra-m3-m4",
        ):
            raise ValueError("cross-segment decision requires LOCo then Lectra")
        if any(
            (item.addendum_id, item.addendum_content_sha256)
            != (self.addendum_id, self.addendum_content_sha256)
            for item in self.segment_decisions
        ):
            raise ValueError("cross-segment decisions differ from their addendum")
        expected = _cross_segment_fields(self.segment_decisions)
        if (self.global_disposition, self.next_actions, self.terminal) != expected:
            raise ValueError("cross-segment disposition differs from segment candidates")
        digest = semantic_sha256(self, excluded_fields={"decision_id", "content_sha256"})
        if self.decision_id != f"yfm11econglobal-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("cross-segment decision identity differs from content")
        return self


def reduce_economic_segment_decisions(
    *,
    addendum: EconomicDecisionAddendum,
    loco_decision: EconomicSegmentDecision,
    lectra_decision: EconomicSegmentDecision,
) -> EconomicCrossSegmentDecision:
    """Apply the frozen cross-segment priority rule without executing data."""

    strict_addendum = _strict_addendum(addendum)
    decisions = (_strict_segment(loco_decision), _strict_segment(lectra_decision))
    if tuple(item.corpus_id for item in decisions) != ("loco-2dics", "lectra-m3-m4"):
        raise ValueError("cross-segment reducer requires LOCo then Lectra decisions")
    if any(
        (item.addendum_id, item.addendum_content_sha256)
        != (strict_addendum.addendum_id, strict_addendum.content_sha256)
        for item in decisions
    ):
        raise ValueError("cross-segment reducer decisions differ from the addendum")
    disposition, actions, terminal = _cross_segment_fields(decisions)
    semantic = {
        "schema_version": "yieldforge.m11-economic-cross-segment-decision.v1",
        "addendum_id": strict_addendum.addendum_id,
        "addendum_content_sha256": strict_addendum.content_sha256,
        "segment_decisions": tuple(item.model_dump(mode="json") for item in decisions),
        "global_disposition": disposition,
        "next_actions": actions,
        "terminal": terminal,
        "product_falsification_scope": (
            "current_modeled_value_not_proof_no_possible_algorithm_can_work"
        ),
        "productization_authorized": False,
        "bounded_pilot_authorized": False,
    }
    digest = semantic_sha256(semantic)
    return EconomicCrossSegmentDecision(
        decision_id=f"yfm11econglobal-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **(semantic | {"segment_decisions": decisions}),
    )


__all__ = [
    "BASE_ECONOMIC_RESOLUTION_PROTOCOL_CONTENT_SHA256",
    "BASE_ECONOMIC_RESOLUTION_PROTOCOL_ID",
    "EconomicCandidateClassification",
    "EconomicCorpusId",
    "EconomicCrossSegmentDecision",
    "EconomicDecisionAddendum",
    "EconomicGlobalDisposition",
    "EconomicNextAction",
    "EconomicSegmentDecision",
    "LocoEconomicNextStep",
    "build_economic_decision_addendum",
    "build_economic_segment_decision",
    "reduce_economic_segment_decisions",
]
