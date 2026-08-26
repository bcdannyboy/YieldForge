from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel
from shapely import Polygon, box

from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.baseline.jagua import (
    JaguaGeneratedPrefilterResult,
    JaguaRepresentationError,
)
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_semantic_runtime_sha256,
    select_m7_fallback,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import certificates, facts
from yieldforge.oracle.checker import check_action_proofs
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.sparse import score_certificate_actions, score_sparse_event
from yieldforge.oracle.visibility import FullRealizedVisibility
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest


def _fallback_cursor(runtime):  # type: ignore[no-untyped-def]
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=False)
    selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selection.action_id)
    return apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selection.decision_key,
    ).cursor


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


def test_unchecked_common_capture_is_portable_and_authority_free() -> None:
    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cursor = _fallback_cursor(runtime)
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    captured = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )

    assert type(captured) is certificates.M8UncheckedProducerTransition
    assert captured.common_fact.cursor_before == cursor
    assert captured.portable_transition.cursor_before_sha256 == (
        captured.common_fact.cursor_before_sha256
    )
    assert tuple(item.remnant_id for item in captured.inventory_classifications) == tuple(
        item.remnant.remnant_id for item in cursor.inventory
    )
    assert {item.classification for item in captured.inventory_classifications} == {"scalar_no_fit"}
    assert captured.standard_candidates
    assert all(item.profile is not None for item in captured.standard_candidates)
    assert captured.source.problem.problem_id == (
        runtime.replay_input.instances[cursor.next_event_position].problem_id
    )
    assert (
        captured.source.candidate_set
        == runtime.runtime_candidates[captured.source.problem.problem_id].evidence
    )
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001
    assert not isinstance(captured, certificates.ValidatedCommonTransition)


def test_unchecked_counted_capture_retains_translation_order_without_trusted_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="unchecked-counted-capture",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    verified = runtime.runtime_candidates[binding.problem_id]
    python_generate = certificates.generate_layout_translations

    def fake_generated_prefilter(  # type: ignore[no-untyped-def]
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
            python_generate(
                area_only.remnant,
                candidate,
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=layout,
                prepared_remnant=remnant,
            )
            for candidate, layout in zip(verified.candidates, layouts, strict=True)
        )
        return JaguaGeneratedPrefilterResult(
            translation_batches=batches,
            collision_masks=tuple((True,) * len(batch.translations) for batch in batches),
            guarded_query_count=sum(len(batch.translations) for batch in batches),
            jagua_rejection_count=0,
            build_microseconds=0,
            generation_microseconds=0,
            query_microseconds=0,
            wall_seconds=0.0,
        )

    def unexpected_trusted_audit(**_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("unchecked producer invoked trusted-local count audit")

    monkeypatch.setattr(certificates, "run_jagua_generated_prefilter", fake_generated_prefilter)
    monkeypatch.setattr(certificates, "audit_layout_translation_batch", unexpected_trusted_audit)
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    captured = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )

    assert captured.authority_mode == "unchecked_portable"
    classification = captured.inventory_classifications[0]
    assert classification.classification == "counted_no_fit"
    assert tuple(item.candidate_id for item in classification.translation_batches) == tuple(
        item.candidate_id for item in verified.candidates
    )
    assert all(
        batch.evaluated_candidate_count == len(batch.translations)
        for batch in classification.translation_batches
    )
    assert "collision_mask" not in repr(classification)
    assert captured.source.jagua_executable_sha256 == (
        "sha256:38fe9f08ce341d1d7f00afa16b26917ccd1efa00bd06b8b4c9cc0515bfb47a67"
    )
    assert captured.source.jagua_executable_size_bytes == len(b"frozen-test-binary")
    assert captured.source.jagua_executable_mode_bits == 0o700
    source = replace(
        classification.translation_batches[0].source,
        translations=((0.0, 0.0), (1.0, 1.0)),
        generated_candidate_count=2,
    )
    reordered = certificates.M8UncheckedTranslationBatchCapture.from_source(
        replace(source, translations=tuple(reversed(source.translations)))
    )
    assert reordered.translations == ((1.0, 1.0), (0.0, 0.0))
    assert reordered.candidate_id == source.candidate_id
    assert not isinstance(reordered, certificates.ValidatedCommonTransition)
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001


