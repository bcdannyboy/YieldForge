from __future__ import annotations

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.economic_resolution import (
    build_economic_resolution_protocol,
)

SUMMARY_ID = "yfm11econsummary-" + "a" * 24
SUMMARY_SHA = "sha256:" + "b" * 64


def _addendum():  # type: ignore[no-untyped-def]
    from yieldforge.realistic_falsification import economic_decision as decision

    return decision.build_economic_decision_addendum(
        base_protocol=build_economic_resolution_protocol()
    )


def _segment(
    *,
    corpus_id: str = "loco-2dics",
    f_green: bool = True,
    k_green: bool = True,
    unknown_green: bool = True,
    **updates,
):  # type: ignore[no-untyped-def]
    from yieldforge.realistic_falsification import economic_decision as decision

    values = {
        "f_mean_savings_percent": "2.500000000000" if f_green else "2.499999999999",
        "f_mean_ci_lower_percent": "0.100000000000",
        "f_median_savings_percent": "0.100000000000",
        "f_positive_stream_fraction": "0.500000000001",
        "unknown_headroom_mean_percentage_points": (
            "1.500000000000" if unknown_green else "1.499999999999"
        ),
        "k_mean_savings_percent": "1.500000000000" if k_green else "1.499999999999",
        "k_mean_ci_lower_percent": "0.100000000000",
        "k_median_savings_percent": "0.100000000000",
        "k_positive_stream_fraction": "0.500000000001",
    }
    values.update(updates)
    return decision.build_economic_segment_decision(
        addendum=_addendum(),
        corpus_id=corpus_id,
        source_summary_id=SUMMARY_ID,
        source_summary_content_sha256=SUMMARY_SHA,
        **values,
    )


def test_outcome_blind_addendum_binds_base_and_freezes_corrected_rules() -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    base = build_economic_resolution_protocol()
    addendum = decision.build_economic_decision_addendum(base_protocol=base)

    assert (addendum.base_protocol_id, addendum.base_protocol_content_sha256) == (
        base.protocol_id,
        base.content_sha256,
    )
    assert addendum.supersession_scope == "central_economic_interpretation_only"
    assert addendum.calibration_evidence_revalidation_required is False
    assert addendum.outcomes_opened_at_registration is False
    assert addendum.bootstrap_generator == "numpy.Generator(PCG64(0))"
    assert addendum.bootstrap_resamples == 10_000
    assert addendum.bootstrap_resampling_unit == "paired_stream"
    assert addendum.bootstrap_quantile_method == "linear_type_7"
    assert addendum.bootstrap_confidence_level == "0.950000000000"
    assert addendum.bootstrap_lower_quantile == "0.025000000000"
    assert addendum.bootstrap_upper_quantile == "0.975000000000"
    assert addendum.f_mean_savings_min_percent == "2.500000000000"
    assert addendum.unknown_headroom_mean_min_percentage_points == "1.500000000000"
    assert addendum.unknown_headroom_role == "diagnostic_only_nonveto"
    assert addendum.k_mean_savings_min_percent == "1.500000000000"
    assert addendum.lower_confidence_bound_rule == "strictly_greater_than_zero"
    assert addendum.median_savings_rule == "strictly_greater_than_zero"
    assert addendum.positive_stream_fraction_rule == "strictly_greater_than_one_half"
    assert addendum.f_proven_upper_bound_of_k is False
    assert addendum.f_alone_may_abandon is False
    assert addendum.segment_candidate_rule == (
        "k_green_then_causal_else_f_green_then_forecast_else_current_segment_red"
    )
    assert addendum.loco_branch_rule == ("causal_adverse_else_forecast_branch_else_lectra_screen")
    assert addendum.cross_segment_rule == (
        "any_causal_adverse_for_each_else_any_forecast_for_each_else_both_red_insufficient"
    )
    assert addendum.global_insufficient_rule == "both_segments_current_red_only"
    assert addendum.productization_authorized is False
    assert addendum.positive_result_ceiling == (
        "bounded_pilot_candidate_only_after_adverse_or_deployable_confirmation"
    )


