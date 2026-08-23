"""Canonical M4 remnant-reuse input preparation and evidence binding."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import secrets
import stat
from collections import Counter
from pathlib import Path
from typing import Literal, Self

import shapely
from pydantic import Field, StrictFloat, StrictInt, StrictStr, ValidationError, model_validator
from shapely import Polygon, box

from yieldforge.domain import Part, ProjectionMode
from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    M0ExperimentContract,
    semantic_sha256,
)
from yieldforge.experiments.residual_geometry import (
    M3ResidualGeometryResult,
    M3ResidualInputPack,
    evaluate_m3_residual_geometry,
)
from yieldforge.residuals.contracts import ResidualGeometryError, ResidualRuleName, rule_set_from_m0
from yieldforge.residuals.geometry import extract_candidate_residual, geometry_sha256
from yieldforge.reuse.contracts import (
    CanonicalPolygon,
    FitPlacement,
    FitSearchConfig,
    FitSearchRejectionCount,
    FitSearchResult,
    FitSearchStatus,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantFitResult,
    RemnantLineage,
    RemnantStock,
    ReuseGeometryError,
    canonical_polygon_record,
    derive_remnant_id,
)
from yieldforge.reuse.geometry import consume_remnant, transform_part, validate_fit_placement
from yieldforge.reuse.search import search_fit_witness

_MAX_M4_INPUT_BYTES = 256 * 1024 * 1024
REGISTERED_M4_SEARCH_CONFIG = FitSearchConfig(
    grid_columns=17,
    grid_rows=17,
    maximum_candidates=4096,
)


class M4EvidenceError(ValueError):
    """M0/M3 evidence could not support a canonical M4 input."""


def _root_stock_id(
    *,
    m3_input_id: str,
    m3_input_sha256: str,
    m3_result_id: str,
    m3_result_sha256: str,
    tasks_index: int,
    candidate_id: str,
) -> str:
    payload = {
        "candidate_id": candidate_id,
        "m3_input_id": m3_input_id,
        "m3_input_sha256": m3_input_sha256,
        "m3_result_id": m3_result_id,
        "m3_result_sha256": m3_result_sha256,
        "tasks_index": tasks_index,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"yfms-{hashlib.sha256(encoded).hexdigest()[:24]}"


class M4OriginRemnant(FrozenExperimentModel):
    """One primary-retained M3 component reconstructed as reusable stock."""

    origin_tasks_index: StrictInt = Field(ge=0)
    origin_candidate_position: StrictInt = Field(ge=0, le=1)
    origin_candidate_id: StrictStr = Field(min_length=1)
    source_component_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    remnant: RemnantStock

    @model_validator(mode="after")
    def require_exact_origin_binding(self) -> Self:
        if self.remnant.lineage.source_candidate_id != self.origin_candidate_id:
            raise ValueError("origin candidate does not match remnant lineage")
        if self.source_component_sha256 != self.remnant.geometry.polygon_sha256:
            raise ValueError("origin component does not match remnant geometry")
        if self.source_component_sha256 != self.remnant.lineage.source_component_sha256:
            raise ValueError("origin component does not match remnant lineage")
        return self


class M4FuturePartRole(FrozenExperimentModel):
    """One projected source part eligible as generated later demand."""

    tasks_index: StrictInt = Field(ge=0)
    dataset_id: StrictStr = Field(min_length=1)
    source_slice_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_projection_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_projection_mode: Literal["source_as_recorded"] = "source_as_recorded"
    acknowledged_assumption_codes: tuple[StrictStr, ...]
    source_flip_part_count: StrictInt = Field(ge=0)
    source_sheet_length: StrictFloat = Field(gt=0)
    source_strip_height: StrictFloat = Field(gt=0)
    part: Part
    reference_placement: FitPlacement

    @model_validator(mode="after")
    def require_valid_explicit_source_part(self) -> Self:
        if self.part.demand != 1:
            raise ValueError("future source part must have explicit demand one")
        orientations = self.part.allowed_orientations
        if orientations is None or not orientations:
            raise ValueError("future source part requires explicit allowed rotations")
        if not all(math.isfinite(value) for value in orientations):
            raise ValueError("future source part rotations must be finite")
        if self.reference_placement.part_id != self.part.id:
            raise ValueError("future source reference placement changes part identity")
        if not any(
            abs(math.remainder(self.reference_placement.rotation - value, 360.0)) <= 1e-7
            for value in orientations
        ):
            raise ValueError("future source reference placement uses an unlisted rotation")
        polygon = Polygon(self.part.shape)
        if polygon.is_empty or polygon.area <= 0 or not polygon.is_valid:
            raise ValueError("future source part must be a valid positive-area polygon")
        if any(not math.isfinite(value) for point in self.part.shape for value in point[:2]):
            raise ValueError("future source part coordinates must be finite")
        return self


class M4ReuseInputPack(FrozenExperimentModel):
    """Content-addressed source-observed inputs for bounded M4 witness search."""

    schema_version: Literal["yieldforge.m4-remnant-reuse-input.v1"] = (
        "yieldforge.m4-remnant-reuse-input.v1"
    )
    input_id: StrictStr = Field(pattern=r"^yfri-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m2_result_id: StrictStr = Field(pattern=r"^yfgfr-[0-9a-f]{24}$")
    m2_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m3_input_id: StrictStr = Field(pattern=r"^yfgi-[0-9a-f]{24}$")
    m3_input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m3_result_id: StrictStr = Field(pattern=r"^yfgr-[0-9a-f]{24}$")
    m3_result_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    shapely_version: StrictStr = Field(min_length=1)
    primary_fit_config: RemnantFitConfig
    assumed_material: MaterialIdentity
    search_config: FitSearchConfig
    generated_order_disclaimer: Literal[
        "greater_task_index_is_deterministic_generated_order_not_observed_chronology"
    ] = "greater_task_index_is_deterministic_generated_order_not_observed_chronology"
    origin_remnants: tuple[M4OriginRemnant, ...] = Field(min_length=1)
    future_part_roles: tuple[M4FuturePartRole, ...] = Field(min_length=1)
    claim_ceiling: Literal[
        "remnant_reuse_input_only_not_fit_frequency_savings_physical_recovery_or_commercial_value"
    ] = "remnant_reuse_input_only_not_fit_frequency_savings_physical_recovery_or_commercial_value"

    @model_validator(mode="after")
    def require_complete_content_addressed_input(self) -> Self:
        if self.primary_fit_config.clearance_distance != 0.0:
            raise ValueError("primary M4 input must use zero clearance")
        if self.assumed_material.provenance is not MaterialProvenance.ASSUMED:
            raise ValueError("M4 source material identity must be labeled assumed")

        origin_keys = tuple(
            (
                item.origin_tasks_index,
                item.origin_candidate_position,
                item.source_component_sha256,
            )
            for item in self.origin_remnants
        )
        if origin_keys != tuple(sorted(set(origin_keys))):
            raise ValueError("M4 origin remnants must use registered sorted unique order")
        for origin in self.origin_remnants:
            expected_root = _root_stock_id(
                m3_input_id=self.m3_input_id,
                m3_input_sha256=self.m3_input_sha256,
                m3_result_id=self.m3_result_id,
                m3_result_sha256=self.m3_result_sha256,
                tasks_index=origin.origin_tasks_index,
                candidate_id=origin.origin_candidate_id,
            )
            if origin.remnant.lineage.root_stock_id != expected_root:
                raise ValueError("M4 origin root stock does not bind the M3 evidence")
            if origin.remnant.material != self.assumed_material:
                raise ValueError("M4 origin material does not match the assumed identity")

        future_keys = tuple((item.tasks_index, item.part.id) for item in self.future_part_roles)
        if future_keys != tuple(sorted(set(future_keys))):
            raise ValueError("M4 future parts must use task then part ID order")
        future_task_ids = {item.tasks_index for item in self.future_part_roles}
        if any(origin.origin_tasks_index not in future_task_ids for origin in self.origin_remnants):
            raise ValueError("every M4 origin task must exist in the future-part catalog")

        digest = semantic_sha256(
            self,
            excluded_fields={"input_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M4 input content SHA-256 does not match semantic content")
        if self.input_id != f"yfri-{digest[:24]}":
            raise ValueError("M4 input ID does not match semantic content")
        return self


def _validated_sources(
    m3_input: M3ResidualInputPack,
    m3_result: M3ResidualGeometryResult,
    m0: M0ExperimentContract,
) -> tuple[M3ResidualInputPack, M3ResidualGeometryResult, M0ExperimentContract]:
    try:
        validated_input = M3ResidualInputPack.model_validate_json(
            json.dumps(m3_input.model_dump(mode="json"), allow_nan=False), strict=True
        )
        validated_result = M3ResidualGeometryResult.model_validate_json(
            json.dumps(m3_result.model_dump(mode="json"), allow_nan=False), strict=True
        )
        validated_m0 = M0ExperimentContract.model_validate_json(
            json.dumps(m0.model_dump(mode="json"), allow_nan=False), strict=True
        )
    except (ValidationError, ValueError) as error:
        raise M4EvidenceError("M0 or M3 source evidence is invalid") from error

    if (
        validated_input.m0_contract_id != validated_m0.contract_id
        or validated_input.m0_contract_sha256 != validated_m0.content_sha256
        or validated_result.m0_contract_id != validated_m0.contract_id
        or validated_result.m0_contract_sha256 != validated_m0.content_sha256
    ):
        raise M4EvidenceError("M0 identity does not match the M3 evidence")
    if (
        validated_result.input_id != validated_input.input_id
        or validated_result.input_sha256 != validated_input.content_sha256
    ):
        raise M4EvidenceError("M3 result does not bind the supplied M3 input")
    if (
        validated_result.m2_result_id != validated_input.m2_result_id
        or validated_result.m2_result_sha256 != validated_input.m2_result_sha256
    ):
        raise M4EvidenceError("M2 identity differs between M3 input and result")
    if (
        validated_input.shapely_version != shapely.__version__
        or validated_result.shapely_version != shapely.__version__
    ):
        raise M4EvidenceError("M3 Shapely identity does not match the M4 runtime")
    if (
        validated_input.expected_task_ids != validated_result.expected_task_ids
        or validated_result.summary.technical_decision != "pass"
    ):
        raise M4EvidenceError("M3 result does not contain the passed registered population")
    recomputed_result = evaluate_m3_residual_geometry(validated_input, validated_m0)
    if recomputed_result != validated_result:
        raise M4EvidenceError("supplied M3 result does not match the recomputed M3 result")
    return validated_input, validated_result, validated_m0


def _assumed_m4_material() -> MaterialIdentity:
    return MaterialIdentity(
        material_code="m4-assumed-uniform",
        grade="m4-assumed-uniform",
        thickness="m4-assumed-uniform",
        surface="m4-assumed-uniform",
        grain="m4-assumed-uniform",
        provenance=MaterialProvenance.ASSUMED,
    )


def _origin_remnants(
    m3_input: M3ResidualInputPack,
    m3_result: M3ResidualGeometryResult,
    m0: M0ExperimentContract,
    material: MaterialIdentity,
) -> tuple[M4OriginRemnant, ...]:
    results_by_task = {item.tasks_index: item for item in m3_result.task_results}
    origins: list[M4OriginRemnant] = []
    rules = rule_set_from_m0(m0.remnant_eligibility)
    for pair in sorted(m3_input.task_pairs, key=lambda item: item.tasks_index):
        task_result = results_by_task.get(pair.tasks_index)
        if task_result is None or not task_result.valid:
            raise M4EvidenceError(
                "M3 task result is absent or invalid during remnant reconstruction"
            )
        observations = (task_result.first_observation, task_result.second_observation)
        expected_ids = tuple(item.candidate.candidate_id for item in pair.selected_candidates)
        if (task_result.first_candidate_id, task_result.second_candidate_id) != expected_ids:
            raise M4EvidenceError("M3 task result changes selected candidate order")

        for position, (selected, recorded) in enumerate(
            zip(pair.selected_candidates, observations, strict=True)
        ):
            try:
                extraction = extract_candidate_residual(
                    pair.problem,
                    selected.candidate,
                    rules,
                    m3_input.primary_geometry_config,
                )
            except ResidualGeometryError as error:
                raise M4EvidenceError("selected M3 candidate residual is invalid") from error
            if extraction.observation != recorded:
                raise M4EvidenceError("recomputed M3 residual does not match recorded evidence")
            primary = tuple(
                item
                for item in extraction.observation.classifications
                if item.rule_name is ResidualRuleName.PRIMARY
            )
            if len(primary) != 1:
                raise M4EvidenceError("M3 residual does not contain exactly one primary rule")
            components = {
                geometry_sha256(component): component
                for component in extraction.component_geometries
            }
            for component_hash in primary[0].retained_component_sha256:
                component = components.get(component_hash)
                if component is None:
                    raise M4EvidenceError("primary M3 component cannot be reconstructed by hash")
                geometry = canonical_polygon_record(component)
                if geometry.polygon_sha256 != component_hash:
                    raise M4EvidenceError("primary M3 component changes canonical identity")
                root_stock_id = _root_stock_id(
                    m3_input_id=m3_input.input_id,
                    m3_input_sha256=m3_input.content_sha256,
                    m3_result_id=m3_result.result_id,
                    m3_result_sha256=m3_result.content_sha256,
                    tasks_index=pair.tasks_index,
                    candidate_id=selected.candidate.candidate_id,
                )
                lineage = RemnantLineage.root(
                    root_stock_id=root_stock_id,
                    source_candidate_id=selected.candidate.candidate_id,
                    source_component_sha256=component_hash,
                )
                remnant = RemnantStock(
                    remnant_id=derive_remnant_id(lineage, geometry, material),
                    geometry=geometry,
                    material=material,
                    root_sheet_area=float(pair.problem.sheet_length * pair.problem.strip_height),
                    root_sheet_short_side=float(
                        min(pair.problem.sheet_length, pair.problem.strip_height)
                    ),
                    lineage=lineage,
                )
                origins.append(
                    M4OriginRemnant(
                        origin_tasks_index=pair.tasks_index,
                        origin_candidate_position=position,
                        origin_candidate_id=selected.candidate.candidate_id,
                        source_component_sha256=component_hash,
                        remnant=remnant,
                    )
                )
    origins.sort(
        key=lambda item: (
            item.origin_tasks_index,
            item.origin_candidate_position,
            item.source_component_sha256,
        )
    )
    if not origins:
        raise M4EvidenceError("M3 produced no primary-retained origin remnants")
    return tuple(origins)


def _future_part_roles(m3_input: M3ResidualInputPack) -> tuple[M4FuturePartRole, ...]:
    roles = []
    for pair in sorted(m3_input.task_pairs, key=lambda item: item.tasks_index):
        binding = pair.source_task_binding
        projection = binding.solver_projection
        if (
            projection is None
            or projection.mode is not ProjectionMode.SOURCE_AS_RECORDED
            or projection.intervention_codes
        ):
            raise M4EvidenceError("M4 future part requires a source-as-recorded projection")
        reference_placements = {
            placement.part_id: placement
            for placement in pair.selected_candidates[0].candidate.placements
        }
        for part in sorted(pair.problem.parts, key=lambda item: item.id):
            try:
                source_placement = reference_placements[part.id]
                role = M4FuturePartRole(
                    tasks_index=pair.tasks_index,
                    dataset_id=binding.dataset_id,
                    source_slice_sha256=binding.source_slice_sha256,
                    source_projection_sha256=projection.projection_sha256,
                    acknowledged_assumption_codes=binding.acknowledged_assumption_codes,
                    source_flip_part_count=projection.source_flip_part_count,
                    source_sheet_length=float(pair.problem.sheet_length),
                    source_strip_height=float(pair.problem.strip_height),
                    part=part,
                    reference_placement=FitPlacement(
                        part_id=source_placement.part_id,
                        rotation=float(source_placement.rotation),
                        translation=(
                            float(source_placement.translation[0]),
                            float(source_placement.translation[1]),
                        ),
                    ),
                )
                transform_part(
                    role.part,
                    role.reference_placement,
                )
            except (KeyError, ReuseGeometryError, ValidationError) as error:
                raise M4EvidenceError("M4 future source part is invalid") from error
            roles.append(role)
    roles.sort(key=lambda item: (item.tasks_index, item.part.id))
    if not roles:
        raise M4EvidenceError("M3 input contains no future source parts")
    return tuple(roles)


def prepare_m4_input_pack(
    m3_input: M3ResidualInputPack,
    m3_result: M3ResidualGeometryResult,
    m0: M0ExperimentContract,
    *,
    search_config: FitSearchConfig,
) -> M4ReuseInputPack:
    """Reconstruct M3 remnants and freeze a fit-result-blind M4 input catalog."""

    m3_input, m3_result, m0 = _validated_sources(m3_input, m3_result, m0)
    try:
        search_config = FitSearchConfig.model_validate(
            search_config.model_dump(mode="python"), strict=True
        )
    except ValidationError as error:
        raise M4EvidenceError("M4 search configuration is invalid") from error
    material = _assumed_m4_material()
    origins = _origin_remnants(m3_input, m3_result, m0, material)
    future_roles = _future_part_roles(m3_input)
    fit_config = RemnantFitConfig()
    payload = {
        "schema_version": "yieldforge.m4-remnant-reuse-input.v1",
        "m0_contract_id": m0.contract_id,
        "m0_contract_sha256": m0.content_sha256,
        "m2_result_id": m3_input.m2_result_id,
        "m2_result_sha256": m3_input.m2_result_sha256,
        "m3_input_id": m3_input.input_id,
        "m3_input_sha256": m3_input.content_sha256,
        "m3_result_id": m3_result.result_id,
        "m3_result_sha256": m3_result.content_sha256,
        "shapely_version": shapely.__version__,
        "primary_fit_config": fit_config.model_dump(mode="json"),
        "assumed_material": material.model_dump(mode="json"),
        "search_config": search_config.model_dump(mode="json"),
        "generated_order_disclaimer": (
            "greater_task_index_is_deterministic_generated_order_not_observed_chronology"
        ),
        "origin_remnants": [item.model_dump(mode="json") for item in origins],
        "future_part_roles": [item.model_dump(mode="json") for item in future_roles],
        "claim_ceiling": (
            "remnant_reuse_input_only_not_fit_frequency_savings_physical_recovery_or_commercial_value"
        ),
    }
    digest = semantic_sha256(payload)
    return M4ReuseInputPack(
        input_id=f"yfri-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m0_contract_id=m0.contract_id,
        m0_contract_sha256=m0.content_sha256,
        m2_result_id=m3_input.m2_result_id,
        m2_result_sha256=m3_input.m2_result_sha256,
        m3_input_id=m3_input.input_id,
        m3_input_sha256=m3_input.content_sha256,
        m3_result_id=m3_result.result_id,
        m3_result_sha256=m3_result.content_sha256,
        shapely_version=shapely.__version__,
        primary_fit_config=fit_config,
        assumed_material=material,
        search_config=search_config,
        origin_remnants=origins,
        future_part_roles=future_roles,
    )


class M4PairAttempt(FrozenExperimentModel):
    """One registered origin/future pair passed to bounded search."""

    origin_remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    origin_tasks_index: StrictInt = Field(ge=0)
    origin_candidate_position: StrictInt = Field(ge=0, le=1)
    origin_candidate_id: StrictStr = Field(min_length=1)
    source_component_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    future_tasks_index: StrictInt = Field(ge=0)
    future_part_id: StrictStr = Field(min_length=1)
    search_result: FitSearchResult

    @model_validator(mode="after")
    def require_bound_search_result(self) -> Self:
        if self.future_tasks_index <= self.origin_tasks_index:
            raise ValueError("M4 pair must use a strictly greater generated future task index")
        if self.search_result.parent_remnant_id != self.origin_remnant_id:
            raise ValueError("M4 pair search changes origin remnant identity")
        if self.search_result.part_id != self.future_part_id:
            raise ValueError("M4 pair search changes future part identity")
        return self


class M4FullSheetReference(FrozenExperimentModel):
    """Exact feasibility evidence for opening the future task's full sheet."""

    reference_stock: RemnantStock
    part_id: StrictStr = Field(min_length=1)
    placement: FitPlacement
    placed_polygon: CanonicalPolygon

    @model_validator(mode="after")
    def require_consistent_reference(self) -> Self:
        if self.placement.part_id != self.part_id:
            raise ValueError("full-sheet reference placement changes part identity")
        return self


