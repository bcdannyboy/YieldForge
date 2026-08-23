"""Source-faithful M6 stream lowering into M0-compatible replay batches."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from yieldforge.domain import Part, StripPackingProblem
from yieldforge.reuse.contracts import MaterialIdentity, MaterialProvenance
from yieldforge.temporal_benchmark.catalog import CatalogSnapshot, StockSignature
from yieldforge.temporal_benchmark.contracts import (
    SOURCE_CATALOG_SHA256,
    TemporalBenchmarkContract,
    TemporalContractModel,
)
from yieldforge.temporal_benchmark.generator import (
    SourceTaskEventReference,
    TemporalEvent,
    TemporalStreamManifest,
)


class LoweringError(ValueError):
    """A temporal stream could not be lowered without evidence loss."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _material_key(material: MaterialIdentity) -> tuple[str, str, str, str, str]:
    return (
        material.material_code,
        material.grade,
        material.thickness,
        material.surface,
        material.grain,
    )


def _group_key(
    event: TemporalEvent,
) -> tuple[datetime, tuple[str, str, str, str, str], StockSignature]:
    return (
        event.occurred_at,
        _material_key(event.material),
        event.source_task.stock_signature,
    )


def compatible_event_groups(
    events: tuple[TemporalEvent, ...],
) -> tuple[tuple[TemporalEvent, ...], ...]:
    """Group all and only same-time, same-material, same-stock work in stable order."""

    grouped: dict[
        tuple[datetime, tuple[str, str, str, str, str], StockSignature],
        list[TemporalEvent],
    ] = defaultdict(list)
    for event in events:
        grouped[_group_key(event)].append(event)
    return tuple(
        tuple(sorted(grouped[key], key=lambda event: event.sequence)) for key in sorted(grouped)
    )


def expected_batch_id(events: tuple[TemporalEvent, ...]) -> str:
    if not events:
        raise ValueError("lowered batch requires at least one event")
    key = _group_key(events[0])
    digest = hashlib.sha256(
        _canonical_bytes(
            {
                "event_ids": [event.event_id for event in events],
                "material": events[0].material.model_dump(mode="json"),
                "occurred_at": key[0].astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "stock_signature": key[2],
            }
        )
    ).hexdigest()
    return f"yftb-{digest[:20]}"


class LoweredProjection(TemporalContractModel):
    event_id: StrictStr = Field(pattern=r"^yfte-[0-9a-f]{20}$")
    tasks_index: StrictInt = Field(ge=0)
    projection_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    assumption_codes: tuple[StrictStr, ...]
    source_flip_part_count: StrictInt = Field(ge=0)
    part_count: StrictInt = Field(gt=0)


