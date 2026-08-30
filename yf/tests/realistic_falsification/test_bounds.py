from __future__ import annotations

import importlib
import math
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.realistic_falsification.pack import visible_event_positions

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bounds():
    try:
        return importlib.import_module("yieldforge.realistic_falsification.bounds")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 4 bounds module is missing: {error}")


def _demand(
    *,
    event_position: int = 0,
    event_id: str = "tiny-event-0",
    geometry_reference_id: str = "tiny-shape-0",
    material_group: str = "material-a",
    reference_area_key: str = "material-a",
    unit_area: Fraction = Fraction(1),
    quantity: int = 1,
    reference_area: Fraction = Fraction(1),
):
    bounds = _bounds()
    return bounds.build_gate1_demand_record(
        event_position=event_position,
        event_id=event_id,
        geometry_reference_id=geometry_reference_id,
        geometry_sha256="a" * 64,
        source_binding_sha256="sha256:" + "b" * 64,
        source_kind="tiny",
        source_instance=None,
        material_group=material_group,
        reference_area_key=reference_area_key,
        unit_area=unit_area,
        quantity=quantity,
        reference_area=reference_area,
    )


def _opening(
    *,
    event_position: int = 0,
    event_id: str = "tiny-event-0",
    material_group: str = "material-a",
    stock_area: Fraction = Fraction(1),
    reference_area: Fraction = Fraction(1),
):
    bounds = _bounds()
    return bounds.build_gate1_feasible_opening(
        event_position=event_position,
        event_id=event_id,
        payload_id=f"tiny-payload-{event_position}",
        material_group=material_group,
        reference_area_key=material_group,
        source_kind="tiny",
        candidate_options=((f"tiny-candidate-{event_position}", "sha256:" + "c" * 64),),
        selected_candidate_id=f"tiny-candidate-{event_position}",
        selection_rule="exhaustive_tiny_case",
        verification_kind="exhaustive_tiny_case",
        geometry_witness_sha256="sha256:" + "d" * 64,
        known_positions_at_release=tuple(range(event_position + 1)),
        stock_area=stock_area,
        reference_area=reference_area,
    )


def _policy(*, kind: str, openings):
    bounds = _bounds()
    return bounds.build_gate1_feasible_policy_cost(
        stream_id="tiny-stream",
        policy_kind=kind,
        openings=tuple(openings),
    )


def _cell(*, lower_bound, baseline, known_only):
    bounds = _bounds()
    return bounds.build_gate1_stream_cell_from_evidence(
        stream_id="tiny-stream",
        corpus_id="tiny",
        lower_bound=lower_bound,
        baseline=baseline,
        known_only=known_only,
    )


def test_zero_demand_has_a_zero_certified_lower_bound() -> None:
    bounds = _bounds()

    result = bounds.calculate_relaxed_lower_bound(stream_id="zero-stream", demands=())

    assert result.demand_records == ()
    assert result.material_subtotals == ()
    assert result.raw_cost_exact == "0"
    assert result.lower_bound_cost == 0.0


def test_task4_public_api_is_exported_from_the_package() -> None:
    package = importlib.import_module("yieldforge.realistic_falsification")

    assert package.Gate1LowerBound is _bounds().Gate1LowerBound
    assert package.build_gate1_stream_cell is _bounds().build_gate1_stream_cell
    assert package.build_gate1_tiny_problem is _bounds().build_gate1_tiny_problem
    assert package.select_gate1_baseline_policy is _bounds().select_gate1_baseline_policy
    assert package.audit_tiny_gate1_bounds is _bounds().audit_tiny_gate1_bounds
    assert package.verify_gate1_tiny_audit is _bounds().verify_gate1_tiny_audit


def test_one_fractional_item_preserves_exact_area_cost_and_rounds_down() -> None:
    bounds = _bounds()
    demand = _demand(unit_area=Fraction(1), reference_area=Fraction(3))

    result = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(demand,),
    )

    assert demand.unit_area_exact == "1"
    assert demand.total_area_exact == "1"
    assert result.material_subtotals[0].demand_area_exact == "1"
    assert result.material_subtotals[0].raw_cost_exact == "100/3"
    assert result.raw_cost_exact == "100/3"
    assert result.lower_bound_cost == 33.333333


