from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import canonical_pretty_json_bytes, semantic_sha256
from yieldforge.experiments.residual_geometry import load_m3_input_pack
from yieldforge.realistic_falsification.contracts import M11ExperimentContract
from yieldforge.realistic_falsification.pack import (
    LECTRA_REFERENCE_AREA,
    LOCO_TARGET_WIDTH_MULTIPLIERS,
    M11_AUDIT_ARMS,
    M11_AUDIT_POSITIONS,
    M11_PROHIBITED_SELECTION_FIELDS,
    M11_REGIMES,
    M11_ROOT_SEED,
    M11EconomicProfile,
    M11Payload,
    M11Population,
    M11Stream,
    PackEvidenceError,
    canonical_pack_artifact_bytes,
    derive_lectra_holdout_partitions,
    generate_m11_pack,
    load_m11_pack_bundle,
    visible_event_positions,
)
from yieldforge.realistic_falsification.sources import load_loco_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "benchmarks/falsification/m11-contract-v1.json"
POPULATION_PATH = REPO_ROOT / "benchmarks/falsification/m11-population-v1.json"
SOURCE_MANIFEST_PATH = REPO_ROOT / "benchmarks/falsification/source-manifest-v1.json"
LOCO_CATALOG_PATH = REPO_ROOT / "datasets/catalogs/loco-2dics-v1/loco-catalog.json"
LECTRA_CATALOG_PATH = REPO_ROOT / "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"
M3_INPUT_PATH = (
    REPO_ROOT / "experiments/results/residual-geometry-input-yfgi-2fe5b848ea643d282c284f90.json"
)


@pytest.fixture(scope="module")
def generated():
    return generate_m11_pack(REPO_ROOT)


def _corpus_streams(population: M11Population, corpus_id: str, *, kind: str | None = None):
    streams = tuple(item for item in population.streams if item.corpus_id == corpus_id)
    if kind is not None:
        streams = tuple(item for item in streams if item.stream_kind == kind)
    return streams


def _rehash(payload: dict[str, object], *, id_field: str, prefix: str) -> None:
    semantic = dict(payload)
    semantic.pop(id_field)
    semantic.pop("content_sha256")
    digest = semantic_sha256(semantic)
    payload[id_field] = f"{prefix}{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"


def test_generation_is_byte_identical_and_committed_artifacts_strict_read_back(generated) -> None:
    second = generate_m11_pack(REPO_ROOT)
    first_bytes = canonical_pack_artifact_bytes(generated)
    second_bytes = canonical_pack_artifact_bytes(second)

    assert first_bytes == second_bytes
    assert CONTRACT_PATH.read_bytes() == first_bytes.contract
    assert POPULATION_PATH.read_bytes() == first_bytes.population
    assert (
        load_m11_pack_bundle(
            repository_root=REPO_ROOT,
            contract_path=CONTRACT_PATH,
            population_path=POPULATION_PATH,
            source_manifest_path=SOURCE_MANIFEST_PATH,
        )
        == generated
    )


def test_exact_population_census_regimes_and_family_disjointness(generated) -> None:
    population = generated.population
    assert M11_ROOT_SEED == 2_026_082_901
    assert M11_REGIMES == ("recurrent", "mixed", "high_mix", "regime_shift")
    assert population.census.registered_id_count == 114
    assert population.census.primary_stream_count == 56
    assert population.census.calibration_stream_count == 16
    assert population.census.confirmation_stream_count == 40
    assert population.census.twin_stream_count == 40
    assert population.census.primary_event_count == 1_344
    assert population.census.twin_event_count == 960
    assert len(population.hard_nulls) == 6
    assert len(population.exact_audits) == 12

    expected = {
        "lectra-m3-m4": ((17, 58), (52, 145)),
        "loco-2dics": ((5, 146), (4, 365)),
    }
    for corpus_id, counts in expected.items():
        partitions = {
            item.partition: item
            for item in population.source_partitions
            if item.corpus_id == corpus_id
        }
        calibration = partitions["calibration"]
        confirmation = partitions["confirmation"]
        assert (calibration.family_count, calibration.source_case_count) == counts[0]
        assert (confirmation.family_count, confirmation.source_case_count) == counts[1]
        assert set(calibration.family_ids).isdisjoint(confirmation.family_ids)
        assert set(calibration.source_family_ids).isdisjoint(confirmation.source_family_ids)

        primary = _corpus_streams(population, corpus_id, kind="primary")
        assert Counter((item.partition, item.regime) for item in primary) == Counter(
            {
                **{("calibration", regime): 2 for regime in M11_REGIMES},
                **{("confirmation", regime): 5 for regime in M11_REGIMES},
            }
        )
        assert all(len(item.events) == 24 for item in primary)

    loco_calibration = next(
        item
        for item in population.source_partitions
        if item.corpus_id == "loco-2dics" and item.partition == "calibration"
    )
    assert set(loco_calibration.source_instances) == {
        "swim",
        "dagli",
        "albano",
        "marques",
        "mao",
    }


