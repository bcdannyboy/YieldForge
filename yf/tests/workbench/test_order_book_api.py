from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yieldforge.order_books.domain import LECTRA_SLICE_SHA256, GenerationRegime
from yieldforge.workbench.app import create_app
from yieldforge.workbench.order_books import (
    GenerateOrderBookInput,
    InvalidOrderBookCursorError,
    InvalidOrderBookRequestError,
    OrderBookCapacityError,
    OrderBookIntegrityError,
    OrderBookNotFoundError,
    OrderBookService,
)

FIXTURE_IDS = (
    "yfob-bf049e9141623c98654a2255",
    "yfob-c4879d4f16c0b6fe5eacc700",
    "yfob-dccfa3fa98b63b3ac6bfd322",
)
STARTS_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _service(tmp_path: Path) -> OrderBookService:
    return OrderBookService.from_repository(runtime_archive_dir=tmp_path / "order-books")


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            corpus=object(),  # type: ignore[arg-type]
            jobs=object(),  # type: ignore[arg-type]
            order_books=_service(tmp_path),
        ),
        raise_server_exceptions=False,
    )


def _request(
    *,
    regime: GenerationRegime = GenerationRegime.EXACT_RECURRENCE,
    seed: int = 7,
    event_count: int = 4,
) -> GenerateOrderBookInput:
    return GenerateOrderBookInput(
        regime=regime,
        seed=seed,
        event_count=event_count,
        starts_at=STARTS_AT,
        interval_minutes=60,
    )


def _assert_canonical_decimal(value: str) -> None:
    assert value == str(int(value))
    assert value != "-0"


def test_service_opens_all_exact_committed_fixtures_as_analysis_only_views(
    tmp_path: Path,
) -> None:
    page = _service(tmp_path).list_books(limit=50)

    assert tuple(item.order_book_id for item in page.items) == FIXTURE_IDS
    assert page.next_cursor is None
    for item in page.items:
        assert item.schema_version == "yieldforge.api-order-book.v1"
        assert item.manifest_schema_version == "yieldforge.order-book.v1"
        assert item.analysis_scope == "analysis_only_full_manifest"
        assert "baseline-facing" in item.analysis_warning
        assert item.content_sha256.startswith("sha256:")
        assert item.source_slice.content_sha256 == f"sha256:{LECTRA_SLICE_SHA256}"
        assert len(item.field_provenance) == 6
        assert tuple(event.sequence for event in item.events) == tuple(range(len(item.events)))
        for event in item.events:
            assert all(isinstance(value, str) for value in event.source_task.shape_hashes)
            for shape_hash in event.source_task.shape_hashes:
                _assert_canonical_decimal(shape_hash)


def test_service_filters_and_pages_with_a_stable_opaque_cursor(tmp_path: Path) -> None:
    service = _service(tmp_path)

    first = service.list_books(limit=1)
    assert [item.order_book_id for item in first.items] == [FIXTURE_IDS[0]]
    assert first.next_cursor is not None
    assert FIXTURE_IDS[0] not in first.next_cursor

    second = service.list_books(limit=1, cursor=first.next_cursor)
    assert [item.order_book_id for item in second.items] == [FIXTURE_IDS[1]]
    assert second.next_cursor is not None

    filtered = service.list_books(limit=50, regime=GenerationRegime.HIGH_MIX)
    assert [item.order_book_id for item in filtered.items] == [FIXTURE_IDS[2]]
    with pytest.raises(InvalidOrderBookCursorError):
        service.list_books(
            limit=1,
            cursor=first.next_cursor,
            regime=GenerationRegime.HIGH_MIX,
        )
    with pytest.raises(InvalidOrderBookCursorError):
        service.list_books(limit=1, cursor="not-a-canonical-cursor")


def test_service_detail_is_verified_and_missing_ids_are_not_paths(tmp_path: Path) -> None:
    service = _service(tmp_path)

    detail = service.detail(FIXTURE_IDS[0])
    assert detail.order_book_id == FIXTURE_IDS[0]
    with pytest.raises(OrderBookNotFoundError):
        service.detail("../../private-file")
    with pytest.raises(OrderBookNotFoundError):
        service.detail("yfob-000000000000000000000000")


