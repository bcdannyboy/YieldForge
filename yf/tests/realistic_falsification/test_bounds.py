from __future__ import annotations

import importlib
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
    assert package.audit_tiny_gate1_bounds is _bounds().audit_tiny_gate1_bounds


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
    baseline = _policy(kind="baseline_as_of", openings=(_opening(stock_area=Fraction(10)),))
    known = _policy(kind="known_only", openings=(_opening(stock_area=Fraction(10)),))
    cell = _cell(lower_bound=lower, baseline=baseline, known_only=known)

    assert lower.relaxation_assumptions == expected_relaxations
    assert lower.lower_bound_cost == 60.0
    assert lower.lower_bound_cost <= baseline.feasible_cost
    audit = bounds.audit_tiny_gate1_bounds(
        cell,
        exact_full_optimum=Fraction(8, 10) * 100,
        exact_known_optimum=Fraction(9, 10) * 100,
    )
    assert audit.all_inequalities_hold is True


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


def test_tiny_exact_audit_rejects_any_bound_direction_violation() -> None:
    bounds = _bounds()
    lower = bounds.calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(_demand(unit_area=Fraction(6), reference_area=Fraction(10)),),
    )
    baseline = _policy(kind="baseline_as_of", openings=(_opening(),))
    known = _policy(kind="known_only", openings=(_opening(),))
    cell = _cell(lower_bound=lower, baseline=baseline, known_only=known)

    with pytest.raises(bounds.Gate1BoundAuditError, match="L_full <= exact_full_optimum"):
        bounds.audit_tiny_gate1_bounds(
            cell,
            exact_full_optimum=Fraction(59),
            exact_known_optimum=Fraction(90),
        )
    with pytest.raises(bounds.Gate1BoundAuditError, match="exact_known_optimum <= K_feasible"):
        bounds.audit_tiny_gate1_bounds(
            cell,
            exact_full_optimum=Fraction(80),
            exact_known_optimum=Fraction(101),
        )
    with pytest.raises(bounds.Gate1BoundAuditError, match="unknown-future gap"):
        bounds.audit_tiny_gate1_bounds(
            cell,
            exact_full_optimum=Fraction(60),
            exact_known_optimum=Fraction(101),
        )


@pytest.fixture(scope="module")
def official_context():
    return _bounds().load_official_gate1_context(REPO_ROOT)


@pytest.mark.parametrize("corpus_id", ("lectra-m3-m4", "loco-2dics"))
def test_representative_official_stream_builds_a_complete_verified_cell(
    official_context,
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

    cell = bounds.build_gate1_stream_cell(official_context, stream)

    assert cell.stream_id == stream.stream_id
    assert cell.corpus_id == stream.corpus_id
    assert cell.lower_bound.event_ids == tuple(item.event_id for item in stream.events)
    assert len(cell.baseline.openings) == len(stream.events) == 24
    assert cell.baseline.openings == cell.known_only.openings
    assert cell.baseline_feasible_cost == cell.known_only_feasible_cost
    assert cell.lower_bound.lower_bound_cost <= cell.baseline_feasible_cost
    assert all(item.geometry_witness_sha256 for item in cell.baseline.openings)
    expected_kind = (
        "lectra_m3_candidate_geometry"
        if corpus_id == "lectra-m3-m4"
        else "loco_bbox_shelf_geometry"
    )
    assert {item.verification_kind for item in cell.baseline.openings} == {expected_kind}


def test_official_known_only_witnesses_bind_the_frozen_visible_prefix(official_context) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "loco-2dics"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    cell = bounds.build_gate1_stream_cell(official_context, stream)

    for event, opening in zip(stream.events, cell.known_only.openings, strict=True):
        assert opening.known_positions_at_release == visible_event_positions(
            stream,
            event.released_at,
        )
        assert event.position in opening.known_positions_at_release


def test_lectra_materials_are_not_collapsed_without_a_contract_declaration(
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

    cell = bounds.build_gate1_stream_cell(official_context, stream)

    assert {item.material_group for item in cell.lower_bound.material_subtotals} == {
        item.material_key for item in stream.events
    }


def test_loco_bound_remains_isolated_by_source_instance_and_scale(official_context) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "loco-2dics"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )

    cell = bounds.build_gate1_stream_cell(official_context, stream)

    for subtotal in cell.lower_bound.material_subtotals:
        assert subtotal.material_group.startswith("loco:")
        assert subtotal.reference_area_key == subtotal.material_group
    assert len({item.reference_area_exact for item in cell.lower_bound.material_subtotals}) >= 1


def test_content_addressing_and_recomputed_fields_reject_tampering(official_context) -> None:
    bounds = _bounds()
    stream = next(
        item
        for item in official_context.bundle.population.streams
        if item.corpus_id == "loco-2dics"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )
    cell = bounds.build_gate1_stream_cell(official_context, stream)
    payload = cell.lower_bound.model_dump(mode="python", round_trip=True)
    payload["lower_bound_cost"] += 1.0

    with pytest.raises(ValidationError, match="lower-bound|identity|reconcile"):
        bounds.Gate1LowerBound.model_validate(payload, strict=True)
