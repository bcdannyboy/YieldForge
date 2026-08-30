from __future__ import annotations

import importlib
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256

REPO_ROOT = Path(__file__).resolve().parents[2]


def _runner():
    try:
        return importlib.import_module(
            "yieldforge.realistic_falsification.geometry_runner"
        )
    except ModuleNotFoundError as error:
        pytest.fail(f"Gate 2 runner module is missing: {error}")


def _open_gate1_artifact():
    return SimpleNamespace(
        run_id="yfm11g1run-" + "1" * 24,
        content_sha256="sha256:" + "2" * 64,
        status="gate_1_survived",
        disposition="OPEN_GATE_2",
        gate1_result=SimpleNamespace(
            result_id="yfm11g1r-" + "3" * 24,
            content_sha256="sha256:" + "4" * 64,
            opens_gate_2=True,
        ),
    )


@pytest.fixture(scope="module")
def synthetic_gate1_run_artifact():
    gate1_tests = importlib.import_module(
        "tests.realistic_falsification.test_runner"
    )
    evaluate_tests = importlib.import_module(
        "tests.realistic_falsification.test_evaluate"
    )
    gate1_runner = importlib.import_module(
        "yieldforge.realistic_falsification.runner"
    )
    context = gate1_tests.official_context.__wrapped__()
    selections = evaluate_tests.official_selections.__wrapped__(context)
    cells = evaluate_tests._synthetic_confirmation_cells(
        context,
        selections,
        lower_ratio=Fraction(97, 100),
    )
    attempts = tuple(
        gate1_runner._Gate1AttemptDraft(
            position=index,
            stream_id=cell.stream_id,
            corpus_id=cell.corpus_id,
            status="success",
            cell=cell,
            failure=None,
        )
        for index, cell in enumerate(cells)
    )
    artifact = gate1_runner._build_run_artifact(
        context=context,
        attempts=attempts,
        baseline_selections=selections,
        tiny_audit=gate1_runner._build_registered_tiny_audit(),
    )
    assert artifact.status == "gate_1_survived"
    assert artifact.disposition == "OPEN_GATE_2"
    return artifact


@pytest.fixture(scope="module")
def synthetic_gate2_context():
    geometry = importlib.import_module(
        "yieldforge.realistic_falsification.geometry_gate"
    )
    return geometry._load_official_gate2_context(REPO_ROOT)


@pytest.fixture(scope="module")
def synthetic_gate2_result(synthetic_gate1_run_artifact, synthetic_gate2_context):
    geometry = importlib.import_module(
        "yieldforge.realistic_falsification.geometry_gate"
    )
    context = synthetic_gate2_context
    gate1_result = synthetic_gate1_run_artifact.gate1_result
    receipt = gate1_result.audit_receipt
    assert receipt is not None
    streams = tuple(
        geometry.evaluate_gate2_stream(
            stream_id=cell.stream_id,
            corpus_id=cell.corpus_id,
            baseline_cost=cell.baseline_feasible_cost,
            lower_bound_cost=cell.lower_bound.lower_bound_cost,
            edges=(),
        )
        for cell in receipt.confirmation_cells
    )
    return geometry._build_gate2_evaluation_result(
        context=context,
        gate1_result=gate1_result,
        stream_results=streams,
        evaluation_stage="stage_a_favorable_superset",
    )


def _rehash_gate2_run(payload: dict[str, object]) -> None:
    digest = semantic_sha256(
        {
            key: value
            for key, value in payload.items()
            if key not in {"run_id", "content_sha256"}
        }
    )
    payload["run_id"] = f"yfm11g2run-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"


