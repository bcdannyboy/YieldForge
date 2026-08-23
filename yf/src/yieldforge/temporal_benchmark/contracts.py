"""Strict content-addressed contracts for the M6 temporal benchmark."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

M0_CONTRACT_ID = "yfm0-29b7efe8ac2a0a9995c4f907"
M0_CONTRACT_SHA256 = "sha256:29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f"
SOURCE_CATALOG_SHA256 = "0e5c3d8aa39846fc69a1c662d01f0a0a9a1761f5d7ce0fbb10efdcf759fc55ad"
SOURCE_CATALOG_MANIFEST_SHA256 = (
    "95a404847a112b47ae27bd6269bc5e3e797c83848cabea2ce3b155004e82976e"
)
SOURCE_CATALOG_LOGICAL_SHA256 = (
    "900dcb5e33308c54db794cccb72f75b8fcf6d11d385fc54d28ccd680dc15ba06"
)
REGISTERED_COMMON_SEEDS = tuple(range(2026082300, 2026082308))


class TemporalContractModel(BaseModel):
    """Immutable strict base for persisted temporal benchmark evidence."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class TemporalRegime(StrEnum):
    """The six controlled temporal worlds registered for M6."""

    NO_SIGNAL = "no_signal"
    EXACT_RECURRENCE = "exact_recurrence"
    FAMILY_SIMILARITY = "family_similarity"
    COMPATIBLE_BUNDLE = "compatible_bundle"
    HIGH_MIX = "high_mix"
    REGIME_SHIFT = "regime_shift"


class TemporalPartition(StrEnum):
    """Immutable experiment use assigned before stream realization."""

    CALIBRATION = "calibration"
    EVALUATION = "evaluation"


class ProvenanceKind(StrEnum):
    SOURCE_OBSERVED = "source_observed"
    DERIVED = "derived"
    GENERATED = "generated"
    ASSUMED = "assumed"


class BenchmarkField(StrEnum):
    GEOMETRY = "geometry"
    COMPOSITION = "composition"
    STOCK = "stock"
    CHRONOLOGY = "chronology"
    MATERIAL = "material"
    ECONOMICS = "economics"
    REGIME_LABEL = "regime_label"
    PARTITION = "partition"


class FieldProvenance(TemporalContractModel):
    field: BenchmarkField
    kind: ProvenanceKind
    explanation: StrictStr = Field(min_length=1)


class SourceCatalogIdentity(TemporalContractModel):
    """Pinned full-catalog evidence admitted to the M6 source pool."""

    dataset_id: Literal["lectra-7030786-v1.1"] = "lectra-7030786-v1.1"
    doi: Literal["10.5281/zenodo.7030786"] = "10.5281/zenodo.7030786"
    repository_path: Literal[
        "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"
    ] = "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"
    manifest_path: Literal[
        "datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json"
    ] = "datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json"
    artifact_sha256: Literal[SOURCE_CATALOG_SHA256] = SOURCE_CATALOG_SHA256
    logical_sha256: Literal[SOURCE_CATALOG_LOGICAL_SHA256] = SOURCE_CATALOG_LOGICAL_SHA256
    manifest_sha256: Literal[SOURCE_CATALOG_MANIFEST_SHA256] = SOURCE_CATALOG_MANIFEST_SHA256
    conversion_ruleset_version: Literal["lectra-catalog-rules.v2"] = "lectra-catalog-rules.v2"
    task_count: Literal[256] = 256
    runnable_task_count: Literal[254] = 254
    blocked_task_count: Literal[2] = 2
    part_count: Literal[8358] = 8358
    shape_count: Literal[745] = 745
    coordinate_unit: Literal["source_literal_m^-4_uninterpreted"] = (
        "source_literal_m^-4_uninterpreted"
    )


class BlockedSourceTask(TemporalContractModel):
    tasks_index: StrictInt = Field(ge=0)
    reason_code: Literal["contains_non_s1_constraints"] = "contains_non_s1_constraints"
    treatment: Literal["excluded_and_reported"] = "excluded_and_reported"


