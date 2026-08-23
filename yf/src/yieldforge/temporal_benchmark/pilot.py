"""Stratified M6 lowering and exact-geometry feasibility pilot."""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import stat
from pathlib import Path
from time import perf_counter
from typing import Literal, Self

import shapely
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator
from shapely.geometry import Polygon

from yieldforge.temporal_benchmark.catalog import CatalogSnapshot
from yieldforge.temporal_benchmark.contracts import (
    TemporalBenchmarkContract,
    TemporalContractModel,
    TemporalPartition,
    TemporalRegime,
)
from yieldforge.temporal_benchmark.generator import TemporalStreamManifest
from yieldforge.temporal_benchmark.lowering import lower_stream
from yieldforge.temporal_benchmark.population import TemporalPopulationManifest


class PilotEvidenceError(ValueError):
    """The M6 pilot could not be executed or published safely."""


def _canonical_bytes(value: object, *, pretty: bool) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    encoded = json.dumps(
        value,
        allow_nan=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
    )
    return (encoded + ("\n" if pretty else "")).encode()


class PilotStreamMeasurement(TemporalContractModel):
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    regime: TemporalRegime
    seed: StrictInt
    partition: Literal[TemporalPartition.EVALUATION] = TemporalPartition.EVALUATION
    event_count: StrictInt = Field(gt=0)
    batch_count: StrictInt = Field(gt=0)
    part_count: StrictInt = Field(gt=0)
    projection_count: StrictInt = Field(gt=0)
    exact_geometry_query_count: StrictInt = Field(gt=0)
    invalid_geometry_count: StrictInt = Field(ge=0)
    lowering_seconds: StrictFloat = Field(ge=0)
    geometry_validation_seconds: StrictFloat = Field(ge=0)
    total_seconds: StrictFloat = Field(ge=0)

    @model_validator(mode="after")
    def require_reconciled_timing(self) -> Self:
        component_seconds = self.lowering_seconds + self.geometry_validation_seconds
        if abs(self.total_seconds - component_seconds) > 2e-6:
            raise ValueError("pilot stream timing does not reconcile")
        if self.exact_geometry_query_count != self.part_count * 3:
            raise ValueError("pilot exact geometry query count does not reconcile")
        if self.projection_count != self.event_count:
            raise ValueError("pilot projection count does not reconcile")
        return self


class M6LoweringPilotResult(TemporalContractModel):
    schema_version: Literal["yieldforge.m6-lowering-pilot.v1"] = (
        "yieldforge.m6-lowering-pilot.v1"
    )
    result_id: StrictStr = Field(pattern=r"^yfm6p-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    population_id: StrictStr = Field(pattern=r"^yftp-[0-9a-f]{24}$")
    population_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    shapely_version: StrictStr = Field(min_length=1)
    streams: tuple[PilotStreamMeasurement, ...] = Field(min_length=6, max_length=6)
    event_count: StrictInt = Field(gt=0)
    batch_count: StrictInt = Field(gt=0)
    part_count: StrictInt = Field(gt=0)
    projection_count: StrictInt = Field(gt=0)
    exact_geometry_query_count: StrictInt = Field(gt=0)
    invalid_geometry_count: StrictInt = Field(ge=0)
    exact_fit_search_call_count: Literal[0] = 0
    exact_fit_search_runtime_share: Literal[0.0] = 0.0
    measured_pilot_seconds: StrictFloat = Field(ge=0)
    projected_full_population_minutes: StrictFloat = Field(ge=0)
    collision_backend_triggered: StrictBool
    collision_backend_decision: Literal["defer_until_repeated_fit_search_pilot"] = (
        "defer_until_repeated_fit_search_pilot"
    )
    collision_backend_rule: Literal[
        "trigger_only_if_actual_fit_search_share_at_least_0.30_or_registered_replay_over_15_minutes"
    ] = (
        "trigger_only_if_actual_fit_search_share_at_least_0.30_or_registered_replay_over_15_minutes"
    )
    claim_ceiling: Literal[
        "lowering_and_geometry_validation_feasibility_only_not_replay_policy_or_savings_evidence"
    ] = (
        "lowering_and_geometry_validation_feasibility_only_not_replay_policy_or_savings_evidence"
    )

    @model_validator(mode="after")
    def require_reconciled_result_and_identity(self) -> Self:
        if tuple(item.regime for item in self.streams) != tuple(TemporalRegime):
            raise ValueError("pilot must contain one stream in the frozen regime order")
        totals = {
            "event_count": sum(item.event_count for item in self.streams),
            "batch_count": sum(item.batch_count for item in self.streams),
            "part_count": sum(item.part_count for item in self.streams),
            "projection_count": sum(item.projection_count for item in self.streams),
            "exact_geometry_query_count": sum(
                item.exact_geometry_query_count for item in self.streams
            ),
            "invalid_geometry_count": sum(item.invalid_geometry_count for item in self.streams),
        }
        if any(getattr(self, name) != value for name, value in totals.items()):
            raise ValueError("pilot aggregate counts do not reconcile")
        expected_seconds = round(sum(item.total_seconds for item in self.streams), 6)
        if abs(self.measured_pilot_seconds - expected_seconds) > 1e-6:
            raise ValueError("pilot aggregate timing does not reconcile")
        if self.collision_backend_triggered:
            raise ValueError("validation-only geometry cannot trigger a collision backend")
        payload = self.model_dump(mode="json", exclude={"result_id", "content_sha256"})
        digest = hashlib.sha256(_canonical_bytes(payload, pretty=False)).hexdigest()
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("pilot result content hash mismatch")
        if self.result_id != f"yfm6p-{digest[:24]}":
            raise ValueError("pilot result ID does not match content hash")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self, pretty=True)


