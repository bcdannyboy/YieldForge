from __future__ import annotations

import gc
import weakref
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
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

_C0_TEST_SCOPE_STACK: ContextVar[ExitStack | None] = ContextVar(
    "yieldforge_c0_test_scope_stack",
    default=None,
)


class _ExplodingWeakRef(weakref.ref):
    def __call__(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected exploding C0 weak reference")


class _FlipHash:
    armed = False

    def __hash__(self) -> int:
        if self.armed:
            raise RuntimeError("injected drifting C0 ledger hash")
        return id(self)


@pytest.fixture(autouse=True)
def _manage_c0_test_scopes():  # type: ignore[no-untyped-def]
    with ExitStack() as stack:
        token = _C0_TEST_SCOPE_STACK.set(stack)
        try:
            yield
        finally:
            _C0_TEST_SCOPE_STACK.reset(token)


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
def test_prepared_count_synthesis_rejects_transient_live_source_mutation(
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
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="source lease payload",
            ):
                certificates._synthesize_scalar_no_fit_source(  # noqa: SLF001
                    runtime,
                    event_position=1,
                    item=area_only,
                    mode=certificates._CommonDerivationMode.UNCHECKED_PORTABLE,  # noqa: SLF001
                    prepared_layouts=prepared,
                )
        finally:
            object.__setattr__(owner, field_name, original)

    assert canonical
    assert getattr(owner, field_name) == original


def _c0_branch_row(
    *,
    branch_id: int,
    item,  # type: ignore[no-untyped-def]
    direction: str = "added",
):  # type: ignore[no-untyped-def]
    return certificates._C0BranchRemnantRow(  # noqa: SLF001
        branch_id=branch_id,
        direction=direction,
        item=item,
    )


def _c0_branch_authorities(  # type: ignore[no-untyped-def]
    runtime,
    rows,
    *,
    authority_limit: int | None = None,
):
    """Build exact cursor/delta authorities for scalar-wrapper tests."""

    from yieldforge.oracle import sparse

    stack = _C0_TEST_SCOPE_STACK.get()
    if stack is None:
        raise AssertionError("C0 test authority requires its managed scope fixture")
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    context = stack.enter_context(sparse._prepare_m8_generator_context(request))  # noqa: SLF001
    context_runtime = context._request.runtime  # noqa: SLF001
    start = context._request.cursor  # noqa: SLF001
    catalog = context._catalog  # noqa: SLF001
    common_transition = sparse._capture_unchecked_m8_common_transition(  # noqa: SLF001
        context_runtime,
        cursor=context._fallback_step.cursor,  # noqa: SLF001
        semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
        prepared_layouts=context._prepared_layouts,  # noqa: SLF001
        runtime_authority=context._authority,  # noqa: SLF001
    )
    prepared_source_guard = stack.enter_context(
        certificates._guard_unchecked_prepared_common_source(  # noqa: SLF001
            context_runtime,
            runtime_authority=context._authority,  # noqa: SLF001
            scope_owner=context,
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            common=common_transition,
        )
    )
    common = common_transition.common_fact.cursor_before
    common_by_id = {item.remnant.remnant_id: item for item in common.inventory}
    source_branches = []
    for branch_id in tuple(dict.fromkeys(row.branch_id for row in rows)):
        descriptor = catalog.actions[branch_id]
        root = apply_m7_action_descriptor(
            context_runtime,
            cursor=start,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
        )
        branch_by_id = dict(common_by_id)
        for row in rows:
            if row.branch_id != branch_id:
                continue
            remnant_id = row.item.remnant.remnant_id
            if row.direction == "added":
                branch_by_id[remnant_id] = row.item
            else:
                branch_by_id.pop(remnant_id)
        branch_before = replace(
            common,
            inventory=tuple(branch_by_id[key] for key in sorted(branch_by_id)),
        )
        source_branch = sparse._UncheckedBranchState(  # noqa: SLF001
            descriptor=descriptor,
            initial_step=root,
            cursor=branch_before,
        )
        source_branches.append(source_branch)
    branch_batch = stack.enter_context(
        sparse._activate_prepared_c0_branch_batch(  # noqa: SLF001
            context,
            common=common_transition,
            prepared_source_guard=prepared_source_guard,
            branches=tuple(source_branches),
        )
    )
    source_scope = certificates._issue_c0_prepared_frontier_generator_scope(  # noqa: SLF001
        branch_batch,
    )
    return tuple(
        certificates._issue_c0_prepared_frontier_branch_authority(  # noqa: SLF001
            source_scope=source_scope,
            source_branch=source_branch,
        )
        for source_branch in (
            source_branches if authority_limit is None else source_branches[:authority_limit]
        )
    )


def _c0_ownership_registry_key_sets():  # type: ignore[no-untyped-def]
    """Return every C0 child ownership registry for exact leak assertions."""

    return (
        set(certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY),  # noqa: SLF001
        set(certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY),  # noqa: SLF001
        set(certificates._C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY),  # noqa: SLF001
        set(certificates._C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY),  # noqa: SLF001
        set(certificates._C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY),  # noqa: SLF001
    )


def test_prepared_columnar_batch_groups_multi_item_branches_fail_closed() -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    tolerance = runtime.replay_input.fit_config.coordinate_tolerance
    too_small = inventory_item(
        box(0, 0, 3, 9),
        material=binding.material.model_copy(deep=True),
        token="columnar-too-small",
    )
    mismatched = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(update={"grade": "other-grade"}),
        token="columnar-material-mismatch",
    )
    equality_survivor = inventory_item(
        box(0, 0, 5, 10.0 - tolerance),
        material=binding.material.model_copy(deep=True),
        token="columnar-tolerance-equality",
    )
    rows = (
        _c0_branch_row(branch_id=0, item=too_small),
        _c0_branch_row(branch_id=0, item=mismatched),
        _c0_branch_row(branch_id=1, item=equality_survivor),
    )
    authorities = _c0_branch_authorities(runtime, rows)

    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            result = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=authorities,
                branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                rows=rows,
            )
    report = profiler.report()

    assert tuple(item.binding.row_id for item in result.rows) == (0, 1, 2)
    assert tuple(item.binding.branch_id for item in result.rows) == (0, 0, 1)
    assert all(item.supported for item in result.rows)
    assert tuple(item.all_impossible for item in result.rows) == (True, True, False)
    assert result.rows[1].binding.material_matches is False
    assert (
        result.rows[2].binding.remnant_height + result.rows[2].binding.coordinate_tolerance == 10.0
    )
    assert tuple(item.branch_id for item in result.branches) == (0, 1)
    assert tuple(item.row_ids for item in result.branches) == ((0, 1), (2,))
    assert tuple(item.compact_eligible for item in result.branches) == (True, False)
    first_binding = result.rows[0].binding
    first_authority = authorities[0]
    assert first_binding.event_position == first_authority.event_position == 1
    assert first_binding.catalog_action_id == first_authority.catalog_action_id
    assert first_binding.root_action_id == first_authority.root_action_id
    assert first_binding.branch_before_sha256 == m7_cursor_sha256(first_authority.branch_before)
    assert first_binding.common_before_sha256 == m7_cursor_sha256(first_authority.common_before)
    assert first_binding.direction == "added"
    assert first_binding.delta == result.branches[0].delta
    assert first_binding.measurement.remnant_id == too_small.remnant.remnant_id
    assert first_binding.measurement_sha256.startswith("sha256:")
    assert first_binding.partition_sha256.startswith("sha256:")
    assert first_binding.rejection_layout_candidate_ids == first_binding.candidate_ids
    assert len(first_binding.rejection_layout_sha256s) == len(first_binding.candidate_ids)
    assert "frontier_columnar_batch" in tuple(item.name for item in report.phases)


@pytest.mark.parametrize(
    "corruption",
    (
        "duplicate",
        "reordered",
        "problem_id",
        "problem_sha256",
        "candidate_set_id",
        "candidate_set_sha256",
        "fit_config_sha256",
    ),
)
def test_production_bundle_rejects_rejection_archive_integrity_before_branch_advance(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.factored import (
        M8UncheckedBundleRequest,
        score_unchecked_fact_bundle,
    )
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    layouts = verified.rejection_layouts
    assert len(layouts) >= 2
    if corruption == "duplicate":
        corrupted = (layouts[0], layouts[0], *layouts[2:])
    elif corruption == "reordered":
        corrupted = tuple(reversed(layouts))
    else:
        replacement = {
            "problem_id": "corrupt-problem",
            "problem_sha256": "sha256:" + "1" * 64,
            "candidate_set_id": "corrupt-candidate-set",
            "candidate_set_sha256": "sha256:" + "2" * 64,
            "fit_config_sha256": "sha256:" + "3" * 64,
        }[corruption]
        corrupted = tuple(replace(layout, **{corruption: replacement}) for layout in layouts)
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=corrupted,
    )
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    freeze_sha256 = "sha256:" + "a" * 64
    request = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id="yfm7freeze-" + "a" * 24,
        freeze_sha256=freeze_sha256,
    )
    advance_calls = 0
    original_advance = sparse._advance_unchecked_branch  # noqa: SLF001

    def counted_advance(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal advance_calls
        advance_calls += 1
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(sparse, "_advance_unchecked_branch", counted_advance)
    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            score_unchecked_fact_bundle(request)

    counts = profiler.report().counts
    assert advance_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_public_unchecked_bundle_captures_cursor_inventory_before_prepared_source() -> None:
    import inspect
    from typing import ClassVar

    from shapely import from_wkb

    from yieldforge.oracle import compiled
    from yieldforge.oracle.factored import (
        M8UncheckedBundleRequest,
        score_unchecked_fact_bundle,
    )
    from yieldforge.oracle.profiling import activate_m8_profile
    from yieldforge.replay.contracts import InventoryItem
    from yieldforge.reuse.contracts import RemnantStock

    class EvilItem(InventoryItem):
        forged: ClassVar[RemnantStock]
        calls: ClassVar[int] = 0

        def __eq__(self, other):  # type: ignore[no-untyped-def]
            return isinstance(other, InventoryItem) and InventoryItem.model_dump(
                self,
                mode="python",
            ) == InventoryItem.model_dump(other, mode="python")

        def __getattribute__(self, name: str):  # type: ignore[no-untyped-def]
            if name == "remnant":
                caller = inspect.currentframe().f_back
                if (
                    caller is not None
                    and caller.f_code.co_name == "_capture_unchecked_m8_common_transition"
                ):
                    type(self).calls += 1
                    return type(self).forged
            return super().__getattribute__(name)

    runtime = two_problem_runtime(
        first_width=4.0,
        second_width=9.0,
        event_count=3,
    )
    cursor = _fallback_cursor(runtime)
    for binding in runtime.replay_input.instances[cursor.next_event_position :]:
        verified = runtime.runtime_candidates[binding.problem_id]
        runtime.runtime_candidates[binding.problem_id] = replace(
            verified,
            rejection_layouts=(),
        )
    binding = runtime.replay_input.instances[cursor.next_event_position]
    original = cursor.inventory[0]
    polygon = from_wkb(bytes.fromhex(original.remnant.geometry.wkb_hex))
    mismatch = binding.material.model_copy(update={"grade": "wrong-grade"})
    EvilItem.forged = inventory_item(
        polygon,
        material=mismatch,
        token="public-unsupported-forge",
    ).remnant
    evil = EvilItem.model_validate(original.model_dump(mode="python"), strict=True)
    cursor = replace(cursor, inventory=(evil,))
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    request = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id="yfm7freeze-" + "a" * 24,
        freeze_sha256="sha256:" + "a" * 64,
    )
    baseline = tuple(  # noqa: SLF001
        key for key, _value in compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_items()
    )

    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            score_unchecked_fact_bundle(request)

    assert EvilItem.calls == 0
    counts = profiler.report().counts
    assert counts["facts"] == 0
    assert counts["actions"] == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert tuple(  # noqa: SLF001
        key for key, _value in compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_items()
    ) == baseline


