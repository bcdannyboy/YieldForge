from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import replace

import pytest

from tests.oracle.fixtures import (
    ExhaustiveCertificateCase,
    exhaustive_certificate_cases,
)
from yieldforge.baseline.policies import M7PolicyName, rank_policy_action
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle import facts
from yieldforge.oracle import sparse as sparse_module
from yieldforge.oracle.checker import check_action_proofs
from yieldforge.oracle.fact_checker import M8FactBundleCheckRequest, check_m8_fact_bundle
from yieldforge.oracle.factored import M8UncheckedBundleRequest, score_unchecked_fact_bundle
from yieldforge.oracle.proofs import m8_suffix_sha256
from yieldforge.oracle.reference import score_reference_event
from yieldforge.oracle.sparse import score_sparse_event

CASES = exhaustive_certificate_cases()
EXPECTED_SEMANTIC_CASE_COUNT = 45
_FREEZE_ID = "yfm7freeze-" + "b" * 24
_FREEZE_SHA256 = "sha256:" + "b" * 64


def _case_id(case: ExhaustiveCertificateCase) -> str:
    return case.case_id


def _full_bundle_request(
    unchecked: M8UncheckedBundleRequest,
    semantic_bytes: bytes,
) -> M8FactBundleCheckRequest:
    oracle_request = unchecked.oracle_request
    runtime = oracle_request.runtime
    cursor = oracle_request.cursor
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    visible = oracle_request.visibility.visible_suffix(current_position=catalog.event_position)
    stop = catalog.event_position + 1 + len(visible)
    semantic_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    return M8FactBundleCheckRequest(
        semantic_bundle_bytes=semantic_bytes,
        oracle_request=oracle_request,
        expected_semantic_runtime_sha256=semantic_runtime_sha256,
        expected_current_cursor_sha256=m7_cursor_sha256(cursor),
        expected_catalog_event_position=catalog.event_position,
        expected_catalog_action_ids=tuple(item.action_id for item in catalog.actions),
        expected_stop_event_position=stop,
        expected_suffix_sha256=m8_suffix_sha256(
            semantic_runtime_sha256=semantic_runtime_sha256,
            start_event_position=catalog.event_position,
            stop_event_position=stop,
            bindings=visible,
        ),
        expected_freeze_id=unchecked.freeze_id,
        expected_freeze_sha256=unchecked.freeze_sha256,
        allow_exact_replay=True,
    )


def _normalized_v1_influences(witness) -> tuple[tuple[object, ...], ...]:  # type: ignore[no-untyped-def]
    return tuple(
        sorted(
            (
                item.remnant_id,
                item.candidate_id,
                item.classification,
                item.common_action_id,
                item.common_catalog_action_id,
                item.common_decision_key,
                item.competing_action_id,
                item.competing_catalog_action_id,
                item.competing_decision_key,
            )
            for item in witness.influences
        )
    )


def _normalized_v2_influences(
    influence,
    *,
    common_decision_key: tuple[str, ...],
) -> tuple[tuple[object, ...], ...]:  # type: ignore[no-untyped-def]
    competitor_by_remnant = {
        item.selected_remnant_id: item for item in influence.competitor_evidence
    }
    remnant_ids = tuple(
        sorted(
            {
                *influence.inventory_delta.removed_remnant_ids,
                *influence.inventory_delta.added_remnant_ids,
            }
        )
    )
    return tuple(
        (
            remnant_id,
            competitor_by_remnant[remnant_id].candidate_id
            if remnant_id in competitor_by_remnant
            else None,
            "policy_dominated" if remnant_id in competitor_by_remnant else "no_fit",
            influence.common_materialized_action_id,
            influence.common_catalog_action_id,
            common_decision_key,
            competitor_by_remnant[remnant_id].materialized_action_id
            if remnant_id in competitor_by_remnant
            else None,
            competitor_by_remnant[remnant_id].catalog_action_id
            if remnant_id in competitor_by_remnant
            else None,
            competitor_by_remnant[remnant_id].decision_key
            if remnant_id in competitor_by_remnant
            else None,
        )
        for remnant_id in remnant_ids
    )


