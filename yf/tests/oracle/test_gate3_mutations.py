from __future__ import annotations

import hashlib
import socket
import sys
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest

from tests.oracle.fixtures import two_problem_runtime
from tests.oracle.test_facts import _bundle, _rehash_fact
from tests.oracle.test_gate3_evidence import (
    _checked_roots,
    _membership_attestation,
    _portable_gate3_result,
    _sha,
)
from yieldforge.baseline.replay import build_m7_replay_input
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import experiment
from yieldforge.oracle.facts import (
    M8UncheckedFactBundleV2,
    canonical_semantic_json,
    m8_bundle_sha256,
)
from yieldforge.oracle.gate3_evidence import (
    M8Gate3AuditSample,
    M8Gate3CheckedActionRoot,
    M8Gate3CheckedRootManifest,
    build_gate3_mutation_manifest,
    build_gate3_mutation_recipe,
    freeze_gate3_audit_sample,
    freeze_gate3_checked_root_manifest,
    load_parent_v3_certificate_proof,
)
from yieldforge.temporal_benchmark.contracts import TemporalPartition, TemporalRegime

_PARENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "experiments/results/m8-certificate-proof-yfm8proof-b296ba919c07d55ece14c6db.json"
)
_PORTABLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "experiments/results/m8-portable-fact-gate3-yfm8gate3-ea8a12969396172d7dbc4774.json"
)


def _attempt_guarded_mutation_access(kind: str, path: str) -> tuple[bool, bool, int]:
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    allowed_runtime = Path(path) if kind == "allowed_runtime" else None
    telemetry = mutations._install_mutation_capability_guard(  # noqa: SLF001
        allowed_executable=allowed_runtime,
        fork_limit=1 if kind in {"allowed_runtime", "fork_overflow"} else 0,
    )
    try:
        if kind == "read":
            Path(path).read_bytes()
            return (
                telemetry.evaluation_accessed,
                telemetry.artifact_published,
                telemetry.fork_count,
            )
        elif kind == "publish":
            Path(path).write_text("forbidden")
        elif kind == "evaluate":
            with socket.socket() as connection:
                connection.connect(("127.0.0.1", 9))
        elif kind == "allowed_runtime":
            Path(path).read_bytes()
            sys.audit("os.fork")
            sys.audit("os.exec", path, (path,), None)
            sys.audit("subprocess.Popen", path, (path,), None, None)
            return (
                telemetry.evaluation_accessed,
                telemetry.artifact_published,
                telemetry.fork_count,
            )
        elif kind == "fork_overflow":
            sys.audit("os.fork")
            sys.audit("os.fork")
        else:
            raise AssertionError("unexpected capability probe")
    except PermissionError:
        return (
            telemetry.evaluation_accessed,
            telemetry.artifact_published,
            telemetry.fork_count,
        )
    raise AssertionError("mutation capability guard permitted filesystem access")


@pytest.fixture(scope="module")
def gate3_inputs():  # type: ignore[no-untyped-def]
    parent = load_parent_v3_certificate_proof(_PARENT_PATH)
    gate3 = _portable_gate3_result()
    roots = _checked_roots(gate3)
    root_manifest = freeze_gate3_checked_root_manifest(
        gate3,
        roots,
        membership_attestation=_membership_attestation(gate3, roots),
    )
    sample = freeze_gate3_audit_sample(parent, root_manifest)
    mutation_manifest = build_gate3_mutation_manifest(
        parent,
        gate3,
        root_manifest,
        sample,
        harness_id="m8-gate3-mutation-harness-test-v1",
        harness_content_sha256=_sha("mutation-harness"),
        runtime_id="m8-gate3-mutation-runtime-test-v1",
        runtime_content_sha256=_sha("mutation-runtime"),
    )
    return parent, gate3, root_manifest, sample, mutation_manifest