class M4ReuseWitness(FrozenExperimentModel):
    """First exactly validated source-observed remnant reuse witness."""

    origin_remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    origin_tasks_index: StrictInt = Field(ge=0)
    origin_candidate_position: StrictInt = Field(ge=0, le=1)
    origin_candidate_id: StrictStr = Field(min_length=1)
    source_component_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    future_tasks_index: StrictInt = Field(ge=0)
    future_part_id: StrictStr = Field(min_length=1)
    parent_remnant: RemnantStock
    search_result: FitSearchResult
    placed_polygon: CanonicalPolygon
    consumption_result: RemnantFitResult
    child_remnants: tuple[RemnantStock, ...]
    full_sheet_reference: M4FullSheetReference
    remnant_available_before_order: Literal[True] = True
    open_full_sheet_count_before_order: Literal[0] = 0
    declared_order_part_count: Literal[1] = 1
    avoided_full_sheet_openings: Literal[1] = 1

    @model_validator(mode="after")
    def require_consistent_witness(self) -> Self:
        if self.future_tasks_index <= self.origin_tasks_index:
            raise ValueError("M4 witness future task must be strictly greater than its origin")
        if self.parent_remnant.remnant_id != self.origin_remnant_id:
            raise ValueError("M4 witness parent changes origin identity")
        if self.source_component_sha256 != self.parent_remnant.geometry.polygon_sha256:
            raise ValueError("M4 witness origin component changes parent geometry")
        if self.search_result.status is not FitSearchStatus.FIT:
            raise ValueError("M4 witness requires a successful bounded search")
        if (
            self.search_result.parent_remnant_id != self.origin_remnant_id
            or self.search_result.part_id != self.future_part_id
            or self.search_result.placement is None
        ):
            raise ValueError("M4 witness search result changes pair identity")
        if self.consumption_result.status != "fit":
            raise ValueError("M4 witness requires successful remnant consumption")
        if self.consumption_result.placed_polygon_sha256 != self.placed_polygon.polygon_sha256:
            raise ValueError("M4 witness placed polygon does not match consumption evidence")
        child_summaries = tuple(
            (item.remnant_id, item.polygon_sha256, item.area)
            for item in self.consumption_result.children
        )
        child_records = tuple(
            (item.remnant_id, item.geometry.polygon_sha256, item.geometry.area)
            for item in self.child_remnants
        )
        if child_records != child_summaries:
            raise ValueError("M4 witness child remnants do not match consumption evidence")
        if self.full_sheet_reference.part_id != self.future_part_id:
            raise ValueError("M4 witness full-sheet reference changes future part identity")
        return self


