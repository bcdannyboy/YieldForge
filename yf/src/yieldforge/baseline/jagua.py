"""Pinned Jagua collision prefilter boundary for the M7 baseline."""

from __future__ import annotations

import json
import math
import os
import stat
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from shapely import Polygon

from yieldforge.baseline.contracts import LayoutFitSearchConfig
from yieldforge.baseline.geometry import (
    LayoutTranslationCandidates,
    PreparedLayoutFootprint,
    PreparedRemnantGeometry,
)
from yieldforge.reuse.contracts import RemnantFitConfig

Point = tuple[float, float]
Ring = tuple[Point, ...]


def _float_bits(value: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", value))[0]


@dataclass(frozen=True)
class JaguaLayoutQueries:
    """One complete layout and its registered translation candidates."""

    layout_id: str
    polygons: tuple[Polygon, ...]
    translations: tuple[Point, ...]


@dataclass(frozen=True)
class JaguaSpikeRequest:
    """Validated flattened request for the pinned Rust extension."""

    container_guard: float
    outer: Ring
    holes: tuple[Ring, ...]
    layout_ids: tuple[str, ...]
    layout_polygons: tuple[tuple[Ring, ...], ...]
    query_layout_indexes: tuple[int, ...]
    query_translations: tuple[Point, ...]

    def to_json_bytes(self) -> bytes:
        payload = {
            "schema_version": "yieldforge.m7-jagua-spike-request.v1",
            "outer": self.outer,
            "holes": self.holes,
            "layouts": [
                {"layout_id": layout_id, "polygons": polygons}
                for layout_id, polygons in zip(
                    self.layout_ids,
                    self.layout_polygons,
                    strict=True,
                )
            ],
            "queries": [
                {"layout_index": layout_index, "translation": translation}
                for layout_index, translation in zip(
                    self.query_layout_indexes,
                    self.query_translations,
                    strict=True,
                )
            ],
        }
        return json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()


@dataclass(frozen=True)
class JaguaSpikeResponse:
    backend: str
    backend_version: str
    coordinate_precision: str
    build_microseconds: int
    query_microseconds: int
    collisions: tuple[bool, ...]


@dataclass(frozen=True)
class JaguaPrefilterResult:
    """Collision masks in input layout order plus non-semantic runtime observations."""

    collision_masks: tuple[tuple[bool, ...], ...]
    guarded_query_count: int
    jagua_rejection_count: int
    build_microseconds: int
    query_microseconds: int
    wall_seconds: float


@dataclass(frozen=True)
class JaguaSearchRequest:
    """Guarded Jagua request that also generates the frozen M7 translations."""

    container_guard: float
    outer: Ring
    holes: tuple[Ring, ...]
    parent_vertices: tuple[Point, ...]
    parent_bounds: tuple[float, float, float, float]
    layout_ids: tuple[str, ...]
    layout_polygons: tuple[tuple[Ring, ...], ...]
    layout_vertices: tuple[tuple[Point, ...], ...]
    layout_bounds: tuple[tuple[float, float, float, float], ...]
    fit_config: RemnantFitConfig
    search_config: LayoutFitSearchConfig

    def to_json_bytes(self) -> bytes:
        payload = {
            "schema_version": "yieldforge.m7-jagua-search-request.v1",
            "outer": self.outer,
            "holes": self.holes,
            "parent_vertex_bits": tuple(
                tuple(_float_bits(value) for value in point) for point in self.parent_vertices
            ),
            "parent_bounds_bits": tuple(_float_bits(value) for value in self.parent_bounds),
            "layouts": [
                {
                    "layout_id": layout_id,
                    "polygons": polygons,
                    "vertex_bits": tuple(
                        tuple(_float_bits(value) for value in point) for point in vertices
                    ),
                    "bounds_bits": tuple(_float_bits(value) for value in bounds),
                }
                for layout_id, polygons, vertices, bounds in zip(
                    self.layout_ids,
                    self.layout_polygons,
                    self.layout_vertices,
                    self.layout_bounds,
                    strict=True,
                )
            ],
            "search_config": {
                "grid_columns": self.search_config.grid_columns,
                "grid_rows": self.search_config.grid_rows,
                "maximum_candidates": self.search_config.maximum_candidates,
                "coordinate_tolerance": self.fit_config.coordinate_tolerance,
                "candidate_source_order": self.search_config.candidate_source_order,
            },
        }
        return json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()


@dataclass(frozen=True)
class JaguaGeneratedPrefilterResult:
    translation_batches: tuple[LayoutTranslationCandidates, ...]
    collision_masks: tuple[tuple[bool, ...], ...]
    guarded_query_count: int
    jagua_rejection_count: int
    build_microseconds: int
    generation_microseconds: int
    query_microseconds: int
    wall_seconds: float


def _canonical_ring(coordinates) -> Ring:  # type: ignore[no-untyped-def]
    points = tuple((float(x), float(y)) for x, y in tuple(coordinates)[:-1])
    if len(points) < 3 or any(not math.isfinite(value) for point in points for value in point):
        raise ValueError("Jagua polygon ring is invalid")
    signed_area = sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, points[1:] + points[:1], strict=True)
    )
    if signed_area < 0:
        points = tuple(reversed(points))
    minimum = min(range(len(points)), key=lambda index: points[index])
    return points[minimum:] + points[:minimum]


