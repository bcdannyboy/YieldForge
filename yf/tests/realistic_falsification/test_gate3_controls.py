from __future__ import annotations

from types import SimpleNamespace

import pytest

from yieldforge.realistic_falsification import gate3_controls

_CORPORA = ("lectra-m3-m4", "loco-2dics")
_NULL_KINDS = (
    "single_action",
    "unique_materials_single_action",
    "all_work_known_single_action",
)
_AUDIT_ARMS = ("central", "central", "adverse", "adverse", "null", "null")


def _inputs():  # type: ignore[no-untyped-def]
    roots = SimpleNamespace(
        contract_id="contract",
        contract_content_sha256="sha256:contract",
        population_id="population",
        population_content_sha256="sha256:population",
        gate1_evaluation_result_id="gate1",
        gate1_evaluation_result_content_sha256="sha256:gate1",
        gate2_evaluation_result_id="gate2",
        gate2_evaluation_result_content_sha256="sha256:gate2",
        gate3_config_id="gate3",
        gate3_config_content_sha256="sha256:gate3",
    )
    hard_nulls = tuple(
        SimpleNamespace(
            corpus_id=corpus_id,
            null_kind=null_kind,
            null_id=f"null:{corpus_id}:{ordinal}",
            source_stream_id=f"source:{corpus_id}:{ordinal}",
        )
        for corpus_id in _CORPORA
        for ordinal, null_kind in enumerate(_NULL_KINDS)
    )
    twins = tuple(
        SimpleNamespace(
            corpus_id=corpus_id,
            stream_id=f"twin:{corpus_id}:{ordinal:02d}",
            source_stream_id=f"source:{corpus_id}:{ordinal:02d}",
            stream_kind="shuffled_twin",
            partition="confirmation",
            no_signal_control=True,
            regime="mixed",
        )
        for corpus_id in _CORPORA
        for ordinal in range(20)
    )
    exact_audits = tuple(
        SimpleNamespace(
            corpus_id=corpus_id,
            audit_ordinal=ordinal,
            economic_arm=arm,
            audit_id=f"audit:{corpus_id}:{ordinal}",
            source_stream_id=f"source:{corpus_id}:{ordinal}",
        )
        for corpus_id in _CORPORA
        for ordinal, arm in enumerate(_AUDIT_ARMS)
    )
    population = SimpleNamespace(
        contract_id=roots.contract_id,
        contract_content_sha256=roots.contract_content_sha256,
        population_id=roots.population_id,
        content_sha256=roots.population_content_sha256,
        hard_nulls=hard_nulls,
        exact_audits=exact_audits,
        streams=twins,
    )
    context = SimpleNamespace(
        population=population,
        gate1_result=SimpleNamespace(
            result_id=roots.gate1_evaluation_result_id,
            content_sha256=roots.gate1_evaluation_result_content_sha256,
        ),
        gate2_result=SimpleNamespace(
            result_id=roots.gate2_evaluation_result_id,
            content_sha256=roots.gate2_evaluation_result_content_sha256,
        ),
        gate3_config=SimpleNamespace(
            config_id=roots.gate3_config_id,
            content_sha256=roots.gate3_config_content_sha256,
        ),
    )
    freezes = tuple(
        SimpleNamespace(
            roots=roots,
            corpus_id=corpus_id,
            selected_policy_id="age_regularity",
        )
        for corpus_id in _CORPORA
    )
    return context, roots, freezes


