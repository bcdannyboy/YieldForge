from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from yieldforge.datasets import postgres_catalog
from yieldforge.datasets.postgres_catalog import (
    COMMITTED_READ_MODEL_ROOT_SHA256,
    CatalogImportError,
    compute_read_model_root,
    import_catalog,
)
from yieldforge.domain import StripPackingProblem

YF_ROOT = Path(__file__).resolve().parents[2]
CATALOG = YF_ROOT / "datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json"
CATALOG_MANIFEST = YF_ROOT / "datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json"
SOURCE_MANIFEST = YF_ROOT / "datasets/sources/lectra-7030786-v1.1.json"
AUDIT_REPORT = YF_ROOT / "var/data/reports/lectra-7030786-v1.1/lectra-audit.json"
ADMIN_DATABASE_URL = os.environ.get(
    "YIELDFORGE_TEST_DATABASE_URL",
    "postgresql://yieldforge:yieldforge-local@127.0.0.1:55433/yieldforge",
)


@pytest.fixture
def database_url() -> Iterator[str]:
    database_name = f"yf_test_{uuid.uuid4().hex}"
    with psycopg.connect(ADMIN_DATABASE_URL, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    isolated_url = ADMIN_DATABASE_URL.rsplit("/", maxsplit=1)[0] + f"/{database_name}"
    try:
        yield isolated_url
    finally:
        with psycopg.connect(ADMIN_DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def _import(database_url: str):  # type: ignore[no-untyped-def]
    return import_catalog(
        database_url=database_url,
        catalog_path=CATALOG,
        catalog_manifest_path=CATALOG_MANIFEST,
        source_manifest_path=SOURCE_MANIFEST,
        audit_report_path=AUDIT_REPORT,
    )


def test_imports_exact_catalog_transactionally_and_is_idempotent(database_url: str) -> None:
    first = _import(database_url)
    second = _import(database_url)

    assert second == first
    assert first.schema_version == "yieldforge.postgres-catalog.v1"
    assert first.catalog_sha256 == (
        "4903e28be9b874460ab565b3fc17b06608a9ccce37b699d6bcda49c7eac03138"
    )
    assert first.task_count == 256
    assert first.projected_task_count == 69

    with psycopg.connect(database_url) as connection:
        catalog_count = connection.execute("SELECT count(*) FROM yieldforge_catalog").fetchone()
        task_counts = connection.execute(
            "SELECT count(*), count(solver_problem_json), "
            "count(DISTINCT tasks_index), count(DISTINCT source_row_index) "
            "FROM yieldforge_catalog_task"
        ).fetchone()
        rows = connection.execute(
            "SELECT support_status, summary_json, detail_json, solver_problem_json, "
            "record_sha256 FROM yieldforge_catalog_task ORDER BY catalog_ordinal"
        ).fetchall()

    assert catalog_count == (1,)
    assert task_counts == (256, 69, 256, 256)
    assert all(len(record_sha256) == 64 for *_, record_sha256 in rows)
    for support_status, summary, detail, problem, _ in rows:
        assert detail["summary"] == summary
        if support_status == "runnable_with_explicit_assumptions":
            StripPackingProblem.model_validate(problem)
        else:
            assert problem is None


def test_imported_rows_match_the_pinned_read_model_root(database_url: str) -> None:
    _import(database_url)
    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as connection:
        rows = connection.execute(
            "SELECT * FROM yieldforge_catalog_task ORDER BY catalog_ordinal"
        ).fetchall()

    assert compute_read_model_root(rows) == COMMITTED_READ_MODEL_ROOT_SHA256


def test_import_requires_the_pinned_prepared_read_model_root(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(postgres_catalog, "COMMITTED_READ_MODEL_ROOT_SHA256", "f" * 64)

    with pytest.raises(CatalogImportError, match="read-model root"):
        _import(database_url)


def test_rejects_a_different_existing_catalog_identity(database_url: str) -> None:
    _import(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "UPDATE yieldforge_catalog SET catalog_sha256 = %s WHERE singleton = %s",
            ("f" * 64, 1),
        )

    with pytest.raises(CatalogImportError, match="different catalog identity"):
        _import(database_url)


def test_rejects_a_wrong_existing_record_hash(database_url: str) -> None:
    _import(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "UPDATE yieldforge_catalog_task SET record_sha256 = %s WHERE catalog_ordinal = %s",
            ("0" * 64, 0),
        )

    with pytest.raises(CatalogImportError, match="record hash"):
        _import(database_url)


def test_rejects_existing_summary_detail_mismatch(database_url: str) -> None:
    _import(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "UPDATE yieldforge_catalog_task "
            "SET summary_json = jsonb_set(summary_json, '{part_count}', '999'::jsonb) "
            "WHERE catalog_ordinal = %s",
            (0,),
        )

    with pytest.raises(CatalogImportError, match="summary/detail mismatch"):
        _import(database_url)


def test_rejects_problem_payload_for_an_ineligible_task(database_url: str) -> None:
    _import(database_url)
    with psycopg.connect(database_url) as connection:
        problem = connection.execute(
            "SELECT solver_problem_json FROM yieldforge_catalog_task "
            "WHERE solver_problem_json IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE yieldforge_catalog_task SET solver_problem_json = %s "
            "WHERE support_status = 'view_only' AND catalog_ordinal = "
            "(SELECT min(catalog_ordinal) FROM yieldforge_catalog_task "
            "WHERE support_status = 'view_only')",
            (json.dumps(problem),),
        )

    with pytest.raises(CatalogImportError, match="ineligible task.*solver problem"):
        _import(database_url)


def test_rejects_partial_or_unexpected_schema(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("CREATE TABLE yieldforge_catalog (unexpected integer)")

    with pytest.raises(CatalogImportError, match="partial or unexpected schema"):
        _import(database_url)


def test_rejects_an_unexpected_existing_column_type(database_url: str) -> None:
    _import(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "ALTER TABLE yieldforge_catalog ALTER COLUMN schema_version TYPE varchar(100)"
        )

    with pytest.raises(CatalogImportError, match="partial or unexpected schema"):
        _import(database_url)


@pytest.mark.parametrize(
    "mutations",
    [
        (
            "ALTER TABLE yieldforge_catalog DROP CONSTRAINT yieldforge_catalog_pkey CASCADE",
            "CREATE INDEX yieldforge_catalog_pkey ON yieldforge_catalog (singleton)",
        ),
        (
            "ALTER TABLE yieldforge_catalog_task DROP CONSTRAINT "
            "yieldforge_catalog_task_catalog_ordinal_key",
            "CREATE INDEX yieldforge_catalog_task_catalog_ordinal_key "
            "ON yieldforge_catalog_task (catalog_ordinal)",
        ),
        (
            "ALTER TABLE yieldforge_catalog_task DROP CONSTRAINT "
            "yieldforge_catalog_task_catalog_singleton_fkey",
        ),
        ("ALTER TABLE yieldforge_catalog DROP CONSTRAINT yieldforge_catalog_singleton_check",),
        (
            "DROP INDEX yieldforge_catalog_task_constraint_types_idx",
            "CREATE INDEX yieldforge_catalog_task_constraint_types_idx "
            "ON yieldforge_catalog_task (tasks_index)",
        ),
        (
            "CREATE INDEX yieldforge_catalog_task_unexpected_idx "
            "ON yieldforge_catalog_task (shape_count)",
        ),
        ("ALTER TABLE yieldforge_catalog ALTER COLUMN created_at DROP DEFAULT",),
    ],
    ids=(
        "primary-key",
        "unique-constraint",
        "foreign-key",
        "check-constraint",
        "index-definition",
        "extra-index",
        "created-at-default",
    ),
)
def test_rejects_lookalike_schema_without_exact_relational_guarantees(
    database_url: str,
    mutations: tuple[str, ...],
) -> None:
    _import(database_url)
    with psycopg.connect(database_url) as connection:
        for statement in mutations:
            connection.execute(statement)

    with pytest.raises(CatalogImportError, match="partial or unexpected schema"):
        _import(database_url)


@pytest.mark.parametrize(
    "mutations",
    [
        (
            "CREATE FUNCTION yieldforge_test_trigger() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
            "CREATE TRIGGER yieldforge_unexpected_trigger BEFORE INSERT "
            "ON yieldforge_catalog_task FOR EACH ROW "
            "EXECUTE FUNCTION yieldforge_test_trigger()",
        ),
        (
            "CREATE RULE yieldforge_unexpected_rule AS ON INSERT "
            "TO yieldforge_catalog_task DO INSTEAD NOTHING",
        ),
        ("ALTER TABLE yieldforge_catalog_task ENABLE ROW LEVEL SECURITY",),
        (
            "ALTER TABLE yieldforge_catalog_task ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE yieldforge_catalog_task FORCE ROW LEVEL SECURITY",
        ),
    ],
    ids=("trigger", "rewrite-rule", "row-security", "forced-row-security"),
)
def test_rejects_unexpected_table_execution_hooks(
    database_url: str,
    mutations: tuple[str, ...],
) -> None:
    _import(database_url)
    with psycopg.connect(database_url) as connection:
        for statement in mutations:
            connection.execute(statement)

    with pytest.raises(CatalogImportError, match="partial or unexpected schema"):
        _import(database_url)


def test_revalidates_new_rows_before_committing(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_ensure_schema = postgres_catalog._ensure_schema  # noqa: SLF001

    def ensure_then_install_mutating_trigger(connection):  # type: ignore[no-untyped-def]
        original_ensure_schema(connection)
        connection.execute(
            "CREATE FUNCTION yieldforge_test_tamper_hash() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ "
            "BEGIN NEW.record_sha256 = repeat('0', 64); RETURN NEW; END $$"
        )
        connection.execute(
            "CREATE TRIGGER yieldforge_tamper_hash BEFORE INSERT "
            "ON yieldforge_catalog_task FOR EACH ROW "
            "EXECUTE FUNCTION yieldforge_test_tamper_hash()"
        )

    monkeypatch.setattr(postgres_catalog, "_ensure_schema", ensure_then_install_mutating_trigger)

    with pytest.raises(CatalogImportError, match="record hash"):
        _import(database_url)

    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT to_regclass('yieldforge_catalog')").fetchone() == (None,)


def test_rejects_duplicate_source_keys_before_connecting(
    database_url: str,
    tmp_path: Path,
) -> None:
    decoded = json.loads(CATALOG.read_text(encoding="utf-8"))
    decoded["tasks"][1]["source_row_index"] = decoded["tasks"][0]["source_row_index"]
    tampered = tmp_path / "duplicate-source-key.json"
    tampered.write_text(
        json.dumps(decoded, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CatalogImportError, match="Invalid catalog"):
        import_catalog(
            database_url=database_url,
            catalog_path=tampered,
            catalog_manifest_path=CATALOG_MANIFEST,
            source_manifest_path=SOURCE_MANIFEST,
            audit_report_path=AUDIT_REPORT,
        )


def test_rejects_duplicate_json_keys_before_connecting(
    database_url: str,
    tmp_path: Path,
) -> None:
    payload = CATALOG.read_bytes().replace(
        b'{"constraint_value_columns":',
        b'{"schema_version":"yieldforge.normalized-slice.v1","constraint_value_columns":',
        1,
    )
    tampered = tmp_path / "duplicate-json-key.json"
    tampered.write_bytes(payload)

    with pytest.raises(CatalogImportError, match="duplicate JSON object key.*schema_version"):
        import_catalog(
            database_url=database_url,
            catalog_path=tampered,
            catalog_manifest_path=CATALOG_MANIFEST,
            source_manifest_path=SOURCE_MANIFEST,
            audit_report_path=AUDIT_REPORT,
        )
