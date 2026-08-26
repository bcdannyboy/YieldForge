from __future__ import annotations

import multiprocessing
from contextlib import contextmanager
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from shapely import Polygon

from tests.oracle.fixtures import (
    exhaustive_certificate_cases,
    inventory_item,
    two_problem_runtime,
)
from yieldforge.baseline.geometry import generate_layout_translations
from yieldforge.baseline.jagua import (
    JaguaGeneratedPrefilterResult,
    JaguaRepresentationError,
)
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import initial_m7_cursor
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import certificates
from yieldforge.oracle.factored import (
    M8UncheckedBundleRequest,
    score_unchecked_fact_bundle,
    unchecked_fact_bundle_semantic_bytes,
)
from yieldforge.oracle.facts import (
    M8UncheckedFactBundleV2,
    canonical_semantic_json,
    decode_canonical_f64,
)
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.visibility import FullRealizedVisibility, KnownOnlyVisibility
from yieldforge.temporal_benchmark.contracts import (
    FeasibilityRateManifest,
    TemporalPartition,
)


def _jsonable(value):  # type: ignore[no-untyped-def]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {item.name: _jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _python_generated_jagua_prefilter(  # type: ignore[no-untyped-def]
    _executable,
    *,
    remnant,
    layouts,
    fit_config,
    search_config,
    container_guard,
):
    assert container_guard == 1.0
    batches = tuple(
        generate_layout_translations(
            SimpleNamespace(remnant_id=remnant.remnant_id),
            SimpleNamespace(candidate_id=layout.candidate_id),
            fit_config=fit_config,
            search_config=search_config,
            prepared_layout=layout,
            prepared_remnant=remnant,
        )
        for layout in layouts
    )
    return JaguaGeneratedPrefilterResult(
        translation_batches=batches,
        collision_masks=tuple((True,) * len(item.translations) for item in batches),
        guarded_query_count=sum(len(item.translations) for item in batches),
        jagua_rejection_count=0,
        build_microseconds=0,
        generation_microseconds=0,
        query_microseconds=0,
        wall_seconds=0.0,
    )


def _spawn_bundle_generation(
    source_jagua: str,
    output: multiprocessing.Queue,  # type: ignore[type-arg]
) -> None:
    with TemporaryDirectory(prefix="m8-bundle-spawn-") as directory:
        jagua_alias = Path(directory) / "worker-jagua"
        jagua_alias.symlink_to(source_jagua)
        runtime = two_problem_runtime(
            first_width=9.0,
            second_width=4.0,
            collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
            jagua_executable=jagua_alias,
        )
        request = M8OracleRequest(
            runtime=runtime,
            cursor=initial_m7_cursor(runtime.replay_input),
            visibility=FullRealizedVisibility(runtime.replay_input.instances),
        )
        generated = score_unchecked_fact_bundle(_bundle_request(request))
        semantic_bytes = generated.semantic_bytes
        strict_loaded = M8UncheckedFactBundleV2.model_validate_json(
            semantic_bytes,
            strict=True,
        )
        output.put(
            (
                semantic_bytes,
                strict_loaded.bundle_sha256,
                tuple(item.fact_sha256 for item in strict_loaded.action_roots),
                generated.telemetry.semantic_serialized_bytes,
                str(jagua_alias),
            )
        )


def _bundle_request(oracle_request: M8OracleRequest) -> M8UncheckedBundleRequest:
    return M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id="yfm7freeze-" + "b" * 24,
        freeze_sha256="sha256:" + "b" * 64,
    )


def _request(*, first_width: float = 9.0, second_width: float = 4.0) -> M8UncheckedBundleRequest:
    runtime = two_problem_runtime(first_width=first_width, second_width=second_width)
    return _bundle_request(
        M8OracleRequest(
            runtime=runtime,
            cursor=initial_m7_cursor(runtime.replay_input),
            visibility=FullRealizedVisibility(runtime.replay_input.instances),
        )
    )


