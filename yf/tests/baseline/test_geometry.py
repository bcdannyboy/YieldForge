from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest
from shapely import box

from tests.baseline.test_replay import _problem, _two_event_runtime, _verified
from tests.oracle.fixtures import inventory_item
from yieldforge.baseline import geometry as geometry_module
from yieldforge.baseline.contracts import LayoutFitSearchStatus
from yieldforge.baseline.geometry import (
    PreparedLayoutFootprint,
    TranslationRejectionCertificate,
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
from yieldforge.reuse.contracts import (
    MaterialIdentity,
    RemnantFitConfig,
    RemnantStock,
    polygon_from_record,
)
from yieldforge.reuse.geometry import material_key


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


def _pre_2eb28fe_translation_rejection(
    layout: PreparedLayoutFootprint,
    remnant: RemnantStock,
    *,
    material: MaterialIdentity,
    fit_config: RemnantFitConfig,
) -> TranslationRejectionCertificate:
    """Reconstruct the pre-scalar certificate formula without prepared helpers."""

    parent = polygon_from_record(remnant.geometry)
    foot_min_x, foot_min_y, foot_max_x, foot_max_y = layout.bounds
    rem_min_x, rem_min_y, rem_max_x, rem_max_y = parent.bounds
    layout_width = foot_max_x - foot_min_x
    layout_height = foot_max_y - foot_min_y
    remnant_width = rem_max_x - rem_min_x
    remnant_height = rem_max_y - rem_min_y
    area_tolerance = max(
        fit_config.coordinate_tolerance,
        parent.area * fit_config.relative_area_tolerance,
    )
    reason = None
    if material_key(material) != material_key(remnant.material):
        reason = "material_mismatch"
    elif layout.geometry.area > parent.area + area_tolerance:
        reason = "footprint_area_exceeds_remnant"
    elif layout_width > remnant_width + fit_config.coordinate_tolerance:
        reason = "footprint_width_exceeds_remnant"
    elif layout_height > remnant_height + fit_config.coordinate_tolerance:
        reason = "footprint_height_exceeds_remnant"
    return TranslationRejectionCertificate(
        impossible=reason is not None,
        reason=reason,
        layout_area=float(layout.geometry.area),
        remnant_area=float(parent.area),
        layout_width=float(layout_width),
        remnant_width=float(remnant_width),
        layout_height=float(layout_height),
        remnant_height=float(remnant_height),
        area_tolerance=float(area_tolerance),
    )


def _certificate_json(certificate: TranslationRejectionCertificate) -> str:
    return json.dumps(asdict(certificate), sort_keys=True, separators=(",", ":"))


def test_material_reconciliation_delta_matches_contract_summation_order() -> None:
    categories = (3079.4000000000005, 0.0, 1048.3, 215.10000000000008)
    expected = abs(
        4342.8
        - (((categories[0] + categories[1]) + categories[2]) + categories[3])
    )

    assert geometry_module._material_reconciliation_delta(4342.8, categories) == expected
    assert expected == 9.094947017729282e-13


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


@pytest.mark.parametrize(
    ("case", "layout_bounds", "material_mismatch", "expected_reason"),
    [
        ("material", (0.0, 0.0, 4.0, 4.0), True, "material_mismatch"),
        (
            "area",
            (0.0, 0.0, 11.0, 10.0),
            False,
            "footprint_area_exceeds_remnant",
        ),
        (
            "width",
            (0.0, 0.0, 11.0, 5.0),
            False,
            "footprint_width_exceeds_remnant",
        ),
        (
            "height",
            (0.0, 0.0, 5.0, 11.0),
            False,
            "footprint_height_exceeds_remnant",
        ),
        ("pass", (0.0, 0.0, 5.0, 5.0), False, None),
    ],
)
def test_legacy_and_prepared_rejections_match_pre_optimization_formula(
    case: str,
    layout_bounds: tuple[float, float, float, float],
    material_mismatch: bool,
    expected_reason: str | None,
) -> None:
    runtime = _two_event_runtime()
    source_material = runtime.replay_input.instances[0].material
    item = inventory_item(
        box(0.0, 0.0, 10.0, 10.0),
        material=source_material,
        token=f"prepared-rejection-{case}",
    )
    footprint = box(*layout_bounds)
    layout = PreparedLayoutFootprint(
        candidate_id=f"prepared-rejection-{case}",
        geometry=footprint,
        part_polygons=(footprint,),
        vertices=(),
        bounds=tuple(float(value) for value in footprint.bounds),
    )
    material = source_material
    if material_mismatch:
        material = MaterialIdentity(
            **(
                source_material.model_dump(mode="python")
                | {"grade": "prepared-rejection-mismatch"}
            )
        )

    expected = _pre_2eb28fe_translation_rejection(
        layout,
        item.remnant,
        material=material,
        fit_config=runtime.replay_input.fit_config,
    )
    legacy = certify_translation_impossible(
        layout,
        item.remnant,
        material=material,
        fit_config=runtime.replay_input.fit_config,
    )
    prepared = geometry_module.certify_prepared_translation_impossible(
        geometry_module.prepare_translation_rejection_layout(layout),
        geometry_module.prepare_translation_rejection_remnant(item.remnant),
        material=material,
        fit_config=runtime.replay_input.fit_config,
    )

    assert expected.reason == expected_reason
    assert legacy == expected
    assert prepared == expected
    assert _certificate_json(legacy) == _certificate_json(expected)
    assert _certificate_json(prepared) == _certificate_json(expected)
    for field_name in (
        "area_tolerance",
        "layout_area",
        "remnant_width",
        "remnant_height",
    ):
        drifted = replace(
            expected,
            **{field_name: getattr(expected, field_name) + 1.0},
        )
        assert legacy != drifted
        assert prepared != drifted
        assert _certificate_json(legacy) != _certificate_json(drifted)
        assert _certificate_json(prepared) != _certificate_json(drifted)
