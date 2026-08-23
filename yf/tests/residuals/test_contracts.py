import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import M0ExperimentContract
from yieldforge.residuals.contracts import (
    CandidateResidualObservation,
    ResidualAccounting,
    ResidualComponentMetrics,
    ResidualGeometryConfig,
    ResidualRuleName,
    RuleClassificationSummary,
    rule_set_from_m0,
)

YF_ROOT = Path(__file__).parents[2]
M0_CONTRACT_PATH = YF_ROOT / "experiments" / "m0-contract-v1.json"


def _m0_contract() -> M0ExperimentContract:
    return M0ExperimentContract.model_validate_json(M0_CONTRACT_PATH.read_text(), strict=True)


def _accounting() -> ResidualAccounting:
    return ResidualAccounting(
        stock_area=100.0,
        placed_area=20.0,
        process_loss_area=2.0,
        forbidden_loss_area=3.0,
        residual_area=75.0,
        reconciliation_delta=0.0,
        area_tolerance=1e-7,
    )


def test_geometry_config_is_explicit_and_rejects_invalid_polygons() -> None:
    config = ResidualGeometryConfig()

    assert config.process_model == "explicit_symmetric_part_buffer"
    assert config.buffer_join_style == "mitre"
    assert config.part_buffer_distance == 0.0
    assert config.forbidden_polygons == ()

    with pytest.raises(ValidationError):
        ResidualGeometryConfig(part_buffer_distance=-0.1)
    with pytest.raises(ValidationError, match="closed"):
        ResidualGeometryConfig(forbidden_polygons=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)),))
    with pytest.raises(ValidationError):
        ResidualGeometryConfig(
            forbidden_polygons=(((0.0, 0.0), (float("nan"), 0.0), (1.0, 1.0), (0.0, 0.0)),)
        )


def test_rule_set_preserves_the_three_frozen_m0_thresholds() -> None:
    rules = rule_set_from_m0(_m0_contract().remnant_eligibility)

    assert tuple(rule.rule_name for rule in rules.ordered()) == (
        ResidualRuleName.PERMISSIVE,
        ResidualRuleName.PRIMARY,
        ResidualRuleName.CONSERVATIVE,
    )
    assert rules.permissive.minimum_area_sheet_fraction == 0.0025
    assert rules.primary.minimum_effective_width_short_side_fraction == 0.02
    assert rules.conservative.minimum_exterior_access_short_side_fraction == 0.05
    assert all(rule.requires_exterior_connection for rule in rules.ordered())


def test_component_and_accounting_contracts_reject_unstable_evidence() -> None:
    component = ResidualComponentMetrics(
        component_sha256="a" * 64,
        area=75.0,
        bounds=(0.0, 0.0, 10.0, 10.0),
        hole_count=0,
        exterior_connected=True,
        exterior_access_length=10.0,
        effective_width_rule_names=(ResidualRuleName.PERMISSIVE, ResidualRuleName.PRIMARY),
    )
    classification = RuleClassificationSummary(
        rule_name=ResidualRuleName.PRIMARY,
        retained_component_sha256=(component.component_sha256,),
        scrap_component_sha256=(),
        retained_area=75.0,
        scrap_area=0.0,
        largest_retained_component_area=75.0,
    )

    observation = CandidateResidualObservation(
        candidate_id="candidate-a",
        valid=True,
        residual_sha256="b" * 64,
        accounting=_accounting(),
        components=(component,),
        classifications=(classification,),
    )

    assert observation.valid is True
    with pytest.raises(ValidationError, match="sorted and unique"):
        RuleClassificationSummary(
            rule_name=ResidualRuleName.PRIMARY,
            retained_component_sha256=("b" * 64, "a" * 64),
            scrap_component_sha256=(),
            retained_area=75.0,
            scrap_area=0.0,
            largest_retained_component_area=75.0,
        )
    with pytest.raises(ValidationError):
        ResidualAccounting(**(_accounting().model_dump() | {"placed_area": -1.0}))


def test_observation_validity_and_error_evidence_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="valid observation"):
        CandidateResidualObservation(
            candidate_id="candidate-a",
            valid=True,
            error_code="invalid_geometry",
            residual_sha256="b" * 64,
            accounting=_accounting(),
        )

    invalid = CandidateResidualObservation(
        candidate_id="candidate-a",
        valid=False,
        error_code="invalid_geometry",
    )
    assert json.loads(invalid.model_dump_json())["accounting"] is None

    with pytest.raises(ValidationError, match="invalid observation"):
        CandidateResidualObservation(
            candidate_id="candidate-a",
            valid=False,
            error_code="invalid_geometry",
            accounting=_accounting(),
        )