def _validate_geometry(stream: TemporalStreamManifest, report) -> tuple[int, int, float]:  # type: ignore[no-untyped-def]
    del stream
    query_count = 0
    invalid_count = 0
    started = perf_counter()
    for batch in report.batches:
        for part in batch.problem.parts:
            polygon = Polygon(part.shape)
            valid = polygon.is_valid
            query_count += 1
            positive_area = polygon.area > 0
            query_count += 1
            finite_bounds = all(math.isfinite(value) for value in polygon.bounds)
            query_count += 1
            if not (valid and positive_area and finite_bounds):
                invalid_count += 1
    elapsed = round(perf_counter() - started, 6)
    return query_count, invalid_count, elapsed


def run_lowering_pilot(
    contract: TemporalBenchmarkContract,
    population: TemporalPopulationManifest,
    streams: dict[str, TemporalStreamManifest],
    catalog: CatalogSnapshot,
) -> M6LoweringPilotResult:
    """Profile one preregistered evaluation stream per regime without selecting actions."""

    if (
        population.contract_id != contract.contract_id
        or population.contract_sha256 != contract.content_sha256
    ):
        raise PilotEvidenceError("pilot population does not bind the supplied M6 contract")
    pilot_seed = contract.common_seeds[2]
    records = tuple(
        next(
            record
            for record in population.streams
            if record.regime is regime
            and record.seed == pilot_seed
            and record.partition is TemporalPartition.EVALUATION
        )
        for regime in TemporalRegime
    )
    measurements: list[PilotStreamMeasurement] = []
    for record in records:
        try:
            stream = streams[record.stream_id]
        except KeyError as error:
            raise PilotEvidenceError(
                f"pilot stream {record.stream_id} is missing from supplied streams"
            ) from error
        started = perf_counter()
        report = lower_stream(contract, stream, catalog)
        lowering_seconds = round(perf_counter() - started, 6)
        query_count, invalid_count, geometry_seconds = _validate_geometry(stream, report)
        measurements.append(
            PilotStreamMeasurement(
                stream_id=stream.stream_id,
                regime=stream.regime,
                seed=stream.seed,
                partition=stream.partition,
                event_count=report.event_count,
                batch_count=report.batch_count,
                part_count=report.part_count,
                projection_count=sum(len(batch.projections) for batch in report.batches),
                exact_geometry_query_count=query_count,
                invalid_geometry_count=invalid_count,
                lowering_seconds=lowering_seconds,
                geometry_validation_seconds=geometry_seconds,
                total_seconds=round(lowering_seconds + geometry_seconds, 6),
            )
        )
    frozen = tuple(measurements)
    measured_seconds = round(sum(item.total_seconds for item in frozen), 6)
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m6-lowering-pilot.v1",
        "contract_id": contract.contract_id,
        "contract_sha256": contract.content_sha256,
        "population_id": population.population_id,
        "population_sha256": population.content_sha256,
        "shapely_version": shapely.__version__,
        "streams": [item.model_dump(mode="json") for item in frozen],
        "event_count": sum(item.event_count for item in frozen),
        "batch_count": sum(item.batch_count for item in frozen),
        "part_count": sum(item.part_count for item in frozen),
        "projection_count": sum(item.projection_count for item in frozen),
        "exact_geometry_query_count": sum(
            item.exact_geometry_query_count for item in frozen
        ),
        "invalid_geometry_count": sum(item.invalid_geometry_count for item in frozen),
        "exact_fit_search_call_count": 0,
        "exact_fit_search_runtime_share": 0.0,
        "measured_pilot_seconds": measured_seconds,
        "projected_full_population_minutes": float(
            round(measured_seconds * population.stream_count / len(frozen) / 60, 6)
        ),
        "collision_backend_triggered": False,
        "collision_backend_decision": "defer_until_repeated_fit_search_pilot",
        "collision_backend_rule": (
            "trigger_only_if_actual_fit_search_share_at_least_0.30_or_registered_replay_over_"
            "15_minutes"
        ),
        "claim_ceiling": (
            "lowering_and_geometry_validation_feasibility_only_not_replay_policy_or_savings_"
            "evidence"
        ),
    }
    digest = hashlib.sha256(_canonical_bytes(semantic, pretty=False)).hexdigest()
    return M6LoweringPilotResult.model_validate_json(
        json.dumps(
            {
                **semantic,
                "result_id": f"yfm6p-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            },
            allow_nan=False,
        ),
        strict=True,
    )


def publish_pilot_result(output_directory: Path, result: M6LoweringPilotResult) -> Path:
    """Publish one measured pilot idempotently without replacing divergent bytes."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"m6-lowering-pilot-{result.result_id}.json"
    payload = result.canonical_bytes()
    if path.exists() or path.is_symlink():
        try:
            metadata = path.lstat()
        except OSError as error:
            raise PilotEvidenceError("M6 pilot artifact could not be inspected") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PilotEvidenceError("M6 pilot artifact must be regular and not a symlink")
        if path.read_bytes() != payload:
            raise PilotEvidenceError("M6 pilot artifact is immutable and differs")
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
