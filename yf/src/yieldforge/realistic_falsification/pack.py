"""Deterministic, outcome-blind construction of the frozen M11 test population."""

from __future__ import annotations

import hashlib
import json
import os
import random
import stat
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, NamedTuple, Self

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

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.experiments.residual_geometry import M3ResidualInputPack, load_m3_input_pack
from yieldforge.realistic_falsification.contracts import (
    M11CorpusContract,
    M11ExperimentContract,
    M11FieldProvenance,
    build_m11_contract,
    build_m11_parent_binding,
    build_m11_source_binding,
)
from yieldforge.realistic_falsification.sources import (
    LECTRA_CATALOG_RAW_SHA256,
    LECTRA_M3_INPUT_RAW_SHA256,
    LOCO_ARCHIVE_URL,
    LOCoCatalog,
    LOCoItem,
    M11SourceManifest,
    load_loco_catalog,
    load_m11_source_manifest,
)
from yieldforge.reuse.contracts import polygon_from_record

M11_ROOT_SEED = 2_026_082_901
M11_REGIMES = ("recurrent", "mixed", "high_mix", "regime_shift")
M11_AUDIT_POSITIONS = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (9, 10, 11),
    (12, 13, 14),
    (21, 22, 23),
)
M11_AUDIT_ARMS = ("central", "central", "adverse", "adverse", "null", "null")
M11_PROHIBITED_SELECTION_FIELDS = (
    "candidate_width",
    "candidate_density",
    "residual_comparisons",
    "m4_fit_outcomes",
    "later_scores",
)
M11_SELECTION_INPUTS = ("source_geometry", "source_demand", "fixed_identifiers", "hashes")
LOCO_TARGET_WIDTH_MULTIPLIERS = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0)
LECTRA_REFERENCE_AREA = 1_296_449_632.0

_PACK_SEED_DOMAIN = "yieldforge.m11-pack.v1"
_CALIBRATION_LOCO_INSTANCES = frozenset({"swim", "dagli", "albano", "marques", "mao"})
_M0_PATH = Path("experiments/m0-contract-v1.json")
_M10_PATH = Path(
    "experiments/results/m10-minimum-investment-verdict-yfm10-931b3a95fe84cd96cff799f2.json"
)
_M3_PATH = Path("experiments/results/residual-geometry-input-yfgi-2fe5b848ea643d282c284f90.json")
_LECTRA_CATALOG_PATH = Path("datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json")
_LOCO_CATALOG_PATH = Path("datasets/catalogs/loco-2dics-v1/loco-catalog.json")
_SOURCE_MANIFEST_PATH = Path("benchmarks/falsification/source-manifest-v1.json")
_MAX_PACK_ARTIFACT_BYTES = 32 * 1024 * 1024
_OFFICIAL_CONTRACT_ID = "yfm11c-e956019aeef85350f2ffa9d3"
_OFFICIAL_CONTRACT_CONTENT_SHA256 = (
    "sha256:e956019aeef85350f2ffa9d351ab15539d1b23137d7566118f73c5f29882143b"
)
_OFFICIAL_CONTRACT_RAW_SHA256 = "0cddf4e144a1eae26159403a8f456fe55fd3bb76bcc28b676c8edc71ae35ccf4"
_OFFICIAL_POPULATION_ID = "yfm11pop-a26084179d2e8f776630f8ac"
_OFFICIAL_POPULATION_CONTENT_SHA256 = (
    "sha256:a26084179d2e8f776630f8ac272d5651069500a5df996869d20df9893ca0bc56"
)
_OFFICIAL_POPULATION_RAW_SHA256 = "c16f38ec48aabfd97a90ff9ff3b2bdd52a280c042bd8786bff6a2224236d163b"

M11CorpusId = Literal["lectra-m3-m4", "loco-2dics"]
M11PartitionKind = Literal["calibration", "confirmation"]
M11Regime = Literal["recurrent", "mixed", "high_mix", "regime_shift"]
M11StreamKind = Literal["primary", "shuffled_twin"]
M11SourceKind = Literal["lectra", "loco_2dics"]
M11EconomicArm = Literal["optimistic", "central", "adverse"]


class PackEvidenceError(ValueError):
    """The generated pack or one of its immutable parents failed closed."""


