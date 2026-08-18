"""Frozen contracts for generated order books and their evidence boundary."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

LECTRA_SLICE_DATASET_ID = "lectra-7030786-v1.1"
LECTRA_SLICE_SHA256 = "d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8"
LECTRA_SLICE_CONTENT_SHA256 = f"sha256:{LECTRA_SLICE_SHA256}"


class FrozenContract(BaseModel):
    """Strict immutable base for every persisted order-book contract."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class GenerationRegime(StrEnum):
    """The deliberately small set of MVP generator regimes."""

    NO_SIGNAL = "no_signal"
    EXACT_RECURRENCE = "exact_recurrence"
    HIGH_MIX = "high_mix"


class FieldFamily(StrEnum):
    """Semantic field families whose provenance is reported independently."""

    GEOMETRY = "geometry"
    COMPOSITION = "composition"
    CHRONOLOGY = "chronology"
    MATERIAL = "material"
    ECONOMICS = "economics"
    REGIME_LABEL = "regime_label"


class ProvenanceKind(StrEnum):
    """Whether values are observed, generated, or assumption-dependent."""

    SOURCE_OBSERVED = "source_observed"
    DERIVED = "derived"
    GENERATED = "generated"
    ASSUMED = "assumed"


class FieldFamilyProvenance(FrozenContract):
    """One explicit claim about the origin of a semantic field family."""

    family: FieldFamily
    kind: ProvenanceKind
    explanation: str = Field(min_length=1)


class GeneratorIdentity(FrozenContract):
    """Identity of the deterministic generator implementation."""

    name: Literal["yieldforge.hybrid-order-book"] = "yieldforge.hybrid-order-book"
    version: Literal["1.0.0"] = "1.0.0"
    algorithm: Literal["python-mt19937-canonical-v1"] = "python-mt19937-canonical-v1"


class SourceSliceIdentity(FrozenContract):
    """Content-addressed identity of the normalized source slice."""

    dataset_id: Literal["lectra-7030786-v1.1"] = "lectra-7030786-v1.1"
    repository_path: Literal["datasets/fixtures/lectra-representative-slice.json"] = (
        "datasets/fixtures/lectra-representative-slice.json"
    )
    content_sha256: Literal[
        "sha256:d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8"
    ]
    conversion_ruleset_version: Literal["lectra-slice-rules.v1"] = "lectra-slice-rules.v1"
    doi: Literal["10.5281/zenodo.7030786"] = "10.5281/zenodo.7030786"