def test_unchecked_bundle_emits_one_common_lemma_shared_by_every_root() -> None:
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    generated = score_unchecked_fact_bundle(_request())

    assert type(generated.bundle) is M8UncheckedFactBundleV2
    assert generated.authority_mode == "unchecked_portable"
    assert not hasattr(generated, "decision")
    assert generated.bundle.bundle_kind == "unchecked_fact_bundle"
    assert len(generated.bundle.common_lemmas) == 1
    common_refs = tuple(item.fact_sha256 for item in generated.bundle.common_lemmas)
    assert all(root.common_lemma_refs == common_refs for root in generated.bundle.action_roots)
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001


def test_terminal_reconciliation_rejects_any_missing_branch_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import factored

    original = factored.run_m7_continuation

    def replayed_event(*args, **kwargs):  # type: ignore[no-untyped-def]
        terminal = original(*args, **kwargs)
        return SimpleNamespace(events=(object(),), final_costs=terminal.final_costs)

    monkeypatch.setattr(factored, "run_m7_continuation", replayed_event)

    with pytest.raises(ValueError, match="terminal reconciliation replayed"):
        score_unchecked_fact_bundle(_request())


def test_unchecked_generator_avoids_trusted_entrypoints_and_reaps_all_leases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline import replay
    from yieldforge.oracle import contracts, factored, reference, sparse

    def forbidden(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("unchecked generator invoked a trusted proof entrypoint")

    for module, name in (
        (contracts, "build_oracle_decision"),
        (reference, "score_reference_action"),
        (reference, "score_reference_actions"),
        (reference, "score_reference_event"),
        (sparse, "build_oracle_decision"),
        (sparse, "_score_prepared_certificate_actions"),
        (sparse, "score_sparse_event"),
        (certificates, "build_m8_common_transition_fact"),
        (certificates, "build_validated_m8_common_transition"),
        (certificates, "build_validated_m8_common_transition_in_context"),
        (certificates, "validate_m8_common_transition_fact"),
        (certificates, "certify_event_passivity"),
    ):
        monkeypatch.setattr(module, name, forbidden)

    registry_snapshots = (
        dict(certificates._VALIDATED_COMMON_REGISTRY),  # noqa: SLF001
        dict(certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY),  # noqa: SLF001
        dict(sparse._PREPARED_GENERATOR_REGISTRY),  # noqa: SLF001
        dict(replay._AUTHORITATIVE_PROOF_RUNTIME_REGISTRY),  # noqa: SLF001
    )

    score_unchecked_fact_bundle(_request())

    assert registry_snapshots == (
        dict(certificates._VALIDATED_COMMON_REGISTRY),  # noqa: SLF001
        dict(certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY),  # noqa: SLF001
        dict(sparse._PREPARED_GENERATOR_REGISTRY),  # noqa: SLF001
        dict(replay._AUTHORITATIVE_PROOF_RUNTIME_REGISTRY),  # noqa: SLF001
    )

    monkeypatch.setattr(
        factored,
        "_common_lemma",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected failure")),
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        score_unchecked_fact_bundle(_request())
    assert registry_snapshots == (
        dict(certificates._VALIDATED_COMMON_REGISTRY),  # noqa: SLF001
        dict(certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY),  # noqa: SLF001
        dict(sparse._PREPARED_GENERATOR_REGISTRY),  # noqa: SLF001
        dict(replay._AUTHORITATIVE_PROOF_RUNTIME_REGISTRY),  # noqa: SLF001
    )


def test_bundle_request_rejects_unbound_freeze_or_evaluation_before_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import factored

    request = _request()
    oracle_request = request.oracle_request
    with pytest.raises(ValueError, match="freeze ID and SHA-256 differ"):
        M8UncheckedBundleRequest(
            oracle_request=oracle_request,
            freeze_id="yfm7freeze-" + "a" * 24,
            freeze_sha256="sha256:" + "b" * 64,
        )

    replay_input = oracle_request.runtime.replay_input
    evaluation_input = replay_input.model_copy(
        update={
            "instances": tuple(
                item.model_copy(update={"partition": TemporalPartition.EVALUATION})
                for item in replay_input.instances
            )
        }
    )
    evaluation_runtime = replace(oracle_request.runtime, replay_input=evaluation_input)
    evaluation_oracle_request = replace(oracle_request, runtime=evaluation_runtime)
    monkeypatch.setattr(
        factored,
        "_prepare_m8_generator_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("evaluation reached producer traversal")
        ),
    )
    with pytest.raises(ValueError, match="calibration-only"):
        M8UncheckedBundleRequest(
            oracle_request=evaluation_oracle_request,
            freeze_id=request.freeze_id,
            freeze_sha256=request.freeze_sha256,
        )
    with pytest.raises(TypeError, match="exact bundle request"):
        score_unchecked_fact_bundle(oracle_request)  # type: ignore[arg-type]


def test_bundle_request_rejects_nested_runtime_partition_drift_before_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import factored

    request = _request()
    runtime = request.oracle_request.runtime
    replay_input = runtime.replay_input
    runtime.replay_input = replay_input.model_copy(
        update={
            "instances": tuple(
                item.model_copy(update={"partition": TemporalPartition.EVALUATION})
                for item in replay_input.instances
            )
        }
    )
    monkeypatch.setattr(
        factored,
        "_prepare_m8_generator_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("mutated request reached producer traversal")
        ),
    )

    with pytest.raises(ValueError, match="calibration-only"):
        score_unchecked_fact_bundle(request)