def _expanded_bundle(
    regime: TemporalRegime,
    root_count: int,
) -> M8UncheckedFactBundleV2:
    raw = deepcopy(_bundle().model_dump(mode="python"))
    raw["provenance"]["regime"] = regime.value
    raw["provenance"]["temporal_seed"] = 2026082300
    common = raw["common_lemmas"][0]
    influence_template = raw["influence_facts"][0]
    root_template = raw["action_roots"][0]
    baseline_action_id = (
        "yfm7a-" + hashlib.sha256(f"{regime.value}:baseline".encode()).hexdigest()[:24]
    )
    baseline_catalog_action_id = f"m7-standard:{regime.value}:0000"
    influences: list[dict[str, object]] = []
    roots: list[dict[str, object]] = []
    for index in range(root_count):
        action_id = (
            baseline_action_id
            if index == 0
            else "yfm7a-"
            + hashlib.sha256(f"{regime.value}:action:{index}".encode()).hexdigest()[:24]
        )
        catalog_action_id = f"m7-standard:{regime.value}:{index:04d}"
        influence = deepcopy(influence_template)
        influence["root_action_id"] = action_id
        _rehash_fact(influence)
        influences.append(influence)

        root = deepcopy(root_template)
        root.update(
            {
                "action_id": action_id,
                "catalog_action_id": catalog_action_id,
                "baseline_action_id": baseline_action_id,
                "baseline_catalog_action_id": baseline_catalog_action_id,
                "common_lemma_refs": (common["fact_sha256"],),
                "influence_fact_refs": (influence["fact_sha256"],),
            }
        )
        _rehash_fact(root)
        roots.append(root)

    raw["influence_facts"] = tuple(
        sorted(
            influences,
            key=lambda item: (
                item["stream_id"],
                item["event_position"],
                item["root_action_id"],
                item["fact_sha256"],
            ),
        )
    )
    raw["action_roots"] = tuple(
        sorted(
            roots,
            key=lambda item: (
                item["stream_id"],
                item["action_id"],
                item["fact_sha256"],
            ),
        )
    )
    payload = {key: value for key, value in raw.items() if key != "bundle_sha256"}
    raw["bundle_sha256"] = m8_bundle_sha256(payload)
    return M8UncheckedFactBundleV2.model_validate(raw, strict=True)


def _portable_gate3_for_bundles(
    bundles: tuple[M8UncheckedFactBundleV2, M8UncheckedFactBundleV2],
    semantic_bytes: tuple[bytes, bytes],
) -> experiment.M8PortableFactGate3Result:
    base = _portable_gate3_result()
    cells = []
    for base_cell, bundle, raw in zip(base.cells, bundles, semantic_bytes, strict=True):
        payload = base_cell.model_dump(mode="python")
        raw_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
        payload.update(
            {
                "stream_id": bundle.provenance.stream_id,
                "first_bundle_sha256": bundle.bundle_sha256,
                "second_bundle_sha256": bundle.bundle_sha256,
                "first_semantic_bundle_bytes_sha256": raw_sha,
                "second_semantic_bundle_bytes_sha256": raw_sha,
                "semantic_serialized_bytes": len(raw),
                "repeated_semantic_serialized_bytes": len(raw),
                "fixed_layer_node_count": (
                    len(bundle.translation_batches)
                    + len(bundle.candidate_scalar_facts)
                    + len(bundle.frontier_facts)
                    + len(bundle.standard_candidate_facts)
                    + len(bundle.common_lemmas)
                    + len(bundle.influence_facts)
                    + len(bundle.action_roots)
                ),
                "translation_batch_count": len(bundle.translation_batches),
                "candidate_scalar_fact_count": len(bundle.candidate_scalar_facts),
                "frontier_fact_count": len(bundle.frontier_facts),
                "standard_candidate_fact_count": len(bundle.standard_candidate_facts),
                "common_lemma_count": len(bundle.common_lemmas),
                "influence_fact_count": len(bundle.influence_facts),
                "generated_action_root_count": len(bundle.action_roots),
                "checked_common_lemma_count": len(bundle.common_lemmas),
                "checked_influence_fact_count": len(bundle.influence_facts),
                "checked_action_root_count": len(bundle.action_roots),
                "producer_counted_search_lemma_count": len(bundle.common_lemmas),
                "counted_translation_audit_count": len(bundle.common_lemmas),
                "counted_translation_audit_call_count": len(bundle.common_lemmas),
                "influence_translation_audit_count": len(bundle.influence_facts),
            }
        )
        cells.append(experiment.M8PortableFactGate3Cell.model_validate(payload, strict=True))

    payload = base.model_dump(mode="python")
    payload.update(
        {
            "cells": tuple(cells),
            "semantic_serialized_bytes": sum(len(item) for item in semantic_bytes),
            "fixed_layer_node_count": sum(item.fixed_layer_node_count for item in cells),
            "retained_first_generation_bundle_bytes": sum(len(item) for item in semantic_bytes),
        }
    )
    draft = base.model_copy(
        update={
            "cells": tuple(cells),
            "semantic_serialized_bytes": payload["semantic_serialized_bytes"],
            "fixed_layer_node_count": payload["fixed_layer_node_count"],
            "retained_first_generation_bundle_bytes": payload[
                "retained_first_generation_bundle_bytes"
            ],
        }
    )
    digest = semantic_sha256(draft, excluded_fields={"gate3_id", "content_sha256"})
    payload["gate3_id"] = f"yfm8gate3-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    return experiment.M8PortableFactGate3Result.model_validate(payload, strict=True)