class SourceTaskReference(FrozenContract):
    """Lossless references to one task and its observed part composition."""

    dataset_id: str = Field(min_length=1)
    tasks_index: StrictInt = Field(ge=0)
    task_source_row_index: StrictInt = Field(ge=0)
    part_ids: tuple[StrictInt, ...] = Field(min_length=1)
    part_source_row_indices: tuple[StrictInt, ...] = Field(min_length=1)
    shape_hashes: tuple[StrictInt, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_parallel_composition_references(self) -> Self:
        counts = {len(self.part_ids), len(self.part_source_row_indices), len(self.shape_hashes)}
        if len(counts) != 1:
            raise ValueError("part IDs, source rows, and shape hashes must have equal lengths")
        if len(self.part_ids) != len(set(self.part_ids)):
            raise ValueError("part IDs must be unique within a source task")
        if len(self.part_source_row_indices) != len(set(self.part_source_row_indices)):
            raise ValueError("part source rows must be unique within a source task")
        return self


class MaterialAssignment(FrozenContract):
    """An explicitly synthetic material label for scenario experimentation."""

    material_code: str = Field(min_length=1)
    thickness_index: StrictInt = Field(gt=0)


class EconomicFields(FrozenContract):
    """Synthetic economic signals that never masquerade as source observations."""

    priority_score: StrictFloat = Field(ge=0, le=1)
    value_index: StrictFloat = Field(ge=0)
    lead_time_minutes: StrictInt = Field(gt=0)
    value_unit: Literal["synthetic_index"] = "synthetic_index"


class OrderEvent(FrozenContract):
    """One chronologically generated arrival referencing an observed task."""

    sequence: StrictInt = Field(ge=0)
    event_id: str = Field(pattern=r"^evt-[0-9a-f]{20}$")
    occurred_at: datetime
    source_task: SourceTaskReference
    material: MaterialAssignment
    economics: EconomicFields

    @field_validator("occurred_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class RegimeThresholds(FrozenContract):
    """Declared acceptance gates evaluated against realized diagnostics."""

    min_unique_task_refs: StrictInt | None = Field(default=None, ge=1)
    min_task_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_task_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_shape_recurrence: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_shape_recurrence: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_total_part_references: StrictInt | None = Field(default=None, gt=0)
    max_total_part_references: StrictInt | None = Field(default=None, gt=0)
    min_mean_task_parts: StrictFloat | None = Field(default=None, gt=0)
    max_mean_task_parts: StrictFloat | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_coherent_thresholds(self) -> Self:
        declared = (
            self.min_unique_task_refs,
            self.min_task_concentration,
            self.max_task_concentration,
            self.min_shape_recurrence,
            self.max_shape_recurrence,
            self.min_total_part_references,
            self.max_total_part_references,
            self.min_mean_task_parts,
            self.max_mean_task_parts,
        )
        if all(value is None for value in declared):
            raise ValueError("at least one threshold must be declared")
        if (
            self.min_task_concentration is not None
            and self.max_task_concentration is not None
            and self.min_task_concentration > self.max_task_concentration
        ):
            raise ValueError("min_task_concentration cannot exceed max_task_concentration")
        if (
            self.min_shape_recurrence is not None
            and self.max_shape_recurrence is not None
            and self.min_shape_recurrence > self.max_shape_recurrence
        ):
            raise ValueError("min_shape_recurrence cannot exceed max_shape_recurrence")
        if (
            self.min_total_part_references is not None
            and self.max_total_part_references is not None
            and self.min_total_part_references > self.max_total_part_references
        ):
            raise ValueError("min_total_part_references cannot exceed max_total_part_references")
        if (
            self.min_mean_task_parts is not None
            and self.max_mean_task_parts is not None
            and self.min_mean_task_parts > self.max_mean_task_parts
        ):
            raise ValueError("min_mean_task_parts cannot exceed max_mean_task_parts")
        return self


class GenerationRequest(FrozenContract):
    """Complete deterministic input to an order-book generation run."""

    regime: GenerationRegime
    seed: StrictInt
    event_count: StrictInt = Field(ge=2, le=10_000)
    starts_at: datetime
    interval_minutes: StrictInt = Field(gt=0)
    source_slice_sha256: Literal["d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8"]
    thresholds: RegimeThresholds | None = None

    @field_validator("starts_at")
    @classmethod
    def require_aware_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at must be timezone-aware")
        return value


class ChronologicalLoad(FrozenContract):
    """Realized load at one generated arrival time."""

    sequence: StrictInt = Field(ge=0)
    occurred_at: datetime
    tasks_index: StrictInt = Field(ge=0)
    part_count: StrictInt = Field(gt=0)
    unique_shape_count: StrictInt = Field(gt=0)


class TaskSizeSummary(FrozenContract):
    """Summary of source task composition sizes across generated events."""

    event_count: StrictInt = Field(gt=0)
    total_part_references: StrictInt = Field(gt=0)
    minimum_parts: StrictInt = Field(gt=0)
    maximum_parts: StrictInt = Field(gt=0)
    mean_parts: StrictFloat = Field(gt=0)


class OrderBookDiagnostics(FrozenContract):
    """Measured properties of the realized, not merely requested, order book."""

    unique_task_ref_count: StrictInt = Field(gt=0)
    max_task_concentration: StrictFloat = Field(gt=0, le=1)
    shape_recurrence: StrictFloat = Field(ge=0, le=1)
    chronological_load: tuple[ChronologicalLoad, ...] = Field(min_length=1)
    task_sizes: TaskSizeSummary
    evaluated_thresholds: RegimeThresholds


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON-compatible value with the repository canonical encoding."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def manifest_content_sha256(manifest: OrderBookManifest) -> str:
    """Hash all semantic manifest content while excluding its derived identity fields."""

    payload = manifest.model_dump(
        mode="json",
        exclude={"content_sha256", "order_book_id"},
    )
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


_REQUIRED_REGIME_THRESHOLDS = {
    GenerationRegime.NO_SIGNAL: RegimeThresholds(
        min_unique_task_refs=2,
        max_task_concentration=0.50,
        max_shape_recurrence=0.0,
    ),
    GenerationRegime.EXACT_RECURRENCE: RegimeThresholds(
        min_task_concentration=1.0,
        min_shape_recurrence=1.0,
    ),
    GenerationRegime.HIGH_MIX: RegimeThresholds(
        min_unique_task_refs=2,
        max_task_concentration=0.60,
        max_shape_recurrence=0.25,
    ),
}


def _maximum_optional(left: int | float | None, right: int | float | None) -> int | float | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def _minimum_optional(left: int | float | None, right: int | float | None) -> int | float | None:
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def resolve_regime_thresholds(request: GenerationRequest) -> RegimeThresholds:
    """Combine caller bounds with non-weakening semantic bounds for its regime."""

    required = _REQUIRED_REGIME_THRESHOLDS[request.regime]
    declared = request.thresholds
    if declared is None:
        return required
    return RegimeThresholds(
        min_unique_task_refs=_maximum_optional(
            required.min_unique_task_refs,
            declared.min_unique_task_refs,
        ),
        min_task_concentration=_maximum_optional(
            required.min_task_concentration,
            declared.min_task_concentration,
        ),
        max_task_concentration=_minimum_optional(
            required.max_task_concentration,
            declared.max_task_concentration,
        ),
        min_shape_recurrence=_maximum_optional(
            required.min_shape_recurrence,
            declared.min_shape_recurrence,
        ),
        max_shape_recurrence=_minimum_optional(
            required.max_shape_recurrence,
            declared.max_shape_recurrence,
        ),
        min_total_part_references=declared.min_total_part_references,
        max_total_part_references=declared.max_total_part_references,
        min_mean_task_parts=declared.min_mean_task_parts,
        max_mean_task_parts=declared.max_mean_task_parts,
    )


def expected_event_id(
    request: GenerationRequest,
    sequence: int,
    reference: SourceTaskReference,
    material: MaterialAssignment,
    economics: EconomicFields,
) -> str:
    """Derive an event identifier from all seeded event choices."""

    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "seed": request.seed,
                "sequence": sequence,
                "tasks_index": reference.tasks_index,
                "material": material.model_dump(mode="json"),
                "economics": economics.model_dump(mode="json"),
            }
        )
    ).hexdigest()
    return f"evt-{digest[:20]}"


