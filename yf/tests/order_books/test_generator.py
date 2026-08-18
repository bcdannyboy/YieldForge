import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.order_books.archive import (
    ArchiveCollisionError,
    ArchiveIntegrityError,
    read_manifest,
    verify_manifest_file,
    write_manifest,
)
from yieldforge.order_books.domain import (
    BaselineOrderBookView,
    FieldFamily,
    GenerationRegime,
    GenerationRequest,
    OrderBookManifest,
    ProvenanceKind,
    RegimeThresholds,
    calculate_diagnostics,
    canonical_json_bytes,
    expected_event_id,
    manifest_content_sha256,
    resolve_regime_thresholds,
)
from yieldforge.order_books.generator import (
    DEFAULT_GENERATOR,
    baseline_view,
    generate_order_book,
    oracle_view,
    slice_sha256,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SLICE = REPOSITORY_ROOT / "datasets/fixtures/lectra-representative-slice.json"


def request_for(
    regime: GenerationRegime,
    *,
    seed: int = 41,
    event_count: int = 8,
    thresholds: RegimeThresholds | None = None,
) -> GenerationRequest:
    defaults = {
        GenerationRegime.NO_SIGNAL: RegimeThresholds(
            min_unique_task_refs=2,
            max_task_concentration=0.50,
            max_shape_recurrence=0.0,
        ),
        GenerationRegime.EXACT_RECURRENCE: RegimeThresholds(
            min_shape_recurrence=1.0,
            min_task_concentration=1.0,
        ),
        GenerationRegime.HIGH_MIX: RegimeThresholds(
            min_unique_task_refs=2,
            max_task_concentration=0.50,
            max_shape_recurrence=0.25,
        ),
    }
    return GenerationRequest(
        regime=regime,
        seed=seed,
        event_count=event_count,
        starts_at=datetime(2026, 1, 1, tzinfo=UTC),
        interval_minutes=60,
        source_slice_sha256=slice_sha256(SLICE),
        thresholds=thresholds or defaults[regime],
    )


@pytest.mark.parametrize(
    ("regime", "event_count", "expected_unique", "max_concentration", "recurrence"),
    [
        (GenerationRegime.NO_SIGNAL, 2, 2, 0.50, 0.0),
        (GenerationRegime.EXACT_RECURRENCE, 8, 1, 1.0, 1.0),
        (GenerationRegime.HIGH_MIX, 2, 2, 0.50, 0.0),
    ],
)
def test_generator_realizes_declared_regimes_with_auditable_diagnostics(
    regime: GenerationRegime,
    event_count: int,
    expected_unique: int,
    max_concentration: float,
    recurrence: float,
) -> None:
    manifest = generate_order_book(request_for(regime, event_count=event_count), SLICE)

    assert manifest.schema_version == "yieldforge.order-book.v1"
    assert manifest.generator == DEFAULT_GENERATOR
    assert len(manifest.events) == event_count
    assert manifest.diagnostics.unique_task_ref_count == expected_unique
    assert manifest.diagnostics.max_task_concentration <= max_concentration
    assert manifest.diagnostics.shape_recurrence == recurrence
    assert manifest.diagnostics.task_sizes.event_count == event_count
    assert len(manifest.diagnostics.chronological_load) == event_count
    assert [load.sequence for load in manifest.diagnostics.chronological_load] == list(
        range(event_count)
    )
    assert manifest.content_sha256.startswith("sha256:")
    assert manifest.order_book_id == f"yfob-{manifest.content_sha256[7:31]}"


def test_source_geometry_and_composition_references_match_the_normalized_slice() -> None:
    source = json.loads(SLICE.read_text())
    expected = {
        task["tasks_index"]: {
            "source_row_index": task["source_row_index"],
            "part_source_row_indices": tuple(
                part["source_row_index"]
                for part in source["parts"]
                if part["tasks_index"] == task["tasks_index"]
            ),
            "shape_hashes": tuple(
                part["shape_hash"]
                for part in source["parts"]
                if part["tasks_index"] == task["tasks_index"]
            ),
        }
        for task in source["tasks"]
    }
    manifest = generate_order_book(
        request_for(GenerationRegime.HIGH_MIX, event_count=2),
        SLICE,
    )

    for event in manifest.events:
        observed = expected[event.source_task.tasks_index]
        assert event.source_task.task_source_row_index == observed["source_row_index"]
        assert event.source_task.part_source_row_indices == observed["part_source_row_indices"]
        assert event.source_task.shape_hashes == observed["shape_hashes"]

    provenance = {record.family: record.kind for record in manifest.field_provenance}
    assert provenance[FieldFamily.GEOMETRY] is ProvenanceKind.SOURCE_OBSERVED
    assert provenance[FieldFamily.COMPOSITION] is ProvenanceKind.SOURCE_OBSERVED
    assert provenance[FieldFamily.CHRONOLOGY] is ProvenanceKind.GENERATED
    assert provenance[FieldFamily.MATERIAL] is ProvenanceKind.ASSUMED
    assert provenance[FieldFamily.ECONOMICS] is ProvenanceKind.GENERATED
    assert provenance[FieldFamily.REGIME_LABEL] is ProvenanceKind.GENERATED


def test_tasks_index_is_not_used_as_chronology() -> None:
    manifest = generate_order_book(
        request_for(GenerationRegime.HIGH_MIX, event_count=2),
        SLICE,
    )

    assert [event.occurred_at for event in manifest.events] == [
        manifest.request.starts_at + timedelta(minutes=60 * sequence) for sequence in range(8)
    ][:2]
    assert all(event.occurred_at.year == 2026 for event in manifest.events)
    assert {event.source_task.tasks_index for event in manifest.events} == {13958, 25801}


def test_same_request_is_canonical_and_different_seed_diverges() -> None:
    first = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, seed=4, event_count=2),
        SLICE,
    )
    repeated = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, seed=4, event_count=2),
        SLICE,
    )
    changed = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, seed=5, event_count=2),
        SLICE,
    )

    assert first == repeated
    assert first.model_dump_json() == repeated.model_dump_json()
    assert first.content_sha256 == repeated.content_sha256
    assert first.order_book_id == repeated.order_book_id
    assert changed.content_sha256 != first.content_sha256
    assert changed.order_book_id != first.order_book_id


