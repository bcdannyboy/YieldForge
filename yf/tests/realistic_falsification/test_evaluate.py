from __future__ import annotations

import importlib
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.bounds import Gate1StreamCell, load_official_gate1_context
from yieldforge.realistic_falsification.contracts import (
    M11EvidenceState,
    M11InvalidReasonCategory,
    M11VerdictAction,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _evaluate():
    try:
        return importlib.import_module("yieldforge.realistic_falsification.evaluate")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 5 evaluator module is missing: {error}")


def _statistics():
    return importlib.import_module("yieldforge.realistic_falsification.statistics")


@pytest.fixture(scope="module")
def official_context():
    return load_official_gate1_context(REPO_ROOT)


def _metric(savings: str, unknown: str):
    return _statistics().Gate1CellMetricPair(
        savings_percent=Decimal(savings),
        unknown_contribution_points=Decimal(unknown),
    )


def _summary(savings: str, unknown: str):
    return _statistics().bootstrap_gate1_statistics(
        (_metric(savings, unknown),) * 20,
        (_metric(savings, unknown),) * 20,
    )


def _cell_ids() -> tuple[str, ...]:
    return tuple(f"yfm11g1-{index:024x}" for index in range(40))


def _receipt():
    evaluate = _evaluate()
    return evaluate.build_gate1_audit_receipt(
        cell_ids=_cell_ids(),
        selection_ids=("yfm11bs-" + "a" * 24, "yfm11bs-" + "b" * 24),
        tiny_audit_id="yfm11ta-" + "c" * 24,
    )


def _constructed_cells(context) -> tuple[Gate1StreamCell, ...]:
    streams = {item.stream_id: item for item in context.bundle.population.streams}
    expected_ids = tuple(
        stream_id
        for corpus in context.bundle.contract.corpora
        for stream_id in corpus.confirmation_stream_ids
    )
    return tuple(
        Gate1StreamCell.model_construct(
            cell_id=f"yfm11g1-{index:024x}",
            content_sha256="sha256:" + f"{index:064x}",
            stream_id=stream_id,
            corpus_id=streams[stream_id].corpus_id,
        )
        for index, stream_id in enumerate(expected_ids)
    )


def _rehash(payload: dict[str, object], *, id_field: str, prefix: str) -> None:
    semantic = dict(payload)
    semantic.pop(id_field)
    semantic.pop("content_sha256")
    digest = semantic_sha256(semantic)
    payload[id_field] = f"{prefix}{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"


def test_confirmation_census_is_exact_contract_order_and_five_per_regime(
    official_context,
) -> None:
    evaluate = _evaluate()
    streams = evaluate._require_confirmation_cell_census(
        official_context,
        _constructed_cells(official_context),
    )

    assert len(streams) == 40
    for corpus_id in ("lectra-m3-m4", "loco-2dics"):
        assert {
            regime: sum(item.corpus_id == corpus_id and item.regime == regime for item in streams)
            for regime in ("recurrent", "mixed", "high_mix", "regime_shift")
        } == {regime: 5 for regime in ("recurrent", "mixed", "high_mix", "regime_shift")}


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "duplicate", "calibration", "twin", "hard_null", "reordered"],
)
def test_confirmation_census_rejects_every_nonconfirmation_shape(
    official_context, mutation: str
) -> None:
    evaluate = _evaluate()
    cells = list(_constructed_cells(official_context))
    population = official_context.bundle.population
    if mutation == "missing":
        cells.pop()
    elif mutation == "extra":
        cells.append(cells[-1].model_copy(update={"cell_id": "yfm11g1-" + "f" * 24}))
    elif mutation == "duplicate":
        cells[1] = cells[0].model_copy(update={"cell_id": cells[1].cell_id})
    elif mutation == "calibration":
        stream_id = next(
            item.stream_id
            for item in population.streams
            if item.partition == "calibration" and item.stream_kind == "primary"
        )
        cells[0] = cells[0].model_copy(update={"stream_id": stream_id})
    elif mutation == "twin":
        stream_id = next(
            item.stream_id for item in population.streams if item.stream_kind == "shuffled_twin"
        )
        cells[0] = cells[0].model_copy(update={"stream_id": stream_id})
    elif mutation == "hard_null":
        cells[0] = cells[0].model_copy(update={"stream_id": population.hard_nulls[0].null_id})
    else:
        cells[0], cells[1] = cells[1], cells[0]

    with pytest.raises(ValueError, match="confirmation|census|order"):
        evaluate._require_confirmation_cell_census(official_context, tuple(cells))