def calculate_diagnostics(
    events: tuple[OrderEvent, ...],
    thresholds: RegimeThresholds,
) -> OrderBookDiagnostics:
    """Rederive every persisted diagnostic solely from realized events."""

    task_counts = Counter(event.source_task.tasks_index for event in events)
    previously_seen_shapes: set[int] = set(events[0].source_task.shape_hashes)
    recurrence_population = 0
    recurrent_shape_occurrences = 0
    for event in events[1:]:
        recurrence_population += len(event.source_task.shape_hashes)
        recurrent_shape_occurrences += sum(
            shape_hash in previously_seen_shapes for shape_hash in event.source_task.shape_hashes
        )
        previously_seen_shapes.update(event.source_task.shape_hashes)
    shape_recurrence = recurrent_shape_occurrences / recurrence_population
    sizes = [len(event.source_task.part_ids) for event in events]
    chronological_load = tuple(
        ChronologicalLoad(
            sequence=event.sequence,
            occurred_at=event.occurred_at,
            tasks_index=event.source_task.tasks_index,
            part_count=len(event.source_task.part_ids),
            unique_shape_count=len(set(event.source_task.shape_hashes)),
        )
        for event in events
    )
    return OrderBookDiagnostics(
        unique_task_ref_count=len(task_counts),
        max_task_concentration=float(max(task_counts.values()) / len(events)),
        shape_recurrence=float(shape_recurrence),
        chronological_load=chronological_load,
        task_sizes=TaskSizeSummary(
            event_count=len(events),
            total_part_references=sum(sizes),
            minimum_parts=min(sizes),
            maximum_parts=max(sizes),
            mean_parts=float(sum(sizes) / len(sizes)),
        ),
        evaluated_thresholds=thresholds,
    )