class M4ReuseSummary(FrozenExperimentModel):
    """Recomputed bounded-search and toy-state M4 totals."""

    registered_origin_remnant_count: StrictInt = Field(ge=1)
    registered_future_part_count: StrictInt = Field(ge=1)
    eligible_pair_count: StrictInt = Field(ge=0)
    attempted_pair_count: StrictInt = Field(ge=0)
    no_witness_pair_count: StrictInt = Field(ge=0)
    fit_pair_count: StrictInt = Field(ge=0, le=1)
    generated_candidate_count: StrictInt = Field(ge=0)
    evaluated_candidate_count: StrictInt = Field(ge=0)
    budget_truncated_candidate_count: StrictInt = Field(ge=0)
    rejection_counts: tuple[FitSearchRejectionCount, ...]
    avoided_full_sheet_openings: StrictInt = Field(ge=0, le=1)
    technical_decision: Literal["pass", "open"]

    @model_validator(mode="after")
    def require_reconciled_summary(self) -> Self:
        if self.attempted_pair_count != self.no_witness_pair_count + self.fit_pair_count:
            raise ValueError("M4 attempted pairs do not reconcile with search outcomes")
        if self.attempted_pair_count > self.eligible_pair_count:
            raise ValueError("M4 attempted pairs exceed registered eligible pairs")
        expected_decision = "pass" if self.fit_pair_count == 1 else "open"
        if self.technical_decision != expected_decision:
            raise ValueError("M4 technical decision does not match fit evidence")
        if self.avoided_full_sheet_openings != self.fit_pair_count:
            raise ValueError("M4 avoided sheet count does not match the toy-state witness")
        codes = tuple(item.error_code for item in self.rejection_counts)
        if codes != tuple(sorted(set(codes))):
            raise ValueError("M4 rejection summaries must be sorted and unique")
        return self


