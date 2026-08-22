from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from yieldforge.datasets.corpus import (
    InvalidCursorError,
    TaskNotSolvableError,
)
from yieldforge.datasets.postgres_catalog import import_catalog
from yieldforge.datasets.postgres_corpus import (
    PostgresCorpusError,
    PostgresCorpusQueryService,
)
from yieldforge.domain import StripPackingProblem

YF_ROOT = Path(__file__).resolve().parents[2]
CATALOG = YF_ROOT / "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"
CATALOG_MANIFEST = YF_ROOT / "datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json"
SOURCE_MANIFEST = YF_ROOT / "datasets/sources/lectra-7030786-v1.1.json"
AUDIT_REPORT = YF_ROOT / "var/data/reports/lectra-7030786-v1.1/lectra-audit.json"
DATABASE_URL = os.environ.get(
    "YIELDFORGE_TEST_DATABASE_URL",
    "postgresql://yieldforge:yieldforge-local@127.0.0.1:55433/yieldforge",
)
CURSOR_KEY = b"p" * 32
ASSUMPTION = "interpret_s1_degenerate_entries_as_allowed_rotations"


def _service(database_url: str = DATABASE_URL) -> PostgresCorpusQueryService:
    return PostgresCorpusQueryService(database_url, cursor_signing_key=CURSOR_KEY)


@pytest.fixture(scope="module")
def service() -> PostgresCorpusQueryService:
    return _service()


@pytest.fixture
def isolated_database_url() -> Iterator[str]:
    database_name = f"yf_query_test_{uuid.uuid4().hex}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    isolated_url = DATABASE_URL.rsplit("/", maxsplit=1)[0] + f"/{database_name}"
    import_catalog(
        database_url=isolated_url,
        catalog_path=CATALOG,
        catalog_manifest_path=CATALOG_MANIFEST,
        source_manifest_path=SOURCE_MANIFEST,
        audit_report_path=AUDIT_REPORT,
    )
    try:
        yield isolated_url
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def _signed_cursor(
    *,
    catalog_hash: str,
    after: tuple[int, int],
    filter_digest: str,
) -> str:
    payload = json.dumps(
        {
            "after": list(after),
            "filters": filter_digest,
            "slice": catalog_hash,
            "v": 1,
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = hmac.new(CURSOR_KEY, payload, hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _record_hash(
    summary: dict[str, object],
    detail: dict[str, object],
    problem: dict[str, object] | None,
) -> str:
    payload = json.dumps(
        {
            "detail": detail,
            "solver_problem": problem,
            "summary": summary,
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_summary_and_two_stable_fifty_task_pages(
    service: PostgresCorpusQueryService,
) -> None:
    summary = service.summary()
    first = service.list_tasks(limit=50)
    second = service.list_tasks(limit=50, cursor=first.next_cursor)

    assert summary.task_count == 256
    assert summary.part_count == 8358
    assert summary.shape_count == 745
    assert summary.constraint_count == 8398
    assert len(first.items) == len(second.items) == 50
    assert (first.items[0].tasks_index, first.items[-1].tasks_index) == (147, 15820)
    assert (second.items[0].tasks_index, second.items[-1].tasks_index) == (15899, 36510)
    assert first.next_cursor is not None
    assert second.next_cursor is not None


def test_fixed_filters_are_composable(service: PostgresCorpusQueryService) -> None:
    page = service.list_tasks(
        limit=50,
        status="runnable_with_explicit_assumptions",
        constraint_type="s1",
        min_parts=30,
        max_parts=35,
    )

    assert page.items
    assert all(
        item.solve_capability.support_status == "runnable_with_explicit_assumptions"
        and item.constraint_types == ("s1",)
        and 30 <= item.part_count <= 35
        for item in page.items
    )
    assert service.list_tasks(task_id=13958).items[0].tasks_index == 13958


def test_cursors_reject_tamper_stale_cross_filter_and_nonmember(
    service: PostgresCorpusQueryService,
) -> None:
    cursor = service.list_tasks(limit=1).next_cursor
    assert cursor is not None

    with pytest.raises(InvalidCursorError, match="tampered"):
        service.list_tasks(limit=1, cursor=cursor[:-1] + ("0" if cursor[-1] != "0" else "1"))
    with pytest.raises(InvalidCursorError, match="filters"):
        service.list_tasks(limit=1, cursor=cursor, status="view_only")

    filter_digest = service._filter_digest(  # noqa: SLF001 - adversarial cursor probe
        status=None,
        constraint_type=None,
        task_id=None,
        min_parts=None,
        max_parts=None,
    )
    stale = _signed_cursor(
        catalog_hash="f" * 64,
        after=(147, 147),
        filter_digest=filter_digest,
    )
    with pytest.raises(InvalidCursorError, match="stale"):
        service.list_tasks(limit=1, cursor=stale)

    nonmember = _signed_cursor(
        catalog_hash=service.summary().source.slice_sha256,
        after=(999_998, 999_999),
        filter_digest=filter_digest,
    )
    with pytest.raises(InvalidCursorError, match="member"):
        service.list_tasks(limit=1, cursor=nonmember)

    runnable_digest = service._filter_digest(  # noqa: SLF001 - adversarial cursor probe
        status="runnable_with_explicit_assumptions",
        constraint_type=None,
        task_id=None,
        min_parts=None,
        max_parts=None,
    )
    wrong_membership = _signed_cursor(
        catalog_hash=service.summary().source.slice_sha256,
        after=(147, 147),
        filter_digest=runnable_digest,
    )
    with pytest.raises(InvalidCursorError, match="filtered result"):
        service.list_tasks(
            limit=1,
            cursor=wrong_membership,
            status="runnable_with_explicit_assumptions",
        )


def test_later_task_detail_is_loaded_with_complete_geometry(
    service: PostgresCorpusQueryService,
) -> None:
    detail = service.task_detail(99349)

    assert detail.summary.tasks_index == 99349
    assert len(detail.parts) == detail.summary.part_count == 28
    assert len(detail.shapes) == len(detail.derived_geometry) == detail.summary.shape_count == 6
    assert {shape.shape_hash for shape in detail.shapes} == {
        geometry.shape_hash for geometry in detail.derived_geometry
    }


def test_projection_requires_capability_and_exact_assumptions(
    service: PostgresCorpusQueryService,
) -> None:
    with pytest.raises(TaskNotSolvableError, match="blocked"):
        service.project_problem(25801, acknowledged_assumption_codes=())
    with pytest.raises(TaskNotSolvableError, match="exact acknowledgement"):
        service.project_problem(13958, acknowledged_assumption_codes=())

    problem_13958 = service.project_problem(
        13958,
        acknowledged_assumption_codes=(ASSUMPTION,),
    )
    problem_1460 = service.project_problem(
        1460,
        acknowledged_assumption_codes=(ASSUMPTION,),
    )

    assert problem_13958.name == "lectra-task-13958"
    assert problem_1460.name == "lectra-task-1460"


def test_startup_rejects_catalog_identity_tamper(isolated_database_url: str) -> None:
    with psycopg.connect(isolated_database_url) as connection:
        connection.execute(
            "UPDATE yieldforge_catalog SET catalog_sha256 = %s WHERE singleton = 1",
            ("f" * 64,),
        )

    with pytest.raises(PostgresCorpusError, match="identity"):
        _service(isolated_database_url)


def test_startup_rejects_task_record_tamper(isolated_database_url: str) -> None:
    with psycopg.connect(isolated_database_url) as connection:
        connection.execute(
            "UPDATE yieldforge_catalog_task SET record_sha256 = %s WHERE catalog_ordinal = 200",
            ("0" * 64,),
        )

    with pytest.raises(PostgresCorpusError, match="record hash"):
        _service(isolated_database_url)


def test_startup_hashes_raw_problem_before_strict_numeric_validation(
    isolated_database_url: str,
) -> None:
    with psycopg.connect(isolated_database_url, row_factory=psycopg.rows.dict_row) as connection:
        row = connection.execute(
            "SELECT summary_json, detail_json, solver_problem_json "
            "FROM yieldforge_catalog_task WHERE tasks_index = 13958"
        ).fetchone()
        problem = row["solver_problem_json"]
        problem["strip_height"] = str(problem["strip_height"])
        assert StripPackingProblem.model_validate(problem).strip_height > 0
        record_hash = _record_hash(row["summary_json"], row["detail_json"], problem)
        connection.execute(
            "UPDATE yieldforge_catalog_task "
            "SET solver_problem_json = %s, record_sha256 = %s WHERE tasks_index = 13958",
            (Jsonb(problem), record_hash),
        )

    with pytest.raises(PostgresCorpusError, match="solver problem.*strict JSON"):
        _service(isolated_database_url)


@pytest.mark.parametrize("mutation", ["direct-with-assumption", "duplicate-assumption"])
def test_startup_rejects_rehashed_malformed_capability_semantics(
    isolated_database_url: str,
    mutation: str,
) -> None:
    with psycopg.connect(isolated_database_url, row_factory=psycopg.rows.dict_row) as connection:
        row = connection.execute(
            "SELECT summary_json, detail_json, solver_problem_json "
            "FROM yieldforge_catalog_task WHERE tasks_index = 13958"
        ).fetchone()
        summary = row["summary_json"]
        detail = row["detail_json"]
        if mutation == "direct-with-assumption":
            summary["solve_capability"]["support_status"] = "directly_supported"
            detail["summary"]["solve_capability"]["support_status"] = "directly_supported"
            support_status = "directly_supported"
        else:
            assumptions = [ASSUMPTION, ASSUMPTION]
            summary["solve_capability"]["assumption_codes"] = assumptions
            detail["summary"]["solve_capability"]["assumption_codes"] = assumptions
            support_status = "runnable_with_explicit_assumptions"
        record_hash = _record_hash(summary, detail, row["solver_problem_json"])
        connection.execute(
            "UPDATE yieldforge_catalog_task SET summary_json = %s, detail_json = %s, "
            "support_status = %s, record_sha256 = %s WHERE tasks_index = 13958",
            (Jsonb(summary), Jsonb(detail), support_status, record_hash),
        )

    with pytest.raises(PostgresCorpusError, match="capability semantics"):
        _service(isolated_database_url)


def test_startup_reconciles_the_directly_supported_count(
    isolated_database_url: str,
) -> None:
    with psycopg.connect(isolated_database_url, row_factory=psycopg.rows.dict_row) as connection:
        row = connection.execute(
            "SELECT summary_json, detail_json, solver_problem_json "
            "FROM yieldforge_catalog_task WHERE tasks_index = 13958"
        ).fetchone()
        summary = row["summary_json"]
        detail = row["detail_json"]
        capability = summary["solve_capability"]
        capability["support_status"] = "directly_supported"
        capability["assumption_codes"] = []
        capability["requires_assumption_acknowledgement"] = False
        detail["summary"]["solve_capability"] = capability
        record_hash = _record_hash(summary, detail, row["solver_problem_json"])
        connection.execute(
            "UPDATE yieldforge_catalog_task SET summary_json = %s, detail_json = %s, "
            "support_status = 'directly_supported', record_sha256 = %s "
            "WHERE tasks_index = 13958",
            (Jsonb(summary), Jsonb(detail), record_hash),
        )
        catalog_summary = connection.execute(
            "SELECT summary_json FROM yieldforge_catalog WHERE singleton = 1"
        ).fetchone()["summary_json"]
        support_counts = {
            item["name"]: item["count"] for item in catalog_summary["support_status_counts"]
        }
        support_counts["runnable_with_explicit_assumptions"] -= 1
        support_counts["directly_supported"] = 1
        catalog_summary["support_status_counts"] = [
            {"name": name, "count": count} for name, count in sorted(support_counts.items())
        ]
        connection.execute(
            "UPDATE yieldforge_catalog SET summary_json = %s WHERE singleton = 1",
            (Jsonb(catalog_summary),),
        )

    with pytest.raises(PostgresCorpusError, match="directly supported count"):
        _service(isolated_database_url)