def threshold_failures(diagnostics: OrderBookDiagnostics) -> tuple[str, ...]:
    """Return stable diagnostics for every missed realized construction gate."""

    thresholds = diagnostics.evaluated_thresholds
    failures: list[str] = []
    checks = (
        (
            "min_unique_task_refs",
            thresholds.min_unique_task_refs,
            diagnostics.unique_task_ref_count,
            lambda actual, bound: actual >= bound,
        ),
        (
            "min_task_concentration",
            thresholds.min_task_concentration,
            diagnostics.max_task_concentration,
            lambda actual, bound: actual >= bound,
        ),
        (
            "max_task_concentration",
            thresholds.max_task_concentration,
            diagnostics.max_task_concentration,
            lambda actual, bound: actual <= bound,
        ),
        (
            "min_shape_recurrence",
            thresholds.min_shape_recurrence,
            diagnostics.shape_recurrence,
            lambda actual, bound: actual >= bound,
        ),
        (
            "max_shape_recurrence",
            thresholds.max_shape_recurrence,
            diagnostics.shape_recurrence,
            lambda actual, bound: actual <= bound,
        ),
        (
            "min_total_part_references",
            thresholds.min_total_part_references,
            diagnostics.task_sizes.total_part_references,
            lambda actual, bound: actual >= bound,
        ),
        (
            "max_total_part_references",
            thresholds.max_total_part_references,
            diagnostics.task_sizes.total_part_references,
            lambda actual, bound: actual <= bound,
        ),
        (
            "min_mean_task_parts",
            thresholds.min_mean_task_parts,
            diagnostics.task_sizes.mean_parts,
            lambda actual, bound: actual >= bound,
        ),
        (
            "max_mean_task_parts",
            thresholds.max_mean_task_parts,
            diagnostics.task_sizes.mean_parts,
            lambda actual, bound: actual <= bound,
        ),
    )
    for name, bound, actual, accepts in checks:
        if bound is not None and not accepts(actual, bound):
            failures.append(f"{name}={bound} (realized {actual})")
    return tuple(failures)


