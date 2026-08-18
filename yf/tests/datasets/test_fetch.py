import hashlib
import math
import os
import threading
from pathlib import Path

import pytest

from yieldforge.datasets.fetch import DatasetIntegrityError, FetchStatus, fetch_file
from yieldforge.datasets.source_manifest import DatasetSourceManifest, SourceFile


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


def partial_files(destination: Path) -> list[Path]:
    return list(destination.parent.glob(f".{destination.name}.*.partial"))


def test_fetch_file_verifies_and_atomically_promotes(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name

    status = fetch_file(source_spec(source), destination, chunk_size=4)

    assert status is FetchStatus.DOWNLOADED
    assert destination.read_bytes() == b"verified source"
    assert partial_files(destination) == []


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
    assert partial_files(destination) == []


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
            partials = partial_files(destination)
            if partials and partials[0].stat().st_size:
                raise OSError("connection lost")
            return b"some bytes"

    monkeypatch.setattr(
        "yieldforge.datasets.fetch.urllib.request.urlopen",
        lambda url, *, timeout: BrokenResponse(),
    )

    with pytest.raises(OSError, match="connection lost"):
        fetch_file(source_spec(source), destination, chunk_size=4)

    assert not destination.exists()
    assert partial_files(destination) == []


def test_fetch_file_refuses_to_overwrite_an_unverified_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    destination.parent.mkdir()
    destination.write_bytes(b"somebody else's bytes")

    with pytest.raises(DatasetIntegrityError, match="existing destination"):
        fetch_file(source_spec(source), destination)

    assert destination.read_bytes() == b"somebody else's bytes"
    assert partial_files(destination) == []


def test_fetch_file_uses_a_unique_partial_path_for_each_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    seen_partial_paths: list[Path] = []

    class RecordingResponse:
        def __enter__(self) -> "RecordingResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            current = partial_files(destination)
            if current:
                seen_partial_paths.append(current[0])
            raise OSError("stop after observing the temporary path")

    monkeypatch.setattr(
        "yieldforge.datasets.fetch.urllib.request.urlopen",
        lambda url, *, timeout: RecordingResponse(),
    )

    for _ in range(2):
        with pytest.raises(OSError, match="temporary path"):
            fetch_file(source_spec(source), destination)

    assert len(set(seen_partial_paths)) == 2
    assert partial_files(destination) == []


def test_fetch_file_never_clobbers_an_unverified_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    real_link = os.link
    winner_may_write = threading.Event()
    winner_finished = threading.Event()

    def win_race() -> None:
        if not winner_may_write.wait(timeout=1):
            return
        destination.write_bytes(b"unverified concurrent winner")
        winner_finished.set()

    winner = threading.Thread(target=win_race)
    winner.start()

    def concurrent_link(source_path: Path, destination_path: Path) -> None:
        winner_may_write.set()
        if not winner_finished.wait(timeout=1):
            raise AssertionError("concurrent writer did not finish")
        real_link(source_path, destination_path)

    monkeypatch.setattr("yieldforge.datasets.fetch.os.link", concurrent_link)

    with pytest.raises(DatasetIntegrityError, match="existing destination"):
        fetch_file(source_spec(source), destination)

    winner.join(timeout=1)
    assert not winner.is_alive()
    assert destination.read_bytes() == b"unverified concurrent winner"
    assert partial_files(destination) == []


def test_fetch_file_accepts_a_verified_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    real_link = os.link

    def concurrent_link(source_path: Path, destination_path: Path) -> None:
        destination_path.write_bytes(b"verified source")
        real_link(source_path, destination_path)

    monkeypatch.setattr("yieldforge.datasets.fetch.os.link", concurrent_link)

    assert fetch_file(source_spec(source), destination) is FetchStatus.ALREADY_VERIFIED
    assert destination.read_bytes() == b"verified source"
    assert partial_files(destination) == []


def test_valid_manifest_preserves_outputs_named_foo_partial_and_foo(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    output = tmp_path / "output"
    foo_partial = sources / "foo.partial"
    foo_partial.write_bytes(b"the manifest's foo.partial")
    foo = sources / "foo"
    foo.write_bytes(b"the manifest's foo")

    manifest = DatasetSourceManifest(
        schema_version="yieldforge.dataset-source.v1",
        dataset_id="partial-name-regression",
        title="Partial name regression",
        doi="10.example/partial-name-regression",
        version="1",
        license="CC0",
        source_page="https://example.test/partial-name-regression",
        files=(source_spec(foo_partial), source_spec(foo)),
    )
    for spec in manifest.files:
        fetch_file(spec, output / spec.name)

    assert (output / "foo.partial").read_bytes() == b"the manifest's foo.partial"
    assert (output / "foo").read_bytes() == b"the manifest's foo"


@pytest.mark.parametrize("target_exists", [True, False])
def test_fetch_file_rejects_symlink_destinations(tmp_path: Path, target_exists: bool) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    target = tmp_path / "target.bin"
    if target_exists:
        target.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    destination.parent.mkdir()
    destination.symlink_to(target)

    with pytest.raises(DatasetIntegrityError, match="symlink"):
        fetch_file(source_spec(source), destination)

    assert destination.is_symlink()
    assert partial_files(destination) == []


def test_fetch_file_passes_a_finite_timeout_and_cleans_up_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    observed_timeout: list[float] = []

    def time_out(url: str, *, timeout: float) -> None:
        observed_timeout.append(timeout)
        raise TimeoutError("source timed out")

    monkeypatch.setattr("yieldforge.datasets.fetch.urllib.request.urlopen", time_out)

    with pytest.raises(TimeoutError, match="source timed out"):
        fetch_file(source_spec(source), destination, timeout_seconds=2.5)

    assert observed_timeout == [2.5]
    assert partial_files(destination) == []


def test_fetch_file_uses_a_finite_default_timeout_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / source.name
    observed_timeout: list[float] = []

    def time_out(url: str, *, timeout: float) -> None:
        observed_timeout.append(timeout)
        raise TimeoutError("source timed out")

    monkeypatch.setattr("yieldforge.datasets.fetch.urllib.request.urlopen", time_out)

    with pytest.raises(TimeoutError, match="source timed out"):
        fetch_file(source_spec(source), destination)

    assert len(observed_timeout) == 1
    assert observed_timeout[0] > 0
    assert math.isfinite(observed_timeout[0])
    assert partial_files(destination) == []