def test_generation_is_write_once_idempotent_and_source_pinned(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request()

    first = service.generate(request)
    second = service.generate(request)

    assert second == first
    assert first.request.source_slice_sha256 == LECTRA_SLICE_SHA256
    assert first.request.thresholds is None
    assert first.source_slice.content_sha256 == f"sha256:{LECTRA_SLICE_SHA256}"
    assert len(tuple((tmp_path / "order-books").glob("*.json"))) == 1


def test_generation_with_a_different_seed_has_a_different_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)

    first = service.generate(_request(seed=7))
    second = service.generate(_request(seed=8))

    assert second.order_book_id != first.order_book_id
    assert second.content_sha256 != first.content_sha256
    assert len(tuple((tmp_path / "order-books").glob("*.json"))) == 2


@pytest.mark.parametrize("regime", [GenerationRegime.NO_SIGNAL, GenerationRegime.HIGH_MIX])
def test_two_task_slice_rejects_dishonest_multi_event_mix_without_writing(
    tmp_path: Path,
    regime: GenerationRegime,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(InvalidOrderBookRequestError):
        service.generate(_request(regime=regime, event_count=3))

    assert not (tmp_path / "order-books").exists()


def test_api_lists_filters_and_returns_js_safe_shape_hashes(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/order-books", params={"limit": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "yieldforge.api-order-book-page.v1"
    assert len(payload["items"]) == 2
    assert payload["next_cursor"]
    for item in payload["items"]:
        for event in item["events"]:
            assert all(isinstance(value, str) for value in event["source_task"]["shape_hashes"])

    filtered = client.get("/api/order-books", params={"regime": "high_mix"})
    assert filtered.status_code == 200
    assert [item["request"]["regime"] for item in filtered.json()["items"]] == ["high_mix"]


def test_api_detail_exposes_provenance_diagnostics_and_chronology(tmp_path: Path) -> None:
    response = _client(tmp_path).get(f"/api/order-books/{FIXTURE_IDS[0]}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_scope"] == "analysis_only_full_manifest"
    assert {item["kind"] for item in payload["field_provenance"]} == {
        "source_observed",
        "generated",
        "assumed",
    }
    assert payload["diagnostics"]["chronological_load"][0]["sequence"] == 0
    assert payload["events"][0]["occurred_at"] <= payload["events"][-1]["occurred_at"]


def test_api_generates_an_idempotent_server_pinned_manifest(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "regime": "exact_recurrence",
        "seed": 47,
        "event_count": 4,
        "starts_at": "2026-01-01T00:00:00Z",
        "interval_minutes": 30,
    }

    first = client.post("/api/order-books", json=body)
    second = client.post("/api/order-books", json=body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == first.json()
    assert first.json()["request"]["source_slice_sha256"] == LECTRA_SLICE_SHA256
    assert list((tmp_path / "order-books").glob("*.json"))


def test_api_canonicalizes_equivalent_start_instants_before_identity_generation(
    tmp_path: Path,
) -> None:
    body = {
        "regime": "exact_recurrence",
        "seed": 47,
        "event_count": 4,
        "starts_at": "2026-01-01T00:00:00Z",
        "interval_minutes": 30,
    }

    utc = _client(tmp_path / "utc").post("/api/order-books", json=body)
    offset = _client(tmp_path / "offset").post(
        "/api/order-books",
        json={**body, "starts_at": "2025-12-31T16:00:00-08:00"},
    )

    assert utc.status_code == 201
    assert offset.status_code == 201
    assert offset.json() == utc.json()


def test_api_rejects_timestamp_overflow_without_writing(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/order-books",
        json={
            "regime": "exact_recurrence",
            "seed": 47,
            "event_count": 2,
            "starts_at": "9999-12-31T23:59:59.999999Z",
            "interval_minutes": 525_600,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_order_book_request"
    assert not (tmp_path / "order-books").exists()


@pytest.mark.parametrize(
    ("patch", "expected_code"),
    [
        ({"regime": "bad_regime"}, "request_validation"),
        ({"event_count": 101}, "request_validation"),
        ({"starts_at": "2026-01-01T00:00:00"}, "request_validation"),
        ({"thresholds": {"min_unique_task_refs": 1}}, "request_validation"),
    ],
)
def test_api_rejects_invalid_public_generation_fields_without_writing(
    tmp_path: Path,
    patch: dict[str, object],
    expected_code: str,
) -> None:
    client = _client(tmp_path)
    body: dict[str, object] = {
        "regime": "exact_recurrence",
        "seed": 47,
        "event_count": 4,
        "starts_at": "2026-01-01T00:00:00Z",
        "interval_minutes": 30,
    }
    body.update(patch)

    response = client.post("/api/order-books", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == expected_code
    assert not (tmp_path / "order-books").exists()


def test_api_rejects_dishonest_mix_count_without_writing(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/order-books",
        json={
            "regime": "no_signal",
            "seed": 47,
            "event_count": 3,
            "starts_at": "2026-01-01T00:00:00Z",
            "interval_minutes": 30,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_order_book_request"
    assert not (tmp_path / "order-books").exists()


def test_api_uses_stable_not_found_and_cursor_errors(tmp_path: Path) -> None:
    client = _client(tmp_path)

    missing = client.get("/api/order-books/yfob-000000000000000000000000")
    invalid_cursor = client.get("/api/order-books", params={"cursor": "not-valid"})

    assert missing.status_code == 404
    assert missing.json()["code"] == "order_book_not_found"
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["code"] == "invalid_order_book_cursor"


def test_runtime_tampering_fails_closed_without_leaking_server_paths(tmp_path: Path) -> None:
    service = _service(tmp_path)
    detail = service.generate(_request())
    runtime_path = tmp_path / "order-books" / f"{detail.order_book_id}.json"
    runtime_path.write_text("{}\n")

    with pytest.raises(OrderBookIntegrityError):
        service.list_books(limit=50)

    response = _client_with_service(service).get("/api/order-books")
    assert response.status_code == 500
    assert response.json()["code"] == "order_book_integrity"
    assert str(tmp_path) not in response.text
    assert str(runtime_path) not in response.text


def _client_with_service(service: OrderBookService) -> TestClient:
    return TestClient(
        create_app(
            corpus=object(),  # type: ignore[arg-type]
            jobs=object(),  # type: ignore[arg-type]
            order_books=service,
        ),
        raise_server_exceptions=False,
    )


def test_runtime_symlink_fails_closed_without_following_it(tmp_path: Path) -> None:
    runtime = tmp_path / "order-books"
    runtime.mkdir()
    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"private_path": str(tmp_path)}) + "\n")
    os.symlink(target, runtime / "yfob-000000000000000000000000.json")
    service = _service(tmp_path)

    with pytest.raises(OrderBookIntegrityError):
        service.list_books(limit=50)

    response = _client_with_service(service).get("/api/order-books")
    assert response.status_code == 500
    assert response.json()["code"] == "order_book_integrity"
    assert str(target) not in response.text


def test_runtime_catalog_is_bounded_before_untrusted_files_are_read(tmp_path: Path) -> None:
    runtime = tmp_path / "order-books"
    runtime.mkdir()
    for index in range(257):
        (runtime / f"yfob-{index:024x}.json").touch()

    with pytest.raises(OrderBookIntegrityError):
        _service(tmp_path).list_books(limit=50)


def test_generation_rejects_a_full_catalog_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    fixture = (
        Path(__file__).resolve().parents[2]
        / "datasets"
        / "fixtures"
        / "order-books"
        / f"{FIXTURE_IDS[0]}.json"
    )
    monkeypatch.setattr(service, "_runtime_paths", lambda: (fixture,) * 253)

    with pytest.raises(OrderBookCapacityError):
        service.generate(_request(seed=99))

    assert not (tmp_path / "order-books").exists()


def test_api_reports_full_catalog_capacity_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    fixture = (
        Path(__file__).resolve().parents[2]
        / "datasets"
        / "fixtures"
        / "order-books"
        / f"{FIXTURE_IDS[0]}.json"
    )
    monkeypatch.setattr(service, "_runtime_paths", lambda: (fixture,) * 253)

    response = _client_with_service(service).post(
        "/api/order-books",
        json={
            "regime": "exact_recurrence",
            "seed": 99,
            "event_count": 4,
            "starts_at": "2026-01-01T00:00:00Z",
            "interval_minutes": 60,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "order_book_catalog_full"
    assert not (tmp_path / "order-books").exists()
