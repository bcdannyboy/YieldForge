from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.datasets import corpus
from yieldforge.datasets.corpus import (
    CorpusQueryService,
    InvalidCursorError,
    InvalidTaskQueryError,
    TaskNotFoundError,
    TaskNotSolvableError,
)
from yieldforge.datasets.normalized_slice import SupportStatus
from yieldforge.datasets.passive_report import parse_normalized_slice

YF_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_SLICE = YF_ROOT / "datasets/fixtures/lectra-representative-slice.json"
COMMITTED_MANIFEST = YF_ROOT / "datasets/sources/lectra-7030786-v1.1.json"


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CorpusQueryService:
    slice_path = tmp_path / "slice.json"
    report_path = tmp_path / "audit.json"
    manifest_path = tmp_path / "manifest.json"
    slice_path.write_bytes(COMMITTED_SLICE.read_bytes())
    report_path.write_text("bound-audit-placeholder", encoding="utf-8")
    manifest_path.write_text("bound-manifest-placeholder", encoding="utf-8")
    normalized = parse_normalized_slice(slice_path.read_bytes())
    calls: list[tuple[Path, Path, Path]] = []

    def bound_loader(
        received_slice: Path,
        received_report: Path,
        received_manifest: Path,
    ) -> tuple[object, object, object]:
        calls.append((received_slice, received_report, received_manifest))
        return normalized, object(), object()

    monkeypatch.setattr(corpus, "load_normalized_slice_evidence", bound_loader)
    result = CorpusQueryService.load_bound(
        slice_path=slice_path,
        report_path=report_path,
        manifest_path=manifest_path,
    )
    assert calls == [(slice_path, report_path, manifest_path)]
    return result


def test_summary_reports_bound_source_identity_and_truthful_counts(
    service: CorpusQueryService,
) -> None:
    summary = service.summary()

    assert summary.schema_version == "yieldforge.corpus-summary.v1"
    assert summary.source.dataset_id == "lectra-7030786-v1.1"
    assert summary.source.slice_sha256 == hashlib.sha256(COMMITTED_SLICE.read_bytes()).hexdigest()
    assert summary.source.evidence_status == "fully_bound_to_local_audit_evidence"
    assert summary.coordinate_unit.literal_label == "m^-4"
    assert summary.coordinate_unit.interpretation is None
    assert (summary.task_count, summary.part_count, summary.shape_count) == (2, 66, 19)
    assert summary.constraint_count == 86
    assert {entry.name: entry.count for entry in summary.constraint_type_counts} == {
        "c8": 20,
        "s1": 66,
    }
    assert {entry.name: entry.count for entry in summary.support_status_counts} == {
        "runnable_with_explicit_assumptions": 1,
        "view_only": 1,
    }
    assert summary.solve_capability.eligible_task_count == 1
    assert summary.solve_capability.blocked_task_count == 1
    assert summary.solve_capability.directly_supported_task_count == 0


def test_task_pages_are_stably_sorted_and_cursor_bound_to_filters(
    service: CorpusQueryService,
) -> None:
    first = service.list_tasks(limit=1)
    assert [item.tasks_index for item in first.items] == [13958]
    assert first.next_cursor is not None

    repeated = service.list_tasks(limit=1)
    assert repeated.next_cursor == first.next_cursor
    second = service.list_tasks(limit=1, cursor=first.next_cursor)
    assert [item.tasks_index for item in second.items] == [25801]
    assert second.next_cursor is None

    assert first.next_cursor is not None
    changed = first.next_cursor[:-1] + ("A" if first.next_cursor[-1] != "A" else "B")
    with pytest.raises(InvalidCursorError):
        service.list_tasks(limit=1, cursor=changed)
    with pytest.raises(InvalidCursorError, match="filters"):
        service.list_tasks(
            limit=1,
            cursor=first.next_cursor,
            status=SupportStatus.VIEW_ONLY,
        )

    forged_member = service._encode_cursor(  # noqa: SLF001 - adversarial contract probe
        after=(999, 999),
        filter_digest=service._filter_digest(  # noqa: SLF001
            status=None,
            constraint_type=None,
            task_id=None,
            min_parts=None,
            max_parts=None,
        ),
    )
    with pytest.raises(InvalidCursorError, match="member"):
        service.list_tasks(limit=1, cursor=forged_member)


