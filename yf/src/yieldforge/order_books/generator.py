"""Deterministic hybrid generation from source-observed task compositions."""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from pathlib import Path

from yieldforge.datasets.passive_report import (
    parse_normalized_slice,
    read_passive_evidence_file,
)
from yieldforge.order_books.domain import (
    LECTRA_SLICE_SHA256,
    BaselineOrderBookView,
    EconomicFields,
    FieldFamily,
    FieldFamilyProvenance,
    GenerationRegime,
    GenerationRequest,
    GeneratorIdentity,
    MaterialAssignment,
    OracleOrderBookView,
    OrderBookManifest,
    OrderEvent,
    ProvenanceKind,
    SourceSliceIdentity,
    SourceTaskReference,
    calculate_diagnostics,
    canonical_json_bytes,
    expected_event_id,
    resolve_regime_thresholds,
    threshold_failures,
)

DEFAULT_GENERATOR = GeneratorIdentity()

COMMITTED_SLICE_RELATIVE_PATH = Path("datasets/fixtures/lectra-representative-slice.json")
COMMITTED_SLICE_PATH = Path(__file__).resolve().parents[3] / COMMITTED_SLICE_RELATIVE_PATH
COMMITTED_SLICE_SHA256 = LECTRA_SLICE_SHA256

_PROVENANCE = (
    FieldFamilyProvenance(
        family=FieldFamily.GEOMETRY,
        kind=ProvenanceKind.SOURCE_OBSERVED,
        explanation="Shape hashes reference normalized source part rows without mutation.",
    ),
    FieldFamilyProvenance(
        family=FieldFamily.COMPOSITION,
        kind=ProvenanceKind.SOURCE_OBSERVED,
        explanation="Part IDs and source-row order reference the selected source task unchanged.",
    ),
    FieldFamilyProvenance(
        family=FieldFamily.CHRONOLOGY,
        kind=ProvenanceKind.GENERATED,
        explanation="Arrival times derive only from starts_at, interval_minutes, and sequence.",
    ),
    FieldFamilyProvenance(
        family=FieldFamily.MATERIAL,
        kind=ProvenanceKind.ASSUMED,
        explanation="Synthetic material classes fill a field absent from the source corpus.",
    ),
    FieldFamilyProvenance(
        family=FieldFamily.ECONOMICS,
        kind=ProvenanceKind.GENERATED,
        explanation="Priority, value, and lead time are deterministic synthetic scenario fields.",
    ),
    FieldFamilyProvenance(
        family=FieldFamily.REGIME_LABEL,
        kind=ProvenanceKind.GENERATED,
        explanation="The regime is generator-only analysis metadata, never a source observation.",
    ),
)


def slice_sha256(path: Path) -> str:
    """Safely read and hash a bounded passive normalized slice file."""

    payload = read_passive_evidence_file(path, label="normalized Lectra slice")
    return hashlib.sha256(payload).hexdigest()


