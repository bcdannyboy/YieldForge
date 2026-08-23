"""Deterministic realization of the six measured M6 temporal regimes."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from yieldforge.order_books.domain import EconomicFields
from yieldforge.reuse.contracts import MaterialIdentity, MaterialProvenance
from yieldforge.temporal_benchmark.catalog import CatalogSnapshot, StockSignature
from yieldforge.temporal_benchmark.contracts import (
    SOURCE_CATALOG_SHA256,
    TemporalBenchmarkContract,
    TemporalContractModel,
    TemporalGeneratorIdentity,
    TemporalPartition,
    TemporalPopulationCell,
    TemporalRegime,
    TemporalTiming,
)


class GenerationError(ValueError):
    """One registered M6 stream could not be realized or validated."""


class SourceTaskEventReference(TemporalContractModel):
    """Lossless compact reference to one source task and stock boundary."""

    dataset_id: Literal["lectra-7030786-v1.1"] = "lectra-7030786-v1.1"
    tasks_index: StrictInt = Field(ge=0)
    source_row_index: StrictInt = Field(ge=0)
    part_ids: tuple[StrictInt, ...] = Field(min_length=1)
    part_source_row_indices: tuple[StrictInt, ...] = Field(min_length=1)
    shape_hashes: tuple[StrictInt, ...] = Field(min_length=1)
    sheet_type: StrictInt
    sheet_length: StrictFloat = Field(gt=0)
    sheet_width: StrictFloat = Field(gt=0)

    @model_validator(mode="after")
    def require_complete_parallel_source_composition(self) -> Self:
        reference_lengths = {
            len(self.part_ids),
            len(self.part_source_row_indices),
            len(self.shape_hashes),
        }
        if len(reference_lengths) != 1:
            raise ValueError("source task part references must have equal lengths")
        if len(self.part_ids) != len(set(self.part_ids)):
            raise ValueError("source part IDs must be unique within one task")
        if len(self.part_source_row_indices) != len(set(self.part_source_row_indices)):
            raise ValueError("source part rows must be unique within one task")
        return self

    @property
    def stock_signature(self) -> StockSignature:
        return (self.sheet_type, self.sheet_length, self.sheet_width)

    @property
    def family_signature(self) -> tuple[int, ...]:
        return tuple(sorted(set(self.shape_hashes)))


class TemporalEvent(TemporalContractModel):
    sequence: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(pattern=r"^yfte-[0-9a-f]{20}$")
    occurred_at: datetime
    source_task: SourceTaskEventReference
    material: MaterialIdentity
    economics: EconomicFields
    chronology_provenance: Literal["generated"] = "generated"
    material_provenance: Literal["assumed"] = "assumed"
    geometry_provenance: Literal["source_observed"] = "source_observed"

    @field_validator("occurred_at")
    @classmethod
    def canonicalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_assumed_material(self) -> Self:
        if self.material.provenance is not MaterialProvenance.ASSUMED:
            raise ValueError("M6 event material must be explicitly assumed")
        return self


class RegimeThresholds(TemporalContractModel):
    min_unique_task_count: StrictInt | None = Field(default=None, ge=1)
    max_unique_task_count: StrictInt | None = Field(default=None, ge=1)
    min_task_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_task_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_shape_recurrence: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_shape_recurrence: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_family_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_family_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_material_recurrence: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_material_recurrence: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_compatible_batch_size: StrictInt | None = Field(default=None, ge=1)
    max_compatible_batch_size: StrictInt | None = Field(default=None, ge=1)
    min_bundled_event_fraction: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_bundled_event_fraction: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_first_half_task_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    min_second_half_unique_task_count: StrictInt | None = Field(default=None, ge=1)
    max_second_half_task_concentration: StrictFloat | None = Field(default=None, ge=0, le=1)
    max_segment_task_overlap: StrictFloat | None = Field(default=None, ge=0, le=1)


class TemporalStreamDiagnostics(TemporalContractModel):
    unique_task_count: StrictInt = Field(gt=0)
    max_task_concentration: StrictFloat = Field(gt=0, le=1)
    shape_recurrence: StrictFloat = Field(ge=0, le=1)
    family_concentration: StrictFloat = Field(gt=0, le=1)
    material_recurrence: StrictFloat = Field(ge=0, le=1)
    max_compatible_batch_size: StrictInt = Field(gt=0)
    compatibly_bundled_event_fraction: StrictFloat = Field(ge=0, le=1)
    first_half_task_concentration: StrictFloat = Field(gt=0, le=1)
    second_half_unique_task_count: StrictInt = Field(gt=0)
    second_half_task_concentration: StrictFloat = Field(gt=0, le=1)
    segment_task_overlap: StrictFloat = Field(ge=0, le=1)
    total_part_references: StrictInt = Field(gt=0)
    thresholds: RegimeThresholds
    threshold_failures: tuple[StrictStr, ...]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _semantic_sha256(value: object, *, excluded: set[str] | None = None) -> str:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")  # type: ignore[union-attr]
    else:
        payload = dict(value)  # type: ignore[arg-type]
    for field in excluded or set():
        payload.pop(field, None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _material_key(material: MaterialIdentity) -> tuple[str, str, str, str, str]:
    return (
        material.material_code,
        material.grade,
        material.thickness,
        material.surface,
        material.grain,
    )


def expected_event_id(
    cell_id: str,
    sequence: int,
    occurred_at: datetime,
    source_task: SourceTaskEventReference,
    material: MaterialIdentity,
    economics: EconomicFields,
) -> str:
    digest = hashlib.sha256(
        _canonical_bytes(
            {
                "cell_id": cell_id,
                "economics": economics.model_dump(mode="json"),
                "material": material.model_dump(mode="json"),
                "occurred_at": occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "sequence": sequence,
                "source_task": source_task.model_dump(mode="json"),
            }
        )
    ).hexdigest()
    return f"yfte-{digest[:20]}"


_THRESHOLDS = {
    TemporalRegime.NO_SIGNAL: RegimeThresholds(
        min_unique_task_count=24,
        max_unique_task_count=24,
        max_task_concentration=float(1 / 24),
        max_shape_recurrence=0.0,
        max_material_recurrence=0.0,
        max_compatible_batch_size=1,
        max_bundled_event_fraction=0.0,
    ),
    TemporalRegime.EXACT_RECURRENCE: RegimeThresholds(
        min_unique_task_count=1,
        max_unique_task_count=1,
        min_task_concentration=1.0,
        min_shape_recurrence=1.0,
        min_material_recurrence=1.0,
        max_compatible_batch_size=1,
    ),
    TemporalRegime.FAMILY_SIMILARITY: RegimeThresholds(
        min_unique_task_count=4,
        max_task_concentration=0.25,
        min_shape_recurrence=1.0,
        min_family_concentration=1.0,
        min_material_recurrence=1.0,
        max_compatible_batch_size=1,
    ),
    TemporalRegime.COMPATIBLE_BUNDLE: RegimeThresholds(
        min_unique_task_count=24,
        max_unique_task_count=24,
        min_material_recurrence=1.0,
        min_compatible_batch_size=3,
        max_compatible_batch_size=3,
        min_bundled_event_fraction=1.0,
    ),
    TemporalRegime.HIGH_MIX: RegimeThresholds(
        min_unique_task_count=24,
        max_unique_task_count=24,
        max_task_concentration=float(1 / 24),
        min_material_recurrence=0.8,
        max_compatible_batch_size=1,
        max_bundled_event_fraction=0.0,
    ),
    TemporalRegime.REGIME_SHIFT: RegimeThresholds(
        min_first_half_task_concentration=1.0,
        min_second_half_unique_task_count=12,
        max_second_half_task_concentration=float(1 / 12),
        max_segment_task_overlap=0.0,
        min_material_recurrence=1.0,
        max_compatible_batch_size=1,
    ),
}


def _threshold_failures(
    diagnostics: TemporalStreamDiagnostics,
) -> tuple[str, ...]:
    thresholds = diagnostics.thresholds
    checks = (
        (
            "min_unique_task_count",
            thresholds.min_unique_task_count,
            diagnostics.unique_task_count,
            "min",
        ),
        (
            "max_unique_task_count",
            thresholds.max_unique_task_count,
            diagnostics.unique_task_count,
            "max",
        ),
        (
            "min_task_concentration",
            thresholds.min_task_concentration,
            diagnostics.max_task_concentration,
            "min",
        ),
        (
            "max_task_concentration",
            thresholds.max_task_concentration,
            diagnostics.max_task_concentration,
            "max",
        ),
        (
            "min_shape_recurrence",
            thresholds.min_shape_recurrence,
            diagnostics.shape_recurrence,
            "min",
        ),
        (
            "max_shape_recurrence",
            thresholds.max_shape_recurrence,
            diagnostics.shape_recurrence,
            "max",
        ),
        (
            "min_family_concentration",
            thresholds.min_family_concentration,
            diagnostics.family_concentration,
            "min",
        ),
        (
            "max_family_concentration",
            thresholds.max_family_concentration,
            diagnostics.family_concentration,
            "max",
        ),
        (
            "min_material_recurrence",
            thresholds.min_material_recurrence,
            diagnostics.material_recurrence,
            "min",
        ),
        (
            "max_material_recurrence",
            thresholds.max_material_recurrence,
            diagnostics.material_recurrence,
            "max",
        ),
        (
            "min_compatible_batch_size",
            thresholds.min_compatible_batch_size,
            diagnostics.max_compatible_batch_size,
            "min",
        ),
        (
            "max_compatible_batch_size",
            thresholds.max_compatible_batch_size,
            diagnostics.max_compatible_batch_size,
            "max",
        ),
        (
            "min_bundled_event_fraction",
            thresholds.min_bundled_event_fraction,
            diagnostics.compatibly_bundled_event_fraction,
            "min",
        ),
        (
            "max_bundled_event_fraction",
            thresholds.max_bundled_event_fraction,
            diagnostics.compatibly_bundled_event_fraction,
            "max",
        ),
        (
            "min_first_half_task_concentration",
            thresholds.min_first_half_task_concentration,
            diagnostics.first_half_task_concentration,
            "min",
        ),
        (
            "min_second_half_unique_task_count",
            thresholds.min_second_half_unique_task_count,
            diagnostics.second_half_unique_task_count,
            "min",
        ),
        (
            "max_second_half_task_concentration",
            thresholds.max_second_half_task_concentration,
            diagnostics.second_half_task_concentration,
            "max",
        ),
        (
            "max_segment_task_overlap",
            thresholds.max_segment_task_overlap,
            diagnostics.segment_task_overlap,
            "max",
        ),
    )
    failures: list[str] = []
    for name, bound, actual, direction in checks:
        if bound is None:
            continue
        missed = actual < bound - 1e-12 if direction == "min" else actual > bound + 1e-12
        if missed:
            failures.append(f"{name}={bound} (realized {actual})")
    return tuple(failures)


def calculate_diagnostics(
    events: tuple[TemporalEvent, ...],
    regime: TemporalRegime,
) -> TemporalStreamDiagnostics:
    task_counts = Counter(event.source_task.tasks_index for event in events)
    family_counts = Counter(event.source_task.family_signature for event in events)
    seen_shapes = set(events[0].source_task.shape_hashes)
    recurrent_shapes = 0
    shape_population = 0
    for event in events[1:]:
        shape_population += len(event.source_task.shape_hashes)
        recurrent_shapes += sum(shape in seen_shapes for shape in event.source_task.shape_hashes)
        seen_shapes.update(event.source_task.shape_hashes)
    seen_materials = {_material_key(events[0].material)}
    recurrent_materials = 0
    for event in events[1:]:
        key = _material_key(event.material)
        recurrent_materials += key in seen_materials
        seen_materials.add(key)
    compatible_groups: dict[
        tuple[datetime, tuple[str, str, str, str, str], StockSignature], int
    ] = defaultdict(int)
    for event in events:
        key = (event.occurred_at, _material_key(event.material), event.source_task.stock_signature)
        compatible_groups[key] += 1
    bundled_events = sum(size for size in compatible_groups.values() if size > 1)
    split = len(events) // 2
    first = events[:split]
    second = events[split:]
    first_counts = Counter(event.source_task.tasks_index for event in first)
    second_counts = Counter(event.source_task.tasks_index for event in second)
    first_tasks = set(first_counts)
    second_tasks = set(second_counts)
    overlap_union = first_tasks | second_tasks
    thresholds = _THRESHOLDS[regime]
    draft = TemporalStreamDiagnostics(
        unique_task_count=len(task_counts),
        max_task_concentration=float(max(task_counts.values()) / len(events)),
        shape_recurrence=float(recurrent_shapes / shape_population),
        family_concentration=float(max(family_counts.values()) / len(events)),
        material_recurrence=float(recurrent_materials / (len(events) - 1)),
        max_compatible_batch_size=max(compatible_groups.values()),
        compatibly_bundled_event_fraction=float(bundled_events / len(events)),
        first_half_task_concentration=float(max(first_counts.values()) / len(first)),
        second_half_unique_task_count=len(second_counts),
        second_half_task_concentration=float(max(second_counts.values()) / len(second)),
        segment_task_overlap=float(len(first_tasks & second_tasks) / len(overlap_union)),
        total_part_references=sum(len(event.source_task.part_ids) for event in events),
        thresholds=thresholds,
        threshold_failures=(),
    )
    return draft.model_copy(update={"threshold_failures": _threshold_failures(draft)})


class TemporalStreamManifest(TemporalContractModel):
    schema_version: Literal["yieldforge.temporal-stream.v1"] = "yieldforge.temporal-stream.v1"
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cell_id: StrictStr = Field(pattern=r"^yfm6c-[0-9a-f]{20}$")
    regime: TemporalRegime
    seed: StrictInt
    partition: TemporalPartition
    source_catalog_sha256: Literal[SOURCE_CATALOG_SHA256] = SOURCE_CATALOG_SHA256
    generator: TemporalGeneratorIdentity
    timing: TemporalTiming
    events: tuple[TemporalEvent, ...] = Field(min_length=2)
    diagnostics: TemporalStreamDiagnostics
    claim_ceiling: Literal[
        "controlled_temporal_construction_only_not_factory_demand_policy_value_or_savings"
    ] = "controlled_temporal_construction_only_not_factory_demand_policy_value_or_savings"

    @model_validator(mode="after")
    def require_consistent_realization_and_identity(self) -> Self:
        if len(self.events) != self.timing.event_count:
            raise ValueError("stream event count differs from registered timing")
        if tuple(event.sequence for event in self.events) != tuple(range(len(self.events))):
            raise ValueError("event sequences must be contiguous from zero")
        for event in self.events:
            slot = (
                event.sequence // self.timing.compatible_bundle_size
                if self.regime is TemporalRegime.COMPATIBLE_BUNDLE
                else event.sequence
            )
            expected_time = self.timing.starts_at + timedelta(
                minutes=self.timing.interval_minutes * slot
            )
            if event.occurred_at != expected_time:
                raise ValueError("event chronology differs from registered regime timing")
            if event.event_id != expected_event_id(
                self.cell_id,
                event.sequence,
                event.occurred_at,
                event.source_task,
                event.material,
                event.economics,
            ):
                raise ValueError("event ID differs from deterministic event content")
        expected_diagnostics = calculate_diagnostics(self.events, self.regime)
        if self.diagnostics != expected_diagnostics:
            raise ValueError("stream diagnostics differ from realized events")
        if self.diagnostics.threshold_failures:
            raise ValueError("stream missed registered regime thresholds")
        digest = _semantic_sha256(self, excluded={"stream_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("stream content hash mismatch")
        if self.stream_id != f"yfts-{digest[:24]}":
            raise ValueError("stream ID does not match content hash")
        return self

    def canonical_bytes(self) -> bytes:
        """Return the stable persisted JSON representation for immutable archives."""

        return (
            json.dumps(
                self.model_dump(mode="json"),
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()


class BaselineTemporalView(TemporalContractModel):
    schema_version: Literal["yieldforge.temporal-baseline-view.v1"] = (
        "yieldforge.temporal-baseline-view.v1"
    )
    contract_id: StrictStr
    stream_id: StrictStr
    as_of: datetime
    events: tuple[TemporalEvent, ...]

    @field_validator("as_of")
    @classmethod
    def require_aware_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("baseline as-of time must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def prohibit_future_events(self) -> Self:
        if any(event.occurred_at > self.as_of for event in self.events):
            raise ValueError("baseline view cannot contain future events")
        return self


class OracleTemporalView(TemporalContractModel):
    schema_version: Literal["yieldforge.temporal-oracle-view.v1"] = (
        "yieldforge.temporal-oracle-view.v1"
    )
    contract_id: StrictStr
    stream_id: StrictStr
    regime: TemporalRegime
    events: tuple[TemporalEvent, ...]
    diagnostics: TemporalStreamDiagnostics


def _event_reference(catalog: CatalogSnapshot, tasks_index: int) -> SourceTaskEventReference:
    task = catalog.task(tasks_index)
    parts = catalog.parts_for_task(tasks_index)
    return SourceTaskEventReference(
        tasks_index=task.tasks_index,
        source_row_index=task.source_row_index,
        part_ids=tuple(part.part_id for part in parts),
        part_source_row_indices=tuple(part.source_row_index for part in parts),
        shape_hashes=tuple(part.shape_hash for part in parts),
        sheet_type=task.sheet_type,
        sheet_length=task.sheet_length,
        sheet_width=task.sheet_width,
    )


def _choose_stock_group(
    catalog: CatalogSnapshot,
    rng: random.Random,
    minimum_size: int,
) -> list[int]:
    runnable = set(catalog.runnable_task_ids)
    groups = [
        list(task_ids)
        for _, task_ids in sorted(catalog.stock_groups.items())
        if len(set(task_ids) & runnable) >= minimum_size
    ]
    if not groups:
        raise GenerationError(f"source pool cannot realize a {minimum_size}-task stock group")
    group = [task_id for task_id in groups[rng.randrange(len(groups))] if task_id in runnable]
    rng.shuffle(group)
    return group


def _greedy_novel_tasks(
    catalog: CatalogSnapshot,
    candidates: list[int],
    count: int,
    *,
    initially_seen: set[int] | None = None,
) -> list[int]:
    selected: list[int] = []
    seen = set(initially_seen or ())
    remaining = list(candidates)
    while remaining and len(selected) < count:
        chosen = max(
            remaining,
            key=lambda task_id: len(set(catalog.profile(task_id).family_signature) - seen),
        )
        remaining.remove(chosen)
        selected.append(chosen)
        seen.update(catalog.profile(chosen).family_signature)
    if len(selected) != count:
        raise GenerationError(f"source pool cannot realize {count} distinct tasks")
    return selected


def _select_tasks(
    regime: TemporalRegime,
    seed: int,
    catalog: CatalogSnapshot,
    event_count: int,
) -> list[int]:
    rng = random.Random(seed)
    runnable = list(catalog.runnable_task_ids)
    if regime is TemporalRegime.NO_SIGNAL:
        rng.shuffle(runnable)
        selected: list[int] = []
        seen: set[int] = set()
        for task_id in runnable:
            shapes = set(catalog.profile(task_id).family_signature)
            if not shapes & seen:
                selected.append(task_id)
                seen.update(shapes)
            if len(selected) == event_count:
                return selected
        raise GenerationError("source pool cannot realize the no-signal construction")
    if regime is TemporalRegime.EXACT_RECURRENCE:
        if not runnable:
            raise GenerationError("source pool cannot realize exact recurrence")
        return [runnable[rng.randrange(len(runnable))]] * event_count
    if regime is TemporalRegime.FAMILY_SIMILARITY:
        runnable_set = set(runnable)
        families = [
            list(task_ids)
            for _, task_ids in sorted(catalog.family_stock_groups.items())
            if len(set(task_ids) & runnable_set) >= 4
        ]
        if not families:
            raise GenerationError("source pool cannot realize the family-similarity construction")
        family = [
            task_id
            for task_id in families[rng.randrange(len(families))]
            if task_id in runnable_set
        ]
        rng.shuffle(family)
        return [family[index % len(family)] for index in range(event_count)]
    if regime is TemporalRegime.COMPATIBLE_BUNDLE:
        return _choose_stock_group(catalog, rng, event_count)[:event_count]
    if regime is TemporalRegime.HIGH_MIX:
        candidates = _choose_stock_group(catalog, rng, event_count)
        return _greedy_novel_tasks(catalog, candidates, event_count)
    if regime is TemporalRegime.REGIME_SHIFT:
        candidates = _choose_stock_group(catalog, rng, event_count // 2 + 1)
        anchor = candidates.pop(0)
        late = _greedy_novel_tasks(
            catalog,
            candidates,
            event_count // 2,
            initially_seen=set(catalog.profile(anchor).family_signature),
        )
        return [anchor] * (event_count // 2) + late
    raise GenerationError(f"unsupported M6 regime {regime}")


def _material(regime: TemporalRegime, seed: int, sequence: int) -> MaterialIdentity:
    if regime is TemporalRegime.NO_SIGNAL:
        material_code = f"m6-no-signal-{seed}-{sequence:02d}"
        thickness = f"unique-{sequence:02d}"
    elif regime is TemporalRegime.HIGH_MIX:
        material_code = f"m6-high-mix-{sequence % 3}"
        thickness = f"class-{sequence % 3}"
    else:
        material_code = f"m6-compatible-{seed}"
        thickness = "class-0"
    return MaterialIdentity(
        material_code=material_code,
        grade="m6-assumed-grade",
        thickness=thickness,
        surface="m6-assumed-surface",
        grain="m6-assumed-grain",
        provenance=MaterialProvenance.ASSUMED,
    )


def generate_stream(
    contract: TemporalBenchmarkContract,
    cell: TemporalPopulationCell,
    catalog: CatalogSnapshot,
) -> TemporalStreamManifest:
    """Realize one registered content-addressed stream from the pinned source catalog."""

    if cell not in contract.population_cells:
        raise GenerationError("population cell is not registered by the M6 contract")
    if catalog.artifact_sha256 != contract.source_catalog.artifact_sha256:
        raise GenerationError("catalog does not match the M6 contract")
    selected = _select_tasks(
        cell.regime,
        cell.seed,
        catalog,
        contract.timing.event_count,
    )
    rng = random.Random(cell.seed ^ 0x5A17F06E)
    events: list[TemporalEvent] = []
    for sequence, tasks_index in enumerate(selected):
        slot = (
            sequence // contract.timing.compatible_bundle_size
            if cell.regime is TemporalRegime.COMPATIBLE_BUNDLE
            else sequence
        )
        occurred_at = contract.timing.starts_at + timedelta(
            minutes=contract.timing.interval_minutes * slot
        )
        source_task = _event_reference(catalog, tasks_index)
        material = _material(cell.regime, cell.seed, sequence)
        economics = EconomicFields(
            priority_score=float(round(rng.random(), 6)),
            value_index=float(round(10 + 990 * rng.random(), 6)),
            lead_time_minutes=60 * (1 + rng.randrange(72)),
        )
        events.append(
            TemporalEvent(
                sequence=sequence,
                event_id=expected_event_id(
                    cell.cell_id,
                    sequence,
                    occurred_at,
                    source_task,
                    material,
                    economics,
                ),
                occurred_at=occurred_at,
                source_task=source_task,
                material=material,
                economics=economics,
            )
        )
    frozen_events = tuple(events)
    diagnostics = calculate_diagnostics(frozen_events, cell.regime)
    if diagnostics.threshold_failures:
        raise GenerationError(
            "registered regime thresholds missed: " + "; ".join(diagnostics.threshold_failures)
        )
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.temporal-stream.v1",
        "contract_id": contract.contract_id,
        "contract_sha256": contract.content_sha256,
        "cell_id": cell.cell_id,
        "regime": cell.regime.value,
        "seed": cell.seed,
        "partition": cell.partition.value,
        "source_catalog_sha256": catalog.artifact_sha256,
        "generator": contract.generator.model_dump(mode="json"),
        "timing": contract.timing.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in frozen_events],
        "diagnostics": diagnostics.model_dump(mode="json"),
        "claim_ceiling": (
            "controlled_temporal_construction_only_not_factory_demand_policy_value_or_savings"
        ),
    }
    digest = _semantic_sha256(semantic)
    return TemporalStreamManifest.model_validate_json(
        json.dumps(
            {
                **semantic,
                "stream_id": f"yfts-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            },
            allow_nan=False,
        ),
        strict=True,
    )


def baseline_view(stream: TemporalStreamManifest, *, as_of: datetime) -> BaselineTemporalView:
    """Expose only events released by one timestamp, without regime metadata."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("baseline as-of time must be timezone-aware")
    canonical_as_of = as_of.astimezone(UTC)
    return BaselineTemporalView(
        contract_id=stream.contract_id,
        stream_id=stream.stream_id,
        as_of=canonical_as_of,
        events=tuple(event for event in stream.events if event.occurred_at <= canonical_as_of),
    )


def oracle_view(stream: TemporalStreamManifest) -> OracleTemporalView:
    """Expose the explicit analysis-only full future and construction metadata."""

    return OracleTemporalView(
        contract_id=stream.contract_id,
        stream_id=stream.stream_id,
        regime=stream.regime,
        events=stream.events,
        diagnostics=stream.diagnostics,
    )
