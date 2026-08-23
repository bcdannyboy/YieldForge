from __future__ import annotations

from pathlib import Path

import pytest

from yieldforge.domain import ProjectionMode
from yieldforge.temporal_benchmark.catalog import (
    COMMITTED_CATALOG_PATH,
    CatalogEvidenceError,
    load_catalog,
    load_registered_catalog,
)
from yieldforge.temporal_benchmark.contracts import (
    SOURCE_CATALOG_LOGICAL_SHA256,
    SOURCE_CATALOG_SHA256,
)


def test_registered_catalog_is_exactly_bound_and_indexes_visible_capabilities() -> None:
    catalog = load_registered_catalog()

    assert catalog.artifact_sha256 == SOURCE_CATALOG_SHA256
    assert catalog.logical_sha256 == SOURCE_CATALOG_LOGICAL_SHA256
    assert len(catalog.task_ids) == 256
    assert len(catalog.runnable_task_ids) == 254
    assert catalog.blocked_task_ids == (4365, 25801)
    assert sum(len(catalog.parts_for_task(task_id)) for task_id in catalog.task_ids) == 8358
    assert len(catalog.shape_hashes) == 745
    assert max(len(group) for group in catalog.stock_groups.values()) >= 33
    assert max(len(group) for group in catalog.family_stock_groups.values()) >= 6


def test_catalog_profiles_preserve_source_task_composition_stock_and_family() -> None:
    catalog = load_registered_catalog()
    profile = catalog.profile(6669)
    task = catalog.task(6669)
    parts = catalog.parts_for_task(6669)

    assert profile.tasks_index == task.tasks_index == 6669
    assert profile.source_row_index == task.source_row_index
    assert profile.part_count == len(parts) > 0
    assert profile.unique_shape_count == len({part.shape_hash for part in parts})
    assert profile.stock_signature == (task.sheet_type, task.sheet_length, task.sheet_width)
    assert profile.family_signature == tuple(sorted({part.shape_hash for part in parts}))
    assert catalog.geometry(parts[0].shape_hash).shape_hash == parts[0].shape_hash


def test_registered_catalog_delegates_exact_source_recorded_projection() -> None:
    catalog = load_registered_catalog()
    projected = catalog.project(6669)

    assert projected.projection.mode is ProjectionMode.SOURCE_AS_RECORDED
    assert len(projected.problem.parts) == len(catalog.parts_for_task(6669))
    assert all(part.demand == 1 for part in projected.problem.parts)
    assert all(part.allowed_orientations for part in projected.problem.parts)
    assert projected.problem.sheet_length == catalog.task(6669).sheet_length
    assert projected.problem.strip_height == catalog.task(6669).sheet_width

    with pytest.raises(CatalogEvidenceError, match="not runnable"):
        catalog.project(25801)


def test_catalog_loader_rejects_any_path_outside_the_pinned_evidence_pair(tmp_path: Path) -> None:
    substitute = tmp_path / "lectra-catalog.json"
    substitute.write_text("{}")

    with pytest.raises(CatalogEvidenceError, match="pinned committed catalog path"):
        load_catalog(substitute, COMMITTED_CATALOG_PATH.parent / "catalog-manifest.json")

