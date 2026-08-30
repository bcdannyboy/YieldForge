from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from shapely import Polygon, from_wkb

from yieldforge.experiments.contracts import canonical_pretty_json_bytes, semantic_sha256
from yieldforge.realistic_falsification.sources import (
    LECTRA_CATALOG_MANIFEST_RAW_SHA256,
    LECTRA_CATALOG_RAW_SHA256,
    LECTRA_M3_INPUT_RAW_SHA256,
    LECTRA_M3_RESULT_RAW_SHA256,
    LECTRA_M4_INPUT_ID,
    LECTRA_M4_RAW_SHA256,
    LOCO_ARCHIVE_SHA256,
    LOCO_ARCHIVE_SIZE_BYTES,
    LOCO_ARCHIVE_URL,
    LOCO_EXPECTED_CENSUS,
    LOCoCatalog,
    LOCoCatalogManifest,
    M11SourceManifest,
    SourceEvidenceError,
    ZipSafetyLimits,
    attest_lectra_source,
    build_loco_catalog_manifest,
    build_m11_source_manifest,
    canonical_source_artifact_bytes,
    import_official_loco_archive,
    load_loco_catalog,
    load_loco_catalog_manifest,
    load_m11_source_manifest,
    parse_loco_archive,
    quarter_turn_family_sha256,
    scale_invariant_family_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_ARCHIVE = Path("/tmp/yieldforge_2dics_cutting_stock.zip")
COMMITTED_CATALOG = REPO_ROOT / "datasets/catalogs/loco-2dics-v1/loco-catalog.json"
COMMITTED_CATALOG_MANIFEST = REPO_ROOT / "datasets/catalogs/loco-2dics-v1/catalog-manifest.json"
COMMITTED_SOURCE_MANIFEST = REPO_ROOT / "benchmarks/falsification/source-manifest-v1.json"


def _zip_bytes(entries: list[tuple[str | zipfile.ZipInfo, bytes | str]]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body.encode() if isinstance(body, str) else body)
    return payload.getvalue()


def _archive(
    items: str,
    demand: str,
    *,
    stem: str = "fixture",
    extras: list[tuple[str | zipfile.ZipInfo, bytes | str]] | None = None,
) -> bytes:
    return _zip_bytes(
        [
            (f"cutting_stock/Items/{stem}.dat", items),
            (f"cutting_stock/demand/{stem}.dat", demand),
            *(extras or []),
        ]
    )


def _one_rectangle_archive(*, demand: str = "1\n1\nfixture\n") -> bytes:
    return _archive("1\n4\n0 0\n2 0\n2 1\n0 1\n", demand)


def test_parser_preserves_repeated_records_and_exact_translation() -> None:
    archive = _archive(
        """
        # three source records; first and third intentionally repeat
        3
        4
        -1.5 2
        1/2 2
        1/2 3
        -1.5 3
        3 # a rational-coordinate triangle
        64/10 -1
        84/10 -1
        64/10 2
        4
        -1.5 2
        1/2 2
        1/2 3
        -1.5 3
        """,
        """
        3
        2
        5
        7
        label_does_not_have_to_match_filename
        """,
        extras=[("cutting_stock/NFPS/fixture.dat.nfps", b"not imported")],
    )

    catalog = parse_loco_archive(archive)

    assert len(catalog.items) == 3
    assert tuple(item.source_demand for item in catalog.items) == (2, 5, 7)
    assert all(
        item.instance_label == "label_does_not_have_to_match_filename" for item in catalog.items
    )
    assert catalog.items[0].source_translation == ("-3/2", "2")
    assert catalog.items[1].source_translation == ("32/5", "-1")
    assert (
        catalog.items[0].translation_normalized_sha256
        == catalog.items[2].translation_normalized_sha256
    )
    assert (
        catalog.items[0].quarter_turn_family_sha256 == catalog.items[2].quarter_turn_family_sha256
    )
    assert catalog.items[0].scale_invariant_family_id == catalog.items[2].scale_invariant_family_id
    assert catalog.items[0].item_id != catalog.items[2].item_id
    assert catalog.unique_translation_normalized_shape_count == 2
    assert catalog.unique_quarter_turn_family_count == 2
    assert catalog.total_source_demand == 14
    assert catalog.nfp_policy == "intentionally_excluded_not_parsed_or_committed"
    assert catalog.nfp_file_count == 1
    for item in catalog.items:
        polygon = from_wkb(bytes.fromhex(item.geometry.wkb_hex))
        assert polygon.bounds[0:2] == (0.0, 0.0)


def test_quarter_turn_family_hash_is_rotation_and_translation_invariant() -> None:
    original = Polygon(((3.0, -2.0), (8.0, -2.0), (7.0, 0.0), (3.0, 1.0)))
    rotated_and_shifted = Polygon(((12.0, 4.0), (12.0, 9.0), (10.0, 8.0), (9.0, 4.0)))

    assert quarter_turn_family_sha256(original) == quarter_turn_family_sha256(rotated_and_shifted)


def test_scale_family_is_scale_and_quarter_turn_invariant_but_not_reflection_invariant() -> None:
    original = Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)))
    scaled_rotated_shifted = Polygon(
        tuple((100 - 7 * y, -50 + 7 * x) for x, y in original.exterior.coords[:-1])
    )
    reflected = Polygon(tuple((-x, y) for x, y in original.exterior.coords[:-1]))

    assert scale_invariant_family_id(original) == scale_invariant_family_id(scaled_rotated_shifted)
    for scale in (0.1, 0.3, 3.7):
        decimal_scaled = Polygon(
            tuple((10 + scale * x, -3 + scale * y) for x, y in original.exterior.coords[:-1])
        )
        assert scale_invariant_family_id(original) == scale_invariant_family_id(decimal_scaled)
    assert scale_invariant_family_id(original) != scale_invariant_family_id(reflected)


