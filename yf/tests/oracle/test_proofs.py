from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import (
    M8ActionProof,
    M8EventWitness,
    M8InfluenceWitness,
    build_m8_action_proof,
)


def _action(index: int) -> str:
    return f"yfm7a-{index:024x}"


def _sha(index: int) -> str:
    return f"sha256:{index:064x}"


def _no_fit_influence(index: int = 1) -> M8InfluenceWitness:
    return M8InfluenceWitness(
        remnant_id=f"yfrm-{index:024x}",
        candidate_id=f"candidate-{index}",
        classification="no_fit",
        evidence_sha256=_sha(100 + index),
        common_action_id=_action(10),
        common_decision_key=("immediate_net_cost=10", f"action_id={_action(10)}"),
    )


def _policy_dominated_influence(index: int = 2) -> M8InfluenceWitness:
    return M8InfluenceWitness(
        remnant_id=f"yfrm-{index:024x}",
        candidate_id=f"candidate-{index}",
        classification="policy_dominated",
        evidence_sha256=_sha(100 + index),
        common_action_id=_action(10),
        competing_action_id=_action(20 + index),
        common_decision_key=("immediate_net_cost=10", f"action_id={_action(10)}"),
        competing_decision_key=("immediate_net_cost=20", f"action_id={_action(20 + index)}"),
    )


def _event_witnesses() -> tuple[M8EventWitness, ...]:
    return (
        M8EventWitness(
            event_position=3,
            classification="state_rejoin",
            common_action_id=_action(10),
            branch_action_id=_action(10),
            state_before_sha256=_sha(1),
            state_after_sha256=_sha(1),
        ),
        M8EventWitness(
            event_position=4,
            classification="no_fit",
            common_action_id=_action(10),
            branch_action_id=_action(10),
            state_before_sha256=_sha(2),
            state_after_sha256=_sha(3),
            influences=(_no_fit_influence(),),
        ),
        M8EventWitness(
            event_position=5,
            classification="policy_dominated",
            common_action_id=_action(10),
            branch_action_id=_action(10),
            state_before_sha256=_sha(3),
            state_after_sha256=_sha(4),
            influences=(_policy_dominated_influence(),),
        ),
        M8EventWitness(
            event_position=6,
            classification="exact_transition",
            common_action_id=_action(10),
            branch_action_id=_action(11),
            state_before_sha256=_sha(4),
            state_after_sha256=_sha(5),
        ),
    )


def _build_proof() -> M8ActionProof:
    return build_m8_action_proof(
        action_id=_action(1),
        baseline_action_id=_action(2),
        start_event_position=2,
        stop_event_position=7,
        suffix_sha256=_sha(50),
        witnesses=_event_witnesses(),
        final_net_cost=125.5,
    )


def _revalidate(payload: dict[str, object]) -> M8ActionProof:
    return M8ActionProof.model_validate(payload, strict=True)


def test_all_four_event_witness_kinds_preserve_class_specific_evidence() -> None:
    proof = _build_proof()

    assert tuple(item.classification for item in proof.witnesses) == (
        "state_rejoin",
        "no_fit",
        "policy_dominated",
        "exact_transition",
    )
    assert proof.witnesses[0].influences == ()
    assert proof.witnesses[1].influences[0].classification == "no_fit"
    assert proof.witnesses[1].influences[0].competing_action_id is None
    assert proof.witnesses[2].influences[0].classification == "policy_dominated"
    assert proof.witnesses[2].influences[0].competing_action_id == _action(22)
    assert proof.witnesses[3].influences == ()


def test_builder_constructs_content_addressed_proof_and_binds_final_result() -> None:
    proof = _build_proof()
    digest = semantic_sha256(proof, excluded_fields={"proof_id", "content_sha256"})

    assert proof.schema_version == "yieldforge.m8-action-proof.v1"
    assert proof.proof_id == f"yfm8ap-{digest[:24]}"
    assert proof.content_sha256 == f"sha256:{digest}"
    assert proof.action_id == _action(1)
    assert proof.baseline_action_id == _action(2)
    assert proof.final_net_cost == 125.5


