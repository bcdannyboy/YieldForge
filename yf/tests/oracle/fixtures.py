from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shapely import Polygon

from tests.baseline.test_replay import _binding, _m0, _problem, _verified
from yieldforge.baseline.policies import M7PolicyName, policy_identity
from yieldforge.baseline.replay import M7ReplayRuntime, build_m7_replay_input
from yieldforge.replay.contracts import InventoryItem
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import (
    MaterialIdentity,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    canonical_polygon_record,
    derive_remnant_id,
)
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest


def two_problem_runtime(
    *,
    first_width: float,
    second_width: float,
    policy: M7PolicyName = M7PolicyName.AGE_REGULARITY,
    rates: FeasibilityRateManifest | None = None,
) -> M7ReplayRuntime:
    first = _problem(part_width=first_width)
    second = _problem(part_width=second_width)
    first_verified = _verified(first, candidate_ids=("candidate-one", "candidate-two"))
    same_problem = first.problem_id == second.problem_id
    if same_problem:
        second = first
        second_verified = first_verified
        problems = (first,)
        candidate_sets = (first_verified.evidence,)
        runtime_candidates = {first.problem_id: first_verified}
    else:
        second_verified = _verified(second)
        problems = (first, second)
        candidate_sets = (first_verified.evidence, second_verified.evidence)
        runtime_candidates = {
            first.problem_id: first_verified,
            second.problem_id: second_verified,
        }
    started = datetime(2026, 1, 1, tzinfo=UTC)
    replay_input = build_m7_replay_input(
        m0_contract_id=_m0().contract_id,
        m0_contract_sha256=_m0().content_sha256,
        problem_index_id="yfm7i-" + "4" * 24,
        problem_index_sha256="sha256:" + "5" * 64,
        m6_contract_id="yfm6-" + "6" * 24,
        m6_contract_sha256="sha256:" + "7" * 64,
        m6_population_id="yftp-" + "8" * 24,
        m6_population_sha256="sha256:" + "9" * 64,
        policy=policy_identity(policy),
        rates=rates
        or FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.01,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        ),
        fit_config=RemnantFitConfig(),
        problems=problems,
        candidate_sets=candidate_sets,
        instances=(
            _binding(first, sequence=0, released_at=started),
            _binding(second, sequence=1, released_at=started + timedelta(hours=1)),
        ),
        horizon_end=started + timedelta(hours=2),
    )
    return M7ReplayRuntime(
        replay_input=replay_input,
        runtime_candidates=runtime_candidates,
        rules=rule_set_from_m0(_m0().remnant_eligibility),
    )


def inventory_item(
    polygon: Polygon,
    *,
    material: MaterialIdentity,
    token: str,
    entered_at: datetime | None = None,
) -> InventoryItem:
    geometry = canonical_polygon_record(polygon)
    lineage = RemnantLineage.root(
        root_stock_id=f"m8-certificate-{token}",
        source_candidate_id=f"m8-certificate-{token}",
        source_component_sha256=geometry.polygon_sha256,
    )
    remnant = RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, material),
        geometry=geometry,
        material=material,
        root_sheet_area=max(100.0, float(polygon.area)),
        root_sheet_short_side=10.0,
        lineage=lineage,
    )
    return InventoryItem(
        remnant=remnant,
        entered_at=entered_at or datetime(2025, 12, 31, tzinfo=UTC),
    )
