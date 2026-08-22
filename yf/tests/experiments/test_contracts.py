import copy
import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import StrictInt

from yieldforge.experiments.contracts import (
    ExperimentContractError,
    FrozenExperimentModel,
    M0ExperimentContract,
    PureGeometryCalibrationProtocol,
    PureGeometryConfirmationProtocol,
    build_geometry_confirmation_protocol,
    canonical_pretty_json_bytes,
    load_frozen_json,
    rank_task_ids,
    semantic_sha256,
    validate_experiment_bundle,
)

YF_ROOT = Path(__file__).parents[2]
M0_CONTRACT_PATH = YF_ROOT / "experiments" / "m0-contract-v1.json"
GEOMETRY_PROTOCOL_PATH = YF_ROOT / "experiments" / "pure-geometry-calibration-v1.json"
GEOMETRY_CONFIRMATION_PATH = YF_ROOT / "experiments" / "pure-geometry-confirmation-v2.json"
CATALOG_PATH = YF_ROOT / "datasets" / "catalogs" / "lectra-7030786-v1.1" / "lectra-catalog.json"
CATALOG_MANIFEST_PATH = CATALOG_PATH.with_name("catalog-manifest.json")


class TinyContract(FrozenExperimentModel):
    schema_version: Literal["tiny.v1"] = "tiny.v1"
    value: StrictInt


def test_frozen_json_round_trips_only_canonical_bytes(tmp_path: Path) -> None:
    contract = TinyContract(value=7)
    path = tmp_path / "tiny.json"
    path.write_bytes(canonical_pretty_json_bytes(contract))

    restored = load_frozen_json(path, TinyContract)

    assert restored == contract
    assert canonical_pretty_json_bytes(restored) == path.read_bytes()


def test_frozen_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"tiny.v1","value":7,"value":8}\n')

    with pytest.raises(ExperimentContractError, match="duplicate JSON key: value"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_noncanonical_encoding(tmp_path: Path) -> None:
    path = tmp_path / "compact.json"
    path.write_text('{"schema_version":"tiny.v1","value":7}\n')

    with pytest.raises(ExperimentContractError, match="canonical JSON encoding"):
        load_frozen_json(path, TinyContract)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "tiny.v1", "value": 7, "unexpected": True},
        {"schema_version": "wrong.v1", "value": 7},
        {"schema_version": "tiny.v1", "value": 7.0},
    ],
)
def test_frozen_json_rejects_invalid_contracts(tmp_path: Path, payload: dict[str, object]) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ExperimentContractError, match="contract validation failed"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_nonfinite_constants(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{\n  "schema_version": "tiny.v1",\n  "value": NaN\n}\n')

    with pytest.raises(ExperimentContractError, match="nonfinite JSON constant: NaN"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(canonical_pretty_json_bytes(TinyContract(value=7)))
    path = tmp_path / "link.json"
    path.symlink_to(target)

    with pytest.raises(ExperimentContractError, match="regular file and not a symlink"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (1024 * 1024 + 1))

    with pytest.raises(ExperimentContractError, match="exceeds 1048576 bytes"):
        load_frozen_json(path, TinyContract)


def _committed_m0_payload() -> dict[str, object]:
    return json.loads(M0_CONTRACT_PATH.read_text())


def _set_nested(payload: dict[str, object], path: tuple[str, ...], value: object) -> None:
    target = payload
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value


def _reidentify_m0(payload: dict[str, object]) -> None:
    digest = semantic_sha256(payload, excluded_fields={"contract_id", "content_sha256"})
    payload["content_sha256"] = f"sha256:{digest}"
    payload["contract_id"] = f"yfm0-{digest[:24]}"


def test_committed_m0_contract_is_canonical_and_content_addressed() -> None:
    contract = load_frozen_json(M0_CONTRACT_PATH, M0ExperimentContract)

    assert contract.status == "frozen_pending_geometry_calibration"
    assert contract.primary_outcome.name == "oracle_savings"
    assert contract.decision_gates.green.minimum_oracle_savings_percent == 2.5
    assert contract.content_sha256[7:31] == contract.contract_id[5:]


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("primary_outcome", "net_cost_formula"), "full_sheet_purchases"),
        (("cost_accounting", "purchase_accrual"), "when_area_is_consumed"),
        (("terminal_inventory", "primary_treatment"), "bounded_continuation_credit"),
        (
            ("comparators", "unknown_future_contribution_formula"),
            "(baseline_cost-full_oracle_cost)/baseline_cost",
        ),
        (("event_timing", "released_work_fulfillment"), "may_be_delayed"),
        (("candidate_parity", "shared_candidate_archive_hashes"), False),
        (("remnant_eligibility", "primary", "minimum_area_sheet_fraction"), 0.02),
        (("failure_handling", "maximum_identical_retries"), 2),
        (("statistics", "bootstrap_resamples"), 9999),
        (("decision_gates", "green", "minimum_oracle_savings_percent"), 2.49),
        (("immutability", "threshold_changes_after_evaluation"), "allowed_with_note"),
    ],
)
def test_m0_contract_rejects_reidentified_rule_drift(
    path: tuple[str, ...], replacement: object
) -> None:
    payload = copy.deepcopy(_committed_m0_payload())
    _set_nested(payload, path, replacement)
    _reidentify_m0(payload)

    with pytest.raises(ValueError, match="approved M0 rules"):
        M0ExperimentContract.model_validate_json(json.dumps(payload), strict=True)