def test_generator_rejects_realized_output_that_misses_declared_thresholds() -> None:
    impossible = RegimeThresholds(
        min_unique_task_refs=3,
        max_shape_recurrence=0.25,
    )

    with pytest.raises(ValueError, match="min_unique_task_refs"):
        generate_order_book(
            request_for(
                GenerationRegime.HIGH_MIX,
                event_count=2,
                thresholds=impossible,
            ),
            SLICE,
        )


def test_generator_enforces_declared_load_and_task_size_bounds() -> None:
    impossible = RegimeThresholds(
        max_total_part_references=1,
        max_mean_task_parts=1.0,
    )

    with pytest.raises(ValueError, match="max_total_part_references|max_mean_task_parts"):
        generate_order_book(
            request_for(
                GenerationRegime.HIGH_MIX,
                event_count=2,
                thresholds=impossible,
            ),
            SLICE,
        )


def test_no_signal_rejects_repeated_source_compositions() -> None:
    with pytest.raises(ValueError, match="max_shape_recurrence"):
        generate_order_book(
            request_for(GenerationRegime.NO_SIGNAL, event_count=8),
            SLICE,
        )


def test_generator_accepts_only_the_pinned_committed_slice(tmp_path: Path) -> None:
    lookalike = tmp_path / "lookalike.json"
    shutil.copyfile(SLICE, lookalike)

    with pytest.raises(ValueError, match="committed normalized slice"):
        generate_order_book(
            request_for(GenerationRegime.HIGH_MIX, event_count=2),
            lookalike,
        )