def _checker_contexts_for_expanded_bundles():  # type: ignore[no-untyped-def]
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    base = runtime.replay_input
    contexts = []
    for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT):
        instances = []
        for binding in base.instances:
            semantic = binding.model_dump(mode="json")
            semantic.update(
                {
                    "stream_id": "yfts-" + "5" * 24,
                    "stream_sha256": "sha256:" + "a" * 64,
                    "regime": regime.value,
                    "temporal_seed": 2026082300,
                }
            )
            digest = semantic_sha256(
                semantic,
                excluded_fields={"binding_id", "content_sha256"},
            )
            payload = binding.model_dump(mode="python")
            payload.update(
                {
                    "binding_id": f"yfm7b-{digest[:24]}",
                    "content_sha256": f"sha256:{digest}",
                    "stream_id": "yfts-" + "5" * 24,
                    "stream_sha256": "sha256:" + "a" * 64,
                    "regime": regime,
                    "temporal_seed": 2026082300,
                }
            )
            instances.append(type(binding).model_validate(payload, strict=True))
        replay_input = build_m7_replay_input(
            m0_contract_id=base.m0_contract_id,
            m0_contract_sha256=base.m0_contract_sha256,
            problem_index_id=base.problem_index_id,
            problem_index_sha256=base.problem_index_sha256,
            m6_contract_id=base.m6_contract_id,
            m6_contract_sha256=base.m6_contract_sha256,
            m6_population_id=base.m6_population_id,
            m6_population_sha256=base.m6_population_sha256,
            policy=base.policy,
            rates=base.rates,
            fit_config=base.fit_config,
            search_config=base.search_config,
            collision_backend=base.collision_backend,
            jagua_container_guard=base.jagua_container_guard,
            problems=base.problems,
            candidate_sets=base.candidate_sets,
            instances=tuple(instances),
            horizon_end=base.horizon_end,
        )
        cell = experiment._ExecutionCell(  # noqa: SLF001
            stream=replay_input.instances,
            problem_ids=tuple(item.problem_id for item in replay_input.problems),
            replay_input=replay_input,
            verified=runtime.runtime_candidates,
        )
        contexts.append(
            mutations.M8Gate3MutationCheckerContext(
                execution_cell=cell,
                rules=runtime.rules,
                jagua_executable=None,
                freeze_id="yfm7freeze-" + "b" * 24,
                freeze_sha256="sha256:" + "b" * 64,
                expected_jagua_sha256=None,
                translation_audit_processes=1,
            )
        )
    return tuple(contexts)