def test_proof_requires_exact_ordered_stop_exclusive_suffix_coverage() -> None:
    proof = _build_proof()
    assert tuple(item.event_position for item in proof.witnesses) == (3, 4, 5, 6)

    missing = proof.model_dump(mode="python")
    missing["witnesses"] = missing["witnesses"][:-1]  # type: ignore[index]
    with pytest.raises(ValidationError, match="exact ordered suffix"):
        _revalidate(missing)

    invalid_bounds = proof.model_dump(mode="python")
    invalid_bounds["stop_event_position"] = invalid_bounds["start_event_position"]
    with pytest.raises(ValidationError, match="stop event position"):
        _revalidate(invalid_bounds)


@pytest.mark.parametrize("classification", ["state_rejoin", "exact_transition"])
def test_state_rejoin_and_exact_transition_reject_influence_evidence(
    classification: str,
) -> None:
    with pytest.raises(ValidationError, match="cannot carry influence"):
        M8EventWitness.model_validate(
            {
                "event_position": 0,
                "classification": classification,
                "common_action_id": _action(10),
                "branch_action_id": _action(10),
                "state_before_sha256": _sha(1),
                "state_after_sha256": _sha(1),
                "influences": (_no_fit_influence().model_dump(mode="python"),),
            },
            strict=True,
        )


@pytest.mark.parametrize("classification", ["no_fit", "policy_dominated"])
def test_influence_classifications_require_matching_nonempty_evidence(
    classification: str,
) -> None:
    with pytest.raises(ValidationError, match="matching influence"):
        M8EventWitness.model_validate(
            {
                "event_position": 0,
                "classification": classification,
                "common_action_id": _action(10),
                "branch_action_id": _action(10),
                "state_before_sha256": _sha(1),
                "state_after_sha256": _sha(2),
                "influences": (),
            },
            strict=True,
        )


def test_no_fit_and_policy_dominated_require_their_specific_fields() -> None:
    no_fit_with_competitor = _no_fit_influence().model_dump(mode="python")
    no_fit_with_competitor["competing_action_id"] = _action(20)
    no_fit_with_competitor["competing_decision_key"] = ("action_id=competitor",)
    with pytest.raises(ValidationError, match="no-fit influence"):
        M8InfluenceWitness.model_validate(no_fit_with_competitor, strict=True)

    dominated_without_competitor = _policy_dominated_influence().model_dump(mode="python")
    dominated_without_competitor["competing_action_id"] = None
    dominated_without_competitor["competing_decision_key"] = None
    with pytest.raises(ValidationError, match="policy-dominated influence"):
        M8InfluenceWitness.model_validate(dominated_without_competitor, strict=True)


def test_certified_same_action_events_reject_inconsistent_action_bindings() -> None:
    payload = _event_witnesses()[1].model_dump(mode="python")
    payload["branch_action_id"] = _action(99)

    with pytest.raises(ValidationError, match="same common and branch action"):
        M8EventWitness.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("proof_hash", "SHA-256"),
        ("proof_id", "proof ID"),
        ("witness_order", "exact ordered suffix"),
        ("classification_evidence", "matching influence"),
        ("suffix_hash", "SHA-256"),
        ("final_cost", "SHA-256"),
        ("action_id", "SHA-256"),
        ("state_hash", "SHA-256"),
        ("evidence_hash", "SHA-256"),
    ],
)
def test_full_revalidation_rejects_tampering(mutation: str, message: str) -> None:
    payload = deepcopy(_build_proof().model_dump(mode="python"))
    if mutation == "proof_hash":
        payload["content_sha256"] = _sha(999)
    elif mutation == "proof_id":
        payload["proof_id"] = f"yfm8ap-{999:024x}"
    elif mutation == "witness_order":
        witnesses = list(payload["witnesses"])  # type: ignore[arg-type]
        witnesses[1], witnesses[2] = witnesses[2], witnesses[1]
        payload["witnesses"] = tuple(witnesses)
    elif mutation == "classification_evidence":
        payload["witnesses"][1]["classification"] = "policy_dominated"  # type: ignore[index]
    elif mutation == "suffix_hash":
        payload["suffix_sha256"] = _sha(999)
    elif mutation == "final_cost":
        payload["final_net_cost"] = 125.75
    elif mutation == "action_id":
        payload["action_id"] = _action(99)
    elif mutation == "state_hash":
        payload["witnesses"][1]["state_after_sha256"] = _sha(999)  # type: ignore[index]
    elif mutation == "evidence_hash":
        payload["witnesses"][1]["influences"][0]["evidence_sha256"] = _sha(999)  # type: ignore[index]
    else:  # pragma: no cover - parametrization is exhaustive.
        raise AssertionError(mutation)

    with pytest.raises(ValidationError, match=message):
        _revalidate(payload)