def test_producer_only_passivity_retains_full_influence_preimage() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = _fallback_cursor(runtime)
    common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )
    survivor = common.inventory_classifications[0]
    assert survivor.classification == "exact_survivor"
    assert survivor.exact_replay_reason == "frontier_survivor"
    assert survivor.frontier is not None
    assert survivor.candidate_rejection_layouts
    assert survivor.translation_batches == ()
    added = inventory_item(
        box(0, 0, 3, 20),
        material=runtime.replay_input.instances[cursor.next_event_position].material,
        token="unchecked-influence-preimage",
    )
    branch = replace(
        cursor,
        inventory=tuple(
            sorted(
                (*cursor.inventory, added),
                key=lambda item: item.remnant.remnant_id,
            )
        ),
    )
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    captured = certificates._capture_unchecked_event_passivity(  # noqa: SLF001
        runtime,
        common=common,
        branch_cursor=branch,
    )

    assert captured.authority_mode == "unchecked_portable"
    assert captured.passive
    assert captured.classification == "no_fit"
    assert captured.branch_after is not None
    assert len(captured.influences) == 1
    influence = captured.influences[0]
    assert influence.direction == "added"
    assert influence.rejections
    assert influence.searches == ()
    assert influence.competitor is None
    assert influence.competitor_context is None
    assert influence.legacy_evidence_sha256 == (
        f"sha256:{semantic_sha256(influence.legacy_evidence_payload)}"
    )
    assert influence.legacy_evidence_payload["cheap_rejections"]
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001

    trusted_common = certificates.build_validated_m8_common_transition(
        runtime,
        cursor=cursor,
    )
    assert id(trusted_common) in certificates._VALIDATED_COMMON_REGISTRY  # noqa: SLF001
    try:
        trusted = certificates.certify_event_passivity(
            runtime,
            common=trusted_common,
            branch_cursor=branch,
        )
        assert trusted.passive
        assert trusted.witness is not None
        assert trusted.witness.influences[0].evidence_sha256 == (influence.legacy_evidence_sha256)
    finally:
        certificates._release_validated_common_transition(trusted_common)  # noqa: SLF001
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001

    with pytest.raises(ValueError, match="validated common transition"):
        certificates.certify_event_passivity(
            runtime,
            common=common,  # type: ignore[arg-type]
            branch_cursor=branch,
        )


def test_event_major_producer_traversal_reuses_one_unchecked_common_per_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    def forbidden_trusted_capability(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("producer traversal created a trusted common capability")

    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001
    original_unchecked_capture = sparse._capture_unchecked_m8_common_transition  # noqa: SLF001

    def registry_checked_capture(*args, **kwargs):  # type: ignore[no-untyped-def]
        assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001
        result = original_unchecked_capture(*args, **kwargs)
        assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001
        return result

    monkeypatch.setattr(
        sparse,
        "build_validated_m8_common_transition_in_context",
        forbidden_trusted_capability,
    )
    monkeypatch.setattr(
        sparse,
        "_capture_unchecked_m8_common_transition",
        registry_checked_capture,
    )
    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        action_ids = tuple(
            item.action_id
            for item in context._catalog.actions  # noqa: SLF001
        )
        traversal = sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
            context,
            action_ids=action_ids,
        )

        assert len(traversal.common_transitions) == len(context._visible)  # noqa: SLF001
        assert len(traversal.branches) == len(action_ids)
        assert all(
            len(branch.events) == len(traversal.common_transitions) for branch in traversal.branches
        )
        assert all(
            common.authority_mode == "unchecked_portable" for common in traversal.common_transitions
        )
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001