def test_production_traversal_owns_prepared_capability_drift_before_branch_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    advance_calls = 0
    original_advance = sparse._advance_unchecked_branch  # noqa: SLF001

    def counted_advance(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal advance_calls
        advance_calls += 1
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(sparse, "_advance_unchecked_branch", counted_advance)
    with activate_m8_profile() as profiler:
        with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
            capability_id = id(context._prepared_layouts)  # noqa: SLF001
            record = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
                capability_id
            )
            try:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    sparse._capture_prepared_unchecked_traversal(context)  # noqa: SLF001
            finally:
                registry = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
                registry[capability_id] = record
                assert registry._repair_untrusted_mutations()  # noqa: SLF001
                assert registry._seal_repaired_state()  # noqa: SLF001

    counts = profiler.report().counts
    assert advance_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


@pytest.mark.parametrize("branch_kind", ("fallback", "nonfallback"))
def test_mid_traversal_prepared_registry_loss_precedes_any_branch_mutation(
    monkeypatch: pytest.MonkeyPatch,
    branch_kind: str,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_capture = sparse._capture_unchecked_m8_common_transition  # noqa: SLF001
    original_frozen = sparse.apply_m7_frozen_action_evidence
    original_descriptor = sparse.apply_m7_action_descriptor
    registry_dropped = False
    frozen_calls = 0
    descriptor_calls = 0

    def counted_frozen(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal frozen_calls
        if registry_dropped:
            frozen_calls += 1
        return original_frozen(*args, **kwargs)

    def counted_descriptor(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal descriptor_calls
        if registry_dropped:
            descriptor_calls += 1
        return original_descriptor(*args, **kwargs)

    monkeypatch.setattr(sparse, "apply_m7_frozen_action_evidence", counted_frozen)
    monkeypatch.setattr(sparse, "apply_m7_action_descriptor", counted_descriptor)
    with activate_m8_profile() as profiler:
        with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
            fallback_id = context._fallback_step.descriptor.action_id  # noqa: SLF001
            action_id = (
                fallback_id
                if branch_kind == "fallback"
                else next(
                    item.action_id
                    for item in context._catalog.actions  # noqa: SLF001
                    if item.action_id != fallback_id
                )
            )
            capability_id = id(context._prepared_layouts)  # noqa: SLF001
            record = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_get(  # noqa: SLF001
                capability_id
            )

            def drop_after_capture(*args, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal registry_dropped
                common = original_capture(*args, **kwargs)
                compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(capability_id)  # noqa: SLF001
                registry_dropped = True
                return common

            monkeypatch.setattr(
                sparse,
                "_capture_unchecked_m8_common_transition",
                drop_after_capture,
            )
            try:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
                        context,
                        action_ids=(action_id,),
                    )
            finally:
                registry_dropped = False
                registry = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
                registry[capability_id] = record
                assert registry._repair_untrusted_mutations()  # noqa: SLF001
                assert registry._seal_repaired_state()  # noqa: SLF001

    counts = profiler.report().counts
    assert frozen_calls == 0
    assert descriptor_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_late_second_branch_failure_commits_no_prepared_branch_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_initial = sparse._initial_unchecked_branch  # noqa: SLF001
    original_advance = sparse._advance_unchecked_branch  # noqa: SLF001
    captured_originals = []
    initial_snapshots = []
    advance_calls = 0
    first_staged_snapshot = None
    successful_advances = 0
    registry_record = None
    registry_key = None
    before_registry_keys = (
        set(compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY),  # noqa: SLF001
        set(compiled._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY),  # noqa: SLF001
        set(compiled._PREPARED_REMNANT_AUTHORITY_REGISTRY),  # noqa: SLF001
        set(compiled._PREPARED_FRONTIER_INPUT_REGISTRY),  # noqa: SLF001
    )

    def snapshot(branch):  # type: ignore[no-untyped-def]
        return (
            branch.descriptor,
            branch.initial_step,
            branch.cursor,
            tuple(branch.events),
            branch.exact_count,
            branch.skipped_count,
            branch.rejection_count,
            branch.survivor_count,
            branch.rejoin_count,
        )

    def capture_initial(*args, **kwargs):  # type: ignore[no-untyped-def]
        branch = original_initial(*args, **kwargs)
        captured_originals.append(branch)
        initial_snapshots.append(snapshot(branch))
        return branch

    def fail_after_first_stage(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal advance_calls, first_staged_snapshot, registry_record
        nonlocal successful_advances
        advance_calls += 1
        fallback_increment = original_advance(*args, **kwargs)
        successful_advances += 1
        if successful_advances == 1:
            assert fallback_increment == 1
            first_staged_snapshot = snapshot(args[1])
            assert registry_key is not None
            registry_record = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
                registry_key
            )
        return fallback_increment

    monkeypatch.setattr(sparse, "_initial_unchecked_branch", capture_initial)
    monkeypatch.setattr(sparse, "_advance_unchecked_branch", fail_after_first_stage)
    with activate_m8_profile() as profiler:
        with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
            fallback_id = context._fallback_step.descriptor.action_id  # noqa: SLF001
            nonfallback_id = next(
                item.action_id
                for item in context._catalog.actions  # noqa: SLF001
                if item.action_id != fallback_id
            )
            action_ids = (nonfallback_id, fallback_id)
            registry_key = id(context._prepared_layouts)  # noqa: SLF001
            try:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
                        context,
                        action_ids=action_ids,
                    )
            finally:
                if registry_record is not None:
                    registry = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY  # noqa: SLF001
                    registry[registry_key] = registry_record
                    assert registry._repair_untrusted_mutations()  # noqa: SLF001
                    assert registry._seal_repaired_state()  # noqa: SLF001

    assert advance_calls == 2
    assert successful_advances == 1
    assert first_staged_snapshot is not None
    assert first_staged_snapshot != initial_snapshots[0]
    assert tuple(snapshot(branch) for branch in captured_originals) == tuple(initial_snapshots)
    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert (
        set(compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY),  # noqa: SLF001
        set(compiled._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY),  # noqa: SLF001
        set(compiled._PREPARED_REMNANT_AUTHORITY_REGISTRY),  # noqa: SLF001
        set(compiled._PREPARED_FRONTIER_INPUT_REGISTRY),  # noqa: SLF001
    ) == before_registry_keys


def test_mid_traversal_prepared_snapshot_drift_is_integrity_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_capture = sparse._capture_unchecked_m8_common_transition  # noqa: SLF001
    with activate_m8_profile() as profiler:
        with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
            fallback_id = context._fallback_step.descriptor.action_id  # noqa: SLF001
            action_id = next(
                item.action_id
                for item in context._catalog.actions  # noqa: SLF001
                if item.action_id != fallback_id
            )
            record = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_get(  # noqa: SLF001
                id(context._prepared_layouts)  # noqa: SLF001
            )
            event_position = context._fallback_step.cursor.next_event_position  # noqa: SLF001
            event_binding = record.source_runtime_snapshot.runtime.replay_input.instances[
                event_position
            ]
            snapshot_verified = record.source_runtime_snapshot.runtime.runtime_candidates[
                event_binding.problem_id
            ]
            original_layouts = snapshot_verified.rejection_layouts

            def corrupt_after_capture(*args, **kwargs):  # type: ignore[no-untyped-def]
                common = original_capture(*args, **kwargs)
                object.__setattr__(snapshot_verified, "rejection_layouts", ())
                return common

            monkeypatch.setattr(
                sparse,
                "_capture_unchecked_m8_common_transition",
                corrupt_after_capture,
            )
            try:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
                        context,
                        action_ids=(action_id,),
                    )
            finally:
                object.__setattr__(snapshot_verified, "rejection_layouts", original_layouts)

    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_public_factored_generator_preserves_typed_body_error_over_cleanup_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, factored, sparse
    from yieldforge.oracle.factored import M8UncheckedBundleRequest
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    request = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id="yfm7freeze-" + "b" * 24,
        freeze_sha256="sha256:" + "b" * 64,
    )
    sentinel = compiled.M8PreparedFrontierIntegrityError(
        "M8 prepared frontier integrity differs: public factored sentinel"
    )

    def corrupt_body(context, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        sparse._PREPARED_GENERATOR_REGISTRY.pop(id(context))  # noqa: SLF001
        compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(  # noqa: SLF001
            id(context._prepared_layouts)  # noqa: SLF001
        )
        raise sentinel

    monkeypatch.setattr(factored, "_capture_prepared_unchecked_traversal", corrupt_body)
    with activate_m8_profile() as profiler:
        with pytest.raises(compiled.M8PreparedFrontierIntegrityError) as captured:
            factored.score_unchecked_fact_bundle(request)

    assert captured.value is sentinel
    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_public_factored_generator_drains_malformed_source_guard_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, factored, sparse
    from yieldforge.oracle.factored import M8UncheckedBundleRequest
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    request = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id="yfm7freeze-" + "c" * 24,
        freeze_sha256="sha256:" + "c" * 64,
    )
    registry_before = dict(  # noqa: SLF001
        certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY
    )
    original_capture = sparse._capture_unchecked_event_passivity  # noqa: SLF001
    corrupted = False

    def corrupt_guard(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal corrupted
        guard = kwargs["prepared_source_guard"]
        if not corrupted:
            certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY[id(guard)] = object()  # type: ignore[assignment]  # noqa: SLF001
            corrupted = True
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(sparse, "_capture_unchecked_event_passivity", corrupt_guard)
    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="unchecked source guard",
        ):
            factored.score_unchecked_fact_bundle(request)

    assert corrupted
    assert (  # noqa: SLF001
        certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY == registry_before
    )
    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


@pytest.mark.parametrize("corruption", ("omitted", "extra", "direction", "reordered"))
def test_prepared_columnar_batch_requires_exact_delta_row_bijection(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    impossible = inventory_item(
        box(0, 0, 3, 9),
        material=binding.material.model_copy(deep=True),
        token="columnar-bijection-impossible",
    )
    survivor = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="columnar-bijection-survivor",
    )
    canonical_rows = tuple(
        sorted(
            (
                _c0_branch_row(branch_id=0, item=impossible),
                _c0_branch_row(branch_id=0, item=survivor),
            ),
            key=lambda row: row.item.remnant.remnant_id,
        )
    )
    authorities = _c0_branch_authorities(runtime, canonical_rows)
    if corruption == "omitted":
        submitted = canonical_rows[:1]
    elif corruption == "extra":
        foreign = inventory_item(
            box(0, 0, 2, 8),
            material=binding.material.model_copy(deep=True),
            token="columnar-bijection-foreign",
        )
        submitted = (*canonical_rows, _c0_branch_row(branch_id=0, item=foreign))
    elif corruption == "direction":
        submitted = (
            replace(canonical_rows[0], direction="removed"),
            canonical_rows[1],
        )
    else:
        submitted = tuple(reversed(canonical_rows))
    before = tuple(authority.branch_before for authority in authorities)

    def forbidden_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("invalid delta rows entered the C0 kernel")

    initial_batches = set(compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY)  # noqa: SLF001
    initial_leases = set(compiled._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY)  # noqa: SLF001
    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            with compiled._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ) as prepared:
                monkeypatch.setattr(
                    certificates,
                    "certify_frontier_impossible_batch",
                    forbidden_kernel,
                )
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=authorities,
                    branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                    rows=submitted,
                )

    counts = profiler.report().counts
    assert tuple(authority.branch_before for authority in authorities) == before
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert set(compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY) == initial_batches  # noqa: SLF001
    assert set(compiled._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY) == initial_leases  # noqa: SLF001