@pytest.mark.parametrize(
    ("items", "demand", "match"),
    [
        ("1\n3\n0 0\nbad 0\n0 1\n", "1\n1\nx\n", "coordinate"),
        ("1\n3\n0 0\nnan 0\n0 1\n", "1\n1\nx\n", "coordinate"),
        ("1\n3\n0 0\ninf 0\n0 1\n", "1\n1\nx\n", "coordinate"),
        ("2\n3\n0 0\n1 0\n0 1\n", "2\n1\n1\nx\n", "count"),
        ("1\n3\n0 0\n1 0\n0 1\ntrailing\n", "1\n1\nx\n", "trailing"),
        ("1\n3\n0 0\n1 0\n0 1\n", "1\n0\nx\n", "positive"),
        ("1\n3\n0 0\n1 0\n0 0\n", "1\n1\nx\n", "distinct"),
        ("1\n4\n0 0\n2 2\n0 2\n2 0\n", "1\n1\nx\n", "valid"),
        ("1\n3\n0 0\n1 0\n2 0\n", "1\n1\nx\n", "positive-area"),
        ("1\n3\n0 0\n1 0\n0 1\n", "2\n1\n1\nx\n", "count"),
        ("1\n3\n0 0\n1 0\n0 1\n", "1\n1\nx\ntrailing\n", "trailing"),
    ],
)
def test_parser_fails_closed_on_malformed_geometry_and_grammar(
    items: str, demand: str, match: str
) -> None:
    with pytest.raises(SourceEvidenceError, match=match):
        parse_loco_archive(_archive(items, demand))


def test_archive_hash_and_size_pins_fail_closed() -> None:
    payload = _one_rectangle_archive()

    with pytest.raises(SourceEvidenceError, match="SHA-256"):
        parse_loco_archive(payload, expected_sha256="0" * 64)
    with pytest.raises(SourceEvidenceError, match="size"):
        parse_loco_archive(payload, expected_size_bytes=len(payload) + 1)


@pytest.mark.parametrize(
    "bad_name", ["../escape.dat", "/absolute.dat", "C:/drive.dat", "bad\\name"]
)
def test_archive_rejects_absolute_traversal_and_non_posix_names(bad_name: str) -> None:
    with pytest.raises(SourceEvidenceError, match="member path"):
        parse_loco_archive(
            _archive(
                "1\n3\n0 0\n1 0\n0 1\n",
                "1\n1\nx\n",
                extras=[(bad_name, b"bad")],
            )
        )


def test_archive_rejects_duplicate_member_names() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("cutting_stock/Items/x.dat", "1\n3\n0 0\n1 0\n0 1\n")
        archive.writestr("cutting_stock/demand/x.dat", "1\n1\nx\n")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("cutting_stock/demand/x.dat", "1\n1\nx\n")

    with pytest.raises(SourceEvidenceError, match="duplicate"):
        parse_loco_archive(payload.getvalue())


