"""Fail-closed source import and attestation for the M11 test pack."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple, Self

from pydantic import ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator
from shapely import Polygon, transform
from shapely.affinity import translate
from shapely.geometry.base import BaseGeometry

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.experiments.remnant_reuse import load_m4_input_pack
from yieldforge.experiments.residual_geometry import load_m3_input_pack, load_m3_result
from yieldforge.reuse.contracts import (
    CanonicalPolygon,
    ReuseGeometryError,
    canonical_polygon_record,
    polygon_from_record,
)

LOCO_ARCHIVE_URL = "https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip"
LOCO_ARCHIVE_SIZE_BYTES = 1_654_751
LOCO_ARCHIVE_SHA256 = "86980c3d4a33fb329bd9a4cdc9464a6de9e8450baf70b1b4365944ab471a5133"
LOCO_PARSER_IDENTITY = "yieldforge.loco-2dics-safe-importer.v1"

LECTRA_M4_INPUT_ID = "yfri-26460ffca19eebfc9e479d01"
LECTRA_M4_RAW_SHA256 = "55ae844109e4d335f28d3a88cd34781be0ec2ab9627146aa4baa827aa14f24e9"
LECTRA_M3_INPUT_RAW_SHA256 = "4be7ab098234493439fe80e1703454936d9a8c4eda8484164242950bdc2447c8"
LECTRA_M3_RESULT_RAW_SHA256 = "297c81dcd4ad14e059cbe0af400d1955bacde63e6d8230fc92089059b0ed0a34"
LECTRA_CATALOG_RAW_SHA256 = "0e5c3d8aa39846fc69a1c662d01f0a0a9a1761f5d7ce0fbb10efdcf759fc55ad"
LECTRA_CATALOG_MANIFEST_RAW_SHA256 = (
    "95a404847a112b47ae27bd6269bc5e3e797c83848cabea2ce3b155004e82976e"
)
LECTRA_QUARTER_TURN_FAMILY_ROOT_SHA256 = (
    "a2234b7c4481cd502b3b014a64e2aa2bdd71747d3b1904c169e285dade536d91"
)

LECTRA_M4_PATH = Path(
    "experiments/results/remnant-reuse-input-yfri-26460ffca19eebfc9e479d01.json.gz"
)
LECTRA_M3_INPUT_PATH = Path(
    "experiments/results/residual-geometry-input-yfgi-2fe5b848ea643d282c284f90.json"
)
LECTRA_M3_RESULT_PATH = Path(
    "experiments/results/residual-geometry-result-yfgr-0ac2c37f0938d9d399e7a076.json"
)
LECTRA_CATALOG_PATH = Path("datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json")
LECTRA_CATALOG_MANIFEST_PATH = Path("datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json")

_COORDINATE_TOKEN = re.compile(r"^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|\d+/[1-9]\d*)$")
_CANONICAL_RATIONAL_TOKEN = re.compile(r"^-?(?:0|[1-9]\d*)(?:/[1-9]\d*)?$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_COMMITTED_SOURCE_ARTIFACT_BYTES = 4 * 1024 * 1024
_MAX_COORDINATE_TOKEN_CHARS = 96
_MAX_COORDINATE_COMPONENT_DIGITS = 32
_MAX_COORDINATE_EXPONENT_ABS = 18
_MAX_CANONICAL_RATIONAL_TOKEN_CHARS = 160
_MAX_CANONICAL_RATIONAL_COMPONENT_DIGITS = 72


class SourceEvidenceError(ValueError):
    """A source archive, normalized artifact, or parent attestation failed closed."""


@dataclass(frozen=True, slots=True)
class ZipSafetyLimits:
    """Resource ceilings applied before any member body is parsed."""

    max_archive_bytes: int = 4 * 1024 * 1024
    max_members: int = 64
    max_total_uncompressed_bytes: int = 32 * 1024 * 1024
    max_member_uncompressed_bytes: int = 12 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(
            value <= 0
            for value in (
                self.max_archive_bytes,
                self.max_members,
                self.max_total_uncompressed_bytes,
                self.max_member_uncompressed_bytes,
            )
        ):
            raise ValueError("ZIP safety limits must be positive")


DEFAULT_ZIP_SAFETY_LIMITS = ZipSafetyLimits()


class LOCoCensus(FrozenExperimentModel):
    """Recomputed archive and item census."""

    member_count: StrictInt = Field(ge=1)
    item_file_count: StrictInt = Field(ge=1)
    demand_file_count: StrictInt = Field(ge=1)
    nfp_file_count: StrictInt = Field(ge=0)
    item_record_count: StrictInt = Field(ge=1)
    total_source_demand: StrictInt = Field(ge=1)
    unique_translation_normalized_shape_count: StrictInt = Field(ge=1)
    unique_quarter_turn_family_count: StrictInt = Field(ge=1)
    unique_scale_invariant_family_count: StrictInt = Field(ge=1)


LOCO_EXPECTED_CENSUS = LOCoCensus(
    member_count=52,
    item_file_count=15,
    demand_file_count=15,
    nfp_file_count=18,
    item_record_count=511,
    total_source_demand=25_658,
    unique_translation_normalized_shape_count=160,
    unique_quarter_turn_family_count=157,
    unique_scale_invariant_family_count=140,
)


class LOCoItem(FrozenExperimentModel):
    """One source record; geometrically repeated records remain distinct items."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.loco-2dics-item.v1"] = "yieldforge.loco-2dics-item.v1"
    item_id: StrictStr = Field(pattern=r"^yflci-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    archive_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    source_member: StrictStr = Field(min_length=1)
    demand_member: StrictStr = Field(min_length=1)
    instance_name: StrictStr = Field(min_length=1)
    instance_label: StrictStr = Field(min_length=1)
    source_item_index: StrictInt = Field(ge=0)
    source_demand: StrictInt = Field(gt=0)
    source_vertex_count: StrictInt = Field(ge=3)
    source_translation: tuple[StrictStr, StrictStr]
    exact_normalized_vertices: tuple[tuple[StrictStr, StrictStr], ...]
    geometry: CanonicalPolygon
    translation_normalized_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    quarter_turn_family_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    scale_invariant_family_id: StrictStr = Field(pattern=r"^yflcf-[0-9a-f]{24}$")
    coordinate_units: Literal["unknown"] = "unknown"
    geometry_provenance: Literal["source_observed"] = "source_observed"
    demand_provenance: Literal["source_observed"] = "source_observed"
    translation_provenance: Literal["derived"] = "derived"
    family_hash_provenance: Literal["derived"] = "derived"

    @model_validator(mode="after")
    def require_geometry_and_content_identity(self) -> Self:
        for token in self.source_translation:
            try:
                _parse_canonical_rational_token(token, label="LOCo source translation")
            except ValueError as error:
                raise ValueError("LOCo source translation must be an exact rational") from error
        if self.translation_normalized_sha256 != self.geometry.polygon_sha256:
            raise ValueError("LOCo translation-normalized hash does not match geometry")
        exact_vertices = _exact_vertices_from_record(self.exact_normalized_vertices)
        if len(exact_vertices) != self.source_vertex_count:
            raise ValueError("LOCo exact vertex count does not match its source record")
        if (
            min(point[0] for point in exact_vertices) != 0
            or min(point[1] for point in exact_vertices) != 0
        ):
            raise ValueError("LOCo exact vertices must be translated to the local origin")
        exact_polygon = Polygon(tuple((float(x), float(y)) for x, y in exact_vertices))
        try:
            exact_geometry = canonical_polygon_record(exact_polygon)
        except ReuseGeometryError as error:
            raise ValueError("LOCo exact vertices do not encode valid geometry") from error
        if exact_geometry != self.geometry:
            raise ValueError("LOCo exact vertices do not match canonical geometry")
        polygon = polygon_from_record(self.geometry)
        if self.quarter_turn_family_sha256 != quarter_turn_family_sha256(polygon):
            raise ValueError("LOCo quarter-turn family hash does not match geometry")
        if self.scale_invariant_family_id != scale_invariant_family_id_from_exact_vertices(
            exact_vertices
        ):
            raise ValueError("LOCo scale-invariant family ID does not match exact geometry")
        digest = semantic_sha256(
            self,
            excluded_fields={"item_id", "content_sha256"},
        )
        if self.item_id != f"yflci-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("LOCo item identity does not match semantic content")
        return self


class LOCoCatalog(FrozenExperimentModel):
    """Compact canonical normalization of one bounded LOCo archive."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.loco-2dics-catalog.v1"] = "yieldforge.loco-2dics-catalog.v1"
    catalog_id: StrictStr = Field(pattern=r"^yflc-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_id: Literal["loco-2dics-v1"] = "loco-2dics-v1"
    parser_identity: Literal["yieldforge.loco-2dics-safe-importer.v1"] = LOCO_PARSER_IDENTITY
    upstream_url: Literal[
        "https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip"
    ] = LOCO_ARCHIVE_URL
    archive_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    archive_size_bytes: StrictInt = Field(gt=0)
    archive_member_names: tuple[StrictStr, ...]
    item_file_members: tuple[StrictStr, ...]
    demand_file_members: tuple[StrictStr, ...]
    nfp_file_members: tuple[StrictStr, ...]
    nfp_policy: Literal["intentionally_excluded_not_parsed_or_committed"] = (
        "intentionally_excluded_not_parsed_or_committed"
    )
    census: LOCoCensus
    items: tuple[LOCoItem, ...] = Field(min_length=1)
    geometry_evidence_boundary: Literal["geometry_source_observed"] = "geometry_source_observed"
    generated_later_boundary: Literal[
        "stock_order_layout_chronology_material_and_economics_generated_or_assumed_later"
    ] = "stock_order_layout_chronology_material_and_economics_generated_or_assumed_later"

    @model_validator(mode="after")
    def require_recomputed_census_and_content_identity(self) -> Self:
        for names, label in (
            (self.archive_member_names, "archive members"),
            (self.item_file_members, "item members"),
            (self.demand_file_members, "demand members"),
            (self.nfp_file_members, "NFP members"),
        ):
            if names != tuple(sorted(set(names))):
                raise ValueError(f"LOCo {label} must be sorted and unique")
        try:
            for name in self.archive_member_names:
                _safe_member_name(name)
            classified_items, classified_demands, classified_nfps = _classify_loco_archive_members(
                self.archive_member_names
            )
        except SourceEvidenceError as error:
            raise ValueError(f"LOCo archive membership is invalid: {error}") from error
        if (
            self.item_file_members != classified_items
            or self.demand_file_members != classified_demands
            or self.nfp_file_members != classified_nfps
        ):
            raise ValueError("LOCo catalog membership does not match archive members")
        order = tuple((item.source_member, item.source_item_index) for item in self.items)
        if order != tuple(sorted(order)) or len(order) != len(set(order)):
            raise ValueError("LOCo item records must use sorted unique source positions")
        if any(item.archive_sha256 != self.archive_sha256 for item in self.items):
            raise ValueError("LOCo item root does not match catalog archive")
        if any(item.source_member not in self.item_file_members for item in self.items):
            raise ValueError("LOCo item source member is not registered")
        if any(item.demand_member not in self.demand_file_members for item in self.items):
            raise ValueError("LOCo item demand member is not registered")
        demand_by_filename = {
            PurePosixPath(member).name: member for member in self.demand_file_members
        }
        grouped: dict[str, list[LOCoItem]] = {member: [] for member in self.item_file_members}
        for item in self.items:
            filename = PurePosixPath(item.source_member).name
            if (
                item.demand_member != demand_by_filename[filename]
                or item.instance_name != PurePosixPath(filename).stem
            ):
                raise ValueError("LOCo item-demand correspondence is contradictory")
            grouped[item.source_member].append(item)
        for _source_member, records in grouped.items():
            if not records:
                raise ValueError("LOCo catalog leaves a registered item file unparsed")
            if tuple(item.source_item_index for item in records) != tuple(range(len(records))):
                raise ValueError("LOCo source item indices must be complete and contiguous")
            if len({item.instance_label for item in records}) != 1:
                raise ValueError("LOCo demand-file label changes within one source instance")

        recomputed = LOCoCensus(
            member_count=len(self.archive_member_names),
            item_file_count=len(self.item_file_members),
            demand_file_count=len(self.demand_file_members),
            nfp_file_count=len(self.nfp_file_members),
            item_record_count=len(self.items),
            total_source_demand=sum(item.source_demand for item in self.items),
            unique_translation_normalized_shape_count=len(
                {item.translation_normalized_sha256 for item in self.items}
            ),
            unique_quarter_turn_family_count=len(
                {item.quarter_turn_family_sha256 for item in self.items}
            ),
            unique_scale_invariant_family_count=len(
                {item.scale_invariant_family_id for item in self.items}
            ),
        )
        if self.census != recomputed:
            raise ValueError("LOCo catalog census does not match its item evidence")
        digest = semantic_sha256(
            self,
            excluded_fields={"catalog_id", "content_sha256"},
        )
        if self.catalog_id != f"yflc-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("LOCo catalog identity does not match semantic content")
        return self

    @property
    def member_count(self) -> int:
        return self.census.member_count

    @property
    def item_file_count(self) -> int:
        return self.census.item_file_count

    @property
    def demand_file_count(self) -> int:
        return self.census.demand_file_count

    @property
    def nfp_file_count(self) -> int:
        return self.census.nfp_file_count

    @property
    def item_record_count(self) -> int:
        return self.census.item_record_count

    @property
    def total_source_demand(self) -> int:
        return self.census.total_source_demand

    @property
    def unique_translation_normalized_shape_count(self) -> int:
        return self.census.unique_translation_normalized_shape_count

    @property
    def unique_quarter_turn_family_count(self) -> int:
        return self.census.unique_quarter_turn_family_count

    @property
    def unique_scale_invariant_family_count(self) -> int:
        return self.census.unique_scale_invariant_family_count


class LOCoCatalogManifest(FrozenExperimentModel):
    """Content-addressed binding from the compact catalog to the upstream archive."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.loco-2dics-catalog-manifest.v1"] = (
        "yieldforge.loco-2dics-catalog-manifest.v1"
    )
    manifest_id: StrictStr = Field(pattern=r"^yflcm-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_id: Literal["loco-2dics-v1"] = "loco-2dics-v1"
    parser_identity: Literal["yieldforge.loco-2dics-safe-importer.v1"] = LOCO_PARSER_IDENTITY
    upstream_url: Literal[
        "https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip"
    ] = LOCO_ARCHIVE_URL
    upstream_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    upstream_size_bytes: StrictInt = Field(gt=0)
    catalog_artifact_name: Literal["loco-catalog.json"] = "loco-catalog.json"
    catalog_artifact_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    catalog_artifact_size_bytes: StrictInt = Field(gt=0)
    catalog_id: StrictStr = Field(pattern=r"^yflc-[0-9a-f]{24}$")
    catalog_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    census: LOCoCensus
    nfp_policy: Literal["intentionally_excluded_not_parsed_or_committed"] = (
        "intentionally_excluded_not_parsed_or_committed"
    )

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        digest = semantic_sha256(
            self,
            excluded_fields={"manifest_id", "content_sha256"},
        )
        if self.manifest_id != f"yflcm-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("LOCo catalog-manifest identity does not match semantic content")
        return self


class LectraSourceAttestation(FrozenExperimentModel):
    """Compact exact-root and join attestation over the existing M3/M4 evidence."""

    schema_version: Literal["yieldforge.m11-lectra-source-attestation.v1"] = (
        "yieldforge.m11-lectra-source-attestation.v1"
    )
    attestation_id: StrictStr = Field(pattern=r"^yfm11la-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: Literal["lectra-m3-m4"] = "lectra-m3-m4"
    lineage_kind: Literal["lectra"] = "lectra"
    origin_root_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    m4_repository_path: StrictStr = Field(min_length=1)
    m4_input_id: StrictStr = Field(pattern=r"^yfri-[0-9a-f]{24}$")
    m4_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m4_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    m3_input_repository_path: StrictStr = Field(min_length=1)
    m3_input_id: StrictStr = Field(pattern=r"^yfgi-[0-9a-f]{24}$")
    m3_input_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m3_input_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    m3_result_repository_path: StrictStr = Field(min_length=1)
    m3_result_id: StrictStr = Field(pattern=r"^yfgr-[0-9a-f]{24}$")
    m3_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m3_result_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    lectra_catalog_repository_path: StrictStr = Field(min_length=1)
    lectra_catalog_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    lectra_catalog_manifest_repository_path: StrictStr = Field(min_length=1)
    lectra_catalog_manifest_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    origin_count: StrictInt = Field(gt=0)
    future_role_count: StrictInt = Field(gt=0)
    task_count: StrictInt = Field(gt=0)
    origins_per_task: StrictInt = Field(gt=0)
    candidate_join_count: StrictInt = Field(gt=0)
    quarter_turn_family_sha256s: tuple[StrictStr, ...]
    quarter_turn_family_root_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    coordinate_units: Literal["unknown"] = "unknown"
    geometry_provenance: Literal["source_observed"] = "source_observed"
    material_provenance: Literal["assumed"] = "assumed"
    chronology_boundary: Literal[
        "greater_task_index_is_generated_order_not_observed_chronology"
    ] = "greater_task_index_is_generated_order_not_observed_chronology"

    @model_validator(mode="after")
    def require_exact_roots_counts_and_identity(self) -> Self:
        if (
            self.origin_root_sha256 != LECTRA_M4_RAW_SHA256
            or self.m4_input_id != LECTRA_M4_INPUT_ID
            or self.m4_raw_sha256 != LECTRA_M4_RAW_SHA256
            or self.m3_input_raw_sha256 != LECTRA_M3_INPUT_RAW_SHA256
            or self.m3_result_raw_sha256 != LECTRA_M3_RESULT_RAW_SHA256
            or self.lectra_catalog_raw_sha256 != LECTRA_CATALOG_RAW_SHA256
            or self.lectra_catalog_manifest_raw_sha256 != LECTRA_CATALOG_MANIFEST_RAW_SHA256
        ):
            raise ValueError("Lectra attestation root hash differs from the frozen source")
        if (
            self.origin_count,
            self.future_role_count,
            self.task_count,
            self.origins_per_task,
            self.candidate_join_count,
        ) != (406, 6_607, 203, 2, 406):
            raise ValueError("Lectra attestation census differs from the frozen source")
        _require_family_set(
            self.quarter_turn_family_sha256s,
            self.quarter_turn_family_root_sha256,
            label="Lectra",
        )
        if (
            len(self.quarter_turn_family_sha256s) != 405
            or self.quarter_turn_family_root_sha256 != LECTRA_QUARTER_TURN_FAMILY_ROOT_SHA256
        ):
            raise ValueError("Lectra derived family root differs from the frozen source")
        digest = semantic_sha256(
            self,
            excluded_fields={"attestation_id", "content_sha256"},
        )
        if (
            self.attestation_id != f"yfm11la-{digest[:24]}"
            or self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("Lectra attestation identity does not match semantic content")
        return self


class LOCoSourceAttestation(FrozenExperimentModel):
    """Compact binding from M11 to the official LOCo root and normalized catalog."""

    schema_version: Literal["yieldforge.m11-loco-source-attestation.v1"] = (
        "yieldforge.m11-loco-source-attestation.v1"
    )
    attestation_id: StrictStr = Field(pattern=r"^yfm11lo-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: Literal["loco-2dics"] = "loco-2dics"
    lineage_kind: Literal["loco_2dics"] = "loco_2dics"
    origin_root_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    upstream_url: Literal[
        "https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip"
    ] = LOCO_ARCHIVE_URL
    upstream_size_bytes: StrictInt = Field(gt=0)
    catalog_id: StrictStr = Field(pattern=r"^yflc-[0-9a-f]{24}$")
    catalog_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    catalog_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    catalog_manifest_id: StrictStr = Field(pattern=r"^yflcm-[0-9a-f]{24}$")
    catalog_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    catalog_manifest_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    census: LOCoCensus
    quarter_turn_family_sha256s: tuple[StrictStr, ...]
    quarter_turn_family_root_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    coordinate_units: Literal["unknown"] = "unknown"
    geometry_provenance: Literal["source_observed"] = "source_observed"
    generated_later_boundary: Literal[
        "stock_order_layout_chronology_material_and_economics_generated_or_assumed_later"
    ] = "stock_order_layout_chronology_material_and_economics_generated_or_assumed_later"
    nfp_boundary: Literal["nfp_files_intentionally_excluded"] = "nfp_files_intentionally_excluded"

    @model_validator(mode="after")
    def require_exact_root_family_set_and_identity(self) -> Self:
        if (
            self.origin_root_sha256 != LOCO_ARCHIVE_SHA256
            or self.upstream_size_bytes != LOCO_ARCHIVE_SIZE_BYTES
            or self.census != LOCO_EXPECTED_CENSUS
        ):
            raise ValueError("LOCo attestation differs from the frozen official root")
        _require_family_set(
            self.quarter_turn_family_sha256s,
            self.quarter_turn_family_root_sha256,
            label="LOCo",
        )
        digest = semantic_sha256(
            self,
            excluded_fields={"attestation_id", "content_sha256"},
        )
        if (
            self.attestation_id != f"yfm11lo-{digest[:24]}"
            or self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("LOCo attestation identity does not match semantic content")
        return self


class M11SourceManifest(FrozenExperimentModel):
    """Two-lineage source constitution with explicit non-evidence boundaries."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-source-manifest.v1"] = (
        "yieldforge.m11-source-manifest.v1"
    )
    source_manifest_id: StrictStr = Field(pattern=r"^yfm11sm-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    parser_identity: Literal["yieldforge.loco-2dics-safe-importer.v1"] = LOCO_PARSER_IDENTITY
    lineage_count: Literal[2] = 2
    lectra: LectraSourceAttestation
    loco: LOCoSourceAttestation
    cross_corpus_quarter_turn_family_collision_count: Literal[0] = 0
    geometry_evidence_boundary: Literal["both_corpora_use_source_observed_geometry_only"] = (
        "both_corpora_use_source_observed_geometry_only"
    )
    non_geometry_evidence_boundary: Literal[
        "chronology_material_order_stock_layout_and_economics_are_not_jointly_observed_factory_evidence"
    ] = (
        "chronology_material_order_stock_layout_and_economics_are_not_jointly_observed_"
        "factory_evidence"
    )

    @model_validator(mode="after")
    def require_independent_collision_free_roots_and_identity(self) -> Self:
        if self.lectra.origin_root_sha256 == self.loco.origin_root_sha256:
            raise ValueError("M11 source corpora must attest an independent root")
        collisions = set(self.lectra.quarter_turn_family_sha256s).intersection(
            self.loco.quarter_turn_family_sha256s
        )
        if collisions:
            raise ValueError("M11 source corpora have a canonical family collision")
        digest = semantic_sha256(
            self,
            excluded_fields={"source_manifest_id", "content_sha256"},
        )
        if (
            self.source_manifest_id != f"yfm11sm-{digest[:24]}"
            or self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("M11 source-manifest identity does not match semantic content")
        return self


class SourceArtifactBytes(NamedTuple):
    catalog: bytes
    catalog_manifest: bytes
    source_manifest: bytes


class SourceArtifactBundle(NamedTuple):
    catalog: LOCoCatalog
    catalog_manifest: LOCoCatalogManifest
    source_manifest: M11SourceManifest


def _fraction_string(value: Fraction) -> str:
    return (
        str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    )


def _coordinate_complexity_exceeded(token: str) -> bool:
    if len(token) > _MAX_COORDINATE_TOKEN_CHARS:
        return True
    unsigned = token.lstrip("+-")
    if "/" in unsigned:
        numerator, denominator = unsigned.split("/", 1)
        return (
            len(numerator) > _MAX_COORDINATE_COMPONENT_DIGITS
            or len(denominator) > _MAX_COORDINATE_COMPONENT_DIGITS
        )
    mantissa = unsigned
    exponent = ""
    for marker in ("e", "E"):
        if marker in mantissa:
            mantissa, exponent = mantissa.split(marker, 1)
            break
    mantissa_digits = sum(character.isdigit() for character in mantissa)
    if mantissa_digits > _MAX_COORDINATE_COMPONENT_DIGITS:
        return True
    if exponent:
        exponent_digits = exponent.lstrip("+-")
        if len(exponent_digits) > 3:
            return True
        if exponent_digits.isdigit() and int(exponent_digits) > _MAX_COORDINATE_EXPONENT_ABS:
            return True
    return False


def _parse_coordinate(token: str, *, label: str) -> Fraction:
    if _coordinate_complexity_exceeded(token):
        raise SourceEvidenceError(f"{label} coordinate token exceeds the complexity limit")
    if not _COORDINATE_TOKEN.fullmatch(token):
        raise SourceEvidenceError(f"{label} coordinate token is malformed or nonfinite")
    try:
        value = Fraction(token)
        numeric = float(value)
    except (OverflowError, ValueError, ZeroDivisionError) as error:
        raise SourceEvidenceError(f"{label} coordinate token is malformed or nonfinite") from error
    if not math.isfinite(numeric):
        raise SourceEvidenceError(f"{label} coordinate token is malformed or nonfinite")
    return value


def _parse_canonical_rational_token(token: str, *, label: str) -> Fraction:
    if len(token) > _MAX_CANONICAL_RATIONAL_TOKEN_CHARS:
        raise ValueError(f"{label} exceeds the canonical rational complexity limit")
    unsigned = token.lstrip("-")
    components = unsigned.split("/", 1)
    if any(len(component) > _MAX_CANONICAL_RATIONAL_COMPONENT_DIGITS for component in components):
        raise ValueError(f"{label} exceeds the canonical rational complexity limit")
    if not _CANONICAL_RATIONAL_TOKEN.fullmatch(token):
        raise ValueError(f"{label} is not a canonical rational")
    value = Fraction(token)
    if token != _fraction_string(value):
        raise ValueError(f"{label} is not a reduced canonical rational")
    return value


def _significant_lines(payload: bytes, *, label: str) -> list[str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SourceEvidenceError(f"{label} is not valid UTF-8") from error
    result: list[str] = []
    for raw_line in text.splitlines():
        value = raw_line.split("#", 1)[0].strip()
        if value:
            result.append(value)
    return result


def _parse_positive_integer(line: str, *, label: str) -> int:
    tokens = line.split()
    if len(tokens) != 1 or not tokens[0].isdigit():
        raise SourceEvidenceError(f"{label} must be one positive integer")
    value = int(tokens[0])
    if value <= 0:
        raise SourceEvidenceError(f"{label} must be positive")
    return value


def _parse_items(payload: bytes, *, member: str) -> list[tuple[tuple[Fraction, Fraction], ...]]:
    lines = _significant_lines(payload, label=member)
    if not lines:
        raise SourceEvidenceError(f"{member} item count is missing")
    count = _parse_positive_integer(lines[0], label=f"{member} item count")
    cursor = 1
    items: list[tuple[tuple[Fraction, Fraction], ...]] = []
    for item_index in range(count):
        if cursor >= len(lines):
            raise SourceEvidenceError(f"{member} item count does not match its records")
        vertex_count = _parse_positive_integer(
            lines[cursor],
            label=f"{member} item {item_index} vertex count",
        )
        cursor += 1
        if vertex_count < 3:
            raise SourceEvidenceError(f"{member} item {item_index} needs 3 distinct vertices")
        coordinates: list[tuple[Fraction, Fraction]] = []
        for vertex_index in range(vertex_count):
            if cursor >= len(lines):
                raise SourceEvidenceError(f"{member} item count does not match its records")
            tokens = lines[cursor].split()
            cursor += 1
            if len(tokens) != 2:
                raise SourceEvidenceError(
                    f"{member} item {item_index} coordinate line must contain two tokens"
                )
            coordinates.append(
                (
                    _parse_coordinate(
                        tokens[0],
                        label=f"{member} item {item_index} vertex {vertex_index}",
                    ),
                    _parse_coordinate(
                        tokens[1],
                        label=f"{member} item {item_index} vertex {vertex_index}",
                    ),
                )
            )
        if len(set(coordinates)) < 3:
            raise SourceEvidenceError(f"{member} item {item_index} needs 3 distinct vertices")
        items.append(tuple(coordinates))
    if cursor != len(lines):
        raise SourceEvidenceError(f"{member} contains trailing garbage")
    return items


def _parse_demands(payload: bytes, *, member: str) -> tuple[list[int], str]:
    lines = _significant_lines(payload, label=member)
    if not lines:
        raise SourceEvidenceError(f"{member} demand count is missing")
    count = _parse_positive_integer(lines[0], label=f"{member} demand count")
    expected_line_count = count + 2
    if len(lines) < expected_line_count:
        raise SourceEvidenceError(f"{member} demand count does not match its records")
    if len(lines) > expected_line_count:
        raise SourceEvidenceError(f"{member} contains trailing garbage")
    demands = [
        _parse_positive_integer(lines[index], label=f"{member} demand {index - 1}")
        for index in range(1, count + 1)
    ]
    label = lines[-1]
    if len(label.split()) != 1:
        raise SourceEvidenceError(f"{member} instance label must be one token")
    return demands, label


def _transform_quarter_turn(geometry: BaseGeometry, turns: int) -> BaseGeometry:
    def quarter_turn(coordinates):
        x = coordinates[:, 0]
        y = coordinates[:, 1]
        if turns == 0:
            return coordinates
        result = coordinates.copy()
        if turns == 1:
            result[:, 0], result[:, 1] = -y, x
        elif turns == 2:
            result[:, 0], result[:, 1] = -x, -y
        else:
            result[:, 0], result[:, 1] = y, -x
        return result

    rotated = transform(geometry, quarter_turn)
    minimum_x, minimum_y, _, _ = rotated.bounds
    return translate(rotated, xoff=-minimum_x, yoff=-minimum_y)


def quarter_turn_family_sha256(geometry: BaseGeometry) -> str:
    """Hash the least canonical WKB across 0/90/180/270-degree local rotations."""

    encodings: list[bytes] = []
    for turns in range(4):
        try:
            record = canonical_polygon_record(_transform_quarter_turn(geometry, turns))
        except ReuseGeometryError as error:
            raise SourceEvidenceError(
                "family geometry must be one valid positive-area polygon"
            ) from error
        encodings.append(bytes.fromhex(record.wkb_hex))
    return hashlib.sha256(min(encodings)).hexdigest()


def _quarter_turn_points(
    points: tuple[tuple[Fraction, Fraction], ...],
    turns: int,
) -> tuple[tuple[Fraction, Fraction], ...]:
    if turns == 0:
        return points
    if turns == 1:
        return tuple((-y, x) for x, y in points)
    if turns == 2:
        return tuple((-x, -y) for x, y in points)
    return tuple((y, -x) for x, y in points)


def _least_cyclic_or_reverse(
    points: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[tuple[str, str], ...]] = []
    for sequence in (points, tuple(reversed(points))):
        candidates.extend(sequence[index:] + sequence[:index] for index in range(len(sequence)))
    return min(candidates)


def _exact_vertices_from_record(
    vertices: tuple[tuple[str, str], ...],
) -> tuple[tuple[Fraction, Fraction], ...]:
    if len(vertices) < 3:
        raise ValueError("LOCo exact geometry needs at least three vertices")
    result: list[tuple[Fraction, Fraction]] = []
    for vertex in vertices:
        if len(vertex) != 2:
            raise ValueError("LOCo exact vertex must contain two rational coordinates")
        parsed: list[Fraction] = []
        for token in vertex:
            try:
                value = _parse_canonical_rational_token(
                    token,
                    label="LOCo exact vertex coordinate",
                )
            except ValueError as error:
                raise ValueError("LOCo exact vertex contains an invalid rational") from error
            parsed.append(value)
        result.append((parsed[0], parsed[1]))
    if len(set(result)) < 3:
        raise ValueError("LOCo exact geometry needs at least three distinct vertices")
    return tuple(result)


def scale_invariant_family_id_from_exact_vertices(
    vertices: tuple[tuple[Fraction, Fraction], ...],
) -> str:
    """Identify an exact rational ring up to translation, scale, and quarter turns."""

    points = tuple(vertices)
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3 or len(set(points)) < 3:
        raise SourceEvidenceError("scale-family geometry needs at least three distinct vertices")

    candidates: list[tuple[tuple[str, str], ...]] = []
    for turns in range(4):
        rotated = _quarter_turn_points(points, turns)
        minimum_x = min(point[0] for point in rotated)
        minimum_y = min(point[1] for point in rotated)
        translated = tuple((x - minimum_x, y - minimum_y) for x, y in rotated)
        maximum_x = max(point[0] for point in translated)
        maximum_y = max(point[1] for point in translated)
        scale = max(maximum_x, maximum_y)
        if scale <= 0:
            raise SourceEvidenceError("scale-family geometry has zero bounding-box span")
        normalized = tuple(
            (_fraction_string(x / scale), _fraction_string(y / scale)) for x, y in translated
        )
        candidates.append(_least_cyclic_or_reverse(normalized))
    canonical = min(candidates)
    encoded = json.dumps(
        {
            "coordinates": canonical,
            "quarter_turn_invariant": True,
            "reflection_invariant": False,
            "schema_version": "yieldforge.loco-exact-scale-invariant-family.v1",
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"yflcf-{hashlib.sha256(encoded).hexdigest()[:24]}"


def scale_invariant_family_id(geometry: BaseGeometry) -> str:
    """Identify the exact numeric coordinates of a polygon under the frozen family policy.

    Ring traversal direction is immaterial and therefore both cyclic directions are
    canonicalized. Mirrored transforms are deliberately absent. Source importers must
    call :func:`scale_invariant_family_id_from_exact_vertices` before float conversion.
    """

    if (
        not isinstance(geometry, Polygon)
        or geometry.is_empty
        or geometry.area <= 0
        or not geometry.is_valid
        or geometry.interiors
    ):
        raise SourceEvidenceError(
            "scale-family geometry must be one valid positive-area polygon without holes"
        )
    coordinates = tuple(geometry.exterior.coords)
    if len(coordinates) < 4:
        raise SourceEvidenceError("scale-family geometry needs at least three vertices")
    if coordinates[0] == coordinates[-1]:
        coordinates = coordinates[:-1]
    exact = tuple((Fraction(str(float(x))), Fraction(str(float(y)))) for x, y in coordinates)
    return scale_invariant_family_id_from_exact_vertices(exact)


def _build_item(
    *,
    archive_sha256: str,
    source_member: str,
    demand_member: str,
    instance_name: str,
    instance_label: str,
    source_item_index: int,
    source_demand: int,
    coordinates: tuple[tuple[Fraction, Fraction], ...],
) -> LOCoItem:
    translation_x = min(point[0] for point in coordinates)
    translation_y = min(point[1] for point in coordinates)
    normalized_exact = tuple((x - translation_x, y - translation_y) for x, y in coordinates)
    try:
        normalized_float = tuple((float(x), float(y)) for x, y in normalized_exact)
    except OverflowError as error:
        raise SourceEvidenceError("LOCo normalized coordinate is nonfinite") from error
    polygon = Polygon(normalized_float)
    if polygon.is_empty or polygon.area <= 0 or not polygon.is_valid or not polygon.is_simple:
        raise SourceEvidenceError("LOCo polygon must be valid, simple, and positive-area")
    try:
        geometry = canonical_polygon_record(polygon)
    except ReuseGeometryError as error:
        raise SourceEvidenceError("LOCo polygon must be valid and positive-area") from error
    semantic = {
        "schema_version": "yieldforge.loco-2dics-item.v1",
        "archive_sha256": archive_sha256,
        "source_member": source_member,
        "demand_member": demand_member,
        "instance_name": instance_name,
        "instance_label": instance_label,
        "source_item_index": source_item_index,
        "source_demand": source_demand,
        "source_vertex_count": len(coordinates),
        "source_translation": (
            _fraction_string(translation_x),
            _fraction_string(translation_y),
        ),
        "exact_normalized_vertices": tuple(
            (_fraction_string(x), _fraction_string(y)) for x, y in normalized_exact
        ),
        "geometry": geometry,
        "translation_normalized_sha256": geometry.polygon_sha256,
        "quarter_turn_family_sha256": quarter_turn_family_sha256(polygon),
        "scale_invariant_family_id": scale_invariant_family_id_from_exact_vertices(
            normalized_exact
        ),
        "coordinate_units": "unknown",
        "geometry_provenance": "source_observed",
        "demand_provenance": "source_observed",
        "translation_provenance": "derived",
        "family_hash_provenance": "derived",
    }
    hashable = {**semantic, "geometry": geometry.model_dump(mode="json")}
    digest = semantic_sha256(hashable)
    return LOCoItem(
        item_id=f"yflci-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _safe_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise SourceEvidenceError("ZIP member path is unsafe")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise SourceEvidenceError("ZIP member path is unsafe")
    if path.parts and path.parts[0].endswith(":"):
        raise SourceEvidenceError("ZIP member path is unsafe")
    canonical = path.as_posix() + ("/" if name.endswith("/") else "")
    if canonical != name:
        raise SourceEvidenceError("ZIP member path is unsafe")


def _classify_loco_archive_members(
    names: tuple[str, ...] | list[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    item_members: list[str] = []
    demand_members: list[str] = []
    nfp_members: list[str] = []
    for name in names:
        path = PurePosixPath(name)
        parts = path.parts
        prefix = parts[:2]
        if prefix == ("cutting_stock", "Items"):
            if name == "cutting_stock/Items/":
                continue
            if name.endswith("/") or len(parts) != 3 or path.suffix != ".dat":
                raise SourceEvidenceError(
                    "LOCo Items members must follow the direct-child .dat grammar"
                )
            item_members.append(name)
        elif prefix == ("cutting_stock", "demand"):
            if name == "cutting_stock/demand/":
                continue
            if name.endswith("/") or len(parts) != 3 or path.suffix != ".dat":
                raise SourceEvidenceError(
                    "LOCo demand members must follow the direct-child .dat grammar"
                )
            demand_members.append(name)
        elif prefix == ("cutting_stock", "NFPS"):
            if not name.endswith("/"):
                nfp_members.append(name)
        elif "Items" in parts or "demand" in parts:
            raise SourceEvidenceError(
                "LOCo archive permits only one direct-child Items/demand directory pair"
            )

    item_members.sort()
    demand_members.sort()
    nfp_members.sort()
    for members, label in ((item_members, "Items"), (demand_members, "demand")):
        logical_names = [
            unicodedata.normalize("NFC", PurePosixPath(name).name).casefold() for name in members
        ]
        if len(logical_names) != len(set(logical_names)):
            raise SourceEvidenceError(f"LOCo {label} contains duplicate logical names")

    item_filenames = {PurePosixPath(name).name for name in item_members}
    demand_filenames = {PurePosixPath(name).name for name in demand_members}
    if not item_filenames or item_filenames != demand_filenames:
        raise SourceEvidenceError(
            "LOCo archive membership violates strict Items/demand pairs correspondence"
        )
    return tuple(item_members), tuple(demand_members), tuple(nfp_members)


def _bounded_member_read(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    limits: ZipSafetyLimits,
) -> bytes:
    try:
        with archive.open(member, "r") as stream:
            payload = stream.read(limits.max_member_uncompressed_bytes + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise SourceEvidenceError(
            f"ZIP member {member.filename} could not be read safely"
        ) from error
    if len(payload) > limits.max_member_uncompressed_bytes:
        raise SourceEvidenceError("ZIP member exceeds its uncompressed byte limit")
    if len(payload) != member.file_size:
        raise SourceEvidenceError("ZIP member size does not match its directory record")
    return payload


def parse_loco_archive(
    payload: bytes,
    *,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    limits: ZipSafetyLimits = DEFAULT_ZIP_SAFETY_LIMITS,
) -> LOCoCatalog:
    """Parse one bounded in-memory LOCo-format archive without extracting any path."""

    if not isinstance(payload, bytes):
        raise TypeError("LOCo archive payload must be bytes")
    if len(payload) > limits.max_archive_bytes:
        raise SourceEvidenceError("ZIP archive byte limit exceeded")
    if expected_size_bytes is not None and len(payload) != expected_size_bytes:
        raise SourceEvidenceError("LOCo archive size does not match the pinned source")
    archive_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and archive_sha256 != expected_sha256:
        raise SourceEvidenceError("LOCo archive SHA-256 does not match the pinned source")

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
        members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise SourceEvidenceError("LOCo archive is not a valid ZIP") from error
    with archive:
        if len(members) > limits.max_members:
            raise SourceEvidenceError("ZIP member-count limit exceeded")
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise SourceEvidenceError("ZIP archive contains duplicate member names")
        total_size = 0
        for member in members:
            _safe_member_name(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SourceEvidenceError("ZIP archive contains a symlink member")
            if member.flag_bits & 0x1:
                raise SourceEvidenceError("ZIP archive contains an encrypted member")
            if member.file_size < 0 or member.file_size > limits.max_member_uncompressed_bytes:
                raise SourceEvidenceError("ZIP member uncompressed byte limit exceeded")
            total_size += member.file_size
        if total_size > limits.max_total_uncompressed_bytes:
            raise SourceEvidenceError("ZIP total uncompressed byte limit exceeded")

        by_name = {member.filename: member for member in members}
        item_members, demand_members, nfp_members = _classify_loco_archive_members(names)
        demand_by_filename = {PurePosixPath(name).name: name for name in demand_members}

        items: list[LOCoItem] = []
        for source_member in item_members:
            filename = PurePosixPath(source_member).name
            demand_member = demand_by_filename[filename]
            source_records = _parse_items(
                _bounded_member_read(archive, by_name[source_member], limits=limits),
                member=source_member,
            )
            demands, instance_label = _parse_demands(
                _bounded_member_read(archive, by_name[demand_member], limits=limits),
                member=demand_member,
            )
            if len(source_records) != len(demands):
                raise SourceEvidenceError(
                    f"{source_member} and {demand_member} item count does not match"
                )
            instance_name = PurePosixPath(filename).stem
            for index, (coordinates, demand) in enumerate(
                zip(source_records, demands, strict=True)
            ):
                items.append(
                    _build_item(
                        archive_sha256=archive_sha256,
                        source_member=source_member,
                        demand_member=demand_member,
                        instance_name=instance_name,
                        instance_label=instance_label,
                        source_item_index=index,
                        source_demand=demand,
                        coordinates=coordinates,
                    )
                )

    census = LOCoCensus(
        member_count=len(names),
        item_file_count=len(item_members),
        demand_file_count=len(demand_members),
        nfp_file_count=len(nfp_members),
        item_record_count=len(items),
        total_source_demand=sum(item.source_demand for item in items),
        unique_translation_normalized_shape_count=len(
            {item.translation_normalized_sha256 for item in items}
        ),
        unique_quarter_turn_family_count=len({item.quarter_turn_family_sha256 for item in items}),
        unique_scale_invariant_family_count=len({item.scale_invariant_family_id for item in items}),
    )
    semantic = {
        "schema_version": "yieldforge.loco-2dics-catalog.v1",
        "dataset_id": "loco-2dics-v1",
        "parser_identity": LOCO_PARSER_IDENTITY,
        "upstream_url": LOCO_ARCHIVE_URL,
        "archive_sha256": archive_sha256,
        "archive_size_bytes": len(payload),
        "archive_member_names": tuple(sorted(names)),
        "item_file_members": tuple(item_members),
        "demand_file_members": tuple(demand_members),
        "nfp_file_members": tuple(nfp_members),
        "nfp_policy": "intentionally_excluded_not_parsed_or_committed",
        "census": census,
        "items": tuple(items),
        "geometry_evidence_boundary": "geometry_source_observed",
        "generated_later_boundary": (
            "stock_order_layout_chronology_material_and_economics_generated_or_assumed_later"
        ),
    }
    hashable = {
        **semantic,
        "census": census.model_dump(mode="json"),
        "items": [item.model_dump(mode="json") for item in items],
    }
    digest = semantic_sha256(hashable)
    return LOCoCatalog(
        catalog_id=f"yflc-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _read_bounded_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SourceEvidenceError(f"source file could not be inspected: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SourceEvidenceError(f"source file must be regular and not a symlink: {path}")
    if metadata.st_size > maximum_bytes:
        raise SourceEvidenceError(f"source file exceeds its byte limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except OSError as error:
        raise SourceEvidenceError(f"source file could not be read safely: {path}") from error
    if len(payload) > maximum_bytes:
        raise SourceEvidenceError(f"source file exceeds its byte limit: {path}")
    return payload


def _raw_file_sha256(path: Path, *, maximum_bytes: int) -> str:
    return hashlib.sha256(_read_bounded_regular_file(path, maximum_bytes=maximum_bytes)).hexdigest()


def import_official_loco_archive(path: Path) -> LOCoCatalog:
    """Import the exact pinned official archive and require its independent census."""

    payload = _read_bounded_regular_file(
        Path(path),
        maximum_bytes=DEFAULT_ZIP_SAFETY_LIMITS.max_archive_bytes,
    )
    catalog = parse_loco_archive(
        payload,
        expected_sha256=LOCO_ARCHIVE_SHA256,
        expected_size_bytes=LOCO_ARCHIVE_SIZE_BYTES,
    )
    if catalog.census != LOCO_EXPECTED_CENSUS:
        raise SourceEvidenceError(
            "official LOCo census differs from the independently expected census"
        )
    return catalog


def build_loco_catalog_manifest(catalog: LOCoCatalog) -> LOCoCatalogManifest:
    """Bind canonical catalog bytes to its exact upstream root."""

    canonical = canonical_pretty_json_bytes(catalog)
    semantic = {
        "schema_version": "yieldforge.loco-2dics-catalog-manifest.v1",
        "dataset_id": "loco-2dics-v1",
        "parser_identity": LOCO_PARSER_IDENTITY,
        "upstream_url": LOCO_ARCHIVE_URL,
        "upstream_sha256": catalog.archive_sha256,
        "upstream_size_bytes": catalog.archive_size_bytes,
        "catalog_artifact_name": "loco-catalog.json",
        "catalog_artifact_sha256": hashlib.sha256(canonical).hexdigest(),
        "catalog_artifact_size_bytes": len(canonical),
        "catalog_id": catalog.catalog_id,
        "catalog_content_sha256": catalog.content_sha256,
        "census": catalog.census,
        "nfp_policy": catalog.nfp_policy,
    }
    hashable = {**semantic, "census": catalog.census.model_dump(mode="json")}
    digest = semantic_sha256(hashable)
    return LOCoCatalogManifest(
        manifest_id=f"yflcm-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _family_root(hashes: tuple[str, ...]) -> str:
    return hashlib.sha256(("\n".join(hashes) + "\n").encode()).hexdigest()


def _require_family_set(hashes: tuple[str, ...], root: str, *, label: str) -> None:
    if not hashes or hashes != tuple(sorted(set(hashes))):
        raise ValueError(f"{label} quarter-turn families must be nonempty, sorted, and unique")
    if any(not re.fullmatch(_SHA256_PATTERN, value) for value in hashes):
        raise ValueError(f"{label} quarter-turn family contains an invalid SHA-256")
    if root != _family_root(hashes):
        raise ValueError(f"{label} quarter-turn family root does not match its members")


def attest_lectra_source(repository_root: Path) -> LectraSourceAttestation:
    """Validate exact M3/M4 files and every M4-origin to M3-candidate join."""

    root = Path(repository_root)
    m4_path = root / LECTRA_M4_PATH
    m3_input_path = root / LECTRA_M3_INPUT_PATH
    m3_result_path = root / LECTRA_M3_RESULT_PATH
    catalog_path = root / LECTRA_CATALOG_PATH
    catalog_manifest_path = root / LECTRA_CATALOG_MANIFEST_PATH
    expected_hashes = (
        (m4_path, LECTRA_M4_RAW_SHA256, 64 * 1024 * 1024),
        (m3_input_path, LECTRA_M3_INPUT_RAW_SHA256, 128 * 1024 * 1024),
        (m3_result_path, LECTRA_M3_RESULT_RAW_SHA256, 32 * 1024 * 1024),
        (catalog_path, LECTRA_CATALOG_RAW_SHA256, 16 * 1024 * 1024),
        (
            catalog_manifest_path,
            LECTRA_CATALOG_MANIFEST_RAW_SHA256,
            1024 * 1024,
        ),
    )
    for path, expected, maximum_bytes in expected_hashes:
        if _raw_file_sha256(path, maximum_bytes=maximum_bytes) != expected:
            raise SourceEvidenceError(f"Lectra source raw SHA-256 differs: {path}")

    try:
        m4 = load_m4_input_pack(m4_path)
        m3_input = load_m3_input_pack(m3_input_path)
        m3_result = load_m3_result(m3_result_path)
    except (ValueError, ValidationError) as error:
        raise SourceEvidenceError("Lectra M3/M4 source failed strict loading") from error
    if m4.input_id != LECTRA_M4_INPUT_ID:
        raise SourceEvidenceError("Lectra M4 semantic ID differs from the frozen root")
    if (
        m4.m3_input_id != m3_input.input_id
        or m4.m3_input_sha256 != m3_input.content_sha256
        or m4.m3_result_id != m3_result.result_id
        or m4.m3_result_sha256 != m3_result.content_sha256
        or m3_result.input_id != m3_input.input_id
        or m3_result.input_sha256 != m3_input.content_sha256
    ):
        raise SourceEvidenceError("Lectra M4 does not bind the supplied M3 roots")

    pairs = {pair.tasks_index: pair for pair in m3_input.task_pairs}
    results = {result.tasks_index: result for result in m3_result.task_results}
    task_counts: Counter[int] = Counter()
    family_hashes: set[str] = set()
    join_count = 0
    for origin in m4.origin_remnants:
        pair = pairs.get(origin.origin_tasks_index)
        result = results.get(origin.origin_tasks_index)
        if pair is None or result is None:
            raise SourceEvidenceError("Lectra M4 origin task is absent from M3")
        position = origin.origin_candidate_position
        selected = pair.selected_candidates[position].candidate.candidate_id
        result_ids = (result.first_candidate_id, result.second_candidate_id)
        if (
            origin.origin_candidate_id != selected
            or result_ids[position] != selected
            or origin.remnant.lineage.source_candidate_id != selected
        ):
            raise SourceEvidenceError("Lectra M4 origin does not join its M3 selected candidate")
        task_counts[origin.origin_tasks_index] += 1
        family_hashes.add(quarter_turn_family_sha256(polygon_from_record(origin.remnant.geometry)))
        join_count += 1
    if set(task_counts) != set(m3_input.expected_task_ids) or set(task_counts.values()) != {2}:
        raise SourceEvidenceError("Lectra M4 does not contain exactly two origins per M3 task")
    if m4.assumed_material.provenance.value != "assumed":
        raise SourceEvidenceError("Lectra M4 material must remain labeled assumed")

    families = tuple(sorted(family_hashes))
    semantic = {
        "schema_version": "yieldforge.m11-lectra-source-attestation.v1",
        "corpus_id": "lectra-m3-m4",
        "lineage_kind": "lectra",
        "origin_root_sha256": LECTRA_M4_RAW_SHA256,
        "m4_repository_path": LECTRA_M4_PATH.as_posix(),
        "m4_input_id": m4.input_id,
        "m4_content_sha256": m4.content_sha256,
        "m4_raw_sha256": LECTRA_M4_RAW_SHA256,
        "m3_input_repository_path": LECTRA_M3_INPUT_PATH.as_posix(),
        "m3_input_id": m3_input.input_id,
        "m3_input_content_sha256": m3_input.content_sha256,
        "m3_input_raw_sha256": LECTRA_M3_INPUT_RAW_SHA256,
        "m3_result_repository_path": LECTRA_M3_RESULT_PATH.as_posix(),
        "m3_result_id": m3_result.result_id,
        "m3_result_content_sha256": m3_result.content_sha256,
        "m3_result_raw_sha256": LECTRA_M3_RESULT_RAW_SHA256,
        "lectra_catalog_repository_path": LECTRA_CATALOG_PATH.as_posix(),
        "lectra_catalog_raw_sha256": LECTRA_CATALOG_RAW_SHA256,
        "lectra_catalog_manifest_repository_path": LECTRA_CATALOG_MANIFEST_PATH.as_posix(),
        "lectra_catalog_manifest_raw_sha256": LECTRA_CATALOG_MANIFEST_RAW_SHA256,
        "origin_count": len(m4.origin_remnants),
        "future_role_count": len(m4.future_part_roles),
        "task_count": len(m3_input.expected_task_ids),
        "origins_per_task": 2,
        "candidate_join_count": join_count,
        "quarter_turn_family_sha256s": families,
        "quarter_turn_family_root_sha256": _family_root(families),
        "coordinate_units": "unknown",
        "geometry_provenance": "source_observed",
        "material_provenance": "assumed",
        "chronology_boundary": "greater_task_index_is_generated_order_not_observed_chronology",
    }
    digest = semantic_sha256(semantic)
    return LectraSourceAttestation(
        attestation_id=f"yfm11la-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _build_loco_attestation(
    catalog: LOCoCatalog,
    manifest: LOCoCatalogManifest,
) -> LOCoSourceAttestation:
    if catalog.census != LOCO_EXPECTED_CENSUS:
        raise SourceEvidenceError("M11 source manifest requires the official LOCo census")
    catalog_bytes = canonical_pretty_json_bytes(catalog)
    manifest_bytes = canonical_pretty_json_bytes(manifest)
    if (
        manifest.upstream_sha256 != catalog.archive_sha256
        or manifest.upstream_size_bytes != catalog.archive_size_bytes
        or manifest.catalog_id != catalog.catalog_id
        or manifest.catalog_content_sha256 != catalog.content_sha256
        or manifest.catalog_artifact_sha256 != hashlib.sha256(catalog_bytes).hexdigest()
        or manifest.catalog_artifact_size_bytes != len(catalog_bytes)
        or manifest.census != catalog.census
    ):
        raise SourceEvidenceError("LOCo catalog manifest does not bind the supplied catalog")
    families = tuple(sorted({item.quarter_turn_family_sha256 for item in catalog.items}))
    semantic = {
        "schema_version": "yieldforge.m11-loco-source-attestation.v1",
        "corpus_id": "loco-2dics",
        "lineage_kind": "loco_2dics",
        "origin_root_sha256": catalog.archive_sha256,
        "upstream_url": LOCO_ARCHIVE_URL,
        "upstream_size_bytes": catalog.archive_size_bytes,
        "catalog_id": catalog.catalog_id,
        "catalog_content_sha256": catalog.content_sha256,
        "catalog_raw_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "catalog_manifest_id": manifest.manifest_id,
        "catalog_manifest_content_sha256": manifest.content_sha256,
        "catalog_manifest_raw_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "census": catalog.census,
        "quarter_turn_family_sha256s": families,
        "quarter_turn_family_root_sha256": _family_root(families),
        "coordinate_units": "unknown",
        "geometry_provenance": "source_observed",
        "generated_later_boundary": (
            "stock_order_layout_chronology_material_and_economics_generated_or_assumed_later"
        ),
        "nfp_boundary": "nfp_files_intentionally_excluded",
    }
    hashable = {**semantic, "census": catalog.census.model_dump(mode="json")}
    digest = semantic_sha256(hashable)
    return LOCoSourceAttestation(
        attestation_id=f"yfm11lo-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def build_m11_source_manifest(
    *,
    loco_catalog: LOCoCatalog,
    loco_manifest: LOCoCatalogManifest,
    lectra: LectraSourceAttestation,
) -> M11SourceManifest:
    """Build the exact two-root source manifest and reject cross-corpus aliases."""

    loco = _build_loco_attestation(loco_catalog, loco_manifest)
    semantic = {
        "schema_version": "yieldforge.m11-source-manifest.v1",
        "parser_identity": LOCO_PARSER_IDENTITY,
        "lineage_count": 2,
        "lectra": lectra,
        "loco": loco,
        "cross_corpus_quarter_turn_family_collision_count": 0,
        "geometry_evidence_boundary": "both_corpora_use_source_observed_geometry_only",
        "non_geometry_evidence_boundary": (
            "chronology_material_order_stock_layout_and_economics_are_not_jointly_observed_"
            "factory_evidence"
        ),
    }
    hashable = {
        **semantic,
        "lectra": lectra.model_dump(mode="json"),
        "loco": loco.model_dump(mode="json"),
    }
    digest = semantic_sha256(hashable)
    try:
        return M11SourceManifest(
            source_manifest_id=f"yfm11sm-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            **semantic,
        )
    except ValidationError as error:
        raise SourceEvidenceError(
            "M11 source manifest failed independent-root validation"
        ) from error


def canonical_source_artifact_bytes(
    catalog: LOCoCatalog,
    catalog_manifest: LOCoCatalogManifest,
    source_manifest: M11SourceManifest,
) -> SourceArtifactBytes:
    """Return the sole accepted bytes for all three committed source artifacts."""

    validated = validate_source_artifact_bundle(
        catalog=catalog,
        catalog_manifest=catalog_manifest,
        source_manifest=source_manifest,
    )
    return SourceArtifactBytes(
        catalog=canonical_pretty_json_bytes(validated.catalog),
        catalog_manifest=canonical_pretty_json_bytes(validated.catalog_manifest),
        source_manifest=canonical_pretty_json_bytes(validated.source_manifest),
    )


def validate_source_artifact_bundle(
    *,
    catalog: LOCoCatalog,
    catalog_manifest: LOCoCatalogManifest,
    source_manifest: M11SourceManifest,
) -> SourceArtifactBundle:
    """Revalidate and cross-bind every identity and raw hash in a source bundle."""

    try:
        validated_catalog = LOCoCatalog.model_validate(
            catalog.model_dump(mode="python", round_trip=True, warnings=False),
            strict=True,
        )
        validated_catalog_manifest = LOCoCatalogManifest.model_validate(
            catalog_manifest.model_dump(mode="python", round_trip=True, warnings=False),
            strict=True,
        )
        validated_source_manifest = M11SourceManifest.model_validate(
            source_manifest.model_dump(mode="python", round_trip=True, warnings=False),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise SourceEvidenceError("source artifact bundle contains an invalid model") from error

    try:
        expected_catalog_manifest = build_loco_catalog_manifest(validated_catalog)
        if validated_catalog_manifest != expected_catalog_manifest:
            raise SourceEvidenceError("catalog manifest does not bind the catalog")
        expected_loco = _build_loco_attestation(
            validated_catalog,
            validated_catalog_manifest,
        )
    except (ValidationError, ValueError) as error:
        raise SourceEvidenceError("source artifact bundle has inconsistent LOCo roots") from error
    if (
        validated_source_manifest.parser_identity != validated_catalog.parser_identity
        or validated_source_manifest.loco != expected_loco
    ):
        raise SourceEvidenceError(
            "source artifact bundle source manifest does not bind the LOCo artifacts"
        )
    return SourceArtifactBundle(
        catalog=validated_catalog,
        catalog_manifest=validated_catalog_manifest,
        source_manifest=validated_source_manifest,
    )


def _reject_duplicate_source_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceEvidenceError(f"duplicate source artifact key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_source_constant(value: str) -> None:
    raise SourceEvidenceError(f"nonfinite source artifact constant: {value}")


def _load_source_model(path: Path, model):
    data = _read_bounded_regular_file(
        Path(path),
        maximum_bytes=_MAX_COMMITTED_SOURCE_ARTIFACT_BYTES,
    )
    try:
        json.loads(
            data,
            object_pairs_hook=_reject_duplicate_source_keys,
            parse_constant=_reject_nonfinite_source_constant,
        )
        result = model.model_validate_json(data, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        raise SourceEvidenceError(f"source artifact is not canonical: {path}") from error
    if data != canonical_pretty_json_bytes(result):
        raise SourceEvidenceError(f"source artifact is not canonical: {path}")
    return result


def load_loco_catalog(path: Path) -> LOCoCatalog:
    return _load_source_model(Path(path), LOCoCatalog)


def load_loco_catalog_manifest(path: Path) -> LOCoCatalogManifest:
    return _load_source_model(Path(path), LOCoCatalogManifest)


def load_m11_source_manifest(path: Path) -> M11SourceManifest:
    return _load_source_model(Path(path), M11SourceManifest)


def load_source_artifact_bundle(
    *,
    catalog_path: Path,
    catalog_manifest_path: Path,
    source_manifest_path: Path,
) -> SourceArtifactBundle:
    """Strict-load and cross-bind the complete committed source bundle."""

    return validate_source_artifact_bundle(
        catalog=load_loco_catalog(catalog_path),
        catalog_manifest=load_loco_catalog_manifest(catalog_manifest_path),
        source_manifest=load_m11_source_manifest(source_manifest_path),
    )


__all__ = [
    "DEFAULT_ZIP_SAFETY_LIMITS",
    "LECTRA_CATALOG_MANIFEST_RAW_SHA256",
    "LECTRA_CATALOG_RAW_SHA256",
    "LECTRA_M3_INPUT_RAW_SHA256",
    "LECTRA_M3_RESULT_RAW_SHA256",
    "LECTRA_M4_INPUT_ID",
    "LECTRA_M4_RAW_SHA256",
    "LECTRA_QUARTER_TURN_FAMILY_ROOT_SHA256",
    "LOCO_ARCHIVE_SHA256",
    "LOCO_ARCHIVE_SIZE_BYTES",
    "LOCO_ARCHIVE_URL",
    "LOCO_EXPECTED_CENSUS",
    "LOCO_PARSER_IDENTITY",
    "LOCoCatalog",
    "LOCoCatalogManifest",
    "LOCoCensus",
    "LOCoItem",
    "LOCoSourceAttestation",
    "LectraSourceAttestation",
    "M11SourceManifest",
    "SourceArtifactBundle",
    "SourceArtifactBytes",
    "SourceEvidenceError",
    "ZipSafetyLimits",
    "attest_lectra_source",
    "build_loco_catalog_manifest",
    "build_m11_source_manifest",
    "canonical_source_artifact_bytes",
    "import_official_loco_archive",
    "load_loco_catalog",
    "load_loco_catalog_manifest",
    "load_m11_source_manifest",
    "load_source_artifact_bundle",
    "parse_loco_archive",
    "quarter_turn_family_sha256",
    "scale_invariant_family_id",
    "scale_invariant_family_id_from_exact_vertices",
    "validate_source_artifact_bundle",
]