def test_prepared_columnar_batch_full_mixed_delta_is_not_compact_eligible() -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    impossible = inventory_item(
        box(0, 0, 3, 9),
        material=binding.material.model_copy(deep=True),
        token="columnar-full-mixed-impossible",
    )
    survivor = inventory_item(
        box(0, 0, 5, 11),
        material=binding.material.model_copy(deep=True),
        token="columnar-full-mixed-survivor",
    )
    rows = tuple(
        sorted(
            (
                _c0_branch_row(branch_id=0, item=impossible),
                _c0_branch_row(branch_id=0, item=survivor),
            ),
            key=lambda row: row.item.remnant.remnant_id,
        )
    )
    authorities = _c0_branch_authorities(runtime, rows)

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        result = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
            runtime,
            prepared_layouts=prepared,
            event_position=1,
            branches=authorities,
            branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
            rows=rows,
        )

    by_remnant = {item.binding.item.remnant.remnant_id: item.all_impossible for item in result.rows}
    assert by_remnant == {
        impossible.remnant.remnant_id: True,
        survivor.remnant.remnant_id: False,
    }
    assert result.branches[0].supported is True
    assert result.branches[0].compact_eligible is False


@pytest.mark.parametrize("corruption", ("lease_payload", "event_material"))
def test_prepared_columnar_batch_rejects_issued_lease_semantic_drift_before_kernel(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-lease-drift-{corruption}",
        ),
    )
    canonical_authorities = _c0_branch_authorities(runtime, (row,))
    repeated_authorities = _c0_branch_authorities(runtime, (row,))
    kernel_calls = 0

    def forbidden_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("corrupt lease payload entered the C0 kernel")

    with activate_m8_profile() as profiler:
        with ExitStack() as stack:
            if corruption == "lease_payload":
                stack.enter_context(
                    pytest.raises(
                        compiled.M8PreparedFrontierIntegrityError,
                        match="prepared frontier integrity",
                    )
                )
            prepared = stack.enter_context(
                compiled._prepare_translation_layout_batch(  # noqa: SLF001
                    runtime,
                    event_positions=(1,),
                )
            )
            canonical = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=canonical_authorities,
                branch_batch=canonical_authorities[0]._branch_batch,  # noqa: SLF001
                rows=(row,),
            )
            assert canonical.branches[0].compact_eligible is False
            lease_entries = (
                compiled._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY._trusted_items()  # noqa: SLF001
            )
            lease_id, leased = next(
                (lease_id, value) for lease_id, value in lease_entries if value.prepared is prepared
            )
            if corruption == "lease_payload":
                original_source_binding = leased.source_binding
                original_rejection_problem = leased.rejection_problem
                forged_source_binding = replace(
                    original_source_binding,
                    rejection_layout_candidate_ids=(
                        original_source_binding.rejection_layout_candidate_ids[0],
                    ),
                    rejection_layout_sha256s=(original_source_binding.rejection_layout_sha256s[0],),
                )
                object.__setattr__(leased, "source_binding", forged_source_binding)
                object.__setattr__(leased, "rejection_problem", None)

                def restore_lease_registry() -> None:
                    object.__setattr__(
                        leased,
                        "source_binding",
                        original_source_binding,
                    )
                    object.__setattr__(
                        leased,
                        "rejection_problem",
                        original_rejection_problem,
                    )

                restore = restore_lease_registry
            else:
                material = leased.event_materials[1]
                assert material is not None
                original_grade = material.grade
                object.__setattr__(material, "grade", f"{original_grade}-forged")
                restore = lambda: object.__setattr__(material, "grade", original_grade)  # noqa: E731
            monkeypatch.setattr(
                certificates,
                "certify_frontier_impossible_batch",
                forbidden_kernel,
            )
            try:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                        runtime,
                        prepared_layouts=prepared,
                        event_position=1,
                        branches=repeated_authorities,
                        branch_batch=repeated_authorities[0]._branch_batch,  # noqa: SLF001
                        rows=(row,),
                    )
            finally:
                restore()

    counts = profiler.report().counts
    assert kernel_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_prepared_columnar_batch_rejects_coherently_recommitted_remnant_cache_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token="columnar-remnant-cache-drift",
        ),
    )
    authorities = _c0_branch_authorities(runtime, (row,))
    kernel_calls = 0

    def forbidden_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("corrupt remnant cache entered the C0 kernel")

    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            canonical = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=authorities,
                branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                rows=(row,),
            )
            assert canonical.branches[0].compact_eligible is False
            record = compiled._PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_get(  # noqa: SLF001
                id(prepared)
            )
            measurements = dict(record.remnant_measurements)
            commitments = dict(record.remnant_commitments)
            snapshots = dict(record.remnant_snapshots)
            authorities_before = dict(prepared._remnant_authorities)  # noqa: SLF001
            key, measured = next(iter(record.remnant_measurements.items()))
            forged = replace(measured, bounds=(0.0, 0.0, 1.0, 1.0))
            forged_commitment = compiled._prepared_remnant_measurement_commitment(  # noqa: SLF001
                key,
                forged,
            )
            record.remnant_measurements[key] = forged
            record.remnant_commitments[key] = forged_commitment
            record.remnant_snapshots.clear()
            record.remnant_snapshots[forged_commitment] = (
                compiled._prepared_remnant_key_values(key),  # noqa: SLF001
                compiled._prepared_remnant_measurement_values(forged),  # noqa: SLF001
            )
            prepared._remnant_authorities[key] = (forged, forged_commitment)  # noqa: SLF001
            monkeypatch.setattr(
                certificates,
                "certify_frontier_impossible_batch",
                forbidden_kernel,
            )
            try:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                        runtime,
                        prepared_layouts=prepared,
                        event_position=1,
                        branches=authorities,
                        branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                        rows=(row,),
                    )
            finally:
                record.remnant_measurements.clear()
                record.remnant_measurements.update(measurements)
                record.remnant_commitments.clear()
                record.remnant_commitments.update(commitments)
                record.remnant_snapshots.clear()
                record.remnant_snapshots.update(snapshots)
                prepared._remnant_authorities.clear()  # noqa: SLF001
                prepared._remnant_authorities.update(authorities_before)  # noqa: SLF001

    counts = profiler.report().counts
    assert kernel_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


@pytest.mark.parametrize("corruption", ("event_material", "fit_config", "frontier"))
def test_prepared_columnar_batch_rejects_post_validation_source_toctou(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-post-validation-toctou-{corruption}",
        ),
    )
    authorities = _c0_branch_authorities(runtime, (row,))
    original = compiled.prepare_translation_rejection_remnant
    restore: list[tuple[object, str, object]] = []
    injected = False

    def corrupt(remnant):  # type: ignore[no-untyped-def]
        nonlocal injected
        measured = original(remnant)
        if injected:
            return measured
        injected = True
        lease_entries = (
            compiled._PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY._trusted_items()  # noqa: SLF001
        )
        leased = next(leased for _lease_id, leased in lease_entries if leased.prepared is prepared)
        if corruption == "event_material":
            owner = leased.event_materials[1]
            assert owner is not None
            field_name = "grade"
            mutated = f"{owner.grade}-forged"
        elif corruption == "fit_config":
            owner = leased.fit_config
            field_name = "coordinate_tolerance"
            mutated = 0.0
        else:
            owner = leased.rejection_problem
            assert owner is not None
            field_name = "frontier"
            mutated = compiled.build_pareto_frontier(
                tuple(
                    replace(
                        member,
                        area=member.area * 100.0,
                        width=member.width * 100.0,
                        height=member.height * 100.0,
                    )
                    for member in owner.frontier.members
                )
            )
        restore.append((owner, field_name, getattr(owner, field_name)))
        object.__setattr__(owner, field_name, mutated)
        return measured

    monkeypatch.setattr(compiled, "prepare_translation_rejection_remnant", corrupt)
    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            try:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                        runtime,
                        prepared_layouts=prepared,
                        event_position=1,
                        branches=authorities,
                        branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                        rows=(row,),
                    )
            finally:
                for owner, field_name, original_value in reversed(restore):
                    object.__setattr__(owner, field_name, original_value)

    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_prepared_columnar_batch_rejects_coherently_rehashed_input_dto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token="columnar-coherently-rehashed-input-dto",
        ),
    )
    authorities = _c0_branch_authorities(runtime, (row,))
    original_inputs = certificates._prepared_frontier_batch_inputs  # noqa: SLF001
    kernel_calls = 0

    def forged_inputs(*args, **kwargs):  # type: ignore[no-untyped-def]
        inputs = original_inputs(*args, **kwargs)
        measured = inputs.measurements[0]
        forged = replace(measured, bounds=(0.0, 0.0, 1.0, measured.area))
        object.__setattr__(inputs, "measurements", (forged,))
        object.__setattr__(inputs, "content_sha256", "")
        object.__setattr__(
            inputs,
            "content_sha256",
            compiled._prepared_frontier_batch_inputs_sha256(inputs),  # noqa: SLF001
        )
        return inputs

    def forbidden_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("coherently forged input DTO entered the C0 kernel")

    monkeypatch.setattr(certificates, "_prepared_frontier_batch_inputs", forged_inputs)
    monkeypatch.setattr(
        certificates,
        "certify_frontier_impossible_batch",
        forbidden_kernel,
    )
    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=authorities,
                    branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                    rows=(row,),
                )

    counts = profiler.report().counts
    assert kernel_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_prepared_columnar_batch_rejects_kernel_query_input_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 5, 11),
            material=binding.material.model_copy(deep=True),
            token="columnar-kernel-query-mutation",
        ),
    )
    original = certificates.certify_frontier_impossible_batch

    def mutating_kernel(columns, queries):  # type: ignore[no-untyped-def]
        for query in queries:
            object.__setattr__(query, "remnant_width", 1.0)
            object.__setattr__(query, "remnant_height", 1.0)
        return original(columns, queries)

    authorities = _c0_branch_authorities(runtime, (row,))
    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            monkeypatch.setattr(
                certificates,
                "certify_frontier_impossible_batch",
                mutating_kernel,
            )
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=authorities,
                    branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                    rows=(row,),
                )

    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


def test_empty_archive_does_not_mask_prepared_layout_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=(),
    )
    original = compiled.prepare_translation_rejection_layout

    def corrupt_layout(layout):  # type: ignore[no-untyped-def]
        return replace(original(layout), candidate_id="corrupt-candidate")

    monkeypatch.setattr(compiled, "prepare_translation_rejection_layout", corrupt_layout)
    with activate_m8_profile() as profiler:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            with compiled._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ):
                raise AssertionError("corrupt empty archive yielded a prepared capability")

    counts = profiler.report().counts
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0


@pytest.mark.parametrize(
    "corruption",
    ("event", "catalog_action", "root_action", "common_cursor", "branch_cursor"),
)
def test_prepared_columnar_batch_rejects_mutated_branch_authority(
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-authority-{corruption}",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    if corruption == "event":
        change = {"event_position": authority.event_position + 1}
    elif corruption == "catalog_action":
        change = {"catalog_action_id": authority.catalog_action_id + "-corrupt"}
    elif corruption == "root_action":
        change = {"root_action_id": authority.root_action_id + "-corrupt"}
    elif corruption == "common_cursor":
        change = {
            "common_before": replace(
                authority.common_before,
                timestamp_subsequence=authority.common_before.timestamp_subsequence + 1,
            )
        }
    else:
        change = {
            "branch_before": replace(
                authority.branch_before,
                timestamp_subsequence=authority.branch_before.timestamp_subsequence + 1,
            )
        }

    if corruption in {"event", "catalog_action", "root_action"}:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            replace(authority, **change)
        return

    corrupted = replace(authority, **change)
    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=(corrupted,),
                branch_batch=corrupted._branch_batch,  # noqa: SLF001
                rows=(row,),
            )