def _load_source(
    path: Path,
    expected_sha256: str,
) -> tuple[SourceSliceIdentity, tuple[SourceTaskReference, ...]]:
    if path.resolve(strict=True) != COMMITTED_SLICE_PATH.resolve(strict=True):
        raise ValueError("order books may only use the pinned committed normalized slice path")
    if expected_sha256 != COMMITTED_SLICE_SHA256:
        raise ValueError("generation request does not identify the committed normalized slice")
    payload = read_passive_evidence_file(path, label="normalized Lectra slice")
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256 or actual_sha256 != COMMITTED_SLICE_SHA256:
        raise ValueError(
            f"source_slice_sha256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    normalized = parse_normalized_slice(payload)
    source_meta = normalized.source
    dataset_id = source_meta.dataset_id
    identity = SourceSliceIdentity(
        dataset_id=dataset_id,
        content_sha256=f"sha256:{actual_sha256}",
        conversion_ruleset_version=source_meta.conversion_ruleset_version,
        doi=source_meta.doi,
    )
    parts_by_task: dict[int, list[object]] = {}
    for part in normalized.parts:
        parts_by_task.setdefault(part.tasks_index, []).append(part)
    references = tuple(
        SourceTaskReference(
            dataset_id=dataset_id,
            tasks_index=task.tasks_index,
            task_source_row_index=task.source_row_index,
            part_ids=tuple(part.part_id for part in parts_by_task[task.tasks_index]),
            part_source_row_indices=tuple(
                part.source_row_index for part in parts_by_task[task.tasks_index]
            ),
            shape_hashes=tuple(part.shape_hash for part in parts_by_task[task.tasks_index]),
        )
        for task in sorted(normalized.tasks, key=lambda item: item.tasks_index)
    )
    if len(references) < 2:
        raise ValueError(
            "hybrid order-book generation requires at least two source task references"
        )
    return identity, references


def committed_source_task_references() -> tuple[SourceTaskReference, ...]:
    """Return strictly validated task references from the pinned committed slice."""

    _, references = _load_source(COMMITTED_SLICE_PATH, COMMITTED_SLICE_SHA256)
    return references


def _select_tasks(
    request: GenerationRequest,
    references: tuple[SourceTaskReference, ...],
    rng: random.Random,
) -> list[SourceTaskReference]:
    if request.regime is GenerationRegime.EXACT_RECURRENCE:
        anchor = references[request.seed % len(references)]
        return [anchor] * request.event_count

    balanced = [references[index % len(references)] for index in range(request.event_count)]
    if request.regime is GenerationRegime.NO_SIGNAL:
        rng.shuffle(balanced)
    elif request.regime is GenerationRegime.HIGH_MIX:
        offset = request.seed % len(references)
        balanced = [
            references[(index + offset) % len(references)] for index in range(request.event_count)
        ]
    return balanced


def _build_events(
    request: GenerationRequest,
    references: list[SourceTaskReference],
    rng: random.Random,
) -> tuple[OrderEvent, ...]:
    materials = ("synthetic-mat-a", "synthetic-mat-b", "synthetic-mat-c")
    events: list[OrderEvent] = []
    for sequence, reference in enumerate(references):
        material = MaterialAssignment(
            material_code=materials[rng.randrange(len(materials))],
            thickness_index=1 + rng.randrange(4),
        )
        economics = EconomicFields(
            priority_score=float(round(rng.random(), 6)),
            value_index=float(round(10 + 990 * rng.random(), 6)),
            lead_time_minutes=60 * (1 + rng.randrange(72)),
        )
        events.append(
            OrderEvent(
                sequence=sequence,
                event_id=expected_event_id(
                    request,
                    sequence,
                    reference,
                    material,
                    economics,
                ),
                occurred_at=request.starts_at
                + timedelta(minutes=request.interval_minutes * sequence),
                source_task=reference,
                material=material,
                economics=economics,
            )
        )
    return tuple(events)


def generate_order_book(request: GenerationRequest, slice_path: Path) -> OrderBookManifest:
    """Generate and validate one content-addressed hybrid order book."""

    source_identity, source_references = _load_source(
        slice_path,
        request.source_slice_sha256,
    )
    thresholds = resolve_regime_thresholds(request)
    rng = random.Random(request.seed)
    selected = _select_tasks(request, source_references, rng)
    events = _build_events(request, selected, rng)
    diagnostics = calculate_diagnostics(events, thresholds)
    failures = threshold_failures(diagnostics)
    if failures:
        raise ValueError("declared regime thresholds missed: " + "; ".join(failures))

    payload = {
        "schema_version": "yieldforge.order-book.v1",
        "generator": DEFAULT_GENERATOR.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "source_slice": source_identity.model_dump(mode="json"),
        "field_provenance": [record.model_dump(mode="json") for record in _PROVENANCE],
        "events": [event.model_dump(mode="json") for event in events],
        "diagnostics": diagnostics.model_dump(mode="json"),
    }
    content_sha256 = "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return OrderBookManifest(
        order_book_id=f"yfob-{content_sha256[7:31]}",
        content_sha256=content_sha256,
        generator=DEFAULT_GENERATOR,
        request=request,
        source_slice=source_identity,
        field_provenance=_PROVENANCE,
        events=events,
        diagnostics=diagnostics,
    )


def baseline_view(manifest: OrderBookManifest, *, as_of: datetime) -> BaselineOrderBookView:
    """Return only information available at or before ``as_of`` without regime labels."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    safe_provenance = tuple(
        record
        for record in manifest.field_provenance
        if record.family is not FieldFamily.REGIME_LABEL
    )
    return BaselineOrderBookView(
        as_of=as_of,
        source_slice=manifest.source_slice,
        field_provenance=safe_provenance,
        events=tuple(event for event in manifest.events if event.occurred_at <= as_of),
    )


def oracle_view(manifest: OrderBookManifest) -> OracleOrderBookView:
    """Return the analysis-only full-book view, including the generator regime."""

    return OracleOrderBookView(
        order_book_id=manifest.order_book_id,
        regime=manifest.request.regime,
        events=manifest.events,
        diagnostics=manifest.diagnostics,
    )
