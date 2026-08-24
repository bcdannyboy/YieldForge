from __future__ import annotations

from pathlib import Path

import pytest
from shapely import box

from yieldforge.baseline.actions import (
    build_remnant_action,
    build_standard_sheet_action,
)
from yieldforge.baseline.contracts import LayoutFitSearchConfig, M7ActionKind
from yieldforge.baseline.geometry import (
    generate_layout_translations,
    prepare_layout_footprint,
    prepare_remnant_geometry,
    search_layout_translation,
)
from yieldforge.domain import (
    Candidate,
    CandidateReportType,
    Part,
    Placement,
    StripPackingProblem,
)
from yieldforge.experiments.contracts import M0ExperimentContract, load_frozen_json
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import (
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    canonical_polygon_record,
    derive_remnant_id,
)


def _material() -> MaterialIdentity:
    return MaterialIdentity(
        material_code="m7-test",
        grade="grade",
        thickness="1",
        surface="plain",
        grain="none",
        provenance=MaterialProvenance.ASSUMED,
    )


def _rules():  # type: ignore[no-untyped-def]
    m0 = load_frozen_json(
        Path(__file__).parents[2] / "experiments/m0-contract-v1.json",
        M0ExperimentContract,
    )
    return rule_set_from_m0(m0.remnant_eligibility)


def _problem(*, two_parts: bool = False) -> StripPackingProblem:
    parts = [
        Part(
            id="a",
            shape=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
            demand=1,
            allowed_orientations=[0.0],
        )
    ]
    if two_parts:
        parts.append(
            Part(
                id="b",
                shape=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
                demand=1,
                allowed_orientations=[0.0],
            )
        )
    return StripPackingProblem(
        name="lectra-task-7",
        strip_height=10.0,
        sheet_length=20.0,
        parts=parts,
    )


def _candidate(
    *,
    two_parts: bool = False,
    overlap: bool = False,
    outside: bool = False,
) -> Candidate:
    placements = [
        Placement(
            part_id="a",
            rotation=0.0,
            translation=(19.0 if outside else 0.0, 0.0),
        )
    ]
    if two_parts:
        placements.append(
            Placement(
                part_id="b",
                rotation=0.0,
                translation=(0.0 if overlap else 3.0, 0.0),
            )
        )
    return Candidate(
        candidate_id="candidate-test",
        report_type=CandidateReportType.FINAL,
        seed=0,
        width=5.0 if two_parts else 2.0,
        density=0.1,
        placements=placements,
    )


def _remnant() -> RemnantStock:
    material = _material()
    geometry = canonical_polygon_record(box(10.0, 10.0, 14.0, 14.0))
    lineage = RemnantLineage.root(
        root_stock_id="test-root",
        source_candidate_id="origin-candidate",
        source_component_sha256=geometry.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, material),
        geometry=geometry,
        material=material,
        root_sheet_area=16.0,
        root_sheet_short_side=4.0,
        lineage=lineage,
    )


def test_standard_sheet_action_consumes_complete_layout_and_reconciles_material() -> None:
    action = build_standard_sheet_action(
        problem_id="yfm7p-" + "a" * 24,
        problem_sha256="sha256:" + "b" * 64,
        candidate_set_id="yfm7c-" + "c" * 24,
        candidate_set_sha256="sha256:" + "d" * 64,
        problem=_problem(),
        candidate=_candidate(),
        material=_material(),
        stock_id="sheet-instance-1",
        rules=_rules(),
        fit_config=RemnantFitConfig(),
    )

    assert action.kind is M7ActionKind.OPEN_STANDARD_SHEET
    assert action.selected_remnant_id is None
    assert action.accounting.parent_remnant_area == 200.0
    assert action.accounting.placed_area == 4.0
    assert action.accounting.reconciliation_delta == 0.0
    assert len(action.placed_parts) == 1
    assert len(action.returned_remnants) == 1
    assert action.returned_remnants[0].lineage.generation == 1
    assert action.action_id == f"yfm7a-{action.content_sha256[7:31]}"