def _checked_roots_for_bundles(
    gate3: experiment.M8PortableFactGate3Result,
    bundles: tuple[M8UncheckedFactBundleV2, M8UncheckedFactBundleV2],
) -> tuple[M8Gate3CheckedActionRoot, ...]:
    compact = []
    for cell, bundle in zip(gate3.cells, bundles, strict=True):
        for root in bundle.action_roots:
            compact.append(
                M8Gate3CheckedActionRoot(
                    regime=cell.regime,
                    temporal_seed=cell.temporal_seed,
                    stream_id=root.stream_id,
                    source_bundle_sha256=bundle.bundle_sha256,
                    checker_decision_id=cell.decision_id,
                    checker_decision_content_sha256=cell.decision_content_sha256,
                    root_fact_sha256=root.fact_sha256,
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
                )
            )
    return tuple(compact)


@pytest.fixture(scope="module")
def executable_gate3_inputs():  # type: ignore[no-untyped-def]
    parent = load_parent_v3_certificate_proof(_PARENT_PATH)
    bundles = (
        _expanded_bundle(TemporalRegime.NO_SIGNAL, 428),
        _expanded_bundle(TemporalRegime.REGIME_SHIFT, 459),
    )
    semantic_bytes = tuple(
        canonical_semantic_json(item.model_dump(mode="json")) for item in bundles
    )
    gate3 = _portable_gate3_for_bundles(bundles, semantic_bytes)  # type: ignore[arg-type]
    roots = _checked_roots_for_bundles(gate3, bundles)
    root_manifest = freeze_gate3_checked_root_manifest(
        gate3,
        roots,
        membership_attestation=_membership_attestation(gate3, roots),
    )
    sample = freeze_gate3_audit_sample(parent, root_manifest)
    mutation_manifest = build_gate3_mutation_manifest(
        parent,
        gate3,
        root_manifest,
        sample,
        harness_id="m8-gate3-mutation-harness-test-v1",
        harness_content_sha256=_sha("mutation-harness"),
        runtime_id="m8-gate3-mutation-runtime-test-v1",
        runtime_content_sha256=_sha("mutation-runtime"),
    )
    return (
        parent,
        gate3,
        root_manifest,
        sample,
        mutation_manifest,
        semantic_bytes,
        _checker_contexts_for_expanded_bundles(),
    )


def test_parent_mutation_is_coherent_rehashed_and_explicitly_rejected() -> None:
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    parent = load_parent_v3_certificate_proof(_PARENT_PATH)
    recipe = build_gate3_mutation_recipe(
        target_kind="parent_v3_certificate",
        target_content_sha256=parent.content_sha256,
    )
    mutated = mutations.mutate_gate3_target(recipe, parent)

    assert type(mutated) is type(parent)
    assert mutated.total_wall_seconds > parent.total_wall_seconds
    assert mutated.content_sha256 != parent.content_sha256
    assert mutated.proof_id != parent.proof_id
    assert (
        mutations.validate_gate3_mutation_cross_binding(recipe, parent, mutated)
        == "parent_v3_binding_mismatch"
    )


def test_portable_wall_time_mutation_is_rejected_by_the_official_artifact_pin() -> None:
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    gate3 = experiment.M8PortableFactGate3Result.model_validate_json(
        _PORTABLE_PATH.read_bytes(),
        strict=True,
    )
    recipe = build_gate3_mutation_recipe(
        target_kind="portable_fact_gate3",
        target_content_sha256=gate3.content_sha256,
    )

    assert (
        mutations.require_official_portable_gate3(  # noqa: SLF001
            gate3,
            label="test official portable artifact",
        )
        == gate3
    )
    mutated = mutations.mutate_gate3_target(recipe, gate3)
    with pytest.raises(ValueError, match="committed freeze"):
        mutations.require_official_portable_gate3(  # noqa: SLF001
            mutated,
            label="test mutated portable artifact",
        )


