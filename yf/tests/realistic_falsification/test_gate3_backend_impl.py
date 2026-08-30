from pathlib import Path
from types import SimpleNamespace

import pytest

import yieldforge.realistic_falsification.gate3_backend_impl as impl
from tests.realistic_falsification.test_gate3_backend import (
    _projection as _low_level_projection,
)
from tests.realistic_falsification.test_gate3_backend import _roots as _low_level_roots
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.adapter import (
    M11M7ProjectionAttestation,
    M11MaterialRuntimeProjection,
)
from yieldforge.realistic_falsification.confirmation import (
    Gate3CalibrationMaterialReplay,
)
from yieldforge.realistic_falsification.gate3_backend_impl import (
    AdapterGate3Backend,
    AdapterGate3BackendError,
    build_adapter_gate3_backend,
)


def test_gate3_backend_impl_public_api_exists() -> None:
    assert AdapterGate3Backend is not None
    assert issubclass(AdapterGate3BackendError, ValueError)
    assert callable(build_adapter_gate3_backend)


def _backend(*, roots: object | None = None) -> AdapterGate3Backend:
    selected_roots = roots if roots is not None else object()
    return AdapterGate3Backend(
        repository_root=Path("/repo"),
        context=SimpleNamespace(),
        roots=selected_roots,
        gate3_config=SimpleNamespace(),
        _calibration_ids={
            "lectra-m3-m4": tuple(f"lectra-cal-{index}" for index in range(8)),
            "loco-2dics": tuple(f"loco-cal-{index}" for index in range(8)),
        },
        _confirmation_ids={
            "lectra-m3-m4": tuple(f"lectra-con-{index}" for index in range(20)),
            "loco-2dics": tuple(f"loco-con-{index}" for index in range(20)),
        },
        _streams={
            **{f"lectra-cal-{index}": SimpleNamespace(regime="recurrent") for index in range(8)},
            **{f"loco-cal-{index}": SimpleNamespace(regime="mixed") for index in range(8)},
            **{f"lectra-con-{index}": SimpleNamespace(regime="high_mix") for index in range(20)},
            **{f"loco-con-{index}": SimpleNamespace(regime="regime_shift") for index in range(20)},
        },
    )


def test_stream_registries_are_exact_and_fail_closed() -> None:
    backend = _backend()

    assert backend.calibration_stream_ids("lectra-m3-m4") == tuple(
        f"lectra-cal-{index}" for index in range(8)
    )
    assert backend.confirmation_stream_ids("loco-2dics") == tuple(
        f"loco-con-{index}" for index in range(20)
    )
    with pytest.raises(AdapterGate3BackendError, match="unregistered Gate 3 corpus"):
        backend.calibration_stream_ids("forged-corpus")  # type: ignore[arg-type]
    with pytest.raises(AdapterGate3BackendError, match="unregistered Gate 3 calibration"):
        backend.execute_calibration_stream(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-con-0",
            policy_id="remnant_first",
        )


def test_authenticated_population_registry_preserves_contract_order() -> None:
    corpus_specs = (
        ("lectra-m3-m4", "lectra"),
        ("loco-2dics", "loco"),
    )
    corpora = []
    streams = []
    for corpus_id, prefix in corpus_specs:
        calibration_ids = tuple(f"{prefix}-cal-{index}" for index in range(8))
        confirmation_ids = tuple(f"{prefix}-con-{index}" for index in range(20))
        corpora.append(
            SimpleNamespace(
                source=SimpleNamespace(corpus_id=corpus_id),
                calibration_stream_ids=calibration_ids,
                confirmation_stream_ids=confirmation_ids,
            )
        )
        streams.extend(
            SimpleNamespace(
                stream_id=stream_id,
                corpus_id=corpus_id,
                partition=partition,
                stream_kind="primary",
                events=tuple(range(24)),
            )
            for partition, ids in (
                ("calibration", calibration_ids),
                ("confirmation", confirmation_ids),
            )
            for stream_id in ids
        )
    context = SimpleNamespace(
        gate1_result=SimpleNamespace(contract=SimpleNamespace(corpora=tuple(corpora))),
        population=SimpleNamespace(streams=tuple(streams)),
    )

    calibration, confirmation, indexed = impl._stream_registries(context)

    assert calibration["lectra-m3-m4"] == tuple(f"lectra-cal-{i}" for i in range(8))
    assert confirmation["loco-2dics"] == tuple(f"loco-con-{i}" for i in range(20))
    assert len(indexed) == 56

    streams[0].partition = "confirmation"
    with pytest.raises(AdapterGate3BackendError, match="authenticated population"):
        impl._stream_registries(context)