def test_prepared_unchecked_jagua_traversal_uses_snapshot_semantic_identity(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import sparse

    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        assert m7_semantic_runtime_sha256(context._request.runtime) != (  # noqa: SLF001
            context._authority.semantic_sha256  # noqa: SLF001
        )
        nonfallback_action_id = next(
            item.action_id
            for item in context._catalog.actions  # noqa: SLF001
            if item.action_id != context._fallback_step.descriptor.action_id  # noqa: SLF001
        )
        traversal = sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
            context,
            action_ids=(nonfallback_action_id,),
        )

        assert len(traversal.common_transitions) == len(context._visible)  # noqa: SLF001
        assert traversal.common_transitions[0].common_fact.semantic_runtime_sha256 == (
            context._authority.semantic_sha256  # noqa: SLF001
        )
        assert traversal.common_transitions[0].source.jagua_executable_sha256 == (
            "sha256:38fe9f08ce341d1d7f00afa16b26917ccd1efa00bd06b8b4c9cc0515bfb47a67"
        )
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001


def test_producer_exact_fallback_retains_the_nonwinning_influence_preimage() -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.REMNANT_FIRST,
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        binding = context._request.runtime.replay_input.instances[1]  # noqa: SLF001
        too_small = inventory_item(
            box(0, 0, 1, 1),
            material=binding.material,
            token="nonpassive-common",
        )
        common_cursor = replace(
            context._fallback_step.cursor,  # noqa: SLF001
            inventory=(too_small,),
        )
        common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            context._request.runtime,  # noqa: SLF001
            cursor=common_cursor,
            semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
        )
        fitting = inventory_item(
            box(0, 0, 4, 10),
            material=binding.material,
            token="nonpassive-competitor",
        )
        branch_cursor = replace(
            common_cursor,
            inventory=tuple(sorted((too_small, fitting), key=lambda item: item.remnant.remnant_id)),
        )
        branch = sparse._UncheckedBranchState(  # noqa: SLF001
            descriptor=context._catalog.actions[0],  # noqa: SLF001
            initial_step=context._fallback_step,  # noqa: SLF001
            cursor=branch_cursor,
        )

        sparse._advance_unchecked_branch(context, branch, common=common)  # noqa: SLF001

    event = branch.events[0]
    assert event.classification == "exact_transition"
    assert event.influences == ()
    assert len(event.attempted_influences) == 1
    attempted = event.attempted_influences[0]
    assert attempted.classification == "policy_not_dominated"
    assert attempted.competitor is not None
    assert attempted.competitor_context is not None
    assert attempted.competitor_rank is not None
    assert attempted.searches
    assert attempted.translation_batches


def test_exact_fallback_retains_passive_prefix_before_terminal_competitor() -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.REMNANT_FIRST,
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        binding = context._request.runtime.replay_input.instances[1]  # noqa: SLF001
        common_cursor = replace(context._fallback_step.cursor, inventory=())  # noqa: SLF001
        common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            context._request.runtime,  # noqa: SLF001
            cursor=common_cursor,
            semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            runtime_authority=context._authority,  # noqa: SLF001
        )
        small_options = tuple(
            inventory_item(
                box(0, 0, 1, 1),
                material=binding.material,
                token=f"passive-prefix-{index}",
            )
            for index in range(8)
        )
        fitting_options = tuple(
            inventory_item(
                box(0, 0, 4, 10),
                material=binding.material,
                token=f"terminal-competitor-{index}",
            )
            for index in range(8)
        )
        too_small, fitting = next(
            (small, survivor)
            for small in small_options
            for survivor in fitting_options
            if small.remnant.remnant_id < survivor.remnant.remnant_id
        )
        branch_cursor = replace(common_cursor, inventory=(too_small, fitting))
        branch = sparse._UncheckedBranchState(  # noqa: SLF001
            descriptor=context._catalog.actions[0],  # noqa: SLF001
            initial_step=context._fallback_step,  # noqa: SLF001
            cursor=branch_cursor,
        )

        sparse._advance_unchecked_branch(context, branch, common=common)  # noqa: SLF001

    event = branch.events[0]
    assert event.classification == "exact_transition"
    assert tuple(item.classification for item in event.attempted_influences) == (
        "no_fit",
        "policy_not_dominated",
    )


