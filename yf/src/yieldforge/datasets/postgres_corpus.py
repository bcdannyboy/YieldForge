"""Fail-closed PostgreSQL query adapter for the committed Lectra catalog."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from yieldforge.datasets.corpus import (
    MAX_SAFE_JSON_INTEGER,
    MIN_CURSOR_SIGNING_KEY_BYTES,
    CoordinateUnitDto,
    CorpusQueryError,
    CorpusSourceDto,
    CorpusSummaryDto,
    InvalidCursorError,
    InvalidTaskQueryError,
    TaskDetailDto,
    TaskNotFoundError,
    TaskNotSolvableError,
    TaskPageDto,
    TaskSummaryDto,
)
from yieldforge.datasets.normalized_slice import (
    NormalizationStatus,
    ProjectionStatus,
    SupportStatus,
    TaskDisposition,
)
from yieldforge.datasets.passive_report import PassiveEvidenceError, decode_strict_json_bytes
from yieldforge.datasets.postgres_catalog import (
    COMMITTED_CATALOG_LOGICAL_SHA256,
    COMMITTED_CATALOG_MANIFEST_SHA256,
    COMMITTED_CATALOG_SHA256,
    COMMITTED_READ_MODEL_ROOT_SHA256,
    READ_MODEL_SCHEMA_VERSION,
    CatalogImportError,
    compute_read_model_root,
    validate_read_model_schema,
)
from yieldforge.domain import StripPackingProblem

_DATASET_ID = "lectra-7030786-v1.1"
_SOURCE_MANIFEST_SHA256 = "bfb5cc9e29fdbd81a642cbe6eed1b8c5ca9e153d23a17037489b9f63e6e05e89"
_AUDIT_REPORT_SHA256 = "58d1978b59b8a023867f3906f1955b06ffceea52d24f92d31c3db3003091a857"
_RULESET_VERSION = "lectra-catalog-rules.v1"
_EXPECTED_COUNTS = {
    "constraint_count": 8398,
    "part_count": 8358,
    "shape_count": 745,
    "task_count": 256,
}
_EXPECTED_MANIFEST = {
    "artifact": {
        "name": "lectra-catalog.json",
        "sha256": COMMITTED_CATALOG_SHA256,
        "size_bytes": 9_554_652,
    },
    "capability_distribution": {
        "runnable_with_explicit_assumptions": 69,
        "view_only": 187,
    },
    "counts": {
        "constraints": 8398,
        "derived_geometry": 745,
        "parts": 8358,
        "shapes": 745,
        "tasks": 256,
    },
    "dataset_id": _DATASET_ID,
    "evidence": {
        "audit_report_sha256": _AUDIT_REPORT_SHA256,
        "conversion_ruleset_version": _RULESET_VERSION,
        "source_manifest_sha256": _SOURCE_MANIFEST_SHA256,
    },
    "schema_version": "yieldforge.catalog-manifest.v1",
}
_LIST_SQL = """
    SELECT catalog_ordinal, tasks_index, source_row_index, sheet_type,
           is_train, is_val, is_test, normalization_status, support_status,
           projection_status, part_count, shape_count, constraint_count,
           constraint_types, summary_json, record_sha256
    FROM yieldforge_catalog_task
    WHERE (%s::text IS NULL OR support_status = %s::text)
      AND (%s::text IS NULL OR %s::text = ANY(constraint_types))
      AND (%s::bigint IS NULL OR tasks_index = %s::bigint)
      AND (%s::integer IS NULL OR part_count >= %s::integer)
      AND (%s::integer IS NULL OR part_count <= %s::integer)
      AND (source_row_index, tasks_index) > (%s::bigint, %s::bigint)
    ORDER BY source_row_index, tasks_index
    LIMIT %s
"""
_RUNTIME_LIST_IDENTITY_FIELDS = (
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
    "record_sha256",
)


class PostgresCorpusError(CorpusQueryError):
    """The configured PostgreSQL catalog failed closed validation or access."""


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


class PostgresCorpusQueryService:
    """Serve one already-imported committed catalog through bounded read-only SQL."""

    def __init__(
        self,
        database_url: str,
        *,
        cursor_signing_key: bytes | None = None,
    ) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database URL must be a nonempty string")
        if cursor_signing_key is None:
            cursor_signing_key = secrets.token_bytes(MIN_CURSOR_SIGNING_KEY_BYTES)
        elif (
            type(cursor_signing_key) is not bytes
            or len(cursor_signing_key) < MIN_CURSOR_SIGNING_KEY_BYTES
        ):
            raise ValueError("cursor signing key must contain at least 32 private bytes")
        self._database_url = database_url
        self._cursor_key = cursor_signing_key
        self._source: CorpusSourceDto
        self._unit: CoordinateUnitDto
        self._summary: CorpusSummaryDto
        self._catalog_sha256: str
        self._record_hashes: dict[int, str]
        self._list_identities: dict[int, str]
        self._validate_startup()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        try:
            with psycopg.connect(
                self._database_url,
                row_factory=dict_row,
                connect_timeout=3,
            ) as connection:
                connection.read_only = True
                connection.execute("SET LOCAL statement_timeout = '5s'")
                connection.execute("SET LOCAL lock_timeout = '1s'")
                connection.execute("SET LOCAL search_path = pg_catalog, public")
                yield connection
        except PostgresCorpusError:
            raise
        except psycopg.Error as error:
            raise PostgresCorpusError("configured PostgreSQL catalog is unavailable") from error

    @staticmethod
    def _validate_manifest(value: object) -> None:
        try:
            observed = _canonical_bytes(value)
            expected = _canonical_bytes(_EXPECTED_MANIFEST)
        except (TypeError, ValueError) as error:
            raise PostgresCorpusError("catalog manifest JSON is invalid") from error
        if not hmac.compare_digest(observed, expected):
            raise PostgresCorpusError(
                "catalog manifest identity does not match the committed catalog"
            )

    @staticmethod
    def _validate_summary_row(row: dict[str, object]) -> TaskSummaryDto:
        try:
            summary = TaskSummaryDto.model_validate(row["summary_json"])
        except (KeyError, ValidationError) as error:
            raise PostgresCorpusError(
                "catalog task summary contains invalid strict JSON"
            ) from error
        expected = {
            "constraint_count": summary.constraint_count,
            "constraint_types": list(summary.constraint_types),
            "is_test": summary.task.is_test,
            "is_train": summary.task.is_train,
            "is_val": summary.task.is_val,
            "normalization_status": summary.solve_capability.normalization_status.value,
            "part_count": summary.part_count,
            "projection_status": summary.solve_capability.projection_status.value,
            "shape_count": summary.shape_count,
            "sheet_type": summary.task.sheet_type,
            "source_row_index": summary.task.source_row_index,
            "support_status": summary.solve_capability.support_status.value,
            "tasks_index": summary.tasks_index,
        }
        if summary.task.tasks_index != summary.tasks_index or any(
            row.get(key) != expected_value for key, expected_value in expected.items()
        ):
            raise PostgresCorpusError("catalog task scalar facets do not match its strict summary")
        return summary

    def _validate_task_record(
        self,
        row: dict[str, object],
    ) -> tuple[TaskSummaryDto, TaskDetailDto, StripPackingProblem | None]:
        raw_summary = row.get("summary_json")
        raw_detail = row.get("detail_json")
        raw_problem = row.get("solver_problem_json")
        if (
            not isinstance(raw_summary, dict)
            or not isinstance(raw_detail, dict)
            or (raw_problem is not None and not isinstance(raw_problem, dict))
        ):
            raise PostgresCorpusError("catalog task record JSON must contain strict objects")
        if row.get("record_sha256") != _record_hash(raw_summary, raw_detail, raw_problem):
            raise PostgresCorpusError("catalog task record hash does not revalidate")

        summary = self._validate_summary_row(row)
        try:
            detail = TaskDetailDto.model_validate(row["detail_json"])
        except (KeyError, ValidationError) as error:
            raise PostgresCorpusError("catalog task detail contains invalid strict JSON") from error
        if (
            detail.summary != summary
            or detail.source != self._source
            or detail.coordinate_unit != self._unit
        ):
            raise PostgresCorpusError("catalog task summary/detail identity is inconsistent")
        try:
            TaskDisposition(
                tasks_index=summary.tasks_index,
                normalization_status=summary.solve_capability.normalization_status,
                support_status=summary.solve_capability.support_status,
                projection_status=summary.solve_capability.projection_status,
                reason_codes=summary.solve_capability.reason_codes,
                assumption_codes=summary.solve_capability.assumption_codes,
            )
        except ValidationError as error:
            raise PostgresCorpusError("catalog task capability semantics are invalid") from error
        capability = summary.solve_capability
        eligible_projection = capability.projection_status in {
            ProjectionStatus.ELIGIBLE,
            ProjectionStatus.PROJECTED,
        }
        if capability.support_status is SupportStatus.DIRECTLY_SUPPORTED and (
            capability.normalization_status is not NormalizationStatus.SOURCE_LOSSLESS
            or not eligible_projection
            or bool(capability.reason_codes)
            or bool(capability.assumption_codes)
            or capability.requires_assumption_acknowledgement
        ):
            raise PostgresCorpusError("catalog task capability semantics are invalid")
        if capability.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS and (
            capability.normalization_status is not NormalizationStatus.SOURCE_LOSSLESS
            or not eligible_projection
            or bool(capability.reason_codes)
            or not capability.assumption_codes
            or not capability.requires_assumption_acknowledgement
        ):
            raise PostgresCorpusError("catalog task capability semantics are invalid")
        if capability.support_status is SupportStatus.VIEW_ONLY and capability.assumption_codes:
            raise PostgresCorpusError("catalog task capability semantics are invalid")

        problem_json = raw_problem
        can_solve = summary.solve_capability.can_solve
        if can_solve is not (problem_json is not None):
            raise PostgresCorpusError("catalog task capability and solver payload are inconsistent")
        problem: StripPackingProblem | None = None
        if problem_json is not None:
            try:
                problem = StripPackingProblem.model_validate_json(
                    _canonical_bytes(problem_json),
                    strict=True,
                )
            except ValidationError as error:
                raise PostgresCorpusError(
                    "catalog task solver problem contains invalid strict JSON"
                ) from error
            if problem.name != f"lectra-task-{summary.tasks_index}":
                raise PostgresCorpusError(
                    "catalog task solver problem source identity is inconsistent"
                )
        return summary, detail, problem

    def _validate_startup(self) -> None:
        try:
            with self._connection() as connection:
                validate_read_model_schema(connection)
                catalog_rows = connection.execute(
                    "SELECT * FROM yieldforge_catalog ORDER BY singleton"
                ).fetchall()
                if len(catalog_rows) != 1:
                    raise PostgresCorpusError("catalog identity requires exactly one metadata row")
                catalog = catalog_rows[0]
                expected_scalars = {
                    "audit_report_sha256": _AUDIT_REPORT_SHA256,
                    "catalog_logical_sha256": COMMITTED_CATALOG_LOGICAL_SHA256,
                    "catalog_manifest_sha256": COMMITTED_CATALOG_MANIFEST_SHA256,
                    "catalog_sha256": COMMITTED_CATALOG_SHA256,
                    "constraint_count": _EXPECTED_COUNTS["constraint_count"],
                    "conversion_ruleset_version": _RULESET_VERSION,
                    "dataset_id": _DATASET_ID,
                    "part_count": _EXPECTED_COUNTS["part_count"],
                    "schema_version": READ_MODEL_SCHEMA_VERSION,
                    "shape_count": _EXPECTED_COUNTS["shape_count"],
                    "singleton": 1,
                    "source_manifest_sha256": _SOURCE_MANIFEST_SHA256,
                    "task_count": _EXPECTED_COUNTS["task_count"],
                }
                if any(catalog.get(key) != value for key, value in expected_scalars.items()):
                    raise PostgresCorpusError(
                        "catalog identity does not match the committed catalog"
                    )
                self._validate_manifest(catalog.get("catalog_manifest_json"))
                try:
                    source = CorpusSourceDto.model_validate(catalog["source_json"])
                    unit = CoordinateUnitDto.model_validate(catalog["coordinate_unit_json"])
                    summary = CorpusSummaryDto.model_validate(catalog["summary_json"])
                except (KeyError, ValidationError) as error:
                    raise PostgresCorpusError(
                        "catalog metadata contains invalid strict JSON"
                    ) from error
                if (
                    summary.source != source
                    or summary.coordinate_unit != unit
                    or source.slice_sha256 != COMMITTED_CATALOG_SHA256
                    or source.source_manifest_sha256 != _SOURCE_MANIFEST_SHA256
                    or source.audit_report_sha256 != _AUDIT_REPORT_SHA256
                    or source.conversion_ruleset_version != _RULESET_VERSION
                    or summary.task_count != _EXPECTED_COUNTS["task_count"]
                    or summary.part_count != _EXPECTED_COUNTS["part_count"]
                    or summary.shape_count != _EXPECTED_COUNTS["shape_count"]
                    or summary.constraint_count != _EXPECTED_COUNTS["constraint_count"]
                ):
                    raise PostgresCorpusError(
                        "catalog metadata identity or counts are inconsistent"
                    )
                self._source = source
                self._unit = unit
                self._summary = summary
                self._catalog_sha256 = COMMITTED_CATALOG_SHA256

                rows = connection.execute(
                    "SELECT * FROM yieldforge_catalog_task ORDER BY catalog_ordinal"
                ).fetchall()
                if len(rows) != _EXPECTED_COUNTS["task_count"]:
                    raise PostgresCorpusError("catalog task count does not revalidate")
                support_counts: Counter[str] = Counter()
                constraint_counts: Counter[str] = Counter()
                shape_hashes: set[str] = set()
                part_count = 0
                previous_source_key = (-1, -1)
                eligible_count = 0
                for ordinal, row in enumerate(rows):
                    if row.get("catalog_ordinal") != ordinal or row.get("catalog_singleton") != 1:
                        raise PostgresCorpusError("catalog task source order is invalid")
                    source_key = (row.get("source_row_index"), row.get("tasks_index"))
                    if (
                        not all(type(value) is int for value in source_key)
                        or source_key <= previous_source_key
                    ):
                        raise PostgresCorpusError("catalog task source order is invalid")
                    previous_source_key = source_key
                    task_summary, detail, _ = self._validate_task_record(row)
                    support_counts[task_summary.solve_capability.support_status.value] += 1
                    eligible_count += task_summary.solve_capability.can_solve
                    part_count += len(detail.parts)
                    constraint_counts.update(item.type for item in detail.constraints)
                    shape_hashes.update(str(item.shape_hash) for item in detail.shapes)
                observed_support = {
                    item.name: item.count for item in self._summary.support_status_counts
                }
                observed_constraints = {
                    item.name: item.count for item in self._summary.constraint_type_counts
                }
                if (
                    dict(sorted(support_counts.items())) != observed_support
                    or dict(sorted(constraint_counts.items())) != observed_constraints
                    or part_count != _EXPECTED_COUNTS["part_count"]
                    or len(shape_hashes) != _EXPECTED_COUNTS["shape_count"]
                    or sum(constraint_counts.values()) != _EXPECTED_COUNTS["constraint_count"]
                    or eligible_count != self._summary.solve_capability.eligible_task_count
                    or len(rows) - eligible_count
                    != self._summary.solve_capability.blocked_task_count
                ):
                    raise PostgresCorpusError("catalog aggregate counts do not revalidate")
                directly_supported_count = support_counts.get(
                    SupportStatus.DIRECTLY_SUPPORTED.value,
                    0,
                )
                if (
                    directly_supported_count
                    != self._summary.solve_capability.directly_supported_task_count
                ):
                    raise PostgresCorpusError(
                        "catalog directly supported count does not revalidate"
                    )
                if support_counts != Counter(
                    {
                        SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS.value: 69,
                        SupportStatus.VIEW_ONLY.value: 187,
                    }
                ):
                    raise PostgresCorpusError(
                        "catalog capability distribution does not match its committed manifest"
                    )
                if not hmac.compare_digest(
                    compute_read_model_root(rows),
                    COMMITTED_READ_MODEL_ROOT_SHA256,
                ):
                    raise PostgresCorpusError(
                        "catalog read-model root does not match the committed catalog"
                    )
                self._record_hashes = {row["tasks_index"]: row["record_sha256"] for row in rows}
                self._list_identities = {
                    row["tasks_index"]: self._runtime_list_identity(row) for row in rows
                }
        except CatalogImportError as error:
            raise PostgresCorpusError(
                "configured PostgreSQL schema fingerprint is invalid"
            ) from error

    def summary(self) -> CorpusSummaryDto:
        return self._summary

    @staticmethod
    def _validate_query(
        *,
        limit: int,
        status: SupportStatus | str | None,
        constraint_type: str | None,
        task_id: int | None,
        min_parts: int | None,
        max_parts: int | None,
    ) -> tuple[SupportStatus | None, str | None]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise InvalidTaskQueryError("limit must be an integer from 1 through 50")
        try:
            parsed_status = SupportStatus(status) if status is not None else None
        except ValueError as error:
            raise InvalidTaskQueryError("status is not a recognized support status") from error
        if constraint_type is not None and (
            not isinstance(constraint_type, str)
            or not constraint_type.strip()
            or constraint_type != constraint_type.strip()
            or len(constraint_type) > 80
        ):
            raise InvalidTaskQueryError("constraint_type must be trimmed and at most 80 chars")
        for name, value in (
            ("task_id", task_id),
            ("min_parts", min_parts),
            ("max_parts", max_parts),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_SAFE_JSON_INTEGER
            ):
                raise InvalidTaskQueryError(f"{name} must be a browser-safe nonnegative integer")
        if min_parts is not None and max_parts is not None and min_parts > max_parts:
            raise InvalidTaskQueryError("min_parts cannot exceed max_parts")
        return parsed_status, constraint_type

    def _filter_digest(
        self,
        *,
        status: SupportStatus | str | None,
        constraint_type: str | None,
        task_id: int | None,
        min_parts: int | None,
        max_parts: int | None,
    ) -> str:
        parsed_status = SupportStatus(status) if status is not None else None
        payload = json.dumps(
            {
                "constraint_type": constraint_type,
                "max_parts": max_parts,
                "min_parts": min_parts,
                "status": parsed_status.value if parsed_status is not None else None,
                "task_id": task_id,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _encode_cursor(self, *, after: tuple[int, int], filter_digest: str) -> str:
        payload = json.dumps(
            {
                "after": list(after),
                "filters": filter_digest,
                "slice": self._catalog_sha256,
                "v": 1,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        signature = hmac.new(self._cursor_key, payload, hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def _decode_cursor(self, cursor: str, *, filter_digest: str) -> tuple[int, int]:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
            raise InvalidCursorError("cursor must be one bounded opaque string")
        try:
            body, signature = cursor.split(".", maxsplit=1)
            if len(signature) != 64 or any(char not in "0123456789abcdef" for char in signature):
                raise ValueError("invalid signature")
            payload = base64.b64decode(
                body + "=" * (-len(body) % 4),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, UnicodeError) as error:
            raise InvalidCursorError("cursor is malformed or tampered with") from error
        expected = hmac.new(self._cursor_key, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCursorError("cursor is malformed or tampered with")
        try:
            decoded = decode_strict_json_bytes(payload, label="task cursor", max_bytes=512)
        except PassiveEvidenceError as error:
            raise InvalidCursorError("cursor is malformed or tampered with") from error
        if not isinstance(decoded, dict) or set(decoded) != {"after", "filters", "slice", "v"}:
            raise InvalidCursorError("cursor is malformed or tampered with")
        after = decoded["after"]
        if (
            type(decoded["v"]) is not int
            or decoded["v"] != 1
            or not isinstance(decoded["slice"], str)
            or decoded["slice"] != self._catalog_sha256
            or not isinstance(decoded["filters"], str)
            or not isinstance(after, list)
            or len(after) != 2
            or any(type(value) is not int or value < 0 for value in after)
        ):
            raise InvalidCursorError("cursor is stale or malformed")
        if decoded["filters"] != filter_digest:
            raise InvalidCursorError("cursor cannot be reused with different filters")
        return after[0], after[1]

    @staticmethod
    def _list_parameters(
        *,
        status: SupportStatus | None,
        constraint_type: str | None,
        task_id: int | None,
        min_parts: int | None,
        max_parts: int | None,
        after: tuple[int, int],
        limit: int,
    ) -> tuple[object, ...]:
        status_value = status.value if status is not None else None
        return (
            status_value,
            status_value,
            constraint_type,
            constraint_type,
            task_id,
            task_id,
            min_parts,
            min_parts,
            max_parts,
            max_parts,
            after[0],
            after[1],
            limit,
        )

    @staticmethod
    def _runtime_list_identity(row: dict[str, object]) -> str:
        try:
            identity = {field: row[field] for field in _RUNTIME_LIST_IDENTITY_FIELDS}
        except KeyError as error:
            raise PostgresCorpusError(
                "catalog runtime list identity is missing a required field"
            ) from error
        constraint_types = identity["constraint_types"]
        if not isinstance(constraint_types, (list, tuple)) or any(
            not isinstance(value, str) for value in constraint_types
        ):
            raise PostgresCorpusError("catalog runtime list identity is invalid")
        identity["constraint_types"] = list(constraint_types)
        return hashlib.sha256(_canonical_bytes(identity)).hexdigest()

    def _validate_runtime_record_identity(self, row: dict[str, object]) -> None:
        tasks_index = row.get("tasks_index")
        record_sha256 = row.get("record_sha256")
        expected = self._record_hashes.get(tasks_index) if type(tasks_index) is int else None
        if (
            not isinstance(record_sha256, str)
            or expected is None
            or not hmac.compare_digest(record_sha256, expected)
        ):
            raise PostgresCorpusError(
                "catalog runtime record identity does not match validated startup state"
            )
        expected_list_identity = self._list_identities.get(tasks_index)
        if expected_list_identity is None or not hmac.compare_digest(
            self._runtime_list_identity(row),
            expected_list_identity,
        ):
            raise PostgresCorpusError(
                "catalog runtime list identity does not match validated startup state"
            )

    def list_tasks(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        status: SupportStatus | str | None = None,
        constraint_type: str | None = None,
        task_id: int | None = None,
        min_parts: int | None = None,
        max_parts: int | None = None,
    ) -> TaskPageDto:
        parsed_status, parsed_constraint_type = self._validate_query(
            limit=limit,
            status=status,
            constraint_type=constraint_type,
            task_id=task_id,
            min_parts=min_parts,
            max_parts=max_parts,
        )
        filter_digest = self._filter_digest(
            status=parsed_status,
            constraint_type=parsed_constraint_type,
            task_id=task_id,
            min_parts=min_parts,
            max_parts=max_parts,
        )
        after = (
            (-1, -1)
            if cursor is None
            else self._decode_cursor(
                cursor,
                filter_digest=filter_digest,
            )
        )
        with self._connection() as connection:
            if cursor is not None:
                member = connection.execute(
                    "SELECT catalog_ordinal, tasks_index, source_row_index, sheet_type, "
                    "is_train, is_val, is_test, normalization_status, support_status, "
                    "projection_status, part_count, shape_count, constraint_count, "
                    "constraint_types, summary_json, record_sha256 "
                    "FROM yieldforge_catalog_task "
                    "WHERE source_row_index = %s AND tasks_index = %s",
                    after,
                ).fetchone()
                if member is None:
                    raise InvalidCursorError("cursor does not identify a real task member")
                self._validate_runtime_record_identity(member)
                if (
                    (parsed_status is not None and member["support_status"] != parsed_status.value)
                    or (
                        parsed_constraint_type is not None
                        and parsed_constraint_type not in member["constraint_types"]
                    )
                    or (task_id is not None and member["tasks_index"] != task_id)
                    or (min_parts is not None and member["part_count"] < min_parts)
                    or (max_parts is not None and member["part_count"] > max_parts)
                ):
                    raise InvalidCursorError(
                        "cursor does not identify a member of the exact filtered result"
                    )
            rows = connection.execute(
                _LIST_SQL,
                self._list_parameters(
                    status=parsed_status,
                    constraint_type=parsed_constraint_type,
                    task_id=task_id,
                    min_parts=min_parts,
                    max_parts=max_parts,
                    after=after,
                    limit=limit + 1,
                ),
            ).fetchall()
        for row in rows:
            self._validate_runtime_record_identity(row)
        summaries = tuple(self._validate_summary_row(row) for row in rows[:limit])
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = self._encode_cursor(
                after=(last["source_row_index"], last["tasks_index"]),
                filter_digest=filter_digest,
            )
        return TaskPageDto(items=summaries, next_cursor=next_cursor)

    def _task_record(self, tasks_index: int) -> dict[str, object]:
        if (
            isinstance(tasks_index, bool)
            or not isinstance(tasks_index, int)
            or not 0 <= tasks_index <= MAX_SAFE_JSON_INTEGER
        ):
            raise TaskNotFoundError("task was not found")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM yieldforge_catalog_task WHERE tasks_index = %s",
                (tasks_index,),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task {tasks_index} was not found")
        self._validate_runtime_record_identity(row)
        return row

    def task_detail(self, tasks_index: int) -> TaskDetailDto:
        _, detail, _ = self._validate_task_record(self._task_record(tasks_index))
        return detail

    def project_problem(
        self,
        tasks_index: int,
        *,
        acknowledged_assumption_codes: tuple[str, ...],
    ) -> StripPackingProblem:
        if not isinstance(acknowledged_assumption_codes, tuple) or any(
            not isinstance(code, str) for code in acknowledged_assumption_codes
        ):
            raise TaskNotSolvableError("assumption acknowledgement must be an exact tuple")
        summary, _, problem = self._validate_task_record(self._task_record(tasks_index))
        capability = summary.solve_capability
        if capability.projection_status not in {
            ProjectionStatus.ELIGIBLE,
            ProjectionStatus.PROJECTED,
        }:
            raise TaskNotSolvableError(f"task {tasks_index} is blocked from solving")
        if acknowledged_assumption_codes != capability.assumption_codes:
            raise TaskNotSolvableError(
                f"task {tasks_index} requires exact acknowledgement of its assumptions"
            )
        if problem is None:
            raise PostgresCorpusError("eligible catalog task is missing its solver problem")
        return problem


__all__ = ["PostgresCorpusError", "PostgresCorpusQueryService"]
