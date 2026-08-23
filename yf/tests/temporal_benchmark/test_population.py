from __future__ import annotations

from pathlib import Path

import pytest

from yieldforge.temporal_benchmark.catalog import load_registered_catalog
from yieldforge.temporal_benchmark.contracts import TemporalPartition, build_registered_contract
from yieldforge.temporal_benchmark.population import (
    PopulationEvidenceError,
    build_population,
    publish_population_artifacts,
    validate_population_artifacts,
)


@pytest.fixture(scope="module")
def built_population():  # type: ignore[no-untyped-def]
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    population, streams = build_population(contract, catalog)
    return contract, catalog, population, streams


def test_population_contains_every_registered_cell_once_with_exact_partitions(
    built_population,
) -> None:  # type: ignore[no-untyped-def]
    contract, _, population, streams = built_population

    assert population.stream_count == 48
    assert population.calibration_stream_count == 12
    assert population.evaluation_stream_count == 36
    assert population.failed_cells == ()
    assert len(streams) == 48
    assert tuple(record.cell_id for record in population.streams) == tuple(
        cell.cell_id for cell in contract.population_cells
    )
    assert sum(
        record.partition is TemporalPartition.CALIBRATION for record in population.streams
    ) == 12
    assert len({record.stream_id for record in population.streams}) == 48
    assert population.population_id == f"yftp-{population.content_sha256[7:31]}"


def test_population_build_is_byte_stable(built_population) -> None:  # type: ignore[no-untyped-def]
    contract, catalog, population, streams = built_population

    repeated_population, repeated_streams = build_population(contract, catalog)

    assert repeated_population == population
    assert repeated_streams == streams


def test_population_publication_is_immutable_idempotent_and_fully_validated(
    tmp_path: Path,
    built_population,
) -> None:  # type: ignore[no-untyped-def]
    contract, _, population, streams = built_population
    contract_path = tmp_path / "m6-contract-v1.json"
    population_path = tmp_path / "m6-population-v1.json"
    stream_root = tmp_path / "streams"

    published = publish_population_artifacts(
        contract_path=contract_path,
        population_path=population_path,
        stream_root=stream_root,
        contract=contract,
        population=population,
        streams=streams,
    )
    repeated = publish_population_artifacts(
        contract_path=contract_path,
        population_path=population_path,
        stream_root=stream_root,
        contract=contract,
        population=population,
        streams=streams,
    )
    summary = validate_population_artifacts(
        contract_path=contract_path,
        population_path=population_path,
        stream_root=stream_root,
    )

    assert published == repeated
    assert len(tuple(stream_root.glob("*.json"))) == 48
    assert summary.valid is True
    assert summary.stream_count == 48
    assert summary.regenerated_stream_count == 48
    assert summary.lowered_stream_count == 48
    assert summary.event_count == 48 * 24
    assert summary.part_count > summary.event_count
    assert summary.batch_count > 0

    first_path = stream_root / population.streams[0].filename
    first_path.write_text("{}")
    with pytest.raises(PopulationEvidenceError, match="immutable"):
        publish_population_artifacts(
            contract_path=contract_path,
            population_path=population_path,
            stream_root=stream_root,
            contract=contract,
            population=population,
            streams=streams,
        )


def test_population_validation_reports_missing_and_unexpected_stream_files(
    tmp_path: Path,
    built_population,
) -> None:  # type: ignore[no-untyped-def]
    contract, _, population, streams = built_population
    contract_path = tmp_path / "m6-contract-v1.json"
    population_path = tmp_path / "m6-population-v1.json"
    stream_root = tmp_path / "streams"
    publish_population_artifacts(
        contract_path=contract_path,
        population_path=population_path,
        stream_root=stream_root,
        contract=contract,
        population=population,
        streams=streams,
    )
    missing = stream_root / population.streams[0].filename
    missing.unlink()

    with pytest.raises(PopulationEvidenceError, match="missing registered stream"):
        validate_population_artifacts(
            contract_path=contract_path,
            population_path=population_path,
            stream_root=stream_root,
        )

    missing.write_bytes(streams[population.streams[0].stream_id].canonical_bytes())
    (stream_root / "unexpected.json").write_text("{}")
    with pytest.raises(PopulationEvidenceError, match="unexpected stream artifact"):
        validate_population_artifacts(
            contract_path=contract_path,
            population_path=population_path,
            stream_root=stream_root,
        )

