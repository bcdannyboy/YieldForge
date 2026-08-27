"""Internal semantic fingerprints for scoped M8 prepared operations."""

from __future__ import annotations

from dataclasses import asdict

from yieldforge.baseline.contracts import TemporalInstanceBinding
from yieldforge.baseline.replay import (
    M7ActionCatalog,
    M7AuthoritativeProofRuntime,
    M7StepResult,
    m7_cursor_sha256,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.reference import M8OracleRequest


def _descriptor_payload(descriptor) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "action_id": descriptor.action_id,
        "kind": descriptor.kind.value,
        "candidate_id": descriptor.candidate_id,
        "selected_remnant_id": descriptor.selected_remnant_id,
        "evidence": (
            descriptor.evidence.model_dump(mode="json")
            if descriptor.evidence is not None
            else None
        ),
    }


def prepared_context_fingerprint(
    *,
    kind: str,
    context_id: int,
    authority: M7AuthoritativeProofRuntime,
    request: M8OracleRequest,
    catalog: M7ActionCatalog,
    fallback_step: M7StepResult,
    visible: tuple[TemporalInstanceBinding, ...],
    stop_event_position: int,
    suffix_sha256: str,
) -> str:
    """Hash all immutable semantics of one prepared generator or checker context."""

    generated = catalog.generated
    payload = {
        "schema_version": "yieldforge.m8-prepared-context-capability.v1",
        "kind": kind,
        "context_id": context_id,
        "authority_id": id(authority),
        "authority_runtime_id": id(authority.runtime),
        "authority_semantic_sha256": authority.semantic_sha256,
        "request_id": id(request),
        "request_runtime_id": id(request.runtime),
        "request_cursor_id": id(request.cursor),
        "request_cursor_sha256": m7_cursor_sha256(request.cursor),
        "visibility_id": id(request.visibility),
        "visibility_type": (
            f"{type(request.visibility).__module__}.{type(request.visibility).__qualname__}"
        ),
        "visibility_mode": request.visibility.mode,
        "visible": tuple(item.model_dump(mode="json") for item in visible),
        "stop_event_position": stop_event_position,
        "suffix_sha256": suffix_sha256,
        "catalog": {
            "event_position": catalog.event_position,
            "actions": tuple(_descriptor_payload(item) for item in catalog.actions),
            "contexts": tuple(asdict(item) for item in catalog.contexts),
            "standard_action_count": catalog.standard_action_count,
            "remnant_action_count": catalog.remnant_action_count,
            "storage_cost": catalog.storage_cost,
            "timestamp_group_sequence": catalog.timestamp_group_sequence,
            "timestamp_subsequence": catalog.timestamp_subsequence,
            "generated": {
                "standard_profiles": tuple(
                    {
                        "candidate_id": item.candidate_id,
                        "candidate_width": item.candidate_width,
                        "accounting": item.accounting.model_dump(mode="json"),
                        "returned_remnant_count": item.returned_remnant_count,
                        "returned_regularity": item.returned_regularity,
                    }
                    for item in generated.standard_profiles
                ),
                "materialized_standard_actions": tuple(
                    {
                        "candidate_id": item.candidate_id,
                        "action_id": item.action_id,
                        "content_sha256": item.content_sha256,
                    }
                    for item in generated.materialized_standard_actions
                ),
                "remnant_actions": tuple(
                    item.model_dump(mode="json") for item in generated.remnant_actions
                ),
                "remnant_action_count": generated.remnant_action_count,
                "fit_search_query_count": generated.fit_search_query_count,
                "fit_search_generated_candidate_count": (
                    generated.fit_search_generated_candidate_count
                ),
                "fit_search_evaluated_candidate_count": (
                    generated.fit_search_evaluated_candidate_count
                ),
                "fit_search_budget_truncated_count": (
                    generated.fit_search_budget_truncated_count
                ),
            },
        },
        "fallback": {
            "descriptor": _descriptor_payload(fallback_step.descriptor),
            "selected_context": asdict(fallback_step.selected_context),
            "action_binding": {
                "catalog_action_id": fallback_step.action_binding.catalog_action_id,
                "materialized_action_id": (
                    fallback_step.action_binding.materialized_action_id
                ),
                "context": asdict(fallback_step.action_binding.context),
            },
            "event": fallback_step.event.model_dump(mode="json"),
            "cursor_sha256": m7_cursor_sha256(fallback_step.cursor),
        },
    }
    return f"sha256:{semantic_sha256(payload)}"


__all__: list[str] = []
