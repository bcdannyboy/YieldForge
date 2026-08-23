from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from yieldforge.temporal_benchmark.contracts import (
    M0_CONTRACT_ID,
    M0_CONTRACT_SHA256,
    SOURCE_CATALOG_SHA256,
    CandidateArchiveRequirement,
    FeasibilityRateManifest,
    TemporalPartition,
    TemporalRegime,
    build_registered_contract,
)


def test_registered_contract_freezes_source_population_partitions_and_budget() -> None:
    contract = build_registered_contract()

    assert contract.m0_contract_id == M0_CONTRACT_ID
    assert contract.m0_contract_sha256 == M0_CONTRACT_SHA256
    assert contract.source_catalog.artifact_sha256 == SOURCE_CATALOG_SHA256
    assert contract.source_catalog.task_count == 256
    assert contract.source_catalog.runnable_task_count == 254
    assert tuple(item.tasks_index for item in contract.blocked_tasks) == (4365, 25801)
    assert contract.regimes == tuple(TemporalRegime)
    assert len(contract.common_seeds) == 8
    assert len(contract.population_cells) == 48
    assert sum(
        cell.partition is TemporalPartition.CALIBRATION for cell in contract.population_cells
    ) == 12
    assert sum(
        cell.partition is TemporalPartition.EVALUATION for cell in contract.population_cells
    ) == 36
    assert {
        cell.seed for cell in contract.population_cells if cell.regime is TemporalRegime.NO_SIGNAL
    } == set(contract.common_seeds)
    assert contract.timing.event_count == 24
    assert contract.timing.compatible_bundle_size == 3
    assert contract.timing.regime_shift_sequence == 12
    assert contract.candidate_requirement == CandidateArchiveRequirement()
    assert contract.rates.provenance == "generated_feasibility_only"
    assert contract.claim_ceiling == (
        "controlled_synthetic_chronology_over_real_catalog_geometry_only_not_factory_demand_"
        "policy_value_savings_physical_or_commercial_evidence"
    )


def test_registered_contract_is_strict_frozen_content_addressed_and_tamper_evident() -> None:
    contract = build_registered_contract()
    payload = contract.model_dump(mode="json")

    assert contract.contract_id == f"yfm6-{contract.content_sha256[7:31]}"
    with pytest.raises(ValidationError, match="frozen"):
        contract.content_sha256 = "sha256:" + "0" * 64  # type: ignore[misc]

    payload["rates"]["purchase_cost_per_area"] = 2e-6  # type: ignore[index]
    with pytest.raises(ValidationError, match="content hash mismatch"):
        type(contract).model_validate_json(json.dumps(payload), strict=True)

    payload = contract.model_dump(mode="json")
    payload["surprise"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(contract).model_validate_json(json.dumps(payload), strict=True)


def test_population_cells_are_common_seed_paired_and_exactly_partitioned() -> None:
    contract = build_registered_contract()
    by_seed = {
        seed: tuple(cell for cell in contract.population_cells if cell.seed == seed)
        for seed in contract.common_seeds
    }

    for seed, cells in by_seed.items():
        assert {cell.regime for cell in cells} == set(TemporalRegime)
        expected = (
            TemporalPartition.CALIBRATION
            if seed in contract.common_seeds[:2]
            else TemporalPartition.EVALUATION
        )
        assert {cell.partition for cell in cells} == {expected}
        assert len({cell.cell_id for cell in cells}) == len(TemporalRegime)

    payload = contract.model_dump(mode="json")
    payload["population_cells"] = payload["population_cells"][:-1]  # type: ignore[index]
    with pytest.raises(ValidationError, match="exactly one cell"):
        type(contract).model_validate_json(json.dumps(payload), strict=True)


def test_rate_and_candidate_contracts_reject_invalid_numeric_or_budget_values() -> None:
    with pytest.raises(ValidationError, match="finite number"):
        FeasibilityRateManifest(
            purchase_cost_per_area=math.inf,
            storage_cost_per_area_hour=1e-9,
            return_handling_cost_per_remnant=0.25,
            retrieval_handling_cost_per_remnant=0.25,
            scrap_credit_per_area=1e-7,
        )
    with pytest.raises(ValidationError, match="greater than 0"):
        CandidateArchiveRequirement(seconds_per_seed=0)
    with pytest.raises(ValidationError, match="Input should be 1"):
        CandidateArchiveRequirement(num_workers=2)
