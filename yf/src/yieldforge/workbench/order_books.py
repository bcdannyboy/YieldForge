"""Bounded query and generation service for immutable order-book manifests."""

from __future__ import annotations

import base64
import binascii
import json
import re
from pathlib import Path

from yieldforge.order_books.archive import (
    ArchiveCollisionError,
    ArchiveIntegrityError,
    read_manifest,
    write_manifest,
)
from yieldforge.order_books.domain import (
    LECTRA_SLICE_SHA256,
    GenerationRegime,
    GenerationRequest,
    OrderBookManifest,
    canonical_json_bytes,
)
from yieldforge.order_books.generator import COMMITTED_SLICE_PATH, generate_order_book
from yieldforge.workbench.api_contracts import (
    GenerateOrderBookInput,
    OrderBookPage,
    OrderBookView,
)

_ORDER_BOOK_ID_PATTERN = re.compile(r"^yfob-[0-9a-f]{24}$")
_EXPECTED_FIXTURE_NAMES = (
    "yfob-bf049e9141623c98654a2255.json",
    "yfob-c4879d4f16c0b6fe5eacc700.json",
    "yfob-dccfa3fa98b63b3ac6bfd322.json",
)
_MAX_CATALOG_FILES = 256
_MAX_PAGE_SIZE = 50
_MAX_PUBLIC_EVENTS = 100


class InvalidOrderBookCursorError(ValueError):
    """A list cursor was malformed, stale, or used with different filters."""


class InvalidOrderBookRequestError(ValueError):
    """A generation request exceeded the honest capability of the current slice."""


class OrderBookNotFoundError(KeyError):
    """No verified catalog entry has the requested immutable identity."""


class OrderBookIntegrityError(RuntimeError):
    """At least one server-owned catalog artifact failed closed validation."""


class OrderBookCapacityError(RuntimeError):
    """The valid bounded catalog cannot accept another immutable archive."""