def test_regime_shapes_chronology_counts_and_fixed_prefix_visibility(generated) -> None:
    for stream in (item for item in generated.population.streams if item.stream_kind == "primary"):
        payload_counts = Counter(event.payload_id for event in stream.events)
        if stream.regime == "recurrent":
            assert sorted(payload_counts.values()) == [6, 6, 6, 6]
        elif stream.regime == "mixed":
            assert sorted(payload_counts.values()) == [2] * 12
        elif stream.regime == "high_mix":
            assert len(payload_counts) == 24
        else:
            first = {event.family_id for event in stream.events[:12]}
            second = {event.family_id for event in stream.events[12:]}
            assert first.isdisjoint(second)

        assert sorted(Counter(event.customer_id for event in stream.events).values()) == [
            2,
            3,
            4,
            4,
            5,
            6,
        ]
        assert Counter(event.due_hours for event in stream.events) == {
            12: 4,
            24: 8,
            48: 8,
            72: 4,
        }
        if stream.corpus_id == "lectra-m3-m4":
            assert sorted(Counter(event.material_key for event in stream.events).values()) == [
                3,
                3,
                4,
                6,
                8,
            ]

        for position, event in enumerate(stream.events):
            assert event.position == position
            assert (
                datetime.fromisoformat(event.released_at.replace("Z", "+00:00"))
                - datetime.fromisoformat(event.known_at.replace("Z", "+00:00"))
            ).total_seconds() == 86_400
            visible = visible_event_positions(stream, event.released_at)
            assert visible == tuple(range(len(visible)))


def test_loco_raw_geometry_instance_isolation_quantity_and_bbox_witness(generated) -> None:
    catalog = load_loco_catalog(LOCO_CATALOG_PATH)
    by_id = {item.item_id: item for item in catalog.items}
    payloads = {
        item.payload_id: item
        for item in generated.population.payloads
        if item.source_kind == "loco_2dics"
    }
    assert LOCO_TARGET_WIDTH_MULTIPLIERS == (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0)

    for payload in payloads.values():
        assert len(payload.geometry_references) == 8
        assert len({item.reference_id for item in payload.geometry_references}) == 8
        instances = {item.instance_name for item in payload.geometry_references}
        assert len(instances) == 1
        for reference, quantity in zip(
            payload.geometry_references, payload.quantities, strict=True
        ):
            source = by_id[reference.reference_id]
            assert reference.content_sha256 == source.content_sha256
            assert reference.geometry_sha256 == source.geometry.polygon_sha256
            assert reference.source_item_index == source.source_item_index
            assert quantity == 1 + (source.source_demand - 1) // 25

        fallback = payload.fallback_stock
        assert fallback is not None
        assert len(fallback.candidate_widths) == 8
        assert fallback.width == fallback.candidate_widths[fallback.selected_width_index]
        for placement in fallback.placements:
            assert placement.x >= 0 and placement.y >= 0
            assert placement.x + placement.width <= fallback.width + 1e-9
            assert placement.y + placement.height <= fallback.height + 1e-9
        for left_index, left in enumerate(fallback.placements):
            for right in fallback.placements[left_index + 1 :]:
                separated = (
                    left.x + left.width <= right.x + 1e-9
                    or right.x + right.width <= left.x + 1e-9
                    or left.y + left.height <= right.y + 1e-9
                    or right.y + right.height <= left.y + 1e-9
                )
                assert separated


def test_sampling_is_invariant_to_prohibited_source_outcome_fields() -> None:
    m3 = load_m3_input_pack(M3_INPUT_PATH)
    catalog = json.loads(LECTRA_CATALOG_PATH.read_bytes())
    baseline = derive_lectra_holdout_partitions(m3.expected_task_ids, catalog)
    mutated = copy.deepcopy(catalog)
    for task in mutated["tasks"]:
        task["sheet_width"] = -999_999.0
        task["sheet_length"] = -999_999.0
        task["efficiency"] = -999_999.0
        task["duration"] = -999_999
        task["m11_score"] = 1e99
    for row in mutated["derived_geometry"]:
        row["area"] = -999_999.0
    assert derive_lectra_holdout_partitions(m3.expected_task_ids, mutated) == baseline
    assert M11_PROHIBITED_SELECTION_FIELDS == (
        "candidate_width",
        "candidate_density",
        "residual_comparisons",
        "m4_fit_outcomes",
        "later_scores",
    )


