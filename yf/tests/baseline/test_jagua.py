from __future__ import annotations

import json
from pathlib import Path

from shapely import Polygon, box
from shapely.prepared import prep

from yieldforge.baseline.contracts import LayoutFitSearchConfig
from yieldforge.baseline.geometry import PreparedLayoutFootprint, PreparedRemnantGeometry
from yieldforge.baseline.jagua import (
    JaguaLayoutQueries,
    build_jagua_request,
    run_jagua_generated_prefilter,
    run_jagua_spike,
)
from yieldforge.reuse.contracts import RemnantFitConfig


def test_jagua_request_batches_layouts_and_uses_guarded_container() -> None:
    request = build_jagua_request(
        container=box(0.0, 0.0, 10.0, 10.0),
        container_guard=1.0,
        layouts=(
            JaguaLayoutQueries(
                layout_id="layout-a",
                polygons=(box(0.0, 0.0, 2.0, 2.0),),
                translations=((0.0, 0.0), (9.0, 9.0)),
            ),
            JaguaLayoutQueries(
                layout_id="layout-b",
                polygons=(box(0.0, 0.0, 1.0, 1.0),),
                translations=((1.0, 1.0),),
            ),
        ),
    )

    assert request.container_guard == 1.0
    assert request.outer == (
        (-1.0, -1.0),
        (11.0, -1.0),
        (11.0, 11.0),
        (-1.0, 11.0),
    )
    assert request.query_layout_indexes == (0, 0, 1)
    assert request.query_translations == ((0.0, 0.0), (9.0, 9.0), (1.0, 1.0))


def test_jagua_request_rejects_layout_polygons_with_holes() -> None:
    polygon_with_hole = Polygon(
        [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)],
        holes=[[(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]],
    )

    try:
        build_jagua_request(
            container=box(0.0, 0.0, 10.0, 10.0),
            container_guard=1.0,
            layouts=(
                JaguaLayoutQueries(
                    layout_id="layout-hole",
                    polygons=(polygon_with_hole,),
                    translations=((0.0, 0.0),),
                ),
            ),
        )
    except ValueError as error:
        assert "holes" in str(error)
    else:
        raise AssertionError("Jagua request accepted a layout polygon with holes")


def test_jagua_runner_validates_response_order_and_backend(tmp_path: Path) -> None:
    executable = tmp_path / "fake-jagua"
    executable.write_text(
        "#!/bin/sh\n"
        "python3 -c 'import json,sys; request=json.load(sys.stdin); "
        'print(json.dumps({"schema_version":"yieldforge.m7-jagua-spike-response.v1",'
        '"backend":"jagua-rs","backend_version":"0.7.0",'
        '"coordinate_precision":"f32","build_microseconds":3,'
        '"query_microseconds":4,"results":[{"layout_id":'
        'request["layouts"][item["layout_index"]]["layout_id"],'
        '"collides":bool(index % 2)} for index,item in enumerate(request["queries"])]}))\'\n'
    )
    executable.chmod(0o700)
    request = build_jagua_request(
        container=box(0.0, 0.0, 10.0, 10.0),
        container_guard=1.0,
        layouts=(
            JaguaLayoutQueries(
                layout_id="layout-a",
                polygons=(box(0.0, 0.0, 2.0, 2.0),),
                translations=((0.0, 0.0), (9.0, 9.0)),
            ),
        ),
    )

    response = run_jagua_spike(executable, request)

    assert response.collisions == (False, True)
    assert response.build_microseconds == 3
    assert response.query_microseconds == 4
    payload = json.loads(request.to_json_bytes())
    assert "container_guard" not in payload


def test_jagua_generated_search_returns_registered_translation_batches(tmp_path: Path) -> None:
    executable = tmp_path / "fake-jagua-search"
    executable.write_text(
        "#!/bin/sh\n"
        "python3 -c 'import json,sys; request=json.load(sys.stdin); "
        'print(json.dumps({"schema_version":"yieldforge.m7-jagua-search-response.v1",'
        '"backend":"jagua-rs","backend_version":"0.7.0","coordinate_precision":"f32",'
        '"build_microseconds":1,"generation_microseconds":2,"query_microseconds":3,'
        '"searches":[{"layout_id":request["layouts"][0]["layout_id"],'
        '"generated_candidate_count":2,"duplicate_candidate_count":0,'
        '"budget_truncated":False,"translations":[[10.0,10.0],[12.0,12.0]],'
        '"collisions":[False,True]}]}))\'\n'
    )
    executable.chmod(0o700)
    parent = box(10.0, 10.0, 14.0, 14.0)
    remnant = PreparedRemnantGeometry(
        remnant_id="yfrm-" + "a" * 24,
        geometry=parent,
        prepared=prep(parent),
        vertices=((10.0, 10.0), (10.0, 14.0), (14.0, 10.0), (14.0, 14.0)),
        bounds=parent.bounds,
    )
    footprint = box(0.0, 0.0, 2.0, 2.0)
    layout = PreparedLayoutFootprint(
        candidate_id="layout-a",
        geometry=footprint,
        part_polygons=(footprint,),
        vertices=((0.0, 0.0), (0.0, 2.0), (2.0, 0.0), (2.0, 2.0)),
        bounds=footprint.bounds,
    )

    result = run_jagua_generated_prefilter(
        executable,
        remnant=remnant,
        layouts=(layout,),
        fit_config=RemnantFitConfig(),
        search_config=LayoutFitSearchConfig(maximum_candidates=8),
        container_guard=1.0,
    )

    assert result.translation_batches[0].translations == ((10.0, 10.0), (12.0, 12.0))
    assert result.collision_masks == ((False, True),)
    assert result.generation_microseconds == 2