class OrderBookManifest(FrozenContract):
    """Content-addressed realized order book with provenance and diagnostics."""

    schema_version: Literal["yieldforge.order-book.v1"] = "yieldforge.order-book.v1"
    order_book_id: str = Field(pattern=r"^yfob-[0-9a-f]{24}$")
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generator: GeneratorIdentity
    request: GenerationRequest
    source_slice: SourceSliceIdentity
    field_provenance: tuple[FieldFamilyProvenance, ...] = Field(min_length=6, max_length=6)
    events: tuple[OrderEvent, ...] = Field(min_length=2)
    diagnostics: OrderBookDiagnostics

    @model_validator(mode="after")
    def require_consistent_content_addressed_manifest(self) -> Self:
        provenance = {record.family: record.kind for record in self.field_provenance}
        expected_provenance = {
            FieldFamily.GEOMETRY: ProvenanceKind.SOURCE_OBSERVED,
            FieldFamily.COMPOSITION: ProvenanceKind.SOURCE_OBSERVED,
            FieldFamily.CHRONOLOGY: ProvenanceKind.GENERATED,
            FieldFamily.MATERIAL: ProvenanceKind.ASSUMED,
            FieldFamily.ECONOMICS: ProvenanceKind.GENERATED,
            FieldFamily.REGIME_LABEL: ProvenanceKind.GENERATED,
        }
        if provenance != expected_provenance or len(self.field_provenance) != len(provenance):
            raise ValueError("field provenance must cover each family exactly once")
        if len(self.events) != self.request.event_count:
            raise ValueError("event count does not match generation request")
        if tuple(event.sequence for event in self.events) != tuple(range(len(self.events))):
            raise ValueError("event sequences must be contiguous from zero")
        if self.source_slice.content_sha256 != f"sha256:{self.request.source_slice_sha256}":
            raise ValueError("request and source slice content identities differ")
        if any(
            event.occurred_at
            != self.request.starts_at
            + timedelta(minutes=self.request.interval_minutes * event.sequence)
            for event in self.events
        ):
            raise ValueError("event chronology does not follow the requested interval")
        if any(
            event.source_task.dataset_id != self.source_slice.dataset_id for event in self.events
        ):
            raise ValueError("event source-task dataset identity is inconsistent")
        if any(
            event.event_id
            != expected_event_id(
                self.request,
                event.sequence,
                event.source_task,
                event.material,
                event.economics,
            )
            for event in self.events
        ):
            raise ValueError("event ID is inconsistent with deterministic event content")
        evaluated_thresholds = resolve_regime_thresholds(self.request)
        expected_diagnostics = calculate_diagnostics(self.events, evaluated_thresholds)
        if self.diagnostics != expected_diagnostics:
            raise ValueError("diagnostics are inconsistent with realized events")
        failures = threshold_failures(expected_diagnostics)
        if failures:
            raise ValueError("declared regime thresholds missed: " + "; ".join(failures))
        expected_hash = manifest_content_sha256(self)
        if self.content_sha256 != expected_hash:
            raise ValueError("content hash mismatch")
        if self.order_book_id != f"yfob-{expected_hash[7:31]}":
            raise ValueError("order book ID does not match content hash")
        return self


class BaselineOrderBookView(FrozenContract):
    """As-of-safe view that deliberately omits the generator regime."""

    schema_version: Literal["yieldforge.order-book-baseline.v1"] = (
        "yieldforge.order-book-baseline.v1"
    )
    as_of: datetime
    source_slice: SourceSliceIdentity
    field_provenance: tuple[FieldFamilyProvenance, ...]
    events: tuple[OrderEvent, ...]

    @field_validator("as_of")
    @classmethod
    def require_aware_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return value

    @model_validator(mode="after")
    def prohibit_future_events_and_regime_labels(self) -> Self:
        if any(event.occurred_at > self.as_of for event in self.events):
            raise ValueError("baseline view cannot contain future events")
        families = tuple(record.family for record in self.field_provenance)
        if FieldFamily.REGIME_LABEL in families:
            raise ValueError("baseline view cannot contain generator regime provenance")
        if len(families) != len(set(families)):
            raise ValueError("baseline field provenance families must be unique")
        return self


class OracleOrderBookView(FrozenContract):
    """Analysis-only view that may inspect the full realized future book."""

    schema_version: Literal["yieldforge.order-book-oracle.v1"] = "yieldforge.order-book-oracle.v1"
    order_book_id: str = Field(pattern=r"^yfob-[0-9a-f]{24}$")
    regime: GenerationRegime
    events: tuple[OrderEvent, ...]
    diagnostics: OrderBookDiagnostics