def test_prepared_columnar_batch_rejects_correlated_alternate_root_authority() -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-correlated-alternate-root",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    start = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(
        runtime,
        cursor=start,
        materialize_standard_actions=True,
    )
    alternate_descriptor = next(
        item for item in catalog.actions if item.action_id != authority.catalog_action_id
    )
    alternate = apply_m7_action_descriptor(
        runtime,
        cursor=start,
        catalog=catalog,
        descriptor=alternate_descriptor,
        decision_key=(f"m8_hypothetical_action_id={alternate_descriptor.action_id}",),
    )
    forged = replace(
        authority,
        catalog_action_id=alternate_descriptor.action_id,
        root_action_id=alternate.event.action.action_id,
        root_step=alternate,
    )

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=(forged,),
                branch_batch=forged._branch_batch,  # noqa: SLF001
                rows=(row,),
            )


def test_prepared_columnar_branch_authority_requires_generator_scope() -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    start = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(
        runtime,
        cursor=start,
        materialize_standard_actions=True,
    )
    descriptor = catalog.actions[0]
    root = apply_m7_action_descriptor(
        runtime,
        cursor=start,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
    )
    source_branch = SimpleNamespace(
        descriptor=descriptor,
        initial_step=root,
        cursor=root.cursor,
    )

    with pytest.raises(
        compiled.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._issue_c0_prepared_frontier_branch_authority(  # noqa: SLF001
            branch_id=0,
            event_position=root.cursor.next_event_position,
            source_branch=source_branch,
            common_before=root.cursor,
        )


def test_prepared_columnar_generator_scope_rejects_raw_runtime_and_branches() -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    start = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(
        runtime,
        cursor=start,
        materialize_standard_actions=True,
    )
    descriptor = catalog.actions[0]
    root = apply_m7_action_descriptor(
        runtime,
        cursor=start,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
    )
    source_branch = SimpleNamespace(
        descriptor=descriptor,
        initial_step=root,
        cursor=root.cursor,
    )

    with pytest.raises(
        compiled.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._issue_c0_prepared_frontier_generator_scope(  # noqa: SLF001
            runtime,
            root_cursor=start,
            common_before=root.cursor,
            source_branches=(source_branch,),
        )


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_authority",
        "missing_scope",
        "replaced_child_owners",
        "missing_parent_authority",
        "replaced_parent_authority",
        "missing_parent_registry",
        "malformed_authority_record",
        "malformed_scope_record",
        "malformed_parent_authority_reference",
        "malformed_parent_authority_ledger",
        "malformed_parent_scope_ledger",
        "malformed_parent_reference",
    ),
)
def test_prepared_columnar_corrupt_child_authority_drains_event_scope_immediately(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-child-authority-cleanup-{corruption}",
        ),
    )
    peer_row = _c0_branch_row(
        branch_id=1,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-child-authority-cleanup-peer-{corruption}",
        ),
    )
    authority, peer = _c0_branch_authorities(runtime, (row, peer_row))
    authority_id = id(authority)
    peer_id = id(peer)
    authority_record = certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
        authority_id
    ]
    branch_batch = authority_record.branch_batch
    scope_id = id(authority_record.source_scope)
    owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(branch_batch)]  # noqa: SLF001
    restore_parent_registry = False
    restore_owner_attribute: tuple[str, object] | None = None
    if corruption == "missing_authority":
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.pop(  # noqa: SLF001
            authority_id
        )
    elif corruption == "missing_scope":
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.pop(  # noqa: SLF001
            scope_id
        )
    elif corruption == "replaced_child_owners":
        foreign_owner = object()
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
            authority_id
        ] = replace(authority_record, branch_batch=foreign_owner)
        scope_record = certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY[  # noqa: SLF001
            scope_id
        ]
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY[  # noqa: SLF001
            scope_id
        ] = replace(scope_record, branch_batch=foreign_owner)
    elif corruption == "missing_parent_authority":
        owner_record.branch_authority_references.pop(authority_id)
    elif corruption == "replaced_parent_authority":
        peer_record = certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
            peer_id
        ]
        owner_record.branch_authority_references[authority_id] = peer_record.reference
    elif corruption == "missing_parent_registry":
        sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY.pop(id(branch_batch))  # noqa: SLF001
        restore_parent_registry = True
    elif corruption == "malformed_authority_record":
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
            authority_id
        ] = object()
    elif corruption == "malformed_scope_record":
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY[  # noqa: SLF001
            scope_id
        ] = object()
    elif corruption == "malformed_parent_authority_reference":
        owner_record.branch_authority_references[authority_id] = object()  # type: ignore[assignment]
    elif corruption == "malformed_parent_authority_ledger":
        restore_owner_attribute = (
            "branch_authority_references",
            owner_record.branch_authority_references,
        )
        object.__setattr__(owner_record, "branch_authority_references", object())
    elif corruption == "malformed_parent_scope_ledger":
        restore_owner_attribute = (
            "generator_scope_references",
            owner_record.generator_scope_references,
        )
        object.__setattr__(owner_record, "generator_scope_references", object())
    else:
        restore_owner_attribute = ("reference", owner_record.reference)
        object.__setattr__(owner_record, "reference", object())
    kernel_calls = 0

    def unexpected_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("missing authority reached the C0 numeric kernel")

    monkeypatch.setattr(
        certificates,
        "certify_frontier_impossible_batch",
        unexpected_kernel,
    )
    try:
        with activate_m8_profile() as profiler:
            with compiled._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ) as prepared:
                with pytest.raises(
                    compiled.M8PreparedFrontierIntegrityError,
                    match="prepared frontier integrity",
                ):
                    certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                        runtime,
                        prepared_layouts=prepared,
                        event_position=1,
                        branches=(authority, peer),
                        branch_batch=authority._branch_batch,  # noqa: SLF001
                        rows=(row, peer_row),
                    )
    finally:
        if restore_parent_registry:
            sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(branch_batch)] = (  # noqa: SLF001
                owner_record
            )
        if restore_owner_attribute is not None:
            field_name, original_ledger = restore_owner_attribute
            object.__setattr__(owner_record, field_name, original_ledger)
            if type(original_ledger) is dict:
                original_ledger.clear()

    counts = profiler.report().counts
    assert kernel_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert authority_id not in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert peer_id not in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert scope_id not in (
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
    )
    assert not any(
        registered.branch_batch is branch_batch
        for registered in (
            certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.values()  # noqa: SLF001
        )
    )
    assert not any(
        registered.branch_batch is branch_batch
        for registered in (
            certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY.values()  # noqa: SLF001
        )
    )


def test_prepared_columnar_foreign_parent_injection_cannot_consume_peer_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row_a = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-foreign-parent-a",
        ),
    )
    row_b = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-foreign-parent-b",
        ),
    )
    authority_a = _c0_branch_authorities(runtime, (row_a,))[0]
    authority_b = _c0_branch_authorities(runtime, (row_b,))[0]
    batch_a = authority_a._branch_batch  # noqa: SLF001
    batch_b = authority_b._branch_batch  # noqa: SLF001
    record_a = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch_a)]  # noqa: SLF001
    record_b = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch_b)]  # noqa: SLF001
    authority_b_record = certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
        id(authority_b)
    ]
    scope_a_id = next(iter(record_a.generator_scope_references))
    scope_b_id = id(authority_b_record.source_scope)
    record_a.branch_authority_references[id(authority_b)] = record_b.branch_authority_references[
        id(authority_b)
    ]
    record_a.generator_scope_references[scope_b_id] = record_b.generator_scope_references[
        scope_b_id
    ]
    original_kernel = certificates.certify_frontier_impossible_batch
    kernel_calls = 0

    def unexpected_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("foreign parent ownership reached the C0 numeric kernel")

    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            monkeypatch.setattr(
                certificates,
                "certify_frontier_impossible_batch",
                unexpected_kernel,
            )
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=(authority_a,),
                    branch_batch=batch_a,
                    rows=(row_a,),
                )
            monkeypatch.setattr(
                certificates,
                "certify_frontier_impossible_batch",
                original_kernel,
            )
            peer_result = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=(authority_b,),
                branch_batch=batch_b,
                rows=(row_b,),
            )

    counts = profiler.report().counts
    assert kernel_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert peer_result.branches[0].branch_id == 0
    assert id(authority_a) not in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert scope_a_id not in (
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
    )
    assert id(authority_b) not in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert scope_b_id not in (
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
    )


def test_prepared_columnar_immutable_owners_survive_reverse_injection_and_missing_peer_parent() -> (
    None
):
    from yieldforge.oracle import sparse

    baseline = _c0_ownership_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    rows = tuple(
        _c0_branch_row(
            branch_id=0,
            item=inventory_item(
                box(0, 0, 3, 9),
                material=binding.material.model_copy(deep=True),
                token=f"columnar-immutable-owner-{suffix}",
            ),
        )
        for suffix in ("local", "foreign")
    )
    authority_a = _c0_branch_authorities(runtime, rows[:1])[0]
    authority_b = _c0_branch_authorities(runtime, rows[1:])[0]
    batch_a = authority_a._branch_batch  # noqa: SLF001
    batch_b = authority_b._branch_batch  # noqa: SLF001
    record_a = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch_a)]  # noqa: SLF001
    record_b = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch_b)]  # noqa: SLF001
    scope_a_id = next(iter(record_a.generator_scope_references))
    scope_b_id = next(iter(record_b.generator_scope_references))
    index_a = certificates._C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY[  # noqa: SLF001
        id(batch_a)
    ]
    index_b = certificates._C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY[  # noqa: SLF001
        id(batch_b)
    ]

    record_a.branch_authority_references[id(authority_b)] = record_b.branch_authority_references[
        id(authority_b)
    ]
    record_a.generator_scope_references[scope_b_id] = record_b.generator_scope_references[
        scope_b_id
    ]
    record_b.branch_authority_references[id(authority_a)] = record_a.branch_authority_references[
        id(authority_a)
    ]
    record_b.generator_scope_references[scope_a_id] = record_a.generator_scope_references[
        scope_a_id
    ]
    index_a.authority_ids.pop(id(authority_a))
    index_a.scope_ids.pop(scope_a_id)
    index_a.authority_ids[id(authority_b)] = None
    index_a.scope_ids[scope_b_id] = None
    index_b.authority_ids[id(authority_a)] = None
    index_b.scope_ids[scope_a_id] = None
    sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY.pop(id(batch_b))  # noqa: SLF001

    with pytest.raises(
        certificates.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._release_c0_prepared_frontier_generator_scopes(  # noqa: SLF001
            batch_a,
            owner_record=record_a,
        )

    assert id(authority_a) not in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert scope_a_id not in (
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
    )
    assert id(authority_b) in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert scope_b_id in (
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
    )

    record_b.branch_authority_references.pop(id(authority_a), None)
    record_b.generator_scope_references.pop(scope_a_id, None)
    index_b.authority_ids.pop(id(authority_a), None)
    index_b.scope_ids.pop(scope_a_id, None)
    sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch_b)] = record_b  # noqa: SLF001
    certificates._release_c0_prepared_frontier_generator_scopes(  # noqa: SLF001
        batch_b,
        owner_record=record_b,
    )
    assert _c0_ownership_registry_key_sets() == baseline


