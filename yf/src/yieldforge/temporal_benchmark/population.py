"""Immutable publication and full validation of the registered M6 population."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from yieldforge.experiments.contracts import M0ExperimentContract, load_frozen_json
from yieldforge.temporal_benchmark.catalog import (
    COMMITTED_CATALOG_MANIFEST_PATH,
    COMMITTED_CATALOG_PATH,
    CatalogSnapshot,
    load_catalog,
)
from yieldforge.temporal_benchmark.contracts import (
    SOURCE_CATALOG_SHA256,
    TemporalBenchmarkContract,
    TemporalContractModel,
    TemporalPartition,
    TemporalRegime,
    build_registered_contract,
)
from yieldforge.temporal_benchmark.generator import (
    GenerationError,
    TemporalStreamManifest,
    generate_stream,
)
from yieldforge.temporal_benchmark.lowering import lower_stream

_MAX_CONTRACT_BYTES = 2 * 1024 * 1024
_MAX_POPULATION_BYTES = 4 * 1024 * 1024
_MAX_STREAM_BYTES = 16 * 1024 * 1024
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_M0_PATH = _PACKAGE_ROOT / "experiments/m0-contract-v1.json"


class PopulationEvidenceError(ValueError):
    """The M6 population or one immutable artifact failed validation."""


class PopulationStreamRecord(TemporalContractModel):
    cell_id: StrictStr = Field(pattern=r"^yfm6c-[0-9a-f]{20}$")
    regime: TemporalRegime
    seed: StrictInt
    partition: TemporalPartition
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    stream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    filename: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}\.json$")
    event_count: Literal[24] = 24
    part_count: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def require_filename_identity(self) -> Self:
        if self.filename != f"{self.stream_id}.json":
            raise ValueError("population stream filename must bind its stream ID")
        return self


class PopulationFailure(TemporalContractModel):
    cell_id: StrictStr = Field(pattern=r"^yfm6c-[0-9a-f]{20}$")
    regime: TemporalRegime
    seed: StrictInt
    partition: TemporalPartition
    reason: StrictStr = Field(min_length=1)


class TemporalPopulationManifest(TemporalContractModel):
    schema_version: Literal["yieldforge.temporal-population.v1"] = (
        "yieldforge.temporal-population.v1"
    )
    population_id: StrictStr = Field(pattern=r"^yftp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_catalog_sha256: Literal[SOURCE_CATALOG_SHA256] = SOURCE_CATALOG_SHA256
    registered_cell_count: Literal[48] = 48
    stream_count: StrictInt = Field(ge=0, le=48)
    calibration_stream_count: StrictInt = Field(ge=0, le=12)
    evaluation_stream_count: StrictInt = Field(ge=0, le=36)
    streams: tuple[PopulationStreamRecord, ...]
    failed_cells: tuple[PopulationFailure, ...]
    claim_ceiling: Literal[
        "immutable_temporal_population_only_not_policy_comparison_or_savings_evidence"
    ] = "immutable_temporal_population_only_not_policy_comparison_or_savings_evidence"

    @model_validator(mode="after")
    def require_complete_population_and_identity(self) -> Self:
        if self.stream_count != len(self.streams):
            raise ValueError("population stream count does not reconcile")
        if self.calibration_stream_count != sum(
            item.partition is TemporalPartition.CALIBRATION for item in self.streams
        ):
            raise ValueError("population calibration count does not reconcile")
        if self.evaluation_stream_count != sum(
            item.partition is TemporalPartition.EVALUATION for item in self.streams
        ):
            raise ValueError("population evaluation count does not reconcile")
        cells = tuple(item.cell_id for item in (*self.streams, *self.failed_cells))
        if len(cells) != self.registered_cell_count or len(cells) != len(set(cells)):
            raise ValueError("population must report every registered cell exactly once")
        if len({item.stream_id for item in self.streams}) != len(self.streams):
            raise ValueError("population stream IDs must be unique")
        payload = self.model_dump(
            mode="json",
            exclude={"population_id", "content_sha256"},
        )
        digest = hashlib.sha256(_canonical_json_bytes(payload, pretty=False)).hexdigest()
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("population content hash mismatch")
        if self.population_id != f"yftp-{digest[:24]}":
            raise ValueError("population ID does not match content hash")
        return self


class PopulationValidationSummary(TemporalContractModel):
    schema_version: Literal["yieldforge.temporal-population-validation.v1"] = (
        "yieldforge.temporal-population-validation.v1"
    )
    valid: StrictBool
    contract_id: StrictStr
    population_id: StrictStr
    stream_count: StrictInt = Field(ge=0)
    regenerated_stream_count: StrictInt = Field(ge=0)
    lowered_stream_count: StrictInt = Field(ge=0)
    event_count: StrictInt = Field(ge=0)
    batch_count: StrictInt = Field(ge=0)
    part_count: StrictInt = Field(ge=0)


@dataclass(frozen=True)
class PublishedPopulationPaths:
    contract_path: Path
    population_path: Path
    stream_paths: tuple[Path, ...]


def _canonical_json_bytes(value: object, *, pretty: bool) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(
        value,
        allow_nan=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
    )
    return (encoded + ("\n" if pretty else "")).encode()


def _read_regular_file(path: Path, *, maximum_bytes: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PopulationEvidenceError(f"{label} could not be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PopulationEvidenceError(f"{label} must be a regular file and not a symlink")
    if metadata.st_size > maximum_bytes:
        raise PopulationEvidenceError(f"{label} exceeds its byte limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except OSError as error:
        raise PopulationEvidenceError(f"{label} could not be read safely") from error
    if len(payload) > maximum_bytes:
        raise PopulationEvidenceError(f"{label} exceeds its byte limit")
    return payload


def _publish(path: Path, payload: bytes, *, label: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        existing = _read_regular_file(path, maximum_bytes=max(len(payload), 1), label=label)
        if existing != payload:
            raise PopulationEvidenceError(f"{label} is immutable and differs from existing bytes")
        return path
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        temporary.rename(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def build_population(
    contract: TemporalBenchmarkContract,
    catalog: CatalogSnapshot,
) -> tuple[TemporalPopulationManifest, dict[str, TemporalStreamManifest]]:
    """Generate every registered cell, preserving any failure in the population index."""

    streams: dict[str, TemporalStreamManifest] = {}
    records: list[PopulationStreamRecord] = []
    failures: list[PopulationFailure] = []
    for cell in contract.population_cells:
        try:
            stream = generate_stream(contract, cell, catalog)
        except GenerationError as error:
            failures.append(
                PopulationFailure(
                    cell_id=cell.cell_id,
                    regime=cell.regime,
                    seed=cell.seed,
                    partition=cell.partition,
                    reason=str(error),
                )
            )
            continue
        streams[stream.stream_id] = stream
        records.append(
            PopulationStreamRecord(
                cell_id=cell.cell_id,
                regime=cell.regime,
                seed=cell.seed,
                partition=cell.partition,
                stream_id=stream.stream_id,
                stream_sha256=stream.content_sha256,
                filename=f"{stream.stream_id}.json",
                part_count=stream.diagnostics.total_part_references,
            )
        )
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.temporal-population.v1",
        "contract_id": contract.contract_id,
        "contract_sha256": contract.content_sha256,
        "source_catalog_sha256": catalog.artifact_sha256,
        "registered_cell_count": len(contract.population_cells),
        "stream_count": len(records),
        "calibration_stream_count": sum(
            record.partition is TemporalPartition.CALIBRATION for record in records
        ),
        "evaluation_stream_count": sum(
            record.partition is TemporalPartition.EVALUATION for record in records
        ),
        "streams": [record.model_dump(mode="json") for record in records],
        "failed_cells": [failure.model_dump(mode="json") for failure in failures],
        "claim_ceiling": (
            "immutable_temporal_population_only_not_policy_comparison_or_savings_evidence"
        ),
    }
    digest = hashlib.sha256(_canonical_json_bytes(semantic, pretty=False)).hexdigest()
    population = TemporalPopulationManifest.model_validate_json(
        json.dumps(
            {
                **semantic,
                "population_id": f"yftp-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            },
            allow_nan=False,
        ),
        strict=True,
    )
    return population, streams


def publish_population_artifacts(
    *,
    contract_path: Path,
    population_path: Path,
    stream_root: Path,
    contract: TemporalBenchmarkContract,
    population: TemporalPopulationManifest,
    streams: dict[str, TemporalStreamManifest],
) -> PublishedPopulationPaths:
    """Publish a complete population idempotently without overwriting divergent evidence."""

    expected_ids = {record.stream_id for record in population.streams}
    if set(streams) != expected_ids:
        raise PopulationEvidenceError("published streams do not match the population index")
    contract_path = _publish(
        Path(contract_path),
        _canonical_json_bytes(contract, pretty=True),
        label="M6 contract artifact",
    )
    stream_root = Path(stream_root)
    stream_paths = tuple(
        _publish(
            stream_root / record.filename,
            streams[record.stream_id].canonical_bytes(),
            label=f"M6 stream {record.stream_id}",
        )
        for record in population.streams
    )
    population_path = _publish(
        Path(population_path),
        _canonical_json_bytes(population, pretty=True),
        label="M6 population artifact",
    )
    return PublishedPopulationPaths(
        contract_path=contract_path,
        population_path=population_path,
        stream_paths=stream_paths,
    )


def _load_model[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
    *,
    maximum_bytes: int,
    label: str,
) -> ModelT:
    payload = _read_regular_file(path, maximum_bytes=maximum_bytes, label=label)
    try:
        return model.model_validate_json(payload, strict=True)
    except (ValidationError, ValueError) as error:
        raise PopulationEvidenceError(f"{label} failed strict validation") from error


def validate_population_artifacts(
    *,
    contract_path: Path,
    population_path: Path,
    stream_root: Path,
    m0_path: Path = COMMITTED_M0_PATH,
    catalog_path: Path = COMMITTED_CATALOG_PATH,
    catalog_manifest_path: Path = COMMITTED_CATALOG_MANIFEST_PATH,
) -> PopulationValidationSummary:
    """Validate, regenerate, and lower every registered M6 stream."""

    contract = _load_model(
        Path(contract_path),
        TemporalBenchmarkContract,
        maximum_bytes=_MAX_CONTRACT_BYTES,
        label="M6 contract artifact",
    )
    if contract != build_registered_contract():
        raise PopulationEvidenceError("M6 contract differs from the registered contract")
    try:
        m0 = load_frozen_json(Path(m0_path), M0ExperimentContract)
    except ValueError as error:
        raise PopulationEvidenceError("M6 could not validate its frozen M0 binding") from error
    if (
        m0.contract_id != contract.m0_contract_id
        or m0.content_sha256 != contract.m0_contract_sha256
    ):
        raise PopulationEvidenceError("M6 contract does not bind the supplied frozen M0 artifact")
    catalog = load_catalog(Path(catalog_path), Path(catalog_manifest_path))
    population = _load_model(
        Path(population_path),
        TemporalPopulationManifest,
        maximum_bytes=_MAX_POPULATION_BYTES,
        label="M6 population artifact",
    )
    if (
        population.contract_id != contract.contract_id
        or population.contract_sha256 != contract.content_sha256
        or population.source_catalog_sha256 != catalog.artifact_sha256
    ):
        raise PopulationEvidenceError("M6 population does not bind its contract and catalog")
    if population.failed_cells:
        raise PopulationEvidenceError("M6 population contains failed registered cells")
    registered_cells = tuple(cell.cell_id for cell in contract.population_cells)
    if tuple(record.cell_id for record in population.streams) != registered_cells:
        raise PopulationEvidenceError(
            "M6 population cell order or membership differs from contract"
        )

    stream_root = Path(stream_root)
    expected_files = {record.filename for record in population.streams}
    try:
        actual_files = {path.name for path in stream_root.iterdir()}
    except OSError as error:
        raise PopulationEvidenceError("M6 stream root could not be inspected") from error
    missing = expected_files - actual_files
    unexpected = actual_files - expected_files
    if missing:
        raise PopulationEvidenceError(f"missing registered stream artifact: {sorted(missing)[0]}")
    if unexpected:
        raise PopulationEvidenceError(f"unexpected stream artifact: {sorted(unexpected)[0]}")

    event_count = 0
    batch_count = 0
    part_count = 0
    regenerated = 0
    lowered = 0
    cells = {cell.cell_id: cell for cell in contract.population_cells}
    for record in population.streams:
        stream_path = stream_root / record.filename
        stream = _load_model(
            stream_path,
            TemporalStreamManifest,
            maximum_bytes=_MAX_STREAM_BYTES,
            label=f"M6 stream {record.stream_id}",
        )
        if (
            stream.stream_id != record.stream_id
            or stream.content_sha256 != record.stream_sha256
            or stream.cell_id != record.cell_id
            or stream.regime is not record.regime
            or stream.seed != record.seed
            or stream.partition is not record.partition
            or len(stream.events) != record.event_count
            or stream.diagnostics.total_part_references != record.part_count
        ):
            raise PopulationEvidenceError(
                f"M6 stream {record.stream_id} differs from its population record"
            )
        repeated = generate_stream(contract, cells[record.cell_id], catalog)
        if repeated != stream or repeated.canonical_bytes() != _read_regular_file(
            stream_path,
            maximum_bytes=_MAX_STREAM_BYTES,
            label=f"M6 stream {record.stream_id}",
        ):
            raise PopulationEvidenceError(
                f"M6 stream {record.stream_id} did not regenerate byte-for-byte"
            )
        regenerated += 1
        report = lower_stream(contract, stream, catalog)
        lowered += 1
        event_count += report.event_count
        batch_count += report.batch_count
        part_count += report.part_count
    return PopulationValidationSummary(
        valid=True,
        contract_id=contract.contract_id,
        population_id=population.population_id,
        stream_count=len(population.streams),
        regenerated_stream_count=regenerated,
        lowered_stream_count=lowered,
        event_count=event_count,
        batch_count=batch_count,
        part_count=part_count,
    )