class OrderBookService:
    """Validate, page, and extend a bounded immutable order-book catalog."""

    def __init__(
        self,
        *,
        fixture_paths: tuple[Path, ...],
        runtime_archive_dir: Path,
    ) -> None:
        if tuple(path.name for path in fixture_paths) != _EXPECTED_FIXTURE_NAMES:
            raise ValueError("the committed order-book fixture set is not exact")
        self._fixture_paths = fixture_paths
        self._runtime_archive_dir = runtime_archive_dir

    @classmethod
    def from_repository(cls, *, runtime_archive_dir: Path) -> OrderBookService:
        project_root = Path(__file__).resolve().parents[3]
        fixture_dir = project_root / "datasets/fixtures/order-books"
        return cls(
            fixture_paths=tuple(fixture_dir / name for name in _EXPECTED_FIXTURE_NAMES),
            runtime_archive_dir=runtime_archive_dir,
        )

    def _runtime_paths(self) -> tuple[Path, ...]:
        directory = self._runtime_archive_dir
        try:
            if directory.is_symlink():
                raise OrderBookIntegrityError("order-book catalog failed integrity validation")
            if not directory.exists():
                return ()
            if not directory.is_dir():
                raise OrderBookIntegrityError("order-book catalog failed integrity validation")
            entries = tuple(directory.iterdir())
        except OSError as error:
            raise OrderBookIntegrityError(
                "order-book catalog failed integrity validation"
            ) from error
        if len(entries) + len(self._fixture_paths) > _MAX_CATALOG_FILES:
            raise OrderBookIntegrityError("order-book catalog exceeds its file limit")
        if any(path.name.startswith(".") or path.suffix != ".json" for path in entries):
            raise OrderBookIntegrityError("order-book catalog failed integrity validation")
        return tuple(sorted(entries, key=lambda path: path.name))

    @staticmethod
    def _read(path: Path) -> OrderBookManifest:
        try:
            manifest = read_manifest(path)
        except (ArchiveIntegrityError, OSError, ValueError) as error:
            raise OrderBookIntegrityError(
                "order-book catalog failed integrity validation"
            ) from error
        if len(manifest.events) > _MAX_PUBLIC_EVENTS:
            raise OrderBookIntegrityError("order-book manifest exceeds the response event limit")
        return manifest

    def _catalog(self) -> tuple[OrderBookManifest, ...]:
        manifests: dict[str, OrderBookManifest] = {}
        for path in (*self._fixture_paths, *self._runtime_paths()):
            manifest = self._read(path)
            existing = manifests.get(manifest.order_book_id)
            if existing is not None and existing != manifest:
                raise OrderBookIntegrityError("order-book identity collision in catalog")
            manifests[manifest.order_book_id] = manifest
        return tuple(manifests[key] for key in sorted(manifests))

    @staticmethod
    def _cursor_payload(order_book_id: str, regime: GenerationRegime | None) -> bytes:
        return canonical_json_bytes(
            {
                "after": order_book_id,
                "regime": regime.value if regime is not None else None,
                "v": 1,
            }
        )

    @classmethod
    def _encode_cursor(
        cls,
        order_book_id: str,
        regime: GenerationRegime | None,
    ) -> str:
        return (
            base64.urlsafe_b64encode(cls._cursor_payload(order_book_id, regime))
            .decode()
            .rstrip("=")
        )

    @classmethod
    def _decode_cursor(
        cls,
        cursor: str,
        regime: GenerationRegime | None,
    ) -> str:
        if not cursor or len(cursor) > 512 or not cursor.isascii():
            raise InvalidOrderBookCursorError("order-book cursor was rejected")
        padding = "=" * (-len(cursor) % 4)
        try:
            raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
            payload = json.loads(raw)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidOrderBookCursorError("order-book cursor was rejected") from error
        expected_regime = regime.value if regime is not None else None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"after", "regime", "v"}
            or payload.get("v") != 1
            or payload.get("regime") != expected_regime
            or not isinstance(payload.get("after"), str)
            or _ORDER_BOOK_ID_PATTERN.fullmatch(payload["after"]) is None
            or cls._encode_cursor(payload["after"], regime) != cursor
        ):
            raise InvalidOrderBookCursorError("order-book cursor was rejected")
        return payload["after"]

    def list_books(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        regime: GenerationRegime | None = None,
    ) -> OrderBookPage:
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise InvalidOrderBookRequestError("order-book page size was rejected")
        manifests = tuple(
            manifest
            for manifest in self._catalog()
            if regime is None or manifest.request.regime is regime
        )
        start = 0
        if cursor is not None:
            after = self._decode_cursor(cursor, regime)
            identifiers = tuple(manifest.order_book_id for manifest in manifests)
            try:
                start = identifiers.index(after) + 1
            except ValueError as error:
                raise InvalidOrderBookCursorError("order-book cursor was rejected") from error
        selected = manifests[start : start + limit]
        next_cursor = None
        if start + limit < len(manifests):
            next_cursor = self._encode_cursor(selected[-1].order_book_id, regime)
        return OrderBookPage(
            items=tuple(OrderBookView.from_manifest(manifest) for manifest in selected),
            next_cursor=next_cursor,
        )

    def detail(self, order_book_id: str) -> OrderBookView:
        if _ORDER_BOOK_ID_PATTERN.fullmatch(order_book_id) is None:
            raise OrderBookNotFoundError(order_book_id)
        for manifest in self._catalog():
            if manifest.order_book_id == order_book_id:
                return OrderBookView.from_manifest(manifest)
        raise OrderBookNotFoundError(order_book_id)

    @staticmethod
    def _require_supported_request(body: GenerateOrderBookInput) -> None:
        if body.regime in (GenerationRegime.NO_SIGNAL, GenerationRegime.HIGH_MIX):
            if body.event_count != 2:
                raise InvalidOrderBookRequestError(
                    "the current two-task slice supports exactly two events for this regime"
                )
        elif not 2 <= body.event_count <= _MAX_PUBLIC_EVENTS:
            raise InvalidOrderBookRequestError("order-book event count was rejected")

    def generate(self, body: GenerateOrderBookInput) -> OrderBookView:
        self._require_supported_request(body)
        request = GenerationRequest(
            regime=body.regime,
            seed=body.seed,
            event_count=body.event_count,
            starts_at=body.starts_at,
            interval_minutes=body.interval_minutes,
            source_slice_sha256=LECTRA_SLICE_SHA256,
            thresholds=None,
        )
        catalog = self._catalog()
        for existing in catalog:
            if existing.request == request:
                return OrderBookView.from_manifest(existing)
        if len(self._runtime_paths()) + len(self._fixture_paths) >= _MAX_CATALOG_FILES:
            raise OrderBookCapacityError("order-book catalog has no capacity for a new archive")
        try:
            manifest = generate_order_book(request, COMMITTED_SLICE_PATH)
        except (OverflowError, ValueError) as error:
            raise InvalidOrderBookRequestError("order-book generation was rejected") from error
        existing_by_id = {item.order_book_id: item for item in catalog}
        collision = existing_by_id.get(manifest.order_book_id)
        if collision is not None:
            if collision != manifest:
                raise OrderBookIntegrityError("order-book identity collision in catalog")
            return OrderBookView.from_manifest(collision)
        try:
            published = write_manifest(manifest, self._runtime_archive_dir)
        except ArchiveCollisionError:
            published = self._runtime_archive_dir / f"{manifest.order_book_id}.json"
        except (ArchiveIntegrityError, OSError, ValueError) as error:
            raise OrderBookIntegrityError(
                "order-book generation archive failed integrity validation"
            ) from error
        persisted = self._read(published)
        if persisted != manifest:
            raise OrderBookIntegrityError("persisted order book differs from generated manifest")
        return OrderBookView.from_manifest(persisted)


__all__ = [
    "GenerateOrderBookInput",
    "InvalidOrderBookCursorError",
    "InvalidOrderBookRequestError",
    "OrderBookCapacityError",
    "OrderBookIntegrityError",
    "OrderBookNotFoundError",
    "OrderBookService",
]