@pytest.mark.parametrize(
    ("child_kind", "sidecar_state"),
    (
        ("scope", "missing"),
        ("scope", "malformed"),
        ("scope", "typed_malformed_child_reference"),
        ("scope", "typed_malformed_batch_reference"),
        ("scope", "exploding_child_reference"),
        ("scope", "exploding_batch_reference"),
        ("scope", "exploding_main_reference"),
        ("scope", "foreign_rebound"),
        ("scope", "foreign_rebound_dead_child"),
        ("authority", "missing"),
        ("authority", "malformed"),
        ("authority", "typed_malformed_child_reference"),
        ("authority", "typed_malformed_batch_reference"),
        ("authority", "exploding_child_reference"),
        ("authority", "exploding_batch_reference"),
        ("authority", "exploding_main_reference"),
        ("authority", "foreign_rebound"),
        ("authority", "foreign_rebound_dead_child"),
    ),
)
def test_prepared_columnar_cleanup_drains_exact_local_child_when_ownership_evidence_is_corrupt(
    child_kind: str,
    sidecar_state: str,
) -> None:
    from yieldforge.oracle import compiled, sparse

    baseline = _c0_ownership_registry_key_sets()
    parent_baseline = set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY)  # noqa: SLF001
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-{child_kind}-{sidecar_state}-owner-sidecar",
        ),
    )
    foreign_row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-{child_kind}-{sidecar_state}-foreign-peer",
        ),
    )
    foreign_stack = ExitStack()
    token = _C0_TEST_SCOPE_STACK.set(foreign_stack)
    try:
        foreign_authority = _c0_branch_authorities(runtime, (foreign_row,))[0]
    finally:
        _C0_TEST_SCOPE_STACK.reset(token)
    foreign_batch = foreign_authority._branch_batch  # noqa: SLF001
    foreign_state = _c0_ownership_registry_key_sets()
    foreign_parent_state = set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY)  # noqa: SLF001

    try:
        local_stack = ExitStack()
        token = _C0_TEST_SCOPE_STACK.set(local_stack)
        try:
            authority = _c0_branch_authorities(runtime, (row,))[0]
            batch = authority._branch_batch  # noqa: SLF001
            owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch)]  # noqa: SLF001
            scope_id = next(iter(owner_record.generator_scope_references))
            if child_kind == "scope":
                child_id = scope_id
                owner_registry = certificates._C0_PREPARED_FRONTIER_SCOPE_OWNER_REGISTRY  # noqa: SLF001
                child_registry = certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
            else:
                child_id = id(authority)
                owner_registry = certificates._C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY  # noqa: SLF001
                child_registry = certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
            if sidecar_state == "missing":
                owner_registry.pop(child_id)
            elif sidecar_state == "malformed":
                owner_registry[child_id] = object()  # type: ignore[assignment]
            elif sidecar_state == "typed_malformed_child_reference":
                owner_registry[child_id] = replace(
                    owner_registry[child_id],
                    child_reference=object(),  # type: ignore[arg-type]
                )
            elif sidecar_state == "typed_malformed_batch_reference":
                owner_registry[child_id] = replace(
                    owner_registry[child_id],
                    branch_batch_reference=object(),  # type: ignore[arg-type]
                )
            elif sidecar_state == "exploding_child_reference":
                binding_record = owner_registry[child_id]
                owner_registry[child_id] = replace(
                    binding_record,
                    child_reference=_ExplodingWeakRef(binding_record.child_reference()),
                )
            elif sidecar_state == "exploding_batch_reference":
                owner_registry[child_id] = replace(
                    owner_registry[child_id],
                    branch_batch_reference=_ExplodingWeakRef(batch),
                )
            elif sidecar_state == "exploding_main_reference":
                main_record = child_registry[child_id]
                child_registry[child_id] = replace(
                    main_record,
                    reference=_ExplodingWeakRef(main_record.reference()),
                )
            else:
                binding_record = owner_registry[child_id]
                child_reference = binding_record.child_reference
                if sidecar_state == "foreign_rebound_dead_child":

                    class _DeadSidecarChild:
                        pass

                    dead_child = _DeadSidecarChild()
                    child_reference = weakref.ref(dead_child)
                    del dead_child
                    assert child_reference() is None
                owner_registry[child_id] = replace(
                    binding_record,
                    child_reference=child_reference,
                    branch_batch_reference=weakref.ref(foreign_batch),
                    branch_batch_id=id(foreign_batch),
                )

            with pytest.raises(compiled.M8PreparedFrontierIntegrityError) as raised:
                local_stack.close()
        finally:
            _C0_TEST_SCOPE_STACK.reset(token)

        assert "prepared frontier integrity" in str(raised.value)
        assert id(batch) not in sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY  # noqa: SLF001
        assert id(foreign_batch) in sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY  # noqa: SLF001
        assert set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY) == foreign_parent_state  # noqa: SLF001
        assert _c0_ownership_registry_key_sets() == foreign_state
    finally:
        foreign_stack.close()

    assert set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY) == parent_baseline  # noqa: SLF001
    assert _c0_ownership_registry_key_sets() == baseline


@pytest.mark.parametrize(
    "index_state",
    (
        "missing",
        "malformed",
        "typed_malformed_batch_reference",
        "exploding_batch_reference",
        "typed_malformed_scope_ids",
        "typed_malformed_authority_ids",
    ),
)
def test_prepared_columnar_cleanup_drains_local_children_when_batch_index_is_corrupt(
    index_state: str,
) -> None:
    from yieldforge.oracle import compiled, sparse

    baseline = _c0_ownership_registry_key_sets()
    parent_baseline = set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY)  # noqa: SLF001
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]

    def row(token: str):  # type: ignore[no-untyped-def]
        return _c0_branch_row(
            branch_id=0,
            item=inventory_item(
                box(0, 0, 3, 9),
                material=binding.material.model_copy(deep=True),
                token=token,
            ),
        )

    foreign_stack = ExitStack()
    token = _C0_TEST_SCOPE_STACK.set(foreign_stack)
    try:
        foreign_authority = _c0_branch_authorities(
            runtime,
            (row(f"columnar-index-{index_state}-foreign"),),
        )[0]
    finally:
        _C0_TEST_SCOPE_STACK.reset(token)
    foreign_batch = foreign_authority._branch_batch  # noqa: SLF001
    foreign_state = _c0_ownership_registry_key_sets()
    foreign_parent_state = set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY)  # noqa: SLF001

    try:
        local_stack = ExitStack()
        token = _C0_TEST_SCOPE_STACK.set(local_stack)
        try:
            authority = _c0_branch_authorities(
                runtime,
                (row(f"columnar-index-{index_state}-local"),),
            )[0]
            batch = authority._branch_batch  # noqa: SLF001
            index_registry = certificates._C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY  # noqa: SLF001
            index = index_registry[id(batch)]
            if index_state == "missing":
                index_registry.pop(id(batch))
            elif index_state == "malformed":
                index_registry[id(batch)] = object()  # type: ignore[assignment]
            elif index_state == "typed_malformed_batch_reference":
                index_registry[id(batch)] = replace(
                    index,
                    branch_batch_reference=object(),  # type: ignore[arg-type]
                )
            elif index_state == "exploding_batch_reference":
                index_registry[id(batch)] = replace(
                    index,
                    branch_batch_reference=_ExplodingWeakRef(batch),
                )
            elif index_state == "typed_malformed_scope_ids":
                index_registry[id(batch)] = replace(
                    index,
                    scope_ids=object(),  # type: ignore[arg-type]
                )
            else:
                index_registry[id(batch)] = replace(
                    index,
                    authority_ids=object(),  # type: ignore[arg-type]
                )

            with pytest.raises(compiled.M8PreparedFrontierIntegrityError) as raised:
                local_stack.close()
        finally:
            _C0_TEST_SCOPE_STACK.reset(token)

        assert "prepared frontier integrity" in str(raised.value)
        assert id(batch) not in sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY  # noqa: SLF001
        assert id(foreign_batch) in sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY  # noqa: SLF001
        assert set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY) == foreign_parent_state  # noqa: SLF001
        assert _c0_ownership_registry_key_sets() == foreign_state
    finally:
        foreign_stack.close()

    assert set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY) == parent_baseline  # noqa: SLF001
    assert _c0_ownership_registry_key_sets() == baseline


@pytest.mark.parametrize("ledger_kind", ("scope", "authority"))
def test_prepared_columnar_cleanup_ignores_drifting_non_integer_ledger_key(
    ledger_kind: str,
) -> None:
    from yieldforge.oracle import compiled, sparse

    baseline = _c0_ownership_registry_key_sets()
    parent_baseline = set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY)  # noqa: SLF001
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-drifting-{ledger_kind}-ledger-key",
        ),
    )
    local_stack = ExitStack()
    token = _C0_TEST_SCOPE_STACK.set(local_stack)
    try:
        authority = _c0_branch_authorities(runtime, (row,))[0]
        batch = authority._branch_batch  # noqa: SLF001
        owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch)]  # noqa: SLF001
        ledger = (
            owner_record.generator_scope_references
            if ledger_kind == "scope"
            else owner_record.branch_authority_references
        )
        drifting_key = _FlipHash()
        ledger[drifting_key] = object()  # type: ignore[index, assignment]
        drifting_key.armed = True

        with pytest.raises(compiled.M8PreparedFrontierIntegrityError) as raised:
            local_stack.close()
    finally:
        _C0_TEST_SCOPE_STACK.reset(token)

    assert "prepared frontier integrity" in str(raised.value)
    assert id(batch) not in sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY  # noqa: SLF001
    assert set(sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY) == parent_baseline  # noqa: SLF001
    assert _c0_ownership_registry_key_sets() == baseline


def test_prepared_columnar_cleanup_rejects_issued_branch_id_drift_and_drains_exact_index() -> None:
    from yieldforge.oracle import sparse

    baseline = _c0_ownership_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-issued-branch-drift",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    batch = authority._branch_batch  # noqa: SLF001
    owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch)]  # noqa: SLF001
    owner_record.lifecycle.issued_branch_ids.clear()

    with pytest.raises(
        certificates.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._release_c0_prepared_frontier_generator_scopes(  # noqa: SLF001
            batch,
            owner_record=owner_record,
        )

    assert owner_record.lifecycle.consumed
    assert _c0_ownership_registry_key_sets() == baseline


def test_prepared_columnar_consumed_outer_close_rejects_post_consumption_id_drift() -> None:
    from yieldforge.oracle import sparse

    baseline = _c0_ownership_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-consumed-lifecycle-drift",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    batch = authority._branch_batch  # noqa: SLF001
    owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch)]  # noqa: SLF001
    certificates._release_c0_prepared_frontier_generator_scopes(  # noqa: SLF001
        batch,
        owner_record=owner_record,
    )
    assert owner_record.lifecycle.issued_branch_ids == set()

    owner_record.lifecycle.issued_branch_ids.add(999)
    with pytest.raises(
        certificates.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._release_c0_prepared_frontier_generator_scopes(  # noqa: SLF001
            batch,
            owner_record=owner_record,
        )

    assert owner_record.lifecycle.issued_branch_ids == set()
    assert _c0_ownership_registry_key_sets() == baseline


def test_prepared_columnar_parent_exit_preserves_typed_body_error_through_cleanup_fault() -> None:
    from yieldforge.oracle import compiled, sparse

    class _OneShotClearFailure(dict):
        failed = False

        def clear(self) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected C0 ledger clear failure")
            super().clear()

    baseline = _c0_ownership_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-parent-failure-atomic",
        ),
    )
    sentinel = compiled.M8PreparedFrontierIntegrityError("typed C0 body sentinel")
    local_stack = ExitStack()
    token = _C0_TEST_SCOPE_STACK.set(local_stack)
    try:
        authority = _c0_branch_authorities(runtime, (row,))[0]
        batch = authority._branch_batch  # noqa: SLF001
        owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch)]  # noqa: SLF001
        object.__setattr__(
            owner_record,
            "branch_authority_references",
            _OneShotClearFailure(owner_record.branch_authority_references),
        )
        local_stack.callback(lambda: (_ for _ in ()).throw(sentinel))
        with pytest.raises(compiled.M8PreparedFrontierIntegrityError) as raised:
            local_stack.close()
    finally:
        _C0_TEST_SCOPE_STACK.reset(token)

    assert raised.value is sentinel
    assert id(batch) not in sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY  # noqa: SLF001
    assert _c0_ownership_registry_key_sets() == baseline


