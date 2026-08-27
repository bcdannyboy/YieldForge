"""Cross-version semantic normalization for the M8 Gate-3 proof audit."""

from __future__ import annotations

import hashlib
import json

from yieldforge.baseline.contracts import BaselineContractModel
from yieldforge.oracle.facts import (
    M8ActionRootV2,
    M8CommonTransitionLemmaV2,
    M8InfluenceFactV2,
    M8UncheckedFactBundleV2,
    encode_canonical_f64,
)
from yieldforge.oracle.gate3_evidence import (
    M8Gate3NormalizedActionRecord,
    M8Gate3NormalizedEventEvidence,
    M8Gate3NormalizedInfluenceEvidence,
    M8Gate3NormalizedPolicyEvidence,
)
from yieldforge.oracle.proofs import M8ActionProof, M8InfluenceWitness

_NORMALIZED_INFLUENCE_DOMAIN = b"yieldforge.m8.gate3.normalized-influence.v1\0"


def _strict_model[ModelT: BaselineContractModel](
    value: ModelT,
    expected_type: type[ModelT],
    *,
    label: str,
) -> ModelT:
    if type(value) is not expected_type:
        raise TypeError(f"{label} requires an exact {expected_type.__name__}")
    return expected_type.model_validate_json(value.model_dump_json(), strict=True)


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _normalized_influence_sha256(
    *,
    remnant_id: str,
    candidate_id: str | None,
    classification: str,
    common_policy: M8Gate3NormalizedPolicyEvidence,
    competing_policy: M8Gate3NormalizedPolicyEvidence | None,
) -> str:
    """Hash only the influence fields shared by the checked v1 and v2 forms."""

    payload: dict[str, object] = {
        "remnant_id": remnant_id,
        "candidate_id": candidate_id,
        "classification": classification,
        "common_action_id": common_policy.action_id,
        "common_catalog_action_id": common_policy.catalog_action_id,
        "common_decision_key": common_policy.decision_key,
        "competing_action_id": (
            competing_policy.action_id if competing_policy is not None else None
        ),
        "competing_catalog_action_id": (
            competing_policy.catalog_action_id if competing_policy is not None else None
        ),
        "competing_decision_key": (
            competing_policy.decision_key if competing_policy is not None else None
        ),
    }
    return (
        "sha256:"
        + hashlib.sha256(_NORMALIZED_INFLUENCE_DOMAIN + _canonical_bytes(payload)).hexdigest()
    )


def _influence_order(item: M8Gate3NormalizedInfluenceEvidence) -> bytes:
    return _canonical_bytes(item.model_dump(mode="json"))


def _normalize_v1_influence(
    influence: M8InfluenceWitness,
) -> M8Gate3NormalizedInfluenceEvidence:
    common_policy = M8Gate3NormalizedPolicyEvidence(
        action_id=influence.common_action_id,
        catalog_action_id=influence.common_catalog_action_id,
        decision_key=influence.common_decision_key,
    )
    competing_policy = (
        M8Gate3NormalizedPolicyEvidence(
            action_id=influence.competing_action_id,
            catalog_action_id=influence.competing_catalog_action_id,
            decision_key=influence.competing_decision_key,
        )
        if (
            influence.competing_action_id is not None
            and influence.competing_catalog_action_id is not None
            and influence.competing_decision_key is not None
        )
        else None
    )
    return M8Gate3NormalizedInfluenceEvidence(
        remnant_id=influence.remnant_id,
        candidate_id=influence.candidate_id,
        classification=influence.classification,
        evidence_sha256=_normalized_influence_sha256(
            remnant_id=influence.remnant_id,
            candidate_id=influence.candidate_id,
            classification=influence.classification,
            common_policy=common_policy,
            competing_policy=competing_policy,
        ),
        common_policy=common_policy,
        competing_policy=competing_policy,
    )