def test_m0_contract_rejects_forged_content_identity() -> None:
    payload = _committed_m0_payload()
    payload["content_sha256"] = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="content SHA-256"):
        M0ExperimentContract.model_validate_json(json.dumps(payload), strict=True)


def test_m0_contract_is_deeply_immutable() -> None:
    contract = load_frozen_json(M0_CONTRACT_PATH, M0ExperimentContract)

    with pytest.raises(Exception, match="frozen"):
        contract.decision_gates.green.minimum_oracle_savings_percent = 2.0  # type: ignore[misc]


def _committed_geometry_payload() -> dict[str, object]:
    return json.loads(GEOMETRY_PROTOCOL_PATH.read_text())


def _reidentify_geometry(payload: dict[str, object]) -> None:
    digest = semantic_sha256(payload, excluded_fields={"protocol_id", "content_sha256"})
    payload["content_sha256"] = f"sha256:{digest}"
    payload["protocol_id"] = f"yfgp-{digest[:24]}"


def test_task_ranking_is_stable_and_salt_scoped() -> None:
    task_ids = (8, 3, 5, 1)

    first = rank_task_ids(task_ids, salt="split-v1", catalog_sha256="a" * 64)
    second = rank_task_ids(reversed(task_ids), salt="split-v1", catalog_sha256="a" * 64)
    alternate = rank_task_ids(task_ids, salt="repeat-v1", catalog_sha256="a" * 64)

    assert first == second
    assert first != alternate
    assert set(first) == set(task_ids)


def test_committed_geometry_protocol_binds_the_complete_catalog_population() -> None:
    bundle = validate_experiment_bundle(
        m0_path=M0_CONTRACT_PATH,
        geometry_path=GEOMETRY_PROTOCOL_PATH,
        catalog_path=CATALOG_PATH,
        catalog_manifest_path=CATALOG_MANIFEST_PATH,
    )

    protocol = bundle.geometry
    assert protocol.status == "calibration_pending"
    assert protocol.confirmation_enabled is False
    assert protocol.budget.selected_seconds_per_seed is None
    assert len(protocol.population.eligible_task_ids) == 254
    assert tuple(item.tasks_index for item in protocol.population.blocked_tasks) == (4365, 25801)
    assert len(protocol.population.flip_bearing_task_ids) == 185
    assert len(protocol.split.calibration_task_ids) == 51
    assert len(protocol.split.evaluation_task_ids) == 203
    assert len(protocol.repeatability.task_ids) == 20
    assert protocol.near_tie.primary_envelope_percent == 0.5
    assert protocol.outcome.primary_denominator == 203
    assert bundle.catalog_sha256 == protocol.references.catalog_artifact_sha256


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("projection", "primary_mode"), "force_flip_x_zero"),
        (("projection", "sensitivity_in_primary"), True),
        (("budget", "ordinary_seeds"), [0, 1, 2, 4]),
        (("budget", "maximum_identical_retries"), 2),
        (("near_tie", "primary_envelope_percent"), 1.0),
        (("candidate_definition", "placement_order_changes_identity"), True),
        (("outcome", "primary_denominator"), 202),
        (("decision_rule", "proceed_minimum_percent"), 59.0),
    ],
)
def test_geometry_protocol_rejects_reidentified_rule_drift(
    path: tuple[str, ...], replacement: object
) -> None:
    payload = copy.deepcopy(_committed_geometry_payload())
    _set_nested(payload, path, replacement)
    _reidentify_geometry(payload)

    with pytest.raises(ValueError, match="approved pure-geometry rules"):
        PureGeometryCalibrationProtocol.model_validate_json(json.dumps(payload), strict=True)