def test_baseline_view_is_as_of_safe_and_hides_generator_only_regime() -> None:
    manifest = generate_order_book(
        request_for(GenerationRegime.HIGH_MIX, event_count=2),
        SLICE,
    )
    as_of = datetime(2026, 1, 1, 2, 30, tzinfo=UTC)

    baseline = baseline_view(manifest, as_of=as_of)
    oracle = oracle_view(manifest)

    assert [event.sequence for event in baseline.events] == [0, 1]
    assert all(event.occurred_at <= as_of for event in baseline.events)
    assert "regime" not in baseline.model_dump(mode="json")
    assert "regime_label" not in json.dumps(baseline.model_dump(mode="json"))
    assert manifest.order_book_id not in json.dumps(baseline.model_dump(mode="json"))
    assert oracle.regime is GenerationRegime.HIGH_MIX
    assert len(oracle.events) == 2
    assert oracle.diagnostics == manifest.diagnostics


def test_baseline_contract_rejects_future_events_and_regime_provenance() -> None:
    manifest = generate_order_book(request_for(GenerationRegime.EXACT_RECURRENCE), SLICE)
    baseline = baseline_view(
        manifest,
        as_of=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
    )
    payload = baseline.model_dump(mode="json")
    payload["events"] = [event.model_dump(mode="json") for event in manifest.events[:2]]

    with pytest.raises(ValidationError, match="future events"):
        BaselineOrderBookView.model_validate_json(json.dumps(payload))

    payload = baseline.model_dump(mode="json")
    regime_provenance = next(
        record for record in manifest.field_provenance if record.family is FieldFamily.REGIME_LABEL
    )
    payload["field_provenance"].append(regime_provenance.model_dump(mode="json"))
    with pytest.raises(ValidationError, match="regime provenance"):
        BaselineOrderBookView.model_validate_json(json.dumps(payload))


def test_archive_is_canonical_write_once_and_detects_tampering(tmp_path: Path) -> None:
    manifest = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, event_count=2),
        SLICE,
    )
    path = write_manifest(manifest, tmp_path)

    assert path.name == f"{manifest.order_book_id}.json"
    assert verify_manifest_file(path) == manifest.content_sha256
    assert read_manifest(path) == manifest
    with pytest.raises(ArchiveCollisionError, match="already exists"):
        write_manifest(manifest, tmp_path)

    payload = json.loads(path.read_text())
    payload["events"][0]["economics"]["priority_score"] = 0.999
    path.write_text(json.dumps(payload))
    with pytest.raises(
        (ArchiveIntegrityError, ValidationError),
        match="event ID|content hash",
    ):
        read_manifest(path)


def test_archive_rejects_noncanonical_encoding(tmp_path: Path) -> None:
    manifest = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, event_count=2),
        SLICE,
    )
    path = write_manifest(manifest, tmp_path)
    path.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2))

    with pytest.raises(ArchiveIntegrityError, match="canonical"):
        read_manifest(path)


def test_archive_revalidates_model_copy_bypasses_before_publication(tmp_path: Path) -> None:
    manifest = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, event_count=2),
        SLICE,
    )
    forged = manifest.model_copy(update={"events": manifest.events[:1]})
    forged_hash = manifest_content_sha256(forged)
    forged = forged.model_copy(
        update={
            "content_sha256": forged_hash,
            "order_book_id": f"yfob-{forged_hash[7:31]}",
        }
    )

    with pytest.raises(ArchiveIntegrityError, match="validation"):
        write_manifest(forged, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_archive_binds_observed_task_references_to_the_pinned_slice(tmp_path: Path) -> None:
    manifest = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, event_count=2),
        SLICE,
    )
    payload = manifest.model_dump(mode="json")
    payload["events"][0]["source_task"]["part_ids"][0] = 999_999_999
    _rehash_payload(payload)
    forged = OrderBookManifest.model_validate_json(json.dumps(payload))

    with pytest.raises(ArchiveIntegrityError, match="pinned normalized slice"):
        write_manifest(forged, tmp_path)
    assert list(tmp_path.iterdir()) == []

    path = tmp_path / f"{forged.order_book_id}.json"
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    with pytest.raises(ArchiveIntegrityError, match="pinned normalized slice"):
        read_manifest(path)