def test_material_groups_are_isolated_and_inconsistent_reference_binding_fails() -> None:
    bounds = _bounds()
    first = _demand(unit_area=Fraction(1), reference_area=Fraction(10))
    second = _demand(
        event_position=1,
        event_id="tiny-event-1",
        geometry_reference_id="tiny-shape-1",
        material_group="material-b",
        reference_area_key="material-b",
        unit_area=Fraction(2),
        reference_area=Fraction(20),
    )

    isolated = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(first, second),
    )

    assert tuple(item.material_group for item in isolated.material_subtotals) == (
        "material-a",
        "material-b",
    )
    assert isolated.raw_cost_exact == "20"

    inconsistent = _demand(
        event_position=1,
        event_id="tiny-event-1",
        geometry_reference_id="tiny-shape-1",
        material_group="material-a",
        reference_area_key="different-stock-scale",
        reference_area=Fraction(11),
    )
    with pytest.raises(bounds.Gate1EvidenceError, match="material group.*reference area"):
        bounds.calculate_relaxed_lower_bound(
            stream_id="tiny-stream",
            demands=(first, inconsistent),
        )


def test_chronology_is_relaxed_without_changing_the_bound() -> None:
    bounds = _bounds()
    demands = tuple(
        _demand(
            event_position=index,
            event_id=f"tiny-event-{index}",
            geometry_reference_id=f"tiny-shape-{index}",
            unit_area=Fraction(index + 1, 7),
        )
        for index in range(3)
    )

    forward = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=demands,
    )
    reverse = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=tuple(reversed(demands)),
    )

    assert forward == reverse
    assert "chronology_relaxed" in forward.relaxation_assumptions


@pytest.mark.parametrize(
    "field,value",
    (
        ("scrap_and_terminal_credit", 0.01),
        ("return_handling", 0.01),
        ("retrieval_handling", 0.01),
        ("storage_per_reference_area_30_days", 0.01),
        ("process_loss_fraction", 0.01),
    ),
)
def test_registered_bound_rejects_credit_friction_and_process_loss(
    field: str,
    value: float,
) -> None:
    bounds = _bounds()

    with pytest.raises(bounds.Gate1EvidenceError, match="zero credit and friction"):
        bounds.calculate_relaxed_lower_bound(
            stream_id="tiny-stream",
            demands=(_demand(),),
            **{field: value},
        )


def test_every_favorable_relaxation_is_explicit_and_bound_is_monotone() -> None:
    bounds = _bounds()
    lower = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(_demand(unit_area=Fraction(6), reference_area=Fraction(10)),),
    )
    expected_relaxations = (
        "empty_initial_inventory",
        "fractional_virgin_stock",
        "geometry_relaxed",
        "stock_indivisibility_relaxed",
        "chronology_relaxed",
        "zero_process_loss",
        "zero_storage",
        "zero_return_handling",
        "zero_retrieval_handling",
        "perfect_inventory_identification",
        "zero_scrap_and_terminal_credit",
        "material_groups_preserved_no_contract_fungibility_declaration",
    )
    baseline = _policy(
        kind="baseline_as_of",
        openings=(_opening(stock_area=Fraction(11, 10)),),
    )
    known = _policy(kind="known_only", openings=(_opening(stock_area=Fraction(11, 10)),))
    cell = _cell(lower_bound=lower, baseline=baseline, known_only=known)
    problem = bounds.build_gate1_tiny_problem()

    assert lower.relaxation_assumptions == expected_relaxations
    assert lower.lower_bound_cost == 60.0
    assert lower.lower_bound_cost <= baseline.feasible_cost
    audit = bounds.audit_tiny_gate1_bounds(
        cell,
        problem=problem,
        expected_problem_root_sha256=problem.problem_root_sha256,
    )
    assert audit.all_inequalities_hold is True
    assert audit.relaxed_lower_bound_exact == "60"
    assert audit.exact_full_optimum == "78"
    assert audit.exact_known_optimum == "100"
    assert audit.baseline_feasible_exact == "110"
    assert audit.known_feasible_exact == "110"
    assert audit.full_information_policy_evaluation_count == 176
    assert audit.truncated_count == 0
    assert tuple(item.relaxation for item in audit.relaxation_checks) == (
        bounds.GATE1_FAVORABLE_RELAXATIONS
    )
    assert all(
        Fraction(item.relaxed_optimum_exact) <= Fraction(item.constrained_optimum_exact)
        for item in audit.relaxation_checks
    )
    assert set(bounds.GATE1_FAVORABLE_RELAXATIONS).isdisjoint(bounds.GATE1_NON_LATTICE_ASSUMPTIONS)
    assert set(bounds.GATE1_FAVORABLE_RELAXATIONS) | set(
        bounds.GATE1_NON_LATTICE_ASSUMPTIONS
    ) == set(bounds.GATE1_RELAXATION_ASSUMPTIONS)