def test_bundle_request_rejects_prepared_authority_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import factored

    original = factored._prepare_m8_generator_context  # noqa: SLF001

    @contextmanager
    def mismatched_authority(oracle_request):  # type: ignore[no-untyped-def]
        with original(oracle_request) as context:
            yield replace(
                context,
                _authority=SimpleNamespace(semantic_sha256="sha256:" + "c" * 64),
            )

    monkeypatch.setattr(factored, "_prepare_m8_generator_context", mismatched_authority)

    with pytest.raises(ValueError, match="prepared generator bindings differ"):
        score_unchecked_fact_bundle(_request())


def test_v2_roots_match_v1_and_keep_policy_event_terminal_cost_domains_distinct() -> None:
    from yieldforge.oracle.sparse import score_sparse_event

    request = _request()
    generated = score_unchecked_fact_bundle(request)
    v1 = score_sparse_event(request.oracle_request)
    bundle = generated.bundle

    proof_by_catalog = {item.catalog_action_id: item for item in v1.proofs}
    influence_by_sha = {item.fact_sha256: item for item in bundle.influence_facts}
    common_by_sha = {item.fact_sha256: item for item in bundle.common_lemmas}
    for root in bundle.action_roots:
        proof = proof_by_catalog[root.catalog_action_id]
        assert root.action_id == proof.action_id
        assert root.baseline_action_id == proof.baseline_action_id
        assert root.baseline_catalog_action_id == proof.baseline_catalog_action_id
        assert root.start_event_position == proof.start_event_position
        assert root.stop_event_position == proof.stop_event_position
        assert root.suffix_sha256 == proof.suffix_sha256
        assert root.start_state_sha256 == proof.start_state_sha256
        assert root.final_state_sha256 == proof.final_state_sha256
        assert decode_canonical_f64(root.final_net_cost_bits) == proof.final_net_cost
        assert tuple(
            common_by_sha[item].event_position for item in root.common_lemma_refs
        ) == tuple(witness.event_position for witness in proof.witnesses)
        assert tuple(
            influence_by_sha[item].classification for item in root.influence_fact_refs
        ) == tuple(witness.classification for witness in proof.witnesses)

    common = bundle.common_lemmas[0]
    standards = {item.fact_sha256: item for item in bundle.standard_candidate_facts}
    ordered_standards = tuple(standards[item] for item in common.standard_candidate_refs)
    assert tuple(item.profile_position for item in ordered_standards) == tuple(
        range(len(ordered_standards))
    )
    selected = standards[common.minimum_standard_candidate_ref]
    assert decode_canonical_f64(selected.storage_cost_bits) == 0.6
    assert decode_canonical_f64(selected.immediate_net_cost_bits) == 102.6
    assert decode_canonical_f64(common.selected_immediate_net_cost_bits) == 102.6
    assert decode_canonical_f64(common.event_net_cost_bits) == 102.1
    baseline_root = next(
        item
        for item in bundle.action_roots
        if item.catalog_action_id == "m7-standard:candidate-one"
    )
    assert decode_canonical_f64(baseline_root.final_net_cost_bits) == 197.8
    assert (
        decode_canonical_f64(common.portable_transition.cursor_after.cumulative_costs.net_cost_bits)
        == 204.1
    )