class TemporalGeneratorIdentity(TemporalContractModel):
    name: Literal["yieldforge.temporal-benchmark"] = "yieldforge.temporal-benchmark"
    version: Literal["1.0.0"] = "1.0.0"
    algorithm: Literal["python-mt19937-canonical-v1"] = "python-mt19937-canonical-v1"


class TemporalTiming(TemporalContractModel):
    starts_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    interval_minutes: Literal[60] = 60
    event_count: Literal[24] = 24
    compatible_bundle_size: Literal[3] = 3
    regime_shift_sequence: Literal[12] = 12
    batching_rule: Literal[
        "all_and_only_same_timestamp_material_and_source_sheet"
    ] = "all_and_only_same_timestamp_material_and_source_sheet"

    @field_validator("starts_at")
    @classmethod
    def canonicalize_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("benchmark start must be timezone-aware")
        return value.astimezone(UTC)


class FeasibilityRateManifest(TemporalContractModel):
    """Generated scale-explicit rates for pipeline feasibility, not factory economics."""

    schema_version: Literal["yieldforge.m6-feasibility-rates.v1"] = (
        "yieldforge.m6-feasibility-rates.v1"
    )
    provenance: Literal["generated_feasibility_only"] = "generated_feasibility_only"
    cost_unit: Literal["generated_cost_unit"] = "generated_cost_unit"
    area_unit: Literal["source_coordinate_unit_squared"] = "source_coordinate_unit_squared"
    time_unit: Literal["hour"] = "hour"
    purchase_cost_per_area: StrictFloat = Field(ge=0)
    storage_cost_per_area_hour: StrictFloat = Field(ge=0)
    return_handling_cost_per_remnant: StrictFloat = Field(ge=0)
    retrieval_handling_cost_per_remnant: StrictFloat = Field(ge=0)
    scrap_credit_per_area: StrictFloat = Field(ge=0)


class CandidateArchiveRequirement(TemporalContractModel):
    """Frozen ordinary candidate evidence M7 must share across paired policies."""

    solver_name: Literal["spyrrow"] = "spyrrow"
    solver_version: Literal["0.9.0"] = "0.9.0"
    seeds: tuple[Literal[0, 1, 2, 3], ...] = (0, 1, 2, 3)
    seconds_per_seed: StrictInt = Field(default=10, gt=0)
    num_workers: Literal[1] = 1
    early_termination: Literal[False] = False
    min_items_separation: None = None
    archive_requirement: Literal["verified_immutable_shared_across_paired_policies"] = (
        "verified_immutable_shared_across_paired_policies"
    )

    @model_validator(mode="after")
    def require_exact_ordinary_seeds(self) -> Self:
        if self.seeds != (0, 1, 2, 3):
            raise ValueError("candidate seeds must equal the frozen ordinary seed tuple")
        return self


