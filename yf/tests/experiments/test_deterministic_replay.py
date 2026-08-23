from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest

from yieldforge.experiments.contracts import M0ExperimentContract
from yieldforge.experiments.deterministic_replay import (
    M5EvidenceError,
    evaluate_m5_replay,
    load_m5_replay_input,
    load_m5_replay_result,
    prepare_m5_replay_input,
    publish_m5_replay_input,
    publish_m5_replay_result,
)
from yieldforge.experiments.remnant_reuse import load_m4_input_pack, load_m4_result

YF_ROOT = Path(__file__).parents[2]
M0_PATH = YF_ROOT / "experiments" / "m0-contract-v1.json"
M4_INPUT_PATH = (
    YF_ROOT
    / "experiments"
    / "results"
    / "remnant-reuse-input-yfri-26460ffca19eebfc9e479d01.json.gz"
)
M4_RESULT_PATH = (
    YF_ROOT / "experiments" / "results" / "remnant-reuse-result-yfrr-b8b1578fc5e0225f00c4386e.json"
)


@lru_cache
def _sources():  # type: ignore[no-untyped-def]
    m0 = M0ExperimentContract.model_validate_json(M0_PATH.read_bytes(), strict=True)
    pack = load_m4_input_pack(M4_INPUT_PATH)
    result = load_m4_result(M4_RESULT_PATH, pack=pack, m0=m0)
    return m0, pack, result


@lru_cache
def _prepared():  # type: ignore[no-untyped-def]
    m0, pack, result = _sources()
    return m0, pack, result, prepare_m5_replay_input(pack, result, m0)


def test_prepare_binds_canonical_evidence_and_labels_generated_fixture() -> None:
    m0, m4_pack, m4_result, replay_input = _prepared()

    assert replay_input.m0_contract_id == m0.contract_id
    assert replay_input.m4_input_id == m4_pack.input_id
    assert replay_input.m4_result_id == m4_result.result_id
    assert replay_input.standard_sheet.length == 10.0
    assert replay_input.standard_sheet.height == 10.0
    assert replay_input.standard_sheet.provenance == "generated"
    assert replay_input.standard_sheet.material.provenance.value == "assumed"
    assert tuple(order.part.id for order in replay_input.orders) == (
        "m5-generated-part-a",
        "m5-generated-part-b",
    )
    assert all(order.chronology_provenance == "generated" for order in replay_input.orders)
    assert all(order.geometry_provenance == "generated" for order in replay_input.orders)
    assert replay_input.rates.provenance == "generated"


def test_evaluate_produces_hand_computable_canonical_replay() -> None:
    m0, _, _, replay_input = _prepared()
    result = evaluate_m5_replay(replay_input, m0)

    assert tuple(event.cumulative_costs.net_cost for event in result.events) == (102.0, 107.6)
    assert result.summary.final_net_cost == 104.9
    assert result.summary.technical_decision == "pass"
    assert result.claim_ceiling.endswith("commercial_evidence")


def test_publish_and_load_revalidate_input_result_and_immutability(tmp_path: Path) -> None:
    m0, m4_pack, m4_result, replay_input = _prepared()
    result = evaluate_m5_replay(replay_input, m0)

    input_path = publish_m5_replay_input(tmp_path, replay_input)
    result_path = publish_m5_replay_result(tmp_path, result)
    assert publish_m5_replay_input(tmp_path, replay_input) == input_path
    assert publish_m5_replay_result(tmp_path, result) == result_path
    assert (
        load_m5_replay_input(
            input_path,
            m4_pack=m4_pack,
            m4_result=m4_result,
            m0=m0,
        )
        == replay_input
    )
    assert load_m5_replay_result(result_path, replay_input=replay_input, m0=m0) == result

    with pytest.raises(M5EvidenceError, match="immutable"):
        input_path.write_text("{}")
        publish_m5_replay_input(tmp_path, replay_input)


def test_load_rejects_tampering_noncanonical_json_and_symlink(tmp_path: Path) -> None:
    m0, m4_pack, m4_result, replay_input = _prepared()
    result = evaluate_m5_replay(replay_input, m0)
    result_path = publish_m5_replay_result(tmp_path, result)

    payload = json.loads(result_path.read_text())
    payload["summary"]["final_net_cost"] = 999.0
    result_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    with pytest.raises(M5EvidenceError, match="validation"):
        load_m5_replay_result(result_path, replay_input=replay_input, m0=m0)

    input_path = tmp_path / "noncanonical.json"
    input_path.write_text(json.dumps(replay_input.model_dump(mode="json")))
    with pytest.raises(M5EvidenceError, match="canonical"):
        load_m5_replay_input(
            input_path,
            m4_pack=m4_pack,
            m4_result=m4_result,
            m0=m0,
        )

    link = tmp_path / "input-link.json"
    link.symlink_to(input_path)
    with pytest.raises(M5EvidenceError, match="regular file"):
        load_m5_replay_input(
            link,
            m4_pack=m4_pack,
            m4_result=m4_result,
            m0=m0,
        )


def test_prepare_rejects_m4_identity_drift() -> None:
    m0, m4_pack, m4_result = _sources()
    drifted = m4_result.model_copy(update={"input_id": "yfri-" + "f" * 24})

    with pytest.raises(M5EvidenceError, match="M4"):
        prepare_m5_replay_input(m4_pack, drifted, m0)