def test_all_four_typed_artifact_mutations_are_deterministic_and_cross_bound(
    gate3_inputs,
) -> None:  # type: ignore[no-untyped-def]
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    parent, gate3, root_manifest, sample, mutation_manifest = gate3_inputs
    targets = {
        "parent_v3_certificate": parent,
        "portable_fact_gate3": gate3,
        "checked_root_manifest": root_manifest,
        "audit_sample": sample,
    }
    recipes = tuple(
        item for item in mutation_manifest.recipes if item.target_kind != "checked_action_root"
    )

    assert len(recipes) == 4
    for recipe in recipes:
        original = targets[recipe.target_kind]
        first = mutations.mutate_gate3_target(recipe, original)
        second = mutations.mutate_gate3_target(recipe, original)
        assert first == second
        assert first.content_sha256 != recipe.target_content_sha256
        assert (
            mutations.validate_gate3_mutation_cross_binding(recipe, original, first)
            == recipe.expected_failure_code
        )

    changed_manifest = mutations.mutate_gate3_target(
        next(item for item in recipes if item.target_kind == "checked_root_manifest"),
        root_manifest,
    )
    assert type(changed_manifest) is M8Gate3CheckedRootManifest
    assert (
        changed_manifest.membership_attestation.producer_id
        != root_manifest.membership_attestation.producer_id
    )

    changed_sample = mutations.mutate_gate3_target(
        next(item for item in recipes if item.target_kind == "audit_sample"),
        sample,
    )
    assert type(changed_sample) is M8Gate3AuditSample
    assert changed_sample.root_manifest_content_sha256 != sample.root_manifest_content_sha256


def test_unrehashed_or_unregistered_typed_mutation_fails_closed(gate3_inputs) -> None:  # type: ignore[no-untyped-def]
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    parent, _gate3, _root_manifest, _sample, mutation_manifest = gate3_inputs
    recipe = next(
        item for item in mutation_manifest.recipes if item.target_kind == "parent_v3_certificate"
    )
    unrehashed = parent.model_dump(mode="python")
    unrehashed["total_wall_seconds"] = parent.total_wall_seconds + 1.0

    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.validate_gate3_mutation_payload(recipe, parent, unrehashed)

    wrong_recipe = build_gate3_mutation_recipe(
        target_kind="portable_fact_gate3",
        target_content_sha256=parent.content_sha256,
    )
    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.mutate_gate3_target(wrong_recipe, parent)


def test_checked_action_root_mutation_rehashes_the_fact_domain_and_is_deterministic() -> None:
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    root = _bundle().action_roots[0]
    recipe = build_gate3_mutation_recipe(
        target_kind="checked_action_root",
        target_content_sha256=root.fact_sha256,
    )

    first = mutations.mutate_gate3_target(recipe, root)
    second = mutations.mutate_gate3_target(recipe, root)

    assert first == second
    assert first.final_state_sha256 != root.final_state_sha256
    assert first.fact_sha256 != root.fact_sha256
    assert (
        mutations.validate_gate3_mutation_cross_binding(recipe, root, first)
        == "checked_action_root_binding_mismatch"
    )

    unrehashed = root.model_dump(mode="python")
    unrehashed["final_state_sha256"] = _sha("changed-without-fact-rehash")
    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.validate_gate3_mutation_payload(recipe, root, unrehashed)


def test_retained_roots_are_strictly_loaded_from_canonical_bundles_and_fail_closed() -> None:
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    bundle = _bundle()
    semantic_bytes = canonical_semantic_json(bundle.model_dump(mode="json"))
    target = bundle.action_roots[0].fact_sha256

    loaded = mutations.load_gate3_retained_action_roots(
        (semantic_bytes,),
        (target,),
    )
    assert loaded == bundle.action_roots

    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.load_gate3_retained_action_roots((semantic_bytes,), (_sha("missing-root"),))
    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.load_gate3_retained_action_roots(
            (semantic_bytes, semantic_bytes),
            (target,),
        )
    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.load_gate3_retained_action_roots(
            (semantic_bytes + b"\n",),
            (target,),
        )