def test_semantic_roundtrip_and_nonsemantic_telemetry_are_exactly_separated() -> None:
    generated = score_unchecked_fact_bundle(_request())
    bundle = generated.bundle
    semantic_bytes = unchecked_fact_bundle_semantic_bytes(bundle)

    assert generated.semantic_bytes == semantic_bytes
    assert generated.telemetry.semantic_serialized_bytes == len(semantic_bytes)
    assert generated.telemetry.serialization_seconds >= 0.0
    assert generated.telemetry.portable_transition_serialized_bytes == tuple(
        len(canonical_semantic_json(item.portable_transition.model_dump(mode="json")))
        for item in bundle.common_lemmas
    )
    assert all(item > 0 for item in generated.telemetry.portable_transition_serialized_bytes)
    assert max(generated.telemetry.portable_transition_serialized_bytes) >= 10_000
    assert b"telemetry" not in semantic_bytes
    assert b"serialization_seconds" not in semantic_bytes
    assert M8UncheckedFactBundleV2.model_validate_json(semantic_bytes, strict=True) == bundle


def test_bounded_45_case_bundle_roots_match_v1_scores_states_actions_and_witnesses() -> None:
    from yieldforge.oracle.sparse import score_sparse_event

    event_modes: set[str] = set()
    common_modes: set[str] = set()
    action_counts: set[int] = set()
    common_counts: set[int] = set()

    cases = exhaustive_certificate_cases()
    for case in cases:
        generated = score_unchecked_fact_bundle(_bundle_request(case.request))
        trusted = score_sparse_event(case.request)
        bundle = generated.bundle
        roots = {item.catalog_action_id: item for item in bundle.action_roots}
        root_scores = tuple(
            (
                score.action_id,
                decode_canonical_f64(roots[score.action_id].final_net_cost_bits),
            )
            for score in trusted.decision.scores
        )
        assert root_scores == tuple(
            (score.action_id, score.final_net_cost) for score in trusted.decision.scores
        ), case.case_id
        assert tuple(proof.catalog_action_id for proof in trusted.proofs) == tuple(
            score.action_id for score in trusted.decision.scores
        )
        assert tuple(
            sorted((item.catalog_action_id, item.action_id) for item in bundle.action_roots)
        ) == tuple(sorted((item.catalog_action_id, item.action_id) for item in trusted.proofs))
        influences = {item.fact_sha256: item for item in bundle.influence_facts}
        commons = {item.fact_sha256: item for item in bundle.common_lemmas}
        standards = {item.fact_sha256: item for item in bundle.standard_candidate_facts}
        for common in bundle.common_lemmas:
            ordered = tuple(standards[item] for item in common.standard_candidate_refs)
            assert tuple(item.profile_position for item in ordered) == tuple(range(len(ordered)))
            expected_minimum = min(
                ordered,
                key=lambda item: tuple(
                    component.orderable_value() for component in item.comparison_key
                ),
            )
            assert common.minimum_standard_candidate_ref == expected_minimum.fact_sha256
            selected_standard = tuple(
                item
                for item in ordered
                if item.catalog_action_id == common.selected_catalog_action_id
            )
            materialized = tuple(
                item for item in ordered if item.materialized_action_id is not None
            )
            if selected_standard:
                assert len(selected_standard) == 1
                assert materialized == selected_standard
                assert materialized[0].materialized_action_id == (
                    common.selected_materialized_action_id
                )
            else:
                assert materialized == ()
        for proof in trusted.proofs:
            root = roots[proof.catalog_action_id]
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
                decode_canonical_f64(root.final_net_cost_bits),
                tuple(commons[item].event_position for item in root.common_lemma_refs),
                tuple(influences[item].classification for item in root.influence_fact_refs),
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
                proof.final_net_cost,
                tuple(item.event_position for item in proof.witnesses),
                tuple(item.classification for item in proof.witnesses),
            ), case.case_id
            for witness, reference, common_reference in zip(
                proof.witnesses,
                root.influence_fact_refs,
                root.common_lemma_refs,
                strict=True,
            ):
                influence = influences[reference]
                common = commons[common_reference]
                assert influence.common_materialized_action_id == witness.common_action_id
                assert influence.branch_materialized_action_id == witness.branch_action_id
                assert influence.state_before_sha256 == witness.state_before_sha256
                assert influence.state_after_sha256 == witness.state_after_sha256
                assert common.selected_materialized_action_id == witness.common_action_id
                assert common.selected_catalog_action_id == influence.common_catalog_action_id
                for legacy in witness.influences:
                    assert legacy.common_decision_key == common.selected_decision_key
                    assert (
                        any(
                            item.remnant_id == legacy.remnant_id
                            for item in influence.rejection_evidence
                        )
                        or any(
                            item.remnant_id == legacy.remnant_id
                            for item in influence.search_evidence
                        )
                        or any(
                            item.selected_remnant_id == legacy.remnant_id
                            for item in influence.competitor_evidence
                        )
                    )
                    if legacy.classification == "policy_dominated":
                        portable = next(
                            item
                            for item in influence.competitor_evidence
                            if item.selected_remnant_id == legacy.remnant_id
                        )
                        assert (
                            portable.candidate_id,
                            portable.materialized_action_id,
                            portable.catalog_action_id,
                            portable.decision_key,
                        ) == (
                            legacy.candidate_id,
                            legacy.competing_action_id,
                            legacy.competing_catalog_action_id,
                            legacy.competing_decision_key,
                        )
        event_modes.update(item.classification for item in bundle.influence_facts)
        common_modes.update(item.evidence_mode for item in bundle.common_lemmas)
        action_counts.add(len(bundle.action_roots))
        common_counts.add(len(bundle.common_lemmas))

    assert len(cases) == 45
    assert event_modes == {"state_rejoin", "no_fit", "policy_dominated", "exact_transition"}
    assert common_modes == {"frontier_no_fit", "exact_replay"}
    assert action_counts == {2, 4, 6}
    assert common_counts == {1, 2, 3}


