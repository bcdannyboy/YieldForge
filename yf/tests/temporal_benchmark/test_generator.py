from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from yieldforge.temporal_benchmark.catalog import load_registered_catalog
from yieldforge.temporal_benchmark.contracts import TemporalRegime, build_registered_contract
from yieldforge.temporal_benchmark.generator import (
    GenerationError,
    baseline_view,
    generate_stream,
    oracle_view,
)


@pytest.fixture(scope="module")
def generated_streams():  # type: ignore[no-untyped-def]
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    cells = tuple(
        next(
            cell
            for cell in contract.population_cells
            if cell.regime is regime and cell.seed == contract.common_seeds[0]
        )
        for regime in TemporalRegime
    )
    return contract, catalog, {
        cell.regime: generate_stream(contract, cell, catalog) for cell in cells
    }


def test_all_six_regimes_are_deterministic_and_meet_realized_construction_gates(
    generated_streams,
) -> None:  # type: ignore[no-untyped-def]
    contract, catalog, streams = generated_streams

    assert set(streams) == set(TemporalRegime)
    for stream in streams.values():
        cell = next(cell for cell in contract.population_cells if cell.cell_id == stream.cell_id)
        assert generate_stream(contract, cell, catalog) == stream
        assert len(stream.events) == 24
        assert stream.diagnostics.threshold_failures == ()
        assert stream.content_sha256.startswith("sha256:")
        assert stream.stream_id == f"yfts-{stream.content_sha256[7:31]}"


def test_no_signal_has_no_shape_or_material_compatibility(generated_streams) -> None:  # type: ignore[no-untyped-def]
    stream = generated_streams[2][TemporalRegime.NO_SIGNAL]

    assert stream.diagnostics.unique_task_count == 24
    assert stream.diagnostics.max_task_concentration == pytest.approx(1 / 24)
    assert stream.diagnostics.shape_recurrence == 0.0
    assert stream.diagnostics.material_recurrence == 0.0
    assert stream.diagnostics.max_compatible_batch_size == 1


def test_exact_and_family_recurrence_are_measurably_distinct(generated_streams) -> None:  # type: ignore[no-untyped-def]
    exact = generated_streams[2][TemporalRegime.EXACT_RECURRENCE]
    family = generated_streams[2][TemporalRegime.FAMILY_SIMILARITY]

    assert exact.diagnostics.unique_task_count == 1
    assert exact.diagnostics.max_task_concentration == 1.0
    assert exact.diagnostics.shape_recurrence == 1.0
    assert family.diagnostics.unique_task_count >= 4
    assert family.diagnostics.max_task_concentration <= 0.25
    assert family.diagnostics.family_concentration == 1.0
    assert family.diagnostics.shape_recurrence == 1.0


def test_compatible_bundle_high_mix_and_shift_have_distinct_measured_properties(
    generated_streams,
) -> None:  # type: ignore[no-untyped-def]
    bundle = generated_streams[2][TemporalRegime.COMPATIBLE_BUNDLE]
    high_mix = generated_streams[2][TemporalRegime.HIGH_MIX]
    shift = generated_streams[2][TemporalRegime.REGIME_SHIFT]

    assert bundle.diagnostics.max_compatible_batch_size == 3
    assert bundle.diagnostics.compatibly_bundled_event_fraction == 1.0
    assert high_mix.diagnostics.unique_task_count == 24
    assert high_mix.diagnostics.max_compatible_batch_size == 1
    assert high_mix.diagnostics.material_recurrence >= 0.8
    assert shift.diagnostics.first_half_task_concentration == 1.0
    assert shift.diagnostics.second_half_unique_task_count == 12
    assert shift.diagnostics.second_half_task_concentration == pytest.approx(1 / 12)
    assert shift.diagnostics.segment_task_overlap == 0.0


def test_different_common_seeds_produce_different_stream_identities() -> None:
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    cells = tuple(
        cell
        for cell in contract.population_cells
        if cell.regime is TemporalRegime.HIGH_MIX
    )

    streams = tuple(generate_stream(contract, cell, catalog) for cell in cells)

    assert len({stream.stream_id for stream in streams}) == len(contract.common_seeds)


def test_baseline_view_is_as_of_safe_and_oracle_is_explicit(generated_streams) -> None:  # type: ignore[no-untyped-def]
    stream = generated_streams[2][TemporalRegime.REGIME_SHIFT]
    as_of = stream.events[4].occurred_at

    baseline = baseline_view(stream, as_of=as_of)
    oracle = oracle_view(stream)

    assert len(baseline.events) == 5
    assert all(event.occurred_at <= as_of for event in baseline.events)
    assert "regime" not in baseline.model_dump(mode="json")
    assert "diagnostics" not in baseline.model_dump(mode="json")
    assert oracle.regime is TemporalRegime.REGIME_SHIFT
    assert oracle.events == stream.events
    assert baseline_view(stream, as_of=as_of + timedelta(minutes=30)) == baseline.model_copy(
        update={"as_of": as_of + timedelta(minutes=30)}
    )


def test_generation_fails_closed_when_source_pool_cannot_realize_a_regime() -> None:
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    cell = next(
        cell
        for cell in contract.population_cells
        if cell.regime is TemporalRegime.NO_SIGNAL
    )
    restricted = replace(catalog, runnable_task_ids=(6669,))

    with pytest.raises(GenerationError, match="cannot realize"):
        generate_stream(contract, cell, restricted)