def test_exact_sixteen_recipe_manifest_executes_in_fresh_supervised_workers(
    executable_gate3_inputs,
) -> None:  # type: ignore[no-untyped-def]
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    (
        parent,
        gate3,
        root_manifest,
        sample,
        manifest,
        retained_bytes,
        checker_contexts,
    ) = executable_gate3_inputs

    result = mutations.execute_gate3_mutations(
        parent,
        gate3,
        root_manifest,
        sample,
        retained_bytes,
        checker_contexts=checker_contexts,
        harness_id=manifest.authorized_harness_id,
        harness_content_sha256=manifest.authorized_harness_content_sha256,
        runtime_id=manifest.authorized_runtime_id,
        runtime_content_sha256=manifest.authorized_runtime_content_sha256,
        process_count=4,
        timeout_seconds=30.0,
    )

    assert result.manifest == manifest
    assert len(result.outcomes) == 16
    assert len({item.recipe_id for item in result.outcomes}) == 16
    assert result.registered_recipe_count == result.executed_outcome_count == 16
    assert result.rejected_mutation_count == 16
    assert result.complete_manifest_reconciliation is True
    assert result.all_expected_failure_codes_match is True
    assert result.all_required_rehashes_performed is True
    assert result.all_checker_failures_typed is True
    assert result.all_worker_exits_clean is True
    assert result.all_mutations_rejected is True
    assert result.surviving_descendant_count == 0
    assert result.surviving_registry_count == 0
    assert result.worker_fork_count == sum(item.worker_fork_count for item in result.outcomes)
    assert result.artifact_published_count == 0
    assert result.evaluation_accessed is False
    assert result.mutation_decision == "pass_executed_mutations"
    assert {item.observed_failure_code for item in result.outcomes} == {
        "parent_v3_binding_mismatch",
        "portable_gate3_binding_mismatch",
        "root_manifest_binding_mismatch",
        "sample_binding_mismatch",
        "checked_action_root_binding_mismatch",
    }
    action_outcomes = tuple(
        item
        for item in result.outcomes
        if item.expected_failure_code == "checked_action_root_binding_mismatch"
    )
    artifact_outcomes = tuple(item for item in result.outcomes if item not in action_outcomes)
    assert len(action_outcomes) == 12
    assert all(
        item.checker_failure_code == "m8_state_chain_mismatch"
        and item.first_failing_fact_sha256 is not None
        and item.first_failing_fact_sha256 != item.target_content_sha256
        and item.worker_fork_count <= 1
        for item in action_outcomes
    )
    assert all(
        item.checker_failure_code is None
        and item.first_failing_fact_sha256 is None
        and item.worker_fork_count == 0
        for item in artifact_outcomes
    )
    repeated = mutations.execute_gate3_mutations(
        parent,
        gate3,
        root_manifest,
        sample,
        retained_bytes,
        checker_contexts=checker_contexts,
        harness_id=manifest.authorized_harness_id,
        harness_content_sha256=manifest.authorized_harness_content_sha256,
        runtime_id=manifest.authorized_runtime_id,
        runtime_content_sha256=manifest.authorized_runtime_content_sha256,
        process_count=4,
        timeout_seconds=30.0,
    )
    assert repeated == result


def test_action_root_mutation_reaches_real_checker_with_exact_typed_failure(
    executable_gate3_inputs,
) -> None:  # type: ignore[no-untyped-def]
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    *_artifacts, manifest, retained_bytes, checker_contexts = executable_gate3_inputs
    recipe = next(item for item in manifest.recipes if item.target_kind == "checked_action_root")
    source = mutations._load_gate3_retained_root_sources(  # noqa: SLF001
        retained_bytes,
        (recipe.target_content_sha256,),
    )[0]
    mutated = mutations.mutate_gate3_target(recipe, source.root)
    mutated_bytes = mutations._mutated_action_root_bundle_bytes(  # noqa: SLF001
        source,
        source.root,
        mutated,
    )
    checker_by_regime = {item.regime: item for item in checker_contexts}
    request = mutations._build_action_root_checker_request(  # noqa: SLF001
        mutated_bytes,
        checker_by_regime[source.regime],
    )

    result = mutations.check_m8_fact_bundle(request)  # noqa: SLF001

    assert result.valid is False
    assert result.failure_code == "m8_state_chain_mismatch"
    assert result.first_failing_fact_sha256 == mutated.fact_sha256