class M4ReuseResult(FrozenExperimentModel):
    """Canonical bounded result of source-observed exact remnant reuse search."""

    schema_version: Literal["yieldforge.m4-remnant-reuse-result.v1"] = (
        "yieldforge.m4-remnant-reuse-result.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfrr-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_id: StrictStr = Field(pattern=r"^yfri-[0-9a-f]{24}$")
    input_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: StrictStr = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    m0_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    shapely_version: StrictStr = Field(min_length=1)
    attempts: tuple[M4PairAttempt, ...]
    witness: M4ReuseWitness | None = None
    summary: M4ReuseSummary
    claim_ceiling: Literal[
        "exact_remnant_reuse_possibility_only_not_frequency_savings_physical_recovery_or_"
        "commercial_value"
    ] = (
        "exact_remnant_reuse_possibility_only_not_frequency_savings_physical_recovery_or_"
        "commercial_value"
    )

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        if self.summary.attempted_pair_count != len(self.attempts):
            raise ValueError("M4 result summary does not match persisted attempts")
        if self.witness is None:
            if self.summary.fit_pair_count != 0:
                raise ValueError("M4 result cannot report a fit without a witness")
        elif (
            not self.attempts
            or self.attempts[-1].search_result.status is not FitSearchStatus.FIT
            or self.attempts[-1].search_result != self.witness.search_result
        ):
            raise ValueError("M4 witness must match the final first-fit attempt")
        digest = semantic_sha256(
            self,
            excluded_fields={"result_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M4 result content SHA-256 does not match semantic content")
        if self.result_id != f"yfrr-{digest[:24]}":
            raise ValueError("M4 result ID does not match semantic content")
        return self


def recompute_m4_summary(
    pack: M4ReuseInputPack,
    attempts: tuple[M4PairAttempt, ...],
    witness: M4ReuseWitness | None,
) -> M4ReuseSummary:
    """Recompute every aggregate from the registered input and persisted pair attempts."""

    rejection_counts: Counter[str] = Counter()
    for attempt in attempts:
        for rejection in attempt.search_result.summary.rejection_counts:
            rejection_counts[rejection.error_code] += rejection.count
    fit_count = sum(attempt.search_result.status is FitSearchStatus.FIT for attempt in attempts)
    no_witness_count = sum(
        attempt.search_result.status is FitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH
        for attempt in attempts
    )
    return M4ReuseSummary(
        registered_origin_remnant_count=len(pack.origin_remnants),
        registered_future_part_count=len(pack.future_part_roles),
        eligible_pair_count=sum(
            origin.origin_tasks_index < role.tasks_index
            for origin in pack.origin_remnants
            for role in pack.future_part_roles
        ),
        attempted_pair_count=len(attempts),
        no_witness_pair_count=no_witness_count,
        fit_pair_count=fit_count,
        generated_candidate_count=sum(
            item.search_result.summary.generated_candidate_count for item in attempts
        ),
        evaluated_candidate_count=sum(
            item.search_result.summary.evaluated_candidate_count for item in attempts
        ),
        budget_truncated_candidate_count=sum(
            item.search_result.summary.budget_truncated_candidate_count for item in attempts
        ),
        rejection_counts=tuple(
            FitSearchRejectionCount(error_code=code, count=count)
            for code, count in sorted(rejection_counts.items())
        ),
        avoided_full_sheet_openings=1 if witness is not None else 0,
        technical_decision="pass" if witness is not None else "open",
    )


def _reference_stock(pack: M4ReuseInputPack, role: M4FuturePartRole) -> RemnantStock:
    sheet = canonical_polygon_record(
        box(0.0, 0.0, role.source_sheet_length, role.source_strip_height)
    )
    identity_payload = {
        "input_id": pack.input_id,
        "part_id": role.part.id,
        "tasks_index": role.tasks_index,
    }
    digest = hashlib.sha256(
        json.dumps(identity_payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    lineage = RemnantLineage.root(
        root_stock_id=f"yfref-{digest[:24]}",
        source_candidate_id=f"full-sheet-reference-{role.tasks_index}-{role.part.id}",
        source_component_sha256=sheet.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, sheet, pack.assumed_material),
        geometry=sheet,
        material=pack.assumed_material,
        root_sheet_area=float(role.source_sheet_length * role.source_strip_height),
        root_sheet_short_side=float(min(role.source_sheet_length, role.source_strip_height)),
        lineage=lineage,
    )


def _full_sheet_reference(
    pack: M4ReuseInputPack,
    role: M4FuturePartRole,
) -> M4FullSheetReference:
    stock = _reference_stock(pack, role)
    validated = validate_fit_placement(
        stock,
        role.part,
        role.reference_placement,
        part_material=pack.assumed_material,
        config=pack.primary_fit_config,
    )
    return M4FullSheetReference(
        reference_stock=stock,
        part_id=role.part.id,
        placement=role.reference_placement,
        placed_polygon=canonical_polygon_record(validated.placed_polygon),
    )


def _result_payload(
    pack: M4ReuseInputPack,
    attempts: tuple[M4PairAttempt, ...],
    witness: M4ReuseWitness | None,
    summary: M4ReuseSummary,
) -> dict[str, object]:
    return {
        "schema_version": "yieldforge.m4-remnant-reuse-result.v1",
        "input_id": pack.input_id,
        "input_sha256": pack.content_sha256,
        "m0_contract_id": pack.m0_contract_id,
        "m0_contract_sha256": pack.m0_contract_sha256,
        "shapely_version": pack.shapely_version,
        "attempts": [item.model_dump(mode="json") for item in attempts],
        "witness": witness.model_dump(mode="json") if witness is not None else None,
        "summary": summary.model_dump(mode="json"),
        "claim_ceiling": (
            "exact_remnant_reuse_possibility_only_not_frequency_savings_physical_recovery_or_"
            "commercial_value"
        ),
    }


def _build_m4_result(
    pack: M4ReuseInputPack,
    attempts: tuple[M4PairAttempt, ...],
    witness: M4ReuseWitness | None,
) -> M4ReuseResult:
    summary = recompute_m4_summary(pack, attempts, witness)
    payload = _result_payload(pack, attempts, witness, summary)
    digest = semantic_sha256(payload)
    return M4ReuseResult(
        result_id=f"yfrr-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        input_id=pack.input_id,
        input_sha256=pack.content_sha256,
        m0_contract_id=pack.m0_contract_id,
        m0_contract_sha256=pack.m0_contract_sha256,
        shapely_version=pack.shapely_version,
        attempts=attempts,
        witness=witness,
        summary=summary,
    )


def _validate_pack_and_m0(
    pack: M4ReuseInputPack,
    m0: M0ExperimentContract,
) -> tuple[M4ReuseInputPack, M0ExperimentContract]:
    try:
        validated_pack = M4ReuseInputPack.model_validate_json(
            json.dumps(pack.model_dump(mode="json"), allow_nan=False), strict=True
        )
        validated_m0 = M0ExperimentContract.model_validate_json(
            json.dumps(m0.model_dump(mode="json"), allow_nan=False), strict=True
        )
    except (ValidationError, ValueError) as error:
        raise M4EvidenceError("M4 input or M0 contract is invalid") from error
    if (
        validated_pack.m0_contract_id != validated_m0.contract_id
        or validated_pack.m0_contract_sha256 != validated_m0.content_sha256
    ):
        raise M4EvidenceError("M4 input does not bind the supplied M0 contract")
    if validated_pack.shapely_version != shapely.__version__:
        raise M4EvidenceError("M4 input Shapely version does not match the runtime")
    return validated_pack, validated_m0


def evaluate_m4_remnant_reuse(
    pack: M4ReuseInputPack,
    m0: M0ExperimentContract,
) -> M4ReuseResult:
    """Evaluate registered pairs in order and stop at the first exact reuse witness."""

    pack, m0 = _validate_pack_and_m0(pack, m0)
    attempts: list[M4PairAttempt] = []
    rules = rule_set_from_m0(m0.remnant_eligibility)
    for origin in pack.origin_remnants:
        for role in pack.future_part_roles:
            if role.tasks_index <= origin.origin_tasks_index:
                continue
            search_result = search_fit_witness(
                origin.remnant,
                role.part,
                part_material=pack.assumed_material,
                fit_config=pack.primary_fit_config,
                search_config=pack.search_config,
            )
            attempt = M4PairAttempt(
                origin_remnant_id=origin.remnant.remnant_id,
                origin_tasks_index=origin.origin_tasks_index,
                origin_candidate_position=origin.origin_candidate_position,
                origin_candidate_id=origin.origin_candidate_id,
                source_component_sha256=origin.source_component_sha256,
                future_tasks_index=role.tasks_index,
                future_part_id=role.part.id,
                search_result=search_result,
            )
            attempts.append(attempt)
            if search_result.status is not FitSearchStatus.FIT:
                continue
            assert search_result.placement is not None
            consumption = consume_remnant(
                origin.remnant,
                role.part,
                search_result.placement,
                part_material=pack.assumed_material,
                rules=rules,
                config=pack.primary_fit_config,
            )
            validated = validate_fit_placement(
                origin.remnant,
                role.part,
                search_result.placement,
                part_material=pack.assumed_material,
                config=pack.primary_fit_config,
            )
            witness = M4ReuseWitness(
                origin_remnant_id=origin.remnant.remnant_id,
                origin_tasks_index=origin.origin_tasks_index,
                origin_candidate_position=origin.origin_candidate_position,
                origin_candidate_id=origin.origin_candidate_id,
                source_component_sha256=origin.source_component_sha256,
                future_tasks_index=role.tasks_index,
                future_part_id=role.part.id,
                parent_remnant=origin.remnant,
                search_result=search_result,
                placed_polygon=canonical_polygon_record(validated.placed_polygon),
                consumption_result=consumption.result,
                child_remnants=consumption.children,
                full_sheet_reference=_full_sheet_reference(pack, role),
            )
            return _build_m4_result(pack, tuple(attempts), witness)
    return _build_m4_result(pack, tuple(attempts), None)


def _eligible_pair_keys(pack: M4ReuseInputPack) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (origin.remnant.remnant_id, role.tasks_index, role.part.id)
        for origin in pack.origin_remnants
        for role in pack.future_part_roles
        if role.tasks_index > origin.origin_tasks_index
    )


def _validate_m4_result_evidence(
    result: M4ReuseResult,
    pack: M4ReuseInputPack,
    m0: M0ExperimentContract,
) -> None:
    pack, m0 = _validate_pack_and_m0(pack, m0)
    if (
        result.input_id != pack.input_id
        or result.input_sha256 != pack.content_sha256
        or result.m0_contract_id != m0.contract_id
        or result.m0_contract_sha256 != m0.content_sha256
        or result.shapely_version != shapely.__version__
    ):
        raise M4EvidenceError("M4 result does not bind its input, M0 contract, and runtime")
    attempt_keys = tuple(
        (item.origin_remnant_id, item.future_tasks_index, item.future_part_id)
        for item in result.attempts
    )
    eligible_keys = _eligible_pair_keys(pack)
    if attempt_keys != eligible_keys[: len(attempt_keys)]:
        raise M4EvidenceError("M4 result attempts do not use registered pair order")
    if result.witness is None:
        if len(result.attempts) != len(eligible_keys):
            raise M4EvidenceError("M4 no-witness result did not exhaust registered pairs")
    elif len(result.attempts) == 0:
        raise M4EvidenceError("M4 witness result has no pair attempt")
    expected_summary = recompute_m4_summary(pack, result.attempts, result.witness)
    if result.summary != expected_summary:
        raise M4EvidenceError("M4 result summary does not match persisted attempts")
    if result.witness is None:
        return

    witness = result.witness
    origin = next(
        (
            item
            for item in pack.origin_remnants
            if item.remnant.remnant_id == witness.origin_remnant_id
        ),
        None,
    )
    role = next(
        (
            item
            for item in pack.future_part_roles
            if item.tasks_index == witness.future_tasks_index
            and item.part.id == witness.future_part_id
        ),
        None,
    )
    if origin is None or role is None:
        raise M4EvidenceError("M4 witness does not reference registered input entities")
    assert witness.search_result.placement is not None
    validated = validate_fit_placement(
        origin.remnant,
        role.part,
        witness.search_result.placement,
        part_material=pack.assumed_material,
        config=pack.primary_fit_config,
    )
    if canonical_polygon_record(validated.placed_polygon) != witness.placed_polygon:
        raise M4EvidenceError("M4 witness placed polygon does not revalidate")
    consumption = consume_remnant(
        origin.remnant,
        role.part,
        witness.search_result.placement,
        part_material=pack.assumed_material,
        rules=rule_set_from_m0(m0.remnant_eligibility),
        config=pack.primary_fit_config,
    )
    if (
        consumption.result != witness.consumption_result
        or consumption.children != witness.child_remnants
    ):
        raise M4EvidenceError("M4 witness recursive residual evidence does not revalidate")
    if _full_sheet_reference(pack, role) != witness.full_sheet_reference:
        raise M4EvidenceError("M4 witness full-sheet reference does not revalidate")


def _canonical_model_bytes(value: FrozenExperimentModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _read_regular_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise M4EvidenceError("M4 input artifact could not be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise M4EvidenceError("M4 input artifact must be a regular file and not a symlink")
    if metadata.st_size > _MAX_M4_INPUT_BYTES:
        raise M4EvidenceError("M4 input artifact exceeds its byte limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(_MAX_M4_INPUT_BYTES + 1)
    except OSError as error:
        raise M4EvidenceError("M4 input artifact could not be read safely") from error
    if len(data) > _MAX_M4_INPUT_BYTES:
        raise M4EvidenceError("M4 input artifact exceeds its byte limit")
    return data


def publish_m4_input_pack(output_directory: Path, pack: M4ReuseInputPack) -> Path:
    """Publish or verify one immutable canonical M4 input artifact."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"remnant-reuse-input-{pack.input_id}.json.gz"
    data = gzip.compress(_canonical_model_bytes(pack), compresslevel=9, mtime=0)
    if path.exists():
        if _read_regular_file(path) != data:
            raise M4EvidenceError("M4 input artifact is immutable and differs from existing bytes")
        return path

    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.rename(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_m4_input_pack(path: Path) -> M4ReuseInputPack:
    """Load one bounded canonical M4 input and recompute its identity."""

    compressed = _read_regular_file(Path(path))
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            data = stream.read(_MAX_M4_INPUT_BYTES + 1)
    except (EOFError, OSError) as error:
        raise M4EvidenceError("M4 input artifact is not valid gzip") from error
    if len(data) > _MAX_M4_INPUT_BYTES:
        raise M4EvidenceError("M4 input artifact exceeds its decompressed byte limit")
    try:
        pack = M4ReuseInputPack.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise M4EvidenceError("M4 input artifact validation failed") from error
    if _canonical_model_bytes(pack) != data:
        raise M4EvidenceError("M4 input artifact does not use canonical JSON encoding")
    return pack


def publish_m4_result(output_directory: Path, result: M4ReuseResult) -> Path:
    """Publish or verify one immutable canonical M4 result artifact."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"remnant-reuse-result-{result.result_id}.json"
    data = _canonical_model_bytes(result)
    if path.exists():
        if _read_regular_file(path) != data:
            raise M4EvidenceError("M4 result artifact is immutable and differs from existing bytes")
        return path

    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.rename(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_m4_result(
    path: Path,
    *,
    pack: M4ReuseInputPack,
    m0: M0ExperimentContract,
) -> M4ReuseResult:
    """Load and independently revalidate one canonical M4 result artifact."""

    data = _read_regular_file(Path(path))
    try:
        result = M4ReuseResult.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise M4EvidenceError("M4 result artifact validation failed") from error
    if _canonical_model_bytes(result) != data:
        raise M4EvidenceError("M4 result artifact does not use canonical JSON encoding")
    try:
        _validate_m4_result_evidence(result, pack, m0)
    except ReuseGeometryError as error:
        raise M4EvidenceError(
            "M4 result placement or residual evidence failed revalidation"
        ) from error
    return result