def test_policy_competitor_capture_retains_search_translation_and_context_preimages() -> None:
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
    )
    cursor = _fallback_cursor(runtime)
    common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )
    added = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[cursor.next_event_position].material,
        token="unchecked-policy-competitor",
    )
    branch = replace(
        cursor,
        inventory=tuple(
            sorted((*cursor.inventory, added), key=lambda item: item.remnant.remnant_id)
        ),
    )

    result = certificates._capture_unchecked_event_passivity(  # noqa: SLF001
        runtime,
        common=common,
        branch_cursor=branch,
    )

    assert result.passive
    influence = next(
        item for item in result.influences if item.remnant_id == added.remnant.remnant_id
    )
    assert influence.classification == "policy_dominated"
    assert influence.competitor is not None
    assert influence.competitor_context is not None
    assert influence.competitor_rank is not None
    assert len(influence.translation_batches) == len(influence.searches)
    for search, batch in zip(influence.searches, influence.translation_batches, strict=True):
        assert batch.candidate_id == search.candidate_id
        assert batch.generated_candidate_count == search.generated_candidate_count
        assert batch.duplicate_candidate_count == search.duplicate_candidate_count
        if search.translation is not None:
            assert batch.translations[search.evaluated_candidate_count - 1] == search.translation
    batch = influence.translation_batches[0]
    translation_perturbations = (
        {"candidate_id": "foreign-candidate"},
        {"translations": (*batch.translations, (999.0, 999.0))},
        {"generated_candidate_count": batch.generated_candidate_count + 1},
        {"duplicate_candidate_count": batch.duplicate_candidate_count + 1},
        {"evaluated_candidate_count": batch.evaluated_candidate_count - 1},
        {"budget_truncated": not batch.budget_truncated},
    )
    for perturbation in translation_perturbations:
        with pytest.raises(ValueError, match="translation capture differs"):
            replace(batch, **perturbation)
    with pytest.raises(ValueError, match="influence source bindings differ"):
        replace(influence, state_before_sha256="foreign-state")
    assert influence.competitor_context is not None
    with pytest.raises(ValueError, match="competitor bindings differ"):
        replace(
            influence,
            competitor_context=replace(
                influence.competitor_context,
                action_id="foreign-action",
            ),
        )


def test_unsupported_jagua_representation_is_an_explicit_exact_survivor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.baseline import replay

    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="unchecked-unsupported-representation",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))

    def unsupported(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise JaguaRepresentationError("unsupported test representation")

    monkeypatch.setattr(certificates, "run_jagua_generated_prefilter", unsupported)
    monkeypatch.setattr(replay, "run_jagua_generated_prefilter", unsupported)
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    captured = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )

    classification = captured.inventory_classifications[0]
    assert classification.classification == "exact_survivor"
    assert classification.exact_replay_reason == "unsupported_representation"
    assert classification.frontier is None
    assert classification.candidate_rejection_layouts == ()
    assert classification.translation_batches == ()
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001


def test_unchecked_capture_failure_never_registers_a_trusted_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="unchecked-capture-failure",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    def failed_source_capture(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("source generation failed")

    monkeypatch.setattr(
        certificates,
        "run_jagua_generated_prefilter",
        failed_source_capture,
    )

    with pytest.raises(RuntimeError, match="source generation failed"):
        certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
        )
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001


def test_unchecked_capture_rejects_jagua_mutation_during_source_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="unchecked-jagua-mutation",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    verified = runtime.runtime_candidates[binding.problem_id]
    original_generate = certificates.generate_layout_translations

    def mutating_prefilter(  # type: ignore[no-untyped-def]
        _executable,
        *,
        remnant,
        layouts,
        fit_config,
        search_config,
        container_guard,
    ):
        batches = tuple(
            original_generate(
                area_only.remnant,
                candidate,
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=layout,
                prepared_remnant=remnant,
            )
            for candidate, layout in zip(verified.candidates, layouts, strict=True)
        )
        jagua_path.write_bytes(b"mutated-test-binary")
        return JaguaGeneratedPrefilterResult(
            translation_batches=batches,
            collision_masks=tuple((True,) * len(batch.translations) for batch in batches),
            guarded_query_count=sum(len(batch.translations) for batch in batches),
            jagua_rejection_count=0,
            build_microseconds=0,
            generation_microseconds=0,
            query_microseconds=0,
            wall_seconds=0.0,
        )

    monkeypatch.setattr(
        certificates,
        "run_jagua_generated_prefilter",
        mutating_prefilter,
    )
    registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001

    with pytest.raises(ValueError, match="executable changed"):
        certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
        )
    assert certificates._VALIDATED_COMMON_REGISTRY == registry_before  # noqa: SLF001


