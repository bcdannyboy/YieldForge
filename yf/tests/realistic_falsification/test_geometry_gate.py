from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely import Polygon, box

import yieldforge.realistic_falsification.geometry_gate as geometry_gate_module
from yieldforge.baseline.contracts import LayoutFitSearchConfig, LayoutFitSearchStatus
from yieldforge.baseline.geometry import generate_layout_translations
from yieldforge.baseline.jagua import (
    JaguaGeneratedPrefilterResult,
    JaguaRepresentationError,
)
from yieldforge.domain import (
    Candidate,
    CandidateReportType,
    Part,
    Placement,
    StripPackingProblem,
)
from yieldforge.experiments.contracts import M0ExperimentContract, load_frozen_json
from yieldforge.realistic_falsification.bounds import build_gate1_feasible_opening
from yieldforge.realistic_falsification.geometry_gate import (
    Gate2AggregateEvidence,
    Gate2EvidenceError,
    Gate2Origin,
    Gate2Target,
    _assess_gate2_necessary_bound,
    _default_economic_profiles,
    _event_geometry_bindings,
    _load_official_gate2_context,
    _opening_geometry_variants,
    _raw_matching_reward,
    _reconstruct_official_payload,
    _require_gate2_stage_closure,
    aggregate_gate2_streams,
    assess_gate2_edge,
    authenticate_official_gate2_evaluation,
    classify_gate2_headroom,
    evaluate_gate2_stream,
    evaluate_official_gate2,
)
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import (
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    canonical_polygon_record,
    derive_remnant_id,
)


def _material(key: str) -> MaterialIdentity:
    return MaterialIdentity(
        material_code=key,
        grade=key,
        thickness=key,
        surface=key,
        grain=key,
        provenance=MaterialProvenance.ASSUMED,
    )


def _remnant(*, key: str, width: float = 10.0, height: float = 10.0) -> RemnantStock:
    material = _material(key)
    geometry = canonical_polygon_record(box(0.0, 0.0, width, height))
    lineage = RemnantLineage.root(
        root_stock_id=f"stock-{key}",
        source_candidate_id=f"origin-candidate-{key}",
        source_component_sha256=geometry.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, material),
        geometry=geometry,
        material=material,
        root_sheet_area=float(width * height),
        root_sheet_short_side=float(min(width, height)),
        lineage=lineage,
    )


def _holed_remnant(*, key: str = "material-a") -> RemnantStock:
    material = _material(key)
    polygon = Polygon(
        shell=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)),
        holes=[((2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0), (2.0, 2.0))],
    )
    geometry = canonical_polygon_record(polygon)
    lineage = RemnantLineage.root(
        root_stock_id="stock-holed",
        source_candidate_id="origin-candidate-holed",
        source_component_sha256=geometry.polygon_sha256,
    )
    return RemnantStock(
        remnant_id=derive_remnant_id(lineage, geometry, material),
        geometry=geometry,
        material=material,
        root_sheet_area=100.0,
        root_sheet_short_side=10.0,
        lineage=lineage,
    )


def _problem_candidate(
    *, width: float = 4.0, height: float = 4.0
) -> tuple[StripPackingProblem, Candidate]:
    part = Part(
        id="part-1",
        shape=[(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)],
        demand=1,
        allowed_orientations=[0.0],
    )
    problem = StripPackingProblem(
        name="gate2-test",
        strip_height=max(10.0, height),
        sheet_length=max(10.0, width),
        parts=[part],
    )
    candidate = Candidate(
        candidate_id=f"candidate-{width}-{height}",
        report_type=CandidateReportType.FINAL,
        seed=0,
        width=width,
        density=1.0,
        placements=[Placement(part_id=part.id, rotation=0.0, translation=(0.0, 0.0))],
    )
    return problem, candidate


def _origin(*, key: str = "material-a", width: float = 10.0, height: float = 10.0):
    return Gate2Origin(
        stream_id="stream-1",
        event_position=0,
        event_id="event-origin",
        released_at=datetime(2026, 1, 1, tzinfo=UTC),
        material_key=key,
        reference_area=100.0,
        source_kind="synthetic_test",
        source_binding_sha256="sha256:" + "1" * 64,
        remnant=_remnant(key=key, width=width, height=height),
    )


