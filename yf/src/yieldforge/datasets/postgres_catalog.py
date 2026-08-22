"""Trusted import of one evidence-bound catalog into a PostgreSQL read model.

The importer consumes passive JSON only. It never opens the source gzip-pickle
files and never treats PostgreSQL as the evidence authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import Field, StrictInt, StrictStr, ValidationError

from yieldforge.datasets.corpus import (
    CoordinateUnitDto,
    CorpusQueryService,
    CorpusSourceDto,
    CorpusSummaryDto,
    TaskDetailDto,
    TaskSummaryDto,
)
from yieldforge.datasets.normalized_slice import (
    LECTRA_DATASET_ID,
    NormalizedSlice,
    ProjectionStatus,
    StrictContractModel,
)
from yieldforge.datasets.passive_report import (
    PassiveEvidenceError,
    bind_normalized_slice_evidence,
    decode_strict_json_bytes,
    parse_dataset_source_manifest,
    parse_lectra_audit_report,
    parse_normalized_slice,
    read_passive_evidence_file,
)
from yieldforge.datasets.projection import project_task
from yieldforge.domain import StripPackingProblem

MAX_CATALOG_BYTES = 64 * 1024 * 1024
READ_MODEL_SCHEMA_VERSION = "yieldforge.postgres-catalog.v1"
COMMITTED_CATALOG_MANIFEST_SHA256 = (
    "b6e915adcc51b2ee683eeebbbb5ce68a55fa306e2b3ddfd472a6a64f28829cc7"
)
COMMITTED_CATALOG_SHA256 = "4903e28be9b874460ab565b3fc17b06608a9ccce37b699d6bcda49c7eac03138"
COMMITTED_CATALOG_LOGICAL_SHA256 = (
    "c01669e5ef3b6bb879f16afea2fcc82594c8dde883d638ca1203e3dbee157778"
)
COMMITTED_READ_MODEL_ROOT_SHA256 = (
    "feff8c47e4ef31629b5d846fd3cec31b7ef6809a56fbd1629ab571442eea85ae"
)
READ_MODEL_ROOT_SCHEMA_VERSION = "yieldforge.postgres-read-model-root.v1"
_EXPECTED_TASK_COUNT = 256
_ADVISORY_LOCK_KEY = 5_947_313_481_882_363_281


class CatalogImportError(ValueError):
    """The catalog or PostgreSQL read model failed closed validation."""


Sha256 = StrictStr


class _CatalogArtifact(StrictContractModel):
    name: Literal["lectra-catalog.json"]
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: StrictInt = Field(gt=0, le=MAX_CATALOG_BYTES)


class _CatalogEvidence(StrictContractModel):
    source_manifest_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    audit_report_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    conversion_ruleset_version: Literal["lectra-catalog-rules.v1"]


class _CatalogCounts(StrictContractModel):
    tasks: StrictInt = Field(gt=0)
    parts: StrictInt = Field(gt=0)
    shapes: StrictInt = Field(gt=0)
    derived_geometry: StrictInt = Field(gt=0)
    constraints: StrictInt = Field(ge=0)


class _CapabilityDistribution(StrictContractModel):
    runnable_with_explicit_assumptions: StrictInt = Field(ge=0)
    view_only: StrictInt = Field(ge=0)


class _CatalogManifest(StrictContractModel):
    schema_version: Literal["yieldforge.catalog-manifest.v1"]
    dataset_id: Literal["lectra-7030786-v1.1"]
    artifact: _CatalogArtifact
    evidence: _CatalogEvidence
    counts: _CatalogCounts
    capability_distribution: _CapabilityDistribution


class CatalogImportResult(StrictContractModel):
    """Stable identity returned for a newly imported or revalidated catalog."""

    schema_version: Literal["yieldforge.postgres-catalog.v1"] = READ_MODEL_SCHEMA_VERSION
    catalog_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_logical_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_manifest_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    task_count: StrictInt = Field(gt=0)
    projected_task_count: StrictInt = Field(ge=0)


@dataclass(frozen=True)
class _TaskRecord:
    catalog_ordinal: int
    tasks_index: int
    source_row_index: int
    sheet_type: int
    is_train: bool
    is_val: bool
    is_test: bool
    normalization_status: str
    support_status: str
    projection_status: str
    part_count: int
    shape_count: int
    constraint_count: int
    constraint_types: tuple[str, ...]
    summary: dict[str, object]
    detail: dict[str, object]
    problem: dict[str, object] | None
    record_sha256: str


def _task_record_root_mapping(record: _TaskRecord) -> dict[str, object]:
    return {
        "catalog_ordinal": record.catalog_ordinal,
        "constraint_count": record.constraint_count,
        "constraint_types": record.constraint_types,
        "detail_json": record.detail,
        "is_test": record.is_test,
        "is_train": record.is_train,
        "is_val": record.is_val,
        "normalization_status": record.normalization_status,
        "part_count": record.part_count,
        "projection_status": record.projection_status,
        "record_sha256": record.record_sha256,
        "shape_count": record.shape_count,
        "sheet_type": record.sheet_type,
        "solver_problem_json": record.problem,
        "source_row_index": record.source_row_index,
        "summary_json": record.summary,
        "support_status": record.support_status,
        "tasks_index": record.tasks_index,
    }


@dataclass(frozen=True)
class _PreparedCatalog:
    normalized: NormalizedSlice
    artifact_sha256: str
    logical_sha256: str
    catalog_manifest_sha256: str
    catalog_manifest: dict[str, object]
    source: dict[str, object]
    coordinate_unit: dict[str, object]
    summary: dict[str, object]
    records: tuple[_TaskRecord, ...]

    @property
    def result(self) -> CatalogImportResult:
        return CatalogImportResult(
            catalog_sha256=self.artifact_sha256,
            catalog_logical_sha256=self.logical_sha256,
            catalog_manifest_sha256=self.catalog_manifest_sha256,
            task_count=len(self.records),
            projected_task_count=sum(record.problem is not None for record in self.records),
        )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _record_hash(
    summary: dict[str, object],
    detail: dict[str, object],
    problem: dict[str, object] | None,
) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            {
                "detail": detail,
                "solver_problem": problem,
                "summary": summary,
            }
        )
    ).hexdigest()


_READ_MODEL_ROOT_FIELDS = (
    "catalog_ordinal",
    "tasks_index",
    "source_row_index",
    "sheet_type",
    "is_train",
    "is_val",
    "is_test",
    "normalization_status",
    "support_status",
    "projection_status",
    "part_count",
    "shape_count",
    "constraint_count",
    "constraint_types",
    "summary_json",
    "detail_json",
    "solver_problem_json",
    "record_sha256",
)


def compute_read_model_root(records: Iterable[Mapping[str, object]]) -> str:
    """Hash the complete ordered read-model payload without model normalization."""

    canonical_records: list[dict[str, object]] = []
    for record in records:
        try:
            item = {field: record[field] for field in _READ_MODEL_ROOT_FIELDS}
        except KeyError as error:
            raise CatalogImportError(
                "read-model root record is missing a required field"
            ) from error
        constraint_types = item["constraint_types"]
        if not isinstance(constraint_types, (list, tuple)) or any(
            not isinstance(value, str) for value in constraint_types
        ):
            raise CatalogImportError("read-model root constraint types are invalid")
        item["constraint_types"] = list(constraint_types)
        canonical_records.append(item)
    canonical_records.sort(key=lambda item: item["catalog_ordinal"])
    if [item["catalog_ordinal"] for item in canonical_records] != list(
        range(len(canonical_records))
    ):
        raise CatalogImportError("read-model root requires contiguous catalog ordinals")
    return hashlib.sha256(
        _canonical_bytes(
            {
                "records": canonical_records,
                "schema_version": READ_MODEL_ROOT_SCHEMA_VERSION,
            }
        )
    ).hexdigest()


def _parse_catalog_manifest(payload: bytes) -> tuple[_CatalogManifest, dict[str, object]]:
    decoded = decode_strict_json_bytes(payload, label="catalog manifest")
    if not isinstance(decoded, dict):
        raise CatalogImportError("Invalid catalog manifest: root must be an object")
    try:
        return _CatalogManifest.model_validate(decoded), decoded
    except ValidationError as error:
        raise CatalogImportError(f"Invalid catalog manifest: {error}") from error


def _validate_catalog_counts(normalized: NormalizedSlice, manifest: _CatalogManifest) -> None:
    observed = {
        "tasks": len(normalized.tasks),
        "parts": len(normalized.parts),
        "shapes": len(normalized.shapes),
        "derived_geometry": len(normalized.derived_geometry),
        "constraints": len(normalized.constraints),
    }
    if observed != manifest.counts.model_dump():
        raise CatalogImportError(
            f"Invalid catalog: manifest row counts do not match the artifact: {observed!r}"
        )
    capability = {
        "runnable_with_explicit_assumptions": sum(
            item.support_status.value == "runnable_with_explicit_assumptions"
            for item in normalized.task_dispositions
        ),
        "view_only": sum(
            item.support_status.value == "view_only" for item in normalized.task_dispositions
        ),
    }
    if capability != manifest.capability_distribution.model_dump():
        raise CatalogImportError(
            "Invalid catalog: capability distribution does not match the artifact"
        )


def _prepare_catalog(
    *,
    catalog_path: Path,
    catalog_manifest_path: Path,
    source_manifest_path: Path,
    audit_report_path: Path,
) -> _PreparedCatalog:
    try:
        catalog_payload = read_passive_evidence_file(
            catalog_path,
            label="catalog",
            max_bytes=MAX_CATALOG_BYTES,
        )
        normalized = parse_normalized_slice(catalog_payload, max_bytes=MAX_CATALOG_BYTES)
    except PassiveEvidenceError as error:
        raise CatalogImportError(f"Invalid catalog: {error}") from error

    try:
        catalog_manifest_payload = read_passive_evidence_file(
            catalog_manifest_path,
            label="catalog manifest",
        )
        catalog_manifest, catalog_manifest_json = _parse_catalog_manifest(catalog_manifest_payload)
        source_manifest_payload = read_passive_evidence_file(
            source_manifest_path,
            label="dataset source manifest",
        )
        audit_report_payload = read_passive_evidence_file(
            audit_report_path,
            label="Lectra audit report",
        )
        source_manifest = parse_dataset_source_manifest(source_manifest_payload)
        audit_report = parse_lectra_audit_report(audit_report_payload)
        bind_normalized_slice_evidence(
            normalized,
            audit_report,
            source_manifest,
            report_payload=audit_report_payload,
            manifest_payload=source_manifest_payload,
        )
    except (PassiveEvidenceError, CatalogImportError) as error:
        if isinstance(error, CatalogImportError):
            raise
        raise CatalogImportError(f"Invalid catalog evidence: {error}") from error

    artifact_sha256 = hashlib.sha256(catalog_payload).hexdigest()
    logical_sha256 = hashlib.sha256(
        _canonical_bytes(normalized.model_dump(mode="json"))
    ).hexdigest()
    catalog_manifest_sha256 = hashlib.sha256(catalog_manifest_payload).hexdigest()
    if not hmac.compare_digest(
        catalog_manifest_sha256,
        COMMITTED_CATALOG_MANIFEST_SHA256,
    ):
        raise CatalogImportError("Invalid catalog manifest: committed identity mismatch")
    if catalog_manifest.artifact.name != catalog_path.name:
        raise CatalogImportError("Invalid catalog: manifest artifact name does not match the path")
    if catalog_manifest.artifact.size_bytes != len(catalog_payload) or not hmac.compare_digest(
        catalog_manifest.artifact.sha256,
        artifact_sha256,
    ):
        raise CatalogImportError("Invalid catalog: artifact identity does not match its manifest")
    if catalog_manifest.dataset_id != source_manifest.dataset_id:
        raise CatalogImportError("Invalid catalog: manifest dataset identity mismatch")
    if (
        catalog_manifest.evidence.source_manifest_sha256 != normalized.source.source_manifest_sha256
        or catalog_manifest.evidence.audit_report_sha256 != normalized.source.audit_report_sha256
        or catalog_manifest.evidence.conversion_ruleset_version
        != normalized.source.conversion_ruleset_version
    ):
        raise CatalogImportError("Invalid catalog: manifest evidence identity mismatch")
    if len(normalized.tasks) != _EXPECTED_TASK_COUNT:
        raise CatalogImportError("Invalid catalog: exactly 256 tasks are required")
    if normalized.source.dataset_id != LECTRA_DATASET_ID:
        raise CatalogImportError("Invalid catalog: unexpected dataset identity")
    _validate_catalog_counts(normalized, catalog_manifest)

    query_service = CorpusQueryService(
        normalized,
        slice_sha256=artifact_sha256,
        evidence_status="fully_bound_to_local_audit_evidence",
        cursor_signing_key=b"yieldforge-catalog-importer-key!",
    )
    summary_model = query_service.summary()
    records: list[_TaskRecord] = []
    source_keys: set[tuple[int, int]] = set()
    for ordinal, task in enumerate(
        sorted(normalized.tasks, key=lambda item: (item.source_row_index, item.tasks_index))
    ):
        source_key = (task.source_row_index, task.tasks_index)
        if source_key in source_keys:
            raise CatalogImportError(f"Invalid catalog: duplicate source key {source_key!r}")
        source_keys.add(source_key)
        detail_model = query_service.task_detail(task.tasks_index)
        summary = detail_model.summary.model_dump(mode="json")
        detail = detail_model.model_dump(mode="json")
        disposition = next(
            item for item in normalized.task_dispositions if item.tasks_index == task.tasks_index
        )
        problem: dict[str, object] | None = None
        if disposition.projection_status in {
            ProjectionStatus.ELIGIBLE,
            ProjectionStatus.PROJECTED,
        }:
            problem_model = project_task(normalized, task.tasks_index)
            problem = problem_model.model_dump(mode="json")
            StripPackingProblem.model_validate(problem)
        records.append(
            _TaskRecord(
                catalog_ordinal=ordinal,
                tasks_index=task.tasks_index,
                source_row_index=task.source_row_index,
                sheet_type=task.sheet_type,
                is_train=task.is_train,
                is_val=task.is_val,
                is_test=task.is_test,
                normalization_status=disposition.normalization_status.value,
                support_status=disposition.support_status.value,
                projection_status=disposition.projection_status.value,
                part_count=detail_model.summary.part_count,
                shape_count=detail_model.summary.shape_count,
                constraint_count=detail_model.summary.constraint_count,
                constraint_types=detail_model.summary.constraint_types,
                summary=summary,
                detail=detail,
                problem=problem,
                record_sha256=_record_hash(summary, detail, problem),
            )
        )
    read_model_root = compute_read_model_root(
        _task_record_root_mapping(record) for record in records
    )
    if not hmac.compare_digest(read_model_root, COMMITTED_READ_MODEL_ROOT_SHA256):
        raise CatalogImportError("Invalid catalog: committed read-model root mismatch")
    return _PreparedCatalog(
        normalized=normalized,
        artifact_sha256=artifact_sha256,
        logical_sha256=logical_sha256,
        catalog_manifest_sha256=catalog_manifest_sha256,
        catalog_manifest=catalog_manifest_json,
        source=summary_model.source.model_dump(mode="json"),
        coordinate_unit=summary_model.coordinate_unit.model_dump(mode="json"),
        summary=summary_model.model_dump(mode="json"),
        records=tuple(records),
    )


_CATALOG_COLUMN_SIGNATURE = {
    "singleton": ("smallint", True, "", "", None),
    "schema_version": ("text", True, "", "", None),
    "dataset_id": ("text", True, "", "", None),
    "catalog_sha256": ("character(64)", True, "", "", None),
    "catalog_logical_sha256": ("character(64)", True, "", "", None),
    "catalog_manifest_sha256": ("character(64)", True, "", "", None),
    "source_manifest_sha256": ("character(64)", True, "", "", None),
    "audit_report_sha256": ("character(64)", True, "", "", None),
    "conversion_ruleset_version": ("text", True, "", "", None),
    "task_count": ("integer", True, "", "", None),
    "part_count": ("integer", True, "", "", None),
    "shape_count": ("integer", True, "", "", None),
    "constraint_count": ("integer", True, "", "", None),
    "source_json": ("jsonb", True, "", "", None),
    "coordinate_unit_json": ("jsonb", True, "", "", None),
    "summary_json": ("jsonb", True, "", "", None),
    "catalog_manifest_json": ("jsonb", True, "", "", None),
    "created_at": (
        "timestamp with time zone",
        True,
        "",
        "",
        "statement_timestamp()",
    ),
}
_TASK_COLUMN_SIGNATURE = {
    "catalog_singleton": ("smallint", True, "", "", None),
    "catalog_ordinal": ("integer", True, "", "", None),
    "tasks_index": ("bigint", True, "", "", None),
    "source_row_index": ("bigint", True, "", "", None),
    "sheet_type": ("bigint", True, "", "", None),
    "is_train": ("boolean", True, "", "", None),
    "is_val": ("boolean", True, "", "", None),
    "is_test": ("boolean", True, "", "", None),
    "normalization_status": ("text", True, "", "", None),
    "support_status": ("text", True, "", "", None),
    "projection_status": ("text", True, "", "", None),
    "part_count": ("integer", True, "", "", None),
    "shape_count": ("integer", True, "", "", None),
    "constraint_count": ("integer", True, "", "", None),
    "constraint_types": ("text[]", True, "", "", None),
    "summary_json": ("jsonb", True, "", "", None),
    "detail_json": ("jsonb", True, "", "", None),
    "solver_problem_json": ("jsonb", False, "", "", None),
    "record_sha256": ("character(64)", True, "", "", None),
}
_CATALOG_CONSTRAINT_SIGNATURE = {
    "yieldforge_catalog_pkey": (
        "p",
        False,
        False,
        True,
        "PRIMARY KEY (singleton)",
    ),
    "yieldforge_catalog_singleton_check": (
        "c",
        False,
        False,
        True,
        "CHECK (singleton = 1)",
    ),
}
_TASK_CONSTRAINT_SIGNATURE = {
    "yieldforge_catalog_task_catalog_ordinal_key": (
        "u",
        False,
        False,
        True,
        "UNIQUE (catalog_ordinal)",
    ),
    "yieldforge_catalog_task_catalog_singleton_fkey": (
        "f",
        False,
        False,
        True,
        "FOREIGN KEY (catalog_singleton) REFERENCES yieldforge_catalog(singleton)",
    ),
    "yieldforge_catalog_task_pkey": (
        "p",
        False,
        False,
        True,
        "PRIMARY KEY (tasks_index)",
    ),
    "yieldforge_catalog_task_source_row_index_key": (
        "u",
        False,
        False,
        True,
        "UNIQUE (source_row_index)",
    ),
}
_CATALOG_INDEX_SIGNATURE = {
    "yieldforge_catalog_pkey": (
        "btree",
        True,
        True,
        True,
        True,
        False,
        "USING btree (singleton)",
    ),
}
_TASK_INDEX_SIGNATURE = {
    "yieldforge_catalog_task_catalog_ordinal_key": (
        "btree",
        True,
        False,
        True,
        True,
        False,
        "USING btree (catalog_ordinal)",
    ),
    "yieldforge_catalog_task_constraint_types_idx": (
        "gin",
        False,
        False,
        True,
        True,
        False,
        "USING gin (constraint_types)",
    ),
    "yieldforge_catalog_task_part_count_idx": (
        "btree",
        False,
        False,
        True,
        True,
        False,
        "USING btree (part_count)",
    ),
    "yieldforge_catalog_task_pkey": (
        "btree",
        True,
        True,
        True,
        True,
        False,
        "USING btree (tasks_index)",
    ),
    "yieldforge_catalog_task_source_order_idx": (
        "btree",
        False,
        False,
        True,
        True,
        False,
        "USING btree (source_row_index, tasks_index)",
    ),
    "yieldforge_catalog_task_source_row_index_key": (
        "btree",
        True,
        False,
        True,
        True,
        False,
        "USING btree (source_row_index)",
    ),
    "yieldforge_catalog_task_support_status_idx": (
        "btree",
        False,
        False,
        True,
        True,
        False,
        "USING btree (support_status)",
    ),
}


def _create_schema(connection: psycopg.Connection[dict[str, object]]) -> None:
    connection.execute(
        """
        CREATE TABLE yieldforge_catalog (
            singleton smallint PRIMARY KEY CHECK (singleton = 1),
            schema_version text NOT NULL,
            dataset_id text NOT NULL,
            catalog_sha256 char(64) NOT NULL,
            catalog_logical_sha256 char(64) NOT NULL,
            catalog_manifest_sha256 char(64) NOT NULL,
            source_manifest_sha256 char(64) NOT NULL,
            audit_report_sha256 char(64) NOT NULL,
            conversion_ruleset_version text NOT NULL,
            task_count integer NOT NULL,
            part_count integer NOT NULL,
            shape_count integer NOT NULL,
            constraint_count integer NOT NULL,
            source_json jsonb NOT NULL,
            coordinate_unit_json jsonb NOT NULL,
            summary_json jsonb NOT NULL,
            catalog_manifest_json jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT statement_timestamp()
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE yieldforge_catalog_task (
            catalog_singleton smallint NOT NULL REFERENCES yieldforge_catalog(singleton),
            catalog_ordinal integer NOT NULL UNIQUE,
            tasks_index bigint PRIMARY KEY,
            source_row_index bigint NOT NULL UNIQUE,
            sheet_type bigint NOT NULL,
            is_train boolean NOT NULL,
            is_val boolean NOT NULL,
            is_test boolean NOT NULL,
            normalization_status text NOT NULL,
            support_status text NOT NULL,
            projection_status text NOT NULL,
            part_count integer NOT NULL,
            shape_count integer NOT NULL,
            constraint_count integer NOT NULL,
            constraint_types text[] NOT NULL,
            summary_json jsonb NOT NULL,
            detail_json jsonb NOT NULL,
            solver_problem_json jsonb,
            record_sha256 char(64) NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX yieldforge_catalog_task_source_order_idx "
        "ON yieldforge_catalog_task (source_row_index, tasks_index)"
    )
    connection.execute(
        "CREATE INDEX yieldforge_catalog_task_support_status_idx "
        "ON yieldforge_catalog_task (support_status)"
    )
    connection.execute(
        "CREATE INDEX yieldforge_catalog_task_part_count_idx "
        "ON yieldforge_catalog_task (part_count)"
    )
    connection.execute(
        "CREATE INDEX yieldforge_catalog_task_constraint_types_idx "
        "ON yieldforge_catalog_task USING gin (constraint_types)"
    )


def _table_signature(
    connection: psycopg.Connection[dict[str, object]],
    table: str,
) -> dict[str, tuple[str, bool, str, str, str | None]]:
    rows = connection.execute(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type_name, "
        "a.attnotnull, a.attidentity, a.attgenerated, "
        "pg_get_expr(d.adbin, d.adrelid, true) AS default_expr "
        "FROM pg_attribute a "
        "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY a.attnum",
        (table,),
    ).fetchall()
    return {
        str(row["attname"]): (
            str(row["type_name"]),
            bool(row["attnotnull"]),
            str(row["attidentity"]),
            str(row["attgenerated"]),
            str(row["default_expr"]) if row["default_expr"] is not None else None,
        )
        for row in rows
    }


def _constraint_signature(
    connection: psycopg.Connection[dict[str, object]],
    table: str,
) -> dict[str, tuple[str, bool, bool, bool, str]]:
    rows = connection.execute(
        "SELECT conname, contype, condeferrable, condeferred, convalidated, "
        "pg_get_constraintdef(oid, true) AS definition "
        "FROM pg_constraint WHERE conrelid = to_regclass(%s) ORDER BY conname",
        (table,),
    ).fetchall()
    return {
        str(row["conname"]): (
            str(row["contype"]),
            bool(row["condeferrable"]),
            bool(row["condeferred"]),
            bool(row["convalidated"]),
            str(row["definition"]),
        )
        for row in rows
    }


def _index_signature(
    connection: psycopg.Connection[dict[str, object]],
    table: str,
) -> dict[str, tuple[str, bool, bool, bool, bool, bool, str]]:
    rows = connection.execute(
        "SELECT i.relname AS index_name, am.amname, x.indisunique, x.indisprimary, "
        "x.indisvalid, x.indisready, x.indnullsnotdistinct, "
        "regexp_replace(pg_get_indexdef(x.indexrelid), "
        "'^CREATE (UNIQUE )?INDEX [^ ]+ ON [^ ]+ ', '') AS definition "
        "FROM pg_index x "
        "JOIN pg_class i ON i.oid = x.indexrelid "
        "JOIN pg_am am ON am.oid = i.relam "
        "WHERE x.indrelid = to_regclass(%s) ORDER BY i.relname",
        (table,),
    ).fetchall()
    return {
        str(row["index_name"]): (
            str(row["amname"]),
            bool(row["indisunique"]),
            bool(row["indisprimary"]),
            bool(row["indisvalid"]),
            bool(row["indisready"]),
            bool(row["indnullsnotdistinct"]),
            str(row["definition"]),
        )
        for row in rows
    }


def _is_plain_permanent_table(
    connection: psycopg.Connection[dict[str, object]],
    table: str,
) -> bool:
    row = connection.execute(
        "SELECT relkind, relpersistence, relispartition, relrowsecurity, relforcerowsecurity "
        "FROM pg_class WHERE oid = to_regclass(%s)",
        (table,),
    ).fetchone()
    return row == {
        "relkind": "r",
        "relpersistence": "p",
        "relispartition": False,
        "relrowsecurity": False,
        "relforcerowsecurity": False,
    }


def _has_unexpected_execution_hooks(
    connection: psycopg.Connection[dict[str, object]],
    table: str,
) -> bool:
    row = connection.execute(
        "SELECT "
        "EXISTS (SELECT 1 FROM pg_trigger "
        "WHERE tgrelid = to_regclass(%s) AND NOT tgisinternal) AS has_user_triggers, "
        "EXISTS (SELECT 1 FROM pg_rewrite "
        "WHERE ev_class = to_regclass(%s)) AS has_rewrite_rules, "
        "EXISTS (SELECT 1 FROM pg_policy "
        "WHERE polrelid = to_regclass(%s)) AS has_row_security_policies",
        (table, table, table),
    ).fetchone()
    return any(bool(value) for value in row.values())


def _ensure_schema(connection: psycopg.Connection[dict[str, object]]) -> None:
    connection.execute("SELECT set_config('quote_all_identifiers', 'off', true)")
    existence = connection.execute(
        "SELECT to_regclass('yieldforge_catalog') IS NOT NULL AS catalog_exists, "
        "to_regclass('yieldforge_catalog_task') IS NOT NULL AS task_exists"
    ).fetchone()
    catalog_exists = existence["catalog_exists"]
    task_exists = existence["task_exists"]
    if not catalog_exists and not task_exists:
        _create_schema(connection)
    elif not catalog_exists or not task_exists:
        raise CatalogImportError("PostgreSQL has a partial or unexpected schema")
    validate_read_model_schema(connection)


def validate_read_model_schema(
    connection: psycopg.Connection[dict[str, object]],
) -> None:
    """Validate the exact serving schema without creating or changing it."""

    connection.execute("SELECT set_config('quote_all_identifiers', 'off', true)")
    existence = connection.execute(
        "SELECT to_regclass('yieldforge_catalog') IS NOT NULL AS catalog_exists, "
        "to_regclass('yieldforge_catalog_task') IS NOT NULL AS task_exists"
    ).fetchone()
    if not existence["catalog_exists"] or not existence["task_exists"]:
        raise CatalogImportError("PostgreSQL has a partial or unexpected schema")
    if (
        not _is_plain_permanent_table(connection, "yieldforge_catalog")
        or not _is_plain_permanent_table(connection, "yieldforge_catalog_task")
        or _has_unexpected_execution_hooks(connection, "yieldforge_catalog")
        or _has_unexpected_execution_hooks(connection, "yieldforge_catalog_task")
        or _table_signature(connection, "yieldforge_catalog") != _CATALOG_COLUMN_SIGNATURE
        or _table_signature(connection, "yieldforge_catalog_task") != _TASK_COLUMN_SIGNATURE
        or _constraint_signature(connection, "yieldforge_catalog") != _CATALOG_CONSTRAINT_SIGNATURE
        or _constraint_signature(connection, "yieldforge_catalog_task")
        != _TASK_CONSTRAINT_SIGNATURE
        or _index_signature(connection, "yieldforge_catalog") != _CATALOG_INDEX_SIGNATURE
        or _index_signature(connection, "yieldforge_catalog_task") != _TASK_INDEX_SIGNATURE
    ):
        raise CatalogImportError("PostgreSQL has a partial or unexpected schema")


def _expected_catalog_row(prepared: _PreparedCatalog) -> dict[str, object]:
    counts = prepared.catalog_manifest["counts"]
    if not isinstance(counts, dict):
        raise CatalogImportError("Invalid catalog manifest counts")
    return {
        "singleton": 1,
        "schema_version": READ_MODEL_SCHEMA_VERSION,
        "dataset_id": prepared.normalized.source.dataset_id,
        "catalog_sha256": prepared.artifact_sha256,
        "catalog_logical_sha256": prepared.logical_sha256,
        "catalog_manifest_sha256": prepared.catalog_manifest_sha256,
        "source_manifest_sha256": prepared.normalized.source.source_manifest_sha256,
        "audit_report_sha256": prepared.normalized.source.audit_report_sha256,
        "conversion_ruleset_version": prepared.normalized.source.conversion_ruleset_version,
        "task_count": len(prepared.records),
        "part_count": counts["parts"],
        "shape_count": counts["shapes"],
        "constraint_count": counts["constraints"],
        "source_json": prepared.source,
        "coordinate_unit_json": prepared.coordinate_unit,
        "summary_json": prepared.summary,
        "catalog_manifest_json": prepared.catalog_manifest,
    }


def _insert_catalog(
    connection: psycopg.Connection[dict[str, object]],
    prepared: _PreparedCatalog,
) -> None:
    expected = _expected_catalog_row(prepared)
    connection.execute(
        """
        INSERT INTO yieldforge_catalog (
            singleton, schema_version, dataset_id, catalog_sha256,
            catalog_logical_sha256, catalog_manifest_sha256,
            source_manifest_sha256, audit_report_sha256,
            conversion_ruleset_version, task_count, part_count, shape_count,
            constraint_count, source_json, coordinate_unit_json, summary_json,
            catalog_manifest_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        """,
        (
            expected["singleton"],
            expected["schema_version"],
            expected["dataset_id"],
            expected["catalog_sha256"],
            expected["catalog_logical_sha256"],
            expected["catalog_manifest_sha256"],
            expected["source_manifest_sha256"],
            expected["audit_report_sha256"],
            expected["conversion_ruleset_version"],
            expected["task_count"],
            expected["part_count"],
            expected["shape_count"],
            expected["constraint_count"],
            Jsonb(expected["source_json"]),
            Jsonb(expected["coordinate_unit_json"]),
            Jsonb(expected["summary_json"]),
            Jsonb(expected["catalog_manifest_json"]),
        ),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO yieldforge_catalog_task (
                catalog_singleton, catalog_ordinal, tasks_index, source_row_index,
                sheet_type, is_train, is_val, is_test, normalization_status,
                support_status, projection_status, part_count, shape_count,
                constraint_count, constraint_types, summary_json, detail_json,
                solver_problem_json, record_sha256
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            [
                (
                    1,
                    record.catalog_ordinal,
                    record.tasks_index,
                    record.source_row_index,
                    record.sheet_type,
                    record.is_train,
                    record.is_val,
                    record.is_test,
                    record.normalization_status,
                    record.support_status,
                    record.projection_status,
                    record.part_count,
                    record.shape_count,
                    record.constraint_count,
                    list(record.constraint_types),
                    Jsonb(record.summary),
                    Jsonb(record.detail),
                    Jsonb(record.problem) if record.problem is not None else None,
                    record.record_sha256,
                )
                for record in prepared.records
            ],
        )


def _validate_existing_task(
    row: dict[str, object],
    expected: _TaskRecord,
) -> None:
    try:
        summary_model = TaskSummaryDto.model_validate(row["summary_json"])
        detail_model = TaskDetailDto.model_validate(row["detail_json"])
    except (KeyError, ValidationError) as error:
        raise CatalogImportError("existing catalog task contains invalid strict JSON") from error
    if detail_model.summary != summary_model:
        raise CatalogImportError("existing catalog task has a summary/detail mismatch")
    problem_json = row["solver_problem_json"]
    eligible = summary_model.solve_capability.can_solve
    if not eligible and problem_json is not None:
        raise CatalogImportError("existing ineligible task contains a solver problem payload")
    if eligible and problem_json is None:
        raise CatalogImportError("existing eligible task is missing its solver problem payload")
    if problem_json is not None:
        try:
            StripPackingProblem.model_validate(problem_json)
        except ValidationError as error:
            raise CatalogImportError("existing task contains an invalid solver problem") from error
    summary = summary_model.model_dump(mode="json")
    detail = detail_model.model_dump(mode="json")
    problem = (
        StripPackingProblem.model_validate(problem_json).model_dump(mode="json")
        if problem_json is not None
        else None
    )
    if row["record_sha256"] != _record_hash(summary, detail, problem):
        raise CatalogImportError("existing catalog task record hash does not revalidate")
    expected_scalars = {
        "catalog_singleton": 1,
        "catalog_ordinal": expected.catalog_ordinal,
        "tasks_index": expected.tasks_index,
        "source_row_index": expected.source_row_index,
        "sheet_type": expected.sheet_type,
        "is_train": expected.is_train,
        "is_val": expected.is_val,
        "is_test": expected.is_test,
        "normalization_status": expected.normalization_status,
        "support_status": expected.support_status,
        "projection_status": expected.projection_status,
        "part_count": expected.part_count,
        "shape_count": expected.shape_count,
        "constraint_count": expected.constraint_count,
        "constraint_types": list(expected.constraint_types),
        "record_sha256": expected.record_sha256,
    }
    if any(row[key] != value for key, value in expected_scalars.items()) or (
        summary != expected.summary or detail != expected.detail or problem != expected.problem
    ):
        raise CatalogImportError("existing catalog task is not logically equivalent")


def _revalidate_existing(
    connection: psycopg.Connection[dict[str, object]],
    prepared: _PreparedCatalog,
    catalog_row: dict[str, object],
) -> None:
    identity_fields = (
        "catalog_sha256",
        "catalog_logical_sha256",
        "catalog_manifest_sha256",
        "source_manifest_sha256",
        "audit_report_sha256",
    )
    expected_catalog = _expected_catalog_row(prepared)
    if any(catalog_row[field] != expected_catalog[field] for field in identity_fields):
        raise CatalogImportError("PostgreSQL contains a different catalog identity")
    for model, field in (
        (CorpusSourceDto, "source_json"),
        (CoordinateUnitDto, "coordinate_unit_json"),
        (CorpusSummaryDto, "summary_json"),
        (_CatalogManifest, "catalog_manifest_json"),
    ):
        try:
            model.model_validate(catalog_row[field])
        except ValidationError as error:
            raise CatalogImportError(
                "existing catalog metadata contains invalid strict JSON"
            ) from error
    if any(catalog_row[key] != value for key, value in expected_catalog.items()):
        raise CatalogImportError("existing catalog metadata is not logically equivalent")
    rows = connection.execute(
        "SELECT * FROM yieldforge_catalog_task ORDER BY catalog_ordinal"
    ).fetchall()
    if len(rows) != len(prepared.records):
        raise CatalogImportError("existing catalog task row count does not revalidate")
    for row, expected in zip(rows, prepared.records, strict=True):
        _validate_existing_task(row, expected)


def import_catalog(
    *,
    database_url: str,
    catalog_path: Path,
    catalog_manifest_path: Path,
    source_manifest_path: Path,
    audit_report_path: Path,
) -> CatalogImportResult:
    """Validate and transactionally import exactly one committed catalog identity."""

    prepared = _prepare_catalog(
        catalog_path=catalog_path,
        catalog_manifest_path=catalog_manifest_path,
        source_manifest_path=source_manifest_path,
        audit_report_path=audit_report_path,
    )
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
            _ensure_schema(connection)
            catalog_rows = connection.execute(
                "SELECT * FROM yieldforge_catalog ORDER BY singleton"
            ).fetchall()
            task_count = connection.execute(
                "SELECT count(*) AS count FROM yieldforge_catalog_task"
            ).fetchone()["count"]
            if not catalog_rows:
                if task_count != 0:
                    raise CatalogImportError("PostgreSQL contains a partially populated catalog")
                _insert_catalog(connection, prepared)
                inserted_catalog_rows = connection.execute(
                    "SELECT * FROM yieldforge_catalog ORDER BY singleton"
                ).fetchall()
                if len(inserted_catalog_rows) != 1:
                    raise CatalogImportError(
                        "PostgreSQL catalog insert did not produce exactly one catalog row"
                    )
                _revalidate_existing(connection, prepared, inserted_catalog_rows[0])
            elif len(catalog_rows) == 1:
                _revalidate_existing(connection, prepared, catalog_rows[0])
            else:
                raise CatalogImportError("PostgreSQL contains unexpected catalog rows")
    except CatalogImportError:
        raise
    except psycopg.Error as error:
        raise CatalogImportError(f"PostgreSQL catalog import failed: {error}") from error
    return prepared.result


__all__ = [
    "CatalogImportError",
    "CatalogImportResult",
    "COMMITTED_CATALOG_LOGICAL_SHA256",
    "COMMITTED_CATALOG_MANIFEST_SHA256",
    "COMMITTED_CATALOG_SHA256",
    "COMMITTED_READ_MODEL_ROOT_SHA256",
    "MAX_CATALOG_BYTES",
    "READ_MODEL_ROOT_SCHEMA_VERSION",
    "READ_MODEL_SCHEMA_VERSION",
    "compute_read_model_root",
    "import_catalog",
    "validate_read_model_schema",
]