def test_standard_sheet_action_rejects_part_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        build_standard_sheet_action(
            problem_id="yfm7p-" + "a" * 24,
            problem_sha256="sha256:" + "b" * 64,
            candidate_set_id="yfm7c-" + "c" * 24,
            candidate_set_sha256="sha256:" + "d" * 64,
            problem=_problem(two_parts=True),
            candidate=_candidate(two_parts=True, overlap=True),
            material=_material(),
            stock_id="sheet-overlap",
            rules=_rules(),
            fit_config=RemnantFitConfig(),
        )


def test_standard_sheet_action_rejects_out_of_stock_layout() -> None:
    with pytest.raises(ValueError, match="outside"):
        build_standard_sheet_action(
            problem_id="yfm7p-" + "a" * 24,
            problem_sha256="sha256:" + "b" * 64,
            candidate_set_id="yfm7c-" + "c" * 24,
            candidate_set_sha256="sha256:" + "d" * 64,
            problem=_problem(),
            candidate=_candidate(outside=True),
            material=_material(),
            stock_id="sheet-outside",
            rules=_rules(),
            fit_config=RemnantFitConfig(),
        )


def test_remnant_action_translates_whole_layout_and_preserves_recursive_lineage() -> None:
    remnant = _remnant()
    first = build_remnant_action(
        problem_id="yfm7p-" + "a" * 24,
        problem_sha256="sha256:" + "b" * 64,
        candidate_set_id="yfm7c-" + "c" * 24,
        candidate_set_sha256="sha256:" + "d" * 64,
        problem=_problem(),
        candidate=_candidate(),
        remnant=remnant,
        material=_material(),
        rules=_rules(),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(),
    )
    second = build_remnant_action(
        problem_id="yfm7p-" + "a" * 24,
        problem_sha256="sha256:" + "b" * 64,
        candidate_set_id="yfm7c-" + "c" * 24,
        candidate_set_sha256="sha256:" + "d" * 64,
        problem=_problem(),
        candidate=_candidate(),
        remnant=remnant,
        material=_material(),
        rules=_rules(),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(),
    )

    assert first == second
    assert first is not None
    assert first.kind is M7ActionKind.CONSUME_REMNANT
    assert first.selected_remnant_id == remnant.remnant_id
    assert first.translation == (10.0, 10.0)
    assert first.search_result is not None
    assert first.search_result.evaluated_candidate_count == 1
    assert first.accounting.parent_remnant_area == 16.0
    assert first.accounting.placed_area == 4.0
    assert first.accounting.reconciliation_delta == 0.0
    assert len(first.returned_remnants) == 1
    assert first.returned_remnants[0].lineage.generation == 2
    assert first.returned_remnants[0].lineage.parent_remnant_id == remnant.remnant_id


def test_remnant_action_returns_none_when_complete_layout_has_no_registered_fit() -> None:
    remnant = _remnant()
    result = build_remnant_action(
        problem_id="yfm7p-" + "a" * 24,
        problem_sha256="sha256:" + "b" * 64,
        candidate_set_id="yfm7c-" + "c" * 24,
        candidate_set_sha256="sha256:" + "d" * 64,
        problem=_problem(two_parts=True),
        candidate=_candidate(two_parts=True),
        remnant=remnant,
        material=_material(),
        rules=_rules(),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(maximum_candidates=8),
    )

    assert result is None


def test_layout_translation_generation_is_reusable_without_changing_search_order() -> None:
    remnant = _remnant()
    problem = _problem()
    candidate = _candidate()
    fit_config = RemnantFitConfig()
    search_config = LayoutFitSearchConfig(maximum_candidates=8)
    prepared_layout = prepare_layout_footprint(problem, candidate, fit_config)
    prepared_remnant = prepare_remnant_geometry(remnant)

    translations = generate_layout_translations(
        remnant,
        candidate,
        fit_config=fit_config,
        search_config=search_config,
        prepared_layout=prepared_layout,
        prepared_remnant=prepared_remnant,
    )
    result = search_layout_translation(
        remnant,
        problem,
        candidate,
        material=_material(),
        fit_config=fit_config,
        search_config=search_config,
        prepared_layout=prepared_layout,
        prepared_remnant=prepared_remnant,
        translation_candidates=translations,
    )

    assert translations.translations[:4] == (
        (10.0, 10.0),
        (10.0, 12.0),
        (12.0, 10.0),
        (12.0, 12.0),
    )
    assert result.translation == (10.0, 10.0)
    assert result.evaluated_candidate_count == 1