def test_geometry_protocol_cannot_enable_confirmation_before_calibration() -> None:
    payload = copy.deepcopy(_committed_geometry_payload())
    payload["confirmation_enabled"] = True
    _reidentify_geometry(payload)

    with pytest.raises(ValueError, match="Input should be False"):
        PureGeometryCalibrationProtocol.model_validate_json(json.dumps(payload), strict=True)


def test_geometry_protocol_rejects_reidentified_population_omission() -> None:
    payload = copy.deepcopy(_committed_geometry_payload())
    population = payload["population"]
    assert isinstance(population, dict)
    eligible = population["eligible_task_ids"]
    assert isinstance(eligible, list)
    eligible.pop()
    _reidentify_geometry(payload)

    with pytest.raises(ValueError, match="approved pure-geometry rules"):
        PureGeometryCalibrationProtocol.model_validate_json(json.dumps(payload), strict=True)


def test_bundle_rejects_forged_catalog_manifest(tmp_path: Path) -> None:
    manifest = json.loads(CATALOG_MANIFEST_PATH.read_text())
    manifest["artifact"]["sha256"] = "0" * 64
    forged = tmp_path / "catalog-manifest.json"
    forged.write_text(json.dumps(manifest, indent=2) + "\n")

    with pytest.raises(ExperimentContractError, match="catalog artifact SHA-256"):
        validate_experiment_bundle(
            m0_path=M0_CONTRACT_PATH,
            geometry_path=GEOMETRY_PROTOCOL_PATH,
            catalog_path=CATALOG_PATH,
            catalog_manifest_path=forged,
        )


def test_confirmation_protocol_changes_only_calibrated_budget_and_status() -> None:
    calibration = load_frozen_json(GEOMETRY_PROTOCOL_PATH, PureGeometryCalibrationProtocol)

    confirmation = build_geometry_confirmation_protocol(
        calibration,
        result_id="yfgcr-c333f934c363abc0d78082ec",
        result_sha256="sha256:c333f934c363abc0d78082ecdb60d8020ee0be8a08992b9e80e5caf4e349cbec",
        selected_seconds_per_seed=10,
    )

    assert confirmation.status == "confirmation_ready"
    assert confirmation.confirmation_enabled is True
    assert confirmation.budget.selected_seconds_per_seed == 10
    assert confirmation.calibration_result.result_id == "yfgcr-c333f934c363abc0d78082ec"
    assert confirmation.protocol_id == f"yfgp-{confirmation.content_sha256[7:31]}"


def test_confirmation_protocol_rejects_reidentified_frozen_rule_drift() -> None:
    calibration = load_frozen_json(GEOMETRY_PROTOCOL_PATH, PureGeometryCalibrationProtocol)
    confirmation = build_geometry_confirmation_protocol(
        calibration,
        result_id="yfgcr-c333f934c363abc0d78082ec",
        result_sha256="sha256:c333f934c363abc0d78082ecdb60d8020ee0be8a08992b9e80e5caf4e349cbec",
        selected_seconds_per_seed=10,
    )
    payload = confirmation.model_dump(mode="json")
    payload["near_tie"]["primary_envelope_percent"] = 1.0
    _reidentify_geometry(payload)

    with pytest.raises(ValueError, match="approved pure-geometry rules"):
        PureGeometryConfirmationProtocol.model_validate_json(json.dumps(payload), strict=True)


def test_committed_confirmation_protocol_is_canonical_and_enabled() -> None:
    confirmation = load_frozen_json(
        GEOMETRY_CONFIRMATION_PATH,
        PureGeometryConfirmationProtocol,
    )

    assert confirmation.protocol_id == "yfgp-392644d98bb7035fdc218512"
    assert confirmation.confirmation_enabled is True
    assert confirmation.budget.selected_seconds_per_seed == 10
    assert confirmation.calibration_result.result_id == "yfgcr-c333f934c363abc0d78082ec"