def test_run_gate2_authenticates_gate1_executes_once_then_publishes_and_reads_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    root = tmp_path / "repository"
    gate1_path = tmp_path / "gate1.json"
    output = tmp_path / "results"
    gate1 = _open_gate1_artifact()
    gate2_result = SimpleNamespace(result_id="yfm11g2r-" + "5" * 24)
    artifact = SimpleNamespace(run_id="yfm11g2run-" + "6" * 24)
    published = output / "m11-gate2-result.json"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda path, *, repository_root: calls.append(
            ("gate1", (path, repository_root))
        )
        or gate1,
    )
    monkeypatch.setattr(
        runner,
        "evaluate_official_gate2",
        lambda *, repository_root, gate1_result: calls.append(
            ("evaluate", (repository_root, gate1_result))
        )
        or gate2_result,
    )
    monkeypatch.setattr(
        runner,
        "build_gate2_run_artifact",
        lambda *, gate1_artifact, gate2_result: calls.append(
            ("build", (gate1_artifact, gate2_result))
        )
        or artifact,
    )
    monkeypatch.setattr(
        runner,
        "publish_gate2_run",
        lambda directory, value: calls.append(("publish", (directory, value)))
        or published,
    )
    monkeypatch.setattr(
        runner,
        "load_official_gate2_run",
        lambda path, *, repository_root, gate1_artifact_path: calls.append(
            ("readback", (path, repository_root, gate1_artifact_path))
        )
        or artifact,
    )

    result, result_path = runner.run_and_publish_official_gate2(
        repository_root=root,
        gate1_artifact_path=gate1_path,
        output_directory=output,
    )

    assert (result, result_path) == (artifact, published)
    assert calls == [
        ("gate1", (gate1_path, root.resolve())),
        ("evaluate", (root.resolve(), gate1.gate1_result)),
        ("build", (gate1, gate2_result)),
        ("publish", (output, artifact)),
        ("readback", (published, root.resolve(), gate1_path)),
    ]


@pytest.mark.parametrize(
    ("status", "disposition", "opens_gate_2"),
    (
        ("falsified_by_optimistic_ceiling", "ABANDON", False),
        ("invalid_test", "INVALID_NONZERO", False),
        ("gate_1_survived", "ABANDON", True),
    ),
)
def test_run_gate2_refuses_any_gate1_artifact_that_does_not_exactly_open_gate2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    disposition: str,
    opens_gate_2: bool,
) -> None:
    runner = _runner()
    gate1 = _open_gate1_artifact()
    gate1.status = status
    gate1.disposition = disposition
    gate1.gate1_result.opens_gate_2 = opens_gate_2
    evaluations: list[object] = []
    monkeypatch.setattr(runner, "load_official_gate1_run", lambda *args, **kwargs: gate1)
    monkeypatch.setattr(
        runner,
        "evaluate_official_gate2",
        lambda **kwargs: evaluations.append(kwargs),
    )

    with pytest.raises(runner.M11Gate2RunnerError, match="OPEN_GATE_2"):
        runner.run_and_publish_official_gate2(
            repository_root=tmp_path,
            gate1_artifact_path=tmp_path / "gate1.json",
            output_directory=tmp_path / "results",
        )

    assert evaluations == []


def test_run_gate2_rejects_a_readback_that_differs_from_the_published_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    gate1 = _open_gate1_artifact()
    artifact = SimpleNamespace(run_id="yfm11g2run-" + "6" * 24)
    monkeypatch.setattr(runner, "load_official_gate1_run", lambda *args, **kwargs: gate1)
    monkeypatch.setattr(
        runner,
        "evaluate_official_gate2",
        lambda **kwargs: SimpleNamespace(result_id="yfm11g2r-" + "5" * 24),
    )
    monkeypatch.setattr(runner, "build_gate2_run_artifact", lambda **kwargs: artifact)
    monkeypatch.setattr(
        runner,
        "publish_gate2_run",
        lambda *args, **kwargs: tmp_path / "published.json",
    )
    monkeypatch.setattr(
        runner,
        "load_official_gate2_run",
        lambda *args, **kwargs: SimpleNamespace(run_id="different"),
    )

    with pytest.raises(runner.M11Gate2RunnerError, match="read-back differs"):
        runner.run_and_publish_official_gate2(
            repository_root=tmp_path,
            gate1_artifact_path=tmp_path / "gate1.json",
            output_directory=tmp_path / "results",
        )


def test_run_gate2_normalizes_gate1_authentication_failure_for_nonzero_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("root mismatch")),
    )

    with pytest.raises(runner.M11Gate2RunnerError, match="Gate 1 authentication failed"):
        runner.run_and_publish_official_gate2(
            repository_root=tmp_path,
            gate1_artifact_path=tmp_path / "gate1.json",
            output_directory=tmp_path / "results",
        )


def test_run_gate2_normalizes_geometry_execution_failure_for_nonzero_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda *args, **kwargs: _open_gate1_artifact(),
    )
    monkeypatch.setattr(
        runner,
        "evaluate_official_gate2",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("geometry mismatch")),
    )

    with pytest.raises(runner.M11Gate2RunnerError, match="Gate 2 execution failed"):
        runner.run_and_publish_official_gate2(
            repository_root=tmp_path,
            gate1_artifact_path=tmp_path / "gate1.json",
            output_directory=tmp_path / "results",
        )