def _target(
    *,
    key: str = "material-a",
    width: float = 4.0,
    height: float = 4.0,
    event_position: int = 2,
    event_id: str = "event-target",
    release_day: int = 3,
    purchase_cost: float = 20.0,
):
    problem, candidate = _problem_candidate(width=width, height=height)
    release = datetime(2026, 1, release_day, tzinfo=UTC)
    return Gate2Target(
        stream_id="stream-1",
        event_position=event_position,
        event_id=event_id,
        known_at=release - timedelta(hours=24),
        released_at=release,
        material_key=key,
        opening_id=f"opening-{event_id}",
        opening_content_sha256="sha256:" + "2" * 64,
        purchase_cost=purchase_cost,
        source_kind="synthetic_test",
        source_binding_sha256="sha256:" + "3" * 64,
        problem=problem,
        candidate=candidate,
    )


def _rules():
    m0 = load_frozen_json(
        Path(__file__).parents[2] / "experiments/m0-contract-v1.json", M0ExperimentContract
    )
    return rule_set_from_m0(m0.remnant_eligibility)


def _synthetic_pinned_jagua(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    payload = b"synthetic pinned Jagua 0.7 test executable"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o700)
    monkeypatch.setattr(
        geometry_gate_module,
        "_PINNED_JAGUA_SHA256",
        hashlib.sha256(payload).hexdigest(),
        raising=False,
    )
    return path.resolve()


def test_material_mismatch_is_a_certified_no_fit() -> None:
    edge = assess_gate2_edge(_origin(key="material-a"), _target(key="material-b"))

    assert edge.status == "certified_no_fit"
    assert edge.rejection_certificate is not None
    assert edge.rejection_certificate.reason == "material_mismatch"
    assert edge.search_result is None
    assert edge.consumption is None
    assert edge.central_reward is None
    assert edge.adverse_reward is None


@pytest.mark.parametrize(
    ("width", "height", "reason"),
    [
        (11.0, 10.0, "footprint_area_exceeds_remnant"),
        (11.0, 5.0, "footprint_width_exceeds_remnant"),
        (5.0, 11.0, "footprint_height_exceeds_remnant"),
    ],
)
def test_candidate_specific_area_and_bounds_rejections_are_certified(
    width: float,
    height: float,
    reason: str,
) -> None:
    edge = assess_gate2_edge(_origin(), _target(width=width, height=height))

    assert edge.status == "certified_no_fit"
    assert edge.rejection_certificate is not None
    assert edge.rejection_certificate.reason == reason


def test_witnessed_fit_persists_exact_translation_and_consumption() -> None:
    edge = assess_gate2_edge(_origin(), _target(), rules=_rules())

    assert edge.status == "fit_witnessed"
    assert edge.rejection_certificate is None
    assert edge.search_result is not None
    assert edge.search_result.status is LayoutFitSearchStatus.FIT
    assert edge.search_result.translation is not None
    assert edge.consumption is not None
    assert edge.consumption.translation == edge.search_result.translation
    assert len(edge.consumption.placements) == 1
    assert edge.consumption.accounting.reconciliation_delta <= (
        edge.consumption.accounting.area_tolerance
    )
    assert edge.central_reward is not None
    assert edge.adverse_reward is not None


def test_bounded_no_witness_remains_optimistically_unresolved() -> None:
    origin = replace(_origin(), remnant=_holed_remnant())

    edge = assess_gate2_edge(origin, _target(), rules=_rules())

    assert edge.status == "unresolved_optimistically_counted"
    assert edge.rejection_certificate is None
    assert edge.search_result is not None
    assert edge.search_result.status is LayoutFitSearchStatus.NO_WITNESS_WITHIN_REGISTERED_SEARCH
    assert edge.consumption is None
    assert edge.central_reward is not None
    assert edge.adverse_reward is not None
    assert edge.optimistically_included_in_matching


def test_budget_truncation_is_unresolved_and_in_every_eligible_optimistic_graph() -> None:
    origin = replace(_origin(), remnant=_holed_remnant())
    edge = assess_gate2_edge(
        origin,
        _target(),
        rules=_rules(),
        search_config=LayoutFitSearchConfig(maximum_candidates=1),
    )
    result = evaluate_gate2_stream(
        stream_id="stream-1",
        corpus_id="lectra-m3-m4",
        baseline_cost=100.0,
        lower_bound_cost=0.0,
        edges=(edge,),
    )

    assert edge.status == "unresolved_optimistically_counted"
    assert edge.search_result is not None
    assert edge.search_result.budget_truncated is True
    assert edge.rejection_certificate is None
    assert edge.edge_id in result.central_total.unresolved_edge_ids_in_graph
    assert edge.edge_id in result.central_unknown.unresolved_edge_ids_in_graph
    assert edge.edge_id in result.adverse_total.unresolved_edge_ids_in_graph
    assert edge.edge_id in result.adverse_unknown.unresolved_edge_ids_in_graph


