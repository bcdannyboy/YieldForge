"""Authenticated adapter-backed implementation of the M11 Gate 3 backend.

The official runner authenticates the parent artifacts before calling this
factory.  This module repeats the semantic root checks, obtains the private
adapter capability, and then packages low-level M7/M9 executions into the
confirmation models.  It never derives aggregate decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from yieldforge.realistic_falsification.adapter import (
    M11M7AdapterContext,
    M11MaterialRuntimeProjection,
    _adapter_context_from_authenticated,
    project_stream,
)
from yieldforge.realistic_falsification.confirmation import (
    GATE3_BASELINE_POLICY_IDS,
    Gate3BaselineCalibrationFreeze,
    Gate3BaselinePolicyId,
    Gate3CalibrationMaterialReplay,
    Gate3CalibrationObservation,
    Gate3ConfirmationBackend,
    Gate3CorpusId,
    Gate3RootBinding,
    Gate3StreamCell,
    Gate3ValidityReceipt,
    build_gate3_applied_action_context,
    build_gate3_calibration_m9_transition,
    build_gate3_calibration_material_replay,
    build_gate3_calibration_observation,
    build_gate3_root_binding,
    build_gate3_stream_cell,
)
from yieldforge.realistic_falsification.gate3_backend import (
    execute_gate3_material_shard,
    gate3_policy_identity,
    merge_gate3_shard_traces,
)
from yieldforge.realistic_falsification.gate3_contracts import (
    M11Gate3ConfirmationConfig,
)
from yieldforge.realistic_falsification.gate3_runner import (
    gate3_adapter_runtime_config_sha256,
)
from yieldforge.realistic_falsification.geometry_gate import (
    _load_official_gate2_context,
)
from yieldforge.realistic_falsification.geometry_runner import (
    M11Gate2RunArtifact,
)
from yieldforge.realistic_falsification.pack import M11Stream
from yieldforge.realistic_falsification.runner import M11Gate1RunArtifact

_CORPUS_ORDER: tuple[Gate3CorpusId, Gate3CorpusId] = (
    "lectra-m3-m4",
    "loco-2dics",
)
_Regime = Literal["recurrent", "mixed", "high_mix", "regime_shift"]


class AdapterGate3BackendError(ValueError):
    """Authenticated Gate 3 backend construction or execution failed closed."""


def _strict_roots(roots: Gate3RootBinding) -> Gate3RootBinding:
    try:
        return Gate3RootBinding.model_validate(
            roots.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (TypeError, ValueError) as error:
        raise AdapterGate3BackendError("Gate 3 backend roots are malformed") from error


def _strict_freeze(
    freeze: Gate3BaselineCalibrationFreeze,
) -> Gate3BaselineCalibrationFreeze:
    try:
        return Gate3BaselineCalibrationFreeze.model_validate(
            freeze.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (TypeError, ValueError) as error:
        raise AdapterGate3BackendError("Gate 3 baseline freeze is malformed") from error


def _expected_roots(
    *,
    gate1: M11Gate1RunArtifact,
    gate2: M11Gate2RunArtifact,
    config: M11Gate3ConfirmationConfig,
) -> Gate3RootBinding:
    return build_gate3_root_binding(
        contract_id=gate1.contract_id,
        contract_content_sha256=gate1.contract_content_sha256,
        population_id=gate1.population_id,
        population_content_sha256=gate1.population_content_sha256,
        gate1_run_id=gate1.run_id,
        gate1_run_content_sha256=gate1.content_sha256,
        gate1_evaluation_result_id=gate1.gate1_result.result_id,
        gate1_evaluation_result_content_sha256=gate1.gate1_result.content_sha256,
        gate2_run_id=gate2.run_id,
        gate2_run_content_sha256=gate2.content_sha256,
        gate2_evaluation_result_id=gate2.gate2_result.result_id,
        gate2_evaluation_result_content_sha256=gate2.gate2_result.content_sha256,
        gate3_config_id=config.config_id,
        gate3_config_content_sha256=config.content_sha256,
        adapter_runtime_config_sha256=gate3_adapter_runtime_config_sha256(config),
    )


def _stream_registries(
    context: M11M7AdapterContext,
) -> tuple[
    dict[Gate3CorpusId, tuple[str, ...]],
    dict[Gate3CorpusId, tuple[str, ...]],
    dict[str, M11Stream],
]:
    corpora = context.gate1_result.contract.corpora
    corpus_ids = tuple(item.source.corpus_id for item in corpora)
    if corpus_ids != _CORPUS_ORDER:
        raise AdapterGate3BackendError("Gate 3 corpus registry differs from frozen order")
    calibration = {
        cast(Gate3CorpusId, item.source.corpus_id): tuple(item.calibration_stream_ids)
        for item in corpora
    }
    confirmation = {
        cast(Gate3CorpusId, item.source.corpus_id): tuple(item.confirmation_stream_ids)
        for item in corpora
    }
    if any(len(ids) != 8 or len(set(ids)) != 8 for ids in calibration.values()) or any(
        len(ids) != 20 or len(set(ids)) != 20 for ids in confirmation.values()
    ):
        raise AdapterGate3BackendError("Gate 3 stream registry has an invalid census")
    admitted = tuple(
        stream_id
        for corpus_id in _CORPUS_ORDER
        for stream_id in calibration[corpus_id] + confirmation[corpus_id]
    )
    if len(set(admitted)) != 56:
        raise AdapterGate3BackendError("Gate 3 registered streams are not disjoint")
    streams = {item.stream_id: item for item in context.population.streams}
    if len(streams) != len(context.population.streams):
        raise AdapterGate3BackendError("Gate 3 source population repeats a stream identity")
    for corpus_id in _CORPUS_ORDER:
        for partition, ids in (
            ("calibration", calibration[corpus_id]),
            ("confirmation", confirmation[corpus_id]),
        ):
            for stream_id in ids:
                stream = streams.get(stream_id)
                if (
                    stream is None
                    or stream.corpus_id != corpus_id
                    or stream.partition != partition
                    or stream.stream_kind != "primary"
                    or len(stream.events) != 24
                ):
                    raise AdapterGate3BackendError(
                        "Gate 3 stream registry differs from the authenticated population"
                    )
    return calibration, confirmation, streams


def _execute_validity_controls(
    *,
    context: M11M7AdapterContext,
    roots: Gate3RootBinding,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> Gate3ValidityReceipt:
    # The controls implementation is intentionally a separate authority and is
    # imported only when the runner reaches the frozen validity stage.
    from yieldforge.realistic_falsification.gate3_controls import (
        execute_gate3_validity_controls,
    )

    return execute_gate3_validity_controls(
        context=context,
        roots=roots,
        baseline_freezes=baseline_freezes,
    )


@dataclass(slots=True)
class AdapterGate3Backend:
    """Concrete authenticated backend consumed by early Gate 3 confirmation."""

    repository_root: Path
    context: M11M7AdapterContext
    roots: Gate3RootBinding
    gate3_config: M11Gate3ConfirmationConfig
    _calibration_ids: dict[Gate3CorpusId, tuple[str, ...]]
    _confirmation_ids: dict[Gate3CorpusId, tuple[str, ...]]
    _streams: dict[str, M11Stream]
    _projection_cache: dict[
        tuple[str, Literal["central", "adverse"], Gate3BaselinePolicyId],
        tuple[M11MaterialRuntimeProjection, ...],
    ] = field(default_factory=dict, repr=False)
    _calibration_cache: dict[
        tuple[Gate3CorpusId, str, Gate3BaselinePolicyId],
        Gate3CalibrationObservation,
    ] = field(default_factory=dict, repr=False)
    _central_cache: dict[tuple[str, str], Gate3StreamCell] = field(
        default_factory=dict,
        repr=False,
    )
    _validity_cache: dict[tuple[str, str], Gate3ValidityReceipt] = field(
        default_factory=dict,
        repr=False,
    )

    def _require_corpus(self, corpus_id: Gate3CorpusId) -> Gate3CorpusId:
        if corpus_id not in _CORPUS_ORDER:
            raise AdapterGate3BackendError("unregistered Gate 3 corpus")
        return corpus_id

    def _require_stream(
        self,
        *,
        corpus_id: Gate3CorpusId,
        stream_id: str,
        partition: Literal["calibration", "confirmation"],
    ) -> M11Stream:
        corpus = self._require_corpus(corpus_id)
        registry = self._calibration_ids if partition == "calibration" else self._confirmation_ids
        if stream_id not in registry[corpus]:
            raise AdapterGate3BackendError(f"unregistered Gate 3 {partition} stream for corpus")
        return self._streams[stream_id]

    def _require_policy(self, policy_id: str) -> Gate3BaselinePolicyId:
        if policy_id not in GATE3_BASELINE_POLICY_IDS:
            raise AdapterGate3BackendError("unregistered Gate 3 baseline policy")
        return cast(Gate3BaselinePolicyId, policy_id)

    def _require_roots(self, roots: Gate3RootBinding) -> None:
        if _strict_roots(roots) != self.roots:
            raise AdapterGate3BackendError("Gate 3 execution roots differ from backend roots")

    def _project_once(
        self,
        *,
        stream_id: str,
        economic_arm: Literal["central", "adverse"],
        policy_id: Gate3BaselinePolicyId,
    ) -> tuple[M11MaterialRuntimeProjection, ...]:
        key = (stream_id, economic_arm, policy_id)
        cached = self._projection_cache.get(key)
        if cached is not None:
            return cached
        projections = project_stream(
            self.context,
            stream_id,
            economic_arm,
            policy=gate3_policy_identity(policy_id),
        )
        if not projections:
            raise AdapterGate3BackendError("Gate 3 stream projected no material shards")
        self._projection_cache[key] = projections
        return projections

    def calibration_stream_ids(self, corpus_id: Gate3CorpusId) -> tuple[str, ...]:
        return self._calibration_ids[self._require_corpus(corpus_id)]

    def confirmation_stream_ids(self, corpus_id: Gate3CorpusId) -> tuple[str, ...]:
        return self._confirmation_ids[self._require_corpus(corpus_id)]

    def execute_calibration_stream(
        self,
        *,
        corpus_id: Gate3CorpusId,
        stream_id: str,
        policy_id: Gate3BaselinePolicyId,
    ) -> Gate3CalibrationObservation:
        self._require_stream(
            corpus_id=corpus_id,
            stream_id=stream_id,
            partition="calibration",
        )
        policy = self._require_policy(policy_id)
        cache_key = (corpus_id, stream_id, policy)
        cached = self._calibration_cache.get(cache_key)
        if cached is not None:
            return cached
        projections = self._project_once(
            stream_id=stream_id,
            economic_arm="central",
            policy_id=policy,
        )
        material_replays: list[Gate3CalibrationMaterialReplay] = []
        is_m9 = policy == "known_only_m9_two_ply_scrap"
        for projection in projections:
            execution = execute_gate3_material_shard(
                projection=projection,
                roots=self.roots,
                arm="B",
                policy_id=policy,
            )
            if is_m9:
                if (
                    execution.shard_trace.arm != "B"
                    or execution.shard_trace.visibility != "known_only"
                    or any(
                        item.algorithm != "m9_two_ply" for item in execution.shard_trace.decisions
                    )
                ):
                    raise AdapterGate3BackendError(
                        "Gate 3 additional calibration baseline is not known-only M9"
                    )
                transitions = tuple(
                    build_gate3_calibration_m9_transition(
                        decision=decision,
                        step=step,
                    )
                    for decision, step in zip(
                        execution.shard_trace.decisions,
                        execution.steps,
                        strict=True,
                    )
                )
                material = build_gate3_calibration_material_replay(
                    roots=self.roots,
                    corpus_id=corpus_id,
                    stream_id=stream_id,
                    policy_id=policy,
                    projection_attestation=projection.attestation,
                    replay_input=projection.runtime.replay_input,
                    shard_trace=execution.shard_trace,
                    m9_transitions=transitions,
                    m9_terminal=execution.terminal.terminal,
                )
            else:
                if (
                    execution.m7_replay_result is None
                    or execution.shard_trace.arm != "B"
                    or execution.shard_trace.visibility != "released_only"
                    or any(
                        item.algorithm != "m7_policy" for item in execution.shard_trace.decisions
                    )
                ):
                    raise AdapterGate3BackendError(
                        "Gate 3 M7 calibration replay differs from the baseline arm"
                    )
                material = build_gate3_calibration_material_replay(
                    roots=self.roots,
                    corpus_id=corpus_id,
                    stream_id=stream_id,
                    policy_id=policy,
                    projection_attestation=projection.attestation,
                    replay_input=projection.runtime.replay_input,
                    shard_trace=execution.shard_trace,
                    m7_replay_result=execution.m7_replay_result,
                    m7_applied_contexts=tuple(
                        build_gate3_applied_action_context(
                            decision=decision,
                            step=step,
                        )
                        for decision, step in zip(
                            execution.shard_trace.decisions,
                            execution.steps,
                            strict=True,
                        )
                    ),
                )
            material_replays.append(material)
        observation = build_gate3_calibration_observation(
            roots=self.roots,
            corpus_id=corpus_id,
            stream_id=stream_id,
            policy_id=policy,
            material_replays=tuple(material_replays),
        )
        self._calibration_cache[cache_key] = observation
        return observation

    def execute_validity_controls(
        self,
        *,
        roots: Gate3RootBinding,
        baseline_freezes: tuple[
            Gate3BaselineCalibrationFreeze,
            Gate3BaselineCalibrationFreeze,
        ],
    ) -> Gate3ValidityReceipt:
        self._require_roots(roots)
        if len(baseline_freezes) != 2:
            raise AdapterGate3BackendError("Gate 3 validity requires two baseline freezes")
        freezes = tuple(_strict_freeze(item) for item in baseline_freezes)
        if (
            tuple(item.corpus_id for item in freezes) != _CORPUS_ORDER
            or any(item.roots != self.roots for item in freezes)
            or any(
                item.calibration_stream_ids != self._calibration_ids[item.corpus_id]
                for item in freezes
            )
        ):
            raise AdapterGate3BackendError(
                "Gate 3 validity freezes differ from backend roots, corpus, or stream registry"
            )
        typed_freezes = cast(
            tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze],
            freezes,
        )
        key = tuple(item.content_sha256 for item in typed_freezes)
        cached = self._validity_cache.get(key)
        if cached is not None:
            return cached
        receipt = _execute_validity_controls(
            context=self.context,
            roots=self.roots,
            baseline_freezes=typed_freezes,
        )
        if receipt.roots != self.roots:
            raise AdapterGate3BackendError("Gate 3 validity receipt differs from backend roots")
        self._validity_cache[key] = receipt
        return receipt

    def execute_central_stream(
        self,
        *,
        roots: Gate3RootBinding,
        corpus_id: Gate3CorpusId,
        stream_id: str,
        baseline_freeze: Gate3BaselineCalibrationFreeze,
    ) -> Gate3StreamCell:
        self._require_roots(roots)
        stream = self._require_stream(
            corpus_id=corpus_id,
            stream_id=stream_id,
            partition="confirmation",
        )
        freeze = _strict_freeze(baseline_freeze)
        if (
            freeze.roots != self.roots
            or freeze.corpus_id != corpus_id
            or freeze.calibration_stream_ids != self._calibration_ids[corpus_id]
        ):
            raise AdapterGate3BackendError(
                "Gate 3 central freeze differs from backend roots, corpus, or stream registry"
            )
        policy = self._require_policy(freeze.selected_policy_id)
        cache_key = (stream_id, freeze.content_sha256)
        cached = self._central_cache.get(cache_key)
        if cached is not None:
            return cached

        # Project exactly once: B, F, and physically masked K all consume the
        # same authenticated projection objects and therefore share private
        # geometry preparation/search caches without weakening source binding.
        projections = self._project_once(
            stream_id=stream_id,
            economic_arm="central",
            policy_id=policy,
        )
        arm_traces = {}
        for arm in ("B", "F", "K"):
            executions = tuple(
                execute_gate3_material_shard(
                    projection=projection,
                    roots=self.roots,
                    arm=arm,
                    policy_id=policy,
                )
                for projection in projections
            )
            arm_traces[arm] = merge_gate3_shard_traces(
                roots=self.roots,
                stream_id=stream_id,
                corpus_id=corpus_id,
                regime=cast(_Regime, stream.regime),
                arm=arm,
                policy_id=policy,
                shards=tuple(item.shard_trace for item in executions),
            )
        cell = build_gate3_stream_cell(
            roots=self.roots,
            baseline_freeze=freeze,
            baseline=arm_traces["B"],
            full_future=arm_traces["F"],
            known_only=arm_traces["K"],
        )
        self._central_cache[cache_key] = cell
        return cell


def build_adapter_gate3_backend(
    *,
    repository_root: Path,
    gate1_artifact: M11Gate1RunArtifact,
    gate2_artifact: M11Gate2RunArtifact,
    gate3_config: M11Gate3ConfirmationConfig,
    roots: Gate3RootBinding,
) -> Gate3ConfirmationBackend:
    """Construct a backend only from exact surviving official parents."""

    root = Path(repository_root).resolve()
    try:
        gate1 = M11Gate1RunArtifact.model_validate(gate1_artifact, strict=True)
        gate2 = M11Gate2RunArtifact.model_validate(gate2_artifact, strict=True)
        config = M11Gate3ConfirmationConfig.model_validate(gate3_config, strict=True)
    except (TypeError, ValueError) as error:
        raise AdapterGate3BackendError("Gate 3 backend parent evidence is malformed") from error
    strict_roots = _strict_roots(roots)
    if (
        gate1.status != "gate_1_survived"
        or gate1.disposition != "OPEN_GATE_2"
        or gate2.status != "gate_2_survived"
        or gate2.disposition != "OPEN_GATE_3"
        or gate2.evaluation_stage != "stage_b_exact_attempted"
        or gate2.blocking_error_count != 0
        or gate2.gate1_run_id != gate1.run_id
        or gate2.gate1_run_content_sha256 != gate1.content_sha256
        or gate2.gate1_result_id != gate1.gate1_result.result_id
        or gate2.gate1_result_content_sha256 != gate1.gate1_result.content_sha256
        or config.status != "frozen_before_confirmation"
        or config.confirmation_inputs_used
    ):
        raise AdapterGate3BackendError(
            "Gate 3 backend requires exact surviving outcome-blind parents"
        )
    if strict_roots != _expected_roots(gate1=gate1, gate2=gate2, config=config):
        raise AdapterGate3BackendError("Gate 3 backend roots differ from parent evidence")
    try:
        geometry_context = _load_official_gate2_context(root)
        context = _adapter_context_from_authenticated(
            repository_root=root,
            geometry_context=geometry_context,
            gate1_result=gate1.gate1_result,
            gate2_result=gate2.gate2_result,
        )
    except (OSError, TypeError, ValueError) as error:
        raise AdapterGate3BackendError("Gate 3 adapter authentication failed") from error
    if (
        context.repository_root != root
        or context.gate1_result != gate1.gate1_result
        or context.gate2_result != gate2.gate2_result
        or context.gate3_config != config
        or context.population.population_id != strict_roots.population_id
        or context.population.content_sha256 != strict_roots.population_content_sha256
    ):
        raise AdapterGate3BackendError(
            "Gate 3 adapter context differs from the authenticated parent roots"
        )
    runtime_maximum = context.geometry_context.search_config.maximum_candidates
    configured_maximum = config.policy.geometry_placement_search_maximum_candidates
    if runtime_maximum != configured_maximum:
        raise AdapterGate3BackendError(
            "Gate 3 runtime geometry search maximum differs from the Gate 3 config"
        )
    calibration, confirmation, streams = _stream_registries(context)
    return AdapterGate3Backend(
        repository_root=root,
        context=context,
        roots=strict_roots,
        gate3_config=config,
        _calibration_ids=calibration,
        _confirmation_ids=confirmation,
        _streams=streams,
    )


__all__ = [
    "AdapterGate3Backend",
    "AdapterGate3BackendError",
    "build_adapter_gate3_backend",
]
