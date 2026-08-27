from __future__ import annotations

import json

import pytest


def _phase_names(phases) -> set[str]:  # type: ignore[no-untyped-def]
    names: set[str] = set()
    for phase in phases:
        names.add(phase.name)
        names.update(_phase_names(phase.children))
    return names


def test_profile_records_nested_process_and_wall_phases(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.oracle import profiling

    process_ticks = iter((100, 120, 150, 180, 230, 260))
    wall_ticks = iter((1_000, 1_030, 1_080, 1_140, 1_200, 1_260))
    monkeypatch.setattr(profiling, "_process_time_ns", lambda: next(process_ticks))
    monkeypatch.setattr(profiling, "_perf_counter_ns", lambda: next(wall_ticks))

    with profiling.activate_m8_profile() as profiler:
        with profiling.profile_phase("pipeline"):
            with profiling.profile_phase("common_transition_derivation"):
                pass

    report = profiler.report()
    assert report.total_process_ns == 160
    assert report.total_wall_ns == 260
    assert report.accounted_wall_ns == 170
    assert report.unattributed_wall_ns == 90
    assert report.accounted_wall_fraction == pytest.approx(170 / 260)
    assert len(report.phases) == 1
    pipeline = report.phases[0]
    assert pipeline.name == "pipeline"
    assert pipeline.process_ns == 110
    assert pipeline.wall_ns == 170
    assert len(pipeline.children) == 1
    common = pipeline.children[0]
    assert common.name == "common_transition_derivation"
    assert common.process_ns == 30
    assert common.wall_ns == 60


def test_profile_counts_use_the_frozen_exact_counter_set() -> None:
    from yieldforge.oracle.profiling import (
        activate_m8_profile,
        increment_profile_count,
    )

    with activate_m8_profile() as profiler:
        increment_profile_count("events", 2)
        increment_profile_count("candidates", 3)
        increment_profile_count("frontier_entries", 5)
        increment_profile_count("actions", 7)
        increment_profile_count("facts", 11)
        increment_profile_count("fallbacks", 13)
        increment_profile_count("frontier_rejected_transitions", 17)
        increment_profile_count("standard_only_materializations", 19)
        increment_profile_count("full_authoritative_fallbacks", 23)
        increment_profile_count("differential_mismatches", 29)
        increment_profile_count("partially_pruned_transitions", 31)
        increment_profile_count("frontier_rejected_inventory_items", 37)
        increment_profile_count("exact_survivor_inventory_items", 41)
        increment_profile_count("counted_no_fit_transitions", 43)
        increment_profile_count("counted_no_fit_inventory_items", 47)
        increment_profile_count("counted_no_fit_candidate_searches", 53)

    assert profiler.report().counts == {
        "events": 2,
        "candidates": 3,
        "frontier_entries": 5,
        "actions": 7,
        "facts": 11,
        "fallbacks": 13,
        "frontier_rejected_transitions": 17,
        "standard_only_materializations": 19,
        "full_authoritative_fallbacks": 23,
        "differential_mismatches": 29,
        "partially_pruned_transitions": 31,
        "frontier_rejected_inventory_items": 37,
        "exact_survivor_inventory_items": 41,
        "counted_no_fit_transitions": 43,
        "counted_no_fit_inventory_items": 47,
        "counted_no_fit_candidate_searches": 53,
    }


def test_profile_rejects_unknown_counts_and_invalid_amounts() -> None:
    from yieldforge.oracle.profiling import (
        activate_m8_profile,
        increment_profile_count,
    )

    with activate_m8_profile():
        with pytest.raises(ValueError, match="unknown M8 profile counter"):
            increment_profile_count("unknown", 1)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-negative integer"):
            increment_profile_count("events", -1)
        with pytest.raises(TypeError, match="exact integer"):
            increment_profile_count("events", True)  # type: ignore[arg-type]


def test_normalized_profile_json_is_independent_of_durations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.oracle import profiling

    def build(ticks: tuple[int, ...]) -> str:
        process_ticks = iter(ticks)
        wall_ticks = iter(tuple(value * 10 for value in ticks))
        monkeypatch.setattr(profiling, "_process_time_ns", lambda: next(process_ticks))
        monkeypatch.setattr(profiling, "_perf_counter_ns", lambda: next(wall_ticks))
        with profiling.activate_m8_profile() as profiler:
            with profiling.profile_phase("pipeline"):
                profiling.increment_profile_count("events", 1)
        return profiler.report().normalized_json()

    first = build((1, 2, 4, 7))
    second = build((10, 30, 80, 130))
    assert first == second
    assert json.loads(first) == {
        "counts": {
            "actions": 0,
            "candidates": 0,
            "events": 1,
            "facts": 0,
            "fallbacks": 0,
            "frontier_entries": 0,
            "frontier_rejected_transitions": 0,
            "standard_only_materializations": 0,
            "full_authoritative_fallbacks": 0,
            "differential_mismatches": 0,
            "partially_pruned_transitions": 0,
            "frontier_rejected_inventory_items": 0,
            "exact_survivor_inventory_items": 0,
            "counted_no_fit_transitions": 0,
            "counted_no_fit_inventory_items": 0,
            "counted_no_fit_candidate_searches": 0,
        },
        "phase_tree": [{"children": [], "name": "pipeline"}],
        "schema_version": "yieldforge.m8-phase-profile.v2",
    }


def test_disabled_profile_does_not_read_clocks_or_record_data(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.oracle import profiling

    def fail_clock() -> int:
        raise AssertionError("disabled profiling read a clock")

    monkeypatch.setattr(profiling, "_process_time_ns", fail_clock)
    monkeypatch.setattr(profiling, "_perf_counter_ns", fail_clock)

    with profiling.activate_m8_profile(enabled=False) as profiler:
        with profiling.profile_phase("pipeline"):
            profiling.increment_profile_count("events", 99)

    report = profiler.report()
    assert report.total_process_ns == 0
    assert report.total_wall_ns == 0
    assert report.phases == ()
    assert set(report.counts.values()) == {0}


def test_profile_requires_well_nested_unique_phase_names() -> None:
    from yieldforge.oracle.profiling import activate_m8_profile, profile_phase

    with activate_m8_profile():
        with pytest.raises(ValueError, match="non-empty snake-case"):
            with profile_phase("Not valid"):
                pass
        with profile_phase("pipeline"):
            with pytest.raises(ValueError, match="cannot contain itself"):
                with profile_phase("pipeline"):
                    pass


def test_sparse_generator_emits_required_profile_phases_and_counts() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.profiling import activate_m8_profile
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.sparse import score_sparse_event
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    with activate_m8_profile() as profiler:
        result = score_sparse_event(request)

    report = profiler.report()
    assert {
        "certificate_generation",
        "standard_layout_materialization",
        "common_transition_derivation",
        "action_catalog_enumeration",
        "fact_serialization",
    } <= _phase_names(report.phases)
    assert report.counts["events"] == result.metrics.common_continuation_event_count
    assert report.counts["facts"] == result.metrics.common_continuation_event_count
    assert report.counts["actions"] == len(result.proofs)


def test_checker_emits_load_and_algebra_profile_phases() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.checker import check_action_proofs
    from yieldforge.oracle.profiling import activate_m8_profile
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.sparse import score_sparse_event
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    proofs = score_sparse_event(request).proofs

    with activate_m8_profile() as profiler:
        results = check_action_proofs(request, proofs)

    assert all(result.valid for result in results)
    assert {"checker_load", "checker_algebra"} <= _phase_names(profiler.report().phases)


def test_portable_fact_generator_profiles_stable_boundaries_without_semantic_drift() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.factored import (
        M8UncheckedBundleRequest,
        score_unchecked_fact_bundle,
    )
    from yieldforge.oracle.profiling import activate_m8_profile
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
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
    unprofiled = score_unchecked_fact_bundle(request)

    with activate_m8_profile() as profiler:
        profiled = score_unchecked_fact_bundle(request)

    assert profiled.bundle == unprofiled.bundle
    assert profiled.semantic_bytes == unprofiled.semantic_bytes
    assert {
        "fact_bundle_prepared_context_session",
        "fact_bundle_unchecked_traversal",
        "fact_bundle_layer_assembly",
        "fact_bundle_hash_validation",
        "fact_bundle_semantic_serialization",
        "fact_bundle_strict_roundtrip",
        "fact_bundle_telemetry",
    } <= _phase_names(profiler.report().phases)


def test_full_fact_checker_profiles_one_existing_execution_without_duplicate_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.replay import (
        enumerate_m7_action_catalog,
        initial_m7_cursor,
        m7_cursor_sha256,
        m7_semantic_runtime_sha256,
    )
    from yieldforge.oracle import fact_checker
    from yieldforge.oracle.fact_checker import (
        M8FactBundleCheckRequest,
        check_m8_fact_bundle,
    )
    from yieldforge.oracle.factored import (
        M8UncheckedBundleRequest,
        score_unchecked_fact_bundle,
    )
    from yieldforge.oracle.profiling import activate_m8_profile
    from yieldforge.oracle.proofs import m8_suffix_sha256
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    freeze_id = "yfm7freeze-" + "b" * 24
    freeze_sha256 = "sha256:" + "b" * 64
    generated = score_unchecked_fact_bundle(
        M8UncheckedBundleRequest(
            oracle_request=oracle_request,
            freeze_id=freeze_id,
            freeze_sha256=freeze_sha256,
        )
    )
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    visible = oracle_request.visibility.visible_suffix(
        current_position=catalog.event_position,
    )
    stop = catalog.event_position + 1 + len(visible)
    runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    request = M8FactBundleCheckRequest(
        semantic_bundle_bytes=generated.semantic_bytes,
        oracle_request=oracle_request,
        expected_semantic_runtime_sha256=runtime_sha256,
        expected_current_cursor_sha256=m7_cursor_sha256(cursor),
        expected_catalog_event_position=catalog.event_position,
        expected_catalog_action_ids=tuple(item.action_id for item in catalog.actions),
        expected_stop_event_position=stop,
        expected_suffix_sha256=m8_suffix_sha256(
            semantic_runtime_sha256=runtime_sha256,
            start_event_position=catalog.event_position,
            stop_event_position=stop,
            bindings=visible,
        ),
        expected_freeze_id=freeze_id,
        expected_freeze_sha256=freeze_sha256,
        allow_exact_replay=True,
    )
    calls = 0
    original_load = fact_checker._canonical_load  # noqa: SLF001

    def counted_load(semantic_bytes: bytes):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original_load(semantic_bytes)

    monkeypatch.setattr(fact_checker, "_canonical_load", counted_load)
    unprofiled = check_m8_fact_bundle(request)
    calls = 0
    with activate_m8_profile() as profiler:
        result = check_m8_fact_bundle(request)

    assert result.valid
    assert result == unprofiled
    assert calls == 1
    assert {
        "fact_bundle_request_snapshot",
        "fact_bundle_strict_load",
        "fact_bundle_authority_session",
        "fact_bundle_context_index_preparation",
        "fact_bundle_common_verification",
        "fact_bundle_request_stability",
        "fact_bundle_capability_registration",
        "fact_bundle_action_traversal",
        "fact_bundle_cleanup",
        "fact_bundle_result_materialization",
    } <= _phase_names(profiler.report().phases)


def test_common_fact_differential_audit_records_exact_portable_identity() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle.profiling import activate_m8_profile
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.sparse import audit_m8_common_transition_exactness
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )

    with activate_m8_profile() as profiler:
        audit = audit_m8_common_transition_exactness(request)

    assert audit.event_position == 1
    assert audit.event_id.startswith("yfm7e-")
    assert audit.content_sha256.startswith("sha256:")
    assert profiler.report().counts["differential_mismatches"] == 0
    assert "common_fact_differential_audit" in _phase_names(profiler.report().phases)


def test_m8_profile_command_is_calibration_only_and_explicit() -> None:
    from yieldforge.cli import build_parser

    args = build_parser().parse_args(
        [
            "benchmark",
            "m8-certificate-profile",
            "--m0",
            "m0.json",
            "--frozen-baseline",
            "freeze.json",
            "--archive-root",
            "archives",
            "--jagua-binary",
            "jagua",
            "--regime",
            "no_signal",
            "--seed",
            "2026082300",
            "--event-count",
            "2",
            "--output",
            "profile.json",
        ]
    )
    assert args.handler.__name__ == "_profile_m8_certificate"
    assert args.regime == "no_signal"
    assert args.seed == 2026082300
    assert args.event_count == 2
    assert "split" not in vars(args)

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "benchmark",
                "m8-certificate-profile",
                "--m0",
                "m0.json",
                "--frozen-baseline",
                "freeze.json",
                "--archive-root",
                "archives",
                "--jagua-binary",
                "jagua",
                "--regime",
                "no_signal",
                "--seed",
                "2026082300",
                "--event-count",
                "2",
                "--output",
                "profile.json",
                "--split",
                "evaluation",
            ]
        )
