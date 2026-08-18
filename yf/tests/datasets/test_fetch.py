import hashlib
from pathlib import Path

import pytest

from yieldforge.datasets.fetch import DatasetIntegrityError, FetchStatus, fetch_file
from yieldforge.datasets.source_manifest import SourceFile


def source_spec(
    source: Path, *, checksum: str | None = None, size: int | None = None
) -> SourceFile:
    payload = source.read_bytes()
    return SourceFile(
        name=source.name,
        url=source.as_uri(),
        size_bytes=len(payload) if size is None else size,
        checksum_algorithm="md5",
        checksum=checksum or hashlib.md5(payload).hexdigest(),
    )


def test_fetch_file_verifies_and_atomically_promotes(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name

    status = fetch_file(source_spec(source), destination, chunk_size=4)

    assert status is FetchStatus.DOWNLOADED
    assert destination.read_bytes() == b"verified source"
    assert not destination.with_name(destination.name + ".partial").exists()


def test_fetch_file_accepts_an_already_verified_destination_without_opening_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    spec = source_spec(source)
    assert fetch_file(spec, destination) is FetchStatus.DOWNLOADED

    def fail_if_opened(*args: object, **kwargs: object) -> None:
        raise AssertionError("verified destinations must not be downloaded again")

    monkeypatch.setattr("yieldforge.datasets.fetch.urllib.request.urlopen", fail_if_opened)

    assert fetch_file(spec, destination) is FetchStatus.ALREADY_VERIFIED


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        ({"size": 999}, "byte count"),
        ({"checksum": "0" * 32}, "md5"),
    ],
)
def test_fetch_file_removes_partial_file_on_integrity_mismatch(
    tmp_path: Path, override: dict[str, object], expected_message: str
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"corrupt relative to the manifest")
    destination = tmp_path / "downloads" / source.name
    spec = source_spec(source, **override)  # type: ignore[arg-type]

    with pytest.raises(DatasetIntegrityError, match=expected_message):
        fetch_file(spec, destination)

    assert not destination.exists()
    assert not destination.with_name(destination.name + ".partial").exists()


def test_fetch_file_removes_partial_file_when_the_transfer_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name

    class BrokenResponse:
        def __enter__(self) -> "BrokenResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            partial = destination.with_name(destination.name + ".partial")
            if partial.exists() and partial.stat().st_size:
                raise OSError("connection lost")
            return b"some bytes"

    monkeypatch.setattr(
        "yieldforge.datasets.fetch.urllib.request.urlopen", lambda url: BrokenResponse()
    )

    with pytest.raises(OSError, match="connection lost"):
        fetch_file(source_spec(source), destination, chunk_size=4)

    assert not destination.exists()
    assert not destination.with_name(destination.name + ".partial").exists()


def test_fetch_file_refuses_to_overwrite_an_unverified_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    destination.parent.mkdir()
    destination.write_bytes(b"somebody else's bytes")

    with pytest.raises(DatasetIntegrityError, match="existing destination"):
        fetch_file(source_spec(source), destination)

    assert destination.read_bytes() == b"somebody else's bytes"
    assert not destination.with_name(destination.name + ".partial").exists()