class LoweredReplayBatch(TemporalContractModel):
    schema_version: Literal["yieldforge.temporal-replay-batch.v1"] = (
        "yieldforge.temporal-replay-batch.v1"
    )
    sequence: StrictInt = Field(ge=0)
    batch_id: StrictStr = Field(pattern=r"^yftb-[0-9a-f]{20}$")
    released_at: datetime
    event_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_tasks: tuple[StrictInt, ...] = Field(min_length=1)
    material: MaterialIdentity
    sheet_type: StrictInt
    sheet_length: StrictFloat = Field(gt=0)
    sheet_width: StrictFloat = Field(gt=0)
    projections: tuple[LoweredProjection, ...] = Field(min_length=1)
    problem: StripPackingProblem
    part_count: StrictInt = Field(gt=0)
    geometry_provenance: Literal["source_observed_with_derived_projection"] = (
        "source_observed_with_derived_projection"
    )
    material_provenance: Literal["assumed"] = "assumed"

    @field_validator("released_at")
    @classmethod
    def canonicalize_release(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("batch release must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_reconciled_batch(self) -> Self:
        if self.material.provenance is not MaterialProvenance.ASSUMED:
            raise ValueError("lowered batch material must remain assumed")
        if len({len(self.event_ids), len(self.source_tasks), len(self.projections)}) != 1:
            raise ValueError("batch event, task, and projection counts must agree")
        if tuple(item.event_id for item in self.projections) != self.event_ids:
            raise ValueError("batch projection event order must match event IDs")
        if tuple(item.tasks_index for item in self.projections) != self.source_tasks:
            raise ValueError("batch projection task order must match source tasks")
        if self.part_count != len(self.problem.parts):
            raise ValueError("batch part count must match its solver problem")
        if self.part_count != sum(item.part_count for item in self.projections):
            raise ValueError("batch part count must reconcile projection part counts")
        if self.problem.sheet_length != self.sheet_length:
            raise ValueError("batch sheet length must match its solver problem")
        if self.problem.strip_height != self.sheet_width:
            raise ValueError("batch sheet width must match its solver problem")
        if self.problem.name != f"m6-temporal-batch-{self.batch_id}":
            raise ValueError("batch problem name must bind its batch identity")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("batch event IDs must be unique")
        if any(
            not any(part.id.startswith(f"{event_id}:") for part in self.problem.parts)
            for event_id in self.event_ids
        ):
            raise ValueError("each batch event must own at least one namespaced part")
        return self


class TemporalLoweringReport(TemporalContractModel):
    schema_version: Literal["yieldforge.temporal-lowering-report.v1"] = (
        "yieldforge.temporal-lowering-report.v1"
    )
    report_id: StrictStr = Field(pattern=r"^yftl-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    stream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_catalog_sha256: Literal[SOURCE_CATALOG_SHA256] = SOURCE_CATALOG_SHA256
    projection_mode: Literal["source_as_recorded"] = "source_as_recorded"
    event_count: StrictInt = Field(gt=0)
    batch_count: StrictInt = Field(gt=0)
    part_count: StrictInt = Field(gt=0)
    batches: tuple[LoweredReplayBatch, ...] = Field(min_length=1)
    claim_ceiling: Literal["replay_ready_demand_lowering_only_not_candidate_or_policy_evidence"] = (
        "replay_ready_demand_lowering_only_not_candidate_or_policy_evidence"
    )

    @model_validator(mode="after")
    def require_reconciled_report_and_identity(self) -> Self:
        if tuple(batch.sequence for batch in self.batches) != tuple(range(len(self.batches))):
            raise ValueError("lowered batch sequences must be contiguous")
        if self.batch_count != len(self.batches):
            raise ValueError("report batch count does not reconcile")
        if self.event_count != sum(len(batch.event_ids) for batch in self.batches):
            raise ValueError("report event count does not reconcile")
        if self.part_count != sum(batch.part_count for batch in self.batches):
            raise ValueError("report part count does not reconcile")
        event_ids = tuple(event_id for batch in self.batches for event_id in batch.event_ids)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("lowered events must occur in exactly one compatible batch")
        payload = self.model_dump(
            mode="json",
            exclude={"report_id", "content_sha256"},
        )
        digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("lowering report content hash mismatch")
        if self.report_id != f"yftl-{digest[:24]}":
            raise ValueError("lowering report ID does not match content hash")
        return self


def _expected_source_reference(
    catalog: CatalogSnapshot,
    tasks_index: int,
) -> SourceTaskEventReference:
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


def _lower_batch(
    sequence: int,
    events: tuple[TemporalEvent, ...],
    catalog: CatalogSnapshot,
) -> LoweredReplayBatch:
    batch_id = expected_batch_id(events)
    projections: list[LoweredProjection] = []
    combined_parts: list[Part] = []
    for event in events:
        if event.source_task != _expected_source_reference(
            catalog,
            event.source_task.tasks_index,
        ):
            raise LoweringError(
                f"source task reference mismatch for event {event.event_id}"
            )
        projected = catalog.project(event.source_task.tasks_index)
        projections.append(
            LoweredProjection(
                event_id=event.event_id,
                tasks_index=event.source_task.tasks_index,
                projection_sha256=projected.projection.projection_sha256,
                assumption_codes=projected.projection.assumption_codes,
                source_flip_part_count=projected.projection.source_flip_part_count,
                part_count=len(projected.problem.parts),
            )
        )
        combined_parts.extend(
            Part(
                id=f"{event.event_id}:{part.id}",
                shape=list(part.shape),
                demand=part.demand,
                allowed_orientations=(
                    list(part.allowed_orientations)
                    if part.allowed_orientations is not None
                    else None
                ),
            )
            for part in projected.problem.parts
        )
    first = events[0]
    return LoweredReplayBatch(
        sequence=sequence,
        batch_id=batch_id,
        released_at=first.occurred_at,
        event_ids=tuple(event.event_id for event in events),
        source_tasks=tuple(event.source_task.tasks_index for event in events),
        material=first.material,
        sheet_type=first.source_task.sheet_type,
        sheet_length=first.source_task.sheet_length,
        sheet_width=first.source_task.sheet_width,
        projections=tuple(projections),
        problem=StripPackingProblem(
            name=f"m6-temporal-batch-{batch_id}",
            strip_height=first.source_task.sheet_width,
            sheet_length=first.source_task.sheet_length,
            parts=combined_parts,
        ),
        part_count=len(combined_parts),
    )


def lower_stream(
    contract: TemporalBenchmarkContract,
    stream: TemporalStreamManifest,
    catalog: CatalogSnapshot,
) -> TemporalLoweringReport:
    """Resolve every event through exact source projection and compatible batching."""

    if (
        stream.contract_id != contract.contract_id
        or stream.contract_sha256 != contract.content_sha256
    ):
        raise LoweringError("stream does not bind the supplied M6 contract")
    if (
        catalog.artifact_sha256 != contract.source_catalog.artifact_sha256
        or stream.source_catalog_sha256 != catalog.artifact_sha256
    ):
        raise LoweringError("catalog does not match the M6 contract and stream")
    cell = next(
        (item for item in contract.population_cells if item.cell_id == stream.cell_id),
        None,
    )
    if cell is None or (cell.regime, cell.seed, cell.partition) != (
        stream.regime,
        stream.seed,
        stream.partition,
    ):
        raise LoweringError("stream dimensions do not match its registered population cell")

    groups = compatible_event_groups(stream.events)
    batches = tuple(
        _lower_batch(sequence, events, catalog) for sequence, events in enumerate(groups)
    )
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.temporal-lowering-report.v1",
        "contract_id": contract.contract_id,
        "contract_sha256": contract.content_sha256,
        "stream_id": stream.stream_id,
        "stream_sha256": stream.content_sha256,
        "source_catalog_sha256": catalog.artifact_sha256,
        "projection_mode": "source_as_recorded",
        "event_count": len(stream.events),
        "batch_count": len(batches),
        "part_count": sum(batch.part_count for batch in batches),
        "batches": [batch.model_dump(mode="json") for batch in batches],
        "claim_ceiling": "replay_ready_demand_lowering_only_not_candidate_or_policy_evidence",
    }
    digest = hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
    return TemporalLoweringReport.model_validate_json(
        json.dumps(
            {
                **semantic,
                "report_id": f"yftl-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            },
            allow_nan=False,
        ),
        strict=True,
    )