def test_archive_rejects_symlinks() -> None:
    link = zipfile.ZipInfo("cutting_stock/NFPS/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16

    with pytest.raises(SourceEvidenceError, match="symlink"):
        parse_loco_archive(
            _archive(
                "1\n3\n0 0\n1 0\n0 1\n",
                "1\n1\nx\n",
                extras=[(link, b"target")],
            )
        )


def test_archive_rejects_missing_item_demand_pairs() -> None:
    with pytest.raises(SourceEvidenceError, match="Items/demand pairs"):
        parse_loco_archive(
            _zip_bytes([("cutting_stock/Items/orphan.dat", "1\n3\n0 0\n1 0\n0 1\n")])
        )


@pytest.mark.parametrize(
    "limits",
    [
        ZipSafetyLimits(max_archive_bytes=1),
        ZipSafetyLimits(max_members=1),
        ZipSafetyLimits(max_total_uncompressed_bytes=1),
        ZipSafetyLimits(max_member_uncompressed_bytes=1),
    ],
)
def test_archive_enforces_every_resource_bound(limits: ZipSafetyLimits) -> None:
    with pytest.raises(SourceEvidenceError, match="limit"):
        parse_loco_archive(_one_rectangle_archive(), limits=limits)


def test_models_reject_semantic_identity_forgery_and_noncanonical_bytes(tmp_path: Path) -> None:
    catalog = parse_loco_archive(_one_rectangle_archive())
    payload = catalog.model_dump(mode="python")
    payload["items"][0]["source_demand"] = 2
    with pytest.raises(ValidationError):
        LOCoCatalog.model_validate(payload, strict=True)

    item_payload = catalog.items[0].model_dump(mode="python")
    item_payload["item_id"] = "yflci-" + "0" * 24
    payload = catalog.model_dump(mode="python")
    items = list(payload["items"])
    items[0] = item_payload
    payload["items"] = tuple(items)
    with pytest.raises(ValidationError):
        LOCoCatalog.model_validate(payload, strict=True)

    canonical_path = tmp_path / "canonical.json"
    canonical_path.write_bytes(canonical_pretty_json_bytes(catalog))
    assert load_loco_catalog(canonical_path) == catalog

    compact_path = tmp_path / "compact.json"
    compact_path.write_text(json.dumps(catalog.model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(SourceEvidenceError, match="canonical"):
        load_loco_catalog(compact_path)


def test_fixture_regeneration_is_byte_deterministic() -> None:
    payload = _one_rectangle_archive()
    first = parse_loco_archive(payload)
    second = parse_loco_archive(payload)

    assert first == second
    assert canonical_pretty_json_bytes(first) == canonical_pretty_json_bytes(second)


def test_task_2_source_contracts_are_public_package_exports() -> None:
    import yieldforge.realistic_falsification as public

    assert public.LOCoCatalog is LOCoCatalog
    assert public.M11SourceManifest is M11SourceManifest
    assert public.parse_loco_archive is parse_loco_archive
    assert public.scale_invariant_family_id is scale_invariant_family_id


@pytest.fixture(scope="module")
def official_catalog() -> LOCoCatalog:
    if not OFFICIAL_ARCHIVE.exists():
        pytest.skip("official LOCo archive is not present at the canonical regeneration path")
    return import_official_loco_archive(OFFICIAL_ARCHIVE)


@pytest.fixture(scope="module")
def lectra_attestation():
    return attest_lectra_source(REPO_ROOT)


def test_official_archive_regenerates_expected_census(official_catalog: LOCoCatalog) -> None:
    assert LOCO_ARCHIVE_URL == (
        "https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip"
    )
    assert LOCO_ARCHIVE_SIZE_BYTES == 1_654_751
    assert LOCO_ARCHIVE_SHA256 == (
        "86980c3d4a33fb329bd9a4cdc9464a6de9e8450baf70b1b4365944ab471a5133"
    )
    assert hashlib.sha256(OFFICIAL_ARCHIVE.read_bytes()).hexdigest() == LOCO_ARCHIVE_SHA256
    assert official_catalog.census == LOCO_EXPECTED_CENSUS
    assert official_catalog.member_count == 52
    assert official_catalog.item_file_count == 15
    assert official_catalog.demand_file_count == 15
    assert official_catalog.nfp_file_count == 18
    assert official_catalog.item_record_count == 511
    assert official_catalog.total_source_demand == 25_658
    assert official_catalog.unique_translation_normalized_shape_count == 160
    assert official_catalog.unique_quarter_turn_family_count == 157
    assert official_catalog.census.unique_scale_invariant_family_count == 140


def test_lectra_attestation_binds_exact_roots_and_candidate_joins(lectra_attestation) -> None:
    assert LECTRA_M4_RAW_SHA256 == (
        "55ae844109e4d335f28d3a88cd34781be0ec2ab9627146aa4baa827aa14f24e9"
    )
    assert LECTRA_M3_INPUT_RAW_SHA256 == (
        "4be7ab098234493439fe80e1703454936d9a8c4eda8484164242950bdc2447c8"
    )
    assert LECTRA_M3_RESULT_RAW_SHA256 == (
        "297c81dcd4ad14e059cbe0af400d1955bacde63e6d8230fc92089059b0ed0a34"
    )
    assert LECTRA_CATALOG_RAW_SHA256 == (
        "0e5c3d8aa39846fc69a1c662d01f0a0a9a1761f5d7ce0fbb10efdcf759fc55ad"
    )
    assert LECTRA_CATALOG_MANIFEST_RAW_SHA256 == (
        "95a404847a112b47ae27bd6269bc5e3e797c83848cabea2ce3b155004e82976e"
    )
    assert lectra_attestation.m4_input_id == LECTRA_M4_INPUT_ID
    assert lectra_attestation.origin_count == 406
    assert lectra_attestation.future_role_count == 6_607
    assert lectra_attestation.task_count == 203
    assert lectra_attestation.origins_per_task == 2
    assert lectra_attestation.candidate_join_count == 406
    assert lectra_attestation.material_provenance == "assumed"
    assert lectra_attestation.coordinate_units == "unknown"


def test_committed_artifacts_strict_load_and_regenerate_byte_identically(
    official_catalog: LOCoCatalog,
    lectra_attestation,
) -> None:
    catalog_manifest = build_loco_catalog_manifest(official_catalog)
    source_manifest = build_m11_source_manifest(
        loco_catalog=official_catalog,
        loco_manifest=catalog_manifest,
        lectra=lectra_attestation,
    )
    expected = canonical_source_artifact_bytes(
        official_catalog,
        catalog_manifest,
        source_manifest,
    )

    assert COMMITTED_CATALOG.read_bytes() == expected.catalog
    assert COMMITTED_CATALOG_MANIFEST.read_bytes() == expected.catalog_manifest
    assert COMMITTED_SOURCE_MANIFEST.read_bytes() == expected.source_manifest
    assert load_loco_catalog(COMMITTED_CATALOG) == official_catalog
    assert load_loco_catalog_manifest(COMMITTED_CATALOG_MANIFEST) == catalog_manifest
    assert load_m11_source_manifest(COMMITTED_SOURCE_MANIFEST) == source_manifest


def test_source_manifest_rejects_family_collision_and_root_aliasing(
    official_catalog: LOCoCatalog,
    lectra_attestation,
) -> None:
    catalog_manifest = build_loco_catalog_manifest(official_catalog)
    source_manifest = build_m11_source_manifest(
        loco_catalog=official_catalog,
        loco_manifest=catalog_manifest,
        lectra=lectra_attestation,
    )

    payload = source_manifest.model_dump(mode="python")
    payload["lectra"]["quarter_turn_family_sha256s"] = tuple(
        sorted(
            {
                *payload["lectra"]["quarter_turn_family_sha256s"],
                payload["loco"]["quarter_turn_family_sha256s"][0],
            }
        )
    )
    payload["lectra"]["quarter_turn_family_root_sha256"] = hashlib.sha256(
        ("\n".join(payload["lectra"]["quarter_turn_family_sha256s"]) + "\n").encode()
    ).hexdigest()
    lectra_semantic = dict(payload["lectra"])
    lectra_semantic.pop("attestation_id")
    lectra_semantic.pop("content_sha256")
    lectra_digest = semantic_sha256(lectra_semantic)
    payload["lectra"]["attestation_id"] = f"yfm11la-{lectra_digest[:24]}"
    payload["lectra"]["content_sha256"] = f"sha256:{lectra_digest}"
    with pytest.raises(ValidationError, match="collision"):
        M11SourceManifest.model_validate(payload, strict=True)

    payload = source_manifest.model_dump(mode="python")
    payload["lectra"]["origin_root_sha256"] = payload["loco"]["origin_root_sha256"]
    with pytest.raises(ValidationError, match="root"):
        M11SourceManifest.model_validate(payload, strict=True)


def test_manifest_models_reject_content_identity_forgery(official_catalog: LOCoCatalog) -> None:
    catalog_manifest = build_loco_catalog_manifest(official_catalog)
    payload = catalog_manifest.model_dump(mode="python")
    payload["manifest_id"] = "yflcm-" + "0" * 24
    with pytest.raises(ValidationError):
        LOCoCatalogManifest.model_validate(payload, strict=True)

    if COMMITTED_SOURCE_MANIFEST.exists():
        source_manifest = load_m11_source_manifest(COMMITTED_SOURCE_MANIFEST)
        payload = source_manifest.model_dump(mode="python")
        payload["source_manifest_id"] = "yfm11sm-" + "0" * 24
        with pytest.raises(ValidationError):
            M11SourceManifest.model_validate(payload, strict=True)
