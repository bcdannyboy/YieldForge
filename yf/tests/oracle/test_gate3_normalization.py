from __future__ import annotations

import hashlib
import json

import pytest

from tests.oracle.fixtures import exhaustive_certificate_cases
from yieldforge.baseline.replay import (
    enumerate_m7_action_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
)
from yieldforge.oracle.checker import check_action_proofs
from yieldforge.oracle.fact_checker import M8FactBundleCheckRequest, check_m8_fact_bundle
from yieldforge.oracle.factored import M8UncheckedBundleRequest, score_unchecked_fact_bundle
from yieldforge.oracle.gate3_normalization import (
    normalize_m8_action_proof,
    normalize_m8_fact_bundle_root,
)
from yieldforge.oracle.proofs import (
    M8EventWitness,
    M8InfluenceWitness,
    build_m8_action_proof,
    m8_suffix_sha256,
)
from yieldforge.oracle.sparse import score_sparse_event

_FREEZE_ID = "yfm7freeze-" + "b" * 24
_FREEZE_SHA256 = "sha256:" + "b" * 64


def _checked_v1_v2(case):  # type: ignore[no-untyped-def]
    v1 = score_sparse_event(case.request)
    assert all(item.valid for item in check_action_proofs(case.request, v1.proofs))

    unchecked = M8UncheckedBundleRequest(
        oracle_request=case.request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    runtime = case.request.runtime
    cursor = case.request.cursor
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    visible = case.request.visibility.visible_suffix(
        current_position=catalog.event_position,
    )
    stop_event_position = catalog.event_position + 1 + len(visible)
    semantic_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    checked = check_m8_fact_bundle(
        M8FactBundleCheckRequest(
            semantic_bundle_bytes=generated.semantic_bytes,
            oracle_request=case.request,
            expected_semantic_runtime_sha256=semantic_runtime_sha256,
            expected_current_cursor_sha256=m7_cursor_sha256(cursor),
            expected_catalog_event_position=catalog.event_position,
            expected_catalog_action_ids=tuple(item.action_id for item in catalog.actions),
            expected_stop_event_position=stop_event_position,
            expected_suffix_sha256=m8_suffix_sha256(
                semantic_runtime_sha256=semantic_runtime_sha256,
                start_event_position=catalog.event_position,
                stop_event_position=stop_event_position,
                bindings=visible,
            ),
            expected_freeze_id=_FREEZE_ID,
            expected_freeze_sha256=_FREEZE_SHA256,
            allow_exact_replay=True,
        )
    )
    assert checked.valid
    return v1.proofs, generated.bundle


def test_normalizes_all_45_checked_v1_and_v2_cases_to_identical_records() -> None:
    cases = exhaustive_certificate_cases()
    assert len(cases) == 45
    observed_classifications: set[str] = set()

    for case in cases:
        proofs, bundle = _checked_v1_v2(case)
        roots_by_catalog_action = {item.catalog_action_id: item for item in bundle.action_roots}
        assert len(roots_by_catalog_action) == len(proofs)

        for proof in proofs:
            root = roots_by_catalog_action[proof.catalog_action_id]
            normalized_v1 = normalize_m8_action_proof(proof)
            normalized_v2 = normalize_m8_fact_bundle_root(
                bundle,
                root_fact_sha256=root.fact_sha256,
            )
            observed_classifications.update(
                item.classification for item in normalized_v1.ordered_event_evidence
            )
            assert normalized_v1 == normalized_v2, case.case_id

    assert observed_classifications == {
        "state_rejoin",
        "no_fit",
        "policy_dominated",
        "exact_transition",
    }


def test_normalized_influence_hash_excludes_v1_legacy_evidence_sha() -> None:
    case = exhaustive_certificate_cases()[1]
    proof = next(
        proof
        for proof in score_sparse_event(case.request).proofs
        if any(witness.influences for witness in proof.witnesses)
    )
    event_index = next(index for index, witness in enumerate(proof.witnesses) if witness.influences)
    original_event = proof.witnesses[event_index]
    original_influence = original_event.influences[0]
    replacement_sha = "sha256:" + (
        "e" * 64 if original_influence.evidence_sha256 != "sha256:" + "e" * 64 else "d" * 64
    )
    altered_influence = M8InfluenceWitness.model_validate(
        {
            **original_influence.model_dump(mode="python"),
            "evidence_sha256": replacement_sha,
        },
        strict=True,
    )
    altered_event = M8EventWitness.model_validate(
        {
            **original_event.model_dump(mode="python"),
            "influences": (altered_influence, *original_event.influences[1:]),
        },
        strict=True,
    )
    altered_events = list(proof.witnesses)
    altered_events[event_index] = altered_event
    altered_proof = build_m8_action_proof(
        action_id=proof.action_id,
        catalog_action_id=proof.catalog_action_id,
        baseline_action_id=proof.baseline_action_id,
        baseline_catalog_action_id=proof.baseline_catalog_action_id,
        start_event_position=proof.start_event_position,
        stop_event_position=proof.stop_event_position,
        suffix_sha256=proof.suffix_sha256,
        semantic_runtime_sha256=proof.semantic_runtime_sha256,
        start_state_sha256=proof.start_state_sha256,
        witnesses=tuple(altered_events),
        final_net_cost=float(proof.final_net_cost),
        final_state_sha256=proof.final_state_sha256,
    )

    assert altered_proof.content_sha256 != proof.content_sha256
    assert normalize_m8_action_proof(altered_proof) == normalize_m8_action_proof(proof)


def test_normalized_influence_hash_is_domain_separated_and_content_stable() -> None:
    case = exhaustive_certificate_cases()[1]
    proof = next(
        proof
        for proof in score_sparse_event(case.request).proofs
        if any(witness.influences for witness in proof.witnesses)
    )
    influence = next(
        item
        for witness in normalize_m8_action_proof(proof).ordered_event_evidence
        for item in witness.influences
    )
    payload = {
        "remnant_id": influence.remnant_id,
        "candidate_id": influence.candidate_id,
        "classification": influence.classification,
        "common_action_id": influence.common_policy.action_id,
        "common_catalog_action_id": influence.common_policy.catalog_action_id,
        "common_decision_key": influence.common_policy.decision_key,
        "competing_action_id": (
            influence.competing_policy.action_id if influence.competing_policy is not None else None
        ),
        "competing_catalog_action_id": (
            influence.competing_policy.catalog_action_id
            if influence.competing_policy is not None
            else None
        ),
        "competing_decision_key": (
            influence.competing_policy.decision_key
            if influence.competing_policy is not None
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected = (
        "sha256:"
        + hashlib.sha256(b"yieldforge.m8.gate3.normalized-influence.v1\0" + encoded).hexdigest()
    )

    assert influence.evidence_sha256 == expected


def test_v1_normalizer_revalidates_tampered_content() -> None:
    proof = score_sparse_event(exhaustive_certificate_cases()[0].request).proofs[0]
    object.__setattr__(proof, "final_net_cost", float(proof.final_net_cost) + 1.0)

    with pytest.raises(ValueError, match="SHA-256"):
        normalize_m8_action_proof(proof)


def test_v2_normalizer_rejects_unknown_root_reference() -> None:
    _, bundle = _checked_v1_v2(exhaustive_certificate_cases()[0])

    with pytest.raises(ValueError, match="not present"):
        normalize_m8_fact_bundle_root(
            bundle,
            root_fact_sha256="sha256:" + "f" * 64,
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("semantic_runtime_sha256", "sha256:" + "f" * 64),
        ("common_lemma_refs", ("sha256:" + "f" * 64,)),
    ],
)
def test_v2_normalizer_revalidates_tampered_root_context_and_refs(
    field_name: str,
    replacement: object,
) -> None:
    _, bundle = _checked_v1_v2(exhaustive_certificate_cases()[0])
    root = bundle.action_roots[0]
    if field_name == "common_lemma_refs" and len(root.common_lemma_refs) != 1:
        replacement = tuple("sha256:" + "f" * 64 for _ in root.common_lemma_refs)
    object.__setattr__(root, field_name, replacement)

    with pytest.raises(ValueError):
        normalize_m8_fact_bundle_root(bundle, root_fact_sha256=root.fact_sha256)