def test_downward_lower_bound_rounding_differs_from_realized_half_up_rounding() -> None:
    bounds = _bounds()
    value = Fraction(6_172_839, 5_000_000)

    assert bounds.round_down_cost(value) == 1.234567
    assert bounds.round_half_up_cost(value) == 1.234568


def test_feasible_cost_is_constructive_half_up_evidence_not_a_lower_bound() -> None:
    opening = _opening(stock_area=Fraction(6_172_839, 500_000_000))
    policy = _policy(kind="baseline_as_of", openings=(opening,))

    assert opening.raw_purchase_cost_exact == "6172839/5000000"
    assert opening.purchase_cost == 1.234568
    assert opening.geometry_witness_sha256 == "sha256:" + "d" * 64
    assert policy.evidence_role == "verified_constructive_feasible_cost_never_lower_bound"
    assert policy.feasible_cost == 1.234568


def test_known_only_uses_the_same_future_blind_algorithm_action_set_and_compute() -> None:
    lower = _bounds().calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(_demand(),),
    )
    openings = (_opening(),)
    baseline = _policy(kind="baseline_as_of", openings=openings)
    known = _policy(kind="known_only", openings=openings)

    cell = _cell(lower_bound=lower, baseline=baseline, known_only=known)

    assert baseline.algorithm == known.algorithm
    assert baseline.action_set_contract == known.action_set_contract
    assert baseline.compute_contract == known.compute_contract
    assert baseline.unknown_events_masked is False
    assert known.unknown_events_masked is True
    assert baseline.ignores_future is known.ignores_future is True
    assert cell.baseline_feasible_cost == cell.known_only_feasible_cost
    assert cell.known_only_equals_baseline is True


def test_mismatched_constructive_B_and_K_are_typed_invalid_evidence() -> None:
    bounds = _bounds()
    lower = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(_demand(),),
    )
    baseline = _policy(kind="baseline_as_of", openings=(_opening(),))
    different_opening = bounds.build_gate1_feasible_opening(
        event_position=0,
        event_id="tiny-event-0",
        payload_id="tiny-payload-0",
        material_group="material-a",
        reference_area_key="material-a",
        source_kind="tiny",
        candidate_options=(("different-candidate", "sha256:" + "e" * 64),),
        selected_candidate_id="different-candidate",
        selection_rule="exhaustive_tiny_case",
        verification_kind="exhaustive_tiny_case",
        geometry_witness_sha256="sha256:" + "f" * 64,
        known_positions_at_release=(0,),
        stock_area=Fraction(1),
        reference_area=Fraction(1),
    )
    known = _policy(kind="known_only", openings=(different_opening,))

    with pytest.raises(bounds.Gate1EvidenceError, match="same algorithm.*actions"):
        _cell(lower_bound=lower, baseline=baseline, known_only=known)


