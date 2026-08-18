from pathlib import Path

import pytest
from pydantic import ValidationError

from yieldforge.datasets.source_manifest import DatasetSourceManifest


def test_lectra_manifest_matches_published_release() -> None:
    path = Path(__file__).parents[2] / "datasets" / "sources" / "lectra-7030786-v1.1.json"
    manifest = DatasetSourceManifest.model_validate_json(path.read_text())

    assert manifest.dataset_id == "lectra-7030786-v1.1"
    assert manifest.doi == "10.5281/zenodo.7030786"
    assert manifest.version == "1.1"
    assert manifest.license == "CC-BY-4.0"
    assert sum(file.size_bytes for file in manifest.files) == 187_809_072
    assert {file.name for file in manifest.files} == {
        "tasks.gz",
        "parts.gz",
        "shapes.gz",
        "constraints.gz",
    }

    expected_files = {
        "parts.gz": (
            "https://zenodo.org/api/records/7030786/files/parts.gz/content",
            22_476_589,
            "d8b51403f0cab79ec990b95a40911c1c",
        ),
        "constraints.gz": (
            "https://zenodo.org/api/records/7030786/files/constraints.gz/content",
            16_299_551,
            "e12581851bd2a357145a9dfccdad5363",
        ),
        "shapes.gz": (
            "https://zenodo.org/api/records/7030786/files/shapes.gz/content",
            147_824_458,
            "ff1623f24adf031710450a30e72984f2",
        ),
        "tasks.gz": (
            "https://zenodo.org/api/records/7030786/files/tasks.gz/content",
            1_208_474,
            "ac18fc58408a3fc832cfd6757b4b16ca",
        ),
    }
    assert {
        file.name: (file.url, file.size_bytes, file.checksum) for file in manifest.files
    } == expected_files
    assert all(file.checksum_algorithm == "md5" for file in manifest.files)


def test_source_manifest_is_immutable_and_forbids_unknown_fields() -> None:
    path = Path(__file__).parents[2] / "datasets" / "sources" / "lectra-7030786-v1.1.json"
    manifest = DatasetSourceManifest.model_validate_json(path.read_text())

    with pytest.raises(ValidationError, match="frozen"):
        manifest.dataset_id = "different"  # type: ignore[misc]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DatasetSourceManifest.model_validate({**manifest.model_dump(), "unexpected": True})

    assert isinstance(manifest.files, tuple)
    with pytest.raises(TypeError, match="does not support item assignment"):
        manifest.files[0] = manifest.files[0]  # type: ignore[index]


@pytest.mark.parametrize(
    "invalid_name",
    ["", ".", "..", "/parts.gz", "nested/parts.gz", "../parts.gz", r"nested\parts.gz"],
)
def test_source_file_name_must_be_a_plain_basename(invalid_name: str) -> None:
    path = Path(__file__).parents[2] / "datasets" / "sources" / "lectra-7030786-v1.1.json"
    manifest_data = DatasetSourceManifest.model_validate_json(path.read_text()).model_dump()
    manifest_data["files"][0]["name"] = invalid_name

    with pytest.raises(ValidationError):
        DatasetSourceManifest.model_validate(manifest_data)


def test_source_manifest_rejects_duplicate_file_names() -> None:
    path = Path(__file__).parents[2] / "datasets" / "sources" / "lectra-7030786-v1.1.json"
    manifest_data = DatasetSourceManifest.model_validate_json(path.read_text()).model_dump()
    files = list(manifest_data["files"])
    files.append(files[0])
    manifest_data["files"] = files

    with pytest.raises(ValidationError, match="file names must be unique"):
        DatasetSourceManifest.model_validate(manifest_data)