def test_twins_are_payload_derangements_with_unique_materials_and_no_signal(generated) -> None:
    streams = {item.stream_id: item for item in generated.population.streams}
    for twin in (item for item in streams.values() if item.stream_kind == "shuffled_twin"):
        source = streams[twin.source_stream_id]
        assert twin.partition == "confirmation"
        assert Counter(event.payload_id for event in twin.events) == Counter(
            event.payload_id for event in source.events
        )
        assert all(
            twin.events[position].payload_id != source.events[position].payload_id
            for position in range(24)
        )
        assert [event.released_at for event in twin.events] == [
            event.released_at for event in source.events
        ]
        assert [event.known_at for event in twin.events] == [
            event.known_at for event in source.events
        ]
        assert [event.due_at for event in twin.events] == [event.due_at for event in source.events]
        assert [event.customer_id for event in twin.events] == [
            event.customer_id for event in source.events
        ]
        assert len({event.material_key for event in twin.events}) == 24
        assert twin.no_signal_control is True


def test_hard_nulls_and_exact_audits_are_frozen_by_construction(generated) -> None:
    assert M11_AUDIT_POSITIONS == (
        (0, 1, 2),
        (3, 4, 5),
        (6, 7, 8),
        (9, 10, 11),
        (12, 13, 14),
        (21, 22, 23),
    )
    assert M11_AUDIT_ARMS == ("central", "central", "adverse", "adverse", "null", "null")
    for corpus_id in ("lectra-m3-m4", "loco-2dics"):
        nulls = tuple(
            item for item in generated.population.hard_nulls if item.corpus_id == corpus_id
        )
        audits = tuple(
            item for item in generated.population.exact_audits if item.corpus_id == corpus_id
        )
        assert [item.null_kind for item in nulls] == [
            "single_action",
            "unique_materials_single_action",
            "all_work_known_single_action",
        ]
        assert all(item.baseline_action_count == item.future_action_count == 1 for item in nulls)
        assert all(item.expected_savings_percent == 0.0 for item in nulls)
        assert [item.event_positions for item in audits] == list(M11_AUDIT_POSITIONS)
        assert [item.economic_arm for item in audits] == list(M11_AUDIT_ARMS)
        assert all(len(item.event_ids) == 3 for item in audits)


def test_profiles_reference_areas_and_all_economics_are_assumed(generated) -> None:
    profiles = {item.arm: item for item in generated.population.economic_profiles}
    assert tuple(profiles) == ("optimistic", "central", "adverse")
    assert [profiles[key].scrap_and_terminal_credit for key in profiles] == [0.0, 10.0, 25.0]
    assert [profiles[key].return_handling for key in profiles] == [0.0, 0.25, 1.0]
    assert [profiles[key].retrieval_handling for key in profiles] == [0.0, 0.25, 1.0]
    assert [profiles[key].storage_per_reference_area_30_days for key in profiles] == [
        0.0,
        0.5,
        2.0,
    ]
    assert all(item.virgin_cost_per_reference_area == 100.0 for item in profiles.values())
    assert all(item.provenance == "assumed" for item in profiles.values())
    lectra = next(
        item for item in generated.population.reference_areas if item.corpus_id == "lectra-m3-m4"
    )
    loco = next(
        item for item in generated.population.reference_areas if item.corpus_id == "loco-2dics"
    )
    assert LECTRA_REFERENCE_AREA == 1_296_449_632.0
    assert lectra.by_material == tuple(
        (f"lectra-material-{index}", LECTRA_REFERENCE_AREA) for index in range(1, 6)
    )
    assert len(loco.by_material) == 15
    assert all(value > 0 for _, value in loco.by_material)


def test_closed_models_semantic_identity_and_task1_task2_cross_binding(generated) -> None:
    population = generated.population
    with pytest.raises(ValidationError):
        M11Population.model_validate({**population.model_dump(mode="python"), "extra": True})
    with pytest.raises(ValidationError):
        M11Population.model_validate(
            {**population.model_dump(mode="python"), "root_seed": M11_ROOT_SEED + 1}, strict=True
        )

    payload = population.model_dump(mode="json")
    payload["root_seed"] += 1
    semantic = dict(payload)
    semantic.pop("population_id")
    semantic.pop("content_sha256")
    mutated_digest = semantic_sha256(semantic)
    assert mutated_digest != population.content_sha256.removeprefix("sha256:")

    source_manifest = json.loads(SOURCE_MANIFEST_PATH.read_bytes())
    assert population.source_catalog_id == "yflc-14dd63c1d1b690236b8f6393"
    assert population.source_catalog_manifest_id == "yflcm-9f5bbd1ee54e18e5fa1cb86f"
    assert population.source_manifest_id == "yfm11sm-54426d56dcccc07b667da56f"
    assert population.source_manifest_sha256 == source_manifest["content_sha256"]
    assert generated.contract.contract_id == population.contract_id
    assert generated.contract.content_sha256 == population.contract_content_sha256
    assert tuple(parent.role for parent in generated.contract.parents) == (
        "m0_contract",
        "m10_verdict",
    )
    assert tuple(corpus.source.lineage_kind for corpus in generated.contract.corpora) == (
        "lectra",
        "loco_2dics",
    )


