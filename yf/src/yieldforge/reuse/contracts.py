"""Strict persisted contracts for exact remnant reuse evidence."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from shapely import Polygon, from_wkb, normalize, to_wkb
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry


class ReuseContractModel(BaseModel):
    """Immutable, finite, strict base for reuse evidence."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ReuseGeometryError(ValueError):
    """A stable fail-closed reuse geometry error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class MaterialProvenance(StrEnum):
    """Evidence status for one material identity."""

    OBSERVED = "observed"
    GENERATED = "generated"
    ASSUMED = "assumed"


class MaterialIdentity(ReuseContractModel):
    """The five exact M0 material compatibility fields and their provenance."""

    schema_version: Literal["yieldforge.material-identity.v1"] = "yieldforge.material-identity.v1"
    material_code: StrictStr = Field(min_length=1)
    grade: StrictStr = Field(min_length=1)
    thickness: StrictStr = Field(min_length=1)
    surface: StrictStr = Field(min_length=1)
    grain: StrictStr = Field(min_length=1)
    provenance: MaterialProvenance


def _require_polygon(geometry: BaseGeometry, *, label: str) -> Polygon:
    if not isinstance(geometry, Polygon):
        raise ReuseGeometryError("nonpolygonal_geometry", f"{label} must be one polygon")
    if geometry.is_empty or geometry.area <= 0 or not geometry.is_valid:
        raise ReuseGeometryError(
            "invalid_polygon_geometry", f"{label} must be valid and positive-area"
        )
    coordinates = list(geometry.exterior.coords)
    for interior in geometry.interiors:
        coordinates.extend(interior.coords)
    if any(not math.isfinite(value) for point in coordinates for value in point[:2]):
        raise ReuseGeometryError("nonfinite_geometry", f"{label} coordinates must be finite")
    return geometry


def _canonical_polygon_bytes(geometry: BaseGeometry) -> tuple[bytes, float]:
    polygon = _require_polygon(geometry, label="polygon geometry")
    try:
        canonical = normalize(polygon)
        encoded = to_wkb(canonical, byte_order=1, output_dimension=2)
    except GEOSException as error:
        raise ReuseGeometryError(
            "geometry_operation_failed", "polygon canonicalization failed"
        ) from error
    return encoded, float(canonical.area)


def _decode_polygon(wkb_hex: str) -> Polygon:
    try:
        decoded = from_wkb(bytes.fromhex(wkb_hex))
    except (GEOSException, ValueError) as error:
        raise ReuseGeometryError(
            "invalid_polygon_wkb", "polygon WKB could not be decoded"
        ) from error
    return _require_polygon(decoded, label="decoded geometry")


class CanonicalPolygon(ReuseContractModel):
    """Self-validating canonical polygon evidence."""

    schema_version: Literal["yieldforge.canonical-polygon.v1"] = "yieldforge.canonical-polygon.v1"
    wkb_hex: StrictStr = Field(min_length=2, pattern=r"^[0-9a-f]+$")
    polygon_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    area: StrictFloat = Field(gt=0)

    @field_validator("wkb_hex")
    @classmethod
    def require_even_hex_length(cls, value: str) -> str:
        if len(value) % 2:
            raise ValueError("polygon WKB hex must have even length")
        return value

    @model_validator(mode="after")
    def require_canonical_identity(self) -> Self:
        polygon = _decode_polygon(self.wkb_hex)
        encoded, area = _canonical_polygon_bytes(polygon)
        if encoded.hex() != self.wkb_hex:
            raise ValueError("polygon WKB must use the canonical encoding")
        if hashlib.sha256(encoded).hexdigest() != self.polygon_sha256:
            raise ValueError("polygon hash does not match WKB")
        if area != self.area:
            raise ValueError("polygon area does not match WKB")
        return self


def canonical_polygon_record(geometry: BaseGeometry) -> CanonicalPolygon:
    """Encode one valid polygon using the M3 canonical geometry convention."""

    encoded, area = _canonical_polygon_bytes(geometry)
    return CanonicalPolygon(
        wkb_hex=encoded.hex(),
        polygon_sha256=hashlib.sha256(encoded).hexdigest(),
        area=area,
    )


def polygon_from_record(record: CanonicalPolygon) -> Polygon:
    """Decode and independently revalidate one canonical polygon record."""

    polygon = _decode_polygon(record.wkb_hex)
    encoded, area = _canonical_polygon_bytes(polygon)
    if hashlib.sha256(encoded).hexdigest() != record.polygon_sha256:
        raise ReuseGeometryError("polygon_hash_mismatch", "polygon hash does not match WKB")
    if encoded.hex() != record.wkb_hex or area != record.area:
        raise ReuseGeometryError("polygon_canonical_mismatch", "polygon evidence is not canonical")
    return polygon


class RemnantLineage(ReuseContractModel):
    """One immutable ancestry record rooted in an M3 stock sheet."""

    schema_version: Literal["yieldforge.remnant-lineage.v1"] = "yieldforge.remnant-lineage.v1"
    root_stock_id: StrictStr = Field(min_length=1)
    parent_remnant_id: StrictStr | None = Field(default=None, pattern=r"^yfrm-[0-9a-f]{24}$")
    ancestor_remnant_ids: tuple[StrictStr, ...] = ()
    generation: StrictInt = Field(ge=1)
    source_candidate_id: StrictStr = Field(min_length=1)
    source_component_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("ancestor_remnant_ids")
    @classmethod
    def require_unique_ancestors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("lineage ancestors must be unique")
        if any(not ancestor.startswith("yfrm-") or len(ancestor) != 29 for ancestor in value):
            raise ValueError("lineage ancestors must be remnant IDs")
        return value

    @model_validator(mode="after")
    def require_consistent_chain(self) -> Self:
        if self.generation != len(self.ancestor_remnant_ids) + 1:
            raise ValueError("lineage generation must equal ancestor count plus one")
        if self.generation == 1:
            if self.parent_remnant_id is not None or self.ancestor_remnant_ids:
                raise ValueError("root lineage cannot have a parent or ancestors")
        elif (
            self.parent_remnant_id is None
            or not self.ancestor_remnant_ids
            or self.ancestor_remnant_ids[-1] != self.parent_remnant_id
        ):
            raise ValueError("child lineage parent must be the final ancestor")
        return self

    @classmethod
    def root(
        cls,
        *,
        root_stock_id: str,
        source_candidate_id: str,
        source_component_sha256: str,
    ) -> RemnantLineage:
        """Create a generation-one lineage record."""

        return cls(
            root_stock_id=root_stock_id,
            generation=1,
            source_candidate_id=source_candidate_id,
            source_component_sha256=source_component_sha256,
        )


def derive_remnant_id(
    lineage: RemnantLineage,
    geometry: CanonicalPolygon,
    material: MaterialIdentity,
) -> str:
    """Derive a stable remnant identity from exact geometry and provenance."""

    payload = {
        "geometry": geometry.model_dump(mode="json"),
        "lineage": lineage.model_dump(mode="json"),
        "material": material.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"yfrm-{hashlib.sha256(encoded).hexdigest()[:24]}"


class RemnantStock(ReuseContractModel):
    """One exact irregular stock object available for reuse."""

    schema_version: Literal["yieldforge.remnant-stock.v1"] = "yieldforge.remnant-stock.v1"
    remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    geometry: CanonicalPolygon
    material: MaterialIdentity
    root_sheet_area: StrictFloat = Field(gt=0)
    root_sheet_short_side: StrictFloat = Field(gt=0)
    lineage: RemnantLineage

    @model_validator(mode="after")
    def require_content_identity_and_acyclic_lineage(self) -> Self:
        expected = derive_remnant_id(self.lineage, self.geometry, self.material)
        if self.remnant_id != expected:
            raise ValueError("remnant identity does not match its content")
        if self.lineage.source_component_sha256 != self.geometry.polygon_sha256:
            raise ValueError("lineage component hash must match remnant geometry")
        if self.remnant_id in self.lineage.ancestor_remnant_ids:
            raise ValueError("remnant identity cannot appear in its ancestors")
        if self.remnant_id == self.lineage.parent_remnant_id:
            raise ValueError("remnant identity cannot be its own parent")
        return self


def child_lineage(parent: RemnantStock, *, source_component_sha256: str) -> RemnantLineage:
    """Extend one verified lineage by exactly one generation."""

    return RemnantLineage(
        root_stock_id=parent.lineage.root_stock_id,
        parent_remnant_id=parent.remnant_id,
        ancestor_remnant_ids=parent.lineage.ancestor_remnant_ids + (parent.remnant_id,),
        generation=parent.lineage.generation + 1,
        source_candidate_id=parent.lineage.source_candidate_id,
        source_component_sha256=source_component_sha256,
    )


class RemnantFitConfig(ReuseContractModel):
    """Explicit geometry settings for one remnant fit."""

    schema_version: Literal["yieldforge.remnant-fit-config.v1"] = "yieldforge.remnant-fit-config.v1"
    clearance_distance: StrictFloat = Field(default=0.0, ge=0)
    buffer_join_style: Literal["mitre"] = "mitre"
    coordinate_tolerance: StrictFloat = Field(default=1e-7, gt=0)
    relative_area_tolerance: StrictFloat = Field(default=1e-10, gt=0)


class FitPlacement(ReuseContractModel):
    """One proposed rigid transform for a future part."""

    part_id: StrictStr = Field(min_length=1)
    rotation: StrictFloat
    translation: tuple[StrictFloat, StrictFloat]


class FitSearchConfig(ReuseContractModel):
    """Frozen deterministic candidate-search settings."""

    schema_version: Literal["yieldforge.fit-search-config.v1"] = "yieldforge.fit-search-config.v1"
    grid_columns: StrictInt = Field(ge=2)
    grid_rows: StrictInt = Field(ge=2)
    maximum_candidates: StrictInt = Field(ge=1)
    candidate_source_order: tuple[StrictStr, ...] = (
        "bbox_alignments",
        "vertex_alignments",
        "uniform_grid",
    )
    rotation_order: Literal["ascending_numeric"] = "ascending_numeric"
    transform_order: Literal["rotation_translation_lexicographic"] = (
        "rotation_translation_lexicographic"
    )

    @field_validator("candidate_source_order")
    @classmethod
    def require_registered_candidate_source_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        expected = ("bbox_alignments", "vertex_alignments", "uniform_grid")
        if value != expected:
            raise ValueError("candidate sources must use the registered order")
        return value


class FitSearchStatus(StrEnum):
    """Exhaustive vocabulary for one bounded witness search."""

    FIT = "fit"
    NO_WITNESS_WITHIN_REGISTERED_SEARCH = "no_witness_within_registered_search"


class FitSearchRejectionCount(ReuseContractModel):
    """Stable count for one exact placement rejection code."""

    error_code: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    count: StrictInt = Field(ge=1)


class FitSearchAttemptSummary(ReuseContractModel):
    """Auditable candidate generation, budget, and evaluation counts."""

    generated_candidate_count: StrictInt = Field(ge=0)
    duplicate_candidate_count: StrictInt = Field(ge=0)
    unique_candidate_count: StrictInt = Field(ge=0)
    budgeted_candidate_count: StrictInt = Field(ge=0)
    evaluated_candidate_count: StrictInt = Field(ge=0)
    budget_truncated_candidate_count: StrictInt = Field(ge=0)
    rejection_counts: tuple[FitSearchRejectionCount, ...] = ()

    @model_validator(mode="after")
    def require_reconciled_counts(self) -> Self:
        if self.generated_candidate_count != (
            self.duplicate_candidate_count + self.unique_candidate_count
        ):
            raise ValueError("generated candidates must reconcile with duplicates and unique count")
        if self.unique_candidate_count != (
            self.budgeted_candidate_count + self.budget_truncated_candidate_count
        ):
            raise ValueError("unique candidates must reconcile with the registered budget")
        if self.evaluated_candidate_count > self.budgeted_candidate_count:
            raise ValueError("evaluated candidates cannot exceed the registered budget")
        error_codes = tuple(item.error_code for item in self.rejection_counts)
        if error_codes != tuple(sorted(set(error_codes))):
            raise ValueError("search rejection counts must be sorted and unique")
        if sum(item.count for item in self.rejection_counts) > self.evaluated_candidate_count:
            raise ValueError("search rejections cannot exceed evaluated candidates")
        return self


class FitSearchResult(ReuseContractModel):
    """One exact witness or an explicitly inconclusive bounded exhaustion."""

    schema_version: Literal["yieldforge.fit-search-result.v1"] = "yieldforge.fit-search-result.v1"
    status: FitSearchStatus
    parent_remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    part_id: StrictStr = Field(min_length=1)
    config: FitSearchConfig
    summary: FitSearchAttemptSummary
    placement: FitPlacement | None = None

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> Self:
        expected_budgeted = min(
            self.summary.unique_candidate_count,
            self.config.maximum_candidates,
        )
        if self.summary.budgeted_candidate_count != expected_budgeted:
            raise ValueError("search summary does not match the registered candidate budget")

        rejected = sum(item.count for item in self.summary.rejection_counts)
        if self.status is FitSearchStatus.FIT:
            if (
                self.placement is None
                or self.placement.part_id != self.part_id
                or self.summary.evaluated_candidate_count < 1
                or rejected != self.summary.evaluated_candidate_count - 1
            ):
                raise ValueError("fit result requires one accepted placement after its rejections")
        elif (
            self.placement is not None
            or self.summary.evaluated_candidate_count != self.summary.budgeted_candidate_count
            or rejected != self.summary.evaluated_candidate_count
        ):
            raise ValueError("no-witness result requires complete inconclusive search exhaustion")
        return self


class ReuseAccounting(ReuseContractModel):
    """Disjoint material categories after consuming one remnant."""

    parent_remnant_area: StrictFloat = Field(gt=0)
    placed_area: StrictFloat = Field(ge=0)
    process_loss_area: StrictFloat = Field(ge=0)
    retained_child_area: StrictFloat = Field(ge=0)
    scrap_area: StrictFloat = Field(ge=0)
    reconciliation_delta: StrictFloat = Field(ge=0)
    area_tolerance: StrictFloat = Field(gt=0)

    @model_validator(mode="after")
    def require_recomputed_delta(self) -> Self:
        accounted = (
            self.placed_area + self.process_loss_area + self.retained_child_area + self.scrap_area
        )
        expected = abs(self.parent_remnant_area - accounted)
        if self.reconciliation_delta != expected:
            raise ValueError("reuse accounting delta does not match material categories")
        if self.reconciliation_delta > self.area_tolerance:
            raise ValueError("reuse accounting exceeds its tolerance")
        return self


class ChildRemnantSummary(ReuseContractModel):
    """Compact identity for one retained child remnant."""

    remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    polygon_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    area: StrictFloat = Field(gt=0)


class RemnantFitResult(ReuseContractModel):
    """One valid exact fit or one fail-closed error."""

    schema_version: Literal["yieldforge.remnant-fit-result.v1"] = "yieldforge.remnant-fit-result.v1"
    status: Literal["fit", "invalid"]
    parent_remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    part_id: StrictStr = Field(min_length=1)
    error_code: StrictStr | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    placement: FitPlacement | None = None
    placed_polygon_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    accounting: ReuseAccounting | None = None
    children: tuple[ChildRemnantSummary, ...] = ()

    @model_validator(mode="after")
    def require_consistent_result(self) -> Self:
        if self.status == "fit":
            if self.error_code is not None:
                raise ValueError("successful fit cannot carry an error code")
            if (
                self.placement is None
                or self.placed_polygon_sha256 is None
                or self.accounting is None
            ):
                raise ValueError("successful fit requires placement and accounting evidence")
        elif (
            self.error_code is None
            or self.placement is not None
            or self.placed_polygon_sha256 is not None
            or self.accounting is not None
            or self.children
        ):
            raise ValueError("failed fit may carry only an error code")

        child_ids = tuple(child.remnant_id for child in self.children)
        if child_ids != tuple(sorted(set(child_ids))):
            raise ValueError("child remnants must be sorted and unique")
        return self
