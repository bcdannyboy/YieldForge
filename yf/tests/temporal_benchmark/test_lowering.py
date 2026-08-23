from __future__ import annotations

from dataclasses import replace

import pytest

from yieldforge.reuse.contracts import MaterialIdentity, MaterialProvenance
from yieldforge.temporal_benchmark.catalog import load_registered_catalog
from yieldforge.temporal_benchmark.contracts import TemporalRegime, build_registered_contract
from yieldforge.temporal_benchmark.generator import generate_stream
from yieldforge.temporal_benchmark.lowering import (
    LoweringError,
    compatible_event_groups,
    lower_stream,
)


def _stream(regime: TemporalRegime):  # type: ignore[no-untyped-def]
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    cell = next(
        cell
        for cell in contract.population_cells
        if cell.regime is regime and cell.seed == contract.common_seeds[0]
    )
    return contract, catalog, generate_stream(contract, cell, catalog)


def test_compatible_bundle_lowers_to_eight_exact_three_event_batches() -> None:
    contract, catalog, stream = _stream(TemporalRegime.COMPATIBLE_BUNDLE)

    report = lower_stream(contract, stream, catalog)

    assert report.event_count == 24
    assert report.batch_count == 8
    assert all(len(batch.event_ids) == 3 for batch in report.batches)
    assert all(len(batch.source_tasks) == 3 for batch in report.batches)
    assert sum(batch.part_count for batch in report.batches) == report.part_count
    assert report.part_count == stream.diagnostics.total_part_references
    assert len(
        {part.id for batch in report.batches for part in batch.problem.parts}
    ) == report.part_count
    assert report.report_id == f"yftl-{report.content_sha256[7:31]}"


def test_lowering_preserves_source_recorded_projection_identity_and_part_contracts() -> None:
    contract, catalog, stream = _stream(TemporalRegime.FAMILY_SIMILARITY)

    report = lower_stream(contract, stream, catalog)

    for batch in report.batches:
        assert batch.problem.sheet_length == batch.sheet_length
        assert batch.problem.strip_height == batch.sheet_width
        for projection in batch.projections:
            source = catalog.project(projection.tasks_index)
            assert projection.projection_sha256 == source.projection.projection_sha256
            assert projection.assumption_codes == source.projection.assumption_codes
            assert projection.source_flip_part_count == source.projection.source_flip_part_count
            assert projection.part_count == len(source.problem.parts)
        assert all(part.allowed_orientations for part in batch.problem.parts)


def test_compatible_grouping_splits_incompatible_same_timestamp_work_deterministically() -> None:
    _, _, stream = _stream(TemporalRegime.COMPATIBLE_BUNDLE)
    first_three = stream.events[:3]
    incompatible_material = MaterialIdentity(
        material_code="m6-explicit-incompatible",
        grade="m6-assumed-grade",
        thickness="other",
        surface="m6-assumed-surface",
        grain="m6-assumed-grain",
        provenance=MaterialProvenance.ASSUMED,
    )
    events = (
        first_three[0],
        first_three[1].model_copy(update={"material": incompatible_material}),
        first_three[2],
    )

    groups = compatible_event_groups(events)

    assert tuple(len(group) for group in groups) == (2, 1)
    assert groups[0][0].event_id == first_three[0].event_id
    assert groups[1][0].material == incompatible_material


def test_lowering_fails_closed_on_catalog_or_source_reference_mismatch() -> None:
    contract, catalog, stream = _stream(TemporalRegime.HIGH_MIX)

    with pytest.raises(LoweringError, match="catalog does not match"):
        lower_stream(
            contract,
            stream,
            replace(catalog, artifact_sha256="0" * 64),
        )

    source = stream.events[0].source_task
    altered_source = source.model_copy(
        update={"part_ids": (source.part_ids[0] + 1, *source.part_ids[1:])}
    )
    altered_event = stream.events[0].model_copy(update={"source_task": altered_source})
    altered_stream = stream.model_copy(update={"events": (altered_event, *stream.events[1:])})
    with pytest.raises(LoweringError, match="source task reference mismatch"):
        lower_stream(contract, altered_stream, catalog)