def test_rehashed_cross_object_fallback_and_frozen_profile_forgeries_fail(generated) -> None:
    loco_payload = next(
        item for item in generated.population.payloads if item.source_kind == "loco_2dics"
    ).model_dump(mode="python")
    fallback = loco_payload["fallback_stock"]
    fallback["placements"][0]["geometry_reference_id"] = "unregistered-source-item"
    _rehash(fallback, id_field="stock_id", prefix="yfm11fb-")
    loco_payload["candidate_references"][0]["candidate_id"] = fallback["stock_id"]
    loco_payload["candidate_references"][0]["content_sha256"] = fallback["content_sha256"]
    _rehash(loco_payload, id_field="payload_id", prefix="yfm11pl-")
    with pytest.raises(ValidationError, match="placements do not match"):
        M11Payload.model_validate(loco_payload, strict=True)

    with pytest.raises(ValidationError, match="frozen economic profile"):
        M11EconomicProfile(
            arm="central",
            virgin_cost_per_reference_area=100.0,
            scrap_and_terminal_credit=11.0,
            return_handling=0.25,
            retrieval_handling=0.25,
            storage_per_reference_area_30_days=0.5,
        )

    stream = next(
        item for item in generated.population.streams if item.stream_kind == "primary"
    ).model_dump(mode="python")
    for index, event in enumerate(stream["events"]):
        event["customer_id"] = f"forged-customer-{index}"
        _rehash(event, id_field="event_id", prefix="yfm11e-")
    _rehash(stream, id_field="stream_id", prefix="yfm11st-")
    with pytest.raises(ValidationError, match="customer multiplicities"):
        M11Stream.model_validate(stream, strict=True)


def test_rehashed_contract_source_forgery_cannot_cross_bind_task2(
    tmp_path: Path, generated
) -> None:
    contract_payload = generated.contract.model_dump(mode="json")
    source = contract_payload["corpora"][0]["source"]
    source["upstream_sha256"] = "sha256:" + "0" * 64
    _rehash(source, id_field="source_id", prefix="yfm11s-")
    _rehash(contract_payload, id_field="contract_id", prefix="yfm11c-")
    contract = M11ExperimentContract.model_validate_json(
        json.dumps(contract_payload, allow_nan=False), strict=True
    )

    population_payload = generated.population.model_dump(mode="json")
    population_payload["contract_id"] = contract.contract_id
    population_payload["contract_content_sha256"] = contract.content_sha256
    _rehash(population_payload, id_field="population_id", prefix="yfm11pop-")
    population = M11Population.model_validate_json(
        json.dumps(population_payload, allow_nan=False), strict=True
    )

    contract_path = tmp_path / "contract.json"
    population_path = tmp_path / "population.json"
    contract_path.write_bytes(canonical_pretty_json_bytes(contract))
    population_path.write_bytes(canonical_pretty_json_bytes(population))
    with pytest.raises(PackEvidenceError, match="Task 2 source"):
        load_m11_pack_bundle(
            repository_root=REPO_ROOT,
            contract_path=contract_path,
            population_path=population_path,
            source_manifest_path=SOURCE_MANIFEST_PATH,
        )


def test_noncanonical_or_crossed_artifacts_fail_closed(tmp_path: Path, generated) -> None:
    canonical = canonical_pack_artifact_bytes(generated)
    contract_path = tmp_path / "contract.json"
    population_path = tmp_path / "population.json"
    contract_path.write_bytes(canonical.contract)
    population_path.write_bytes(canonical.population.replace(b"\n", b"", 1))
    with pytest.raises(PackEvidenceError, match="canonical"):
        load_m11_pack_bundle(
            repository_root=REPO_ROOT,
            contract_path=contract_path,
            population_path=population_path,
            source_manifest_path=SOURCE_MANIFEST_PATH,
        )


def test_task3_contracts_are_public_package_exports() -> None:
    import yieldforge.realistic_falsification as public

    assert public.M11Population is M11Population
    assert public.generate_m11_pack is generate_m11_pack
    assert public.load_m11_pack_bundle is load_m11_pack_bundle
