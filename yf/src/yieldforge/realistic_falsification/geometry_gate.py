"""Geometry-informed optimistic relaxation for M11 Gate 2."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)
from shapely import box, union_all

from yieldforge.baseline.contracts import (
    LayoutFitSearchConfig,
    LayoutFitSearchResult,
    LayoutFitSearchStatus,
    PlacedPartEvidence,
)
from yieldforge.baseline.geometry import (
    LayoutConsumption,
    certify_translation_impossible,
    consume_layout,
    prepare_layout_footprint,
    search_layout_translation,
)
from yieldforge.domain import (
    Candidate,
    CandidateReportType,
    Part,
    Placement,
    StripPackingProblem,
)
from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    M0ExperimentContract,
    load_frozen_json,
    semantic_sha256,
)
from yieldforge.experiments.remnant_reuse import M4ReuseInputPack, load_m4_input_pack
from yieldforge.realistic_falsification.bounds import (
    Gate1SourceContext,
    load_official_gate1_context,
)
from yieldforge.realistic_falsification.contracts import (
    M11EvidenceState,
    M11ExperimentContract,
    M11InvalidReason,
    M11InvalidReasonCategory,
    M11VerdictResult,
    build_m11_verdict,
)
from yieldforge.realistic_falsification.evaluate import (
    Gate1EvaluationResult,
    authenticate_official_gate1_evaluation,
)
from yieldforge.realistic_falsification.matching import (
    MatchingEdge,
    maximum_reward_matching,
)
from yieldforge.realistic_falsification.pack import M11EconomicProfile, M11Payload
from yieldforge.residuals.contracts import (
    ResidualGeometryError,
    ResidualRuleName,
    ResidualRuleSet,
    rule_set_from_m0,
)
from yieldforge.residuals.geometry import (
    classify_residual_components,
    geometry_sha256,
    measure_residual_components,
    polygon_components,
)
from yieldforge.reuse.contracts import (
    FitPlacement,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    ReuseAccounting,
    canonical_polygon_record,
    child_lineage,
    derive_remnant_id,
    polygon_from_record,
)
from yieldforge.reuse.geometry import validate_fit_placement

_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"

Gate2EdgeStatus = Literal[
    "certified_no_fit",
    "fit_witnessed",
    "unresolved_optimistically_counted",
    "blocking_error",
]
_OPTIMISTIC_EDGE_STATUSES = frozenset(
    {
        "fit_witnessed",
        "unresolved_optimistically_counted",
    }
)


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


class Gate2EvidenceError(ValueError):
    """Gate 2 evidence could not be classified without understating headroom."""


@dataclass(frozen=True, slots=True)
class _OfficialGate2Context:
    gate1: Gate1SourceContext
    m4: M4ReuseInputPack
    m0: M0ExperimentContract
    rules: ResidualRuleSet
    fit_config: RemnantFitConfig
    search_config: LayoutFitSearchConfig


@dataclass(frozen=True, slots=True)
class _ReconstructedPayload:
    payload_id: str
    source_kind: str
    source_binding_sha256: str
    problem: StripPackingProblem
    candidate: Candidate
    origin_remnants: tuple[RemnantStock, ...]
    standard_consumption: LayoutConsumption | None


@dataclass(frozen=True, slots=True)
class Gate2Origin:
    """Runtime binding for one first-generation eligible remnant."""

    stream_id: str
    event_position: int
    event_id: str
    released_at: datetime
    material_key: str
    reference_area: float
    source_kind: str
    source_binding_sha256: str
    remnant: RemnantStock

    def __post_init__(self) -> None:
        if not self.stream_id or not self.event_id or not self.material_key or not self.source_kind:
            raise ValueError("Gate 2 origin identifiers must be nonempty")
        if self.event_position < 0:
            raise ValueError("Gate 2 origin position cannot be negative")
        if self.released_at.tzinfo is None or self.released_at.utcoffset() is None:
            raise ValueError("Gate 2 origin release must be timezone-aware")
        if self.reference_area <= 0:
            raise ValueError("Gate 2 origin reference area must be positive")
        if not _is_sha256(self.source_binding_sha256):
            raise ValueError("Gate 2 origin source binding must be a SHA-256 identity")
        if self.remnant.material != _event_material(self.material_key):
            raise ValueError("Gate 2 origin material key differs from exact remnant material")


@dataclass(frozen=True, slots=True)
class Gate2Target:
    """Runtime binding for one later standard-opening layout."""

    stream_id: str
    event_position: int
    event_id: str
    known_at: datetime
    released_at: datetime
    material_key: str
    opening_id: str
    opening_content_sha256: str
    purchase_cost: float
    source_kind: str
    source_binding_sha256: str
    problem: StripPackingProblem
    candidate: Candidate

    def __post_init__(self) -> None:
        if not all(
            (
                self.stream_id,
                self.event_id,
                self.material_key,
                self.opening_id,
                self.source_kind,
            )
        ):
            raise ValueError("Gate 2 target identifiers must be nonempty")
        if self.event_position < 0:
            raise ValueError("Gate 2 target position cannot be negative")
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.known_at, self.released_at)
        ):
            raise ValueError("Gate 2 target times must be timezone-aware")
        if self.known_at > self.released_at:
            raise ValueError("Gate 2 target cannot be released before it is known")
        if self.purchase_cost < 0:
            raise ValueError("Gate 2 target purchase cost cannot be negative")
        if not _is_sha256(self.opening_content_sha256) or not _is_sha256(
            self.source_binding_sha256
        ):
            raise ValueError("Gate 2 target bindings must be SHA-256 identities")

    @property
    def material(self) -> MaterialIdentity:
        source = self.material_key
        return MaterialIdentity(
            material_code=source,
            grade=source,
            thickness=source,
            surface=source,
            grain=source,
            provenance=MaterialProvenance.ASSUMED,
        )


def _load_official_gate2_context(repository_root: Path) -> _OfficialGate2Context:
    """Strictly load the official M0/M3/M4/LOCo geometry leaves for Gate 2."""

    root = Path(repository_root).resolve()
    gate1 = load_official_gate1_context(root)
    lectra = gate1.source_manifest.lectra
    m4_path = root / lectra.m4_repository_path
    try:
        raw = m4_path.read_bytes()
    except OSError as error:
        raise Gate2EvidenceError("Gate 2 could not read the pinned M4 input") from error
    if hashlib.sha256(raw).hexdigest() != lectra.m4_raw_sha256:
        raise Gate2EvidenceError("Gate 2 M4 raw bytes differ from the source attestation")
    try:
        m4 = load_m4_input_pack(m4_path)
        m0 = load_frozen_json(root / "experiments/m0-contract-v1.json", M0ExperimentContract)
    except (OSError, TypeError, ValueError) as error:
        raise Gate2EvidenceError("Gate 2 official geometry leaves failed strict loading") from error
    if (
        m4.input_id != lectra.m4_input_id
        or m4.content_sha256 != lectra.m4_content_sha256
        or m4.m3_input_id != gate1.m3_input.input_id
        or m4.m3_input_sha256 != gate1.m3_input.content_sha256
        or m4.m0_contract_id != m0.contract_id
        or m4.m0_contract_sha256 != m0.content_sha256
        or m4.primary_fit_config.clearance_distance != 0.0
    ):
        raise Gate2EvidenceError("Gate 2 M0/M3/M4 root bindings do not reconcile")
    return _OfficialGate2Context(
        gate1=gate1,
        m4=m4,
        m0=m0,
        rules=rule_set_from_m0(m0.remnant_eligibility),
        fit_config=m4.primary_fit_config,
        search_config=LayoutFitSearchConfig(),
    )


def _event_material(material_key: str) -> MaterialIdentity:
    if not material_key or material_key.strip() != material_key:
        raise Gate2EvidenceError("Gate 2 material key must be nonempty and canonical")
    return MaterialIdentity(
        material_code=material_key,
        grade=material_key,
        thickness=material_key,
        surface=material_key,
        grain=material_key,
        provenance=MaterialProvenance.ASSUMED,
    )


def _reroot_remnant(
    source: RemnantStock,
    *,
    material: MaterialIdentity,
    root_domain: str,
) -> RemnantStock:
    root_digest = semantic_sha256(
        {
            "domain": root_domain,
            "source_remnant_id": source.remnant_id,
            "source_polygon_sha256": source.geometry.polygon_sha256,
            "material": material.model_dump(mode="json"),
        }
    )
    lineage = RemnantLineage.root(
        root_stock_id=f"yfm11g2stock-{root_digest[:24]}",
        source_candidate_id=source.lineage.source_candidate_id,
        source_component_sha256=source.geometry.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, source.geometry, material),
        geometry=source.geometry,
        material=material,
        root_sheet_area=source.root_sheet_area,
        root_sheet_short_side=source.root_sheet_short_side,
        lineage=lineage,
    )


def _lectra_payload_geometry(
    context: _OfficialGate2Context,
    payload: M11Payload,
    *,
    selected_candidate_id: str,
    material: MaterialIdentity,
) -> _ReconstructedPayload:
    try:
        tasks_index = int(payload.source_case_id.removeprefix("lectra-task:"))
    except ValueError as error:
        raise Gate2EvidenceError("Gate 2 Lectra payload has an invalid task identity") from error
    pairs = tuple(
        item for item in context.gate1.m3_input.task_pairs if item.tasks_index == tasks_index
    )
    if len(pairs) != 1:
        raise Gate2EvidenceError("Gate 2 Lectra payload does not join exactly one M3 task")
    pair = pairs[0]
    selected_values = tuple(
        item
        for item in pair.selected_candidates
        if item.candidate.candidate_id == selected_candidate_id
    )
    references = tuple(
        item for item in payload.candidate_references if item.candidate_id == selected_candidate_id
    )
    origins = tuple(
        item
        for item in context.m4.origin_remnants
        if item.origin_tasks_index == tasks_index
        and item.origin_candidate_id == selected_candidate_id
    )
    if len(selected_values) != 1 or len(references) != 1 or len(origins) != 1:
        raise Gate2EvidenceError("Gate 2 Lectra candidate does not join M3 and M4 exactly")
    source_origin = origins[0].remnant
    remnant = _reroot_remnant(
        source_origin,
        material=material,
        root_domain=f"{payload.payload_id}|{selected_candidate_id}",
    )
    binding = "sha256:" + semantic_sha256(
        {
            "payload_id": payload.payload_id,
            "payload_content_sha256": payload.content_sha256,
            "m3_input_id": context.gate1.m3_input.input_id,
            "m3_input_content_sha256": context.gate1.m3_input.content_sha256,
            "m4_input_id": context.m4.input_id,
            "m4_content_sha256": context.m4.content_sha256,
            "tasks_index": tasks_index,
            "candidate_reference": references[0].model_dump(mode="json"),
            "source_remnant_id": source_origin.remnant_id,
        }
    )
    return _ReconstructedPayload(
        payload_id=payload.payload_id,
        source_kind="lectra",
        source_binding_sha256=binding,
        problem=pair.problem,
        candidate=selected_values[0].candidate,
        origin_remnants=(remnant,),
        standard_consumption=None,
    )


def _loco_problem_candidate(
    context: _OfficialGate2Context,
    payload: M11Payload,
) -> tuple[StripPackingProblem, Candidate]:
    fallback = payload.fallback_stock
    if fallback is None:
        raise Gate2EvidenceError("Gate 2 LOCo payload lacks its fallback stock")
    items = {item.item_id: item for item in context.gate1.loco_catalog.items}
    parts = []
    placements = []
    placed_area = 0.0
    for placement in fallback.placements:
        try:
            item = items[placement.geometry_reference_id]
        except KeyError as error:
            raise Gate2EvidenceError("Gate 2 LOCo fallback references an absent item") from error
        polygon = polygon_from_record(item.geometry)
        if polygon.interiors:
            raise Gate2EvidenceError("Gate 2 LOCo Part adapter cannot discard source holes")
        part_id = f"{item.item_id}#copy-{placement.copy_index}"
        part = Part(
            id=part_id,
            shape=[(float(x), float(y)) for x, y in polygon.exterior.coords],
            demand=1,
            allowed_orientations=[0.0],
        )
        parts.append(part)
        placements.append(
            Placement(
                part_id=part_id,
                rotation=0.0,
                translation=(placement.x, placement.y),
            )
        )
        placed_area += float(polygon.area)
    density = placed_area / fallback.area
    if density > 1.0 + 1e-12:
        raise Gate2EvidenceError("Gate 2 LOCo fallback density exceeds its stock")
    problem = StripPackingProblem(
        name=f"m11-loco-{payload.payload_id}",
        strip_height=fallback.height,
        sheet_length=fallback.width,
        parts=parts,
    )
    candidate = Candidate(
        candidate_id=fallback.stock_id,
        report_type=CandidateReportType.FINAL,
        seed=0,
        width=fallback.width,
        density=min(1.0, density),
        placements=placements,
    )
    return problem, candidate


def _loco_payload_geometry(
    context: _OfficialGate2Context,
    payload: M11Payload,
    *,
    selected_candidate_id: str,
    material: MaterialIdentity,
) -> _ReconstructedPayload:
    fallback = payload.fallback_stock
    if fallback is None or selected_candidate_id != fallback.stock_id:
        raise Gate2EvidenceError("Gate 2 LOCo selection differs from the frozen fallback")
    problem, candidate = _loco_problem_candidate(context, payload)
    stock_geometry = canonical_polygon_record(box(0.0, 0.0, fallback.width, fallback.height))
    root_digest = semantic_sha256(
        {
            "payload_id": payload.payload_id,
            "payload_content_sha256": payload.content_sha256,
            "fallback_content_sha256": fallback.content_sha256,
            "material": material.model_dump(mode="json"),
        }
    )
    lineage = RemnantLineage.root(
        root_stock_id=f"yfm11g2stock-{root_digest[:24]}",
        source_candidate_id=candidate.candidate_id,
        source_component_sha256=stock_geometry.polygon_sha256,
    )
    stock = RemnantStock(
        remnant_id=derive_remnant_id(lineage, stock_geometry, material),
        geometry=stock_geometry,
        material=material,
        root_sheet_area=fallback.area,
        root_sheet_short_side=min(fallback.width, fallback.height),
        lineage=lineage,
    )
    try:
        consumption = _consume_layout_with_exact_delta(
            stock,
            problem,
            candidate,
            (0.0, 0.0),
            material=material,
            rules=context.rules,
            fit_config=context.fit_config,
            reroot_standard_sheet=True,
        )
    except (TypeError, ValueError) as error:
        raise Gate2EvidenceError(
            "Gate 2 LOCo fallback failed exact residual reconstruction"
        ) from error
    binding = "sha256:" + semantic_sha256(
        {
            "payload_id": payload.payload_id,
            "payload_content_sha256": payload.content_sha256,
            "fallback": fallback.model_dump(mode="json"),
            "loco_catalog_id": context.gate1.loco_catalog.catalog_id,
            "loco_catalog_content_sha256": context.gate1.loco_catalog.content_sha256,
            "standard_stock_polygon_sha256": stock_geometry.polygon_sha256,
            "returned_remnant_ids": [item.remnant_id for item in consumption.children],
        }
    )
    return _ReconstructedPayload(
        payload_id=payload.payload_id,
        source_kind="loco_2dics",
        source_binding_sha256=binding,
        problem=problem,
        candidate=candidate,
        origin_remnants=consumption.children,
        standard_consumption=consumption,
    )


def _consume_layout_float_compatible(
    stock: RemnantStock,
    problem: StripPackingProblem,
    candidate: Candidate,
    translation_xy: tuple[float, float],
    *,
    material: MaterialIdentity,
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
    reroot_standard_sheet: bool,
) -> LayoutConsumption:
    """Replay ``consume_layout`` with its model's exact summation order.

    Python 3.12's compensated ``sum`` can differ by one ULP from the left-to-right
    expression used by :class:`ReuseAccounting`.  This fallback intentionally
    reproduces the baseline geometry and lineage algorithm, but calculates the
    persisted reconciliation delta with the validator's own expression.
    """

    if any(part.demand != 1 for part in problem.parts):
        raise ValueError("M7 complete-layout actions require explicit demand-one parts")
    part_by_id = {item.id: item for item in problem.parts}
    placement_ids = tuple(item.part_id for item in candidate.placements)
    if len(placement_ids) != len(set(placement_ids)) or set(placement_ids) != set(part_by_id):
        raise ValueError("layout placements do not match problem parts uniquely")
    source_by_id = {item.part_id: item for item in candidate.placements}
    placements = tuple(
        FitPlacement(
            part_id=part.id,
            rotation=source_by_id[part.id].rotation,
            translation=(
                source_by_id[part.id].translation[0] + translation_xy[0],
                source_by_id[part.id].translation[1] + translation_xy[1],
            ),
        )
        for part in problem.parts
    )
    validated = tuple(
        validate_fit_placement(
            stock,
            part_by_id[placement.part_id],
            placement,
            part_material=material,
            config=fit_config,
        )
        for placement in placements
    )
    parent = polygon_from_record(stock.geometry)
    placed_union = union_all(tuple(item.placed_polygon for item in validated))
    overlap_area = sum(item.placed_polygon.area for item in validated) - placed_union.area
    layout_tolerance = max(
        fit_config.coordinate_tolerance,
        problem.sheet_length * problem.strip_height * fit_config.relative_area_tolerance,
    )
    if overlap_area > layout_tolerance:
        raise ValueError("complete layout contains part overlap")
    buffered_union = union_all(tuple(item.buffered_footprint for item in validated))
    process_loss = buffered_union.difference(placed_union)
    unused = parent.difference(buffered_union)
    for label, geometry in (
        ("placed", placed_union),
        ("process loss", process_loss),
        ("unused", unused),
    ):
        if not geometry.is_valid:
            raise ValueError(f"complete-layout {label} geometry is invalid")
    try:
        components = polygon_components(unused)
        metrics = measure_residual_components(
            components,
            access_boundary=parent.boundary,
            rules=rules,
            reference_short_side=stock.root_sheet_short_side,
            coordinate_tolerance=fit_config.coordinate_tolerance,
        )
        area_tolerance = max(
            fit_config.coordinate_tolerance,
            parent.area * fit_config.relative_area_tolerance,
        )
        classifications = classify_residual_components(
            components,
            metrics,
            rules,
            reference_area=stock.root_sheet_area,
            reference_short_side=stock.root_sheet_short_side,
            area_tolerance=area_tolerance,
            coordinate_tolerance=fit_config.coordinate_tolerance,
        )
    except ResidualGeometryError as error:
        raise ValueError("complete-layout residual classification failed") from error
    primary = next(item for item in classifications if item.rule_name is ResidualRuleName.PRIMARY)
    component_by_hash = {geometry_sha256(item): item for item in components}
    children = []
    for component_hash in primary.retained_component_sha256:
        geometry = canonical_polygon_record(component_by_hash[component_hash])
        lineage = (
            RemnantLineage.root(
                root_stock_id=stock.lineage.root_stock_id,
                source_candidate_id=candidate.candidate_id,
                source_component_sha256=geometry.polygon_sha256,
            )
            if reroot_standard_sheet
            else child_lineage(
                stock,
                source_component_sha256=geometry.polygon_sha256,
            )
        )
        children.append(
            RemnantStock(
                remnant_id=derive_remnant_id(lineage, geometry, material),
                geometry=geometry,
                material=material,
                root_sheet_area=stock.root_sheet_area,
                root_sheet_short_side=stock.root_sheet_short_side,
                lineage=lineage,
            )
        )
    children.sort(key=lambda item: item.remnant_id)
    placed_area = float(placed_union.area)
    process_loss_area = float(process_loss.area)
    retained_area = primary.retained_area
    scrap_area = primary.scrap_area
    accounted = placed_area + process_loss_area + retained_area + scrap_area
    delta = abs(float(parent.area) - accounted)
    if delta > area_tolerance:
        raise ValueError("complete-layout material accounting does not reconcile")
    return LayoutConsumption(
        placements=placements,
        placed_parts=tuple(
            PlacedPartEvidence(
                part_id=item.placement.part_id,
                geometry=canonical_polygon_record(item.placed_polygon),
            )
            for item in validated
        ),
        accounting=ReuseAccounting(
            parent_remnant_area=float(parent.area),
            placed_area=placed_area,
            process_loss_area=process_loss_area,
            retained_child_area=retained_area,
            scrap_area=scrap_area,
            reconciliation_delta=delta,
            area_tolerance=area_tolerance,
        ),
        children=tuple(children),
    )


def _consume_layout_with_exact_delta(
    stock: RemnantStock,
    problem: StripPackingProblem,
    candidate: Candidate,
    translation_xy: tuple[float, float],
    *,
    material: MaterialIdentity,
    rules: ResidualRuleSet,
    fit_config: RemnantFitConfig,
    reroot_standard_sheet: bool,
) -> LayoutConsumption:
    """Prefer the frozen baseline and repair only its known one-ULP delta defect."""

    try:
        return consume_layout(
            stock,
            problem,
            candidate,
            translation_xy,
            material=material,
            rules=rules,
            fit_config=fit_config,
            reroot_standard_sheet=reroot_standard_sheet,
        )
    except ValueError as error:
        if "reuse accounting delta does not match material categories" not in str(error):
            raise
    return _consume_layout_float_compatible(
        stock,
        problem,
        candidate,
        translation_xy,
        material=material,
        rules=rules,
        fit_config=fit_config,
        reroot_standard_sheet=reroot_standard_sheet,
    )


def _reconstruct_official_payload(
    context: _OfficialGate2Context,
    *,
    payload: M11Payload,
    selected_candidate_id: str,
    material_key: str,
) -> _ReconstructedPayload:
    """Reconstruct one official event layout and its first-generation remnants."""

    canonical = next(
        (
            item
            for item in context.gate1.bundle.population.payloads
            if item.payload_id == payload.payload_id
        ),
        None,
    )
    if canonical is None or canonical != payload:
        raise Gate2EvidenceError("Gate 2 payload differs from the official population")
    material = _event_material(material_key)
    if payload.source_kind == "lectra":
        return _lectra_payload_geometry(
            context,
            payload,
            selected_candidate_id=selected_candidate_id,
            material=material,
        )
    return _loco_payload_geometry(
        context,
        payload,
        selected_candidate_id=selected_candidate_id,
        material=material,
    )


class Gate2RejectionCertificate(FrozenExperimentModel):
    """Persisted necessary-condition proof for one impossible edge."""

    impossible: Literal[True] = True
    reason: Literal[
        "material_mismatch",
        "footprint_area_exceeds_remnant",
        "footprint_width_exceeds_remnant",
        "footprint_height_exceeds_remnant",
    ]
    layout_area: StrictFloat = Field(gt=0)
    remnant_area: StrictFloat = Field(gt=0)
    layout_width: StrictFloat = Field(ge=0)
    remnant_width: StrictFloat = Field(ge=0)
    layout_height: StrictFloat = Field(ge=0)
    remnant_height: StrictFloat = Field(ge=0)
    area_tolerance: StrictFloat = Field(gt=0)


class Gate2ConsumptionEvidence(FrozenExperimentModel):
    """Complete exact layout-consumption witness for a geometry-positive edge."""

    witness_id: StrictStr = Field(pattern=r"^yfm11g2w-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    translation: tuple[StrictFloat, StrictFloat]
    placements: tuple[FitPlacement, ...] = Field(min_length=1)
    placed_parts: tuple[PlacedPartEvidence, ...] = Field(min_length=1)
    accounting: ReuseAccounting
    children: tuple[RemnantStock, ...]

    @model_validator(mode="after")
    def require_identity_and_reconciled_placement(self):
        if tuple(item.part_id for item in self.placements) != tuple(
            item.part_id for item in self.placed_parts
        ):
            raise ValueError("Gate 2 consumption placements differ from placed-part evidence")
        digest = semantic_sha256(self, excluded_fields={"witness_id", "content_sha256"})
        if self.witness_id != f"yfm11g2w-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 2 consumption identity differs from exact evidence")
        return self


class Gate2RewardEvidence(FrozenExperimentModel):
    """One safe arm-specific optimistic edge reward."""

    arm: Literal["central", "adverse"]
    purchase_avoided_exact: StrictStr = Field(min_length=1)
    return_handling_exact: StrictStr = Field(min_length=1)
    retrieval_handling_exact: StrictStr = Field(min_length=1)
    elapsed_storage_exact: StrictStr = Field(min_length=1)
    raw_reward_exact: StrictStr = Field(min_length=1)
    reward_micro_units: StrictInt
    rounding_mode: Literal["ceiling_to_micro_unit"] = "ceiling_to_micro_unit"
    scrap_credit_omitted: Literal[True] = True
    terminal_credit_omitted: Literal[True] = True


class Gate2EdgeEvidence(FrozenExperimentModel):
    """One auditable origin-to-target geometry classification."""

    schema_version: Literal["yieldforge.m11-gate2-edge.v1"] = "yieldforge.m11-gate2-edge.v1"
    edge_id: StrictStr = Field(pattern=r"^yfm11g2e-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    stream_id: StrictStr = Field(min_length=1)
    origin_event_position: StrictInt = Field(ge=0)
    origin_event_id: StrictStr = Field(min_length=1)
    origin_released_at: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    origin_material_key: StrictStr = Field(min_length=1)
    origin_source_binding_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    origin_remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    target_event_position: StrictInt = Field(ge=0)
    target_event_id: StrictStr = Field(min_length=1)
    target_known_at: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    target_released_at: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    target_material_key: StrictStr = Field(min_length=1)
    target_source_binding_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    target_opening_id: StrictStr = Field(min_length=1)
    target_opening_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    target_candidate_id: StrictStr = Field(min_length=1)
    unknown_at_origin: StrictBool
    status: Gate2EdgeStatus
    resolution_basis: Literal[
        "necessary_filter_certificate",
        "exact_shapely_fit_witness",
        "bounded_no_witness",
        "not_searched_favorable_relaxation",
        "blocking_error",
    ]
    blocking_error_code: (
        Literal[
            "missing_residual_rules",
            "layout_preparation_failed",
            "necessary_filter_failed",
            "search_failed",
            "reward_not_safely_computable",
            "fit_consumption_failed",
        ]
        | None
    )
    optimistically_included_in_matching: StrictBool
    rejection_certificate: Gate2RejectionCertificate | None
    search_result: LayoutFitSearchResult | None
    consumption: Gate2ConsumptionEvidence | None
    central_reward: Gate2RewardEvidence | None
    adverse_reward: Gate2RewardEvidence | None

    @model_validator(mode="after")
    def require_closed_status_and_identity(self):
        if self.status == "certified_no_fit":
            valid_shape = (
                self.resolution_basis == "necessary_filter_certificate"
                and self.blocking_error_code is None
                and self.rejection_certificate is not None
                and self.search_result is None
                and self.consumption is None
                and self.central_reward is None
                and self.adverse_reward is None
            )
        elif self.status == "fit_witnessed":
            valid_shape = (
                self.resolution_basis == "exact_shapely_fit_witness"
                and self.blocking_error_code is None
                and self.rejection_certificate is None
                and self.search_result is not None
                and self.search_result.status is LayoutFitSearchStatus.FIT
                and self.consumption is not None
                and self.central_reward is not None
                and self.adverse_reward is not None
            )
        elif self.status == "unresolved_optimistically_counted":
            searched_shape = (
                self.resolution_basis == "bounded_no_witness"
                and self.search_result is not None
                and self.search_result.status
                is LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH
            )
            unsearched_shape = (
                self.resolution_basis == "not_searched_favorable_relaxation"
                and self.search_result is None
            )
            valid_shape = (
                self.blocking_error_code is None
                and self.rejection_certificate is None
                and (searched_shape or unsearched_shape)
                and self.consumption is None
                and self.central_reward is not None
                and self.adverse_reward is not None
            )
        else:
            valid_shape = (
                self.resolution_basis == "blocking_error"
                and self.blocking_error_code is not None
                and self.rejection_certificate is None
                and self.consumption is None
                and self.central_reward is None
                and self.adverse_reward is None
            )
        if not valid_shape:
            raise ValueError("Gate 2 edge evidence differs from its closed status")
        if (
            self.origin_event_position >= self.target_event_position
            or self.origin_released_at >= self.target_released_at
        ):
            raise ValueError("Gate 2 edge chronology is not strictly forward")
        expected_unknown = self.target_known_at > self.origin_released_at
        if self.unknown_at_origin is not expected_unknown:
            raise ValueError("Gate 2 unknown-future classification differs from chronology")
        if self.optimistically_included_in_matching is not (
            self.status == "unresolved_optimistically_counted"
        ):
            raise ValueError("Gate 2 unresolved inclusion flag differs from edge status")
        digest = semantic_sha256(self, excluded_fields={"edge_id", "content_sha256"})
        if self.edge_id != f"yfm11g2e-{digest[:24]}" or self.content_sha256 != (f"sha256:{digest}"):
            raise ValueError("Gate 2 edge identity differs from its evidence")
        return self


def _default_economic_profiles() -> tuple[M11EconomicProfile, M11EconomicProfile]:
    return (
        M11EconomicProfile(
            arm="central",
            virgin_cost_per_reference_area=100.0,
            scrap_and_terminal_credit=10.0,
            return_handling=0.25,
            retrieval_handling=0.25,
            storage_per_reference_area_30_days=0.5,
        ),
        M11EconomicProfile(
            arm="adverse",
            virgin_cost_per_reference_area=100.0,
            scrap_and_terminal_credit=25.0,
            return_handling=1.0,
            retrieval_handling=1.0,
            storage_per_reference_area_30_days=2.0,
        ),
    )


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _reward(
    origin: Gate2Origin,
    target: Gate2Target,
    profile: M11EconomicProfile,
) -> Gate2RewardEvidence:
    elapsed_seconds = Decimal(
        str(
            (
                target.released_at.astimezone(UTC) - origin.released_at.astimezone(UTC)
            ).total_seconds()
        )
    )
    if elapsed_seconds <= 0:
        raise Gate2EvidenceError("Gate 2 reward requires strictly positive elapsed chronology")
    purchase = _decimal(target.purchase_cost)
    returned = _decimal(profile.return_handling)
    retrieved = _decimal(profile.retrieval_handling)
    area_ratio = _decimal(origin.remnant.geometry.area) / _decimal(origin.reference_area)
    storage = (
        _decimal(profile.storage_per_reference_area_30_days)
        * area_ratio
        * elapsed_seconds
        / Decimal(30 * 24 * 60 * 60)
    )
    raw = purchase - returned - retrieved - storage
    micro = int((raw * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))
    return Gate2RewardEvidence(
        arm=profile.arm,
        purchase_avoided_exact=format(purchase, "f"),
        return_handling_exact=format(returned, "f"),
        retrieval_handling_exact=format(retrieved, "f"),
        elapsed_storage_exact=format(storage, "f"),
        raw_reward_exact=format(raw, "f"),
        reward_micro_units=micro,
    )


def _consumption_evidence(
    translation: tuple[float, float],
    consumption,
) -> Gate2ConsumptionEvidence:
    semantic = {
        "translation": translation,
        "placements": [item.model_dump(mode="json") for item in consumption.placements],
        "placed_parts": [item.model_dump(mode="json") for item in consumption.placed_parts],
        "accounting": consumption.accounting.model_dump(mode="json"),
        "children": [item.model_dump(mode="json") for item in consumption.children],
    }
    digest = semantic_sha256(semantic)
    return Gate2ConsumptionEvidence(
        witness_id=f"yfm11g2w-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        translation=translation,
        placements=consumption.placements,
        placed_parts=consumption.placed_parts,
        accounting=consumption.accounting,
        children=consumption.children,
    )


def _build_edge(
    origin: Gate2Origin,
    target: Gate2Target,
    *,
    status: Gate2EdgeStatus,
    resolution_basis: Literal[
        "necessary_filter_certificate",
        "exact_shapely_fit_witness",
        "bounded_no_witness",
        "not_searched_favorable_relaxation",
        "blocking_error",
    ]
    | None = None,
    blocking_error_code: Literal[
        "missing_residual_rules",
        "layout_preparation_failed",
        "necessary_filter_failed",
        "search_failed",
        "reward_not_safely_computable",
        "fit_consumption_failed",
    ]
    | None = None,
    rejection_certificate: Gate2RejectionCertificate | None,
    search_result: LayoutFitSearchResult | None = None,
    consumption: Gate2ConsumptionEvidence | None = None,
    central_reward: Gate2RewardEvidence | None = None,
    adverse_reward: Gate2RewardEvidence | None = None,
) -> Gate2EdgeEvidence:
    if resolution_basis is None:
        resolution_basis = {
            "certified_no_fit": "necessary_filter_certificate",
            "fit_witnessed": "exact_shapely_fit_witness",
            "unresolved_optimistically_counted": "bounded_no_witness",
            "blocking_error": "blocking_error",
        }[status]
    semantic = {
        "schema_version": "yieldforge.m11-gate2-edge.v1",
        "stream_id": origin.stream_id,
        "origin_event_position": origin.event_position,
        "origin_event_id": origin.event_id,
        "origin_released_at": _timestamp(origin.released_at),
        "origin_material_key": origin.material_key,
        "origin_source_binding_sha256": origin.source_binding_sha256,
        "origin_remnant_id": origin.remnant.remnant_id,
        "target_event_position": target.event_position,
        "target_event_id": target.event_id,
        "target_known_at": _timestamp(target.known_at),
        "target_released_at": _timestamp(target.released_at),
        "target_material_key": target.material_key,
        "target_source_binding_sha256": target.source_binding_sha256,
        "target_opening_id": target.opening_id,
        "target_opening_content_sha256": target.opening_content_sha256,
        "target_candidate_id": target.candidate.candidate_id,
        "unknown_at_origin": target.known_at > origin.released_at,
        "status": status,
        "resolution_basis": resolution_basis,
        "blocking_error_code": blocking_error_code,
        "optimistically_included_in_matching": (status == "unresolved_optimistically_counted"),
        "rejection_certificate": (
            rejection_certificate.model_dump(mode="json")
            if rejection_certificate is not None
            else None
        ),
        "search_result": search_result.model_dump(mode="json") if search_result else None,
        "consumption": consumption.model_dump(mode="json") if consumption else None,
        "central_reward": central_reward.model_dump(mode="json") if central_reward else None,
        "adverse_reward": adverse_reward.model_dump(mode="json") if adverse_reward else None,
    }
    digest = semantic_sha256(semantic)
    return Gate2EdgeEvidence(
        edge_id=f"yfm11g2e-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        schema_version="yieldforge.m11-gate2-edge.v1",
        stream_id=origin.stream_id,
        origin_event_position=origin.event_position,
        origin_event_id=origin.event_id,
        origin_released_at=_timestamp(origin.released_at),
        origin_material_key=origin.material_key,
        origin_source_binding_sha256=origin.source_binding_sha256,
        origin_remnant_id=origin.remnant.remnant_id,
        target_event_position=target.event_position,
        target_event_id=target.event_id,
        target_known_at=_timestamp(target.known_at),
        target_released_at=_timestamp(target.released_at),
        target_material_key=target.material_key,
        target_source_binding_sha256=target.source_binding_sha256,
        target_opening_id=target.opening_id,
        target_opening_content_sha256=target.opening_content_sha256,
        target_candidate_id=target.candidate.candidate_id,
        unknown_at_origin=target.known_at > origin.released_at,
        status=status,
        resolution_basis=resolution_basis,
        blocking_error_code=blocking_error_code,
        optimistically_included_in_matching=(status == "unresolved_optimistically_counted"),
        rejection_certificate=rejection_certificate,
        search_result=search_result,
        consumption=consumption,
        central_reward=central_reward,
        adverse_reward=adverse_reward,
    )


class Gate2MatchingEvidence(FrozenExperimentModel):
    """One exact one-use matching under one economic arm and information scope."""

    arm: Literal["central", "adverse"]
    scope: Literal["total", "unknown"]
    input_edge_ids: tuple[StrictStr, ...]
    unresolved_edge_ids_in_graph: tuple[StrictStr, ...]
    selected_edge_ids: tuple[StrictStr, ...]
    raw_reward_micro_units: StrictInt = Field(ge=0)
    cap_micro_units: StrictInt = Field(ge=0)
    capped_reward_micro_units: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_canonical_matching_evidence(self):
        for values in (
            self.input_edge_ids,
            self.unresolved_edge_ids_in_graph,
            self.selected_edge_ids,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("Gate 2 matching edge IDs must be sorted and unique")
        if not set(self.unresolved_edge_ids_in_graph).issubset(self.input_edge_ids):
            raise ValueError("Gate 2 unresolved edges are absent from the optimistic graph")
        if not set(self.selected_edge_ids).issubset(self.input_edge_ids):
            raise ValueError("Gate 2 selected edges are absent from the matching graph")
        if self.capped_reward_micro_units != min(self.raw_reward_micro_units, self.cap_micro_units):
            raise ValueError("Gate 2 matching reward does not respect the Gate 1 cap")
        return self


class Gate2StreamResult(FrozenExperimentModel):
    """Four capped opportunity matchings for one authenticated Gate 1 stream."""

    model_config = FrozenExperimentModel.model_config | {"revalidate_instances": "always"}

    schema_version: Literal["yieldforge.m11-gate2-stream.v1"] = "yieldforge.m11-gate2-stream.v1"
    result_id: StrictStr = Field(pattern=r"^yfm11g2s-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    stream_id: StrictStr = Field(min_length=1)
    corpus_id: Literal["lectra-m3-m4", "loco-2dics"]
    baseline_cost: StrictFloat = Field(gt=0)
    lower_bound_cost: StrictFloat = Field(ge=0)
    gate1_gap_micro_units: StrictInt = Field(ge=0)
    edges: tuple[Gate2EdgeEvidence, ...]
    central_total: Gate2MatchingEvidence
    central_unknown: Gate2MatchingEvidence
    adverse_total: Gate2MatchingEvidence
    adverse_unknown: Gate2MatchingEvidence
    central_savings_percent: StrictFloat = Field(ge=0)
    central_unknown_points: StrictFloat = Field(ge=0)
    adverse_savings_percent: StrictFloat = Field(ge=0)
    adverse_unknown_points: StrictFloat = Field(ge=0)
    certified_no_fit_count: StrictInt = Field(ge=0)
    fit_witnessed_count: StrictInt = Field(ge=0)
    unresolved_optimistically_counted: StrictInt = Field(ge=0)
    blocking_error_count: StrictInt = Field(ge=0)
    all_unresolved_edges_optimistically_included: StrictBool

    @model_validator(mode="after")
    def require_recomputed_stream_result(self):
        if any(item.stream_id != self.stream_id for item in self.edges):
            raise ValueError("Gate 2 stream result contains a cross-stream edge")
        if tuple(item.edge_id for item in self.edges) != tuple(
            sorted(item.edge_id for item in self.edges)
        ) or len({item.edge_id for item in self.edges}) != len(self.edges):
            raise ValueError("Gate 2 stream edges must be sorted and unique")
        expected_gap = _cost_micro_units(
            _decimal(self.baseline_cost) - _decimal(self.lower_bound_cost)
        )
        if expected_gap < 0 or self.gate1_gap_micro_units != expected_gap:
            raise ValueError("Gate 2 cap differs from authenticated Gate 1 B-L")
        expected_matchings = (
            _matching_evidence(self.edges, arm="central", scope="total", cap=expected_gap),
            _matching_evidence(self.edges, arm="central", scope="unknown", cap=expected_gap),
            _matching_evidence(self.edges, arm="adverse", scope="total", cap=expected_gap),
            _matching_evidence(self.edges, arm="adverse", scope="unknown", cap=expected_gap),
        )
        if expected_matchings != (
            self.central_total,
            self.central_unknown,
            self.adverse_total,
            self.adverse_unknown,
        ):
            raise ValueError("Gate 2 stream matchings differ from exact recomputation")
        expected_metrics = tuple(
            _percent(item.capped_reward_micro_units, self.baseline_cost)
            for item in expected_matchings
        )
        if expected_metrics != (
            self.central_savings_percent,
            self.central_unknown_points,
            self.adverse_savings_percent,
            self.adverse_unknown_points,
        ):
            raise ValueError("Gate 2 stream percentages differ from capped matchings")
        statuses = tuple(item.status for item in self.edges)
        if (
            self.certified_no_fit_count != statuses.count("certified_no_fit")
            or self.fit_witnessed_count != statuses.count("fit_witnessed")
            or self.unresolved_optimistically_counted
            != statuses.count("unresolved_optimistically_counted")
            or self.blocking_error_count != statuses.count("blocking_error")
        ):
            raise ValueError("Gate 2 stream edge counts do not reconcile")
        unresolved_ids = {
            item.edge_id
            for item in self.edges
            if item.status == "unresolved_optimistically_counted"
        }
        included = all(
            unresolved_ids.issubset(set(item.unresolved_edge_ids_in_graph))
            for item in expected_matchings
            if item.scope == "total"
        )
        if self.all_unresolved_edges_optimistically_included is not included:
            raise ValueError("Gate 2 unresolved completeness flag does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.result_id != f"yfm11g2s-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 2 stream result identity differs from evidence")
        return self


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cost_micro_units(value: Decimal) -> int:
    return int((value * Decimal(1_000_000)).to_integral_exact())


def _percent(reward_micro_units: int, baseline_cost: float) -> float:
    value = (
        Decimal(reward_micro_units) * Decimal(100) / Decimal(1_000_000) / _decimal(baseline_cost)
    )
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _matching_evidence(
    edges: tuple[Gate2EdgeEvidence, ...],
    *,
    arm: Literal["central", "adverse"],
    scope: Literal["total", "unknown"],
    cap: int,
) -> Gate2MatchingEvidence:
    usable = tuple(
        item
        for item in edges
        if item.status in _OPTIMISTIC_EDGE_STATUSES and (scope == "total" or item.unknown_at_origin)
    )
    reward_name = f"{arm}_reward"
    matching_edges = tuple(
        MatchingEdge(
            edge_id=item.edge_id,
            origin_id=f"{item.origin_event_id}|{item.origin_remnant_id}",
            target_id=item.target_event_id,
            reward_micro_units=getattr(item, reward_name).reward_micro_units,
        )
        for item in usable
    )
    matched = maximum_reward_matching(
        origin_ids=tuple(sorted({item.origin_id for item in matching_edges})),
        target_ids=tuple(sorted({item.target_id for item in matching_edges})),
        edges=matching_edges,
    )
    input_ids = tuple(sorted(item.edge_id for item in usable))
    unresolved_ids = tuple(
        sorted(
            item.edge_id
            for item in usable
            if item.status == "unresolved_optimistically_counted"
            and item.optimistically_included_in_matching
        )
    )
    return Gate2MatchingEvidence(
        arm=arm,
        scope=scope,
        input_edge_ids=input_ids,
        unresolved_edge_ids_in_graph=unresolved_ids,
        selected_edge_ids=matched.selected_edge_ids,
        raw_reward_micro_units=matched.total_reward_micro_units,
        cap_micro_units=cap,
        capped_reward_micro_units=min(matched.total_reward_micro_units, cap),
    )


def _raw_matching_reward(
    edges: tuple[Gate2EdgeEvidence, ...],
    *,
    arm: Literal["central", "adverse"],
    scope: Literal["total", "known", "unknown"],
) -> int:
    """Return one uncapped optimum used to audit the unknown-future bound.

    For nonnegative optional matching edges, partitioning the graph into known
    and unknown subsets guarantees ``max(total) - max(known) <= max(unknown)``.
    Gate 2 deliberately reports the right-hand side as an optimistic ceiling on
    the later Gate 3 marginal K-F metric, not as an estimate of that metric.
    """

    usable = tuple(
        item
        for item in edges
        if item.status in _OPTIMISTIC_EDGE_STATUSES
        and (
            scope == "total"
            or (scope == "unknown" and item.unknown_at_origin)
            or (scope == "known" and not item.unknown_at_origin)
        )
    )
    reward_name = f"{arm}_reward"
    graph = tuple(
        MatchingEdge(
            edge_id=item.edge_id,
            origin_id=f"{item.origin_event_id}|{item.origin_remnant_id}",
            target_id=item.target_event_id,
            reward_micro_units=getattr(item, reward_name).reward_micro_units,
        )
        for item in usable
    )
    return maximum_reward_matching(
        origin_ids=tuple(sorted({item.origin_id for item in graph})),
        target_ids=tuple(sorted({item.target_id for item in graph})),
        edges=graph,
    ).total_reward_micro_units


def evaluate_gate2_stream(
    *,
    stream_id: str,
    corpus_id: Literal["lectra-m3-m4", "loco-2dics"],
    baseline_cost: float,
    lower_bound_cost: float,
    edges: tuple[Gate2EdgeEvidence, ...],
) -> Gate2StreamResult:
    """Run all four one-use matchings and cap each by authenticated Gate 1 B-L."""

    baseline = _decimal(baseline_cost)
    lower = _decimal(lower_bound_cost)
    if baseline <= 0 or lower < 0 or lower > baseline:
        raise Gate2EvidenceError("Gate 2 requires a valid nonnegative Gate 1 B-L gap")
    canonical_edges = tuple(sorted(edges, key=lambda item: item.edge_id))
    if len({item.edge_id for item in canonical_edges}) != len(canonical_edges):
        raise Gate2EvidenceError("Gate 2 stream repeats an edge identity")
    cap = _cost_micro_units(baseline - lower)
    matchings = (
        _matching_evidence(canonical_edges, arm="central", scope="total", cap=cap),
        _matching_evidence(canonical_edges, arm="central", scope="unknown", cap=cap),
        _matching_evidence(canonical_edges, arm="adverse", scope="total", cap=cap),
        _matching_evidence(canonical_edges, arm="adverse", scope="unknown", cap=cap),
    )
    for arm, total, unknown in (
        ("central", matchings[0], matchings[1]),
        ("adverse", matchings[2], matchings[3]),
    ):
        known_reward = _raw_matching_reward(canonical_edges, arm=arm, scope="known")
        if total.raw_reward_micro_units - known_reward > unknown.raw_reward_micro_units:
            raise Gate2EvidenceError(
                "Gate 2 unknown-only matching failed its optimistic marginal bound"
            )
    statuses = tuple(item.status for item in canonical_edges)
    unresolved_ids = {
        item.edge_id
        for item in canonical_edges
        if item.status == "unresolved_optimistically_counted"
    }
    all_included = unresolved_ids.issubset(
        set(matchings[0].unresolved_edge_ids_in_graph)
    ) and unresolved_ids.issubset(set(matchings[2].unresolved_edge_ids_in_graph))
    semantic = {
        "schema_version": "yieldforge.m11-gate2-stream.v1",
        "stream_id": stream_id,
        "corpus_id": corpus_id,
        "baseline_cost": float(baseline_cost),
        "lower_bound_cost": float(lower_bound_cost),
        "gate1_gap_micro_units": cap,
        "edges": [item.model_dump(mode="json") for item in canonical_edges],
        "central_total": matchings[0].model_dump(mode="json"),
        "central_unknown": matchings[1].model_dump(mode="json"),
        "adverse_total": matchings[2].model_dump(mode="json"),
        "adverse_unknown": matchings[3].model_dump(mode="json"),
        "central_savings_percent": _percent(
            matchings[0].capped_reward_micro_units, float(baseline)
        ),
        "central_unknown_points": _percent(matchings[1].capped_reward_micro_units, float(baseline)),
        "adverse_savings_percent": _percent(
            matchings[2].capped_reward_micro_units, float(baseline)
        ),
        "adverse_unknown_points": _percent(matchings[3].capped_reward_micro_units, float(baseline)),
        "certified_no_fit_count": statuses.count("certified_no_fit"),
        "fit_witnessed_count": statuses.count("fit_witnessed"),
        "unresolved_optimistically_counted": statuses.count("unresolved_optimistically_counted"),
        "blocking_error_count": statuses.count("blocking_error"),
        "all_unresolved_edges_optimistically_included": all_included,
    }
    digest = semantic_sha256(semantic)
    return Gate2StreamResult(
        result_id=f"yfm11g2s-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        schema_version="yieldforge.m11-gate2-stream.v1",
        stream_id=stream_id,
        corpus_id=corpus_id,
        baseline_cost=float(baseline_cost),
        lower_bound_cost=float(lower_bound_cost),
        gate1_gap_micro_units=cap,
        edges=canonical_edges,
        central_total=matchings[0],
        central_unknown=matchings[1],
        adverse_total=matchings[2],
        adverse_unknown=matchings[3],
        central_savings_percent=semantic["central_savings_percent"],
        central_unknown_points=semantic["central_unknown_points"],
        adverse_savings_percent=semantic["adverse_savings_percent"],
        adverse_unknown_points=semantic["adverse_unknown_points"],
        certified_no_fit_count=semantic["certified_no_fit_count"],
        fit_witnessed_count=semantic["fit_witnessed_count"],
        unresolved_optimistically_counted=semantic["unresolved_optimistically_counted"],
        blocking_error_count=semantic["blocking_error_count"],
        all_unresolved_edges_optimistically_included=all_included,
    )


class Gate2AggregateEvidence(FrozenExperimentModel):
    """One equal-stream corpus mean or the equal-corpus pooled mean."""

    aggregate_id: Literal["lectra-m3-m4", "loco-2dics", "equal-corpus-pool"]
    stream_count: StrictInt = Field(gt=0)
    central_savings_percent: StrictFloat = Field(ge=0)
    central_unknown_points: StrictFloat = Field(ge=0)
    adverse_savings_percent: StrictFloat = Field(ge=0)
    adverse_unknown_points: StrictFloat = Field(ge=0)


class Gate2EvaluationConfig(FrozenExperimentModel):
    """Outcome-blind, content-addressed Gate 2 geometry and decision contract."""

    schema_version: Literal["yieldforge.m11-gate2-evaluation-config.v1"] = (
        "yieldforge.m11-gate2-evaluation-config.v1"
    )
    config_id: StrictStr = Field(pattern=r"^yfm11g2c-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    corpus_order: tuple[Literal["lectra-m3-m4"], Literal["loco-2dics"]] = (
        "lectra-m3-m4",
        "loco-2dics",
    )
    confirmation_streams_per_corpus: Literal[20] = 20
    generation_scope: Literal["first_generation_only"] = "first_generation_only"
    lectra_candidate_scope: Literal["every_registered_m3_candidate"] = (
        "every_registered_m3_candidate"
    )
    loco_candidate_scope: Literal["frozen_single_fallback"] = "frozen_single_fallback"
    chronology_rule: Literal[
        "strictly_later_event_and_release_unknown_if_known_after_origin_release"
    ] = "strictly_later_event_and_release_unknown_if_known_after_origin_release"
    material_rule: Literal["exact_five_field_m0_compatibility"] = (
        "exact_five_field_m0_compatibility"
    )
    one_use_rule: Literal["maximum_reward_bipartite_origin_remnant_to_target_event"] = (
        "maximum_reward_bipartite_origin_remnant_to_target_event"
    )
    alternate_candidate_exclusivity: Literal[
        "relaxed_candidate_variants_may_coexist_as_favorable_headroom_overstatement"
    ] = "relaxed_candidate_variants_may_coexist_as_favorable_headroom_overstatement"
    rejection_rule: Literal["necessary_area_and_axis_aligned_bounds_only"] = (
        "necessary_area_and_axis_aligned_bounds_only"
    )
    fit_rule: Literal["bounded_exact_shapely_translation_witness"] = (
        "bounded_exact_shapely_translation_witness"
    )
    geometry_authority: Literal["shapely_authoritative_jagua_optional_prefilter_only"] = (
        "shapely_authoritative_jagua_optional_prefilter_only"
    )
    official_graph_fit_policy: Literal[
        "stage_a_necessary_superset_then_stage_b_bounded_exact_on_survival"
    ] = "stage_a_necessary_superset_then_stage_b_bounded_exact_on_survival"
    decision_invariance: Literal[
        "fit_witness_and_optimistic_unresolved_share_identical_reward_and_matching_role"
    ] = "fit_witness_and_optimistic_unresolved_share_identical_reward_and_matching_role"
    closure_rule: Literal[
        "stage_a_may_abandon_from_favorable_superset_stage_b_exact_attempt_required_to_open_gate3"
    ] = "stage_a_may_abandon_from_favorable_superset_stage_b_exact_attempt_required_to_open_gate3"
    unresolved_rule: Literal[
        "stage_a_unsearched_and_stage_b_bounded_no_witness_are_optimistically_counted"
    ] = "stage_a_unsearched_and_stage_b_bounded_no_witness_are_optimistically_counted"
    reward_rule: Literal["purchase_avoided_minus_return_retrieval_and_elapsed_storage"] = (
        "purchase_avoided_minus_return_retrieval_and_elapsed_storage"
    )
    credit_rule: Literal["omit_scrap_and_terminal_credit"] = "omit_scrap_and_terminal_credit"
    cap_rule: Literal["each_stream_matching_capped_at_authenticated_gate1_B_minus_L"] = (
        "each_stream_matching_capped_at_authenticated_gate1_B_minus_L"
    )
    aggregation_rule: Literal["equal_stream_within_corpus_then_equal_corpus_pool"] = (
        "equal_stream_within_corpus_then_equal_corpus_pool"
    )
    unknown_metric_rule: Literal[
        "standalone_unknown_edge_matching_upper_bounds_total_minus_known_matching"
    ] = "standalone_unknown_edge_matching_upper_bounds_total_minus_known_matching"
    fit_config: RemnantFitConfig
    search_config: LayoutFitSearchConfig
    economic_profiles: tuple[M11EconomicProfile, M11EconomicProfile]
    central_savings_floor_percent: Literal[2.5] = 2.5
    central_unknown_floor_points: Literal[1.5] = 1.5
    adverse_savings_floor_percent: Literal[1.5] = 1.5
    adverse_unknown_floor_points: Literal[0.5] = 0.5
    equality_survives: Literal[True] = True

    @model_validator(mode="after")
    def require_frozen_profiles_and_identity(self):
        if self.economic_profiles != _default_economic_profiles():
            raise ValueError("Gate 2 economic profiles differ from the outcome-blind registry")
        digest = semantic_sha256(self, excluded_fields={"config_id", "content_sha256"})
        if self.config_id != f"yfm11g2c-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 2 config identity differs from its registered semantics")
        return self


def _build_gate2_config(
    *,
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
) -> Gate2EvaluationConfig:
    semantic = {
        "schema_version": "yieldforge.m11-gate2-evaluation-config.v1",
        "corpus_order": ["lectra-m3-m4", "loco-2dics"],
        "confirmation_streams_per_corpus": 20,
        "generation_scope": "first_generation_only",
        "lectra_candidate_scope": "every_registered_m3_candidate",
        "loco_candidate_scope": "frozen_single_fallback",
        "chronology_rule": (
            "strictly_later_event_and_release_unknown_if_known_after_origin_release"
        ),
        "material_rule": "exact_five_field_m0_compatibility",
        "one_use_rule": "maximum_reward_bipartite_origin_remnant_to_target_event",
        "alternate_candidate_exclusivity": (
            "relaxed_candidate_variants_may_coexist_as_favorable_headroom_overstatement"
        ),
        "rejection_rule": "necessary_area_and_axis_aligned_bounds_only",
        "fit_rule": "bounded_exact_shapely_translation_witness",
        "geometry_authority": "shapely_authoritative_jagua_optional_prefilter_only",
        "official_graph_fit_policy": (
            "stage_a_necessary_superset_then_stage_b_bounded_exact_on_survival"
        ),
        "decision_invariance": (
            "fit_witness_and_optimistic_unresolved_share_identical_reward_and_matching_role"
        ),
        "closure_rule": (
            "stage_a_may_abandon_from_favorable_superset_stage_b_exact_attempt_required_to_open_gate3"
        ),
        "unresolved_rule": (
            "stage_a_unsearched_and_stage_b_bounded_no_witness_are_optimistically_counted"
        ),
        "reward_rule": "purchase_avoided_minus_return_retrieval_and_elapsed_storage",
        "credit_rule": "omit_scrap_and_terminal_credit",
        "cap_rule": "each_stream_matching_capped_at_authenticated_gate1_B_minus_L",
        "aggregation_rule": "equal_stream_within_corpus_then_equal_corpus_pool",
        "unknown_metric_rule": (
            "standalone_unknown_edge_matching_upper_bounds_total_minus_known_matching"
        ),
        "fit_config": fit_config.model_dump(mode="json"),
        "search_config": search_config.model_dump(mode="json"),
        "economic_profiles": [
            item.model_dump(mode="json") for item in _default_economic_profiles()
        ],
        "central_savings_floor_percent": 2.5,
        "central_unknown_floor_points": 1.5,
        "adverse_savings_floor_percent": 1.5,
        "adverse_unknown_floor_points": 0.5,
        "equality_survives": True,
    }
    digest = semantic_sha256(semantic)
    return Gate2EvaluationConfig(
        config_id=f"yfm11g2c-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        fit_config=fit_config,
        search_config=search_config,
        economic_profiles=_default_economic_profiles(),
    )


_GATE2_METRIC_FIELDS = (
    "central_savings_percent",
    "central_unknown_points",
    "adverse_savings_percent",
    "adverse_unknown_points",
)


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        raise Gate2EvidenceError("Gate 2 cannot aggregate an empty corpus")
    exact = sum((_decimal(item) for item in values), start=Decimal(0)) / Decimal(len(values))
    return float(exact.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def aggregate_gate2_streams(
    stream_results: tuple[Gate2StreamResult, ...],
) -> tuple[Gate2AggregateEvidence, Gate2AggregateEvidence, Gate2AggregateEvidence]:
    """Aggregate equal stream weight within corpus, then equal total corpus weight."""

    groups = {
        corpus_id: tuple(item for item in stream_results if item.corpus_id == corpus_id)
        for corpus_id in ("lectra-m3-m4", "loco-2dics")
    }
    if any(not values for values in groups.values()):
        raise Gate2EvidenceError("Gate 2 aggregation requires both registered corpora")
    if len({item.stream_id for item in stream_results}) != len(stream_results):
        raise Gate2EvidenceError("Gate 2 aggregation repeats a stream identity")
    corpora = []
    for corpus_id in ("lectra-m3-m4", "loco-2dics"):
        values = groups[corpus_id]
        metrics = {
            field_name: _mean(tuple(getattr(item, field_name) for item in values))
            for field_name in _GATE2_METRIC_FIELDS
        }
        corpora.append(
            Gate2AggregateEvidence(
                aggregate_id=corpus_id,
                stream_count=len(values),
                **metrics,
            )
        )
    pool_metrics = {
        field_name: _mean(tuple(getattr(item, field_name) for item in corpora))
        for field_name in _GATE2_METRIC_FIELDS
    }
    pool = Gate2AggregateEvidence(
        aggregate_id="equal-corpus-pool",
        stream_count=len(stream_results),
        **pool_metrics,
    )
    return corpora[0], corpora[1], pool


def classify_gate2_headroom(
    aggregates: tuple[Gate2AggregateEvidence, ...],
    *,
    blocking_error_count: int,
    all_unresolved_edges_optimistically_included: bool,
) -> Literal["invalid_test", "insufficient_headroom", "gate_2_survived"]:
    """Apply the frozen central/adverse floors; exact equality survives."""

    if type(blocking_error_count) is not int or blocking_error_count < 0:
        raise Gate2EvidenceError("Gate 2 blocking-error count must be a nonnegative integer")
    if type(all_unresolved_edges_optimistically_included) is not bool:
        raise Gate2EvidenceError("Gate 2 unresolved-completeness flag must be boolean")
    if tuple(item.aggregate_id for item in aggregates) != (
        "lectra-m3-m4",
        "loco-2dics",
        "equal-corpus-pool",
    ):
        raise Gate2EvidenceError("Gate 2 aggregates differ from registered order")
    if blocking_error_count or not all_unresolved_edges_optimistically_included:
        return "invalid_test"
    survives = all(
        item.central_savings_percent >= 2.5
        and item.central_unknown_points >= 1.5
        and item.adverse_savings_percent >= 1.5
        and item.adverse_unknown_points >= 0.5
        for item in aggregates
    )
    return "gate_2_survived" if survives else "insufficient_headroom"


Gate2EvaluationStatus = Literal[
    "invalid_test",
    "insufficient_headroom",
    "gate_2_survived",
]


def _require_gate2_stage_closure(
    *,
    status: Gate2EvaluationStatus,
    evaluation_stage: Literal[
        "stage_a_favorable_superset",
        "stage_b_exact_attempted",
    ],
    stream_results: tuple[Gate2StreamResult, ...],
) -> None:
    """Enforce that favorable unsearched edges can stop but never advance M11."""

    unsearched_count = sum(
        edge.resolution_basis == "not_searched_favorable_relaxation"
        for stream in stream_results
        for edge in stream.edges
    )
    if status == "insufficient_headroom" and evaluation_stage != ("stage_a_favorable_superset"):
        raise ValueError("Gate 2 insufficient headroom must close from Stage A")
    if status == "gate_2_survived" and (
        evaluation_stage != "stage_b_exact_attempted" or unsearched_count
    ):
        raise ValueError("Gate 2 cannot open Gate 3 from unsearched favorable relaxation")


class Gate2EvaluationResult(FrozenExperimentModel):
    """Closed Gate 2 branch bound to Gate 1 and every official geometry leaf."""

    model_config = ConfigDict(
        **FrozenExperimentModel.model_config,
        revalidate_instances="always",
    )

    schema_version: Literal["yieldforge.m11-gate2-evaluation-result.v1"] = (
        "yieldforge.m11-gate2-evaluation-result.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm11g2r-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    status: Gate2EvaluationStatus
    evaluation_stage: Literal[
        "stage_a_favorable_superset",
        "stage_b_exact_attempted",
    ]
    contract_id: StrictStr = Field(pattern=r"^yfm11c-[0-9a-f]{24}$")
    contract_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    population_id: StrictStr = Field(pattern=r"^yfm11pop-[0-9a-f]{24}$")
    population_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    source_manifest_id: StrictStr = Field(pattern=r"^yfm11sm-[0-9a-f]{24}$")
    source_manifest_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    gate1_result_id: StrictStr = Field(pattern=r"^yfm11g1r-[0-9a-f]{24}$")
    gate1_result_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    gate1_receipt_id: StrictStr = Field(pattern=r"^yfm11g1a-[0-9a-f]{24}$")
    gate1_receipt_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    gate1_cell_ids: tuple[StrictStr, ...] = Field(min_length=40, max_length=40)
    gate1_cell_content_sha256s: tuple[StrictStr, ...] = Field(
        min_length=40,
        max_length=40,
    )
    m0_contract_id: StrictStr = Field(min_length=1)
    m0_contract_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    m3_input_id: StrictStr = Field(min_length=1)
    m3_input_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    m4_input_id: StrictStr = Field(min_length=1)
    m4_input_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    loco_catalog_id: StrictStr = Field(min_length=1)
    loco_catalog_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    contract: M11ExperimentContract
    config: Gate2EvaluationConfig
    stream_results: tuple[Gate2StreamResult, ...] = Field(min_length=40, max_length=40)
    aggregates: tuple[Gate2AggregateEvidence, ...] = Field(min_length=3, max_length=3)
    blocking_error_count: StrictInt = Field(ge=0)
    unresolved_optimistically_counted: StrictInt = Field(ge=0)
    all_unresolved_edges_optimistically_included: StrictBool
    invalid_reason: M11InvalidReason | None
    verdict: M11VerdictResult | None
    repair_count: StrictInt
    terminal: StrictBool
    opens_gate_3: StrictBool
    retention_authorized: Literal[False] = False
    productization_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_exact_census_roots_branch_and_identity(self):
        canonical_contract = M11ExperimentContract.model_validate(
            self.contract.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        if (
            canonical_contract != self.contract
            or self.contract_id != self.contract.contract_id
            or self.contract_content_sha256 != self.contract.content_sha256
        ):
            raise ValueError("Gate 2 contract differs from its official root binding")
        if self.repair_count not in (0, 1):
            raise ValueError("Gate 2 repair count must be the strict integer 0 or 1")
        if len(set(self.gate1_cell_ids)) != 40 or len(set(self.gate1_cell_content_sha256s)) != 40:
            raise ValueError("Gate 2 Gate 1 cell bindings must be unique and complete")
        expected_stream_ids = tuple(
            stream_id
            for corpus in self.contract.corpora
            for stream_id in corpus.confirmation_stream_ids
        )
        actual_stream_ids = tuple(item.stream_id for item in self.stream_results)
        actual_corpora = tuple(item.corpus_id for item in self.stream_results)
        if (
            len(expected_stream_ids) != 40
            or actual_stream_ids != expected_stream_ids
            or actual_corpora != ("lectra-m3-m4",) * 20 + ("loco-2dics",) * 20
        ):
            raise ValueError("Gate 2 stream census/order differs from the official 20+20 set")
        recomputed_aggregates = aggregate_gate2_streams(self.stream_results)
        if self.aggregates != recomputed_aggregates:
            raise ValueError("Gate 2 aggregates differ from exact stream recomputation")
        expected_blockers = sum(item.blocking_error_count for item in self.stream_results)
        expected_unresolved = sum(
            item.unresolved_optimistically_counted for item in self.stream_results
        )
        expected_complete = all(
            item.all_unresolved_edges_optimistically_included for item in self.stream_results
        )
        expected_status = classify_gate2_headroom(
            self.aggregates,
            blocking_error_count=expected_blockers,
            all_unresolved_edges_optimistically_included=expected_complete,
        )
        if (
            self.blocking_error_count != expected_blockers
            or self.unresolved_optimistically_counted != expected_unresolved
            or self.all_unresolved_edges_optimistically_included is not expected_complete
            or self.status != expected_status
        ):
            raise ValueError("Gate 2 branch inputs differ from exact stream evidence")
        _require_gate2_stage_closure(
            status=self.status,
            evaluation_stage=self.evaluation_stage,
            stream_results=self.stream_results,
        )
        if self.status == "insufficient_headroom":
            expected_verdict = build_m11_verdict(
                contract=self.contract,
                evidence_state=M11EvidenceState.INSUFFICIENT_HEADROOM,
                repair_count=self.repair_count,
            )
            valid_branch = (
                self.terminal
                and not self.opens_gate_3
                and self.invalid_reason is None
                and self.verdict == expected_verdict
            )
        elif self.status == "gate_2_survived":
            valid_branch = (
                not self.terminal
                and self.opens_gate_3
                and self.invalid_reason is None
                and self.verdict is None
            )
        else:
            expected_reason = M11InvalidReason(
                category=(M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT),
                reason_code="software_implementation_defect",
                repair_eligible=True,
            )
            expected_verdict = build_m11_verdict(
                contract=self.contract,
                evidence_state=M11EvidenceState.INVALID_TEST,
                repair_count=self.repair_count,
                invalid_reason=expected_reason,
            )
            valid_branch = (
                self.terminal
                and not self.opens_gate_3
                and self.invalid_reason == expected_reason
                and self.verdict == expected_verdict
            )
        if not valid_branch:
            raise ValueError("Gate 2 result branch differs from its registered decision rule")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.result_id != f"yfm11g2r-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 2 result identity differs from its complete evidence")
        return self


def assess_gate2_edge(
    origin: Gate2Origin,
    target: Gate2Target,
    *,
    fit_config: RemnantFitConfig | None = None,
    search_config: LayoutFitSearchConfig | None = None,
    rules: ResidualRuleSet | None = None,
    economic_profiles: tuple[M11EconomicProfile, M11EconomicProfile] | None = None,
) -> Gate2EdgeEvidence:
    """Apply safe necessary filters before any bounded exact witness search."""

    if (
        origin.stream_id != target.stream_id
        or origin.event_position >= target.event_position
        or origin.released_at >= target.released_at
    ):
        raise Gate2EvidenceError(
            "Gate 2 edges require one strictly later target in the same stream"
        )
    config = fit_config or RemnantFitConfig()
    try:
        layout = prepare_layout_footprint(
            target.problem,
            target.candidate,
            config,
        )
    except (TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="layout_preparation_failed",
            rejection_certificate=None,
        )
    try:
        certificate = certify_translation_impossible(
            layout,
            origin.remnant,
            material=target.material,
            fit_config=config,
        )
    except (TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="necessary_filter_failed",
            rejection_certificate=None,
        )
    if certificate.impossible:
        persisted = Gate2RejectionCertificate(**asdict(certificate))
        return _build_edge(
            origin,
            target,
            status="certified_no_fit",
            rejection_certificate=persisted,
        )
    if rules is None:
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="missing_residual_rules",
            rejection_certificate=None,
        )
    try:
        search = search_layout_translation(
            origin.remnant,
            target.problem,
            target.candidate,
            material=target.material,
            fit_config=config,
            search_config=search_config or LayoutFitSearchConfig(),
            prepared_layout=layout,
        )
    except (TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="search_failed",
            rejection_certificate=None,
        )
    central_profile, adverse_profile = economic_profiles or _default_economic_profiles()
    try:
        central_reward = _reward(origin, target, central_profile)
        adverse_reward = _reward(origin, target, adverse_profile)
    except (ArithmeticError, TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="reward_not_safely_computable",
            rejection_certificate=None,
            search_result=search,
        )
    if search.status is LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH:
        return _build_edge(
            origin,
            target,
            status="unresolved_optimistically_counted",
            rejection_certificate=None,
            search_result=search,
            central_reward=central_reward,
            adverse_reward=adverse_reward,
        )
    if search.translation is None:  # pragma: no cover - enforced by the strict result model
        raise Gate2EvidenceError("Gate 2 fit search omitted its translation witness")
    try:
        consumed = _consume_layout_with_exact_delta(
            origin.remnant,
            target.problem,
            target.candidate,
            search.translation,
            material=target.material,
            rules=rules,
            fit_config=config,
            reroot_standard_sheet=False,
        )
    except (TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="fit_consumption_failed",
            rejection_certificate=None,
            search_result=search,
        )
    return _build_edge(
        origin,
        target,
        status="fit_witnessed",
        rejection_certificate=None,
        search_result=search,
        consumption=_consumption_evidence(search.translation, consumed),
        central_reward=central_reward,
        adverse_reward=adverse_reward,
    )


def _assess_gate2_necessary_bound(
    origin: Gate2Origin,
    target: Gate2Target,
    *,
    fit_config: RemnantFitConfig,
    economic_profiles: tuple[M11EconomicProfile, M11EconomicProfile],
) -> Gate2EdgeEvidence:
    """Apply only safe no-fit certificates, then count every survivor favorably.

    This is the official whole-graph path. A witnessed fit and an unresolved
    survivor receive the same chronology-dependent reward and matching endpoints,
    so exact search cannot change a stream reward, aggregate, or Gate 2 branch.
    """

    if (
        origin.stream_id != target.stream_id
        or origin.event_position >= target.event_position
        or origin.released_at >= target.released_at
    ):
        raise Gate2EvidenceError(
            "Gate 2 edges require one strictly later target in the same stream"
        )
    try:
        layout = prepare_layout_footprint(
            target.problem,
            target.candidate,
            fit_config,
        )
    except (TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="layout_preparation_failed",
            rejection_certificate=None,
        )
    try:
        certificate = certify_translation_impossible(
            layout,
            origin.remnant,
            material=target.material,
            fit_config=fit_config,
        )
    except (TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="necessary_filter_failed",
            rejection_certificate=None,
        )
    if certificate.impossible:
        return _build_edge(
            origin,
            target,
            status="certified_no_fit",
            rejection_certificate=Gate2RejectionCertificate(**asdict(certificate)),
        )
    central_profile, adverse_profile = economic_profiles
    try:
        central_reward = _reward(origin, target, central_profile)
        adverse_reward = _reward(origin, target, adverse_profile)
    except (ArithmeticError, TypeError, ValueError):
        return _build_edge(
            origin,
            target,
            status="blocking_error",
            blocking_error_code="reward_not_safely_computable",
            rejection_certificate=None,
        )
    return _build_edge(
        origin,
        target,
        status="unresolved_optimistically_counted",
        resolution_basis="not_searched_favorable_relaxation",
        rejection_certificate=None,
        central_reward=central_reward,
        adverse_reward=adverse_reward,
    )


def _parse_gate2_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError) as error:
        raise Gate2EvidenceError("Gate 2 event timestamp is not canonical UTC") from error
    return parsed


def _opening_geometry_variants(
    context: _OfficialGate2Context,
    *,
    payload: M11Payload,
    opening,
    cache: dict[tuple[str, str, str], _ReconstructedPayload],
) -> tuple[_ReconstructedPayload, ...]:
    """Reconstruct every authenticated candidate option for one fresh opening."""

    expected_options = tuple(
        (item.candidate_id, item.content_sha256) for item in payload.candidate_references
    )
    if tuple(opening.candidate_options) != expected_options:
        raise Gate2EvidenceError(
            "Gate 2 opening candidate options differ from the official payload"
        )
    variants = []
    for candidate_id, _candidate_content_sha256 in opening.candidate_options:
        key = (payload.payload_id, candidate_id, opening.material_group)
        reconstructed = cache.get(key)
        if reconstructed is None:
            reconstructed = _reconstruct_official_payload(
                context,
                payload=payload,
                selected_candidate_id=candidate_id,
                material_key=opening.material_group,
            )
            cache[key] = reconstructed
        variants.append(reconstructed)
    if not variants:
        raise Gate2EvidenceError("Gate 2 opening has no authenticated geometry variants")
    return tuple(variants)


def _event_geometry_bindings(
    context: _OfficialGate2Context,
    *,
    stream_id: str,
    event,
    opening,
    payload: M11Payload,
    reconstruction_cache: dict[tuple[str, str, str], _ReconstructedPayload],
) -> tuple[
    tuple[Gate2Origin, ...],
    tuple[Gate2Target, ...],
]:
    """Bind all candidate variants to one exact event without selecting an outcome."""

    if (
        opening.event_position != event.position
        or opening.event_id != event.event_id
        or opening.payload_id != event.payload_id
        or opening.material_group != event.material_key
        or opening.source_kind != payload.source_kind
    ):
        raise Gate2EvidenceError("Gate 2 opening differs from its official event")
    try:
        reference_area = float(Fraction(opening.reference_area_exact))
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise Gate2EvidenceError("Gate 2 opening reference area is not exact") from error
    if reference_area <= 0:
        raise Gate2EvidenceError("Gate 2 opening reference area must be positive")
    known_at = _parse_gate2_timestamp(event.known_at)
    released_at = _parse_gate2_timestamp(event.released_at)
    variants = _opening_geometry_variants(
        context,
        payload=payload,
        opening=opening,
        cache=reconstruction_cache,
    )
    origins = []
    targets = []
    option_content_by_id = dict(opening.candidate_options)
    for variant in variants:
        candidate_id = variant.candidate.candidate_id
        binding = "sha256:" + semantic_sha256(
            {
                "stream_id": stream_id,
                "event": event.model_dump(mode="json"),
                "opening": opening.model_dump(mode="json"),
                "candidate_id": candidate_id,
                "candidate_content_sha256": option_content_by_id[candidate_id],
                "payload_geometry_binding_sha256": variant.source_binding_sha256,
            }
        )
        target = Gate2Target(
            stream_id=stream_id,
            event_position=event.position,
            event_id=event.event_id,
            known_at=known_at,
            released_at=released_at,
            material_key=event.material_key,
            opening_id=opening.opening_id,
            opening_content_sha256=opening.content_sha256,
            purchase_cost=opening.purchase_cost,
            source_kind=variant.source_kind,
            source_binding_sha256=binding,
            problem=variant.problem,
            candidate=variant.candidate,
        )
        targets.append(target)
        for source_remnant in variant.origin_remnants:
            remnant = _reroot_remnant(
                source_remnant,
                material=_event_material(event.material_key),
                root_domain=(
                    f"{stream_id}|{event.event_id}|{opening.opening_id}|"
                    f"{candidate_id}|{source_remnant.remnant_id}"
                ),
            )
            origin = Gate2Origin(
                stream_id=stream_id,
                event_position=event.position,
                event_id=event.event_id,
                released_at=released_at,
                material_key=event.material_key,
                reference_area=reference_area,
                source_kind=variant.source_kind,
                source_binding_sha256=binding,
                remnant=remnant,
            )
            origins.append(origin)
    return tuple(origins), tuple(targets)


def _evaluate_official_stream(
    context: _OfficialGate2Context,
    *,
    stream,
    cell,
    payload_by_id: dict[str, M11Payload],
    reconstruction_cache: dict[tuple[str, str, str], _ReconstructedPayload],
    evaluation_stage: Literal[
        "stage_a_favorable_superset",
        "stage_b_exact_attempted",
    ],
) -> Gate2StreamResult:
    """Build the complete favorable first-generation graph for one stream."""

    if (
        stream.stream_id != cell.stream_id
        or stream.corpus_id != cell.corpus_id
        or tuple(item.event_id for item in stream.events)
        != tuple(item.event_id for item in cell.baseline.openings)
    ):
        raise Gate2EvidenceError("Gate 2 stream differs from its authenticated Gate 1 cell")
    event_origins = []
    event_targets = []
    for event, opening in zip(
        stream.events,
        cell.baseline.openings,
        strict=True,
    ):
        try:
            payload = payload_by_id[event.payload_id]
        except KeyError as error:
            raise Gate2EvidenceError("Gate 2 event references an absent payload") from error
        origins, targets = _event_geometry_bindings(
            context,
            stream_id=stream.stream_id,
            event=event,
            opening=opening,
            payload=payload,
            reconstruction_cache=reconstruction_cache,
        )
        event_origins.extend(origins)
        event_targets.extend(targets)
    edges = []
    for origin in event_origins:
        for target in event_targets:
            if target.event_position <= origin.event_position:
                continue
            edges.append(
                _assess_gate2_necessary_bound(
                    origin,
                    target,
                    fit_config=context.fit_config,
                    economic_profiles=_default_economic_profiles(),
                )
                if evaluation_stage == "stage_a_favorable_superset"
                else assess_gate2_edge(
                    origin,
                    target,
                    fit_config=context.fit_config,
                    search_config=context.search_config,
                    rules=context.rules,
                    economic_profiles=_default_economic_profiles(),
                )
            )
    return evaluate_gate2_stream(
        stream_id=stream.stream_id,
        corpus_id=stream.corpus_id,
        baseline_cost=cell.baseline_feasible_cost,
        lower_bound_cost=cell.lower_bound.lower_bound_cost,
        edges=tuple(edges),
    )


def _build_gate2_evaluation_result(
    *,
    context: _OfficialGate2Context,
    gate1_result: Gate1EvaluationResult,
    stream_results: tuple[Gate2StreamResult, ...],
    evaluation_stage: Literal[
        "stage_a_favorable_superset",
        "stage_b_exact_attempted",
    ],
) -> Gate2EvaluationResult:
    """Seal complete authenticated Gate 2 evidence into its forced branch."""

    receipt = gate1_result.audit_receipt
    if receipt is None:
        raise Gate2EvidenceError("Gate 2 requires the complete Gate 1 audit receipt")
    aggregates = aggregate_gate2_streams(stream_results)
    blockers = sum(item.blocking_error_count for item in stream_results)
    unresolved = sum(item.unresolved_optimistically_counted for item in stream_results)
    complete = all(item.all_unresolved_edges_optimistically_included for item in stream_results)
    status = classify_gate2_headroom(
        aggregates,
        blocking_error_count=blockers,
        all_unresolved_edges_optimistically_included=complete,
    )
    invalid_reason = (
        M11InvalidReason(
            category=M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT,
            reason_code="software_implementation_defect",
            repair_eligible=True,
        )
        if status == "invalid_test"
        else None
    )
    verdict = (
        build_m11_verdict(
            contract=gate1_result.contract,
            evidence_state=(
                M11EvidenceState.INVALID_TEST
                if status == "invalid_test"
                else M11EvidenceState.INSUFFICIENT_HEADROOM
            ),
            repair_count=gate1_result.repair_count,
            invalid_reason=invalid_reason,
        )
        if status != "gate_2_survived"
        else None
    )
    config = _build_gate2_config(
        fit_config=context.fit_config,
        search_config=context.search_config,
    )
    semantic = {
        "schema_version": "yieldforge.m11-gate2-evaluation-result.v1",
        "status": status,
        "evaluation_stage": evaluation_stage,
        "contract_id": gate1_result.contract_id,
        "contract_content_sha256": gate1_result.contract_content_sha256,
        "population_id": gate1_result.population_id,
        "population_content_sha256": gate1_result.population_content_sha256,
        "source_manifest_id": gate1_result.source_manifest_id,
        "source_manifest_content_sha256": gate1_result.source_manifest_content_sha256,
        "gate1_result_id": gate1_result.result_id,
        "gate1_result_content_sha256": gate1_result.content_sha256,
        "gate1_receipt_id": receipt.receipt_id,
        "gate1_receipt_content_sha256": receipt.content_sha256,
        "gate1_cell_ids": list(receipt.cell_ids),
        "gate1_cell_content_sha256s": list(receipt.cell_content_sha256s),
        "m0_contract_id": context.m0.contract_id,
        "m0_contract_content_sha256": context.m0.content_sha256,
        "m3_input_id": context.gate1.m3_input.input_id,
        "m3_input_content_sha256": context.gate1.m3_input.content_sha256,
        "m4_input_id": context.m4.input_id,
        "m4_input_content_sha256": context.m4.content_sha256,
        "loco_catalog_id": context.gate1.loco_catalog.catalog_id,
        "loco_catalog_content_sha256": context.gate1.loco_catalog.content_sha256,
        "contract": gate1_result.contract.model_dump(mode="json"),
        "config": config.model_dump(mode="json"),
        "stream_results": [item.model_dump(mode="json") for item in stream_results],
        "aggregates": [item.model_dump(mode="json") for item in aggregates],
        "blocking_error_count": blockers,
        "unresolved_optimistically_counted": unresolved,
        "all_unresolved_edges_optimistically_included": complete,
        "invalid_reason": (
            None if invalid_reason is None else invalid_reason.model_dump(mode="json")
        ),
        "verdict": None if verdict is None else verdict.model_dump(mode="json"),
        "repair_count": gate1_result.repair_count,
        "terminal": status != "gate_2_survived",
        "opens_gate_3": status == "gate_2_survived",
        "retention_authorized": False,
        "productization_authorized": False,
    }
    digest = semantic_sha256(semantic)
    return Gate2EvaluationResult(
        result_id=f"yfm11g2r-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        status=status,
        evaluation_stage=evaluation_stage,
        contract_id=gate1_result.contract_id,
        contract_content_sha256=gate1_result.contract_content_sha256,
        population_id=gate1_result.population_id,
        population_content_sha256=gate1_result.population_content_sha256,
        source_manifest_id=gate1_result.source_manifest_id,
        source_manifest_content_sha256=gate1_result.source_manifest_content_sha256,
        gate1_result_id=gate1_result.result_id,
        gate1_result_content_sha256=gate1_result.content_sha256,
        gate1_receipt_id=receipt.receipt_id,
        gate1_receipt_content_sha256=receipt.content_sha256,
        gate1_cell_ids=receipt.cell_ids,
        gate1_cell_content_sha256s=receipt.cell_content_sha256s,
        m0_contract_id=context.m0.contract_id,
        m0_contract_content_sha256=context.m0.content_sha256,
        m3_input_id=context.gate1.m3_input.input_id,
        m3_input_content_sha256=context.gate1.m3_input.content_sha256,
        m4_input_id=context.m4.input_id,
        m4_input_content_sha256=context.m4.content_sha256,
        loco_catalog_id=context.gate1.loco_catalog.catalog_id,
        loco_catalog_content_sha256=context.gate1.loco_catalog.content_sha256,
        contract=gate1_result.contract,
        config=config,
        stream_results=stream_results,
        aggregates=aggregates,
        blocking_error_count=blockers,
        unresolved_optimistically_counted=unresolved,
        all_unresolved_edges_optimistically_included=complete,
        invalid_reason=invalid_reason,
        verdict=verdict,
        repair_count=gate1_result.repair_count,
        terminal=status != "gate_2_survived",
        opens_gate_3=status == "gate_2_survived",
    )


def evaluate_official_gate2(
    *,
    repository_root: Path,
    gate1_result: Gate1EvaluationResult,
) -> Gate2EvaluationResult:
    """Authenticate Gate 1 and execute the two-stage forty-stream Gate 2."""

    try:
        canonical_gate1 = authenticate_official_gate1_evaluation(
            gate1_result,
            repository_root=Path(repository_root).resolve(),
        )
    except (AttributeError, OSError, TypeError, ValueError, ValidationError) as error:
        raise Gate2EvidenceError("Gate 2 prerequisite Gate 1 failed authentication") from error
    if canonical_gate1.status != "gate_1_survived" or not canonical_gate1.opens_gate_2:
        raise Gate2EvidenceError("Gate 2 prerequisite did not exactly open Gate 2")
    context = _load_official_gate2_context(repository_root)
    if (
        context.gate1.bundle.contract != canonical_gate1.contract
        or context.gate1.bundle.population.population_id != canonical_gate1.population_id
        or context.gate1.bundle.population.content_sha256
        != canonical_gate1.population_content_sha256
        or context.gate1.source_manifest.source_manifest_id != canonical_gate1.source_manifest_id
        or context.gate1.source_manifest.content_sha256
        != canonical_gate1.source_manifest_content_sha256
    ):
        raise Gate2EvidenceError("Gate 2 official roots differ from authenticated Gate 1")
    receipt = canonical_gate1.audit_receipt
    if receipt is None:  # pragma: no cover - authenticated surviving Gate 1 enforces this
        raise Gate2EvidenceError("Gate 2 authenticated Gate 1 omitted its receipt")
    stream_by_id = {item.stream_id: item for item in context.gate1.bundle.population.streams}
    payload_by_id = {item.payload_id: item for item in context.gate1.bundle.population.payloads}
    reconstruction_cache: dict[tuple[str, str, str], _ReconstructedPayload] = {}

    def run_stage(
        stage: Literal[
            "stage_a_favorable_superset",
            "stage_b_exact_attempted",
        ],
    ) -> tuple[Gate2StreamResult, ...]:
        values = []
        for cell in receipt.confirmation_cells:
            try:
                stream = stream_by_id[cell.stream_id]
            except KeyError as error:
                raise Gate2EvidenceError("Gate 2 receipt references an absent stream") from error
            values.append(
                _evaluate_official_stream(
                    context,
                    stream=stream,
                    cell=cell,
                    payload_by_id=payload_by_id,
                    reconstruction_cache=reconstruction_cache,
                    evaluation_stage=stage,
                )
            )
        return tuple(values)

    stage_a = run_stage("stage_a_favorable_superset")
    stage_a_aggregates = aggregate_gate2_streams(stage_a)
    stage_a_status = classify_gate2_headroom(
        stage_a_aggregates,
        blocking_error_count=sum(item.blocking_error_count for item in stage_a),
        all_unresolved_edges_optimistically_included=all(
            item.all_unresolved_edges_optimistically_included for item in stage_a
        ),
    )
    if stage_a_status != "gate_2_survived":
        return _build_gate2_evaluation_result(
            context=context,
            gate1_result=canonical_gate1,
            stream_results=stage_a,
            evaluation_stage="stage_a_favorable_superset",
        )
    stage_b = run_stage("stage_b_exact_attempted")
    return _build_gate2_evaluation_result(
        context=context,
        gate1_result=canonical_gate1,
        stream_results=stage_b,
        evaluation_stage="stage_b_exact_attempted",
    )


def authenticate_official_gate2_evaluation(
    result: Gate2EvaluationResult,
    *,
    repository_root: Path,
    gate1_result: Gate1EvaluationResult,
) -> Gate2EvaluationResult:
    """Strictly reconstruct and exact-compare an official Gate 2 result."""

    try:
        supplied = Gate2EvaluationResult.model_validate(
            result.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValueError, ValidationError) as error:
        raise Gate2EvidenceError("supplied Gate 2 result is not a valid artifact") from error
    canonical = evaluate_official_gate2(
        repository_root=repository_root,
        gate1_result=gate1_result,
    )
    if supplied != canonical:
        raise Gate2EvidenceError(
            "supplied Gate 2 result differs from freshly reconstructed official evidence"
        )
    return canonical


__all__ = [
    "Gate2AggregateEvidence",
    "Gate2EdgeEvidence",
    "Gate2EvaluationConfig",
    "Gate2EvaluationResult",
    "Gate2EvidenceError",
    "Gate2Origin",
    "Gate2StreamResult",
    "Gate2Target",
    "aggregate_gate2_streams",
    "assess_gate2_edge",
    "authenticate_official_gate2_evaluation",
    "classify_gate2_headroom",
    "evaluate_gate2_stream",
    "evaluate_official_gate2",
]
