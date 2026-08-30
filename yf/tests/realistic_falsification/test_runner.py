from __future__ import annotations

import importlib
import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.bounds import (
    Gate1EvidenceError,
    load_official_gate1_context,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _runner():
    try:
        return importlib.import_module("yieldforge.realistic_falsification.runner")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 6 runner module is missing: {error}")


def _fake_session(*, failed_stream_ids: frozenset[str] = frozenset()):
    stream_ids = tuple(f"stream-{index:02d}" for index in range(40))
    corpora = (
        SimpleNamespace(confirmation_stream_ids=stream_ids[:20]),
        SimpleNamespace(confirmation_stream_ids=stream_ids[20:]),
    )
    built: list[str] = []

    class Session:
        context = SimpleNamespace(bundle=SimpleNamespace(contract=SimpleNamespace(corpora=corpora)))
        baseline_selections = ("lectra-selection", "loco-selection")

        def build_stream_cell(self, stream_id: str):
            built.append(stream_id)
            if stream_id in failed_stream_ids:
                raise ValueError(f"failed {stream_id}")
            return f"cell-for-{stream_id}"

    return Session(), stream_ids, built


def test_execute_gate1_opens_one_session_and_builds_all_cells_in_contract_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    session, stream_ids, built = _fake_session()
    opens: list[Path] = []
    captured: dict[str, object] = {}

    def open_session(root: Path):
        opens.append(root)
        return session

    def build_artifact(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return "artifact"

    monkeypatch.setattr(runner, "_open_official_gate1_session", open_session)
    monkeypatch.setattr(runner, "_build_registered_tiny_audit", lambda: "tiny-audit")
    monkeypatch.setattr(runner, "_strict_gate1_cell", lambda value: value)
    monkeypatch.setattr(runner, "_build_run_artifact", build_artifact)

    root = Path("/official/repository")
    assert runner.execute_official_gate1(root) == "artifact"
    assert opens == [root.resolve()]
    assert built == list(stream_ids)
    assert tuple(item.stream_id for item in captured["attempts"]) == stream_ids
    assert all(item.status == "success" for item in captured["attempts"])
    assert captured["baseline_selections"] == session.baseline_selections
    assert captured["tiny_audit"] == "tiny-audit"


def test_execute_gate1_continues_after_cell_failures_and_preserves_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    failed = frozenset({"stream-03", "stream-22"})
    session, stream_ids, built = _fake_session(failed_stream_ids=failed)
    captured: dict[str, object] = {}

    monkeypatch.setattr(runner, "_open_official_gate1_session", lambda root: session)
    monkeypatch.setattr(runner, "_build_registered_tiny_audit", lambda: "tiny-audit")
    monkeypatch.setattr(runner, "_strict_gate1_cell", lambda value: value)
    monkeypatch.setattr(
        runner,
        "_build_run_artifact",
        lambda **kwargs: captured.update(kwargs) or "invalid-artifact",
    )

    assert runner.execute_official_gate1(Path("/official/repository")) == "invalid-artifact"
    assert built == list(stream_ids)
    attempts = captured["attempts"]
    assert tuple(item.stream_id for item in attempts) == stream_ids
    assert {item.stream_id for item in attempts if item.status == "failure"} == failed
    assert all(
        item.cell is None and item.failure is not None
        for item in attempts
        if item.status == "failure"
    )
    assert all(
        item.cell is not None and item.failure is None
        for item in attempts
        if item.status == "success"
    )


def test_malformed_session_cell_is_recorded_as_failure_and_does_not_stop_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    session, stream_ids, built = _fake_session()
    captured: dict[str, object] = {}

    def strict_cell(value: str) -> str:
        if value == "cell-for-stream-05":
            raise TypeError("malformed Gate 1 cell")
        return value

    monkeypatch.setattr(runner, "_open_official_gate1_session", lambda root: session)
    monkeypatch.setattr(runner, "_build_registered_tiny_audit", lambda: "tiny-audit")
    monkeypatch.setattr(runner, "_strict_gate1_cell", strict_cell)
    monkeypatch.setattr(
        runner,
        "_build_run_artifact",
        lambda **kwargs: captured.update(kwargs) or "invalid-artifact",
    )

    assert runner.execute_official_gate1(Path("/official/repository")) == "invalid-artifact"
    assert built == list(stream_ids)
    attempts = captured["attempts"]
    assert attempts[5].status == "failure"
    assert attempts[5].cell is None
    assert attempts[5].failure is not None
    assert all(item.status == "success" for item in attempts[:5] + attempts[6:])


def test_publish_then_readback_must_exact_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = SimpleNamespace(status="gate_1_survived", result_id="run-id")
    published = tmp_path / "m11-gate1-run-id.json"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        runner,
        "validate_official_m11_pack",
        lambda root: calls.append(("validate", root)) or object(),
    )

    monkeypatch.setattr(
        runner,
        "execute_official_gate1",
        lambda root: calls.append(("execute", root)) or artifact,
    )
    monkeypatch.setattr(
        runner,
        "publish_gate1_run",
        lambda output, value: calls.append(("publish", (output, value))) or published,
    )
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda path, *, repository_root: (
            calls.append(("readback", (path, repository_root))) or artifact
        ),
    )

    result, path = runner.run_and_publish_official_gate1(
        repository_root=tmp_path,
        output_directory=tmp_path / "results",
    )

    assert (result, path) == (artifact, published)
    assert calls == [
        ("validate", tmp_path.resolve()),
        ("execute", tmp_path.resolve()),
        ("publish", (tmp_path / "results", artifact)),
        ("readback", (published, tmp_path.resolve())),
    ]