def test_tiny_exact_audit_rejects_wrong_cell_costs_without_caller_optima() -> None:
    bounds = _bounds()
    lower = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(_demand(unit_area=Fraction(6), reference_area=Fraction(10)),),
    )
    baseline = _policy(kind="baseline_as_of", openings=(_opening(),))
    known = _policy(kind="known_only", openings=(_opening(),))
    cell = _cell(lower_bound=lower, baseline=baseline, known_only=known)
    problem = bounds.build_gate1_tiny_problem()

    with pytest.raises(bounds.Gate1BoundAuditError, match="enumerated feasible baseline"):
        bounds.audit_tiny_gate1_bounds(
            cell,
            problem=problem,
            expected_problem_root_sha256=problem.problem_root_sha256,
        )


def test_tiny_audit_rejects_wrong_root_caller_optima_and_resigned_tampering() -> None:
    bounds = _bounds()
    lower = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(_demand(unit_area=Fraction(6), reference_area=Fraction(10)),),
    )
    openings = (_opening(stock_area=Fraction(11, 10)),)
    cell = _cell(
        lower_bound=lower,
        baseline=_policy(kind="baseline_as_of", openings=openings),
        known_only=_policy(kind="known_only", openings=openings),
    )
    problem = bounds.build_gate1_tiny_problem()
    audit = bounds.audit_tiny_gate1_bounds(
        cell,
        problem=problem,
        expected_problem_root_sha256=problem.problem_root_sha256,
    )

    with pytest.raises(bounds.Gate1EvidenceError, match="problem root"):
        bounds.audit_tiny_gate1_bounds(
            cell,
            problem=problem,
            expected_problem_root_sha256="sha256:" + "0" * 64,
        )
    with pytest.raises(TypeError):
        bounds.audit_tiny_gate1_bounds(
            cell,
            problem=problem,
            expected_problem_root_sha256=problem.problem_root_sha256,
            exact_full_optimum=Fraction(1),
        )

    payload = audit.model_dump(mode="python", round_trip=True)
    payload["exact_full_optimum"] = "79"
    semantic = {
        key: value for key, value in payload.items() if key not in {"audit_id", "content_sha256"}
    }
    identifier, content = bounds._identity("yfm11ta-", semantic)
    payload["audit_id"] = identifier
    payload["content_sha256"] = content
    with pytest.raises(ValidationError, match="enumerat|optimum|reconcile"):
        bounds.Gate1TinyAudit.model_validate(payload, strict=True)


@pytest.fixture(scope="module")
def official_context():
    return _bounds().load_official_gate1_context(REPO_ROOT)


@pytest.fixture(scope="module")
def official_baseline_selections(official_context):
    bounds = _bounds()
    return {
        corpus_id: bounds.select_gate1_baseline_policy(official_context, corpus_id)
        for corpus_id in ("lectra-m3-m4", "loco-2dics")
    }


def test_finite_baseline_registry_is_scored_only_on_eight_calibration_streams_per_corpus(
    official_context,
) -> None:
    bounds = _bounds()
    official_baseline_selections = {
        corpus_id: bounds.select_gate1_baseline_policy(official_context, corpus_id)
        for corpus_id in ("lectra-m3-m4", "loco-2dics")
    }
    assert tuple(item.policy_id for item in bounds.GATE1_BASELINE_POLICY_REGISTRY) == (
        "fresh-candidate-position-0",
        "fresh-candidate-position-1",
        "fresh-minimum-used-area",
    )

    for corpus_id, selection in official_baseline_selections.items():
        calibration_ids = {
            stream.stream_id
            for stream in official_context.bundle.population.streams
            if stream.corpus_id == corpus_id
            and stream.partition == "calibration"
            and stream.stream_kind == "primary"
        }
        assert len(calibration_ids) == 8
        assert set(selection.calibration_stream_ids) == calibration_ids
        assert selection.calibration_only is True
        assert selection.confirmation_inputs_used is False
        assert selection.strongest_scope == (
            "strongest_within_registered_feasible_as_of_time_family_not_universal_optimum"
        )
        assert selection.selected_policy_id == min(selection.tied_lowest_policy_ids)
        assert {item.total_cost for item in selection.policy_scores} == {
            selection.policy_scores[0].total_cost
        }

    assert official_baseline_selections["lectra-m3-m4"].tied_lowest_policy_ids == (
        "fresh-candidate-position-0",
        "fresh-candidate-position-1",
        "fresh-minimum-used-area",
    )
    assert official_baseline_selections["loco-2dics"].tied_lowest_policy_ids == (
        "fresh-candidate-position-0",
        "fresh-minimum-used-area",
    )