def test_archive_replays_generator_to_reject_alternate_self_consistent_book(
    tmp_path: Path,
) -> None:
    canonical = generate_order_book(
        request_for(GenerationRegime.HIGH_MIX, event_count=2),
        SLICE,
    )
    swapped_references = tuple(reversed([event.source_task for event in canonical.events]))
    alternate_events = []
    for event, source_task in zip(canonical.events, swapped_references, strict=True):
        changed = event.model_copy(update={"source_task": source_task})
        alternate_events.append(
            changed.model_copy(
                update={
                    "event_id": expected_event_id(
                        canonical.request,
                        changed.sequence,
                        changed.source_task,
                        changed.material,
                        changed.economics,
                    )
                }
            )
        )
    events = tuple(alternate_events)
    diagnostics = calculate_diagnostics(
        events,
        resolve_regime_thresholds(canonical.request),
    )
    alternate = canonical.model_copy(update={"events": events, "diagnostics": diagnostics})
    alternate_hash = manifest_content_sha256(alternate)
    alternate = alternate.model_copy(
        update={
            "content_sha256": alternate_hash,
            "order_book_id": f"yfob-{alternate_hash[7:31]}",
        }
    )
    alternate = OrderBookManifest.model_validate_json(
        canonical_json_bytes(alternate.model_dump(mode="json"))
    )

    assert alternate.request == canonical.request
    assert alternate.generator == canonical.generator
    assert alternate != canonical
    with pytest.raises(ArchiveIntegrityError, match="deterministic generator replay"):
        write_manifest(alternate, tmp_path)
    assert list(tmp_path.iterdir()) == []

    path = tmp_path / f"{alternate.order_book_id}.json"
    path.write_bytes(canonical_json_bytes(alternate.model_dump(mode="json")) + b"\n")
    with pytest.raises(ArchiveIntegrityError, match="deterministic generator replay"):
        read_manifest(path)


def test_archive_rejects_symlink_destination(tmp_path: Path) -> None:
    manifest = generate_order_book(
        request_for(GenerationRegime.NO_SIGNAL, event_count=2),
        SLICE,
    )
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ArchiveIntegrityError, match="symlink"):
        write_manifest(manifest, linked)


def _rehash_payload(payload: dict[str, object]) -> None:
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"content_sha256", "order_book_id"}
    }
    digest = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    payload["content_sha256"] = f"sha256:{digest}"
    payload["order_book_id"] = f"yfob-{digest[:24]}"


def test_manifest_rejects_self_hashed_chronology_that_ignores_requested_interval() -> None:
    manifest = generate_order_book(request_for(GenerationRegime.EXACT_RECURRENCE), SLICE)
    payload = manifest.model_dump(mode="json")
    for event in payload["events"]:
        changed = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=30 * event["sequence"])
        event["occurred_at"] = changed.isoformat().replace("+00:00", "Z")
    for load in payload["diagnostics"]["chronological_load"]:
        changed = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=30 * load["sequence"])
        load["occurred_at"] = changed.isoformat().replace("+00:00", "Z")
    _rehash_payload(payload)

    with pytest.raises(ValidationError, match="requested interval"):
        OrderBookManifest.model_validate_json(json.dumps(payload))


def test_manifest_rejects_self_hashed_diagnostics_inconsistent_with_events() -> None:
    manifest = generate_order_book(request_for(GenerationRegime.EXACT_RECURRENCE), SLICE)
    payload = manifest.model_dump(mode="json")
    payload["diagnostics"]["unique_task_ref_count"] = 2
    _rehash_payload(payload)

    with pytest.raises(ValidationError, match="diagnostics"):
        OrderBookManifest.model_validate_json(json.dumps(payload))


def test_committed_tiny_fixtures_cover_each_regime_and_are_hand_inspectable() -> None:
    paths = sorted((REPOSITORY_ROOT / "datasets/fixtures/order-books").glob("yfob-*.json"))

    manifests = [read_manifest(path) for path in paths]

    assert {manifest.request.regime for manifest in manifests} == set(GenerationRegime)
    assert len(manifests) == 3
    assert all(len(manifest.events) <= 8 for manifest in manifests)
    assert all(path.stat().st_size < 60_000 for path in paths)