def test_addendum_identity_and_base_binding_reject_mutation() -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    addendum = decision.build_economic_decision_addendum(
        base_protocol=build_economic_resolution_protocol()
    )

    for update, match in (
        ({"addendum_id": "yfm11econdec-" + "0" * 24}, "identity"),
        ({"base_protocol_content_sha256": "sha256:" + "0" * 64}, "base protocol"),
    ):
        forged = addendum.model_copy(update=update)
        with pytest.raises(ValidationError, match=match):
            decision.EconomicDecisionAddendum.model_validate(
                forged.model_dump(mode="python", round_trip=True),
                strict=True,
            )


@pytest.mark.parametrize("f_green", (False, True))
@pytest.mark.parametrize("k_green", (False, True))
@pytest.mark.parametrize("unknown_green", (False, True))
def test_segment_truth_table_keeps_unknown_diagnostic_only(
    f_green: bool,
    k_green: bool,
    unknown_green: bool,
) -> None:
    segment = _segment(f_green=f_green, k_green=k_green, unknown_green=unknown_green)

    expected_candidate = (
        "causal_candidate"
        if k_green
        else "forecast_candidate"
        if f_green
        else "current_segment_red"
    )
    expected_next_step = {
        "causal_candidate": "CONTINUE_ADVERSE_LOCO",
        "forecast_candidate": "CONTINUE_FORECAST_LOCO",
        "current_segment_red": "CONTINUE_LECTRA_SCREEN",
    }[expected_candidate]
    assert segment.f_economic_green is f_green
    assert segment.unknown_headroom_diagnostic_green is unknown_green
    assert segment.k_causal_green is k_green
    assert segment.candidate_classification == expected_candidate
    assert segment.loco_next_step == expected_next_step
    assert segment.global_product_decision_terminal is False
    assert segment.f_proven_upper_bound_of_k is False
    assert segment.productization_authorized is False
    assert segment.bounded_pilot_authorized is False


@pytest.mark.parametrize(
    ("updates", "expected_flag", "expected_candidate"),
    (
        ({"f_mean_savings_percent": "2.500000000000"}, "f_mean_passes", "forecast_candidate"),
        ({"f_mean_ci_lower_percent": "0.000000000000"}, "f_lcb_passes", "current_segment_red"),
        ({"f_median_savings_percent": "0.000000000000"}, "f_median_passes", "current_segment_red"),
        (
            {"f_positive_stream_fraction": "0.500000000000"},
            "f_positive_fraction_passes",
            "current_segment_red",
        ),
        ({"k_mean_savings_percent": "1.500000000000"}, "k_mean_passes", "causal_candidate"),
        ({"k_mean_ci_lower_percent": "0.000000000000"}, "k_lcb_passes", "forecast_candidate"),
        ({"k_median_savings_percent": "0.000000000000"}, "k_median_passes", "forecast_candidate"),
        (
            {"k_positive_stream_fraction": "0.500000000000"},
            "k_positive_fraction_passes",
            "forecast_candidate",
        ),
    ),
)
def test_mean_boundaries_are_inclusive_and_other_gate_boundaries_are_strict(
    updates: dict[str, str],
    expected_flag: str,
    expected_candidate: str,
) -> None:
    base = {
        "f_mean_savings_percent": "2.500000000000",
        "f_mean_ci_lower_percent": "0.100000000000",
        "f_median_savings_percent": "0.100000000000",
        "f_positive_stream_fraction": "0.500000000001",
        "k_mean_savings_percent": "1.500000000000",
        "k_mean_ci_lower_percent": "0.100000000000",
        "k_median_savings_percent": "0.100000000000",
        "k_positive_stream_fraction": "0.500000000001",
    }
    base.update(updates)
    if expected_flag.startswith("f_"):
        base["k_mean_savings_percent"] = "1.499999999999"
    segment = _segment(**base)

    assert getattr(segment, expected_flag) is (expected_flag in {"f_mean_passes", "k_mean_passes"})
    assert segment.candidate_classification == expected_candidate