def test_missing_scalar_archive_is_explicitly_unsupported_not_frontier_provenance() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = _fallback_cursor(runtime)
    binding = runtime.replay_input.instances[cursor.next_event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=(),
    )

    captured = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )

    classification = captured.inventory_classifications[0]
    assert classification.classification == "exact_survivor"
    assert classification.exact_replay_reason == "unsupported_representation"
    assert classification.frontier is None
    assert classification.candidate_rejection_layouts == ()


def test_common_capture_rejects_an_omitted_standard_nonwinner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    too_small = inventory_item(
        box(0, 0, 1, 1),
        material=binding.material,
        token="omitted-standard-nonwinner",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(too_small,))
    original = certificates.enumerate_m7_standard_only_catalog

    def omit_nonwinner(*args, **kwargs):  # type: ignore[no-untyped-def]
        catalog = original(*args, **kwargs)
        return replace(
            catalog,
            actions=catalog.actions[:-1],
            contexts=catalog.contexts[:-1],
        )

    monkeypatch.setattr(
        certificates,
        "enumerate_m7_standard_only_catalog",
        omit_nonwinner,
    )

    with pytest.raises(ValueError, match="complete ordered standard candidates"):
        certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
        )


def test_influence_search_consumes_each_captured_translation_sequence_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    )
    cursor = _fallback_cursor(runtime)
    common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )
    added = inventory_item(
        box(0, 0, 4, 10),
        material=runtime.replay_input.instances[cursor.next_event_position].material,
        token="single-source-influence",
    )
    branch = replace(
        cursor,
        inventory=tuple(
            sorted((*cursor.inventory, added), key=lambda item: item.remnant.remnant_id)
        ),
    )
    original = certificates.generate_layout_translations
    captured_candidate_ids: list[str] = []

    def tracked_generation(*args, **kwargs):  # type: ignore[no-untyped-def]
        batch = original(*args, **kwargs)
        captured_candidate_ids.append(batch.candidate_id)
        return batch

    monkeypatch.setattr(certificates, "generate_layout_translations", tracked_generation)

    result = certificates._capture_unchecked_event_passivity(  # noqa: SLF001
        runtime,
        common=common,
        branch_cursor=branch,
    )

    verified = runtime.runtime_candidates[
        runtime.replay_input.instances[cursor.next_event_position].problem_id
    ]
    assert result.passive
    assert tuple(captured_candidate_ids) == tuple(item.candidate_id for item in verified.candidates)