@pytest.mark.parametrize(
    ("baseline", "known_only", "lower_bound"),
    [
        (1.234567, 1.0, 0.0),
        (1.2345678, 1.0, 0.0),
        (float("nan"), 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 2.0),
    ],
)
def test_cell_cost_extraction_requires_finite_canonical_six_place_bounds(
    baseline: float, known_only: float, lower_bound: float
) -> None:
    evaluate = _evaluate()
    cell = Gate1StreamCell.model_construct(
        baseline_feasible_cost=baseline,
        known_only_feasible_cost=known_only,
        lower_bound=type("Lower", (), {"lower_bound_cost": lower_bound})(),
    )
    if (baseline, known_only, lower_bound) == (1.234567, 1.0, 0.0):
        assert evaluate._cell_metric_pair(cell).savings_percent == Decimal(100)
    else:
        with pytest.raises(ValueError, match="finite|six|B > 0|L <="):
            evaluate._cell_metric_pair(cell)


def test_falsified_branch_embeds_existing_abandon_verdict(official_context) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cell_ids=_cell_ids(),
        audit_receipt=_receipt(),
        statistics=_summary("0", "0"),
        repair_count=0,
    )

    assert result.status == "falsified_by_optimistic_ceiling"
    assert result.terminal is True
    assert result.opens_gate_2 is False
    assert result.retention_authorized is False
    assert result.verdict is not None
    assert result.verdict.evidence_state is M11EvidenceState.FALSIFIED_BY_OPTIMISTIC_CEILING
    assert result.verdict.action is M11VerdictAction.ABANDON


def test_exact_zero_survival_is_intermediate_and_never_retention(official_context) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cell_ids=_cell_ids(),
        audit_receipt=_receipt(),
        statistics=_summary("1.5", "0.5"),
        repair_count=0,
    )

    assert result.status == "gate_1_survived"
    assert result.terminal is False
    assert result.opens_gate_2 is True
    assert result.retention_authorized is False
    assert result.productization_authorized is False
    assert result.verdict is None
    assert "gate_1_survived" not in {item.value for item in M11EvidenceState}


@pytest.mark.parametrize(
    ("category", "reason_code", "repair_count", "action"),
    [
        (
            M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT,
            "software_implementation_defect",
            0,
            M11VerdictAction.ONE_REPAIR_AND_RERUN,
        ),
        (
            M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT,
            "software_implementation_defect",
            1,
            M11VerdictAction.ABANDON,
        ),
        (
            M11InvalidReasonCategory.OTHER_VALIDITY_FAILURE,
            "control_failure",
            0,
            M11VerdictAction.ABANDON,
        ),
    ],
)
def test_invalid_branch_has_no_numeric_bootstrap_and_preserves_repair_semantics(
    official_context,
    category,
    reason_code: str,
    repair_count: int,
    action,
) -> None:
    result = _evaluate()._build_gate1_invalid_result(
        context=official_context,
        observed_cell_ids=(),
        category=category,
        reason_code=reason_code,
        repair_count=repair_count,
    )

    assert result.status == "invalid_test"
    assert result.terminal is True
    assert result.opens_gate_2 is False
    assert result.statistics is None
    assert result.audit_receipt is None
    assert result.verdict is not None
    assert result.verdict.evidence_state is M11EvidenceState.INVALID_TEST
    assert result.verdict.action is action