def test_unknown_equality_passes_diagnostic_but_unknown_never_vetoes_causal() -> None:
    at_threshold = _segment(unknown_headroom_mean_percentage_points="1.500000000000")
    below_threshold_causal = _segment(
        f_green=False,
        k_green=True,
        unknown_headroom_mean_percentage_points="-100.000000000000",
    )

    assert at_threshold.unknown_headroom_diagnostic_green is True
    assert below_threshold_causal.unknown_headroom_diagnostic_green is False
    assert below_threshold_causal.candidate_classification == "causal_candidate"
    assert below_threshold_causal.loco_next_step == "CONTINUE_ADVERSE_LOCO"


def test_loco_red_is_a_nonterminal_lectra_screen_branch() -> None:
    segment = _segment(f_green=False, k_green=False)

    assert segment.candidate_classification == "current_segment_red"
    assert segment.loco_next_step == "CONTINUE_LECTRA_SCREEN"
    assert segment.global_product_decision_terminal is False


def test_segment_rejects_resigned_flags_disposition_and_dominance_claim() -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    segment = _segment(f_green=False, k_green=True, unknown_green=False)
    contradictions = (
        {"k_causal_green": False},
        {"candidate_classification": "current_segment_red"},
        {"loco_next_step": "CONTINUE_LECTRA_SCREEN"},
        {"f_proven_upper_bound_of_k": True},
    )
    for update in contradictions:
        draft = segment.model_copy(update=update)
        semantic = draft.model_dump(
            mode="json",
            exclude={"decision_id", "content_sha256"},
        )
        digest = semantic_sha256(semantic)
        resigned = draft.model_copy(
            update={
                "decision_id": f"yfm11econseg-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            }
        )
        with pytest.raises(ValidationError):
            decision.EconomicSegmentDecision.model_validate(
                resigned.model_dump(mode="python", round_trip=True),
                strict=True,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("f_mean_savings_percent", "NaN"),
        ("f_mean_savings_percent", "1.0"),
        ("f_mean_savings_percent", "1000000.000000000001"),
        ("f_positive_stream_fraction", "-0.000000000001"),
        ("f_positive_stream_fraction", "1.000000000001"),
        ("k_mean_ci_lower_percent", "2.000000000000"),
    ),
)
def test_segment_decimal_inputs_are_canonical_and_bounded(field: str, value: str) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        _segment(**{field: value})


def test_segment_identity_rejects_mutation() -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    segment = _segment()
    forged = segment.model_copy(update={"decision_id": "yfm11econseg-" + "0" * 24})

    with pytest.raises(ValidationError, match="identity"):
        decision.EconomicSegmentDecision.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )


@pytest.mark.parametrize(
    ("loco_candidate", "lectra_candidate"),
    tuple(
        (loco, lectra)
        for loco in ("causal_candidate", "forecast_candidate", "current_segment_red")
        for lectra in ("causal_candidate", "forecast_candidate", "current_segment_red")
    ),
)
def test_cross_segment_reducer_truth_table(
    loco_candidate: str,
    lectra_candidate: str,
) -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    def build(corpus_id: str, candidate: str):  # type: ignore[no-untyped-def]
        return _segment(
            corpus_id=corpus_id,
            f_green=candidate == "forecast_candidate",
            k_green=candidate == "causal_candidate",
        )

    loco = build("loco-2dics", loco_candidate)
    lectra = build("lectra-m3-m4", lectra_candidate)
    reduced = decision.reduce_economic_segment_decisions(
        addendum=_addendum(),
        loco_decision=loco,
        lectra_decision=lectra,
    )

    candidates = {
        "loco-2dics": loco_candidate,
        "lectra-m3-m4": lectra_candidate,
    }
    causal = tuple(
        f"CONTINUE_ADVERSE_{'LOCO' if corpus == 'loco-2dics' else 'LECTRA'}"
        for corpus, candidate in candidates.items()
        if candidate == "causal_candidate"
    )
    forecast = tuple(
        f"CONTINUE_FORECAST_{'LOCO' if corpus == 'loco-2dics' else 'LECTRA'}"
        for corpus, candidate in candidates.items()
        if candidate == "forecast_candidate"
    )
    if causal:
        assert reduced.global_disposition == "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION"
        assert reduced.next_actions == causal
        assert reduced.terminal is False
    elif forecast:
        assert reduced.global_disposition == "CONTINUE_FORECAST_SEGMENT_CONFIRMATION"
        assert reduced.next_actions == forecast
        assert reduced.terminal is False
    else:
        assert reduced.global_disposition == "INSUFFICIENT_CURRENT_MODELED_VALUE"
        assert reduced.next_actions == ()
        assert reduced.terminal is True
    assert reduced.product_falsification_scope == (
        "current_modeled_value_not_proof_no_possible_algorithm_can_work"
    )
    assert reduced.productization_authorized is False
    assert reduced.bounded_pilot_authorized is False