def test_publish_then_readback_rejects_a_different_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = SimpleNamespace(status="gate_1_survived", result_id="run-id")
    replacement = SimpleNamespace(status="gate_1_survived", result_id="other-id")
    published = tmp_path / "m11-gate1-run-id.json"

    monkeypatch.setattr(runner, "validate_official_m11_pack", lambda root: object())
    monkeypatch.setattr(runner, "execute_official_gate1", lambda root: artifact)
    monkeypatch.setattr(runner, "publish_gate1_run", lambda output, value: published)
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda path, *, repository_root: replacement,
    )

    with pytest.raises(ValueError, match="read-back"):
        runner.run_and_publish_official_gate1(
            repository_root=tmp_path,
            output_directory=tmp_path / "results",
        )


@pytest.fixture(scope="module")
def official_context():
    return load_official_gate1_context(REPO_ROOT)


@pytest.fixture(scope="module")
def synthetic_run_artifact(official_context):
    runner = _runner()
    evaluate_tests = importlib.import_module("tests.realistic_falsification.test_evaluate")
    context = official_context
    selections = evaluate_tests.official_selections.__wrapped__(context)
    cells = evaluate_tests._synthetic_confirmation_cells(
        context,
        selections,
        lower_ratio=Fraction(99, 100),
    )
    tiny_audit = runner._build_registered_tiny_audit()
    attempts = tuple(
        runner._Gate1AttemptDraft(
            position=index,
            stream_id=cell.stream_id,
            corpus_id=cell.corpus_id,
            status="success",
            cell=cell,
            failure=None,
        )
        for index, cell in enumerate(cells)
    )
    return runner._build_run_artifact(
        context=context,
        attempts=attempts,
        baseline_selections=selections,
        tiny_audit=tiny_audit,
    )


def _rehash_run(payload: dict[str, object]) -> None:
    digest = semantic_sha256(
        {key: value for key, value in payload.items() if key not in {"run_id", "content_sha256"}}
    )
    payload["run_id"] = f"yfm11g1run-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"


def test_run_artifact_round_trips_all_forty_cells_and_bound_roots(
    synthetic_run_artifact,
) -> None:
    runner = _runner()
    raw = runner._canonical_run_bytes(synthetic_run_artifact)

    strict = runner.M11Gate1RunArtifact.model_validate_json(raw, strict=True)

    assert strict == synthetic_run_artifact
    assert strict.successful_cell_count == 40
    assert strict.failed_cell_count == 0
    assert strict.gate1_result.audit_receipt is not None
    assert tuple(item.cell for item in strict.attempts) == (
        strict.gate1_result.audit_receipt.confirmation_cells
    )
    assert strict.productization_authorized is False