@pytest.mark.parametrize(
    ("status", "verdict_action", "expected"),
    (
        ("invalid_test", "ONE_REPAIR_AND_RERUN", "ONE_REPAIR_AND_RERUN"),
        ("invalid_test", "ABANDON", "ABANDON"),
        ("insufficient_headroom", "ABANDON", "ABANDON"),
        ("gate_2_survived", None, "OPEN_GATE_3"),
    ),
)
def test_gate2_disposition_preserves_invalid_repair_semantics(
    status: str,
    verdict_action: str | None,
    expected: str,
) -> None:
    runner = _runner()
    result = SimpleNamespace(
        status=status,
        verdict=(
            SimpleNamespace(action=verdict_action)
            if verdict_action is not None
            else None
        ),
    )

    assert runner._disposition_for_result(result) == expected


def test_build_gate2_run_artifact_binds_gate1_and_preserves_complete_gate2_census(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
) -> None:
    runner = _runner()

    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )

    assert artifact.gate1_run_id == synthetic_gate1_run_artifact.run_id
    assert (
        artifact.gate1_run_content_sha256
        == synthetic_gate1_run_artifact.content_sha256
    )
    assert artifact.gate1_result_id == synthetic_gate1_run_artifact.gate1_result.result_id
    assert artifact.gate2_result == synthetic_gate2_result
    assert artifact.stream_count == 40
    assert artifact.edge_count == 0
    assert artifact.unresolved_optimistically_counted == 0
    assert artifact.blocking_error_count == 0
    assert artifact.status == "insufficient_headroom"
    assert artifact.evaluation_stage == "stage_a_favorable_superset"
    assert artifact.disposition == "ABANDON"
    assert artifact.productization_authorized is False


def test_gate2_run_artifact_strictly_round_trips_canonical_bytes(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
) -> None:
    runner = _runner()
    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )

    raw = runner._canonical_gate2_run_bytes(artifact)
    strict = runner.M11Gate2RunArtifact.model_validate_json(raw, strict=True)

    assert strict == artifact
    assert b'"productization_authorized": false' in raw
    assert b'"stream_results": [' in raw


def test_gate2_run_artifact_rejects_rehashed_edge_count_tampering(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
) -> None:
    runner = _runner()
    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )
    payload = artifact.model_dump(mode="python", round_trip=True)
    payload["edge_count"] = 1
    _rehash_gate2_run(payload)

    with pytest.raises(ValidationError, match="edge census"):
        runner.M11Gate2RunArtifact.model_validate(payload, strict=True)


def test_publish_gate2_run_is_immutable_and_idempotent(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )

    path = runner.publish_gate2_run(tmp_path / "results", artifact)

    assert path.name == f"m11-gate2-{artifact.run_id}.json"
    assert path.read_bytes() == runner._canonical_gate2_run_bytes(artifact)
    assert runner.publish_gate2_run(tmp_path / "results", artifact) == path


def test_strict_gate2_readback_freshly_authenticates_gate1_and_gate2(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )
    path = runner.publish_gate2_run(tmp_path / "results", artifact)
    gate1_path = tmp_path / "gate1.json"
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda path, *, repository_root: calls.append(
            ("gate1", (path, repository_root))
        )
        or synthetic_gate1_run_artifact,
    )
    monkeypatch.setattr(
        runner,
        "authenticate_official_gate2_evaluation",
        lambda result, *, repository_root, gate1_result: calls.append(
            ("gate2", (result, repository_root, gate1_result))
        )
        or synthetic_gate2_result,
    )

    loaded = runner.load_official_gate2_run(
        path,
        repository_root=REPO_ROOT,
        gate1_artifact_path=gate1_path,
    )

    assert loaded == artifact
    assert calls == [
        ("gate1", (gate1_path, REPO_ROOT.resolve())),
        (
            "gate2",
            (
                synthetic_gate2_result,
                REPO_ROOT.resolve(),
                synthetic_gate1_run_artifact.gate1_result,
            ),
        ),
    ]