def test_default_repository_load_is_content_pinned_and_needs_no_ignored_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slice_path = tmp_path / "lectra-representative-slice.json"
    manifest_path = tmp_path / "lectra-manifest.json"
    missing_audit_path = tmp_path / "audit-is-intentionally-absent.json"
    slice_path.write_bytes(COMMITTED_SLICE.read_bytes())
    manifest_path.write_bytes(COMMITTED_MANIFEST.read_bytes())
    monkeypatch.setattr(corpus, "COMMITTED_SLICE_PATH", slice_path)
    monkeypatch.setattr(corpus, "COMMITTED_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(corpus, "BOUND_AUDIT_REPORT_PATH", missing_audit_path)

    loaded = CorpusQueryService.from_repository()

    assert not missing_audit_path.exists()
    assert loaded.summary().source.evidence_status == "content_pinned_with_manifest_identity"
    assert loaded.task_detail(13958).summary.solve_capability.can_solve is True


def test_default_repository_load_rejects_unpinned_slice_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slice_path = tmp_path / "lectra-representative-slice.json"
    manifest_path = tmp_path / "lectra-manifest.json"
    slice_path.write_bytes(COMMITTED_SLICE.read_bytes() + b" ")
    manifest_path.write_bytes(COMMITTED_MANIFEST.read_bytes())
    monkeypatch.setattr(corpus, "COMMITTED_SLICE_PATH", slice_path)
    monkeypatch.setattr(corpus, "COMMITTED_MANIFEST_PATH", manifest_path)

    with pytest.raises(ValueError, match="pinned content hash"):
        CorpusQueryService.from_repository()


def test_task_filters_are_bounded_and_composable(service: CorpusQueryService) -> None:
    assert [item.tasks_index for item in service.list_tasks(status="view_only").items] == [25801]
    assert [item.tasks_index for item in service.list_tasks(constraint_type="c8").items] == [25801]
    assert [item.tasks_index for item in service.list_tasks(constraint_type="s1").items] == [
        13958,
        25801,
    ]
    assert [item.tasks_index for item in service.list_tasks(task_id=13958).items] == [13958]
    assert [item.tasks_index for item in service.list_tasks(min_parts=33).items] == [13958]
    assert [item.tasks_index for item in service.list_tasks(max_parts=33).items] == [25801]
    assert service.list_tasks(task_id=999).items == ()

    for limit in (0, 51, True):
        with pytest.raises(InvalidTaskQueryError):
            service.list_tasks(limit=limit)  # type: ignore[arg-type]
    with pytest.raises(InvalidTaskQueryError, match="min_parts"):
        service.list_tasks(min_parts=40, max_parts=20)
    with pytest.raises(InvalidTaskQueryError, match="constraint_type"):
        service.list_tasks(constraint_type=" ")
    with pytest.raises(InvalidTaskQueryError, match="status"):
        service.list_tasks(status="invented")  # type: ignore[arg-type]
    with pytest.raises(InvalidTaskQueryError, match="browser-safe"):
        service.list_tasks(task_id=2**53)


def test_task_items_expose_authoritative_solve_capability(
    service: CorpusQueryService,
) -> None:
    runnable, blocked = service.list_tasks().items

    assert runnable.solve_capability.can_solve is True
    assert runnable.solve_capability.requires_assumption_acknowledgement is True
    assert runnable.solve_capability.assumption_codes == (
        "interpret_s1_degenerate_entries_as_allowed_rotations",
    )
    assert runnable.solve_capability.reason_codes == ()

    assert blocked.solve_capability.can_solve is False
    assert blocked.solve_capability.requires_assumption_acknowledgement is False
    assert blocked.solve_capability.assumption_codes == ()
    assert blocked.solve_capability.reason_codes == ("contains_non_s1_constraints",)


def test_task_detail_is_exact_task_scoped_passive_data(service: CorpusQueryService) -> None:
    runnable = service.task_detail(13958)
    blocked = service.task_detail(25801)

    assert runnable.summary.part_count == len(runnable.parts) == 34
    assert runnable.summary.constraint_count == len(runnable.constraints) == 34
    assert len(runnable.shapes) == len(runnable.derived_geometry) == 8
    assert {part.shape_hash for part in runnable.parts} == {
        shape.shape_hash for shape in runnable.shapes
    }
    assert blocked.summary.part_count == len(blocked.parts) == 32
    assert blocked.summary.constraint_types == ("c8", "s1")
    assert len(blocked.constraints) == 52
    assert len(blocked.shapes) == len(blocked.derived_geometry) == 11
    assert blocked.constraint_value_columns[0] == "parts_1"
    assert blocked.provenance

    with pytest.raises(TaskNotFoundError):
        service.task_detail(999)


def test_dtos_serialize_hashes_and_opaque_integers_without_js_number_loss(
    service: CorpusQueryService,
) -> None:
    detail = service.task_detail(13958)
    encoded = detail.model_dump_json()
    decoded = json.loads(encoded)

    assert all(isinstance(part["shape_hash"], str) for part in decoded["parts"])
    assert all(isinstance(shape["shape_hash"], str) for shape in decoded["shapes"])
    assert all(isinstance(geometry["shape_hash"], str) for geometry in decoded["derived_geometry"])
    integer_values = [
        item["value"]
        for constraint in decoded["constraints"]
        for value in constraint["values"]
        if value["kind"] == "sequence"
        for item in value["items"]
        if item["kind"] == "integer"
    ]
    assert integer_values
    assert all(isinstance(value, str) for value in integer_values)
    assert '"shape_hash":-8727500516347896752' not in encoded
    assert '"shape_hash":"-8727500516347896752"' in encoded


def test_projection_requires_exact_acknowledgement_and_refuses_blocked_task(
    service: CorpusQueryService,
) -> None:
    required = ("interpret_s1_degenerate_entries_as_allowed_rotations",)

    with pytest.raises(TaskNotSolvableError, match="acknowledge"):
        service.project_problem(13958, acknowledged_assumption_codes=())
    with pytest.raises(TaskNotSolvableError, match="exact"):
        service.project_problem(
            13958,
            acknowledged_assumption_codes=required + ("invented",),
        )
    problem = service.project_problem(
        13958,
        acknowledged_assumption_codes=required,
    )
    assert problem.name == "lectra-task-13958"
    assert len(problem.parts) == 34

    with pytest.raises(TaskNotSolvableError, match="blocked"):
        service.project_problem(25801, acknowledged_assumption_codes=())
    with pytest.raises(TaskNotFoundError):
        service.project_problem(999, acknowledged_assumption_codes=())


def test_dtos_are_strict_frozen_finite_and_do_not_leak_server_paths(
    service: CorpusQueryService,
) -> None:
    summary = service.summary()
    payload = summary.model_dump()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        type(summary).model_validate(payload)
    with pytest.raises(ValidationError):
        summary.task_count = 100  # type: ignore[misc]

    serialized = service.task_detail(13958).model_dump_json()
    assert str(YF_ROOT) not in serialized
    assert "/var/data/" not in serialized
    assert "NaN" not in serialized
    assert "Infinity" not in serialized


def test_corpus_import_does_not_load_pandas_or_pickle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import yieldforge.datasets.corpus; "
            "assert 'pandas' not in sys.modules; assert 'pickle' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