def test_loco_fallback_area_accepts_one_serialization_ulp_but_rejects_two(
    official_context,
) -> None:
    bounds = _bounds()
    payload = next(
        item
        for item in official_context.bundle.population.payloads
        if item.payload_id == "yfm11pl-34f4426471d586785e96979c"
    )
    fallback = payload.fallback_stock
    assert fallback is not None
    exact_float = float(Fraction(str(fallback.width)) * Fraction(str(fallback.height)))
    assert fallback.area != exact_float
    assert bounds._float_matches_within_one_ulp(fallback.area, exact_float)

    one_ulp = math.nextafter(exact_float, math.inf)
    two_ulps = math.nextafter(one_ulp, math.inf)
    assert not bounds._float_matches_within_one_ulp(exact_float, two_ulps)


def test_confirmation_mutation_cannot_change_calibration_policy_selection(
    official_context,
    official_baseline_selections,
) -> None:
    bounds = _bounds()
    original = official_baseline_selections["lectra-m3-m4"]
    confirmation = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "lectra-m3-m4" and item.partition == "confirmation"
    )
    mutated_confirmation = confirmation.model_copy(update={"content_sha256": "sha256:" + "f" * 64})
    mutated_population = official_context.bundle.population.model_copy(
        update={
            "streams": tuple(
                mutated_confirmation if item.stream_id == confirmation.stream_id else item
                for item in official_context.bundle.population.streams
            )
        }
    )
    mutated_context = official_context._replace(
        bundle=official_context.bundle._replace(population=mutated_population)
    )

    assert bounds.select_gate1_baseline_policy(mutated_context, "lectra-m3-m4") == original


def test_weaker_feasible_outer_comparator_cannot_understate_the_opportunity_ceiling() -> None:
    bounds = _bounds()

    assert bounds.verify_weaker_feasible_comparator_ceiling(
        lower_bound_cost=Fraction(60),
        strongest_registered_cost=Fraction(80),
        weaker_feasible_cost=Fraction(100),
    )
    with pytest.raises(bounds.Gate1EvidenceError, match="ordered feasible costs"):
        bounds.verify_weaker_feasible_comparator_ceiling(
            lower_bound_cost=Fraction(60),
            strongest_registered_cost=Fraction(100),
            weaker_feasible_cost=Fraction(80),
        )


def test_confirmation_cell_requires_matching_calibration_selection_evidence(
    official_context,
) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "lectra-m3-m4"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    with pytest.raises(bounds.Gate1EvidenceError, match="calibration selection evidence"):
        bounds.build_gate1_stream_cell(official_context, stream)


@pytest.mark.parametrize("corpus_id", ("lectra-m3-m4", "loco-2dics"))
def test_representative_official_stream_builds_a_complete_verified_cell(
    official_context,
    official_baseline_selections,
    corpus_id: str,
) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == corpus_id
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    selection = official_baseline_selections[corpus_id]
    cell = bounds.build_gate1_stream_cell(
        official_context,
        stream,
        baseline_selection=selection,
    )

    assert cell.stream_id == stream.stream_id
    assert cell.corpus_id == stream.corpus_id
    assert cell.lower_bound.event_ids == tuple(item.event_id for item in stream.events)
    assert len(cell.baseline.openings) == len(stream.events) == 24
    assert cell.baseline.openings == cell.known_only.openings
    assert cell.baseline_feasible_cost == cell.known_only_feasible_cost
    assert cell.baseline.registered_policy_id == selection.selected_policy_id
    assert cell.baseline.calibration_selection_id == selection.selection_id
    assert cell.lower_bound.lower_bound_cost <= cell.baseline_feasible_cost
    assert all(item.geometry_witness_sha256 for item in cell.baseline.openings)
    expected_kind = (
        "lectra_m3_candidate_geometry"
        if corpus_id == "lectra-m3-m4"
        else "loco_bbox_shelf_geometry"
    )
    assert {item.verification_kind for item in cell.baseline.openings} == {expected_kind}


