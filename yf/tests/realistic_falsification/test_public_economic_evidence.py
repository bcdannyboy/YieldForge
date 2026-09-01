from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

YF_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = YF_ROOT.parent
PUBLIC_EVIDENCE_ROOT = YF_ROOT / "experiments/results/m11-economic-resolution"

EXPECTED_MANIFESTS = {
    "m11-economic-calibration-manifest-"
    "3409ada18b831fee1394410dfec88a02806ff0c3709372cfbe9340e05b920533.json": (
        "7b7f2b9e954730e31711fea95a476a8f38f08750a9feddc326aab2d8ce4e02f9"
    ),
    "m11-economic-validity-stage-"
    "8d736641be30b3d04dad50eb21698738ac4924bb42f664961911910ddb9ddfe4.json": (
        "512a371bdee295f21699ddd23174b88319b92061ceacc7fdfe6a87d7b619d7e0"
    ),
    "m11-economic-central-manifest-"
    "71171ff1cb601f546f55b78eda8dc2b81d60d7e02949042a55d53feb29e5dcf2.json": (
        "3f3eb6aaa59ea4a1809e8684b3603096b180ecad4d53e42af6e950d08f7f4633"
    ),
}

PRIVATE_EVIDENCE_RULES = {
    "yf/experiments/results/m11-gate2-*.json",
    "yf/experiments/results/m11-gate3-early-*.json",
    ("yf/experiments/results/m11-economic-resolution/m11-gate3-calibration-observation-*.json.gz"),
    ("yf/experiments/results/m11-economic-resolution/m11-gate3-validity-receipt-*.json.gz"),
    ("yf/experiments/results/m11-economic-resolution/m11-gate3-central-cell-*.json.gz"),
    ("yf/experiments/results/m11-economic-resolution/m11-economic-calibration-checkpoint-*.json"),
    ("yf/experiments/results/m11-economic-resolution/m11-economic-central-cell-checkpoint-*.json"),
    (
        "yf/experiments/results/m11-economic-resolution/"
        "m11-economic-validity-evidence-checkpoint-*.json"
    ),
    ("yf/experiments/results/m11-economic-resolution/m11-economic-central-segment-*.json"),
}

SOURCE_RICH_KEYS = {
    "coordinate",
    "coordinates",
    "demand",
    "demands",
    "demand_rows",
    "events",
    "exact_normalized_vertices",
    "ewkb",
    "geometries",
    "geometry",
    "geojson",
    "order_book",
    "order_books",
    "part",
    "part_ids",
    "part_source_row_indices",
    "parts",
    "placement",
    "placements",
    "points",
    "polygon",
    "polygons",
    "raw",
    "rings",
    "shape_hashes",
    "source_demand",
    "source_demand_rows",
    "source_task",
    "source_vertices",
    "vertex",
    "vertices",
    "wkb",
    "wkt",
}

_EMBEDDED_SOURCE_FIELD = re.compile(
    r"""(?ix)
    ["']
    (?:
        coordinates?|demands?|demand_rows?|events?|exact_normalized_vertices|
        geometr(?:y|ies)|order_books?|parts?|part_ids|part_source_row_indices|
        placements?|points?|polygons?|raw|rings?|shape_hashes|source_demands?|
        source_demand_rows?|source_tasks?|source_vertices|vertices?|wkb|wkt
    )
    ["']\s*:
    """
)
_WKT_GEOMETRY = re.compile(
    r"(?i)\b(?:geometrycollection|linestring|multilinestring|multipoint|"
    r"multipolygon|point|polygon)\s*(?:z|m|zm)?\s*\("
)
_WKB_HEX = re.compile(
    r"(?i)^(?:0x)?(?:01(?:01000000|02000000|03000000|04000000|05000000|"
    r"06000000|07000000)|00(?:00000001|00000002|00000003|00000004|"
    r"00000005|00000006|00000007))[0-9a-f]{24,}$"
)
_SOURCE_FORMAT_VALUE = re.compile(r"(?i)(?:^|[\s;/])(?:ewkb|geojson|wkb)\s*[:=]")


def _normalized_key(key: str) -> str:
    return re.sub(r"[\s-]+", "_", key.casefold())


def _assert_non_reconstructive(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert isinstance(key, str), f"JSON object key at {path} is not text"
            assert _normalized_key(key) not in SOURCE_RICH_KEYS, (
                f"source-rich key {key!r} found at {path}"
            )
            _assert_non_reconstructive(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_non_reconstructive(child, f"{path}[{index}]")
    elif isinstance(value, str):
        assert not _EMBEDDED_SOURCE_FIELD.search(value), (
            f"embedded source-rich field found at {path}"
        )
        assert not _WKT_GEOMETRY.search(value), f"WKT geometry found at {path}"
        assert not _WKB_HEX.fullmatch(value), f"WKB geometry found at {path}"
        assert not _SOURCE_FORMAT_VALUE.search(value), f"encoded source geometry found at {path}"


def test_public_economic_evidence_is_the_exact_authenticated_manifest_set() -> None:
    actual = {path.name for path in PUBLIC_EVIDENCE_ROOT.iterdir()}
    assert actual == set(EXPECTED_MANIFESTS)

    for name, expected_sha256 in EXPECTED_MANIFESTS.items():
        path = PUBLIC_EVIDENCE_ROOT / name
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


def test_public_economic_manifests_are_non_reconstructive() -> None:
    for name in EXPECTED_MANIFESTS:
        payload = json.loads((PUBLIC_EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
        _assert_non_reconstructive(payload)


def test_source_rich_scanner_allows_compact_audit_metadata() -> None:
    safe_metadata = {
        "compressed_raw_sha256": "sha256:" + "a" * 64,
        "positive_stream_count": 3,
        "sidecar_name": "m11-gate3-central-cell-" + "b" * 64 + ".json.gz",
        "source_lineage": "repaired_runtime",
        "source_metrics_content_sha256": "sha256:" + "c" * 64,
    }

    _assert_non_reconstructive(safe_metadata)


def test_private_economic_evidence_families_are_ignored() -> None:
    rules = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert PRIVATE_EVIDENCE_RULES <= rules

    for rule in sorted(PRIVATE_EVIDENCE_RULES):
        representative_path = rule.replace("*", "private-evidence")
        completed = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", representative_path],
            cwd=REPOSITORY_ROOT,
            check=False,
        )
        assert completed.returncode == 0, representative_path