def test_counted_no_fit_evidence_and_candidate_batches_are_shared_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.baseline import replay
    from yieldforge.oracle.sparse import score_sparse_event

    jagua_path = tmp_path / "counted-jagua"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        ),
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="factored-counted-no-fit",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(area_only,))
    request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    monkeypatch.setattr(
        certificates,
        "run_jagua_generated_prefilter",
        _python_generated_jagua_prefilter,
    )
    monkeypatch.setattr(
        replay,
        "run_jagua_generated_prefilter",
        _python_generated_jagua_prefilter,
    )

    generated = score_unchecked_fact_bundle(_bundle_request(request))
    bundle = generated.bundle
    counted = tuple(
        (lemma, item)
        for lemma in bundle.common_lemmas
        for item in lemma.inventory_classifications
        if item.remnant_id == area_only.remnant.remnant_id
    )

    assert len(counted) == 1
    lemma, classification = counted[0]
    assert classification.classification == "counted_no_fit"
    assert lemma.evidence_mode in {"counted_no_fit", "exact_replay"}
    assert generated.telemetry.counted_inventory_evidence_count == sum(
        item.classification == "counted_no_fit"
        for common in bundle.common_lemmas
        for item in common.inventory_classifications
    )
    translations = {item.fact_sha256: item for item in bundle.translation_batches}
    referenced = tuple(translations[item] for item in classification.translation_batch_refs)
    assert len(referenced) == len(runtime.runtime_candidates[binding.problem_id].candidates)
    assert len(referenced) == len(
        {(item.event_position, item.remnant_id, item.candidate_id) for item in referenced}
    )
    assert all(item.remnant_id == area_only.remnant.remnant_id for item in referenced)
    assert all(root.common_lemma_refs == (lemma.fact_sha256,) for root in bundle.action_roots)
    trusted = score_sparse_event(request)
    roots = {item.catalog_action_id: item for item in bundle.action_roots}
    for proof in trusted.proofs:
        root = roots[proof.catalog_action_id]
        assert root.action_id == proof.action_id
        assert root.final_state_sha256 == proof.final_state_sha256
        assert decode_canonical_f64(root.final_net_cost_bits) == proof.final_net_cost