def test_missing_or_failed_audits_return_typed_invalid_without_dropping_rows(
    official_context,
) -> None:
    cells = _constructed_cells(official_context)

    result = _evaluate().evaluate_gate1_confirmation(
        context=official_context,
        cells=cells,
        baseline_selections=(),
        tiny_audit=None,
        repair_count=0,
    )

    assert result.status == "invalid_test"
    assert result.observed_cell_ids == tuple(item.cell_id for item in cells)
    assert len(result.observed_cell_ids) == 40
    assert result.statistics is None


def test_tampered_context_returns_typed_invalid(official_context) -> None:
    forged_population = official_context.bundle.population.model_copy(update={"root_seed": 0})
    forged_context = official_context._replace(
        bundle=official_context.bundle._replace(population=forged_population)
    )

    result = _evaluate().evaluate_gate1_confirmation(
        context=forged_context,
        cells=(),
        baseline_selections=(),
        tiny_audit=None,
        repair_count=0,
    )

    assert result.status == "invalid_test"
    assert result.verdict is not None
    assert result.verdict.invalid_reason is not None
    assert result.verdict.invalid_reason.reason_code == "source_lineage_failure"


def test_result_config_receipt_and_roots_reject_resigned_tampering(official_context) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cell_ids=_cell_ids(),
        audit_receipt=_receipt(),
        statistics=_summary("1.5", "0.5"),
        repair_count=0,
    )

    payload = result.model_dump(mode="python", round_trip=True)
    payload["population_id"] = "yfm11pop-" + "0" * 24
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    with pytest.raises(ValidationError, match="population_id"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)

    config = result.config.model_dump(mode="python", round_trip=True)
    config["bootstrap_seed"] = 1
    _rehash(config, id_field="config_id", prefix="yfm11g1c-")
    with pytest.raises(ValidationError, match="bootstrap_seed"):
        evaluate.Gate1EvaluationConfig.model_validate(config, strict=True)

    receipt = result.audit_receipt.model_dump(mode="python", round_trip=True)
    receipt["cell_ids"] = (receipt["cell_ids"][0],) * 40
    _rehash(receipt, id_field="receipt_id", prefix="yfm11g1a-")
    with pytest.raises(ValidationError, match="cell|unique"):
        evaluate.Gate1AuditReceipt.model_validate(receipt, strict=True)


def test_result_rejects_resigned_verdict_and_statistics_cross_binding(
    official_context,
) -> None:
    evaluate = _evaluate()
    statistics = _statistics()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cell_ids=_cell_ids(),
        audit_receipt=_receipt(),
        statistics=_summary("0", "0"),
        repair_count=0,
    )

    mismatched_verdict = result.verdict.model_copy(update={"repair_count": 1})
    verdict_payload = mismatched_verdict.model_dump(mode="python", round_trip=True)
    _rehash(verdict_payload, id_field="result_id", prefix="yfm11r-")
    payload = result.model_dump(mode="python", round_trip=True)
    payload["verdict"] = verdict_payload
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    with pytest.raises(ValidationError, match="repair|verdict|binding"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)

    wrong_census = statistics.bootstrap_gate1_statistics(
        (_metric("0", "0"),),
        (_metric("0", "0"),),
    )
    payload = result.model_dump(mode="python", round_trip=True)
    payload["statistics"] = wrong_census.model_dump(mode="python", round_trip=True)
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    with pytest.raises(ValidationError, match="census|stream_count|statistics"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)


def test_task5_public_api_is_exported_from_package() -> None:
    package = importlib.import_module("yieldforge.realistic_falsification")
    evaluate = _evaluate()

    assert package.Gate1EvaluationResult is evaluate.Gate1EvaluationResult
    assert package.bootstrap_gate1_statistics is _statistics().bootstrap_gate1_statistics
    assert package.evaluate_gate1_confirmation is evaluate.evaluate_gate1_confirmation