def test_central_projects_once_and_reuses_same_shards_across_b_f_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = object()
    backend = _backend(roots=roots)
    freeze = SimpleNamespace(
        roots=roots,
        corpus_id="lectra-m3-m4",
        selected_policy_id="remnant_first",
        content_sha256="sha256:freeze",
        calibration_stream_ids=backend.calibration_stream_ids("lectra-m3-m4"),
    )
    projections = (SimpleNamespace(name="material-a"), SimpleNamespace(name="material-b"))
    project_calls = []
    execution_calls = []
    merge_calls = []
    cell = object()

    monkeypatch.setattr(impl, "_strict_roots", lambda value: value)
    monkeypatch.setattr(impl, "_strict_freeze", lambda value: value)
    monkeypatch.setattr(impl, "gate3_policy_identity", lambda policy_id: ("policy", policy_id))

    def fake_project(context, stream_id, arm, *, policy):  # type: ignore[no-untyped-def]
        project_calls.append((context, stream_id, arm, policy))
        return projections

    def fake_execute(*, projection, roots, arm, policy_id):  # type: ignore[no-untyped-def]
        execution_calls.append((projection, roots, arm, policy_id))
        return SimpleNamespace(shard_trace=(projection.name, arm))

    def fake_merge(**kwargs):  # type: ignore[no-untyped-def]
        merge_calls.append(kwargs)
        return f"trace-{kwargs['arm']}"

    monkeypatch.setattr(impl, "project_stream", fake_project)
    monkeypatch.setattr(impl, "execute_gate3_material_shard", fake_execute)
    monkeypatch.setattr(impl, "merge_gate3_shard_traces", fake_merge)
    monkeypatch.setattr(impl, "build_gate3_stream_cell", lambda **kwargs: cell)

    result = backend.execute_central_stream(
        roots=roots,
        corpus_id="lectra-m3-m4",
        stream_id="lectra-con-0",
        baseline_freeze=freeze,
    )
    repeated = backend.execute_central_stream(
        roots=roots,
        corpus_id="lectra-m3-m4",
        stream_id="lectra-con-0",
        baseline_freeze=freeze,
    )

    assert result is cell and repeated is cell
    assert len(project_calls) == 1
    assert tuple(item[2] for item in execution_calls) == ("B", "B", "F", "F", "K", "K")
    assert tuple(item[0] for item in execution_calls) == projections * 3
    assert tuple(item["shards"] for item in merge_calls) == (
        (("material-a", "B"), ("material-b", "B")),
        (("material-a", "F"), ("material-b", "F")),
        (("material-a", "K"), ("material-b", "K")),
    )
    forged_freeze = SimpleNamespace(
        **(vars(freeze) | {"calibration_stream_ids": tuple(f"forged-{i}" for i in range(8))})
    )
    with pytest.raises(AdapterGate3BackendError, match="stream registry"):
        backend.execute_central_stream(
            roots=roots,
            corpus_id="lectra-m3-m4",
            stream_id="lectra-con-0",
            baseline_freeze=forged_freeze,
        )


@pytest.mark.parametrize(
    ("policy_id", "expected_arm", "expect_m9"),
    (
        ("remnant_first", "B", False),
        ("known_only_m9_two_ply_scrap", "B", True),
    ),
)
def test_calibration_packages_direct_and_known_only_evidence(
    monkeypatch: pytest.MonkeyPatch,
    policy_id: str,
    expected_arm: str,
    expect_m9: bool,
) -> None:
    backend = _backend()
    projection = SimpleNamespace(
        attestation=object(),
        runtime=SimpleNamespace(replay_input=object()),
    )
    decision = SimpleNamespace(algorithm="m9_two_ply" if expect_m9 else "m7_policy")
    applied_event = object()
    step = SimpleNamespace(event=applied_event)
    terminal_record = object()
    replay_result = None if expect_m9 else object()
    execution = SimpleNamespace(
        shard_trace=SimpleNamespace(
            arm="B",
            visibility="known_only" if expect_m9 else "released_only",
            decisions=(decision,),
        ),
        steps=(step,),
        terminal=SimpleNamespace(terminal=terminal_record),
        m7_replay_result=replay_result,
    )
    execute_calls = []
    material_calls = []
    transition = object()
    transition_calls = []
    applied_context = object()
    material = object()
    observation = object()

    monkeypatch.setattr(impl, "gate3_policy_identity", lambda value: value)
    monkeypatch.setattr(impl, "project_stream", lambda *args, **kwargs: (projection,))

    def fake_execute(**kwargs):  # type: ignore[no-untyped-def]
        execute_calls.append(kwargs)
        return execution

    def fake_material(**kwargs):  # type: ignore[no-untyped-def]
        material_calls.append(kwargs)
        return material

    monkeypatch.setattr(impl, "execute_gate3_material_shard", fake_execute)

    def fake_transition(**kwargs):  # type: ignore[no-untyped-def]
        transition_calls.append(kwargs)
        return transition

    monkeypatch.setattr(impl, "build_gate3_calibration_m9_transition", fake_transition)
    monkeypatch.setattr(
        impl,
        "build_gate3_applied_action_context",
        lambda **kwargs: applied_context,
    )
    monkeypatch.setattr(impl, "build_gate3_calibration_material_replay", fake_material)
    monkeypatch.setattr(
        impl,
        "build_gate3_calibration_observation",
        lambda **kwargs: observation,
    )

    result = backend.execute_calibration_stream(
        corpus_id="lectra-m3-m4",
        stream_id="lectra-cal-0",
        policy_id=policy_id,  # type: ignore[arg-type]
    )

    assert result is observation
    assert execute_calls[0]["arm"] == expected_arm
    if expect_m9:
        assert transition_calls == [{"decision": decision, "step": step}]
        assert material_calls[0]["m9_transitions"] == (transition,)
        assert material_calls[0]["m9_terminal"] is terminal_record
        assert "m7_replay_result" not in material_calls[0]
    else:
        assert material_calls[0]["m7_replay_result"] is replay_result
        assert material_calls[0]["m7_applied_contexts"] == (applied_context,)
        assert "m9_terminal" not in material_calls[0]


