from __future__ import annotations

from pathlib import Path

from yieldforge.temporal_benchmark.catalog import load_registered_catalog
from yieldforge.temporal_benchmark.contracts import (
    TemporalPartition,
    TemporalRegime,
    build_registered_contract,
)
from yieldforge.temporal_benchmark.pilot import (
    publish_pilot_result,
    run_lowering_pilot,
)
from yieldforge.temporal_benchmark.population import build_population


def test_stratified_pilot_selects_one_frozen_evaluation_stream_per_regime() -> None:
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    population, streams = build_population(contract, catalog)

    result = run_lowering_pilot(contract, population, streams, catalog)

    assert len(result.streams) == 6
    assert tuple(item.regime for item in result.streams) == tuple(TemporalRegime)
    assert {item.seed for item in result.streams} == {contract.common_seeds[2]}
    assert all(item.partition is TemporalPartition.EVALUATION for item in result.streams)
    assert result.event_count == 6 * 24
    assert result.batch_count == sum(item.batch_count for item in result.streams)
    assert result.part_count == sum(item.part_count for item in result.streams)
    assert result.projection_count == result.event_count
    assert result.exact_geometry_query_count == 3 * result.part_count
    assert result.invalid_geometry_count == 0
    assert result.exact_fit_search_call_count == 0
    assert result.collision_backend_triggered is False
    assert result.collision_backend_decision == "defer_until_repeated_fit_search_pilot"
    assert result.projected_full_population_minutes >= 0
    assert result.result_id == f"yfm6p-{result.content_sha256[7:31]}"


def test_pilot_result_publication_is_idempotent(tmp_path: Path) -> None:
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    population, streams = build_population(contract, catalog)
    result = run_lowering_pilot(contract, population, streams, catalog)

    first = publish_pilot_result(tmp_path, result)
    repeated = publish_pilot_result(tmp_path, result)

    assert first == repeated
    assert first.name == f"m6-lowering-pilot-{result.result_id}.json"
    assert first.read_bytes() == result.canonical_bytes()