def test_unsupported_jagua_is_an_exact_survivor_without_count_claims(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.baseline import replay

    jagua_path = tmp_path / "unsupported-jagua"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        ),
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="factored-unsupported-jagua",
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=replace(initial_m7_cursor(runtime.replay_input), inventory=(area_only,)),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    def unsupported(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise JaguaRepresentationError("unsupported test representation")

    monkeypatch.setattr(certificates, "run_jagua_generated_prefilter", unsupported)
    monkeypatch.setattr(replay, "run_jagua_generated_prefilter", unsupported)

    bundle = score_unchecked_fact_bundle(_bundle_request(request)).bundle
    matches = tuple(
        (lemma, item)
        for lemma in bundle.common_lemmas
        for item in lemma.inventory_classifications
        if item.remnant_id == area_only.remnant.remnant_id
    )

    assert len(matches) == 1
    lemma, classification = matches[0]
    assert classification.classification == "exact_survivor"
    assert classification.exact_replay_reason == "unsupported_representation"
    assert classification.frontier_ref is None
    assert classification.candidate_scalar_refs == ()
    assert classification.translation_batch_refs == ()
    assert lemma.evidence_mode == "exact_replay"
    assert lemma.exact_replay_reason in {
        "exact_survivor_unsupported_representation",
        "exact_survivor_mixed",
    }


def test_mixed_passive_prefix_exact_fallback_stays_exact_and_omits_attempted_claims() -> None:
    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "remnant_first-two-match-fit-unequal-same-three"
    )
    bundle = score_unchecked_fact_bundle(_bundle_request(case.request)).bundle
    exact = tuple(
        item for item in bundle.influence_facts if item.classification == "exact_transition"
    )

    assert exact
    assert all(item.evidence_mode == "exact_transition" for item in exact)
    assert all(item.rejection_evidence == () for item in exact)
    assert all(item.search_evidence == () for item in exact)
    assert all(item.competitor_evidence == () for item in exact)
    assert bundle.translation_batches == ()
    assert any(
        item.inventory_delta.removed_remnant_ids or item.inventory_delta.added_remnant_ids
        for item in exact
    )