def expected_cell_id(
    regime: TemporalRegime,
    seed: int,
    partition: TemporalPartition,
) -> str:
    payload = json.dumps(
        {"partition": partition.value, "regime": regime.value, "seed": seed},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "yfm6c-" + hashlib.sha256(payload).hexdigest()[:20]


class TemporalPopulationCell(TemporalContractModel):
    cell_id: StrictStr = Field(pattern=r"^yfm6c-[0-9a-f]{20}$")
    regime: TemporalRegime
    seed: StrictInt
    partition: TemporalPartition

    @model_validator(mode="after")
    def require_derived_cell_id(self) -> Self:
        if self.cell_id != expected_cell_id(self.regime, self.seed, self.partition):
            raise ValueError("population cell ID is inconsistent with its registered dimensions")
        return self


def _contract_semantic_sha256(value: BaseModel | dict[str, object]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    payload.pop("contract_id", None)
    payload.pop("content_sha256", None)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


_EXPECTED_PROVENANCE = (
    FieldProvenance(
        field=BenchmarkField.GEOMETRY,
        kind=ProvenanceKind.SOURCE_OBSERVED,
        explanation="Exact polygon coordinates reference the pinned Lectra catalog.",
    ),
    FieldProvenance(
        field=BenchmarkField.COMPOSITION,
        kind=ProvenanceKind.SOURCE_OBSERVED,
        explanation="Each event preserves one source task's complete part composition.",
    ),
    FieldProvenance(
        field=BenchmarkField.STOCK,
        kind=ProvenanceKind.SOURCE_OBSERVED,
        explanation="Sheet type and dimensions are copied from the selected source task.",
    ),
    FieldProvenance(
        field=BenchmarkField.CHRONOLOGY,
        kind=ProvenanceKind.GENERATED,
        explanation="Release timestamps are deterministic generated scenario fields.",
    ),
    FieldProvenance(
        field=BenchmarkField.MATERIAL,
        kind=ProvenanceKind.ASSUMED,
        explanation="Material compatibility is assumed because the source has no material field.",
    ),
    FieldProvenance(
        field=BenchmarkField.ECONOMICS,
        kind=ProvenanceKind.GENERATED,
        explanation="Rates and economic indexes are generated feasibility fields.",
    ),
    FieldProvenance(
        field=BenchmarkField.REGIME_LABEL,
        kind=ProvenanceKind.GENERATED,
        explanation="Regime labels describe generator construction, not source observations.",
    ),
    FieldProvenance(
        field=BenchmarkField.PARTITION,
        kind=ProvenanceKind.GENERATED,
        explanation="Calibration and evaluation membership is preregistered generated metadata.",
    ),
)


class TemporalBenchmarkContract(TemporalContractModel):
    """Complete preregistered input for the M6 stream population."""

    schema_version: Literal["yieldforge.temporal-benchmark-contract.v1"] = (
        "yieldforge.temporal-benchmark-contract.v1"
    )
    contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m0_contract_id: Literal[M0_CONTRACT_ID] = M0_CONTRACT_ID
    m0_contract_sha256: Literal[M0_CONTRACT_SHA256] = M0_CONTRACT_SHA256
    source_catalog: SourceCatalogIdentity
    generator: TemporalGeneratorIdentity
    timing: TemporalTiming
    regimes: tuple[TemporalRegime, ...]
    common_seeds: tuple[StrictInt, ...]
    population_cells: tuple[TemporalPopulationCell, ...]
    blocked_tasks: tuple[BlockedSourceTask, ...]
    projection_mode: Literal["source_as_recorded"] = "source_as_recorded"
    field_provenance: tuple[FieldProvenance, ...]
    rates: FeasibilityRateManifest
    candidate_requirement: CandidateArchiveRequirement
    claim_ceiling: Literal[
        "controlled_synthetic_chronology_over_real_catalog_geometry_only_not_factory_demand_"
        "policy_value_savings_physical_or_commercial_evidence"
    ] = (
        "controlled_synthetic_chronology_over_real_catalog_geometry_only_not_factory_demand_"
        "policy_value_savings_physical_or_commercial_evidence"
    )

    @model_validator(mode="after")
    def require_registered_population_and_identity(self) -> Self:
        if self.regimes != tuple(TemporalRegime):
            raise ValueError("regimes must equal the complete frozen M6 order")
        if self.common_seeds != REGISTERED_COMMON_SEEDS:
            raise ValueError("common seeds must equal the frozen M6 seed tuple")
        expected_dimensions = {
            (regime, seed)
            for regime in TemporalRegime
            for seed in REGISTERED_COMMON_SEEDS
        }
        observed_dimensions = {(cell.regime, cell.seed) for cell in self.population_cells}
        if (
            len(self.population_cells) != len(expected_dimensions)
            or observed_dimensions != expected_dimensions
        ):
            raise ValueError("population requires exactly one cell per regime and common seed")
        for cell in self.population_cells:
            expected_partition = (
                TemporalPartition.CALIBRATION
                if cell.seed in REGISTERED_COMMON_SEEDS[:2]
                else TemporalPartition.EVALUATION
            )
            if cell.partition is not expected_partition:
                raise ValueError("population cell partition differs from the frozen seed split")
        if self.blocked_tasks != (
            BlockedSourceTask(tasks_index=4365),
            BlockedSourceTask(tasks_index=25801),
        ):
            raise ValueError("blocked source tasks must equal the catalog's visible exclusions")
        if self.field_provenance != _EXPECTED_PROVENANCE:
            raise ValueError("field provenance must equal the frozen evidence boundary")
        digest = _contract_semantic_sha256(self)
        expected_hash = f"sha256:{digest}"
        if self.content_sha256 != expected_hash:
            raise ValueError("content hash mismatch")
        if self.contract_id != f"yfm6-{digest[:24]}":
            raise ValueError("contract ID does not match content hash")
        return self


def build_registered_contract() -> TemporalBenchmarkContract:
    """Build the one frozen M6 contract from explicit registered constants."""

    cells = tuple(
        TemporalPopulationCell(
            cell_id=expected_cell_id(
                regime,
                seed,
                (
                    TemporalPartition.CALIBRATION
                    if seed in REGISTERED_COMMON_SEEDS[:2]
                    else TemporalPartition.EVALUATION
                ),
            ),
            regime=regime,
            seed=seed,
            partition=(
                TemporalPartition.CALIBRATION
                if seed in REGISTERED_COMMON_SEEDS[:2]
                else TemporalPartition.EVALUATION
            ),
        )
        for regime in TemporalRegime
        for seed in REGISTERED_COMMON_SEEDS
    )
    source_catalog = SourceCatalogIdentity()
    generator = TemporalGeneratorIdentity()
    timing = TemporalTiming()
    blocked_tasks = (
        BlockedSourceTask(tasks_index=4365),
        BlockedSourceTask(tasks_index=25801),
    )
    rates = FeasibilityRateManifest(
        purchase_cost_per_area=1e-6,
        storage_cost_per_area_hour=1e-9,
        return_handling_cost_per_remnant=0.25,
        retrieval_handling_cost_per_remnant=0.25,
        scrap_credit_per_area=1e-7,
    )
    candidate_requirement = CandidateArchiveRequirement()
    claim_ceiling = (
        "controlled_synthetic_chronology_over_real_catalog_geometry_only_not_factory_demand_"
        "policy_value_savings_physical_or_commercial_evidence"
    )
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.temporal-benchmark-contract.v1",
        "m0_contract_id": M0_CONTRACT_ID,
        "m0_contract_sha256": M0_CONTRACT_SHA256,
        "source_catalog": source_catalog.model_dump(mode="json"),
        "generator": generator.model_dump(mode="json"),
        "timing": timing.model_dump(mode="json"),
        "regimes": [regime.value for regime in TemporalRegime],
        "common_seeds": list(REGISTERED_COMMON_SEEDS),
        "population_cells": [cell.model_dump(mode="json") for cell in cells],
        "blocked_tasks": [item.model_dump(mode="json") for item in blocked_tasks],
        "projection_mode": "source_as_recorded",
        "field_provenance": [item.model_dump(mode="json") for item in _EXPECTED_PROVENANCE],
        "rates": rates.model_dump(mode="json"),
        "candidate_requirement": candidate_requirement.model_dump(mode="json"),
        "claim_ceiling": claim_ceiling,
    }
    digest = _contract_semantic_sha256(semantic)
    return TemporalBenchmarkContract(
        contract_id=f"yfm6-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m0_contract_id=M0_CONTRACT_ID,
        m0_contract_sha256=M0_CONTRACT_SHA256,
        source_catalog=source_catalog,
        generator=generator,
        timing=timing,
        regimes=tuple(TemporalRegime),
        common_seeds=REGISTERED_COMMON_SEEDS,
        population_cells=cells,
        blocked_tasks=blocked_tasks,
        projection_mode="source_as_recorded",
        field_provenance=_EXPECTED_PROVENANCE,
        rates=rates,
        candidate_requirement=candidate_requirement,
        claim_ceiling=claim_ceiling,
    )