def test_all_lectra_openings_charge_and_persist_the_full_pinned_sheet(official_context) -> None:
    bounds = _bounds()
    references = bounds._reference_registry(official_context)["lectra-m3-m4"]
    pairs = {item.tasks_index: item for item in official_context.m3_input.task_pairs}
    payloads = tuple(
        item for item in official_context.bundle.population.payloads if item.source_kind == "lectra"
    )

    assert len(payloads) == 203
    for payload in payloads:
        proof = bounds._validate_lectra_payload(official_context, payload, references)
        pair = pairs[int(payload.source_case_id.removeprefix("lectra-task:"))]
        purchased_width = Fraction(str(pair.problem.sheet_length))
        purchased_height = Fraction(str(pair.problem.strip_height))

        assert proof.selected_used_layout_width <= purchased_width
        assert proof.purchased_stock_width == purchased_width
        assert proof.purchased_stock_height == purchased_height
        assert proof.selected_stock_area == purchased_width * purchased_height


def test_representative_lectra_cell_persists_used_and_purchased_dimensions(
    official_context,
    official_baseline_selections,
) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "lectra-m3-m4"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    cell = bounds.build_gate1_stream_cell(
        official_context,
        stream,
        baseline_selection=official_baseline_selections["lectra-m3-m4"],
    )

    assert cell.baseline_feasible_cost == 2506.905288
    for opening in cell.baseline.openings:
        used_width = Fraction(opening.used_layout_width_exact)
        purchased_width = Fraction(opening.purchased_stock_width_exact)
        purchased_height = Fraction(opening.purchased_stock_height_exact)
        assert used_width <= purchased_width
        assert Fraction(opening.stock_area_exact) == purchased_width * purchased_height


def test_official_known_only_witnesses_bind_the_frozen_visible_prefix(
    official_context,
    official_baseline_selections,
) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "loco-2dics"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    cell = bounds.build_gate1_stream_cell(
        official_context,
        stream,
        baseline_selection=official_baseline_selections["loco-2dics"],
    )

    for event, opening in zip(stream.events, cell.known_only.openings, strict=True):
        assert opening.known_positions_at_release == visible_event_positions(
            stream,
            event.released_at,
        )
        assert event.position in opening.known_positions_at_release


def test_lectra_materials_are_not_collapsed_without_a_contract_declaration(
    official_context,
    official_baseline_selections,
) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "lectra-m3-m4"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    cell = bounds.build_gate1_stream_cell(
        official_context,
        stream,
        baseline_selection=official_baseline_selections["lectra-m3-m4"],
    )

    assert {item.material_group for item in cell.lower_bound.material_subtotals} == {
        item.material_key for item in stream.events
    }


def test_loco_bound_remains_isolated_by_source_instance_and_scale(
    official_context,
    official_baseline_selections,
) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "loco-2dics"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    cell = bounds.build_gate1_stream_cell(
        official_context,
        stream,
        baseline_selection=official_baseline_selections["loco-2dics"],
    )

    for subtotal in cell.lower_bound.material_subtotals:
        assert subtotal.material_group.startswith("loco:")
        assert subtotal.reference_area_key == subtotal.material_group
    assert len({item.reference_area_exact for item in cell.lower_bound.material_subtotals}) >= 1


def test_content_addressing_and_recomputed_fields_reject_tampering(
    official_context,
    official_baseline_selections,
) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "loco-2dics"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )
    cell = bounds.build_gate1_stream_cell(
        official_context,
        stream,
        baseline_selection=official_baseline_selections["loco-2dics"],
    )
    payload = cell.lower_bound.model_dump(mode="python", round_trip=True)
    payload["lower_bound_cost"] += 1.0

    with pytest.raises(ValidationError, match="lower-bound|identity|reconcile"):
        bounds.Gate1LowerBound.model_validate(payload, strict=True)