def test_cross_segment_reducer_rejects_resigned_contradictions_and_wrong_order() -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    loco = _segment(corpus_id="loco-2dics", f_green=False, k_green=False)
    lectra = _segment(corpus_id="lectra-m3-m4", f_green=False, k_green=False)
    reduced = decision.reduce_economic_segment_decisions(
        addendum=_addendum(),
        loco_decision=loco,
        lectra_decision=lectra,
    )

    contradictions = (
        {"global_disposition": "CONTINUE_FORECAST_SEGMENT_CONFIRMATION"},
        {"next_actions": ("CONTINUE_FORECAST_LOCO",)},
        {"terminal": False},
        {"segment_decisions": (lectra, loco)},
    )
    for updates in contradictions:
        draft = reduced.model_copy(update=updates)
        semantic = draft.model_dump(
            mode="json",
            exclude={"decision_id", "content_sha256"},
        )
        digest = semantic_sha256(semantic)
        resigned = draft.model_copy(
            update={
                "decision_id": f"yfm11econglobal-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            }
        )
        with pytest.raises(ValidationError):
            decision.EconomicCrossSegmentDecision.model_validate(
                resigned.model_dump(mode="python", round_trip=True),
                strict=True,
            )


def test_cross_segment_model_revalidates_nested_decision_instances() -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    loco = _segment(corpus_id="loco-2dics", f_green=False, k_green=True)
    lectra = _segment(corpus_id="lectra-m3-m4", f_green=False, k_green=False)
    valid = decision.reduce_economic_segment_decisions(
        addendum=_addendum(),
        loco_decision=loco,
        lectra_decision=lectra,
    )
    forged_loco = loco.model_copy(
        update={
            "candidate_classification": "current_segment_red",
            "loco_next_step": "CONTINUE_LECTRA_SCREEN",
        }
    )
    draft = valid.model_copy(
        update={
            "segment_decisions": (forged_loco, lectra),
            "global_disposition": "INSUFFICIENT_CURRENT_MODELED_VALUE",
            "next_actions": (),
            "terminal": True,
        }
    )
    semantic = draft.model_dump(mode="json", exclude={"decision_id", "content_sha256"})
    digest = semantic_sha256(semantic)
    payload = {field: getattr(draft, field) for field in type(draft).model_fields} | {
        "decision_id": f"yfm11econglobal-{digest[:24]}",
        "content_sha256": f"sha256:{digest}",
    }

    with pytest.raises(ValidationError):
        decision.EconomicCrossSegmentDecision.model_validate(payload, strict=True)


def test_cross_segment_identity_rejects_mutation() -> None:
    from yieldforge.realistic_falsification import economic_decision as decision

    reduced = decision.reduce_economic_segment_decisions(
        addendum=_addendum(),
        loco_decision=_segment(corpus_id="loco-2dics"),
        lectra_decision=_segment(corpus_id="lectra-m3-m4"),
    )
    forged = reduced.model_copy(update={"decision_id": "yfm11econglobal-" + "0" * 24})

    with pytest.raises(ValidationError, match="identity"):
        decision.EconomicCrossSegmentDecision.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )
