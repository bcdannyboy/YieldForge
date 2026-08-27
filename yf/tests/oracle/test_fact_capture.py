from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from shapely import Polygon, box

from tests.oracle.fixtures import (
    exhaustive_certificate_cases,
    inventory_item,
    two_problem_runtime,
)
from yieldforge.baseline.jagua import (
    JaguaGeneratedPrefilterResult,
    JaguaRepresentationError,
)
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    M7AuthoritativeProofRuntime,
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    run_m7_continuation,
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


def _producer_influence_row(influence):  # type: ignore[no-untyped-def]
    competitor = influence.competitor
    competitor_rank = influence.competitor_rank
    return (
        influence.remnant_id,
        competitor.candidate_id if competitor is not None else None,
        influence.classification,
        influence.legacy_evidence_sha256,
        influence.common_action_id,
        influence.common.catalog_action_id,
        (
            competitor.evidence.action_id
            if competitor is not None and competitor.evidence is not None
            else None
        ),
        competitor.action_id if competitor is not None else None,
        influence.common_rank.decision_key,
        competitor_rank.decision_key if competitor_rank is not None else None,
    )


def _trusted_influence_row(influence):  # type: ignore[no-untyped-def]
    return (
        influence.remnant_id,
        influence.candidate_id,
        influence.classification,
        influence.evidence_sha256,
        influence.common_action_id,
        influence.common_catalog_action_id,
        influence.competing_action_id,
        influence.competing_catalog_action_id,
        influence.common_decision_key,
        influence.competing_decision_key,
    )


def _producer_event_row(event):  # type: ignore[no-untyped-def]
    return (
        event.event_position,
        event.classification,
        event.common_action_id,
        event.branch_action_id,
        event.state_before_sha256,
        event.state_after_sha256,
        tuple(_producer_influence_row(item) for item in event.influences),
    )


def _trusted_event_row(event):  # type: ignore[no-untyped-def]
    return (
        event.event_position,
        event.classification,
        event.common_action_id,
        event.branch_action_id,
        event.state_before_sha256,
        event.state_after_sha256,
        tuple(_trusted_influence_row(item) for item in event.influences),
    )


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


def test_unchecked_counted_capture_skips_duplicate_audit_and_matches_trusted_v1(
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
        token="unchecked-counted-capture",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    verified = runtime.runtime_candidates[binding.problem_id]
    python_generate = certificates.generate_layout_translations
    trusted_audit = certificates.audit_layout_translation_batch

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
    monkeypatch.setattr(replay, "run_jagua_generated_prefilter", fake_generated_prefilter)
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

    monkeypatch.setattr(certificates, "audit_layout_translation_batch", trusted_audit)
    trusted = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )
    assert trusted is not None
    assert trusted.counted_no_fit_inventory == (area_only,)
    assert trusted.fact == captured.common_fact


def test_prepared_count_synthesis_reuses_exact_layouts_and_remnant_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="prepared-count-synthesis",
    )
    legacy = certificates._synthesize_scalar_no_fit_source(  # noqa: SLF001
        runtime,
        event_position=1,
        item=area_only,
        mode=certificates._CommonDerivationMode.UNCHECKED_PORTABLE,  # noqa: SLF001
    )
    original_layout = compiled.prepare_layout_footprint
    original_remnant = compiled.prepare_translation_rejection_remnant
    constructed: list[str] = []
    measured: list[str] = []

    def counted_layout(problem, candidate, config):  # type: ignore[no-untyped-def]
        constructed.append(candidate.candidate_id)
        return original_layout(problem, candidate, config)

    def counted_remnant(remnant):  # type: ignore[no-untyped-def]
        measured.append(remnant.remnant_id)
        return original_remnant(remnant)

    def unexpected_layout(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("prepared count synthesis rebuilt candidate geometry")

    monkeypatch.setattr(compiled, "prepare_layout_footprint", counted_layout)
    monkeypatch.setattr(compiled, "prepare_translation_rejection_remnant", counted_remnant)
    monkeypatch.setattr(certificates, "prepare_layout_footprint", unexpected_layout)
    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        reused = certificates._synthesize_scalar_no_fit_source(  # noqa: SLF001
            runtime,
            event_position=1,
            item=area_only,
            mode=certificates._CommonDerivationMode.UNCHECKED_PORTABLE,  # noqa: SLF001
            prepared_layouts=prepared,
        )

    expected_ids = tuple(
        candidate.candidate_id
        for candidate in runtime.runtime_candidates[binding.problem_id].candidates
    )
    assert tuple(constructed) == expected_ids
    assert measured == [area_only.remnant.remnant_id]
    assert reused == legacy


@pytest.mark.parametrize("source_field", ("event_material", "fit_config"))
def test_prepared_count_synthesis_ignores_transient_live_source_mutation(
    source_field: str,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-count-snapshot-{source_field}",
    )

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        canonical = certificates._synthesize_scalar_no_fit_source(  # noqa: SLF001
            runtime,
            event_position=1,
            item=area_only,
            mode=certificates._CommonDerivationMode.UNCHECKED_PORTABLE,  # noqa: SLF001
            prepared_layouts=prepared,
        )
        if source_field == "event_material":
            owner, field_name = binding.material, "grade"
            mutated = f"{binding.material.grade}-transient"
        else:
            owner, field_name = runtime.replay_input.fit_config, "coordinate_tolerance"
            mutated = 100.0
        original = getattr(owner, field_name)
        object.__setattr__(owner, field_name, mutated)
        try:
            repeated = certificates._synthesize_scalar_no_fit_source(  # noqa: SLF001
                runtime,
                event_position=1,
                item=area_only,
                mode=certificates._CommonDerivationMode.UNCHECKED_PORTABLE,  # noqa: SLF001
                prepared_layouts=prepared,
            )
        finally:
            object.__setattr__(owner, field_name, original)

    assert repeated == canonical


@pytest.mark.parametrize(
    "source_field",
    ("event_material", "fit_config", "search_config"),
)
def test_prepared_common_capture_is_issuance_bound_during_transient_runtime_mutation(
    source_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    event_position = 1
    binding = runtime.replay_input.instances[event_position]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material.model_copy(deep=True),
        token=f"prepared-common-snapshot-{source_field}",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    semantic_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    if source_field == "event_material":
        owner, field_name = binding.material, "grade"
        mutated = f"{binding.material.grade}-transient"
    elif source_field == "fit_config":
        owner, field_name = runtime.replay_input.fit_config, "coordinate_tolerance"
        mutated = 100.0
    else:
        owner, field_name = runtime.replay_input.search_config, "maximum_candidates"
        mutated = 1
    original = getattr(owner, field_name)
    original_standard = certificates._prepared_standard_winner  # noqa: SLF001
    original_fact = certificates._common_transition_fact_from_catalog  # noqa: SLF001
    mutation_observed = False

    def mutate_after_boundary(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = original_standard(*args, **kwargs)
        object.__setattr__(owner, field_name, mutated)
        return result

    def restore_before_exit(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal mutation_observed
        mutation_observed = getattr(owner, field_name) == mutated
        object.__setattr__(owner, field_name, original)
        return original_fact(*args, **kwargs)

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(event_position,),
    ) as prepared:
        canonical = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_runtime_sha256,
            prepared_layouts=prepared,
        )
        monkeypatch.setattr(certificates, "_prepared_standard_winner", mutate_after_boundary)
        monkeypatch.setattr(
            certificates,
            "_common_transition_fact_from_catalog",
            restore_before_exit,
        )
        try:
            repeated = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
                runtime,
                cursor=cursor,
                semantic_runtime_sha256=semantic_runtime_sha256,
                prepared_layouts=prepared,
            )
        finally:
            object.__setattr__(owner, field_name, original)

    assert mutation_observed
    assert repeated == canonical
    assert repr(repeated).encode() == repr(canonical).encode()


def test_prepared_unchecked_counted_capture_is_exact_without_layout_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="prepared-unchecked-counted-capture",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    semantic_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    legacy = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_runtime_sha256,
    )

    def unexpected_layout(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("prepared producer rebuilt candidate geometry")

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        monkeypatch.setattr(certificates, "prepare_layout_footprint", unexpected_layout)
        reused = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_runtime_sha256,
            prepared_layouts=prepared,
        )

    assert reused == legacy
    assert reused.inventory_classifications[0].classification == "counted_no_fit"


def test_prepared_trusted_counted_transition_is_exact_without_layout_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="prepared-trusted-counted-transition",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    semantic_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    legacy = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=semantic_runtime_sha256,
    )

    def unexpected_layout(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("prepared trusted path rebuilt candidate geometry")

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        monkeypatch.setattr(certificates, "prepare_layout_footprint", unexpected_layout)
        reused = certificates._try_derive_m8_common_transition_fact_fast(  # noqa: SLF001
            runtime,
            cursor=cursor,
            semantic_runtime_sha256=semantic_runtime_sha256,
            prepared_layouts=prepared,
        )

    assert reused == legacy
    assert reused is not None
    assert reused.counted_no_fit_inventory == (area_only,)


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
    for record in (common, captured):
        assert record.authority_mode == "unchecked_portable"
        assert "authority_mode" not in {item.name for item in fields(record)}
        with pytest.raises(TypeError, match="authority_mode"):
            replace(record, authority_mode="trusted_local")

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
        unchecked_records = (
            traversal,
            traversal.branches[0],
            traversal.branches[0].events[0],
        )
        for record in unchecked_records:
            assert record.authority_mode == "unchecked_portable"
            assert "authority_mode" not in {item.name for item in fields(record)}
            with pytest.raises(TypeError, match="authority_mode"):
                replace(record, authority_mode="trusted_local")
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


def test_prepared_unchecked_integrity_work_scales_with_events_not_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.baseline import replay
    from yieldforge.oracle import sparse

    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=4.0,
        event_count=3,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    material = runtime.replay_input.instances[0].material
    inventory = tuple(
        sorted(
            (
                inventory_item(
                    box(0, 0, 4, 10),
                    material=material,
                    token=f"integrity-scaling-{index}",
                )
                for index in range(8)
            ),
            key=lambda item: item.remnant.remnant_id,
        )
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=replace(initial_m7_cursor(runtime.replay_input), inventory=inventory),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    candidates = {
        candidate.candidate_id: candidate
        for verified in runtime.runtime_candidates.values()
        for candidate in verified.candidates
    }

    def nonfiltering_prefilter(  # type: ignore[no-untyped-def]
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
            certificates.generate_layout_translations(
                SimpleNamespace(remnant_id=remnant.remnant_id),
                candidates[layout.candidate_id],
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=layout,
                prepared_remnant=remnant,
            )
            for layout in layouts
        )
        return JaguaGeneratedPrefilterResult(
            translation_batches=batches,
            collision_masks=tuple((False,) * len(batch.translations) for batch in batches),
            guarded_query_count=sum(len(batch.translations) for batch in batches),
            jagua_rejection_count=0,
            build_microseconds=0,
            generation_microseconds=0,
            query_microseconds=0,
            wall_seconds=0.0,
        )

    monkeypatch.setattr(replay, "run_jagua_generated_prefilter", nonfiltering_prefilter)
    monkeypatch.setattr(
        certificates,
        "run_jagua_generated_prefilter",
        nonfiltering_prefilter,
    )
    original_executable_identity = certificates._capture_executable_identity  # noqa: SLF001
    original_require_active = M7AuthoritativeProofRuntime.require_active
    calls = {"executable": 0, "authority": 0}

    def counted_executable_identity(runtime):  # type: ignore[no-untyped-def]
        calls["executable"] += 1
        return original_executable_identity(runtime)

    def counted_require_active(self, runtime=None):  # type: ignore[no-untyped-def]
        calls["authority"] += 1
        return original_require_active(self, runtime)

    monkeypatch.setattr(
        certificates,
        "_capture_executable_identity",
        counted_executable_identity,
    )
    monkeypatch.setattr(
        M7AuthoritativeProofRuntime,
        "require_active",
        counted_require_active,
    )

    def capture_counts(*, all_actions: bool) -> tuple[int, int, int, int]:
        with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
            action_ids = tuple(
                item.action_id
                for item in context._catalog.actions  # noqa: SLF001
            )
            nonfallback = next(
                action_id
                for action_id in action_ids
                if action_id != context._fallback_step.descriptor.action_id  # noqa: SLF001
            )
            calls.update(executable=0, authority=0)
            traversal = sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
                context,
                action_ids=action_ids if all_actions else (nonfallback,),
            )
            observed = (
                calls["executable"],
                calls["authority"],
                len(action_ids),
                len(traversal.common_transitions),
            )
        return observed

    one_action = capture_counts(all_actions=False)
    all_actions = capture_counts(all_actions=True)

    assert one_action[2] >= 18
    assert one_action[3] == 2
    assert all_actions[2:] == one_action[2:]
    assert all_actions[:2] == one_action[:2]
    assert all_actions[0] == 4 * all_actions[3]
    assert all_actions[1] == 4 * all_actions[3] + 2


def test_prepared_unchecked_source_guard_is_cleaned_after_branch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    registry_before = dict(  # noqa: SLF001
        certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY
    )

    def fail_branch(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic producer branch failure")

    monkeypatch.setattr(sparse, "_advance_unchecked_branch", fail_branch)
    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        with pytest.raises(RuntimeError, match="synthetic producer branch failure"):
            sparse._capture_prepared_unchecked_traversal(context)  # noqa: SLF001
        assert (  # noqa: SLF001
            certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY == registry_before
        )
    assert (  # noqa: SLF001
        certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY == registry_before
    )


def test_prepared_unchecked_source_guard_is_exactly_scope_and_source_bound() -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    registry_before = dict(  # noqa: SLF001
        certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY
    )

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            context._request.runtime,  # noqa: SLF001
            cursor=context._fallback_step.cursor,  # noqa: SLF001
            semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            runtime_authority=context._authority,  # noqa: SLF001
        )
        with certificates._guard_unchecked_prepared_common_source(  # noqa: SLF001
            context._request.runtime,  # noqa: SLF001
            runtime_authority=context._authority,  # noqa: SLF001
            scope_owner=context,
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            common=common,
        ) as guard:
            certificates._require_unchecked_prepared_source_guard(  # noqa: SLF001
                guard,
                runtime=context._request.runtime,  # noqa: SLF001
                common=common,
                scope_owner=context,
                prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            )
            with pytest.raises(ValueError, match="invalid or inactive"):
                certificates._require_unchecked_prepared_source_guard(  # noqa: SLF001
                    guard,
                    runtime=context._request.runtime,  # noqa: SLF001
                    common=replace(common),
                    scope_owner=context,
                    prepared_layouts=context._prepared_layouts,  # noqa: SLF001
                )
            with pytest.raises(ValueError, match="invalid or inactive"):
                certificates._require_unchecked_prepared_source_guard(  # noqa: SLF001
                    guard,
                    runtime=context._request.runtime,  # noqa: SLF001
                    common=common,
                    scope_owner=object(),
                    prepared_layouts=context._prepared_layouts,  # noqa: SLF001
                )
            with pytest.raises(ValueError, match="invalid or inactive"):
                certificates._require_unchecked_prepared_source_guard(  # noqa: SLF001
                    guard,
                    runtime=context._request.runtime,  # noqa: SLF001
                    common=common,
                    scope_owner=context,
                    prepared_layouts=None,
                )
        with pytest.raises(ValueError, match="invalid or inactive"):
            certificates._require_unchecked_prepared_source_guard(  # noqa: SLF001
                guard,
                runtime=context._request.runtime,  # noqa: SLF001
                common=common,
                scope_owner=context,
                prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            )
    assert (  # noqa: SLF001
        certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY == registry_before
    )


def test_direct_unchecked_passivity_keeps_full_pre_and_post_integrity_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    cursor = _fallback_cursor(runtime)
    common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )
    added = inventory_item(
        box(0, 0, 1, 1),
        material=runtime.replay_input.instances[cursor.next_event_position].material,
        token="direct-integrity-check",
    )
    branch = replace(
        cursor,
        inventory=tuple(
            sorted((*cursor.inventory, added), key=lambda item: item.remnant.remnant_id)
        ),
    )
    original_executable_identity = certificates._capture_executable_identity  # noqa: SLF001
    original_semantic_identity = certificates.m7_semantic_runtime_sha256
    calls = {"executable": 0, "semantic": 0}

    def counted_executable_identity(runtime):  # type: ignore[no-untyped-def]
        calls["executable"] += 1
        return original_executable_identity(runtime)

    def counted_semantic_identity(runtime):  # type: ignore[no-untyped-def]
        calls["semantic"] += 1
        return original_semantic_identity(runtime)

    monkeypatch.setattr(
        certificates,
        "_capture_executable_identity",
        counted_executable_identity,
    )
    monkeypatch.setattr(
        certificates,
        "m7_semantic_runtime_sha256",
        counted_semantic_identity,
    )

    captured = certificates._capture_unchecked_event_passivity(  # noqa: SLF001
        runtime,
        common=common,
        branch_cursor=branch,
    )

    assert captured.passive
    assert calls == {"executable": 2, "semantic": 2}


def test_direct_unchecked_passivity_rechecks_integrity_after_internal_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    cursor = _fallback_cursor(runtime)
    common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )
    added = inventory_item(
        box(0, 0, 1, 1),
        material=runtime.replay_input.instances[cursor.next_event_position].material,
        token="direct-failure-integrity-check",
    )
    branch = replace(
        cursor,
        inventory=tuple(
            sorted((*cursor.inventory, added), key=lambda item: item.remnant.remnant_id)
        ),
    )
    original_executable_identity = certificates._capture_executable_identity  # noqa: SLF001
    original_semantic_identity = certificates.m7_semantic_runtime_sha256
    calls = {"executable": 0, "semantic": 0}

    def counted_executable_identity(runtime):  # type: ignore[no-untyped-def]
        calls["executable"] += 1
        return original_executable_identity(runtime)

    def counted_semantic_identity(runtime):  # type: ignore[no-untyped-def]
        calls["semantic"] += 1
        return original_semantic_identity(runtime)

    def fail_influence(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic influence capture failure")

    monkeypatch.setattr(
        certificates,
        "_capture_executable_identity",
        counted_executable_identity,
    )
    monkeypatch.setattr(
        certificates,
        "m7_semantic_runtime_sha256",
        counted_semantic_identity,
    )
    monkeypatch.setattr(certificates, "_calculate_influence_source", fail_influence)

    with pytest.raises(RuntimeError, match="synthetic influence capture failure"):
        certificates._capture_unchecked_event_passivity(  # noqa: SLF001
            runtime,
            common=common,
            branch_cursor=branch,
        )

    assert calls == {"executable": 2, "semantic": 2}


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


def test_prepared_unchecked_branch_uses_exact_transition_without_complete_archive() -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=9.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=verified.rejection_layouts[:1],
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        common_cursor = replace(context._fallback_step.cursor, inventory=())  # noqa: SLF001
        common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            context._request.runtime,  # noqa: SLF001
            cursor=common_cursor,
            semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            runtime_authority=context._authority,  # noqa: SLF001
        )
        assert common.inventory_classifications == ()
        assert common.standard_candidates
        added = inventory_item(
            box(0, 0, 1, 1),
            material=binding.material,
            token="incomplete-archive-branch-delta",
        )
        branch = sparse._UncheckedBranchState(  # noqa: SLF001
            descriptor=context._catalog.actions[0],  # noqa: SLF001
            initial_step=context._fallback_step,  # noqa: SLF001
            cursor=replace(common_cursor, inventory=(added,)),
        )

        sparse._advance_unchecked_branch(context, branch, common=common)  # noqa: SLF001

    assert len(branch.events) == 1
    event = branch.events[0]
    assert event.classification == "exact_transition"
    assert event.influences == ()
    assert event.attempted_influences == ()
    assert event.exact_step is not None


def test_direct_unchecked_passivity_rejects_cheap_authority_without_complete_archive() -> None:
    runtime = two_problem_runtime(first_width=9.0, second_width=9.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=verified.rejection_layouts[:1],
    )
    common_cursor = replace(_fallback_cursor(runtime), inventory=())
    common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
        runtime,
        cursor=common_cursor,
        semantic_runtime_sha256=m7_semantic_runtime_sha256(runtime),
    )
    added = inventory_item(
        box(0, 0, 1, 1),
        material=binding.material,
        token="incomplete-archive-direct-branch-delta",
    )

    passivity = certificates._capture_unchecked_event_passivity(  # noqa: SLF001
        runtime,
        common=common,
        branch_cursor=replace(common_cursor, inventory=(added,)),
    )

    assert not passivity.passive
    assert passivity.classification is None
    assert passivity.influences == ()
    assert passivity.exact_search_count == 0


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


def test_unchecked_producer_matches_v1_across_bounded_45_case_matrix() -> None:
    from yieldforge.oracle import sparse

    cases = exhaustive_certificate_cases()
    event_classifications: set[str] = set()
    inventory_classifications: set[str] = set()
    influence_classifications: set[str] = set()
    attempted_sequences: set[tuple[str, ...]] = set()
    action_counts: set[int] = set()
    common_counts: set[int] = set()

    for case in cases:
        with sparse._prepare_m8_generator_context(case.request) as context:  # noqa: SLF001
            action_ids = tuple(
                item.action_id
                for item in context._catalog.actions  # noqa: SLF001
            )
            producer = sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
                context,
                action_ids=action_ids,
            )
            producer_rows = []
            for branch in producer.branches:
                terminal = run_m7_continuation(
                    context._request.runtime,  # noqa: SLF001
                    cursor=branch.cursor,
                    stop_event_position=context._stop_event_position,  # noqa: SLF001
                )
                producer_rows.append(
                    (
                        branch.descriptor.action_id,
                        branch.initial_step.event.action.action_id,
                        terminal.final_costs.net_cost,
                        m7_cursor_sha256(branch.cursor),
                        tuple(_producer_event_row(event) for event in branch.events),
                        branch.exact_count,
                        branch.skipped_count,
                        branch.rejection_count,
                        branch.survivor_count,
                        branch.rejoin_count,
                    )
                )
                for event in branch.events:
                    event_classifications.add(event.classification)
                    influence_classifications.update(
                        influence.classification for influence in event.influences
                    )
                    if event.attempted_influences:
                        attempted_sequences.add(
                            tuple(
                                influence.classification for influence in event.attempted_influences
                            )
                        )
            for common in producer.common_transitions:
                inventory_classifications.update(
                    item.classification for item in common.inventory_classifications
                )
            action_counts.add(len(action_ids))
            common_counts.add(len(producer.common_transitions))

        with sparse._prepare_m8_generator_context(case.request) as context:  # noqa: SLF001
            trusted = sparse._score_prepared_certificate_actions(  # noqa: SLF001
                context,
                action_ids=action_ids,
            )
            trusted_rows = tuple(
                (
                    result.score.action_id,
                    result.proof.action_id,
                    result.score.final_net_cost,
                    result.proof.final_state_sha256,
                    tuple(_trusted_event_row(event) for event in result.proof.witnesses),
                    result.exact_branch_event_count,
                    result.skipped_passive_event_count,
                    result.rejection_certificate_count,
                    result.survivor_pair_count,
                    result.state_rejoin_count,
                )
                for result in trusted
            )

        assert tuple(producer_rows) == trusted_rows, case.case_id

    assert len(cases) == 45
    assert action_counts == {2, 4, 6}
    assert common_counts == {1, 2, 3}
    assert event_classifications == {
        "state_rejoin",
        "no_fit",
        "policy_dominated",
        "exact_transition",
    }
    assert inventory_classifications == {"scalar_no_fit", "exact_survivor"}
    assert influence_classifications == {"no_fit", "policy_dominated"}
    assert ("policy_dominated", "policy_not_dominated") in attempted_sequences


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