def test_exact_execution_rejects_missing_or_wrong_retained_roots(
    executable_gate3_inputs,
) -> None:  # type: ignore[no-untyped-def]
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    (
        parent,
        gate3,
        root_manifest,
        sample,
        manifest,
        retained_bytes,
        checker_contexts,
    ) = executable_gate3_inputs
    common = {
        "parent_v3": parent,
        "portable_fact_gate3": gate3,
        "root_manifest": root_manifest,
        "sample": sample,
        "checker_contexts": checker_contexts,
        "process_count": 1,
        "timeout_seconds": 30.0,
    }

    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.execute_gate3_mutation_manifest(
            manifest,
            retained_bundle_bytes=retained_bytes[:1],
            **common,
        )
    with pytest.raises(mutations.M8Gate3MutationExecutionError):
        mutations.execute_gate3_mutation_manifest(
            manifest,
            retained_bundle_bytes=(retained_bytes[0], retained_bytes[0]),
            **common,
        )


def test_checker_mutation_width_cannot_exceed_the_frozen_compute_budget(
    executable_gate3_inputs,
) -> None:  # type: ignore[no-untyped-def]
    mutations = import_module("yieldforge.oracle.gate3_mutations")
    (
        parent,
        gate3,
        root_manifest,
        sample,
        manifest,
        retained_bytes,
        checker_contexts,
    ) = executable_gate3_inputs
    nested_contexts = tuple(
        replace(item, translation_audit_processes=2) for item in checker_contexts
    )

    with pytest.raises(ValueError, match="frozen 1..2 boundary"):
        replace(checker_contexts[0], translation_audit_processes=3)

    with pytest.raises(
        mutations.M8Gate3MutationExecutionError,
        match="compute-slot budget",
    ):
        mutations.execute_gate3_mutation_manifest(
            manifest,
            parent_v3=parent,
            portable_fact_gate3=gate3,
            root_manifest=root_manifest,
            sample=sample,
            retained_bundle_bytes=retained_bytes,
            checker_contexts=nested_contexts,
            process_count=8,
            timeout_seconds=30.0,
        )


def test_mutation_checker_context_rejects_evaluation_authority(
    executable_gate3_inputs,
) -> None:  # type: ignore[no-untyped-def]
    *_, checker_contexts = executable_gate3_inputs
    context = checker_contexts[0]
    evaluation_instances = tuple(
        item.model_copy(update={"partition": TemporalPartition.EVALUATION})
        for item in context.execution_cell.stream
    )
    evaluation_replay = context.execution_cell.replay_input.model_copy(
        update={"instances": evaluation_instances}
    )
    evaluation_cell = replace(
        context.execution_cell,
        stream=evaluation_instances,
        replay_input=evaluation_replay,
    )

    with pytest.raises(ValueError, match="calibration"):
        replace(context, execution_cell=evaluation_cell)


def test_mutation_worker_capability_guard_allows_only_pinned_runtime_and_bounded_fork(
    tmp_path: Path,
) -> None:
    readable = _PARENT_PATH
    publish_target = tmp_path / "must-not-exist.json"
    allowed_runtime = tmp_path / "pinned-jagua"
    allowed_runtime.write_bytes(b"test-runtime")

    read_result = experiment._run_process_phase(  # noqa: SLF001
        _attempt_guarded_mutation_access,
        (("read", str(readable)),),
        process_count=1,
    )
    publish_result = experiment._run_process_phase(  # noqa: SLF001
        _attempt_guarded_mutation_access,
        (("publish", str(publish_target)),),
        process_count=1,
    )
    evaluation_result = experiment._run_process_phase(  # noqa: SLF001
        _attempt_guarded_mutation_access,
        (("evaluate", "unused"),),
        process_count=1,
    )
    allowed_result = experiment._run_process_phase(  # noqa: SLF001
        _attempt_guarded_mutation_access,
        (("allowed_runtime", str(allowed_runtime)),),
        process_count=1,
    )
    overflow_result = experiment._run_process_phase(  # noqa: SLF001
        _attempt_guarded_mutation_access,
        (("fork_overflow", "unused"),),
        process_count=1,
    )

    assert read_result == ((True, False, 0),)
    assert publish_result == ((False, True, 0),)
    assert evaluation_result == ((True, False, 0),)
    assert allowed_result == ((False, False, 1),)
    assert overflow_result == ((False, False, 1),)
    assert publish_target.exists() is False