def normalize_m8_action_proof(proof: M8ActionProof) -> M8Gate3NormalizedActionRecord:
    """Normalize one strict, content-valid v1 action proof for Gate-3 comparison."""

    strict = _strict_model(proof, M8ActionProof, label="M8 Gate-3 v1 normalization")
    events = tuple(
        M8Gate3NormalizedEventEvidence(
            event_position=witness.event_position,
            classification=witness.classification,
            common_action_id=witness.common_action_id,
            branch_action_id=witness.branch_action_id,
            state_before_sha256=witness.state_before_sha256,
            state_after_sha256=witness.state_after_sha256,
            influences=tuple(
                sorted(
                    (_normalize_v1_influence(item) for item in witness.influences),
                    key=_influence_order,
                )
            ),
        )
        for witness in strict.witnesses
    )
    initial_state_after_sha256 = (
        events[0].state_before_sha256 if events else strict.final_state_sha256
    )
    return M8Gate3NormalizedActionRecord(
        action_id=strict.action_id,
        catalog_action_id=strict.catalog_action_id,
        baseline_action_id=strict.baseline_action_id,
        baseline_catalog_action_id=strict.baseline_catalog_action_id,
        start_event_position=strict.start_event_position,
        stop_event_position=strict.stop_event_position,
        suffix_sha256=strict.suffix_sha256,
        semantic_runtime_sha256=strict.semantic_runtime_sha256,
        start_state_sha256=strict.start_state_sha256,
        initial_state_after_sha256=initial_state_after_sha256,
        final_state_sha256=strict.final_state_sha256,
        ordered_event_evidence=events,
        final_net_cost_bits=encode_canonical_f64(float(strict.final_net_cost)),
    )


def _require_v2_event_context(
    *,
    root: M8ActionRootV2,
    common_ref: str,
    influence_ref: str,
    common: M8CommonTransitionLemmaV2,
    influence: M8InfluenceFactV2,
) -> None:
    if common.fact_sha256 != common_ref or influence.fact_sha256 != influence_ref:
        raise ValueError("M8 Gate-3 v2 dependency identity differs from its root reference")
    if (
        common.semantic_runtime_sha256 != root.semantic_runtime_sha256
        or influence.semantic_runtime_sha256 != root.semantic_runtime_sha256
        or common.stream_id != root.stream_id
        or influence.stream_id != root.stream_id
    ):
        raise ValueError("M8 Gate-3 v2 dependency context differs from its action root")
    if (
        influence.common_lemma_ref != common.fact_sha256
        or influence.root_action_id != root.action_id
        or influence.event_position != common.event_position
        or influence.common_catalog_action_id != common.selected_catalog_action_id
        or influence.common_materialized_action_id != common.selected_materialized_action_id
    ):
        raise ValueError("M8 Gate-3 v2 common and influence bindings differ")


def _normalize_v2_influences(
    *,
    common: M8CommonTransitionLemmaV2,
    influence: M8InfluenceFactV2,
) -> tuple[M8Gate3NormalizedInfluenceEvidence, ...]:
    if influence.classification in {"state_rejoin", "exact_transition"}:
        if influence.competitor_evidence:
            raise ValueError("M8 Gate-3 exact/rejoin influence carries a policy competitor")
        return ()

    competitors = {item.selected_remnant_id: item for item in influence.competitor_evidence}
    if len(competitors) != len(influence.competitor_evidence):
        raise ValueError("M8 Gate-3 v2 influence repeats a remnant competitor")
    remnant_ids = tuple(
        sorted(
            {
                *influence.inventory_delta.removed_remnant_ids,
                *influence.inventory_delta.added_remnant_ids,
            }
        )
    )
    common_policy = M8Gate3NormalizedPolicyEvidence(
        action_id=influence.common_materialized_action_id,
        catalog_action_id=influence.common_catalog_action_id,
        decision_key=common.selected_decision_key,
    )
    normalized: list[M8Gate3NormalizedInfluenceEvidence] = []
    for remnant_id in remnant_ids:
        competitor = competitors.get(remnant_id)
        classification = "policy_dominated" if competitor is not None else "no_fit"
        candidate_id = competitor.candidate_id if competitor is not None else None
        competing_policy = (
            M8Gate3NormalizedPolicyEvidence(
                action_id=competitor.materialized_action_id,
                catalog_action_id=competitor.catalog_action_id,
                decision_key=competitor.decision_key,
            )
            if competitor is not None
            else None
        )
        normalized.append(
            M8Gate3NormalizedInfluenceEvidence(
                remnant_id=remnant_id,
                candidate_id=candidate_id,
                classification=classification,
                evidence_sha256=_normalized_influence_sha256(
                    remnant_id=remnant_id,
                    candidate_id=candidate_id,
                    classification=classification,
                    common_policy=common_policy,
                    competing_policy=competing_policy,
                ),
                common_policy=common_policy,
                competing_policy=competing_policy,
            )
        )
    return tuple(sorted(normalized, key=_influence_order))