def test_proof_rejects_unknown_evidence_and_nonfinite_cost() -> None:
    payload = _build_proof().model_dump(mode="python")
    payload["witnesses"][1]["influences"][0]["unknown"] = "not allowed"  # type: ignore[index]
    with pytest.raises(ValidationError):
        _revalidate(payload)

    nonfinite = _build_proof().model_dump(mode="python")
    nonfinite["final_net_cost"] = float("inf")
    with pytest.raises(ValidationError):
        _revalidate(nonfinite)

    nan_cost = _build_proof().model_dump(mode="python")
    nan_cost["final_net_cost"] = float("nan")
    with pytest.raises(ValidationError):
        _revalidate(nan_cost)


def test_zero_future_proof_is_valid_with_empty_witnesses() -> None:
    proof = build_m8_action_proof(
        action_id=_action(1),
        baseline_action_id=_action(2),
        start_event_position=8,
        stop_event_position=9,
        suffix_sha256=_sha(50),
        witnesses=(),
        final_net_cost=125.5,
    )

    assert proof.witnesses == ()
    assert proof.stop_event_position == proof.start_event_position + 1


def test_event_revalidation_rejects_tampered_nested_influence_instance() -> None:
    invalid_influence = _no_fit_influence().model_copy(update={"remnant_id": "bad"})
    event = _event_witnesses()[1].model_copy(
        update={"influences": (invalid_influence,)}
    )

    with pytest.raises(ValidationError, match="remnant_id"):
        M8EventWitness.model_validate(event, strict=True)


def test_proof_revalidation_rejects_tampered_nested_event_instance() -> None:
    invalid_event = _event_witnesses()[1].model_copy(update={"event_position": -1})
    proof = _build_proof().model_copy(
        update={
            "witnesses": (
                _event_witnesses()[0],
                invalid_event,
                *_event_witnesses()[2:],
            )
        }
    )

    with pytest.raises(ValidationError, match="event_position"):
        M8ActionProof.model_validate(proof, strict=True)


def test_proof_revalidation_rejects_tampered_outer_instance() -> None:
    proof = _build_proof().model_copy(update={"action_id": "bad"})

    with pytest.raises(ValidationError, match="action_id"):
        M8ActionProof.model_validate(proof, strict=True)


@pytest.mark.parametrize("tamper", ["influence", "event"])
def test_public_builder_revalidates_nested_instances_before_hashing(tamper: str) -> None:
    witnesses = _event_witnesses()
    if tamper == "influence":
        invalid_influence = witnesses[1].influences[0].model_copy(
            update={"remnant_id": "bad"}
        )
        invalid_event = witnesses[1].model_copy(
            update={"influences": (invalid_influence,)}
        )
    else:
        invalid_event = witnesses[1].model_copy(update={"event_position": -1})
    invalid_witnesses = (witnesses[0], invalid_event, *witnesses[2:])

    with pytest.raises(ValidationError):
        build_m8_action_proof(
            action_id=_action(1),
            baseline_action_id=_action(2),
            start_event_position=2,
            stop_event_position=7,
            suffix_sha256=_sha(50),
            witnesses=invalid_witnesses,
            final_net_cost=125.5,
        )
