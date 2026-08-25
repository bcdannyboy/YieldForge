from __future__ import annotations

import pytest

from tests.oracle.fixtures import two_problem_runtime
from yieldforge.baseline.replay import initial_m7_cursor
from yieldforge.oracle.proofs import M8ActionProof, build_m8_action_proof
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.sparse import score_sparse_event
from yieldforge.oracle.visibility import FullRealizedVisibility


def _request(*, passive: bool = True) -> M8OracleRequest:
    runtime = two_problem_runtime(
        first_width=9.0 if passive else 4.0,
        second_width=4.0,
    )
    return M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )


def _rebuild(proof: M8ActionProof, **changes: object) -> M8ActionProof:
    fields = {
        "action_id": proof.action_id,
        "catalog_action_id": proof.catalog_action_id,
        "baseline_action_id": proof.baseline_action_id,
        "baseline_catalog_action_id": proof.baseline_catalog_action_id,
        "start_event_position": proof.start_event_position,
        "stop_event_position": proof.stop_event_position,
        "suffix_sha256": proof.suffix_sha256,
        "semantic_runtime_sha256": proof.semantic_runtime_sha256,
        "start_state_sha256": proof.start_state_sha256,
        "witnesses": proof.witnesses,
        "final_net_cost": proof.final_net_cost,
        "final_state_sha256": proof.final_state_sha256,
    }
    fields.update(changes)
    return build_m8_action_proof(**fields)  # type: ignore[arg-type]


def _proof_with_classification(
    request: M8OracleRequest,
    classification: str,
) -> M8ActionProof:
    return next(
        proof
        for proof in score_sparse_event(request).proofs
        if any(witness.classification == classification for witness in proof.witnesses)
    )


def test_checker_is_independent_of_both_scoring_control_flows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.oracle import reference, sparse
    from yieldforge.oracle.checker import check_action_proof

    request = _request()
    proof = score_sparse_event(request).proofs[0]
    monkeypatch.setattr(
        sparse,
        "score_certificate_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called generator")),
    )
    monkeypatch.setattr(
        reference,
        "score_reference_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called reference")),
    )

    result = check_action_proof(request, proof)

    assert result.valid
    assert result.failure_code == "valid"
    assert result.checked_event_count == len(proof.witnesses)


@pytest.mark.parametrize(
    "mutation",
    [
        "classification",
        "event_position",
        "state_hash",
        "decision_key",
        "score",
        "suffix_hash",
        "runtime_binding",
        "start_state",
        "catalog_action_id",
        "materialized_action_id",
    ],
)
def test_checker_fails_closed_on_independently_rehashed_tampering(
    mutation: str,
) -> None:
    from yieldforge.oracle.checker import check_action_proof

    request = _request()
    proof = _proof_with_classification(request, "no_fit")
    if mutation == "classification":
        witness = proof.witnesses[0].model_copy(update={"classification": "exact_transition"})
        tampered = proof.model_copy(update={"witnesses": (witness,)})
    elif mutation == "event_position":
        witness = proof.witnesses[0].model_copy(
            update={"event_position": proof.witnesses[0].event_position + 1}
        )
        tampered = proof.model_copy(update={"witnesses": (witness,)})
    elif mutation == "state_hash":
        witness = proof.witnesses[0].model_copy(
            update={"state_after_sha256": "sha256:" + "f" * 64}
        )
        tampered = _rebuild(proof, witnesses=(witness,))
    elif mutation == "decision_key":
        witness = proof.witnesses[0]
        influence = witness.influences[0].model_copy(
            update={
                "common_decision_key": (
                    *witness.influences[0].common_decision_key,
                    "tampered=true",
                )
            }
        )
        tampered = _rebuild(
            proof,
            witnesses=(witness.model_copy(update={"influences": (influence,)}),),
        )
    elif mutation == "score":
        tampered = _rebuild(proof, final_net_cost=proof.final_net_cost + 1.0)
    elif mutation == "suffix_hash":
        tampered = _rebuild(proof, suffix_sha256="sha256:" + "f" * 64)
    elif mutation == "runtime_binding":
        tampered = _rebuild(proof, semantic_runtime_sha256="sha256:" + "f" * 64)
    elif mutation == "start_state":
        tampered = _rebuild(proof, start_state_sha256="sha256:" + "f" * 64)
    elif mutation == "catalog_action_id":
        tampered = _rebuild(proof, catalog_action_id="m7-standard:missing")
    elif mutation == "materialized_action_id":
        tampered = _rebuild(proof, action_id="yfm7a-" + "f" * 24)
    else:  # pragma: no cover
        raise AssertionError(mutation)

    result = check_action_proof(request, tampered)

    assert not result.valid
    assert result.failure_code != "valid"


def test_checker_validates_exact_transition_and_final_state() -> None:
    from yieldforge.oracle.checker import check_action_proof

    request = _request(passive=False)
    proof = _proof_with_classification(request, "exact_transition")
    result = check_action_proof(request, proof)

    assert result.valid
    assert result.exact_transition_count > 0
    assert result.checked_event_count == len(proof.witnesses)

    tampered = _rebuild(proof, final_state_sha256="sha256:" + "f" * 64)
    assert not check_action_proof(request, tampered).valid