def test_common_capture_retains_complete_nonwinners_source_ids_and_semantic_time() -> None:
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=2.0,
            storage_cost_per_area_hour=3.0,
            return_handling_cost_per_remnant=4.0,
            retrieval_handling_cost_per_remnant=5.0,
            scrap_credit_per_area=0.25,
        ),
    )
    binding = runtime.replay_input.instances[1]
    too_small = inventory_item(
        box(0, 0, 1, 1),
        material=binding.material,
        token="complete-standard-source",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(too_small,))

    captured = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )

    verified = runtime.runtime_candidates[binding.problem_id]
    assert tuple(item.profile_position for item in captured.standard_candidates) == tuple(
        range(len(verified.candidates))
    )
    assert tuple(item.profile.candidate_id for item in captured.standard_candidates) == tuple(
        item.candidate_id for item in verified.candidates
    )
    assert len(captured.standard_candidates) > 1
    selected = next(
        item
        for item in captured.standard_candidates
        if item.descriptor.action_id == captured.common_fact.step.descriptor.action_id
    )
    assert selected.rank == captured.common_fact.policy_rank
    assert selected.rank == min(item.rank for item in captured.standard_candidates)
    assert selected.policy_immediate_net_cost == (
        captured.common_fact.step.selected_context.immediate_net_cost
    )
    assert selected.selected_replay_event_net_cost == (
        captured.common_fact.step.event.delta_costs.net_cost
    )
    assert selected.policy_immediate_net_cost != selected.selected_replay_event_net_cost
    assert captured.common_fact.step.event.delta_costs.storage_cost > 0.0
    scalar = captured.inventory_classifications[0]
    assert scalar.scalar_witness is not None
    with pytest.raises(ValueError, match="scalar witness differs"):
        replace(scalar, remnant_area=scalar.remnant_area + 1.0)
    with pytest.raises(ValueError, match="standard candidate source is inconsistent"):
        replace(
            selected,
            context=replace(
                selected.context,
                immediate_net_cost=selected.context.immediate_net_cost + 1.0,
            ),
            policy_immediate_net_cost=selected.policy_immediate_net_cost + 1.0,
        )

    source = captured.source
    assert source.replay_input == runtime.replay_input
    assert source.rules == runtime.rules
    assert source.verified_candidates == verified
    assert source.candidate_set.archives == verified.evidence.archives
    assert all(item.source_transform_sha256 for item in verified.rejection_layouts)
    portable = captured.portable_transition
    event = captured.common_fact.step.event
    assert portable.cursor_before.current_time == facts.encode_canonical_utc(cursor.current_time)
    assert portable.cursor_before.previous_release == facts.encode_canonical_utc(
        cursor.previous_release
    )
    assert portable.event.occurred_at == facts.encode_canonical_utc(event.occurred_at)
    assert portable.event.storage_interval_start == facts.encode_canonical_utc(
        event.storage_interval_start
    )
    assert portable.event.storage_interval_end == facts.encode_canonical_utc(
        event.storage_interval_end
    )
    assert portable.cursor_after.current_time == facts.encode_canonical_utc(
        captured.common_fact.step.cursor.current_time
    )
    assert portable.cursor_after.previous_release == facts.encode_canonical_utc(
        captured.common_fact.step.cursor.previous_release
    )
    assert portable.cursor_before.inventory[0].entered_at == facts.encode_canonical_utc(
        too_small.entered_at
    )
    assert portable.selected_context.immediate_net_cost_bits == facts.encode_canonical_f64(
        selected.policy_immediate_net_cost
    )
    assert portable.event.delta_costs.storage_cost_bits == facts.encode_canonical_f64(
        event.delta_costs.storage_cost
    )
    assert portable.event.delta_costs.net_cost_bits == facts.encode_canonical_f64(
        event.delta_costs.net_cost
    )


def test_v1_public_apis_never_return_unchecked_producer_records() -> None:
    from yieldforge.oracle import checker, sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    sparse_result = score_sparse_event(request)
    action_ids = (sparse_result.decision.selected_action_id,)
    action_results = score_certificate_actions(request, action_ids=action_ids)
    checks = check_action_proofs(request, sparse_result.proofs)

    assert type(sparse_result) is sparse.M8SparseResult
    assert all(type(item) is sparse.M8CertificateActionResult for item in action_results)
    assert all(type(item) is checker.M8ProofCheckResult for item in checks)
    assert "M8UncheckedProducerTransition" not in repr(sparse_result)
    assert "M8UncheckedProducerTransition" not in repr(action_results)
    assert "M8UncheckedProducerTransition" not in repr(checks)
    assert "M8UncheckedProducerTransition" not in certificates.__all__
    assert "_capture_unchecked_m8_common_transition" not in certificates.__all__
    assert not hasattr(checker, "_capture_unchecked_m8_common_transition")


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
def test_v1_capture_disabled_output_matches_frozen_head(
    first_width: float,
    second_width: float,
    head_sha256: str,
) -> None:
    runtime = two_problem_runtime(
        first_width=first_width,
        second_width=second_width,
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    result = score_sparse_event(request)

    assert semantic_sha256(_jsonable(result)) == head_sha256
