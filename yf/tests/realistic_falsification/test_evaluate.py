from __future__ import annotations

import importlib
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.bounds import Gate1StreamCell, load_official_gate1_context
from yieldforge.realistic_falsification.contracts import (
    M11EvidenceState,
    M11InvalidReasonCategory,
    M11VerdictAction,
    build_m11_verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _evaluate():
    try:
        return importlib.import_module("yieldforge.realistic_falsification.evaluate")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 5 evaluator module is missing: {error}")


def _statistics():
    return importlib.import_module("yieldforge.realistic_falsification.statistics")


def _bounds():
    return importlib.import_module("yieldforge.realistic_falsification.bounds")


@pytest.fixture(scope="module")
def official_context():
    return load_official_gate1_context(REPO_ROOT)


@pytest.fixture(scope="module")
def official_selections(official_context):
    bounds = _bounds()
    return tuple(
        bounds.select_gate1_baseline_policy(official_context, corpus_id)
        for corpus_id in ("lectra-m3-m4", "loco-2dics")
    )


def _synthetic_confirmation_cells(
    context,
    selections,
    *,
    lower_ratio: Fraction,
):
    bounds = _bounds()
    stream_by_id = {item.stream_id: item for item in context.bundle.population.streams}
    selection_by_corpus = {item.corpus_id: item for item in selections}
    expected_ids = tuple(
        stream_id
        for corpus in context.bundle.contract.corpora
        for stream_id in corpus.confirmation_stream_ids
    )
    cells = []
    for index, stream_id in enumerate(expected_ids):
        stream = stream_by_id[stream_id]
        selection = selection_by_corpus[stream.corpus_id]
        event_id = f"gate1-result-fixture-event-{index}"
        demand = bounds.build_gate1_demand_record(
            event_position=0,
            event_id=event_id,
            geometry_reference_id=f"gate1-result-fixture-geometry-{index}",
            geometry_sha256=f"{index + 1:064x}",
            source_binding_sha256="sha256:" + "b" * 64,
            source_kind="tiny",
            source_instance=None,
            material_group="fixture-material",
            reference_area_key="fixture-material",
            unit_area=lower_ratio,
            quantity=1,
            reference_area=Fraction(1),
        )
        lower = bounds.calculate_relaxed_lower_bound(
            stream_id=stream_id,
            demands=(demand,),
        )
        opening = bounds.build_gate1_feasible_opening(
            event_position=0,
            event_id=event_id,
            payload_id=f"gate1-result-fixture-payload-{index}",
            material_group="fixture-material",
            reference_area_key="fixture-material",
            source_kind="tiny",
            candidate_options=((f"gate1-result-fixture-candidate-{index}", "sha256:" + "c" * 64),),
            selected_candidate_id=f"gate1-result-fixture-candidate-{index}",
            selection_rule="exhaustive_tiny_case",
            verification_kind="exhaustive_tiny_case",
            geometry_witness_sha256="sha256:" + "d" * 64,
            known_positions_at_release=(0,),
            stock_area=Fraction(1),
            reference_area=Fraction(1),
        )
        policies = tuple(
            bounds.build_gate1_feasible_policy_cost(
                stream_id=stream_id,
                policy_kind=kind,
                openings=(opening,),
                registered_policy_id=selection.selected_policy_id,
                calibration_selection_id=selection.selection_id,
                evidence_stage="confirmation_application",
            )
            for kind in ("baseline_as_of", "known_only")
        )
        cells.append(
            bounds.build_gate1_stream_cell_from_evidence(
                stream_id=stream_id,
                corpus_id=stream.corpus_id,
                lower_bound=lower,
                baseline=policies[0],
                known_only=policies[1],
            )
        )
    return tuple(cells)


def _build_tiny_audit_fixture(*, identity_prefix: str = ""):
    bounds = _bounds()
    event_id = f"{identity_prefix}tiny-event-0"
    material_group = f"{identity_prefix}material-a"
    stream_id = f"{identity_prefix}tiny-stream"
    demand = bounds.build_gate1_demand_record(
        event_position=0,
        event_id=event_id,
        geometry_reference_id=f"{identity_prefix}tiny-shape-0",
        geometry_sha256=("e" if identity_prefix else "a") * 64,
        source_binding_sha256="sha256:" + "b" * 64,
        source_kind="tiny",
        source_instance=None,
        material_group=material_group,
        reference_area_key=material_group,
        unit_area=Fraction(6),
        quantity=1,
        reference_area=Fraction(10),
    )
    lower = bounds.calculate_relaxed_lower_bound(stream_id=stream_id, demands=(demand,))
    opening = bounds.build_gate1_feasible_opening(
        event_position=0,
        event_id=event_id,
        payload_id=f"{identity_prefix}tiny-payload-0",
        material_group=material_group,
        reference_area_key=material_group,
        source_kind="tiny",
        candidate_options=((f"{identity_prefix}tiny-candidate-0", "sha256:" + "c" * 64),),
        selected_candidate_id=f"{identity_prefix}tiny-candidate-0",
        selection_rule="exhaustive_tiny_case",
        verification_kind="exhaustive_tiny_case",
        geometry_witness_sha256="sha256:" + ("f" if identity_prefix else "d") * 64,
        known_positions_at_release=(0,),
        stock_area=Fraction(11, 10),
        reference_area=Fraction(1),
    )
    policies = tuple(
        bounds.build_gate1_feasible_policy_cost(
            stream_id=stream_id,
            policy_kind=kind,
            openings=(opening,),
        )
        for kind in ("baseline_as_of", "known_only")
    )
    cell = bounds.build_gate1_stream_cell_from_evidence(
        stream_id=stream_id,
        corpus_id="tiny",
        lower_bound=lower,
        baseline=policies[0],
        known_only=policies[1],
    )
    return bounds.audit_tiny_gate1_bounds(
        cell,
        problem=bounds.build_preregistered_gate1_tiny_problem(),
    )


@pytest.fixture(scope="module")
def tiny_audit():
    return _build_tiny_audit_fixture()


@pytest.fixture(scope="module")
def falsified_cells(official_context, official_selections):
    return _synthetic_confirmation_cells(
        official_context,
        official_selections,
        lower_ratio=Fraction(1),
    )


@pytest.fixture(scope="module")
def surviving_cells(official_context, official_selections):
    return _synthetic_confirmation_cells(
        official_context,
        official_selections,
        lower_ratio=Fraction(197, 200),
    )


def _metric(savings: str, unknown: str):
    return _statistics().Gate1CellMetricPair(
        savings_percent=Decimal(savings),
        unknown_contribution_points=Decimal(unknown),
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


def _install_fake_official_session(
    monkeypatch,
    evaluate,
    *,
    context,
    selections,
    cells,
):
    cells_by_stream = {item.stream_id: item for item in cells}
    calls = {"repository_roots": [], "stream_ids": []}

    class FakeSession:
        def __init__(self) -> None:
            self.context = context
            self.baseline_selections = selections

        def build_stream_cell(self, stream_id):
            calls["stream_ids"].append(stream_id)
            return cells_by_stream[stream_id]

    def open_session(repository_root):
        calls["repository_roots"].append(repository_root)
        return FakeSession()

    monkeypatch.setattr(evaluate, "_open_official_gate1_session", open_session)
    return calls


def _forge_calibration_selection_and_bound_cells(
    *,
    context,
    selections,
    cells,
    tiny_audit,
):
    evaluate = _evaluate()
    bounds = _bounds()
    original_selection = selections[0]
    selection_payload = original_selection.model_dump(mode="python", round_trip=True)
    replacement_policy_id = next(
        policy_id
        for policy_id in selection_payload["eligible_policy_ids"]
        if policy_id != selection_payload["selected_policy_id"]
    )
    for score in selection_payload["policy_scores"]:
        per_stream_cost = 1.0 if score["policy_id"] == replacement_policy_id else 2.0
        score["calibration_stream_costs"] = tuple(
            (stream_id, per_stream_cost) for stream_id, _cost in score["calibration_stream_costs"]
        )
        score["total_cost_exact"] = str(
            len(score["calibration_stream_costs"]) * int(per_stream_cost)
        )
        score["total_cost"] = len(score["calibration_stream_costs"]) * per_stream_cost
        _rehash(score, id_field="score_id", prefix="yfm11bsc-")
    selection_payload["selected_policy_id"] = replacement_policy_id
    selection_payload["tied_lowest_policy_ids"] = (replacement_policy_id,)
    _rehash(selection_payload, id_field="selection_id", prefix="yfm11bs-")
    forged_selection = bounds.Gate1BaselineSelectionEvidence.model_validate(
        selection_payload,
        strict=True,
    )

    forged_cells = []
    for cell in cells:
        if cell.corpus_id != forged_selection.corpus_id:
            forged_cells.append(cell)
            continue
        cell_payload = cell.model_dump(mode="python", round_trip=True)
        for evidence_name in ("baseline", "known_only"):
            feasible = cell_payload[evidence_name]
            feasible["registered_policy_id"] = replacement_policy_id
            feasible["calibration_selection_id"] = forged_selection.selection_id
            _rehash(feasible, id_field="witness_id", prefix="yfm11fp-")
        _rehash(cell_payload, id_field="cell_id", prefix="yfm11g1-")
        forged_cells.append(bounds.Gate1StreamCell.model_validate(cell_payload, strict=True))

    forged_selections = (forged_selection, *selections[1:])
    return evaluate._build_gate1_valid_result(
        context=context,
        cells=tuple(forged_cells),
        baseline_selections=forged_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )


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


def test_falsified_branch_embeds_existing_abandon_verdict(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )

    assert result.status == "falsified_by_optimistic_ceiling"
    assert result.terminal is True
    assert result.opens_gate_2 is False
    assert result.retention_authorized is False
    assert result.verdict is not None
    assert result.verdict.evidence_state is M11EvidenceState.FALSIFIED_BY_OPTIMISTIC_CEILING
    assert result.verdict.action is M11VerdictAction.ABANDON
    assert result.audit_receipt.confirmation_cells == falsified_cells
    assert result.audit_receipt.baseline_selections == official_selections
    assert result.audit_receipt.tiny_audit == tiny_audit
    assert result.audit_receipt.cell_ids == tuple(item.cell_id for item in falsified_cells)
    assert result.observed_cell_ids == result.audit_receipt.cell_ids


def test_exact_zero_survival_is_intermediate_and_never_retention(
    official_context,
    official_selections,
    tiny_audit,
    surviving_cells,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=surviving_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
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


def test_invalid_repair_count_rejects_before_authentication_or_bootstrap(
    official_context,
    monkeypatch,
) -> None:
    evaluate = _evaluate()
    calls = {"auth": 0, "bootstrap": 0}

    def forbidden_auth(*_args, **_kwargs):
        calls["auth"] += 1
        raise AssertionError("authentication must not run for an invalid repair count")

    def forbidden_bootstrap(*_args, **_kwargs):
        calls["bootstrap"] += 1
        raise AssertionError("bootstrap must not run for an invalid repair count")

    monkeypatch.setattr(evaluate, "_open_official_gate1_session", forbidden_auth)
    monkeypatch.setattr(evaluate, "_bootstrap_gate1_statistics", forbidden_bootstrap)

    with pytest.raises(ValueError, match="repair_count|repair count|0 or 1"):
        evaluate.evaluate_gate1_confirmation(
            context=official_context,
            cells=(),
            baseline_selections=(),
            tiny_audit=None,
            repair_count=2,
        )

    assert calls == {"auth": 0, "bootstrap": 0}


def test_evaluator_uses_one_session_and_routes_all_forty_cells_through_it(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
    monkeypatch,
) -> None:
    evaluate = _evaluate()
    cells_by_stream = {item.stream_id: item for item in falsified_cells}
    expected_ids = tuple(item.stream_id for item in falsified_cells)
    calls = {"open": 0, "build": []}

    class FakeSession:
        context = official_context
        baseline_selections = official_selections

        def build_stream_cell(self, stream_id):
            calls["build"].append(stream_id)
            return cells_by_stream[stream_id]

    def open_session(repository_root):
        assert repository_root == official_context.repository_root
        calls["open"] += 1
        return FakeSession()

    def forbidden_public_builder(*_args, **_kwargs):
        raise AssertionError("evaluator must not re-enter the fully authenticating public builder")

    monkeypatch.setattr(evaluate, "_open_official_gate1_session", open_session)
    monkeypatch.setattr(
        evaluate,
        "build_gate1_stream_cell",
        forbidden_public_builder,
        raising=False,
    )

    result = evaluate.evaluate_gate1_confirmation(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )

    assert calls == {"open": 1, "build": list(expected_ids)}
    assert result.status == "falsified_by_optimistic_ceiling"


def test_official_authenticator_reconstructs_and_exact_compares_all_evidence(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
    monkeypatch,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )
    calls = _install_fake_official_session(
        monkeypatch,
        evaluate,
        context=official_context,
        selections=official_selections,
        cells=falsified_cells,
    )

    authenticated = evaluate.authenticate_official_gate1_evaluation(
        result,
        repository_root=REPO_ROOT,
    )

    assert authenticated == result
    assert calls == {
        "repository_roots": [REPO_ROOT],
        "stream_ids": [item.stream_id for item in falsified_cells],
    }


def test_official_authenticator_rejects_all_forty_replaced_and_resigned_cells(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
    surviving_cells,
    monkeypatch,
) -> None:
    evaluate = _evaluate()
    forged = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=surviving_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )
    assert (
        evaluate.Gate1EvaluationResult.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        == forged
    )
    calls = _install_fake_official_session(
        monkeypatch,
        evaluate,
        context=official_context,
        selections=official_selections,
        cells=falsified_cells,
    )

    with pytest.raises(ValueError, match="official|canonical|reconstructed|differ"):
        evaluate.authenticate_official_gate1_evaluation(
            forged,
            repository_root=REPO_ROOT,
        )

    assert calls["repository_roots"] == [REPO_ROOT]
    assert calls["stream_ids"] == [item.stream_id for item in falsified_cells]


def test_official_authenticator_rejects_forged_selection_and_all_affected_cells(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
    monkeypatch,
) -> None:
    evaluate = _evaluate()
    forged = _forge_calibration_selection_and_bound_cells(
        context=official_context,
        selections=official_selections,
        cells=falsified_cells,
        tiny_audit=tiny_audit,
    )
    assert (
        evaluate.Gate1EvaluationResult.model_validate(
            forged.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        == forged
    )
    calls = _install_fake_official_session(
        monkeypatch,
        evaluate,
        context=official_context,
        selections=official_selections,
        cells=falsified_cells,
    )

    with pytest.raises(ValueError, match="official|canonical|reconstructed|differ"):
        evaluate.authenticate_official_gate1_evaluation(
            forged,
            repository_root=REPO_ROOT,
        )

    assert calls["repository_roots"] == [REPO_ROOT]
    assert calls["stream_ids"] == [item.stream_id for item in falsified_cells]


def test_official_authenticator_rejects_alternate_resigned_tiny_audit(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
    monkeypatch,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )
    alternate_tiny = _build_tiny_audit_fixture(identity_prefix="alternate-")
    assert alternate_tiny != tiny_audit
    payload = result.model_dump(mode="python", round_trip=True)
    payload["audit_receipt"]["tiny_audit"] = alternate_tiny.model_dump(
        mode="python",
        round_trip=True,
    )
    _rehash(payload["audit_receipt"], id_field="receipt_id", prefix="yfm11g1a-")
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    forged = evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)
    calls = _install_fake_official_session(
        monkeypatch,
        evaluate,
        context=official_context,
        selections=official_selections,
        cells=falsified_cells,
    )

    with pytest.raises(ValueError, match="official|canonical|reconstructed|differ"):
        evaluate.authenticate_official_gate1_evaluation(
            forged,
            repository_root=REPO_ROOT,
        )

    assert calls["repository_roots"] == [REPO_ROOT]
    assert calls["stream_ids"] == [item.stream_id for item in falsified_cells]


def test_result_config_receipt_and_roots_reject_resigned_tampering(
    official_context,
    official_selections,
    tiny_audit,
    surviving_cells,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=surviving_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
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
    swapped_cells = list(receipt["confirmation_cells"])
    swapped_cells[0], swapped_cells[1] = swapped_cells[1], swapped_cells[0]
    receipt["confirmation_cells"] = tuple(swapped_cells)
    _rehash(receipt, id_field="receipt_id", prefix="yfm11g1a-")
    with pytest.raises(ValidationError, match="cell|stream|corpus|regime|official"):
        evaluate.Gate1AuditReceipt.model_validate(receipt, strict=True)


def test_result_rejects_resigned_verdict_and_statistics_cross_binding(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
) -> None:
    evaluate = _evaluate()
    statistics = _statistics()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )

    mismatched_verdict = result.verdict.model_copy(update={"repair_count": 1})
    verdict_payload = mismatched_verdict.model_dump(mode="python", round_trip=True)
    _rehash(verdict_payload, id_field="result_id", prefix="yfm11r-")
    payload = result.model_dump(mode="python", round_trip=True)
    payload["verdict"] = verdict_payload
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    with pytest.raises(ValidationError, match="repair|verdict|binding|branch"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)

    with pytest.raises(ValueError, match="20|census"):
        statistics._bootstrap_gate1_statistics(
            (_metric("0", "0"),),
            (_metric("0", "0"),),
        )


def test_result_rejects_fully_resigned_falsified_to_survived_branch_flip(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )
    payload = result.model_dump(mode="python", round_trip=True)
    summary = payload["statistics"]
    summary["joint_upper_adverse_margin"] = 0.0
    summary["falsifies_optimistic_ceiling"] = False
    _rehash(summary, id_field="summary_id", prefix="yfm11g1s-")
    payload.update(
        status="gate_1_survived",
        terminal=False,
        opens_gate_2=True,
        verdict=None,
    )
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")

    with pytest.raises(ValidationError, match="evidence|recompute|statistics|branch"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)


def test_result_rejects_resigned_survived_to_falsified_branch_flip(
    official_context,
    official_selections,
    tiny_audit,
    surviving_cells,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=surviving_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )
    payload = result.model_dump(mode="python", round_trip=True)
    summary = payload["statistics"]
    summary["joint_upper_adverse_margin"] = -0.1
    summary["falsifies_optimistic_ceiling"] = True
    _rehash(summary, id_field="summary_id", prefix="yfm11g1s-")
    payload.update(
        status="falsified_by_optimistic_ceiling",
        terminal=True,
        opens_gate_2=False,
        verdict=build_m11_verdict(
            contract=official_context.bundle.contract,
            evidence_state=M11EvidenceState.FALSIFIED_BY_OPTIMISTIC_CEILING,
            repair_count=0,
        ).model_dump(mode="python", round_trip=True),
    )
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")

    with pytest.raises(ValidationError, match="evidence|recompute|statistics|branch"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)


def test_result_rejects_resigned_same_branch_statistics_and_nested_evidence(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )

    payload = result.model_dump(mode="python", round_trip=True)
    payload["statistics"]["groups"][0]["savings_mean_ci_lower"] = -0.1
    _rehash(payload["statistics"], id_field="summary_id", prefix="yfm11g1s-")
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    with pytest.raises(ValidationError, match="evidence|recompute|statistics"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)

    payload = result.model_dump(mode="python", round_trip=True)
    forged_cell = payload["audit_receipt"]["confirmation_cells"][0]
    forged_cell["ceiling_savings_percent"] = 99.0
    _rehash(forged_cell, id_field="cell_id", prefix="yfm11g1-")
    _rehash(payload["audit_receipt"], id_field="receipt_id", prefix="yfm11g1a-")
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    with pytest.raises(ValidationError, match="cell|ceiling|reconcile|evidence"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)

    payload = result.model_dump(mode="python", round_trip=True)
    selections = list(payload["audit_receipt"]["baseline_selections"])
    selections.reverse()
    payload["audit_receipt"]["baseline_selections"] = tuple(selections)
    _rehash(payload["audit_receipt"], id_field="receipt_id", prefix="yfm11g1a-")
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")
    with pytest.raises(ValidationError, match="selection|corpus|order|binding"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)


def test_result_rejects_resigned_cell_policy_different_from_frozen_selection(
    official_context,
    official_selections,
    tiny_audit,
    falsified_cells,
) -> None:
    evaluate = _evaluate()
    result = evaluate._build_gate1_valid_result(
        context=official_context,
        cells=falsified_cells,
        baseline_selections=official_selections,
        tiny_audit=tiny_audit,
        repair_count=0,
    )
    payload = result.model_dump(mode="python", round_trip=True)
    receipt = payload["audit_receipt"]
    selection = receipt["baseline_selections"][0]
    replacement_policy_id = next(
        policy_id
        for policy_id in selection["eligible_policy_ids"]
        if policy_id != selection["selected_policy_id"]
    )
    forged_cell = receipt["confirmation_cells"][0]
    for evidence_name in ("baseline", "known_only"):
        feasible = forged_cell[evidence_name]
        feasible["registered_policy_id"] = replacement_policy_id
        _rehash(feasible, id_field="witness_id", prefix="yfm11fp-")
    _rehash(forged_cell, id_field="cell_id", prefix="yfm11g1-")
    payload["observed_cell_ids"] = (
        forged_cell["cell_id"],
        *payload["observed_cell_ids"][1:],
    )
    _rehash(receipt, id_field="receipt_id", prefix="yfm11g1a-")
    _rehash(payload, id_field="result_id", prefix="yfm11g1r-")

    with pytest.raises(ValidationError, match="selection|policy|binding"):
        evaluate.Gate1EvaluationResult.model_validate(payload, strict=True)


def test_task5_public_api_is_exported_from_package() -> None:
    package = importlib.import_module("yieldforge.realistic_falsification")
    evaluate = _evaluate()

    assert (
        package.authenticate_official_gate1_evaluation
        is evaluate.authenticate_official_gate1_evaluation
    )
    assert package.Gate1EvaluationError is evaluate.Gate1EvaluationError
    assert evaluate.__all__ == [
        "Gate1EvaluationError",
        "authenticate_official_gate1_evaluation",
    ]
    for semantic_only_name in (
        "Gate1AuditReceipt",
        "Gate1EvaluationConfig",
        "Gate1EvaluationResult",
        "Gate1EvaluationStatus",
        "build_gate1_audit_receipt",
        "evaluate_gate1_confirmation",
    ):
        assert not hasattr(package, semantic_only_name)
    assert not hasattr(package, "bootstrap_gate1_statistics")
    assert not hasattr(package, "draw_gate1_bootstrap_indices")