def test_prepared_columnar_exceptional_exit_drains_partial_authority_issuance() -> None:
    from yieldforge.oracle import compiled, sparse

    baseline = _c0_ownership_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    rows = tuple(
        _c0_branch_row(
            branch_id=branch_id,
            item=inventory_item(
                box(0, 0, 3, 9),
                material=binding.material.model_copy(deep=True),
                token=f"columnar-partial-issuance-{branch_id}",
            ),
        )
        for branch_id in range(2)
    )
    local_stack = ExitStack()
    token = _C0_TEST_SCOPE_STACK.set(local_stack)
    try:
        authority = _c0_branch_authorities(
            runtime,
            rows,
            authority_limit=1,
        )[0]
        batch = authority._branch_batch  # noqa: SLF001
        local_stack.callback(
            lambda: (_ for _ in ()).throw(RuntimeError("partial issuance body failure"))
        )
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            local_stack.close()
    finally:
        _C0_TEST_SCOPE_STACK.reset(token)

    assert id(batch) not in sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY  # noqa: SLF001
    assert _c0_ownership_registry_key_sets() == baseline


def test_prepared_columnar_weakref_cleanup_keeps_sidecar_until_exact_main_release() -> None:
    from yieldforge.oracle import sparse

    baseline = _c0_ownership_registry_key_sets()
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-weakref-owner-asymmetry",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    authority_id = id(authority)
    batch = authority._branch_batch  # noqa: SLF001
    owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(batch)]  # noqa: SLF001
    certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
        authority_id
    ] = object()
    del authority
    gc.collect()

    assert authority_id in (
        certificates._C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY  # noqa: SLF001
    )
    assert authority_id in (
        certificates._C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY[  # noqa: SLF001
            id(batch)
        ].authority_ids
    )
    with pytest.raises(
        certificates.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._release_c0_prepared_frontier_generator_scopes(  # noqa: SLF001
            batch,
            owner_record=owner_record,
        )
    assert _c0_ownership_registry_key_sets() == baseline

    exact_authority = _c0_branch_authorities(runtime, (row,))[0]
    exact_authority_id = id(exact_authority)
    exact_batch = exact_authority._branch_batch  # noqa: SLF001
    del exact_authority
    gc.collect()
    assert exact_authority_id not in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert exact_authority_id not in (
        certificates._C0_PREPARED_FRONTIER_AUTHORITY_OWNER_REGISTRY  # noqa: SLF001
    )
    assert id(exact_batch) not in (
        certificates._C0_PREPARED_FRONTIER_BATCH_CHILD_INDEX_REGISTRY  # noqa: SLF001
    )
    assert _c0_ownership_registry_key_sets() == baseline


def test_prepared_columnar_event_authority_is_one_shot_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-one-shot-authority",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    branch_batch = authority._branch_batch  # noqa: SLF001
    owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(branch_batch)]  # noqa: SLF001
    scope_id = next(iter(owner_record.generator_scope_references))
    original_kernel = certificates.certify_frontier_impossible_batch
    kernel_calls = 0

    def counted_kernel(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        return original_kernel(*args, **kwargs)

    monkeypatch.setattr(
        certificates,
        "certify_frontier_impossible_batch",
        counted_kernel,
    )
    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            first = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=(authority,),
                branch_batch=branch_batch,
                rows=(row,),
            )
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=(authority,),
                    branch_batch=branch_batch,
                    rows=(row,),
                )

    counts = profiler.report().counts
    assert first.branches[0].branch_id == 0
    assert kernel_calls == 1
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert owner_record.branch_authority_references == {}
    assert owner_record.generator_scope_references == {}
    assert id(authority) not in (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
    )
    assert scope_id not in (
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
    )


def test_prepared_columnar_captures_row_item_before_consuming_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-instance-serializer",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    branch_batch = authority._branch_batch  # noqa: SLF001
    serializer_calls = 0
    kernel_calls = 0

    def forged_model_dump(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal serializer_calls
        serializer_calls += 1
        return {"forged": True}

    def unexpected_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("caller row serializer reached the C0 kernel")

    object.__getattribute__(row.item, "__dict__")["model_dump"] = forged_model_dump
    monkeypatch.setattr(
        certificates,
        "certify_frontier_impossible_batch",
        unexpected_kernel,
    )

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="inventory source capture",
        ):
            certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=(authority,),
                branch_batch=branch_batch,
                rows=(row,),
            )

    assert serializer_calls == 0
    assert kernel_calls == 0
    assert (
        certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY.get(  # noqa: SLF001
            id(authority)
        )
        is not None
    )


@pytest.mark.parametrize("submission", ("subset", "empty"))
def test_prepared_columnar_incomplete_event_authority_coverage_rejects_before_kernel(
    monkeypatch: pytest.MonkeyPatch,
    submission: str,
) -> None:
    from yieldforge.oracle import compiled, sparse
    from yieldforge.oracle.profiling import activate_m8_profile

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    rows = tuple(
        _c0_branch_row(
            branch_id=branch_id,
            item=inventory_item(
                box(0, 0, 3, 9),
                material=binding.material.model_copy(deep=True),
                token=f"columnar-incomplete-authority-{submission}-{branch_id}",
            ),
        )
        for branch_id in range(2)
    )
    authorities = _c0_branch_authorities(runtime, rows)
    branch_batch = authorities[0]._branch_batch  # noqa: SLF001
    owner_record = sparse._PREPARED_C0_BRANCH_BATCH_REGISTRY[id(branch_batch)]  # noqa: SLF001
    scope_id = next(iter(owner_record.generator_scope_references))
    kernel_calls = 0

    def unexpected_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("incomplete authority set reached the C0 numeric kernel")

    monkeypatch.setattr(
        certificates,
        "certify_frontier_impossible_batch",
        unexpected_kernel,
    )
    submitted_authorities = authorities[:1] if submission == "subset" else ()
    submitted_rows = rows[:1] if submission == "subset" else ()
    with activate_m8_profile() as profiler:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=submitted_authorities,
                    branch_batch=branch_batch,
                    rows=submitted_rows,
                )

    counts = profiler.report().counts
    assert kernel_calls == 0
    assert counts["fallbacks"] == 0
    assert counts["full_authoritative_fallbacks"] == 0
    assert owner_record.branch_authority_references == {}
    assert owner_record.generator_scope_references == {}
    assert all(
        id(authority) not in certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY  # noqa: SLF001
        for authority in authorities
    )
    assert scope_id not in (
        certificates._C0_PREPARED_FRONTIER_GENERATOR_SCOPE_REGISTRY  # noqa: SLF001
    )


def test_prepared_columnar_event_rejects_duplicate_scope_and_branch_issuance() -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-duplicate-authority-issuance",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    registered = certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
        id(authority)
    ]

    with pytest.raises(
        compiled.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._issue_c0_prepared_frontier_generator_scope(  # noqa: SLF001
            authority._branch_batch  # noqa: SLF001
        )
    with pytest.raises(
        compiled.M8PreparedFrontierIntegrityError,
        match="prepared frontier integrity",
    ):
        certificates._issue_c0_prepared_frontier_branch_authority(  # noqa: SLF001
            source_scope=registered.source_scope,
            source_branch=registered.source_branch,
        )


def test_prepared_columnar_authority_validation_is_linear_in_branch_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.baseline.test_replay import _verified
    from yieldforge.baseline.replay import build_m7_replay_input
    from yieldforge.oracle import compiled, sparse

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    replay_input = runtime.replay_input
    problem = replay_input.problems[0]
    branch_count = 12
    verified = _verified(
        problem,
        candidate_ids=tuple(f"linear-candidate-{index:02d}" for index in range(branch_count)),
    )
    runtime.replay_input = build_m7_replay_input(
        m0_contract_id=replay_input.m0_contract_id,
        m0_contract_sha256=replay_input.m0_contract_sha256,
        problem_index_id=replay_input.problem_index_id,
        problem_index_sha256=replay_input.problem_index_sha256,
        m6_contract_id=replay_input.m6_contract_id,
        m6_contract_sha256=replay_input.m6_contract_sha256,
        m6_population_id=replay_input.m6_population_id,
        m6_population_sha256=replay_input.m6_population_sha256,
        policy=replay_input.policy,
        rates=replay_input.rates,
        fit_config=replay_input.fit_config,
        search_config=replay_input.search_config,
        problems=replay_input.problems,
        candidate_sets=(verified.evidence,),
        instances=replay_input.instances,
        horizon_end=replay_input.horizon_end,
        collision_backend=replay_input.collision_backend,
        jagua_container_guard=replay_input.jagua_container_guard,
    )
    runtime.runtime_candidates = {problem.problem_id: verified}
    binding = runtime.replay_input.instances[1]
    rows = tuple(
        _c0_branch_row(
            branch_id=branch_id,
            item=inventory_item(
                box(0, 0, 3, 9),
                material=binding.material.model_copy(deep=True),
                token=f"columnar-linear-validation-{branch_id}",
            ),
        )
        for branch_id in range(branch_count)
    )
    call_counts = {
        "full_batch": 0,
        "full_scope": 0,
        "scope_fingerprint": 0,
        "shallow_authority": 0,
        "index_rebuilds": 0,
    }
    original_full_batch = sparse._require_prepared_c0_branch_batch  # noqa: SLF001
    original_full_scope = (  # noqa: SLF001
        certificates._require_c0_prepared_frontier_generator_scope
    )
    original_scope_fingerprint = sparse._generator_context_fingerprint  # noqa: SLF001
    original_replace = certificates.replace

    def counted_full_batch(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_counts["full_batch"] += 1
        return original_full_batch(*args, **kwargs)

    def counted_full_scope(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_counts["full_scope"] += 1
        return original_full_scope(*args, **kwargs)

    def counted_scope_fingerprint(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_counts["scope_fingerprint"] += 1
        return original_scope_fingerprint(*args, **kwargs)

    def counted_replace(instance, **changes):  # type: ignore[no-untyped-def]
        if type(instance) is certificates._C0PreparedFrontierBatchChildIndex:  # noqa: SLF001
            call_counts["index_rebuilds"] += 1
        return original_replace(instance, **changes)

    monkeypatch.setattr(sparse, "_require_prepared_c0_branch_batch", counted_full_batch)
    monkeypatch.setattr(
        certificates,
        "_require_c0_prepared_frontier_generator_scope",
        counted_full_scope,
    )
    monkeypatch.setattr(sparse, "_generator_context_fingerprint", counted_scope_fingerprint)
    monkeypatch.setattr(certificates, "replace", counted_replace)

    authorities = _c0_branch_authorities(runtime, rows)

    assert call_counts == {
        "full_batch": 1,
        "full_scope": 0,
        "scope_fingerprint": 5,
        "shallow_authority": 0,
        "index_rebuilds": 0,
    }

    original_shallow_authority = (  # noqa: SLF001
        certificates._require_c0_prepared_frontier_branch_authority_registration_shallow
    )

    def counted_shallow_authority(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_counts["shallow_authority"] += 1
        return original_shallow_authority(*args, **kwargs)

    monkeypatch.setattr(
        certificates,
        "_require_c0_prepared_frontier_branch_authority_registration_shallow",
        counted_shallow_authority,
    )
    for key in call_counts:
        call_counts[key] = 0

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        result = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
            runtime,
            prepared_layouts=prepared,
            event_position=1,
            branches=authorities,
            branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
            rows=rows,
        )

    assert len(result.branches) == branch_count
    assert call_counts["full_batch"] == 3
    assert call_counts["full_scope"] == 2
    assert call_counts["scope_fingerprint"] == 7
    assert call_counts["shallow_authority"] == 4 * branch_count
    assert call_counts["index_rebuilds"] == 0


def test_prepared_columnar_batch_rejects_live_branch_root_mutation_after_issuance() -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-live-root-mutation",
        ),
    )
    authority = _c0_branch_authorities(runtime, (row,))[0]
    registered = certificates._C0_PREPARED_FRONTIER_BRANCH_AUTHORITY_REGISTRY[  # noqa: SLF001
        id(authority)
    ]
    original = registered.source_branch.initial_step
    object.__setattr__(
        registered.source_branch,
        "initial_step",
        replace(original, cursor=replace(original.cursor, timestamp_subsequence=99)),
    )
    try:
        with compiled._prepare_translation_layout_batch(  # noqa: SLF001
            runtime,
            event_positions=(1,),
        ) as prepared:
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="prepared frontier integrity",
            ):
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=(authority,),
                    branch_batch=authority._branch_batch,  # noqa: SLF001
                    rows=(row,),
                )
    finally:
        object.__setattr__(registered.source_branch, "initial_step", original)