def test_run_artifact_rejects_reordered_attempts_even_when_rehashed(
    synthetic_run_artifact,
) -> None:
    runner = _runner()
    payload = synthetic_run_artifact.model_dump(mode="python", round_trip=True)
    attempts = list(payload["attempts"])
    attempts[0], attempts[1] = attempts[1], attempts[0]
    payload["attempts"] = tuple(attempts)
    _rehash_run(payload)

    with pytest.raises(ValidationError, match="contract order"):
        runner.M11Gate1RunArtifact.model_validate(payload, strict=True)


def _make_invalid_run_artifact(
    synthetic_run_artifact,
    official_context,
    *,
    failure: Exception | None = None,
):
    runner = _runner()
    drafts = [
        runner._Gate1AttemptDraft(
            position=item.position,
            stream_id=item.stream_id,
            corpus_id=item.corpus_id,
            status=item.status,
            cell=item.cell,
            failure=item.failure,
        )
        for item in synthetic_run_artifact.attempts
    ]
    failed = drafts[7]
    drafts[7] = runner._Gate1AttemptDraft(
        position=failed.position,
        stream_id=failed.stream_id,
        corpus_id=failed.corpus_id,
        status="failure",
        cell=None,
        failure=runner._failure_record(failure or RuntimeError("registered cell failed")),
    )

    return runner._build_run_artifact(
        context=official_context,
        attempts=tuple(drafts),
        baseline_selections=synthetic_run_artifact.baseline_selections,
        tiny_audit=synthetic_run_artifact.tiny_audit,
    )


def test_failed_cell_is_preserved_as_invalid_without_bootstrap(
    synthetic_run_artifact,
    official_context,
) -> None:
    artifact = _make_invalid_run_artifact(synthetic_run_artifact, official_context)
    failed = synthetic_run_artifact.attempts[7]

    assert artifact.status == "invalid_test"
    assert artifact.disposition == "INVALID_NONZERO"
    assert artifact.successful_cell_count == 39
    assert artifact.failed_cell_count == 1
    assert artifact.attempts[7].failure is not None
    assert artifact.gate1_result.statistics is None
    assert artifact.gate1_result.audit_receipt is None
    assert artifact.gate1_result.observed_cell_ids[7] == f"failure:{failed.stream_id}"