@pytest.mark.parametrize(
    "policy_id",
    ("age_regularity", "known_only_m9_two_ply_scrap"),
)
def test_calibration_packages_actual_low_level_execution_evidence(
    monkeypatch: pytest.MonkeyPatch,
    policy_id: str,
) -> None:
    base = _low_level_projection()
    payload = base.attestation.model_dump(
        mode="python",
        round_trip=True,
        exclude={"attestation_id", "content_sha256"},
    )
    payload["registration_kind"] = "calibration"
    payload["source_registration_id"] = base.attestation.source_stream_id
    digest = semantic_sha256(payload)
    attestation = M11M7ProjectionAttestation(
        attestation_id=f"yfm11m7a-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **payload,
    )
    projection = M11MaterialRuntimeProjection(
        attestation=attestation,
        runtime=base.runtime,
    )
    roots = _low_level_roots()
    stream_id = attestation.source_stream_id
    backend = AdapterGate3Backend(
        repository_root=Path("/repo"),
        context=SimpleNamespace(),
        roots=roots,
        gate3_config=SimpleNamespace(),
        _calibration_ids={
            "lectra-m3-m4": (stream_id,) + tuple(f"unused-{i}" for i in range(7)),
            "loco-2dics": tuple(f"loco-{i}" for i in range(8)),
        },
        _confirmation_ids={
            "lectra-m3-m4": tuple(f"lectra-{i}" for i in range(20)),
            "loco-2dics": tuple(f"loco-con-{i}" for i in range(20)),
        },
        _streams={stream_id: SimpleNamespace(regime="recurrent")},
    )
    monkeypatch.setattr(impl, "project_stream", lambda *args, **kwargs: (projection,))
    monkeypatch.setattr(
        impl,
        "build_gate3_calibration_observation",
        lambda *, material_replays, **kwargs: material_replays[0],
    )

    material = backend.execute_calibration_stream(
        corpus_id="lectra-m3-m4",
        stream_id=stream_id,
        policy_id=policy_id,  # type: ignore[arg-type]
    )

    assert isinstance(material, Gate3CalibrationMaterialReplay)
    if policy_id == "age_regularity":
        assert material.execution_kind == "m7_replay"
        assert len(material.m7_applied_contexts) == 3
        assert tuple(item.catalog_action_id for item in material.m7_applied_contexts) == tuple(
            item.selected_action_id for item in material.shard_trace.decisions
        )
    else:
        assert material.execution_kind == "m9_two_ply_known_only"
        assert material.shard_trace.arm == "B"
        assert material.shard_trace.visibility == "known_only"
        assert len(material.m9_transitions) == 3
        assert tuple(
            item.applied_context.catalog_action_id for item in material.m9_transitions
        ) == tuple(item.selected_action_id for item in material.shard_trace.decisions)


