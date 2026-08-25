from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from shapely import Polygon, box

from tests.baseline.test_replay import _binding, _m0, _problem, _verified
from yieldforge.baseline.policies import M7PolicyName, policy_identity
from yieldforge.baseline.replay import (
    M7ReplayRuntime,
    build_m7_replay_input,
    initial_m7_cursor,
)
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.visibility import FullRealizedVisibility
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
    collision_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ] = "shapely_authoritative",
    jagua_executable: Path | None = None,
    jagua_differential_audit: bool = False,
    event_count: int = 2,
    release_hour_step: Literal[0, 1] = 1,
) -> M7ReplayRuntime:
    if event_count < 2:
        raise ValueError("M8 two-problem fixture requires at least two events")
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
            *(
                _binding(
                    second,
                    sequence=sequence,
                    released_at=started + timedelta(hours=sequence * release_hour_step),
                )
                for sequence in range(1, event_count)
            ),
        ),
        horizon_end=started + timedelta(hours=event_count),
        collision_backend=collision_backend,
        jagua_container_guard=(
            1.0
            if collision_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
            else None
        ),
    )
    return M7ReplayRuntime(
        replay_input=replay_input,
        runtime_candidates=runtime_candidates,
        rules=rule_set_from_m0(_m0().remnant_eligibility),
        jagua_executable=jagua_executable,
        jagua_differential_audit=jagua_differential_audit,
    )


@dataclass(frozen=True)
class ExhaustiveCertificateCase:
    """One deterministic point in the bounded M8 semantic differential matrix."""

    case_id: str
    request: M8OracleRequest
    policy: M7PolicyName
    material_relation: Literal["none", "match", "mismatch"]
    first_width: float
    second_width: float
    future_fit: bool
    equal_costs: bool
    same_time: bool


@dataclass(frozen=True)
class _CertificateScenario:
    slug: str
    first_width: float
    second_width: float
    event_count: Literal[2, 3, 4]
    inventory_count: Literal[0, 1, 2]
    material_relation: Literal["none", "match", "mismatch"]
    rate_kind: Literal["ordinary", "zero", "high_retrieval"]
    future_fit: bool
    equal_costs: bool
    same_time: bool


def _finite_certificate_scenarios() -> tuple[_CertificateScenario, ...]:
    return (
        _CertificateScenario(
            slug="zero-fit-equal-same-two",
            first_width=6.0,
            second_width=4.0,
            event_count=2,
            inventory_count=0,
            material_relation="none",
            rate_kind="ordinary",
            future_fit=True,
            equal_costs=True,
            same_time=True,
        ),
        _CertificateScenario(
            slug="zero-no-fit-equal-separated-two",
            first_width=9.0,
            second_width=4.0,
            event_count=2,
            inventory_count=0,
            material_relation="none",
            rate_kind="ordinary",
            future_fit=False,
            equal_costs=True,
            same_time=False,
        ),
        _CertificateScenario(
            slug="escape-rejoin-equal-separated-four",
            first_width=2.0,
            second_width=4.0,
            event_count=4,
            inventory_count=0,
            material_relation="none",
            rate_kind="ordinary",
            future_fit=True,
            equal_costs=True,
            same_time=False,
        ),
        _CertificateScenario(
            slug="one-match-fit-unequal-separated-three",
            first_width=4.0,
            second_width=4.0,
            event_count=3,
            inventory_count=1,
            material_relation="match",
            rate_kind="ordinary",
            future_fit=True,
            equal_costs=False,
            same_time=False,
        ),
        _CertificateScenario(
            slug="two-match-fit-unequal-same-three",
            first_width=4.0,
            second_width=4.0,
            event_count=3,
            inventory_count=2,
            material_relation="match",
            rate_kind="ordinary",
            future_fit=True,
            equal_costs=False,
            same_time=True,
        ),
        _CertificateScenario(
            slug="one-mismatch-fit-equal-separated-three",
            first_width=4.0,
            second_width=4.0,
            event_count=3,
            inventory_count=1,
            material_relation="mismatch",
            rate_kind="ordinary",
            future_fit=True,
            equal_costs=True,
            same_time=False,
        ),
        _CertificateScenario(
            slug="one-match-fit-equal-zero-cost-three",
            first_width=4.0,
            second_width=4.0,
            event_count=3,
            inventory_count=1,
            material_relation="match",
            rate_kind="zero",
            future_fit=True,
            equal_costs=True,
            same_time=False,
        ),
        _CertificateScenario(
            slug="one-match-fit-unequal-high-retrieval-three",
            first_width=4.0,
            second_width=4.0,
            event_count=3,
            inventory_count=1,
            material_relation="match",
            rate_kind="high_retrieval",
            future_fit=True,
            equal_costs=False,
            same_time=True,
        ),
        _CertificateScenario(
            slug="one-match-no-fit-equal-separated-three",
            first_width=9.0,
            second_width=4.0,
            event_count=3,
            inventory_count=1,
            material_relation="match",
            rate_kind="ordinary",
            future_fit=False,
            equal_costs=True,
            same_time=False,
        ),
    )


def _certificate_rates(kind: str) -> FeasibilityRateManifest | None:
    if kind == "ordinary":
        return None
    if kind == "zero":
        return FeasibilityRateManifest(
            purchase_cost_per_area=0.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=0.0,
            scrap_credit_per_area=0.0,
        )
    if kind == "high_retrieval":
        return FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        )
    raise ValueError("unregistered finite certificate rate kind")


def exhaustive_certificate_cases() -> tuple[ExhaustiveCertificateCase, ...]:
    """Return all 5 policies crossed with 9 bounded registered semantic scenarios."""

    cases: list[ExhaustiveCertificateCase] = []
    for policy in M7PolicyName:
        for scenario in _finite_certificate_scenarios():
            runtime = two_problem_runtime(
                first_width=scenario.first_width,
                second_width=scenario.second_width,
                policy=policy,
                rates=_certificate_rates(scenario.rate_kind),
                event_count=scenario.event_count,
                release_hour_step=0 if scenario.same_time else 1,
            )
            cursor = initial_m7_cursor(runtime.replay_input)
            material = runtime.replay_input.instances[0].material
            if scenario.material_relation == "mismatch":
                material = MaterialIdentity(
                    **(
                        material.model_dump(mode="python")
                        | {"grade": "m8-exhaustive-mismatch"}
                    )
                )
            items = tuple(
                sorted(
                    (
                        inventory_item(
                            box(0.0, 0.0, 4.0, 10.0),
                            material=material,
                            token=f"{policy.value}-{scenario.slug}-{index}",
                        )
                        for index in range(scenario.inventory_count)
                    ),
                    key=lambda item: item.remnant.remnant_id,
                )
            )
            cursor = replace(cursor, inventory=items)
            cases.append(
                ExhaustiveCertificateCase(
                    case_id=f"{policy.value}-{scenario.slug}",
                    request=M8OracleRequest(
                        runtime=runtime,
                        cursor=cursor,
                        visibility=FullRealizedVisibility(runtime.replay_input.instances),
                    ),
                    policy=policy,
                    material_relation=scenario.material_relation,
                    first_width=scenario.first_width,
                    second_width=scenario.second_width,
                    future_fit=scenario.future_fit,
                    equal_costs=scenario.equal_costs,
                    same_time=scenario.same_time,
                )
            )
    return tuple(cases)


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