def test_invalid_readback_reconstructs_successes_and_reproduces_failed_fingerprint(
    synthetic_run_artifact,
    official_context,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = _make_invalid_run_artifact(synthetic_run_artifact, official_context)
    context = official_context
    expected_cells = {
        item.stream_id: item.cell for item in artifact.attempts if item.status == "success"
    }
    built: list[str] = []

    class FakeSession:
        def __init__(self) -> None:
            self.context = context
            self.baseline_selections = artifact.baseline_selections

        def build_stream_cell(self, stream_id: str):
            built.append(stream_id)
            if stream_id == artifact.attempts[7].stream_id:
                raise RuntimeError("registered cell failed")
            return expected_cells[stream_id]

    opens: list[Path] = []

    def open_session(root: Path):
        opens.append(root)
        return FakeSession()

    monkeypatch.setattr(runner, "_open_official_gate1_session", open_session)
    monkeypatch.setattr(
        runner,
        "_build_registered_tiny_audit",
        lambda: artifact.tiny_audit,
    )
    monkeypatch.setattr(
        runner,
        "authenticate_official_gate1_evaluation",
        lambda *args, **kwargs: pytest.fail("invalid run entered valid-only Task 5 authenticator"),
    )
    path = runner.publish_gate1_run(tmp_path / "results", artifact)

    loaded = runner.load_official_gate1_run(path, repository_root=REPO_ROOT)

    assert loaded == artifact
    assert opens == [REPO_ROOT.resolve()]
    assert built == [item.stream_id for item in artifact.attempts]


def test_invalid_readback_rejects_a_fabricated_failure_when_stream_succeeds(
    synthetic_run_artifact,
    official_context,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = _make_invalid_run_artifact(synthetic_run_artifact, official_context)
    context = official_context
    canonical_cells = {item.stream_id: item.cell for item in synthetic_run_artifact.attempts}

    class FakeSession:
        def __init__(self) -> None:
            self.context = context
            self.baseline_selections = artifact.baseline_selections

        def build_stream_cell(self, stream_id: str):
            return canonical_cells[stream_id]

    monkeypatch.setattr(runner, "_open_official_gate1_session", lambda root: FakeSession())
    monkeypatch.setattr(
        runner,
        "_build_registered_tiny_audit",
        lambda: artifact.tiny_audit,
    )
    path = runner.publish_gate1_run(tmp_path / "results", artifact)

    with pytest.raises(runner.M11Gate1RunnerError, match="invalid read-back"):
        runner.load_official_gate1_run(path, repository_root=REPO_ROOT)


def test_invalid_readback_rejects_failure_kind_and_repair_eligibility_flip(
    synthetic_run_artifact,
    official_context,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    evidence_artifact = _make_invalid_run_artifact(
        synthetic_run_artifact,
        official_context,
        failure=Gate1EvidenceError("registered cell failed"),
    )
    flipped_artifact = _make_invalid_run_artifact(
        synthetic_run_artifact,
        official_context,
        failure=RuntimeError("registered cell failed"),
    )
    assert evidence_artifact.attempts[7].failure.failure_kind == "evidence_failure"
    assert flipped_artifact.attempts[7].failure.failure_kind == "software_execution_failure"
    assert evidence_artifact.gate1_result.verdict.action == "ABANDON"
    assert flipped_artifact.gate1_result.verdict.action == "ONE_REPAIR_AND_RERUN"

    context = official_context
    expected_cells = {
        item.stream_id: item.cell for item in evidence_artifact.attempts if item.status == "success"
    }

    class FakeSession:
        def __init__(self) -> None:
            self.context = context
            self.baseline_selections = evidence_artifact.baseline_selections

        def build_stream_cell(self, stream_id: str):
            if stream_id == evidence_artifact.attempts[7].stream_id:
                raise Gate1EvidenceError("registered cell failed")
            return expected_cells[stream_id]

    monkeypatch.setattr(runner, "_open_official_gate1_session", lambda root: FakeSession())
    monkeypatch.setattr(
        runner,
        "_build_registered_tiny_audit",
        lambda: evidence_artifact.tiny_audit,
    )
    path = runner.publish_gate1_run(tmp_path / "results", flipped_artifact)

    with pytest.raises(runner.M11Gate1RunnerError, match="invalid read-back"):
        runner.load_official_gate1_run(path, repository_root=REPO_ROOT)


def test_invalid_publish_and_authenticated_readback_returns_artifact_for_nonzero_cli(
    synthetic_run_artifact,
    official_context,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = _make_invalid_run_artifact(synthetic_run_artifact, official_context)
    published = tmp_path / "results" / f"m11-gate1-{artifact.run_id}.json"

    monkeypatch.setattr(runner, "validate_official_m11_pack", lambda root: object())
    monkeypatch.setattr(runner, "execute_official_gate1", lambda root: artifact)
    monkeypatch.setattr(runner, "publish_gate1_run", lambda output, value: published)
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda path, *, repository_root: artifact,
    )

    result, path = runner.run_and_publish_official_gate1(
        repository_root=REPO_ROOT,
        output_directory=tmp_path / "results",
    )

    assert result == artifact
    assert path == published


def test_publication_is_immutable_and_idempotent(
    synthetic_run_artifact,
    tmp_path: Path,
) -> None:
    runner = _runner()
    path = runner.publish_gate1_run(tmp_path / "results", synthetic_run_artifact)
    first = path.read_bytes()

    assert runner.publish_gate1_run(tmp_path / "results", synthetic_run_artifact) == path
    assert path.read_bytes() == first

    path.write_bytes(
        first.replace(
            b'"productization_authorized": false',
            b'"productization_authorized": true',
        )
    )
    with pytest.raises(runner.M11Gate1RunnerError, match="publication"):
        runner.publish_gate1_run(tmp_path / "results", synthetic_run_artifact)


def test_strict_readback_invokes_task5_authenticator_and_exact_compares_envelope(
    synthetic_run_artifact,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    path = runner.publish_gate1_run(tmp_path / "results", synthetic_run_artifact)
    calls: list[tuple[object, Path]] = []

    def authenticate(result, *, repository_root):  # type: ignore[no-untyped-def]
        calls.append((result, repository_root))
        return result

    monkeypatch.setattr(runner, "authenticate_official_gate1_evaluation", authenticate)

    loaded = runner.load_official_gate1_run(path, repository_root=REPO_ROOT)

    assert loaded == synthetic_run_artifact
    assert calls == [(synthetic_run_artifact.gate1_result, REPO_ROOT.resolve())]


def test_strict_readback_rejects_duplicate_keys_before_authentication(
    synthetic_run_artifact,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    raw = runner._canonical_run_bytes(synthetic_run_artifact)
    duplicate = raw.replace(
        b'{\n  "attempts":',
        b'{\n  "status": "gate_1_survived",\n  "attempts":',
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_bytes(duplicate)
    monkeypatch.setattr(
        runner,
        "authenticate_official_gate1_evaluation",
        lambda *args, **kwargs: pytest.fail("duplicate JSON reached Task 5 authentication"),
    )

    with pytest.raises(runner.M11Gate1RunnerError, match="canonical evidence"):
        runner.load_official_gate1_run(path, repository_root=REPO_ROOT)


def test_strict_readback_rejects_task5_authentication_failure(
    synthetic_run_artifact,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    path = runner.publish_gate1_run(tmp_path / "results", synthetic_run_artifact)

    def reject(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("coordinated replacement rejected")

    monkeypatch.setattr(runner, "authenticate_official_gate1_evaluation", reject)

    with pytest.raises(runner.M11Gate1RunnerError, match="authentication"):
        runner.load_official_gate1_run(path, repository_root=REPO_ROOT)


def test_canonical_run_bytes_are_stable_and_json_has_no_nan(
    synthetic_run_artifact,
) -> None:
    runner = _runner()
    first = runner._canonical_run_bytes(synthetic_run_artifact)
    second = runner._canonical_run_bytes(synthetic_run_artifact)

    assert first == second
    assert b"NaN" not in first
    assert json.loads(first)["run_id"] == synthetic_run_artifact.run_id


def test_generate_pack_publishes_both_roots_then_strict_loads_exact_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    bundle = object()
    artifacts = SimpleNamespace(contract=b"contract\n", population=b"population\n")
    published: list[tuple[Path, bytes, str]] = []

    monkeypatch.setattr(runner, "generate_m11_pack", lambda root: bundle)
    monkeypatch.setattr(runner, "canonical_pack_artifact_bytes", lambda value: artifacts)

    def publish(path, data, *, validate, label):  # type: ignore[no-untyped-def]
        assert validate(data) == data
        published.append((path, data, label))
        return path

    monkeypatch.setattr(runner, "publish_immutable_artifact", publish)
    monkeypatch.setattr(runner, "_validate_contract_bytes", lambda data: data)
    monkeypatch.setattr(runner, "_validate_population_bytes", lambda data: data)
    monkeypatch.setattr(runner, "load_m11_pack_bundle", lambda **kwargs: bundle)

    result = runner.generate_and_publish_m11_pack(tmp_path)

    assert result.bundle is bundle
    assert published == [
        (
            tmp_path / "benchmarks/falsification/m11-contract-v1.json",
            artifacts.contract,
            "M11 contract artifact",
        ),
        (
            tmp_path / "benchmarks/falsification/m11-population-v1.json",
            artifacts.population,
            "M11 population artifact",
        ),
    ]


def test_validate_pack_regenerates_and_exact_compares_canonical_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    loaded = object()
    regenerated = object()
    calls: list[object] = []

    monkeypatch.setattr(runner, "load_m11_pack_bundle", lambda **kwargs: loaded)
    monkeypatch.setattr(runner, "generate_m11_pack", lambda root: regenerated)
    monkeypatch.setattr(
        runner,
        "canonical_pack_artifact_bytes",
        lambda bundle: calls.append(bundle) or b"same canonical bytes",
    )

    assert runner.validate_official_m11_pack(tmp_path) is loaded
    assert calls == [loaded, regenerated]


def test_validate_pack_rejects_regeneration_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    loaded = object()
    regenerated = object()

    monkeypatch.setattr(runner, "load_m11_pack_bundle", lambda **kwargs: loaded)
    monkeypatch.setattr(runner, "generate_m11_pack", lambda root: regenerated)
    monkeypatch.setattr(
        runner,
        "canonical_pack_artifact_bytes",
        lambda bundle: b"loaded" if bundle is loaded else b"regenerated",
    )

    with pytest.raises(runner.M11Gate1RunnerError, match="differs"):
        runner.validate_official_m11_pack(tmp_path)