def test_exhaustive_matrix_covers_the_registered_semantic_dimensions() -> None:
    print(f"M8 finite semantic case count: {len(CASES)}")
    assert len(CASES) == EXPECTED_SEMANTIC_CASE_COUNT
    assert len({case.case_id for case in CASES}) == EXPECTED_SEMANTIC_CASE_COUNT
    assert {case.policy for case in CASES} == set(M7PolicyName)
    assert {case.material_relation for case in CASES} == {"none", "match", "mismatch"}
    assert {case.future_fit for case in CASES} == {False, True}
    assert {len(case.request.cursor.inventory) for case in CASES} == {0, 1, 2}
    assert {case.equal_costs for case in CASES} == {False, True}
    assert {case.same_time for case in CASES} == {False, True}
    assert {len(case.request.runtime.replay_input.instances) for case in CASES} == {
        2,
        3,
        4,
    }

    current_action_delta_kinds: set[str] = set()
    terminal_inventory_counts: set[int] = set()
    for case in CASES:
        assert case.future_fit is (case.first_width + case.second_width <= 10.0)
        request = case.request
        runtime = request.runtime
        binding = runtime.replay_input.instances[request.cursor.next_event_position]
        if case.material_relation == "match":
            assert request.cursor.inventory
            assert all(
                item.remnant.material == binding.material for item in request.cursor.inventory
            )
        elif case.material_relation == "mismatch":
            assert request.cursor.inventory
            assert all(
                item.remnant.material != binding.material for item in request.cursor.inventory
            )
        else:
            assert not request.cursor.inventory

        release_times = tuple(item.released_at for item in runtime.replay_input.instances)
        assert (len(set(release_times)) == 1) is case.same_time

        catalog = enumerate_m7_action_catalog(runtime, cursor=request.cursor)
        immediate_costs = {context.immediate_net_cost for context in catalog.contexts}
        assert (len(immediate_costs) == 1) is case.equal_costs

        standard_contexts = tuple(
            context for context in catalog.contexts if context.action_id.startswith("m7-standard:")
        )
        assert len(standard_contexts) == 2
        standard_ranks = tuple(
            rank_policy_action(case.policy, context) for context in standard_contexts
        )
        assert standard_ranks[0].comparison_key[:-3] == standard_ranks[1].comparison_key[:-3]
        assert standard_ranks[0].comparison_key[-3:] != standard_ranks[1].comparison_key[-3:]

        fallback = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
        fallback_descriptor = next(
            descriptor
            for descriptor in catalog.actions
            if descriptor.action_id == fallback.action_id
        )
        fallback_step = apply_m7_action_descriptor(
            runtime,
            cursor=request.cursor,
            catalog=catalog,
            descriptor=fallback_descriptor,
            decision_key=fallback.decision_key,
        )
        fallback_ids = {item.remnant.remnant_id for item in fallback_step.cursor.inventory}
        for descriptor in catalog.actions:
            step = apply_m7_action_descriptor(
                runtime,
                cursor=request.cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=(f"exhaustive_action_id={descriptor.action_id}",),
            )
            branch_ids = {item.remnant.remnant_id for item in step.cursor.inventory}
            if branch_ids - fallback_ids:
                current_action_delta_kinds.add("added")
            if fallback_ids - branch_ids:
                current_action_delta_kinds.add("removed")
            continuation = run_m7_continuation(
                runtime,
                cursor=step.cursor,
                stop_event_position=len(runtime.replay_input.instances),
            )
            terminal_inventory_counts.add(len(continuation.terminal.inventory_before_liquidation))

    assert current_action_delta_kinds == {"added", "removed"}
    assert 0 in terminal_inventory_counts
    assert any(count > 0 for count in terminal_inventory_counts)


def test_exhaustive_matrix_covers_all_witness_kinds_and_rejoin_order() -> None:
    witness_kinds: set[str] = set()
    exact_escape_then_rejoin = False
    for case in CASES:
        sparse = score_sparse_event(case.request)
        for proof in sparse.proofs:
            classifications = tuple(witness.classification for witness in proof.witnesses)
            witness_kinds.update(classifications)
            exact_positions = tuple(
                index
                for index, classification in enumerate(classifications)
                if classification == "exact_transition"
            )
            rejoin_positions = tuple(
                index
                for index, classification in enumerate(classifications)
                if classification == "state_rejoin"
            )
            exact_escape_then_rejoin |= bool(
                exact_positions
                and rejoin_positions
                and min(exact_positions) < max(rejoin_positions)
            )

    assert witness_kinds == {
        "state_rejoin",
        "no_fit",
        "policy_dominated",
        "exact_transition",
    }
    assert exact_escape_then_rejoin