def test_unresolved_safe_reward_participates_and_stream_reward_is_capped_by_gate1_gap() -> None:
    origin = replace(_origin(), remnant=_holed_remnant())
    unresolved = assess_gate2_edge(
        origin,
        _target(
            event_position=2,
            event_id="event-unresolved",
            release_day=3,
            purchase_cost=60.0,
        ),
        rules=_rules(),
    )
    witnessed = assess_gate2_edge(
        origin,
        _target(
            width=1.0,
            height=1.0,
            event_position=3,
            event_id="event-witnessed",
            release_day=4,
            purchase_cost=20.0,
        ),
        rules=_rules(),
    )

    result = evaluate_gate2_stream(
        stream_id="stream-1",
        corpus_id="lectra-m3-m4",
        baseline_cost=100.0,
        lower_bound_cost=70.0,
        edges=(witnessed, unresolved),
    )

    assert result.central_total.selected_edge_ids == (unresolved.edge_id,)
    assert result.central_total.raw_reward_micro_units > 30_000_000
    assert result.central_total.cap_micro_units == 30_000_000
    assert result.central_total.capped_reward_micro_units == 30_000_000
    assert result.central_savings_percent == 30.0
    assert result.central_unknown_points == 30.0
    assert result.unresolved_optimistically_counted == 1
    assert result.all_unresolved_edges_optimistically_included


def test_unknown_only_matching_upper_bounds_total_minus_known_under_one_use() -> None:
    origin = _origin()
    known_target = _target(
        event_position=1,
        event_id="event-known",
        release_day=2,
        purchase_cost=30.0,
    )
    unknown_target = _target(
        event_position=2,
        event_id="event-unknown",
        release_day=3,
        purchase_cost=20.0,
    )
    edges = (
        assess_gate2_edge(origin, known_target, rules=_rules()),
        assess_gate2_edge(origin, unknown_target, rules=_rules()),
    )

    total = _raw_matching_reward(edges, arm="central", scope="total")
    known = _raw_matching_reward(edges, arm="central", scope="known")
    unknown = _raw_matching_reward(edges, arm="central", scope="unknown")

    assert edges[0].unknown_at_origin is False
    assert edges[1].unknown_at_origin is True
    assert total - known <= unknown


def test_exact_fit_and_unsearched_optimistic_survivor_have_identical_gate2_decision_value() -> None:
    exact_results = []
    relaxed_results = []
    for stream_id, corpus_id in (
        ("stream-lectra-bound", "lectra-m3-m4"),
        ("stream-loco-bound", "loco-2dics"),
    ):
        origin = replace(
            _origin(),
            stream_id=stream_id,
            event_id=f"origin-{stream_id}",
        )
        target = replace(
            _target(purchase_cost=20.0),
            stream_id=stream_id,
            event_id=f"target-{stream_id}",
            opening_id=f"opening-{stream_id}",
        )
        exact = assess_gate2_edge(origin, target, rules=_rules())
        relaxed = _assess_gate2_necessary_bound(
            origin,
            target,
            fit_config=RemnantFitConfig(),
            economic_profiles=_default_economic_profiles(),
        )
        assert exact.status == "fit_witnessed"
        assert relaxed.status == "unresolved_optimistically_counted"
        assert relaxed.resolution_basis == "not_searched_favorable_relaxation"
        assert relaxed.search_result is None
        assert relaxed.central_reward == exact.central_reward
        assert relaxed.adverse_reward == exact.adverse_reward
        exact_results.append(
            evaluate_gate2_stream(
                stream_id=stream_id,
                corpus_id=corpus_id,
                baseline_cost=100.0,
                lower_bound_cost=0.0,
                edges=(exact,),
            )
        )
        relaxed_results.append(
            evaluate_gate2_stream(
                stream_id=stream_id,
                corpus_id=corpus_id,
                baseline_cost=100.0,
                lower_bound_cost=0.0,
                edges=(relaxed,),
            )
        )

    for exact, relaxed in zip(exact_results, relaxed_results, strict=True):
        for field_name in (
            "central_total",
            "central_unknown",
            "adverse_total",
            "adverse_unknown",
        ):
            assert (
                getattr(exact, field_name).raw_reward_micro_units
                == getattr(
                    relaxed,
                    field_name,
                ).raw_reward_micro_units
            )
        assert (
            exact.central_savings_percent,
            exact.central_unknown_points,
            exact.adverse_savings_percent,
            exact.adverse_unknown_points,
        ) == (
            relaxed.central_savings_percent,
            relaxed.central_unknown_points,
            relaxed.adverse_savings_percent,
            relaxed.adverse_unknown_points,
        )
    exact_aggregates = aggregate_gate2_streams(tuple(exact_results))
    relaxed_aggregates = aggregate_gate2_streams(tuple(relaxed_results))
    assert exact_aggregates == relaxed_aggregates
    assert classify_gate2_headroom(
        exact_aggregates,
        blocking_error_count=0,
        all_unresolved_edges_optimistically_included=True,
    ) == classify_gate2_headroom(
        relaxed_aggregates,
        blocking_error_count=0,
        all_unresolved_edges_optimistically_included=True,
    )
    with pytest.raises(ValueError, match="cannot open Gate 3"):
        _require_gate2_stage_closure(
            status="gate_2_survived",
            evaluation_stage="stage_a_favorable_superset",
            stream_results=tuple(relaxed_results),
        )
    _require_gate2_stage_closure(
        status="gate_2_survived",
        evaluation_stage="stage_b_exact_attempted",
        stream_results=tuple(exact_results),
    )