def normalize_m8_fact_bundle_root(
    bundle: M8UncheckedFactBundleV2,
    *,
    root_fact_sha256: str,
) -> M8Gate3NormalizedActionRecord:
    """Normalize one referenced root from a strict, content-valid v2 fact bundle."""

    strict = _strict_model(
        bundle,
        M8UncheckedFactBundleV2,
        label="M8 Gate-3 v2 normalization",
    )
    roots = {item.fact_sha256: item for item in strict.action_roots}
    try:
        root = roots[root_fact_sha256]
    except KeyError as exc:
        raise ValueError("M8 Gate-3 v2 action root is not present in the bundle") from exc
    if (
        root.semantic_runtime_sha256 != strict.provenance.semantic_runtime_sha256
        or root.stream_id != strict.provenance.stream_id
        or root.suffix_sha256 != strict.provenance.suffix_sha256
    ):
        raise ValueError("M8 Gate-3 v2 action root differs from bundle provenance")

    commons = {item.fact_sha256: item for item in strict.common_lemmas}
    influences = {item.fact_sha256: item for item in strict.influence_facts}
    events: list[M8Gate3NormalizedEventEvidence] = []
    for common_ref, influence_ref in zip(
        root.common_lemma_refs,
        root.influence_fact_refs,
        strict=True,
    ):
        try:
            common = commons[common_ref]
            influence = influences[influence_ref]
        except KeyError as exc:
            raise ValueError("M8 Gate-3 v2 action root contains an unknown dependency") from exc
        _require_v2_event_context(
            root=root,
            common_ref=common_ref,
            influence_ref=influence_ref,
            common=common,
            influence=influence,
        )
        events.append(
            M8Gate3NormalizedEventEvidence(
                event_position=influence.event_position,
                classification=influence.classification,
                common_action_id=influence.common_materialized_action_id,
                branch_action_id=influence.branch_materialized_action_id,
                state_before_sha256=influence.state_before_sha256,
                state_after_sha256=influence.state_after_sha256,
                influences=_normalize_v2_influences(
                    common=common,
                    influence=influence,
                ),
            )
        )
    return M8Gate3NormalizedActionRecord(
        action_id=root.action_id,
        catalog_action_id=root.catalog_action_id,
        baseline_action_id=root.baseline_action_id,
        baseline_catalog_action_id=root.baseline_catalog_action_id,
        start_event_position=root.start_event_position,
        stop_event_position=root.stop_event_position,
        suffix_sha256=root.suffix_sha256,
        semantic_runtime_sha256=root.semantic_runtime_sha256,
        start_state_sha256=root.start_state_sha256,
        initial_state_after_sha256=root.initial_state_after_sha256,
        final_state_sha256=root.final_state_sha256,
        ordered_event_evidence=tuple(events),
        final_net_cost_bits=root.final_net_cost_bits,
    )


__all__ = [
    "normalize_m8_action_proof",
    "normalize_m8_fact_bundle_root",
]