def _patch_execution(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    calls: dict[str, list[object]] = {
        "project": [],
        "execute": [],
        "evidence": [],
        "merge": [],
        "exact": [],
        "evaluate": [],
    }

    def projection(label: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            label=label,
            attestation=f"attestation:{label}",
            runtime=SimpleNamespace(replay_input=SimpleNamespace(instances=())),
        )

    def project(kind: str, identifier: str, arm: str | None = None):
        calls["project"].append((kind, identifier, arm))
        return (projection(f"{kind}:{identifier}"),)

    monkeypatch.setattr(
        gate3_controls,
        "project_hard_null",
        lambda context, null_id, arm, *, policy: project("hard-null", null_id, arm),
    )
    monkeypatch.setattr(
        gate3_controls,
        "project_stream",
        lambda context, stream_id, arm, *, policy: project("twin", stream_id, arm),
    )
    monkeypatch.setattr(
        gate3_controls,
        "project_exact_audit",
        lambda context, audit_id, *, policy: project("exact", audit_id),
    )
    monkeypatch.setattr(gate3_controls, "gate3_policy_identity", lambda value: f"m7:{value}")

    def execute(*, projection, roots, arm, policy_id):  # type: ignore[no-untyped-def]
        calls["execute"].append((projection.label, arm, policy_id))
        return SimpleNamespace(
            shard_trace=f"shard:{projection.label}:{arm}",
            steps=(),
        )

    monkeypatch.setattr(gate3_controls, "execute_gate3_material_shard", execute)

    def evidence(*, projection, execution, roots):  # type: ignore[no-untyped-def]
        calls["evidence"].append((projection.label, execution.shard_trace))
        return f"evidence:{execution.shard_trace}"

    monkeypatch.setattr(gate3_controls, "_build_projection_evidence", evidence)
    monkeypatch.setattr(
        gate3_controls,
        "build_gate3_hard_null_arm_trace",
        lambda **values: ("hard-arm", values["registration"].null_id, values["arm"]),
    )
    monkeypatch.setattr(
        gate3_controls,
        "build_gate3_hard_null_control",
        lambda **values: ("hard", values["registration"].null_id),
    )

    def merge(**values):  # type: ignore[no-untyped-def]
        calls["merge"].append(values)
        return f"arm:{values['stream_id']}:{values['arm']}"

    monkeypatch.setattr(gate3_controls, "merge_gate3_shard_traces", merge)
    monkeypatch.setattr(
        gate3_controls,
        "build_gate3_stream_cell",
        lambda **values: SimpleNamespace(
            stream_id=values["baseline"].split(":", 2)[1],
            corpus_id=values["baseline_freeze"].corpus_id,
        ),
    )
    monkeypatch.setattr(
        gate3_controls,
        "build_gate3_twin_control",
        lambda **values: ("twin", values["source_stream_id"], values["twin_cell"].stream_id),
    )
    monkeypatch.setattr(gate3_controls, "initial_m7_cursor", lambda value: "cursor")

    def exact_search(  # type: ignore[no-untyped-def]
        request,
        *,
        include_terminal_credit=True,
        action_catalog_requirement,
    ):
        calls["exact"].append((request, include_terminal_credit, action_catalog_requirement))
        return f"exact-result:{id(request.runtime)}"

    monkeypatch.setattr(gate3_controls, "solve_exact_search", exact_search)
    monkeypatch.setattr(
        gate3_controls,
        "build_gate3_exact_material_audit",
        lambda **values: ("exact-material", values["evidence"], values["exact_result"]),
    )
    monkeypatch.setattr(
        gate3_controls,
        "build_gate3_exact_audit_trace",
        lambda **values: ("exact-audit", values["registration"].audit_id),
    )
    receipt = object()

    def evaluate(**values):  # type: ignore[no-untyped-def]
        calls["evaluate"].append(values)
        return receipt

    monkeypatch.setattr(gate3_controls, "evaluate_gate3_validity_controls", evaluate)
    return calls, receipt


def test_execute_uses_each_canonical_registration_once_and_reuses_each_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, roots, freezes = _inputs()
    calls, expected = _patch_execution(monkeypatch)

    result = gate3_controls.execute_gate3_validity_controls(
        context=context,
        roots=roots,
        baseline_freezes=freezes,
    )

    assert result is expected
    assert len(calls["project"]) == 58
    assert tuple(kind for kind, _, _ in calls["project"]) == (
        ("hard-null",) * 6 + ("twin",) * 40 + ("exact",) * 12
    )
    assert len(calls["execute"]) == 150
    for kind, identifier, _arm in calls["project"][:46]:
        label = f"{kind}:{identifier}"
        assert tuple(arm for observed, arm, _policy in calls["execute"] if observed == label) == (
            "B",
            "F",
            "K",
        )
    assert all(arm == "F" for _label, arm, _policy in calls["execute"][-12:])
    assert len(calls["exact"]) == 12
    assert all(include_terminal_credit for _request, include_terminal_credit, _ in calls["exact"])
    assert {requirement for _request, _include_terminal_credit, requirement in calls["exact"]} == {
        "complete_over_all_actions_discovered_by_registered_bounded_m7_geometry_search"
    }
    evaluated = calls["evaluate"][0]
    assert len(evaluated["hard_nulls"]) == 6
    assert len(evaluated["twin_controls"]) == 40
    assert len(evaluated["exact_audits"]) == 12


@pytest.mark.parametrize("mutation", ("freeze_order", "hard_null_order", "population_root"))
def test_execute_rejects_noncanonical_inputs_before_projection(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    context, roots, freezes = _inputs()
    projected: list[object] = []
    monkeypatch.setattr(
        gate3_controls,
        "project_hard_null",
        lambda *args, **kwargs: projected.append((args, kwargs)),
    )
    if mutation == "freeze_order":
        freezes = tuple(reversed(freezes))
    elif mutation == "hard_null_order":
        context.population.hard_nulls = tuple(reversed(context.population.hard_nulls))
    else:
        context.population.content_sha256 = "sha256:wrong"

    with pytest.raises(gate3_controls.Gate3ControlsError):
        gate3_controls.execute_gate3_validity_controls(
            context=context,
            roots=roots,
            baseline_freezes=freezes,
        )

    assert projected == []


def test_projection_evidence_records_the_exact_known_only_runtime_per_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_runtime = SimpleNamespace(replay_input=SimpleNamespace(instances=("e0", "e1", "e2")))
    projection = SimpleNamespace(
        runtime=base_runtime,
        attestation=SimpleNamespace(
            known_visible_local_prefixes=(
                SimpleNamespace(visible_local_event_positions=(1,)),
                SimpleNamespace(visible_local_event_positions=(1, 2)),
                SimpleNamespace(visible_local_event_positions=(2,)),
            )
        ),
    )
    decisions = tuple(
        SimpleNamespace(
            decision_id=f"decision-{position}",
            algorithm="m9_two_ply",
            visibility="known_only",
        )
        for position in range(3)
    )
    execution = SimpleNamespace(
        shard_trace=SimpleNamespace(decisions=decisions),
        steps=tuple(SimpleNamespace(cursor=f"after-{position}") for position in range(3)),
    )
    runtime_calls: list[object] = []
    receipt_calls: list[object] = []
    monkeypatch.setattr(gate3_controls, "initial_m7_cursor", lambda value: "initial")

    def known_runtime(*, projection, cursor, local_event_position):  # type: ignore[no-untyped-def]
        runtime_calls.append((cursor, local_event_position))
        return f"masked-{local_event_position}"

    monkeypatch.setattr(gate3_controls, "build_gate3_known_only_runtime", known_runtime)

    def receipt(**values):  # type: ignore[no-untyped-def]
        receipt_calls.append(values)
        return f"receipt:{values['decision'].decision_id}"

    monkeypatch.setattr(gate3_controls, "build_gate3_decision_runtime_receipt", receipt)
    monkeypatch.setattr(
        gate3_controls,
        "build_gate3_projection_shard_evidence",
        lambda **values: values,
    )

    evidence = gate3_controls._build_projection_evidence(
        projection=projection,
        execution=execution,
        roots="roots",
    )

    assert runtime_calls == [("initial", 0), ("after-0", 1), ("after-1", 2)]
    assert (
        tuple(item["runtime_role"] for item in receipt_calls) == ("known_only_physical_mask",) * 3
    )
    assert tuple(item["retained_local_event_positions"] for item in receipt_calls) == (
        (0, 1),
        (0, 1, 2),
        (0, 1, 2),
    )
    assert evidence["decision_runtime_receipts"] == (
        "receipt:decision-0",
        "receipt:decision-1",
        "receipt:decision-2",
    )


def test_exact_control_propagates_structural_search_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, roots, freezes = _inputs()
    _patch_execution(monkeypatch)

    def fail_structurally(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("structurally incomplete bounded action graph")

    monkeypatch.setattr(gate3_controls, "solve_exact_search", fail_structurally)

    with pytest.raises(RuntimeError, match="structurally incomplete"):
        gate3_controls.execute_gate3_validity_controls(
            context=context,
            roots=roots,
            baseline_freezes=freezes,
        )