def test_fast_path_optimistic_graph_is_a_superset_of_exact_witness_graph() -> None:
    ordinary = _origin()
    holed = replace(
        _origin(),
        event_id="event-origin-holed",
        remnant=_holed_remnant(),
    )
    pairs = (
        (ordinary, _target(event_id="event-fit")),
        (holed, _target(event_id="event-bounded-unresolved")),
        (ordinary, _target(key="material-b", event_id="event-certified")),
    )
    exact_edges = tuple(
        assess_gate2_edge(origin, target, rules=_rules()) for origin, target in pairs
    )
    fast_edges = tuple(
        _assess_gate2_necessary_bound(
            origin,
            target,
            fit_config=RemnantFitConfig(),
            economic_profiles=_default_economic_profiles(),
        )
        for origin, target in pairs
    )

    def graph_pairs(edges):
        return {
            (
                edge.origin_event_id,
                edge.origin_remnant_id,
                edge.target_event_id,
                edge.target_candidate_id,
            )
            for edge in edges
            if edge.status
            in {
                "fit_witnessed",
                "unresolved_optimistically_counted",
            }
        }

    assert graph_pairs(exact_edges).issubset(graph_pairs(fast_edges))
    assert tuple(edge.status for edge in exact_edges) == (
        "fit_witnessed",
        "unresolved_optimistically_counted",
        "certified_no_fit",
    )
    assert tuple((edge.status, edge.resolution_basis) for edge in fast_edges) == (
        ("unresolved_optimistically_counted", "not_searched_favorable_relaxation"),
        ("unresolved_optimistically_counted", "not_searched_favorable_relaxation"),
        ("certified_no_fit", "necessary_filter_certificate"),
    )