def test_full_fact_checker_matches_every_v1_transition_and_decision() -> None:
    observed_classifications: set[str] = set()
    for case in CASES:
        unchecked = M8UncheckedBundleRequest(
            oracle_request=case.request,
            freeze_id=_FREEZE_ID,
            freeze_sha256=_FREEZE_SHA256,
        )
        generated = score_unchecked_fact_bundle(unchecked)
        checked = check_m8_fact_bundle(
            _full_bundle_request(unchecked, generated.semantic_bytes),
        )
        sparse = score_sparse_event(case.request)

        assert checked.valid, (case.case_id, checked)
        assert checked.decision == sparse.decision
        assert checked.decision is not None
        assert tuple(score.action_id for score in checked.decision.scores) == tuple(
            proof.catalog_action_id for proof in sparse.proofs
        )

        root_by_catalog_id = {
            root.catalog_action_id: root for root in generated.bundle.action_roots
        }
        influence_by_ref = {
            influence.fact_sha256: influence for influence in generated.bundle.influence_facts
        }
        common_by_ref = {common.fact_sha256: common for common in generated.bundle.common_lemmas}
        for proof in sparse.proofs:
            root = root_by_catalog_id[proof.catalog_action_id]
            assert (
                root.action_id,
                root.catalog_action_id,
                root.baseline_action_id,
                root.baseline_catalog_action_id,
                root.start_event_position,
                root.stop_event_position,
                root.suffix_sha256,
                root.semantic_runtime_sha256,
                root.start_state_sha256,
                root.final_state_sha256,
                root.final_net_cost_bits,
            ) == (
                proof.action_id,
                proof.catalog_action_id,
                proof.baseline_action_id,
                proof.baseline_catalog_action_id,
                proof.start_event_position,
                proof.stop_event_position,
                proof.suffix_sha256,
                proof.semantic_runtime_sha256,
                proof.start_state_sha256,
                proof.final_state_sha256,
                facts.encode_canonical_f64(float(proof.final_net_cost)),
            )
            assert len(root.influence_fact_refs) == len(proof.witnesses)
            for common_ref, influence_ref, witness in zip(
                root.common_lemma_refs,
                root.influence_fact_refs,
                proof.witnesses,
                strict=True,
            ):
                common = common_by_ref[common_ref]
                influence = influence_by_ref[influence_ref]
                observed_classifications.add(influence.classification)
                assert (
                    influence.event_position,
                    influence.classification,
                    influence.common_catalog_action_id,
                    influence.common_materialized_action_id,
                    influence.branch_materialized_action_id,
                    influence.state_before_sha256,
                    influence.state_after_sha256,
                ) == (
                    witness.event_position,
                    witness.classification,
                    common.selected_catalog_action_id,
                    witness.common_action_id,
                    witness.branch_action_id,
                    witness.state_before_sha256,
                    witness.state_after_sha256,
                )
                if witness.classification in {"state_rejoin", "exact_transition"}:
                    assert not witness.influences
                    assert not influence.rejection_evidence
                    assert not influence.search_evidence
                    assert not influence.competitor_evidence
                else:
                    assert _normalized_v2_influences(
                        influence,
                        common_decision_key=common.selected_decision_key,
                    ) == _normalized_v1_influences(witness)

    assert observed_classifications == {
        "state_rejoin",
        "no_fit",
        "policy_dominated",
        "exact_transition",
    }


def test_active_generator_rejects_layout_substitution_and_stays_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(item for item in CASES if item.case_id.endswith("zero-no-fit-equal-separated-two"))
    original = sparse_module._prepare_translation_layout_batch  # noqa: SLF001
    substitution_attempts = 0

    @contextmanager
    def attacked_batch(runtime, *, event_positions):  # type: ignore[no-untyped-def]
        nonlocal substitution_attempts
        with original(runtime, event_positions=event_positions) as prepared:
            substitution_attempts += 1
            assert not hasattr(prepared, "_layouts")
            with pytest.raises(AttributeError):
                object.__setattr__(prepared, "_layouts", ())
            yield prepared

    monkeypatch.setattr(
        sparse_module,
        "_prepare_translation_layout_batch",
        attacked_batch,
    )

    sparse = sparse_module.score_sparse_event(case.request)
    reference = score_reference_event(case.request)
    checks = check_action_proofs(case.request, sparse.proofs)

    assert substitution_attempts == 1
    assert sparse.metrics.rejection_certificate_count > 0
    assert sparse.decision == reference.decision
    assert all(result.valid for result in checks)


@pytest.mark.parametrize("mutation", ["empty", "duplicate"])
def test_differential_rejects_incomplete_or_duplicate_proofs(
    monkeypatch,  # type: ignore[no-untyped-def]
    mutation: str,
) -> None:
    case = CASES[0]
    sparse = score_sparse_event(case.request)
    proofs = () if mutation == "empty" else tuple(sparse.proofs[0] for _ in sparse.proofs)
    monkeypatch.setattr(
        sys.modules[__name__],
        "score_sparse_event",
        lambda request: replace(sparse, proofs=proofs),
    )

    with pytest.raises(AssertionError):
        test_certificate_kernel_matches_full_reference(case)


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_certificate_kernel_matches_full_reference(
    case: ExhaustiveCertificateCase,
) -> None:
    sparse = score_sparse_event(case.request)
    reference = score_reference_event(case.request)

    assert sparse.decision == reference.decision
    assert len(sparse.proofs) == sparse.decision.scored_action_count
    proof_catalog_action_ids = tuple(proof.catalog_action_id for proof in sparse.proofs)
    proof_materialized_action_ids = tuple(proof.action_id for proof in sparse.proofs)
    assert len(set(proof_catalog_action_ids)) == len(proof_catalog_action_ids)
    assert len(set(proof_materialized_action_ids)) == len(proof_materialized_action_ids)
    assert tuple(sorted(proof_catalog_action_ids)) == sparse.decision.action_ids
    assert proof_catalog_action_ids == tuple(score.action_id for score in sparse.decision.scores)

    checks = check_action_proofs(case.request, sparse.proofs)
    assert len(checks) == sparse.decision.scored_action_count
    assert all(result.valid for result in checks)
