"""Fail-closed indexing of the pinned full Lectra catalog for M6."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from yieldforge.datasets.normalized_slice import (
    DerivedShapeGeometry,
    NormalizedSlice,
    PartSourceRow,
    SupportStatus,
    TaskSourceRow,
)
from yieldforge.datasets.passive_report import (
    PassiveEvidenceError,
    decode_strict_json_bytes,
    parse_normalized_slice,
    read_passive_evidence_file,
)
from yieldforge.datasets.projection import ProjectionError, project_task
from yieldforge.domain import ProjectedTask, ProjectionMode
from yieldforge.temporal_benchmark.contracts import (
    SOURCE_CATALOG_LOGICAL_SHA256,
    SOURCE_CATALOG_MANIFEST_SHA256,
    SOURCE_CATALOG_SHA256,
)

_MAX_CATALOG_BYTES = 64 * 1024 * 1024
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_CATALOG_PATH = (
    _PACKAGE_ROOT / "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"
)
COMMITTED_CATALOG_MANIFEST_PATH = COMMITTED_CATALOG_PATH.parent / "catalog-manifest.json"

StockSignature = tuple[int, float, float]
FamilySignature = tuple[int, ...]
FamilyStockSignature = tuple[FamilySignature, StockSignature]


class CatalogEvidenceError(ValueError):
    """The M6 catalog or one requested source task failed its evidence boundary."""


@dataclass(frozen=True)
class CatalogTaskProfile:
    """Stable generator-facing facts for one source task."""

    tasks_index: int
    source_row_index: int
    part_count: int
    unique_shape_count: int
    stock_signature: StockSignature
    family_signature: FamilySignature


@dataclass(frozen=True)
class CatalogSnapshot:
    """Read-only indexes over a fully validated normalized catalog."""

    artifact_sha256: str
    logical_sha256: str
    manifest_sha256: str
    task_ids: tuple[int, ...]
    runnable_task_ids: tuple[int, ...]
    blocked_task_ids: tuple[int, ...]
    shape_hashes: tuple[int, ...]
    stock_groups: Mapping[StockSignature, tuple[int, ...]]
    family_stock_groups: Mapping[FamilyStockSignature, tuple[int, ...]]
    _normalized: NormalizedSlice
    _tasks: Mapping[int, TaskSourceRow]
    _parts: Mapping[int, tuple[PartSourceRow, ...]]
    _geometry: Mapping[int, DerivedShapeGeometry]

    def task(self, tasks_index: int) -> TaskSourceRow:
        try:
            return self._tasks[tasks_index]
        except KeyError as error:
            raise CatalogEvidenceError(f"unknown source task {tasks_index}") from error

    def parts_for_task(self, tasks_index: int) -> tuple[PartSourceRow, ...]:
        try:
            return self._parts[tasks_index]
        except KeyError as error:
            raise CatalogEvidenceError(f"unknown source task {tasks_index}") from error

    def geometry(self, shape_hash: int) -> DerivedShapeGeometry:
        try:
            return self._geometry[shape_hash]
        except KeyError as error:
            raise CatalogEvidenceError(f"unknown source shape {shape_hash}") from error

    def profile(self, tasks_index: int) -> CatalogTaskProfile:
        task = self.task(tasks_index)
        parts = self.parts_for_task(tasks_index)
        shapes = tuple(sorted({part.shape_hash for part in parts}))
        return CatalogTaskProfile(
            tasks_index=tasks_index,
            source_row_index=task.source_row_index,
            part_count=len(parts),
            unique_shape_count=len(shapes),
            stock_signature=(task.sheet_type, task.sheet_length, task.sheet_width),
            family_signature=shapes,
        )

    def project(self, tasks_index: int) -> ProjectedTask:
        if tasks_index not in set(self.runnable_task_ids):
            if tasks_index in set(self.blocked_task_ids):
                raise CatalogEvidenceError(f"source task {tasks_index} is not runnable")
            raise CatalogEvidenceError(f"unknown source task {tasks_index}")
        try:
            return project_task(
                self._normalized,
                tasks_index,
                mode=ProjectionMode.SOURCE_AS_RECORDED,
            )
        except ProjectionError as error:
            raise CatalogEvidenceError(
                f"source-recorded projection failed for task {tasks_index}"
            ) from error


def _semantic_sha256(normalized: NormalizedSlice) -> str:
    encoded = json.dumps(
        normalized.model_dump(mode="json"),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_pinned_paths(catalog_path: Path, manifest_path: Path) -> None:
    try:
        actual_catalog = catalog_path.resolve(strict=True)
        actual_manifest = manifest_path.resolve(strict=True)
        expected_catalog = COMMITTED_CATALOG_PATH.resolve(strict=True)
        expected_manifest = COMMITTED_CATALOG_MANIFEST_PATH.resolve(strict=True)
    except OSError as error:
        raise CatalogEvidenceError("M6 catalog evidence path could not be resolved") from error
    if actual_catalog != expected_catalog or actual_manifest != expected_manifest:
        raise CatalogEvidenceError("M6 requires the pinned committed catalog path and manifest")


def load_catalog(catalog_path: Path, manifest_path: Path) -> CatalogSnapshot:
    """Read, bind, and index exactly the committed M6 catalog evidence pair."""

    catalog_path = Path(catalog_path)
    manifest_path = Path(manifest_path)
    _require_pinned_paths(catalog_path, manifest_path)
    try:
        payload = read_passive_evidence_file(
            catalog_path,
            label="M6 Lectra catalog",
            max_bytes=_MAX_CATALOG_BYTES,
        )
        manifest_payload = read_passive_evidence_file(
            manifest_path,
            label="M6 Lectra catalog manifest",
        )
        manifest = decode_strict_json_bytes(manifest_payload, label="M6 catalog manifest")
        normalized = parse_normalized_slice(payload, max_bytes=_MAX_CATALOG_BYTES)
    except PassiveEvidenceError as error:
        raise CatalogEvidenceError("M6 catalog evidence could not be validated") from error
    if not isinstance(manifest, dict):
        raise CatalogEvidenceError("M6 catalog manifest root must be an object")

    artifact_sha256 = hashlib.sha256(payload).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    logical_sha256 = _semantic_sha256(normalized)
    if not hmac.compare_digest(artifact_sha256, SOURCE_CATALOG_SHA256):
        raise CatalogEvidenceError("M6 catalog artifact identity mismatch")
    if not hmac.compare_digest(manifest_sha256, SOURCE_CATALOG_MANIFEST_SHA256):
        raise CatalogEvidenceError("M6 catalog manifest identity mismatch")
    if not hmac.compare_digest(logical_sha256, SOURCE_CATALOG_LOGICAL_SHA256):
        raise CatalogEvidenceError("M6 catalog logical identity mismatch")

    artifact = manifest.get("artifact")
    counts = manifest.get("counts")
    capability = manifest.get("capability_distribution")
    if not isinstance(artifact, dict) or (
        artifact.get("name") != catalog_path.name
        or artifact.get("sha256") != artifact_sha256
        or artifact.get("size_bytes") != len(payload)
    ):
        raise CatalogEvidenceError("M6 catalog manifest does not bind the artifact")
    observed_counts = {
        "tasks": len(normalized.tasks),
        "parts": len(normalized.parts),
        "shapes": len(normalized.shapes),
        "derived_geometry": len(normalized.derived_geometry),
        "constraints": len(normalized.constraints),
    }
    if counts != observed_counts:
        raise CatalogEvidenceError("M6 catalog manifest row counts do not match")

    tasks = {task.tasks_index: task for task in normalized.tasks}
    parts: dict[int, list[PartSourceRow]] = defaultdict(list)
    for part in normalized.parts:
        parts[part.tasks_index].append(part)
    dispositions = {item.tasks_index: item for item in normalized.task_dispositions}
    runnable = tuple(
        sorted(
            task_id
            for task_id, disposition in dispositions.items()
            if disposition.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
        )
    )
    blocked = tuple(sorted(set(tasks) - set(runnable)))
    observed_capability = {
        "runnable_with_explicit_assumptions": len(runnable),
        "view_only": len(blocked),
    }
    if capability != observed_capability or len(tasks) != 256 or len(runnable) != 254:
        raise CatalogEvidenceError("M6 catalog capability census does not match")
    if blocked != (4365, 25801):
        raise CatalogEvidenceError("M6 catalog blocked-task census does not match")

    stock_groups: dict[StockSignature, list[int]] = defaultdict(list)
    family_stock_groups: dict[FamilyStockSignature, list[int]] = defaultdict(list)
    for task_id in runnable:
        task = tasks[task_id]
        task_parts = parts[task_id]
        stock = (task.sheet_type, task.sheet_length, task.sheet_width)
        family = tuple(sorted({part.shape_hash for part in task_parts}))
        stock_groups[stock].append(task_id)
        family_stock_groups[(family, stock)].append(task_id)

    return CatalogSnapshot(
        artifact_sha256=artifact_sha256,
        logical_sha256=logical_sha256,
        manifest_sha256=manifest_sha256,
        task_ids=tuple(sorted(tasks)),
        runnable_task_ids=runnable,
        blocked_task_ids=blocked,
        shape_hashes=tuple(shape.shape_hash for shape in normalized.shapes),
        stock_groups=MappingProxyType(
            {key: tuple(sorted(values)) for key, values in stock_groups.items()}
        ),
        family_stock_groups=MappingProxyType(
            {key: tuple(sorted(values)) for key, values in family_stock_groups.items()}
        ),
        _normalized=normalized,
        _tasks=MappingProxyType(tasks),
        _parts=MappingProxyType(
            {
                key: tuple(sorted(values, key=lambda part: part.source_row_index))
                for key, values in parts.items()
            }
        ),
        _geometry=MappingProxyType(
            {geometry.shape_hash: geometry for geometry in normalized.derived_geometry}
        ),
    )


@lru_cache(maxsize=1)
def load_registered_catalog() -> CatalogSnapshot:
    """Return the process-cached registered M6 catalog snapshot."""

    return load_catalog(COMMITTED_CATALOG_PATH, COMMITTED_CATALOG_MANIFEST_PATH)
