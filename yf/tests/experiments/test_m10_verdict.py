from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from yieldforge.experiments.m10_verdict import (
    M10EvidenceSnapshot,
    M10MinimumInvestmentVerdict,
    M10ParentBinding,
    build_minimum_investment_verdict,
)

PARENT_ROLES = (
    "m0_contract",
    "m6_contract",
    "m6_population",
    "m7_evaluation",
    "m8_gate3",
    "m9_repair",
)


def _parent(role: str, position: int) -> M10ParentBinding:
    digit = format(position + 1, "x")
    return M10ParentBinding(
        role=role,
        repository_path=f"evidence/{role}.json",
        schema_version=f"yieldforge.{role}.v1",
        semantic_id=f"yf-{role}-{digit * 24}",
        content_sha256=f"sha256:{digit * 64}",
        raw_file_sha256=f"sha256:{digit * 64}",
    )


def _snapshot(**changes: object) -> M10EvidenceSnapshot:
    payload: dict[str, object] = {
        "parents": tuple(_parent(role, position) for position, role in enumerate(PARENT_ROLES)),
        "geometry_corpus_ids": ("lectra-7030786-v1.1",),
        "required_positive_geometry_corpus_count": 2,
        "chronology_provenance": "generated",
        "economics_provenance": "generated",
        "material_provenance": "assumed",
        "baseline_stream_count": 36,
        "baseline_repeat_count": 2,
        "baseline_repeat_identity_match": True,
        "m8_decision": "hold_performance",
        "oracle_evaluation_opened": False,
        "oracle_savings_percent": None,
        "unknown_future_contribution_percentage_points": None,
        "m9_decision": "pass_decision_feasibility",
    }
    payload.update(changes)
    return M10EvidenceSnapshot.model_validate(payload, strict=True)


def test_builder_derives_the_minimum_current_investment_verdict() -> None:
    evidence = _snapshot()

    result = build_minimum_investment_verdict(evidence)

    assert tuple(parent.role for parent in result.evidence.parents) == PARENT_ROLES
    assert result.evidence.geometry_corpus_ids == ("lectra-7030786-v1.1",)
    assert result.evidence.required_positive_geometry_corpus_count == 2
    assert result.formal_economic_band == "not_computed"
    assert result.formal_numeric_m10_complete is False
    assert result.investment_verdict == "acquire_real_manufacturer_history"
    assert result.productization_decision == "do_not_productize"
    assert result.additional_virtual_oracle_investment == "stop"
    assert result.roadmap_decision_complete is True
    assert result.green_eligible is False
    assert result.missing_formal_measurements == (
        "oracle_savings_percent",
        "unknown_future_contribution_percentage_points",
    )
    assert result.missing_required_controls == (
        "full_future_oracle_evaluation",
        "known_only_information_ablation",
        "no_signal_oracle_control",
        "terminal_value_evaluation_sensitivity",
        "remnant_eligibility_evaluation_sensitivity",
        "ordinary_vs_expanded_search_evaluation",
        "rollout_vs_beam_evaluation",
    )
    assert result.reopen_conditions == (
        "permissioned_real_manufacturer_chronology_and_remnant_history",
        "observed_material_identities_and_economically_meaningful_costs",
        "independent_second_geometry_corpus",
        "buyer_or_operator_owned_bounded_decision",
    )
    assert result.result_id == f"yfm10-{result.content_sha256[7:31]}"


@pytest.mark.parametrize(
    "changes",
    [
        {"oracle_savings_percent": 1.0},
        {"unknown_future_contribution_percentage_points": 0.5},
    ],
)
def test_snapshot_rejects_formal_oracle_metrics(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _snapshot(**changes)


def test_snapshot_rejects_a_noncurrent_geometry_corpus_census() -> None:
    with pytest.raises(ValidationError, match="exactly one geometry corpus"):
        _snapshot(geometry_corpus_ids=("lectra", "independent"))


@pytest.mark.parametrize(
    "changes",
    [
        {"baseline_stream_count": 35},
        {"baseline_repeat_count": 1},
        {"baseline_repeat_identity_match": False},
    ],
)
def test_snapshot_rejects_incomplete_or_unreproduced_m7(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _snapshot(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"m8_decision": "pass_performance"},
        {"oracle_evaluation_opened": True},
    ],
)
def test_snapshot_rejects_noncurrent_m8_state(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _snapshot(**changes)


def test_snapshot_rejects_nonpassing_m9_state() -> None:
    with pytest.raises(ValidationError):
        _snapshot(m9_decision="fail_search_gap")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reordered"])
def test_snapshot_rejects_parent_role_census_or_ordering(mutation: str) -> None:
    parents = list(_snapshot().parents)
    if mutation == "missing":
        parents.pop()
    elif mutation == "duplicate":
        parents[-1] = _parent("m8_gate3", 5)
    else:
        parents[0], parents[1] = parents[1], parents[0]

    with pytest.raises(ValidationError, match="parent roles"):
        _snapshot(parents=tuple(parents))


def test_strict_models_reject_extra_fields() -> None:
    parent_payload = _parent("m0_contract", 0).model_dump(mode="python")
    parent_payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        M10ParentBinding.model_validate(parent_payload, strict=True)

    snapshot_payload = _snapshot().model_dump(mode="python")
    snapshot_payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        M10EvidenceSnapshot.model_validate(snapshot_payload, strict=True)


def test_builder_does_not_accept_a_caller_supplied_verdict() -> None:
    builder: Any = build_minimum_investment_verdict

    with pytest.raises(TypeError):
        builder(_snapshot(), investment_verdict="stop")


@pytest.mark.parametrize("identity_field", ["result_id", "content_sha256"])
def test_result_rejects_a_forged_content_identity(identity_field: str) -> None:
    payload = build_minimum_investment_verdict(_snapshot()).model_dump(mode="python")
    payload[identity_field] = (
        "yfm10-" + "0" * 24
        if identity_field == "result_id"
        else "sha256:" + "0" * 64
    )

    with pytest.raises(ValidationError, match="identity does not match semantic content"):
        M10MinimumInvestmentVerdict.model_validate(payload, strict=True)