def test_strict_gate2_readback_rejects_duplicate_keys_before_authentication(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )
    duplicate = runner._canonical_gate2_run_bytes(artifact).replace(
        b'{\n  "blocking_error_count":',
        b'{\n  "status": "insufficient_headroom",\n  "blocking_error_count":',
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_bytes(duplicate)
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda *args, **kwargs: pytest.fail("duplicate JSON reached Gate 1 authentication"),
    )

    with pytest.raises(runner.M11Gate2RunnerError, match="canonical evidence"):
        runner.load_official_gate2_run(
            path,
            repository_root=REPO_ROOT,
            gate1_artifact_path=tmp_path / "gate1.json",
        )


def test_strict_gate2_readback_rejects_rehashed_gate1_run_replacement(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )
    payload = artifact.model_dump(mode="python", round_trip=True)
    payload["gate1_run_id"] = "yfm11g1run-" + "a" * 24
    _rehash_gate2_run(payload)
    replaced = runner.M11Gate2RunArtifact.model_validate(payload, strict=True)
    path = tmp_path / "replaced.json"
    path.write_bytes(runner._canonical_gate2_run_bytes(replaced))
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda *args, **kwargs: synthetic_gate1_run_artifact,
    )
    monkeypatch.setattr(
        runner,
        "authenticate_official_gate2_evaluation",
        lambda *args, **kwargs: pytest.fail("replacement reached Gate 2 authentication"),
        raising=False,
    )

    with pytest.raises(runner.M11Gate2RunnerError, match="Gate 1 run binding"):
        runner.load_official_gate2_run(
            path,
            repository_root=REPO_ROOT,
            gate1_artifact_path=tmp_path / "gate1.json",
        )


def test_strict_gate2_readback_rejects_geometry_authentication_failure(
    synthetic_gate1_run_artifact,
    synthetic_gate2_result,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner()
    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=synthetic_gate2_result,
    )
    path = runner.publish_gate2_run(tmp_path / "results", artifact)
    monkeypatch.setattr(
        runner,
        "load_official_gate1_run",
        lambda *args, **kwargs: synthetic_gate1_run_artifact,
    )

    def reject(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("coordinated replacement rejected")

    monkeypatch.setattr(
        runner,
        "authenticate_official_gate2_evaluation",
        reject,
        raising=False,
    )

    with pytest.raises(runner.M11Gate2RunnerError, match="authentication failed"):
        runner.load_official_gate2_run(
            path,
            repository_root=REPO_ROOT,
            gate1_artifact_path=tmp_path / "gate1.json",
        )


def test_invalid_gate2_run_preserves_one_repair_disposition_and_complete_edges(
    synthetic_gate1_run_artifact,
    synthetic_gate2_context,
) -> None:
    runner = _runner()
    geometry = importlib.import_module(
        "yieldforge.realistic_falsification.geometry_gate"
    )
    geometry_tests = importlib.import_module(
        "tests.realistic_falsification.test_geometry_gate"
    )
    gate1_result = synthetic_gate1_run_artifact.gate1_result
    receipt = gate1_result.audit_receipt
    assert receipt is not None
    first = receipt.confirmation_cells[0]
    origin = replace(geometry_tests._origin(), stream_id=first.stream_id)
    target = replace(geometry_tests._target(), stream_id=first.stream_id)
    blocking = geometry.assess_gate2_edge(origin, target)
    assert blocking.status == "blocking_error"
    streams = tuple(
        geometry.evaluate_gate2_stream(
            stream_id=cell.stream_id,
            corpus_id=cell.corpus_id,
            baseline_cost=cell.baseline_feasible_cost,
            lower_bound_cost=cell.lower_bound.lower_bound_cost,
            edges=((blocking,) if cell.stream_id == first.stream_id else ()),
        )
        for cell in receipt.confirmation_cells
    )
    result = geometry._build_gate2_evaluation_result(
        context=synthetic_gate2_context,
        gate1_result=gate1_result,
        stream_results=streams,
        evaluation_stage="stage_a_favorable_superset",
    )

    artifact = runner.build_gate2_run_artifact(
        gate1_artifact=synthetic_gate1_run_artifact,
        gate2_result=result,
    )

    assert result.status == "invalid_test"
    assert result.verdict.action == "ONE_REPAIR_AND_RERUN"
    assert artifact.status == "invalid_test"
    assert artifact.disposition == "ONE_REPAIR_AND_RERUN"
    assert artifact.edge_count == 1
    assert artifact.blocking_error_count == 1
    assert artifact.gate2_result.stream_results[0].edges == (blocking,)