@pytest.mark.parametrize(
    "corruption",
    (
        "candidate_duplicate",
        "candidate_reordered",
        "layout_duplicate",
        "layout_reordered",
        "problem_id",
        "candidate_set_sha256",
        "fit_config_sha256",
        "frontier_member_partition",
        "layout_sha256",
        "measurement_bounds",
    ),
)
def test_prepared_columnar_batch_rejects_partition_drift_as_integrity(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-partition-{corruption}",
        ),
    )
    rows = (row,)
    authorities = _c0_branch_authorities(runtime, rows)

    def forbidden_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("corrupt partition entered the C0 kernel")

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        canonical = compiled._prepared_frontier_batch_inputs(  # noqa: SLF001
            prepared,
            runtime,
            event_position=1,
            remnants=(row.item.remnant,),
        )
        if corruption == "candidate_duplicate":
            corrupted = replace(
                canonical,
                candidate_ids=(canonical.candidate_ids[0], canonical.candidate_ids[0]),
            )
        elif corruption == "candidate_reordered":
            corrupted = replace(canonical, candidate_ids=tuple(reversed(canonical.candidate_ids)))
        elif corruption == "layout_duplicate":
            corrupted = replace(
                canonical,
                rejection_layout_candidate_ids=(
                    canonical.rejection_layout_candidate_ids[0],
                    canonical.rejection_layout_candidate_ids[0],
                ),
            )
        elif corruption == "layout_reordered":
            corrupted = replace(
                canonical,
                rejection_layout_candidate_ids=tuple(
                    reversed(canonical.rejection_layout_candidate_ids)
                ),
            )
        elif corruption == "problem_id":
            assert canonical.problem is not None
            corrupted = replace(
                canonical,
                problem=replace(canonical.problem, problem_id="corrupt-problem"),
            )
        elif corruption == "candidate_set_sha256":
            assert canonical.problem is not None
            corrupted = replace(
                canonical,
                problem=replace(
                    canonical.problem,
                    candidate_set_sha256="sha256:" + "4" * 64,
                ),
            )
        elif corruption == "fit_config_sha256":
            corrupted = replace(
                canonical,
                fit_config_sha256="sha256:" + "5" * 64,
            )
        elif corruption == "layout_sha256":
            corrupted = replace(
                canonical,
                rejection_layout_sha256s=(
                    "sha256:" + "6" * 64,
                    *canonical.rejection_layout_sha256s[1:],
                ),
            )
        elif corruption == "measurement_bounds":
            measurement = canonical.measurements[0]
            min_x, min_y, max_x, max_y = measurement.bounds
            corrupted = replace(
                canonical,
                measurements=(
                    replace(
                        measurement,
                        bounds=(min_x, min_y, max_x + 1.0, max_y),
                    ),
                ),
            )
        else:
            assert canonical.problem is not None
            frontier = canonical.problem.frontier
            altered_member = replace(frontier.members[0], problem_id="corrupt-problem")
            altered_frontier = object.__new__(type(frontier))
            object.__setattr__(
                altered_frontier,
                "members",
                (altered_member, *frontier.members[1:]),
            )
            object.__setattr__(altered_frontier, "retained", frontier.retained)
            object.__setattr__(altered_frontier, "dominated_by", frontier.dominated_by)
            corrupted = replace(
                canonical,
                problem=replace(canonical.problem, frontier=altered_frontier),
            )
        monkeypatch.setattr(
            certificates,
            "_prepared_frontier_batch_inputs",
            lambda *_args, **_kwargs: corrupted,
        )
        monkeypatch.setattr(
            certificates,
            "certify_frontier_impossible_batch",
            forbidden_kernel,
        )
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="prepared frontier integrity",
        ):
            certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=authorities,
                branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                rows=rows,
            )


@pytest.mark.parametrize("corruption", ("missing", "duplicate", "reordered", "boolean"))
def test_prepared_columnar_batch_rejects_corrupt_kernel_output(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from yieldforge.oracle import compiled
    from yieldforge.oracle.columnar import C0FrontierResult

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    rows = tuple(
        _c0_branch_row(
            branch_id=position,
            item=inventory_item(
                box(0, 0, 3, 9),
                material=binding.material.model_copy(deep=True),
                token=f"columnar-corrupt-{corruption}-{position}",
            ),
        )
        for position in range(2)
    )
    original = certificates.certify_frontier_impossible_batch

    def corrupt(frontier, queries):  # type: ignore[no-untyped-def]
        result = original(frontier, queries)
        if corruption == "missing":
            return result[:-1]
        if corruption == "duplicate":
            return (result[0], result[0])
        if corruption == "reordered":
            return tuple(reversed(result))
        return (
            C0FrontierResult(
                row_id=result[0].row_id,
                all_impossible=not result[0].all_impossible,
            ),
            result[1],
        )

    authorities = _c0_branch_authorities(runtime, rows)
    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        monkeypatch.setattr(certificates, "certify_frontier_impossible_batch", corrupt)
        with pytest.raises(ValueError, match="C0 frontier batch result integrity differs"):
            certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                runtime,
                prepared_layouts=prepared,
                event_position=1,
                branches=authorities,
                branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
                rows=rows,
            )


def test_prepared_columnar_batch_marks_incomplete_rejection_archive_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=(),
    )
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-incomplete-archive",
        ),
    )

    def forbidden_kernel(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("unsupported archive entered the C0 kernel")

    authorities = _c0_branch_authorities(runtime, (row,))
    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        monkeypatch.setattr(
            certificates,
            "certify_frontier_impossible_batch",
            forbidden_kernel,
        )
        result = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
            runtime,
            prepared_layouts=prepared,
            event_position=1,
            branches=authorities,
            branch_batch=authorities[0]._branch_batch,  # noqa: SLF001
            rows=(row,),
        )

    assert result.rows[0].supported is False
    assert result.rows[0].all_impossible is False
    assert result.branches[0].compact_eligible is False


@pytest.mark.parametrize("source_field", ("event_material", "fit_config"))
def test_prepared_columnar_batch_rejects_transient_live_source_mutation(
    source_field: str,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token=f"columnar-transient-{source_field}",
        ),
    )
    if source_field == "event_material":
        owner, field_name = binding.material, "grade"
        mutated = f"{binding.material.grade}-transient"
    else:
        owner, field_name = runtime.replay_input.fit_config, "coordinate_tolerance"
        mutated = 100.0
    original = getattr(owner, field_name)
    canonical_authorities = _c0_branch_authorities(runtime, (row,))
    repeated_authorities = _c0_branch_authorities(runtime, (row,))

    with compiled._prepare_translation_layout_batch(  # noqa: SLF001
        runtime,
        event_positions=(1,),
    ) as prepared:
        canonical = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
            runtime,
            prepared_layouts=prepared,
            event_position=1,
            branches=canonical_authorities,
            branch_batch=canonical_authorities[0]._branch_batch,  # noqa: SLF001
            rows=(row,),
        )
        object.__setattr__(owner, field_name, mutated)
        try:
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="source lease payload",
            ):
                certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=repeated_authorities,
                    branch_batch=repeated_authorities[0]._branch_batch,  # noqa: SLF001
                    rows=(row,),
                )
        finally:
            object.__setattr__(owner, field_name, original)

    assert canonical


def test_prepared_columnar_batch_exit_rejects_persistent_source_drift() -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    row = _c0_branch_row(
        branch_id=0,
        item=inventory_item(
            box(0, 0, 3, 9),
            material=binding.material.model_copy(deep=True),
            token="columnar-persistent-fit-drift",
        ),
    )
    owner = runtime.replay_input.fit_config
    original = owner.coordinate_tolerance
    canonical_authorities = _c0_branch_authorities(runtime, (row,))
    repeated_authorities = _c0_branch_authorities(runtime, (row,))
    try:
        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="source lease payload",
        ):
            with compiled._prepare_translation_layout_batch(  # noqa: SLF001
                runtime,
                event_positions=(1,),
            ) as prepared:
                canonical = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=canonical_authorities,
                    branch_batch=canonical_authorities[0]._branch_batch,  # noqa: SLF001
                    rows=(row,),
                )
                object.__setattr__(owner, "coordinate_tolerance", 100.0)
                repeated = certificates._certify_prepared_frontier_batch(  # noqa: SLF001
                    runtime,
                    prepared_layouts=prepared,
                    event_position=1,
                    branches=repeated_authorities,
                    branch_batch=repeated_authorities[0]._branch_batch,  # noqa: SLF001
                    rows=(row,),
                )
                assert repeated == canonical
    finally:
        object.__setattr__(owner, "coordinate_tolerance", original)


def test_prepared_context_fingerprint_optionally_binds_c0_kernel_identity() -> None:
    from yieldforge.oracle import sparse
    from yieldforge.oracle.prepared import (
        _C0_FRONTIER_KERNEL_IDENTITY,
        _C0_FRONTIER_KERNEL_MODE,
        prepared_context_fingerprint,
    )

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        arguments = {
            "kind": (
                f"generator:{id(context._prepared_layouts)}:"  # noqa: SLF001
                f"source-runtime:{id(context._source_runtime)}"  # noqa: SLF001
            ),
            "context_id": id(context),
            "authority": context._authority,  # noqa: SLF001
            "request": context._request,  # noqa: SLF001
            "catalog": context._catalog,  # noqa: SLF001
            "fallback_step": context._fallback_step,  # noqa: SLF001
            "visible": context._visible,  # noqa: SLF001
            "stop_event_position": context._stop_event_position,  # noqa: SLF001
            "suffix_sha256": context._suffix_sha256,  # noqa: SLF001
        }
        legacy = prepared_context_fingerprint(**arguments)
        bound = prepared_context_fingerprint(
            **arguments,
            kernel_mode=_C0_FRONTIER_KERNEL_MODE,
            kernel_identity=_C0_FRONTIER_KERNEL_IDENTITY,
        )

    assert bound == sparse._generator_context_fingerprint(context)  # noqa: SLF001
    assert bound != legacy


@pytest.mark.parametrize(
    "source_field",
    ("event_material", "fit_config"),
)
def test_prepared_common_capture_rejects_transient_live_source_mutation(
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
            with pytest.raises(
                compiled.M8PreparedFrontierIntegrityError,
                match="source lease payload",
            ):
                certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
                    runtime,
                    cursor=cursor,
                    semantic_runtime_sha256=semantic_runtime_sha256,
                    prepared_layouts=prepared,
                )
        finally:
            object.__setattr__(owner, field_name, original)

    assert canonical
    assert not mutation_observed
    assert getattr(owner, field_name) == original