def test_policy_dominated_mapping_keeps_complete_search_and_policy_bindings() -> None:
    from yieldforge.oracle.sparse import score_sparse_event

    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "myopic_geometry-zero-fit-equal-same-two"
    )
    bundle = score_unchecked_fact_bundle(_bundle_request(case.request)).bundle
    trusted = score_sparse_event(case.request)
    common_by_sha = {item.fact_sha256: item for item in bundle.common_lemmas}
    influence_by_sha = {item.fact_sha256: item for item in bundle.influence_facts}
    roots = {item.catalog_action_id: item for item in bundle.action_roots}
    policy_rows = tuple(
        item for item in bundle.influence_facts if item.classification == "policy_dominated"
    )

    assert policy_rows
    assert bundle.translation_batches
    for influence in policy_rows:
        assert influence.evidence_mode == "policy_dominated_exact_check"
        assert influence.rejection_evidence
        assert influence.search_evidence
        assert influence.competitor_evidence
        assert all(item.result == "fit" for item in influence.search_evidence)
        assert {item.translation_batch_ref for item in influence.search_evidence} <= {
            item.fact_sha256 for item in bundle.translation_batches
        }
    for proof in trusted.proofs:
        root = roots[proof.catalog_action_id]
        for witness, reference, common_reference in zip(
            proof.witnesses,
            root.influence_fact_refs,
            root.common_lemma_refs,
            strict=True,
        ):
            influence = influence_by_sha[reference]
            common = common_by_sha[common_reference]
            for legacy in witness.influences:
                assert legacy.common_decision_key == common.selected_decision_key
                if legacy.classification == "policy_dominated":
                    competitor = next(
                        item
                        for item in influence.competitor_evidence
                        if item.selected_remnant_id == legacy.remnant_id
                    )
                    assert competitor.decision_key == legacy.competing_decision_key
                    assert competitor.catalog_action_id == legacy.competing_catalog_action_id
                    assert competitor.materialized_action_id == legacy.competing_action_id


def test_two_fresh_spawn_workers_emit_identical_strict_semantic_bytes(
    tmp_path: Path,
) -> None:
    source_jagua = tmp_path / "canonical-jagua"
    source_jagua.write_bytes(b"frozen-test-binary")
    source_jagua.chmod(0o700)
    context = multiprocessing.get_context("spawn")
    outputs = tuple(context.Queue() for _ in range(2))
    workers = tuple(
        context.Process(
            target=_spawn_bundle_generation,
            args=(str(source_jagua), output),
        )
        for output in outputs
    )
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30.0)
        assert worker.exitcode == 0
    results = tuple(output.get(timeout=5.0) for output in outputs)

    first, second = results
    assert first[:4] == second[:4]
    assert first[4] != second[4]
    assert len(first[0]) == first[3]
    strict_first = M8UncheckedFactBundleV2.model_validate_json(first[0], strict=True)
    strict_second = M8UncheckedFactBundleV2.model_validate_json(second[0], strict=True)
    assert strict_first == strict_second
    assert strict_first.bundle_sha256 == first[1] == second[1]
    assert tuple(item.fact_sha256 for item in strict_first.action_roots) == first[2] == second[2]