def m11_subseed(label: str) -> int:
    """Return the frozen SHA-derived unsigned 64-bit seed for one operation label."""

    digest = hashlib.sha256(f"{_PACK_SEED_DOMAIN}|{M11_ROOT_SEED}|{label}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _identity(prefix: str, semantic: dict[str, object]) -> tuple[str, str]:
    digest = semantic_sha256(semantic)
    return f"{prefix}{digest[:24]}", f"sha256:{digest}"


def _strict_json_model(model, payload: dict[str, object]):
    """Validate generated JSON semantics strictly while accepting JSON arrays as tuples."""

    return model.model_validate_json(
        json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
        strict=True,
    )


class M11GeometryReference(FrozenExperimentModel):
    """A compact pointer to immutable source geometry, never a copied geometry body."""

    kind: Literal["lectra_m3_task", "loco_item"]
    reference_id: StrictStr = Field(min_length=1)
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    geometry_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    instance_name: StrictStr | None = None
    source_item_index: StrictInt | None = Field(default=None, ge=0)
    source_demand: StrictInt | None = Field(default=None, gt=0)
    bbox_width: StrictFloat | None = Field(default=None, gt=0)
    bbox_height: StrictFloat | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_kind_specific_fields(self) -> Self:
        loco_fields = (
            self.instance_name,
            self.source_item_index,
            self.source_demand,
            self.bbox_width,
            self.bbox_height,
        )
        if self.kind == "loco_item" and any(value is None for value in loco_fields):
            raise ValueError("LOCo geometry references require raw item and bounding-box evidence")
        if self.kind == "lectra_m3_task" and any(value is not None for value in loco_fields):
            raise ValueError("Lectra task references cannot masquerade as LOCo item rows")
        return self


class M11CandidateReference(FrozenExperimentModel):
    """An ordered candidate pointer bound to an immutable parent or generated fallback."""

    position: StrictInt = Field(ge=0)
    candidate_id: StrictStr = Field(min_length=1)
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provenance: Literal["externally_anchored", "generated"]


class M11FallbackPlacement(FrozenExperimentModel):
    geometry_reference_id: StrictStr = Field(min_length=1)
    copy_index: StrictInt = Field(ge=0)
    x: StrictFloat = Field(ge=0)
    y: StrictFloat = Field(ge=0)
    width: StrictFloat = Field(gt=0)
    height: StrictFloat = Field(gt=0)


class M11FallbackStock(FrozenExperimentModel):
    """A deterministic bounding-box shelf witness proving one LOCo order is feasible."""

    schema_version: Literal["yieldforge.m11-loco-fallback.v1"] = "yieldforge.m11-loco-fallback.v1"
    stock_id: StrictStr = Field(pattern=r"^yfm11fb-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    algorithm: Literal["first_fit_decreasing_bbox_shelves_min_area_width_index"] = (
        "first_fit_decreasing_bbox_shelves_min_area_width_index"
    )
    target_width_multipliers: tuple[StrictFloat, ...]
    candidate_widths: tuple[StrictFloat, ...]
    candidate_heights: tuple[StrictFloat, ...]
    selected_width_index: StrictInt = Field(ge=0, le=7)
    width: StrictFloat = Field(gt=0)
    height: StrictFloat = Field(gt=0)
    area: StrictFloat = Field(gt=0)
    placements: tuple[M11FallbackPlacement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_feasible_minimum_witness_and_identity(self) -> Self:
        if self.target_width_multipliers != LOCO_TARGET_WIDTH_MULTIPLIERS:
            raise ValueError("LOCo fallback target widths differ from the frozen registry")
        if len(self.candidate_widths) != 8 or len(self.candidate_heights) != 8:
            raise ValueError("LOCo fallback requires exactly eight target-width candidates")
        areas = tuple(
            width * height
            for width, height in zip(self.candidate_widths, self.candidate_heights, strict=True)
        )
        expected_index = min(
            range(8),
            key=lambda index: (areas[index], self.candidate_widths[index], index),
        )
        if (
            self.selected_width_index != expected_index
            or self.width != self.candidate_widths[expected_index]
            or self.height != self.candidate_heights[expected_index]
            or self.area != self.width * self.height
        ):
            raise ValueError("LOCo fallback is not the frozen minimum envelope")
        keys = tuple((item.geometry_reference_id, item.copy_index) for item in self.placements)
        if len(keys) != len(set(keys)):
            raise ValueError("LOCo fallback repeats a geometry-copy placement")
        for item in self.placements:
            if item.x + item.width > self.width + 1e-9 or item.y + item.height > self.height + 1e-9:
                raise ValueError("LOCo fallback placement exceeds its stock boundary")
        for index, left in enumerate(self.placements):
            for right in self.placements[index + 1 :]:
                if not (
                    left.x + left.width <= right.x + 1e-9
                    or right.x + right.width <= left.x + 1e-9
                    or left.y + left.height <= right.y + 1e-9
                    or right.y + right.height <= left.y + 1e-9
                ):
                    raise ValueError("LOCo fallback bounding boxes overlap")
        digest = semantic_sha256(self, excluded_fields={"stock_id", "content_sha256"})
        if self.stock_id != f"yfm11fb-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("LOCo fallback identity does not match semantic content")
        return self


class M11Payload(FrozenExperimentModel):
    """One source-order template referenced by one or more chronological events."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-event-payload.v1"] = "yieldforge.m11-event-payload.v1"
    payload_id: StrictStr = Field(pattern=r"^yfm11pl-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_kind: M11SourceKind
    source_case_id: StrictStr = Field(min_length=1)
    family_id: StrictStr = Field(pattern=r"^yfm11[lo]f-[0-9a-f]{24}$")
    geometry_references: tuple[M11GeometryReference, ...] = Field(min_length=1)
    quantities: tuple[StrictInt, ...] = Field(min_length=1)
    candidate_references: tuple[M11CandidateReference, ...] = Field(min_length=1)
    fallback_stock: M11FallbackStock | None = None
    geometry_provenance: Literal["source_observed"] = "source_observed"
    quantity_provenance: Literal["derived"] = "derived"

    @model_validator(mode="after")
    def require_source_semantics_and_identity(self) -> Self:
        if len(self.quantities) != len(self.geometry_references) or any(
            quantity <= 0 for quantity in self.quantities
        ):
            raise ValueError("payload quantity vector must align with source geometry")
        if self.source_kind == "lectra":
            if (
                len(self.geometry_references) != 1
                or self.geometry_references[0].kind != "lectra_m3_task"
                or self.quantities != (1,)
                or len(self.candidate_references) != 2
                or tuple(item.position for item in self.candidate_references) != (0, 1)
                or any(
                    item.provenance != "externally_anchored" for item in self.candidate_references
                )
                or self.fallback_stock is not None
            ):
                raise ValueError("Lectra payload must reference one complete M3 task and its pair")
        else:
            instances = {item.instance_name for item in self.geometry_references}
            expected_placements = sum(self.quantities)
            if (
                len(self.geometry_references) != 8
                or len({item.reference_id for item in self.geometry_references}) != 8
                or any(item.kind != "loco_item" for item in self.geometry_references)
                or len(instances) != 1
                or self.fallback_stock is None
                or len(self.candidate_references) != 1
                or self.candidate_references[0].candidate_id != self.fallback_stock.stock_id
                or self.candidate_references[0].content_sha256 != self.fallback_stock.content_sha256
                or self.candidate_references[0].provenance != "generated"
                or len(self.fallback_stock.placements) != expected_placements
            ):
                raise ValueError(
                    "LOCo payload must isolate eight rows and bind its feasible fallback"
                )
            expected_boxes = {
                (reference.reference_id, copy_index): (
                    reference.bbox_width,
                    reference.bbox_height,
                )
                for reference, quantity in zip(
                    self.geometry_references, self.quantities, strict=True
                )
                for copy_index in range(quantity)
            }
            actual_boxes = {
                (placement.geometry_reference_id, placement.copy_index): (
                    placement.width,
                    placement.height,
                )
                for placement in self.fallback_stock.placements
            }
            if actual_boxes != expected_boxes:
                raise ValueError("LOCo fallback placements do not match payload geometry copies")
        digest = semantic_sha256(self, excluded_fields={"payload_id", "content_sha256"})
        if self.payload_id != f"yfm11pl-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 payload identity does not match semantic content")
        return self


class M11Event(FrozenExperimentModel):
    """One generated chronological event with an immutable payload reference."""

    schema_version: Literal["yieldforge.m11-event.v1"] = "yieldforge.m11-event.v1"
    event_id: StrictStr = Field(pattern=r"^yfm11e-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    position: StrictInt = Field(ge=0, le=23)
    known_at: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    released_at: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    due_at: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    due_hours: StrictInt
    customer_id: StrictStr = Field(min_length=1)
    job_id: StrictStr = Field(min_length=1)
    family_id: StrictStr = Field(pattern=r"^yfm11[lo]f-[0-9a-f]{24}$")
    payload_id: StrictStr = Field(pattern=r"^yfm11pl-[0-9a-f]{24}$")
    material_key: StrictStr = Field(min_length=1)
    priority: StrictInt = Field(ge=1, le=3)
    payload_source_event_id: StrictStr | None = Field(
        default=None, pattern=r"^yfm11e-[0-9a-f]{24}$"
    )

    @model_validator(mode="after")
    def require_timing_and_identity(self) -> Self:
        known = _parse_timestamp(self.known_at)
        released = _parse_timestamp(self.released_at)
        due = _parse_timestamp(self.due_at)
        if released - known != timedelta(hours=24):
            raise ValueError("M11 event known_at must be exactly 24 hours before release")
        if self.due_hours not in (12, 24, 48, 72) or due - released != timedelta(
            hours=self.due_hours
        ):
            raise ValueError("M11 event due time differs from its registered bucket")
        digest = semantic_sha256(self, excluded_fields={"event_id", "content_sha256"})
        if self.event_id != f"yfm11e-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 event identity does not match semantic content")
        return self


class M11Stream(FrozenExperimentModel):
    """One primary or no-signal chronological stream."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-stream.v1"] = "yieldforge.m11-stream.v1"
    stream_id: StrictStr = Field(pattern=r"^yfm11st-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: M11CorpusId
    partition: M11PartitionKind
    stream_kind: M11StreamKind
    regime: M11Regime
    partition_ordinal: StrictInt = Field(ge=0)
    regime_ordinal: StrictInt = Field(ge=0)
    root_seed: Literal[2026082901] = M11_ROOT_SEED
    source_stream_id: StrictStr | None = Field(default=None, pattern=r"^yfm11st-[0-9a-f]{24}$")
    no_signal_control: StrictBool
    events: tuple[M11Event, ...]

    @model_validator(mode="after")
    def require_stream_shape_and_identity(self) -> Self:
        if len(self.events) != 24 or tuple(item.position for item in self.events) != tuple(
            range(24)
        ):
            raise ValueError("M11 streams require exactly 24 ordered event positions")
        releases = tuple(_parse_timestamp(item.released_at) for item in self.events)
        if releases != _release_schedule():
            raise ValueError("M11 stream release chronology differs from the frozen schedule")
        if Counter(item.due_hours for item in self.events) != {
            12: 4,
            24: 8,
            48: 8,
            72: 4,
        }:
            raise ValueError("M11 stream due-bucket multiplicities differ")
        if sorted(Counter(item.customer_id for item in self.events).values()) != [
            2,
            3,
            4,
            4,
            5,
            6,
        ]:
            raise ValueError("M11 stream customer multiplicities differ")
        if self.stream_kind == "primary":
            if any(item.payload_source_event_id is not None for item in self.events):
                raise ValueError("primary events cannot carry twin payload lineage")
            if self.source_stream_id is not None or self.no_signal_control:
                raise ValueError("primary streams cannot carry twin lineage")
            counts = Counter(item.payload_id for item in self.events)
            if self.regime == "recurrent" and sorted(counts.values()) != [6, 6, 6, 6]:
                raise ValueError("recurrent streams require four templates repeated six times")
            if self.regime == "mixed" and sorted(counts.values()) != [2] * 12:
                raise ValueError("mixed streams require twelve templates repeated twice")
            if self.regime == "high_mix" and len(counts) != 24:
                raise ValueError("high-mix streams require 24 distinct templates")
            if self.regime == "regime_shift" and not {
                item.family_id for item in self.events[:12]
            }.isdisjoint({item.family_id for item in self.events[12:]}):
                raise ValueError("regime-shift halves must use disjoint holdout-family subsets")
            if self.corpus_id == "lectra-m3-m4" and sorted(
                Counter(item.material_key for item in self.events).values()
            ) != [3, 3, 4, 6, 8]:
                raise ValueError("Lectra stream material multiplicities differ")
        else:
            if (
                self.partition != "confirmation"
                or self.source_stream_id is None
                or not self.no_signal_control
            ):
                raise ValueError("shuffled twins must be confirmation no-signal controls")
            if any(item.payload_source_event_id is None for item in self.events):
                raise ValueError("twin events require explicit source-event lineage")
        digest = semantic_sha256(self, excluded_fields={"stream_id", "content_sha256"})
        if self.stream_id != f"yfm11st-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 stream identity does not match semantic content")
        return self


class M11HoldoutFamily(FrozenExperimentModel):
    family_id: StrictStr = Field(pattern=r"^yfm11[lo]f-[0-9a-f]{24}$")
    source_family_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_instances: tuple[StrictStr, ...]
    source_case_ids: tuple[StrictStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_canonical_sets(self) -> Self:
        for values in (self.source_family_ids, self.source_instances, self.source_case_ids):
            if values != tuple(sorted(set(values))):
                raise ValueError("M11 holdout-family members must be sorted and unique")
        return self


class M11SourcePartition(FrozenExperimentModel):
    schema_version: Literal["yieldforge.m11-source-partition.v1"] = (
        "yieldforge.m11-source-partition.v1"
    )
    partition_id: StrictStr = Field(pattern=r"^yfm11sp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: M11CorpusId
    partition: M11PartitionKind
    families: tuple[M11HoldoutFamily, ...] = Field(min_length=1)

    @property
    def family_ids(self) -> tuple[str, ...]:
        return tuple(item.family_id for item in self.families)

    @property
    def source_family_ids(self) -> tuple[str, ...]:
        return tuple(sorted({value for item in self.families for value in item.source_family_ids}))

    @property
    def source_instances(self) -> tuple[str, ...]:
        return tuple(sorted({value for item in self.families for value in item.source_instances}))

    @property
    def family_count(self) -> int:
        return len(self.families)

    @property
    def source_case_count(self) -> int:
        return sum(len(item.source_case_ids) for item in self.families)

    @model_validator(mode="after")
    def require_disjoint_canonical_families_and_identity(self) -> Self:
        if self.families != tuple(sorted(self.families, key=lambda item: item.family_id)):
            raise ValueError("M11 partition families must use canonical ID order")
        cases = tuple(value for item in self.families for value in item.source_case_ids)
        source_families = tuple(value for item in self.families for value in item.source_family_ids)
        if len(cases) != len(set(cases)) or len(source_families) != len(set(source_families)):
            raise ValueError("M11 holdout families overlap within a partition")
        digest = semantic_sha256(self, excluded_fields={"partition_id", "content_sha256"})
        if (
            self.partition_id != f"yfm11sp-{digest[:24]}"
            or self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("M11 source-partition identity does not match semantic content")
        return self


class M11EconomicProfile(FrozenExperimentModel):
    arm: M11EconomicArm
    virgin_cost_per_reference_area: StrictFloat
    scrap_and_terminal_credit: StrictFloat
    return_handling: StrictFloat
    retrieval_handling: StrictFloat
    storage_per_reference_area_30_days: StrictFloat
    provenance: Literal["assumed"] = "assumed"

    @model_validator(mode="after")
    def require_frozen_rates(self) -> Self:
        expected = {
            "optimistic": (100.0, 0.0, 0.0, 0.0, 0.0),
            "central": (100.0, 10.0, 0.25, 0.25, 0.5),
            "adverse": (100.0, 25.0, 1.0, 1.0, 2.0),
        }[self.arm]
        actual = (
            self.virgin_cost_per_reference_area,
            self.scrap_and_terminal_credit,
            self.return_handling,
            self.retrieval_handling,
            self.storage_per_reference_area_30_days,
        )
        if actual != expected:
            raise ValueError("M11 frozen economic profile rates differ")
        return self


class M11ReferenceAreas(FrozenExperimentModel):
    corpus_id: M11CorpusId
    policy: Literal["fixed_lectra_median", "per_instance_median_verified_fallback"]
    by_material: tuple[tuple[StrictStr, StrictFloat], ...]
    provenance: Literal["derived"] = "derived"

    @model_validator(mode="after")
    def require_canonical_positive_areas(self) -> Self:
        if self.by_material != tuple(sorted(self.by_material)) or any(
            value <= 0 for _, value in self.by_material
        ):
            raise ValueError("reference areas must be positive and canonically ordered")
        return self


class M11HardNull(FrozenExperimentModel):
    schema_version: Literal["yieldforge.m11-hard-null.v1"] = "yieldforge.m11-hard-null.v1"
    null_id: StrictStr = Field(pattern=r"^yfm11null-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: M11CorpusId
    null_kind: Literal[
        "single_action", "unique_materials_single_action", "all_work_known_single_action"
    ]
    source_stream_id: StrictStr = Field(pattern=r"^yfm11st-[0-9a-f]{24}$")
    event_ids: tuple[StrictStr, StrictStr, StrictStr]
    baseline_action_count: Literal[1] = 1
    future_action_count: Literal[1] = 1
    unique_material_per_event: StrictBool
    all_work_known: StrictBool
    expected_savings_percent: Literal[0.0] = 0.0
    zero_savings_semantics: Literal[
        "identical_single_feasible_action_and_ledger_for_baseline_and_future"
    ] = "identical_single_feasible_action_and_ledger_for_baseline_and_future"

    @model_validator(mode="after")
    def require_kind_and_identity(self) -> Self:
        expected = {
            "single_action": (False, False),
            "unique_materials_single_action": (True, False),
            "all_work_known_single_action": (False, True),
        }[self.null_kind]
        if (self.unique_material_per_event, self.all_work_known) != expected:
            raise ValueError("hard-null flags differ from their frozen construction")
        digest = semantic_sha256(self, excluded_fields={"null_id", "content_sha256"})
        if self.null_id != f"yfm11null-{digest[:24]}" or self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M11 hard-null identity does not match semantic content")
        return self


class M11ExactAuditEpisode(FrozenExperimentModel):
    schema_version: Literal["yieldforge.m11-exact-audit.v1"] = "yieldforge.m11-exact-audit.v1"
    audit_id: StrictStr = Field(pattern=r"^yfm11audit-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: M11CorpusId
    audit_ordinal: StrictInt = Field(ge=0, le=5)
    source_stream_id: StrictStr = Field(pattern=r"^yfm11st-[0-9a-f]{24}$")
    event_positions: tuple[StrictInt, StrictInt, StrictInt]
    event_ids: tuple[StrictStr, StrictStr, StrictStr]
    economic_arm: Literal["central", "adverse", "null"]
    search_contract: Literal["exhaustive_three_event_exact_search"] = (
        "exhaustive_three_event_exact_search"
    )

    @model_validator(mode="after")
    def require_frozen_position_arm_and_identity(self) -> Self:
        if (
            self.event_positions != M11_AUDIT_POSITIONS[self.audit_ordinal]
            or self.economic_arm != M11_AUDIT_ARMS[self.audit_ordinal]
        ):
            raise ValueError("exact-audit positions or arm differ from the frozen registry")
        digest = semantic_sha256(self, excluded_fields={"audit_id", "content_sha256"})
        if (
            self.audit_id != f"yfm11audit-{digest[:24]}"
            or self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("M11 exact-audit identity does not match semantic content")
        return self


class M11PopulationCensus(FrozenExperimentModel):
    primary_stream_count: Literal[56] = 56
    calibration_stream_count: Literal[16] = 16
    confirmation_stream_count: Literal[40] = 40
    twin_stream_count: Literal[40] = 40
    hard_null_count: Literal[6] = 6
    exact_audit_count: Literal[12] = 12
    registered_id_count: Literal[114] = 114
    primary_event_count: Literal[1344] = 1_344
    twin_event_count: Literal[960] = 960


class M11Population(FrozenExperimentModel):
    """The complete compact event population, controls, and provenance registry."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-population.v1"] = "yieldforge.m11-population.v1"
    population_id: StrictStr = Field(pattern=r"^yfm11pop-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(pattern=r"^yfm11c-[0-9a-f]{24}$")
    contract_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_catalog_id: Literal["yflc-14dd63c1d1b690236b8f6393"]
    source_catalog_manifest_id: Literal["yflcm-9f5bbd1ee54e18e5fa1cb86f"]
    source_manifest_id: Literal["yfm11sm-54426d56dcccc07b667da56f"]
    source_manifest_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    root_seed: Literal[2026082901] = M11_ROOT_SEED
    selection_inputs: tuple[StrictStr, ...]
    prohibited_selection_fields: tuple[StrictStr, ...]
    source_partitions: tuple[M11SourcePartition, ...]
    economic_profiles: tuple[M11EconomicProfile, ...]
    reference_areas: tuple[M11ReferenceAreas, ...]
    field_provenance: tuple[M11FieldProvenance, ...]
    payloads: tuple[M11Payload, ...]
    streams: tuple[M11Stream, ...]
    hard_nulls: tuple[M11HardNull, ...]
    exact_audits: tuple[M11ExactAuditEpisode, ...]
    census: M11PopulationCensus = Field(default_factory=M11PopulationCensus)
    outcome_scoring_present: Literal[False] = False

    @model_validator(mode="after")
    def require_complete_population_and_identity(self) -> Self:
        if self.selection_inputs != M11_SELECTION_INPUTS:
            raise ValueError("M11 selection inputs differ from the outcome-blind registry")
        if self.prohibited_selection_fields != M11_PROHIBITED_SELECTION_FIELDS:
            raise ValueError("M11 prohibited selection fields differ from the frozen registry")
        if self.field_provenance != _field_provenance():
            raise ValueError("M11 field provenance differs from the frozen exhaustive registry")
        if tuple(item.arm for item in self.economic_profiles) != (
            "optimistic",
            "central",
            "adverse",
        ):
            raise ValueError("M11 economic profiles must use frozen arm order")
        if tuple(item.corpus_id for item in self.reference_areas) != ("lectra-m3-m4", "loco-2dics"):
            raise ValueError("M11 reference-area registry differs")
        if len(self.source_partitions) != 4:
            raise ValueError("M11 population requires two partitions for each corpus")
        partition_specs = (
            ("lectra-m3-m4", "calibration", 17, 58),
            ("lectra-m3-m4", "confirmation", 52, 145),
            ("loco-2dics", "calibration", 5, 146),
            ("loco-2dics", "confirmation", 4, 365),
        )
        actual_partitions = tuple(
            (
                item.corpus_id,
                item.partition,
                item.family_count,
                item.source_case_count,
            )
            for item in self.source_partitions
        )
        if actual_partitions != partition_specs:
            raise ValueError("M11 source partitions differ from the frozen family census")
        if self.source_partitions[2].source_instances != tuple(sorted(_CALIBRATION_LOCO_INSTANCES)):
            raise ValueError("M11 LOCo calibration instances differ from the frozen singletons")
        payload_ids = tuple(item.payload_id for item in self.payloads)
        if payload_ids != tuple(sorted(set(payload_ids))):
            raise ValueError("M11 payload catalog must be sorted and unique")
        stream_ids = tuple(item.stream_id for item in self.streams)
        registered = (
            stream_ids
            + tuple(item.null_id for item in self.hard_nulls)
            + tuple(item.audit_id for item in self.exact_audits)
        )
        if len(registered) != 114 or len(set(registered)) != 114:
            raise ValueError("M11 registered IDs must contain exactly 114 unique values")
        primary = tuple(item for item in self.streams if item.stream_kind == "primary")
        calibration = tuple(item for item in primary if item.partition == "calibration")
        confirmation = tuple(item for item in primary if item.partition == "confirmation")
        twins = tuple(item for item in self.streams if item.stream_kind == "shuffled_twin")
        if (len(primary), len(calibration), len(confirmation), len(twins)) != (56, 16, 40, 40):
            raise ValueError("M11 stream census differs from the frozen population")
        if len(self.hard_nulls) != 6 or len(self.exact_audits) != 12:
            raise ValueError("M11 control census differs from the frozen population")
        payloads_by_id = {item.payload_id: item for item in self.payloads}
        for stream in self.streams:
            for event in stream.events:
                payload = payloads_by_id.get(event.payload_id)
                if payload is None:
                    raise ValueError("M11 event references an absent compact payload")
                if event.family_id != payload.family_id:
                    raise ValueError("M11 event family does not match its compact payload")
                if stream.stream_kind == "primary" and payload.source_kind == "loco_2dics":
                    expected_material = f"loco:{payload.geometry_references[0].instance_name}"
                    if event.material_key != expected_material:
                        raise ValueError("M11 LOCo event material crosses its source instance")
        streams_by_id = {item.stream_id: item for item in self.streams}
        for twin in twins:
            source = streams_by_id.get(twin.source_stream_id or "")
            if (
                source is None
                or source.stream_kind != "primary"
                or source.partition != "confirmation"
            ):
                raise ValueError("M11 twin does not bind a confirmation primary stream")
            if Counter(item.payload_id for item in twin.events) != Counter(
                item.payload_id for item in source.events
            ) or any(
                twin.events[index].payload_id == source.events[index].payload_id
                for index in range(24)
            ):
                raise ValueError("M11 twin is not a no-fixed-payload derangement")
            if len({item.material_key for item in twin.events}) != 24:
                raise ValueError("M11 twin materials must be unique by event")
            stable_fields = ("known_at", "released_at", "due_at", "customer_id", "job_id")
            if any(
                tuple(getattr(item, field) for item in twin.events)
                != tuple(getattr(item, field) for item in source.events)
                for field in stable_fields
            ):
                raise ValueError("M11 twin changes frozen chronology or customer/job identity")
        for control in (*self.hard_nulls, *self.exact_audits):
            source = streams_by_id.get(control.source_stream_id)
            if source is None or source.corpus_id != control.corpus_id:
                raise ValueError("M11 control crosses or omits its source stream")
        digest = semantic_sha256(self, excluded_fields={"population_id", "content_sha256"})
        if (
            self.population_id != f"yfm11pop-{digest[:24]}"
            or self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("M11 population identity does not match semantic content")
        return self


class M11PackBundle(NamedTuple):
    contract: M11ExperimentContract
    population: M11Population


class M11PackArtifactBytes(NamedTuple):
    contract: bytes
    population: bytes


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("M11 timestamps must use canonical UTC seconds") from error
    return parsed


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _release_schedule() -> tuple[datetime, ...]:
    day = datetime(2026, 1, 5, tzinfo=UTC)
    days: list[datetime] = []
    while len(days) < 8:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(base + timedelta(hours=hour) for base in days for hour in (8, 11, 14))


def visible_event_positions(stream: M11Stream, as_of: str) -> tuple[int, ...]:
    """Return the firm-schedule prefix visible at one canonical as-of time."""

    moment = _parse_timestamp(as_of)
    visible = tuple(
        item.position for item in stream.events if _parse_timestamp(item.known_at) <= moment
    )
    if visible != tuple(range(len(visible))):
        raise PackEvidenceError("firm-schedule visibility is not a release-ordered prefix")
    return visible


class _UnionFind:
    def __init__(self, values: tuple[object, ...]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: object) -> object:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: object, right: object) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _build_partition(
    *,
    corpus_id: M11CorpusId,
    partition: M11PartitionKind,
    families: tuple[M11HoldoutFamily, ...],
) -> M11SourcePartition:
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-source-partition.v1",
        "corpus_id": corpus_id,
        "partition": partition,
        "families": [
            item.model_dump(mode="json") for item in sorted(families, key=lambda x: x.family_id)
        ],
    }
    identifier, content = _identity("yfm11sp-", semantic)
    return M11SourcePartition(
        partition_id=identifier,
        content_sha256=content,
        schema_version="yieldforge.m11-source-partition.v1",
        corpus_id=corpus_id,
        partition=partition,
        families=tuple(sorted(families, key=lambda item: item.family_id)),
    )


def derive_lectra_holdout_partitions(
    expected_task_ids: tuple[int, ...], lectra_catalog: dict[str, object]
) -> tuple[M11SourcePartition, M11SourcePartition]:
    """Derive the exact family split using only task IDs and source shape hashes."""

    task_ids = tuple(expected_task_ids)
    if task_ids != tuple(sorted(set(task_ids))) or len(task_ids) != 203:
        raise PackEvidenceError("Lectra holdout requires the exact 203 sorted M3 task IDs")
    allowed = set(task_ids)
    shapes_by_task: dict[int, set[int]] = {value: set() for value in task_ids}
    try:
        parts = lectra_catalog["parts"]
        if not isinstance(parts, list):
            raise TypeError
        for row in parts:
            task = row["tasks_index"]
            shape = row["shape_hash"]
            if type(task) is int and type(shape) is int and task in allowed:
                shapes_by_task[task].add(shape)
    except (KeyError, TypeError) as error:
        raise PackEvidenceError("Lectra catalog lacks task-to-shape source evidence") from error
    if any(not values for values in shapes_by_task.values()):
        raise PackEvidenceError("Lectra task has no source shape-family evidence")

    union = _UnionFind(tuple(task_ids))
    first_for_shape: dict[int, int] = {}
    for task in task_ids:
        for shape in shapes_by_task[task]:
            if shape in first_for_shape:
                union.union(task, first_for_shape[shape])
            else:
                first_for_shape[shape] = task
    components: dict[object, list[int]] = defaultdict(list)
    for task in task_ids:
        components[union.find(task)].append(task)

    split: dict[str, list[M11HoldoutFamily]] = {"calibration": [], "confirmation": []}
    for tasks in components.values():
        ordered = tuple(sorted(tasks))
        split_key = hashlib.sha256("|".join(str(value) for value in ordered).encode()).hexdigest()
        selector = hashlib.sha256(
            f"m11|lectra|family-split-v1|m11|{split_key}".encode()
        ).hexdigest()
        partition = "calibration" if int(selector, 16) % 7 < 2 else "confirmation"
        source_shapes = tuple(sorted({shape for task in ordered for shape in shapes_by_task[task]}))
        family_digest = hashlib.sha256(
            ("lectra-family-v1|" + "|".join(str(value) for value in ordered)).encode()
        ).hexdigest()
        split[partition].append(
            M11HoldoutFamily(
                family_id=f"yfm11lf-{family_digest[:24]}",
                source_family_ids=tuple(sorted(f"lectra-shape:{value}" for value in source_shapes)),
                source_instances=(),
                source_case_ids=tuple(sorted(f"lectra-task:{value}" for value in ordered)),
            )
        )
    result = (
        _build_partition(
            corpus_id="lectra-m3-m4", partition="calibration", families=tuple(split["calibration"])
        ),
        _build_partition(
            corpus_id="lectra-m3-m4",
            partition="confirmation",
            families=tuple(split["confirmation"]),
        ),
    )
    if tuple((item.family_count, item.source_case_count) for item in result) != (
        (17, 58),
        (52, 145),
    ):
        raise PackEvidenceError(
            "Lectra family split differs from the frozen 17/58 and 52/145 census"
        )
    if set(result[0].source_family_ids).intersection(result[1].source_family_ids):
        raise PackEvidenceError("Lectra calibration and confirmation share a source shape family")
    return result


def _derive_loco_holdout_partitions(
    catalog: LOCoCatalog,
) -> tuple[M11SourcePartition, M11SourcePartition]:
    items_by_instance: dict[str, list[LOCoItem]] = defaultdict(list)
    instances_by_family: dict[str, set[str]] = defaultdict(set)
    for item in catalog.items:
        items_by_instance[item.instance_name].append(item)
        instances_by_family[item.scale_invariant_family_id].add(item.instance_name)
    instances = tuple(sorted(items_by_instance))
    union = _UnionFind(instances)
    for members in instances_by_family.values():
        ordered = sorted(members)
        for member in ordered[1:]:
            union.union(ordered[0], member)
    components: dict[object, list[str]] = defaultdict(list)
    for instance in instances:
        components[union.find(instance)].append(instance)
    if len(components) != 9:
        raise PackEvidenceError("LOCo scale-family holdout graph does not have nine components")

    split: dict[str, list[M11HoldoutFamily]] = {"calibration": [], "confirmation": []}
    for component in components.values():
        names = tuple(sorted(component))
        is_calibration = set(names).issubset(_CALIBRATION_LOCO_INSTANCES)
        if is_calibration and len(names) != 1:
            raise PackEvidenceError(
                "LOCo calibration components must be the five frozen singletons"
            )
        partition = "calibration" if is_calibration else "confirmation"
        component_items = tuple(
            sorted(
                (item for name in names for item in items_by_instance[name]),
                key=lambda item: (item.source_member, item.source_item_index),
            )
        )
        source_families = tuple(
            sorted({item.scale_invariant_family_id for item in component_items})
        )
        family_digest = hashlib.sha256(
            ("loco-holdout-family-v1|" + "|".join(names)).encode()
        ).hexdigest()
        split[partition].append(
            M11HoldoutFamily(
                family_id=f"yfm11of-{family_digest[:24]}",
                source_family_ids=source_families,
                source_instances=names,
                source_case_ids=tuple(sorted(item.item_id for item in component_items)),
            )
        )
    result = (
        _build_partition(
            corpus_id="loco-2dics", partition="calibration", families=tuple(split["calibration"])
        ),
        _build_partition(
            corpus_id="loco-2dics", partition="confirmation", families=tuple(split["confirmation"])
        ),
    )
    if tuple((item.family_count, item.source_case_count) for item in result) != (
        (5, 146),
        (4, 365),
    ):
        raise PackEvidenceError("LOCo holdout split differs from the frozen 5/146 and 4/365 census")
    if result[0].source_instances != tuple(sorted(_CALIBRATION_LOCO_INSTANCES)):
        raise PackEvidenceError("LOCo calibration instances differ from the five frozen singletons")
    if set(result[0].source_family_ids).intersection(result[1].source_family_ids):
        raise PackEvidenceError("LOCo calibration and confirmation share a scale family")
    return result


def _shelf_layout(
    references: tuple[M11GeometryReference, ...], quantities: tuple[int, ...], stock_width: float
) -> tuple[float, tuple[M11FallbackPlacement, ...]]:
    units = [
        (reference, copy_index)
        for reference, quantity in zip(references, quantities, strict=True)
        for copy_index in range(quantity)
    ]
    units.sort(
        key=lambda value: (
            -float(value[0].bbox_height or 0.0),
            -float(value[0].bbox_width or 0.0),
            value[0].reference_id,
            value[1],
        )
    )
    shelves: list[dict[str, float]] = []
    placements: list[M11FallbackPlacement] = []
    for reference, copy_index in units:
        width = float(reference.bbox_width or 0.0)
        height = float(reference.bbox_height or 0.0)
        shelf = next((item for item in shelves if item["x"] + width <= stock_width + 1e-9), None)
        if shelf is None:
            shelf = {
                "x": 0.0,
                "y": sum(item["height"] for item in shelves),
                "height": height,
            }
            shelves.append(shelf)
        placements.append(
            M11FallbackPlacement(
                geometry_reference_id=reference.reference_id,
                copy_index=copy_index,
                x=float(shelf["x"]),
                y=float(shelf["y"]),
                width=width,
                height=height,
            )
        )
        shelf["x"] += width
    return float(sum(item["height"] for item in shelves)), tuple(placements)


def build_loco_fallback(
    references: tuple[M11GeometryReference, ...], quantities: tuple[int, ...]
) -> M11FallbackStock:
    """Build and content-address the registered eight-width bbox-shelf witness."""

    if len(references) != 8 or len(quantities) != 8:
        raise PackEvidenceError("LOCo fallback requires exactly eight distinct source rows")
    maximum_width = max(float(item.bbox_width or 0.0) for item in references)
    candidate_widths = tuple(maximum_width * value for value in LOCO_TARGET_WIDTH_MULTIPLIERS)
    layouts = tuple(_shelf_layout(references, quantities, width) for width in candidate_widths)
    candidate_heights = tuple(height for height, _placements in layouts)
    selected = min(
        range(8),
        key=lambda index: (
            candidate_widths[index] * candidate_heights[index],
            candidate_widths[index],
            index,
        ),
    )
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-loco-fallback.v1",
        "algorithm": "first_fit_decreasing_bbox_shelves_min_area_width_index",
        "target_width_multipliers": list(LOCO_TARGET_WIDTH_MULTIPLIERS),
        "candidate_widths": list(candidate_widths),
        "candidate_heights": list(candidate_heights),
        "selected_width_index": selected,
        "width": candidate_widths[selected],
        "height": candidate_heights[selected],
        "area": candidate_widths[selected] * candidate_heights[selected],
        "placements": [item.model_dump(mode="json") for item in layouts[selected][1]],
    }
    identifier, content = _identity("yfm11fb-", semantic)
    return _strict_json_model(
        M11FallbackStock,
        {"stock_id": identifier, "content_sha256": content, **semantic},
    )


def _geometry_reference(item: LOCoItem) -> M11GeometryReference:
    bounds = polygon_from_record(item.geometry).bounds
    return M11GeometryReference(
        kind="loco_item",
        reference_id=item.item_id,
        content_sha256=item.content_sha256,
        geometry_sha256=item.geometry.polygon_sha256,
        instance_name=item.instance_name,
        source_item_index=item.source_item_index,
        source_demand=item.source_demand,
        bbox_width=float(bounds[2] - bounds[0]),
        bbox_height=float(bounds[3] - bounds[1]),
    )


def _build_payload(semantic: dict[str, object]) -> M11Payload:
    identifier, content = _identity("yfm11pl-", semantic)
    return _strict_json_model(
        M11Payload,
        {"payload_id": identifier, "content_sha256": content, **semantic},
    )


def _lectra_payloads(
    m3: M3ResidualInputPack, partitions: tuple[M11SourcePartition, M11SourcePartition]
) -> dict[str, M11Payload]:
    family_by_case = {
        case: family.family_id
        for partition in partitions
        for family in partition.families
        for case in family.source_case_ids
    }
    payloads: dict[str, M11Payload] = {}
    for pair in m3.task_pairs:
        case = f"lectra-task:{pair.tasks_index}"
        task_reference_digest = hashlib.sha256(
            f"{m3.input_id}|{m3.content_sha256}|{pair.tasks_index}".encode()
        ).hexdigest()
        candidates = tuple(
            M11CandidateReference(
                position=position,
                candidate_id=selected.candidate.candidate_id,
                content_sha256="sha256:"
                + hashlib.sha256(
                    (
                        f"{m3.input_id}|{m3.content_sha256}|{pair.tasks_index}|{position}|"
                        f"{selected.archive_batch_sha256}|{selected.candidate.candidate_id}"
                    ).encode()
                ).hexdigest(),
                provenance="externally_anchored",
            )
            for position, selected in enumerate(pair.selected_candidates)
        )
        semantic: dict[str, object] = {
            "schema_version": "yieldforge.m11-event-payload.v1",
            "source_kind": "lectra",
            "source_case_id": case,
            "family_id": family_by_case[case],
            "geometry_references": [
                M11GeometryReference(
                    kind="lectra_m3_task",
                    reference_id=case,
                    content_sha256=f"sha256:{task_reference_digest}",
                    geometry_sha256=task_reference_digest,
                ).model_dump(mode="json")
            ],
            "quantities": [1],
            "candidate_references": [item.model_dump(mode="json") for item in candidates],
            "fallback_stock": None,
            "geometry_provenance": "source_observed",
            "quantity_provenance": "derived",
        }
        payloads[case] = _build_payload(semantic)
    return payloads


def _canonical_loco_items(items: tuple[LOCoItem, ...]) -> tuple[LOCoItem, ...]:
    return tuple(
        sorted(
            items,
            key=lambda item: (item.instance_name, item.source_item_index, item.item_id),
        )
    )


def _loco_composition_key(items: tuple[LOCoItem, ...]) -> tuple[tuple[str, int], ...]:
    return tuple(
        (item.item_id, 1 + (item.source_demand - 1) // 25) for item in _canonical_loco_items(items)
    )


def _loco_payload(items: tuple[LOCoItem, ...], family_id: str) -> M11Payload:
    canonical_items = _canonical_loco_items(items)
    references = tuple(_geometry_reference(item) for item in canonical_items)
    quantities = tuple(quantity for _item_id, quantity in _loco_composition_key(canonical_items))
    if any(value > 4 for value in quantities):
        raise PackEvidenceError("LOCo quantity transform exceeded its frozen cap")
    fallback = build_loco_fallback(references, quantities)
    case_digest = hashlib.sha256(
        "|".join(
            f"{item_id}:{quantity}" for item_id, quantity in _loco_composition_key(items)
        ).encode()
    ).hexdigest()
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-event-payload.v1",
        "source_kind": "loco_2dics",
        "source_case_id": f"loco-batch:{case_digest[:24]}",
        "family_id": family_id,
        "geometry_references": [item.model_dump(mode="json") for item in references],
        "quantities": list(quantities),
        "candidate_references": [
            M11CandidateReference(
                position=0,
                candidate_id=fallback.stock_id,
                content_sha256=fallback.content_sha256,
                provenance="generated",
            ).model_dump(mode="json")
        ],
        "fallback_stock": fallback.model_dump(mode="json"),
        "geometry_provenance": "source_observed",
        "quantity_provenance": "derived",
    }
    return _build_payload(semantic)


def _sample_without_outcomes(values: tuple[str, ...], count: int, label: str) -> tuple[str, ...]:
    if count > len(values):
        raise PackEvidenceError(
            "registered regime requests more distinct source cases than available"
        )
    generator = random.Random(m11_subseed(label))
    return tuple(generator.sample(list(values), count))


def _lectra_sequence(
    *,
    partition: M11SourcePartition,
    regime: M11Regime,
    label: str,
    payload_by_case: dict[str, M11Payload],
) -> tuple[M11Payload, ...]:
    cases = tuple(case for family in partition.families for case in family.source_case_ids)
    if regime == "recurrent":
        templates = _sample_without_outcomes(cases, 4, f"{label}|templates")
        sequence = list(templates * 6)
        random.Random(m11_subseed(f"{label}|payload-order")).shuffle(sequence)
        return tuple(payload_by_case[value] for value in sequence)
    if regime == "mixed":
        templates = _sample_without_outcomes(cases, 12, f"{label}|templates")
        sequence = list(templates * 2)
        random.Random(m11_subseed(f"{label}|payload-order")).shuffle(sequence)
        return tuple(payload_by_case[value] for value in sequence)
    if regime == "high_mix":
        return tuple(
            payload_by_case[value]
            for value in _sample_without_outcomes(cases, 24, f"{label}|templates")
        )
    families = list(partition.families)
    random.Random(m11_subseed(f"{label}|family-halves")).shuffle(families)
    cut = len(families) // 2
    halves = (families[:cut], families[cut:])
    output: list[M11Payload] = []
    for half_index, half in enumerate(halves):
        half_cases = tuple(case for family in half for case in family.source_case_ids)
        selected = _sample_without_outcomes(half_cases, 12, f"{label}|half-{half_index}|templates")
        output.extend(payload_by_case[value] for value in selected)
    return tuple(output)


def _loco_sequence(
    *,
    partition: M11SourcePartition,
    regime: M11Regime,
    label: str,
    items_by_instance: dict[str, tuple[LOCoItem, ...]],
    payload_catalog: dict[str, M11Payload],
) -> tuple[M11Payload, ...]:
    families: tuple[M11HoldoutFamily, ...] = partition.families

    def unique_templates(
        count: int, allowed: tuple[M11HoldoutFamily, ...], operation: str
    ) -> tuple[M11Payload, ...]:
        selected: list[M11Payload] = []
        seen: set[tuple[tuple[str, int], ...]] = set()
        attempt = 0
        while len(selected) < count:
            generator = random.Random(m11_subseed(f"{label}|{operation}|attempt-{attempt}"))
            family = generator.choice(allowed)
            instance = generator.choice(family.source_instances)
            items = tuple(generator.sample(list(items_by_instance[instance]), 8))
            composition = _loco_composition_key(items)
            attempt += 1
            if attempt > 10_000:
                raise PackEvidenceError("could not construct distinct LOCo templates")
            if composition in seen:
                continue
            seen.add(composition)
            payload = _loco_payload(items, family.family_id)
            payload_catalog[payload.payload_id] = payload
            selected.append(payload)
        return tuple(selected)

    if regime == "recurrent":
        templates = unique_templates(4, families, "templates")
        sequence = list(templates * 6)
        random.Random(m11_subseed(f"{label}|payload-order")).shuffle(sequence)
        return tuple(sequence)
    if regime == "mixed":
        templates = unique_templates(12, families, "templates")
        sequence = list(templates * 2)
        random.Random(m11_subseed(f"{label}|payload-order")).shuffle(sequence)
        return tuple(sequence)
    if regime == "high_mix":
        return unique_templates(24, families, "templates")
    shuffled = list(families)
    random.Random(m11_subseed(f"{label}|family-halves")).shuffle(shuffled)
    cut = len(shuffled) // 2
    return unique_templates(12, tuple(shuffled[:cut]), "first-half") + unique_templates(
        12, tuple(shuffled[cut:]), "second-half"
    )


def _shuffled_multiset(values: list[object], label: str) -> list[object]:
    result = list(values)
    random.Random(m11_subseed(label)).shuffle(result)
    return result


def _build_event(semantic: dict[str, object]) -> M11Event:
    identifier, content = _identity("yfm11e-", semantic)
    return _strict_json_model(
        M11Event,
        {"event_id": identifier, "content_sha256": content, **semantic},
    )


def _primary_stream(
    *,
    corpus_id: M11CorpusId,
    partition: M11PartitionKind,
    regime: M11Regime,
    partition_ordinal: int,
    regime_ordinal: int,
    payloads: tuple[M11Payload, ...],
    label: str,
) -> M11Stream:
    releases = _release_schedule()
    due_hours = _shuffled_multiset([12] * 4 + [24] * 8 + [48] * 8 + [72] * 4, f"{label}|due")
    customers = _shuffled_multiset(
        [
            f"customer-{index + 1}"
            for index, count in enumerate((6, 5, 4, 4, 3, 2))
            for _ in range(count)
        ],
        f"{label}|customers",
    )
    priorities = _shuffled_multiset([1] * 12 + [2] * 8 + [3] * 4, f"{label}|priorities")
    materials: list[object] | None = None
    if corpus_id == "lectra-m3-m4":
        materials = _shuffled_multiset(
            [
                f"lectra-material-{index + 1}"
                for index, count in enumerate((8, 6, 4, 3, 3))
                for _ in range(count)
            ],
            f"{label}|materials",
        )
    events: list[M11Event] = []
    for position, (released, payload) in enumerate(zip(releases, payloads, strict=True)):
        due = int(due_hours[position])
        if materials is None:
            instance = payload.geometry_references[0].instance_name
            material = f"loco:{instance}"
        else:
            material = str(materials[position])
        semantic: dict[str, object] = {
            "schema_version": "yieldforge.m11-event.v1",
            "position": position,
            "known_at": _timestamp(released - timedelta(hours=24)),
            "released_at": _timestamp(released),
            "due_at": _timestamp(released + timedelta(hours=due)),
            "due_hours": due,
            "customer_id": str(customers[position]),
            "job_id": f"job:{corpus_id}:{partition}:{partition_ordinal:02d}:{position:02d}",
            "family_id": payload.family_id,
            "payload_id": payload.payload_id,
            "material_key": material,
            "priority": int(priorities[position]),
            "payload_source_event_id": None,
        }
        events.append(_build_event(semantic))
    stream_semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-stream.v1",
        "corpus_id": corpus_id,
        "partition": partition,
        "stream_kind": "primary",
        "regime": regime,
        "partition_ordinal": partition_ordinal,
        "regime_ordinal": regime_ordinal,
        "root_seed": M11_ROOT_SEED,
        "source_stream_id": None,
        "no_signal_control": False,
        "events": [item.model_dump(mode="json") for item in events],
    }
    identifier, content = _identity("yfm11st-", stream_semantic)
    return _strict_json_model(
        M11Stream,
        {"stream_id": identifier, "content_sha256": content, **stream_semantic},
    )


def _payload_derangement(events: tuple[M11Event, ...], label: str) -> tuple[int, ...]:
    indexes = list(range(24))
    generator = random.Random(m11_subseed(label))
    for _ in range(10_000):
        generator.shuffle(indexes)
        if all(
            events[indexes[position]].payload_id != events[position].payload_id
            for position in range(24)
        ):
            return tuple(indexes)
    raise PackEvidenceError("could not construct a no-fixed-payload confirmation twin")


def _twin_stream(source: M11Stream, label: str) -> M11Stream:
    permutation = _payload_derangement(source.events, f"{label}|payload-derangement")
    events: list[M11Event] = []
    for position, source_event in enumerate(source.events):
        payload_event = source.events[permutation[position]]
        semantic: dict[str, object] = {
            "schema_version": "yieldforge.m11-event.v1",
            "position": position,
            "known_at": source_event.known_at,
            "released_at": source_event.released_at,
            "due_at": source_event.due_at,
            "due_hours": source_event.due_hours,
            "customer_id": source_event.customer_id,
            "job_id": source_event.job_id,
            "family_id": payload_event.family_id,
            "payload_id": payload_event.payload_id,
            "material_key": f"twin:{source.stream_id}:{position:02d}",
            "priority": source_event.priority,
            "payload_source_event_id": payload_event.event_id,
        }
        events.append(_build_event(semantic))
    semantic_stream: dict[str, object] = {
        "schema_version": "yieldforge.m11-stream.v1",
        "corpus_id": source.corpus_id,
        "partition": "confirmation",
        "stream_kind": "shuffled_twin",
        "regime": source.regime,
        "partition_ordinal": source.partition_ordinal,
        "regime_ordinal": source.regime_ordinal,
        "root_seed": M11_ROOT_SEED,
        "source_stream_id": source.stream_id,
        "no_signal_control": True,
        "events": [item.model_dump(mode="json") for item in events],
    }
    identifier, content = _identity("yfm11st-", semantic_stream)
    return _strict_json_model(
        M11Stream,
        {"stream_id": identifier, "content_sha256": content, **semantic_stream},
    )


def _hard_nulls(
    corpus_id: M11CorpusId, confirmation: tuple[M11Stream, ...]
) -> tuple[M11HardNull, ...]:
    result: list[M11HardNull] = []
    specifications = (
        ("single_action", False, False),
        ("unique_materials_single_action", True, False),
        ("all_work_known_single_action", False, True),
    )
    for ordinal, (kind, unique, known) in enumerate(specifications):
        source = confirmation[ordinal]
        semantic: dict[str, object] = {
            "schema_version": "yieldforge.m11-hard-null.v1",
            "corpus_id": corpus_id,
            "null_kind": kind,
            "source_stream_id": source.stream_id,
            "event_ids": [item.event_id for item in source.events[:3]],
            "baseline_action_count": 1,
            "future_action_count": 1,
            "unique_material_per_event": unique,
            "all_work_known": known,
            "expected_savings_percent": 0.0,
            "zero_savings_semantics": (
                "identical_single_feasible_action_and_ledger_for_baseline_and_future"
            ),
        }
        identifier, content = _identity("yfm11null-", semantic)
        result.append(
            _strict_json_model(
                M11HardNull,
                {"null_id": identifier, "content_sha256": content, **semantic},
            )
        )
    return tuple(result)


def _exact_audits(
    corpus_id: M11CorpusId, confirmation: tuple[M11Stream, ...]
) -> tuple[M11ExactAuditEpisode, ...]:
    result: list[M11ExactAuditEpisode] = []
    for ordinal, (positions, arm) in enumerate(
        zip(M11_AUDIT_POSITIONS, M11_AUDIT_ARMS, strict=True)
    ):
        source = confirmation[ordinal]
        semantic: dict[str, object] = {
            "schema_version": "yieldforge.m11-exact-audit.v1",
            "corpus_id": corpus_id,
            "audit_ordinal": ordinal,
            "source_stream_id": source.stream_id,
            "event_positions": list(positions),
            "event_ids": [source.events[position].event_id for position in positions],
            "economic_arm": arm,
            "search_contract": "exhaustive_three_event_exact_search",
        }
        identifier, content = _identity("yfm11audit-", semantic)
        result.append(
            _strict_json_model(
                M11ExactAuditEpisode,
                {"audit_id": identifier, "content_sha256": content, **semantic},
            )
        )
    return tuple(result)


def _economic_profiles() -> tuple[M11EconomicProfile, ...]:
    return (
        M11EconomicProfile(
            arm="optimistic",
            virgin_cost_per_reference_area=100.0,
            scrap_and_terminal_credit=0.0,
            return_handling=0.0,
            retrieval_handling=0.0,
            storage_per_reference_area_30_days=0.0,
        ),
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


def _loco_reference_areas(catalog: LOCoCatalog) -> tuple[tuple[str, float], ...]:
    grouped: dict[str, list[LOCoItem]] = defaultdict(list)
    for item in catalog.items:
        grouped[item.instance_name].append(item)
    result: list[tuple[str, float]] = []
    for instance, items in sorted(grouped.items()):
        ordered = tuple(sorted(items, key=lambda item: item.source_item_index))
        areas: list[float] = []
        for start in range(len(ordered) - 7):
            window = ordered[start : start + 8]
            references = tuple(_geometry_reference(item) for item in window)
            quantities = tuple(1 + (item.source_demand - 1) // 25 for item in window)
            areas.append(build_loco_fallback(references, quantities).area)
        result.append((f"loco:{instance}", float(statistics.median(areas))))
    return tuple(result)


def _reference_area_registry(catalog: LOCoCatalog) -> tuple[M11ReferenceAreas, ...]:
    return (
        M11ReferenceAreas(
            corpus_id="lectra-m3-m4",
            policy="fixed_lectra_median",
            by_material=tuple(
                (f"lectra-material-{index}", LECTRA_REFERENCE_AREA) for index in range(1, 6)
            ),
        ),
        M11ReferenceAreas(
            corpus_id="loco-2dics",
            policy="per_instance_median_verified_fallback",
            by_material=_loco_reference_areas(catalog),
        ),
    )


def _field_provenance() -> tuple[M11FieldProvenance, ...]:
    values = {
        "chronology": "generated",
        "customer_identity": "generated",
        "due_time": "generated",
        "economics": "assumed",
        "family_identity": "derived",
        "fallback_layout": "generated",
        "geometry_reference": "source_observed",
        "job_identity": "generated",
        "known_at": "generated",
        "lectra_candidate_references": "externally_anchored",
        "loco_candidate_references": "generated",
        "material_identity": "assumed",
        "priority": "generated",
        "quantity": "derived",
        "release_time": "generated",
        "source_demand": "source_observed",
        "stock_boundary": "generated",
    }
    return tuple(
        M11FieldProvenance(field_name=key, provenance=value)
        for key, value in sorted(values.items())
    )


def _read_json(path: Path, *, maximum_bytes: int) -> tuple[dict[str, object], bytes]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PackEvidenceError(f"cannot inspect M11 input: {path}") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > maximum_bytes
    ):
        raise PackEvidenceError(f"M11 input is not a bounded regular file: {path}")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PackEvidenceError(f"cannot decode M11 input: {path}") from error
    if not isinstance(payload, dict):
        raise PackEvidenceError(f"M11 input root must be an object: {path}")
    return payload, raw


def _parent_bindings(root: Path):
    m0, m0_raw = _read_json(root / _M0_PATH, maximum_bytes=4 * 1024 * 1024)
    m10, m10_raw = _read_json(root / _M10_PATH, maximum_bytes=4 * 1024 * 1024)
    try:
        return (
            build_m11_parent_binding(
                role="m0_contract",
                repository_path=_M0_PATH.as_posix(),
                schema_version=str(m0["schema_version"]),
                parent_semantic_id=str(m0["contract_id"]),
                parent_content_sha256=str(m0["content_sha256"]),
                raw_file_sha256=f"sha256:{hashlib.sha256(m0_raw).hexdigest()}",
            ),
            build_m11_parent_binding(
                role="m10_verdict",
                repository_path=_M10_PATH.as_posix(),
                schema_version=str(m10["schema_version"]),
                parent_semantic_id=str(m10["result_id"]),
                parent_content_sha256=str(m10["content_sha256"]),
                raw_file_sha256=f"sha256:{hashlib.sha256(m10_raw).hexdigest()}",
            ),
        )
    except (KeyError, ValidationError, ValueError) as error:
        raise PackEvidenceError("M11 parent artifacts do not satisfy the Task 1 binding") from error


def _source_bindings(source_manifest: M11SourceManifest):
    return (
        build_m11_source_binding(
            corpus_id="lectra-m3-m4",
            lineage_kind="lectra",
            source_uri=f"repository:{source_manifest.lectra.m3_input_repository_path}",
            upstream_sha256=f"sha256:{source_manifest.lectra.origin_root_sha256}",
            normalized_manifest_sha256=source_manifest.lectra.content_sha256,
            coordinate_units="unknown",
            geometry_provenance="source_observed",
        ),
        build_m11_source_binding(
            corpus_id="loco-2dics",
            lineage_kind="loco_2dics",
            source_uri=LOCO_ARCHIVE_URL,
            upstream_sha256=f"sha256:{source_manifest.loco.origin_root_sha256}",
            normalized_manifest_sha256=source_manifest.loco.catalog_manifest_content_sha256,
            coordinate_units="unknown",
            geometry_provenance="source_observed",
        ),
    )


def _build_contract(
    *,
    root: Path,
    source_manifest: M11SourceManifest,
    streams: tuple[M11Stream, ...],
    hard_nulls: tuple[M11HardNull, ...],
    audits: tuple[M11ExactAuditEpisode, ...],
    provenance: tuple[M11FieldProvenance, ...],
) -> M11ExperimentContract:
    source_bindings = _source_bindings(source_manifest)
    corpora: list[M11CorpusContract] = []
    for binding in source_bindings:
        corpus_id = binding.corpus_id
        corpora.append(
            M11CorpusContract(
                source=binding,
                calibration_stream_ids=tuple(
                    item.stream_id
                    for item in streams
                    if item.corpus_id == corpus_id
                    and item.stream_kind == "primary"
                    and item.partition == "calibration"
                ),
                confirmation_stream_ids=tuple(
                    item.stream_id
                    for item in streams
                    if item.corpus_id == corpus_id
                    and item.stream_kind == "primary"
                    and item.partition == "confirmation"
                ),
                shuffled_twin_stream_ids=tuple(
                    item.stream_id
                    for item in streams
                    if item.corpus_id == corpus_id and item.stream_kind == "shuffled_twin"
                ),
                hard_null_fixture_ids=tuple(
                    item.null_id for item in hard_nulls if item.corpus_id == corpus_id
                ),
                exact_audit_episode_ids=tuple(
                    item.audit_id for item in audits if item.corpus_id == corpus_id
                ),
                events_per_stream=24,
            )
        )
    return build_m11_contract(
        parents=_parent_bindings(root), corpora=tuple(corpora), field_provenance=provenance
    )


def generate_m11_pack(repository_root: Path) -> M11PackBundle:
    """Generate the complete frozen population without consulting any outcome field."""

    root = Path(repository_root)
    source_manifest = load_m11_source_manifest(root / _SOURCE_MANIFEST_PATH)
    if source_manifest.source_manifest_id != "yfm11sm-54426d56dcccc07b667da56f":
        raise PackEvidenceError("M11 generator requires the frozen Task 2 source manifest")
    loco = load_loco_catalog(root / _LOCO_CATALOG_PATH)
    if (
        loco.catalog_id != "yflc-14dd63c1d1b690236b8f6393"
        or source_manifest.loco.catalog_id != loco.catalog_id
        or source_manifest.loco.catalog_manifest_id != "yflcm-9f5bbd1ee54e18e5fa1cb86f"
    ):
        raise PackEvidenceError("M11 generator source catalog is not the frozen Task 2 bundle")
    lectra_payload, lectra_raw = _read_json(
        root / _LECTRA_CATALOG_PATH, maximum_bytes=16 * 1024 * 1024
    )
    if hashlib.sha256(lectra_raw).hexdigest() != LECTRA_CATALOG_RAW_SHA256:
        raise PackEvidenceError("Lectra source catalog raw hash differs from Task 2")
    m3_path = root / _M3_PATH
    if hashlib.sha256(m3_path.read_bytes()).hexdigest() != LECTRA_M3_INPUT_RAW_SHA256:
        raise PackEvidenceError("Lectra M3 source raw hash differs from Task 2")
    m3 = load_m3_input_pack(m3_path)

    lectra_partitions = derive_lectra_holdout_partitions(m3.expected_task_ids, lectra_payload)
    loco_partitions = _derive_loco_holdout_partitions(loco)
    partitions = lectra_partitions + loco_partitions
    lectra_cases = _lectra_payloads(m3, lectra_partitions)
    all_payloads: dict[str, M11Payload] = {item.payload_id: item for item in lectra_cases.values()}
    items_by_instance: dict[str, tuple[LOCoItem, ...]] = {}
    for instance in sorted({item.instance_name for item in loco.items}):
        items_by_instance[instance] = tuple(
            sorted(
                (item for item in loco.items if item.instance_name == instance),
                key=lambda item: item.source_item_index,
            )
        )

    primary: list[M11Stream] = []
    partition_lookup = {(item.corpus_id, item.partition): item for item in partitions}
    for corpus_id in ("lectra-m3-m4", "loco-2dics"):
        for partition_name, per_regime in (("calibration", 2), ("confirmation", 5)):
            partition = partition_lookup[(corpus_id, partition_name)]
            partition_ordinal = 0
            for regime in M11_REGIMES:
                for regime_ordinal in range(per_regime):
                    label = (
                        f"{corpus_id}|{partition_name}|{partition_ordinal:02d}|{regime}|"
                        f"{regime_ordinal:02d}"
                    )
                    if corpus_id == "lectra-m3-m4":
                        sequence = _lectra_sequence(
                            partition=partition,
                            regime=regime,
                            label=label,
                            payload_by_case=lectra_cases,
                        )
                    else:
                        sequence = _loco_sequence(
                            partition=partition,
                            regime=regime,
                            label=label,
                            items_by_instance=items_by_instance,
                            payload_catalog=all_payloads,
                        )
                    primary.append(
                        _primary_stream(
                            corpus_id=corpus_id,
                            partition=partition_name,
                            regime=regime,
                            partition_ordinal=partition_ordinal,
                            regime_ordinal=regime_ordinal,
                            payloads=sequence,
                            label=label,
                        )
                    )
                    partition_ordinal += 1
    confirmation = tuple(item for item in primary if item.partition == "confirmation")
    twins = tuple(
        _twin_stream(item, f"{item.corpus_id}|confirmation|{item.partition_ordinal:02d}|twin")
        for item in confirmation
    )
    streams = tuple(primary) + twins
    hard_nulls = tuple(
        item
        for corpus_id in ("lectra-m3-m4", "loco-2dics")
        for item in _hard_nulls(
            corpus_id,
            tuple(
                stream
                for stream in primary
                if stream.corpus_id == corpus_id and stream.partition == "confirmation"
            ),
        )
    )
    audits = tuple(
        item
        for corpus_id in ("lectra-m3-m4", "loco-2dics")
        for item in _exact_audits(
            corpus_id,
            tuple(
                stream
                for stream in primary
                if stream.corpus_id == corpus_id and stream.partition == "confirmation"
            ),
        )
    )
    provenance = _field_provenance()
    contract = _build_contract(
        root=root,
        source_manifest=source_manifest,
        streams=streams,
        hard_nulls=hard_nulls,
        audits=audits,
        provenance=provenance,
    )
    population_semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-population.v1",
        "contract_id": contract.contract_id,
        "contract_content_sha256": contract.content_sha256,
        "source_catalog_id": loco.catalog_id,
        "source_catalog_manifest_id": source_manifest.loco.catalog_manifest_id,
        "source_manifest_id": source_manifest.source_manifest_id,
        "source_manifest_sha256": source_manifest.content_sha256,
        "root_seed": M11_ROOT_SEED,
        "selection_inputs": list(M11_SELECTION_INPUTS),
        "prohibited_selection_fields": list(M11_PROHIBITED_SELECTION_FIELDS),
        "source_partitions": [item.model_dump(mode="json") for item in partitions],
        "economic_profiles": [item.model_dump(mode="json") for item in _economic_profiles()],
        "reference_areas": [
            item.model_dump(mode="json") for item in _reference_area_registry(loco)
        ],
        "field_provenance": [item.model_dump(mode="json") for item in provenance],
        "payloads": [
            item.model_dump(mode="json")
            for item in sorted(all_payloads.values(), key=lambda x: x.payload_id)
        ],
        "streams": [item.model_dump(mode="json") for item in streams],
        "hard_nulls": [item.model_dump(mode="json") for item in hard_nulls],
        "exact_audits": [item.model_dump(mode="json") for item in audits],
        "census": M11PopulationCensus().model_dump(mode="json"),
        "outcome_scoring_present": False,
    }
    identifier, content = _identity("yfm11pop-", population_semantic)
    population = _strict_json_model(
        M11Population,
        {
            "population_id": identifier,
            "content_sha256": content,
            **population_semantic,
        },
    )
    return _validate_pack_cross_binding(M11PackBundle(contract, population))


def _validate_pack_cross_binding(bundle: M11PackBundle) -> M11PackBundle:
    try:
        contract = M11ExperimentContract.model_validate(
            bundle.contract.model_dump(mode="python", round_trip=True, warnings=False), strict=True
        )
        population = M11Population.model_validate(
            bundle.population.model_dump(mode="python", round_trip=True, warnings=False),
            strict=True,
        )
    except (AttributeError, ValidationError, ValueError) as error:
        raise PackEvidenceError("M11 pack contains an invalid strict model") from error
    if (
        population.contract_id != contract.contract_id
        or population.contract_content_sha256 != contract.content_sha256
    ):
        raise PackEvidenceError("M11 population does not bind its Task 1 contract")
    if contract.field_provenance != population.field_provenance:
        raise PackEvidenceError("M11 contract and population provenance registries differ")
    streams = {item.stream_id: item for item in population.streams}
    nulls = {item.null_id for item in population.hard_nulls}
    audits = {item.audit_id for item in population.exact_audits}
    for twin in (item for item in population.streams if item.stream_kind == "shuffled_twin"):
        source = streams.get(twin.source_stream_id or "")
        if (
            source is None
            or source.stream_kind != "primary"
            or source.partition != "confirmation"
            or twin.corpus_id != source.corpus_id
            or twin.regime != source.regime
            or twin.partition_ordinal != source.partition_ordinal
            or twin.regime_ordinal != source.regime_ordinal
        ):
            raise PackEvidenceError("M11 twin does not bind its confirmation source stream")
        source_events = {item.event_id: item for item in source.events}
        lineage: list[str] = []
        for position, (source_event, twin_event) in enumerate(
            zip(source.events, twin.events, strict=True)
        ):
            payload_source = source_events.get(twin_event.payload_source_event_id or "")
            stable_source_fields = (
                source_event.position,
                source_event.known_at,
                source_event.released_at,
                source_event.due_at,
                source_event.due_hours,
                source_event.customer_id,
                source_event.job_id,
                source_event.priority,
            )
            stable_twin_fields = (
                twin_event.position,
                twin_event.known_at,
                twin_event.released_at,
                twin_event.due_at,
                twin_event.due_hours,
                twin_event.customer_id,
                twin_event.job_id,
                twin_event.priority,
            )
            if stable_twin_fields != stable_source_fields:
                raise PackEvidenceError(
                    "M11 twin stable chronology or priority differs from source"
                )
            if (
                payload_source is None
                or twin_event.payload_id != payload_source.payload_id
                or twin_event.family_id != payload_source.family_id
                or twin_event.payload_id == source_event.payload_id
                or twin_event.material_key != f"twin:{source.stream_id}:{position:02d}"
            ):
                raise PackEvidenceError("M11 twin payload lineage is invalid")
            lineage.append(payload_source.event_id)
        if Counter(lineage) != Counter(source_events.keys()):
            raise PackEvidenceError("M11 twin payload lineage is not an exact source permutation")
    for control in population.hard_nulls:
        source = streams[control.source_stream_id]
        if (
            source.stream_kind != "primary"
            or source.partition != "confirmation"
            or control.event_ids != tuple(item.event_id for item in source.events[:3])
        ):
            raise PackEvidenceError("M11 hard-null does not bind its prescribed source window")
    for control in population.exact_audits:
        source = streams[control.source_stream_id]
        expected_event_ids = tuple(
            source.events[position].event_id for position in control.event_positions
        )
        if (
            source.stream_kind != "primary"
            or source.partition != "confirmation"
            or control.event_ids != expected_event_ids
        ):
            raise PackEvidenceError("M11 exact audit does not bind its frozen stream positions")
    for corpus in contract.corpora:
        corpus_id = corpus.source.corpus_id
        expected_calibration = tuple(
            item.stream_id
            for item in population.streams
            if item.corpus_id == corpus_id
            and item.stream_kind == "primary"
            and item.partition == "calibration"
        )
        expected_confirmation = tuple(
            item.stream_id
            for item in population.streams
            if item.corpus_id == corpus_id
            and item.stream_kind == "primary"
            and item.partition == "confirmation"
        )
        expected_twins = tuple(
            item.stream_id
            for item in population.streams
            if item.corpus_id == corpus_id and item.stream_kind == "shuffled_twin"
        )
        expected_nulls = tuple(
            item.null_id for item in population.hard_nulls if item.corpus_id == corpus_id
        )
        expected_audits = tuple(
            item.audit_id for item in population.exact_audits if item.corpus_id == corpus_id
        )
        if (
            corpus.calibration_stream_ids != expected_calibration
            or corpus.confirmation_stream_ids != expected_confirmation
            or corpus.shuffled_twin_stream_ids != expected_twins
            or corpus.hard_null_fixture_ids != expected_nulls
            or corpus.exact_audit_episode_ids != expected_audits
            or not set(corpus.hard_null_fixture_ids).issubset(nulls)
            or not set(corpus.exact_audit_episode_ids).issubset(audits)
            or any(
                value not in streams
                for value in (
                    *corpus.calibration_stream_ids,
                    *corpus.confirmation_stream_ids,
                    *corpus.shuffled_twin_stream_ids,
                )
            )
        ):
            raise PackEvidenceError("M11 contract census does not cross-bind its population")
    return M11PackBundle(contract, population)


def canonical_pack_artifact_bytes(bundle: M11PackBundle) -> M11PackArtifactBytes:
    """Return the only accepted committed encodings for the Task 3 artifacts."""

    validated = _validate_pack_cross_binding(bundle)
    return M11PackArtifactBytes(
        contract=canonical_pretty_json_bytes(validated.contract),
        population=canonical_pretty_json_bytes(validated.population),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PackEvidenceError(f"duplicate M11 artifact key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise PackEvidenceError(f"nonfinite M11 artifact constant: {value}")


def _load_pack_model(path: Path, model):
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_PACK_ARTIFACT_BYTES
        ):
            raise PackEvidenceError("M11 artifact must be a bounded regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            raw = os.read(descriptor, _MAX_PACK_ARTIFACT_BYTES + 1)
        finally:
            os.close(descriptor)
        json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite)
        value = model.model_validate_json(raw, strict=True)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as error:
        raise PackEvidenceError(f"M11 artifact is not canonical: {path}") from error
    if raw != canonical_pretty_json_bytes(value):
        raise PackEvidenceError(f"M11 artifact is not canonical: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def _bounded_file_sha256(path: Path, *, maximum_bytes: int) -> str:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PackEvidenceError(f"cannot inspect pinned M11 source: {path}") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > maximum_bytes
    ):
        raise PackEvidenceError(f"pinned M11 source is not a bounded regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PackEvidenceError(f"cannot open pinned M11 source: {path}") from error
    digest = hashlib.sha256()
    read_count = 0
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            read_count += len(chunk)
            if read_count > maximum_bytes:
                raise PackEvidenceError(f"pinned M11 source exceeds its bound: {path}")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _validate_population_against_pinned_sources(
    *,
    root: Path,
    population: M11Population,
    source_manifest: M11SourceManifest,
) -> None:
    """Recompute every Task 3 source leaf from the exact Task 2 parents."""

    lectra_catalog_path = root / Path(source_manifest.lectra.lectra_catalog_repository_path)
    lectra_catalog, lectra_raw = _read_json(lectra_catalog_path, maximum_bytes=16 * 1024 * 1024)
    if hashlib.sha256(lectra_raw).hexdigest() != source_manifest.lectra.lectra_catalog_raw_sha256:
        raise PackEvidenceError("M11 Lectra catalog differs from the pinned Task 2 source")
    m3_path = root / Path(source_manifest.lectra.m3_input_repository_path)
    if (
        _bounded_file_sha256(m3_path, maximum_bytes=128 * 1024 * 1024)
        != source_manifest.lectra.m3_input_raw_sha256
    ):
        raise PackEvidenceError("M11 M3 input differs from the pinned Task 2 source")
    try:
        m3 = load_m3_input_pack(m3_path)
        loco = load_loco_catalog(root / _LOCO_CATALOG_PATH)
    except (ValidationError, ValueError) as error:
        raise PackEvidenceError("M11 pinned source leaves failed strict loading") from error
    if (
        loco.catalog_id != population.source_catalog_id
        or loco.catalog_id != source_manifest.loco.catalog_id
    ):
        raise PackEvidenceError("M11 LOCo catalog differs from the pinned Task 2 source")

    lectra_partitions = derive_lectra_holdout_partitions(m3.expected_task_ids, lectra_catalog)
    loco_partitions = _derive_loco_holdout_partitions(loco)
    expected_partitions = lectra_partitions + loco_partitions
    if population.source_partitions != expected_partitions:
        raise PackEvidenceError(
            "M11 source partitions do not match freshly derived Task 2 families"
        )

    expected_lectra = _lectra_payloads(m3, lectra_partitions)
    actual_lectra = {
        item.source_case_id: item for item in population.payloads if item.source_kind == "lectra"
    }
    if set(actual_lectra) != set(expected_lectra) or any(
        actual_lectra[case_id] != expected for case_id, expected in expected_lectra.items()
    ):
        raise PackEvidenceError("M11 Lectra tasks or ordered candidate references differ from M3")

    loco_items = {item.item_id: item for item in loco.items}
    loco_family_by_item = {
        case_id: family.family_id
        for partition in loco_partitions
        for family in partition.families
        for case_id in family.source_case_ids
    }
    for payload in (item for item in population.payloads if item.source_kind == "loco_2dics"):
        try:
            items = tuple(
                loco_items[reference.reference_id] for reference in payload.geometry_references
            )
            family_ids = {loco_family_by_item[item.item_id] for item in items}
        except KeyError as error:
            raise PackEvidenceError(
                "M11 LOCo payload references an unregistered source row"
            ) from error
        if len(family_ids) != 1:
            raise PackEvidenceError("M11 LOCo payload crosses freshly derived holdout families")
        expected_payload = _loco_payload(items, next(iter(family_ids)))
        if payload != expected_payload:
            raise PackEvidenceError(
                "M11 LOCo source references or eight-width fallback differ from pinned geometry"
            )

    payloads = {item.payload_id: item for item in population.payloads}
    allowed_families = {
        (item.corpus_id, item.partition): set(item.family_ids)
        for item in population.source_partitions
    }
    expected_source_kind = {"lectra-m3-m4": "lectra", "loco-2dics": "loco_2dics"}
    for stream in population.streams:
        admitted = allowed_families[(stream.corpus_id, stream.partition)]
        for event in stream.events:
            payload = payloads[event.payload_id]
            if (
                payload.source_kind != expected_source_kind[stream.corpus_id]
                or payload.family_id not in admitted
            ):
                raise PackEvidenceError(
                    "M11 stream payload is outside its corpus holdout partition"
                )

    if population.reference_areas != _reference_area_registry(loco):
        raise PackEvidenceError("M11 reference areas differ from pinned deterministic derivation")


def _load_m11_pack_bundle(
    *,
    repository_root: Path,
    contract_path: Path,
    population_path: Path,
    source_manifest_path: Path,
    authenticate_official: bool,
) -> M11PackBundle:
    root = Path(repository_root)
    contract, contract_raw_sha256 = _load_pack_model(Path(contract_path), M11ExperimentContract)
    population, population_raw_sha256 = _load_pack_model(Path(population_path), M11Population)
    source_manifest = load_m11_source_manifest(Path(source_manifest_path))
    bundle = _validate_pack_cross_binding(M11PackBundle(contract, population))
    if (
        source_manifest.source_manifest_id != population.source_manifest_id
        or source_manifest.content_sha256 != population.source_manifest_sha256
        or source_manifest.loco.catalog_id != population.source_catalog_id
        or source_manifest.loco.catalog_manifest_id != population.source_catalog_manifest_id
    ):
        raise PackEvidenceError("M11 population does not bind the supplied Task 2 source bundle")
    if tuple(corpus.source for corpus in bundle.contract.corpora) != _source_bindings(
        source_manifest
    ):
        raise PackEvidenceError("M11 contract does not bind the supplied Task 2 source manifest")
    _validate_population_against_pinned_sources(
        root=root,
        population=bundle.population,
        source_manifest=source_manifest,
    )
    parents = _parent_bindings(root)
    if bundle.contract.parents != parents:
        raise PackEvidenceError("M11 contract does not bind the supplied Task 1 parent files")
    if authenticate_official and (
        contract.contract_id != _OFFICIAL_CONTRACT_ID
        or contract.content_sha256 != _OFFICIAL_CONTRACT_CONTENT_SHA256
        or contract_raw_sha256 != _OFFICIAL_CONTRACT_RAW_SHA256
        or population.population_id != _OFFICIAL_POPULATION_ID
        or population.content_sha256 != _OFFICIAL_POPULATION_CONTENT_SHA256
        or population_raw_sha256 != _OFFICIAL_POPULATION_RAW_SHA256
    ):
        raise PackEvidenceError("M11 artifacts differ from the official canonical identity")
    return bundle


def _load_unpublished_m11_pack_bundle(
    *, repository_root: Path, contract_path: Path, population_path: Path, source_manifest_path: Path
) -> M11PackBundle:
    """Strict-load a generated candidate before publishing its official identity."""

    return _load_m11_pack_bundle(
        repository_root=repository_root,
        contract_path=contract_path,
        population_path=population_path,
        source_manifest_path=source_manifest_path,
        authenticate_official=False,
    )


def load_m11_pack_bundle(
    *, repository_root: Path, contract_path: Path, population_path: Path, source_manifest_path: Path
) -> M11PackBundle:
    """Strict-load the official seed-derived Task 3 pack and both frozen parents."""

    return _load_m11_pack_bundle(
        repository_root=repository_root,
        contract_path=contract_path,
        population_path=population_path,
        source_manifest_path=source_manifest_path,
        authenticate_official=True,
    )


__all__ = [
    "LECTRA_REFERENCE_AREA",
    "LOCO_TARGET_WIDTH_MULTIPLIERS",
    "M11_AUDIT_ARMS",
    "M11_AUDIT_POSITIONS",
    "M11_PROHIBITED_SELECTION_FIELDS",
    "M11_REGIMES",
    "M11_ROOT_SEED",
    "M11CandidateReference",
    "M11EconomicProfile",
    "M11Event",
    "M11ExactAuditEpisode",
    "M11FallbackPlacement",
    "M11FallbackStock",
    "M11GeometryReference",
    "M11HardNull",
    "M11HoldoutFamily",
    "M11PackArtifactBytes",
    "M11PackBundle",
    "M11Payload",
    "M11Population",
    "M11PopulationCensus",
    "M11ReferenceAreas",
    "M11SourcePartition",
    "M11Stream",
    "PackEvidenceError",
    "build_loco_fallback",
    "canonical_pack_artifact_bytes",
    "derive_lectra_holdout_partitions",
    "generate_m11_pack",
    "load_m11_pack_bundle",
    "m11_subseed",
    "visible_event_positions",
]