def test_prepared_common_capture_is_issuance_bound_during_transient_search_config_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    event_position = 1
    binding = runtime.replay_input.instances[event_position]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material.model_copy(deep=True),
        token="prepared-common-snapshot-search-config",
    )
    cursor = replace(_fallback_cursor(runtime), inventory=(area_only,))
    semantic_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    owner = runtime.replay_input.search_config
    original = owner.maximum_candidates
    original_standard = certificates._prepared_standard_winner  # noqa: SLF001
    original_fact = certificates._common_transition_fact_from_catalog  # noqa: SLF001
    mutation_observed = False

    def mutate_after_boundary(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = original_standard(*args, **kwargs)
        object.__setattr__(owner, "maximum_candidates", 1)
        return result

    def restore_before_exit(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal mutation_observed
        mutation_observed = owner.maximum_candidates == 1
        object.__setattr__(owner, "maximum_candidates", original)
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
            object.__setattr__(owner, "maximum_candidates", original)

    assert mutation_observed
    assert repeated == canonical
    assert repr(repeated).encode() == repr(canonical).encode()
    assert owner.maximum_candidates == original


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


def test_public_passivity_captures_branch_cursor_before_validated_authority() -> None:
    import sys

    from yieldforge.oracle import compiled

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    common = certificates.build_validated_m8_common_transition(runtime, cursor=cursor)
    original_material = runtime.replay_input.instances[0].material
    item = inventory_item(
        box(0, 0, 4, 10),
        material=original_material.model_copy(deep=True),
        token="public-passivity-order",
    )
    clean_branch = replace(cursor, inventory=(item.model_copy(deep=True),))
    try:
        clean = certificates.certify_event_passivity(
            runtime,
            common=common,
            branch_cursor=clean_branch,
        )
        assert not clean.passive
        assert clean.witness is None
        assert clean.exact_search_count == 1

        state = {"hit": 0, "reads": 0}
        item_type = type(item)

        def evil_item_getattribute(self, name):  # type: ignore[no-untyped-def]
            if name == "remnant":
                frame = sys._getframe(1)  # noqa: SLF001
                while frame is not None:
                    if frame.f_code.co_name == "compile_translation_rejections":
                        state["hit"] += 1
                        break
                    frame = frame.f_back
            return object.__getattribute__(self, name)

        evil_item_type = type(
            "EvilPassivityItem",
            (item_type,),
            {"__getattribute__": evil_item_getattribute},
        )
        evil_item_type.__pydantic_generic_metadata__ = {
            "origin": item_type,
            "args": (),
            "parameters": (),
        }
        object.__setattr__(item, "__class__", evil_item_type)

        with pytest.raises(
            compiled.M8PreparedFrontierIntegrityError,
            match="inventory source capture",
        ):
            certificates.certify_event_passivity(
                runtime,
                common=common,
                branch_cursor=replace(cursor, inventory=(item,)),
        )

        assert state == {"hit": 0, "reads": 0}
        assert runtime.replay_input.instances[0].material is original_material
    finally:
        certificates._release_validated_common_transition(common)  # noqa: SLF001


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
    from yieldforge.oracle import compiled, sparse

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
    original_prepared_fingerprint = compiled._prepared_translation_layout_fingerprint  # noqa: SLF001
    calls = {"executable": 0, "authority": 0, "prepared_fingerprint": 0}

    def counted_executable_identity(runtime):  # type: ignore[no-untyped-def]
        calls["executable"] += 1
        return original_executable_identity(runtime)

    def counted_require_active(self, runtime=None):  # type: ignore[no-untyped-def]
        calls["authority"] += 1
        return original_require_active(self, runtime)

    def counted_prepared_fingerprint(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["prepared_fingerprint"] += 1
        return original_prepared_fingerprint(*args, **kwargs)

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
    monkeypatch.setattr(
        compiled,
        "_prepared_translation_layout_fingerprint",
        counted_prepared_fingerprint,
    )

    def capture_counts(*, all_actions: bool) -> tuple[int, int, int, int, int]:
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
            calls.update(executable=0, authority=0, prepared_fingerprint=0)
            traversal = sparse._capture_prepared_unchecked_traversal(  # noqa: SLF001
                context,
                action_ids=action_ids if all_actions else (nonfallback,),
            )
            observed = (
                calls["executable"],
                calls["authority"],
                calls["prepared_fingerprint"],
                len(action_ids),
                len(traversal.common_transitions),
            )
        return observed

    one_action = capture_counts(all_actions=False)
    all_actions = capture_counts(all_actions=True)

    assert one_action[3] >= 18
    assert one_action[4] == 2
    assert all_actions[3:] == one_action[3:]
    assert all_actions[:3] == one_action[:3]
    assert all_actions[0] == 4 * all_actions[4]
    assert all_actions[1] == 4 * all_actions[4] + 2


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


def test_prepared_unchecked_source_guard_preserves_typed_body_over_cleanup_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import compiled, sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    sentinel = compiled.M8PreparedFrontierIntegrityError(
        "M8 prepared frontier integrity differs: source guard body sentinel"
    )

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        common = certificates._capture_unchecked_m8_common_transition(  # noqa: SLF001
            context._request.runtime,  # noqa: SLF001
            cursor=context._fallback_step.cursor,  # noqa: SLF001
            semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
            prepared_layouts=context._prepared_layouts,  # noqa: SLF001
            runtime_authority=context._authority,  # noqa: SLF001
        )
        with pytest.raises(compiled.M8PreparedFrontierIntegrityError) as captured:
            with certificates._guard_unchecked_prepared_common_source(  # noqa: SLF001
                context._request.runtime,  # noqa: SLF001
                runtime_authority=context._authority,  # noqa: SLF001
                scope_owner=context,
                prepared_layouts=context._prepared_layouts,  # noqa: SLF001
                common=common,
            ) as guard:
                monkeypatch.setattr(
                    certificates,
                    "_require_unchecked_prepared_source_guard",
                    lambda *_args, **_kwargs: None,
                )
                certificates._UNCHECKED_PREPARED_SOURCE_GUARD_REGISTRY.pop(  # noqa: SLF001
                    id(guard)
                )
                raise sentinel

    assert captured.value is sentinel


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


@pytest.mark.parametrize("fail_branch", (False, True))
def test_prepared_certificate_validation_scope_is_once_per_event_and_exception_safe(
    monkeypatch: pytest.MonkeyPatch,
    fail_branch: bool,
) -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0, event_count=4)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_advance = sparse._advance_branch  # noqa: SLF001
    original_common_fact = sparse._validated_common_transition_fact  # noqa: SLF001
    original_builder = sparse.build_validated_m8_common_transition_in_context
    entered: list[int] = []
    exited: list[int] = []
    active_positions: list[int] = []
    active_runtimes: list[object] = []
    builder_scope_states: list[bool] = []
    common_fact_scope_states: list[bool] = []
    branch_calls = 0

    @contextmanager
    def tracked_scope(
        _prepared,  # type: ignore[no-untyped-def]
        scoped_runtime,  # type: ignore[no-untyped-def]
        *,
        event_position: int,
    ):
        assert not active_positions
        entered.append(event_position)
        active_positions.append(event_position)
        active_runtimes.append(scoped_runtime)
        try:
            yield
        finally:
            assert active_positions.pop() == event_position
            assert active_runtimes.pop() is scoped_runtime
            exited.append(event_position)

    def tracked_advance(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal branch_calls
        branch_calls += 1
        assert active_positions
        if fail_branch and branch_calls == 2:
            raise RuntimeError("injected prepared branch failure")
        return original_advance(*args, **kwargs)

    def tracked_common_fact(*args, **kwargs):  # type: ignore[no-untyped-def]
        common_fact_scope_states.append(bool(active_positions))
        assert args[0] is active_runtimes[-1]
        return original_common_fact(*args, **kwargs)

    def tracked_builder(*args, **kwargs):  # type: ignore[no-untyped-def]
        builder_scope_states.append(bool(active_positions))
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(
        sparse,
        "_activate_prepared_event_validation",
        tracked_scope,
        raising=False,
    )
    monkeypatch.setattr(sparse, "_advance_branch", tracked_advance)
    monkeypatch.setattr(
        sparse,
        "_validated_common_transition_fact",
        tracked_common_fact,
    )
    monkeypatch.setattr(
        sparse,
        "build_validated_m8_common_transition_in_context",
        tracked_builder,
    )

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        action_ids = tuple(
            item.action_id for item in context._catalog.actions  # noqa: SLF001
        )
        expected_positions = list(
            range(
                context._fallback_step.cursor.next_event_position,  # noqa: SLF001
                context._stop_event_position,  # noqa: SLF001
            )
        )
        if fail_branch:
            with pytest.raises(RuntimeError, match="injected prepared branch failure"):
                sparse._score_prepared_certificate_actions(  # noqa: SLF001
                    context,
                    action_ids=action_ids,
                )
        else:
            results = sparse._score_prepared_certificate_actions(  # noqa: SLF001
                context,
                action_ids=action_ids,
            )
            assert len(results) == len(action_ids)

    assert not active_positions
    assert not active_runtimes
    assert exited == entered
    assert builder_scope_states and all(builder_scope_states)
    assert common_fact_scope_states and all(common_fact_scope_states)
    if fail_branch:
        assert entered == expected_positions[:1]
        assert branch_calls == 2
    else:
        assert entered == expected_positions
        assert branch_calls == len(expected_positions) * len(action_ids)
        assert len(entered) < branch_calls


@pytest.mark.parametrize("fail_capture", (False, True))
def test_prepared_unchecked_common_capture_starts_inside_event_scope(
    monkeypatch: pytest.MonkeyPatch,
    fail_capture: bool,
) -> None:
    from yieldforge.oracle import sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0, event_count=3)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_capture = sparse._capture_unchecked_m8_common_transition  # noqa: SLF001
    entered: list[int] = []
    exited: list[int] = []
    active_positions: list[int] = []
    active_runtimes: list[object] = []
    capture_scope_states: list[bool] = []

    @contextmanager
    def tracked_scope(
        _prepared,  # type: ignore[no-untyped-def]
        scoped_runtime,  # type: ignore[no-untyped-def]
        *,
        event_position: int,
    ):
        assert not active_positions
        entered.append(event_position)
        active_positions.append(event_position)
        active_runtimes.append(scoped_runtime)
        try:
            yield
        finally:
            assert active_positions.pop() == event_position
            assert active_runtimes.pop() is scoped_runtime
            exited.append(event_position)

    def tracked_capture(*args, **kwargs):  # type: ignore[no-untyped-def]
        capture_scope_states.append(bool(active_positions))
        assert args[0] is active_runtimes[-1]
        if fail_capture:
            raise RuntimeError("injected prepared common capture failure")
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(
        sparse,
        "_activate_prepared_event_validation",
        tracked_scope,
    )
    monkeypatch.setattr(
        sparse,
        "_capture_unchecked_m8_common_transition",
        tracked_capture,
    )

    with sparse._prepare_m8_generator_context(request) as context:  # noqa: SLF001
        expected_positions = list(
            range(
                context._fallback_step.cursor.next_event_position,  # noqa: SLF001
                context._stop_event_position,  # noqa: SLF001
            )
        )
        if fail_capture:
            with pytest.raises(RuntimeError, match="injected prepared common capture failure"):
                sparse._capture_prepared_unchecked_traversal(context)  # noqa: SLF001
        else:
            captured = sparse._capture_prepared_unchecked_traversal(context)  # noqa: SLF001
            assert len(captured.common_transitions) == len(expected_positions)

    assert capture_scope_states and all(capture_scope_states)
    assert not active_positions
    assert not active_runtimes
    assert exited == entered
    assert entered == (expected_positions[:1] if fail_capture else expected_positions)


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
