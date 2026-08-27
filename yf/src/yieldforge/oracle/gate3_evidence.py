"""Pure fail-closed evidence contracts for M8 Gate-3 calibration.

The module does not execute an oracle, a mutation, or a process. It binds and
reconciles evidence produced by separately authorized execution paths.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, NoReturn, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from yieldforge.baseline.contracts import BaselineContractModel
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.experiment import (
    M8AuditActionBinding,
    M8CertificateProofResult,
    M8PortableFactGate3Result,
    audit_sample_sha256,
)
from yieldforge.oracle.facts import M8CanonicalF64, M8Sha256
from yieldforge.temporal_benchmark.contracts import TemporalRegime

_PARENT_V3_PROOF_ID = "yfm8proof-b296ba919c07d55ece14c6db"
_PARENT_V3_CONTENT_SHA256 = (
    "sha256:b296ba919c07d55ece14c6dbb6ecbce1aa4a24e612dd1a251757e7a3b739739d"
)
_AUDIT_RANK_DOMAIN = b"yieldforge.m8.gate3.cost-blind-audit-rank.v2\0"
_PARENT_ARTIFACT_BYTE_CAP = 16 * 1024 * 1024
_HELD_OUT_ACTION_COUNT = 550_542
_HELD_OUT_MEAN_FUTURE_EVENT_COUNT = 11.5
_PROJECTION_SAFETY_FACTOR = 2.0
_SECONDS_PER_DAY = 86_400.0
_REFERENCE_SLOT_COUNT = 8
_MINIMUM_REFERENCE_SPEEDUP = 25.0
_MAXIMUM_PROJECTED_DAYS = 5.0

type M8Gate3Regime = Literal[
    TemporalRegime.NO_SIGNAL,
    TemporalRegime.REGIME_SHIFT,
]
type M8Gate3EventClassification = Literal[
    "state_rejoin",
    "no_fit",
    "policy_dominated",
    "exact_transition",
]
type M8Gate3ProofFailureCode = Literal[
    "valid",
    "invalid_proof",
    "runtime_binding_mismatch",
    "start_state_mismatch",
    "suffix_mismatch",
    "action_catalog_mismatch",
    "initial_action_mismatch",
    "witness_mismatch",
    "terminal_mismatch",
]


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_model[ModelT: BaselineContractModel](
    value: ModelT,
    expected_type: type[ModelT],
    *,
    label: str,
) -> ModelT:
    if type(value) is not expected_type:
        raise TypeError(f"{label} requires an exact {expected_type.__name__}")
    return expected_type.model_validate_json(value.model_dump_json(), strict=True)


def _regime_index(regime: TemporalRegime) -> int:
    if regime is TemporalRegime.NO_SIGNAL:
        return 0
    if regime is TemporalRegime.REGIME_SHIFT:
        return 1
    raise ValueError("M8 Gate-3 evidence contains a non-target regime")


def load_parent_v3_certificate_proof(path: Path) -> M8CertificateProofResult:
    """Strict-load the exact committed parent-v3 artifact by content identity."""

    source = Path(path)
    if not source.is_file() or source.stat().st_size > _PARENT_ARTIFACT_BYTE_CAP:
        raise ValueError("M8 Gate-3 parent-v3 artifact is absent or exceeds its byte cap")
    parent = M8CertificateProofResult.model_validate_json(
        source.read_bytes(),
        strict=True,
    )
    if parent.proof_id != _PARENT_V3_PROOF_ID or parent.content_sha256 != _PARENT_V3_CONTENT_SHA256:
        raise ValueError("M8 Gate-3 parent-v3 artifact differs from the committed freeze")
    return parent


class M8Gate3CheckedActionRoot(BaselineContractModel):
    """Compact identity of one v2 root accepted by the full Task-7 checker."""

    schema_version: Literal["yieldforge.m8-gate3-checked-action-root.v2"] = (
        "yieldforge.m8-gate3-checked-action-root.v2"
    )
    source_root_schema_version: Literal["yieldforge.m8-action-root.v2"] = (
        "yieldforge.m8-action-root.v2"
    )
    checked: Literal[True] = True
    regime: M8Gate3Regime
    temporal_seed: Literal[2026082300]
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    source_bundle_sha256: M8Sha256
    checker_decision_id: StrictStr = Field(pattern=r"^yfm8d-[0-9a-f]{24}$")
    checker_decision_content_sha256: M8Sha256
    root_fact_sha256: M8Sha256
    action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    catalog_action_id: StrictStr = Field(min_length=1)
    baseline_action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    baseline_catalog_action_id: StrictStr = Field(min_length=1)
    start_event_position: Literal[0] = 0
    stop_event_position: Literal[2] = 2
    suffix_sha256: M8Sha256
    semantic_runtime_sha256: M8Sha256
    start_state_sha256: M8Sha256
    initial_state_after_sha256: M8Sha256
    final_state_sha256: M8Sha256

    @property
    def is_baseline(self) -> bool:
        return self.action_id == self.baseline_action_id

    @property
    def observed_action_event_count(self) -> int:
        return self.stop_event_position - self.start_event_position - 1

    @property
    def audit_rank_sha256(self) -> str:
        payload = {
            "parent_v3_proof_id": _PARENT_V3_PROOF_ID,
            "parent_v3_content_sha256": _PARENT_V3_CONTENT_SHA256,
            "regime": self.regime.value,
            "temporal_seed": self.temporal_seed,
            "stream_id": self.stream_id,
            "semantic_runtime_sha256": self.semantic_runtime_sha256,
            "suffix_sha256": self.suffix_sha256,
            "baseline_status": self.is_baseline,
            "catalog_action_id": self.catalog_action_id,
        }
        digest = hashlib.sha256(_AUDIT_RANK_DOMAIN + _canonical_bytes(payload)).hexdigest()
        return f"sha256:{digest}"

    @model_validator(mode="after")
    def require_root_context(self) -> Self:
        if (self.action_id == self.baseline_action_id) != (
            self.catalog_action_id == self.baseline_catalog_action_id
        ):
            raise ValueError("M8 Gate-3 baseline materialized and catalog identities differ")
        if self.stop_event_position < self.start_event_position + 1:
            raise ValueError("M8 Gate-3 checked root has an invalid suffix interval")
        return self


def _root_order(
    root: M8Gate3CheckedActionRoot,
) -> tuple[int, str, str]:
    return (
        _regime_index(root.regime),
        root.catalog_action_id,
        root.root_fact_sha256,
    )


def gate3_checked_root_sequence_sha256(
    checked_action_roots: tuple[M8Gate3CheckedActionRoot, ...],
) -> str:
    """Commit to the canonical compact root sequence without claiming membership."""

    roots = tuple(
        _strict_model(
            item,
            M8Gate3CheckedActionRoot,
            label="M8 Gate-3 checked-root sequence",
        )
        for item in checked_action_roots
    )
    if len(roots) != 887:
        raise ValueError("M8 Gate-3 checked-root sequence requires exactly 887 roots")
    ordered = tuple(sorted(roots, key=_root_order))
    payload = {"roots": tuple(item.model_dump(mode="json") for item in ordered)}
    return f"sha256:{semantic_sha256(payload)}"


class M8Gate3RootMembershipBinding(BaselineContractModel):
    """One Task-7 cell identity retained by the external membership extractor."""

    regime: M8Gate3Regime
    source_bundle_sha256: M8Sha256
    source_semantic_bundle_bytes_sha256: M8Sha256
    checker_decision_id: StrictStr = Field(pattern=r"^yfm8d-[0-9a-f]{24}$")
    checker_decision_content_sha256: M8Sha256
    checked_root_count: StrictInt = Field(gt=0)


class M8Gate3RootMembershipAttestation(BaselineContractModel):
    """External evidence that the compact sequence was extracted from checked bundles.

    The source bytes are deliberately not embedded or reverified by this pure layer.
    """

    schema_version: Literal["yieldforge.m8-gate3-root-membership-attestation.v1"] = (
        "yieldforge.m8-gate3-root-membership-attestation.v1"
    )
    attestation_id: StrictStr = Field(pattern=r"^yfm8g3membership-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    portable_gate3_id: StrictStr = Field(pattern=r"^yfm8gate3-[0-9a-f]{24}$")
    portable_gate3_content_sha256: M8Sha256
    producer_id: StrictStr = Field(min_length=1)
    producer_content_sha256: M8Sha256
    runtime_id: StrictStr = Field(min_length=1)
    runtime_content_sha256: M8Sha256
    bindings: tuple[M8Gate3RootMembershipBinding, M8Gate3RootMembershipBinding]
    checked_root_count: Literal[887] = 887
    checked_root_sequence_sha256: M8Sha256
    source_verification_scope: Literal[
        "external_strict_canonical_bundle_and_checked_result_membership"
    ] = "external_strict_canonical_bundle_and_checked_result_membership"
    canonical_bundle_bytes_retained_by_executor: Literal[True] = True
    checked_result_retained_by_executor: Literal[True] = True
    producer_exit_code: Literal[0] = 0
    surviving_descendant_count: Literal[0] = 0
    surviving_registry_count: Literal[0] = 0
    evaluation_accessed: Literal[False] = False
    claim_ceiling: Literal[
        "executor_attested_checked_bundle_membership_only_source_bytes_not_embedded_or_"
        "reverified_here"
    ] = (
        "executor_attested_checked_bundle_membership_only_source_bytes_not_embedded_or_"
        "reverified_here"
    )

    @model_validator(mode="after")
    def require_attestation_identity(self) -> Self:
        if tuple(item.regime for item in self.bindings) != (
            TemporalRegime.NO_SIGNAL,
            TemporalRegime.REGIME_SHIFT,
        ) or tuple(item.checked_root_count for item in self.bindings) != (428, 459):
            raise ValueError("M8 Gate-3 membership bindings differ from the two-probe freeze")
        digest = semantic_sha256(
            self,
            excluded_fields={"attestation_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.attestation_id != (
            f"yfm8g3membership-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 membership attestation content identity differs")
        return self


class M8Gate3CheckedRootManifest(BaselineContractModel):
    """Complete compact manifest of all 887 roots accepted by Task 7."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-gate3-checked-root-manifest.v1"] = (
        "yieldforge.m8-gate3-checked-root-manifest.v1"
    )
    manifest_id: StrictStr = Field(pattern=r"^yfm8g3roots-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    portable_gate3_id: StrictStr = Field(pattern=r"^yfm8gate3-[0-9a-f]{24}$")
    portable_gate3_content_sha256: M8Sha256
    checked_root_count: Literal[887] = 887
    no_signal_checked_root_count: Literal[428] = 428
    regime_shift_checked_root_count: Literal[459] = 459
    observed_action_event_count: StrictInt = Field(gt=0)
    membership_attestation: M8Gate3RootMembershipAttestation
    roots: tuple[M8Gate3CheckedActionRoot, ...] = Field(
        min_length=887,
        max_length=887,
    )
    evaluation_accessed: Literal[False] = False
    claim_ceiling: Literal[
        "executor_attested_complete_checked_root_identity_manifest_source_bytes_not_embedded_"
        "not_evaluation_or_m8_result"
    ] = (
        "executor_attested_complete_checked_root_identity_manifest_source_bytes_not_embedded_"
        "not_evaluation_or_m8_result"
    )

    @model_validator(mode="after")
    def require_complete_manifest_and_identity(self) -> Self:
        if self.roots != tuple(sorted(self.roots, key=_root_order)):
            raise ValueError("M8 Gate-3 checked roots are not in canonical order")
        if (
            sum(item.regime is TemporalRegime.NO_SIGNAL for item in self.roots) != 428
            or sum(item.regime is TemporalRegime.REGIME_SHIFT for item in self.roots) != 459
        ):
            raise ValueError("M8 Gate-3 checked-root regime counts differ")
        if (
            len({(item.regime, item.catalog_action_id) for item in self.roots}) != 887
            or len({(item.regime, item.action_id) for item in self.roots}) != 887
            or len({item.root_fact_sha256 for item in self.roots}) != 887
        ):
            raise ValueError("M8 Gate-3 checked-root identities are not unique")
        if self.observed_action_event_count != sum(
            item.observed_action_event_count for item in self.roots
        ):
            raise ValueError("M8 Gate-3 observed action-event count differs from roots")
        if (
            self.membership_attestation.portable_gate3_id != self.portable_gate3_id
            or self.membership_attestation.portable_gate3_content_sha256
            != self.portable_gate3_content_sha256
            or self.membership_attestation.checked_root_sequence_sha256
            != gate3_checked_root_sequence_sha256(self.roots)
        ):
            raise ValueError("M8 Gate-3 root manifest differs from membership evidence")
        for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT):
            roots = tuple(item for item in self.roots if item.regime is regime)
            if sum(item.is_baseline for item in roots) != 1:
                raise ValueError("M8 Gate-3 checked-root manifest lacks a unique baseline")
            contexts = {
                (
                    item.baseline_action_id,
                    item.baseline_catalog_action_id,
                    item.start_event_position,
                    item.stop_event_position,
                    item.suffix_sha256,
                    item.semantic_runtime_sha256,
                    item.start_state_sha256,
                )
                for item in roots
            }
            if len(contexts) != 1:
                raise ValueError("M8 Gate-3 checked roots differ from one probe context")
        digest = semantic_sha256(
            self,
            excluded_fields={"manifest_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.manifest_id != (
            f"yfm8g3roots-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 checked-root manifest content identity differs")
        return self


def _canonical_checked_roots(
    gate3: M8PortableFactGate3Result,
    roots: tuple[M8Gate3CheckedActionRoot, ...],
) -> tuple[M8Gate3CheckedActionRoot, ...]:
    if len(roots) != 887:
        raise ValueError("M8 Gate-3 manifest requires exactly 887 checked roots")
    cell_by_regime = {item.regime: item for item in gate3.cells}
    expected_counts = {
        TemporalRegime.NO_SIGNAL: 428,
        TemporalRegime.REGIME_SHIFT: 459,
    }
    for regime, expected_count in expected_counts.items():
        candidates = tuple(item for item in roots if item.regime is regime)
        if len(candidates) != expected_count:
            raise ValueError("M8 Gate-3 manifest requires exactly 428 and 459 roots")
        cell = cell_by_regime[regime]
        if any(
            (
                item.temporal_seed,
                item.stream_id,
                item.source_bundle_sha256,
                item.checker_decision_id,
                item.checker_decision_content_sha256,
            )
            != (
                cell.temporal_seed,
                cell.stream_id,
                cell.first_bundle_sha256,
                cell.decision_id,
                cell.decision_content_sha256,
            )
            for item in candidates
        ):
            raise ValueError("M8 Gate-3 checked root differs from its authoritative cell")
    ordered = tuple(sorted(roots, key=_root_order))
    if (
        len({(item.regime, item.catalog_action_id) for item in ordered}) != 887
        or len({(item.regime, item.action_id) for item in ordered}) != 887
        or len({item.root_fact_sha256 for item in ordered}) != 887
    ):
        raise ValueError("M8 Gate-3 checked roots contain duplicate identities")
    return ordered


def freeze_gate3_checked_root_manifest(
    portable_fact_gate3: M8PortableFactGate3Result,
    checked_action_roots: tuple[M8Gate3CheckedActionRoot, ...],
    *,
    membership_attestation: M8Gate3RootMembershipAttestation | None = None,
) -> M8Gate3CheckedRootManifest:
    """Bind the complete checked-root universe and derive action-event work."""

    gate3 = _strict_model(
        portable_fact_gate3,
        M8PortableFactGate3Result,
        label="M8 Gate-3 checked-root manifest",
    )
    roots = tuple(
        _strict_model(
            item,
            M8Gate3CheckedActionRoot,
            label="M8 Gate-3 checked-root manifest",
        )
        for item in checked_action_roots
    )
    ordered = _canonical_checked_roots(gate3, roots)
    if membership_attestation is None:
        raise ValueError("M8 Gate-3 requires external membership attestation evidence")
    membership = _strict_model(
        membership_attestation,
        M8Gate3RootMembershipAttestation,
        label="M8 Gate-3 checked-root membership",
    )
    expected_bindings = tuple(
        (
            cell.regime,
            cell.first_bundle_sha256,
            cell.first_semantic_bundle_bytes_sha256,
            cell.decision_id,
            cell.decision_content_sha256,
            cell.checked_action_root_count,
        )
        for cell in gate3.cells
    )
    observed_bindings = tuple(
        (
            item.regime,
            item.source_bundle_sha256,
            item.source_semantic_bundle_bytes_sha256,
            item.checker_decision_id,
            item.checker_decision_content_sha256,
            item.checked_root_count,
        )
        for item in membership.bindings
    )
    if (
        membership.portable_gate3_id != gate3.gate3_id
        or membership.portable_gate3_content_sha256 != gate3.content_sha256
        or observed_bindings != expected_bindings
        or membership.checked_root_sequence_sha256 != gate3_checked_root_sequence_sha256(ordered)
    ):
        raise ValueError("M8 Gate-3 external membership root sequence differs")
    observed = sum(item.observed_action_event_count for item in ordered)
    if observed <= 0:
        raise ValueError("M8 Gate-3 checked roots contain no observed action-events")
    semantic = {
        "schema_version": "yieldforge.m8-gate3-checked-root-manifest.v1",
        "portable_gate3_id": gate3.gate3_id,
        "portable_gate3_content_sha256": gate3.content_sha256,
        "checked_root_count": 887,
        "no_signal_checked_root_count": 428,
        "regime_shift_checked_root_count": 459,
        "observed_action_event_count": observed,
        "membership_attestation": membership.model_dump(mode="json"),
        "roots": tuple(item.model_dump(mode="json") for item in ordered),
        "evaluation_accessed": False,
        "claim_ceiling": (
            "executor_attested_complete_checked_root_identity_manifest_source_bytes_not_"
            "embedded_not_evaluation_or_m8_result"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8Gate3CheckedRootManifest(
        manifest_id=f"yfm8g3roots-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        portable_gate3_id=gate3.gate3_id,
        portable_gate3_content_sha256=gate3.content_sha256,
        observed_action_event_count=observed,
        membership_attestation=membership,
        roots=ordered,
    )


class M8Gate3AuditSampleAction(M8Gate3CheckedActionRoot):
    """One selected checked root plus its outcome-blind rank."""

    selection_rank_sha256: M8Sha256

    @model_validator(mode="after")
    def require_rank(self) -> Self:
        if self.selection_rank_sha256 != self.audit_rank_sha256:
            raise ValueError("M8 Gate-3 selected action rank differs")
        return self


def _sample_action_order(
    action: M8Gate3AuditSampleAction,
) -> tuple[int, int, str, str]:
    return (
        _regime_index(action.regime),
        0 if action.is_baseline else 1,
        action.selection_rank_sha256,
        action.catalog_action_id,
    )


def _target_parent_bindings(
    parent: M8CertificateProofResult,
) -> tuple[
    M8AuditActionBinding,
    M8AuditActionBinding,
    M8AuditActionBinding,
    M8AuditActionBinding,
]:
    selected = tuple(
        item
        for item in parent.audit_bindings
        if item.regime in {TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT}
    )
    if len(selected) != 4 or tuple(item.regime for item in selected) != (
        TemporalRegime.NO_SIGNAL,
        TemporalRegime.NO_SIGNAL,
        TemporalRegime.REGIME_SHIFT,
        TemporalRegime.REGIME_SHIFT,
    ):
        raise ValueError("M8 Gate-3 parent-v3 target-regime bindings differ")
    return selected  # type: ignore[return-value]


class M8Gate3AuditSample(BaselineContractModel):
    """New 12-action sample; no v3 proof or sampled action is inherited."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-gate3-audit-sample.v2"] = (
        "yieldforge.m8-gate3-audit-sample.v2"
    )
    sample_id: StrictStr = Field(pattern=r"^yfm8g3sample-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    parent_v3_proof_id: Literal["yfm8proof-b296ba919c07d55ece14c6db"] = _PARENT_V3_PROOF_ID
    parent_v3_content_sha256: Literal[
        "sha256:b296ba919c07d55ece14c6dbb6ecbce1aa4a24e612dd1a251757e7a3b739739d"
    ] = _PARENT_V3_CONTENT_SHA256
    parent_v3_target_regime_binding_count: Literal[4] = 4
    parent_v3_total_binding_count: Literal[12] = 12
    parent_v3_target_regime_bindings: tuple[
        M8AuditActionBinding,
        M8AuditActionBinding,
        M8AuditActionBinding,
        M8AuditActionBinding,
    ]
    parent_v3_target_regime_binding_sha256: M8Sha256
    inherited_parent_proof_count: Literal[0] = 0
    parent_v3_sample_reused: Literal[False] = False
    root_manifest_id: StrictStr = Field(pattern=r"^yfm8g3roots-[0-9a-f]{24}$")
    root_manifest_content_sha256: M8Sha256
    source_root_count: Literal[887] = 887
    selected_action_count: Literal[12] = 12
    actions: tuple[M8Gate3AuditSampleAction, ...] = Field(
        min_length=12,
        max_length=12,
    )
    selection_semantics: Literal[
        "unique_baseline_then_five_lowest_pre_outcome_sha256_ranks_per_probe"
    ] = "unique_baseline_then_five_lowest_pre_outcome_sha256_ranks_per_probe"
    rank_excludes: Literal[
        "source_bundle_root_fact_checker_decision_timing_final_cost_savings_reference_result_"
        "and_input_order"
    ] = (
        "source_bundle_root_fact_checker_decision_timing_final_cost_savings_reference_result_"
        "and_input_order"
    )
    claim_ceiling: Literal[
        "calibration_audit_sample_identity_only_not_inherited_proof_evaluation_or_m8_result"
    ] = "calibration_audit_sample_identity_only_not_inherited_proof_evaluation_or_m8_result"

    @model_validator(mode="after")
    def require_sample_shape_and_identity(self) -> Self:
        if self.actions != tuple(sorted(self.actions, key=_sample_action_order)):
            raise ValueError("M8 Gate-3 sample actions are not in canonical order")
        if (
            len(
                {
                    (item.regime, item.catalog_action_id, item.root_fact_sha256)
                    for item in self.actions
                }
            )
            != 12
        ):
            raise ValueError("M8 Gate-3 sample actions are not unique")
        for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT):
            actions = tuple(item for item in self.actions if item.regime is regime)
            if (
                len(actions) != 6
                or not actions[0].is_baseline
                or any(item.is_baseline for item in actions[1:])
            ):
                raise ValueError("M8 Gate-3 sample requires baseline plus five per probe")
        if tuple(item.regime for item in self.parent_v3_target_regime_bindings) != (
            TemporalRegime.NO_SIGNAL,
            TemporalRegime.NO_SIGNAL,
            TemporalRegime.REGIME_SHIFT,
            TemporalRegime.REGIME_SHIFT,
        ) or self.parent_v3_target_regime_binding_sha256 != audit_sample_sha256(
            self.parent_v3_target_regime_bindings
        ):
            raise ValueError("M8 Gate-3 parent target-regime binding evidence differs")
        digest = semantic_sha256(
            self,
            excluded_fields={"sample_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.sample_id != (
            f"yfm8g3sample-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 sample content identity differs")
        return self


def freeze_gate3_audit_sample(
    parent_v3: M8CertificateProofResult,
    root_manifest: M8Gate3CheckedRootManifest,
) -> M8Gate3AuditSample:
    """Freeze one cost-blind sample from the complete checked-root manifest."""

    parent = _strict_model(
        parent_v3,
        M8CertificateProofResult,
        label="M8 Gate-3 sample freeze",
    )
    if parent.proof_id != _PARENT_V3_PROOF_ID or parent.content_sha256 != _PARENT_V3_CONTENT_SHA256:
        raise ValueError("M8 Gate-3 parent-v3 content differs from the committed freeze")
    manifest = _strict_model(
        root_manifest,
        M8Gate3CheckedRootManifest,
        label="M8 Gate-3 sample freeze",
    )
    target_bindings = _target_parent_bindings(parent)
    selected: list[M8Gate3AuditSampleAction] = []
    for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT):
        candidates = tuple(item for item in manifest.roots if item.regime is regime)
        baseline = tuple(item for item in candidates if item.is_baseline)
        if len(baseline) != 1:
            raise ValueError("M8 Gate-3 sample lacks one unique baseline")
        ranked = tuple(
            sorted(
                (item for item in candidates if not item.is_baseline),
                key=lambda item: (
                    item.audit_rank_sha256,
                    item.catalog_action_id,
                ),
            )[:5]
        )
        selected.extend(
            M8Gate3AuditSampleAction(
                **item.model_dump(mode="python"),
                selection_rank_sha256=item.audit_rank_sha256,
            )
            for item in (baseline[0], *ranked)
        )
    actions = tuple(sorted(selected, key=_sample_action_order))
    semantic = {
        "schema_version": "yieldforge.m8-gate3-audit-sample.v2",
        "parent_v3_proof_id": parent.proof_id,
        "parent_v3_content_sha256": parent.content_sha256,
        "parent_v3_target_regime_binding_count": 4,
        "parent_v3_total_binding_count": len(parent.audit_bindings),
        "parent_v3_target_regime_bindings": tuple(
            item.model_dump(mode="json") for item in target_bindings
        ),
        "parent_v3_target_regime_binding_sha256": audit_sample_sha256(target_bindings),
        "inherited_parent_proof_count": 0,
        "parent_v3_sample_reused": False,
        "root_manifest_id": manifest.manifest_id,
        "root_manifest_content_sha256": manifest.content_sha256,
        "source_root_count": 887,
        "selected_action_count": 12,
        "actions": tuple(item.model_dump(mode="json") for item in actions),
        "selection_semantics": (
            "unique_baseline_then_five_lowest_pre_outcome_sha256_ranks_per_probe"
        ),
        "rank_excludes": (
            "source_bundle_root_fact_checker_decision_timing_final_cost_savings_reference_result_"
            "and_input_order"
        ),
        "claim_ceiling": (
            "calibration_audit_sample_identity_only_not_inherited_proof_evaluation_or_m8_result"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8Gate3AuditSample(
        sample_id=f"yfm8g3sample-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        parent_v3_target_regime_bindings=target_bindings,
        root_manifest_id=manifest.manifest_id,
        root_manifest_content_sha256=manifest.content_sha256,
        actions=actions,
        parent_v3_target_regime_binding_sha256=audit_sample_sha256(target_bindings),
    )


class M8Gate3NormalizedPolicyEvidence(BaselineContractModel):
    action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    catalog_action_id: StrictStr = Field(min_length=1)
    decision_key: tuple[StrictStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_action_key(self) -> Self:
        if f"action_id={self.catalog_action_id}" not in self.decision_key:
            raise ValueError("M8 Gate-3 normalized policy key omits its action")
        return self


class M8Gate3NormalizedInfluenceEvidence(BaselineContractModel):
    remnant_id: StrictStr = Field(pattern=r"^yfrm-[0-9a-f]{24}$")
    candidate_id: StrictStr | None = Field(default=None, min_length=1)
    classification: Literal["no_fit", "policy_dominated"]
    evidence_sha256: M8Sha256
    common_policy: M8Gate3NormalizedPolicyEvidence
    competing_policy: M8Gate3NormalizedPolicyEvidence | None = None

    @model_validator(mode="after")
    def require_influence_shape(self) -> Self:
        if self.classification == "no_fit" and self.competing_policy is not None:
            raise ValueError("M8 Gate-3 no-fit influence carries a competing policy")
        if self.classification == "policy_dominated" and (
            self.candidate_id is None or self.competing_policy is None
        ):
            raise ValueError("M8 Gate-3 dominated influence lacks policy evidence")
        if (
            self.classification == "policy_dominated"
            and self.competing_policy is not None
            and (
                self.competing_policy.action_id == self.common_policy.action_id
                or self.competing_policy.catalog_action_id == self.common_policy.catalog_action_id
            )
        ):
            raise ValueError("M8 Gate-3 dominated influence lacks a distinct competitor")
        return self


class M8Gate3NormalizedEventEvidence(BaselineContractModel):
    event_position: StrictInt = Field(ge=0)
    classification: M8Gate3EventClassification
    common_action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    branch_action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    state_before_sha256: M8Sha256
    state_after_sha256: M8Sha256
    influences: tuple[M8Gate3NormalizedInfluenceEvidence, ...] = ()

    @model_validator(mode="after")
    def require_event_shape(self) -> Self:
        if self.classification in {"state_rejoin", "exact_transition"}:
            if self.influences:
                raise ValueError("M8 Gate-3 exact/rejoin event carries influences")
            if (
                self.classification == "state_rejoin"
                and self.common_action_id != self.branch_action_id
            ):
                raise ValueError("M8 Gate-3 state-rejoin event has differing actions")
            return self
        if self.common_action_id != self.branch_action_id or not self.influences:
            raise ValueError("M8 Gate-3 certified event lacks matching action evidence")
        observed = {item.classification for item in self.influences}
        if self.classification == "no_fit" and observed != {"no_fit"}:
            raise ValueError("M8 Gate-3 no-fit event has conflicting influences")
        if self.classification == "policy_dominated" and not (
            observed <= {"no_fit", "policy_dominated"} and "policy_dominated" in observed
        ):
            raise ValueError("M8 Gate-3 dominated event lacks dominated influence")
        if any(item.common_policy.action_id != self.common_action_id for item in self.influences):
            raise ValueError("M8 Gate-3 influence differs from its common action")
        influence_keys = tuple(
            (
                item.remnant_id,
                item.candidate_id,
                (item.competing_policy.action_id if item.competing_policy is not None else None),
                (
                    item.competing_policy.catalog_action_id
                    if item.competing_policy is not None
                    else None
                ),
            )
            for item in self.influences
        )
        if len(influence_keys) != len(set(influence_keys)):
            raise ValueError("M8 Gate-3 normalized influences are not unique")
        return self


class M8Gate3NormalizedActionRecord(BaselineContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    catalog_action_id: StrictStr = Field(min_length=1)
    baseline_action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    baseline_catalog_action_id: StrictStr = Field(min_length=1)
    start_event_position: StrictInt = Field(ge=0)
    stop_event_position: StrictInt = Field(ge=1)
    suffix_sha256: M8Sha256
    semantic_runtime_sha256: M8Sha256
    start_state_sha256: M8Sha256
    initial_state_after_sha256: M8Sha256
    final_state_sha256: M8Sha256
    ordered_event_evidence: tuple[M8Gate3NormalizedEventEvidence, ...]
    final_net_cost_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_complete_suffix(self) -> Self:
        expected = tuple(range(self.start_event_position + 1, self.stop_event_position))
        if tuple(item.event_position for item in self.ordered_event_evidence) != expected:
            raise ValueError("M8 Gate-3 normalized suffix coverage differs")
        if self.ordered_event_evidence and (
            self.ordered_event_evidence[0].state_before_sha256 != self.initial_state_after_sha256
            or self.ordered_event_evidence[-1].state_after_sha256 != self.final_state_sha256
            or any(
                left.state_after_sha256 != right.state_before_sha256
                for left, right in zip(
                    self.ordered_event_evidence,
                    self.ordered_event_evidence[1:],
                    strict=False,
                )
            )
        ):
            raise ValueError("M8 Gate-3 normalized state chain differs")
        if (
            not self.ordered_event_evidence
            and self.initial_state_after_sha256 != self.final_state_sha256
        ):
            raise ValueError("M8 Gate-3 empty normalized suffix state differs")
        return self


def _require_normalized_root_context(
    action: M8Gate3AuditSampleAction,
    normalized: M8Gate3NormalizedActionRecord,
) -> None:
    if (
        normalized.action_id,
        normalized.catalog_action_id,
        normalized.baseline_action_id,
        normalized.baseline_catalog_action_id,
        normalized.start_event_position,
        normalized.stop_event_position,
        normalized.suffix_sha256,
        normalized.semantic_runtime_sha256,
        normalized.start_state_sha256,
        normalized.initial_state_after_sha256,
        normalized.final_state_sha256,
    ) != (
        action.action_id,
        action.catalog_action_id,
        action.baseline_action_id,
        action.baseline_catalog_action_id,
        action.start_event_position,
        action.stop_event_position,
        action.suffix_sha256,
        action.semantic_runtime_sha256,
        action.start_state_sha256,
        action.initial_state_after_sha256,
        action.final_state_sha256,
    ):
        raise ValueError("M8 Gate-3 normalized record differs from its root context")


def normalized_gate3_action_semantic_sha256(
    normalized: M8Gate3NormalizedActionRecord,
) -> str:
    strict = _strict_model(
        normalized,
        M8Gate3NormalizedActionRecord,
        label="M8 Gate-3 normalized action",
    )
    payload = strict.model_dump(mode="json")
    payload.pop("final_net_cost_bits")
    return f"sha256:{semantic_sha256(payload)}"


class M8Gate3AuditComputationIdentity(BaselineContractModel):
    """Bound external implementation, runtime, and output identity for one audit arm."""

    role: Literal["v1_generator", "v1_checker", "checked_v2", "reference"]
    implementation_id: StrictStr = Field(min_length=1)
    implementation_content_sha256: M8Sha256
    runtime_id: StrictStr = Field(min_length=1)
    runtime_content_sha256: M8Sha256
    output_content_sha256: M8Sha256
    worker_exit_code: Literal[0] = 0
    evaluation_accessed: Literal[False] = False
    evidence_scope: Literal["external_independent_output_artifact"] = (
        "external_independent_output_artifact"
    )


class M8Gate3V1GeneratorAuditRecord(BaselineContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    action: M8Gate3AuditSampleAction
    computation: M8Gate3AuditComputationIdentity
    generated_proof_id: StrictStr = Field(pattern=r"^yfm8ap-[0-9a-f]{24}$")
    generated_proof_content_sha256: M8Sha256
    normalized: M8Gate3NormalizedActionRecord

    @model_validator(mode="after")
    def require_generated_proof_binding(self) -> Self:
        if (
            self.computation.role != "v1_generator"
            or self.computation.output_content_sha256 != self.generated_proof_content_sha256
        ):
            raise ValueError("M8 Gate-3 v1 generator computation identity differs")
        if self.generated_proof_id != (
            f"yfm8ap-{self.generated_proof_content_sha256.removeprefix('sha256:')[:24]}"
        ):
            raise ValueError("M8 Gate-3 generated proof ID and SHA differ")
        _require_normalized_root_context(self.action, self.normalized)
        return self


class M8Gate3V1CheckerAuditRecord(BaselineContractModel):
    action: M8Gate3AuditSampleAction
    computation: M8Gate3AuditComputationIdentity
    checked_proof_id: StrictStr = Field(pattern=r"^yfm8ap-[0-9a-f]{24}$")
    checked_proof_content_sha256: M8Sha256
    checked_semantic_sha256: M8Sha256
    checked_final_net_cost_bits: M8CanonicalF64
    checker_valid: StrictBool
    checked_event_count: StrictInt = Field(ge=0)
    certificate_count: StrictInt = Field(ge=0)
    exact_transition_count: StrictInt = Field(ge=0)
    failure_code: M8Gate3ProofFailureCode

    @model_validator(mode="after")
    def require_checker_status(self) -> Self:
        if self.computation.role != "v1_checker":
            raise ValueError("M8 Gate-3 v1 checker computation identity differs")
        if self.checker_valid != (self.failure_code == "valid"):
            raise ValueError("M8 Gate-3 v1 checker validity differs from its failure code")
        if self.checked_proof_id != (
            f"yfm8ap-{self.checked_proof_content_sha256.removeprefix('sha256:')[:24]}"
        ):
            raise ValueError("M8 Gate-3 checked proof ID and SHA differ")
        return self


class M8Gate3CheckedV2AuditRecord(BaselineContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    action: M8Gate3AuditSampleAction
    computation: M8Gate3AuditComputationIdentity
    normalized: M8Gate3NormalizedActionRecord

    @model_validator(mode="after")
    def require_checked_v2_context(self) -> Self:
        if (
            self.computation.role != "checked_v2"
            or self.computation.output_content_sha256 != self.action.root_fact_sha256
        ):
            raise ValueError("M8 Gate-3 checked-v2 computation identity differs")
        _require_normalized_root_context(self.action, self.normalized)
        return self


class M8Gate3ReferenceCostAttestation(BaselineContractModel):
    """Exact reference attestation of cost only."""

    computation: M8Gate3AuditComputationIdentity
    regime: M8Gate3Regime
    action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    catalog_action_id: StrictStr = Field(min_length=1)
    root_fact_sha256: M8Sha256
    final_net_cost_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_reference_identity(self) -> Self:
        if self.computation.role != "reference":
            raise ValueError("M8 Gate-3 reference computation identity differs")
        return self


def _action_key(value) -> tuple[TemporalRegime, str, str, str]:  # type: ignore[no-untyped-def]
    action = value.action if hasattr(value, "action") else value
    return (
        action.regime,
        action.action_id,
        action.catalog_action_id,
        action.root_fact_sha256,
    )


class M8Gate3ActionAuditComparison(BaselineContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    action: M8Gate3AuditSampleAction
    v1_generator: M8Gate3V1GeneratorAuditRecord
    v1_checker: M8Gate3V1CheckerAuditRecord
    checked_v2: M8Gate3CheckedV2AuditRecord
    reference: M8Gate3ReferenceCostAttestation
    independent_computations: StrictBool
    proof_binding_match: StrictBool
    semantic_match: StrictBool
    cost_match: StrictBool
    all_match: StrictBool

    @model_validator(mode="after")
    def require_four_way_flags(self) -> Self:
        key = _action_key(self.action)
        if any(
            _action_key(item) != key
            for item in (
                self.v1_generator,
                self.v1_checker,
                self.checked_v2,
                self.reference,
            )
        ):
            raise ValueError("M8 Gate-3 four-way audit differs from its sample action")
        computations = (
            self.v1_generator.computation,
            self.v1_checker.computation,
            self.checked_v2.computation,
            self.reference.computation,
        )
        independent = (
            len({item.implementation_content_sha256 for item in computations}) == 4
            and len({item.output_content_sha256 for item in computations}) == 4
        )
        normalized = self.v1_generator.normalized
        proof_match = (
            self.v1_checker.checker_valid
            and self.v1_checker.checked_proof_id == self.v1_generator.generated_proof_id
            and self.v1_checker.checked_proof_content_sha256
            == self.v1_generator.generated_proof_content_sha256
            and self.v1_checker.checked_event_count == len(normalized.ordered_event_evidence)
            and self.v1_checker.certificate_count
            == sum(len(item.influences) for item in normalized.ordered_event_evidence)
            and self.v1_checker.exact_transition_count
            == sum(
                item.classification == "exact_transition"
                for item in normalized.ordered_event_evidence
            )
        )
        generator_semantic = normalized_gate3_action_semantic_sha256(self.v1_generator.normalized)
        semantic_match = (
            self.v1_checker.checked_semantic_sha256 == generator_semantic
            and normalized_gate3_action_semantic_sha256(self.checked_v2.normalized)
            == generator_semantic
        )
        cost_match = (
            len(
                {
                    self.v1_generator.normalized.final_net_cost_bits,
                    self.v1_checker.checked_final_net_cost_bits,
                    self.checked_v2.normalized.final_net_cost_bits,
                    self.reference.final_net_cost_bits,
                }
            )
            == 1
        )
        if (
            self.independent_computations != independent
            or self.proof_binding_match != proof_match
            or self.semantic_match != semantic_match
            or self.cost_match != cost_match
            or self.all_match != (independent and proof_match and semantic_match and cost_match)
        ):
            raise ValueError("M8 Gate-3 four-way comparison flags differ")
        return self


def _require_global_audit_computation_matrix(
    comparisons: tuple[M8Gate3ActionAuditComparison, ...],
) -> None:
    role_computations = {
        "v1_generator": tuple(item.v1_generator.computation for item in comparisons),
        "v1_checker": tuple(item.v1_checker.computation for item in comparisons),
        "checked_v2": tuple(item.checked_v2.computation for item in comparisons),
        "reference": tuple(item.reference.computation for item in comparisons),
    }
    for computations in role_computations.values():
        if len({item.output_content_sha256 for item in computations}) != 12:
            raise ValueError("M8 Gate-3 audit roles require 12 unique per-action outputs")
        if (
            len(
                {
                    (item.implementation_id, item.implementation_content_sha256)
                    for item in computations
                }
            )
            != 1
            or len({(item.runtime_id, item.runtime_content_sha256) for item in computations}) != 1
        ):
            raise ValueError("M8 Gate-3 audit role implementation/runtime identities drift")
    all_outputs = {
        item.output_content_sha256
        for computations in role_computations.values()
        for item in computations
    }
    if len(all_outputs) != 48:
        raise ValueError("M8 Gate-3 audit output identities overlap across roles")


class M8Gate3AuditResult(BaselineContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-gate3-four-way-audit.v1"] = (
        "yieldforge.m8-gate3-four-way-audit.v1"
    )
    audit_id: StrictStr = Field(pattern=r"^yfm8g3audit-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    sample: M8Gate3AuditSample
    audited_action_count: Literal[12] = 12
    comparisons: tuple[M8Gate3ActionAuditComparison, ...] = Field(
        min_length=12,
        max_length=12,
    )
    independence_mismatch_count: StrictInt = Field(ge=0, le=12)
    proof_binding_mismatch_count: StrictInt = Field(ge=0, le=12)
    semantic_mismatch_count: StrictInt = Field(ge=0, le=12)
    cost_mismatch_count: StrictInt = Field(ge=0, le=12)
    total_mismatch_count: StrictInt = Field(ge=0, le=48)
    proof_decision: Literal["pass_proof_audit", "redesign_proof"]
    reference_attestation_scope: Literal["exact_final_net_cost_bits_only"] = (
        "exact_final_net_cost_bits_only"
    )
    claim_ceiling: Literal[
        "calibration_four_way_software_audit_only_not_performance_evaluation_or_m8_result"
    ] = "calibration_four_way_software_audit_only_not_performance_evaluation_or_m8_result"

    @model_validator(mode="after")
    def require_audit_aggregates_and_identity(self) -> Self:
        if tuple(item.action for item in self.comparisons) != self.sample.actions:
            raise ValueError("M8 Gate-3 audit differs from its sample")
        _require_global_audit_computation_matrix(self.comparisons)
        independence = sum(not item.independent_computations for item in self.comparisons)
        proof = sum(not item.proof_binding_match for item in self.comparisons)
        semantic = sum(not item.semantic_match for item in self.comparisons)
        cost = sum(not item.cost_match for item in self.comparisons)
        total = independence + proof + semantic + cost
        decision = "pass_proof_audit" if total == 0 else "redesign_proof"
        if (
            self.independence_mismatch_count != independence
            or self.proof_binding_mismatch_count != proof
            or self.semantic_mismatch_count != semantic
            or self.cost_mismatch_count != cost
            or self.total_mismatch_count != total
            or self.proof_decision != decision
        ):
            raise ValueError("M8 Gate-3 audit aggregates differ")
        digest = semantic_sha256(
            self,
            excluded_fields={"audit_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.audit_id != (
            f"yfm8g3audit-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 audit content identity differs")
        return self


def _index_unique[ValueT](
    values: Iterable[ValueT],
    *,
    label: str,
) -> dict[tuple[TemporalRegime, str, str, str], ValueT]:
    indexed: dict[tuple[TemporalRegime, str, str, str], ValueT] = {}
    for value in values:
        key = _action_key(value)
        if key in indexed:
            raise ValueError(f"{label} contains duplicate sample actions")
        indexed[key] = value
    return indexed


def finalize_gate3_audit(
    sample: M8Gate3AuditSample,
    v1_generator_records: tuple[M8Gate3V1GeneratorAuditRecord, ...],
    v1_checker_records: tuple[M8Gate3V1CheckerAuditRecord, ...],
    checked_v2_records: tuple[M8Gate3CheckedV2AuditRecord, ...],
    reference_attestations: tuple[M8Gate3ReferenceCostAttestation, ...],
) -> M8Gate3AuditResult:
    """Reconcile generator, independent checker, checked v2, and reference."""

    frozen = _strict_model(sample, M8Gate3AuditSample, label="M8 Gate-3 audit")
    generators = tuple(
        _strict_model(item, M8Gate3V1GeneratorAuditRecord, label="M8 v1 generator audit")
        for item in v1_generator_records
    )
    checkers = tuple(
        _strict_model(item, M8Gate3V1CheckerAuditRecord, label="M8 v1 checker audit")
        for item in v1_checker_records
    )
    checked_v2 = tuple(
        _strict_model(item, M8Gate3CheckedV2AuditRecord, label="M8 checked-v2 audit")
        for item in checked_v2_records
    )
    references = tuple(
        _strict_model(
            item,
            M8Gate3ReferenceCostAttestation,
            label="M8 reference audit",
        )
        for item in reference_attestations
    )
    by_generator = _index_unique(generators, label="M8 v1 generator audit")
    by_checker = _index_unique(checkers, label="M8 v1 checker audit")
    by_v2 = _index_unique(checked_v2, label="M8 checked-v2 audit")
    by_reference = _index_unique(references, label="M8 reference audit")
    expected = {_action_key(item) for item in frozen.actions}
    if any(set(indexed) != expected for indexed in (by_generator, by_checker, by_v2, by_reference)):
        raise ValueError("M8 Gate-3 audit input differs from a sample action")
    comparisons = []
    for action in frozen.actions:
        key = _action_key(action)
        generator = by_generator[key]
        checker = by_checker[key]
        v2 = by_v2[key]
        reference = by_reference[key]
        computations = (
            generator.computation,
            checker.computation,
            v2.computation,
            reference.computation,
        )
        independent = (
            len({item.implementation_content_sha256 for item in computations}) == 4
            and len({item.output_content_sha256 for item in computations}) == 4
        )
        proof_match = (
            checker.checker_valid
            and checker.checked_proof_id == generator.generated_proof_id
            and checker.checked_proof_content_sha256 == generator.generated_proof_content_sha256
            and checker.checked_event_count == len(generator.normalized.ordered_event_evidence)
            and checker.certificate_count
            == sum(len(item.influences) for item in generator.normalized.ordered_event_evidence)
            and checker.exact_transition_count
            == sum(
                item.classification == "exact_transition"
                for item in generator.normalized.ordered_event_evidence
            )
        )
        generator_semantic = normalized_gate3_action_semantic_sha256(generator.normalized)
        semantic_match = (
            checker.checked_semantic_sha256 == generator_semantic
            and normalized_gate3_action_semantic_sha256(v2.normalized) == generator_semantic
        )
        cost_match = (
            len(
                {
                    generator.normalized.final_net_cost_bits,
                    checker.checked_final_net_cost_bits,
                    v2.normalized.final_net_cost_bits,
                    reference.final_net_cost_bits,
                }
            )
            == 1
        )
        comparisons.append(
            M8Gate3ActionAuditComparison(
                action=action,
                v1_generator=generator,
                v1_checker=checker,
                checked_v2=v2,
                reference=reference,
                independent_computations=independent,
                proof_binding_match=proof_match,
                semantic_match=semantic_match,
                cost_match=cost_match,
                all_match=independent and proof_match and semantic_match and cost_match,
            )
        )
    comparison_tuple = tuple(comparisons)
    _require_global_audit_computation_matrix(comparison_tuple)
    independence = sum(not item.independent_computations for item in comparison_tuple)
    proof = sum(not item.proof_binding_match for item in comparison_tuple)
    semantic_count = sum(not item.semantic_match for item in comparison_tuple)
    cost = sum(not item.cost_match for item in comparison_tuple)
    semantic = {
        "schema_version": "yieldforge.m8-gate3-four-way-audit.v1",
        "sample": frozen.model_dump(mode="json"),
        "audited_action_count": 12,
        "comparisons": tuple(item.model_dump(mode="json") for item in comparison_tuple),
        "independence_mismatch_count": independence,
        "proof_binding_mismatch_count": proof,
        "semantic_mismatch_count": semantic_count,
        "cost_mismatch_count": cost,
        "total_mismatch_count": independence + proof + semantic_count + cost,
        "proof_decision": (
            "pass_proof_audit"
            if independence + proof + semantic_count + cost == 0
            else "redesign_proof"
        ),
        "reference_attestation_scope": "exact_final_net_cost_bits_only",
        "claim_ceiling": (
            "calibration_four_way_software_audit_only_not_performance_evaluation_or_m8_result"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8Gate3AuditResult(
        audit_id=f"yfm8g3audit-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        sample=frozen,
        comparisons=comparison_tuple,
        independence_mismatch_count=independence,
        proof_binding_mismatch_count=proof,
        semantic_mismatch_count=semantic_count,
        cost_mismatch_count=cost,
        total_mismatch_count=independence + proof + semantic_count + cost,
        proof_decision=(
            "pass_proof_audit"
            if independence + proof + semantic_count + cost == 0
            else "redesign_proof"
        ),
    )


type M8Gate3MutationTargetKind = Literal[
    "parent_v3_certificate",
    "portable_fact_gate3",
    "checked_root_manifest",
    "audit_sample",
    "checked_action_root",
]
type M8Gate3MutationFailureCode = Literal[
    "parent_v3_binding_mismatch",
    "portable_gate3_binding_mismatch",
    "root_manifest_binding_mismatch",
    "sample_binding_mismatch",
    "checked_action_root_binding_mismatch",
]

_MUTATION_FAILURE_BY_TARGET: dict[str, str] = {
    "parent_v3_certificate": "parent_v3_binding_mismatch",
    "portable_fact_gate3": "portable_gate3_binding_mismatch",
    "checked_root_manifest": "root_manifest_binding_mismatch",
    "audit_sample": "sample_binding_mismatch",
    "checked_action_root": "checked_action_root_binding_mismatch",
}


class M8Gate3MutationRecipeBinding(BaselineContractModel):
    schema_version: Literal["yieldforge.m8-gate3-mutation-recipe.v1"] = (
        "yieldforge.m8-gate3-mutation-recipe.v1"
    )
    recipe_id: StrictStr = Field(pattern=r"^mutation-[0-9a-f]{24}$")
    recipe_sha256: M8Sha256
    target_kind: M8Gate3MutationTargetKind
    target_content_sha256: M8Sha256
    expected_failure_code: M8Gate3MutationFailureCode
    rehash_required: Literal[True] = True

    @model_validator(mode="after")
    def require_recipe_identity(self) -> Self:
        if self.expected_failure_code != _MUTATION_FAILURE_BY_TARGET[self.target_kind]:
            raise ValueError("M8 Gate-3 mutation recipe target kind and failure code differ")
        digest = semantic_sha256(
            self,
            excluded_fields={"recipe_id", "recipe_sha256"},
        )
        if self.recipe_sha256 != f"sha256:{digest}" or self.recipe_id != (
            f"mutation-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 mutation recipe content identity differs")
        return self


def build_gate3_mutation_recipe(
    *,
    target_kind: M8Gate3MutationTargetKind,
    target_content_sha256: str,
) -> M8Gate3MutationRecipeBinding:
    semantic = {
        "schema_version": "yieldforge.m8-gate3-mutation-recipe.v1",
        "target_kind": target_kind,
        "target_content_sha256": target_content_sha256,
        "expected_failure_code": _MUTATION_FAILURE_BY_TARGET[target_kind],
        "rehash_required": True,
    }
    digest = semantic_sha256(semantic)
    return M8Gate3MutationRecipeBinding(
        recipe_id=f"mutation-{digest[:24]}",
        recipe_sha256=f"sha256:{digest}",
        target_kind=target_kind,
        target_content_sha256=target_content_sha256,
        expected_failure_code=_MUTATION_FAILURE_BY_TARGET[target_kind],  # type: ignore[arg-type]
    )


class M8Gate3MutationManifest(BaselineContractModel):
    schema_version: Literal["yieldforge.m8-gate3-mutation-manifest.v1"] = (
        "yieldforge.m8-gate3-mutation-manifest.v1"
    )
    manifest_id: StrictStr = Field(pattern=r"^yfm8g3mutmanifest-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    parent_v3_proof_id: Literal["yfm8proof-b296ba919c07d55ece14c6db"] = _PARENT_V3_PROOF_ID
    parent_v3_content_sha256: Literal[
        "sha256:b296ba919c07d55ece14c6dbb6ecbce1aa4a24e612dd1a251757e7a3b739739d"
    ] = _PARENT_V3_CONTENT_SHA256
    portable_gate3_id: StrictStr = Field(pattern=r"^yfm8gate3-[0-9a-f]{24}$")
    portable_gate3_content_sha256: M8Sha256
    root_manifest_id: StrictStr = Field(pattern=r"^yfm8g3roots-[0-9a-f]{24}$")
    root_manifest_content_sha256: M8Sha256
    sample_id: StrictStr = Field(pattern=r"^yfm8g3sample-[0-9a-f]{24}$")
    sample_content_sha256: M8Sha256
    sample_action_root_sha256s: tuple[M8Sha256, ...] = Field(
        min_length=12,
        max_length=12,
    )
    authorized_harness_id: StrictStr = Field(min_length=1)
    authorized_harness_content_sha256: M8Sha256
    authorized_runtime_id: StrictStr = Field(min_length=1)
    authorized_runtime_content_sha256: M8Sha256
    base_content_sha256s: tuple[M8Sha256, ...] = Field(min_length=4)
    registered_recipe_count: Literal[16] = 16
    recipes: tuple[M8Gate3MutationRecipeBinding, ...] = Field(
        min_length=16,
        max_length=16,
    )

    @model_validator(mode="after")
    def require_manifest_identity(self) -> Self:
        if self.base_content_sha256s != tuple(sorted(set(self.base_content_sha256s))):
            raise ValueError("M8 Gate-3 mutation base hashes are not canonical")
        required = {
            self.parent_v3_content_sha256,
            self.portable_gate3_content_sha256,
            self.root_manifest_content_sha256,
            self.sample_content_sha256,
        }
        if not required <= set(self.base_content_sha256s):
            raise ValueError("M8 Gate-3 mutation manifest omits a required base hash")
        if len(set(self.sample_action_root_sha256s)) != 12:
            raise ValueError("M8 Gate-3 mutation sample root targets are not unique")
        required_targets = {
            ("parent_v3_certificate", self.parent_v3_content_sha256),
            ("portable_fact_gate3", self.portable_gate3_content_sha256),
            ("checked_root_manifest", self.root_manifest_content_sha256),
            ("audit_sample", self.sample_content_sha256),
            *(("checked_action_root", item) for item in self.sample_action_root_sha256s),
        }
        observed_targets = {(item.target_kind, item.target_content_sha256) for item in self.recipes}
        if (
            self.recipes != tuple(sorted(self.recipes, key=lambda item: item.recipe_id))
            or len({item.recipe_id for item in self.recipes}) != 16
            or observed_targets != required_targets
        ):
            raise ValueError("M8 Gate-3 mutation recipes do not reconcile")
        digest = semantic_sha256(
            self,
            excluded_fields={"manifest_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.manifest_id != (
            f"yfm8g3mutmanifest-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 mutation manifest content identity differs")
        return self


def _require_gate3_manifest_binding(
    gate3: M8PortableFactGate3Result,
    manifest: M8Gate3CheckedRootManifest,
) -> None:
    rebuilt = freeze_gate3_checked_root_manifest(
        gate3,
        manifest.roots,
        membership_attestation=manifest.membership_attestation,
    )
    if rebuilt != manifest:
        raise ValueError("M8 Gate-3 complete root manifest differs from Task-7 evidence")


def _require_sample_manifest_binding(
    sample: M8Gate3AuditSample,
    manifest: M8Gate3CheckedRootManifest,
) -> None:
    if (
        sample.root_manifest_id != manifest.manifest_id
        or sample.root_manifest_content_sha256 != manifest.content_sha256
    ):
        raise ValueError("M8 Gate-3 sample differs from its complete root manifest")


def build_gate3_mutation_manifest(
    parent_v3: M8CertificateProofResult,
    portable_fact_gate3: M8PortableFactGate3Result,
    root_manifest: M8Gate3CheckedRootManifest,
    sample: M8Gate3AuditSample,
    *,
    harness_id: str,
    harness_content_sha256: str,
    runtime_id: str,
    runtime_content_sha256: str,
    additional_base_content_sha256s: tuple[str, ...] = (),
) -> M8Gate3MutationManifest:
    parent = _strict_model(parent_v3, M8CertificateProofResult, label="mutation manifest")
    gate3 = _strict_model(
        portable_fact_gate3,
        M8PortableFactGate3Result,
        label="mutation manifest",
    )
    roots = _strict_model(root_manifest, M8Gate3CheckedRootManifest, label="mutation manifest")
    frozen = _strict_model(sample, M8Gate3AuditSample, label="mutation manifest")
    if parent.proof_id != _PARENT_V3_PROOF_ID or parent.content_sha256 != _PARENT_V3_CONTENT_SHA256:
        raise ValueError("M8 Gate-3 mutation parent-v3 differs")
    _require_gate3_manifest_binding(gate3, roots)
    if freeze_gate3_audit_sample(parent, roots) != frozen:
        raise ValueError("M8 Gate-3 mutation sample differs from deterministic re-freeze")
    sample_roots = tuple(item.root_fact_sha256 for item in frozen.actions)
    strict_recipes = (
        build_gate3_mutation_recipe(
            target_kind="parent_v3_certificate",
            target_content_sha256=parent.content_sha256,
        ),
        build_gate3_mutation_recipe(
            target_kind="portable_fact_gate3",
            target_content_sha256=gate3.content_sha256,
        ),
        build_gate3_mutation_recipe(
            target_kind="checked_root_manifest",
            target_content_sha256=roots.content_sha256,
        ),
        build_gate3_mutation_recipe(
            target_kind="audit_sample",
            target_content_sha256=frozen.content_sha256,
        ),
        *(
            build_gate3_mutation_recipe(
                target_kind="checked_action_root",
                target_content_sha256=item,
            )
            for item in sample_roots
        ),
    )
    ordered_recipes = tuple(sorted(strict_recipes, key=lambda item: item.recipe_id))
    bases = tuple(
        sorted(
            {
                parent.content_sha256,
                gate3.content_sha256,
                roots.content_sha256,
                frozen.content_sha256,
                *additional_base_content_sha256s,
            }
        )
    )
    semantic = {
        "schema_version": "yieldforge.m8-gate3-mutation-manifest.v1",
        "parent_v3_proof_id": parent.proof_id,
        "parent_v3_content_sha256": parent.content_sha256,
        "portable_gate3_id": gate3.gate3_id,
        "portable_gate3_content_sha256": gate3.content_sha256,
        "root_manifest_id": roots.manifest_id,
        "root_manifest_content_sha256": roots.content_sha256,
        "sample_id": frozen.sample_id,
        "sample_content_sha256": frozen.content_sha256,
        "sample_action_root_sha256s": sample_roots,
        "authorized_harness_id": harness_id,
        "authorized_harness_content_sha256": harness_content_sha256,
        "authorized_runtime_id": runtime_id,
        "authorized_runtime_content_sha256": runtime_content_sha256,
        "base_content_sha256s": bases,
        "registered_recipe_count": len(ordered_recipes),
        "recipes": tuple(item.model_dump(mode="json") for item in ordered_recipes),
    }
    digest = semantic_sha256(semantic)
    return M8Gate3MutationManifest(
        manifest_id=f"yfm8g3mutmanifest-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        portable_gate3_id=gate3.gate3_id,
        portable_gate3_content_sha256=gate3.content_sha256,
        root_manifest_id=roots.manifest_id,
        root_manifest_content_sha256=roots.content_sha256,
        sample_id=frozen.sample_id,
        sample_content_sha256=frozen.content_sha256,
        sample_action_root_sha256s=sample_roots,
        authorized_harness_id=harness_id,
        authorized_harness_content_sha256=harness_content_sha256,
        authorized_runtime_id=runtime_id,
        authorized_runtime_content_sha256=runtime_content_sha256,
        base_content_sha256s=bases,
        registered_recipe_count=len(ordered_recipes),
        recipes=ordered_recipes,
    )


class M8Gate3MutationOutcome(BaselineContractModel):
    recipe_id: StrictStr = Field(pattern=r"^mutation-[0-9a-f]{24}$")
    recipe_sha256: M8Sha256
    target_content_sha256: M8Sha256
    expected_failure_code: M8Gate3MutationFailureCode
    observed_failure_code: StrictStr | None = Field(default=None, min_length=1)
    rehash_required: StrictBool
    rehash_performed: StrictBool
    mutation_rejected: StrictBool
    worker_exit_code: StrictInt
    surviving_descendant_count: StrictInt = Field(ge=0)
    surviving_registry_count: StrictInt = Field(ge=0)
    artifact_published: StrictBool
    evaluation_accessed: StrictBool


class M8Gate3MutationResult(BaselineContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-gate3-executed-mutations.v1"] = (
        "yieldforge.m8-gate3-executed-mutations.v1"
    )
    mutation_result_id: StrictStr = Field(pattern=r"^yfm8g3mutation-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    manifest: M8Gate3MutationManifest
    harness_id: StrictStr = Field(min_length=1)
    harness_content_sha256: M8Sha256
    runtime_id: StrictStr = Field(min_length=1)
    runtime_content_sha256: M8Sha256
    outcomes: tuple[M8Gate3MutationOutcome, ...]
    registered_recipe_count: StrictInt = Field(gt=0)
    executed_outcome_count: StrictInt = Field(ge=0)
    rejected_mutation_count: StrictInt = Field(ge=0)
    complete_manifest_reconciliation: StrictBool
    all_expected_failure_codes_match: StrictBool
    all_required_rehashes_performed: StrictBool
    all_worker_exits_clean: StrictBool
    all_mutations_rejected: StrictBool
    surviving_descendant_count: StrictInt = Field(ge=0)
    surviving_registry_count: StrictInt = Field(ge=0)
    artifact_published_count: StrictInt = Field(ge=0)
    evaluation_accessed: StrictBool
    mutation_decision: Literal["pass_executed_mutations", "redesign_proof"]
    execution_scope: Literal["bound_executed_harness_evidence"] = "bound_executed_harness_evidence"

    @model_validator(mode="after")
    def require_execution_aggregates_and_identity(self) -> Self:
        recipes = {item.recipe_id: item for item in self.manifest.recipes}
        authorized_execution = (
            self.harness_id == self.manifest.authorized_harness_id
            and self.harness_content_sha256 == self.manifest.authorized_harness_content_sha256
            and self.runtime_id == self.manifest.authorized_runtime_id
            and self.runtime_content_sha256 == self.manifest.authorized_runtime_content_sha256
        )
        if not authorized_execution:
            raise ValueError("M8 mutation result differs from its authorized harness/runtime")
        outcome_ids = tuple(item.recipe_id for item in self.outcomes)
        complete = len(outcome_ids) == len(set(outcome_ids)) and set(outcome_ids) == set(recipes)
        codes = complete and all(
            item.recipe_sha256 == recipes[item.recipe_id].recipe_sha256
            and item.target_content_sha256 == recipes[item.recipe_id].target_content_sha256
            and item.expected_failure_code == recipes[item.recipe_id].expected_failure_code
            and item.observed_failure_code == recipes[item.recipe_id].expected_failure_code
            for item in self.outcomes
        )
        rehashes = complete and all(
            item.rehash_required == recipes[item.recipe_id].rehash_required
            and (not item.rehash_required or item.rehash_performed)
            for item in self.outcomes
        )
        exits = complete and all(item.worker_exit_code == 0 for item in self.outcomes)
        rejected = complete and all(item.mutation_rejected for item in self.outcomes)
        descendants = sum(item.surviving_descendant_count for item in self.outcomes)
        registries = sum(item.surviving_registry_count for item in self.outcomes)
        published = sum(item.artifact_published for item in self.outcomes)
        evaluation = any(item.evaluation_accessed for item in self.outcomes)
        decision = (
            "pass_executed_mutations"
            if complete
            and codes
            and rehashes
            and exits
            and rejected
            and descendants == 0
            and registries == 0
            and published == 0
            and not evaluation
            else "redesign_proof"
        )
        expected = (
            self.registered_recipe_count == len(recipes),
            self.executed_outcome_count == len(self.outcomes),
            self.rejected_mutation_count == sum(item.mutation_rejected for item in self.outcomes),
            self.complete_manifest_reconciliation == complete,
            self.all_expected_failure_codes_match == codes,
            self.all_required_rehashes_performed == rehashes,
            self.all_worker_exits_clean == exits,
            self.all_mutations_rejected == rejected,
            self.surviving_descendant_count == descendants,
            self.surviving_registry_count == registries,
            self.artifact_published_count == published,
            self.evaluation_accessed == evaluation,
            self.mutation_decision == decision,
        )
        if not all(expected):
            raise ValueError("M8 Gate-3 mutation execution aggregates differ")
        digest = semantic_sha256(
            self,
            excluded_fields={"mutation_result_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.mutation_result_id != (
            f"yfm8g3mutation-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 mutation execution content identity differs")
        return self


def finalize_gate3_mutation_execution(
    manifest: M8Gate3MutationManifest,
    *,
    harness_id: str,
    harness_content_sha256: str,
    runtime_id: str,
    runtime_content_sha256: str,
    outcomes: tuple[M8Gate3MutationOutcome, ...],
) -> M8Gate3MutationResult:
    """Reconcile a registered manifest with bound external harness outcomes."""

    registered = _strict_model(
        manifest,
        M8Gate3MutationManifest,
        label="M8 mutation execution",
    )
    strict_outcomes = tuple(
        _strict_model(item, M8Gate3MutationOutcome, label="M8 mutation outcome")
        for item in outcomes
    )
    if (
        harness_id != registered.authorized_harness_id
        or harness_content_sha256 != registered.authorized_harness_content_sha256
        or runtime_id != registered.authorized_runtime_id
        or runtime_content_sha256 != registered.authorized_runtime_content_sha256
    ):
        raise ValueError("M8 mutation execution differs from its authorized harness/runtime")
    ordered = tuple(sorted(strict_outcomes, key=lambda item: item.recipe_id))
    recipes = {item.recipe_id: item for item in registered.recipes}
    ids = tuple(item.recipe_id for item in ordered)
    complete = len(ids) == len(set(ids)) and set(ids) == set(recipes)
    codes = complete and all(
        item.recipe_sha256 == recipes[item.recipe_id].recipe_sha256
        and item.target_content_sha256 == recipes[item.recipe_id].target_content_sha256
        and item.expected_failure_code == recipes[item.recipe_id].expected_failure_code
        and item.observed_failure_code == recipes[item.recipe_id].expected_failure_code
        for item in ordered
    )
    rehashes = complete and all(
        item.rehash_required == recipes[item.recipe_id].rehash_required
        and (not item.rehash_required or item.rehash_performed)
        for item in ordered
    )
    exits = complete and all(item.worker_exit_code == 0 for item in ordered)
    rejected = complete and all(item.mutation_rejected for item in ordered)
    descendants = sum(item.surviving_descendant_count for item in ordered)
    registries = sum(item.surviving_registry_count for item in ordered)
    published = sum(item.artifact_published for item in ordered)
    evaluation = any(item.evaluation_accessed for item in ordered)
    decision = (
        "pass_executed_mutations"
        if complete
        and codes
        and rehashes
        and exits
        and rejected
        and descendants == 0
        and registries == 0
        and published == 0
        and not evaluation
        else "redesign_proof"
    )
    semantic = {
        "schema_version": "yieldforge.m8-gate3-executed-mutations.v1",
        "manifest": registered.model_dump(mode="json"),
        "harness_id": harness_id,
        "harness_content_sha256": harness_content_sha256,
        "runtime_id": runtime_id,
        "runtime_content_sha256": runtime_content_sha256,
        "outcomes": tuple(item.model_dump(mode="json") for item in ordered),
        "registered_recipe_count": len(registered.recipes),
        "executed_outcome_count": len(ordered),
        "rejected_mutation_count": sum(item.mutation_rejected for item in ordered),
        "complete_manifest_reconciliation": complete,
        "all_expected_failure_codes_match": codes,
        "all_required_rehashes_performed": rehashes,
        "all_worker_exits_clean": exits,
        "all_mutations_rejected": rejected,
        "surviving_descendant_count": descendants,
        "surviving_registry_count": registries,
        "artifact_published_count": published,
        "evaluation_accessed": evaluation,
        "mutation_decision": decision,
        "execution_scope": "bound_executed_harness_evidence",
    }
    digest = semantic_sha256(semantic)
    return M8Gate3MutationResult(
        mutation_result_id=f"yfm8g3mutation-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        manifest=registered,
        harness_id=harness_id,
        harness_content_sha256=harness_content_sha256,
        runtime_id=runtime_id,
        runtime_content_sha256=runtime_content_sha256,
        outcomes=ordered,
        registered_recipe_count=len(registered.recipes),
        executed_outcome_count=len(ordered),
        rejected_mutation_count=sum(item.mutation_rejected for item in ordered),
        complete_manifest_reconciliation=complete,
        all_expected_failure_codes_match=codes,
        all_required_rehashes_performed=rehashes,
        all_worker_exits_clean=exits,
        all_mutations_rejected=rejected,
        surviving_descendant_count=descendants,
        surviving_registry_count=registries,
        artifact_published_count=published,
        evaluation_accessed=evaluation,
        mutation_decision=decision,
    )


def build_gate3_mutation_result(**_external_counts: object) -> NoReturn:
    """Reject legacy unbound counts; only executed manifest evidence may pass."""

    raise ValueError("external mutation counts cannot create Gate-3 mutation evidence")


class M8Gate3ReferenceTiming(BaselineContractModel):
    schema_version: Literal["yieldforge.m8-gate3-reference-timing.v1"] = (
        "yieldforge.m8-gate3-reference-timing.v1"
    )
    timing_id: StrictStr = Field(pattern=r"^yfm8g3reftime-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    computation: M8Gate3AuditComputationIdentity
    regime: M8Gate3Regime
    action_id: StrictStr = Field(pattern=r"^yfm7a-[0-9a-f]{24}$")
    catalog_action_id: StrictStr = Field(min_length=1)
    root_fact_sha256: M8Sha256
    worker_seconds: StrictFloat = Field(gt=0.0)
    measurement_scope: Literal["external_reference_action_worker_monotonic_wall"] = (
        "external_reference_action_worker_monotonic_wall"
    )

    @model_validator(mode="after")
    def require_reference_timing_identity(self) -> Self:
        if self.computation.role != "reference":
            raise ValueError("M8 Gate-3 reference timing computation identity differs")
        digest = semantic_sha256(
            self,
            excluded_fields={"timing_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.timing_id != (
            f"yfm8g3reftime-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 reference timing content identity differs")
        return self


def build_gate3_reference_timing(
    *,
    regime: M8Gate3Regime,
    action_id: str,
    catalog_action_id: str,
    root_fact_sha256: str,
    computation: M8Gate3AuditComputationIdentity,
    worker_seconds: float,
) -> M8Gate3ReferenceTiming:
    identity = _strict_model(
        computation,
        M8Gate3AuditComputationIdentity,
        label="M8 reference timing",
    )
    semantic = {
        "schema_version": "yieldforge.m8-gate3-reference-timing.v1",
        "computation": identity.model_dump(mode="json"),
        "regime": regime.value,
        "action_id": action_id,
        "catalog_action_id": catalog_action_id,
        "root_fact_sha256": root_fact_sha256,
        "worker_seconds": worker_seconds,
        "measurement_scope": "external_reference_action_worker_monotonic_wall",
    }
    digest = semantic_sha256(semantic)
    return M8Gate3ReferenceTiming(
        timing_id=f"yfm8g3reftime-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        computation=identity,
        regime=regime,
        action_id=action_id,
        catalog_action_id=catalog_action_id,
        root_fact_sha256=root_fact_sha256,
        worker_seconds=worker_seconds,
    )


class M8Gate3PerformanceSensitivity(BaselineContractModel):
    name: Literal["first_generation_plus_checker_only"] = "first_generation_plus_checker_only"
    gating: Literal[False] = False
    charged_wall_seconds: StrictFloat = Field(gt=0.0)
    projected_held_out_calendar_days: StrictFloat = Field(gt=0.0)
    reference_equivalent_speedup: StrictFloat = Field(gt=0.0)


class M8Gate3PerformanceResult(BaselineContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-gate3-performance.v2"] = (
        "yieldforge.m8-gate3-performance.v2"
    )
    performance_id: StrictStr = Field(pattern=r"^yfm8g3perf-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    portable_gate3_id: StrictStr = Field(pattern=r"^yfm8gate3-[0-9a-f]{24}$")
    portable_gate3_content_sha256: M8Sha256
    root_manifest_id: StrictStr = Field(pattern=r"^yfm8g3roots-[0-9a-f]{24}$")
    root_manifest_content_sha256: M8Sha256
    sample_id: StrictStr = Field(pattern=r"^yfm8g3sample-[0-9a-f]{24}$")
    sample_content_sha256: M8Sha256
    observed_action_event_count: StrictInt = Field(gt=0)
    reference_action_count: Literal[12] = 12
    reference_timings: tuple[M8Gate3ReferenceTiming, ...] = Field(
        min_length=12,
        max_length=12,
    )
    reference_timing_order: Literal["audit_sample_action_order"] = "audit_sample_action_order"
    reference_action_worker_seconds_sum: StrictFloat = Field(gt=0.0)
    charged_pipeline_wall_seconds: StrictFloat = Field(gt=0.0)
    projected_action_count: Literal[550542] = _HELD_OUT_ACTION_COUNT
    projected_mean_future_event_count: Literal[11.5] = _HELD_OUT_MEAN_FUTURE_EVENT_COUNT
    projection_safety_factor: Literal[2.0] = _PROJECTION_SAFETY_FACTOR
    projected_held_out_calendar_days: StrictFloat = Field(gt=0.0)
    reference_slot_count: Literal[8] = _REFERENCE_SLOT_COUNT
    reference_equal_8_slot_wall_seconds: StrictFloat = Field(gt=0.0)
    reference_equivalent_speedup: StrictFloat = Field(gt=0.0)
    minimum_reference_speedup: Literal[25.0] = _MINIMUM_REFERENCE_SPEEDUP
    maximum_projected_calendar_days: Literal[5.0] = _MAXIMUM_PROJECTED_DAYS
    sensitivity: M8Gate3PerformanceSensitivity
    performance_decision: Literal[
        "pass_abbreviated_performance",
        "hold_performance",
    ]
    claim_ceiling: Literal[
        "abbreviated_calibration_software_performance_only_not_official_execution_or_m8_result"
    ] = "abbreviated_calibration_software_performance_only_not_official_execution_or_m8_result"

    @model_validator(mode="after")
    def require_performance_math_and_identity(self) -> Self:
        if len({_action_key(item) for item in self.reference_timings}) != 12:
            raise ValueError("M8 Gate-3 reference timings are not uniquely keyed")
        if (
            len({item.computation.implementation_content_sha256 for item in self.reference_timings})
            != 1
            or len({item.computation.runtime_content_sha256 for item in self.reference_timings})
            != 1
            or len({item.computation.output_content_sha256 for item in self.reference_timings})
            != 12
        ):
            raise ValueError("M8 Gate-3 reference timing execution identities differ")
        reference_sum = sum(item.worker_seconds for item in self.reference_timings)
        projected_days = (
            self.charged_pipeline_wall_seconds
            / self.observed_action_event_count
            * self.projected_action_count
            * self.projected_mean_future_event_count
            * self.projection_safety_factor
            / _SECONDS_PER_DAY
        )
        reference_wall = (
            reference_sum / self.reference_action_count * 887
        ) / self.reference_slot_count
        speedup = reference_wall / self.charged_pipeline_wall_seconds
        sensitivity_days = (
            self.sensitivity.charged_wall_seconds
            / self.observed_action_event_count
            * self.projected_action_count
            * self.projected_mean_future_event_count
            * self.projection_safety_factor
            / _SECONDS_PER_DAY
        )
        sensitivity_speedup = reference_wall / self.sensitivity.charged_wall_seconds
        decision = (
            "pass_abbreviated_performance"
            if speedup >= self.minimum_reference_speedup
            and projected_days <= self.maximum_projected_calendar_days
            else "hold_performance"
        )
        if (
            self.reference_action_worker_seconds_sum != reference_sum
            or self.projected_held_out_calendar_days != projected_days
            or self.reference_equal_8_slot_wall_seconds != reference_wall
            or self.reference_equivalent_speedup != speedup
            or self.sensitivity.projected_held_out_calendar_days != sensitivity_days
            or self.sensitivity.reference_equivalent_speedup != sensitivity_speedup
            or self.performance_decision != decision
        ):
            raise ValueError("M8 Gate-3 performance math differs")
        digest = semantic_sha256(
            self,
            excluded_fields={"performance_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.performance_id != (
            f"yfm8g3perf-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 performance content identity differs")
        return self


def _projected_days(charged_wall: float, observed_action_events: int) -> float:
    return (
        charged_wall
        / observed_action_events
        * _HELD_OUT_ACTION_COUNT
        * _HELD_OUT_MEAN_FUTURE_EVENT_COUNT
        * _PROJECTION_SAFETY_FACTOR
        / _SECONDS_PER_DAY
    )


def finalize_gate3_performance(
    portable_fact_gate3: M8PortableFactGate3Result,
    root_manifest: M8Gate3CheckedRootManifest,
    sample: M8Gate3AuditSample,
    *,
    reference_timings: tuple[M8Gate3ReferenceTiming, ...],
) -> M8Gate3PerformanceResult:
    """Charge full Task-7 wall against manifest-derived observed action-events."""

    gate3 = _strict_model(
        portable_fact_gate3,
        M8PortableFactGate3Result,
        label="M8 Gate-3 performance",
    )
    roots = _strict_model(
        root_manifest,
        M8Gate3CheckedRootManifest,
        label="M8 Gate-3 performance",
    )
    frozen = _strict_model(sample, M8Gate3AuditSample, label="M8 Gate-3 performance")
    _require_gate3_manifest_binding(gate3, roots)
    _require_sample_manifest_binding(frozen, roots)
    timings = tuple(
        _strict_model(item, M8Gate3ReferenceTiming, label="M8 reference timing")
        for item in reference_timings
    )
    indexed = _index_unique(timings, label="M8 reference timings")
    expected = {_action_key(item) for item in frozen.actions}
    if set(indexed) != expected:
        raise ValueError("M8 Gate-3 reference timing differs from a sample action")
    ordered = tuple(indexed[_action_key(item)] for item in frozen.actions)
    reference_sum = sum(item.worker_seconds for item in ordered)
    reference_wall = (reference_sum / 12 * 887) / 8
    charged = gate3.total_pipeline_wall_seconds
    projected = _projected_days(charged, roots.observed_action_event_count)
    speedup = reference_wall / charged
    sensitivity_wall = gate3.first_generation_phase_wall_seconds + gate3.checker_phase_wall_seconds
    sensitivity = M8Gate3PerformanceSensitivity(
        charged_wall_seconds=sensitivity_wall,
        projected_held_out_calendar_days=_projected_days(
            sensitivity_wall,
            roots.observed_action_event_count,
        ),
        reference_equivalent_speedup=reference_wall / sensitivity_wall,
    )
    semantic = {
        "schema_version": "yieldforge.m8-gate3-performance.v2",
        "portable_gate3_id": gate3.gate3_id,
        "portable_gate3_content_sha256": gate3.content_sha256,
        "root_manifest_id": roots.manifest_id,
        "root_manifest_content_sha256": roots.content_sha256,
        "sample_id": frozen.sample_id,
        "sample_content_sha256": frozen.content_sha256,
        "observed_action_event_count": roots.observed_action_event_count,
        "reference_action_count": 12,
        "reference_timings": tuple(item.model_dump(mode="json") for item in ordered),
        "reference_timing_order": "audit_sample_action_order",
        "reference_action_worker_seconds_sum": reference_sum,
        "charged_pipeline_wall_seconds": charged,
        "projected_action_count": _HELD_OUT_ACTION_COUNT,
        "projected_mean_future_event_count": _HELD_OUT_MEAN_FUTURE_EVENT_COUNT,
        "projection_safety_factor": _PROJECTION_SAFETY_FACTOR,
        "projected_held_out_calendar_days": projected,
        "reference_slot_count": _REFERENCE_SLOT_COUNT,
        "reference_equal_8_slot_wall_seconds": reference_wall,
        "reference_equivalent_speedup": speedup,
        "minimum_reference_speedup": _MINIMUM_REFERENCE_SPEEDUP,
        "maximum_projected_calendar_days": _MAXIMUM_PROJECTED_DAYS,
        "sensitivity": sensitivity.model_dump(mode="json"),
        "performance_decision": (
            "pass_abbreviated_performance"
            if speedup >= _MINIMUM_REFERENCE_SPEEDUP and projected <= _MAXIMUM_PROJECTED_DAYS
            else "hold_performance"
        ),
        "claim_ceiling": (
            "abbreviated_calibration_software_performance_only_not_official_execution_or_m8_result"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8Gate3PerformanceResult(
        performance_id=f"yfm8g3perf-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        portable_gate3_id=gate3.gate3_id,
        portable_gate3_content_sha256=gate3.content_sha256,
        root_manifest_id=roots.manifest_id,
        root_manifest_content_sha256=roots.content_sha256,
        sample_id=frozen.sample_id,
        sample_content_sha256=frozen.content_sha256,
        observed_action_event_count=roots.observed_action_event_count,
        reference_timings=ordered,
        reference_action_worker_seconds_sum=reference_sum,
        charged_pipeline_wall_seconds=charged,
        projected_held_out_calendar_days=projected,
        reference_equal_8_slot_wall_seconds=reference_wall,
        reference_equivalent_speedup=speedup,
        sensitivity=sensitivity,
        performance_decision=(
            "pass_abbreviated_performance"
            if speedup >= _MINIMUM_REFERENCE_SPEEDUP and projected <= _MAXIMUM_PROJECTED_DAYS
            else "hold_performance"
        ),
    )


class M8Gate3Decision(BaselineContractModel):
    """Software-only decision wrapping Task-7 evidence without extending it."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-gate3-decision.v2"] = "yieldforge.m8-gate3-decision.v2"
    decision_id: StrictStr = Field(pattern=r"^yfm8g3decision-[0-9a-f]{24}$")
    content_sha256: M8Sha256
    parent_v3_certificate_proof: M8CertificateProofResult
    portable_fact_gate3: M8PortableFactGate3Result
    root_manifest: M8Gate3CheckedRootManifest
    sample: M8Gate3AuditSample
    audit: M8Gate3AuditResult
    mutations: M8Gate3MutationResult
    performance: M8Gate3PerformanceResult | None
    decision: Literal[
        "redesign_proof",
        "hold_performance",
        "authorize_official_six_cell_calibration",
    ]
    official_six_cell_calibration_authorized: StrictBool
    evaluation_opened: Literal[False] = False
    official_six_cell_executed: Literal[False] = False
    claim_ceiling: Literal[
        "calibration_software_gate_evidence_only_not_evaluation_m8_advantage_savings_physical_"
        "buyer_or_commercial_proof"
    ] = (
        "calibration_software_gate_evidence_only_not_evaluation_m8_advantage_savings_physical_"
        "buyer_or_commercial_proof"
    )

    @model_validator(mode="after")
    def require_complete_bound_decision(self) -> Self:
        _require_decision_bindings(
            self.parent_v3_certificate_proof,
            self.portable_fact_gate3,
            self.root_manifest,
            self.sample,
            self.audit,
            self.mutations,
            self.performance,
        )
        proof_passed = (
            self.audit.proof_decision == "pass_proof_audit"
            and self.mutations.mutation_decision == "pass_executed_mutations"
        )
        if not proof_passed:
            decision = "redesign_proof"
        elif self.performance is None or (
            self.performance.performance_decision != "pass_abbreviated_performance"
        ):
            decision = "hold_performance"
        else:
            decision = "authorize_official_six_cell_calibration"
        authorized = decision == "authorize_official_six_cell_calibration"
        if self.decision != decision or (
            self.official_six_cell_calibration_authorized != authorized
        ):
            raise ValueError("M8 Gate-3 decision authorization differs")
        digest = semantic_sha256(
            self,
            excluded_fields={"decision_id", "content_sha256"},
        )
        if self.content_sha256 != f"sha256:{digest}" or self.decision_id != (
            f"yfm8g3decision-{digest[:24]}"
        ):
            raise ValueError("M8 Gate-3 decision content identity differs")
        return self


def _require_decision_bindings(
    parent: M8CertificateProofResult,
    gate3: M8PortableFactGate3Result,
    roots: M8Gate3CheckedRootManifest,
    sample: M8Gate3AuditSample,
    audit: M8Gate3AuditResult,
    mutations: M8Gate3MutationResult,
    performance: M8Gate3PerformanceResult | None,
) -> None:
    if parent.proof_id != _PARENT_V3_PROOF_ID or parent.content_sha256 != _PARENT_V3_CONTENT_SHA256:
        raise ValueError("M8 Gate-3 decision parent-v3 differs")
    _require_gate3_manifest_binding(gate3, roots)
    expected_sample = freeze_gate3_audit_sample(parent, roots)
    if expected_sample != sample:
        raise ValueError("M8 Gate-3 decision sample differs from deterministic re-freeze")
    if audit.sample != sample:
        raise ValueError("M8 Gate-3 decision audit differs from sample")
    mutation_manifest = mutations.manifest
    if (
        mutation_manifest.parent_v3_content_sha256 != parent.content_sha256
        or mutation_manifest.portable_gate3_id != gate3.gate3_id
        or mutation_manifest.portable_gate3_content_sha256 != gate3.content_sha256
        or mutation_manifest.root_manifest_id != roots.manifest_id
        or mutation_manifest.root_manifest_content_sha256 != roots.content_sha256
        or mutation_manifest.sample_id != sample.sample_id
        or mutation_manifest.sample_content_sha256 != sample.content_sha256
        or mutation_manifest.sample_action_root_sha256s
        != tuple(item.root_fact_sha256 for item in sample.actions)
    ):
        raise ValueError("M8 Gate-3 mutation execution differs from bound base evidence")
    if mutations.evaluation_accessed:
        raise ValueError("M8 Gate-3 decision cannot bind evaluation-accessed mutations")
    if performance is not None:
        if (
            performance.portable_gate3_id != gate3.gate3_id
            or performance.portable_gate3_content_sha256 != gate3.content_sha256
            or performance.root_manifest_id != roots.manifest_id
            or performance.root_manifest_content_sha256 != roots.content_sha256
            or performance.sample_id != sample.sample_id
            or performance.sample_content_sha256 != sample.content_sha256
            or performance.observed_action_event_count != roots.observed_action_event_count
            or tuple(_action_key(item) for item in performance.reference_timings)
            != tuple(_action_key(item) for item in sample.actions)
            or tuple(item.computation for item in performance.reference_timings)
            != tuple(item.reference.computation for item in audit.comparisons)
            or performance.charged_pipeline_wall_seconds != gate3.total_pipeline_wall_seconds
            or performance.sensitivity.charged_wall_seconds
            != (gate3.first_generation_phase_wall_seconds + gate3.checker_phase_wall_seconds)
        ):
            raise ValueError("M8 Gate-3 performance differs from bound base evidence")


def finalize_gate3_decision(
    parent_v3: M8CertificateProofResult,
    portable_fact_gate3: M8PortableFactGate3Result,
    root_manifest: M8Gate3CheckedRootManifest,
    sample: M8Gate3AuditSample,
    audit: M8Gate3AuditResult,
    mutations: M8Gate3MutationResult,
    performance: M8Gate3PerformanceResult | None,
) -> M8Gate3Decision:
    """Re-freeze all roots/sample before any six-cell authorization."""

    parent = _strict_model(parent_v3, M8CertificateProofResult, label="M8 Gate-3 decision")
    gate3 = _strict_model(
        portable_fact_gate3,
        M8PortableFactGate3Result,
        label="M8 Gate-3 decision",
    )
    roots = _strict_model(
        root_manifest,
        M8Gate3CheckedRootManifest,
        label="M8 Gate-3 decision",
    )
    frozen = _strict_model(sample, M8Gate3AuditSample, label="M8 Gate-3 decision")
    strict_audit = _strict_model(audit, M8Gate3AuditResult, label="M8 Gate-3 decision")
    strict_mutations = _strict_model(
        mutations,
        M8Gate3MutationResult,
        label="M8 Gate-3 decision",
    )
    strict_performance = (
        _strict_model(
            performance,
            M8Gate3PerformanceResult,
            label="M8 Gate-3 decision",
        )
        if performance is not None
        else None
    )
    _require_decision_bindings(
        parent,
        gate3,
        roots,
        frozen,
        strict_audit,
        strict_mutations,
        strict_performance,
    )
    proof_passed = (
        strict_audit.proof_decision == "pass_proof_audit"
        and strict_mutations.mutation_decision == "pass_executed_mutations"
    )
    if not proof_passed:
        decision = "redesign_proof"
    elif strict_performance is None or (
        strict_performance.performance_decision != "pass_abbreviated_performance"
    ):
        decision = "hold_performance"
    else:
        decision = "authorize_official_six_cell_calibration"
    semantic = {
        "schema_version": "yieldforge.m8-gate3-decision.v2",
        "parent_v3_certificate_proof": parent.model_dump(mode="json"),
        "portable_fact_gate3": gate3.model_dump(mode="json"),
        "root_manifest": roots.model_dump(mode="json"),
        "sample": frozen.model_dump(mode="json"),
        "audit": strict_audit.model_dump(mode="json"),
        "mutations": strict_mutations.model_dump(mode="json"),
        "performance": (
            strict_performance.model_dump(mode="json") if strict_performance is not None else None
        ),
        "decision": decision,
        "official_six_cell_calibration_authorized": (
            decision == "authorize_official_six_cell_calibration"
        ),
        "evaluation_opened": False,
        "official_six_cell_executed": False,
        "claim_ceiling": (
            "calibration_software_gate_evidence_only_not_evaluation_m8_advantage_savings_"
            "physical_buyer_or_commercial_proof"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8Gate3Decision(
        decision_id=f"yfm8g3decision-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        parent_v3_certificate_proof=parent,
        portable_fact_gate3=gate3,
        root_manifest=roots,
        sample=frozen,
        audit=strict_audit,
        mutations=strict_mutations,
        performance=strict_performance,
        decision=decision,
        official_six_cell_calibration_authorized=(
            decision == "authorize_official_six_cell_calibration"
        ),
    )


__all__ = [
    "M8Gate3ActionAuditComparison",
    "M8Gate3AuditComputationIdentity",
    "M8Gate3AuditResult",
    "M8Gate3AuditSample",
    "M8Gate3AuditSampleAction",
    "M8Gate3CheckedActionRoot",
    "M8Gate3CheckedRootManifest",
    "M8Gate3CheckedV2AuditRecord",
    "M8Gate3Decision",
    "M8Gate3MutationManifest",
    "M8Gate3MutationOutcome",
    "M8Gate3MutationRecipeBinding",
    "M8Gate3MutationResult",
    "M8Gate3NormalizedActionRecord",
    "M8Gate3NormalizedEventEvidence",
    "M8Gate3NormalizedInfluenceEvidence",
    "M8Gate3NormalizedPolicyEvidence",
    "M8Gate3PerformanceResult",
    "M8Gate3PerformanceSensitivity",
    "M8Gate3ReferenceCostAttestation",
    "M8Gate3ReferenceTiming",
    "M8Gate3RootMembershipAttestation",
    "M8Gate3RootMembershipBinding",
    "M8Gate3V1CheckerAuditRecord",
    "M8Gate3V1GeneratorAuditRecord",
    "build_gate3_mutation_manifest",
    "build_gate3_mutation_recipe",
    "build_gate3_mutation_result",
    "build_gate3_reference_timing",
    "finalize_gate3_audit",
    "finalize_gate3_decision",
    "finalize_gate3_mutation_execution",
    "finalize_gate3_performance",
    "freeze_gate3_audit_sample",
    "freeze_gate3_checked_root_manifest",
    "gate3_checked_root_sequence_sha256",
    "load_parent_v3_certificate_proof",
    "normalized_gate3_action_semantic_sha256",
]
