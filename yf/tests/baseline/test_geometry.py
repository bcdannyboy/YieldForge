from __future__ import annotations

from tests.baseline.test_replay import _problem, _two_event_runtime, _verified
from yieldforge.baseline.contracts import LayoutFitSearchStatus
from yieldforge.baseline.geometry import (
    certify_translation_impossible,
    generate_layout_translations,
    prepare_layout_footprint,
    prepare_remnant_geometry,
    search_layout_translation,
)
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
)


def _returned_remnant():  # type: ignore[no-untyped-def]
    runtime = _two_event_runtime()
    cursor = initial_m7_cursor(runtime.replay_input)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    descriptor = catalog.actions[0]
    step = apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=("geometry_fixture",),
    )
    return runtime, step.event.action.returned_remnants[0]


def test_safe_certificate_never_rejects_registered_known_fit() -> None:
    runtime, remnant = _returned_remnant()
    problem = runtime.replay_input.problems[0]
    candidate = runtime.runtime_candidates[problem.problem_id].candidates[0]
    layout = prepare_layout_footprint(problem.problem, candidate, runtime.replay_input.fit_config)
    certificate = certify_translation_impossible(
        layout,
        remnant,
        material=runtime.replay_input.instances[1].material,
        fit_config=runtime.replay_input.fit_config,
    )
    search = search_layout_translation(
        remnant,
        problem.problem,
        candidate,
        material=runtime.replay_input.instances[1].material,
        fit_config=runtime.replay_input.fit_config,
        search_config=runtime.replay_input.search_config,
    )

    assert not certificate.impossible
    assert search.status is LayoutFitSearchStatus.FIT


def test_safe_width_rejection_produces_empty_registered_translation_sequence() -> None:
    runtime, remnant = _returned_remnant()
    oversized = _problem(part_width=7.0)
    candidate = _verified(oversized).candidates[0]
    short_part = oversized.problem.parts[0].model_copy(
        update={
            "shape": [
                (0.0, 0.0),
                (7.0, 0.0),
                (7.0, 5.0),
                (0.0, 5.0),
            ]
        }
    )
    width_only_problem = oversized.problem.model_copy(update={"parts": [short_part]})
    layout = prepare_layout_footprint(
        width_only_problem,
        candidate,
        runtime.replay_input.fit_config,
    )
    prepared_remnant = prepare_remnant_geometry(remnant)
    certificate = certify_translation_impossible(
        layout,
        remnant,
        material=runtime.replay_input.instances[1].material,
        fit_config=runtime.replay_input.fit_config,
    )
    translations = generate_layout_translations(
        remnant,
        candidate,
        fit_config=runtime.replay_input.fit_config,
        search_config=runtime.replay_input.search_config,
        prepared_layout=layout,
        prepared_remnant=prepared_remnant,
    )

    assert certificate.impossible
    assert certificate.reason == "footprint_width_exceeds_remnant"
    assert translations.translations == ()
