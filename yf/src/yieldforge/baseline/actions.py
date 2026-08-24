"""Construct exact standard-sheet and remnant actions from shared M7 candidates."""

from __future__ import annotations

from shapely import box

from yieldforge.baseline.contracts import (
    LayoutFitSearchConfig,
    LayoutFitSearchResult,
    LayoutFitSearchStatus,
    M7ActionKind,
    M7LayoutActionEvidence,
)
from yieldforge.baseline.geometry import (
    LayoutConsumption,
    consume_layout,
    search_layout_translation,
)
from yieldforge.domain import Candidate, StripPackingProblem
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.reuse.contracts import (
    MaterialIdentity,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    canonical_polygon_record,
    derive_remnant_id,
)


def _standard_sheet_stock(
    problem: StripPackingProblem,
    candidate: Candidate,
    material: MaterialIdentity,
    *,
    stock_id: str,
) -> RemnantStock:
    geometry = canonical_polygon_record(box(0.0, 0.0, problem.sheet_length, problem.strip_height))
    lineage = RemnantLineage.root(
        root_stock_id=stock_id,
        source_candidate_id=candidate.candidate_id,
        source_component_sha256=geometry.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, material),
        geometry=geometry,
        material=material,
        root_sheet_area=float(problem.sheet_length * problem.strip_height),
        root_sheet_short_side=float(min(problem.sheet_length, problem.strip_height)),
        lineage=lineage,
    )


def _action(
    *,
    problem_id: str,
    problem_sha256: str,
    candidate_set_id: str,
    candidate_set_sha256: str,
    candidate: Candidate,
    kind: M7ActionKind,
    stock: RemnantStock,
    translation: tuple[float, float],
    search_result: LayoutFitSearchResult | None,
    consumption: LayoutConsumption,
) -> M7LayoutActionEvidence:
    selected_remnant_id = stock.remnant_id if kind is M7ActionKind.CONSUME_REMNANT else None
    semantic = {
        "schema_version": "yieldforge.m7-layout-action.v1",
        "problem_id": problem_id,
        "problem_sha256": problem_sha256,
        "candidate_set_id": candidate_set_id,
        "candidate_set_sha256": candidate_set_sha256,
        "candidate_id": candidate.candidate_id,
        "kind": kind.value,
        "selected_stock": stock.model_dump(mode="json"),
        "selected_remnant_id": selected_remnant_id,
        "translation": translation,
        "placements": [item.model_dump(mode="json") for item in consumption.placements],
        "placed_parts": [item.model_dump(mode="json") for item in consumption.placed_parts],
        "search_result": (
            search_result.model_dump(mode="json") if search_result is not None else None
        ),
        "accounting": consumption.accounting.model_dump(mode="json"),
        "returned_remnants": [item.model_dump(mode="json") for item in consumption.children],
    }
    digest = semantic_sha256(semantic)
    return M7LayoutActionEvidence(
        action_id=f"yfm7a-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        problem_id=problem_id,
        problem_sha256=problem_sha256,
        candidate_set_id=candidate_set_id,
        candidate_set_sha256=candidate_set_sha256,
        candidate_id=candidate.candidate_id,
        kind=kind,
        selected_stock=stock,
        selected_remnant_id=selected_remnant_id,
        translation=translation,
        placements=consumption.placements,
        placed_parts=consumption.placed_parts,
        search_result=search_result,
        accounting=consumption.accounting,
        returned_remnants=consumption.children,
    )


def build_standard_sheet_action(
    *,
    problem_id: str,
    problem_sha256: str,
    candidate_set_id: str,
    candidate_set_sha256: str,
    problem: StripPackingProblem,
    candidate: Candidate,
    material: MaterialIdentity,
    stock_id: str,
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
) -> M7LayoutActionEvidence:
    """Build one exact standard-sheet action from an archived layout."""

    stock = _standard_sheet_stock(problem, candidate, material, stock_id=stock_id)
    consumption = consume_layout(
        stock,
        problem,
        candidate,
        (0.0, 0.0),
        material=material,
        rules=rules,
        fit_config=fit_config,
        reroot_standard_sheet=True,
    )
    return _action(
        problem_id=problem_id,
        problem_sha256=problem_sha256,
        candidate_set_id=candidate_set_id,
        candidate_set_sha256=candidate_set_sha256,
        candidate=candidate,
        kind=M7ActionKind.OPEN_STANDARD_SHEET,
        stock=stock,
        translation=(0.0, 0.0),
        search_result=None,
        consumption=consumption,
    )


def build_remnant_action(
    *,
    problem_id: str,
    problem_sha256: str,
    candidate_set_id: str,
    candidate_set_sha256: str,
    problem: StripPackingProblem,
    candidate: Candidate,
    remnant: RemnantStock,
    material: MaterialIdentity,
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
) -> M7LayoutActionEvidence | None:
    """Return an exact remnant action, or none when registered search finds no witness."""

    search = search_layout_translation(
        remnant,
        problem,
        candidate,
        material=material,
        fit_config=fit_config,
        search_config=search_config,
    )
    if search.status is LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH:
        return None
    if search.translation is None:
        raise ValueError("layout search reported fit without a translation")
    consumption = consume_layout(
        remnant,
        problem,
        candidate,
        search.translation,
        material=material,
        rules=rules,
        fit_config=fit_config,
        reroot_standard_sheet=False,
    )
    return _action(
        problem_id=problem_id,
        problem_sha256=problem_sha256,
        candidate_set_id=candidate_set_id,
        candidate_set_sha256=candidate_set_sha256,
        candidate=candidate,
        kind=M7ActionKind.CONSUME_REMNANT,
        stock=remnant,
        translation=search.translation,
        search_result=search,
        consumption=consumption,
    )