def test_fixed_layers_ignore_construction_order_but_references_keep_semantic_order() -> None:
    from yieldforge.oracle import factored

    bundle = score_unchecked_fact_bundle(_request()).bundle
    forward = factored._FactStore()  # noqa: SLF001
    reverse = factored._FactStore()  # noqa: SLF001
    layers = (
        ("translations", bundle.translation_batches),
        ("scalars", bundle.candidate_scalar_facts),
        ("frontiers", bundle.frontier_facts),
        ("standards", bundle.standard_candidate_facts),
    )
    for attribute, entries in layers:
        setattr(forward, attribute, {index: item for index, item in enumerate(entries)})
        setattr(
            reverse,
            attribute,
            {index: item for index, item in enumerate(reversed(entries))},
        )
    arguments = {
        "provenance": bundle.provenance,
        "common_lemmas": bundle.common_lemmas,
        "influence_facts": tuple(reversed(bundle.influence_facts)),
        "action_roots": tuple(reversed(bundle.action_roots)),
    }
    forward_payload = factored._bundle_payload(store=forward, **arguments)  # noqa: SLF001
    reverse_payload = factored._bundle_payload(store=reverse, **arguments)  # noqa: SLF001

    assert canonical_semantic_json(forward_payload) == canonical_semantic_json(reverse_payload)
    standards = {item.fact_sha256: item for item in bundle.standard_candidate_facts}
    for common in bundle.common_lemmas:
        assert tuple(
            standards[item].profile_position for item in common.standard_candidate_refs
        ) == (tuple(range(len(common.standard_candidate_refs))))
    commons = {item.fact_sha256: item for item in bundle.common_lemmas}
    influences = {item.fact_sha256: item for item in bundle.influence_facts}
    for root in bundle.action_roots:
        assert tuple(commons[item].event_position for item in root.common_lemma_refs) == tuple(
            sorted(commons[item].event_position for item in root.common_lemma_refs)
        )
        assert tuple(influences[item].event_position for item in root.influence_fact_refs) == tuple(
            sorted(influences[item].event_position for item in root.influence_fact_refs)
        )


def test_fact_store_rejects_same_identity_with_different_semantic_content() -> None:
    from yieldforge.oracle import factored
    from yieldforge.oracle.facts import encode_canonical_f64

    scalar = score_unchecked_fact_bundle(_request()).bundle.candidate_scalar_facts[0]
    conflicting = scalar.model_copy(update={"layout_width_bits": encode_canonical_f64(999.0)})
    layer = {}

    assert (
        factored._FactStore._deduplicate(  # noqa: SLF001
            layer,
            "same-identity",
            scalar,
            label="test scalar",
        )
        is scalar
    )
    with pytest.raises(ValueError, match="semantic identity has conflicting content"):
        factored._FactStore._deduplicate(  # noqa: SLF001
            layer,
            "same-identity",
            conflicting,
            label="test scalar",
        )


def test_empty_visible_suffix_builds_terminal_roots_without_common_facts() -> None:
    from yieldforge.oracle.sparse import score_sparse_event

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=KnownOnlyVisibility(runtime.replay_input.instances),
    )
    bundle = score_unchecked_fact_bundle(_bundle_request(oracle_request)).bundle
    trusted = score_sparse_event(oracle_request)

    assert bundle.common_lemmas == ()
    assert bundle.influence_facts == ()
    assert all(item.common_lemma_refs == () for item in bundle.action_roots)
    assert all(item.influence_fact_refs == () for item in bundle.action_roots)
    assert tuple(
        sorted(
            (
                item.catalog_action_id,
                item.action_id,
                decode_canonical_f64(item.final_net_cost_bits),
                item.final_state_sha256,
            )
            for item in bundle.action_roots
        )
    ) == tuple(
        sorted(
            (
                item.catalog_action_id,
                item.action_id,
                item.final_net_cost,
                item.final_state_sha256,
            )
            for item in trusted.proofs
        )
    )


@pytest.mark.parametrize(
    ("first_width", "second_width", "head_sha256"),
    (
        (
            9.0,
            4.0,
            "9ecbcce03b2a537cdaa068bb745f86da55d667381bb0ad46f3853e4129db0589",
        ),
        (
            4.0,
            4.0,
            "70cdd4e4c77c4e20851136169da291954f55d89242fb311bdf70ed4938557d82",
        ),
    ),
)
def test_v1_public_output_hashes_remain_frozen(
    first_width: float,
    second_width: float,
    head_sha256: str,
) -> None:
    from yieldforge.oracle.sparse import score_sparse_event

    request = _request(first_width=first_width, second_width=second_width)
    result = score_sparse_event(request.oracle_request)

    assert semantic_sha256(_jsonable(result)) == head_sha256