def build_jagua_request(
    *,
    container: Polygon,
    container_guard: float,
    layouts: tuple[JaguaLayoutQueries, ...],
) -> JaguaSpikeRequest:
    """Build one guarded, batched Jagua request without changing final fit semantics."""

    if not math.isfinite(container_guard) or container_guard <= 0.0:
        raise ValueError("Jagua container guard must be positive and finite")
    if not layouts or len({item.layout_id for item in layouts}) != len(layouts):
        raise ValueError("Jagua request requires unique non-empty layouts")
    guarded = container.buffer(container_guard, join_style="mitre")
    if not isinstance(guarded, Polygon) or guarded.is_empty or not guarded.is_valid:
        raise ValueError("Jagua guarded container must remain one valid polygon")
    layout_polygons = []
    query_indexes = []
    query_translations = []
    for layout_index, layout in enumerate(layouts):
        if not layout.layout_id or not layout.polygons or not layout.translations:
            raise ValueError("Jagua layout query cannot be empty")
        rings = []
        for polygon in layout.polygons:
            if polygon.interiors:
                raise ValueError("Jagua layout polygons with holes require Shapely fallback")
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError("Jagua layout polygon is invalid")
            rings.append(_canonical_ring(polygon.exterior.coords))
        layout_polygons.append(tuple(rings))
        for translation in layout.translations:
            if any(not math.isfinite(value) for value in translation):
                raise ValueError("Jagua translation must be finite")
            query_indexes.append(layout_index)
            query_translations.append(tuple(float(value) for value in translation))
    return JaguaSpikeRequest(
        container_guard=float(container_guard),
        outer=_canonical_ring(guarded.exterior.coords),
        holes=tuple(_canonical_ring(ring.coords) for ring in guarded.interiors),
        layout_ids=tuple(item.layout_id for item in layouts),
        layout_polygons=tuple(layout_polygons),
        query_layout_indexes=tuple(query_indexes),
        query_translations=tuple(query_translations),
    )


def build_jagua_search_request(
    *,
    remnant: PreparedRemnantGeometry,
    container_guard: float,
    layouts: tuple[PreparedLayoutFootprint, ...],
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
) -> JaguaSearchRequest:
    """Build one request that preserves M7's exact f64 translation-source order."""

    if not isinstance(remnant.geometry, Polygon):
        raise ValueError("Jagua search requires one polygonal remnant")
    guarded = build_jagua_request(
        container=remnant.geometry,
        container_guard=container_guard,
        layouts=tuple(
            JaguaLayoutQueries(
                layout_id=layout.candidate_id,
                polygons=layout.part_polygons,
                translations=((0.0, 0.0),),
            )
            for layout in layouts
        ),
    )
    return JaguaSearchRequest(
        container_guard=container_guard,
        outer=guarded.outer,
        holes=guarded.holes,
        parent_vertices=remnant.vertices,
        parent_bounds=remnant.bounds,
        layout_ids=guarded.layout_ids,
        layout_polygons=guarded.layout_polygons,
        layout_vertices=tuple(layout.vertices for layout in layouts),
        layout_bounds=tuple(layout.bounds for layout in layouts),
        fit_config=fit_config,
        search_config=search_config,
    )