@pytest.mark.parametrize(
    "evaluation_stage",
    ("stage_a_favorable_superset", "stage_b_exact_attempted"),
)
def test_official_stage_prepares_each_target_layout_once_across_multiple_origins(
    monkeypatch: pytest.MonkeyPatch,
    evaluation_stage: str,
) -> None:
    origins = (
        _origin(),
        replace(
            _origin(),
            event_position=1,
            event_id="event-origin-second",
            released_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    )
    target = _target(event_position=3, release_day=4)
    expected = tuple(
        _assess_gate2_necessary_bound(
            origin,
            target,
            fit_config=RemnantFitConfig(),
            economic_profiles=_default_economic_profiles(),
        )
        if evaluation_stage == "stage_a_favorable_superset"
        else assess_gate2_edge(
            origin,
            target,
            fit_config=RemnantFitConfig(),
            search_config=LayoutFitSearchConfig(),
            rules=_rules(),
            economic_profiles=_default_economic_profiles(),
        )
        for origin in origins
    )
    calls = []
    original = geometry_gate_module.prepare_layout_footprint

    def counted_prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(
        geometry_gate_module,
        "prepare_layout_footprint",
        counted_prepare,
    )

    edges = geometry_gate_module._evaluate_official_edge_graph(
        origins=origins,
        targets=(target,),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(),
        rules=_rules(),
        economic_profiles=_default_economic_profiles(),
        evaluation_stage=evaluation_stage,
    )

    assert len(edges) == 2
    assert edges == expected
    assert len(calls) == 1


@pytest.mark.parametrize(
    "evaluation_stage",
    ("stage_a_favorable_superset", "stage_b_exact_attempted"),
)
def test_official_stage_repeats_one_preparation_failure_for_every_eligible_edge(
    monkeypatch: pytest.MonkeyPatch,
    evaluation_stage: str,
) -> None:
    origins = (
        _origin(),
        replace(
            _origin(),
            event_position=1,
            event_id="event-origin-second",
            released_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    )
    target = _target(event_position=3, release_day=4)
    calls = []

    def failed_prepare(*args, **kwargs):
        calls.append((args, kwargs))
        raise ValueError("synthetic preparation failure")

    monkeypatch.setattr(
        geometry_gate_module,
        "prepare_layout_footprint",
        failed_prepare,
    )

    edges = geometry_gate_module._evaluate_official_edge_graph(
        origins=origins,
        targets=(target,),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(),
        rules=_rules(),
        economic_profiles=_default_economic_profiles(),
        evaluation_stage=evaluation_stage,
    )

    assert len(calls) == 1
    assert len(edges) == 2
    assert all(edge.status == "blocking_error" for edge in edges)
    assert all(edge.blocking_error_code == "layout_preparation_failed" for edge in edges)


def test_official_jagua_discovery_requires_the_exact_pinned_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = Path("native/m7-jagua-spike/target/release/yieldforge-m7-jagua-spike")
    executable = _synthetic_pinned_jagua(tmp_path / relative, monkeypatch)

    assert geometry_gate_module._discover_pinned_jagua_executable(tmp_path) == executable

    executable.write_bytes(b"tampered")
    executable.chmod(0o700)

    assert geometry_gate_module._discover_pinned_jagua_executable(tmp_path) is None


def test_official_stage_b_passes_frozen_jagua_prefilter_to_authoritative_shapely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = _origin()
    targets = (
        _target(event_position=2, event_id="target-first", release_day=3),
        _target(
            width=2.0,
            height=2.0,
            event_position=3,
            event_id="target-second",
            release_day=4,
        ),
    )
    fit_config = RemnantFitConfig()
    search_config = LayoutFitSearchConfig()
    profiles = _default_economic_profiles()
    expected = tuple(
        assess_gate2_edge(
            origin,
            target,
            fit_config=fit_config,
            search_config=search_config,
            rules=_rules(),
            economic_profiles=profiles,
        )
        for target in targets
    )
    executable = _synthetic_pinned_jagua(tmp_path / "jagua", monkeypatch)
    prefilter_calls = []
    shapely_calls = []
    remnant_preparations = []
    original_search = geometry_gate_module.search_layout_translation
    original_prepare_remnant = geometry_gate_module.prepare_remnant_geometry

    def fake_prefilter(
        received_executable,
        *,
        remnant,
        layouts,
        fit_config,
        search_config,
        container_guard,
    ):
        prefilter_calls.append((received_executable, remnant, layouts, container_guard))
        batches = tuple(
            generate_layout_translations(
                origin.remnant,
                target.candidate,
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=layout,
                prepared_remnant=remnant,
            )
            for target, layout in zip(targets, layouts, strict=True)
        )
        masks = tuple((False,) * len(batch.translations) for batch in batches)
        return JaguaGeneratedPrefilterResult(
            translation_batches=batches,
            collision_masks=masks,
            guarded_query_count=sum(len(mask) for mask in masks),
            jagua_rejection_count=0,
            build_microseconds=1,
            generation_microseconds=2,
            query_microseconds=3,
            wall_seconds=0.001,
        )

    def recorded_search(*args, **kwargs):
        shapely_calls.append(kwargs)
        return original_search(*args, **kwargs)

    def counted_prepare_remnant(*args, **kwargs):
        remnant_preparations.append((args, kwargs))
        return original_prepare_remnant(*args, **kwargs)

    monkeypatch.setattr(
        geometry_gate_module,
        "run_jagua_generated_prefilter",
        fake_prefilter,
        raising=False,
    )
    monkeypatch.setattr(
        geometry_gate_module,
        "search_layout_translation",
        recorded_search,
    )
    monkeypatch.setattr(
        geometry_gate_module,
        "prepare_remnant_geometry",
        counted_prepare_remnant,
        raising=False,
    )

    edges = geometry_gate_module._evaluate_official_edge_graph(
        origins=(origin,),
        targets=targets,
        fit_config=fit_config,
        search_config=search_config,
        rules=_rules(),
        economic_profiles=profiles,
        evaluation_stage="stage_b_exact_attempted",
        jagua_executable=executable,
    )

    assert edges == expected
    assert len(prefilter_calls) == 1
    assert prefilter_calls[0][0] == executable
    assert tuple(item.candidate_id for item in prefilter_calls[0][2]) == tuple(
        target.candidate.candidate_id for target in targets
    )
    assert len(remnant_preparations) == 1
    assert len(shapely_calls) == 2
    assert all(call["prepared_remnant"] is prefilter_calls[0][1] for call in shapely_calls)
    assert all(call["translation_candidates"] is not None for call in shapely_calls)
    assert all(call["collision_prefilter"] is not None for call in shapely_calls)


def test_official_stage_b_never_executes_a_tampered_jagua_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = _origin()
    target = _target()
    expected = assess_gate2_edge(origin, target, rules=_rules())
    executable = _synthetic_pinned_jagua(tmp_path / "jagua", monkeypatch)
    executable.write_bytes(b"tampered after discovery")
    executable.chmod(0o700)

    def forbidden_prefilter(*args, **kwargs):
        raise AssertionError("tampered Jagua binary must never execute")

    monkeypatch.setattr(
        geometry_gate_module,
        "run_jagua_generated_prefilter",
        forbidden_prefilter,
        raising=False,
    )

    edges = geometry_gate_module._evaluate_official_edge_graph(
        origins=(origin,),
        targets=(target,),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(),
        rules=_rules(),
        economic_profiles=_default_economic_profiles(),
        evaluation_stage="stage_b_exact_attempted",
        jagua_executable=executable,
    )

    assert edges == (expected,)


def test_official_stage_b_falls_back_to_exact_shapely_when_jagua_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = _origin()
    target = _target()
    expected = assess_gate2_edge(origin, target, rules=_rules())
    executable = _synthetic_pinned_jagua(tmp_path / "jagua", monkeypatch)
    calls = []

    def unsupported(*args, **kwargs):
        calls.append((args, kwargs))
        raise JaguaRepresentationError("synthetic unsupported representation")

    monkeypatch.setattr(
        geometry_gate_module,
        "run_jagua_generated_prefilter",
        unsupported,
        raising=False,
    )

    edges = geometry_gate_module._evaluate_official_edge_graph(
        origins=(origin,),
        targets=(target,),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(),
        rules=_rules(),
        economic_profiles=_default_economic_profiles(),
        evaluation_stage="stage_b_exact_attempted",
        jagua_executable=executable,
    )

    assert len(calls) == 1
    assert edges == (expected,)


def _stream_result(stream_id: str, corpus_id: str, purchase_cost: float):
    origin = replace(
        _origin(),
        stream_id=stream_id,
        event_id=f"origin-{stream_id}",
    )
    target = replace(
        _target(purchase_cost=purchase_cost),
        stream_id=stream_id,
        event_id=f"target-{stream_id}",
        opening_id=f"opening-{stream_id}",
    )
    edge = assess_gate2_edge(origin, target, rules=_rules())
    return evaluate_gate2_stream(
        stream_id=stream_id,
        corpus_id=corpus_id,
        baseline_cost=100.0,
        lower_bound_cost=0.0,
        edges=(edge,),
    )


def test_aggregation_is_equal_stream_within_corpus_then_equal_corpus_pool() -> None:
    lectra = _stream_result("stream-lectra", "lectra-m3-m4", 60.0)
    loco = tuple(_stream_result(f"stream-loco-{index}", "loco-2dics", 20.0) for index in range(3))

    aggregates = aggregate_gate2_streams((lectra, *loco))

    assert tuple(item.aggregate_id for item in aggregates) == (
        "lectra-m3-m4",
        "loco-2dics",
        "equal-corpus-pool",
    )
    for field_name in (
        "central_savings_percent",
        "central_unknown_points",
        "adverse_savings_percent",
        "adverse_unknown_points",
    ):
        assert getattr(aggregates[2], field_name) == pytest.approx(
            (getattr(aggregates[0], field_name) + getattr(aggregates[1], field_name)) / 2
        )
        assert getattr(aggregates[2], field_name) != pytest.approx(
            sum(getattr(item, field_name) for item in (lectra, *loco)) / 4
        )


def _aggregate(
    aggregate_id: str,
    *,
    central_savings: float,
    central_unknown: float,
    adverse_savings: float,
    adverse_unknown: float,
) -> Gate2AggregateEvidence:
    return Gate2AggregateEvidence(
        aggregate_id=aggregate_id,
        stream_count=20 if aggregate_id != "equal-corpus-pool" else 40,
        central_savings_percent=central_savings,
        central_unknown_points=central_unknown,
        adverse_savings_percent=adverse_savings,
        adverse_unknown_points=adverse_unknown,
    )


def test_headroom_threshold_equality_survives_and_one_subfloor_metric_abandons() -> None:
    exact_floor = tuple(
        _aggregate(
            aggregate_id,
            central_savings=2.5,
            central_unknown=1.5,
            adverse_savings=1.5,
            adverse_unknown=0.5,
        )
        for aggregate_id in ("lectra-m3-m4", "loco-2dics", "equal-corpus-pool")
    )

    assert (
        classify_gate2_headroom(
            exact_floor,
            blocking_error_count=0,
            all_unresolved_edges_optimistically_included=True,
        )
        == "gate_2_survived"
    )
    below = list(exact_floor)
    below[1] = _aggregate(
        "loco-2dics",
        central_savings=2.499999,
        central_unknown=1.5,
        adverse_savings=1.5,
        adverse_unknown=0.5,
    )
    assert (
        classify_gate2_headroom(
            tuple(below),
            blocking_error_count=0,
            all_unresolved_edges_optimistically_included=True,
        )
        == "insufficient_headroom"
    )


def test_blocking_error_invalidates_gate2_instead_of_opening_gate3_or_abandoning() -> None:
    zero = tuple(
        _aggregate(
            aggregate_id,
            central_savings=0.0,
            central_unknown=0.0,
            adverse_savings=0.0,
            adverse_unknown=0.0,
        )
        for aggregate_id in ("lectra-m3-m4", "loco-2dics", "equal-corpus-pool")
    )

    assert (
        classify_gate2_headroom(
            zero,
            blocking_error_count=1,
            all_unresolved_edges_optimistically_included=False,
        )
        == "invalid_test"
    )
    assert (
        classify_gate2_headroom(
            zero,
            blocking_error_count=0,
            all_unresolved_edges_optimistically_included=True,
        )
        == "insufficient_headroom"
    )


def test_public_edge_api_does_not_accept_unbound_prepared_geometry() -> None:
    forged_layout = SimpleNamespace(candidate_id="forged", area=10_000.0)

    with pytest.raises(TypeError, match="prepared_layout"):
        assess_gate2_edge(  # type: ignore[call-arg]
            _origin(),
            _target(),
            rules=_rules(),
            prepared_layout=forged_layout,
        )


@pytest.fixture(scope="module")
def official_context():
    return _load_official_gate2_context(Path(__file__).parents[2])


def test_one_real_lectra_payload_reconstructs_m4_origin_and_m3_layout(
    official_context,
) -> None:
    payload = next(
        item
        for item in official_context.gate1.bundle.population.payloads
        if item.source_kind == "lectra"
    )
    selected = payload.candidate_references[0].candidate_id

    reconstructed = _reconstruct_official_payload(
        official_context,
        payload=payload,
        selected_candidate_id=selected,
        material_key="lectra-material-1",
    )

    assert reconstructed.problem.name.startswith("lectra-task-")
    assert reconstructed.candidate.candidate_id == selected
    assert len(reconstructed.origin_remnants) == 1
    assert reconstructed.origin_remnants[0].material.material_code == "lectra-material-1"
    assert reconstructed.standard_consumption is None


def test_every_real_lectra_candidate_is_emitted_as_origin_and_target_variant(
    official_context,
) -> None:
    stream = next(
        item
        for item in official_context.gate1.bundle.population.streams
        if item.corpus_id == "lectra-m3-m4"
        and item.partition == "confirmation"
        and item.stream_kind == "primary"
    )
    payload_by_id = {
        item.payload_id: item for item in official_context.gate1.bundle.population.payloads
    }
    first_event = stream.events[0]
    later_event = next(
        item for item in stream.events[1:] if item.material_key == first_event.material_key
    )
    reconstruction_cache = {}

    def opening(event):
        payload = payload_by_id[event.payload_id]
        variants = _opening_geometry_variants(
            official_context,
            payload=payload,
            opening=SimpleNamespace(
                candidate_options=tuple(
                    (item.candidate_id, item.content_sha256)
                    for item in payload.candidate_references
                ),
                material_group=event.material_key,
            ),
            cache=reconstruction_cache,
        )
        selected = variants[0]
        stock_area = selected.problem.sheet_length * selected.problem.strip_height
        return build_gate1_feasible_opening(
            event_position=event.position,
            event_id=event.event_id,
            payload_id=event.payload_id,
            material_group=event.material_key,
            reference_area_key=event.material_key,
            source_kind="lectra",
            candidate_options=tuple(
                (item.candidate_id, item.content_sha256) for item in payload.candidate_references
            ),
            selected_candidate_id=selected.candidate.candidate_id,
            selection_rule="registered_candidate_position_0",
            verification_kind="lectra_m3_candidate_geometry",
            geometry_witness_sha256="sha256:" + "a" * 64,
            known_positions_at_release=tuple(range(event.position + 1)),
            stock_area=stock_area,
            reference_area=stock_area,
            used_layout_width=selected.candidate.width,
            purchased_stock_width=selected.problem.sheet_length,
            purchased_stock_height=selected.problem.strip_height,
        )

    first_opening = opening(first_event)
    later_opening = opening(later_event)
    origins, _first_targets = _event_geometry_bindings(
        official_context,
        stream_id=stream.stream_id,
        event=first_event,
        opening=first_opening,
        payload=payload_by_id[first_event.payload_id],
        reconstruction_cache=reconstruction_cache,
    )
    _later_origins, targets = _event_geometry_bindings(
        official_context,
        stream_id=stream.stream_id,
        event=later_event,
        opening=later_opening,
        payload=payload_by_id[later_event.payload_id],
        reconstruction_cache=reconstruction_cache,
    )

    expected_origin_candidates = tuple(
        item.candidate_id for item in payload_by_id[first_event.payload_id].candidate_references
    )
    expected_target_candidates = tuple(
        item.candidate_id for item in payload_by_id[later_event.payload_id].candidate_references
    )
    assert tuple(item.remnant.lineage.source_candidate_id for item in origins) == (
        expected_origin_candidates
    )
    assert tuple(item.candidate.candidate_id for item in targets) == (expected_target_candidates)
    edges = tuple(
        assess_gate2_edge(origin, target, rules=official_context.rules)
        for origin in origins
        for target in targets
    )
    assert {(edge.origin_remnant_id, edge.target_candidate_id) for edge in edges} == {
        (origin.remnant.remnant_id, target.candidate.candidate_id)
        for origin in origins
        for target in targets
    }


def test_rehashed_or_substituted_official_lectra_evidence_fails_closed(
    official_context,
) -> None:
    payloads = tuple(
        item
        for item in official_context.gate1.bundle.population.payloads
        if item.source_kind == "lectra"
    )
    payload = payloads[0]
    rehashed = payload.model_copy(update={"payload_id": "yfm11pl-" + "f" * 24})

    with pytest.raises(ValueError, match="official population"):
        _reconstruct_official_payload(
            official_context,
            payload=rehashed,
            selected_candidate_id=payload.candidate_references[0].candidate_id,
            material_key="lectra-material-1",
        )
    substituted_candidate = next(
        item.candidate_id
        for other in payloads[1:]
        for item in other.candidate_references
        if item.candidate_id not in {value.candidate_id for value in payload.candidate_references}
    )
    with pytest.raises(ValueError, match="join M3 and M4 exactly"):
        _reconstruct_official_payload(
            official_context,
            payload=payload,
            selected_candidate_id=substituted_candidate,
            material_key="lectra-material-1",
        )


def test_official_evaluator_refuses_a_prerequisite_that_did_not_open_gate2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context_loads = []
    monkeypatch.setattr(
        geometry_gate_module,
        "authenticate_official_gate1_evaluation",
        lambda *args, **kwargs: SimpleNamespace(
            status="invalid_test",
            opens_gate_2=False,
        ),
    )
    monkeypatch.setattr(
        geometry_gate_module,
        "_load_official_gate2_context",
        lambda *args, **kwargs: context_loads.append(True),
    )

    with pytest.raises(Gate2EvidenceError, match="did not exactly open"):
        evaluate_official_gate2(
            repository_root=tmp_path,
            gate1_result=SimpleNamespace(),  # type: ignore[arg-type]
        )

    assert context_loads == []


def test_gate2_authenticator_rejects_nonmodel_evidence_before_reexecution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executions = []
    monkeypatch.setattr(
        geometry_gate_module,
        "evaluate_official_gate2",
        lambda **kwargs: executions.append(kwargs),
    )

    with pytest.raises(Gate2EvidenceError, match="not a valid artifact"):
        authenticate_official_gate2_evaluation(
            SimpleNamespace(),  # type: ignore[arg-type]
            repository_root=tmp_path,
            gate1_result=SimpleNamespace(),  # type: ignore[arg-type]
        )

    assert executions == []


def test_one_real_loco_payload_reconstructs_fallback_stock_and_residuals(
    official_context,
) -> None:
    payload = next(
        item
        for item in official_context.gate1.bundle.population.payloads
        if item.source_kind == "loco_2dics"
    )
    assert payload.fallback_stock is not None
    instance = payload.geometry_references[0].instance_name
    assert instance is not None

    reconstructed = _reconstruct_official_payload(
        official_context,
        payload=payload,
        selected_candidate_id=payload.fallback_stock.stock_id,
        material_key=f"loco:{instance}",
    )

    assert reconstructed.candidate.candidate_id == payload.fallback_stock.stock_id
    assert reconstructed.problem.sheet_length == payload.fallback_stock.width
    assert reconstructed.problem.strip_height == payload.fallback_stock.height
    assert reconstructed.standard_consumption is not None
    accounting = reconstructed.standard_consumption.accounting
    assert accounting.reconciliation_delta <= accounting.area_tolerance
    assert tuple(reconstructed.origin_remnants) == tuple(
        reconstructed.standard_consumption.children
    )