def test_validity_delegates_authenticated_context_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = object()
    backend = _backend(roots=roots)
    lectra = SimpleNamespace(
        roots=roots,
        corpus_id="lectra-m3-m4",
        content_sha256="sha256:lectra-freeze",
        calibration_stream_ids=backend.calibration_stream_ids("lectra-m3-m4"),
    )
    loco = SimpleNamespace(
        roots=roots,
        corpus_id="loco-2dics",
        content_sha256="sha256:loco-freeze",
        calibration_stream_ids=backend.calibration_stream_ids("loco-2dics"),
    )
    receipt = SimpleNamespace(roots=roots)
    calls = []
    monkeypatch.setattr(impl, "_strict_roots", lambda value: value)
    monkeypatch.setattr(impl, "_strict_freeze", lambda value: value)

    def fake_delegate(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return receipt

    monkeypatch.setattr(impl, "_execute_validity_controls", fake_delegate)

    first = backend.execute_validity_controls(
        roots=roots,
        baseline_freezes=(lectra, loco),
    )
    second = backend.execute_validity_controls(
        roots=roots,
        baseline_freezes=(lectra, loco),
    )

    assert first is receipt and second is receipt
    assert calls == [
        {
            "context": backend.context,
            "roots": roots,
            "baseline_freezes": (lectra, loco),
        }
    ]
    with pytest.raises(AdapterGate3BackendError, match="corpus"):
        backend.execute_validity_controls(
            roots=roots,
            baseline_freezes=(loco, lectra),
        )


def _cached_validity_case(monkeypatch: pytest.MonkeyPatch):
    roots = object()
    backend = _backend(roots=roots)
    lectra = SimpleNamespace(
        roots=roots,
        corpus_id="lectra-m3-m4",
        content_sha256="sha256:" + "1" * 64,
        calibration_stream_ids=backend.calibration_stream_ids("lectra-m3-m4"),
    )
    loco = SimpleNamespace(
        roots=roots,
        corpus_id="loco-2dics",
        content_sha256="sha256:" + "2" * 64,
        calibration_stream_ids=backend.calibration_stream_ids("loco-2dics"),
    )
    receipt = SimpleNamespace(
        roots=roots,
        receipt_id="yfm11g3valid-" + "3" * 24,
        content_sha256="sha256:" + "4" * 64,
    )
    key = (lectra.content_sha256, loco.content_sha256)
    monkeypatch.setattr(impl, "_strict_roots", lambda value: value)
    monkeypatch.setattr(impl, "_strict_freeze", lambda value: value)
    backend._validity_cache[key] = receipt  # type: ignore[assignment]
    return roots, backend, (lectra, loco), receipt, key


def test_release_validity_controls_evidence_deletes_only_exact_cached_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots, backend, freezes, receipt, key = _cached_validity_case(monkeypatch)
    other_validity_key = ("sha256:" + "5" * 64, "sha256:" + "6" * 64)
    other_validity = object()
    backend._validity_cache[other_validity_key] = other_validity  # type: ignore[assignment]
    calibration_key = ("lectra-m3-m4", "lectra-cal-0", "remnant_first")
    projection_key = ("lectra-cal-0", "central", "remnant_first")
    central_key = ("lectra-con-0", "sha256:" + "7" * 64)
    backend._calibration_cache[calibration_key] = object()  # type: ignore[assignment]
    backend._projection_cache[projection_key] = (object(),)  # type: ignore[assignment]
    backend._central_cache[central_key] = object()  # type: ignore[assignment]

    backend.release_validity_controls_evidence(
        roots=roots,
        baseline_freezes=freezes,
        expected_receipt_id=receipt.receipt_id,
        expected_receipt_content_sha256=receipt.content_sha256,
    )

    assert key not in backend._validity_cache
    assert backend._validity_cache == {other_validity_key: other_validity}
    assert calibration_key in backend._calibration_cache
    assert projection_key in backend._projection_cache
    assert central_key in backend._central_cache


@pytest.mark.parametrize("mismatch", ("id", "sha", "freeze_order", "missing"))
def test_release_validity_controls_mismatch_releases_nothing(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    roots, backend, freezes, receipt, key = _cached_validity_case(monkeypatch)
    expected_id = receipt.receipt_id
    expected_sha = receipt.content_sha256
    selected_freezes = freezes
    if mismatch == "id":
        expected_id = "yfm11g3valid-" + "f" * 24
    elif mismatch == "sha":
        expected_sha = "sha256:" + "f" * 64
    elif mismatch == "freeze_order":
        selected_freezes = (freezes[1], freezes[0])
    else:
        del backend._validity_cache[key]

    with pytest.raises(
        AdapterGate3BackendError,
        match="validity (?:freezes|receipt|cache)",
    ):
        backend.release_validity_controls_evidence(
            roots=roots,
            baseline_freezes=selected_freezes,
            expected_receipt_id=expected_id,
            expected_receipt_content_sha256=expected_sha,
        )

    if mismatch != "missing":
        assert backend._validity_cache[key] is receipt
    else:
        assert key not in backend._validity_cache


def test_release_validity_controls_requires_keyword_arguments() -> None:
    backend = _backend()

    with pytest.raises(TypeError):
        backend.release_validity_controls_evidence(
            object(),  # type: ignore[misc]
            (),
            "yfm11g3valid-" + "1" * 24,
            "sha256:" + "2" * 64,
        )


def test_release_calibration_stream_evidence_removes_only_exact_cache_pair() -> None:
    backend = _backend()
    target_key = ("lectra-m3-m4", "lectra-cal-0", "remnant_first")
    other_calibration_key = ("lectra-m3-m4", "lectra-cal-1", "remnant_first")
    target_projection_key = ("lectra-cal-0", "central", "remnant_first")
    other_projection_key = ("lectra-cal-1", "central", "remnant_first")
    target = SimpleNamespace(
        observation_id="yfm11g3calobs-" + "1" * 24,
        content_sha256="sha256:" + "3" * 64,
    )
    unrelated = SimpleNamespace(
        observation_id="yfm11g3calobs-" + "2" * 24,
        content_sha256="sha256:" + "4" * 64,
    )
    backend._calibration_cache[target_key] = target  # type: ignore[assignment]
    backend._calibration_cache[other_calibration_key] = unrelated  # type: ignore[assignment]
    backend._projection_cache[target_projection_key] = (object(),)  # type: ignore[assignment]
    backend._projection_cache[other_projection_key] = (object(),)  # type: ignore[assignment]
    backend._central_cache[("lectra-con-0", "sha256:freeze")] = object()  # type: ignore[assignment]
    backend._validity_cache[("sha256:a", "sha256:b")] = object()  # type: ignore[assignment]

    backend.release_calibration_stream_evidence(
        corpus_id="lectra-m3-m4",
        stream_id="lectra-cal-0",
        policy_id="remnant_first",
        expected_observation_id=target.observation_id,
        expected_observation_content_sha256=target.content_sha256,
    )

    assert target_key not in backend._calibration_cache
    assert target_projection_key not in backend._projection_cache
    assert backend._calibration_cache == {other_calibration_key: unrelated}
    assert other_projection_key in backend._projection_cache
    assert ("lectra-con-0", "sha256:freeze") in backend._central_cache
    assert ("sha256:a", "sha256:b") in backend._validity_cache
    with pytest.raises(AdapterGate3BackendError, match="cached calibration observation"):
        backend.release_calibration_stream_evidence(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-cal-0",
            policy_id="remnant_first",
            expected_observation_id=target.observation_id,
            expected_observation_content_sha256=target.content_sha256,
        )


def test_release_calibration_stream_evidence_mismatch_releases_nothing() -> None:
    backend = _backend()
    cache_key = ("lectra-m3-m4", "lectra-cal-0", "remnant_first")
    projection_key = ("lectra-cal-0", "central", "remnant_first")
    observation = SimpleNamespace(
        observation_id="yfm11g3calobs-" + "1" * 24,
        content_sha256="sha256:" + "2" * 64,
    )
    projection = (object(),)
    backend._calibration_cache[cache_key] = observation  # type: ignore[assignment]
    backend._projection_cache[projection_key] = projection  # type: ignore[assignment]

    with pytest.raises(AdapterGate3BackendError, match="identity differs"):
        backend.release_calibration_stream_evidence(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-cal-0",
            policy_id="remnant_first",
            expected_observation_id="yfm11g3calobs-" + "f" * 24,
            expected_observation_content_sha256=observation.content_sha256,
        )
    assert backend._calibration_cache[cache_key] is observation
    assert backend._projection_cache[projection_key] is projection

    empty = _backend()
    empty._projection_cache[projection_key] = projection  # type: ignore[assignment]
    with pytest.raises(AdapterGate3BackendError, match="cached calibration observation"):
        empty.release_calibration_stream_evidence(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-cal-0",
            policy_id="remnant_first",
            expected_observation_id=observation.observation_id,
            expected_observation_content_sha256=observation.content_sha256,
        )
    assert empty._projection_cache[projection_key] is projection


def test_release_calibration_stream_evidence_requires_full_sha_before_mutation() -> None:
    backend = _backend()
    cache_key = ("lectra-m3-m4", "lectra-cal-0", "remnant_first")
    projection_key = ("lectra-cal-0", "central", "remnant_first")
    observation = SimpleNamespace(
        observation_id="yfm11g3calobs-" + "1" * 24,
        content_sha256="sha256:" + "2" * 64,
    )
    projection = (object(),)
    backend._calibration_cache[cache_key] = observation  # type: ignore[assignment]
    backend._projection_cache[projection_key] = projection  # type: ignore[assignment]

    with pytest.raises(AdapterGate3BackendError, match="identity differs"):
        backend.release_calibration_stream_evidence(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-cal-0",
            policy_id="remnant_first",
            expected_observation_id=observation.observation_id,
            expected_observation_content_sha256="sha256:" + "f" * 64,
        )

    assert backend._calibration_cache[cache_key] is observation
    assert backend._projection_cache[projection_key] is projection


def test_release_calibration_stream_evidence_arguments_are_keyword_only() -> None:
    backend = _backend()

    with pytest.raises(TypeError):
        backend.release_calibration_stream_evidence(
            "lectra-m3-m4",  # type: ignore[misc]
            "lectra-cal-0",
            "remnant_first",
            "yfm11g3calobs-" + "1" * 24,
            "sha256:" + "2" * 64,
        )


def test_release_calibration_stream_evidence_keeps_retry_handle_if_projection_pop_fails() -> None:
    class FailingProjectionCache(dict):
        def pop(self, key, default=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("injected projection release failure")

    backend = _backend()
    cache_key = ("lectra-m3-m4", "lectra-cal-0", "remnant_first")
    projection_key = ("lectra-cal-0", "central", "remnant_first")
    observation = SimpleNamespace(
        observation_id="yfm11g3calobs-" + "1" * 24,
        content_sha256="sha256:" + "2" * 64,
    )
    projection = (object(),)
    backend._calibration_cache[cache_key] = observation  # type: ignore[assignment]
    backend._projection_cache = FailingProjectionCache(  # type: ignore[assignment]
        {projection_key: projection}
    )

    with pytest.raises(RuntimeError, match="injected projection release failure"):
        backend.release_calibration_stream_evidence(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-cal-0",
            policy_id="remnant_first",
            expected_observation_id=observation.observation_id,
            expected_observation_content_sha256=observation.content_sha256,
        )

    assert backend._calibration_cache[cache_key] is observation
    assert backend._projection_cache[projection_key] is projection


def test_discard_incomplete_calibration_stream_evidence_removes_only_exact_projection() -> None:
    backend = _backend()
    target_projection_key = ("lectra-cal-0", "central", "remnant_first")
    other_projection_keys = (
        ("lectra-cal-0", "adverse", "remnant_first"),
        ("lectra-cal-0", "central", "known_only_m9_two_ply_scrap"),
        ("lectra-cal-1", "central", "remnant_first"),
    )
    target_projection = (object(),)
    backend._projection_cache[target_projection_key] = target_projection  # type: ignore[assignment]
    for key in other_projection_keys:
        backend._projection_cache[key] = (object(),)  # type: ignore[assignment]
    unrelated_calibration_key = (
        "lectra-m3-m4",
        "lectra-cal-1",
        "remnant_first",
    )
    unrelated_calibration = object()
    backend._calibration_cache[unrelated_calibration_key] = unrelated_calibration  # type: ignore[assignment]
    backend._central_cache[("lectra-con-0", "sha256:freeze")] = object()  # type: ignore[assignment]
    backend._validity_cache[("sha256:a", "sha256:b")] = object()  # type: ignore[assignment]
    expected_other_projections = {
        key: backend._projection_cache[key] for key in other_projection_keys
    }

    backend.discard_incomplete_calibration_stream_evidence(
        corpus_id="lectra-m3-m4",
        stream_id="lectra-cal-0",
        policy_id="remnant_first",
    )
    backend.discard_incomplete_calibration_stream_evidence(
        corpus_id="lectra-m3-m4",
        stream_id="lectra-cal-0",
        policy_id="remnant_first",
    )

    assert target_projection_key not in backend._projection_cache
    assert backend._projection_cache == expected_other_projections
    assert backend._calibration_cache == {
        unrelated_calibration_key: unrelated_calibration,
    }
    assert ("lectra-con-0", "sha256:freeze") in backend._central_cache
    assert ("sha256:a", "sha256:b") in backend._validity_cache


def test_discard_incomplete_calibration_stream_evidence_refuses_completed_observation() -> None:
    backend = _backend()
    calibration_key = ("lectra-m3-m4", "lectra-cal-0", "remnant_first")
    projection_key = ("lectra-cal-0", "central", "remnant_first")
    completed_observation = object()
    projection = (object(),)
    backend._calibration_cache[calibration_key] = completed_observation  # type: ignore[assignment]
    backend._projection_cache[projection_key] = projection  # type: ignore[assignment]

    with pytest.raises(AdapterGate3BackendError, match="completed calibration observation"):
        backend.discard_incomplete_calibration_stream_evidence(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-cal-0",
            policy_id="remnant_first",
        )

    assert backend._calibration_cache[calibration_key] is completed_observation
    assert backend._projection_cache[projection_key] is projection


def test_discard_incomplete_calibration_stream_evidence_validates_exact_tuple_and_policy() -> None:
    backend = _backend()
    projection_key = ("lectra-cal-0", "central", "remnant_first")
    projection = (object(),)
    backend._projection_cache[projection_key] = projection  # type: ignore[assignment]

    with pytest.raises(AdapterGate3BackendError, match="unregistered Gate 3 calibration"):
        backend.discard_incomplete_calibration_stream_evidence(
            corpus_id="loco-2dics",
            stream_id="lectra-cal-0",
            policy_id="remnant_first",
        )
    with pytest.raises(AdapterGate3BackendError, match="unregistered Gate 3 baseline policy"):
        backend.discard_incomplete_calibration_stream_evidence(
            corpus_id="lectra-m3-m4",
            stream_id="lectra-cal-0",
            policy_id="forged-policy",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        backend.discard_incomplete_calibration_stream_evidence(
            "lectra-m3-m4",  # type: ignore[misc]
            "lectra-cal-0",
            "remnant_first",
        )

    assert backend._projection_cache[projection_key] is projection


def _cached_central_case(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    roots = object()
    backend = _backend(roots=roots)
    freeze = SimpleNamespace(
        roots=roots,
        corpus_id="loco-2dics",
        freeze_id="yfm11g3bf-" + "1" * 24,
        content_sha256="sha256:" + "1" * 64,
        selected_policy_id="age_regularity",
        calibration_stream_ids=backend.calibration_stream_ids("loco-2dics"),
    )
    cell = SimpleNamespace(
        cell_id="yfm11g3cell-" + "2" * 24,
        content_sha256="sha256:" + "2" * 64,
    )
    monkeypatch.setattr(impl, "_strict_roots", lambda value: value)
    monkeypatch.setattr(impl, "_strict_freeze", lambda value: value)
    central_key = ("loco-con-0", freeze.content_sha256)
    projection_key = ("loco-con-0", "central", freeze.selected_policy_id)
    backend._central_cache[central_key] = cell  # type: ignore[assignment]
    backend._projection_cache[projection_key] = (object(),)  # type: ignore[assignment]
    return roots, backend, freeze, cell, central_key, projection_key


def test_release_central_stream_evidence_removes_only_exact_projection_then_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots, backend, freeze, cell, central_key, projection_key = _cached_central_case(monkeypatch)
    other_projection_key = ("loco-con-1", "central", "age_regularity")
    other_central_key = ("loco-con-1", freeze.content_sha256)
    other_projection = (object(),)
    other_cell = object()
    backend._projection_cache[other_projection_key] = other_projection  # type: ignore[assignment]
    backend._central_cache[other_central_key] = other_cell  # type: ignore[assignment]
    calibration_key = ("loco-2dics", "loco-cal-0", "age_regularity")
    validity_key = ("sha256:a", "sha256:b")
    backend._calibration_cache[calibration_key] = object()  # type: ignore[assignment]
    backend._validity_cache[validity_key] = object()  # type: ignore[assignment]

    backend.release_central_stream_evidence(
        roots=roots,
        corpus_id="loco-2dics",
        stream_id="loco-con-0",
        baseline_freeze=freeze,
        expected_cell_id=cell.cell_id,
        expected_cell_content_sha256=cell.content_sha256,
    )

    assert projection_key not in backend._projection_cache
    assert central_key not in backend._central_cache
    assert backend._projection_cache[other_projection_key] is other_projection
    assert backend._central_cache[other_central_key] is other_cell
    assert calibration_key in backend._calibration_cache
    assert validity_key in backend._validity_cache


@pytest.mark.parametrize("mismatch", ("id", "sha", "missing", "freeze", "stream"))
def test_release_central_stream_mismatch_releases_nothing(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    roots, backend, freeze, cell, central_key, projection_key = _cached_central_case(monkeypatch)
    expected_id = cell.cell_id
    expected_sha = cell.content_sha256
    selected_freeze = freeze
    selected_stream = "loco-con-0"
    if mismatch == "id":
        expected_id = "yfm11g3cell-" + "f" * 24
    elif mismatch == "sha":
        expected_sha = "sha256:" + "f" * 64
    elif mismatch == "missing":
        del backend._central_cache[central_key]
    elif mismatch == "freeze":
        selected_freeze = SimpleNamespace(
            **(vars(freeze) | {"calibration_stream_ids": tuple(f"forged-{i}" for i in range(8))})
        )
    else:
        selected_stream = "loco-cal-0"

    with pytest.raises(
        AdapterGate3BackendError,
        match="(?:central .*cell|central freeze|confirmation stream)",
    ):
        backend.release_central_stream_evidence(
            roots=roots,
            corpus_id="loco-2dics",
            stream_id=selected_stream,
            baseline_freeze=selected_freeze,
            expected_cell_id=expected_id,
            expected_cell_content_sha256=expected_sha,
        )

    if mismatch != "missing":
        assert backend._central_cache[central_key] is cell
    assert projection_key in backend._projection_cache


def test_release_central_keeps_retry_handle_if_projection_pop_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProjectionCache(dict):
        def pop(self, key, default=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("injected central projection release failure")

    roots, backend, freeze, cell, central_key, projection_key = _cached_central_case(monkeypatch)
    projection = backend._projection_cache[projection_key]
    backend._projection_cache = FailingProjectionCache(  # type: ignore[assignment]
        {projection_key: projection}
    )

    with pytest.raises(RuntimeError, match="central projection release failure"):
        backend.release_central_stream_evidence(
            roots=roots,
            corpus_id="loco-2dics",
            stream_id="loco-con-0",
            baseline_freeze=freeze,
            expected_cell_id=cell.cell_id,
            expected_cell_content_sha256=cell.content_sha256,
        )

    assert backend._central_cache[central_key] is cell
    assert backend._projection_cache[projection_key] is projection


def test_release_central_rejects_internally_inconsistent_cached_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots, backend, freeze, _cell, central_key, projection_key = _cached_central_case(monkeypatch)
    inconsistent = SimpleNamespace(
        cell_id="yfm11g3cell-" + "2" * 24,
        content_sha256="sha256:" + "3" * 64,
    )
    backend._central_cache[central_key] = inconsistent  # type: ignore[assignment]
    projection = backend._projection_cache[projection_key]

    with pytest.raises(AdapterGate3BackendError, match="identity differs"):
        backend.release_central_stream_evidence(
            roots=roots,
            corpus_id="loco-2dics",
            stream_id="loco-con-0",
            baseline_freeze=freeze,
            expected_cell_id=inconsistent.cell_id,
            expected_cell_content_sha256=inconsistent.content_sha256,
        )

    assert backend._central_cache[central_key] is inconsistent
    assert backend._projection_cache[projection_key] is projection


def test_discard_incomplete_central_stream_removes_only_exact_projection_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots, backend, freeze, _cell, central_key, projection_key = _cached_central_case(monkeypatch)
    del backend._central_cache[central_key]
    other_keys = (
        ("loco-con-0", "adverse", "age_regularity"),
        ("loco-con-0", "central", "remnant_first"),
        ("loco-con-1", "central", "age_regularity"),
    )
    others = {}
    for key in other_keys:
        value = (object(),)
        others[key] = value
        backend._projection_cache[key] = value  # type: ignore[assignment]
    unrelated_central_key = ("loco-con-1", freeze.content_sha256)
    unrelated_central = object()
    backend._central_cache[unrelated_central_key] = unrelated_central  # type: ignore[assignment]

    backend.discard_incomplete_central_stream_evidence(
        roots=roots,
        corpus_id="loco-2dics",
        stream_id="loco-con-0",
        baseline_freeze=freeze,
    )
    backend.discard_incomplete_central_stream_evidence(
        roots=roots,
        corpus_id="loco-2dics",
        stream_id="loco-con-0",
        baseline_freeze=freeze,
    )

    assert projection_key not in backend._projection_cache
    assert all(backend._projection_cache[key] is value for key, value in others.items())
    assert backend._central_cache[unrelated_central_key] is unrelated_central


def test_discard_incomplete_central_refuses_completed_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots, backend, freeze, cell, central_key, projection_key = _cached_central_case(monkeypatch)
    projection = backend._projection_cache[projection_key]

    with pytest.raises(AdapterGate3BackendError, match="completed central cell"):
        backend.discard_incomplete_central_stream_evidence(
            roots=roots,
            corpus_id="loco-2dics",
            stream_id="loco-con-0",
            baseline_freeze=freeze,
        )

    assert backend._central_cache[central_key] is cell
    assert backend._projection_cache[projection_key] is projection


def test_central_cache_lifecycle_methods_require_keyword_arguments() -> None:
    backend = _backend()
    with pytest.raises(TypeError):
        backend.release_central_stream_evidence(  # type: ignore[misc]
            object(),
            "loco-2dics",
            "loco-con-0",
            object(),
            "yfm11g3cell-" + "1" * 24,
            "sha256:" + "2" * 64,
        )
    with pytest.raises(TypeError):
        backend.discard_incomplete_central_stream_evidence(  # type: ignore[misc]
            object(),
            "loco-2dics",
            "loco-con-0",
            object(),
        )


def test_factory_consumes_authenticated_parents_and_reconstructs_context_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gate1_result = SimpleNamespace(
        result_id="gate1-result",
        content_sha256="sha256:gate1-result",
    )
    gate2_result = object()
    roots = SimpleNamespace(population_id="population", population_content_sha256="sha256:pop")
    config = SimpleNamespace(
        status="frozen_before_confirmation",
        confirmation_inputs_used=False,
        policy=SimpleNamespace(geometry_placement_search_maximum_candidates=256),
    )
    gate1 = SimpleNamespace(
        status="gate_1_survived",
        disposition="OPEN_GATE_2",
        run_id="gate1-run",
        content_sha256="sha256:gate1-run",
        gate1_result=gate1_result,
    )
    gate2 = SimpleNamespace(
        status="gate_2_survived",
        disposition="OPEN_GATE_3",
        evaluation_stage="stage_b_exact_attempted",
        blocking_error_count=0,
        gate1_run_id=gate1.run_id,
        gate1_run_content_sha256=gate1.content_sha256,
        gate1_result_id="gate1-result",
        gate1_result_content_sha256="sha256:gate1-result",
        gate2_result=gate2_result,
    )
    geometry_context = SimpleNamespace(
        search_config=SimpleNamespace(maximum_candidates=256),
    )
    context = SimpleNamespace(
        repository_root=tmp_path.resolve(),
        gate1_result=gate1_result,
        gate2_result=gate2_result,
        gate3_config=config,
        geometry_context=geometry_context,
        population=SimpleNamespace(
            population_id=roots.population_id,
            content_sha256=roots.population_content_sha256,
        ),
    )
    validator = SimpleNamespace(model_validate=lambda value, strict: value)
    monkeypatch.setattr(impl, "M11Gate1RunArtifact", validator)
    monkeypatch.setattr(impl, "M11Gate2RunArtifact", validator)
    monkeypatch.setattr(impl, "M11Gate3ConfirmationConfig", validator)
    monkeypatch.setattr(impl, "_strict_roots", lambda value: value)
    monkeypatch.setattr(impl, "_expected_roots", lambda **kwargs: roots)
    geometry_calls = []
    context_calls = []

    def fake_load_geometry(root):  # type: ignore[no-untyped-def]
        geometry_calls.append(root)
        return geometry_context

    def fake_context(**kwargs):  # type: ignore[no-untyped-def]
        context_calls.append(kwargs)
        return context

    monkeypatch.setattr(impl, "_load_official_gate2_context", fake_load_geometry)
    monkeypatch.setattr(impl, "_adapter_context_from_authenticated", fake_context)
    monkeypatch.setattr(
        impl,
        "_stream_registries",
        lambda value: (
            _backend()._calibration_ids,
            _backend()._confirmation_ids,
            _backend()._streams,
        ),
    )

    backend = build_adapter_gate3_backend(
        repository_root=tmp_path,
        gate1_artifact=gate1,
        gate2_artifact=gate2,
        gate3_config=config,
        roots=roots,
    )

    assert isinstance(backend, AdapterGate3Backend)
    assert backend.context is context
    assert geometry_calls == [tmp_path.resolve()]
    assert context_calls == [
        {
            "repository_root": tmp_path.resolve(),
            "geometry_context": geometry_context,
            "gate1_result": gate1_result,
            "gate2_result": gate2_result,
        }
    ]

    geometry_context.search_config.maximum_candidates = 255
    with pytest.raises(AdapterGate3BackendError, match="maximum.*Gate 3 config"):
        build_adapter_gate3_backend(
            repository_root=tmp_path,
            gate1_artifact=gate1,
            gate2_artifact=gate2,
            gate3_config=config,
            roots=roots,
        )
    geometry_context.search_config.maximum_candidates = 256

    monkeypatch.setattr(impl, "_expected_roots", lambda **kwargs: object())
    with pytest.raises(AdapterGate3BackendError, match="roots differ"):
        build_adapter_gate3_backend(
            repository_root=tmp_path,
            gate1_artifact=gate1,
            gate2_artifact=gate2,
            gate3_config=config,
            roots=roots,
        )