def _regular_executable(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Jagua extension must be a regular file")
    if not os.access(path, os.X_OK):
        raise ValueError("Jagua extension is not executable")


def run_jagua_spike(
    executable: Path,
    request: JaguaSpikeRequest,
    *,
    timeout_seconds: float = 120.0,
) -> JaguaSpikeResponse:
    """Execute the pinned extension and fail closed on any response drift."""

    path = Path(executable).resolve(strict=True)
    _regular_executable(path)
    completed = subprocess.run(
        [str(path)],
        input=request.to_json_bytes(),
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Jagua extension failed: {error}")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Jagua extension returned invalid JSON") from error
    expected_fields = {
        "schema_version",
        "backend",
        "backend_version",
        "coordinate_precision",
        "build_microseconds",
        "query_microseconds",
        "results",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("Jagua extension response fields differ from the frozen schema")
    if (
        payload["schema_version"] != "yieldforge.m7-jagua-spike-response.v1"
        or payload["backend"] != "jagua-rs"
        or payload["backend_version"] != "0.7.0"
        or payload["coordinate_precision"] != "f32"
    ):
        raise ValueError("Jagua extension runtime identity differs from the pinned backend")
    results = payload["results"]
    if not isinstance(results, list) or len(results) != len(request.query_translations):
        raise ValueError("Jagua extension result count differs from the request")
    expected_ids = tuple(request.layout_ids[index] for index in request.query_layout_indexes)
    collisions = []
    for result, expected_id in zip(results, expected_ids, strict=True):
        if (
            not isinstance(result, dict)
            or set(result) != {"layout_id", "collides"}
            or result["layout_id"] != expected_id
            or not isinstance(result["collides"], bool)
        ):
            raise ValueError("Jagua extension results differ from request order")
        collisions.append(result["collides"])
    build = payload["build_microseconds"]
    query = payload["query_microseconds"]
    if (
        isinstance(build, bool)
        or isinstance(query, bool)
        or not isinstance(build, int)
        or not isinstance(query, int)
        or build < 0
        or query < 0
    ):
        raise ValueError("Jagua extension timings are invalid")
    return JaguaSpikeResponse(
        backend=payload["backend"],
        backend_version=payload["backend_version"],
        coordinate_precision=payload["coordinate_precision"],
        build_microseconds=build,
        query_microseconds=query,
        collisions=tuple(collisions),
    )


def run_jagua_prefilter(
    executable: Path,
    *,
    remnant: PreparedRemnantGeometry,
    layouts: tuple[PreparedLayoutFootprint, ...],
    translations: tuple[LayoutTranslationCandidates, ...],
    container_guard: float,
) -> JaguaPrefilterResult:
    """Batch compatible layouts; leave unsupported shapes entirely to Shapely."""

    if len(layouts) != len(translations):
        raise ValueError("Jagua layouts and translation batches differ in length")
    masks: list[tuple[bool, ...] | None] = [None] * len(layouts)
    compatible_indexes = []
    compatible = []
    for index, (layout, candidates) in enumerate(zip(layouts, translations, strict=True)):
        if (
            layout.candidate_id != candidates.candidate_id
            or remnant.remnant_id != candidates.remnant_id
        ):
            raise ValueError("Jagua prefilter inputs do not share candidate/remnant identity")
        has_holes = any(polygon.interiors for polygon in layout.part_polygons)
        if not candidates.translations or has_holes:
            masks[index] = (False,) * len(candidates.translations)
            continue
        compatible_indexes.append(index)
        compatible.append(
            JaguaLayoutQueries(
                layout_id=layout.candidate_id,
                polygons=layout.part_polygons,
                translations=candidates.translations,
            )
        )
    if not compatible:
        return JaguaPrefilterResult(
            collision_masks=tuple(item or () for item in masks),
            guarded_query_count=0,
            jagua_rejection_count=0,
            build_microseconds=0,
            query_microseconds=0,
            wall_seconds=0.0,
        )
    if not isinstance(remnant.geometry, Polygon):
        raise ValueError("Jagua prefilter requires one polygonal remnant")
    request = build_jagua_request(
        container=remnant.geometry,
        container_guard=container_guard,
        layouts=tuple(compatible),
    )
    started = perf_counter()
    response = run_jagua_spike(executable, request)
    wall = perf_counter() - started
    offset = 0
    for target_index, layout in zip(compatible_indexes, compatible, strict=True):
        next_offset = offset + len(layout.translations)
        masks[target_index] = response.collisions[offset:next_offset]
        offset = next_offset
    if offset != len(response.collisions) or any(item is None for item in masks):
        raise ValueError("Jagua prefilter failed to reconstruct collision masks")
    complete_masks = tuple(item for item in masks if item is not None)
    return JaguaPrefilterResult(
        collision_masks=complete_masks,
        guarded_query_count=len(response.collisions),
        jagua_rejection_count=sum(sum(mask) for mask in complete_masks),
        build_microseconds=response.build_microseconds,
        query_microseconds=response.query_microseconds,
        wall_seconds=wall,
    )


def run_jagua_generated_prefilter(
    executable: Path,
    *,
    remnant: PreparedRemnantGeometry,
    layouts: tuple[PreparedLayoutFootprint, ...],
    fit_config: RemnantFitConfig,
    search_config: LayoutFitSearchConfig,
    container_guard: float,
    timeout_seconds: float = 120.0,
) -> JaguaGeneratedPrefilterResult:
    """Generate registered translations and classify them in one Rust invocation."""

    if not layouts or any(
        polygon.interiors for layout in layouts for polygon in layout.part_polygons
    ):
        raise ValueError("Jagua generated search requires compatible non-empty layouts")
    request = build_jagua_search_request(
        remnant=remnant,
        container_guard=container_guard,
        layouts=layouts,
        fit_config=fit_config,
        search_config=search_config,
    )
    path = Path(executable).resolve(strict=True)
    _regular_executable(path)
    started = perf_counter()
    completed = subprocess.run(
        [str(path)],
        input=request.to_json_bytes(),
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    wall = perf_counter() - started
    if completed.returncode != 0:
        error = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Jagua generated search failed: {error}")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Jagua generated search returned invalid JSON") from error
    expected_fields = {
        "schema_version",
        "backend",
        "backend_version",
        "coordinate_precision",
        "build_microseconds",
        "generation_microseconds",
        "query_microseconds",
        "searches",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("Jagua generated search fields differ from the frozen schema")
    if (
        payload["schema_version"] != "yieldforge.m7-jagua-search-response.v1"
        or payload["backend"] != "jagua-rs"
        or payload["backend_version"] != "0.7.0"
        or payload["coordinate_precision"] != "f32"
    ):
        raise ValueError("Jagua generated search runtime identity differs")
    searches = payload["searches"]
    if not isinstance(searches, list) or len(searches) != len(layouts):
        raise ValueError("Jagua generated search count differs from layouts")
    translation_batches = []
    collision_masks = []
    for search, layout in zip(searches, layouts, strict=True):
        expected_search_fields = {
            "layout_id",
            "generated_candidate_count",
            "duplicate_candidate_count",
            "budget_truncated",
            "translations",
            "collisions",
        }
        if not isinstance(search, dict) or set(search) != expected_search_fields:
            raise ValueError("Jagua generated layout result fields differ")
        translations = tuple((float(point[0]), float(point[1])) for point in search["translations"])
        collisions = tuple(search["collisions"])
        if (
            search["layout_id"] != layout.candidate_id
            or len(translations) != len(collisions)
            or len(translations) > search_config.maximum_candidates
            or any(
                len(point) != 2 or any(not math.isfinite(value) for value in point)
                for point in search["translations"]
            )
            or any(not isinstance(value, bool) for value in collisions)
            or isinstance(search["generated_candidate_count"], bool)
            or not isinstance(search["generated_candidate_count"], int)
            or search["generated_candidate_count"] < len(translations)
            or isinstance(search["duplicate_candidate_count"], bool)
            or not isinstance(search["duplicate_candidate_count"], int)
            or search["duplicate_candidate_count"] < 0
            or not isinstance(search["budget_truncated"], bool)
        ):
            raise ValueError("Jagua generated layout result is invalid")
        translation_batches.append(
            LayoutTranslationCandidates(
                candidate_id=layout.candidate_id,
                remnant_id=remnant.remnant_id,
                translations=translations,
                generated_candidate_count=search["generated_candidate_count"],
                duplicate_candidate_count=search["duplicate_candidate_count"],
                budget_truncated=search["budget_truncated"],
            )
        )
        collision_masks.append(collisions)
    timings = tuple(
        payload[field]
        for field in ("build_microseconds", "generation_microseconds", "query_microseconds")
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in timings):
        raise ValueError("Jagua generated search timings are invalid")
    return JaguaGeneratedPrefilterResult(
        translation_batches=tuple(translation_batches),
        collision_masks=tuple(collision_masks),
        guarded_query_count=sum(len(item) for item in collision_masks),
        jagua_rejection_count=sum(sum(item) for item in collision_masks),
        build_microseconds=timings[0],
        generation_microseconds=timings[1],
        query_microseconds=timings[2],
        wall_seconds=wall,
    )


__all__ = [
    "JaguaLayoutQueries",
    "JaguaGeneratedPrefilterResult",
    "JaguaSpikeRequest",
    "JaguaSpikeResponse",
    "JaguaSearchRequest",
    "JaguaPrefilterResult",
    "build_jagua_request",
    "build_jagua_search_request",
    "run_jagua_generated_prefilter",
    "run_jagua_prefilter",
    "run_jagua_spike",
]
